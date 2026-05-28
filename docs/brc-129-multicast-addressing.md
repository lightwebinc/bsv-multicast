# BRC-129 — Multicast Group Addressing

BRC-129 defines the IPv6 multicast group address scheme for the BSV transaction sharding pipeline, including data-plane shard groups, control-plane beacon groups, and reserved indices. The scheme is aligned with IANA's IPv6 multicast address allocation practice and the IANA-assigned Bitcoin group `FF0X::B`.

> **Canonical BRC:** [BRC-129](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md)

---

## IANA Allocation

IANA allocates IPv6 multicast group addresses on the **96-bit boundary**: the top 96 bits (bytes `[0:12]`) identify the IANA-assigned group, leaving the bottom 32 bits (bytes `[12:16]`) for application sub-allocation. The scheme below splits those 32 bits into a configurable 16-bit IANA group-id (default = `0x000B`, the IANA Bitcoin allocation) followed by a 16-bit shard index.

The on-wire default is the IANA Bitcoin allocation `FF0X::B` (group-id `0x000B`). Operators MAY override the group-id via `-mc-group-id` for testing or private deployments, but conformant deployments use `0x000B`.

---

## Address Derivation

All IPv6 multicast group addresses are derived from three components:

1. **Scope prefix** (`MCPrefix`, 2 bytes) — the first two bytes of the IPv6 address, e.g. `FF05` (site-local), `FF08` (organisation-local), or `FF0E` (global).
2. **IANA group-id** (`MCGroupID`, 2 bytes) — occupies bytes `[12:14]`, default `0x000B` (IANA Bitcoin).
3. **Shard group index** (2 bytes) — occupies bytes `[14:16]` (16-bit index space).

```text
Byte:  0  1    2  3  4  5  6  7  8  9 10 11   12 13   14 15
       [scope] [-------- IANA boundary zero -------]  [GID] [IDX]
       FF 05    00 00 00 00 00 00 00 00 00 00         00 0B  XX XX
```

The `shard.Engine.Addr(groupIndex, port)` function assembles these components into a `net.UDPAddr`.

---

## Data-Plane Shard Groups

Shard group indices occupy the range `0x0000`–`0x0FFF` (4,096 indices). `shardBits` is bounded at 12, keeping all shard indices within this range. Indices at or above `0x1000` MUST NOT be used as shard group indices.

The group index for a transaction is derived deterministically from its TxID:

```
groupIndex = binary.BigEndian.Uint32(txid[0:4]) >> (32 - shardBits)
```

---

## Free Space and Specialty Transmission Domains

Indices `0x1000`–`0xF7FF` (56,832 indices) are unassigned and reserved for future use. This range accommodates future shard group expansion and specialty transmission domains for purpose-specific multicast services.

---

## Network Service Groups

Network service groups occupy `0xF800`–`0xFFFF` (2,048 indices). Current protocol assignments are allocated from the top of this range; the remainder is reserved for future network services.

| Index    | Purpose                              | Scope  | Full address (default group-id)                       | Compressed                |
| -------- | ------------------------------------ | ------ | ----------------------------------------------------- | ------------------------- |
| `0xFFFA` | Block Header egress (BRC-135)        | varies | `FF0X:0000:0000:0000:0000:0000:<egress-gid>:FFFA`     | `FF05::<egress-gid>:FFFA` |
| `0xFFFB` | Subtree Announcements (site)         | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFB` | `FF05::B:FFFB` |
| `0xFFFB` | Subtree Announcements (org)          | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFB` | `FF08::B:FFFB` |
| `0xFFFB` | Subtree Announcements (global)       | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFB` | `FF0E::B:FFFB` |
| `0xFFFC` | Subtree Group Announcements (site)    | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFC` | `FF05::B:FFFC` |
| `0xFFFC` | Subtree Group Announcements (org)     | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFC` | `FF08::B:FFFC` |
| `0xFFFC` | Subtree Group Announcements (global)  | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFC` | `FF0E::B:FFFC` |
| `0xFFFD` | Beacon (site)                        | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFD` | `FF05::B:FFFD` |
| `0xFFFD` | Beacon (org)                         | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFD` | `FF08::B:FFFD` |
| `0xFFFD` | Beacon (global)                      | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFD` | `FF0E::B:FFFD` |
| `0xFFFE` | Block Broadcast channel              | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFE` | `FF0E::B:FFFE` |
| `0xFFFF` | _(reserved)_                         | —      | reserved                                  | do not use     |

---

## Group-ID Override

The `-mc-group-id` flag (env `MC_GROUP_ID`) configures the 16-bit IANA group-id occupying bytes `[12:14]` of every IPv6 multicast address. The default is `0x000B` (IANA Bitcoin allocation `FF0X::B`).

```
-mc-group-id 0x000B    # default (IANA Bitcoin)
-mc-group-id 0xCAFE    # private deployment / lab
```

