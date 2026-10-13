"""Formatos PDF del Ciclo 2 — Egresos / Adquisiciones (Fase 4), con reportlab.

Replican los formatos del Anexo 6 (Ciclo de Egresos): encabezado con código y
versión, datos de contexto, cuerpo de datos, bloque de validación y firmas,
riesgos mitigados (MAN-FIN-001) y pie con número de página.

Formatos: FIN-EGR-01 (Acta de Control de Stock Semanal), FIN-EGR-02 (Reporte
de Disponibilidad Presupuestaria), FIN-EGR-03 (Orden de Compra), FIN-EGR-04
(Acta de Recepción de Insumos), FIN-EGR-05 (Programación de Pagos a
Proveedores) y Acta de Baja de Inventario (PC-07 §V.C).

Reciben los diccionarios de ``servicios.compras`` / ``servicios.inventarios``
y devuelven los bytes del PDF. No importan ``streamlit``.
"""
from __future__ import annotations

from typing import Any, Sequence

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import (
    E_NORMAL, E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _si_no, _tabla,
)
from servicios.inventarios import formato_cantidad
from utils.formato import fecha_pantalla

CICLO = "Ciclo 2 — Egresos (Adquisiciones)"
PIE = "Ciclo 2 — Egresos"

__all__ = ["Contexto", "fin_egr_01", "fin_egr_02", "fin_egr_03", "fin_egr_04", "fin_egr_05", "acta_baja"]


def _riesgos(nota: str, filas: Sequence[Sequence[str]]) -> list[Any]:
    return [Spacer(1, 8), _p("Riesgos Mitigados — Referencia al Mapa de Riesgos (MAN-FIN-001)", E_SUB),
            _p(nota, E_PEQ), Spacer(1, 3),
            _tabla(["N°", "Riesgo (síntesis)", "Nivel", "Tipo de Control", "Objetivo COSO"], filas,
                   [1, 8.6, 2.2, 2.6, 3])]


_R1 = ["1", "Desabastecimiento de alimento balanceado o pollitos BB que prolongue el ciclo productivo más allá de "
       "los 73 días.", "Alto (NR=9)"]
_R15 = ["15", "Compra u orden emitida sin saldo presupuestario disponible, generando un compromiso de gasto no "
        "financiado.", "Moderado"]
_R16 = ["16", "Registro de inventario que no coincide con la existencia física, distorsionando el costo de "
        "producción.", "Moderado"]
_R17 = ["17", "Deterioro de granos por humedad de los Yungas no detectado a tiempo.", "Moderado"]


