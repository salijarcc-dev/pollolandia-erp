-- ============================================================================
-- ERP POLLOLANDIA - Migración 4 (Fase 4: Compras e inventarios, Ciclo 2)
-- Proveedores y fichas de pequeños productores (PET-RAU §2.2), insumos y
-- kárdex perpetuo con Costo Promedio Ponderado (PC-07), punto de reorden y
-- alertas (CI-FIN-16), acta de stock del viernes (FIN-EGR-01), compras de
-- emergencia, partidas presupuestarias y reporte de disponibilidad
-- (FIN-EGR-02), órdenes de compra con cotizaciones (FIN-EGR-03), recepción
-- (FIN-EGR-04), cuentas por pagar 2.1.01.xx, programación de pagos
-- (FIN-EGR-05) y bajas de inventario con visto del CV (PC-07).
-- Cantidades en milésimas de la unidad (*_mil) y montos en centavos (*_cent).
-- Todo hecho económico se registra además como asiento (asiento_id).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. PROVEEDORES Y FICHAS DE PEQUEÑOS PRODUCTORES
-- ---------------------------------------------------------------------------
CREATE TABLE proveedores (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo                       TEXT    NOT NULL CHECK (tipo IN ('EMPRESA','PEQUENO_PRODUCTOR')),
    nombre                     TEXT    NOT NULL CHECK (length(trim(nombre)) > 0),
    nit                        TEXT,
    ci                         TEXT,
    comunidad                  TEXT,
    hectareas                  TEXT,
    producto                   TEXT,
    telefono                   TEXT,
    tipo_certificado           TEXT    CHECK (tipo_certificado IS NULL OR tipo_certificado IN ('RAU','NO_IMPONIBILIDAD')),
    nro_certificado            TEXT,
    certificado_vigente_hasta  TEXT,
    ultimo_pago_rau_ref        TEXT,
    estado                     TEXT    NOT NULL CHECK (estado IN ('HABILITADO','OBSERVADO','INACTIVO')),
    motivo_estado              TEXT,
    registrado_por             TEXT    NOT NULL,
    registrado_en              TEXT    NOT NULL,
    actualizado_en             TEXT,
    CHECK (tipo <> 'EMPRESA' OR length(trim(IFNULL(nit,''))) > 0)
);
CREATE UNIQUE INDEX ux_proveedor_nit ON proveedores(nit) WHERE nit IS NOT NULL;
CREATE UNIQUE INDEX ux_proveedor_ci ON proveedores(ci) WHERE ci IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. INSUMOS Y KÁRDEX PERPETUO (CPP)
-- ---------------------------------------------------------------------------
CREATE TABLE insumos (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo                   TEXT    NOT NULL UNIQUE CHECK (length(trim(codigo)) > 0),
    nombre                   TEXT    NOT NULL CHECK (length(trim(nombre)) > 0),
    cuenta_inventario        TEXT    NOT NULL REFERENCES cuentas(codigo) CHECK (cuenta_inventario LIKE '1.1.03.%'),
    unidad                   TEXT    NOT NULL CHECK (length(trim(unidad)) > 0),
    grupo_critico            TEXT    CHECK (grupo_critico IS NULL OR grupo_critico IN ('ALIMENTO_BB','VETERINARIO_LIMPIEZA')),
    es_grano                 INTEGER NOT NULL DEFAULT 0 CHECK (es_grano IN (0,1)),
    controla_vencimiento     INTEGER NOT NULL DEFAULT 0 CHECK (controla_vencimiento IN (0,1)),
    transferencia_inmediata  INTEGER NOT NULL DEFAULT 0 CHECK (transferencia_inmediata IN (0,1)),
    comprable                INTEGER NOT NULL DEFAULT 1 CHECK (comprable IN (0,1)),
    dias_reposicion          INTEGER CHECK (dias_reposicion IS NULL OR dias_reposicion > 0),
    activo                   INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
    registrado_por           TEXT    NOT NULL,
    registrado_en            TEXT    NOT NULL,
    actualizado_en           TEXT
);

