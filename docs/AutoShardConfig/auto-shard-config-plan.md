# Proxy/Listener Automatic Shard Configuration Plan

- Status: **Implemented** (Phase 1 + Phase 2; live-resharding bridging mode is the proxy-side dual-emit path)
- Scope: `shard-proxy`, `shard-listener` (consumer-side implementation); small
  follow-up to `shard-manifest`.
- Default behavior unchanged: manual CLI configuration remains the default;
  auto-config is opt-in per component.
- Implementation landed across `shard-common`
  (`frame.SuccessorBlock` + `manifest` consumer package), `shard-manifest`
  (`-pilot-only` + `-successor-*` flags), `shard-listener` (beacon
  demux + manifest applier + runtime `AddGroup`/`RemoveGroup` on the
  worker), `shard-proxy` (dedicated beacon socket + restart-on-adopt +
  optional `forwarder.BridgingEngine` for dual-emit), and
  `multicast-test` (scenarios 70-71). 50+ unit tests plus loopback
  integration tests confirm the wire-level pipeline.
- Compatible with both ASM and SSM, and with all four
  [deployment postures](../SourceSpecificMulticast/ssm-support-plan.md#deployment-postures)
  (A: legacy ASM, B: data-plane SSM, C: SSM-everywhere intra-domain, D: SSM
  inter-domain).
- Out of scope: per-listener group assignment from the pilot, retry-endpoint
  discovery (stays on BRC-126 ADVERT), NACK parameter distribution.

## What this plan is, and isn't, anymore

The protocol surface for automatic configuration is already complete in
[BRC-137](../brc-137-shard-manifest.md): wire format, flag bits
(`Authoritative`, `SourceModeSSM`, `SourcesValid`, `PilotOnly`), sources
payload, and a normative "Consumer Behaviour — Auto-configuration" section that
fixes quorum, hysteresis, the ±1 `ShardBits` shift bound, manual-pin precedence,
and divergence telemetry. **The earlier draft of this plan proposed those
amendments; they have since landed in
[`brc-137-shard-manifest.md`](../brc-137-shard-manifest.md).** What remains is
implementation, fitted into the SSM plan's existing config surface — this
document is the implementation plan, not a protocol plan.

Two architectural decisions from the SSM plan also reshape what this plan needs
to say:

1. **Data-plane source identity flows exclusively through shard-manifest.** The
   SSM plan rejects per-publisher gossip and removes `discover=beacon` as a
   data-plane source-discovery mechanism. So the sidecar-manifest pattern
   (proxies/listeners emitting their own manifests with their `bindSource`) is
   **dropped** from this plan. `shard-manifest` is the sole authority; how it
   learns the publisher set is its internal concern.
2. **Posture C makes even the beacon group SSM.** When deployed under Posture C
   the consumer joining the beacon group to read manifests does so via
   `MCAST_JOIN_SOURCE_GROUP` against the union of `bootstrap.beacon`
   (retry-endpoint pods, for BRC-126 ADVERTs) and `bootstrap.manifest`
   (shard-manifest pods, for BRC-137 manifests), not via `IPV6_JOIN_GROUP`. The
   previous draft's blanket "beacon always ASM" carve-out is no longer correct.

The remaining additive change this plan brings is the consumer-side machinery —
a `manifest-consumer` subsystem in proxy and listener, the listener-only
`autoJoinFromManifest` toggle, and the operational restart-on-adopt semantics.

## Goals / non-goals

- **Goal.** Implement BRC-137 §"Consumer Behaviour — Auto-configuration" in
  proxy and listener, behind an opt-in switch.
- **Goal.** Let listeners optionally auto-join the shard indices a `PilotOnly=1`
  manifest announces, additive to whatever `-shard-include` already lists.
- **Goal.** Fit cleanly into the SSM plan's
  [Helm `multicast:` block](../SourceSpecificMulticast/ssm-support-plan.md#new-configuration-surface-additive-all-components),
  reusing the same `bootstrap.*` and `consume` machinery; do not invent a
  parallel surface.
- **Goal.** Work for all four deployment postures. ASM auto-config consumers do
  an ASM beacon join; SSM/Posture-C consumers do an SSM beacon join with
  combined source filter. Same opt-in flag, same registry, same adoption gates.
- **Non-goal.** Per-listener group assignment, consistent-hash, or explicit
  listener→groups maps from the pilot. Static `-shard-include` is always
  preserved.
- **Non-goal.** Retry-endpoint / NACK-parameter distribution. BRC-126 stays the
  source of truth.
- **Configurable.** Live `ShardBits` re-sharding without restart is supported
  as an opt-in (`autoConfig.liveResharding=true`); see
  [Live re-sharding](#live-re-sharding-optional). When disabled (the default),
  a `ShardBits` adoption flips `/readyz`, drains, then exits non-zero and the
  orchestrator rolls the pod.
- **Bounded protocol surface.** The bridging-mode design adds one optional
  payload block (`Successor`) and one flag bit to BRC-137; see
  [BRC-137 amendments](#brc-137-amendments). All other wire-level surface is
  already in BRC-137.

## Design decisions

1. **Single consumer subsystem, two purposes.** The proxy and listener each gain
   a `manifest-consumer` package (promoted into `shard-common/manifest/` for
   reuse). It maintains the registry, evaluates BRC-137's adoption gates, and
   exposes two surfaces: (a) an accessor for the current adopted view
   (`ShardBits`, `SourceModeSSM`) used by the addressing layer; (b) a source-set
   accessor used by `netjoin` (per SSM plan) when `SourceModeSSM=1`.
2. **No sidecar manifests from data-plane components.** Per the SSM plan, only
   `shard-manifest` emits manifests in steady state. Proxies and listeners are
   consumers only.
3. **Posture-aware beacon-group join.** The consumer's beacon socket is opened
   via the same `netjoin` helper as everywhere else (per SSM plan). The source
   filter depends on posture:
   - Posture A / Posture B (control plane is ASM): `IPV6_JOIN_GROUP`.
   - Posture C / Posture D: `MCAST_JOIN_SOURCE_GROUP` with the union of
     `bootstrap.beacon ∪ bootstrap.manifest` as the source list. The beacon
     group carries both retry-endpoint ADVERTs and shard-manifest manifests, so
     both source sets must be filterable.
4. **Manual pin always wins.** Per BRC-137 normative rule: when the operator
   pins a value via CLI/env, the local component uses the pinned value and only
   emits divergence telemetry if the pilot view differs.
5. **Additive listener auto-join.** When
   `multicast.autoConfig.autoJoinFromManifest=true`, the listener's effective
   subscription is `union(-shard-include, pilot_groups)`, where `pilot_groups`
   is the union of `Flags.GroupsValid` payloads from authoritative manifests
   (after quorum + hysteresis). Static `-shard-include` entries are **never**
   leaved. Groups added by pilot are leaved only when no pilot still claims
   them.
6. **Bootstrap behavior is configurable.** Mirrors the SSM plan's fail-closed
   pattern:
   - `manifest-bootstrap=optional` (default): start with CLI/env values, apply
     pilot adjustments later as quorum is met.
   - `manifest-bootstrap=required`: refuse to bind data-plane sockets until
     quorum is reached for `ShardBits` (and for `SourceModeSSM` when SSM is
     configured). `/readyz` returns 503 until then. `MCGroupID` is not gated —
     it is derived from the beacon socket's destination address, not from a
     payload field.
7. **`ShardBits` adoption has two modes; operator picks per component.**
   - **Restart mode (default, `autoConfig.liveResharding=false`).** The
     forwarder and listener join state are not rebuilt in place. Adoption
     flips `/readyz`, drains for `DrainTimeout`, and the process exits
     non-zero. The orchestrator restarts the pod, which reloads with the new
     adopted value already cached in the warm registry on second start.
     Simple, well-tested control flow; the only path that touches the hot
     send/receive loops is process re-init.
   - **Bridging mode (opt-in, `autoConfig.liveResharding=true`).** The
     consumer enters a transition window when it sees a quorum-confirmed
     BRC-137 `Successor` block with a future `TransitionEpoch`. During the
     window the proxy emits each frame to BOTH the current and successor
     shard layouts; listeners with `autoJoinFromManifest=true` join the
     union of current ∪ successor groups; downstream TxID dedup absorbs the
     duplicates. At `TransitionEpoch` the consumer atomically swaps the
     active generation to the successor and leaves the now-unused
     current-only groups. No restart; `/readyz` stays green throughout. See
     [Live re-sharding](#live-re-sharding-optional) for the full lifecycle.
8. **Adoption is independent of the source-set update path.** The source set
   (from `Flags.SourcesValid` payloads) is a deduplicated union, not
   quorum-gated (per BRC-137 §"Source set"). It feeds `netjoin` join/leave calls
   directly, rate-limited per the SSM plan's `netjoin` rules. The two update
   paths share the registry but evaluate independently.

## Configuration surface

The auto-config plan extends the SSM plan's
[`multicast:` Helm block](../SourceSpecificMulticast/ssm-support-plan.md#new-configuration-surface-additive-all-components)
with a single `autoConfig` subsection. No parallel CLI surface; the existing CLI
flags map 1:1 onto these YAML keys via each component's Helm chart.

```yaml
multicast:
  # ... existing fields from the SSM plan: sourceMode, scope,
  # bindSource (senders), sources.{consume,refresh,bootstrap,static}
  # (receivers) ...

  autoConfig:
    enabled: false # master switch; when false, manifests are ignored
    bootstrap: optional # optional | required
    pilotQuorum: 2 # distinct authoritative announcers required for adoption
    pilotHysteresis: 0s # 0 ⇒ 2 × AnnounceInterval (from the candidate manifest)
    beaconScope: "" # "" ⇒ inherit multicast.scope; comma list otherwise

    # Live re-sharding (opt-in; see Live re-sharding section).
    # When false (default), a ShardBits / SourceModeSSM change exits the
    # process and the orchestrator rolls the pod.
    # When true, the consumer enters a bridging window driven by the
    # pilot's Successor block and switches generations in place.
    liveResharding: false
    bridgingWindow: 0s # 0 ⇒ honour the pilot's TransitionEpoch verbatim;
                       # nonzero ⇒ local floor on the bridging duration
                       # (consumer waits MAX(pilot, this) before cutover)

    # Listener-only:
    autoJoinFromManifest: false # additive: union(-shard-include, pilot_groups)
```

Per-flag CLI mapping (proxy and listener):

| Helm key                          | CLI flag                       | Env var                       |
| --------------------------------- | ------------------------------ | ----------------------------- |
| `autoConfig.enabled`              | `-manifest-consumer-enabled`   | `MANIFEST_CONSUMER_ENABLED`   |
| `autoConfig.bootstrap`            | `-manifest-bootstrap`          | `MANIFEST_BOOTSTRAP`          |
| `autoConfig.pilotQuorum`          | `-pilot-quorum`                | `PILOT_QUORUM`                |
| `autoConfig.pilotHysteresis`      | `-pilot-hysteresis`            | `PILOT_HYSTERESIS`            |
| `autoConfig.beaconScope`          | `-manifest-beacon-scope`       | `MANIFEST_BEACON_SCOPE`       |
| `autoConfig.liveResharding`       | `-live-resharding`             | `LIVE_RESHARDING`             |
| `autoConfig.bridgingWindow`       | `-bridging-window`             | `BRIDGING_WINDOW`             |
| `autoConfig.autoJoinFromManifest` | `-shard-include-from-manifest` | `SHARD_INCLUDE_FROM_MANIFEST` |

Existing manual flags continue to work unchanged. When
`autoConfig.enabled=false` (the default), no manifest is read, no adoption
occurs, and behavior matches today exactly.

The auto-config plan introduces **no new data-plane source-discovery
mechanism.** For SSM-mode source learning the consumer feeds
`Flags.SourcesValid` unions into the same `netjoin` helper described in the SSM
plan; the operator opts in by setting `multicast.sources.consume: [manifest]`
(per the SSM plan), which is already the only valid value.

## Posture-aware behavior

The auto-config consumer composes the same `netjoin` helper, registry, and
adoption gates across all postures. What varies is the beacon-socket join
syscall, the data-plane addressing prefix, and whether `autoJoinFromManifest`
issues ASM or SSM joins. The MsgType demux (`buf[6]` → `0x20` ADVERT vs `0x40`
manifest), the registry, and the adoption rules are posture-independent.

**Postures A / B (control plane is ASM):**

- Beacon socket: `IPV6_JOIN_GROUP` on `FFx5::B:FFFD` (or `FFx8::B:FFFD`).
- Data group address: `Scopes[MCScope]::MCGroupID:idx`.
- Data-plane source list (Posture B only): from
  `multicast.sources.consume: [manifest]` → manifest's `SourcesValid` union →
  `netjoin`.
- `autoJoinFromManifest`: ASM join for each added shard index.

**Postures C / D (control plane is SSM):**

- Beacon socket: `MCAST_JOIN_SOURCE_GROUP` on the same beacon group, with source
  filter = `bootstrap.beacon ∪ bootstrap.manifest`. Issued through the SSM
  plan's `netjoin` helper. The union is required because the beacon group
  carries BRC-126 ADVERTs from retry-endpoint pods _and_ BRC-137 manifests from
  shard-manifest pods.
- Data group address: `FF35::MCGroupID:idx` (site) or `FF3E::MCGroupID:idx`
  (global) once `SourceModeSSM=1` is adopted. The addressing helper flips at
  adoption time.
- Data-plane source list: same as Posture B — manifest-fed, via `netjoin`.
- `autoJoinFromManifest`: SSM `(S,G)` join for each added shard index against
  the `SourcesValid` union, sharing the SSM plan's per-listener join-rate
  budget.

**Pilot disappearance (all postures).** When all pilots TTL-expire, retain the
last adopted values indefinitely. `multicast_manifest_pilots_known` drops to 0
and alerting fires; no automatic revert to CLI defaults (that would cause flap).

## Live re-sharding (optional)

Goal: change `ShardBits` (and, optionally, `SourceModeSSM`) across the fleet
without taking proxies or listeners out of service. Default remains restart;
this section describes the opt-in bridging path enabled by
`autoConfig.liveResharding=true` on the proxy and listener.

### Mental model

A re-shard is a generation transition. At any moment the fleet has one
**active** generation and at most one **successor** generation. A generation
is identified by `GenerationID` (already in BRC-137) and pinned to a
`(ShardBits, SourceModeSSM)` tuple. The pilot signals a transition by
including a `Successor` block in its manifest payload; the block names the
incoming `GenerationID`, the incoming `ShardBits` (which BRC-137's normative
±1 rule constrains to `current ± 1`), and a `TransitionEpoch` (Unix
seconds) at which the successor becomes the sole active generation.

Three phases:

1. **Steady.** No `Successor` block in any quorum-confirmed manifest.
   Components emit/join on the active generation only.
2. **Bridging.** Quorum of authoritative manifests carries a `Successor`
   block with `now < TransitionEpoch`. Components dual-emit (proxy) and
   union-join (listener) across active + successor. Frames duplicate
   wherever the two layouts overlap; TxID dedup absorbs the duplicates
   downstream of the listener.
3. **Cutover.** At `TransitionEpoch` (local clock), the consumer atomically
   swaps active ← successor and the `Successor` block is dropped. The
   prior active generation is left immediately; any tail-end frames still
   in flight are discarded by dedup.

### Why duplicates are tolerable

Under the address scheme `Scopes[MCScope]::MCGroupID:idx`, group `:k` is the
same multicast address regardless of `ShardBits`. Going from `ShardBits=N`
to `N+1` doubles the number of valid `k` values but does not invalidate any
existing `k`. So during bridging:

- For each TxID, the proxy emits to its `N`-bit group **and** its
  `(N+1)`-bit group. When the two indices collide (the low N bits match),
  the proxy emits twice to the same address — listeners get two frames with
  identical payload but different `HashKey` (because HashKey is derived
  from the destination group, which is the same here, **and** the
  per-flow sequence; under SB change the per-flow seq is what changes).
  The listener's per-TxID dedup absorbs the duplicate.
- For TxIDs whose two indices differ (`(N+1)`-bit-suffix = 1), the two
  emissions go to two different group addresses. Listeners joined to both
  receive both; dedup absorbs.
- After cutover, only the `N+1`-bit groups are emitted to; listeners can
  release any old-only groups (any group `:k` with `k ≥ 2^N`-bit-domain
  membership is still valid under the new layout, so in practice listeners
  joined to a contiguous range only **add** groups when going N→N+1, and
  **drop** half their joins when going N+1→N).

The same reasoning works in reverse for `N+1 → N` (merge): a frame's new
group is one of two old groups; the proxy emits to both old and new during
the window; listeners need to keep the old group joined until cutover.

The HashKey / per-flow `SeqNum` machinery is **per-flow** (per destination
group). The bridging-window dual emission introduces new flows for the
incoming generation; those flows start at seq 0 and ramp like any new
publisher. There is no in-flight gap-tracking impact for the existing
flows — they continue, then stop emitting at cutover. New-generation flows
simply did not exist before bridging began.

### Why dedup is required, and how the SSM plan helps

Bridging multiplies frame count by up to 2× during the window. The
listener's downstream emits each TxID at most once because the egress dedup
(`-egress-dedup-cap` / `egress-dedup-ttl`) collapses duplicates. **Live
re-sharding requires `EgressDedupCap > 0` and an egress-dedup TTL of at
least 2× the bridging window.** The plan validates this at startup; a
listener with dedup disabled refuses to enable `liveResharding`.

Cross-listener TxID dedup via Redis (existing `-egress-dedup-redis-addr`
plumbing) is helpful but not required; the per-listener LRU is sufficient
for correctness because the duplicate frames target the same listener.

### Proxy implementation

The forwarder's hot path today computes one address per frame:
`shard_index = topBits(txid, ShardBits)` →
`group = engine.Addr(shard_index, port, mode, scope)`. Under bridging mode
the forwarder computes two:

```text
active_idx     = topBits(txid, active.ShardBits)
successor_idx  = topBits(txid, successor.ShardBits)
active_group   = engine.Addr(active_idx, port, active.mode, scope)
successor_group= engine.Addr(successor_idx, port, successor.mode, scope)
```

The proxy then issues two writes. To keep per-frame syscall cost flat we
batch through `sendmmsg` (the existing fast-path), so the bridging cost is
one extra entry per message buffer, not one extra syscall.

A small fast-path check (`if !bridging { ... }`) short-circuits the second
write outside the window. Outside the bridging window the cost is one
predictable branch; inside, it is one extra socket write per frame.

State transitions:

- **Enter bridging** when the registry signals "quorum-confirmed Successor,
  `now < TransitionEpoch`, active ≠ successor". Forwarder picks up the
  successor `(ShardBits, mode, GenerationID)` atomically (single pointer
  swap behind a `sync.Map.Load` of the consumer's current view).
- **Exit bridging** at `max(TransitionEpoch, now + autoConfig.bridgingWindow)`.
  Forwarder swaps the active pointer to the successor; the previous active
  is freed.
- **Abort bridging** if the Successor block disappears from quorum before
  `TransitionEpoch`. Forwarder reverts: stop emitting to successor, keep
  emitting to active. (Operator rolled back the pilot announcement.)

### Listener implementation

The listener's data-plane socket already supports incremental joins via
`netjoin`. Bridging mode rebuilds the join set as
`static_include ∪ active.pilot_groups ∪ successor.pilot_groups` (when
`autoJoinFromManifest=true`) or
`static_include ∪ derived_from(active.ShardBits) ∪ derived_from(successor.ShardBits)`
(when auto-join is off — i.e. operator-pinned shard ranges expand to cover
both layouts). The `netjoin` rate limiter (per SSM plan) governs the
join/leave churn so a transition does not flood MLDv2 reports.

At cutover the listener leaves the now-unused old-generation-only groups
in one rate-limited batch. The dedup window is what protects downstream
from in-flight tail frames after the leave but before MLDv2 prune
completes; the dedup TTL ≥ 2× bridging-window requirement captures this.

### Shard-manifest (pilot) implementation

The pilot operator stages a re-shard by:

1. Setting `-successor-shard-bits`, `-successor-generation-id`, and
   `-transition-epoch` on the authoritative shard-manifest replicas
   (all of them; the pilot quorum will form on the new Successor block).
2. Optionally adjusting `-successor-source-mode` if changing ASM↔SSM
   simultaneously (the bridging mechanism is independent of the
   addressing mode).
3. After `TransitionEpoch + grace`, removing the `-successor-*` flags so
   the manifest reverts to single-generation steady state, with
   `GenerationID` rolled to the value that was the successor.

The pilot is operationally responsible for picking `TransitionEpoch`
sufficiently far in the future that all consumers have time to enter
bridging mode. Recommended: `now + 4 × AnnounceInterval` so even the
last-to-receive consumer has at least `2 × AnnounceInterval` of bridging.
Hard floor enforced at the pilot: `TransitionEpoch >= now + 2 ×
AnnounceInterval`; below that the pilot refuses to publish.

### Failure modes (live re-sharding specific)

- **TransitionEpoch already passed when consumer first sees it.** Treat as
  invalid; the manifest is logged and discarded for adoption purposes.
  `multicast_manifest_resharding_stale_total` increments.
- **Successor block contradicts the ±1 rule** (e.g. consumer is at SB=10,
  pilot announces successor SB=12). Reject; log; do not enter bridging.
  `multicast_manifest_resharding_invalid_total{reason="shift-bound"}`
  increments. Pilot is misconfigured.
- **Clock skew between consumer and pilot exceeds `AnnounceInterval / 2`.**
  Detected by comparing the manifest's `Epoch` field to local time;
  consumers with > `AnnounceInterval/2` skew log a warning, increment
  `multicast_manifest_clock_skew_seconds`, and floor their bridging
  duration to `autoConfig.bridgingWindow` regardless of `TransitionEpoch`.
  Operators MUST run NTP; document this in the runbook.
- **Quorum lost mid-bridging.** Treat as "abort bridging" per the proxy
  state machine: stop emitting to successor; keep emitting to active.
  Listener releases successor-only joins. `pilot_groups` reverts.
- **Dedup disabled or under-sized at the listener.** Helm validation
  fails closed: `liveResharding=true && (EgressDedupCap == 0 ||
  EgressDedupTTL < 2 × max(bridgingWindow, AnnounceInterval))` is
  rejected at startup. Operator must enable / size dedup before flipping
  the toggle.
- **Per-frame latency budget tight under 2× emission cost.** This is a
  capacity-planning concern, not a correctness one. The
  `bsp_forwarder_emit_seconds` histogram already exists; alert on
  P99 > target during the window. If the proxy cannot sustain 2× emission
  the operator should either provision more proxies before the
  transition or accept restart-mode.

### Why this is bounded by BRC-137's existing safety rules

- BRC-137 already requires `ShardBits` shifts to be ±1 per rolling
  `AnnounceInterval`. Bridging mode inherits this — no faster shifts
  are possible.
- The pilot quorum and hysteresis already gate adoption of the
  Successor block, so a single misconfigured pilot cannot initiate a
  fleet-wide re-shard.
- Manual pins still win; a manually-pinned `ShardBits` on a component
  means that component does not enter bridging mode even if the pilot
  announces one. It continues serving its pinned generation; dedup
  handles any cross-traffic.

For Posture C the consumer joining the beacon group **MUST** include
shard-manifest pods in its source filter or it will not see any manifests. This
is the practical consequence of BRC-137 §"Multicast Group" reusing the beacon
group `0xFFFD` for both ADVERTs and manifests. Operators specifying
`bootstrap.manifest` for the SSM bootstrap of the shard-manifest emitters also
satisfy the auto-config consumer's source filter requirement on the same group.

> **BRC-137 amendment required to support Posture C cleanly:** the current spec
> wording says "the beacon group itself remains ASM regardless." This is in
> tension with Posture C. The SSM plan already calls this out as a required
> follow-up amendment; this plan adopts the relaxed wording in
> [BRC-137 amendments](#brc-137-amendments) below.

## Per-component changes

### `shard-common/manifest/` (new package)

Promoted from a per-component subsystem to a shared package because proxy and
listener need the same evaluator. Responsibilities:

- Manifest decoder dispatch from a shared beacon socket (callers demux MsgType
  and hand `0x40` payloads here).
- Registry keyed on `(SrcIPv6, InstanceID)` with TTL eviction.
- Quorum + hysteresis evaluator per BRC-137 normative rules.
- Source-set union evaluator (no quorum, dedup).
- Divergence accounting + Prometheus metrics surface.
- `Applier` interface so each component plugs its own restart-or-reload
  semantics.

### `shard-manifest`

The wire format change has already landed in BRC-137; the daemon needs the Go
encode/decode and config additions to match.

- **Encode/decode** for `SourceCount`, `Flags.SourcesValid`,
  `Flags.SourceModeSSM`, `Flags.PilotOnly`. Files:
  [`shard-common/frame/shard_manifest.go`](../../../shard-common/frame/shard_manifest.go),
  [`shard-common/frame/frame.go`](../../../shard-common/frame/frame.go).
- **New config** flags: `-source-mode {asm,ssm}`, `-sources` (comma list of IPv6
  or DNS names; resolved at startup and on `-sources-refresh`, matching the SSM
  plan's bootstrap-resolution semantics), `-pilot-only` (forces
  `-authoritative=true`). Files:
  [`shard-manifest/config/config.go`](../../../shard-manifest/config/config.go),
  [`shard-manifest/sender/sender.go`](../../../shard-manifest/sender/sender.go).
- How `shard-manifest` learns the data-plane publisher set (K8s pod watch,
  operator inventory, registration RPC) remains its internal concern per the SSM
  plan. This plan adds no requirement there.

### `shard-proxy`

Today the proxy emits to data groups only — it does **not** join the beacon
group. Auto-config adds a beacon-socket consumer.

- **New `manifest/` package** wiring the shared `shard-common/manifest/`
  consumer to the proxy's lifecycle: opens the beacon socket (posture-aware),
  demuxes `buf[6]` (proxy has no ADVERT consumer today, so non-`0x40` MsgTypes
  are counted and dropped), feeds payloads to the registry. Started only when
  `autoConfig.enabled=true`. New package
  [`shard-proxy/manifest/`](../../../shard-proxy/).
- **Reactive addressing.** The forwarder's group-address helper currently
  captures `MCPrefix` / `MCGroupID` / `ShardBits` at load. Under auto-config
  those become reads against the adopted view. A change in `ShardBits` or
  `SourceModeSSM` flips `/readyz`, drains, exits non-zero. Files:
  [`shard-proxy/config/config.go`](../../../shard-proxy/config/config.go),
  [`shard-proxy/forwarder/forwarder.go:204`](../../../shard-proxy/forwarder/forwarder.go#L204)
  (`OpenTargets`),
  [`shard-proxy/forwarder/forwarder.go:853`](../../../shard-proxy/forwarder/forwarder.go#L853)
  (`openEgressSocket`).
- **New flags / env** per the [config table](#configuration-surface). File:
  [`shard-proxy/config/config.go`](../../../shard-proxy/config/config.go).

The proxy does not adopt anything beyond addressing parameters. It does not
derive `bindSource` from manifests; `bindSource` remains an operator-provided
value (SSM plan).

### `shard-listener`

The listener already has a beacon-socket consumer for BRC-126 ADVERTs. This plan
extends the demux and adds the auto-join applier.

- **Extend the existing beacon listener** to demux on `buf[6]`: `0x20` →
  existing ADVERT path, `0x40` → new manifest consumer path. ADVERT path is
  unchanged. Under Posture C/D the listener's beacon socket is opened SSM via
  `netjoin` with `bootstrap.beacon ∪ bootstrap.manifest` instead of
  `net.ListenMulticastUDP`. File:
  [`shard-listener/discovery/beacon.go:48-108`](../../../shard-listener/discovery/beacon.go#L48-L108).
- **New `manifest/applier.go`.** When `autoJoinFromManifest=true`, maintain a
  `pilot_groups` set rebuilt from the union of authoritative `Flags.GroupsValid`
  payloads (after quorum + hysteresis). The listener's effective subscription =
  `static_include ∪ pilot_groups`. On set changes, issue posture-correct join /
  leave through `netjoin`. New file
  [`shard-listener/manifest/applier.go`](../../../shard-listener/); call sites
  in
  [`shard-listener/listener/listener.go:178-191`](../../../shard-listener/listener/listener.go#L178-L191).
- **SSM source-set integration.** When `SourceModeSSM=1`, the source set for
  `MCAST_JOIN_SOURCE_GROUP` is the deduplicated union of `Flags.SourcesValid`
  payloads. This composes with the SSM plan's
  `multicast.sources.consume: [manifest]` field — same consumer subsystem, no
  second wiring path. Files:
  [`shard-listener/listener/listener.go`](../../../shard-listener/listener/listener.go),
  `shard-common/netjoin/` (per SSM plan).
- **New flags / env** per the [config table](#configuration-surface). File:
  [`shard-listener/config/config.go`](../../../shard-listener/config/config.go).

Listener `ShardBits` / `SourceModeSSM` change handling mirrors the proxy:

- With `liveResharding=false` (default): flip `/readyz`, drain, exit.
- With `liveResharding=true`: enter bridging mode per
  [Live re-sharding](#live-re-sharding-optional); no exit.

Adopting a new `pilot_groups` set does **not** require restart in either
mode — `netjoin` issues incremental join/leave. Static `-shard-include`
entries are never leaved.

## Bootstrap and failure modes

Cold-start paths:

- **`manifest-bootstrap=optional`** (default). Use CLI / env values; data plane
  binds immediately. Pilot view applied opportunistically once quorum +
  hysteresis met.
- **`manifest-bootstrap=required`, quorum not met within readiness budget.**
  `/readyz` returns 503 throughout. Pod restart depends on the readinessProbe
  budget; document this as the failure mode operators must size probes for.
- **Posture C, no `bootstrap.manifest` configured.** Beacon socket has no source
  filter for shard-manifest pods → no manifests received → quorum never met.
  Helm validation MUST fail closed when
  `autoConfig.enabled=true && sourceMode=ssm && len(bootstrap.manifest)==0`.

Steady-state divergence:

- **Pilot disagreement (split brain).** No adoption (quorum not met for any
  single value). Last adopted value retained. Divergence telemetry fires.
  Operator reconciles pilots.
- **Manual pin contradicts adopted pilot.** Manual pin wins locally.
  `multicast_manifest_divergence_total{kind=pin-disagree}` increments. No
  restart.
- **All pilots disappear (TTL expiry).** Retain last adopted values
  indefinitely. `multicast_manifest_pilots_known` drops to 0; alert fires. No
  automatic revert to CLI defaults (would cause flap).

Adoption transitions (with `liveResharding=false`, the default):

- **`ShardBits` adoption change.** Flip `/readyz`, drain `DrainTimeout`, exit
  non-zero. Orchestrator restarts; warm registry preserves the new value across
  the restart.
- **`SourceModeSSM` adoption change.** Same restart path. Egress sockets need
  re-binding with the new prefix (and, for proxy, the existing `bindSource`);
  restart is simpler than in-place rebind.
- **`pilot_groups` change (listener auto-join).** Incremental join/leave via
  `netjoin`. No restart. Static `-shard-include` entries never leaved.
- **SSM source-set shrinks.** `MCAST_LEAVE_SOURCE_GROUP` for departed sources
  via `netjoin`; rate-limited per the SSM plan. Shares the `netjoin` rate
  limiter with the cold-start machinery.

Adoption transitions (with `liveResharding=true`):

- **`ShardBits` / `SourceModeSSM` change announced via `Successor` block.**
  Enter bridging mode per [Live re-sharding](#live-re-sharding-optional).
  No restart. `/readyz` stays green. Dedup MUST be enabled at the listener
  (Helm validation rejects the combination otherwise).
- **`ShardBits` / `SourceModeSSM` change without a `Successor` block** (pilot
  bumped the active generation directly with no bridging). Fall back to the
  restart path: flip `/readyz`, drain, exit. Pilots intending live re-shards
  MUST publish a Successor block; this is the safety valve when an operator
  forgets.
- **Bridging window aborted** (Successor block disappears before
  `TransitionEpoch`). Cleanly stop emitting to / joined to the successor;
  retain the active generation. Logged with the original `Successor`
  GenerationID for traceability.
- All other (`pilot_groups`, source-set) transitions are unchanged.

## Metrics

The BRC-137 normative profile already requires:

- `multicast_manifest_pilots_known`
- `multicast_manifest_quorum_met{field=...}`
- `multicast_manifest_divergence_total{field=...,kind=peer-disagree|pin-disagree|crc-fail}`
- `multicast_manifest_last_divergence_epoch{field=...}`

This plan adds, on every consumer:

- `multicast_manifest_adopted_value_info{field=...,value=...}` — info-style
  gauge for the currently adopted value per low-cardinality field (`shard_bits`,
  `source_mode`, `mc_group_id`).
- `multicast_manifest_adoption_total{field=...,reason=...}` — counter for
  `bootstrap`, `quorum-shift`, `pin-removed`.

Listener-only:

- `multicast_manifest_groups_added_total` / `_removed_total` — counters tracking
  auto-join churn from `autoJoinFromManifest`.
- The SSM plan already provides `multicast_ssm_sources_joined`,
  `multicast_ssm_filter_capacity_used`, and friends; the manifest-fed source set
  lights these up the same way as the `sources.bootstrap.*` path. No new metric
  needed.

Live re-sharding additions (every consumer when `liveResharding=true`):

- `multicast_manifest_resharding_state` — gauge with values `0` (steady),
  `1` (bridging), `2` (cutover-pending). Bridging duration can be derived
  from successive samples.
- `multicast_manifest_resharding_window_seconds` — gauge of seconds remaining
  until `TransitionEpoch`. Negative briefly during cutover.
- `multicast_manifest_resharding_emit_duplicates_total` — proxy-only counter
  of frames double-emitted (one increment per pair). Lets dashboards
  estimate the bandwidth uplift during bridging.
- `multicast_manifest_resharding_stale_total`,
  `multicast_manifest_resharding_invalid_total{reason=...}` — counters for
  the failure modes enumerated in
  [Live re-sharding § Failure modes](#failure-modes-live-re-sharding-specific).
- `multicast_manifest_clock_skew_seconds` — gauge of consumer-to-pilot
  clock skew computed from each manifest's `Epoch` field. Alert when
  > `AnnounceInterval / 2`.

Raw IPv6 labels MUST NOT appear on any metric here, per the SSM plan's
cardinality discipline.

## BRC-137 amendments

Two amendments are required: one wording clarification (already in flight
from the SSM plan) and one optional payload block to support live
re-sharding.

### 1. Beacon-group posture clarification (in the spec already)

Aligns BRC-137 with the SSM plan's
[Posture C](../SourceSpecificMulticast/ssm-support-plan.md#posture-c--ssm-intra-domain-recommended)
and lets auto-config consumers join the beacon group SSM under that
posture. Already applied to the docs/ copy of BRC-137: the bit-3
description now reads "Whether the beacon group itself is joined via ASM
or SSM is a deployment-posture choice … not a BRC-137 invariant."

### 2. Successor block for live re-sharding (new, when `liveResharding` ships)

New flag bit + new optional payload section. Backward compatible:
consumers that do not implement the bit ignore it and the trailing
payload bytes (the existing `BitmapBytes` / `GroupCount` / `SourceCount`
total still bounds the payload they care about; the extra trailing bytes
are slack to such consumers, which is already the documented forward-
compatibility posture for BRC-137 payload growth).

- **New flag bit 6:** `SuccessorValid`. When set, the trailing payload
  carries a 24-byte fixed Successor block appended after the sources
  payload (i.e. after `64 + max(GroupCount × 2, BitmapBytes) +
  SourceCount × 16` bytes).
- **Successor block layout (24 bytes, all big-endian):**

  ```text
  Offset (within block)  Size  Field
  ---------------------  ----  -----
                      0    16  SuccessorGenerationID  the incoming generation's 128-bit ID
                     16     1  SuccessorShardBits     1..15; MUST satisfy |Successor - Active| ≤ 1
                     17     1  SuccessorFlags         bit0 SuccessorSourceModeSSM, bits1..7 reserved (=0)
                     18     2  Reserved               MUST be 0
                     20     4  TransitionEpoch        Unix seconds at which the successor becomes the sole active generation
  ```

- **Normative consumer rules when `SuccessorValid=1`:**
  - Reject the datagram if `|SuccessorShardBits − ShardBits| > 1`.
  - Reject the datagram if `Authoritative=0` (live re-sharding signals
    require operator authority).
  - Apply the existing quorum + hysteresis gate to the Successor block
    as a unit (`(SuccessorGenerationID, SuccessorShardBits,
    SuccessorFlags, TransitionEpoch)` is the candidate value).
  - Once quorum is met, consumers with `liveResharding=true` MAY enter
    bridging mode; consumers with `liveResharding=false` MUST treat the
    Successor block as a divergence event only and continue restart-mode
    semantics on the next `GenerationID` rotation.
  - A consumer that has entered bridging mode MUST exit it at
    `local_clock ≥ TransitionEpoch`. Operators MUST keep clock skew
    below `AnnounceInterval / 2`; the consumer SHOULD log and
    `multicast_manifest_clock_skew_seconds` SHOULD reflect observed
    skew.
- **Pilot guidance:** The pilot SHOULD choose
  `TransitionEpoch ≥ now + 4 × AnnounceInterval` so the last-to-receive
  consumer has at least `2 × AnnounceInterval` of bridging. Pilots MUST
  reject configurations with `TransitionEpoch < now + 2 ×
  AnnounceInterval`.

This is the only new wire-format surface. No other amendments needed.

## Migration / rollout

Phase 1 — auto-config consumer (restart mode only):

1. Land the BRC-137 wording relaxation #1 called out in
   [BRC-137 amendments](#brc-137-amendments). Self-contained, no code.
2. Land the `shard-common/manifest/` consumer package: decoder dispatch,
   registry, quorum/hysteresis evaluator, source-set union, divergence
   telemetry, `Applier` interface. Purely additive, no wiring yet.
3. Implement the BRC-137 Go encode/decode catch-up in `shard-common` for
   `SourceCount`, `SourcesValid`, `SourceModeSSM`, `PilotOnly`. Round-trip
   tests; no behavior change for emitters until step 4.
4. Extend `shard-manifest` to emit the new flags / payload, including
   DNS-resolved `-sources`. Validate with the existing test harness.
5. Wire the consumer into `shard-listener` behind `autoConfig.enabled=false`.
   Ship metrics + docs but no behavior change for existing deployments.
6. Wire the consumer into `shard-proxy`, same opt-in pattern. The proxy gains
   its first beacon-socket listener here; validate the restart-on-adopt path in
   a small lab fleet.
7. Add `autoJoinFromManifest` to the listener. Validate that static includes are
   never leaved, that the union behaves correctly under pilot churn, and that
   the SSM source path composes cleanly with the SSM plan's `netjoin` rate
   limiter.
8. Validate end-to-end under each posture, focusing on Posture C where the
   beacon socket itself is SSM (`bootstrap.beacon ∪ bootstrap.manifest` source
   filter). Stand up at least three `Flags.PilotOnly=1` shard-manifest replicas
   across failure domains.

Phase 2 — live re-sharding (gated on Phase 1 stability):

1. Land BRC-137 amendment #2 (Successor block, flag bit 6) in the docs/
   copy. Round-trip encode/decode tests in `shard-common`.
2. Add Successor-block emission to `shard-manifest`
   (`-successor-shard-bits`, `-successor-generation-id`,
   `-transition-epoch`, `-successor-source-mode`). Validate the pilot-side
   floor (`TransitionEpoch >= now + 2 × AnnounceInterval`) rejects bad
   inputs.
3. Implement bridging mode in the proxy forwarder: dual-emit fast path,
   state machine (enter/exit/abort), `multicast_manifest_resharding_*`
   metrics. Lab validation at proxy throughput targets to confirm the
   extra emission stays within capacity.
4. Implement bridging mode in the listener: union-join applier, dedup
   sizing check at startup (fail-closed if undersized), cutover-leave
   rate-limiting via `netjoin`.
5. End-to-end re-shard exercises in a representative lab: SB=11→12 and
   SB=12→11, with `autoConfig.liveResharding=true` on a subset of
   consumers and `false` on the rest. Confirm: zero downstream gaps for
   the live-reshard consumers; restart-mode consumers cycle cleanly in
   parallel; dedup absorbs duplicate frames; `/readyz` stays green for
   live-reshard consumers throughout.

Operator runbook (Phase 1 + 2):

1. Document in `docs/`: pilot deployment, `GenerationID` bumping
   discipline, divergence triage, restart-on-adopt and live-reshard
   semantics, the Helm-validation rules (fail-closed when
   `autoConfig.enabled=true && sourceMode=ssm && bootstrap.manifest
   empty`; fail-closed when `liveResharding=true && dedup undersized`),
   NTP requirement for live re-sharding, and the rollback procedure if a
   Successor block needs to be retracted mid-bridging.

## Interactions with other BRCs and plans

- **BRC-126 (ADVERT).** Unchanged on the wire. The proxy gains a beacon-socket
  consumer for auto-config; non-`0x40` MsgTypes are counted and dropped (it has
  no ADVERT consumer today). The listener's existing ADVERT consumer continues
  to work; the new manifest consumer shares the same socket and demuxes on
  `buf[6]`.
- **BRC-127 (Subtree group announcements).** Unchanged. BRC-127 frames go
  through the proxy on `0xFFFC`; orthogonal to the addressing parameters this
  plan distributes.
- **BRC-129 (Multicast addressing).** No new index allocated. Consumers derive
  ASM/SSM prefix from `Flags.SourceModeSSM` per the spec; `MCGroupID` is derived
  from the beacon socket's destination address, not from a payload field.
- **BRC-137.** This plan implements the consumer profile. One documentation-only
  amendment landed by this plan; no wire-format changes.
- **SSM support plan.** This plan reuses the SSM plan's `multicast:` Helm block
  (`bootstrap.*`, `sources.consume`, `sourceMode`, `bindSource`), `netjoin`
  helper, join-rate limiter, and Multus/IPAM prerequisites. Sequence-independent
  of SSM (an ASM-only deployment can opt in and get `ShardBits` adoption), but
  SSM deployments at the target scale depend on this plan's consumer to feed
  `netjoin` with the manifest-derived source set.

## Open questions

- `pilot-quorum=2` default is conservative; `1` is friendlier in small or lab
  clusters but is a single point of misconfiguration failure. Current proposal:
  keep `2` with a warning on `1`. Revisit after operator experience.
- Under Posture C the auto-config consumer joining the beacon group needs the
  source filter to include shard-manifest pods. The plan uses
  `bootstrap.beacon ∪ bootstrap.manifest`. An alternative is a dedicated
  bootstrap key (`bootstrap.beaconReadAll` or similar) so operators can
  explicitly enumerate "everything that emits to the beacon group." Current
  proposal is to keep the union and document it; the union is the natural
  composition and avoids a fifth bootstrap key.
- Sidecar `shard-manifest` instances behind data-plane components were proposed
  in an earlier draft; the SSM plan now centralizes publisher discovery in
  `shard-manifest` itself. This plan follows the SSM plan. If a deployment shape
  ever needs per-publisher manifest emission (e.g. for partitioned fleets),
  revisit the trade-off explicitly rather than re-introducing sidecars by
  default.
