"""Formatos PDF del Ciclo 2 — Egresos / Planillas (Fase 5), con reportlab.

Replican los formatos del Anexo 6: CIR-EGR-01 (Circular de Aviso de Cierre),
FIN-EGR-06 (Autorización de Personal Eventual), CONT-EGR-01 (Contrato Eventual),
FIN-EGR-08 (Liquidación de Jornales), FIN-EGR-09 (Planilla de Sueldos),
FIN-EGR-10 (Beneficios Sociales), FIN-EGR-11 (Liquidación Final), FIN-EGR-12
(Hojas de Tiempo) y FIN-EGR-13 (Absorción de Mano de Obra), con bloque de firmas
y riesgos mitigados (MAN-FIN-001).

Reciben los diccionarios de ``servicios.planillas`` y devuelven bytes. No
importan ``streamlit``.
"""
from __future__ import annotations

from typing import Any, Sequence

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import (
    E_NORMAL, E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _si_no, _tabla,
)
from utils.formato import fecha_pantalla

PIE = "Ciclo 2 — Egresos"
PLANILLAS = "Ciclo 2 — Egresos (Planillas)"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

__all__ = ["Contexto", "cir_egr_01", "fin_egr_06", "cont_egr_01", "fin_egr_08", "fin_egr_09", "fin_egr_10",
           "fin_egr_11", "fin_egr_12", "fin_egr_13"]


def _riesgos(nota: str, filas: Sequence[Sequence[str]]) -> list[Any]:
    return [Spacer(1, 8), _p("Riesgos Mitigados — Referencia al Mapa de Riesgos (MAN-FIN-001)", E_SUB),
            _p(nota, E_PEQ), Spacer(1, 3),
            _tabla(["N°", "Riesgo (síntesis)", "Nivel", "Tipo de Control", "Objetivo COSO"], filas,
                   [1, 8.6, 2.2, 2.6, 3])]


_R5 = ["5", "Retraso en la contratación de personal eventual para el faenado, con pérdida del lote semanal.", "Alto"]
_R18 = ["18", "Sobrecosto de mano de obra eventual respecto de la tarifa de destajo establecida.", "Moderado"]
_R19 = ["19", "Cálculo incorrecto o insuficiente de las provisiones de indemnización, aguinaldo o aportes patronales.",
        "Moderado"]
_R20 = ["20", "Distorsión del costo unitario por prorrateo de mano de obra polivalente mal calculado o no verificado.",
        "Moderado"]
_R38 = ["38", "Pago de aportes patronales o de beneficios sociales fuera de los plazos legales, con riesgo de multa.",
        "Bajo"]


def _periodo(planilla: dict[str, Any]) -> str:
    return f"{MESES[int(planilla['mes']) - 1]} de {planilla['anio']}"


