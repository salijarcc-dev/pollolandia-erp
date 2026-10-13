"""Pruebas de servicios/planillas.py (Fase 5).

Cifras de los documentos:
* Manual de Costos §6.2: salario Bs 3.300 → por ciclo Bs 7.920 (sufijo 20) +
  aguinaldo 660 (21) + patronal 1.363,03 (38) + indemnización 660 (39);
  eventual 3.000 aves × Bs 1,50 = Bs 4.500 (22); total MOD Bs 15.103,03.
* Manual Presupuestario D.4-1 (2027): con 5 % de indexación y 75 % a carne,
  salario 6.237, aguinaldo 520 y patronal 1.073 por ciclo.
* PC-09: aporte laboral 12,71 % (2.1.02.05), patronal 17,21 % (2.1.02.03),
  provisiones de 1/12 (8,33 %).
* CI-FIN-03: 30 h crianza + 10 h planta = 75 % / 25 %.
* FIN-EGR-08: DPF Bueno ≤ 100 %, Normal hasta 105 %, Malo > 105 %.
* CIR-EGR-01: 7 días de antelación; FIN-EGR-11: 15 días; PC-09: > 90 días.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from reportes import formatos_planillas as pdf
from servicios import contable, planillas, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal
BANCO = "1.1.01.03"


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def depositar(con, s, monto="100000", fecha="2026-07-02"):
    tesoreria.registrar_deposito(con, s["jf"], fecha, monto, BANCO, "5.1.01.01", "Venta de carne de pollo")


def personal_fijo(con, s, ingreso="2026-07-01"):
    enc = planillas.registrar_personal(con, s["jf"], "Encargado de Granja", "6012345", "Encargado de Granja", "GRANJA",
                                       "3300", ingreso, "INDEFINIDO", "Contrato 01/2026", polivalente=True)
    gg = planillas.registrar_personal(con, s["jf"], "Rocío Del Alba Flores Colque", "9105875", "Gerente General",
                                      "ADMINISTRACION", "3500", ingreso, "INDEFINIDO", "Contrato 02/2026")
    jf = planillas.registrar_personal(con, s["jf"], "Jefe de Finanzas", "7012345", "Jefe de Finanzas", "ADMINISTRACION",
                                      "3000", ingreso, "INDEFINIDO", "Contrato 03/2026")
    return enc, gg, jf


def planilla_contabilizada(con, s, anio=2026, mes=7, bonos=None):
    pid = planillas.preparar_planilla(con, s["jf"], anio, mes, bonos)
    planillas.autorizar_planilla(con, s["gg"], pid)
    planillas.contabilizar_planilla(con, s["jf"], pid)
    return pid


def autorizar_desembolso(con, s, monto, fecha="2026-07-31"):
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, "Pago de planilla", ctx)
    if seguridad.firmar_autorizacion(con, s["gg"], aid) == "PENDIENTE":
        seguridad.firmar_autorizacion(con, s["cv"], aid)
    return aid


# ======================= CRITERIO DEL MASTER PLAN ============================
def test_salario_3300_reproduce_el_costo_por_ciclo_del_manual_de_costos(con):
    c = planillas.costo_mod_ciclo(con, "3300", "2026-07-01")
    assert (c["salario"], c["aguinaldo"], c["aporte_patronal"], c["indemnizacion"]) == \
        (D("7920.00"), D("660.00"), D("1363.03"), D("660.00"))
    eventual = planillas.costo_eventual(con, 3000, "2026-07-01")
    assert eventual == D("4500.00")
    assert c["total"] + eventual == D("15103.03")          # total MOD por ciclo del Manual de Costos
    # 2027: 5 % de indexación y 75 % a carne (Manual Presupuestario D.4-1 2027)
    c27 = planillas.costo_mod_ciclo(con, D("3300") * D("1.05"), "2027-07-01")
    assert [round(c27[k] * D("0.75")) for k in ("salario", "aguinaldo", "aporte_patronal")] == [6237, 520, 1073]


def test_provision_doceavos_frente_al_porcentaje_redondeado():
    assert planillas.provision(D("7920"), D("0.0833"), True) == D("660.00")
    assert planillas.provision(D("7920"), D("0.0833"), False) == D("659.74")


# ============================== PLANILLA MENSUAL =============================
def test_planilla_mensual_y_asiento_de_devengo(con, s):
    enc, gg, jf = personal_fijo(con, s)
    pid = planillas.preparar_planilla(con, s["jf"], 2026, 7, bonos={gg: "200"})
    p = planillas.obtener_planilla(con, s["jf"], pid)
    fila = next(l for l in p["lineas"] if l["personal_id"] == enc)
    assert (fila["haber_basico"], fila["total_ganado"], fila["aporte_laboral"], fila["liquido"]) == \
        (D("3300.00"), D("3300.00"), D("419.43"), D("2880.57"))
    assert (fila["aporte_patronal"], fila["aguinaldo"], fila["indemnizacion"]) == (D("567.93"), D("275.00"), D("275.00"))
    assert fila["fuente_prorrateo"] == "NO_APLICA" and fila["pct_carne"] == "1"
    filag = next(l for l in p["lineas"] if l["personal_id"] == gg)
    assert (filag["total_ganado"], filag["aporte_laboral"]) == (D("3700.00"), D("470.27"))
    with pytest.raises(AutorizacionError):
        planillas.contabilizar_planilla(con, s["jf"], pid)
    with pytest.raises(AutorizacionError):
        planillas.autorizar_planilla(con, s["jf"], pid)
    planillas.autorizar_planilla(con, s["gg"], pid)
    asiento_id = planillas.contabilizar_planilla(con, s["jf"], pid)
    a = contable.obtener_asiento(con, s["jf"], asiento_id)
    assert a["fecha"] == "2026-07-31" and a["total_debe"] == a["total_haber"]
    debe = {(l["cuenta"], l["sufijo"]): l["debe"] for l in a["lineas"] if l["debe"]}
    assert debe[("6.1.01.01", "20")] == D("3300.00") and debe[("6.1.01.01", "21")] == D("275.00")
    assert debe[("6.1.01.01", "38")] == D("567.93") and debe[("6.1.01.01", "39")] == D("275.00")
    assert debe[("7.1.01.01", None)] == D("6700.00")                   # 3.700 + 3.000
    assert debe[("7.1.01.03", None)] == D("1153.07")                   # 636,77 + 516,30
    haber = {l["cuenta"]: l["haber"] for l in a["lineas"] if l["haber"]}
    assert haber["2.1.02.05"] == D("1271.00")                          # 419,43 + 470,27 + 381,30
    assert haber["2.1.02.01"] == D("8729.00")
    # El aporte laboral es retención: el costo de granja no lo suma.
    assert contable.libro_mayor(con, s["jf"], "6.1.01.01")["saldo_final"] == D("4417.93")
    with pytest.raises(ReglaNegocioError):
        planillas.preparar_planilla(con, s["jf"], 2026, 7)
    with pytest.raises(ReglaNegocioError):
        planillas.anular_planilla(con, s["jf"], pid, "Error")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE planilla_lineas SET bonos_cent = 0")


def test_mes_incompleto_y_anulacion(con, s):
    planillas.registrar_personal(con, s["jf"], "Encargado", "6012345", "Encargado de Granja", "GRANJA", "3300",
                                 "2026-07-16", "INDEFINIDO", "Contrato 01")
    pid = planillas.preparar_planilla(con, s["jf"], 2026, 7)
    linea = planillas.obtener_planilla(con, s["jf"], pid)["lineas"][0]
    assert (linea["dias_trabajados"], linea["haber_basico"]) == (16, D("1760.00"))
    planillas.anular_planilla(con, s["jf"], pid, "Faltó un bono")
    assert planillas.preparar_planilla(con, s["jf"], 2026, 7) != pid
    with pytest.raises(ReglaNegocioError):
        planillas.preparar_planilla(con, s["jf"], 2026, 6)


def test_pago_de_sueldos_y_aportes(con, s):
    depositar(con, s)
    personal_fijo(con, s)
    pid = planilla_contabilizada(con, s)
    p = planillas.obtener_planilla(con, s["jf"], pid)
    with pytest.raises(AutorizacionError):
        planillas.pagar_sueldos(con, s["jf"], pid, "2026-07-31", BANCO)
    planillas.pagar_sueldos(con, s["jf"], pid, "2026-07-31", BANCO,
                            autorizacion_id=autorizar_desembolso(con, s, p["liquido"]))
    total_aportes = p["aporte_laboral"] + p["aporte_patronal"]
    mid = planillas.pagar_aportes(con, s["jf"], pid, "2026-08-14", BANCO,
                                  autorizacion_id=autorizar_desembolso(con, s, total_aportes, "2026-08-14"))
    assert tesoreria.saldo_cuenta(con, "2.1.02.01") == D("0.00")
    assert tesoreria.saldo_cuenta(con, "2.1.02.05") == D("0.00") and tesoreria.saldo_cuenta(con, "2.1.02.03") == D("0.00")
    movimiento = next(m for m in tesoreria.movimientos_banco(con, s["jf"]) if m["id"] == mid)
    assert movimiento["monto"] == total_aportes and movimiento["categoria_pago"] == "PLANILLA_APORTES"
    with pytest.raises(ReglaNegocioError):
        planillas.pagar_aportes(con, s["jf"], pid, "2026-08-14", BANCO)


def test_alertas_de_pago_legal(con, s):
    personal_fijo(con, s)
    pid = planilla_contabilizada(con, s)
    venc = planillas.vencimientos_planilla(con, 2026, 7)
    assert venc == {"sueldos": date(2026, 7, 31), "aportes": date(2026, 8, 31)}
    alertas = {a["tipo"]: a for a in planillas.alertas_pago(con, "2026-07-28")}
    assert alertas["SUELDOS"]["estado"] == "POR_VENCER" and alertas["APORTES"]["estado"] == "AL_DIA"
    alertas = {a["tipo"]: a for a in planillas.alertas_pago(con, "2026-08-01")}
    assert alertas["SUELDOS"]["estado"] == "VENCIDO" and alertas["AGUINALDO"]["vencimiento"] == date(2026, 12, 20)
    assert planillas.ultimo_dia_habil(2026, 10) == date(2026, 10, 30)    # 31-oct-2026 es sábado
    assert pid


# =========================== PRORRATEO Y CONCILIACIÓN ========================
def test_prorrateo_75_25_desde_2027_con_hojas_de_tiempo(con, s):
    enc, _, _ = personal_fijo(con, s)
    # 2026: todo a carne aunque haya hojas.
    planillas.registrar_hoja_tiempo(con, s["ut"], enc, "2026-07-06", 6, 2, True)
    p26 = planillas.calcular_planilla(con, 2026, 7)
    assert next(l for l in p26["lineas"] if l["personal_id"] == enc)["fuente"] == "NO_APLICA"
    # Julio 2027: semana del ejemplo CI-FIN-03 (30 h crianza, 10 h planta) → 75 %.
    for dia, (hc, ha) in zip(range(5, 10), [(6, 2)] * 5):
        planillas.registrar_hoja_tiempo(con, s["ut"], enc, f"2027-07-{dia:02d}", hc, ha, True)
    sin_firma = planillas.registrar_hoja_tiempo(con, s["ut"], enc, "2027-07-12", 0, 8, False)
    linea = next(l for l in planillas.calcular_planilla(con, 2027, 7)["lineas"] if l["personal_id"] == enc)
    assert (linea["fuente"], linea["pct_carne"], linea["sin_regularizar"]) == ("HOJAS_TIEMPO", D("0.7500"), 1)
    planillas.regularizar_hoja(con, s["ut"], sin_firma, "Omisión del 12/07 estimada con el trabajador el 13/07")
    linea = next(l for l in planillas.calcular_planilla(con, 2027, 7)["lineas"] if l["personal_id"] == enc)
    assert linea["pct_carne"] == D("0.6250")                         # 30 / 48
    # Agosto 2027 sin hojas: 75 % por defecto (POL-06).
    linea = next(l for l in planillas.calcular_planilla(con, 2027, 8)["lineas"] if l["personal_id"] == enc)
    assert (linea["fuente"], linea["pct_carne"]) == ("DEFECTO", D("0.75"))
    with pytest.raises(ReglaNegocioError):          # el administrativo no es polivalente
        planillas.registrar_hoja_tiempo(con, s["ut"], _, "2027-07-05", 8, 0, True)
    with pytest.raises(DatosInvalidosError):
        planillas.registrar_hoja_tiempo(con, s["ut"], enc, "2027-07-05", 8, 0, True)


def test_conciliacion_pagado_vs_absorbido_fin_egr_13(con, s):
    enc, _, _ = personal_fijo(con, s)
    planillas.actualizar_salario(con, s["jf"], enc, "2027-07-01", D("3465"), "Indexación 5 %")
    for dia in range(5, 10):
        planillas.registrar_hoja_tiempo(con, s["ut"], enc, f"2027-07-{dia:02d}", 6, 2, True)
    pid = planilla_contabilizada(con, s, 2027, 7)
    conc = planillas.conciliar_mano_obra(con, s["jf"], pid, "Sin observaciones en la muestra semestral")
    # 3.465 + 288,75 + 596,33 + 288,75 = 4.638,83 → 75 % carne y 25 % alimento
    assert conc["pagado"] == D("4638.83") and conc["diferencia"] == D("0.00")
    assert (conc["carne"], conc["alimento"]) == (D("3479.12"), D("1159.71"))
    assert contable.libro_mayor(con, s["jf"], "6.1.01.02", sufijo="20")["saldo_final"] == D("866.25")
    with pytest.raises(AutorizacionError):
        planillas.fiscalizar_conciliacion(con, s["jf"], conc["id"])
    planillas.fiscalizar_conciliacion(con, s["cv"], conc["id"])
    with pytest.raises(ReglaNegocioError):
        planillas.conciliar_mano_obra(con, s["jf"], pid)


def test_verificacion_cruzada_de_horas_ci_fin_14(con, s):
    enc, _, _ = personal_fijo(con, s)
    # 6 semanas con horas de planta; la semana del 2-ago tiene 30 h de planta.
    for lunes in ("2027-07-05", "2027-07-12", "2027-07-19", "2027-07-26", "2027-08-02", "2027-08-09"):
        base = date.fromisoformat(lunes)
        for k in range(5):
            alimento = 6 if lunes == "2027-08-02" else 2
            planillas.registrar_hoja_tiempo(con, s["ut"], enc, base.replace(day=base.day + k), 8 - alimento, alimento,
                                            True)
    semanas = planillas.semanas_con_horas(con, "2027-07-01", "2027-08-31")
    assert len(semanas) == 6 and semanas[0]["semana_inicio"] == "2027-08-02"   # prioridad: mayor % a alimento
    with pytest.raises(ReglaNegocioError) as e:          # 20 % de 6 semanas = 2 semanas como mínimo
        planillas.registrar_verificacion_horas(con, s["cv"], "2027-07-01", "2027-08-31", "0.15", "0.35",
                                               "40 qq por semana (PO-03)", {"2027-08-02": 40})
    assert e.value.codigo == "MUESTRA_INSUFICIENTE"
    r = planillas.registrar_verificacion_horas(con, s["cv"], "2027-07-01", "2027-08-31", "0.15", "0.35",
                                               "Ensacado de 40 qq por semana (PO-03)", {"2027-08-02": 40, "2027-07-12": 40})
    assert r["requeridas"] == 2 and r["observadas"] == ["2027-08-02"]             # 30 h / 40 qq = 0,75 h/qq
    with pytest.raises(AutorizacionError):
        planillas.registrar_verificacion_horas(con, s["jf"], "2027-07-01", "2027-08-31", "0.15", "0.35", "x",
                                               {"2027-08-02": 40, "2027-07-12": 40})


# ============================== PERSONAL EVENTUAL ============================
def circular(con, s, emision="2026-09-01", faenado="2026-09-09", aves=3000):
    cid = planillas.emitir_circular(con, s["ut"], emision, faenado, "L-2026-01", aves, "2.8")
    planillas.acusar_circular(con, s["gg"], cid)
    planillas.acusar_circular(con, s["jf"], cid)
    return cid


def test_eventual_de_3000_aves_cuesta_4500_con_dpf_bueno(con, s):
    depositar(con, s)
    with pytest.raises(ReglaNegocioError) as e:
        planillas.emitir_circular(con, s["ut"], "2026-09-03", "2026-09-09", "L-2026-01", 3000, "2.8")
    assert e.value.codigo == "CIRCULAR_SIN_ANTELACION"
    with pytest.raises(AutorizacionError):
        planillas.emitir_circular(con, s["jf"], "2026-09-01", "2026-09-09", "L-2026-01", 3000, "2.8")
    cid = planillas.emitir_circular(con, s["ut"], "2026-09-01", "2026-09-09", "L-2026-01", 3000, "2.8")
    with pytest.raises(ReglaNegocioError) as e:
        planillas.solicitar_contratacion(con, s["jf"], cid, "2026-09-07", 5)
    assert e.value.codigo == "CIRCULAR_SIN_ACUSE"
    planillas.acusar_circular(con, s["gg"], cid)
    planillas.acusar_circular(con, s["jf"], cid)
    with pytest.raises(ReglaNegocioError) as e:
        planillas.solicitar_contratacion(con, s["jf"], cid, "2026-09-08", 5)
    assert e.value.codigo == "EVENTUAL_DIA_NO_PERMITIDO"
    sol = planillas.solicitar_contratacion(con, s["jf"], cid, "2026-09-07", 5)
    assert sol["presupuesto"] == D("4500.00") and not sol["suspendida"]
    with pytest.raises(ReglaNegocioError) as e:          # martes: no se autoriza
        planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-08")
    assert e.value.codigo == "EVENTUAL_DIA_NO_PERMITIDO"
    with pytest.raises(AutorizacionError):
        planillas.registrar_contrato(con, s["jf"], sol["id"], "Jornalero 1", "800001", "2026-09-07")
    assert planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-07") == "AUTORIZADA"
    contratos = [planillas.registrar_contrato(con, s["jf"], sol["id"], f"Jornalero {n}", f"80000{n}", "2026-09-07")
                 for n in range(1, 6)]
    with pytest.raises(ReglaNegocioError):
        planillas.registrar_contrato(con, s["jf"], sol["id"], "Sexto", "800006", "2026-09-07")
    liq = planillas.liquidar_jornales(con, s["jf"], sol["id"], "2026-09-10", {c: 600 for c in contratos})
    assert (liq["total"], liq["dpf"], liq["semaforo"]) == (D("4500.00"), D("100.00"), "BUENO")
    assert contable.libro_mayor(con, s["jf"], "6.1.01.01", sufijo="22")["saldo_final"] == D("4500.00")
    with pytest.raises(AutorizacionError):
        planillas.pagar_jornales(con, s["jf"], liq["id"], "2026-09-10", BANCO)
    planillas.autorizar_pago_jornales(con, s["gg"], liq["id"])
    planillas.pagar_jornales(con, s["jf"], liq["id"], "2026-09-10", BANCO,
                             autorizacion_id=autorizar_desembolso(con, s, "4500", "2026-09-10"))
    assert tesoreria.saldo_cuenta(con, "2.1.02.04") == D("0.00")
    assert uno(con, "SELECT estado FROM compromisos WHERE id = ?", sol["compromiso_id"]) == "PAGADO"
    # Partida del ciclo agotada (Bs 4.500 por ciclo): otra contratación no procede.
    otra = circular(con, s, "2026-09-01", "2026-09-10", 1000)
    with pytest.raises(ReglaNegocioError) as e:
        planillas.solicitar_contratacion(con, s["jf"], otra, "2026-09-07", 2)
    assert e.value.codigo == "PRESUPUESTO_INSUFICIENTE"


def test_semaforo_dpf_y_justificacion(con, s):
    assert [planillas.semaforo_dpf(con, "2026-09-07", D(x)) for x in ("100", "100.01", "105", "105.01")] == \
        ["BUENO", "NORMAL", "NORMAL", "MALO"]
    depositar(con, s)
    sol = planillas.solicitar_contratacion(con, s["jf"], circular(con, s), "2026-09-07", 2)
    planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-07")
    a = planillas.registrar_contrato(con, s["jf"], sol["id"], "Jornalero A", "900001", "2026-09-07")
    b = planillas.registrar_contrato(con, s["jf"], sol["id"], "Jornalero B", "900002", "2026-09-07")
    liq = planillas.liquidar_jornales(con, s["jf"], sol["id"], "2026-09-10", {a: 1600, b: 1551})   # 3.151 aves
    assert (liq["total"], liq["dpf"], liq["semaforo"]) == (D("4726.50"), D("105.03"), "MALO")
    with pytest.raises(ReglaNegocioError) as e:
        planillas.autorizar_pago_jornales(con, s["gg"], liq["id"])
    assert e.value.codigo == "DPF_SIN_JUSTIFICACION"
    planillas.autorizar_pago_jornales(con, s["gg"], liq["id"], "151 aves de un lote adicional del galpón 2")


def test_suplencia_y_compromiso_suspendido(con, s):
    # Sin depósito, el saldo (Bs 138.010) queda bajo el 120 % del SME: el compromiso se suspende.
    sol = planillas.solicitar_contratacion(con, s["jf"], circular(con, s), "2026-09-07", 5)
    assert sol["suspendida"]
    with pytest.raises(AutorizacionError) as e:
        planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-07")
    assert e.value.codigo == "COMPROMISO_SUSPENDIDO"
    aut = uno(con, "SELECT autorizacion_id FROM compromisos WHERE id = ?", sol["compromiso_id"])
    seguridad.firmar_autorizacion(con, s["gg"], aut, documento_ref="Resolución GG 07/2026")
    tesoreria.confirmar_compromiso(con, s["jf"], sol["compromiso_id"])
    # Ausencia de la GG el lunes: suple el Presidente del CA con constancia (CI-FIN-17).
    with pytest.raises(DatosInvalidosError):
        planillas.autorizar_contratacion(con, s["ca_presidente"], sol["id"], "2026-09-07",
                                         seguridad.Suplencia("", ""))
    estado = planillas.autorizar_contratacion(con, s["ca_presidente"], sol["id"], "2026-09-07",
                                              seguridad.Suplencia("GG en capacitación en La Paz", "Constancia 03/2026"))
    assert estado == "AUTORIZADA" and uno(con, "SELECT por_suplencia FROM autorizaciones_eventuales WHERE id = ?",
                                          sol["id"]) == 1


# ============================ LIQUIDACIÓN FINAL ==============================
def test_liquidacion_final_dentro_de_15_dias(con, s):
    enc, _, _ = personal_fijo(con, s)
    for mes in (7, 8, 9):
        planilla_contabilizada(con, s, 2026, mes)
    with pytest.raises(ReglaNegocioError):
        planillas.preparar_liquidacion_final(con, s["jf"], enc, "2026-10-20", 5)
    planillas.registrar_retiro(con, s["jf"], enc, "2026-10-15", "Renuncia voluntaria")
    calculo = planillas.calcular_liquidacion_final(con, enc, 5)
    # 105 días comerciales (1-jul a 15-oct); promedio Bs 3.300
    assert (calculo["dias_servicio"], calculo["promedio"]) == (105, D("3300.00"))
    assert (calculo["indemnizacion"], calculo["aguinaldo"], calculo["vacaciones"]) == \
        (D("962.50"), D("962.50"), D("550.00"))
    assert (calculo["salario_dias"], calculo["salario"], calculo["aporte_laboral"], calculo["total"]) == \
        (15, D("1650.00"), D("209.72"), D("3915.28"))
    with pytest.raises(ReglaNegocioError) as e:
        planillas.preparar_liquidacion_final(con, s["jf"], enc, "2026-10-31", 5)
    assert e.value.codigo == "LIQUIDACION_FUERA_DE_PLAZO"
    lid = planillas.preparar_liquidacion_final(con, s["jf"], enc, "2026-10-30", 5)      # último día del plazo
    assert uno(con, "SELECT dentro_de_plazo FROM liquidaciones_finales WHERE id = ?", lid) == 1
    with pytest.raises(AutorizacionError):
        planillas.contabilizar_liquidacion_final(con, s["jf"], lid, "2026-10-30")
    planillas.autorizar_liquidacion_final(con, s["gg"], lid)
    asiento = contable.obtener_asiento(con, s["jf"], planillas.contabilizar_liquidacion_final(con, s["jf"], lid,
                                                                                               "2026-10-30"))
    debe = {(l["cuenta"], l["sufijo"]): l["debe"] for l in asiento["lineas"] if l["debe"]}
    assert debe[("2.2.01.01", None)] == D("825.00") and debe[("6.1.01.01", "39")] == D("137.50")
    assert debe[("2.1.02.02", None)] == D("825.00") and debe[("6.1.01.01", "21")] == D("137.50")
    assert debe[("6.1.01.01", "20")] == D("2200.00")
    # Queda la provisión de la GG (3 × 291,67) y del JF (3 × 250).
    assert tesoreria.saldo_cuenta(con, "2.2.01.01") == D("-1625.01")


def test_liquidacion_final_requiere_mas_de_90_dias_y_causa_si_hay_retraso(con, s):
    enc, gg, _ = personal_fijo(con, s)
    planillas.registrar_retiro(con, s["jf"], enc, "2026-09-15", "Fin de contrato")
    with pytest.raises(ReglaNegocioError) as e:
        planillas.calcular_liquidacion_final(con, enc, 0)
    assert e.value.codigo == "NO_CORRESPONDE_LIQUIDACION"
    planillas.registrar_retiro(con, s["jf"], gg, "2026-10-15", "Renuncia")
    alertas = {a["tipo"]: a for a in planillas.alertas_pago(con, "2026-11-01")}
    assert alertas["LIQUIDACION_FINAL"]["estado"] == "VENCIDO"
    lid = planillas.preparar_liquidacion_final(con, s["jf"], gg, "2026-11-05", 0, "Demora en el finiquito firmado")
    assert uno(con, "SELECT dentro_de_plazo FROM liquidaciones_finales WHERE id = ?", lid) == 0


# ================================== PDFs =====================================
def test_formatos_pdf(con, s):
    depositar(con, s)
    personal_fijo(con, s)
    pid = planilla_contabilizada(con, s)
    sol = planillas.solicitar_contratacion(con, s["jf"], circular(con, s), "2026-09-07", 1)
    planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-07")
    k = planillas.registrar_contrato(con, s["jf"], sol["id"], "Jornalero 1", "800001", "2026-09-07")
    liq = planillas.liquidar_jornales(con, s["jf"], sol["id"], "2026-09-10", {k: 3000})
    ctx = pdf.Contexto(date(2026, 7, 31), "Jefe de Finanzas", "Jefe de Finanzas y Comercialización", "Ciclo 1")
    planilla = planillas.obtener_planilla(con, s["jf"], pid)
    ev = planillas.obtener_eventual(con, s["jf"], sol["id"])
    documentos = {
        "FIN-EGR-09": pdf.fin_egr_09(planilla, ctx), "FIN-EGR-10": pdf.fin_egr_10(planilla, ctx),
        "CIR-EGR-01": pdf.cir_egr_01(ev["circular"], ctx), "FIN-EGR-06": pdf.fin_egr_06(ev, ctx),
        "CONT-EGR-01": pdf.cont_egr_01(ev, ev["contratos"][0], ctx),
        "FIN-EGR-08": pdf.fin_egr_08(planillas.obtener_liquidacion_eventual(con, s["jf"], liq["id"]), ctx),
    }
    for codigo, contenido in documentos.items():
        assert contenido.startswith(b"%PDF") and codigo.encode() in contenido
