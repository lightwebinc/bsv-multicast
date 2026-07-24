# BRC-148 BEEF Object Plane — OSS Implementation Plan

> **Status:** Plan (2026-07-24). Implements
> [BRC-148](brc-148-shard-domain-beef-plane.md) (spec-only today) across the
> open-source components. A companion frame/lane-grammar spec is deliverable
> WP0 of this plan. Operator/commercial integration (delivery profiles,
> per-consumer election provisioning, billing) is tracked separately in the
> operator's private planning docs.

## 1. Scope and deliverables

Bring the BEEF object plane (domain `0x1`) up end-to-end on the OSS
components: submission at the proxy over a new tunnel-side TCP lane, carriage
as `FrameVer 0x09` multicast frames on the `0x1000`–`0x1FFF` group band,
NACK/fragmentation recovery via the retry endpoint, topic/version-filtered
delivery at the listener, and per-domain shard coordination via the BRC-139
Domains extension. Deliverables:

1. **WP0** — companion spec: BEEF object frame (`FrameVer 0x09`) + submission
   / delivery record grammar (`docs/brc-1xx-beef-object-frame.md`, number
   assigned by the upstream queue).
2. **WP1–WP6** — code across `shard-common`, `shard-proxy`, `shard-listener`,
   `retry-endpoint`, `shard-manifest`, `subtx-generator` (§4).
3. **Testing** — unit suites per repo, in-repo E2E additions, and
   `multicast-test` scenarios **92–98** (§7).
4. **Docs** — the full cross-repo documentation checklist (§9).

**Suitability verdict up front (§3): all four data-plane components host the
new functionality directly — no new OSS service is warranted.** BRC-148
deliberately reuses the BRC-124 92-byte header at identical offsets; the
frame, NACK, fragmentation, dedup, SSM-join, and manifest machinery all have
clean seams for a new frame class. This repeats the BRC-142/143/144 pattern
(codec in `shard-common`, lane/class in existing binaries).

## 2. Locked design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | No new OSS service; extend proxy/listener/retry/manifest | Header-offset reuse means every downstream mechanism (cache keying, reassembly, stamping) already operates on the right bytes; a separate daemon would duplicate the entire datapath |
| D2 | Ingress = new single-class TCP lane, port **8728** (`-beef-listen-port`, default 0=off), tunnel-bound | Follows the 8726/8727 push-lane pattern; port = lane = class; overlay ingress class gated like the miner lanes (tunnel membership + per-socket class) |
| D3 | Lane grammar = explicit **submission record** (length-carrying envelope), not a bare object stream | BEEF objects are not length-walkable without a full structural parse; the fabric never parses BEEF (spec §Payload). The record's explicit length restores the `objfmt.Reader` self-delimiting invariant |
| D4 | HashKey = `XXH64(senderIPv6 ∥ BE32(0x1000+shardIndex) ∥ zero32)` | Spec §Frame carriage: TopicID is **excluded** from the flow key (flows per (sender, group), state bounded by groups × sources). The zero 32-byte ingredient must be used identically at proxy stamp, listener own-traffic guard, and generator direct-mode self-stamp |
| D5 | Ingress dedup claims per **(ContentID, TopicID)** pair; egress dedup key = `SHA-256(ContentID ∥ TopicID)` | Multi-topic submissions emit one frame per topic sharing a ContentID; a bare-ContentID claim would wrongly suppress sibling-topic emissions and later re-submission to a new topic. Keying on the pair still satisfies the spec's MUST (key on object bytes, never subject TxID). Fold this clarification into WP0 |
| D6 | NACKs for BEEF flows carry a **zero SubtreeID** | A (sender, group) flow interleaves topics; the missing frame's TopicID is unknowable at gap time. Retry lookup is HashKey∥SeqNum only, so the field is informational |
| D7 | Reassembly keeps its existing key/verify (offset-8 field + SHA-256d) and gains an explicit `OrigFrameVer 0x09` completion case | ContentID = SHA-256d(payload) is exactly the BRC-130 verification hash; only the completion dispatch must preserve `0x09` (today it would re-encode as V2 and route down the tx path) |
| D8 | Per-domain manifest = BRC-139 `Flags` bit 7 `DomainsValid` + trailing Domains section; per-domain quorum/hysteresis in the evaluator | Bit 7 is the last free flag bit (verified); descriptor `Version` field is the forward extension point per the spec |
| D9 | OSS v1 caps `beef-shard-bits` at **12** (SlotSpan = 1, band `0x1000`–`0x1FFF`) | Wide planes (≤15) stay spec-supported in the shard helpers (validated against the `0xF800` control bound) but are not a runtime flag until needed |
| D10 | BRC-142 coalescing excluded for BEEF | Bundles pack small same-(group,subtree) txs; BEEF objects are large, per-topic, ContentID-identified. Enforce ClassTx-only at the coalesce hook |

