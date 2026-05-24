# Roadmap

## Phase 0 — Documentation

**Status: complete (revised 2026-05 to match shipped harness + adopt Multus default).**

- [x] `containerization/` doc tree in `bitcoin-multicast`
- [x] Revision pass aligning docs with implemented `bitcoin-multicast-test/harness/`
- [x] Multus designated as default k0s networking mode

---

## Phase 1 — Canonical Dockerfile + Dagger CI for missing components

**Status: not started.** Note: the Go harness already builds working images via host cross-compile + distroless bake (`harness/build/build.go`). This phase delivers **canonical per-repo Dockerfiles** for OCI publishing and Helm `appVersion` pinning.

**Targets: `bitcoin-retry-endpoint`, `bitcoin-subtx-generator`**

Deliverables:
- `Dockerfile` in each repo (multi-stage, distroless runtime)
- `ci/main.go` (Dagger pipeline: unit, lint, build subcommands) in each repo
- Image build verified on self-hosted runner; no push
- `NACK_ADDR` explicit config in retry-endpoint noted in README

Dependencies: none. Can start immediately.

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

**Target: four new `-helm` repos.** Each chart now exposes the `networking.mode` toggle (`multus` | `host` | `unicast`) and an optional `metrics.serviceMonitor.enabled` switch.

Deliverables:
- `bitcoin-shard-proxy-helm/` — Chart.yaml, values.yaml, templates/
- `bitcoin-shard-listener-helm/` — same; DaemonSet support; NUM_WORKERS enforced = 1
- `bitcoin-retry-endpoint-helm/` — same; NACK_ADDR template warning
- `bitcoin-subtx-generator-helm/` — Deployment + Job modes
- `helm lint` + `helm template` CI on every push (GH Actions hosted runner)
- `release.yml` scaffold present but workflow_dispatch-gated with `RELEASE` confirmation
- OCI push target configured but not enabled

Dependencies: Phase 1 (Dockerfiles needed to define correct image references).

---

## Phase 4.5 — Multus enablement + BGP scenarios

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

**Target: GHCR + GitHub Pages**

Deliverables:
- OCI image push to `ghcr.io/lightwebinc/bitcoin-shard-proxy`, etc.
- Helm chart publish via chart-releaser to GitHub Pages
- Helm OCI push to `ghcr.io/lightwebinc/` (same registry, different path)
- Semantic versioning: `v0.1.0` initial release tag
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

---

## Timeline estimate

| Phase | Effort | Blocking |
|---|---|---|
| 0 | 1 session | — |
| 1 | 2–3 sessions | — |
| 2 | **done** | — |
| 3 | 1–2 sessions | — (harness ships, Dagger wrapping only) |
| 4 | 2–3 sessions | Phase 1 |
| 4.5 | 2–4 sessions | Phase 4 |
| 5 | 2–3 sessions | Phase 4.5 |
| 6 | 1 session + approval | Phase 5 |
| 7 | 3–5 sessions | user decision |

Phases 1, 3 and 4 can proceed in parallel. Phase 4.5 unblocks the BGP scenarios and the k0s Multus default.
