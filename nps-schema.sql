-- ═══════════════════════════════════════════════════════════════
-- BOOLEAN · Sistema de encuestas NPS por WhatsApp (Twilio)
-- ═══════════════════════════════════════════════════════════════

-- Control del Director: habilita el muestreo por un rango de fechas
-- y define qué % de casos exitosos entran al sorteo cada día.
CREATE TABLE IF NOT EXISTS config_encuestas_nps (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  activa BOOLEAN DEFAULT FALSE,
  fecha_desde DATE,
  fecha_hasta DATE,
  porcentaje_muestra INT DEFAULT 10,
  dias_antifraude_mismo_telefono INT DEFAULT 60,  -- ventana para bloquear reuso del mismo celular
  updated_by TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Fila única de configuración (se hace upsert siempre sobre id=1)
INSERT INTO config_encuestas_nps (id, activa) VALUES (1, false)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE config_encuestas_nps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "config_nps_select" ON config_encuestas_nps;
CREATE POLICY "config_nps_select" ON config_encuestas_nps FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "config_nps_write" ON config_encuestas_nps;
CREATE POLICY "config_nps_write" ON config_encuestas_nps FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- Nota: caso_id es TEXT (no FK tipada) porque no tenemos certeza si
-- casos.id es UUID o BIGINT en tu instancia — TEXT es compatible con
-- cualquiera de los dos sin riesgo de que este script falle.
CREATE TABLE IF NOT EXISTS encuestas_nps (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  caso_id TEXT,
  tecnico_id UUID,
  telefono TEXT NOT NULL,          -- normalizado formato E.164, ej: +59899123456
  estado TEXT DEFAULT 'pendiente', -- pendiente | enviada | respondida | error | bloqueada_antifraude
  puntaje INT,                     -- 0-10, null hasta que responda
  comentario TEXT,
  twilio_sid_enviado TEXT,
  twilio_sid_respuesta TEXT,
  error_detalle TEXT,
  enviada_at TIMESTAMPTZ,
  respondida_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nps_telefono ON encuestas_nps(telefono);
CREATE INDEX IF NOT EXISTS idx_nps_caso ON encuestas_nps(caso_id);
CREATE INDEX IF NOT EXISTS idx_nps_estado ON encuestas_nps(estado);

ALTER TABLE encuestas_nps ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "nps_select" ON encuestas_nps;
CREATE POLICY "nps_select" ON encuestas_nps FOR SELECT TO authenticated USING (true);
-- Sin policy de INSERT/UPDATE para 'authenticated' — solo el backend
-- (con la service_role key, que bypassea RLS) puede escribir acá.
-- Esto evita que alguien desde el frontend falsifique una respuesta.
