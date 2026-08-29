import asyncio
import json
import time
import uuid

import httpx

from app.catalog import SCENARIOS
from app.config import API_KEY, GATEWAY_URL, MODEL
from app.neon import (
    check_allowlist_bg,
    check_allowlist_fresh,
    check_allowlist_pooled,
    drop_pool,
    fail_unlisted_connect,
    insert_log,
)

IDS = {s["id"] for s in SCENARIOS}


def _chars(messages: list) -> int:
    return sum(len(m.get("content") or "") for m in messages if isinstance(m, dict))


async def stream_completion(messages: list, model: str | None = None, scenario: str = "good", role: str = "solo"):
    if scenario not in IDS:
        scenario = "good"
    ctx = {
        "id": str(uuid.uuid4()),
        "model": model or MODEL,
        "messages": messages,
        "scenario": scenario,
        "role": role,
        "t0": time.perf_counter(),
        "ttft_ms": None,
        "stopped_ms": None,
        "connect_ms": 0,
        "query_ms": 0,
        "usage": {},
        "error": None,
        "status": "ok",
    }
    run = {"good": _good, "nat": _nat, "scale": _scale, "timeout": _timeout}[scenario]
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
        "stopped_ms": ctx["stopped_ms"],
        "connect_ms": ctx["connect_ms"],
        "query_ms": ctx["query_ms"],
        "scenario": ctx["scenario"],
    }
    return f"event: meta\ndata: {json.dumps(body)}\n\n"


def _payload(ctx: dict, max_tokens: int | None = None) -> dict:
    body = {
        "model": ctx["model"],
        "messages": ctx["messages"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return body


async def _llm(ctx: dict, max_tokens: int | None = 64):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=headers, json=_payload(ctx, max_tokens)) as resp:
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
    timing = await asyncio.to_thread(check_allowlist_pooled)
    ctx["connect_ms"] = timing["connect_ms"]
    ctx["query_ms"] = timing["query_ms"]
    async for chunk in _llm(ctx):
        yield chunk


async def _scale(ctx: dict):
    await asyncio.to_thread(drop_pool)
    timing = await asyncio.to_thread(check_allowlist_fresh)
    ctx["connect_ms"] = timing["connect_ms"]
    ctx["query_ms"] = timing["query_ms"]
    async for chunk in _llm(ctx):
        yield chunk


def _task_error(task: asyncio.Task) -> str:
    if task.cancelled():
        return "cancelled"
    exc = task.exception()
    return str(exc) if exc else "database connection failed"


def _cut(ctx: dict, db: asyncio.Task, first: bool):
    ctx["stopped_ms"] = int((time.perf_counter() - ctx["t0"]) * 1000)
    ctx["connect_ms"] = ctx["stopped_ms"]
    ctx["status"] = "stopped"
    ctx["error"] = _task_error(db)
    out = []
    if not first:
        ctx["ttft_ms"] = ctx["stopped_ms"]
        out.append(_meta(ctx))
    out.append(f"data: {json.dumps({'error': ctx['error'], 'stopped_ms': ctx['stopped_ms']})}\n\n")
    return out


async def _timeout(ctx: dict):
    db = asyncio.create_task(asyncio.to_thread(fail_unlisted_connect))
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=headers, json=_payload(ctx, 256)) as resp:
            if resp.status_code >= 400:
                raw = (await resp.aread()).decode("utf-8", errors="replace")
                ctx["status"] = "error"
                ctx["error"] = f"gateway {resp.status_code}: {raw[:500]}"
                yield f"data: {json.dumps({'error': ctx['error']})}\n\n"
                db.cancel()
                return
            aiter = resp.aiter_bytes()
            first = False
            while True:
                if db.done():
                    for part in _cut(ctx, db, first):
                        yield part
                    return
                chunk_task = asyncio.create_task(aiter.__anext__())
                done, _ = await asyncio.wait({chunk_task, db}, return_when=asyncio.FIRST_COMPLETED)
                if db in done:
                    chunk_task.cancel()
                    for part in _cut(ctx, db, first):
                        yield part
                    return
                try:
                    chunk = chunk_task.result()
                except StopAsyncIteration:
                    break
                if not chunk:
                    continue
                if not first:
                    ctx["ttft_ms"] = int((time.perf_counter() - ctx["t0"]) * 1000)
                    first = True
                    yield _meta(ctx)
                yield chunk
                _usage(chunk, ctx["usage"])
            if not db.done():
                await asyncio.wait({db})
            for part in _cut(ctx, db, first):
                yield part


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
