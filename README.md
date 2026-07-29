# BOOLEAN Analytics Engine

Servicio Python (FastAPI) que calcula el Bloque A — Radiografía Operativa.
Se conecta en modo **solo lectura** a Supabase, vía su **API REST**
(PostgREST) sobre HTTPS.

## ⚠️ Decisión de arquitectura clave — LEER ANTES DE TOCAR db.py

Este servicio corre en Railway. Railway **no tiene salida de red IPv6**
y además **bloquea conexiones TCP crudas** a puertos de base de datos
(5432/6543). Por eso:

1. **No se usa `asyncpg` ni conexión directa a Postgres.** Se usa la
   API REST de Supabase (PostgREST) sobre HTTPS puerto 443, que sí
   funciona siempre — el mismo mecanismo que usa el frontend con
   supabase-js.
2. **`app/netfix.py` fuerza DNS a devolver solo IPv4`** para TODO el
   proceso. Sin este patch, cualquier hostname que resuelva a IPv6
   (incluido el de Supabase) hace que la conexión se cuelgue hasta
   timeout, y Railway corta con un 502 genérico sin información.
   Se importa automáticamente desde `app/__init__.py`, antes que
   cualquier otro módulo cree un cliente de red.

Si en el futuro aparece un 502 "Application failed to respond" al
agregar una nueva librería que hable por red, sospechar primero de
este mismo patrón (IPv6) antes de asumir que es un bug de código.

## Endpoints
- `GET /health` — chequeo de vida básico, sin autenticación ni red externa.
- `GET /health/supabase` — chequeo específico de la conexión a Supabase
  (sin token), útil para diagnosticar problemas de configuración rápido.
- `GET /radiografia` — devuelve los 5 bloques de la radiografía operativa.
  Requiere header `Authorization: Bearer <API_SECRET>`.
  Query params opcionales: `equipo=TRANS`, `vista_tendencia=semanas|meses`.

## Variables de entorno necesarias en Railway
1. **`SUPABASE_URL`** — `https://mvavxxhjazwfovjvjnbm.supabase.co`
   (Supabase → Settings → API → "Project URL"). **Sin `/rest/v1` al final.**
2. **`SUPABASE_SERVICE_KEY`** — clave `service_role` de Supabase
   (Settings → API → "Project API keys" → service_role → Reveal).
   Acceso total a la base — nunca va al frontend, solo vive acá.
3. **`API_SECRET`** — token inventado, compartido con el frontend para
   autenticar sus pedidos a este servicio.

`DATABASE_URL` ya no se usa (era para la conexión directa descartada).

## Deploy
Conectado a este repo (`boolean-analytics`, rama `main`) vía
Railway → Settings → Source. Cada `git push` dispara redeploy automático.
Si solo cambiás una variable de entorno, a veces Railway no redeploya
solo — verificar si aparece un botón "Deploy"/"Apply changes" arriba
de la pantalla y hacer click ahí manualmente.

## Verificación rápida
```powershell
(Invoke-WebRequest -Uri "https://TU-DOMINIO.up.railway.app/health/supabase").Content
```
Debe responder `{"status":"ok",...}`.

```powershell
(Invoke-WebRequest -Uri "https://TU-DOMINIO.up.railway.app/radiografia" -Headers @{Authorization='Bearer TU_API_SECRET'}).Content
```
(Usar comillas SIMPLES en PowerShell si el secret tiene caracteres
especiales como `$` o `&` — con comillas dobles PowerShell los interpreta
como parte de su propio lenguaje.)

## Nota sobre datos simulados
Hasta que se cargue la tabla `casos_historicos` con los 2 años de datos
reales, las comparaciones interanuales usan una simulación determinística
(mismo caso → mismo resultado simulado). Todo campo así se marca con
`"simulado": true` en la respuesta — nunca se confunde con un dato real.
