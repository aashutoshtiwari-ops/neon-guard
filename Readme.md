# Chat proxy

Streams chat from the [Vercel AI Gateway](https://ai-gateway.vercel.sh) and demos how an allow-list check in **application code** inflates TTFT.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 43127
```

One worker (default). Walkthrough: [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md).

| Env | Required | Purpose |
| --- | --- | --- |
| `AI_GATEWAY_API_KEY` | yes | Vercel AI Gateway key |
| `DATABASE_URL` | no | Neon URI. Allow-list checks no-op if empty |
| `MODEL` | no | default `openai/gpt-5.6-sol` |

## Endpoints

- `GET /` demo UI (four cards)
- `GET /demos` catalog
- `POST /chat/completions?scenario=good|serial|gather|sync&role=`
- `GET /requests`
- `GET /health`
