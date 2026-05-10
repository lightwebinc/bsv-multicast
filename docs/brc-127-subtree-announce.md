# BRC-127 — Subtree Group Announcement

BRC-127 defines the protocol for dynamically advertising SubtreeID–GroupID bindings over the multicast fabric. Producers send periodic `SubtreeAnnounce` datagrams to the proxy via TCP; the proxy forwards them to the `CtrlGroupSubtreeAnnounce` multicast group (`FF05::FF:FFFC`). Listeners subscribe to this group and populate a dynamic registry used at the subtree filter layer.

> **Status:** To be submitted as BRC-127 PR to github.com/bitcoin-sv/BRCs

---

## Purpose

Static subtree filtering (`-subtree-include`, `-subtree-exclude`) requires operator reconfiguration when new subtrees are introduced. BRC-127 allows producers (block assemblers, transaction routers) to dynamically announce which SubtreeIDs belong to which logical groups. Listeners subscribe to named groups and automatically accept any SubtreeID announced for that group, without a config change or service restart.

---

## SubtreeGroupAnnounce Wire Format (`MsgType 0x30`) — 64 bytes

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x30 (SubtreeAnnounce)
     7     1  Flags (reserved 0x00)
     8    32  SubtreeID  — 32-byte SHA-256 subtree root hash (from BRC-124 frame header)
    40    16  GroupID    — 128-bit group identifier (big-endian)
    56     4  Epoch      — Unix timestamp (seconds) when this announcement was created
    60     2  TTL        — Validity in seconds; 0 = use listener's configured default
    62     2  Reserved
```

One datagram maps one SubtreeID to one GroupID. Producers send one datagram per `(SubtreeID, GroupID)` pair per announcement cycle.

---

## Control-Plane Multicast Group

SubtreeAnnounce datagrams are distributed on the control-plane group:

| Index      | Scope | Compressed Address | Constant                   |
| ---------- | ----- | ------------------ | -------------------------- |
| `0xFFFFFC` | FF05  | `FF05::FF:FFFC`    | `CtrlGroupSubtreeAnnounce` |

Defined in `bitcoin-shard-common/shard/control.go`. Occupies the top of the 24-bit index space and is orthogonal to all data-plane shard groups (`shardBits ≤ 23`). When middle bytes are configured via `-mc-base-addr`, the same operator prefix applies to this group as to all other multicast addresses.

---

## Flow

```text
Producer (subtx-gen, block assembler)
    │
    │  TCP connection to proxy (e.g. [::1]:9002)
    │  One 64-byte SubtreeAnnounce per (SubtreeID, GroupID) pair
    ▼
bitcoin-shard-proxy  (TCP ingress, worker/tcp.go)
    │  Detects MsgType=0x30 at buf[6]
    │  Reads 64-byte datagram
    │  Calls ForwardControl(targets, buf, CtrlGroupSubtreeAnnounce, egressPort)
    ▼
IPv6 multicast fabric  →  FF05::FF:FFFC:9001
    │
    ▼
bitcoin-shard-listener
    │  SubtreeAnnounceListener joins FF05::FF:FFFC
    │  DecodeSubtreeAnnounce → subtreegroup.Registry.Add(GroupID, SubtreeID, TTL)
    ▼
filter.Allow (hot path, per received frame)
    │  groupReg.Contains(frame.SubtreeID)
    │  true  → forward to downstream consumer
    │  false → drop with "subtree_include_miss" (unless static include also matches)
