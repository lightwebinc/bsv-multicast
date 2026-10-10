# Terraform layout

Shared by ingress-infra, listener-infra and retransmission-infra (manifest-infra
has no Terraform). Terraform creates or adopts hosts and hands per-host
provisioning to the repository's own Ansible playbook.

## Requirements

- Terraform 1.9+ or OpenTofu 1.9+ (fully compatible; every command below works
  with `tofu` in place of `terraform`)
- An SSH key with access to the target hosts
- Ansible installed on the machine running `apply`

## Structure

```text
terraform/
├── modules/
│   ├── <svc>-node/     one host: writes an inventory, runs Ansible via local-exec
│   └── bgp helper      ingress-infra (bgp-anycast) and listener-infra (bgp) only
└── examples/
    ├── generic/        existing SSH-reachable hosts (bare metal, any cloud, lab)
    └── aws-ec2/        VPC, subnets, security group, Ubuntu 24.04 EC2, optional EIP
```

| Repository | Node module | BGP helper |
|---|---|---|
| ingress-infra | `ingress-node` | `bgp-anycast` |
| listener-infra | `listener-node` | `bgp` |
| retransmission-infra | `retry-endpoint-node` | none |

The BGP helpers create no resources: they build a map of `bgp_*` variables to
pass into the node module's `extra_ansible_vars`.

## How a node module provisions

1. A `local_file` renders a per-host inventory (`generated-inventory-*.yml`).
2. A `null_resource` with a **`local-exec`** provisioner runs
   `ansible-playbook site.yml` from the machine running Terraform (no
   remote-exec), passing the module's inputs as `--extra-vars`. It re-runs
   when the host or the extra-vars change.

The common service, network and BGP knobs are first-class module inputs with
defaults; anything else goes through `extra_ansible_vars`.

## Running an example

```sh
cd terraform/examples/<generic|aws-ec2>
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
terraform init
terraform plan
terraform apply
```

On AWS the security group is the cloud-level perimeter and the on-host
nftables ruleset (where the repository ships a `firewall` role) is the
fine-grained one; keep them aligned. ip6gre endpoints on EC2 must be the
instance's IPv6 address: Elastic IPs are IPv4-only.

## Version pin coupling

Every input is passed as `--extra-vars`, which outranks
`ansible/group_vars/all.yml`. The service version (`<prefix>_version`) is
therefore pinned twice: the node module's `variables.tf` default must equal the
value in `group_vars/all.yml`, the single source of truth. Move both in one
change; a lagging default (or `main`) silently deploys a different build from
Terraform than from a plain `ansible-playbook` run.

`variables.tf` files keep their variables in lexicographic order.

## Adding a cloud

1. Create `terraform/examples/<provider>/`.
2. Provision instances, network and firewall with that provider.
3. Pass each instance's address into the node module (one module instance per
   host), following `examples/aws-ec2/main.tf`.
4. Open the cloud firewall for the service ports listed in the repository's
   docs, plus TCP/22 and the metrics port from `mgmt_cidrs_*` and TCP/179 when
   BGP is enabled.

The node modules need no changes.
