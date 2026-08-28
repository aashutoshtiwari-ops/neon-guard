# Allow-list vs TTFT — live demo script

This is the talk track. The app is small on purpose: **the same Postgres allow-list `SELECT`**, written four different ways, and what that does to **time to first token (TTFT)**.

Run the four cards **top to bottom**. Write down numbers as you go; later cards only make sense against card 1.

---

## What this demo is (and is not)

**Is:** How *application code* that gates a chat request on an allow-list can delay the first model token — or delay *someone else’s* first token.

**Is not:** Neon Scale IP Allow List as extra latency. If this GCP VM’s public IP is on Neon’s list, connections from the VM are simply **allowed**. That is a pass/fail door, not a hop. You need it so `DATABASE_URL` works. You are not measuring it.

**Is not:** Cold compute, connection-pooler vs direct URL, NAT in front of serverless, or “the SQL is slow.” Each card uses the same tiny `SELECT id FROM allowlist WHERE id = 'demo'`.

The story: *“We added an allow-list in Neon. If we put that check on the path to the first token — or we call it with a blocking driver on the event loop — TTFT gets worse. Here are the exact code shapes.”*

---

## Before the room sits down

1. App is running with **one uvicorn worker** (default). Pair demo (card 4) is wrong with multiple workers.
2. Open the UI (`/`). Pills should show **DB: neon** and **Key: set**.
3. Leave the prompt as the default one-sentence question unless you need a shorter stream.
4. Have a notepad or the “Recent requests” table for TTFT numbers. After each successful run, **Refresh logs** if you want a paper trail; it is optional and talks to Neon (fine after you no longer care about a cold start).

If DB is `off`, allow-list calls no-op (`allowlist_ms` stays 0) and cards 2–4 will not move. Fix `DATABASE_URL` first.

---

## How to read the metrics

| Field | Meaning |
| --- | --- |
| **TTFT ms** | Server clock: request handler starts → first SSE `meta` event. For a single chat, this is the number to quote. |
| **client TTFT ms** | Browser clock: `fetch` starts → first `meta`. Use this on **card 4**. The victim’s wait includes time the server loop was frozen *before* the victim handler could finish producing the first token, and also queueing in the browser/network. |
| **allowlist_ms** | Time spent in the allow-list function *on this request’s critical path*. Card 1 reports **0** here even though a background check still runs — that is the point. Cards 2–3 report the pooled `SELECT`. Card 4’s blocker reports the sync call (including `pg_sleep(1)`). |

Model TTFT itself jitters (load, region, gateway). Always compare **the same session**, card 1 vs 2 vs 3, not a number from yesterday.

---

## Card 1 — Don’t wait (control)

### What you are proving

The allow-list does **not** have to sit in front of the first token. Start the model stream immediately; run `SELECT` in the background (`asyncio.create_task` + `to_thread`). Logging after the stream also uses `to_thread`, so it does not freeze the loop.

This is the **only** number the rest of the demo is allowed to beat.

### What the code does

```text
create_task(allowlist())          # do not await
async for token in model.stream():
    yield token                   # first yield is TTFT
```

### Steps

1. Click **1. Don’t wait**.
2. Click **Run**. Wait until tokens appear and the metrics row fills in.
3. Write down **TTFT ms** and **client TTFT ms**. Ignore this first run if it looks like an outlier (cold model / first HTTP to the gateway).
4. Click **Run** again.
5. Write down the **second** TTFT. That is **Baseline B**. Say it out loud: “This is model time only.”

### What you should see

- **allowlist_ms = 0** on the metric strip (not on the critical path).
- Tokens start as fast as the model/gateway allow.
- A reply still streams; the check happened, it just did not block TTFT.
- Status in logs: `good` (or `ok` with scenario `good`).

### If it looks wrong

- No stream / gateway error: API key or model, not Neon.
- `allowlist_ms` not 0: you are on the wrong card.

**Talking point:** *“The product still enforced allow-list. We did not make TTFT wait for Postgres.”*

---

## Card 2 — Await, then stream

### What you are proving

The classic bug: **same query**, wrong place on the timeline. The handler `await`s the allow-list, *then* opens the model. Every millisecond of that `SELECT` (and pool checkout) is added to TTFT.

This is not “Neon is slow SQL.” It is **order**.

### What the code does

```text
allowlist_ms = await allowlist()  # must finish
async for token in model.stream():
    yield token
```

The allow-list still runs in a worker thread (`to_thread`), so this card does **not** freeze other requests. It only delays **this** chat’s first token.

### Steps

1. Click **2. Await, then stream**.
2. Click **Run** once (twice if the first run looks noisy).
3. Put **TTFT ms** next to Baseline B.
4. Look at **allowlist_ms** on the same row.

### What you should see

- **TTFT ≈ Baseline B + allowlist_ms** (plus a little jitter).
- **allowlist_ms** is typically tens of milliseconds on a warm pool from this VM, sometimes more. Even a small add is the mechanism; you do not need a huge number.
- Logs scenario: `serial`.

