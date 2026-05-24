# Roadmap

## Phase 0 — Documentation

**Status: complete (revised 2026-05 to match shipped harness + adopt Multus default).**

- [x] `containerization/` doc tree in `bitcoin-multicast`
- [x] Revision pass aligning docs with implemented `bitcoin-multicast-test/harness/`
- [x] Multus designated as default k0s networking mode

---

## Phase 1 — Canonical Dockerfile + Dagger CI

**Status: complete (2026-05).** All four component repos now ship a canonical multi-stage Dockerfile and a Dagger-driven CI pipeline. The Go harness in `bitcoin-multicast-test/harness/build/build.go` continues to bake images via host cross-compile for fast E2E loops; the canonical Dockerfiles are the publish-ready artefact (Phase 6).

Shipped per repo (`bitcoin-shard-proxy`, `bitcoin-shard-listener`, `bitcoin-retry-endpoint`, `bitcoin-subtx-generator`):
- `Dockerfile` — `golang:1.25-alpine` builder with module/build cache mounts → `gcr.io/distroless/static:nonroot` runtime, `USER nonroot:nonroot`, no in-image `ENV` defaults. Buildx multi-arch ready (`TARGETOS`/`TARGETARCH`).
- `bitcoin-shard-proxy`: dropped `ubuntu:24.04` + `apt-get` layer; binary at `/usr/local/bin/bitcoin-shard-proxy`.
- `bitcoin-shard-listener`: distroless retained; binary moved to `/usr/local/bin/bitcoin-shard-listener` (chart relies on `ENTRYPOINT`, no breakage).
- `bitcoin-retry-endpoint`: new Dockerfile; binary at `/usr/local/bin/bitcoin-retry-endpoint`.
- `bitcoin-subtx-generator`: single image bundling all four `cmd/` binaries (`subtx-gen`, `send-anchor-frame`, `send-block-announce`, `send-subtree-data`) — **no `ENTRYPOINT`**; consumer (Helm chart `mode`, `docker run --entrypoint`) selects the binary.
- `.dockerignore` per repo, omitting `.git`, `ci/`, docs, `*.md`, etc.

Dagger pipeline (`ci/` subdirectory in each repo, separate Go module pinned to `dagger.io/dagger v0.20.8`):
- Subcommands: `unit`, `lint`, `vuln`, `tidy`, `build`, `image`, `all`, `dev-shell`. The `image` subcommand reuses the canonical `Dockerfile` via Dagger's `Directory.DockerBuild()` (single image source of truth) and supports `-export=<path>` for OCI tarballs and `-address=<ref>` for registry publish.
- Shared Go module/build/golangci-lint cache volumes across stages.
- Reproduces the existing `replace github.com/lightwebinc/bitcoin-shard-common=…` dance inside the container without mutating the host repo.

Makefile (additive — existing host-side `build`, `test`, `lint`, `test-e2e`, `install-source`, `hooks` targets preserved):
- `make ci` (full), `ci-unit`, `ci-lint`, `ci-vuln`, `ci-tidy`, `ci-build`, `ci-image`, `ci-export`, `ci-publish`, `ci-shell`, `fmt`, `help`.
- Variables: `VERSION`, `TAG`, `IMAGE`, `COMMON`. `make ci-publish IMAGE=ghcr.io/foo/bar TAG=v0.1.0` ready for Phase 6.
- Pipeline runs via `GOWORK=off go run .` from `ci/` so the parent `go.work` is not pulled in.

GitHub Actions:
- `ci.yml` collapsed to a single `make ci` step calling Dagger (with sibling `bitcoin-shard-common` checkout). Per-repo, ~120 lines of YAML reduced to ~30.
- Listener and proxy keep a separate `e2e` job for host-side multicast smoke (`make test-e2e`).
- `release.yml` and `codeql.yml` unchanged.

README updates:
- `bitcoin-retry-endpoint/README.md`: prominent **NACK_ADDR (required in production)** section explaining the SLAAC source-address mismatch failure mode and the listener-side ACK rejection.
- `bitcoin-subtx-generator/README.md`: **Container image** section listing all four binaries and the no-`ENTRYPOINT` contract.
- Proxy and listener READMEs: short Container image notes (distroless/nonroot, no in-image `ENV` defaults).

