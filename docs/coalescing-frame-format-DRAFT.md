# Coalescing Frame Format — design rationale (normative spec: BRC-142)

> **Status: DRAFT.** The normative wire spec is
> [BRC-142](brc-142-coalescing-frame.md); this document is the **design
> rationale** — why coalescing is operator-side not a producer carrot, the
> alternatives weighed, the open problems and how each was resolved, and the
> simulation results (`coalesce-spike`) that fixed the parameters. Read BRC-142
> for the wire format; read this for the reasoning behind it. Not yet on the wire.

## Why

Data-plane forwarding cost is dominated by **packets per second**, not bits per
second — fan-out is a per-packet syscall/worker cost. BSV transaction traffic is
small-packet (avg ~217 B), so a Mbit of txs is ~7× the packets of a Mbit at MTU
and ~40× at jumbo. That makes small-packet flows the expensive case, and it's
the reason the rating engine prices a **pps-aware floor** (`max(bytes,
packets×min_packet_bytes)`) — see `1bsv-ops/services/rating/docs/rate-derivation.md`.

Coalescing packs many small transactions into one datagram (up to path MTU /
jumbo), so the same payload crosses the fabric as a fraction of the packets.
**This is a transparent operator-side optimization, not a producer incentive.**
Any frame-passing node already decodes every datagram (parse, dedup, stamp,
re-emit), so the proxy — or a spine, or an inter-domain gateway — can bucket by
group and coalesce on its own. The sender is not involved and is not the billed
party: ingress is permissionless/free; the *receiver* pays on delivered units
(`rate-derivation.md`). Because the meter bills on **wire pps**, a node that
coalesces lowers the delivered-unit pps and the saving flows to the bill
automatically — no carrot, no opt-in, just honest cost-based pricing.

Producer-side coalescing is therefore **niche, not the model**: it only relieves
one heavy sender's own uplink and that proxy's RX syscall load, only when a
single sender emits many same-group txs in a window, and nothing bills the
producer to motivate it. Leave it possible; build the economics on operator-side
coalescing.

This is the **inverse of [BRC-130 fragmentation](brc-130-fragmentation.md)**:
130 splits one oversized tx across many datagrams; coalescing packs many small
txs into one datagram.

## Shape (sketch)

A new **frame version** (BRC-124 byte 6; `0x01`–`0x07` are already assigned —
`0x03` is BRC-130 — so the next free value is `0x08`) marking a *bundle* frame.
Unlike [BRC-130](brc-130-fragmentation.md), a bundle **cannot** be layout-identical
to a [BRC-124](brc-124-frame-format.md) single-tx header — there is no single TxID
for N transactions. But a bundle **is** a single `(sender, group, subtree)` flow
(open problem 7), so it carries one HashKey + one SeqNum like any flow. The header
carries bundle-level metadata; the payload is a length-prefixed sequence of
transactions:

```
[ bundle header ]                    (~65 B, amortized over all members)
  network magic / protocol ver / frame version = 0x08
  flags (uint8)                     (bit0 = per-member TxIDs present, all-or-none)
  groupIdx (uint16) + shardBits (uint8)   (#1: fast demux + pins the generation)
  SubtreeID (32B)                   (#7: a bundle is one (group, subtree) flow)
  HashKey (8B)                      (XXH64(sender ∥ groupIdx ∥ subtreeID); stamped by proxy)
  bundle SeqNum (8B)                (#2: monotonic per (sender,group,subtree); retransmission key)
  tx count (uint16)                 (≤ ~4.7k even in a 64KB super-jumbo — see sizing)
  bundle payload length (uint32)    (bounds the parse; count is a convenience)
[ repeated × count ]
  tx length (uint16)                (member ≤ datagram ≤ 64KB; uint32 was 4 GiB overkill)
  [ 32-byte TxID ]                  (only when flags bit0 set; else recompute)
  raw serialised tx bytes           (standard or EF — EF self-identifies via its 6B marker, #4)
```

