"""Formatos PDF del sistema de costos (Fase 7), con reportlab.

Replican los formatos del Anexo 7 (Ciclo de Transformación): FIN-TRA-03
(Mayor Analítico por Sufijo), FIN-TRA-06 (Informe de Costo de Ciclo) y
FIN-TRA-07 (Cálculo de Costo Unitario), más el reporte de comparación contra
el presupuesto D.4 (Manual Presupuestario E.1), con bloque de firmas y
riesgos mitigados (MAN-FIN-001).

Reciben los diccionarios de ``servicios.costos`` y devuelven bytes. No
importan ``streamlit``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import (
    E_NORMAL, E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _tabla,
)
from servicios.costos import NOMBRE_ELEMENTO, pct_texto
from utils.formato import fecha_pantalla

PIE = "Ciclo 3 — Transformación"
TRANSFORMACION = "Ciclo 3 — Transformación (Producción)"

__all__ = ["Contexto", "fin_tra_03", "fin_tra_06", "fin_tra_07", "comparacion_d4"]


def _riesgos(nota: str, filas: Sequence[Sequence[str]]) -> list[Any]:
    return [Spacer(1, 8), _p("Riesgos Mitigados — Referencia al Mapa de Riesgos (MAN-FIN-001)", E_SUB),
            _p(nota, E_PEQ), Spacer(1, 3),
            _tabla(["N°", "Riesgo (síntesis)", "Nivel", "Tipo de Control", "Objetivo COSO"], filas,
                   [1, 8.6, 2.2, 2.6, 3])]


def _n(valor: Any) -> str:
    return f"{int(valor):,}".replace(",", ".")


def _dec(valor: Any, decimales: int = 2) -> str:
    if valor is None:
        return "—"
    texto = f"{Decimal(str(valor)):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


_R22 = ["22", "Error en la acumulación diaria de sufijos que distorsione el costo unitario del lote.", "Moderado"]
_R8 = ["8", "Mortalidad anormal absorbida indebidamente en el costo del lote.", "Alto"]
_R39 = ["39", "Cierre del ciclo sin conciliación física del número de aves, ocultando faltantes no reportados.", "Bajo"]
_R15 = ["15", "Gasto o compromiso sin saldo presupuestario disponible, o desvío no informado.", "Moderado"]


# ==========================================================================
# FIN-TRA-03 — Mayor Analítico por Sufijo
# ==========================================================================
def fin_tra_03(mayor: dict[str, Any], ctx: Contexto) -> bytes:
    filas = [[f["sufijo"], f["nombre"], NOMBRE_ELEMENTO.get(f["elemento"], f["elemento"]), _bs(f["monto"])]
             for f in mayor["filas"]]
    filas.append(["TOTAL", "Total acumulado del centro de costos", "", _bs(mayor["total"])])
    rango = (f"del {fecha_pantalla(mayor['desde'])} " if mayor["desde"] else "desde la apertura ") + \
        f"al {fecha_pantalla(mayor['hasta'])}"
    cuerpo = _encabezado("FIN-TRA-03", "Mayor Analítico por Sufijo", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Acumula, dentro del centro de costos 6.1.01.01, el costo por cada sufijo del Plan de Cuentas, "
                  f"{rango}, antes del traslado a Productos Terminados.", E_NORMAL), Spacer(1, 4),
               _tabla(["Sufijo", "Concepto", "Elemento", "Acumulado al día de hoy (Bs)"], filas, [1.6, 8, 4.4, 3.4],
                      (3,)),
               Spacer(1, 4),
               _p("Resumen por elemento: " + "; ".join(f"{NOMBRE_ELEMENTO[k]} {_bs(v)}"
                                                       for k, v in mayor["por_elemento"].items() if v), E_NORMAL)]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Verificado por (Gerencia General)"])
    cuerpo += _riesgos("La columna del sufijo 40 aislada muestra si la mortalidad debió desviarse a 7.2.01.04.",
                       [_R22 + ["Preventivo", "Clasificación"], _R8 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-TRA-03", cuerpo, PIE)


# ==========================================================================
# FIN-TRA-06 — Informe de Costo de Ciclo
# ==========================================================================
def fin_tra_06(inf: dict[str, Any], ctx: Contexto) -> bytes:
    c, conc = inf["ciclo"], inf["conciliacion"]
    sufijos = [[s["sufijo"], s["nombre"], _bs(s["monto"])] for s in inf["por_sufijo"]]
    cuerpo = _encabezado("FIN-TRA-06", "Informe de Costo de Ciclo", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Emitido al cierre del ciclo {c['numero']} del ejercicio {c['ejercicio']} "
                  f"({fecha_pantalla(inf['fecha_cierre'])}), transfiriendo el saldo acumulado del centro de costos "
                  f"6.1.01.01 a 1.1.03.12 (asiento N.º {inf['asiento_id']}).", E_NORMAL), Spacer(1, 4),
               _tabla(["Concepto", "Detalle"], [
                   ["Aves ingresadas", _n(conc["ingresadas"])],
                   ["Mortalidad normal (cantidad y %)", f"{_n(conc['muertas_normal'])} · {pct_texto(conc['pct_normal'])}"],
                   ["Mortalidad anormal (cantidad y %) — 7.2.01.04, fuera del costo",
                    f"{_n(conc['muertas_anormal'])} · {pct_texto(conc['pct_anormal'])} · "
                    f"{_bs(inf['mortalidad_anormal_monto'])}"],
                   ["Aves faenadas", _n(conc["faenadas"])],
                   ["Faltante de aves", _n(conc["faltante"])],
                   ["Kilogramos de producto terminado (peso canal)", f"{_dec(inf['kg_producidos'], 1)} kg"],
                   ["Kilogramos de peso vivo", f"{_dec(inf['kg_vivos'], 1)} kg"],
                   ["ICA del ciclo", _dec(inf["ica"])],
                   ["Costo total del ciclo", _bs(inf["total"])],
                   ["Costo unitario Bs/kg (ver FIN-TRA-07)", _bs(inf["costo_unitario"])]], [10, 7.4]),
               Spacer(1, 6), _p("Costo total del ciclo, por sufijo (FIN-TRA-03)", E_SUB),
               _tabla(["Sufijo", "Concepto", "Monto (Bs)"], sufijos, [1.8, 11.6, 4], (2,)),
               Spacer(1, 4),
               _p("Por elemento: " + "; ".join(f"{NOMBRE_ELEMENTO[k]} {_bs(v)}" for k, v in inf["por_elemento"].items()
                                              if v), E_NORMAL),
               _p(f"Conciliación: aves ingresadas {_n(conc['ingresadas'])} = mortalidad normal "
                  f"{_n(conc['muertas_normal'])} + mortalidad anormal {_n(conc['muertas_anormal'])} + faenadas "
                  f"{_n(conc['faenadas'])} + faltante {_n(conc['faltante'])}.", E_NORMAL)]
    if inf.get("corte_mod") and inf["corte_mod"]["monto"]:
        cuerpo.append(_p(f"Corte de MOD fija: {inf['corte_mod']['dias']} días del ciclo devengados al cierre por "
                         f"{_bs(inf['corte_mod']['monto'])} (se revierten en el ciclo siguiente).", E_PEQ))
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Aprobado por (Gerencia General)"],
                      [inf["elaborado_por"], inf["aprobado_por"] or ""])
    cuerpo += _riesgos("La conciliación de aves exigida en este informe es el control aritmético del riesgo 39.",
                       [_R39 + ["Preventivo", "Protección Física"], _R22 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-TRA-06", cuerpo, PIE)


# ==========================================================================
# FIN-TRA-07 — Cálculo de Costo Unitario
# ==========================================================================
def fin_tra_07(inf: dict[str, Any], ctx: Contexto) -> bytes:
    filas = [[NOMBRE_ELEMENTO[k], _bs(v), _bs(Decimal(v) / Decimal(inf["kg_producidos"]))]
             for k, v in inf["por_elemento"].items() if v]
    filas.append(["TOTAL", _bs(inf["total"]), _bs(inf["costo_unitario"])])
    variacion = ("Sin ciclo anterior cerrado." if inf["variacion"] is None else
                 f"Ciclo anterior: {_bs(inf['costo_unitario_anterior'])} por kg; variación {_bs(inf['variacion'])} "
                 f"({pct_texto(inf['variacion_pct'])}).")
    cuerpo = _encabezado("FIN-TRA-07", "Cálculo de Costo Unitario", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Costo unitario = costo total del ciclo ÷ kilogramos de producto terminado "
                  f"({_dec(inf['kg_producidos'], 1)} kg). Insumo del Índice de Costo por Ventas (ICL).", E_NORMAL),
               Spacer(1, 4),
               _tabla(["Elemento del costo", "Monto (Bs)", "Bs por kg"], filas, [9, 4.2, 4.2], (1, 2)),
               Spacer(1, 4), _p(variacion, E_NORMAL)]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Informado a (Gerencia General)"],
                      [inf["elaborado_por"], inf["aprobado_por"] or ""])
    cuerpo += _riesgos("El costo unitario por elemento permite detectar desvíos frente al presupuesto.",
                       [_R22 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-TRA-07", cuerpo, PIE)


# ==========================================================================
# Comparación contra el presupuesto D.4 (Manual Presupuestario E.1)
# ==========================================================================
def comparacion_d4(comp: dict[str, Any], ctx: Contexto) -> bytes:
    c = comp["ciclo"]
    filas = [[f["sufijo"], f["concepto"], _bs(f["presupuesto"]), _bs(f["real"]), _bs(f["variacion"]),
              pct_texto(f["variacion_pct"]) if f["variacion_pct"] is not None else "—",
              _bs(f["variacion_precio"]) if f["variacion_precio"] is not None else "—",
              _bs(f["variacion_cantidad"]) if f["variacion_cantidad"] is not None else "—", f["semaforo"].capitalize()]
             for f in comp["filas"]]
    filas.append(["", "TOTAL", _bs(comp["presupuesto"]), _bs(comp["real"]), _bs(comp["variacion"]),
                  pct_texto(comp["variacion_pct"]) if comp["variacion_pct"] is not None else "—", "", "",
                  comp["semaforo"].capitalize()])
    cuerpo = _encabezado("PPTO-D4", "Comparación del Costo del Ciclo contra el Presupuesto",
                         "Unidad de Finanzas y Comercialización", TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Ciclo {c['numero']} del ejercicio {c['ejercicio']} frente al presupuesto D.4-1. Variación de precio = "
                  "(P real − P presupuestado) × Q real; de cantidad = (Q real − Q presupuestada) × P presupuestado. "
                  "En costos, la variación positiva es desfavorable.", E_NORMAL), Spacer(1, 4),
               _tabla(["Suf.", "Concepto", "Presupuesto", "Real", "Variación", "%", "Precio", "Cantidad", "Semáforo"],
                      filas, [0.9, 3.6, 2.1, 2.1, 1.8, 1.4, 1.8, 1.8, 1.9], (2, 3, 4, 5, 6, 7))]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Revisado por (Gerencia General)"])
    cuerpo += _riesgos("Las variaciones sobre el umbral se informan a la Gerencia (Manual Presupuestario E.2).",
                       [_R15 + ["Detección", "Verificación y Evaluación"]])
    return _construir("PPTO-D4", cuerpo, PIE)
