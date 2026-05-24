# k0s Deployment Reference

## Why k0s

- Single binary, zero external dependencies — no kubeadm/etcd/containerd wiring beyond what k0s manages
- Simple worker-only join: `k0s worker --token <token>` on each fabric node
- Built-in containerd; no separate runtime install
- Familiar `kubectl` + Helm workflow
- Self-hosted; stays fully on-prem
- Multus add-on installs cleanly via a single Helm release — keeps the primary CNI for control/metrics and gives multicast pods a dedicated secondary attachment on the fabric NIC. `hostNetwork: true` remains supported as a single-NIC fallback.

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

## Networking modes

The four Helm charts expose a single toggle `networking.mode` taking values:

| Mode | Pod sees | When to choose |
|---|---|---|
| `multus` (default) | Primary CNI eth0 for control/metrics; macvlan `net1` on the dedicated multicast NIC | Operator dedicates one (or more) NICs to multicast — even back-to-back between two boxes. Recommended. |
| `host` | All host interfaces (`hostNetwork: true`) | Single-NIC operators; smallest install footprint; no Multus dependency |
| `unicast` (future) | Primary CNI only; proxy uses `EGRESS_MODE=unicast-list` to fan out unicast UDP to listener addresses | Any standard CNI deployment; required for cloud-managed K8s once that path is needed. Not implemented yet (proxy work pending). |

The rest of this document assumes `multus`. The fallback paths are noted inline.

### Install Multus on k0s

k0s does not bundle Multus; install via Helm into `kube-system`:

```bash
helm repo add k8snetworkplumbingwg https://k8snetworkplumbingwg.github.io/helm-charts
helm install multus k8snetworkplumbingwg/multus -n kube-system
```

Verify the DaemonSet:

```bash
kubectl -n kube-system get ds multus
kubectl get crd network-attachment-definitions.k8s.cni.cncf.io
```

### NetworkAttachmentDefinitions

One `NetworkAttachmentDefinition` per dedicated NIC / logical fabric. Apply once per cluster.

```yaml
# mcast-fabric NAD — macvlan over the dedicated multicast NIC
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: mcast-fabric
  namespace: bitcoin-mcast
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "type": "macvlan",
      "master": "enp5s0",
      "mode": "bridge",
      "ipam": {
        "type": "static"
      }
    }
```

For BGP-ECMP scenarios (40–42) declare two additional NADs over the transit and iBGP NICs:

```yaml
# bgp-transit NAD (one per transit interface)
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata: { name: bgp-transit, namespace: bitcoin-mcast }
spec:
  config: |
    { "cniVersion": "0.3.1", "type": "macvlan",
      "master": "enp6s0", "mode": "bridge",
      "ipam": { "type": "static" } }
---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata: { name: bgp-ibgp, namespace: bitcoin-mcast }
spec:
  config: |
    { "cniVersion": "0.3.1", "type": "macvlan",
      "master": "enp7s0", "mode": "bridge",
      "ipam": { "type": "static" } }
```

### Pod attachment

Multus annotations request the secondary interface(s):

```yaml
metadata:
  annotations:
    k8s.v1.cni.cncf.io/networks: |
      [{ "name": "mcast-fabric",
         "ips": ["fd20::21/64"],
         "interface": "net1" }]
```

Inside the pod: `net1` is the dedicated mcast interface. The chart sets `MULTICAST_IF=net1` automatically when `networking.mode: multus`.

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

With `networking.mode: multus` the primary CNI carries control and metrics traffic only; multicast data rides the Multus `net1` macvlan attached to the dedicated NIC. With `networking.mode: host` pods bypass the pod CIDR entirely and see the host's real interfaces.

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

`bitcoin-retry-endpoint` needs `NACK_ADDR` set to each node's individual fabric IPv6 — the listeners filter ACK/MISS replies by source address. This cannot be a single chart value across replicas. With Multus the `NACK_ADDR` value matches the `ips:` field in the pod's `k8s.v1.cni.cncf.io/networks` annotation; with hostNetwork it matches the node's fabric NIC address.

Two deployment patterns regardless of mode:

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
| `bitcoin-shard-listener` | `DaemonSet` | One listener per fabric node; Multus `mcast-fabric` attachment with per-pod IPv6, or `hostNetwork` fallback with node label selector |
| `bitcoin-retry-endpoint` | `Deployment` (replicas=1 per release) | Per-node installs via Option A above |
| `bitcoin-subtx-generator` | `Deployment` or `Job` | Load test: Job. Continuous: Deployment. Not fabric-node-bound. |

---

## Multicast kernel parameter requirements

Applies to **every** networking mode — macvlan attachments still rely on the host kernel for MLD. On each k0s worker that joins multicast groups, ensure:

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

The metrics stack remains **external** — these charts do not ship Prometheus, Grafana, or `kube-prometheus-stack`. Each chart exposes `/metrics` on the primary-CNI interface and ships an *optional* `ServiceMonitor` (disabled by default, `metrics.serviceMonitor.enabled: false`).

### `networking.mode: multus` (default)

The metrics endpoint binds the primary CNI interface only. Two equivalent scrape paths:

```yaml
# Option 1 — external Prometheus federates a single in-cluster scraper
# (or uses kube-apiserver proxy to reach pod IPs on the primary CNI)
scrape_configs:
  - job_name: bitcoin-mcast
    kubernetes_sd_configs:
      - role: pod
        api_server: https://k0s.example.lan:6443
        bearer_token_file: /etc/prometheus/k0s.token
    relabel_configs:
      - source_labels: [__meta_kubernetes_namespace]
        action: keep
        regex: bitcoin-mcast
```

```yaml
# Option 2 — the simpler `static_configs` route via Service ClusterIP / NodePort
scrape_configs:
  - job_name: bitcoin-mcast-proxy
    static_configs:
      - targets: ['proxy.bitcoin-mcast.svc.cluster.lan:9100']
```

`net1` (the Multus macvlan) is carrying multicast data only — do not scrape it. The chart's `containerPort` and Service point at the primary-CNI interface.

### `networking.mode: host` (fallback)

Pods share the host network namespace and `/metrics` binds the host's management IP. The original node-IP scrape pattern applies:

```yaml
scrape_configs:
  - job_name: k0s-proxy
    static_configs:
      - targets: ['192.168.0.20:9100']
  - job_name: k0s-listener
    static_configs:
      - targets: ['192.168.0.31:9200', '192.168.0.32:9200', '192.168.0.33:9200']
```

In either mode, no in-cluster `ServiceMonitor` resource is needed unless the operator chooses to deploy `kube-prometheus-stack` separately.

---

## Upgrade strategy

k0s rolling upgrade via Helm `helm upgrade --install` is safe for stateless pods. For listeners:
- A brief gap in multicast coverage occurs during pod restart; NACK/retransmit handles this transparently.
- `DRAIN_TIMEOUT` should be set to at least `5s` in the Helm values to allow in-flight NACK cycles to complete before the socket closes.

For retry endpoints:
- Cache is in-memory (freecache). Pod restart clears the cache.
- TTLs are short (60s for tx); data re-arrives naturally within one proxy feed window.
- Redis-backed deployments retain cache across pod restarts.
