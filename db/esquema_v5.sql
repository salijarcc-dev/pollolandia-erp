-- ============================================================================
-- ERP POLLOLANDIA - Migración 5 (Fase 5: Personal y planillas, Ciclo 2)
-- Personal fijo con contrato e historial salarial; planilla mensual
-- (FIN-EGR-09) y beneficios sociales (FIN-EGR-10); hojas de tiempo
-- (FIN-EGR-12), conciliación pagado vs absorbido (FIN-EGR-13) y verificación
-- cruzada de horas (CI-FIN-14); personal eventual: circular (CIR-EGR-01),
-- autorización (FIN-EGR-06), contratos (CONT-EGR-01) y liquidación de
-- jornales (FIN-EGR-08); liquidación final de beneficios (FIN-EGR-11).
-- Montos en centavos (*_cent); horas y unidades en enteros o milésimas.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. PERSONAL FIJO
-- ---------------------------------------------------------------------------
CREATE TABLE personal (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre             TEXT    NOT NULL CHECK (length(trim(nombre)) > 0),
    ci                 TEXT    NOT NULL UNIQUE CHECK (length(trim(ci)) > 0),
    cargo              TEXT    NOT NULL CHECK (length(trim(cargo)) > 0),
    area               TEXT    NOT NULL CHECK (area IN ('GRANJA','ADMINISTRACION')),
    polivalente        INTEGER NOT NULL DEFAULT 0 CHECK (polivalente IN (0,1)),
    tipo_contrato      TEXT    NOT NULL CHECK (tipo_contrato IN ('INDEFINIDO','PLAZO_FIJO')),
    contrato_ref       TEXT    NOT NULL CHECK (length(trim(contrato_ref)) > 0),
    fecha_ingreso      TEXT    NOT NULL,
    nua_gestora        TEXT,
    matricula_cns      TEXT,
    fecha_retiro       TEXT,
    motivo_retiro      TEXT,
    estado             TEXT    NOT NULL CHECK (estado IN ('ACTIVO','RETIRADO')),
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    actualizado_en     TEXT,
    CHECK (area = 'GRANJA' OR polivalente = 0),
    CHECK (fecha_retiro IS NULL OR fecha_retiro >= fecha_ingreso)
);

CREATE TABLE historial_salarios (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_id        INTEGER NOT NULL REFERENCES personal(id),
    vigente_desde      TEXT    NOT NULL,
    salario_cent       INTEGER NOT NULL CHECK (salario_cent > 0),
    motivo             TEXT    NOT NULL,
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    UNIQUE (personal_id, vigente_desde)
);

-- ---------------------------------------------------------------------------
-- 2. PLANILLA MENSUAL (FIN-EGR-09) Y BENEFICIOS SOCIALES (FIN-EGR-10)
-- ---------------------------------------------------------------------------
CREATE TABLE planillas (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    anio                   INTEGER NOT NULL,
    mes                    INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    ejercicio              INTEGER NOT NULL,
    total_ganado_cent      INTEGER NOT NULL,
    aporte_laboral_cent    INTEGER NOT NULL,
    liquido_cent           INTEGER NOT NULL,
    aporte_patronal_cent   INTEGER NOT NULL,
    aguinaldo_cent         INTEGER NOT NULL,
    indemnizacion_cent     INTEGER NOT NULL,
    estado                 TEXT    NOT NULL CHECK (estado IN ('PREPARADA','AUTORIZADA','CONTABILIZADA','ANULADA')),
    elaborado_por          TEXT    NOT NULL,
    elaborado_en           TEXT    NOT NULL,
    autorizado_por         TEXT,
    autorizado_en          TEXT,
    asiento_id             INTEGER REFERENCES asientos(id),
    contabilizado_en       TEXT,
    pago_sueldos_mov_id    INTEGER REFERENCES movimientos_bancarios(id),
    sueldos_pagados_en     TEXT,
    pago_aportes_mov_id    INTEGER REFERENCES movimientos_bancarios(id),
    aportes_pagados_en     TEXT,
    motivo_anulacion       TEXT
);
CREATE UNIQUE INDEX ux_planilla_mes ON planillas(anio, mes) WHERE estado <> 'ANULADA';