**Why carry `groupIdx` + `shardBits`** (reversing an earlier call to derive them).
In steady state the group *is* derivable — hash any member (double-SHA256 →
`TxID[0:4]` → groupIdx) at the agreed shardBits, or read the `(S,G)` destination
via `IPV6_PKTINFO`. But three cases break derivation, and all of them bite during a
reshard: (1) the multicast **address is generation-ambiguous** — the same numeric
`IDX` is a *different* group at a different `shardBits` (`IDX=5` = top-4 bits at
shardBits=4 but top-8 bits at shardBits=8), so the address alone can't name the
generation; (2) during the quorum/hysteresis window the `shardBits` to derive
*with* is exactly what's in flux; (3) off the multicast path (unicast recovery,
relay re-bucket, cache) there is no delivery address at all. Carrying `groupIdx`
(2B) + `shardBits` (1B — groupIdx alone doesn't say how many bits are significant)
resolves all three: downstream demuxes without hashing or PKTINFO, a re-bucketing
relay maps source→target generation deterministically, and the tag substitutes for
the per-member TxID's disambiguation role when TxIDs are omitted. This does **not**
violate the density rule — that rule is about *per-member* bytes; a 3-byte
*per-bundle* tag amortizes to ~0.07 B/member at jumbo.

### Field sizing — per-member overhead is the density tax

Coalescing exists to buy density, so every per-member byte spent is win given
back. Right-size accordingly:

- **`tx count` (uint16)** — sufficient with >10× headroom. Bundles stay ≤ path
  MTU by construction (no IP fragmentation; BRC-130 mutually exclusive — open
  problem 5), and the frame-size ceiling is real: IEEE 802.3 standardises payload
  only to 1500; jumbo (≤9216) is vendor de-facto, never IEEE-ratified;
  super-jumbo to 64KB has been demonstrated (Supercomputing 2005) but CRC-32
  error-detection strength degrades with length — that wall keeps frames from
  growing, and high-speed Ethernet (800GbE/1.6TbE) scales lanes, not frame size.
  Even a 64KB super-jumbo packed with theoretical-minimum (~14B) txs is ~4.7k
  members; 65,535 only overflows above ~900KB of sub-minimal txs, which no path
  MTU provides. `bundle payload length` bounds the parse anyway, so a count
  overflow can't desync — worst case caps members/bundle.
- **`tx length` (fixed uint16, not uint32, not varint)** — a member must fit the
  datagram (≤64KB even at super-jumbo); large txs are excluded by construction
  (they go via BRC-130, never a bundle). UDP's own length is 16-bit, so uint32's
  4 GiB is dead range. Fixed-width over varint: it already covers any
  single-datagram member and keeps the member parse branch-free.
- **Per-member `TxID` (32B)** — the dominant tax (+16% on a ~195B tx). Recompute
  by default; carry only where the receiver needs cheap dedup/billing.

Per-tx TxID may be carried (cheap dedup/billing/parse — *not* retry; see open
problem 3) or recomputed on receipt (smaller frames). That's an explicit trade to
settle in the BRC.

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

**Tightened by open problem 7:** the real bucket is `(group, SubtreeID)`, not group
alone — a bundle carries one SubtreeID in its header, so coalescing needs
*subtree*-density, not merely shard-density. Large subtrees (a batch's txs that land
in one group) still pack well; small/sparse subtrees do not.

## Latency trade-off

Coalescing trades latency for density: you either wait to fill a frame or batch
on a timer. So it must be:

- **opt-in per flow** — low-latency tx relay opts out and correctly pays the
  small-packet price; bulk/replay/archival flows opt in.
- **bounded** — a max coalescing delay and a max bundle size (≤ path MTU, or ≤
  jumbo where the path supports it). Never block a frame indefinitely to fill it.

**MTU reality — design baseline is 1500, jumbo is a per-segment upside.** The
public internet path MTU is 1500 (less over ip6gre/WireGuard tunnels, ~1440),
which caps a bundle at **~8 members** of a 164B tx (~5 on a realistic mixed-size
stream) versus ~53 at 9000 jumbo. Consequences, all measured in the spike's
`simbench`/`ppsbench`: (a) **latency is small and self-limiting** — at fabric-scale
rates a bundle fills in <250µs, so p99 dwell is sub-millisecond and the window
setting barely matters (you are MTU-capped, not window-capped); (b) the **pps
reduction is ~5–8×**, not 35–53×; (c) on a bandwidth-bound link the **TPS gain is
still ~1.9×** — header stripping (the 92B BRC-124 header dwarfs a small tx)
dominates and works at any MTU; (d) the **A1 re-bucket rule is stricter** — at 1500
even a 1-shardbit-finer subscriber fed raw parent bundles is already net-negative.
The order-of-magnitude pps reduction (and the fat-pipe TPS scaling it unlocks —
otherwise TPS caps pps-bound at ~9M×R regardless of bandwidth) requires **jumbo**,
i.e. a controlled-MTU underlay: available on **fabric / inter-spine segments the
operator owns** (private peering / DX / same-DC L2), never on public 1500 ingress.
Since the default edge-decoalesce model coalesces across the fabric and hands
consumers individual frames over the 1500 last hop, jumbo-where-it-matters is the
inter-spine path, not ingress. Treat 1500 as the baseline; jumbo is a per-segment
optimization.

## Decoalescing contract

A multicast group delivers one identical datagram to **every** subscriber, so a
group cannot mix bundle-receivers and individual-frame-receivers from one
emission. That single fact — not incentive design — dictates where you may
coalesce:

- **Edge-decoalesce (default).** Coalesce across the shared fabric (proxy egress →
  fabric links → listener ingress — the replicated hops where pps cost is
  highest), then the edge listener splits the bundle back into individual txs
  before the per-consumer unicast fan-out. The consumer contract stays BRC-124;
  no consumer-visible format, no opt-in. This is "the proxy just optimizes."
- **Consumer-decoalesce (opt-in).** Consumers that can parse bundles accept them
  and unpack themselves. Maximises the pps win end-to-end but changes the
  consumer-side parser and per-tx delivery semantics (ordering, dedup, NACK
  granularity). This is the **only** variant where an incentive applies — a
  consumer trading a parser change for a lower wire-pps bill — and it is a
  *consumer* opt-in, never a producer carrot.

The meter counts per-tx (for dedup/receipts) but bills on wire bytes/pps, so the
*receiver's* delivered-unit bill drops with the reduced pps under either contract
(the sender has no bill).

### Inter-domain: bundles cross fine; re-pack only when forced

Inter-domain delivery passes through a **re-emit relay** (spine source-relay /
MBGP border edge), but for **source-identity / RPF / underlay-isolation** reasons
that apply to *every* frame, not bundles specifically: the downstream domain RPFs
against its own sources, so the relay re-emits under its own `(S_relay, G)` and
re-keys the flow (recovery still works — retransmission keyed on HashKey,
NACK-proxying one-hop-bounded).

**Format is not a barrier.** A bundle is a speced frame (a BRC); a remote domain
running the same software — or any conformant implementation — parses it
identically. The relay carries a bundle across **as-is**, exactly as it carries
an individual frame (source re-stamp only), and remote listeners consume it.
Bundles work end-to-end.

A relay must actually **decoalesce / re-pack** only when something forces it:

- **shardBits / reshard-generation mismatch** — a bundle is atomic to group `G`
  at the upstream's `shardBits`; if the downstream runs a different `shardBits`,
  `G`'s membership maps to different (child/parent) groups there, so the relay
  splits the bundle into the downstream's groups (or accepts redundant carriage +
  per-tx filtering at the edge). This is the genuinely bundle-specific case — but
  the relay does not *guess* the downstream `shardBits`: it reads it off the
  **inter-domain [BRC-139](brc-139-shard-manifest.md) manifest** (see the SSM
  caveat below), so the re-bucket is deterministic.
