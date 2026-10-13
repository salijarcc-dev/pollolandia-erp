-- ===========================================================================
-- ERP Pollolandia — Esquema v13 (Fase 13): integración y puesta en marcha.
--
-- Fuentes: Manual de Archivo Contable MAN-ARC-01 §5.2 (regla de respaldo 3-2-1:
-- 3 copias, 2 medios, 1 fuera del sitio; copia incremental diaria, completa
-- semanal y de cierre mensual y anual; prueba de restauración semestral con
-- acta y verificación del Consejo de Vigilancia), POA 2026-2027 (§1.1
-- calendario de ciclos, §3.1 presupuesto por objeto de gasto y área, §3.2 flujo
-- de caja mensual) y matriz de permisos (cambio de parámetros: GG propone, CA o
-- AG aprueban con acta).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. RESPALDOS 3-2-1
-- ---------------------------------------------------------------------------
CREATE TABLE respaldos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo             TEXT    NOT NULL CHECK (tipo IN ('DIARIO','SEMANAL','MENSUAL','ANUAL','MANUAL')),
    fecha            TEXT    NOT NULL,
    referencia       TEXT    NOT NULL,          -- AAAA-MM-DD, AAAA-Wnn, período o ejercicio
    archivo          TEXT    NOT NULL,          -- nombre del paquete .zip
    sha256           TEXT    NOT NULL CHECK (length(sha256) = 64),
    bytes            INTEGER NOT NULL CHECK (bytes > 0),
    version_esquema  INTEGER NOT NULL,
    documentos       INTEGER NOT NULL DEFAULT 0, -- archivos del archivo digital incluidos
    incremental      INTEGER NOT NULL DEFAULT 0 CHECK (incremental IN (0,1)),
    conteos_json     TEXT    NOT NULL,          -- conteos y sumas de control al momento del respaldo
    ubicaciones_json TEXT    NOT NULL,          -- {LOCAL: ruta|null, EXTERNO: ruta|null, FUERA_SITIO: ruta|null}
    copias           INTEGER NOT NULL CHECK (copias BETWEEN 1 AND 3),
    estado           TEXT    NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','DEPURADO')),
    depurado_en      TEXT,
    creado_por       TEXT    NOT NULL,
    creado_en        TEXT    NOT NULL,
    UNIQUE (tipo, referencia)
);
CREATE INDEX idx_respaldos_fecha ON respaldos(fecha, tipo);

CREATE TABLE actas_restauracion (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    respaldo_id        INTEGER NOT NULL REFERENCES respaldos(id),
    fecha              TEXT    NOT NULL,
    ubicacion          TEXT    NOT NULL CHECK (ubicacion IN ('LOCAL','EXTERNO','FUERA_SITIO')),
    resultado          TEXT    NOT NULL CHECK (resultado IN ('CONFORME','FALLIDA')),
    verificaciones_json TEXT   NOT NULL,
    duracion_ms        INTEGER NOT NULL CHECK (duracion_ms >= 0),
    elaborado_por      TEXT    NOT NULL,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios(id),
    verificado_por     TEXT,                   -- miembro del CV (MAN-ARC-01 §5.2)
    verificado_en      TEXT,
    observacion_cv     TEXT,
    registrado_en      TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. ADMINISTRACIÓN DE PARÁMETROS CON HISTORIAL
-- ---------------------------------------------------------------------------
CREATE TABLE solicitudes_parametro (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    clave             TEXT    NOT NULL,
    valor_anterior    TEXT,
    valor_nuevo       TEXT    NOT NULL CHECK (length(trim(valor_nuevo)) > 0),
    tipo              TEXT    NOT NULL CHECK (tipo IN ('ENTERO','DECIMAL','TEXTO','BOOLEANO')),
    ejercicio_desde   INTEGER NOT NULL CHECK (ejercicio_desde BETWEEN 2000 AND 9998),
    fuente_documental TEXT    NOT NULL CHECK (length(trim(fuente_documental)) > 0),
    motivo            TEXT    NOT NULL CHECK (length(trim(motivo)) > 0),
    estado            TEXT    NOT NULL DEFAULT 'SOLICITADA' CHECK (estado IN ('SOLICITADA','APROBADA','RECHAZADA')),
    solicitado_por    TEXT    NOT NULL,
    solicitado_en     TEXT    NOT NULL,
    resuelto_por      TEXT,
    resuelto_rol      TEXT,
    resuelto_en       TEXT,
    acta_ref          TEXT,
    observacion       TEXT,
    parametro_id      INTEGER REFERENCES parametros(id),
    CHECK (estado = 'SOLICITADA' OR (resuelto_por IS NOT NULL AND resuelto_en IS NOT NULL)),
    CHECK (estado <> 'APROBADA' OR (acta_ref IS NOT NULL AND length(trim(acta_ref)) > 0))
);
CREATE INDEX idx_solicitudes_parametro ON solicitudes_parametro(clave, estado);

-- ---------------------------------------------------------------------------
-- 3. DATOS DEL POA 2026-2027 (carga con scripts/cargar_datos_poa.py)
-- ---------------------------------------------------------------------------
CREATE TABLE poa_cargas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    version      TEXT    NOT NULL UNIQUE,
    fuente       TEXT    NOT NULL,
    resumen_json TEXT    NOT NULL,
    cargado_por  TEXT    NOT NULL,
    cargado_en   TEXT    NOT NULL
);

