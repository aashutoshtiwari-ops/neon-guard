# Demo: Neon IP allow list and TTFT on Cloud Run

You already have a GCP project with billing and a Neon Scale database. This document is the full path: **build the network, deploy the app, turn on Neon’s IP allow list, then run the four cards.**

The app streams a chat model and can run a Postgres allow-list `SELECT` in different ways. **TTFT** is time until the first token (SSE `meta`). Model time still jitters; for the allow-list story, trust **connect_ms** vs **query_ms**, and **DB ping** when you do not want the model in the number.

---

## What you will show

Four implementation choices. Same `SELECT id FROM allowlist WHERE id = 'demo'`.

| Card | Code path | What TTFT includes |
| --- | --- | --- |
| **1. Don’t wait** | Start the model stream. Allow-list runs in the background. | Model only. `connect_ms` = 0. |
| **2. New connect via NAT** | `await connect()` + `SELECT`, *then* stream. New TCP every time. | Model + **NAT + TLS** (`connect_ms`). Query is cheap. |
| **3. New Cloud Run instance** | Drop any socket, then the same as card 2. Real version: scale to zero first. | Card 2 again, plus **container start** if the service was at 0 instances. |
| **4. Egress IP not listed** | Talk only. No Run. | **~10s** `connect_timeout` or error if traffic is not the NAT IP on Neon’s list. |

**One sentence for the room:** *IP allow list on Cloud Run means a static NAT. A new Postgres connection on the first-token path adds that handshake to TTFT. A new instance pays it again. A wrong egress IP looks like a 10 second TTFT.*

---

## Part A — Setup

Use one region for Cloud Run, the VPC connector, and Cloud NAT (example: `asia-south1`). Prefer a region close to the Neon endpoint.

Replace:

- `PROJECT_ID`
- `REGION` (e.g. `asia-south1`)
- `AI_GATEWAY_API_KEY` (Vercel AI Gateway)

### A1. CLI and APIs

```bash
gcloud config set project PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  compute.googleapis.com \
  vpcaccess.googleapis.com
```

### A2. Network: NAT with a static IP

Cloud Run has no stable public IP. Neon IP allow list needs one IP you control. That IP is **Cloud NAT**.

```bash
# Reserve the address you will paste into Neon
gcloud compute addresses create neon-nat-ip \
  --region=REGION

gcloud compute addresses describe neon-nat-ip \
  --region=REGION \
  --format='get(address)'
```

Save that address (e.g. `34.x.x.x`).

```bash
gcloud compute routers create neon-router \
  --network=default \
  --region=REGION

gcloud compute routers nats create neon-nat \
  --router=neon-router \
  --region=REGION \
  --nat-all-subnet-ip-ranges \
  --nat-external-ip-pool=neon-nat-ip
```

If `default` VPC is missing, create a VPC and subnet in `REGION` first and pass `--network` / subnet flags accordingly.

### A3. Serverless VPC Access connector

```bash
gcloud compute networks vpc-access connectors create neon-connector \
  --region=REGION \
  --network=default \
  --range=10.8.0.0/28 \
  --min-instances=2 \
  --max-instances=3 \
  --machine-type=e2-micro
```

Wait until the connector is **READY**. If `10.8.0.0/28` clashes, pick another unused `/28`.

### A4. Neon: connection string and IP allow list

In the Neon console, open the Scale project.

1. **Dashboard → Connection details**  
   Copy the URI (`postgresql://...`). Direct or pooled both work for this demo. You need a role/password that can `CREATE TABLE` (first request creates `allowlist` and `request_logs`).

2. **Settings → IP Allow** (wording may be “Allowed IPs” / “IP allow list”)  
   - Enable it.  
   - Add **only** the NAT address from A2.  
   - Do not add `0.0.0.0/0`.  
   - Remove any old VM IPs if you want this demo to prove Cloud Run’s path.

Until Cloud Run egress uses that NAT (A6), connections from your laptop will fail. That is expected.

### A5. Build and deploy the service

From the repo root:

```bash
gcloud artifacts repositories create neon-guard \
  --repository-format=docker \
  --location=REGION \
  --description="neon-guard demo"

gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/neon-guard/app:latest
```

If Artifact Registry push is denied, grant Cloud Build the Artifact Registry Writer role on that repo.

```bash
gcloud run deploy neon-guard \
  --image=REGION-docker.pkg.dev/PROJECT_ID/neon-guard/app:latest \
  --region=REGION \
  --allow-unauthenticated \
  --port=8080 \
  --cpu=1 \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=3 \
  --timeout=300 \
  --set-env-vars="AI_GATEWAY_API_KEY=YOUR_KEY,DATABASE_URL=YOUR_NEON_URI,MODEL=openai/gpt-5.6-sol"
```

`--allow-unauthenticated` is for a live demo URL. Use IAM auth if you need it locked down.

Prefer Secret Manager for the URI and API key in anything beyond a talk:

```bash
# after creating secrets neon-db-url and neon-ai-key
gcloud run services update neon-guard --region=REGION \
  --set-secrets="DATABASE_URL=neon-db-url:latest,AI_GATEWAY_API_KEY=neon-ai-key:latest"
```

### A6. Send Cloud Run traffic through the NAT

Postgres to Neon is a **public** hostname. If egress is “private ranges only”, that traffic **skips** NAT and uses rotating Cloud Run IPs. Neon will then reject it.

