"""Reportes PDF oficiales del módulo de contabilidad (ReportLab).

Base institucional unificada para todos los documentos contables:

* **Membrete en cada página:** «COOPERATIVA AGROPECUARIA POLLOLANDIA R.L.»,
  «Caranavi, La Paz – Bolivia | NIT | RAU», título y subtítulo del documento
  y filete verde.
* **Pie en cada página:** fecha y hora de emisión, usuario que emite, código
  del documento y «Página x de y».
* **Tablas uniformes:** bordes, encabezado resaltado, montos alineados a la
  derecha en bolivianos (``Bs 1.234,56``) y filas de totales destacadas.

Documentos:

* Comprobante contable (Apertura, Ingreso, Egreso, Traspaso, Ajuste y Cierre)
  con metadatos, detalle de cuentas, totales cuadrados, glosa y cuadro de
  firmas (Contabilidad, Gerencia General, Vº Bº Consejo de Vigilancia).
* Libro Diario del período con el desglose de cada comprobante y los totales
  de verificación.
* Libro Mayor analítico por cuenta (y sufijo) y en forma masiva.
* Balance de Comprobación de Sumas y Saldos de 4 y de 8 columnas.
* Estado de los períodos contables y Plan de Cuentas.

Las funciones reciben diccionarios de ``servicios/`` y devuelven bytes. No
importan ``streamlit`` ni acceden a la base de datos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Mapping, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from utils.formato import fecha_pantalla, formato_bs

VERDE = colors.HexColor("#1f5c43")
VERDE_CLARO = colors.HexColor("#e3eee7")
VERDE_TOTAL = colors.HexColor("#cfe3d6")
GRIS = colors.HexColor("#5d6a62")
GRIS_LINEA = colors.HexColor("#b9c4bd")
ROJO = colors.HexColor("#a12a2a")

_base = getSampleStyleSheet()["Normal"]
E_TEXTO = ParagraphStyle("pc_texto", parent=_base, fontName="Helvetica", fontSize=8.5, leading=11)
E_CELDA = ParagraphStyle("pc_celda", parent=E_TEXTO, fontSize=7.8, leading=9.6)
E_CELDA_DER = ParagraphStyle("pc_celda_der", parent=E_CELDA, alignment=TA_RIGHT)
E_ENCAB = ParagraphStyle("pc_encab", parent=E_CELDA, fontName="Helvetica-Bold", textColor=colors.white,
                         alignment=TA_CENTER)
E_NEGRITA = ParagraphStyle("pc_negrita", parent=E_CELDA, fontName="Helvetica-Bold")
E_NEGRITA_DER = ParagraphStyle("pc_negrita_der", parent=E_NEGRITA, alignment=TA_RIGHT)
E_SECCION = ParagraphStyle("pc_seccion", parent=E_TEXTO, fontName="Helvetica-Bold", fontSize=9.5, leading=12,
                           textColor=VERDE, spaceBefore=6, spaceAfter=3)
E_NOTA = ParagraphStyle("pc_nota", parent=E_TEXTO, fontSize=7.5, leading=9.5, textColor=GRIS)
E_ESTADO_OK = ParagraphStyle("pc_ok", parent=E_TEXTO, fontName="Helvetica-Bold", textColor=VERDE)
E_ESTADO_MAL = ParagraphStyle("pc_mal", parent=E_TEXTO, fontName="Helvetica-Bold", textColor=ROJO)

NOMBRE_TIPO = {"APERTURA": "Comprobante de Apertura", "INGRESO": "Comprobante de Ingreso",
               "EGRESO": "Comprobante de Egreso", "TRASPASO": "Comprobante de Traspaso",
               "AJUSTE": "Comprobante de Ajuste", "CIERRE": "Comprobante de Cierre"}
FIRMAS_COMPROBANTE = ("Elaborado por: Contabilidad", "Autorizado por: Gerencia General",
                      "Vº Bº Consejo de Vigilancia")


# ==========================================================================
# MEMBRETE, EMISIÓN Y CONSTRUCCIÓN DEL DOCUMENTO
# ==========================================================================
@dataclass(frozen=True)
class Membrete:
    """Identidad institucional impresa en cada página."""

    entidad: str = "COOPERATIVA AGROPECUARIA POLLOLANDIA R.L."
    ubicacion: str = "Caranavi, La Paz – Bolivia"
    nit: str = "Por registrar"
    rau: str = "En trámite"

    @property
    def linea_identidad(self) -> str:
        return f"{self.ubicacion}  |  NIT: {self.nit}  |  RAU: {self.rau}"


@dataclass(frozen=True)
class Emision:
    """Quién y cuándo emite el documento (pie de página)."""

    usuario: str = "Sistema"
    cargo: str = ""
    momento: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))

    @property
    def texto(self) -> str:
        quien = f"{self.usuario} ({self.cargo})" if self.cargo else self.usuario
        return f"Emitido el {self.momento:%d/%m/%Y %H:%M} por {quien}"


def _seguro(texto: Any) -> str:
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def p(texto: Any, estilo: ParagraphStyle = E_CELDA) -> Paragraph:
    """Párrafo con el texto escapado."""
    return Paragraph(_seguro("" if texto is None else texto), estilo)


def bs(valor: Any, vacio_si_cero: bool = False) -> str:
    """Monto en bolivianos ``Bs 1.234,56`` (vacío si es ``None``)."""
    if valor is None or valor == "":
        return ""
    d = Decimal(str(valor))
    if vacio_si_cero and d == 0:
        return ""
    return formato_bs(d)


class _Lienzo(rl_canvas.Canvas):
    """Dibuja membrete y pie en cada página, con «Página x de y» (dos pasadas)."""

    def __init__(self, *args: Any, membrete: Membrete, emision: Emision, titulo: str, subtitulo: str, codigo: str,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._estados: list[dict[str, Any]] = []
        self._m, self._e, self._titulo, self._subtitulo, self._codigo = membrete, emision, titulo, subtitulo, codigo

    def showPage(self) -> None:  # noqa: N802 - API de reportlab
        self._estados.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._estados)
        for estado in self._estados:
            self.__dict__.update(estado)
            self._marco(total)
            super().showPage()
        super().save()

    def _marco(self, total: int) -> None:
        ancho, alto = self._pagesize
        izquierda, derecha = 1.6 * cm, ancho - 1.6 * cm
        self.setFillColor(VERDE)
        self.setFont("Helvetica-Bold", 11.5)
        self.drawCentredString(ancho / 2, alto - 1.25 * cm, self._m.entidad)
        self.setFillColor(GRIS)
        self.setFont("Helvetica", 7.8)
        self.drawCentredString(ancho / 2, alto - 1.65 * cm, self._m.linea_identidad)
        self.setFillColor(colors.black)
        self.setFont("Helvetica-Bold", 10.5)
        self.drawCentredString(ancho / 2, alto - 2.2 * cm, self._titulo.upper())
        if self._subtitulo:
            self.setFont("Helvetica", 8.3)
            self.drawCentredString(ancho / 2, alto - 2.6 * cm, self._subtitulo)
        self.setStrokeColor(VERDE)
        self.setLineWidth(1.2)
        self.line(izquierda, alto - 2.85 * cm, derecha, alto - 2.85 * cm)
        self.setLineWidth(0.4)
        self.setStrokeColor(GRIS_LINEA)
        self.line(izquierda, 1.45 * cm, derecha, 1.45 * cm)
        self.setFillColor(GRIS)
        self.setFont("Helvetica", 7)
        self.drawString(izquierda, 1.05 * cm, self._e.texto)
        self.drawCentredString(ancho / 2, 0.7 * cm, f"{self._m.entidad}  ·  {self._codigo}")
        self.drawRightString(derecha, 1.05 * cm, f"Página {self._pageNumber} de {total}")


def construir(titulo: str, subtitulo: str, codigo: str, cuerpo: list[Any], membrete: Membrete | None = None,
              emision: Emision | None = None, horizontal: bool = False) -> bytes:
    """Arma el PDF con el membrete y el pie institucionales."""
    m, e = membrete or Membrete(), emision or Emision()
    tamano = landscape(letter) if horizontal else letter
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=tamano, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=3.2 * cm,
                            bottomMargin=1.9 * cm, title=f"{titulo} — {subtitulo}".strip(" —"), author=m.entidad,
                            subject=codigo, creator="ERP Pollolandia", pageCompression=0)
    doc.build(cuerpo or [Spacer(1, 1)], canvasmaker=lambda *a, **k: _Lienzo(
        *a, membrete=m, emision=e, titulo=titulo, subtitulo=subtitulo, codigo=codigo, **k))
    return buffer.getvalue()


def ancho_util(horizontal: bool = False) -> float:
    """Ancho del área de texto en cm."""
    return (27.94 if horizontal else 21.59) - 3.2


def tabla(encabezados: Sequence[str], filas: Sequence[Sequence[Any]], anchos: Sequence[float],
          montos: Sequence[int] = (), totales: Sequence[int] = (), subtotales: Sequence[int] = (),
          repetir: bool = True) -> Table:
    """Tabla institucional: bordes, encabezado verde con texto blanco, montos
    (índices de columna) alineados a la derecha y filas de totales destacadas
    (índices de fila de ``filas``, base 0)."""
    datos: list[list[Any]] = [[p(h, E_ENCAB) for h in encabezados]]
    for i, fila in enumerate(filas):
        negrita = i in totales or i in subtotales
        celdas = []
        for j, c in enumerate(fila):
            if isinstance(c, Paragraph):
                celdas.append(c)
            elif j in montos:
                celdas.append(p(c, E_NEGRITA_DER if negrita else E_CELDA_DER))
            else:
                celdas.append(p(c, E_NEGRITA if negrita else E_CELDA))
        datos.append(celdas)
    t = Table(datos, colWidths=[a * cm for a in anchos], repeatRows=1 if repetir else 0)
    estilo: list[Any] = [
        ("GRID", (0, 0), (-1, -1), 0.4, GRIS_LINEA), ("BOX", (0, 0), (-1, -1), 0.8, VERDE),
        ("BACKGROUND", (0, 0), (-1, 0), VERDE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in totales:
        estilo += [("BACKGROUND", (0, i + 1), (-1, i + 1), VERDE_TOTAL),
                   ("LINEABOVE", (0, i + 1), (-1, i + 1), 0.9, VERDE)]
    for i in subtotales:
        estilo.append(("BACKGROUND", (0, i + 1), (-1, i + 1), VERDE_CLARO))
    t.setStyle(TableStyle(estilo))
    return t


def ficha(pares: Sequence[tuple[str, Any]], columnas: int = 2, horizontal: bool = False) -> Table:
    """Cuadro de metadatos «etiqueta: valor» en ``columnas`` pares por fila."""
    total = ancho_util(horizontal)
    etiqueta, valor = total / columnas * 0.36, total / columnas * 0.64
    filas, actual = [], []
    for k, v in pares:
        actual += [p(k, E_NEGRITA), p(v)]
        if len(actual) == columnas * 2:
            filas.append(actual)
            actual = []
    if actual:
        actual += [p("")] * (columnas * 2 - len(actual))
        filas.append(actual)
    t = Table(filas, colWidths=[etiqueta * cm, valor * cm] * columnas)
    estilo = [("GRID", (0, 0), (-1, -1), 0.4, GRIS_LINEA), ("BOX", (0, 0), (-1, -1), 0.8, VERDE),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    estilo += [("BACKGROUND", (c, 0), (c, -1), VERDE_CLARO) for c in range(0, columnas * 2, 2)]
    t.setStyle(TableStyle(estilo))
    return t


def firmas(etiquetas: Sequence[str], nombres: Sequence[str] | None = None, horizontal: bool = False) -> KeepTogether:
    """Cuadro de firmas con línea, nombre (opcional) y cargo."""
    nombres = list(nombres or [""] * len(etiquetas))
    ancho = ancho_util(horizontal) / len(etiquetas)
    t = Table([[p(" ") for _ in etiquetas], [p("_" * 34, ParagraphStyle("l", parent=E_CELDA, alignment=TA_CENTER))
                                             for _ in etiquetas],
               [p(n or " ", ParagraphStyle("n", parent=E_CELDA, alignment=TA_CENTER)) for n in nombres],
               [p(e, ParagraphStyle("e", parent=E_NEGRITA, alignment=TA_CENTER)) for e in etiquetas]],
              colWidths=[ancho * cm] * len(etiquetas), rowHeights=[30, 10, None, None])
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    return KeepTogether([Spacer(1, 12), t])


def verificacion(texto: str, cuadra: bool) -> Paragraph:
    """Leyenda de control de cuadratura (verde si cuadra, rojo si no)."""
    return p(f"Control: {texto} — {'CUADRA' if cuadra else 'NO CUADRA'}",
             E_ESTADO_OK if cuadra else E_ESTADO_MAL)


def _periodo(desde: str | None, hasta: str | None) -> str:
    if desde and hasta:
        return f"Del {fecha_pantalla(desde)} al {fecha_pantalla(hasta)}"
    if hasta:
        return f"Al {fecha_pantalla(hasta)}"
    if desde:
        return f"Desde el {fecha_pantalla(desde)}"
    return "Todos los registros"


# ==========================================================================
# 1. COMPROBANTE CONTABLE
# ==========================================================================
def _cuerpo_comprobante(a: Mapping[str, Any], autorizacion: str | None = None) -> list[Any]:
    estado = a["estado"] + (" (reversión del asiento interno n.º " + str(a["reversa_de"]) + ")" if a.get("reversa_de")
                            else "")
    cuerpo: list[Any] = [ficha([
        ("Comprobante", a["comprobante"]), ("Fecha", fecha_pantalla(a["fecha"])),
        ("Tipo", NOMBRE_TIPO.get(a["tipo"], a["tipo"])), ("Origen", a["origen"]),
        ("Documento de respaldo", a.get("documento_ref") or "—"), ("Estado", estado),
        ("Registrado por", a.get("creado_por") or "—"), ("Registrado el", (a.get("creado_en") or "")[:16].replace("T", " ")),
        *([("Autorización", autorizacion)] if autorizacion else []),
    ]), Spacer(1, 6), p("Glosa general", E_SECCION), p(a["glosa"], E_TEXTO), Spacer(1, 6),
        p("Detalle de cuentas", E_SECCION)]
    filas = [[str(l["linea"]), l["cuenta"], l["nombre"], l.get("sufijo") or "", l.get("glosa") or "",
              bs(l["debe"], True), bs(l["haber"], True)] for l in a["lineas"]]
    filas.append(["", "", "TOTALES", "", "", bs(a["total_debe"]), bs(a["total_haber"])])
    cuerpo += [tabla(["N.º", "Cuenta", "Denominación", "Suf.", "Glosa de línea", "Debe", "Haber"], filas,
                     [0.9, 1.9, 5.0, 0.9, 4.2, 2.7, 2.7], montos=(5, 6), totales=(len(filas) - 1,)),
               Spacer(1, 4), verificacion(f"Partida doble: Debe {bs(a['total_debe'])} = Haber {bs(a['total_haber'])}",
                                          a["total_debe"] == a["total_haber"])]
    cuerpo.append(firmas(FIRMAS_COMPROBANTE, [a.get("creado_por") or "", "", ""]))
    return cuerpo


def comprobante(a: Mapping[str, Any], membrete: Membrete | None = None, emision: Emision | None = None,
                autorizacion: str | None = None) -> bytes:
    """Comprobante contable de un asiento (``servicios.contable.obtener_asiento``)."""
    return construir(NOMBRE_TIPO.get(a["tipo"], "Comprobante contable"), f"N.º {a['comprobante']} — "
                     f"{fecha_pantalla(a['fecha'])}", f"Comprobante {a['comprobante']}",
                     _cuerpo_comprobante(a, autorizacion), membrete, emision)


def comprobantes(asientos: Sequence[Mapping[str, Any]], desde: str | None, hasta: str | None,
                 membrete: Membrete | None = None, emision: Emision | None = None) -> bytes:
    """Todos los comprobantes del período, uno por página."""
    cuerpo: list[Any] = []
    for i, a in enumerate(asientos):
        if i:
            cuerpo.append(PageBreak())
        cuerpo.append(p(f"{NOMBRE_TIPO.get(a['tipo'], a['tipo'])} N.º {a['comprobante']}", E_SECCION))
        cuerpo += _cuerpo_comprobante(a)
    if not asientos:
        cuerpo.append(p("No hay comprobantes en el período.", E_TEXTO))
    return construir("Comprobantes contables", _periodo(desde, hasta), "Comprobantes", cuerpo, membrete, emision)


# ==========================================================================
# 2. LIBRO DIARIO
# ==========================================================================
def libro_diario(asientos: Sequence[Mapping[str, Any]], desde: str | None, hasta: str | None,
                 membrete: Membrete | None = None, emision: Emision | None = None,
                 incluye_reversados: bool = True) -> bytes:
    """Libro Diario: cada comprobante con su desglose y los totales del período."""
    w = ancho_util(True)
    anchos = [1.9, 2.9, 2.0, 6.4, 0.9, 5.6, 2.8, 2.8]
    anchos[3] += w - sum(anchos)
    filas: list[list[Any]] = []
    cabeceras, totales_asiento = [], []
    total_debe = total_haber = Decimal(0)
    for a in asientos:
        etiqueta = f"{a['comprobante']}  ·  {a['glosa']}" + ("  [REVERSADO]" if a["estado"] == "REVERSADO" else "")
        cabeceras.append(len(filas))
        filas.append([fecha_pantalla(a["fecha"]), a["comprobante"], "", p(etiqueta, E_NEGRITA), "",
                      f"Origen: {a['origen']}" + (f" · Doc.: {a['documento_ref']}" if a.get("documento_ref") else ""),
                      "", ""])
        for l in a["lineas"]:
            filas.append(["", "", l["cuenta"], l["nombre"], l.get("sufijo") or "", l.get("glosa") or "",
                          bs(l["debe"], True), bs(l["haber"], True)])
        totales_asiento.append(len(filas))
        filas.append(["", "", "", "Suma del comprobante", "", "", bs(a["total_debe"]), bs(a["total_haber"])])
        total_debe += Decimal(str(a["total_debe"]))
        total_haber += Decimal(str(a["total_haber"]))
    filas.append(["", "", "", f"TOTALES DEL PERÍODO ({len(asientos)} comprobantes)", "", "", bs(total_debe),
                  bs(total_haber)])
    cuerpo: list[Any] = [
        p("Asientos en orden cronológico; los reversados se muestran con su reversión (nunca se borran)."
          if incluye_reversados else "Solo asientos vigentes (sin reversados ni reversiones).", E_NOTA), Spacer(1, 4),
        tabla(["Fecha", "Comprobante", "Cuenta", "Denominación / Glosa general", "Suf.", "Detalle", "Debe", "Haber"],
              filas, anchos, montos=(6, 7), totales=(len(filas) - 1,), subtotales=tuple(cabeceras)),
        Spacer(1, 4),
        verificacion(f"Totales de verificación: Debe {bs(total_debe)} = Haber {bs(total_haber)}",
                     total_debe == total_haber),
        firmas(("Contador General (Unidad de Finanzas)", "Gerencia General", "Vº Bº Consejo de Vigilancia"),
               horizontal=True)]
    return construir("Libro Diario", _periodo(desde, hasta), "Libro Diario", cuerpo, membrete, emision,
                     horizontal=True)


# ==========================================================================
# 3. LIBRO MAYOR ANALÍTICO
# ==========================================================================
def _cuerpo_mayor(m: Mapping[str, Any], sufijo: str | None = None) -> list[Any]:
    titulo = f"{m['cuenta']} — {m['nombre']}" + (f" · Sufijo {sufijo}" if sufijo else "")
    naturaleza = str(m.get("naturaleza") or "DEUDORA")
    filas: list[list[Any]] = [["", "", "SALDO INICIAL", "", "", "", bs(m["saldo_inicial"])]]
    for mv in m["movimientos"]:
        glosa = mv["glosa"] + ("  [REVERSADO]" if mv.get("estado") == "REVERSADO" else "")
        filas.append([fecha_pantalla(mv["fecha"]), mv["comprobante"], glosa, mv.get("sufijo") or "",
                      bs(mv["debe"], True), bs(mv["haber"], True), bs(mv["saldo"])])
    filas.append(["", "", "TOTALES Y SALDO FINAL", "", bs(m["total_debe"]), bs(m["total_haber"]),
                  bs(m["saldo_final"])])
    signo = -1 if naturaleza == "ACREEDORA" else 1
    calculado = Decimal(str(m["saldo_inicial"])) + signo * (Decimal(str(m["total_debe"])) -
                                                           Decimal(str(m["total_haber"])))
    return [p(titulo, E_SECCION),
            p(f"Naturaleza {naturaleza.lower()}: saldo = {'Haber − Debe' if signo < 0 else 'Debe − Haber'}.", E_NOTA),
            tabla(["Fecha", "Comprobante", "Glosa", "Suf.", "Debe", "Haber", "Saldo"], filas,
                  [1.9, 2.3, 5.3, 0.9, 2.5, 2.5, 2.9], montos=(4, 5, 6), subtotales=(0,), totales=(len(filas) - 1,)),
            Spacer(1, 2),
            verificacion(f"Saldo inicial {bs(m['saldo_inicial'])} + movimientos = saldo final {bs(m['saldo_final'])}",
                         calculado == Decimal(str(m["saldo_final"])))]


def libro_mayor(m: Mapping[str, Any], desde: str | None, hasta: str | None, sufijo: str | None = None,
                membrete: Membrete | None = None, emision: Emision | None = None) -> bytes:
    """Mayor analítico de una cuenta (``servicios.contable.libro_mayor``)."""
    cuerpo = _cuerpo_mayor(m, sufijo) + [firmas(("Contador General (Unidad de Finanzas)", "Vº Bº Consejo de Vigilancia"))]
    return construir("Libro Mayor analítico", f"{m['cuenta']} {m['nombre']} — {_periodo(desde, hasta)}",
                     f"Mayor {m['cuenta']}", cuerpo, membrete, emision)


def libro_mayor_masivo(mayores: Sequence[Mapping[str, Any]], desde: str | None, hasta: str | None,
                       membrete: Membrete | None = None, emision: Emision | None = None) -> bytes:
    """Mayores de todas las cuentas (o cuenta-sufijo) con movimiento o saldo."""
    cuerpo: list[Any] = [p(f"{len(mayores)} cuenta(s) con saldo o movimiento en el período.", E_NOTA)]
    for i, m in enumerate(mayores):
        if i:
            cuerpo.append(Spacer(1, 10))
        cuerpo += _cuerpo_mayor(m, m.get("sufijo"))
    cuerpo.append(firmas(("Contador General (Unidad de Finanzas)", "Vº Bº Consejo de Vigilancia")))
    return construir("Libro Mayor analítico", f"Todas las cuentas — {_periodo(desde, hasta)}", "Libro Mayor",
                     cuerpo, membrete, emision)


# ==========================================================================
# 4. BALANCE DE COMPROBACIÓN DE SUMAS Y SALDOS
# ==========================================================================
NIVELES = {4: "Cuenta", 3: "Subgrupo", 2: "Clasificación", 1: "Grupo"}


def balance_comprobacion(b: Mapping[str, Any], membrete: Membrete | None = None,
                         emision: Emision | None = None) -> bytes:
    """Balance de 4 columnas: sumas Debe/Haber y saldos deudor/acreedor."""
    filas = [[f["codigo"], f["nombre"], bs(f["debe"], True), bs(f["haber"], True), bs(f["saldo_deudor"], True),
              bs(f["saldo_acreedor"], True)] for f in b["filas"]]
    t = b["totales"]
    filas.append(["", "TOTALES", bs(t["debe"]), bs(t["haber"]), bs(t["saldo_deudor"]), bs(t["saldo_acreedor"])])
    cuerpo = [p(f"Nivel de agregación: {NIVELES.get(int(b.get('nivel', 4)), 'Cuenta')}.", E_NOTA), Spacer(1, 3),
              tabla(["Código", "Denominación", "Sumas Debe", "Sumas Haber", "Saldo deudor", "Saldo acreedor"], filas,
                    [2.0, 5.6, 2.6, 2.6, 2.6, 2.6], montos=(2, 3, 4, 5), totales=(len(filas) - 1,)),
              Spacer(1, 4),
              verificacion(f"Sumas: Debe {bs(t['debe'])} = Haber {bs(t['haber'])}", bool(b["cuadra_sumas"])),
              verificacion(f"Saldos: deudores {bs(t['saldo_deudor'])} = acreedores {bs(t['saldo_acreedor'])}",
                           bool(b["cuadra_saldos"])),
              firmas(("Contador General (Unidad de Finanzas)", "Gerencia General", "Vº Bº Consejo de Vigilancia"))]
    return construir("Balance de Comprobación de Sumas y Saldos", _periodo(b.get("desde"), b.get("hasta")),
                     "Balance de Comprobación (4 columnas)", cuerpo, membrete, emision)


def balance_ocho_columnas(b: Mapping[str, Any], membrete: Membrete | None = None,
                          emision: Emision | None = None) -> bytes:
    """Balance de 8 columnas (``servicios.contable.balance_ocho_columnas``):
    sumas, saldos, inventario (balance) y resultados, con el resultado del
    período que iguala ambos pares."""
    w = ancho_util(True)
    anchos = [1.7, 4.4] + [(w - 6.1) / 8] * 8
    filas = [[f["codigo"], f["nombre"]] + [bs(f[c], True) for c in COLUMNAS_8] for f in b["filas"]]
    t = b["totales"]
    filas.append(["", "SUMAS"] + [bs(t[c]) for c in COLUMNAS_8])
    r = b["resultado"]
    etiqueta = "EXCEDENTE DEL PERÍODO" if r["monto"] >= 0 else "DÉFICIT DEL PERÍODO"
    filas.append(["", etiqueta] + [bs(r.get(c), True) for c in COLUMNAS_8])
    f = b["finales"]
    filas.append(["", "TOTALES IGUALADOS"] + [bs(f[c]) for c in COLUMNAS_8])
    n = len(filas)
    cuerpo = [p("Inventario (balance): grupos 1 a 4. Resultados: grupos 5 a 8. Las cuentas de orden (grupo 9) "
                "figuran solo en sumas y saldos.", E_NOTA), Spacer(1, 3),
              tabla(["Código", "Denominación", "Sumas Debe", "Sumas Haber", "Saldo deudor", "Saldo acreedor",
                     "Activo", "Pasivo y Patrimonio", "Pérdidas", "Ganancias"], filas, anchos,
                    montos=tuple(range(2, 10)), totales=(n - 3, n - 1), subtotales=(n - 2,)),
              Spacer(1, 4),
              verificacion(f"Sumas {bs(t['debe'])} = {bs(t['haber'])}", bool(b["cuadra_sumas"])),
              verificacion(f"Saldos {bs(t['saldo_deudor'])} = {bs(t['saldo_acreedor'])}", bool(b["cuadra_saldos"])),
              verificacion(f"El resultado del inventario y el de resultados coinciden ({bs(r['monto'])})",
                           bool(b["cuadra_resultado"])),
              firmas(("Contador General (Unidad de Finanzas)", "Gerencia General", "Vº Bº Consejo de Vigilancia"),
                     horizontal=True)]
    return construir("Balance de Comprobación de 8 columnas", _periodo(b.get("desde"), b.get("hasta")),
                     "Balance de Comprobación (8 columnas)", cuerpo, membrete, emision, horizontal=True)


COLUMNAS_8 = ("debe", "haber", "saldo_deudor", "saldo_acreedor", "activo", "pasivo_patrimonio", "perdidas",
              "ganancias")


# ==========================================================================
# 5. PERÍODOS CONTABLES Y PLAN DE CUENTAS
# ==========================================================================
def periodos(ejercicio: int, filas: Sequence[Mapping[str, Any]], membrete: Membrete | None = None,
             emision: Emision | None = None) -> bytes:
    """Estado de los períodos contables del ejercicio."""
    datos = [[str(f["numero"]), f["nombre"], f["estado"], str(f["asientos"]), f.get("cerrado_por") or "",
              (f.get("cerrado_en") or "")[:16].replace("T", " ")] for f in filas]
    cerrados = sum(1 for f in filas if f["estado"] == "CERRADO")
    cuerpo = [tabla(["N.º", "Período", "Estado", "Asientos", "Cerrado por", "Cerrado el"], datos,
                    [1.0, 6.6, 2.0, 1.8, 3.0, 3.0], montos=(3,)),
              Spacer(1, 4), p(f"{cerrados} de {len(filas)} períodos cerrados. Los períodos se cierran en orden con la "
                              "FIN-INF-01 sin pendientes y no se reabren.", E_NOTA),
              firmas(("Contador General (Unidad de Finanzas)", "Gerencia General"))]
    return construir("Estado de los períodos contables", f"Ejercicio {ejercicio} (1-jul-{ejercicio} a "
                     f"30-jun-{ejercicio + 1})", f"Períodos {ejercicio}", cuerpo, membrete, emision)


def plan_cuentas(cuentas: Sequence[Mapping[str, Any]], membrete: Membrete | None = None,
                 emision: Emision | None = None) -> bytes:
    """Plan de Cuentas v3 con nivel, naturaleza y vigencia."""
    datos, grupos = [], []
    for c in cuentas:
        if int(c["nivel"]) <= 2:
            grupos.append(len(datos))
        sangria = "    " * (int(c["nivel"]) - 1)
        datos.append([c["codigo"], p(sangria + c["nombre"], E_NEGRITA if int(c["nivel"]) <= 2 else E_CELDA),
                      str(c["nivel"]), (c.get("naturaleza") or "").capitalize(),
                      "Movimiento" if c.get("es_movimiento") else "Título",
                      "Desde 2027 (*)" if c.get("uso_desde_2027") else ""])
    cuerpo = [tabla(["Código", "Denominación", "Nivel", "Naturaleza", "Tipo", "Vigencia"], datos,
                    [2.0, 8.3, 1.1, 2.0, 2.0, 2.0], subtotales=tuple(grupos))]
    return construir("Plan de Cuentas", "Plan de Cuentas v3", "Plan de Cuentas", cuerpo, membrete, emision)
