# BRC-124 — Data-Plane Frame Format

BRC-124 defines the wire format for transporting BSV transactions over IPv6 multicast and TCP/UDP unicast. This document is the canonical reference for the 92-byte BRC-124 header and the 44-byte legacy BRC-12 header.

> **Status:** Current BRC for the data-plane frame format.
>
> **Canonical BRC:** [bitcoin-sv/BRCs — transactions/0124.md](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md)
> The latest updates (PrevSeq/CurSeq XXH64 hash chain fields) are not yet published to the BRC; they are pending in a pull request.

---

## BRC-124 Frame Format (92-byte header)

All multi-byte integers are big-endian. 8-byte alignment for all fields after offset 8.

| Offset | Size | Align | Field          | Value / Notes                                            |
| ------ | ---- | ----- | -------------- | -------------------------------------------------------- |
| 0      | 4    | —     | Network magic  | 0xE3E1F3E8 (BSV mainnet P2P magic)                       |
| 4      | 2    | —     | Protocol ver   | 0x02BF = 703 (BSV node version baseline)                 |
| 6      | 1    | —     | Frame version  | 0x02 (BRC-124)                                           |
| 7      | 1    | —     | Reserved       | 0x00                                                     |
| 8      | 32   | 8B    | Transaction ID | Raw 256-bit txid (internal byte order)                   |
| 40     | 8    | 8B    | PrevSeq        | XXH64 of previous chain state; 0 = unset (proxy-stamped) |
| 48     | 8    | 8B    | CurSeq         | XXH64 of current chain state; 0 = unset (proxy-stamped)  |
| 56     | 32   | 8B    | Subtree ID     | 32-byte batch identifier; zeros = unset                  |
| 88     | 4    | 8B    | Payload length | uint32 BE; max 10 MiB                                    |
| 92     | \*   | —     | BSV tx payload | Raw serialised transaction bytes                         |

### Field Details

- **Network magic (0:4):** BSV mainnet P2P magic; enables standard firewall classification.
- **Protocol version (4:6):** Informational; 703 = BSV large-block policy baseline.
- **Frame version (6):** `0x02` for BRC-124, `0x01` for legacy BRC-12.
- **Transaction ID (8:40):** Raw 256-bit txid in internal byte order (NOT display-reversed).
- **PrevSeq (40:48):** 8-byte XXH64 hash of the previous frame in this sender+group chain, stamped in-place by the proxy. A value of `0` means the proxy has not yet stamped the frame. Equals the `CurSeq` of the immediately preceding frame; a mismatch indicates a missing frame.
- **CurSeq (48:56):** 8-byte XXH64 hash of the current frame's chain state, stamped in-place by the proxy. Computed as `XXH64(senderIPv6 ∥ groupIdx ∥ counter)`. A value of `0` means the frame has not been stamped. Receivers use this as the primary cache key for NACK-based retransmission.
- **Subtree ID (56:88):** Opaque 32-byte batch identifier for subtree-level filtering.
- **Payload (92+):** BSV transaction bytes. BRC-124 frames carry BRC-12 raw transactions; BRC-128 frames carry BRC-30 Extended Format (EF) transactions. Inspect payload bytes 4–9 to distinguish: `0x00 0x00 0x00 0x00 0x00 0xEF` = BRC-30 EF (BRC-128), otherwise BRC-12 raw (BRC-124). See **[BRC-128 Extended Format](brc-128-ef-frame-format.md)**.

---

## BRC-12 Frame Format (Legacy — 44-byte header)

Accepted and forwarded verbatim for backward compatibility.

| Offset | Size | Field          |
| ------ | ---- | -------------- | ---------- |
| 0      | 4    | Network magic  | 0xE3E1F3E8 |
| 4      | 2    | Protocol ver   | 0x02BF     |
| 6      | 1    | Frame version  | 0x01       |
| 7      | 1    | Reserved       | 0x00       |
| 8      | 32   | Transaction ID |
| 40     | 4    | Payload length |
| 44     | \*   | Payload        |

**BRC-12 Limitations:** No `PrevSeq`, `CurSeq`, or `SubtreeID` fields. Hash-chain gap tracking and subtree filtering do not apply.

---

## Frame Processing Rules

### Proxy (`bitcoin-shard-proxy`)

- Decode header (BRC-12 or BRC-124); drop on bad magic or unknown version.
- For BRC-124: stamp `PrevSeq` and `CurSeq` in-place at bytes 40–55 using the XXH64 hash chain per `(senderIPv6, groupIdx)`.
- Forward verbatim to all egress interfaces (no re-encoding).

### Listener (`bitcoin-shard-listener`)

- Decode header; apply shard filter (group index).
- Apply subtree filter (SubtreeID include/exclude).
- For BRC-124 with non-zero `CurSeq`: track gaps per group by verifying `PrevSeq == lastCurSeq` (hash-chain break = missing frame).
- Forward matching frames to egress address (UDP or TCP).

### Retry Endpoint (`bitcoin-retry-endpoint`)

- Receive multicast frames; decode header.
- Store raw frame indexed by `CurSeq` (primary key, `0x01` prefix) and `PrevSeq` (secondary pointer, `0x00` prefix) for dual-direction NACK lookup.

---

## Backward Compatibility

- BRC-12 frames are decoded with zero-valued BRC-124-only fields (`PrevSeq = 0`, `CurSeq = 0`, `SubtreeID = zeros`).
- The forwarder (proxy) forwards BRC-12 frames verbatim — no upgrade to BRC-124 encoding.
- Unknown frame versions are dropped with `ErrBadVer`.
- All components accept both BRC-12 and BRC-124/BRC-128 frames on the wire.

---

## Implementation

- **Canonical source:** `bitcoin-shard-common/frame/frame.go`
- **Constants:** `MagicBSV = 0xE3E1F3E8`, `ProtoVer = 0x02BF`, `FrameVerV1 = 0x01`, `FrameVerV2 = 0x02`, `HeaderSizeLegacy = 44`, `HeaderSize = 92`
