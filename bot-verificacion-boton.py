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

import json
import os

import discord
from aiohttp import web

CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))  # Render inyecta PORT automáticamente

V_ROLE_ID  = 1508568101770367156   # V  | Verificado
NV_ROLE_ID = 1532919695827665057   # NV | No Verificado

CUSTOM_ID = "atc24_verificar_aceptacion"
ARCHIVO_MENSAJE = os.path.join(CARPETA_SCRIPT, "verificacion-boton-components-v2.json")
# ─────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # necesario para poder modificar roles de miembros

client = discord.Client(intents=intents)


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

    # Enviamos el payload crudo (Components V2) porque discord.py aún
    # no tiene una API de alto nivel para Container/Text Display/Separator.
    await client.http.send_message(message.channel.id, payload)
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