# ==========================================================================
# CIR-EGR-01 — Circular de Aviso de Cierre de Ciclo Productivo
# ==========================================================================
def cir_egr_01(circular: dict[str, Any], ctx: Contexto) -> bytes:
    numero = f"CIR-EGR-01 / {circular['numero']} / {circular['anio']}"
    cuerpo = _encabezado("CIR-EGR-01", "Circular de Aviso de Cierre de Ciclo Productivo", "Unidad Técnica", PLANILLAS)
    cuerpo += [_tabla(["Campo", "Detalle"], [
        ["N° de Circular", numero], ["Fecha de emisión", fecha_pantalla(circular["fecha_emision"])],
        ["De (emisor)", "Unidad Técnica"], ["Para (destinatarios)", "Gerencia General; Unidad de Finanzas y Comercialización"],
        ["Asunto", "Aviso de cierre de ciclo productivo — Día 73 del lote en curso"]], [5, 12.4]),
        _p("Cuerpo del Aviso", E_SUB),
        _p("Por medio de la presente, la Unidad Técnica comunica la fecha estimada de faenado del lote en curso y el "
           "volumen de aves a procesar, a fin de preparar con antelación la contratación de personal eventual "
           "(PR-FIN-04) y la verificación presupuestaria (FIN-EGR-02).", E_NORMAL), Spacer(1, 4),
        _tabla(["Dato", "Detalle"], [
            ["Fecha estimada de faenado (día 73)", fecha_pantalla(circular["fecha_faenado"])],
            ["N° de lote", circular["lote"]], ["Volumen de aves a procesar", f"{circular['aves']:,}".replace(",", ".")],
            ["Mortalidad acumulada del lote a la fecha", f"{circular['mortalidad_pct']} %".replace(".", ",")]], [8, 9.4]),
        Spacer(1, 4), _p("Referencia normativa: Procedimiento PR-FIN-04, Paso 1; Mapa de Procesos Integral, PO-04.", E_PEQ),
        _p("Acuse de Recibo", E_SUB),
        _tabla(["Destinatario", "Fecha de Recepción", "Firma"], [
            ["Gerencia General", (circular.get("acuse_gg_en") or "")[:10], circular.get("acuse_gg_por") or ""],
            ["Unidad de Finanzas y Comercialización", (circular.get("acuse_jf_en") or "")[:10],
             circular.get("acuse_jf_por") or ""]], [7, 4.4, 6])]
    cuerpo += _riesgos("Comunicación que habilita con antelación la contratación y la verificación presupuestaria.",
                       [_R5 + ["Preventivo", "Información y Comunicación"]])
    return _construir("CIR-EGR-01", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-06 — Autorización de Contratación de Personal Eventual
# ==========================================================================
def fin_egr_06(eventual: dict[str, Any], ctx: Contexto) -> bytes:
    c = eventual["circular"]
    cuerpo = _encabezado("FIN-EGR-06", "Autorización de Contratación de Personal Eventual", "Gerencia General",
                         PLANILLAS) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Autorizada el lunes {fecha_pantalla(eventual['fecha_lunes'])}, según el volumen del lote "
                  f"{c['lote']} ({eventual['aves']} aves) y la tarifa de destajo vigente. Presupuesto de faena: "
                  f"{_bs(eventual['presupuesto'])}. En caso de suplencia aplica el Anexo CI-FIN-17.", E_NORMAL),
               Spacer(1, 4),
               _tabla(["N° de Trabajadores a Contratar", "Tarifa de Destajo Vigente (Bs/unidad)", "Fecha de Faenado",
                       "¿Autorizado por Suplente? (CI-FIN-17)"],
                      [[str(eventual["trabajadores"]), _bs(eventual["tarifa"]), fecha_pantalla(c["fecha_faenado"]),
                        _si_no(bool(eventual["por_suplencia"]))]], [4, 4.4, 3.6, 5.4])]
    cuerpo += _firmas(["Autorizado por (Gerencia General / Suplente CI-FIN-17)",
                       "Verificación presupuestaria previa (Unidad de Finanzas)"],
                      [eventual.get("autorizado_por") or "", eventual["solicitado_por"]])
    cuerpo += _riesgos("Control de autorización del gasto variable de faenado.", [_R5 + ["Preventivo", "Autorización"]])
    return _construir("FIN-EGR-06", cuerpo, PIE)


