# BOOLEAN Analytics Engine

Servicio Python (FastAPI) que calcula el Bloque A — Radiografía Operativa.
Se conecta en modo **solo lectura** a la base de Supabase de BOOLEAN.

## Endpoints
- `GET /health` — chequeo de vida, sin autenticación.
- `GET /radiografia` — devuelve los 5 bloques de la radiografía operativa.
  Requiere header `Authorization: Bearer <API_SECRET>`.
  Query params opcionales: `equipo=TRANS`, `vista_tendencia=semanas|meses`.

## Variables de entorno (ya cargadas en Railway)
- `DATABASE_URL` — connection string directo de Supabase Postgres.
- `API_SECRET` — token compartido con el frontend para autorizar requests.

## Deploy en Railway — conectar este código al servicio ya creado

1. Creá un repositorio nuevo en GitHub (ej: `boolean-analytics`) y subí
   todo el contenido de esta carpeta:
   ```powershell
   git init
   git add .
   git commit -m "BOOLEAN Analytics Engine - bloque A"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/boolean-analytics.git
   git push -u origin main
   ```
2. En Railway, entrá al servicio que ya creaste (el que tiene las
   variables `DATABASE_URL` y `API_SECRET` cargadas).
3. Andá a **Settings → Source** → **Connect Repo** → elegí el repo
   `boolean-analytics` que acabás de subir.
4. Railway va a detectar el `Procfile` y desplegar automáticamente.
5. Cuando el deploy diga **Active/Success**, andá a **Settings → Networking**
   y generá un dominio público (**Generate Domain**). Vas a obtener algo como
   `boolean-analytics-production.up.railway.app`.

## Verificación
Con el dominio generado, probá desde el navegador o `curl`:
```
GET https://TU-DOMINIO.up.railway.app/health
```
Debería responder `{"status":"ok","service":"boolean-analytics"}`.

Para probar el endpoint protegido (reemplazando el token real):
```powershell
curl https://TU-DOMINIO.up.railway.app/radiografia -H "Authorization: Bearer TU_API_SECRET"
```

## Nota sobre datos simulados
Hasta que se cargue la tabla `casos_historicos` con los 2 años de datos
reales, las comparaciones interanuales usan una simulación determinística
(mismo caso → mismo resultado simulado). Todo campo así se marca con
`"simulado": true` en la respuesta — nunca se confunde con un dato real.
