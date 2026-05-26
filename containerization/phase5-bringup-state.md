# Phase 5 Single-Node k0s Bring-Up — Implementation State

**Snapshot date:** 2026-05-25 (UTC-06:00)
**Driver:** Plan `~/.windsurf/plans/k0s-phase5-bootstrap-5d646c.md`
**Status:** Cluster + platform layer green. Apps stage blocked on GHCR org migration.

This document captures the state of the Phase 4.5 / Phase 5 / Phase 6 work so it can be picked back up cleanly after the `lightwebinc` → new-org GitHub migration completes.

---

## 1. Host

- Machine: `lax` (Ubuntu 24.04.4 LTS, kernel 6.17.0-29-generic).
- Multicast fabric NIC: `enp2s0f0` (9000 MTU) loopback-cabled to `enp2s0f1`. Host IPv6 on fabric: `fd00:dead:beef:1::1/64`.
- Management NIC: `eno1`.
- LXD lab VMs (`listener1-4`, `proxy`, `proxy2`, `retry1-3`, `redis`, `source`, `router1-2`, `metrics`): **stopped**, preserved on disk.
  - Restart with `@/home/light/repo/bitcoin-multicast-test/vm-lab/lab-start.sh`.
  - Re-stop with `@/home/light/repo/bitcoin-multicast-test/vm-lab/lab-stop.sh`.

## 2. Host tools installed

| Tool | Version | Path |
|---|---|---|
| `k0s` | v1.31.2+k0s.0 | `/usr/local/bin/k0s` |
| `k0sctl` | v0.20.0 | `/usr/local/bin/k0sctl` (installed but **not used** — see §4) |
| `kubectl` | v1.31.4 | `/usr/local/bin/kubectl` |
| `helmfile` | v0.169.2 | `/usr/local/bin/helmfile` |
| `helm` | v3.16.0 | `/home/light/.local/bin/helm` (pre-existing) |
| `envsubst` | 0.21 | `/usr/bin/envsubst` (pre-existing) |
| `docker` | 29.1.3 | system |
| `gh` | (logged in as `jefflightweb`) | system |

## 3. Cluster: k0s single-node controller+worker

Installed directly (no k0sctl/SSH) on the dev host:

```bash
sudo k0s install controller --single --config /etc/k0s/k0s.yaml
sudo k0s start
```

Config (`/etc/k0s/k0s.yaml`):

```yaml
apiVersion: k0s.k0sproject.io/v1beta1
kind: ClusterConfig
metadata:
  name: k0s
spec:
  network:
    provider: kuberouter
    podCIDR: 10.244.0.0/16
    serviceCIDR: 10.96.0.0/12
    kubeProxy:
      mode: iptables
  telemetry:
    enabled: false
  extensions:
    helm:
      charts: []
      repositories: []
```

**Important — dual-stack disabled.** The original `k0s-config.yaml.example` enabled IPv6 dual-stack with `IPv6podCIDR: fd20:0:0:1::/64`. With kuberouter and the default `--node-cidr-mask-size-ipv6=110`, the kube-controller-manager refuses to start with `"New CIDR set failed; the node CIDR size is too big"`. The 2^46-node allocator is rejected. To re-enable dual-stack, shrink the IPv6 podCIDR to `/108` (or larger numeric prefix) and verify node-mask sizing. For Phase 5 single-node, IPv4-only primary CNI is sufficient — IPv6 multicast still works on the macvlan secondary attached to `enp2s0f0`.

Kubeconfig: `@/home/light/repo/bitcoin-multicast-kube-infra/.kube/k0s.config` (chmod 600).

Node labels applied (`make label-nodes` equivalent — done manually):

```
bitcoin-mcast/fabric-iface=enp2s0f0
bitcoin-mcast/node=retry-1
bitcoin-mcast/role=proxy
bitcoin-mcast/role-proxy=true
bitcoin-mcast/role-listener=true
bitcoin-mcast/role-retry-endpoint=true
```

Multicast sysctls in `/etc/sysctl.d/80-bitcoin-mcast.conf`:

```
net.ipv6.conf.all.disable_ipv6 = 0
net.ipv6.conf.enp2s0f0.disable_ipv6 = 0
net.ipv6.conf.all.force_mld_version = 2
```