CREATE TABLE planilla_lineas (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    planilla_id            INTEGER NOT NULL REFERENCES planillas(id),
    personal_id            INTEGER NOT NULL REFERENCES personal(id),
    area                   TEXT    NOT NULL CHECK (area IN ('GRANJA','ADMINISTRACION')),
    dias_trabajados        INTEGER NOT NULL CHECK (dias_trabajados BETWEEN 0 AND 31),
    salario_mensual_cent   INTEGER NOT NULL,
    haber_basico_cent      INTEGER NOT NULL CHECK (haber_basico_cent >= 0),
    bonos_cent             INTEGER NOT NULL CHECK (bonos_cent >= 0),
    total_ganado_cent      INTEGER NOT NULL CHECK (total_ganado_cent >= 0),
    aporte_laboral_cent    INTEGER NOT NULL CHECK (aporte_laboral_cent >= 0),
    liquido_cent           INTEGER NOT NULL,
    aporte_patronal_cent   INTEGER NOT NULL CHECK (aporte_patronal_cent >= 0),
    aguinaldo_cent         INTEGER NOT NULL CHECK (aguinaldo_cent >= 0),
    indemnizacion_cent     INTEGER NOT NULL CHECK (indemnizacion_cent >= 0),
    pct_carne              TEXT    NOT NULL,
    fuente_prorrateo       TEXT    NOT NULL CHECK (fuente_prorrateo IN ('NO_APLICA','HOJAS_TIEMPO','DEFECTO')),
    horas_crianza          INTEGER NOT NULL DEFAULT 0,
    horas_alimento         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (planilla_id, personal_id)
);

-- ---------------------------------------------------------------------------
-- 3. HOJAS DE TIEMPO (FIN-EGR-12), CONCILIACIÓN (FIN-EGR-13), CI-FIN-14
-- ---------------------------------------------------------------------------
CREATE TABLE hojas_tiempo (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_id         INTEGER NOT NULL REFERENCES personal(id),
    fecha               TEXT    NOT NULL,
    horas_crianza       INTEGER NOT NULL CHECK (horas_crianza BETWEEN 0 AND 24),
    horas_alimento      INTEGER NOT NULL CHECK (horas_alimento BETWEEN 0 AND 24),
    firmada_trabajador  INTEGER NOT NULL CHECK (firmada_trabajador IN (0,1)),
    regularizacion      TEXT,
    validado_por        TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    UNIQUE (personal_id, fecha),
    CHECK (horas_crianza + horas_alimento BETWEEN 1 AND 24)
);

CREATE TABLE conciliaciones_mod (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    planilla_id            INTEGER NOT NULL UNIQUE REFERENCES planillas(id),
    costo_pagado_cent      INTEGER NOT NULL,
    absorbido_carne_cent   INTEGER NOT NULL,
    absorbido_alimento_cent INTEGER NOT NULL,
    diferencia_cent        INTEGER NOT NULL,
    observacion_ci_fin_14  TEXT,
    elaborado_por          TEXT    NOT NULL,
    elaborado_en           TEXT    NOT NULL,
    fiscalizado_por        TEXT,
    fiscalizado_en         TEXT
);

CREATE TABLE verificaciones_horas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    desde               TEXT    NOT NULL,
    hasta               TEXT    NOT NULL,
    criterio_min_horas_qq TEXT  NOT NULL,
    criterio_max_horas_qq TEXT  NOT NULL,
    criterio_detalle    TEXT    NOT NULL,
    semanas_periodo     INTEGER NOT NULL,
    semanas_muestra     INTEGER NOT NULL,
    observaciones       INTEGER NOT NULL,
    verificado_por      TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    CHECK (hasta >= desde)
);

