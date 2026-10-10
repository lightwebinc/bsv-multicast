# Documentation Index

## Repositories

| Repository                                                                        | Role                    | Purpose                                                                                     |
| --------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------- |
| [shard-proxy](https://github.com/lightwebinc/shard-proxy)         | Ingress                 | Stateless ingress proxy; receives frames, derives multicast group, forwards verbatim        |
| [ingress-infra](https://github.com/lightwebinc/ingress-infra)                 | Ingress (deploy)        | Ansible/Terraform automation for `shard-proxy` nodes                                |
| [shard-listener](https://github.com/lightwebinc/shard-listener)   | Egress                  | Multicast subscriber; filters by shard/subtree, forwards to unicast and multicast consumers |
| [listener-infra](https://github.com/lightwebinc/listener-infra)               | Egress (deploy)         | Ansible/Terraform automation for `shard-listener` nodes                             |
| [retry-endpoint](https://github.com/lightwebinc/retry-endpoint)   | Retransmission          | Caches frames, retransmits on NACK requests; beacon-advertised                              |
| [retransmission-infra](https://github.com/lightwebinc/retransmission-infra)   | Retransmission (deploy) | Ansible/Terraform automation for `retry-endpoint` nodes                             |
| [shard-manifest](https://github.com/lightwebinc/shard-manifest)   | Manifest                | BRC-139 announcer; periodic `shard_bits` + joined-groups beacon                             |
| [manifest-infra](https://github.com/lightwebinc/manifest-infra)   | Manifest (deploy)       | Ansible/Terraform automation for `shard-manifest` nodes                                     |
| [teranode-bridge](https://github.com/lightwebinc/teranode-bridge) | Integration             | Landing-tier bridge: terminates BRC-30, BRC-143 and BRC-144 push lanes in front of an unmodified Teranode cluster, and publishes what that cluster produces back |
| [arcade-bridge](https://github.com/lightwebinc/arcade-bridge) | Integration             | Landing-tier bridge for the broadcaster tier: feeds an unmodified Arcade v2 + merkle-service stack from BRC-143/144 delivery lanes, and forwards its transaction broadcasts into the fabric |
| [overlay-bridge](https://github.com/lightwebinc/overlay-bridge) | Integration | Landing-tier bridge: runs an unmodified overlay services engine fed by the BRC-149 BEEF delivery lane and the BRC-135 header lane, and publishes accepted submissions back to the BEEF plane |
| [overlay-blueprints](https://github.com/lightwebinc/overlay-blueprints) | Integration (apps) | Reference overlay services host on the BEEF object plane, fed by overlay-bridge; applications load as host-side modules (experimental) |
| [shard-common](https://github.com/lightwebinc/shard-common)       | Shared library          | Protocol primitives: `frame`, `shard`, `bundle`, `seqhash`, `sequence`, `txidset`, `cache`, `pow`, `manifest`, `netjoin`, … |
| [subtx-generator](https://github.com/lightwebinc/subtx-generator) | Testing                 | Traffic generator for load/functional testing; BRC-127/131/132/134 + BRC-143/144 push senders |
| [beef-generator](https://github.com/lightwebinc/beef-generator) | Testing                 | BRC-148/149 BEEF object-plane traffic generator (`beef-gen`)                                |
| [multicast-test](https://github.com/lightwebinc/multicast-test)   | Testing                 | Integration test suite: Go + Docker scenarios (`harness/`) on an isolated IPv6 bridge       |
| [multicast-kube-infra](https://github.com/lightwebinc/multicast-kube-infra) | Kubernetes (deploy)     | k0s-reference + Helm composition of the full stack; distribution-agnostic                   |
| [charts/shard-listener](https://github.com/lightwebinc/charts/tree/main/charts/shard-listener)       | Helm chart              | Chart for `shard-listener`                                                                  |
| [charts/retry-endpoint](https://github.com/lightwebinc/charts/tree/main/charts/retry-endpoint)       | Helm chart              | Chart for `retry-endpoint`                                                                  |
| [charts/subtx-generator](https://github.com/lightwebinc/charts/tree/main/charts/subtx-generator)     | Helm chart              | Chart for `subtx-generator`                                                                 |
| [charts/shard-manifest](https://github.com/lightwebinc/charts/tree/main/charts/shard-manifest)       | Helm chart              | Chart for `shard-manifest`                                                                  |
| [charts/teranode-bridge](https://github.com/lightwebinc/charts/tree/main/charts/teranode-bridge)     | Helm chart              | Chart for `teranode-bridge`                                                                 |
| [charts/arcade-bridge](https://github.com/lightwebinc/charts/tree/main/charts/arcade-bridge)         | Helm chart              | Chart for `arcade-bridge`                                                                   |
| [bsv-multicast](https://github.com/lightwebinc/bsv-multicast)             | Documentation           | This repository; project overview, design, and BRC specifications                           |

## Documents

- **[DESIGN.md](../DESIGN.md)** — Comprehensive design overview: architecture, data
  flow, sharding, frame format, components, retransmission, subtree filtering,
  deployment
- [BRC-124 Frame Format](brc-124-frame-format.md) — 92-byte wire format
  with HashKey/SeqNum per-flow sequencing
- [BRC-126 Retransmission Protocol](brc-126-retransmission-protocol.md) —
  NACK/ACK/MISS, ADVERT beacon, tier/preference model
- [BRC-127 Subtree Group Announcement](brc-127-subtree-announce.md) —
  Dynamic subtree-to-group binding protocol
- [BRC-128 Extended Format](brc-128-ef-frame-format.md) — BRC-30 EF
  payloads within BRC-124 frames
- [BRC-129 Multicast Group Address Assignments](brc-129-multicast-addressing.md)
  — IPv6 address scheme, control-plane indices
- [BRC-130 Fragmentation](brc-130-fragmentation.md) — Large-transaction
  fragmentation; per-fragment NACK; listener reassembly
- [BRC-131 Block Announcement Frame Format](brc-131-block-announcements.md)
  — BlockAnnounce (payload = BRC-144 block object, verbatim) and CoinbaseTx
  frame types; control-group routing
  (CoinbaseTx is deprecated and retained; see BRC-133)
- [BRC-132 Subtree Data Frame Format](brc-132-subtree-data.md) — Subtree
  data distribution with Merkle roots
- [BRC-133 Coinbase Transaction Frame Format](brc-133-coinbase-delivery.md)
  — Coinbase transaction wire format on the control channel (**deprecated**,
  retained deliberately: not produced by current implementations, since the
  coinbase travels inline in the BRC-144 block body; dropped by the listener's
  default-on block-control gate; kept reserved so a future design could carry
  blocks and their coinbase separately on the fabric and recombine them at the
  edges)
- [BRC-134 Anchor Transaction Frame Format](brc-134-anchor-transactions.md)
  — Chained anchor transaction distribution
- [BRC-135 Multicast Block Header Format](brc-135-block-header-format.md) —
  Standalone 80-byte block header split (`payload[0:80]` of a BRC-131
  announce); datagram form and bare-header stream form
- [BRC-139 Shard Manifest Announcement](brc-139-shard-manifest.md) —
  Periodic participant configuration announcement (shard_bits + joined groups)
- [BRC-142 Coalescing (Bundle) Frame](brc-142-coalescing-frame.md) —
  Packs many small txs of one (group, subtree) into one ≤MTU datagram; inverse
  of BRC-130
- **Push frame formats** — whole-object delivery/ingest to a consumer reached by
  push (round-robin SDA over a tunnel) rather than announce/pull. Only subtree
  and block need new formats; a coinbase off the fabric is a plain **BRC-12**
  transaction (delivered inline inside the block frame — Teranode rejects a loose
  coinbase) and an anchor is an ordinary **BRC-30 EF** transaction, so neither
  gets its own format:
  - [BRC-143 Subtree Data Frame Format](brc-143-subtree-data.md) — in-band
    merkle root + `uint64` node count + ordered node hashes; coinbase placeholder
    `0xFF×32` detected by value
  - [BRC-144 Block Frame Format](brc-144-block-frame.md) — strict parity with
    Teranode's block body: header + counts + ordered subtree roots + full inline
    coinbase + height + BRC-74 coinbase BUMP
  - Reference consumers of both formats:
    [teranode-bridge](https://github.com/lightwebinc/teranode-bridge) (miner
    tier, both directions) and
    [arcade-bridge](https://github.com/lightwebinc/arcade-bridge) (application
    tier, delivery side)
- [BRC-148 Shard Domain Partitioning and BEEF Object Plane](brc-148-shard-domain-beef-plane.md)
  — Partitions the 16-bit shard-index space into object planes by high nibble;
  allocates the BEEF plane (domain `0x1`, sharded by overlay topic) with
  per-domain BRC-139 manifest coordination
- [BRC-149 Multicast BEEF Object Frame Format](brc-149-beef-object-frame.md)
  — Companion to BRC-148: assigns FrameVer `0x09` and fixes the object frame
  plus the submission/delivery record grammars (open-port `0xBEEF` tag
  detection)
  - Reference consumers of BRC-148/149 and the BRC-135 stream form:
    [overlay-bridge](https://github.com/lightwebinc/overlay-bridge) (BRC-149
    delivery records on 9171, bare BRC-135 headers on 9172) and
    [beef-generator](https://github.com/lightwebinc/beef-generator) (test
    traffic)
- [Lane Numbering](lane-numbering.md) — the port names the side of the fabric
  a lane sits on; fabric-side 872x, consumer-side one decade per bridge
- [Deliver Once per Landing Site](deliver-once.md) — one delivery slot per
  site; local fan-out, never a second tunnel crossing
- **Operational modes** —
  [Source-Specific Multicast (SSM)](../DESIGN.md#source-specific-multicast-ssm)
  (deployment default) and
  [Automatic Shard Configuration](../DESIGN.md#automatic-shard-configuration)
  (opt-in)
- [Diagrams](../diagrams/README.md) — component and flow diagrams (Mermaid)
- [1BSV User Guide](https://1bsv.net/guide.html) — using the commercial
  delivery network built on this stack (hosted service; sending is free,
  receiving is a subscription)

