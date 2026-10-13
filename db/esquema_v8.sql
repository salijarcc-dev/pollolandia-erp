-- ============================================================================
-- ERP POLLOLANDIA - Migración 8 (Fase 8: Activos fijos y CAPEX)
-- Plan anual de inversiones CAPEX (FIN-TES-01) con análisis de ROA y
-- compatibilidad con el SME (FIN-TES-02/03), aprobación del CA y ratificación
-- de la Asamblea; inversiones no previstas y variaciones (POL-02 Art. 1 y 4;
-- CI-FIN-09); autorización de activación (FIN-TRA-08); ficha individual con
-- código interno (FIN-TRA-09); depreciación y amortización mensual en línea
-- recta (FIN-TRA-10; PC-06); bajas con acta y verificación del CV (PR-FIN-07);
-- informe semestral de ejecución CAPEX (FIN-TES-04) y acta de verificación
-- física (INF-CV-01). Referencia presupuestaria D.5 (Manual Presupuestario).
-- Montos en centavos (*_cent).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. REFERENCIA PRESUPUESTARIA D.5 (Plan CAPEX 2026-2030)
-- ---------------------------------------------------------------------------
CREATE TABLE capex_referencia (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio      INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    orden          INTEGER NOT NULL,
    activo         TEXT    NOT NULL,
    categoria      TEXT    NOT NULL,
    monto_cent     INTEGER NOT NULL CHECK (monto_cent > 0),
    ciclo          INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    justificacion  TEXT    NOT NULL,
    fuente         TEXT    NOT NULL,
    UNIQUE (ejercicio, orden)
);

-- ---------------------------------------------------------------------------
-- 2. PLAN ANUAL DE INVERSIONES CAPEX (FIN-TES-01, FIN-TES-02, FIN-TES-03)
-- ---------------------------------------------------------------------------
CREATE TABLE planes_capex (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio                INTEGER NOT NULL UNIQUE REFERENCES ejercicios(ejercicio),
    fecha_elaboracion        TEXT    NOT NULL,
    fecha_limite             TEXT    NOT NULL,
    fuera_de_plazo           INTEGER NOT NULL CHECK (fuera_de_plazo IN (0,1)),
    estado                   TEXT    NOT NULL DEFAULT 'BORRADOR'
                             CHECK (estado IN ('BORRADOR','PRESENTADO','APROBADO','RATIFICADO')),
    elaborado_por            TEXT    NOT NULL,
    -- FIN-TES-02: hoja de cálculo de indicadores (ROA y SME)
    analisis_fecha           TEXT,
    utilidad_neta_cent       INTEGER,
    activos_totales_cent     INTEGER,
    inversion_total_cent     INTEGER,
    roa                      TEXT,
    roa_semaforo             TEXT CHECK (roa_semaforo IS NULL OR roa_semaforo IN ('VERDE','AMARILLO','ROJO')),
    saldo_proyectado_cent    INTEGER,
    sme_cent                 INTEGER,
    saldo_post_inversion_cent INTEGER,
    compatible_sme           INTEGER CHECK (compatible_sme IS NULL OR compatible_sme IN (0,1)),
    bajo_umbral_compromiso   INTEGER CHECK (bajo_umbral_compromiso IS NULL OR bajo_umbral_compromiso IN (0,1)),
    analizado_por            TEXT,
    -- FIN-TES-03: informe técnico-financiero al Consejo de Administración
    resumen                  TEXT,
    recomendacion            TEXT,
    presentado_por           TEXT,
    presentado_en            TEXT,
    -- Aprobación del CA y ratificación de la Asamblea
    observacion_ca           TEXT,
    aprobado_por             TEXT,
    acta_ca                  TEXT,
    aprobado_en              TEXT,
    ratificado_por           TEXT,
    acta_ag                  TEXT,
    ratificado_en            TEXT,
    registrado_en            TEXT    NOT NULL,
    CHECK (estado = 'BORRADOR' OR (presentado_por IS NOT NULL AND roa IS NOT NULL)),
    CHECK (estado NOT IN ('APROBADO','RATIFICADO') OR (aprobado_por IS NOT NULL AND acta_ca IS NOT NULL)),
    CHECK (estado <> 'RATIFICADO' OR (ratificado_por IS NOT NULL AND acta_ag IS NOT NULL))
);

