# Composition Spec

This document is the **operator reference** for wiring the four independent Helm charts into a coherent stack. No umbrella chart is provided; choose whichever composition layer fits your GitOps toolchain.

---

## Prerequisite: shared parameters

All charts must agree on these values. Capture them once in your composition layer:

| Parameter | Recommended value | Charts that consume it |
|---|---|---|
| `SHARD_BITS` | `2` (4 groups) or `8` (256 groups) | proxy, listener, retry-endpoint |
| `MC_SCOPE` | `site` (FF05::/16 for closed fabric) | proxy, listener, retry-endpoint |
| `MC_GROUP_ID` | `0x000B` (IANA Bitcoin allocation) | proxy, listener, retry-endpoint |
| `MULTICAST_IF` / `MC_IFACE` | fabric NIC name on each node | proxy, listener, retry-endpoint |
| `EGRESS_PORT` (proxy) == `LISTEN_PORT` (listener/retry) | `9001` | proxy, listener, retry-endpoint |
| Listener `RETRY_ENDPOINTS` | comma-separated `[addr]:port` of all retry nodes | listener |
| Retry `NACK_ADDR` | per-node fabric IPv6 | retry-endpoint (per-release) |
| `BEACON_PORT` | `9300` | listener, retry-endpoint |

---

## Option A — Helmfile

```yaml
# helmfile.yaml
repositories:
  - name: bsp    # bitcoin-shard-proxy
    url: https://lightwebinc.github.io/bitcoin-shard-proxy-helm
  - name: bsl
    url: https://lightwebinc.github.io/bitcoin-shard-listener-helm
  - name: bre
    url: https://lightwebinc.github.io/bitcoin-retry-endpoint-helm

environments:
  production:
    values:
      - env/production.yaml

releases:
  - name: proxy
    namespace: bitcoin-mcast
    chart: bsp/bitcoin-shard-proxy
    values:
      - config:
          multicastIf: {{ .Values.fabricIface }}
          shardBits: {{ .Values.shardBits }}
          mcScope: {{ .Values.mcScope }}
          egressPort: {{ .Values.dataPort }}
        nodeSelector:
          bitcoin-mcast/role: proxy

  - name: listener
    namespace: bitcoin-mcast
    chart: bsl/bitcoin-shard-listener
    values:
      - config:
          multicastIf: {{ .Values.fabricIface }}
          shardBits: {{ .Values.shardBits }}
          mcScope: {{ .Values.mcScope }}
          listenPort: {{ .Values.dataPort }}
          retryEndpoints: {{ .Values.retryEndpoints | join "," }}
        workloadType: DaemonSet
        nodeSelector:
          bitcoin-mcast/role: listener

  - name: retry-node-1
    namespace: bitcoin-mcast
    chart: bre/bitcoin-retry-endpoint
    values:
      - config:
          mcIface: {{ .Values.fabricIface }}
          shardBits: {{ .Values.shardBits }}
          mcScope: {{ .Values.mcScope }}
          listenPort: {{ .Values.dataPort }}
          nackAddr: {{ .Values.retry1FabricAddr }}
          beaconTier: 0
          beaconPreference: 128
        nodeSelector:
          bitcoin-mcast/node: retry-1

  - name: retry-node-2
    namespace: bitcoin-mcast
    chart: bre/bitcoin-retry-endpoint
    values:
      - config:
          nackAddr: {{ .Values.retry2FabricAddr }}
          beaconTier: 0
          beaconPreference: 64
        nodeSelector:
          bitcoin-mcast/node: retry-2

  - name: retry-node-3
    namespace: bitcoin-mcast
    chart: bre/bitcoin-retry-endpoint
    values:
      - config:
          nackAddr: {{ .Values.retry3FabricAddr }}
          beaconTier: 1
          beaconPreference: 128
        nodeSelector:
          bitcoin-mcast/node: retry-3
```

`env/production.yaml`:

```yaml
fabricIface: enp5s0
shardBits: 8
mcScope: site
dataPort: 9001
retryEndpoints:
  - "[fd20::24]:9300"
  - "[fd20::25]:9300"
  - "[fd20::26]:9300"
retry1FabricAddr: "fd20::24"
retry2FabricAddr: "fd20::25"
retry3FabricAddr: "fd20::26"
```

---

