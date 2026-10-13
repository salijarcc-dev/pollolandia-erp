-- ============================================================================
-- ERP POLLOLANDIA - Migración 3 (Fase 3: Tesorería, Ciclo 1)
-- Caja y fondo fijo (CI-FIN-01), bancos y movimientos, compromisos y SME
-- (POL-04), flujo de caja proyectado (FIN-TES-05), alertas y medidas
-- correctivas (FIN-TES-07/08), reporte diario de saldo (FIN-TES-06) y
-- conciliación bancaria (FIN-INF-02, CI-FIN-07).
-- Todo hecho económico se registra además como asiento (asiento_id).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CAJA: FONDO FIJO, MOVIMIENTOS, REPOSICIONES Y ARQUEOS
-- ---------------------------------------------------------------------------
CREATE TABLE fondos_caja (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    monto_fijo_cent     INTEGER NOT NULL CHECK (monto_fijo_cent > 0),
    custodio            TEXT    NOT NULL CHECK (length(trim(custodio)) > 0),
    resolucion_ref      TEXT    NOT NULL CHECK (length(trim(resolucion_ref)) > 0),
    fecha_constitucion  TEXT    NOT NULL,
    asiento_id          INTEGER NOT NULL REFERENCES asientos(id),
    estado              TEXT    NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','CERRADO')),
    registrado_por      TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL
);
CREATE UNIQUE INDEX ux_fondo_activo ON fondos_caja(estado) WHERE estado = 'ACTIVO';

CREATE TABLE reposiciones_caja (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fondo_id         INTEGER NOT NULL REFERENCES fondos_caja(id),
    fecha_solicitud  TEXT    NOT NULL,
    total_cent       INTEGER NOT NULL CHECK (total_cent > 0),
    estado           TEXT    NOT NULL DEFAULT 'SOLICITADA'
                     CHECK (estado IN ('SOLICITADA','VALIDADA','RECHAZADA','EJECUTADA')),
    solicitado_por   TEXT    NOT NULL,
    validado_por     TEXT,
    validado_en      TEXT,
    observacion      TEXT,
    cuenta_banco     TEXT    REFERENCES cuentas(codigo),
    asiento_id       INTEGER REFERENCES asientos(id),
    ejecutado_en     TEXT
);

CREATE TABLE caja_movimientos (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    fondo_id              INTEGER NOT NULL REFERENCES fondos_caja(id),
    tipo                  TEXT    NOT NULL CHECK (tipo IN
                          ('CONSTITUCION','GASTO_MENOR','INGRESO_EFECTIVO','DEPOSITO_EXCEDENTE','REPOSICION')),
    fecha                 TEXT    NOT NULL,
    monto_cent            INTEGER NOT NULL CHECK (monto_cent > 0),
    comprobante           TEXT,
    beneficiario          TEXT,
    descripcion           TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    cuenta_contrapartida  TEXT    NOT NULL REFERENCES cuentas(codigo),
    asiento_id            INTEGER NOT NULL REFERENCES asientos(id),
    reposicion_id         INTEGER REFERENCES reposiciones_caja(id),
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (tipo <> 'GASTO_MENOR' OR length(trim(IFNULL(comprobante,''))) > 0)
);
CREATE UNIQUE INDEX ux_caja_comprobante ON caja_movimientos(comprobante) WHERE tipo = 'GASTO_MENOR';
CREATE INDEX idx_caja_fecha ON caja_movimientos(fecha, tipo);

CREATE TABLE arqueos_caja (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha                           TEXT    NOT NULL,
    fondo_id                        INTEGER NOT NULL REFERENCES fondos_caja(id),
    efectivo_contado_cent           INTEGER NOT NULL CHECK (efectivo_contado_cent >= 0),
    comprobantes_no_repuestos_cent  INTEGER NOT NULL CHECK (comprobantes_no_repuestos_cent >= 0),
    total_fisico_cent               INTEGER NOT NULL,
    saldo_contable_cent             INTEGER NOT NULL,
    diferencia_cent                 INTEGER NOT NULL,
    limite_diario_cumplido          INTEGER NOT NULL CHECK (limite_diario_cumplido IN (0,1)),
    dias_con_exceso                 INTEGER NOT NULL DEFAULT 0,
    custodio_presente               TEXT    NOT NULL CHECK (length(trim(custodio_presente)) > 0),
    observaciones                   TEXT,
    arqueado_por                    TEXT    NOT NULL,
    registrado_en                   TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. BANCOS
-- ---------------------------------------------------------------------------
CREATE TABLE cuentas_bancarias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_codigo   TEXT NOT NULL UNIQUE REFERENCES cuentas(codigo)
                    CHECK (cuenta_codigo IN ('1.1.01.03','1.1.01.04')),
    banco           TEXT NOT NULL CHECK (length(trim(banco)) > 0),
    numero_cuenta   TEXT NOT NULL CHECK (length(trim(numero_cuenta)) > 0),
    moneda          TEXT NOT NULL DEFAULT 'BOB',
    activa          INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0,1)),
    registrado_por  TEXT NOT NULL,
    registrado_en   TEXT NOT NULL
);

