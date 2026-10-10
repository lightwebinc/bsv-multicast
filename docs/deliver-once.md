# Deliver Once per Landing Site

An object must cross the consumer tunnel (or direct connect) into a landing
site **exactly once**, no matter how many local consumers use it. The tunnel is
the scarce resource; anything local to the landing machine (loopback or LAN)
is effectively free. This document is the canonical statement of that
invariant for every bridge.

## One slot per site

A site provisions **one** delivery slot and elects every lane it needs on it.
Each bridge terminates its own lanes on its own decade of ports (see
[lane-numbering.md](lane-numbering.md)), so a broadcaster stack and an overlay
host on one site take disjoint lanes on the same slot and nothing is pulled
twice.

The single-consumer case (an Arcade-only, Teranode-only or overlay-only site)
is one slot, one tunnel, one delivery: the sole bridge binds its lanes on the
canonical ports and there is nothing to deduplicate.

## Two bridges, same lane: fan out locally

Lanes are single-class and a lane's port is claimed by whichever bridge binds
it, so two bridges on one host cannot both terminate the same lane. That
collision is deliberate: two bridges binding two lane sets means two consumer
slots, and the fabric then delivers (and bills) each object across the tunnel
twice. The canonical `9143`/`9144` subtree/block ports make that wrong path
fail fast instead of shipping quietly.

When one site genuinely needs the same lane in two bridges (for example
Teranode and Arcade co-located), provision one consumer whose delivery address
set lists the site's tunnel exits, configured to fail over rather than spread.
The edge sends the whole stream to the first healthy tunnel inner, decided
from TCP evidence, so each object crosses once. Replication happens at the
far end, where it is free, in a small stateless tier-neutral tee:

```text
  edge / fabric ══ one crossing per object ══▶ tee   (landing host)
                                  wildcard :9143 / :9144
       ├─ loopback copy ─▶ teranode-bridge [::1]:9153/9154
       └─ loopback copy ─▶ arcade-bridge   [::1]:9163/9164
```

The tee is design, built when the first site needs it. Load-bearing details:

- **Deep-copy is mandatory.** `objfmt.Reader.Next` aliases its internal
  buffer; a queue that retained the slice would serve corrupt bytes.
- **Per-destination isolation.** Bounded per-destination queues: one slow or
  dead downstream must never block the tunnel lane or a healthy peer, and must
  never drop the tunnel connection (that would masquerade as tunnel failure
  and trip failover).
- **Loopback ports must differ, not just addresses.** A wildcard `[::]:9143`
  also claims `::1:9143` and, with `bindv6only=0`, `127.0.0.1:9143`. Bridges
  behind the tee therefore use distinct loopback ports. Divergent lane ports
  exist only on loopback, only in this opt-in configuration.
- **Neutral module.** Byte plumbing over a public wire format, reusing
  `shard-common/objfmt.Reader` and the public lane package. No entitlements,
  no reverse path, not a mode of any bridge, and no bridge in another's
  availability path.

## A grammar owns its connection

The fabric ingress decides a connection's grammar from its first four bytes,
once, for the life of the connection. A bridge that publishes more than one
grammar (for example BRC-149 submission records and BRC-30 transactions) uses
one socket per grammar; interleaving corrupts one grammar rather than erroring.

## Rejected alternatives

- **Control-plane co-delivery group.** A point-to-point tunnel cannot
  replicate, so a landing-side fan is required regardless; a group buys
  nothing and puts control-plane membership back in the runtime failover path.
- **Edge-side fan-out.** Fans before the tunnel (two crossings), on framed
  rather than bare objects, synchronously. Wrong side, wrong layer.
- **One bridge sourcing objects from another.** Couples one tier's delivery to
  another bridge at runtime and bakes a foreign client into a public binary.
- **One bridge with every announce sink.** Bridges also own facades and
  up-tunnel submitters that cannot merge without breaking the open-core split.