CREATE TABLE kardex (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    insumo_id            INTEGER NOT NULL REFERENCES insumos(id),
    fecha                TEXT    NOT NULL,
    tipo                 TEXT    NOT NULL CHECK (tipo IN ('ENTRADA_COMPRA','SALIDA_CONSUMO','BAJA')),
    cantidad_mil         INTEGER NOT NULL CHECK (cantidad_mil > 0),
    valor_cent           INTEGER NOT NULL CHECK (valor_cent >= 0),
    costo_unitario       TEXT    NOT NULL,
    saldo_cantidad_mil   INTEGER NOT NULL CHECK (saldo_cantidad_mil >= 0),
    saldo_valor_cent     INTEGER NOT NULL CHECK (saldo_valor_cent >= 0),
    cpp                  TEXT    NOT NULL,
    documento_ref        TEXT,
    recepcion_id         INTEGER,
    baja_id              INTEGER,
    centro_costo         TEXT    REFERENCES cuentas(codigo),
    sufijo               TEXT,
    asiento_id           INTEGER NOT NULL REFERENCES asientos(id),
    registrado_por       TEXT    NOT NULL,
    registrado_en        TEXT    NOT NULL,
    CHECK (saldo_cantidad_mil > 0 OR saldo_valor_cent = 0)
);
CREATE INDEX idx_kardex_insumo ON kardex(insumo_id, id);
CREATE INDEX idx_kardex_fecha ON kardex(fecha, tipo);

-- ---------------------------------------------------------------------------
-- 3. PUNTO DE REORDEN, ACTA DE STOCK, ALERTAS Y COMPRAS DE EMERGENCIA
-- ---------------------------------------------------------------------------
CREATE TABLE puntos_reorden (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    insumo_id            INTEGER NOT NULL REFERENCES insumos(id),
    vigente_desde        TEXT    NOT NULL,
    ejercicio            INTEGER NOT NULL,
    ciclo                INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    fuente               TEXT    NOT NULL CHECK (fuente IN ('BITACORA','PROYECTADO')),
    base_desde           TEXT,
    base_hasta           TEXT,
    consumo_total_mil    INTEGER,
    consumo_diario       TEXT    NOT NULL,
    dias_reposicion      INTEGER NOT NULL CHECK (dias_reposicion > 0),
    dias_seguridad       INTEGER NOT NULL CHECK (dias_seguridad >= 0),
    stock_seguridad_mil  INTEGER NOT NULL CHECK (stock_seguridad_mil >= 0),
    punto_reorden_mil    INTEGER NOT NULL CHECK (punto_reorden_mil >= 0),
    calculado_por        TEXT    NOT NULL,
    calculado_en         TEXT    NOT NULL
);
CREATE INDEX idx_pr_insumo ON puntos_reorden(insumo_id, vigente_desde, id);

CREATE TABLE actas_stock (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha            TEXT    NOT NULL UNIQUE,
    ejercicio        INTEGER NOT NULL,
    ciclo            INTEGER NOT NULL,
    deficit          INTEGER NOT NULL CHECK (deficit IN (0,1)),
    elaborado_por    TEXT    NOT NULL,
    rol_elaborador   TEXT    NOT NULL,
    verificado_por   TEXT,
    rol_verificador  TEXT,
    verificado_en    TEXT,
    observaciones    TEXT,
    estado           TEXT    NOT NULL CHECK (estado IN ('REGISTRADA','VERIFICADA')),
    registrado_en    TEXT    NOT NULL
);

CREATE TABLE acta_stock_lineas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    acta_id               INTEGER NOT NULL REFERENCES actas_stock(id),
    insumo_id             INTEGER NOT NULL REFERENCES insumos(id),
    existencia_fisica_mil INTEGER NOT NULL CHECK (existencia_fisica_mil >= 0),
    saldo_contable_mil    INTEGER NOT NULL,
    diferencia_mil        INTEGER NOT NULL,
    punto_reorden_mil     INTEGER,
    bajo_pr               INTEGER NOT NULL CHECK (bajo_pr IN (0,1)),
    accion                TEXT    NOT NULL,
    UNIQUE (acta_id, insumo_id)
);

