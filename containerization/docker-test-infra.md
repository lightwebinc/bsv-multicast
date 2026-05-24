# Docker Test Infrastructure

## Status: implemented

The Go test harness lives in [`bitcoin-multicast-test/harness/`](../../bitcoin-multicast-test/harness/) and is the **primary** test infrastructure for all containerizable components. It covers 40 scenarios from `TestScenario00` through `TestScenario99` (BGP scenarios 40–42 are present as `t.Skip` stubs pending Multus / multi-network support — see [k0s-deployment.md](k0s-deployment.md)).

This document reflects what is actually in the tree. The earlier draft described a `docker-compose` + `overlays/` design that was never implemented and is not needed; the harness orchestrates containers directly from Go.

---

## High-level architecture

```
bitcoin-multicast-test/
├── Makefile                 # make test / test-quick / test-retransmit / test-frag
├── harness/
│   ├── build/build.go       # cross-compile + distroless image bake (uses go.work)
│   ├── driver/
│   │   ├── driver.go        # Driver interface (Start/Stop/Exec/Addr/MetricsURL/WaitExit)
│   │   └── docker/
│   │       ├── bridge.go    # CreateMcastBridge — fd10::/64 + MLD snooping + querier
│   │       └── docker.go    # docker run / inspect / stop wrapper
│   ├── env/
│   │   ├── env.go           # Env binds Driver + NodeConfigs + t.Cleanup
│   │   ├── netem.go         # tc netem (loss/delay) on container veth
│   │   └── iptables.go      # ip6tables DROP for ingress block / "apply_listener_loss"
│   ├── metrics/
│   │   ├── scrape.go        # expfmt direct HTTP scrape; map[string]float64
│   │   └── assert.go        # ratio + threshold assertions
│   └── scenarios/
│       ├── main_test.go     # TestMain — bridge create + global cleanup
│       ├── topology_helpers_test.go
│       └── scenarioNN_test.go  # one file per scenario
└── vm-lab/                  # Legacy LXD bash suite (see lxd-coexistence.md)
```

No `docker-compose.yml`, no `overlays/`, no shell scripts for orchestration. Everything is `go test`.

---

## Network: `mcast-fabric` user-defined bridge

`harness/driver/docker/bridge.go` creates a Docker user-defined IPv6 bridge once per test session:

```
NetworkName = "mcast-fabric"
BridgeName  = "brmcast0"
Subnet      = "fd10::/64"
```

After creating the Docker network, the harness enables MLD snooping and the IPv6 querier on the backing Linux bridge from Go:

```go
ip link set dev brmcast0 type bridge mcast_snooping 1
ip link set dev brmcast0 type bridge mcast_querier  1
echo 1 > /sys/class/net/brmcast0/bridge/mcast_querier6   // direct sysfs
```

A 3-second settle delay is applied after first creation so the MLD querier completes its first query cycle before containers join groups. Subsequent test runs reuse the existing bridge and skip the delay.

Tests run as root (or under `sudo`) — `make test` invokes `sudo go test`. Sysfs writes require either root or `CAP_NET_ADMIN`.

---

## Container lifecycle

Each scenario test uses `env.New(t, dockerDriver)` to wire up nodes:

```go
e := env.New(t, docker.New())
e.AddNode(driver.NodeConfig{
    Name:        "proxy",
    Image:       "bitcoin-shard-proxy:harness",
    IPv6:        "fd10::2",
    Env:         map[string]string{...},
    MetricsPort: 9100,
    Role:        driver.RoleProxy,
})
// ... add listener1..3, retry1..3, source ...
e.StartAll(ctx)
t.Cleanup(func() { e.StopAll(context.Background()) })
```

The Docker driver issues `docker run -d --network mcast-fabric --ip6 <addr> --cap-add NET_ADMIN -e K=V ... <image>`. There is no host port mapping — `/metrics` is scraped over the container's IPv6 directly on the bridge.

Every container runs with `--cap-add NET_ADMIN` for `setsockopt(IPV6_MULTICAST_IF)` / MLD join. No `network_mode: host` is used; bridge mode works reliably with MLD snooping + querier on the user-defined bridge.

---

## Image build

`harness/build/build.go` cross-compiles each component's binary on the host and packages it into a minimal Docker image:

1. Resolves `bitcoin-shard-common` via `go.work` (preferred — workspace lives in the parent of all repos) or via a temporary `replace` directive in `go.mod`.
2. `GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -buildvcs=false -trimpath` on the host.
3. Bakes the static binary into a `distroless/static:nonroot` image with a one-shot `docker build` from a tiny in-memory Dockerfile template.
4. Tag convention: `<component>:harness`.

This bypasses any per-component `Dockerfile` (the harness does not depend on them being present or correct). The same build pipeline is used by `make test` and is therefore the ground truth for the binary versions exercised by every scenario.

