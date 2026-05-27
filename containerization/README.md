# Containerization — Index

This tree documents the containerization strategy for bsv-multicast components: Docker-based testing, Kubernetes/Helm deployment (targeting k0s), CI automation via Dagger + GitHub Actions, and coexistence with the existing LXD integration test lab.

## Decision summary

| Topic | Decision |
|---|---|
| Container runtime | Docker / OCI |
| K8s target | **k0s** (self-hosted, no cloud). |
| Multicast networking (default) | **Multus + macvlan** secondary interface on the dedicated multicast NIC; primary CNI carries metrics/control |
| Multicast networking (fallbacks) | `hostNetwork: true` (single-NIC operators); future `EGRESS_MODE=unicast-list` (pure pod-network) |
| Helm layout | **One chart per repo**, `-helm` suffix, independent versioning — no umbrella |
| Test harness | **Implemented** — Go + Docker driver under `multicast-test/harness/`. 40 scenarios. `go test` only, no docker-compose. |
| CI | Dagger (Go SDK) as single pipeline source; GH Actions invokes Dagger; self-hosted runner for integration + multicast tests |
| Metrics stack | **External** in every environment. Harness scrapes via `expfmt` direct HTTP; vm-lab uses external Prometheus VM; k0s uses external Prometheus scraping the primary-CNI Service / pod IP. No in-cluster Prometheus shipped by these charts. |
| LXD lab | **Legacy `vm-lab/`** — kept for switch/BGP fidelity; no Go-harness LXD driver |
| Release gating | All image/chart publish workflows written but **disabled** until explicit approval |

## Document map

| File | Contents |
|---|---|
| [component-viability.md](component-viability.md) | Per-component Docker/k0s viability, existing assets, known constraints |
| [docker-test-infra.md](docker-test-infra.md) | Implemented Go harness under `multicast-test/harness/` — driver, bridge, env, metrics |
| [helm-charts.md](helm-charts.md) | Chart repo layout, `values.yaml` cross-reference incl. `networking.mode`, publishing gate |
| [k0s-deployment.md](k0s-deployment.md) | k0s reference deployment: Multus (default) + hostNetwork fallback + node labeling |
| [composition-spec.md](composition-spec.md) | Operator wiring spec: Helmfile / ArgoCD / Flux / Terraform / plain Helm |
| [ci-strategy.md](ci-strategy.md) | Dagger pipeline design, GH Actions tiers, self-hosted runner requirements |
| [lxd-coexistence.md](lxd-coexistence.md) | How the existing LXD scenario suite keeps working alongside the new harness |
| [roadmap.md](roadmap.md) | Phased adoption plan |

## Multus rationale

The original plan rejected Multus to minimise operator surface area. With the dedicated-multicast-NIC topology (operator wires one or more NICs specifically to the multicast switch — or even back-to-back between two hosts) Multus provides:

- Hardware boundary preserved: the pod sees the mcast NIC only as `net1`, not all host interfaces
- No host-port collisions when running multiple replicas per node
- Working `Service` / `ClusterIP` / `ServiceMonitor` on the primary CNI for metrics & control
- The same model that BGP scenarios (40–42) need (`bgp-transit`, `bgp-ibgp` secondary networks) — currently `t.Skip`'d in the harness pending Multus or multi-network Docker support

Single-NIC operators may still pick `networking.mode: host` in the chart values for the previous `hostNetwork` behaviour. See [k0s-deployment.md](k0s-deployment.md) for NetworkAttachmentDefinition examples.

## Repo layout (new repos, none published yet)

```
shard-proxy-helm      Helm chart — shard-proxy
shard-listener-helm   Helm chart — shard-listener
retry-endpoint-helm   Helm chart — retry-endpoint
subtx-generator-helm  Helm chart — subtx-generator (load-test Jobs)
```

All four chart repos follow the same conventions described in [helm-charts.md](helm-charts.md).
