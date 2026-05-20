# BRC-131 — Block Announcement Protocol

BRC-131 defines a new frame version (0x04) for distributing block-level metadata over the multicast fabric. Block announcements and coinbase transactions are delivered to all subscribers via a dedicated control-plane multicast group, independently of the shard groups used for transaction distribution.

---

## Purpose

The multicast fabric distributes transactions to sharded subscriber groups. Blocks involve two additional distribution needs:

1. **Block announcement** — subscribers must learn that a new block has been found and which subtree-root hashes it references, so they can update their block templates and gap-tracking state.
2. **Coinbase delivery** — the coinbase transaction is a singleton that every subscriber needs regardless of shard assignment.

Both payloads are small relative to a typical block and must reach every subscriber with the same reliability guarantees (sequence tracking, NACK-based retransmission) as BRC-124 transaction frames. BRC-131 reuses the BRC-124 header layout and control infrastructure for both, on a dedicated control-plane multicast group.

---

## Control-Plane Multicast Group

Block frames are sent to the **CtrlGroupControl** group:

| Index  | Scope  | Compressed Address | Constant            |
| ------ | ------ | ------------------ | ------------------- |
| 0xFFFE | global | `FF0E::B:FFFE`     | `CtrlGroupControl`  |

The global scope (`FF0E`) ensures block announcements cross site boundaries and reach all geographically distributed subscribers. The group index `0xFFFE` is in the reserved control-plane range (above the maximum shard group index `0x0FFF` for `shard_bits` ≤ 12).

---

## Frame Header Format (92 bytes)

The BRC-131 header is **layout-identical** to a BRC-124 header. Infrastructure components that inspect the Magic, HashKey, or SeqNum fields read correct values at the same offsets.

| Offset | Size | Align | Field          | Value / Notes                                             |
| ------ | ---- | ----- | -------------- | --------------------------------------------------------- |
| 0      | 4    | —     | Network Magic  | `0xE3E1F3E8` (BSV mainnet P2P magic)                      |
| 4      | 2    | —     | Protocol Ver   | `0x02BF` (703, BSV large-block baseline)                  |
| 6      | 1    | —     | Frame Version  | **`0x04`** — BRC-131 block control                        |
| 7      | 1    | —     | MsgType        | `0x01` = BlockAnnounce, `0x02` = CoinbaseTx               |
| 8      | 32   | 8B    | ContentID      | BlockHash (announce) or CoinbaseTxID (coinbase)           |
| 40     | 8    | 8B    | HashKey        | XXH64(senderIPv6 ∥ 0xFFFE ∥ zeros); stamped by proxy     |
| 48     | 8    | 8B    | SeqNum         | Per-sender monotonic counter; stamped by proxy            |
| 56     | 32   | 8B    | LayoutPad32    | All zeros. Keeps the header at 92 bytes so all infrastructure components share `HeaderSize`, one TCP read sequence, and one stamping path. |
| 88     | 4    | 8B    | PayloadLen     | Size of payload in bytes (uint32 BE)                      |
| 92     | \*   | —     | Payload        | MsgType-specific payload (see below)                      |

**Key distinction from BRC-124:** byte 7 carries `MsgType` rather than `Reserved=0x00`. The `LayoutPad32` field at bytes 56–87 is always zeros — block frames have no subtree scope, so there is no meaningful value to place here. The field exists as a deliberate layout trade-off: keeping the BRC-131 header at exactly 92 bytes means the proxy TCP reader (`read 44 bytes → detect FrameVer → read 48 more`), the stamping path, the listener, and the retry endpoint all share the single `HeaderSize` constant with no special-casing. The cost is 32 zero bytes per frame, which is negligible relative to any real payload.

---

## MsgType Values

| MsgType | Constant           | Payload Format                       |
| ------- | ------------------ | ------------------------------------ |
| `0x01`  | `BlockMsgAnnounce` | BlockAnnounce payload (see §BlockAnnounce Payload) |
| `0x02`  | `BlockMsgCoinbase` | Raw coinbase transaction bytes       |

Any other MsgType value causes the frame to be rejected with `ErrBadBlockMsg`.

---

## BlockAnnounce Payload

The payload for a `BlockAnnounce` frame (`MsgType=0x01`) is structured as follows:

| Offset | Size        | Field          | Description                                             |
| ------ | ----------- | -------------- | ------------------------------------------------------- |
| 0      | 80          | BlockHeader    | Standard 80-byte BSV block header                       |
| 80     | 32          | CoinbaseTxID   | SHA256d of the coinbase transaction (internal byte order)|
| 112    | 4           | SubtreeCount   | Number of subtree root hashes (uint32 BE); may be 0     |
| 116    | 32 × N      | SubtreeHashes  | Ordered subtree root hashes (each 32 bytes)             |

**Minimum payload size:** 116 bytes (header + CoinbaseTxID + SubtreeCount field with N=0).

**ContentID:** The `ContentID` field in the frame header carries the 32-byte block hash (internal byte order, identical to how TxIDs are stored in BRC-124).

**SubtreeHashes:** The ordered list of Merkle roots of the sharded transaction subtrees included in this block. Subscribers use these to verify their received transactions against the block. The ordering matches the producer's subtree enumeration; the count may be zero for empty blocks.

---

## CoinbaseTx Payload

The payload for a `CoinbaseTx` frame (`MsgType=0x02`) is the raw serialized coinbase transaction — the same encoding as a BRC-12 transaction payload (version LE32 + inputs + outputs + locktime LE32), with no additional envelope.

**ContentID:** The `ContentID` field carries the SHA256d of the raw coinbase transaction bytes — i.e., the CoinbaseTxID.

---

## Sequence Tracking and Retransmission

BRC-131 frames participate in the same NACK-based reliability mechanism as BRC-124 frames:

- The proxy stamps `HashKey` and `SeqNum` in-place before forwarding. `HashKey` is computed as `XXH64(senderIPv6 ∥ ctrlGroupIdx ∥ zeroSubtreeID)` where `ctrlGroupIdx = 0xFFFE`. The all-zero subtree input reflects that block frames have no subtree scope; the `LayoutPad32` field on the wire is the visual counterpart of that. `SeqNum` is a monotonic per-sender counter.
- If `SeqNum` is already non-zero when the proxy receives the frame, it is forwarded verbatim (pre-stamped path).
- Listeners detect gaps by comparing consecutive `SeqNum` values on the `(HashKey, ctrlGroupIdx, zeroSubtreeID)` flow and dispatch BRC-126 NACKs to retry endpoints.
- Retry endpoints join the `FF0E::B:FFFE` group and cache all BRC-131 frames by `HashKey ∥ SeqNum`. On NACK, the retransmitted frame is sent back to `FF0E::B:FFFE` (the control group), not to a shard group.

---

## Fragmentation

When the payload exceeds the path MTU, the proxy fragments the frame using BRC-130. The BRC-130 fragment header at bytes 0–91 is populated as for a normal BRC-131 frame (Magic, HashKey, SeqNum). The `OrigFrameVer` field at byte 100 of the BRC-130 header is set to `0x04` so the reassembler can reconstruct the correct frame version. The `MsgType` byte is preserved in the BRC-130 fragment's byte 7.

Block announcements for typical blocks (80-byte header + CoinbaseTxID + a few hundred subtree hashes) fit well within a 9000-byte jumbo frame and do not require fragmentation in practice. Fragmentation is relevant primarily for `CoinbaseTx` frames carrying large coinbase transactions.

---

## Proxy Forwarding Rules

1. **Receive** — BRC-131 frames are accepted over TCP ingress (same 92-byte header read sequence as BRC-124: read 44 bytes, detect `FrameVer=0x04`, read 48 more, read `PayLen` bytes).
2. **Decode** — `DecodeBlock` validates Magic, FrameVer, MsgType, and PayLen. Invalid frames are dropped.
3. **Stamp** — If `SeqNum == 0`, the proxy stamps `HashKey` and `SeqNum` in-place per `(senderIPv6, 0xFFFE, zeros)` flow.
4. **Fragment** — If `len(Payload) > fragDataSize`, fragment via BRC-130 with `OrigFrameVer=0x04`.
5. **Forward** — Write the frame to all egress interfaces with destination `FF0E::B:FFFE:<egressPort>`.

---

## Listener Processing Rules

1. **Detection** — `IsBlockFrame(raw)` checks Magic and `raw[6] == 0x04` before `frame.Decode` is called (which rejects V4 with `ErrBadVer`).
2. **Decode** — `DecodeBlock` validates the frame and returns a `BlockFrame` with `MsgType`, `ContentID`, `HashKey`, `SeqNum`, and `Payload`.
3. **Egress** — The frame (or payload, in strip-header mode) is forwarded to the configured downstream via `Sender.SendBlock`.
4. **Gap tracking** — `Tracker.Observe(ctrlGroupIdx=0xFFFE, zeroSubtreeID, HashKey, SeqNum, ContentID)` is called when `SeqNum != 0`.
5. **Filtering** — Block frames bypass shard/subtree filtering; every subscriber receives every block announcement.

