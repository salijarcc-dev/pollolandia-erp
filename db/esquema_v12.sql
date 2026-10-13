-- ===========================================================================
-- ERP Pollolandia — Esquema v12 (Fase 12): Presupuesto, Consejo de Vigilancia
-- y Dashboard de indicadores.
--
-- Fuentes: Manual del Sistema Presupuestario (Presupuesto Maestro 2026-2030,
-- calendario §2.2, módulo E y anexos FIN-TES-09 a FIN-TES-13), POA 2026-27,
-- CI-FIN-09 (autoridad de las modificaciones), CI-FIN-18 (verificación previa
-- de indicadores: INF-CV-07 antes del FIN-INF-05), MOF (funciones del CV:
-- arqueos, inventario sorpresivo, verificación de activos, INF-CV-01 a 09).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. PRESUPUESTO MAESTRO 2026-2030 (módulos A a E del Manual Presupuestario)
-- ---------------------------------------------------------------------------
CREATE TABLE presupuesto_maestro (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio      INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    modulo         TEXT    NOT NULL CHECK (modulo IN ('VENTAS','PRODUCCION','MP','MP_ALIMENTO','MOD','MOD_ALIMENTO',
                                                      'CIF','CIF_ALIMENTO','COSTO','GASTOS','CAPEX','FLUJO',
                                                      'RESULTADOS','BALANCE','D6','INDICADORES')),
    orden          INTEGER NOT NULL,
    clave          TEXT    NOT NULL CHECK (length(trim(clave)) > 0),
    concepto       TEXT    NOT NULL CHECK (length(trim(concepto)) > 0),
    unidad         TEXT,
    cantidad_mil   INTEGER,
    valor          TEXT,
    monto_cent     INTEGER NOT NULL DEFAULT 0,
    es_total       INTEGER NOT NULL DEFAULT 0 CHECK (es_total IN (0,1)),
    fuente         TEXT    NOT NULL,
    cargado_por    TEXT    NOT NULL,
    cargado_en     TEXT    NOT NULL,
    UNIQUE (ejercicio, modulo, clave)
);
CREATE INDEX idx_presupuesto_maestro ON presupuesto_maestro(ejercicio, modulo, orden);

-- Presupuesto por ciclo (FIN-TES-10): la meta de cada partida en cada ciclo.
CREATE TABLE presupuesto_lineas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio      INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    ciclo          INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    partida        TEXT    NOT NULL CHECK (partida IN ('ING_CARNE','ING_ALIMENTO','ING_GALLINAZA','MP_POLLITOS',
                                                       'MP_ALIMENTO','MP_OTROS','MOD_FIJA','MOD_EVENTUAL','CIF',
                                                       'ALI_MP','ALI_MOD','ALI_CIF','GASTOS_ADM','GASTOS_VENTA',
                                                       'CAPEX')),
    concepto       TEXT    NOT NULL,
    unidad         TEXT,
    cantidad_mil   INTEGER CHECK (cantidad_mil IS NULL OR cantidad_mil > 0),
    precio         TEXT,
    monto_cent     INTEGER NOT NULL CHECK (monto_cent >= 0),
    fuente         TEXT    NOT NULL,
    UNIQUE (ejercicio, ciclo, partida),
    CHECK ((cantidad_mil IS NULL) = (precio IS NULL))
);

-- ---------------------------------------------------------------------------
-- 2. CALENDARIO PRESUPUESTARIO (Manual §2.2: abril a julio)
-- ---------------------------------------------------------------------------
CREATE TABLE hitos_presupuesto (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio      INTEGER NOT NULL,
    actividad      INTEGER NOT NULL CHECK (actividad BETWEEN 1 AND 7),
    fecha          TEXT    NOT NULL,
    limite         TEXT    NOT NULL,
    en_plazo       INTEGER NOT NULL CHECK (en_plazo IN (0,1)),
    documento_ref  TEXT    NOT NULL CHECK (length(trim(documento_ref)) > 0),
    detalle_json   TEXT,
    registrado_por TEXT    NOT NULL,
    rol            TEXT    NOT NULL,
    registrado_en  TEXT    NOT NULL,
    UNIQUE (ejercicio, actividad)
);

