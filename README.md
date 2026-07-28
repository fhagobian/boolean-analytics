# BOOLEAN Analytics Engine

Servicio Python (FastAPI) que calcula el Bloque A — Radiografía Operativa.
Se conecta en modo **solo lectura** a Supabase, vía su **API REST**
(PostgREST) sobre HTTPS — no usa conexión TCP directa a Postgres, porque
Railway no permite salida de red por los puertos nativos de base de datos
(5432/6543). HTTPS (443) sí funciona siempre, es el mismo mecanismo que
ya usa tu frontend con supabase-js.

## Endpoints
- `GET /health` — chequeo de vida, sin autenticación.
- `GET /radiografia` — devuelve los 5 bloques de la radiografía operativa.
  Requiere header `Authorization: Bearer <API_SECRET>`.
  Query params opcionales: `equipo=TRANS`, `vista_tendencia=semanas|meses`.

## Variables de entorno necesarias en Railway

Andá a Railway → tu servicio → pestaña **Variables** y cargá estas 3:

1. **`SUPABASE_URL`** — la URL base de tu proyecto Supabase, formato:
   `https://mvavxxhjazwfovjvjnbm.supabase.co`
   (la sacás de Supabase → Settings → API → "Project URL")

2. **`SUPABASE_SERVICE_KEY`** — la clave `service_role` de Supabase
   (Supabase → Settings → API → sección "Project API keys" → `service_role`
   → "Reveal" y copiar). **Es una clave con acceso total a la base — nunca
   se comparte con el frontend, solo vive acá en Railway.**

3. **`API_SECRET`** — el token que ya tenías cargado, sin cambios (lo usa
   el frontend para autenticar sus pedidos a este servicio).

La variable vieja `DATABASE_URL` ya no se usa — podés dejarla o borrarla,
no afecta.

## Deploy en Railway
Ya está conectado a este repo (`boolean-analytics`, rama `main`). Cada
`git push` dispara un redeploy automático. Si cambiaste las variables de
arriba, Railway redeploya solo al guardarlas.

## Verificación
```
GET https://TU-DOMINIO.up.railway.app/health
```
Debería responder `{"status":"ok","service":"boolean-analytics"}`.

```powershell
curl https://TU-DOMINIO.up.railway.app/radiografia -H "Authorization: Bearer TU_API_SECRET"
```

## Nota sobre datos simulados
Hasta que se cargue la tabla `casos_historicos` con los 2 años de datos
reales, las comparaciones interanuales usan una simulación determinística
(mismo caso → mismo resultado simulado). Todo campo así se marca con
`"simulado": true` en la respuesta — nunca se confunde con un dato real.
