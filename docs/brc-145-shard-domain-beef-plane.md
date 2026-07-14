# BRC-145: Multicast Shard Domain Partitioning and the BEEF Object Plane

Jeff Harris (jeff@lightweb.net)

> **Status:** Draft — provisional number, not yet submitted to the
> [BRCs repository](https://github.com/bitcoin-sv/BRCs). Extends
> [BRC-129](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md)
> (addressing) and forward-extends
> [BRC-139](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0139.md)
> (shard manifest).

## Abstract

This BRC extends the IPv6 multicast group address scheme of
[BRC-129](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md) by
partitioning the 16-bit shard-index space into independent **object planes**,
each identified by a 4-bit domain selector in the high nibble of the index. The
existing transaction plane (raw and Extended Format transactions) is retained
unchanged as domain `0x0`; a new **BEEF object plane** is allocated as domain
`0x1` to carry
[BRC-62](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0062.md),
[BRC-95](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0095.md), and
[BRC-96](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0096.md)
BEEF-family transaction objects for peer-to-peer and overlay synchronisation.
The BEEF plane shards by **overlay topic**: each object carries a topic
identifier from which its multicast group is derived by the BRC-129 top-bits
rule, and overlay-tier subscribers filter delivery by elected topic and by BEEF
version (encoding). Each plane subscribes and publishes independently and may
run its own shard-bit width. This BRC also
forward-extends the BRC-139 shard-manifest protocol with a
per-domain descriptor section so each plane can advertise and coordinate its own
`shard_bits` and generation transitions without disturbing the others. The
transaction plane's on-wire addresses, frames, and manifests are byte-identical
to their pre-BRC-145 form; this is a strictly additive extension.

## Summary (non-normative)

*This section restates the design in plain language. The Specification below is
authoritative.*

The multicast network today works like a set of numbered broadcast channels:
every new Bitcoin transaction is announced on one of up to 4,096 channels, and
receivers such as miners tune into the channels they want instead of drinking
from one giant firehose. This document adds a **second, independent set of
channels** for a different kind of traffic — BEEF transactions, the
proof-carrying package format that apps and wallets exchange — without changing
anything about the existing transaction channels. In the 16-bit channel
numbering, the first hex digit now names the *plane* (`0` = transactions,
`1` = BEEF) and the rest names the channel within it.

The important design choice is **what decides the channel**. On the transaction
plane it is the transaction's own ID, which spreads traffic evenly — right for
miners, who want everything. On the BEEF plane it is the **topic**: the name of
the app community a transaction belongs to (for example `tm_uhrp_files`). A
topic name mathematically maps to exactly one channel, so an application host
interested in a topic tunes into just that one channel and ignores the rest of
the network's traffic. Today, an overlay publisher must find every interested
host and send each one a copy over HTTPS; on this plane it publishes once and
every subscribed host in the world receives it, with lost packets automatically
re-requested.

Within a channel, the delivery edge applies two per-customer filters before
forwarding: only the **topics** that customer asked for, and only the BEEF
**encodings** it can process (there are three; the encoding marker is not an
app identifier and is never used as one). So the network does coarse sorting by
channel, and the edge does exact sorting per customer.

Why this scales to millions of overlays: the network itself never keeps a list
of topics. There is no registry, nothing to provision — any topic name hashes
to a channel, the way a word hashes to a dictionary page. The only places that
remember topics are the delivery edges, and each remembers only the topics its
own paying customers asked for. Channel counts, router state, and
retransmission bookkeeping all stay bounded no matter how many topics exist,
and the BEEF plane has reserved room to grow eight-fold (to 32,768 channels)
without renumbering.

Two deliberate limits: this plane carries the **live feed**, not history — a
host joining a topic catches up through the existing overlay sync protocols,
then stays current here; and it does **not** get transactions mined — anyone
who needs settlement also submits to the transaction plane, exactly as before.

## Copyright

This BRC is licensed under the Open BSV License.

## Motivation

[BRC-129](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md)
allocates the bottom 32 bits of the IANA Bitcoin multicast allocation
(`FF0X::B`) as a 16-bit group-id (default `0x000B`) followed by a 16-bit shard
index. It caps `shard_bits` at 12 so that all transaction shard groups occupy
`0x0000`–`0x0FFF`, and it reserves `0x1000`–`0xF7FF` as free space "for future
expansion ... as well as specialty transmission domains for purpose-specific
multicast services that are not general-purpose transaction sharding."

BEEF-family transactions are exactly such a purpose-specific service. Unlike the
transaction plane — whose consumers are miners and settlement infrastructure
requiring raw or Extended Format transactions — BEEF objects carry ancestry and
Merkle-path proofs
([BRC-74](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0074.md))
for **peer-to-peer and overlay synchronisation**: SPV wallets, overlay services,
and application-layer indexers that validate without a UTXO set. The two use
cases do not overlap. A BEEF object is never a substitute for the settlement
transaction on the transaction plane, and the transaction plane never carries
proof ancestry. They are distinct planes of operation with distinct classes of
both publishers and subscribers.

Overlay networks identify themselves by **topic**. A
[BRC-22](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0022.md)
submission is a BEEF object plus a list of topic names; per-topic managers
decide admittance;
[BRC-87](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0087.md) names
topics (`tm_*`); and under
[BRC-88](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0088.md) a
broadcaster discovers the hosts interested in a topic via SHIP advertisements
and then propagates the object to each host point-to-point over HTTPS. That
per-host fan-out is the step that does not scale as topics and hosts multiply.
This BRC gives that propagation step a multicast substrate: a publisher emits a
topical BEEF object once, and every subscribed host of that topic receives it,
globally, with multicast-native retransmission. History bootstrap remains the
province of the existing synchronisation protocols (see Operational
Considerations).

Carrying BEEF objects on their own multicast plane — rather than translating them
into the transaction plane — keeps the fabric stateless and lossless: no proof
data is stripped, no per-input UTXO resolution is required, and subscribers
receive exactly the format they requested. It also establishes the addressing and
coordination pattern for the broader application/overlay layer, which is expected
to scale independently of, and potentially beyond, the transaction plane's shard
count.

This BRC specifies:

1. A backward-compatible **domain partition** of the 16-bit shard-index space so
   that multiple independent object planes coexist without colliding with each
   other, with the transaction plane, or with the control plane.
2. The allocation of the **BEEF object plane** (domain `0x1`) with its topical
   sharding, publication, and filtered-delivery rules.
3. A **plane growth policy** that reserves each plane a contiguous runway so it
   can widen its shard-bit space over time without re-basing its addresses.
4. A **per-domain extension to the BRC-139 shard manifest** so each plane
   advertises and live-reshards its own `shard_bits` and generation
   independently.

## Specification

### Relationship to BRC-129

This BRC supersedes the
[BRC-129](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md)
section *"Free Space and Specialty Transmission Domains"* and refines the
*"Data-Plane Shard Groups"* derivation. All other BRC-129 provisions — the IANA
allocation, the group-id field (bytes `[12:14]`, default `0x000B`), the
source-mode / address-range rules (ASM `FF0x` vs SSM `FF3x`), the group-id
override, and the control-plane assignments `0xF800`–`0xFFFF` — remain in force
unchanged. Domain `0x0` under this BRC is bit-for-bit identical to a BRC-129
deployment; a participant that implements only BRC-129 interoperates fully with
the transaction plane of a BRC-145 deployment.

Arithmetic correction: BRC-129 states the free band `0x1000`–`0xF7FF` contains
"56,832 indices." The correct count is `0xF7FF − 0x1000 + 1 = 59,392`
(equivalently `65536 − 4096 transaction − 2048 control`). This BRC uses the
corrected figure.

### Domain-Partitioned Shard Index

The 16-bit shard-index field (address bytes `[14:16]`, "IDX") is partitioned into
a 4-bit **domain selector** in the high nibble and a shard index in the low bits:

```text
IDX[15:0] =  D D D D | S S S S S S S S S S S S
             └domain┘   └──── shard index ────┘   (shard_bits ≤ 12 ⇒ within low 12 bits)
```

Because the transaction plane bounds `shard_bits ≤ 12`, its shard index never
occupies the high nibble, which is therefore always `0x0` for domain `0x0` —
i.e. the pre-existing `0x0000`–`0x0FFF` range. A non-zero domain selects a
distinct plane whose base address is offset by the domain value.

#### Plane base and address derivation

Each plane is assigned a **plane base** aligned to a `0x1000` boundary:

```text
planeBase(domain) = uint16(domain) << 12          // 0x0000, 0x1000, 0x2000, …
```

Each plane defines a 32-byte **shard key** for its objects: the transaction
plane keys on the transaction ID; the BEEF plane keys on the topic identifier
(see *Topical sharding* below). The group index is the unmodified BRC-129
top-bits derivation applied to the plane's shard key at the plane's own
shard-bit width, and the plane base is added to form the 16-bit IDX:

```text
shardIndex = (binary.BigEndian.Uint32(shardKey[0:4]) >> (32 - shardBits)) & (2^shardBits - 1)
IDX        = planeBase(domain) + shardIndex
address    = [MCPrefix][0x00 × 10][MCGroupID][IDX]
```

For domain `0x0` (shard key = TxID, `shard_bits ≤ 12`), `planeBase` is `0x0000`
and the formula reduces exactly to BRC-129. The addition (rather than a bitwise OR) is
significant only for **wide planes** (see below); for any plane with
`shard_bits ≤ 12` and a `0x1000`-aligned base, the low-12-bit shard index and the
high-nibble base do not overlap and the result is identical to an OR.

The consistent-hashing split property of BRC-129 is preserved per plane:
increasing a plane's `shard_bits` by one splits each of its groups into exactly
two children, so no subscription is ever re-hashed to an unrelated group. A
subscriber holding a shard *slice* adds both children; a subscriber following a
single shard key (e.g. one topic) follows it into the one child that inherits
it, joining the child before leaving the parent during the transition window.

#### Domain registry

| Domain | Base     | Plane                                   | Specification |
| ------ | -------- | --------------------------------------- | ------------- |
| `0x0`  | `0x0000` | Transaction plane (raw / EF)            | BRC-124, BRC-128, BRC-129 |
| `0x1`  | `0x1000` | **BEEF object plane**                   | this BRC |
| `0x2`–`0xE` | `0x2000`–`0xE000` | Reserved for future object planes | future BRC |
| `0xF`  | `0xF000` | **Forbidden** — overlaps control plane (`0xF800`–`0xFFFF`) | — |

Domain `0xF` MUST NOT be assigned as a plane base: its slot spans
`0xF000`–`0xFFFF`, of which `0xF800`–`0xFFFF` is the BRC-129 control plane. Planes
therefore occupy domains `0x1`–`0xE`. The residual band `0xF000`–`0xF7FF` (2,048
indices) between the top plane slot and the control plane remains reserved;
implementations MUST NOT join or transmit to it.

Implementations MUST NOT join or transmit to a plane's addresses unless that
plane is defined by this BRC or a future BRC.

### Plane Allocation and Growth Policy

Planes are allocated from opposite ends of the object-plane band so that the BEEF
plane retains a contiguous runway to widen its shard space:

- The **BEEF object plane grows upward** from base `0x1000`. Additional
  `shard_bits` consume successive `0x1000` slots immediately above it.
- **Future specialty planes are allocated from the top downward**, beginning at
  domain `0xE` (base `0xE000`) and descending, so their bases never intrude on
  the BEEF plane's upward runway.

Under this policy the BEEF plane may widen to as many as `shard_bits = 15`
(32,768 groups, occupying `0x1000`–`0x8FFF`) before meeting descending specialty
allocations, without ever re-basing its `0x1000` origin. A plane MUST NOT be
widened into slots already reserved to another plane.

### Wide Planes (`shard_bits > 12`)

A plane MAY operate with `shard_bits` greater than 12, in which case its shard
index exceeds 12 bits and its address range spans multiple contiguous `0x1000`
slots. Such a plane reserves **SlotSpan** = `ceil(2^shardBits / 4096)`
consecutive slots starting at its base. Constraints:

1. `planeBase + 2^shardBits ≤ 0xF800` (the range MUST NOT reach the control
   plane).
2. A plane's reserved slots MUST NOT overlap any other plane's reserved slots.
3. The plane base MUST remain `0x1000`-aligned; widening reserves additional
   slots upward but does not move the base.

Domain `0x0` (the transaction plane) retains the BRC-129 cap of `shard_bits ≤ 12`
and MUST NOT be widened, preserving interoperability with BRC-129-only
participants.

### The BEEF Object Plane (Domain `0x1`)

#### Payload

The BEEF object plane carries BEEF-family transaction objects **verbatim**. All
three BEEF encodings are permitted and are self-identifying by their leading
marker, so a single plane carries them without a per-format sub-allocation:

| Object                        | Leading marker            | Reference |
| ----------------------------- | ------------------------- | --------- |
| BEEF                          | `0100BEEF`                | [BRC-62](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0062.md) |
| Atomic BEEF                   | `01010101` + 32-byte TXID | [BRC-95](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0095.md) |
| BEEF V2 (TXID-only extension) | `0200BEEF`                | [BRC-96](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0096.md) |

The fabric never parses BEEF structure. Sharding, identity, and filtering key
on envelope fields fixed at ingress — the submitted topic (TopicID), a hash of
the whole object bytes (ContentID), and the fixed-offset version word. It
performs no UTXO resolution and strips no proof data.

#### Topical sharding

The BEEF plane's shard key is the **overlay topic**, not the transaction ID.
Overlay subscribers want topical slices; transaction IDs are uniformly
distributed, so TxID sharding would spread every topic across every group and
force each topical subscriber to receive the entire plane. Keying the group on
the topic makes the multicast group itself the coarse topical filter.

The **topic identifier** of an object is the hash of its topic name:

```text
TopicID = SHA-256(UTF-8 topic name)        // e.g. SHA-256("tm_uhrp_files")
```

and the object's group is the plane derivation applied to it:

```text
shardIndex = (binary.BigEndian.Uint32(TopicID[0:4]) >> (32 - shardBits)) & (2^shardBits - 1)
IDX        = 0x1000 + shardIndex
```

Properties:

- **One topic, one group.** A subscriber to topic *T* joins exactly
  `0x1000 + shardIndex(T)` and receives *T* plus only the topics that share its
  group by hash: at one million topics, ≈244 co-resident topics per group at
  `shard_bits = 12`, ≈31 at the reserved width of 15.
- **Zero fabric topic state.** Any topic string hashes to a group without
  registration, matching the permissionless BRC-22 topic space. Per-topic state
  exists only where topics are *elected* — at delivery operators, for their own
  subscribers. The fabric's routing state scales with groups and sources, never
  with topic count.
- **Consistent-hash splits.** Widening `shard_bits` by one sends each topic to
  one of its group's two children; subscribers move each elected topic to its
  child group during the generation-transition window.
- **Multi-topic objects.** A BRC-22 submission may name several topics; the
  object is emitted once per topic, each frame carrying that topic's TopicID.
  Sibling emissions share a ContentID (see *Frame carriage*).

**Per-topic throughput bound.** Because a topic occupies one group at a time, a
single topic's sustained rate is bounded by per-group delivery capacity, and
widening `shard_bits` splits topic *sets*, not one hot topic. A future
descriptor revision (`Version > 0`) may define a declared-spread mechanism (a
hot topic spread across `2^w` sibling groups by ContentID bits); this revision
reserves the problem and does not define it.

**Subject transaction.** The subject TxID remains consumer-level semantics —
Atomic BEEF (BRC-95) carries it explicitly in the 32 bytes after its prefix;
for BRC-62/BRC-96 it is the last transaction in topological order. The fabric
does not use it for sharding or identity.

#### Publication (ingress)

BEEF-plane publishers — overlay hosts and application services — form an
**overlay ingress class**, distinct from transaction-plane submitters and
admitted by operator ingress policy in the same manner as the miner-tier gate
on block and subtree ingress. A submission is the pair *(topic list, BEEF
object)*, mirroring the BRC-22 submit shape. For each submitted topic the
ingress derives the TopicID, computes the object's ContentID, and emits one
frame to the topic's group. Publishers submit to operator ingress; the plane's
multicast sources remain the operator's proxies, as on the transaction plane.

Re-submission of the same subject transaction with an updated proof (a BRC-62
BUMP refreshed after the transaction mines) is a legitimate, distinct object.
Ingress duplicate suppression MUST therefore key on ContentID (the object
bytes), never on the subject TxID.

#### Independent-plane semantics

The BEEF plane and the transaction plane are independent planes of operation:

1. **No cross-plane translation.** An object submitted to the BEEF plane is
   delivered only on the BEEF plane; a transaction submitted to the transaction
   plane is delivered only there. The fabric never bridges the two. A publisher
   that requires a transaction to reach both miners and overlay subscribers
   submits to both planes.
2. **Distinct publisher and subscriber classes (tiers).** The transaction plane
   serves the *settlement* and *miner* consumer tiers; the BEEF plane serves a
   distinct **overlay** consumer tier with its own ingress and delivery paths.
   These are separate participant classes; a single node MAY participate in more
   than one.
3. **Independent shard width.** The BEEF plane MAY run a `shard_bits` different
   from the transaction plane (coordinated per the manifest extension below).

#### Delivery identifiers and filtering

Fan-out filtering reads exactly two fixed-offset envelope fields; neither
requires a payload parse:

1. **TopicID** (header offset 56) — the **selectivity axis**. Cardinality is
   unbounded: the plane accommodates millions of concurrent topics because the
   fabric holds no per-topic state and filters resolve by hash lookup.
2. **BEEF version** (payload bytes 0–3, immediately after the header) — the
   **encoding-capability axis**. Cardinality is small and closed: the three
   markers in the Payload table above. [BRC-62](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0062.md)
   fixes the version word as a Uint32LE sequence beginning at `4022206465`
   (`0100BEEF`) whose marker remains `BEEF` only through `4022271999` — at most
   65,535 format versions. The version identifies an *encoding*, never an
   overlay: it MUST NOT be repurposed as an overlay namespace, both because it
   collides with future BEEF format revisions and because its space is orders
   of magnitude smaller than the topic population.

An **overlay-tier** subscriber's election is the pair *(topics, versions)*. A
subscriber's group set follows from its election: a topical subscriber's groups
are derived from its elected topics (one group per topic hash); an aggregator
elects groups directly — up to the whole plane — and takes every topic they
carry.

- **Topic filter** — the set of elected TopicIDs. An object is delivered only
  when its TopicID is a member. An absent topic filter admits every topic on
  the subscriber's elected groups (aggregator mode).
- **Version filter** — the set of accepted BEEF versions; absent admits all.
  This is a capability gate: for example, a host that cannot resolve BRC-96
  TXID-only ancestors excludes `0200BEEF`.

Filters compose in order: **group membership (network) → topic filter →
version filter → delivery**. Filtering narrows what a joined subscriber
receives; it never alters shard-group derivation, join sets, or
subscriber-to-node placement.

#### Conforming listener profile

A listener that serves the BEEF plane MUST:

1. Join the plane's domain-tagged groups (per source mode) and receive frames
   using the same [BRC-124](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md) /
   [BRC-126](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0126.md)
   machinery as the transaction plane, with retransmission keying and
   sequencing per *Frame carriage* below: flows are tracked per
   (sender, group), gap-detected on `SeqNum`, and recovered by NACK
   independently of transaction-plane flows.
2. Read each object's TopicID and BEEF version from their fixed offsets.
3. Apply each subscriber's topic filter, then version filter, at fan-out. The
   topic filter MUST resolve in time independent of the number of elected
   topics (e.g. a TopicID-keyed hash lookup), so per-frame cost does not grow
   with topic count.
4. Suppress retransmit duplicates per flow and `SeqNum` exactly as on the
   transaction plane.

These obligations are identical for every conforming listener implementation.

#### Frame carriage

Objects on the BEEF plane are carried in multicast frames that reuse the
[BRC-124](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md)
92-byte header layout with a distinct `FrameVersion`, preserving HashKey/SeqNum
stamping and
[BRC-126](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0126.md)
NACK retransmission; objects that exceed the path MTU are fragmented per
[BRC-130](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0130.md).
The concrete frame format is specified in a companion BRC; this BRC constrains
the header fields that addressing, retransmission, and filtering depend on
(Appendix A renders them as an informative reference layout):

- **ContentID (offset 8, 32 bytes)** — SHA-256d over the complete object bytes.
  This is the same hash BRC-130 already requires for reassembly verification of
  fragmented payloads, so fragmentation needs no special-casing. It MUST NOT be
  the subject TxID: a proof update re-emits the same subject with different
  bytes, and BRC-130 keys fragment reassembly on this field, so two in-flight
  objects for one subject must never share it.
- **TopicID (offset 56, 32 bytes)** — the field that carries the SubtreeID in
  transaction frames.
- **HashKey** = `XXH64(senderIPv6 ∥ domain-tagged groupIdx ∥ zeros)`. Unlike
  transaction frames, the 32-byte field (TopicID) is **excluded** from the flow
  key: including it would create one flow — and one gap tracker — per
  (sender, topic). Flows are per (sender, group), as in BRC-131, so
  retransmission and sequencing state is bounded by groups × multicast sources
  regardless of topic count. The domain-tagged IDX (`0x1nnn`) in the HashKey
  keeps BEEF flows distinct from transaction flows for the same shard number.
- **SeqNum** — per-sender monotonic within the (sender, group) flow; gap
  detection and NACK recovery operate on it unchanged.
- **BEEF version** — the first four payload bytes; not duplicated in the
  header.

Fragmentation composes cleanly with filtering: BRC-130 fragment headers are
layout-identical to BRC-124 for bytes 0–91, so ContentID and TopicID appear in
**every** fragment, while the version word appears only in the first fragment's
data. Listeners reassemble before fan-out (BRC-130 delivers the reassembled
object as a synthetic frame), so both filters evaluate on whole objects; a
listener MAY additionally drop fragments early by TopicID when no subscriber
has elected the topic.

### Per-Domain Shard Coordination (BRC-139 Extension)

[BRC-139](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0139.md)
advertises a single `shard_bits` and generation for the fabric. This BRC adds an
optional, backward-compatible **Domains** payload section so each plane advertises
and coordinates its own parameters.

#### Flags

One new flag bit is defined in the BRC-139 `Flags` byte (offset 7):

| Bit    | Name           | Meaning |
| ------ | -------------- | ------- |
| `1<<7` | `DomainsValid` | The datagram carries a Domains descriptor section (see below). |

`1<<7` is the final unallocated bit in the BRC-139 `Flags` byte. To avoid
exhausting the flag space, the Domain Descriptor block carries its own `Version`
field (below), which is the forward extension point for all subsequent per-domain
additions; no further top-level flag bit is to be spent on plane coordination.

#### Backward compatibility

When `DomainsValid = 0`, the datagram is a standard BRC-139 manifest describing
the transaction plane only; this is the default and requires no change from
BRC-139 producers or consumers.

When `DomainsValid = 1`, the top-level `ShardBits` (offset 36), `GenerationID`
(offset 48), and any top-level Successor block continue to describe **domain
`0x0`** exactly as in BRC-139. A BRC-139-only consumer ignores the trailing
Domains section and remains correct for the transaction plane. A BRC-145 consumer
additionally parses the Domains section for planes `≥ 0x1`. A domain-0 descriptor
MAY appear in the Domains section; if present it MUST agree with the top-level
fields, which remain authoritative for domain `0x0`.

#### Domains payload section

When `DomainsValid = 1`, a Domains section is appended **after** the BRC-139
Groups, Sources, and (top-level) Successor payloads, in this order. It begins with
a one-byte `DomainCount` followed by `DomainCount` fixed-layout descriptors:

```text
DomainCount (1 byte)  — number of descriptors, 1..15 (domains 0x00–0x0E)
followed by DomainCount × Domain Descriptor
```

**Domain Descriptor** — 24-byte core, optionally followed by a 24-byte Successor
block:

| Offset | Size | Field         | Description                                                          |
| ------ | ---- | ------------- | -------------------------------------------------------------------- |
| 0      | 1    | DomainID      | Plane domain selector (`0x00`–`0x0E`)                                |
| 1      | 1    | ShardBits     | This plane's shard-bit width (`0`–`15`; `0x00` MUST be `≤ 12`)       |
| 2      | 1    | SlotSpan      | Contiguous `0x1000` slots reserved to this plane; `≥ ceil(2^ShardBits/4096)` |
| 3      | 1    | DomainFlags   | See below                                                            |
| 4      | 1    | Version       | Descriptor format version; `0x00` in this revision                  |
| 5      | 3    | Reserved      | MUST be zero on send; ignored on receive                            |
| 8      | 16   | GenerationID  | This plane's 128-bit generation identifier; bumped on `ShardBits` change |
| 24     | 24   | Successor     | Present iff `DomainFlags.SuccessorValid`; layout per BRC-139 Successor block |

**DomainFlags**:

| Bit    | Name             | Meaning |
| ------ | ---------------- | ------- |
| `1<<0` | `SourceModeSSM`  | This plane's data plane uses SSM (`FF3x`). |
| `1<<1` | `SuccessorValid` | A 24-byte Successor block follows this descriptor's core. |
| `1<<2` | `Active`         | The announcer publishes and/or serves this plane (authoritative per-domain participation signal). |

The overall datagram size becomes:

```text
64 + groups + sources + topSuccessor
   + (DomainsValid ? 1 + Σ(24 + (SuccessorValid_i ? 24 : 0)) : 0)
```

The `ManifestCRC` (offset 44) is computed over the whole datagram including the
Domains section, as in BRC-139. Implementations SHOULD keep the total datagram
`≤ 1232` bytes to avoid IPv6 fragmentation; a handful of descriptors fits
comfortably within that budget.

#### Validation

- `DomainsValid = 1` with `DomainCount = 0` is malformed and MUST be rejected.
- `DomainID` values MUST be in `0x00`–`0x0E`, unique within a datagram, and MUST
  NOT be `0x0F`.
- For each descriptor, `planeBase(DomainID) + 2^ShardBits ≤ 0xF800`, and the
  reserved slot ranges of distinct descriptors MUST NOT overlap.
- A descriptor with `DomainID = 0x00` MUST carry `ShardBits ≤ 12` and MUST agree
  with the top-level `ShardBits`/`GenerationID`.
- `SuccessorValid = 1` requires the announcer to be `Authoritative` (BRC-139
  `Flags` `1<<1`) and requires the successor's `ShardBits` to differ from the
  descriptor's `ShardBits` by at most ±1. The slot-range constraint
  (`planeBase(DomainID) + 2^ShardBits ≤ 0xF800`, no overlap with other planes'
  reserved slots) applies to the successor's `ShardBits` equally.
- Consumers MUST ignore descriptors whose `DomainID` names a plane they do not
  implement, rather than rejecting the datagram.

#### Per-domain adoption and live resharding

The BRC-139 normative consumer profile — authoritative quorum, hysteresis, ±1
`shard_bits`-shift bound, manual-pin precedence, divergence telemetry, and
Successor-block generation transitions with dual-emit bridging — applies **per
domain**, keyed on `DomainID`. Each plane adopts, pins, and transitions its own
`ShardBits`/`GenerationID` independently; a generation transition on one plane
does not perturb another. Domain `0x0` continues to be coordinated by the
top-level BRC-139 fields for backward compatibility, equivalently to a
`DomainID = 0x00` descriptor.

`SlotSpan` MAY exceed the value implied by the current `ShardBits` to reserve a
widening runway in advance; consumers MUST treat the reserved slots as belonging
to that plane even before they are populated.

#### RoleHint

Two `RoleHint` values (BRC-139 offset 37) are added as informational hints that a
participant serves the BEEF plane:

| Value  | Name           | Meaning |
| ------ | -------------- | ------- |
| `6`    | `ProducerBEEF` | Publishes BEEF-plane objects. |
| `7`    | `ListenerBEEF` | Subscribes to BEEF-plane groups. |

`RoleHint` is a single informational byte and cannot express a node that serves
multiple planes; the per-domain `DomainFlags.Active` bit is the authoritative
per-plane participation signal, and the **TopicID and BEEF version** govern
per-object routing and filtering. `RoleHint` conveys only a coarse
participant-class hint and MUST NOT be relied upon for any routing, filtering,
or delivery decision.

### Source Discovery (SSM) for Object Planes

Under SSM, a subscriber must learn each plane's publisher sources before issuing
`(S,G)` joins. When an object plane is published by the same sources as the
transaction plane, the plane inherits the BRC-139 global Sources payload: a
subscriber unions the announced sources and issues `(S,G)` joins for the plane's
domain-tagged groups using the same source set. A plane whose
`DomainFlags.SourceModeSSM` differs from domain `0x0` selects the corresponding
`FF3x`/`FF0x` prefix for that plane's addresses only.

If a future deployment publishes an object plane from a **disjoint** source set,
per-domain source advertisement is required; this is reserved for a future
revision via the descriptor's `Version` field. In this revision, all object
planes MUST be published from the announced global source set.

### Operational and Security Considerations

- **Firewall / PIM / MLD.** Deployments MUST permit join and forwarding for the
  BEEF plane's address band (`0x1000`–`0x1FFF` at the configured scope and source
  mode, extended per `SlotSpan`) in addition to the transaction-plane and
  control-plane ranges. Operators SHOULD scope object-plane groups identically to
  the transaction plane unless deliberately isolating them.