-- ---------------------------------------------------------------------------
-- 3. INFORME DE EJECUCIÓN Y VARIACIONES (FIN-TES-12)
-- ---------------------------------------------------------------------------
CREATE TABLE informes_ejecucion (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio       INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    tipo            TEXT    NOT NULL CHECK (tipo IN ('CICLO','SEMESTRE','ANUAL')),
    numero          INTEGER NOT NULL CHECK (numero >= 0),
    desde           TEXT    NOT NULL,
    hasta           TEXT    NOT NULL,
    fecha           TEXT    NOT NULL,
    limite          TEXT    NOT NULL,
    fuera_de_plazo  INTEGER NOT NULL CHECK (fuera_de_plazo IN (0,1)),
    contenido_json  TEXT    NOT NULL,
    verdes          INTEGER NOT NULL CHECK (verdes >= 0),
    amarillos       INTEGER NOT NULL CHECK (amarillos >= 0),
    rojos           INTEGER NOT NULL CHECK (rojos >= 0),
    elaborado_por   TEXT    NOT NULL,
    presentado_por  TEXT,
    presentado_en   TEXT,
    registrado_en   TEXT    NOT NULL,
    UNIQUE (ejercicio, tipo, numero),
    CHECK (hasta >= desde)
);

CREATE TABLE acciones_variacion (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    informe_id        INTEGER NOT NULL REFERENCES informes_ejecucion(id),
    partida           TEXT    NOT NULL,
    semaforo          TEXT    NOT NULL CHECK (semaforo IN ('AMARILLO','ROJO')),
    causa             TEXT    NOT NULL CHECK (length(trim(causa)) > 0),
    responsable       TEXT,
    accion            TEXT,
    fecha_compromiso  TEXT,
    UNIQUE (informe_id, partida),
    -- Manual E.1: todo rojo exige causa, responsable y acción; el amarillo, explicación escrita.
    CHECK (semaforo = 'AMARILLO' OR (responsable IS NOT NULL AND length(trim(responsable)) > 0
                                     AND accion IS NOT NULL AND length(trim(accion)) > 0))
);

-- ---------------------------------------------------------------------------
-- 4. SOLICITUD DE MODIFICACIÓN PRESUPUESTARIA (FIN-TES-11; autoridad CI-FIN-09)
-- ---------------------------------------------------------------------------
CREATE TABLE modificaciones_presupuesto (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio              INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    ciclo                  INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    partida                TEXT    NOT NULL,
    concepto               TEXT    NOT NULL,
    monto_aprobado_cent    INTEGER NOT NULL CHECK (monto_aprobado_cent >= 0),
    monto_solicitado_cent  INTEGER NOT NULL CHECK (monto_solicitado_cent >= 0),
    variacion_pct          TEXT,
    motivo                 TEXT    NOT NULL CHECK (length(trim(motivo)) > 0),
    informe_id             INTEGER REFERENCES informes_ejecucion(id),
    fuente_recursos        TEXT    NOT NULL CHECK (length(trim(fuente_recursos)) > 0),
    efecto_flujo           TEXT    NOT NULL CHECK (length(trim(efecto_flujo)) > 0),
    autoridad              TEXT    NOT NULL CHECK (autoridad IN ('GG','CA','AG')),
    fundamentos            TEXT    NOT NULL,
    estado                 TEXT    NOT NULL DEFAULT 'SOLICITADA' CHECK (estado IN ('SOLICITADA','APROBADA','RECHAZADA')),
    fecha                  TEXT    NOT NULL,
    solicitado_por         TEXT    NOT NULL,
    verificado_por         TEXT,
    resuelto_por           TEXT,
    resuelto_en            TEXT,
    acta_ref               TEXT,
    observacion            TEXT,
    registrado_en          TEXT    NOT NULL,
    CHECK (monto_solicitado_cent <> monto_aprobado_cent)
);

