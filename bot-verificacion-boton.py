#!/usr/bin/env python3
"""
Bot de verificación por botón — ATC24 Español

Publica un mensaje (Components V2) con un botón "Acepto y confirmo".
Cuando alguien lo presiona:
  1. Se le retira el rol NV | No Verificado
  2. Se le otorga el rol  V | Verificado
  3. Recibe una respuesta privada (solo él la ve) confirmando el cambio

Este botón SOLO confirma que la persona aceptó las reglas — no revisa
si ya completó la verificación de Roblox con Bloxlink. Si quieres que
también valide Bloxlink antes de dar el rol, dímelo y lo agrego.

REQUISITOS ANTES DE CORRERLO
-----------------------------
1. pip install discord.py aiohttp
2. En el Portal de Desarrolladores de Discord, tu bot debe tener el
   permiso "Manage Roles" (Gestionar roles) al invitarlo al servidor.
3. MUY IMPORTANTE: en Ajustes del servidor → Roles, el rol de tu BOT
   debe estar POR ENCIMA de los roles V y NV en la lista. Si el bot
   está más abajo que esos roles, Discord le va a negar el permiso
   para asignarlos, aunque tenga "Manage Roles" activado.
4. El token NO va escrito en este archivo — se lee de la variable de
   entorno DISCORD_BOT_TOKEN. Esto es a propósito: así puedes subir
   este código a GitHub sin exponer tu token en el repo.

   Antes de correr el script, defínela:

     Windows (cmd):        set DISCORD_BOT_TOKEN=tu_token_aqui
     Windows (PowerShell):  $env:DISCORD_BOT_TOKEN="tu_token_aqui"
     Linux/Mac/VPS:          export DISCORD_BOT_TOKEN="tu_token_aqui"

5. Asegúrate de que "verificacion-boton-components-v2.json" esté en
   la misma carpeta que este script.

CÓMO PUBLICAR EL MENSAJE
-------------------------
Corre el bot normalmente (python bot-verificacion-boton.py). Una vez
conectado, escribe en el canal donde quieres el botón:

    !publicar-verificacion

(solo funciona para quien tenga permiso de Administrador). El bot
publica el mensaje ahí y borra tu comando. Después de publicarlo una
vez, no hace falta volver a escribirlo — el bot debe seguir
corriendo 24/7 para poder reaccionar a los clics futuros.

HOSTING EN RENDER (GRATIS, SIN TARJETA)
-----------------------------------------
Este script además levanta un mini servidor web (puerto tomado de la
variable de entorno PORT, que Render define solo). Esto es SOLO para
que Render detecte que el servicio está "vivo" — Render exige que un
"Web Service" escuche en un puerto, si no, lo apaga. El servidor no
hace nada más que responder "OK" a quien lo visite.

Como el plan gratis de Render duerme el servicio tras 15 min sin
tráfico HTTP, hay que configurar un ping externo gratuito (UptimeRobot)
que visite esa URL cada 5 minutos para mantenerlo despierto. Ver
GUIA-hosting-render.md para el paso a paso completo.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os

import aiohttp
import discord
from discord import app_commands
from aiohttp import web

from welcome_card import generar_tarjeta_bienvenida

CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))  # Render inyecta PORT automáticamente

# Si está definida, los slash commands se sincronizan solo en este server
# (instantáneo). Sin ella, la sincronización es global y puede tardar ~1h
# en propagarse la primera vez.
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")

# Para que /ascender pueda consultar Academia en la web (repo ATC24Espanol,
# separado de este). BOT_SHARED_SECRET debe ser EXACTAMENTE igual al que
# está en el .env de la web (variable del mismo nombre).
WEB_API_BASE = os.environ.get("WEB_API_BASE", "https://atc24espanol.lat")
BOT_SHARED_SECRET = os.environ.get("BOT_SHARED_SECRET")

# API key de Bloxlink (dashboard de Bloxlink → tu servidor → API). Se usa
# para leer el nombre REAL de Roblox ya verificado y usarlo como base del
# apodo, en vez del username de Discord. Si no está configurada, el apodo
# sigue funcionando igual mostrando el nombre de Discord (no rompe nada).
BLOXLINK_API_KEY = os.environ.get("BLOXLINK_API_KEY")

V_ROLE_ID  = 1508568101770367156   # V  | Verificado
NV_ROLE_ID = 1532919695827665057   # NV | No Verificado

FLT_ROLE_ID = 1238796825381834760   # FLT | Piloto (rol base)
ATC_ROLE_ID = 1532224008555204669   # ATC | Controlador de Tráfico Aéreo (rol base)

LLEGADAS_CHANNEL_ID = 1238796825415389294

CUSTOM_ID = "atc24_verificar_aceptacion"
CUSTOM_ID_PILOTO = "atc24_bienvenida_piloto"
CUSTOM_ID_ATC = "atc24_bienvenida_atc"
ARCHIVO_MENSAJE = os.path.join(CARPETA_SCRIPT, "verificacion-boton-components-v2.json")
ARCHIVO_GUIA = os.path.join(CARPETA_SCRIPT, "guia-web-bot-components-v2.json")

# ─── Jerarquía de roles y prefijos (guía oficial de ATC24 Español) ────────
# Cada lista va del rango MÁS ALTO al más bajo dentro de su categoría.
# "Un solo prefijo por categoría": si un miembro tiene más de un rol de una
# misma lista, solo el primero (más alto) se usa en el apodo, y el resto se
# retira automáticamente (ver enforce_single_rank_per_category).

LIDERAZGO_ORDER = [
    1238796825415389288,  # CEO
    1238796825415389287,  # EXO
    1238796825402544150,  # DEV
    1238796825415389286,  # STF
    1238796825390092358,  # PM
]

# No son jerárquicos entre sí (un instructor puede tener varios a la vez);
# el orden acá solo desempata cuál se muestra si tiene más de uno.
INSTRUCTOR_ORDER = [
    1238796825402544154,  # CTI
    1238796825402544153,  # CFI
    1238796825402544152,  # GTI
]

ATC_ORDER = [
    1488656415232102460,  # C3
    1238796825390092356,  # C1
    1238796825390092355,  # S3
    1238796825390092354,  # S2
    1238796825390092353,  # S1
    1238796825390092351,  # ATO
]

PILOTO_ORDER = [
    1238796825381834762,  # PCA
    1238796825381834761,  # PPA
    1238796825381834759,  # APA
]

GC_ORDER = [
    1495560972759466124,  # ADT
    1238796825381834755,  # ETG
    1238796825381834754,  # EET
]

# Categorías con "un solo rango a la vez" — se les aplica auto-limpieza.
# Liderazgo NO va acá: sus roles se pueden combinar libremente (alguien puede
# ser DEV + STF + PM a la vez); LIDERAZGO_ORDER solo define cuál se muestra
# primero en el apodo cuando tiene varios, no cuáles puede tener asignados.
RANKED_CATEGORIES = {
    "ATC": ATC_ORDER,
    "PILOTO": PILOTO_ORDER,
    "GC": GC_ORDER,
}

PREFIX_LABELS = {
    1238796825415389288: "CEO",
    1238796825415389287: "EXO",
    1238796825402544150: "DEV",
    1238796825415389286: "STF",
    1238796825390092358: "PM",
    1238796825402544154: "CTI",
    1238796825402544153: "CFI",
    1238796825402544152: "GTI",
    1488656415232102460: "C3",
    1238796825390092356: "C1",
    1238796825390092355: "S3",
    1238796825390092354: "S2",
    1238796825390092353: "S1",
    1238796825390092351: "ATO",
    1238796825381834762: "PCA",
    1238796825381834761: "PPA",
    1238796825381834759: "APA",
    1495560972759466124: "ADT",
    1238796825381834755: "ETG",
    1238796825381834754: "EET",
}


def _highest(role_ids: set, order: list):
    for rid in order:
        if rid in role_ids:
            return rid
    return None


def _strip_prefix(nombre: str) -> str:
    """Si el nombre ya tiene un prefijo (`X | nombre` o `X / Y | nombre`),
    devuelve solo la parte real del nombre."""
    if " | " in nombre:
        return nombre.split(" | ", 1)[1]
    return nombre


_bloxlink_cache = {}  # discord_id -> (nombre_roblox, expira_en)
BLOXLINK_CACHE_TTL = 300  # 5 minutos — evita golpear la API de Bloxlink en /apodo-todos


async def _roblox_username(discord_id: int):
    """Nombre real de Roblox ya verificado en Bloxlink para este Discord ID,
    o None si no está configurado / no se pudo resolver (nunca rompe el
    cálculo del apodo — solo cae a usar el nombre de Discord)."""
    if not BLOXLINK_API_KEY or not GUILD_ID:
        return None
    ahora = asyncio.get_running_loop().time()
    cacheado = _bloxlink_cache.get(discord_id)
    if cacheado and cacheado[1] > ahora:
        return cacheado[0]
    url = f"https://api.blox.link/v4/public/guilds/{GUILD_ID}/discord-to-roblox/{discord_id}"
    headers = {"Authorization": BLOXLINK_API_KEY}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception as err:
        print(f"Aviso: no pude consultar Bloxlink para {discord_id}: {err}")
        return None
    resolved = (data or {}).get("resolved") or {}
    roblox = resolved.get("roblox") or {}
    nombre = roblox.get("name") or roblox.get("displayName")
    if nombre:
        _bloxlink_cache[discord_id] = (nombre, ahora + BLOXLINK_CACHE_TTL)
    return nombre


def build_nickname(member: discord.Member, base_name: str = None):
    """Calcula el apodo con prefijo según la guía de jerarquía. Devuelve
    None si el miembro no tiene ningún rol de instructor/liderazgo/rango
    operativo (no hay nada que anteponer). `base_name` permite pasar el
    nombre real de Roblox (Bloxlink) en vez de derivarlo del apodo actual de
    Discord — ver build_nickname_async()."""
    role_ids = {r.id for r in member.roles}

    instructor = _highest(role_ids, INSTRUCTOR_ORDER)
    liderazgo = _highest(role_ids, LIDERAZGO_ORDER)
    atc = _highest(role_ids, ATC_ORDER)
    piloto = _highest(role_ids, PILOTO_ORDER)
    gc = _highest(role_ids, GC_ORDER)

    slot1 = instructor or liderazgo
    operativas = [c for c in (atc, piloto, gc) if c]  # ya en orden ATC>Piloto>GC

    if slot1:
        slots = [slot1] + operativas[:1]
    else:
        slots = operativas[:2]

    if not slots:
        return None

    prefijo = " / ".join(PREFIX_LABELS[rid] for rid in slots)
    nombre_base = base_name if base_name is not None else _strip_prefix(member.display_name)
    return f"{prefijo} | {nombre_base}"


async def build_nickname_async(member: discord.Member):
    """Versión que prioriza el nombre real de Roblox (Bloxlink) como base
    del apodo, con fallback automático al nombre de Discord."""
    roblox_name = await _roblox_username(member.id)
    base = roblox_name if roblox_name else _strip_prefix(member.display_name)
    return build_nickname(member, base_name=base)


async def enforce_base_tags(member: discord.Member) -> bool:
    """El rol base ATC (Controlador de Tráfico Aéreo) y FLT (Piloto) sigue a
    la rama: si tiene cualquier rango de esa rama (desde estudiante ATO/APA
    en adelante), debe tener el rol base sí o sí; si no tiene ningún rango
    de esa rama, no debe tenerlo. Devuelve True si cambió algo."""
    role_ids = {r.id for r in member.roles}

    tiene_rango_atc = any(rid in role_ids for rid in ATC_ORDER)
    tiene_rango_piloto = any(rid in role_ids for rid in PILOTO_ORDER)

    a_agregar = []
    a_quitar = []

    if tiene_rango_atc and ATC_ROLE_ID not in role_ids:
        a_agregar.append(ATC_ROLE_ID)
    elif not tiene_rango_atc and ATC_ROLE_ID in role_ids:
        a_quitar.append(ATC_ROLE_ID)

    if tiene_rango_piloto and FLT_ROLE_ID not in role_ids:
        a_agregar.append(FLT_ROLE_ID)
    elif not tiene_rango_piloto and FLT_ROLE_ID in role_ids:
        a_quitar.append(FLT_ROLE_ID)

    if not a_agregar and not a_quitar:
        return False

    if a_agregar:
        roles_obj = [r for r in (member.guild.get_role(rid) for rid in a_agregar) if r is not None]
        if roles_obj:
            await member.add_roles(*roles_obj, reason="Rol base sigue al rating (ATC/FLT)")
    if a_quitar:
        roles_obj = [r for r in (member.guild.get_role(rid) for rid in a_quitar) if r is not None]
        if roles_obj:
            await member.remove_roles(*roles_obj, reason="Sin rating en esa rama — se retira el rol base")
    return True


async def enforce_single_rank_per_category(member: discord.Member) -> bool:
    """Si el miembro tiene más de un rango dentro de la misma categoría
    (ej. S1 y S2 a la vez), retira todos menos el más alto. Devuelve True
    si removió algo."""
    role_ids = {r.id for r in member.roles}
    a_remover = []
    for orden in RANKED_CATEGORIES.values():
        presentes = [rid for rid in orden if rid in role_ids]
        if len(presentes) > 1:
            a_remover.extend(presentes[1:])  # se queda con presentes[0], el más alto

    if not a_remover:
        return False

    roles_obj = [member.guild.get_role(rid) for rid in a_remover]
    roles_obj = [r for r in roles_obj if r is not None]
    if roles_obj:
        await member.remove_roles(*roles_obj, reason="Un solo prefijo por categoría — se conserva el rango más alto")
    return True


def has_any_role(member: discord.Member, role_ids: list) -> bool:
    ids = {r.id for r in member.roles}
    return any(rid in ids for rid in role_ids)


def _categoria_de_rol(role_id: int):
    if role_id in LIDERAZGO_ORDER:
        return "LIDERAZGO"
    if role_id in INSTRUCTOR_ORDER:
        return "INSTRUCTOR"
    if role_id in ATC_ORDER:
        return "ATC"
    if role_id in PILOTO_ORDER:
        return "PILOTO"
    if role_id in GC_ORDER:
        return "GC"
    return None


def _puede_ascender(invocador: discord.Member, categoria: str) -> bool:
    """Liderazgo puede otorgar cualquier categoría. Cada instructor solo
    puede otorgar rangos de SU rama (CTI→ATC, CFI→Piloto, GTI→Equipo de
    Tierra). Instructor/Liderazgo como categoría de DESTINO solo lo puede
    otorgar Liderazgo."""
    if has_any_role(invocador, LIDERAZGO_ORDER):
        return True
    if categoria == "ATC":
        return INSTRUCTOR_ORDER[0] in {r.id for r in invocador.roles}  # CTI
    if categoria == "PILOTO":
        return INSTRUCTOR_ORDER[1] in {r.id for r in invocador.roles}  # CFI
    if categoria == "GC":
        return INSTRUCTOR_ORDER[2] in {r.id for r in invocador.roles}  # GTI
    return False


async def _consulta_web(ruta: str, params: dict = None) -> dict:
    """GET genérico contra la API de la web, autenticado con BOT_SHARED_SECRET.
    Lanza RuntimeError con un mensaje legible si algo sale mal."""
    if not BOT_SHARED_SECRET:
        raise RuntimeError("BOT_SHARED_SECRET no está configurado en el bot")
    url = f"{WEB_API_BASE}{ruta}"
    headers = {"x-bot-secret": BOT_SHARED_SECRET}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params or {}) as resp:
            if resp.status != 200:
                detalle = await resp.text()
                raise RuntimeError(f"la web respondió {resp.status}: {detalle}")
            return await resp.json()


BRANCH_LABEL = {"atc": "🛫 ATC", "pilot": "✈️ Piloto"}
BRANCH_ORDER = ["atc", "pilot"]

COURSE_STATE_LABEL = {
    "locked": "🔒 Bloqueado",
    "in_progress": "📘 En progreso",
    "theory_done": "📗 Teoría completada",
    "completed": "✅ Completado",
}
EVAL_STATE_LABEL = {
    "locked": "Evaluación bloqueada",
    "available": "Evaluación disponible",
    "pending": "⏳ Evaluación en revisión",
    "approved": "✅ Evaluación aprobada",
    "rejected": "❌ Evaluación rechazada",
}
CERT_TYPE_LABEL = {"final": "🏅 Certificado final", "theory": "📄 Certificado de teoría"}
CERT_TYPE_ORDEN = {"final": 0, "theory": 1}


def _agrupar_por_rama(items: list, campo_rama: str = "branch") -> dict:
    grupos = {b: [] for b in BRANCH_ORDER}
    for it in items:
        grupos.setdefault(it.get(campo_rama), []).append(it)
    return grupos


async def _certificado_en_rama(discord_id: str, branch: str) -> bool:
    """Consulta la web (repo ATC24Espanol) para saber si el usuario tiene
    certificado (teoría + examen final) en esa rama de Academia."""
    data = await _consulta_web(f"/api/bot/academy-status/{discord_id}", {"branch": branch})
    return bool(data.get("certified"))


# Todas las categorías ascendibles por comando (Liderazgo/Instructor incluidos,
# para que el staff tenga un solo comando en vez de asignar roles a mano).
RANGO_CHOICES = [
    app_commands.Choice(name=PREFIX_LABELS[rid], value=str(rid))
    for rid in (LIDERAZGO_ORDER + INSTRUCTOR_ORDER + ATC_ORDER + PILOTO_ORDER + GC_ORDER)
]


async def _target_nickname(member: discord.Member):
    """Nombre final que debería tener el miembro: con prefijo si le
    corresponde, o su nombre limpio (sin prefijo) si no tiene ningún rol
    que lo amerite. Ya usa el nombre real de Roblox (Bloxlink) si está
    disponible."""
    nuevo = await build_nickname_async(member)
    if nuevo is not None:
        return nuevo
    roblox_name = await _roblox_username(member.id)
    return roblox_name if roblox_name else _strip_prefix(member.display_name)


async def actualizar_apodo(member: discord.Member):
    """Sigue el flujo pedido: BORRAR apodo actual -> CALCULAR el nuevo ->
    CAMBIAR. Si el miembro ya tiene exactamente el apodo que le corresponde,
    no hace ninguna llamada a la API (evita rate-limit innecesario en
    /apodo-todos). Devuelve (nombre_final, hubo_cambio)."""
    objetivo = await _target_nickname(member)
    if objetivo == member.display_name:
        return objetivo, False

    if member.nick is not None:
        await member.edit(nick=None, reason="Actualización de apodo — paso 1/2: borrar")

    nuevo = await build_nickname_async(member)
    if nuevo:
        await member.edit(nick=nuevo, reason="Actualización de apodo — paso 2/2: cambiar")

    return objetivo, True


async def _asignar_rol_bienvenida(interaction: discord.Interaction, nombre_rama: str, ruta_academia: str):
    """Elegir rama en el mensaje de bienvenida verifica al usuario (V, se
    saca NV) y lo manda a inscribirse en Academia — NO le da FLT ni ATC acá:
    esos roles base solo se otorgan cuando tiene un rating real (ver
    enforce_base_tags), y el rol de estudiante (ATO/APA) lo otorga la propia
    web al completar la inscripción en Academia."""
    # El botón se manda por DM, así que interaction.guild siempre es None acá
    # (los DM no tienen servidor asociado) — hay que buscar el servidor real
    # por ID en vez de asumir que la interacción trae uno.
    guild = client.get_guild(int(GUILD_ID)) if GUILD_ID else None
    if guild is None:
        await interaction.response.send_message(
            "No pude identificar el servidor de ATC24 Español. Avisa al staff.", ephemeral=True,
        )
        print("ERROR: GUILD_ID no está configurado o el bot no está en ese servidor.")
        return

    member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)

    v_role = guild.get_role(V_ROLE_ID)
    nv_role = guild.get_role(NV_ROLE_ID)
    if v_role is None or nv_role is None:
        await interaction.response.send_message("Hubo un problema encontrando los roles V/NV. Avisa al staff.", ephemeral=True)
        print("ERROR: no se encontraron V_ROLE_ID/NV_ROLE_ID para el botón de bienvenida.")
        return

    ya_verificado = v_role in member.roles
    try:
        if not ya_verificado:
            await member.add_roles(v_role, reason="Eligió su rama en el mensaje de bienvenida")
        if nv_role in member.roles:
            await member.remove_roles(nv_role, reason="Eligió su rama en el mensaje de bienvenida")
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude verificarte — el bot no tiene permisos suficientes. Avisa al staff.",
            ephemeral=True,
        )
        return

    saludo = "¡Verificación confirmada! 🎉" if not ya_verificado else "Ya estabas verificado, todo en orden. ✈️"
    mensaje = (
        f"{saludo}\n"
        f"Ahora entra a https://atc24espanol.lat/{ruta_academia} para inscribirte en Academia — {nombre_rama} — "
        "y empezar tu formación real (examen de admisión, lecciones, evaluación final)."
    )

    # El botón ya vive dentro del DM, así que respondemos directo a la
    # interacción con el mensaje real (no hace falta mandar un DM aparte).
    await interaction.response.send_message(mensaje)


class BienvenidaView(discord.ui.View):
    """Vista persistente (timeout=None) con los botones de elección de rama.
    Se registra en setup_hook vía client.add_view() para seguir funcionando
    después de reiniciar el bot, sin depender de que el mensaje original
    siga "vivo" en memoria."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✈️ Quiero ser Piloto", style=discord.ButtonStyle.primary, custom_id=CUSTOM_ID_PILOTO)
    async def piloto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _asignar_rol_bienvenida(interaction, "Piloto", "academia/pilotos")

    @discord.ui.button(label="🎙️ Quiero ser ATC", style=discord.ButtonStyle.primary, custom_id=CUSTOM_ID_ATC)
    async def atc_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _asignar_rol_bienvenida(interaction, "Controlador de Tráfico Aéreo", "academia/atc")

