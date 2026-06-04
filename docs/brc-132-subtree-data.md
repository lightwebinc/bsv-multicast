# BRC-132 — Subtree Data Frame Format

BRC-132 defines a new frame version (0x05) for distributing complete subtree data payloads (transaction hashes and metadata) over the multicast fabric. Subtree data is delivered to all subscribers via the dedicated `GroupSubtreeDataAnnounce` multicast group (`FF0X::B:FFFB`), independently of the shard groups used for individual transaction distribution.

> **Canonical BRC:** [BRC-132](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0132.md)

---

## Purpose

The multicast fabric already distributes transactions shard-by-shard (BRC-124) and block-level metadata (BRC-131). BRC-132 fills the remaining gap: delivering the _contents_ of each Merkle subtree so that subscribers can:

1. Reconstruct the subtree Merkle tree locally.
2. Verify block inclusion without fetching individual transactions.
3. Power downstream analytics and block-assembly tooling without libp2p retrieval.

BRC-132 coexists with BRC-127 (subtree group announcements, `FF0X::B:FFFC`), which maps SubtreeIDs to GroupIDs for filter purposes on a separate multicast group.

---

## Control-Plane Multicast Group

Subtree data frames are sent to the **GroupSubtreeDataAnnounce** group:

| Index  | Scope      | Compressed Address | Constant                      |
| ------ | ---------- | ------------------ | ----------------------------- |
| 0xFFFB | site       | `FF05::B:FFFB`     | `GroupSubtreeDataAnnounce`    |
| 0xFFFB | org        | `FF08::B:FFFB`     | `GroupSubtreeDataAnnounce`    |
| 0xFFFB | global     | `FF0E::B:FFFB`     | `GroupSubtreeDataAnnounce`    |

Scope selection mirrors the `GroupBeacon` pattern; operators choose one or more scopes via `-announce-scope` on listening components.

---

## Frame Header Format (92 bytes)

The BRC-132 header is **layout-identical** to a BRC-124 header. All infrastructure components that inspect Magic, HashKey, or SeqNum read correct values at the same offsets.

| Offset | Size | Align | Field          | Value / Notes                                              |
| ------ | ---- | ----- | -------------- | ---------------------------------------------------------- |
| 0      | 4    | —     | Network Magic  | `0xE3E1F3E8` (BSV mainnet P2P magic)                       |
| 4      | 2    | —     | Protocol Ver   | `0x02BF` (703, BSV large-block baseline)                   |
| 6      | 1    | —     | Frame Version  | **`0x05`** — BRC-132 subtree data                          |
| 7      | 1    | —     | MsgType        | `0x01` = HashesOnly, `0x02` = FullNodes                    |
| 8      | 32   | 8B    | SubtreeID      | SHA-256 Merkle root hash (content identifier)              |
| 40     | 8    | 8B    | HashKey        | `XXH64(senderIPv6 ∥ 0xFFFB ∥ subtreeID)`; stamped by proxy|
| 48     | 8    | 8B    | SeqNum         | Monotonic per `(sender, subtreeID)` counter; stamped by proxy |
| 56     | 32   | 8B    | LayoutPad32    | All zeros (field retained for uniform `HeaderSize`)        |
| 88     | 4    | 8B    | PayloadLen     | Size of payload in bytes (uint32 BE)                       |
| 92     | \*   | —     | Payload        | MsgType-specific subtree data payload (see §Payload Format)|

**Key distinctions from BRC-124:**
- Byte 7 carries `MsgType` rather than `Reserved=0x00`.
- Bytes 8–39 carry `SubtreeID` (the Merkle root, the content identifier), not a `TxID`.
- `LayoutPad32` at bytes 56–87 is always zeros. For BRC-132, the SubtreeID serves as both content identifier and flow scope; there is no secondary field. The field is retained to keep `HeaderSize = 92` uniform across V2/V4/V5.
- `HashKey` is computed as `XXH64(senderIPv6 ∥ 0xFFFB ∥ subtreeID)`. Each distinct subtree from the same sender owns an independent sequence stream, so loss in one subtree cannot create false gaps in another.

