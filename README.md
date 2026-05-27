# BSV Layered Multicast

A high-throughput, horizontally-scalable transaction distribution system for
BSV (Bitcoin SV) designed to pave the road towards 1 billion+ transactions per
second. It uses IPv6 multicast to efficiently distribute transaction data across
a fabric of subscribers (miners, exchanges, service providers) with
deterministic sharding and NACK-based reliability.

**Conceptual Attribution:** The IPv6 multicast transaction broadcast
architecture from which this software draws inspiration was articulated by Dr.
Craig S. Wright in
[Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast).

## Position

The reference architecture treats IPv6 multicast as the only medium that can
carry BSV at billion-transactions-per-second scale: anycast-load-balanced
ingress proxies deterministically shard transactions onto independent multicast
groups, each subscriber receives only the shards it cares about, and lost
packets are recovered through per-shard NACK requests to cache endpoints that
re-multicast the missing frames. This project is the concrete implementation of
that pipeline — stateless sharded ingress, multicast fan-out fabric, NACK-based
sharded resends, and hierarchical distribution of blocks, subtrees, coinbase,
and anchor transactions over reserved control groups.

## Repositories

| Repository                                                                        | Role                    | Purpose                                                                                     |
| --------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------- |
| [shard-proxy](https://github.com/lightwebinc/shard-proxy)         | Ingress                 | Stateless ingress proxy; receives frames, derives multicast group, forwards verbatim        |
| [ingress-infra](https://github.com/lightwebinc/ingress-infra)                 | Ingress (deploy)        | Ansible/Terraform automation for `shard-proxy` nodes                                |
| [shard-listener](https://github.com/lightwebinc/shard-listener)   | Egress                  | Multicast subscriber; filters by shard/subtree, forwards to unicast and multicast consumers |
| [listener-infra](https://github.com/lightwebinc/listener-infra)               | Egress (deploy)         | Ansible/Terraform automation for `shard-listener` nodes                             |
| [retry-endpoint](https://github.com/lightwebinc/retry-endpoint)   | Retransmission          | Caches frames, retransmits on NACK requests; beacon-advertised                              |
| [retransmission-infra](https://github.com/lightwebinc/retransmission-infra)   | Retransmission (deploy) | Ansible/Terraform automation for `retry-endpoint` nodes                             |
| [shard-manifest](https://github.com/lightwebinc/shard-manifest)   | Manifest                | BRC-137 announcer; periodic `shard_bits` + joined-groups beacon                             |
| [manifest-infra](https://github.com/lightwebinc/manifest-infra)   | Manifest (deploy)       | Ansible/Terraform automation for `shard-manifest` nodes                                     |
| [shard-common](https://github.com/lightwebinc/shard-common)       | Shared library          | Protocol primitives: `frame`, `shard`, `seqhash`, `sequence`, `txidset`                     |
| [subtx-generator](https://github.com/lightwebinc/subtx-generator) | Testing                 | Traffic generator for load/functional testing; BRC-127/131/132/134 senders                  |
| [multicast-test](https://github.com/lightwebinc/multicast-test)   | Testing                 | Integration test harness: Go + Docker scenarios (`harness/`) and legacy LXD VM lab (`vm-lab/`) |
| [multicast-kube-infra](https://github.com/lightwebinc/multicast-kube-infra) | Kubernetes (deploy)     | k0s-reference + Helm composition of the full stack; distribution-agnostic                   |
| [shard-proxy-helm](https://github.com/lightwebinc/shard-proxy-helm)             | Helm chart              | Chart for `shard-proxy`                                                                     |
| [shard-listener-helm](https://github.com/lightwebinc/shard-listener-helm)       | Helm chart              | Chart for `shard-listener`                                                                  |
| [retry-endpoint-helm](https://github.com/lightwebinc/retry-endpoint-helm)       | Helm chart              | Chart for `retry-endpoint`                                                                  |
| [subtx-generator-helm](https://github.com/lightwebinc/subtx-generator-helm)     | Helm chart              | Chart for `subtx-generator`                                                                 |
| [shard-manifest-helm](https://github.com/lightwebinc/shard-manifest-helm)       | Helm chart              | Chart for `shard-manifest`                                                                  |
| [bsv-multicast](https://github.com/lightwebinc/bsv-multicast)             | Documentation           | This repository; project overview, design, and BRC specifications                           |

## Documentation

- **[DESIGN.md](DESIGN.md)** — Comprehensive design overview: architecture, data
  flow, sharding, frame format, components, retransmission, subtree filtering,
  deployment
- [BRC-124 Frame Format](docs/brc-124-frame-format.md) — 92-byte wire format
  with HashKey/SeqNum per-flow sequencing
- [BRC-126 Retransmission Protocol](docs/brc-126-retransmission-protocol.md) —
  NACK/ACK/MISS, ADVERT beacon, tier/preference model
- [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md) —
  Dynamic subtree-to-group binding protocol
- [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md) — BRC-30 EF
  payloads within BRC-124 frames
- [BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md)
  — IPv6 address scheme, control-plane indices
- [BRC-130 Fragmentation](docs/brc-130-fragmentation.md) — Large-transaction
  fragmentation; per-fragment NACK; listener reassembly
- [BRC-131 Block Announcement Frame Format](docs/brc-131-block-announcements.md)
  — BlockAnnounce/CoinbaseTx frame types; control-group routing
- [BRC-132 Subtree Data Frame Format](docs/brc-132-subtree-data.md) — Subtree
  data distribution with Merkle roots
- [BRC-133 Coinbase Transaction Frame Format](docs/brc-133-coinbase-delivery.md)
  — Coinbase transaction wire format on the control channel
- [BRC-134 Anchor Transaction Frame Format](docs/brc-134-anchor-transactions.md)
  — Chained anchor transaction distribution
- [BRC-135 Multicast Block Header Format](docs/brc-135-block-header-format.md) —
  Standalone 80-byte block header split; emitter-originated
- [BRC-137 Shard Manifest Announcement](docs/brc-137-shard-manifest.md) —
  Periodic participant configuration announcement (shard_bits + joined groups)
- [NACK Retransmission Flow](docs/nack-retransmission-flow.md) — End-to-end
  pipeline diagrams

## License

See [LICENSE](LICENSE).
