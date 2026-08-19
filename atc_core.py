"""Motor de datos propio del bot — Fase A del rediseño.

Reemplaza la dependencia de server.js (ATC24Español) para vuelos y
posiciones ATC: el bot ahora guarda todo en su propia base SQLite embebida
(vía aiosqlite) y ya no le pega por HTTP a la web para estas dos cosas.

Diseño deliberadamente más simple que el motor original de la web (que tenía
una máquina de estados de 16 pasos pensada para una UI con un botón por
fase). Sin esa UI, esa granularidad no aporta nada — aquí un vuelo solo tiene
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
    await _migrar_columnas(conn)
    return conn


# Columnas agregadas después del esquema original (Bloque A) — ALTER TABLE
# no soporta "ADD COLUMN IF NOT EXISTS" en SQLite, así que se intenta y se
# ignora el error si la columna ya existe. Así una base ya desplegada se
# actualiza sola al reiniciar el bot, sin perder los datos existentes.
_COLUMNAS_NUEVAS = {
    "flights": [
        ("squawk", "TEXT DEFAULT ''"),
        ("closed_at", "TEXT"),
        ("dm_channel_id", "TEXT"),
        ("dm_message_id", "TEXT"),
        ("last_activity_at", "TEXT"),
        ("inactivity_state", "TEXT NOT NULL DEFAULT 'none'"),
        ("inactivity_deadline", "TEXT"),
    ],
    "atc_positions": [
        ("close_scheduled_at", "TEXT"),
        ("close_announcement_channel_id", "TEXT"),
        ("close_announcement_message_id", "TEXT"),
        ("dm_channel_id", "TEXT"),
        ("dm_message_id", "TEXT"),
    ],
}


async def _migrar_columnas(conn: aiosqlite.Connection) -> None:
    for tabla, columnas in _COLUMNAS_NUEVAS.items():
        for nombre, tipo in columnas:
            try:
                await conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
            except aiosqlite.OperationalError:
                pass  # ya existe — normal en cada arranque salvo el primero
    # Filas creadas antes de que existiera last_activity_at quedan con NULL;
    # sin esto, el loop de inactividad las tomaría todas como "inactivas
    # desde siempre" apenas se actualiza el bot y las cerraría de golpe.
    await conn.execute(
        "UPDATE flights SET last_activity_at = updated_at WHERE last_activity_at IS NULL"
    )
    await conn.commit()


# ───────────────────────────── vuelos ──────────────────────────────────


async def create_flight(conn: aiosqlite.Connection, *, owner_id: str, callsign: str,
                         aircraft_type: str = "", departure: str = "", destination: str = "",
                         level: str = "", flight_rules: str = "IFR", route: str = "",
                         alternate: str = "", remarks: str = "", squawk: str = "") -> dict:
    row_uuid = str(_uuid.uuid4())
    now = _now()
    await conn.execute(
        """INSERT INTO flights
           (uuid, owner_id, callsign, aircraft_type, departure, destination, level,
            flight_rules, route, alternate, remarks, squawk, state, created_at, updated_at,
            last_activity_at, inactivity_state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none')""",
        (row_uuid, owner_id, callsign, aircraft_type, departure, destination, level,
         flight_rules, route, alternate, remarks, squawk, FLIGHT_ACTIVO, now, now, now),
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
        "UPDATE flights SET state = ?, updated_at = ?, closed_at = ? WHERE uuid = ? AND state = ?",
        (state, now, now, row_uuid, FLIGHT_ACTIVO),
    )
    await conn.commit()
    return await get_flight(conn, row_uuid)


async def edit_flight(conn: aiosqlite.Connection, row_uuid: str, **campos) -> dict | None:
    """Edita campos del plan (usado por el dashboard privado, Bloque A4).
    Cualquier edición cuenta como actividad real: reinicia el reloj de
    inactividad (last_activity_at + inactivity_state='none')."""
    permitidos = {"callsign", "aircraft_type", "departure", "destination", "level",
                  "flight_rules", "route", "alternate", "remarks"}
    campos = {k: v for k, v in campos.items() if k in permitidos and v is not None}
    if not campos:
        return await get_flight(conn, row_uuid)
    now = _now()
    set_clause = ", ".join(f"{k} = ?" for k in campos)
    await conn.execute(
        f"UPDATE flights SET {set_clause}, updated_at = ?, last_activity_at = ?, "
        f"inactivity_state = 'none', inactivity_deadline = NULL WHERE uuid = ? AND state = ?",
        (*campos.values(), now, now, row_uuid, FLIGHT_ACTIVO),
    )
    await conn.commit()
    return await get_flight(conn, row_uuid)


