import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.catalog import SCENARIOS
from app.config import API_KEY, DATABASE_URL, MODEL, STATIC_DIR
from app.gateway import stream_completion
from app.neon import list_logs, ping, platform

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Allow-list TTFT")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {
        "ok": True,
        "model": MODEL,
        "gateway_key": bool(API_KEY),
        "database": "neon" if DATABASE_URL else None,
        "platform": platform(),
        "service": os.getenv("K_SERVICE"),
    }


@app.get("/demos")
def demos():
    return {"scenarios": SCENARIOS}


@app.post("/demos/ping")
def demos_ping():
    return ping()


@app.get("/requests")
def requests_list(limit: int = 50):
    return {"rows": list_logs(limit=min(limit, 200))}


@app.post("/chat/completions")
async def chat_completions(request: Request):
    if not API_KEY:
        return JSONResponse({"error": "AI_GATEWAY_API_KEY is not set"}, status_code=503)
    body = await request.json()
    scenario = request.query_params.get("scenario") or body.get("scenario") or "good"
    role = request.query_params.get("role") or body.get("role") or "solo"
    return StreamingResponse(
        stream_completion(body.get("messages") or [], body.get("model"), scenario=scenario, role=role),
        media_type="text/event-stream",
    )
