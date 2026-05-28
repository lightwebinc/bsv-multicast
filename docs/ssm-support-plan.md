# Source-Specific Multicast (SSM) Support Plan

Status: Proposal Scope: All multicast-aware components and infra Default
behavior unchanged: ASM remains the default; SSM is opt-in.

## Background

The current design is pure Any-Source Multicast (ASM): receivers do
`IPV6_JOIN_GROUP` with no source filter; senders rely on kernel source-address
selection on the egress interface; HashKey-based gap detection uses the
_observed_ source IPv6 post-receive, so multi-publisher topologies already
create per-source flows implicitly. There is no source identity in the
addressing scheme ([BRC-129](brc-129-multicast-addressing.md)) and no source
list anywhere in the config surface.

## Goals / non-goals

- **Goal**: Add an opt-in `sourceMode: ssm` switch that, when enabled, (a) joins
  `(S,G)` instead of `(*,G)`, (b) uses the IPv6 SSM address range, (c) lets
  operators pre-declare source IPs or learn them dynamically. ASM remains the
  default.
- **Non-goals**: No change to frame format, NACK protocol, HashKey computation,
  or shard derivation. No change to subtx-generator (it unicasts to the proxy).
  SSM is a deployment/transport mode, not a protocol revision.

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
`retry-endpoint-helm`, `subtx-generator-helm` (no-op there),
`shard-manifest-helm`.

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
[shard-proxy/forwarder/forwarder.go:233](../../shard-proxy/forwarder/forwarder.go#L233)
and the listener join sites).

## Per-component changes

### Receivers — every join site needs a branched syscall

| Component                       | File                                                                                                                 | Change                                                                                                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| shard-listener data plane       | [shard-listener/listener/listener.go:178-191](../../shard-listener/listener/listener.go#L178-L191)                   | When SSM: loop over `sources`, call `setsockopt(IPPROTO_IPV6, MCAST_JOIN_SOURCE_GROUP, group_source_req{...})` per (S,G). Keep `IPV6_JOIN_GROUP` path for ASM.             |
| shard-listener beacon           | [shard-listener/discovery/beacon.go:48](../../shard-listener/discovery/beacon.go#L48)                                | Replace `net.ListenMulticastUDP` (ASM-only) with raw socket + branched join. The beacon group itself should remain ASM (chicken-and-egg: it's how sources are discovered). |
| shard-listener subtree announce | [shard-listener/discovery/subtree_announce.go:152-171](../../shard-listener/discovery/subtree_announce.go#L152-L171) | Same branch as data plane; sources for the announce group come from manifest.                                                                                              |
| retry-endpoint                  | [retry-endpoint/ingress/ingress.go:87-102](../../retry-endpoint/ingress/ingress.go#L87-L102)                         | Same branch. Needs the widest source list (cache must accept every legitimate publisher).                                                                                  |

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
| shard-proxy    | [shard-proxy/forwarder/forwarder.go:210-245](../../shard-proxy/forwarder/forwarder.go#L210-L245) (`OpenTargets`) | Optional `bindSource` config: when set, bind the egress socket to that IPv6 (`syscall.Bind` before send) so SSM receivers can pre-declare it. Without `bindSource`, source IP is whatever the kernel picks — fine for ASM, brittle for SSM. |
| shard-proxy    | beacon emit path                                                                                                 | Must include the bound source IP in the ADVERT payload (already does this per the survey) and now also emit on the _configured_ mode/scope group.                                                                                           |
| shard-manifest | manifest publish                                                                                                 | Add `sourceAddresses[]` field to the [BRC-137](brc-137-shard-manifest.md) manifest schema so listeners using `discover=manifest` can build their (S,G) join list without listening to the ASM beacon.                                       |

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

Three modes, in order of operational simplicity:

1. **Static** (`sources.static`): operator lists publisher IPs in `values.yaml`.
   Simplest, but every proxy add/remove is a Helm change.
2. **Beacon** (`discover=beacon`): listener joins the ASM beacon group
   `FF05::B:FFFD`, learns source IPs from ADVERTs, then issues
   `MCAST_JOIN_SOURCE_GROUP` for the data groups. Requires the beacon group to
   remain ASM regardless of `sourceMode` (called out above).
3. **Manifest** (`discover=manifest`): pull source list from shard-manifest at
   startup + refresh. Removes runtime dependency on the beacon for SSM bootstrap
   but adds a hard dependency on manifest reachability.

Recommend shipping `static` and `beacon`; defer `manifest` until shard-manifest
is widely deployed.

## Metrics & observability

Add to each receiver:

- `multicast_ssm_sources_joined{group=...}` — gauge
- `multicast_ssm_join_errors_total{op=join|leave,reason=...}` — counter
- `multicast_ssm_unexpected_source_total{group=...}` — frames received from a
  source IP not in the configured/learned set (should be zero on a
  correctly-configured PIM-SSM fabric; non-zero means the network is falling
  back to ASM delivery).

## Migration path

1. Land the `netjoin` helper + `sourceMode=asm` no-op refactor first — zero
   behavior change, gives every component the branch point.
2. Update [BRC-129](brc-129-multicast-addressing.md) with the SSM addressing
   appendix.
3. Add the config surface to each Helm chart with `sourceMode: asm` default.
4. Ship `static` source mode. Validate against a lab fabric with PIM-SSM.
5. Add `beacon` discovery, then optional `manifest` discovery.
6. Document the fabric prerequisite in `multicast-kube-infra`.

## Open questions

- **Single mode per deployment, or per-group?** A mixed deployment (data plane
  SSM, control plane ASM) is more flexible but doubles the config surface.
  Recommendation: single-mode-per-component for v1.
- **Anycast source IP across multiple shard-proxies, or distinct per-proxy?**
  Anycast lets listeners declare one source; distinct preserves the
  per-publisher HashKey semantics already in the code. Needs a deliberate call
  before binding to a design.
- **Should subtx-generator gain an SSM-aware direct-multicast mode** (it
  currently unicasts to the proxy)? Out of scope for this plan but a natural
  extension for the `10gb-direct-testing` harness.
