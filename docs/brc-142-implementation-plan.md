# BRC-142 Implementation Plan — Coalescing (Bundle) Frame

Plan to take BRC-142 from the `coalesce-spike` prototype to shipped open +
commercial code. Spec: [brc-142-coalescing-frame.md](brc-142-coalescing-frame.md).
Rationale + simulation results: [coalescing-frame-format-DRAFT.md](coalescing-frame-format-DRAFT.md).

## Principles (carried from the spike validation)

- **Ship dark, opt-in per flow.** FrameVer `0x08` is new; a node that doesn't
  understand it would drop it. Coalescing MUST be enabled only when the receiving
  side decodes bundles — gate on a capability signal (config now; BRC-139 manifest
  flag later), default **off**.
- **Edge-decoalesce first (lowest risk).** The fabric carries bundles; the edge
  listener splits them before per-consumer egress, so the consumer contract stays
  BRC-124. Consumer-decoalesce is a later opt-in.
- **Re-bucketing is mandatory for finer subscribers** (BRC-142 §11) — a relay MUST
  NOT raw-deliver a parent-group bundle to a subscriber finer than the bundle's
  `ShardBits`. This is a correctness/economics rule, not an optimization.
- **Baseline MTU is 1500** (R≈5–8); jumbo is a per-segment fabric upside.
- **Open / commercial seam unchanged:** OSS `shard-proxy`/`shard-listener` get the
  baseline (collapsed coalescing, edge-decoalesce, kernel sockets); the commercial
  `shard-proxy-afxdp` adds the `-mode` split + AF_XDP TX packing, and
  `shard-listener-1bsv` adds consumer-decoalesce + per-member metering — both reuse
  the OSS packages via `go.mod` (no fork).

## Reuse from the spike

`coalesce-spike` already contains validated logic to port (not copy blindly —
productionize): `bundle` codec (Encode/Decode), `coalesce.Coalescer` (bucket +
windowless pack), `coalesce.Decoalesce`, `coalesce.Rebucketer`. The spike's
`Coalescer` packs a static batch; production needs the **time-driven flush** (size
OR max-delay) modelled in `sim/window.go`.

---

## Phase 0 — `shard-common`: the bundle codec (foundation)

**Goal:** one canonical, allocation-conscious codec every repo imports.

- Add `FrameVerBundle = 0x08` to `shard-common/frame` (keep all FrameVer constants
  together); add `IsBundle(buf)`.
- New package `shard-common/bundle`: `Bundle`/`Member` types, `Encode`/`Decode`
  (port from the spike), constants (§17 of the spec). Zero-copy `Decode` (members
  alias the buffer); an `Encode` variant that writes into a caller buffer (no
  alloc) for the hot path.
- Reuse `shard/Engine.GroupIndex` and `seqhash.Hash` for flow identity.
- Tests: round-trip (carried/recomputed TxID), EF + mixed members, truncation,
  bounds, `TxCount`/`PayloadLen` cross-check. Fuzz `Decode`.
- **Deliverable:** tagged `shard-common` release; run `update-shard-common.sh` to
  bump dependents. *Tag before any dependent builds in CI.*

## Phase 1 — `shard-proxy`: coalescing encoder (opt-in, default off)

**Goal:** the proxy can coalesce its egress per `(group, subtree)`.

