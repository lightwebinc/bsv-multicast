# Docker Test Infrastructure

## Goal

A self-contained, reproducible test environment using Docker that:
- Runs IPv6 multicast end-to-end without a physical NIC or LXD
- Supports the same scenario taxonomy as the existing bash/LXD suite
- Is driveable from a **Go test harness** using `go test`
- Runs on the developer workstation and on a self-hosted CI runner

## Multicast in Docker — bridge configuration

Linux bridge `docker0` blocks MLD by default. Creating a user-defined bridge with MLD snooping enabled and an MLD querier restores IPv6 multicast delivery between containers.

```bash
docker network create \
  --driver bridge \
  --ipv6 \
  --subnet fd10::/64 \
  --opt com.docker.network.bridge.name=brmcast0 \
  mcast-fabric

# After creation, enable MLD snooping and querier on the bridge
echo 1 > /sys/class/net/brmcast0/bridge/mcast_snooping
echo 1 > /sys/class/net/brmcast0/bridge/mcast_querier
echo 1 > /sys/class/net/brmcast0/bridge/mcast_querier6
```

These `sysfs` writes must be issued on the host (not inside the container). The Go harness issues them via `exec.Command("sh", "-c", "echo 1 > ...")` or via the docker SDK + `nsenter` before starting any service containers.

**Fallback for environments that cannot create custom bridges:** set `MC_SCOPE=link` (FF02::/16) — Linux loopback delivers link-local multicast to all sockets in the same network namespace. This is used by the existing E2E tests in `bitcoin-shard-listener` (unicast injection pattern avoids even that dependency).

## Compose stack topology

```
subtx-gen → proxy  →  [mcast-fabric]  →  listener1
                   →                 →  listener2
                   →                 →  listener3
                                     →  retry1
                                     →  retry2
```

All nodes attach to `mcast-fabric` (user-defined IPv6 bridge). Each container uses:

```yaml
networks:
  mcast-fabric:
    ipv6_address: fd10::X
```

Because the containers share a single Linux bridge, `network_mode: host` is **not required** for the compose stack. However all containers need:

```yaml
cap_add:
  - NET_ADMIN   # for setsockopt IPV6_MULTICAST_IF, IP_ADD_MEMBERSHIP
sysctls:
  net.ipv6.conf.all.disable_ipv6: "0"
```

### Proxy container

```yaml
proxy:
  image: bitcoin-shard-proxy:dev
  networks:
    mcast-fabric:
      ipv6_address: fd10::2
  cap_add: [NET_ADMIN]
  environment:
    UDP_LISTEN_PORT: "9000"
    MULTICAST_IF: "eth0"    # eth0 = mcast-fabric inside the container
    EGRESS_PORT: "9001"
    SHARD_BITS: "2"
    MC_SCOPE: "site"
    METRICS_ADDR: ":9100"
  ports:
    - "9000:9000/udp"       # expose to harness for frame injection
    - "9100:9100"           # metrics scrape
```

### Listener container (replicated)

```yaml
listener1:
  image: bitcoin-shard-listener:dev
  networks:
    mcast-fabric:
      ipv6_address: fd10::11
  cap_add: [NET_ADMIN]
  environment:
    MULTICAST_IF: "eth0"
    LISTEN_PORT: "9001"
    SHARD_BITS: "2"
    MC_SCOPE: "site"
    EGRESS_ADDR: "127.0.0.1:9100"  # loopback sink or real downstream
    RETRY_ENDPOINTS: "[fd10::21]:9300,[fd10::22]:9300"
    BEACON_ENABLED: "true"
    NUM_WORKERS: "1"          # MUST be 1 — see component-viability.md
    METRICS_ADDR: ":9200"
  ports:
    - "9201:9200"             # metrics (distinct host port per listener)
```

### Retry-endpoint container

```yaml
retry1:
  image: bitcoin-retry-endpoint:dev
  networks:
    mcast-fabric:
      ipv6_address: fd10::21
  cap_add: [NET_ADMIN]
  environment:
    MC_IFACE: "eth0"
    LISTEN_PORT: "9001"
    SHARD_BITS: "2"
    MC_SCOPE: "site"
    EGRESS_IFACE: "eth0"
    NACK_PORT: "9300"
    NACK_ADDR: "fd10::21"   # REQUIRED — explicit unicast addr for ACK/MISS
    BEACON_ENABLED: "true"
    BEACON_TIER: "0"
    METRICS_ADDR: ":9400"
  ports:
    - "9401:9400"
```

---

## Go test harness architecture

The harness lives inside `bitcoin-multicast-test` under a new `harness/` package tree. It provides:

