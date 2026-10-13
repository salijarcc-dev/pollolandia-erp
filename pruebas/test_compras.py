"""Pruebas de servicios/compras.py (Fase 4).

Cifras de los documentos:
* PR-FIN-03 / FIN-EGR-03: OC solo los lunes; 3 cotizaciones sobre Bs 5.000.
* Manual Presupuestario D.4-1 (2026, por ciclo): alimento Bs 55.500
  (300 qq × Bs 185), pollitos Bs 27.000 (3.000 × Bs 9), veterinarios
  Bs 5.895, crianza Bs 2.910.
* POL-04 Art. 2: saldo proyectado bajo el 120 % del SME (Bs 163.695,60 en
  2026) suspende el compromiso; saldo de apertura Bs 138.010.
* CI-FIN-09: GG sobre Bs 1.000; firma mancomunada GG + CV sobre Bs 5.000.
* CAC: Malo < 90 %, Normal 90-99 %, Bueno 100 %.
"""
from __future__ import annotations

import pytest

from pruebas.ayuda_compras import (
    BANCO, LUNES, LUNES_2, MARTES, MIERCOLES, D, autorizar_desembolso, comprar, cotizaciones, depositar, insumo,
    tres_proveedores, uno,
)
from reportes import formatos_compras as pdf
from servicios import compras, contable, inventarios, seguridad, tesoreria
from servicios.compras import CotizacionOC, LineaOC, LineaRecepcion
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError


# ============================ CRITERIOS DEL MASTER PLAN ======================
def test_oc_en_martes_rechazada(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], MARTES, [LineaOC(insumo(con, "INS-0309"), 10, 50)])
    assert e.value.codigo == "OC_DIA_NO_PERMITIDO" and "lunes" in str(e.value)
    # Preparada para el lunes, la GG no puede emitirla un martes.
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 10, 50)])
    seguridad.firmar_autorizacion(con, s["gg"], r["autorizacion_id"])
    with pytest.raises(ReglaNegocioError) as e:
        compras.emitir_orden(con, s["gg"], r["orden_id"], MARTES)
    assert e.value.codigo == "OC_DIA_NO_PERMITIDO"
    assert compras.emitir_orden(con, s["gg"], r["orden_id"], LUNES) == r["numero"]


def test_compra_5001_sin_tres_cotizaciones_rechazada(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    vet = insumo(con, "INS-0308")
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(vet, 1, "5001.00")],
                               cotizaciones=cotizaciones(provs, "5001")[:2])
    assert e.value.codigo == "COTIZACIONES_INSUFICIENTES" and e.value.detalle == {"minimo": 3, "presentadas": 2}
    with pytest.raises(ReglaNegocioError):
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(vet, 1, "5001.00")])
    # Bs 5.000,00 exactos no superan el umbral: no exige cotizaciones.
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(vet, 1, "5000.00")])
    assert r["total"] == D("5000.00")
    assert uno(con, "SELECT COUNT(*) FROM oc_cotizaciones") == 0


def test_tres_cotizaciones_del_mismo_proveedor_o_sin_justificacion(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    vet = insumo(con, "INS-0308")
    repetidas = [CotizacionOC(provs[0], 5001, "A"), CotizacionOC(provs[0], 5100, "B"), CotizacionOC(provs[1], 5200, "C")]
    with pytest.raises(DatosInvalidosError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(vet, 1, "5001")], cotizaciones=repetidas)
    assert e.value.codigo == "COTIZACION_DUPLICADA"
    caro = [CotizacionOC(provs[0], 5300, "A"), CotizacionOC(provs[1], 5001, "B"), CotizacionOC(provs[2], 5200, "C")]
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(vet, 1, "5300")], cotizaciones=caro)
    assert e.value.codigo == "JUSTIFICACION_REQUERIDA"
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(vet, 1, "5300")], cotizaciones=caro,
                               justificacion_seleccion="Entrega en 24 horas con cadena de frío")
    assert r["total"] == D("5300.00")


