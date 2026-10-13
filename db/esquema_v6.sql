-- ============================================================================
-- ERP POLLOLANDIA - Migración 6 (Fase 6: Operativo y trazabilidad, Ciclo 3)
-- Galpones; ciclos productivos de 73 días con su apertura (FIN-TRA-01) y sus
-- lotes de hasta 1.000 aves por galpón; bitácora diaria (FIN-TRA-02); alertas
-- sanitarias (CI-FIN-11); bajas por mortalidad (FIN-TRA-04); disposición
-- sanitaria (FIN-TRA-11); muestreo semanal de peso (POL-03 Art. 2); revisión
-- semanal de la Gerencia; faenado por lote (FIN-TRA-05, CI-FIN-12) con PMF e
-- ICA; conciliación de aves y trazabilidad QR «Vida Digna» (POL-03 Art. 3).
-- Montos en centavos (*_cent); kilogramos en milésimas (*_mil); m² en
-- centésimas (*_cm2 = m² x 100).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. GALPONES
-- ---------------------------------------------------------------------------
CREATE TABLE galpones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo           TEXT    NOT NULL UNIQUE CHECK (length(trim(codigo)) > 0),
    nombre           TEXT    NOT NULL CHECK (length(trim(nombre)) > 0),
    superficie_cm2   INTEGER NOT NULL CHECK (superficie_cm2 > 0),
    fotos_url        TEXT,
    activo           INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    actualizado_en   TEXT
);

-- ---------------------------------------------------------------------------
-- 2. CICLOS PRODUCTIVOS Y LOTES (FIN-TRA-01)
-- ---------------------------------------------------------------------------
CREATE TABLE ciclos_produccion (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio             INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    numero                INTEGER NOT NULL CHECK (numero BETWEEN 1 AND 12),
    fecha_inicio          TEXT    NOT NULL,
    fecha_cierre          TEXT    NOT NULL,
    aves_iniciales        INTEGER NOT NULL CHECK (aves_iniciales > 0),
    insumo_id             INTEGER NOT NULL REFERENCES insumos(id),
    proveedor             TEXT,
    certificado_sanitario TEXT,
    costo_pollitos_cent   INTEGER NOT NULL CHECK (costo_pollitos_cent > 0),
    kardex_id             INTEGER NOT NULL UNIQUE REFERENCES kardex(id),
    asiento_id            INTEGER NOT NULL REFERENCES asientos(id),
    estado                TEXT    NOT NULL CHECK (estado IN ('ABIERTO','FAENADO','CONCLUIDO')),
    recepcion_conforme_por TEXT,
    recepcion_conforme_en  TEXT,
    conciliacion_json     TEXT,
    faltante_autorizado_por TEXT,
    faltante_motivo       TEXT,
    concluido_por         TEXT,
    concluido_en          TEXT,
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    UNIQUE (ejercicio, numero)
);

CREATE TABLE lotes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id            INTEGER NOT NULL REFERENCES ciclos_produccion(id),
    codigo              TEXT    NOT NULL UNIQUE,
    galpon_id           INTEGER NOT NULL REFERENCES galpones(id),
    aves_iniciales      INTEGER NOT NULL CHECK (aves_iniciales > 0),
    densidad            TEXT    NOT NULL,
    qr_token            TEXT    NOT NULL UNIQUE,
    estado              TEXT    NOT NULL CHECK (estado IN ('EN_CRIANZA','FAENADO')),
    faenado_anticipado_informe TEXT,
    faenado_anticipado_por     TEXT,
    faenado_anticipado_en      TEXT,
    UNIQUE (ciclo_id, galpon_id)
);

-- ---------------------------------------------------------------------------
-- 3. BITÁCORA DIARIA (FIN-TRA-02) Y REVISIÓN SEMANAL DE LA GERENCIA
-- ---------------------------------------------------------------------------
CREATE TABLE bitacora_diaria (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id             INTEGER NOT NULL REFERENCES lotes(id),
    fecha               TEXT    NOT NULL,
    dia_ciclo           INTEGER NOT NULL CHECK (dia_ciclo > 0),
    fase                TEXT    NOT NULL CHECK (fase IN ('INICIO','CRECIMIENTO','ACABADO')),
    alimento_kg_mil     INTEGER NOT NULL CHECK (alimento_kg_mil >= 0),
    aves_muertas        INTEGER NOT NULL CHECK (aves_muertas >= 0),
    causa_presunta      TEXT,
    novedad_sanitaria   TEXT,
    tratamiento         TEXT,
    signos_alerta       INTEGER NOT NULL DEFAULT 0 CHECK (signos_alerta IN (0,1)),
    cumple_ci_fin_11    INTEGER NOT NULL CHECK (cumple_ci_fin_11 IN (0,1)),
    muertas_acum_ciclo  INTEGER NOT NULL CHECK (muertas_acum_ciclo >= 0),
    mortalidad_acum     TEXT    NOT NULL,
    semaforo            TEXT    NOT NULL CHECK (semaforo IN ('VERDE','AMARILLO','ROJO')),
    registrado_por      TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    UNIQUE (lote_id, fecha),
    CHECK (aves_muertas = 0 OR (causa_presunta IS NOT NULL AND length(trim(causa_presunta)) > 0))
);
CREATE INDEX idx_bitacora_fecha ON bitacora_diaria(fecha);

