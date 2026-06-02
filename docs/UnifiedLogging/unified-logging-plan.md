# Unified Component Logging Plan

- Status: **Proposed** (design only — no code yet)
- Scope (this phase): the **emit side** only. Standardize how every component
  *produces* logs so they are structured, self-identifying, and consistent.
- **Explicitly deferred to a later phase:** central aggregation, shipping
  agents, and a log store (Loki / OTLP / ELK). This document's job is to make
  that later phase a drop-in by fixing the on-host output contract now, so no
  application call site has to change when collection is added.
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
| 1 | **Lifecycle** | Info | no | `shard-proxy starting`, `received signal, starting drain`, `drain complete`, `all workers stopped`, `shutdown complete` | base only |
| 2 | **Config / capability** | Info | no | `BRC-130 fragmentation enabled`, `ingress TxID dedup enabled`, `multicast egress source bound`, `manifest consumer enabled`, `metrics server listening` | the toggled setting + value |
| 3 | **Config warnings** | Warn | no | `deprecated -txid-dedup-* flags in use`, `unknown cache backend, using memory`, `redis dedup unavailable` | the offending value + fallback |
| 4 | **Auto-config / adoption** | Info / Warn | no | `auto-config adopted new ShardBits (restart mode)`, `auto-join applied`, `live-resharding: bridging engine installed` | `shard_bits`, `mc_group_id`, `source_mode`, `epoch`, quorum |
| 5 | **Fatal startup** | Error → exit | no | `configuration error`, `multicast interface not found`, `metrics init failed`, `invalid bind-source` | `error`, offending input |
| 6 | **Runtime subsystem error** | Error | no | `worker exited with error`, `beacon listener error`, `sender exited with error`, `TCP ingress exited with error` | `error`, subsystem id (`worker`, `iface`, `group`) |
| 7 | **Protocol / data-plane event** | Debug (Warn on anomaly) | **yes** | `subtree_announce: decode error`, `… sender rejected by filter`, `… datagram too short`; gap-detected, NACK-sent, retransmit | `group`, `seq`, `txid`, `frame_type`, `proto` — **rate-limited/sampled** |
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
  exact failure mode `conventions.md`/the SSM plan warns about; today it is a
  generic `auto-join AddGroup failed`.

These are cheap (error paths, not the success hot path) and turn opaque drops
into actionable, host-attributed events.

**(b) Out-of-process, host-attributed — DEFER (note only).** NIC ring drops
(`ethtool -S`), `/proc/net/snmp6`, `/proc/net/softnet_stat`, conntrack table
full, `dmesg`/kernel-facility messages, FreeBSD `netstat -i` / kern syslog.
These cannot come from the Go binaries. They belong to the deferred infra phase
(a host metrics exporter for the numbers; the log shipper picking up the kernel
log facility for the events). Listed here so the taxonomy is complete; **no work
in this phase.**

## What this phase does NOT do

- No shipping agent, DaemonSet, Loki/OTLP/ELK, or any collector. Logs continue
  to land wherever they land today (journald / `/var/log/messages` / container
  stdout).
- No OTLP **log** export. The metrics OTLP path is untouched. (When aggregation
  is designed, an opt-in slog→OTLP bridge mirroring the existing
  `OTLP_ENDPOINT` metrics toggle is the natural fit — noted, not built.)
- No host/kernel collector — see §3(b).

The output contract (JSON-to-stdout, identity-attributed, low-cardinality
messages, metric-aligned keys) is fixed now precisely so the deferred phase is
configuration, not code.

## Phasing

| Phase | Deliverable | Repos | Status |
|-------|-------------|-------|--------|
| 0 | This design doc | `bsv-multicast` | **this PR** |
| 1 | `shard-common/logging` package | `shard-common` | next |
| 2 | Wire all 5 binaries to it; add `-log-format`/`LOG_FORMAT` + `-log-level`/`LOG_LEVEL` (LevelVar) config; convert `subtx-generator` off plain `log` | all services | next |
| 3 | Category-8 in-process OS/NIC logs at proxy/listener syscall sites | `shard-proxy`, `shard-listener` | next |
| 4 | Runtime level control (SIGHUP + admin endpoint) | all services | next |
| — | **Aggregation / shipping / store** | infra repos | **deferred — separate plan** |

## Config surface (Phases 2 & 4)

Per [conventions.md § Configuration](../../multicast-skills/conventions.md) every
flag gets an UPPERCASE env equivalent, in the per-repo `config/` package.

| Flag | Env | Default | Meaning |
|------|-----|---------|---------|
| `-log-format` | `LOG_FORMAT` | `text` | `text` \| `json`. `json` is the fleet/aggregation format. |
| `-log-level` | `LOG_LEVEL` | `info` | `debug`\|`info`\|`warn`\|`error`. Supersedes the boolean `-debug` (kept as alias = `debug`). |

`-debug` is retained as a deprecated alias (`LOG_LEVEL=debug`) to avoid breaking
existing units; emits a category-3 warning when used.

## Cross-repo documentation checklist

Per [conventions.md § Cross-Repo Feature Documentation](../../multicast-skills/conventions.md#cross-repo-feature-documentation-load-bearing-checklist),
when Phases 1–4 ship:

- **shard-common**: `README.md` Packages table + `docs/` entry for the new
  `logging` package (the identity/format/level contract).
- **Each service repo**: `docs/configuration.md` (the two new flags, the
  `-debug` deprecation), `docs/architecture.md` (a "Logging" section naming the
  category-8 syscall sites for proxy/listener), `README.md` quick-start line.
- **Helm charts**: `values.yaml` `logFormat`/`logLevel` keys + `values.schema.json`
  enums (`{text,json}`, `{debug,info,warn,error}`); `README.md` values reference.
- **Infra repos**: `config.env.j2` gains `LOG_FORMAT`/`LOG_LEVEL`. (Shipper
  roles are the deferred phase, not here.)
- **multicast-skills**: add a `logging.md` skill (or a Logging section in
  `conventions.md`) capturing the taxonomy and attribute vocabulary so future
  call sites inherit the discipline.

## Open questions (for the deferred aggregation phase)

1. Backend: Loki (pairs with existing Prometheus/Grafana, label-based, cheap at
   fleet scale) vs. OTLP-collector (single protocol with metrics) vs. ELK
   (heaviest). Deferred — recorded here so the emit-side JSON contract above
   stays backend-neutral.
2. Trace/correlation id for request-scoped flows (NACK → retransmit spanning
   listener + retry-endpoint). Out of scope here; the attribute vocabulary
   leaves room for a `trace.id`/`correlation.id` key.
