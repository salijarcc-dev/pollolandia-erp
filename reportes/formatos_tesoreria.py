"""Formatos PDF del Ciclo 1 — Tesorería (Fase 3), con reportlab.

Replican la estructura de los formatos del Anexo 5 (Ciclo de Tesorería) y del
Anexo 9 (FIN-INF-02): encabezado con código y versión, datos de contexto,
cuerpo de datos, bloque de validación y firmas, y pie con número de página.

Formatos: FIN-TES-05, FIN-TES-06, FIN-TES-07, FIN-TES-08, INF-CV-03,
FIN-INF-02 y Comprobante de Reposición de Caja Chica (CI-FIN-01).

Las funciones reciben los diccionarios que devuelve ``servicios.tesoreria`` y
devuelven los bytes del PDF. No importan ``streamlit``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import config
from utils.formato import fecha_pantalla, formato_bs

VERSION_FORMATO = "2026-v.1"
VERDE = colors.HexColor("#1f5c43")
VERDE_CLARO = colors.HexColor("#e3eee7")
GRIS = colors.HexColor("#5d6a62")

_estilos = getSampleStyleSheet()
E_NORMAL = ParagraphStyle("pl_normal", parent=_estilos["Normal"], fontName="Helvetica", fontSize=9, leading=12)
E_PEQ = ParagraphStyle("pl_peq", parent=E_NORMAL, fontSize=8, leading=10, textColor=GRIS)
E_TITULO = ParagraphStyle("pl_titulo", parent=E_NORMAL, fontName="Helvetica-Bold", fontSize=12, leading=15,
                          alignment=TA_CENTER)
E_SUB = ParagraphStyle("pl_sub", parent=E_NORMAL, fontName="Helvetica-Bold", fontSize=10, leading=13,
                       textColor=VERDE, spaceBefore=6, spaceAfter=4)
E_CELDA = ParagraphStyle("pl_celda", parent=E_NORMAL, fontSize=8.5, leading=11)


@dataclass(frozen=True)
class Contexto:
    """Datos de contexto que encabezan cada formato."""

    fecha: date
    elaborado_por: str
    cargo: str
    ciclo: str = ""


def _bs(valor: Any) -> str:
    return "" if valor is None or valor == "" else formato_bs(valor)


def _pct(valor: Any) -> str:
    return "" if valor is None else f"{Decimal(valor):.2f} %".replace(".", ",")


def _p(texto: Any, estilo: ParagraphStyle = E_CELDA) -> Paragraph:
    seguro = str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(seguro, estilo)


class _LienzoNumerado(rl_canvas.Canvas):
    """Lienzo que escribe "Página x de y" y la fecha de emisión al final (dos pasadas)."""

    def __init__(self, *args: Any, pie: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._paginas: list[dict[str, Any]] = []
        self._pie = pie
        self._emitido = datetime.now().strftime("%d/%m/%Y %H:%M")

    def showPage(self) -> None:  # noqa: N802 - API de reportlab
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(GRIS)
            self.drawCentredString(letter[0] / 2, 1.2 * cm, f"{self._pie}  ·  Página {self._pageNumber} de {total}")
            self.drawCentredString(letter[0] / 2, 0.85 * cm, f"Emitido el {self._emitido}")
            super().showPage()
        super().save()


def _encabezado(codigo: str, titulo: str, area: str, ciclo_transaccional: str) -> list[Any]:
    tabla = Table(
        [[_p("COOPERATIVA AGROPECUARIA POLLOLANDIA R.L.", E_TITULO)],
         [_p(titulo.upper(), E_TITULO)],
         [Table([[_p(f"Código: {codigo}"), _p(f"Versión: {VERSION_FORMATO}")],
                 [_p(f"Área / Proceso: {area}"), _p(f"Ciclo Transaccional: {ciclo_transaccional}")]],
                colWidths=[8.6 * cm, 8.6 * cm])]],
        colWidths=[17.4 * cm])
    tabla.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, VERDE),
        ("BACKGROUND", (0, 0), (-1, 1), VERDE_CLARO),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, VERDE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [tabla, Spacer(1, 6)]


def _contexto(ctx: Contexto) -> list[Any]:
    tabla = Table([
        [_p("Fecha (D/M/A):"), _p(fecha_pantalla(ctx.fecha)),
         _p("N° de Ciclo:"), _p(ctx.ciclo or "—")],
        [_p("Elaborado por:"), _p(ctx.elaborado_por), _p("Cargo:"), _p(ctx.cargo)],
    ], colWidths=[3.2 * cm, 5.5 * cm, 2.6 * cm, 6.1 * cm])
    tabla.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("BACKGROUND", (0, 0), (0, -1), VERDE_CLARO), ("BACKGROUND", (2, 0), (2, -1), VERDE_CLARO),
    ]))
    return [_p("Datos de Contexto", E_SUB), tabla, Spacer(1, 4)]


def _tabla(encabezados: Sequence[str], filas: Sequence[Sequence[Any]], anchos: Sequence[float],
           alinear_derecha: Sequence[int] = ()) -> Table:
    datos = [[_p(h) for h in encabezados]] + [[c if isinstance(c, Paragraph) else _p(c) for c in f] for f in filas]
    tabla = Table(datos, colWidths=[a * cm for a in anchos], repeatRows=1)
    estilo = [("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey), ("BACKGROUND", (0, 0), (-1, 0), VERDE_CLARO),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    estilo += [("ALIGN", (c, 1), (c, -1), "RIGHT") for c in alinear_derecha]
    tabla.setStyle(TableStyle(estilo))
    return tabla


def _firmas(etiquetas: Sequence[str], nombres: Sequence[str] | None = None) -> list[Any]:
    nombres = list(nombres or [""] * len(etiquetas))
    ancho = 17.4 / len(etiquetas)
    tabla = Table([[_p(n or " ", E_PEQ) for n in nombres],
                   [_p("_" * 32) for _ in etiquetas],
                   [_p(e) for e in etiquetas]], colWidths=[ancho * cm] * len(etiquetas), rowHeights=[28, 12, None])
    tabla.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, 0), "BOTTOM")]))
    return [Spacer(1, 10), _p("Bloque de Validación y Firmas", E_SUB), tabla]


def _construir(codigo: str, cuerpo: list[Any], ciclo: str = "Ciclo 1 — Tesorería") -> bytes:
    buffer = BytesIO()
    pie = f"{config.NOMBRE_ENTIDAD}  ·  {ciclo}  ·  {codigo}"
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=2.1 * cm, rightMargin=2.1 * cm, topMargin=1.6 * cm,
                            bottomMargin=2 * cm, title=codigo, author=config.NOMBRE_ENTIDAD, pageCompression=0,
                            invariant=1)
    doc.build(cuerpo, canvasmaker=lambda *a, **k: _LienzoNumerado(*a, pie=pie, **k))
    return buffer.getvalue()


def _si_no(valor: bool) -> str:
    return "Sí [X]   No [ ]" if valor else "Sí [ ]   No [X]"


# ==========================================================================
# FIN-TES-05 — Flujo de Caja Proyectado
# ==========================================================================
def fin_tes_05(flujo: dict[str, Any], ctx: Contexto, aprobado_por: str = "") -> bytes:
    """Flujo de caja proyectado a dos ciclos (resultado de ``evaluar_flujo``)."""
    a, s = flujo["ciclos"]["ACTUAL"], flujo["ciclos"]["SIGUIENTE"]
    filas = [
        ["(+) Ingresos esperados por ventas", _bs(a["INGRESO_VENTAS"]), _bs(s["INGRESO_VENTAS"])],
        ["(–) Egresos operativos programados", _bs(a["EGRESO_OPERATIVO"]), _bs(s["EGRESO_OPERATIVO"])],
        ["(–) Inversiones CAPEX del período", _bs(a["CAPEX"]), _bs(s["CAPEX"])],
        ["(–) Pagos de excedentes comprometidos", _bs(a["EXCEDENTES"]), _bs(s["EXCEDENTES"])],
        ["(=) Saldo final proyectado", _bs(a["saldo_final"]), _bs(s["saldo_final"])],
        ["Saldo Mínimo de Efectivo (SME) del ejercicio", _bs(a["sme"]), _bs(s["sme"])],
        ["Cumplimiento del SME (Saldo / SME × 100%)", _pct(a["cumplimiento"]), _pct(s["cumplimiento"])],
    ]
    cuerpo = _encabezado("FIN-TES-05", "Flujo de Caja Proyectado", "Unidad de Finanzas y Comercialización",
                         "Ciclo 1 — Tesorería (Financiamiento)") + _contexto(ctx)
    cuerpo += [
        _p("Cuerpo de Datos", E_SUB),
        _p(f"Elaborado al inicio del ciclo ({fecha_pantalla(flujo['inicio_ciclo'])}), con horizonte de dos ciclos: "
           f"ciclo actual al {fecha_pantalla(a['fin'])} y ciclo siguiente al {fecha_pantalla(s['fin'])} "
           "(PR-FIN-02, Art. 4 de la POL-04). Saldo inicial: " + _bs(flujo["saldo_inicial"]) + ".", E_NORMAL),
        Spacer(1, 4),
        _tabla(["Concepto", "Ciclo actual (Bs)", "Ciclo siguiente (Bs)"], filas, [9.4, 4, 4], (1, 2)),
        Spacer(1, 6),
        _p(f"Saldo mínimo diario proyectado: {_bs(flujo['saldo_minimo'])} el {fecha_pantalla(flujo['fecha_minimo'])} "
           f"(SME {_bs(flujo['sme_en_minimo'])}). Días del horizonte bajo el SME: {flujo['dias_bajo_sme']}. "
           f"Riesgo de caer bajo el SME: {_si_no(flujo['riesgo_bajo_sme'])}.", E_NORMAL),
        _p("Margen de seguridad al cierre (POL-04 Art. 1, 0,5 ciclos): ciclo actual "
           f"{_si_no(a['margen_seguridad_cumplido'])}; ciclo siguiente {_si_no(s['margen_seguridad_cumplido'])}.",
           E_NORMAL),
    ]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Aprobado por (Gerencia General)"],
                      [ctx.elaborado_por, aprobado_por])
    return _construir("FIN-TES-05", cuerpo)


# ==========================================================================
# FIN-TES-06 — Reporte Diario de Saldo Bancario
# ==========================================================================
def fin_tes_06(reportes: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    """Monitoreo diario del saldo bancario frente al SME."""
    if not reportes:
        raise ValueError("El reporte diario necesita al menos un día.")
    filas = [[fecha_pantalla(r["fecha"]), _bs(r["saldo"]), _bs(r["sme"]), _pct(r["cumplimiento"]),
              f"{'Sí' if r['requiere_alerta'] else 'No'} ({r['semaforo'].capitalize()})"] for r in reportes]
    cuerpo = _encabezado("FIN-TES-06", "Reporte Diario de Saldo Bancario", "Gerencia General",
                         "Ciclo 1 — Tesorería (Financiamiento)") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Monitoreo diario del saldo bancario disponible frente al SME vigente del ejercicio "
                  "(PR-FIN-02, Paso 4).", E_NORMAL), Spacer(1, 4),
               _tabla(["Fecha", "Saldo Bancario (Bs)", "SME Vigente (Bs)", "% de Cumplimiento",
                       "¿Requiere Alerta? (Sí/No)"], filas, [2.6, 3.8, 3.6, 3.2, 4.2], (1, 2, 3)),
               Spacer(1, 4), _p("Umbral: Malo < 100%  ·  Normal 100% – 150%  ·  Bueno > 150%.", E_PEQ)]
    cuerpo += _firmas(["Monitoreado por (Gerencia General)"], [ctx.elaborado_por])
    return _construir("FIN-TES-06", cuerpo)


# ==========================================================================
# FIN-TES-07 — Alerta de Riesgo de Liquidez
# ==========================================================================
def fin_tes_07(alerta: dict[str, Any], ctx: Contexto) -> bytes:
    """Alerta por desvío mayor al umbral o riesgo de caer bajo el SME."""
    cuerpo = _encabezado("FIN-TES-07", "Alerta de Riesgo de Liquidez", "Unidad de Finanzas y Comercialización",
                         "Ciclo 1 — Tesorería (Financiamiento)") + _contexto(ctx)
    punto = fecha_pantalla(alerta["punto_horizonte"]) if alerta.get("punto_horizonte") else "—"
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Emitida cuando el flujo proyectado se desvía en más del 10% respecto de lo estimado, o cuando se "
                  "proyecta un riesgo de caer bajo el SME en algún punto del horizonte (PR-FIN-02, Paso 5).", E_NORMAL),
               Spacer(1, 4),
               _tabla(["Descripción de la desviación detectada", "% de Desviación", "Punto del Horizonte Afectado",
                       "Riesgo de Caer bajo el SME (Sí/No)"],
                      [[alerta["descripcion"], _pct(alerta.get("porcentaje_desvio")), punto,
                        "Sí" if alerta["riesgo_bajo_sme"] else "No"]], [7.4, 2.8, 3.4, 3.8], (1,)),
               Spacer(1, 6), _p("Medida correctiva sugerida (ver detalle en FIN-TES-08)", E_SUB),
               _p(alerta.get("medida_sugerida") or "—", E_NORMAL)]
    cuerpo += _firmas(["Emitida por (Unidad de Finanzas)", "Recibida por (Gerencia General)"],
                      [alerta.get("emitida_por", ""), alerta.get("recibida_por") or ""])
    return _construir("FIN-TES-07", cuerpo)


# ==========================================================================
# FIN-TES-08 — Registro de Medida Correctiva Adoptada
# ==========================================================================
def fin_tes_08(alerta: dict[str, Any], ctx: Contexto, etiquetas: dict[str, str]) -> bytes:
    """Medida adoptada por la GG ante la alerta (POL-04 Art. 4)."""
    if not alerta.get("medida_id"):
        raise ValueError("La alerta todavía no tiene medida correctiva registrada.")
    filas = [[texto, "[X]" if alerta.get(clave) else "[ ]"] for clave, texto in etiquetas.items()]
    cuerpo = _encabezado("FIN-TES-08", "Registro de Medida Correctiva Adoptada", "Gerencia General",
                         "Ciclo 1 — Tesorería (Financiamiento)") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Registra la medida correctiva activada por la Gerencia General ante la Alerta de Riesgo de Liquidez "
                  f"n.º {alerta['id']} (FIN-TES-07), conforme a POL-04 Art. 4.", E_NORMAL), Spacer(1, 4),
               _tabla(["Medida Correctiva Adoptada", "Marque"], filas, [14.4, 3]),
               Spacer(1, 6),
               _p(f"Fecha de aplicación: {fecha_pantalla(alerta['fecha_aplicacion'])}", E_NORMAL),
               _p(f"Resultado obtenido: {alerta.get('resultado') or '—'}", E_NORMAL)]
    cuerpo += _firmas(["Adoptada por (Gerencia General)", "Fiscalizada por (Consejo de Vigilancia)"],
                      [alerta.get("adoptada_por") or "", alerta.get("fiscalizada_por") or ""])
    return _construir("FIN-TES-08", cuerpo)


# ==========================================================================
# INF-CV-03 — Acta de Arqueo de Caja
# ==========================================================================
def inf_cv_03(arqueo: dict[str, Any], ctx: Contexto, tope: Decimal) -> bytes:
    """Acta del arqueo sorpresivo del CV (resultado de ``registrar_arqueo``)."""
    filas = [["Efectivo físico contado", _bs(arqueo["efectivo_contado"])],
             ["Comprobantes de gasto no repuestos", _bs(arqueo["comprobantes_no_repuestos"])],
             ["Total según arqueo físico", _bs(arqueo["total_fisico"])],
             ["Saldo según registro contable", _bs(arqueo["saldo_contable"])],
             ["Diferencia (física – contable)", _bs(arqueo["diferencia"])]]
    cumplido = bool(arqueo["limite_diario_cumplido"])
    cuerpo = _encabezado("INF-CV-03", "Acta de Arqueo de Caja", "Consejo de Vigilancia",
                         "Ciclo 1 — Tesorería (Financiamiento)") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Arqueo sorpresivo, al menos dos veces al año, del Fondo de Caja de {_bs(tope)} (FICHA-FIN-01 / "
                  "CI-FIN-01, Paso 7).", E_NORMAL), Spacer(1, 4),
               _tabla(["Concepto", "Monto (Bs)"], filas, [12.4, 5], (1,)), Spacer(1, 6),
               _p(f"¿Se cumplió el límite diario de {_bs(tope)} en custodia? {_si_no(cumplido)}", E_NORMAL),
               _p(f"Observaciones: {arqueo.get('observaciones') or '—'}", E_NORMAL)]
    if not cumplido and arqueo.get("dias_con_exceso"):
        dias = ", ".join(f"{fecha_pantalla(d['fecha'])} ({_bs(d['excedente'])})" for d in arqueo["dias_con_exceso"])
        cuerpo.append(_p(f"Días con exceso no depositado: {dias}.", E_NORMAL))
    cuerpo += _firmas(["Arqueado por (Consejo de Vigilancia)", "Presente (Custodio de Caja)"],
                      [ctx.elaborado_por, arqueo.get("custodio_presente", "")])
    return _construir("INF-CV-03", cuerpo)


# ==========================================================================
# FIN-INF-02 — Conciliación Bancaria del Mes
# ==========================================================================
def fin_inf_02(conciliacion: dict[str, Any], ctx: Contexto) -> bytes:
    """Conciliación bancaria (resultado de ``obtener_conciliacion``)."""
    suma: dict[str, Decimal] = {}
    for p in conciliacion["partidas"]:
        suma[p["tipo"]] = suma.get(p["tipo"], Decimal("0")) + Decimal(p["monto"])
    otros_libros = suma.get("ABONO_NO_REGISTRADO", Decimal(0)) + suma.get("ERROR_LIBROS_MAS", Decimal(0)) \
        - suma.get("ERROR_LIBROS_MENOS", Decimal(0))
    filas = [
        ["Saldo según libros al cierre del mes", _bs(conciliacion["saldo_libros"])],
        ["(–) Cargos bancarios no registrados", _bs(suma.get("CARGO_NO_REGISTRADO", Decimal(0)))],
        ["(+/–) Otros ajustes de libros (abonos no registrados y correcciones)", _bs(otros_libros)],
        ["Saldo conciliado (según libros, ajustado)", _bs(conciliacion["saldo_conciliado_libros"])],
        ["Saldo según extracto bancario", _bs(conciliacion["saldo_extracto"])],
        ["(+) Depósitos en tránsito", _bs(suma.get("DEPOSITO_TRANSITO", Decimal(0)))],
        ["(–) Cheques en circulación", _bs(suma.get("CHEQUE_CIRCULACION", Decimal(0)))],
        ["Saldo conciliado (según banco, ajustado)", _bs(conciliacion["saldo_conciliado_banco"])],
    ]
    cuadra = int(conciliacion["diferencia_cent"]) == 0
    detalle = [[fecha_pantalla(p["fecha"]), p["tipo"].replace("_", " ").capitalize(), p["descripcion"],
                _bs(p["monto"])] for p in conciliacion["partidas"]]
    cuerpo = _encabezado("FIN-INF-02", "Conciliación Bancaria del Mes", "Unidad de Finanzas y Comercialización",
                         "Ciclo 5 — Información Gerencial / FICHA-FIN-07") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Cuenta {conciliacion['cuenta_codigo']} · mes {conciliacion['mes']:02d}/{conciliacion['anio']} · "
                  f"plazo hasta el {fecha_pantalla(conciliacion['fecha_limite'])} (Guía CI-FIN-07: primeros días "
                  "hábiles del mes siguiente).", E_NORMAL), Spacer(1, 4),
               _tabla(["Concepto", "Monto (Bs)"], filas, [12.4, 5], (1,)), Spacer(1, 6),
               _p(f"¿Los dos saldos conciliados coinciden? {_si_no(cuadra)}"
                  + ("" if cuadra else f" — diferencia no explicada {_bs(conciliacion['diferencia'])}: notificar de "
                                       "inmediato."), E_NORMAL)]
    if detalle:
        cuerpo += [_p("Detalle de partidas", E_SUB),
                   _tabla(["Fecha", "Tipo", "Descripción", "Monto (Bs)"], detalle, [2.4, 4.2, 7.6, 3.2], (3,))]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Revisado y firmado por (Gerencia General)"],
                      [conciliacion.get("elaborado_por", ""), conciliacion.get("firmado_por") or ""])
    return _construir("FIN-INF-02", cuerpo, "Ciclo 5 — Información Gerencial")


# ==========================================================================
# Comprobante de Reposición de Caja Chica (CI-FIN-01, anexo)
# ==========================================================================
def comprobante_reposicion(reposicion: dict[str, Any], gastos: Sequence[dict[str, Any]], ctx: Contexto,
                           custodio: str) -> bytes:
    """Planilla de reposición con los comprobantes que justifican el consumo del fondo."""
    filas = [[g["comprobante"], fecha_pantalla(g["fecha"]), g.get("beneficiario") or "", g["descripcion"],
              g["cuenta_contrapartida"], _bs(g["monto"])] for g in gastos]
    filas.append(["", "", "", "TOTAL A REPONER", "", _bs(reposicion["total"])])
    cuerpo = _encabezado("CI-FIN-01 · Anexo", "Comprobante de Reposición de Caja Chica",
                         "Unidad de Finanzas y Comercialización", "Ciclo 1 — Tesorería (Financiamiento)") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"Solicitud n.º {reposicion['id']} · estado {reposicion['estado']} · custodio {custodio}. La "
                  "Gerencia General revisa los comprobantes antes de autorizar la reposición (CI-FIN-01, Paso 5).",
                  E_NORMAL), Spacer(1, 4),
               _tabla(["Comprobante", "Fecha", "Beneficiario", "Concepto", "Cuenta", "Monto (Bs)"], filas,
                      [2.4, 2.2, 3.2, 4.8, 2.2, 2.6], (5,))]
    cuerpo += _firmas(["Solicitado por (Unidad de Finanzas)", "Validado por (Gerencia General)",
                       "Recibido por (Custodio de Caja)"],
                      [reposicion.get("solicitado_por", ""), reposicion.get("validado_por") or "", custodio])
    return _construir("CI-FIN-01", cuerpo)
