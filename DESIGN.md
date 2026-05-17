# Bitcoin Multicast

## Overview

The Bitcoin Multicast project is a high-throughput, horizontally-scalable transaction distribution system for Bitcoin SV (BSV) designed to pave the road towards 1 billion+ transactions per second. It uses IPv6 multicast to efficiently distribute transaction data across a fabric of subscribers (miners, exchanges, service providers) with deterministic sharding and NACK-based reliability.

This document provides a comprehensive design overview of the entire multicast ecosystem, encompassing all repositories and their interactions.

**Conceptual Attribution:** The IPv6 multicast transaction broadcast architecture from which this software draws inspiration was articulated by Dr. Craig S. Wright in [Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast).

---

## Table of Contents

- [Terminology](#terminology)
- [High-Level Architecture](#high-level-architecture)
- [Repository Overview](#repository-overview)
- [Network Topology](#network-topology)
- [Data Flow](#data-flow)
- [Sharding Mechanism](#sharding-mechanism)
- [Frame Format](#frame-format)
- [Component Deep Dives](#component-deep-dives)
- [Retransmission and Reliability](#retransmission-and-reliability)
- [Subtree Filtering](#subtree-filtering)
- [Group Announcement Protocol (BRC-127)](#group-announcement-protocol-brc-127)
- [Testing and Validation](#testing-and-validation)
- [Deployment Considerations](#deployment-considerations)

---

## Terminology

| Term               | Definition                                                                                                                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Shard**          | A deterministic partition of the transaction space. Each shard maps to one IPv6 multicast group; group membership is derived from the TxID.                                                   |
| **Subtree**        | An ordered set of related transactions sharing a common 32-byte batch identifier (`SubtreeID`, or Subtree Merkle root hash). Used for transaction specialization and block template assembly. |
| **Gap**            | A detected break in the monotonic `SeqNum` counter for a flow, indicating one or more missing frames.                                                                                          |
| **Flow**           | The per-(sender IP, multicast group, subtree) sequence of frames sharing a common `HashKey`.                                                                                                    |
| **NACK**           | Negative acknowledgement — a 64-byte datagram requesting retransmission of a missing frame.                                                                                                   |
| **ACK**            | Positive acknowledgement — a 16-byte response confirming a retransmit was dispatched.                                                                                                         |
| **MISS**           | Cache-miss response — a 16-byte response indicating the requested frame is not cached; triggers immediate escalation.                                                                         |
| **ADVERT**         | A 56-byte beacon datagram advertising a retry endpoint's address, tier, and preference.                                                                                                       |
| **Fabric**         | The IPv6 multicast network interconnecting proxies, listeners, and retry endpoints.                                                                                                           |
| **Ingress**        | The initial stage where transactions are received and processed before being distributed to the multicast network.                                                                            |
| **Egress**         | The final stage where transactions are delivered to the final destination after being processed through the multicast network.                                                                |
| **Proxy**          | A node that receives transactions from senders and forwards them to the multicast network.                                                                                                    |
| **Listener**       | A node that receives transactions from the multicast network and forwards them to the egress stage.                                                                                           |
| **Retry Endpoint** | A node that caches frames, receives NACKs, and retransmits missing frames to the multicast network.                                                                                           |
| **Frame**          | A single transaction or subtree announcement packaged for multicast transmission.                                                                                                             |
| **TxID**           | The unique identifier for a transaction, used to determine shard membership and frame ordering.                                                                                               |

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
│         │ Direct Subs  │ Listeners    │ Retry Nodes  │                      │
│         │ (Miners,     │ (Filtered    │ (Cache &     │                      │
│         │  Exchanges)  │  Forward)    │  Retransmit) │                      │
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

| Repository                                                                      | Purpose                                                                                     |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| [bitcoin-shard-proxy](https://github.com/lightwebinc/bitcoin-shard-proxy)       | Stateless ingress proxy; receives frames, derives multicast group, forwards verbatim        |
| [bitcoin-shard-listener](https://github.com/lightwebinc/bitcoin-shard-listener) | Multicast subscriber; filters by shard/subtree, forwards to unicast and multicast consumers |
| [bitcoin-retry-endpoint](https://github.com/lightwebinc/bitcoin-retry-endpoint) | Caches frames, retransmits on NACK requests                                                 |

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

| Repository                                                                        | Purpose                                                     |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| [bitcoin-subtx-generator](https://github.com/lightwebinc/bitcoin-subtx-generator) | Traffic generator for load/functional testing               |
| [bitcoin-multicast-test](https://github.com/lightwebinc/bitcoin-multicast-test)   | Integration test harness; scenario suite, lab setup, deploy |

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
   │ • Stamp HashKey/SeqNum in-place (BRC-124 only, bytes 40–55)             │
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
                                           │ • Track sequence gaps per flow           │
                                           │   (HashKey/SeqNum monotonic counter)      │
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
│ • SeqNum > lastSeqNum + 1 (gap detected for this HashKey)               │
│ • Register missing frame(s) in pending map (key = HashKey + SeqNum)    │
│ • Background sweeper dispatches NACK after nack-gap-ttl                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
NACK Dispatch (UDP to retry-endpoint:9300)
┌─────────────────────────────────────────────────────────────────────────┐
│ 64-byte BRC-126 NACK datagram (see BRC-126 wire format)                  │
│ Endpoints tried by (Tier ASC, Preference DESC); MISS triggers escalation│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
bitcoin-retry-endpoint
┌─────────────────────────────────────────────────────────────────────────┐
│ • Receive NACK on port 9300                                             │
│ • Rate limit (IP, HashKey, SeqNum)                                      │
│ • Lookup frame in cache by HashKey ∥ SeqNum (single 16-byte key)        │
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

### IPv6 Multicast Address Layout (IANA-aligned)

```text
Bits [127:112]   FF0X  Multicast prefix + scope (e.g., FF05 for site-local)
Bits [111: 32]   0x00  Zero (IANA 96-bit boundary, 80 bits)
Bits [ 31: 16]   GID   IANA group-id (default 0x000B = IANA Bitcoin SV Node Groups)
Bits [ 15:  0]   IDX   Shard index (up to 16 bits; top 4 reserved for control)
```

The IANA Bitcoin SV Node Groups allocation is `FF0X::B`. Operators MAY override the
group-id via `-mc-group-id` for testing or private deployments, but the
on-wire default is `0x000B` for IANA conformance.

**Scope Codes:**

| Scope        | Code | Example | Use Case                |
| ------------ | ---- | ------- | ----------------------- |
| link-local   | 1    | FF01::  | Single network segment  |
| site-local   | 5    | FF05::  | Entire site (default)   |
| organization | 8    | FF08::  | Multi-site organization |
| global       | E    | FF0E::  | Internet-wide           |

**Control-Plane Reserved Indices (BRC-129):**

| Index  | Purpose                         | Scope | Compressed Address |
| ------ | ------------------------------- | ----- | ------------------ |
| 0xFFFB | Subtree announce (site)         | FF05  | FF05::B:FFFB       |
| 0xFFFB | Subtree announce (org)          | FF08  | FF08::B:FFFB       |
| 0xFFFB | Subtree announce (global)       | FF0E  | FF0E::B:FFFB       |
| 0xFFFC | Subtree Group announce (site)   | FF05  | FF05::B:FFFC       |
| 0xFFFC | Subtree Group announce (org)    | FF08  | FF08::B:FFFC       |
| 0xFFFC | Subtree Group announce (global) | FF0E  | FF0E::B:FFFC       |
| 0xFFFD | Beacon (site)                   | FF05  | FF05::B:FFFD       |
| 0xFFFD | Beacon (org)                    | FF08  | FF08::B:FFFD       |
| 0xFFFD | Beacon (global)                 | FF0E  | FF0E::B:FFFD       |
| 0xFFFE | Block Control channel           | FF0E  | FF0E::B:FFFE       |
| 0xFFFF | _(reserved)_                    | —     | do not use         |

See [BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md) for full details.

## Frame Format

The BRC-124 data-plane frame format (92-byte header, replacing the legacy 44-byte BRC-12 header) is defined in a dedicated design document:

**→ [BRC-124 Frame Format](docs/brc-124-frame-format.md)**

Key fields: Network magic, Protocol version, Frame version, Transaction ID, HashKey (XXH64 per-flow identifier), SeqNum (monotonic per-flow counter), Subtree ID, Payload length, and BSV tx payload. Both BRC-12 (legacy) and BRC-124/BRC-128 frames are accepted by all components.

**BRC-128 (Extended Format):** BRC-128 frames carry BRC-30 Extended Format (EF) transaction payloads inside the standard 92-byte BRC-124 header. Frame Version remains `0x02`; the payload is self-identifying via the 6-byte EF marker at payload bytes 4–9 (`0x000000000000EF`). All infrastructure components are payload-agnostic — no changes required to proxy, listener, or retry endpoint.

**→ [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md)**

---

## Component Deep Dives

### bitcoin-shard-proxy (Ingress)

**Purpose:** Stateless ingress proxy; receives BSV transaction frames, derives multicast group, forwards verbatim.

**Key Characteristics:**

- Zero-copy forwarding: frame never modified after HashKey/SeqNum stamp
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

**Hot path:** Decode frame → stamp HashKey/SeqNum in-place (BRC-124 only) → derive multicast group from TxID → `WriteTo` verbatim to all egress interfaces. TCP connections carry the same frame stream plus BRC-127 SubtreeAnnounce control datagrams (forwarded verbatim to the announce multicast group).

**→ [bitcoin-shard-proxy Architecture](https://github.com/lightwebinc/bitcoin-shard-proxy/blob/main/docs/architecture.md)** — hot-path detail, configuration reference, metrics

---

### bitcoin-shard-listener (Subscriber)

**Purpose:** Multicast subscriber; filters by shard/subtree, forwards to unicast and/or multicast consumers, performs NACK-based gap recovery.

**Key Characteristics:**

- SO_REUSEPORT multi-worker receive (kernel-level source affinity)
- Dual-level filtering: MLD group join + userspace shard/subtree filter
- NORM-inspired gap tracking per flow via HashKey/SeqNum monotonic counter
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
  │ Join FF05::B:FFFC (SO_REUSEPORT socket) │──▶ subtreegroup.Registry
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

**Important:** Linux delivers multicast to ALL SO_REUSEPORT sockets (no load balancing). For multicast deployments, `NUM_WORKERS` must be set to 1.

**Filtering:** Dual-level — shard index (MLD group join + userspace filter) and subtree ID (static include/exclude lists, plus dynamic BRC-127 group membership via `subtreegroup.Registry`). See [Subtree Filtering](#subtree-filtering) and [BRC-127](#group-announcement-protocol-brc-127) below.

**Gap tracking:** Per-flow `SeqNum` monotonic counter verification (keyed by `HashKey`). Gaps (`SeqNum` advances by >1) register gap entries; a background sweeper dispatches NACKs with exponential backoff. Gaps are auto-closed when the missing frame arrives via multicast or explicit NACK ACK.

**→ [bitcoin-shard-listener Architecture](https://github.com/lightwebinc/bitcoin-shard-listener/blob/main/docs/architecture.md)** — filter behavior table, gap tracker internals, configuration reference, metrics

---

### bitcoin-retry-endpoint (Retransmission)

**Purpose:** Caches frames, retransmits on NACK requests from listeners.

**Key Characteristics:**

- Single-worker multicast receiver (SO_REUSEPORT limitation)
- In-memory cache (freecache, 60 s TTL, GC-free, single 16-byte key: `HashKey ∥ SeqNum`)
- Multi-tier rate limiting: per-IP, per-HashKey, per-SeqNum pre-lookup; per-group (groupIdx) post-lookup (ACK still sent on throttle)
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

**Cache:** Single 16-byte key (`HashKey ∥ SeqNum`) → raw frame. Default backend: in-process freecache (60 s TTL, GC-free). Optional: Redis for cross-instance shared cache.

**→ [bitcoin-retry-endpoint Architecture](https://github.com/lightwebinc/bitcoin-retry-endpoint/blob/main/docs/architecture.md)** — cache encoding, rate-limit configuration, configuration reference, metrics

---

### bitcoin-shard-common (Protocol Primitives)

**Purpose:** Shared protocol primitives imported by proxy, listener, and retry endpoint.

**Packages:** `frame` (BRC-12/BRC-124/BRC-128 encode/decode), `shard` (txid → multicast group derivation), `seqhash` (XXH64 flow hash for HashKey), `sequence` (per-flow monotonic counters).

**→ [bitcoin-shard-common README](https://github.com/lightwebinc/bitcoin-shard-common)** — package API, [protocol spec](https://github.com/lightwebinc/bitcoin-shard-common/blob/main/docs/protocol.md)

---

## Retransmission and Reliability

### NACK Protocol (BRC-126)

Listeners detect sequence gaps and send 64-byte NACK datagrams to retry endpoints. The full wire format, response protocol, and escalation state machine are defined in:

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
     → Open ephemeral UDP socket; send 64-byte NACK; wait ≤300ms
     → ACK received: cancel gap entry
     → MISS received: advance endpoint; retry immediately (no backoff)
     → Timeout: exponential backoff; retry next sweep

3. If retries exhausted or GapTTL exceeded
   → Evict as bsl_gaps_unrecovered_total

4. Multicast repair arrives independently
   → Tracker.Fill() cancels pending gap regardless of NACK state
```

### Endpoint Discovery

Retry endpoints advertise via periodic ADVERT beacons (see [BRC-126](docs/brc-126-retransmission-protocol.md)). Listeners join the site beacon group (`FF05::B:FFFD`) and optionally the global beacon group (`FF0E::B:FFFD`) to discover endpoints dynamically. Static `-retry-endpoints` seeds the registry at lowest priority (`Tier=0xFF, Preference=0`).

Group address assignments for beacons and the control channel are defined in:

**→ [BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md)**

Inter-AS extension via MP-BGP requires no protocol changes — network teams extend the multicast fabric; endpoints and listeners operate identically.

The end-to-end NACK retransmission flow — from gap detection through escalation to repair delivery — is documented with ASCII diagrams in:

**→ [NACK Retransmission Flow](docs/nack-retransmission-flow.md)**

### Retry Endpoint Processing

The retry endpoint applies four-tier rate limiting (per-IP, per-HashKey, per-SeqNum pre-lookup; per-group post-lookup), performs a single-key cache lookup (`HashKey ∥ SeqNum`), and retransmits via multicast and/or unicast on a hit. On a miss, a 16-byte MISS response triggers immediate listener escalation. The group-tier limiter skips the retransmit but still sends ACK so the listener does not escalate unnecessarily.

See **[BRC-126 (Retransmission Protocol)](docs/brc-126-retransmission-protocol.md)** and **[bitcoin-retry-endpoint Architecture](https://github.com/lightwebinc/bitcoin-retry-endpoint/blob/main/docs/architecture.md)** for the full processing pipeline and rate-limit configuration.

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

A _subtree_ is an ordered set of related transactions sharing a common batch context. The 32-byte `SubtreeID` field allows downstream subscribers to associate frames with a named batch. In Teranode, this is currently used to batch transactions for processing and to link ordered sets of validated transactions from block templates. This may be extended to support transaction specialization, and some sort of dynamic announcement and hashing mechanism may be required later. A rudimentary implementation has been put together in the proposed [BRC-127: Subtree Group Announcement Protocol](https://github.com/lightwebinc/bitcoin-multicast/blob/main/docs/brc-127-subtree-announce.md).

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

**BRC-12 Frames:**

- BRC-12 frames have zero SubtreeID
- Only pass subtree filter if zero is explicitly listed in `subtree-include`

---

## Group Announcement Protocol (BRC-127)

BRC-127 defines the dynamic subtree group announcement protocol. Producers advertise which SubtreeIDs belong to which logical group by sending 64-byte `SubtreeAnnounce` datagrams (`MsgType 0x30`) to the proxy TCP ingress. The proxy forwards these verbatim to the control-plane multicast group (`CtrlGroupSubtreeAnnounce = 0xFFFC`). Listeners join this group and populate a `subtreegroup.Registry`, automatically accepting frames from announced subtrees without static configuration.

Announcements must be re-sent before their TTL expires (recommended: interval 10–30 s; TTL ≥ 3× interval). If announcements cease, entries expire and frames are dropped.

**→ [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md)** — wire format, listener configuration flags, distribution path, refresh/expiry rules

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

### Integration Test Scenarios (bitcoin-multicast-test)

**Purpose:** Full-stack integration testing across all components in an LXD-based lab environment.

The [bitcoin-multicast-test](https://github.com/lightwebinc/bitcoin-multicast-test) repository provides:

- **Lab setup scripts** — automated LXD VM provisioning (source, proxy, listeners, retry endpoints, metrics)
- **Ansible deploy** — single-command deployment of all services via `run-deploy.sh`
- **Scenario suite** — numbered test scenarios covering functional validation, shard/subtree filtering, multicast egress bridging, NACK retransmission, rate limiting, beacon discovery, MISS escalation, and BRC-127 group announcements
- **`run-all.sh`** — sequential execution of all scenarios with pass/fail summary

**Getting Started:**

```bash
cd bitcoin-multicast-test
bash lab/01-network.sh      # Create LXD networks and VM profiles
bash lab/03-launch.sh       # Launch VMs
bash ansible/run-deploy.sh  # Deploy all services
bash scenarios/run-all.sh   # Run full scenario suite
```

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

- [Wire Protocol Specification](https://github.com/lightwebinc/bitcoin-shard-common/blob/main/docs/protocol.md) — Complete BRC-12/BRC-124/BRC-128 frame format
- [BRC-124 Frame Format](docs/brc-124-frame-format.md) — 92-byte header, HashKey/SeqNum per-flow sequencing, backward compatibility
- [BRC-126 Retransmission Protocol](docs/brc-126-retransmission-protocol.md) — NACK/ACK/MISS wire formats, ADVERT beacon, tier/preference model
- [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md) — SubtreeAnnounce wire format, proxy forwarding, listener integration
- [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md) — EF payload format, detection, infrastructure impact
- [BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md) — IPv6 address scheme, control-plane indices, beacon groups
- [NACK Retransmission Flow](docs/nack-retransmission-flow.md) — End-to-end pipeline diagrams, escalation state machine, flood prevention

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

- The BRC-12 wire-frame format transports transactions conforming to BRC-12
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

| Version | Header Size | Flow Sequencing      | Subtree Support  |
| ------- | ----------- | -------------------- | ---------------- |
| BRC-12  | 44 bytes    | No                   | No               |
| BRC-124 | 92 bytes    | Yes (HashKey/SeqNum) | Yes              |
| BRC-128 | 92 bytes    | Yes (HashKey/SeqNum) | Yes (EF payload) |

---

_Document Version: 1.8_  
_Last Updated: 2026-05-14_
