"""Pruebas de servicios/tesoreria.py y reportes/formatos_tesoreria.py (Fase 3).

Cifras de los documentos:
* POA §3.2-3.3: saldo al 30-sep-2026 Bs 116.825, egresos de octubre Bs 47.769,
  cobro del ciclo Bs 184.800 el día 73 (23-nov), saldo mínimo diario
  Bs 59.527 el 22-nov-2026; saldo al 30-jun-2027 Bs 291.669 y pago de
  excedentes de Bs 145.756 en el ciclo 1 de 2027 con SME 2027 de Bs 145.912.
* SME 2026 Bs 136.413; 120 % = Bs 163.695,60; 150 % = Bs 204.619,50.
* CI-FIN-01: fondo Bs 5.000, gasto menor máx. Bs 500, ejemplo de cobro de
  Bs 3.200 con depósito del excedente.
* CI-FIN-07: conciliación en 5 días hábiles (feriado del 6 de agosto).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from reportes import formatos_tesoreria as pdf
from servicios import contable, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.tesoreria import LineaFlujo, PagoPendiente, PartidaConciliacion

BANCO = "1.1.01.03"
AHORRO = "1.1.01.04"
D = Decimal


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def aut_desembolso(con, s, monto, fecha="2026-07-02"):
    """Autorización DESEMBOLSO firmada: GG y, si supera Bs 5.000, un miembro del CV."""
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, "Desembolso", ctx)
    estado = seguridad.firmar_autorizacion(con, s["gg"], aid)
    if estado == "PENDIENTE":
        estado = seguridad.firmar_autorizacion(con, s["cv"], aid)
    assert estado == "AUTORIZADA"
    return aid


def fondo(con, s, fecha="2026-07-02"):
    aid = aut_desembolso(con, s, "5000", fecha)
    return tesoreria.constituir_fondo(con, s["jf"], fecha, "Jefe de Finanzas", "Resolución GG 01/2026",
                                      autorizacion_id=aid)


def depositar_ventas(con, s, monto, fecha):
    return tesoreria.registrar_deposito(con, s["jf"], fecha, monto, BANCO, "5.1.01.01", "Venta de carne de pollo")


# ================================ CAJA ======================================
def test_constituir_fondo_exige_autorizacion(con, s):
    with pytest.raises(AutorizacionError) as e:
        tesoreria.constituir_fondo(con, s["jf"], "2026-07-02", "Custodio", "Res. 01")
    assert e.value.codigo == "DESEMBOLSO_SIN_AUTORIZACION"
    fondo(con, s)
    assert tesoreria.saldo_cuenta(con, "1.1.01.05") == D("5000.00")
    assert tesoreria.saldo_bancario(con) == D("133010.00")
    assert uno(con, "SELECT COUNT(*) FROM autorizaciones WHERE estado='UTILIZADA'") == 1
    with pytest.raises(ReglaNegocioError) as e:
        fondo(con, s)
    assert e.value.codigo == "FONDO_YA_CONSTITUIDO"


def test_gasto_menor_tope_y_comprobante(con, s):
    fondo(con, s)
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-03", "500.00", "7.1.02.01", "GM-001", "Librería Caranavi",
                                    "Material de escritorio")
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-03", "500.01", "7.1.02.01", "GM-002", "X", "Y")
    assert e.value.codigo == "GASTO_MENOR_EXCEDE" and "PR-FIN-03" in str(e.value)
    with pytest.raises(DatosInvalidosError) as e:
        tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-03", "20", "7.1.02.01", "GM-001", "X", "Duplicado")
    assert e.value.codigo == "COMPROBANTE_DUPLICADO"
    with pytest.raises(DatosInvalidosError):
        tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-03", "20", "7.1.02.01", " ", "X", "Sin comprobante")
    with pytest.raises(ReglaNegocioError):
        tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-03", "20", BANCO, "GM-003", "X", "Disponible")
    with pytest.raises(AutorizacionError):
        tesoreria.registrar_gasto_menor(con, s["gg"], "2026-07-03", "20", "7.1.02.01", "GM-004", "X", "La GG no registra")
    assert tesoreria.saldo_cuenta(con, "1.1.01.05") == D("4500.00")


def test_gasto_menor_a_centro_de_costo_con_sufijo(con, s):
    fondo(con, s)
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-03", "180", "6.1.01.01", "GM-010", "Agroveterinaria",
                                    "Desinfectante de galpón", sufijo="09")
    assert contable.libro_mayor(con, s["jf"], "6.1.01.01", sufijo="09")["saldo_final"] == D("180.00")


def test_fondo_insuficiente(con, s):
    fondo(con, s)
    for n in range(10):
        tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-04", "500", "7.1.02.03", f"GM-{n:03d}", "Proveedor",
                                        "Mantenimiento")
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-04", "1", "7.1.02.03", "GM-999", "Proveedor", "Extra")
    assert e.value.codigo == "FONDO_INSUFICIENTE"


def test_ejemplo_ci_fin_01_deposito_del_excedente(con, s):
    fondo(con, s)
    tesoreria.registrar_ingreso_efectivo(con, s["jf"], "2026-07-06", "3200", "5.1.01.01", "NE-0001",
                                         "Venta directa de pollo")
    control = tesoreria.control_custodia(con, "2026-07-06")
    assert (control["total_custodia"], control["excedente"], control["deposito_obligatorio"]) == \
        (D("8200.00"), D("3200.00"), True)
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.depositar_excedente(con, s["jf"], "2026-07-06", "3000", "BOL-001")
    assert e.value.codigo == "DEPOSITO_INSUFICIENTE"
    tesoreria.depositar_excedente(con, s["jf"], "2026-07-06", "3200", "BOL-001")
    control = tesoreria.control_custodia(con, "2026-07-06")
    assert control["total_custodia"] == D("5000.00") and not control["deposito_obligatorio"]
    assert tesoreria.saldo_bancario(con) == D("136210.00")
    assert tesoreria.dias_con_exceso(con, "2026-07-01", "2026-07-31") == []


def test_dia_sin_deposito_queda_registrado_como_exceso(con, s):
    fondo(con, s)
    tesoreria.registrar_ingreso_efectivo(con, s["jf"], "2026-07-06", "3200", "5.1.01.01", "NE-0001", "Venta")
    tesoreria.depositar_excedente(con, s["jf"], "2026-07-07", "3200", "BOL-002")
    excesos = tesoreria.dias_con_exceso(con, "2026-07-01", "2026-07-31")
    assert [x["fecha"] for x in excesos] == ["2026-07-06"] and excesos[0]["excedente"] == D("3200.00")


def test_reposicion_validada_por_la_gg(con, s):
    fondo(con, s)
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-08", "300", "7.1.02.01", "GM-101", "Librería", "Útiles")
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-08", "200", "7.1.02.02", "GM-102", "Entel", "Crédito")
    rid = tesoreria.solicitar_reposicion(con, s["jf"], "2026-07-09")
    assert uno(con, "SELECT total_cent FROM reposiciones_caja WHERE id=?", rid) == 50_000
    with pytest.raises(AutorizacionError):
        tesoreria.ejecutar_reposicion(con, s["jf"], rid, "2026-07-09")
    with pytest.raises(AutorizacionError) as e:
        tesoreria.validar_reposicion(con, s["jf"], rid)
    assert e.value.codigo == "SEGREGACION_FUNCIONES"
    assert tesoreria.validar_reposicion(con, s["gg"], rid) == "VALIDADA"
    tesoreria.ejecutar_reposicion(con, s["jf"], rid, "2026-07-09")
    assert tesoreria.saldo_cuenta(con, "1.1.01.05") == D("5000.00")
    assert tesoreria.gastos_pendientes_reposicion(con) == []
    with pytest.raises(ReglaNegocioError):
        tesoreria.solicitar_reposicion(con, s["jf"], "2026-07-10")


def test_reposicion_rechazada_libera_los_gastos(con, s):
    fondo(con, s)
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-08", "300", "7.1.02.01", "GM-201", "Librería", "Útiles")
    rid = tesoreria.solicitar_reposicion(con, s["jf"], "2026-07-09")
    with pytest.raises(DatosInvalidosError):
        tesoreria.validar_reposicion(con, s["gg"], rid, aprobar=False)
    assert tesoreria.validar_reposicion(con, s["gg"], rid, aprobar=False, observacion="Falta firma del proveedor") \
        == "RECHAZADA"
    rid2 = tesoreria.solicitar_reposicion(con, s["jf"], "2026-07-10")
    assert rid2 != rid and uno(con, "SELECT reposicion_id FROM caja_movimientos WHERE comprobante='GM-201'") == rid2


def test_arqueo_sorpresivo_inf_cv_03(con, s):
    fondo(con, s)
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-08", "300", "7.1.02.01", "GM-301", "Librería", "Útiles")
    with pytest.raises(AutorizacionError):
        tesoreria.registrar_arqueo(con, s["jf"], "2026-07-10", "4700", "300", "Custodio")
    a = tesoreria.registrar_arqueo(con, s["cv"], "2026-07-10", "4700", "300", "Jefe de Finanzas")
    assert (a["total_fisico"], a["saldo_contable"], a["diferencia"]) == (D("5000.00"), D("5000.00"), D("0.00"))
    assert a["limite_diario_cumplido"]
    tesoreria.registrar_ingreso_efectivo(con, s["jf"], "2026-07-13", "3200", "5.1.01.01", "NE-0002", "Venta")
    b = tesoreria.registrar_arqueo(con, s["cv2"], "2026-07-14", "7850", "300", "Jefe de Finanzas", "Faltan Bs 50")
    assert b["diferencia"] == D("-50.00") and not b["limite_diario_cumplido"]
    assert [d["fecha"] for d in b["dias_con_exceso"]] == ["2026-07-13"]
    assert tesoreria.arqueos_del_ejercicio(con, 2026) == {"ejercicio": 2026, "realizados": 2, "minimo": 2, "cumple": True}
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE arqueos_caja SET diferencia_cent = 0")
    con.rollback()


# ================================ BANCOS ====================================
def test_pagos_con_matriz_ci_fin_09(con, s):
    tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "1000.00", BANCO, "7.1.04.01", "Certificación SENASAG",
                             "SENASAG")
    with pytest.raises(AutorizacionError) as e:
        tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "1000.01", BANCO, "7.1.04.01", "Pago", "SENASAG")
    assert e.value.codigo == "DESEMBOLSO_SIN_AUTORIZACION"
    ctx = seguridad.contexto_desde_bd(con, "2026-07-03")
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", "6000", "Pago a proveedor", ctx)
    seguridad.firmar_autorizacion(con, s["gg"], aid)
    with pytest.raises(AutorizacionError) as e:
        tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "6000", BANCO, "7.1.02.03", "Mantenimiento", "Taller",
                                 autorizacion_id=aid)
    assert e.value.codigo == "FIRMA_MANCOMUNADA_INCOMPLETA"
    seguridad.firmar_autorizacion(con, s["cv"], aid)
    mid = tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "6000", BANCO, "7.1.02.03", "Mantenimiento",
                                   "Taller", autorizacion_id=aid, nro_cheque="000123")
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id=?", aid) == "UTILIZADA"
    assert uno(con, "SELECT nro_cheque FROM movimientos_bancarios WHERE id=?", mid) == "000123"
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "6000", BANCO, "7.1.02.03", "Otra vez", "Taller",
                                 autorizacion_id=aid)
    assert e.value.codigo == "AUTORIZACION_YA_UTILIZADA"
    assert tesoreria.saldo_bancario(con) == D("131010.00")


def test_pago_sin_saldo_y_excedentes_por_su_via(con, s):
    aid = aut_desembolso(con, s, "140000", "2026-07-03")
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "140000", BANCO, "1.2.01.05", "Maquinaria",
                                 "Proveedor", autorizacion_id=aid)
    assert e.value.codigo == "SALDO_INSUFICIENTE"
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.registrar_pago(con, s["jf"], "2026-07-03", "100", BANCO, "2.1.05.01", "Excedentes", "Socios")
    assert e.value.codigo == "USAR_PAGO_EXCEDENTES"


def test_deposito_transferencia_y_cuenta_bancaria(con, s):
    tesoreria.registrar_cuenta_bancaria(con, s["jf"], BANCO, "Banco Unión", "1-0000123")
    with pytest.raises(DatosInvalidosError):
        tesoreria.registrar_cuenta_bancaria(con, s["jf"], BANCO, "Otro", "2")
    depositar_ventas(con, s, "18330", "2026-07-10")
    tesoreria.registrar_transferencia(con, s["jf"], "2026-07-11", "10000", BANCO, AHORRO, "Traspaso a ahorros")
    assert (tesoreria.saldo_cuenta(con, BANCO), tesoreria.saldo_cuenta(con, AHORRO)) == (D("146340.00"), D("10000.00"))
    assert tesoreria.saldo_bancario(con) == D("156340.00")
    with pytest.raises(AutorizacionError) as e:
        tesoreria.registrar_deposito(con, s["cv"], "2026-07-10", "1", BANCO, "5.1.01.01", "El CV no registra")
    assert e.value.codigo == "SEGREGACION_FUNCIONES"
    assert len(tesoreria.movimientos_banco(con, s["cv"], BANCO)) == 2


# ============================ COMPROMISOS Y SME =============================
def test_validar_compromiso_umbral_120_por_ciento(con, s):
    e = tesoreria.validar_compromiso(con, "1000", "COMPRA", "2026-07-05")
    assert (e.saldo_bancario, e.sme, e.umbral) == (D("138010.00"), D("136413.00"), D("163695.60"))
    assert e.suspendido and "SUSPENDIDO" in e.mensaje
    depositar_ventas(con, s, "30000", "2026-07-05")                       # saldo 168.010
    justo = tesoreria.validar_compromiso(con, "4314.40", "OTRO", "2026-07-05")
    assert justo.saldo_proyectado == D("163695.60") and not justo.suspendido
    assert tesoreria.validar_compromiso(con, "4314.41", "PERSONAL_EVENTUAL", "2026-07-05").suspendido
    with pytest.raises(DatosInvalidosError):
        tesoreria.validar_compromiso(con, "1", "VIAJE", "2026-07-05")


def test_compromiso_suspendido_hasta_la_firma_escrita_de_la_gg(con, s):
    cid, ev, aid = tesoreria.registrar_compromiso(con, s["jf"], "2500", "COMPRA", "Alimento de emergencia",
                                                  "2026-07-05")
    assert ev.suspendido and aid is not None
    assert uno(con, "SELECT estado FROM compromisos WHERE id=?", cid) == "SUSPENDIDO"
    with pytest.raises(AutorizacionError) as e:
        tesoreria.registrar_pago(con, s["jf"], "2026-07-06", "2500", BANCO, "1.1.03.02", "Alimento", "Proveedor",
                                 compromiso_id=cid)
    assert e.value.codigo == "COMPROMISO_SUSPENDIDO"
    with pytest.raises(DatosInvalidosError):                               # exige constancia escrita
        seguridad.firmar_autorizacion(con, s["gg"], aid)
    seguridad.firmar_autorizacion(con, s["gg"], aid, documento_ref="Nota GG-021/2026")
    assert tesoreria.confirmar_compromiso(con, s["jf"], cid) == "AUTORIZADO"
    pago = aut_desembolso(con, s, "2500", "2026-07-06")
    tesoreria.registrar_pago(con, s["jf"], "2026-07-06", "2500", BANCO, "1.1.03.02", "Alimento", "Proveedor",
                             compromiso_id=cid, autorizacion_id=pago)
    assert uno(con, "SELECT estado FROM compromisos WHERE id=?", cid) == "PAGADO"


def test_compromisos_pendientes_reducen_la_proyeccion(con, s):
    depositar_ventas(con, s, "60000", "2026-07-05")                       # saldo 198.010
    c1, ev1, _ = tesoreria.registrar_compromiso(con, s["jf"], "20000", "COMPRA", "Insumos C2", "2026-07-05")
    assert not ev1.suspendido
    ev2 = tesoreria.validar_compromiso(con, "20000", "COMPRA", "2026-07-05")
    assert ev2.compromisos_previos == D("20000.00") and ev2.saldo_proyectado == D("158010.00") and ev2.suspendido
    tesoreria.anular_compromiso(con, s["jf"], c1, "Compra cancelada")
    assert not tesoreria.validar_compromiso(con, "20000", "COMPRA", "2026-07-05").suspendido


# ============================ JERARQUÍA DE PAGOS ============================
def test_jerarquia_de_7_niveles(con, s):
    lote = [
        PagoPendiente("E-1", "Excedentes 2026", "EXCEDENTES_SOCIOS", D("145756")),
        PagoPendiente("K-1", "Maquinaria de alimento", "CAPEX_APROBADO", D("97000")),
        PagoPendiente("C-1", "SENASAG", "CERTIFICACIONES", D("3000")),
        PagoPendiente("A-1", "Alimento C2", "ALIMENTO_VETERINARIOS_CICLO", D("30000")),
        PagoPendiente("R-1", "RAU anual", "OBLIGACIONES_RAU", D("24.24")),
        PagoPendiente("P-1", "Pollitos BB C3", "POLLITOS_INSUMOS_NUEVO_CICLO", D("27000")),
        PagoPendiente("S-1", "Planilla de octubre", "PLANILLA_APORTES", D("10600")),
    ]
    plan = tesoreria.ordenar_pagos(con, lote, "2026-10-31", saldo_disponible="120000")
    assert [p["nivel"] for p in plan["pagos"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [p["referencia"] for p in plan["pagos"] if p["pagable"]] == ["S-1", "P-1", "A-1", "R-1", "C-1"]
    assert plan["total_pagable"] == D("70624.24") and plan["saldo_final"] == D("49375.76")
    capex, excedentes = plan["pagos"][5], plan["pagos"][6]
    assert "insuficiente" in capex["motivo"] and "mayor prioridad" in excedentes["motivo"]
    with pytest.raises(DatosInvalidosError):
        tesoreria.ordenar_pagos(con, [PagoPendiente("X", "x", "VIAJES", D("1"))], "2026-10-31", "1000")


def test_jerarquia_bloquea_excedentes_bajo_sme(con):
    lote = [PagoPendiente("E-1", "Excedentes 2026", "EXCEDENTES_SOCIOS", D("145756"))]
    assert tesoreria.ordenar_pagos(con, lote, "2027-07-15", "291669")["pagos"][0]["pagable"]
    bloqueado = tesoreria.ordenar_pagos(con, lote, "2027-07-15", "291667")["pagos"][0]
    assert not bloqueado["pagable"] and "SME" in bloqueado["motivo"]


# ================================ EXCEDENTES ================================
def _escenario_excedentes(con, s, ventas):
    """Saldo POA al 30-jun-2027 y excedentes por pagar de Bs 145.756."""
    depositar_ventas(con, s, ventas, "2027-06-30")
    contable.crear_asiento(con, s["jf"], "2027-06-30", "Excedente en distribución aprobado (simulación Fase 10)", [
        {"cuenta": "8.1.01.01", "debe": "145756"}, {"cuenta": "2.1.05.01", "haber": "145756"}], "PRUEBA",
        tipo="TRASPASO")
    ctx = seguridad.contexto_desde_bd(con, "2027-07-15")
    asamblea = seguridad.solicitar_autorizacion(con, s["jf"], "DISTRIBUCION_EXCEDENTES", "145756",
                                                "Distribución del excedente 2026", ctx,
                                                documentos={"INF-CV-04": "INF-CV-04 n.º 1/2027"})
    seguridad.firmar_autorizacion(con, s["ag"], asamblea, documento_ref="Acta AG 01/2027")
    desembolso = aut_desembolso(con, s, "145756", "2027-07-15")
    return asamblea, desembolso


def test_poa_pago_de_145756_bajo_sme_bloqueado(con, s):
    asamblea, desembolso = _escenario_excedentes(con, s, "153657")        # saldo 291.667
    control = tesoreria.verificar_pago_excedentes(con, "2027-07-15", "145756")
    assert (control["saldo_posterior"], control["sme"], control["permitido"]) == (D("145911.00"), D("145912.00"), False)
    antes = uno(con, "SELECT COUNT(*) FROM movimientos_bancarios")
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.pagar_excedentes(con, s["jf"], "2027-07-15", "145756", BANCO,
                                   autorizacion_asamblea_id=asamblea, autorizacion_desembolso_id=desembolso)
    assert e.value.codigo == "PAGO_EXCEDENTES_BAJO_SME"
    assert "Bs 145.911,00" in str(e.value) and "Bs 145.912,00" in str(e.value)
    assert uno(con, "SELECT COUNT(*) FROM movimientos_bancarios") == antes
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id=?", asamblea) == "AUTORIZADA"


def test_poa_saldo_al_30_jun_2027_permite_el_pago(con, s):
    asamblea, desembolso = _escenario_excedentes(con, s, "153659")        # saldo 291.669 (POA)
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.pagar_excedentes(con, s["jf"], "2027-07-15", "145756", BANCO, medio="EFECTIVO",
                                   autorizacion_asamblea_id=asamblea, autorizacion_desembolso_id=desembolso)
    assert e.value.codigo == "EFECTIVO_EXCEDE_LIMITE"
    tesoreria.pagar_excedentes(con, s["jf"], "2027-07-15", "145756", BANCO, autorizacion_asamblea_id=asamblea,
                               autorizacion_desembolso_id=desembolso)
    assert tesoreria.saldo_bancario(con, "2027-07-15") == D("145913.00")
    assert tesoreria.saldo_cuenta(con, "2.1.05.01") == D("0.00")


# ============================ FLUJO PROYECTADO ==============================
def flujo_poa(con, s):
    """Ciclo 2 (12-sep a 23-nov-2026) y ciclo 3, con las cifras del POA §3.2."""
    lineas = [
        LineaFlujo(date(2026, 10, 15), "EGRESO_OPERATIVO", "Egresos de octubre", D("47770")),
        LineaFlujo(date(2026, 11, 22), "EGRESO_OPERATIVO", "Sueldos y servicios 1-22 nov", D("9528")),
        LineaFlujo(date(2026, 11, 23), "INGRESO_VENTAS", "Cobro de carne y gallinaza C2", D("184800")),
        LineaFlujo(date(2026, 11, 23), "EGRESO_OPERATIVO", "Eventual y fletes C2", D("7302")),
        LineaFlujo(date(2026, 11, 24), "EGRESO_OPERATIVO", "Pollitos BB e insumos C3", D("64916")),
        LineaFlujo(date(2027, 2, 4), "INGRESO_VENTAS", "Cobro C3", D("184800")),
    ]
    return tesoreria.crear_flujo(con, s["jf"], "2026-10-01", "2026-09-12", "116825", lineas, "Flujo C2-C3 (POA)")


def test_poa_saldo_minimo_59527_genera_alerta(con, s):
    fid = flujo_poa(con, s)
    ev = tesoreria.evaluar_flujo(con, fid)
    assert (ev["ciclos"]["ACTUAL"]["fin"], ev["ciclos"]["SIGUIENTE"]["fin"]) == ("2026-11-23", "2027-02-04")
    assert (ev["saldo_minimo"], ev["fecha_minimo"]) == (D("59527.00"), "2026-11-22")
    assert ev["riesgo_bajo_sme"] and ev["sme_en_minimo"] == D("136413.00")
    assert ev["ciclos"]["ACTUAL"]["INGRESO_VENTAS"] == D("184800.00")
    assert tesoreria.saldo_proyectado_en(con, fid, "2026-10-31") == D("69055.00")
    aid = tesoreria.emitir_alerta_flujo(con, s["jf"], fid)
    alerta = con.execute("SELECT * FROM alertas_liquidez WHERE id=?", (aid,)).fetchone()
    assert alerta["origen"] == "FLUJO_SME" and alerta["punto_horizonte"] == "2026-11-22" and alerta["riesgo_bajo_sme"]
    assert "Bs 59.527,00" in alerta["descripcion"] and "22/11/2026" in alerta["descripcion"]
    assert tesoreria.emitir_alerta_flujo(con, s["jf"], fid) == aid      # no duplica


def test_flujo_horizonte_dos_ciclos(con, s):
    with pytest.raises(DatosInvalidosError, match="fuera del horizonte"):
        tesoreria.crear_flujo(con, s["jf"], "2026-10-01", "2026-09-12", "116825",
                              [LineaFlujo(date(2027, 2, 5), "INGRESO_VENTAS", "Fuera", D("1"))], "x")
    with pytest.raises(DatosInvalidosError):
        tesoreria.crear_flujo(con, s["jf"], "2026-10-01", "2026-09-12", "116825",
                              [{"fecha": "2026-10-02", "categoria": "OTRO", "descripcion": "x", "monto": "1"}], "x")


def test_desvio_mayor_al_10_por_ciento(con, s):
    fid = flujo_poa(con, s)
    sin_alerta = tesoreria.evaluar_desvio(con, s["jf"], fid, "2026-10-31", saldo_real="63000")
    assert sin_alerta["desvio"] == D("-8.77") and not sin_alerta["alerta"]
    con_alerta = tesoreria.evaluar_desvio(con, s["jf"], fid, "2026-10-31", saldo_real="61000")
    assert con_alerta["desvio"] == D("-11.66") and con_alerta["alerta"] and con_alerta["alerta_id"]
    assert con_alerta["riesgo_bajo_sme"]


def test_medida_correctiva_fin_tes_08(con, s):
    fid = flujo_poa(con, s)
    aid = tesoreria.emitir_alerta_flujo(con, s["jf"], fid)
    with pytest.raises(AutorizacionError):
        tesoreria.registrar_medida_correctiva(con, s["jf"], aid, ["acelerar_cobranza"], "2026-10-02")
    with pytest.raises(DatosInvalidosError):
        tesoreria.registrar_medida_correctiva(con, s["gg"], aid, [], "2026-10-02")
    tesoreria.recibir_alerta(con, s["gg"], aid)
    mid = tesoreria.registrar_medida_correctiva(con, s["gg"], aid, ["postergar_capex", "negociar_proveedores"],
                                                "2026-10-02", "Proveedor de alimento acepta 15 días")
    assert uno(con, "SELECT estado FROM alertas_liquidez WHERE id=?", aid) == "ATENDIDA"
    with pytest.raises(AutorizacionError):
        tesoreria.fiscalizar_medida(con, s["gg"], mid)
    tesoreria.fiscalizar_medida(con, s["cv"], mid)
    fila = tesoreria.listar_alertas(con, s["cv"])[0]
    assert fila["postergar_capex"] == 1 and fila["acelerar_cobranza"] == 0 and fila["fiscalizada_por"] == "palarcon"


def test_flujo_aprobado_por_la_gg_queda_cerrado(con, s):
    fid = flujo_poa(con, s)
    with pytest.raises(AutorizacionError):
        tesoreria.aprobar_flujo(con, s["jf"], fid)
    tesoreria.aprobar_flujo(con, s["gg"], fid)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO flujo_lineas (flujo_id, fecha, categoria, descripcion, monto_cent) "
                    "VALUES (?, '2026-10-05', 'CAPEX', 'x', 100)", (fid,))
    con.rollback()


# ============================ REPORTE DIARIO ================================
def test_semaforo_del_reporte_diario(con, s):
    r = tesoreria.reporte_diario(con, "2026-07-01")
    assert (r["saldo"], r["sme"], r["cumplimiento"], r["semaforo"]) == (D("138010.00"), D("136413.00"), D("101.17"),
                                                                         "NORMAL")
    aid = aut_desembolso(con, s, "1597.01", "2026-07-02")
    tesoreria.registrar_pago(con, s["jf"], "2026-07-02", "1597.01", BANCO, "7.1.05.01", "Licencia municipal",
                             "GAM Caranavi", autorizacion_id=aid)
    malo = tesoreria.reporte_diario(con, "2026-07-02")
    assert malo["saldo"] == D("136412.99") and malo["semaforo"] == "MALO" and malo["requiere_alerta"]
    depositar_ventas(con, s, "68206.51", "2026-07-03")                     # 204.619,50 = 150 %
    assert tesoreria.reporte_diario(con, "2026-07-03")["semaforo"] == "NORMAL"
    depositar_ventas(con, s, "0.01", "2026-07-04")
    assert tesoreria.reporte_diario(con, "2026-07-04")["semaforo"] == "BUENO"


def test_registrar_reporte_diario_con_alerta(con, s):
    aid = aut_desembolso(con, s, "2000", "2026-07-02")
    tesoreria.registrar_pago(con, s["jf"], "2026-07-02", "2000", BANCO, "7.1.05.01", "Licencia", "GAM",
                             autorizacion_id=aid)
    with pytest.raises(AutorizacionError):
        tesoreria.registrar_reporte_diario(con, s["jf"], "2026-07-02")
    r = tesoreria.registrar_reporte_diario(con, s["gg"], "2026-07-02")
    assert r["semaforo"] == "MALO" and r["alerta_id"]
    assert uno(con, "SELECT origen FROM alertas_liquidez WHERE id=?", r["alerta_id"]) == "REPORTE_DIARIO"
    with pytest.raises(ReglaNegocioError):
        tesoreria.registrar_reporte_diario(con, s["gg"], "2026-07-02")
    assert len(tesoreria.serie_saldo_diario(con, "2026-07-01", "2026-07-31")) == 31


# =========================== CONCILIACIÓN BANCARIA ==========================
def test_calculo_de_conciliacion_corregido():
    c = tesoreria.calcular_conciliacion(D("45000"), D("45800"), [
        PartidaConciliacion("CHEQUE_CIRCULACION", "Cheque 1.200", date(2026, 7, 20), D("1200")),
        PartidaConciliacion("CARGO_NO_REGISTRADO", "Comisión", date(2026, 7, 31), D("400"))])
    assert (c["saldo_conciliado_libros"], c["saldo_conciliado_banco"], c["cuadra"]) == (D("44600"), D("44600"), True)


def _julio_con_cheque(con, s):
    aid = aut_desembolso(con, s, "1200", "2026-07-20")
    return tesoreria.registrar_pago(con, s["jf"], "2026-07-20", "1200", BANCO, "7.1.02.03", "Reparación de bomba",
                                    "Taller Yungas", autorizacion_id=aid, nro_cheque="000045")


def test_conciliacion_mensual_firmada_por_la_gg(con, s):
    cheque = _julio_con_cheque(con, s)                                     # libros 136.810
    partidas = [PartidaConciliacion("CHEQUE_CIRCULACION", "Cheque 000045", date(2026, 7, 20), D("1200"), cheque),
                PartidaConciliacion("CARGO_NO_REGISTRADO", "Comisión de mantenimiento", date(2026, 7, 31), D("400"))]
    c = tesoreria.preparar_conciliacion(con, s["jf"], BANCO, 2026, 7, "137610", partidas, "2026-08-05")
    assert (c["saldo_libros"], c["saldo_conciliado_libros"], c["saldo_conciliado_banco"]) == \
        (D("136810.00"), D("136410.00"), D("136410.00"))
    assert c["cuadra"] and c["fecha_limite"] == "2026-08-10" and c["dentro_plazo"]   # 6-ago es feriado
    assert c["desembolsos_sin_mancomunada"] == []
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.firmar_conciliacion(con, s["gg"], c["id"], "2026-08-07")
    assert e.value.codigo == "PARTIDAS_SIN_REGULARIZAR"
    tesoreria.regularizar_partidas(con, s["jf"], c["id"], "2026-07-31")
    assert tesoreria.saldo_cuenta(con, BANCO, "2026-07-31") == D("136410.00")
    assert tesoreria.saldo_cuenta(con, "7.2.01.03") == D("400.00")
    with pytest.raises(AutorizacionError):
        tesoreria.firmar_conciliacion(con, s["jf"], c["id"], "2026-08-07")
    assert tesoreria.firmar_conciliacion(con, s["gg"], c["id"], "2026-08-10") is True
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.preparar_conciliacion(con, s["jf"], BANCO, 2026, 7, "137610", partidas, "2026-08-11")
    assert e.value.codigo == "CONCILIACION_FIRMADA"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE conciliaciones SET firmado_por = 'otro' WHERE id = ?", (c["id"],))
    con.rollback()


def test_conciliacion_descuadrada_no_se_firma_y_se_sustituye(con, s):
    _julio_con_cheque(con, s)
    mala = tesoreria.preparar_conciliacion(con, s["jf"], BANCO, 2026, 7, "137000", [], "2026-08-11")
    assert not mala["cuadra"] and not mala["dentro_plazo"]
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.firmar_conciliacion(con, s["gg"], mala["id"], "2026-08-11")
    assert e.value.codigo == "CONCILIACION_NO_CUADRA" and "48 horas" in str(e.value)
    buena = tesoreria.preparar_conciliacion(con, s["jf"], BANCO, 2026, 7, "138010", [
        PartidaConciliacion("CHEQUE_CIRCULACION", "Cheque 000045", date(2026, 7, 20), D("1200")),
        PartidaConciliacion("DEPOSITO_TRANSITO", "Depósito antiguo", date(2026, 6, 25), D("0.01")),
        PartidaConciliacion("ERROR_LIBROS_MAS", "Ajuste", date(2026, 7, 31), D("0.01"))], "2026-08-11")
    assert buena["cuadra"]
    assert [p["dias"] for p in buena["partidas_antiguas"]] == [36]
    assert uno(con, "SELECT estado FROM conciliaciones WHERE id=?", mala["id"]) == "SUSTITUIDA"
    assert tesoreria.firmar_conciliacion(con, s["gg"], buena["id"], "2026-08-11") is False   # fuera de plazo


def test_conciliacion_antes_de_cerrar_el_mes(con, s):
    with pytest.raises(ReglaNegocioError) as e:
        tesoreria.preparar_conciliacion(con, s["jf"], BANCO, 2026, 7, "1", [], "2026-07-31")
    assert e.value.codigo == "MES_NO_CERRADO"


# ================================== PDF =====================================
CTX = pdf.Contexto(fecha=date(2026, 10, 1), elaborado_por="Jefe de Finanzas", cargo="JF", ciclo="C2")


def _es_pdf(datos: bytes, codigo: str) -> None:
    assert datos.startswith(b"%PDF") and len(datos) > 1500
    assert codigo.encode("latin-1") in datos and b"Bloque de Validaci" in datos and b"P\\341gina 1 de" in datos


def test_pdf_fin_tes_05_06_07_08(con, s):
    fid = flujo_poa(con, s)
    _es_pdf(pdf.fin_tes_05(tesoreria.evaluar_flujo(con, fid), CTX, "Gerencia General"), "FIN-TES-05")
    _es_pdf(pdf.fin_tes_06(tesoreria.serie_saldo_diario(con, "2026-07-01", "2026-07-07"), CTX), "FIN-TES-06")
    aid = tesoreria.emitir_alerta_flujo(con, s["jf"], fid)
    alerta = next(a for a in tesoreria.listar_alertas(con, s["gg"]) if a["id"] == aid)
    _es_pdf(pdf.fin_tes_07(alerta, CTX), "FIN-TES-07")
    with pytest.raises(ValueError):
        pdf.fin_tes_08(alerta, CTX, tesoreria.MEDIDAS_CORRECTIVAS)
    tesoreria.registrar_medida_correctiva(con, s["gg"], aid, ["acelerar_cobranza"], "2026-10-02")
    alerta = next(a for a in tesoreria.listar_alertas(con, s["gg"]) if a["id"] == aid)
    _es_pdf(pdf.fin_tes_08(alerta, CTX, tesoreria.MEDIDAS_CORRECTIVAS), "FIN-TES-08")


def test_pdf_arqueo_conciliacion_y_reposicion(con, s):
    fondo(con, s)
    tesoreria.registrar_gasto_menor(con, s["jf"], "2026-07-08", "300", "7.1.02.01", "GM-901", "Librería", "Útiles")
    arqueo = tesoreria.registrar_arqueo(con, s["cv"], "2026-07-10", "4700", "300", "Jefe de Finanzas")
    _es_pdf(pdf.inf_cv_03({**arqueo, "custodio_presente": "Jefe de Finanzas"}, CTX, D("5000")), "INF-CV-03")
    rid = tesoreria.solicitar_reposicion(con, s["jf"], "2026-07-11")
    repo = next(r for r in tesoreria.listar_reposiciones(con, s["jf"]) if r["id"] == rid)
    gastos = [g for g in tesoreria.gastos_pendientes_reposicion(con) if g["reposicion_id"] == rid]
    _es_pdf(pdf.comprobante_reposicion(repo, gastos, CTX, "Jefe de Finanzas"), "CI-FIN-01")
    c = tesoreria.preparar_conciliacion(con, s["jf"], BANCO, 2026, 7, "133010", [], "2026-08-05")
    _es_pdf(pdf.fin_inf_02(tesoreria.obtener_conciliacion(con, s["gg"], c["id"]), CTX), "FIN-INF-02")
