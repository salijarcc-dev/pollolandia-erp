-- ============================================================================
-- ERP POLLOLANDIA - Migración 9 (Fase 9: Ventas, HORECA y cobranza, Ciclo 4)
-- Fichas de clientes (HORECA, friales, mercado popular, agricultores);
-- tarifario con aprobación conjunta GG + CA (CI-FIN-06); pre-venta del lote:
-- pedidos (FIN-ING-01), despachos y verificación de cobertura (CI-FIN-13);
-- resoluciones de crédito (FIN-ING-02); notas de entrega HORECA y contado
-- (FIN-ING-03/06) con comprobante (FIN-ING-04) y exhibición del Certificado
-- RAU; cuentas por cobrar y cobros (FIN-ING-05, CI-FIN-15); suspensión de la
-- línea (CIR-ING-01); devoluciones por inocuidad (PC-08); parte diario de
-- recaudación (FIN-ING-07); análisis de margen por canal (FIN-ING-08) y
-- provisión de incobrables (POL-05 Art. 3). Montos en centavos (*_cent);
-- cantidades en milésimas (*_mil).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CLIENTES (ficha) Y SUSPENSIÓN DE LA LÍNEA DE CRÉDITO (CIR-ING-01)
-- ---------------------------------------------------------------------------
CREATE TABLE clientes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo             TEXT    NOT NULL UNIQUE,
    tipo               TEXT    NOT NULL CHECK (tipo IN ('HORECA','FRIAL','MERCADO_POPULAR','AGRICULTOR','OTRO')),
    nombre             TEXT    NOT NULL CHECK (length(trim(nombre)) > 0),
    documento          TEXT,
    contacto           TEXT,
    telefono           TEXT,
    direccion          TEXT,
    es_socio           INTEGER NOT NULL DEFAULT 0 CHECK (es_socio IN (0,1)),
    fecha_alta         TEXT    NOT NULL,
    estado_credito     TEXT    NOT NULL DEFAULT 'HABILITADO' CHECK (estado_credito IN ('HABILITADO','SUSPENDIDO')),
    restablecido_en    TEXT,
    observacion        TEXT,
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL
);
CREATE UNIQUE INDEX ux_clientes_documento ON clientes(documento) WHERE documento IS NOT NULL;

CREATE TABLE suspensiones_credito (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    numero             INTEGER NOT NULL,
    anio               INTEGER NOT NULL,
    cliente_id         INTEGER NOT NULL REFERENCES clientes(id),
    fecha              TEXT    NOT NULL,
    detalle_json       TEXT    NOT NULL,
    firmada_por        TEXT,
    firmada_en         TEXT,
    restablecida_por   TEXT,
    restablecida_en    TEXT,
    motivo_restablecimiento TEXT,
    registrado_en      TEXT    NOT NULL,
    UNIQUE (anio, numero)
);

-- ---------------------------------------------------------------------------
-- 2. TARIFARIO OFICIAL (CI-FIN-06 Arts. 8-9)
-- ---------------------------------------------------------------------------
CREATE TABLE tarifarios (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    vigente_desde        TEXT    NOT NULL,
    motivo               TEXT    NOT NULL CHECK (length(trim(motivo)) > 0),
    costo_referencia     TEXT    NOT NULL,
    origen_costo         TEXT    NOT NULL CHECK (origen_costo IN ('CICLO_CERRADO','PRESUPUESTO_D4')),
    estado               TEXT    NOT NULL DEFAULT 'PROPUESTO' CHECK (estado IN ('PROPUESTO','APROBADO','RECHAZADO')),
    propuesto_por        TEXT    NOT NULL,
    propuesto_en         TEXT    NOT NULL,
    aprobado_ca_por      TEXT,
    acta_ca              TEXT,
    aprobado_en          TEXT,
    observacion_ca       TEXT,
    CHECK (estado <> 'APROBADO' OR (aprobado_ca_por IS NOT NULL AND acta_ca IS NOT NULL))
);

CREATE TABLE tarifario_lineas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tarifario_id    INTEGER NOT NULL REFERENCES tarifarios(id),
    producto        TEXT    NOT NULL CHECK (producto IN ('CARNE_ENTERO','CARNE_CORTES','EXCEDENTE','GALLINAZA')),
    canal           TEXT    NOT NULL CHECK (canal IN ('HORECA','FRIAL','MERCADO_POPULAR','TODOS')),
    unidad          TEXT    NOT NULL,
    precio_cent     INTEGER NOT NULL CHECK (precio_cent > 0),
    UNIQUE (tarifario_id, producto, canal)
);