# ============================== FLUJO COMPLETO ===============================
def test_flujo_completo_oc_recepcion_asiento_programacion_y_pago(con, s):
    depositar(con, s, "100000")
    res = comprar(con, s, "INS-0302", 100, 185)
    orden = compras.obtener_orden(con, s["jf"], res["orden"]["orden_id"])
    assert orden["estado"] == "RECIBIDA" and orden["etiqueta"] == "OC-2026-0001" and orden["emitida_puntual"] == 1
    assert orden["cuenta_por_pagar"] == "2.1.01.02" and orden["categoria_pago"] == "ALIMENTO_VETERINARIOS_CICLO"
    # Asiento automático: inventario 1.1.03.02 contra cuenta por pagar 2.1.01.02.
    asiento = contable.obtener_asiento(con, s["jf"], res["contable"]["asiento_id"])
    lineas = {(l["cuenta"], l["debe"], l["haber"]) for l in asiento["lineas"]}
    assert lineas == {("1.1.03.02", D("18500.00"), D("0.00")), ("2.1.01.02", D("0.00"), D("18500.00"))}
    assert tesoreria.saldo_cuenta(con, "1.1.03.02") == D("18500.00")
    saldo = inventarios.saldo_insumo(con, insumo(con, "INS-0302"))
    assert (saldo.cantidad, saldo.valor, saldo.cpp) == (D("100.000"), D("18500.00"), D("185.0000"))
    # Programación FIN-EGR-05 y pago con firma mancomunada (> Bs 5.000).
    cxp = compras.listar_cuentas_por_pagar(con, s["jf"])[0]
    assert (cxp["monto"], cxp["estado"], cxp["vencimiento"]) == (D("18500.00"), "PENDIENTE", LUNES)
    pid = compras.preparar_programacion(con, s["jf"], LUNES)
    prog = compras.obtener_programacion(con, s["jf"], pid)
    assert prog["lineas"][0]["requiere_mancomunada"] == 1 and prog["lineas"][0]["pagable"] == 1
    assert prog["lineas"][0]["nivel"] == 3
    with pytest.raises(AutorizacionError) as e:
        compras.pagar_cuenta_por_pagar(con, s["jf"], cxp["id"], LUNES, BANCO)
    assert e.value.codigo == "CXP_NO_PROGRAMADA"
    with pytest.raises(AutorizacionError):
        compras.autorizar_programacion(con, s["jf"], pid)
    compras.autorizar_programacion(con, s["gg"], pid)
    with pytest.raises(AutorizacionError) as e:
        compras.pagar_cuenta_por_pagar(con, s["jf"], cxp["id"], LUNES, BANCO)
    assert e.value.codigo == "DESEMBOLSO_SIN_AUTORIZACION"
    aid = autorizar_desembolso(con, s, "18500")
    banco_antes = tesoreria.saldo_bancario(con)
    compras.pagar_cuenta_por_pagar(con, s["jf"], cxp["id"], LUNES, BANCO, autorizacion_id=aid)
    assert tesoreria.saldo_bancario(con) == banco_antes - D("18500.00")
    assert tesoreria.saldo_cuenta(con, "2.1.01.02") == D("0.00")
    assert uno(con, "SELECT estado FROM compromisos WHERE id = ?", orden["compromiso_id"]) == "PAGADO"
    assert uno(con, "SELECT estado FROM cuentas_por_pagar WHERE id = ?", cxp["id"]) == "PAGADA"
    assert contable.asientos_descuadrados(con) == []


def test_validar_compromiso_suspende_la_oc_bajo_120_por_ciento_del_sme(con, s):
    # Saldo de apertura Bs 138.010 < 120 % del SME 2026 (Bs 163.695,60).
    provs = tres_proveedores(con, s)
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 10, 50)])
    assert r["suspendida"] and "SUSPENDIDO" in r["mensaje_sme"] and r["autorizacion_compromiso_id"]
    seguridad.firmar_autorizacion(con, s["gg"], r["autorizacion_id"])
    with pytest.raises(AutorizacionError) as e:
        compras.emitir_orden(con, s["gg"], r["orden_id"], LUNES)
    assert e.value.codigo == "COMPROMISO_SUSPENDIDO"
    # Autorización escrita de la GG (exige documento) y confirmación de Finanzas.
    with pytest.raises(DatosInvalidosError):
        seguridad.firmar_autorizacion(con, s["gg"], r["autorizacion_compromiso_id"])
    seguridad.firmar_autorizacion(con, s["gg"], r["autorizacion_compromiso_id"], documento_ref="Resolución GG 05/2026")
    tesoreria.confirmar_compromiso(con, s["jf"], r["compromiso_id"])
    compras.emitir_orden(con, s["gg"], r["orden_id"], LUNES)
    assert uno(con, "SELECT estado FROM ordenes_compra WHERE id = ?", r["orden_id"]) == "EMITIDA"