CREATE TABLE revisiones_bitacora (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id         INTEGER NOT NULL REFERENCES ciclos_produccion(id),
    semana           INTEGER NOT NULL CHECK (semana > 0),
    observacion      TEXT,
    revisado_por     TEXT    NOT NULL,
    revisado_en      TEXT    NOT NULL,
    UNIQUE (ciclo_id, semana)
);

-- ---------------------------------------------------------------------------
-- 4. ALERTAS SANITARIAS (CI-FIN-11, Pasos 4 y 5)
-- ---------------------------------------------------------------------------
CREATE TABLE alertas_sanitarias (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id            INTEGER NOT NULL REFERENCES ciclos_produccion(id),
    lote_id             INTEGER REFERENCES lotes(id),
    bitacora_id         INTEGER REFERENCES bitacora_diaria(id),
    origen              TEXT    NOT NULL CHECK (origen IN ('AUTOMATICA','UNIDAD_TECNICA')),
    tipo                TEXT    NOT NULL CHECK (tipo IN ('MORTALIDAD_ALERTA','MORTALIDAD_ANORMAL','SIGNOS_CLINICOS',
                                                         'PMF_MALO','INOCUIDAD','OTRA')),
    fecha_deteccion     TEXT    NOT NULL,
    fecha_reporte       TEXT    NOT NULL,
    mismo_dia           INTEGER NOT NULL CHECK (mismo_dia IN (0,1)),
    causa_demora        TEXT,
    descripcion         TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    mortalidad_acum     TEXT,
    reportado_por       TEXT    NOT NULL,
    decision            TEXT,
    medida              TEXT    CHECK (medida IS NULL OR medida IN ('CONSULTA_VETERINARIA','AISLAMIENTO',
                                                                    'REFUERZO_DESINFECCION','SIN_MEDIDA','OTRA')),
    atendida_por        TEXT,
    atendida_en         TEXT,
    CHECK (mismo_dia = 1 OR (causa_demora IS NOT NULL AND length(trim(causa_demora)) > 0))
);

-- ---------------------------------------------------------------------------
-- 5. BAJAS POR MORTALIDAD (FIN-TRA-04, CI-FIN-05) Y DISPOSICIÓN (FIN-TRA-11)
-- ---------------------------------------------------------------------------
CREATE TABLE bajas_mortalidad (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id              INTEGER NOT NULL REFERENCES ciclos_produccion(id),
    bitacora_id           INTEGER NOT NULL UNIQUE REFERENCES bitacora_diaria(id),
    fecha                 TEXT    NOT NULL,
    aves                  INTEGER NOT NULL CHECK (aves > 0),
    muertas_acumuladas    INTEGER NOT NULL CHECK (muertas_acumuladas >= aves),
    pct_acumulado         TEXT    NOT NULL,
    clasificacion         TEXT    NOT NULL CHECK (clasificacion IN ('NORMAL','ANORMAL')),
    costo_acumulado_cent  INTEGER NOT NULL CHECK (costo_acumulado_cent >= 0),
    costo_por_ave         TEXT    NOT NULL,
    monto_cent            INTEGER NOT NULL CHECK (monto_cent >= 0),
    informe_veterinario   TEXT,
    estado                TEXT    NOT NULL CHECK (estado IN ('PENDIENTE_AUTORIZACION','AUTORIZADA','CONTABILIZADA')),
    autorizado_por        TEXT,
    autorizado_en         TEXT,
    asiento_id            INTEGER REFERENCES asientos(id),
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (clasificacion = 'NORMAL' OR (informe_veterinario IS NOT NULL AND length(trim(informe_veterinario)) > 0)),
    CHECK (estado <> 'CONTABILIZADA' OR asiento_id IS NOT NULL)
);

