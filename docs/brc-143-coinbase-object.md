# BRC-143 — Coinbase Object Format (Non-Multicast)

BRC-143 defines the **header-stripped** wire form of a coinbase transaction:
the raw coinbase object with **no multicast frame header**, for delivery and
ingest over unicast byte streams and datagrams. It is the non-multicast
counterpart of [BRC-133](brc-133-coinbase-delivery.md) (coinbase over the
multicast fabric), and it composes inline with BRC-12 (raw) and BRC-30 (EF)
transactions.

> **Status: DRAFT (design).** First member of the **BRC-143–146
> non-multicast object family** (coinbase, anchor, subtree, block) used for
> header-stripped delivery and symmetric ingest — the object crosses the wire
> with none of the BRC-124 92-byte envelope. Local design doc; an upstream
> `bitcoin-sv/BRCs` PR is future work. This document defines the shared
> **Stream Composition** framing (below) that BRC-144/145/146 reference.

---

## 1. Purpose

The multicast formats (BRC-131/132/133/134) wrap each object in a 92-byte
BRC-124 header carrying `Magic`, `HashKey`, `SeqNum`, and a subtree/pad field.
Those fields serve the replicated fabric: per-flow identity, gap detection,
NACK-based retransmission. A **unicast** consumer reached over reliable TCP —
for example a node ingesting raw objects behind a tunnel — needs none of them:
TCP supplies ordering and reliability, and the delivering edge has already
resolved reassembly, decoalescing, and recovery.

BRC-143 therefore delivers a coinbase as exactly the bytes a node parses: the
raw serialised transaction, self-delimiting, with a one-byte type identity so
it is distinguishable from an ordinary transaction on a shared stream. The
same object is accepted on ingress, so a producer submits a coinbase in the
identical form it would receive one.

---

## 2. Stream Composition — inline with BRC-12 / BRC-30

*This section is the shared framing for the BRC-143–146 family; BRC-144/145/146
reference it.*

Family objects carry **no multicast frame header** — none of the BRC-124
`Magic`/`HashKey`/`SeqNum` envelope. They are delivered and ingested as bare,
**self-delimiting** objects, interleavable with BRC-12 (raw) and BRC-30 (EF)
transactions on one stream (a byte stream, e.g. header-stripped unicast
delivery) or as one object per datagram.

**Type identity — 1-byte tag (multiplexed mode).** When object classes share
one stream, each object is preceded by a single type-tag byte:

| Tag    | Object                        | Body self-delimited by | Spec    |
| ------ | ----------------------------- | ---------------------- | ------- |
| `0x01` | Transaction (BRC-12 / BRC-30) | transaction structure  | —       |
| `0x02` | Coinbase                      | transaction structure  | BRC-143 |
| `0x03` | Anchor                        | transaction structure  | BRC-144 |
| `0x04` | Subtree                       | `NodeCount`            | BRC-145 |
| `0x05` | Block                         | `SubtreeCount`         | BRC-146 |

The tag supplies the type identity that structure alone cannot: a coinbase, an
anchor, and an ordinary payment are all valid transactions on the wire, byte-
indistinguishable without it.

**Single-class mode.** On a lane dedicated to one class — e.g. a
transaction-only delivery stream, or a per-class ingest port — the tag is
omitted and objects are bare. A BRC-12 / BRC-30 transaction stream is then
byte-for-byte unmodified; the class is known from the lane. This keeps the raw
transaction stream a hard, unadorned baseline.

**No length prefix.** Every body is self-delimiting — transactions by their own
structure (version, input/output vectors, locktime; EF by the BRC-30 marker),
Subtree by `NodeCount`, Block by `SubtreeCount`. A reader advances object by
object with no outer frame. This is the deliberate inverse of the multicast
header: identity and boundaries come from the object, not an envelope.

---

## 3. Body Format

The coinbase body is the **raw serialised coinbase transaction**, identical to
a BRC-12 payload — version (4 bytes LE), input vector, output vector, locktime
(4 bytes LE) — or its BRC-30 Extended Format serialisation. No additional
envelope. This is the same payload BRC-133 carries after its 92-byte header.

A coinbase is structurally a transaction with **exactly one input** whose
previous outpoint is null — 32 zero bytes and index `0xFFFFFFFF`. That
structure is a validity property, **not** a reliable type discriminator on a
mixed stream (a malformed ordinary tx could imitate it and the parser must not
guess); type identity is the tag byte (`0x02`), per §2.

**Extended Format.** When the body is BRC-30 EF, it carries the
`0000000000EF` marker after the version, exactly as a BRC-30 transaction. A
reader distinguishes EF from standard by the marker while delimiting, identical
to a tag-`0x01` transaction body.

---

## 4. Identity

`CoinbaseTxID = SHA256d(standard serialisation)` — the double-SHA256 of the
**standard** (non-EF) transaction bytes, in internal byte order. For a BRC-30
EF body the TxID is computed over the standard serialisation (EF input extras
excluded), matching how BRC-133 derives its `ContentID`.

There is no in-band `ContentID` field: unlike the multicast frame (which
carries the TxID in the header for flow keying and NACK), the unicast object
needs none — the receiver computes the TxID from the bytes if it needs one.

---

## 5. Relationship to BRC-133

BRC-143 is BRC-133 with the transport envelope removed:

| Aspect             | BRC-133 (multicast)                          | BRC-143 (non-multicast)             |
| ------------------ | -------------------------------------------- | ----------------------------------- |
| Header             | 92-byte BRC-131 frame, `FrameVer 0x04`, `MsgType 0x02` | none — 1-byte tag `0x02` (multiplexed) or bare (single-class) |
| Flow id / sequence | `HashKey` + `SeqNum` (proxy-stamped)         | none — TCP provides ordering        |
| Reliability        | BRC-126 NACK/retry                           | TCP (delivery); producer resend (ingest) |
| Payload            | raw coinbase tx                              | **identical** raw coinbase tx       |
| Delivery scope     | `GroupBlockBroadcast` (all subscribers)      | the unicast destination(s)          |

An edge translating between fabric and a unicast consumer maps one to the
other by adding or stripping the header; the coinbase bytes are unchanged.

---

## References

- [BRC-133 — Coinbase Transaction Frame Format](brc-133-coinbase-delivery.md) (multicast counterpart)
- [BRC-131 — Block Announcement Frame Format](brc-131-block-announcements.md)
- [BRC-144 — Anchor Object Format](brc-144-anchor-object.md),
  [BRC-145 — Subtree Object Format](brc-145-subtree-object.md),
  [BRC-146 — Block Object Format](brc-146-block-object.md) (family members)
- BRC-12 (raw transaction) / BRC-30 (Extended Format) — the transaction bodies
  this format composes with
