# Source-Specific Multicast (SSM) Support Plan

- Status: **Implemented (opt-in)** across `shard-common` (v0.11.0+) and every
  consumer repo; full intra-domain Posture C (SSM-everywhere) still requires a
  PIM-SSM fabric. Required BRC amendments remain open — see below.
- Scope: All multicast-aware components and infra
- Default behavior unchanged: ASM remains the default; SSM is opt-in.

## Implementation state

- **[BRC-137 shard-manifest](../brc-137-shard-manifest.md) is already SSM-aware
  at the wire-format level.** It defines `Flags.SourceModeSSM` (bit 3),
  `Flags.SourcesValid` (bit 4), and an optional `SourceCount × 16`-byte IPv6
  sources payload after the groups encoding. It also locks in the consumer
  rules: when `SourceModeSSM=1`, auto-config consumers MUST use the SSM
  prefix when deriving data-plane group addresses, MUST union sources across
  currently-valid manifests, and MUST feed the union into `(S,G)` join calls.
  BRC-137 currently states "the beacon group itself remains ASM
  regardless" — this conflicts with the
  [Posture C](#posture-c--ssm-intra-domain-recommended)
  bootstrap-source design and is captured under
  [Required BRC amendments](#required-brc-amendments-out-of-scope-here)
  as a follow-up. The shard-manifest Go code has **not** yet implemented
  encode/decode for these fields — only the BRC is final.
- **No other component has SSM scaffolding yet.** All sender/receiver join
  and emit sites listed below are still pure ASM.
- **No Helm chart has any `multicast.sourceMode` / `multicast.bindSource` /
  `multicast.sources` field yet.** The config surface proposed here is
  entirely additive.
- **BRC-129 has not been amended** with the SSM addressing section
  proposed below, nor with the RFC 8815 inter-domain ASM deprecation —
  see [Required BRC amendments](#required-brc-amendments-out-of-scope-here).
- **RFC 8815 (Dec 2020) deprecates any-source multicast for inter-domain
  use.** Today's `FF0E::B:idx` global-scope ASM addressing is out of spec
  for inter-domain delivery and must be removed. The replacement is
  SSM-only at global scope (`FF3E::B:idx`). Site-scope ASM (`FF05`) remains
  permissible for intra-domain operation.

## Background

The current design is pure Any-Source Multicast (ASM): receivers do
`IPV6_JOIN_GROUP` with no source filter; senders rely on kernel source-address
selection on the egress interface; HashKey-based gap detection uses the
_observed_ source IPv6 post-receive, so multi-publisher topologies already
create per-source flows implicitly. There is no source identity in the
addressing scheme ([BRC-129](../brc-129-multicast-addressing.md)) and no source
list anywhere in the config surface.

## Goals / non-goals

- **Goal**: Add an opt-in `sourceMode: ssm` switch that, when enabled, (a) joins
  `(S,G)` instead of `(*,G)`, (b) uses the IPv6 SSM address range, (c) lets
  operators pre-declare source IPs or learn them dynamically. ASM remains the
  default.
- **Non-goals**:
  - No change to frame format, NACK protocol, HashKey computation, or shard
    derivation. SSM is a deployment/transport mode, not a protocol
    revision.
  - **No edits to BRC-129 or BRC-137 in this plan.** This document
    enumerates the amendments those specs require (see
    [Required BRC amendments](#required-brc-amendments-out-of-scope-here)),
    but the BRC files themselves are touched only in follow-up
    BRC-cleanup work.
- **Design decisions** (resolved from open questions):
  1. **Single mode per deployment.** `sourceMode` applies to every group a
     component touches; no per-group override.
  2. **Distinct source IP per shard-proxy.** Preserves the per-publisher
     HashKey semantics already in the listener/retry-endpoint flow tracking,
     and is the only model PIM-SSM tolerates cleanly. SSM RPF picks one path
     per `S`, so anycast/ECMP-style "shared source IP" deployments are
     **not supported** — packets from the non-RPF proxies are dropped at the
     first L3 hop. If a deployment requires a single stable source identity
     (e.g. for a constrained bootstrap source list), use VRRP/keepalived
     active-standby: one proxy is live at a time, holding a floating IP, and
     others are warm spares. That is failover, not load distribution;
     horizontal scale-out means more distinct `S` values, period.
  3. **subtx-generator gains an SSM-aware direct-multicast mode.** Its
     existing unicast-to-proxy path stays the default; a new direct-emit
     mode lets the `10gb-direct-testing` harness exercise the SSM data path
     without a proxy in the loop.
  4. **Target end state is SSM-everywhere, including control groups.**
     Each control group is joined as SSM against a small per-group
     bootstrap source list naming that group's authoritative emitters
     (retry-endpoint pods for the beacon group, shard-manifest pods for
     the manifest group, etc.). This removes the chicken-and-egg
     "control groups must be ASM" carve-out and eliminates the need
     for PIM-SM / RP / MSDP anywhere in the fabric. ASM is a
     compatibility posture, not the destination — see
     [Deployment postures](#deployment-postures).

## New configuration surface (additive, all components)

Two facts about the current architecture frame the config surface:

1. **Beacons are emitted only by retry-endpoint** ([retry-endpoint/beacon/beacon.go](../../../retry-endpoint/beacon/beacon.go)).
   They advertise NACK-reception address, port, tier and preference so
   shard-listeners know where to send retransmission requests. Beacons
   are **not** a data-plane source-discovery mechanism. shard-proxy does
   not emit beacons.
2. **Data-plane source identity flows exclusively through
   shard-manifest** (BRC-137 `Flags.SourcesValid` payload). That is the
   only mechanism a receiver uses to learn which proxy IPs to put in its
   data-group `(S,G)` joins.

Each control group has a different authoritative emitter, so SSM joins
need a **per-control-group bootstrap source list**: the beacon group's
sources are the retry-endpoint pods, the manifest group's sources are
the shard-manifest pods, the subtree-announce group's sources are
whatever component emits to it.

```yaml
multicast:
  sourceMode: asm # asm | ssm   (default: asm)
  scope: site # site | global (intra-domain | inter-domain)

  # SENDER-side. Set on any component that emits multicast.
  # bindSource is the stable IPv6 the kernel binds for egress; it MUST
  # be distinct per replica and never aliased.
  bindSource: ""

  # RECEIVER-side. Used by shard-listener and retry-endpoint.
  sources:
    # Data-plane source discovery. Currently manifest-only; beacons do
    # not carry data-plane source identity. Listed as a list for
    # forward compatibility.
    consume: [manifest]
    refresh: 30s

    # Bootstrap source lists for control groups. Each control group
    # joins SSM against its own list. Entries may be IPv6 literals OR
    # DNS names; names are resolved at startup and re-resolved every
    # `refresh`. The union of currently-resolved AAAA records across
    # entries forms the active bootstrap set. Without a list for a
    # given control group, that group falls back to ASM (data-plane-SSM
    # posture — see Deployment postures).
    bootstrap:
      manifest: [] # shard-manifest pods (e.g. "shard-manifest-headless.svc.cluster.local")
      beacon: [] # retry-endpoint pods (e.g. "retry-endpoint-headless.svc.cluster.local")
      subtreeAnnounce: [] # subtree-announce emitter pods

    # Lab/CI escape hatch: pre-declared DATA-plane source list. Use only
    # for development; production must use manifest discovery.
    # Helm validation fails closed when sourceMode=ssm AND
    # consume=[] AND len(static) > 16.
    static: []
```

Notes:

- There is no `advertise:` block. Whether a component emits beacons or
  manifests is a property of the component, not a runtime toggle:
  retry-endpoint always emits beacons; shard-manifest always emits
  manifests. SSM only adds `bindSource` for those emitters.
- The same block ships in `shard-proxy-helm`, `shard-listener-helm`,
  `retry-endpoint-helm`, `subtx-generator-helm`, `shard-manifest-helm`.
  Per the single-mode-per-deployment decision, `sourceMode` applies
  uniformly to every group the component opens.

Per-role expected use:

- **shard-proxy** — data-plane sender. Sets `bindSource`. Does not
  advertise. Does not consume.
- **subtx-generator** (direct mode) — data-plane sender. Same shape as
  shard-proxy.
- **shard-manifest** — control-plane authority. Sets `bindSource`,
  emits BRC-137 manifests (built-in behavior). Its `bindSource` is
  what receivers list in `bootstrap.manifest`.
- **retry-endpoint** — NACK responder. Sets `bindSource`, emits
  beacons (built-in behavior — these are the NACK-discovery beacons,
  not data-plane source advertisements). Its `bindSource` is what
  receivers list in `bootstrap.beacon`. Also **consumes** manifest to
  learn proxy source IPs for its data-plane SSM joins, so cache
  accepts every legitimate publisher.
- **shard-listener** — receiver only. Sets `sources.consume:
  [manifest]` for data-plane source learning, and
  `bootstrap.{beacon,manifest,subtreeAnnounce}` for the SSM joins to
  each respective control group. Does not advertise.

## Addressing

The address space is determined by `(sourceMode, scope)`. Global-scope ASM
is removed per RFC 8815 — inter-domain delivery is SSM-only.

| Mode | Site scope (intra-domain) | Global scope (inter-domain)                              |
| ---- | ------------------------- | -------------------------------------------------------- |
| ASM  | `FF05::B:idx`             | **Not supported** — RFC 8815 deprecates inter-domain ASM |
| SSM  | `FF35::B:idx`             | `FF3E::B:idx`                                            |

`FF3x::/32` is the RFC 4607 IPv6 SSM range (flags P=1, T=1). Group-ID
`0x000B` and the shard-index field are preserved — the only change is the
high 32 bits. A single helper
`engine.Addr(groupIdx, port, mode, scope)` centralizes this; today it
lives in the shard engine (used by
[shard-proxy/forwarder/forwarder.go:853](../../../shard-proxy/forwarder/forwarder.go#L853)
inside the `openEgressSocket` helper, and at the listener join sites).

[BRC-137](../brc-137-shard-manifest.md) already mandates that auto-config
consumers select the SSM prefix from `Flags.SourceModeSSM`; the BRC-129
side of the same contract is the table above.

## Required BRC amendments (out of scope here)

This plan does not edit any BRC. Two amendments are required as
follow-up BRC-cleanup work, captured here for cross-reference:

**[BRC-129](../brc-129-multicast-addressing.md) (multicast addressing)**

1. Split the addressing section into explicit **ASM** and **SSM**
   subsections instead of presenting a single ASM-only address table.
2. Document the RFC 8815 deprecation of inter-domain ASM and remove
   `FF0E::B:idx` from the global-scope row.
3. Declare **inter-domain operation is SSM-only**, with `FF3E::B:idx` as
   the sole global-scope address.
4. Cross-reference `Flags.SourceModeSSM` in BRC-137 so the auto-config
   consumer rules are reachable from BRC-129.
5. Add the `(S,G)` notational convention for receiver-side joins so the
   address table reads cleanly in SSM mode.

**[BRC-137](../brc-137-shard-manifest.md) (shard manifest)**

The data-plane wire format (`Flags.SourceModeSSM`, `Flags.SourcesValid`,
sources payload) is already final and does not need amendment. One
control-plane clause does:

1. Relax "the beacon group itself remains ASM regardless" to "MAY be
   SSM when the consumer has a bootstrap source list configured", so
   [Posture C](#posture-c--ssm-intra-domain-recommended) is
   spec-conformant and the fabric can drop PIM-SM entirely.

## Per-component changes

### Receivers — every join site needs a branched syscall

Receiver components (shard-listener, retry-endpoint) **consume** source
identity. Each join site picks its source list from exactly one place:
data groups from `sources.consume` (manifest-derived), control groups
from the matching `sources.bootstrap.<group>` entry.

| Component                       | File                                                                                                                   | Change                                                                                                                                                                                                                                                                                                  |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| shard-listener data plane       | [shard-listener/listener/listener.go:178-191](../../../shard-listener/listener/listener.go#L178-L191)                  | When SSM: loop over manifest-learned proxy sources, call `setsockopt(IPPROTO_IPV6, MCAST_JOIN_SOURCE_GROUP, group_source_req{...})` per (S,G). Keep `IPV6_JOIN_GROUP` path for ASM. Sources come only from manifest — beacons do not carry data-plane source identity. |
| shard-listener beacon           | [shard-listener/discovery/beacon.go:48](../../../shard-listener/discovery/beacon.go#L48)                               | Replace `net.ListenMulticastUDP` (ASM-only) with raw socket + branched join. The beacon group carries retry-endpoint NACK-discovery ADVERTs; under Posture C it joins SSM with `sources.bootstrap.beacon` (the retry-endpoint pods). Under Posture B it joins ASM. |
| shard-listener subtree announce | [shard-listener/discovery/subtree_announce.go:152-171](../../../shard-listener/discovery/subtree_announce.go#L152-L171) | Same branch as beacon. Under Posture C joins SSM with `sources.bootstrap.subtreeAnnounce`; under Posture B joins ASM.                                                                                                                                                |
| retry-endpoint data plane       | [retry-endpoint/ingress/ingress.go:87-102](../../../retry-endpoint/ingress/ingress.go#L87-L102)                        | Same branch as shard-listener data plane. Joins SSM with manifest-learned proxy sources; cache must accept every legitimate publisher.                                                                                                                              |

Helper to introduce: `netjoin.Join(fd, group, ifaceIdx, sources []netip.Addr)`
in `shard-common` so the four sites share one branched implementation.
`MCAST_JOIN_SOURCE_GROUP` (RFC 3678, protocol-independent) is preferred over
`IPV6_JOIN_SOURCE_GROUP` because it takes `group_source_req` with
`sockaddr_storage` — works for v4/v6 uniformly and matches the
`golang.org/x/sys/unix` constants already in use.

Dynamic membership for data-plane joins: when the manifest source set
changes, listeners maintain a `(source, group) → joined` set, issue
`MCAST_JOIN_SOURCE_GROUP` for new sources, `MCAST_LEAVE_SOURCE_GROUP`
for departed sources. Source-set changes must be rate-limited (a flapping
publisher should not thrash the kernel mfib). Bootstrap source lists for
control groups change only on DNS re-resolution and follow the same
diff-and-rate-limit pattern.

### Senders — must publish a stable, known source IP

Three components emit multicast today: shard-proxy (data plane),
retry-endpoint (NACK-discovery beacons), shard-manifest (manifests).
subtx-generator gains a direct-multicast mode that also makes it a
data-plane emitter. Under SSM each gets a distinct `bindSource`; the
emission paths themselves don't change.

| Component       | Role                                                | File                                                                                                                                                                                                  | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| shard-proxy     | data-plane sender                                   | [shard-proxy/forwarder/forwarder.go:204](../../../shard-proxy/forwarder/forwarder.go#L204) (`OpenTargets`) → [forwarder.go:853](../../../shard-proxy/forwarder/forwarder.go#L853) (`openEgressSocket`) | Required `bindSource` when `sourceMode=ssm`: in `openEgressSocket` (alongside the existing `IPV6_MULTICAST_IF` setsockopt) bind the egress socket to that IPv6 (`syscall.Bind` before send) so SSM receivers can pre-declare it. Each proxy replica uses a **distinct** `bindSource` (preserves HashKey per-flow semantics; required by PIM-SSM RPF — anycast sources break SSM). For single-identity deployments use VRRP active-standby, not anycast. Without `bindSource`, source IP is whatever the kernel picks — fine for ASM, broken for SSM. shard-proxy emits no beacons and authors no manifests; its identity propagates through shard-manifest's `Flags.SourcesValid` payload. |
| subtx-generator | data-plane sender (direct mode only)                | [subtx-generator/internal/sender/sender.go:189](../../../subtx-generator/internal/sender/sender.go#L189)                                                                                              | Add a new `mode: direct-multicast` alongside today's unicast-to-proxy. In direct mode the generator derives the group from TxID (same `engine.Addr` helper) and emits via a socket bound to `multicast.bindSource`. Unicast mode remains the default. Direct-mode generators must register their `bindSource` with shard-manifest (same path proxies use) so receivers learn the source via manifest.                                                                                                                                                              |
| retry-endpoint  | NACK responder; emits NACK-discovery beacons        | [retry-endpoint/beacon/beacon.go](../../../retry-endpoint/beacon/beacon.go)                                                                                                                           | Existing beacon emit path. ADVERTs carry NACK address/port/tier/preference for listener-side NACK routing; they do **not** carry data-plane source identity. Under SSM, the beacon socket binds `multicast.bindSource` so listeners can join the beacon group as `(S,G)` with the retry-endpoint pod IPs in `sources.bootstrap.beacon`. Each retry-endpoint replica uses a distinct `bindSource`.                                                                                                                                                                                                            |
| shard-manifest  | control-plane authority; emits BRC-137 manifests    | manifest publish path                                                                                                                                                                                 | Implement [BRC-137](../brc-137-shard-manifest.md)'s already-specified `Flags.SourceModeSSM` (bit 3), `Flags.SourcesValid` (bit 4), and `SourceCount × 16`-byte sources payload in the shard-manifest Go encode/decode path. The payload enumerates the union of every live data-plane publisher's `bindSource` (proxies + direct-mode generators); shard-manifest learns this set from its existing publisher-tracking mechanism (K8s pod watch, operator inventory, or registration RPC — out of scope here). The shard-manifest pod's own `bindSource` is what receivers list in `sources.bootstrap.manifest`. Wire format is locked — no further BRC change needed. |

Senders perform no SSM-specific data-plane syscall — SSM is purely a
receiver-side filter plus a network-fabric routing optimization. The
sender-side requirement is just identity stability (`bindSource`); the
egress send itself is unchanged.

## Infra / network fabric

Out-of-tree but must be flagged in `multicast-kube-infra` README. Required
config depends on the chosen [deployment posture](#deployment-postures).

- **PIM-SSM** must be enabled on the fabric router for the FF3x::/32 range
  under Postures B, C, and D. PIM-SSM has no RP, no shared tree — simpler
  and faster convergence than PIM-SM, and the only PIM mode permitted by
  RFC 8815 for inter-domain delivery.
- **PIM-SM with an RP** is required **only under Postures A and B** (for
  the ASM beacon / manifest / subtree-announce groups). Posture C removes
  this requirement entirely by joining control groups as SSM with a
  bootstrap source list. Deployments targeting Posture C can skip RP
  provisioning altogether.
- **MLDv2** is required on the L2 segment under Postures B, C, and D
  (MLDv1 cannot carry source lists). Most modern switches enable MLDv2
  snooping by default; document a check.
- For Multus macvlan pods: the macvlan interface inherits MLDv2 from the
  host kernel — no extra CNI config needed. Worth a smoke-test note.

## Source discovery

Source identities feed two distinct join surfaces:

| Surface                                                                 | How sources are learned                                                                                                     |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Data groups** (shard groups: TxID-derived `FF35::B:idx`)              | shard-manifest's `Flags.SourcesValid` payload only. Beacons do not carry data-plane source identity.                        |
| **Control groups** (beacon `:FFFD`, manifest, subtree-announce `:FFFC`) | per-control-group bootstrap source list (DNS or IP), one entry under `sources.bootstrap.{beacon,manifest,subtreeAnnounce}`. |

### Data-plane discovery — manifest only

shard-manifest publishes BRC-137 manifests that enumerate (a) the
authoritative shard-group layout and (b) the union of every live
data-plane publisher's `bindSource` (proxies + direct-mode generators).
Receivers set `sources.consume: [manifest]` and pull at startup +
`sources.refresh`. The `(S,G)` join set for each data group is the
manifest's source union, filtered to publishers actually mapped to that
group.

[BRC-137](../brc-137-shard-manifest.md) already specifies the wire
format (`Flags.SourceModeSSM` + `Flags.SourcesValid` + sources payload)
and the consumer rules. The shard-manifest Go implementation needs to
catch up to the BRC; that's the only blocker on manifest-based
data-plane discovery. The `consume` field is a list for forward
compatibility but `manifest` is the only valid value today.

How shard-manifest itself learns the publisher set (K8s pod watch,
operator inventory, registration RPC) is shard-manifest's internal
concern and out of scope here. Data-plane senders just bind a stable
`bindSource`; shard-manifest discovers them through its own mechanism
and republishes the union in the manifest payload.

### Control-group discovery — per-group bootstrap

Each control group has a different authoritative emitter, so each gets
its own bootstrap source list:

- `sources.bootstrap.beacon` — the **retry-endpoint** pod IPs (or a
  headless Service fronting them). Used to SSM-join the beacon group,
  which carries retry-endpoint NACK-discovery ADVERTs that listeners
  consume to know where to send NACKs.
- `sources.bootstrap.manifest` — the **shard-manifest** pod IPs.
  Used to SSM-join the manifest group itself.
- `sources.bootstrap.subtreeAnnounce` — the IPs of whichever component
  emits BRC-127 subtree-announce frames. Used to SSM-join the
  subtree-announce group.

Each entry may be an IPv6 literal or a DNS name. Names are resolved at
startup and re-resolved every `sources.refresh`; the union of currently-
resolved AAAA records becomes the active source set for that group. The
expected production form is one headless-Service name per control group,
which lets the underlying pods reschedule freely without bootstrap drift.

Resolution semantics (per list):

- **Startup**: if `sourceMode=ssm` and a posture demands SSM joins for
  this control group, at least one entry MUST resolve to ≥ 1 AAAA
  record or the process fails to start. Fail-closed; do not silently
  fall back to ASM.
- **Refresh**: failures are non-fatal. The last good resolved set is
  retained until the next successful refresh. A
  `bootstrap_resolve_errors_total{group=...}` counter increments so
  transient DNS issues are visible without breaking joins.
- **Diff**: when a refresh produces a different set, issue
  `MCAST_JOIN_SOURCE_GROUP` for new IPs and `MCAST_LEAVE_SOURCE_GROUP`
  for departing IPs, rate-limited per the
  [scale prerequisites](#hard-prerequisites-for-the-target-scale).
- **TTL**: respect resolver TTLs only up to the configured `refresh`
  ceiling — a stale TTL longer than `refresh` would defeat the
  re-resolve loop.

Each list stays small (≤ 16 resolved entries) because it enumerates only
the replicas of one specific control-plane component, not the hundreds
of data-plane publishers.

### Static — lab and CI only

`sources.static` is a pre-declared data-plane source list. Helm
validation fails closed when `sourceMode=ssm AND consume=[] AND
len(static) > 16`. Do not use in production at the target scale —
every proxy add/remove is a Helm rollout across every listener.

## Deployment postures

Four supported postures, each a self-consistent network and config
state. They are not a sequence — a fresh deployment picks whichever
posture matches its fabric capabilities and reach requirements. **C is
the recommended target for new intra-domain deployments**; D is the
recommended target for inter-domain.

### Posture A — pure ASM (legacy / compatibility)

- **Sender mode**: ASM. Source IP is whatever the kernel selects on the
  egress interface.
- **Receiver mode**: `IPV6_JOIN_GROUP` `(*,G)`.
- **Addresses in use**: `FF05::B:*` (site scope ASM). Global-scope ASM
  (`FF0E::B:*`) is removed per RFC 8815.
- **Fabric**: PIM-SM with RP, MLDv1 or v2 acceptable.
- **Scale ceiling**: comfortably tens of publishers; RP becomes the
  control-plane bottleneck above that.
- **When to pick**: only for environments that cannot run PIM-SSM at
  all. Not recommended for new work.

### Posture B — data-plane SSM, ASM control groups

- **Sender mode**: data-plane senders (shard-proxy, subtx-generator
  direct mode) bind `bindSource` and emit on `FF35::B:*`. Control-plane
  emitters (retry-endpoint beacons, shard-manifest, subtree-announce
  source) continue on `FF05::B:FFFD` / `FF05::B:FFFE` / `FF05::B:FFFC`.
- **Receiver mode**: data groups joined `(S,G)` from manifest-learned
  proxy sources; control groups (beacon, manifest, subtree-announce)
  still `(*,G)` ASM with the chicken-and-egg carve-out.
- **Fabric**: PIM-SSM **and** PIM-SM (RP) coexisting. MLDv2 mandatory.
- **Scale ceiling**: hundreds of data-plane publishers (the
  [hard prerequisites](#hard-prerequisites-for-the-target-scale) apply
  here). Control plane is still RP-bottlenecked, but control-plane
  publisher count is small.
- **When to pick**: environments where the fabric can do PIM-SSM for
  data groups but cannot drop PIM-SM/RP entirely — e.g. shared fabrics
  with other ASM tenants.

### Posture C — SSM intra-domain (recommended)

- **Sender mode**: every multicast emitter — data plane and control
  plane — binds a distinct `bindSource` and emits on `FF35::B:*`. No
  ASM emission anywhere.
- **Receiver mode**: every join site is `(S,G)`. Each control group
  uses its matching `sources.bootstrap.<group>` (DNS or IP); data
  groups use manifest-learned proxy sources.
- **Fabric**: PIM-SSM only. **No RP. No PIM-SM. No MSDP.** The primary
  operational win — the fabric configuration shrinks to "enable PIM-SSM
  on the FF3x::/32 range, enforce MLDv2" and the RP failure modes
  disappear.
- **Scale ceiling**: thousands of data-plane publishers. Control-plane
  scale is capped by the bootstrap list size (≤ 16 by convention),
  which is comfortable because it only enumerates shard-manifest
  replicas.
- **When to pick**: default for new intra-domain deployments.

### Posture D — SSM inter-domain

- **Sender mode**: same as C but with `scope: global` → `FF3E::B:*`.
- **Receiver mode**: same as C.
- **Fabric**: PIM-SSM peering across administrative domains, with
  source-list signaling preserved through inter-domain MLDv2 / IGMPv3.
  No MSDP (MSDP is an ASM construct and is irrelevant under SSM — the
  RFC 8815 design intent).
- **Scale ceiling**: subject to inter-domain RPF and fabric mfib budget
  at each peering point. Document the calculation per peering.
- **When to pick**: deployments that need cross-domain delivery. The
  only RFC 8815-compliant inter-domain configuration.

### Posture comparison

| Posture | Publisher count budget                             | Fabric needs               | Inter-domain               |
| ------- | -------------------------------------------------- | -------------------------- | -------------------------- |
| A       | ~tens                                              | PIM-SM + RP                | Not supported (RFC 8815)   |
| B       | ~hundreds (data) + ~tens (control)                 | PIM-SM (RP) **and** PIM-SSM | Data plane only            |
| C       | ~thousands (data) + bootstrap-list-bounded control | PIM-SSM only               | Intra-domain only          |
| D       | per-peering capacity                               | PIM-SSM + inter-domain peering | Yes                    |

## Scale viability

Target deployment: hundreds of publishers (shard-proxy / subtx-generator
instances), hundreds of listeners (shard-listener, retry-endpoint), tens of
shard groups. Worked example used below: **200 publishers × 200 listeners ×
64 shard groups**.

### Patterns that **do** scale

The shape of the design at the target deployment size. These are the
deliberate choices that let "hundreds of each" stay tractable; they
should be preserved as the system grows further.

- **Distinct source IP per publisher.** Linear in publisher count: no
  coordination, no consensus, no shared state. Adding the 201st proxy is
  the same operation as adding the 2nd. Preserves the per-publisher
  HashKey semantics already used for gap detection.
- **PIM-SSM in the fabric.** No RP, no shared tree, no MSDP. `(S,G)`
  state grows as `N_sources × N_groups` on routers along the SPT, but
  the control-plane churn is dramatically lower than PIM-SM. The fabric
  configuration shrinks to "enable PIM-SSM on the FF3x::/32 range,
  enforce MLDv2" — small and operationally boring.
- **SSM control groups with per-group bootstrap source lists.** Joining
  the beacon / manifest / subtree-announce groups as `(S,G)` against
  small lists (≤ 16 resolved entries each) eliminates the need for
  ASM / PIM-SM anywhere. Each list enumerates only the replicas of one
  specific control-plane component (retry-endpoints for beacon,
  shard-manifest pods for manifest, etc.) — sizes are independent of
  data-plane fleet size.
- **DNS-driven bootstrap.** Each bootstrap list accepts DNS names that
  resolve to AAAA records (typically a headless Service per
  control-plane component). Pod reschedules don't require operator
  action — the re-resolve loop picks up the new IPs and diffs the
  active source set under the same rate limit as manifest-driven joins.
- **Manifest-based authoritative data-plane discovery.** BRC-137's
  `Flags.SourcesValid` payload provides a single authoritative
  source-set view with bounded refresh latency. Manifests scale better
  than per-publisher gossip under fleet churn because the receiver does
  one pull rather than tracking N independent advertisements. This is
  the only data-plane source-discovery mechanism in the design.
- **Per-socket aggregated `(S,G)` filters.** A single listener socket
  can hold thousands of `(S,G)` filter entries once `mld_max_msf` is
  raised (see prerequisites). No need for per-source sockets and no
  per-publisher fd budget. MLDv2 reports are source-aggregated by the
  kernel.
- **Deterministic per-pod IPv6 via Multus + Whereabouts.** Pod ordinal
  drives the IPv6 suffix, making `bindSource` stable across restarts
  and reschedules without operator intervention. Scales to as many pods
  as the IPAM range allows.
- **StatefulSet + per-pod ordinal identity** for shard-proxy and
  shard-manifest. The same ordinal that drives the IPv6 also names the
  pod, so logs / metrics / debug surfaces all converge on one identity
  per publisher. No service-discovery indirection needed for source-IP
  attribution.
- **Cardinality-bounded metrics.** Bucket sources by `group_role`,
  region, or zone label on receiver-side metrics; keep raw-IP labels
  only on the single-source sender side and on narrowly-scoped debug
  metrics. Prometheus stays sane as the fleet grows.
- **VRRP/keepalived active-standby for single-identity deployments.**
  Where a deployment genuinely needs one stable `S` value (e.g. for a
  bootstrap entry or a constrained client), one proxy holds a floating
  IP and others are warm spares. Failover, not load distribution, but
  it composes cleanly with SSM RPF — only one publisher emits at a
  time, so RPF picks the obvious path.

### Hard prerequisites for the target scale

These are not optional at "hundreds of each"; they must land before any
production SSM rollout.

1. **Raise `mld_max_msf`.** The Linux default of 64 source filters per
   socket is below `N_publishers`. With 200 proxies a fresh
   `MCAST_JOIN_SOURCE_GROUP` returns `ENOBUFS`. Document the required
   sysctl in `multicast-kube-infra` and set it via a node-level DaemonSet
   or kernel-args bootstrap:

   ```ini
   net.ipv6.mld_max_msf = 1024
   ```

   Pick the value as `≥ 2 × N_publishers` to leave headroom for fleet
   growth and transient overlap during proxy rollouts.

2. **Deterministic per-proxy IPv6 via Multus.** `bindSource` is meaningless
   if pod IPs change on restart. Required: Multus + Whereabouts (or static
   IPAM) on the macvlan secondary interface, with IP allocation pinned by
   pod ordinal (StatefulSet). `hostNetwork: true` is the fallback when
   Multus is unavailable but couples proxy identity to node identity. A
   normal Kubernetes `Service` does **not** solve this — it allocates a
   unicast VIP, not an interface address.

3. **Fabric mfib sizing.** `N_publishers × N_groups` = 200 × 64 = 12.8k
   (S,G) entries per router on the distribution tree. Modern silicon
   handles 32k–128k, so within budget — but it must be sized deliberately,
   not assumed. Document the calculation in the network design.

4. **Join-rate limiting in `netjoin`.** Cold-start at target scale is
   `N_listeners × N_publishers × N_groups` ≈ 2.5M individual
   `MCAST_JOIN_SOURCE_GROUP` calls. Without rate limiting the resulting
   MLDv2 report burst can overwhelm the upstream querier and trigger
   report drops (silently breaking joins). Required in the helper:

   - Per-listener join rate ceiling (default ~1k joins/sec, configurable).
   - Startup jitter (random delay 0–500ms) so 200 pods don't issue MLDv2
     reports in the same 10ms window.

5. **Manifest discovery for production.** `sources.static` is
   unmaintainable past ~10 publishers. At the target scale `manifest`
   is the only viable data-plane discovery mechanism. The Helm charts
   should `fail-closed` when `sourceMode=ssm && consume=[] &&
   len(sources.static) > 16`.

### Operational concerns at scale (monitor, don't block)

- **Beacon group churn.** retry-endpoints emit NACK-discovery ADVERTs
  at the configured interval (default 60s per
  [retry-endpoint/beacon/beacon.go](../../../retry-endpoint/beacon/beacon.go)).
  Even with dozens of retry-endpoints the beacon group sees only single-
  digit pps — far below data-plane rates. Worth instrumenting for storm
  detection but not a per-publisher scale concern (the beacon source
  set is the retry-endpoint count, not the data-plane fleet size).
- **MLDv2 general-query response load.** Every general query triggers each
  listener to dump its full source-filter state. With ~12k (S,G) entries
  per listener and a default 125 s query interval, this is manageable —
  but aggressive tuning of the query interval is dangerous and should be
  avoided.
- **Retry-endpoint cache footprint.** Scales linearly with
  `N_publishers`. Already implicitly true under ASM with multi-publisher
  topologies; SSM just makes the floor explicit. Size the cache for
  `N_publishers × N_shards × cache_depth` and add an eviction metric.
- **Discovery convergence at scale-up.** A new data-plane publisher takes
  one manifest refresh cycle plus the listener's join-rate budget to be
  fully reachable. At a 30 s `sources.refresh` and 1k joins/sec limit,
  the slowest listener has the new source live within `refresh + ~1 s`.
  Acceptable for elastic scale; document the bound. Tighten `refresh`
  if faster convergence is required.

### Patterns that **do not** scale and must be rejected

- **Anycast / ECMP shared source IP across proxies.** Breaks PIM-SSM RPF
  — the router picks one path per `(S,G)`, packets from the losing
  proxies are dropped at the first L3 hop. This was suggested in earlier
  drafts of this plan and is incorrect; do not use.
- **A unicast L4 load balancer in front of multicast egress.** Unicast
  LBs do not relay multicast emission, and even if rebuilt to do so they
  become a single PPS-bottleneck and SPOF. The right primitive for
  "stable single source identity" is VRRP active-standby, accepting that
  it is failover and not load distribution.
- **Per-source labels in Prometheus.** Cardinality explodes as
  `N_publishers × N_listeners`. On receivers, bucket sources by role or
  region label, not raw IP. Raw-IP labels are tolerable only on the
  sender (where cardinality is 1) and on a narrow set of debug-only
  metrics.

## Metrics & observability

Add to each receiver:

- `multicast_ssm_sources_joined{group_role=...}` — gauge of currently-held
  (S,G) filters. Bucketed by group role (data / control / announce), **not**
  per raw group — keeps cardinality bounded as `N_groups` grows.
- `multicast_ssm_filter_capacity_used` — ratio of held filters to the
  effective `mld_max_msf` limit. Alert when > 0.8 — that's the early
  signal that the sysctl needs raising before the next scale-up.
- `multicast_ssm_join_errors_total{op=join|leave,reason=...}` — counter;
  `reason=enobufs` is the specific symptom of (1) above.
- `multicast_ssm_join_rate_limited_total{op=...}` — counter incremented
  by the `netjoin` rate limiter so cold-start storms are visible.
- `multicast_ssm_unexpected_source_total{group_role=...}` — frames
  received from a source IP not in the configured/learned set. Should be
  zero on a correctly-configured PIM-SSM fabric; non-zero means the
  network is falling back to ASM delivery (or a misconfigured anycast
  source is leaking through).
- `multicast_ssm_discovery_publishers_known` — gauge of distinct
  data-plane publishers learned from the manifest. Should match the
  operator's expected fleet size; drift indicates manifest staleness or
  shard-manifest discovery loss.
- `multicast_ssm_bootstrap_sources_resolved{group=beacon|manifest|subtree_announce}` —
  gauge of currently-resolved AAAA records per control-group bootstrap
  list. Should equal the replica count of the relevant control-plane
  component (retry-endpoint count for `beacon`, shard-manifest replica
  count for `manifest`, etc.). Alert on collapse to zero (DNS down
  during refresh); use the matching `bootstrap_resolve_errors_total`
  counter for transient resolution failures.
- `multicast_ssm_posture` — info-style gauge labelled with the
  operator's declared posture (`A`/`B`/`C`/`D` per
  [Deployment postures](#deployment-postures)), so dashboards can
  correlate prerequisite-check failures with the intended posture.

Add to each sender (any component with a non-empty `bindSource`):

- `multicast_ssm_bind_source` — info-style metric reporting the actual
  bound IPv6, so dashboards can confirm distinct values across replicas
  of the same component.
