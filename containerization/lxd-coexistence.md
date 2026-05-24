# LXD Coexistence

## Principle

The existing LXD test lab and its 40+ scenario suite are **unchanged**. New Docker/k0s infrastructure is additive. Both coexist on the same physical host.

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

Scenarios: `scenarios/00` through `scenarios/53` and `99`, run via `bash scenarios/run-all.sh` or individually.

---

## Port and bridge isolation

Docker user-defined network uses `fd10::/64` (distinct from LXD fabric `fd20::/64`) and `mcast-fabric` bridge (`brmcast0`). LXD uses `lxdbr0`/`lxdbr1`.

No IP overlap, no bridge bridging. Both can run simultaneously.

### Parallel run rules

- If a scenario needs specific multicast groups (FF05::FF:000B:0000–0003 for shard_bits=2), Docker test containers use the same groups on the Docker bridge — but because the bridges are separate Linux bridges, MLD snooping is per-bridge. There is no cross-bridge multicast leakage.
- The same host UDP ports **can conflict** if both stacks use default ports simultaneously. Recommended approach: assign distinct host-side port mappings in Docker compose files (e.g., Docker proxy listens on host port `19000` vs. LXD proxy on host port `9000`). The Go harness NodeConfig sets `HostPort` overrides for Docker containers.

---

## Go harness — LXD driver

The harness `Driver` interface supports LXD via a thin wrapper over `lxc exec`:

```go
// lxd/lxd.go

type LXDDriver struct {
    Profile string   // LXD profile name, e.g. "ubuntu-small-mcast"
}

func (d *LXDDriver) Start(ctx context.Context, name string, cfg NodeConfig) error {
    // lxc launch ubuntu:24.04 <name> --profile <profile>
    // lxc exec <name> -- systemctl start <service>
    // ...
}

func (d *LXDDriver) Exec(ctx context.Context, name, cmd string, args ...string) (string, error) {
    // lxc exec <name> -- <cmd> <args...>
}

func (d *LXDDriver) Addr(ctx context.Context, name string) (net.IP, error) {
    // lxc exec <name> -- ip -6 addr show eth1 scope global | parse
}
```

However, **the LXD driver is secondary**. The existing bash scenarios already cover LXD comprehensively. The LXD driver exists to allow harness-authored scenarios to optionally run on LXD VMs for fidelity comparison without rewriting the scenarios as bash.

---

## Scenario taxonomy and driver applicability

| Scenario group | Bash/LXD | Docker driver | LXD driver |
|---|---|---|---|
| 00–09 (functional) | ✅ existing | ✅ Phase 2 | optional |
| 10–16 (NACK/ratelimit) | ✅ existing | ✅ Phase 2 | optional |
| 20–26 (subtree/fragmentation) | ✅ existing | planned | optional |
| 30–37 (block announce, anchor) | ✅ existing | planned | optional |
| 40–42 (BGP ingress) | ✅ existing | not applicable (BGP needs real routing) | only |
| 50–53 (txid-dedup/Redis) | ✅ existing | ✅ Phase 2 (Redis in Docker) | optional |
| 99 (nack-retransmit perf) | ✅ existing | ✅ Phase 2 | optional |

Scenarios 40–42 require real BGP routing (FRRouting/Bird on LXD VMs) and are **LXD-only** — the Docker driver cannot simulate BGP ECMP. This is not a gap; BGP is infrastructure-level and tested adequately in the LXD lab.

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

## Preserving existing bash scenarios

No changes to `bitcoin-multicast-test/scenarios/`. The new Go harness is additive in `bitcoin-multicast-test/harness/`. The `run-all.sh` at the root of `scenarios/` is unchanged and continues to work standalone.

The Go harness scenarios are registered in `harness/scenarios/` as Go files and run via `go test`. They do not replace or depend on the bash scenarios — they are independent test coverage.

---

## Migrating a bash scenario to Go (optional, future)

If a bash scenario needs to be ported to the Go harness (e.g., to enable Docker driver support), the process is:

1. Copy logic from `scenarios/NN-name/run.sh` into a new `harness/scenarios/nn_name_test.go`
2. Replace `lxc exec <vm> -- <cmd>` with `env.Driver.Exec(ctx, node, cmd, args...)`
3. Replace metric snapshot assertions (`snapshot_metrics.sh`) with `env.WaitForMetric(...)`
4. The original bash scenario remains in place — both run independently

The bash scenarios are the ground truth for LXD behavior. Go harness scenarios are the ground truth for Docker/k0s behavior.
