# Ansible operations

Shared by ingress-infra, listener-infra, retransmission-infra and manifest-infra.
Naming (`<svc>`, `<prefix>`) is defined in the [README](README.md).

## Requirements

- Ansible 2.15+ and Python 3.9+ on the control machine
- SSH access to targets with `sudo` or root
- Targets: see [platforms.md](platforms.md)

## Workflow

```sh
cd ansible
ansible-galaxy collection install -r requirements.yml   # community.general, ansible.posix
cp inventory/hosts.example.yml inventory/hosts.yml
$EDITOR inventory/hosts.yml
ansible-playbook -i inventory/hosts.yml site.yml
```

Useful variations:

```sh
ansible-playbook -i inventory/hosts.yml site.yml --tags <tag>   # one role
ansible-playbook -i inventory/hosts.yml site.yml -l <host>      # one host
ansible-playbook -i inventory/hosts.yml site.yml --tags os_update   # opt-in patching
```

All roles are idempotent; re-running applies only differences.

## Variables and precedence

`ansible/group_vars/all.yml` is the canonical variable list: every variable
ships there with a default and an inline comment. Each repository's
`docs/ansible.md` carries a full table of its service variables.

Ansible ranks `group_vars/all.yml` **above** inventory-group `vars:` blocks.
Anything that must differ per host (interface names, `mgmt_cidrs_*`, BGP router
IDs, `ansible_*` connection vars) must be set under the host entry, not the
group, or the `group_vars` default silently wins. `--extra-vars` (and therefore
Terraform, which passes its inputs that way) outranks both.

## Version pins

`<prefix>_version` in `group_vars/all.yml` is the single source of truth for the
service release a plain `ansible-playbook` run deploys. Pin a release tag; `main`
floats and makes rebuilds non-reproducible. `go_version` must be at or above the
`go` directive in the service's `go.mod` at that tag. Where a Terraform module
exists its `variables.tf` default must move in the same change (see
[terraform-layout.md](terraform-layout.md#version-pin-coupling)).

## Build, upgrade and pre-built binaries

The service role clones `<prefix>_repo`, checks out `<prefix>_version`, runs
`go build`, installs the binary under `/usr/local/bin` and restarts the service
only when the binary changed.

- **Upgrade:** bump `<prefix>_version` in `group_vars/all.yml` and run the
  service tag (or pass `-e <prefix>_version=<tag>` for a one-off).
- **Stat-guarded build:** if a binary already exists in `<prefix>_install_dir`
  it is not rebuilt; set `<prefix>_force_build: true` to rebuild.
- **Pre-built binary:** set `<prefix>_local_binary` to a local path to skip
  clone and build entirely (air-gapped hosts, cross-built FreeBSD binaries).

## The `common` role

Identical in all four repositories. It installs build dependencies and the Go
toolchain (go.dev tarball into `go_install_dir`, default `/usr/local/go`), keeps
the root filesystem bounded on Linux, and carries the opt-in patch path.

- **Disk hygiene (Linux, every converge):** a journald `SystemMaxUse` drop-in
  plus `node-disk-maintenance.timer`, which reclaims the apt cache and stale Go
  build caches and drops a node_exporter textfile metric.
- **Patching (`--tags os_update`, never by default):** Debian-family hosts
  dist-upgrade and reboot when `/var/run/reboot-required` appears; FreeBSD runs
  `freebsd-update` and `pkg upgrade` and reports (never performs) a pending
  reboot. Unknown OS families fail rather than skip.

Knobs (`roles/common/defaults/main.yml`):

| Variable | Default | Effect |
|---|---|---|
| `common_disk_maintenance` | `true` | Install the reclaim timer; `false` removes it |
| `common_disk_maintenance_oncalendar` | `daily` | systemd `OnCalendar` for the timer |
| `common_disk_maintenance_splay_sec` | `3600` | `RandomizedDelaySec` so nodes do not fire in lockstep |
| `common_gocache_max_age_days` | `7` | Go build caches touched within this window are kept |
| `common_journal_max_use` | `300M` | journald `SystemMaxUse` |
| `common_journal_keep_free` | `1G` | journald `SystemKeepFree` |
| `common_journal_max_retention` | `2week` | journald `MaxRetentionSec` |
| `node_exporter_textfile_dir` | `/var/lib/node_exporter/textfile_collector` | Where the reclaim script writes its metric |

## The `perf-tuning` role

Shipped by ingress-infra, listener-infra and retransmission-infra (manifest-infra
does not need it). Host-level network and CPU tunings for high small-packet
rates; knobs in `roles/perf-tuning/defaults/main.yml`:

| Variable | Default | Effect |
|---|---|---|
| `perf_tuning_enabled` | `true` | Master switch; `false` for stock OS behavior |
| `perf_tuning_sysctls` | UDP rmem/wmem 256 MiB, `busy_poll`/`busy_read` 50 us, backlog 1 M | `/etc/sysctl.d/65-perf-tuning.conf` |
| `perf_tuning_txqueuelen` | `10000` | systemd-networkd `.link` drop-in on the data NIC |
| `perf_tuning_disable_cstates` | `true` | Disables C3 to C10 at runtime and boot (systemd oneshot) |
| `perf_tuning_grub_cmdline` | `true` | Adds `intel_idle.max_cstate=1` to GRUB (reboot required) |
| `perf_tuning_disable_irqbalance` | `true` | Stops and masks `irqbalance` (conflicts with manual IRQ affinity) |

## Service stop timeout

systemd sends `SIGKILL` after `TimeoutStopSec`. Keep
`TimeoutStopSec > drain_timeout + 15s` (OTLP flush plus drain). The proxy,
listener and retry units set `TimeoutStopSec=30` (enough for `drain_timeout`
up to 15s); the manifest unit sets 15.

## Known issues

- Ubuntu LXD images may lack `acl`; `common` installs it.
- The `git` module fails in some images with "dubious ownership"; the service
  role marks `<prefix>_install_dir` as a git `safe.directory` before cloning.
- Precedence: see above. Set interface names and allow-lists per host.

## Labs and Kubernetes

For container labs and CI, use
[multicast-test](https://github.com/lightwebinc/multicast-test). For
Kubernetes, use [multicast-kube-infra](https://github.com/lightwebinc/multicast-kube-infra)
and the [charts](https://github.com/lightwebinc/charts).
