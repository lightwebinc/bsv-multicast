# BRC-130 — Multicast Transaction Frame Fragmentation

BRC-130 is a fragmentation extension to BRC-124. When a BSV transaction payload exceeds the path MTU, the proxy decomposes it into a sequence of fixed-size fragment datagrams. Listeners reassemble the fragments and verify the reconstructed payload against the TxID before forwarding.

> **Status:** Current BRC for large-transaction fragmentation on the multicast fabric.
>
> **Canonical BRC:** [lightwebinc/BRCs — transactions/0130.md](https://github.com/lightwebinc/BRCs/blob/master/transactions/0130.md)

---

## Fragment Header Format (104 bytes)

Bytes 0–91 are **layout-identical** to a BRC-124 header. Existing infrastructure that inspects the TxID, HashKey, SeqNum, or Subtree ID fields reads correct values from a BRC-130 datagram at the same offsets.

| Offset | Size | Align | Field           | Value / Notes                                                  |
| ------ | ---- | ----- | --------------- | -------------------------------------------------------------- |
| 0      | 4    | —     | Network Magic   | 0xE3E1F3E8 (BSV mainnet P2P magic)                             |
| 4      | 2    | —     | Protocol Ver    | 0x02BF (703, BSV large-block baseline)                         |
| 6      | 1    | —     | Frame Version   | **0x03** — BRC-130 fragment                                    |
| 7      | 1    | —     | Reserved        | 0x00                                                           |
| 8      | 32   | 8B    | Transaction ID  | SHA256d(reassembled payload); same on every fragment           |
| 40     | 8    | 8B    | HashKey         | XXH64(senderIPv6 ∥ groupIdx ∥ subtreeID); stamped by proxy     |
| 48     | 8    | 8B    | SeqNum          | Per-flow monotonic counter; independent per fragment           |
| 56     | 32   | 8B    | Subtree ID      | 32-byte batch identifier; zeros = unset                        |
| 88     | 4    | 8B    | PayloadLen      | Size of **this fragment's** data bytes (uint32 BE)             |
| 92     | 4    | 4B    | OrigPayloadLen  | Total unfragmented payload length (uint32 BE)                  |
| 96     | 2    | 2B    | FragIndex       | 0-based index of this fragment (uint16 BE)                     |
| 98     | 2    | 2B    | FragTotal       | Total number of fragments in this transaction (uint16 BE)      |
| 100    | 4    | 4B    | Reserved2       | 0x00000000                                                     |
| 104    | \*   | —     | Fragment data   | Slice of the original payload (PayloadLen bytes)               |

---

## Fragment Data Size

```
fragDataSize = pathMTU − IPv6HeaderSize − UDPHeaderSize − BRC130HeaderSize
             = pathMTU − 40 − 8 − 104
             = pathMTU − 152
```

| Path MTU | fragDataSize |
| -------- | ------------ |
| 1500 B   | 1348 B       |
| 9000 B   | 8848 B       |

---

## Per-Fragment Gap Tracking

Each fragment is stamped with an **independent** HashKey and SeqNum by the proxy. This treats every fragment as a separate frame for gap-tracking purposes, allowing retransmission of individual lost fragments via the standard BRC-126 NACK mechanism with no changes to the retry endpoint.

---

## Reassembly (Listener)

1. **Slot allocation** — On first fragment for a TxID, allocate an `OrigPayloadLen`-byte buffer, a `FragTotal`-bit received-fragment bitmask, and a TTL timer.
2. **Fragment placement** — Copy data into buffer at `offset = FragIndex × fragDataSize`. Mark the bit.
3. **Completion** — When all `FragTotal` bits are set, proceed to verification.
4. **Hash verification** — Compute `SHA256(SHA256(buffer))` and compare to TxID. Mismatch → drop + increment `bsl_reassembly_hash_mismatch_total`.
5. **Delivery** — Construct a synthetic BRC-124 frame (FrameVer = 0x02) and route through the normal filter → egress → gap-tracking path.
6. **TTL eviction** — Slots not completed within 10 s are discarded; increment `bsl_reassembly_abandoned_total`.
7. **Slot cap** — Default maximum 4096 concurrent slots; oldest incomplete slot evicted on overflow.
8. **Duplicates** — Same TxID + FragIndex silently ignored.

---

## Reassembly Metrics

| Metric                               | Description                                             |
| ------------------------------------ | ------------------------------------------------------- |
| `bsl_reassembly_started_total`       | New slots opened (first fragment received)              |
| `bsl_reassembly_completed_total`     | Reassemblies completed and delivered downstream         |
| `bsl_reassembly_abandoned_total`     | Slots evicted due to TTL expiry or slot-cap eviction    |
| `bsl_reassembly_hash_mismatch_total` | Reassembled payloads that failed SHA256d verification   |

---

## Error Handling

| Condition                      | Action                                       |
| ------------------------------ | -------------------------------------------- |
| FrameVer ≠ 0x03                | Not BRC-130; decode as BRC-124               |
| Bad magic                      | Silent drop                                  |
| FragIndex ≥ FragTotal          | Silent drop (malformed)                      |
| FragTotal = 0                  | Silent drop (malformed)                      |
| OrigPayloadLen = 0             | Silent drop                                  |
| PayloadLen > fragDataSize      | Silent drop                                  |
| Datagram shorter than header   | Silent drop                                  |
| Hash mismatch after reassembly | Drop slot; increment hash_mismatch counter   |
| TTL expiry                     | Drop slot; increment abandoned counter       |

---

## Infrastructure Impact

- **Proxy** — slices payload into _k_ fragments, stamps independent HashKey/SeqNum per fragment, forwards each as a normal UDP datagram.
- **Listener** — new `reassembly` package handles BRC-130 datagrams; delivers synthetic BRC-124 frame to existing filter/egress/gap-tracker pipeline. No changes to the gap tracker or egress path.
- **Retry endpoint** — no changes. Caches each fragment by `HashKey ∥ SeqNum`; retransmits individual fragments on NACK exactly as for BRC-124 frames.
- **Firewall / classifiers** — no changes. Magic bytes, TxID, HashKey, SeqNum, and Subtree ID are at the same offsets as BRC-124.

---

## Constants Reference

| Name                | Value | Hex    | Description                              |
| ------------------- | ----- | ------ | ---------------------------------------- |
| FrameVerV3          | 3     | 0x03   | BRC-130 fragment frame version           |
| HeaderSizeV3        | 104   | 0x68   | BRC-130 header size in bytes             |
| IPv6HeaderSize      | 40    | 0x28   | IPv6 header overhead                     |
| UDPHeaderSize       | 8     | 0x08   | UDP header overhead                      |
| EthernetMTU         | 1500  | 0x5DC  | Standard Ethernet MTU                    |
| JumboMTU            | 9000  | 0x2328 | Jumbo frame MTU                          |
| DefaultFragDataSize | 1348  | 0x544  | fragDataSize at 1500-byte path MTU       |

---

## References

- [BRC-124: Multicast Transaction Frame Format](brc-124-frame-format.md) — base frame format extended by BRC-130
- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md) — NACK/ACK/MISS used for per-fragment retransmission
- [bitcoin-shard-common/frame](https://github.com/lightwebinc/bitcoin-shard-common/tree/main/frame) — `EncodeFragment`, `DecodeFragment`, `IsFragment`
- [bitcoin-shard-proxy/forwarder](https://github.com/lightwebinc/bitcoin-shard-proxy/tree/main/forwarder) — proxy-side fragmentation
- [bitcoin-shard-listener/reassembly](https://github.com/lightwebinc/bitcoin-shard-listener/tree/main/reassembly) — listener-side reassembly