-- ---------------------------------------------------------------------------
-- 5. POA: ACTIVIDADES POR TRIMESTRE Y AVANCE FÍSICO
-- ---------------------------------------------------------------------------
CREATE TABLE poa_actividades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio      INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    codigo         TEXT    NOT NULL CHECK (length(trim(codigo)) > 0),
    area           TEXT    NOT NULL CHECK (area IN ('PRODUCCION','FAENADO','COMERCIAL','ADMINISTRATIVA','GOBERNANZA')),
    descripcion    TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    indicador      TEXT    NOT NULL,
    responsable    TEXT    NOT NULL,
    trimestres     TEXT    NOT NULL CHECK (length(trim(trimestres)) > 0),
    fuente         TEXT    NOT NULL,
    registrado_por TEXT    NOT NULL,
    registrado_en  TEXT    NOT NULL,
    UNIQUE (ejercicio, codigo)
);

CREATE TABLE poa_avances (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    actividad_id   INTEGER NOT NULL REFERENCES poa_actividades(id),
    trimestre      INTEGER NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    fecha          TEXT    NOT NULL,
    estado         TEXT    NOT NULL CHECK (estado IN ('CUMPLIDA','PARCIAL','NO_CUMPLIDA')),
    en_plazo       INTEGER NOT NULL CHECK (en_plazo IN (0,1)),
    evidencia      TEXT,
    observacion    TEXT,
    registrado_por TEXT    NOT NULL,
    registrado_en  TEXT    NOT NULL,
    UNIQUE (actividad_id, trimestre)
);

-- ---------------------------------------------------------------------------
-- 6. CONSEJO DE VIGILANCIA: INFORMES, INVENTARIO SORPRESIVO Y DESEMBOLSOS
-- ---------------------------------------------------------------------------
CREATE TABLE informes_cv (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT    NOT NULL CHECK (codigo IN ('INF-CV-02','INF-CV-05','INF-CV-06','INF-CV-07','INF-CV-08')),
    ejercicio       INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    periodo         TEXT    NOT NULL,
    periodo_id      INTEGER REFERENCES periodos(id),
    fecha           TEXT    NOT NULL,
    datos_json      TEXT    NOT NULL,
    dictamen        TEXT    NOT NULL CHECK (dictamen IN ('CONFORME','CON_OBSERVACIONES')),
    observaciones   TEXT,
    reportado_ag_en TEXT,
    elaborado_por   TEXT    NOT NULL,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    registrado_en   TEXT    NOT NULL,
    UNIQUE (codigo, periodo),
    CHECK (codigo <> 'INF-CV-07' OR periodo_id IS NOT NULL),
    CHECK (dictamen = 'CONFORME' OR (observaciones IS NOT NULL AND length(trim(observaciones)) > 0))
);

-- FIN-INF-05: solo con el INF-CV-07 del mismo mes (CI-FIN-18, Pasos 5 y 6).
CREATE TABLE informes_gerenciales (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo_id                  INTEGER NOT NULL UNIQUE REFERENCES periodos(id),
    inf_cv_07_id                INTEGER NOT NULL REFERENCES informes_cv(id),
    fecha                       TEXT    NOT NULL,
    indicadores_json            TEXT    NOT NULL,
    observaciones_incorporadas  TEXT,
    decisiones_json             TEXT    NOT NULL,
    emitido_por                 TEXT    NOT NULL,
    registrado_en               TEXT    NOT NULL
);

CREATE TABLE inventarios_sorpresivos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio       INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    fecha           TEXT    NOT NULL,
    items           INTEGER NOT NULL CHECK (items > 0),
    con_diferencia  INTEGER NOT NULL CHECK (con_diferencia >= 0),
    diferencia_cent INTEGER NOT NULL,
    observacion     TEXT,
    reportar_ag     INTEGER NOT NULL CHECK (reportar_ag IN (0,1)),
    verificado_por  TEXT    NOT NULL,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    registrado_en   TEXT    NOT NULL
);

CREATE TABLE inventario_sorpresivo_detalle (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    inventario_id   INTEGER NOT NULL REFERENCES inventarios_sorpresivos(id),
    insumo_id       INTEGER NOT NULL REFERENCES insumos(id),
    contable_mil    INTEGER NOT NULL,
    fisico_mil      INTEGER NOT NULL CHECK (fisico_mil >= 0),
    diferencia_mil  INTEGER NOT NULL,
    cpp             TEXT    NOT NULL,
    diferencia_cent INTEGER NOT NULL,
    UNIQUE (inventario_id, insumo_id)
);

