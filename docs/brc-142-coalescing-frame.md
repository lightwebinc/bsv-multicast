# BRC-142 — Multicast Transaction Bundle Frame Format

BRC-142 packs many small BSV transactions destined for the **same shard group and
subtree** into a single datagram (a *bundle*), so a payload that would otherwise
cross the fabric as N small packets crosses it as one. It is the **inverse of
[BRC-130](brc-130-fragmentation.md)**: BRC-130 splits one oversized transaction
across many datagrams; BRC-142 merges many small transactions into one. The goal
is to cut **packets-per-second** — the dominant data-plane forwarding cost — on
the replicated fabric and per-tunnel egress hops.

> **Status: PROPOSED — [bitcoin-sv/BRCs PR #164](https://github.com/bitcoin-sv/BRCs/pull/164)**
> (opened 2026-06-29, awaiting review). The brief normative text is the PR
> (`transactions/0142.md`); this document is the detailed design and rationale and
> is kept congruent with it.
>
> **Reference implementation: SHIPPED** across the data plane — `shard-common`
> bundle codec (`v0.14.0`), `shard-proxy` coalescing + verbatim relay,
> `shard-listener` edge-/consumer-decoalesce (`v1.6.5`), `retry-endpoint` bundle
> cache — and the commercial layer — `shard-proxy-1bsv` origin-side coalescing +
> spine relay, `shard-listener-1bsv` consumer-decoalesce + per-member metering.
> **§20 records the deliberate deployment decisions** (within-batch flush, spine
> relays / origin-only coalescing, listener-side re-bucketing, per-proxy opt-in).
> Design rationale, alternatives, and the simulation results that fixed the
> parameters below are in [coalescing-frame-format-DRAFT.md](coalescing-frame-format-DRAFT.md).

---

## 1. Design summary

- **Operator-side optimization, not a producer protocol.** Any frame-passing node
  (proxy, spine, inter-domain relay) may coalesce; senders are not required to
  participate and are not the billed party. Coalescing conserves transactions and
  reduces packets; billing on wire-pps passes the saving through automatically.
- **One bundle = one `(sender, group, subtree)` flow.** A bundle carries a single
  HashKey and a single SeqNum and slots into the existing BRC-126 NACK/retry
  machinery as a "fat frame." This is the keystone that keeps ordering, gap
  detection, and retransmission unchanged.
- **Intra-group, intra-subtree only.** The multicast group is derived per
  transaction (`groupIdx = f(TxID)`), and the subtree partitions a group further,
  so a bundle may only contain transactions sharing both.
- **Bounded.** A bundle never exceeds the path MTU (it does not fragment —
  mutually exclusive with BRC-130) and is flushed under a bounded coalescing
  delay.
- **Default delivery is edge-decoalesce.** The fabric carries bundles; the edge
  listener splits them back into individual transactions in the consumer's format
  (a multicast frame — BRC-124/128 at FrameVer 0x02 — or the transaction's base
  BRC-12 / BRC-30 format) before per-consumer egress, so the consumer contract is
  unchanged.
  Consumer-decoalesce (bundles end-to-end) is an opt-in.

---

## 2. Bundle Header Format (66 bytes)

Unlike BRC-130, a bundle **cannot** be layout-identical to a BRC-124 header: there
is no single TxID for N transactions. All multi-byte integers are big-endian.

| Offset | Size | Field         | Value / Notes                                                       |
| ------ | ---- | ------------- | ------------------------------------------------------------------- |
| 0      | 4    | Network Magic | 0xE3E1F3E8 (BSV mainnet P2P magic)                                  |
| 4      | 2    | Protocol Ver  | 0x02BF (703, BSV large-block baseline)                              |
| 6      | 1    | Frame Version | **0x08** — BRC-142 bundle                                           |
| 7      | 1    | Flags         | bit0 = `TxIDsPresent` (per-member 32-byte TxID present, all-or-none); bits 1–7 reserved (0) |
| 8      | 32   | Subtree ID    | The single 32-byte subtree shared by all members; zeros = unset (mempool flow) |
| 40     | 8    | HashKey       | `XXH64(senderIPv6 ∥ groupIdx ∥ subtreeID)`; the bundle flow identity; stamped by the coalescing node |
| 48     | 8    | SeqNum        | uint64 BE, monotonic per `(sender, group, subtree)` **bundle** flow, starts at 1; 0 = unset |
| 56     | 2    | GroupIdx      | Shard group index the bundle was built for (uint16 BE)              |
| 58     | 1    | ShardBits     | The shard-bit width `GroupIdx` was computed at (1–12); pins the generation |
| 59     | 1    | Reserved      | 0x00                                                                |
| 60     | 2    | TxCount       | Number of members (uint16 BE)                                       |
| 62     | 4    | PayloadLen    | Total byte length of the member section that follows (uint32 BE)    |
| 66     | \*   | Member section| `TxCount` members (§3)                                              |

### 2.1 Why `GroupIdx` + `ShardBits` are carried

BRC-124 carries no explicit group — it is derived from `TxID[0:4]`. A bundle could
likewise be derived by hashing any member, **except** during a re-shard: the
multicast address is generation-ambiguous (the same numeric index is a different
group at a different `ShardBits`), and `ShardBits` is exactly what is in flux
during the BRC-139 adoption window. Carrying `GroupIdx` (2 B) + `ShardBits` (1 B)
lets any node classify and re-bucket deterministically without hashing or relying
on the delivery address (which is absent on the unicast-recovery and relay paths).
The cost is amortized per-bundle (~0.4 B/member at 1500, ~0.06 B/member at jumbo),
so it does not affect the density goal — that constraint applies to *per-member*
fields only.

---

## 3. Member Format

Each member is a length-prefixed transaction:

| Offset (relative) | Size | Field   | Notes                                                              |
| ----------------- | ---- | ------- | ----------------------------------------------------------------- |
| 0                 | 2    | TxLen   | Member transaction byte length (uint16 BE)                        |
| 2                 | 32   | TxID    | **Present only when `Flags.TxIDsPresent`**; raw 256-bit TxID      |
| 2 or 34           | TxLen| Tx      | Raw serialised BSV transaction (standard **or** EF — §6)          |

- **`TxLen` is a fixed uint16, not a varint and not uint32.** A member must fit the
  datagram (≤ MTU, ≤ 64 KB even at super-jumbo); transactions that would exceed the
  MTU are ineligible (they go via BRC-130). UDP's own length field is 16-bit, so a
  4 GiB uint32 is dead range; fixed width keeps the member parse branch-free.
- **Per-member TxID is optional.** Carry it when receivers need cheap
  dedup/billing/receipts; otherwise omit and recompute on receipt — `SHA256d(Tx)`
  for a raw member, or the hash of the de-extended *base* transaction for an EF
  member (the canonical id is **not** a hash of the EF bytes — see §6). Trades
  32 B/member for a hash; **not** a retransmission aid (§7).
- The parser walks members until `PayloadLen` bytes are consumed; `TxCount` is a
  convenience and a cross-check. A `TxCount`/`PayloadLen` disagreement is malformed
  (§11).

---

## 4. Addressing, Flow Identity, and Constraints

- **Intra-group, intra-subtree.** Every member satisfies
  `GroupIndex(member.TxID) == GroupIdx` at `ShardBits`, and every member shares the
  bundle's `Subtree ID`. A coalescing node buckets its input by `(group, subtree)`
  and packs within each bucket.
- **Flow identity.** The bundle's `(HashKey, SeqNum)` is the per-`(sender, group,
  subtree)` flow exactly as BRC-124. Subtree-filtering subscribers track gaps on
  the one bundle stream per `(group, subtree)` they subscribe to.
- **Density requirement.** Coalescing gain depends on `(group, subtree)` density
  within the coalescing window. Uniformly-random traffic across many shards
  coalesces poorly; shard-/subtree-dense (bulk, replay, large-subtree) traffic
  coalesces well. This is a property of the traffic shape, not a defect.
- **MTU bound.** `66 + Σ(member encoded size) ≤ path MTU`. A bundle never
  fragments; BRC-142 and BRC-130 are **mutually exclusive per datagram** (§9).

---

## 5. Coalescing (Encoder)

A coalescing node (proxy, spine, relay):

1. Decodes each input BRC-124/128 frame; computes `groupIdx = GroupIndex(TxID)` at
   the active `ShardBits`.
2. Buckets frames by `(groupIdx, subtreeID)`.
3. Packs each bucket's frames, in arrival order, into one or more bundles. A new
   bundle is started when adding the next member would exceed **either** the MTU
   budget **or** `TxCount = 65535`. (At 1500 MTU the MTU budget binds first — §10.)
4. Stamps `GroupIdx`, `ShardBits`, `SubtreeID`, the flow `HashKey`, and a monotonic
   per-flow bundle `SeqNum`.
5. Flushes a bundle when it reaches the MTU/count cap **or** when the oldest
   buffered member reaches the configured **max coalescing delay** (§8), whichever
   first.

Coalescing is **opt-in**: low-latency paths opt out and pay the small-packet
price; bulk/replay/archival paths opt in.

> **Implementation note (§20).** The reference proxy realizes the bounded-delay
> requirement as a **within-batch flush** — it packs only the eligible frames of
> one receive batch (`recvmmsg`) and flushes at batch end, so there is *no timer
> and no configurable delay knob*; added latency is the batch dwell (≈0). This is
> the limit of the "window is irrelevant at 1500 MTU" finding (§14). Opt-in is
> **per proxy** (a `-coalesce` flag), not yet per-flow; per-flow opt-in is a future
> refinement.

---

## 6. Extended Format (BRC-128) Members

A bundle uses **one format** for standard and EF members. An EF transaction
self-identifies via the BRC-128 6-byte marker (`00 00 00 00 00 EF` at payload
bytes 4–9) exactly as a standalone EF frame, so:

- No per-member type flag and no separate bundle version are needed.
- Standard and EF members may be **mixed** in one bundle; each self-describes.
- On edge-decoalesce, each member is re-emitted in the consumer's format (a
  multicast frame — BRC-124/128 at `FrameVer 0x02` — or the transaction's base
  BRC-12 / BRC-30 format) with the EF marker intact where present, so standard/EF
  correctness is preserved without extra metadata.
- An EF member's canonical TxID is the hash of its *de-extended* base transaction,
  not of the EF bytes, so **EF members SHOULD carry the per-member TxID** (§3)
  rather than rely on recompute.

---

## 7. Retransmission (BRC-126)

**The retransmission unit is the whole bundle.** Loss is whole-datagram (UDP is
atomic): a lost bundle is one the receiver never saw, so it observes only a gap in
the bundle `SeqNum` stream and cannot enumerate the members to request a subset. A
per-member index or carried TxID does **not** enable partial retransmission.

- A retry endpoint caches a bundle by `(HashKey ∥ SeqNum)` and retransmits it to
  the bundle's group on NACK — identical mechanics to a BRC-124 frame, no retry
  endpoint changes beyond treating a bundle as an opaque cached frame.
- Once edge-decoalesced, retransmission reverts to per-tx BRC-124/126 on the last
  hop, so a bundle's NACK lifetime is bounded to the coalesced segment.

---

## 8. Decoalescing Contract

A multicast group delivers one identical datagram to every subscriber, so a group
cannot mix bundle-receivers and individual-frame-receivers from one emission. This
dictates where decoalescing happens:

- **Edge-decoalesce (default, REQUIRED to support).** Coalesce across the shared
  fabric; the edge listener splits the bundle into individual transactions in the
  consumer's format (a multicast frame — BRC-124/128 at `FrameVer 0x02` — or the
  transaction's base BRC-12 / BRC-30 format) before the per-consumer unicast
  fan-out. The consumer
  contract is unchanged. On the split, each emitted frame inherits the bundle's `SubtreeID`,
  carries its own (carried or recomputed) `TxID`, and the egress side **re-stamps
  per-tx `SeqNum`** on its own flow (the bundle `SeqNum` is frame-bound and does
  not survive the split).
- **Consumer-decoalesce (OPTIONAL).** A consumer that advertises bundle capability
  receives bundles and unpacks them itself, maximising the end-to-end pps win. It
  changes the consumer-side parser and per-tx delivery semantics; member order
  within a bundle is the array order.

---

## 9. Interaction with Fragmentation (BRC-130)

A bundle is `≤` MTU by construction, so a transaction that would itself need
fragmentation is **never** a bundle member; the two extensions are mutually
exclusive per datagram. A coalescing node MUST exclude any transaction whose
encoded member size exceeds `MTU − 66 − memberOverhead` and route it through
BRC-130 instead.

---

## 10. MTU Sizing

`capMembers = ⌊(MTU − 66) / (TxLen + memberOverhead)⌋`, where `memberOverhead` is
2 (TxIDs omitted) or 34 (TxIDs carried).

| Path MTU            | cap (164 B tx, no TxID) | cap (avg ~270 B mixed) |
| ------------------- | ----------------------- | ---------------------- |
| 1500 (public)       | ~8                      | ~5                     |
| 1440 (ip6gre/WG)    | ~8                      | ~5                     |
| 9000 (jumbo)        | ~53                     | ~32                    |
| 64000 (super-jumbo) | ~385                    | ~235                   |

**The design baseline is 1500.** On the public internet (and over ip6gre/WireGuard
tunnels, ~1440) the cap is ~8 members, giving a ~5–8× packet reduction. Jumbo —
available only on operator-controlled underlay (private peering / DX / same-DC L2),
typically inter-spine — raises the cap into the tens and is the prerequisite for
order-of-magnitude reduction. `TxCount` (uint16, max 65535) is sufficient with
>10× headroom even at a 64 KB super-jumbo; `PayloadLen` bounds the parse so a count
overflow cannot desync it.

---

## 11. Re-bucketing (Re-shard / Cross-domain Relay)

When a relay forwards a bundle into a domain or generation running a **different
`ShardBits`**, the bundle's group membership maps to different child/parent groups
there. The relay re-buckets: it decoalesces and re-coalesces at the target
`ShardBits`, routing each member to its correct child group (recomputing the group
from each member's TxID), preserving `SubtreeID`, and re-stamping `HashKey`/`SeqNum`
on the new per-`(group, subtree)` flows. The relay learns the target `ShardBits`
from the inter-domain **[BRC-139](brc-139-shard-manifest.md)** manifest, so the
re-bucket is deterministic, not inferred.

**Normative rule (load-bearing — derived from the loss/over-delivery analysis):** a
relay MUST NOT raw-deliver a parent-group bundle to a subscriber whose interest is
finer than the bundle's `ShardBits`. Raw parent-bundle delivery over-delivers
`2^k` for a `k`-shardbit-finer subscriber, which at 1500 MTU is already
net-negative at `k = 1` (and 2–8× worse at `k = 2..4`). The relay MUST re-bucket to
the subscriber's `ShardBits` first; re-bucketed (child) delivery restores the full
benefit for any `k`. Re-shard cutover follows the BRC-139 `Successor` block
`TransitionEpoch`: during the adoption window a node handles both generations and
routes each bundle by its tagged `ShardBits`; past `TransitionEpoch` the old
generation retires.

> **Implementation status (§20) — ENFORCED at the delivery edge.** The **listener**
> (`shard-listener` `processBundle`, used by both the OSS and the `…-1bsv`
> commercial listener) applies the rule: before filter/dedup/delivery it compares
> the bundle's `ShardBits` to its own generation, and on a mismatch re-buckets to
> the local `ShardBits` (`bundle.Rebucketer` — split + re-coalesce each member into
> its correct local group) so neither edge- nor consumer-decoalesce can
> over-deliver a coarser bundle to a finer subscriber. Re-bucketing is applied
> **once, at the delivery edge** (where subscribers attach), not at every hop: the
> mid-fabric `shard-proxy-1bsv` spine relay (`Forwarder.ProcessBundle`)
> deliberately re-emits **verbatim** — it forwards toward listeners, it does not
> deliver to subscribers, so it has no finer-subscriber to protect. Metric
> `bsl_bundles_rebucketed_total`. **Caveat:** re-bucketing re-stamps child-flow
> HashKeys from a re-emit identity, so own-traffic exclusion (which keys on the
> original sender's HashKey) does not apply to re-bucketed cross-generation flows —
> a documented re-shard-boundary limitation, not a common-path regression.

---

## 12. Inter-domain

Bundles cross domains over **SSM only** (RFC 8815 forbids inter-domain ASM): the
data-plane shard groups and the BRC-139 manifest must be SSM at global scope
(`FF3E::B:idx`; manifest beacon `FF3E::B:FFFD`) — deployment Posture D. A bundle
rides the inter-domain re-emit relay as-is (source re-stamp only, like any frame);
the relay re-buckets only on a `ShardBits` mismatch (§11) or a smaller onward MTU
(§10). SSM source discovery is bootstrapped (`sources.bootstrap.manifest`), which
is also the trust gate for which peer domains' manifests a node accepts.

---

## 13. Dedup & Own-traffic Exclusion

Dedup (`bsp:tx:` ingress, `bsl:egr:` egress) and own-traffic exclusion are
**per-transaction**, so they operate on bundle **members**, not the bundle. The
edge-decoalesce path makes this natural (decoalesce, then dedup/exclude each
member). A coalescing node SHOULD claim/check each member's TxID before packing,
and MUST drop individual already-claimed members rather than the whole bundle.
Consumer-decoalesce moves member-level dedup to the consumer.

---

## 14. Latency

Coalescing trades latency for density, bounded by the max coalescing delay.
Because a bundle also flushes on the MTU cap, the dwell is self-limiting: at 1500
MTU the small cap (~8) fills in well under the window at fabric-scale rates, so p99
dwell is sub-millisecond and the window setting is largely irrelevant (the bundle
is MTU-capped, not window-capped). Set the max delay to roughly the bundle
fill-time for the flow's arrival rate; a delay beyond fill-time adds latency
without adding density. Recommended starting point at 1500: `≤ 250 µs`–`1 ms`.
The reference implementation takes this to its limit and uses **no delay knob at
all** — a within-batch flush (§5/§20), so its added latency is the receive-batch
dwell (≈0) and bundles form only when a batch carries enough same-flow members.

---

## 15. Metrics

The metrics the **reference implementation actually emits** (the earlier
aspirational `flush_size`/`flush_timer`/`ineligible`/`bsl_decoalesce_*` names are
**not** emitted — there is no flush timer (§5), and decoalesced members are
counted on the shared `bsl_frames_forwarded_total`):

| Metric (label)                                   | Where         | Description                                                                 |
| ------------------------------------------------ | ------------- | -------------------------------------------------------------------------- |
| `bsp_coalesce_bundles_total`                     | proxy         | Bundle datagrams emitted (origin pack **and** verbatim relay re-emit)      |
| `bsp_coalesce_members_total`                     | proxy         | Member transactions packed into bundles                                    |
| `bsp_coalesce_members_per_bundle`                | proxy         | Histogram of members/bundle (achieved R)                                   |
| `bsp_coalesce_flush_total{reason}`               | proxy         | Flushes by reason: `batch` (origin within-batch), `relay` (spine verbatim), `encode_error` |
| `bsp_packets_dropped_total{reason}`              | proxy         | Relay rejects: `bundle_short`, `bundle_malformed` (failed magic/length check) |
| `bsp_spine_tx_oversize_drops`                    | afxdp spine   | Bundle `62 + len > frame-size` dropped at the AF_XDP TX (§10/§20)          |
| `bsp_spine_tx_saturated_drops`                   | afxdp spine   | AF_XDP TX ring saturated                                                   |
| `bsl_frames_received_total{type="brc142"}`       | listener      | Bundles received at the edge                                               |
| `bsl_frames_dropped_total{reason="bundle_decode_error"}` | listener | Bundles that failed to decode                                          |
| `bsl_frames_forwarded_total`                     | listener      | Decoalesced member frames forwarded (shared with non-bundle frames)        |
| `bsl_bundles_rebucketed_total`                   | listener      | Bundles re-bucketed to the local ShardBits generation before delivery (§11) |
| `consumer_egress_packets_total` vs `consumer_egress_txs_total` | listener-1bsv | Wire datagrams (bill on this) vs member transactions (receipts); diverge only when a bundle is delivered **whole** to a bundle-capable consumer |

---

## 16. Error Handling

| Condition                              | Action                                          |
| -------------------------------------- | ----------------------------------------------- |
| FrameVer ≠ 0x08                        | Not BRC-142; decode per its own version         |
| Bad magic                              | Silent drop                                     |
| Datagram shorter than 66-byte header   | Silent drop                                     |
| `PayloadLen` > datagram remainder      | Silent drop (truncated)                         |
| Member `TxLen` runs past `PayloadLen`  | Silent drop (truncated member section)          |
| `TxCount` members not consumed exactly | Malformed; drop bundle (`ErrCountMismatch`)     |
| Member group ≠ `GroupIdx` at `ShardBits` | Encoder invariant — origin MUST pack only same-group members (not decoder-enforced) |
| Member tx > MTU budget                 | Encoder invariant — origin MUST route via BRC-130, never pack |

> **Implementation note (§20).** The reference impl makes these drops **counted,
> not silent** (`bundle_short` / `bundle_malformed` on the relay; `bundle_decode_error`
> on the listener decoder) — silence would hide upstream corruption, so each drop
> increments a labelled counter. The decoder (`bundle.Decode`) enforces magic,
> short-header, `PayloadLen ≤ datagram`, per-member truncation, **and exact
> consumption** (`ErrCountMismatch` when the `TxCount` members do not consume
> `PayloadLen` exactly — a `TxCount`/`PayloadLen` disagreement). The one row it does
> **not** enforce is *"member group ≠ `GroupIdx`"*: that is an **encoder invariant**
> (like the BRC-130-routing row), not a decode-time check — verifying it would cost
> a hash per member and a routing engine the decoder does not carry, so the decoder
> trusts the coalescing origin to pack only same-group members.

## 17. Constants Reference

| Name                | Value | Hex    | Description                              |
| ------------------- | ----- | ------ | ---------------------------------------- |
| FrameVerBundle      | 8     | 0x08   | BRC-142 bundle frame version             |
| BundleHeaderSize    | 66    | 0x42   | Bundle header size in bytes              |
| FlagTxIDsPresent    | 1     | 0x01   | Flags bit0: per-member TxIDs present      |
| MaxMembers          | 65535 | 0xFFFF | TxCount ceiling (uint16)                 |
| MaxMemberTxLen      | 65535 | 0xFFFF | Largest member tx **length** (uint16 TxLen) — a length ceiling, distinct from the `MaxMembers` count |

The member-section field sizes — length prefix `2` and per-member TxID `32` — are
fixed by the member format (§3) and kept unexported in the reference codec
(`memberLenSize` / `memberTxIDSize`); they are structural, not part of the public
constant API, so the brief BRC omits them (the member-format table already states
them).

---

## 18. Infrastructure Impact (as shipped)

- **Proxy (`shard-proxy`, OSS)** — opt-in coalescing stage at an **origin** proxy:
  bucket eligible BRC-124/128 frames by `(sender, group, subtree)` within a receive
  batch, pack to the MTU/count cap, stamp, emit FrameVer 0x08. Plus a verbatim
  **relay** path (`ProcessBundle`): a node that *receives* a bundle re-emits it
  unchanged to the group in its header. Default-off (`-coalesce`).
- **Commercial proxy (`shard-proxy-1bsv`)** — coalesces on the **origin** modes
  (collapsed `-xdp-mode kernel`, ingress); the **spine** relays bundles verbatim
  (one bundle → one AF_XDP TX descriptor). `-mode collapsed -xdp-mode native|skb`
  + `-coalesce` is a **fatal** combo: the in-place AF_XDP TX cannot carry a
  heap-allocated bundle datagram. (Why not coalesce at the spine: the coalescing
  divert needs the per-source IP, which a spine re-emit does not have — §20.)
- **Listener (`shard-listener`, OSS)** — `processBundle` runs ahead of the existing
  filter/own-exclusion/fan-out path: filter + gap-track + dedup at **bundle**
  granularity, then **edge-decoalesce** (split into BRC-124/128 frames, re-stamp
  per-tx egress SeqNum) — the default. An optional `BundleSink`/`fanout.SendBundle`
  seam delivers whole bundles to consumers that advertise `BundleCapable`
  (consumer-decoalesce). A bundle built at a different `ShardBits` generation is
  **re-bucketed to the local generation** in `processBundle` before delivery
  (`bundle.Rebucketer`), so neither delivery mode over-delivers a coarser bundle to
  a finer subscriber (§11/§20).
- **Commercial listener (`shard-listener-1bsv`)** — `policy.ConsumerSpec.BundleCapable`
  (broker-pushed) selects whole-bundle delivery; the meter records **wire packets
  (bill) and member transactions (`Txs`, receipts)** separately, so a whole-bundle
  delivery bills one packet for N transactions.
- **Retry endpoint** — caches/retransmits a bundle as an opaque frame keyed by
  `(HashKey ∥ SeqNum)`; group derived from the bundle header. No new logic beyond
  the larger frame.
- **Firewall / classifiers** — magic and `ProtoVer` are at the same offsets; the
  TxID/HashKey/SeqNum offsets differ from BRC-124, so any classifier that reads
  those fields must branch on FrameVer 0x08.

---

## 19. References

- [BRC-124: Multicast Transaction Frame Format](brc-124-frame-format.md) — base frame coalesced by BRC-142
- [BRC-128: Extended Format (EF) Frames](brc-128-ef-frame-format.md) — EF members (self-identifying)
- [BRC-126: Retransmission Protocol](brc-126-retransmission-protocol.md) — bundle-unit NACK/retransmit
- [BRC-129: Multicast Addressing](brc-129-multicast-addressing.md) — group/subtree addressing, SSM scope
- [BRC-130: Fragmentation](brc-130-fragmentation.md) — the inverse; mutually exclusive per datagram
- [BRC-139: Shard Manifest](brc-139-shard-manifest.md) — `ShardBits`/generation coordination, re-shard `Successor`
- [coalescing-frame-format-DRAFT.md](coalescing-frame-format-DRAFT.md) — design rationale + simulation results

---

## 20. Implementation status & spec delta

The wire format (§2, §3), constants (§17), addressing/flow identity (§4),
retransmission (§7), edge-/consumer-decoalesce (§8), re-bucketing (§11), and EF
handling (§6) are implemented as specified. The rows below are **deliberate
deployment decisions** (not gaps) — they do **not** change the wire format, so an
independent implementation that follows §2–§17 interoperates.

| Topic | Decision | Rationale |
| --- | --- | --- |
| Flush trigger (§5/§8/§14) | **Within-batch flush** — pack one `recvmmsg` batch, flush at batch end; **no timer, no delay knob** | Zero added latency; the limit of the "window irrelevant at 1500 MTU" finding. A delay timer is **deferred** while the design targets 1500 MTU (bundles MTU-cap quickly). Bundles form under load; at low rate it degrades to ~1 member/bundle |
| Opt-in granularity (§5/§1) | **Per proxy** (a `-coalesce` flag) | Per-flow opt-in is a possible future refinement; per-proxy is sufficient for the pps win and matches how operators run the fleet |
| Where coalescing runs (§1/§18) | **Origin only** (collapsed/ingress); the **spine relays, does not coalesce** | The coalescing divert keys the bundle HashKey on the per-source IP for own-traffic exclusion; a spine re-emit has `src=nil`, so coalescing there would mis-key the flow |
| Re-bucketing (§11) | **Implemented at the listener** (the delivery edge), via `processBundle` generation-alignment; the mid-fabric spine relay stays verbatim | Re-bucket once where subscribers attach, not per hop. Own-traffic exclusion does not survive a cross-generation re-stamp (documented §11 caveat) |
| Drop visibility (§16) | Drops are **counted, not silent** (`bundle_short`/`bundle_malformed`/`bundle_decode_error`/`ErrCountMismatch`) | Silence hides upstream corruption. The one un-enforced row (member group ≠ GroupIdx) is an **encoder invariant**, not a decode-time check (a hash + routing engine per member) |
| Metric names (§15) | Actual emitted names differ from the original draft | See the §15 table (the real names) |

**Congruence with [PR #164](https://github.com/bitcoin-sv/BRCs/pull/164)
(`transactions/0142.md`):** congruent on wire format and behavior. The member-length
ceiling is named **`MaxMemberTxLen`** consistently in the PR, this doc, and the code
(`shard-common/bundle`). The member field sizes (`2`, `32`) stay unexported in the
codec and appear in both documents' member-format tables, so the brief PR correctly
omits them from its constants list. The PR's error-handling table is a deliberate
subset of §16. **No PR changes are required.**
