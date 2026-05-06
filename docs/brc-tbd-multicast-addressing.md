# BRC-TBD-addressing — Multicast Group Address Assignments

BRC-TBD-addressing defines the IPv6 multicast group address scheme for the BSV transaction sharding pipeline, including data-plane shard groups, control-plane beacon groups, and reserved indices.

> **Status:** To be submitted as BRC-TBD-addressing PR to jefflightweb/BRCs.

---

## Address Derivation

All IPv6 multicast group addresses are derived from three components:

1. **Scope prefix** (`MCPrefix`, 2 bytes) — the first two bytes of the IPv6 address, e.g. `FF05` (site-local) or `FF0E` (global).
2. **Middle bytes** (`MCMiddleBytes`, 11 bytes) — operator-configurable prefix set via `-mc-base-addr`; occupies bytes 2–12 of the IPv6 address. Defaults to all zeros.
3. **Group index** (3 bytes) — occupies bytes 13–15 of the IPv6 address.

```text
Byte:  0  1    2  3  4  5  6  7  8  9  10 11 12 13 14 15
       [scope] [--------- middle bytes -------] [index]
       FF 05   00 00 00 00 00 00 00 00 00 00 00 XX XX XX
```

The `shard.Engine.Addr(groupIndex, port)` function assembles these components into a `net.UDPAddr`.

---

## Data-Plane Shard Groups

Shard group indices occupy the range `0x000000`–`0x7FFFFF` (24-bit index space, lower half). With `shardBits = N`, the proxy creates `2^N` groups using indices `0` through `2^N - 1`.

The group index for a transaction is derived deterministically from its TxID:

```
groupIndex = binary.BigEndian.Uint32(txid[0:4]) >> (32 - shardBits)
```

---

## Control-Plane Reserved Indices

Control-plane groups occupy the top of the 24-bit index space, ensuring orthogonality with all practical shard configurations (`shardBits ≤ 23`).

| Index      | Purpose         | Scope  | Full address (no middle bytes)            | Compressed      |
| ---------- | --------------- | ------ | ----------------------------------------- | --------------- |
| `0xFFFFFD` | Beacon (site)   | `FF05` | `FF05:0000:0000:0000:0000:0000:00FF:FFFD` | `FF05::FF:FFFD` |
| `0xFFFFFD` | Beacon (org)    | `FF08` | `FF08:0000:0000:0000:0000:0000:00FF:FFFD` | `FF08::FF:FFFD` |
| `0xFFFFFD` | Beacon (global) | `FF0E` | `FF0E:0000:0000:0000:0000:0000:00FF:FFFD` | `FF0E::FF:FFFD` |
| `0xFFFFFE` | Control channel | `FF0E` | `FF0E:0000:0000:0000:0000:0000:00FF:FFFE` | `FF0E::FF:FFFE` |
| `0xFFFFFF` | _(reserved)_    | —      | reserved                                  | do not use      |

When middle bytes are configured (via `-mc-base-addr`), the same 11 bytes are interleaved at positions 2–12, shifting byte 12 from `0x00` to the last middle byte. The control-plane group indices remain orthogonal to all shard indices regardless.

---

## Beacon Groups

Three beacon groups are defined to support intra-site, organisation-wide, and inter-AS endpoint discovery (see [BRC-TBD-retransmission](brc-tbd-retransmission-protocol.md)). Each `bitcoin-retry-endpoint` instance beacons to **exactly one** group (set via `-beacon-scope`). Deployments that need coverage across multiple scopes run separate instances at each scope.

### Site Beacon (`FF05::FF:FFFD` + middle bytes)

- **Scope:** Site-local (`FF05`).
- **Purpose:** Intra-site retry endpoint discovery. All listeners join this group at startup.
- **Sender:** Every `bitcoin-retry-endpoint` with `-beacon-scope site`.
- **Content:** 56-byte ADVERT datagram (BRC-TBD-retransmission).

### Org Beacon (`FF08::FF:FFFD` + middle bytes)

- **Scope:** Organization-local (`FF08`).
- **Purpose:** Organization-wide retry endpoint discovery. Org-level listeners join this group at startup.
- **Sender:** Every `bitcoin-retry-endpoint` with `-beacon-scope org`.
- **Content:** 56-byte ADVERT datagram (BRC-TBD-retransmission).

### Global Beacon (`FF0E::FF:FFFD` + middle bytes)

- **Scope:** Global (`FF0E`).
- **Purpose:** Inter-AS endpoint discovery via MP-BGP MVPN or MSDP. Tier 0 (source-adjacent) endpoints beacon here so remote-AS listeners can discover them.
- **Sender:** Every `bitcoin-retry-endpoint` with `-beacon-scope global`.
- **Transport:** Requires MP-BGP multicast routing to propagate across AS boundaries.

### Multi-Beacon Rationale

A single global-scope group would work but leaks discovery traffic to all ASes even when only local endpoints are needed. Splitting by scope allows operators to place beacon traffic at the appropriate boundary without running a single fat process that straddles multiple scopes:

- Local-only deployments run endpoints with `-beacon-scope site` only.
- Organisation-wide deployments add a second instance with `-beacon-scope org`.
- Multi-AS deployments add a third instance with `-beacon-scope global`.
- Each scope level can use independent Tier/Preference tuning.

---

## Block Template / Control Channel

`FF0E::FF:FFFE` (index `0xFFFFFE`) is reserved for a future global-scope control channel distributing block templates and other producer-broadcast data. The address is defined here to complete the control-plane namespace; the data format and producer design are out of scope (separate BRC).

---

## Site-Specific Prefix Interaction

The `-mc-base-addr` flag configures the operator's middle bytes. Example:

```
-mc-base-addr "FF05::DEAD:BEEF:0"
```

This sets `MCPrefix = 0xFF05` and fills middle bytes from the address. All group addresses — both shard and control-plane — inherit these middle bytes. Two operators with different `-mc-base-addr` values use entirely disjoint multicast address spaces.

---

## Implementation

- **Group derivation:** `bitcoin-shard-common/shard/shard.go` — `Engine.Addr(groupIndex, port)`
- **Control group helper:** `bitcoin-shard-common/shard/control.go` — `ControlGroupAddr(scopePrefix, middleBytes, index)` (standalone; not bound to Engine scope)
- **Constants:** `CtrlGroupBeacon = 0xFFFFFD`, `CtrlGroupControl = 0xFFFFFE`
