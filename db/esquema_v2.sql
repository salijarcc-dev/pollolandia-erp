-- ============================================================================
-- ERP POLLOLANDIA - Migración 2 (Fase 2: motor contable y seguridad)
-- Se aplica sobre la versión 1 dentro de UNA transacción (db/migraciones.py).
-- Agrega:
--   * Controles de período cerrado y de fecha dentro del período (defensa en
--     profundidad: servicios/contable.py valida antes y da mensajes claros).
--   * Prohibición de reabrir períodos y ejercicios cerrados.
--   * Autorizaciones según la Matriz CI-FIN-09 y firmas (incluida la
--     suplencia CI-FIN-17 con constancia obligatoria).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. LIBRO DIARIO: período abierto y fecha coherente
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_asientos_periodo_abierto
BEFORE INSERT ON asientos
WHEN (SELECT estado FROM periodos WHERE id = NEW.periodo_id) = 'CERRADO'
  OR (SELECT e.estado FROM periodos p JOIN ejercicios e ON e.ejercicio = p.ejercicio
       WHERE p.id = NEW.periodo_id) = 'CERRADO'
BEGIN
    SELECT RAISE(ABORT, 'El período contable está cerrado: no admite asientos.');
END;

CREATE TRIGGER trg_asientos_fecha_en_periodo
BEFORE INSERT ON asientos
WHEN NOT EXISTS (SELECT 1 FROM periodos p
                  WHERE p.id = NEW.periodo_id
                    AND NEW.fecha BETWEEN p.fecha_inicio AND p.fecha_fin)
BEGIN
    SELECT RAISE(ABORT, 'La fecha del asiento no corresponde al período indicado.');
END;

CREATE TRIGGER trg_detalle_periodo_abierto
BEFORE INSERT ON asiento_detalle
WHEN (SELECT p.estado FROM asientos a JOIN periodos p ON p.id = a.periodo_id
       WHERE a.id = NEW.asiento_id) = 'CERRADO'
BEGIN
    SELECT RAISE(ABORT, 'El período contable está cerrado: no admite líneas de asiento.');
END;

CREATE TRIGGER trg_periodos_no_reabrir
BEFORE UPDATE OF estado ON periodos
WHEN OLD.estado = 'CERRADO' AND NEW.estado <> 'CERRADO'
BEGIN
    SELECT RAISE(ABORT, 'Un período cerrado no se reabre: registre un ajuste en un período abierto.');
END;

CREATE TRIGGER trg_ejercicios_no_reabrir
BEFORE UPDATE OF estado ON ejercicios
WHEN OLD.estado = 'CERRADO' AND NEW.estado <> 'CERRADO'
BEGIN
    SELECT RAISE(ABORT, 'Un ejercicio cerrado no se reabre.');
END;

CREATE INDEX idx_asientos_reversa ON asientos(reversa_de);

-- ---------------------------------------------------------------------------
-- 2. AUTORIZACIONES (Matriz CI-FIN-09)
-- ---------------------------------------------------------------------------
CREATE TABLE autorizaciones (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_decision       TEXT    NOT NULL,
    monto_cent          INTEGER NOT NULL CHECK (monto_cent >= 0),
    ejercicio           INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    descripcion         TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    referencia          TEXT,
    requisito_json      TEXT    NOT NULL,
    documentos_json     TEXT,
    estado              TEXT    NOT NULL DEFAULT 'PENDIENTE'
                        CHECK (estado IN ('PENDIENTE','AUTORIZADA','RECHAZADA','UTILIZADA')),
    solicitado_por_id   INTEGER NOT NULL REFERENCES usuarios(id),
    solicitado_en       TEXT    NOT NULL,
    resuelto_en         TEXT,
    utilizado_tabla     TEXT,
    utilizado_id        TEXT,
    utilizado_en        TEXT
);
CREATE INDEX idx_autorizaciones_estado ON autorizaciones(estado, tipo_decision);

-- Solo cambian estado y datos de resolución/uso; las transiciones válidas son
-- PENDIENTE -> AUTORIZADA | RECHAZADA y AUTORIZADA -> UTILIZADA.
CREATE TRIGGER trg_autorizaciones_solo_estado
BEFORE UPDATE ON autorizaciones
WHEN OLD.tipo_decision <> NEW.tipo_decision OR OLD.monto_cent <> NEW.monto_cent
  OR OLD.ejercicio <> NEW.ejercicio OR OLD.descripcion <> NEW.descripcion
  OR IFNULL(OLD.referencia,'') <> IFNULL(NEW.referencia,'')
  OR OLD.requisito_json <> NEW.requisito_json
  OR IFNULL(OLD.documentos_json,'') <> IFNULL(NEW.documentos_json,'')
  OR OLD.solicitado_por_id <> NEW.solicitado_por_id OR OLD.solicitado_en <> NEW.solicitado_en
  OR NOT ((OLD.estado = NEW.estado)
       OR (OLD.estado = 'PENDIENTE' AND NEW.estado IN ('AUTORIZADA','RECHAZADA'))
       OR (OLD.estado = 'AUTORIZADA' AND NEW.estado = 'UTILIZADA'))
