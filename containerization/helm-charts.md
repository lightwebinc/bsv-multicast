# Helm Charts

## Layout

One chart per component repo, `-helm` suffix on the repo name, independent release cadence. No umbrella chart.

| Chart repo | Chart name | App repo |
|---|---|---|
| `shard-proxy-helm` | `shard-proxy` | `shard-proxy` |
| `shard-listener-helm` | `shard-listener` | `shard-listener` |
| `retry-endpoint-helm` | `retry-endpoint` | `retry-endpoint` |
| `subtx-generator-helm` | `subtx-generator` | `subtx-generator` |

### Why no umbrella chart

Operators are expected to manage composition through their own GitOps toolchain (Helmfile, ArgoCD, Flux, Terraform, plain Helm). An umbrella chart would constrain versioning alignment across all four components and create unnecessary coupling. See [composition-spec.md](composition-spec.md).

---

## Repo structure (per chart repo)

```
shard-proxy-helm/
  Chart.yaml             # apiVersion v2, kubeVersion >= 1.27, maintainers, ArtifactHub annotations
  values.yaml            # full surface — every binary flag exposed under .config
  values.schema.json     # JSON Schema validation at `helm install` time
  cr.yaml                # chart-releaser config (owner, git-repo)
  templates/
    _helpers.tpl
    NOTES.txt
    serviceaccount.yaml
    service.yaml
    deployment.yaml      # (+ daemonset.yaml for listener; + job.yaml for subtx-gen)
    hpa.yaml             # optional, gated by autoscaling.enabled
    pdb.yaml             # optional, gated by podDisruptionBudget.enabled
    servicemonitor.yaml  # optional, gated by metrics.serviceMonitor.enabled
    networkpolicy.yaml   # optional, gated by networkPolicy.enabled
    tests/
      test-metrics-endpoint.yaml   # `helm test` busybox probe
  .github/
    workflows/
      lint.yml           # helm lint --strict + helm template smoke; every push/PR
      release.yml        # chart-releaser + OCI push; DISABLED, manual + RELEASE confirm
  .helmignore
  LICENSE
  NOTICE
  README.md
```

Per-chart deltas:
- `shard-listener-helm/templates/` adds `daemonset.yaml`; switched via `workloadType`.
- `subtx-generator-helm/templates/` has `deployment.yaml` and `job.yaml` (switched via `workloadType`); no `servicemonitor.yaml` / `hpa.yaml` / `test-metrics-endpoint.yaml` (binaries are flag-only, no metrics endpoint).
- `retry-endpoint-helm/` has no Redis subchart — operator manages Redis externally and sets `config.redisAddr`.

---

## Chart.yaml (example — proxy)

```yaml
apiVersion: v2
name: shard-proxy
description: IPv6 multicast frame proxy for the Bitcoin transaction distribution network
type: application
version: 0.1.0          # chart semver — incremented independently of appVersion
appVersion: "0.1.0"     # matches OCI image tag
keywords: [bitcoin, multicast, brc-124, brc-128]
home: https://github.com/lightwebinc/shard-proxy
sources:
  - https://github.com/lightwebinc/shard-proxy
  - https://github.com/lightwebinc/shard-proxy-helm
```

---

## values.yaml cross-reference

All four charts share the same top-level key structure. Per-component notes are inline.

### Common keys (all charts)

```yaml
replicaCount: 1

image:
  repository: ghcr.io/lightwebinc/shard-proxy   # adjust per chart
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
    namespace: bsv-mcast
    fabricIPv6: ""          # e.g. "fd20::21/64" — must be unique per pod
    interface: net1
  host:
    dnsPolicy: ClusterFirstWithHostNet

resources: {}
nodeSelector: {}
tolerations: []
affinity: {}
```

### shard-proxy values

Full `.config` surface mirrors every flag in `shard-proxy/config/config.go`:
`listenAddr`, `udpListenPort`, `tcpListenPort`, `multicastIf`, `egressPort`, `shardBits` (default `2`), `mcScope`, `mcGroupId`, `numWorkers`, `fragMtu`, `drainTimeout`, `debug`, `metricsAddr`, `instanceId`, `otlpEndpoint`, `otlpInterval`.

Additional top-level keys:

```yaml
service: { type: ClusterIP, metricsPort: 9100 }
metrics:
  enabled: true
  path: /metrics
  port: 9100
  serviceMonitor: { enabled: false, interval: 30s, scrapeTimeout: 10s, labels: {}, relabelings: [], metricRelabelings: [] }
probes:
  readiness: { enabled: true, initialDelaySeconds: 5, periodSeconds: 10, ... }
  liveness:  { enabled: true, initialDelaySeconds: 10, periodSeconds: 30, ... }
autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 4
  targetCPUUtilizationPercentage: 80
podDisruptionBudget: { enabled: false, minAvailable: 1 }
networkPolicy: { enabled: false, ingressFrom: [] }
extraEnv: []                  # passthrough for forward-compat
```

### shard-listener values

Full `.config` surface mirrors every flag in `shard-listener/config/config.go`:
- Core: `multicastIf`, `listenPort`, `shardBits` (default `2`), `mcScope`, `mcGroupId`, `shardInclude`, `subtreeInclude`, `subtreeExclude`, `egressAddr`, `egressProto`, `stripHeader`.
- Multicast egress (BRC-128 bridging): `mcEgressEnabled`, `mcEgressIface`, `mcEgressPort`, `mcEgressScope`, `mcEgressGroupId`, `mcEgressHopLimit`.
- Block header egress (BRC-131 SPV): `headerEgressEnabled`, `headerEgressAddr`, `headerEgressProto`, `headerMcEgressEnabled`, `headerMcEgressIface`, `headerMcEgressPort`, `headerMcEgressScope`, `headerMcEgressGroupId`, `headerMcEgressHopLimit`.
- NACK / retry: `retryEndpoints`, `nackJitterMax`, `nackBackoffMax`, `nackMaxRetries`, `nackGapTtl`.
- Beacon (BRC-126): `beaconEnabled`, `beaconPort`, `beaconScope`.
- BRC-127 subtree groups: `subtreeGroups`, `subtreeGroupDefaultTtl`, `announceScope`, `senderInclude`, `senderExclude`.
- BRC-132 subtree data: `subtreeDataEnabled`, `subtreeDataVerifyMerkle`.
- Egress dedup: `egressDedupCap`, `egressDedupTtl`, `txidDedupAddr`, `txidDedupPrefix`, `txidDedupTtl`.
- Runtime: `numWorkers` (hardcoded to 1 by chart), `verifyPayloadHash`, `drainTimeout`, `debug`.
- Observability: `metricsAddr`, `instanceId`, `otlpEndpoint`, `otlpInterval`.

Additional top-level keys: `workloadType: Deployment | DaemonSet`, plus the same `service`, `metrics`, `probes`, `podDisruptionBudget`, `networkPolicy`, `updateStrategy`, `extraEnv` blocks as the proxy.

> **`numWorkers` is hardcoded to `1` in the rendered Deployment/DaemonSet template regardless of input.** Linux SO_REUSEPORT delivers each multicast datagram to all sockets in the reuseport group — multiple workers cause N-fold frame duplication. The `values.schema.json` rejects any other value.

### retry-endpoint values

Full `.config` surface mirrors every flag in `retry-endpoint/config/config.go`:
- Ingress: `mcIface`, `listenPort`, `shardBits` (default `2`, standardized across charts — binary defaults to `8`), `mcScope`, `mcGroupId`.
- Egress: `egressIface`, `egressPort`, `dedupWindow`.
- NACK server: `nackPort`, `nackAddr` (effectively required), `nackWorkers`.
- Cache: `cacheBackend` (memory|redis), `redisAddr`, `cacheTtl`, `cacheTtlTx`, `cacheTtlBlock`, `cacheTtlSubtree`, `cacheTtlAnchor`, `cacheMaxKeys`.
- Rate limits: `rlIpRate`, `rlIpBurst`, `rlChainRate`, `rlChainWindow`, `rlSequenceMax`, `rlSequenceWindow`, `rlGroupRate`, `rlGroupBurst`.
- Beacon: `beaconEnabled`, `beaconTier`, `beaconPreference`, `beaconInterval`, `beaconScope`, `beaconFlagsUnicast`, `beaconFlagsMulticast`, `beaconFlagsDraining`.
- Response suppression: `suppressAck`, `suppressMiss`.
- BRC-132: `subtreeDataEnabled`.
- Runtime / observability: `drainTimeout`, `debug`, `metricsAddr`, `instanceId`, `otlpEndpoint`, `otlpInterval`.

No Redis subchart. Operators run Redis separately and set `config.redisAddr` when `config.cacheBackend=redis`.

