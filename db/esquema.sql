-- ============================================================================
-- ERP POLLOLANDIA - Esquema de base de datos, versión 1 (Fase 1)
-- Motor: SQLite (PRAGMA foreign_keys=ON, journal_mode=WAL).
-- Este script lo ejecuta db/migraciones.py dentro de UNA transacción.
-- La tabla esquema_version la crea el propio runner de migraciones.
-- Convenciones:
--   * Montos monetarios: INTEGER en CENTAVOS (columnas *_cent). Evita errores
--     de punto flotante y permite SUM() exacto. Conversión en db/conexion.py.
--   * Fechas: TEXT ISO 'AAAA-MM-DD'. Fecha y hora: 'AAAA-MM-DDTHH:MM:SS'.
--   * Porcentajes en parametros: fracción decimal ('0.1721' = 17,21 %).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CATÁLOGO CONTABLE (Plan de Cuentas v3: 4 niveles)
--    nivel 1 = grupo (1), 2 = clasificación (1.1), 3 = subgrupo (1.1.01),
--    nivel 4 = cuenta de movimiento (1.1.01.01).
-- ---------------------------------------------------------------------------
CREATE TABLE cuentas (
    codigo          TEXT    PRIMARY KEY,
    nombre          TEXT    NOT NULL,
    nivel           INTEGER NOT NULL CHECK (nivel BETWEEN 1 AND 4),
    grupo           INTEGER NOT NULL CHECK (grupo BETWEEN 1 AND 9),
    clasificacion   TEXT    NOT NULL
                    CHECK (clasificacion IN ('CORRIENTE','NO_CORRIENTE','OPERATIVO','NO_OPERATIVO','NA')),
    naturaleza      TEXT    CHECK (naturaleza IN ('DEUDORA','ACREEDORA','DUAL')),
    es_movimiento   INTEGER NOT NULL DEFAULT 0 CHECK (es_movimiento IN (0,1)),
    uso_desde_2027  INTEGER NOT NULL DEFAULT 0 CHECK (uso_desde_2027 IN (0,1)),
    padre_codigo    TEXT    REFERENCES cuentas(codigo),
    origen          TEXT    NOT NULL DEFAULT 'PLAN_V3' CHECK (origen IN ('PLAN_V3','DESCRIPCION')),
    activa          INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
    observacion     TEXT,
    CHECK (length(codigo) = CASE nivel WHEN 1 THEN 1 WHEN 2 THEN 3 WHEN 3 THEN 6 ELSE 9 END),
    CHECK ((nivel = 4 AND es_movimiento = 1 AND naturaleza IS NOT NULL)
        OR (nivel < 4 AND es_movimiento = 0)),
    CHECK ((nivel = 1 AND padre_codigo IS NULL) OR (nivel > 1 AND padre_codigo IS NOT NULL)),
    CHECK (CAST(substr(codigo, 1, 1) AS INTEGER) = grupo)
);
CREATE INDEX idx_cuentas_padre ON cuentas(padre_codigo);
CREATE INDEX idx_cuentas_nivel ON cuentas(nivel, grupo);

-- ---------------------------------------------------------------------------
-- 2. SISTEMA MATRICIAL DE COSTOS (Grupo 6)
-- ---------------------------------------------------------------------------
CREATE TABLE centros_costo (
    codigo                  TEXT PRIMARY KEY REFERENCES cuentas(codigo),
    nombre                  TEXT NOT NULL,
    cuenta_producto_term    TEXT NOT NULL REFERENCES cuentas(codigo),
    cuenta_costo_ventas     TEXT NOT NULL REFERENCES cuentas(codigo),
    uso_desde_2027          INTEGER NOT NULL DEFAULT 0 CHECK (uso_desde_2027 IN (0,1))
);

CREATE TABLE sufijos_costo (
    sufijo                  TEXT PRIMARY KEY CHECK (sufijo GLOB '[0-9][0-9]'),
    nombre                  TEXT NOT NULL,
    elemento                TEXT NOT NULL CHECK (elemento IN ('MPD','MOD','CIF','PERDIDA')),
    cuenta_contrapartida    TEXT REFERENCES cuentas(codigo),
    cuenta_contrapartida_2  TEXT REFERENCES cuentas(codigo),
    uso_desde_2027          INTEGER NOT NULL DEFAULT 0 CHECK (uso_desde_2027 IN (0,1)),
    observacion             TEXT
);

-- Qué sufijos puede usar cada centro de costo (relación N:M).
CREATE TABLE sufijo_centro (
    sufijo        TEXT NOT NULL REFERENCES sufijos_costo(sufijo),
    centro_costo  TEXT NOT NULL REFERENCES centros_costo(codigo),
    PRIMARY KEY (sufijo, centro_costo)
);

