# Source-Specific Multicast (SSM) Support Plan

- Status: Proposal
- Scope: All multicast-aware components and infra
- Default behavior unchanged: ASM remains the default; SSM is opt-in.

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
- **Non-goals**: No change to frame format, NACK protocol, HashKey computation,
  or shard derivation. SSM is a deployment/transport mode, not a protocol
  revision.
- **Design decisions** (resolved from open questions):
  1. **Single mode per deployment.** `sourceMode` applies to every group a
     component touches; no per-group override. The beacon group remains ASM by
     special-case (see Source discovery) even when `sourceMode=ssm`.
  2. **Distinct source IP per shard-proxy.** Preserves the per-publisher
     HashKey semantics already in the listener/retry-endpoint flow tracking,
     and is the only model PIM-SSM tolerates cleanly. SSM RPF picks one path
     per `S`, so anycast/ECMP-style "shared source IP" deployments are
     **not supported** — packets from the non-RPF proxies are dropped at the
     first L3 hop. If a deployment requires a single stable source identity
     (e.g. for a constrained `sources.static` list), use VRRP/keepalived
     active-standby: one proxy is live at a time, holding a floating IP, and
     others are warm spares. That is failover, not load distribution; horizontal
     scale-out means more distinct `S` values, period.
  3. **subtx-generator gains an SSM-aware direct-multicast mode.** Its existing
     unicast-to-proxy path stays the default; a new direct-emit mode lets the
     `10gb-direct-testing` harness exercise the SSM data path without a proxy
     in the loop.

## New configuration surface (additive, all components)

```yaml
multicast:
  sourceMode: asm # asm | ssm   (default: asm)
  scope: site # site | global  (selects FF05/FF0E or FF35/FF3E)
  sources: # only consulted when sourceMode=ssm
    static: [] # ["fd20::1", "fd20::2"]  pre-declared source IPv6s
    discover: beacon # off | beacon | manifest  dynamic source learning
    refresh: 30s
```

The same block ships in `shard-proxy-helm`, `shard-listener-helm`,
`retry-endpoint-helm`, `subtx-generator-helm`, `shard-manifest-helm`. Per the
single-mode-per-deployment decision, the block applies uniformly to every
group the component opens.

Sender-side components (`shard-proxy`, `subtx-generator` in direct mode) also
take `multicast.bindSource: <ipv6>` so each sender publishes a stable, known
source IP. Each proxy replica must hold a distinct `bindSource`; horizontal
scale-out adds publishers, never aliases an existing one.

## Addressing (BRC-129 amendment)

Add a section "SSM addressing". When `sourceMode=ssm` the prefix changes:

| Mode        | Site scope    | Global scope  |
| ----------- | ------------- | ------------- |
| ASM (today) | `FF05::B:idx` | `FF0E::B:idx` |
| SSM (new)   | `FF35::B:idx` | `FF3E::B:idx` |

