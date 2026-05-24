# Containerization — Index

This tree documents the containerization strategy for bitcoin-multicast components: Docker-based testing, Kubernetes/Helm deployment (targeting k0s), CI automation via Dagger + GitHub Actions, and coexistence with the existing LXD integration test lab.

## Decision summary

| Topic | Decision |
|---|---|
| Container runtime | Docker / OCI |
| K8s target | **k0s** (self-hosted, no cloud). Multus not required: `hostNetwork: true` for mcast. |
| Multicast strategy | `hostNetwork` pods on fabric-attached nodes for phase 1; `EGRESS_MODE=unicast-list` unlocks standard CNI in a future phase |
| Helm layout | **One chart per repo**, `-helm` suffix, independent versioning — no umbrella |
| Test harness language | **Go** (`go test` + in-repo primitives). No Python/venv. |
| CI | Dagger (Go SDK) as single pipeline source; GH Actions invokes Dagger; self-hosted runner for integration + multicast tests |
| Metrics stack | **External** — not bundled with test infra. Harness scrapes directly via HTTP. |
| LXD lab | **Preserved unchanged** — new harness runs alongside via pluggable driver |
| Release gating | All image/chart publish workflows written but **disabled** until explicit approval |

## Document map

| File | Contents |
|---|---|
| [component-viability.md](component-viability.md) | Per-component Docker/k0s viability, existing assets, known constraints |
| [docker-test-infra.md](docker-test-infra.md) | Compose stack design + Go test harness architecture |
| [helm-charts.md](helm-charts.md) | Chart repo layout, `values.yaml` cross-reference, publishing gate |
| [k0s-deployment.md](k0s-deployment.md) | k0s reference deployment, hostNetwork multicast, node labeling |
| [composition-spec.md](composition-spec.md) | Operator wiring spec: Helmfile / ArgoCD / Flux / Terraform / plain Helm |
| [ci-strategy.md](ci-strategy.md) | Dagger pipeline design, GH Actions tiers, self-hosted runner requirements |
| [lxd-coexistence.md](lxd-coexistence.md) | How the existing LXD scenario suite keeps working alongside the new harness |
| [roadmap.md](roadmap.md) | Phased adoption plan |

## Repo layout (new repos, none published yet)

```
bitcoin-shard-proxy-helm      Helm chart — bitcoin-shard-proxy
bitcoin-shard-listener-helm   Helm chart — bitcoin-shard-listener
bitcoin-retry-endpoint-helm   Helm chart — bitcoin-retry-endpoint
bitcoin-subtx-generator-helm  Helm chart — bitcoin-subtx-generator (load-test Jobs)
```

All four chart repos follow the same conventions described in [helm-charts.md](helm-charts.md).