def test_segregacion_jf_no_emite_y_gg_no_prepara(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    with pytest.raises(AutorizacionError):
        compras.preparar_orden(con, s["gg"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 10, 50)])
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 10, 50)])
    with pytest.raises(AutorizacionError):
        compras.emitir_orden(con, s["jf"], r["orden_id"], LUNES)
    with pytest.raises(AutorizacionError) as e:  # sin la firma de la GG en CI-FIN-09
        compras.emitir_orden(con, s["gg"], r["orden_id"], LUNES)
    assert e.value.codigo == "AUTORIZACION_INCOMPLETA"


def test_emision_por_suplente_ci_fin_17(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 10, 50)])
    seguridad.firmar_autorizacion(con, s["ca_presidente"], r["autorizacion_id"],
                                  suplencia=seguridad.Suplencia("Viaje de la GG a La Paz", "Constancia 01/2026"))
    with pytest.raises(AutorizacionError):
        compras.emitir_orden(con, s["ca_secretario"], r["orden_id"], LUNES)
    compras.emitir_orden(con, s["ca_presidente"], r["orden_id"], LUNES)
    assert uno(con, "SELECT emitida_por FROM ordenes_compra WHERE id = ?", r["orden_id"]) == "gcapia"


# ============================== PROVEEDORES RAU ==============================
def test_pequeno_productor_sin_certificado_queda_bloqueado(con, s):
    depositar(con, s, "100000")
    pid, estado = compras.registrar_proveedor(con, s["jf"], "PEQUENO_PRODUCTOR", "2026-07-01",
                                              nombre="Juan Mamani Quispe", ci="4567890", comunidad="Alto Beni",
                                              hectareas="3.5", producto="Afrecho")
    assert estado == "OBSERVADO"
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], pid, LUNES, [LineaOC(insumo(con, "INS-0310"), 10, 50)])
    assert e.value.codigo == "PROVEEDOR_OBSERVADO" and "certificado RAU" in str(e.value)
    sin_ci, estado = compras.registrar_proveedor(con, s["jf"], "PEQUENO_PRODUCTOR", "2026-07-01",
                                                 nombre="Productora sin C.I.", tipo_certificado="NO_IMPONIBILIDAD",
                                                 nro_certificado="NI-77", certificado_vigente_hasta="2027-12-31")
    motivo = next(p["motivo_a_la_fecha"] for p in compras.listar_proveedores(con, s["jf"], "2026-07-01")
                  if p["id"] == sin_ci)
    assert estado == "OBSERVADO" and "C.I." in motivo
    # RAU sin copia del último pago sigue observado; con ella, habilitado.
    assert compras.actualizar_proveedor(con, s["jf"], pid, "2026-07-01", tipo_certificado="RAU",
                                        nro_certificado="RAU-123", certificado_vigente_hasta="2026-12-31") == "OBSERVADO"
    assert compras.actualizar_proveedor(con, s["jf"], pid, "2026-07-01",
                                        ultimo_pago_rau_ref="Form. 701 pagado 2025") == "HABILITADO"
    compras.preparar_orden(con, s["jf"], pid, LUNES, [LineaOC(insumo(con, "INS-0310"), 10, 50)])
    # Certificado vencido a la fecha de la compra: bloqueo.
    with pytest.raises(ReglaNegocioError) as e:
        compras.verificar_proveedor(con, pid, "2027-01-04")
    assert "venció" in str(e.value)
    with pytest.raises(DatosInvalidosError):
        compras.registrar_proveedor(con, s["jf"], "EMPRESA", nombre="Sin NIT")
    with pytest.raises(DatosInvalidosError) as e:
        compras.registrar_proveedor(con, s["jf"], "PEQUENO_PRODUCTOR", nombre="Duplicado", ci="4567890")
    assert e.value.codigo == "PROVEEDOR_DUPLICADO"


