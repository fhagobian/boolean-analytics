-- ═══════════════════════════════════════════════════════════════
-- BOOLEAN · Calendario de eventos comerciales (Bloque C, Fase 1)
-- Tabla configurable — se puede agregar/editar eventos desde CONFIG.
-- Precargada con el "año tipo" según la Cámara de Comercio y
-- Servicios del Uruguay (CCSUy), fuente oficial del calendario
-- comercial uruguayo, más fechas de e-commerce regionales que
-- también aplican a Uruguay.
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS calendario_eventos (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nombre TEXT NOT NULL,
  fecha DATE NOT NULL,
  categoria TEXT DEFAULT 'comercial',  -- comercial | feriado_no_laborable | regional_especifico
  impacto_esperado TEXT DEFAULT 'medio',  -- alto | medio | bajo — cuánto puede mover la demanda
  dias_influencia_antes INT DEFAULT 3,  -- Prophet suele modelar el efecto unos días antes/después
  dias_influencia_despues INT DEFAULT 1,
  activo BOOLEAN DEFAULT TRUE,
  notas TEXT
);

CREATE INDEX IF NOT EXISTS idx_calendario_fecha ON calendario_eventos(fecha);

ALTER TABLE calendario_eventos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "calendario_select" ON calendario_eventos;
CREATE POLICY "calendario_select" ON calendario_eventos FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS "calendario_write" ON calendario_eventos;
CREATE POLICY "calendario_write" ON calendario_eventos FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- ── Año tipo 2026 (ajustar fecha exacta año a año — los días de la
-- semana de estas fechas móviles cambian, pero el mes se mantiene) ──

INSERT INTO calendario_eventos (nombre, fecha, categoria, impacto_esperado, dias_influencia_antes, dias_influencia_despues, notas) VALUES
  ('Día de Reyes',                    '2026-01-06', 'comercial', 'bajo',  3, 0, 'Impulso menor de ventas, juguetes y regalos'),
  ('Vuelta a clases',                 '2026-02-15', 'comercial', 'bajo',  10, 0, 'Ventana amplia feb-mar, útiles y tecnología'),
  ('Semana de Turismo (Carnaval/Sta.)','2026-04-06', 'comercial', 'medio', 3, 3, 'Semana completa de alto movimiento comercial y turístico'),
  ('Día de la Madre',                 '2026-05-17', 'comercial', 'alto',  7, 1, 'Una de las fechas de mayor movimiento comercial del año — CCSUy'),
  ('Día de los Abuelos',              '2026-06-18', 'comercial', 'bajo',  3, 0, 'CCSUy'),
  ('Día del Padre',                   '2026-07-12', 'comercial', 'medio', 5, 1, 'CCSUy'),
  ('Semana de la Cerveza (Paysandú)', '2026-08-01', 'regional_especifico', 'medio', 2, 8, 'Relevante para comercios/gastronomía de la zona litoral'),
  ('Día de los Niños',                '2026-08-16', 'comercial', 'alto',  7, 1, 'CCSUy — alta venta de juguetes y electrónica'),
  ('Cyber Monday (1ra edición)',      '2026-05-25', 'comercial', 'alto',  2, 2, 'Alto tráfico e-commerce, pico de transacciones con tarjeta'),
  ('Cyber Monday (2da edición)',      '2026-10-26', 'comercial', 'alto',  2, 2, 'Segunda edición anual — Uruguay/CCSUy'),
  ('Black Friday',                    '2026-11-27', 'comercial', 'alto',  3, 2, 'Pico de transacciones, alta demanda de terminales activas'),
  ('Fin de año / Navidad',            '2026-12-24', 'comercial', 'alto',  15, 5, 'Ventana extendida diciembre — mayor volumen de todo el mes'),
  ('Año Nuevo',                       '2026-12-31', 'comercial', 'alto',  3, 3, 'Cierre de año, alto movimiento gastronómico/retail')
ON CONFLICT DO NOTHING;
