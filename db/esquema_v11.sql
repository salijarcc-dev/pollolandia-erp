-- ============================================================================
-- ERP POLLOLANDIA - Migración 11 (Fase 11: RAU, cumplimiento legal y archivo)
-- Calendario fiscal (POL-07; PET-RAU §3.2): consulta de la RND, liquidación
-- individual por socio, Formulario 701, patentes y acta de compatibilidad;
-- certificado RAU; superficie declarada vs usada y control ante cada CAPEX;
-- programa de excedente y balance de masas del alimento; padrón de
-- compradores no socios; servicios a terceros (alerta roja); revisión
-- trimestral del CV (INF-CV-09); expediente EXP-RAU A-G; notificaciones y
-- plan de respuesta ante fiscalización; archivo contable del MAN-ARC-01
-- (tabla de retención, expedientes, préstamos y actas de transferencia y de
-- destrucción). Montos en centavos (*_cent); cantidades en milésimas (*_mil).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CATÁLOGOS (semillas)
-- ---------------------------------------------------------------------------
CREATE TABLE retencion_documental (
    codigo        TEXT PRIMARY KEY,
    documento     TEXT NOT NULL,
    procedimiento TEXT NOT NULL,
    clase         TEXT NOT NULL CHECK (clase IN ('PERMANENTE','T10','T5','VIDA_UTIL_10','DESECHO')),
    computo       TEXT NOT NULL CHECK (computo IN ('CIERRE','SUSTITUCION','VIGENCIA','BAJA')),
    criterio      TEXT NOT NULL,
    disposicion   TEXT NOT NULL,
    ciclo         TEXT NOT NULL CHECK (ciclo IN ('TES','EGR','TRA','ING','INF','TRV','EEFF'))
);

CREATE TABLE exp_rau_requisitos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    carpeta       TEXT NOT NULL CHECK (carpeta IN ('A','B','C','D','E','F','G')),
    clave         TEXT NOT NULL UNIQUE,
    documento     TEXT NOT NULL,
    responsable   TEXT NOT NULL,
    periodicidad  TEXT NOT NULL CHECK (periodicidad IN ('PERMANENTE','ANUAL'))
);

CREATE TABLE exp_rau_documentos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    requisito_id     INTEGER NOT NULL REFERENCES exp_rau_requisitos(id),
    gestion          INTEGER NOT NULL,
    fecha_documento  TEXT    NOT NULL,
    referencia       TEXT    NOT NULL CHECK (length(trim(referencia)) > 0),
    archivo_digital  TEXT    NOT NULL,
    ubicacion_fisica TEXT,
    origen           TEXT    NOT NULL DEFAULT 'MANUAL',
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);
CREATE INDEX idx_exp_rau_doc_req ON exp_rau_documentos(requisito_id, gestion);

-- ---------------------------------------------------------------------------
-- 2. INSCRIPCIÓN, SUPERFICIE Y CONTROL ANTE CAPEX
-- ---------------------------------------------------------------------------
CREATE TABLE certificados_rau (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    numero           TEXT    NOT NULL UNIQUE,
    fecha_emision    TEXT    NOT NULL,
    vigente_hasta    TEXT,
    ultimo_pago_ref  TEXT,
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);

CREATE TABLE superficies_declaradas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha            TEXT    NOT NULL,
    hectareas_mil    INTEGER NOT NULL CHECK (hectareas_mil > 0),
    zona             TEXT    NOT NULL,
    constancia_ref   TEXT    NOT NULL CHECK (length(trim(constancia_ref)) > 0),
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);

CREATE TABLE superficies_usadas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    componente       TEXT    NOT NULL CHECK (length(trim(componente)) > 0),
    hectareas_mil    INTEGER NOT NULL CHECK (hectareas_mil > 0),
    desde            TEXT    NOT NULL,
    hasta            TEXT,
    documento_ref    TEXT,
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    CHECK (hasta IS NULL OR hasta >= desde)
);

