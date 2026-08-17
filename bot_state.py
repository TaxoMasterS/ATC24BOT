"""Almacén clave-valor genérico para estado del bot que tiene que sobrevivir
un reinicio pero no amerita su propia tabla — hoy solo los message_id de
paneles permanentes que se editan/repostean in-place (tabla ATC, sesiones
agendadas de Academia). Antes vivían en variables de Python en memoria, así
que un reinicio los perdía y el bot terminaba creando un panel nuevo
duplicado en vez de seguir usando el de siempre.
"""

from __future__ import annotations

import time

import aiosqlite


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at INTEGER NOT NULL
        )"""
    )
    await conn.commit()


async def get(conn: aiosqlite.Connection, key: str) -> str | None:
    cur = await conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row[0] if row else None


async def set(conn: aiosqlite.Connection, key: str, value: str | None) -> None:
    if value is None:
        await conn.execute("DELETE FROM bot_state WHERE key = ?", (key,))
    else:
        now = int(time.time() * 1000)
        await conn.execute(
            """INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, now),
        )
    await conn.commit()