# ─────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # necesario para poder modificar roles de miembros

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


async def _ping(_request):
    """Endpoint que UptimeRobot visita para mantener el servicio despierto."""
    return web.Response(text="ATC24 Español — bot de verificación activo.")


async def iniciar_servidor_web():
    """Levanta el mini servidor HTTP que Render necesita para no apagar el servicio."""
    app = web.Application()
    app.router.add_get("/", _ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Mini servidor web escuchando en el puerto {PORT} (para Render/UptimeRobot).")


_presencia_iniciada = False  # evita arrancar el loop dos veces si on_ready se dispara de nuevo (reconexión)
_BOT_START_TIME = datetime.datetime.now(datetime.timezone.utc)  # fijo — no se reinicia en cada refresco


async def _actualizar_presencia_loop():
    while True:
        try:
            data = await _consulta_web("/api/bot/live-counts")
            texto = f"{data.get('activeFlights', 0)} vuelos · {data.get('activeControllers', 0)} controladores"
            # start=_BOT_START_TIME hace que Discord muestre "hace X tiempo" y lo
            # vaya actualizando solo en el cliente de cada usuario — no hace
            # falta que nosotros recalculemos ningún texto de tiempo a mano.
            actividad = discord.Activity(type=discord.ActivityType.watching, name=texto, start=_BOT_START_TIME)
            await client.change_presence(activity=actividad)
        except Exception as err:
            print(f"Aviso: no pude actualizar el rich presence: {err}")
        await asyncio.sleep(120)  # cada 2 minutos — suficiente para que se sienta "en vivo" sin saturar la web


@client.event
async def on_ready():
    print(f"Conectado como {client.user} (id {client.user.id})")
    print("Esperando el comando !publicar-verificacion en algún canal…")
    print("El bot debe seguir corriendo para poder reaccionar a los clics del botón.\n")

    global _presencia_iniciada
    if not _presencia_iniciada:
        _presencia_iniciada = True
        client.loop.create_task(_actualizar_presencia_loop())


@client.event
async def setup_hook():
    # Se ejecuta antes de conectar a Discord — arrancamos el mini servidor
    # web aquí para que Render vea el puerto abierto cuanto antes.
    await iniciar_servidor_web()

    client.add_view(BienvenidaView())  # persistente: sobrevive a reinicios del bot

    if GUILD_ID:
        guild_obj = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild_obj)
        await tree.sync(guild=guild_obj)
        print(f"Slash commands sincronizados al servidor {GUILD_ID} (instantáneo).")
    else:
        await tree.sync()
        print("Slash commands sincronizados globalmente (puede tardar ~1h en propagarse).")