CREATE TABLE alertas_stock (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    insumo_id        INTEGER NOT NULL REFERENCES insumos(id),
    fecha            TEXT    NOT NULL,
    tipo             TEXT    NOT NULL CHECK (tipo IN ('BAJO_PUNTO_REORDEN','ROTACION_EXCEDIDA')),
    origen           TEXT    NOT NULL CHECK (origen IN ('MONITOREO_CONTINUO','ACTA_VIERNES','REVISION_ROTACION')),
    existencia_mil   INTEGER NOT NULL,
    referencia_mil   INTEGER,
    dias             INTEGER,
    acta_id          INTEGER REFERENCES actas_stock(id),
    detalle          TEXT    NOT NULL,
    estado           TEXT    NOT NULL CHECK (estado IN ('ABIERTA','EMERGENCIA_AUTORIZADA','ATENDIDA','CERRADA')),
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL,
    cerrada_en       TEXT,
    cierre_detalle   TEXT
);
CREATE UNIQUE INDEX ux_alerta_stock_abierta ON alertas_stock(insumo_id, tipo)
    WHERE estado IN ('ABIERTA','EMERGENCIA_AUTORIZADA');

CREATE TABLE compras_emergencia (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alerta_id           INTEGER NOT NULL UNIQUE REFERENCES alertas_stock(id),
    fecha               TEXT    NOT NULL,
    autorizado_por_id   INTEGER NOT NULL REFERENCES usuarios(id),
    rol_autorizador     TEXT    NOT NULL,
    constancia_ref      TEXT    NOT NULL CHECK (length(trim(constancia_ref)) > 0),
    justificacion       TEXT    NOT NULL CHECK (length(trim(justificacion)) > 0),
    estado              TEXT    NOT NULL CHECK (estado IN ('AUTORIZADA','UTILIZADA')),
    orden_compra_id     INTEGER,
    registrado_en       TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 4. PRESUPUESTO DE COMPRAS (FIN-EGR-02)
-- ---------------------------------------------------------------------------
CREATE TABLE partidas_presupuesto (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio          INTEGER NOT NULL,
    ciclo              INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    cuenta_inventario  TEXT    NOT NULL REFERENCES cuentas(codigo),
    descripcion        TEXT    NOT NULL,
    monto_cent         INTEGER NOT NULL CHECK (monto_cent >= 0),
    fuente             TEXT    NOT NULL,
    UNIQUE (ejercicio, ciclo, cuenta_inventario)
);

CREATE TABLE reportes_disponibilidad (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha           TEXT    NOT NULL,
    ejercicio       INTEGER NOT NULL,
    ciclo           INTEGER NOT NULL,
    habilita        INTEGER NOT NULL CHECK (habilita IN (0,1)),
    detalle_json    TEXT    NOT NULL,
    elaborado_por   TEXT    NOT NULL,
    registrado_en   TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 5. ÓRDENES DE COMPRA (FIN-EGR-03) Y COTIZACIONES
-- ---------------------------------------------------------------------------
CREATE TABLE ordenes_compra (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    numero                   INTEGER NOT NULL,
    ejercicio                INTEGER NOT NULL,
    ciclo                    INTEGER NOT NULL,
    fecha_programada         TEXT    NOT NULL,
    proveedor_id             INTEGER NOT NULL REFERENCES proveedores(id),
    cuenta_por_pagar         TEXT    NOT NULL REFERENCES cuentas(codigo) CHECK (cuenta_por_pagar LIKE '2.1.01.%'),
    categoria_pago           TEXT    NOT NULL,
    condicion_pago           TEXT    NOT NULL CHECK (condicion_pago IN ('CONTADO','CREDITO')),
    dias_credito             INTEGER NOT NULL DEFAULT 0 CHECK (dias_credito >= 0),
    destino_ciclo            TEXT    NOT NULL CHECK (destino_ciclo IN ('ACTUAL','NUEVO')),
    total_cent               INTEGER NOT NULL CHECK (total_cent > 0),
    es_emergencia            INTEGER NOT NULL DEFAULT 0 CHECK (es_emergencia IN (0,1)),
    emergencia_id            INTEGER REFERENCES compras_emergencia(id),
    justificacion_seleccion  TEXT,
    reporte_disponibilidad_id INTEGER REFERENCES reportes_disponibilidad(id),
    compromiso_id            INTEGER REFERENCES compromisos(id),
    autorizacion_id          INTEGER REFERENCES autorizaciones(id),
    estado                   TEXT    NOT NULL CHECK (estado IN
                             ('PREPARADA','EMITIDA','RECIBIDA_PARCIAL','RECIBIDA','CERRADA','ANULADA')),
    preparado_por            TEXT    NOT NULL,
    preparado_en             TEXT    NOT NULL,
    fecha_emision            TEXT,
    emitida_por              TEXT,
    emitida_puntual          INTEGER CHECK (emitida_puntual IS NULL OR emitida_puntual IN (0,1)),
    motivo_cierre            TEXT,
    actualizado_en           TEXT,
    UNIQUE (ejercicio, numero),
    CHECK (es_emergencia = 0 OR emergencia_id IS NOT NULL)
);
CREATE INDEX idx_oc_estado ON ordenes_compra(estado, fecha_programada);

CREATE TABLE oc_lineas (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    orden_id                INTEGER NOT NULL REFERENCES ordenes_compra(id),
    insumo_id               INTEGER NOT NULL REFERENCES insumos(id),
    cantidad_mil            INTEGER NOT NULL CHECK (cantidad_mil > 0),
    precio_unitario_cent    INTEGER NOT NULL CHECK (precio_unitario_cent > 0),
    total_cent              INTEGER NOT NULL CHECK (total_cent > 0),
    cantidad_recibida_mil   INTEGER NOT NULL DEFAULT 0 CHECK (cantidad_recibida_mil >= 0),
    CHECK (cantidad_recibida_mil <= cantidad_mil)
);

CREATE TABLE oc_cotizaciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    orden_id        INTEGER NOT NULL REFERENCES ordenes_compra(id),
    proveedor_id    INTEGER NOT NULL REFERENCES proveedores(id),
    monto_cent      INTEGER NOT NULL CHECK (monto_cent > 0),
    documento_ref   TEXT    NOT NULL CHECK (length(trim(documento_ref)) > 0),
    UNIQUE (orden_id, proveedor_id)
);

-- ---------------------------------------------------------------------------
-- 6. RECEPCIÓN (FIN-EGR-04) Y CUENTAS POR PAGAR (2.1.01.xx)
-- ---------------------------------------------------------------------------
CREATE TABLE recepciones (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    orden_id             INTEGER NOT NULL REFERENCES ordenes_compra(id),
    fecha                TEXT    NOT NULL,
    documento_proveedor  TEXT    NOT NULL CHECK (length(trim(documento_proveedor)) > 0),
    recibido_por         TEXT    NOT NULL,
    rol_receptor         TEXT    NOT NULL,
    observaciones        TEXT,
    valor_aceptado_cent  INTEGER NOT NULL CHECK (valor_aceptado_cent >= 0),
    estado               TEXT    NOT NULL CHECK (estado IN ('RECIBIDA','CONTABILIZADA','RECHAZADA')),
    registrado_por       TEXT,
    asiento_id           INTEGER REFERENCES asientos(id),
    registrado_en        TEXT    NOT NULL,
    contabilizado_en     TEXT
);

CREATE TABLE recepcion_lineas (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    recepcion_id           INTEGER NOT NULL REFERENCES recepciones(id),
    oc_linea_id            INTEGER NOT NULL REFERENCES oc_lineas(id),
    cantidad_pendiente_mil INTEGER NOT NULL,
    cantidad_recibida_mil  INTEGER NOT NULL CHECK (cantidad_recibida_mil >= 0),
    cantidad_aceptada_mil  INTEGER NOT NULL CHECK (cantidad_aceptada_mil >= 0),
    calidad_conforme       INTEGER NOT NULL CHECK (calidad_conforme IN (0,1)),
    fecha_vencimiento      TEXT,
    observacion            TEXT,
    valor_cent             INTEGER NOT NULL CHECK (valor_cent >= 0),
    CHECK (cantidad_aceptada_mil <= cantidad_recibida_mil),
    CHECK (cantidad_recibida_mil <= cantidad_pendiente_mil)
);

CREATE TABLE cuentas_por_pagar (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_id     INTEGER NOT NULL REFERENCES proveedores(id),
    orden_id         INTEGER NOT NULL REFERENCES ordenes_compra(id),
    recepcion_id     INTEGER NOT NULL UNIQUE REFERENCES recepciones(id),
    cuenta_pasivo    TEXT    NOT NULL REFERENCES cuentas(codigo) CHECK (cuenta_pasivo LIKE '2.1.01.%'),
    categoria_pago   TEXT    NOT NULL,
    fecha            TEXT    NOT NULL,
    vencimiento      TEXT    NOT NULL,
    monto_cent       INTEGER NOT NULL CHECK (monto_cent > 0),
    documento_ref    TEXT    NOT NULL,
    estado           TEXT    NOT NULL CHECK (estado IN ('PENDIENTE','PROGRAMADA','PAGADA')),
    movimiento_bancario_id INTEGER REFERENCES movimientos_bancarios(id),
    pagado_en        TEXT,
    registrado_en    TEXT    NOT NULL
);
CREATE INDEX idx_cxp_estado ON cuentas_por_pagar(estado, vencimiento);

-- ---------------------------------------------------------------------------
-- 7. PROGRAMACIÓN DE PAGOS (FIN-EGR-05)
-- ---------------------------------------------------------------------------
CREATE TABLE programaciones_pago (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha             TEXT    NOT NULL,
    saldo_inicial_cent INTEGER NOT NULL,
    sme_cent          INTEGER NOT NULL,
    total_pagable_cent INTEGER NOT NULL,
    estado            TEXT    NOT NULL CHECK (estado IN ('PREPARADA','AUTORIZADA','SUSTITUIDA')),
    elaborado_por     TEXT    NOT NULL,
    elaborado_en      TEXT    NOT NULL,
    autorizado_por    TEXT,
    autorizado_en     TEXT
);

CREATE TABLE programacion_lineas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    programacion_id       INTEGER NOT NULL REFERENCES programaciones_pago(id),
    cuenta_por_pagar_id   INTEGER NOT NULL REFERENCES cuentas_por_pagar(id),
    nivel                 INTEGER NOT NULL,
    categoria             TEXT    NOT NULL,
    monto_cent            INTEGER NOT NULL CHECK (monto_cent > 0),
    vencimiento           TEXT    NOT NULL,
    requiere_mancomunada  INTEGER NOT NULL CHECK (requiere_mancomunada IN (0,1)),
    pagable               INTEGER NOT NULL CHECK (pagable IN (0,1)),
    motivo                TEXT,
    UNIQUE (programacion_id, cuenta_por_pagar_id)
);