CREATE TABLE capex_lineas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id               INTEGER NOT NULL REFERENCES planes_capex(id),
    activo                TEXT    NOT NULL CHECK (length(trim(activo)) > 0),
    categoria             TEXT    NOT NULL,
    costo_estimado_cent   INTEGER NOT NULL CHECK (costo_estimado_cent > 0),
    proveedor_referencia  TEXT,
    justificacion         TEXT    NOT NULL CHECK (length(trim(justificacion)) > 0),
    -- POL-02 Art. 2: financiamiento 100 % con recursos propios
    fuente                TEXT    NOT NULL DEFAULT 'RECURSOS_PROPIOS' CHECK (fuente = 'RECURSOS_PROPIOS'),
    ciclo                 INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    fecha_estimada        TEXT    NOT NULL,
    previsto              INTEGER NOT NULL CHECK (previsto IN (0,1)),
    autorizacion_id       INTEGER REFERENCES autorizaciones(id),
    referencia_d5_id      INTEGER REFERENCES capex_referencia(id),
    estado                TEXT    NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','RETIRADA')),
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (previsto = 1 OR autorizacion_id IS NOT NULL)
);
CREATE INDEX idx_capex_lineas_plan ON capex_lineas(plan_id);

-- ---------------------------------------------------------------------------
-- 3. AUTORIZACIÓN DE ACTIVACIÓN (FIN-TRA-08) Y VARIACIONES (POL-02 Art. 4)
-- ---------------------------------------------------------------------------
CREATE TABLE activaciones (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    capex_linea_id           INTEGER NOT NULL REFERENCES capex_lineas(id),
    descripcion              TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    categoria                TEXT    NOT NULL,
    destino                  TEXT    NOT NULL CHECK (destino IN ('OPERATIVO','ADMINISTRATIVO')),
    precio_cent              INTEGER NOT NULL CHECK (precio_cent > 0),
    flete_cent               INTEGER NOT NULL DEFAULT 0 CHECK (flete_cent >= 0),
    instalacion_cent         INTEGER NOT NULL DEFAULT 0 CHECK (instalacion_cent >= 0),
    costo_cent               INTEGER NOT NULL CHECK (costo_cent > 0),
    fecha_disponible         TEXT    NOT NULL,
    proveedor                TEXT    NOT NULL,
    documento_ref            TEXT    NOT NULL,
    ubicacion                TEXT    NOT NULL,
    saldo_bancario_cent      INTEGER NOT NULL,
    aprobacion_ag_saldo_ref  TEXT,
    autorizado_por           TEXT    NOT NULL,
    autorizado_en            TEXT    NOT NULL,
    estado                   TEXT    NOT NULL DEFAULT 'AUTORIZADA' CHECK (estado IN ('AUTORIZADA','REGISTRADA','ANULADA')),
    activo_id                INTEGER REFERENCES activos(id),
    motivo_anulacion         TEXT,
    CHECK (precio_cent + flete_cent + instalacion_cent = costo_cent),
    CHECK (estado <> 'REGISTRADA' OR activo_id IS NOT NULL),
    CHECK (estado <> 'ANULADA' OR motivo_anulacion IS NOT NULL)
);
CREATE INDEX idx_activaciones_linea ON activaciones(capex_linea_id);

