import asyncio
import logging
import threading
import time

from app.config import DATABASE_URL

log = logging.getLogger("chat-proxy")
_schema_ready = False
_pool_lock = threading.Lock()
_pool_conn = None
_hold_sem: asyncio.Semaphore | None = None


def configured() -> bool:
    return bool(DATABASE_URL)


def _url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    if "sslmode=" in DATABASE_URL:
        return DATABASE_URL
    sep = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{sep}sslmode=require"


def connect_fresh():
    import psycopg

    return psycopg.connect(_url(), connect_timeout=10)


def _ensure_schema(conn) -> None:
    global _schema_ready
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS request_logs (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            model TEXT,
            status TEXT,
            scenario TEXT,
            prompt_chars INTEGER,
            ttft_ms INTEGER,
            total_ms INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            error TEXT
        )
        """
    )
    conn.execute("ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS scenario TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS allowlist (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO allowlist (id) VALUES ('demo') ON CONFLICT DO NOTHING")
    conn.commit()
    _schema_ready = True


def ensure_schema() -> None:
    if not DATABASE_URL or _schema_ready:
        return
    with connect_fresh() as conn:
        _ensure_schema(conn)


def _pooled() -> object:
    global _pool_conn
    import psycopg

    if _pool_conn is None or _pool_conn.closed:
        _pool_conn = psycopg.connect(_url(), connect_timeout=10)
        _ensure_schema(_pool_conn)
    return _pool_conn


def warmup() -> dict:
    if not DATABASE_URL:
        return {"ok": False, "error": "DATABASE_URL is not set"}
    t0 = time.perf_counter()
    with _pool_lock:
        _pooled()
        _pooled().execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
    return {"ok": True, "ms": int((time.perf_counter() - t0) * 1000)}


def ping(mode: str) -> dict:
    if not DATABASE_URL:
        return {"ok": False, "error": "DATABASE_URL is not set"}
    if mode == "pooled":
        return {"ok": True, "mode": "pooled", **timed_lookup(fresh=False)}
    return {"ok": True, "mode": "fresh", **timed_lookup(fresh=True)}


def timed_lookup(*, fresh: bool, round_trips: int = 1) -> dict:
    empty = {"connect_ms": 0, "query_ms": 0, "round_trips": round_trips, "pool_wait_ms": 0}
    if not DATABASE_URL:
        return empty
    if fresh:
        t_connect = time.perf_counter()
        conn = connect_fresh()
        connect_ms = int((time.perf_counter() - t_connect) * 1000)
        own = True
    else:
        _pool_lock.acquire()
        conn = _pooled()
        connect_ms = 0
        own = False
    try:
        if not _schema_ready:
            _ensure_schema(conn)
        t_query = time.perf_counter()
        row = None
        for _ in range(round_trips):
            row = conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
        query_ms = int((time.perf_counter() - t_query) * 1000)
        if not row:
            raise PermissionError("not on allowlist")
        return {
            "connect_ms": connect_ms,
            "query_ms": query_ms,
            "round_trips": round_trips,
            "pool_wait_ms": 0,
        }
    finally:
        if own:
            conn.close()
        else:
            _pool_lock.release()


def stall_sync() -> dict:
    """Blocking Neon work meant to freeze the asyncio loop."""
    t0 = time.perf_counter()
    conn = connect_fresh()
    connect_ms = int((time.perf_counter() - t0) * 1000)
    try:
        if not _schema_ready:
            _ensure_schema(conn)
        conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
        t_sleep = time.perf_counter()
        conn.execute("SELECT pg_sleep(1)")
        conn.commit()
        query_ms = int((time.perf_counter() - t_sleep) * 1000)
    finally:
        conn.close()
    return {
        "connect_ms": connect_ms,
        "query_ms": query_ms,
        "round_trips": 2,
        "pool_wait_ms": 0,
    }


def hold_slot() -> asyncio.Semaphore:
    global _hold_sem
    if _hold_sem is None:
        _hold_sem = asyncio.Semaphore(1)
    return _hold_sem


def insert_log(row: dict) -> None:
    if not DATABASE_URL:
        return
    try:
        ensure_schema()
        record = {
            "id": row["id"],
            "model": row.get("model"),
            "status": row.get("status"),
            "scenario": row.get("scenario"),
            "prompt_chars": row.get("prompt_chars"),
            "ttft_ms": row.get("ttft_ms"),
            "total_ms": row.get("total_ms"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "error": row.get("error"),
        }
        with connect_fresh() as conn:
            conn.execute(
                """
                INSERT INTO request_logs (
                    id, model, status, scenario, prompt_chars, ttft_ms, total_ms,
                    input_tokens, output_tokens, error
                ) VALUES (
                    %(id)s, %(model)s, %(status)s, %(scenario)s, %(prompt_chars)s,
                    %(ttft_ms)s, %(total_ms)s, %(input_tokens)s, %(output_tokens)s,
                    %(error)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    scenario = EXCLUDED.scenario,
                    ttft_ms = EXCLUDED.ttft_ms,
                    total_ms = EXCLUDED.total_ms,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    error = EXCLUDED.error
                """,
                record,
            )
            conn.commit()
    except Exception:
        log.exception("log insert failed")


def list_logs(limit: int = 50) -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        ensure_schema()
        with connect_fresh() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, model, status, scenario, prompt_chars, ttft_ms,
                       total_ms, input_tokens, output_tokens, error
                FROM request_logs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1].isoformat() if r[1] else None,
                "model": r[2],
                "status": r[3],
                "scenario": r[4],
                "prompt_chars": r[5],
                "ttft_ms": r[6],
                "total_ms": r[7],
                "input_tokens": r[8],
                "output_tokens": r[9],
                "error": r[10],
            }
            for r in rows
        ]
    except Exception:
        log.exception("log list failed")
        return []