### D3 — record grammars (summarised; WP0 is authoritative)

**Submission record** (up, on 8728; one record per submission, proxy expands
to one frame per topic):

```text
u8   recordVer   (0x01)
u8   topicCount  (1..15)
topicCount × { u8 nameLen (1..64) ∥ UTF-8 topic name }
u32  objectLen   (BE; 1 .. -beef-max-object-bytes)
objectLen bytes  BEEF object, leading marker ∈ {0100BEEF, 0200BEEF, 01010101}
```

**Delivery record** (down, for stripped push delivery; the edge knows the
TopicID hash, not the name): `TopicID[32] ∥ u32 BE objectLen ∥ object`. The
OSS receiver path forwards whole `0x09` frames (envelope-preserving), so the
delivery record is exercised by the commercial delivery lane; it is specified
in WP0 so both sides share one grammar.

The proxy computes `TopicID = SHA-256(topic)` per topic and
`ContentID = SHA-256d(object)` once, validates the leading marker (cheap
fixed-offset check; counted reject otherwise), builds unstamped `0x09` frames,
then stamps/derives/fragments/emits on the normal path. Framed `0x09` input
(relay) is handled by the existing framed TCP/UDP paths via the new
`DispatchClass` case under a class gate (below), not by the 8728 lane.

## 3. Component suitability evaluation

