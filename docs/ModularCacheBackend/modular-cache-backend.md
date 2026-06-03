# Modular Cache Backend

**Status:** Implemented (shard-common `cache` package + every cache-enabled
consumer). Opt-in per service; defaults preserve prior behaviour.

This document is the canonical reference for the pluggable cache backend used
by the cache-enabled components — **shard-proxy**, **shard-listener**, and
**retry-endpoint**. Everything else (per-repo `docs/configuration.md`, Helm
`values.yaml`, infra roles) links here.

## Motivation

Two caching surfaces existed in the stack, only one of which was modular:

| Surface | Access pattern | Components | Old implementation |
|---------|----------------|------------|--------------------|
| Dedup / claim store | `SetNX(key, ttl) → won?` + async `Mark` | proxy ingress, listener egress + courtesy mark | `shard-common/txidset` with a hardcoded `*redis.Client` tier-2 |
| Frame KV cache | `Store / Retrieve / Delete` (+ SetNX dedup) | retry-endpoint | repo-local `cache.Cache` (memory + redis) |

Both are the same primitive — **TTL'd key-value with an atomic create-only
operation** — so they now share one backend interface. This lets operators
choose the substrate per deployment (single Redis for small fabrics; a
horizontally-scaled store for large ones) without touching component code.

## The interface — `shard-common/cache`

```go
type Backend interface {
    SetNX(ctx, key, val []byte, ttl time.Duration) (bool, error) // atomic create-only
    Set(ctx, key, val []byte, ttl time.Duration) error           // unconditional write
    Get(ctx, key []byte) ([]byte, error)                         // (nil,nil) on miss
    Del(ctx, key []byte) error
    Healthy(ctx) bool   // cold-path /readyz probe; never on the hot path
    Close() error
}
```

- **Keys/values are opaque `[]byte`.** Callers own namespacing — backends apply
  no prefixes. Cross-service key shapes are unchanged (`bsp:tx:`, `bsl:egr:…`,
  `bre:frame:`, `bre:dedup:`).
- **Miss is `(nil, nil)`, never an error.**
- **Fail-open contract.** Dedup callers treat a `SetNX` error as *proceed*; the
  frame store treats `Get`/`Set` errors as a miss. A backend outage must never
  stop frames from forwarding.

Construction is via `cache.Open(ctx, cache.Config)` (fail-closed: dials and
verifies redis/aerospike, returns an error rather than a degraded backend).
`Config.Backend` selects `memory | redis | aerospike | none`.

## Shipped backends

| Backend | Package | SetNX | TTL | Use case |
|---------|---------|-------|-----|----------|
| `memory` | `cache/memory` | per-process map (striped, 64 shards) | sub-second OK | dev/CI; per-instance retry frame store |
| `redis` | `cache/redis` | `SET NX EX` | sub-second OK | **default for scale-up** — any Redis-protocol server |
| `aerospike` | `cache/aerospike` | `CREATE_ONLY` write | **whole seconds, floor 1s** | largest fleets; auto-sharded, hybrid RAM/SSD |
| `none` | — | — | — | tier-1 LRU only (dedup); rejected by the frame store |

### Redis backend covers more than Redis

The `redis` backend speaks the Redis wire protocol, so it targets **Redis,
Valkey, Dragonfly, or a Redis Cluster by address alone** — no code change:

- **Valkey** (BSD-licensed Redis fork) — future-proofs licensing; drop-in.
- **Dragonfly** — multi-threaded, scales vertically far past single-thread Redis.
- **Redis Cluster** — horizontal sharding; all operations here are single-key,
  so cluster slot routing is transparent.

### Aerospike Community Edition — constraints

- **TTL granularity is whole seconds, floor 1s.** Sub-second TTLs are not
  representable. Every TTL in this stack (dedup 10m/60s, frame caches 60s–10m,
  retransmit dedup-window 60s) satisfies this; the backend rounds up to the
  second.
- **Requires an operator-provisioned namespace** (`-aerospike-namespace`,
  default `cache`). CE has **no XDR / rack-awareness** — for multi-region
  replication use a Redis-compatible backend or Aerospike Enterprise.
- A shared Aerospike frame store also **raises retry-endpoint cache-hit ratio**:
  frames become cross-instance instead of per-pod.

### Backends evaluated but not shipped

- **memcached** (`add` = SetNX, native TTL) — viable lightweight dedup-only
  option; client-sharded, no persistence. A backend could be added if demand
  appears.
- **ScyllaDB / Cassandra** LWT (`IF NOT EXISTS`) — Paxos per claim is too costly
  for the hot dedup gate. Not recommended.
- **NATS JetStream KV** — weaker high-rate SetNX semantics. Not recommended.

## Performance — no hot-path detriment

The per-packet dedup gate (proxy ingress, listener egress) is served by the
**tier-1 in-process LRU inside `txidset`**, which is *not* behind `Backend`. A
`Backend` call happens only on a tier-1 miss (a novel TxID) — exactly as before
the refactor. `txidset` was already consumed through an interface
(`forwarder.TxidDedup`), so there is no new dispatch on the hot path.

