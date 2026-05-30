# Proxy/Listener Automatic Shard Configuration Plan

- Status: Proposal
- Scope: `shard-proxy`, `shard-listener`, `shard-manifest`, BRC-137 (docs/ copy only)
- Default behavior unchanged: manual configuration remains the default; auto-config is opt-in per component.
- Compatible with both ASM and SSM v6 multicast modes (see
  [SSM support plan](../SourceSpecificMulticast/ssm-support-plan.md)).
- Out of scope: per-listener group assignment via the manifest, retry-endpoint
  discovery (stays on BRC-126 ADVERT), NACK parameter distribution.

## Background

`shard-bits` and the IANA multicast group-id (`mc-group-id`) are the two
parameters that every component in the pipeline must agree on for the
addressing to interoperate. Today both are set manually on every component;
divergence is silent until traffic stops flowing.

[BRC-137](../brc-137-shard-manifest.md) defines a one-way announcement
datagram that already carries `ShardBits`, `GenerationID`, an authoritative
flag, and an optional joined-groups payload. The current spec is observational
— there is no normative consumer profile and no path for proxy/listener to act
on what it sees.

This plan promotes BRC-137 to a two-purpose protocol:

1. **Observational (today).** Every participant publishes its own state for
   operator visibility and divergence detection. No change.
2. **Pilot (new, opt-in).** A small set of operator-curated `Authoritative=1`
   manifests serve as the source of truth for `ShardBits`,
   `MCGroupID`, `SourceMode` (ASM/SSM), and the publisher source list (SSM
   only). Proxies and listeners that have opted in adopt these values after a
   quorum + hysteresis gate.

Auto-configuration never replaces the manual flags; it overlays them. An
operator can pin any field with a CLI flag and the consumer must respect the
pin while still tracking divergence in metrics.

## Goals / non-goals

- **Goal.** Let an operator stand up `shard-manifest` instances as the
  fleet's pilot and have proxies/listeners self-configure their addressing
  surface from those announcements, without touching the per-component
  Helm/CLI surface during steady-state operations.
- **Goal.** Let listeners optionally auto-join shard indices that the pilot
  announces, additive to whatever `-shard-include` already lists.
- **Goal.** Work uniformly for ASM and SSM. Under SSM the same manifest
  pipeline carries the publisher source list, removing the need for
  `sources.static` in steady state.
- **Goal.** Smallest-possible BRC-137 wire change.
- **Non-goal.** Per-listener group assignment from the pilot (no consistent
  hash, no explicit listener→groups map). Operators express listener role via
  `-shard-include` + the new `-shard-include-from-manifest` toggle.
- **Non-goal.** Distributing retry-endpoint addresses or NACK parameters.
  These remain on BRC-126 ADVERT and per-component config respectively.
- **Non-goal.** Live `ShardBits` re-sharding without restart. A
  `ShardBits` change is detected, surfaced, and triggers a graceful restart
  request via `/readyz` flipping false; orchestration handles the rollout.

## Design decisions

1. **Pilot model with authoritative quorum.** Consumers ignore manifests
   with `Flags.Authoritative=0` for adoption purposes (still indexed for
   visibility). They require a quorum of `≥ pilot-quorum` distinct
   authoritative announcers agreeing on a value before adopting it.
   Default quorum is `2` so a single misconfigured operator-side announcer
   cannot flip the fleet. `pilot-quorum=1` is supported (lab) but logs a
   warning at startup.
2. **Hysteresis gate.** A candidate value must hold quorum for
   `≥ 2 × AnnounceInterval` before adoption, matching the existing
   non-normative guidance in BRC-137 §Safety.
3. **Manual pin wins.** Whenever the operator passes a CLI flag, that value
   is authoritative for the local component. The consumer still tracks pilot
   announcements; divergence is recorded as a metric and a structured log,
   but the local value is unchanged.
4. **Additive listener auto-join.** When
   `-shard-include-from-manifest=true`, the listener's effective joined-group
   set is `union(-shard-include, pilot-announced-groups)`. Manifests
   without `Flags.GroupsValid=1` contribute nothing. Removing a group from
   the manifest releases the corresponding `MCAST_LEAVE_GROUP` only when no
   other pilot still claims it; groups in the static `-shard-include` are
   never released.
