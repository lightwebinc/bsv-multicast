# Helm Charts

## Layout

One chart per component repo, `-helm` suffix on the repo name, independent release cadence. No umbrella chart.

| Chart repo | Chart name | App repo |
|---|---|---|
| `bitcoin-shard-proxy-helm` | `bitcoin-shard-proxy` | `bitcoin-shard-proxy` |
| `bitcoin-shard-listener-helm` | `bitcoin-shard-listener` | `bitcoin-shard-listener` |
| `bitcoin-retry-endpoint-helm` | `bitcoin-retry-endpoint` | `bitcoin-retry-endpoint` |
| `bitcoin-subtx-generator-helm` | `bitcoin-subtx-generator` | `bitcoin-subtx-generator` |

### Why no umbrella chart

Operators are expected to manage composition through their own GitOps toolchain (Helmfile, ArgoCD, Flux, Terraform, plain Helm). An umbrella chart would constrain versioning alignment across all four components and create unnecessary coupling. See [composition-spec.md](composition-spec.md).

---

## Repo structure (per chart repo)

```
bitcoin-shard-proxy-helm/
  Chart.yaml
  values.yaml
  templates/
    deployment.yaml
    service.yaml
    serviceaccount.yaml
    hpa.yaml            (optional)
    _helpers.tpl
  .github/
    workflows/
      lint.yml          # helm lint + helm template smoke-check; always runs
      release.yml       # chart-releaser; DISABLED until approved
  README.md
```

---

## Chart.yaml (example — proxy)

```yaml
apiVersion: v2
name: bitcoin-shard-proxy
description: IPv6 multicast frame proxy for the Bitcoin transaction distribution network
type: application
version: 0.1.0          # chart semver — incremented independently of appVersion
appVersion: "0.1.0"     # matches OCI image tag
keywords: [bitcoin, multicast, brc-124, brc-128]
home: https://github.com/lightwebinc/bitcoin-shard-proxy
sources:
  - https://github.com/lightwebinc/bitcoin-shard-proxy
  - https://github.com/lightwebinc/bitcoin-shard-proxy-helm
```

---

## values.yaml cross-reference

All four charts share the same top-level key structure. Per-component notes are inline.

### Common keys (all charts)

```yaml
replicaCount: 1

image:
  repository: ghcr.io/lightwebinc/bitcoin-shard-proxy   # adjust per chart
  pullPolicy: IfNotPresent
  tag: ""           # defaults to Chart.appVersion

imagePullSecrets: []
nameOverride: ""
fullnameOverride: ""

serviceAccount:
  create: true
  name: ""

podAnnotations: {}
podSecurityContext: {}

networking:
  mode: multus              # multus (default) | host | unicast (future)
  multus:
    # Per-release pod IPv6 on the dedicated mcast NIC. Required when mode=multus.
    networkName: mcast-fabric
    namespace: bitcoin-mcast
    fabricIPv6: ""          # e.g. "fd20::21/64" — must be unique per pod
    interface: net1
  host:
    dnsPolicy: ClusterFirstWithHostNet

resources: {}
nodeSelector: {}
tolerations: []
affinity: {}
```

### bitcoin-shard-proxy values

```yaml
config:
  listenAddr: "[::]"
  udpListenPort: 9000
  tcpListenPort: 0
  multicastIf: "eth0"         # NIC for multicast egress on the host
  egressPort: 9001
  shardBits: 2
  mcScope: "site"
  mcGroupId: "0x000B"
  numWorkers: 0               # 0 = runtime.NumCPU
  debug: false
  drainTimeout: "0s"
  fragMtu: 0
  metricsAddr: ":9100"
  instanceId: ""
  otlpEndpoint: ""
  otlpInterval: "30s"

service:
  type: ClusterIP
  metricsPort: 9100

metrics:
  enabled: true               # expose /metrics
  path: /metrics
  port: 9100
  serviceMonitor:
    enabled: false            # opt-in; requires kube-prometheus-stack CRDs
    interval: 30s
    labels: {}                # e.g. { release: kube-prometheus-stack }
```

### bitcoin-shard-listener values

```yaml
config:
  multicastIf: "eth0"
  listenPort: 9001
  shardBits: 2
  mcScope: "site"
  mcGroupId: "0x000B"
  shardInclude: ""
  subtreeInclude: ""
  subtreeExclude: ""
  egressAddr: "127.0.0.1:9100"
  egressProto: "udp"
  stripHeader: false
  retryEndpoints: ""          # "host:port,..." — set by operator
  beaconEnabled: true
  beaconPort: 9300
  nackJitterMax: "200ms"
  nackBackoffMax: "5s"
  nackMaxRetries: 5
  nackGapTtl: "10m"
  numWorkers: 1               # FIXED AT 1 — template enforces this
  debug: false
  metricsAddr: ":9200"
  instanceId: ""
  otlpEndpoint: ""
  otlpInterval: "30s"

service:
  metricsPort: 9200
```

> **`numWorkers` is hardcoded to `1` in the Deployment template regardless of values.** The listener's SO_REUSEPORT design delivers multicast to all sockets in the group; multiple workers cause N-fold duplication. A note in `values.yaml` explains this constraint.

### bitcoin-retry-endpoint values

