import json
import os
from contextlib import contextmanager
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must point to a shared PostgreSQL database.")

app = FastAPI(title="Mission Control shared state API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ssi-sunbyte.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class StatePayload(BaseModel):
    state: dict[str, Any] = Field(description="Complete Mission Control state")


@contextmanager
def connection():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mission_control_state (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    state JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        yield conn


@app.get("/health")
def health():
    try:
        with connection() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/state")
def get_state():
    try:
        with connection() as conn:
            row = conn.execute(
                "SELECT state, updated_at FROM mission_control_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return {"state": None, "updated_at": None}
        return {"state": row["state"], "updated_at": row["updated_at"].isoformat()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not load shared state") from exc


@app.put("/api/state")
async def put_state(payload: StatePayload, request: Request):
    # Keep accidental or abusive uploads from exhausting the shared database.
    if int(request.headers.get("content-length", "0") or 0) > 1_000_000:
        raise HTTPException(status_code=413, detail="State payload is too large")
    state = payload.state
    if not isinstance(state.get("teams", []), list) or not isinstance(state.get("tasks", []), list):
        raise HTTPException(status_code=422, detail="teams and tasks must be arrays")
    try:
        with connection() as conn:
            row = conn.execute(
                """
                INSERT INTO mission_control_state (id, state, updated_at)
                VALUES (1, %s::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE
                SET state = EXCLUDED.state, updated_at = NOW()
                RETURNING updated_at
                """,
                (json.dumps(state),),
            ).fetchone()
        return {"ok": True, "updated_at": row["updated_at"].isoformat()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not save shared state") from exc