# ==========================================================================
# FIN-EGR-01 — Acta de Control de Stock Semanal
# ==========================================================================
def fin_egr_01(acta: dict[str, Any], ctx: Contexto) -> bytes:
    """Acta del viernes: físico vs contable vs punto de reorden (CI-FIN-16)."""
    filas = []
    for l in acta["lineas"]:
        filas.append([l["insumo"], formato_cantidad(l["existencia_fisica_mil"], l["unidad"]),
                      formato_cantidad(l["saldo_contable_mil"], l["unidad"]),
                      formato_cantidad(l["punto_reorden_mil"], l["unidad"]) if l["punto_reorden_mil"] is not None
                      else "Sin PR", "Sí" if l["bajo_pr"] else "No", l["accion"]])
    autoriza = ", ".join(f"{e['autorizado_por']} ({e['insumo']}, {e['constancia_ref']})"
                         for e in acta.get("emergencias", [])) or "_______________________"
    cuerpo = _encabezado("FIN-EGR-01", "Acta de Control de Stock Semanal",
                         "Unidad Técnica / Unidad de Finanzas y Comercialización", CICLO) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Control de stock físico e inventarios concurrentes, todos los viernes por la mañana, cotejado contra "
                  "el registro contable y contra el Punto de Reorden vigente (CI-FIN-16).", E_NORMAL), Spacer(1, 4),
               _tabla(["Insumo Crítico", "Existencia Física", "Saldo Contable", "Punto de Reorden (CI-FIN-16)",
                       "¿Bajo el PR? (Sí/No)", "Acción"], filas, [3.6, 2.4, 2.4, 2.6, 1.8, 4.6], (1, 2, 3)),
               Spacer(1, 6),
               _p(f"Déficit detectado frente al Punto de Reorden: {_si_no(bool(acta['deficit']))}", E_NORMAL),
               _p(f"De ser afirmativo, compra de emergencia autorizada por: {autoriza}", E_NORMAL)]
    if acta.get("observaciones"):
        cuerpo.append(_p(f"Observaciones: {acta['observaciones']}", E_NORMAL))
    ut = acta["elaborado_por"] if acta["rol_elaborador"] == "UT" else (acta.get("verificado_por") or "")
    jf = acta["elaborado_por"] if acta["rol_elaborador"] == "JF" else (acta.get("verificado_por") or "")
    cuerpo += _firmas(["Verificado por (Unidad Técnica)", "Verificado por (Unidad de Finanzas)"], [ut, jf])
    cuerpo += _riesgos("Control ordinario semanal de existencias, complementado por el monitoreo continuo del CI-FIN-16.",
                       [_R1 + ["Detección", "Verificación y Evaluación"], _R16 + ["Preventivo", "Clasificación"],
                        _R17 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-EGR-01", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-02 — Reporte de Disponibilidad Presupuestaria
# ==========================================================================
def fin_egr_02(disponibilidad: dict[str, Any], ctx: Contexto) -> bytes:
    """Saldo presupuestario por partida del ciclo (resultado de ``compras.disponibilidad``)."""
    filas = [[f"{l['cuenta']} — {l['partida']}", _bs(l["aprobado"]), _bs(l["ejecutado"]), _bs(l["saldo"]),
              "Sí" if l["habilita"] else "No"] for l in disponibilidad["lineas"]]
    cuerpo = _encabezado("FIN-EGR-02", "Reporte de Disponibilidad Presupuestaria",
                         "Unidad de Finanzas y Comercialización", "Ciclo 2 — Egresos (Adquisiciones y Planillas)")
    cuerpo += _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Verificación del saldo presupuestario disponible en el ciclo {disponibilidad['ciclo']} del ejercicio "
                  f"{disponibilidad['ejercicio']} antes de habilitar la emisión de la orden de compra (PR-FIN-03).",
                  E_NORMAL), Spacer(1, 4),
               _tabla(["Partida Presupuestaria", "Presupuesto Aprobado (Bs)", "Ejecutado a la Fecha (Bs)",
                       "Saldo Disponible (Bs)", "¿Habilita la Emisión? (Sí/No)"], filas, [6, 3, 3, 3, 2.4], (1, 2, 3)),
               Spacer(1, 4),
               _p("Un «No» en la última columna suspende de inmediato la emisión de la orden de compra (POL-04 Art. 2). "
                  "Fuente de las partidas: Manual del Sistema Presupuestario.", E_PEQ)]
    cuerpo += _firmas(["Verificado por (Unidad de Finanzas)"], [ctx.elaborado_por])
    cuerpo += _riesgos("Control preventivo previo a todo compromiso de gasto.",
                       [_R1 + ["Preventivo", "Autorización"], _R15 + ["Preventivo", "Autorización"]])
    return _construir("FIN-EGR-02", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-03 — Orden de Compra
# ==========================================================================
def fin_egr_03(orden: dict[str, Any], ctx: Contexto) -> bytes:
    """Orden de compra emitida por la Gerencia General."""
    condicion = ("Contado [X]  Crédito pactado [ ]" if orden["condicion_pago"] == "CONTADO"
                 else f"Contado [ ]  Crédito pactado [X] ({orden['dias_credito']} días)")
    filas = [[orden["proveedor"]["nombre"], l["insumo"], formato_cantidad(l["cantidad_mil"], l["unidad"]),
              _bs(l["precio_unitario"]), _bs(l["total"]), condicion] for l in orden["lineas"]]
    cot = [[c["proveedor"], _bs(c["monto"]), c["documento_ref"]] for c in orden["cotizaciones"]]
    cuerpo = _encabezado("FIN-EGR-03", "Orden de Compra", "Gerencia General", CICLO) + _contexto(ctx)
    emision = fecha_pantalla(orden["fecha_emision"]) if orden.get("fecha_emision") else "pendiente"
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"{orden['etiqueta']} · programada para el {fecha_pantalla(orden['fecha_programada'])} · emitida el "
                  f"{emision}. Emitida únicamente los días lunes por la Gerencia General, previa verificación del saldo "
                  "presupuestario (FIN-EGR-02)" + (". Compra de emergencia autorizada (CI-FIN-16 Art. 11)."
                                                   if orden["es_emergencia"] else "."), E_NORMAL), Spacer(1, 4),
               _tabla(["Proveedor", "Insumo/Servicio", "Cantidad", "Precio Unitario (Bs)", "Total (Bs)",
                       "Condición de Pago"], filas, [3.3, 3.4, 2.1, 2.4, 2.4, 3.8], (2, 3, 4)),
               Spacer(1, 4),
               _p(f"Total de la orden: {_bs(orden['total'])}. ¿Monto > Bs 5.000? "
                  f"{_si_no(len(orden['cotizaciones']) > 0)} — si es afirmativo, se adjuntan las cotizaciones:", E_NORMAL)]
    if cot:
        cuerpo += [Spacer(1, 3), _tabla(["Proveedor cotizado", "Monto (Bs)", "Documento"], cot, [8.4, 4, 5], (1,))]
    if orden.get("justificacion_seleccion"):
        cuerpo.append(_p(f"Justificación de la selección: {orden['justificacion_seleccion']}", E_NORMAL))
    cuerpo += _firmas(["Emitida por (Gerencia General)", "Verificación previa (Unidad de Finanzas)"],
                      [orden.get("emitida_por") or "", orden.get("preparado_por") or ""])
    cuerpo += _riesgos("Control de autorización del gasto de adquisición.",
                       [_R1 + ["Preventivo", "Autorización"], _R15 + ["Preventivo", "Autorización"]])
    return _construir("FIN-EGR-03", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-04 — Acta de Recepción de Insumos
# ==========================================================================
def fin_egr_04(recepcion: dict[str, Any], ctx: Contexto) -> bytes:
    """Verificación de cantidad y calidad frente a la orden de compra."""
    filas = []
    for l in recepcion["lineas"]:
        obs = l.get("observacion") or ""
        if l.get("fecha_vencimiento"):
            obs = f"Vence {fecha_pantalla(l['fecha_vencimiento'])}. {obs}".strip()
        filas.append([l["insumo"], formato_cantidad(l["cantidad_pendiente_mil"], l["unidad"]),
                      formato_cantidad(l["cantidad_recibida_mil"], l["unidad"]), "Sí" if l["calidad_conforme"] else "No",
                      obs or "—"])
    cuerpo = _encabezado("FIN-EGR-04", "Acta de Recepción de Insumos", "Unidad Técnica", CICLO) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Recepción de {recepcion['orden']} ({recepcion['proveedor']}), documento del proveedor "
                  f"{recepcion['documento_proveedor']}. Verificación de cantidad y calidad del insumo recibido frente a "
                  "la Orden de Compra (FIN-EGR-03), previo a su estibado en silos o áreas secas.", E_NORMAL),
               Spacer(1, 4),
               _tabla(["Insumo Recibido", "Cantidad según O.C.", "Cantidad Recibida", "Calidad Conforme (Sí/No)",
                       "Observaciones"], filas, [3.8, 2.6, 2.6, 2.4, 6], (1, 2)),
               Spacer(1, 4), _p(f"Valor aceptado para el registro contable: {_bs(recepcion['valor_aceptado'])}.", E_NORMAL)]
    cuerpo += _firmas(["Recibido por (Unidad Técnica)", "Registrado por (Unidad de Finanzas)"],
                      [recepcion["recibido_por"], recepcion.get("registrado_por") or ""])
    cuerpo += _riesgos("Control de verificación en el punto de ingreso del inventario.",
                       [_R16 + ["Preventivo", "Clasificación"], _R17 + ["Detección", "Protección Física"]])
    return _construir("FIN-EGR-04", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-05 — Programación de Pagos a Proveedores
# ==========================================================================
def fin_egr_05(programacion: dict[str, Any], ctx: Contexto) -> bytes:
    """Programación según la jerarquía de POL-04 Art. 2 y el vencimiento."""
    filas = [[l["proveedor"], f"CxP-{l['cuenta_por_pagar_id']} ({l['orden']})", _bs(l["monto"]),
              fecha_pantalla(l["vencimiento"]), f"Nivel {l['nivel']} — {l['nombre_nivel']}",
              "Sí [X]  No [ ]" if l["requiere_mancomunada"] else "Sí [ ]  No [X]"] for l in programacion["lineas"]]
    no_pagables = [f"CxP-{l['cuenta_por_pagar_id']}: {l['motivo']}" for l in programacion["lineas"] if not l["pagable"]]
    cuerpo = _encabezado("FIN-EGR-05", "Programación de Pagos a Proveedores", "Unidad de Finanzas y Comercialización",
                         CICLO) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Programación del pago de la cuenta por pagar conforme a la jerarquía de pagos POL-04 Art. 2 y al "
                  f"vencimiento pactado con el proveedor. Saldo bancario inicial {_bs(programacion['saldo_inicial'])}; "
                  f"total pagable {_bs(programacion['total_pagable'])}.", E_NORMAL), Spacer(1, 4),
               _tabla(["Proveedor", "N° de Cuenta por Pagar", "Monto (Bs)", "Fecha de Vencimiento",
                       "Prioridad (Jerarquía POL-04)", "¿Requiere Firma Mancomunada? (>Bs 5.000)"], filas,
                      [3, 3, 2.3, 2.2, 4, 2.9], (2,))]
    if no_pagables:
        cuerpo += [Spacer(1, 4), _p("No pagables en esta programación: " + "; ".join(no_pagables), E_PEQ)]
    cuerpo += _firmas(["Programado por (Unidad de Finanzas)", "Autorizado por (Gerencia General)"],
                      [programacion["elaborado_por"], programacion.get("autorizado_por") or ""])
    cuerpo += _riesgos("Control de autorización y protección física del desembolso.",
                       [_R15 + ["Preventivo", "Autorización"]])
    return _construir("FIN-EGR-05", cuerpo, PIE)


