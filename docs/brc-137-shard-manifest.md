# BRC-137 — Shard Manifest Announcement

BRC-137 defines the protocol by which multicast participants periodically advertise their `shard_bits` configuration and the set of shard groups they are joined to. Manifest datagrams are emitted directly to the beacon multicast group (`GroupBeacon`, index `0xFFFD`) at a configurable scope. This BRC supports operator visibility into network-wide sharding configuration, enables divergence detection, and provides hooks for future automated coordination.

> **Canonical BRC:** [BRC-137](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0137.md)

---

## Purpose

All components of the multicast pipeline (proxy, listener, retry endpoint, transaction producer) must agree on a single `shard_bits` value to interoperate. Today this value is configured manually on each component and there is no on-network signal to verify agreement. BRC-137 introduces a small, dedicated announcement service that:

1. Lets every participant periodically declare its current `shard_bits` and the set of shard group indices it claims to have joined.
2. Lets observers detect inconsistent configuration across peers.
3. Provides identity, timestamp, TTL, and a `GenerationID` so future versions can implement automated, rate-limited shard-bit shifts safely.

The service that emits these announcements is `shard-manifest` (defined separately). No retransmission and no listener-side acknowledgment are required.

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
                               bit2 Shutdown, bit3 SourceModeSSM,
                               bit4 SourcesValid, bit5 PilotOnly,
                               bit6 SuccessorValid, bit7 reserved (=0)
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
    42     2  SourceCount      K: number of 16-byte source-IPv6 entries
                               appended after the groups payload. 0 when
                               Flags.SourcesValid=0. MUST be 0 in v1.0
                               implementations that pre-date this field
                               (formerly Reserved).
    44     4  ManifestCRC      CRC32c (Castagnoli) over bytes [0..44) ‖
                               bytes [48..end), i.e. the entire datagram
                               with the CRC field itself zeroed
    48    16  GenerationID     operator-supplied 128-bit value; bumped
                               whenever ShardBits changes
    64     ?  Payload          groups, sources, and successor sections in
                               order:
                               • groups: N×2 bytes of big-endian groupIndex
                                 (sorted ascending, no duplicates), or
                                 exactly M bytes of bitmap (LSB-first, bit i
                                 = group index i), or empty
                                 (Flags.GroupsValid=0).
                               • sources: K × 16 bytes of source IPv6
                                 (network byte order). Present iff
                                 Flags.SourcesValid=1.
                               • successor: 24 bytes describing a pending
                                 generation transition. Present iff
                                 Flags.SuccessorValid=1. See Successor
                                 block.