| Component | Verdict | Load-bearing seams (verified) | Caveats to fix in-plan |
|-----------|---------|-------------------------------|------------------------|
| shard-proxy | **Suitable** | `worker.NewObjectIngress` lane template (8726/8727); `forwarder.DispatchClass` single routing switch on `raw[6]`; `stampInPlace`/`nextSeq` already operate on offsets 40/48 with caller-supplied group+32B ingredient; fragmentation takes an `OrigFrameVer`; per-class `IngressClass` gate + metrics class enum | Group-address caches capped at `0x1000` (`maxGroupCache`, `addrFor`, egress `cacheable`) — indices ≥ `0x1000` silently bypass caching (per-packet allocs); widen to `0x2000`. `objingress` hardcodes `MulticastBytes`→`DispatchClass` 1:1 — BEEF needs a per-class dispatch hook (1 submission → N frames) |
| shard-listener | **Suitable** | `processFrame` single demux point; `fanout` decode-once + `byShard` reverse index keys `uint32` group idx (band values fine); `filter`/consumer election extensible; `nack.Tracker` is (HashKey, SeqNum)-only; `AddGroup`/`RemoveGroup` runtime joins; `engine.Addr` writes full 16-bit idx | `frame.Decode` returns `ErrBadVer` for 0x09 (add sniffer branch before it); reassembly completion re-encodes unknown `OrigFrameVer` as **V2** (add 0x09 case + callback); own-traffic guard is `FrameVerV2`-gated (extend, zero32 ingredient); egress dedup key must be the D5 synthetic, not bare ContentID |
| retry-endpoint | **Suitable** | Cache key = HashKey∥SeqNum at fixed offsets — already correct for 0x09; retransmit target **re-derived from the cached frame per FrameVer** (`retransmit.Retransmit` switch) — exactly the right seam for a TopicID@56 + plane-base case; NACK server retransmits verbatim | 0x09 currently dropped at ingress (`frame.Decode` fallthrough) — add sniffer + `processBEEFFrame`; server's group rate-limiter derives group from offset-8 (ContentID) — add a 0x09 branch (TopicID@56, banded); needs `-beef-enabled` join of the band + `-beef-shard-bits` + `-cache-ttl-beef` |
| shard-manifest | **Suitable** | Sender assembles payloads in order (groups→sources→successor) in `shard-common/frame/shard_manifest.go`; successor-config pattern is the template for per-domain config | Encode/decode leave the offset un-advanced after the successor block — must be restored before appending a Domains section (verified gotcha) |
| shard-common | **Suitable (codec home)** | `BlockFrame.ContentID` already reinterprets offset 8 — direct precedent; `shard` has no band special-casing (zone is comment-only); `netjoin` joins any group; `seqhash` takes caller ingredients | `Engine` holds a single `shardBits` — per-plane operation = a second Engine (chosen) or plane-parameterised methods; `objfmt.Reader` requires self-delimiting bodies (addressed by D3) |
| subtx-generator | **Suitable** | `send-subtree-push`/`send-block-push` dial/reconnect/self-verify skeleton; `-mode=direct-multicast` self-stamps with proxy-identical seqhash inputs | None material |

## 4. Implementation plan (dependency order)

### WP1 — shard-common (tag first; `update-shard-common.sh` fans out)

- `frame`: `FrameVerV9 = 0x09`; `beef.go` with `BEEFFrame{ContentID, TopicID,
  HashKey, SeqNum, Payload}`, `DecodeBEEF`/`EncodeBEEF`/`IsBEEFFrame`
  (mirror `block.go`; offsets identical to BRC-124); `Decode` gets a
  `case FrameVerV9` steering to the dedicated decoder (as V3–V8 do).
- `shard`: plane constants (`DomainTx=0x0`, `DomainBEEF=0x1`,
  `PlaneBase(domain) = domain<<12`); `PlaneIndex(key [32]byte, shardBits,
  domain) uint16` = BRC-129 top-bits + base, validated
  `base + 2^bits ≤ 0xF800` and bits ≤ 15; band helpers
  (`PlaneGroups(domain, bits)`, `InPlane(idx, domain)`). Runtime model:
  **a second `shard.Engine` per plane** (own shardBits), no change to the
  existing engine.
- `beef` helpers (in `objfmt` or a sibling): marker table
  (`0100BEEF`/`0200BEEF`/`01010101` at payload[0:4]), `VersionWord(payload)`,
  `TopicID(name)`, `ContentID(obj)`. **No structural BEEF parse anywhere.**
- `objfmt`: `ClassBEEF` + `beefrecord.go` — `BEEFRecordSize` walks the D3
  submission record (cheap: explicit length), register in `Size`/`Reader`/
  `String`; `StripBytes` for 0x09 (frame → bare object); `MulticastBytes` is
  **not** defined for ClassBEEF (1:N expansion lives in the forwarder).
- `manifest` + `frame/shard_manifest.go`: `ShardManifestFlagDomainsValid`
  (bit 7); Domains section encode/decode after the successor block (fixing the
  un-advanced-offset gotcha), `DomainDescriptor{DomainID, ShardBits, SlotSpan,
  DomainFlags, Version, GenerationID, Successor}`, validation per spec
  (count ≥ 1 when flagged, IDs unique ∈ 0x00–0x0E, slot ranges disjoint and
  below 0xF800, domain-0 agreement with top level, successor ±1);
  `Entry.Domains`; evaluator: per-DomainID candidate tallies,
  `Adopted.Domains map[uint8]DomainAdoption` (shard bits, source mode,
  generation, successor), per-domain divergence fields, unknown DomainIDs
  ignored not rejected. Domain 0x0 stays governed by the top-level fields.
