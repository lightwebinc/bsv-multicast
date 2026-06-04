# Unified Component Logging Plan

- Status: **Implemented** (emit side) — `shard-common v0.13.4` (`logging`,
  `hostinfo`, `tracing` packages) wired into `shard-proxy v1.12.4`,
  `shard-listener v1.5.4`, `retry-endpoint v1.4.2`, `shard-manifest v0.2.1`,
  `subtx-generator v0.2.1`; e2e `multicast-test` scenario 73. Collector rollout
  (Grafana Alloy → Loki) + node_exporter remain the deferred infra phase.
- Scope (this phase): the **emit side** only. Standardize how every component
  *produces* logs so they are structured, self-identifying, and consistent.
- **Deferred to a later phase (but its architecture is now decided, see
  [Recommended transport](#recommended-transport-architecture-decided)):** the
  shipping agents and log store. This document fixes the on-host output contract
  now so that later phase is configuration, not code. The transport standard
  (OTLP via a node-local collector → Loki) is recommended here; only its rollout
  is deferred.
- **Brevity is a hard requirement.** A data-plane node at scale must be nearly
  silent in steady state. Logs prioritize bandwidth/storage economy over
  chattiness — see [Log economy](#log-economy-brevity-is-a-requirement).
- Affects: `shard-common` (new package), `shard-proxy`, `shard-listener`,
  `retry-endpoint`, `shard-manifest`, `subtx-generator`. Infra repos only later.
- Default behavior preserved: human-readable text output stays the default for
  interactive/dev use; JSON is opt-in via one flag until aggregation lands.

## Why

A multicast fabric of hundreds-to-thousands of proxies, listeners, and retry
endpoints cannot be operated by SSH-ing into hosts. Metrics
([conventions.md § Metrics](../../multicast-skills/conventions.md)) already give
us aggregate counters with consistent identity (`service.name`,
`service.instance.id`, `service.version` resource attributes + per-group/worker
labels). Logs are the other half — the *per-event narrative* metrics can't
carry: which group failed to join, which errno a `sendmmsg` returned, which
deprecated flag a host still uses, why a frame was dropped. Today that narrative
is inconsistent and unattributed, so it is unusable at fleet scale.

### Current state (inventory)

| Component | Logger | Handler | Sink | Identity attrs on logs? |
|-----------|--------|---------|------|-------------------------|
| `shard-proxy` | `log/slog` | `TextHandler` | stderr | none |
| `shard-listener` | `log/slog` | `TextHandler` | stderr | none |
| `retry-endpoint` | `log/slog` | `TextHandler` | stderr | none |
| `shard-manifest` | `log/slog` | `JSONHandler` | stderr | none |
| `shard-manifest`/`manifest-emit` | `log/slog` | `TextHandler` (no level) | stderr | none |
| `subtx-generator` | plain `log` | — | stderr | none |
| `shard-common` | none (library) | — | — | — |

~357 call sites. Five divergent initialization sites. Problems:

1. **Format split** — four text, one JSON, one unstructured. No single parser
   works across the fleet.
2. **No identity on logs** — the `service.name`/`instance.id`/`version` triple
   that OTLP metrics attach as resource attributes is absent from every log
   line, so a log cannot be tied back to the metric series for the same process.
3. **Coarse levels** — only Info↔Debug via `-debug`; no runtime level change, so
   debugging a hot host means a restart (and a restart hides the bug).
4. **Inconsistent keys** — each service invents attribute names; they do not
   match the metric label names (`group`, `worker`, `network.interface.name`),
   so logs and metrics cannot be joined on shared dimensions.
5. **OS/NIC blind spot** — syscall sites that *observe* kernel conditions
   (`ENOBUFS` on `sendmmsg`/`recvmmsg`, `SO_RCVBUF` set failure,
   `MCAST_JOIN_SOURCE_GROUP` rejection when `mld_max_msf` is exceeded) do not
   log them with an errno. The host's own kernel signals are invisible.

## Message taxonomy

Every existing and future log line maps to one of these categories. The
category fixes the **level**, the **required attributes**, and the **hot-path
discipline**. This is the catalogue the user asked for — "all potential log
messaging" — organized so each new call site has an obvious home.

| # | Category | Default level | Hot path? | Examples (from today's code) | Required attrs |
|---|----------|---------------|-----------|------------------------------|----------------|
| 0 | **Host inventory** *(new, one-shot)* | Info | no | *(none today)* — `host.inventory` emitted once at startup | host/os/cpu/mem/nic/sysctl (see §4) |
| 1 | **Lifecycle** | Info | no | `shard-proxy starting`, `received signal, starting drain`, `drain complete`, `all workers stopped`, `shutdown complete` | base only |
| 2 | **Config / capability** | Info | no | the ~dozen `… enabled` lines collapse into **one** `startup.config` event | capabilities as attrs (see [§Log economy](#log-economy-brevity-is-a-requirement)) |
| 3 | **Config warnings** | Warn | no | `deprecated -txid-dedup-* flags in use`, `unknown cache backend, using memory`, `redis dedup unavailable` | the offending value + fallback |
| 4 | **Auto-config / adoption** | Info / Warn | no | `auto-config adopted new ShardBits (restart mode)`, `auto-join applied`, `live-resharding: bridging engine installed` | `shard_bits`, `mc_group_id`, `source_mode`, `epoch`, quorum |
| 5 | **Fatal startup** | Error → exit | no | `configuration error`, `multicast interface not found`, `metrics init failed`, `invalid bind-source` | `error`, offending input |
| 6 | **Runtime subsystem error** | Error | no | `worker exited with error`, `beacon listener error`, `sender exited with error`, `TCP ingress exited with error` | `error`, subsystem id (`worker`, `iface`, `group`) |
| 7 | **Protocol / data-plane event** | Debug (Warn on anomaly) | **yes** | `subtree_group_announce: decode error`, `… sender rejected by filter`, `… datagram too short`; gap-detected, NACK-sent, retransmit | `group`, `seq`, `txid`, `frame_type`, `proto` — **rate-limited/sampled** |
| 8 | **OS / NIC / host** *(new)* | Warn / Error | partly | *(none today)* — `ENOBUFS` on egress, `SO_RCVBUF`/`SO_SNDBUF` clamp, MLD source-filter exhausted, IPv6 join failure | `errno`, `network.interface.name`, `group`, syscall name |

**Rule:** categories 1–6 are unconditional. Category 7 is the only one allowed
on the zero-alloc hot path and **must** be rate-limited or gated behind Debug —
a per-packet log at line rate is a self-inflicted outage. Category 8 is the new
work described below.

## Design

### 1. One shared emitter: `shard-common/logging`

A single small package, imported by all five binaries (`shard-common` is already
the universal dependency). It owns initialization so the five divergent setup
sites collapse to one call.

```go
// package logging
type Options struct {
    Service    string      // "shard-proxy" — MUST equal metrics.ServiceName
    InstanceID string      // == OTLP service.instance.id (hostname/pod fallback)
    Version    string      // == metrics.Version (ldflags)
    Level      slog.Level  // initial level
    Format     Format      // FormatText (default) | FormatJSON
}

// Init installs a process-wide slog default with base attributes pre-attached,
// returns a *slog.LevelVar so the level can be changed at runtime, and a
// handle for structured child loggers. Idempotent; safe before config parse
// (falls back to text/Info on the pre-config path already used in main.go).
func Init(Options) *slog.LevelVar
```

What it guarantees, fleet-wide:

- **Format:** `FormatJSON` writes one JSON object per line to **stdout**
  (12-factor: the process emits, the platform routes). `FormatText` stays the
  default for now so interactive use and current systemd/journald reading are
  unchanged. When aggregation lands, the only change is flipping the default /
  setting `LOG_FORMAT=json` in the deploy templates — **no call site changes.**
- **Identity on every line:** `Init` does `slog.New(handler).With(
  "service.name", …, "service.instance.id", …, "service.version", …)` so all
  ~357 existing call sites inherit the exact identity triple OTLP metrics use.
  This is what makes log↔metric correlation possible with zero call-site edits.
- **Runtime level:** the returned `*slog.LevelVar` is wired to a `SIGHUP`
  handler and/or a `POST /loglevel` on the existing metrics/admin listener, so an
  operator can drop one hot host to Debug without a restart (and restore it).
- **Keys match metric labels:** the package documents and (where helpful)
  provides helpers for the shared attribute vocabulary so logs and metrics join.

### 2. Standard attribute vocabulary

Reuse the names already in the metrics layer so a Grafana/whatever pivot from a
metric series to its logs is a label match, not a translation table.

| Key | Source of truth | Notes |
|-----|-----------------|-------|
| `service.name` | `metrics.ServiceName` | OTel semconv |
| `service.instance.id` | OTLP `service.instance.id` | hostname/pod fallback |
| `service.version` | `metrics.Version` | ldflags |
| `deployment.id` | dedup `<deployment-id>` | groups replicas of one logical deploy |
| `network.interface.name` | metrics `ifaceAttr` | OTel semconv; already used |
| `group` | metrics group label (`%04x`) | shard / control group idx |
| `worker` | metrics worker label | proxy worker id |
| `frame_type` / `flow` | metrics labels | BRC frame discriminator |
| `seq`, `txid` | protocol | data-plane events only |
| `error` | Go error | Error/Warn lines; never interpolate into msg |
| `errno`, `syscall` | category-8 only | OS/NIC conditions |

Convention: the message string is a **stable, low-cardinality identifier**
(matches today's style — `"auto-join applied"`), and all variables go in
attributes. This keeps messages groupable and aggregation-friendly.

### 3. OS / NIC / host visibility (the telemetry gap)

Telemetry already exposes aggregate drop *counts* (`bsp_packets_dropped_total`,
listener gaps). What it cannot explain is the **kernel-level cause**. Split into
what the component can see vs. what only the host can see — this phase does the
former; the latter is noted for the deferred infra phase.

**(a) In-process, app-attributed — DO NOW.** The proxy and listener are the only
things that know *which group/iface/worker* a kernel error belongs to. Add
category-8 logs at the syscall sites that already get an error today but swallow
or genericize it:

- `WriteBatch`/`sendmmsg` returning `ENOBUFS`/`EAGAIN` → Warn with `errno`,
  `network.interface.name`, batch size, count dropped. (This is the kernel
  socket buffer / qdisc backpressure that metrics show only as a counter.)
- `ReadBatch`/`recvmmsg` short reads / errors → Warn with `errno`.
- `SO_RCVBUF`/`SO_SNDBUF` requested-vs-granted mismatch at startup → Warn with
  requested/actual (the kernel silently clamps to `net.core.rmem_max`).
- `netjoin.Join` failure, especially `ENOBUFS` from exceeding `mld_max_msf`
  source filters (SSM) → Error with `group`, source count, `errno`. This is the
  exact failure mode `conventions.md`/the SSM design warns about; today it is a
  generic `auto-join AddGroup failed`.

These are cheap (error paths, not the success hot path) and turn opaque drops
into actionable, host-attributed events.

**(b) Out-of-process, *continuous* host telemetry — use the open-source standard,
do NOT reimplement.** NIC ring drops (`ethtool -S`), `/proc/net/snmp6`,
`/proc/net/softnet_stat`, conntrack table fill, per-queue counters,
`dmesg`/kernel-facility messages, FreeBSD `netstat -i` / kern syslog. These are
*high-frequency time series*, not events — they belong in the metrics plane, and
**[node_exporter](https://github.com/prometheus/node_exporter)** (Prometheus,
Apache-2.0) already exposes nearly all of them (`node_netstat_*`,
`node_softnet_*`, `node_network_*`, and the `ethtool` collector for ring/driver
counters). Reimplementing this inside the Go binaries would be large, brittle,
and platform-divergent for zero benefit. **node_exporter is deployed
*alongside* each component by the infra repos (Ansible role / k8s DaemonSet),
never bundled into or invoked by the application binary.** The code has **zero
dependency** on it — node_exporter being absent only loses continuous host
telemetry; the application's own `host.inventory` and §3(a) event logs are
unaffected. The application's job is the *one-shot startup inventory* below and
the *app-attributed event* logs in §3(a) — the two things node_exporter cannot
do. (Infra wiring for node_exporter + the Alloy collector is the deferred
rollout phase; see [Recommended transport](#recommended-transport-architecture-decided).)

### 4. Host inventory at startup (one-shot, near-zero cost)

When a component comes online it emits **one** structured `host.inventory`
event (category 1, Info) capturing the static facts of the machine. One line per
process lifetime — no ongoing cost, no hot-path impact — but it answers "what was
this box?" for every node in the fleet without SSH, and it is the join key that
makes a later node_exporter series interpretable ("which of the 2000 proxies is
on the slow NIC / old kernel / undersized rmem").

**Fields** (single event, nested attrs):

| Group | Fields | Source |
|-------|--------|--------|
| `host` | hostname, boot id, uptime, virtualization role | gopsutil `host` |
| `os` | GOOS, kernel version/release, distro + version | gopsutil / `uname` |
| `cpu` | model, physical/logical core count, base MHz | gopsutil `cpu` + `runtime.NumCPU` |
| `mem` | total RAM, hugepages if set | gopsutil `mem` |
| `net.<iface>` | name, MAC, MTU, link speed/duplex, driver, ring rx/tx, key offloads, **IPv4 and IPv6 addrs**, oper state | per-iface (see below) |
| `sysctl` | `net.core.rmem_max`, `wmem_max`, `net.ipv6.*mld_max_msf*`, `netdev_max_backlog` — the knobs that gate multicast throughput | `/proc/sys` read (Linux), `sysctl` (FreeBSD) |
| `build` | service, version, go version, build vcs rev | ldflags + `runtime/debug` |

**Implementation, by portability tier (minimize dependency surface):**

1. **Portable facts** (host/os/cpu/mem/basic net: name, MAC, MTU, addrs, state):
   **[gopsutil](https://github.com/shirou/gopsutil)** (v4, BSD-3, **pure Go — no
   cgo**, so it respects the `CGO_ENABLED=0` rule, and supports Linux **and**
   FreeBSD). One call at startup. This is the "cover as much as possible with the
   tooling" path the request asks for. **Both IPv4 and IPv6** addresses are
   recorded per interface — boundary nodes that bridge to non-multicast networks
   carry IPv4, so the inventory must not be v6-only.
2. **Deep NIC facts** (link speed/duplex, driver, ring sizes, offloads): on Linux
   these come from `ETHTOOL_*` ioctls. Use the thin wrappers in
   **`golang.org/x/sys/unix`** (already an indirect dep via `x/net/ipv6`) — a few
   ioctls, no `exec`, no cgo. On FreeBSD, fall back to what gopsutil exposes
   (speed/driver detail is thinner there; degrade gracefully, don't fail).
3. **sysctls:** direct `/proc/sys` reads (Linux) / `unix.Sysctl` (FreeBSD). Cheap.

If a field can't be read on a platform, omit it — the inventory is best-effort
and must never block or fail startup. gopsutil is the only new dependency, and it
lands in `shard-common/logging` (or a sibling `shard-common/hostinfo`) so all
five binaries inherit it identically.

**Why a log and not a metric:** these are high-cardinality *strings* (CPU model,
kernel release, NIC driver). As a metric they would be a single
`*_host_info{...}=1` info-gauge — acceptable, and worth mirroring the handful of
low-cardinality numerics (core count, rmem_max) as one info-gauge for dashboard
joins — but the descriptive payload belongs in a log event. Emit the log as the
source of truth; optionally mirror a slim `host_info` gauge.

## Recommended transport architecture (decided)

The backend question is answered so the emit-side contract is built right;
**rollout is the only thing deferred.**

```text
  app process                node-local collector             backend (later)
  ───────────                ────────────────────             ───────────────
  slog JSON ──▶ stdout ──▶  Grafana Alloy / OTel Collector ──OTLP──▶ Loki
                            (DaemonSet on k8s; systemd unit          (OTLP-native
                             on VMs; tails journald/stdout)           ingest, 3.x)
```

**Decision: OTLP is the wire standard; a node-local collector produces it; the
app does not.** Rationale, in priority order:

1. **Performance (the hard constraint).** The data-plane binaries
   (`shard-proxy`, `shard-listener`) must never block on log transport. An
   **in-process** OTLP gRPC exporter adds a batch queue + periodic network flush
   inside the hot binary; if the collector or network stalls, that backpressure
   reaches the process. Writing JSON to **stdout** is a buffered local write with
   no network, no TLS, no retry queue — the minimal-impact path "no matter
   what." The OTLP/batching/retry cost is paid **out-of-process** by the
   collector, which can stall, restart, or backpressure without touching the
   data plane.
2. **Standard + future-proof.** OTLP is the CNCF vendor-neutral standard; the
   collector can fan out to Loki, ELK, ClickHouse, or an OTLP SaaS with a config
   change and no app rebuild. The user's intuition is correct: **emit toward
   OTLP, let Loki ingest later** — Loki 3.x accepts OTLP natively, so the path is
   direct.
3. **One agent, three signals.** Grafana Alloy (or the OTel Collector) carries
   logs **and** the existing Prometheus metrics **and** node_exporter host
   telemetry (§3b) through one pipeline, with the same resource attributes — so
   logs, metrics, and host facts join on `service.instance.id` in one place.

**Collector choice — DECIDED: Grafana Alloy** (Apache-2.0). It fits the existing
Prometheus/Grafana stack and speaks OTLP + Loki + Prometheus + traces natively in
one agent. Deployed by the infra repos as a systemd unit (VMs) / DaemonSet (k8s);
it also runs/relabels node_exporter scrapes (§3b). The application stays
collector-agnostic — Alloy is a deployment choice, not a code dependency.

**Why not in-process OTLP at all?** For the non-hot binaries (`shard-manifest`,
`subtx-generator`) an in-process slog→OTLP bridge would be harmless, but mixing
two transport models across the fleet costs more in operational consistency than
it saves. Uniform "JSON to stdout, collector ships it" is simpler to reason
about at thousands of nodes. Recorded as explicitly rejected.

## Distributed tracing (control-plane only — never the packet hot path)

Maximum tracing is desired **subject to the hard rule that the data-plane hot
path is never touched.** Per-packet spans on a billion-tx/s forwarder are
impossible; so tracing is applied to the **control- and request-scoped flows**,
which are low-frequency and where causality across processes is the actual
operability win:

| Traced flow | Spans across | Why |
|-------------|--------------|-----|
| NACK → ACK/MISS → retransmit | listener → retry-endpoint → (multicast) | the canonical cross-process recovery path |
| Manifest adoption / auto-config | shard-manifest → proxy/listener appliers | quorum/hysteresis transitions are multi-step |
| Startup / drain lifecycle | within a process | bounds the inventory + config + ready sequence |
| Subtree/group join + leave | listener appliers | the `mld_max_msf`/join failures from §3(a) get span context |

Design constraints:

- **A `shard-common/tracing` package** mirrors the metrics OTLP pattern exactly:
  an OTel `TracerProvider` with an **opt-in** OTLP/gRPC span exporter behind the
  same `OTLP_ENDPOINT` (a new `-trace-sampling` / `TRACE_SAMPLING` ratio,
  default `0` = off). When disabled it installs a **no-op tracer** — zero
  allocation, zero cost — so a build with tracing compiled in but unconfigured
  pays nothing.
- **The forwarder receive/send loops, the per-flow SeqNum map, and reassembly
  hot paths take no span and no context** — enforced by keeping the tracer out of
  those packages entirely. Spans live only in control-plane packages
  (`nack`, `retransmit`, `beacon`, `manifest`, `server`, lifecycle in `main`).
- **Trace context rides existing protocol where it already exists** (NACK/ACK
  carry no spare bytes today, so cross-process linkage uses
  `trace.id`/`span.id` attributes logged on both ends and joined in the backend;
  a future BRC field could carry W3C `traceparent` if warranted — noted, not
  forced).
- **Export is out-of-process via Alloy** (OTLP traces), same isolation argument
  as logs: an exporter stall cannot reach the data plane because the traced
  flows are not on it.

This gives "maximum tracing" where it is safe and meaningful, and **none** where
it would cost throughput.

## What this phase does NOT do

- No shipping agent, DaemonSet, collector, or Loki **deployed yet** — that is the
  deferred rollout. The architecture above is fixed; only its Ansible/Helm
  wiring is later.
- No in-process OTLP **log** export, ever (see rationale above). The metrics OTLP
  path is untouched.
- No reimplementation of host/kernel telemetry — node_exporter owns it (§3b).

The output contract (JSON-to-stdout, identity-attributed, low-cardinality
messages, metric-aligned keys, one-shot host inventory) is fixed now precisely so
the deferred rollout is collector configuration, not application code.

## Log economy (brevity is a requirement)

At thousands of nodes, every steady-state log line is multiplied by the fleet and
paid for in NIC bandwidth, collector CPU, and store retention. Brevity is a
design constraint, not a style preference. Rules:

1. **Steady-state silence.** A healthy component emits its startup sequence
   (`starting` → `host.inventory` → capability summary → `ready`) and then
   **nothing** until a state change or anomaly. Liveness is the metrics plane's
   job, not a heartbeat log. No periodic "still alive" / "processed N" lines.
2. **No per-event logging on the data plane at Info.** Category-7 events are
   Debug and **sampled/rate-limited** (e.g. log-once-then-count: first ENOBUFS
   logged, subsequent ones counted and summarized on a timer). A per-packet log
   at line rate is an outage.
3. **Collapse boot chatter.** Today's ~8–12 separate "X enabled" Info lines
   become **one** `startup.config` event with the capabilities as boolean/value
   attributes. One line instead of a dozen, fully structured.
4. **Short, stable keys; no prose.** Message = low-cardinality identifier;
   variables in attributes; never restate base identity (it is auto-attached);
   never interpolate values into the message string.
5. **Errors deduplicate.** Repeated identical errors (a flapping iface, a down
   Redis) are rate-limited with an occurrence count, not emitted per failure.
6. **Right level by default.** `info` ships lifecycle + config + warnings +
   errors only — a near-empty stream in steady state. `debug` (opt-in, per-host,
   runtime-togglable via the LevelVar) is the only verbose mode.

These rules make JSON's per-line overhead irrelevant: the win is **far fewer
lines**, and the collector compresses what remains on the wire.

## Phasing

| Phase | Deliverable | Repos | Status |
|-------|-------------|-------|--------|
| 0 | This design doc | `bsv-multicast` | **this PR** |
| 1 | `shard-common/logging` (+ `hostinfo`) package; add gopsutil dep | `shard-common` | next |
| 2 | Wire all 5 binaries to it; add `-log-format`/`LOG_FORMAT` + `-log-level`/`LOG_LEVEL` (LevelVar) config; collapse boot lines into one `startup.config`; convert `subtx-generator` off plain `log` | all services | next |
| 3 | One-shot `host.inventory` event at startup (gopsutil + ethtool ioctls + sysctls) | all services | next |
| 4 | Category-8 in-process OS/NIC syscall logs at proxy/listener | `shard-proxy`, `shard-listener` | next |
| 5 | Runtime level control (SIGHUP + admin endpoint) | all services | next |
| 6 | `shard-common/tracing` (opt-in OTLP traces, no-op when off); spans on control-plane flows only | `shard-common` + all services | next |
| 7 | Slim `<prefix>_host_info` gauge mirror in each component | all services | next |
| — | **Collector rollout (Grafana Alloy → Loki) + node_exporter** | infra repos | **deferred — separate plan, architecture decided above** |

## Config surface (Phases 2 & 4)

Per [conventions.md § Configuration](../../multicast-skills/conventions.md) every
flag gets an UPPERCASE env equivalent, in the per-repo `config/` package.

| Flag | Env | Default | Meaning |
|------|-----|---------|---------|
| `-log-format` | `LOG_FORMAT` | `text` | `text` \| `json`. `json` is the fleet/aggregation format. |
| `-log-level` | `LOG_LEVEL` | `info` | `debug`\|`info`\|`warn`\|`error`. Supersedes the boolean `-debug` (kept as alias = `debug`). |
| `-trace-sampling` | `TRACE_SAMPLING` | `0` | Span sampling ratio `0.0`–`1.0`; `0` = tracing off (no-op tracer). Exports via `OTLP_ENDPOINT` when > 0. |

`-debug` is retained as a deprecated alias (`LOG_LEVEL=debug`) to avoid breaking
existing units; emits a category-3 warning when used. Tracing reuses the existing
`OTLP_ENDPOINT`; with `-trace-sampling 0` the tracer is a no-op and costs nothing.

## Cross-repo documentation checklist

Per [conventions.md § Cross-Repo Feature Documentation](../../multicast-skills/conventions.md#cross-repo-feature-documentation-load-bearing-checklist),
when Phases 1–4 ship:

- **shard-common**: `README.md` Packages table + `docs/` entry for the new
  `logging` (+ `hostinfo`) package (the identity/format/level contract and the
  `host.inventory` field list); note the new gopsutil dependency.
- **Each service repo**: `docs/configuration.md` (the two new flags, the
  `-debug` deprecation), `docs/architecture.md` (a "Logging" section naming the
  category-8 syscall sites for proxy/listener), `README.md` quick-start line.
- **Helm charts**: `values.yaml` `logFormat`/`logLevel` keys + `values.schema.json`
  enums (`{text,json}`, `{debug,info,warn,error}`); `README.md` values reference.
- **Infra repos**: `config.env.j2` gains `LOG_FORMAT`/`LOG_LEVEL`. (Collector +
  node_exporter roles are the deferred rollout phase, not here.)
- **multicast-skills**: add a `logging.md` skill (or a Logging section in
  `conventions.md`) capturing the taxonomy and attribute vocabulary so future
  call sites inherit the discipline.

## Open questions

1. **Collector**: Grafana Alloy vs. upstream OpenTelemetry Collector — both
   Apache-2.0, both OTLP→Loki capable; decide at rollout (lead: Alloy, for stack
   affinity). The backend itself is **decided** (OTLP → Loki); see
   [Recommended transport](#recommended-transport-architecture-decided).
2. **Cross-process trace context propagation** — NACK/ACK frames carry no spare
   bytes for a W3C `traceparent` today, so initial linkage is via logged
   `trace.id`/`span.id` attributes joined in the backend. A future BRC field
   could carry `traceparent` natively — noted, not forced. (Tracing itself is
   **decided** and in scope; see [Distributed tracing](#distributed-tracing-control-plane-only--never-the-packet-hot-path).)
3. ~~`host_info` mirror gauge~~ — **DECIDED: expose it.** Each component
   publishes a slim `<prefix>_host_info` info-gauge (value 1, low-cardinality
   labels: core count, `rmem_max`, link speed, kernel, carrying the same
   `service.instance.id`) for dashboard joins; the descriptive payload stays in
   the `host.inventory` log event.