CREATE TABLE controles_superficie (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha                 TEXT    NOT NULL,
    capex_linea_id        INTEGER REFERENCES capex_lineas(id),
    descripcion           TEXT    NOT NULL,
    hectareas_adicionales_mil INTEGER NOT NULL CHECK (hectareas_adicionales_mil >= 0),
    usadas_mil            INTEGER NOT NULL,
    declaradas_mil        INTEGER NOT NULL,
    semaforo              TEXT    NOT NULL CHECK (semaforo IN ('VERDE','AMARILLO','ROJO')),
    accion                TEXT    NOT NULL,
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 3. CUOTA, LIQUIDACIÓN POR SOCIO, FORMULARIO 701, PATENTES Y CUMPLIMIENTOS
-- ---------------------------------------------------------------------------
CREATE TABLE liquidaciones_rau (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    gestion          INTEGER NOT NULL UNIQUE REFERENCES ejercicios(ejercicio),
    fecha            TEXT    NOT NULL,
    hectareas_mil    INTEGER NOT NULL CHECK (hectareas_mil > 0),
    cuota_ha         TEXT    NOT NULL,
    total_cent       INTEGER NOT NULL CHECK (total_cent > 0),
    socios           INTEGER NOT NULL CHECK (socios > 0),
    advertencias     TEXT    NOT NULL,
    asiento_id       INTEGER REFERENCES asientos(id),
    comunicada_en    TEXT,
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);

CREATE TABLE liquidaciones_rau_socios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    liquidacion_id   INTEGER NOT NULL REFERENCES liquidaciones_rau(id),
    socio_id         INTEGER NOT NULL REFERENCES socios(id),
    monto_cent       INTEGER NOT NULL CHECK (monto_cent >= 0),
    UNIQUE (liquidacion_id, socio_id)
);

CREATE TABLE formularios_701 (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    gestion               INTEGER NOT NULL UNIQUE REFERENCES ejercicios(ejercicio),
    fecha_presentacion    TEXT    NOT NULL,
    vencimiento           TEXT    NOT NULL,
    limite_anticipacion   TEXT    NOT NULL,
    dias_habiles_antes    INTEGER NOT NULL,
    semaforo              TEXT    NOT NULL CHECK (semaforo IN ('VERDE','AMARILLO','ROJO')),
    hectareas_mil         INTEGER NOT NULL,
    cuota_ha              TEXT    NOT NULL,
    monto_cent            INTEGER NOT NULL CHECK (monto_cent > 0),
    numero_orden          TEXT    NOT NULL CHECK (length(trim(numero_orden)) > 0),
    comprobante_ref       TEXT    NOT NULL CHECK (length(trim(comprobante_ref)) > 0),
    movimiento_id         INTEGER REFERENCES movimientos_bancarios(id),
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL
);

CREATE TABLE patentes_municipales (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    gestion          INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    municipio        TEXT    NOT NULL,
    fecha_pago       TEXT    NOT NULL,
    limite           TEXT    NOT NULL,
    dentro_plazo     INTEGER NOT NULL CHECK (dentro_plazo IN (0,1)),
    monto_cent       INTEGER NOT NULL CHECK (monto_cent > 0),
    comprobante_ref  TEXT    NOT NULL CHECK (length(trim(comprobante_ref)) > 0),
    movimiento_id    INTEGER REFERENCES movimientos_bancarios(id),
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    UNIQUE (gestion, municipio)
);

CREATE TABLE cumplimientos_fiscales (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    gestion          INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    evento           TEXT    NOT NULL CHECK (evento IN ('CONSULTA_RND','ACTA_COMPATIBILIDAD')),
    fecha            TEXT    NOT NULL,
    referencia       TEXT    NOT NULL CHECK (length(trim(referencia)) > 0),
    valor            TEXT,
    detalle          TEXT,
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    UNIQUE (gestion, evento)
);