```

---

## Proxy Forwarding

The proxy TCP ingress (`worker/tcp.go`) handles SubtreeAnnounce datagrams transparently in the same connection loop as BRC-124 frames:

1. Read 44 bytes (common TCP preamble).
2. Check `buf[6]`:
   - `0x01` or `0x02` → data frame path (BRC-12 / BRC-124).
   - `0x30` → control frame path.
3. Read remaining 20 bytes to complete the 64-byte datagram.
4. Call `fwd.ForwardControl(targets, ctrlBuf[:], shard.CtrlGroupSubtreeAnnounce, fwd.EgressPort())`.

`ForwardControl` derives the destination using `shard.ControlGroupAddr(mcPrefix, mcMiddleBytes, 0xFFFFFC)` and calls `WriteTo` on all egress interfaces. No sequence stamping, caching, or frame decoding is performed.

---

## Listener Integration

### Configuration Flags

| Flag / Env var                                          | Default | Description                                                  |
| ------------------------------------------------------- | ------- | ------------------------------------------------------------ |
| `-subtree-groups` / `SUBTREE_GROUPS`                    | `""`    | Comma-separated 32-char hex GroupIDs to subscribe            |
| `-subtree-group-default-ttl` / `SUBTREE_GROUP_DEFAULT_TTL` | `900s` | Fallback TTL applied when announcement TTL = 0             |
| `-announce-scope` / `ANNOUNCE_SCOPE`                    | `site`  | Comma-separated scope(s) for announcement group joins        |
| `-sender-include` / `SENDER_INCLUDE`                    | `""`    | IPv6 CIDRs of trusted announcement senders (empty = all)     |
| `-sender-exclude` / `SENDER_EXCLUDE`                    | `""`    | IPv6 CIDRs to reject (checked before include)                |

### SubtreeAnnounceListener

`discovery.SubtreeAnnounceListener` joins the `CtrlGroupSubtreeAnnounce` group on each configured scope. It uses a raw syscall socket with `SO_REUSEPORT` so it can coexist with data workers bound to the same listen port (both use `SO_REUSEPORT`; Linux requires all sockets sharing a port this way to have the option set).

- Eviction loop: 1-second tick calls `Registry.Evict()`, removing entries whose TTL has elapsed.
- Source filtering: `SenderExclude` is checked first; `SenderInclude` is then applied (empty = accept all remaining).

### subtreegroup.Registry

Thread-safe map: `groupID → map[subtreeID]expiry`. Constructed with the set of GroupIDs from `-subtree-groups`; announcements for other GroupIDs are silently dropped. Memory is bounded by the number of subscribed GroupIDs × active SubtreeIDs.

Hot-path method: `Contains(subtreeID [32]byte) bool` — holds a read lock, scans subscribed groups. Returns `true` if any live (non-expired) entry matches.

### filter.Allow Integration

When a `subtreegroup.Registry` is wired into `filter.New(...)`, `filter.Allow` accepts a frame if:

1. Its `SubtreeID` is in the static `-subtree-include` list, **OR**
2. `groupReg.Contains(SubtreeID)` returns `true` (dynamic group membership).

Both criteria are independent and additive. A frame is denied with reason `"subtree_include_miss"` only if both fail.

Setting `-subtree-groups` without `-subtree-include` creates a pure dynamic filter: frames are accepted only for SubtreeIDs with live announcements.

---

## TTL and Refresh

Announcements must be re-sent before their TTL expires. Recommended practice:

- **Announcement interval:** 10–30 seconds (`subtx-gen` default: `10s`).
- **TTL:** at least 3× the interval to absorb packet loss (e.g. `60` for a 20 s interval).
- **Default TTL:** when `TTL = 0`, the listener applies its configured `DefaultSubtreeGroupTTL` (default: `900s`).

If announcements cease, entries expire naturally. Frames with the affected SubtreeIDs are then dropped with `bsl_frames_dropped_total{reason="subtree_include_miss"}`.

---

## Producer: bitcoin-subtx-generator

The `subtx-gen` tool includes BRC-127 announcement support:

| Flag                 | Default | Description                                             |
| -------------------- | ------- | ------------------------------------------------------- |
| `-announce-addr`     | `""`    | Proxy TCP address (e.g. `[::1]:9002`); empty = disabled |
| `-subtree-group`     | `""`    | Comma-separated 32-char hex GroupIDs for all subtrees   |
| `-announce-interval` | `10s`   | Re-announce period                                      |
| `-announce-ttl`      | `0`     | TTL in seconds; `0` = use listener default              |

When both `-announce-addr` and `-subtree-group` are set, `subtx-gen` maintains a TCP connection to the proxy and sends one `SubtreeAnnounce` datagram per `(SubtreeID, GroupID)` pair at the configured interval.

---

## Implementation

| Component                                      | File                                                              |
| ---------------------------------------------- | ----------------------------------------------------------------- |
| Wire format encode/decode                      | `bitcoin-shard-common/frame/subtree_announce.go`                  |
| `MsgTypeSubtreeAnnounce = 0x30`, `SubtreeAnnounceSize = 64` | `bitcoin-shard-common/frame/frame.go`              |
| `CtrlGroupSubtreeAnnounce = 0xFFFFFC`          | `bitcoin-shard-common/shard/control.go`                           |
| Proxy TCP detection + `ForwardControl`         | `bitcoin-shard-proxy/worker/tcp.go`, `forwarder/forwarder.go`     |
| `SubtreeAnnounceListener`                      | `bitcoin-shard-listener/discovery/subtree_announce.go`            |
| `subtreegroup.Registry`                        | `bitcoin-shard-listener/subtreegroup/registry.go`                 |
| Filter integration (`groupReg`)                | `bitcoin-shard-listener/filter/filter.go`                         |
| Listener config flags                          | `bitcoin-shard-listener/config/config.go`                         |
| Producer sender                                | `bitcoin-subtx-generator/internal/announce/sender.go`             |
