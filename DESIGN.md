# BSV Layered Multicast

## Overview

The BSV Layered Multicast project is a high-throughput, horizontally-scalable
transaction distribution system for BSV (Bitcoin SV) designed to pave the road
towards 1 billion+ transactions per second. It uses IPv6 multicast to
efficiently distribute transaction data across a fabric of subscribers (miners,
exchanges, service providers) with deterministic sharding and NACK-based
reliability.

This document provides a comprehensive design overview of the entire multicast
ecosystem, encompassing all repositories and their interactions.

**Conceptual Attribution:** The IPv6 multicast transaction broadcast
architecture from which this software draws inspiration was articulated by Dr.
Craig S. Wright in
[Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast).

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
- [Ingress Authorization (Miner-tier Gate)](#ingress-authorization-miner-tier-gate)
- [Retransmission and Reliability](#retransmission-and-reliability)
- [Subtree Filtering](#subtree-filtering)
- [Fragmentation (BRC-130)](#fragmentation-brc-130)
- [Transaction Coalescing (BRC-142)](#transaction-coalescing-brc-142)
- [Subtree Group Announcement (BRC-127)](#subtree-group-announcement-brc-127)
- [Block Announcement Frame Format (BRC-131)](#block-announcement-frame-format-brc-131)
- [Subtree Data Frame Format (BRC-132)](#subtree-data-frame-format-brc-132)
- [Coinbase Transaction Frame Format (BRC-133)](#coinbase-transaction-frame-format-brc-133)
- [Anchor Transaction Frame Format (BRC-134)](#anchor-transaction-frame-format-brc-134)
- [Block Header Format (BRC-135)](#block-header-format-brc-135)
- [Shard Manifest Announcement (BRC-139)](#shard-manifest-announcement-brc-139)
- [Source-Specific Multicast (SSM)](#source-specific-multicast-ssm)
- [Automatic Shard Configuration](#automatic-shard-configuration)
- [Testing and Validation](#testing-and-validation)
- [Deployment Considerations](#deployment-considerations)

---

## Terminology

| Term               | Definition                                                                                                                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Shard**          | A deterministic partition of the transaction space. Each shard maps to one IPv6 multicast group; group membership is derived from the TxID.                                                   |
| **Subtree**        | An ordered set of related transactions sharing a common 32-byte batch identifier (`SubtreeID`, or Subtree Merkle root hash). Used for transaction specialization and block template assembly. |
| **Gap**            | A detected break in the monotonic `SeqNum` counter for a flow, indicating one or more missing frames.                                                                                         |
| **Flow**           | The per-(sender IP, multicast group, subtree) sequence of frames sharing a common `HashKey`.                                                                                                  |
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
│                        Ingress Tier (ingress-infra)                         │
│                    Deploys: shard-proxy nodes                               │
│                  Stateless, deterministic, horizontally scalable            │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │  IPv6 UDP Multicast (FF05::B:<shard>)
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

- **Stateless Ingress:** Proxy nodes carry no state; any number can be deployed
  without coordination
- **Deterministic Sharding:** Same transaction ID always maps to the same
  multicast group
- **Consistent Hashing:** Increasing shard bits splits groups without
  invalidating existing subscriptions
- **Horizontal Scale:** Add nodes to scale capacity; no reconfiguration of
  existing nodes required
- **NACK-based Recovery:** Listeners detect gaps and request retransmission from
  cached retry endpoints

---

## Repository Overview

The project is organized into multiple repositories, each with a specific
responsibility:

### Core Services (Binaries)

| Repository                                                      | Purpose                                                                                     |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| [shard-proxy](https://github.com/lightwebinc/shard-proxy)       | Stateless ingress proxy; receives frames, derives multicast group, forwards verbatim        |
| [shard-listener](https://github.com/lightwebinc/shard-listener) | Multicast subscriber; filters by shard/subtree, forwards to unicast and multicast consumers |
| [retry-endpoint](https://github.com/lightwebinc/retry-endpoint) | Caches frames, retransmits on NACK requests                                                 |
| [shard-manifest](https://github.com/lightwebinc/shard-manifest) | BRC-139 manifest announcer; emits `shard_bits` + joined-groups beacons                      |

### Shared Libraries

| Repository                                                  | Purpose                                    | Packages                                           |
| ----------------------------------------------------------- | ------------------------------------------ | -------------------------------------------------- |
| [shard-common](https://github.com/lightwebinc/shard-common) | Protocol primitives shared across services | `frame`, `shard`, `bundle`, `seqhash`, `sequence`, `txidset`, `cache`, `pow`, `manifest`, `netjoin`, … (full list in the repo README) |

### Infrastructure Automation

| Repository                                                                  | Purpose                                         | Deploys             |
| --------------------------------------------------------------------------- | ----------------------------------------------- | ------------------- |
| [ingress-infra](https://github.com/lightwebinc/ingress-infra)               | Ansible/Terraform for ingress proxy deployment  | shard-proxy         |
| [listener-infra](https://github.com/lightwebinc/listener-infra)             | Ansible/Terraform for listener deployment       | shard-listener      |
| [retransmission-infra](https://github.com/lightwebinc/retransmission-infra) | Ansible/Terraform for retry endpoint deployment | retry-endpoint      |
| [manifest-infra](https://github.com/lightwebinc/manifest-infra)             | Ansible/Terraform for manifest deployment       | shard-manifest      |
| [multicast-kube-infra](https://github.com/lightwebinc/multicast-kube-infra) | Kubernetes deployment (k0s reference, EKS stub) | full stack via Helm |
| [integrated-infra](https://github.com/lightwebinc/integrated-infra)         | Collapsed single-host node (Ansible/Terraform)  | all three services  |

### Helm Charts

Each service has a dedicated chart repository, consumed by
`multicast-kube-infra`:

| Repository                                                                  | Chart           |
| --------------------------------------------------------------------------- | --------------- |
| [shard-proxy-helm](https://github.com/lightwebinc/shard-proxy-helm)         | shard-proxy     |
| [shard-listener-helm](https://github.com/lightwebinc/shard-listener-helm)   | shard-listener  |
| [retry-endpoint-helm](https://github.com/lightwebinc/retry-endpoint-helm)   | retry-endpoint  |
| [subtx-generator-helm](https://github.com/lightwebinc/subtx-generator-helm) | subtx-generator |
| [shard-manifest-helm](https://github.com/lightwebinc/shard-manifest-helm)   | shard-manifest  |

### Testing and Tools

| Repository                                                        | Purpose                                                                                        |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| [subtx-generator](https://github.com/lightwebinc/subtx-generator) | Traffic generator for load/functional testing                                                  |
| [multicast-test](https://github.com/lightwebinc/multicast-test)   | Integration test suite: Go + Docker scenarios (`harness/`) on an isolated IPv6 bridge          |

### Meta Repository

| Repository                                                    | Purpose                                                    |
| ------------------------------------------------------------- | ---------------------------------------------------------- |
| [bsv-multicast](https://github.com/lightwebinc/bsv-multicast) | This repository; project overview and design documentation |

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
                    │  IPv6 UDP Multicast (FF05::B:<shard>, port 9001)        │
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
                                              (re-multicast to FF05::B:<shard>)
```

---

## Data Flow

### Normal Flow (No Retransmission)

```text
1. BSV Sender → shard-proxy
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ UDP/TCP: BRC-12/BRC-124 frame (TxID, payload, Sequence, Subtree)        │
   └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
2. shard-proxy
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ • Decode frame (extract TxID)                                           │
   │ • Stamp HashKey/SeqNum in-place (BRC-124 only, bytes 40–55)             │
   │ • Derive multicast group: FF05::B:<groupIndex> from TxID top bits       │
   │ • Forward verbatim to all egress interfaces                             │
   └─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
3. Multicast Fabric
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ FF05::B:<groupIndex>:9001 delivered to all joined subscribers           │
   │ (MLD snooping / PIM distribution tree)                                  │
   └─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
4a. Direct Subscriber              4b. shard-listener
   ┌─────────────────────────────┐         ┌──────────────────────────────────────────┐
   │ Miner / Exchange            │         │ • Join configured shard groups via MLD   │
   │ (consumes directly)         │         │ • Apply shard filter (defense-in-depth)  │
   └─────────────────────────────┘         │ • Apply subtree filter (include/exclude) │
                                           │ • Track sequence gaps per flow           │
                                           │   (HashKey/SeqNum monotonic counter)     │
                                           │ • Forward matching frames to egress_addr │
                                           │   (UDP or TCP, optional strip-header)    │
                                           └──────────────────────────────────────────┘
                                                            │
                                                            ▼
                                                5. Downstream Consumer
```

### Retransmission Flow (NACK-based)

```text
shard-listener detects gap:
┌─────────────────────────────────────────────────────────────────────────┐
│ • SeqNum > lastSeqNum + 1 (gap detected for this HashKey)               │
│ • Register missing frame(s) in pending map (key = HashKey + SeqNum)     │
│ • Background sweeper dispatches NACK after nack-gap-ttl                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
NACK Dispatch (UDP to retry-endpoint:9300)
┌─────────────────────────────────────────────────────────────────────────┐
│ 64-byte BRC-126 NACK datagram (see BRC-126 wire format)                 │
│ Endpoints tried by (Tier ASC, Preference DESC); MISS triggers escalation│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
retry-endpoint
┌─────────────────────────────────────────────────────────────────────────┐
│ • Receive NACK on port 9300                                             │
│ • Rate limit (IP, HashKey, SeqNum)                                      │
│ • Lookup frame in cache by HashKey + SeqNum (single 16-byte key)        │
│ • If found: re-multicast to FF05::B:<shard>:9001 (-beacon-flags-        │
│   multicast); unicast to NACK source (-beacon-flags-unicast); send ACK  │
│ • If not found: send 16-byte MISS (listener escalates to next endpoint) │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
shard-listener receives repair
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
IPv6 group = [FFsc::<group-id>:<groupIndex>]   (default group-id 0x000B)
```

**Example with shard_bits=2:**

| txid[0:4] (hex) | txid[0:4] (uint32) | >> 30 | groupIndex | Multicast Address |
| --------------- | ------------------ | ----- | ---------- | ----------------- |
| 0x12345678      | 305419896          | 0     | 0          | FF05::B:0         |
| 0x87654321      | 2271560481         | 3     | 3          | FF05::B:3         |
| 0xABCD1234      | 2882343444         | 2     | 2          | FF05::B:2         |
| 0x4567ABCD      | 1164413357         | 1     | 1          | FF05::B:1         |

### Consistent Hashing Property

Using top bits (right shift) instead of modulo provides consistent hashing:

```text
shard_bits = 2  →  4 groups (0, 1, 2, 3)
shard_bits = 3  →  8 groups (0a, 0b, 1a, 1b, 2a, 2b, 3a, 3b)

Group 0 splits into:  0a (txid[0] bit 31 = 0), 0b (txid[0] bit 31 = 1)
Group 1 splits into:  1a (txid[0] bit 31 = 0), 1b (txid[0] bit 31 = 1)
...
```

**Benefit:** When increasing shard_bits, subscribers only need to join
additional groups. Existing subscriptions remain valid.

A worked walkthrough of the top-bits extraction arithmetic is in
[shard_bit_extraction.pdf](docs/explanations/shardbits/shard_bit_extraction.pdf).

### IPv6 Multicast Address Layout (IANA-aligned)

```text
Bits [127:112]   FF0X  Multicast prefix + scope (e.g., FF05 for site-local)
Bits [111: 32]   0x00  Zero (IANA 96-bit boundary, 80 bits)
Bits [ 31: 16]   GID   IANA group-id (default 0x000B = IANA Bitcoin SV Node Groups)
Bits [ 15:  0]   IDX   Shard index (up to 16 bits; top 4 reserved for control)
```

The IANA Bitcoin SV Node Groups allocation is `FF0X::B`. Operators MAY override
the group-id via `-mc-group-id` for testing or private deployments, but the
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
| 0xFFFB | Subtree data (site)             | FF05  | FF05::B:FFFB       |
| 0xFFFB | Subtree data (org)              | FF08  | FF08::B:FFFB       |
| 0xFFFB | Subtree data (global)           | FF0E  | FF0E::B:FFFB       |
| 0xFFFC | Subtree Group announce (site)   | FF05  | FF05::B:FFFC       |
| 0xFFFC | Subtree Group announce (org)    | FF08  | FF08::B:FFFC       |
| 0xFFFC | Subtree Group announce (global) | FF0E  | FF0E::B:FFFC       |
| 0xFFFD | Beacon (site)                   | FF05  | FF05::B:FFFD       |
| 0xFFFD | Beacon (org)                    | FF08  | FF08::B:FFFD       |
| 0xFFFD | Beacon (global)                 | FF0E  | FF0E::B:FFFD       |
| 0xFFFE | Block Control channel           | FF0E  | FF0E::B:FFFE       |
| 0xFFFF | _(reserved)_                    | —     | do not use         |

See
[BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md)
for full details.

## Frame Format

The BRC-124 data-plane frame format (92-byte header, replacing the legacy
44-byte BRC-12 header) is defined in a dedicated design document:

**→ [BRC-124 Frame Format](docs/brc-124-frame-format.md)**

**Transaction ingress (framed, bare, EF-native).** A transaction enters on the
tx port (8725) either *framed* (a BRC-124/128/legacy-BRC-12 frame, identified by
the leading network magic) or *bare* (a header-stripped transaction, one per
datagram, detected by the absence of the magic and wrapped into an unstamped
frame). A single ingress path serves both — there is no separate raw-tx port.
Under the opt-in **`-require-ef`** the ingress is *EF-native*: a submission must
be BRC-30 Extended Format, because Teranode requires EF and the stateless fabric
cannot extend a raw transaction (extension needs a per-input UTXO lookup — a
wallet operation — and a raw tx shares its extended form's TxID, so a fabric
re-transmit would collide with ingress dedup). Relayed (already-stamped) frames
are exempt, so the relay hot path is untouched. See the proxy's
`docs/architecture.md` § Transaction ingress.

Key fields: Network magic, Protocol version, Frame version, Transaction ID,
HashKey (XXH64 per-flow identifier), SeqNum (monotonic per-flow counter),
Subtree ID, Payload length, and BSV tx payload. Both BRC-12 (legacy) and
BRC-124/BRC-128 frames are accepted by all components.

**BRC-128 (Extended Format):** BRC-128 frames carry BRC-30 Extended Format (EF)
transaction payloads inside the standard 92-byte BRC-124 header. Frame Version
remains `0x02`; the payload is self-identifying via the 6-byte EF marker at
payload bytes 4–9 (`0x000000000000EF`). All infrastructure components are
payload-agnostic — no changes required to proxy, listener, or retry endpoint.

**→ [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md)**

**BRC-130 (Fragmentation):** BRC-130 fragments large transactions that exceed
the path MTU into a sequence of fixed-size UDP datagrams (Frame Version `0x03`).
Bytes 0–91 are layout-identical to BRC-124, preserving firewall rule and
classifier compatibility. The proxy stamps an independent `HashKey`/`SeqNum` per
fragment so that individual lost fragments can be retransmitted via the standard
BRC-126 NACK mechanism without changes to the retry endpoint. Listeners
reassemble fragments keyed on `TxID`, verify `SHA256d`, and deliver a synthetic
BRC-124 frame to the normal filter → egress → gap-tracking pipeline.

**→ [BRC-130 Fragmentation](docs/brc-130-fragmentation.md)**

**BRC-132 (Subtree Data Frame Format):** BRC-132 defines Frame Version `0x05`
for distributing complete subtree data payloads (transaction hashes and optional
fee/size metadata) over the multicast fabric. Frames are delivered to the
`GroupSubtreeDataAnnounce` group (`FF0X::B:FFFB`). Two message types are defined:
`HashesOnly` (32 bytes/node) and `FullNodes` (48 bytes/node, includes fee and
size). The 92-byte header is layout-identical to BRC-124. Payloads of 32–48 MB
(at 1M nodes) are always fragmented via BRC-130 (`OrigFrameVer=0x05`). Gap
tracking and NACK retransmission work identically to BRC-124; the retry endpoint
retransmits to `FF0X::B:FFFB` rather than a shard group.

**→ [BRC-132 Subtree Data Frame Format](docs/brc-132-subtree-data.md)**

**BRC-133 (Coinbase Transaction):** BRC-133 formalizes MsgType `0x02` within
BRC-131 frames (FrameVer `0x04`) as the canonical wire format for distributing
raw coinbase transactions. The ContentID in the frame header carries the SHA256d
of the coinbase transaction. Frames are delivered on `GroupBlockBroadcast`
(`FF0E::B:FFFE`); NACK-based retransmission and gap tracking work identically to
BRC-131 block announcement frames.

**→
[BRC-133 Coinbase Transaction Frame Format](docs/brc-133-coinbase-delivery.md)**

**BRC-134 (Anchor Transaction):** BRC-134 defines Frame Version `0x06` for
distributing chained anchor transactions — the root transaction of a dependent
chain — over the control plane. Because all subsequent chain transactions
reference the anchor as an input, every subscriber must receive it regardless of
shard assignment. The 92-byte header is layout-identical to BRC-124 with
`FrameVer=0x06`; the TxID field carries the SHA256d of the anchor transaction.
Frames are delivered on `GroupBlockBroadcast` (`FF0E::B:FFFE`). BRC-130
fragmentation is not defined for BRC-134. Gap tracking and NACK retransmission
are identical to BRC-131.

**→
[BRC-134 Anchor Transaction Frame Format](docs/brc-134-anchor-transactions.md)**

**BRC-135 (Block Header Format):** BRC-135 defines Frame Version `0x07` for
distributing standalone 80-byte BSV block headers. BRC-135 frames are produced
by an emitter (any node that receives a BRC-131 `BlockAnnounce`) by extracting
the raw 80-byte header and wrapping it in a minimal 172-byte frame. The emitter
stamps its own `HashKey`/`SeqNum` and sends the frame to its configured egress
(unicast or multicast). BRC-135 frames are not re-injected onto the primary
fabric. No fragmentation is required.

**→
[BRC-135 Multicast Block Header Format](docs/brc-135-block-header-format.md)**

**BRC-142 (Transaction Bundle / Coalescing):** BRC-142 packs many small
transactions destined for the same shard group and subtree into a single
datagram (Frame Version `0x08`, 66-byte bundle header) — the inverse of BRC-130
fragmentation — to cut packets-per-second on the fabric. A bundle carries a
single `HashKey`/`SeqNum` and rides the BRC-126 NACK/retransmission machinery
as one "fat frame"; gap tracking and retry are at bundle granularity. Bundles
never exceed the path MTU (coalescing and fragmentation are mutually exclusive
per datagram). Coalescing is an opt-in origin-proxy stage (`-coalesce`, default
off); relays re-emit bundles verbatim; listeners decoalesce at the edge by
default (whole-bundle consumer delivery is opt-in), re-bucketing bundles built
at a different `ShardBits` generation before delivery.

**→
[BRC-142 Coalescing (Bundle) Frame Format](docs/brc-142-coalescing-frame.md)**

---

## Component Deep Dives

### shard-proxy (Ingress)

**Purpose:** Stateless ingress proxy; receives BSV transaction frames, derives
multicast group, forwards verbatim.

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

**Hot path:** Decode frame → stamp HashKey/SeqNum in-place (BRC-124 only) →
derive multicast group from TxID → `WriteTo` verbatim to all egress interfaces.
TCP connections carry the same frame stream plus BRC-127 SubtreeAnnounce control
datagrams (forwarded verbatim to the announce multicast group).

**→
[shard-proxy Architecture](https://github.com/lightwebinc/shard-proxy/blob/main/docs/architecture.md)**
— hot-path detail, configuration reference, metrics

---

### shard-listener (Subscriber)

**Purpose:** Multicast subscriber; filters by shard/subtree, forwards to unicast
and/or multicast consumers, performs NACK-based gap recovery.

**Key Characteristics:**

- Role modes: `-mode collapsed` (default: join + demux + gap/NACK + fan-out),
  `receiver` (multicast half only), `delivery` (consumer half: unicast ingest +
  fan-out; no multicast join, no gap/NACK)
- SO_REUSEPORT multi-worker receive (kernel-level source affinity)
- Dual-level filtering: MLD group join + userspace shard/subtree filter
- NORM-inspired gap tracking per flow via HashKey/SeqNum monotonic counter
- NACK dispatch to configurable retry endpoints
- Egress via UDP or TCP (optional strip-header mode)
- Multicast egress for domain bridging (re-emit filtered frames to a separate
  multicast address space)
- BRC-130 fragment reassembly (`reassembly` package) with SHA256d verification

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
               │     • reassembly.Buffer (BRC-130 fragments)
               │

SubtreeAnnounceListener (1 goroutine, BRC-127)
  ┌──────────────────────────────────────────┐
  │ Join FF05::B:FFFC (SO_REUSEPORT socket)  │──▶ subtreegroup.Registry
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

**Important:** Linux delivers multicast to ALL SO_REUSEPORT sockets (no load
balancing). For multicast deployments, `NUM_WORKERS` must be set to 1.

**Filtering:** Dual-level — shard index (MLD group join + userspace filter) and
subtree ID (static include/exclude lists, plus dynamic BRC-127 group membership
via `subtreegroup.Registry`). See [Subtree Filtering](#subtree-filtering) and
[BRC-127](#subtree-group-announcement-brc-127) below.

**Gap tracking:** Per-flow `SeqNum` monotonic counter verification (keyed by
`HashKey`). Gaps (`SeqNum` advances by >1) register gap entries; a background
sweeper dispatches NACKs with exponential backoff. Gaps are auto-closed when the
missing frame arrives via multicast or explicit NACK ACK.

**→
[shard-listener Architecture](https://github.com/lightwebinc/shard-listener/blob/main/docs/architecture.md)**
— filter behavior table, gap tracker internals, configuration reference, metrics

---

### retry-endpoint (Retransmission)

**Purpose:** Caches frames, retransmits on NACK requests from listeners.

**Key Characteristics:**

- Single-worker multicast receiver (SO_REUSEPORT limitation)
- Pluggable cache backend (`shard-common/cache`): in-process striped map
  (default), Redis, or Aerospike; 60 s TTL, single 16-byte key:
  `HashKey ∥ SeqNum`
- Multi-tier rate limiting: per-IP, per-HashKey, per-SeqNum pre-lookup;
  per-group (groupIdx) post-lookup. The honest-congestion tiers (per-HashKey,
  per-SeqNum, per-group) optionally emit THROTTLED (`-rl-throttle-response`)
  so the listener holds rather than escalates; without it they stay silent
- Sharding-based multicast egress for retransmitted frames

**Architecture:**

```text
Multicast Receiver (1 worker, SO_REUSEPORT)
  ┌──────────────────┐
  │ Join all groups  │──▶ Cache (memory/redis/aerospike, 60 s TTL)
  └──────────────────┘

NACK Server (NACK_WORKERS goroutines)
  ┌──────────────────┐
  │ UDP listener     │──▶ Rate limit ──▶ Cache lookup ──▶ Retransmit
  └──────────────────┘

Retransmit Egress
  ┌──────────────────┐
  │ Multicast send   │──▶ FF05::B:<shard>:9001
  └──────────────────┘
```

**Cache:** Single 16-byte key (`HashKey ∥ SeqNum`) → raw frame. Pluggable
`shard-common/cache` backend (`-cache-backend`): in-process striped map
(default), Redis, or Aerospike for cross-instance shared cache (60 s TTL). See
[shard-common cache backend](https://github.com/lightwebinc/shard-common/blob/main/docs/cache-backend.md).

**→
[retry-endpoint Architecture](https://github.com/lightwebinc/retry-endpoint/blob/main/docs/architecture.md)**
— cache encoding, rate-limit configuration, configuration reference, metrics

---

### shard-common (Protocol Primitives)

**Purpose:** Shared protocol primitives imported by proxy, listener, and retry
endpoint.

**Packages:** `frame` (BRC-12/BRC-124/BRC-128/BRC-130/BRC-132/BRC-133/BRC-134
encode/decode, `EncodeFragment`/`DecodeFragment`/`IsFragment`,
`EncodeSubtreeData`/`DecodeSubtreeData`/`IsSubtreeDataFrame`,
`BlockMsgCoinbase`, `DecodeBlock`, `FrameVerV6`,
`DecodeAnchor`/`IsAnchorFrame`), `shard` (txid → multicast group derivation),
`bundle` (BRC-142 bundle encode/decode + re-bucketing), `seqhash` (XXH64 flow
hash for HashKey), `sequence` (per-flow monotonic counters), `txidset` (TxID
dedup), `cache` (pluggable memory/Redis/Aerospike backend), `pow` (stateless
block-header PoW check), `manifest` (BRC-139 registry + adoption gates), and
`netjoin` (SSM join/leave management). Full list in the repo README.

**→ [shard-common README](https://github.com/lightwebinc/shard-common)** —
package API,
[protocol spec](https://github.com/lightwebinc/shard-common/blob/main/docs/protocol.md)

---

## Ingress Authorization (Miner-tier Gate)

Most ingress is open by design: any sender submits a transaction (BRC-12 /
124 / 128) to the nearest proxy and it is sharded to the fabric. But three
frame classes are **privileged control-plane** messages that egress to a
broadcast group every subscriber receives, and must originate only from
miner-tier peers:

| Class | Frame | Egress group |
|-------|-------|--------------|
| Block announce | BRC-131 (`FrameVerV4`, `BlockMsgAnnounce`) | `GroupBlockBroadcast` |
| Coinbase | BRC-133 (`FrameVerV4`, `BlockMsgCoinbase`) | `GroupBlockBroadcast` |
| Subtree data | BRC-132 (`FrameVerV5`) | `GroupSubtreeDataAnnounce` |

End-user / service consumers (which both submit ordinary transactions) must
not be able to announce blocks — including indirectly, e.g. relaying a libp2p
block-gossip message up a consumer tunnel.

Two distinct mechanisms cover this, and the distinction is load-bearing —
conflating them is how a permissionless network accidentally becomes a
permissioned one:

1. **Permissionless protocol gate (validate the artifact).** A block
   announcement carries its own authorization in the form of proof of work —
   anyone may announce, but the announcement must hash under its claimed
   target. No identity, no allowlist, nothing to coordinate across domains.
   This is the BSV-native gate and the right default for an open network.
2. **Domain-local admission control (govern your own resources).** An operator
   may additionally restrict *which peers may use this domain's edges and
   metered bandwidth* — a business/operational policy (subscription, abuse
   attribution), not a network-wide permission. It is identity/network based
   and therefore only meaningful inside the issuing domain. A peer an operator
   declines can still announce through another domain or its own ingress; the
   network stays open.

The frame-class gate and source allowlist below are mechanism (2) — admission
control. Proof-of-work validation is mechanism (1). They compose: a privileged
frame may be required to both arrive on an admitted path AND carry valid work.

### Per-socket frame-class gate (admission control)

The proxy enforces authorization as a property of the **ingress socket**, not
the host, so one edge serves miners and ordinary consumers interleaved (best
fit per device + bandwidth):

- **User / transaction ingress** (`-udp-listen-port`, default 8725;
  `-tcp-listen-port`) accepts transactions + BRC-134 anchor only. Privileged
  BRC-131/133/132 frames are dropped at `forwarder.DispatchClass` and counted
  (`bsp_privileged_frame_rejected_total{frame_type}`). Exposed to all
  consumers.
- **Miner ingress** (`-miner-listen-port`, e.g. 9000; `-miner-tcp-listen-port`)
  accepts every class, including the privileged ones. Opening it is the
  proxy's "accept block/coinbase/subtree data?" switch (both `0` ⇒ the proxy
  ingests transactions only).

`-tx-accept-privileged` (default `false`) reverts the user port to legacy
accept-all for collapsed/dev single-port nodes.

### Network access = tier

The gate above is the application-layer enforcement point (it holds even if a
firewall is misconfigured). Operationally, *which* peers can reach the miner
port is the network-access layer: an operator's control plane / subscription
system routes miner-tier peers to the edge's miner port and maintains the
firewall source set that admits them; consumer paths reach only 8725. Miners
that are not customers can be added to the miner source set out of band — no
subscription record required. The open data plane stays tier-agnostic (just
sockets + a frame-class gate); the operator's control plane supplies the
routing and source roster.

### Permissionless gate: proof of work

The permissionless mechanism validates the **artifact**, not the emitter. A
BRC-131 block announce carries the 80-byte header in-frame; the proxy gates it
(opt-in, `-require-block-pow`) on a cheap stateless check — `hash(header) ≤
target(nBits)` and that target ≤ a configured difficulty floor
(`-min-pow-bits`). Forging a passing header costs work proportional to the
floor; verifying costs one double-SHA256. That asymmetry is the spam gate, and
because proof of work is globally verifiable it needs **zero cross-domain
coordination** — every domain checks identically against the same chain rules,
with no shared key registry to replicate or reconcile.

This is deliberately not full consensus validation (the proxy has no chain
context to confirm `nBits` is the correct retarget for the height); it rejects
ingress spam cheaply, and the consuming node (Teranode) does full validation.
Rejections increment `bsp_block_pow_rejected_total`. Implemented in
`shard-common/pow` + `shard-proxy` (`forwarder.SetBlockPoW`).

**The check belongs at the listener too, not only the proxy.** A block
announcement that originates in another domain arrives over the multicast
fabric (inter-domain peering) and never passes our proxy — so the **listener**
independently re-validates before fan-out (opt-in, `-require-block-pow`):

- **Block announce (BRC-131):** same stateless header-PoW check before
  forwarding downstream; a frame failing PoW is dropped (`bsl_frames_dropped_total{reason="block_pow"}`)
  and not gap-tracked, so a junk injection can't pollute recovery state.
- **Coinbase (BRC-133):** has no in-frame PoW, so it is gated by **correlation**
  — the listener records the coinbase TxID of every PoW-valid block announce
  (a shared, TTL-bounded `CoinbaseCorrelator`) and forwards a coinbase frame
  only if its TxID matches one (`reason="coinbase_uncorrelated"` otherwise). An
  uncorrelated coinbase (arriving before its block, or matching none) is
  dropped and re-evaluated if the block arrives and the coinbase is re-sent.
- **Subtree data (BRC-132):** no in-frame PoW and no block to correlate against
  pre-block; bounded only by admission control + the 60s cache TTL (unanchored
  subtrees age out). This is the soft edge, called out honestly.

Implemented in `shard-common/pow` + `shard-listener`
(`listener.Worker.SetBlockPoW`, `CoinbaseCorrelator`), on both the direct and
the BRC-130-reassembled block paths.

> **Anchor transactions (BRC-134) are deliberately ungated — and stay that way.**
> Anchors are **user-submitted**: any participant may create and emit one, and
> wide dissemination to every subscriber is the intended behaviour. They carry
> no PoW gate and no admission requirement — the permissionless baseline. This
> is a firm design decision: anchors are NOT folded into the miner-tier gate.

> **Cross-domain note on signing.** Cryptographic frame signing (a per-frame
> identity signature against a pubkey allowlist) is a *domain-local* attribution
> tool — useful inside one commercial domain for billing / abuse attribution,
> but it does NOT generalise across domains: a signature registry is shared
> mutable state with revocation/split-brain coordination costs, exactly what
> multicast set out to avoid. Inter-domain, frames are re-validated by proof of
> work, not by signature. Signing stays an optional per-domain add-on, never the
> network authorization.

---

## Retransmission and Reliability

### NACK Protocol (BRC-126)

Listeners detect sequence gaps and send 64-byte NACK datagrams to retry
endpoints. The full wire format, response protocol, and escalation state machine
are defined in:

**→
[BRC-126 (Retransmission Protocol)](docs/brc-126-retransmission-protocol.md)**

**Key changes from the original fire-and-forget NACK model:**

- **ACK/MISS responses** — every NACK receives a unicast response (16 bytes).
  ACK confirms retransmit dispatched; MISS indicates cache miss and triggers
  immediate escalation to the next endpoint.
- **THROTTLED response (optional)** — an honest-congestion signal (16 bytes,
  `MsgType 0x13`) emitted by the per-flow/per-gap/per-group rate-limit tiers
  when enabled (`-rl-throttle-response`). The listener holds the gap for a
  hinted backoff and retries the same endpoint without escalating or consuming
  its retry budget. The flood (per-source-IP) tier stays silent to avoid
  reflection.
- **Beacon discovery** — retry endpoints periodically multicast ADVERT messages
  (56 bytes) to site/global beacon groups. Listeners maintain a dynamic endpoint
  registry, sorted by `(Tier ASC, Preference DESC)`.
- **Tier-based escalation** — on MISS, listeners advance through endpoints at
  the same tier, then escalate to the next tier. No backoff on MISS; immediate
  retry.
- **Configurable retransmit modes** — endpoints can retransmit via multicast,
  unicast, or both. Responses can be selectively suppressed.

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

Retry endpoints advertise via periodic ADVERT beacons (see
[BRC-126](docs/brc-126-retransmission-protocol.md)). Listeners join the site
beacon group (`FF05::B:FFFD`) and optionally the global beacon group
(`FF0E::B:FFFD`) to discover endpoints dynamically. Static `-retry-endpoints`
seeds the registry at lowest priority (`Tier=0xFF, Preference=0`).

Group address assignments for beacons and the control channel are defined in:

**→
[BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md)**

Inter-AS extension via MP-BGP requires no protocol changes — network teams
extend the multicast fabric; endpoints and listeners operate identically.

The end-to-end NACK retransmission flow — from gap detection through escalation
to repair delivery — is documented with ASCII diagrams in:

**→ [NACK Retransmission Flow](docs/nack-retransmission-flow.md)**

### Retry Endpoint Processing

The retry endpoint applies four-tier rate limiting (per-IP, per-HashKey,
per-SeqNum pre-lookup; per-group post-lookup), performs a single-key cache
lookup (`HashKey ∥ SeqNum`), and retransmits via multicast and/or unicast on a
hit. On a miss, a 16-byte MISS response triggers immediate listener escalation.
When `-rl-throttle-response` is enabled, the honest-congestion tiers
(per-HashKey, per-SeqNum, per-group) return a 16-byte THROTTLED response so the
listener holds and retries the same endpoint; without it they stay silent and
the listener falls back to timeout + backoff.

See
**[BRC-126 (Retransmission Protocol)](docs/brc-126-retransmission-protocol.md)**
and
**[retry-endpoint Architecture](https://github.com/lightwebinc/retry-endpoint/blob/main/docs/architecture.md)**
for the full processing pipeline and rate-limit configuration.

### Reliability Characteristics

**Best-effort delivery with deterministic escalation:**

- Multicast delivery is inherently unreliable
- NACK + ACK/MISS provides deterministic gap recovery signalling
- MISS triggers immediate escalation (no wasted backoff time)
- Multicast repair path and NACK path are independent; either can fill a gap
- No guarantee of recovery (network partition, cache expiration, all endpoints
  MISS)

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

A _subtree_ is an ordered set of related transactions sharing a common batch
context. The 32-byte `SubtreeID` field allows downstream subscribers to
associate frames with a named batch. In Teranode, this is currently used to
batch transactions for processing and to link ordered sets of validated
transactions from block templates. This may be extended to support transaction
specialization, and some sort of dynamic announcement and hashing mechanism may
be required later. A rudimentary implementation has been put together in the
proposed
[BRC-127: Subtree Group Announcement](https://github.com/lightwebinc/bsv-multicast/blob/main/docs/brc-127-subtree-announce.md).

**Use Cases:**

- Shard by transaction type (payments, contracts, tokens)
- Shard by application or service
- Shard by geographic region
- Shard by time window

### Subtree Filter Behavior

**Include Mode:**

```text
subtree-include = "abc123...,def456..."  (hex, 32-byte each)
→ Only frames with SubtreeID in this set pass
```

**Exclude Mode:**

```text
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

## Fragmentation (BRC-130)

BRC-130 solves the path-MTU problem for large BSV transactions without relying
on IP-layer fragmentation (unreliable on multicast paths). The proxy slices the
payload into _k_ equal-sized chunks and emits _k_ independent UDP datagrams.
Each datagram carries a 104-byte header that is layout-compatible with BRC-124
at bytes 0–91 (`FrameVer=0x03`).

**Fragment data size** at standard Ethernet MTU (1500 B):
`1500 − 40 − 8 − 104 = 1348 bytes/fragment`.

**Per-fragment gap tracking:** The proxy stamps an independent
`HashKey`/`SeqNum` per fragment so each fragment is treated as a separate frame.
Individual lost fragments are recovered via the standard BRC-126 NACK mechanism
— no changes to the retry endpoint.

**Listener reassembly (`reassembly` package):**

```text
 Fragment arrives (FrameVer=0x03)
   → Allocate slot (TxID key, OrigPayloadLen buffer, FragTotal bitmask, TTL)
   → Copy fragment data at offset = FragIndex × fragDataSize
   → All bits set → SHA256d verify → deliver synthetic BRC-124 frame
   → filter → egress → gap-tracking (unchanged)

 TTL expiry (10 s): drop slot; bsl_reassembly_abandoned_total++
 Hash mismatch:     drop slot; bsl_reassembly_hash_mismatch_total++
```

Key metrics: `bsl_reassembly_started_total`, `bsl_reassembly_completed_total`,
`bsl_reassembly_abandoned_total`, `bsl_reassembly_hash_mismatch_total`.

**→ [BRC-130 Fragmentation](docs/brc-130-fragmentation.md)** — header layout,
fragDataSize derivation, error handling, constants reference

---

## Transaction Coalescing (BRC-142)

BRC-142 is the inverse of BRC-130: instead of splitting one oversized
transaction across many datagrams, it packs many small transactions destined
for the same shard group and subtree into a single bundle datagram (Frame
Version `0x08`). The motivation is packets-per-second — the dominant
data-plane forwarding cost on the replicated fabric and per-tunnel egress
hops; a payload that would cross the fabric as N small packets crosses it as
one.

A bundle cannot reuse the BRC-124 header layout (there is no single TxID for N
transactions), so it carries a dedicated 66-byte header: magic/version, a
shared `SubtreeID`, a single per-`(sender, group, subtree)` `HashKey`/`SeqNum`,
`GroupIdx` + `ShardBits` (pinning the shard generation), `TxCount`, and
`PayloadLen`. Members are length-prefixed transactions (`TxLen` + optional
32-byte TxID + raw tx bytes); standard and EF members self-identify and may be
mixed.

The reference proxy coalesces at the **origin** only (opt-in `-coalesce`,
default off), packing eligible frames within one receive batch and flushing at
batch end — no timer, near-zero added latency. A bundle never exceeds the path
MTU (a transaction that would need BRC-130 is never a member; the two are
mutually exclusive per datagram). Relays re-emit bundles verbatim. NACK
retransmission (BRC-126) operates at bundle granularity: the retry endpoint
caches a bundle as one opaque frame keyed `HashKey ∥ SeqNum`.

Listeners **edge-decoalesce** by default — split the bundle back into
individual frames before per-consumer egress, so the consumer contract is
unchanged; whole-bundle (consumer-decoalesce) delivery is opt-in. A bundle
built at a different `ShardBits` generation is re-bucketed to the local
generation at the delivery edge before delivery. Malformed or truncated
bundles are dropped and counted, not silently discarded
(`bundle_short`/`bundle_malformed`/`bundle_decode_error`).

**→ [BRC-142 Coalescing (Bundle) Frame Format](docs/brc-142-coalescing-frame.md)**
— bundle header layout, member format, MTU sizing, bundle-unit NACK,
re-bucketing rules, deployment decisions

---

## Subtree Group Announcement (BRC-127)

BRC-127 defines the dynamic subtree group announcement protocol. Producers
advertise which SubtreeIDs belong to which logical group by sending 64-byte
`SubtreeAnnounce` datagrams (`MsgType 0x30`) to the proxy TCP ingress. The proxy
forwards these verbatim to the control-plane multicast group
(`GroupSubtreeGroupAnnounce = 0xFFFC`). Listeners join this group and populate a
`subtreegroup.Registry`, automatically accepting frames from announced subtrees
without static configuration.

Announcements must be re-sent before their TTL expires (recommended: interval
10–30 s; TTL ≥ 3× interval). If announcements cease, entries expire and frames
are dropped.

**→ [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md)** —
wire format, listener configuration flags, distribution path, refresh/expiry
rules

---

## Block Announcement Frame Format (BRC-131)

BRC-131 defines a dedicated frame version (`FrameVer 0x04`) for distributing
block-level metadata to all fabric subscribers. Two message types are defined:

- **BlockAnnounce (`MsgType 0x01`)** — carries the 80-byte block header, the
  CoinbaseTxID, and an ordered list of subtree root hashes included in the
  block. Subscribers use this to update block templates and validate received
  transactions against the new chain tip.
- **CoinbaseTx (`MsgType 0x02`)** — carries the raw coinbase transaction bytes.
  The ContentID in the frame header is the SHA256d of the coinbase transaction.

Both types share the 92-byte BRC-124 header layout and are delivered on the
**GroupBlockBroadcast** group (`FF0E::B:FFFE`), ensuring global reach
independent of shard assignment. Every subscriber receives every block
announcement — there is no shard or subtree filtering for block frames.

Sequence tracking and NACK-based retransmission work identically to BRC-124: the
proxy stamps `HashKey` and `SeqNum` in-place, listeners track the control flow
for gaps, and retry endpoints cache and retransmit V4 frames back to the control
group (not to a shard group — a critical routing distinction).

For payloads exceeding the path MTU (uncommon for typical block announcements
but relevant for large coinbase transactions), the proxy uses BRC-130
fragmentation with `OrigFrameVer=0x04` in the fragment header.

**→
[BRC-131 Block Announcement Frame Format](docs/brc-131-block-announcements.md)**
— frame header layout, BlockAnnounce payload format, CoinbaseTx payload, gap
tracking on the control flow, fragmentation rules, proxy/listener/retry-endpoint
changes

---

## Subtree Data Frame Format (BRC-132)

BRC-132 defines Frame Version `0x05` for distributing complete subtree data
payloads — the transaction hashes (and optionally fee/size metadata) that make
up a Merkle subtree — over the multicast fabric. This fills the gap between
individual transaction distribution (BRC-124) and block-level metadata
(BRC-131), enabling subscribers to reconstruct subtree Merkle trees locally and
verify block inclusion without fetching individual transactions.

Two message types are defined:

- **HashesOnly (`MsgType 0x01`)** — 32-byte transaction hash per node, plus a
  24-byte metadata prefix (TotalFees, TotalSizeBytes, NodeCount) and a conflict
  set.
- **FullNodes (`MsgType 0x02`)** — 48-byte entry per node (TxHash + Fee + Size),
  same prefix and conflict set.

Both types are delivered on the **GroupSubtreeDataAnnounce** group (`FF0X::B:FFFB`).
BRC-127 subtree group announcements use a separate group
(`GroupSubtreeGroupAnnounce`, `FF0X::B:FFFC`).

The 92-byte header is layout-identical to BRC-124. `HashKey` is computed as
`XXH64(senderIPv6 ∥ 0xFFFB ∥ subtreeID)` so each (sender, subtreeID) pair owns
an independent sequence stream. Because payloads range from ~32 MB (HashesOnly,
1M nodes) to ~48 MB (FullNodes, 1M nodes), BRC-130 fragmentation is always
required in practice; the proxy sets `OrigFrameVer=0x05` in each fragment
header. Listener reassembly is keyed by SubtreeID; SHA256d hash verification is
skipped (SubtreeID is a Merkle root, not a payload double-hash). Optional
post-reassembly Merkle-root recomputation is available via
`-subtree-data-verify-merkle`.

Sequence tracking and NACK retransmission are identical to BRC-124 and BRC-131:
the retry endpoint joins `FF0X::B:FFFB`, caches BRC-132 frames and their BRC-130
fragments, and retransmits to `FF0X::B:FFFB` on NACK (not to a shard group).

**→ [BRC-132 Subtree Data Frame Format](docs/brc-132-subtree-data.md)** — frame
header layout, MsgType payload formats, fragmentation rules, Merkle
verification, proxy/listener/retry-endpoint changes, error handling, constants
reference

---

## Coinbase Transaction Frame Format (BRC-133)

BRC-133 formalizes the wire mechanism for distributing coinbase transactions as
a dedicated message type (`BlockMsgCoinbase = 0x02`) within BRC-131 block
control frames (FrameVer `0x04`). Coinbase transactions are delivered to all
subscribers via the **GroupBlockBroadcast** group (`FF0E::B:FFFE`), independent
of shard assignment.

The 92-byte header is identical to the BRC-124 / BRC-131 layout. The ContentID
field (bytes 8–39) carries the CoinbaseTxID (SHA256d of the coinbase
transaction). The proxy stamps `HashKey` as `XXH64(senderIPv6 ∥ 0xFFFE ∥ zeros)`
and a monotonic `SeqNum` in-place. The raw BSV-serialised coinbase transaction
(no P2P envelope) is carried as the payload.

Sequence tracking and NACK retransmission are identical to BRC-131: the retry
endpoint joins `FF0E::B:FFFE`, caches FrameVerV4 frames with `MsgType=0x02`, and
retransmits to `FF0E::B:FFFE` on NACK.

**→
[BRC-133 Coinbase Transaction Frame Format](docs/brc-133-coinbase-delivery.md)**
— header layout, MsgType constants, proxy/listener/retry-endpoint changes,
sequencing rules

---

## Anchor Transaction Frame Format (BRC-134)

BRC-134 defines Frame Version `0x06` for distributing chained anchor
transactions over the multicast fabric. An _anchor transaction_ is the root
(first) transaction in a chain of dependent transactions; because all subsequent
transactions reference it as an input, every subscriber must receive it
regardless of which shard its TxID would otherwise hash to.

Anchor frames are delivered on the **GroupBlockBroadcast** group
(`FF0E::B:FFFE`), the same global control channel used for BRC-131 block
announcements and BRC-133 coinbase transactions.

The 92-byte header is layout-identical to BRC-124 with Frame Version `0x06` at
offset 6. The TxID field (bytes 8–39) carries the SHA256d of the anchor
transaction. `HashKey` is stamped as `XXH64(senderIPv6 ∥ 0xFFFE ∥ zeros)` by the
proxy; `SeqNum` is a monotonic per-sender counter. The raw BSV-serialised anchor
transaction is carried as the payload (no P2P envelope). BRC-130 fragmentation
is not defined for BRC-134.

Sequence tracking and NACK retransmission are identical to BRC-131 and BRC-133:
the retry endpoint joins `FF0E::B:FFFE`, caches FrameVerV6 frames, and
retransmits to `FF0E::B:FFFE` on NACK.

**→
[BRC-134 Anchor Transaction Frame Format](docs/brc-134-anchor-transactions.md)**
— header layout, FrameVerV6 constant, proxy/listener/retry-endpoint changes,
sequencing rules

---

## Block Header Format (BRC-135)

BRC-135 defines Frame Version `0x07` for distributing standalone 80-byte BSV
block headers as a lightweight derivative of BRC-131 `BlockAnnounce` frames. Any
node that receives a `BlockAnnounce` can act as an _emitter_: it extracts the
80-byte header from the announce payload, wraps it in a 172-byte BRC-135 frame
(92-byte header + 80-byte payload), stamps its own `HashKey`/`SeqNum`, and sends
it to downstream consumers via unicast or multicast egress.

BRC-135 frames are emitter-originated and are NOT re-injected onto the primary
fabric (`FF0E::B:FFFE`). They target downstream consumers that need only block
headers (SPV clients, header-chain validators, mining coordinators). At 172
bytes total, no fragmentation is required. BRC-135 frames are not covered by the
primary-fabric BRC-126 NACK path; recovery relies on redundant emitters,
upstream BRC-131 NACK retransmission, or application-level retry.

**→
[BRC-135 Multicast Block Header Format](docs/brc-135-block-header-format.md)** —
header layout, FrameVerV7 constant, payload format, sequencing, retransmission
strategy

---

## Shard Manifest Announcement (BRC-139)

BRC-139 defines a periodic announcement datagram (MsgType `0x40`, a 64-byte
header plus variable payload) that each participant emits to the beacon group
(`FF0X::B:FFFD`, `GroupBeacon`/`0xFFFD`). It carries the announcer's
`ShardBits`, joined-group set (list or bitmap form), `GenerationID`, and role
hint. BRC-139 datagrams do not carry a BRC-124 frame header, are not
proxy-stamped, are not retransmitted, and are never ACKed.

The `shard-manifest` daemon is the canonical announcer; any participant (proxy,
listener, retry-endpoint, producer) MAY also self-announce its own
configuration. Consumers detect cross-peer divergence and, when opted in via
[Automatic Shard Configuration](#automatic-shard-configuration), adopt
`Authoritative=1` values after a quorum + hysteresis gate.

**→ [BRC-139 Shard Manifest Announcement](docs/brc-139-shard-manifest.md)** —
wire format, flags, encoding-form rules, beacon-group routing, observer and
auto-config consumer profiles

---

## Source-Specific Multicast (SSM)

The fabric runs in either Source-Specific Multicast (SSM, **the default and
first-class mode**) or Any-Source Multicast (ASM, a **lab/dev fallback** —
`sourceMode: asm`). SSM is required for **inter-domain** operation (RFC 8815
forbids inter-domain ASM) and gives RP-less, loop-free source trees; it is the
default across the Helm charts and integrated-infra. ASM is retained only for
smcroute collapsed-unicast labs (and the bare
binary CLI default, since a bare invocation is a lab/dev scenario). SSM vs ASM is
a deployment/transport mode only — frame format, NACK protocol, HashKey
computation, and shard derivation are unchanged. Under SSM, receivers
`(S,G)`-join the publisher roster (`ssm_publishers_static`) instead of `(*,G)`,
each emitter binds a routable per-node source (`bind_source`), and the fabric
uses the RFC 4607 SSM address range.

### Addressing

The address space is selected by `(sourceMode, scope)`. RFC 8815 deprecates
inter-domain ASM, so global scope is SSM-only.

| Mode | Site scope (intra-domain) | Global scope (inter-domain) |
| ---- | ------------------------- | --------------------------- |
| ASM  | `FF05::B:idx`             | Not supported (RFC 8815)    |
| SSM  | `FF35::B:idx`             | `FF3E::B:idx`               |

`FF3x::/32` is the RFC 4607 IPv6 SSM range. Group-ID (`0x000B`) and the
shard-index field are preserved; only the high 32 bits change. A single
`engine.Addr(groupIdx, port, mode, scope)` helper centralizes derivation.

### Key properties

- **Distinct source IP per publisher** (`bindSource`, set on every emitter).
  Required by PIM-SSM RPF and preserves the per-publisher HashKey flow
  semantics. Anycast/shared-source deployments are not supported; for a single
  stable identity use VRRP active-standby (failover, not load distribution).
- **Source discovery.** Data-plane sources flow exclusively through
  shard-manifest (BRC-139 `Flags.SourcesValid`); receivers set
  `sources.consume: [manifest]`. Control groups (beacon, manifest,
  subtree-announce) are joined against per-group bootstrap source lists
  (`sources.bootstrap.*`, IPv6 literals or DNS names re-resolved on refresh).
- **Receiver joins** use `MCAST_JOIN_SOURCE_GROUP` (RFC 3678) via a shared
  `netjoin` helper that diffs and rate-limits join/leave churn.

### Deployment postures

Four self-consistent network + config states. **C is recommended for new
intra-domain deployments; D for inter-domain.** AutoShardConfig works across all
four.

| Posture | Data plane | Control plane | Fabric                         | Inter-domain  |
| ------- | ---------- | ------------- | ------------------------------ | ------------- |
| A       | ASM        | ASM           | PIM-SM + RP                    | No (RFC 8815) |
| B       | SSM        | ASM           | PIM-SM (RP) **and** PIM-SSM    | Data only     |
| C       | SSM        | SSM           | PIM-SSM only (no RP/MSDP)      | Intra only    |
| D       | SSM        | SSM, global   | PIM-SSM + inter-domain peering | Yes           |

At the target scale (hundreds of publishers/listeners), SSM requires raising
`net.ipv6.mld_max_msf`, deterministic per-pod IPv6 (Multus + Whereabouts),
fabric mfib sizing, and join-rate limiting in `netjoin`.

---

## Automatic Shard Configuration

shard-proxy and shard-listener can opt in to consuming BRC-139 manifests
(`multicast.autoConfig.enabled=true`) and adopting the announced `ShardBits` /
`SourceModeSSM` after a quorum + hysteresis gate. Manual CLI/env pins always
win. It works identically under ASM and SSM and across all four deployment
postures (the beacon-socket join is ASM under A/B, SSM under C/D). When disabled
(the default), manual configuration is used and behavior is unchanged.

A shared `shard-common/manifest/` package holds the registry, adoption gates,
and source-set union; proxy and listener wrap it with their own apply semantics.
shard-manifest is the sole authority — data-plane components are consumers only.

### Adoption modes

- **Restart (default, `liveResharding=false`).** A `ShardBits` / `SourceModeSSM`
  change flips `/readyz`, drains, then exits non-zero; the orchestrator rolls
  the pod, which restarts with the adopted value warm in the registry.
- **Live re-sharding (opt-in, `liveResharding=true`).** A re-shard is a
  generation transition signalled by a BRC-139 `Successor` block carrying the
  incoming `ShardBits` (constrained to ±1) and a `TransitionEpoch`. During the
  bridging window the proxy dual-emits each frame to both the current and
  successor layouts and listeners union-join both; downstream TxID dedup absorbs
  the duplicates. At `TransitionEpoch` the consumer atomically swaps to the
  successor and leaves the now-unused groups — no restart, `/readyz` stays
  green. Requires egress dedup sized to ≥ 2× the bridging window.

### Listener auto-join

With `autoJoinFromManifest=true`, a listener's effective subscription is
`union(-shard-include, pilot_groups)`, where `pilot_groups` is the union of
authoritative `Flags.GroupsValid` payloads. Static `-shard-include` entries are
never leaved; pilot-added groups are leaved only when no pilot still claims
them.

**→ [BRC-139 Shard Manifest Announcement](docs/brc-139-shard-manifest.md)** —
the normative wire format, adoption gates, and Successor-block layout this
behavior implements.

---

## Testing and Validation

### subtx-generator

**Purpose:** Random BSV-shaped frame generator for load and functional testing.

**Features:** random BSV-shaped payloads, deterministic subtree ID pool,
optional gap injection (`-seq-gap-every`, `-seq-gap-delay`) for NACK/retransmit
tests, multi-core token-bucket pacer.

**→ [subtx-generator README](https://github.com/lightwebinc/subtx-generator)** —
usage examples, flags

### shard-listener E2E Tests

**Purpose:** Self-contained end-to-end tests for listener functionality.

**Approach:** Inject frames as unicast UDP directly to listener's bound port
(`[::1]:listen-port`), bypassing proxy and multicast fabric. This avoids Linux
loopback multicast reliability issues on CI.

**Test Scenarios:**

1. Basic delivery (all frames, metric verification)
2. Shard filter (single shard acceptance)
3. Strip-header (payload-only forwarding)

**Execution:**

```bash
cd shard-listener
make test-e2e
```

**Documentation:**
[shard-listener README](https://github.com/lightwebinc/shard-listener)

### Integration Test Scenarios (multicast-test)

**Purpose:** Full-stack integration testing across all components.

The [multicast-test](https://github.com/lightwebinc/multicast-test) repository
is the public integration suite: a **Go Docker harness** (`harness/`) of ~45
scenario tests driven by `go test`. Each scenario spawns ephemeral Docker
containers on an isolated IPv6 multicast bridge (`fd10::/64`) and covers
functional filters, NACK retransmission, fragmentation, BRC-127 group
announcements, BRC-131/132/134 control-plane delivery, SSM rollout, unified
logging, TxID dedup with Redis, and rate-limit defenses. See the repo's
[`SCENARIOS.md`](https://github.com/lightwebinc/multicast-test/blob/main/SCENARIOS.md)
for the full index.

Deployment / applied-infrastructure testing (the LXD VM lab, privileged netns
mesh repros, and real-host deployment tooling) is maintained separately from
this public suite.

**Getting Started:**

```bash
cd multicast-test
make test          # all scenarios (~30 min, requires Docker + sudo)
make test-quick    # tier-1 filter scenarios (~60s)
make help          # show all targets
```

---

## Deployment Considerations

### Platform Support

| OS                   | Service Manager | Network Config     | Proxy | Listener | Retry | Manifest |
| -------------------- | --------------- | ------------------ | ----- | -------- | ----- | -------- |
| Ubuntu 24.04         | systemd         | Netplan / ip       | ✓     | ✓        | ✓     | ✓        |
| FreeBSD 14           | rc.d            | rc.conf / ifconfig | ✓     | ✓        | ✓     | ✓        |
| AWS EC2              | systemd         | ENI + Terraform    | ✓     | ✓        | ✓     | ✓        |
| Kubernetes (k0s ref) | kubelet         | Multus macvlan     | ✓     | ✓        | ✓     | ✓        |

Kubernetes deployment is provided by
[multicast-kube-infra](https://github.com/lightwebinc/multicast-kube-infra),
which composes the per-service Helm charts (`shard-proxy-helm`,
`shard-listener-helm`, `retry-endpoint-helm`, `subtx-generator-helm`,
`shard-manifest-helm`).

For a single-host footprint,
[integrated-infra](https://github.com/lightwebinc/integrated-infra) deploys a
**collapsed node** — `shard-proxy`, `shard-listener`, and `retry-endpoint`
co-located on one multi-homed host (uplink for sender ingress, multicast-fabric
interface for IPv6 multicast in/out). It targets Ubuntu 24.04, FreeBSD 14, AWS
EC2, and any SSH host via the same Ansible/Terraform automation as the
per-service infra repos.

### Networking Requirements

**Ingress (shard-proxy):**

- IPv6 enabled on egress interface(s)
- Multicast routing / MLD snooping configured for subscriber fabric
- Optional: GRE tunnel for cloud VMs
- Optional: eBGP for nearest-node routing

**Listener (shard-listener):**

- IPv6 enabled on ingress interface
- MLDv1/v2 support for multicast group join
- Optional: BGP for listener reachability into fabric
- Firewall: multicast-fabric perimeter (default-on in listener-infra)

**Retry Endpoint (retry-endpoint):**

- IPv6 enabled on multicast interface
- Optional: Redis or Aerospike for shared cache (multi-node deployments)

### Firewall Configuration

**Proxy (ingress-infra):**

- Allow UDP/TCP transaction ingress on the user listen port (default 8725)
  from all consumers
- If a miner ingress is enabled (`-miner-listen-port`, e.g. 9000), allow it
  **only** from the miner-tier source set (tunnels / firewall allowlist) — it
  accepts privileged block/coinbase/subtree-data frames. See
  [§ Ingress Authorization](#ingress-authorization-miner-tier-gate).
- Allow IPv6 multicast egress on egress interface

**Listener (listener-infra):**

- **Multicast-fabric perimeter:** Built-in firewall enforces:
  - Ingress: Only multicast data on ingress interface
  - Egress: Only NACK datagrams outbound
  - All other traffic dropped
- See
  [listener-infra security docs](https://github.com/lightwebinc/listener-infra/blob/main/docs/security.md)

**Retry Endpoint (retransmission-infra):**

- Simplified UDP-only firewall
- Allow NACK ingress on port 9300
- Allow multicast egress on egress interface

### BGP Integration

**Ingress (ingress-infra):**

- Optional eBGP on ingress interface
- Announce shared prefixes from all proxy nodes
- Senders routed to nearest proxy via BGP best-path selection
- See
  [ingress-infra BGP docs](https://github.com/lightwebinc/ingress-infra/blob/main/docs/bgp.md)

**Listener (listener-infra):**

- Optional BGP for listener reachability into fabric
- Advertise listener's own unicast prefix
- Enables MLD/PIM distribution trees in L3 fabrics
- See
  [listener-infra BGP docs](https://github.com/lightwebinc/listener-infra/blob/main/docs/bgp.md)

**Retry Endpoint (retransmission-infra):**

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

- Deploy multiple retry endpoints with a shared Redis or Aerospike cache
- Cross-instance deduplication prevents duplicate retransmissions
- Rate limiting protects against NACK storms

### Monitoring and Metrics

All services expose Prometheus metrics on dedicated ports:

| Service        | Metrics Port | Prefix |
| -------------- | ------------ | ------ |
| shard-proxy    | :9100        | bsp\_  |
| shard-listener | :9200        | bsl\_  |
| retry-endpoint | :9400        | bre\_  |
| shard-manifest | :9091        | bsm\_  |

Key signals: `bsp_packets_dropped_total`, `bsl_gaps_detected_total`,
`bsl_gaps_unrecovered_total`, `bre_cache_misses_total`,
`bre_rate_limit_drops_total`, `bsm_announcements_sent_total`. See each
component's docs for full metric reference.

### Graceful Shutdown

All services handle SIGINT/SIGTERM identically: set draining flag (`/readyz` →
503), optional drain timeout, close ingress sockets, wait for in-flight
processing, flush OTLP exporter.

---

## References and Further Reading

### Source Documentation

**Protocol:**

- [Wire Protocol Specification](https://github.com/lightwebinc/shard-common/blob/main/docs/protocol.md)
  — Complete BRC-12/BRC-124/BRC-128 frame format
- [BRC-124 Frame Format](docs/brc-124-frame-format.md) — 92-byte header,
  HashKey/SeqNum per-flow sequencing, backward compatibility
- [BRC-126 Retransmission Protocol](docs/brc-126-retransmission-protocol.md) —
  NACK/ACK/MISS wire formats, ADVERT beacon, tier/preference model
- [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md) —
  SubtreeAnnounce wire format, proxy forwarding, listener integration
- [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md) — EF payload
  format, detection, infrastructure impact
- [BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md)
  — IPv6 address scheme, control-plane indices, beacon groups
- [BRC-130 Fragmentation](docs/brc-130-fragmentation.md) — fragment header
  layout, fragDataSize, per-fragment NACK, reassembly algorithm, metrics
- [BRC-131 Block Announcement Frame Format](docs/brc-131-block-announcements.md)
  — block frame header, BlockAnnounce + CoinbaseTx payloads, control-group
  routing, proxy/listener/retry-endpoint changes
- [BRC-132 Subtree Data Frame Format](docs/brc-132-subtree-data.md) — frame
  header layout, HashesOnly/FullNodes payload formats, fragmentation rules,
  Merkle verification, proxy/listener/retry-endpoint changes
- [BRC-133 Coinbase Transaction Frame Format](docs/brc-133-coinbase-delivery.md)
  — coinbase frame wire format, MsgType constants, proxy/listener/retry-endpoint
  changes
- [BRC-134 Anchor Transaction Frame Format](docs/brc-134-anchor-transactions.md)
  — anchor frame wire format, FrameVerV6, proxy/listener/retry-endpoint changes
- [BRC-135 Multicast Block Header Format](docs/brc-135-block-header-format.md) —
  standalone block header split, FrameVerV7, emitter-originated sequencing
- [BRC-139 Shard Manifest Announcement](docs/brc-139-shard-manifest.md) —
  periodic participant configuration announcement (shard_bits, joined groups,
  GenerationID); beacon-group distribution
- [BRC-142 Coalescing (Bundle) Frame Format](docs/brc-142-coalescing-frame.md)
  — bundle header layout, member format, MTU sizing, bundle-unit NACK,
  re-bucketing rules
- [NACK Retransmission Flow](docs/nack-retransmission-flow.md) — End-to-end
  pipeline diagrams, escalation state machine, flood prevention

**Services:**

- [shard-proxy Architecture](https://github.com/lightwebinc/shard-proxy/blob/main/docs/architecture.md)
- [shard-listener Architecture](https://github.com/lightwebinc/shard-listener/blob/main/docs/architecture.md)
- [retry-endpoint Architecture](https://github.com/lightwebinc/retry-endpoint/blob/main/docs/architecture.md)

**Infrastructure:**

- [ingress-infra Architecture](https://github.com/lightwebinc/ingress-infra/blob/main/docs/architecture.md)
- [listener-infra Architecture](https://github.com/lightwebinc/listener-infra/blob/main/docs/architecture.md)
- [retransmission-infra Architecture](https://github.com/lightwebinc/retransmission-infra/blob/main/docs/architecture.md)

### Conceptual Attribution

The IPv6 multicast transaction broadcast architecture from which this software
draws inspiration was articulated by Dr. Craig S. Wright:

- [Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast)
- [Multicast as the Only Viable Architecture](https://singulargrit.substack.com/p/multicast-as-the-only-viable-architecture)
- [Singulargrit Substack](https://singulargrit.substack.com/)

### Standards

#### BRC-12: Raw Transaction Format

- The BRC-12 wire-frame format transports transactions conforming to BRC-12
- [BSV Blockchain Standards Repository](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0012.md)

#### BRC-30: Extended Format (EF) Transaction

- The payload format for BRC-128 frames
- [BSV Blockchain Standards Repository](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0030.md)

---

## Appendix: Quick Reference

### Default Ports

| Service                     | Port         | Protocol | Purpose                                      |
| --------------------------- | ------------ | -------- | -------------------------------------------- |
| shard-proxy (UDP ingress)   | 8725         | UDP      | User/tx frame ingress                        |
| shard-proxy (TCP ingress)   | configurable | TCP      | Reliable frame ingress (disabled by default) |
| shard-proxy (miner ingress) | configurable | UDP/TCP  | Privileged miner-tier ingress (`-miner-listen-port`, e.g. 9000; disabled by default) |
| shard-proxy (egress)        | 9001         | UDP      | Multicast egress                             |
| shard-listener (multicast)  | 9001         | UDP      | Multicast receive                            |
| shard-listener (NACK)       | 9300         | UDP      | NACK send                                    |
| retry-endpoint (multicast)  | 9001         | UDP      | Multicast receive                            |
| retry-endpoint (NACK)       | 9300         | UDP      | NACK receive                                 |
| retry-endpoint (retransmit) | 9001         | UDP      | Retransmission egress                        |

### Metrics Ports

| Service        | Port | Endpoint                          |
| -------------- | ---- | --------------------------------- |
| shard-proxy    | 9100 | `/metrics`, `/healthz`, `/readyz` |
| shard-listener | 9200 | `/metrics`, `/healthz`, `/readyz` |
| retry-endpoint | 9400 | `/metrics`, `/healthz`, `/readyz` |
| shard-manifest | 9091 | `/metrics`, `/healthz`, `/readyz` (`bsm_` prefix) |

### Default AS Numbers

| Service               | AS    |
| --------------------- | ----- |
| ingress-infra (proxy) | 65001 |
| listener-infra        | 65002 |

### Frame Version Summary

| Version | Header Size | Flow Sequencing      | Subtree Support          |
| ------- | ----------- | -------------------- | ------------------------ |
| BRC-12  | 44 bytes    | No                   | No                       |
| BRC-124 | 92 bytes    | Yes (HashKey/SeqNum) | Yes                      |
| BRC-128 | 92 bytes    | Yes (HashKey/SeqNum) | Yes (EF payload)         |
| BRC-130 | 104 bytes   | Yes (per-fragment)   | Yes (fragmented)         |
| BRC-131 | 92 bytes    | Yes (HashKey/SeqNum) | No (ctrl-plane)          |
| BRC-132 | 92 bytes    | Yes (per-subtree)    | No (ctrl-plane)          |
| BRC-133 | 92 bytes    | Yes (HashKey/SeqNum) | No (ctrl-plane coinbase) |
| BRC-134 | 92 bytes    | Yes (HashKey/SeqNum) | No (ctrl-plane anchor)   |
| BRC-135 | 92 bytes    | Yes (emitter-stamped HashKey/SeqNum) | No (ctrl-plane header egress) |
| BRC-142 | 66 bytes (bundle) | Yes (per-bundle HashKey/SeqNum) | Yes (members share one group + subtree) |