@client.event
async def on_member_join(member: discord.Member):
    # Se une por primera vez o vuelve a unirse tras haberse ido — en ambos
    # casos Discord lo deja sin roles, así que hay que darle NV explícito.
    nv_role = member.guild.get_role(NV_ROLE_ID)
    if nv_role is not None:
        try:
            await member.add_roles(nv_role, reason="Ingreso al servidor — pendiente de verificación")
        except discord.Forbidden:
            print(f"ERROR 403: no pude darle NV a {member} al unirse — revisa la jerarquía del bot.")
    else:
        print("ERROR: no se encontró el rol NV_ROLE_ID en este servidor.")

    if LLEGADAS_CHANNEL_ID is not None:
        canal = member.guild.get_channel(LLEGADAS_CHANNEL_ID)
        if canal is None:
            print(f"ERROR: no encontré el canal de llegadas {LLEGADAS_CHANNEL_ID}")
        else:
            saludo = f"¡Hola {member.mention}! 👋\n¡Te damos la bienvenida a **ATC24 Español**! 🛫"
            texto_bienvenida = (
                f"Bienvenido a **ATC24 Español**, {member.mention}. Gracias por unirte a nuestra comunidad.\n\n"
                "Para comenzar, te invitamos a leer nuestros documentos y reglamento, y a completar tu "
                "verificación en el canal correspondiente.\n\n"
                "Esperamos que disfrutes tu estadía y aproveches al máximo la experiencia dentro del servidor."
            )
            try:
                tarjeta = await generar_tarjeta_bienvenida(member)
                archivo = discord.File(tarjeta, filename="bienvenida.png")
                embed = discord.Embed(description=saludo, color=discord.Color.blue())
                embed.set_image(url="attachment://bienvenida.png")
                await canal.send(embed=embed, file=archivo)
            except FileNotFoundError as err:
                print(f"Aviso: {err} — mando la bienvenida sin tarjeta por ahora.")
                embed = discord.Embed(description=saludo, color=discord.Color.blue())
                if member.guild.icon:
                    embed.set_thumbnail(url=member.guild.icon.url)
                await canal.send(embed=embed)
            await canal.send(texto_bienvenida)

    # El DM para elegir rama se manda recién cuando se verifica de verdad
    # (aprieta "Acepto y confirmo" en el mensaje de !publicar-verificacion),
    # no acá — ver on_interaction más abajo.


