# k0s Deployment Reference

## Why k0s

- Single binary, zero external dependencies — no kubeadm/etcd/containerd wiring beyond what k0s manages
- Simple worker-only join: `k0s worker --token <token>` on each fabric node
- Built-in containerd; no separate runtime install
- Familiar `kubectl` + Helm workflow
- Self-hosted; stays fully on-prem
- No Multus required: `hostNetwork: true` gives pods direct access to host NICs

---

## Cluster topology

```
k0s controller (management node)
  |
  +-- worker node: proxy          fabric NIC: enp5s0 or eth1
  +-- worker node: listener-1     fabric NIC: enp5s0 or eth1
  +-- worker node: listener-2     fabric NIC: enp5s0 or eth1
  +-- worker node: listener-3     fabric NIC: enp5s0 or eth1
  +-- worker node: retry-1        fabric NIC: enp5s0 or eth1
  +-- worker node: retry-2        fabric NIC: enp5s0 or eth1
  +-- worker node: retry-3        fabric NIC: enp5s0 or eth1
```

In the initial lab deployment, all roles can run on the same physical machines already hosting the LXD VMs — k0s workers run directly on Ubuntu 24.04 hosts or inside their own LXD VMs alongside the existing service VMs.

---

## k0s install (single controller + workers)

```bash
# Controller
curl -sSLf https://get.k0s.sh | sh
k0s install controller --single   # single-node dev; omit --single for multi-node
k0s start

# Generate worker token
k0s token create --role=worker > worker.token

# Workers (each fabric node)
curl -sSLf https://get.k0s.sh | sh
k0s install worker --token-file worker.token
k0s start
```

---

## k0s config — IPv6 + hostNetwork considerations

`/etc/k0s/k0s.yaml`:

```yaml
apiVersion: k0s.k0sproject.io/v1beta1
kind: ClusterConfig
spec:
  network:
    provider: kuberouter            # or calico; both support IPv6 dual-stack
    dualStack:
      enabled: true
      IPv6podCIDR: "fd20::/112"
      IPv6serviceCIDR: "fd30::/112"
  podCIDR: "10.244.0.0/16"
  serviceCIDR: "10.96.0.0/12"
```

`hostNetwork: true` pods bypass the pod CIDR entirely — they see the host's real interfaces. The CNI choice only affects non-`hostNetwork` pods (e.g., `subtx-generator` or Redis).

---

## Node labeling

Apply these labels to classify nodes by their fabric NIC and role:

```bash
# For each fabric node — substitute actual NIC name
kubectl label node <node-name> bitcoin-mcast/fabric-iface=enp5s0
kubectl label node <node-name> bitcoin-mcast/role=proxy
kubectl label node <node-name> bitcoin-mcast/role=listener   # multiple labels OK
kubectl label node <node-name> bitcoin-mcast/role=retry-endpoint
```

Per-role `nodeSelector` in Helm values:

```yaml
# bitcoin-shard-proxy values
nodeSelector:
  bitcoin-mcast/role: proxy
  bitcoin-mcast/fabric-iface: enp5s0

# bitcoin-shard-listener values
nodeSelector:
  bitcoin-mcast/role: listener
  bitcoin-mcast/fabric-iface: enp5s0
```

`DaemonSet` is appropriate for listener — one pod per labeled node automatically:

```yaml
# values.yaml
workloadType: DaemonSet    # listener chart supports DaemonSet | Deployment
```

---

## Per-node env var overrides (NACK_ADDR)

`bitcoin-retry-endpoint` needs `NACK_ADDR` set to each node's individual fabric IPv6. This cannot be a single chart value across replicas. Two approaches:

### Option A — Per-node Helm release

```bash
helm install retry-node-1 bitcoin-retry-endpoint-helm/ \
  --set config.nackAddr=fd20::24 \
  --set nodeSelector."bitcoin-mcast/node"=retry-1

helm install retry-node-2 bitcoin-retry-endpoint-helm/ \
  --set config.nackAddr=fd20::25 \
  --set nodeSelector."bitcoin-mcast/node"=retry-2
```

