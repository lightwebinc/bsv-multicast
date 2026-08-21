# Diagrams

Component and flow diagrams for the multicast pipeline, maintained as Mermaid
sources (rendered natively by GitHub). [DESIGN.md](../DESIGN.md) and the
[`docs/`](../docs/) BRC specifications are authoritative; these diagrams are
orientation aids.

Addressing is shown as SSM (`FF3E::B:<shard>`, the deployment default);
ASM (`FF05::`/`FF0E::`) remains supported — see DESIGN.md § Source-Specific
Multicast.

## shard-proxy (ingress)

Stateless ingress: parse, gate, shard, stamp, emit. One frame in, one (or k
fragments, or one bundle) out — no per-flow state beyond monotonic SeqNum
counters.

```mermaid
flowchart LR
    subgraph senders["Senders"]
        UDPTX["UDP 8725<br/>one tx per datagram<br/>(framed or bare)"]
        TCPTX["TCP 8725 framed lane<br/>grammar-detect: magic-led frame /<br/>0xBEEF record / bare tx"]
        PUSH["Privileged push lanes (default off)<br/>TCP 8726 BRC-143 subtree<br/>TCP 8727 BRC-144 block<br/>TCP 8728 BRC-149 BEEF"]
    end

    subgraph proxy["shard-proxy"]
        PARSE["Parse + validate<br/>BRC-124 / BRC-128 / bare tx<br/>(require-ef: EF-native ingress)"]
        GATE["Gates<br/>block-announce PoW (require-block-pow)<br/>privileged-class socket rejection"]
        SHARD["Shard derivation<br/>TxID bits → group index<br/>(shard_bits, BRC-129)"]
        STAMP["Stamp HashKey + SeqNum<br/>XXH64 per-flow identity,<br/>monotonic counter"]
        FRAG["BRC-130 fragmentation<br/>payload > MTU−140 → k fragments<br/>sized to smallest egress-path MTU"]
        COAL["BRC-142 coalescing (opt-in, origin only)<br/>small txs of one group+subtree<br/>→ one bundle frame"]
    end

    subgraph fabric["IPv6 multicast fabric (UDP 9001)"]
        SHARDS["FF3E::B:&lt;shard&gt;<br/>sharded tx groups"]
        CTRL["FF3E::B:FFFE block control<br/>(announce, anchor)"]
        HDR["FF3E::B:FFFA header lane<br/>(BRC-135, bare 80 B)"]
    end

    UDPTX --> PARSE
    TCPTX --> PARSE
    PUSH -->|"reframed: 143→132, 144→131"| GATE
    PARSE --> GATE
    GATE --> SHARD
    SHARD --> STAMP
    STAMP --> FRAG
    STAMP --> COAL
    FRAG --> SHARDS
    COAL --> SHARDS
    STAMP --> SHARDS
    GATE --> CTRL
    GATE --> HDR
```

## shard-listener (egress)

Role modes: `-mode collapsed` (default, everything below), `receiver`
(multicast half only), `delivery` (consumer half only: unicast ingest +
fan-out, no join/gap/NACK).

```mermaid
flowchart LR
    subgraph fabric["IPv6 multicast fabric"]
        GRP["FF3E::B:&lt;shard&gt; +<br/>control groups"]
    end

    subgraph listener["shard-listener"]
        JOIN["MLD join (SSM source-filtered)<br/>SO_REUSEPORT workers"]
        FILT["Filters<br/>shard filter (defense-in-depth)<br/>subtree include/exclude"]
        BGATE["Block-control gate (require-block-pow)<br/>re-validates announce PoW<br/>drops legacy standalone coinbase"]
        REASM["BRC-130 reassembly<br/>slot per TxID, SHA256d verify"]
        DECO["BRC-142 decoalesce<br/>bundle → member frames"]
        GAP["Gap tracking<br/>per HashKey/SeqNum flow"]
        DEDUP["TxID dedup<br/>(optional, shared backend)"]
    end

    subgraph recovery["Recovery"]
        RETRY["retry-endpoint<br/>NACK UDP 9300"]
    end

    subgraph consumers["Consumers"]
        TCPOUT["TCP egress<br/>(optional strip-header;<br/>UDP legacy)"]
        MCOUT["Multicast egress<br/>(domain bridging)"]
    end

    GRP --> JOIN
    JOIN --> FILT
    FILT --> REASM
    FILT --> DECO
    REASM --> GAP
    DECO --> GAP
    FILT --> GAP
    JOIN -->|"V4 block frames (bypass filters)"| BGATE
    BGATE --> GAP
    GAP -->|"gap detected: NACK"| RETRY
    RETRY -->|"re-multicast into group"| GRP
    GAP --> DEDUP
    DEDUP --> TCPOUT
    DEDUP --> MCOUT
```

## NACK retransmission (BRC-126)

Wire detail, tier escalation, and cross-domain proxying:
[BRC-126](../docs/brc-126-retransmission-protocol.md).

```mermaid
sequenceDiagram
    participant P as shard-proxy
    participant F as multicast fabric
    participant L as shard-listener
    participant R as retry-endpoint tier 0
    participant U as upstream retry-endpoint

    R--)F: ADVERT beacon (FF3E::B:FFFD)<br/>groups, tier, preference, HasParent
    P->>F: frame (HashKey, SeqNum=n)
    F->>L: deliver
    F->>R: deliver (cache, per-class TTL)
    P--)F: frame (SeqNum=n+2) — n+1 lost
    F->>L: deliver — gap detected
    L->>R: NACK (unicast, 64 B): HashKey, missing range
    alt cached
        R->>F: re-multicast missing frame(s) into original group
        R->>L: ACK (16 B)
    else not cached
        R->>L: MISS (16 B)
        opt proxying enabled (one hop max)
        R->>U: NACK with Proxied flag
        U->>R: missing frame(s) (unicast)
        R->>F: re-cache + re-multicast into own domain
        end
        L->>R: escalate to next tier on MISS/timeout
    else overloaded
        R->>L: THROTTLED (16 B) — back off
    end
```
