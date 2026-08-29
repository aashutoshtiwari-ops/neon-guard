import logging
import os
import threading
import time

from app.config import DATABASE_URL

log = logging.getLogger("chat-proxy")
_ready = False
_lock = threading.Lock()
_conn = None


def platform() -> str:
    return "cloudrun" if os.getenv("K_SERVICE") else "vm"


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


def drop_pool() -> None:
    global _conn
    with _lock:
        if _conn is not None and not _conn.closed:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None


def _pooled_conn():
    global _conn
    if _conn is None or _conn.closed:
        t0 = time.perf_counter()
        _conn = _connect()
        _schema(_conn)
        return _conn, int((time.perf_counter() - t0) * 1000)
    return _conn, 0


def warmup() -> dict:
    if not DATABASE_URL:
        return {"ok": False, "error": "DATABASE_URL is not set"}
    with _lock:
        conn, connect_ms = _pooled_conn()
        t1 = time.perf_counter()
        conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
        query_ms = int((time.perf_counter() - t1) * 1000)
    return {"ok": True, "connect_ms": connect_ms, "query_ms": query_ms}


def check_allowlist_pooled() -> dict:
    """Reuse one connection in this process. connect_ms is 0 after warmup."""
    if not DATABASE_URL:
        return {"connect_ms": 0, "query_ms": 0}
    with _lock:
        conn, connect_ms = _pooled_conn()
        t1 = time.perf_counter()
        row = conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
        query_ms = int((time.perf_counter() - t1) * 1000)
    if not row:
        raise PermissionError("not on allowlist")
    return {"connect_ms": connect_ms, "query_ms": query_ms}


def ping() -> dict:
    """Time a new TCP+TLS+auth through whatever path Cloud Run uses (NAT if configured)."""
    empty = {"ok": False, "connect_ms": 0, "query_ms": 0, "error": "DATABASE_URL is not set"}
    if not DATABASE_URL:
        return empty
    t0 = time.perf_counter()
    conn = _connect()
    connect_ms = int((time.perf_counter() - t0) * 1000)
    try:
        if not _ready:
            _schema(conn)
        t1 = time.perf_counter()
        row = conn.execute("SELECT id FROM allowlist WHERE id = %s", ("demo",)).fetchone()
        query_ms = int((time.perf_counter() - t1) * 1000)
        if not row:
            raise PermissionError("not on allowlist")
        return {"ok": True, "connect_ms": connect_ms, "query_ms": query_ms}
    finally:
        conn.close()


def check_allowlist_fresh() -> dict:
    if not DATABASE_URL:
        return {"connect_ms": 0, "query_ms": 0}
    result = ping()
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "allowlist ping failed")
    return {"connect_ms": result["connect_ms"], "query_ms": result["query_ms"]}


def check_allowlist_bg() -> None:
    if not DATABASE_URL:
        return
    ping()


def fail_unlisted_connect() -> None:
    """Hang until connect_timeout — same shape as Neon dropping an unlisted IP."""
    import psycopg

    host = "db.neon.tech"
    if DATABASE_URL:
        from urllib.parse import urlparse

        parsed = urlparse(DATABASE_URL.replace("postgres://", "postgresql://", 1))
        if parsed.hostname:
            host = parsed.hostname
    try:
        psycopg.connect(
            f"host={host} hostaddr=192.0.2.1 port=5432 user=denied password=denied "
            "dbname=neondb sslmode=require connect_timeout=5",
        )
    except Exception as exc:
        raise ConnectionError(
            f"Database connection failed (IP not on allow list). {exc}"
        ) from exc
    raise ConnectionError("Database connection failed (IP not on allow list).")


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