-- ---------------------------------------------------------------------------
-- 4. ALIMENTO: PROGRAMA DE EXCEDENTE, BALANCE DE MASAS Y COMPRADORES
-- ---------------------------------------------------------------------------
CREATE TABLE programas_excedente (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio        INTEGER NOT NULL UNIQUE REFERENCES ejercicios(ejercicio),
    tope_qq_mil      INTEGER NOT NULL CHECK (tope_qq_mil >= 0),
    canal_socios     INTEGER NOT NULL CHECK (canal_socios IN (0,1)),
    canal_no_socios  INTEGER NOT NULL CHECK (canal_no_socios IN (0,1)),
    acta_ca_ref      TEXT    NOT NULL CHECK (length(trim(acta_ca_ref)) > 0),
    fecha            TEXT    NOT NULL,
    aprobado_por     TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);

CREATE TABLE balances_masas (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio              INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    trimestre              INTEGER NOT NULL CHECK (trimestre BETWEEN 1 AND 4),
    desde                  TEXT    NOT NULL,
    hasta                  TEXT    NOT NULL,
    producido_mil          INTEGER NOT NULL CHECK (producido_mil >= 0),
    consumo_mil            INTEGER NOT NULL CHECK (consumo_mil >= 0),
    merma_mil              INTEGER NOT NULL CHECK (merma_mil >= 0),
    excedente_mil          INTEGER NOT NULL,
    consumo_proyectado_mil INTEGER NOT NULL CHECK (consumo_proyectado_mil >= 0),
    merma_pct              TEXT,
    merma_semaforo         TEXT    CHECK (merma_semaforo IS NULL OR merma_semaforo IN ('VERDE','AMARILLO','ROJO')),
    autorizado_mil         INTEGER CHECK (autorizado_mil IS NULL OR autorizado_mil >= 0),
    preparado_por          TEXT    NOT NULL,
    firmado_por            TEXT,
    firmado_en             TEXT,
    registrado_en          TEXT    NOT NULL,
    UNIQUE (ejercicio, trimestre)
);

CREATE TABLE compradores_autorizados (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id       INTEGER NOT NULL REFERENCES clientes(id),
    ejercicio        INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    comunidad        TEXT    NOT NULL CHECK (length(trim(comunidad)) > 0),
    actividad        TEXT    NOT NULL CHECK (length(trim(actividad)) > 0),
    fecha            TEXT    NOT NULL,
    autorizado_por   TEXT    NOT NULL,
    estado           TEXT    NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','REVOCADA')),
    motivo_revocacion TEXT,
    registrado_en    TEXT    NOT NULL
);
CREATE UNIQUE INDEX ux_comprador_vigente ON compradores_autorizados(cliente_id, ejercicio) WHERE estado = 'VIGENTE';

-- ---------------------------------------------------------------------------
-- 5. SERVICIOS A TERCEROS, REVISIONES DEL CV Y FISCALIZACIÓN
-- ---------------------------------------------------------------------------
CREATE TABLE servicios_terceros (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha              TEXT    NOT NULL,
    tipo               TEXT    NOT NULL CHECK (tipo IN ('MAQUILA','FAENADO_ENCARGO','ALQUILER_PLANTA','ALQUILER_SILOS','OTRO')),
    tercero            TEXT    NOT NULL CHECK (length(trim(tercero)) > 0),
    descripcion        TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    detectado_por      TEXT    NOT NULL,
    limite_gg          TEXT    NOT NULL,
    limite_ca          TEXT    NOT NULL,
    informado_gg_en    TEXT,
    resuelto_ca_en     TEXT,
    acta_ca_ref        TEXT,
    medida             TEXT,
    estado             TEXT    NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','INFORMADO','CERRADO')),
    registrado_en      TEXT    NOT NULL
);

