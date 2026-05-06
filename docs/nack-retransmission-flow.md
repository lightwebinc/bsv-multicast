# NACK Retransmission Flow

System-level design document describing the end-to-end NACK retransmission pipeline across all components. This is a cross-repo document covering `bitcoin-shard-proxy`, `bitcoin-shard-listener`, `bitcoin-retry-endpoint`, and the multicast fabric.

---

## 1. Full Pipeline

```text
                          multicast fabric (FF05::<shard>)
                         ┌──────────────────────────────────────────────────┐
                         │                                                  │
BSV Source ──► bitcoin-shard-proxy ──┬──► listener-1 ──► downstream consumer
              (stamps PrevSeq/CurSeq)│
                                     ├──► listener-2 ──► downstream consumer
                                     │
                                     └──► bitcoin-retry-endpoint (caches all frames)
```

- **Proxy** receives transactions, stamps `PrevSeq`/`CurSeq` (XXH64 hash chain per sender+group), derives shard group from TxID, multicasts to `FF05::<shard>`.
- **Listeners** subscribe to shard groups, decode frames, track per-group PrevSeq/CurSeq chain breaks, forward to consumers.
- **Retry endpoint** subscribes to all shard groups, caches raw frames indexed by `CurSeq` (primary) and `PrevSeq` (secondary).

---

## 2. Gap Detection & NACK Dispatch

```text
Listener receives:  Seq 1, 2, 3, [gap], 6, 7, ...
                              │
                              ▼
              Gap detected: prevSeq=X, curSeq=Z but expected prevSeq=lastCurSeq
              Key: CurSeq of the missing frame (= incoming PrevSeq)
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
            Hold-off jitter      Register gap entry
            (suppression)        in Tracker.pending
                    │
                    ▼
              NACK dispatched (24 bytes, unicast UDP)
              to retry endpoint from registry snapshot
                    │
                    ▼
         ┌──────────────────────────┐
         │  Ephemeral UDP socket    │
         │  conn = DialUDP(endpoint)│
         │  Write NACK              │
         │  Read response (300ms)   │
         └──────────┬───────────────┘
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
        ACK       MISS     Timeout
          │         │         │
    cancel gap   advance   backoff
                endpoint   & retry
```

The NACK carries a `LookupType` (by `PrevSeq` or by `CurSeq`) and the corresponding `LookupSeq` XXH64 value identifying the missing frame. The listener opens a per-request ephemeral UDP socket (`[::]:0`), sends the NACK, and waits up to 300 ms for a single response.

---

## 3. Tier Model

Tiers represent proximity to the transaction source. Lower tier = closer to source.

| Tier | Meaning          | Example                                        |
| ---- | ---------------- | ---------------------------------------------- |
| 0    | Same AS as proxy | Data-center-local retry endpoint               |
| 1    | One AS hop       | Regional PoP                                   |
| 2    | Two AS hops      | Remote continent                               |
| 0xFF | Static seed      | Bootstrap `-retry-endpoints` (lowest priority) |

```text
[BSV Source]
     │
     ▼
[Proxy] ──multicast──► [Tier-0 Retry Endpoint]
                              │
                         NACK forwarding (Phase 2)
                              │
                              ▼
                        [Tier-1 Retry Endpoint]  (HasParent)
                              │
                              ▼
                        [Tier-2 Retry Endpoint]  (HasParent)
```

Listeners try Tier 0 first. On MISS, they advance to Tier 1, then Tier 2, etc.

---

## 4. Preference Within a Tier

Multiple endpoints at the same tier are sorted by **Preference DESC**. Higher Preference = tried first.

```text
Tier 0:
  Endpoint A  (Preference 200)  ◄── tried first
  Endpoint B  (Preference 128)
  Endpoint C  (Preference  50)  ◄── tried last within Tier 0

Tier 1:
  Endpoint D  (Preference 180)  ◄── tried after all Tier 0 exhausted
  Endpoint E  (Preference  90)
```

**Use cases:**