- `seqhash`: unchanged (callers pass zero32 + banded idx per D4).

### WP2 — shard-proxy

- Config: `-beef-listen-port`/`BEEF_LISTEN_PORT` (0=off),
  `-beef-shard-bits`/`BEEF_SHARD_BITS` (default 4, valid 1–12; production
  widths are an operator/fleet decision), `-beef-max-object-bytes` (default
  1 MiB); add 8728 to the TCP-port collision check.
- `forwarder`: `IngressClassBEEF` (lane class) + metrics enum value `"beef"`;
  `DispatchClass` `case frame.FrameVerV9` → `ProcessBEEF` (gated: privileged
  or beef class); `ProcessBEEF` = decode → claim `(ContentID, TopicID)`
  (`bsp:beef:` prefix) → derive `0x1000 + PlaneIndex(TopicID)` via the BEEF
  engine → stamp with zero32 ingredient (no re-stamp when relaying, mirroring
  `Process`) → fragment (`OrigFrameVer 0x09`) → emit. `SubmitBEEF(record)`
  expands one submission into per-topic frames (marker/size/topic validation,
  counted rejects) and feeds `ProcessBEEF`.
- `worker`: BEEF lane = `ObjectIngress` with `ClassBEEF` + a per-class
  dispatch hook (record → `SubmitBEEF` instead of `MulticastBytes`); framed
  TCP path adds `FrameVerV9` to the 92-byte header cases.
- Egress: widen `maxGroupCache`/`addrFor`/`cacheable` to `0x2000`.
- Coalesce hook: assert/ignore non-ClassTx (D10).
- Manifest applier: adopt per-domain shard bits for the BEEF engine
  (restart-on-adopt path, manual pins win), from `Adopted.Domains[0x1]`.

### WP3 — shard-listener

- Config: `-beef-topics` (comma topic names and/or 64-hex TopicIDs),
  `-beef-groups` (explicit plane-relative indices — aggregator mode),
  `-beef-shard-bits`, `-beef-versions` (`beef,beefv2,atomic`; absent = all).
- `buildGroups`: append band groups — union of topic-derived indices and
  explicit `-beef-groups` — via the BEEF engine (receiver/collapsed modes
  only, like other joins); SSM sources inherit the global roster (spec
  §Source Discovery).
- `processFrame`: `IsBEEFFrame` branch → `processBeefFrame`: decode →
  (optional `-beef-verify-content` debug hash check) → topic filter →
  version filter → egress-dedup claim (D5 key) → fan-out → `Tracker.Observe`
  (banded idx, zero SubtreeID, ContentID as the tx-id argument);
  `flowLabel` maps the band to `"brc148"`.
- `fanout`: `Consumer` gains `TopicSet map[[32]byte]struct{}` +
  `BEEFVersions map[uint32]struct{}` (empty = admit-all per spec);
  `SendBeef` path routed by the existing `byShard` reverse index on the
  banded group idx; own-traffic guard extended to 0x09 with the zero32
  ingredient.
- `egress`: `EgressSink.SendBeef(raw, *frame.BEEFFrame)` following the
  per-class method precedent (`SendBlock`/`SendSubtreeData`/`SendBundle`);
  `Sender`/`MultiSender` forward the whole frame verbatim; strip mode emits
  the bare object (one object per datagram).
- `reassembly`: explicit `case 0x09` completion → `SetBeefCallback` /
  `DeliverReassembledBeef` preserving FrameVer + TopicID (verification
  already correct). Early fragment-drop by TopicID (spec MAY) is deferred.