CREATE TABLE verificacion_semanas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    verificacion_id     INTEGER NOT NULL REFERENCES verificaciones_horas(id),
    semana_inicio       TEXT    NOT NULL,
    horas_alimento      INTEGER NOT NULL,
    quintales_mil       INTEGER NOT NULL CHECK (quintales_mil >= 0),
    horas_por_qq        TEXT,
    observada           INTEGER NOT NULL CHECK (observada IN (0,1)),
    detalle             TEXT,
    UNIQUE (verificacion_id, semana_inicio)
);

-- ---------------------------------------------------------------------------
-- 4. PERSONAL EVENTUAL
-- ---------------------------------------------------------------------------
CREATE TABLE circulares_cierre (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    numero                INTEGER NOT NULL,
    anio                  INTEGER NOT NULL,
    fecha_emision         TEXT    NOT NULL,
    fecha_faenado         TEXT    NOT NULL,
    lote                  TEXT    NOT NULL CHECK (length(trim(lote)) > 0),
    aves                  INTEGER NOT NULL CHECK (aves > 0),
    mortalidad_pct        TEXT    NOT NULL,
    emitida_por           TEXT    NOT NULL,
    acuse_gg_por          TEXT,
    acuse_gg_en           TEXT,
    acuse_jf_por          TEXT,
    acuse_jf_en           TEXT,
    registrado_en         TEXT    NOT NULL,
    UNIQUE (anio, numero),
    CHECK (fecha_faenado > fecha_emision)
);

CREATE TABLE autorizaciones_eventuales (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    circular_id           INTEGER NOT NULL UNIQUE REFERENCES circulares_cierre(id),
    fecha_lunes           TEXT    NOT NULL,
    trabajadores          INTEGER NOT NULL CHECK (trabajadores > 0),
    aves                  INTEGER NOT NULL CHECK (aves > 0),
    tarifa_cent           INTEGER NOT NULL CHECK (tarifa_cent > 0),
    presupuesto_cent      INTEGER NOT NULL CHECK (presupuesto_cent > 0),
    ejercicio             INTEGER NOT NULL,
    ciclo                 INTEGER NOT NULL,
    disponibilidad_json   TEXT    NOT NULL,
    compromiso_id         INTEGER REFERENCES compromisos(id),
    autorizacion_id       INTEGER NOT NULL REFERENCES autorizaciones(id),
    estado                TEXT    NOT NULL CHECK (estado IN ('SOLICITADA','AUTORIZADA','LIQUIDADA','ANULADA')),
    autorizado_por        TEXT,
    por_suplencia         INTEGER NOT NULL DEFAULT 0 CHECK (por_suplencia IN (0,1)),
    solicitado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    actualizado_en        TEXT
);

CREATE TABLE contratos_eventuales (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    autorizacion_eventual_id INTEGER NOT NULL REFERENCES autorizaciones_eventuales(id),
    nombre                TEXT    NOT NULL CHECK (length(trim(nombre)) > 0),
    ci                    TEXT    NOT NULL CHECK (length(trim(ci)) > 0),
    fecha                 TEXT    NOT NULL,
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    UNIQUE (autorizacion_eventual_id, ci)
);

CREATE TABLE liquidaciones_eventuales (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    autorizacion_eventual_id INTEGER NOT NULL UNIQUE REFERENCES autorizaciones_eventuales(id),
    fecha                 TEXT    NOT NULL,
    unidades              INTEGER NOT NULL CHECK (unidades > 0),
    total_cent            INTEGER NOT NULL CHECK (total_cent > 0),
    presupuesto_cent      INTEGER NOT NULL CHECK (presupuesto_cent > 0),
    dpf                   TEXT    NOT NULL,
    semaforo              TEXT    NOT NULL CHECK (semaforo IN ('BUENO','NORMAL','MALO')),
    asiento_id            INTEGER NOT NULL REFERENCES asientos(id),
    pago_autorizado_por   TEXT,
    pago_autorizado_en    TEXT,
    movimiento_bancario_id INTEGER REFERENCES movimientos_bancarios(id),
    pagado_en             TEXT,
    liquidado_por         TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL
);