---

## Retry Endpoint Behaviour

- **Group join** — On startup, the retry endpoint joins `FF0E::B:FFFE` in addition to all shard groups.
- **Cache** — BRC-131 frames are cached by `HashKey ∥ SeqNum` with the same TTL as BRC-124 frames.
- **Retransmission routing** — On NACK, `FrameVer` is inspected: if `raw[6] == 0x04`, the frame is retransmitted to `FF0E::B:FFFE` rather than to the shard group derived from ContentID.

---

## Infrastructure Impact

| Component              | Change                                                                                        |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| bitcoin-shard-proxy    | TCP case extended to `FrameVerV4`; new `ProcessBlock` + `fragmentBlock` methods              |
| bitcoin-shard-listener | Joins `FF0E::B:FFFE`; `processBlockFrame` + `egress.SendBlock`; gap tracking on ctrl flow    |
| bitcoin-retry-endpoint | Joins `FF0E::B:FFFE`; `processBlockFrame` in ingress; V4-aware retransmitter routing         |
| bitcoin-shard-common   | `FrameVerV4`, `BlockMsgAnnounce`, `BlockMsgCoinbase` constants; `BlockFrame`, `BlockAnnouncePayload` structs; `OrigFrameVer` field in BRC-130 fragment header |
| Firewall               | No additional rules — `FF0E::B:FFFE` uses the same port as shard groups                      |

---

## Error Handling

| Condition                      | Action                                         |
| ------------------------------ | ---------------------------------------------- |
| `raw[6] != 0x04`               | Not BRC-131; handled by other decoders         |
| Bad magic                      | Silent drop                                    |
| Unknown MsgType                | Drop; `ErrBadBlockMsg`                         |
| PayloadLen exceeds buffer      | Drop; `io.ErrUnexpectedEOF`                    |
| Datagram shorter than 92 bytes | Drop; `ErrTooShort`                            |
| SeqNum == 0                    | Frame not yet proxy-stamped; listener discards |

---

## Constants Reference

| Name                  | Value  | Hex    | Description                                       |
| --------------------- | ------ | ------ | ------------------------------------------------- |
| `FrameVerV4`          | 4      | `0x04` | BRC-131 block control frame version               |
| `BlockMsgAnnounce`    | 1      | `0x01` | MsgType: block header + subtree hashes            |
| `BlockMsgCoinbase`    | 2      | `0x02` | MsgType: raw coinbase transaction                 |
| `CtrlGroupControl`    | 65534  | `0xFFFE` | Block control multicast group index             |
| `BlockHeaderSize`     | 80     | `0x50` | Standard BSV block header size in bytes           |
| `BlockAnnounceMinPayload` | 116 | `0x74` | Minimum BlockAnnounce payload (N=0 subtrees)    |
| `HeaderSize`          | 92     | `0x5C` | BRC-131 header size (identical to BRC-124)        |

---

## References

- [BRC-124: Multicast Transaction Frame Format](brc-124-frame-format.md) — base header layout reused by BRC-131
- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md) — NACK/ACK/MISS used for block frame retransmission
- [BRC-129: Multicast Group Address Assignments](brc-129-multicast-addressing.md) — control-plane group index allocations
- [BRC-130: Fragmentation](brc-130-fragmentation.md) — BRC-130 extension for large block payloads; `OrigFrameVer=0x04`
- [bitcoin-shard-common/frame](https://github.com/lightwebinc/bitcoin-shard-common/tree/main/frame) — `EncodeBlock`, `DecodeBlock`, `IsBlockFrame`, `EncodeBlockAnnounce`, `DecodeBlockAnnounce`
- [bitcoin-shard-proxy/forwarder](https://github.com/lightwebinc/bitcoin-shard-proxy/tree/main/forwarder) — `ProcessBlock`, `fragmentBlock`
- [bitcoin-shard-listener/listener](https://github.com/lightwebinc/bitcoin-shard-listener/tree/main/listener) — `processBlockFrame`, `egress.SendBlock`
- [bitcoin-retry-endpoint/ingress](https://github.com/lightwebinc/bitcoin-retry-endpoint/tree/main/ingress) — `processBlockFrame`
- [bitcoin-retry-endpoint/retransmit](https://github.com/lightwebinc/bitcoin-retry-endpoint/tree/main/retransmit) — V4-aware retransmit routing
