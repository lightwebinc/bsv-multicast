# BRC-149: Multicast BEEF Object Frame Format

Jeff Harris (jeff@lightweb.net)

> **Status:** Draft — official submission prepared as BRC-149. Companion to
> [BRC-148](brc-148-shard-domain-beef-plane.md), which allocates the BEEF
> object plane and constrains the fields this format carries.

## Abstract

This BRC specifies the three wire forms of the
[BRC-148](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0148.md)
BEEF object plane: the **multicast object frame** (FrameVer `0x09`, assigned
here), which carries one BEEF-family transaction object on the plane's
domain-tagged shard groups; the **submission record**, the unicast envelope a
publisher sends to operator ingress naming one object to one or more overlay
topics; and the **delivery record**, the unicast envelope a delivery edge
streams to a subscriber. The frame reuses the
[BRC-124](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0124.md)
92-byte header layout at identical offsets, so existing classifiers,
retransmission, and fragmentation infrastructure require no changes. The
records exist because BEEF bytes are not self-delimiting without a full
structural parse — which the fabric never performs — so unlike the
[BRC-143](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0143.md)/[BRC-144](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0144.md)
push lanes, BEEF lanes carry an explicit length-carrying envelope.

## Copyright

This BRC is licensed under the Open BSV License.

## Motivation

[BRC-148](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0148.md)
allocates the BEEF object plane (domain `0x1`), defines its topical sharding,
filtering, and coordination, and constrains the header fields that
addressing, retransmission, and filtering depend on — but, following the
convention that frame formats are specified separately from addressing
(BRC-124 vs BRC-129; BRC-143/BRC-144 vs their carriage rules), it defers the
concrete formats to this BRC. This BRC assigns the frame version and fixes
the byte layouts: the fabric frame, and the two unicast record forms that
carry BEEF objects between participants and operator infrastructure.

## Specification

### BEEF object frame (FrameVer `0x09`) — 92-byte header + payload

