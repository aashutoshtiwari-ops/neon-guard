import logging

from app.config import DATABASE_URL

log = logging.getLogger("chat-proxy")
_ready = False


def _url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    if "sslmode=" in DATABASE_URL:
        return DATABASE_URL
    sep = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{sep}sslmode=require"


def _connect():
    import psycopg

    return psycopg.connect(_url(), connect_timeout=10)


def ensure_schema() -> None:
    global _ready
    if not DATABASE_URL or _ready:
        return
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_logs (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                model TEXT,
                status TEXT,
                prompt_chars INTEGER,
                ttft_ms INTEGER,
                total_ms INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                error TEXT
            )
            """
        )
        conn.commit()
    _ready = True


def insert_log(row: dict) -> None:
    if not DATABASE_URL:
        return
    try:
        ensure_schema()
        record = {
            "id": row["id"],
            "model": row.get("model"),
            "status": row.get("status"),
            "prompt_chars": row.get("prompt_chars"),
            "ttft_ms": row.get("ttft_ms"),
            "total_ms": row.get("total_ms"),
            "input_tokens": row.get("input_tokens"),
            "output_tokens": row.get("output_tokens"),
            "error": row.get("error"),
        }
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO request_logs (
                    id, model, status, prompt_chars, ttft_ms, total_ms,
                    input_tokens, output_tokens, error
                ) VALUES (
                    %(id)s, %(model)s, %(status)s, %(prompt_chars)s, %(ttft_ms)s,
                    %(total_ms)s, %(input_tokens)s, %(output_tokens)s, %(error)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
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
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, model, status, prompt_chars, ttft_ms,
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
                "prompt_chars": r[4],
                "ttft_ms": r[5],
                "total_ms": r[6],
                "input_tokens": r[7],
                "output_tokens": r[8],
                "error": r[9],
            }
            for r in rows
        ]
    except Exception:
        log.exception("log list failed")
        return []