## 4. k0sctl distribution files (not currently in the active install path)

The user-edited copies under `@/home/light/repo/bitcoin-multicast-kube-infra/distributions/k0s/` exist for the documented k0sctl flow but were **not** used for this bring-up — direct `k0s install` is simpler for single-host. Files left in place:

- `hosts.env` — `NODE0_ADDR=127.0.0.1`, `NODE0_FABRIC_IFACE=enp2s0f0`, `SSH_USER=root`, `SSH_KEY=$HOME/.ssh/id_ed25519`.
- `k0sctl.yaml`, `k0s-config.yaml` — operator copies.

**Upstream template fix committed to the repo:**
`@/home/light/repo/bitcoin-multicast-kube-infra/distributions/k0s/k0sctl.yaml.example:37-41` was repaired for k0sctl v0.20 schema:
- Removed unsupported `configPath` field.
- Removed `!!str |` so `config:` inlines as a YAML object (not a string).
- `__INLINE_K0S_CONFIG__` is now at column 1; `bootstrap.sh`'s 6-space sed indent puts it under `config:` correctly.

Anyone retrying the k0sctl flow on a multi-host install can use the fixed template without further edits.

If we ever go back to k0sctl on this host, also set `~/.ssh/known_hosts` to contain all key types for `127.0.0.1` (the `ssh-keyscan -t ed25519` single-type entry caused `host key mismatch` because k0sctl's go-ssh client checks the offered key against any known type for the host — partial known_hosts → mismatch).

## 5. Platform layer (Phase 4.5 cluster-side)

Applied via `make platform CNI=kube-router NADS=mcast-fabric FABRIC_IFACE=enp2s0f0` from `@/home/light/repo/bitcoin-multicast-kube-infra/`.

Cluster state:

```
NAMESPACE          NAME                                              READY
external-secrets   external-secrets-cert-controller-...              1/1   Running
external-secrets   external-secrets-...                              1/1   Running
external-secrets   external-secrets-webhook-...                      1/1   Running
kube-system        coredns-...                                       1/1   Running
kube-system        kube-proxy-...                                    1/1   Running
kube-system        kube-router-...                                   1/1   Running
kube-system        metrics-server-...                                1/1   Running
kube-system        multus-multus-ds-...                              1/1   Running
```

NetworkAttachmentDefinitions:

```
NAMESPACE       NAME           AGE
bitcoin-mcast   mcast-fabric   ...
```

BGP NADs (`bgp-transit`, `bgp-ibgp`) intentionally skipped — single-node lab, no BGP fabric.

The benign `ClusterSecretStore "bitcoin-mcast-secret-store"` validation error during `platform-apply.sh` is the unconfigured-provider stub from `@/home/light/repo/bitcoin-multicast-kube-infra/platform/secrets/cluster-secret-store.example.yaml`. ESO works; only the example store is inert. Replace with a real provider when secrets land.

## 6. Apps stage (Phase 5 workloads) — **NOT YET APPLIED**

`make apps ENV=reference-k0s` was **not** run. It would fail because the pod images referenced by the Helm charts (`ghcr.io/lightwebinc/bitcoin-shard-{proxy,listener}`, etc.) are not published yet. See §7.

Chart versions pinned in `@/home/light/repo/bitcoin-multicast-kube-infra/apps/environments/default.yaml:7-11`:

```yaml
chartVersions:
  proxy: "0.1.0"
  listener: "0.1.0"
  retryEndpoint: "0.1.0"
  subtxGenerator: "0.1.0"
```

OCI helm charts are already pushed under `oci://ghcr.io/lightwebinc/charts/` at v0.1.0; they will need to be **re-pushed to the new org** (see §7).

## 7. Phase 6 image publish — **BLOCKED on GitHub org migration**

GHCR `docker push` to `ghcr.io/lightwebinc/bitcoin-shard-proxy:v0.1.0` returned `403 permission_denied: create_package`. `jefflightweb` is an org Member but lacks rights to create new packages in `lightwebinc`. User opted to migrate to a new GitHub org rather than grant per-package perms.

### Migration status (per user, 2026-05-25 ~16:18)

- New org created.
- Repos still being migrated; some failed and need retry.
- `jefflightweb` is already a member of the new org. Org has GitHub default settings.

### What needs to happen before the publish/apply can resume

1. **All 8 repos transferred to the new org:**
   - Component code: `bitcoin-shard-proxy`, `bitcoin-shard-listener`, `bitcoin-retry-endpoint`, `bitcoin-subtx-generator`
   - Helm charts: `bitcoin-shard-proxy-helm`, `bitcoin-shard-listener-helm`, `bitcoin-retry-endpoint-helm`, `bitcoin-subtx-generator-helm`
   - (Also useful but not blockers: `bitcoin-multicast-kube-infra`, `bitcoin-shard-common`, `bitcoin-shard-manifest`, `bitcoin-shard-manifest-helm`, `bitcoin-ingress`, `bitcoin-listener`, `bitcoin-retransmission`, `bitcoin-manifest`)

2. **Org Settings → Packages** (org Owner action):
   - **"Inherit access from source repository": ON**. With the `org.opencontainers.image.source` label on each Dockerfile (already present), this auto-grants repo collaborators package write rights — eliminates the per-package permissions dance.
   - **"Container creation"**: allow Members to create the visibility you want (Public / Private / Internal).

3. **PAT scopes** — current `gh` token for `jefflightweb` was refreshed earlier with `write:packages,read:packages` (good for the OLD org but works against any org). Verify with:
   ```bash
   gh auth status
   # Token scopes should include 'write:packages'
   ```
   If missing: `gh auth refresh -h github.com -s write:packages,read:packages,delete:packages`.

4. **Docker login refresh** after any token rotation:
   ```bash
   gh auth token | docker login ghcr.io -u jefflightweb --password-stdin
   ```

### Resume commands once unblocked

Let `NEW_ORG` = the new org slug.

```bash
# Component images (run from each repo root)
cd /home/light/repo/bitcoin-shard-proxy        && IMAGE=ghcr.io/$NEW_ORG/bitcoin-shard-proxy        TAG=v0.1.0 make ci-publish
cd /home/light/repo/bitcoin-shard-listener     && IMAGE=ghcr.io/$NEW_ORG/bitcoin-shard-listener     TAG=v0.1.0 make ci-publish
cd /home/light/repo/bitcoin-retry-endpoint     && IMAGE=ghcr.io/$NEW_ORG/bitcoin-retry-endpoint     TAG=v0.1.0 make ci-publish
cd /home/light/repo/bitcoin-subtx-generator    && IMAGE=ghcr.io/$NEW_ORG/bitcoin-subtx-generator    TAG=v0.1.0 make ci-publish
```

Then patch chart `values.yaml` repos and re-release the OCI charts:

```bash
# In each *-helm repo:
sed -i "s|ghcr.io/lightwebinc/|ghcr.io/$NEW_ORG/|g" values.yaml
# Bump Chart.yaml version if you want a clean v0.1.1, or keep v0.1.0 if charts haven't been pushed to new org yet.
# Re-run the chart release workflow (tag push v0.1.0 → release.yml in *-helm repo).
```

Finally update the Helmfile chart URL:

```
# @/home/light/repo/bitcoin-multicast-kube-infra/apps/helmfile.yaml.gotmpl
- chart: oci://ghcr.io/lightwebinc/charts/bitcoin-shard-proxy
+ chart: oci://ghcr.io/<NEW_ORG>/charts/bitcoin-shard-proxy
```

(Same for listener, retry-endpoint, subtx-generator.) Also update `repositories:` block.

## 8. Apps + verify (remaining work)

After §7 is unblocked:

```bash
cd /home/light/repo/bitcoin-multicast-kube-infra
make apps ENV=reference-k0s
make verify
```

Expected: proxy, listener, retry-1 Ready (single-node lab — only retry-1 is scheduled because only one node and the listener DaemonSet has nodeSelector `bitcoin-mcast/role-listener=true`, which is on `lax`). retry-2 and retry-3 will be Pending (no matching nodes) and that is acceptable.

The reference-k0s env (`@/home/light/repo/bitcoin-multicast-kube-infra/apps/environments/reference-k0s.yaml`) pins proxy fabric IPv6 to `fd20::20/64` and listener to `fd20::21/64`. The host fabric is on `fd00:dead:beef:1::/64`. The macvlan NAD on `enp2s0f0` will give pods L2 access; pick an IP plan compatible with what the host advertises. Either:
- Reassign host to `fd20::1/64` and keep chart defaults, or
- Override `fabricAddrs.proxy: fd00:dead:beef:1::20/64` etc. in `reference-k0s.yaml`.

## 9. E2E smoke (remaining)

Flip subtx-gen on in `reference-k0s.yaml`:

```yaml
subtxGenerator:
  enabled: true
  pps: 500
  duration: "15s"
```

`make apps ENV=reference-k0s` again, then scrape `bsl_frames_received_total` from the listener pod via `kubectl -n bitcoin-mcast exec ... -- wget -qO- http://127.0.0.1:9100/metrics | grep bsl_frames_received_total`. Confirm non-zero delta.

## 10. Roadmap status to flip (remaining)

In `@/home/light/repo/bitcoin-multicast/containerization/roadmap.md`:

- Phase 4.5 cluster-side bits: **done** (Multus DS + mcast-fabric NAD verified Running on k0s lab). Docker-harness BGP scenarios 40–42 remain a separate Phase 4.5 line-item.
- Phase 5: cluster + platform **done**; apps **pending** image publish.
- Phase 6: **in progress**, gated on org migration.

## 11. Repo edits made (file-level)

| File | Change |
|---|---|
| `@/home/light/repo/bitcoin-multicast-test/vm-lab/lab-stop.sh` | New — `lxc stop --all` wrapper. |
| `@/home/light/repo/bitcoin-multicast-test/vm-lab/lab-start.sh` | New — idempotent ordered VM start. |
| `@/home/light/repo/bitcoin-multicast-kube-infra/distributions/k0s/k0sctl.yaml.example:37-41` | Removed `configPath`, removed `!!str \|` from `config`, restructured `__INLINE_K0S_CONFIG__` placeholder. Compatible with k0sctl v0.20+. |
| `@/home/light/repo/bitcoin-multicast-kube-infra/distributions/k0s/k0sctl.yaml` | Rendered copy of fixed template (gitignored). |
| `@/home/light/repo/bitcoin-multicast-kube-infra/distributions/k0s/k0s-config.yaml` | Local copy with `provider: kuberouter` (gitignored). |
| `@/home/light/repo/bitcoin-multicast-kube-infra/distributions/k0s/hosts.env` | Local copy for `lax` (gitignored). |
| `/etc/k0s/k0s.yaml` (host) | Active k0s ClusterConfig — IPv4-only kuberouter. |
| `/etc/sysctl.d/80-bitcoin-mcast.conf` (host) | Multicast sysctls for `enp2s0f0`. |
| `~/.docker/config.json` | `ghcr.io` credentials (jefflightweb). |
| `/root/.ssh/authorized_keys` | `light@lax` ed25519 pubkey (no longer required; can be removed). |
| `~/.ssh/known_hosts` | Full keyscan of `127.0.0.1` (all key types). |

No edits made to: any `*-helm` repo, any component repo source, `bitcoin-multicast-kube-infra/Makefile`, helmfile or platform yaml.

## 12. To resume — TL;DR checklist

1. Finish migrating the 8 repos to the new GitHub org.
2. Org Owner: turn on **Inherit access from source repository** in Org → Settings → Packages.
3. Confirm `gh` token has `write:packages` and re-login docker: `gh auth token \| docker login ghcr.io -u <you> --password-stdin`.
4. Run the 4 `make ci-publish` commands in §7.
5. Update 4 chart `values.yaml` files, bump+release charts to new OCI namespace.
6. Update `apps/helmfile.yaml.gotmpl` chart URLs.
7. Reconcile `reference-k0s.yaml` `fabricAddrs.*` with the host's chosen fabric IPv6 prefix.
8. `make apps ENV=reference-k0s && make verify` in `bitcoin-multicast-kube-infra/`.
9. Smoke test by enabling subtx-gen briefly.
10. Mark Phases 4.5/5/6 complete in `roadmap.md`.

## 13. Roll-back

- Destroy apps + platform: `make teardown` in `bitcoin-multicast-kube-infra/`.
- Destroy cluster: `sudo k0s stop && sudo k0s reset && sudo rm -rf /var/lib/k0s /etc/k0s /usr/local/bin/k0s`.
- Restart LXD lab: `vm-lab/lab-start.sh`.
