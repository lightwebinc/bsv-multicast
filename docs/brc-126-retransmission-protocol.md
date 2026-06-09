# BRC-126 — Retransmission Protocol

BRC-126 defines the NACK-based retransmission and endpoint discovery protocol for the BSV multicast pipeline. It specifies the ADVERT beacon message, the MISS/ACK/THROTTLED response messages, tier/preference-based endpoint selection, and configurable retransmit modes.

> **Canonical BRC:** [BRC-126](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0126.md)

---

## Overview

Retry endpoints cache BRC-124 frames received via multicast and respond to NACK requests from listeners experiencing gaps. BRC-126 adds:

1. **ADVERT** — periodic multicast beacon advertising retry endpoint availability.
2. **ACK/MISS responses** — every NACK receives a unicast response (16 bytes).
3. **THROTTLED response** — optional honest-congestion signal (16 bytes); the listener holds the gap and retries the same endpoint without escalating.
4. **Tier/Preference** — hierarchical endpoint selection for multi-AS deployments, as well as local preference.
5. **Configurable retransmit modes** — multicast, unicast, or both.

---

## ADVERT Wire Format (`MsgType 0x20`) — 56 bytes

Sent periodically (default **60 s**, configurable via `-beacon-interval`) to the beacon group determined by `-beacon-scope`. Valid values: `site` → `FF05::B:FFFD`; `global` → `FF0E::B:FFFD`; `both` → sends to both site and global groups. Listeners derive TTL as `3 × BeaconInterval`. Org scope (`FF08::B:FFFD`, scope byte `0x08`) is defined in the wire format but not yet a supported `-beacon-scope` value.

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x20 (ADVERT)
     7     1  Scope  (0x05=site, 0x08=org, 0x0E=global)
     8    16  NACKAddr      — IPv6 unicast address for NACK requests
    24     2  NACKPort      — UDP NACK listen port (default 9300)
    26     1  Tier          — operator-assigned; 0 = same AS as proxy (max 255)
    27     1  Preference    — weighting within a tier; higher = more preferred (default 128)
    28     2  BeaconInterval — seconds; listeners compute TTL = 3 × this value
    30     2  Flags         — see below
    32     4  InstanceID    — CRC32c of hostname; stable across restarts
    36     4  Reserved
    40    16  Reserved      — future use (BGP community, capability bitmap)
```

### Flags Bitmask

| Bit      | Name                | Meaning                                                    |
| -------- | ------------------- | ---------------------------------------------------------- |
| `0x0001` | _(reserved)_        | Unused, Reserved for future use                            |
| `0x0002` | HasParent           | NACK proxying enabled (an upstream endpoint is configured) |
| `0x0004` | Draining            | Entering shutdown; stop routing new NACKs here             |
| `0x0008` | UnicastRetransmit   | Supports unicast frame delivery to NACK source             |
| `0x0010` | MulticastRetransmit | Retransmits via multicast (default on)                     |

**HasParent rationale:** When set, the endpoint forwards NACKs to a parent (upstream) endpoint on local cache miss. Benefits: topology encapsulation (listeners need only know local endpoints), reduced global beacon traffic, faster recovery via co-located forwarding.

### NACK Proxying (cross-domain recovery)

A retry-endpoint serving a downstream multicast domain (fed by a `shard-listener`'s multicast egress) can only cache what the listener actually emitted. A frame the listener never put on the downstream wire — egress send error, interface flap, in-fabric loss — is missed identically by the downstream endpoint and every downstream consumer, so a downstream-only cache cannot repair it. NACK proxying recovers such frames from an upstream endpoint that received them directly from the proxy:

1. A downstream consumer NACKs the downstream endpoint → local cache miss → MISS returned immediately.
2. The downstream endpoint forwards the NACK to a configured upstream endpoint with the **Proxied** flag set (`0x01`). Recovery is asynchronous ("cache-warm") so no NACK worker is held.
3. The upstream endpoint serves the proxied NACK from its cache. Because the requester (the downstream endpoint) is not joined to the upstream shard groups, the frame is returned via **unicast** to the NACK source — a proxied NACK is always served a unicast copy regardless of the upstream's advertised retransmit mode.
4. The downstream endpoint re-caches the recovered frame (keyed `HashKey ∥ SeqNum`, per-FrameVer TTL) and multicast-retransmits it into the downstream domain; the consumer's gap auto-fills.

**One-hop bound:** the Proxied flag prevents an upstream endpoint from re-proxying, so a chain is at most one hop. Upstream discovery is static configuration (a separated downstream domain generally cannot receive upstream multicast beacons). When several downstream endpoints run with proxying, a shared cache backend (Redis/Aerospike) lets an in-flight claim dedup the upstream NACKs so only one endpoint recovers each gap.

---

## NACK Wire Format (`MsgType 0x10`) — 64 bytes

Sent by listener to retry endpoint on gap detection. Identifies the missing frame by its flow (`HashKey`) and sequence number (`StartSeq`). `StartSeq == EndSeq` for single-frame retrieval; range requests (`StartSeq < EndSeq`) are reserved for future use.

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x10 (NACK)
     7     1  Flags — bit 0 (0x01) = Proxied; bits 1–7 reserved 0x00
     8     8  HashKey   — stable per-flow XXH64 identifier from the BRC-124 frame
    16     8  StartSeq  — first missing SeqNum (inclusive)
    24     8  EndSeq    — last missing SeqNum (inclusive); equals StartSeq for single-frame
    32    32  SubtreeID — 32-byte batch identifier; zeros = unset
```

