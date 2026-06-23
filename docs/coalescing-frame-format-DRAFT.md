# Coalescing Frame Format — DRAFT design (no assigned BRC yet)

> **Status: DRAFT / exploratory.** Not an assigned BRC, not on the wire. This
> captures the design intent and the open problems so the work can be scoped.
> A real BRC + prototype is needed before anything ships.

## Why

Data-plane forwarding cost is dominated by **packets per second**, not bits per
second — fan-out is a per-packet syscall/worker cost. BSV transaction traffic is
small-packet (avg ~217 B), so a Mbit of txs is ~7× the packets of a Mbit at MTU
and ~40× at jumbo. That makes small-packet flows the expensive case, and it's
the reason the rating engine prices a **pps-aware floor** (`max(bytes,
packets×min_packet_bytes)`) — see `1bsv-ops/services/rating/docs/rate-derivation.md`.

A coalescing frame format is the **carrot** that pairs with that floor: pack many
small transactions into one datagram (up to path MTU / jumbo), so the same
payload crosses the fabric as a fraction of the packets. The producer's bill
drops toward true byte cost; our pps headroom rises. Incentives align — we don't
need to force it, just price it and let customers opt in.

This is the **inverse of [BRC-130 fragmentation](brc-130-fragmentation.md)**:
130 splits one oversized tx across many datagrams; coalescing packs many small
txs into one datagram.

## Shape (sketch)

A new **frame version** (BRC-124 byte 6 = `0x03`, say) marking a *bundle* frame.
Unlike [BRC-130](brc-130-fragmentation.md), a bundle **cannot** be layout-identical
to a [BRC-124](brc-124-frame-format.md) single-tx header — there is no single
TxID/HashKey/SeqNum for N transactions. The header carries bundle-level metadata;
the payload is a length-prefixed sequence of transactions:

```
[ bundle header ]
  network magic / protocol ver / frame version = 0x03
  shard / group identifier          (all txs share one multicast group — see constraint)
  bundle SeqNum                     (per-flow, for the bundle stream)
  tx count (uint16)
  bundle payload length (uint32)
[ repeated × count ]
  tx length (uint32)
  [ optional 32-byte TxID ]          (or recomputed by the receiver)
  raw serialised tx bytes
```

Per-tx TxID may be carried (cheap dedup/billing/NACK) or recomputed on receipt
(smaller frames). That's an explicit trade to settle in the BRC.

## The hard constraint: addressing is per-group

This fabric addresses **per transaction** — the multicast group / shard is
derived from the txid (GroupIndex from `txid[0:4]`), and HashKey identifies the
flow. A bundle has many txids, so it cannot fan out to multiple groups.

**Therefore coalescing is intra-group only:** a bundle may only contain txs
destined for the *same* shard / (S,G). The producer (or proxy) buckets txs by
group, then coalesces within each bucket. This is a real limit on the win — a
uniformly-random tx stream across many shards coalesces poorly; a shard-dense
stream coalesces well. The economics still hold (you pay for the pps you cause),
coalescing is just the escape valve where the traffic shape allows it.

## Latency trade-off

Coalescing trades latency for density: you either wait to fill a frame or batch
on a timer. So it must be:

- **opt-in per flow** — low-latency tx relay opts out and correctly pays the
  small-packet price; bulk/replay/archival flows opt in.
- **bounded** — a max coalescing delay and a max bundle size (≤ path MTU, or ≤
  jumbo where the path supports it). Never block a frame indefinitely to fill it.

## Decoalescing contract

Open: who unpacks, and where.

- **Edge-decoalesce** — the listener splits a bundle back into individual txs
  before fan-out to consumers. Keeps the consumer contract unchanged; the win is
  only on the producer→proxy and inter-node hops, not the last hop.
- **Consumer-decoalesce** — consumers that opt in receive bundles and unpack
  themselves. Maximises the pps win end-to-end but changes the consumer-side
  parser and the per-tx delivery semantics (ordering, dedup, NACK granularity).

The billing meter counts per-tx (for dedup/receipts) but bills on wire
bytes/pps, so either contract lowers the producer's bill via reduced pps.

## Open problems (the "needs work")

1. **Addressing** — confirm intra-group-only; define how a producer buckets by
   shard, and whether the proxy may re-coalesce across its inputs.
2. **Ordering & SeqNum** — per-bundle SeqNum vs per-tx; how this interacts with
   the per-flow monotonic SeqNum consumers rely on.
3. **NACK / retransmission** ([BRC-126](brc-126-retransmission-protocol.md)) —
   is the retransmission unit the bundle or the individual tx? Bundle-level is
   simpler but wastes bandwidth on a single-tx loss.
4. **EF payloads** ([BRC-128](brc-128-ef-frame-format.md)) — bundles of EF txs.
5. **Interaction with fragmentation** ([BRC-130](brc-130-fragmentation.md)) — a
   bundle must stay ≤ MTU by construction, so a tx that would itself fragment is
   never a bundle member; the two extensions are mutually exclusive per datagram.
6. **AF_XDP synergy** — TX-side batching already wants large frames; coalescing
   and the AF_XDP TX path reinforce each other (fewer, fuller descriptors).
7. **Manifest / subtree semantics** — how bundles relate to subtree batching
   ([BRC-132](brc-132-subtree-data.md)), which is already a batching concept.

## Next steps

- Prototype an intra-shard coalescer in the proxy with edge-decoalesce (lowest
  risk: consumer contract unchanged) and measure the pps reduction on a
  shard-dense stream.
- If the win justifies it, write the BRC (assign a number, nail the wire layout
  and the open problems above) and align with the rating packet-floor so the
  incentive is real and visible to customers.