BEGIN
    SELECT RAISE(ABORT, 'Una autorización solo cambia de estado por la vía prevista y no se edita.');
END;

CREATE TRIGGER trg_autorizaciones_no_delete
BEFORE DELETE ON autorizaciones
BEGIN
    SELECT RAISE(ABORT, 'Las autorizaciones no se borran.');
END;

-- ---------------------------------------------------------------------------
-- 3. FIRMAS DE AUTORIZACIÓN (incluye suplencia CI-FIN-17)
--    calidad = en calidad de qué autoridad firma: GG, CV, CA o AG.
-- ---------------------------------------------------------------------------
CREATE TABLE autorizacion_firmas (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    autorizacion_id            INTEGER NOT NULL REFERENCES autorizaciones(id),
    calidad                    TEXT    NOT NULL CHECK (calidad IN ('GG','CV','CA','AG')),
    usuario_id                 INTEGER NOT NULL REFERENCES usuarios(id),
    rol_usuario                TEXT    NOT NULL REFERENCES roles(codigo),
    decision                   TEXT    NOT NULL CHECK (decision IN ('APRUEBA','RECHAZA')),
    documento_ref              TEXT,
    observacion                TEXT,
    es_suplencia               INTEGER NOT NULL DEFAULT 0 CHECK (es_suplencia IN (0,1)),
    orden_prelacion            INTEGER CHECK (orden_prelacion IS NULL OR orden_prelacion IN (1,2)),
    causa_suplencia            TEXT,
    motivo_omision_presidente  TEXT,
    votos_favor                INTEGER CHECK (votos_favor IS NULL OR votos_favor >= 0),
    votos_emitidos             INTEGER CHECK (votos_emitidos IS NULL OR votos_emitidos > 0),
    firmado_en                 TEXT    NOT NULL,
    comunicado_gg_en           TEXT,
    revisado_gg_en             TEXT,
    revisado_gg_por            TEXT,
    UNIQUE (autorizacion_id, calidad),
    UNIQUE (autorizacion_id, usuario_id),
    -- CI-FIN-17 Art. 2: la suplencia solo sustituye a la GG y exige causa y constancia escrita.
    CHECK (es_suplencia = 0 OR (calidad = 'GG' AND orden_prelacion IN (1,2)
           AND length(trim(IFNULL(causa_suplencia,''))) > 0
           AND length(trim(IFNULL(documento_ref,''))) > 0)),
    -- CI-FIN-17 Art. 1: el Tesorero solo actúa "en defecto" del Presidente.
    CHECK (es_suplencia = 0 OR orden_prelacion = 1
           OR length(trim(IFNULL(motivo_omision_presidente,''))) > 0),
    CHECK (votos_favor IS NULL OR votos_emitidos IS NULL OR votos_favor <= votos_emitidos)
);
CREATE INDEX idx_firmas_autorizacion ON autorizacion_firmas(autorizacion_id);

CREATE TRIGGER trg_firmas_no_delete
BEFORE DELETE ON autorizacion_firmas
BEGIN
    SELECT RAISE(ABORT, 'Las firmas de autorización no se borran.');
END;

-- Una firma es inmutable; solo se completan los datos de seguimiento de la
-- suplencia (comunicación a la GG en 24 h y revisión a su reincorporación).
CREATE TRIGGER trg_firmas_solo_seguimiento
BEFORE UPDATE ON autorizacion_firmas
WHEN OLD.autorizacion_id <> NEW.autorizacion_id OR OLD.calidad <> NEW.calidad
  OR OLD.usuario_id <> NEW.usuario_id OR OLD.rol_usuario <> NEW.rol_usuario
  OR OLD.decision <> NEW.decision OR IFNULL(OLD.documento_ref,'') <> IFNULL(NEW.documento_ref,'')
  OR IFNULL(OLD.observacion,'') <> IFNULL(NEW.observacion,'')
  OR OLD.es_suplencia <> NEW.es_suplencia
  OR IFNULL(OLD.orden_prelacion,0) <> IFNULL(NEW.orden_prelacion,0)
  OR IFNULL(OLD.causa_suplencia,'') <> IFNULL(NEW.causa_suplencia,'')
  OR IFNULL(OLD.motivo_omision_presidente,'') <> IFNULL(NEW.motivo_omision_presidente,'')
  OR IFNULL(OLD.votos_favor,-1) <> IFNULL(NEW.votos_favor,-1)
  OR IFNULL(OLD.votos_emitidos,-1) <> IFNULL(NEW.votos_emitidos,-1)
  OR OLD.firmado_en <> NEW.firmado_en
  OR (OLD.comunicado_gg_en IS NOT NULL AND IFNULL(NEW.comunicado_gg_en,'') <> OLD.comunicado_gg_en)
  OR (OLD.revisado_gg_en IS NOT NULL AND IFNULL(NEW.revisado_gg_en,'') <> OLD.revisado_gg_en)
BEGIN
    SELECT RAISE(ABORT, 'Una firma registrada no se modifica.');
END;
