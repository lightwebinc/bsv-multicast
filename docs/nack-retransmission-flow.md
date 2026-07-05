# NACK Retransmission Flow

System-level design document describing the end-to-end NACK retransmission pipeline across all components. This is a cross-repo document covering `shard-proxy`, `shard-listener`, `retry-endpoint`, and the multicast fabric.

---

## 1. Full Pipeline

```text
                          multicast fabric (FF05::B:<shard>)
                         ┌──────────────────────────────────────────────────┐
                         │                                                  │
BSV Source ──► shard-proxy ──┬──► listener-1 ──► downstream consumer
              (stamps HashKey/SeqNum) │
                                     ├──► listener-2 ──► downstream consumer
                                     │
                                     └──► retry-endpoint (caches all frames)
```

- **Proxy** receives transactions, stamps `HashKey` (XXH64 of sender+group+subtree) and `SeqNum` (monotonic per-flow counter), derives shard group from TxID, multicasts to `FF05::B:<shard>`.
- **Listeners** subscribe to shard groups, decode frames, track per-flow `SeqNum` gaps (keyed by `HashKey`), forward to consumers.
- **Retry endpoint** subscribes to all shard groups, caches raw frames indexed by `HashKey ∥ SeqNum` (single 16-byte key).

---

## 2. Gap Detection & NACK Dispatch

```text
Listener receives:  SeqNum 1, 2, 3, [gap], 6, 7, ...
                              │
                              ▼
              Gap detected: incoming SeqNum (6) > lastSeqNum (3) + 1
              Missing: SeqNum 4, 5 for this HashKey
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
            Hold-off jitter      Register gap entry
            (suppression)        in Tracker.pending
                    │
                    ▼
              NACK dispatched (64 bytes, unicast UDP)
              to retry endpoint from registry snapshot
                    │
                    ▼
         ┌──────────────────────────┐
         │  Ephemeral UDP socket    │
         │  conn = ListenPacket     │
         │  WriteTo NACK            │
         │  ReadFrom response(300ms)│
         └──────────┬───────────────┘
                    │
      ┌────────┬────┴────┬─────────┐
      ▼        ▼         ▼         ▼
    ACK      MISS    THROTTLED  Timeout
      │        │         │         │
 cancel gap advance   hold same  backoff
            endpoint  endpoint   & retry
                      (no escalate)
```

The NACK carries the `HashKey` (stable per-flow identifier) and `StartSeq`/`EndSeq` (missing sequence range). The retry endpoint looks up the frame using the 16-byte cache key `HashKey ∥ StartSeq`. The listener opens a per-request ephemeral UDP socket (`[::]:0`), sends the NACK, and waits up to 300 ms for a single response (`ACK`, `MISS`, or — when the endpoint runs with `-rl-throttle-response` — `THROTTLED`).

### Re-baseline on emitter change (v1.7.0/v1.7.1)

Not every forward jump is a gap. A jump larger than `-nack-max-forward-jump`
(default 4096), or one implausible against the flow's smoothed inter-arrival
estimate (`ewmaIPG` in `shard-listener/nack/nack.go` — the elapsed time at the
observed rate could not have carried that many frames), signals an **emitter
change** (restart / re-key), not loss. The tracker **re-baselines** the flow
instead of registering thousands of phantom gaps: pending phantom entries are
dropped, the rate estimate resets to re-learn the new cadence, and the
`SeqRebaselined` metric increments. When the pre-jump rate estimate was settled
(≥ 16 contiguous in-order frames), a rate-plausible **transition tail** — the
≈ `elapsed / ewmaIPG` frames a real outage of that duration would have lost,
capped at the max forward jump — is still NACK-recovered, so a genuine outage
that merely looks like a jump is not silently skipped.

---

## 3. Tier Model

Tiers represent proximity to the transaction source. Lower tier = closer to source.

| Tier | Meaning          | Example                                        |
| ---- | ---------------- | ---------------------------------------------- |
| 0    | Same AS as proxy | Data-center-local retry endpoint               |
| 1    | One AS hop       | Regional PoP                                   |
| 2    | Two AS hops      | Remote continent                               |
| 0xFF | Static seed      | Bootstrap `-retry-endpoints` (lowest priority) |

```text
[BSV Source]
     │
     ▼
[Proxy] ──multicast──► [Tier-0 Retry Endpoint]
                              │
                         NACK proxying (-proxy-enabled)
                              │
                              ▼
                        [Tier-1 Retry Endpoint]  (HasParent)
                              │
                              ▼
                        [Tier-2 Retry Endpoint]  (HasParent)
```

Listeners try Tier 0 first. On MISS, they advance to Tier 1, then Tier 2, etc.