-- ---------------------------------------------------------------------------
-- 3. PRE-VENTA DEL LOTE: DESPACHOS, PEDIDOS Y COBERTURA (CI-FIN-13; FIN-ING-01)
-- ---------------------------------------------------------------------------
CREATE TABLE despachos (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id                INTEGER NOT NULL UNIQUE REFERENCES lotes(id),
    ciclo_id               INTEGER NOT NULL REFERENCES ciclos_produccion(id),
    fecha_faenado          TEXT    NOT NULL,
    origen_fecha           TEXT    NOT NULL CHECK (origen_fecha IN ('CIR-EGR-01','MANUAL')),
    aves                   INTEGER NOT NULL CHECK (aves > 0),
    respaldo_activado_por  TEXT,
    respaldo_activado_en   TEXT,
    respaldo_motivo        TEXT,
    estado                 TEXT    NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','CERRADO')),
    cerrado_por            TEXT,
    cerrado_en             TEXT,
    registrado_por         TEXT    NOT NULL,
    registrado_en          TEXT    NOT NULL,
    CHECK (estado <> 'CERRADO' OR cerrado_por IS NOT NULL)
);

CREATE TABLE pedidos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    numero               INTEGER NOT NULL UNIQUE,
    despacho_id          INTEGER NOT NULL REFERENCES despachos(id),
    cliente_id           INTEGER NOT NULL REFERENCES clientes(id),
    tipo                 TEXT    NOT NULL CHECK (tipo IN ('HORECA','INDICATIVO')),
    aves                 INTEGER NOT NULL CHECK (aves > 0),
    kg_estimado_mil      INTEGER NOT NULL CHECK (kg_estimado_mil > 0),
    producto             TEXT    NOT NULL CHECK (producto IN ('CARNE_ENTERO','CARNE_CORTES')),
    fecha_pedido         TEXT    NOT NULL,
    fecha_faenado        TEXT    NOT NULL,
    anticipacion_dias    INTEGER NOT NULL,
    cumple_anticipacion  INTEGER NOT NULL CHECK (cumple_anticipacion IN (0,1)),
    estado               TEXT    NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','ANULADO')),
    motivo_anulacion     TEXT,
    observacion          TEXT,
    registrado_por       TEXT    NOT NULL,
    registrado_en        TEXT    NOT NULL,
    CHECK (estado <> 'ANULADO' OR motivo_anulacion IS NOT NULL)
);
CREATE INDEX idx_pedidos_despacho ON pedidos(despacho_id);

