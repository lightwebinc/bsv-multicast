# BRC-128 — Extended Format (EF) Payload for Multicast Frames

BRC-128 defines BRC-30 Extended Format transaction payloads inside the standard BRC-124 frame. The 92-byte header is unchanged; Frame Version remains `0x02`.

> **Canonical BRC:** [BRC-128](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0128.md)

---

## Frame Layout

The header is identical to BRC-124. Only the payload format differs.

| Offset | Size | Align | Field          | Value / Notes                                            |
| ------ | ---- | ----- | -------------- | -------------------------------------------------------- |
| 0      | 4    | —     | Network magic  | 0xE3E1F3E8 (BSV mainnet P2P magic)                       |
| 4      | 2    | —     | Protocol ver   | 0x02BF = 703 (BSV node version baseline)                 |
| 6      | 1    | —     | Frame version  | 0x02 (BRC-124 — unchanged)                               |
| 7      | 1    | —     | Reserved       | 0x00                                                     |
| 8      | 32   | 8B    | Transaction ID | Raw 256-bit txid (internal byte order)                   |
| 40     | 8    | 8B    | HashKey        | Stable per-flow XXH64 identifier; 0 = unstamped          |
| 48     | 8    | 8B    | SeqNum         | Monotonic per-flow counter (starts at 1); 0 = unstamped  |
| 56     | 32   | 8B    | Subtree ID     | 32-byte batch identifier; zeros = unset                  |
| 88     | 4    | 8B    | Payload length | uint32 BE                                                |
| 92     | \*   | —     | EF tx payload  | BRC-30 Extended Format transaction bytes                 |

---

## Payload: BRC-30 Extended Format

The payload is a BSV transaction in BRC-30 Extended Format:

```text
Version (4B LE) | EF marker (6B: 0x000000000000EF) | inputs | outputs | locktime (4B LE)
```

Each EF input appends the spent output's satoshi value (8B LE) and locking script (VarInt + script bytes) after the standard input fields.

---

## Detecting EF vs Raw Payloads

Inspect payload bytes 4–9:

- **`0x00 0x00 0x00 0x00 0x00 0xEF`** → BRC-30 Extended Format (BRC-128)
- **Anything else** → BRC-12 raw transaction (BRC-124)

The EF marker is part of the BRC-30 spec and cannot collide with a valid BRC-12 input count VarInt.

---

## Why No Frame Version Bump

- Frame Version signals **header structure** changes (BRC-12=44B → BRC-124=92B). The header is structurally identical.
- BRC-30 EF is **self-identifying** via its embedded marker — no header-level signal needed.
- BSV precedent (BRC-62 BEEF, BRC-95, BRC-96) uses **payload-level markers**, not outer envelope versions.
- A new frame version would **break** all deployed infrastructure (`ErrBadVer` → drop) for zero benefit.

---

## Infrastructure Impact

- **Proxy** — no changes. Forwards verbatim, stamps HashKey/SeqNum. Payload is opaque.
- **Listener** — no changes. Header decode, shard/subtree filter, gap tracking all work identically.
- **Retry endpoint** — no changes. Caches by `HashKey ∥ SeqNum`, not payload content.
- **Downstream consumers** — must inspect payload bytes 4–9 to choose BRC-12 or BRC-30 parser.

BRC-124 and BRC-128 frames coexist on the same multicast groups.

---

## References

- [BRC-12: Raw Transaction Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0012.md)
- [BRC-30: Transaction Extended Format (EF)](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0030.md)
- [BRC-124: Multicast Transaction Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0124.md)
- **Canonical source:** `shard-common/frame/frame.go`
- [BRC-128: Multicast Extended Transaction Frame Format](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0128.md) — published BRC
