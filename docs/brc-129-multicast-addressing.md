# BRC-129 — Multicast Group Addressing

BRC-129 defines the IPv6 multicast group address scheme for the BSV transaction sharding pipeline, including data-plane shard groups, control-plane beacon groups, and reserved indices. The scheme is aligned with IANA's IPv6 multicast address allocation practice and the IANA-assigned Bitcoin group `FF0X::B`.

> **Canonical BRC:** [BRC-129](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0129.md)

---

## IANA Allocation

IANA allocates IPv6 multicast group addresses on the **96-bit boundary**: the top 96 bits (bytes `[0:12]`) identify the IANA-assigned group, leaving the bottom 32 bits (bytes `[12:16]`) for application sub-allocation. The scheme below splits those 32 bits into a configurable 16-bit IANA group-id (default = `0x000B`, the IANA Bitcoin allocation) followed by a 16-bit shard index.

The on-wire default is the IANA Bitcoin allocation `FF0X::B` (group-id `0x000B`). Operators MAY override the group-id via `-mc-group-id` for testing or private deployments, but conformant deployments use `0x000B`.

This bottom-32-bit split (group-id + shard index) is identical under both source modes (see [Source Mode and Address Range](#source-mode-and-address-range)); only the high-order prefix bytes `[0:2]` differ.

---

## Source Mode and Address Range

The fabric runs in one of two source modes. **Any-Source Multicast (ASM)** is the default; **Source-Specific Multicast (SSM)** is an opt-in transport mode for deployments needing inter-domain distribution (see [Source-Specific Multicast (SSM)](#source-specific-multicast-ssm)). The mode selects the address range via the multicast prefix byte `[1]`, which encodes `flags(4) | scope(4)`:

- **ASM** uses the well-known permanent prefix (`flags = 0`) → `FF0x`.
- **SSM** uses the RFC 4607 SSM range `FF3x::/32` (`flags = 3`) → `FF3x`.

The scope nibble (`5` site, `8` org, `E` global) keeps its meaning across modes, but SSM is implemented at site and global scope only — there is no SSM org-scope prefix. RFC 8815 deprecates inter-domain ASM, so global scope SHOULD be SSM (the ASM configuration path does not reject `-scope global`; running ASM at global scope is out of spec, not out of reach).

| Mode | Site scope (intra-domain) | Global scope (inter-domain)              |
| ---- | ------------------------- | ---------------------------------------- |
| ASM  | `FF05::B:idx`             | discouraged (RFC 8815; not rejected)     |
| SSM  | `FF35::B:idx`             | `FF3E::B:idx`                            |

The group-id (`0x000B`) and the shard-index field are preserved across modes — only the high 32 bits change. Addresses elsewhere in this doc are written in ASM (`FF0x`) form; under SSM, substitute the corresponding `FF3x` prefix.

---

## Address Derivation

All IPv6 multicast group addresses are derived from three components:

1. **Multicast prefix** (`MCPrefix`, 2 bytes) — the first two bytes of the IPv6 address, encoding `flags(4) | scope(4)`. ASM: `FF02` (link, lab use), `FF05` (site), `FF08` (org), `FF0E` (global); SSM: `FF35` (site), `FF3E` (global) — SSM has no link- or org-scope prefix (see [Source Mode and Address Range](#source-mode-and-address-range)).
2. **IANA group-id** (`MCGroupID`, 2 bytes) — occupies bytes `[12:14]`, default `0x000B` (IANA Bitcoin).
3. **Shard group index** (2 bytes) — occupies bytes `[14:16]` (16-bit index space).

```text
Byte:  0  1    2  3  4  5  6  7  8  9 10 11   12 13   14 15
       [scope] [-------- IANA boundary zero -------]  [GID] [IDX]
       FF 05    00 00 00 00 00 00 00 00 00 00         00 0B  XX XX
```

The `shard.Engine.Addr(groupIndex, port)` function assembles these components into a `net.UDPAddr` from the Engine's configured `mcPrefix` (which carries the ASM/SSM flags nibble and scope).

---

## Data-Plane Shard Groups

Shard group indices occupy the range `0x0000`–`0x0FFF` (4,096 indices). `shardBits` is bounded at 12, keeping all shard indices within this range. Indices at or above `0x1000` MUST NOT be used as shard group indices.

The group index for a transaction is derived deterministically from its TxID:

```
groupIndex = binary.BigEndian.Uint32(txid[0:4]) >> (32 - shardBits)
```

---

## Object Planes and Specialty Transmission Domains

Indices `0x1000`–`0xEFFF` are partitioned into **object planes** by the index high nibble ([BRC-148](brc-148-shard-domain-beef-plane.md): `PlaneBase(domain) = domain << 12`). Domain `0x1` (`0x1000`–`0x1FFF`) is the allocated BEEF object plane; domains `0x2`–`0xE` are reserved for future object planes and specialty transmission domains. Domain `0xF` MUST NOT be assigned as a plane base — its slot would overlap the network service range — so the residual band `0xF000`–`0xF7FF` remains reserved; implementations MUST NOT join or transmit to it.

---

## Network Service Groups

Network service groups occupy `0xF800`–`0xFFFF` (2,048 indices). Current protocol assignments are allocated from the top of this range; the remainder is reserved for future network services.

| Index    | Purpose                              | Scope  | Full address (default group-id)                       | Compressed                |
| -------- | ------------------------------------ | ------ | ----------------------------------------------------- | ------------------------- |
| `0xFFFA` | Block Header egress (BRC-135)        | varies | `FF0X:0000:0000:0000:0000:0000:<egress-gid>:FFFA`     | `FF05::<egress-gid>:FFFA` |
| `0xFFFB` | Subtree Announcements (site)         | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFB` | `FF05::B:FFFB` |
| `0xFFFB` | Subtree Announcements (org)          | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFB` | `FF08::B:FFFB` |
| `0xFFFB` | Subtree Announcements (global)       | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFB` | `FF0E::B:FFFB` |
| `0xFFFC` | Subtree Group Announcements (site)   | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFC` | `FF05::B:FFFC` |
| `0xFFFC` | Subtree Group Announcements (org)    | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFC` | `FF08::B:FFFC` |
| `0xFFFC` | Subtree Group Announcements (global) | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFC` | `FF0E::B:FFFC` |
| `0xFFFD` | Beacon (site)                        | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFD` | `FF05::B:FFFD` |
| `0xFFFD` | Beacon (org)                         | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFD` | `FF08::B:FFFD` |
| `0xFFFD` | Beacon (global)                      | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFD` | `FF0E::B:FFFD` |
| `0xFFFE` | Block Broadcast channel              | `FF0E`* | `FF0E:0000:0000:0000:0000:0000:000B:FFFE` | `FF0E::B:FFFE` |
| `0xFFFF` | _(reserved)_                         | —      | reserved                                  | do not use     |

\* Scope values are the normative deployment posture. The implementation derives every control-plane address from the deployment's configured scope (`-scope`), so a site-scoped deployment emits `FF05::B:FFFE`; global (`FF0E`/`FF3E`) is the posture for inter-domain reach.

---

## Group-ID Override

The `-mc-group-id` flag (env `MC_GROUP_ID`) configures the 16-bit IANA group-id occupying bytes `[12:14]` of every IPv6 multicast address. The default is `0x000B` (IANA Bitcoin allocation `FF0X::B`).

```
-mc-group-id 0x000B    # default (IANA Bitcoin)
-mc-group-id 0xCAFE    # private deployment / lab
```

All group addresses — both shard and network-service — inherit the same group-id. Operators with different `-mc-group-id` values use entirely disjoint multicast address spaces.

---

## Source-Specific Multicast (SSM)

SSM is a transport mode only: the frame format (BRC-124), the NACK protocol (BRC-126), the `HashKey` computation, and the shard derivation are all unchanged. SSM affects only the address range (the high 32 bits, per [Source Mode and Address Range](#source-mode-and-address-range)) and how receivers join a group.

- **Joins.** Under ASM, receivers perform an any-source `(*,G)` join. Under SSM, receivers perform a source-specific `(S,G)` join per RFC 3678 (`MCAST_JOIN_SOURCE_GROUP`), one join per `(source, group)` pair.
- **Distinct source per publisher.** Each publisher emits from a distinct, stable unicast source address (`bindSource`). Required by PIM-SSM reverse-path forwarding; preserves the per-publisher `HashKey` flow identity. Anycast/shared-source emission is not supported under SSM — a single stable identity uses VRRP active-standby (failover, not load distribution).
- **Source discovery.** Receivers learn publisher sources before issuing `(S,G)` joins:
  - **Data-plane sources** flow through the shard manifest (BRC-139 `Flags.SourcesValid`); receivers enable manifest consumption (`-manifest-consumer-enabled` / Helm `config.autoConfig.*`) and union the source set across currently-valid manifests.
  - **Control-plane groups** (beacon, manifest, subtree-announce) are joined against per-group bootstrap source lists (`-ssm-bootstrap-*` flags / Helm `config.ssmBootstrap.*`, IPv6 literals or DNS names re-resolved on refresh), since their sources cannot be discovered from within the group.

Deployment postures combining ASM/SSM data and control planes (and their PIM/fabric prerequisites) are out of scope here — see [Source-Specific Multicast (SSM)](../DESIGN.md#source-specific-multicast-ssm) in DESIGN.md.

---

## Block Broadcast Channel

Index `0xFFFE` is the mandatory global-scope channel distributing block headers, block templates, coinbase transactions, and chained-transaction anchors, as well as other producer data useful to all network participants. All network participants join this group. The concrete address depends on the source mode: under ASM it is `FF0E::B:FFFE` (intra-domain only, since RFC 8815 deprecates inter-domain ASM); inter-domain distribution uses the SSM address `FF3E::B:FFFE`.

### Virtual HashKey Ingredient Indices

Several frame types share `GroupBlockBroadcast` (`0xFFFE`) as their egress destination but must form independent per-sender flows on the proxy so each carries its own monotonic `SeqNum` counter. The proxy's flow key is `(senderIPv6, groupIdx, subtreeID)`; to keep these flows separate while still emitting to the same multicast group, the proxy substitutes a distinct virtual `groupIdx` into the HashKey computation. These virtual indices **never appear in an actual IPv6 multicast address**; they exist only as inputs to XXH64-based HashKey derivation.

| Virtual index | Constant            | Used by             | Egress group |
| ------------- | ------------------- | ------------------- | ------------ |
| `0xFFF8`      | `GroupCoinbaseFlow` | BRC-133 coinbase tx (deprecated — legacy frames only) | `0xFFFE`     |
| `0xFFF9`      | `GroupAnchorFlow`   | BRC-134 anchor tx   | `0xFFFE`     |

BRC-131 block announces continue to use `0xFFFE` itself as the HashKey ingredient.

---

## Block Header Egress Channel

`FF05::<egress-gid>:FFFA` (index `0xFFFA`) is used by listener nodes to re-emit standalone 80-byte block header frames (BRC-135) to downstream consumers such as SPV wallets and header-chain validators. The channel is opt-in (`-header-mc-egress-enabled`, default off); its scope and group-id are configured via the listener's `-header-mc-egress-scope` and `-header-mc-egress-group-id` flags, which default to the same values as the ingress fabric but can be set independently to isolate the egress domain (the separate `-mc-egress-*` flags configure the general data-frame multicast egress, a different sender). BRC-135 frames on this channel are NOT re-injected onto the primary `FF0E::B:FFFE` fabric to avoid feedback loops.

---

## Beacon Groups

Beacon groups support infrastructure service discovery across multiple scopes (site-local, organization-local, and global). Each beacon-enabled service instance advertises to one or more groups via `-beacon-scope` (`site|org|global|both|all` — `both`/`all` emit to site, org, and global from a single instance). See the control-plane table above for complete scope and address details.

---

## Subtree Group Announcements

Subtree group announcements (BRC-127, MsgType `0x30`) advertise SubtreeID–GroupID bindings so listeners can discover related groups of transactions and filter dynamically for downstream special interest networks. These 64-byte datagrams are forwarded by the proxy to the `GroupSubtreeGroupAnnounce` group (`0xFFFC`).

Like beacon groups, subtree group announcements support multiple scopes (site-local, organization-local, and global). See the network-service table above for complete scope and address details.

---

## Subtree Data Announcements

Subtree data frames (BRC-132, FrameVer `0x05`) deliver the full contents of a Merkle subtree (transaction hashes and optional metadata) to all subscribed listeners on the `GroupSubtreeDataAnnounce` group (`0xFFFB`). The subtree is identified by its Merkle root hash (SubtreeID).

BRC-132 frames are large (32–48 MB per subtree at 1M transactions) and are fragmented using BRC-130. Each BRC-130 fragment carries `OrigFrameVer = 0x05` so that the reassembly path can deliver the reconstituted payload to the correct handler.

Like beacon groups, subtree data announcements support multiple scopes (site-local, organization-local, and global). See the network-service table above for complete scope and address details. See [BRC-132](brc-132-subtree-data.md) for the frame format specification.

---

## Implementation

- **Group derivation:** `shard-common/shard/shard.go` — `New(mcPrefix, groupID, shardBits)` builds the Engine; `mcPrefix` is the full upper 16 bits carrying flags+scope (`0xFF05` ASM site, `0xFF35` SSM site, `0xFF3E` SSM global). `Engine.Addr(groupIndex uint32, port int)` assembles the address from that prefix (only the low 16 bits of `groupIndex` are used) — unchanged across modes.
- **Source mode:** `shard-common/shard/mode.go` — `SourceMode` (`SourceModeASM`/`SourceModeSSM`, `ParseSourceMode`) selects which `mcPrefix` is built.
- **Group joins:** `shard-common/netjoin/netjoin.go` — `Join(fd, ifaceIdx, group, sources)` issues `MCAST_JOIN_SOURCE_GROUP` `(S,G)` joins (RFC 3678) when `sources` is non-empty (SSM), or a plain `(*,G)` join otherwise (ASM); diffs and rate-limits join/leave churn.
- **Network-service group helper:** `shard-common/shard/control.go` — `GroupAddr(scopePrefix uint16, groupID uint16, idx GroupIdx)` (standalone; not bound to Engine scope).
- **Group index type:** `type GroupIdx uint16` — typed wrapper for the 16-bit IANA group index in bytes 14–15. Provides a `String()` method returning a stable snake_case label (`"block_broadcast"`, `"beacon"`, etc.) used in metrics and logs.
- **Constants:** `GroupBlockHeader = 0xFFFA`, `GroupSubtreeDataAnnounce = 0xFFFB`, `GroupSubtreeGroupAnnounce = 0xFFFC`, `GroupBeacon = 0xFFFD`, `GroupBlockBroadcast = 0xFFFE`.
- **Virtual HashKey ingredients (not multicast addresses):** `GroupCoinbaseFlow = 0xFFF8` (deprecated — legacy BRC-133 frames only), `GroupAnchorFlow = 0xFFF9`.
- **Default group-id:** `shard.DefaultGroupID = 0x000B` (IANA Bitcoin).

---

## References

- [BRC-129: IPv6 Multicast Group Address Assignments](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0129.md) — canonical spec
- [BRC-139: Shard Manifest Announcement](brc-139-shard-manifest.md) — distributes the publisher source set for SSM data-plane source discovery
- [Source-Specific Multicast (SSM)](../DESIGN.md#source-specific-multicast-ssm) — deployment postures and fabric prerequisites
- [RFC 4607](https://www.rfc-editor.org/rfc/rfc4607) — Source-Specific Multicast (`FF3x::/32` range)
- [RFC 3678](https://www.rfc-editor.org/rfc/rfc3678) — `MCAST_JOIN_SOURCE_GROUP` `(S,G)` join API
- [RFC 8815](https://www.rfc-editor.org/rfc/rfc8815) — deprecating ASM for inter-domain multicast
