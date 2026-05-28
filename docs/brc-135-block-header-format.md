# BRC-135 — Multicast Block Header Frame Format

BRC-135 defines a new frame version (`0x07`) for distributing standalone 80-byte
BSV block headers over the multicast fabric. Block header frames are produced as
a lightweight split of BRC-131 `BlockAnnounce` payloads, enabling downstream
consumers that require only block headers (SPV wallets, header-chain validators,
mining coordinators) to receive a minimal 172-byte datagram instead of the full
announce payload.

> **Canonical BRC:** [BRC-135](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0135.md)

---

## Purpose

The BRC-131 `BlockAnnounce` frame carries the 80-byte block header, a 32-byte
CoinbaseTxID, and a variable-length list of subtree root hashes. For subscribers
that only need the block header itself, this payload is oversized:

- **SPV clients** require only the header chain to verify proof-of-work and
  compute chain state; subtree hashes and CoinbaseTxID are irrelevant.
- **Mining coordinators** need the previous-block hash and timestamp from the
  header to build new templates; the remainder of the announce payload adds
  latency on bandwidth-constrained links.
- **Header-chain archival services** store and index raw 80-byte headers; they
  discard everything else.

BRC-135 addresses this by defining a minimal, fixed-size frame that any node
receiving BRC-131 `BlockAnnounce` frames can produce by extracting the header
and re-emitting it to downstream consumers (unicast, multicast, or both). A node
that produces BRC-135 frames is referred to as an **emitter** throughout this
specification.

---

## Multicast Group

BRC-135 frames are emitted to the emitter's configured **multicast egress
group** or **unicast egress address**. They are NOT re-injected onto the primary
fabric group `FF0E::B:FFFE` — doing so would create a feedback loop (other
fabric subscribers would receive the split frame on the control channel they
already subscribe to).

When multicast egress is enabled, the emitter sends BRC-135 frames to the
`CtrlGroupBlockHeader` index (`0xFFFA`) on the **egress scope** (typically a
different scope or group-id from the ingress fabric):

| Index  | Scope           | Compressed Address        | Notes                            |
| ------ | --------------- | ------------------------- | -------------------------------- |
| 0xFFFA | egress (varies) | `FF05::<egress-gid>:FFFA` | Emitter multicast egress channel |

The egress group-id is set independently of the fabric group-id via
`-mc-egress-group-id` (default: same as `-mc-group-id`). This ensures BRC-135
frames reach only downstream consumers, not peer fabric subscribers or retry
endpoints on the ingress fabric.

---

## Frame Header Format (92 bytes)

The BRC-135 header is **layout-identical** to BRC-124. All infrastructure
components that inspect Magic, HashKey, or SeqNum read correct values at the
same offsets.

| Offset | Size | Align | Field         | Value / Notes                                             |
| ------ | ---- | ----- | ------------- | --------------------------------------------------------- |
| 0      | 4    | —     | Network Magic | `0xE3E1F3E8` (BSV mainnet P2P magic)                      |
| 4      | 2    | —     | Protocol Ver  | `0x02BF` (703, BSV large-block baseline)                  |
| 6      | 1    | —     | Frame Version | `0x07` — BRC-135 block header                             |
| 7      | 1    | —     | Reserved      | `0x00`                                                    |
| 8      | 32   | 8B    | BlockHash     | SHA256d of the 80-byte block header (internal byte order) |
| 40     | 8    | 8B    | HashKey       | XXH64(emitterIPv6 ∥ 0xFFFA ∥ zeros); stamped by emitter   |
| 48     | 8    | 8B    | SeqNum        | Monotonic per-emitter counter; stamped by emitter         |
| 56     | 32   | 8B    | LayoutPad32   | All zeros (no subtree scope for block headers)            |
| 88     | 4    | —     | PayloadLen    | `0x00000050` (80 = fixed block header size, uint32 BE)    |
| 92     | 80   | —     | Payload       | Raw 80-byte BSV block header                              |

**Total frame size:** 172 bytes (92 header + 80 payload). Always fits in a
single UDP datagram — no fragmentation required.

**Key distinctions from BRC-131:**

- Frame version is `0x07` (not `0x04`).
- No `MsgType` byte — byte 7 is `Reserved = 0x00`.
- `BlockHash` at bytes 8–39 is the SHA256d of the raw 80-byte block header
  (identical to `ContentID` in a BRC-131 `BlockAnnounce` for the same block).
- `PayloadLen` is always exactly 80.
- Payload contains only the raw block header — no CoinbaseTxID, no subtree
  hashes.
- `HashKey` is stamped by the **emitter** (not the proxy), using the emitter's
  own IPv6 address as the sender component. This reflects that the emitter is
  the originator of this split frame.

---

## Payload Format (80 bytes)

The payload is the standard BSV block header, byte-for-byte identical to the
first 80 bytes of a BRC-131 `BlockAnnounce` payload:

