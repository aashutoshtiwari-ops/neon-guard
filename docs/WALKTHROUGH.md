# TTFT demo walkthrough

Live script for the Neon allow-list cards. Run the cards **top to bottom**. Card 1 only works if nothing has talked to Neon yet in this session.

## What you are proving

An allow-list (or any Postgres check) does not have to sit in front of the first model token. When it does — or when it shares the event loop, the connection pool, or a longer network path — **time to first token (TTFT)** goes up. Each card isolates one mechanism so you are not mixing “we queried before the model” with “we froze the loop” with “compute was asleep.”

**TTFT** here is wall time until the first SSE `meta` event (server) and until the browser sees that event (**client TTFT**). Prefer client TTFT when two requests overlap.

## Before you start

1. `uvicorn app.main:app --host 0.0.0.0 --port 43127` with **one worker** (default). Loop-block and pool-hold are single-process.
2. Open `/`. Confirm the pills: DB `neon`, gateway key set.
3. Leave **Refresh logs** alone until after card 1. Logs query Neon and will wake compute.
4. Do not click **Warm up DB** or **DB ping** until the walkthrough says so.
5. Neon console: compute can go Idle (Scale still autosuspends unless you pin it). Short suspend makes card 1 practical.

If you already warmed the database, skip to card 2 and come back to card 1 after compute shows Idle again.

---

### 1. Cold compute

**Trying to do:** Show that the first allow-list hit after suspend includes **compute wake**, not just SQL. That delay is on the path to the first token.

**Why first:** Warm up, pings, other Runs, and log refresh all start compute. You get one cold sample per idle period.

**Do:** Confirm Idle in Neon. Stay on this card. Click **Run** (or **DB ping (fresh)** to skip the model). Then **Run** again immediately.

**See:**

| | connect ms | query ms | TTFT |
|---|---|---|---|
| First hit | large (hundreds of ms to seconds) | small | inflated by connect |
| Second hit | much smaller | still small | closer to a warm connect |

If both hits look warm, compute was never Idle (page load does not hit Neon; something else did).

---

### 2. New TLS connection

**Trying to do:** With compute **warm**, show that `psycopg.connect()` per check is the cost. The allow-list `SELECT` is not.

**Do:** **DB ping (fresh)** then **DB ping (pooled)**. Then **Run** (fresh TLS before the stream).

**See:** Fresh ping: **connect ms ≫ query ms**. Pooled ping: **connect ms = 0**. The Run’s TTFT includes fresh **connect ms**. Same query as later cards; different checkout.

---

### 3. Stream first (control)

**Trying to do:** Give every later card a comparison number. Tokens start without waiting on Neon. Allow-list/logging run off the loop.

**Do:** **Warm up DB**. **Run** twice. Keep the second TTFT / client TTFT.

**See:** **connect ms**, **query ms**, **pool wait ms** on the critical path are **0**. TTFT tracks the model. This is the “done correctly” path.

---

### 4. Allow-list, then stream

**Trying to do:** Classic ordering bug — wait for a **warm pooled** lookup, *then* open the model. Isolates order; not TLS, not cold start.

**Do:** Warm if needed. **Run**. Compare to card 3.

**See:** TTFT ≈ baseline + **query ms**. **connect ms = 0**, **round trips = 1**. Same as the old `?block_db=1` flag.

---

### 5. Parallel, then yield

**Trying to do:** `gather(allow-list, open model)` still withholds the first token until **both** finish. Parallelism ≠ overlapping TTFT.

**Do:** Warm. **Run**. Compare to cards 3 and 4.

**See:** TTFT is **max(db, model connect)**, not model-only. Slow Neon ≈ card 4. Fast Neon may look near card 3 — that is why card 6 exists.

---

### 6. Four round trips

**Trying to do:** User → org → role → flag as four sequential SELECTs on a warm connection. Chatty checks add RTT, not CPU.

**Do:** Warm. **Run**. Compare **query ms** and **round trips** to card 4.

**See:** **round trips = 4**. **query ms** roughly 4× serial. TTFT up by that extra query time. One indexed lookup (or a cache) would flatten this.

---

### 7. Sync DB freezes the loop

**Trying to do:** Show a **sibling** request’s TTFT, not “this request logged first.” Sync Postgres on asyncio blocks **everyone** on that worker. Logging after a stream can still stall the next chat.

**Why a pair:** One request cannot be both the stall and the victim. **Run pair** starts the victim (stream first), then the blocker (sync connect + `pg_sleep(1)`, no model).

**See:** Blocker **TTFT** is tiny. Victim **client TTFT** is about **1s+** above card 3, and that request never ran the allow-list itself. This is not the same bug as card 4.

---

### 8. Hold the pool slot

**Trying to do:** Pool size 1. Checkout, then **keep the slot while the model streams**. The other chat waits **before** it may open the model. Distinct from card 7: the loop still runs; only the slot is busy.

**Do:** **Run pair** (both start at once). Streams are capped (`max_tokens` 48).

**See:** One request: **pool wait ms ≈ 0**, TTFT near baseline. The other: **pool wait ms** on the order of the first stream’s duration, TTFT inflated by that wait.

---

### 9. IP allow list / NAT hop

**Trying to do:** Explain the Scale IP allow-list path. Serverless egress IPs rotate, so people front Neon with a **static NAT or proxy**. That extra hop is on every new connection. A wrong listed IP fails until **connect_timeout** (10s here).

**Do:** No **Run**. Compare direct Neon vs the same query through NAT/proxy, or mislist an IP.

**See:** Success: small constant on **connect ms**. Failure: multi-second TTFT or errors — not a slow `SELECT`.

---

## Metrics on the card

| Field | Meaning |
|---|---|
| TTFT ms | Server clock: start of handler → first SSE meta |
| client TTFT ms | Browser clock: `fetch` → first meta (use this for pairs) |
| connect ms | New TLS/auth to Neon (0 if pooled socket already open) |
| query ms | Time in `SELECT`s after the socket is up |
| pool wait ms | Time waiting for the one-slot hold demo |
| round trips | How many allow-list `SELECT`s ran |

## If numbers look wrong

- **Cold never looks cold** — something already hit Neon; idle compute and retry card 1 only.
- **Serial ≈ connect card** — pool was not warm; Warm up, then serial again.
- **Loop victim not slower** — more than one uvicorn worker, or the stall missed the overlap; one worker and Run pair only.
- **Hold both wait ≈ 0** — they did not overlap; use Run pair, not two manual Sends.
- **No DB metrics** — `DATABASE_URL` missing; pills will not say `neon`.