async def _mandar_eleccion_de_rama_por_dm(member: discord.Member):
    embed_dm = discord.Embed(
        title="Elige tu rama en ATC24 Español",
        description=(
            "Cuando quieras, elige por dónde comenzar tu camino en la red. "
            "Esto te verifica en el servidor y te envía el enlace para inscribirte en Academia."
        ),
        color=discord.Color.blue(),
    )
    try:
        await member.send(embed=embed_dm, view=BienvenidaView())
    except discord.Forbidden:
        if LLEGADAS_CHANNEL_ID is not None:
            canal = member.guild.get_channel(LLEGADAS_CHANNEL_ID)
            if canal is not None:
                await canal.send(
                    f"{member.mention} no fue posible enviarte un mensaje directo — abre tus mensajes directos "
                    "a miembros del servidor e inténtalo de nuevo, o pide ayuda a un miembro del Staff para elegir tu rama.",
                )


@client.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles:
        return
    try:
        await enforce_single_rank_per_category(after)
        await enforce_base_tags(after)
        await actualizar_apodo(after)  # sigue el flujo borrar->calcular->cambiar
    except discord.Forbidden:
        print(f"No pude actualizar roles/apodo de {after} — jerarquía del bot insuficiente o es el dueño del servidor.")
    except discord.HTTPException as err:
        print(f"Error de Discord actualizando a {after}: {err}")


