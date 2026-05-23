# Bitcoin Multicast

A high-throughput, horizontally-scalable transaction distribution system for
Bitcoin SV (BSV) designed to pave the road towards 1 billion+ transactions per
second. It uses IPv6 multicast to efficiently distribute transaction data across
a fabric of subscribers (miners, exchanges, service providers) with
deterministic sharding and NACK-based reliability.

**Conceptual Attribution:** The IPv6 multicast transaction broadcast
architecture from which this software draws inspiration was articulated by Dr.
Craig S. Wright in
[Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast).

## Position

The reference architecture treats IPv6 multicast as the only medium that can
carry Bitcoin SV at billion-transactions-per-second scale: anycast-load-balanced
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
| [bitcoin-shard-proxy](https://github.com/lightwebinc/bitcoin-shard-proxy)         | Ingress                 | Stateless ingress proxy; receives frames, derives multicast group, forwards verbatim        |
| [bitcoin-ingress](https://github.com/lightwebinc/bitcoin-ingress)                 | Ingress (deploy)        | Ansible/Terraform automation for `bitcoin-shard-proxy` nodes                                |
| [bitcoin-shard-listener](https://github.com/lightwebinc/bitcoin-shard-listener)   | Egress                  | Multicast subscriber; filters by shard/subtree, forwards to unicast and multicast consumers |
| [bitcoin-listener](https://github.com/lightwebinc/bitcoin-listener)               | Egress (deploy)         | Ansible/Terraform automation for `bitcoin-shard-listener` nodes                             |
| [bitcoin-retry-endpoint](https://github.com/lightwebinc/bitcoin-retry-endpoint)   | Retransmission          | Caches frames, retransmits on NACK requests; beacon-advertised                              |
| [bitcoin-retransmission](https://github.com/lightwebinc/bitcoin-retransmission)   | Retransmission (deploy) | Ansible/Terraform automation for `bitcoin-retry-endpoint` nodes                             |
| [bitcoin-shard-common](https://github.com/lightwebinc/bitcoin-shard-common)       | Shared library          | Protocol primitives: `frame`, `shard`, `seqhash`, `sequence`                                |
| [bitcoin-subtx-generator](https://github.com/lightwebinc/bitcoin-subtx-generator) | Testing                 | Traffic generator for load/functional testing; BRC-127/131/132 senders                      |
| [bitcoin-multicast-test](https://github.com/lightwebinc/bitcoin-multicast-test)   | Testing                 | Integration test harness; scenario suite, LXD lab setup, deploy                             |
| [bitcoin-multicast](https://github.com/lightwebinc/bitcoin-multicast)             | Documentation           | This repository; project overview, design, and BRC specifications                           |

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
- [NACK Retransmission Flow](docs/nack-retransmission-flow.md) — End-to-end
  pipeline diagrams

## License

See [LICENSE](LICENSE).
