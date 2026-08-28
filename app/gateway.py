import json
import time
import uuid

import httpx

from app.config import API_KEY, GATEWAY_URL, MODEL
from app.db import insert_log


def prompt_chars(messages: list) -> int:
    n = 0
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else ""
        if isinstance(content, str):
            n += len(content)
    return n


async def stream_completion(messages: list, model: str | None = None, block_db: bool = False):
    model = model or MODEL
    req_id = str(uuid.uuid4())
    started = time.perf_counter()
    ttft_ms = None
    usage: dict = {}
    error = None
    status = "ok"
    first = False

    if block_db:
        insert_log(
            {
                "id": req_id,
                "model": model,
                "status": "blocked_before_stream",
                "prompt_chars": prompt_chars(messages),
            }
        )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            async with client.stream("POST", GATEWAY_URL, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")
                    error = f"gateway {resp.status_code}: {raw[:500]}"
                    status = "error"
                    yield f"data: {json.dumps({'error': error})}\n\n"
                    return
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if not first:
                        ttft_ms = int((time.perf_counter() - started) * 1000)
                        first = True
                        yield f"event: meta\ndata: {json.dumps({'ttft_ms': ttft_ms, 'id': req_id, 'block_db': block_db})}\n\n"
                    yield chunk
                    _capture_usage(chunk, usage)
    except Exception as exc:
        status = "error"
        error = str(exc)
        yield f"data: {json.dumps({'error': error})}\n\n"
    finally:
        insert_log(
            {
                "id": req_id,
                "model": model,
                "status": status,
                "prompt_chars": prompt_chars(messages),
                "ttft_ms": ttft_ms,
                "total_ms": int((time.perf_counter() - started) * 1000),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "error": error,
            }
        )


def _capture_usage(chunk: bytes, usage: dict) -> None:
    text = chunk.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
            continue
        try:
            parsed = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if parsed.get("usage"):
            usage.update(parsed["usage"])