@tree.command(name="ascender", description="[Instructor/Liderazgo] Otorga un rango a un usuario")
@app_commands.describe(miembro="Usuario a ascender", rango="Rango a otorgar")
@app_commands.choices(rango=RANGO_CHOICES)
async def ascender(interaction: discord.Interaction, miembro: discord.Member, rango: app_commands.Choice[str]):
    role_id = int(rango.value)
    categoria = _categoria_de_rol(role_id)
    if categoria is None:
        await interaction.response.send_message("Rango desconocido.", ephemeral=True)
        return

    if not _puede_ascender(interaction.user, categoria):
        await interaction.response.send_message(
            "No tienes permiso para otorgar ese rango (necesitas ser Instructor de esa rama o Liderazgo).",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)  # la consulta a la web puede tardar más de 3s

    if categoria in ("ATC", "PILOTO"):
        branch = "atc" if categoria == "ATC" else "pilot"
        try:
            certificado = await _certificado_en_rama(str(miembro.id), branch)
        except Exception as err:
            await interaction.followup.send(f"No pude verificar Academia en la web: {err}", ephemeral=True)
            print(f"ERROR /ascender consultando la web: {err}")
            return
        if not certificado:
            await interaction.followup.send(
                f"{miembro.mention} todavía no tiene el certificado de **{branch.upper()}** en Academia "
                "(teoría + examen final) — no se puede ascender hasta que lo complete.",
                ephemeral=True,
            )
            return

    rol = interaction.guild.get_role(role_id)
    if rol is None:
        await interaction.followup.send("No encontré ese rol en el servidor — avisa al staff.", ephemeral=True)
        return
    try:
        await miembro.add_roles(rol, reason=f"Ascenso otorgado por {interaction.user} (/ascender)")
    except discord.Forbidden:
        await interaction.followup.send(
            "No pude asignar el rol — la jerarquía de roles del bot es insuficiente.",
            ephemeral=True,
        )
        return

    # No hace falta llamar enforce_single_rank_per_category/actualizar_apodo acá:
    # el add_roles de arriba dispara on_member_update, que ya se encarga.
    await interaction.followup.send(
        f"Listo — {miembro.mention} ahora tiene **{PREFIX_LABELS.get(role_id, rol.name)}**.",
        ephemeral=True,
    )


