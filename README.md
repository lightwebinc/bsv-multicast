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

## Documentation

- [DESIGN.md](DESIGN.md) — architecture, data flow, sharding, components,
  retransmission, deployment
- [docs/README.md](docs/README.md) — repository map and full document index
  (BRC specifications, lane numbering, deliver-once)
- [1BSV User Guide](https://1bsv.net/guide.html) — the commercial delivery
  network built on this stack

## License

See [LICENSE](LICENSE).