CREATE TABLE capex_variaciones (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    activacion_id         INTEGER NOT NULL UNIQUE REFERENCES activaciones(id),
    capex_linea_id        INTEGER NOT NULL REFERENCES capex_lineas(id),
    aprobado_cent         INTEGER NOT NULL CHECK (aprobado_cent > 0),
    ejecutado_cent        INTEGER NOT NULL CHECK (ejecutado_cent > 0),
    variacion_cent        INTEGER NOT NULL,
    variacion_pct         TEXT    NOT NULL,
    informa_ca            INTEGER NOT NULL CHECK (informa_ca IN (0,1)),
    informe_ca_ref        TEXT,
    requiere_ag           INTEGER NOT NULL CHECK (requiere_ag IN (0,1)),
    autorizacion_id       INTEGER REFERENCES autorizaciones(id),
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (informa_ca = 0 OR informe_ca_ref IS NOT NULL),
    CHECK (requiere_ag = 0 OR autorizacion_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- 4. FICHA INDIVIDUAL DEL ACTIVO (FIN-TRA-09) — libro de inventario
-- ---------------------------------------------------------------------------
CREATE TABLE activos (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo_interno        TEXT    NOT NULL UNIQUE,
    descripcion           TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    categoria             TEXT    NOT NULL,
    cuenta_activo         TEXT    NOT NULL REFERENCES cuentas(codigo),
    cuenta_depreciacion   TEXT    REFERENCES cuentas(codigo),
    destino               TEXT    NOT NULL CHECK (destino IN ('OPERATIVO','ADMINISTRATIVO')),
    centro_costo          TEXT    REFERENCES centros_costo(codigo),
    sufijo                TEXT    REFERENCES sufijos_costo(sufijo),
    cuenta_gasto          TEXT    REFERENCES cuentas(codigo),
    costo_cent            INTEGER NOT NULL CHECK (costo_cent > 0),
    tasa_anual            TEXT    NOT NULL,
    clave_tasa            TEXT    NOT NULL,
    fecha_activacion      TEXT    NOT NULL,
    origen                TEXT    NOT NULL CHECK (origen IN ('APERTURA','CAPEX')),
    activacion_id         INTEGER UNIQUE REFERENCES activaciones(id),
    asiento_id            INTEGER NOT NULL REFERENCES asientos(id),
    proveedor             TEXT,
    documento_ref         TEXT,
    ubicacion             TEXT    NOT NULL,
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (origen = 'APERTURA' OR activacion_id IS NOT NULL),
    CHECK (destino = 'ADMINISTRATIVO' OR centro_costo IS NULL OR sufijo IS NOT NULL),
    CHECK (centro_costo IS NULL OR cuenta_gasto IS NULL)
);
CREATE INDEX idx_activos_categoria ON activos(categoria);

-- ---------------------------------------------------------------------------
-- 5. DEPRECIACIÓN Y AMORTIZACIÓN MENSUAL (FIN-TRA-10; PC-06)
-- ---------------------------------------------------------------------------
CREATE TABLE depreciacion_meses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    anio            INTEGER NOT NULL,
    mes             INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    fecha           TEXT    NOT NULL,
    total_cent      INTEGER NOT NULL CHECK (total_cent > 0),
    asiento_id      INTEGER NOT NULL REFERENCES asientos(id),
    registrado_por  TEXT    NOT NULL,
    registrado_en   TEXT    NOT NULL,
    UNIQUE (anio, mes, asiento_id)
);

CREATE TABLE depreciaciones (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    activo_id          INTEGER NOT NULL REFERENCES activos(id),
    mes_id             INTEGER NOT NULL REFERENCES depreciacion_meses(id),
    periodo            TEXT    NOT NULL,
    fecha              TEXT    NOT NULL,
    cuota_cent         INTEGER NOT NULL CHECK (cuota_cent > 0),
    acumulada_cent     INTEGER NOT NULL CHECK (acumulada_cent > 0),
    valor_neto_cent    INTEGER NOT NULL CHECK (valor_neto_cent >= 0),
    meses_transcurridos INTEGER NOT NULL CHECK (meses_transcurridos >= 1),
    UNIQUE (activo_id, periodo)
);
CREATE INDEX idx_depreciaciones_mes ON depreciaciones(mes_id);

-- ---------------------------------------------------------------------------
-- 6. BAJA DE ACTIVOS (PR-FIN-07; PC-05; PC-06 §V.C)
-- ---------------------------------------------------------------------------
CREATE TABLE bajas_activos (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    activo_id               INTEGER NOT NULL REFERENCES activos(id),
    fecha                   TEXT    NOT NULL,
    causa                   TEXT    NOT NULL CHECK (causa IN ('VENTA','OBSOLESCENCIA','DETERIORO','SINIESTRO','ROBO',
                                                               'FIN_VIDA_UTIL','OTRA')),
    detalle_causa           TEXT    NOT NULL CHECK (length(trim(detalle_causa)) > 0),
    destino_final           TEXT    NOT NULL CHECK (length(trim(destino_final)) > 0),
    valor_realizacion_cent  INTEGER NOT NULL CHECK (valor_realizacion_cent >= 0),
    cuenta_cobro            TEXT    REFERENCES cuentas(codigo),
    costo_cent              INTEGER NOT NULL,
    acumulada_cent          INTEGER NOT NULL,
    valor_neto_cent         INTEGER NOT NULL CHECK (valor_neto_cent >= 0),
    resultado_cent          INTEGER NOT NULL,
    acta_ref                TEXT    NOT NULL,
    autorizado_por          TEXT    NOT NULL,
    autorizado_en           TEXT    NOT NULL,
    requiere_cv             INTEGER NOT NULL CHECK (requiere_cv IN (0,1)),
    verificado_por          TEXT,
    verificado_en           TEXT,
    conforme_cv             INTEGER CHECK (conforme_cv IS NULL OR conforme_cv IN (0,1)),
    observacion_cv          TEXT,
    estado                  TEXT    NOT NULL CHECK (estado IN ('PENDIENTE_CV','OBSERVADA','AUTORIZADA','CONTABILIZADA')),
    asiento_id              INTEGER REFERENCES asientos(id),
    contabilizado_por       TEXT,
    contabilizado_en        TEXT,
    CHECK (costo_cent - acumulada_cent = valor_neto_cent),
    CHECK (valor_realizacion_cent - valor_neto_cent = resultado_cent),
    CHECK (valor_realizacion_cent = 0 OR cuenta_cobro IS NOT NULL),
    CHECK (estado <> 'CONTABILIZADA' OR asiento_id IS NOT NULL),
    CHECK (requiere_cv = 0 OR estado = 'PENDIENTE_CV' OR verificado_por IS NOT NULL)
);
CREATE INDEX idx_bajas_activos_activo ON bajas_activos(activo_id);
-- Una sola acta vigente por activo (la observada por el CV permite emitir otra).
CREATE UNIQUE INDEX ux_bajas_activos_vigente ON bajas_activos(activo_id) WHERE estado <> 'OBSERVADA';

-- ---------------------------------------------------------------------------
-- 7. INFORME SEMESTRAL DE EJECUCIÓN CAPEX (FIN-TES-04)
-- ---------------------------------------------------------------------------
CREATE TABLE informes_capex (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio        INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    semestre         INTEGER NOT NULL CHECK (semestre IN (1,2)),
    desde            TEXT    NOT NULL,
    hasta            TEXT    NOT NULL,
    fecha            TEXT    NOT NULL,
    fuera_de_plazo   INTEGER NOT NULL CHECK (fuera_de_plazo IN (0,1)),
    aprobado_cent    INTEGER NOT NULL,
    ejecutado_cent   INTEGER NOT NULL,
    contenido_json   TEXT    NOT NULL,
    observacion      TEXT,
    presentado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    UNIQUE (ejercicio, semestre)
);

-- ---------------------------------------------------------------------------
-- 8. ACTA DE VERIFICACIÓN FÍSICA DE ACTIVOS (INF-CV-01)
-- ---------------------------------------------------------------------------
CREATE TABLE verificaciones_fisicas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio          INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    fecha              TEXT    NOT NULL,
    activos_verificados INTEGER NOT NULL CHECK (activos_verificados > 0),
    faltantes          INTEGER NOT NULL CHECK (faltantes >= 0),
    tasas_distintas    INTEGER NOT NULL CHECK (tasas_distintas >= 0),
    observacion        TEXT,
    verificado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL
);