FrameVer `0x09` is assigned to the BEEF object frame (the next code after
BRC-142's `0x08`).

| Offset | Size | Type | Field | Description |
| ------ | ---- | ---- | ----- | ----------- |
| 0 | 4 | `uint32` BE | Network Magic | `0xE3E1F3E8` (BSV mainnet P2P magic). Frames with incorrect magic are rejected. |
| 4 | 2 | `uint16` BE | Protocol Version | `0x02BF` (703). Informational; receivers do not validate. |
| 6 | 1 | `byte` | Frame Version | `0x09` — BEEF object frame. Any other value is handled by a different decoder. |
| 7 | 1 | `byte` | Reserved | `0x00` on send; ignored on receive (reserved for future plane-level message types). The BEEF encoding version is **not** duplicated here — it is the payload's first four bytes. |
| 8 | 32 | `[32]byte` | ContentID | `SHA-256d(payload bytes)` — the object's identity; keys BRC-130 fragment reassembly and, with TopicID, ingress duplicate suppression. Never the subject TxID. |
| 40 | 8 | `uint64` BE | HashKey | Per-(sender, group) flow identifier; stamped at ingress; `0` = unset. Derivation and flow semantics per [BRC-148](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0148.md) §Frame carriage (TopicID excluded). |
| 48 | 8 | `uint64` BE | SeqNum | Per-sender monotonic counter within the (sender, group) flow; stamped at ingress; `0` = unstamped. Drives gap detection, NACK recovery, and retransmit dedup. |
| 56 | 32 | `[32]byte` | TopicID | `SHA-256(UTF-8 topic name)`. The delivery-selectivity key: group derivation takes its top bits, and fan-out filters subscribers on it. Occupies the field that carries the SubtreeID in transaction frames. |
| 88 | 4 | `uint32` BE | Payload Length | Byte length of the payload. |
| 92 | \* | `[]byte` | Payload | The BEEF object **verbatim** — no envelope, no re-encoding, proof data intact. |

#### Payload leading bytes — BEEF version word

| Payload `[0:4]` | Type | Encoding | Reference |
| --------------- | ---- | -------- | --------- |
| `0100BEEF` | `uint32` LE (4022206465) | BEEF | [BRC-62](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0062.md) |
| `0200BEEF` | `uint32` LE (4022206466) | BEEF V2 (TXID-only extension) | [BRC-96](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0096.md) |
| `01010101` | 4-byte prefix (32-byte subject TxID follows) | Atomic BEEF | [BRC-95](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0095.md) |

The version word is the BRC-148 version filter's input — an
encoding-capability gate only, never an overlay namespace. Intentionally
absent from the header: the subject TxID (consumer-level semantics inside
the payload) and any per-format sub-type byte (the marker is
self-identifying at a fixed offset).

#### Fragmentation

Objects exceeding the path MTU are carried as
[BRC-130](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0130.md)
fragments (`FrameVer 0x03`, `OrigFrameVer = 0x09`) with bytes 0–91
layout-identical to the table above, so ContentID and TopicID appear in
every fragment; ContentID is the reassembly key and verification hash. The
interaction with filtering is specified in BRC-148 §Frame carriage.

### Submission record (ingress)

A publisher submits the pair *(topic list, BEEF object)* as one record:

```text
Offset  Size  Field
  0       2   Tag         (uint16 BE = 0xBEEF — record discriminator on shared ports)
  2       1   RecordVer   (0x01)
  3       1   TopicCount  (1..15)
  4       …   Topics      (TopicCount × { uint8 NameLen (1..64) ∥ NameLen bytes UTF-8 topic name })
  …       4   ObjectLen   (uint32 BE, ≥ 1; operators bound the maximum accepted size)
  …       …   Object      (the BEEF object; leading marker per the version-word table)
```

For each named topic the ingress derives `TopicID = SHA-256(name)`, computes
the object's ContentID once, and emits one `FrameVer 0x09` frame to that
topic's group — sibling emissions share a ContentID, and ingress duplicate
suppression keys on the (ContentID, TopicID) pair per BRC-148. A record
whose object does not lead with a marker from the version-word table, whose
lengths violate the bounds above, or whose object exceeds the operator's
size bound MUST be rejected. A malformed record desynchronises its stream;
the receiver MUST close the connection.

#### Detection on shared ports

BEEF is an open ingress class: submission records MAY ride the open
transaction port alongside the existing grammars, distinguished by leading
bytes — network magic `0xE3E1F3E8` selects a framed datagram, the `0xBEEF`
tag selects a submission record, and anything else is a bare transaction
([BRC-12](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0012.md)
raw / [BRC-30](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0030.md)
Extended Format, whose little-endian version byte at offset 1 is `0x00` —
the three forms cannot collide). Over TCP the grammar is committed once per
connection; over UDP it is detected per datagram, and a record must fit one
datagram (larger objects submit over a stream transport). Operators MAY
additionally expose a dedicated single-record-grammar port for flow
separation; it carries no additional admission semantics.

### Delivery record (egress)

A delivery edge streaming BEEF objects to a subscriber over a unicast lane
emits, per delivered object:

```text
Offset  Size  Field
  0      32   TopicID    (the matched topic's identifier)
 32       4   ObjectLen  (uint32 BE, ≥ 1)
 36       …   Object     (the BEEF object verbatim)
```

The record carries the TopicID, not the topic name — the subscriber elected
its topics and maps identifiers back locally. Subscribers taking whole
`FrameVer 0x09` frames instead of a stripped lane need no record; the frame
already carries both identifiers.

## References

- [BRC-12: Raw Transaction Format](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0012.md)
  — bare-transaction grammar sharing the open port
- [BRC-30: Extended Format Transaction](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0030.md)
  — bare-transaction grammar sharing the open port
- [BRC-62: Background Evaluation Extended Format (BEEF) Transactions](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0062.md)
  — BEEF encoding and version word
- [BRC-95: Atomic BEEF Transactions](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0095.md)
  — explicit-subject BEEF encoding
- [BRC-96: BEEF V2 Txid Only Extension](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0096.md)
  — TXID-only BEEF encoding
- [BRC-124: Multicast Transaction Frame Format](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0124.md)
  — the 92-byte header layout this frame reuses
- [BRC-126: Multicast Transaction NACK Retransmission Protocol](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0126.md)
  — retransmission machinery operating on HashKey/SeqNum
- [BRC-130: Multicast Transaction Frame Fragmentation](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0130.md)
  — fragmentation for large objects
- [BRC-143: Multicast Subtree Data Push Frame Format](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0143.md)
  — push-lane format precedent
- [BRC-144: Multicast Block Push Frame Format](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0144.md)
  — push-lane format precedent
- [BRC-148: Multicast Shard Domain Partitioning and the BEEF Object Plane](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0148.md)
  — the plane, sharding, filtering, and coordination this format serves