- **Structural parsing bound.** The fabric never walks BEEF structure (identity
  and filtering key on ingress-fixed envelope fields), but ingress MUST bound
  accepted object size, and consumers parsing attacker-influenced objects MUST
  bound the walk (transaction count, BUMP sizes, nesting) and reject malformed
  input rather than allocating unboundedly.
- **State bounds at scale.** Every per-topic cost lives at the edge of the
  system: the fabric's multicast routing state scales with joined groups ×
  sources; ingress holds per-(sender, group) flow counters; a delivery listener
  holds one filter entry per *elected* topic of its own subscribers; per-frame
  filter evaluation is O(1). No component's state scales with the global topic
  population, which is what admits millions of concurrent overlays.
- **Live tail, not history.** Multicast delivery begins at join time; NACK
  recovers transit gaps, not missed history. A host bootstrapping a topic
  acquires history through the overlay synchronisation protocols
  ([BRC-88](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0088.md)
  propagation,
  [BRC-76](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0076.md)
  Graph Aware Sync,
  [BRC-136](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0136.md)
  block-anchored sync) and uses this plane for the live tail thereafter.
- **No settlement guarantee.** Because the planes do not bridge, a transaction
  submitted only to the BEEF plane is not delivered to transaction-plane
  (settlement) subscribers. This is intentional; publishers requiring settlement
  MUST also submit on the transaction plane.
