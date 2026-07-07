# BRC-143 — Subtree Data Frame Format (push)

BRC-143 defines the **push** wire form of a subtree: the merkle root that
identifies it plus the ordered list of transaction node hashes, delivered whole
over a byte stream. It is the counterpart of [BRC-132](brc-132-subtree-data.md)
(subtree data over the multicast fabric) for a consumer reached by **push**
(e.g. round-robin SDA delivery over a tunnel) rather than announce/pull.

> **Status: PROPOSED.** Upstream `bitcoin-sv/BRCs`
> [PR #175](https://github.com/bsv-blockchain/BRCs/pull/175). Finalized against
> **go-subtree v1.4.2** (the version Teranode HEAD pins) and Teranode's
> `subtreevalidation` consumer. Canonical bytes live in the upstream BRC; this
> doc is the local design mirror.

---

## 1. Purpose

In an announce/pull system a receiver fetches a subtree **by its root hash** —
Teranode's own `GET /subtree/{hash}` transfers a bare list of 32-byte node
hashes and the root is the request key, carried out-of-band in the URL. A
**push** delivery has no request path: the subtree arrives unsolicited, so the
identifying root must travel **in-band**.

BRC-143 is that frame — the merkle root followed by the ordered node hashes, and
nothing a receiver recomputes locally. Per-node fee/size, the aggregate totals,
and the conflict set are deliberately omitted: a receiver's transaction-meta
store supplies fee/size (Teranode's `AddNode(hash,0,0)` sources them locally),
and validation does not consume the others on the ingest path — carrying them
would only inflate an already large object.

---

## 2. Wire Format

All frame integers are big-endian. Node hashes are carried in internal
(`chainhash`) byte order — not display order. `NodeCount` delimits the body, so
the frame is self-delimiting on a back-to-back stream with no outer prefix.

| Offset | Size   | Field              | Notes                                                                 |
| ------ | ------ | ------------------ | --------------------------------------------------------------------- |
| 0      | 32     | `SubtreeMerkleRoot`| Merkle root of the node hashes: subtree identity and verify target.   |
| 32     | 8      | `NodeCount` (N)    | uint64 BE. Number of node hashes that follow. Delimits the body.      |
| 40     | 32 × N | `NodeHashes`       | Ordered node hashes, each 32 bytes. Includes the coinbase placeholder.|

Header: **40 bytes**. `NodeCount` is uint64 for parity with go-subtree (subtree
size is uncapped; Teranode's `subtreevalidation_max_incoming_subtree_bytes`
default 128 MiB ≈ 4M leaves).

### 2.1 Coinbase placeholder

A block's **first** subtree begins with a coinbase placeholder: **32 bytes of
`0xFF`** at `NodeHashes[0]` — *not* zeros (a zero hash is the merkle padding
value; zeros where the coinbase leaf belongs corrupts the root). The placeholder
is self-describing and detected **by value** (`CoinbasePlaceholderHashValue`),
exactly as Teranode's `subtreevalidation` does. There is **no flag**. A
non-first subtree carries no placeholder.

---

## 3. Reconstruction and verification

The receiver rebuilds the subtree through the go-subtree node API, then verifies:

- For each hash in order: if it equals `0xFF × 32`, insert as the **coinbase
  node** (`AddCoinbaseNode`); otherwise `AddNode(hash, fee, size)` with fee/size
  from the local transaction-meta store (or zero when only the root is checked).
- Assert the recomputed root equals `SubtreeMerkleRoot`.

Tree height is derived as `ceil(log2(N))`; the merkle computation pads to the
next power of two with zero-hash leaves, and an odd node is hashed with itself.
No height/capacity field is carried — it would fight that derivation.

**Do not byte-splice.** This wire frame is **not** go-subtree's on-disk
serialization (little-endian, root-first, with fee/size node records and a
conflict set). Rebuild via the API; never memcpy these bytes into
`Deserialize()`.

### 3.1 Member transactions

A subtree references its members by hash and does **not** carry them. A receiver
validating the subtree also needs the member transaction bytes (script/UTXO/
parent checks), supplied from the receiver's own transaction feed or fetched out
of band. BRC-143 proper is hashes-only.

---

## 4. Relationship to BRC-132

BRC-143 is the push counterpart of the multicast [BRC-132](brc-132-subtree-data.md)
subtree frame: the same node set, carried without the multicast envelope, with
the merkle root (which rode the BRC-132 frame header as `SubtreeID`) moved
in-band because push has no `/{hash}` request path. Fees/size/conflicts that
BRC-132 may carry are dropped — the receiver recomputes them.

---

## References

- [BRC-144 — Block Frame Format](brc-144-block-frame.md) — carries the ordered
  subtree list (block↔subtree association) this frame omits
- [BRC-132 — Multicast Subtree Data Frame Format](brc-132-subtree-data.md)
  (multicast counterpart)
- BRC-12 / BRC-30 — the member transactions a subtree references by hash
