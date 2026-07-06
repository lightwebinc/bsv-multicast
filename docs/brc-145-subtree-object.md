# BRC-145 — Subtree Object Format (Non-Multicast)

BRC-145 defines the **header-stripped** wire form of a subtree: the node-hash
list plus the minimal context a receiver needs to reconstruct and verify a
subtree object, with **no multicast frame header**. It is the non-multicast
counterpart of [BRC-132](brc-132-subtree-data.md) (subtree data over the
multicast fabric), and it composes inline with the BRC-143–146 family.

> **Status: DRAFT (design).** Member of the **BRC-143–146 non-multicast object
> family**; shared framing in
> [BRC-143 §2](brc-143-coinbase-object.md#2-stream-composition--inline-with-brc-12--brc-30);
> subtree tag `0x04`. Finalised against **go-subtree v1.4.2** (the version
> Teranode pins) and its consumers — the format mirrors what a node's subtree
> validation actually ingests. Local design doc; upstream `bitcoin-sv/BRCs` PR
> is future work.

---

## 1. Purpose

Stripping the multicast header off a BRC-132 frame is **not** sufficient to
deliver a usable subtree. BRC-132 carries the node set, but its identifying
merkle root rides the *frame header* (as `SubtreeID`) and is lost on strip, and
the object a node reconstructs needs two things a naive hash list omits:

- the **merkle root**, in-band — the object's identity and the value the
  receiver verifies its reconstruction against (the unicast object has no
  `/{hash}` request path to name it);
- a **coinbase-placeholder** signal — a block's first subtree begins with a
  placeholder leaf, not a real transaction, and mishandling it corrupts the
  root.

BRC-145 is the lean, verifiable object: **hashes-only plus the merkle root and
a placeholder flag**. It deliberately omits per-node fee/size, the aggregate
totals, and the conflict set — a node's subtree validation recomputes fee/size
from its own transaction-meta store and does not consume the conflict set on
the fetch path, so carrying them is dead weight. This mirrors exactly what a
node transfers when it fetches a subtree by hash: an ordered list of 32-byte
node hashes, nothing more.

---

## 2. Stream Composition

Per the family framing in
[BRC-143 §2](brc-143-coinbase-object.md#2-stream-composition--inline-with-brc-12--brc-30):
the subtree object rides a shared stream behind the 1-byte tag `0x04`, or bare
on a subtree-dedicated lane. Its body is **self-delimiting by `NodeCount`** —
the reader consumes the fixed header, then exactly `NodeCount × 32` hash bytes.

Subtrees are large (up to ~32 MB at 1M nodes) and so are delivered/ingested
over a stream (TCP), never a single datagram; a datagram transport would
require fragmentation (as BRC-132 does over multicast via BRC-130). The subtree
lane is independent of the transaction shards — a subtree object references its
members by hash and does not carry them (see §5).

---

## 3. Body Format

All multi-byte integers are big-endian (family convention). Fixed header, then
the ordered hash list:

| Offset | Size   | Field              | Notes                                                                                     |
| ------ | ------ | ------------------ | ----------------------------------------------------------------------------------------- |
| 0      | 32     | `SubtreeMerkleRoot`| Merkle root of the node hashes = the subtree identity and the receiver's verify target. Equals BRC-132's `SubtreeID`. |
| 32     | 1      | `Flags`            | bit0 `HasCoinbasePlaceholder` — this is a block's first subtree. bits 1–7 reserved (0).   |
| 33     | 4      | `NodeCount` (N)    | Number of node hashes that follow, including the placeholder (uint32 BE). Delimits the body. |
| 37     | 32 × N | `NodeHashes`       | Ordered node hashes (internal byte order). When `HasCoinbasePlaceholder`, `NodeHashes[0]` **MUST** be the coinbase placeholder (§4). |

There is **no tree-height / leaf-capacity field.** Capacity is derived from N:
the receiver treats the tree height as `ceil(log2(N))` and pads the merkle
computation to `NextPowerOfTwo(N)` with zero-hash leaves (odd nodes hashed with
themselves). An explicit height field would fight that derivation. (Fixed-height
composition is only used when folding an incomplete *final* subtree into a
*block* merkle root — a block-level concern that belongs to BRC-146, not to a
standalone subtree object.)

---

## 4. Coinbase Placeholder

The coinbase placeholder is **32 bytes of `0xFF`** — *not* zeros. Zeros is the
merkle *padding* value; placing zeros where the coinbase leaf belongs corrupts
the root. When `Flags.HasCoinbasePlaceholder` is set, `NodeHashes[0]` MUST
equal `0xFF × 32`.

The flag is advisory: the placeholder is self-describing at index 0 (a receiver
detects it by value, which is how a node's validator does), and the flag lets
the receiver cross-check its expectation against the data. A non-first subtree
sets neither the flag nor a placeholder leaf.

---

## 5. Reconstruction, Verification, and Member Coverage

**Reconstruct + verify.** The receiver builds a subtree object through the
subtree library's node API — for each hash, insert as the coinbase node when it
equals `0xFF × 32`, else as an ordinary node (with fee/size sourced locally, or
zero when only the root is being checked) — then asserts the computed merkle
root equals `SubtreeMerkleRoot`. **Hash byte order:** node hashes are carried
and inserted in the library's internal (`chainhash`) byte order; do **not**
reverse to display order.

**Do not byte-splice.** This wire object is **not** the subtree library's
on-disk serialisation (which is little-endian, root-hash-first, with 48-byte
`hash∥fee∥size` node records and a trailing conflict set). Reconstruct via the
API; never memcpy BRC-145 bytes into the library's deserialiser.

**Member transactions.** A subtree object references its members by hash and
does **not** carry the transactions. A node validating the subtree also needs
the member transaction bytes (for script/UTXO/parent checks). That is
round-trip-free only if the consumer already holds every member from its
transaction feed; where coverage is partial, the missing members are supplied
out of band — either a **SubtreeData companion** object (the concatenated raw
member transactions, coinbase slot skipped) or a pull fallback, sized to the
coverage gap. The companion is a distinct, additive object; BRC-145 proper is
the hashes-only form.

---

## 6. Relationship to BRC-132

| Aspect            | BRC-132 (multicast)                                   | BRC-145 (non-multicast)                          |
| ----------------- | ----------------------------------------------------- | ------------------------------------------------ |
| Header            | 92-byte frame; `SubtreeID` (root) in the header       | none — root carried **in-band** at offset 0; tag `0x04` or bare |
| Node encoding     | HashesOnly (32B) **or** FullNodes (32B∥fee8∥size8)    | **hashes-only** (fee/size omitted; node recomputes) |
| Totals / conflicts| 24-byte fee/size/count preamble + conflict set        | omitted (dead weight for validation)             |
| Fragmentation     | BRC-130 over multicast (payload > MTU)                | stream transport; no per-object fragmentation    |
| Reliability       | BRC-126 NACK/retry                                    | TCP (delivery); producer resend (ingest)         |

---

## References

- [BRC-132 — Subtree Data Frame Format](brc-132-subtree-data.md) (multicast counterpart)
- [BRC-146 — Block Object Format](brc-146-block-object.md) — carries the block↔subtree
  association (ordered subtree-hash list); the block context this object omits
- [BRC-143 — Coinbase Object Format](brc-143-coinbase-object.md) (family lead; shared Stream Composition)
- [BRC-127 — Subtree Group Announcement](brc-127-subtree-announce.md)