Label each node: `kubectl label node retry-1 bitcoin-mcast/node=retry-1`

### Option B — Downward API + startup wrapper

Use the Downward API to inject `status.hostIP` as `NODE_IP`, then a startup wrapper resolves the fabric IPv6 from the interface:

```yaml
env:
  - name: NODE_IP
    valueFrom:
      fieldRef:
        fieldPath: status.hostIP
  - name: MULTICAST_IF
    value: "enp5s0"
```

A lightweight init container resolves: `ip -6 addr show enp5s0 scope global | awk '/inet6/{print $2}' | cut -d/ -f1`

**Option A is recommended** for simplicity. Option B is useful when the same chart release spans multiple nodes without per-node releases.

---

## DaemonSet vs Deployment

| Component | Workload type | Rationale |
|---|---|---|
| `bitcoin-shard-proxy` | `Deployment` (replicas=1) | Single ingress point per site; or per-site DaemonSet if multiple proxy nodes |
| `bitcoin-shard-listener` | `DaemonSet` | One listener per fabric node; `hostNetwork` + node label selector restricts which nodes |
| `bitcoin-retry-endpoint` | `Deployment` (replicas=1 per release) | Per-node installs via Option A above |
| `bitcoin-subtx-generator` | `Deployment` or `Job` | Load test: Job. Continuous: Deployment. Not fabric-node-bound. |

---

## Multicast kernel parameter requirements

On each k0s worker that joins multicast groups, ensure:

```bash
# IPv6 enabled
sysctl -w net.ipv6.conf.all.disable_ipv6=0
sysctl -w net.ipv6.conf.enp5s0.disable_ipv6=0

# MLD version (use MLDv2 for SSM)
sysctl -w net.ipv6.conf.all.force_mld_version=2

# Increase multicast group membership limit (default 20)
sysctl -w net.ipv6.conf.all.mc_forwarding=0
echo 512 > /proc/sys/net/ipv6/conf/all/mc_fwd   # not normally needed

# Persistent (add to /etc/sysctl.d/80-bitcoin-mcast.conf)
net.ipv6.conf.all.disable_ipv6 = 0
net.ipv6.conf.enp5s0.disable_ipv6 = 0
net.ipv6.conf.all.force_mld_version = 2
```

These are the same parameters configured by the existing Ansible `common` role. The k0s worker bootstrap can apply them via a DaemonSet or cloud-init.

---

## Metrics scraping in k0s

The metrics stack remains external. The external Prometheus instance is configured to scrape `hostNetwork` pod endpoints by node IP:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: k0s-proxy
    static_configs:
      - targets: ['192.168.0.20:9100']   # proxy node mgmt IP
  - job_name: k0s-listener
    static_configs:
      - targets: ['192.168.0.31:9200', '192.168.0.32:9200', '192.168.0.33:9200']
  - job_name: k0s-retry
    static_configs:
      - targets: ['192.168.0.34:9400', '192.168.0.35:9400', '192.168.0.36:9400']
```

Since pods use `hostNetwork`, the metrics ports are bound on the host's management IP — no in-cluster `ServiceMonitor` is needed.

---

## Upgrade strategy

k0s rolling upgrade via Helm `helm upgrade --install` is safe for stateless pods. For listeners:
- A brief gap in multicast coverage occurs during pod restart; NACK/retransmit handles this transparently.
- `DRAIN_TIMEOUT` should be set to at least `5s` in the Helm values to allow in-flight NACK cycles to complete before the socket closes.

For retry endpoints:
- Cache is in-memory (freecache). Pod restart clears the cache.
- TTLs are short (60s for tx); data re-arrives naturally within one proxy feed window.
- Redis-backed deployments retain cache across pod restarts.