@tree.command(name="apodo", description="Actualiza tu propio apodo según tus roles actuales")
async def apodo(interaction: discord.Interaction):
    member = interaction.user
    try:
        nuevo, cambio = await actualizar_apodo(member)
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiarte el apodo — el bot no tiene permisos suficientes sobre tu rol más alto, "
            "o eres el dueño del servidor (Discord no permite cambiarle el apodo al dueño).",
            ephemeral=True,
        )
        return

    if not nuevo:
        await interaction.response.send_message(
            "No tienes ningún rol operativo, de instructor o liderazgo — te dejé sin prefijo.",
            ephemeral=True,
        )
    elif cambio:
        await interaction.response.send_message(f"Listo, tu apodo ahora es **{nuevo}**.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Tu apodo ya estaba correcto: **{nuevo}**.", ephemeral=True)


@tree.command(name="apodo-miembro", description="[Liderazgo/Staff] Actualiza el apodo de otro usuario según su jerarquía de roles")
@app_commands.describe(miembro="Usuario a actualizar")
async def apodo_miembro(interaction: discord.Interaction, miembro: discord.Member):
    if not has_any_role(interaction.user, LIDERAZGO_ORDER):
        await interaction.response.send_message("Este comando es solo para Liderazgo/Staff.", ephemeral=True)
        return

    try:
        nuevo, cambio = await actualizar_apodo(miembro)
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiar ese apodo — jerarquía de roles del bot insuficiente, o es el dueño del servidor.",
            ephemeral=True,
        )
        return

    if not nuevo:
        await interaction.response.send_message(
            f"{miembro.mention} no tiene ningún rol operativo, de instructor o liderazgo — quedó sin prefijo.",
            ephemeral=True,
        )
    elif cambio:
        await interaction.response.send_message(f"Apodo de {miembro.mention} actualizado a **{nuevo}**.", ephemeral=True)
    else:
        await interaction.response.send_message(f"El apodo de {miembro.mention} ya estaba correcto: **{nuevo}**.", ephemeral=True)


