# Supported platforms

| OS | Automation | Service manager | Notes |
|---|---|---|---|
| Ubuntu 24.04 LTS | Ansible | systemd | reference platform |
| Debian 13 | Ansible | systemd | same task set as Ubuntu (`ansible_os_family: Debian`); see [differences](os/debian-13.md) |
| FreeBSD 14 | Ansible | rc.d | see [FreeBSD notes](os/freebsd-14.md) |
| AWS EC2 | Terraform (then Ansible) | systemd | Ubuntu 24.04 image; `aws-ec2` example |
| Any SSH host | Terraform (then Ansible) | per OS | `generic` example |

`manifest-infra` ships Ansible only; the other three repositories also ship the
Terraform modules described in [terraform-layout.md](terraform-layout.md).

Every service binary is built from source on the host (or pushed pre-built) as
a static executable (`CGO_ENABLED=0`), so there are no shared-library
dependencies on any platform.

## Target requirements

- IPv6 enabled on the fabric (multicast) interface; the fabric is IPv6-only.
- Outbound access for package installation, the Go toolchain download
  (go.dev, port 443) and the service's git clone, or a pre-staged
  `/usr/local/go` plus a pre-built binary (`<prefix>_local_binary`).
- SSH access with `sudo` or root for the Ansible user.
