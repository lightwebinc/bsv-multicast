# Debian 13 (Trixie)

Debian 13 is a first-class target. It is `ansible_os_family: Debian`, so the
roles run the same task set as on [Ubuntu 24.04](ubuntu-24.04.md): service
management, netplan layout, BGP daemon paths, firewall and file locations are
identical. Only the differences are listed here.

- **netplan is not preinstalled.** The `networking` role installs `netplan.io`
  and enables `systemd-networkd` (the netplan renderer); retransmission-infra
  skips this when `manage_netplan: false`. Rendered config lands in
  `/run/systemd/network/10-netplan-*` as on Ubuntu, and ip6gre tunnels come up
  `POINTOPOINT,NOARP` on both.
- **Cloud images render networkd directly.** Debian cloud and LXD images ship
  without netplan or ifupdown; cloud-init writes
  `/etc/systemd/network/*.network`. These coexist with netplan-rendered files as
  long as they configure different interfaces (the roles manage only the data
  interfaces). If cloud-init manages the same interface, disable its network
  rendering (`/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg`).
- **Kernel.** The stock 6.12 LTS kernel passes the full multicast validation
  (ip6gre mesh replication, SSM `MCAST_JOIN_SOURCE_GROUP` joins, MLDv2 source
  filters, zero-copy datapath on mlx5). No backports kernel is required;
  `trixie-backports` has newer kernels if a NIC driver needs one.
- **FRR.** Debian's `frr` (10.x) is far newer than Ubuntu 24.04's and ships
  `pim6d`. The `bgp` role installs the distro package on both.
- **BIRD2.** `bird2` 2.17.x from the trixie repository; same config paths.
- **Minimal images.** Debian cloud images omit `curl`; `common` installs it
  before anything needs it.
