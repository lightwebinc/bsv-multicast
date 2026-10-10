# BRC-131 — Block Announcement Frame Format

BRC-131 defines a new frame version (0x04) for distributing blocks over the multicast fabric. Block announcements and coinbase transactions are delivered to all subscribers via a dedicated control-plane multicast group, independently of the shard groups used for transaction distribution. Standalone coinbase delivery (`MsgType=0x02`) is deprecated and deliberately retained (see BRC-133): current implementations do not produce it, the coinbase travels inline in the BRC-144 block body, and the listener's default-on block-control gate drops a standalone coinbase frame. The message type stays reserved so that a future design can carry blocks and their coinbase separately on the fabric and recombine them at the edges.

> **Canonical spec:** [BRC-131](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0131.md).
> This document is the detailed design and rationale.

---

## Purpose

The multicast fabric distributes transactions to sharded subscriber groups. Blocks involve two additional distribution needs:

1. **Block announcement** — subscribers must learn that a new block has been found and receive the block itself. On this fabric the announcement and the block data are the same thing: the announcement payload is the complete BRC-144 block object (header, counts, subtree roots, full coinbase, height, coinbase BUMP).
2. **Coinbase delivery** — the coinbase transaction is a singleton that every subscriber needs regardless of shard assignment. *(Deprecated and retained per BRC-133; the coinbase travels inline in the BRC-144 block body.)*

Both must reach every subscriber with the same reliability guarantees (sequence tracking, NACK-based retransmission) as BRC-124 transaction frames. BRC-131 reuses the BRC-124 header layout and control infrastructure for both, on a dedicated control-plane multicast group.

---

## Control-Plane Multicast Group

Block frames are sent to the **GroupBlockBroadcast** group:

| Index  | Scope  | Compressed Address | Constant            |
| ------ | ------ | ------------------ | ------------------- |
| 0xFFFE | global | `FF0E::B:FFFE`     | `GroupBlockBroadcast`  |

The global scope (`FF0E`) ensures block announcements cross site boundaries and reach all geographically distributed subscribers. The group index `0xFFFE` is in the reserved control-plane range (above the maximum shard group index `0x0FFF` for `shard_bits` ≤ 12). `FF0E::B:FFFE` is the normative posture; the implementation derives the address prefix from the configured `-scope`.

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
| 40     | 8    | 8B    | HashKey        | XXH64(senderIPv6 ∥ flowIdx ∥ zeros); flowIdx = 0xFFFE (announce) or 0xFFF8 (coinbase); stamped by proxy |
| 48     | 8    | 8B    | SeqNum         | Per-sender monotonic counter; stamped by proxy            |
| 56     | 32   | 8B    | LayoutPad32    | All zeros. Keeps the header at 92 bytes so all infrastructure components share `HeaderSize`, one TCP read sequence, and one stamping path. |
| 88     | 4    | 8B    | PayloadLen     | Size of payload in bytes (uint32 BE)                      |
| 92     | \*   | —     | Payload        | MsgType-specific payload (see below)                      |

**Key distinction from BRC-124:** byte 7 carries `MsgType` rather than `Reserved=0x00`. The `LayoutPad32` field at bytes 56–87 is always zeros — block frames have no subtree scope, so there is no meaningful value to place here. The field exists as a deliberate layout trade-off: keeping the BRC-131 header at exactly 92 bytes means the proxy TCP reader (`read 44 bytes → detect FrameVer → read 48 more`), the stamping path, the listener, and the retry endpoint all share the single `HeaderSize` constant with no special-casing. The cost is 32 zero bytes per frame, which is negligible relative to any real payload.

---

## MsgType Values

| MsgType | Constant           | Payload Format                       |
| ------- | ------------------ | ------------------------------------ |
| `0x01`  | `BlockMsgAnnounce` | A BRC-144 block object, carried verbatim (see §BlockAnnounce Payload) |
| `0x02`  | `BlockMsgCoinbase` | Raw coinbase transaction bytes. Deprecated: not produced, dropped by default at the listener's block-control gate; reserved, never reused (see BRC-133) |