CREATE TABLE verificacion_detalle (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    verificacion_id      INTEGER NOT NULL REFERENCES verificaciones_fisicas(id),
    activo_id            INTEGER NOT NULL REFERENCES activos(id),
    existe               INTEGER NOT NULL CHECK (existe IN (0,1)),
    estado_conservacion  TEXT    CHECK (estado_conservacion IS NULL OR estado_conservacion IN ('BUENO','REGULAR','MALO')),
    ubicacion_verificada TEXT,
    valor_neto_cent      INTEGER NOT NULL,
    tasa_ficha           TEXT    NOT NULL,
    tasa_politica        TEXT    NOT NULL,
    tasa_coincide        INTEGER NOT NULL CHECK (tasa_coincide IN (0,1)),
    observacion          TEXT,
    UNIQUE (verificacion_id, activo_id),
    CHECK (existe = 0 OR estado_conservacion IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- 9. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_capex_referencia_no_update BEFORE UPDATE ON capex_referencia
BEGIN SELECT RAISE(ABORT, 'La referencia presupuestaria D.5 no se modifica.'); END;
CREATE TRIGGER trg_capex_referencia_no_delete BEFORE DELETE ON capex_referencia
BEGIN SELECT RAISE(ABORT, 'La referencia presupuestaria D.5 no se borra.'); END;
CREATE TRIGGER trg_planes_capex_no_delete BEFORE DELETE ON planes_capex
BEGIN SELECT RAISE(ABORT, 'El Plan CAPEX no se borra.'); END;
CREATE TRIGGER trg_planes_capex_ratificado BEFORE UPDATE ON planes_capex
WHEN OLD.estado = 'RATIFICADO' OR OLD.ejercicio <> NEW.ejercicio
BEGIN SELECT RAISE(ABORT, 'El Plan CAPEX ratificado por la Asamblea no se modifica.'); END;
CREATE TRIGGER trg_capex_lineas_no_delete BEFORE DELETE ON capex_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del Plan CAPEX no se borran: se retiran.'); END;
CREATE TRIGGER trg_capex_lineas_solo_estado BEFORE UPDATE ON capex_lineas
WHEN OLD.estado <> 'VIGENTE' OR OLD.costo_estimado_cent <> NEW.costo_estimado_cent OR OLD.plan_id <> NEW.plan_id
  OR OLD.categoria <> NEW.categoria OR OLD.previsto <> NEW.previsto OR OLD.activo <> NEW.activo
BEGIN SELECT RAISE(ABORT, 'La línea CAPEX solo admite su retiro, una sola vez.'); END;
CREATE TRIGGER trg_activaciones_no_delete BEFORE DELETE ON activaciones
BEGIN SELECT RAISE(ABORT, 'La autorización de activación no se borra.'); END;
CREATE TRIGGER trg_activaciones_un_cambio BEFORE UPDATE ON activaciones
WHEN OLD.estado <> 'AUTORIZADA' OR OLD.costo_cent <> NEW.costo_cent OR OLD.destino <> NEW.destino
  OR OLD.capex_linea_id <> NEW.capex_linea_id OR OLD.fecha_disponible <> NEW.fecha_disponible
BEGIN SELECT RAISE(ABORT, 'La activación autorizada solo se registra o se anula, una sola vez.'); END;
CREATE TRIGGER trg_capex_variaciones_no_update BEFORE UPDATE ON capex_variaciones
BEGIN SELECT RAISE(ABORT, 'Las variaciones CAPEX no se modifican.'); END;
CREATE TRIGGER trg_capex_variaciones_no_delete BEFORE DELETE ON capex_variaciones
BEGIN SELECT RAISE(ABORT, 'Las variaciones CAPEX no se borran.'); END;
CREATE TRIGGER trg_activos_no_update BEFORE UPDATE ON activos
BEGIN SELECT RAISE(ABORT, 'La ficha del activo no se modifica: su historia queda en depreciaciones, bajas y verificaciones.'); END;
CREATE TRIGGER trg_activos_no_delete BEFORE DELETE ON activos
BEGIN SELECT RAISE(ABORT, 'La ficha del activo no se borra: se da de baja con acta.'); END;
CREATE TRIGGER trg_dep_meses_no_update BEFORE UPDATE ON depreciacion_meses
BEGIN SELECT RAISE(ABORT, 'La planilla de depreciación no se modifica.'); END;
CREATE TRIGGER trg_dep_meses_no_delete BEFORE DELETE ON depreciacion_meses
BEGIN SELECT RAISE(ABORT, 'La planilla de depreciación no se borra.'); END;
CREATE TRIGGER trg_depreciaciones_no_update BEFORE UPDATE ON depreciaciones
BEGIN SELECT RAISE(ABORT, 'La depreciación registrada no se modifica.'); END;
CREATE TRIGGER trg_depreciaciones_no_delete BEFORE DELETE ON depreciaciones
BEGIN SELECT RAISE(ABORT, 'La depreciación registrada no se borra.'); END;
CREATE TRIGGER trg_bajas_activos_no_delete BEFORE DELETE ON bajas_activos
BEGIN SELECT RAISE(ABORT, 'El acta de baja no se borra.'); END;
CREATE TRIGGER trg_bajas_activos_fija BEFORE UPDATE ON bajas_activos
WHEN OLD.estado = 'CONTABILIZADA' OR OLD.valor_neto_cent <> NEW.valor_neto_cent
  OR OLD.valor_realizacion_cent <> NEW.valor_realizacion_cent OR OLD.activo_id <> NEW.activo_id
  OR OLD.fecha <> NEW.fecha
BEGIN SELECT RAISE(ABORT, 'El acta de baja solo admite la verificación del CV y su contabilización.'); END;
CREATE TRIGGER trg_informes_capex_no_update BEFORE UPDATE ON informes_capex
BEGIN SELECT RAISE(ABORT, 'El informe FIN-TES-04 no se modifica.'); END;
CREATE TRIGGER trg_informes_capex_no_delete BEFORE DELETE ON informes_capex
BEGIN SELECT RAISE(ABORT, 'El informe FIN-TES-04 no se borra.'); END;
CREATE TRIGGER trg_verif_fisicas_no_update BEFORE UPDATE ON verificaciones_fisicas
BEGIN SELECT RAISE(ABORT, 'El acta INF-CV-01 no se modifica.'); END;
CREATE TRIGGER trg_verif_fisicas_no_delete BEFORE DELETE ON verificaciones_fisicas
BEGIN SELECT RAISE(ABORT, 'El acta INF-CV-01 no se borra.'); END;
CREATE TRIGGER trg_verif_detalle_no_update BEFORE UPDATE ON verificacion_detalle
BEGIN SELECT RAISE(ABORT, 'El detalle del acta INF-CV-01 no se modifica.'); END;
CREATE TRIGGER trg_verif_detalle_no_delete BEFORE DELETE ON verificacion_detalle
BEGIN SELECT RAISE(ABORT, 'El detalle del acta INF-CV-01 no se borra.'); END;