For k0s the per-repo `Dockerfile` is still the publishing target — see [helm-charts.md](helm-charts.md) and [roadmap.md](roadmap.md) Phase 1.

---

## Network emulation primitives

`harness/env/netem.go` and `harness/env/iptables.go` replace the bash-era `apply_listener_loss` nftables hack and `lxc exec ... iptables` calls. Both run on the host (the harness has `NET_ADMIN`) against the container's veth or namespace.

| Primitive | Implementation | Replaces (bash era) |
|---|---|---|
| `ApplyLoss(node, pct)` / `RemoveLoss(node)` | `tc qdisc add dev <veth> root netem loss <pct>%` on the host-side veth | `nft add rule ... limit rate over ... drop` on the LXD VM |
| `BlockIngress(node, src)` / `UnblockIngress(node, src)` | `ip6tables -I DOCKER-USER ...` (host) | `nft add rule ip6 input ip6 saddr ... drop` on the VM |

Loss is declarative per-scenario; cleanup is guaranteed by `t.Cleanup` even on test panic.

---

## Metrics scrape — no Prometheus required

`harness/metrics/scrape.go` performs HTTP scrapes directly against each container's `/metrics` endpoint and parses the Prometheus text exposition format via `github.com/prometheus/common/expfmt`. Returned as `map[string]float64` (summed across label sets) or filtered by label.

```go
m, _ := metrics.Scrape("http://[fd10::21]:9200/metrics")
got := m["bsl_frames_forwarded_total"]

dropped, _ := metrics.ScrapeWithLabel(url, "bsl_frames_dropped_total", "reason", "shard_filter")

v, ok := metrics.WaitFor(url, "bre_cache_hits_total",
    func(x float64) bool { return x >= 10 }, 30*time.Second, 200*time.Millisecond)
```

Implications:

- The harness **never starts Prometheus or Grafana**. Test pass/fail does not depend on any external metrics infrastructure.
- Developers who want a Grafana view of an in-progress test run can point an external Prometheus at the container IPv6 addresses on `fd10::/64`. This is manual and out-of-band; the harness does not coordinate with it.
- This is the same direct-scrape model the plan called for. It is also the model `vm-lab/` uses for assertions (`snapshot_metrics` in `vm-lab/scenarios/lib/common.sh`), but `vm-lab` *additionally* keeps a long-running external Prometheus VM for visualisation. See [lxd-coexistence.md](lxd-coexistence.md).

---

## Scenario structure

Each scenario is a `func TestScenarioNN_Name(t *testing.T)` in `harness/scenarios/scenarioNN_test.go`. Shared topology builders live in `topology_helpers_test.go`. The Makefile selects subsets via Go test filters:

```bash
make test            # all 40 scenarios (~30 min)
make test-quick      # functional tier 1
make test-retransmit # NACK / retransmit scenarios
make test-frag       # fragmentation scenarios
sudo go test ./harness/scenarios/... -v -run TestScenario13
```

`TestMain` (`scenarios/main_test.go`) calls `docker.CreateMcastBridge` once per `go test` invocation. Per-test cleanup is via `t.Cleanup` on the `env.Env`.

---

## Known limitations / deferred work

- **`NUM_WORKERS=1` for listeners** — required by SO_REUSEPORT multicast delivery semantics. Harness `NodeConfig` for `RoleListener` enforces it.
- **`NACK_ADDR` must be set** for each retry endpoint — the harness wires it automatically from `driver.Addr(name)`.
- **BGP scenarios (40–42) are stubbed** — they need additional Docker networks (`bgp-transit`, `bgp-ibgp`) and FRR + BIRD sidecar containers. This is the same multi-network requirement Multus addresses in k0s. Scoped for Phase 4.5 in [roadmap.md](roadmap.md).
- **Root required.** Tests cannot run as an unprivileged user because the harness writes `mcast_querier6` via sysfs and manipulates host tc/ip6tables.
- **Single host.** The harness is single-host by design; cross-host multicast belongs in `vm-lab/` (LXD) or eventually k0s.

---

## Why no docker-compose

The original plan called for `stack.compose.yml` + per-scenario `overlays/`. In practice:

- The harness needs typed Go config (`NodeConfig`) anyway, to pass into the Driver interface; compose YAML would be a redundant second source.
- `docker compose -p <project>` adds a layer of indirection between the test and the container without any test-level benefit; `docker run --network mcast-fabric` is sufficient.
- `tc netem` / `ip6tables` primitives operate on host veths, not inside compose abstractions.
- Multi-driver portability (originally cited as a reason for compose) didn't materialise — the LXD driver was never built (LXD scenarios stayed bash-native).

If a future need ever arises to declaratively share a topology with an external tool, dump `NodeConfig`s to compose YAML from Go — not the other way around.
