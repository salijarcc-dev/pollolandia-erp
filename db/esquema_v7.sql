-- ============================================================================
-- ERP POLLOLANDIA - Migración 7 (Fase 7: Costos matriciales, Ciclo 3)
-- Consumos de inventario al CPP por sufijo; CIF registrados y prorrateados
-- (sufijos 30-37); corte de MOD al día 73; producción de alimento propio
-- (6.1.01.02 -> 1.1.03.13), traslado al sufijo 11 y ensaquillado del
-- excedente (sufijo 13); kárdex de productos terminados (carne, alimento,
-- excedente) con costo de ventas (6.1.02.01 / 6.1.02.02); cierre del ciclo
-- (1.1.03.12 contra 6.1.01.01) con su Informe de Costo de Ciclo (FIN-TRA-06,
-- FIN-TRA-07) y presupuesto de costos por sufijo (Manual Presupuestario D.4).
-- Montos en centavos (*_cent); cantidades en milésimas (*_mil).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CONSUMOS DE INVENTARIO AL CPP (PC-07; Manual de Costos §6.1)
-- ---------------------------------------------------------------------------
CREATE TABLE consumos_costo (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id          INTEGER REFERENCES ciclos_produccion(id),
    centro_costo      TEXT    NOT NULL REFERENCES centros_costo(codigo),
    sufijo            TEXT    NOT NULL REFERENCES sufijos_costo(sufijo),
    producto          TEXT,
    insumo_id         INTEGER REFERENCES insumos(id),
    fecha             TEXT    NOT NULL,
    cantidad_mil      INTEGER NOT NULL CHECK (cantidad_mil > 0),
    valor_cent        INTEGER NOT NULL CHECK (valor_cent > 0),
    kardex_id         INTEGER REFERENCES kardex(id),
    asiento_id        INTEGER NOT NULL REFERENCES asientos(id),
    origen            TEXT    NOT NULL CHECK (origen IN ('MANUAL','BITACORA')),
    bitacora_desde    TEXT,
    bitacora_hasta    TEXT,
    kg_bitacora_mil   INTEGER,
    documento_ref     TEXT,
    registrado_por    TEXT    NOT NULL,
    registrado_en     TEXT    NOT NULL,
    CHECK (insumo_id IS NOT NULL OR producto IS NOT NULL),
    CHECK (origen = 'MANUAL' OR (bitacora_desde IS NOT NULL AND bitacora_hasta IS NOT NULL))
);
CREATE INDEX idx_consumos_ciclo ON consumos_costo(ciclo_id, fecha);

-- ---------------------------------------------------------------------------
-- 2. COSTOS INDIRECTOS DE FABRICACIÓN (sufijos 30-37; CI-FIN-03)
-- ---------------------------------------------------------------------------
CREATE TABLE cif_registrados (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id          INTEGER REFERENCES ciclos_produccion(id),
    sufijo            TEXT    NOT NULL REFERENCES sufijos_costo(sufijo),
    fecha             TEXT    NOT NULL,
    monto_cent        INTEGER NOT NULL CHECK (monto_cent > 0),
    monto_carne_cent  INTEGER NOT NULL CHECK (monto_carne_cent >= 0),
    monto_alimento_cent INTEGER NOT NULL CHECK (monto_alimento_cent >= 0),
    pct_carne         TEXT    NOT NULL,
    base_prorrateo    TEXT    NOT NULL CHECK (base_prorrateo IN ('DIRECTO','HORAS','DEFECTO')),
    contrapartida     TEXT    NOT NULL REFERENCES cuentas(codigo),
    concepto          TEXT    NOT NULL CHECK (length(trim(concepto)) > 0),
    documento_ref     TEXT,
    asiento_id        INTEGER NOT NULL REFERENCES asientos(id),
    registrado_por    TEXT    NOT NULL,
    registrado_en     TEXT    NOT NULL,
    CHECK (monto_carne_cent + monto_alimento_cent = monto_cent)
);