- Direct traffic to higher-capacity nodes (higher Preference).
- Prefer endpoints on better-connected paths.
- Graceful migration: set old endpoint to Preference 0 before draining.

---

## 5. Escalation State Machine

```text
  ┌──────────┐
  │ PENDING  │  gap detected; hold-off jitter applied
  └────┬─────┘
       │ timer fires
       ▼
  ┌────────────────┐
  │ NACKED(Tier-K) │  NACK sent to endpoint at current (tier, preference)
  └───┬────┬───┬───┘
      │    │   │
      │    │   └─── Timeout ──► exponential backoff; retry next sweep
      │    │
      │    └─── MISS ──► advance to next endpoint at same tier (by Preference);
      │                  if tier exhausted, advance to Tier K+1;
      │                  retry IMMEDIATELY (no backoff)
      │
      └─── ACK ──► gap entry cancelled; done
                   (ACK.Flags indicates multicast_sent / unicast_sent)

  Any state ──► FILLED  (multicast repair arrived independently)
                gap entry cancelled; in-flight socket times out harmlessly
```

---

## 6. Beacon Discovery

```text
bitcoin-retry-endpoint                    bitcoin-shard-listener
┌─────────────────────┐                  ┌─────────────────────┐
│ beacon.Sender       │                  │ discovery.Beacon    │
│                     │  ADVERT (56B)    │   Listener          │
│ every 60s ──────────┼──── multicast ──►│                     │
│ to FF05::FF:FFFD    │  (site-scoped)   │ DecodeADVERT()      │
│ to FF0E::FF:FFFD    │  (global-scoped) │       │             │
│ (per -beacon-scope) │                  │       ▼             │
└─────────────────────┘                  │ registry.Upsert()   │
                                         │  TTL = 3 × interval │
                                         │       │             │
                                         │       ▼             │
                                         │ Snapshot() ─► NACK  │
                                         │ (sorted Tier ASC,   │
                                         │  Preference DESC)   │
                                         └─────────────────────┘

Fallback: `-retry-endpoints host1:9300,host2:9300`
  → Seed() at Tier=0xFF, Preference=0 (tried last)
```

---

## 7. Inter-AS Extension

```text
AS 100 (Source)                    AS 200 (Remote)
┌──────────────────┐              ┌───────────────────┐
│ Proxy            │              │                   │
│   │              │              │                   │
│   ▼              │  MP-BGP      │                   │
│ Tier-0 Endpoint  │◄─multicast──►│ Tier-1 Endpoint   │
│ beacons on       │  MVPN/MSDP   │ (HasParent)       │
│ FF0E::FF:FFFD    │              │                   │
│                  │              │ Listeners         │
└──────────────────┘              │ join both beacon  │
                                  │ groups; discover  │
                                  │ Tier-0 via global │
                                  │ beacon            │
                                  └───────────────────┘

NACK escalation path:
  Listener (AS 200) ──NACK──► Tier-1 (AS 200)
                                  │ cache miss
                                  ▼
                              Tier-0 (AS 100)  ──► retransmit to multicast
                                                   MP-BGP delivers to AS 200
```

No protocol changes required. Network team extends multicast fabric via MP-BGP.

---

## 8. Flood Prevention

| Mechanism                     | Layer                    | Effect                                                          |
| ----------------------------- | ------------------------ | --------------------------------------------------------------- |
| Redis `SET NX` (60 s)         | Retry endpoint           | Only one endpoint retransmits per frame per site                |
| `SequenceIDRetransmit` marker | Retry endpoint → ingress | Retransmit frames dropped from recaching                        |
| `Tracker.Fill()`              | Listener                 | Multicast repair cancels pending NACKs for all listeners        |
| Jitter hold-off               | Listener                 | Randomised delay before first NACK suppresses duplicates        |
| Exponential backoff           | Listener                 | Reduces NACK rate on persistent gaps                            |
| `MaxRetries` + `GapTTL`       | Listener                 | Gap entries evicted after retry exhaustion or absolute deadline |
