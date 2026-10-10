# Ubuntu 24.04 (Noble)

Reference platform for all four infra repositories. Naming (`<svc>`,
`<prefix>`) is defined in the [README](../README.md); each repository's
`docs/os/ubuntu-24.04.md` lists its service's exact paths, ports and
diagnostics.

## Packages

The `common` role installs `acl`, `build-essential`, `git`, `curl`,
`ca-certificates` and `tar` from apt (the Go build itself runs with
`CGO_ENABLED=0`), and the Go toolchain from the go.dev tarball into
`/usr/local/go` (`go_version`). Service roles add `nftables` (when
`enable_firewall: true`) and `bird2` or `frr` (when `enable_bgp: true`).

## Service management

Each service is a systemd unit rendered from
`ansible/roles/<svc>/templates/<svc>.service.j2`, reading its environment from
`/etc/<svc>/config.env`.

```sh
sudo systemctl status <svc>
sudo systemctl restart <svc>
sudo journalctl -u <svc> -f
```

## Networking

The `networking` role writes netplan files numbered `60-` (data interface),
`61-` (ip6gre tunnel, when the mode is `gre`) and `62-` (BGP VIP on loopback,
BGP repositories only), plus `/etc/sysctl.d/60-*.conf`. Apply by hand with:

```sh
sudo netplan apply
```

## Firewall

Where a repository ships a `firewall` role, its nftables ruleset lands in
`/etc/nftables.d/60-<svc>.nft`, included from `/etc/nftables.conf`:

```sh
sudo nft list table inet <svc>
sudo systemctl status nftables
```

ingress-infra ships no firewall role; enforce its port list with your own
policy (ufw, provider ACLs).

## BGP daemons

| Daemon | Config | Reload | Inspect |
|---|---|---|---|
| BIRD2 | `/etc/bird/bird.conf` | `systemctl reload bird` / `birdc configure` | `birdc show protocols`, `birdc show route` |
| FRR | `/etc/frr/frr.conf` | `systemctl reload frr` | `vtysh -c 'show bgp summary'` |

Ubuntu 24.04's FRR is 8.4.x and does not ship `pim6d`; only `bgpd` is used here.

## Multicast diagnostics

```sh
ip -6 maddr show dev <iface>                     # joined groups
ip -6 route show table local to ff00::/8         # multicast route
tcpdump -i <iface> -nn 'udp and ip6 multicast'   # live capture
```