- **Group-id override and scope.** The BRC-129 group-id override and scope rules
  apply unchanged to all planes; a private group-id isolates every plane
  simultaneously.

## Appendix A (informative): Reference Frame Layout

This appendix renders the *Frame carriage* constraints as a concrete layout for
implementers. It is informative: the companion frame-format BRC is
authoritative for the frame, and the `FrameVersion` value `0x09` is provisional
(the next unassigned code after BRC-142's `0x08`). The header reuses the
[BRC-124](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md)
92-byte layout at identical offsets, so existing firewalls, classifiers, and
retry infrastructure require no changes.

### BEEF object frame (`FrameVer 0x09`, provisional) — 92-byte header + payload

| Offset | Size | Type | Field | Description |
| ------ | ---- | ---- | ----- | ----------- |
| 0 | 4 | `uint32` BE | Network Magic | `0xE3E1F3E8` (BSV mainnet P2P magic). Frames with incorrect magic are rejected. |
| 4 | 2 | `uint16` BE | Protocol Version | `0x02BF` (703). Informational; receivers do not validate. |
| 6 | 1 | `byte` | Frame Version | `0x09` — BEEF object frame. Any other value is handled by a different decoder. |
| 7 | 1 | `byte` | Reserved | `0x00`. The BEEF version is **not** duplicated here — it is read from the payload's first four bytes. Reserved for future plane-level message types. |
| 8 | 32 | `[32]byte` | ContentID | `SHA-256d(payload bytes)` — the object's identity. Keys ingress duplicate suppression and BRC-130 fragment reassembly (the reassembly-verification hash BRC-130 already requires). MUST NOT be the subject TxID: a proof update re-emits the same subject with different bytes. |
| 40 | 8 | `uint64` BE | HashKey | `XXH64(senderIPv6 ∥ domain-tagged groupIdx ∥ zeros)`; proxy-stamped; `0` = unset. Per (sender, group) flow — TopicID is **excluded**, bounding gap-tracker state by groups × multicast sources regardless of topic count. The domain-tagged IDX (`0x1nnn`) keeps BEEF flows distinct from transaction-plane flows on the same shard number. |
| 48 | 8 | `uint64` BE | SeqNum | Per-sender monotonic counter within the (sender, group) flow; proxy-stamped; `0` = unstamped. Drives gap detection, NACK recovery, and retransmit dedup exactly as on the transaction plane. |
| 56 | 32 | `[32]byte` | TopicID | `SHA-256(UTF-8 topic name)` (e.g. `SHA-256("tm_uhrp_files")`). The delivery-selectivity key: group derivation takes its top bits (`Uint32(TopicID[0:4]) >> (32 − shardBits)`), and listener fan-out filters subscribers on it. Occupies the field that carries the SubtreeID in transaction frames. |
| 88 | 4 | `uint32` BE | Payload Length | Byte length of the payload. |
| 92 | \* | `[]byte` | Payload | The BEEF object **verbatim** — no envelope, no re-encoding, proof data intact. |

### Payload leading bytes — BEEF version word

| Payload `[0:4]` | Type | Encoding | Reference |
| --------------- | ---- | -------- | --------- |
| `0100BEEF` | `uint32` LE (4022206465) | BEEF | [BRC-62](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0062.md) |
| `0200BEEF` | `uint32` LE (4022206466) | BEEF V2 (TXID-only extension) | [BRC-96](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0096.md) |
| `01010101` | 4-byte prefix (32-byte subject TxID follows) | Atomic BEEF | [BRC-95](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0095.md) |

The version word is the version filter's input — an encoding-capability gate
only, never an overlay namespace (see *Delivery identifiers and filtering*).

Intentionally absent from the header: the **subject TxID** (consumer-level
semantics inside the payload — explicit in Atomic BEEF, last in topological
order for BRC-62/BRC-96) and any **per-format sub-type byte** (the payload
marker is self-identifying at a fixed offset).

Objects exceeding the path MTU are carried as
[BRC-130](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0130.md)
fragments (`FrameVer 0x03`, 104-byte header, `OrigFrameVer = 0x09`) with bytes
0–91 layout-identical to the table above; the interaction with filtering is
specified in *Frame carriage*.

## References

- [BRC-22: Overlay Network Data Synchronization](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0022.md)
  — topical submission and admittance model the plane transports
- [BRC-62: Background Evaluation Extended Format (BEEF) Transactions](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0062.md)
  — BEEF encoding carried on the object plane
- [BRC-74: BSV Unified Merkle Path (BUMP) Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0074.md)
  — proof payload embedded in BEEF
- [BRC-95: Atomic BEEF Transactions](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0095.md)
  — explicit-subject BEEF encoding
- [BRC-76: Graph Aware Sync Protocol](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0076.md)
  — overlay history synchronisation complementing the live tail
- [BRC-87: Standardized Naming Conventions for BRC-22 Topic Managers and BRC-24 Lookup Services](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0087.md)
  — `tm_*` topic naming hashed into TopicIDs
- [BRC-88: Overlay Services Synchronization Architecture](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0088.md)
  — SHIP/SLAP host discovery and the per-host propagation this plane subsumes
- [BRC-96: BEEF V2 Txid Only Extension](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0096.md)
  — TXID-only BEEF encoding
- [BRC-124: Multicast Transaction Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md)
  — data-frame header reused by object-plane frames
- [BRC-126: Multicast Transaction NACK Retransmission Protocol](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0126.md)
  — retransmission machinery inherited by object planes
- [BRC-128: Multicast Extended Transaction Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0128.md)
  — Extended Format frames on the transaction plane
- [BRC-129: IPv6 Multicast Group Address Assignments](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0129.md)
  — base addressing scheme this BRC extends
- [BRC-130: Multicast Transaction Frame Fragmentation](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0130.md)
  — fragmentation for large objects
- [BRC-136: Block-Anchored Overlay Synchronization via Block-Aligned Sparse Merkle Trees (BASM)](https://github.com/bitcoin-sv/BRCs/blob/master/overlays/0136.md)
  — block-anchored overlay history synchronisation
- [BRC-139: Multicast Shard Manifest Announcement Protocol](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0139.md)
  — manifest protocol this BRC forward-extends
- [RFC 4607: Source-Specific Multicast for IP](https://www.rfc-editor.org/rfc/rfc4607)
  — SSM address range
- [RFC 8815: Deprecating Any-Source Multicast (ASM) for Interdomain Multicast](https://www.rfc-editor.org/rfc/rfc8815)
  — rationale for SSM-only global scope
