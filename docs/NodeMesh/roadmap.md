# Node Mesh — Roadmap & Specification

Status: **active** (Phase 0 implemented). Owner: infra.

## Goal

Demonstrate multiple **independently deployed, fully self-contained nodes** that
form a multicast tunnel mesh (full or partial) between them. Each node:

- accepts transaction **ingress from the internet** (anycast/BGP → proxy), and
- carries **bidirectional consumer tunnels** to consumers such as miners that
  both send transactions and listen to shard groups,

so every node is full-duplex: proxy **egress** to the fabric and listener
**ingress** from the fabric, over `ip6gre`.

Scaling target: from a **single-host collapsed node** up to a datacenter
domain, with a per-site horizontal scale path, automated repeatable fleet
deployment (including the mesh topology), a disconnected administration
overlay, and eventually a distributed tunnel-broker service for consumer
provisioning across the fleet.

### Success criteria

- **Demo:** 3 independent nodes, 1 connected consumer each, every defined
  transaction traffic type flowing **both directions** end-to-end, with gap →
  NACK → retransmit recovery working across the fabric.
- **Stretch:** new sites added incrementally with minimal per-site definition,
  deployable across multiple providers (cloud, on-prem, dedicated).

## Locked decisions

| Area              | Decision                                                                                     |
| ----------------- | -------------------------------------------------------------------------------------------- |
| Admin overlay     | **WireGuard** on a separate address space; SSH binds the wg address, public SSH closed; `mgmt_cidrs_*` + provider ACL are the emergency key-auth-only fallback. |
| Test substrate    | **Container/netns `ip6gre` mesh** extending the Go Docker harness (default Docker bridge can't do PIM/SSM, but ip6gre + ASM work inside netns with `NET_ADMIN`). |
| Scale-out         | **Collapsed node first**; a neighbour **consumer-edge** node (itself a multicast listener/consumer) terminates many consumer tunnels as the horizontal scale path. |
| Fabric routing    | **smcroute first** (verify the full mesh), **FRR `pim6d` going forward** (production / partial-mesh). |

## Addressing principles

- **Multicast group addressing is [BRC-129](../brc-129-multicast-addressing.md)** —
  `FF0X::B:<idx>` (ASM) / `FF3x::B:<idx>` (SSM), scope from `mc_scope`, group-id
  from `mc_group_id`. The mesh layer **never invents group addresses**; it
  replicates the BRC-129 group ranges under the active scope plus the
  always-global Block Broadcast channel (`0xFFFE` @ `FF0E`).
- **Underlay / tunnel / consumer / admin addressing is scenario-specific** —
  outer tunnel endpoints, inner link prefixes, the local-segment address, the
  WireGuard prefix: all operator-supplied per deployment/topology, **never
  hard-coded** in the infra repos. Lab/scenario fixtures may pick example
  values; the repos stay addressing-agnostic for the underlay.

## The core problem: multicast over a GRE mesh

`ip6gre` is point-to-point with no native multicast replication, and a full mesh
risks loops/duplicates. Each node both emits (proxy) and receives (listener +
retry) on the fabric, so a frame emitted on node A must reach the listeners on
B and C. Solution: a **multicast router per node** (`mc-router`).

- **Full mesh (default):** every node reaches every other directly. The router
  fans **local → all peer tunnels** and **each peer tunnel → local only** — no
  tunnel→tunnel transit, so it is inherently loop-free and duplicate-free.
- **Partial mesh:** requires transit relay; use FRR PIM (RPF prevents loops).
  Application-layer TxID dedup (`bsp:tx` ingress / `bsl:egr` egress) is a
  backstop against duplicate delivery.

**Resolved (Phase 0 repro, full duplex across a 3-node mesh):** Linux submits
*locally-originated* multicast to the MFC using the **transmit** interface as
the input VIF — so the proxy emitting on `mc_iface` matches a `from mc_iface to
<tunnels>` fan-out rule with **no veth and no interface-model change** (the
co-located listener gets its copy via `IPV6_MULTICAST_LOOP`). smcroute is
sufficient for the full mesh. Three operational requirements were established
and are encoded in the role: the **MULTICAST flag** must be set on the ip6gre
tunnels + local segment (they come up without it; netplan won't set it),
smcroute needs explicit **`phyint … enable`** lines, and the emit **source must
be global/ULA** (link-local is never forwarded off-link; the proxy already
binds a global address). FRR `pim6d` remains the forward path for **partial
meshes** (transit relay needs PIM RPF, which smcroute lacks). See
[`integrated-infra/docs/mesh.md`](https://github.com/lightwebinc/integrated-infra/blob/main/docs/mesh.md).

## Phases

### Phase 0 — `mc-router` role + fabric mesh proof — IMPLEMENTED

- `integrated-infra` `mc-router` role: smcroute + FRR `pim6d` backends, BRC-129
  scope-aware group fan-out, off by default (`enable_mc_router`). Interface
  model: `mc_iface` becomes the node-local multicast segment (dummy); each
  `fabric: true` tunnel is a peer link; the ff0X route lives on the local
  segment only. Firewall opens multicast on all fabric ifaces, adds the
  forward-chain replication accept, and opens PIM under the FRR backend.
- Spec: [`integrated-infra/docs/mesh.md`](https://github.com/lightwebinc/integrated-infra/blob/main/docs/mesh.md).
- Proof: `multicast-test/mesh/ip6gre-mesh.sh` (privileged netns repro mirroring
  the role's smcroute config) + skipped `scenario80_test.go` (`MESH_REPRO=1`).
- **Acceptance: met** — the repro verifies full-duplex replication across a
  3-node full mesh in every direction (smcroute, single `mc_iface`, no veth).

### Phase 1 — Topology-as-data + config generation

- New `fleet-orchestration` repo. `topology.yml`: nodes (provider, WAN addr,
  roles), mesh type (full/partial + adjacency), consumers per node.
- Generator → per-node `tunnels[]`, mc-router rules, generated Ansible
  inventory, WireGuard peer maps. "Add a site" = add a node + regenerate.

### Phase 2 — Full-duplex collapsed node + bidirectional consumer tunnels — IMPLEMENTED

- A consumer tunnel is a **multicast leaf**: the miner sends txns **UP** to the
  proxy by unicast (its tunnel is a proxy ingress iface) and receives **DOWN**
  by joining the shard groups the mc-router fans onto the tunnel. Mark a
  non-fabric tunnel `mc_egress: true`. `mode: unicast` consumers instead get a
  single listener `egress_addr` (SPV/app). Groups fan to consumer leaves on
  both the local-emit and fan-in paths, so a miner sees txns ingested at **any**
  node; no rule accepts multicast *from* a leaf.
- `integrated-infra` mc-router + firewall (`firewall_consumer_mc_ifaces`) and
  the `fleet-orchestration` generator (`consumers[]` → `mc_egress` tunnels)
  implement it.
- **Acceptance: met** — `multicast-test/mesh/ip6gre-mesh.sh` attaches a miner
  per node and verifies DOWN multicast receipt from every node + UP unicast
  ingress. (Per-frame-type validation across the live binaries lands with the
  Phase 3 harness.)

### Phase 3 — Multi-node mesh test harness + success demo — IMPLEMENTED

- `multicast-test/mesh/collapsed-mesh.sh` stands up N collapsed nodes running
  the **real** `shard-proxy` + `shard-listener` + `retry-endpoint` in per-node
  netns over the ip6gre mesh + mc-router, each with one miner (subtx-gen sender
  + sink). Runs host-native (netns, not the Docker bridge, which can't carry the
  multicast mesh). Scenario 81 (`MESH_DEMO=1`) drives it.
- **Acceptance: met** — the 3-node demo verifies every miner receives traffic
  ingested at every node (1797 frames/miner: own 600 via loopback + 600 from
  each remote node), full-duplex, through the real binaries.
- **Finding (fixed):** emitters send IPv6 multicast at hoplimit 1, which a
  routed/tunneled mesh decrements to 0 on the first hop. Added
  `shard-proxy` `EGRESS_HOPLIMIT` (mirrors the listener's `MC_EGRESS_HOPLIMIT`);
  integrated-infra auto-raises it to 64 in mesh mode. The proxy's co-located
  loopback (`DEBUG`/`IPV6_MULTICAST_LOOP`) governs whether the local listener
  sees the node's own emissions.

### Phase 4 — WireGuard admin overlay — IMPLEMENTED

- `integrated-infra` `admin-overlay` role: per-node wg keypair, full admin mesh
  built from gathered peer pubkeys (hostvars), overlay address operator-supplied
  (`fleet-orchestration` assigns from `admin_overlay.prefix`). SSH over the
  overlay always; firewall keeps `mgmt_cidrs` as the emergency key-auth fallback
  (provider ACL the complementary half); optional `admin_overlay_lock_ssh` binds
  sshd to the overlay only. Linux-only (systemd wg-quick) for now.
- **Acceptance: met** — `multicast-test/mesh/admin-overlay.sh` (scenario 82)
  verifies an N-node full-mesh WireGuard overlay: handshake + reachability over
  the overlay in every direction.

### Phase 5 — Consumer-edge scale-out role

- `listener-infra` `consumer-edge` node: a multicast listener/consumer that
  terminates many consumer tunnels, offloading the core node. Documents the
  collapsed → distributed (proxy/listener/retry/consumer-edge) per-site path.

### Phase 6 — Fleet orchestration + multi-provider (stretch)

- Repeatable incremental site-add across AWS / generic-SSH / on-prem / dedicated
  via the existing Terraform examples, auto-wired into mesh + admin overlay by
  the Phase 1 generator.

### Phase 7 — Tunnel broker service + registration API

- New `tunnel-broker` repo: end-user/consumer registration, tunnel provisioning
  across the fleet, shard-group assignment, wg/gre config issuance. Built last,
  on the stable data/admin-plane primitives from Phases 0–5.

## Sequencing

Phase 0 de-risks the mesh-multicast core. Phases 1–3 produce the demonstrable
3-node deliverable with automation and a CI test. Phase 4 closes the
admin/security requirement. Phases 5–6 are the scale story. Phase 7 is the
eventual broker. The demo track (0–3) and the broker (7) are independent once
Phase 0 lands.
