# Bitcoin Multicast

## Overview

The Bitcoin Multicast project is a high-throughput, horizontally-scalable transaction distribution system for Bitcoin SV (BSV) designed to pave the road towards 1 billion+ transactions per second. It uses IPv6 multicast to efficiently distribute transaction data across a fabric of subscribers (miners, exchanges, service providers) with deterministic sharding and NACK-based reliability.

This document provides a comprehensive design overview of the entire multicast ecosystem, encompassing all repositories and their interactions.

**Conceptual Attribution:** The IPv6 multicast transaction broadcast architecture from which this software draws inspiration was articulated by Dr. Craig S. Wright in [Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast).

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Repository Overview](#repository-overview)
3. [Network Topology](#network-topology)
4. [Data Flow](#data-flow)
5. [Sharding Mechanism](#sharding-mechanism)
6. [Frame Format](#frame-format)
7. [Component Deep Dives](#component-deep-dives)
8. [Retransmission and Reliability](#retransmission-and-reliability)
9. [Subtree Filtering](#subtree-filtering)
10. [Group Announcement Protocol (BRC-127)](#group-announcement-protocol-brc-127)
11. [Testing and Validation](#testing-and-validation)
12. [Deployment Considerations](#deployment-considerations)
13. [Endpoint Discovery (BRC-126)](#endpoint-discovery-brc-126)
14. [BRC-128: Extended Format Frames](#brc-128-extended-format-frames)
15. [NACK Retransmission Flow](#nack-retransmission-flow)

---

## High-Level Architecture

The multicast pipeline consists of three tiers:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                        BSV Senders (Miners, Services)                       │
│                              (UDP/TCP Ingress)                              │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Ingress Tier (bitcoin-ingress)                       │
│                    Deploys: bitcoin-shard-proxy nodes                       │
│                  Stateless, deterministic, horizontally scalable            │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │  IPv6 UDP Multicast (FF05::<shard>)
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Multicast Fabric (Site-Scoped)                        │
│                    FF05::/16, UDP Port 9001                                 │
│         ┌──────────────┬──────────────┬──────────────┐                      │
│         │ Direct Subs  │   Listeners  │  Retry Nodes │                      │
│         │ (Miners,     │  (Filtered   │  (Cache &    │                      │
│         │  Exchanges)  │   Forward)   │   Retransmit)│                      │
│         └──────────────┴──────────────┴──────────────┘                      │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │ Downstream   │ │ Downstream   │ │ Downstream   │
        │ Consumers    │ │ Consumers    │ │ Consumers    │
        └──────────────┘ └──────────────┘ └──────────────┘
```

**Key Design Principles:**

- **Stateless Ingress:** Proxy nodes carry no state; any number can be deployed without coordination
- **Deterministic Sharding:** Same transaction ID always maps to the same multicast group
- **Consistent Hashing:** Increasing shard bits splits groups without invalidating existing subscriptions
- **Horizontal Scale:** Add nodes to scale capacity; no reconfiguration of existing nodes required
- **NACK-based Recovery:** Listeners detect gaps and request retransmission from cached retry endpoints

---

## Repository Overview

The project is organized into multiple repositories, each with a specific responsibility:

### Core Services (Binaries)

| Repository                                                                      | Purpose                                                                              |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| [bitcoin-shard-proxy](https://github.com/lightwebinc/bitcoin-shard-proxy)       | Stateless ingress proxy; receives frames, derives multicast group, forwards verbatim |
| [bitcoin-shard-listener](https://github.com/lightwebinc/bitcoin-shard-listener) | Multicast subscriber; filters by shard/subtree, forwards to unicast consumers        |
| [bitcoin-retry-endpoint](https://github.com/lightwebinc/bitcoin-retry-endpoint) | Caches frames, retransmits on NACK requests                                          |

### Shared Libraries

| Repository                                                                  | Purpose                                    | Packages                                |
| --------------------------------------------------------------------------- | ------------------------------------------ | --------------------------------------- |
| [bitcoin-shard-common](https://github.com/lightwebinc/bitcoin-shard-common) | Protocol primitives shared across services | `frame`, `shard`, `seqhash`, `sequence` |

### Infrastructure Automation

| Repository                                                                      | Purpose                                         | Deploys                |
| ------------------------------------------------------------------------------- | ----------------------------------------------- | ---------------------- |
| [bitcoin-ingress](https://github.com/lightwebinc/bitcoin-ingress)               | Ansible/Terraform for ingress proxy deployment  | bitcoin-shard-proxy    |
| [bitcoin-listener](https://github.com/lightwebinc/bitcoin-listener)             | Ansible/Terraform for listener deployment       | bitcoin-shard-listener |
| [bitcoin-retransmission](https://github.com/lightwebinc/bitcoin-retransmission) | Ansible/Terraform for retry endpoint deployment | bitcoin-retry-endpoint |

### Testing and Tools

| Repository                                                                        | Purpose                                       |
| --------------------------------------------------------------------------------- | --------------------------------------------- |
| [bitcoin-subtx-generator](https://github.com/lightwebinc/bitcoin-subtx-generator) | Traffic generator for load/functional testing |

### Meta Repository

| Repository                                                            | Purpose                                                    |
| --------------------------------------------------------------------- | ---------------------------------------------------------- |
| [bitcoin-multicast](https://github.com/lightwebinc/bitcoin-multicast) | This repository; project overview and design documentation |

---

## Network Topology

### Full Production Topology

```text
                    ┌─────────────────────────────────────────────────────────┐
                    │                    BSV Senders                          │
                    │            (Miners, Transaction Services)               │
                    └───────────────────────────┬─────────────────────────────┘
                                                │  UDP/TCP (BRC-12/BRC-124 frames)
                    ┌───────────────────────────┼─────────────────────────────┐
                    │                           │                             │
              ┌─────▼─────┐               ┌─────▼─────┐                 ┌─────▼─────┐
              │ Ingress   │               │ Ingress   │                 │ Ingress   │
              │ Node A    │               │ Node B    │                 │ Node C    │
              │ (proxy)   │               │ (proxy)   │                 │ (proxy)   │
              └─────┬─────┘               └─────┬─────┘                 └─────┬─────┘
                    │  IPv6 UDP Multicast (FF05::<shard>, port 9001)          │
                    └───────────────────────────┼─────────────────────────────┘
                                                │
                                ┌───────────────┴───────────────┐
                                │   Multicast Fabric Router     │
                                │   (MLD/PIM, FF05::/16)        │
                                └───────────────┬───────────────┘
                                                │
        ┌───────────────────────┬───────────────┼───────────────┬─────────────────┐
        │                       │               │               │                 │
  ┌───────────┐         ┌───────────┐   ┌───────────┐   ┌───────────┐       ┌───────────┐
  │  Miner 1  │         │  Miner 2  │   │ Listener  │   │ Listener  │       │  Retry    │
  │  (direct) │         │  (direct) │   │  Node A   │   │  Node B   │       │ Endpoint  │
  └───────────┘         └───────────┘   └─────┬─────┘   └─────┬─────┘       └─────┬─────┘
                                              │               │                   │
                                              │ Unicast       │ Multicast         │ NACK (UDP)
                                              │ UDP/TCP       │ UDP               │ port 9300
                                              ▼               ▼                   │
                                    ┌──────────────┐ ┌──────────────┐             │
                                    │ Consumer A   │ │ Consumer B   │             │
                                    └──────────────┘ └──────────────┘             │
                                                                                  │
                                                   NACK Retransmission ◄──────────┘
                                              (re-multicast to FF05::<shard>)
```

---

## Data Flow

### Normal Flow (No Retransmission)

```text
1. BSV Sender → bitcoin-shard-proxy
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ UDP/TCP: BRC-12/BRC-124 frame (TxID, payload, Sequence, Subtree)        │
   └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
2. bitcoin-shard-proxy
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ • Decode frame (extract TxID)                                           │
   │ • Stamp PrevSeq/CurSeq in-place (BRC-124 only, bytes 40–55)             │
   │ • Derive multicast group: FF05::<groupIndex> from TxID top bits         │
   │ • Forward verbatim to all egress interfaces                             │
   └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
3. Multicast Fabric
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ FF05::<groupIndex>:9001 delivered to all joined subscribers             │
   │ (MLD snooping / PIM distribution tree)                                  │
   └─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
4a. Direct Subscriber              4b. bitcoin-shard-listener
   ┌─────────────────────────────┐         ┌──────────────────────────────────────────┐
   │ Miner / Exchange            │         │ • Join configured shard groups via MLD   │
   │ (consumes directly)         │         │ • Apply shard filter (defense-in-depth)  │
   └─────────────────────────────┘         │ • Apply subtree filter (include/exclude) │
                                           │ • Track sequence gaps per group          │
                                           │   (PrevSeq/CurSeq hash-chain breaks)     │
                                           │ • Forward matching frames to egress_addr │
                                           │   (UDP or TCP, optional strip-header)    │
                                           └──────────────────────────────────────────┘
                                                            │
                                                            ▼
                                                5. Downstream Consumer
```

### Retransmission Flow (NACK-based)

```text
bitcoin-shard-listener detects gap:
┌─────────────────────────────────────────────────────────────────────────┐
│ • PrevSeq ≠ lastCurSeq (hash-chain break detected)                      │
│ • Register missing frame in pending map (key = incoming PrevSeq)        │
│ • Background sweeper dispatches NACK after nack-gap-ttl                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
NACK Dispatch (UDP to retry-endpoint:9300)
┌─────────────────────────────────────────────────────────────────────────┐
│ 24-byte NACK datagram: (Magic, LookupType, LookupSeq)                   │
│ Endpoints tried by (Tier ASC, Preference DESC); MISS triggers escalation│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
bitcoin-retry-endpoint
┌─────────────────────────────────────────────────────────────────────────┐
│ • Receive NACK on port 9300                                             │
│ • Rate limit (IP, LookupSeq)                                            │
│ • Lookup frame in cache by LookupType + LookupSeq (dual-index)          │
│ • If found: re-multicast to FF05::<shard>:9001 (if -retransmit-multicast│
│   enabled); unicast to NACK source (if -retransmit-unicast); send ACK   │
│ • If not found: send 16-byte MISS (listener escalates to next endpoint) │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
bitcoin-shard-listener receives repair
┌─────────────────────────────────────────────────────────────────────────┐
│ • Frame arrives via normal multicast path                               │
│ • Gap tracker fills pending entry → bsl_gaps_suppressed_total           │
│ • Frame forwarded to downstream consumer                                │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Sharding Mechanism

### Deterministic Group Derivation

The multicast group for a transaction is derived purely from its transaction ID:

```text
groupIndex = (txid[0:4] as uint32 BE) >> (32 - shardBits)
IPv6 group = [FFsc::groupIndex]
```

**Example with shard_bits=2:**

| txid[0:4] (hex) | txid[0:4] (uint32) | >> 30 | groupIndex | Multicast Address |
| --------------- | ------------------ | ----- | ---------- | ----------------- |
| 0x12345678      | 305419896          | 0     | 0          | FF05::0           |
| 0x87654321      | 2271560481         | 3     | 3          | FF05::3           |
| 0xABCD1234      | 2882343444         | 2     | 2          | FF05::2           |
| 0x4567ABCD      | 1164413357         | 1     | 1          | FF05::1           |

### Consistent Hashing Property

Using top bits (right shift) instead of modulo provides consistent hashing:

```text
shard_bits = 2  →  4 groups (0, 1, 2, 3)
shard_bits = 3  →  8 groups (0a, 0b, 1a, 1b, 2a, 2b, 3a, 3b)

Group 0 splits into:  0a (txid[0] bit 31 = 0), 0b (txid[0] bit 31 = 1)
Group 1 splits into:  1a (txid[0] bit 31 = 0), 1b (txid[0] bit 31 = 1)
...
```

**Benefit:** When increasing shard_bits, subscribers only need to join additional groups. Existing subscriptions remain valid.

### IPv6 Multicast Address Layout

```text
Bits [127:112]   FFsc   Multicast prefix + scope (e.g., FF05 for site-local)
Bits [111:24]    0x00   Zero padding (assigned address space)
Bits [23:0]      index  Group index (up to 24 bits = 16,777,216 groups)
```

**Scope Codes:**

| Scope        | Code | Example | Use Case                |
| ------------ | ---- | ------- | ----------------------- |
| link-local   | 1    | FF01::  | Single network segment  |
| site-local   | 5    | FF05::  | Entire site (default)   |
| organization | 8    | FF08::  | Multi-site organization |
| global       | E    | FF0E::  | Internet-wide           |

**Control-Plane Reserved Indices (BRC-TBD-addressing):**

| Index    | Purpose                    | Scope | Compressed Address |
| -------- | -------------------------- | ----- | ------------------ |
| 0xFFFFFC | Subtree announce (BRC-127) | FF05  | FF05::FF:FFFC      |
| 0xFFFFFD | Beacon (site)              | FF05  | FF05::FF:FFFD      |
| 0xFFFFFD | Beacon (global)            | FF0E  | FF0E::FF:FFFD      |
| 0xFFFFFE | Control channel            | FF0E  | FF0E::FF:FFFE      |
| 0xFFFFFF | _(reserved)_               | —     | do not use         |

See [BRC-TBD-addressing Multicast Group Address Assignments](docs/brc-tbd-multicast-addressing.md) for full details.

## Frame Format

The BRC-124 data-plane frame format (92-byte header, replacing the legacy 44-byte BRC-12 header) is defined in a dedicated design document:

**→ [BRC-124 Frame Format](docs/brc-124-frame-format.md)**

Key fields: Network magic, Protocol version, Frame version, Transaction ID, PrevSeq (XXH64), CurSeq (XXH64), Subtree ID, Payload length, and BSV tx payload. Both v1 (legacy) and BRC-124 frames are accepted by all components.

**BRC-128 (Extended Format):** BRC-128 frames carry BRC-30 Extended Format (EF) transaction payloads inside the standard 92-byte BRC-124 header. Frame Version remains `0x02`; the payload is self-identifying via the 6-byte EF marker at payload bytes 4–9 (`0x000000000000EF`). All infrastructure components are payload-agnostic — no changes required to proxy, listener, or retry endpoint.

**→ [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md)**

---

## Component Deep Dives

### bitcoin-shard-proxy (Ingress)

**Purpose:** Stateless ingress proxy; receives BSV transaction frames, derives multicast group, forwards verbatim.

**Key Characteristics:**

- Zero-copy forwarding: frame never modified after PrevSeq/CurSeq stamp
- Multi-CPU design: N UDP workers via SO_REUSEPORT + 1 TCP listener
- Deterministic: same txid always maps to same group
- Stateless: no coordination between workers or nodes required

**Architecture:**

```text
UDP Workers (N goroutines, SO_REUSEPORT)
  ┌─────────┐
  │ Worker 0│──┐
  └─────────┘  │
  ┌─────────┐  ├──▶ shared Forwarder ──▶ egress sockets (per iface)
  │ Worker 1│──│
  └─────────┘  │
  ...          │
  ┌─────────┐  │
  │ Worker N│──┘
  └─────────┘

TCP Listener (1 goroutine)
  ┌──────────────┐
  │ Accept loop  │──▶ per-connection goroutines ──▶ shared Forwarder
  └──────────────┘
```

**Hot Path (UDP and TCP data frames):**

1. `frame.Decode(raw)` → extract TxID
2. If CurSeq (`raw[48:56]`) is non-zero: sender pre-stamped; forward verbatim. Else stamp `raw[40:48]` (PrevSeq) and `raw[48:56]` (CurSeq) = XXH64 hash chain per `(senderIPv6, groupIdx)`
3. `shard.Engine.GroupIndex(txid)` → derive group
4. `WriteTo(raw)` → write to all egress interfaces

**TCP Control Frame Path (BRC-127):**

- Detect `MsgTypeSubtreeAnnounce` (0x30) at `buf[6]` in the TCP stream
- Read 64-byte fixed datagram; call `ForwardControl(targets, buf, CtrlGroupSubtreeAnnounce, egressPort)`
- Multicasts verbatim to `FF05::FF:FFFC`; no sequence stamping or frame decoding

**→ [bitcoin-shard-proxy docs](https://github.com/lightwebinc/bitcoin-shard-proxy/blob/main/docs/architecture.md)** — architecture, configuration reference, metrics

---

### bitcoin-shard-listener (Subscriber)

**Purpose:** Multicast subscriber; filters by shard/subtree, forwards to unicast and/or multicast consumers, performs NACK-based gap recovery.

**Key Characteristics:**

- SO_REUSEPORT multi-worker receive (kernel-level source affinity)
- Dual-level filtering: MLD group join + userspace shard/subtree filter
- NORM-inspired gap tracking per group via PrevSeq/CurSeq hash chain
- NACK dispatch to configurable retry endpoints
- Egress via UDP or TCP (optional strip-header mode)
- Multicast egress for domain bridging (re-emit filtered frames to a separate multicast address space)

**Architecture:**

```text
Receive Workers (NUM_WORKERS goroutines, SO_REUSEPORT)
  ┌─────────┐
  │ Worker 0│──┐
  └─────────┘  │
  ┌─────────┐  ├──▶ per-worker components:
  │ Worker 1│──│     • frame.Decode
  └─────────┘  │     • shard.Engine.GroupIndex
  ...          │     • filter.Allow (shard + subtree + groupReg)
  ┌─────────┐  │     • egress.Send (unicast)
  │ Worker N│──│     • mcastEgr.Send (multicast, optional)
  └─────────┘  │     • nack.Tracker.Observe
               │

SubtreeAnnounceListener (1 goroutine, BRC-127)
  ┌──────────────────────────────────────────┐
  │ Join FF05::FF:FFFC (SO_REUSEPORT socket) │──▶ subtreegroup.Registry
  │ Evict loop (1 s tick)                    │       ▲
  └──────────────────────────────────────────┘       │
                                                filter.Allow (groupReg)

NACK Queue (background goroutines)
  ┌──────────────────┐
  │ NACK dispatcher  │──▶ UDP send to retry-endpoint:9300
  └──────────────────┘

Gap Tracker Sweeper (100ms interval)
  ┌──────────────────┐
  │ Evict expired    │
  │ Dispatch pending │
  └──────────────────┘
```

**Important:** Linux delivers multicast to ALL SO_REUSEPORT sockets (no load balancing). For multicast deployments, `NUM_WORKERS` must be set to 1. Multiple workers are only useful for unicast ingress (E2E test suite).

**Filter Behavior:**

| Config                      | Behavior                               |
| --------------------------- | -------------------------------------- |
| `shard-include` empty       | All shard indices accepted             |
| `shard-include` non-empty   | Only listed indices accepted           |
| `subtree-include` empty     | All SubtreeIDs accepted                |
| `subtree-include` non-empty | Only listed IDs accepted               |
| `subtree-exclude`           | Listed IDs dropped (overrides include)                                    |
| `-subtree-groups` non-empty | SubtreeIDs in any live announced GroupID accepted (OR with static include) |

**Dynamic Subtree Group Filtering (BRC-127):**

When `-subtree-groups` is configured, the listener instantiates a `subtreegroup.Registry` and wires it into the filter as `groupReg`. On each frame, `filter.Allow` calls `groupReg.Contains(SubtreeID)` as an additional acceptance path alongside the static `-subtree-include` list. Entries expire when not refreshed before their TTL (default: 900 s); configure with `-subtree-group-default-ttl`.

Source filtering for announcements: `-sender-include` / `-sender-exclude` restrict which IPv6 sources are accepted. Both support CIDR notation.

**Gap Tracking:**

- State: per-group `lastCurSeq` and `pending` map
- When `PrevSeq ≠ lastCurSeq`: register gap entry keyed on incoming `PrevSeq`
- When incoming `CurSeq` matches a pending key: auto-close gap (multicast fill)
- `Tracker.Fill(groupIdx, curSeq)` closes gap from explicit NACK ACK
- Sweeper evicts expired gaps as `bsl_gaps_unrecovered_total`
- NACK dispatch with exponential backoff (capped at `nack-backoff-max`)

**→ [bitcoin-shard-listener docs](https://github.com/lightwebinc/bitcoin-shard-listener/blob/main/docs/architecture.md)** — architecture, configuration reference, metrics

---

### bitcoin-retry-endpoint (Retransmission)

**Purpose:** Caches frames, retransmits on NACK requests from listeners.

**Key Characteristics:**

- Single-worker multicast receiver (SO_REUSEPORT limitation)
- In-memory cache (freecache, 60 s TTL, GC-free, dual-index by CurSeq and PrevSeq)
- Multi-tier rate limiting: per-IP, per-chain (ChainID), per-sequence (LookupSeq) pre-lookup; per-group (groupIdx) post-lookup (ACK still sent on throttle)
- Sharding-based multicast egress for retransmitted frames

**Architecture:**

```text
Multicast Receiver (1 worker, SO_REUSEPORT)
  ┌──────────────────┐
  │ Join all groups  │──▶ Cache (freecache, 60 s TTL)
  └──────────────────┘

NACK Server (NACK_WORKERS goroutines)
  ┌──────────────────┐
  │ UDP listener     │──▶ Rate limit ──▶ Cache lookup ──▶ Retransmit
  └──────────────────┘

Retransmit Egress
  ┌──────────────────┐
  │ Multicast send   │──▶ FF05::<shard>:9001
  └──────────────────┘
```

**Cache:** freecache (60 s TTL, GC-free). Dual-index: primary key `0x01‖CurSeq → raw frame`, secondary key `0x00‖PrevSeq → CurSeq`. Supports both forward (by PrevSeq) and backward (by CurSeq) NACK lookups.

**→ [bitcoin-retry-endpoint Architecture](https://github.com/lightwebinc/bitcoin-retry-endpoint/blob/main/docs/architecture.md)** — architecture, configuration reference, metrics

---

### bitcoin-shard-common (Protocol Primitives)

**Purpose:** Shared protocol primitives imported by proxy, listener, and retry endpoint.

**Packages:** `frame` (v1/BRC-124 encode/decode), `shard` (txid → multicast group derivation), `seqhash` (XXH64 hash chain for PrevSeq/CurSeq), `sequence` (per-shard monotonic counters).

**→ [bitcoin-shard-common README](https://github.com/lightwebinc/bitcoin-shard-common)** — package API, [protocol spec](https://github.com/lightwebinc/bitcoin-shard-common/blob/main/docs/protocol.md)

---

## Retransmission and Reliability

### NACK Protocol (BRC-126)

Listeners detect sequence gaps and send 24-byte NACK datagrams to retry endpoints. The full wire format, response protocol, and escalation state machine are defined in:

**→ [BRC-126 (Retransmission Protocol)](docs/brc-126-retransmission-protocol.md)**

**Key changes from the original fire-and-forget NACK model:**

- **ACK/MISS responses** — every NACK receives a unicast response (16 bytes). ACK confirms retransmit dispatched; MISS indicates cache miss and triggers immediate escalation to the next endpoint.
- **Beacon discovery** — retry endpoints periodically multicast ADVERT messages (56 bytes) to site/global beacon groups. Listeners maintain a dynamic endpoint registry, sorted by `(Tier ASC, Preference DESC)`.
- **Tier-based escalation** — on MISS, listeners advance through endpoints at the same tier, then escalate to the next tier. No backoff on MISS; immediate retry.
- **Configurable retransmit modes** — endpoints can retransmit via multicast, unicast, or both. Responses can be selectively suppressed.

### NACK Dispatch Flow

```text
1. Gap detected (seq > highestConsec + 1)
   → Register in pending map with jitter hold-off

2. Background sweeper (100ms interval)
   → If past nextAttempt and retries < nack-max-retries:
     → Select endpoint from registry snapshot (Tier ASC, Preference DESC)
     → Open ephemeral UDP socket; send 24-byte NACK; wait ≤300ms
     → ACK received: cancel gap entry
     → MISS received: advance endpoint; retry immediately (no backoff)
     → Timeout: exponential backoff; retry next sweep

3. If retries exhausted or GapTTL exceeded
   → Evict as bsl_gaps_unrecovered_total

4. Multicast repair arrives independently
   → Tracker.Fill() cancels pending gap regardless of NACK state
```

### Endpoint Discovery

Retry endpoints advertise via periodic ADVERT beacons (see [BRC-126](docs/brc-126-retransmission-protocol.md)). Listeners join the site beacon group (`FF05::FF:FFFD`) and optionally the global beacon group (`FF0E::FF:FFFD`) to discover endpoints dynamically. Static `-retry-endpoints` seeds the registry at lowest priority (`Tier=0xFF, Preference=0`).

Group address assignments for beacons and the control channel are defined in:

**→ [BRC-TBD-addressing Multicast Group Address Assignments](docs/brc-tbd-multicast-addressing.md)**

### Retry Endpoint Processing

```text
1. Receive NACK on port 9300
2. Rate limit tier 1 (IP): per-source-IP token bucket
   → If exceeded: silent drop, increment bre_rate_limit_drops_total{level="ip"}
3. Rate limit tier 2 (chain): per-(srcIP,ChainID) sliding window; ChainID=0 bypasses
   → If exceeded: silent drop, increment bre_rate_limit_drops_total{level="chain"}
4. Rate limit tier 3 (sequence): per-LookupSeq sliding window
   → If exceeded: silent drop, increment bre_rate_limit_drops_total{level="sequence"}
5. Cache lookup by LookupType + LookupSeq (dual-index)
   → If not found: send 16-byte MISS; increment bre_cache_misses_total
   → If found:
     6. Rate limit tier 4 (group): per-(srcIP,groupIdx) token bucket, post-lookup
        → If exceeded: skip retransmit; increment bre_rate_limit_drops_total{level="group"}
        → Send 16-byte ACK regardless (listener must not escalate)
     7. Re-multicast to FF05::<shard>:9001 (if -retransmit-multicast, not throttled)
     8. Unicast to NACK source (if -retransmit-unicast, not throttled)
     9. Send 16-byte ACK unicast to NACK source (unless -suppress-ack)
```

### Reliability Characteristics

**Best-effort delivery with deterministic escalation:**

- Multicast delivery is inherently unreliable
- NACK + ACK/MISS provides deterministic gap recovery signalling
- MISS triggers immediate escalation (no wasted backoff time)
- Multicast repair path and NACK path are independent; either can fill a gap
- No guarantee of recovery (network partition, cache expiration, all endpoints MISS)

**Cache TTL considerations:**

- Default cache TTL: 60 seconds
- Trade-off: Longer TTL = higher recovery probability, but more memory
- Adjust based on expected gap detection latency and network conditions

**Flood prevention:**

- Cache TTL (60 s) bounds the retransmit window; expired frames drop naturally
- `Tracker.Fill()` suppresses pending NACKs on multicast repair arrival
- Jitter hold-off and exponential backoff reduce NACK storm risk
- All drops are counted in metrics

---

## Subtree Filtering

### Subtree Model

A _subtree_ is an ordered set of related transactions sharing a common batch context. The 32-byte `SubtreeID` field allows downstream subscribers to associate frames with a named batch. In Teranode, this is currently used to batch transactions for processing and to link ordered sets of validated transactions from block templates. This may be extended to support transaction specialization, and some sort of dynamic announcement and hashing mechanism may be required later.

**Use Cases:**

- Shard by transaction type (payments, contracts, tokens)
- Shard by application or service
- Shard by geographic region
- Shard by time window

### Subtree Filter Behavior

**Include Mode:**

```
subtree-include = "abc123...,def456..."  (hex, 32-byte each)
→ Only frames with SubtreeID in this set pass
```

**Exclude Mode:**

```
subtree-exclude = "abc123...,def456..."  (hex, 32-byte each)
→ Frames with SubtreeID in this set are dropped (overrides include)
```

**Empty Sets:**

- `subtree-include` empty: all SubtreeIDs accepted
- `subtree-exclude` empty: no exclusion
- Both empty: no subtree filtering (all frames pass)

**V1 Frames:**

- V1 frames have zero SubtreeID
- Only pass subtree filter if zero is explicitly listed in `subtree-include`

### Deterministic Subtree Selection (Testing)

The `bitcoin-subtx-generator` tool uses deterministic subtree selection for reproducible tests:

```
SubtreeID = pool[uint64(TxID[:8]) % N]
```

With N=8 subtrees and a fixed seed, the same txid always maps to the same subtree. This allows listeners filtering on a single subtree to see a predictable traffic fraction (~1/N).

---

## Group Announcement Protocol (BRC-127)

BRC-127 defines the dynamic subtree group announcement protocol. Producers advertise which SubtreeIDs belong to which logical group; listeners subscribe to named groups and automatically accept frames from those subtrees without static configuration.

**→ [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md)**

### Wire Format

A 64-byte `SubtreeAnnounce` datagram (`MsgType 0x30`) maps one SubtreeID to one GroupID with a TTL:

```text
Offset  Size  Field
------  ----  -----
     0     4  Magic (0xE3E1F3E8)
     4     2  ProtoVer (0x02BF)
     6     1  MsgType = 0x30
     7     1  Flags (reserved)
     8    32  SubtreeID  — 32-byte SHA-256 subtree root hash
    40    16  GroupID    — 128-bit logical group identifier
    56     4  Epoch      — Unix timestamp of announcement
    60     2  TTL        — Validity in seconds; 0 = listener default (900 s)
    62     2  Reserved
```

### Distribution

Producers send SubtreeAnnounce datagrams to the proxy TCP ingress. The proxy's TCP path detects `MsgType 0x30` and calls `ForwardControl`, multicasting verbatim to `FF05::FF:FFFC` (`CtrlGroupSubtreeAnnounce = 0xFFFFFC`). Listeners join this group and populate `subtreegroup.Registry`.

### Listener Configuration

| Flag / Env var                                          | Default | Description                                        |
| ------------------------------------------------------- | ------- | -------------------------------------------------- |
| `-subtree-groups` / `SUBTREE_GROUPS`                    | `""`    | Comma-separated 32-char hex GroupIDs to subscribe  |
| `-subtree-group-default-ttl` / `SUBTREE_GROUP_DEFAULT_TTL` | `900s`  | Fallback TTL when announcement TTL = 0          |
| `-announce-scope` / `ANNOUNCE_SCOPE`                    | `site`  | Scope(s) for announcement group joins              |
| `-sender-include` / `SENDER_INCLUDE`                    | `""`    | IPv6 CIDRs of trusted announcement senders         |
| `-sender-exclude` / `SENDER_EXCLUDE`                    | `""`    | IPv6 CIDRs to reject                               |

### Refresh and Expiry

Announcements must be re-sent before their TTL expires. Recommended: interval 10–30 s; TTL ≥ 3× interval. If announcements cease, entries expire and frames are dropped with `bsl_frames_dropped_total{reason="subtree_include_miss"}`.

---

## Testing and Validation

### bitcoin-subtx-generator

**Purpose:** Random BSV-shaped frame generator for load and functional testing.

**Features:** random BSV-shaped payloads, deterministic subtree ID pool, optional gap injection (`-seq-gap-every`, `-seq-gap-delay`) for NACK/retransmit tests, multi-core token-bucket pacer.

**→ [bitcoin-subtx-generator README](https://github.com/lightwebinc/bitcoin-subtx-generator)** — usage examples, flags

### bitcoin-shard-listener E2E Tests

**Purpose:** Self-contained end-to-end tests for listener functionality.

**Approach:** Inject frames as unicast UDP directly to listener's bound port (`[::1]:listen-port`), bypassing proxy and multicast fabric. This avoids Linux loopback multicast reliability issues on CI.

**Test Scenarios:**

1. Basic delivery (all frames, metric verification)
2. Shard filter (single shard acceptance)
3. Strip-header (payload-only forwarding)

**Execution:**

```bash
cd bitcoin-shard-listener
make test-e2e
```

**Documentation:** [bitcoin-shard-listener README](https://github.com/lightwebinc/bitcoin-shard-listener)

---

## Deployment Considerations

### Platform Support

| OS           | Service Manager | Network Config     | Proxy | Listener | Retry |
| ------------ | --------------- | ------------------ | ----- | -------- | ----- |
| Ubuntu 24.04 | systemd         | Netplan / ip       | ✓     | ✓        | ✓     |
| FreeBSD 14   | rc.d            | rc.conf / ifconfig | ✓     | ✓        | ✓     |
| AWS EC2      | systemd         | ENI + Terraform    | ✓     | ✓        | ✓     |

### Networking Requirements

**Ingress (bitcoin-shard-proxy):**

- IPv6 enabled on egress interface(s)
- Multicast routing / MLD snooping configured for subscriber fabric
- Optional: GRE tunnel for cloud VMs
- Optional: eBGP for nearest-node routing

**Listener (bitcoin-shard-listener):**

- IPv6 enabled on ingress interface
- MLDv1/v2 support for multicast group join
- Optional: BGP for listener reachability into fabric
- Firewall: multicast-fabric perimeter (default-on in bitcoin-listener)

**Retry Endpoint (bitcoin-retry-endpoint):**

- IPv6 enabled on multicast interface
- Optional: Redis for shared cache (multi-node deployments)

### Firewall Configuration

**Proxy (bitcoin-ingress):**

- Allow UDP/TCP ingress on listen port (default 9000)
- Allow IPv6 multicast egress on egress interface
- No additional firewall rules required

**Listener (bitcoin-listener):**

- **Multicast-fabric perimeter:** Built-in firewall enforces:
  - Ingress: Only multicast data on ingress interface
  - Egress: Only NACK datagrams outbound
  - All other traffic dropped
- See [bitcoin-listener security docs](https://github.com/lightwebinc/bitcoin-listener/blob/main/docs/security.md)

**Retry Endpoint (bitcoin-retransmission):**

- Simplified UDP-only firewall
- Allow NACK ingress on port 9300
- Allow multicast egress on egress interface

### BGP Integration

**Ingress (bitcoin-ingress):**

- Optional eBGP on ingress interface
- Announce shared prefixes from all proxy nodes
- Senders routed to nearest proxy via BGP best-path selection
- See [bitcoin-ingress BGP docs](https://github.com/lightwebinc/bitcoin-ingress/blob/main/docs/bgp.md)

**Listener (bitcoin-listener):**

- Optional BGP for listener reachability into fabric
- Advertise listener's own unicast prefix
- Enables MLD/PIM distribution trees in L3 fabrics
- See [bitcoin-listener BGP docs](https://github.com/lightwebinc/bitcoin-listener/blob/main/docs/bgp.md)

**Retry Endpoint (bitcoin-retransmission):**

- No BGP integration (pure cache-and-retransmit service)

### Scaling Guidelines

**Ingress Scaling:**

- Add more proxy nodes (stateless, no coordination)
- Increase `shard_bits` to split multicast groups
- Use eBGP for load distribution across nodes

**Listener Scaling:**

- Deploy multiple listeners with different `shard-include` configurations
- Use subtree filtering for application-level sharding
- Horizontal scale: add more listeners per shard/subtree

**Retry Endpoint Scaling:**

- Deploy multiple retry endpoints with shared Redis cache
- Cross-instance deduplication prevents duplicate retransmissions
- Rate limiting protects against NACK storms

### Monitoring and Metrics

All services expose Prometheus metrics on dedicated ports:

| Service                | Metrics Port | Prefix |
| ---------------------- | ------------ | ------ |
| bitcoin-shard-proxy    | :9100        | bsp\_  |
| bitcoin-shard-listener | :9200        | bsl\_  |
| bitcoin-retry-endpoint | :9400        | bre\_  |

Key signals: `bsp_packets_dropped_total`, `bsl_gaps_detected_total`, `bsl_gaps_unrecovered_total`, `bre_cache_misses_total`, `bre_rate_limit_drops_total`. See each component's docs for full metric reference.

### Graceful Shutdown

All services handle SIGINT/SIGTERM identically: set draining flag (`/readyz` → 503), optional drain timeout, close ingress sockets, wait for in-flight processing, flush OTLP exporter.

---

## References and Further Reading

### Source Documentation

**Protocol:**

- [Wire Protocol Specification](https://github.com/lightwebinc/bitcoin-shard-common/blob/main/docs/protocol.md) - Complete v1/BRC-124 frame format
- [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md) - SubtreeAnnounce wire format, proxy forwarding, listener integration
- [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md) - EF payload format, detection, infrastructure impact

**Services:**

- [bitcoin-shard-proxy Architecture](https://github.com/lightwebinc/bitcoin-shard-proxy/blob/main/docs/architecture.md)
- [bitcoin-shard-listener Architecture](https://github.com/lightwebinc/bitcoin-shard-listener/blob/main/docs/architecture.md)
- [bitcoin-retry-endpoint Architecture](https://github.com/lightwebinc/bitcoin-retry-endpoint/blob/main/docs/architecture.md)

**Infrastructure:**

- [bitcoin-ingress Architecture](https://github.com/lightwebinc/bitcoin-ingress/blob/main/docs/architecture.md)
- [bitcoin-listener Architecture](https://github.com/lightwebinc/bitcoin-listener/blob/main/docs/architecture.md)
- [bitcoin-retransmission Architecture](https://github.com/lightwebinc/bitcoin-retransmission/blob/main/docs/architecture.md)

### Conceptual Attribution

The IPv6 multicast transaction broadcast architecture from which this software draws inspiration was articulated by Dr. Craig S. Wright:

- [Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast)
- [Multicast as the Only Viable Architecture](https://singulargrit.substack.com/p/multicast-as-the-only-viable-architecture)
- [Singulargrit Substack](https://singulargrit.substack.com/)

### Standards

**BRC-12: Raw Transaction Format**

- The v1 wire-frame format transports transactions conforming to BRC-12
- [BSV Blockchain Standards Repository](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0012.md)

**BRC-30: Extended Format (EF) Transaction**

- The payload format for BRC-128 frames
- [BSV Blockchain Standards Repository](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0030.md)

---

## Appendix: Quick Reference

### Default Ports

| Service                             | Port         | Protocol | Purpose                                      |
| ----------------------------------- | ------------ | -------- | -------------------------------------------- |
| bitcoin-shard-proxy (UDP ingress)   | 9000         | UDP      | Frame ingress                                |
| bitcoin-shard-proxy (TCP ingress)   | configurable | TCP      | Reliable frame ingress (disabled by default) |
| bitcoin-shard-proxy (egress)        | 9001         | UDP      | Multicast egress                             |
| bitcoin-shard-listener (multicast)  | 9001         | UDP      | Multicast receive                            |
| bitcoin-shard-listener (NACK)       | 9300         | UDP      | NACK send                                    |
| bitcoin-retry-endpoint (multicast)  | 9001         | UDP      | Multicast receive                            |
| bitcoin-retry-endpoint (NACK)       | 9300         | UDP      | NACK receive                                 |
| bitcoin-retry-endpoint (retransmit) | 9001         | UDP      | Retransmission egress                        |

### Metrics Ports

| Service                | Port | Endpoint                          |
| ---------------------- | ---- | --------------------------------- |
| bitcoin-shard-proxy    | 9100 | `/metrics`, `/healthz`, `/readyz` |
| bitcoin-shard-listener | 9200 | `/metrics`, `/healthz`, `/readyz` |
| bitcoin-retry-endpoint | 9400 | `/metrics`, `/healthz`, `/readyz` |

### Default AS Numbers

| Service                 | AS    |
| ----------------------- | ----- |
| bitcoin-ingress (proxy) | 65001 |
| bitcoin-listener        | 65002 |

### Frame Version Summary

| Version | Header Size | Hash-Chain Seq       | Subtree Support |
| ------- | ----------- | -------------------- | --------------- |
| v1      | 44 bytes    | No                   | No              |
| BRC-124 | 92 bytes    | Yes (PrevSeq/CurSeq) | Yes             |
| BRC-128 | 92 bytes    | Yes (PrevSeq/CurSeq) | Yes (EF payload)|

---

## Endpoint Discovery (BRC-126)

Retry endpoint discoverability and hierarchical retransmission are defined across two BRCs:

- **[BRC-126 — Retransmission Protocol](docs/brc-126-retransmission-protocol.md):** ADVERT beacon format, NACK/ACK/MISS wire formats, Tier/Preference model, escalation state machine, configurable retransmit modes, flood prevention.
- **[BRC-TBD-addressing — Multicast Group Address Assignments](docs/brc-tbd-multicast-addressing.md):** Control-plane group index reservations, beacon group addresses, site vs global scope, block template group reservation.

### Summary

- Retry endpoints send 56-byte ADVERT beacons every 60 s (configurable) to site (`FF05::FF:FFFD`) and/or global (`FF0E::FF:FFFD`) beacon groups.
- Listeners join beacon groups at startup and maintain a dynamic `discovery.Registry` sorted by `(Tier ASC, Preference DESC)`.
- Static `-retry-endpoints` seeds the registry at `Tier=0xFF, Preference=0` (lowest priority).
- On NACK, the listener selects the highest-priority endpoint, sends a 24-byte NACK, and waits ≤300 ms for a 16-byte ACK or MISS response.
- ACK cancels the gap; MISS advances to the next endpoint immediately; timeout triggers exponential backoff.
- Inter-AS extension via MP-BGP requires no protocol changes.

---

## BRC-128: Extended Format Frames

BRC-128 carries BRC-30 Extended Format (EF) transaction payloads inside the standard 92-byte BRC-124 header. Frame Version remains `0x02`.

**→ [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md)**

### Summary

- **Header:** Identical to BRC-124 (92 bytes). Frame Version `0x02` unchanged.
- **Payload:** BRC-30 Extended Format. Self-identifying via the 6-byte marker `0x00 0x00 0x00 0x00 0x00 0xEF` at payload bytes 4–9.
- **Detection:** Inspect payload bytes 4–9; the EF marker present → BRC-30 EF (BRC-128); absent → BRC-12 raw transaction (BRC-124).
- **Infrastructure impact:** None. Proxy, listener, and retry endpoint are payload-agnostic. BRC-124 and BRC-128 frames coexist on the same multicast groups.
- **Downstream consumers:** Must inspect the payload marker to select the correct parser (BRC-12 or BRC-30).

---

## NACK Retransmission Flow

The end-to-end NACK retransmission flow — from gap detection through escalation to repair delivery — is documented with ASCII diagrams in:

**→ [NACK Retransmission Flow](docs/nack-retransmission-flow.md)**

Covers: full pipeline diagram, gap detection & dispatch, tier model, preference within a tier, escalation state machine, beacon discovery, inter-AS extension, and flood prevention.

---

_Document Version: 1.3_  
_Last Updated: 2026-05-10_
