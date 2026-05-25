# BRC-137 — Shard Manifest Announcement

BRC-137 defines the protocol by which multicast participants periodically advertise their `shard_bits` configuration and the set of shard groups they are joined to. Manifest datagrams are emitted directly to the beacon multicast group (`CtrlGroupBeacon`, index `0xFFFD`) at a configurable scope. This BRC supports operator visibility into network-wide sharding configuration, enables divergence detection, and provides hooks for future automated coordination.

> **Canonical BRC:** [BRC-137](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0137.md)

---

## Purpose

All components of the multicast pipeline (proxy, listener, retry endpoint, transaction producer) must agree on a single `shard_bits` value to interoperate. Today this value is configured manually on each component and there is no on-network signal to verify agreement. BRC-137 introduces a small, dedicated announcement service that:

1. Lets every participant periodically declare its current `shard_bits` and the set of shard group indices it claims to have joined.
2. Lets observers detect inconsistent configuration across peers.
3. Provides identity, timestamp, TTL, and a `GenerationID` so future versions can implement automated, rate-limited shard-bit shifts safely.

The service that emits these announcements is `bitcoin-shard-manifest` (defined separately). No retransmission and no listener-side acknowledgment are required.

---

## ShardManifest Wire Format (`MsgType 0x40`) — 64 B header + variable payload

All multi-byte integers are big-endian.

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic            (0xE3E1F3E8, MagicBSV)
     4     2  ProtoVer         (0x02BF)
     6     1  MsgType          (0x40, MsgTypeShardManifest)
     7     1  Flags            bit0 GroupsValid, bit1 Authoritative,
                               bit2 Shutdown, bits3..7 reserved (=0)
     8    16  SrcIPv6          announcer's primary IPv6 (informational)
    24     4  InstanceID       CRC32c of hostname (stable across restarts)
    28     4  Epoch            Unix seconds when announcement was generated
    32     2  TTL              validity in seconds; 0 = use consumer default
    34     2  AnnounceInterval seconds between sends; consumers compute
                               TTL hint = 3 × this value
    36     1  ShardBits        0..12 (MUST be ≤12 per BRC-129)
    37     1  RoleHint         0 generic, 1 proxy, 2 listener,
                               3 retry-endpoint, 4 producer,
                               5 manifest-only (informational)
    38     2  GroupCount       N: number of groupIndex entries in list form
                               (0 if Flags.GroupsValid=0 or bitmap form)
    40     2  BitmapBytes      M: length of trailing bitmap; 0 ⇒ list form
    42     2  Reserved         MUST be 0
    44     4  ManifestCRC      CRC32c (Castagnoli) over bytes [0..44) ‖
                               bytes [48..end), i.e. the entire datagram
                               with the CRC field itself zeroed
    48    16  GenerationID     operator-supplied 128-bit value; bumped
                               whenever ShardBits changes
    64     ?  Payload          either N×2 bytes of big-endian groupIndex
                               (sorted ascending, no duplicates), or
                               exactly M bytes of bitmap (LSB-first, bit i
                               = group index i)
