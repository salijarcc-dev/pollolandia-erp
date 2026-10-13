"""Formatos PDF del ciclo de ingresos (Fase 9), con reportlab.

Replican los formatos del Anexo 8 (Ciclo de Ingresos): FIN-ING-01 (Registro
de Pedido), FIN-ING-02 (Resolución de Autorización de Crédito), FIN-ING-03
(Nota de Entrega HORECA), FIN-ING-04 (Comprobante de Entrega y Compromiso de
Pago), FIN-ING-05 (Registro de Cuentas por Cobrar), FIN-ING-06 (Nota de
Entrega al Contado), FIN-ING-07 (Parte Diario de Recaudación), FIN-ING-08
(Análisis de Margen por Canal) y CIR-ING-01 (Suspensión de Línea de Crédito),
más la verificación de cobertura del lote (CI-FIN-13 Art. 8) y el tarifario
oficial (CI-FIN-06), con bloque de firmas y riesgos mitigados (MAN-FIN-001).

Reciben los diccionarios de ``servicios.ventas`` y devuelven bytes. No
importan ``streamlit``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import (
    E_NORMAL, E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _tabla,
)
from servicios.ventas import NOMBRE_CANAL, NOMBRE_MEDIO, PRODUCTOS, cantidad_texto, pct_texto
from utils.formato import fecha_pantalla

INGRESOS = "Ciclo 4 — Ingresos (Comercialización)"
PIE = "Ciclo 4 — Ingresos"

__all__ = ["Contexto", "fin_ing_01", "fin_ing_02", "fin_ing_03", "fin_ing_04", "fin_ing_05", "fin_ing_06",
           "fin_ing_07", "fin_ing_08", "cir_ing_01", "cobertura_ci_fin_13", "tarifario"]

_R10 = ["10", "Incobrabilidad de cuentas por cobrar HORECA por falta de seguimiento oportuno al vencimiento del "
        "crédito.", "Alto"]
_R11 = ["11", "Faenado de un lote sin contar previamente con comprador confirmado, con pérdida total del flujo de "
        "efectivo de la semana ante la ausencia de capacidad de refrigeración.", "Alto (NR=6)"]
_R24 = ["24", "Otorgamiento de crédito HORECA fuera de las condiciones autorizadas por la POL-05, o seguimiento de "
        "cobranza tardío que derive en cuenta incobrable.", "Moderado"]
_R25 = ["25", "Volumen del lote no reservado por HORECA que llega al día del faenado sin ningún comprador de respaldo "
        "en mercado popular o friales.", "Moderado"]
_R26 = ["26", "Precios desactualizados frente al mercado convencional, con deterioro sostenido del margen bruto por "
        "canal.", "Moderado"]
_R42 = ["42", "Entrega de producto sin comprobante de pago correspondiente, en contravención de la política de venta al "
        "contado.", "Bajo"]


def _riesgos(nota: str, filas: Sequence[Sequence[str]]) -> list[Any]:
    return [Spacer(1, 8), _p("Riesgos Mitigados — Referencia al Mapa de Riesgos (MAN-FIN-001)", E_SUB),
            _p(nota, E_PEQ), Spacer(1, 3),
            _tabla(["N°", "Riesgo (síntesis)", "Nivel", "Tipo de Control", "Objetivo COSO"], filas,
                   [1, 8.6, 2.2, 2.6, 3])]


def _si(valor: Any) -> str:
    return "Sí" if valor else "No"


def _dec(valor: Any, decimales: int = 2) -> str:
    if valor is None:
        return "—"
    texto = f"{Decimal(str(valor)):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _cuerpo(codigo: str, titulo: str, area: str, ctx: Contexto, descripcion: str) -> list[Any]:
    return _encabezado(codigo, titulo, area, INGRESOS) + _contexto(ctx) + [
        _p("Cuerpo de Datos", E_SUB), _p(descripcion, E_NORMAL), Spacer(1, 4)]


# ==========================================================================
# FIN-ING-01 — Registro de Pedido
# ==========================================================================
def fin_ing_01(pedidos: Sequence[Mapping[str, Any]], ctx: Contexto) -> bytes:
    filas = [[p["cliente"], f"{p['aves']} aves ({_dec(p['kg_estimado'], 1)} kg) · {PRODUCTOS[p['producto']]}"
              + (" — ANULADO" if p["estado"] == "ANULADO" else ""), fecha_pantalla(p["fecha_pedido"]),
              fecha_pantalla(p["fecha_faenado"]), str(p["anticipacion_dias"]),
              ("Sí" if p["cumple_anticipacion"] else "No") if p["tipo"] == "HORECA" else "Indicativo"]
             for p in pedidos]
    cuerpo = _cuerpo("FIN-ING-01", "Registro de Pedido", "Unidad de Finanzas y Comercialización", ctx,
                     "Recepción del pedido del cliente HORECA, verificando que exista tiempo suficiente para "
                     "confirmarlo dentro del plazo mínimo de anticipación exigido por el Reglamento de Pre-Venta "
                     "CI-FIN-13 (3 días antes del faenado). Los pedidos de friales y mercado popular son indicativos.")
    cuerpo.append(_tabla(["Cliente", "Producto/Volumen Solicitado", "Fecha del Pedido",
                          "Fecha Estimada de Faenado (CIR-EGR-01)", "Anticipación (días)",
                          "¿Cumple mínimo de 3 días? (Sí/No)"], filas, [3.4, 4.6, 2, 2.6, 1.8, 3]))
    cuerpo += _firmas(["Registrado por (Unidad de Finanzas)"])
    cuerpo += _riesgos("Un «No» en la última columna activa de inmediato el Artículo 5° del Reglamento CI-FIN-13 "
                       "(respaldo por mercado popular y friales).",
                       [_R11 + ["Preventivo", "Autorización"], _R10 + ["Preventivo", "Autorización"]])
    return _construir("FIN-ING-01", cuerpo, PIE)


# ==========================================================================
# FIN-ING-02 — Resolución de Autorización de Crédito
# ==========================================================================
def fin_ing_02(r: Mapping[str, Any], ctx: Contexto) -> bytes:
    cuerpo = _cuerpo("FIN-ING-02", "Resolución de Autorización de Crédito", "Gerencia General", ctx,
                     "Autorización escrita de la Gerencia General para operar a crédito con plazos superiores a un (1) "
                     "día, exclusiva del régimen 2028 en adelante (POL-05, Art. 2).")
    cuerpo.append(_tabla(["Cliente HORECA", "Historial Comercial (≥6 meses) (Sí/No)", "Plazo Autorizado (máx. 7 días)",
                          "Monto Máximo (≤1 entrega semanal)", "Evaluación de Capacidad de Pago"],
                         [[r["cliente"], f"Sí ({r['historial_meses']} meses)", f"{r['plazo_dias']} días",
                           f"{_bs(r['monto_maximo'])} (entrega semanal {_bs(r['entrega_semanal'])})", r["evaluacion"]]],
                         [3.2, 3, 2.6, 3.6, 5]))
    cuerpo.append(_p(f"Resolución del {fecha_pantalla(r['fecha'])}. Estado: {r['estado'].capitalize()}.", E_PEQ))
    cuerpo += _firmas(["Autorizado por (Gerencia General)", "Comunicado a (Consejo de Vigilancia)"],
                      [r["autorizada_por"], ""])
    cuerpo += _riesgos("Las tres condiciones acumulativas de POL-05 Art. 2 (historial, plazo, monto) se verifican "
                       "las tres, no de forma alternativa.", [_R24 + ["Preventivo", "Autorización"]])
    return _construir("FIN-ING-02", cuerpo, PIE)


# ==========================================================================
# FIN-ING-03 / FIN-ING-06 — Notas de Entrega
# ==========================================================================
def fin_ing_03(n: Mapping[str, Any], ctx: Contexto) -> bytes:
    cuerpo = _cuerpo("FIN-ING-03", f"Nota de Entrega (HORECA) n.º {n['numero']}",
                     "Unidad Técnica / Unidad de Finanzas y Comercialización", ctx,
                     "Documenta el traslado del pollo faenado al cliente HORECA, cumpliendo la política de despacho "
                     "inmediato post-faena.")
    cuerpo.append(_tabla(["Cliente HORECA", "Producto (Entero/Cortes)", "Cantidad (kg)",
                          "Precio por Kilo (Tarifario CI-FIN-06)", "Total (Bs)", "Hora de Entrega"],
                         [[n["cliente"], PRODUCTOS[n["producto"]], _dec(n["cantidad"], 3), _bs(n["precio"]),
                           _bs(n["total"]), n["hora_entrega"]]], [3.6, 3.4, 2.2, 3, 2.6, 2.6], (2, 3, 4)))
    cuerpo.append(_p(f"Condición: {'crédito de ' + str(n['dias_credito']) + ' día(s), vence ' + fecha_pantalla(n['vencimiento']) if n['condicion'] == 'CREDITO' else 'contado'} · "
                     f"medio: {NOMBRE_MEDIO[n['medio_pago']]} · comprobante: {n['comprobante_ref']} · "
                     f"Certificado RAU exhibido: {n['certificado_rau_ref']}.", E_NORMAL))
    cuerpo += _firmas(["Entregado por (Unidad Técnica)", "Recibido conforme (Cliente HORECA)"],
                      [n["entregado_por"], n["recibido_por"]])
    cuerpo += _riesgos("Sin nota de entrega firmada por el cliente no existe respaldo documental de la venta a crédito "
                       "(POL-05, Art. 1).", [_R10 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-ING-03", cuerpo, PIE)


def fin_ing_06(n: Mapping[str, Any], ctx: Contexto) -> bytes:
    cuerpo = _cuerpo("FIN-ING-06", f"Nota de Entrega (Venta al Contado) n.º {n['numero']}",
                     "Unidad de Finanzas y Comercialización", ctx,
                     "Documenta el despacho de pollo faenado, gallinaza o alimento balanceado excedente hacia friales, "
                     "mercados populares o el comprador de gallinaza, exclusivamente al contado (POL-05, Art. 1).")
    cuerpo.append(_tabla(["Comprador (Frial/Mercado/Agricultor)", "Producto", "Cantidad",
                          "Precio (Tarifario CI-FIN-06)", "Total (Bs)", "Medio de Pago"],
                         [[n["cliente"], PRODUCTOS[n["producto"]], cantidad_texto(n["cantidad"], n["unidad"]),
                           _bs(n["precio"]), _bs(n["total"]), NOMBRE_MEDIO[n["medio_pago"]]]],
                         [3.8, 3.4, 2.2, 2.8, 2.6, 2.6], (3, 4)))
    cuerpo.append(_p(f"Comprobante de pago: {n['comprobante_ref']} · Certificado RAU exhibido: "
                     f"{n['certificado_rau_ref']} · hora {n['hora_entrega']}.", E_NORMAL))
    cuerpo += _firmas(["Entregado por (Unidad Técnica)", "Recibido conforme (Comprador)"],
                      [n["entregado_por"], n["recibido_por"]])
    cuerpo += _riesgos("Queda prohibido entregar el producto sin la constancia documentada correspondiente (POL-05, "
                       "Art. 1).", [_R25 + ["Preventivo", "Autorización"], _R42 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-ING-06", cuerpo, PIE)


# ==========================================================================
# FIN-ING-04 — Comprobante de Entrega y Compromiso de Pago
# ==========================================================================
def fin_ing_04(n: Mapping[str, Any], ctx: Contexto) -> bytes:
    medios = "  ".join(("[X] " if n["medio_pago"] == m else "[ ] ") + NOMBRE_MEDIO[m]
                       for m in ("EFECTIVO", "TRANSFERENCIA", "DEPOSITO", "CHEQUE_NOMINATIVO"))
    cuerpo = _cuerpo("FIN-ING-04", "Comprobante de Entrega y Compromiso de Pago", "Unidad de Finanzas y Comercialización",
                     ctx, "Formaliza, en las ventas HORECA a crédito, el compromiso de pago firmado por el comprador, "
                          "incorporando el control de cobranza rápida a 24 horas.")
    cuerpo.append(_tabla(["Cliente HORECA", "Monto Comprometido (Bs)", "Fecha de Entrega",
                          "Fecha Límite de Pago (+1 día / hasta +7 días desde 2028)", "Medio de Pago Comprometido"],
                         [[n["cliente"], _bs(n["total"]), fecha_pantalla(n["fecha"]),
                           fecha_pantalla(n["vencimiento"]) if n["vencimiento"] else "Contado", medios]],
                         [3.2, 2.8, 2.4, 3.4, 5.6], (1,)))
    cuerpo.append(_p(f"Comprobante n.º {n['comprobante_ref']}; nota FIN-ING-03 n.º {n['numero']}.", E_PEQ))
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Compromiso firmado por (Cliente HORECA)"],
                      ["", n["recibido_por"]])
    cuerpo += _riesgos("No se aceptan cheques diferidos como medio de pago comprometido (POL-05, Art. 1).",
                       [_R10 + ["Preventivo", "Autorización"], _R11 + ["Preventivo", "Autorización"]])
    return _construir("FIN-ING-04", cuerpo, PIE)


# ==========================================================================
# FIN-ING-05 — Registro de Cuentas por Cobrar por Cliente
# ==========================================================================
def fin_ing_05(cartera: Sequence[Mapping[str, Any]], ctx: Contexto) -> bytes:
    filas = [[x["cliente"], _bs(x["monto"]), fecha_pantalla(x["vencimiento"]), _si(x["vencido"] or x["incumplida"]),
              fecha_pantalla(x["fecha_cobro"]) if x["fecha_cobro"] else ("Pendiente" if x["estado"] == "PENDIENTE"
                                                                         else x["estado"].capitalize()),
              str(x["incumplimientos"])] for x in cartera]
    cuerpo = _cuerpo("FIN-ING-05", "Registro de Cuentas por Cobrar por Cliente", "Unidad de Finanzas y Comercialización",
                     ctx, "Seguimiento diario del vencimiento del crédito otorgado a cada cliente HORECA, conforme al "
                          "Instructivo de Cobranza CI-FIN-15.")
    cuerpo.append(_tabla(["Cliente HORECA", "Monto (Bs)", "Fecha de Vencimiento", "¿Vencido? (Sí/No)",
                          "Fecha de Cobro Efectivo", "N° de Incumplimientos Acumulados"], filas,
                         [4, 2.6, 2.4, 2.2, 3, 3.2], (1,)))
    cuerpo += _firmas(["Actualizado por (Unidad de Finanzas)"])
    cuerpo += _riesgos("La columna «N° de Incumplimientos Acumulados» es el disparador directo del Paso 6 del "
                       "Instructivo CI-FIN-15 (suspensión al segundo incumplimiento consecutivo).",
                       [_R10 + ["Detección", "Verificación y Evaluación"], _R24 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-ING-05", cuerpo, PIE)


# ==========================================================================
# FIN-ING-07 — Parte Diario de Recaudación
# ==========================================================================
def fin_ing_07(parte: Mapping[str, Any], ctx: Contexto, boleta: str | None = None) -> bytes:
    filas = [["Ventas de carne de pollo al contado", _bs(parte["carne"])],
             ["Ventas de alimento balanceado excedente", _bs(parte["alimento"])],
             ["Ventas de gallinaza", _bs(parte["gallinaza"])],
             ["Cobros de cuentas por cobrar HORECA", _bs(parte["cobros"])],
             ["Total recaudado del día", _bs(parte["total"])],
             ["(De ello, ingresado directamente al banco: transferencia, QR/ACH, depósito)", _bs(parte["bancarizado"])],
             ["Monto depositado en cuenta corriente (efectivo del día)", _bs(parte["efectivo"])]]
    cuerpo = _cuerpo("FIN-ING-07", f"Parte Diario de Recaudación — {fecha_pantalla(parte['fecha'])}",
                     "Unidad de Finanzas y Comercialización", ctx,
                     "Consolidación diaria del efectivo recaudado por ventas al contado, con depósito en la cuenta "
                     "bancaria de la cooperativa (no se mantienen fondos en cuentas personales).")
    cuerpo.append(_tabla(["Concepto", "Monto (Bs)"], filas, [12.4, 5], (1,)))
    if boleta:
        cuerpo.append(_p(f"Boleta de depósito: {boleta}.", E_NORMAL))
    cuerpo += _firmas(["Consolidado por (Unidad de Finanzas)", "Depósito verificado por (Gerencia General)"])
    cuerpo += _riesgos("Control de protección física del efectivo diario e insumo del RINC (PR-FIN-10).",
                       [_R25 + ["Detección", "Verificación y Evaluación"], _R42 + ["Detección", "Protección Física"]])
    return _construir("FIN-ING-07", cuerpo, PIE)


# ==========================================================================
# FIN-ING-08 — Análisis de Margen por Canal
# ==========================================================================
def fin_ing_08(analisis: Mapping[str, Any], ctx: Contexto) -> bytes:
    costo = Decimal(str(analisis["costo_unitario"]))
    filas = [[NOMBRE_CANAL[l["canal"]], _bs(l["ingresos"]), _dec(costo, 2),
              pct_texto(l["margen"]) if l["margen"] is not None else "Sin ventas", _si(l["bajo_umbral"])]
             for l in analisis["lineas"]]
    cuerpo = _cuerpo("FIN-ING-08", f"Análisis de Margen por Canal — {analisis['mes']:02d}/{analisis['anio']}",
                     "Gerencia General", ctx,
                     "Cálculo mensual del margen bruto por canal de distribución, comparado contra la meta "
                     f"institucional de margen superior al 20 % (CI-FIN-06, Art. 7°). Costo de referencia: "
                     f"{analisis['ciclo_referencia']}.")
    cuerpo.append(_tabla(["Canal", "Ingresos del Mes (Bs)", "Costo Unitario Aplicado (Bs/kg)", "Margen Bruto (%)",
                          "¿Bajo el 20%? (Sí/No)"], filas, [3.8, 3.4, 3.6, 3.2, 3.4], (1, 2, 3)))
    if analisis.get("ajuste_requerido"):
        cuerpo.append(_p("DOS MESES CONSECUTIVOS BAJO EL MÍNIMO en: " + str(analisis.get("canales_ajuste")) +
                         ". Corresponde someter un ajuste del tarifario a la aprobación conjunta de la Gerencia y el "
                         "Consejo de Administración (CI-FIN-06 Arts. 7-8).", E_NORMAL))
    cuerpo += _firmas(["Elaborado por (Gerencia General)", "Insumo para (Consejo de Administración — ajuste de tarifario)"])
    cuerpo += _riesgos("Dos meses consecutivos con «Sí» en la última columna activan la obligación de ajuste.",
                       [_R26 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-ING-08", cuerpo, PIE)


# ==========================================================================
# CIR-ING-01 — Notificación de Suspensión de Línea de Crédito
# ==========================================================================
def cir_ing_01(s: Mapping[str, Any], ctx: Contexto) -> bytes:
    cuerpo = _encabezado("CIR-ING-01", "Notificación de Suspensión de Línea de Crédito", "Gerencia General", INGRESOS)
    cuerpo += [_tabla(["Campo", "Detalle"], [
        ["N° de Circular", f"CIR-ING-01 / {s['numero']} / {s['anio']}"],
        ["Fecha de emisión", fecha_pantalla(s["fecha"])],
        ["De (emisor)", "Gerencia General"],
        ["Para (destinatario)", f"Cliente HORECA: {s['cliente']}"],
        ["Asunto", "Suspensión de la línea de crédito por incumplimiento reiterado"]], [5, 12.4]), Spacer(1, 6),
        _p("Cuerpo del Aviso", E_SUB),
        _p("Por medio de la presente, la Gerencia General de la Cooperativa Agropecuaria Pollolandia R.L. comunica al "
           "cliente arriba identificado la suspensión automática de su línea de crédito, en aplicación del Artículo 2° "
           "de la Política de Créditos a Clientes (POL-05), por haber incurrido en incumplimientos de pago "
           "consecutivos, según el detalle siguiente:", E_NORMAL), Spacer(1, 4),
        _tabla(["Incumplimiento", "Fecha de Vencimiento", "Monto (Bs)", "Fecha de Regularización"],
               [[f"{i}°", fecha_pantalla(x["vencimiento"]), _bs(Decimal(x["monto"])),
                 fecha_pantalla(x["fecha_cobro"]) if x["fecha_cobro"] else "Pendiente"]
                for i, x in enumerate(s["detalle"], start=1)], [3.4, 4.6, 4.4, 5], (2,)),
        Spacer(1, 4),
        _p("Régimen aplicable a partir de esta fecha: contado, hasta que la Gerencia General autorice expresamente el "
           "restablecimiento de la línea de crédito. Referencia normativa: POL-05, Art. 2; Instructivo CI-FIN-15, "
           "Paso 6.", E_NORMAL)]
    if s.get("restablecida_por"):
        cuerpo.append(_p(f"Línea restablecida el {fecha_pantalla(s['restablecida_en'])}: {s['motivo_restablecimiento']}.",
                         E_PEQ))
    cuerpo += _firmas(["Gerencia General", "Recibido (Cliente HORECA)", "Copia informativa (Consejo de Vigilancia)"],
                      [s.get("firmada_por") or "", "", ""])
    cuerpo += _riesgos("La automaticidad de la suspensión la convierte en un control preventivo frente a un tercer "
                       "incumplimiento.", [_R24 + ["Preventivo", "Autorización"], _R10 + ["Preventivo", "Protección Física"]])
    return _construir("CIR-ING-01", cuerpo, PIE)


# ==========================================================================
# CI-FIN-13 Art. 8 — Verificación de cobertura del lote
# ==========================================================================
def cobertura_ci_fin_13(c: Mapping[str, Any], despacho: Mapping[str, Any], ctx: Contexto) -> bytes:
    filas = [["Aves del lote", str(c["aves"])],
             ["Pedidos HORECA confirmados con 3 días de antelación", str(c["horeca"])],
             ["Pedidos HORECA fuera de plazo", str(c["horeca_fuera_plazo"])],
             ["Pedidos indicativos (friales y mercado popular)", str(c["indicativo"])],
             ["Compradores indicativos distintos", f"{c['compradores_indicativos']} (mínimo {c['friales_min']})"],
             ["Total comprometido / cobertura", f"{c['total']} aves · {pct_texto(c['cobertura'])}"],
             ["Remanente sin destino", f"{c['remanente']} aves"],
             ["Canal de respaldo (CI-FIN-13 Art. 5)", "Activado por la GG" if c["respaldo_activado"] else
              ("Requerido" if c["respaldo_requerido"] else "No requerido")],
             ["Resultado", "COBERTURA COMPLETA" if c["cubre"] else "COBERTURA INCOMPLETA — informar a la GG"]]
    cuerpo = _encabezado("CI-FIN-13", f"Verificación de Cobertura del Lote {despacho['lote']}",
                         "Unidad de Finanzas y Comercialización", INGRESOS) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Faenado estimado el {fecha_pantalla(despacho['fecha_faenado'])}. La suma de volúmenes "
                  "comprometidos debe cubrir la totalidad del lote a más tardar el día anterior (Art. 8).", E_NORMAL),
               Spacer(1, 4), _tabla(["Concepto", "Valor"], filas, [10.4, 7])]
    cuerpo += _firmas(["Verificado por (Unidad de Finanzas)", "Informado a (Gerencia General)"])
    cuerpo += _riesgos("El Artículo 8° cierra los riesgos 11 y 25 al obligar a sumar los dos canales antes del faenado.",
                       [_R11 + ["Preventivo", "Autorización"], _R25 + ["Preventivo", "Autorización"]])
    return _construir("CI-FIN-13", cuerpo, PIE)


# ==========================================================================
# Tarifario Oficial Vigente (CI-FIN-06)
# ==========================================================================
def tarifario(t: Mapping[str, Any], ctx: Contexto) -> bytes:
    filas = [[PRODUCTOS[l["producto"]], NOMBRE_CANAL[l["canal"]], l["unidad"], _bs(l["precio"]),
              pct_texto(l["margen"]) if l["margen"] is not None else "—",
              ("Sí" if l["bajo_umbral"] else "No") if l["margen"] is not None else "—"] for l in t["lineas"]]
    estado = {"PROPUESTO": "Propuesto por la Gerencia General", "APROBADO": f"Aprobado — {t.get('acta_ca')}",
              "RECHAZADO": "Rechazado por el CA"}[t["estado"]]
    cuerpo = _encabezado("TARIFARIO", "Tarifario Oficial Vigente", "Gerencia General / Consejo de Administración",
                         INGRESOS) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Vigente desde el {fecha_pantalla(t['vigente_desde'])}. {estado}. Costo de referencia: "
                  f"{_bs(t['costo'])}/kg ({'último ciclo cerrado' if t['origen_costo'] == 'CICLO_CERRADO' else 'presupuesto D.4'}). "
                  f"Motivo: {t['motivo']}.", E_NORMAL), Spacer(1, 4),
               _tabla(["Producto", "Canal", "Unidad", "Precio (Bs)", "Margen bruto", "¿Bajo el 20%?"], filas,
                      [4.6, 3, 1.6, 2.6, 2.6, 3], (3, 4))]
    cuerpo += _firmas(["Propuesto por (Gerencia General)", "Aprobado por (Consejo de Administración)"],
                      [t["propuesto_por"], t.get("aprobado_ca_por") or ""])
    cuerpo += _riesgos("Ninguna venta podrá entregarse a un precio distinto del vigente sin la aprobación conjunta "
                       "(CI-FIN-06 Art. 9).", [_R26 + ["Preventivo", "Autorización"]])
    return _construir("TARIFARIO", cuerpo, PIE)
