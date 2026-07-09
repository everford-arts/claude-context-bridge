#!/usr/bin/env python3
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

# Load .env when running outside systemd
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SECRET = os.environ["BRIDGE_SECRET"]
HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))
DB_PATH = os.environ.get("BRIDGE_DB", str(Path(__file__).parent / "bridge.db"))
LOCAL_TZ = ZoneInfo("America/New_York")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT,
                updated_by TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                source    TEXT,
                summary   TEXT,
                details   TEXT
            )
        """)


mcp = FastMCP("Claude Bridge")


@mcp.tool()
def get_context(key: Optional[str] = None) -> str:
    """Get project context. Pass a key for a specific value, or omit to list all keys and values."""
    with get_db() as conn:
        if key:
            row = conn.execute(
                "SELECT value, updated_at, updated_by FROM context WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return f"No context found for key: {key}"
            return f"{key}: {row['value']}\n(updated {row['updated_at']} by {row['updated_by']})"
        rows = conn.execute(
            "SELECT key, value, updated_at, updated_by FROM context ORDER BY key"
        ).fetchall()
        if not rows:
            return "No context stored yet."
        return "\n\n".join(
            f"{r['key']}: {r['value']}\n(updated {r['updated_at']} by {r['updated_by']})"
            for r in rows
        )


@mcp.tool()
def set_context(key: str, value: str, updated_by: str) -> str:
    """Store or update a context value. updated_by should identify the source, e.g. 'claude-code' or 'claude-ai'."""
    now = datetime.now(LOCAL_TZ).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO context (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, value, now, updated_by),
        )
    return f"Context '{key}' saved at {now}."


@mcp.tool()
def log_session(source: str, summary: str, details: str = "") -> str:
    """Append a session log entry. source identifies the Claude instance: 'claude-code' or 'claude-ai'."""
    now = datetime.now(LOCAL_TZ).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (timestamp, source, summary, details) VALUES (?, ?, ?, ?)",
            (now, source, summary, details),
        )
    return f"Session logged at {now}."


@mcp.tool()
def get_history(limit: int = 10, source: Optional[str] = None) -> str:
    """Get recent session logs, newest first. Optionally filter by source ('claude-code' or 'claude-ai')."""
    with get_db() as conn:
        if source:
            rows = conn.execute(
                "SELECT timestamp, source, summary, details FROM sessions "
                "WHERE source = ? ORDER BY id DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT timestamp, source, summary, details FROM sessions "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    if not rows:
        return "No session history yet."
    entries = []
    for r in rows:
        entry = f"[{r['timestamp']}] ({r['source']}) {r['summary']}"
        if r["details"]:
            entry += f"\n  {r['details']}"
        entries.append(entry)
    return "\n\n".join(entries)


if __name__ == "__main__":
    init_db()
    mcp.run(transport="streamable-http", host=HOST, port=PORT, path=f"/mcp-{SECRET}/")