-- El miembro del CV que cofirmó el desembolso no puede verificarlo (MOF §3.2).
CREATE TABLE verificaciones_desembolso (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    autorizacion_id  INTEGER NOT NULL UNIQUE REFERENCES autorizaciones(id),
    fecha            TEXT    NOT NULL,
    conforme         INTEGER NOT NULL CHECK (conforme IN (0,1)),
    observacion      TEXT,
    usuario_id       INTEGER NOT NULL REFERENCES usuarios(id),
    verificado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    CHECK (conforme = 1 OR (observacion IS NOT NULL AND length(trim(observacion)) > 0))
);

-- ---------------------------------------------------------------------------
-- 7. INMUTABILIDAD Y CONTROLES
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_p12_maestro_no_update BEFORE UPDATE ON presupuesto_maestro
BEGIN SELECT RAISE(ABORT, 'El Presupuesto Maestro aprobado no se modifica: tramite una modificación (FIN-TES-11).'); END;
CREATE TRIGGER trg_p12_maestro_no_delete BEFORE DELETE ON presupuesto_maestro
BEGIN SELECT RAISE(ABORT, 'El Presupuesto Maestro no se borra.'); END;
CREATE TRIGGER trg_p12_lineas_no_update BEFORE UPDATE ON presupuesto_lineas
BEGIN SELECT RAISE(ABORT, 'El presupuesto por ciclo no se modifica: tramite una modificación (FIN-TES-11).'); END;
CREATE TRIGGER trg_p12_lineas_no_delete BEFORE DELETE ON presupuesto_lineas
BEGIN SELECT RAISE(ABORT, 'El presupuesto por ciclo no se borra.'); END;
CREATE TRIGGER trg_p12_hitos_no_update BEFORE UPDATE ON hitos_presupuesto
BEGIN SELECT RAISE(ABORT, 'El hito del calendario presupuestario no se modifica.'); END;
CREATE TRIGGER trg_p12_hitos_no_delete BEFORE DELETE ON hitos_presupuesto
BEGIN SELECT RAISE(ABORT, 'El hito del calendario presupuestario no se borra.'); END;
CREATE TRIGGER trg_p12_informes_ej_solo_presentacion BEFORE UPDATE ON informes_ejecucion
WHEN OLD.presentado_en IS NOT NULL OR OLD.contenido_json <> NEW.contenido_json OR OLD.fecha <> NEW.fecha
  OR OLD.rojos <> NEW.rojos OR OLD.amarillos <> NEW.amarillos
BEGIN SELECT RAISE(ABORT, 'El FIN-TES-12 solo admite registrar su presentación, una vez.'); END;
CREATE TRIGGER trg_p12_informes_ej_no_delete BEFORE DELETE ON informes_ejecucion
BEGIN SELECT RAISE(ABORT, 'El FIN-TES-12 no se borra.'); END;
CREATE TRIGGER trg_p12_acciones_no_update BEFORE UPDATE ON acciones_variacion
BEGIN SELECT RAISE(ABORT, 'La acción correctiva registrada no se modifica.'); END;
CREATE TRIGGER trg_p12_acciones_no_delete BEFORE DELETE ON acciones_variacion
BEGIN SELECT RAISE(ABORT, 'La acción correctiva registrada no se borra.'); END;
CREATE TRIGGER trg_p12_modif_solo_resolucion BEFORE UPDATE ON modificaciones_presupuesto
WHEN OLD.estado <> 'SOLICITADA' OR OLD.monto_solicitado_cent <> NEW.monto_solicitado_cent
  OR OLD.monto_aprobado_cent <> NEW.monto_aprobado_cent OR OLD.autoridad <> NEW.autoridad