5. **Bootstrap behavior is configurable.** Two modes:
   - `manifest-bootstrap=optional` (default): the component starts with the
     CLI/env values and applies any pilot adjustments later (subject to the
     manual-pin rule above).
   - `manifest-bootstrap=required`: the component refuses to bind data-plane
     sockets until a quorum of pilots is observed for `ShardBits` and
     `MCGroupID`. `/readyz` returns 503 until then.
6. **Beacon group is always ASM and always required.** Per BRC-137 §Multicast
   Group, manifests ride the beacon group `FFx5::B:FFFD`. Auto-config
   consumers join this group on the configured `-beacon-scope`. SSM
   compatibility is preserved because the beacon group itself is exempt from
   the FF3x prefix change (see SSM plan §Source discovery).
7. **Divergence is a first-class metric.** Whether or not the consumer
   adopts, every observed `(SrcIPv6, InstanceID, ShardBits, MCGroupID,
   SourceMode, GenerationID)` mismatch increments a counter and refreshes a
   `last-divergence` gauge, with per-field cardinality bounded by bucket
   labels (not raw IP).

## BRC-137 amendments

The required wire-level additions are small and backward compatible: new
flag bits and a repurposed Reserved field. Encoded into the BRC-137 spec
in [`docs/brc-137-shard-manifest.md`](../brc-137-shard-manifest.md). The
canonical [BRC-137](https://github.com/bitcoin-sv/BRCs/blob/master/transactions/0137.md)
update follows after the docs/ copy lands and bakes.

### Header repurposing

| Offset | Field          | v1 meaning           | v1.1 meaning                                                                                  |
| ------ | -------------- | -------------------- | --------------------------------------------------------------------------------------------- |
|     42 | Reserved (2 B) | MUST be 0            | `SourceCount` (uint16, big-endian): number of trailing 16-byte source-IPv6 entries appended after the groups payload. 0 = no source list. |

Wire layout when `SourceCount > 0`:

```text
[0..64)            ShardManifest header (existing)
[64..groups_end)   Groups payload: bitmap or list, per Flags.GroupsValid
[groups_end..end)  Sources payload: SourceCount × 16 bytes IPv6, in network byte order
```

`groups_end = 64 + max(GroupCount × 2, BitmapBytes)`. When
`Flags.GroupsValid=0`, `groups_end = 64`.

CRC coverage already spans the entire datagram, so `ManifestCRC` requires no
change.

### New flag bits

| Bit | Name           | Meaning                                                                                                                                                                                                                                                                                |
| --- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3   | SourceModeSSM  | When set, the announcer declares the data-plane uses SSM (FF3x::/32). Auto-config consumers MUST use the SSM prefix when computing data-plane group addresses derived from this manifest. Beacon and BRC-127 announce groups remain ASM regardless. Adoption requires quorum like other fields. |
| 4   | SourcesValid   | When set, the trailing `SourceCount × 16` bytes carry the announcer's contributed publisher source IPv6 list. Consumers union the source set across all currently-valid manifests they hold. When `SourceModeSSM=1`, listeners feed this union into the SSM (S,G) join calls. When clear, `SourceCount` MUST be 0. |
| 5   | PilotOnly      | When set, this manifest is exclusively a pilot/assignment broadcast and the announcer is not itself joined to the announced groups. The groups payload (if any) describes what the fleet SHOULD join, not what the announcer joined. Implies `Authoritative=1`; consumers MUST reject manifests with `PilotOnly=1 && Authoritative=0` as malformed. |

Bits 6..7 remain reserved (MUST be 0).

`RoleHint=5` (`manifest-only`) is the suggested role for a pilot announcer.
`PilotOnly` carries the semantic distinction on the wire so consumers do not
need to filter by the informational `RoleHint`.

### MCGroupID source

`MCGroupID` is **not** added to the manifest payload. The beacon group
address already encodes it (bytes 12–13 = `0x000B` by default), and the
receive socket joined to that group exposes the value via the destination
address. Consumers MUST derive `MCGroupID` from the destination address of
the received datagram rather than trusting a payload field. This means a
pilot operating on a non-default `MCGroupID` necessarily reaches consumers
only on a beacon address that already carries that group-id, so quorum
naturally partitions across fleets sharing addressing.

### Consumer profile (normative addition to BRC-137)

A new "Consumer Behaviour — Auto-configuration" section promotes most of
the existing informative consumer guidance to normative MUST/SHOULD when
the consumer opts in. Summary:

- MUST verify `ManifestCRC` and reject mismatches.
- MUST dispatch on `buf[6]` to demux ADVERT vs ShardManifest.
- MUST key registry entries on `(SrcIPv6, InstanceID)` taken from the IPv6
  datagram header (not the informational in-frame `SrcIPv6`).
- MUST evict on `Epoch + TTL` or `Epoch + 3 × AnnounceInterval` when
  `TTL=0`.
- MUST ignore manifests with `Authoritative=0` for adoption.
- MUST require a quorum of `pilot-quorum` distinct authoritative
  announcers reporting the same value before adopting any field.
- MUST hold a candidate value for `≥ 2 × AnnounceInterval` before adopting
  (hysteresis).
- MUST NOT downgrade `ShardBits` by more than ±1 per rolling
  `AnnounceInterval` window (matches existing safety guidance).
- MUST emit `multicast_manifest_divergence_total{field=...}` and refresh
  `multicast_manifest_last_divergence_epoch{field=...}` on any observed
  disagreement, whether or not adoption occurs.
- SHOULD prefer the union of `SourcesValid=1` payloads from all currently
  valid manifests (not only authoritative ones) for SSM source discovery,
  since a non-authoritative proxy still legitimately publishes its own
  `bindSource`.

## Per-component changes

### `shard-manifest`

The daemon already publishes the announcer's own state. Two additions:

| Change | Files |
| ------ | ----- |
| Encode/decode `SourceCount`, `SourcesValid`, `SourceModeSSM`, `PilotOnly` flags. | [`shard-common/frame/shard_manifest.go`](../../../shard-common/frame/shard_manifest.go), [`shard-common/frame/frame.go`](../../../shard-common/frame/frame.go) |
| New config: `-source-mode {asm,ssm}`, `-sources` (comma list of IPv6 for `SourcesValid` payload; `auto` resolves to the daemon's primary IPv6), `-pilot-only`. When `-pilot-only=true`, `-authoritative` is forced to true. | [`shard-manifest/config/config.go`](../../../shard-manifest/config/config.go), [`shard-manifest/sender/sender.go`](../../../shard-manifest/sender/sender.go) |

Operators deploy `shard-manifest` in two flavours:

- **Sidecar manifests** (current default): one per data-plane component,
  publishing the component's own state. Non-authoritative. Used for
  visibility and (under SSM) per-proxy source contribution.
- **Pilot manifests** (new): a small operator-curated set
  (recommended: 3 instances across failure domains) with `-authoritative` and
  `-pilot-only`. They declare the canonical `ShardBits`, `SourceMode`, and
  (optionally) the full fleet source list.

### `shard-proxy`

| Change | Files |
| ------ | ----- |
| Add manifest-consumer subsystem (new package `manifest/`). Joins the beacon group(s), maintains registry, evaluates quorum + hysteresis, exposes accessor for current pilot view. | new files under [`shard-proxy/manifest/`](../../../shard-proxy/) |
| Wire pilot view into addressing. Proxy currently derives `MCPrefix` from `-scope` and `MCGroupID` from `-mc-group-id` at load time. Under auto-config, those values become reactive: a successful pilot adoption can change `ShardBits` (triggers graceful restart request), or `SourceMode` (re-bind egress sockets with new prefix). | [`shard-proxy/config/config.go`](../../../shard-proxy/config/config.go), [`shard-proxy/forwarder/forwarder.go:210-245`](../../../shard-proxy/forwarder/forwarder.go#L210-L245) |
| New flags (env vars in CAPS): `-manifest-consumer-enabled` (default `false`), `-manifest-beacon-scope` (default `=scope`), `-manifest-bootstrap {optional,required}` (default `optional`), `-pilot-quorum` (default `2`), `-pilot-hysteresis` (default `0` ⇒ `2 × AnnounceInterval`). | [`shard-proxy/config/config.go`](../../../shard-proxy/config/config.go) |
| Self-publish a sidecar manifest. The proxy emits its own non-authoritative manifest with `RoleHint=proxy` and (under SSM) `SourcesValid=1` carrying its `bindSource`. Implementation can either spawn `shard-manifest` as a sidecar (Helm pattern) or call the encoder inline; recommend the sidecar route to keep the proxy hot path lean. | [`shard-proxy-helm/`](../../../shard-proxy-helm) |

`ShardBits` change handling: the proxy does **not** rebuild its addressing
tables in place. On adopted-value change it flips `/readyz` to 503, drains
in-flight datagrams for `DrainTimeout`, then exits non-zero. The
orchestrator (Helm/Kubernetes) restarts the pod, which reloads with the new
value already present in the registry on second start. This keeps the
forwarder lock-free.

### `shard-listener`

| Change | Files |
| ------ | ----- |
| Manifest-consumer subsystem analogous to the proxy. Reuses the same `manifest/` package promoted into `shard-common/manifest/`. | new package [`shard-common/manifest/`](../../../shard-common/) |
| Extend the existing beacon listener to dispatch on `buf[6]`: `0x20` → existing ADVERT path, `0x40` → new manifest path. Keep the ADVERT path unchanged (BRC-126 is out of scope). | [`shard-listener/discovery/beacon.go:48-108`](../../../shard-listener/discovery/beacon.go#L48-L108) |
| Auto-join logic. When `-shard-include-from-manifest=true`, maintain a `manifest_groups` set rebuilt from the union of authoritative groups payloads (after quorum/hysteresis). Listener's effective subscription = `static_include ∪ manifest_groups`. On set changes, issue `MCAST_JOIN_GROUP` / `MCAST_LEAVE_GROUP` (ASM) or `MCAST_JOIN_SOURCE_GROUP` / `MCAST_LEAVE_SOURCE_GROUP` (SSM, sources from `SourcesValid` payloads). | [`shard-listener/listener/listener.go`](../../../shard-listener/listener/listener.go) (join sites), new file [`shard-listener/manifest/applier.go`](../../../shard-listener/) |
| SSM source-discovery integration. When `sourceMode=ssm`, the listener's source list is `sources.static ∪ manifest_sources`. Hooks the same applier so source-set changes flow into the existing `netjoin` helper described in the SSM plan. | [`shard-listener/listener/listener.go`](../../../shard-listener/listener/listener.go), [`shard-common/netjoin/`](../../../shard-common/) (per SSM plan) |
| New flags: `-manifest-consumer-enabled` (default `false`), `-shard-include-from-manifest` (default `false`), `-source-discover {off,beacon,manifest}` (default `off`; `beacon` per SSM plan, `manifest` consumes `SourcesValid` payloads), `-pilot-quorum`, `-pilot-hysteresis`, `-manifest-bootstrap`. | [`shard-listener/config/config.go`](../../../shard-listener/config/config.go) |
| Self-publish a sidecar manifest (optional). Same Helm pattern as proxy; useful for fleet visibility but not required for the listener to consume. | [`shard-listener-helm/`](../../../shard-listener-helm) |

Listener `ShardBits` change handling mirrors the proxy: flip `/readyz`,
drain, exit. Adopting a new `manifest_groups` set does **not** require
restart — the join/leave path is incremental.

## ASM vs SSM compatibility

| Aspect              | ASM consumer                                          | SSM consumer                                                                                       |
| ------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Beacon group join   | `IPV6_JOIN_GROUP` on `FF0?::B:FFFD`                   | Same — beacon stays ASM by design (chicken-and-egg per SSM plan)                                   |
| Data group address  | `Scopes[MCScope]::MCGroupID:idx`                      | `0xFF35`/`0xFF3E::MCGroupID:idx` when adopted `SourceModeSSM=1`                                    |
| Source list usage   | Ignored                                               | `SourcesValid` payloads unioned across all valid manifests; fed into `MCAST_JOIN_SOURCE_GROUP`     |
| Adoption gating     | Quorum on `ShardBits`, `SourceMode=ASM`               | Quorum on `ShardBits`, `SourceMode=SSM`; source-list updates are not gated by quorum (additive)    |
| Fallback when pilot disappears | Continue with last adopted values until TTL expiry, then revert to CLI defaults and warn loudly | Same; source list naturally shrinks as per-proxy manifests expire, so SSM filter table self-prunes |

The same component build serves both modes. The branching introduced by
the SSM plan in `netjoin` is unchanged; the manifest applier feeds the
same helper.

## Config surface (per-component summary)

Common to proxy and listener (new flags):

| Flag                                 | Default                | Description                                                                                              |
| ------------------------------------ | ---------------------- | -------------------------------------------------------------------------------------------------------- |
| `-manifest-consumer-enabled`         | `false`                | Master switch; when false, no manifest is read for configuration purposes.                               |
| `-manifest-beacon-scope`             | `=scope`               | Comma list of scopes to join the beacon group on. Independent of data-plane scope.                       |
| `-manifest-bootstrap`                | `optional`             | `optional` ⇒ start with CLI values; `required` ⇒ refuse data-plane bind until quorum reached.            |
| `-pilot-quorum`                      | `2`                    | Minimum distinct authoritative announcers required for adoption. `1` allowed but logs a warning.          |
| `-pilot-hysteresis`                  | `0`                    | Duration to hold candidate value before adoption. `0` ⇒ `2 × AnnounceInterval` of the candidate manifest. |

Listener-only:

| Flag                                  | Default | Description                                                                                                                    |
| ------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `-shard-include-from-manifest`        | `false` | Enable additive auto-join. Effective set = `union(-shard-include, pilot-announced-groups)`.                                    |
| `-source-discover`                    | `off`   | `off` (today) / `beacon` (SSM plan) / `manifest` (new): consume `SourcesValid` payloads. Multiple values may be combined.       |

Proxy-only: none beyond the common set. The proxy adopts addressing
parameters and (if SSM) `SourceMode`; it does not derive its `bindSource`
from manifests.

`shard-manifest` (new flags):

| Flag           | Default | Description                                                                                                       |
| -------------- | ------- | ----------------------------------------------------------------------------------------------------------------- |
| `-source-mode` | `asm`   | `asm` or `ssm`; sets `Flags.SourceModeSSM`.                                                                       |
| `-sources`     | `""`    | Comma list of IPv6; `auto` ⇒ daemon's primary IPv6. Sets `Flags.SourcesValid` and writes the source payload.       |
| `-pilot-only`  | `false` | Sets `Flags.PilotOnly`; forces `-authoritative=true`; groups payload describes desired fleet state, not own joins. |

## Bootstrap and failure modes

| Scenario                                                            | Behavior                                                                                                                                                                  |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cold start, `manifest-bootstrap=optional`                           | Use CLI values; data plane binds immediately. Pilot view applied opportunistically.                                                                                       |
| Cold start, `manifest-bootstrap=required`, no pilots within 30 s    | `/readyz` returns 503; log every 5 s. Pod is restarted by orchestrator after its readinessProbe budget. Document this as the failure mode operators must size probes for. |
| Cold start, `manifest-bootstrap=required`, pilot quorum reached     | Adopt then bind. Total bootstrap time ≈ first-manifest latency + hysteresis window. With defaults: 0..AnnounceInterval + 2 × AnnounceInterval = up to 15 min. Tune `AnnounceInterval` down for faster fleets. |
| Pilot disagreement (split brain)                                    | No adoption (quorum not met for any single value). Last adopted value retained. Divergence metrics fire. Operator must reconcile pilots.                                  |
| Manual pin contradicts adopted pilot                                | Manual pin wins locally. `multicast_manifest_pin_divergence_total{field=...}` increments. No restart.                                                                     |
| All pilots disappear (TTL expiry)                                   | Retain last adopted values indefinitely. `multicast_manifest_pilots_known` drops to 0 and alert fires. No automatic revert to CLI defaults (would cause needless flap).    |
| `ShardBits` adoption change                                         | Flip `/readyz`, drain `DrainTimeout`, exit non-zero. Orchestrator restarts pod. Restart picks up new value from the still-warm registry on second start.                  |
| `SourceMode` adoption change                                        | Same restart path as `ShardBits`. Rebinding sockets in place is possible but adds invariant complexity; restart is cheap and predictable.                                 |
| `manifest_groups` change (listener auto-join)                       | Incremental join/leave on the data socket. No restart. Static `-shard-include` entries are never leaved.                                                                  |
| SSM source list shrinks                                             | Issue `MCAST_LEAVE_SOURCE_GROUP` for removed sources. Bounded by the `netjoin` rate limiter so a flapping publisher does not thrash the kernel mfib (see SSM plan).         |

## Metrics

New, on every consumer:

- `multicast_manifest_pilots_known` — gauge, distinct authoritative
  announcers currently within TTL.
- `multicast_manifest_quorum_met{field=...}` — gauge, `1` when the field has
  enough agreeing announcers to satisfy `-pilot-quorum`, `0` otherwise.
- `multicast_manifest_adopted_value_info{field=...,value=...}` — info-style
  gauge reporting the currently adopted value per field. `field` bucketed to
  `shard_bits`, `source_mode`, `mc_group_id` (raw value is low-cardinality
  by design).
- `multicast_manifest_divergence_total{field=...,kind=...}` — counter; `kind`
  is `peer-disagree`, `pin-disagree`, or `crc-fail`.
- `multicast_manifest_last_divergence_epoch{field=...}` — gauge, Unix seconds
  of most recent divergence.
- `multicast_manifest_adoption_total{field=...,reason=...}` — counter;
  `reason` is `bootstrap`, `quorum-shift`, `pin-removed`.

Listener-only:

- `multicast_manifest_groups_added` / `_removed_total` — counters tracking
  auto-join churn.
- `multicast_ssm_sources_from_manifest` — gauge of distinct source IPs
  contributed by `SourcesValid` payloads currently held. Composes with the
  SSM plan's `multicast_ssm_sources_joined`.

Per-source labels (raw IPv6) MUST NOT appear in any metric here; cardinality
guidance from the SSM plan applies.

## Migration / rollout

1. Land BRC-137 amendments in [`docs/brc-137-shard-manifest.md`](../brc-137-shard-manifest.md):
   new flag bits, `SourceCount`, the sources payload, and the normative
   auto-configuration consumer profile. Canonical BRC update follows.
2. Land the `shard-common/manifest/` consumer package: registry, quorum +
   hysteresis evaluator, divergence accounting, applier interface. No
   wiring yet — purely additive code.
3. Extend `shard-manifest` to emit `SourceCount`, `SourcesValid`,
   `SourceModeSSM`, `PilotOnly`. Validate end-to-end with the existing
   `shard-manifest` test harness; round-trip the new payload through
   encode/decode tests.
4. Wire the consumer into `shard-listener` behind
   `-manifest-consumer-enabled=false`. Ship with documentation and metrics
   but no behavior change for existing deployments.
5. Wire the consumer into `shard-proxy`, same opt-in pattern. Validate
   restart-on-adoption-change path in a small lab fleet.
6. Add `-shard-include-from-manifest` and `-source-discover=manifest` to
   the listener. Validate that static includes are never leaved, that the
   union behaves correctly under pilot churn, and that the SSM source path
   composes cleanly with the SSM plan's `netjoin` rate limiter.
7. Stand up pilot manifests in a single test cluster (3 replicas,
   `-pilot-only -authoritative`, separate failure domains). Flip
   `-manifest-consumer-enabled=true` on listeners first, then proxies.
8. Document the operator runbook in [`docs/`](..): pilot deployment,
   `GenerationID` bumping discipline, divergence triage, restart-on-adopt
   semantics.

## Interactions with other BRCs and plans

- **BRC-126 (ADVERT).** Unchanged. Retry-endpoint discovery stays on
  BRC-126. The auto-config consumer demuxes by `MsgType` and never touches
  ADVERTs.
- **BRC-127 (Subtree group announcements).** Unchanged. BRC-127 carries
  SubtreeID → GroupID mappings on `0xFFFC`; orthogonal to the addressing
  parameters this plan distributes.
- **BRC-129 (Multicast addressing).** No new index allocated. Consumers
  derive ASM/SSM prefix from `SourceModeSSM` and reuse the existing
  group-id and shard-index fields.
- **SSM support plan.** This plan is the discovery path that makes
  `discover=manifest` real. The two plans share the `netjoin` helper and
  Multus/IPAM prerequisites; this plan is sequence-independent of SSM (can
  ship ASM-only first), but SSM in production requires this plan's
  `SourcesValid` machinery to avoid `sources.static` sprawl.

## Open questions

- Should `pilot-quorum` default to `1` to make the lab → prod path
  smoother, accepting the SPOF risk? Current proposal: `2` with a clear
  warning on `1`. Revisit after lab experience.
- Should `manifest-bootstrap=required` apply to `MCGroupID` adoption?
  Currently MCGroupID is derived from the destination address of the
  beacon socket, so "no manifest received" cannot establish the value;
  `required` mode therefore gates on `ShardBits` and `SourceMode` only.
  Documented above but worth a sanity check during implementation.
- Sidecar vs in-process manifest emit from proxy/listener: sidecar keeps
  the hot path lean but adds a Helm component; in-process avoids the extra
  pod. Recommend sidecar; revisit if the operational overhead bites.