---

## MsgType Values

| MsgType | Constant              | Node size | Description                            |
| ------- | --------------------- | --------- | -------------------------------------- |
| `0x01`  | `SubtreeMsgHashesOnly`| 32 bytes  | TxHashes only (network transfer format)|
| `0x02`  | `SubtreeMsgFullNodes` | 48 bytes  | TxHash + Fee + Size per node           |

Any other MsgType value causes the frame to be rejected with `ErrBadSubtreeMsg`.

---

## Payload Format

Both formats share a fixed 24-byte metadata prefix followed by N node entries then a conflict set.

### Common prefix (24 bytes)

| Offset | Size | Field          | Description                              |
| ------ | ---- | -------------- | ---------------------------------------- |
| 0      | 8    | TotalFees      | Aggregate fee sum for the subtree (uint64 BE, satoshis) |
| 8      | 8    | TotalSizeBytes | Aggregate serialised tx size (uint64 BE, bytes) |
| 16     | 8    | NodeCount      | Number of transaction nodes (uint64 BE)  |

### MsgType 0x01 — HashesOnly

| Offset       | Size    | Field         |
| ------------ | ------- | ------------- |
| 24           | 32 × N  | TxHashes      |
| 24 + 32N     | 8       | ConflictCount (uint64 BE) |
| 24 + 32N + 8 | 32 × M  | ConflictHashes|

Size at 1M nodes, 0 conflicts: 24 + 32 × 1,048,576 + 8 = **~32 MB**

### MsgType 0x02 — FullNodes

| Offset       | Size    | Field       |
| ------------ | ------- | ----------- |
| 24           | 48 × N  | Nodes: TxHash(32B) ∥ Fee(8B BE) ∥ Size(8B BE) |
| 24 + 48N     | 8       | ConflictCount (uint64 BE) |
| 24 + 48N + 8 | 32 × M  | ConflictHashes|

Size at 1M nodes, 0 conflicts: 24 + 48 × 1,048,576 + 8 = **~48 MB**

---

## Fragmentation

Payloads of 32–48 MB far exceed any path MTU. The proxy fragments each BRC-132 frame using BRC-130:

- `OrigFrameVer = 0x05` in each BRC-130 fragment header (byte 100).
- `MsgType` is preserved in fragment header byte 7 (same pattern as BRC-131 block fragments, `fragmentBlock`).
- Fragment reassembly is keyed by SubtreeID (bytes 8–39 of the fragment header, identical to the `TxID` slot in BRC-124 fragments).
- SHA256d hash verification (`SHA256(SHA256(payload)) == TxID`) does **not apply** — SubtreeID is a Merkle root, not a payload double-hash. The `verifyHash` flag must be `false` for V5 reassembly slots.
- Optional post-reassembly Merkle-root verification is available (see §Merkle Verification).

**Fragment counts** at MTU 9000 (fragDataSize = 8848 bytes):

| Subtree size | Fragments |
| ------------ | --------- |
| ~32 MB (HashesOnly, 1M nodes) | ~3,793 |
| ~48 MB (FullNodes, 1M nodes)  | ~5,689 |

All fit within the uint16 `FragTotal` limit (65,535).

---

## Sequence Tracking and Retransmission

BRC-132 frames participate in the same NACK-based reliability mechanism as BRC-124 and BRC-131 frames:

- The proxy stamps `HashKey` and `SeqNum` in-place before forwarding. `HashKey = XXH64(senderIPv6 ∥ ctrlGroupIdx ∥ subtreeID)` where `ctrlGroupIdx = 0xFFFB`. Each (sender, subtreeID) pair owns an independent sequence stream.
- If `SeqNum` is already non-zero when the proxy receives the frame, it is forwarded verbatim (pre-stamped path).
- Listeners detect gaps on the `(HashKey, 0xFFFB, subtreeID)` flow and dispatch BRC-126 NACKs to retry endpoints.
- Retry endpoints join `FF0X::B:FFFB` and cache BRC-132 frames (and BRC-130 fragments with `OrigFrameVer=0x05`) by `HashKey ∥ SeqNum`. On NACK, retransmit to `FF0X::B:FFFB`.

