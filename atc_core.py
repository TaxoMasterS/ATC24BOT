"""Motor de datos propio del bot — Fase A del rediseño.

Reemplaza la dependencia de server.js (ATC24Español) para vuelos y
posiciones ATC: el bot ahora guarda todo en su propia base SQLite embebida
(vía aiosqlite) y ya no le pega por HTTP a la web para estas dos cosas.

Diseño deliberadamente más simple que el motor original de la web (que tenía
una máquina de estados de 16 pasos pensada para una UI con un botón por
fase). Sin esa UI, esa granularidad no aporta nada — acá un vuelo solo tiene
tres estados posibles (activo/completado/cancelado) y una posición ATC
cuatro (abierta/operativa/cerrando/finalizada), que es lo que de verdad se
usa desde Discord.
"""

from __future__ import annotations

import datetime
import uuid as _uuid

import aiosqlite

UNICOM_NAME = "UNICOM | 122.800"

FLIGHT_ACTIVO = "Activo"
FLIGHT_COMPLETADO = "Completado"
FLIGHT_CANCELADO = "Cancelado"
FLIGHT_EXPIRADO = "Expirado"

ATC_ABIERTA = "Abierta"
ATC_CERRANDO = "Cerrando"
ATC_FINALIZADA = "Finalizada"


class PositionAlreadyOpen(Exception):
    """Ya hay una posición activa para ese aeródromo+puesto."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def init_db(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS flights (
            uuid TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            callsign TEXT NOT NULL,
            aircraft_type TEXT,
            departure TEXT,
            destination TEXT,
            level TEXT,
            flight_rules TEXT,
            route TEXT,
            alternate TEXT,
            remarks TEXT,
            state TEXT NOT NULL DEFAULT 'Activo',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS atc_positions (
            uuid TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            controller_name TEXT,
            airport TEXT NOT NULL,
            position_type TEXT NOT NULL,
            frequency TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'Abierta',
            category_id TEXT,
            voice_channel_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS airport_categories (
            icao TEXT PRIMARY KEY,
            category_id TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_flights_state ON flights(state);
        CREATE INDEX IF NOT EXISTS idx_atc_state ON atc_positions(state);
        """
    )
    await conn.commit()
    return conn


# ───────────────────────────── vuelos ──────────────────────────────────


async def create_flight(conn: aiosqlite.Connection, *, owner_id: str, callsign: str,
                         aircraft_type: str = "", departure: str = "", destination: str = "",
                         level: str = "", flight_rules: str = "IFR", route: str = "",
                         alternate: str = "", remarks: str = "") -> dict:
    row_uuid = str(_uuid.uuid4())
    now = _now()
    await conn.execute(
        """INSERT INTO flights
           (uuid, owner_id, callsign, aircraft_type, departure, destination, level,
            flight_rules, route, alternate, remarks, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row_uuid, owner_id, callsign, aircraft_type, departure, destination, level,
         flight_rules, route, alternate, remarks, FLIGHT_ACTIVO, now, now),
    )
    await conn.commit()
    return await get_flight(conn, row_uuid)


async def get_flight(conn: aiosqlite.Connection, row_uuid: str) -> dict | None:
    cur = await conn.execute("SELECT * FROM flights WHERE uuid = ?", (row_uuid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def latest_active_flight_for_owner(conn: aiosqlite.Connection, owner_id: str) -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM flights WHERE owner_id = ? AND state = ? ORDER BY created_at DESC LIMIT 1",
        (owner_id, FLIGHT_ACTIVO),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def close_flight(conn: aiosqlite.Connection, row_uuid: str, state: str) -> dict | None:
    now = _now()
    await conn.execute(
        "UPDATE flights SET state = ?, updated_at = ? WHERE uuid = ? AND state = ?",
        (state, now, row_uuid, FLIGHT_ACTIVO),
    )
    await conn.commit()
    return await get_flight(conn, row_uuid)


async def count_active_flights(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM flights WHERE state = ?", (FLIGHT_ACTIVO,))
    (n,) = await cur.fetchone()
    return n


async def expire_stale_flights(conn: aiosqlite.Connection, older_than_hours: int) -> list[dict]:
    """Marca como Expirado cualquier vuelo activo más viejo que el umbral —
    evita que un plan que el piloto nunca cerró quede activo para siempre en
    el dashboard/contador. Devuelve las filas afectadas."""
    corte = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=older_than_hours)).isoformat()
    cur = await conn.execute(
        "SELECT * FROM flights WHERE state = ? AND created_at < ?", (FLIGHT_ACTIVO, corte)
    )
    filas = [dict(r) for r in await cur.fetchall()]
    if filas:
        now = _now()
        await conn.executemany(
            "UPDATE flights SET state = ?, updated_at = ? WHERE uuid = ?",
            [(FLIGHT_EXPIRADO, now, f["uuid"]) for f in filas],
        )
        await conn.commit()
    return filas


# ─────────────────────────── posiciones ATC ────────────────────────────


async def _find_open_position(conn: aiosqlite.Connection, airport: str, position_type: str) -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM atc_positions WHERE airport = ? AND position_type = ? AND state != ?",
        (airport, position_type, ATC_FINALIZADA),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def open_atc(conn: aiosqlite.Connection, *, owner_id: str, controller_name: str,
                    airport: str, position_type: str, frequency: str) -> dict:
    if await _find_open_position(conn, airport, position_type):
        raise PositionAlreadyOpen(f"{airport}_{position_type}")
    row_uuid = str(_uuid.uuid4())
    now = _now()
    await conn.execute(
        """INSERT INTO atc_positions
           (uuid, owner_id, controller_name, airport, position_type, frequency, state,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row_uuid, owner_id, controller_name, airport, position_type, frequency, ATC_ABIERTA, now, now),
    )
    await conn.commit()
    return await get_atc(conn, row_uuid)


