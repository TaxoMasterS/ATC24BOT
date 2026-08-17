# Guía: bot de verificación 24/7 gratis con Render (sin tarjeta)

Todos los archivos del bot ya están en este repo. Solo faltan tres pasos: subirlo a GitHub, desplegarlo en Render, y configurar UptimeRobot.

---

## Paso 1 — Subir este repo a GitHub

Abre una terminal dentro de esta carpeta (`ATC24BOT`) y ejecuta:

```
git add .
git commit -m "Bot de verificacion ATC24 Espanol"
git push
```

Si el repo aún no tiene remoto configurado (`git remote -v` no muestra nada), primero:

1. Crea un repositorio nuevo en [github.com](https://github.com) (puede ser **Private**), sin plantilla ni README.
2. Conéctalo:
   ```
   git remote add origin https://github.com/TU-USUARIO/ATC24BOT.git
   git branch -M main
   git push -u origin main
   ```

> El token nunca se sube — el script lo lee de la variable de entorno `DISCORD_BOT_TOKEN`, y `.gitignore` ya excluye archivos `.env`/`.token`.

---

## Paso 2 — Crear la cuenta y el servicio en Render

1. Ve a [render.com](https://render.com) y regístrate (puedes usar tu cuenta de GitHub directamente — no pide tarjeta).
2. En el panel, botón **New +** → **Web Service**.
3. Conecta tu repositorio `ATC24BOT` (Render te va a pedir autorizar acceso a GitHub la primera vez).
4. Configura así:
   - **Name**: `atc24-bot-verificacion`
   - **Region**: la que esté más cerca (Oregon o Frankfurt están bien)
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: **Free**
5. En **Environment Variables**, agrega:
   - Key: `DISCORD_BOT_TOKEN` → Value: tu token real del bot
6. Botón **Create Web Service**. Render instala dependencias y arranca el bot — tarda 2-3 minutos.
7. Revisa la pestaña **Logs**: debe aparecer `Conectado como ...` y `Mini servidor web escuchando en el puerto...`.
8. Copia la URL que Render asigna arriba del panel (ej. `https://atc24-bot-verificacion.onrender.com`) — la necesitas en el siguiente paso.

---

## Paso 3 — Configurar UptimeRobot para que no se duerma

El plan gratis de Render apaga el servicio tras 15 minutos sin visitas. UptimeRobot lo visita cada 5 minutos gratis, así nunca llega a dormirse.

1. Ve a [uptimerobot.com](https://uptimerobot.com) y crea una cuenta gratis (no pide tarjeta).
2. Botón **+ Add New Monitor**.
3. Configura:
   - **Monitor Type**: HTTP(s)
   - **Friendly Name**: `ATC24 Bot Verificación`
   - **URL**: la URL de Render que copiaste
   - **Monitoring Interval**: 5 minutos
4. Guarda. Listo — UptimeRobot mantiene el bot despierto solo, desde ahora.

---

## Paso 4 — Publicar el mensaje de verificación

En el canal de Discord donde quieras el botón, escribe (como administrador):

```
!publicar-verificacion
```

El bot publica el mensaje y borra tu comando.

---

## Actualizar el bot más adelante

```
git add .
git commit -m "actualizacion"
git push
```

Render detecta el push automáticamente y vuelve a desplegar solo — no necesitas hacer nada más.

## Notas

- Es normal ver alguna reconexión breve de vez en cuando (el plan gratis no es tan estable como uno pagado), pero el bot se recupera solo.
- Si más adelante quieres más estabilidad, Render tiene un plan pagado (~$7/mes) que elimina el dormido.

## ⚠️ Disco persistente (IMPORTANTE desde que el bot tiene su propia base de datos)

Desde el rediseño del bot (vuelos, ATC, moderación, Academia — todo con motor
propio en `data/atc24.db`, SQLite), **el disco free de Render es efímero**:
cada vez que Render redespliega el servicio (un push nuevo, o un reinicio del
plan free), el archivo `data/atc24.db` se borra y el bot arranca con la base
vacía — se pierden vuelos activos, casos de moderación, progreso de Academia,
todo.

Para que los datos sobrevivan a un redeploy, hay que agregar un **Persistent
Disk** al servicio en Render (Settings → Disks → Add Disk), montado en la
carpeta `data/` de este repo, con al menos 1 GB (de sobra para SQLite). Esto
es un servicio pago aparte del Web Service gratis — revisa el precio actual
en Render antes de activarlo. Sin este disco, tratá al bot como si perdiera
memoria en cada actualización.