CREATE TABLE disposiciones_sanitarias (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id          INTEGER NOT NULL REFERENCES lotes(id),
    fecha            TEXT    NOT NULL,
    aves             INTEGER NOT NULL CHECK (aves > 0),
    metodo           TEXT    NOT NULL CHECK (metodo IN ('ENTIERRO_CAL','INCINERACION','COMPOSTAJE','OTRO')),
    detalle_metodo   TEXT,
    responsable      TEXT    NOT NULL CHECK (length(trim(responsable)) > 0),
    supervisado_por  TEXT,
    supervisado_en   TEXT,
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    CHECK (metodo <> 'OTRO' OR (detalle_metodo IS NOT NULL AND length(trim(detalle_metodo)) > 0))
);

-- ---------------------------------------------------------------------------
-- 6. MUESTREO SEMANAL DE PESO (POL-03 Art. 2 v; POA A7)
-- ---------------------------------------------------------------------------
CREATE TABLE muestreos_peso (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id          INTEGER NOT NULL REFERENCES lotes(id),
    fecha            TEXT    NOT NULL,
    dia_ciclo        INTEGER NOT NULL CHECK (dia_ciclo > 0),
    semana           INTEGER NOT NULL CHECK (semana > 0),
    aves_muestra     INTEGER NOT NULL CHECK (aves_muestra > 0),
    peso_total_mil   INTEGER NOT NULL CHECK (peso_total_mil > 0),
    peso_promedio    TEXT    NOT NULL,
    observacion      TEXT,
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    UNIQUE (lote_id, semana)
);

-- ---------------------------------------------------------------------------
-- 7. FAENADO POR LOTE (FIN-TRA-05; CI-FIN-12) CON PMF E ICA
-- ---------------------------------------------------------------------------
CREATE TABLE faenados (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id               INTEGER NOT NULL UNIQUE REFERENCES lotes(id),
    fecha                 TEXT    NOT NULL,
    dia_ciclo             INTEGER NOT NULL CHECK (dia_ciclo > 0),
    aves_vivas_lote       INTEGER NOT NULL CHECK (aves_vivas_lote >= 0),
    aves_faenadas         INTEGER NOT NULL CHECK (aves_faenadas > 0),
    faltante_aves         INTEGER NOT NULL CHECK (faltante_aves >= 0),
    explicacion_faltante  TEXT,
    peso_vivo_mil         INTEGER NOT NULL CHECK (peso_vivo_mil > 0),
    peso_canal_mil        INTEGER NOT NULL CHECK (peso_canal_mil > 0),
    merma_mil             INTEGER NOT NULL CHECK (merma_mil >= 0),
    carcasas_separadas    INTEGER NOT NULL DEFAULT 0 CHECK (carcasas_separadas >= 0),
    pmf                   TEXT    NOT NULL,
    semaforo_pmf          TEXT    NOT NULL CHECK (semaforo_pmf IN ('BUENO','NORMAL','MALO')),
    alimento_kg_mil       INTEGER NOT NULL CHECK (alimento_kg_mil >= 0),
    ica                   TEXT    NOT NULL,
    semaforo_ica          TEXT    NOT NULL CHECK (semaforo_ica IN ('BUENO','NORMAL','MALO')),
    checklist_json        TEXT    NOT NULL,
    cumple_ci_fin_12      INTEGER NOT NULL CHECK (cumple_ci_fin_12 IN (0,1)),
    observacion           TEXT,
    supervisor_eventual   TEXT    NOT NULL CHECK (length(trim(supervisor_eventual)) > 0),
    fuera_de_plazo        INTEGER NOT NULL DEFAULT 0 CHECK (fuera_de_plazo IN (0,1)),
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (peso_canal_mil <= peso_vivo_mil),
    CHECK (merma_mil = peso_vivo_mil - peso_canal_mil),
    CHECK (faltante_aves = aves_vivas_lote - aves_faenadas),
    CHECK (faltante_aves = 0 OR (explicacion_faltante IS NOT NULL AND length(trim(explicacion_faltante)) > 0)),
    CHECK (fuera_de_plazo = 0 OR (observacion IS NOT NULL AND length(trim(observacion)) > 0))
);

-- ---------------------------------------------------------------------------
-- 8. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_galpones_no_delete BEFORE DELETE ON galpones
BEGIN SELECT RAISE(ABORT, 'Los galpones no se borran: desactívelos.'); END;

CREATE TRIGGER trg_ciclos_no_delete BEFORE DELETE ON ciclos_produccion
BEGIN SELECT RAISE(ABORT, 'Los ciclos productivos no se borran.'); END;
CREATE TRIGGER trg_ciclos_fijos BEFORE UPDATE ON ciclos_produccion
WHEN OLD.aves_iniciales <> NEW.aves_iniciales OR OLD.fecha_inicio <> NEW.fecha_inicio
  OR OLD.costo_pollitos_cent <> NEW.costo_pollitos_cent OR OLD.asiento_id <> NEW.asiento_id
  OR OLD.estado = 'CONCLUIDO'