-- ---------------------------------------------------------------------------
-- 3. PARÁMETROS NORMATIVOS (regla 2: ninguna cifra normativa en el código)
-- ---------------------------------------------------------------------------
CREATE TABLE parametros (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    clave              TEXT    NOT NULL,
    valor              TEXT    NOT NULL,
    tipo               TEXT    NOT NULL CHECK (tipo IN ('ENTERO','DECIMAL','TEXTO','BOOLEANO')),
    ejercicio_desde    INTEGER NOT NULL CHECK (ejercicio_desde BETWEEN 2000 AND 9998),
    ejercicio_hasta    INTEGER CHECK (ejercicio_hasta IS NULL OR ejercicio_hasta >= ejercicio_desde),
    fuente_documental  TEXT    NOT NULL CHECK (length(trim(fuente_documental)) > 0),
    descripcion        TEXT,
    UNIQUE (clave, ejercicio_desde)
);
CREATE INDEX idx_parametros_clave ON parametros(clave);

-- Impide vigencias solapadas de una misma clave.
CREATE TRIGGER trg_parametros_sin_solape_ins
BEFORE INSERT ON parametros
WHEN EXISTS (SELECT 1 FROM parametros p
              WHERE p.clave = NEW.clave
                AND p.ejercicio_desde <= COALESCE(NEW.ejercicio_hasta, 9999)
                AND COALESCE(p.ejercicio_hasta, 9999) >= NEW.ejercicio_desde)
BEGIN
    SELECT RAISE(ABORT, 'Vigencia solapada: ya existe un valor del parámetro para esos ejercicios.');
END;

CREATE TRIGGER trg_parametros_sin_solape_upd
BEFORE UPDATE OF clave, ejercicio_desde, ejercicio_hasta ON parametros
WHEN EXISTS (SELECT 1 FROM parametros p
              WHERE p.clave = NEW.clave AND p.id <> NEW.id
                AND p.ejercicio_desde <= COALESCE(NEW.ejercicio_hasta, 9999)
                AND COALESCE(p.ejercicio_hasta, 9999) >= NEW.ejercicio_desde)
BEGIN
    SELECT RAISE(ABORT, 'Vigencia solapada: ya existe un valor del parámetro para esos ejercicios.');
END;

-- ---------------------------------------------------------------------------
-- 4. EJERCICIOS Y PERÍODOS (1-jul a 30-jun; períodos mensuales)
-- ---------------------------------------------------------------------------
CREATE TABLE ejercicios (
    ejercicio     INTEGER PRIMARY KEY,
    fecha_inicio  TEXT NOT NULL,
    fecha_fin     TEXT NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','CERRADO')),
    cerrado_en    TEXT,
    cerrado_por   TEXT,
    CHECK (fecha_fin > fecha_inicio)
);

CREATE TABLE periodos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio     INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    numero        INTEGER NOT NULL CHECK (numero BETWEEN 1 AND 12),
    fecha_inicio  TEXT NOT NULL,
    fecha_fin     TEXT NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','CERRADO')),
    cerrado_en    TEXT,
    cerrado_por   TEXT,
    UNIQUE (ejercicio, numero),
    CHECK (fecha_fin >= fecha_inicio)
);
CREATE INDEX idx_periodos_fechas ON periodos(fecha_inicio, fecha_fin);

-- ---------------------------------------------------------------------------
-- 5. SOCIOS
-- ---------------------------------------------------------------------------
CREATE TABLE socios (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    numero             INTEGER NOT NULL UNIQUE CHECK (numero > 0),
    nombre_completo    TEXT    NOT NULL,
    ci                 TEXT    NOT NULL UNIQUE,
    ci_expedido        TEXT    NOT NULL DEFAULT 'LP',
    certificados       INTEGER NOT NULL CHECK (certificados > 0),
    aporte_cent        INTEGER NOT NULL CHECK (aporte_cent > 0),
    estado             TEXT    NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','RETIRADO')),
    cargo_cooperativo  TEXT,
    fecha_ingreso      TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 6. SEGURIDAD: roles, permisos, usuarios
-- ---------------------------------------------------------------------------
CREATE TABLE roles (
    codigo                      TEXT PRIMARY KEY CHECK (codigo IN ('GG','JF','UT','CA','CV','AG')),
    nombre                      TEXT NOT NULL,
    descripcion                 TEXT NOT NULL,
    solo_lectura_transaccional  INTEGER NOT NULL DEFAULT 0 CHECK (solo_lectura_transaccional IN (0,1))
);

CREATE TABLE permisos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rol       TEXT NOT NULL REFERENCES roles(codigo),
    recurso   TEXT NOT NULL,
    accion    TEXT NOT NULL CHECK (accion IN
              ('VER','CREAR','EDITAR','REVERSAR','AUTORIZAR','VERIFICAR','APROBAR','COFIRMAR')),
    fuente    TEXT NOT NULL,
    UNIQUE (rol, recurso, accion)
);

