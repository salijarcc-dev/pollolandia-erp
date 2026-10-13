"""Pruebas de servicios/contable.py (Fase 2).

Cifras de los documentos: saldo de Bancos de apertura Bs 138.010 (Manual
Presupuestario §5.1), pollitos BB a Bs 9,00 × 3.000 aves = Bs 27.000 (Manual
Presupuestario B.2, aves por ciclo 2026), faenado a Bs 1,50 por ave × 1.000
aves = Bs 1.500 (POA B2), capital Bs 534.300 (Acta).
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from servicios import contable, seguridad
from servicios.contable import LineaAsiento
from servicios.excepciones import (
    AutorizacionError, DatosInvalidosError, PeriodoCerradoError, ReglaNegocioError,
)

BANCO = "1.1.01.03"
POLLITOS = "1.1.03.01"
CARNE = "6.1.01.01"


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def compra_pollitos(con, s, fecha="2026-07-01", monto="27000"):
    return contable.crear_asiento(
        con, s["jf"], fecha, "Compra de 3.000 pollitos BB a Bs 9,00", [
            {"cuenta": POLLITOS, "debe": monto},
            {"cuenta": BANCO, "haber": monto},
        ], "MANUAL", "FIN-EGR-04", tipo="EGRESO")


def autorizar_ajuste(con, s, monto, fecha="2026-07-20"):
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "AJUSTE_CONTABLE", monto, "Ajuste contable", ctx)
    assert seguridad.firmar_autorizacion(con, s["gg"], aid) == "AUTORIZADA"
    return aid


def periodo_id(con, fecha):
    return uno(con, "SELECT id FROM periodos WHERE fecha_inicio <= ? AND fecha_fin >= ?", fecha, fecha)


# ------------------------------- registro ---------------------------------
def test_asiento_cuadrado_se_registra(con, s):
    a = compra_pollitos(con, s)
    assert (a.tipo, a.numero, a.total, a.comprobante) == ("EGRESO", 1, Decimal("27000.00"), "EGRESO-00001")
    assert uno(con, "SELECT COUNT(*) FROM asiento_detalle WHERE asiento_id=?", a.id) == 2
    assert uno(con, "SELECT creado_por FROM asientos WHERE id=?", a.id) == "jfinanzas"
    assert con.execute("SELECT * FROM v_asientos_descuadrados").fetchall() == []


def test_correlativo_automatico_por_tipo(con, s):
    e1 = compra_pollitos(con, s, monto="100")
    e2 = compra_pollitos(con, s, monto="200")
    t1 = contable.crear_asiento(con, s["jf"], "2026-07-02", "Traspaso al centro de carne", [
        {"cuenta": CARNE, "debe": "300", "sufijo": "01"}, {"cuenta": POLLITOS, "haber": "300"}],
        "MANUAL", tipo="TRASPASO")
    i1 = contable.crear_asiento(con, s["jf"], "2026-07-03", "Depósito de prueba", [
        {"cuenta": BANCO, "debe": "50"}, {"cuenta": "5.2.01.05", "haber": "50"}], "MANUAL", tipo="INGRESO")
    assert [e1.numero, e2.numero, t1.numero, i1.numero] == [1, 2, 1, 1]


def test_asiento_descuadrado_rechazado(con, s):
    antes = uno(con, "SELECT COUNT(*) FROM asientos")
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-01", "Compra descuadrada", [
            {"cuenta": POLLITOS, "debe": "27000.00"}, {"cuenta": BANCO, "haber": "26999.99"}],
            "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "ASIENTO_DESCUADRADO"
    assert e.value.detalle["diferencia"] == "0.01"
    assert "Bs 27.000,00" in str(e.value) and "Bs 0,01" in str(e.value)
    assert uno(con, "SELECT COUNT(*) FROM asientos") == antes  # rollback completo


def test_cuadre_exacto_despues_de_redondear(con, s):
    a = contable.crear_asiento(con, s["jf"], "2026-07-01", "Redondeo ROUND_HALF_UP", [
        LineaAsiento(POLLITOS, debe=Decimal("10.005")), LineaAsiento(BANCO, haber=10.01)], "MANUAL", tipo="EGRESO")
    assert a.total == Decimal("10.01")
    assert uno(con, "SELECT debe_cent FROM asiento_detalle WHERE asiento_id=? AND linea=1", a.id) == 1001


@pytest.mark.parametrize("linea, mensaje", [
    ({"cuenta": POLLITOS, "debe": "-5"}, "negativo"),
    ({"cuenta": POLLITOS, "debe": "5", "haber": "5"}, "no en ambos"),
    ({"cuenta": POLLITOS}, "ni en ninguno"),
    ({"cuenta": POLLITOS, "debe": "cinco"}, "no es un número"),
    ({"cuenta": "", "debe": "5"}, "indique la cuenta"),
])
def test_lineas_invalidas(con, s, linea, mensaje):
    with pytest.raises(DatosInvalidosError, match=mensaje):
        contable.crear_asiento(con, s["jf"], "2026-07-01", "x", [linea, {"cuenta": BANCO, "haber": "5"}],
                               "MANUAL", tipo="EGRESO")


def test_minimo_dos_lineas_y_tipo_valido(con, s):
    with pytest.raises(DatosInvalidosError, match="dos líneas"):
        contable.crear_asiento(con, s["jf"], "2026-07-01", "x", [{"cuenta": BANCO, "debe": "5"}], "MANUAL",
                               tipo="INGRESO")
    with pytest.raises(DatosInvalidosError, match="Tipo de asiento"):
        compra = [{"cuenta": POLLITOS, "debe": "5"}, {"cuenta": BANCO, "haber": "5"}]
        contable.crear_asiento(con, s["jf"], "2026-07-01", "x", compra, "MANUAL", tipo="APERTURA")
    with pytest.raises(DatosInvalidosError, match="glosa"):
        contable.crear_asiento(con, s["jf"], "2026-07-01", "  ", compra, "MANUAL", tipo="EGRESO")


def test_solo_cuentas_de_movimiento(con, s):
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-01", "x", [
            {"cuenta": "1.1.01", "debe": "5"}, {"cuenta": BANCO, "haber": "5"}], "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "CUENTA_NO_MOVIMIENTO"
    with pytest.raises(DatosInvalidosError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-01", "x", [
            {"cuenta": "1.1.01.99", "debe": "5"}, {"cuenta": BANCO, "haber": "5"}], "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "CUENTA_INEXISTENTE"


def test_fecha_fuera_del_calendario(con, s):
    with pytest.raises(ReglaNegocioError) as e:
        compra_pollitos(con, s, fecha="2026-06-30")
    assert e.value.codigo == "PERIODO_NO_EXISTE"


# ------------------------------ períodos ----------------------------------
def test_periodo_cerrado_rechazado(con, s):
    contable.cerrar_periodo(con, s["gg"], periodo_id(con, "2026-07-15"))
    with pytest.raises(PeriodoCerradoError) as e:
        compra_pollitos(con, s, fecha="2026-07-15")
    assert "julio de 2026" in str(e.value)
    assert compra_pollitos(con, s, fecha="2026-08-03").numero == 1  # agosto sigue abierto


def test_la_bd_tambien_impide_asientos_en_periodo_cerrado(con, s):
    pid = periodo_id(con, "2026-07-15")
    contable.cerrar_periodo(con, s["gg"], pid)
    with pytest.raises(sqlite3.IntegrityError, match="cerrado"):
        con.execute("""INSERT INTO asientos (tipo, numero, fecha, periodo_id, glosa, origen, creado_por, creado_en)
                       VALUES ('EGRESO', 99, '2026-07-15', ?, 'directo', 'SQL', 'x', 'x')""", (pid,))
    with pytest.raises(sqlite3.IntegrityError, match="no se reabre"):
        con.execute("UPDATE periodos SET estado='ABIERTO' WHERE id=?", (pid,))
    con.rollback()


def test_cierre_secuencial_y_solo_gg(con, s):
    julio, agosto = periodo_id(con, "2026-07-01"), periodo_id(con, "2026-08-01")
    with pytest.raises(AutorizacionError) as e:
        contable.cerrar_periodo(con, s["jf"], julio)
    assert e.value.codigo == "SEGREGACION_FUNCIONES"
    with pytest.raises(ReglaNegocioError) as e:
        contable.cerrar_periodo(con, s["gg"], agosto)
    assert e.value.codigo == "PERIODO_ANTERIOR_ABIERTO"
    contable.cerrar_periodo(con, s["gg"], julio)
    with pytest.raises(ReglaNegocioError):
        contable.cerrar_periodo(con, s["gg"], julio)
    assert uno(con, "SELECT cerrado_por FROM periodos WHERE id=?", julio) == "rflores"
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE accion='CIERRE' AND tabla='periodos'") == 1


# ------------------------------ naturaleza --------------------------------
def test_banco_no_puede_quedar_con_saldo_acreedor(con, s):
    with pytest.raises(ReglaNegocioError) as e:
        compra_pollitos(con, s, monto="138010.01")
    assert e.value.codigo == "SALDO_CONTRARIO_NATURALEZA"
    assert "Bs 0,01" in str(e.value) and "Bs 138.010,00" in str(e.value)
    assert compra_pollitos(con, s, monto="138010.00").total == Decimal("138010.00")


def test_pasivo_no_puede_quedar_deudor(con, s):
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-05", "Pago a proveedor sin deuda registrada", [
            {"cuenta": "2.1.01.01", "debe": "100"}, {"cuenta": BANCO, "haber": "100"}], "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "SALDO_CONTRARIO_NATURALEZA"


# ------------------------- cuentas (*) desde 2027 -------------------------
def test_cuentas_desde_2027(con, s):
    asiento = [{"cuenta": "1.1.03.03", "debe": "100"}, {"cuenta": BANCO, "haber": "100"}]
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2027-06-30", "Maíz amarillo", asiento, "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "CUENTA_NO_HABILITADA" and "2027" in str(e.value)
    a = contable.crear_asiento(con, s["jf"], "2027-07-01", "Maíz amarillo", asiento, "MANUAL", tipo="EGRESO")
    assert a.ejercicio == 2027


# ------------------------- Grupo 6: centro y sufijo -----------------------
def test_grupo6_con_sufijo_y_centro_autocompletado(con, s):
    compra_pollitos(con, s)
    a = contable.crear_asiento(con, s["jf"], "2026-07-01", "Pollitos al centro de carne", [
        {"cuenta": CARNE, "debe": "27000", "sufijo": "1"}, {"cuenta": POLLITOS, "haber": "27000"}],
        "MANUAL", tipo="TRASPASO")
    fila = con.execute("SELECT centro_costo, sufijo FROM asiento_detalle WHERE asiento_id=? AND linea=1",
                       (a.id,)).fetchone()
    assert tuple(fila) == (CARNE, "01")


def test_grupo6_sufijo_obligatorio_y_aplicable(con, s):
    sin_sufijo = [{"cuenta": CARNE, "debe": "10"}, {"cuenta": BANCO, "haber": "10"}]
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2027-07-05", "x", sin_sufijo, "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "SUFIJO_OBLIGATORIO"
    maiz_en_carne = [{"cuenta": CARNE, "debe": "10", "sufijo": "03"}, {"cuenta": BANCO, "haber": "10"}]
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2027-07-05", "x", maiz_en_carne, "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "SUFIJO_NO_APLICABLE"
    inexistente = [{"cuenta": CARNE, "debe": "10", "sufijo": "15"}, {"cuenta": BANCO, "haber": "10"}]
    with pytest.raises(DatosInvalidosError):
        contable.crear_asiento(con, s["jf"], "2027-07-05", "x", inexistente, "MANUAL", tipo="EGRESO")


def test_sufijo_fuera_del_grupo6_y_centro_distinto(con, s):
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-05", "x", [
            {"cuenta": POLLITOS, "debe": "10", "sufijo": "01"}, {"cuenta": BANCO, "haber": "10"}],
            "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "SUFIJO_FUERA_DE_GRUPO6"
    with pytest.raises(DatosInvalidosError):
        contable.crear_asiento(con, s["jf"], "2027-07-05", "x", [
            {"cuenta": CARNE, "debe": "10", "sufijo": "01", "centro_costo": "6.1.01.02"},
            {"cuenta": BANCO, "haber": "10"}], "MANUAL", tipo="EGRESO")


def test_sufijos_y_centro_de_alimento_desde_2027(con, s):
    alimento_propio = [{"cuenta": CARNE, "debe": "10", "sufijo": "11"}, {"cuenta": BANCO, "haber": "10"}]
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-05", "x", alimento_propio, "MANUAL", tipo="EGRESO")
    assert e.value.codigo == "CUENTA_NO_HABILITADA"
    centro_alimento = [{"cuenta": "6.1.01.02", "debe": "10", "sufijo": "20"}, {"cuenta": BANCO, "haber": "10"}]
    with pytest.raises(ReglaNegocioError):
        contable.crear_asiento(con, s["jf"], "2026-07-05", "x", centro_alimento, "MANUAL", tipo="EGRESO")
    assert contable.crear_asiento(con, s["jf"], "2027-07-05", "MOD alimento", centro_alimento, "MANUAL",
                                  tipo="EGRESO").total == Decimal("10.00")


def test_reclasificacion_a_mortandad_normal_sufijo_40(con, s):
    compra_pollitos(con, s)
    contable.crear_asiento(con, s["jf"], "2026-07-01", "Pollitos al centro", [
        {"cuenta": CARNE, "debe": "27000", "sufijo": "01"}, {"cuenta": POLLITOS, "haber": "27000"}],
        "MANUAL", tipo="TRASPASO")
    a = contable.crear_asiento(con, s["jf"], "2026-07-31", "Mortandad normal 2 % (60 aves × Bs 9,00)", [
        {"cuenta": CARNE, "debe": "540", "sufijo": "40"}, {"cuenta": CARNE, "haber": "540", "sufijo": "01"}],
        "MANUAL", tipo="TRASPASO")
    mayor_40 = contable.libro_mayor(con, s["jf"], CARNE, sufijo="40")
    assert mayor_40["saldo_final"] == Decimal("540.00") and a.total == Decimal("540.00")
    assert contable.libro_mayor(con, s["jf"], CARNE)["saldo_final"] == Decimal("27000.00")


# ------------------------ segregación de funciones ------------------------
@pytest.mark.parametrize("rol, codigo", [("gg", "SEGREGACION_FUNCIONES"), ("cv", "SEGREGACION_FUNCIONES"),
                                         ("ut", "SEGREGACION_FUNCIONES"), ("ca_presidente", "SIN_PERMISO"),
                                         ("ag", "SOLO_LECTURA")])
def test_solo_el_jf_registra(con, s, rol, codigo):
    with pytest.raises(AutorizacionError) as e:
        contable.crear_asiento(con, s[rol], "2026-07-01", "x", [
            {"cuenta": POLLITOS, "debe": "5"}, {"cuenta": BANCO, "haber": "5"}], "MANUAL", tipo="EGRESO")
    assert e.value.codigo == codigo
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE accion='OTRO' AND detalle LIKE 'Acceso denegado%'") == 1


def test_cv_consulta_libros_y_ut_no(con, s):
    assert contable.libro_diario(con, s["cv"])[0]["tipo"] == "APERTURA"
    with pytest.raises(AutorizacionError):
        contable.libro_diario(con, s["ut"])


# ------------------------------ ajustes -----------------------------------
def test_ajuste_exige_autorizacion_de_la_gg(con, s):
    lineas = [{"cuenta": "7.1.02.01", "debe": "500"}, {"cuenta": BANCO, "haber": "500"}]
    with pytest.raises(AutorizacionError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-20", "Ajuste", lineas, "MANUAL", tipo="AJUSTE")
    assert e.value.codigo == "AJUSTE_SIN_AUTORIZACION"
    aid = autorizar_ajuste(con, s, "500")
    a = contable.crear_asiento(con, s["jf"], "2026-07-20", "Ajuste", lineas, "MANUAL", tipo="AJUSTE",
                               autorizacion_id=aid)
    assert a.tipo == "AJUSTE"
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id=?", aid) == "UTILIZADA"
    assert uno(con, "SELECT utilizado_id FROM autorizaciones WHERE id=?", aid) == str(a.id)
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-20", "Ajuste", lineas, "MANUAL", tipo="AJUSTE",
                               autorizacion_id=aid)
    assert e.value.codigo == "AUTORIZACION_YA_UTILIZADA"


def test_ajuste_por_monto_mayor_al_autorizado(con, s):
    aid = autorizar_ajuste(con, s, "400")
    with pytest.raises(ReglaNegocioError) as e:
        contable.crear_asiento(con, s["jf"], "2026-07-20", "Ajuste", [
            {"cuenta": "7.1.02.01", "debe": "500"}, {"cuenta": BANCO, "haber": "500"}], "MANUAL",
            tipo="AJUSTE", autorizacion_id=aid)
    assert e.value.codigo == "MONTO_EXCEDE_AUTORIZADO"
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id=?", aid) == "AUTORIZADA"


# ------------------------------ reversión ---------------------------------
def test_reversar_asiento(con, s):
    a = compra_pollitos(con, s)
    aid = autorizar_ajuste(con, s, "27000")
    r = contable.reversar_asiento(con, s["jf"], a.id, "Factura duplicada", autorizacion_id=aid)
    assert (r.tipo, r.total) == ("AJUSTE", Decimal("27000.00"))
    assert uno(con, "SELECT estado FROM asientos WHERE id=?", a.id) == "REVERSADO"
    assert uno(con, "SELECT reversa_de FROM asientos WHERE id=?", r.id) == a.id
    espejo = con.execute("SELECT cuenta_codigo, debe_cent, haber_cent FROM asiento_detalle WHERE asiento_id=? "
                         "ORDER BY linea", (r.id,)).fetchall()
    assert [tuple(x) for x in espejo] == [(POLLITOS, 0, 2_700_000), (BANCO, 2_700_000, 0)]
    assert contable.libro_mayor(con, s["jf"], BANCO)["saldo_final"] == Decimal("138010.00")
    assert uno(con, "SELECT COUNT(*) FROM asientos WHERE id=?", a.id) == 1  # nunca se borra
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE accion='REVERSA'") == 1


def test_reversion_rechazos(con, s):
    a = compra_pollitos(con, s)
    aid = autorizar_ajuste(con, s, "27000")
    r = contable.reversar_asiento(con, s["jf"], a.id, "Error de imputación", autorizacion_id=aid)
    aid2 = autorizar_ajuste(con, s, "27000")
    with pytest.raises(ReglaNegocioError) as e:
        contable.reversar_asiento(con, s["jf"], a.id, "otra vez", autorizacion_id=aid2)
    assert e.value.codigo == "ASIENTO_YA_REVERSADO"
    with pytest.raises(ReglaNegocioError) as e:
        contable.reversar_asiento(con, s["jf"], r.id, "revertir la reversión", autorizacion_id=aid2)
    assert e.value.codigo == "ASIENTO_NO_REVERSIBLE"
    with pytest.raises(ReglaNegocioError) as e:
        contable.reversar_asiento(con, s["jf"], 1, "apertura", autorizacion_id=aid2)
    assert e.value.codigo == "ASIENTO_NO_REVERSIBLE"
    with pytest.raises(AutorizacionError):
        contable.reversar_asiento(con, s["gg"], a.id, "la GG no registra", autorizacion_id=aid2)


def test_reversion_sin_firma_de_la_gg(con, s):
    a = compra_pollitos(con, s)
    ctx = seguridad.contexto_desde_bd(con, "2026-07-20")
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "AJUSTE_CONTABLE", "27000", "Reversión", ctx)
    with pytest.raises(AutorizacionError) as e:
        contable.reversar_asiento(con, s["jf"], a.id, "sin firma", autorizacion_id=aid)
    assert e.value.codigo == "AUTORIZACION_INCOMPLETA"
    assert uno(con, "SELECT estado FROM asientos WHERE id=?", a.id) == "VIGENTE"


def test_reversion_de_periodo_cerrado_va_a_periodo_abierto(con, s):
    a = compra_pollitos(con, s, fecha="2026-07-10")
    contable.cerrar_periodo(con, s["gg"], periodo_id(con, "2026-07-10"))
    aid = autorizar_ajuste(con, s, "27000", fecha="2026-08-05")
    with pytest.raises(PeriodoCerradoError):
        contable.reversar_asiento(con, s["jf"], a.id, "Error", autorizacion_id=aid)
    r = contable.reversar_asiento(con, s["jf"], a.id, "Error", autorizacion_id=aid, fecha="2026-08-05")
    assert r.fecha == "2026-08-05"


# -------------------------------- libros ----------------------------------
def test_balance_de_comprobacion_de_apertura(con, s):
    b = contable.balance_comprobacion(con, s["cv"])
    assert b["cuadra_sumas"] and b["cuadra_saldos"]
    assert b["totales"]["debe"] == b["totales"]["haber"] == Decimal("534300.00")
    assert b["totales"]["saldo_deudor"] == Decimal("534300.00")
    por_grupo = {f["codigo"]: f for f in contable.balance_comprobacion(con, s["cv"], nivel=1)["filas"]}
    assert por_grupo["1"]["saldo_deudor"] == Decimal("534300.00")
    assert por_grupo["3"]["saldo_acreedor"] == Decimal("534300.00") and por_grupo["3"]["nombre"] == "PATRIMONIO"


def test_libros_despues_de_operaciones(con, s):
    compra_pollitos(con, s, fecha="2026-07-01")
    contable.crear_asiento(con, s["jf"], "2026-09-11", "Faenado: 1.000 aves × Bs 1,50", [
        {"cuenta": CARNE, "debe": "1500", "sufijo": "22"}, {"cuenta": BANCO, "haber": "1500"}],
        "MANUAL", "FIN-EGR-08", tipo="EGRESO")
    mayor = contable.libro_mayor(con, s["jf"], BANCO, desde="2026-07-02")
    assert mayor["saldo_inicial"] == Decimal("111010.00")
    assert [m["haber"] for m in mayor["movimientos"]] == [Decimal("1500.00")]
    assert mayor["saldo_final"] == Decimal("109510.00")
    diario = contable.libro_diario(con, s["jf"], desde="2026-07-01", hasta="2026-07-31")
    assert [d["comprobante"] for d in diario] == ["APERTURA-00001", "EGRESO-00001"]
    assert diario[0]["total_debe"] == Decimal("534300.00") and len(diario[0]["lineas"]) == 11
    b = contable.balance_comprobacion(con, s["jf"], hasta="2026-09-30")
    assert b["cuadra_sumas"] and b["totales"]["debe"] == Decimal("562800.00")
    with pytest.raises(DatosInvalidosError):
        contable.libro_diario(con, s["jf"], desde="2026-08-01", hasta="2026-07-01")
    with pytest.raises(ReglaNegocioError):
        contable.libro_mayor(con, s["jf"], "1.1.01")


def test_auditoria_del_asiento(con, s):
    a = compra_pollitos(con, s)
    fila = con.execute("SELECT * FROM auditoria WHERE tabla='asientos' AND accion='INSERT' AND registro_id=?",
                       (str(a.id),)).fetchone()
    assert fila["usuario"] == "jfinanzas" and fila["rol"] == "JF"
    assert '"total": "27000.00"' in fila["valores_despues"] and '"cuenta": "1.1.03.01"' in fila["valores_despues"]


def test_catalogos(con):
    cuentas = {c["codigo"]: c for c in contable.listar_cuentas_movimiento(con, 2026)}
    assert len(cuentas) == 127 and cuentas[CARNE]["es_centro"] and not cuentas["1.1.03.03"]["habilitada"]
    sufijos = {x["sufijo"]: x for x in contable.listar_sufijos(con, CARNE, 2026)}
    assert len(sufijos) == 19 and not sufijos["11"]["habilitado"] and sufijos["01"]["habilitado"]
    assert len(contable.listar_periodos(con, 2026)) == 12