BEGIN SELECT RAISE(ABORT, 'La modificación presupuestaria solo admite su resolución, una vez.'); END;
CREATE TRIGGER trg_p12_modif_no_delete BEFORE DELETE ON modificaciones_presupuesto
BEGIN SELECT RAISE(ABORT, 'La solicitud de modificación presupuestaria no se borra.'); END;
CREATE TRIGGER trg_p12_poa_act_no_update BEFORE UPDATE ON poa_actividades
BEGIN SELECT RAISE(ABORT, 'La actividad del POA aprobada no se modifica.'); END;
CREATE TRIGGER trg_p12_poa_act_no_delete BEFORE DELETE ON poa_actividades
BEGIN SELECT RAISE(ABORT, 'La actividad del POA no se borra.'); END;
CREATE TRIGGER trg_p12_poa_av_no_update BEFORE UPDATE ON poa_avances
BEGIN SELECT RAISE(ABORT, 'El avance del POA registrado no se modifica.'); END;
CREATE TRIGGER trg_p12_poa_av_no_delete BEFORE DELETE ON poa_avances
BEGIN SELECT RAISE(ABORT, 'El avance del POA no se borra.'); END;
CREATE TRIGGER trg_p12_inf_cv_solo_reporte BEFORE UPDATE ON informes_cv
WHEN OLD.reportado_ag_en IS NOT NULL OR OLD.datos_json <> NEW.datos_json OR OLD.dictamen <> NEW.dictamen
  OR IFNULL(OLD.observaciones, '') <> IFNULL(NEW.observaciones, '')
BEGIN SELECT RAISE(ABORT, 'El informe del Consejo de Vigilancia solo admite registrar su reporte a la Asamblea.'); END;
CREATE TRIGGER trg_p12_inf_cv_no_delete BEFORE DELETE ON informes_cv
BEGIN SELECT RAISE(ABORT, 'El informe del Consejo de Vigilancia no se borra.'); END;
CREATE TRIGGER trg_p12_fin_inf_05_requiere_cv07 BEFORE INSERT ON informes_gerenciales
WHEN NOT EXISTS (SELECT 1 FROM informes_cv c WHERE c.id = NEW.inf_cv_07_id AND c.codigo = 'INF-CV-07'
                   AND c.periodo_id = NEW.periodo_id)
BEGIN SELECT RAISE(ABORT, 'CI-FIN-18: el FIN-INF-05 exige el INF-CV-07 del mismo mes.'); END;
CREATE TRIGGER trg_p12_fin_inf_05_no_update BEFORE UPDATE ON informes_gerenciales
BEGIN SELECT RAISE(ABORT, 'El Informe Gerencial de Indicadores emitido no se modifica.'); END;
CREATE TRIGGER trg_p12_fin_inf_05_no_delete BEFORE DELETE ON informes_gerenciales
BEGIN SELECT RAISE(ABORT, 'El Informe Gerencial de Indicadores no se borra.'); END;
CREATE TRIGGER trg_p12_inv_sorp_no_update BEFORE UPDATE ON inventarios_sorpresivos
BEGIN SELECT RAISE(ABORT, 'El inventario sorpresivo registrado no se modifica.'); END;
CREATE TRIGGER trg_p12_inv_sorp_no_delete BEFORE DELETE ON inventarios_sorpresivos
BEGIN SELECT RAISE(ABORT, 'El inventario sorpresivo no se borra.'); END;
CREATE TRIGGER trg_p12_inv_det_no_update BEFORE UPDATE ON inventario_sorpresivo_detalle
BEGIN SELECT RAISE(ABORT, 'El detalle del inventario sorpresivo no se modifica.'); END;
CREATE TRIGGER trg_p12_inv_det_no_delete BEFORE DELETE ON inventario_sorpresivo_detalle
BEGIN SELECT RAISE(ABORT, 'El detalle del inventario sorpresivo no se borra.'); END;
CREATE TRIGGER trg_p12_verif_des_no_update BEFORE UPDATE ON verificaciones_desembolso
BEGIN SELECT RAISE(ABORT, 'La verificación del desembolso no se modifica.'); END;
CREATE TRIGGER trg_p12_verif_des_no_delete BEFORE DELETE ON verificaciones_desembolso
BEGIN SELECT RAISE(ABORT, 'La verificación del desembolso no se borra.'); END;
-- Segregación: quien cofirmó como CV no verifica el mismo desembolso.
CREATE TRIGGER trg_p12_verif_des_segregacion BEFORE INSERT ON verificaciones_desembolso
WHEN EXISTS (SELECT 1 FROM autorizacion_firmas f WHERE f.autorizacion_id = NEW.autorizacion_id
               AND f.usuario_id = NEW.usuario_id)
BEGIN SELECT RAISE(ABORT, 'Segregación de funciones: quien firmó el desembolso no puede verificarlo.'); END;
