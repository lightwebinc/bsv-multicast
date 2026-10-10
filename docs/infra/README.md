# Host deployment (Ansible and Terraform)

Each pipeline service ships a VM-side deployment repository. They share one
layout, one `common` role, and the same OS and operations conventions; this
directory holds that shared content once. Each repository keeps only what is
specific to its service (variables, ports, file paths, topology).

| Repository | Deploys | Terraform | BGP | Firewall role |
|---|---|---|---|---|
| [ingress-infra](https://github.com/lightwebinc/ingress-infra) | [`shard-proxy`](https://github.com/lightwebinc/shard-proxy) (ingress) | yes | yes | no |
| [listener-infra](https://github.com/lightwebinc/listener-infra) | [`shard-listener`](https://github.com/lightwebinc/shard-listener) (subscriber) | yes | yes | yes |
| [retransmission-infra](https://github.com/lightwebinc/retransmission-infra) | [`retry-endpoint`](https://github.com/lightwebinc/retry-endpoint) (NACK cache) | yes | no | yes |
| [manifest-infra](https://github.com/lightwebinc/manifest-infra) | [`shard-manifest`](https://github.com/lightwebinc/shard-manifest) (BRC-139 emitter) | no | no | yes |

For Kubernetes, use the charts in
[lightwebinc/charts](https://github.com/lightwebinc/charts) composed by
[multicast-kube-infra](https://github.com/lightwebinc/multicast-kube-infra).

## Shared pages

- [Platforms](platforms.md): supported operating systems and service managers
- [Ansible operations](ansible-operations.md): requirements, workflow, variable
  precedence, version pins, the `common` and `perf-tuning` roles, upgrades
- [Terraform layout](terraform-layout.md): module/example structure, the
  Ansible hand-off, version pin coupling, adding a cloud
- OS notes: [Ubuntu 24.04](os/ubuntu-24.04.md), [Debian 13](os/debian-13.md),
  [FreeBSD 14](os/freebsd-14.md)

## Naming in these pages

`<svc>` is the service binary (`shard-proxy`, `shard-listener`, `retry-endpoint`,
`shard-manifest`), `<svc_>` its rc.d name (`shard_proxy`, ...), and `<prefix>`
its variable prefix (`proxy`, `listener`, `retry`, `manifest`). Each
repository's own docs list its exact ports, paths and variables.