CREATE TABLE usuarios (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_usuario            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nombre_completo           TEXT NOT NULL,
    rol                       TEXT NOT NULL REFERENCES roles(codigo),
    socio_id                  INTEGER REFERENCES socios(id),
    password_hash             TEXT NOT NULL,
    activo                    INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    intentos_fallidos         INTEGER NOT NULL DEFAULT 0 CHECK (intentos_fallidos >= 0),
    bloqueado_hasta           TEXT,
    ultimo_cambio_password    TEXT NOT NULL,
    debe_cambiar_password     INTEGER NOT NULL DEFAULT 1 CHECK (debe_cambiar_password IN (0,1)),
    creado_en                 TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 7. LIBRO DIARIO (partida doble). Nunca se borra: solo se reversa.
-- ---------------------------------------------------------------------------
CREATE TABLE asientos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo                TEXT NOT NULL CHECK (tipo IN
                        ('APERTURA','INGRESO','EGRESO','TRASPASO','AJUSTE','CIERRE')),
    numero              INTEGER NOT NULL CHECK (numero > 0),
    fecha               TEXT NOT NULL,
    periodo_id          INTEGER NOT NULL REFERENCES periodos(id),
    glosa               TEXT NOT NULL CHECK (length(trim(glosa)) > 0),
    origen              TEXT NOT NULL,
    documento_ref       TEXT,
    estado              TEXT NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','REVERSADO')),
    reversa_de          INTEGER REFERENCES asientos(id),
    creado_por          TEXT NOT NULL,
    creado_en           TEXT NOT NULL,
    UNIQUE (tipo, numero)
);
CREATE INDEX idx_asientos_fecha ON asientos(fecha);
CREATE INDEX idx_asientos_periodo ON asientos(periodo_id);

CREATE TABLE asiento_detalle (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    asiento_id     INTEGER NOT NULL REFERENCES asientos(id),
    linea          INTEGER NOT NULL CHECK (linea > 0),
    cuenta_codigo  TEXT    NOT NULL REFERENCES cuentas(codigo),
    debe_cent      INTEGER NOT NULL DEFAULT 0 CHECK (debe_cent >= 0),
    haber_cent     INTEGER NOT NULL DEFAULT 0 CHECK (haber_cent >= 0),
    centro_costo   TEXT    REFERENCES centros_costo(codigo),
    sufijo         TEXT    REFERENCES sufijos_costo(sufijo),
    glosa_linea    TEXT,
    UNIQUE (asiento_id, linea),
    CHECK (debe_cent = 0 OR haber_cent = 0),
    CHECK (debe_cent > 0 OR haber_cent > 0),
    CHECK (sufijo IS NULL OR centro_costo IS NOT NULL)
);
CREATE INDEX idx_detalle_cuenta ON asiento_detalle(cuenta_codigo);
CREATE INDEX idx_detalle_centro ON asiento_detalle(centro_costo, sufijo);

-- Solo cuentas de movimiento (nivel 4) y activas admiten líneas.
CREATE TRIGGER trg_detalle_solo_movimiento
BEFORE INSERT ON asiento_detalle
WHEN (SELECT es_movimiento * activa FROM cuentas WHERE codigo = NEW.cuenta_codigo) <> 1
BEGIN
    SELECT RAISE(ABORT, 'La cuenta no admite movimientos (no es de nivel 4 o está inactiva).');
END;

-- El sufijo debe pertenecer al centro de costo indicado.
CREATE TRIGGER trg_detalle_sufijo_centro
BEFORE INSERT ON asiento_detalle
WHEN NEW.sufijo IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM sufijo_centro sc
                  WHERE sc.sufijo = NEW.sufijo AND sc.centro_costo = NEW.centro_costo)
BEGIN
    SELECT RAISE(ABORT, 'El sufijo no es aplicable al centro de costo indicado.');
END;

-- Inmutabilidad: nunca se borra ni se modifica el diario; solo se reversa.
CREATE TRIGGER trg_asientos_no_delete
BEFORE DELETE ON asientos
BEGIN
    SELECT RAISE(ABORT, 'Los asientos no se borran: use la reversión.');
END;

CREATE TRIGGER trg_asientos_solo_estado
BEFORE UPDATE ON asientos
WHEN OLD.id <> NEW.id OR OLD.tipo <> NEW.tipo OR OLD.numero <> NEW.numero
  OR OLD.fecha <> NEW.fecha OR OLD.periodo_id <> NEW.periodo_id OR OLD.glosa <> NEW.glosa
  OR OLD.origen <> NEW.origen OR IFNULL(OLD.documento_ref,'') <> IFNULL(NEW.documento_ref,'')
  OR IFNULL(OLD.reversa_de,0) <> IFNULL(NEW.reversa_de,0)
  OR OLD.creado_por <> NEW.creado_por OR OLD.creado_en <> NEW.creado_en
  OR (OLD.estado = 'REVERSADO' AND NEW.estado <> 'REVERSADO')