CREATE TABLE liquidacion_eventual_lineas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    liquidacion_id        INTEGER NOT NULL REFERENCES liquidaciones_eventuales(id),
    contrato_id           INTEGER NOT NULL REFERENCES contratos_eventuales(id),
    unidades              INTEGER NOT NULL CHECK (unidades >= 0),
    tarifa_cent           INTEGER NOT NULL,
    total_cent            INTEGER NOT NULL CHECK (total_cent >= 0),
    UNIQUE (liquidacion_id, contrato_id)
);

-- ---------------------------------------------------------------------------
-- 5. LIQUIDACIÓN FINAL DE BENEFICIOS (FIN-EGR-11)
-- ---------------------------------------------------------------------------
CREATE TABLE liquidaciones_finales (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    personal_id              INTEGER NOT NULL UNIQUE REFERENCES personal(id),
    fecha_retiro             TEXT    NOT NULL,
    fecha_preparacion        TEXT    NOT NULL,
    fecha_limite             TEXT    NOT NULL,
    dentro_de_plazo          INTEGER NOT NULL CHECK (dentro_de_plazo IN (0,1)),
    causa_retraso            TEXT,
    dias_servicio            INTEGER NOT NULL,
    promedio_cent            INTEGER NOT NULL,
    indemnizacion_cent       INTEGER NOT NULL,
    vacaciones_dias          TEXT    NOT NULL,
    vacaciones_cent          INTEGER NOT NULL,
    aguinaldo_cent           INTEGER NOT NULL,
    salario_dias             INTEGER NOT NULL,
    salario_cent             INTEGER NOT NULL,
    aporte_laboral_cent      INTEGER NOT NULL,
    total_cent               INTEGER NOT NULL,
    estado                   TEXT    NOT NULL CHECK (estado IN ('PREPARADA','AUTORIZADA','CONTABILIZADA')),
    elaborado_por            TEXT    NOT NULL,
    autorizado_por           TEXT,
    autorizado_en            TEXT,
    asiento_id               INTEGER REFERENCES asientos(id),
    registrado_en            TEXT    NOT NULL,
    CHECK (dentro_de_plazo = 1 OR length(trim(IFNULL(causa_retraso,''))) > 0)
);

-- ---------------------------------------------------------------------------
-- 6. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_personal_no_delete BEFORE DELETE ON personal
BEGIN SELECT RAISE(ABORT, 'El personal no se borra: registre su retiro.'); END;
CREATE TRIGGER trg_salarios_no_update BEFORE UPDATE ON historial_salarios
BEGIN SELECT RAISE(ABORT, 'El historial salarial no se modifica: registre un nuevo salario.'); END;
CREATE TRIGGER trg_salarios_no_delete BEFORE DELETE ON historial_salarios
BEGIN SELECT RAISE(ABORT, 'El historial salarial no se borra.'); END;

CREATE TRIGGER trg_planillas_no_delete BEFORE DELETE ON planillas
BEGIN SELECT RAISE(ABORT, 'Las planillas no se borran: anúlelas antes de contabilizar.'); END;
CREATE TRIGGER trg_planilla_totales_fijos BEFORE UPDATE ON planillas
WHEN OLD.total_ganado_cent <> NEW.total_ganado_cent OR OLD.liquido_cent <> NEW.liquido_cent
  OR OLD.aporte_patronal_cent <> NEW.aporte_patronal_cent OR OLD.estado = 'ANULADA'
  OR (OLD.estado = 'CONTABILIZADA' AND NEW.estado <> 'CONTABILIZADA')
BEGIN SELECT RAISE(ABORT, 'Una planilla no altera sus totales; contabilizada o anulada no cambia de estado.'); END;
CREATE TRIGGER trg_planilla_lineas_no_update BEFORE UPDATE ON planilla_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas de la planilla no se modifican: anule y prepare otra.'); END;
CREATE TRIGGER trg_planilla_lineas_no_delete BEFORE DELETE ON planilla_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas de la planilla no se borran.'); END;