### What to say

*“We didn’t change the query. We waited for it before we were allowed to start the model. TTFT is now model plus allow-list.”*

### If it looks wrong

- TTFT ≈ card 1 and **allowlist_ms = 0**: `DATABASE_URL` missing.
- TTFT much larger than allowlist_ms extra: model jitter. Run card 1 again, then card 2 again, same minute.

---

## Card 3 — Gather, then yield

### What you are proving

A common “fix” that is not a fix: start allow-list and the model HTTP connect **at the same time**, but **do not yield any token until both have finished**.

```text
allowlist, stream = await gather(allowlist(), open_model())
async for token in stream:
    yield token   # first yield is after gather returns
```

Parallel work. **TTFT still waits for the slower of the two.** If the allow-list is slower than “open the stream,” this looks as bad as card 2. If the allow-list is faster, it may look close to card 1 — which is why you already showed card 2, and why you say the sentence below.

True overlap (card 1) is: **yield as soon as the model has a token**, even if allow-list is still running (and abort later if denied).

### Steps

1. Click **3. Gather, then yield**.
2. Click **Run**.
3. Compare **TTFT** to Baseline B and to card 2.
4. Note **allowlist_ms** (time for the check that ran inside `gather`).

### What you should see

- TTFT is **at least** as large as the slower of {allow-list, time to first model byte after connect}.
- It should **not** be better than card 1 in a systematic way.
- If Neon is fast from this VM, gather may look **near** card 1. That is honest: the bug is the **join before first yield**, not “gather always adds 200ms.” Say: *“We still coupled first token to both finishing. We got lucky if Postgres was faster than the model.”*
- Logs scenario: `gather`.

### What to say

*“asyncio.gather does not mean the user sees a token sooner. It means we wait for max(db, model-connect) before we are allowed to stream.”*

---

## Card 4 — Sync allow-list (pair)

### What you are proving

This is **not** “await allow-list then stream” (that was card 2). This is: a **blocking** allow-list (`psycopg.connect` + `SELECT` on the asyncio thread) so **another** request cannot run.

Card 2 delays **this** user. Card 4 delays **the other** user, even when that other user wrote card-1-correct code.

`pg_sleep(1)` is only so the stall is obvious in a noisy room. The bug is **sync I/O on the loop**; a slow TLS handshake would do the same.

### Why one click cannot be a single request

One HTTP handler cannot be both the stall and the victim. **Run pair**:

1. **Victim** starts first: same path as card 1 (stream, don’t wait on allow-list).
2. After ~80ms, **blocker** sends a `meta` immediately, then runs the **sync** allow-list on the loop (no model).

While the blocker is inside `connect` / `SELECT` / `pg_sleep(1)`, the loop cannot resume the victim’s `await` on the model stream, so the victim’s first token is late.

### Steps

1. You already have Baseline B from card 1. If it has been a while, run card 1 once more and update B.
2. Click **4. Sync allow-list (pair)**.
3. Click **Run pair** once. Do not click Run twice overlapping; let the pair finish.
4. Read **two** metric blocks: Victim, then Blocker.

### What you should see

| | TTFT ms | client TTFT ms | allowlist_ms |
| --- | --- | --- | --- |
| **Blocker** | tiny (it yields `meta` before the stall) | small | ~1000+ (sleep + connect) |
| **Victim** | ~1s above Baseline B if the stall overlapped waiting for the first token | **this is the slide number** | 0 on the critical path |

- Victim still never `await`ed the allow-list. Their inflation is **someone else’s sync call**.
- Logs: `sync:victim` and `sync:blocker` (blocker status `sync_stall`).

### What to say

*“Logging or allow-list with a sync driver after you think you’re done still shares the event loop. The next chat’s TTFT includes your `connect()`.”*

### If it looks wrong

- Victim **not** ~1s slower: **more than one uvicorn worker**, or the pair did not overlap (clicked wrong). Confirm one worker; use only **Run pair**.
- Both fail: Neon/IP allow list or network; blocker needs a real connect unless `DATABASE_URL` is unset (then it still `sleep(1)`).
- You compared server TTFT only and it looks mild: quote **victim client TTFT** vs card 1 **client TTFT**.

---

## Suggested 5-minute spine

1. Card 1 twice → “Baseline B, allow-list off the path.”
2. Card 2 → “Same SELECT, we waited. TTFT += allowlist_ms.”
3. Card 3 → “Gather still joins before the first token.”
4. Card 4 → “Sync check on the loop taxes the *other* request. One worker, Run pair, look at victim client TTFT.”

Close: *“Correct shape is card 1: start the stream; don’t await the allow-list before the first token; don’t put blocking Postgres on the event loop.”*

---

## After the demo (optional)

Refresh logs and walk the scenario column: `good`, `serial`, `gather`, `sync:victim`, `sync:blocker`. Same table, four code paths.