| Offset | Size | Field         | Encoding  | Description                             |
| ------ | ---- | ------------- | --------- | --------------------------------------- |
| 0      | 4    | Version       | int32 LE  | Block version                           |
| 4      | 32   | PrevBlockHash | bytes     | Hash of previous block (internal order) |
| 36     | 32   | MerkleRoot    | bytes     | Merkle root of the block's tx tree      |
| 68     | 4    | Timestamp     | uint32 LE | Block timestamp (Unix epoch seconds)    |
| 72     | 4    | Bits          | uint32 LE | Compact difficulty target               |
| 76     | 4    | Nonce         | uint32 LE | Proof-of-work nonce                     |

No additional framing or envelope is applied. The block header is copied
verbatim from the BRC-131 `BlockAnnounce` payload bytes `[0:80]`.

---

## Sequencing

BRC-135 frames carry their own independent `HashKey`/`SeqNum` flow stamped by
the emitter:

- **HashKey** is `XXH64(emitterIPv6 ∥ 0xFFFA ∥ zeros)` — stable for the lifetime
  of the emitter process. The group ingredient `0xFFFA` (`CtrlGroupBlockHeader`)
  matches the actual BRC-135 egress multicast group, so the HashKey input is
  self-consistent with the destination.
- **SeqNum** is a monotonic counter starting at 1, incremented for each BRC-135
  frame emitted. Each emitter maintains a single counter for all block header
  frames.
- Downstream consumers that track gaps on BRC-135 flows identify the emitter via
  HashKey. If the downstream consumer receives BRC-135 frames from multiple
  emitters (e.g., via anycast or failover), each emitter produces an independent
  sequence stream.

---

## Retransmission

BRC-135 frames are **not retransmitted** via the standard BRC-126 NACK path on
the primary fabric. They are a derived product: if a downstream consumer misses
a BRC-135 frame, it can recover from a different source:

1. **Redundant emitters** — multiple emitters produce the same block header;
   downstream consumers subscribe to more than one for reliability.
2. **Re-request from upstream** — the consumer re-requests the full BRC-131
   BlockAnnounce via BRC-126 NACK to a retry endpoint, then extracts the header
   locally.
3. **Application-level retry** — the downstream consumer requests the block
   header by hash from any BSV peer using the standard `getheaders` protocol.

If a deployment requires NACK-based retransmission for BRC-135 frames on the
egress network, a secondary retry endpoint can be deployed on the egress segment
that joins the egress multicast group and caches BRC-135 frames by
`HashKey ∥ SeqNum`. This is an optional deployment topology, not a
protocol-level requirement.

---

## Downstream Consumer Processing

A consumer receiving BRC-135 frames:

1. **Validate** — Check `raw[0:4] == Magic`, `raw[6] == 0x07`,
   `PayloadLen == 80`.
2. **Extract** — Read the 80-byte block header from `raw[92:172]`.
3. **Verify** — Optionally compute `SHA256d(payload)` and compare against
   `BlockHash` (bytes 8–39) to confirm integrity.
4. **Gap track** — If consuming from a single emitter, monitor `SeqNum`
   continuity on the `HashKey` flow to detect missed headers.

---

## Error Handling

| Condition                       | Action                                 |
| ------------------------------- | -------------------------------------- |
| `raw[6] != 0x07`                | Not BRC-135; handled by other decoders |
| Bad magic                       | Silent drop                            |
| `PayloadLen != 80`              | Drop; `ErrBadBlockHeaderLen`           |
| Datagram shorter than 172 bytes | Drop; `ErrTooShort`                    |
| BlockHash mismatch (optional)   | Drop; integrity verification failed    |

---

## Constants Reference

| Name                   | Value | Hex      | Description                                       |
| ---------------------- | ----- | -------- | ------------------------------------------------- |
| `FrameVerV7`           | 7     | `0x07`   | BRC-135 block header frame version                |
| `BlockHeaderPayload`   | 80    | `0x50`   | Fixed payload size (standard BSV block header)    |
| `BlockHeaderFrameSize` | 172   | `0xAC`   | Total frame size (92 + 80)                        |
| `CtrlGroupBlockHeader` | 65530 | `0xFFFA` | Block header egress channel (BRC-135 mc-egress)   |
| `CtrlGroupControl`     | 65534 | `0xFFFE` | Control group index (shared with BRC-131/133/134) |
| `HeaderSize`           | 92    | `0x5C`   | BRC-135 header size (identical to BRC-124)        |

---

## References

- [BRC-124: Multicast Transaction Frame Format](./brc-124-frame-format.md) —
  base header layout reused by BRC-135
- [BRC-129: Multicast Group Address Assignments](./brc-129-multicast-addressing.md)
  — control-plane group index allocations
- [BRC-131: Block Announcement Protocol](./brc-131-block-announcements.md) —
  source of the 80-byte block header extracted by the emitter
- [BRC-133: Coinbase Transaction Delivery](./brc-133-coinbase-delivery.md) —
  companion control-plane frame type
- [BRC-134: Chained Anchor Transaction Frames](./brc-134-anchor-transactions.md)
  — companion control-plane frame type
- [shard-common/frame](https://github.com/lightwebinc/shard-common/tree/main/frame)
  — `EncodeBlockHeader`, `DecodeBlockHeader`, `IsBlockHeaderFrame`, `FrameVerV7`
- [shard-listener/listener](https://github.com/lightwebinc/shard-listener/tree/main/listener)
  — reference emitter implementation
- [BRC-135: Multicast Block Header Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0135.md) — published BRC