> **`nackAddr` is effectively required.** The binary auto-detects from the egress interface, which may resolve to a SLAAC address that listeners' `RETRY_ENDPOINTS` lists do not match — ACK/MISS replies are then silently filtered. The chart emits a `helm.sh/chart-warnings` pod annotation and a `NOTES.txt` warning when `nackAddr` is empty.

### subtx-generator values

The four binaries (`subtx-gen`, `send-anchor-frame`, `send-block-announce`, `send-subtree-data`) accept **CLI flags only** — there are no environment variables. The chart selects a binary via `.Values.mode` and translates the matching per-mode args block into the container's `command` + `args`. Zero/empty values are omitted so each binary's native defaults apply.

```yaml
mode: "subtx-gen"          # subtx-gen | send-anchor-frame | send-block-announce | send-subtree-data
workloadType: "Deployment" # Deployment | Job

args:
  addr: "[::1]:9000"       # shared across all binaries

subtxGen:                  # full surface of cmd/subtx-gen
  frameVersion: 2
  shardBits: 2
  subtrees: 8
  subtreeSeed: "subtx-generator-default"
  pps: 1000
  duration: "10s"
  count: 0
  workers: 0
  payloadSize: 512
  payloadFormat: "brc124"
  seqStart: 1
  seqGapEvery: 0
  seqGapSize: 1
  seqGapDelay: "0s"
  logInterval: "1s"
  printSubtrees: false
  subtreeGroup: ""
  announceAddr: ""
  announceInterval: "10s"
  announceTtl: 0
  announcePhaseSize: 0
  announcePhaseInterval: "0s"
  corruptTxidRate: 0

sendAnchorFrame:    { count: 10, payloadSize: 256, interval: "50ms", tcp: false }
sendBlockAnnounce:  { blocks: 10, subtrees: 4, interval: "100ms", coinbase: true }
sendSubtreeData:    { frames: 20, msgType: "hashes", nodes: 16, payloadSize: 0, subtreeCount: 0, interval: "50ms" }

job:
  completions: 1
  parallelism: 1
  backoffLimit: 0
  ttlSecondsAfterFinished: 600

# Pure UDP/TCP client. No MLD join, no fabric NIC required.
networking:
  mode: pod                # pod | host | multus
```

The generator binaries do not expose a `/metrics` endpoint; `metrics.enabled` defaults to `false`, no `ServiceMonitor` template is shipped, and no `helm test` probe is included.

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
kubectl label node fabric-node-1 bsv-mcast/fabric-iface=enp5s0
```

Node selector in values:

```yaml
nodeSelector:
  bsv-mcast/fabric-iface: enp5s0
```

See [k0s-deployment.md](k0s-deployment.md) for full node labeling strategy and `NetworkAttachmentDefinition` examples.

---

## Publishing — gated workflow

All chart repos include two GH Actions workflows:

### `lint.yml` (always enabled)

Runs on every push and PR:

- `helm lint` (default + `--strict`).
- `helm template` smoke renders against multiple value permutations (Multus default, hostNetwork fallback, ServiceMonitor + PDB + NetworkPolicy + HPA enabled).
- For `shard-listener-helm`, renders both `workloadType=Deployment` and `workloadType=DaemonSet`.
- For `subtx-generator-helm`, renders all four `mode` values across both `Deployment` and `Job` workload types.

### `release.yml` (disabled by default)

```yaml
on:
  workflow_dispatch:          # manual trigger only
    inputs:
      confirm:
        description: "Type RELEASE to publish chart to GH Pages and OCI registry"
        required: true

jobs:
  release:
    if: github.event.inputs.confirm == 'RELEASE'
    runs-on: ubuntu-latest
    environment: production   # second-layer GitHub Environment reviewer gate
    steps:
      - uses: helm/chart-releaser-action@v1.6.0   # GH Pages publish
      - run: |
          helm package .
          helm push *.tgz oci://ghcr.io/lightwebinc/charts
```

**The `release.yml` file exists from day one but requires:**
1. Manual `workflow_dispatch` with the literal string `RELEASE` as confirmation, AND
2. A `production` GitHub Environment with required reviewer approval (configured out-of-band when Phase 6 is approved).

No automated trigger. No tag-based trigger. The GH Pages `gh-pages` branch is created by `chart-releaser-action` on first publish; Pages must be enabled in repo settings by an admin before the first release.

`cr.yaml` per repo contains:

```yaml
owner: lightwebinc
git-repo: <chart-name>-helm
package-path: .cr-release-packages
skip-existing: true
sign: false
```