## Option B — ArgoCD ApplicationSet

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: bitcoin-mcast-listeners
spec:
  generators:
    - list:
        elements:
          - node: listener-1
            fabricAddr: fd20::21
          - node: listener-2
            fabricAddr: fd20::22
          - node: listener-3
            fabricAddr: fd20::23
  template:
    metadata:
      name: 'bsl-{{node}}'
    spec:
      source:
        repoURL: https://lightwebinc.github.io/bitcoin-shard-listener-helm
        chart: bitcoin-shard-listener
        targetRevision: "0.1.0"
        helm:
          values: |
            config:
              multicastIf: enp5s0
              shardBits: 8
              mcScope: site
              retryEndpoints: "[fd20::24]:9300,[fd20::25]:9300,[fd20::26]:9300"
            nodeSelector:
              bitcoin-mcast/node: "{{node}}"
      destination:
        server: https://kubernetes.default.svc
        namespace: bitcoin-mcast
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
```

---

## Option C — Terraform (Helm provider)

```hcl
resource "helm_release" "proxy" {
  name       = "proxy"
  repository = "https://lightwebinc.github.io/bitcoin-shard-proxy-helm"
  chart      = "bitcoin-shard-proxy"
  version    = "0.1.0"
  namespace  = "bitcoin-mcast"

  set {
    name  = "config.multicastIf"
    value = var.fabric_iface
  }
  set {
    name  = "config.shardBits"
    value = var.shard_bits
  }
  set {
    name  = "config.mcScope"
    value = "site"
  }
  set {
    name  = "nodeSelector.bitcoin-mcast/role"
    value = "proxy"
  }
}

resource "helm_release" "retry" {
  for_each = var.retry_nodes     # map of {name, addr, tier, pref}
  name       = "retry-${each.key}"
  repository = "https://lightwebinc.github.io/bitcoin-retry-endpoint-helm"
  chart      = "bitcoin-retry-endpoint"
  version    = "0.1.0"
  namespace  = "bitcoin-mcast"

  set { name = "config.nackAddr";         value = each.value.addr }
  set { name = "config.beaconTier";       value = each.value.tier }
  set { name = "config.beaconPreference"; value = each.value.pref }
  set { name = "nodeSelector.bitcoin-mcast/node"; value = each.key }
}
```

---

## Option D — Plain Helm (imperative)

```bash
NS=bitcoin-mcast
kubectl create namespace $NS

helm install proxy bitcoin-shard-proxy-helm/ -n $NS \
  --set config.multicastIf=enp5s0 \
  --set config.shardBits=8 \
  --set nodeSelector."bitcoin-mcast/role"=proxy

helm install listener bitcoin-shard-listener-helm/ -n $NS \
  --set config.multicastIf=enp5s0 \
  --set config.shardBits=8 \
  --set config.retryEndpoints="[fd20::24]:9300\,[fd20::25]:9300" \
  --set workloadType=DaemonSet \
  --set nodeSelector."bitcoin-mcast/role"=listener

for i in 1 2 3; do
  helm install retry-node-$i bitcoin-retry-endpoint-helm/ -n $NS \
    --set config.nackAddr="fd20::2$((i+3))" \
    --set nodeSelector."bitcoin-mcast/node"="retry-$i"
done
```

---

## Dependency ordering

No hard dependency ordering is required by Kubernetes — all pods tolerate a brief startup window where peers are unavailable:

- Listener starts without retry endpoints: NACK dispatch fails silently until `RETRY_ENDPOINTS` are reachable or beacon discovery populates the registry. Gaps accumulate in the gap tracker but are not lost until `NACK_GAP_TTL` expires (default 10m).
- Retry endpoint starts without multicast frames: joins groups, receives nothing, serves no NACKs. No error.
- Proxy starts without listeners: sends to empty groups; frames are delivered once listeners join.

A conservative startup sequence for scripted deploys: proxy → retry endpoints → listeners. Allow 10s between each stage for MLD/beacon propagation.

---

## Namespace and RBAC

All components run under their chart's `ServiceAccount`. No cross-namespace communication is needed. The only external dependencies are:

- DNS resolution for `RETRY_ENDPOINTS` (if using hostnames vs. IPv6 literals)
- Redis (if `cacheBackend=redis` for retry endpoint)
- External Prometheus scrape (out of band — no cluster RBAC needed)

Minimal `NetworkPolicy` for `hostNetwork` pods: because `hostNetwork` pods share the host network namespace, Kubernetes `NetworkPolicy` does not apply to them. Network segmentation must be handled at the host level (nftables, firewall rules) as it is in the existing Ansible roles.

---

## Future: unicast egress mode

When `EGRESS_MODE=unicast-list` is added to `bitcoin-shard-proxy`, the proxy will send unicast UDP to an explicit list of listener addresses instead of multicast groups. This eliminates the `hostNetwork` requirement and allows standard CNI deployment:

```yaml
# Future values.yaml addition
config:
  egressMode: "unicast-list"
  egressTargets:
    - "[10.0.0.1]:9001"
    - "[10.0.0.2]:9001"
    - "[10.0.0.3]:9001"
```

When this mode is implemented, remove `hostNetwork: true` from proxy and listener charts, and drop the `NET_ADMIN` capability requirement. The composition spec remains identical in all other respects.
