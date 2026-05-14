# BRC-129 — Multicast Group Addressing

BRC-129 defines the IPv6 multicast group address scheme for the BSV transaction sharding pipeline, including data-plane shard groups, control-plane beacon groups, and reserved indices. The scheme is aligned with IANA's IPv6 multicast address allocation practice and the IANA-assigned Bitcoin group `FF0X::B`.

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

| Index    | Purpose                              | Scope  | Full address (default group-id)           | Compressed     |
| -------- | ------------------------------------ | ------ | ----------------------------------------- | -------------- |
| `0xFFFB` | Subtree Announcements (site)         | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFB` | `FF05::B:FFFB` |
| `0xFFFB` | Subtree Announcements (org)          | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFB` | `FF08::B:FFFB` |
| `0xFFFB` | Subtree Announcements (global)       | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFB` | `FF0E::B:FFFB` |
| `0xFFFC` | Subtree Group Announcements (site)   | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFC` | `FF05::B:FFFC` |
| `0xFFFC` | Subtree Group Announcements (org)    | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFC` | `FF08::B:FFFC` |
| `0xFFFC` | Subtree Group Announcements (global) | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFC` | `FF0E::B:FFFC` |
| `0xFFFD` | Beacon (site)                        | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFD` | `FF05::B:FFFD` |
| `0xFFFD` | Beacon (org)                         | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFD` | `FF08::B:FFFD` |
| `0xFFFD` | Beacon (global)                      | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFD` | `FF0E::B:FFFD` |
| `0xFFFE` | Block Control channel                | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFE` | `FF0E::B:FFFE` |
| `0xFFFF` | _(reserved)_                         | —      | reserved                                  | do not use     |

---

## Group-ID Override

The `-mc-group-id` flag (env `MC_GROUP_ID`) configures the 16-bit IANA group-id occupying bytes `[12:14]` of every IPv6 multicast address. The default is `0x000B` (IANA Bitcoin allocation `FF0X::B`).

```
-mc-group-id 0x000B    # default (IANA Bitcoin)
-mc-group-id 0xCAFE    # private deployment / lab
```

All group addresses — both shard and control-plane — inherit the same group-id. Operators with different `-mc-group-id` values use entirely disjoint multicast address spaces.

---

## Block Control Channel

`FF0E::B:FFFE` (index `0xFFFE`) is used for mandatory global-scope control channel distributing block headers, block templates, coinbase transactions, and chained-transaction anchors, as well as other producer data useful to all network participants. This channel is mandatory for all network participants to join.

---

## Beacon Groups

Beacon groups support infrastructure service discovery across multiple scopes (site-local, organization-local, and global). Each beacon-enabled service instance advertises to exactly one group (set via `-beacon-scope`). Deployments requiring coverage across multiple scopes run separate instances at each scope. See the control-plane table above for complete scope and address details.

---

## Subtree Group Announcements

Subtree group announcements are used to announce the presence of new subtree inclusion in a group of subtrees to other nodes in the network. This is used by listeners to discover related groups of transactions and allow for automated filtering for downstream special interest networks.

Like beacon groups, subtree group announcements support multiple scopes (site-local, organization-local, and global). See the control-plane table above for complete scope and address details.

---

## Subtree Announcements

Subtree announcements are used to announce the presence of a subtree and its ordered, validated, and linked transaction IDs to other nodes in the network. The subtree is a Merkle tree structure that rolls up into the Merkle root hash, which forms the subtree ID.

Like beacon groups, subtree announcements support multiple scopes (site-local, organization-local, and global). See the control-plane table above for complete scope and address details.

---

## Implementation

- **Group derivation:** `bitcoin-shard-common/shard/shard.go` — `Engine.Addr(groupIndex uint32, port int)` (only the low 16 bits of `groupIndex` are used).
- **Control group helper:** `bitcoin-shard-common/shard/control.go` — `ControlGroupAddr(scopePrefix, groupID, index uint16)` (standalone; not bound to Engine scope).
- **Constants:** `CtrlGroupSubtreeAnnounce = 0xFFFB`, `CtrlGroupSubtreeGroupAnnounce = 0xFFFC`, `CtrlGroupBeacon = 0xFFFD`, `CtrlGroupControl = 0xFFFE`.
- **Default group-id:** `shard.DefaultGroupID = 0x000B` (IANA Bitcoin).
