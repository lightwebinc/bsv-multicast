# BRC-144 — Anchor Object Format (Non-Multicast)

BRC-144 defines the **header-stripped** wire form of a chained anchor
transaction: the raw anchor object with **no multicast frame header**, for
delivery and ingest over unicast byte streams and datagrams. It is the
non-multicast counterpart of [BRC-134](brc-134-anchor-transactions.md) (anchor
over the multicast fabric), and it composes inline with BRC-12 (raw) and
BRC-30 (EF) transactions.

> **Status: DRAFT (design).** Member of the **BRC-143–146 non-multicast object
> family**. The shared **Stream Composition** framing (1-byte type tag,
> self-delimiting bodies, bare on single-class lanes) is defined in
> [BRC-143 §2](brc-143-coinbase-object.md#2-stream-composition--inline-with-brc-12--brc-30);
> the anchor tag is `0x03`. Local design doc; an upstream `bitcoin-sv/BRCs` PR
> is future work.

---

## 1. Purpose

An anchor transaction is the root (first) transaction of a chain of dependent
transactions; every dependent references it as an input, so a consumer that
misses the anchor cannot validate the chain. Over multicast this justifies
routing the anchor to the global control group (BRC-134); over unicast it
justifies delivering the anchor **whole and identified** ahead of, or
alongside, the dependents.

BRC-144 delivers the anchor as exactly the bytes a node parses: the raw
serialised transaction, self-delimiting, with a one-byte type identity. Unlike
a coinbase, an anchor has **no structural signature** — it is an ordinary
transaction in every respect except its role in the chain — so the type tag is
the *only* signal that a given transaction is the anchor. This makes the tag a
hard requirement in multiplexed mode.

---

## 2. Stream Composition

Per the family framing in
[BRC-143 §2](brc-143-coinbase-object.md#2-stream-composition--inline-with-brc-12--brc-30):
the anchor object rides a shared stream behind the 1-byte tag `0x03`, or bare
on an anchor-dedicated lane. Its body is self-delimiting by transaction
structure — the reader advances by parsing the transaction, needing no length
prefix and no envelope.

Because an anchor is byte-indistinguishable from any other transaction,
**single-class (bare) mode is only valid on a lane whose entire contents are
anchors** (e.g. a dedicated ingest port). On any stream that also carries
ordinary transactions, coinbases, or other anchors, the `0x03` tag is
mandatory — there is no fallback structural detection.

---

## 3. Body Format

The anchor body is the **raw serialised anchor transaction**, identical to a
BRC-12 payload — version (4 bytes LE), input vector, output vector, locktime
(4 bytes LE) — or its BRC-30 Extended Format serialisation. No additional
envelope. This is the same payload BRC-134 carries after its 92-byte header.

**Extended Format.** A BRC-30 EF body carries the `0000000000EF` marker after
the version and is delimited identically to a standard transaction body.

---

## 4. Identity

`AnchorTxID = SHA256d(standard serialisation)` — internal byte order; for a
BRC-30 EF body computed over the standard serialisation (EF input extras
excluded), matching BRC-134's `TxID` header field. No in-band `ContentID`
field: the receiver computes the TxID from the bytes when it needs one (e.g.
to key the dependents that reference it).

---

## 5. Relationship to BRC-134

BRC-144 is BRC-134 with the transport envelope removed:

| Aspect             | BRC-134 (multicast)                     | BRC-144 (non-multicast)             |
| ------------------ | --------------------------------------- | ----------------------------------- |
| Header             | 92-byte frame, `FrameVer 0x06`          | none — 1-byte tag `0x03` (multiplexed) or bare (single-class) |
| Flow id / sequence | `HashKey` + `SeqNum` (proxy-stamped)    | none — TCP provides ordering        |
| Reliability        | BRC-126 NACK/retry                      | TCP (delivery); producer resend (ingest) |
| Payload            | raw anchor tx                           | **identical** raw anchor tx         |
| Delivery scope     | `GroupBlockBroadcast` (all subscribers) | the unicast destination(s)          |

The anchor bytes are unchanged across the translation; an edge adds or strips
the header.

---

## References

- [BRC-134 — Anchor Transaction Frame Format](brc-134-anchor-transactions.md) (multicast counterpart)
- [BRC-143 — Coinbase Object Format](brc-143-coinbase-object.md) (family lead; shared Stream Composition)
- [BRC-145 — Subtree Object Format](brc-145-subtree-object.md),
  [BRC-146 — Block Object Format](brc-146-block-object.md) (family members)
- BRC-12 (raw transaction) / BRC-30 (Extended Format) — the transaction bodies
  this format composes with