# ==========================================================================
# CONT-EGR-01 — Contrato Eventual por Servicio de Faenado
# ==========================================================================
def cont_egr_01(eventual: dict[str, Any], contrato: dict[str, Any], ctx: Contexto) -> bytes:
    c = eventual["circular"]
    clausulas = [
        ("Primera — Objeto", "El Contratado prestará, por obra o servicio determinado, el servicio de faenado del lote "
                             f"{c['lote']} conforme al Instructivo de Inocuidad Alimentaria en el Faenado (CI-FIN-12)."),
        ("Segunda — Naturaleza del vínculo", "Personal técnico de apoyo operativo (Ley N° 356, Art. 18); no genera "
                                             "relación de dependencia laboral permanente."),
        ("Tercera — Plazo", f"Por la duración del faenado del {fecha_pantalla(c['fecha_faenado'])}, concluyendo con la "
                            "liquidación de jornales (FIN-EGR-08)."),
        ("Cuarta — Remuneración", f"Por destajo, a {_bs(eventual['tarifa'])} por unidad faenada (FIN-EGR-06)."),
        ("Quinta — Obligaciones del Contratado", "Cumplir las normas sanitarias y de cadena de frío del CI-FIN-12 y usar "
                                                 "el equipo de bioseguridad provisto."),
        ("Sexta — Obligaciones de la Cooperativa", "Proveer el equipo de bioseguridad y liquidar el jornal al concluir "
                                                   "el servicio."),
        ("Séptima — Terminación", "Concluye con el faenado del lote y la liquidación del jornal, sin preaviso ni "
                                  "indemnización."),
        ("Octava — Jurisdicción", "Ley General del Trabajo, Ley N° 356 (Art. 18) y normas civiles aplicables."),
    ]
    cuerpo = _encabezado("CONT-EGR-01", "Contrato Eventual por Servicio de Faenado",
                         "Personal técnico de apoyo operativo (Ley N° 356, Art. 18)", PLANILLAS)
    cuerpo += [_tabla(["Parte", "Datos"], [
        ["Contratante", "Cooperativa Agropecuaria Pollolandia R.L., representada por su Gerente General"],
        ["Contratado (personal eventual)", f"{contrato['nombre']} · C.I. {contrato['ci']}"],
        ["Fecha", fecha_pantalla(contrato["fecha"])]], [5, 12.4]), _p("Cláusulas", E_SUB)]
    for titulo, texto in clausulas:
        cuerpo.append(_p(f"{titulo}.— {texto}", E_NORMAL))
    cuerpo += _firmas(["Por la Cooperativa (Gerencia General)", "El Contratado (Personal Eventual)"],
                      [eventual.get("autorizado_por") or "", contrato["nombre"]])
    return _construir("CONT-EGR-01", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-08 — Liquidación de Jornales Eventuales
# ==========================================================================
def fin_egr_08(liquidacion: dict[str, Any], ctx: Contexto) -> bytes:
    filas = [[f"{l['nombre']} (C.I. {l['ci']})", f"{l['unidades']:,}".replace(",", "."), _bs(l["tarifa"]),
              _bs(l["total"])] for l in liquidacion["lineas"]]
    filas.append(["Total", f"{liquidacion['unidades']:,}".replace(",", "."), "", _bs(liquidacion["total"])])
    cuerpo = _encabezado("FIN-EGR-08", "Liquidación de Jornales Eventuales", "Unidad de Finanzas y Comercialización",
                         PLANILLAS) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Liquidación y conciliación de los jornales a la conclusión del faenado, con cargo a la cuenta 2.1.02.04 "
                  "— Personal Eventual por Pagar (sufijo 22 del centro 6.1.01.01).", E_NORMAL), Spacer(1, 4),
               _tabla(["Trabajador Eventual", "Unidades Faenadas", "Tarifa de Destajo (Bs)", "Total a Pagar (Bs)"], filas,
                      [7.4, 3.2, 3.4, 3.4], (1, 2, 3)), Spacer(1, 6),
               _p(f"Desviación del Presupuesto de Faenado (DPF) = {_bs(liquidacion['total'])} / "
                  f"{_bs(liquidacion['presupuesto'])} × 100 % = {str(liquidacion['dpf']).replace('.', ',')} % — "
                  f"{liquidacion['semaforo'].capitalize()}.", E_NORMAL),
               _p("Umbral del DPF: Malo > 105 % · Normal 100-105 % · Bueno ≤ 100 %.", E_PEQ)]
    cuerpo += _firmas(["Liquidado por (Unidad de Finanzas)", "Autorizado el pago por (Gerencia General)"],
                      [liquidacion["liquidado_por"], liquidacion.get("pago_autorizado_por") or ""])
    cuerpo += _riesgos("Control de verificación del gasto variable de faenado.",
                       [_R18 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-EGR-08", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-09 — Planilla de Sueldos / FIN-EGR-10 — Beneficios Sociales
# ==========================================================================
def fin_egr_09(planilla: dict[str, Any], ctx: Contexto) -> bytes:
    filas = [[l["nombre"], l["cargo"], _bs(l["haber_basico"]), _bs(l["bonos"]), _bs(l["aporte_laboral"]),
              _bs(l["liquido"])] for l in planilla["lineas"]]
    filas.append(["Totales", "", _bs(sum(l["haber_basico"] for l in planilla["lineas"])),
                  _bs(sum(l["bonos"] for l in planilla["lineas"])), _bs(planilla["aporte_laboral"]),
                  _bs(planilla["liquido"])])
    cuerpo = _encabezado("FIN-EGR-09", "Planilla de Sueldos", "Unidad de Finanzas y Comercialización", PLANILLAS)
    cuerpo += _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Planilla mensual del personal fijo de {_periodo(planilla)}: haber básico, bonos y descuento de ley "
                  "(aporte laboral 12,71 % del total ganado, cuenta 2.1.02.05).", E_NORMAL), Spacer(1, 4),
               _tabla(["Trabajador", "Cargo", "Haber Básico (Bs)", "Bonos (Bs)", "Descuentos (Bs)", "Líquido Pagable (Bs)"],
                      filas, [3.8, 3.4, 2.6, 2.2, 2.6, 2.8], (2, 3, 4, 5)), Spacer(1, 4),
               _p(f"Vencimiento legal del pago de sueldos: {fecha_pantalla(planilla['vencimientos']['sueldos'])}.", E_PEQ)]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Autorizado por (Gerencia General)"],
                      [planilla["elaborado_por"], planilla.get("autorizado_por") or ""])
    cuerpo += _riesgos("Insumo base del cálculo de beneficios sociales.", [_R19 + ["Preventivo", "Clasificación"]])
    return _construir("FIN-EGR-09", cuerpo, PIE)