-- ---------------------------------------------------------------------------
-- 8. BAJAS DE INVENTARIO (PC-07)
-- ---------------------------------------------------------------------------
CREATE TABLE bajas_inventario (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    insumo_id            INTEGER NOT NULL REFERENCES insumos(id),
    fecha                TEXT    NOT NULL,
    cantidad_mil         INTEGER NOT NULL CHECK (cantidad_mil > 0),
    causa                TEXT    NOT NULL CHECK (causa IN ('DETERIORO','VENCIMIENTO','PERDIDA')),
    descripcion          TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    informe_tecnico_ref  TEXT    NOT NULL CHECK (length(trim(informe_tecnico_ref)) > 0),
    valor_estimado_cent  INTEGER NOT NULL CHECK (valor_estimado_cent >= 0),
    requiere_visto_cv    INTEGER NOT NULL CHECK (requiere_visto_cv IN (0,1)),
    estado               TEXT    NOT NULL CHECK (estado IN ('PROPUESTA','AUTORIZADA','CONTABILIZADA','RECHAZADA')),
    propuesto_por        TEXT    NOT NULL,
    propuesto_en         TEXT    NOT NULL,
    autorizado_por       TEXT,
    autorizado_en        TEXT,
    acta_ref             TEXT,
    visto_cv_por         TEXT,
    visto_cv_en          TEXT,
    observacion          TEXT,
    valor_cent           INTEGER,
    asiento_id           INTEGER REFERENCES asientos(id),
    kardex_id            INTEGER REFERENCES kardex(id),
    contabilizado_en     TEXT
);