- Delivery mode: BEEF frames traverse `RunUnicastIngest`→`processFrame`
  unchanged (envelope-preserving).

### WP4 — retry-endpoint

- Config: `-beef-enabled` (default off), `-beef-shard-bits`,
  `-cache-ttl-beef` (default 60s — live-tail resend window).
- Ingress: join the plane band when enabled (SSM per the global roster);
  `IsBEEFFrame` → `processBEEFFrame` (cache under HashKey∥SeqNum, BEEF TTL).
- `retransmit.Retransmit`: `case FrameVerV9` → group =
  `PlaneBase(0x1) + PlaneIndex(TopicID@56, beefShardBits)` via a BEEF engine.
- Server group rate-limiter: 0x09 branch keys the group tier on TopicID@56
  (banded), not offset-8.
- Fragments: no change (BRC-130 handling is version-blind; fragment bytes
  0–91 carry ContentID/TopicID per spec).

### WP5 — shard-manifest

- Config: repeatable `-domain` flag,
  `id:bits=N[:ssm][:active][:slotspan=S][:generation=HEX]`, mirroring the
  successor pattern; validation per WP1 rules.
- Sender: set `DomainsValid`, append descriptors (+ optional per-domain
  successor for live re-shard); RoleHint values 6/7 supported.

### WP6 — subtx-generator

- `cmd/send-beef-push`: dial/reconnect skeleton from `send-subtree-push`;
  flags `-addr [::1]:8728`, `-topics`, `-encoding beef|beefv2|atomic`,
  `-object-bytes` (payload size), `-count/-interval/-duration`, `-seed`,
  `-log-hashes`; body self-verified via `objfmt.BEEFRecordSize` before every
  write (malformed-emitter guard). Embed the BRC-62 spec example vector for a
  real-object mode (verbatim-carriage proof); synthetic objects otherwise
  (marker + deterministic bytes — legitimate, the fabric reads only the
  marker).
- `subtx-gen -mode=direct-multicast`: optional `-beef` emission that
  self-stamps with D4 inputs (proxy-bypassed listener tests).

## 5. Configuration surface (summary)

| Flag | Component(s) | Default | Notes |
|------|--------------|---------|-------|
| `-beef-listen-port` / `BEEF_LISTEN_PORT` | proxy | 0 (off) | Standard port **8728**, tunnel-side |
| `-beef-shard-bits` / `BEEF_SHARD_BITS` | proxy, listener, retry, generator | 4 | Valid 1–12 (v1); per-domain manifest can govern it |
| `-beef-max-object-bytes` | proxy | 1 MiB | Envelope bound; spec MUST bound accepted size |
| `-beef-topics` | listener | — | Names and/or hex TopicIDs; derives joins + topic filter |
| `-beef-groups` | listener | — | Plane-relative indices; aggregator (no topic filter) |
| `-beef-versions` | listener | all | Encoding capability gate |
| `-beef-verify-content` | listener | off | Debug ContentID check (test support; analog of payload-hash verify) |
| `-beef-enabled` | retry | off | Joins the plane band |
| `-cache-ttl-beef` | retry | 60s | Fifth semantic TTL |
| `-domain` (repeatable) | manifest | — | Per-domain descriptor announce |

## 6. Metrics

New label **values** on existing instruments wherever possible: proxy
`bsp_ingress_class_*{class="beef"}`, `bsp_ingress_deduped_total
{version="brc148"}`; listener `bsl_frames_received/forwarded_total
{version="brc148"}`, `bsl_frames_dropped_total{reason="topic_filter"|
"beef_version_filter"}`, gap/NACK counters via the `"brc148"` flow label.
New instruments (each with a concrete function): proxy
`bsp_beef_submissions_total{result}` (admission accounting — drives e2e
assertions and ingress-abuse posture); manifest `bsm_domain_shard_bits
{domain}` (cross-plane divergence detection, mirror of `bsm_shard_bits`).

## 7. Testing design