`FF3x::/32` is the RFC 4607 IPv6 SSM range (flags P=1, T=1). Group-ID `0x000B`
and the shard-index field are preserved — the only change is the high 32 bits. A
single helper `engine.Addr(groupIdx, port, mode, scope)` centralizes this; today
it lives in the shard engine (used by
[shard-proxy/forwarder/forwarder.go:233](../../../shard-proxy/forwarder/forwarder.go#L233)
and the listener join sites).

## Per-component changes

### Receivers — every join site needs a branched syscall

| Component                       | File                                                                                                                 | Change                                                                                                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| shard-listener data plane       | [shard-listener/listener/listener.go:178-191](../../../shard-listener/listener/listener.go#L178-L191)                   | When SSM: loop over `sources`, call `setsockopt(IPPROTO_IPV6, MCAST_JOIN_SOURCE_GROUP, group_source_req{...})` per (S,G). Keep `IPV6_JOIN_GROUP` path for ASM.             |
| shard-listener beacon           | [shard-listener/discovery/beacon.go:48](../../../shard-listener/discovery/beacon.go#L48)                                | Replace `net.ListenMulticastUDP` (ASM-only) with raw socket + branched join. The beacon group itself should remain ASM (chicken-and-egg: it's how sources are discovered). |
| shard-listener subtree announce | [shard-listener/discovery/subtree_announce.go:152-171](../../../shard-listener/discovery/subtree_announce.go#L152-L171) | Same branch as data plane; sources for the announce group come from manifest.                                                                                              |
| retry-endpoint                  | [retry-endpoint/ingress/ingress.go:87-102](../../../retry-endpoint/ingress/ingress.go#L87-L102)                         | Same branch. Needs the widest source list (cache must accept every legitimate publisher).                                                                                  |

Helper to introduce: `netjoin.Join(fd, group, ifaceIdx, sources []netip.Addr)`
in `shard-common` so the four sites share one branched implementation.
`MCAST_JOIN_SOURCE_GROUP` (RFC 3678, protocol-independent) is preferred over
`IPV6_JOIN_SOURCE_GROUP` because it takes `group_source_req` with
`sockaddr_storage` — works for v4/v6 uniformly and matches the
`golang.org/x/sys/unix` constants already in use.

Dynamic membership: when `discover=beacon|manifest`, listeners maintain a
`(source, group) → joined` set, issue `MCAST_JOIN_SOURCE_GROUP` for new sources,
`MCAST_LEAVE_SOURCE_GROUP` for sources whose advert TTL expires. Source-set
changes must be rate-limited (a flapping publisher should not thrash the kernel
mfib).

### Senders — must publish a stable, known source IP

| Component      | File                                                                                                             | Change                                                                                                                                                                                                                                      |
| -------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| shard-proxy      | [shard-proxy/forwarder/forwarder.go:210-245](../../../shard-proxy/forwarder/forwarder.go#L210-L245) (`OpenTargets`) | Required `bindSource` when `sourceMode=ssm`: bind the egress socket to that IPv6 (`syscall.Bind` before send) so SSM receivers can pre-declare it. Each proxy replica uses a **distinct** `bindSource` (preserves HashKey per-flow semantics; required by PIM-SSM RPF — anycast sources break SSM). For single-identity deployments use VRRP active-standby, not anycast. Without `bindSource`, source IP is whatever the kernel picks — fine for ASM, broken for SSM. |
| shard-proxy      | beacon emit path                                                                                                 | Must include the bound source IP in the ADVERT payload (already does this per the survey) and now also emit on the _configured_ mode/scope group.                                                                                                                                                                                                                                                                                                                |
| subtx-generator  | [subtx-generator/internal/sender/sender.go:189](../../../subtx-generator/internal/sender/sender.go#L189)            | Add a new `mode: direct-multicast` alongside today's unicast-to-proxy. In direct mode the generator derives the group from TxID (same `engine.Addr` helper) and emits via a socket bound to `multicast.bindSource`. Enables the `10gb-direct-testing` harness to exercise the SSM data path without a proxy. Unicast mode remains the default.                                                                                                                  |
| shard-manifest   | manifest publish                                                                                                 | Add `sourceAddresses[]` field to the [BRC-137](../brc-137-shard-manifest.md) manifest schema so listeners using `discover=manifest` can build their (S,G) join list without listening to the ASM beacon. The list contains every live publisher's distinct `bindSource`; for VRRP-fronted spares, only the currently active floating IP is listed.                                                                                                                  |

Senders themselves perform no SSM-specific syscall — SSM is purely a
receiver-side filter plus a network-fabric routing optimization.

## Infra / network fabric

Out-of-tree but must be flagged in `multicast-kube-infra` README:

- **PIM-SSM** must be enabled on the fabric router for the FF3x::/32 range.
  PIM-SSM has no RP, no shared tree — simpler and faster convergence than
  PIM-SM, which is one of the motivations for offering SSM.
- **MLDv2** is required on the L2 segment (MLDv1 cannot carry source lists).
  Most modern switches enable MLDv2 snooping by default; document a check.
- For Multus macvlan pods: the macvlan interface inherits MLDv2 from the host
  kernel — no extra CNI config needed. Worth a smoke-test note.

## Source discovery

Three modes. Operational suitability depends on publisher count — see
[Scale viability](#scale-viability) for the breakpoints.

1. **Beacon** (`discover=beacon`) — **production default.** Listener joins the
   ASM beacon group `FF05::B:FFFD`, learns source IPs from ADVERTs, then
   issues `MCAST_JOIN_SOURCE_GROUP` for the data groups. The beacon group
   remains ASM regardless of `sourceMode` (chicken-and-egg: it's how sources
   are discovered). Scales to hundreds of publishers because no operator
   action is required when the fleet changes shape.
2. **Manifest** (`discover=manifest`): pull source list from shard-manifest at
   startup + periodic refresh. Removes runtime dependency on the beacon
   for SSM bootstrap but adds a hard dependency on manifest reachability.
   Ship after `shard-manifest` is widely deployed.
3. **Static** (`sources.static`) — **lab and CI only.** Operator lists
   publisher IPs in `values.yaml`. Becomes unmaintainable past a handful of
   publishers (every proxy add/remove is a Helm rollout across every
   listener). Do not use in production at the target scale.

## Scale viability

Target deployment: hundreds of publishers (shard-proxy / subtx-generator
instances), hundreds of listeners (shard-listener, retry-endpoint), tens of
shard groups. Worked example used below: **200 publishers × 200 listeners ×
64 shard groups**.

### What scales cleanly

- **Distinct source IP per proxy** is linear in publisher count: no
  coordination, no consensus, no shared state. Adding the 201st proxy is the
  same operation as adding the 2nd.
- **PIM-SSM in the fabric.** No RP, no shared tree, no MSDP. The (S,G)
  state grows as `N_sources × N_groups` on routers along the SPT, but the
  control-plane churn is dramatically lower than PIM-SM at this size — a
  primary motivation for offering SSM at all.
- **Per-socket join model.** A single listener socket can hold thousands of
  (S,G) filter entries once kernel limits are raised (see below). No need
  for per-source sockets.

### Hard prerequisites for the target scale

These are not optional at "hundreds of each"; they must land before any
production SSM rollout.

1. **Raise `mld_max_msf`.** The Linux default of 64 source filters per
   socket is below `N_publishers`. With 200 proxies a fresh
   `MCAST_JOIN_SOURCE_GROUP` returns `ENOBUFS`. Document the required
   sysctl in `multicast-kube-infra` and set it via a node-level DaemonSet
   or kernel-args bootstrap:

   ```
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

5. **Dynamic discovery for production.** `sources.static` is
   unmaintainable past ~10 publishers. At the target scale only `beacon`
   or `manifest` discovery is viable. The Helm charts should `fail-closed`
   when `sourceMode=ssm && discover=off` and `len(sources.static) > 16`.

### Operational concerns at scale (monitor, don't block)

- **Beacon group churn.** With 200 publishers each ADVERTing at 1 Hz the
  ASM beacon group sees ~200 pps. Acceptable, but instrument it and add
  back-pressure so a runaway beacon storm cannot starve the data plane
  (e.g. drop the listener's beacon-socket-recv-buffer rather than the data
  socket under memory pressure).
- **MLDv2 general-query response load.** Every general query triggers each
  listener to dump its full source-filter state. With ~12k (S,G) entries
  per listener and a default 125 s query interval, this is manageable —
  but aggressive tuning of the query interval is dangerous and should be
  avoided.
- **Retry-endpoint cache footprint.** Scales linearly with
  `N_publishers`. Already implicitly true under ASM with multi-publisher
  topologies; SSM just makes the floor explicit. Size the cache for
  `N_publishers × N_shards × cache_depth` and add an eviction metric.
- **Discovery convergence at scale-up.** A new publisher takes one beacon
  cycle plus the listener's join-rate budget to be fully reachable. At
  200 listeners and a 1k joins/sec limit, the slowest listener has the
  new source live within ~1 s. Acceptable for elastic scale; document the
  bound.

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
  publishers learned via beacon/manifest. Should match the operator's
  expected fleet size; drift indicates discovery loss.

Add to each sender:

- `multicast_ssm_bind_source` — info-style metric reporting the actual
  bound IPv6, so dashboards can confirm distinct values across replicas.

## Migration path

1. Land the `netjoin` helper + `sourceMode=asm` no-op refactor first — zero
   behavior change, gives every component the branch point. Include
   join-rate limiting and startup jitter from day one so the helper is
   already scale-safe when SSM is enabled later.
2. Update [BRC-129](../brc-129-multicast-addressing.md) with the SSM
   addressing appendix.
3. Add the config surface to each Helm chart with `sourceMode: asm` default
   and the fail-closed validation on `static` source-list size.
4. Land the scale prerequisites: `mld_max_msf` sysctl DaemonSet, Multus
   deterministic IPAM for shard-proxy, fabric mfib sizing documented in
   `multicast-kube-infra`.
5. Ship shard-proxy `bindSource` and `static` source mode. Validate against
   a small lab fabric with PIM-SSM, distinct source IP per proxy. Do not
   promote `static` beyond lab use.
6. Ship `beacon` discovery — the production default. Scale-test against a
   fleet sized to the target deployment (200+ publishers, 200+ listeners)
   and verify the cold-start MLDv2 burst stays within the upstream
   querier's budget.
7. Ship optional `manifest` discovery once `shard-manifest` is widely
   deployed.
8. Add subtx-generator `direct-multicast` mode and wire it into
   `10gb-direct-testing`.