-- ---------------------------------------------------------------------------
-- 9. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_kardex_no_update BEFORE UPDATE ON kardex
BEGIN SELECT RAISE(ABORT, 'Un movimiento de kárdex no se modifica: registre el movimiento inverso.'); END;
CREATE TRIGGER trg_kardex_no_delete BEFORE DELETE ON kardex
BEGIN SELECT RAISE(ABORT, 'Los movimientos de kárdex no se borran.'); END;

CREATE TRIGGER trg_pr_no_update BEFORE UPDATE ON puntos_reorden
BEGIN SELECT RAISE(ABORT, 'Un punto de reorden calculado no se modifica: recalcúlelo.'); END;
CREATE TRIGGER trg_pr_no_delete BEFORE DELETE ON puntos_reorden
BEGIN SELECT RAISE(ABORT, 'El historial de puntos de reorden no se borra.'); END;

CREATE TRIGGER trg_acta_verificada BEFORE UPDATE ON actas_stock
WHEN OLD.estado = 'VERIFICADA'
BEGIN SELECT RAISE(ABORT, 'Un acta de stock verificada no se modifica.'); END;
CREATE TRIGGER trg_acta_no_delete BEFORE DELETE ON actas_stock
BEGIN SELECT RAISE(ABORT, 'Las actas de stock no se borran.'); END;
CREATE TRIGGER trg_acta_lineas_no_update BEFORE UPDATE ON acta_stock_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del acta de stock no se modifican.'); END;
CREATE TRIGGER trg_acta_lineas_no_delete BEFORE DELETE ON acta_stock_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del acta de stock no se borran.'); END;

