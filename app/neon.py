import logging
import threading
import time

from app.config import DATABASE_URL

log = logging.getLogger("chat-proxy")
_ready = False
_lock = threading.Lock()
_conn = None


def _dsn() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    if "sslmode=" in DATABASE_URL:
        return DATABASE_URL
    sep = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{sep}sslmode=require"


def _connect():
    import psycopg

    return psycopg.connect(_dsn(), connect_timeout=10)


def _schema(conn) -> None:
    global _ready
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
    _ready = True


def check_allowlist() -> int:
    """Pooled SELECT. Returns elapsed ms. 0 if DATABASE_URL is unset."""
    if not DATABASE_URL:
        return 0
    t0 = time.perf_counter()
    with _lock:
        global _conn
        if _conn is None or _conn.closed:
            _conn = _connect()
            _schema(_conn)
        row = _conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
    if not row:
        raise PermissionError("not on allowlist")
    return int((time.perf_counter() - t0) * 1000)


def check_allowlist_blocking() -> int:
    """Sync connect + SELECT on the caller’s thread (freezes asyncio if used on the loop)."""
    if not DATABASE_URL:
        time.sleep(1)
        return 1000
    t0 = time.perf_counter()
    with _connect() as conn:
        if not _ready:
            _schema(conn)
        conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
        conn.execute("SELECT pg_sleep(1)")
        conn.commit()
    return int((time.perf_counter() - t0) * 1000)


def insert_log(row: dict) -> None:
    if not DATABASE_URL:
        return
    try:
        with _connect() as conn:
            if not _ready:
                _schema(conn)
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
                {
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
                },
            )
            conn.commit()
    except Exception:
        log.exception("log insert failed")


def list_logs(limit: int = 50) -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with _connect() as conn:
            if not _ready:
                _schema(conn)
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
