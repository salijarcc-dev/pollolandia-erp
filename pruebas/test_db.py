"""Pruebas de las Fases 1-13: esquema, semillas, parámetros, apertura, FK y migraciones."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from decimal import Decimal

import pytest

import config
from db import semillas
from db.conexion import (
    a_centavos, buscar_periodo, conexion, de_centavos, obtener_conexion,
    obtener_parametro, obtener_parametros_vigentes, verificar_conexion,
)
from db.migraciones import Migracion, aplicar_migraciones, estado, inicializar, version_actual
from servicios.excepciones import DatosInvalidosError, ErrorPollolandia, ReglaNegocioError

TABLAS = {
    "cuentas", "sufijos_costo", "centros_costo", "sufijo_centro", "parametros", "ejercicios",
    "periodos", "socios", "usuarios", "roles", "permisos", "asientos", "asiento_detalle",
    "auditoria", "adjuntos_documentales", "esquema_version", "semillas_aplicadas",
    "conciliacion_catalogo", "autorizaciones", "autorizacion_firmas", "fondos_caja", "caja_movimientos",
    "reposiciones_caja", "arqueos_caja", "cuentas_bancarias", "movimientos_bancarios", "compromisos",
    "flujos_proyectados", "flujo_lineas", "alertas_liquidez", "medidas_correctivas", "reportes_saldo_diario",
    "conciliaciones", "conciliacion_partidas", "proveedores", "insumos", "kardex", "puntos_reorden",
    "actas_stock", "acta_stock_lineas", "alertas_stock", "compras_emergencia", "partidas_presupuesto",
    "reportes_disponibilidad", "ordenes_compra", "oc_lineas", "oc_cotizaciones", "recepciones", "recepcion_lineas",
    "cuentas_por_pagar", "programaciones_pago", "programacion_lineas", "bajas_inventario",
    "personal", "historial_salarios", "planillas", "planilla_lineas", "hojas_tiempo", "conciliaciones_mod",
    "verificaciones_horas", "verificacion_semanas", "circulares_cierre", "autorizaciones_eventuales",
    "contratos_eventuales", "liquidaciones_eventuales", "liquidacion_eventual_lineas", "liquidaciones_finales",
    "galpones", "ciclos_produccion", "lotes", "bitacora_diaria", "revisiones_bitacora", "alertas_sanitarias",
    "bajas_mortalidad", "disposiciones_sanitarias", "muestreos_peso", "faenados",
    "consumos_costo", "cif_registrados", "cortes_mod", "kardex_terminados", "producciones_alimento", "costos_ciclo",
    "presupuesto_costos",
    "capex_referencia", "planes_capex", "capex_lineas", "activaciones", "capex_variaciones", "activos",
    "depreciacion_meses", "depreciaciones", "bajas_activos", "informes_capex", "verificaciones_fisicas",
    "verificacion_detalle",
    "clientes", "suspensiones_credito", "tarifarios", "tarifario_lineas", "despachos", "pedidos",
    "verificaciones_cobertura", "resoluciones_credito", "notas_entrega", "cuentas_cobrar", "cobros", "devoluciones",
    "partes_recaudacion", "analisis_margen", "analisis_margen_lineas", "provisiones_incobrables",
    "listas_cierre", "cierres_mensuales", "cierres_anuales", "estados_emitidos", "presentaciones_eeff",
    "distribuciones_excedentes", "informes_cv_04", "liquidaciones_socios",
    "retencion_documental", "exp_rau_requisitos", "exp_rau_documentos", "certificados_rau", "superficies_declaradas",
    "superficies_usadas", "controles_superficie", "liquidaciones_rau", "liquidaciones_rau_socios", "formularios_701",
    "patentes_municipales", "cumplimientos_fiscales", "programas_excedente", "balances_masas",
    "compradores_autorizados", "servicios_terceros", "revisiones_rau", "notificaciones_fiscales",
    "acciones_fiscalizacion", "revisiones_buzon", "expedientes_archivo", "documentos_archivo", "prestamos_archivo",
    "presupuesto_maestro", "presupuesto_lineas", "hitos_presupuesto", "informes_ejecucion", "acciones_variacion",
    "modificaciones_presupuesto", "poa_actividades", "poa_avances", "informes_cv", "informes_gerenciales",
    "inventarios_sorpresivos", "inventario_sorpresivo_detalle", "verificaciones_desembolso",
    "respaldos", "actas_restauracion", "solicitudes_parametro", "poa_cargas", "poa_calendario", "poa_flujo_mensual",
    "poa_presupuesto_area",
    "actas_archivo", "acta_archivo_detalle",
}


@pytest.fixture(scope="module")
def ruta_bd(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("bd") / "prueba.db"
    inicializar(ruta)
    return ruta


@pytest.fixture()
def con(ruta_bd):
    c = obtener_conexion(ruta_bd)
    yield c
    c.rollback()
    c.close()


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


# --------------------------- creación desde cero -------------------------
def test_se_crea_desde_cero_con_un_comando(tmp_path):
    ruta = tmp_path / "cli.db"
    r = subprocess.run(
        [sys.executable, "-m", "db.migraciones", "--ruta", str(ruta)],
        cwd=config.BASE_DIR, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "Versión de esquema: 13" in r.stdout
    assert estado(ruta)["cuentas_nivel_4"] == 127


def test_tablas_y_version(con):
    tablas = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLAS <= tablas
    assert version_actual(con) == 13


def test_idempotente_no_duplica(tmp_path):
    ruta = tmp_path / "x.db"
    inicializar(ruta)
    antes = estado(ruta)
    r2 = inicializar(ruta)
    assert r2["migraciones_aplicadas"] == [] and r2["semillas_aplicadas"] == {}
    assert estado(ruta) == antes


def test_reiniciar_recrea(tmp_path):
    ruta = tmp_path / "r.db"
    inicializar(ruta)
    with conexion(ruta) as c:
        c.execute("UPDATE parametros SET valor='9' WHERE clave='dias_ciclo'")
    inicializar(ruta, reiniciar=True)
    with conexion(ruta) as c:
        assert obtener_parametro(c, "dias_ciclo", 2026) == 73


def test_sin_semillas(tmp_path):
    ruta = tmp_path / "s.db"
    inicializar(ruta, con_semillas=False)
    with conexion(ruta) as c:
        assert uno(c, "SELECT COUNT(*) FROM cuentas") == 0


def test_migracion_fallida_hace_rollback(tmp_path):
    c = obtener_conexion(tmp_path / "m.db")
    malas = [Migracion(1, "buena", "CREATE TABLE t1 (id INTEGER);"),
             Migracion(2, "mala", "CREATE TABLE t2 (id INTEGER); CREATE TABLE t2 (id INTEGER);")]
    with pytest.raises(ReglaNegocioError) as e:
        aplicar_migraciones(c, malas)
    assert e.value.codigo == "MIGRACION_FALLIDA"
    assert version_actual(c) == 1
    tablas = {f[0] for f in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "t1" in tablas and "t2" not in tablas
    c.close()


def test_bd_con_version_futura(tmp_path):
    c = obtener_conexion(tmp_path / "f.db")
    aplicar_migraciones(c, [Migracion(1, "a", "CREATE TABLE a (id INTEGER);"),
                            Migracion(2, "b", "CREATE TABLE b (id INTEGER);")])
    with pytest.raises(ReglaNegocioError):
        aplicar_migraciones(c, [Migracion(1, "a", "SELECT 1;")])
    c.close()


def test_pragmas(ruta_bd):
    est = verificar_conexion(ruta_bd)
    assert est["ok"] and est["foreign_keys"] and est["journal_mode"].lower() == "wal"


# ------------------------------- cuentas ---------------------------------
def test_cuentas_igual_al_plan_v3(con):
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel=4 AND origen='PLAN_V3'") == 126
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel=4 AND origen='DESCRIPCION'") == 1
    assert uno(con, "SELECT codigo FROM cuentas WHERE origen='DESCRIPCION'") == "1.1.01.05"
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel=1") == 9
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel=2") == 17
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel=3") == 37
    por_grupo = dict(con.execute(
        "SELECT grupo, COUNT(*) FROM cuentas WHERE nivel=4 GROUP BY grupo").fetchall())
    assert por_grupo == {1: 38, 2: 16, 3: 16, 4: 15, 5: 8, 6: 4, 7: 22, 8: 4, 9: 4}


def test_jerarquia_coherente(con):
    huerfanas = con.execute(
        """SELECT c.codigo FROM cuentas c JOIN cuentas p ON p.codigo = c.padre_codigo
            WHERE p.nivel <> c.nivel - 1 OR substr(c.codigo, 1, length(p.codigo)) <> p.codigo""").fetchall()
    assert huerfanas == []
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel=4 AND es_movimiento=0") == 0
    assert uno(con, "SELECT COUNT(*) FROM cuentas WHERE nivel<4 AND es_movimiento=1") == 0


@pytest.mark.parametrize("codigo, nombre, naturaleza, clasif", [
    ("1.1.01.03", "Banco – Cuenta Corriente Moneda Nacional", "DEUDORA", "CORRIENTE"),
    ("1.2.01.02", "Galpones de Producción", "DEUDORA", "NO_CORRIENTE"),
    ("2.2.01.01", "Provisión para Indemnización", "ACREEDORA", "NO_CORRIENTE"),
    ("3.1.01.01", "Certificados de Aportación – Socios Fundadores", "ACREEDORA", "NA"),
    ("3.1.03.03", "Déficit del Ejercicio", "DEUDORA", "NA"),
    ("4.1.02.01", "Deprec. Acumulada – Galpones de Producción", "ACREEDORA", "NO_CORRIENTE"),
    ("4.3.01.01", "Certificados de Aportación No Pagados", "DEUDORA", "NA"),
    ("5.1.04.01", "Devoluciones en Ventas", "DEUDORA", "OPERATIVO"),
    ("5.2.01.04", "Ventas – Gallinaza / Abono Orgánico", "ACREEDORA", "NO_OPERATIVO"),
    ("6.1.01.01", "Centro de costo – Producción de Carne de Pollo", "DEUDORA", "OPERATIVO"),
    ("7.2.01.04", "Pérdidas por Mortandad Avícola anormal", "DEUDORA", "NO_OPERATIVO"),
    ("8.1.01.01", "Resumen de Déficit y Excedentes del Ejercicio", "DUAL", "NA"),
    ("9.1.01.01", "Bienes Recibidos en Consignación", "DEUDORA", "NA"),
    ("9.2.01.02", "Acreedores por Garantías de Socios", "ACREEDORA", "NA"),
])
def test_cuentas_muestra(con, codigo, nombre, naturaleza, clasif):
    f = con.execute("SELECT * FROM cuentas WHERE codigo=?", (codigo,)).fetchone()
    assert f["nombre"] == nombre and f["naturaleza"] == naturaleza and f["clasificacion"] == clasif


def test_uso_desde_2027(con):
    cods = {f[0] for f in con.execute("SELECT codigo FROM cuentas WHERE uso_desde_2027=1 AND nivel=4")}
    assert cods == {"1.1.03.03", "1.1.03.04", "1.1.03.05", "1.1.03.06", "1.1.03.07", "1.1.03.13",
                    "1.2.01.04", "1.2.01.05", "2.1.01.03", "4.1.02.03", "4.1.02.04",
                    "5.1.02.01", "6.1.01.02"}


# ----------------------------- sufijos y centros -------------------------
def test_sufijos(con):
    codigos = [f[0] for f in con.execute("SELECT sufijo FROM sufijos_costo ORDER BY sufijo")]
    assert codigos == [f"{n:02d}" for n in list(range(1, 14)) + [20, 21, 22] + list(range(30, 42))]
    elementos = dict(con.execute("SELECT elemento, COUNT(*) FROM sufijos_costo GROUP BY elemento").fetchall())
    assert elementos == {"MPD": 13, "MOD": 5, "CIF": 8, "PERDIDA": 2}
    sin_cuenta = [f[0] for f in con.execute(
        "SELECT sufijo FROM sufijos_costo WHERE cuenta_contrapartida IS NULL ORDER BY 1")]
    assert sin_cuenta == ["40", "41"]


def test_contrapartidas_y_centros(con):
    cp = dict(con.execute("SELECT sufijo, cuenta_contrapartida FROM sufijos_costo").fetchall())
    assert cp["01"] == "1.1.03.01" and cp["11"] == "1.1.03.13" and cp["12"] == "1.1.03.11"
    assert cp["20"] == "2.1.02.01" and cp["39"] == "2.2.01.01" and cp["34"] == "4.1.02.04"
    assert uno(con, "SELECT cuenta_contrapartida_2 FROM sufijos_costo WHERE sufijo='20'") == "2.1.02.05"
    centros = {f[0]: (f[1], f[2]) for f in con.execute(
        "SELECT codigo, cuenta_producto_term, cuenta_costo_ventas FROM centros_costo")}
    assert centros == {"6.1.01.01": ("1.1.03.12", "6.1.02.01"), "6.1.01.02": ("1.1.03.13", "6.1.02.02")}
    ambos = {f[0] for f in con.execute(
        "SELECT sufijo FROM sufijo_centro GROUP BY sufijo HAVING COUNT(*) = 2")}
    assert ambos == {"20", "21", "36", "37", "38", "39"}


# ------------------------------- parámetros ------------------------------
@pytest.mark.parametrize("ej, sme", [(2026, 136413), (2027, 145912), (2028, 194693),
                                      (2029, 237922), (2030, 288775)])
def test_sme_por_ejercicio(con, ej, sme):
    assert obtener_parametro(con, "sme", ej) == Decimal(sme)


def test_valores_normativos(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert p["sme_piso_absoluto"] == Decimal("15000")
    assert p["sme_umbral_compromiso"] == Decimal("1.20")
    assert p["aporte_patronal"] == Decimal("0.1721") and p["aporte_laboral"] == Decimal("0.1271")
    assert p["aguinaldo_pct"] == Decimal("0.0833") and p["indemnizacion_pct"] == Decimal("0.0833")
    assert p["tope_caja_bs"] == 5000 and p["gasto_menor_max_bs"] == 500
    assert (p["autoriza_desembolso_gg_sobre_bs"], p["firma_mancomunada_sobre_bs"],
            p["capex_no_previsto_ca_sobre_bs"], p["capex_variacion_monto_bs"]) == (1000, 5000, 20000, 10000)
    assert p["capex_variacion_pct"] == Decimal("0.15")
    assert (p["mortalidad_alerta"], p["mortalidad_anormal"]) == (Decimal("0.03"), Decimal("0.05"))
    assert p["rendimiento_kg_ave"] == Decimal("2.6")
    assert (p["dias_ciclo"], p["dias_ciclo_minimo"]) == (73, 70)
    assert p["ciclo_cierre_inclusivo"] is True
    assert p["tarifa_eventual_por_ave"] == Decimal("1.50")
    assert (p["rau_hectareas"], p["rau_cuota_por_ha"]) == (8, Decimal("3.03"))
    assert (p["precio_carne_kg"], p["precio_gallinaza_carga"]) == (Decimal("23.50"), Decimal("500"))
    assert p["metodo_valuacion_inventarios"] == "CPP"
    assert p["deprec_tasa_galpones"] == Decimal("0.05") and p["deprec_tasa_maquinaria_alimento"] == Decimal("0.125")
    assert "precio_alimento_qq" not in p  # vigente desde 2027
    assert obtener_parametro(con, "precio_alimento_qq", 2027) == Decimal("180")


def test_credito_horeca_por_ejercicio(con):
    assert obtener_parametro(con, "horeca_credito_dias_max", 2026) == 1
    assert obtener_parametro(con, "horeca_credito_dias_max", 2027) == 1
    assert obtener_parametro(con, "horeca_credito_dias_max", 2028) == 7
    assert obtener_parametro(con, "horeca_credito_dias_max", 2035) == 7
    with pytest.raises(ReglaNegocioError):
        obtener_parametro(con, "horeca_historial_meses_min", 2027)


def test_aves_por_ciclo(con):
    assert [obtener_parametro(con, "aves_por_ciclo", e) for e in range(2026, 2031)] == [3000, 3000, 4000, 5000, 6000]


def test_parametro_inexistente(con):
    with pytest.raises(ReglaNegocioError) as e:
        obtener_parametro(con, "no_existe", 2026)
    assert e.value.codigo == "PARAMETRO_NO_ENCONTRADO"


def test_parametros_sin_solape_y_con_fuente(con):
    with pytest.raises(sqlite3.IntegrityError, match="Vigencia solapada"):
        con.execute("INSERT INTO parametros (clave, valor, tipo, ejercicio_desde, fuente_documental)"
                    " VALUES ('dias_ciclo','80','ENTERO',2027,'prueba')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO parametros (clave, valor, tipo, ejercicio_desde, fuente_documental)"
                    " VALUES ('nuevo','1','ENTERO',2026,'  ')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO parametros (clave, valor, tipo, ejercicio_desde, ejercicio_hasta, fuente_documental)"
                    " VALUES ('nuevo','1','ENTERO',2028,2027,'x')")
    assert uno(con, "SELECT COUNT(*) FROM parametros WHERE fuente_documental IS NULL OR trim(fuente_documental)=''") == 0


def test_todos_los_parametros_validan_su_tipo(con):
    from db.conexion import convertir_valor_parametro
    for f in con.execute("SELECT clave, valor, tipo FROM parametros"):
        convertir_valor_parametro(f["valor"], f["tipo"], f["clave"])
    with pytest.raises(DatosInvalidosError):
        convertir_valor_parametro("abc", "ENTERO", "x")
    with pytest.raises(DatosInvalidosError):
        convertir_valor_parametro("quizás", "BOOLEANO", "x")


# ---------------------------- calendario y socios ------------------------
def test_periodos(con):
    assert uno(con, "SELECT COUNT(*) FROM ejercicios") == 5 and uno(con, "SELECT COUNT(*) FROM periodos") == 60
    p1 = con.execute("SELECT * FROM periodos WHERE ejercicio=2026 AND numero=1").fetchone()
    p12 = con.execute("SELECT * FROM periodos WHERE ejercicio=2026 AND numero=12").fetchone()
    assert (p1["fecha_inicio"], p1["fecha_fin"]) == ("2026-07-01", "2026-07-31")
    assert (p12["fecha_inicio"], p12["fecha_fin"]) == ("2027-06-01", "2027-06-30")
    assert buscar_periodo(con, "2027-02-15")["numero"] == 8
    assert buscar_periodo(con, "2028-02-29")["ejercicio"] == 2027  # bisiesto
    with pytest.raises(ReglaNegocioError):
        buscar_periodo(con, "2040-01-01")


def test_socios_y_capital(con):
    assert uno(con, "SELECT COUNT(*) FROM socios") == 12
    assert uno(con, "SELECT SUM(certificados) FROM socios") == 5343
    assert uno(con, "SELECT SUM(aporte_cent) FROM socios") == 53_430_000
    assert uno(con, "SELECT COUNT(DISTINCT ci) FROM socios") == 12
    assert uno(con, "SELECT aporte_cent FROM socios WHERE numero=1") == 4_460_000
    assert uno(con, "SELECT aporte_cent FROM socios WHERE numero=3") == 4_450_000
    assert uno(con, "SELECT nombre_completo FROM socios WHERE cargo_cooperativo LIKE 'Gerente%'") == "Rocío Del Alba Flores Colque"
    assert uno(con, "SELECT COUNT(*) FROM socios WHERE cargo_cooperativo LIKE '%Vigilancia%'") == 5
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO socios (numero, nombre_completo, ci, certificados, aporte_cent, fecha_ingreso)"
                    " VALUES (13,'X','9105875',1,10000,'2026-05-01')")


# ------------------------------ apertura ---------------------------------
def test_apertura_cuadra(con):
    a = con.execute("SELECT * FROM asientos WHERE tipo='APERTURA'").fetchone()
    assert a["numero"] == 1 and a["fecha"] == "2026-07-01" and a["estado"] == "VIGENTE"
    debe, haber = con.execute("SELECT SUM(debe_cent), SUM(haber_cent) FROM asiento_detalle").fetchone()
    assert debe == haber == 53_430_000
    assert con.execute("SELECT * FROM v_asientos_descuadrados").fetchall() == []


def test_balance_de_apertura(con):
    s = {f["codigo"]: f["saldo_deudor_cent"] for f in con.execute("SELECT * FROM v_saldos_cuentas")}
    assert s["1.1.01.03"] == 13_801_000
    assert s["1.2.01.01"] == 21_000_000 and s["1.2.01.02"] == 10_500_000
    assert s["3.1.01.01"] == -53_430_000
    activo = sum(v for k, v in s.items() if k.startswith("1."))
    assert activo == 53_430_000 == -s["3.1.01.01"]
    assert sum(s.values()) == 0


# --------------------------- integridad referencial ----------------------
def test_fk_detalle_cuenta_inexistente(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent) VALUES (1, 99, '9.9.99.99', 100)")
        con.execute("PRAGMA foreign_key_check")
        con.commit()


def test_fk_usuario_rol_inexistente(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO usuarios (nombre_usuario, nombre_completo, rol, password_hash,"
                    " ultimo_cambio_password, creado_en) VALUES ('u','U','XX','h','2026-01-01','2026-01-01')")


def test_fk_permiso_rol_inexistente(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES ('XX','tesoreria','VER','x')")


def test_fk_asiento_periodo_inexistente(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO asientos (tipo, numero, fecha, periodo_id, glosa, origen, creado_por, creado_en)"
                    " VALUES ('INGRESO',1,'2026-08-01',9999,'x','t','u','2026-08-01')")


def test_fk_sufijo_centro_inexistente(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO sufijo_centro (sufijo, centro_costo) VALUES ('99','6.1.01.01')")


def test_no_se_borra_cuenta_con_movimientos(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM cuentas WHERE codigo='1.1.01.03'")


# ---------------------------- reglas del diario --------------------------
def _nuevo_asiento(con, numero=1):
    pid = uno(con, "SELECT id FROM periodos WHERE ejercicio=2026 AND numero=2")
    return con.execute(
        "INSERT INTO asientos (tipo, numero, fecha, periodo_id, glosa, origen, creado_por, creado_en)"
        " VALUES ('INGRESO',?,'2026-08-05',?,'prueba','test','t','2026-08-05')", (numero, pid)).lastrowid


def test_linea_solo_cuentas_de_movimiento(con):
    a = _nuevo_asiento(con)
    with pytest.raises(sqlite3.IntegrityError, match="no admite movimientos"):
        con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent) VALUES (?,1,'1.1.01',100)", (a,))


def test_linea_debe_y_haber_exclusivos(con):
    a = _nuevo_asiento(con)
    for debe, haber in [(100, 100), (0, 0)]:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent, haber_cent)"
                        " VALUES (?,1,'1.1.01.01',?,?)", (a, debe, haber))


def test_sufijo_debe_pertenecer_al_centro(con):
    a = _nuevo_asiento(con)
    with pytest.raises(sqlite3.IntegrityError, match="no es aplicable"):
        con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent, centro_costo, sufijo)"
                    " VALUES (?,1,'6.1.01.01',100,'6.1.01.01','03')", (a,))
    con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent, centro_costo, sufijo)"
                " VALUES (?,1,'6.1.01.01',100,'6.1.01.01','01')", (a,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent, sufijo)"
                    " VALUES (?,2,'6.1.01.01',100,'01')", (a,))


def test_asiento_descuadrado_se_detecta(con):
    a = _nuevo_asiento(con)
    con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent) VALUES (?,1,'1.1.01.01',500)", (a,))
    con.execute("INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, haber_cent) VALUES (?,2,'5.1.01.01',400)", (a,))
    assert [f["asiento_id"] for f in con.execute("SELECT * FROM v_asientos_descuadrados")] == [a]


def test_inmutabilidad_del_diario(con):
    with pytest.raises(sqlite3.IntegrityError, match="no se borran"):
        con.execute("DELETE FROM asientos WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError, match="no se borran"):
        con.execute("DELETE FROM asiento_detalle WHERE asiento_id=1")
    with pytest.raises(sqlite3.IntegrityError, match="no se modifican"):
        con.execute("UPDATE asiento_detalle SET debe_cent=1 WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError, match="solo puede cambiar"):
        con.execute("UPDATE asientos SET glosa='otra' WHERE id=1")
    con.execute("UPDATE asientos SET estado='REVERSADO' WHERE id=1")  # único cambio permitido
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE asientos SET estado='VIGENTE' WHERE id=1")


def test_auditoria_inmutable_y_poblada(con):
    # 15 bloques de semillas + asiento de apertura
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE accion='CARGA_INICIAL'") == 37
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE usuario='SISTEMA'") == 37
    with pytest.raises(sqlite3.IntegrityError, match="inmutable"):
        con.execute("UPDATE auditoria SET usuario='x'")
    with pytest.raises(sqlite3.IntegrityError, match="inmutable"):
        con.execute("DELETE FROM auditoria")


# ------------------------------ roles y permisos --------------------------
def test_roles(con):
    assert [f[0] for f in con.execute("SELECT codigo FROM roles ORDER BY codigo")] == ["AG", "CA", "CV", "GG", "JF", "UT"]


def _acciones(con, rol, recurso=None):
    sql = "SELECT recurso, accion FROM permisos WHERE rol=?"
    args = [rol]
    if recurso:
        sql += " AND recurso=?"
        args.append(recurso)
    return {tuple(f) for f in con.execute(sql, args)}


def test_segregacion_de_funciones(con):
    assert not any(a == "AUTORIZAR" for _, a in _acciones(con, "JF"))          # JF no autoriza
    assert ("tesoreria", "COFIRMAR") not in _acciones(con, "JF")               # ni firma
    assert not any(a in ("CREAR", "EDITAR", "REVERSAR") for r, a in _acciones(con, "GG") if r == "contabilidad")
    assert not any(a in ("CREAR", "EDITAR", "REVERSAR", "AUTORIZAR", "APROBAR")
                   for r, a in _acciones(con, "CV") if r != "vigilancia")      # CV solo lectura/verifica
    assert ("tesoreria", "COFIRMAR") in _acciones(con, "CV")
    assert ("compras", "CREAR") in _acciones(con, "GG") and ("tesoreria", "AUTORIZAR") in _acciones(con, "GG")
    assert ("produccion", "CREAR") in _acciones(con, "UT")
    assert not any(a == "AUTORIZAR" for _, a in _acciones(con, "UT"))
    assert ("presupuesto", "APROBAR") in _acciones(con, "CA") and ("contabilidad", "APROBAR") in _acciones(con, "AG")
    assert ("contabilidad", "REVERSAR") in _acciones(con, "JF")


# --------------------------- conciliación y reporte -----------------------
def test_conciliacion_y_reporte(con):
    assert uno(con, "SELECT COUNT(*) FROM conciliacion_catalogo") == 25
    assert "1.1.03.15" in uno(con, "SELECT detalle FROM conciliacion_catalogo WHERE orden=24")
    assert "Plan_de_Cuentas_Pollolandia.xlsx" in uno(con, "SELECT fuente FROM conciliacion_catalogo WHERE orden=1")
    reporte = semillas.generar_reporte_conciliacion(con)
    for texto in ("5.1.03.01", "5.1.04.01", "Previsión", "Provisión", "28", "Bs 534.300", "138.010"):
        assert texto in reporte
    assert "A. Catálogo" in reporte and "B. Balance de apertura" in reporte and "C. Normas" in reporte


def test_escribir_reporte(con, tmp_path):
    ruta = semillas.escribir_reporte_conciliacion(con, tmp_path / "sub" / "r.md")
    assert ruta.exists() and ruta.read_text(encoding="utf-8").startswith("# Reporte de conciliación")


# ------------------------------- centavos --------------------------------
@pytest.mark.parametrize("valor, cent", [("1234.56", 123456), (24.24, 2424), (Decimal("0.005"), 1),
                                          (534300, 53_430_000), ("-50", -5000)])
def test_a_centavos(valor, cent):
    assert a_centavos(valor) == cent
    assert de_centavos(cent) == Decimal(cent) / 100


def test_de_centavos_invalido():
    for malo in (1.5, "10", True, None):
        with pytest.raises(DatosInvalidosError):
            de_centavos(malo)


def test_conexion_error_amigable(tmp_path):
    with pytest.raises(ErrorPollolandia) as e:
        obtener_conexion(tmp_path / "no" / "existe" / "x.db")
    assert e.value.codigo == "BD_CONEXION"


# ------------------------------- Fase 2 -----------------------------------
def test_parametros_fase2(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert p["password_caducidad_dias"] == 90
    assert (p["login_intentos_max"], p["login_bloqueo_minutos"], p["sesion_inactividad_minutos"]) == (3, 15, 30)
    assert p["suplencia_cargo_orden_1"] == "Presidente del Consejo de Administración"
    assert p["suplencia_cargo_orden_2"] == "Tesorero del Consejo de Administración"
    assert p["suplencia_comunicacion_horas"] == 24 and p["ejercicio_inicio_alimento_propio"] == 2027
    assert uno(con, "SELECT COUNT(*) FROM parametros") == 234


def test_actualiza_bd_de_fase1_a_fase2(tmp_path, monkeypatch):
    """Una base creada con el código de la Fase 1 (esquema v1 y 8 bloques) se
    actualiza sin perder datos."""
    ruta = tmp_path / "f1.db"
    c = obtener_conexion(ruta)
    from db.migraciones import obtener_migraciones
    aplicar_migraciones(c, obtener_migraciones()[:1])
    with monkeypatch.context() as m:
        m.setattr(semillas, "_BLOQUES", semillas._BLOQUES[:8])
        semillas.cargar_semillas(c)
    c.close()
    r = inicializar(ruta)
    assert r["migraciones_aplicadas"] == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    assert set(r["semillas_aplicadas"]) == {"parametros_fase2", "conciliacion_fase2", "parametros_fase3",
                                            "conciliacion_fase3", "parametros_fase4", "insumos_fase4",
                                            "partidas_compras_fase4", "parametros_fase5",
                                            "partidas_eventual_fase5", "parametros_fase6",
                                            "parametros_fase7", "presupuesto_costos_fase7",
                                            "parametros_fase8", "capex_d5_fase8", "permisos_fase8",
                                            "parametros_fase9", "parametros_fase10",
                                            "parametros_fase11", "retencion_fase11", "exp_rau_fase11",
                                            "permisos_fase11", "parametros_fase12",
                                            "presupuesto_maestro_fase12", "poa_fase12", "permisos_fase12",
                                            "parametros_fase13", "permisos_fase13", "parametros_identidad"}
    with conexion(ruta) as c2:
        assert uno(c2, "SELECT COUNT(*) FROM conciliacion_catalogo") == 25


def test_parametros_fase3_y_confirmacion_o1(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert p["jerarquia_pagos"].split(",")[0] == "PLANILLA_APORTES"
    assert p["jerarquia_pagos"].split(",")[-1] == "EXCEDENTES_SOCIOS"
    assert (p["cuenta_cargos_bancarios"], p["cuenta_intereses_bancarios"]) == ("7.2.01.03", "5.2.01.01")
    assert (p["partida_transito_alerta_dias"], p["conciliacion_diferencia_reporte_horas"]) == (30, 48)
    assert "Confirmado por el usuario" in uno(con, "SELECT decision FROM conciliacion_catalogo WHERE orden=24")


# ------------------------------- Fase 4 -----------------------------------
def test_semillas_fase4_insumos_partidas_y_parametros(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert p["cuenta_perdida_baja_inventario"] == "7.2.01.03"
    assert (p["stock_seguridad_dias_alimento_bb"], p["stock_seguridad_dias_otros"], p["rotacion_granos_dias_max"]) == \
        (7, 5, 30)
    assert (p["dia_semana_orden_compra"], p["dia_semana_control_stock"], p["cotizaciones_minimas"]) == (1, 5, 3)
    assert uno(con, "SELECT COUNT(*) FROM insumos") == 16
    assert uno(con, "SELECT COUNT(DISTINCT cuenta_inventario) FROM insumos") == 15
    assert uno(con, "SELECT COUNT(*) FROM insumos WHERE grupo_critico IS NOT NULL") == 10
    assert uno(con, "SELECT COUNT(*) FROM partidas_presupuesto WHERE cuenta_inventario LIKE '1.1.03.%'") == 85
    # Manual Presupuestario D.4-1: materia prima del ciclo 1 de 2026 = Bs 96.885 (+ mantenimiento Bs 1.500)
    assert uno(con, "SELECT SUM(monto_cent) FROM partidas_presupuesto WHERE ejercicio=2026 AND ciclo=1 "
                    "AND cuenta_inventario LIKE '1.1.03.%' AND cuenta_inventario <> '1.1.03.15'") == 9688500
    assert uno(con, "SELECT SUM(monto_cent) FROM partidas_presupuesto WHERE ejercicio=2026 "
                    "AND cuenta_inventario LIKE '1.1.03.%'") == 47962500 + 750000
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM partidas_presupuesto")


# ------------------------------- Fase 5 -----------------------------------
def test_semillas_fase5_parametros_y_partida_eventual(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["aporte_laboral"], p["aporte_patronal"]) == (Decimal("0.1271"), Decimal("0.1721"))
    assert (p["aguinaldo_pct"], p["indemnizacion_pct"]) == (Decimal("0.0833"), Decimal("0.0833"))
    assert p["provision_base_doceavos"] in (1, True) and p["dias_mes_comercial"] == 30
    assert p["liquidacion_final_dias"] == 15 and p["liquidacion_final_antiguedad_min_dias"] == 90
    assert p["tarifa_eventual_por_ave"] == Decimal("1.50") and p["circular_cierre_dias_antelacion"] == 7
    assert p["ci_fin_14_muestra_min"] == Decimal("0.20")
    # POA B2: 3.000 aves x Bs 1,50 = Bs 4.500 por ciclo, 5 ciclos por ejercicio
    assert uno(con, "SELECT COUNT(*) FROM partidas_presupuesto WHERE cuenta_inventario = '2.1.02.04'") == 10
    assert uno(con, "SELECT SUM(monto_cent) FROM partidas_presupuesto WHERE cuenta_inventario = '2.1.02.04' "
                    "AND ejercicio = 2026") == 2250000
    assert uno(con, "SELECT COUNT(*) FROM personal") == 0
    disparadores = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert any("planillas" in d and "delete" in d for d in disparadores)


# ------------------------------- Fase 6 -----------------------------------
def test_semillas_fase6_parametros_de_produccion(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["dias_ciclo"], p["dias_ciclo_minimo"], p["lote_aves"], p["densidad_max_aves_m2"]) == (73, 70, 1000, 8)
    assert (p["fase_inicio_dia_fin"], p["fase_crecimiento_dia_fin"], p["muestreo_peso_dias"]) == (21, 50, 7)
    assert (p["mortalidad_alerta"], p["mortalidad_anormal"]) == (Decimal("0.03"), Decimal("0.05"))
    assert (p["pmf_umbral_bueno"], p["pmf_umbral_malo"]) == (Decimal("0.10"), Decimal("0.20"))
    assert (p["ica_umbral_bueno"], p["ica_umbral_malo"]) == (Decimal("2.00"), Decimal("2.10"))
    assert p["qr_url_base"].startswith("https://")
    assert uno(con, "SELECT COUNT(*) FROM ciclos_produccion") == 0
    disparadores = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"trg_bitacora_no_update", "trg_faenados_no_delete", "trg_bajas_mort_fija"} <= disparadores


# ------------------------------- Fase 7 -----------------------------------
def test_semillas_fase7_presupuesto_d4_y_parametros(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["kg_por_quintal"], p["saquillos_por_qq"]) == (46, 1)
    # Manual Presupuestario D.4-1: ciclo 1 de 2026 = Bs 116.286; ciclos 2 a 5 = Bs 115.086 (bolsas Bs 3.600)
    assert uno(con, "SELECT SUM(monto_cent) FROM presupuesto_costos WHERE ejercicio = 2026 AND ciclo = 1") == 11628600
    assert uno(con, "SELECT SUM(monto_cent) FROM presupuesto_costos WHERE ejercicio = 2026 AND ciclo = 2") == 11508600
    # D.4-1 2027 carne Bs 97.578 por ciclo; D.4-2 alimento Bs 57.568 (redondeo de D.4)
    assert uno(con, "SELECT SUM(monto_cent) FROM presupuesto_costos WHERE ejercicio = 2027 AND ciclo = 3 "
                    "AND centro_costo = '6.1.01.01'") == 9757800
    assert uno(con, "SELECT SUM(monto_cent) FROM presupuesto_costos WHERE ejercicio = 2027 AND ciclo = 3 "
                    "AND centro_costo = '6.1.01.02'") == 5756800
    assert uno(con, "SELECT cantidad_mil FROM presupuesto_costos WHERE ejercicio = 2026 AND ciclo = 1 "
                    "AND sufijo = '02'") == 300000
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM presupuesto_costos")


# ------------------------------- Fase 8 -----------------------------------
def test_semillas_fase8_capex_d5_y_parametros(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["capex_plan_mes_limite"], p["capex_plan_dia_limite"], p["capex_ciclo_ejecucion"]) == (5, 31, 5)
    assert (p["capex_informe_mes_semestre_1"], p["capex_informe_mes_semestre_2"]) == (1, 7)
    assert (p["verificacion_fisica_meses"], p["deprec_tasa_terrenos"]) == (12, Decimal("0"))
    assert (p["deprec_tasa_software"], p["amort_tasa_software_apertura"]) == (Decimal("0.25"), Decimal("0.20"))
    assert (p["capex_no_previsto_ca_sobre_bs"], p["capex_variacion_pct"], p["capex_variacion_monto_bs"],
            p["baja_activo_cv_sobre_bs"]) == (Decimal("20000"), Decimal("0.15"), Decimal("10000"), Decimal("5000"))
    # Manual Presupuestario D.5: 97.000 / 51.390 / 48.150 / 148.390
    totales = dict(con.execute("SELECT ejercicio, SUM(monto_cent) FROM capex_referencia GROUP BY ejercicio"))
    assert totales == {2026: 9700000, 2027: 5139000, 2028: 4815000, 2029: 14839000}
    # Las cuentas reguladoras de PC-06 coinciden con las contrapartidas de los sufijos 30-35.
    from servicios.activos import CATEGORIAS
    contrapartidas = dict(con.execute("SELECT sufijo, cuenta_contrapartida FROM sufijos_costo"))
    for cat in CATEGORIAS.values():
        if cat.sufijo:
            assert contrapartidas[cat.sufijo] == cat.cuenta_depreciacion
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM capex_referencia")
    assert _acciones(con, "AG", "activos") == {("activos", "VER"), ("activos", "APROBAR")}


# ------------------------------- Fase 9 -----------------------------------
def test_semillas_fase9_parametros_de_ventas(con):
    p26, p28 = obtener_parametros_vigentes(con, 2026), obtener_parametros_vigentes(con, 2028)
    assert (p26["horeca_credito_dias_max"], p28["horeca_credito_dias_max"]) == (1, 7)
    assert (p26["preventa_dias_min"], p26["preventa_friales_min"], p26["margen_bruto_canal_min"]) == \
        (3, 2, Decimal("0.20"))
    assert (p26["margen_meses_consecutivos"], p26["cobertura_lote_min"], p26["cobertura_verificacion_dias_antes"]) == \
        (2, Decimal("1.00"), 1)
    assert (p26["cobranza_reporte_horas"], p26["credito_incumplimientos_suspension"]) == (24, 2)
    assert p26["cheques_diferidos_permitidos"] is False
    assert "provision_incobrables_pct" not in p26 and "provision_incobrables_pct_mora" not in p26
    assert (p28["provision_incobrables_pct"], p28["provision_incobrables_pct_mora"], p28["provision_dudoso_dias_mora"]) \
        == (Decimal("0.05"), Decimal("1.00"), 30)
    assert uno(con, "SELECT COUNT(*) FROM clientes") == 0
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO notas_entrega (tipo) VALUES ('X')")


# ------------------------------- Fase 10 ----------------------------------
def test_semillas_fase10_parametros_de_cierre(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["reserva_legal_min"], p["fondo_educacion_min"]) == (Decimal("0.05"), Decimal("0.01"))
    assert (p["eeff_consejos_dias"], p["eeff_asamblea_dias"], p["eeff_alerta_dias"]) == (60, 90, 15)
    assert p["excedentes_criterio_distribucion"] == "IGUALITARIO"
    assert (p["excedentes_pago_meses"], p["excedentes_rc_iva_tasa"]) == (3, Decimal("0"))
    assert (p["icl_umbral_bueno"], p["icl_umbral_malo"], p["roa_umbral_bueno"]) == \
        (Decimal("0.61"), Decimal("0.63"), Decimal("0.18"))
    assert uno(con, "SELECT COUNT(*) FROM cierres_anuales") == 0
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO distribuciones_excedentes (ejercicio) VALUES (2026)")


# ------------------------------- Fase 11 ----------------------------------
def test_semillas_fase11_rau_y_archivo(con):
    p26, p27 = obtener_parametros_vigentes(con, 2026), obtener_parametros_vigentes(con, 2027)
    assert (p26["rau_alerta_dias"], p26["rau_alerta_temprana_dias"]) == (30, 60)
    assert (p26["rau_anticipacion_dias_habiles"], p26["rau_liquidacion_socio_dias_habiles"]) == (15, 20)
    assert "rau_i1_autoconsumo_verde_min" not in p26 and p27["rau_i1_autoconsumo_verde_min"] == Decimal("0.65")
    assert (p27["rau_i2_ventas_alimento_amarillo"], p27["rau_i2_ventas_alimento_rojo"]) == (Decimal("0.15"),
                                                                                          Decimal("0.20"))
    assert (p26["archivo_retencion_t10_anios"], p26["archivo_retencion_t5_anios"],
            p26["archivo_retencion_desecho_anios"], p26["archivo_folios_max_legajo"]) == (10, 5, 2, 200)
    assert uno(con, "SELECT COUNT(*) FROM retencion_documental") == 72
    assert uno(con, "SELECT COUNT(*) FROM retencion_documental WHERE clase = 'PERMANENTE'") == 12
    assert uno(con, "SELECT COUNT(*) FROM exp_rau_requisitos") == 39
    assert {r[0] for r in con.execute("SELECT DISTINCT carpeta FROM exp_rau_requisitos")} == set("ABCDEFG")
    assert uno(con, "SELECT COUNT(*) FROM permisos WHERE recurso = 'archivo'") >= 5
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM retencion_documental WHERE codigo = 'FIN-TES-01'")


# ------------------------------- Fase 12 ----------------------------------
def test_semillas_fase12_presupuesto_poa_y_vigilancia(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["presupuesto_ingresos_amarillo"], p["presupuesto_ingresos_rojo"]) == (Decimal("0.05"), Decimal("0.10"))
    assert (p["presupuesto_costos_amarillo"], p["presupuesto_costos_rojo"]) == (Decimal("0.05"), Decimal("0.10"))
    assert (p["poa_cumplimiento_meta"], p["presupuesto_informe_ciclo_dias"]) == (Decimal("0.90"), 10)
    assert (p["presupuesto_cal_supuestos"], p["presupuesto_cal_comunicacion"]) == ("04-30", "07-01")
    assert p["cv07_partidas_min"] == 3
    assert uno(con, "SELECT COUNT(*) FROM presupuesto_maestro") == 625
    assert uno(con, "SELECT COUNT(*) FROM presupuesto_lineas") == 334
    assert uno(con, "SELECT COUNT(*) FROM poa_actividades") == 49
    assert uno(con, "SELECT COUNT(*) FROM permisos WHERE rol = 'GG' AND recurso = 'presupuesto' "
                    "AND accion = 'AUTORIZAR'") == 1
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM presupuesto_maestro WHERE ejercicio = 2026")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO informes_cv (codigo, ejercicio, periodo, fecha, datos_json, dictamen, elaborado_por, "
                    "usuario_id, registrado_en) VALUES ('INF-CV-07', 2026, '2026-09', '2026-10-05', '{}', 'CONFORME', "
                    "'x', 1, 'x')")


# ------------------------------- Fase 13 ----------------------------------
def test_semillas_fase13_respaldo_y_permisos(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert (p["respaldo_copias_minimas"], p["respaldo_medios_minimos"], p["respaldo_fuera_sitio_minimas"]) == (3, 2, 1)
    assert p["respaldo_prueba_restauracion_meses"] == 6 and p["respaldo_dia_semanal"] == 7
    assert p["poa_flujo_tolerancia_bs"] == Decimal("1")
    assert uno(con, "SELECT COUNT(*) FROM permisos WHERE recurso = 'respaldos'") == 8
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM parametros WHERE clave = 'sme'")