Any other MsgType value causes the frame to be rejected with `ErrBadBlockMsg`.

---

## BlockAnnounce Payload

The payload for a `BlockAnnounce` frame (`MsgType=0x01`) is a complete [BRC-144](brc-144-block-frame.md) block object, carried verbatim with no additional envelope:

| Size   | Field            | Description                                                      |
| ------ | ---------------- | ---------------------------------------------------------------- |
| 80     | BlockHeader      | Standard 80-byte BSV block header                                |
| 8      | TransactionCount | uint64 BE. Transactions committed by the block                   |
| 8      | SizeInBytes      | uint64 BE. Total serialized block size                           |
| 8      | SubtreeCount (M) | uint64 BE. Number of subtree roots that follow                   |
| 32 × M | SubtreeHashes    | Ordered subtree Merkle roots, each 32 bytes                      |
| \*     | Coinbase         | Full coinbase transaction (BRC-12), self-delimiting by structure |
| 8      | Height           | uint64 BE. Block height                                          |
| 8      | CoinbaseBUMPLen  | uint64 BE. Byte length of the coinbase BUMP that follows         |
| \*     | CoinbaseBUMP     | BRC-74 Merkle path of the coinbase; present only when `CoinbaseBUMPLen > 0` |

The fixed prefix through `SubtreeCount` is 104 bytes. The `Coinbase` carries no length prefix; a reader parses it by transaction structure and resumes at `Height`. Field definitions are normative in BRC-144.

**Minimum payload size:** 120 bytes plus the coinbase (M = 0, no BUMP). **Total:** 120 + 32 × M + `len(Coinbase)` + `CoinbaseBUMPLen`.

**ContentID:** the 32-byte block hash (internal byte order), verified as `SHA256d(payload[0:80])`. A block-header consumer (BRC-135) takes `payload[0:80]`.

**No separate coinbase identifier.** The coinbase itself is present; its id is `SHA256d(Coinbase)`. A payload that ends after the subtree hashes is malformed and MUST be rejected.

**Revision note.** Earlier revisions defined this payload as header + 32-byte CoinbaseTxID + `uint32` subtree count. No producer ever emitted that grammar; every producer emits the BRC-144 object (`shard-common/objfmt.MulticastBytes`, `ClassBlock`). Because the grammars share a prefix, a reader built to the old text misparses a zero-subtree block rather than failing cleanly, so a reader MUST treat the payload as a BRC-144 object. The legacy `frame.EncodeBlockAnnounce` / `DecodeBlockAnnounce` helpers remain only for the `send-test-frames` test tool.

---

## CoinbaseTx Payload

Standalone coinbase frames are deprecated per BRC-133: current implementations do not produce them, the listener's default-on block-control gate drops them (`bsl_frames_dropped_total{reason="coinbase_legacy"}`), and the coinbase travels inline in the BRC-144 block body. `MsgType 0x02` is retained deliberately and stays reserved, so that a future design can carry blocks and their coinbase separately on the fabric and recombine them at the edges. The wire format below remains the reference for that option, for deployments that disable the gate, and for decoding archived traffic.

The payload for a `CoinbaseTx` frame (`MsgType=0x02`) is the raw serialized coinbase transaction — the same encoding as a BRC-12 transaction payload (version LE32 + inputs + outputs + locktime LE32), with no additional envelope.

**ContentID:** The `ContentID` field carries the SHA256d of the raw coinbase transaction bytes — i.e., the CoinbaseTxID.

---

## Sequence Tracking and Retransmission

BRC-131 frames participate in the same NACK-based reliability mechanism as BRC-124 frames:

- The proxy stamps `HashKey` and `SeqNum` in-place before forwarding. `HashKey` is computed as `XXH64(senderIPv6 ∥ ctrlGroupIdx ∥ zeroSubtreeID)` where `ctrlGroupIdx = 0xFFFE` for `BlockAnnounce` and the virtual `GroupCoinbaseFlow` index `0xFFF8` for `CoinbaseTx` (BRC-133), so the two message types form independent flows. The all-zero subtree input reflects that block frames have no subtree scope; the `LayoutPad32` field on the wire is the visual counterpart of that. `SeqNum` is a monotonic per-sender counter.
- If `SeqNum` is already non-zero when the proxy receives the frame it is preserved (pre-stamped path); `HashKey` is still re-stamped from the observed source when the proxy runs `-stamp-source` (default on).
- Listeners detect gaps by comparing consecutive `SeqNum` values on the `(HashKey, ctrlGroupIdx, zeroSubtreeID)` flow and dispatch BRC-126 NACKs to retry endpoints.
- Retry endpoints join the `FF0E::B:FFFE` group and cache all BRC-131 frames by `HashKey ∥ SeqNum`. On NACK, the retransmitted frame is sent back to `FF0E::B:FFFE` (the control group), not to a shard group.

---

## Fragmentation

When the payload exceeds the path MTU, the proxy fragments the frame using BRC-130. The BRC-130 fragment header at bytes 0–91 is populated as for a normal BRC-131 frame (Magic, HashKey, SeqNum). The `OrigFrameVer` field at byte 100 of the BRC-130 header is set to `0x04` so the reassembler can reconstruct the correct frame version. The `MsgType` byte is preserved in the BRC-130 fragment's byte 7.

A block announcement carries the whole block object, so its size follows the block: the fixed prefix and subtree list are small (104 + 32 × 128 = 4200 bytes for 128 subtrees), and the coinbase and its optional BUMP carry the rest. Announcements routinely exceed a 9000-byte jumbo frame, so fragmentation is the normal case.

---

## Proxy Forwarding Rules

1. **Receive** — BRC-131 frames are accepted over UDP or TCP ingress (privileged ingress class only; TCP uses the same 92-byte header read sequence as BRC-124: read 44 bytes, detect `FrameVer=0x04`, read 48 more, read `PayLen` bytes).
2. **Privileged-class admission** — V4 frames are rejected unless the ingress class is privileged (`rejectPrivileged` counter).
3. **Decode** — `DecodeBlock` validates Magic, FrameVer, MsgType, and PayLen. Invalid frames are dropped.
4. **PoW gate** — `BlockMsgAnnounce` payloads must pass the block-PoW check (`-require-block-pow`, default on).
5. **Dedup** — the ingress ContentID dedup claim drops duplicate ContentIDs.
6. **Stamp** — the proxy stamps `SeqNum` only when 0; `HashKey` is (re)stamped whenever it is zero or the proxy runs `-stamp-source`, per the `(senderIPv6, 0xFFFE, zeros)` flow for announces and `(senderIPv6, 0xFFF8, zeros)` for coinbase frames.
7. **Fragment** — If `len(Payload) > fragDataSize`, fragment via BRC-130 with `OrigFrameVer=0x04`.
8. **Forward** — Write the frame to all egress interfaces with destination `FF0E::B:FFFE:<egressPort>` (normative posture; the implementation derives the prefix from the configured `-scope`).

---

## Listener Processing Rules

1. **Detection** — `IsBlockFrame(raw)` checks Magic and `raw[6] == 0x04` before `frame.Decode` is called (which rejects V4 with `ErrBadVer`).
2. **Decode** — `DecodeBlock` validates the frame and returns a `BlockFrame` with `MsgType`, `ContentID`, `HashKey`, `SeqNum`, and `Payload`.
3. **Block-control gate** — `blockGateAllows` must admit the frame before delivery; gated-out frames are dropped. While the gate is on (the default), every deprecated `CoinbaseTx` frame is dropped here (`coinbase_legacy`).
4. **Egress** — The frame (or payload, in strip-header mode) is forwarded to the configured downstream via `Sender.SendBlock`.
5. **Gap tracking** — `Tracker.Observe(ctrlGroupIdx=0xFFFE (0xFFF8 for coinbase frames), zeroSubtreeID, HashKey, SeqNum, ContentID, source net.IP)` is called when `SeqNum != 0`.
6. **Filtering** — Block frames bypass shard/subtree filtering; every subscriber receives every block announcement.