CREATE TABLE poa_calendario (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    carga_id           INTEGER NOT NULL REFERENCES poa_cargas(id),
    ejercicio          INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    ciclo              INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    inicio             TEXT    NOT NULL,
    cierre             TEXT    NOT NULL,
    aves               INTEGER NOT NULL CHECK (aves > 0),
    tramo              TEXT    NOT NULL,
    dias_en_poa        INTEGER NOT NULL CHECK (dias_en_poa >= 0),
    ingreso_cent       INTEGER NOT NULL CHECK (ingreso_cent >= 0),
    UNIQUE (carga_id, ciclo),
    CHECK (cierre > inicio)
);

CREATE TABLE poa_flujo_mensual (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    carga_id   INTEGER NOT NULL REFERENCES poa_cargas(id),
    mes        TEXT    NOT NULL,                -- AAAA-MM
    orden      INTEGER NOT NULL,
    concepto   TEXT    NOT NULL CHECK (concepto IN ('SALDO_INICIAL','COBROS_CARNE','COBROS_GALLINAZA','INSUMOS',
                                                    'MOD','CIF','ADMINISTRACION','COMERCIALIZACION','CAPEX',
                                                    'SALDO_FINAL')),
    monto_cent INTEGER NOT NULL,
    UNIQUE (carga_id, mes, concepto)
);

CREATE TABLE poa_presupuesto_area (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    carga_id       INTEGER NOT NULL REFERENCES poa_cargas(id),
    orden          INTEGER NOT NULL,
    grupo          TEXT    NOT NULL CHECK (grupo IN ('INGRESOS','COSTOS_DIRECTOS','GASTOS','CAPEX')),
    objeto         TEXT    NOT NULL,
    area           TEXT    NOT NULL,
    ejercicio_cent INTEGER NOT NULL,
    previo_cent    INTEGER NOT NULL,
    poa_cent       INTEGER NOT NULL,
    UNIQUE (carga_id, orden)
);

-- ---------------------------------------------------------------------------
-- 4. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_p13_respaldos_solo_depurar BEFORE UPDATE ON respaldos
WHEN NOT (OLD.estado = 'VIGENTE' AND NEW.estado = 'DEPURADO' AND NEW.depurado_en IS NOT NULL
          AND NEW.sha256 = OLD.sha256 AND NEW.archivo = OLD.archivo AND NEW.tipo = OLD.tipo
          AND NEW.referencia = OLD.referencia AND NEW.conteos_json = OLD.conteos_json)
