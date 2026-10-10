# Lane Numbering

A lane is a single-class TCP byte stream: one object class, one grammar, one
port. **A lane's port number names the side of the fabric the lane sits on**,
so the same object class carries two numbers depending on direction. The
payload is the same bare object on both sides; only the side differs.

## Fabric side: the 872x block

These are the edge proxy's object ingress. The proxy only ever emanates onto
the fabric, so its lanes carry the fabric's numbers whatever transport a
submit arrives over (direct, tunnel, or a bridge's reverse path).

| Port | Class | Grammar | Proxy flag |
| --- | --- | --- | --- |
| 8725 | transactions | BRC-12 / BRC-30 (framed BRC-124/128 also accepted) | `-udp-listen-port`, `-tcp-listen-port` |
| 8726 | subtrees | BRC-143 push frame (privileged) | `-subtree-listen-port` |
| 8727 | blocks | BRC-144 push frame (privileged) | `-block-listen-port` |
| 8728 | BEEF | BRC-149 submission record (open class) | `-beef-listen-port` |

The open port `8725` also admits BRC-149 submission records, detected by their
`0xBEEF` tag; `8728` is an optional dedicated lane for flow separation.

`8725` is the transaction class number and is reused in the delivery
direction for a consumer's transaction lane, because a transaction is the same
bare object either way.

## Consumer side: one decade per bridge

These exist only on the consumer. The consumer listens and the edge dials.
Nothing in the fabric listens on them. Each bridge takes one decade, so lanes,
retrieval planes, facades and metrics of co-hosted bridges never collide, and
every number stays clear of a stock Teranode cluster's own service ports
(notably `8833`, its propagation HTTP port).

| Decade | Owner | Lanes (edge dials) | Other listeners |
| --- | --- | --- | --- |
| 914x | teranode-bridge | 9143 subtrees (BRC-143), 9144 blocks (BRC-144); tx on 8725 | 9145 retrieval, 9146 metrics |
| 916x | arcade-bridge | 9143 subtrees, 9144 blocks (canonical, shared with teranode-bridge) | 9165 retrieval, 9166 facade, 9167 metrics |
| 917x | overlay-bridge | 9171 BEEF delivery records (BRC-149), 9172 bare headers (BRC-135 stream form) | 9175 facade, 9178 header API, 9179 metrics |

The subtree and block lanes are canonical: every consumer of those classes
binds `9143`/`9144`. That is deliberate. Two bridges on one host cannot both
bind the same lane, so the wasteful topology (two delivery slots pulling the
same bytes across one tunnel) fails fast. See [deliver-once.md](deliver-once.md).

## Rules

1. A submit targets the fabric number (872x), never a consumer number. A
   bridge's reverse path that submits to `9143`/`9144` reaches a port nothing
   in the fabric listens on.
2. A delivery lane is a consumer number (9xxx decade, or 8725 for
   transactions). The edge dials it.
3. A lane carries one class and one grammar for the life of the connection;
   the ingress decides the grammar from the first four bytes. Never interleave
   grammars on one socket.
4. Divergent lane ports are allowed only on loopback behind a local tee (see
   [deliver-once.md](deliver-once.md)), never tunnel-facing and never as a
   default.
