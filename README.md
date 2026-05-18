# Bitcoin Multicast

A high-throughput, horizontally-scalable transaction distribution system for Bitcoin SV (BSV) designed to pave the road towards 1 billion+ transactions per second. It uses IPv6 multicast to efficiently distribute transaction data across a fabric of subscribers (miners, exchanges, service providers) with deterministic sharding and NACK-based reliability.

**Conceptual Attribution:** The IPv6 multicast transaction broadcast architecture from which this software draws inspiration was articulated by Dr. Craig S. Wright in [Multicast Within Multicast: Anycast, Sharded Resends, and Hierarchical Distribution for Transaction and Block Propagation](https://singulargrit.substack.com/p/multicast-within-multicast-anycast).

## Repositories

| Repository | Purpose |
| ---------- | ------- |
| [bitcoin-shard-proxy](https://github.com/lightwebinc/bitcoin-shard-proxy) | Stateless ingress proxy; derives multicast group from TxID, forwards verbatim |
| [bitcoin-shard-listener](https://github.com/lightwebinc/bitcoin-shard-listener) | Multicast subscriber; shard/subtree filtering, unicast/multicast egress, NACK gap recovery |
| [bitcoin-retry-endpoint](https://github.com/lightwebinc/bitcoin-retry-endpoint) | Caches frames, retransmits on NACK; beacon discovery |
| [bitcoin-shard-common](https://github.com/lightwebinc/bitcoin-shard-common) | Shared protocol primitives: `frame`, `shard`, `seqhash`, `sequence` |
| [bitcoin-subtx-generator](https://github.com/lightwebinc/bitcoin-subtx-generator) | Traffic generator for load and functional testing |
| [bitcoin-multicast-test](https://github.com/lightwebinc/bitcoin-multicast-test) | Integration test harness; LXD lab, scenario suite, deploy scripts |
| [bitcoin-ingress](https://github.com/lightwebinc/bitcoin-ingress) | Ansible/Terraform for proxy deployment |
| [bitcoin-listener](https://github.com/lightwebinc/bitcoin-listener) | Ansible/Terraform for listener deployment |
| [bitcoin-retransmission](https://github.com/lightwebinc/bitcoin-retransmission) | Ansible/Terraform for retry endpoint deployment |

## Documentation

- **[DESIGN.md](DESIGN.md)** — Comprehensive design overview: architecture, data flow, sharding, frame format, components, retransmission, subtree filtering, deployment
- [BRC-124 Frame Format](docs/brc-124-frame-format.md) — 92-byte wire format with HashKey/SeqNum per-flow sequencing
- [BRC-126 Retransmission Protocol](docs/brc-126-retransmission-protocol.md) — NACK/ACK/MISS, ADVERT beacon, tier/preference model
- [BRC-127 Subtree Group Announcement](docs/brc-127-subtree-announce.md) — Dynamic subtree-to-group binding protocol
- [BRC-128 Extended Format](docs/brc-128-ef-frame-format.md) — BRC-30 EF payloads within BRC-124 frames
- [BRC-129 Multicast Group Address Assignments](docs/brc-129-multicast-addressing.md) — IPv6 address scheme, control-plane indices
- [BRC-130 Fragmentation](docs/brc-130-fragmentation.md) — Large-transaction fragmentation; per-fragment NACK; listener reassembly
- [BRC-131 Block Announcement Protocol](docs/brc-131-block-announcements.md) — BlockAnnounce/CoinbaseTx frame types; control-group routing
- [NACK Retransmission Flow](docs/nack-retransmission-flow.md) — End-to-end pipeline diagrams

## License

See [LICENSE](LICENSE).