BEGIN SELECT RAISE(ABORT, 'El registro de respaldo no se modifica (solo puede marcarse como depurado).'); END;
CREATE TRIGGER trg_p13_respaldos_no_delete BEFORE DELETE ON respaldos
BEGIN SELECT RAISE(ABORT, 'El registro de respaldo no se borra.'); END;

CREATE TRIGGER trg_p13_actas_solo_visto_cv BEFORE UPDATE ON actas_restauracion
WHEN NOT (OLD.verificado_por IS NULL AND NEW.verificado_por IS NOT NULL AND NEW.verificado_en IS NOT NULL
          AND NEW.resultado = OLD.resultado AND NEW.verificaciones_json = OLD.verificaciones_json
          AND NEW.respaldo_id = OLD.respaldo_id AND NEW.fecha = OLD.fecha)
BEGIN SELECT RAISE(ABORT, 'El acta de restauración no se modifica (solo admite el visto del Consejo de Vigilancia).'); END;
CREATE TRIGGER trg_p13_actas_no_delete BEFORE DELETE ON actas_restauracion
BEGIN SELECT RAISE(ABORT, 'El acta de restauración no se borra.'); END;

CREATE TRIGGER trg_p13_solicitudes_solo_resolver BEFORE UPDATE ON solicitudes_parametro
WHEN NOT (OLD.estado = 'SOLICITADA' AND NEW.estado IN ('APROBADA','RECHAZADA')
          AND NEW.clave = OLD.clave AND NEW.valor_nuevo = OLD.valor_nuevo
          AND NEW.ejercicio_desde = OLD.ejercicio_desde)
BEGIN SELECT RAISE(ABORT, 'La solicitud de cambio de parámetro ya fue resuelta o no se modifica.'); END;
CREATE TRIGGER trg_p13_solicitudes_no_delete BEFORE DELETE ON solicitudes_parametro
BEGIN SELECT RAISE(ABORT, 'La solicitud de cambio de parámetro no se borra.'); END;

-- El historial de parámetros se conserva: ninguna vigencia se borra.
CREATE TRIGGER trg_p13_parametros_no_delete BEFORE DELETE ON parametros
BEGIN SELECT RAISE(ABORT, 'Los parámetros no se borran: registre una nueva vigencia.'); END;

CREATE TRIGGER trg_p13_poa_cargas_no_update BEFORE UPDATE ON poa_cargas
BEGIN SELECT RAISE(ABORT, 'La carga del POA no se modifica: registre una nueva versión.'); END;
CREATE TRIGGER trg_p13_poa_cargas_no_delete BEFORE DELETE ON poa_cargas
BEGIN SELECT RAISE(ABORT, 'La carga del POA no se borra.'); END;
CREATE TRIGGER trg_p13_poa_calendario_no_update BEFORE UPDATE ON poa_calendario
BEGIN SELECT RAISE(ABORT, 'El calendario del POA no se modifica.'); END;
CREATE TRIGGER trg_p13_poa_calendario_no_delete BEFORE DELETE ON poa_calendario
BEGIN SELECT RAISE(ABORT, 'El calendario del POA no se borra.'); END;
CREATE TRIGGER trg_p13_poa_flujo_no_update BEFORE UPDATE ON poa_flujo_mensual
BEGIN SELECT RAISE(ABORT, 'El flujo mensual del POA no se modifica.'); END;
CREATE TRIGGER trg_p13_poa_flujo_no_delete BEFORE DELETE ON poa_flujo_mensual
BEGIN SELECT RAISE(ABORT, 'El flujo mensual del POA no se borra.'); END;
CREATE TRIGGER trg_p13_poa_area_no_update BEFORE UPDATE ON poa_presupuesto_area
BEGIN SELECT RAISE(ABORT, 'El presupuesto por área del POA no se modifica.'); END;
CREATE TRIGGER trg_p13_poa_area_no_delete BEFORE DELETE ON poa_presupuesto_area
BEGIN SELECT RAISE(ABORT, 'El presupuesto por área del POA no se borra.'); END;