> **HashKey** (offset 8) is the `HashKey` field from the BRC-124 frame, computed as `XXH64(senderIPv6 ∥ groupIdx ∥ subtreeID)`. It uniquely identifies the flow. The retry endpoint uses `HashKey` as a per-flow rate-limiting key (NACK storm cap). A value of `0` bypasses the per-flow check.

> **StartSeq / EndSeq** (offsets 16/24) specify the range of missing sequence numbers. For current single-frame retrieval, `StartSeq == EndSeq`. The retry endpoint looks up the frame using the 16-byte cache key `HashKey ∥ StartSeq`.

> **SubtreeID** (offset 32) is carried for informational purposes; the cache key is `HashKey ∥ SeqNum` and does not require SubtreeID for disambiguation.

> **Flags / Proxied** (offset 7, bit `0x01`) marks a NACK that an endpoint issued on behalf of a downstream domain (cross-domain proxying — see below). An endpoint receiving a NACK with this bit set MUST serve it from its own cache but MUST NOT re-proxy it, bounding any proxy chain to a single hop. The bit was previously reserved and is ignored by legacy endpoints (which simply never re-proxy).

---

## MISS Response (`MsgType 0x11`) — 16 bytes

Sent unicast to the NACK source when the requested frame is not in cache. Listener advances to next endpoint immediately (no backoff wait).

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x11 (MISS)
     7     1  Flags (reserved 0x00)
     8     8  SeqNum — always 0 on MISS
```

---

## ACK Response (`MsgType 0x12`) — 16 bytes

Sent unicast to the NACK source when the frame is found and retransmit dispatched. Listener suppresses further NACKs for this gap immediately.

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x12 (ACK)
     7     1  Flags (0x01=multicast_sent)
     8     8  SeqNum — SeqNum of the retransmitted frame
```

---

## THROTTLED Response (`MsgType 0x13`) — 16 bytes

Sent unicast to the NACK source when the request was rejected by a congestion-control tier that limits per-gap (per-SeqNum), per-flow (per-HashKey/chain), or per-group (groupIdx) request rate. It is a flow-control signal, **not** a failure: the endpoint is healthy, and for a per-gap throttle a retransmit for this exact gap was likely just served and is propagating over the multicast data plane. On receipt a listener MUST hold the gap for the hinted backoff and retry the **same** endpoint; it MUST NOT escalate to another endpoint and MUST NOT count the throttle as a recovery failure.

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x13 (THROTTLED)
     7     1  Flags — bits 0–3 = backoff bucket; 4–7 reserved
     8     8  SeqNum — echo of the throttled request's StartSeq
