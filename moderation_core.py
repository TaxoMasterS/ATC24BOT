"""Motor de moderación propio del bot — Fase B del rediseño.

Antes las advertencias vivían en la web (server.js) y no existía ningún
comando de timeout/kick/ban — solo /advertir (warn) y /borrar (purge de
mensajes, nativo de Discord, sin registro). Esto reemplaza esa dependencia
con casos numerados (case ID) guardados en la misma base SQLite del bot
(atc_core.init_db ya crea el archivo; este módulo agrega sus propias
tablas a la misma conexión), y le da a cada acción de moderación un mismo
formato de registro.
"""

from __future__ import annotations

import datetime

import aiosqlite

WARN = "WARN"
TIMEOUT = "TIMEOUT"
KICK = "KICK"
BAN = "BAN"
UNBAN = "UNBAN"

ACTION_LABELS = {
    WARN: "Advertencia",
    TIMEOUT: "Timeout",
    KICK: "Expulsión",
    BAN: "Ban",
    UNBAN: "Unban",
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mod_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            moderator_id TEXT NOT NULL,
            moderator_name TEXT,
            action TEXT NOT NULL,
            reason TEXT,
            duration_minutes INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mod_cases_user ON mod_cases(user_id);
        """
    )
    await conn.commit()


async def create_case(conn: aiosqlite.Connection, *, user_id: str, moderator_id: str,
                       moderator_name: str, action: str, reason: str = "",
                       duration_minutes: int | None = None) -> dict:
    now = _now()
    cur = await conn.execute(
        """INSERT INTO mod_cases
           (user_id, moderator_id, moderator_name, action, reason, duration_minutes, active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
        (user_id, moderator_id, moderator_name, action, reason, duration_minutes, now),
    )
    await conn.commit()
    return await get_case(conn, cur.lastrowid)


async def get_case(conn: aiosqlite.Connection, case_id: int) -> dict | None:
    cur = await conn.execute("SELECT * FROM mod_cases WHERE id = ?", (case_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def cases_for_user(conn: aiosqlite.Connection, user_id: str, *, limit: int = 25) -> list[dict]:
    cur = await conn.execute(
        "SELECT * FROM mod_cases WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    return [dict(r) for r in await cur.fetchall()]


async def count_active_warns(conn: aiosqlite.Connection, user_id: str) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM mod_cases WHERE user_id = ? AND action = ? AND active = 1",
        (user_id, WARN),
    )
    (n,) = await cur.fetchone()
    return n


async def revoke_case(conn: aiosqlite.Connection, case_id: int) -> dict | None:
    await conn.execute("UPDATE mod_cases SET active = 0 WHERE id = ?", (case_id,))
    await conn.commit()
    return await get_case(conn, case_id)
