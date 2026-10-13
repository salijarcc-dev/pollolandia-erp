"""Formatos PDF de activos fijos y CAPEX (Fase 8), con reportlab.

Replican los formatos del Anexo 5 (Ciclo de Tesorería): FIN-TES-01 (Plan
Anual de Inversiones CAPEX), FIN-TES-02 (Hoja de Cálculo de Indicadores —
ROA), FIN-TES-03 (Informe Técnico-Financiero de Inversiones), FIN-TES-04
(Informe Semestral de Ejecución CAPEX) e INF-CV-01 (Acta de Verificación
Física de Activos); y del Anexo 7 (Ciclo de Transformación): FIN-TRA-08
(Formulario de Activación), FIN-TRA-09 (Ficha Individual), FIN-TRA-10
(Planilla Mensual de Depreciación), más el Acta de Baja de Activo Fijo y el
Libro de Inventario de Activos Fijos (PR-FIN-07). Cada uno lleva el bloque de
firmas y los riesgos mitigados (MAN-FIN-001).

Reciben los diccionarios de ``servicios.activos`` y devuelven bytes. No
importan ``streamlit``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import (
    E_NORMAL, E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _si_no, _tabla,
)
from servicios.activos import CATEGORIAS, CAUSAS_BAJA, pct_texto
from utils.formato import fecha_pantalla

TESORERIA = "Ciclo 1 — Tesorería (Financiamiento)"
TRANSFORMACION = "Ciclo 3 — Transformación (Producción) / PR-FIN-07"
PIE_TES = "Ciclo 1 — Tesorería"
PIE_TRA = "Ciclo 3 — Transformación"

__all__ = ["Contexto", "fin_tes_01", "fin_tes_02", "fin_tes_03", "fin_tes_04", "fin_tra_08", "fin_tra_09",
           "fin_tra_10", "acta_baja", "inf_cv_01", "libro_inventario"]

_R2 = ["2", "Inversión ejecutada sin aprobación de la Asamblea o por encima del techo de Bs 20.000 sin autorización "
       "del Consejo de Administración.", "Alto"]
_R13 = ["13", "Sobreestimación del ROA proyectado, que induzca a decisiones de expansión no sostenibles frente al "
        "flujo real.", "Moderado"]
_R28 = ["28", "Activos declarados como adquiridos que no fueron efectivamente recepcionados o instalados (activo "
        "fantasma).", "Moderado"]
_R40 = ["40", "Activación de un activo a costo o fecha incorrecta, distorsionando el CIF del ciclo productivo.", "Bajo"]
_R41 = ["41", "Activos no verificados físicamente durante el ejercicio (pérdida, robo u obsolescencia no detectada).",
        "Bajo"]


def _riesgos(nota: str, filas: Sequence[Sequence[str]]) -> list[Any]:
    return [Spacer(1, 8), _p("Riesgos Mitigados — Referencia al Mapa de Riesgos (MAN-FIN-001)", E_SUB),
            _p(nota, E_PEQ), Spacer(1, 3),
            _tabla(["N°", "Riesgo (síntesis)", "Nivel", "Tipo de Control", "Objetivo COSO"], filas,
                   [1, 8.6, 2.2, 2.6, 3])]


def _tasa(valor: Any) -> str:
    return pct_texto(valor)


def _dec(valor: Any, decimales: int = 2) -> str:
    if valor is None:
        return "—"
    texto = f"{Decimal(str(valor)):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _campos(filas: Sequence[tuple[str, Any]]) -> Any:
    return _tabla(["Campo", "Detalle"], [[k, v] for k, v in filas], [6.4, 11])


# ==========================================================================
# FIN-TES-01 — Plan Anual de Inversiones (CAPEX)
# ==========================================================================
def fin_tes_01(plan: Mapping[str, Any], ctx: Contexto) -> bytes:
    filas = []
    for n, l in enumerate([l for l in plan["lineas"] if l["estado"] == "VIGENTE"], start=1):
        filas.append([str(n), l["activo"] + ("" if l["previsto"] else " (no previsto, autorizado por el CA)"),
                      _bs(l["costo"]), l["proveedor_referencia"] or "—", l["justificacion"], "Recursos propios",
                      f"Ciclo {l['ciclo']} · {fecha_pantalla(l['fecha_estimada'])}"])
    filas.append(["", "TOTAL", _bs(plan["total"]), "", "", "", ""])
    estado = {"BORRADOR": "Borrador", "PRESENTADO": "Presentado al CA", "APROBADO": "Aprobado por el CA",
              "RATIFICADO": "Ratificado por la Asamblea"}[plan["estado"]]
    cuerpo = _encabezado("FIN-TES-01", "Plan Anual de Inversiones (CAPEX)", "Gerencia General", TESORERIA)
    cuerpo += _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Ejercicio {plan['ejercicio']} ({estado}). Elaborado el {fecha_pantalla(plan['fecha_elaboracion'])}; "
                  f"plazo POL-02 Art. 1: {fecha_pantalla(plan['fecha_limite'])}"
                  + (" — PRESENTADO FUERA DE PLAZO." if plan["fuera_de_plazo"] else "."), E_NORMAL), Spacer(1, 4),
               _tabla(["N°", "Activo a Adquirir/Construir", "Costo Estimado (Bs)", "Proveedor Referencial",
                       "Justificación Técnica", "Fuente de Financ.", "Ciclo/Fecha Prevista"], filas,
                      [0.8, 3.6, 2.3, 2.3, 4, 2, 2.4], (2,)),
               Spacer(1, 4),
               _p(f"Impacto proyectado en depreciación mensual (Bs): {_bs(plan['depreciacion_mensual'])} "
                  f"(anual {_bs(plan['depreciacion_anual'])}).", E_NORMAL),
               _p(f"Impacto proyectado en el costo unitario Bs/kg: {_dec(plan['bs_por_kg'], 4)} (depreciación anual "
                  "de los activos productivos ÷ kg de carne del ejercicio de operación).", E_NORMAL)]
    if plan.get("acta_ca"):
        cuerpo.append(_p(f"Aprobación del CA: {plan['acta_ca']} ({fecha_pantalla(plan['aprobado_en'])})."
                         + (f" Ratificación de la Asamblea: {plan['acta_ag']} "
                            f"({fecha_pantalla(plan['ratificado_en'])})." if plan.get("acta_ag") else ""), E_NORMAL))
    cuerpo += _firmas(["Elaborado por (Gerencia General)", "Revisado por (Consejo de Administración)",
                       "Aprobado por (Asamblea General)"])
    cuerpo += _riesgos("Evidencia documental primaria del control preventivo del Ciclo 1 sobre inversión CAPEX.",
                       [_R2 + ["Preventivo", "Autorización"], _R13 + ["Detección", "Verificación y Evaluación"],
                        _R28 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TES-01", cuerpo, PIE_TES)


# ==========================================================================
# FIN-TES-02 — Hoja de Cálculo de Indicadores (ROA)
# ==========================================================================
def fin_tes_02(plan: Mapping[str, Any], ctx: Contexto) -> bytes:
    cuerpo = _encabezado("FIN-TES-02", "Hoja de Cálculo de Indicadores (ROA)", "Unidad de Finanzas y Comercialización",
                         TESORERIA) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Se calcula incorporando la nueva inversión proyectada en el FIN-TES-01, antes de presentar el Plan "
                  "CAPEX al Consejo de Administración.", E_NORMAL), Spacer(1, 4)]
    if plan.get("roa") is None:
        cuerpo.append(_p("El análisis aún no fue elaborado por la Unidad de Finanzas.", E_NORMAL))
    else:
        semaforo = {"VERDE": "Bueno (> 18 %)", "AMARILLO": "Normal (10 % a 18 %)", "ROJO": "Malo (< 10 %)"}
        cuerpo += [_tabla(["Concepto", "Valor"], [
            ["Utilidad neta anual proyectada (Bs)", _bs(plan["utilidad_neta"])],
            ["Activos totales actuales (Bs)", _bs(plan["activos_totales"])],
            ["(+) Nueva inversión del Plan CAPEX (Bs)", _bs(plan["inversion_total"])],
            ["Activos totales con la inversión (Bs)", _bs(plan["activos_totales"] + plan["inversion_total"])],
            ["ROA proyectado = Utilidad neta ÷ Activos totales × 100", pct_texto(plan["roa"])],
            ["Interpretación (MAN-05 PR-FIN-01)", semaforo.get(plan["roa_semaforo"], "—")],
        ], [11, 6.4], (1,)), Spacer(1, 6), _p("Compatibilidad con el Saldo Mínimo de Efectivo (POL-04)", E_SUB),
            _tabla(["Concepto", "Valor"], [
                ["Saldo bancario proyectado (Bs)", _bs(plan["saldo_proyectado"])],
                ["(−) Desembolso de la inversión (Bs)", _bs(plan["inversion_total"])],
                ["Saldo posterior a la inversión (Bs)", _bs(plan["saldo_post_inversion"])],
                ["SME vigente del ejercicio (Bs)", _bs(plan["sme"])],
                ["Compatible con el SME", _si_no(bool(plan["compatible_sme"]))],
                ["Bajo el umbral de compromiso (exige autorización escrita de la GG)",
                 _si_no(bool(plan["bajo_umbral_compromiso"]))],
            ], [11, 6.4], (1,)),
            _p(f"Análisis del {fecha_pantalla(plan['analisis_fecha'])} por {plan['analizado_por']}.", E_PEQ)]
    cuerpo += _firmas(["Calculado por (Unidad de Finanzas)", "Revisado por (Gerencia General)"])
    cuerpo += _riesgos("Control de detección sobre la viabilidad financiera del Plan CAPEX.",
                       [_R13 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-TES-02", cuerpo, PIE_TES)


# ==========================================================================
# FIN-TES-03 — Informe Técnico-Financiero de Inversiones
# ==========================================================================
def fin_tes_03(plan: Mapping[str, Any], ctx: Contexto) -> bytes:
    cuerpo = _encabezado("FIN-TES-03", "Informe Técnico-Financiero de Inversiones", "Gerencia General",
                         TESORERIA) + _contexto(ctx)
    activos_txt = "; ".join(f"{l['activo']} ({_bs(l['costo'])})" for l in plan["lineas"] if l["estado"] == "VIGENTE")
    cuerpo += [_p("1. Resumen ejecutivo del Plan CAPEX", E_SUB),
               _p(plan.get("resumen") or f"Plan CAPEX {plan['ejercicio']}: {activos_txt}. Total {_bs(plan['total'])}, "
                  "financiado 100 % con recursos propios.", E_NORMAL),
               _p("2. Análisis de Retorno sobre Activos (ROA) — ver FIN-TES-02 adjunto", E_SUB),
               _p(f"ROA proyectado con la inversión: {pct_texto(plan.get('roa'))}." if plan.get("roa") is not None
                  else "Pendiente del análisis de la Unidad de Finanzas.", E_NORMAL),
               _p("3. Verificación de compatibilidad con el Saldo Mínimo de Efectivo (POL-04)", E_SUB),
               _tabla(["Compatible con el SME vigente", "Observaciones"], [[
                   _si_no(bool(plan.get("compatible_sme"))),
                   f"Saldo posterior {_bs(plan.get('saldo_post_inversion'))} frente a un SME de {_bs(plan.get('sme'))}"
                   if plan.get("sme") is not None else "—"]], [5, 12.4]),
               _p("4. Recomendación de la Gerencia General", E_SUB),
               _p(plan.get("recomendacion") or "—", E_NORMAL)]
    if plan.get("observacion_ca"):
        cuerpo.append(_p(f"Observaciones del Consejo de Administración: {plan['observacion_ca']}", E_NORMAL))
    cuerpo += _firmas(["Elaborado por (Gerencia General)", "Recibido por (Consejo de Administración)"])
    cuerpo += _riesgos("Documento clave de autorización previa a la ejecución del CAPEX; la sección 3 impide "
                       "comprometer pagos sobre el saldo bancario sin aprobación de la Asamblea.",
                       [_R2 + ["Preventivo", "Autorización"], _R13 + ["Detección", "Verificación y Evaluación"],
                        _R28 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TES-03", cuerpo, PIE_TES)


# ==========================================================================
# FIN-TES-04 — Informe Semestral de Ejecución CAPEX
# ==========================================================================
def fin_tes_04(inf: Mapping[str, Any], ctx: Contexto) -> bytes:
    filas = [[f["activo"], _bs(f["aprobado"]), _bs(f["ejecutado"]),
              pct_texto(f["variacion_pct"]) if f["variacion_pct"] is not None else "—",
              _si_no(bool(f["supera"])), f["observacion"]] for f in inf["filas"]]
    filas.append(["TOTAL", _bs(inf["aprobado"]), _bs(inf["ejecutado"]),
                  f"Ejecución {pct_texto(inf['ejecucion_pct'])}" if inf["ejecucion_pct"] is not None else "—", "", ""])
    cuerpo = _encabezado("FIN-TES-04", "Informe Semestral de Ejecución CAPEX", "Gerencia General",
                         TESORERIA) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Ejercicio {inf['ejercicio']}, semestre {inf['semestre']} (del {fecha_pantalla(inf['desde'])} al "
                  f"{fecha_pantalla(inf['hasta'])}); se presenta en {fecha_pantalla(inf['mes_presentacion'])[3:]}. "
                  "Presentado al Consejo de Administración, comparando lo presupuestado con lo ejecutado.", E_NORMAL),
               Spacer(1, 4),
               _tabla(["Activo", "Monto Aprobado (Bs)", "Monto Ejecutado (Bs)", "Variación (%)",
                       f"¿Supera {pct_texto(inf['limite_variacion'], 0)}?", "Observación / Acción"], filas,
                      [4, 2.4, 2.4, 2, 2.4, 4.2], (1, 2, 3)),
               _p("Nota: toda variación superior al 15 % respecto del monto aprobado para un activo específico debe "
                  "informarse al Consejo de Administración; si supera Bs 10.000 requiere nueva aprobación de la "
                  "Asamblea General (POL-02, Art. 4).", E_PEQ)]
    cuerpo += _firmas(["Elaborado por (Gerencia General)", "Recibido por (Consejo de Administración)"])
    cuerpo += _riesgos("Control detectivo periódico sobre la ejecución real del Plan CAPEX.",
                       [_R13 + ["Detección", "Verificación y Evaluación"], _R28 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TES-04", cuerpo, PIE_TES)


# ==========================================================================
# FIN-TRA-08 — Formulario de Activación de Activo Fijo
# ==========================================================================
def fin_tra_08(act: Mapping[str, Any], ctx: Contexto, tasa: Any = None) -> bytes:
    cat = CATEGORIAS[act["categoria"]]
    vida = f"{cat.nombre}: tasa {_tasa(tasa)}" if tasa is not None else f"{cat.nombre}: tasa de PC-06 (parámetro " \
        f"deprec_tasa_{cat.clave})"
    costo = f"{_bs(act['costo'])} (precio {_bs(act['precio_cent'] / 100)} + fletes {_bs(act['flete_cent'] / 100)} " \
            f"+ instalación {_bs(act['instalacion_cent'] / 100)})"
    destino = "Operativo [X]  Administrativo [ ]" if act["destino"] == "OPERATIVO" else \
        "Operativo [ ]  Administrativo [X]"
    cuerpo = _encabezado("FIN-TRA-08", "Formulario de Activación de Activo Fijo", "Gerencia General",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB), _campos([
        ("Descripción del activo", act["descripcion"]),
        ("Costo total de adquisición (precio + fletes + instalación) (Bs)", costo),
        ("Fecha de puesta en condiciones de uso", fecha_pantalla(act["fecha_disponible"])),
        ("Destino: Operativo (centro de costo) / Administrativo (gasto)", destino),
        ("Vida útil / Tasa de depreciación anual aplicable (PC-06)", vida),
        ("Línea del Plan CAPEX", f"{act.get('linea', '')} (ejercicio {act.get('ejercicio', '')})"),
        ("Proveedor / documento", f"{act['proveedor']} · {act['documento_ref']}"),
        ("Estado", {"AUTORIZADA": "Autorizada, pendiente de registro", "REGISTRADA":
                    f"Registrada: {act.get('codigo_interno') or ''}", "ANULADA": "Anulada"}[act["estado"]]),
    ])]
    cuerpo += _firmas(["Autorizado por (Gerencia General)", "Registrado por (Unidad de Finanzas)"],
                      [act["autorizado_por"], ""])
    cuerpo += _riesgos("Control de autorización previo a todo cómputo de depreciación.",
                       [_R40 + ["Preventivo", "Clasificación"], _R41 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TRA-08", cuerpo, PIE_TRA)


# ==========================================================================
# FIN-TRA-09 — Ficha Individual de Activo Fijo
# ==========================================================================
def fin_tra_09(ficha: Mapping[str, Any], ctx: Contexto) -> bytes:
    ultima = ficha.get("ultima_verificacion")
    verif = (f"{fecha_pantalla(ultima['fecha'])} — {ultima['verificado_por']} "
             f"({'existe' if ultima['existe'] else 'NO ENCONTRADO'})") if ultima else "Sin verificación registrada"
    vida = f"{_dec(ficha['vida_util'])} años / {_tasa(ficha['tasa'])}" if ficha["vida_util"] else "No se deprecia"
    cuerpo = _encabezado("FIN-TRA-09", "Ficha Individual de Activo Fijo", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB), _campos([
        ("Código interno del activo", ficha["codigo_interno"]),
        ("Descripción", ficha["descripcion"]),
        ("Categoría / cuenta", f"{ficha['categoria_nombre']} · {ficha['cuenta_activo']}"),
        ("Fecha de adquisición / activación", fecha_pantalla(ficha["fecha_activacion"])),
        ("Costo de activación (Bs)", _bs(ficha["costo"])),
        ("Vida útil (años) / Tasa anual", vida),
        ("Destino de la cuota", f"{ficha['destino'].capitalize()} — {ficha['destino_cuota']}"),
        ("Depreciación acumulada a la fecha (Bs)", _bs(ficha["acumulada"])),
        ("Valor neto en libros (Bs)", _bs(ficha["valor_neto"])),
        ("Ubicación", ficha["ubicacion"]),
        ("Última verificación física (fecha, Consejo de Vigilancia)", verif),
        ("Estado", {"VIGENTE": "Vigente", "EN_BAJA": "Con acta de baja en trámite", "BAJA": "Dado de baja"}
         [ficha["estado"]]),
    ])]
    if ficha.get("cuotas"):
        ultimas = ficha["cuotas"][-12:]
        cuerpo += [Spacer(1, 6), _p("Últimas cuotas registradas (FIN-TRA-10)", E_SUB),
                   _tabla(["Período", "Cuota (Bs)", "Acumulada (Bs)", "Valor neto (Bs)"],
                          [[c["periodo"], _bs(c["cuota"]), _bs(c["acumulada"]), _bs(c["valor_neto"])] for c in ultimas],
                          [4, 4.4, 4.5, 4.5], (1, 2, 3))]
    cuerpo += _firmas(["Mantenida por (Unidad de Finanzas)", "Verificada por (Consejo de Vigilancia, anual)"])
    cuerpo += _riesgos("Registro individualizado que sustenta la existencia y valuación de cada bien.",
                       [_R40 + ["Preventivo", "Clasificación"], _R41 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TRA-09", cuerpo, PIE_TRA)


# ==========================================================================
# FIN-TRA-10 — Planilla Mensual de Depreciación
# ==========================================================================
def fin_tra_10(planilla: Mapping[str, Any], ctx: Contexto) -> bytes:
    """Planilla del mes: las cuotas ya registradas y, si las hay, las
    pendientes de registro (vista previa)."""
    fuente = list(planilla.get("registrado") or []) + list(planilla["filas"])
    filas = [[f"{f['codigo_interno']} {f['descripcion']}", _bs(f["costo"]), _tasa(f["tasa"]), _bs(f["cuota"]),
              (f"CIF — {f['destino']}" if f["destino"].startswith("6.") else f"Gasto Admin. — {f['destino']}")]
             for f in fuente]
    total = sum((Decimal(str(f["cuota"])) for f in fuente), Decimal("0.00"))
    filas.append(["TOTAL DEL MES", "", "", _bs(total), ""])
    cuerpo = _encabezado("FIN-TRA-10", "Planilla Mensual de Depreciación", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Mes de {planilla['mes']:02d}/{planilla['anio']} (cuota al {fecha_pantalla(planilla['fecha'])}). "
                  "Cálculo y registro mensual de la cuota de depreciación (Costo × Tasa Anual ÷ 12), imputada como "
                  "CIF al centro de costos productivo o como gasto administrativo, según el destino del bien.",
                  E_NORMAL), Spacer(1, 4),
               _tabla(["Activo", "Costo (Bs)", "Tasa Anual", "Cuota Mensual (Bs)",
                       "Destino (CIF sufijo 30-35 / Gasto Admin.)"], filas, [6.2, 2.6, 1.8, 2.6, 4.2], (1, 2, 3))]
    if planilla["filas"]:
        cuerpo.append(_p(f"Pendiente de registro: {_bs(planilla['total'])} (vista previa).", E_PEQ))
    if planilla.get("pendientes_previos"):
        cuerpo.append(_p("Meses anteriores pendientes: " + ", ".join(planilla["pendientes_previos"]), E_NORMAL))
    if planilla.get("registradas"):
        cuerpo.append(_p("Registrada en: " + "; ".join(f"asiento {r['asiento_id']} ({_bs(r['total'])})"
                                                       for r in planilla["registradas"]), E_PEQ))
    cuerpo += _firmas(["Calculado por (Unidad de Finanzas)", "Verificado por (Gerencia General)"])
    cuerpo += _riesgos("Control de exactitud del cómputo mensual de depreciación (tasas de PC-06).",
                       [_R40 + ["Preventivo", "Clasificación"]])
    return _construir("FIN-TRA-10", cuerpo, PIE_TRA)


# ==========================================================================
# Acta de Baja de Activo Fijo (PR-FIN-07 pasos 6-7)
# ==========================================================================
def acta_baja(baja: Mapping[str, Any], ctx: Contexto) -> bytes:
    resultado = baja["resultado"]
    texto_res = "Pérdida en 7.2.01.02" if resultado < 0 else ("Ganancia en 5.2.01.05" if resultado > 0 else "Sin resultado")
    verif = "No requerida (valor neto hasta el umbral de PR-FIN-07)" if not baja["requiere_cv"] else (
        "Pendiente" if baja["estado"] == "PENDIENTE_CV" else
        f"{'Conforme' if baja['conforme_cv'] else 'Observada'} — {baja['verificado_por']}: {baja['observacion_cv']}")
    cuerpo = _encabezado("ACTA-BAJA", "Acta de Baja de Activo Fijo", "Gerencia General", TRANSFORMACION)
    cuerpo += _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB), _campos([
        ("Acta n.º", baja["acta_ref"]),
        ("Activo", f"{baja['codigo_interno']} — {baja['descripcion']}"),
        ("Fecha de baja", fecha_pantalla(baja["fecha"])),
        ("Causa", f"{CAUSAS_BAJA[baja['causa']]}: {baja['detalle_causa']}"),
        ("Destino final", baja["destino_final"]),
        ("Costo histórico (Bs)", _bs(baja["costo"])),
        ("Depreciación acumulada (Bs)", _bs(baja["acumulada"])),
        ("Valor neto en libros (Bs)", _bs(baja["valor_neto"])),
        ("Valor de realización (Bs)", _bs(baja["valor_realizacion"])),
        ("Resultado (Bs)", f"{_bs(resultado)} — {texto_res}"),
        ("Verificación del Consejo de Vigilancia", verif),
        ("Estado", {"PENDIENTE_CV": "Pendiente de verificación del CV", "OBSERVADA": "Observada por el CV",
                    "AUTORIZADA": "Autorizada, pendiente de registro", "CONTABILIZADA":
                    f"Contabilizada (asiento {baja['asiento_id']})"}[baja["estado"]]),
    ])]
    cuerpo += _firmas(["Emitida por (Gerencia General)", "Verificada por (Consejo de Vigilancia)",
                       "Registrada por (Unidad de Finanzas)"], [baja["autorizado_por"], baja.get("verificado_por") or "",
                                                                baja.get("contabilizado_por") or ""])
    cuerpo += _riesgos("Toda baja requiere autorización escrita de la Gerencia y notificación al CV (PC-06 §V.C).",
                       [_R41 + ["Preventivo", "Protección Física"], _R28 + ["Preventivo", "Protección Física"]])
    return _construir("ACTA-BAJA", cuerpo, PIE_TRA)


# ==========================================================================
# INF-CV-01 — Acta de Verificación Física de Activos
# ==========================================================================
def inf_cv_01(acta: Mapping[str, Any], ctx: Contexto) -> bytes:
    filas = []
    for n, d in enumerate(acta["detalle"], start=1):
        obs = d["observacion"] or ""
        if not d["tasa_coincide"]:
            obs = (obs + "; " if obs else "") + f"Tasa de la ficha {_tasa(d['tasa_ficha'])} ≠ PC-06 " \
                                                f"{_tasa(d['tasa_politica'])}"
        filas.append([str(n), d["descripcion"], d["codigo_interno"], "Sí" if d["existe"] else "No",
                      (d["estado_conservacion"] or "—").capitalize(), "Sí" if d["existe"] else "No", obs])
    cuerpo = _encabezado("INF-CV-01", "Acta de Verificación Física de Activos", "Consejo de Vigilancia",
                         "Ciclo 1 — Tesorería / Ciclo 3 — Transformación") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Inspección física del {fecha_pantalla(acta['fecha'])} (ejercicio {acta['ejercicio']}): "
                  f"{acta['activos_verificados']} activos verificados, {acta['faltantes']} no encontrados, "
                  f"{acta['tasas_distintas']} con tasa distinta de PC-06.", E_NORMAL), Spacer(1, 4),
               _tabla(["N°", "Activo Verificado", "Código Interno", "Existe Físicamente (Sí/No)",
                       "Estado de Conservación", "Coincide con Registro Contable (Sí/No)", "Observaciones"], filas,
                      [0.8, 4.2, 2.2, 1.8, 2, 2.2, 4.2])]
    if acta.get("observacion"):
        cuerpo.append(_p(f"Observación general: {acta['observacion']}", E_NORMAL))
    cuerpo += _firmas(["Verificado por (Consejo de Vigilancia)", "Recibido por (Gerencia General)"],
                      [acta["verificado_por"], ""])
    cuerpo += _riesgos("Control de detección/verificación física que cierra el ciclo de autorización de CAPEX.",
                       [_R28 + ["Detección", "Protección Física"], _R41 + ["Detección", "Protección Física"]])
    return _construir("INF-CV-01", cuerpo, "Consejo de Vigilancia")


# ==========================================================================
# Libro de Inventario de Activos Fijos (POL-02; PR-FIN-07)
# ==========================================================================
def libro_inventario(libro: Sequence[Mapping[str, Any]], ctx: Contexto) -> bytes:
    filas = [[a["codigo_interno"], a["descripcion"], fecha_pantalla(a["fecha_activacion"]), _bs(a["costo"]),
              _tasa(a["tasa"]), _bs(a["acumulada"]), _bs(a["valor_neto"]),
              {"VIGENTE": "Vigente", "EN_BAJA": "En baja", "BAJA": "Baja"}[a["estado"]]] for a in libro]
    vigentes = [a for a in libro if a["estado"] != "BAJA"]
    filas.append(["TOTAL", "Activos vigentes", "", _bs(sum((a["costo"] for a in vigentes), Decimal("0.00"))), "",
                  _bs(sum((a["acumulada"] for a in vigentes), Decimal("0.00"))),
                  _bs(sum((a["valor_neto"] for a in vigentes), Decimal("0.00"))), ""])
    cuerpo = _encabezado("LIB-AF", "Libro de Inventario de Activos Fijos", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Registro individualizado de cada activo (POL-02, responsabilidades de la Unidad de Finanzas).",
                  E_NORMAL), Spacer(1, 4),
               _tabla(["Código", "Descripción", "Activación", "Costo (Bs)", "Tasa", "Dep. acumulada (Bs)",
                       "Valor neto (Bs)", "Estado"], filas, [2, 4.6, 1.8, 2.2, 1.4, 2.2, 2.2, 1.2], (3, 4, 5, 6))]
    cuerpo += _firmas(["Mantenido por (Unidad de Finanzas)", "Revisado por (Consejo de Vigilancia)"])
    return _construir("LIB-AF", cuerpo, PIE_TRA)