```

**Backoff hint.** The suggested hold is `ThrottleHintBase << bucket`, where `ThrottleHintBase = 125 ms` and `bucket` is the Flags low nibble. Endpoints SHOULD use bucket `2` (~500 ms) for a per-gap throttle, bucket `3` (~1 s) for a per-flow throttle, and bucket `4` (~2 s) for a per-group throttle. Listeners SHOULD apply jitter and MAY clamp to a local maximum; the gap's absolute TTL remains the upper bound, and a multicast repair cancels the gap regardless.

**Emission rules.** THROTTLED is OPTIONAL and defaults to disabled (`-rl-throttle-response` / `RL_THROTTLE_RESPONSE`); it is a load-shedding refinement for high-fan-out deployments. An endpoint MUST NOT send THROTTLED for a flood-tier (per-source IP) rejection: that tier sheds abusive or spoofed-source load, and answering it would permit reflection. The 16-byte response is smaller than the 64-byte NACK, so the protocol is never a bandwidth amplifier regardless.

---

## Tier / Preference Model

| Tier | Meaning                                            |
| ---- | -------------------------------------------------- |
| 0    | Same AS as `shard-proxy` (source-adjacent) |
| 1    | One AS boundary from source                        |
| N    | N hops from source                                 |
| 0xFF | Static seed (no beacon received; lowest priority)  |

Operator assigns `-tier` (0–254) and `-preference` (0–255, default 128) on each `retry-endpoint`. Endpoints are sorted by `(Tier ASC, Preference DESC)` — higher-preference endpoints are tried first within a tier.

### Escalation State Machine

```text
                    ┌──────────────────────────────────────┐
                    │                                      │
  ┌─────────┐  dispatch ┌───────────────┐  ACK    ┌────────▼───────┐
  │ PENDING ├──────────►│ NACKED(Tier-K)├────────►│  GAP CANCELLED │
  └─────────┘           └──────┬────────┘         └────────────────┘
                               │                           ▲
                    MISS       │  Timeout                  │
                    ┌──────────┘  ┌──────┘                 │
                    ▼             ▼                        │
              ┌───────────┐  ┌──────────┐   multicast fill │
              │ advance   │  │ backoff  │──────────────────┘
              │ endpoint  │  │ & retry  │
              └─────┬─────┘  └──────────┘
                    │
                    ▼
              next endpoint at same tier,
              or escalate to next tier
```

- **ACK received** → cancel gap entry immediately.
- **MISS received** → advance to next endpoint at same tier (by Preference); if tier exhausted, escalate to next tier; retry immediately (no backoff).
- **THROTTLED received** → hold the same endpoint for the hinted backoff; do not escalate and do not consume the retry budget (no failed round).
- **Timeout** → apply exponential backoff; next sweep retries.
- **Multicast fill** (independent receive goroutine) → cancel gap regardless of NACK state.

---

## Configurable Retransmit Modes

| Flag                    | Default | Meaning                                              |
| ----------------------- | ------- | ---------------------------------------------------- |
| `-retransmit-multicast` | `true`  | Send cached frame to multicast group on NACK hit     |
| `-retransmit-unicast`   | `false` | Send cached frame unicast to NACK source on NACK hit |
| `-suppress-miss`        | `false` | Do not send MISS responses                           |
| `-suppress-ack`         | `false` | Do not send ACK responses                            |

### Deployment profiles

- **On-fabric (default):** multicast retransmit + ACK + MISS
- **Edge endpoint:** `-retransmit-unicast=true -retransmit-multicast=false`
- **High-volume:** `-suppress-ack=true` to reduce response traffic; MISS preserved for escalation

---

## Flood Prevention

- **Multicast fill suppression:** retransmits go to multicast; all listeners receive them; `Tracker.Fill()` cancels pending NACKs.
- **Cache TTL (60 s):** retransmitted frames remain in cache for the TTL window; natural expiry bounds the retransmit window without coordination overhead.
- **Rate-limit tiers:** per-source-IP (flood, silent drop), per-flow (HashKey), per-gap (SeqNum), and per-group (groupIdx). The honest-congestion tiers (per-flow, per-gap, per-group) MAY emit THROTTLED so the listener holds rather than escalates; the flood tier stays silent to avoid reflection.
- **Inter-AS:** MP-BGP propagates retransmits; remote listeners fill before backoff fires.

---

## Implementation

- **Listener:** `shard-listener/nack/wire.go` (NACK encode/decode), `shard-listener/discovery/` (ADVERT decode, registry, beacon listener)
- **Endpoint:** `retry-endpoint/server/server.go` (NACK receive, ACK/MISS send), `retry-endpoint/beacon/` (ADVERT encode/send)
- **Common:** `shard-common/frame/` (MsgType constants)

---

## References

- [BRC-126: Multicast Transaction NACK Retransmission Protocol](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0126.md) — published BRC