- New `forwarder` coalescing stage after decode/stamp: bucket eligible frames by
  `(groupIdx, subtreeID)`; flush a bundle on MTU/`TxCount` cap **or** max-delay
  (port `sim/window.go`'s timer). Per-bucket monotonic bundle `SeqNum`.
- Eligibility: a tx whose encoded member size exceeds `MTU − 66 − overhead` is
  **excluded** and follows the existing BRC-130 path. Never pack an oversized tx.
- Config: `-coalesce` (off), `-coalesce-max-delay` (e.g. 250µs–1ms),
  `-coalesce-carry-txid` (off), `-coalesce-mtu` (defaults to egress MTU), and a
  per-flow/per-group opt-in selector.
- Metrics: `bsp_coalesce_*` (§15 of the spec).
- Tests: unit (bucketing, flush triggers, eligibility); ensure non-coalesced path
  is byte-identical when disabled.
- **Risk gate:** keep default off; coalescing only safe once Phase 2 listeners
  decode bundles.

## Phase 2 — `shard-listener`: edge-decoalesce (default contract preserved)

**Goal:** listeners transparently unpack bundles; consumers see BRC-124 frames.

- New `decoalesce` step ahead of `filter` → own-exclusion → `fanout`: split a
  FrameVer `0x08` datagram into per-member `FrameVer 0x02` frames; carry/recompute
  `TxID`; inherit `SubtreeID`; **re-stamp per-tx egress `SeqNum`** on the egress
  flow (bundle SeqNum is frame-bound).
- Per-member dedup + own-traffic exclusion (BRC-142 §13): drop already-claimed
  members, not the whole bundle.
- Gap tracking: track gaps on the **bundle** SeqNum stream (one per
  `(group, subtree)`); NACK whole bundles.
- Metrics: `bsl_decoalesce_*`.
- Tests: real round-trip (coalesced in → exact individual frames out, 0 dup/cross,
  EF preserved); gap on bundle stream → recovery.

## Phase 3 — `retry-endpoint`: bundle as an opaque cached frame

**Goal:** loss recovery for bundles with minimal change.

- Cache a FrameVer `0x08` datagram by `(HashKey ∥ SeqNum)`; retransmit to the
  bundle's group on NACK exactly as a BRC-124 frame. The endpoint treats the
  bundle as opaque — no member parsing.
- Verify per-FrameVer cache TTL applies; confirm the larger frame fits cache
  sizing.
- Tests: drop a bundle → listener NACK → retransmit → decoalesce recovers all
  members (the A1 "full subscriber" path).

## Phase 4 — Re-bucketing relay (`shard-listener` relay role)

**Goal:** cross-domain / re-shard correctness; enforce the finer-subscriber rule.

- `Rebucketer` (port from spike): decoalesce + re-coalesce at a target `ShardBits`;
  preserve `SubtreeID`; re-stamp `HashKey`/`SeqNum` per new child flow.
- Source the target `ShardBits` from the BRC-139 manifest (consumer already wired);
  honor the `Successor` block `TransitionEpoch` for cutover (handle both
  generations during the window).
- **Enforce §11:** never raw-deliver a parent bundle to a finer subscriber —
  re-bucket first. Metric `bsl_rebucket_bundles_total`.
- Tests: N→N+1 split routes members to correct child groups, conserved, re-stamped
  (the spike's `rebucket_test.go` becomes the basis).

## Phase 5 — E2E + scenarios (`multicast-test`)

**Goal:** validate the lab-only assumptions the sims couldn't.

- Real-binary E2E: proxy coalesce → fabric → listener decoalesce → exact delivery,
  0 dup/cross; EF + mixed; measure achieved R and **end-to-end latency** (the real
  dwell + RTT, vs the sim's dwell-only).
- Loss/recovery scenario: induced loss → bundle NACK → recovery; measure delivery
  vs uncoalesced.
- Re-shard scenario: live `ShardBits` shift with in-flight bundles → re-bucketer.
- **Density on representative traffic:** drive `subtx-generator` output (and any
  captured traffic) through the proxy to measure achieved R on real shape, not the
  uniform sim (the single highest-value open assumption).

## Phase 6 — Commercial (`shard-proxy-afxdp`, `shard-listener-1bsv`)

**Goal:** the performance + product layer.

- `shard-proxy-afxdp`: coalescing inside the `-mode` split (collapsed/ingress/spine
  — coalesce on the spine/fabric egress where pps cost is highest); **AF_XDP TX-side
  packing** composes with coalescing (fewer, fuller descriptors). Use the
  allocation-free `Encode`-into-buffer path; benchmark the inline frames/s ceiling
  (the spike's A5).
- `shard-listener-1bsv`: **consumer-decoalesce** option (bundles end-to-end to
  capable consumers) + **per-member metering** — count per-tx for receipts/billing,
  bill on wire-pps so the coalescing saving flows to the bill. Advertise
  bundle-capability so the upstream only coalesces toward capable consumers.

### P6 status — SHIPPED (origin-coalescing, not spine-coalescing)

A design correction surfaced during implementation and is now load-bearing:
coalescing happens at the **origin** (collapsed/ingress proxy), **not** at the
spine. The forwarder's coalescing divert only fires when `src != nil`; a spine
re-emits with `src = nil`, and re-stamping there would break the per-source
HashKey that own-traffic exclusion depends on. So:

- **`shard-proxy` (OSS):** new `Forwarder.ProcessBundle` + a `FrameVer 0x08` case
  in `DispatchClass` — a relay (the spine) re-emits an ingress-coalesced bundle
  **verbatim** to its group (group read from the bundle header, no re-stamp,
  members opaque). Cheap magic + payload-length validation guards a corrupt
  header (`bundle_malformed` drop counter).
- **`shard-proxy-afxdp`:** `-coalesce` / `-coalesce-max-bytes` (1500) /
  `-coalesce-max-members` / `-coalesce-carry-txid`, default off. `SetCoalesce` +
  `FlushCoalesced` wired on the **origin** drains only (collapsed `-xdp-mode
  kernel`, ingress). **`-mode collapsed -xdp-mode native|skb` + `-coalesce` is a
  fatal startup error**: the in-place AF_XDP TX (`TxEgress.Send`) emits straight
  from UMEM and silently drops a heap bundle datagram. The spine relays bundles
  verbatim (one bundle → one `SpineTx.SendCopy` descriptor = the "TX-side
  packing") and surfaces oversize/saturated TX drops on `/metrics`. A bundle must
  fit `frame-size − 62`; size the spine `-frame-size` ≥ any feeding proxy's
  `-coalesce-max-bytes + 62`.
- **`shard-listener` (OSS):** optional `egress.BundleSink` seam +
  `fanout.Sink.SendBundle` — bundle-capable consumers get the whole bundle, the
  rest get decoalesced+re-stamped frames (default path unchanged). `Consumer`
  gains `BundleCapable`.
- **`shard-listener-1bsv`:** `policy.ConsumerSpec.BundleCapable` (broker-pushed),
  the wrapper chain forwards `SendBundle`, and the meter gains `Txs`/`TxsTotal`
  (+ `consumer_egress_txs_total`): a whole bundle meters **1 wire packet, N txs**
  — bill on packets, receipt on txs.

Deps: `shard-common v0.14.0`, `shard-listener v1.6.5`, `shard-proxy` at the
bundle-relay commit. Build/test green incl. `GOWORK=off` CI mode.

---

## Cross-cutting

- **Capability gating.** Near-term: config flags + operator knowledge. Next:
  a BRC-139 manifest flag advertising bundle-decode capability so coalescing
  auto-enables only toward capable receivers (prevents black-holing 0x08 at
  legacy nodes).
- **Firewall / classifiers.** FrameVer `0x08` puts TxID/HashKey/SeqNum at
  different offsets than BRC-124; any classifier reading those must branch on the
  version byte (integrated-infra nft/pf, AWS SGs unaffected — they match port/magic).
- **Config/metrics/docs.** Per-repo `docs/configuration.md` + `architecture.md`
  updates; flip the BRC-142 status in `multicast-skills/architecture.md` §BRC Index
  and the Active Proposals list when each phase ships (per the conventions
  cross-repo checklist).
- **Helm/IAC.** New proxy/listener flags surfaced in the helm charts + ansible
  group_vars; default off everywhere.

## Sequencing & dependencies

```
Phase 0 (shard-common)  ─┬─► Phase 1 (proxy coalesce)  ─┐
                         └─► Phase 2 (listener decoal.) ─┼─► Phase 5 (E2E) ─► Phase 6 (commercial)
                             Phase 3 (retry)  ───────────┤
                             Phase 4 (rebucket relay) ───┘
```

- 0 first (everything imports the codec; tag before dependents build).
- 1 and 2 can proceed in parallel but coalescing stays default-off until 2 lands.
- 3 small, do alongside 2.
- 4 after 2 (reuses decoalesce) and the BRC-139 consumer.
- 5 gates 6.

## Validation gates (carried from the pre-commit sims)

| Assumption | Validated where |
| --- | --- |
| Codec round-trip / EF / bounds | Phase 0 unit + fuzz |
| Edge-decoalesce preserves consumer contract | Phase 2 + 5 E2E |
| Bundle-unit recovery works under loss | Phase 3 + 5 loss scenario |
| Finer-subscriber re-bucket rule | Phase 4 + tests |
| Achieved R on **real** traffic shape | Phase 5 (generator/captured) |
| End-to-end latency (not just dwell) | Phase 5 |
| Inline frames/s at line rate | Phase 6 (AF_XDP bench) |

## Out of scope (this plan)

- Submitting BRC-142 to `bitcoin-sv/BRCs` (do after Phase 5 proves the wins).
- Jumbo fabric provisioning (separate infra workstream; BRC-142 is MTU-agnostic).