Caveats:
- Image build not yet pushed anywhere (Phase 6 gate).
- `bitcoin-shard-proxy` image dropped its in-image `ENV` defaults; bare `docker run` users must now pass env explicitly. Helm chart sets all values, so the chart-driven path is unchanged.
- Dagger CLI itself is not required on the runner — the SDK launches the engine on connect (Docker required, present on `ubuntu-latest`).

Dependencies satisfied: none.

---

## Phase 2 — Go harness + Docker E2E scenarios

**Status: complete.** Implemented in `bitcoin-multicast-test/harness/`. 40 scenarios pass via `make test`. BGP scenarios 40–42 ship as `t.Skip` stubs pending Phase 4.5.

Shipped:
- `harness/driver/driver.go` + `harness/driver/docker/` (bridge + container lifecycle in Go)
- `harness/env/` — `Env`, `tc netem`, `ip6tables` primitives
- `harness/metrics/` — `expfmt` scrape + ratio/threshold assertions
- `harness/build/` — cross-compile via `go.work` + distroless bake
- 40 scenarios in `harness/scenarios/` driven by `make test`

Not implemented (intentional deltas vs. original plan):
- No `docker-compose` files. `docker run` from Go is sufficient.
- No LXD driver. See [lxd-coexistence.md](lxd-coexistence.md).
- No `cmd/run-scenario/` CLI — `go test ./harness/scenarios/...` is the entry point.

---

## Phase 3 — Self-hosted CI integration

**Target: all component repos + `bitcoin-multicast-test`**

Deliverables:
- Self-hosted runner registered with labels `[docker, mcast-fabric, lxd]`
- `.github/workflows/ci.yml` updated in each component repo:
  - Tier 1 (hosted): unit + lint — already in place, no change
  - Tier 2 (self-hosted docker): Docker E2E via Dagger
- `bitcoin-multicast-test` CI: nightly LXD run-all.sh + Docker harness
- Dagger `integration` subcommand wiring Go harness via Docker driver

Dependencies: Phase 2.

---

## Phase 4 — Helm charts (scaffold + lint CI)

**Status: complete (2026-05).** Four `-helm` repos scaffolded, linted, and rendering across all networking modes / workload types. See [helm-charts.md](helm-charts.md) for the per-chart values surface.

Shipped:
- `bitcoin-shard-proxy-helm/` — Deployment; full `.config` flag surface; HPA/PDB/ServiceMonitor/NetworkPolicy gated
- `bitcoin-shard-listener-helm/` — Deployment **or** DaemonSet via `workloadType`; `NUM_WORKERS=1` hardcoded to avoid SO_REUSEPORT multicast duplication
- `bitcoin-retry-endpoint-helm/` — Deployment; `config.nackAddr` required (effectively); no Redis subchart bundled (operator-managed)
- `bitcoin-subtx-generator-helm/` — Deployment **or** Job via `workloadType`; CLI args renderer (binaries are flag-only) covering all four `cmd/` modes
- All charts: `Chart.yaml` (`kubeVersion >= 1.27.0-0`, ArtifactHub annotations), `values.schema.json`, `_helpers.tpl`, `NOTES.txt`, `serviceaccount.yaml`, `service.yaml`, optional `hpa.yaml` / `pdb.yaml` / `servicemonitor.yaml` / `networkpolicy.yaml`, and `templates/tests/test-metrics-endpoint.yaml`
- `networking.mode` toggle (`multus` | `host` | `unicast`) wired on every workload template
- `.github/workflows/lint.yml` runs `helm lint --strict` + multi-permutation `helm template` smoke renders on every push/PR (hosted runner)
- `.github/workflows/release.yml` — `workflow_dispatch` gated (`RELEASE` confirm, `production` environment); chart-releaser/GH Pages step **removed** (OCI-only approach)
- `cr.yaml` retained (GH Pages restore reference); OCI push to `ghcr.io/lightwebinc/charts` **enabled and shipped** — see Phase 6
- `LICENSE` (Apache-2.0, Lightweb Inc.) + `NOTICE` per chart repo
- Component repos cross-link the charts from `README.md` and `docs/configuration.md`