CREATE TABLE compromisos (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo                      TEXT    NOT NULL CHECK (tipo IN ('COMPRA','PERSONAL_EVENTUAL','OTRO')),
    fecha                     TEXT    NOT NULL,
    monto_cent                INTEGER NOT NULL CHECK (monto_cent > 0),
    descripcion               TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    saldo_bancario_cent       INTEGER NOT NULL,
    compromisos_previos_cent  INTEGER NOT NULL,
    saldo_proyectado_cent     INTEGER NOT NULL,
    sme_cent                  INTEGER NOT NULL,
    umbral_cent               INTEGER NOT NULL,
    estado                    TEXT    NOT NULL CHECK (estado IN ('VIGENTE','SUSPENDIDO','AUTORIZADO','PAGADO','ANULADO')),
    autorizacion_id           INTEGER REFERENCES autorizaciones(id),
    registrado_por            TEXT    NOT NULL,
    registrado_en             TEXT    NOT NULL,
    actualizado_en            TEXT
);

CREATE TABLE conciliaciones (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_codigo                   TEXT    NOT NULL REFERENCES cuentas(codigo),
    anio                            INTEGER NOT NULL,
    mes                             INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    fecha_corte                     TEXT    NOT NULL,
    saldo_libros_cent               INTEGER NOT NULL,
    saldo_extracto_cent             INTEGER NOT NULL,
    saldo_conciliado_libros_cent    INTEGER NOT NULL,
    saldo_conciliado_banco_cent     INTEGER NOT NULL,
    diferencia_cent                 INTEGER NOT NULL,
    fecha_elaboracion               TEXT    NOT NULL,
    fecha_limite                    TEXT    NOT NULL,
    dentro_plazo                    INTEGER NOT NULL CHECK (dentro_plazo IN (0,1)),
    elaborado_por                   TEXT    NOT NULL,
    estado                          TEXT    NOT NULL DEFAULT 'PREPARADA'
                                    CHECK (estado IN ('PREPARADA','SUSTITUIDA','FIRMADA')),
    firmado_por                     TEXT,
    firmado_en                      TEXT,
    firma_dentro_plazo              INTEGER CHECK (firma_dentro_plazo IS NULL OR firma_dentro_plazo IN (0,1))
);
-- Una sola conciliación vigente (preparada o firmada) por cuenta y mes.
CREATE UNIQUE INDEX ux_conciliacion_vigente ON conciliaciones(cuenta_codigo, anio, mes)
    WHERE estado IN ('PREPARADA','FIRMADA');

CREATE TABLE movimientos_bancarios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_codigo    TEXT    NOT NULL REFERENCES cuentas(codigo) CHECK (cuenta_codigo IN ('1.1.01.03','1.1.01.04')),
    tipo             TEXT    NOT NULL CHECK (tipo IN ('DEPOSITO','PAGO','TRANSFERENCIA_SALIDA','TRANSFERENCIA_ENTRADA',
                                                      'CARGO_BANCARIO','ABONO_BANCARIO')),
    fecha            TEXT    NOT NULL,
    monto_cent       INTEGER NOT NULL CHECK (monto_cent > 0),
    contrapartida    TEXT    NOT NULL REFERENCES cuentas(codigo),
    beneficiario     TEXT,
    nro_cheque       TEXT,
    documento_ref    TEXT,
    descripcion      TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    categoria_pago   TEXT,
    autorizacion_id  INTEGER REFERENCES autorizaciones(id),
    compromiso_id    INTEGER REFERENCES compromisos(id),
    asiento_id       INTEGER NOT NULL REFERENCES asientos(id),
    conciliacion_id  INTEGER REFERENCES conciliaciones(id),
    registrado_por   TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);
CREATE INDEX idx_movbanco_cuenta_fecha ON movimientos_bancarios(cuenta_codigo, fecha);

CREATE TABLE conciliacion_partidas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conciliacion_id  INTEGER NOT NULL REFERENCES conciliaciones(id),
    tipo             TEXT    NOT NULL CHECK (tipo IN ('CHEQUE_CIRCULACION','DEPOSITO_TRANSITO','CARGO_NO_REGISTRADO',
                                                      'ABONO_NO_REGISTRADO','ERROR_LIBROS_MAS','ERROR_LIBROS_MENOS')),
    descripcion      TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    fecha            TEXT    NOT NULL,
    monto_cent       INTEGER NOT NULL CHECK (monto_cent > 0),
    movimiento_id    INTEGER REFERENCES movimientos_bancarios(id),
    asiento_id       INTEGER REFERENCES asientos(id)
);

