# FreeBSD 14

FreeBSD 14.0-RELEASE or later. Naming (`<svc>`, `<svc_>`) is defined in the
[README](../README.md); each repository's `docs/os/freebsd-14.md` lists its
service's exact paths, ports and diagnostics.

## Packages

The `common` role installs `gmake`, `git`, `curl`, `ca_root_nss`, `bash` and
`tar` with `pkg`, and the Go toolchain from the go.dev tarball into
`/usr/local/go`. Binaries are built with a direct `go build` (`gmake` is
installed but unused) as static executables. BGP repositories add `bird2` or
`frr` when `enable_bgp: true`.

## Service management

Each service is an rc.d script rendered from
`ansible/roles/<svc>/templates/<svc_>.rc.j2` into `/usr/local/etc/rc.d/<svc_>`.
It sources `/usr/local/etc/<svc>.conf` with every variable exported and runs
the binary under `daemon(8)`, logging to `/var/log/<svc_>.log`.

```sh
sysrc <svc_>_enable=YES
service <svc_> status
service <svc_> restart
tail -f /var/log/<svc_>.log
```

## Networking

The `networking` role manages `/etc/rc.conf`: `ifconfig_<iface>` and
`ifconfig_<iface>_ipv6` for the data interface, `cloned_interfaces="gif0"` plus
`ifconfig_gif0*` in GRE mode, a static multicast route on the data interface,
and (BGP repositories) `ifconfig_lo0_alias*` VIPs. Apply without rebooting:

```sh
service netif restart
service routing restart
```

- Interface names are `vtnet0`, `em0` and so on; set the interface variable per
  host.
- Tunnels use `gif0` (`if_gif`, IPv6-in-IPv6), not `gre0`. The name is fixed in
  the rc.conf template; adapt it for more than one tunnel.

## Firewall (pf)

Where a repository ships a `firewall` role, it renders a pf anchor loaded from
`/etc/pf.conf`:

```sh
sysrc pf_enable=YES pflog_enable=YES
service pf start
pfctl -sr
pfctl -f /etc/pf.conf     # reload after editing outside Ansible
```

## BGP daemons

| Daemon | Config | Reload | Inspect |
|---|---|---|---|
| BIRD2 | `/usr/local/etc/bird/bird.conf` | `service bird reload` | `birdc show protocols` |
| FRR | `/usr/local/etc/frr/frr.conf` | `service frr reload` | `vtysh -c 'show bgp ipv6 summary'` |

FRR on FreeBSD selects daemons with rc.conf variables (`frr_enable`,
`zebra_enable`, `bgpd_enable`) instead of Linux's `/etc/frr/daemons`.

## Multicast diagnostics

```sh
netstat -g -f inet6                               # joined groups
netstat -rn -f inet6 | grep ff                    # multicast routes
tcpdump -i <iface> -nn 'udp and ip6 multicast'
```
