#!/usr/bin/env python3
"""Migración única: copia el catálogo de Academia + progreso de alumnos +
certificados desde la base SQLite de la web (ATC24Español) a la base propia
del bot (data/atc24.db).

Por qué existe este script aparte (no corre solo dentro del bot): el bot en
producción (Render) no tiene acceso al disco del servicio web — ambos son
procesos separados en hosts separados. Esta migración solo puede correrse
UNA VEZ, a mano, en una máquina que tenga los dos repos (ej. tu PC), y el
archivo resultante (data/atc24.db) es el que hay que llevar a donde corra
el bot en producción (ver nota sobre disco persistente de Render en el plan
de la Fase A).

Uso:
    python migrar_academia.py "C:\\ruta\\a\\ATC24Espanol\\data.db"

Es seguro correrlo más de una vez — usa INSERT OR IGNORE, así que filas que
ya existen (mismo uuid / mismo par user_id+course_uuid) no se duplican ni se
pisan.
"""

from __future__ import annotations

import asyncio
import os
import sys

import atc_core
import academy_core

CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

TABLAS = [
    ("academy_courses", "uuid"),
    ("academy_modules", "uuid"),
    ("academy_lessons", "uuid"),
    ("academy_enrollments", "uuid"),
    ("academy_progress", "user_id, lesson_uuid"),
    ("academy_course_progress", "user_id, course_uuid"),
    ("academy_exam_submissions", "user_id, course_uuid"),
    ("academy_certificates", "uuid"),
    ("academy_observations", "uuid"),
]


async def migrar(ruta_web_db: str) -> None:
    if not os.path.isfile(ruta_web_db):
        print(f"No encontré el archivo: {ruta_web_db}")
        sys.exit(1)

    ruta_bot_db = os.path.join(CARPETA_SCRIPT, "data", "atc24.db")
    os.makedirs(os.path.dirname(ruta_bot_db), exist_ok=True)

    conn = await atc_core.init_db(ruta_bot_db)
    await academy_core.init_schema(conn)

    ruta_web_escapada = ruta_web_db.replace("'", "''")
    await conn.execute(f"ATTACH DATABASE '{ruta_web_escapada}' AS web")
    try:
        total = 0
        for tabla, _clave in TABLAS:
            cur = await conn.execute(f"SELECT COUNT(*) FROM web.{tabla}")
            (disponibles,) = await cur.fetchone()
            cur = await conn.execute(f"SELECT COUNT(*) FROM {tabla}")
            (antes,) = await cur.fetchone()
            await conn.execute(f"INSERT OR IGNORE INTO {tabla} SELECT * FROM web.{tabla}")
            await conn.commit()
            cur = await conn.execute(f"SELECT COUNT(*) FROM {tabla}")
            (despues,) = await cur.fetchone()
            copiadas = despues - antes
            total += copiadas
            print(f"  {tabla}: {disponibles} en la web, {copiadas} nuevas copiadas al bot")
    finally:
        await conn.execute("DETACH DATABASE web")

    print(f"\nListo. Base del bot: {ruta_bot_db}")
    print("Recordatorio: si el bot corre en Render, este archivo tiene que llegar")
    print("al disco persistente del servicio (ver nota en el plan de la Fase A) —")
    print("copiarlo a mano no alcanza si Render usa disco efímero.")
    await conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Uso: python {os.path.basename(__file__)} <ruta a ATC24Espanol/data.db>")
        sys.exit(1)
    asyncio.run(migrar(sys.argv[1]))