-- ---------------------------------------------------------------------------
-- 3. FLUJO PROYECTADO, ALERTAS, MEDIDAS Y REPORTE DIARIO
-- ---------------------------------------------------------------------------
CREATE TABLE flujos_proyectados (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio            INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    fecha_elaboracion    TEXT    NOT NULL,
    inicio_ciclo         TEXT    NOT NULL,
    fin_ciclo_actual     TEXT    NOT NULL,
    fin_ciclo_siguiente  TEXT    NOT NULL,
    saldo_inicial_cent   INTEGER NOT NULL,
    descripcion          TEXT    NOT NULL,
    estado               TEXT    NOT NULL DEFAULT 'BORRADOR' CHECK (estado IN ('BORRADOR','APROBADO')),
    elaborado_por        TEXT    NOT NULL,
    aprobado_por         TEXT,
    aprobado_en          TEXT,
    CHECK (fin_ciclo_siguiente > fin_ciclo_actual AND fin_ciclo_actual >= inicio_ciclo)
);

CREATE TABLE flujo_lineas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    flujo_id     INTEGER NOT NULL REFERENCES flujos_proyectados(id),
    fecha        TEXT    NOT NULL,
    categoria    TEXT    NOT NULL CHECK (categoria IN ('INGRESO_VENTAS','EGRESO_OPERATIVO','CAPEX','EXCEDENTES')),
    descripcion  TEXT    NOT NULL CHECK (length(trim(descripcion)) > 0),
    monto_cent   INTEGER NOT NULL CHECK (monto_cent > 0)
);
CREATE INDEX idx_flujo_lineas ON flujo_lineas(flujo_id, fecha);

CREATE TABLE alertas_liquidez (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha               TEXT    NOT NULL,
    origen              TEXT    NOT NULL CHECK (origen IN ('FLUJO_SME','DESVIO_FLUJO','REPORTE_DIARIO')),
    flujo_id            INTEGER REFERENCES flujos_proyectados(id),
    descripcion         TEXT    NOT NULL,
    porcentaje_desvio   TEXT,
    punto_horizonte     TEXT,
    riesgo_bajo_sme     INTEGER NOT NULL CHECK (riesgo_bajo_sme IN (0,1)),
    medida_sugerida     TEXT,
    emitida_por         TEXT    NOT NULL,
    emitida_en          TEXT    NOT NULL,
    estado              TEXT    NOT NULL DEFAULT 'ABIERTA' CHECK (estado IN ('ABIERTA','ATENDIDA')),
    recibida_por        TEXT,
    recibida_en         TEXT
);

CREATE TABLE medidas_correctivas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    alerta_id             INTEGER NOT NULL UNIQUE REFERENCES alertas_liquidez(id),
    acelerar_cobranza     INTEGER NOT NULL DEFAULT 0 CHECK (acelerar_cobranza IN (0,1)),
    postergar_capex       INTEGER NOT NULL DEFAULT 0 CHECK (postergar_capex IN (0,1)),
    negociar_proveedores  INTEGER NOT NULL DEFAULT 0 CHECK (negociar_proveedores IN (0,1)),
    informar_asamblea     INTEGER NOT NULL DEFAULT 0 CHECK (informar_asamblea IN (0,1)),
    fecha_aplicacion      TEXT    NOT NULL,
    resultado             TEXT,
    adoptada_por          TEXT    NOT NULL,
    adoptada_en           TEXT    NOT NULL,
    fiscalizada_por       TEXT,
    fiscalizada_en        TEXT,
    CHECK (acelerar_cobranza + postergar_capex + negociar_proveedores + informar_asamblea >= 1)
);

