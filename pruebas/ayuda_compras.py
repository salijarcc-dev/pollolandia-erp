"""Utilidades compartidas por las pruebas de compras e inventarios (Fase 4).

Fechas del ejercicio 2026 (1-jul-2026 a 30-jun-2027): lunes 6, 13, 20 y 27 de
julio; viernes 10 y 17 de julio. Ciclo 1: 1-jul a 11-sep-2026 (POA).
"""
from __future__ import annotations

from decimal import Decimal

from servicios import compras, seguridad, tesoreria
from servicios.compras import CotizacionOC, LineaOC, LineaRecepcion

BANCO = "1.1.01.03"
LUNES = "2026-07-06"
LUNES_2 = "2026-07-13"
MARTES = "2026-07-07"
MIERCOLES = "2026-07-08"
VIERNES = "2026-07-10"
D = Decimal


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def insumo(con, codigo: str) -> int:
    return int(uno(con, "SELECT id FROM insumos WHERE codigo = ?", codigo))


def depositar(con, s, monto, fecha="2026-07-02"):
    """Ventas depositadas para que el saldo supere el 120 % del SME."""
    return tesoreria.registrar_deposito(con, s["jf"], fecha, monto, BANCO, "5.1.01.01", "Venta de carne de pollo")


def proveedor(con, s, nombre: str, nit: str) -> int:
    pid, estado = compras.registrar_proveedor(con, s["jf"], "EMPRESA", "2026-07-01", nombre=nombre, nit=nit,
                                              producto="Insumos avícolas")
    assert estado == "HABILITADO"
    return pid


def tres_proveedores(con, s) -> tuple[int, int, int]:
    return (proveedor(con, s, "Agroveterinaria Caranavi S.R.L.", "1020304011"),
            proveedor(con, s, "Insumos Yungas Ltda.", "2030405022"),
            proveedor(con, s, "Avícola La Paz S.A.", "3040506033"))


def cotizaciones(provs, total) -> list[CotizacionOC]:
    """Cotización ganadora (el primero) y dos más altas."""
    total = D(str(total))
    return [CotizacionOC(provs[0], total, "COT-001"), CotizacionOC(provs[1], total + 100, "COT-002"),
            CotizacionOC(provs[2], total + 250, "COT-003")]


def firmar_compra(con, s, autorizacion_id):
    assert seguridad.firmar_autorizacion(con, s["gg"], autorizacion_id) == "AUTORIZADA"


def comprar(con, s, codigo: str, cantidad, precio, fecha=LUNES, provs=None, *, recibir=True, contabilizar=True,
            fecha_recepcion=None, emergencia_id=None, condicion_pago="CONTADO", dias_credito=0, destino="ACTUAL",
            vencimiento=None):
    """Flujo completo: OC preparada por el JF, firmada y emitida por la GG,
    recibida por la UT y contabilizada por el JF."""
    provs = provs or tres_proveedores(con, s)
    total = (D(str(cantidad)) * D(str(precio))).quantize(D("0.01"))
    r = compras.preparar_orden(con, s["jf"], provs[0], fecha, [LineaOC(insumo(con, codigo), cantidad, precio)],
                               cotizaciones=cotizaciones(provs, total) if total > 5000 else (),
                               emergencia_id=emergencia_id, condicion_pago=condicion_pago, dias_credito=dias_credito,
                               destino_ciclo=destino)
    firmar_compra(con, s, r["autorizacion_id"])
    compras.emitir_orden(con, s["gg"], r["orden_id"], fecha)
    resultado = {"orden": r, "provs": provs}
    if recibir:
        linea = int(uno(con, "SELECT id FROM oc_lineas WHERE orden_id = ?", r["orden_id"]))
        rid = compras.registrar_recepcion(con, s["ut"], r["orden_id"], fecha_recepcion or fecha, "FAC-" + str(linea),
                                          [LineaRecepcion(linea, cantidad, True, vencimiento)])
        resultado["recepcion_id"] = rid
        if contabilizar:
            resultado["contable"] = compras.contabilizar_recepcion(con, s["jf"], rid)
    return resultado


def autorizar_desembolso(con, s, monto, fecha="2026-07-06"):
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, "Pago a proveedor", ctx)
    estado = seguridad.firmar_autorizacion(con, s["gg"], aid)
    if estado == "PENDIENTE":
        estado = seguridad.firmar_autorizacion(con, s["cv"], aid)
    assert estado == "AUTORIZADA"
    return aid