---

## Retry Endpoint Behaviour

- **Group join** — On startup, the retry endpoint joins `FF0E::B:FFFE` in addition to all shard groups.
- **Cache** — BRC-131 frames are cached by `HashKey ∥ SeqNum` with the block-class TTL (default 10 minutes; tx frames default 60 s).
- **Retransmission routing** — On NACK, `FrameVer` is inspected: if `raw[6] == 0x04`, the frame is retransmitted to `FF0E::B:FFFE` rather than to the shard group derived from ContentID.

---

## Infrastructure Impact

| Component              | Change                                                                                        |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| shard-proxy    | TCP case extended to `FrameVerV4`; new `ProcessBlock` + `fragmentBlock` methods              |
| shard-listener | Joins `FF0E::B:FFFE`; `processBlockFrame` + `egress.SendBlock`; gap tracking on ctrl flow    |
| retry-endpoint | Joins `FF0E::B:FFFE`; `processBlockFrame` in ingress; V4-aware retransmitter routing         |
| shard-common   | `FrameVerV4`, `BlockMsgAnnounce`, `BlockMsgCoinbase` constants; `BlockFrame`, `BlockAnnouncePayload` structs; `OrigFrameVer` field in BRC-130 fragment header |
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
| SeqNum == 0                    | Frame not yet proxy-stamped; excluded from gap tracking but still forwarded |

---

## Constants Reference

| Name                  | Value  | Hex    | Description                                       |
| --------------------- | ------ | ------ | ------------------------------------------------- |
| `FrameVerV4`          | 4      | `0x04` | BRC-131 block control frame version               |
| `BlockMsgAnnounce`    | 1      | `0x01` | MsgType: block announcement (payload = BRC-144 object) |
| `BlockMsgCoinbase`    | 2      | `0x02` | MsgType: raw coinbase transaction (deprecated; reserved) |
| `GroupBlockBroadcast`    | 65534  | `0xFFFE` | Block control multicast group index             |
| `BlockHeaderSize`     | 80     | `0x50` | Standard BSV block header size in bytes           |
| `HeaderSize`          | 92     | `0x5C` | BRC-131 header size (identical to BRC-124)        |

---

## References

- [BRC-124: Multicast Transaction Frame Format](brc-124-frame-format.md) — base header layout reused by BRC-131
- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md) — NACK/ACK/MISS used for block frame retransmission
- [BRC-129: Multicast Group Address Assignments](brc-129-multicast-addressing.md) — control-plane group index allocations
- [BRC-130: Fragmentation](brc-130-fragmentation.md) — BRC-130 extension for large block payloads; `OrigFrameVer=0x04`
- [shard-common/frame](https://github.com/lightwebinc/shard-common/tree/main/frame) — `EncodeBlock`, `DecodeBlock`, `IsBlockFrame`, `EncodeBlockAnnounce`, `DecodeBlockAnnounce` (legacy test grammar)
- [shard-common/objfmt](https://github.com/lightwebinc/shard-common/tree/main/objfmt) — `MulticastBytes` (`ClassBlock` wraps the BRC-144 object verbatim)
- [shard-proxy/forwarder](https://github.com/lightwebinc/shard-proxy/tree/main/forwarder) — `ProcessBlock`, `fragmentBlock`
- [shard-listener/listener](https://github.com/lightwebinc/shard-listener/tree/main/listener) — `processBlockFrame`, `egress.SendBlock`
- [retry-endpoint/ingress](https://github.com/lightwebinc/retry-endpoint/tree/main/ingress) — `processBlockFrame`
- [retry-endpoint/retransmit](https://github.com/lightwebinc/retry-endpoint/tree/main/retransmit) — V4-aware retransmit routing
- [BRC-131: Multicast Block Announcement Frame Format](https://github.com/bsv-blockchain/BRCs/blob/master/transactions/0131.md) — canonical spec