CREATE TABLE reportes_saldo_diario (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha            TEXT    NOT NULL UNIQUE,
    saldo_cent       INTEGER NOT NULL,
    sme_cent         INTEGER NOT NULL CHECK (sme_cent > 0),
    cumplimiento     TEXT    NOT NULL,
    semaforo         TEXT    NOT NULL CHECK (semaforo IN ('MALO','NORMAL','BUENO')),
    requiere_alerta  INTEGER NOT NULL CHECK (requiere_alerta IN (0,1)),
    alerta_id        INTEGER REFERENCES alertas_liquidez(id),
    monitoreado_por  TEXT    NOT NULL,
    registrado_en    TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 4. INMUTABILIDAD (los registros de tesorería no se borran)
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_caja_mov_no_delete BEFORE DELETE ON caja_movimientos
BEGIN SELECT RAISE(ABORT, 'Los movimientos de caja no se borran: use la reversión del asiento.'); END;

CREATE TRIGGER trg_caja_mov_solo_reposicion BEFORE UPDATE ON caja_movimientos
WHEN OLD.tipo <> NEW.tipo OR OLD.fecha <> NEW.fecha OR OLD.monto_cent <> NEW.monto_cent
  OR IFNULL(OLD.comprobante,'') <> IFNULL(NEW.comprobante,'') OR OLD.asiento_id <> NEW.asiento_id
  OR OLD.cuenta_contrapartida <> NEW.cuenta_contrapartida
  OR (OLD.reposicion_id IS NOT NULL AND IFNULL(NEW.reposicion_id, -1) <> OLD.reposicion_id
      AND (SELECT estado FROM reposiciones_caja WHERE id = OLD.reposicion_id) <> 'RECHAZADA')
BEGIN SELECT RAISE(ABORT, 'Un movimiento de caja no se modifica.'); END;

CREATE TRIGGER trg_arqueos_no_update BEFORE UPDATE ON arqueos_caja
BEGIN SELECT RAISE(ABORT, 'Un acta de arqueo no se modifica.'); END;
CREATE TRIGGER trg_arqueos_no_delete BEFORE DELETE ON arqueos_caja
BEGIN SELECT RAISE(ABORT, 'Un acta de arqueo no se borra.'); END;

CREATE TRIGGER trg_movbanco_no_delete BEFORE DELETE ON movimientos_bancarios
BEGIN SELECT RAISE(ABORT, 'Los movimientos bancarios no se borran: use la reversión del asiento.'); END;

CREATE TRIGGER trg_movbanco_solo_conciliacion BEFORE UPDATE ON movimientos_bancarios
WHEN OLD.cuenta_codigo <> NEW.cuenta_codigo OR OLD.tipo <> NEW.tipo OR OLD.fecha <> NEW.fecha
  OR OLD.monto_cent <> NEW.monto_cent OR OLD.asiento_id <> NEW.asiento_id
  OR OLD.contrapartida <> NEW.contrapartida
BEGIN SELECT RAISE(ABORT, 'Un movimiento bancario no se modifica.'); END;

CREATE TRIGGER trg_conciliacion_firmada BEFORE UPDATE ON conciliaciones
WHEN OLD.estado IN ('FIRMADA','SUSTITUIDA')
  OR OLD.saldo_libros_cent <> NEW.saldo_libros_cent OR OLD.saldo_extracto_cent <> NEW.saldo_extracto_cent
  OR OLD.diferencia_cent <> NEW.diferencia_cent
BEGIN SELECT RAISE(ABORT, 'Una conciliación firmada o sustituida no se modifica, ni se alteran sus saldos.'); END;
CREATE TRIGGER trg_conciliacion_no_delete BEFORE DELETE ON conciliaciones
BEGIN SELECT RAISE(ABORT, 'Las conciliaciones no se borran.'); END;
CREATE TRIGGER trg_partidas_no_delete BEFORE DELETE ON conciliacion_partidas
BEGIN SELECT RAISE(ABORT, 'Las partidas de conciliación no se borran.'); END;

CREATE TRIGGER trg_reportes_diarios_no_update BEFORE UPDATE ON reportes_saldo_diario
WHEN OLD.saldo_cent <> NEW.saldo_cent OR OLD.sme_cent <> NEW.sme_cent OR OLD.fecha <> NEW.fecha
BEGIN SELECT RAISE(ABORT, 'Un reporte diario no se modifica.'); END;
CREATE TRIGGER trg_reportes_diarios_no_delete BEFORE DELETE ON reportes_saldo_diario
BEGIN SELECT RAISE(ABORT, 'Los reportes diarios no se borran.'); END;

CREATE TRIGGER trg_flujo_aprobado BEFORE UPDATE ON flujos_proyectados
WHEN OLD.estado = 'APROBADO'
BEGIN SELECT RAISE(ABORT, 'Un flujo aprobado no se modifica: elabore uno nuevo.'); END;
CREATE TRIGGER trg_flujo_lineas_aprobado BEFORE INSERT ON flujo_lineas
WHEN (SELECT estado FROM flujos_proyectados WHERE id = NEW.flujo_id) = 'APROBADO'
BEGIN SELECT RAISE(ABORT, 'Un flujo aprobado no admite nuevas líneas.'); END;

CREATE TRIGGER trg_alertas_no_delete BEFORE DELETE ON alertas_liquidez
BEGIN SELECT RAISE(ABORT, 'Las alertas de liquidez no se borran.'); END;
CREATE TRIGGER trg_medidas_no_delete BEFORE DELETE ON medidas_correctivas
BEGIN SELECT RAISE(ABORT, 'Las medidas correctivas no se borran.'); END;
CREATE TRIGGER trg_compromisos_no_delete BEFORE DELETE ON compromisos
BEGIN SELECT RAISE(ABORT, 'Los compromisos no se borran: anúlelos.'); END;