async def set_squawk(conn: aiosqlite.Connection, row_uuid: str, squawk: str) -> dict | None:
    now = _now()
    await conn.execute(
        "UPDATE flights SET squawk = ?, updated_at = ?, last_activity_at = ?, "
        "inactivity_state = 'none', inactivity_deadline = NULL WHERE uuid = ? AND state = ?",
        (squawk, now, now, row_uuid, FLIGHT_ACTIVO),
    )
    await conn.commit()
    return await get_flight(conn, row_uuid)


async def set_flight_dm(conn: aiosqlite.Connection, row_uuid: str, channel_id: str, message_id: str) -> None:
    await conn.execute(
        "UPDATE flights SET dm_channel_id = ?, dm_message_id = ? WHERE uuid = ?",
        (channel_id, message_id, row_uuid),
    )
    await conn.commit()


# ─── Temporizador de inactividad del plan de vuelo (Bloque A5) ──────────
# Ciclo dirigido por columnas en vez de una task en memoria por vuelo (a
# diferencia del cierre de ATC por canal de voz vacío): sobrevive un
# reinicio del bot sin perder el estado, porque todo el "cuándo pasa qué"
# vive en la fila, no en un asyncio.Task que se pierde al reiniciar.
INACTIVIDAD_AVISO_MIN = 14
INACTIVIDAD_GRACIA_MIN = 1
INACTIVIDAD_EXTENSION_MIN = 5


async def flights_para_avisar(conn: aiosqlite.Connection) -> list[dict]:
    corte = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=INACTIVIDAD_AVISO_MIN)).isoformat()
    cur = await conn.execute(
        "SELECT * FROM flights WHERE state = ? AND inactivity_state = 'none' AND last_activity_at < ?",
        (FLIGHT_ACTIVO, corte),
    )
    return [dict(r) for r in await cur.fetchall()]


async def marcar_flight_avisado(conn: aiosqlite.Connection, row_uuid: str) -> None:
    deadline = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=INACTIVIDAD_GRACIA_MIN)).isoformat()
    await conn.execute(
        "UPDATE flights SET inactivity_state = 'warned', inactivity_deadline = ? WHERE uuid = ?",
        (deadline, row_uuid),
    )
    await conn.commit()


async def confirmar_flight_activo(conn: aiosqlite.Connection, row_uuid: str) -> dict | None:
    """El piloto tocó "Sigo aquí" a tiempo — se le dan 5 minutos más antes de
    volver a evaluar. Devuelve None si ya no correspondía confirmar (el
    vuelo ya se cerró o el plazo ya venció)."""
    fila = await get_flight(conn, row_uuid)
    if not fila or fila["state"] != FLIGHT_ACTIVO or fila["inactivity_state"] != "warned":
        return None
    deadline = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=INACTIVIDAD_EXTENSION_MIN)).isoformat()
    await conn.execute(
        "UPDATE flights SET inactivity_state = 'extended', inactivity_deadline = ? WHERE uuid = ?",
        (deadline, row_uuid),
    )
    await conn.commit()
    return await get_flight(conn, row_uuid)


async def flights_para_finalizar_por_inactividad(conn: aiosqlite.Connection) -> list[dict]:
    """Vuelos en 'warned' o 'extended' cuyo plazo ya venció sin que el
    piloto confirmara ni hiciera una edición real — se cierran solos."""
    ahora = _now()
    cur = await conn.execute(
        "SELECT * FROM flights WHERE state = ? AND inactivity_state IN ('warned', 'extended') "
        "AND inactivity_deadline IS NOT NULL AND inactivity_deadline < ?",
        (FLIGHT_ACTIVO, ahora),
    )
    return [dict(r) for r in await cur.fetchall()]