```

Total datagram size = `64 + max(N×2, M) + K × 16 + (24 if SuccessorValid
else 0)`. Implementations SHOULD keep total size ≤ 1232 B to avoid IPv6
fragmentation on typical paths. With `ShardBits=12` (4096 groups) the
bitmap form is exactly 512 B; the list form is 2 B per joined group; each
source entry adds 16 B; the successor block adds 24 B. Operators with
large source lists SHOULD spread the list across multiple announcers (each
publisher advertising its own source as the lone entry) so individual
datagrams stay within the recommended MTU.

### Encoding-form rules

| `Flags.GroupsValid` | `BitmapBytes` | `GroupCount` | Groups payload form            |
| ------------------- | ------------- | ------------ | ------------------------------ |
| `0`                 | `0`           | `0`          | identity-only (no group claim) |
| `1`                 | `> 0`         | `0`          | bitmap, exactly `BitmapBytes` bytes; bit positions 0..(BitmapBytes×8)-1 |
| `1`                 | `0`           | `> 0`        | list, exactly `GroupCount × 2` bytes, sorted ascending, no duplicates |

A datagram with `Flags.GroupsValid=1` and both `BitmapBytes=0` and
`GroupCount=0` is malformed and MUST be rejected by consumers. A datagram
with `Flags.GroupsValid=1` and both `BitmapBytes>0` and `GroupCount>0` is
also malformed.

For bitmap form the bitmap MUST cover only valid shard indices (0..2^ShardBits − 1). Bits at positions ≥ 2^ShardBits MUST be zero and MUST be ignored by consumers.

### Sources payload (when `Flags.SourcesValid=1`)

When `Flags.SourcesValid=1` the datagram appends exactly `SourceCount × 16`
bytes immediately after the groups payload. Each 16-byte entry is a
publisher source IPv6 in network byte order, contributed by this announcer.
Consumers MUST:

- Reject the datagram when `Flags.SourcesValid=1 && SourceCount=0`.
- Reject the datagram when `Flags.SourcesValid=0 && SourceCount>0`.
- Treat entries as set-valued (order not significant) and tolerate
  duplicates by deduplicating across the union of all currently-valid
  manifests.
- When `SourceModeSSM=1`, feed the union into the SSM `(S,G)` join calls
  for data-plane groups derived from announced parameters.

Sender guidance: each shard-proxy SHOULD announce its own `bindSource`
(typically a single entry) rather than the operator-curated full fleet
list, so per-datagram size stays small and source-set churn naturally
follows publisher lifecycle.

### Successor block (when `Flags.SuccessorValid=1`)

The Successor block signals an in-flight generation transition: the
announcer is committing to a future `ShardBits` (and optionally
`SourceModeSSM`) value that becomes the sole active generation at
`TransitionEpoch`. The block enables live re-sharding consumers (see
the [Automatic Shard Configuration Plan](AutoShardConfig/auto-shard-config-plan.md))
to enter a bridging window before cutover; consumers that do not
implement live re-sharding MUST still parse and account for the
divergence but otherwise behave as for any other future `GenerationID`
change.

When `Flags.SuccessorValid=1` the datagram appends exactly 24 bytes
immediately after the sources payload:

```text
Offset (within block)  Size  Field
---------------------  ----  -----
                    0    16  SuccessorGenerationID  the incoming generation's 128-bit ID
                   16     1  SuccessorShardBits     1..15; MUST satisfy |Successor - ShardBits| ≤ 1
                   17     1  SuccessorFlags         bit0 SuccessorSourceModeSSM,
                                                    bits1..7 reserved (=0)
                   18     2  Reserved               MUST be 0
                   20     4  TransitionEpoch        Unix seconds at which the successor becomes
                                                    the sole active generation
