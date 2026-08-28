# Chat proxy

FastAPI app that streams chat from the [Vercel AI Gateway](https://ai-gateway.vercel.sh) and stores request metadata in Neon.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 43127
```

| Env | Required | Purpose |
| --- | --- | --- |
| `AI_GATEWAY_API_KEY` | yes | Vercel AI Gateway key |
| `DATABASE_URL` | no | Neon pooled URI. Logging is skipped if empty |
| `MODEL` | no | default `openai/gpt-5.6-sol` |

## Endpoints

- `GET /` chat UI
- `POST /chat/completions` body `{ "messages": [...] }`. Default: stream first, log after. `?block_db=1` waits on Neon before the first token (worse TTFT).
- `GET /requests` recent metadata
- `GET /health`