```
harness/
  driver/
    driver.go          # interface Driver { Start, Stop, Exec, Addr }
    docker/
      docker.go        # Driver implementation via Docker SDK
    lxd/
      lxd.go           # Driver implementation via LXD CLI (wraps existing bash)
  topology/
    topology.go        # Declarative graph: nodes + links + role assignments
  scenario/
    runner.go          # Execute a scenario against a running topology
    metrics.go         # Scrape Prometheus endpoints, assert counters
  cmd/
    run-scenario/
      main.go          # CLI entry point: run-scenario -driver docker -scenario 01
```

### Driver interface

```go
type Driver interface {
    // Start launches a named node with the given role and env vars.
    Start(ctx context.Context, name string, cfg NodeConfig) error
    // Stop halts and removes the node.
    Stop(ctx context.Context, name string) error
    // Exec runs a command inside the node; returns stdout.
    Exec(ctx context.Context, name, cmd string, args ...string) (string, error)
    // Addr returns the accessible IPv6 address of a named node.
    Addr(ctx context.Context, name string) (net.IP, error)
    // MetricsURL returns the HTTP URL to scrape Prometheus metrics.
    MetricsURL(ctx context.Context, name string) (string, error)
}
```

### Topology declaration

```go
topo := topology.New().
    Node("source",    topology.RoleSubtxGen).
    Node("proxy",     topology.RoleProxy).
    Node("listener1", topology.RoleListener).
    Node("listener2", topology.RoleListener).
    Node("retry1",    topology.RoleRetryEndpoint).
    Link("mcast-fabric", "proxy", "listener1", "listener2", "retry1")
```

### Scenario execution pattern

Each scenario is a Go function matching:

```go
func ScenarioFn(t *testing.T, env *scenario.Env)
```

`scenario.Env` exposes:
- `env.Send(frames int, gapRate float64)` — drives subtx-gen to inject traffic with optional artificial gaps
- `env.Metrics(node string) prometheus.Gatherer` — snapshot metrics from node
- `env.Assert(cond scenario.Condition)` — structured assertion with rich failure output
- `env.WaitForMetric(node, metric string, op scenario.Op, val float64, timeout time.Duration)`

### Metrics assertions

The harness scrapes `/metrics` via the Prometheus text exposition format (no Prometheus server required — harness decodes directly using `github.com/prometheus/common/expfmt`).

Example:

```go
env.WaitForMetric("listener1", `bsl_frames_forwarded_total`, scenario.GTE, float64(frames), 30*time.Second)
env.WaitForMetric("retry1",    `bre_cache_hits_total`,        scenario.GTE, 10, 30*time.Second)
```

### Scenario registration

```go
var Scenarios = map[string]scenario.ScenarioFn{
    "01-functional-all-shards":  Scenario01FunctionalAllShards,
    "09-nack-retransmit":        Scenario09NACKRetransmit,
    "13-miss-escalation-tier":   Scenario13MissEscalationTier,
    // ...
}
```

`go test ./harness/... -run TestScenario/01` runs scenario `01` using the selected driver.

---

## Gap injection strategy

The harness uses the existing `subtx-gen` flag `-seq-gap-delay` combined with `iptables`/`nftables` DROP rules applied via `driver.Exec` (or equivalent LXD exec) to simulate configurable packet loss.

For Docker:

```go
env.Driver.Exec(ctx, "proxy", "nft", "add", "rule", "ip6", "output", "udp", "dport", "9001", "limit", "rate", "over", "90/second", "drop")
```

This is safer than patching the binary and exercises the actual NACK recovery path.

---

## Metrics stack coexistence

The test harness does **not** start Prometheus or Grafana. Service containers expose `/metrics` endpoints. The harness scrapes them directly over HTTP after each test phase. This keeps the test environment minimal and avoids port conflicts with the external metrics stack running on the `metrics` VM.

If a developer wants to visualise a test run, they can point the external Prometheus instance to the running containers:

```yaml
# prometheus.yml scrape_configs addition (manual, not automated by harness)
- job_name: docker-test
  static_configs:
    - targets: ['localhost:9100', 'localhost:9201', 'localhost:9202', 'localhost:9203', 'localhost:9401']
```

---

## Known limitations

- **`NUM_WORKERS=1` for listeners:** The listener must use a single SO_REUSEPORT socket or the multicast delivery will be N-fold duplicated. Set explicitly; the harness enforces it in `NodeConfig` for `RoleListener`.
- **MLD querier latency:** After bridge setup, wait 2–3 seconds for the first MLD query cycle before sending multicast traffic. The harness `Start()` call includes a 3-second settle delay.
- **Privileged bridge setup:** `echo 1 > /sys/class/net/...` requires root on the Docker host. On CI self-hosted runners this is expected. On shared runners it may be unavailable — use the link-scope (FF02) fallback.
- **Source address pinning:** `NACK_ADDR` must be set to the exact container IPv6 for retry endpoints. The harness injects this automatically from `driver.Addr()`.
