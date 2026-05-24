# LXD Coexistence

## Principle

The LXD lab is now **legacy** under `bitcoin-multicast-test/vm-lab/`. The Go Docker harness in `harness/` is the primary test path and supersedes the LXD bash suite for everything except switch/BGP fidelity. Both can run on the same host; they share no state.

---

## Current LXD lab topology (reference)

```
Host: lax (Ubuntu 24.04)
  lxdbr0 (mgmt, 10.10.10.0/24)
  lxdbr1 (fabric, 10.10.11.0/24, fd20::/64)

VMs (ubuntu-small-mcast profile: eth0=lxdbr0, eth1=lxdbr1):
  source    (.10)        bitcoin-subtx-generator
  proxy     (.20/.2)     bitcoin-shard-proxy
  listener1 (.31/fd20::21) bitcoin-shard-listener
  listener2 (.32/fd20::22) bitcoin-shard-listener
  listener3 (.33/fd20::23) bitcoin-shard-listener
  retry1    (.34/fd20::24) bitcoin-retry-endpoint  tier=0, pref=128
  retry2    (.35/fd20::25) bitcoin-retry-endpoint  tier=0, pref=64
  retry3    (.36/fd20::26) bitcoin-retry-endpoint  tier=1, pref=128
  metrics   (.142)       Prometheus + Grafana (external)
```

Scenarios: `vm-lab/scenarios/00` through `vm-lab/scenarios/53` and `99`, run via `bash vm-lab/scenarios/run-all.sh` or individually. These are kept for switch-level MLD snooping fidelity, BGP/anycast routing (scenarios 40–42), and as a regression reference against the harness.

---

## Port and bridge isolation

Docker user-defined network uses `fd10::/64` (distinct from LXD fabric `fd20::/64`) and `mcast-fabric` bridge (`brmcast0`). LXD uses `lxdbr0`/`lxdbr1`.

No IP overlap, no bridge bridging. Both can run simultaneously.

### Parallel run rules

- If a scenario needs specific multicast groups (FF05::FF:000B:0000–0003 for shard_bits=2), Docker test containers use the same groups on the Docker bridge — but because the bridges are separate Linux bridges, MLD snooping is per-bridge. There is no cross-bridge multicast leakage.
- The harness does **not** publish host ports — metrics are scraped via the container's IPv6 on `fd10::/64`. So there is no host-port collision with the LXD lab (which binds VM ports on `lxdbr0` / `lxdbr1`). The two stacks are fully independent on the same host.

---

## No Go-harness LXD driver

The original plan proposed an `harness/driver/lxd/` to let Go-authored scenarios target LXD VMs. **This was not built and is not on the roadmap.** Reasons:

- The LXD scenarios (`vm-lab/scenarios/*.sh`) already cover LXD comprehensively in bash.
- The Docker driver is the only test-driving path that produces hermetic, parallel-safe runs.
- Re-implementing scenario logic on top of an LXD driver would duplicate effort with no fidelity gain.

If a harness-authored scenario ever needs to target LXD for switch-level fidelity, the recommended path is to write that scenario as a bash file under `vm-lab/scenarios/` rather than extend the Go harness.

---

## Scenario coverage map

| Scenario group | LXD bash (`vm-lab/`) | Go harness (`harness/`) |
|---|---|---|
| 00–09 (functional) | ✅ reference | ✅ implemented |
| 10–16 (NACK / ratelimit) | ✅ reference | ✅ implemented |
| 20–26 (subtree / fragmentation) | ✅ reference | ✅ implemented |
| 30–37 (block announce, anchor) | ✅ reference | ✅ implemented |
| 40–42 (BGP ingress / failover / anycast) | ✅ only | ⏸ `t.Skip` — needs Multus-style multi-network + FRR/BIRD sidecars |
| 50–53 (txid-dedup / Redis) | ✅ reference | ✅ implemented |
| 99 (NACK retransmit perf) | ✅ reference | ✅ implemented |

BGP scenarios 40–42 remain LXD-only in practice. In k0s they re-enable once `bgp-transit` and `bgp-ibgp` NetworkAttachmentDefinitions are declared (see [k0s-deployment.md](k0s-deployment.md)); in Docker they require additional Docker user-defined networks and BGP-speaker sidecars and are gated on Phase 4.5 of the [roadmap](roadmap.md).

---

## Avoiding double-run conflicts in CI

The CI self-hosted runner runs both stacks on the same host. To prevent interference:

1. **LXD scenario suite** is triggered by a separate GH Actions job with its own `concurrency.group` key.
2. **Docker harness** uses a different concurrency group.
3. Both jobs declare `concurrency.cancel-in-progress: false` so a LXD run is never cancelled mid-scenario by a Docker run.

```yaml
# LXD job
concurrency:
  group: lxd-scenarios-${{ github.ref }}
  cancel-in-progress: false

# Docker job
concurrency:
  group: docker-e2e-${{ github.ref }}
  cancel-in-progress: true
```

---

## Source of truth

- Go harness scenarios under `harness/scenarios/` are the ground truth for **container** (Docker, k0s) behaviour.
- LXD bash scenarios under `vm-lab/scenarios/` are the ground truth for **VM-level** and **switch-level** behaviour (BGP, real MLD snooping on real switches, large-scale `run-all.sh` regression).

The two suites are independent. There is no shared scenario format and no test-level coupling.