CREATE TABLE revisiones_rau (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo                TEXT    NOT NULL CHECK (tipo IN ('TRIMESTRAL','ANUAL')),
    anio                INTEGER NOT NULL,
    trimestre           INTEGER NOT NULL CHECK (trimestre BETWEEN 0 AND 4),
    fecha               TEXT    NOT NULL,
    verificaciones_json TEXT    NOT NULL,
    semaforo            TEXT    NOT NULL CHECK (semaforo IN ('VERDE','AMARILLO','ROJO')),
    observaciones       TEXT,
    elaborado_por       TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    UNIQUE (tipo, anio, trimestre)
);

CREATE TABLE notificaciones_fiscales (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_recepcion    TEXT    NOT NULL,
    entidad            TEXT    NOT NULL CHECK (entidad IN ('GERENCIA_DISTRITAL','GRACO','OTRA')),
    tipo               TEXT    NOT NULL CHECK (tipo IN ('ORDEN_VERIFICACION','REQUERIMIENTO','VISTA_CARGO',
                                                        'RESOLUCION_DETERMINATIVA','INSPECCION','EXCLUSION','OTRA')),
    numero             TEXT    NOT NULL CHECK (length(trim(numero)) > 0),
    funcionario        TEXT    NOT NULL,
    gestiones          TEXT    NOT NULL,
    plazo_dias         INTEGER NOT NULL CHECK (plazo_dias >= 0),
    vencimiento        TEXT    NOT NULL,
    escenario          TEXT    CHECK (escenario IS NULL OR escenario IN ('A','B','C','D','E')),
    recibido_por       TEXT    NOT NULL,
    estado             TEXT    NOT NULL DEFAULT 'ABIERTA' CHECK (estado IN ('ABIERTA','RESPONDIDA','CERRADA')),
    lecciones          TEXT,
    cerrado_por        TEXT,
    cerrado_en         TEXT,
    registrado_en      TEXT    NOT NULL
);

CREATE TABLE acciones_fiscalizacion (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    notificacion_id    INTEGER NOT NULL REFERENCES notificaciones_fiscales(id),
    paso               TEXT    NOT NULL CHECK (paso IN ('INFORME_24H','ANALISIS','PRORROGA','RESPUESTA','DESCARGOS',
                                                        'RECURSO','CIERRE')),
    fecha              TEXT    NOT NULL,
    detalle            TEXT    NOT NULL CHECK (length(trim(detalle)) > 0),
    documento_ref      TEXT,
    nuevo_vencimiento  TEXT,
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL
);

