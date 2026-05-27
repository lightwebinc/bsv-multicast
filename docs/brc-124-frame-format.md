# BRC-124 — Data-Plane Frame Format

BRC-124 defines the wire format for transporting BSV transactions over IPv6 multicast and TCP/UDP unicast. This document is a reference for the 92-byte BRC-124 header and the 44-byte legacy BRC-12 header.

> **Canonical BRC:** [BRC-124](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md)

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
| 40     | 8    | 8B    | HashKey        | Stable per-flow XXH64 identifier; 0 = unstamped           |
| 48     | 8    | 8B    | SeqNum         | Monotonic per-flow counter (starts at 1); 0 = unstamped   |
| 56     | 32   | 8B    | Subtree ID     | 32-byte batch identifier; zeros = unset                  |
| 88     | 4    | 8B    | Payload length | uint32 BE; max 10 MiB                                    |
| 92     | \*   | —     | BSV tx payload | Raw serialised transaction bytes                         |

### Field Details

- **Network magic (0:4):** BSV mainnet P2P magic; enables standard firewall classification.
- **Protocol version (4:6):** Informational; 703 = BSV large-block policy baseline.
- **Frame version (6):** `0x02` for BRC-124, `0x01` for legacy BRC-12.
- **Transaction ID (8:40):** Raw 256-bit txid in internal byte order (NOT display-reversed).
- **HashKey (40:48):** Stable per-flow identifier computed as `XXH64(senderIPv6 ∥ groupIdx ∥ subtreeID)`, stamped in-place by the proxy. Constant for all frames of the same flow. A value of `0` means the proxy has not yet stamped the frame.
- **SeqNum (48:56):** Monotonic per-flow counter starting at 1, stamped in-place by the proxy. A gap (`SeqNum` advances by >1) indicates a missing frame. The pair `(HashKey, SeqNum)` forms the 16-byte cache key for NACK-based retransmission. A value of `0` means the frame has not been stamped.
- **Subtree ID (56:88):** Opaque 32-byte batch identifier for subtree-level filtering.
- **Payload (92+):** BSV transaction bytes. BRC-124 frames carry BRC-12 raw transactions; BRC-128 frames carry BRC-30 Extended Format (EF) transactions. Inspect payload bytes 4–9 to distinguish: `0x00 0x00 0x00 0x00 0x00 0xEF` = BRC-30 EF (BRC-128), otherwise BRC-12 raw (BRC-124). See **[BRC-128 Extended Format](brc-128-ef-frame-format.md)**.

---

## BRC-12 Frame Format (Legacy — 44-byte header)

Accepted and forwarded verbatim for backward compatibility.

| Offset | Size | Field          | Value / Notes |
| ------ | ---- | -------------- | ------------- |
| 0      | 4    | Network magic  | 0xE3E1F3E8    |
| 4      | 2    | Protocol ver   | 0x02BF        |
| 6      | 1    | Frame version  | 0x01          |
| 7      | 1    | Reserved       | 0x00          |
| 8      | 32   | Transaction ID |               |
| 40     | 4    | Payload length |               |
| 44     | \*   | Payload        |               |

**BRC-12 Limitations:** No `HashKey`, `SeqNum`, or `SubtreeID` fields. Flow-based gap tracking and subtree filtering do not apply.

---

## Frame Processing Rules

### Proxy (`shard-proxy`)

- Decode header (BRC-12 or BRC-124); drop on bad magic or unknown version.
- For BRC-124: stamp `HashKey` (bytes 40–47) as `XXH64(senderIPv6 ∥ groupIdx ∥ subtreeID)` and `SeqNum` (bytes 48–55) as a monotonic per-flow counter, in-place.
- Forward verbatim to all egress interfaces (no re-encoding).

### Listener (`shard-listener`)

- Decode header; apply shard filter (group index).
- Apply subtree filter (SubtreeID include/exclude).
- For BRC-124 with non-zero `SeqNum`: track gaps per flow identified by `HashKey`; a gap is detected when `SeqNum` advances by more than 1.
- Forward matching frames to egress address (UDP or TCP).

### Retry Endpoint (`retry-endpoint`)

- Receive multicast frames; decode header.
- Store raw frame indexed by `HashKey ∥ SeqNum` (16-byte key) for single-key NACK lookup.

---

## Backward Compatibility

- BRC-12 frames are decoded with zero-valued BRC-124-only fields (`HashKey = 0`, `SeqNum = 0`, `SubtreeID = zeros`).
- The forwarder (proxy) forwards BRC-12 frames verbatim — no upgrade to BRC-124 encoding.
- Unknown frame versions are dropped with `ErrBadVer`.
- All components accept both BRC-12 and BRC-124/BRC-128 frames on the wire.

---

## Implementation

- **Canonical source:** `shard-common/frame/frame.go`
- **Constants:** `MagicBSV = 0xE3E1F3E8`, `ProtoVer = 0x02BF`, `FrameVerV1 = 0x01`, `FrameVerV2 = 0x02`, `HeaderSizeLegacy = 44`, `HeaderSize = 92`

---

## References

- [BRC-124: Multicast Transaction Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md) — published BRC
