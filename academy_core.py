"""Motor de Academia propio del bot — Fase C del rediseño.

Mismo esquema (nombres de tabla y columnas) que `academy_*` en
storage/SqliteDatabase.js del repo web, a propósito: así `migrar_academia.py`
puede copiar las filas 1:1 con un simple ATTACH + INSERT, sin pisar ni perder
el catálogo de cursos ya autorado ni el progreso real de los alumnos.

Alcance de esta fase (deliberado, ver plan): progreso/certificados/cola de
evaluaciones/inscripción/aprobar-rechazar quedan operativos desde Discord.
El armado de cursos/lecciones/exámenes (antes un editor en la web) y la
vista de "tomar una lección/examen" quedan FUERA de esta fase — son una
pieza de UI grande en sí misma (contenido enriquecido + examen cronometrado)
que merece su propio diseño dedicado, no un apéndice apurado aquí.
"""

from __future__ import annotations

import time

import aiosqlite

BRANCHES = ("pilot", "atc")


def _now_ms() -> int:
    return int(time.time() * 1000)


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS academy_courses (
            uuid TEXT PRIMARY KEY,
            branch TEXT NOT NULL,
            code TEXT, title TEXT, description TEXT,
            order_index INTEGER NOT NULL DEFAULT 0,
            requires_course_uuid TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            resources TEXT,
            exam TEXT,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ac_courses_branch ON academy_courses(branch, status, order_index);

        CREATE TABLE IF NOT EXISTS academy_modules (
            uuid TEXT PRIMARY KEY,
            course_uuid TEXT NOT NULL,
            title TEXT,
            order_index INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS academy_lessons (
            uuid TEXT PRIMARY KEY,
            module_uuid TEXT NOT NULL,
            course_uuid TEXT NOT NULL,
            title TEXT,
            order_index INTEGER NOT NULL DEFAULT 0,
            required INTEGER NOT NULL DEFAULT 1,
            content TEXT,
            resources TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS academy_enrollments (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            branch TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            claimed_by TEXT, claim_expires_at INTEGER,
            resolved_by TEXT, reason TEXT,
            exam_answers TEXT,
            mc_score INTEGER, mc_total INTEGER,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ac_enroll_branch_state ON academy_enrollments(branch, state);
        CREATE INDEX IF NOT EXISTS idx_ac_enroll_user ON academy_enrollments(user_id, branch);

        CREATE TABLE IF NOT EXISTS academy_progress (
            user_id TEXT NOT NULL,
            lesson_uuid TEXT NOT NULL,
            completed_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, lesson_uuid)
        );

        CREATE TABLE IF NOT EXISTS academy_course_progress (
            user_id TEXT NOT NULL,
            course_uuid TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'locked',
            theory_done_at INTEGER,
            eval_state TEXT NOT NULL DEFAULT 'locked',
            eval_claimed_by TEXT, eval_claim_expires_at INTEGER,
            approved_by TEXT, approved_at INTEGER,
            exam_started_at INTEGER, exam_attempts INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, course_uuid)
        );
        CREATE INDEX IF NOT EXISTS idx_ac_cprog_course ON academy_course_progress(course_uuid, eval_state);

        CREATE TABLE IF NOT EXISTS academy_exam_submissions (
            user_id TEXT NOT NULL,
            course_uuid TEXT NOT NULL,
            answers TEXT NOT NULL,
            mc_score INTEGER NOT NULL DEFAULT 0,
            mc_total INTEGER NOT NULL DEFAULT 0,
            submitted_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, course_uuid)
        );

        CREATE TABLE IF NOT EXISTS academy_certificates (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            course_uuid TEXT NOT NULL,
            type TEXT NOT NULL,
            issued_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ac_certs_user ON academy_certificates(user_id);

        CREATE TABLE IF NOT EXISTS academy_observations (
            uuid TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            course_uuid TEXT,
            instructor_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    await conn.commit()
    # Bloque D2: código corto de verificación de autenticidad, agregado
    # después del esquema original — mismo patrón de migración incremental
    # que atc_core.py (ALTER TABLE, se ignora si ya existe).
    try:
        await conn.execute("ALTER TABLE academy_certificates ADD COLUMN verify_code TEXT")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    # Rediseño de certificados: quién lo emitió (instructor que aprobó la
    # evaluación) y su estado (VALIDO/REVOCADO) — un código revocado sigue
    # existiendo en la tabla, nunca se borra ni se reutiliza.
    try:
        await conn.execute("ALTER TABLE academy_certificates ADD COLUMN instructor_id TEXT")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    try:
        await conn.execute("ALTER TABLE academy_certificates ADD COLUMN status TEXT NOT NULL DEFAULT 'VALIDO'")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    try:
        await conn.execute("ALTER TABLE academy_certificates ADD COLUMN revoked_by TEXT")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass
    try:
        await conn.execute("ALTER TABLE academy_certificates ADD COLUMN revoked_at INTEGER")
        await conn.commit()
    except aiosqlite.OperationalError:
        pass


async def has_courses(conn: aiosqlite.Connection) -> bool:
    cur = await conn.execute("SELECT COUNT(*) FROM academy_courses")
    (n,) = await cur.fetchone()
    return n > 0


# ─────────────────────────── inscripción ───────────────────────────────


async def find_active_enrollment(conn: aiosqlite.Connection, user_id: str, branch: str) -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM academy_enrollments WHERE user_id = ? AND branch = ? AND state != 'rejected' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, branch),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def request_enrollment(conn: aiosqlite.Connection, user_id: str, branch: str) -> dict:
    """Sin examen de admisión (Fase C no porta el motor de exámenes) — la
    inscripción queda pendiente de aprobación manual de un instructor."""
    import uuid as _uuid
    now = _now_ms()
    row_uuid = str(_uuid.uuid4())
    await conn.execute(
        """INSERT INTO academy_enrollments
           (uuid, user_id, branch, state, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?)""",
        (row_uuid, user_id, branch, now, now),
    )
    await conn.commit()
    cur = await conn.execute("SELECT * FROM academy_enrollments WHERE uuid = ?", (row_uuid,))
    return dict(await cur.fetchone())


async def resolve_enrollment(conn: aiosqlite.Connection, enrollment_uuid: str, approve: bool,
                              resolver_id: str, reason: str = "") -> dict | None:
    cur = await conn.execute("SELECT * FROM academy_enrollments WHERE uuid = ?", (enrollment_uuid,))
    row = await cur.fetchone()
    if not row or row["state"] != "pending":
        return None
    now = _now_ms()
    nuevo_estado = "approved" if approve else "rejected"
    await conn.execute(
        "UPDATE academy_enrollments SET state = ?, resolved_by = ?, reason = ?, updated_at = ? WHERE uuid = ?",
        (nuevo_estado, resolver_id, reason, now, enrollment_uuid),
    )
    if approve:
        cur2 = await conn.execute(
            """SELECT * FROM academy_courses WHERE branch = ? AND status = 'active'
               AND requires_course_uuid IS NULL AND code NOT LIKE 'RG-%'
               ORDER BY order_index ASC LIMIT 1""",
            (row["branch"],),
        )
        primero = await cur2.fetchone()
        if primero:
            await _upsert_course_progress(conn, row["user_id"], primero["uuid"], state="in_progress", eval_state="locked")
    await conn.commit()
    cur3 = await conn.execute("SELECT * FROM academy_enrollments WHERE uuid = ?", (enrollment_uuid,))
    return dict(await cur3.fetchone())


async def pending_enrollments(conn: aiosqlite.Connection, branch: str | None = None) -> list[dict]:
    if branch:
        cur = await conn.execute(
            "SELECT * FROM academy_enrollments WHERE branch = ? AND state = 'pending' ORDER BY created_at ASC", (branch,)
        )
    else:
        cur = await conn.execute("SELECT * FROM academy_enrollments WHERE state = 'pending' ORDER BY created_at ASC")
    return [dict(r) for r in await cur.fetchall()]


# ────────────────────────── progreso / cursos ───────────────────────────


async def _upsert_course_progress(conn: aiosqlite.Connection, user_id: str, course_uuid: str, **campos) -> None:
    cur = await conn.execute(
        "SELECT * FROM academy_course_progress WHERE user_id = ? AND course_uuid = ?", (user_id, course_uuid)
    )
    actual = await cur.fetchone()
    base = dict(actual) if actual else {
        "state": "locked", "theory_done_at": None, "eval_state": "locked",
        "eval_claimed_by": None, "eval_claim_expires_at": None,
        "approved_by": None, "approved_at": None, "exam_started_at": None, "exam_attempts": 0,
    }
    base.update(campos)
    await conn.execute(
        """INSERT INTO academy_course_progress
           (user_id, course_uuid, state, theory_done_at, eval_state, eval_claimed_by, eval_claim_expires_at,
            approved_by, approved_at, exam_started_at, exam_attempts, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, course_uuid) DO UPDATE SET
             state=excluded.state, theory_done_at=excluded.theory_done_at, eval_state=excluded.eval_state,
             eval_claimed_by=excluded.eval_claimed_by, eval_claim_expires_at=excluded.eval_claim_expires_at,
             approved_by=excluded.approved_by, approved_at=excluded.approved_at,
             exam_started_at=excluded.exam_started_at, exam_attempts=excluded.exam_attempts,
             updated_at=excluded.updated_at""",
        (user_id, course_uuid, base["state"], base["theory_done_at"], base["eval_state"],
         base["eval_claimed_by"], base["eval_claim_expires_at"], base["approved_by"], base["approved_at"],
         base["exam_started_at"], base["exam_attempts"], _now_ms()),
    )
    await conn.commit()


async def user_state(conn: aiosqlite.Connection, user_id: str) -> dict:
    cur1 = await conn.execute(
        "SELECT * FROM academy_enrollments WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    )
    enrollments = [dict(r) for r in await cur1.fetchall()]

    cur2 = await conn.execute(
        """SELECT cp.*, c.title AS course_title, c.branch AS branch FROM academy_course_progress cp
           JOIN academy_courses c ON c.uuid = cp.course_uuid WHERE cp.user_id = ?""",
        (user_id,),
    )
    course_progress = [
        {"branch": r["branch"], "courseTitle": r["course_title"], "state": r["state"], "evalState": r["eval_state"]}
        for r in await cur2.fetchall()
    ]

    cur3 = await conn.execute(
        """SELECT cert.*, c.title AS course_title, c.branch AS branch FROM academy_certificates cert
           JOIN academy_courses c ON c.uuid = cert.course_uuid
           WHERE cert.user_id = ? ORDER BY c.branch ASC, c.order_index ASC, cert.issued_at ASC""",
        (user_id,),
    )
    certificates = [
        {"type": r["type"], "courseTitle": r["course_title"], "branch": r["branch"], "issuedAt": r["issued_at"]}
        for r in await cur3.fetchall()
    ]
    return {"enrollments": enrollments, "courseProgress": course_progress, "certificates": certificates}


async def pending_evaluations(conn: aiosqlite.Connection, branch: str | None = None) -> list[dict]:
    clause = "AND c.branch = ?" if branch else ""
    params = (branch,) if branch else ()
    cur = await conn.execute(
        f"""SELECT cp.*, c.title AS course_title, c.branch AS branch FROM academy_course_progress cp
            JOIN academy_courses c ON c.uuid = cp.course_uuid
            WHERE cp.eval_state IN ('available', 'pending') {clause}
            ORDER BY cp.updated_at ASC""",
        params,
    )
    return [
        {
            "userId": r["user_id"], "courseUuid": r["course_uuid"], "courseTitle": r["course_title"],
            "branch": r["branch"], "evalState": r["eval_state"],
        }
        for r in await cur.fetchall()
    ]


async def mark_ready_for_evaluation(conn: aiosqlite.Connection, user_id: str, branch: str) -> dict | None:
    """El instructor aprueba que un alumno ya dio la lección/clase y puede
    pasar a evaluación — busca el curso que el alumno tiene en progreso en
    esa rama ahora mismo y lo pone eval_state='available' (ahí lo recoge la
    Cola de Academia). Devuelve None si no hay ningún curso en progreso en
    esa rama para ese alumno."""
    cur = await conn.execute(
        """SELECT cp.* FROM academy_course_progress cp
           JOIN academy_courses c ON c.uuid = cp.course_uuid
           WHERE cp.user_id = ? AND c.branch = ? AND cp.state = 'in_progress'
           ORDER BY cp.updated_at DESC LIMIT 1""",
        (user_id, branch),
    )
    fila = await cur.fetchone()
    if not fila:
        return None
    await _upsert_course_progress(conn, user_id, fila["course_uuid"], eval_state="available")
    cur2 = await conn.execute("SELECT title FROM academy_courses WHERE uuid = ?", (fila["course_uuid"],))
    curso = await cur2.fetchone()
    return {"courseUuid": fila["course_uuid"], "courseTitle": curso["title"] if curso else None}


async def resolve_evaluation(conn: aiosqlite.Connection, user_id: str, course_uuid: str, approve: bool,
                              approver_id: str) -> dict | None:
    cur = await conn.execute(
        "SELECT * FROM academy_course_progress WHERE user_id = ? AND course_uuid = ?", (user_id, course_uuid)
    )
    progreso = await cur.fetchone()
    if not progreso or progreso["eval_state"] not in ("available", "pending"):
        return None
    if not approve:
        await _upsert_course_progress(conn, user_id, course_uuid, eval_state="available")
        return {"approved": False}

    await _upsert_course_progress(
        conn, user_id, course_uuid, state="completed", eval_state="approved",
        approved_by=approver_id, approved_at=_now_ms(),
    )
    certificado = await _grant_certificate_if_missing(conn, user_id, course_uuid, "final", instructor_id=approver_id)

    cur2 = await conn.execute("SELECT * FROM academy_courses WHERE uuid = ?", (course_uuid,))
    curso = await cur2.fetchone()
    if curso:
        cur3 = await conn.execute(
            "SELECT uuid FROM academy_courses WHERE branch = ? AND status = 'active' AND requires_course_uuid = ?",
            (curso["branch"], course_uuid),
        )
        siguiente = await cur3.fetchone()
        if siguiente:
            await _upsert_course_progress(conn, user_id, siguiente["uuid"], state="in_progress", eval_state="locked")
    return {
        "approved": True, "courseTitle": curso["title"] if curso else None,
        "branch": curso["branch"] if curso else None,
        "certificateCode": certificado["verify_code"] if certificado else None,
        "instructorId": approver_id,
    }


# Códigos de certificado — 6 caracteres alfanuméricos, A-Z y 2-9, excluyendo
# 0/1/I/O (se confunden entre sí o con letras parecidas). Generado al azar y
# comprobado contra la tabla hasta encontrar uno libre — un código nunca se
# reutiliza, ni siquiera si el certificado que lo tenía se revoca (queda en
# la tabla con status='REVOCADO' en vez de borrarse).
_CODIGO_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODIGO_LARGO = 6


async def _generar_codigo_unico(conn: aiosqlite.Connection) -> str:
    import secrets
    while True:
        codigo = "".join(secrets.choice(_CODIGO_ALFABETO) for _ in range(_CODIGO_LARGO))
        cur = await conn.execute("SELECT 1 FROM academy_certificates WHERE verify_code = ?", (codigo,))
        if not await cur.fetchone():
            return codigo


async def _grant_certificate_if_missing(conn: aiosqlite.Connection, user_id: str, course_uuid: str, tipo: str,
                                         instructor_id: str | None = None) -> dict | None:
    """Devuelve la fila del certificado (nueva o ya existente) — Bloque D2
    necesita el verify_code para generar la imagen, no solo saber si ya
    existía."""
    cur = await conn.execute(
        "SELECT * FROM academy_certificates WHERE user_id = ? AND course_uuid = ? AND type = ?",
        (user_id, course_uuid, tipo),
    )
    existente = await cur.fetchone()
    if existente:
        return dict(existente)
    import uuid as _uuid
    row_uuid = str(_uuid.uuid4())
    now = _now_ms()
    codigo = await _generar_codigo_unico(conn)
    await conn.execute(
        """INSERT INTO academy_certificates (uuid, user_id, course_uuid, type, issued_at, verify_code, instructor_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'VALIDO')""",
        (row_uuid, user_id, course_uuid, tipo, now, codigo, instructor_id),
    )
    await conn.commit()
    cur2 = await conn.execute("SELECT * FROM academy_certificates WHERE uuid = ?", (row_uuid,))
    return dict(await cur2.fetchone())


async def get_certificate_by_code(conn: aiosqlite.Connection, code: str) -> dict | None:
    cur = await conn.execute(
        """SELECT cert.*, c.title AS course_title, c.branch AS branch, c.code AS course_code
           FROM academy_certificates cert
           JOIN academy_courses c ON c.uuid = cert.course_uuid
           WHERE cert.verify_code = ?""",
        (code.strip().upper(),),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def revoke_certificate(conn: aiosqlite.Connection, code: str, revoked_by: str) -> dict | None:
    """Revoca un certificado por su código — el registro queda (nunca se
    borra), solo cambia de estado. Devuelve None si el código no existe o
    ya estaba revocado."""
    cert = await get_certificate_by_code(conn, code)
    if not cert or cert["status"] == "REVOCADO":
        return None
    await conn.execute(
        "UPDATE academy_certificates SET status = 'REVOCADO', revoked_by = ?, revoked_at = ? WHERE uuid = ?",
        (revoked_by, _now_ms(), cert["uuid"]),
    )
    await conn.commit()
    return await get_certificate_by_code(conn, code)


def compact_record(cert: dict) -> str:
    """Registro compacto tipo "7K4X9P|S1|V" — ID de código, código del curso
    (rango) y V/R según esté vigente o revocado."""
    estado = "V" if cert.get("status", "VALIDO") == "VALIDO" else "R"
    return f"{cert['verify_code']}|{cert.get('course_code') or '?'}|{estado}"


async def has_final_certificate(conn: aiosqlite.Connection, user_id: str, branch: str) -> bool:
    """Reemplaza a `_certificado_en_rama` (que antes le pegaba a la web) —
    usado para saber si alguien ya se recibió en una rama (ej. para /ascender)."""
    cur = await conn.execute(
        """SELECT 1 FROM academy_certificates cert JOIN academy_courses c ON c.uuid = cert.course_uuid
           WHERE cert.user_id = ? AND c.branch = ? AND cert.type = 'final' LIMIT 1""",
        (user_id, branch),
    )
    return (await cur.fetchone()) is not None