### 7.1 Unit (per repo)

- **shard-common**: `frame` — 0x09 round-trip + `IsBEEFFrame` + header-size
  constant guards (house round-trip pattern); `shard` — plane-derivation
  vectors (spec formula, boundary `base+2^bits ≤ 0xF800`, bits ≤ 15
  accepted / 16 rejected, domain 0xF forbidden, tx-plane reduction identity
  at base 0); `objfmt` — record size walker (valid, truncated, zero topics,
  oversize, bad marker), `Reader` split of back-to-back records, strip;
  `seqhash` — zero32-ingredient vector; `manifest` — Domains encode/decode
  round-trip incl. successor-present descriptors and the offset-restore fix,
  validation table (dup IDs, 0x0F, overlap, count 0, domain-0 disagreement),
  evaluator per-domain quorum/hysteresis/divergence/±1-successor, and
  **BRC-139-only back-compat** (DomainsValid=0 unchanged; unknown domain
  ignored).
- **shard-proxy**: dispatch-gate table gains 0x09 rows (beef class admits,
  transaction class rejects, privileged admits); `ProcessBEEF` stamp
  assertions (HashKey zero32 input, banded group, SeqNum per (sender,group)
  across two topics in one group — one flow, no per-topic counters);
  submission expansion (N topics → N frames, shared ContentID, per-pair
  dedup); fragment path OrigFrameVer 0x09; config validation (port
  collision, bits bounds); widened addr-cache bounds.
- **shard-listener**: `processBeefFrame` path (filters → dedup → fan-out →
  observe ordering); fanout topic/version election matrix incl. absent-filter
  admit-all and aggregator; own-traffic 0x09 exclusion; reassembly
  `TestReassembly_V9BeefCallback` (template: V4/V5 cases); egress dedup
  sibling-topic non-suppression (D5).
- **retry-endpoint**: ingress FrameVer table gains a 0x09 vector (cache under
  HashKey∥SeqNum + BEEF TTL); retransmit-target derivation for 0x09
  (TopicID@56 + base, wrong-field regression against offset-8); rate-limiter
  group tier on the banded idx.
- **shard-manifest**: `-domain` parse/validation table; sender flag+payload
  assembly round-trip.
- **subtx-generator**: record self-verify; embedded BRC-62 vector integrity.

### 7.2 In-repo E2E

`shard-listener make test-e2e` additions (unicast-injection pattern): BEEF
delivery, topic filter, version filter, 0x09 fragmentation → reassembled
BEEF callback. `shard-proxy make test-e2e`: submission over 8728 →
stamped 0x09 multicast observed (loopback), reject counters for bad
marker/oversize.

### 7.3 multicast-test scenarios (92–98)

Prerequisite: wire the push emitters into the harness (`build.DefaultSpecs`
+ `specialBinaries` currently reference only the legacy framed senders —
`send-beef-push` is added, and `send-subtree-push`/`send-block-push` should
be wired in the same pass, closing the existing gap where no harness scenario
exercises 8726/8727).

| # | Scenario | Proves | Pattern template |
|---|----------|--------|------------------|
| 92 | Submit → deliver | 8728 record → per-listener `received{version="brc148"} ≈ N`, `forwarded+egrErr ≈ received`; proxy class/admission counters | 32 (TCP object lane) |
| 93 | Multi-topic + topic filter | Topics chosen to **co-reside in one group** (helper brute-forces a colliding pair at the test width): electing listeners receive only their topic (`topic_filter` drops > 0), aggregator receives both; sibling frames share ContentID and are both delivered (D5 regression) | 32 + filter-env pattern |
| 94 | Version filter + verbatim carriage | One object per encoding (incl. the real BRC-62 vector); `-beef-versions=beef` admits 1, drops 2 (`beef_version_filter`); `-beef-verify-content` proves byte-identical delivery | 06 (payload-hash verify) |
| 95 | Fragmentation | Object ≫ MTU → BRC-130 frags → reassembled BEEF delivery; filters evaluate post-reassembly; ContentID verify on completion | 22–26 |
| 96 | NACK recovery | netem loss on listeners; gaps → NACK → retry (BEEF-enabled) ACK+retransmit; `recovered > 0`; loss removed before drain | 91/99 |
| 97 | Per-domain manifest | Loopback pipeline (70/72 template): DomainsValid quorum adoption of domain-1 bits; per-domain divergence gate; domain-0 top-level agreement; validation rejects; BRC-139-only consumer unaffected | 70/72 |
| 98 | Plane independence | Concurrent tx-plane + BEEF traffic: tx delivery matches its solo baseline (ratio-based), BEEF delivers, zero unrecovered gaps on either plane, distinct flow labels (no cross-plane HashKey collision) | 90 + ratio pattern |