- **smaller onward-path MTU** — a bundle sized to the upstream path (e.g. jumbo)
  that exceeds the inter-domain MTU cannot fragment (bundles and BRC-130 are
  mutually exclusive), so it must be re-packed to the smaller MTU.

Better still, the BRC-139 manifest is the cross-domain channel for *avoiding* the
mismatch in the first place: it carries `ShardBits` + `GenerationID` + the
`Successor` block (`TransitionEpoch`), so domains can converge on a generation —
or bridge a live reshard transition — deterministically. When the domains are
reshard-coordinated and the MTU holds, the bundle flows untouched; a domain may
*also choose* to re-pack to suit its own consumer-mix, but that is a local
optimization, not a requirement.

**Caveat — inter-domain is SSM-only, and bootstrapped.** None of this rides ASM:
RFC 8815 forbids inter-domain ASM, so the data-plane shard groups *and* the
manifest beacon must be **SSM at global scope** (`FF3E::B:idx`; the manifest
beacon at `FF3E::B:FFFD`) — DESIGN.md **Posture D**. ASM (`FF05::B:idx`) is
intra-domain / lab only and does not cross a domain boundary. SSM has no source
discovery (no RP/MSDP), so the manifest beacon group is itself joined against a
**bootstrapped source list** (`sources.bootstrap.manifest`, IPv6 literals or DNS
re-resolved on refresh) — the out-of-band trust anchor. That bootstrap *is* the
adoption gate: you receive a peer domain's manifest only by configuring its
announcer `/128`s, so a foreign domain's pilots never silently drive your topology
(BRC-139's Authoritative-quorum + hysteresis still apply on top; manual pins win).
Note the recursion: the manifest distributes the *data-plane* SSM sources (the
`Flags.SourcesValid` payload), but its own transport can't bootstrap itself — the
control-group sources are **configured, not learned**. The same holds for the data
plane: the inter-domain re-emit relay is an **SSM source** the downstream RPFs
against, so it must appear in the downstream's source set (via the manifest
`Sources` payload) for bundles — or any frames — to arrive at all.

## Open problems (the "needs work")