# =========================== PRESUPUESTO FIN-EGR-02 ==========================
def test_disponibilidad_presupuestaria_suspende_la_emision(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    crianza = insumo(con, "INS-0310")
    disp = compras.disponibilidad(con, LUNES, {"1.1.03.10": "2910"})
    assert disp["ciclo"] == 1 and disp["habilita"] and disp["lineas"][0]["aprobado"] == D("2910.00")
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(crianza, 1, "2910.01")])
    assert e.value.codigo == "PRESUPUESTO_INSUFICIENTE" and "FIN-EGR-02" in str(e.value)
    compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(crianza, 1, "2000")])
    rep = compras.reporte_disponibilidad(con, s["jf"], LUNES)
    fila = next(l for l in rep["lineas"] if l["cuenta"] == "1.1.03.10")
    assert (fila["ejecutado"], fila["saldo"]) == (D("2000.00"), D("910.00"))
    assert not compras.disponibilidad(con, LUNES, {"1.1.03.10": "910.01"})["habilita"]
    # Partida sin presupuesto (materiales de mantenimiento existe en 2026; saquillos no).
    assert not compras.disponibilidad(con, LUNES, {"1.1.03.12": "1"})["habilita"]


def test_ciclo_siguiente_tiene_su_propia_partida(con, s):
    depositar(con, s, "300000")
    provs = tres_proveedores(con, s)
    comprar(con, s, "INS-0302", 300, 185, provs=provs)                # Bs 55.500: agota el ciclo 1
    with pytest.raises(ReglaNegocioError):
        compras.preparar_orden(con, s["jf"], provs[0], LUNES_2, [LineaOC(insumo(con, "INS-0302"), 1, 185)])
    r = compras.preparar_orden(con, s["jf"], provs[0], "2026-09-14", [LineaOC(insumo(con, "INS-0302"), 1, 185)])
    assert uno(con, "SELECT ciclo FROM ordenes_compra WHERE id = ?", r["orden_id"]) == 2


# ================================ VALIDACIONES ===============================
def test_lineas_y_cuentas(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0308"), 1, 100),
                                                               LineaOC(insumo(con, "INS-0309"), 1, 100)])
    assert e.value.codigo == "OC_CUENTAS_MEZCLADAS"
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 2, 100),
                                                               LineaOC(insumo(con, "INS-0310"), 3, 100)])
    assert r["total"] == D("500.00")
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0303"), 10, "95.12")])
    assert e.value.codigo == "CUENTA_NO_HABILITADA"
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0312"), 1, 23.5)])
    assert e.value.codigo == "INSUMO_NO_COMPRABLE"
    with pytest.raises(DatosInvalidosError):
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [])
    with pytest.raises(DatosInvalidosError):
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 1, 0)])
    with pytest.raises(DatosInvalidosError):
        compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 1, 10)],
                               condicion_pago="CREDITO")


def test_recepcion_cantidad_y_calidad(con, s):
    depositar(con, s, "100000")
    res = comprar(con, s, "INS-0308", 20, 100, recibir=False)
    orden_id = res["orden"]["orden_id"]
    linea = int(uno(con, "SELECT id FROM oc_lineas WHERE orden_id = ?", orden_id))
    with pytest.raises(ReglaNegocioError) as e:
        compras.registrar_recepcion(con, s["ut"], orden_id, MIERCOLES, "FAC-9",
                                    [LineaRecepcion(linea, 21, True, "2027-06-30")])
    assert e.value.codigo == "RECEPCION_EXCEDE_OC"
    with pytest.raises(DatosInvalidosError):  # veterinario sin fecha de vencimiento
        compras.registrar_recepcion(con, s["ut"], orden_id, MIERCOLES, "FAC-9", [LineaRecepcion(linea, 5, True)])
    with pytest.raises(DatosInvalidosError):  # no conforme sin explicación
        compras.registrar_recepcion(con, s["ut"], orden_id, MIERCOLES, "FAC-9",
                                    [LineaRecepcion(linea, 5, False, "2027-06-30")])
    # Vence en 20 días (< 30, PC-07): se rechaza aunque la UT la marque conforme.
    rid = compras.registrar_recepcion(con, s["ut"], orden_id, MIERCOLES, "FAC-9",
                                      [LineaRecepcion(linea, 5, True, "2026-07-28")])
    detalle = compras.obtener_recepcion(con, s["jf"], rid)
    assert detalle["estado"] == "RECHAZADA" and detalle["lineas"][0]["aceptada"] == D("0.000")
    assert "PC-07" in detalle["lineas"][0]["observacion"]
    # Recepción parcial conforme: 12 de 20; luego 8 → RECIBIDA.
    rid = compras.registrar_recepcion(con, s["ut"], orden_id, MIERCOLES, "FAC-10",
                                      [LineaRecepcion(linea, 12, True, "2027-06-30")])
    with pytest.raises(ReglaNegocioError):  # el pendiente descuenta lo recibido sin contabilizar
        compras.registrar_recepcion(con, s["ut"], orden_id, MIERCOLES, "FAC-11",
                                    [LineaRecepcion(linea, 9, True, "2027-06-30")])
    assert compras.contabilizar_recepcion(con, s["jf"], rid)["estado_orden"] == "RECIBIDA_PARCIAL"
    rid2 = compras.registrar_recepcion(con, s["ut"], orden_id, "2026-07-09", "FAC-11",
                                       [LineaRecepcion(linea, 8, True, "2027-06-30")])
    with pytest.raises(AutorizacionError):  # la UT no contabiliza
        compras.contabilizar_recepcion(con, s["ut"], rid2)
    assert compras.contabilizar_recepcion(con, s["jf"], rid2)["estado_orden"] == "RECIBIDA"
    assert tesoreria.saldo_cuenta(con, "2.1.01.04") == D("-2000.00")


