# Chat proxy

Demo app: Neon IP allow list on Cloud Run and TTFT. Full setup and talk track: [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md).

## Cloud Run

Build and deploy the `Dockerfile`. The process listens on `$PORT` (Cloud Run sets this).

For Neon IP Allow List:

1. Serverless VPC Access connector (or Direct VPC egress) on the service.
2. Cloud NAT with a reserved IP on that network.
3. Put the **NAT IP** on Neon’s allow list (not a Cloud Run ephemeral IP).
4. Set `AI_GATEWAY_API_KEY`, `DATABASE_URL`, optional `MODEL`.

Health pill shows **Cloud Run** when `K_SERVICE` is set.

## Local / VM

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 43127
```

On a VM with a listed public IP there is **no NAT hop**; cards 2–3 still time `connect()`, but the story is for Cloud Run.

| Env | Required | Purpose |
| --- | --- | --- |
| `AI_GATEWAY_API_KEY` | yes | Vercel AI Gateway key |
| `DATABASE_URL` | no | Neon URI |
| `MODEL` | no | default `openai/gpt-5.6-sol` |

## Endpoints

- `GET /` UI
- `GET /demos`
- `POST /demos/ping` — new connection: `connect_ms` / `query_ms` (no model)
- `POST /chat/completions?scenario=good|nat|scale`
- `GET /requests`
- `GET /health` — includes `platform`: `cloudrun` or `vm`