# ==========================================================================
# Acta de Baja de Inventario (PC-07 §V.C)
# ==========================================================================
def acta_baja(baja: dict[str, Any], ctx: Contexto, causas: dict[str, str]) -> bytes:
    """Acta firmada por la GG con causa, cantidad y costo; visto del CV sobre el umbral."""
    valor = baja.get("valor") if baja.get("valor") is not None else baja["valor_estimado"]
    filas = [[baja["insumo"], formato_cantidad(baja["cantidad_mil"], baja["unidad"]),
              causas.get(baja["causa"], baja["causa"]), _bs(valor), baja["informe_tecnico_ref"]]]
    cuerpo = _encabezado("ACTA-BAJA", "Acta de Baja de Inventario", "Gerencia General", CICLO) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Baja n.º {baja['id']} — {baja['descripcion']}. Valuada al Costo Promedio Ponderado (PC-07).",
                  E_NORMAL), Spacer(1, 4),
               _tabla(["Insumo", "Cantidad", "Causa", "Costo (Bs)", "Informe técnico"], filas,
                      [4, 2.4, 4.6, 2.6, 3.8], (1, 3)),
               Spacer(1, 4),
               _p(f"Requiere visto del Consejo de Vigilancia (monto mayor al umbral): "
                  f"{_si_no(bool(baja['requiere_visto_cv']))}. Acta: {baja.get('acta_ref') or '—'}.", E_NORMAL)]
    cuerpo += _firmas(["Propuesto por (Unidad de Finanzas)", "Autorizado por (Gerencia General)",
                       "Visto (Consejo de Vigilancia)"],
                      [baja["propuesto_por"], baja.get("autorizado_por") or "", baja.get("visto_cv_por") or ""])
    cuerpo += _riesgos("Control de autorización de bajas y protección del inventario.",
                       [_R16 + ["Detección", "Clasificación"], _R17 + ["Detección", "Protección Física"]])
    return _construir("ACTA-BAJA", cuerpo, PIE)
