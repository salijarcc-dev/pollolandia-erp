"""Pruebas de la Fase 10 — Contabilidad general, cierres y estados financieros.

Casos con las cifras de los documentos:
* Escenario proyectado 2026 (Manual Presupuestario; «21. EEFF proyectados»):
  excedente neto Bs 210.293,39, ingresos Bs 924.000, flujo final
  Bs 291.668,90 y balance cuadrado (activo Bs 767.742,65).
* Indicadores 2026: ICL 62,41 % (Normal), ROA 27,39 % (Bueno), RINC 0,81 %.
* Distribución: Reserva Legal 5 % (Bs 10.514,67), Educación 1 %
  (Bs 2.102,93), Reinversión Bs 51.919,30 → distribuible Bs 145.756,49 y
  pasivo total Bs 168.905,75 (BG proyectado 2026).
* Pago del excedente bloqueado con SME 2027 Bs 145.912 (saldo posterior
  Bs 140.912,41) y permitido tras los cobros del ciclo 1 de 2027.
* Plazos de 60 y 90 días desde el 30-jun-2027 (29-ago y 28-sep-2027).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from io import BytesIO

from openpyxl import load_workbook

from pruebas import escenario_2026 as esc
from reportes import estados as rep
from servicios import cierre, contable, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, PeriodoCerradoError, ReglaNegocioError

D = Decimal


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def periodo(con, ejercicio, numero):
    return int(uno(con, "SELECT id FROM periodos WHERE ejercicio = ? AND numero = ?", ejercicio, numero))


def desembolso(con, s, monto, fecha):
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, "Desembolso", ctx)
    if seguridad.firmar_autorizacion(con, s["gg"], aid) == "PENDIENTE":
        seguridad.firmar_autorizacion(con, s["cv"], aid)
    return aid


@pytest.fixture()
def cerrado(con, s):
    esc.cargar_escenario_2026(con, s)
    esc.cerrar_ejercicio_completo(con, s)
    return con


def ctx(s):
    from datetime import date
    return rep.Contexto(fecha=date(2027, 8, 20), elaborado_por=s["jf"].nombre_completo, cargo="JF")


# ============================ ESCENARIO 2026 ================================
def test_escenario_2026_reproduce_cifras_y_balance_cuadra(cerrado, s):
    con = cerrado
    er = cierre.estado_resultados(con, s["jf"], 2026)
    assert er["total_ingresos"] == D("924000.00")
    assert er["excedente_neto"] == D("210293.39") and round(er["excedente_neto"]) == 210293
    assert (er["costo_ventas"], er["utilidad_bruta"], er["ebit"]) == (D("576631.41"), D("339868.59"), D("202793.39"))
    assert er["cuadra"]
    efe = cierre.estado_flujo_efectivo(con, s["jf"], 2026)
    assert efe["saldo_inicial"] == D("138010.00")
    assert efe["saldo_final"] == D("291668.90") and round(efe["saldo_final"]) == 291669
    assert efe["inversion"] == D("97000.00") and efe["cuadra"]
    lineas = {f["concepto"]: f["monto"] for f in efe["filas"]}
    assert lineas["Maquinaria para alimento balanceado"] == D("77000.00")
    assert lineas["Construcción de ambiente para preparación"] == D("20000.00")
    assert efe["montos"]["P_APORTES"] == D("-6247.23")         # EFE del Excel: 4 × 1.363,03 + 795,10
    bg = cierre.balance_general(con, s["jf"], "2027-06-30")
    assert bg["cuadra"] and bg["total_activo"] == D("767742.65")
    assert (bg["total_activo_corriente"], bg["total_activo_no_corriente"]) == (D("291668.90"), D("476073.75"))
    assert (bg["total_pasivo_corriente"], bg["total_pasivo_no_corriente"]) == (D("12549.26"), D("10600.00"))
    assert bg["total_patrimonio"] == D("744593.39")
    ee = cierre.estado_evolucion_patrimonio(con, s["jf"], 2026)
    assert ee["cuadra"] and ee["total_final"] == D("744593.39")
    assert ee["filas"][0]["capital"] == D("534300.00") and ee["filas"][2]["excedentes"] == D("210293.39")


def test_cierre_anual_salda_resultados_en_8_1_01_01_y_traslada_a_3_1_03_02(cerrado, s):
    con = cerrado
    assert tesoreria.saldo_cuenta(con, "8.1.01.01") == D("0.00")
    assert tesoreria.saldo_cuenta(con, "3.1.03.02") == D("-210293.39")
    for cuenta in ("5.1.01.01", "5.2.01.04", "6.1.02.01", "7.1.01.01"):
        assert tesoreria.saldo_cuenta(con, cuenta) == D("0.00")
    assert uno(con, "SELECT COUNT(*) FROM asientos WHERE tipo = 'CIERRE'") == 2
    assert uno(con, "SELECT estado FROM ejercicios WHERE ejercicio = 2026") == "CERRADO"
    assert uno(con, "SELECT COUNT(*) FROM cierres_mensuales") == 12
    # el Estado de Resultados excluye el asiento de cierre
    assert cierre.estado_resultados(con, s["jf"], 2026)["excedente_neto"] == D("210293.39")
    with pytest.raises(PeriodoCerradoError):
        contable.crear_asiento(con, s["jf"], "2027-06-30", "Tardío", [
            {"cuenta": "7.1.02.01", "debe": "1"}, {"cuenta": "1.1.01.03", "haber": "1"}], "PRUEBA", tipo="EGRESO")
    cierre_id = uno(con, "SELECT asiento_cierre_id FROM cierres_anuales")
    aut = esc.autorizacion_cierre(con, s, "1", "2027-07-01")
    with pytest.raises(ReglaNegocioError) as e:
        contable.reversar_asiento(con, s["jf"], cierre_id, "No", autorizacion_id=aut, fecha="2027-07-01")
    assert e.value.codigo == "ASIENTO_NO_REVERSIBLE"


def test_indicadores_anuales_y_fin_inf_04_mensual(cerrado, s):
    con = cerrado
    ind = cierre.indicadores_anuales(con, s["jf"], 2026)
    assert (ind["ICL"]["valor"], ind["ICL"]["semaforo"]) == (D("0.6241"), "NORMAL")      # Manual: 62,4 %
    assert (ind["ROA"]["valor"], ind["ROA"]["semaforo"]) == (D("0.2739"), "BUENO")       # Manual: 27,4 %
    assert (ind["RINC"]["valor"], ind["RINC"]["semaforo"]) == (D("0.0081"), "MALO")
    sep = cierre.estado_resultados_mensual(con, s["jf"], periodo(con, 2026, 3))           # cierre del ciclo 1
    assert sep["mes"]["ventas_totales"] == D("184800.00") and sep["mes"]["costo_ventas"] == D("116286.28")
    assert sep["indicadores_mes"]["ICL"]["valor"] == D("0.6293")
    assert sep["indicadores_mes"]["ICL"]["semaforo"] == "NORMAL"
    jun = cierre.estado_resultados_mensual(con, s["jf"], periodo(con, 2026, 12))
    assert jun["acumulado"]["excedente_neto"] == D("210293.39")
    ago = cierre.estado_resultados_mensual(con, s["jf"], periodo(con, 2026, 2))
    assert ago["indicadores_mes"]["ICL"]["valor"] is None                                  # mes sin ventas
    paquete = cierre.paquete_cierre(con, s["jf"], periodo(con, 2026, 3))
    assert D(paquete["costeo"]["costo_ventas"]) == D("116286.28")


def test_notas_1_a_16_con_saldos_reales(cerrado, s):
    con = cerrado
    notas = cierre.generar_notas(con, s["jf"], 2026, hechos_posteriores="Inicio del ciclo 1 de 2027 con 3.000 aves.")
    assert [n["numero"] for n in notas] == list(range(1, 17))
    texto = str(notas)
    assert "XXX" not in texto
    tabla3 = next(b for b in notas[2]["bloques"] if b["tipo"] == "tabla")
    assert tabla3["filas"][-1][-1] == "Bs 291.668,90"
    tabla13 = next(b for b in notas[12]["bloques"] if b["tipo"] == "tabla")
    assert tabla13["filas"][-1][2] == "Bs 924.000,00" and tabla13["filas"][0][3] == "99,2 %"
    tabla11 = next(b for b in notas[10]["bloques"] if b["tipo"] == "tabla")
    assert tabla11["filas"][0][-1] == "Bs 10.600,00" and "8,33 %" in tabla11["filas"][0][2]
    tabla12 = [b for b in notas[11]["bloques"] if b["tipo"] == "tabla"]
    assert tabla12[0]["filas"][-1][-1] == "Bs 534.300,00" and tabla12[0]["filas"][0][-1] == "Bs 138.010,00"
    tabla61 = next(b for b in notas[5]["bloques"] if b["tipo"] == "tabla")
    assert tabla61["filas"][1][-1] == "Bs 99.750,00"                      # galpones 105.000 − 5.250
    assert "Inicio del ciclo 1 de 2027" in str(notas[15])
    assert notas[13]["titulo"] == "COSTOS DE PRODUCCIÓN Y GASTOS OPERATIVOS"
    tabla14 = next(b for b in notas[13]["bloques"] if b["tipo"] == "tabla")
    assert ["Total costo de producción / costo de ventas", "Bs 576.631,41"] in tabla14["filas"]


# ========================== CIERRE MENSUAL ==================================
def test_lista_fin_inf_01_bloquea_el_cierre_hasta_pendientes_en_cero(con, s):
    esc.cargar_escenario_2026(con, s)
    julio = periodo(con, 2026, 1)
    with pytest.raises(ReglaNegocioError) as e:
        cierre.cerrar_mes(con, s["gg"], julio)
    assert e.value.codigo == "SIN_LISTA_VERIFICACION"
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-10", "120", "7.1.02.01", "GM-901", "Librería", "Útiles")
    rid = tesoreria.solicitar_reposicion(con, s["jf"], "2026-07-20")
    lista = cierre.registrar_lista_verificacion(con, s["jf"], julio)
    assert lista["pendientes"] == 1 and lista["filas"][0]["pendientes"] == 1 and not lista["filas"][0]["cumple"]
    with pytest.raises(ReglaNegocioError) as e:
        cierre.cerrar_mes(con, s["gg"], julio)
    assert e.value.codigo == "PENDIENTES_CIERRE"
    with pytest.raises(AutorizacionError):
        cierre.cerrar_mes(con, s["jf"], julio)                     # el cierre lo ejecuta la GG
    tesoreria.validar_reposicion(con, s["gg"], rid)
    tesoreria.ejecutar_reposicion(con, s["jf"], rid, "2026-07-25")
    assert cierre.registrar_lista_verificacion(con, s["jf"], julio)["pendientes"] == 0
    cierre.cerrar_mes(con, s["gg"], julio)
    assert uno(con, "SELECT estado FROM periodos WHERE id = ?", julio) == "CERRADO"
    with pytest.raises(PeriodoCerradoError):
        contable.crear_asiento(con, s["jf"], "2026-07-31", "Tardío", [
            {"cuenta": "7.1.02.01", "debe": "1"}, {"cuenta": "1.1.01.03", "haber": "1"}], "PRUEBA", tipo="EGRESO")
    with pytest.raises(Exception):
        con.execute("DELETE FROM listas_cierre")


def test_cierre_anual_exige_requisitos_y_autorizacion(con, s):
    esc.cargar_escenario_2026(con, s)
    aut = esc.autorizacion_cierre(con, s, "2000000")
    with pytest.raises(ReglaNegocioError) as e:
        cierre.ejecutar_cierre_anual(con, s["jf"], 2026, aut)
    assert e.value.codigo == "CIERRE_ANUAL_NO_LISTO"
    esc.cerrar_meses(con, s, 2026, 11)
    junio = periodo(con, 2026, 12)
    cierre.registrar_lista_verificacion(con, s["jf"], junio)
    with pytest.raises(ReglaNegocioError) as e:
        cierre.cerrar_mes(con, s["gg"], junio)
    assert e.value.codigo == "CIERRE_ANUAL_PENDIENTE"
    previa = cierre.preparar_cierre_anual(con, s["jf"], 2026)
    assert previa["listo"] and previa["resultado"] == D("210293.39")
    corta = esc.autorizacion_cierre(con, s, "1000")
    with pytest.raises(AutorizacionError) as e:
        cierre.ejecutar_cierre_anual(con, s["jf"], 2026, corta)
    assert e.value.codigo == "AUTORIZACION_INSUFICIENTE"
    with pytest.raises(AutorizacionError):
        cierre.ejecutar_cierre_anual(con, s["cv"], 2026, aut)
    r = cierre.ejecutar_cierre_anual(con, s["jf"], 2026, aut)
    assert r["resultado"] == D("210293.39")
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id = ?", aut) == "UTILIZADA"
    with pytest.raises(ReglaNegocioError):
        cierre.cerrar_ejercicio(con, s["gg"], 2026)                    # junio sigue abierto


# ============================ PLAZOS 60/90 ==================================
def test_plazos_de_60_y_90_dias_con_alertas(cerrado, s):
    con = cerrado
    p = cierre.plazos_eeff(con, s["gg"], "2027-08-20")[0]
    assert (p["limite_consejos"], p["limite_asamblea"]) == ("2027-08-29", "2027-09-28")
    assert (p["estado_consejos"], p["dias_consejos"], p["estado_asamblea"]) == ("POR_VENCER", 9, "EN_PLAZO")
    alertas = cierre.alertas_plazos(con, s["gg"], "2027-09-15")
    assert [a["estado"] for a in alertas] == ["VENCIDO", "POR_VENCER"]
    with pytest.raises(ReglaNegocioError):
        cierre.registrar_aprobacion_asamblea(con, s["ag"], 2026, "2027-09-10", "Acta AG 1/2027", "INF-CV-08 1/2027")
    r = cierre.registrar_presentacion_consejos(con, s["gg"], 2026, "2027-08-25", "Acta CA 9/2027")
    assert r["dentro_plazo"]
    emitido = cierre.obtener_estado_emitido(con, s["gg"], r["estado_emitido_id"])
    assert emitido["integro"] and D(emitido["paquete"]["resultados"]["excedente_neto"]) == D("210293.39")
    a = cierre.registrar_aprobacion_asamblea(con, s["ag"], 2026, "2027-09-30", "Acta AG 1/2027", "INF-CV-08 1/2027")
    assert not a["dentro_plazo"]
    p = cierre.plazos_eeff(con, s["gg"], "2027-10-01")[0]
    assert (p["estado_consejos"], p["estado_asamblea"]) == ("CUMPLIDO", "CUMPLIDO_FUERA_DE_PLAZO")
    assert cierre.alertas_plazos(con, s["gg"], "2027-10-01") == []
    with pytest.raises(Exception):
        con.execute("UPDATE estados_emitidos SET huella = 'x'")


# ======================= DISTRIBUCIÓN DE EXCEDENTES =========================
def test_distribucion_reservas_minimas_y_pago_condicionado_al_sme(cerrado, s):
    con = cerrado
    with pytest.raises(ReglaNegocioError) as e:
        cierre.calcular_distribucion(con, 2026, pct_reserva_legal="0.04")
    assert e.value.codigo == "RESERVA_BAJO_MINIMO"
    with pytest.raises(ReglaNegocioError) as e:
        cierre.calcular_distribucion(con, 2026, pct_educacion="0.005")
    assert e.value.codigo == "RESERVA_BAJO_MINIMO"
    prop = cierre.proponer_distribucion(con, s["jf"], 2026, "2027-07-10", reinversion="51919.30")
    assert (prop["reserva_legal"], prop["educacion"], prop["distribuible"]) == \
        (D("10514.67"), D("2102.93"), D("145756.49"))
    montos = [r["monto"] for r in prop["reparto"]]
    assert len(montos) == 12 and sum(montos) == D("145756.49") and set(montos) == {D("12146.37"), D("12146.38")}
    did = prop["id"]
    with pytest.raises(ReglaNegocioError) as e:
        cierre.aprobar_distribucion(con, s["jf"], did, "2027-08-30", autorizacion_asamblea_id=1, acta_ref="Acta")
    assert e.value.codigo == "SIN_INF_CV_04"
    with pytest.raises(ReglaNegocioError) as e:                          # saldo posterior 140.912,41 < SME 145.912
        cierre.registrar_inf_cv_04(con, s["cv"], did, "2027-07-15", dictamen="FAVORABLE", reinversion_conforme=True)
    assert e.value.codigo == "DICTAMEN_INCONSISTENTE"
    inf = cierre.registrar_inf_cv_04(con, s["cv"], did, "2027-07-15", dictamen="CON_OBSERVACIONES",
                                     reinversion_conforme=True,
                                     observaciones="Pago sujeto a los cobros del ciclo 1 (SME 145.912).")
    assert [v["cumple"] for v in inf["verificaciones"]] == [True, True, True, False, True]
    ctx_ag = seguridad.contexto_desde_bd(con, "2027-08-30")
    otra = seguridad.solicitar_autorizacion(con, s["jf"], "DISTRIBUCION_EXCEDENTES", "145756.49", "Otra", ctx_ag,
                                            documentos={"INF-CV-04": "INF-CV-04 N.º 99/2027"})
    seguridad.firmar_autorizacion(con, s["ag"], otra, documento_ref="Acta AG 1/2027")
    with pytest.raises(ReglaNegocioError) as e:
        cierre.aprobar_distribucion(con, s["jf"], did, "2027-08-30", autorizacion_asamblea_id=otra, acta_ref="Acta")
    assert e.value.codigo == "AUTORIZACION_SIN_INF_CV_04"
    asamblea = seguridad.solicitar_autorizacion(con, s["jf"], "DISTRIBUCION_EXCEDENTES", "145756.49",
                                                "Distribución 2026", ctx_ag, documentos={"INF-CV-04": inf["codigo"]})
    seguridad.firmar_autorizacion(con, s["ag"], asamblea, documento_ref="Acta AG 1/2027")
    r = cierre.aprobar_distribucion(con, s["jf"], did, "2027-08-30", autorizacion_asamblea_id=asamblea,
                                    acta_ref="Acta AG 1/2027")
    assert r["estado"] == "APROBADA"
    assert tesoreria.saldo_cuenta(con, "2.1.05.01") == D("-145756.49")
    assert tesoreria.saldo_cuenta(con, "3.1.03.02") == D("0.00")
    bg = cierre.balance_general(con, s["jf"], "2027-08-30")
    assert bg["total_pasivo"] == D("168905.75") and bg["reservas"] == D("64536.90")   # BG proyectado 2026
    assert bg["cuadra"] and bg["total_patrimonio"] == D("598836.90")
    ee = cierre.estado_evolucion_patrimonio(con, s["jf"], 2027, "2027-08-30")
    assert ee["cuadra"] and [c for c, _ in ee["columnas"]] == ["capital", "reserva_legal", "educacion",
                                                              "otras_reservas", "excedentes"]
    assert ee["filas"][3]["excedentes"] == D("-145756.49")
    aut = desembolso(con, s, "145756.49", "2027-08-31")
    with pytest.raises(ReglaNegocioError) as e:
        cierre.pagar_distribucion(con, s["jf"], did, "2027-08-31", "1.1.01.03", autorizacion_desembolso_id=aut)
    assert e.value.codigo == "LIQUIDACIONES_SIN_FIRMA"
    assert cierre.firmar_liquidaciones(con, s["gg"], did) == 12
    control = cierre.verificar_pago_distribucion(con, did, "2027-08-31")
    assert (control["saldo_posterior"], control["sme"], control["permitido"]) == \
        (D("140912.41"), D("145912.00"), False)
    with pytest.raises(ReglaNegocioError) as e:
        cierre.pagar_distribucion(con, s["jf"], did, "2027-08-31", "1.1.01.03", autorizacion_desembolso_id=aut)
    assert e.value.codigo == "PAGO_EXCEDENTES_BAJO_SME"
    tesoreria.registrar_deposito(con, s["jf"], "2027-09-11", "205860", "1.1.01.03", "5.1.01.01",
                                 "Cobros del ciclo 1 de 2027 (carne, alimento y gallinaza)")
    pago = cierre.pagar_distribucion(con, s["jf"], did, "2027-09-12", "1.1.01.03", autorizacion_desembolso_id=aut)
    assert pago["monto"] == D("145756.49")
    assert tesoreria.saldo_cuenta(con, "2.1.05.01") == D("0.00")
    assert tesoreria.saldo_bancario(con, "2027-09-12") == D("346772.41")
    liqs = cierre.liquidaciones(con, s["jf"], did)
    assert {l["estado"] for l in liqs} == {"PAGADA"} and sum(l["neto"] for l in liqs) == D("145756.49")
    efe27 = cierre.estado_flujo_efectivo(con, s["jf"], 2027, "2027-09-30")
    assert efe27["montos"]["FIN_UTILIDADES"] == D("-145756.49") and efe27["cuadra"]
    assert "ANT_SUELDOS" not in efe27["montos"]                           # los sueldos de junio aún no se pagaron
    assert efe27["saldo_inicial"] == D("291668.90")
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id = ?", asamblea) == "UTILIZADA"


def test_pago_bloqueado_por_obligaciones_vencidas_y_capitalizacion(cerrado, s):
    con = cerrado
    prop = cierre.proponer_distribucion(con, s["jf"], 2026, "2027-07-10", reinversion="51919.30",
                                        capitalizacion="45756.49")
    assert prop["distribuible"] == D("100000.00")
    tesoreria.registrar_deposito(con, s["jf"], "2027-07-12", "205860", "1.1.01.03", "5.1.01.01", "Cobros ciclo 1")
    inf = cierre.registrar_inf_cv_04(con, s["cv"], prop["id"], "2027-07-15", dictamen="FAVORABLE",
                                     reinversion_conforme=True)
    ctx_ag = seguridad.contexto_desde_bd(con, "2027-08-30")
    asamblea = seguridad.solicitar_autorizacion(con, s["jf"], "DISTRIBUCION_EXCEDENTES", "100000", "Distribución",
                                                ctx_ag, documentos={"INF-CV-04": inf["codigo"]})
    seguridad.firmar_autorizacion(con, s["ag"], asamblea, documento_ref="Acta AG 1/2027")
    with pytest.raises(AutorizacionError) as e:
        cierre.aprobar_distribucion(con, s["jf"], prop["id"], "2027-08-30", autorizacion_asamblea_id=asamblea,
                                    acta_ref="Acta AG 1/2027")
    assert e.value.codigo == "CAPITALIZACION_SIN_AUTORIZACION"
    cap = seguridad.solicitar_autorizacion(con, s["jf"], "CAPITALIZACION_EXCEDENTES", "45756.49", "Capitalización",
                                           ctx_ag)
    seguridad.firmar_autorizacion(con, s["ag"], cap, documento_ref="Acta AG 1/2027", votos_favor=10,
                                  votos_emitidos=12)
    cierre.aprobar_distribucion(con, s["jf"], prop["id"], "2027-08-30", autorizacion_asamblea_id=asamblea,
                                acta_ref="Acta AG 1/2027", autorizacion_capitalizacion_id=cap)
    assert tesoreria.saldo_cuenta(con, "3.1.01.05") == D("-45756.49")
    cierre.firmar_liquidaciones(con, s["gg"], prop["id"])
    vencidas = cierre.obligaciones_vencidas(con, "2027-09-01")
    assert vencidas == []
    aut = desembolso(con, s, "100000", "2027-09-01")
    cierre.pagar_distribucion(con, s["jf"], prop["id"], "2027-09-01", "1.1.01.03", autorizacion_desembolso_id=aut)
    assert uno(con, "SELECT estado FROM distribuciones_excedentes WHERE id = ?", prop["id"]) == "PAGADA"


def test_anulacion_y_permisos(cerrado, s):
    con = cerrado
    prop = cierre.proponer_distribucion(con, s["jf"], 2026, "2027-07-10")
    assert prop["reinversion"] == D("0.00") and prop["distribuible"] == D("197675.79")
    with pytest.raises(ReglaNegocioError) as e:
        cierre.proponer_distribucion(con, s["jf"], 2026, "2027-07-11")
    assert e.value.codigo == "DISTRIBUCION_EXISTENTE"
    with pytest.raises(AutorizacionError):
        cierre.registrar_inf_cv_04(con, s["jf"], prop["id"], "2027-07-15", dictamen="FAVORABLE",
                                   reinversion_conforme=True)
    cierre.anular_distribucion(con, s["jf"], prop["id"], "Reinversión pendiente del Plan CAPEX")
    assert {l["estado"] for l in cierre.liquidaciones(con, s["jf"], prop["id"])} == {"ANULADA"}
    nueva = cierre.proponer_distribucion(con, s["jf"], 2026, "2027-07-12", criterio="PARTICIPACION",
                                         pesos={sid: 1 if i < 6 else 3 for i, sid in enumerate(
                                             r[0] for r in con.execute("SELECT id FROM socios ORDER BY numero"))})
    montos = sorted(r["monto"] for r in nueva["reparto"])
    assert sum(montos) == nueva["distribuible"]
    assert abs(montos[-1] - 3 * montos[0]) <= D("0.03")
    with pytest.raises(AutorizacionError):
        cierre.balance_general(con, s["ut"], "2027-06-30")


# ============================ EXPORTACIÓN ===================================
def test_exportacion_pdf_y_excel(cerrado, s):
    con = cerrado
    paquete = cierre.estados_financieros(con, s["jf"], 2026)
    for datos in (rep.paquete_pdf(paquete, "abc"), rep.balance_pdf(paquete["balance"]),
                  rep.resultados_pdf(paquete["resultados"]), rep.flujo_pdf(paquete["flujo"]),
                  rep.patrimonio_pdf(paquete["patrimonio"]), rep.notas_pdf(paquete["notas"], paquete["fecha_corte"]),
                  rep.fin_inf_01(cierre.lista_verificacion(con, s["jf"], periodo(con, 2026, 3)), ctx(s)),
                  rep.fin_inf_03(cierre.consolidado_costeo(con, s["jf"], periodo(con, 2026, 3)), ctx(s)),
                  rep.fin_inf_04(cierre.estado_resultados_mensual(con, s["jf"], periodo(con, 2026, 3)), ctx(s))):
        assert datos.startswith(b"%PDF") and b"P\\341gina 1 de" in datos
    wb = load_workbook(BytesIO(rep.paquete_excel(paquete)))
    assert wb.sheetnames == ["Balance General", "Estado de Resultados", "Flujo de Efectivo", "Evolución Patrimonio",
                             "Notas", "Indicadores"]
    valores = [c.value for fila in wb["Estado de Resultados"].iter_rows() for c in fila]
    assert 210293.39 in valores
    valores_efe = [c.value for fila in wb["Flujo de Efectivo"].iter_rows() for c in fila]
    assert valores_efe.count(291668.9) >= 2
    wb4 = load_workbook(BytesIO(rep.fin_inf_04_excel(cierre.estado_resultados_mensual(con, s["jf"],
                                                                                         periodo(con, 2026, 12)))))
    assert wb4.sheetnames == ["FIN-INF-04"]
    assert rep.numero(D("-5250")) == "(5.250,00)"


def test_flujo_2027_expone_pagos_de_la_gestion_anterior(cerrado, s):
    """EFE 2027 del Excel: sueldos devengados Bs 10.600, aportes Bs 1.824,26 y servicios Bs 125."""
    con = cerrado
    contable.crear_asiento(con, s["jf"], "2027-07-05", "Pago de sueldos de junio", [
        {"cuenta": "2.1.02.01", "debe": "9252.74"}, {"cuenta": "2.1.02.05", "debe": "1347.26"},
        {"cuenta": "1.1.01.03", "haber": "10600"}], "PRUEBA", tipo="EGRESO")
    contable.crear_asiento(con, s["jf"], "2027-07-05", "Pago de aportes y servicios de junio", [
        {"cuenta": "2.1.02.03", "debe": "1824.26"}, {"cuenta": "2.1.01.06", "debe": "125"},
        {"cuenta": "1.1.01.03", "haber": "1949.26"}], "PRUEBA", tipo="EGRESO")
    efe = cierre.estado_flujo_efectivo(con, s["jf"], 2027, "2027-07-31")
    lineas = {f["concepto"]: f["monto"] for f in efe["filas"]}
    assert lineas["Pago de Sueldos devengados"] == D("10600.00")
    assert lineas["Pago de Aportes patronales devengados"] == D("1824.26")
    assert lineas["Pago de Servicios basicos devengados"] == D("125.00")
    assert efe["saldo_final"] == D("279119.64") and efe["cuadra"]
