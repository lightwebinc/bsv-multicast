# Component Containerization Viability

## Summary table

| Component | Stateful? | Multicast role | Docker viability | k0s viability (Multus default) | Existing assets |
|---|---|---|---|---|---|
| `bitcoin-shard-proxy` | Stateless | Egress sender | **High** — used by harness via cross-compile + distroless image | **High** — macvlan `net1` on fabric NIC, or `hostNetwork: true` fallback | `Dockerfile`, `test/Dockerfile.e2e` (harness builds its own minimal image via `harness/build`) |
| `bitcoin-shard-listener` | In-memory gap state | Ingress subscriber (MLD join) | **High** — used by harness | **High** with Multus (DaemonSet); `hostNetwork` fallback | `Dockerfile` (distroless), `test/Dockerfile.e2e` |
| `bitcoin-retry-endpoint` | In-memory freecache (+ optional Redis) | Ingress subscriber + egress retransmitter | **High** — image built on the fly by `harness/build` | **High** with Multus (per-node release for `NACK_ADDR`); `hostNetwork` fallback | No standalone `Dockerfile` in repo — harness produces a minimal image; Phase 1 still ships a canonical one for k0s |
| `bitcoin-subtx-generator` | Stateless | Client/sender | **Very High** — used by harness | **High** — standard CNI is fine (UDP/TCP egress only); no Multus needed | Multi-binary repo; harness packages `subtx-gen`, `send-anchor-frame`, `send-block-announce`, `send-subtree-data` |
| `bitcoin-shard-common` | Library | — | n/a | n/a | Built into other images via host `go.work` workspace |
| Infra repos (ingress/listener/retransmission) | Ansible/Terraform | — | n/a (host-level) | Future: NetworkPolicy on primary CNI | Keep as-is for VM/baremetal |
| `bitcoin-multicast-test` | Test scenarios (Go + Docker) | Harness | **Implemented:** `harness/` Go + Docker driver, 40 scenarios | n/a | `vm-lab/scenarios/` bash suite — **legacy**, LXD-only, switch/BGP fidelity reference |

---

## bitcoin-shard-proxy

### Existing Docker assets
- `Dockerfile` — multi-stage Go build → ubuntu:24.04 runtime
- `test/Dockerfile.e2e` — unicast-injection E2E test image (build context: parent dir; bundles `send-test-frames`)
- `test/docker-compose.yml` — repo-local E2E only; **not used by the multi-component Go harness**, which provisions containers directly via `docker run` from `harness/driver/docker/docker.go`

### Key env vars (all accepted by binary)

| Env | Default | Notes |
|---|---|---|
| `LISTEN_ADDR` | `[::]` | Ingress bind address |
| `UDP_LISTEN_PORT` | `9000` | UDP frame ingress |
| `TCP_LISTEN_PORT` | `0` | TCP ingress (0=disabled) |
| `MULTICAST_IF` | `eth0` | Comma-separated egress NICs |
| `EGRESS_PORT` | `9001` | Multicast dest port |
| `SHARD_BITS` | `2` | Must match listeners |
| `MC_SCOPE` | `site` | link/site/org/global |
| `MC_GROUP_ID` | `0x000B` | IANA Bitcoin allocation |
| `NUM_WORKERS` | NumCPU | SO_REUSEPORT workers |
| `FRAG_MTU` | `0` | BRC-130 fragmentation (0=off) |
| `METRICS_ADDR` | `:9100` | /metrics /healthz /readyz |
| `DRAIN_TIMEOUT` | `0s` | Pre-SIGTERM drain |