async def get_atc(conn: aiosqlite.Connection, row_uuid: str) -> dict | None:
    cur = await conn.execute("SELECT * FROM atc_positions WHERE uuid = ?", (row_uuid,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_atc_by_voice_channel(conn: aiosqlite.Connection, voice_channel_id: str) -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM atc_positions WHERE voice_channel_id = ? AND state != ?",
        (voice_channel_id, ATC_FINALIZADA),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def set_atc_channel(conn: aiosqlite.Connection, row_uuid: str, category_id: str | None,
                           voice_channel_id: str | None) -> None:
    await conn.execute(
        "UPDATE atc_positions SET category_id = ?, voice_channel_id = ?, updated_at = ? WHERE uuid = ?",
        (category_id, voice_channel_id, _now(), row_uuid),
    )
    await conn.commit()


async def close_atc(conn: aiosqlite.Connection, row_uuid: str, *, reason: str = "") -> dict | None:
    row = await get_atc(conn, row_uuid)
    if not row or row["state"] == ATC_FINALIZADA:
        return None
    now = _now()
    await conn.execute(
        "UPDATE atc_positions SET state = ?, updated_at = ?, closed_at = ? WHERE uuid = ?",
        (ATC_FINALIZADA, now, now, row_uuid),
    )
    await conn.commit()
    return await get_atc(conn, row_uuid)


async def close_atc_by_owner(conn: aiosqlite.Connection, owner_id: str, *, reason: str = "") -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM atc_positions WHERE owner_id = ? AND state != ? ORDER BY created_at DESC LIMIT 1",
        (owner_id, ATC_FINALIZADA),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return await close_atc(conn, row["uuid"], reason=reason)


async def get_active_atc(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute(
        "SELECT * FROM atc_positions WHERE state != ? ORDER BY airport, position_type", (ATC_FINALIZADA,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def count_active_atc(conn: aiosqlite.Connection, airport: str, *, exclude_uuid: str | None = None) -> int:
    if exclude_uuid:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM atc_positions WHERE airport = ? AND state != ? AND uuid != ?",
            (airport, ATC_FINALIZADA, exclude_uuid),
        )
    else:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM atc_positions WHERE airport = ? AND state != ?",
            (airport, ATC_FINALIZADA),
        )
    (n,) = await cur.fetchone()
    return n


# ────────────────────────── categorías por ICAO ─────────────────────────


async def get_category(conn: aiosqlite.Connection, icao: str) -> str | None:
    cur = await conn.execute("SELECT category_id FROM airport_categories WHERE icao = ?", (icao,))
    row = await cur.fetchone()
    return row["category_id"] if row else None


async def save_category(conn: aiosqlite.Connection, icao: str, category_id: str) -> None:
    await conn.execute(
        "INSERT INTO airport_categories (icao, category_id) VALUES (?, ?) "
        "ON CONFLICT(icao) DO UPDATE SET category_id = excluded.category_id",
        (icao, category_id),
    )
    await conn.commit()


async def clear_category(conn: aiosqlite.Connection, icao: str) -> None:
    await conn.execute("DELETE FROM airport_categories WHERE icao = ?", (icao,))
    await conn.commit()


# ────────────────────────────── rankings ────────────────────────────────
# Fase D — antes esto vivía en statsService.js, leyendo la tabla `operations`
# de la web. Acá es el mismo tipo de conteo pero sobre las tablas propias del
# bot: los vuelos/posiciones ATC no se borran al cerrarse, solo cambian de
# estado, así que ya tenemos todo el historial necesario sin tablas nuevas.


async def top_pilots(conn: aiosqlite.Connection, *, limit: int = 10) -> list[dict]:
    cur = await conn.execute(
        """SELECT owner_id, COUNT(*) AS total FROM flights WHERE state = ?
           GROUP BY owner_id ORDER BY total DESC LIMIT ?""",
        (FLIGHT_COMPLETADO, limit),
    )
    return [dict(r) for r in await cur.fetchall()]


async def top_controllers(conn: aiosqlite.Connection, *, limit: int = 10) -> list[dict]:
    cur = await conn.execute(
        """SELECT owner_id, COUNT(*) AS total FROM atc_positions WHERE state = ?
           GROUP BY owner_id ORDER BY total DESC LIMIT ?""",
        (ATC_FINALIZADA, limit),
    )
    return [dict(r) for r in await cur.fetchall()]