NACK proxying is shipped: an endpoint started with `-proxy-enabled` forwards a
cache-miss NACK to a configured upstream (`-upstream-retry-endpoints`), setting
the NACK **Proxied** flag (bit 0) and advertising `HasParent` in its ADVERT.
The upstream always serves a proxied NACK via unicast; the downstream endpoint
re-caches the recovered frame and multicast-retransmits it into its own domain.
The Proxied flag bounds any proxy chain to a single hop. See
[BRC-126 § NACK Proxying](brc-126-retransmission-protocol.md#nack-proxying-cross-domain-recovery).

---

## 4. Preference Within a Tier

Multiple endpoints at the same tier are sorted by **Preference DESC**. Higher Preference = tried first.

```text
Tier 0:
  Endpoint A  (Preference 200)  ◄── tried first
  Endpoint B  (Preference 128)
  Endpoint C  (Preference  50)  ◄── tried last within Tier 0

Tier 1:
  Endpoint D  (Preference 180)  ◄── tried after all Tier 0 exhausted
  Endpoint E  (Preference  90)
```

**Use cases:**

- Direct traffic to higher-capacity nodes (higher Preference).
- Prefer endpoints on better-connected paths.
- Graceful migration: set old endpoint to Preference 0 before draining.

---

## 5. Escalation State Machine

```text
  ┌──────────┐
  │ PENDING  │  gap detected; hold-off jitter applied
  └────┬─────┘
       │ timer fires
       ▼
  ┌────────────────┐
  │ NACKED(Tier-K) │  NACK sent to endpoint at current (tier, preference)
  └─┬───┬───┬───┬──┘
    │   │   │   │
    │   │   │   └─ Timeout ──► exponential backoff; retry next sweep
    │   │   │
    │   │   └─ THROTTLED ──► hold the SAME endpoint for the hinted backoff
    │   │                    (ThrottleHintBase << bucket, jittered);
    │   │                    do NOT escalate, do NOT count a failed round
    │   │
    │   └─ MISS ──► advance to next endpoint at same tier (by Preference);
    │               if tier exhausted, advance to Tier K+1;
    │               retry IMMEDIATELY (no backoff)
    │
    └─ ACK ──► gap entry cancelled; done
               (ACK.Flags indicates multicast_sent / unicast_sent)

  Any state ──► FILLED  (multicast repair arrived independently)
                gap entry cancelled; in-flight socket times out harmlessly
```

`THROTTLED` is an optional honest-congestion signal (enabled per-endpoint via
`-rl-throttle-response`). It means the request hit a per-gap, per-flow, or
per-group rate-limit tier — the endpoint is healthy and a multicast repair for
this exact gap is likely already propagating — so the listener parks the gap
briefly and retries the same endpoint rather than escalating or burning a retry.
The per-source-IP flood tier never emits `THROTTLED` (it would enable
reflection); it stays silent and the listener falls back to timeout + backoff.

A forward jump exceeding `-nack-max-forward-jump` or implausible against the
flow's rate estimate never enters `PENDING`: the flow is **re-baselined**
(emitter change — see §2), phantom gaps are dropped (`SeqRebaselined`), and
only a rate-plausible transition tail is NACK-recovered once the estimate has
settled.

---

## 6. Beacon Discovery

```text
retry-endpoint                    shard-listener
┌─────────────────────┐                  ┌─────────────────────┐
│ beacon.Sender       │                  │ discovery.Beacon    │
│                     │  ADVERT (56B)    │   Listener          │
│ every 60s ──────────┼──── multicast ──►│                     │
│ to FF05::B:FFFD     │  (site-scoped)   │ DecodeADVERT()      │
│ to FF0E::B:FFFD     │  (global-scoped) │       │             │
│ (per -beacon-scope) │                  │       ▼             │
└─────────────────────┘                  │ registry.Upsert()   │
                                         │  TTL = 3 × interval │
                                         │       │             │
                                         │       ▼             │
                                         │ Snapshot() ─► NACK  │
                                         │ (sorted Tier ASC,   │
                                         │  Preference DESC)   │
                                         └─────────────────────┘

Fallback: `-retry-endpoints host1:9300,host2:9300`
  → Seed() at Tier=0xFF, Preference=0 (tried last)
```

---

## 7. Inter-AS Extension

```text
AS 100 (Source)                    AS 200 (Remote)
┌──────────────────┐              ┌───────────────────┐
│ Proxy            │              │                   │
│   │              │              │                   │
│   ▼              │  MP-BGP      │                   │
│ Tier-0 Endpoint  │◄─multicast──►│ Tier-1 Endpoint   │
│ beacons on       │  MVPN/MSDP   │ (HasParent)       │
│ FF0E::B:FFFD     │              │                   │
│                  │              │ Listeners         │
└──────────────────┘              │ join both beacon  │
                                  │ groups; discover  │
                                  │ Tier-0 via global │
                                  │ beacon            │
                                  └───────────────────┘

NACK escalation path:
  Listener (AS 200) ──NACK──► Tier-1 (AS 200)
                                  │ cache miss
                                  ▼
                              Tier-0 (AS 100)  ──► retransmit to multicast
                                                   MP-BGP delivers to AS 200
```

No protocol changes required. Network team extends multicast fabric via MP-BGP.

---

## 8. Flood Prevention

| Mechanism                | Layer          | Effect                                                                                            |
| ------------------------ | -------------- | ------------------------------------------------------------------------------------------------- |
| Cache TTL (60 s)         | Retry endpoint | Frames expire naturally; bounds retransmit window                                                 |
| Multi-tier rate limiting | Retry endpoint | Per-IP (flood, always silent); per-HashKey, per-SeqNum (pre-lookup) and per-group (post-lookup) — silent by default, or emit `THROTTLED` under `-rl-throttle-response` |
| `Tracker.Fill()`         | Listener       | Multicast repair cancels pending NACKs for all listeners                                          |
| Jitter hold-off          | Listener       | Randomised delay before first NACK suppresses duplicates                                          |
| Exponential backoff      | Listener       | Reduces NACK rate on persistent gaps                                                              |
| `MaxRetries` + `GapTTL`  | Listener       | Gap entries evicted after retry exhaustion or absolute deadline                                   |