---

## Merkle Verification (optional, default off)

After reassembly, optional Merkle-root recomputation verifies the SubtreeID:

- Enabled by `-subtree-data-verify-merkle` / `SUBTREE_DATA_VERIFY_MERKLE=true` on the listener.
- Requires decoding the payload into nodes and computing `SHA256d` pairwise up the binary tree.
- Computationally significant at 1M nodes (~1M double-SHA256 operations); disabled by default.
- Mismatch: drop slot, increment `bsl_reassembly_merkle_mismatch_total`.

---

## Proxy Forwarding Rules

1. **Receive** — BRC-132 frames are accepted over TCP ingress. The switch in `handleConn` recognises `FrameVerV5` using the same 44+48 two-step header read as V2/V4.
2. **Decode** — `DecodeSubtreeData` validates Magic, FrameVer, MsgType, and PayLen. Invalid frames are dropped.
3. **Stamp** — If `SeqNum == 0`, the proxy stamps `HashKey` and `SeqNum` in-place per `(senderIPv6, 0xFFFB, subtreeID)` flow, reading SubtreeID from bytes 8–39.
4. **Fragment** — If `len(Payload) > fragDataSize`, fragment via BRC-130 with `OrigFrameVer=0x05` and MsgType preserved in byte 7.
5. **Forward** — Write the frame to all egress interfaces with destination `FF0X::B:FFFB:<egressPort>`.

---

## Listener Processing Rules

1. **Detection** — `IsSubtreeDataFrame(raw)` checks Magic and `raw[6] == 0x05` before `frame.Decode` is called.
2. **Decode** — `DecodeSubtreeData` validates the frame and returns a `SubtreeDataFrame` with `MsgType`, `SubtreeID`, `HashKey`, `SeqNum`, and `Payload`.
3. **Egress** — The frame is forwarded to the configured downstream.
4. **Gap tracking** — `Tracker.Observe(0xFFFB, subtreeID, HashKey, SeqNum, subtreeID)` when `SeqNum != 0`.
5. **Filtering** — Subtree data frames bypass shard filtering. Listeners may optionally filter by SubtreeID.
6. **Reassembly** — BRC-130 fragments with `OrigFrameVer=0x05` are routed to `processSubtreeDataFrame` after reassembly (keyed by callback registered on construction).

---

## Retry Endpoint Behaviour

- **Group join** — On startup, the retry endpoint joins `FF0X::B:FFFB` in addition to all shard groups and `FF0E::B:FFFE`.
- **Cache** — BRC-132 frames and BRC-130 fragments (`OrigFrameVer=0x05`) are cached by `HashKey ∥ SeqNum` with a configurable TTL (default `120s`; longer than the `60s` default for transaction frames to accommodate large reassembly windows).
- **Retransmission routing** — On NACK, `FrameVer` is inspected: if `raw[6] == 0x05`, the frame is retransmitted to `FF0X::B:FFFB` rather than to the shard group derived from SubtreeID.

---

## Infrastructure Impact

| Component              | Change                                                                                                |
| ---------------------- | ----------------------------------------------------------------------------------------------------- |
| shard-common   | `FrameVerV5`, `SubtreeMsgHashesOnly`, `SubtreeMsgFullNodes`, `ErrBadSubtreeMsg` constants; `SubtreeDataFrame`, `SubtreeDataPayload` structs; `EncodeSubtreeData`, `DecodeSubtreeData`, `IsSubtreeDataFrame`, `EncodeSubtreeDataPayload`, `DecodeSubtreeDataPayload` |
| shard-proxy    | `FrameVerV5` case in TCP `handleConn`; `ProcessSubtreeData` + `fragmentSubtreeData` methods          |
| shard-listener | Joins `FF0X::B:FFFB`; `IsSubtreeDataFrame` detection; `processSubtreeDataFrame`; reassembly `OrigFrameVer=0x05` callback path; optional Merkle verification |
| retry-endpoint | Joins `FF0X::B:FFFB`; `processSubtreeDataFrame` in ingress; `FrameVerV5`-aware retransmit routing   |
| Firewall               | No additional rules — `FF0X::B:FFFB` uses the same port as other groups                              |