CREATE TRIGGER trg_alertas_stock_no_delete BEFORE DELETE ON alertas_stock
BEGIN SELECT RAISE(ABORT, 'Las alertas de stock no se borran.'); END;
CREATE TRIGGER trg_emergencias_no_delete BEFORE DELETE ON compras_emergencia
BEGIN SELECT RAISE(ABORT, 'Las compras de emergencia no se borran.'); END;

CREATE TRIGGER trg_reportes_disp_no_update BEFORE UPDATE ON reportes_disponibilidad
BEGIN SELECT RAISE(ABORT, 'Un reporte de disponibilidad no se modifica.'); END;
CREATE TRIGGER trg_reportes_disp_no_delete BEFORE DELETE ON reportes_disponibilidad
BEGIN SELECT RAISE(ABORT, 'Los reportes de disponibilidad no se borran.'); END;

CREATE TRIGGER trg_oc_no_delete BEFORE DELETE ON ordenes_compra
BEGIN SELECT RAISE(ABORT, 'Las órdenes de compra no se borran: anúlelas.'); END;
CREATE TRIGGER trg_oc_datos_fijos BEFORE UPDATE ON ordenes_compra
WHEN OLD.total_cent <> NEW.total_cent OR OLD.proveedor_id <> NEW.proveedor_id
  OR OLD.fecha_programada <> NEW.fecha_programada OR OLD.numero <> NEW.numero
  OR OLD.estado IN ('CERRADA','ANULADA','RECIBIDA')
BEGIN SELECT RAISE(ABORT, 'Una orden de compra no altera su proveedor, monto ni fecha; cerrada, recibida o anulada no se modifica.'); END;
CREATE TRIGGER trg_oc_lineas_no_delete BEFORE DELETE ON oc_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas de una orden de compra no se borran.'); END;
CREATE TRIGGER trg_oc_lineas_fijas BEFORE UPDATE ON oc_lineas
WHEN OLD.cantidad_mil <> NEW.cantidad_mil OR OLD.precio_unitario_cent <> NEW.precio_unitario_cent
  OR OLD.total_cent <> NEW.total_cent OR OLD.insumo_id <> NEW.insumo_id
  OR NEW.cantidad_recibida_mil < OLD.cantidad_recibida_mil