```

Consumer rules (normative when auto-configuration is enabled):

- Reject the datagram when
  `|SuccessorShardBits − ShardBits| > 1`.
- Reject the datagram when `Flags.SuccessorValid=1 && Flags.Authoritative=0`
  (live re-sharding signals require operator authority).
- Apply the existing adoption gates (quorum, hysteresis) to the
  Successor block as a unit (the tuple
  `(SuccessorGenerationID, SuccessorShardBits, SuccessorFlags, TransitionEpoch)`
  is the candidate value).
- Consumers that implement live re-sharding (an opt-in, deployment-side
  policy) MAY enter a bridging window between the moment the Successor
  block first satisfies quorum and `local_clock ≥ TransitionEpoch`. The
  bridging-mode semantics (dual-emission for senders, union-join for
  receivers) are specified in the
  [Automatic Shard Configuration Plan](AutoShardConfig/auto-shard-config-plan.md).
- Consumers that do not implement live re-sharding MUST treat
  Successor-block adoption as a divergence event and otherwise wait for
  the pilot to roll `GenerationID` (i.e. drop the Successor block and
  promote it to the active generation) before reacting on the new
  value. Pilots MUST honour this by rolling `GenerationID` to the
  former `SuccessorGenerationID` at or after `TransitionEpoch`.

Pilot guidance:

- Choose `TransitionEpoch ≥ now + 2 × AnnounceInterval`; pilots MUST
  reject configurations below that floor.
- Recommended `TransitionEpoch ≥ now + 4 × AnnounceInterval` so the
  last-to-receive consumer has at least `2 × AnnounceInterval` of
  bridging.
- Clock skew between pilot and consumers MUST stay below
  `AnnounceInterval / 2`; operators SHOULD run NTP and the consumer
  SHOULD expose observed skew via metrics.

### Flags

| Bit | Name           | Meaning                                                                                                                                                                                                                                                                                                                                                                                                            |
| --- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 0   | GroupsValid    | Set when the trailing payload carries a valid joined-groups encoding.                                                                                                                                                                                                                                                                                                                                              |
| 1   | Authoritative  | Operator-curated authoritative announcer (e.g. orchestrator); see Safety.                                                                                                                                                                                                                                                                                                                                          |
| 2   | Shutdown       | Final announcement before graceful shutdown; consumers MAY evict immediately.                                                                                                                                                                                                                                                                                                                                      |
| 3   | SourceModeSSM  | Announcer declares the data plane uses Source-Specific Multicast (FF3x::/32 per RFC 4607). Auto-configuration consumers MUST use the SSM prefix when computing data-plane group addresses derived from this manifest. Whether the beacon group itself is joined via ASM or SSM is a deployment-posture choice (see the [SSM support plan](SourceSpecificMulticast/ssm-support-plan.md)), not a BRC-137 invariant.  |
| 4   | SourcesValid   | The trailing payload includes `SourceCount × 16` bytes of publisher source IPv6 addresses after the groups payload. Consumers union the source set across all currently-valid manifests they hold. MUST be 0 when `SourceCount=0`.                                                                                                                                                                                |
| 5   | PilotOnly      | This manifest is exclusively a pilot/assignment broadcast: the announcer is not itself joined to the announced groups and the groups payload describes desired fleet state, not its own joins. Implies `Authoritative=1`; consumers MUST reject `PilotOnly=1 && Authoritative=0` as malformed.                                                                                                                     |
| 6   | SuccessorValid | The trailing payload includes a 24-byte Successor block (see "Successor block") describing an in-flight generation transition. Requires `Authoritative=1`; consumers MUST reject `SuccessorValid=1 && Authoritative=0` as malformed. The Successor block's `ShardBits` MUST be within ±1 of the announcer's current `ShardBits` per the existing safety guidance.                                                 |

Bit 7 is reserved and MUST be 0.

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

## Producer Service (`shard-manifest`)

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

## Consumer Behaviour — Observation (informative)

A BRC-137 consumer MAY join the beacon group(s) of interest and:

1. Dispatch incoming datagrams on `buf[6]`. `0x40` ⇒ ShardManifest; pass to manifest decoder. Other MsgTypes ⇒ existing handlers (e.g. `0x20` BRC-126 ADVERT).
2. Verify `ManifestCRC`. Reject on mismatch.
3. Upsert into a registry keyed on `(SrcIPv6, InstanceID)`.
4. Evict on `Epoch + TTL` (or `Epoch + 3 × AnnounceInterval` when TTL=0).
5. Surface metrics: per-peer ShardBits, joined-group count, last-seen, ShardBits divergence (count of distinct values currently observed).

Operators MAY also scrape manifests with packet capture tools for
visibility-only deployments.

## Consumer Behaviour — Auto-configuration (normative when opted in)

A consumer that opts in to automatic configuration (e.g. a `shard-proxy`
or `shard-listener` with `-manifest-consumer-enabled=true`) MUST implement
the observation requirements above and, additionally, MUST satisfy the
rules in this section. Components that do not opt in are unaffected.

### Adoption gating

1. **Authoritative-only adoption.** Manifests with `Flags.Authoritative=0`
   MUST NOT contribute to any adopted value. They MAY still be indexed for
   visibility and MAY contribute to `Flags.SourcesValid` payload unions
   (per (3) below).
2. **Quorum.** A candidate value for a field is eligible for adoption only
   when reported by at least `pilot-quorum` distinct authoritative
   announcers (keyed on `(SrcIPv6, InstanceID)`) within their TTL window.
   Implementations MUST expose `pilot-quorum` as configuration; default
   `2`. `pilot-quorum=1` MAY be supported but the consumer MUST log a
   warning at startup.
3. **Hysteresis.** A candidate value that satisfies quorum MUST hold
   quorum continuously for `≥ 2 × AnnounceInterval` (taken from any one
   contributing manifest) before adoption. A change in adopted value
   resets the hysteresis timer.
4. **`ShardBits` shift bound.** A consumer MUST NOT adopt a `ShardBits`
   value that differs from the currently adopted value by more than ±1
   within any rolling `AnnounceInterval` window. This caps the rate at
   which the addressable space can be doubled or halved during an
   automated shift.
5. **Manual pin precedence.** When the local operator has pinned a value
   via CLI/env, that value is the local authority and MUST NOT be
   overridden by adoption. The consumer MUST still evaluate quorum and
   MUST emit divergence telemetry when the adopted candidate differs from
   the pin.

Fields subject to adoption: `ShardBits`, `Flags.SourceModeSSM`, and the
union of `Flags.SourcesValid` payloads. `MCGroupID` is not carried in the
payload; consumers MUST derive it from the destination address of the
beacon socket they received the manifest on.

### Source set (`Flags.SourcesValid` payloads)

The source set is not gated by authoritative quorum: it is the deduplicated
union of every currently-valid manifest's sources payload, irrespective of
`Flags.Authoritative`. This lets each publisher contribute its own
`bindSource` via its own sidecar manifest without operator-curated
authority. Consumers SHOULD rate-limit additions and removals when feeding
the set into kernel join calls (`MCAST_JOIN_SOURCE_GROUP` /
`MCAST_LEAVE_SOURCE_GROUP`) to avoid thrashing the multicast forwarding
information base.

### Divergence telemetry

The consumer MUST emit, at minimum:

- `multicast_manifest_divergence_total{field=...,kind=peer-disagree|pin-disagree|crc-fail}` —
  counter.
- `multicast_manifest_last_divergence_epoch{field=...}` — gauge of the
  most recent Unix-seconds disagreement timestamp.
- `multicast_manifest_pilots_known` — gauge of distinct authoritative
  announcers currently within TTL.
- `multicast_manifest_quorum_met{field=...}` — gauge `1`/`0`.

Implementations MUST NOT label any metric with raw source IPv6 addresses;
cardinality MUST be bounded by role, group-role, or fleet bucket.

### State transitions

A consumer MUST NOT silently drop traffic when an adopted value changes.
Components SHOULD signal `ShardBits` or `SourceModeSSM` changes by
flipping their readiness probe and draining in-flight datagrams within the
configured drain window before reloading, so the orchestrator can roll the
pod predictably. Incremental changes — adding or removing entries from
`Flags.GroupsValid` payloads (when consumed as join hints) or from the
source set — MAY be applied in place without restart.

---

## Safety Guidance

The Consumer Behaviour — Auto-configuration section above promotes the
hysteresis, quorum, and ±1 `ShardBits` shift bound to normative
requirements for opted-in consumers. The following remain operator-side
guidance:

1. Bump `GenerationID` whenever `ShardBits` changes.
2. Keep authoritative announcers to a small operator-curated set
   (recommended: 3 instances across failure domains).
3. Deploy authoritative announcers with `Flags.PilotOnly=1` so consumers
   can distinguish operator intent from the announcer's own joins.
4. Treat non-authoritative manifests as observational (and, when
   `Flags.SourcesValid=1`, as per-publisher source contributions).
5. Warn (do not auto-shift) on observed `ShardBits` divergence — the
   adoption gates already prevent unsafe shifts, but operator visibility
   is the right place to catch misconfiguration.

---

## Interactions With Other BRCs

- **BRC-126 (Retransmission / ADVERT)** — shares the beacon group `0xFFFD` and listen port. Distinguished by MsgType byte at offset 6 (`0x20` ADVERT vs `0x40` ShardManifest). BRC-137 does not retransmit and is not retransmitted.
- **BRC-127 (Subtree group announcements)** — orthogonal: BRC-127 announces SubtreeID→GroupID bindings on `0xFFFC` and goes through the proxy. BRC-137 announces participant configuration directly on `0xFFFD`.
- **BRC-129 (Multicast addressing)** — no new index allocated. Manifests reuse the existing beacon group.

---

## Implementation

| Component                                         | File                                                        |
| ------------------------------------------------- | ----------------------------------------------------------- |
| `MsgTypeShardManifest = 0x40`, `ShardManifestHeaderSize = 64` | `shard-common/frame/frame.go`            |
| Wire format encode/decode                         | `shard-common/frame/shard_manifest.go`              |
| Daemon                                            | `shard-manifest/{main.go, sender/, config/, metrics/}` |
| Ansible deployment role                           | `manifest-infra/ansible/roles/shard-manifest/`    |
| Helm chart                                        | `shard-manifest-helm/`                              |

---

## References

- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md)
- [BRC-127: Subtree Group Announcement](brc-127-subtree-announce.md)
- [BRC-129: Multicast Group Addressing](brc-129-multicast-addressing.md)
- [BRC-137: Shard Manifest Announcement (canonical)](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0137.md)
- [Proxy/Listener Automatic Shard Configuration Plan](AutoShardConfig/auto-shard-config-plan.md)
- [Source-Specific Multicast Support Plan](SourceSpecificMulticast/ssm-support-plan.md)
