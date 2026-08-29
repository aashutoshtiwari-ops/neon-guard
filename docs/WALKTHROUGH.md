# Cloud Run + Neon IP allow list — demo script

This demo is **only** TTFT problems that appear when the app runs on **Cloud Run** and Neon **IP Allow List** is on. It is not the VM “await vs gather vs sync driver” talk.

Run cards **top to bottom**. Quote **connect_ms** vs **query_ms**. Model TTFT still jitters (~1s ± a few hundred ms); do not compare raw TTFT across minutes without looking at connect_ms.

The health pill should say **Cloud Run** (`K_SERVICE` is set). If it says **VM**, you are not on the path this script is about.

---

## Why Cloud Run is different from the GCP VM

On the VM you listed a **stable public IP**. Neon accepts that IP. Traffic is **VM → Neon**. No extra box.

Cloud Run tasks do **not** have a stable public IP. Egress IPs rotate. You cannot put “the Cloud Run IP” on Neon’s list the way you did for the VM.

To keep IP Allow List, you typically:

1. Attach a **Serverless VPC Access connector** (or Direct VPC egress).
2. Send outbound traffic through **Cloud NAT** with a **reserved static IP**.
3. Put **that NAT IP** on Neon’s allow list.

Every **new** Postgres connection is then:

**Cloud Run instance → VPC connector → Cloud NAT → internet → Neon**

That extra hop (plus TLS) is what cards 2–3 measure. Card 4 is what happens if a request does **not** leave through that NAT IP.

Not in this demo (same on VM or not caused by the IP list): gather-then-yield, sync event-loop stalls, “SQL is slow,” Neon compute suspend (unless you hit it by accident).

---

## What must be true in GCP / Neon

1. Cloud Run service, image from this repo’s `Dockerfile` (listens on `$PORT`).
2. VPC connector (or Direct VPC) on the service.
3. Cloud NAT on the same network/region, static IP reserved.
4. Neon Scale **IP Allow List** contains **only** that NAT IP (not the old VM IP unless you still use the VM).
5. `DATABASE_URL` on the Cloud Run service.
6. For card 3’s real scale-to-zero: **min instances = 0** (and be willing to wait for idle).

If IP Allow List is off, card 2 still times a connect, but you are no longer proving the NAT requirement.

---

## Metrics

| Field | Meaning |
| --- | --- |
| **TTFT ms** | Handler start → first SSE `meta` (includes model if you clicked Run). |
| **client TTFT ms** | Browser `fetch` → first `meta`. |
| **connect_ms** | New `psycopg.connect()`: TCP + TLS + auth **through NAT** (when configured). This is the IP-allow-list tax. |
| **query_ms** | The `SELECT` after the socket is up. Should stay small. |

**DB ping (no model)** is the clean measurement. Use it when model jitter hides a 50–200 ms connect.

---

## Card 1 — Don’t wait (control)

### What you are proving

The IP allow list / NAT path cannot delay the first token if the handler **does not wait** on a new Neon connection before streaming.

### Steps

1. Confirm the pill says **Cloud Run**.
2. Click **1. Don’t wait**.
3. **Run** twice. Keep the second **TTFT** as the model band (it will still move).
4. **connect_ms** and **query_ms** on the strip should be **0**.

### See

TTFT ≈ model only. Background ping may still open a NAT connection; it must not sit in front of the first token.

### Say

*“IP allow list is on. We still don’t make the user wait for NAT+TLS before tokens.”*

---

## Card 2 — New connect via NAT

### What you are proving

The usual Cloud Run + IP allow list design: **static NAT**, and a **new Postgres connection** on the allow-list / auth path before the model. The bottleneck is **connect_ms** (NAT + TLS), not the `SELECT`.

On the VM with a listed public IP, this handshake had no NAT gateway. Here it does.

### Steps