BEGIN SELECT RAISE(ABORT, 'Una línea de orden de compra solo acumula la cantidad recibida.'); END;
CREATE TRIGGER trg_cotizaciones_no_update BEFORE UPDATE ON oc_cotizaciones
BEGIN SELECT RAISE(ABORT, 'Las cotizaciones no se modifican.'); END;
CREATE TRIGGER trg_cotizaciones_no_delete BEFORE DELETE ON oc_cotizaciones
BEGIN SELECT RAISE(ABORT, 'Las cotizaciones no se borran.'); END;

CREATE TRIGGER trg_recepciones_no_delete BEFORE DELETE ON recepciones
BEGIN SELECT RAISE(ABORT, 'Las actas de recepción no se borran.'); END;
CREATE TRIGGER trg_recepcion_contabilizada BEFORE UPDATE ON recepciones
WHEN OLD.estado IN ('CONTABILIZADA','RECHAZADA')
BEGIN SELECT RAISE(ABORT, 'Un acta de recepción contabilizada o rechazada no se modifica.'); END;
CREATE TRIGGER trg_recepcion_lineas_no_update BEFORE UPDATE ON recepcion_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del acta de recepción no se modifican.'); END;
CREATE TRIGGER trg_recepcion_lineas_no_delete BEFORE DELETE ON recepcion_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del acta de recepción no se borran.'); END;

CREATE TRIGGER trg_cxp_no_delete BEFORE DELETE ON cuentas_por_pagar
BEGIN SELECT RAISE(ABORT, 'Las cuentas por pagar no se borran.'); END;
CREATE TRIGGER trg_cxp_datos_fijos BEFORE UPDATE ON cuentas_por_pagar
WHEN OLD.monto_cent <> NEW.monto_cent OR OLD.cuenta_pasivo <> NEW.cuenta_pasivo OR OLD.estado = 'PAGADA'
BEGIN SELECT RAISE(ABORT, 'Una cuenta por pagar no altera su monto ni su cuenta; pagada no se modifica.'); END;

CREATE TRIGGER trg_programacion_autorizada BEFORE UPDATE ON programaciones_pago
WHEN OLD.estado = 'SUSTITUIDA' OR OLD.total_pagable_cent <> NEW.total_pagable_cent
BEGIN SELECT RAISE(ABORT, 'Una programación sustituida no se modifica ni altera su total.'); END;
CREATE TRIGGER trg_programacion_no_delete BEFORE DELETE ON programaciones_pago
BEGIN SELECT RAISE(ABORT, 'Las programaciones de pago no se borran.'); END;
CREATE TRIGGER trg_programacion_lineas_no_update BEFORE UPDATE ON programacion_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas de la programación de pagos no se modifican.'); END;
CREATE TRIGGER trg_programacion_lineas_no_delete BEFORE DELETE ON programacion_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas de la programación de pagos no se borran.'); END;

CREATE TRIGGER trg_bajas_no_delete BEFORE DELETE ON bajas_inventario
BEGIN SELECT RAISE(ABORT, 'Las bajas de inventario no se borran.'); END;
CREATE TRIGGER trg_bajas_contabilizada BEFORE UPDATE ON bajas_inventario
WHEN OLD.estado IN ('CONTABILIZADA','RECHAZADA') OR OLD.cantidad_mil <> NEW.cantidad_mil
BEGIN SELECT RAISE(ABORT, 'Una baja contabilizada o rechazada no se modifica, ni se altera su cantidad.'); END;

CREATE TRIGGER trg_partidas_presupuesto_no_delete BEFORE DELETE ON partidas_presupuesto
BEGIN SELECT RAISE(ABORT, 'Las partidas presupuestarias no se borran (modificación presupuestaria, Fase 12).'); END;