Phase 1 has now shipped the canonical Dockerfiles, so `helm install` is unblocked once images are published (Phase 6).

Dependencies: Phase 1 — **satisfied**.

---

## Phase 4.5 — Multus enablement + BGP scenarios

**Status: in progress.** Multus + NAD wiring shipped under
[`bitcoin-multicast-kube-infra`](https://github.com/lightwebinc/bitcoin-multicast-kube-infra)
(`platform/multus/`, `platform/nads/`). Docker harness BGP scenarios remaining.

**Targets: `bitcoin-multicast-test/harness/`, `*-helm` charts, k0s lab.**

Deliverables:
- Multus install verified on the k0s lab cluster (`helm install multus ...`).
- `mcast-fabric`, `bgp-transit`, `bgp-ibgp` `NetworkAttachmentDefinition`s applied.
- Each chart's `networking.mode: multus` path smoke-tested end-to-end.
- Harness gains additional Docker user-defined networks (`bgp-transit`, `bgp-ibgp`) and FRR + BIRD2 sidecar container images.
- BGP scenarios 40–42 lifted out of `t.Skip` and made green in the harness.
- BGP scenarios continue to run in `vm-lab/` as fidelity reference.

Dependencies: Phase 4 (charts must expose `networking.mode`).

---

## Phase 5 — k0s reference deployment (Multus default)

**Status: in progress.** Reference implementation lives in
[`bitcoin-multicast-kube-infra`](https://github.com/lightwebinc/bitcoin-multicast-kube-infra) —
distribution-agnostic repo with `distributions/k0s/` (k0sctl-driven), modular
`platform/` (CNI, Multus, ESO, NADs), and Helmfile-based `apps/` composition.

**Target: lab host (co-located with LXD)**

Deliverables:
- k0s controller + worker nodes deployed on lab host or dedicated VMs
- Multus + NADs applied (from Phase 4.5)
- Node labels applied: `bitcoin-mcast/role`, `bitcoin-mcast/node`, `bitcoin-mcast/fabric-iface`
- Helmfile-based composition wiring all four charts with `networking.mode: multus`
- Verified: end-to-end multicast delivery via macvlan `net1` on real fabric
- Verified: NACK recovery + beacon discovery in k0s environment
- External Prometheus reconfigured to scrape primary-CNI pod IPs (k8s SD) or Service ClusterIPs
- `vm-lab/` scenarios continue passing in parallel
- Optional: smoke-test `networking.mode: host` fallback path on a single-NIC worker

Dependencies: Phase 4.5.

---

## Phase 6 — Gated publish (image + chart) — requires explicit approval

**Status: partial (2026-05).** Helm chart OCI release shipped. Docker image publish pending.

**Done:**
- Helm OCI push to `oci://ghcr.io/lightwebinc/charts/<name>:0.1.0` — all four charts released via `workflow_dispatch` (`RELEASE` confirm)
- GH Pages / chart-releaser approach dropped in favour of OCI-only

**Remaining:**
- OCI image push to `ghcr.io/lightwebinc/bitcoin-shard-proxy`, etc. (Docker images, not charts)
- Semantic versioning: `v0.1.0` image tag applied to component repos
- All triggered only by `workflow_dispatch` with `confirm: RELEASE`

**This phase does not start without explicit approval.** No automated tag-based trigger.

Dependencies: Phase 5 (k0s deployment verified), all CI tiers green.

---

## Phase 7 — Unicast egress mode (future)

**Target: `bitcoin-shard-proxy`**

Deliverables:
- `EGRESS_MODE=unicast-list` flag + `EGRESS_TARGETS=host:port,...`
- Proxy sends unicast UDP to explicit listener list instead of multicast groups
- Listener config: no MLD join needed; `LISTEN_PORT` unchanged
- Docker/k0s: remove `hostNetwork: true` requirement for proxy and listener
- Standard CNI network for all pods
- Helm chart updates: `hostNetwork` becomes optional (default false in unicast mode)
- Docker compose: remove `NET_ADMIN` cap for listener
- CI: add unicast-mode E2E scenario

This phase is scoped for cloud portability but intentionally deferred. It does not affect LXD or multicast deployments.

### Phase 7b — Local socket transport (collapsed proxy + listener)

**Target: `bitcoin-shard-proxy`, `bitcoin-shard-listener`, both Helm charts**

**Use case:** a single-host deployment that collapses the proxy and the listener into one pod (or one VM) and exposes a **full-duplex local API to client processes** on that host. The fabric NIC, MLD joins, and multicast plumbing become an implementation detail of the pod; the client speaks only AF_UNIX. This unlocks:
- **Edge / single-tenant deployments** where one consumer process attaches to the feed without any IPv6 multicast configuration.
- **Sidecar pattern** in Kubernetes: a workload pod runs the listener as a sidecar, shares an `emptyDir` mount for the socket dir, and consumes frames over `/run/bsl/data.sock` — no `hostNetwork`, no Multus.
- **Client-side NACK / control flow** over a paired ingress socket on the same host, providing duplex semantics without exposing a UDP port to the local node.

#### Viability assessment

| Concern | Verdict | Notes |
|---|---|---|
| Message framing | ✅ Already abstracted | Listener `egress/egress.go` (`Sender`) and proxy ingress reader both treat the transport as a stream of complete frames. AF_UNIX `SOCK_DGRAM` preserves message boundaries 1:1 with UDP; `SOCK_SEQPACKET` adds connection state and ordering. Either drops in behind the existing `EGRESS_PROTO` switch with no frame-format change. |
| Throughput | ✅ Higher than UDP loopback | AF_UNIX dgram skips the IP stack and routing decision; measured ~2–3× the small-packet throughput of `udp` to `[::1]` on Linux, with lower jitter. No fragmentation budget needed. |
| Fan-out (multiple clients) | ⚠️ Manual | AF_UNIX dgram is unicast. The listener already supports a single downstream egress target — for >1 local consumer, the listener writes to a list of sockets (`EGRESS_TARGETS=unix:/run/bsl/a.sock,unix:/run/bsl/b.sock`) replicating the per-listener fan-out that the proxy does for multicast. Acceptable up to ~16 local consumers; beyond that, use a shared-memory ring (out of scope for Phase 7b). |
| Backpressure | ✅ Bounded | `SO_SNDBUF` / `SO_RCVBUF` on AF_UNIX dgram are tunable; on overflow the kernel returns `ENOBUFS` (dgram) or blocks (seqpacket). Listener's existing per-worker `Sender` already handles write errors and accounts them via `egress_errors_total`. |
| Filesystem lifecycle | ⚠️ Operator | Socket path must be in a writable mount (`emptyDir`, `hostPath`, or tmpfs). `unlink()` on shutdown handled by the binary; charts must set `socketDir` and mount it on both the producer and the consumer container. |
| Security | ✅ Filesystem ACL | No network ACL needed — `chmod`/`chown` on the socket path. Charts expose `socketMode` (default `0660`) and `socketGroup`. |
| Full-duplex (client → proxy ingress) | ✅ Symmetric | Proxy's UDP frame ingress (`UDP_LISTEN_PORT`, `worker/worker.go`) and TCP ingress (`TCP_LISTEN_PORT`, `worker/tcp.go`) both read complete frames from a `net.PacketConn` / `net.Listener`. Adding `UNIX_LISTEN_PATH` + `UNIX_TCP_LISTEN_PATH` (seqpacket) follows the same pattern. **Separate sockets per direction** keeps the duplex flows independent — client publishes via `/run/bsp/ingress.sock`, consumes via `/run/bsl/egress.sock`. |
| Retry endpoint / NACK | ➖ Out of scope | NACK plane stays on IPv6 unicast inside the pod. Collapsed deployments may also collapse the retry endpoint, but that is independent of the data-plane transport. |
| Beacon discovery | ➖ Unchanged | Beacons remain multicast on the fabric NIC owned by the collapsed pod; the client never sees them. |
| Helm / chart surface | ✅ Additive | New values: `egress.mode: udp|tcp|unix-dgram|unix-seqpacket`, `egress.socketPath`, `egress.socketMode`, `egress.socketGroup`. `networking.mode: collapsed` shortcut wires the proxy and listener into a single Deployment with a shared `emptyDir`. |

**Concluding viability: high.** All required hooks already exist in the codebase (`Sender` proto switch, proxy UDP/TCP ingress workers, distroless image, non-root user). The change is a transport addition, not a protocol or frame-format change.

#### Deliverables

- `bitcoin-shard-listener`:
  - `Sender` (egress/egress.go) gains `unix-dgram` and `unix-seqpacket` proto branches via `net.DialUnix` (auto-reconnect on `ECONNREFUSED`, same retry semantics as the existing TCP path).
  - `EGRESS_ADDR` accepts `unix:/path/to/sock` in addition to `host:port`; `EGRESS_PROTO=unix-dgram|unix-seqpacket` validated in `config/config.go`.
  - Optional multi-target list (`EGRESS_TARGETS`) for local fan-out to multiple client sockets.
  - `HEADER_EGRESS_*` mirror the same surface so the SPV header stream can also run over AF_UNIX.
- `bitcoin-shard-proxy`:
  - `UNIX_LISTEN_PATH` (`SOCK_DGRAM`) and `UNIX_SEQPACKET_LISTEN_PATH` configs added to the UDP / TCP worker constructors; reuses `forwarder.Process` unchanged.
  - Startup creates parent dirs, `unlink()`s stale sockets, applies `socketMode`/`socketGroup`.
- Helm charts:
  - `bitcoin-shard-listener-helm`: `egress.mode`, `egress.socketPath`, `egress.socketMode`, `egress.socketGroup`; renders an `emptyDir` mount when the mode is AF_UNIX.
  - `bitcoin-shard-proxy-helm`: `ingress.unix` and `ingress.unixSeqpacket` value blocks.
  - New `bitcoin-collapsed-helm` chart (or composite values in proxy chart) bundling proxy + listener in one Deployment with the socket dir shared via `emptyDir`. Multicast fabric attachment (`networking.mode: multus|host`) still required at the pod boundary.
- Harness:
  - `harness/driver/docker` gains a `volumes:` mount for the socket dir between two collapsed containers.
  - New scenario `harness/scenarios/scenarioNN_collapsed_unix_test.go`: proxy + listener on the same docker network with AF_UNIX data plane, verifies duplex (frames downstream, NACK / control upstream) and metric parity with the multicast baseline.
  - Microbench (`harness/perf/`) comparing `udp` loopback vs `unix-dgram` at 1 M fps.
- Documentation:
  - `bitcoin-multicast/containerization/composition-spec.md` — new "Collapsed local-socket profile" section.
  - `bitcoin-shard-listener/docs/configuration.md` — `EGRESS_PROTO` table extended.
  - `bitcoin-shard-proxy/docs/configuration.md` — `UNIX_LISTEN_PATH` documented.

Dependencies: Phase 1 (canonical image is the deployment unit). Independent of Phase 7 unicast-egress work — the two modes can ship in either order.

Out of scope for Phase 7b (candidates for a later phase):
- Shared-memory ring transport (mmap + atomic head/tail) for >16 local consumers or zero-copy receive.
- AF_UNIX between proxy and a co-located retry endpoint (current NACK plane is IPv6 unicast and works unmodified inside a single pod).
- Windows / non-Linux support (AF_UNIX dgram semantics differ).

---

## Timeline estimate

| Phase | Effort | Blocking |
|---|---|---|
| 0 | 1 session | — |
| 1 | **done** | — |
| 2 | **done** | — |
| 3 | 1–2 sessions | — (harness ships, Dagger wrapping only) |
| 4 | **done** | Phase 1 |
| 4.5 | 2–4 sessions | Phase 4 |
| 5 | 2–3 sessions | Phase 4.5 |
| 6 | **partial** (charts done; images pending approval + Phase 5) | Phase 5 |
| 7 | 3–5 sessions | user decision |
| 7b | 2–3 sessions | Phase 1 (independent of Phase 7) |

Phases 1, 3 and 4 can proceed in parallel. Phase 4.5 unblocks the BGP scenarios and the k0s Multus default. Phase 7b (local socket transport) is independent of Phase 7 and can ship first.