CREATE TABLE revisiones_buzon (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha            TEXT    NOT NULL,
    novedades        INTEGER NOT NULL CHECK (novedades IN (0,1)),
    detalle          TEXT,
    revisado_por     TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 6. ARCHIVO CONTABLE (MAN-ARC-01)
-- ---------------------------------------------------------------------------
CREATE TABLE expedientes_archivo (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo             TEXT    NOT NULL UNIQUE,
    ciclo              TEXT    NOT NULL CHECK (ciclo IN ('TES','EGR','TRA','ING','INF','TRV','EEFF')),
    gestion            INTEGER NOT NULL,
    correlativo        TEXT    NOT NULL,
    tipo               TEXT    NOT NULL,
    titulo             TEXT    NOT NULL CHECK (length(trim(titulo)) > 0),
    procedimiento      TEXT    NOT NULL,
    codigo_documental  TEXT    REFERENCES retencion_documental(codigo),
    clase              TEXT    NOT NULL CHECK (clase IN ('PERMANENTE','T10','T5','VIDA_UTIL_10','DESECHO')),
    computo            TEXT    NOT NULL CHECK (computo IN ('CIERRE','SUSTITUCION','VIGENCIA','BAJA')),
    fecha_base         TEXT,
    vencimiento        TEXT,
    ruta               TEXT    NOT NULL,
    caja               TEXT,
    fecha_inicio       TEXT,
    fecha_fin          TEXT,
    folios             INTEGER NOT NULL DEFAULT 0 CHECK (folios >= 0),
    estado             TEXT    NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','CERRADO','TRANSFERIDO','DESTRUIDO')),
    bloqueo_legal      TEXT,
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    cerrado_por        TEXT,
    cerrado_en         TEXT,
    UNIQUE (ciclo, gestion, correlativo)
);

CREATE TABLE documentos_archivo (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id      INTEGER NOT NULL REFERENCES expedientes_archivo(id),
    codigo_formato     TEXT    NOT NULL,
    fecha              TEXT    NOT NULL,
    correlativo        INTEGER NOT NULL CHECK (correlativo > 0),
    version            TEXT    NOT NULL,
    estado_documento   TEXT    NOT NULL CHECK (estado_documento IN ('BOR','REV','APR','OBS')),
    descripcion        TEXT    NOT NULL,
    folios             INTEGER NOT NULL CHECK (folios > 0),
    folio_desde        INTEGER NOT NULL,
    folio_hasta        INTEGER NOT NULL,
    tomo               INTEGER NOT NULL CHECK (tomo > 0),
    nombre_digital     TEXT    NOT NULL,
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    CHECK (folio_hasta >= folio_desde)
);

CREATE TABLE prestamos_archivo (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id        INTEGER NOT NULL REFERENCES expedientes_archivo(id),
    solicitante          TEXT    NOT NULL CHECK (length(trim(solicitante)) > 0),
    cargo                TEXT    NOT NULL,
    motivo               TEXT    NOT NULL CHECK (length(trim(motivo)) > 0),
    original             INTEGER NOT NULL CHECK (original IN (0,1)),
    fecha_salida         TEXT    NOT NULL,
    devolucion_prevista  TEXT    NOT NULL,
    autorizado_por       TEXT,
    fecha_devolucion     TEXT,
    recibido_por         TEXT,
    estado               TEXT    NOT NULL DEFAULT 'PRESTADO' CHECK (estado IN ('PRESTADO','DEVUELTO')),
    registrado_por       TEXT    NOT NULL,
    registrado_en        TEXT    NOT NULL,
    CHECK (devolucion_prevista >= fecha_salida),
    CHECK (original = 0 OR autorizado_por IS NOT NULL)
);
CREATE UNIQUE INDEX ux_prestamo_abierto ON prestamos_archivo(expediente_id) WHERE estado = 'PRESTADO';

CREATE TABLE actas_archivo (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo             TEXT    NOT NULL CHECK (tipo IN ('TRANSFERENCIA','DESTRUCCION','DESTRUCCION_SIMPLIFICADA')),
    numero           INTEGER NOT NULL,
    anio             INTEGER NOT NULL,
    fecha            TEXT    NOT NULL,
    metodo           TEXT    NOT NULL,
    observacion      TEXT,
    elaborado_por    TEXT    NOT NULL,
    autorizado_por   TEXT,
    autorizado_en    TEXT,
    visado_por       TEXT,
    visado_en        TEXT,
    ejecutado_en     TEXT,
    estado           TEXT    NOT NULL DEFAULT 'PREPARADA' CHECK (estado IN ('PREPARADA','AUTORIZADA','VISADA','EJECUTADA')),
    registrado_en    TEXT    NOT NULL,
    UNIQUE (tipo, anio, numero)
);

CREATE TABLE acta_archivo_detalle (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    acta_id          INTEGER NOT NULL REFERENCES actas_archivo(id),
    expediente_id    INTEGER NOT NULL REFERENCES expedientes_archivo(id),
    UNIQUE (acta_id, expediente_id)
);

-- ---------------------------------------------------------------------------
-- 7. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_retencion_no_delete BEFORE DELETE ON retencion_documental
BEGIN SELECT RAISE(ABORT, 'La tabla de retención no se borra.'); END;
CREATE TRIGGER trg_exp_req_no_delete BEFORE DELETE ON exp_rau_requisitos
BEGIN SELECT RAISE(ABORT, 'El índice maestro del expediente no se borra.'); END;
CREATE TRIGGER trg_exp_doc_no_update BEFORE UPDATE ON exp_rau_documentos
BEGIN SELECT RAISE(ABORT, 'El documento indexado no se modifica: registre uno nuevo.'); END;
CREATE TRIGGER trg_exp_doc_no_delete BEFORE DELETE ON exp_rau_documentos
BEGIN SELECT RAISE(ABORT, 'El documento indexado no se borra.'); END;
CREATE TRIGGER trg_certificados_no_delete BEFORE DELETE ON certificados_rau
BEGIN SELECT RAISE(ABORT, 'El certificado RAU no se borra.'); END;
CREATE TRIGGER trg_sup_decl_no_update BEFORE UPDATE ON superficies_declaradas
BEGIN SELECT RAISE(ABORT, 'La superficie declarada no se modifica: registre la actualización ante el SIN.'); END;
CREATE TRIGGER trg_sup_decl_no_delete BEFORE DELETE ON superficies_declaradas
BEGIN SELECT RAISE(ABORT, 'La superficie declarada no se borra.'); END;
CREATE TRIGGER trg_sup_usada_no_delete BEFORE DELETE ON superficies_usadas
BEGIN SELECT RAISE(ABORT, 'La superficie usada no se borra: registre su fecha de término.'); END;
CREATE TRIGGER trg_sup_usada_solo_cierre BEFORE UPDATE ON superficies_usadas
WHEN OLD.hasta IS NOT NULL OR OLD.hectareas_mil <> NEW.hectareas_mil OR OLD.desde <> NEW.desde
  OR OLD.componente <> NEW.componente
BEGIN SELECT RAISE(ABORT, 'La superficie usada solo admite registrar su fecha de término.'); END;
CREATE TRIGGER trg_controles_sup_no_update BEFORE UPDATE ON controles_superficie
BEGIN SELECT RAISE(ABORT, 'El control de superficie no se modifica.'); END;
CREATE TRIGGER trg_controles_sup_no_delete BEFORE DELETE ON controles_superficie
BEGIN SELECT RAISE(ABORT, 'El control de superficie no se borra.'); END;
CREATE TRIGGER trg_liq_rau_no_delete BEFORE DELETE ON liquidaciones_rau
BEGIN SELECT RAISE(ABORT, 'La liquidación RAU no se borra.'); END;
CREATE TRIGGER trg_liq_rau_cifras BEFORE UPDATE ON liquidaciones_rau
WHEN OLD.total_cent <> NEW.total_cent OR OLD.hectareas_mil <> NEW.hectareas_mil OR OLD.cuota_ha <> NEW.cuota_ha
  OR OLD.asiento_id IS NOT NULL AND IFNULL(NEW.asiento_id, 0) <> OLD.asiento_id
  OR OLD.comunicada_en IS NOT NULL AND IFNULL(NEW.comunicada_en, '') <> OLD.comunicada_en
BEGIN SELECT RAISE(ABORT, 'La liquidación RAU solo admite su devengo y su comunicación, una vez.'); END;
CREATE TRIGGER trg_liq_rau_socios_no_update BEFORE UPDATE ON liquidaciones_rau_socios
BEGIN SELECT RAISE(ABORT, 'La liquidación individual no se modifica.'); END;
CREATE TRIGGER trg_liq_rau_socios_no_delete BEFORE DELETE ON liquidaciones_rau_socios
BEGIN SELECT RAISE(ABORT, 'La liquidación individual no se borra.'); END;
CREATE TRIGGER trg_f701_no_update BEFORE UPDATE ON formularios_701
BEGIN SELECT RAISE(ABORT, 'El Formulario 701 registrado no se modifica.'); END;
CREATE TRIGGER trg_f701_no_delete BEFORE DELETE ON formularios_701
BEGIN SELECT RAISE(ABORT, 'El Formulario 701 registrado no se borra.'); END;
CREATE TRIGGER trg_patentes_no_update BEFORE UPDATE ON patentes_municipales
BEGIN SELECT RAISE(ABORT, 'El pago de patentes no se modifica.'); END;
CREATE TRIGGER trg_patentes_no_delete BEFORE DELETE ON patentes_municipales
BEGIN SELECT RAISE(ABORT, 'El pago de patentes no se borra.'); END;
CREATE TRIGGER trg_cumplimientos_no_update BEFORE UPDATE ON cumplimientos_fiscales
BEGIN SELECT RAISE(ABORT, 'El cumplimiento fiscal registrado no se modifica.'); END;
CREATE TRIGGER trg_cumplimientos_no_delete BEFORE DELETE ON cumplimientos_fiscales
BEGIN SELECT RAISE(ABORT, 'El cumplimiento fiscal registrado no se borra.'); END;
CREATE TRIGGER trg_programas_no_update BEFORE UPDATE ON programas_excedente
BEGIN SELECT RAISE(ABORT, 'El programa de excedente aprobado no se modifica.'); END;
CREATE TRIGGER trg_programas_no_delete BEFORE DELETE ON programas_excedente
BEGIN SELECT RAISE(ABORT, 'El programa de excedente no se borra.'); END;
CREATE TRIGGER trg_balances_no_delete BEFORE DELETE ON balances_masas
BEGIN SELECT RAISE(ABORT, 'El balance de masas no se borra.'); END;
CREATE TRIGGER trg_balances_firmado BEFORE UPDATE ON balances_masas
WHEN OLD.firmado_por IS NOT NULL OR OLD.producido_mil <> NEW.producido_mil OR OLD.consumo_mil <> NEW.consumo_mil
  OR OLD.merma_mil <> NEW.merma_mil OR OLD.excedente_mil <> NEW.excedente_mil
BEGIN SELECT RAISE(ABORT, 'El balance de masas solo admite la firma de la Gerencia General, una vez.'); END;
CREATE TRIGGER trg_compradores_no_delete BEFORE DELETE ON compradores_autorizados
BEGIN SELECT RAISE(ABORT, 'La autorización del comprador no se borra: se revoca.'); END;
CREATE TRIGGER trg_compradores_solo_revocar BEFORE UPDATE ON compradores_autorizados
WHEN OLD.estado <> 'VIGENTE' OR OLD.cliente_id <> NEW.cliente_id OR OLD.ejercicio <> NEW.ejercicio
BEGIN SELECT RAISE(ABORT, 'La autorización del comprador solo admite su revocación.'); END;
CREATE TRIGGER trg_servicios_no_delete BEFORE DELETE ON servicios_terceros
BEGIN SELECT RAISE(ABORT, 'El registro de servicio a terceros no se borra.'); END;
CREATE TRIGGER trg_servicios_fijo BEFORE UPDATE ON servicios_terceros
WHEN OLD.estado = 'CERRADO' OR OLD.fecha <> NEW.fecha OR OLD.tipo <> NEW.tipo OR OLD.descripcion <> NEW.descripcion
BEGIN SELECT RAISE(ABORT, 'El servicio a terceros cerrado no se modifica.'); END;
CREATE TRIGGER trg_revisiones_rau_no_update BEFORE UPDATE ON revisiones_rau
BEGIN SELECT RAISE(ABORT, 'El INF-CV-09 no se modifica.'); END;
CREATE TRIGGER trg_revisiones_rau_no_delete BEFORE DELETE ON revisiones_rau
BEGIN SELECT RAISE(ABORT, 'El INF-CV-09 no se borra.'); END;
CREATE TRIGGER trg_notificaciones_no_delete BEFORE DELETE ON notificaciones_fiscales
BEGIN SELECT RAISE(ABORT, 'La notificación fiscal no se borra.'); END;
CREATE TRIGGER trg_notificaciones_fijo BEFORE UPDATE ON notificaciones_fiscales
WHEN OLD.estado = 'CERRADA' OR OLD.fecha_recepcion <> NEW.fecha_recepcion OR OLD.numero <> NEW.numero
BEGIN SELECT RAISE(ABORT, 'La notificación cerrada no se modifica.'); END;
CREATE TRIGGER trg_acciones_no_update BEFORE UPDATE ON acciones_fiscalizacion
BEGIN SELECT RAISE(ABORT, 'La acción del plan de respuesta no se modifica.'); END;
CREATE TRIGGER trg_acciones_no_delete BEFORE DELETE ON acciones_fiscalizacion
BEGIN SELECT RAISE(ABORT, 'La acción del plan de respuesta no se borra.'); END;
CREATE TRIGGER trg_buzon_no_update BEFORE UPDATE ON revisiones_buzon
BEGIN SELECT RAISE(ABORT, 'La revisión del buzón no se modifica.'); END;
CREATE TRIGGER trg_buzon_no_delete BEFORE DELETE ON revisiones_buzon
BEGIN SELECT RAISE(ABORT, 'La revisión del buzón no se borra.'); END;
CREATE TRIGGER trg_expedientes_no_delete BEFORE DELETE ON expedientes_archivo
BEGIN SELECT RAISE(ABORT, 'El expediente no se borra: se destruye con acta.'); END;
CREATE TRIGGER trg_expedientes_destruido BEFORE UPDATE ON expedientes_archivo
WHEN OLD.estado IN ('TRANSFERIDO','DESTRUIDO') OR OLD.codigo <> NEW.codigo OR OLD.clase <> NEW.clase
BEGIN SELECT RAISE(ABORT, 'El expediente transferido o destruido no se modifica.'); END;
CREATE TRIGGER trg_documentos_archivo_no_update BEFORE UPDATE ON documentos_archivo
BEGIN SELECT RAISE(ABORT, 'El documento archivado no se modifica.'); END;
CREATE TRIGGER trg_documentos_archivo_no_delete BEFORE DELETE ON documentos_archivo
BEGIN SELECT RAISE(ABORT, 'El documento archivado no se borra.'); END;
CREATE TRIGGER trg_prestamos_no_delete BEFORE DELETE ON prestamos_archivo
BEGIN SELECT RAISE(ABORT, 'El registro del libro de préstamo no se borra.'); END;
CREATE TRIGGER trg_prestamos_solo_devolucion BEFORE UPDATE ON prestamos_archivo
WHEN OLD.estado = 'DEVUELTO' OR OLD.expediente_id <> NEW.expediente_id OR OLD.fecha_salida <> NEW.fecha_salida
BEGIN SELECT RAISE(ABORT, 'El préstamo solo admite su devolución, una vez.'); END;
CREATE TRIGGER trg_actas_archivo_no_delete BEFORE DELETE ON actas_archivo
BEGIN SELECT RAISE(ABORT, 'El acta de archivo no se borra.'); END;
CREATE TRIGGER trg_actas_archivo_ejecutada BEFORE UPDATE ON actas_archivo
WHEN OLD.estado = 'EJECUTADA'
BEGIN SELECT RAISE(ABORT, 'El acta ejecutada no se modifica.'); END;
CREATE TRIGGER trg_acta_detalle_no_update BEFORE UPDATE ON acta_archivo_detalle
BEGIN SELECT RAISE(ABORT, 'El detalle del acta no se modifica.'); END;
CREATE TRIGGER trg_acta_detalle_no_delete BEFORE DELETE ON acta_archivo_detalle
BEGIN SELECT RAISE(ABORT, 'El detalle del acta no se borra.'); END;