Benchmarks (`shard-common/txidset/bench_test.go`):

```
BenchmarkClaimLocalHit-12     151.8 ns/op   0 B/op   0 allocs/op   # tier-1 hit, no backend touched
BenchmarkClaimLocalMiss-12    328.7 ns/op   0 B/op   0 allocs/op   # tier-1 insert, local-only mode
```

The tier-1 hit path is allocation-free and never reaches the backend. Backend
method signatures take `[]byte` so dedup callers add no conversions; the
existing `prefix + hex(txid)` allocation stays on the cold (miss) path.

## Per-component wiring

| Component | Surface | Store(s) | Config prefix |
|-----------|---------|----------|---------------|
| shard-proxy | ingress dedup | one `txidset.Store` (`bsp:tx:`) | `-txid-dedup-*` |
| shard-listener | egress dedup + courtesy ingress mark | two `txidset.Store` (`bsl:egr:…`, `bsp:tx:`) | `-egress-dedup-*`, `-ingress-set-*` |
| retry-endpoint | frame cache + retransmit dedup | `cache.Store` adapter over one `Backend` (`bre:frame:`, `bre:dedup:`) | `-cache-backend`, `-redis-addr`, `-aerospike-*` |

- `txidset.Config` gained a `Backend cache.Backend` field. The deprecated
  `RedisAddr` field still works (builds an internal redis backend), so existing
  deployments are unaffected.
- The listener's two stores are addressed independently — either may use a
  different backend or endpoint.
- The retry endpoint reuses one backend for both the frame store and the
  cross-instance dedup gate (distinct prefixes) when the backend is
  redis/aerospike. With `memory` frames, a separate `-redis-addr` still enables
  the dedup gate.

## Configuration

Every flag has an UPPERCASE env-var equivalent.

### shard-proxy (ingress dedup)

| Flag | Default | Notes |
|------|---------|-------|
| `-txid-dedup-backend` | infer (`redis` if addr set, else `none`) | `redis\|aerospike\|memory\|none` |
| `-txid-dedup-redis-addr` | "" | Redis/Valkey/Dragonfly address |
| `-txid-dedup-aerospike-hosts` | "" | comma-separated `host:port`; required for aerospike |
| `-txid-dedup-aerospike-namespace` | `cache` | |
| `-txid-dedup-aerospike-set` | `bsp` | |

### shard-listener (egress dedup + ingress mark)

Two independent stores, each with its own backend selector:
`-egress-dedup-backend` / `-egress-dedup-redis-addr` /
`-egress-dedup-aerospike-{hosts,namespace,set}` and the `-ingress-set-*`
equivalents.

### retry-endpoint (frame cache + dedup)

| Flag | Default | Notes |
|------|---------|-------|
| `-cache-backend` | `memory` | `memory\|redis\|aerospike` |
| `-redis-addr` | "" | required for `redis`; also enables dedup when backend=`memory` |
| `-aerospike-hosts` | "" | required for `aerospike` |
| `-aerospike-namespace` | `cache` | |
| `-aerospike-set` | `bre` | |
| `-cache-dial-timeout` | `1s` | |
| `-cache-op-timeout` | `1s` | per-op ceiling |

## Rollout

1. **Release `shard-common`** with the `cache` package and the `txidset`
   refactor (tag + `update-shard-common.sh`). Dependents pin the new version;
   until then they build only inside the Go workspace.
2. **proxy / listener / retry-endpoint** pick up the new flags. Defaults are
   unchanged (proxy/listener fail-open to tier-1 LRU; retry defaults to
   `memory`).
3. **Aerospike adopters** provision the namespace (infra role) and set
   `-cache-backend=aerospike` / `*-backend=aerospike`.

## Cross-repo surfaces (complete)

- **Go services** — `shard-proxy`, `shard-listener`, `retry-endpoint`: flags +
  `docs/configuration.md` + `docs/architecture` notes.
- **shard-common** — `cache` package + README packages table.
- **Helm charts** — `retry-endpoint-helm` (`cacheBackend` + `aerospike*`),
  `shard-proxy-helm` (`txidDedup.backend` + `aerospike*`), `shard-listener-helm`
  (`egressDedup*` / `ingressSet*` backend + `aerospike*`): `values.yaml`,
  `values.schema.json` enums, and README values reference. Operators passing
  comma-separated `aerospikeHosts` via `--set` must escape commas.
- **Infra** — `ingress-infra`, `listener-infra`, `retransmission-infra`:
  `group_vars` + `config.env.j2` backend/aerospike vars and a `docs/networking.md`
  cache-backend connectivity section. `retransmission-infra` ships an optional
  `aerospike` Ansible role (CE install + namespace provisioning, gated on an
  `aerospike_nodes` inventory group). `multicast-kube-infra` documents deploying
  the backend as an in-cluster workload.
- **skills** — `architecture.md` (freecache → modular note) and `conventions.md`
  (Modular Cache Backend section).
