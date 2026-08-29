import asyncio
import json
import time
import uuid

import httpx

from app.catalog import SCENARIOS
from app.config import API_KEY, GATEWAY_URL, MODEL
from app.neon import check_allowlist_bg, check_allowlist_fresh, drop_pool, insert_log

IDS = {s["id"] for s in SCENARIOS}


def _chars(messages: list) -> int:
    return sum(len(m.get("content") or "") for m in messages if isinstance(m, dict))


async def stream_completion(messages: list, model: str | None = None, scenario: str = "good", role: str = "solo"):
    if scenario not in IDS or scenario == "timeout":
        scenario = "good"
    ctx = {
        "id": str(uuid.uuid4()),
        "model": model or MODEL,
        "messages": messages,
        "scenario": scenario,
        "role": role,
        "t0": time.perf_counter(),
        "ttft_ms": None,
        "connect_ms": 0,
        "query_ms": 0,
        "usage": {},
        "error": None,
        "status": "ok",
    }
    run = {"good": _good, "nat": _nat, "scale": _scale}[scenario]
    try:
        async for chunk in run(ctx):
            yield chunk
    except Exception as exc:
        ctx["status"] = "error"
        ctx["error"] = str(exc)
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    finally:
        await asyncio.to_thread(
            insert_log,
            {
                "id": ctx["id"],
                "model": ctx["model"],
                "status": ctx["status"],
                "scenario": ctx["scenario"],
                "prompt_chars": _chars(ctx["messages"]),
                "ttft_ms": ctx["ttft_ms"],
                "total_ms": int((time.perf_counter() - ctx["t0"]) * 1000),
                "input_tokens": ctx["usage"].get("prompt_tokens"),
                "output_tokens": ctx["usage"].get("completion_tokens"),
                "error": ctx["error"],
            },
        )


def _meta(ctx: dict) -> str:
    body = {
        "id": ctx["id"],
        "ttft_ms": ctx["ttft_ms"],
        "connect_ms": ctx["connect_ms"],
        "query_ms": ctx["query_ms"],
        "scenario": ctx["scenario"],
    }
    return f"event: meta\ndata: {json.dumps(body)}\n\n"


async def _llm(ctx: dict):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": ctx["model"],
        "messages": ctx["messages"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                raw = (await resp.aread()).decode("utf-8", errors="replace")
                ctx["status"] = "error"
                ctx["error"] = f"gateway {resp.status_code}: {raw[:500]}"
                yield f"data: {json.dumps({'error': ctx['error']})}\n\n"
                return
            first = False
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                if not first:
                    ctx["ttft_ms"] = int((time.perf_counter() - ctx["t0"]) * 1000)
                    first = True
                    yield _meta(ctx)
                yield chunk
                _usage(chunk, ctx["usage"])


async def _good(ctx: dict):
    task = asyncio.create_task(asyncio.to_thread(check_allowlist_bg))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    async for chunk in _llm(ctx):
        yield chunk


async def _nat(ctx: dict):
    timing = await asyncio.to_thread(check_allowlist_fresh)
    ctx["connect_ms"] = timing["connect_ms"]
    ctx["query_ms"] = timing["query_ms"]
    async for chunk in _llm(ctx):
        yield chunk


async def _scale(ctx: dict):
    await asyncio.to_thread(drop_pool)
    async for chunk in _nat(ctx):
        yield chunk


def _usage(chunk: bytes, usage: dict) -> None:
    for line in chunk.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
            continue
        try:
            parsed = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if parsed.get("usage"):
            usage.update(parsed["usage"])