```bash
gcloud run services update neon-guard \
  --region=REGION \
  --vpc-connector=neon-connector \
  --vpc-egress=all-traffic
```

`all-traffic` = every outbound packet (including Neon) goes VPC → Cloud NAT → internet.

Confirm:

```bash
gcloud run services describe neon-guard --region=REGION \
  --format='yaml(spec.template.metadata.annotations)'
```

You should see the connector and egress all-traffic.

### A7. Smoke check

```bash
URL=$(gcloud run services describe neon-guard --region=REGION --format='value(status.url)')
curl -s "$URL/health"
```

Expect `"platform": "cloudrun"` and `"database": "neon"`.

Open `$URL` in the browser. Pills: **DB: neon**, **Key: set**, **Cloud Run**.

On card 2, click **DB ping (no model)**. You want `ok` with **connect_ms** and **query_ms**. If it hangs ~10s or errors, NAT IP is not on Neon’s list, or egress is not `all-traffic`.

---

## Part B — Metrics on the UI

| Field | Meaning |
| --- | --- |
| **TTFT ms** | Server: request start → first SSE `meta`. |
| **client TTFT ms** | Browser: `fetch` → first `meta`. |
| **connect_ms** | New `psycopg.connect()` (TCP + TLS + auth via NAT). **This is the allow-list path cost.** |
| **query_ms** | Time in the `SELECT` after the socket is up. |

**Refresh logs** reloads the last 20 rows. It does **not** delete data.

---

## Part C — Live demo (top to bottom)

Do not click Warm/ping on other cards before you need them. Card 1 first.

### Card 1 — Don’t wait

**Implementation:** `create_task(allowlist)` then stream. Do not `await` Postgres before the first token.

**Do:**

1. Open **1. Don’t wait**.
2. **Run**. Wait for the reply.
3. **Run** again. Write the second **TTFT** as the model band (it will still move by hundreds of ms).

**See:** `connect_ms` = 0, `query_ms` = 0 on the strip. Tokens as fast as the model.

**Say:** *The allow-list still runs. TTFT does not wait for NAT or TLS.*

---

### Card 2 — New connect via NAT

**Implementation:** `await connect()` + `SELECT`, then open the model. New connection every Run/ping.

**Do:**

1. Open **2. New connect via NAT**.
2. **DB ping (no model)** two or three times. Write **connect_ms** and **query_ms**.
3. **Run**. Point at **connect_ms** on the same row as TTFT.

**See:** `connect_ms` ≫ `query_ms`. Ping extra vs card 1 is that connect, not the SQL. TTFT ≈ model band + `connect_ms` (model jitter still applies — if TTFT is noisy, stay on ping).

**Say:** *Same SELECT. We put a new connection on the first-token path. The delay is the handshake through Cloud NAT, which exists so Neon’s IP list has a stable address.*

---

### Card 3 — New Cloud Run instance

**Implementation:** Drop any in-process socket, then the same as card 2. Cloud Run does this whenever it starts a **new container** (scale from zero, extra instance, new revision).

**Do (stand-in, no waiting):**

1. Open **3. New Cloud Run instance**.
2. **Run** or **DB ping**. Expect **connect_ms** like card 2.

**Do (real zero instances):**

1. Leave **min-instances = 0**.
2. Stop hitting the service until Cloud Run shows **0 instances** (often 10–15+ minutes).
3. One **DB ping** or **Run**. First hit = container start + NAT connect. Second hit on the same instance: no container start; ping still opens a **new** TCP so **connect_ms** stays.

**See:** Same DB shape as card 2. Real idle adds a one-time jump (Cloud Run start) on the first request.

**Say:** *Every new instance pays a full NAT+TLS connect. We do not keep a VM-sized process around.*

---

### Card 4 — Egress IP not listed

**Implementation:** `connect_timeout=10`. If the source IP is not on Neon’s list, the wait is on the critical path whenever you `await connect()` before tokens.

**Do:** Talk. Do not detach the connector or delete the NAT IP during the talk.

**When it happens:** IP allow list on, but Cloud Run not using `all-traffic` + this NAT; or Neon still lists the wrong IP.

**See (only if you reproduce later):** ping/Run error or ~10s. No useful `query_ms`.

**Say:** *Wrong egress IP is not a slow query. It is a closed door, and the timeout becomes TTFT.*

---

## Spine (about five minutes)

1. Health: Cloud Run + neon.  
2. Card 1 twice — TTFT = model; connect 0.  
3. Card 2 ping — connect_ms vs query_ms.  
4. Card 2 Run — TTFT includes connect.  
5. Card 3 — new instance = pay connect again; optional real scale-to-zero.  
6. Card 4 — 10s timeout if the NAT IP is not what Neon allows.

**Close:** *Don’t await a new connection before the first token. If you must check Postgres, reuse a socket on a warm instance. Keep min-instances > 0 if first-hit NAT+cold-start is unacceptable. List the NAT IP and force all egress through it.*

---

## If something fails

| Symptom | Fix |
| --- | --- |
| Health `platform` is `vm` | You are not on the Cloud Run URL. |
| Ping hangs ~10s / connection refused | NAT IP not on Neon; or `--vpc-egress` is not `all-traffic`. |
| `database: null` | `DATABASE_URL` missing on the revision. |
| Gateway error on Run | `AI_GATEWAY_API_KEY` or `MODEL`. |
| Connector create fails | `/28` in use; pick another range. |
| Card 1 TTFT jumps 900–1600 ms | Model noise. Use ping for the allow-list story. |