CREATE TABLE verificaciones_cobertura (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    despacho_id          INTEGER NOT NULL REFERENCES despachos(id),
    fecha                TEXT    NOT NULL,
    aves_lote            INTEGER NOT NULL,
    horeca_aves          INTEGER NOT NULL,
    indicativo_aves      INTEGER NOT NULL,
    otros_aves           INTEGER NOT NULL,
    compradores_indicativos INTEGER NOT NULL,
    cobertura            TEXT    NOT NULL,
    cubre                INTEGER NOT NULL CHECK (cubre IN (0,1)),
    respaldo_requerido   INTEGER NOT NULL CHECK (respaldo_requerido IN (0,1)),
    en_plazo             INTEGER NOT NULL CHECK (en_plazo IN (0,1)),
    remanente_aves       INTEGER NOT NULL,
    observacion          TEXT,
    verificado_por       TEXT    NOT NULL,
    registrado_en        TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 4. RESOLUCIÓN DE CRÉDITO (FIN-ING-02; POL-05 Art. 2, desde 2028)
-- ---------------------------------------------------------------------------
CREATE TABLE resoluciones_credito (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id            INTEGER NOT NULL REFERENCES clientes(id),
    fecha                 TEXT    NOT NULL,
    plazo_dias            INTEGER NOT NULL CHECK (plazo_dias > 1),
    monto_maximo_cent     INTEGER NOT NULL CHECK (monto_maximo_cent > 0),
    historial_meses       INTEGER NOT NULL,
    entrega_semanal_cent  INTEGER NOT NULL,
    evaluacion            TEXT    NOT NULL CHECK (length(trim(evaluacion)) > 0),
    autorizada_por        TEXT    NOT NULL,
    comunicada_cv         INTEGER NOT NULL DEFAULT 1 CHECK (comunicada_cv IN (0,1)),
    estado                TEXT    NOT NULL DEFAULT 'VIGENTE' CHECK (estado IN ('VIGENTE','REVOCADA')),
    registrado_en         TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 5. NOTAS DE ENTREGA (FIN-ING-03 HORECA / FIN-ING-06 CONTADO)
-- ---------------------------------------------------------------------------
CREATE TABLE notas_entrega (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo                   TEXT    NOT NULL CHECK (tipo IN ('HORECA','CONTADO')),
    numero                 INTEGER NOT NULL,
    cliente_id             INTEGER NOT NULL REFERENCES clientes(id),
    canal                  TEXT    NOT NULL,
    despacho_id            INTEGER REFERENCES despachos(id),
    pedido_id              INTEGER REFERENCES pedidos(id),
    fecha                  TEXT    NOT NULL,
    hora_entrega           TEXT    NOT NULL,
    producto               TEXT    NOT NULL CHECK (producto IN ('CARNE_ENTERO','CARNE_CORTES','EXCEDENTE','GALLINAZA')),
    unidad                 TEXT    NOT NULL,
    cantidad_mil           INTEGER NOT NULL CHECK (cantidad_mil > 0),
    precio_cent            INTEGER NOT NULL CHECK (precio_cent > 0),
    total_cent             INTEGER NOT NULL CHECK (total_cent > 0),
    tarifario_id           INTEGER NOT NULL REFERENCES tarifarios(id),
    condicion              TEXT    NOT NULL CHECK (condicion IN ('CONTADO','CREDITO')),
    dias_credito           INTEGER NOT NULL DEFAULT 0 CHECK (dias_credito >= 0),
    vencimiento            TEXT,
    medio_pago             TEXT    NOT NULL CHECK (medio_pago IN ('EFECTIVO','TRANSFERENCIA','QR_ACH','DEPOSITO',
                                                                  'CHEQUE_NOMINATIVO')),
    comprobante_ref        TEXT    NOT NULL CHECK (length(trim(comprobante_ref)) > 0),
    certificado_rau_ref    TEXT    NOT NULL CHECK (length(trim(certificado_rau_ref)) > 0),
    resolucion_id          INTEGER REFERENCES resoluciones_credito(id),
    cuenta_ingreso         TEXT    NOT NULL REFERENCES cuentas(codigo),
    cuenta_cobro           TEXT    NOT NULL REFERENCES cuentas(codigo),
    asiento_venta_id       INTEGER NOT NULL REFERENCES asientos(id),
    costo_cent             INTEGER,
    asiento_costo_id       INTEGER REFERENCES asientos(id),
    recibido_por           TEXT    NOT NULL CHECK (length(trim(recibido_por)) > 0),
    entregado_por          TEXT    NOT NULL CHECK (length(trim(entregado_por)) > 0),
    registrado_por         TEXT    NOT NULL,
    registrado_en          TEXT    NOT NULL,
    UNIQUE (tipo, numero),
    CHECK (condicion = 'CONTADO' OR (tipo = 'HORECA' AND dias_credito >= 1 AND vencimiento IS NOT NULL)),
    CHECK (condicion = 'CREDITO' OR dias_credito = 0),
    CHECK (dias_credito <= 1 OR resolucion_id IS NOT NULL)
);
CREATE INDEX idx_notas_fecha ON notas_entrega(fecha);

-- ---------------------------------------------------------------------------
-- 6. CUENTAS POR COBRAR Y COBROS (FIN-ING-05; CI-FIN-15)
-- ---------------------------------------------------------------------------
CREATE TABLE cuentas_cobrar (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    nota_id              INTEGER NOT NULL UNIQUE REFERENCES notas_entrega(id),
    cliente_id           INTEGER NOT NULL REFERENCES clientes(id),
    monto_cent           INTEGER NOT NULL CHECK (monto_cent > 0),
    fecha_entrega        TEXT    NOT NULL,
    vencimiento          TEXT    NOT NULL,
    cobrado_cent         INTEGER NOT NULL DEFAULT 0 CHECK (cobrado_cent >= 0),
    devuelto_cent        INTEGER NOT NULL DEFAULT 0 CHECK (devuelto_cent >= 0),
    fecha_cobro          TEXT,
    incumplida           INTEGER NOT NULL DEFAULT 0 CHECK (incumplida IN (0,1)),
    reportada_en         TEXT,
    reporte_en_plazo     INTEGER CHECK (reporte_en_plazo IS NULL OR reporte_en_plazo IN (0,1)),
    estado               TEXT    NOT NULL DEFAULT 'PENDIENTE' CHECK (estado IN ('PENDIENTE','COBRADA','ANULADA')),
    CHECK (cobrado_cent + devuelto_cent <= monto_cent)
);
CREATE INDEX idx_cxc_cliente ON cuentas_cobrar(cliente_id, vencimiento);

CREATE TABLE cobros (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id          INTEGER NOT NULL REFERENCES cuentas_cobrar(id),
    fecha              TEXT    NOT NULL,
    monto_cent         INTEGER NOT NULL CHECK (monto_cent > 0),
    medio_pago         TEXT    NOT NULL CHECK (medio_pago IN ('EFECTIVO','TRANSFERENCIA','QR_ACH','DEPOSITO',
                                                              'CHEQUE_NOMINATIVO')),
    comprobante_ref    TEXT    NOT NULL,
    en_plazo           INTEGER NOT NULL CHECK (en_plazo IN (0,1)),
    asiento_id         INTEGER NOT NULL REFERENCES asientos(id),
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 7. DEVOLUCIONES POR RECHAZO DE INOCUIDAD (PC-08; CI-FIN-12)
-- ---------------------------------------------------------------------------
CREATE TABLE devoluciones (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    nota_id            INTEGER NOT NULL REFERENCES notas_entrega(id),
    fecha              TEXT    NOT NULL,
    cantidad_mil       INTEGER NOT NULL CHECK (cantidad_mil > 0),
    monto_cent         INTEGER NOT NULL CHECK (monto_cent > 0),
    a_cuenta_cobrar_cent INTEGER NOT NULL CHECK (a_cuenta_cobrar_cent >= 0),
    saldo_favor_cent   INTEGER NOT NULL CHECK (saldo_favor_cent >= 0),
    motivo             TEXT    NOT NULL CHECK (length(trim(motivo)) > 0),
    informe_ref        TEXT    NOT NULL CHECK (length(trim(informe_ref)) > 0),
    asiento_id         INTEGER NOT NULL REFERENCES asientos(id),
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    CHECK (a_cuenta_cobrar_cent + saldo_favor_cent = monto_cent)
);

-- ---------------------------------------------------------------------------
-- 8. PARTE DIARIO DE RECAUDACIÓN (FIN-ING-07)
-- ---------------------------------------------------------------------------
CREATE TABLE partes_recaudacion (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT    NOT NULL UNIQUE,
    carne_cent          INTEGER NOT NULL,
    alimento_cent       INTEGER NOT NULL,
    gallinaza_cent      INTEGER NOT NULL,
    cobros_cent         INTEGER NOT NULL,
    total_cent          INTEGER NOT NULL,
    efectivo_cent       INTEGER NOT NULL,
    bancarizado_cent    INTEGER NOT NULL,
    depositado_cent     INTEGER NOT NULL,
    boleta_ref          TEXT,
    caja_movimiento_id  INTEGER REFERENCES caja_movimientos(id),
    verificado_por      TEXT,
    verificado_en       TEXT,
    registrado_por      TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    CHECK (carne_cent + alimento_cent + gallinaza_cent + cobros_cent = total_cent),
    CHECK (efectivo_cent + bancarizado_cent = total_cent),
    CHECK (depositado_cent = efectivo_cent)
);

-- ---------------------------------------------------------------------------
-- 9. ANÁLISIS DE MARGEN POR CANAL (FIN-ING-08; CI-FIN-06 Arts. 5-7)
-- ---------------------------------------------------------------------------
CREATE TABLE analisis_margen (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    anio               INTEGER NOT NULL,
    mes                INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    costo_unitario     TEXT    NOT NULL,
    ciclo_referencia   TEXT    NOT NULL,
    ajuste_requerido   INTEGER NOT NULL CHECK (ajuste_requerido IN (0,1)),
    canales_ajuste     TEXT,
    elaborado_por      TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    UNIQUE (anio, mes)
);

CREATE TABLE analisis_margen_lineas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    analisis_id        INTEGER NOT NULL REFERENCES analisis_margen(id),
    canal              TEXT    NOT NULL,
    ingresos_cent      INTEGER NOT NULL,
    kg_mil             INTEGER NOT NULL,
    precio_promedio    TEXT,
    margen             TEXT,
    bajo_umbral        INTEGER NOT NULL CHECK (bajo_umbral IN (0,1)),
    UNIQUE (analisis_id, canal)
);

-- ---------------------------------------------------------------------------
-- 10. PROVISIÓN DE INCOBRABLES (POL-05 Art. 3; desde 2028)
-- ---------------------------------------------------------------------------
CREATE TABLE provisiones_incobrables (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha              TEXT    NOT NULL,
    saldo_deudores_cent INTEGER NOT NULL,
    requerida_cent     INTEGER NOT NULL,
    anterior_cent      INTEGER NOT NULL,
    ajuste_cent        INTEGER NOT NULL,
    detalle_json       TEXT    NOT NULL,
    asiento_id         INTEGER REFERENCES asientos(id),
    registrado_por     TEXT    NOT NULL,
    registrado_en      TEXT    NOT NULL,
    CHECK (ajuste_cent = 0 OR asiento_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- 11. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_clientes_no_delete BEFORE DELETE ON clientes
BEGIN SELECT RAISE(ABORT, 'La ficha del cliente no se borra.'); END;
CREATE TRIGGER trg_suspensiones_no_delete BEFORE DELETE ON suspensiones_credito
BEGIN SELECT RAISE(ABORT, 'La circular CIR-ING-01 no se borra.'); END;
CREATE TRIGGER trg_suspensiones_fija BEFORE UPDATE ON suspensiones_credito
WHEN OLD.cliente_id <> NEW.cliente_id OR OLD.fecha <> NEW.fecha OR OLD.detalle_json <> NEW.detalle_json
  OR OLD.restablecida_por IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'La circular CIR-ING-01 solo admite la firma de la GG y el restablecimiento.'); END;
CREATE TRIGGER trg_tarifarios_no_delete BEFORE DELETE ON tarifarios
BEGIN SELECT RAISE(ABORT, 'El tarifario no se borra.'); END;
CREATE TRIGGER trg_tarifarios_resuelto BEFORE UPDATE ON tarifarios
WHEN OLD.estado <> 'PROPUESTO'
BEGIN SELECT RAISE(ABORT, 'El tarifario aprobado o rechazado no se modifica: proponga uno nuevo.'); END;
CREATE TRIGGER trg_tarifario_lineas_no_update BEFORE UPDATE ON tarifario_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del tarifario no se modifican.'); END;
CREATE TRIGGER trg_tarifario_lineas_no_delete BEFORE DELETE ON tarifario_lineas
BEGIN SELECT RAISE(ABORT, 'Las líneas del tarifario no se borran.'); END;
CREATE TRIGGER trg_despachos_no_delete BEFORE DELETE ON despachos
BEGIN SELECT RAISE(ABORT, 'El despacho no se borra.'); END;
CREATE TRIGGER trg_despachos_cerrado BEFORE UPDATE ON despachos
WHEN OLD.estado = 'CERRADO' OR OLD.aves <> NEW.aves OR OLD.lote_id <> NEW.lote_id
BEGIN SELECT RAISE(ABORT, 'El despacho cerrado no se modifica.'); END;
CREATE TRIGGER trg_pedidos_no_delete BEFORE DELETE ON pedidos
BEGIN SELECT RAISE(ABORT, 'El pedido no se borra: se anula.'); END;
CREATE TRIGGER trg_pedidos_solo_anulacion BEFORE UPDATE ON pedidos
WHEN OLD.estado <> 'VIGENTE' OR OLD.aves <> NEW.aves OR OLD.cliente_id <> NEW.cliente_id
  OR OLD.fecha_pedido <> NEW.fecha_pedido
BEGIN SELECT RAISE(ABORT, 'El pedido solo admite su anulación.'); END;
CREATE TRIGGER trg_verif_cobertura_no_update BEFORE UPDATE ON verificaciones_cobertura
BEGIN SELECT RAISE(ABORT, 'La verificación de cobertura no se modifica.'); END;
CREATE TRIGGER trg_verif_cobertura_no_delete BEFORE DELETE ON verificaciones_cobertura
BEGIN SELECT RAISE(ABORT, 'La verificación de cobertura no se borra.'); END;
CREATE TRIGGER trg_resoluciones_no_delete BEFORE DELETE ON resoluciones_credito
BEGIN SELECT RAISE(ABORT, 'La resolución FIN-ING-02 no se borra.'); END;
CREATE TRIGGER trg_resoluciones_solo_revocar BEFORE UPDATE ON resoluciones_credito
WHEN OLD.estado <> 'VIGENTE' OR OLD.plazo_dias <> NEW.plazo_dias OR OLD.monto_maximo_cent <> NEW.monto_maximo_cent
BEGIN SELECT RAISE(ABORT, 'La resolución FIN-ING-02 solo admite su revocación.'); END;
CREATE TRIGGER trg_notas_no_update BEFORE UPDATE ON notas_entrega
WHEN OLD.asiento_costo_id IS NOT NULL OR OLD.total_cent <> NEW.total_cent OR OLD.cantidad_mil <> NEW.cantidad_mil
  OR OLD.precio_cent <> NEW.precio_cent OR OLD.cliente_id <> NEW.cliente_id OR OLD.fecha <> NEW.fecha
BEGIN SELECT RAISE(ABORT, 'La nota de entrega solo admite el registro de su costo de ventas, una sola vez.'); END;
CREATE TRIGGER trg_notas_no_delete BEFORE DELETE ON notas_entrega
BEGIN SELECT RAISE(ABORT, 'La nota de entrega no se borra: se registra la devolución.'); END;
CREATE TRIGGER trg_cxc_no_delete BEFORE DELETE ON cuentas_cobrar
BEGIN SELECT RAISE(ABORT, 'La cuenta por cobrar no se borra.'); END;
CREATE TRIGGER trg_cxc_fija BEFORE UPDATE ON cuentas_cobrar
WHEN OLD.monto_cent <> NEW.monto_cent OR OLD.vencimiento <> NEW.vencimiento OR OLD.nota_id <> NEW.nota_id
  OR NEW.cobrado_cent < OLD.cobrado_cent OR (OLD.incumplida = 1 AND NEW.incumplida = 0)
BEGIN SELECT RAISE(ABORT, 'La cuenta por cobrar solo registra cobros, devoluciones, incumplimientos y reportes.'); END;
CREATE TRIGGER trg_cobros_no_update BEFORE UPDATE ON cobros
BEGIN SELECT RAISE(ABORT, 'El cobro no se modifica: reverse su asiento.'); END;
CREATE TRIGGER trg_cobros_no_delete BEFORE DELETE ON cobros
BEGIN SELECT RAISE(ABORT, 'El cobro no se borra.'); END;
CREATE TRIGGER trg_devoluciones_no_update BEFORE UPDATE ON devoluciones
BEGIN SELECT RAISE(ABORT, 'La devolución no se modifica.'); END;
CREATE TRIGGER trg_devoluciones_no_delete BEFORE DELETE ON devoluciones
BEGIN SELECT RAISE(ABORT, 'La devolución no se borra.'); END;
CREATE TRIGGER trg_partes_no_delete BEFORE DELETE ON partes_recaudacion
BEGIN SELECT RAISE(ABORT, 'El parte diario no se borra.'); END;
CREATE TRIGGER trg_partes_solo_verificacion BEFORE UPDATE ON partes_recaudacion
WHEN OLD.verificado_por IS NOT NULL OR OLD.total_cent <> NEW.total_cent OR OLD.depositado_cent <> NEW.depositado_cent
BEGIN SELECT RAISE(ABORT, 'El parte diario solo admite la verificación de la GG, una sola vez.'); END;
CREATE TRIGGER trg_analisis_no_update BEFORE UPDATE ON analisis_margen
BEGIN SELECT RAISE(ABORT, 'El análisis FIN-ING-08 no se modifica.'); END;
CREATE TRIGGER trg_analisis_no_delete BEFORE DELETE ON analisis_margen
BEGIN SELECT RAISE(ABORT, 'El análisis FIN-ING-08 no se borra.'); END;
CREATE TRIGGER trg_analisis_lin_no_update BEFORE UPDATE ON analisis_margen_lineas
BEGIN SELECT RAISE(ABORT, 'El análisis FIN-ING-08 no se modifica.'); END;
CREATE TRIGGER trg_analisis_lin_no_delete BEFORE DELETE ON analisis_margen_lineas
BEGIN SELECT RAISE(ABORT, 'El análisis FIN-ING-08 no se borra.'); END;
CREATE TRIGGER trg_provisiones_no_update BEFORE UPDATE ON provisiones_incobrables
BEGIN SELECT RAISE(ABORT, 'La provisión no se modifica.'); END;
CREATE TRIGGER trg_provisiones_no_delete BEFORE DELETE ON provisiones_incobrables
BEGIN SELECT RAISE(ABORT, 'La provisión no se borra.'); END;
