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
import json
import os

import aiohttp
import discord
from discord import app_commands
from aiohttp import web

CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))  # Render inyecta PORT automáticamente

# Si está definida, los slash commands se sincronizan solo en este server
# (instantáneo). Sin ella, la sincronización es global y puede tardar ~1h
# en propagarse la primera vez.
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")

V_ROLE_ID  = 1508568101770367156   # V  | Verificado
NV_ROLE_ID = 1532919695827665057   # NV | No Verificado

CUSTOM_ID = "atc24_verificar_aceptacion"
ARCHIVO_MENSAJE = os.path.join(CARPETA_SCRIPT, "verificacion-boton-components-v2.json")

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
RANKED_CATEGORIES = {
    "LIDERAZGO": LIDERAZGO_ORDER,
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


def build_nickname(member: discord.Member):
    """Calcula el apodo con prefijo según la guía de jerarquía. Devuelve
    None si el miembro no tiene ningún rol de instructor/liderazgo/rango
    operativo (no hay nada que anteponer)."""
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
    base_name = _strip_prefix(member.display_name)
    return f"{prefijo} | {base_name}"


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


async def actualizar_apodo(member: discord.Member) -> str | None:
    """Recalcula y aplica el apodo de un miembro. Devuelve el nuevo apodo
    aplicado, o None si no había nada que asignar."""
    nuevo = build_nickname(member)
    if not nuevo or nuevo == member.display_name:
        return nuevo
    await member.edit(nick=nuevo, reason="Actualización automática de apodo por jerarquía de roles")
    return nuevo

# ─────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # necesario para poder modificar roles de miembros

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
apodo_group = app_commands.Group(name="apodo", description="Gestión de apodos según la jerarquía de roles de ATC24 Español")


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


@client.event
async def on_ready():
    print(f"Conectado como {client.user} (id {client.user.id})")
    print("Esperando el comando !publicar-verificacion en algún canal…")
    print("El bot debe seguir corriendo para poder reaccionar a los clics del botón.\n")


@client.event
async def setup_hook():
    # Se ejecuta antes de conectar a Discord — arrancamos el mini servidor
    # web aquí para que Render vea el puerto abierto cuanto antes.
    await iniciar_servidor_web()

    tree.add_command(apodo_group)
    if GUILD_ID:
        guild_obj = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild_obj)
        await tree.sync(guild=guild_obj)
        print(f"Slash commands sincronizados al servidor {GUILD_ID} (instantáneo).")
    else:
        await tree.sync()
        print("Slash commands sincronizados globalmente (puede tardar ~1h en propagarse).")


@client.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles:
        return
    try:
        await enforce_single_rank_per_category(after)
        await actualizar_apodo(after)
    except discord.Forbidden:
        print(f"No pude actualizar roles/apodo de {after} — jerarquía del bot insuficiente o es el dueño del servidor.")
    except discord.HTTPException as err:
        print(f"Error de Discord actualizando a {after}: {err}")


@apodo_group.command(name="yo", description="Actualiza tu propio apodo según tus roles actuales")
async def apodo_yo(interaction: discord.Interaction):
    member = interaction.user
    nuevo = build_nickname(member)
    if not nuevo:
        await interaction.response.send_message(
            "No tienes ningún rol operativo, de instructor o liderazgo — no hay prefijo que asignar.",
            ephemeral=True,
        )
        return
    try:
        await member.edit(nick=nuevo, reason="Actualización manual de apodo (/apodo yo)")
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiarte el apodo — el bot no tiene permisos suficientes sobre tu rol más alto, "
            "o eres el dueño del servidor (Discord no permite cambiarle el apodo al dueño).",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(f"Listo, tu apodo ahora es **{nuevo}**.", ephemeral=True)


@apodo_group.command(name="usuario", description="[Liderazgo/Staff] Actualiza el apodo de otro usuario según su jerarquía de roles")
@app_commands.describe(miembro="Usuario a actualizar")
async def apodo_usuario(interaction: discord.Interaction, miembro: discord.Member):
    if not has_any_role(interaction.user, LIDERAZGO_ORDER):
        await interaction.response.send_message("Este comando es solo para Liderazgo/Staff.", ephemeral=True)
        return

    nuevo = build_nickname(miembro)
    if not nuevo:
        await interaction.response.send_message(
            f"{miembro.mention} no tiene ningún rol operativo, de instructor o liderazgo.",
            ephemeral=True,
        )
        return
    try:
        await miembro.edit(nick=nuevo, reason=f"Actualización de apodo por {interaction.user}")
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiar ese apodo — jerarquía de roles del bot insuficiente, o es el dueño del servidor.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(f"Apodo de {miembro.mention} actualizado a **{nuevo}**.", ephemeral=True)


@apodo_group.command(name="todos", description="[Solo dueño del server] Recalcula el apodo de todos los miembros")
async def apodo_todos(interaction: discord.Interaction):
    if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Este comando es solo para el dueño del servidor.", ephemeral=True)
        return

    await interaction.response.send_message("Actualizando apodos de todo el servidor, esto puede tardar…", ephemeral=True)

    actualizados = 0
    fallidos = 0
    async for member in interaction.guild.fetch_members(limit=None):
        nuevo = build_nickname(member)
        if not nuevo or nuevo == member.display_name:
            continue
        try:
            await member.edit(nick=nuevo, reason=f"Recálculo masivo de apodos por {interaction.user}")
            actualizados += 1
        except (discord.Forbidden, discord.HTTPException):
            fallidos += 1
        await asyncio.sleep(0.5)  # evitar rate limit de Discord al editar apodos en masa

    await interaction.followup.send(
        f"Listo. Apodos actualizados: {actualizados}. No se pudieron cambiar: {fallidos} "
        "(probablemente su rol más alto está por encima del bot, o son el dueño del servidor).",
        ephemeral=True,
    )


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


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.content.strip() != "!publicar-verificacion":
        return
    if not message.author.guild_permissions.administrator:
        await message.reply("Solo un administrador puede publicar este mensaje.", delete_after=8)
        return

    with open(ARCHIVO_MENSAJE, encoding="utf-8") as f:
        payload = json.load(f)

    try:
        await _publicar_payload_crudo(message.channel.id, payload)
    except Exception as err:
        await message.reply(f"No pude publicar el mensaje: {err}", delete_after=15)
        print(f"ERROR al publicar mensaje de verificación: {err}")
        return

    await message.delete()
    print(f"Mensaje de verificación publicado en #{message.channel.name}")


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


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: no encontré la variable de entorno DISCORD_BOT_TOKEN.")
        print("Defínela antes de correr este script (ver instrucciones arriba).")
    else:
        client.run(BOT_TOKEN)