1. **Addressing & who coalesces** — **resolved.** Intra-group-only; any
   frame-passing node may coalesce (proxy, spine, inter-domain gateway). The bundle
   header carries `groupIdx` + `shardBits` (see Shape), so downstream
   classification and reshard re-bucketing are deterministic, not inferred.
   **Reshard window** (the previously-unclear quorum/cutover): during the BRC-139
   quorum + hysteresis window a component handles *both* generations — it joins old
   and new groups (BRC-139 bridging) and routes each bundle by its tagged
   `shardBits`. The cutover point is the `Successor` block's `TransitionEpoch`: past
   it the old generation retires, and a relay re-buckets any bundle still tagged
   with the old `shardBits` (or drops it as stale after a grace). Where `shardBits`
   agree, the bundle is carried as-is. Open sub-point: whether a node may
   *re-coalesce* across its inputs vs only pass / re-bucket.
2. **Ordering & SeqNum** — **resolved: SeqNum is frame-bound (one per bundle), not
   per member.** The bundle is a single `(sender, group, subtree)` flow with a
   monotonic bundle SeqNum; retransmission keys off `(HashKey ∥ SeqNum)` at bundle
   granularity, so the bundle drops into the existing NACK/retry machinery as a
   "fat frame." Members carry no SeqNum; ordering within a bundle is the member
   array order. A subtree-filtering consumer tracks gaps on the one bundle stream
   per `(group, subtree)` it subscribes to.
3. **NACK / retransmission** ([BRC-126](brc-126-retransmission-protocol.md)) —
   **resolved: the retransmission unit is the bundle.** Loss is whole-datagram
   (UDP is atomic), so a lost bundle is one the listener never saw — it observes
   only a gap in the bundle SeqNum stream and cannot enumerate the members to
   request a subset. A per-tx index does **not** enable partial retry (you can't
   name txs in a frame you never received); its real uses are dedup / billing /
   receipts and faster local parsing — never retransmission. Once edge-decoalesced,
   retry reverts to per-tx BRC-124/126 on the last hop, so a bundle's NACK lifetime
   is bounded to the coalesced segment.
4. **EF payloads** ([BRC-128](brc-128-ef-frame-format.md)) — **resolved: one
   bundle format for both.** A member is length-prefixed raw tx bytes; an EF member
   self-identifies via BRC-128's 6-byte marker exactly as a standalone EF frame
   does — no per-member type flag, no separate bundle version. Mixed standard + EF
   members in one bundle are allowed (each self-describes). Edge-decoalesce re-emits
   each member as FrameVer `0x02` with the marker intact, so standard/EF
   correctness falls out for free.
5. **Interaction with fragmentation** ([BRC-130](brc-130-fragmentation.md)) — a
   bundle must stay ≤ MTU by construction, so a tx that would itself fragment is
   never a bundle member; the two extensions are mutually exclusive per datagram.
6. **AF_XDP synergy** — TX-side batching already wants large frames; coalescing
   and the AF_XDP TX path reinforce each other (fewer, fuller descriptors).
7. **Subtree semantics** — **resolved: a bundle is scoped to one `(group,
   SubtreeID)` flow.** SubtreeID is *not derivable* from a tx (external batch
   assignment), so it is carried once in the bundle header (32B, amortized) — never
   per member. Subtree-filtering subscribers then filter at **bundle granularity**
   (one SubtreeID in the header; accept/reject the whole bundle), preserving the
   per-`(group, subtree)` HashKey/SeqNum model unchanged. This is what makes #2
   work — the bundle has a single well-defined flow identity. Mempool txs with no
   subtree use `SubtreeID = 0`, i.e. a `(sender, group, 0)` flow, exactly as BRC-124
   with an unset subtree. Rejected alternative: per-member SubtreeID — a 32B/member
   tax that also breaks per-subtree sequencing.

## Next steps

- **Prototype in isolation from the afxdp proxy (retractable).** Build the bundle
  codec — coalescer (bucket BRC-124/128 frames by `(group, SubtreeID)`, pack) +
  decoalescer (unpack → individual FrameVer `0x02` frames) — as a **standalone
  package/repo** that reuses `shard-common` (frame/shard/seqhash) via `go.mod`,
  *not* wired into `shard-proxy` or `shard-proxy-afxdp`. Test it on its own:
  round-trip (incl. EF + mixed members), `(group, subtree)` bucketing, bundle-SeqNum
  gap → whole-bundle NACK recovery, and size/count/length bounds. Then measure pps
  reduction on a subtree-dense stream with edge-decoalesce.
- If the win justifies it, fold the codec into the proxy behind a `-mode`/flag, then
  write the BRC (assign a number, nail the wire layout and the open problems above)
  and align with the rating packet-floor so the wire-pps saving is real and visible.
