# Roadmap

## Phase 0 — Documentation (current)

**Status: in progress**

- [x] `containerization/` doc tree in `bitcoin-multicast`
- [ ] Review and sign-off

---

## Phase 1 — Dockerfile + Dagger CI for missing components

**Targets: `bitcoin-retry-endpoint`, `bitcoin-subtx-generator`**

Deliverables:
- `Dockerfile` in each repo (multi-stage, distroless runtime)
- `ci/main.go` (Dagger pipeline: unit, lint, build subcommands) in each repo
- Image build verified on self-hosted runner; no push
- `NACK_ADDR` explicit config in retry-endpoint noted in README

Dependencies: none. Can start immediately.

---

## Phase 2 — Go harness + Docker E2E scenarios

**Target: `bitcoin-multicast-test/harness/`**

Deliverables:
- `harness/driver/` — `Driver` interface + Docker implementation
- `harness/topology/` — declarative node/link graph
- `harness/scenario/` — `Env`, metrics assertions, gap injection
- Initial scenario coverage: 01, 09/99, 10, 11, 13 (functional + NACK/RL core)
- `harness/cmd/run-scenario/` CLI
- `go test ./harness/...` runnable locally and on self-hosted runner
- MLD bridge setup utility (host sysfs writes)

Dependencies: Phase 1 (needs retry-endpoint and subtx-gen Dockerfiles).

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

**Target: four new `-helm` repos**

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

## Phase 5 — k0s reference deployment

**Target: lab host (co-located with LXD)**

Deliverables:
- k0s controller + worker nodes deployed on lab host or dedicated VMs
- Node labels applied: `bitcoin-mcast/role`, `bitcoin-mcast/node`, `bitcoin-mcast/fabric-iface`
- Helmfile-based composition wiring all four charts
- Verified: end-to-end multicast delivery via hostNetwork pods on real fabric
- Verified: NACK recovery + beacon discovery in k0s environment
- External Prometheus updated to scrape k0s pod endpoints
- LXD scenarios continue passing in parallel

Dependencies: Phase 4.

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
| 2 | 3–5 sessions | Phase 1 |
| 3 | 1–2 sessions | Phase 2 |
| 4 | 2–3 sessions | Phase 1 |
| 5 | 2–3 sessions | Phase 4 |
| 6 | 1 session + approval | Phase 5 |
| 7 | 3–5 sessions | user decision |

Phases 1 and 4 can proceed in parallel.