@tree.command(name="apodo-todos", description="[Solo dueño del server] Recalcula el apodo de todos los miembros")
async def apodo_todos(interaction: discord.Interaction):
    if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Este comando es solo para el dueño del servidor.", ephemeral=True)
        return

    await interaction.response.send_message("Actualizando apodos de todo el servidor, esto puede tardar…", ephemeral=True)

    actualizados = 0
    fallidos = 0
    async for member in interaction.guild.fetch_members(limit=None):
        try:
            _, cambio = await actualizar_apodo(member)  # borrar -> calcular -> cambiar; no-op si ya está bien
        except (discord.Forbidden, discord.HTTPException):
            fallidos += 1
            continue
        if cambio:
            actualizados += 1
            await asyncio.sleep(0.5)  # solo pausamos cuando de verdad hubo llamadas a la API

    await interaction.followup.send(
        f"Listo. Apodos actualizados: {actualizados}. No se pudieron cambiar: {fallidos} "
        "(probablemente su rol más alto está por encima del bot, o son el dueño del servidor).",
        ephemeral=True,
    )


@tree.command(name="apodo-borrartodos", description="[Solo dueño del server] Borra el apodo de TODOS los miembros (vuelven a su nombre de usuario)")
async def apodo_borrar_todos(interaction: discord.Interaction):
    if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Este comando es solo para el dueño del servidor.", ephemeral=True)
        return

    await interaction.response.send_message("Borrando el apodo de todo el servidor, esto puede tardar…", ephemeral=True)

    borrados = 0
    fallidos = 0
    async for member in interaction.guild.fetch_members(limit=None):
        if member.nick is None:
            continue  # ya no tiene apodo, nada que hacer
        try:
            await member.edit(nick=None, reason=f"Borrado masivo de apodos por {interaction.user}")
            borrados += 1
        except (discord.Forbidden, discord.HTTPException):
            fallidos += 1
            continue
        await asyncio.sleep(0.5)  # evitar rate limit de Discord

    await interaction.followup.send(
        f"Listo. Apodos borrados: {borrados}. No se pudieron borrar: {fallidos} "
        "(probablemente su rol más alto está por encima del bot, o son el dueño del servidor).",
        ephemeral=True,
    )


