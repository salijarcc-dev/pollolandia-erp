"""Pruebas de la Fase 12: sistema presupuestario (servicios/presupuesto.py).

Casos de borde tomados del Manual del Sistema Presupuestario:
* Presupuesto Maestro: ingresos 2026 Bs 924.000; excedente neto Bs 210.293;
  costo del ciclo 1 de 2026 Bs 116.286; ahorro D.6 2027 Bs 70.423.
* E.1, ejemplo del maíz: 208 qq a Bs 95,12 frente a 215 qq a Bs 98,00 →
  +Bs 1.285 (+6,5 %, amarillo); precio Bs 619,20; cantidad Bs 665,84.
* Umbrales: ingresos −5 %/−10 %; costos +5 %/+10 %; DPF 100 %/105 %; CAPEX 15 %; flujo 10 %.
* CI-FIN-09: variación CAPEX > 15 % y > Bs 10.000 → Asamblea; CAPEX no previsto → CA.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from db.conexion import obtener_parametros_vigentes
from pruebas import escenario_2026 as esc
from servicios import contable, presupuesto
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


@pytest.fixture()
def cargado(con, s):
    esc.cargar_escenario_2026(con, s)
    return con


# ============================ MAESTRO Y POR CICLO =============================
def test_presupuesto_maestro_2026_2030_cargado(con, s):
    m = {(r["modulo"], r["clave"]): r for r in presupuesto.presupuesto_maestro(con, s["ca_presidente"], 2026)}
    assert m[("VENTAS", "TOTAL_INGRESOS")]["monto"] == D("924000.00")
    assert m[("RESULTADOS", "EXCEDENTE_NETO")]["monto"] == D("210293.00")
    assert m[("FLUJO", "SALDO_FINAL")]["monto"] == D("291669.00")
    assert m[("GASTOS", "TOTAL_ADM")]["monto"] == D("122525.24")            # incluye RAU Bs 24,24
    assert m[("INDICADORES", "ICL")]["valor"] == D("0.624")
    d6 = {r["clave"]: r for r in presupuesto.presupuesto_maestro(con, s["jf"], 2027, "D6")}
    assert d6["AHORRO"]["monto"] == D("70423.00")
    serie = {f["clave"]: f for f in presupuesto.serie_maestro(con, s["jf"], "CAPEX")}
    assert [serie["TOTAL"][a] for a in ("2026", "2027", "2028", "2029", "2030")] == \
        [D("97000.00"), D("51390.00"), D("48150.00"), D("148390.00"), D("0.00")]
    assert uno(con, "SELECT COUNT(DISTINCT ejercicio) FROM presupuesto_lineas") == 5
    with pytest.raises(AutorizacionError):
        presupuesto.presupuesto_maestro(con, s["ut"], 2026)
    with pytest.raises(ReglaNegocioError):
        presupuesto.cargar_presupuesto_maestro(con, s["jf"], 2026, [{"modulo": "VENTAS", "clave": "X", "concepto": "X"}],
                                               [{"partida": "CAPEX", "ciclo": 1, "monto": 1}])


def test_metas_por_ciclo_suman_el_anual(con, s):
    for e in (2026, 2027, 2028, 2029, 2030):
        for partida, modulo, clave in (("ING_CARNE", "VENTAS", "ING_CARNE"), ("MP_POLLITOS", "MP", "POLLITOS"),
                                       ("CIF", "CIF", "TOTAL"), ("CAPEX", "CAPEX", "TOTAL")):
            lineas = uno(con, "SELECT COALESCE(SUM(monto_cent), 0) FROM presupuesto_lineas WHERE ejercicio = ? AND "
                              "partida = ?", e, partida)
            maestro = uno(con, "SELECT monto_cent FROM presupuesto_maestro WHERE ejercicio = ? AND modulo = ? AND "
                               "clave = ?", e, modulo, clave)
            assert lineas == maestro, (e, partida)
    c1 = presupuesto.presupuesto_ciclo(con, s["jf"], 2026, 1)
    costo = sum(f["monto"] for f in c1["filas"] if f["partida"] in
                ("MP_POLLITOS", "MP_ALIMENTO", "MP_OTROS", "MOD_FIJA", "MOD_EVENTUAL", "CIF"))
    assert costo.quantize(D("1")) == D("116286")                             # D.4-1: costo del ciclo 1
    assert {f["partida"]: f["monto"] for f in c1["filas"]}["MP_OTROS"] == D("14385.00")   # bolsas Bs 4.800
    c5 = presupuesto.presupuesto_ciclo(con, s["jf"], 2026, 5)
    assert c5["saldo_final_proyectado"] == D("291669.00") and c5["sme"] == D("136413.00")
    assert {f["partida"]: f for f in c5["filas"]}["CAPEX"]["acumulado"] == D("97000.00")
    sup = {f["clave"]: f for f in presupuesto.hoja_supuestos(con, s["jf"], 2027)}
    assert sup["precio_carne_kg"]["valor"] == D("23.50") and sup["sme"]["valor"] == D("145912")


# ============================ VARIACIONES ====================================
def test_variacion_de_precio_y_cantidad_ejemplo_del_maiz(con):
    v = presupuesto.variacion_precio_cantidad(208, "95.12", 215, "21070")
    assert v["variacion_precio"] == D("619.20") and v["variacion_cantidad"] == D("665.84")
    p = obtener_parametros_vigentes(con, 2027)
    sem = presupuesto.semaforo_partida(p, "COSTO", D("19784.96"), D("21070"))
    assert sem["semaforo"] == "AMARILLO" and sem["pct"] == D("0.0650") and sem["variacion"] == D("1285.04")
    with pytest.raises(DatosInvalidosError):
        presupuesto.variacion_precio_cantidad(208, "95.12", 0, "21070")


@pytest.mark.parametrize("regla, presu, real, esperado", [
    ("INGRESO", "100000", "95000", "VERDE"), ("INGRESO", "100000", "94999", "AMARILLO"),
    ("INGRESO", "100000", "90000", "AMARILLO"), ("INGRESO", "100000", "89999", "ROJO"),
    ("INGRESO", "100000", "120000", "VERDE"),
    ("COSTO", "100000", "105000", "VERDE"), ("COSTO", "100000", "105001", "AMARILLO"),
    ("COSTO", "100000", "110000", "AMARILLO"), ("COSTO", "100000", "110001", "ROJO"), ("COSTO", "0", "1", "ROJO"),
    ("DPF", "4500", "4500", "VERDE"), ("DPF", "4500", "4725", "AMARILLO"), ("DPF", "4500", "4726", "ROJO"),
    ("CAPEX", "97000", "111550", "VERDE"), ("CAPEX", "97000", "111551", "ROJO"), ("CAPEX", "0", "20000", "ROJO"),
    ("FLUJO", "291669", "262502.10", "VERDE"), ("FLUJO", "291669", "262502", "ROJO"),
])
def test_semaforos_por_partida(con, regla, presu, real, esperado):
    p = obtener_parametros_vigentes(con, 2026)
    assert presupuesto.semaforo_partida(p, regla, D(presu), D(real))["semaforo"] == esperado


def test_flujo_bajo_el_sme_es_rojo(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert presupuesto.semaforo_partida(p, "FLUJO", D("140000"), D("135000"), D("136413"))["semaforo"] == "ROJO"


def test_ejecucion_del_escenario_2026_en_verde(cargado, s):
    c1 = presupuesto.calcular_ejecucion(cargado, s["jf"], 2026, "CICLO", 1)
    por = {f["partida"]: f for f in c1["filas"]}
    assert (c1["desde"], c1["hasta"]) == ("2026-07-01", "2026-09-11")
    assert por["ING_CARNE"]["real"] == D("183300.00") and por["MP_POLLITOS"]["real"] == D("27000.00")
    assert c1["rojos"] == 0 and c1["amarillos"] == 0 and c1["semaforo"] == "VERDE"
    anual = presupuesto.calcular_ejecucion(cargado, s["ca_presidente"], 2026, "ANUAL", 0)
    assert anual["flujo"]["presupuesto"] == D("291669.00") and anual["flujo"]["real"] == D("291668.90")
    assert {f["partida"]: f for f in anual["filas"]}["CAPEX"]["real"] == D("97000.00")
    sem = presupuesto.calcular_ejecucion(cargado, s["jf"], 2026, "SEMESTRE", 1)
    assert sem["ciclos"] == [1, 2]
    con_cantidad = presupuesto.calcular_ejecucion(cargado, s["jf"], 2026, "CICLO", 2,
                                                  cantidades_reales={"ING_CARNE": "7566"})
    carne = {f["partida"]: f for f in con_cantidad["filas"]}["ING_CARNE"]
    # Manual B.1: 7.566 kg reales (3 % de mortalidad) frente a 7.800 kg presupuestados.
    assert carne["variacion_cantidad"] == D("-5499.00")
    assert con_cantidad["costo_unitario"]["real"] == D("15.21")


def test_informe_fin_tes_12_exige_causa_y_accion_en_rojo(cargado, s):
    con = cargado
    contable.crear_asiento(con, s["jf"], "2026-11-23", "Mantenimiento extraordinario de galpones", [
        {"cuenta": "6.1.01.01", "sufijo": "36", "debe": D("1000")}, {"cuenta": "1.1.01.03", "haber": D("1000")}],
        "PRUEBA", "MANT-1", tipo="EGRESO")
    calc = presupuesto.calcular_ejecucion(con, s["jf"], 2026, "CICLO", 2)
    cif = {f["partida"]: f for f in calc["filas"]}["CIF"]
    assert cif["semaforo"] == "ROJO" and cif["pct"] == D("0.1582")
    with pytest.raises(DatosInvalidosError):
        presupuesto.registrar_informe_ejecucion(con, s["jf"], 2026, "CICLO", 2, "2026-11-20")
    with pytest.raises(ReglaNegocioError, match="causa"):
        presupuesto.registrar_informe_ejecucion(con, s["jf"], 2026, "CICLO", 2, "2026-12-01")
    with pytest.raises(ReglaNegocioError, match="responsable"):
        presupuesto.registrar_informe_ejecucion(con, s["jf"], 2026, "CICLO", 2, "2026-12-01",
                                                {"CIF": {"causa": "Reparación de techos"}})
    r = presupuesto.registrar_informe_ejecucion(
        con, s["jf"], 2026, "CICLO", 2, "2026-12-01",
        {"CIF": {"causa": "Reparación de techos", "responsable": "Encargado de Granja",
                 "accion": "Programar mantenimiento preventivo", "fecha_compromiso": "2027-01-15"}})
    assert r["rojos"] == 1 and r["fuera_de_plazo"] is False and r["limite"] == "2026-12-03"
    with pytest.raises(ReglaNegocioError):
        presupuesto.registrar_informe_ejecucion(con, s["jf"], 2026, "CICLO", 2, "2026-12-02",
                                                {"CIF": {"causa": "x", "responsable": "y", "accion": "z"}})
    with pytest.raises(AutorizacionError):
        presupuesto.presentar_informe(con, s["jf"], r["id"], "2026-12-05")
    presupuesto.presentar_informe(con, s["gg"], r["id"], "2026-12-05")
    inf = presupuesto.obtener_informe(con, s["ca_presidente"], r["id"])
    assert inf["acciones"]["CIF"]["accion"] == "Programar mantenimiento preventivo"
    with pytest.raises(ReglaNegocioError):
        presupuesto.presentar_informe(con, s["gg"], r["id"], "2026-12-06")


# ============================ MODIFICACIONES (CI-FIN-09) ======================
@pytest.mark.parametrize("partida, ciclo, solicitado, autoridad", [
    ("CAPEX", 5, "110000", "GG"),        # +13,4 %
    ("CAPEX", 5, "112000", "AG"),        # +15,5 % y Bs 15.000 > Bs 10.000
    ("CAPEX", 3, "25000", "CA"),         # CAPEX no previsto en el ciclo 3
    ("GASTOS_VENTA", 1, "3100", "GG"),   # +6,5 %
    ("GASTOS_VENTA", 1, "3300", "CA"),   # +13,4 %
])
def test_autoridad_de_la_modificacion(con, partida, ciclo, solicitado, autoridad):
    aprobado = presupuesto._linea(con, 2026, ciclo, partida)["monto"]
    obtenida, fundamentos = presupuesto.autoridad_modificacion(con, 2026, partida, aprobado, D(solicitado), "2026-08-01")
    assert obtenida == autoridad and fundamentos


def test_modificacion_capex_mayor_al_15_y_10000_requiere_asamblea(con, s):
    r = presupuesto.solicitar_modificacion(con, s["gg"], 2026, 5, "CAPEX", "115000", motivo="Alza del acero",
                                           fuente_recursos="Recursos propios", efecto_flujo="Saldo sobre el SME",
                                           fecha="2027-04-20")
    assert r["autoridad"] == "AG" and r["variacion_pct"] == D("0.1856")
    with pytest.raises(ReglaNegocioError, match="verificar"):
        presupuesto.resolver_modificacion(con, s["ag"], r["id"], True, "2027-04-25", "Acta AG 2/2027")
    presupuesto.verificar_modificacion(con, s["jf"], r["id"])
    with pytest.raises(AutorizacionError):
        presupuesto.resolver_modificacion(con, s["ca_presidente"], r["id"], True, "2027-04-25", "Acta CA 4")
    with pytest.raises(DatosInvalidosError):
        presupuesto.resolver_modificacion(con, s["ag"], r["id"], True, "2027-04-25")
    assert presupuesto.resolver_modificacion(con, s["ag"], r["id"], True, "2027-04-25", "Acta AG 2/2027") == "APROBADA"
    assert presupuesto._linea(con, 2026, 5, "CAPEX")["monto"] == D("115000.00")


def test_modificacion_por_la_gg_y_rechazo(con, s):
    with pytest.raises(AutorizacionError):
        presupuesto.solicitar_modificacion(con, s["jf"], 2026, 1, "GASTOS_VENTA", "3100", motivo="m",
                                           fuente_recursos="f", efecto_flujo="e", fecha="2026-08-01")
    r = presupuesto.solicitar_modificacion(con, s["gg"], 2026, 1, "GASTOS_VENTA", "3100", motivo="Flete adicional",
                                           fuente_recursos="Recursos propios", efecto_flujo="Sin efecto en el SME",
                                           fecha="2026-08-01")
    with pytest.raises(ReglaNegocioError):
        presupuesto.solicitar_modificacion(con, s["gg"], 2026, 1, "GASTOS_VENTA", "3200", motivo="m",
                                           fuente_recursos="f", efecto_flujo="e", fecha="2026-08-01")
    presupuesto.verificar_modificacion(con, s["jf"], r["id"])
    with pytest.raises(DatosInvalidosError):
        presupuesto.resolver_modificacion(con, s["gg"], r["id"], False, "2026-08-02")
    assert presupuesto.resolver_modificacion(con, s["gg"], r["id"], False, "2026-08-02",
                                             observacion="Usar el flete presupuestado") == "RECHAZADA"
    assert presupuesto._linea(con, 2026, 1, "GASTOS_VENTA")["monto"] == D("2910.00")
    with pytest.raises(Exception):
        con.execute("DELETE FROM modificaciones_presupuesto WHERE id = ?", (r["id"],))


# ============================ CALENDARIO =====================================
def test_calendario_presupuestario_abril_a_julio(con, s):
    cal = presupuesto.calendario_presupuestario(con, s["jf"], 2027, "2027-05-20")
    limites = [h["limite"] for h in cal["hitos"]]
    assert limites == ["2027-04-30", "2027-05-15", "2027-05-24", "2027-05-31", "2027-06-15", "2027-06-25",
                       "2027-07-01"]
    estados = {h["actividad"]: h["estado"] for h in cal["hitos"]}
    assert estados[1] == "VENCIDO" and estados[3] == "POR_VENCER" and estados[7] == "PROGRAMADO"
    assert presupuesto.calendario_presupuestario(con, s["jf"], 2026)["transitoria"] is True
    with pytest.raises(ReglaNegocioError, match="primero"):
        presupuesto.registrar_hito(con, s["jf"], 2027, 2, "2027-05-10", "FIN-TES-10 v1")
    with pytest.raises(AutorizacionError):
        presupuesto.registrar_hito(con, s["gg"], 2027, 1, "2027-04-28", "FIN-TES-09")
    h = presupuesto.registrar_hito(con, s["jf"], 2027, 1, "2027-04-28", "FIN-TES-09 2027")
    assert h["en_plazo"] is True
    assert "precio" in uno(con, "SELECT detalle_json FROM hitos_presupuesto WHERE id = ?", h["id"]).lower()
    assert presupuesto.registrar_hito(con, s["jf"], 2027, 2, "2027-05-18", "FIN-TES-10 2027")["en_plazo"] is False
    presupuesto.registrar_hito(con, s["gg"], 2027, 3, "2027-05-22", "Acta Comité 1/2027")
    presupuesto.registrar_hito(con, s["gg"], 2027, 4, "2027-05-30", "FIN-TES-01 2027")
    with pytest.raises(AutorizacionError):
        presupuesto.registrar_hito(con, s["gg"], 2027, 5, "2027-06-10", "Acta CA")
    presupuesto.registrar_hito(con, s["ca_presidente"], 2027, 5, "2027-06-10", "Acta CA 6/2027")
    presupuesto.registrar_hito(con, s["cv"], 2027, 6, "2027-06-20", "Nota CV 3/2027")
    cal = presupuesto.calendario_presupuestario(con, s["jf"], 2027, "2027-06-21")
    assert [h["estado"] for h in cal["hitos"]][:6] == ["CUMPLIDO", "CUMPLIDO_FUERA_DE_PLAZO", "CUMPLIDO", "CUMPLIDO",
                                                       "CUMPLIDO", "CUMPLIDO"]


# ============================ POA ============================================
def test_poa_cumplimiento_fisico_y_semaforo(con, s):
    actividades = presupuesto.listar_poa(con, s["jf"], 2026)
    assert len(actividades) == 49
    a1 = next(a for a in actividades if a["codigo"] == "A1")
    a9 = next(a for a in actividades if a["codigo"] == "A9")
    with pytest.raises(ReglaNegocioError):
        presupuesto.registrar_avance(con, s["jf"], a9["id"], 2, "2026-11-20", "CUMPLIDA", "Acta")
    with pytest.raises(DatosInvalidosError):
        presupuesto.registrar_avance(con, s["jf"], a1["id"], 2, "2026-11-20", "CUMPLIDA")
    t2 = [a for a in actividades if 2 in a["programados"]]
    for a in t2[:-3]:
        presupuesto.registrar_avance(con, s["jf"], a["id"], 2, "2026-12-20", "CUMPLIDA", "Formato del período")
    presupuesto.registrar_avance(con, s["jf"], t2[-1]["id"], 2, "2027-01-05", "CUMPLIDA", "Fuera de plazo")
    presupuesto.registrar_avance(con, s["jf"], t2[-2]["id"], 2, "2026-12-30", "PARCIAL")
    c = presupuesto.cumplimiento_poa(con, s["ca_presidente"], 2026, "2027-01-10")
    t = c["trimestres"][1]
    assert t["programadas"] == len(t2) and t["cumplidas"] == len(t2) - 3
    assert t["semaforo"] == ("VERDE" if (len(t2) - 3) / len(t2) >= 0.9 else "AMARILLO")
    assert c["trimestres"][2]["semaforo"] is None                     # trimestre 3 aún no vencido
    p = obtener_parametros_vigentes(con, 2026)
    assert [presupuesto.semaforo_poa(p, D(x)) for x in ("0.90", "0.8999", "0.75", "0.7499")] == \
        ["VERDE", "AMARILLO", "AMARILLO", "ROJO"]
    with pytest.raises(ReglaNegocioError):
        presupuesto.registrar_avance(con, s["jf"], t2[0]["id"], 2, "2026-12-21", "CUMPLIDA", "Duplicado")
    nueva = presupuesto.registrar_actividad(con, s["jf"], 2026, "F1", "COMERCIAL", "Feria de Caranavi",
                                            "1 participación", "JFC", [3])
    assert nueva > 0


# ============================ D.6 =============================================
def test_alimento_propio_frente_a_comprado(con, s):
    con.execute("INSERT INTO kardex_terminados (producto, fecha, tipo, cantidad_mil, valor_cent, saldo_cantidad_mil, "
                "saldo_valor_cent, cpp, registrado_por, registrado_en) VALUES ('ALIMENTO','2027-07-20',"
                "'SALIDA_CONSUMO_PROPIO',300000,4141500,0,0,'138.05','test','2027-07-20')")
    d6 = presupuesto.alimento_propio_vs_comprado(con, s["jf"], 2027)
    assert d6["presupuesto"]["AHORRO"] == D("70423.00") and d6["presupuesto"]["CONSUMO_QQ"] == D("1500")
    assert d6["real"]["COSTO_COMPRA"] == D("55500.00") and d6["real"]["AHORRO"] == D("14085.00")
    assert d6["real"]["UNITARIO_PROPIO"] == D("138.05")
    assert presupuesto.alimento_propio_vs_comprado(con, s["jf"], 2026)["aplica"] is False