def fin_egr_10(planilla: dict[str, Any], ctx: Contexto) -> bytes:
    filas = [[l["nombre"], _bs(l["total_ganado"]), _bs(l["indemnizacion"]), _bs(l["aguinaldo"]), _bs(l["aporte_patronal"])]
             for l in planilla["lineas"]]
    filas.append(["Totales", _bs(planilla["total_ganado"]), _bs(planilla["indemnizacion"]), _bs(planilla["aguinaldo"]),
                  _bs(planilla["aporte_patronal"])])
    cuerpo = _encabezado("FIN-EGR-10", "Planilla de Beneficios Sociales", "Unidad de Finanzas y Comercialización",
                         PLANILLAS) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Provisiones de {_periodo(planilla)}: indemnización (2.2.01.01) y aguinaldo (2.1.02.02) de 1/12 "
                  "(8,33 %) y aporte patronal a la Gestora Pública y la CNS de 17,21 % (2.1.02.03), sobre el salario bruto. "
                  "Personal de granja: sufijos 20, 21, 38 y 39; administrativo: 7.1.01.01 a 7.1.01.04.", E_NORMAL),
               Spacer(1, 4),
               _tabla(["Trabajador", "Salario Bruto (Bs)", "Provisión Indemnización 8,33%", "Provisión Aguinaldo 8,33%",
                       "Aporte Patronal 17,21%"], filas, [4.6, 3.2, 3.2, 3.2, 3.2], (1, 2, 3, 4)), Spacer(1, 4),
               _p(f"Vencimiento legal de los aportes: {fecha_pantalla(planilla['vencimientos']['aportes'])}.", E_PEQ)]
    cuerpo += _firmas(["Calculado por (Unidad de Finanzas)", "Verificado por (Gerencia General)"],
                      [planilla["elaborado_por"], planilla.get("autorizado_por") or ""])
    cuerpo += _riesgos("Control de suficiencia de las provisiones laborales.",
                       [_R19 + ["Preventivo", "Clasificación"], _R38 + ["Preventivo", "Verificación y Evaluación"]])
    return _construir("FIN-EGR-10", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-11 — Liquidación Final de Beneficios
# ==========================================================================
def fin_egr_11(liquidacion: dict[str, Any], ctx: Contexto) -> bytes:
    filas = [["Indemnización por tiempo de servicios", _bs(liquidacion["indemnizacion"])],
             [f"Vacaciones no gozadas ({str(liquidacion['vacaciones_dias']).replace('.', ',')} días)",
              _bs(liquidacion["vacaciones"])],
             ["Aguinaldo proporcional", _bs(liquidacion["aguinaldo"])],
             [f"Salario proporcional del último mes ({liquidacion['salario_dias']} días)", _bs(liquidacion["salario"])],
             ["(–) Aporte laboral sobre el salario proporcional", _bs(liquidacion["aporte_laboral"])],
             ["Total líquido a pagar", _bs(liquidacion["total"])]]
    plazo = ("dentro del plazo" if liquidacion["dentro_de_plazo"] else
             f"FUERA DE PLAZO — causa: {liquidacion['causa_retraso']} (se reporta al Consejo de Vigilancia)")
    cuerpo = _encabezado("FIN-EGR-11", "Liquidación Final de Beneficios", "Unidad de Finanzas y Comercialización",
                         PLANILLAS) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"{liquidacion['nombre']} (C.I. {liquidacion['ci']}), {liquidacion['cargo']}: ingreso "
                  f"{fecha_pantalla(liquidacion['fecha_ingreso'])}, retiro {fecha_pantalla(liquidacion['fecha_retiro'])}, "
                  f"{liquidacion['dias_servicio']} días de servicio (30/360). Promedio de los últimos 3 meses: "
                  f"{_bs(liquidacion['promedio'])}. Preparada el {fecha_pantalla(liquidacion['fecha_preparacion'])}, "
                  f"límite {fecha_pantalla(liquidacion['fecha_limite'])}: {plazo}.", E_NORMAL), Spacer(1, 4),
               _tabla(["Concepto", "Monto (Bs)"], filas, [12.4, 5], (1,))]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Autorizado por (Gerencia General)"],
                      [liquidacion["elaborado_por"], liquidacion.get("autorizado_por") or ""])
    cuerpo += _riesgos("Control de exactitud de la liquidación final.",
                       [_R19 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-EGR-11", cuerpo, PIE)


# ==========================================================================
# FIN-EGR-12 — Hojas de Tiempo / FIN-EGR-13 — Absorción de Mano de Obra
# ==========================================================================
def fin_egr_12(hojas: Sequence[dict[str, Any]], trabajador: str, ctx: Contexto) -> bytes:
    filas = [[fecha_pantalla(h["fecha"]), str(h["horas_crianza"]), str(h["horas_alimento"]),
              str(h["horas_crianza"] + h["horas_alimento"]),
              "Firmada" if h["firmada_trabajador"] else ("Regularizada" if h.get("regularizacion") else "Sin firma")]
             for h in sorted(hojas, key=lambda x: x["fecha"])]
    cuerpo = _encabezado("FIN-EGR-12", "Hojas de Tiempo Firmadas", "Unidad Técnica", "Ciclo 2 — Egresos (Transformación)")
    cuerpo += _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Registro diario de {trabajador}, validado por el Encargado de Granja (CI-FIN-03). Las hojas sin firma "
                  "no se usan en el prorrateo hasta su regularización.", E_NORMAL), Spacer(1, 4),
               _tabla(["Día", "Horas — Crianza/Faenado", "Horas — Planta de Alimento", "Total Horas del Día",
                       "Firma del Trabajador"], filas, [3, 3.6, 3.6, 3.2, 4], (1, 2, 3))]
    cuerpo += _firmas(["Validado por (Encargado de Granja)", "Recibido por (Contabilidad)"])
    cuerpo += _riesgos("Insumo base del prorrateo de mano de obra.", [_R20 + ["Preventivo", "Clasificación"]])
    return _construir("FIN-EGR-12", cuerpo, PIE)