All group addresses — both shard and network-service — inherit the same group-id. Operators with different `-mc-group-id` values use entirely disjoint multicast address spaces.

---

## Block Broadcast Channel

`FF0E::B:FFFE` (index `0xFFFE`) is the mandatory global-scope channel distributing block headers, block templates, coinbase transactions, and chained-transaction anchors, as well as other producer data useful to all network participants. This channel is mandatory for all network participants to join.

### Virtual HashKey Ingredient Indices

Several frame types share `GroupBlockBroadcast` (`0xFFFE`) as their egress destination but must form independent per-sender flows on the proxy so each carries its own monotonic `SeqNum` counter. The proxy's flow key is `(senderIPv6, groupIdx, subtreeID)`; to keep these flows separate while still emitting to the same multicast group, the proxy substitutes a distinct virtual `groupIdx` into the HashKey computation. These virtual indices **never appear in an actual IPv6 multicast address**; they exist only as inputs to XXH64-based HashKey derivation.

| Virtual index | Constant            | Used by             | Egress group |
| ------------- | ------------------- | ------------------- | ------------ |
| `0xFFF8`      | `GroupCoinbaseFlow` | BRC-133 coinbase tx | `0xFFFE`     |
| `0xFFF9`      | `GroupAnchorFlow`   | BRC-134 anchor tx   | `0xFFFE`     |

BRC-131 block announces continue to use `0xFFFE` itself as the HashKey ingredient.

---

## Block Header Egress Channel

`FF05::<egress-gid>:FFFA` (index `0xFFFA`) is used by listener nodes to re-emit standalone 80-byte block header frames (BRC-135) to downstream consumers such as SPV wallets and header-chain validators. The scope and group-id are configured via the listener's `-mc-egress-prefix` and `-mc-egress-group-id` flags, which default to the same values as the ingress fabric but can be set independently to isolate the egress domain. BRC-135 frames on this channel are NOT re-injected onto the primary `FF0E::B:FFFE` fabric to avoid feedback loops.

---

## Beacon Groups

Beacon groups support infrastructure service discovery across multiple scopes (site-local, organization-local, and global). Each beacon-enabled service instance advertises to exactly one group (set via `-beacon-scope`). Deployments requiring coverage across multiple scopes run separate instances at each scope. See the control-plane table above for complete scope and address details.

---

## Subtree Group Announcements

Subtree group announcements (BRC-127, MsgType `0x30`) advertise SubtreeID–GroupID bindings so listeners can discover related groups of transactions and filter dynamically for downstream special interest networks. These 64-byte datagrams are forwarded by the proxy to the `GroupSubtreeGroupAnnounce` group (`0xFFFC`).

Like beacon groups, subtree group announcements support multiple scopes (site-local, organization-local, and global). See the network-service table above for complete scope and address details.

---

## Subtree Data Announcements

Subtree data frames (BRC-132, FrameVer `0x05`) deliver the full contents of a Merkle subtree (transaction hashes and optional metadata) to all subscribed listeners on the `GroupSubtreeAnnounce` group (`0xFFFB`). The subtree is identified by its Merkle root hash (SubtreeID).

BRC-132 frames are large (32–48 MB per subtree at 1M transactions) and are fragmented using BRC-130. Each BRC-130 fragment carries `OrigFrameVer = 0x05` so that the reassembly path can deliver the reconstituted payload to the correct handler.

Like beacon groups, subtree data announcements support multiple scopes (site-local, organization-local, and global). See the network-service table above for complete scope and address details. See [BRC-132](brc-132-subtree-data.md) for the frame format specification.

---

## Implementation

- **Group derivation:** `shard-common/shard/shard.go` — `Engine.Addr(groupIndex uint32, port int)` (only the low 16 bits of `groupIndex` are used).
- **Network-service group helper:** `shard-common/shard/control.go` — `GroupAddr(scopePrefix uint16, groupID uint16, idx GroupIdx)` (standalone; not bound to Engine scope).
- **Group index type:** `type GroupIdx uint16` — typed wrapper for the 16-bit IANA group index in bytes 14–15. Provides a `String()` method returning a stable snake_case label (`"block_broadcast"`, `"beacon"`, etc.) used in metrics and logs.
- **Constants:** `GroupBlockHeader = 0xFFFA`, `GroupSubtreeAnnounce = 0xFFFB`, `GroupSubtreeGroupAnnounce = 0xFFFC`, `GroupBeacon = 0xFFFD`, `GroupBlockBroadcast = 0xFFFE`.
- **Virtual HashKey ingredients (not multicast addresses):** `GroupCoinbaseFlow = 0xFFF8`, `GroupAnchorFlow = 0xFFF9`.
- **Default group-id:** `shard.DefaultGroupID = 0x000B` (IANA Bitcoin).

---

## References

- [BRC-129: IPv6 Multicast Group Address Assignments](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md) — published BRC
