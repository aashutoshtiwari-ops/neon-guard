# Chat proxy

FastAPI app that streams chat from the [Vercel AI Gateway](https://ai-gateway.vercel.sh) and stores request metadata in Neon. The UI walks through distinct allow-list / TTFT anti-patterns.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 43127
```

Use **one worker** (the default). The loop-block and pool-hold demos are single-process.

| Env | Required | Purpose |
| --- | --- | --- |
| `AI_GATEWAY_API_KEY` | yes | Vercel AI Gateway key |
| `DATABASE_URL` | no | Neon URI (direct is fine; the connect demo is about TLS, not the pooler hostname) |
| `MODEL` | no | default `openai/gpt-5.6-sol` |

## UI

Open `/`. Cards are in demo order (cold compute first). Logs are not fetched on load so a cold-compute run stays cold.

Full script: [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) — what each card proves, what to click, what to see.

## Endpoints

- `GET /` demo UI
- `GET /demos` scenario catalog
- `POST /demos/warmup` open a warm pooled connection
- `POST /demos/ping-db?mode=fresh|pooled` time connect vs query, no model call
- `POST /chat/completions?scenario=&role=` stream. `?block_db=1` still maps to `serial`
- `GET /requests` recent metadata
- `GET /health`
