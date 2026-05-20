# BRC-133 — Coinbase Transaction Delivery

BRC-133 defines the policy and wire mechanism for distributing coinbase
transactions over the multicast fabric. Coinbase transactions are carried as a
dedicated message type within BRC-131 block control frames, delivered to all
subscribers via the control-plane multicast group independently of the shard
groups used for ordinary transaction distribution.

---

## Purpose

The multicast fabric shards ordinary transactions across per-shard multicast
groups based on the top bits of each transaction's TxID. Coinbase transactions
cannot be sharded this way: they are a singleton per block and every subscriber
needs them regardless of shard assignment, because:

- The coinbase TxID is included in every `BlockAnnounce` (BRC-131, MsgType
  `0x01`) and is required to verify the block's Merkle root.
- Coinbase outputs pay the block subsidy and any miner fee aggregations that
  downstream systems may need to validate template construction.
- No subscriber can predict which shard a coinbase transaction's TxID would hash
  to before the block is found.

---

## Control-Plane Multicast Group

Coinbase frames are delivered on the **CtrlGroupControl** group:

| Index  | Scope  | Compressed Address | Constant           |
| ------ | ------ | ------------------ | ------------------ |
| 0xFFFE | global | `FF0E::B:FFFE`     | `CtrlGroupControl` |

The global scope (`FF0E`) ensures coinbase transactions cross site boundaries.
The group index `0xFFFE` is in the reserved control-plane range and never
overlaps with data-plane shard groups (maximum shard group index is `0x7FFF` for
`shard_bits=15`).

---

## Wire Format

Coinbase transactions are carried as **BRC-131 frames** (FrameVer `0x04`) with
`MsgType = 0x02` (`BlockMsgCoinbase`). The full BRC-131 header format is
specified in
[BRC-131](./brc-131-block-announcements.md). The
relevant fields are:

| Offset | Size | Field        | Value for Coinbase                                    |
| ------ | ---- | ------------ | ----------------------------------------------------- |
| 6      | 1    | FrameVersion | `0x04` (BRC-131)                                      |
| 7      | 1    | MsgType      | `0x02` = `BlockMsgCoinbase`                           |
| 8      | 32   | ContentID    | CoinbaseTxID — SHA256d of the raw coinbase tx bytes   |
| 40     | 8    | HashKey      | XXH64(senderIPv6 ∥ 0xFFFE ∥ zeros); stamped by proxy  |
| 48     | 8    | SeqNum       | Monotonic per-sender counter; stamped by proxy        |
| 56     | 32   | LayoutPad32  | All zeros                                             |
| 88     | 4    | PayloadLen   | Length of the raw coinbase transaction bytes          |
| 92     | \*   | Payload      | Raw serialised coinbase transaction (no P2P envelope) |

**Payload encoding:** The payload is the raw BSV serialised transaction —
identical to the encoding used in BRC-12/BRC-124 transaction frames: version (4
bytes LE), input vector, output vector, locktime (4 bytes LE).

---

## Sequencing and Retransmission

Coinbase frames participate in the same NACK-based reliability mechanism as
BRC-124 shard frames:

- The proxy stamps `HashKey = XXH64(senderIPv6 ∥ 0xFFFE ∥ zeros)` and `SeqNum`
  (monotonic per sender) in-place before forwarding. If the frame arrives
  pre-stamped (`SeqNum != 0`), it is forwarded verbatim.
- Listeners observe
  `(ctrlGroupIdx=0xFFFE, zeroSubtreeID, HashKey, SeqNum, ContentID)` for gap
  detection and dispatch BRC-126 NACKs to retry endpoints on gap.
- Retry endpoints join `FF0E::B:FFFE` and cache BRC-131 `BlockMsgCoinbase`
  frames by `HashKey ∥ SeqNum`. On NACK, the frame is retransmitted to
  `FF0E::B:FFFE` (not to any shard group).

---

## Relationship to BRC-131 Block Announcements

