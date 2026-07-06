# BRC-146 — Block Object Format (Non-Multicast)

BRC-146 defines the **header-stripped** wire form of a block announcement: the
80-byte block header, the coinbase TxID, and the ordered list of subtree roots
the block references, with **no multicast frame header**. It is the
non-multicast counterpart of the `BlockAnnounce` payload in
[BRC-131](brc-131-block-announcements.md), and it composes inline with the
BRC-143–146 family.

> **Status: DRAFT (design).** Member of the **BRC-143–146 non-multicast object
> family**; shared framing in
> [BRC-143 §2](brc-143-coinbase-object.md#2-stream-composition--inline-with-brc-12--brc-30);
> block tag `0x05`. Local design doc; upstream `bitcoin-sv/BRCs` PR is future
> work.

---

## 1. Purpose

A block announcement tells a consumer that a block was found, commits to it via
the 80-byte header, and — critically — names **which subtrees the block
references, in order**. This ordered subtree-hash list is the block↔subtree
association: a consumer validates subtrees independently by their own roots
(BRC-145), and the block object is what binds those roots into a block and
fixes their order for merkle assembly.

BRC-146 delivers that as the bare `BlockAnnounce` payload — header + coinbase
TxID + ordered subtree roots — with the multicast envelope removed. It carries
no transactions and no subtree contents; it is pure block-level metadata,
small relative to the block it describes.

---

## 2. Stream Composition

Per the family framing in
[BRC-143 §2](brc-143-coinbase-object.md#2-stream-composition--inline-with-brc-12--brc-30):
the block object rides a shared stream behind the 1-byte tag `0x05`, or bare on
a block-dedicated lane. Its body is **self-delimiting by `SubtreeCount`** — the
reader consumes the fixed prefix, then exactly `SubtreeCount × 32` hash bytes.

---

## 3. Body Format

All multi-byte integers are big-endian (family convention). This is the
BRC-131 `BlockAnnounce` payload with no frame header:

| Offset | Size   | Field          | Notes                                                                    |
| ------ | ------ | -------------- | ------------------------------------------------------------------------ |
| 0      | 80     | `BlockHeader`  | Standard 80-byte BSV block header (version, prev-block hash, merkle root, timestamp, bits, nonce). See [BRC-135](brc-135-block-header-format.md). |
| 80     | 32     | `CoinbaseTxID` | SHA256d of the coinbase transaction (internal byte order).               |
| 112    | 4      | `SubtreeCount` (N) | Number of subtree root hashes that follow (uint32 BE); may be 0 (empty block). Delimits the body. |
| 116    | 32 × N | `SubtreeHashes`| Ordered subtree merkle roots (each 32 bytes, internal byte order).       |

**Minimum size:** 116 bytes (header + coinbase TxID + `SubtreeCount = 0`).

**BlockHash** is `SHA256d(BlockHeader)` — the receiver computes it from the
80-byte header; there is no separate block-hash field. The header already
carries the previous-block hash (bytes 4–35), so chain context is intrinsic; no
extra prev/height fields are added. Block height is not present in a BSV header
(it appears only in the coinbase per BIP-34) and is recovered from chain state,
not this object.

**SubtreeHashes ordering** matches the producer's subtree enumeration and is
the order used to assemble the block merkle root from the subtree roots. The
per-subtree objects (BRC-145) deliberately omit their own index; this ordered
list is where position lives.

---

## 4. Standalone block-root recomposition (optional)

A consumer that only *announces and validates* needs no more than §3: each
referenced subtree is verified by its own root (BRC-145), and the block header
commits to the composed root. A consumer that must **recompute the block merkle
root standalone** from partial subtrees — folding an incomplete *final* subtree
into a fixed-height slot — additionally needs the block's subtree **capacity**
(equivalently, the leaf count of the first, complete, power-of-two subtree),
because fixed-height padding differs from natural `NextPowerOfTwo` padding when
the final subtree is short.

This is not carried by default (no known consumer needs it; the block header's
committed root plus per-subtree verification is sufficient). If required, it is
an additive `FirstSubtreeLeafCount` (uint64 BE) field — a **per-block** value,
never per subtree — appended after `SubtreeHashes`, gated by a `Flags` bit. Left
unspecified in the base object to keep it lean.

---

## 5. Relationship to BRC-131

BRC-146 is the BRC-131 `BlockAnnounce` (MsgType `0x01`) with the transport
envelope removed:

| Aspect        | BRC-131 BlockAnnounce (multicast)                | BRC-146 (non-multicast)                   |
| ------------- | ------------------------------------------------ | ----------------------------------------- |
| Header        | 92-byte frame; `FrameVer 0x04`, `MsgType 0x01`; block hash in `ContentID` | none — block hash computed from the header; tag `0x05` or bare |
| Flow / seq    | `HashKey` + `SeqNum` (proxy-stamped)             | none — TCP provides ordering              |
| Reliability   | BRC-126 NACK/retry                               | TCP (delivery); producer resend (ingest)  |
| Payload       | header ∥ coinbase TxID ∥ subtree count ∥ roots   | **identical** payload                     |
| Coinbase tx   | separate BRC-131 `MsgType 0x02` / BRC-143 object | separate BRC-143 object                   |

The coinbase *transaction* is a separate object (BRC-143); BRC-146 carries only
its TxID, exactly as BRC-131's `BlockAnnounce` does.

---

## References

- [BRC-131 — Block Announcement Frame Format](brc-131-block-announcements.md) (multicast counterpart)
- [BRC-135 — Multicast Block Header Format](brc-135-block-header-format.md) — the 80-byte header
- [BRC-145 — Subtree Object Format](brc-145-subtree-object.md) — the subtrees this block orders;
  block↔subtree association lives here, not in the subtree object
- [BRC-143 — Coinbase Object Format](brc-143-coinbase-object.md) (family lead; shared Stream Composition),
  [BRC-144 — Anchor Object Format](brc-144-anchor-object.md)