1. Click **2. New connect via NAT**.
2. Click **DB ping (no model)** two or three times. Write **connect_ms** and **query_ms**.
3. Click **Run**. Compare **TTFT** to card 1’s band. The extra should be about **connect_ms** (model noise still applies). Point at **connect_ms ≫ query_ms** on the same row.

### See

| | Typical |
| --- | --- |
| query_ms | small (single-digit to tens of ms) |
| connect_ms | larger (often tens to hundreds of ms; more if NAT/region is far from Neon) |

If ping **fails** or hangs ~10s, you are already on card 4 (egress IP not listed).

### Say

*“We didn’t make the query expensive. We opened a new connection through Cloud NAT because that’s how Cloud Run satisfies Neon’s IP list.”*

---

## Card 3 — New Cloud Run instance

### What you are proving

A VM process can keep a socket for days. Cloud Run **throws the process away** (scale to zero, new instance for load, deploy). The next request has **no pool**. First checkout is another full NAT+TLS connect (card 2), sometimes plus **container start** (not the IP list, but it stacks).

The in-app **Run** drops any in-process socket and connects again — the **database** part of a new instance. It does **not** simulate Cloud Run’s own cold start.

### Steps (in-app stand-in)

1. Click **3. New Cloud Run instance**.
2. **Run** (or ping). Expect **connect_ms** in the same ballpark as card 2.

### Steps (real scale-to-zero)

1. Cloud Run: min instances **0**.
2. Stop traffic until the revision shows 0 instances (often many minutes).
3. One **DB ping** or **Run** on card 3. First hit = container start + NAT connect. Second hit on the **same** instance should drop container-start cost; connect_ms stays if you still open a new TCP each time (this app’s ping always does).

### See

Same shape as card 2 for the DB handshake. Real idle adds a large jump once (Cloud Run), then card-2-like connects.

### Say

*“The allow list forced NAT. Cloud Run makes us pay that handshake every time we get a new instance, not once per VM lifetime.”*

---

## Card 4 — Egress IP not listed (talk only)

### What you are proving

IP allow list is a **door**. Cloud Run’s **default internet egress IPs are not the NAT IP**. If the service is not actually sending Postgres through that NAT, Neon refuses the connection. This app waits up to **10s** (`connect_timeout`). If that wait is before the first token, TTFT is ~10s or the request errors.

Do **not** turn off the connector mid-demo unless you want a hard fail.

### When it happens

- IP allow list on, Cloud Run **without** VPC/NAT (rotating Google egress IPs).
- Connector attached but **VPC egress** still “public” for the Neon destination so traffic skips NAT.
- Neon list still has the **VM IP** and not the **NAT IP**.
- NAT IP changed and the list was not updated.

### See (if you ever reproduce)

Ping/Run errors or ~10s delay. **query_ms** never appears. Not a slow `SELECT`.

### Say

*“On the VM, listing the VM IP was enough. On Cloud Run, listing the wrong IP looks like a TTFT disaster.”*

---

## Suggested spine (about 5 minutes)

1. Pill: Cloud Run. Card 1 twice — “NAT is not on TTFT.”
2. Card 2 ping — “connect_ms is the allow-list path; query_ms is noise.”
3. Card 2 Run — TTFT includes that connect.
4. Card 3 — new instance = pay connect again; real zero-instance optional.
5. Card 4 — talk: default Cloud Run IPs vs NAT IP.

Close: *“If you need Neon IP allow list on Cloud Run, you take NAT. Don’t put a new connect on the first-token path (card 1). Don’t skip NAT and hope rotating IPs match the list (card 4).”*

---

## If numbers look wrong

- Pill says **VM** — you are not measuring Cloud Run NAT.
- **connect_ms** tiny and you expected NAT — traffic may be going **direct** (allow list off, or not via NAT). Compare to ping from the old VM.
- Card 1 **TTFT** jumps around — model jitter; ignore for this story except as a band.
- Ping hangs 10s — card 4; fix NAT IP on Neon’s list.
- Refresh logs does **not** clear rows; it only reloads the last 20.
