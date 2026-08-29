# Demo: Neon IP allow list and TTFT on Cloud Run

You already have a GCP project with billing and a Neon Scale database. This document is the full path: **build the network, deploy the app, turn on Neon’s IP allow list, then run the four cards.**

The app streams a chat model and can talk to Postgres in different ways. **TTFT** is time until the first token. Each **Run 10×** averages ten requests so one noisy model call does not set the story. For cards 2–3, also look at **connect_ms** vs **query_ms**.

---

## What you will show

| Card | What it shows |
| --- | --- |
| **1. Fastest first token** | Chat starts with no wait on the database. TTFT ≈ the model. |
| **2. Wait for DB, then chat** | Wait for allow-list, then chat. **Reuses** a saved connection. A bit slower than 1. |
| **3. Brand-new container** | No saved connection. **New** connect every time, then chat. Slower than 2. |
| **4. Chat starts, then DB fails** | Chat begins; a DB connect never gets through (IP not allowed). Stream stops. TTFT vs time until cutoff. |

**One sentence:** *Don’t wait on a new DB connection if you want a fast first token. A new container pays that connection again. If the IP is not allowed, you can still start chatting — then the stream is cut when the DB call fails.*

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
| **TTFT ms** | Average time to first token (10 requests). |
| **client TTFT ms** | Same, measured in the browser. |
| **connect_ms** | Average time to open a new database connection. |
| **query_ms** | Average time for the allow-list `SELECT` after the socket is up. |
| **stopped ms** | Card 4 only: average time until the chat is cut off. |

**Refresh logs** reloads the last 20 rows. It does **not** delete data.

---

## Part C — Live demo (top to bottom)

Cards show only **what** they demonstrate. Clicks below. Each **Run 10×** takes a while (ten model calls; card 4 also waits ~5s per call for the failed connect).

### Card 1 — Fastest first token

**Implementation:** Start the model stream at once. Database check runs in the background and is not awaited.

**Do:** Open the card. **Run 10×**. Wait for “Average of 10 requests”.

**See:** Average TTFT is the model band. **connect_ms** and **query_ms** are 0.

**Say:** *This is the fastest first token. We did not wait for the database.*

---

### Card 2 — Wait for DB, then chat

**Implementation:** `await` the allow-list `SELECT`, then start the model. Uses **one saved connection** in this process. **Run 10×** warms that connection first so the average is not dominated by a single handshake.

**Do:** **Run 10×**. Compare average TTFT to card 1. **connect_ms** should be ~0; **query_ms** small.

**See:** TTFT a bit above card 1 (wait for the query). Much less than a full NAT+TLS connect.

**Say:** *We waited for the database, but we reused the connection. Extra TTFT is the SELECT, not a new handshake.*

---

### Card 3 — Brand-new container

**Implementation:** Close the saved connection, then **new** `connect()` + `SELECT` on **every** request, then the model. Same as a new Cloud Run instance with an empty pool.

**Do:** **Run 10×**. Compare to cards 1 and 2.

**See:** **connect_ms** large. Average TTFT **higher than card 2**, which is higher than card 1.

**Say:** *No saved connection. Every request dials Neon again. That is 3 > 2 > 1.*

---

### Card 4 — Chat starts, then DB fails

**Implementation:** Start the model immediately (like card 1). In parallel, try to reach Neon in a way that **never completes** (same timing as an IP that is not on the allow list: wait until `connect_timeout`, here 5s). When that fails, **stop the stream** and show the error.

The default prompt counts at length so you see tokens **before** the cutoff.

**Do:** Open the card. **Run 10×**. Watch one run: numbers appear, then the error. Then read the averages.

**See:** **TTFT** is still fast (chat started). **stopped ms** is about 5 seconds (connect timeout). The last sample shows text plus the error.

**Say:** *We started chatting without waiting. The database never got in (IP not allowed). The stream stopped. Fast first token does not mean the request finished.*

---

## Spine

1. Health: Cloud Run + neon.  
2. Card 1 · Run 10× — fastest TTFT, connect 0.  
3. Card 2 · Run 10× — slightly above card 1 (query only).  
4. Card 3 · Run 10× — above card 2 (new connect every time).  
5. Card 4 · Run 10× — tokens, then cut; TTFT vs stopped ms.

**Close:** *Fast first token: don’t wait on a new DB connection. New containers pay connect again. If the IP is blocked, start-then-fail cuts the chat; the timeout is “stopped,” not a slow query.*

---

## If something fails

| Symptom | Fix |
| --- | --- |
| Health `platform` is `vm` | You are not on the Cloud Run URL. |
| Ping hangs ~10s / connection refused | NAT IP not on Neon; or `--vpc-egress` is not `all-traffic`. |
| `database: null` | `DATABASE_URL` missing on the revision. |
| Gateway error on Run | `AI_GATEWAY_API_KEY` or `MODEL`. |
| Connector create fails | `/28` in use; pick another range. |
| Card 1 TTFT still moves a bit after 10× | Normal model noise; the average is the number to quote. |
