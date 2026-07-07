# BRC-144 — Block Frame Format (push)

BRC-144 defines the **push** wire form of a block: the 80-byte header, block-level
counts, the ordered subtree roots the block references, the full coinbase
transaction, the block height, and the coinbase merkle path — delivered whole
over a byte stream so a receiver can assemble and validate the block with no
follow-up fetch. It is the push counterpart of the `BlockAnnounce` payload in
[BRC-131](brc-131-block-announcements.md).

> **Status: PROPOSED.** Upstream `bitcoin-sv/BRCs`
> [PR #176](https://github.com/bsv-blockchain/BRCs/pull/176). Field sequence is
> **strict parity** with Teranode's `model.Block.Bytes()` (HEAD 2026-07-06),
> each VarInt replaced by a fixed-width big-endian field. Canonical bytes live
> in the upstream BRC; this doc is the local design mirror.

---

## 1. Purpose

A block commits to its transactions through the header and names the subtrees it
references **in order** — the block↔subtree association that binds independently
delivered subtrees ([BRC-143](brc-143-subtree-data.md)) into a block and fixes
their order for merkle assembly.

In an announce/pull system a node is *notified* of a block (hash, height, a fetch
URL) and pulls the full block on demand. A **push** delivery has no fetch URL:
the block arrives whole and unsolicited, so the frame carries the complete block
body inline. Its field sequence mirrors the block body Teranode already
serialises/ingests (`model.Block.Bytes()` → `ProcessBlock`), with each
variable-length integer replaced by a fixed-width big-endian field.

The **coinbase is carried in-band** because it cannot be delivered as a loose
transaction — Teranode rejects a standalone coinbase on its transaction path
(`propagation/Server.go`) — and block assembly needs its bytes.

---

## 2. Wire Format

All frame integers are big-endian. Hashes are internal byte order. The embedded
block header, coinbase transaction, and coinbase BUMP keep their native
serialisations.

| Size   | Field              | Notes                                                             |
| ------ | ------------------ | ----------------------------------------------------------------- |
| 80     | `BlockHeader`      | Standard 80-byte block header (§2.1).                             |
| 8      | `TransactionCount` | uint64 BE. Total transactions committed by the block.            |
| 8      | `SizeInBytes`      | uint64 BE. Total serialized block size.                          |
| 8      | `SubtreeCount` (M) | uint64 BE. Number of subtree roots that follow.                  |
| 32 × M | `SubtreeHashes`    | Ordered subtree merkle roots, each 32 bytes.                     |
| \*     | `Coinbase`         | Full coinbase transaction (BRC-12), self-delimiting by structure.|
| 8      | `Height`           | uint64 BE. Block height.                                         |
| 8      | `CoinbaseBUMPLen`  | uint64 BE. Byte length of the coinbase BUMP that follows.        |
| \*     | `CoinbaseBUMP`     | BRC-74 merkle path of the coinbase; present only when len > 0.   |

Fixed prefix through `SubtreeCount` is **104 bytes**; `SubtreeHashes`,
`Coinbase`, and `CoinbaseBUMP` are variable. The `Coinbase` has **no length
prefix** — it is self-delimiting by transaction structure (as in the native
block body), so a reader parses it and resumes at `Height`.

### 2.1 Block header (80 bytes)

Standard block header, consensus byte layout: 4B Version (LE) ∥ 32B previous
block hash ∥ 32B merkle root ∥ 4B Timestamp (LE) ∥ 4B nBits (LE) ∥ 4B Nonce
(LE). `BlockHash = SHA256d(BlockHeader)`; the previous-block hash gives chain
context intrinsically. Block height is not in the header (BIP-34 coinbase) and
is carried explicitly as `Height` so the receiver need not extract it.

---

## 3. Coinbase and subtree association

`SubtreeHashes` is ordered to match the producer's subtree enumeration — the
order used to assemble the block merkle root from the subtree roots. A block's
**first** subtree ([BRC-143](brc-143-subtree-data.md)) carries the `0xFF × 32`
coinbase placeholder at its first node; the receiver substitutes the coinbase
transaction carried here into that slot on reconstruction.

The coinbase is the full transaction, not just its `TxID`: a node rejects a loose
coinbase on its transaction-ingest path, so it is delivered only here and block
assembly consumes its bytes directly. This is why there is **no standalone
coinbase frame** — a coinbase off the fabric is a plain BRC-12 transaction that
only ever travels inside this block frame.

---

## 4. Relationship to BRC-131

BRC-144 is the push counterpart of the [BRC-131](brc-131-block-announcements.md)
`BlockAnnounce`. Where BRC-131 (mirroring an announcement) carries the header,
coinbase **TxID**, and subtree roots, BRC-144 carries the header, the **full
coinbase**, block counts, height, and the coinbase BUMP — everything
`ProcessBlock` needs — because a push receiver cannot pull the rest.

---

## References

- [BRC-143 — Subtree Data Frame Format](brc-143-subtree-data.md) — the subtrees
  this block orders; the block↔subtree association lives here
- [BRC-131 — Multicast Block Announcement Frame Format](brc-131-block-announcements.md)
  (multicast counterpart)
- BRC-74 — BSV Unified Merkle Path (BUMP), the `CoinbaseBUMP` payload
- BRC-12 — the coinbase transaction body