Add `make test-beef` (`Scenario9[2-8]`), SCENARIOS.md rows (92–98 under a
new "BRC-148 BEEF plane" range heading), README highlight. Numbering per
harness registry: 92–98 are free; 80s stay reserved.

## 8. Out of scope (v1)

- **Declared-spread for hot topics** — reserved by the spec (`Version > 0`
  descriptor revision).
- **Per-domain disjoint SSM source sets** — spec reserves; all planes publish
  from the announced global source set.
- **History bootstrap** — live tail only; BRC-88/76/136 own history (spec
  §Operational Considerations).
- **Cross-plane translation** — forbidden by the spec; enforce by
  construction (no bridge code path).
- **Wide planes at runtime** (`shard_bits > 12`, SlotSpan > 1) — helpers
  validate them; flags cap at 12.
- **BRC-142 coalescing of BEEF** (D10) and **early fragment drop by TopicID**
  (spec MAY) — deferred.

## 9. Documentation and release checklist

Per the cross-repo checklist in the project conventions:

- Per-binary repo: README quick-start + feature line; `docs/configuration.md`
  entries for every §5 flag (defaults, validation, fail-closed notes);
  `docs/architecture.md` section naming every join/emit/branch site; godoc on
  new setters carrying the invariants (zero32 HashKey ingredient; D5 keys).
- Helm charts: `values.yaml` keys + schema enums (`beefVersions`), README
  values reference; infra repos: firewall templates open the BEEF lane port
  variable tunnel-side and the band's group range where enumerated;
  `docs/networking.md` band prerequisites (spec §Operational: permit
  `0x1000`–`0x1FFF` join/forward at the configured scope and source mode).
- `shard-common` README packages table + protocol docs; `multicast-test`
  README + SCENARIOS.md; this repo: WP0 companion spec, BRC-148
  Implementations section, BRC-129 draft cross-note (superseded free-space
  section), README index links.
- Skills: architecture.md BRC index + Active Proposals flip on ship;
  protocol.md FrameVer table row (0x09), HashKey-ingredient table row
  (zero32 + banded idx), retransmission-routing row, push-lane table row
  (8728), port reference row.

## 10. Sequencing and gates

1. **WP0 spec first** — the frame/record grammar freezes before code.
2. **WP1 `shard-common`** — tag next minor (from v0.16.0 → v0.17.0),
   `update-shard-common.sh` fan-out; all codecs + manifest extension land
   with unit suites green before any consumer work.
3. **WP2 proxy → WP3 listener → WP4 retry** (each: unit + in-repo E2E green,
   `-race`, per-repo Dagger CI with `GOWORK=off`).
4. **WP5 manifest + WP6 generator**, then **scenarios 92–98** green
   (`make test-beef`) as the integration gate.
5. Docs checklist (§9) lands with the code, not after; grep-audit per the
   conventions verification step.
6. Defaults keep every lane/plane **off** — zero behavior change for
   existing deployments until a flag opts in (strictly additive, matching
   the spec's compatibility posture).
