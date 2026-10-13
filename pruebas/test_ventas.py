"""Pruebas de la Fase 9 — Ventas, HORECA y cobranza (servicios/ventas.py).

Casos de borde con las cifras de los documentos:
* POL-05 Art. 1: crédito HORECA de 2 días en 2026 rechazado (máximo 1 día).
* POL-05 Art. 1 / PR-FIN-08: entrega sin comprobante rechazada.
* CI-FIN-13 Art. 8: el lote sin cobertura completa (1.000 aves, 2 friales) no
  permite cerrar el despacho.
* Tarifario Bs 23,50/kg y gallinaza Bs 500/carga (Manual Presupuestario
  §3.1); costo de referencia Bs 14,91/kg (D.4 y ciclo 2026); margen 20 %.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from pruebas.test_costos import abrir_ciclo_2026, ciclo_completo_2026
from pruebas.test_tesoreria import fondo
from reportes import formatos_ventas as pdf
from servicios import costos, tesoreria, ventas
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal
RAU = "Certificado RAU N.º 0001/2026 y Form. 701 pagado"


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def tarifario(con, s, fecha="2026-07-01", lineas=None):
    lineas = lineas or [("CARNE_ENTERO", "TODOS", "23.50"), ("GALLINAZA", "TODOS", "500")]
    r = ventas.proponer_tarifario(con, s["gg"], fecha, lineas, "Tarifario del Manual Presupuestario §3.1")
    assert ventas.resolver_tarifario(con, s["ca_presidente"], r["id"], "APRUEBA", "Acta CA 2/2026") == "APROBADO"
    return r


def clientes(con, s):
    return {
        "hotel": ventas.registrar_cliente(con, s["jf"], "HORECA", "Hotel Esmeralda Coroico", "2026-07-01",
                                          documento="4455667")["id"],
        "eco": ventas.registrar_cliente(con, s["jf"], "HORECA", "Eco-lodge Sol y Luna", "2026-07-01",
                                        documento="5566778")["id"],
        "frial1": ventas.registrar_cliente(con, s["jf"], "FRIAL", "Frial Caranavi", "2026-07-01")["id"],
        "frial2": ventas.registrar_cliente(con, s["jf"], "FRIAL", "Frial Los Yungas", "2026-07-01")["id"],
        "mercado": ventas.registrar_cliente(con, s["jf"], "MERCADO_POPULAR", "Puesto 14 Mercado Central",
                                            "2026-07-01")["id"],
        "agricultor": ventas.registrar_cliente(con, s["jf"], "AGRICULTOR", "Juan Quispe (cafetal)", "2026-07-01")["id"],
    }


def nota(con, s, cliente, fecha, cantidad, despacho=None, producto="CARNE_ENTERO", medio="TRANSFERENCIA", dias=0,
         comprobante="COMP-001", rau=True):
    return ventas.registrar_nota(con, s["jf"], cliente, fecha, producto, cantidad, hora_entrega="10:30",
                                 medio_pago=medio, comprobante_ref=comprobante, certificado_rau_exhibido=rau,
                                 certificado_rau_ref=RAU, recibido_por="Encargado de cocina",
                                 entregado_por="Encargado de Granja", despacho_id=despacho, dias_credito=dias)


def base(con, s, faenado="2026-09-13"):
    """Ciclo 2026 abierto, tarifario aprobado, clientes y despacho del primer lote."""
    cid, lotes, provs = abrir_ciclo_2026(con, s)
    tarifario(con, s)
    c = clientes(con, s)
    despacho = ventas.crear_despacho(con, s["jf"], lotes[0], faenado)
    return {"ciclo": cid, "lotes": lotes, "clientes": c, "despacho": despacho["id"]}


# ===================== PRUEBA 1: CRÉDITO DE 2 DÍAS EN 2026 =====================
def test_credito_de_2_dias_en_2026_rechazado(con, s):
    b = base(con, s)
    hotel = b["clientes"]["hotel"]
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, hotel, "2026-09-13", "100", b["despacho"], medio="EFECTIVO", dias=2)
    assert e.value.codigo == "CREDITO_EXCEDE_PLAZO"
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, hotel, "2026-09-13", "100", b["despacho"], medio="CHEQUE_DIFERIDO", dias=1)
    assert e.value.codigo == "CHEQUE_DIFERIDO"
    r = nota(con, s, hotel, "2026-09-13", "100", b["despacho"], medio="CHEQUE_NOMINATIVO", dias=1,
             comprobante="FIN-ING-04 n.º 1")
    assert (r["tipo"], r["documento"], r["precio"], r["total"]) == ("HORECA", "FIN-ING-03 n.º 1", D("23.50"),
                                                                     D("2350.00"))
    assert r["vencimiento"].isoformat() == "2026-09-14" and r["costo"] is None   # la carne entra al cierre
    assert tesoreria.saldo_cuenta(con, "1.1.02.01") == D("2350.00")
    assert tesoreria.saldo_cuenta(con, "5.1.01.01") == D("-302350.00")             # + Bs 300.000 del depósito inicial
    # La resolución de crédito > 1 día no corresponde al régimen 2026-27.
    with pytest.raises(ReglaNegocioError) as e:
        ventas.autorizar_credito(con, s["gg"], hotel, "2026-09-13", 3, "1000", "Buen pagador")
    assert e.value.codigo == "REGIMEN_SIN_CREDITO_EXTENDIDO"
    # Fuera de HORECA, solo contado.
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, b["clientes"]["frial1"], "2026-09-13", "100", b["despacho"], dias=1)
    assert e.value.codigo == "SOLO_CONTADO"


# ===================== PRUEBA 2: ENTREGA SIN COMPROBANTE =======================
def test_entrega_sin_comprobante_rechazada(con, s):
    b = base(con, s)
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, b["clientes"]["hotel"], "2026-09-13", "100", b["despacho"], dias=1, medio="EFECTIVO",
             comprobante=None)
    assert e.value.codigo == "COMPROBANTE_REQUERIDO"
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, b["clientes"]["frial1"], "2026-09-13", "100", b["despacho"], comprobante="  ")
    assert e.value.codigo == "COMPROBANTE_REQUERIDO"
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, b["clientes"]["frial1"], "2026-09-13", "100", b["despacho"], rau=False)
    assert e.value.codigo == "RAU_NO_EXHIBIDO"
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, b["clientes"]["frial1"], "2026-09-13", "100", b["despacho"], medio="DEPOSITO")
    assert e.value.codigo == "MEDIO_NO_ADMITIDO"
    assert uno(con, "SELECT COUNT(*) FROM notas_entrega") == 0
    r = nota(con, s, b["clientes"]["frial1"], "2026-09-13", "50.5", b["despacho"], medio="QR_ACH")
    assert (r["tipo"], r["documento"], r["total"]) == ("CONTADO", "FIN-ING-06 n.º 1", D("1186.75"))  # 50,5 × 23,50
    with pytest.raises(DatosInvalidosError):
        nota(con, s, b["clientes"]["frial1"], "2026-09-13", "10")                  # carne sin despacho


# ===================== PRUEBA 3: COBERTURA DEL LOTE ============================
def test_lote_sin_cobertura_completa_no_permite_cerrar_el_despacho(con, s):
    b = base(con, s)
    c, d = b["clientes"], b["despacho"]
    assert ventas.cobertura(con, d)["aves"] == 1000
    p1 = ventas.registrar_pedido(con, s["jf"], d, c["hotel"], 600, "2026-09-08")      # 5 días: cumple
    p2 = ventas.registrar_pedido(con, s["jf"], d, c["eco"], 100, "2026-09-12")        # 1 día: no cumple
    assert (p1["cumple"], p1["anticipacion"]) == (True, 5)
    assert (p2["cumple"], p2["activa_respaldo"]) == (False, True)
    ventas.registrar_pedido(con, s["jf"], d, c["frial1"], 300, "2026-09-11")
    with pytest.raises(ReglaNegocioError) as e:
        ventas.cerrar_despacho(con, s["jf"], d, "2026-09-13")
    assert e.value.codigo == "SIN_VERIFICACION"
    # 1.000 aves cubiertas, pero con un solo frial (CI-FIN-13 exige 2).
    v = ventas.verificar_cobertura(con, s["jf"], d, "2026-09-12")
    assert (v["total"], v["compradores_indicativos"], v["cubre"], v["en_plazo"]) == (1000, 1, False, True)
    assert "Gerencia General" in v["mensaje"]
    with pytest.raises(ReglaNegocioError) as e:
        ventas.cerrar_despacho(con, s["jf"], d, "2026-09-13")
    assert e.value.codigo == "COBERTURA_INCOMPLETA"
    # Con un segundo comprador pero 50 aves menos: sigue incompleta.
    ventas.anular_pedido(con, s["jf"], p1["id"], "El hotel reduce su pedido")
    ventas.registrar_pedido(con, s["jf"], d, c["hotel"], 550, "2026-09-08")
    ventas.registrar_pedido(con, s["jf"], d, c["frial2"], 1, "2026-09-12")
    v = ventas.verificar_cobertura(con, s["jf"], d, "2026-09-12")
    assert (v["total"], v["remanente"], v["cubre"]) == (951, 49, False)
    with pytest.raises(ReglaNegocioError) as e:
        ventas.cerrar_despacho(con, s["jf"], d, "2026-09-13")
    assert e.value.codigo == "COBERTURA_INCOMPLETA"
    ventas.registrar_pedido(con, s["jf"], d, c["mercado"], 49, "2026-09-12")
    v = ventas.verificar_cobertura(con, s["jf"], d, "2026-09-12")
    assert (v["total"], v["cobertura"], v["cubre"], v["respaldo_requerido"]) == (1000, D("1.0000"), True, True)
    with pytest.raises(ReglaNegocioError) as e:
        ventas.cerrar_despacho(con, s["jf"], d, "2026-09-13")
    assert e.value.codigo == "RESPALDO_NO_ACTIVADO"
    with pytest.raises(AutorizacionError):
        ventas.activar_respaldo(con, s["jf"], d, "HORECA insuficiente")
    ventas.activar_respaldo(con, s["gg"], d, "HORECA confirmó 550 aves; respaldo con friales y mercado")
    ventas.cerrar_despacho(con, s["jf"], d, "2026-09-13")
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, c["frial1"], "2026-09-13", "100", d)
    assert e.value.codigo == "DESPACHO_CERRADO"
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE despachos SET aves = 1 WHERE id = ?", (d,))


# ===================== VENTA, COSTO, GALLINAZA Y PARTE DIARIO ==================
def test_venta_con_costo_al_cierre_gallinaza_y_parte_diario(con, s):
    cid, lotes, _ = ciclo_completo_2026(con, s)
    fondo(con, s, "2026-09-01")
    r0 = tarifario(con, s)
    assert (r0["costo_referencia"], r0["origen_costo"]) == (D("14.91"), "PRESUPUESTO_D4")   # 116.286 ÷ 7.800 kg
    c = clientes(con, s)
    d = ventas.crear_despacho(con, s["jf"], lotes[0], "2026-09-13")["id"]
    venta = nota(con, s, c["hotel"], "2026-09-13", "1000", d, medio="TRANSFERENCIA")
    assert venta["costo"] is None and len(ventas.costos_pendientes(con, s["jf"])) == 1
    gall = nota(con, s, c["agricultor"], "2026-09-13", "1", producto="GALLINAZA", medio="EFECTIVO",
                comprobante="REC-0001")
    assert (gall["total"], gall["costo"]) == (D("500.00"), None)
    assert tesoreria.saldo_cuenta(con, "5.2.01.04") == D("-500.00")
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, c["agricultor"], "2026-09-13", "10", producto="EXCEDENTE", medio="EFECTIVO")
    assert e.value.codigo == "EXCEDENTE_NO_VIGENTE"                                # alimento propio desde 2027
    # Parte diario: 23.500 bancarizados + 500 en efectivo depositados.
    parte = ventas.preparar_parte(con, s["jf"], "2026-09-13")
    assert (parte["carne"], parte["gallinaza"], parte["total"], parte["efectivo"]) == \
        (D("23500.00"), D("500.00"), D("24000.00"), D("500.00"))
    with pytest.raises(DatosInvalidosError):
        ventas.registrar_parte(con, s["jf"], "2026-09-13", None)
    p = ventas.registrar_parte(con, s["jf"], "2026-09-13", "BOL-0913")
    assert tesoreria.saldo_cuenta(con, "1.1.01.01") == D("0.00")
    ventas.verificar_parte(con, s["gg"], p["id"])
    with pytest.raises(ReglaNegocioError):
        ventas.registrar_parte(con, s["jf"], "2026-09-13", "BOL-0913")
    # Cierre del ciclo y costo de ventas pendiente al CPP (116.286,28 ÷ 7.800 kg).
    costos.cerrar_ciclo(con, s["jf"], cid)
    with pytest.raises(ReglaNegocioError):
        ventas.contabilizar_costos_pendientes(con, s["jf"], "2026-09-12")
    r = ventas.contabilizar_costos_pendientes(con, s["jf"], "2026-09-16")
    assert (r["notas"], r["costo"]) == (1, D("14908.50"))
    assert tesoreria.saldo_cuenta(con, "6.1.02.01") == D("14908.50")
    # Después del cierre el costo se registra con la nota.
    otra = nota(con, s, c["frial1"], "2026-09-17", "200", d, medio="QR_ACH")
    assert otra["costo"] == D("2981.70")
    assert ventas.costo_referencia(con, "2026-09-30")[:2] == (D("14.91"), "CICLO_CERRADO")


# ===================== COBRANZA Y SUSPENSIÓN (CI-FIN-15; CIR-ING-01) ===========
def test_cobranza_reporte_24_horas_y_suspension_automatica(con, s):
    b = base(con, s)
    c, d = b["clientes"], b["despacho"]
    hotel, eco = c["hotel"], c["eco"]
    a = nota(con, s, hotel, "2026-09-13", "100", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 1")
    a2 = nota(con, s, hotel, "2026-09-14", "100", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 2")
    # El eco-lodge paga a tiempo: no acumula incumplimientos.
    e1 = nota(con, s, eco, "2026-09-13", "50", d, medio="TRANSFERENCIA", dias=1, comprobante="FIN-ING-04 n.º 3")
    r = ventas.registrar_cobro(con, s["jf"], e1["cuenta_id"], "2026-09-14", "1175", "TRANSFERENCIA", "CI-0001")
    assert (r["en_plazo"], r["completa"]) == (True, True)
    with pytest.raises(ReglaNegocioError) as e:
        ventas.registrar_cobro(con, s["jf"], a["cuenta_id"], "2026-09-14", "100", "CHEQUE_DIFERIDO", "x")
    assert e.value.codigo == "CHEQUE_DIFERIDO"
    rev = ventas.revisar_cartera(con, s["jf"], "2026-09-16")
    assert set(rev["vencidas"]) == {a["cuenta_id"], a2["cuenta_id"]} and len(rev["suspensiones"]) == 1
    filas = {f["id"]: f for f in ventas.cartera(con, s["jf"], "2026-09-16")}
    assert (filas[a["cuenta_id"]]["reporte_en_plazo"], filas[a2["cuenta_id"]]["reporte_en_plazo"]) == (0, 1)
    assert filas[a["cuenta_id"]]["dias_mora"] == 2 and filas[a["cuenta_id"]]["incumplimientos"] == 2
    assert uno(con, "SELECT estado_credito FROM clientes WHERE id = ?", hotel) == "SUSPENDIDO"
    assert ventas.incumplimientos_consecutivos(con, eco) == 0
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, hotel, "2026-09-16", "100", d, medio="EFECTIVO", dias=1)
    assert e.value.codigo == "CREDITO_SUSPENDIDO"
    nota(con, s, hotel, "2026-09-16", "100", d, medio="TRANSFERENCIA")              # contado sí
    circular = ventas.listar_suspensiones(con, s["gg"])[0]
    assert (circular["numero"], len(circular["detalle"])) == (1, 2)
    with pytest.raises(AutorizacionError):
        ventas.firmar_suspension(con, s["jf"], circular["id"])
    ventas.firmar_suspension(con, s["gg"], circular["id"])
    # Cobro tardío: queda incumplido; la GG restablece la línea y la serie vuelve a cero.
    r = ventas.registrar_cobro(con, s["jf"], a["cuenta_id"], "2026-09-17", "2350", "TRANSFERENCIA", "CI-0002")
    assert r["en_plazo"] is False
    ventas.restablecer_credito(con, s["gg"], hotel, "2026-09-17", "Canceló la deuda vencida")
    assert ventas.incumplimientos_consecutivos(con, hotel) == 0
    nota(con, s, hotel, "2026-09-18", "10", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 5")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE cuentas_cobrar SET incumplida = 0 WHERE id = ?", (a["cuenta_id"],))


# ===================== DEVOLUCIÓN POR INOCUIDAD ===============================
def test_devolucion_por_rechazo_de_inocuidad(con, s):
    b = base(con, s)
    c, d = b["clientes"], b["despacho"]
    credito = nota(con, s, c["hotel"], "2026-09-13", "100", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 1")
    dv = ventas.registrar_devolucion(con, s["jf"], credito["id"], "2026-09-13", "40", "Carcasas con olor anormal",
                                     "Informe CI-FIN-12 n.º 1")
    assert (dv["monto"], dv["a_cuenta_cobrar"], dv["saldo_favor"]) == (D("940.00"), D("940.00"), D("0.00"))
    assert tesoreria.saldo_cuenta(con, "5.1.04.01") == D("940.00")
    r = ventas.registrar_cobro(con, s["jf"], credito["cuenta_id"], "2026-09-14", "1410", "TRANSFERENCIA", "CI-0001")
    assert r["completa"] is True
    with pytest.raises(ReglaNegocioError):
        ventas.registrar_devolucion(con, s["jf"], credito["id"], "2026-09-14", "61", "x", "y")
    contado = nota(con, s, c["frial1"], "2026-09-13", "10", d, medio="QR_ACH")
    dv2 = ventas.registrar_devolucion(con, s["jf"], contado["id"], "2026-09-13", "10", "Rechazo en recepción",
                                      "Informe CI-FIN-12 n.º 2")
    assert (dv2["monto"], dv2["saldo_favor"]) == (D("235.00"), D("235.00"))
    assert tesoreria.saldo_cuenta(con, "2.1.04.01") == D("-235.00")


# ===================== MARGEN POR CANAL (CI-FIN-06; FIN-ING-08) ================
def test_margen_bajo_dos_meses_seguidos_exige_ajuste(con, s):
    cid, lotes, _ = ciclo_completo_2026(con, s)
    lineas = [("CARNE_ENTERO", "HORECA", "23.50"), ("CARNE_ENTERO", "FRIAL", "18.00"), ("GALLINAZA", "TODOS", "500")]
    t = tarifario(con, s, lineas=lineas)
    det = ventas.detalle_tarifario(con, s["gg"], t["id"])
    frial = next(l for l in det["lineas"] if l["canal"] == "FRIAL")
    assert (frial["margen"], frial["bajo_umbral"]) == (D("0.1717"), True)           # (18 − 14,91) ÷ 18
    c = clientes(con, s)
    d = ventas.crear_despacho(con, s["jf"], lotes[0], "2026-09-13")["id"]
    with pytest.raises(ReglaNegocioError) as e:
        ventas.calcular_margen(con, s["gg"], 2026, 8)
    assert e.value.codigo == "SIN_COSTO_CICLO"
    with pytest.raises(ReglaNegocioError) as e:                                     # mercado sin precio propio
        nota(con, s, c["mercado"], "2026-09-14", "100", d, medio="QR_ACH")
    assert e.value.codigo == "SIN_PRECIO"
    costos.cerrar_ciclo(con, s["jf"], cid)
    nota(con, s, c["hotel"], "2026-09-17", "100", d)
    nota(con, s, c["frial1"], "2026-09-17", "100", d, medio="QR_ACH")
    nota(con, s, c["frial2"], "2026-10-05", "100", d, medio="QR_ACH")
    nota(con, s, c["hotel"], "2026-10-05", "100", d)
    sep = ventas.registrar_analisis_margen(con, s["gg"], 2026, 9)
    margenes = {l["canal"]: l for l in sep["lineas"]}
    assert (margenes["HORECA"]["margen"], margenes["FRIAL"]["margen"]) == (D("0.3655"), D("0.1717"))
    assert sep["ajuste_requerido"] is False and margenes["MERCADO_POPULAR"]["margen"] is None
    octubre = ventas.registrar_analisis_margen(con, s["gg"], 2026, 10)
    assert (octubre["ajuste_requerido"], octubre["canales_ajuste"]) == (True, ["FRIAL"])
    assert ventas.ajuste_tarifario_pendiente(con) is not None
    analisis = ventas.listar_analisis(con, s["gg"])[0]
    datos = pdf.fin_ing_08(analisis, pdf.Contexto(fecha="2026-11-01", elaborado_por="GG", cargo="Gerente General"))
    assert b"P\\341gina 1 de" in datos
    with pytest.raises(ReglaNegocioError):
        ventas.registrar_analisis_margen(con, s["gg"], 2026, 10)
    tarifario(con, s, "2026-11-01", [("CARNE_ENTERO", "TODOS", "23.50"), ("GALLINAZA", "TODOS", "500")])
    assert ventas.ajuste_tarifario_pendiente(con) is None
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE tarifario_lineas SET precio_cent = 1")


# ===================== CRÉDITO DESDE 2028 Y PROVISIÓN ==========================
def test_credito_2028_con_resolucion_y_provision_de_incobrables(con, s):
    b = base(con, s)
    c, d = b["clientes"], b["despacho"]
    hotel = c["hotel"]
    nota(con, s, hotel, "2026-09-13", "2000", d)                                    # historial e ingresos del ciclo
    nota(con, s, hotel, "2028-06-05", "100", d)
    nota(con, s, hotel, "2028-06-12", "100", d)
    with pytest.raises(ReglaNegocioError) as e:
        ventas.calcular_provision(con, s["jf"], "2027-06-30")
    assert e.value.codigo == "PROVISION_NO_VIGENTE"
    with pytest.raises(AutorizacionError) as e:
        nota(con, s, hotel, "2028-07-10", "100", d, medio="EFECTIVO", dias=5, comprobante="FIN-ING-04 n.º 9")
    assert e.value.codigo == "RESOLUCION_REQUERIDA"
    with pytest.raises(ReglaNegocioError) as e:
        nota(con, s, hotel, "2028-07-10", "100", d, medio="EFECTIVO", dias=8, comprobante="FIN-ING-04 n.º 9")
    assert e.value.codigo == "CREDITO_EXCEDE_PLAZO"
    with pytest.raises(ReglaNegocioError) as e:                                    # > 1 entrega semanal (2.350)
        ventas.autorizar_credito(con, s["gg"], hotel, "2028-07-01", 7, "5000", "Cliente con 21 meses de historial")
    assert e.value.codigo == "MONTO_EXCEDE_ENTREGA"
    with pytest.raises(ReglaNegocioError) as e:
        ventas.autorizar_credito(con, s["gg"], c["eco"], "2028-07-01", 7, "100", "Sin compras")
    assert e.value.codigo == "HISTORIAL_INSUFICIENTE"
    res = ventas.autorizar_credito(con, s["gg"], hotel, "2028-07-01", 7, "2350", "Cliente con 21 meses de historial")
    assert (res["historial_meses"], res["entrega_semanal"]) == (21, D("2350.00"))
    resolucion = ventas.listar_resoluciones(con, s["gg"])[0]
    datos = pdf.fin_ing_02(resolucion, pdf.Contexto(fecha="2028-07-01", elaborado_por="GG", cargo="Gerente General"))
    assert b"P\\341gina 1 de" in datos
    cred = nota(con, s, hotel, "2028-07-10", "100", d, medio="EFECTIVO", dias=5, comprobante="FIN-ING-04 n.º 9")
    assert cred["vencimiento"].isoformat() == "2028-07-15"
    otra = nota(con, s, hotel, "2028-08-18", "50", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 10")
    assert otra["total"] == D("1175.00")
    # 2.350 con 36 días de mora al 100 % + 1.175 al día al 5 % = 2.408,75.
    prov = ventas.calcular_provision(con, s["jf"], "2028-08-20")
    assert (prov["saldo_deudores"], prov["requerida"], prov["ajuste"]) == (D("3525.00"), D("2408.75"), D("2408.75"))
    r = ventas.registrar_provision(con, s["jf"], "2028-08-20")
    assert r["asiento_id"] and tesoreria.saldo_cuenta(con, "4.1.01.01") == D("-2408.75")
    assert tesoreria.saldo_cuenta(con, "7.1.02.08") == D("2408.75")


# ===================== SEGREGACIÓN, TARIFARIO Y FORMATOS =======================
def test_segregacion_y_tarifario_conjunto(con, s):
    with pytest.raises(AutorizacionError):
        ventas.proponer_tarifario(con, s["jf"], "2026-07-01", [("CARNE_ENTERO", "TODOS", "23.50")], "x")
    r = ventas.proponer_tarifario(con, s["gg"], "2026-07-01", ventas.lineas_desde_presupuesto(con, "2026-07-01"),
                                  "Precios del presupuesto")
    assert ventas.tarifario_vigente(con, "2026-07-15") is None                      # sin aprobación del CA
    with pytest.raises(AutorizacionError):
        ventas.resolver_tarifario(con, s["gg"], r["id"], "APRUEBA", "Acta")
    ventas.resolver_tarifario(con, s["ca_presidente"], r["id"], "APRUEBA", "Acta CA 1/2026")
    assert ventas.precio_vigente(con, "CARNE_ENTERO", "HORECA", "2026-07-15")[0] == D("23.50")
    with pytest.raises(ReglaNegocioError):
        ventas.precio_vigente(con, "EXCEDENTE", "TODOS", "2026-07-15")               # el alimento rige desde 2027
    with pytest.raises(AutorizacionError):
        ventas.registrar_cliente(con, s["gg"], "HORECA", "Hotel", "2026-07-01")
    with pytest.raises(AutorizacionError):
        ventas.registrar_cliente(con, s["ut"], "HORECA", "Hotel", "2026-07-01")
    ventas.registrar_cliente(con, s["jf"], "HORECA", "Hotel Uno", "2026-07-01", documento="111")
    with pytest.raises(ReglaNegocioError):
        ventas.registrar_cliente(con, s["jf"], "HORECA", "Hotel Dos", "2026-07-01", documento="111")


def test_formatos_pdf(con, s):
    b = base(con, s)
    c, d = b["clientes"], b["despacho"]
    ventas.registrar_pedido(con, s["jf"], d, c["hotel"], 600, "2026-09-08")
    credito = nota(con, s, c["hotel"], "2026-09-13", "100", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 1")
    nota(con, s, c["hotel"], "2026-09-14", "100", d, medio="EFECTIVO", dias=1, comprobante="FIN-ING-04 n.º 2")
    contado = nota(con, s, c["frial1"], "2026-09-13", "100", d, medio="QR_ACH")
    ventas.revisar_cartera(con, s["jf"], "2026-09-16")
    ctx = pdf.Contexto(fecha="2026-09-16", elaborado_por="Jefe de Finanzas", cargo="JF", ciclo="1 (2026)")
    documentos = [
        pdf.fin_ing_01(ventas.listar_pedidos(con, s["jf"], d), ctx),
        pdf.fin_ing_03(ventas.obtener_nota(con, s["jf"], credito["id"]), ctx),
        pdf.fin_ing_04(ventas.obtener_nota(con, s["jf"], credito["id"]), ctx),
        pdf.fin_ing_05(ventas.cartera(con, s["jf"], "2026-09-16"), ctx),
        pdf.fin_ing_06(ventas.obtener_nota(con, s["jf"], contado["id"]), ctx),
        pdf.fin_ing_07(ventas.preparar_parte(con, s["jf"], "2026-09-13"), ctx),
        pdf.cir_ing_01(ventas.listar_suspensiones(con, s["gg"])[0], ctx),
        pdf.cobertura_ci_fin_13(ventas.cobertura(con, d), ventas.listar_despachos(con, s["jf"])[0], ctx),
        pdf.tarifario(ventas.detalle_tarifario(con, s["gg"], 1), ctx),
    ]
    for doc in documentos:
        assert doc.startswith(b"%PDF") and b"P\\341gina 1 de" in doc, documentos.index(doc)