### Containerization constraints
- **Multicast egress requires NIC-level access.** In Docker harness: user-defined bridge `mcast-fabric` (`fd10::/64`) with MLD snooping + querier; no `network_mode: host` needed. In k0s (default): Multus secondary attachment over macvlan on the dedicated fabric NIC; pod sees it as `net1` with `MULTICAST_IF=net1`. Fallback: `hostNetwork: true` with `MULTICAST_IF` set to the host NIC name.
- **`IPV6_MULTICAST_IF` is set explicitly** by the binary via the interface name — no ambient kernel routing assumption.
- **Multiple egress interfaces:** comma-separated `MULTICAST_IF` supported.
- Stateless: safe to run N replicas without coordination.

---

## bitcoin-shard-listener

### Existing Docker assets
- `Dockerfile` — multi-stage Go build → `gcr.io/distroless/static:nonroot` runtime; requires build context to include `../bitcoin-shard-proxy` (for `send-test-frames` test tool)
- `test/Dockerfile.e2e` + `test/docker-compose.yml` — E2E uses unicast injection to `[::1]:port` to avoid Linux loopback multicast unreliability (confirmed pattern for CI)

### Key env vars

| Env | Default | Notes |
|---|---|---|
| `MULTICAST_IF` | `eth0` | NIC for MLD joins + NACK send |
| `LISTEN_PORT` | `9001` | Multicast receive |
| `SHARD_BITS` | `2` | Must match proxy |
| `MC_SCOPE` | `site` | link/site/org/global |
| `MC_GROUP_ID` | `0x000B` | IANA Bitcoin |
| `SHARD_INCLUDE` | `""` | Comma indices/ranges; empty=all |
| `SUBTREE_INCLUDE` | `""` | Hex subtree IDs; empty=all |
| `SUBTREE_EXCLUDE` | `""` | Hex subtree IDs to drop |
| `EGRESS_ADDR` | `127.0.0.1:9100` | Downstream unicast host:port |
| `EGRESS_PROTO` | `udp` | udp or tcp |
| `STRIP_HEADER` | `false` | Payload-only egress |
| `RETRY_ENDPOINTS` | `""` | `host:port,...` for NACK |
| `BEACON_ENABLED` | `true` | Dynamic endpoint discovery |
| `NACK_MAX_RETRIES` | `5` | Per-gap retry limit |
| `NACK_BACKOFF_MAX` | `5s` | Exponential backoff cap |
| `NACK_GAP_TTL` | `10m` | Gap state lifetime |
| `NUM_WORKERS` | NumCPU | SO_REUSEPORT workers |
| `METRICS_ADDR` | `:9200` | /metrics /healthz /readyz |

### Containerization constraints
- **`NUM_WORKERS` must be 1 when using SO_REUSEPORT on a shared multicast group.** Linux kernel delivers each multicast datagram to _all_ sockets in the reuseport group (no load balancing). Multiple workers cause N-fold frame duplication. This is enforced in the Helm chart template and should be the default in any compose file.
- **MLD join requires NIC-level membership.** Docker harness: works on the `mcast-fabric` user-defined bridge with MLD snooping + querier enabled (set by `harness/driver/docker/bridge.go`). In k0s (default): Multus macvlan attachment. Fallback: `hostNetwork: true`.
- **Beacon UDP source address pinning.** Listener uses `net.ListenPacket` (unconnected) for NACK dispatch, accepting replies from any source address. No operator action needed.
- Distroless runtime image: no shell. Debug via `docker exec` with a debug build or external log forwarding.

---

## bitcoin-retry-endpoint

### Existing Docker assets
No canonical `Dockerfile` in the repo yet — the Go harness compiles a binary on the host with `GOWORK=go.work` and bakes it into a distroless image. For k0s, Phase 1 still ships a canonical Dockerfile for OCI publishing. Proposed structure:

```dockerfile
FROM golang:1.25 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -buildvcs=false -o /bitcoin-retry-endpoint .

FROM gcr.io/distroless/static:nonroot
COPY --from=builder /bitcoin-retry-endpoint /bitcoin-retry-endpoint
ENTRYPOINT ["/bitcoin-retry-endpoint"]
```

### Key env vars

