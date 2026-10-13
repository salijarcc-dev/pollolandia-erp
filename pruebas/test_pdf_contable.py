"""Pruebas de los PDF oficiales del módulo de contabilidad.

Cifras del escenario 2026 (Manual Presupuestario / EEFF proyectados):
excedente neto Bs 210.293,39; apertura con banco Bs 138.010 y capital
Bs 534.300. Se verifica el membrete institucional en cada documento, el pie
con fecha de emisión y número de página, la cuadratura del balance de 8
columnas y del mayor, y la identidad tomada de la base (NIT y Certificado RAU).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from pruebas import escenario_2026 as esc
from reportes import estados as rep
from reportes import pdf_contable as pc
from reportes.formatos_tesoreria import Contexto
from servicios import cierre, contable, identidad, parametros, rau
from servicios.excepciones import AutorizacionError

D = Decimal
ENTIDAD = b"COOPERATIVA AGROPECUARIA POLLOLANDIA R.L."
CTX = Contexto(fecha=datetime(2027, 7, 5).date(), elaborado_por="Jefe de Finanzas", cargo="Jefe de Finanzas")


def _oficial(pdf: bytes, *textos: bytes) -> None:
    """PDF con membrete, pie de emisión y numeración de páginas."""
    assert pdf.startswith(b"%PDF")
    assert ENTIDAD in pdf and b"Caranavi, La Paz" in pdf
    assert b"Emitido el 05/07/2027 10:30" in pdf and b"P\\341gina 1 de" in pdf
    for t in textos:
        assert t in pdf, t


@pytest.fixture()
def cargado(con, s):
    esc.cargar_escenario_2026(con, s)
    return con


@pytest.fixture()
def me(cargado, s):
    return identidad.membrete(cargado), identidad.emision(s["jf"], datetime(2027, 7, 5, 10, 30))


def test_membrete_desde_la_base_nit_y_certificado_rau(con, s):
    m = identidad.membrete(con)
    assert (m.entidad, m.ubicacion, m.nit, m.rau) == ("COOPERATIVA AGROPECUARIA POLLOLANDIA R.L.",
                                                     "Caranavi, La Paz – Bolivia", "Por registrar", "En trámite")
    rau.registrar_certificado(con, s["jf"], "RAU-0001/2026", "2026-07-02")
    sol = parametros.solicitar_cambio(con, s["gg"], "entidad_nit", "1023456027", 2026, "Certificado del SIN",
                                      "Registro del NIT asignado")
    parametros.resolver_cambio(con, s["ca_presidente"], sol["id"], True, "Acta CA 3/2026")
    m = identidad.membrete(con)
    assert (m.nit, m.rau) == ("1023456027", "RAU-0001/2026")
    assert m.linea_identidad == "Caranavi, La Paz – Bolivia  |  NIT: 1023456027  |  RAU: RAU-0001/2026"
    e = identidad.emision(s["jf"], datetime(2027, 7, 5, 10, 30, 15, 99))
    assert e.texto == "Emitido el 05/07/2027 10:30 por Jefe de Finanzas y Comercialización (Jefe de Finanzas y " \
                      "Comercialización)"
    pdf = pc.comprobante(contable.obtener_asiento(con, s["jf"], 1), m, e)
    assert b"NIT: 1023456027" in pdf and b"RAU: RAU-0001/2026" in pdf


def test_comprobantes_de_apertura_ingreso_egreso_y_traspaso(cargado, s, me):
    diario = contable.libro_diario(cargado, s["jf"], "2026-07-01", "2026-09-11")
    vistos = {}
    for a in diario:
        vistos.setdefault(a["tipo"], a)
    assert {"APERTURA", "INGRESO", "EGRESO", "TRASPASO"} <= set(vistos)
    apertura = pc.comprobante(vistos["APERTURA"], *me)
    _oficial(apertura, b"COMPROBANTE DE APERTURA", b"APERTURA-00001", b"Bs 534.300,00", b"TOTALES",
             b"Elaborado por: Contabilidad", b"Autorizado por: Gerencia General", b"Consejo de Vigilancia",
             b"Partida doble")
    for tipo in ("INGRESO", "EGRESO", "TRASPASO"):
        _oficial(pc.comprobante(vistos[tipo], *me), f"COMPROBANTE DE {tipo}".encode(), b"CUADRA")
    lote = pc.comprobantes(diario[:5], "2026-07-01", "2026-09-11", *me)
    _oficial(lote, b"P\\341gina 5 de 5")


def test_libro_diario_con_totales_de_verificacion(cargado, s, me):
    diario = contable.libro_diario(cargado, s["jf"], "2026-07-01", "2027-06-30")
    total = sum((a["total_debe"] for a in diario), D(0))
    pdf = pc.libro_diario(diario, "2026-07-01", "2027-06-30", *me)
    _oficial(pdf, b"LIBRO DIARIO", b"Del 01/07/2026 al 30/06/2027", b"TOTALES DEL PER",
             b"comprobantes", pc.bs(total).encode())
    assert b"Totales de verificaci" in pdf and b"CUADRA" in pdf
    with pytest.raises(AutorizacionError):
        contable.libro_diario(cargado, s["ut"], "2026-07-01", "2026-07-31")


def test_libro_mayor_individual_y_masivo(cargado, s, me):
    m = contable.libro_mayor(cargado, s["jf"], "1.1.01.03", "2026-07-01", "2027-06-30")
    assert m["saldo_inicial"] == D("0.00")          # la apertura del 1-jul entra como movimiento del período
    pdf = pc.libro_mayor(m, "2026-07-01", "2027-06-30", None, *me)
    _oficial(pdf, b"LIBRO MAYOR ANAL", b"SALDO INICIAL", b"TOTALES Y SALDO FINAL", pc.bs(m["saldo_final"]).encode())
    segundo = contable.libro_mayor(cargado, s["jf"], "1.1.01.03", "2026-09-12", "2027-06-30")
    assert segundo["saldo_inicial"] > 0
    lista = contable.mayores(cargado, s["jf"], "2026-07-01", "2027-06-30", por_sufijo=True)
    centro = [x for x in lista if x["cuenta"] == "6.1.01.01"]
    assert len(centro) > 1 and all(x["sufijo"] for x in centro)            # abierto por sufijo
    assert len(contable.mayores(cargado, s["jf"], "2026-07-01", "2027-06-30")) < len(lista)
    masivo = pc.libro_mayor_masivo(lista, "2026-07-01", "2027-06-30", *me)
    _oficial(masivo, b"Sufijo 01", b"6.1.01.01")


def test_balance_de_comprobacion_4_y_8_columnas(cargado, s, me):
    b4 = contable.balance_comprobacion(cargado, s["jf"], "2026-07-01", "2027-06-30")
    _oficial(pc.balance_comprobacion(b4, *me), b"BALANCE DE COMPROBACI", b"Sumas Debe", b"Saldo acreedor",
             pc.bs(b4["totales"]["debe"]).encode())
    b8 = contable.balance_ocho_columnas(cargado, s["jf"], "2026-07-01", "2027-06-30")
    assert b8["cuadra_sumas"] and b8["cuadra_saldos"] and b8["cuadra_resultado"]
    assert b8["resultado"]["monto"] == D("210293.39")                      # excedente neto del escenario 2026
    f = b8["finales"]
    assert f["activo"] == f["pasivo_patrimonio"] and f["perdidas"] == f["ganancias"]
    assert b8["totales"]["saldo_deudor"] == b4["totales"]["saldo_deudor"]
    pdf = pc.balance_ocho_columnas(b8, *me)
    _oficial(pdf, b"BALANCE DE COMPROBACI", b"EXCEDENTE DEL PER", b"TOTALES IGUALADOS", b"Bs 210.293,39")


def test_estados_financieros_con_membrete_firmas_y_notas(cargado, s, me):
    esc.cerrar_ejercicio_completo(cargado, s)
    paquete = cierre.estados_financieros(cargado, s["jf"], 2026)
    for pdf, titulo in ((rep.balance_pdf(paquete["balance"], *me), b"BALANCE GENERAL"),
                        (rep.resultados_pdf(paquete["resultados"], *me), b"ESTADO DE RESULTADOS"),
                        (rep.flujo_pdf(paquete["flujo"], *me), b"ESTADO DE FLUJO DE EFECTIVO"),
                        (rep.patrimonio_pdf(paquete["patrimonio"], *me), b"PATRIMONIO NETO")):
        _oficial(pdf, titulo, b"Representante Legal", b"Contador General", b"Presidente del Consejo de Administraci",
                 b"Notas y observaciones", b"Expresado en Bolivianos")
    _oficial(rep.notas_pdf(paquete["notas"], paquete["fecha_corte"], *me), b"NOTAS A LOS ESTADOS FINANCIEROS")
    completo = rep.paquete_pdf(paquete, "abc123", *me)
    _oficial(completo, b"ESTADOS FINANCIEROS", b"Huella SHA-256 del paquete emitido: abc123")
    assert b"210.293,39" in rep.resultados_pdf(paquete["resultados"], *me)


def test_formatos_del_cierre_mensual_y_periodos(cargado, s, me):
    sep = int(cargado.execute("SELECT id FROM periodos WHERE ejercicio = 2026 AND numero = 3").fetchone()[0])
    lista = cierre.lista_verificacion(cargado, s["jf"], sep)
    _oficial(rep.fin_inf_01(lista, CTX, membrete=me[0], emision=me[1]), b"LISTA DE VERIFICACI", b"FIN-INF-01")
    _oficial(rep.fin_inf_04(cierre.estado_resultados_mensual(cargado, s["jf"], sep), CTX, membrete=me[0],
                            emision=me[1]), b"FIN-INF-04")
    filas = [{**p, "nombre": contable.describir_periodo(p)} for p in contable.listar_periodos(cargado, 2026)]
    _oficial(pc.periodos(2026, filas, *me), b"ESTADO DE LOS PER", b"septiembre de 2026")
    plan = pc.plan_cuentas(contable.plan_de_cuentas(cargado, s["cv"]), *me)
    _oficial(plan, b"PLAN DE CUENTAS", b"1.1.01.03", b"Desde 2027")