A `BlockAnnounce` frame (BRC-131, MsgType `0x01`) is sent first and carries the
`CoinbaseTxID` in its payload. The separate `BlockMsgCoinbase` frame then
carries the full raw coinbase bytes. Subscribers that only need to verify the
Merkle root may use the `CoinbaseTxID` from the announce frame without waiting
for the coinbase frame itself.

The two frame types share the same `HashKey` per sender but have independent
`SeqNum` sequences (they are distinct flows within the `(sender, 0xFFFE, zeros)`
namespace because their ContentIDs differ). Gap tracking treats them as separate
objects.

---

## Proxy Forwarding Rules

1. **Receive** — BRC-131 frames (FrameVer `0x04`) are accepted over UDP or TCP
   ingress. The proxy detects version byte `0x04` before dispatching to
   `ProcessBlock`.
2. **Decode** — `frame.DecodeBlock` validates Magic, FrameVer, MsgType, and
   PayLen. Invalid MsgType values (`!= 0x01` and `!= 0x02`) are dropped.
3. **Stamp** — If `SeqNum == 0`, stamp HashKey and SeqNum in-place using
   `(senderIPv6, 0xFFFE, zeros)` flow key.
4. **Fragment** — If `len(Payload) > fragDataSize`, fragment via BRC-130 with
   `OrigFrameVer = 0x04`.
5. **Forward** — Write frame to `FF0E::B:FFFE:<egressPort>` on all egress
   interfaces.

---

## Listener Processing Rules

1. **Detection** — `frame.IsBlockFrame(raw)` checks Magic and `raw[6] == 0x04`.
2. **Decode** — `frame.DecodeBlock` returns a `BlockFrame` with `MsgType`,
   `ContentID`, `HashKey`, `SeqNum`, and `Payload`.
3. **Egress** — `egress.Sender.SendBlock(raw, bf)` forwards the frame (or
   payload only in strip-header mode) downstream.
4. **Gap tracking** —
   `Tracker.Observe(0xFFFE, zeroSubtreeID, HashKey, SeqNum, ContentID)` when
   `SeqNum != 0`.
5. **Filtering** — Coinbase frames bypass all shard/subtree filters; every
   subscriber receives every coinbase frame.

---

## Infrastructure Impact

| Component              | Change                                                                            |
| ---------------------- | --------------------------------------------------------------------------------- |
| bitcoin-shard-proxy    | `ProcessBlock` handles MsgType `0x02`; routes to `CtrlGroupControl`               |
| bitcoin-shard-listener | `processBlockFrame` handles MsgType `0x02`; gap tracking on ctrl flow             |
| bitcoin-retry-endpoint | Joins `FF0E::B:FFFE`; caches and retransmits BRC-131 frames regardless of MsgType |
| bitcoin-shard-common   | `BlockMsgCoinbase = 0x02` constant; `DecodeBlock` validates MsgType               |

---

## Constants Reference

| Name               | Value    | Description                         |
| ------------------ | -------- | ----------------------------------- |
| `FrameVerV4`       | `0x04`   | BRC-131 block control frame version |
| `BlockMsgCoinbase` | `0x02`   | MsgType: raw coinbase transaction   |
| `CtrlGroupControl` | `0xFFFE` | Block control multicast group index |

---

## References

- [BRC-124: Multicast Transaction Frame Format](./brc-124-frame-format.md)
  — base header layout reused by BRC-131
- [BRC-129: Multicast Group Address Assignments](./brc-129-multicast-addressing.md)
  — control-plane group index allocations
- [BRC-130: Fragmentation](./brc-130-fragmentation.md) —
  BRC-130 extension for large coinbase payloads
- [BRC-131: Block Announcement Protocol](./brc-131-block-announcements.md)
  — full BRC-131 header format and BlockAnnounce payload
- [BRC-134: Chained Anchor Transaction Frames](brc-134-anchor-transactions.md) —
  another control-group transaction type
