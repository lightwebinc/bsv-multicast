# BRC-TBD-addressing — Multicast Group Address Assignments

BRC-TBD-addressing defines the IPv6 multicast group address scheme for the BSV transaction sharding pipeline, including data-plane shard groups, control-plane beacon groups, and reserved indices. The scheme is aligned with IANA's IPv6 multicast address allocation practice and the IANA-assigned Bitcoin group `FF0X::B`.

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

Shard group indices occupy the range `0x0000`–`0xFFFB` (the lower 65 532 indices of the 16-bit shard space; the top four indices are reserved for control). With `shardBits = N`, the proxy creates `2^N` groups using indices `0` through `2^N − 1`. Practical `shardBits` is bounded at 15 to keep the data-plane orthogonal to the control-plane reservations.

The group index for a transaction is derived deterministically from its TxID:

```
groupIndex = binary.BigEndian.Uint32(txid[0:4]) >> (32 - shardBits)
```

---

## Control-Plane Reserved Indices

Control-plane groups occupy the top of the 16-bit shard space, ensuring orthogonality with all practical shard configurations (`shardBits ≤ 15`).

| Index    | Purpose                   | Scope  | Full address (default group-id)           | Compressed       |
| -------- | ------------------------- | ------ | ----------------------------------------- | ---------------- |
| `0xFFFC` | Subtree announce (site)   | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFC` | `FF05::B:FFFC`   |
| `0xFFFC` | Subtree announce (global) | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFC` | `FF0E::B:FFFC`   |
| `0xFFFD` | Beacon (site)             | `FF05` | `FF05:0000:0000:0000:0000:0000:000B:FFFD` | `FF05::B:FFFD`   |
| `0xFFFD` | Beacon (org)              | `FF08` | `FF08:0000:0000:0000:0000:0000:000B:FFFD` | `FF08::B:FFFD`   |
| `0xFFFD` | Beacon (global)           | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFD` | `FF0E::B:FFFD`   |
| `0xFFFE` | Control channel           | `FF0E` | `FF0E:0000:0000:0000:0000:0000:000B:FFFE` | `FF0E::B:FFFE`   |
| `0xFFFF` | _(reserved)_              | —      | reserved                                  | do not use       |

When the IANA group-id is overridden via `-mc-group-id`, the same group-id is interleaved at bytes `[12:14]` for both control-plane and data-plane addresses. The control-plane indices remain orthogonal to all shard indices regardless.

---

## Beacon Groups

Three beacon groups are defined to support intra-site, organisation-wide, and inter-AS endpoint discovery (see [BRC-126](brc-126-retransmission-protocol.md)). Each `bitcoin-retry-endpoint` instance beacons to **exactly one** group (set via `-beacon-scope`). Deployments that need coverage across multiple scopes run separate instances at each scope.

### Site Beacon (`FF05::B:FFFD`)

- **Scope:** Site-local (`FF05`).
- **Purpose:** Intra-site retry endpoint discovery. All listeners join this group at startup.
- **Sender:** Every `bitcoin-retry-endpoint` with `-beacon-scope site`.
- **Content:** 56-byte ADVERT datagram (BRC-126).

### Org Beacon (`FF08::B:FFFD`)

- **Scope:** Organization-local (`FF08`).
- **Purpose:** Organization-wide retry endpoint discovery. Org-level listeners join this group at startup.
- **Sender:** A `bitcoin-retry-endpoint` configured to beacon on the org scope. Note: org scope (`0x08`) is defined in the BRC-126 wire format but `-beacon-scope=org` is not currently a supported flag value. Use two instances with `-beacon-scope site` and `-beacon-scope global` to cover both levels.
- **Content:** 56-byte ADVERT datagram (BRC-126).

### Global Beacon (`FF0E::B:FFFD`)

- **Scope:** Global (`FF0E`).
- **Purpose:** Inter-AS endpoint discovery via MP-BGP MVPN or MSDP. Tier 0 (source-adjacent) endpoints beacon here so remote-AS listeners can discover them.
- **Sender:** Every `bitcoin-retry-endpoint` with `-beacon-scope global`.
- **Transport:** Requires MP-BGP multicast routing to propagate across AS boundaries.

### Multi-Beacon Rationale

A single global-scope group would work but leaks discovery traffic to all ASes even when only local endpoints are needed. Splitting by scope allows operators to place beacon traffic at the appropriate boundary without running a single fat process that straddles multiple scopes:

- Local-only deployments run endpoints with `-beacon-scope site` only.
- Organisation-wide deployments run separate instances with `-beacon-scope site` and `-beacon-scope global`; the org scope wire byte is reserved for future use.
- Multi-AS deployments add a third instance with `-beacon-scope global`.
- Each scope level can use independent Tier/Preference tuning.

---

## Block Template / Control Channel

`FF0E::B:FFFE` (index `0xFFFE`) is reserved for a future global-scope control channel distributing block templates and other producer-broadcast data. The address is defined here to complete the control-plane namespace; the data format and producer design are out of scope (separate BRC).

---

## Group-ID Override

The `-mc-group-id` flag (env `MC_GROUP_ID`) configures the 16-bit IANA group-id occupying bytes `[12:14]` of every IPv6 multicast address derived by this stack. The default is `0x000B`, matching the IANA Bitcoin allocation `FF0X::B`. Example overrides:

```
-mc-group-id 0x000B    # default (IANA Bitcoin)
-mc-group-id 0xCAFE    # private deployment / lab
```

All group addresses — both shard and control-plane — inherit the same group-id at bytes `[12:14]`. Two operators with different `-mc-group-id` values use entirely disjoint multicast address spaces.

---

## Implementation

- **Group derivation:** `bitcoin-shard-common/shard/shard.go` — `Engine.Addr(groupIndex uint32, port int)` (only the low 16 bits of `groupIndex` are used).
- **Control group helper:** `bitcoin-shard-common/shard/control.go` — `ControlGroupAddr(scopePrefix, groupID, index uint16)` (standalone; not bound to Engine scope).
- **Constants:** `CtrlGroupSubtreeAnnounce = 0xFFFC`, `CtrlGroupBeacon = 0xFFFD`, `CtrlGroupControl = 0xFFFE`.
- **Default group-id:** `shard.DefaultGroupID = 0x000B` (IANA Bitcoin).