async def count_active_flights(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM flights WHERE state = ?", (FLIGHT_ACTIVO,))
    (n,) = await cur.fetchone()
    return n



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


async def schedule_atc_close(conn: aiosqlite.Connection, row_uuid: str, *, minutes: int,
                              channel_id: str, message_id: str) -> None:
    """Cierre programado (Bloque A7): en vez de cerrar ya, deja un aviso en
    anuncios ATC con el tiempo restante — el loop de mantenimiento cierra la
    posición y borra el aviso cuando llega la hora."""
    cuando = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()
    await conn.execute(
        "UPDATE atc_positions SET close_scheduled_at = ?, close_announcement_channel_id = ?, "
        "close_announcement_message_id = ? WHERE uuid = ?",
        (cuando, channel_id, message_id, row_uuid),
    )
    await conn.commit()


async def cancel_scheduled_close(conn: aiosqlite.Connection, row_uuid: str) -> dict | None:
    fila = await get_atc(conn, row_uuid)
    if not fila or not fila.get("close_announcement_message_id"):
        return None
    await conn.execute(
        "UPDATE atc_positions SET close_scheduled_at = NULL, close_announcement_channel_id = NULL, "
        "close_announcement_message_id = NULL WHERE uuid = ?",
        (row_uuid,),
    )
    await conn.commit()
    return fila  # devuelve el estado ANTERIOR — el llamador necesita el message_id para borrar el aviso


async def atc_pendientes_de_cierre_programado(conn: aiosqlite.Connection) -> list[dict]:
    ahora = _now()
    cur = await conn.execute(
        "SELECT * FROM atc_positions WHERE state != ? AND close_scheduled_at IS NOT NULL AND close_scheduled_at < ?",
        (ATC_FINALIZADA, ahora),
    )
    return [dict(r) for r in await cur.fetchall()]


async def atc_avisos_cierre_huerfanos(conn: aiosqlite.Connection) -> list[dict]:
    """Red de seguridad: posiciones YA cerradas (por cualquier vía) a las
    que se les olvidó borrar el aviso de "cierra en X minutos" — no debería
    pasar si el cierre limpia bien detrás suyo, pero si algo se escapa
    (reinicio a mitad de camino, excepción puntual) esto lo detecta en la
    próxima pasada del mantenimiento en vez de dejarlo pegado para siempre."""
    cur = await conn.execute(
        "SELECT * FROM atc_positions WHERE state = ? AND close_announcement_message_id IS NOT NULL",
        (ATC_FINALIZADA,),
    )
    return [dict(r) for r in await cur.fetchall()]


async def set_atc_dm(conn: aiosqlite.Connection, row_uuid: str, channel_id: str, message_id: str) -> None:
    await conn.execute(
        "UPDATE atc_positions SET dm_channel_id = ?, dm_message_id = ? WHERE uuid = ?",
        (channel_id, message_id, row_uuid),
    )
    await conn.commit()


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
# de la web. Aquí es el mismo tipo de conteo pero sobre las tablas propias del
# bot: los vuelos/posiciones ATC no se borran al cerrarse, solo cambian de
# estado, así que ya tenemos todo el historial necesario sin tablas nuevas.


def _minutos_entre(inicio: str, fin: str) -> float:
    try:
        t0 = datetime.datetime.fromisoformat(inicio)
        t1 = datetime.datetime.fromisoformat(fin)
        return max(0.0, (t1 - t0).total_seconds() / 60)
    except (TypeError, ValueError):
        return 0.0


async def _minutos_por_owner(conn: aiosqlite.Connection, tabla: str, estado: str) -> dict:
    """Suma minutos (closed_at - created_at) agrupados por owner_id, calculado
    en Python — más simple y portable que confiar en el parseo de fechas
    ISO8601-con-offset de las funciones de fecha nativas de SQLite (que varía
    entre versiones). El volumen esperado (vuelos/posiciones de una comunidad,
    no millones de filas) hace esto perfectamente razonable en rendimiento."""
    cur = await conn.execute(
        f"SELECT owner_id, created_at, closed_at FROM {tabla} WHERE state = ? AND closed_at IS NOT NULL",
        (estado,),
    )
    minutos: dict = {}
    for r in await cur.fetchall():
        minutos[r["owner_id"]] = minutos.get(r["owner_id"], 0.0) + _minutos_entre(r["created_at"], r["closed_at"])
    return minutos


async def top_pilots(conn: aiosqlite.Connection, *, limit: int = 10) -> list[dict]:
    minutos = await _minutos_por_owner(conn, "flights", FLIGHT_COMPLETADO)
    ranking = sorted(
        ({"owner_id": oid, "total_minutos": round(m)} for oid, m in minutos.items()),
        key=lambda x: x["total_minutos"], reverse=True,
    )
    return ranking[:limit]


async def top_controllers(conn: aiosqlite.Connection, *, limit: int = 10) -> list[dict]:
    minutos = await _minutos_por_owner(conn, "atc_positions", ATC_FINALIZADA)
    ranking = sorted(
        ({"owner_id": oid, "total_minutos": round(m)} for oid, m in minutos.items()),
        key=lambda x: x["total_minutos"], reverse=True,
    )
    return ranking[:limit]


async def top_actividad(conn: aiosqlite.Connection, *, limit: int = 10) -> list[dict]:
    """Ranking combinado: suma de minutos volados + minutos controlados por
    persona — para quien participa activamente de las dos ramas."""
    vuelo = await _minutos_por_owner(conn, "flights", FLIGHT_COMPLETADO)
    atc = await _minutos_por_owner(conn, "atc_positions", ATC_FINALIZADA)
    combinado: dict = {}
    for oid, m in vuelo.items():
        combinado[oid] = combinado.get(oid, 0.0) + m
    for oid, m in atc.items():
        combinado[oid] = combinado.get(oid, 0.0) + m
    ranking = sorted(
        ({"owner_id": oid, "total_minutos": round(m)} for oid, m in combinado.items()),
        key=lambda x: x["total_minutos"], reverse=True,
    )
    return ranking[:limit]
