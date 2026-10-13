-- ============================================================================
-- ERP POLLOLANDIA - Migración 10 (Fase 10: Contabilidad general, cierres y
-- estados financieros, Ciclo 5 — Información Gerencial)
-- Lista de verificación de cierre mensual (FIN-INF-01) y paquete de cierre
-- mensual archivado (FIN-INF-03 y FIN-INF-04, PR-FIN-10 paso 7); cierre anual
-- al 30 de junio (8.1.01.01 -> 3.1.03.02); estados financieros emitidos
-- (MAN-006 §18-19) con huella; plazos de presentación de 60 y 90 días
-- (PR-FIN-11; Ley 356 Art. 55); distribución de excedentes (POL-01; CI-FIN-02)
-- con Informe Previo de Fiscalización INF-CV-04 y liquidaciones individuales
-- por socio. Montos en centavos (*_cent). Ningún registro se borra.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CIERRE MENSUAL (PR-FIN-10)
-- ---------------------------------------------------------------------------
CREATE TABLE listas_cierre (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo_id       INTEGER NOT NULL REFERENCES periodos(id),
    fecha            TEXT    NOT NULL,
    detalle_json     TEXT    NOT NULL,
    pendientes       INTEGER NOT NULL CHECK (pendientes >= 0),
    elaborado_por    TEXT    NOT NULL,
    cargo            TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);
CREATE INDEX idx_listas_cierre_periodo ON listas_cierre(periodo_id);

CREATE TABLE cierres_mensuales (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo_id          INTEGER NOT NULL UNIQUE REFERENCES periodos(id),
    lista_id            INTEGER NOT NULL REFERENCES listas_cierre(id),
    costeo_json         TEXT    NOT NULL,
    resultados_json     TEXT    NOT NULL,
    cerrado_por         TEXT    NOT NULL,
    cerrado_en          TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. CIERRE ANUAL (30 de junio) Y PLAZOS DE PRESENTACIÓN
-- ---------------------------------------------------------------------------
CREATE TABLE cierres_anuales (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio            INTEGER NOT NULL UNIQUE REFERENCES ejercicios(ejercicio),
    fecha                TEXT    NOT NULL,
    resultado_cent       INTEGER NOT NULL,
    asiento_cierre_id    INTEGER NOT NULL REFERENCES asientos(id),
    asiento_traslado_id  INTEGER REFERENCES asientos(id),
    autorizacion_id      INTEGER NOT NULL REFERENCES autorizaciones(id),
    registrado_por       TEXT    NOT NULL,
    registrado_en        TEXT    NOT NULL,
    ejercicio_cerrado_por TEXT,
    ejercicio_cerrado_en  TEXT
);

CREATE TABLE estados_emitidos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio        INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    fecha_corte      TEXT    NOT NULL,
    motivo           TEXT    NOT NULL CHECK (motivo IN ('PRESENTACION_CONSEJOS','APROBACION_ASAMBLEA')),
    contenido_json   TEXT    NOT NULL,
    huella           TEXT    NOT NULL,
    emitido_por      TEXT    NOT NULL,
    emitido_en       TEXT    NOT NULL
);

CREATE TABLE presentaciones_eeff (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio               INTEGER NOT NULL UNIQUE REFERENCES ejercicios(ejercicio),
    fecha_cierre            TEXT    NOT NULL,
    limite_consejos         TEXT    NOT NULL,
    limite_asamblea         TEXT    NOT NULL,
    fecha_consejos          TEXT,
    acta_ca_ref             TEXT,
    estado_emitido_id       INTEGER REFERENCES estados_emitidos(id),
    presentado_por          TEXT,
    dentro_plazo_consejos   INTEGER CHECK (dentro_plazo_consejos IN (0,1)),
    fecha_asamblea          TEXT,
    acta_asamblea_ref       TEXT,
    inf_cv_08_ref           TEXT,
    aprobado_por            TEXT,
    dentro_plazo_asamblea   INTEGER CHECK (dentro_plazo_asamblea IN (0,1)),
    registrado_en           TEXT    NOT NULL,
    CHECK (limite_asamblea >= limite_consejos)
);

