# CI Strategy

## Overview

Two complementary layers:

| Layer | Tool | Where it runs | Triggers |
|---|---|---|---|
| **Unit + compile + lint** | `go test ./...`, `golangci-lint` | GitHub Actions hosted runner (ubuntu-latest) | Every push, every PR |
| **Docker E2E** | Dagger (Go SDK) | GitHub Actions **self-hosted** runner | Every push to `main`, manual |
| **Integration / multicast** | Dagger (Go SDK) → Go harness Docker driver | Self-hosted runner (fabric-attached host) | Manual dispatch + nightly |
| **LXD full-stack** | Existing bash scenarios | Self-hosted runner (LXD host) | Manual dispatch + nightly |

**No artifact is published without explicit manual approval.** See [gating policy](#gating-policy).

---

## Dagger as the pipeline definition language

Dagger is the single source of truth for all pipeline logic. GH Actions `.yml` files are thin wrappers that call `dagger run go run ./ci/...`. This means:

- Any developer can reproduce the exact CI run locally: `go run ./ci/ unit` or `go run ./ci/ e2e`
- No YAML DSL magic — pipeline logic is type-safe Go
- Dagger's cache layer deduplicates repeated Go module downloads across runs

### Dagger pipeline structure (per component repo)

```
ci/
  main.go          # Dagger entry point: subcommands unit, e2e, lint, publish
  pipeline/
    build.go       # image build function
    test.go        # unit + e2e test functions
    publish.go     # image push (gated)
```

### Example: unit pipeline

```go
func Unit(ctx context.Context, client *dagger.Client, src *dagger.Directory) error {
    golang := client.Container().
        From("golang:1.25").
        WithDirectory("/src", src).
        WithWorkdir("/src").
        WithExec([]string{"go", "test", "-race", "-count=1", "./..."})
    _, err := golang.Sync(ctx)
    return err
}
```

### Example: Docker E2E pipeline (proxy)

```go
func E2E(ctx context.Context, client *dagger.Client, src *dagger.Directory) error {
    // Build proxy image
    proxyImg := client.Container().
        From("golang:1.25").
        WithDirectory("/src", src).
        WithWorkdir("/src").
        WithExec([]string{"go", "build", "-trimpath", "-buildvcs=false", "-o", "/proxy", "."}).
        From("ubuntu:24.04").
        WithFile("/proxy", ...).
        WithEntrypoint([]string{"/proxy"})

    // Run E2E via docker-compose or direct container wiring
    result := client.Container().
        From("golang:1.25").
        WithServiceBinding("proxy", proxyImg.AsService()).
        WithDirectory("/src", src).
        WithWorkdir("/src/test").
        WithExec([]string{"bash", "run-e2e.sh"})
    _, err := result.Sync(ctx)
    return err
}
```

---

## GitHub Actions tiers

### Tier 1 — hosted runner (unit/lint/compile)

File: `.github/workflows/ci.yml` in each component repo (already exists for proxy, listener, retry-endpoint).

```yaml
on:
  push:
    branches: ["**"]
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version-file: go.mod }
      - run: go test -race -count=1 ./...
      - run: go vet ./...

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: golangci/golangci-lint-action@v6
        with: { version: latest }
```

### Tier 2 — self-hosted runner (Docker E2E)

The self-hosted runner runs on any Linux host with Docker installed. No physical multicast NIC required — uses the user-defined IPv6 bridge approach from [docker-test-infra.md](docker-test-infra.md).

```yaml
jobs:
  docker-e2e:
    runs-on: [self-hosted, linux, docker]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version-file: go.mod }
      - name: Setup IPv6 bridge
        run: |
          docker network create --driver bridge --ipv6 \
            --subnet fd10::/64 mcast-fabric 2>/dev/null || true
          BRIDGE=$(docker network inspect mcast-fabric \
            --format '{{.Options}}' | grep -oP 'bridge.name=\K\S+')
          echo 1 | sudo tee /sys/class/net/$BRIDGE/bridge/mcast_snooping
          echo 1 | sudo tee /sys/class/net/$BRIDGE/bridge/mcast_querier6
      - name: Run E2E
        run: go run ./ci/ e2e
```

### Tier 3 — self-hosted runner (integration with real multicast)

Runs on the dedicated lab host (same machine running the LXD VMs). Requires fabric NIC and MLD-capable switch. This tier runs the full Go harness with the Docker driver against a real physical multicast fabric.

```yaml
jobs:
  integration:
    runs-on: [self-hosted, linux, mcast-fabric]
    if: github.ref == 'refs/heads/main' || github.event_name == 'workflow_dispatch'
    steps:
      - uses: actions/checkout@v4
        with:
          repository: lightwebinc/bitcoin-multicast-test
          path: bitcoin-multicast-test
      - name: Run integration scenarios
        run: |
          cd bitcoin-multicast-test
          go test ./harness/... -driver docker -timeout 30m -v
```

### Tier 4 — LXD full-stack (nightly)

Runs the existing bash scenario suite unchanged. See [lxd-coexistence.md](lxd-coexistence.md).

```yaml
jobs:
  lxd-scenarios:
    runs-on: [self-hosted, linux, lxd]
    if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
    steps:
      - uses: actions/checkout@v4
        with:
          repository: lightwebinc/bitcoin-multicast-test
      - name: Run all scenarios
        run: bash scenarios/run-all.sh
        timeout-minutes: 60
```

---

## Self-hosted runner requirements

| Runner label | Host requirements |
|---|---|
| `[self-hosted, linux, docker]` | Docker ≥ 24, Go ≥ 1.25, sudo for bridge sysfs writes |
| `[self-hosted, linux, mcast-fabric]` | All of the above + fabric NIC + MLD-capable switch |
| `[self-hosted, linux, lxd]` | LXD ≥ 5.x, existing lab profiles, `lxc` in PATH |

The lab host satisfies all three labels simultaneously. Register it once with all labels:

```bash
./config.sh --url https://github.com/lightwebinc/bitcoin-shard-proxy \
            --token <token> \
            --labels self-hosted,linux,docker,mcast-fabric,lxd
```

---

## Dagger local usage

Developers can run any tier locally without GH Actions:

```bash
# In any component repo
cd bitcoin-shard-proxy

# Unit tests
go run ./ci/ unit

# Docker E2E (requires Docker)
go run ./ci/ e2e

# Build image locally (no push)
go run ./ci/ build --tag dev

# Integration (requires harness repo alongside)
go run ./ci/ integration --driver docker

# Show all subcommands
go run ./ci/ --help
```

---

## Gating policy

### What is gated

| Artifact | Gate |
|---|---|
| OCI image push to GHCR | `workflow_dispatch` with `RELEASE` confirmation |
| Helm chart publish (GH Pages) | Same |
| Helm OCI push to GHCR | Same |
| GitHub Release tag | Same |

### What is NOT gated (always runs)

- `go test`, `go vet`, `golangci-lint` — every push
- `helm lint`, `helm template` — every push to chart repos
- Docker image **build** (no push) — every push to component repos
- Docker E2E tests — every push to `main` on self-hosted runner

### Gate implementation

In every publish workflow:

```yaml
on:
  workflow_dispatch:
    inputs:
      confirm:
        description: "Type RELEASE to publish images and charts"
        required: true

jobs:
  publish:
    if: github.event.inputs.confirm == 'RELEASE'
    runs-on: ubuntu-latest
    environment: production   # optional: GH Environment approval gate
    steps:
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - run: |
          docker build -t ghcr.io/lightwebinc/bitcoin-shard-proxy:${{ github.sha }} .
          docker push ghcr.io/lightwebinc/bitcoin-shard-proxy:${{ github.sha }}
```

Adding a GitHub Environment (`production`) with required reviewer approval adds a second human-in-the-loop confirmation on top of the `RELEASE` string.

---

## Multi-repo CI for bitcoin-shard-listener

The listener's E2E already requires both `bitcoin-shard-listener` and `bitcoin-shard-proxy` checked out (for `send-test-frames`). The dual-checkout pattern used today:

```yaml
- uses: actions/checkout@v4
  with:
    repository: lightwebinc/bitcoin-shard-proxy
    path: bitcoin-shard-proxy
- uses: actions/checkout@v4
  with:
    path: bitcoin-shard-listener
```

The Dagger E2E function loads both directories via `client.Host().Directory()` and handles the relative path assumption in `Dockerfile.e2e`.