@tree.command(name="progreso", description="Muestra tu progreso en Academia (cursos, evaluaciones, certificados)")
async def progreso(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        data = await _consulta_web(f"/api/bot/user-progress/{interaction.user.id}")
    except Exception as err:
        await interaction.followup.send(f"No pude consultar tu progreso: {err}", ephemeral=True)
        return

    embed = discord.Embed(title="📚 Tu progreso en Academia", color=discord.Color.blurple())

    if not data.get("enrollments"):
        embed.description = "Todavía no te inscribiste en ninguna rama de Academia. Elige tu rama desde el mensaje que recibiste por mensaje directo al verificarte."
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    cursos_por_rama = _agrupar_por_rama(data.get("courseProgress", []))
    for rama in BRANCH_ORDER:
        cursos = cursos_por_rama.get(rama, [])
        if not cursos:
            continue
        lineas = []
        for curso in cursos:
            estado = COURSE_STATE_LABEL.get(curso["state"], curso["state"])
            eval_estado = EVAL_STATE_LABEL.get(curso.get("evalState"), None)
            linea = f"**{curso['courseTitle']}** — {estado}"
            if eval_estado:
                linea += f"\n{eval_estado}"
            lineas.append(linea)
        embed.add_field(name=BRANCH_LABEL[rama], value="\n\n".join(lineas), inline=False)

    certs = sorted(data.get("certificates", []), key=lambda c: CERT_TYPE_ORDEN.get(c["type"], 9))
    certs_por_rama = _agrupar_por_rama(certs)
    for rama in BRANCH_ORDER:
        items = certs_por_rama.get(rama, [])
        if not items:
            continue
        texto = "\n".join(f"{CERT_TYPE_LABEL.get(c['type'], c['type'])} — {c['courseTitle']}" for c in items)
        embed.add_field(name=f"{BRANCH_LABEL[rama]} · Certificados", value=texto, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="certificado", description="Muestra los certificados de un usuario en Academia")
@app_commands.describe(miembro="Usuario a consultar (si se deja vacío, se muestra el tuyo)")
async def certificado(interaction: discord.Interaction, miembro: discord.Member = None):
    objetivo = miembro or interaction.user
    await interaction.response.defer()
    try:
        data = await _consulta_web(f"/api/bot/certificates/{objetivo.id}")
    except Exception as err:
        await interaction.followup.send(f"No pude consultar los certificados: {err}", ephemeral=True)
        return

    items = data.get("items", [])
    if not items:
        await interaction.followup.send(f"{objetivo.mention} todavía no tiene certificados en Academia.")
        return

    items = sorted(items, key=lambda c: CERT_TYPE_ORDEN.get(c["type"], 9))
    por_rama = _agrupar_por_rama(items)

    embed = discord.Embed(title=f"🎓 Certificados de {objetivo.display_name}", color=discord.Color.gold())
    for rama in BRANCH_ORDER:
        certs = por_rama.get(rama, [])
        if not certs:
            continue
        texto = "\n".join(f"{CERT_TYPE_LABEL.get(c['type'], c['type'])} — {c['courseTitle']} ({c['issuedAt']})" for c in certs)
        embed.add_field(name=BRANCH_LABEL[rama], value=texto, inline=False)

    await interaction.followup.send(embed=embed)


@tree.command(name="cola", description="[Instructor/Liderazgo] Muestra las evaluaciones pendientes de revisar")
@app_commands.describe(rama="Filtrar por rama (dejalo vacío para ver ambas)")
@app_commands.choices(rama=[
    app_commands.Choice(name="ATC", value="atc"),
    app_commands.Choice(name="Piloto", value="pilot"),
])
async def cola(interaction: discord.Interaction, rama: app_commands.Choice[str] = None):
    es_instructor = has_any_role(interaction.user, LIDERAZGO_ORDER + INSTRUCTOR_ORDER)
    if not es_instructor:
        await interaction.response.send_message("Este comando es solo para Instructores/Liderazgo.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        data = await _consulta_web("/api/bot/pending-evaluations", {"branch": rama.value} if rama else None)
    except Exception as err:
        await interaction.followup.send(f"No pude consultar la cola: {err}", ephemeral=True)
        return

    items = data.get("items", [])
    if not items:
        await interaction.followup.send("No hay evaluaciones pendientes ahora mismo. 🎉", ephemeral=True)
        return

    por_rama = _agrupar_por_rama(items)
    embed = discord.Embed(title="📋 Evaluaciones pendientes de revisar", color=discord.Color.orange())
    for r in BRANCH_ORDER:
        pendientes = por_rama.get(r, [])
        if not pendientes:
            continue
        texto = "\n".join(
            f"• **{it['username']}** — {it['courseTitle']} ({EVAL_STATE_LABEL.get(it['evalState'], it['evalState'])})"
            for it in pendientes
        )
        embed.add_field(name=BRANCH_LABEL[r], value=texto, inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="eco", description="[Liderazgo/Staff] Manda un mensaje formal como el bot (a un canal o por MD)")
@app_commands.describe(
    mensaje="Texto a enviar",
    canal="Canal donde publicarlo (dejalo vacío si vas a mandarlo por MD)",
    usuario="Usuario a quien mandárselo por MD (dejalo vacío si vas a publicarlo en un canal)",
)
async def eco(
    interaction: discord.Interaction,
    mensaje: str,
    canal: discord.TextChannel = None,
    usuario: discord.Member = None,
):
    if not has_any_role(interaction.user, LIDERAZGO_ORDER + INSTRUCTOR_ORDER):
        await interaction.response.send_message("Este comando es solo para Instructores/Liderazgo.", ephemeral=True)
        return
    if bool(canal) == bool(usuario):  # ninguno de los dos, o los dos a la vez
        await interaction.response.send_message("Elegí exactamente uno: un canal O un usuario, no ambos ni ninguno.", ephemeral=True)
        return

    destino = canal or usuario
    try:
        await destino.send(mensaje)
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude mandar el mensaje ahí — el bot no tiene permisos, o el usuario tiene los MD cerrados.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(f"Mensaje enviado a {destino.mention}. ✅", ephemeral=True)
    print(f"{interaction.user} usó /eco hacia {'#' + canal.name if canal else usuario} : {mensaje[:80]}")


async def _publicar_payload_crudo(channel_id: int, payload: dict):
    """Publica un mensaje Components V2 llamando directamente a la API REST
    de Discord (en vez de usar client.http.send_message, cuya firma interna
    cambia entre versiones de discord.py sin previo aviso)."""
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status >= 300:
                detalle = await resp.text()
                raise RuntimeError(f"Discord respondió {resp.status}: {detalle}")


COMANDOS_PUBLICAR = {
    "!publicar-verificacion": ARCHIVO_MENSAJE,
    "!publicar-guia": ARCHIVO_GUIA,
}


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    comando = message.content.strip()
    archivo = COMANDOS_PUBLICAR.get(comando)
    if archivo is None:
        return
    if not message.author.guild_permissions.administrator:
        await message.reply("Solo un administrador puede publicar este mensaje.", delete_after=8)
        return

    with open(archivo, encoding="utf-8") as f:
        payload = json.load(f)

    try:
        await _publicar_payload_crudo(message.channel.id, payload)
    except Exception as err:
        await message.reply(f"No pude publicar el mensaje: {err}", delete_after=15)
        print(f"ERROR al publicar mensaje ({comando}): {err}")
        return

    await message.delete()
    print(f"Mensaje publicado ({comando}) en #{message.channel.name}")


@client.event
async def on_interaction(interaction: discord.Interaction):
    # Solo nos interesan los clics de botón con nuestro custom_id
    if interaction.type != discord.InteractionType.component:
        return
    if interaction.data.get("custom_id") != CUSTOM_ID:
        return

    guild = interaction.guild
    member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)

    v_role = guild.get_role(V_ROLE_ID)
    nv_role = guild.get_role(NV_ROLE_ID)

    if v_role is None or nv_role is None:
        await interaction.response.send_message(
            "Hubo un problema encontrando los roles V o NV. Avisa al staff.",
            ephemeral=True,
        )
        print("ERROR: no se encontraron los roles V_ROLE_ID / NV_ROLE_ID en este servidor.")
        return

    try:
        if nv_role in member.roles:
            await member.remove_roles(nv_role, reason="Confirmó aceptación de reglas")
        if v_role not in member.roles:
            await member.add_roles(v_role, reason="Confirmó aceptación de reglas")
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiar tus roles — el bot no tiene permisos suficientes. "
            "Avisa al staff para que revisen la posición del rol del bot.",
            ephemeral=True,
        )
        print("ERROR 403: el rol del bot está por debajo de V/NV en la jerarquía, o falta el permiso Manage Roles.")
        return

    await interaction.response.send_message(
        "✅ Verificación confirmada. Ya tienes el rol **Verificado** y acceso completo a la comunidad. ¡Bienvenido a bordo!",
        ephemeral=True,
    )
    print(f"{member} confirmó verificación por botón.")

    # Recién ahora, ya verificado, le mandamos el DM para elegir su rama.
    await _mandar_eleccion_de_rama_por_dm(member)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: no encontré la variable de entorno DISCORD_BOT_TOKEN.")
        print("Defínela antes de correr este script (ver instrucciones arriba).")
    else:
        client.run(BOT_TOKEN)
