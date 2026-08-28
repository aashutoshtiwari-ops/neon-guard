import asyncio
import json
import time
import uuid

import httpx

from app.catalog import SCENARIOS
from app.config import API_KEY, GATEWAY_URL, MODEL
from app.neon import hold_slot, insert_log, stall_sync, timed_lookup

SCENARIO_IDS = {s["id"] for s in SCENARIOS}
_EMPTY_DB = {"connect_ms": 0, "query_ms": 0, "round_trips": 0, "pool_wait_ms": 0}


def prompt_chars(messages: list) -> int:
    n = 0
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else ""
        if isinstance(content, str):
            n += len(content)
    return n


async def stream_completion(
    messages: list,
    model: str | None = None,
    scenario: str = "baseline",
    role: str = "solo",
):
    if scenario not in SCENARIO_IDS or scenario == "nat":
        scenario = "baseline"
    model = model or MODEL
    req_id = str(uuid.uuid4())
    started = time.perf_counter()
    ctx = {
        "req_id": req_id,
        "model": model,
        "messages": messages,
        "scenario": scenario,
        "role": role,
        "started": started,
        "ttft_ms": None,
        "usage": {},
        "error": None,
        "status": "ok",
        "db": dict(_EMPTY_DB),
    }
    runner = {
        "baseline": _baseline,
        "serial": _serial,
        "gather": _gather,
        "connect": _connect,
        "cold": _connect,
        "rtts": _rtts,
        "loop": _loop,
        "hold": _hold,
    }[scenario]
    try:
        async for chunk in runner(ctx):
            yield chunk
    except Exception as exc:
        ctx["status"] = "error"
        ctx["error"] = str(exc)
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    finally:
        if scenario == "loop" and role == "blocker":
            insert_log(_log_row(ctx))
        else:
            await asyncio.to_thread(insert_log, _log_row(ctx))


async def _allowlist(*, fresh: bool, round_trips: int = 1) -> dict:
    return await asyncio.to_thread(timed_lookup, fresh=fresh, round_trips=round_trips)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


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


def _meta(ctx: dict) -> str:
    payload = {
        "id": ctx["req_id"],
        "ttft_ms": ctx["ttft_ms"],
        "scenario": ctx["scenario"],
        "role": ctx["role"],
        **ctx["db"],
    }
    return f"event: meta\ndata: {json.dumps(payload)}\n\n"


async def _iter_stream(resp: httpx.Response, ctx: dict):
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
            ctx["ttft_ms"] = int((time.perf_counter() - ctx["started"]) * 1000)
            first = True
            yield _meta(ctx)
        yield chunk
        _capture_usage(chunk, ctx["usage"])


async def _baseline(ctx: dict):
    task = asyncio.create_task(_allowlist(fresh=False), name="allowlist-bg")
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=_headers(), json=_payload(ctx)) as resp:
            async for chunk in _iter_stream(resp, ctx):
                yield chunk


async def _serial(ctx: dict):
    ctx["db"] = await _allowlist(fresh=False)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=_headers(), json=_payload(ctx)) as resp:
            async for chunk in _iter_stream(resp, ctx):
                yield chunk


async def _connect(ctx: dict):
    ctx["db"] = await _allowlist(fresh=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=_headers(), json=_payload(ctx)) as resp:
            async for chunk in _iter_stream(resp, ctx):
                yield chunk


async def _rtts(ctx: dict):
    ctx["db"] = await _allowlist(fresh=False, round_trips=4)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        async with client.stream("POST", GATEWAY_URL, headers=_headers(), json=_payload(ctx)) as resp:
            async for chunk in _iter_stream(resp, ctx):
                yield chunk


async def _gather(ctx: dict):
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        req = client.build_request("POST", GATEWAY_URL, headers=_headers(), json=_payload(ctx))
        db, resp = await asyncio.gather(_allowlist(fresh=False), client.send(req, stream=True))
        ctx["db"] = db
        try:
            async for chunk in _iter_stream(resp, ctx):
                yield chunk
        finally:
            await resp.aclose()


async def _loop(ctx: dict):
    if ctx["role"] != "blocker":
        async for chunk in _baseline(ctx):
            yield chunk
        return
    ctx["ttft_ms"] = int((time.perf_counter() - ctx["started"]) * 1000)
    yield _meta(ctx)
    ctx["db"] = stall_sync()
    ctx["status"] = "loop_stall"


async def _hold(ctx: dict):
    sem = hold_slot()
    t_wait = time.perf_counter()
    await sem.acquire()
    ctx["db"] = {
        **_EMPTY_DB,
        "pool_wait_ms": int((time.perf_counter() - t_wait) * 1000),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            async with client.stream(
                "POST", GATEWAY_URL, headers=_headers(), json=_payload(ctx, max_tokens=48)
            ) as resp:
                async for chunk in _iter_stream(resp, ctx):
                    yield chunk
    finally:
        sem.release()


def _log_row(ctx: dict) -> dict:
    return {
        "id": ctx["req_id"],
        "model": ctx["model"],
        "status": ctx["status"],
        "scenario": ctx["scenario"] if ctx["role"] == "solo" else f"{ctx['scenario']}:{ctx['role']}",
        "prompt_chars": prompt_chars(ctx["messages"]),
        "ttft_ms": ctx["ttft_ms"],
        "total_ms": int((time.perf_counter() - ctx["started"]) * 1000),
        "input_tokens": ctx["usage"].get("prompt_tokens"),
        "output_tokens": ctx["usage"].get("completion_tokens"),
        "error": ctx["error"],
    }


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