-- ---------------------------------------------------------------------------
-- 3. DISTRIBUCIÓN DE EXCEDENTES (POL-01; CI-FIN-02) E INF-CV-04
-- ---------------------------------------------------------------------------
CREATE TABLE distribuciones_excedentes (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio                   INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    fecha_propuesta             TEXT    NOT NULL,
    excedente_cent              INTEGER NOT NULL CHECK (excedente_cent > 0),
    pct_reserva_legal           TEXT    NOT NULL,
    reserva_legal_cent          INTEGER NOT NULL CHECK (reserva_legal_cent >= 0),
    pct_educacion               TEXT    NOT NULL,
    educacion_cent              INTEGER NOT NULL CHECK (educacion_cent >= 0),
    reinversion_cent            INTEGER NOT NULL CHECK (reinversion_cent >= 0),
    capitalizacion_cent         INTEGER NOT NULL CHECK (capitalizacion_cent >= 0),
    distribuible_cent           INTEGER NOT NULL CHECK (distribuible_cent >= 0),
    criterio                    TEXT    NOT NULL CHECK (criterio IN ('IGUALITARIO','PARTICIPACION')),
    estado                      TEXT    NOT NULL DEFAULT 'PROPUESTA'
                                CHECK (estado IN ('PROPUESTA','VERIFICADA_CV','APROBADA','PAGADA','ANULADA')),
    motivo_anulacion            TEXT,
    propuesto_por               TEXT    NOT NULL,
    inf_cv_04_id                INTEGER,
    autorizacion_asamblea_id    INTEGER REFERENCES autorizaciones(id),
    autorizacion_capitalizacion_id INTEGER REFERENCES autorizaciones(id),
    acta_ref                    TEXT,
    fecha_aprobacion            TEXT,
    asiento_apropiacion_id      INTEGER REFERENCES asientos(id),
    asiento_capitalizacion_id   INTEGER REFERENCES asientos(id),
    asiento_distribucion_id     INTEGER REFERENCES asientos(id),
    aprobado_por                TEXT,
    movimiento_pago_id          INTEGER REFERENCES movimientos_bancarios(id),
    fecha_pago                  TEXT,
    pagado_por                  TEXT,
    registrado_en               TEXT    NOT NULL,
    CHECK (reserva_legal_cent + educacion_cent + reinversion_cent + capitalizacion_cent + distribuible_cent
           = excedente_cent)
);
CREATE UNIQUE INDEX ux_distribucion_vigente ON distribuciones_excedentes(ejercicio) WHERE estado <> 'ANULADA';

CREATE TABLE informes_cv_04 (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    numero              INTEGER NOT NULL,
    anio                INTEGER NOT NULL,
    distribucion_id     INTEGER NOT NULL REFERENCES distribuciones_excedentes(id),
    fecha               TEXT    NOT NULL,
    verificaciones_json TEXT    NOT NULL,
    dictamen            TEXT    NOT NULL CHECK (dictamen IN ('FAVORABLE','CON_OBSERVACIONES')),
    observaciones       TEXT,
    elaborado_por       TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    UNIQUE (anio, numero)
);

CREATE TABLE liquidaciones_socios (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    distribucion_id   INTEGER NOT NULL REFERENCES distribuciones_excedentes(id),
    socio_id          INTEGER NOT NULL REFERENCES socios(id),
    base              TEXT    NOT NULL,
    bruto_cent        INTEGER NOT NULL CHECK (bruto_cent >= 0),
    deducciones_cent  INTEGER NOT NULL DEFAULT 0 CHECK (deducciones_cent >= 0),
    neto_cent         INTEGER NOT NULL CHECK (neto_cent >= 0),
    estado            TEXT    NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE','FIRMADA','PAGADA','ANULADA')),
    firmada_por       TEXT,
    firmada_en        TEXT,
    pagada_en         TEXT,
    UNIQUE (distribucion_id, socio_id),
    CHECK (neto_cent = bruto_cent - deducciones_cent)
);

-- ---------------------------------------------------------------------------
-- 4. INMUTABILIDAD (los registros de cierre nunca se borran ni se reescriben)
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_listas_cierre_no_update BEFORE UPDATE ON listas_cierre
BEGIN SELECT RAISE(ABORT, 'La lista FIN-INF-01 no se modifica: prepare una nueva.'); END;
CREATE TRIGGER trg_listas_cierre_no_delete BEFORE DELETE ON listas_cierre
BEGIN SELECT RAISE(ABORT, 'La lista FIN-INF-01 no se borra.'); END;
CREATE TRIGGER trg_cierres_mensuales_no_update BEFORE UPDATE ON cierres_mensuales
BEGIN SELECT RAISE(ABORT, 'El paquete de cierre mensual no se modifica.'); END;
CREATE TRIGGER trg_cierres_mensuales_no_delete BEFORE DELETE ON cierres_mensuales
BEGIN SELECT RAISE(ABORT, 'El paquete de cierre mensual no se borra.'); END;