```yaml
config:
  mcIface: "eth0"
  listenPort: 9001
  shardBits: 8
  mcScope: "site"
  egressIface: "eth0"
  egressPort: 9001
  nackPort: 9300
  nackAddr: ""                # REQUIRED — set to node IPv6 fabric address
  cacheBackend: "memory"      # memory | redis
  redisAddr: ""               # required when cacheBackend=redis
  cacheTtlTx: "60s"
  cacheTtlBlock: "10m"
  beaconEnabled: true
  beaconTier: 0
  beaconPreference: 128
  beaconInterval: "60s"
  rlGroupRate: "200"          # tokens/s
  rlGroupBurst: "50"
  rlChainRate: "2000"
  rlChainBurst: "200"
  metricsAddr: ":9400"
  instanceId: ""
  otlpEndpoint: ""
  otlpInterval: "30s"

# Optional Redis subchart (bitnami/redis)
redis:
  enabled: false
  auth:
    enabled: false

service:
  metricsPort: 9400
```

> **`nackAddr` must be explicitly set** — the binary cannot reliably auto-detect the SLAAC address that listeners trust. The Helm template emits a `helm.sh/chart-warnings` annotation when `nackAddr` is empty.

### bitcoin-subtx-generator values

```yaml
mode: "subtx-gen"   # subtx-gen | send-anchor-frame | send-block-announce | send-subtree-data
                    # Sets the container command

config:
  target: ""                  # proxy UDP host:port
  shardBits: 2
  rateHz: 1000
  seqGapDelay: "0s"           # artificial gap injection for testing
  mcIface: "eth0"
  mcScope: "site"
  debug: false

# Run as a Job (finite) or Deployment (continuous)
workloadType: "Deployment"    # Deployment | Job

job:
  completions: 1
  parallelism: 1
```

---

## Deployment template — networking.mode dispatch (proxy example)

The template selects pod-spec fields based on `.Values.networking.mode`. Sketch:

```yaml
metadata:
  {{- if eq .Values.networking.mode "multus" }}
  annotations:
    k8s.v1.cni.cncf.io/networks: |
      [{
        "name": {{ .Values.networking.multus.networkName | quote }},
        "namespace": {{ .Values.networking.multus.namespace | quote }},
        "ips": [ {{ .Values.networking.multus.fabricIPv6 | quote }} ],
        "interface": {{ .Values.networking.multus.interface | quote }}
      }]
  {{- end }}
spec:
  {{- if eq .Values.networking.mode "host" }}
  hostNetwork: true
  dnsPolicy: {{ .Values.networking.host.dnsPolicy | default "ClusterFirstWithHostNet" }}
  {{- end }}
  containers:
    - name: {{ .Chart.Name }}
      image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
      env:
        - name: MULTICAST_IF
          {{- if eq .Values.networking.mode "multus" }}
          value: {{ .Values.networking.multus.interface | quote }}
          {{- else }}
          value: {{ .Values.config.multicastIf | quote }}
          {{- end }}
        - name: UDP_LISTEN_PORT
          value: {{ .Values.config.udpListenPort | quote }}
        # ... remaining env vars ...
      securityContext:
        capabilities:
          add: ["NET_ADMIN"]
      ports:
        - name: metrics
          containerPort: {{ .Values.service.metricsPort }}
          protocol: TCP
      readinessProbe:
        httpGet:
          path: /readyz
          port: metrics
        initialDelaySeconds: 5
        periodSeconds: 10
      livenessProbe:
        httpGet:
          path: /healthz
          port: metrics
        initialDelaySeconds: 10
        periodSeconds: 30
```

---

## Node labeling

Independent of `networking.mode`, label each node with the dedicated fabric NIC so pods land on a node where the NIC actually exists. With `multus` the macvlan `master` parameter in the `NetworkAttachmentDefinition` must name a NIC present on the node; with `host` the same constraint applies via `MULTICAST_IF`.

```bash
kubectl label node fabric-node-1 bitcoin-mcast/fabric-iface=enp5s0
```

Node selector in values:

```yaml
nodeSelector:
  bitcoin-mcast/fabric-iface: enp5s0
```

See [k0s-deployment.md](k0s-deployment.md) for full node labeling strategy and `NetworkAttachmentDefinition` examples.

---

## Publishing — gated workflow

All chart repos include two GH Actions workflows:

### `lint.yml` (always enabled)

```yaml
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/setup-helm@v4
      - run: helm lint .
      - run: helm template test . --debug
```

### `release.yml` (disabled by default)

```yaml
on:
  workflow_dispatch:          # manual trigger only
    inputs:
      confirm:
        description: "Type RELEASE to confirm"
        required: true

jobs:
  release:
    if: ${{ github.event.inputs.confirm == 'RELEASE' }}
    runs-on: ubuntu-latest
    steps:
      # chart-releaser (GitHub Pages) + helm push (OCI, GHCR)
      - uses: helm/chart-releaser-action@v1.6.0
        # ...
      - run: |
          helm package .
          helm push *.tgz oci://ghcr.io/lightwebinc
```

**The `release.yml` file exists in the repo from day one but requires a manual `workflow_dispatch` with the literal string `RELEASE` as confirmation. No automated trigger. No tag-based trigger.**

Both GH Pages index and OCI push happen in the same job; partial publish is prevented by the atomic `if:` guard.
