"""Sesiones agendadas de Academia — instructor agenda una clase con horario,
alumnos se anotan de antemano, y al llegar la hora se publica un panel en
vivo con inscripción abierta (Unirse/Salir/Ver alumnos).

No está ligado 1:1 al catálogo de cursos (academy_core) a propósito: acá
"curso"/"categoría" son texto libre que pone el instructor al agendar (igual
que el sistema de referencia), no necesariamente un academy_courses.uuid —
mantiene el agendado simple sin forzar que cada sesión mapee a un curso
formal del motor de evaluación.
"""

from __future__ import annotations

import time
import uuid as _uuid

import aiosqlite

SCHEDULED = "scheduled"
LIVE = "live"
COMPLETED = "completed"
CANCELLED = "cancelled"


def _now_ms() -> int:
    return int(time.time() * 1000)


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS academy_sessions (
            uuid TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            course_title TEXT NOT NULL,
            instructor_id TEXT NOT NULL,
            scheduled_at INTEGER NOT NULL,
            max_students INTEGER,
            state TEXT NOT NULL DEFAULT 'scheduled',
            channel_id TEXT,
            message_id TEXT,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_state ON academy_sessions(state, scheduled_at);

        CREATE TABLE IF NOT EXISTS academy_session_signups (
            session_uuid TEXT NOT NULL,
            user_id TEXT NOT NULL,
            signed_up_at INTEGER NOT NULL,
            PRIMARY KEY (session_uuid, user_id)
        );
        """
    )
    await conn.commit()


async def create_session(conn: aiosqlite.Connection, *, category: str, course_title: str,
                          instructor_id: str, scheduled_at_ms: int, max_students: int | None = None) -> dict:
    row_uuid = str(_uuid.uuid4())
    now = _now_ms()
    await conn.execute(
        """INSERT INTO academy_sessions
           (uuid, category, course_title, instructor_id, scheduled_at, max_students, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)""",
        (row_uuid, category, course_title, instructor_id, scheduled_at_ms, max_students, now, now),
    )
    await conn.commit()
    return await get_session(conn, row_uuid)


async def get_session(conn: aiosqlite.Connection, session_uuid: str) -> dict | None:
    cur = await conn.execute("SELECT * FROM academy_sessions WHERE uuid = ?", (session_uuid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def upcoming_sessions(conn: aiosqlite.Connection, *, limit: int = 50) -> list[dict]:
    cur = await conn.execute(
        "SELECT * FROM academy_sessions WHERE state = 'scheduled' ORDER BY scheduled_at ASC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def due_sessions(conn: aiosqlite.Connection) -> list[dict]:
    """Sesiones agendadas cuya hora ya llegó — listas para pasar a 'live'."""
    cur = await conn.execute(
        "SELECT * FROM academy_sessions WHERE state = 'scheduled' AND scheduled_at <= ?", (_now_ms(),)
    )
    return [dict(r) for r in await cur.fetchall()]


async def set_live(conn: aiosqlite.Connection, session_uuid: str, channel_id: str, message_id: str) -> None:
    await conn.execute(
        "UPDATE academy_sessions SET state = 'live', channel_id = ?, message_id = ?, updated_at = ? WHERE uuid = ?",
        (channel_id, message_id, _now_ms(), session_uuid),
    )
    await conn.commit()


async def set_state(conn: aiosqlite.Connection, session_uuid: str, state: str) -> None:
    await conn.execute(
        "UPDATE academy_sessions SET state = ?, updated_at = ? WHERE uuid = ?", (state, _now_ms(), session_uuid)
    )
    await conn.commit()


async def sign_up(conn: aiosqlite.Connection, session_uuid: str, user_id: str) -> str:
    """Devuelve 'ok', 'already', o 'full'."""
    fila = await get_session(conn, session_uuid)
    if not fila:
        return "not_found"
    cur = await conn.execute(
        "SELECT 1 FROM academy_session_signups WHERE session_uuid = ? AND user_id = ?", (session_uuid, user_id)
    )
    if await cur.fetchone():
        return "already"
    if fila["max_students"] is not None:
        n = await count_signups(conn, session_uuid)
        if n >= fila["max_students"]:
            return "full"
    await conn.execute(
        "INSERT INTO academy_session_signups (session_uuid, user_id, signed_up_at) VALUES (?, ?, ?)",
        (session_uuid, user_id, _now_ms()),
    )
    await conn.commit()
    return "ok"


async def leave(conn: aiosqlite.Connection, session_uuid: str, user_id: str) -> bool:
    cur = await conn.execute(
        "DELETE FROM academy_session_signups WHERE session_uuid = ? AND user_id = ?", (session_uuid, user_id)
    )
    await conn.commit()
    return cur.rowcount > 0


async def count_signups(conn: aiosqlite.Connection, session_uuid: str) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM academy_session_signups WHERE session_uuid = ?", (session_uuid,))
    (n,) = await cur.fetchone()
    return n


async def list_signups(conn: aiosqlite.Connection, session_uuid: str) -> list[str]:
    cur = await conn.execute(
        "SELECT user_id FROM academy_session_signups WHERE session_uuid = ? ORDER BY signed_up_at ASC", (session_uuid,)
    )
    return [r["user_id"] for r in await cur.fetchall()]