---

## Error Handling

| Condition                      | Action                                         |
| ------------------------------ | ---------------------------------------------- |
| `raw[6] != 0x05`               | Not BRC-132; handled by other decoders         |
| Bad magic                      | Silent drop                                    |
| Unknown MsgType                | Drop; `ErrBadSubtreeMsg`                       |
| PayloadLen exceeds buffer      | Drop; `io.ErrUnexpectedEOF`                    |
| Datagram shorter than 92 bytes | Drop; `ErrTooShort`                            |
| SeqNum == 0                    | Frame not yet proxy-stamped; listener discards |
| Merkle mismatch (optional)     | Drop; `bsl_reassembly_merkle_mismatch_total`   |

---

## Constants Reference

| Name                    | Value  | Hex    | Description                                          |
| ----------------------- | ------ | ------ | ---------------------------------------------------- |
| `FrameVerV5`            | 5      | `0x05` | BRC-132 subtree data frame version                   |
| `SubtreeMsgHashesOnly`  | 1      | `0x01` | MsgType: transaction hashes only (32B per node)      |
| `SubtreeMsgFullNodes`   | 2      | `0x02` | MsgType: full nodes with fee and size (48B per node) |
| `GroupSubtreeDataAnnounce` | 65531 | `0xFFFB` | Subtree data multicast group index               |
| `HeaderSize`            | 92     | `0x5C` | BRC-132 header size (identical to BRC-124)           |
| `SubtreeDataPayloadHeaderSize` | 24 | `0x18` | Fixed metadata prefix size                      |
| `SubtreeNodeHashSize`   | 32     | `0x20` | Node size in HashesOnly payload                      |
| `SubtreeNodeFullSize`   | 48     | `0x30` | Node size in FullNodes payload                       |

---

## References

- [BRC-124: Multicast Transaction Frame Format](brc-124-frame-format.md) — base header layout reused by BRC-132
- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md) — NACK/ACK/MISS used for subtree frame retransmission
- [BRC-127: Subtree Group Announcement](brc-127-subtree-announce.md) — SubtreeID→GroupID metadata (distinct from BRC-132 data delivery)
- [BRC-129: Multicast Group Address Assignments](brc-129-multicast-addressing.md) — group index allocations; `0xFFFB` = GroupSubtreeDataAnnounce
- [BRC-130: Fragmentation](brc-130-fragmentation.md) — BRC-130 extension for large subtree payloads; `OrigFrameVer=0x05`
- [BRC-131: Block Announcements](brc-131-block-announcements.md) — `FrameVerV4` pattern followed by BRC-132
- [shard-common/frame](https://github.com/lightwebinc/shard-common/tree/main/frame) — `EncodeSubtreeData`, `DecodeSubtreeData`, `IsSubtreeDataFrame`, `EncodeSubtreeDataPayload`, `DecodeSubtreeDataPayload`
- [shard-proxy/forwarder](https://github.com/lightwebinc/shard-proxy/tree/main/forwarder) — `ProcessSubtreeData`, `fragmentSubtreeData`
- [shard-listener/listener](https://github.com/lightwebinc/shard-listener/tree/main/listener) — `processSubtreeDataFrame`
- [retry-endpoint/ingress](https://github.com/lightwebinc/retry-endpoint/tree/main/ingress) — `processSubtreeDataFrame`
- [retry-endpoint/retransmit](https://github.com/lightwebinc/retry-endpoint/tree/main/retransmit) — V5-aware retransmit routing
- [BRC-132: Multicast Subtree Data Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0132.md) — published BRC