```

Total datagram size = `64 + max(N×2, M)`. Implementations SHOULD keep total
size ≤ 1232 B to avoid IPv6 fragmentation on typical paths. With
`ShardBits=12` (4096 groups) the bitmap form is exactly 512 B; the list
form is 2 B per joined group.

### Encoding-form rules

| `Flags.GroupsValid` | `BitmapBytes` | `GroupCount` | Form                           |
| ------------------- | ------------- | ------------ | ------------------------------ |
| `0`                 | `0`           | `0`          | identity-only (no group claim) |
| `1`                 | `> 0`         | `0`          | bitmap, exactly `BitmapBytes` bytes; bit positions 0..(BitmapBytes×8)-1 |
| `1`                 | `0`           | `> 0`        | list, exactly `GroupCount × 2` bytes, sorted ascending, no duplicates |

A datagram with `Flags.GroupsValid=1` and both `BitmapBytes=0` and
`GroupCount=0` is malformed and MUST be rejected by consumers. A datagram
with `Flags.GroupsValid=1` and both `BitmapBytes>0` and `GroupCount>0` is
also malformed.

For bitmap form the bitmap MUST cover only valid shard indices (0..2^ShardBits − 1). Bits at positions ≥ 2^ShardBits MUST be zero and MUST be ignored by consumers.

### Flags

| Bit | Name          | Meaning                                                                      |
| --- | ------------- | ---------------------------------------------------------------------------- |
| 0   | GroupsValid   | Set when the trailing payload carries a valid joined-groups encoding.        |
| 1   | Authoritative | Operator-curated authoritative announcer (e.g. orchestrator); see Safety.    |
| 2   | Shutdown      | Final announcement before graceful shutdown; consumers MAY evict immediately. |

### RoleHint

`RoleHint` is informational; consumers SHOULD NOT make filtering decisions on it. Defined values:

| Value | Role             |
| ----- | ---------------- |
| `0`   | generic          |
| `1`   | proxy            |
| `2`   | listener         |
| `3`   | retry-endpoint   |
| `4`   | producer         |
| `5`   | manifest-only    |

Values ≥ `6` are reserved.

### ManifestCRC

`ManifestCRC` is a CRC32c (Castagnoli polynomial, same as BSV stream IDs) computed over the entire datagram with the four CRC bytes themselves treated as zero. Consumers MUST verify the CRC before acting on the manifest.

---

## Multicast Group and Scope

Manifests are sent **directly** to the beacon group used by BRC-126 ADVERT:

| Index    | Scope    | Compressed Address |
| -------- | -------- | ------------------ |
| `0xFFFD` | `FF05`   | `FF05::B:FFFD`    |
| `0xFFFD` | `FF08`   | `FF08::B:FFFD`    |
| `0xFFFD` | `FF0E`   | `FF0E::B:FFFD`    |

The announcer chooses one or more scopes via `-manifest-scope` (default `site`). When multiple scopes are configured the same datagram is sent to each. The proxy is **not** involved; manifests do not transit the BRC-124 ingress path.

Because BRC-126 ADVERT (`MsgType 0x20`) shares this group, listeners on the beacon group MUST dispatch on `buf[6]` (MsgType byte) before parsing.

---

## Cadence and Freshness

| Parameter         | Default  | Notes                                              |
| ----------------- | -------- | -------------------------------------------------- |
| AnnounceInterval  | `300 s`  | every 5 minutes                                    |
| Default TTL       | `900 s`  | 3 × AnnounceInterval (used when `TTL == 0`)        |
| Send jitter       | ±10 %    | RECOMMENDED to avoid global synchronisation        |

Consumers MUST treat entries older than `Epoch + TTL` (or `Epoch + 3 × AnnounceInterval` when TTL=0) as expired. If announcements cease, entries expire naturally; an empty registry is valid.

No retransmission and no NACK semantics. Loss is tolerated by re-announcement.

---

## Identity and State

Consumers SHOULD key registry entries on `(SrcIPv6, InstanceID)`. `SrcIPv6` is sourced from the IPv6 datagram header (authoritative); the in-frame `SrcIPv6` field is informational only. `InstanceID` is the CRC32c of the announcer's hostname, matching BRC-126 ADVERT semantics, and remains stable across restarts.

`GenerationID` is an operator-supplied 128-bit value (typically a UUID) that operators MUST bump whenever `ShardBits` changes. It is opaque to the protocol; consumers compare it for change-detection.

---

## Producer Service (`bitcoin-shard-manifest`)

A new standalone daemon emits ShardManifest datagrams. It does not subscribe to or interpret data-plane shard groups. Configuration:

| Flag / Env                                 | Default        | Description                                                        |
| ------------------------------------------ | -------------- | ------------------------------------------------------------------ |
| `-shard-bits` / `SHARD_BITS`               | required       | 0..12                                                              |
| `-joined-groups` / `JOINED_GROUPS`         | `""`           | comma list of hex group indices, or `all`, or empty (no claim)     |
| `-bitmap` / `BITMAP`                       | `auto`         | `auto` selects list ≤32 entries else bitmap; `list`/`bitmap` force |
| `-role-hint` / `ROLE_HINT`                 | `generic`      | one of generic/proxy/listener/retry-endpoint/producer/manifest-only|
| `-generation-id` / `GENERATION_ID`         | zero UUID      | 16-byte hex (with or without dashes)                               |
| `-authoritative` / `AUTHORITATIVE`         | `false`        | sets Flags.Authoritative                                           |
| `-manifest-scope` / `MANIFEST_SCOPE`       | `site`         | comma list of `site,org,global`                                    |
| `-announce-interval` / `ANNOUNCE_INTERVAL` | `300s`         | re-announce period                                                 |
| `-ttl` / `TTL`                             | `0`            | seconds; 0 = consumer default                                      |
| `-iface` / `IFACE`                         | first non-lo   | egress interface for multicast send                                |
| `-port` / `PORT`                           | `9001`         | UDP destination port (matches beacon listen)                       |
| `-mc-prefix` / `MC_PREFIX`                 | `0xff05`       | per BRC-129; default site-local                                    |
| `-mc-group-id` / `MC_GROUP_ID`             | `0x000B`       | per BRC-129                                                        |
| `-metrics-addr` / `METRICS_ADDR`           | `[::]:9091`    | Prometheus/health HTTP listener                                    |
| `-otlp-endpoint` / `OTLP_ENDPOINT`         | `""`           | optional OTLP gRPC endpoint                                        |
| `-otlp-interval` / `OTLP_INTERVAL`         | `15s`          | OTLP push interval                                                 |
| `-debug` / `DEBUG`                         | `false`        | verbose logging                                                    |

The daemon exposes:

- `GET /metrics` — Prometheus exposition.
- `GET /healthz` — process-alive probe.
- `GET /readyz` — ready when at least one successful send has occurred within `2 × AnnounceInterval`.

On `SIGTERM` the daemon emits one final manifest with `Flags.Shutdown=1` before shutting down its HTTP server (5 s deadline).

---

## Consumer Behaviour (informative)

A future BRC-137 consumer MAY join the beacon group(s) of interest and:

1. Dispatch incoming datagrams on `buf[6]`. `0x40` ⇒ ShardManifest; pass to manifest decoder. Other MsgTypes ⇒ existing handlers (e.g. `0x20` BRC-126 ADVERT).
2. Verify `ManifestCRC`. Reject on mismatch.
3. Upsert into a registry keyed on `(SrcIPv6, InstanceID)`.
4. Evict on `Epoch + TTL` (or `Epoch + 3 × AnnounceInterval` when TTL=0).
5. Surface metrics: per-peer ShardBits, joined-group count, last-seen, ShardBits divergence (count of distinct values currently observed).

This BRC does not mandate a consumer implementation in v1. Operators MAY scrape manifests with packet capture tools for visibility.

---

## Safety Guidance (non-normative)

This BRC v1 does not enforce shift limits. Operators and future automation SHOULD:

1. Bump `GenerationID` whenever `ShardBits` changes.
2. Avoid changing `ShardBits` by more than ±1 within any rolling window (RECOMMENDED: 1 hour).
3. Apply hysteresis: hold a candidate `ShardBits` value for ≥ `2 × AnnounceInterval` before adopting.
4. Require a quorum of authoritative manifests (`Flags.Authoritative=1`) before automated consumers act on a value.
5. Keep authoritative announcers to a small operator-curated set; treat non-authoritative manifests as observational.
6. Warn (do not auto-shift) on observed `ShardBits` divergence.

A future revision MAY promote these to normative requirements with explicit rejection rules.

---

## Interactions With Other BRCs

- **BRC-126 (Retransmission / ADVERT)** — shares the beacon group `0xFFFD` and listen port. Distinguished by MsgType byte at offset 6 (`0x20` ADVERT vs `0x40` ShardManifest). BRC-137 does not retransmit and is not retransmitted.
- **BRC-127 (Subtree group announcements)** — orthogonal: BRC-127 announces SubtreeID→GroupID bindings on `0xFFFC` and goes through the proxy. BRC-137 announces participant configuration directly on `0xFFFD`.
- **BRC-129 (Multicast addressing)** — no new index allocated. Manifests reuse the existing beacon group.

---

## Implementation

| Component                                         | File                                                        |
| ------------------------------------------------- | ----------------------------------------------------------- |
| `MsgTypeShardManifest = 0x40`, `ShardManifestHeaderSize = 64` | `bitcoin-shard-common/frame/frame.go`            |
| Wire format encode/decode                         | `bitcoin-shard-common/frame/shard_manifest.go`              |
| Daemon                                            | `bitcoin-shard-manifest/{main.go, sender/, config/, metrics/}` |
| Ansible deployment role                           | `bitcoin-manifest/ansible/roles/bitcoin-shard-manifest/`    |
| Helm chart                                        | `bitcoin-shard-manifest-helm/`                              |

---

## References

- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md)
- [BRC-127: Subtree Group Announcement](brc-127-subtree-announce.md)
- [BRC-129: Multicast Group Addressing](brc-129-multicast-addressing.md)
- [BRC-137: Shard Manifest Announcement (canonical)](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0137.md)