def test_cierre_con_faltante_y_anulacion_liberan_el_compromiso(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    res = comprar(con, s, "INS-0309", 10, 50, provs=provs, recibir=False)
    orden_id = res["orden"]["orden_id"]
    linea = int(uno(con, "SELECT id FROM oc_lineas WHERE orden_id = ?", orden_id))
    rid = compras.registrar_recepcion(con, s["ut"], orden_id, LUNES, "R-1", [LineaRecepcion(linea, 6, True)])
    compras.contabilizar_recepcion(con, s["jf"], rid)
    compras.cerrar_orden(con, s["jf"], orden_id, "El proveedor no tiene más stock")
    compromiso = uno(con, "SELECT compromiso_id FROM ordenes_compra WHERE id = ?", orden_id)
    assert uno(con, "SELECT estado FROM compromisos WHERE id = ?", compromiso) == "VIGENTE"   # aún se debe Bs 300
    r = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0310"), 1, 100)])
    compras.anular_orden(con, s["jf"], r["orden_id"], "Error de cantidad")
    assert uno(con, "SELECT estado FROM compromisos WHERE id = ?", r["compromiso_id"]) == "ANULADO"
    with pytest.raises(ReglaNegocioError):
        compras.anular_orden(con, s["jf"], orden_id, "Ya cerrada")


# =========================== JERARQUÍA Y FIRMAS ==============================
def test_programacion_respeta_jerarquia_y_firma_mancomunada(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    vet = comprar(con, s, "INS-0308", 30, 150, provs=provs, vencimiento="2027-06-30",
                  condicion_pago="CREDITO", dias_credito=15)                       # Bs 4.500, nivel 3
    bb = comprar(con, s, "INS-0301", 3000, 9, provs=provs)                         # Bs 27.000, nivel 2
    pid = compras.preparar_programacion(con, s["jf"], LUNES)
    lineas = compras.obtener_programacion(con, s["jf"], pid)["lineas"]
    assert [l["nivel"] for l in lineas] == [2, 3]
    assert [l["requiere_mancomunada"] for l in lineas] == [1, 0]
    compras.autorizar_programacion(con, s["gg"], pid)
    cxp_vet = vet["contable"]["cuenta_por_pagar_id"]
    cxp_bb = bb["contable"]["cuenta_por_pagar_id"]
    assert uno(con, "SELECT vencimiento FROM cuentas_por_pagar WHERE id = ?", cxp_vet) == "2026-07-21"
    with pytest.raises(ReglaNegocioError) as e:
        compras.pagar_cuenta_por_pagar(con, s["jf"], cxp_vet, LUNES, BANCO, autorizacion_id=autorizar_desembolso(
            con, s, "4500"))
    assert e.value.codigo == "JERARQUIA_PAGOS"
    # Firma mancomunada incompleta: solo la GG sobre Bs 27.000.
    ctx = seguridad.contexto_desde_bd(con, LUNES)
    incompleta = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", "27000", "Pollitos BB", ctx)
    seguridad.firmar_autorizacion(con, s["gg"], incompleta)
    with pytest.raises(AutorizacionError) as e:
        compras.pagar_cuenta_por_pagar(con, s["jf"], cxp_bb, LUNES, BANCO, autorizacion_id=incompleta)
    assert e.value.codigo == "FIRMA_MANCOMUNADA_INCOMPLETA"
    seguridad.firmar_autorizacion(con, s["cv"], incompleta)
    compras.pagar_cuenta_por_pagar(con, s["jf"], cxp_bb, LUNES, BANCO, autorizacion_id=incompleta)
    aut_vet = int(uno(con, "SELECT id FROM autorizaciones WHERE tipo_decision='DESEMBOLSO' AND estado='AUTORIZADA'"))
    compras.pagar_cuenta_por_pagar(con, s["jf"], cxp_vet, LUNES, BANCO, autorizacion_id=aut_vet)
    assert tesoreria.saldo_cuenta(con, "2.1.01.01") == D("0.00") and tesoreria.saldo_cuenta(con, "2.1.01.04") == D("0.00")
    mov = tesoreria.movimientos_banco(con, s["jf"])
    assert {m["categoria_pago"] for m in mov if m["tipo"] == "PAGO"} == {"POLLITOS_INSUMOS_NUEVO_CICLO",
                                                                         "ALIMENTO_VETERINARIOS_CICLO"}


# ==================================== CAC ====================================
def test_indicador_cac(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    a = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0309"), 1, 50)])
    b = compras.preparar_orden(con, s["jf"], provs[0], LUNES, [LineaOC(insumo(con, "INS-0310"), 1, 50)])
    for r in (a, b):
        seguridad.firmar_autorizacion(con, s["gg"], r["autorizacion_id"])
    compras.emitir_orden(con, s["gg"], a["orden_id"], LUNES)
    compras.emitir_orden(con, s["gg"], b["orden_id"], LUNES_2)        # una semana tarde
    cac = compras.indicador_cac(con, 2026, 7)
    assert (cac["programadas"], cac["puntuales"], cac["porcentaje"], cac["semaforo"]) == (2, 1, D("50.00"), "MALO")
    assert compras.indicador_cac(con, 2026, 8)["porcentaje"] is None
    assert [compras.semaforo_cac(con, LUNES, D(x)) for x in ("100", "99.99", "90", "89.99")] == \
        ["BUENO", "NORMAL", "NORMAL", "MALO"]
    with pytest.raises(ReglaNegocioError) as e:
        c = compras.preparar_orden(con, s["jf"], provs[0], LUNES_2, [LineaOC(insumo(con, "INS-0309"), 1, 50)])
        seguridad.firmar_autorizacion(con, s["gg"], c["autorizacion_id"])
        compras.emitir_orden(con, s["gg"], c["orden_id"], LUNES)
    assert e.value.codigo == "OC_ANTES_DE_PROGRAMADA"


# =================================== PDFs ====================================
def test_formatos_pdf_con_bloque_de_firmas(con, s):
    depositar(con, s, "100000")
    res = comprar(con, s, "INS-0302", 100, 185)
    ctx = pdf.Contexto(fecha=inventarios.ciclo_de_fecha(con, LUNES)["inicio"], elaborado_por="Jefe de Finanzas",
                       cargo="Jefe de Finanzas y Comercialización", ciclo="Ciclo 1")
    pid = compras.preparar_programacion(con, s["jf"], LUNES)
    documentos = {
        "FIN-EGR-02": pdf.fin_egr_02(compras.reporte_disponibilidad(con, s["jf"], LUNES), ctx),
        "FIN-EGR-03": pdf.fin_egr_03(compras.obtener_orden(con, s["jf"], res["orden"]["orden_id"]), ctx),
        "FIN-EGR-04": pdf.fin_egr_04(compras.obtener_recepcion(con, s["jf"], res["recepcion_id"]), ctx),
        "FIN-EGR-05": pdf.fin_egr_05(compras.obtener_programacion(con, s["jf"], pid), ctx),
    }
    for codigo, contenido in documentos.items():
        assert contenido.startswith(b"%PDF") and codigo.encode() in contenido
        assert b"Bloque de Validaci" in contenido and b"P\\341gina 1 de" in contenido