-- ---------------------------------------------------------------------------
-- 3. CORTE DE LA MANO DE OBRA FIJA AL DÍA 73 (devengo y reversión)
-- ---------------------------------------------------------------------------
CREATE TABLE cortes_mod (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id               INTEGER NOT NULL UNIQUE REFERENCES ciclos_produccion(id),
    fecha                  TEXT    NOT NULL,
    dias_no_cubiertos      INTEGER NOT NULL CHECK (dias_no_cubiertos >= 0),
    dias_ciclo             INTEGER NOT NULL CHECK (dias_ciclo > 0),
    monto_cent             INTEGER NOT NULL CHECK (monto_cent >= 0),
    detalle_json           TEXT    NOT NULL,
    asiento_id             INTEGER REFERENCES asientos(id),
    reversion_asiento_id   INTEGER REFERENCES asientos(id),
    registrado_por         TEXT    NOT NULL,
    registrado_en          TEXT    NOT NULL,
    CHECK (monto_cent = 0 OR asiento_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- 4. KÁRDEX DE PRODUCTOS TERMINADOS (1.1.03.12 carne; 1.1.03.13 alimento)
-- ---------------------------------------------------------------------------
CREATE TABLE kardex_terminados (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    producto            TEXT    NOT NULL CHECK (producto IN ('CARNE','ALIMENTO','EXCEDENTE')),
    fecha               TEXT    NOT NULL,
    tipo                TEXT    NOT NULL CHECK (tipo IN ('ENTRADA_PRODUCCION','ENTRADA_ENSAQUILLADO',
                                                         'SALIDA_CONSUMO_PROPIO','SALIDA_ENSAQUILLADO','SALIDA_VENTA')),
    cantidad_mil        INTEGER NOT NULL CHECK (cantidad_mil > 0),
    valor_cent          INTEGER NOT NULL CHECK (valor_cent >= 0),
    saldo_cantidad_mil  INTEGER NOT NULL CHECK (saldo_cantidad_mil >= 0),
    saldo_valor_cent    INTEGER NOT NULL CHECK (saldo_valor_cent >= 0),
    cpp                 TEXT    NOT NULL,
    ciclo_id            INTEGER REFERENCES ciclos_produccion(id),
    documento_ref       TEXT,
    asiento_id          INTEGER REFERENCES asientos(id),
    registrado_por      TEXT    NOT NULL,
    registrado_en       TEXT    NOT NULL,
    CHECK (saldo_cantidad_mil > 0 OR saldo_valor_cent = 0)
);
CREATE INDEX idx_kardex_terminados ON kardex_terminados(producto, id);

-- ---------------------------------------------------------------------------
-- 5. PRODUCCIÓN DE ALIMENTO BALANCEADO (6.1.01.02 -> 1.1.03.13, desde 2027)
-- ---------------------------------------------------------------------------
CREATE TABLE producciones_alimento (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha             TEXT    NOT NULL,
    qq_mil            INTEGER NOT NULL CHECK (qq_mil > 0),
    costo_cent        INTEGER NOT NULL CHECK (costo_cent > 0),
    costo_qq          TEXT    NOT NULL,
    por_sufijo_json   TEXT    NOT NULL,
    asiento_id        INTEGER NOT NULL REFERENCES asientos(id),
    kardex_id         INTEGER NOT NULL REFERENCES kardex_terminados(id),
    registrado_por    TEXT    NOT NULL,
    registrado_en     TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- 6. CIERRE DEL CICLO E INFORME DE COSTO (FIN-TRA-06, FIN-TRA-07)
-- ---------------------------------------------------------------------------
CREATE TABLE costos_ciclo (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id              INTEGER NOT NULL UNIQUE REFERENCES ciclos_produccion(id),
    fecha_cierre          TEXT    NOT NULL,
    total_cent            INTEGER NOT NULL CHECK (total_cent > 0),
    mpd_cent              INTEGER NOT NULL,
    mod_cent              INTEGER NOT NULL,
    cif_cent              INTEGER NOT NULL,
    perdidas_cent         INTEGER NOT NULL,
    por_sufijo_json       TEXT    NOT NULL,
    kg_producidos_mil     INTEGER NOT NULL CHECK (kg_producidos_mil > 0),
    kg_vivos_mil          INTEGER NOT NULL CHECK (kg_vivos_mil > 0),
    costo_unitario        TEXT    NOT NULL,
    ciclo_anterior_id     INTEGER REFERENCES ciclos_produccion(id),
    costo_unitario_anterior TEXT,
    variacion_unitaria    TEXT,
    variacion_pct         TEXT,
    ica                   TEXT,
    conciliacion_json     TEXT    NOT NULL,
    asiento_id            INTEGER NOT NULL REFERENCES asientos(id),
    kardex_id             INTEGER NOT NULL REFERENCES kardex_terminados(id),
    aprobado_por          TEXT,
    aprobado_en           TEXT,
    observacion_gg        TEXT,
    registrado_por        TEXT    NOT NULL,
    registrado_en         TEXT    NOT NULL,
    CHECK (mpd_cent + mod_cent + cif_cent + perdidas_cent = total_cent)
);

-- ---------------------------------------------------------------------------
-- 7. PRESUPUESTO DE COSTOS POR SUFIJO (Manual Presupuestario D.4)
-- ---------------------------------------------------------------------------
CREATE TABLE presupuesto_costos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ejercicio      INTEGER NOT NULL REFERENCES ejercicios(ejercicio),
    ciclo          INTEGER NOT NULL CHECK (ciclo BETWEEN 1 AND 12),
    centro_costo   TEXT    NOT NULL REFERENCES centros_costo(codigo),
    sufijo         TEXT    NOT NULL REFERENCES sufijos_costo(sufijo),
    concepto       TEXT    NOT NULL,
    unidad         TEXT,
    cantidad_mil   INTEGER CHECK (cantidad_mil IS NULL OR cantidad_mil > 0),
    precio         TEXT,
    monto_cent     INTEGER NOT NULL CHECK (monto_cent >= 0),
    fuente         TEXT    NOT NULL,
    UNIQUE (ejercicio, ciclo, centro_costo, sufijo),
    CHECK ((cantidad_mil IS NULL) = (precio IS NULL))
);

-- ---------------------------------------------------------------------------
-- 8. INMUTABILIDAD
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_consumos_costo_no_update BEFORE UPDATE ON consumos_costo
BEGIN SELECT RAISE(ABORT, 'Los consumos no se modifican: reverse su asiento.'); END;
CREATE TRIGGER trg_consumos_costo_no_delete BEFORE DELETE ON consumos_costo
BEGIN SELECT RAISE(ABORT, 'Los consumos no se borran.'); END;
CREATE TRIGGER trg_cif_no_update BEFORE UPDATE ON cif_registrados
BEGIN SELECT RAISE(ABORT, 'Los CIF registrados no se modifican: reverse su asiento.'); END;
CREATE TRIGGER trg_cif_no_delete BEFORE DELETE ON cif_registrados
BEGIN SELECT RAISE(ABORT, 'Los CIF registrados no se borran.'); END;
CREATE TRIGGER trg_cortes_mod_no_update BEFORE UPDATE ON cortes_mod
WHEN OLD.reversion_asiento_id IS NOT NULL OR OLD.monto_cent <> NEW.monto_cent OR OLD.asiento_id IS NOT NEW.asiento_id
  OR OLD.fecha <> NEW.fecha
BEGIN SELECT RAISE(ABORT, 'El corte de MOD solo registra su reversión, una sola vez.'); END;
CREATE TRIGGER trg_cortes_mod_no_delete BEFORE DELETE ON cortes_mod
BEGIN SELECT RAISE(ABORT, 'El corte de MOD no se borra.'); END;
CREATE TRIGGER trg_kardex_term_no_update BEFORE UPDATE ON kardex_terminados
BEGIN SELECT RAISE(ABORT, 'El kárdex de productos terminados no se modifica.'); END;
CREATE TRIGGER trg_kardex_term_no_delete BEFORE DELETE ON kardex_terminados
BEGIN SELECT RAISE(ABORT, 'El kárdex de productos terminados no se borra.'); END;
CREATE TRIGGER trg_prod_alimento_no_update BEFORE UPDATE ON producciones_alimento
BEGIN SELECT RAISE(ABORT, 'La producción de alimento no se modifica.'); END;
CREATE TRIGGER trg_prod_alimento_no_delete BEFORE DELETE ON producciones_alimento
BEGIN SELECT RAISE(ABORT, 'La producción de alimento no se borra.'); END;
CREATE TRIGGER trg_costos_ciclo_no_delete BEFORE DELETE ON costos_ciclo
BEGIN SELECT RAISE(ABORT, 'El Informe de Costo de Ciclo no se borra.'); END;
CREATE TRIGGER trg_costos_ciclo_fijo BEFORE UPDATE ON costos_ciclo
WHEN OLD.total_cent <> NEW.total_cent OR OLD.costo_unitario <> NEW.costo_unitario OR OLD.aprobado_por IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'El Informe de Costo de Ciclo solo admite la aprobación de la Gerencia, una sola vez.'); END;
CREATE TRIGGER trg_presupuesto_costos_no_delete BEFORE DELETE ON presupuesto_costos
BEGIN SELECT RAISE(ABORT, 'El presupuesto de costos no se borra.'); END;