BEGIN
    SELECT RAISE(ABORT, 'Un asiento solo puede cambiar de VIGENTE a REVERSADO.');
END;

CREATE TRIGGER trg_detalle_no_delete
BEFORE DELETE ON asiento_detalle
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de asiento no se borran: use la reversión.');
END;

CREATE TRIGGER trg_detalle_no_update
BEFORE UPDATE ON asiento_detalle
BEGIN
    SELECT RAISE(ABORT, 'Las líneas de asiento no se modifican: use la reversión.');
END;

-- Control de cuadre (consulta de diagnóstico; el servicio contable valida antes de insertar).
CREATE VIEW v_asientos_descuadrados AS
SELECT a.id AS asiento_id, a.tipo, a.numero,
       COALESCE(SUM(d.debe_cent), 0)  AS debe_cent,
       COALESCE(SUM(d.haber_cent), 0) AS haber_cent
  FROM asientos a
  LEFT JOIN asiento_detalle d ON d.asiento_id = a.id
 GROUP BY a.id
HAVING COUNT(d.id) < 2 OR COALESCE(SUM(d.debe_cent),0) <> COALESCE(SUM(d.haber_cent),0);

CREATE VIEW v_saldos_cuentas AS
SELECT c.codigo, c.nombre, c.naturaleza,
       COALESCE(SUM(d.debe_cent), 0)  AS debe_cent,
       COALESCE(SUM(d.haber_cent), 0) AS haber_cent,
       COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0) AS saldo_deudor_cent
  FROM cuentas c
  LEFT JOIN asiento_detalle d ON d.cuenta_codigo = c.codigo
 WHERE c.es_movimiento = 1
 GROUP BY c.codigo;

-- ---------------------------------------------------------------------------
-- 8. BITÁCORA DE AUDITORÍA (inmutable) Y ADJUNTOS DOCUMENTALES
-- ---------------------------------------------------------------------------
CREATE TABLE auditoria (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_hora       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
    usuario          TEXT NOT NULL,
    rol              TEXT,
    tabla            TEXT NOT NULL,
    registro_id      TEXT,
    accion           TEXT NOT NULL CHECK (accion IN
                     ('INSERT','UPDATE','DELETE','REVERSA','LOGIN','LOGOUT','AUTORIZACION',
                      'CIERRE','CARGA_INICIAL','OTRO')),
    valores_antes    TEXT,
    valores_despues  TEXT,
    detalle          TEXT
);
CREATE INDEX idx_auditoria_tabla ON auditoria(tabla, fecha_hora);

CREATE TRIGGER trg_auditoria_no_update
BEFORE UPDATE ON auditoria
BEGIN
    SELECT RAISE(ABORT, 'La bitácora de auditoría es inmutable.');
END;

CREATE TRIGGER trg_auditoria_no_delete
BEFORE DELETE ON auditoria
BEGIN
    SELECT RAISE(ABORT, 'La bitácora de auditoría es inmutable.');
END;

CREATE TABLE adjuntos_documentales (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tabla_origen     TEXT NOT NULL,
    registro_id      TEXT NOT NULL,
    codigo_documento TEXT,
    nombre_archivo   TEXT NOT NULL,
    ruta_relativa    TEXT NOT NULL,
    hash_sha256      TEXT NOT NULL CHECK (length(hash_sha256) = 64),
    clase_retencion  TEXT NOT NULL CHECK (clase_retencion IN
                     ('PERMANENTE','TEMPORAL_10','TEMPORAL_5','VIDA_UTIL_10','DESECHO_2')),
    expediente       TEXT,
    subido_por       TEXT NOT NULL,
    subido_en        TEXT NOT NULL
);
CREATE INDEX idx_adjuntos_origen ON adjuntos_documentales(tabla_origen, registro_id);

-- ---------------------------------------------------------------------------
-- 9. CONTROL DE SEMILLAS Y CONCILIACIÓN DE DISCREPANCIAS ENTRE DOCUMENTOS
-- ---------------------------------------------------------------------------
CREATE TABLE semillas_aplicadas (
    clave        TEXT PRIMARY KEY,
    aplicada_en  TEXT NOT NULL,
    registros    INTEGER NOT NULL
);

CREATE TABLE conciliacion_catalogo (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    orden     INTEGER NOT NULL UNIQUE,
    tipo      TEXT NOT NULL CHECK (tipo IN ('CATALOGO','APERTURA','NORMATIVA')),
    tema      TEXT NOT NULL,
    detalle   TEXT NOT NULL,
    decision  TEXT NOT NULL,
    fuente    TEXT NOT NULL
);