CREATE TRIGGER trg_cierres_anuales_no_delete BEFORE DELETE ON cierres_anuales
BEGIN SELECT RAISE(ABORT, 'El cierre anual no se borra.'); END;
CREATE TRIGGER trg_cierres_anuales_fijo BEFORE UPDATE ON cierres_anuales
WHEN OLD.resultado_cent <> NEW.resultado_cent OR OLD.asiento_cierre_id <> NEW.asiento_cierre_id
  OR IFNULL(OLD.asiento_traslado_id, 0) <> IFNULL(NEW.asiento_traslado_id, 0)
  OR OLD.fecha <> NEW.fecha OR OLD.ejercicio_cerrado_en IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'El cierre anual solo admite registrar el cierre del ejercicio, una vez.'); END;

CREATE TRIGGER trg_estados_emitidos_no_update BEFORE UPDATE ON estados_emitidos
BEGIN SELECT RAISE(ABORT, 'Los estados financieros emitidos no se modifican.'); END;
CREATE TRIGGER trg_estados_emitidos_no_delete BEFORE DELETE ON estados_emitidos
BEGIN SELECT RAISE(ABORT, 'Los estados financieros emitidos no se borran.'); END;

CREATE TRIGGER trg_presentaciones_no_delete BEFORE DELETE ON presentaciones_eeff
BEGIN SELECT RAISE(ABORT, 'El registro de presentación no se borra.'); END;
CREATE TRIGGER trg_presentaciones_fijo BEFORE UPDATE ON presentaciones_eeff
WHEN OLD.limite_consejos <> NEW.limite_consejos OR OLD.limite_asamblea <> NEW.limite_asamblea
  OR OLD.fecha_cierre <> NEW.fecha_cierre
  OR (OLD.fecha_consejos IS NOT NULL AND IFNULL(NEW.fecha_consejos, '') <> OLD.fecha_consejos)
  OR (OLD.fecha_asamblea IS NOT NULL AND IFNULL(NEW.fecha_asamblea, '') <> OLD.fecha_asamblea)
BEGIN SELECT RAISE(ABORT, 'Las fechas de presentación y aprobación no se reescriben.'); END;

CREATE TRIGGER trg_distribuciones_no_delete BEFORE DELETE ON distribuciones_excedentes
BEGIN SELECT RAISE(ABORT, 'La distribución de excedentes no se borra: se anula.'); END;
CREATE TRIGGER trg_distribuciones_cifras BEFORE UPDATE ON distribuciones_excedentes
WHEN OLD.excedente_cent <> NEW.excedente_cent OR OLD.reserva_legal_cent <> NEW.reserva_legal_cent
  OR OLD.educacion_cent <> NEW.educacion_cent OR OLD.reinversion_cent <> NEW.reinversion_cent
  OR OLD.capitalizacion_cent <> NEW.capitalizacion_cent OR OLD.distribuible_cent <> NEW.distribuible_cent
  OR OLD.ejercicio <> NEW.ejercicio OR OLD.estado IN ('PAGADA','ANULADA')
BEGIN SELECT RAISE(ABORT, 'Las cifras de la distribución no se modifican; una distribución pagada o anulada queda fija.'); END;

CREATE TRIGGER trg_inf_cv_04_no_update BEFORE UPDATE ON informes_cv_04
BEGIN SELECT RAISE(ABORT, 'El INF-CV-04 no se modifica.'); END;
CREATE TRIGGER trg_inf_cv_04_no_delete BEFORE DELETE ON informes_cv_04
BEGIN SELECT RAISE(ABORT, 'El INF-CV-04 no se borra.'); END;

CREATE TRIGGER trg_liquidaciones_socios_no_delete BEFORE DELETE ON liquidaciones_socios
BEGIN SELECT RAISE(ABORT, 'La liquidación individual no se borra.'); END;
CREATE TRIGGER trg_liquidaciones_socios_cifras BEFORE UPDATE ON liquidaciones_socios
WHEN OLD.bruto_cent <> NEW.bruto_cent OR OLD.deducciones_cent <> NEW.deducciones_cent
  OR OLD.neto_cent <> NEW.neto_cent OR OLD.socio_id <> NEW.socio_id OR OLD.estado IN ('PAGADA','ANULADA')
BEGIN SELECT RAISE(ABORT, 'La liquidación individual solo admite su firma, pago o anulación.'); END;