def fin_egr_13(conciliaciones: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    filas = [[f"{MESES[int(c['mes']) - 1].capitalize()} {c['anio']}", _bs(c["costo_pagado"]),
              _bs(c["absorbido_carne"] + c["absorbido_alimento"]), _bs(c["diferencia"]),
              c.get("observacion_ci_fin_14") or "—"] for c in conciliaciones]
    cuerpo = _encabezado("FIN-EGR-13", "Reporte de Absorción de Mano de Obra",
                         "Unidad de Finanzas y Comercialización / Consejo de Vigilancia",
                         "Ciclo 2 — Egresos (Transformación)") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Conciliación mensual entre el costo de planilla del personal de granja y lo absorbido por los centros "
                  "de costo 6.1.01.01 y 6.1.01.02 (sufijos 20, 21, 38 y 39), con las observaciones de la verificación "
                  "cruzada (CI-FIN-14).", E_NORMAL), Spacer(1, 4),
               _tabla(["Mes", "Costo de Planilla Pagado (Bs)", "Costo Absorbido por Centros de Costo (Bs)",
                       "Diferencia (Bs)", "Observación CI-FIN-14"], filas, [2.6, 3.4, 3.8, 2.8, 4.8], (1, 2, 3))]
    fiscalizado = next((c["fiscalizado_por"] for c in conciliaciones if c.get("fiscalizado_por")), "")
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Fiscalizado por (Consejo de Vigilancia)"],
                      [ctx.elaborado_por, fiscalizado or ""])
    cuerpo += _riesgos("Control de conciliación mensual entre lo pagado y lo absorbido.",
                       [_R20 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-EGR-13", cuerpo, PIE)