CREATE TRIGGER trg_hojas_no_delete BEFORE DELETE ON hojas_tiempo
BEGIN SELECT RAISE(ABORT, 'Las hojas de tiempo no se borran.'); END;
CREATE TRIGGER trg_hojas_solo_regularizar BEFORE UPDATE ON hojas_tiempo
WHEN OLD.horas_crianza <> NEW.horas_crianza OR OLD.horas_alimento <> NEW.horas_alimento OR OLD.fecha <> NEW.fecha
  OR OLD.firmada_trabajador = 1
BEGIN SELECT RAISE(ABORT, 'Una hoja de tiempo solo se regulariza con la firma omitida; no cambia sus horas.'); END;

CREATE TRIGGER trg_conc_mod_no_delete BEFORE DELETE ON conciliaciones_mod
BEGIN SELECT RAISE(ABORT, 'Las conciliaciones de mano de obra no se borran.'); END;
CREATE TRIGGER trg_conc_mod_fija BEFORE UPDATE ON conciliaciones_mod
WHEN OLD.diferencia_cent <> NEW.diferencia_cent OR OLD.costo_pagado_cent <> NEW.costo_pagado_cent
  OR OLD.fiscalizado_por IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'Una conciliación fiscalizada no se modifica ni cambia sus montos.'); END;
CREATE TRIGGER trg_verif_no_update BEFORE UPDATE ON verificaciones_horas
BEGIN SELECT RAISE(ABORT, 'Una verificación cruzada no se modifica.'); END;
CREATE TRIGGER trg_verif_no_delete BEFORE DELETE ON verificaciones_horas
BEGIN SELECT RAISE(ABORT, 'Las verificaciones cruzadas no se borran.'); END;
CREATE TRIGGER trg_verif_sem_no_update BEFORE UPDATE ON verificacion_semanas
BEGIN SELECT RAISE(ABORT, 'Las semanas verificadas no se modifican.'); END;

CREATE TRIGGER trg_circular_no_delete BEFORE DELETE ON circulares_cierre
BEGIN SELECT RAISE(ABORT, 'Las circulares no se borran.'); END;
CREATE TRIGGER trg_circular_fija BEFORE UPDATE ON circulares_cierre
WHEN OLD.fecha_faenado <> NEW.fecha_faenado OR OLD.aves <> NEW.aves OR OLD.fecha_emision <> NEW.fecha_emision
BEGIN SELECT RAISE(ABORT, 'Una circular solo admite los acuses de recibo.'); END;
CREATE TRIGGER trg_aut_eventual_no_delete BEFORE DELETE ON autorizaciones_eventuales
BEGIN SELECT RAISE(ABORT, 'Las autorizaciones de personal eventual no se borran.'); END;
CREATE TRIGGER trg_contratos_no_delete BEFORE DELETE ON contratos_eventuales
BEGIN SELECT RAISE(ABORT, 'Los contratos eventuales no se borran.'); END;
CREATE TRIGGER trg_liq_eventual_no_delete BEFORE DELETE ON liquidaciones_eventuales
BEGIN SELECT RAISE(ABORT, 'Las liquidaciones de jornales no se borran.'); END;
CREATE TRIGGER trg_liq_eventual_fija BEFORE UPDATE ON liquidaciones_eventuales
WHEN OLD.total_cent <> NEW.total_cent OR OLD.asiento_id <> NEW.asiento_id OR OLD.movimiento_bancario_id IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'Una liquidación de jornales no cambia su monto; pagada no se modifica.'); END;
CREATE TRIGGER trg_liq_eventual_lineas_no_update BEFORE UPDATE ON liquidacion_eventual_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas de la liquidación no se modifican.'); END;

CREATE TRIGGER trg_liq_final_no_delete BEFORE DELETE ON liquidaciones_finales
BEGIN SELECT RAISE(ABORT, 'Las liquidaciones finales no se borran.'); END;
CREATE TRIGGER trg_liq_final_fija BEFORE UPDATE ON liquidaciones_finales
WHEN OLD.total_cent <> NEW.total_cent OR OLD.estado = 'CONTABILIZADA'
BEGIN SELECT RAISE(ABORT, 'Una liquidación final no cambia su total; contabilizada no se modifica.'); END;