BEGIN SELECT RAISE(ABORT, 'Un ciclo no cambia sus aves, fecha ni costo de apertura; concluido no se modifica.'); END;

CREATE TRIGGER trg_lotes_no_delete BEFORE DELETE ON lotes
BEGIN SELECT RAISE(ABORT, 'Los lotes no se borran.'); END;
CREATE TRIGGER trg_lotes_fijos BEFORE UPDATE ON lotes
WHEN OLD.aves_iniciales <> NEW.aves_iniciales OR OLD.galpon_id <> NEW.galpon_id OR OLD.qr_token <> NEW.qr_token
  OR OLD.estado = 'FAENADO'
BEGIN SELECT RAISE(ABORT, 'Un lote no cambia sus aves, galpón ni código QR; faenado no se modifica.'); END;

CREATE TRIGGER trg_bitacora_no_update BEFORE UPDATE ON bitacora_diaria
BEGIN SELECT RAISE(ABORT, 'La bitácora diaria no se modifica: registre la corrección como novedad del día siguiente.'); END;
CREATE TRIGGER trg_bitacora_no_delete BEFORE DELETE ON bitacora_diaria
BEGIN SELECT RAISE(ABORT, 'La bitácora diaria no se borra.'); END;
CREATE TRIGGER trg_revisiones_no_update BEFORE UPDATE ON revisiones_bitacora
BEGIN SELECT RAISE(ABORT, 'Las revisiones semanales no se modifican.'); END;
CREATE TRIGGER trg_revisiones_no_delete BEFORE DELETE ON revisiones_bitacora
BEGIN SELECT RAISE(ABORT, 'Las revisiones semanales no se borran.'); END;

CREATE TRIGGER trg_alertas_san_no_delete BEFORE DELETE ON alertas_sanitarias
BEGIN SELECT RAISE(ABORT, 'Las alertas sanitarias no se borran.'); END;
CREATE TRIGGER trg_alertas_san_fija BEFORE UPDATE ON alertas_sanitarias
WHEN OLD.descripcion <> NEW.descripcion OR OLD.fecha_deteccion <> NEW.fecha_deteccion
  OR OLD.fecha_reporte <> NEW.fecha_reporte OR OLD.atendida_por IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'Una alerta sanitaria solo registra la decisión de la Gerencia, una sola vez.'); END;

CREATE TRIGGER trg_bajas_mort_no_delete BEFORE DELETE ON bajas_mortalidad
BEGIN SELECT RAISE(ABORT, 'Las bajas por mortalidad no se borran: reverse su asiento.'); END;
CREATE TRIGGER trg_bajas_mort_fija BEFORE UPDATE ON bajas_mortalidad
WHEN OLD.clasificacion <> NEW.clasificacion OR OLD.aves <> NEW.aves OR OLD.monto_cent <> NEW.monto_cent
  OR OLD.pct_acumulado <> NEW.pct_acumulado OR OLD.estado = 'CONTABILIZADA'
BEGIN SELECT RAISE(ABORT, 'Una baja no cambia su clasificación ni su monto; contabilizada no se modifica.'); END;

CREATE TRIGGER trg_disposiciones_fija BEFORE UPDATE ON disposiciones_sanitarias
WHEN OLD.aves <> NEW.aves OR OLD.metodo <> NEW.metodo OR OLD.fecha <> NEW.fecha OR OLD.supervisado_por IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'Una disposición sanitaria solo admite el visto de la Gerencia, una sola vez.'); END;
CREATE TRIGGER trg_disposiciones_no_delete BEFORE DELETE ON disposiciones_sanitarias
BEGIN SELECT RAISE(ABORT, 'Las disposiciones sanitarias no se borran.'); END;

CREATE TRIGGER trg_muestreos_no_update BEFORE UPDATE ON muestreos_peso
BEGIN SELECT RAISE(ABORT, 'Los muestreos de peso no se modifican.'); END;
CREATE TRIGGER trg_muestreos_no_delete BEFORE DELETE ON muestreos_peso
BEGIN SELECT RAISE(ABORT, 'Los muestreos de peso no se borran.'); END;

CREATE TRIGGER trg_faenados_no_update BEFORE UPDATE ON faenados
BEGIN SELECT RAISE(ABORT, 'El registro de faenado no se modifica.'); END;
CREATE TRIGGER trg_faenados_no_delete BEFORE DELETE ON faenados
BEGIN SELECT RAISE(ABORT, 'El registro de faenado no se borra.'); END;