| Env | Default | Notes |
|---|---|---|
| `MC_IFACE` | `eth0` | NIC for multicast ingress |
| `LISTEN_PORT` | `9001` | Multicast receive |
| `SHARD_BITS` | `8` | Must match proxy |
| `MC_SCOPE` | `site` | link/site/org/global |
| `EGRESS_IFACE` | `eth0` | Comma-separated retransmit NICs |
| `EGRESS_PORT` | `9001` | Retransmit dest port |
| `NACK_PORT` | `9300` | NACK listen port |
| `NACK_ADDR` | `""` | **Required in containers** — explicit IPv6 unicast bind+advertise addr |
| `CACHE_BACKEND` | `memory` | memory or redis |
| `REDIS_ADDR` | `""` | Required when `CACHE_BACKEND=redis` |
| `CACHE_TTL_TX` | `60s` | BRC-124/128 tx TTL |
| `CACHE_TTL_BLOCK` | `10m` | BRC-131 block TTL |
| `BEACON_ENABLED` | `true` | ADVERT beacon multicast |
| `BEACON_TIER` | `0` | 0 = closest to source |
| `BEACON_PREFERENCE` | `128` | Higher = preferred within tier |
| `BEACON_INTERVAL` | `60s` | ADVERT cadence |
| `METRICS_ADDR` | `:9400` | /metrics /healthz /readyz |

### Containerization constraints
- **`NACK_ADDR` is mandatory in container deployments.** Without it the binary auto-detects from the egress interface, which may resolve to a SLAAC address. Listeners filter ACK/MISS responses by advertised source — a SLAAC address mismatch causes silent ACK drops. In Docker: set explicitly from the container's IPv6. In k0s `hostNetwork` mode: set to the node's fabric IPv6.
- **Multi-subscriber + retransmitter:** binary joins multicast groups (ingress) AND sends multicast (retransmit). Both paths need host NIC.
- **Redis for shared cache:** optional; required for cross-instance deduplication in multi-replica deployments. Can be provided via bitnami/redis subchart (toggled in Helm values).

---

## bitcoin-subtx-generator

### Existing Docker assets
No `Dockerfile` yet. Phase 2 delivers it. Single multi-binary image containing:
- `subtx-gen` — main traffic generator (`cmd/subtx-gen`)
- `send-anchor-frame` — anchor tx sender (`cmd/send-anchor-frame`)
- `send-block-announce` — block announce sender (`cmd/send-block-announce`)
- `send-subtree-data` — subtree data sender (`cmd/send-subtree-data`)

The entrypoint is operator-selected via the `command:` field in compose/Helm.

### Containerization constraints
- Pure UDP/TCP client toward proxy. No multicast receive, no MLD, no host NIC access needed.
- In Docker: default bridge network with IPv6 is sufficient.
- In k0s: standard pod network (no `hostNetwork` required) since it only sends unicast to the proxy UDP port.

---

## Per-component firewall notes

All three service binaries implement nftables/pf perimeter rules for VM deployments (via Ansible templates in the infra repos). **Containers do not use these templates.** Container networking is isolated to the harness user-defined network or the pod network; perimeter rules are replaced by:
- Docker harness: isolation via `mcast-fabric` user-defined network + direct IPv6 container addressing on `fd10::/64`. The harness applies `ip6tables` DROP rules (`harness/env/iptables.go`) and `tc netem` (`harness/env/netem.go`) per-veth for scenario-level loss injection.
- k0s (Multus default): `NetworkPolicy` on the primary CNI for control/metrics traffic; Multus macvlan attachment has no `NetworkPolicy` enforcement — segregate at the switch / NIC.
- k0s (hostNetwork fallback): `NetworkPolicy` is advisory only; segregate at the host.

The only multicast-specific concern in containers is MLD snooping — a Linux bridge behaviour, configured automatically by the harness bridge setup. See [docker-test-infra.md](docker-test-infra.md).
