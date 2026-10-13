"""Utilitarios de presentación compartidos por las páginas Streamlit.

Las páginas solo llaman a ``servicios/``; aquí se concentran la conexión por
petición, la presentación de errores y el formato de tablas (Bs 1.234,56).
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd
import streamlit as st

from db.conexion import conexion
from servicios.excepciones import ErrorPollolandia
from utils.formato import fecha_pantalla, formato_bs

ESTILO = """
<style>
  [data-testid="stSidebar"] h1 { font-size: 1.25rem; }
  .pl-chip { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.8rem; font-weight:600; }
  .pl-ok { background:#e1f2e7; color:#1f7a4a; } .pl-pend { background:#fbefcf; color:#946200; }
  .pl-mal { background:#f8e1de; color:#a3322a; } .pl-info { background:#e2ecf7; color:#2d5a8a; }
</style>
"""

COLOR_ESTADO = {
    "AUTORIZADA": "pl-ok", "UTILIZADA": "pl-info", "PENDIENTE": "pl-pend", "RECHAZADA": "pl-mal",
    "VIGENTE": "pl-ok", "REVERSADO": "pl-mal", "ABIERTO": "pl-ok", "CERRADO": "pl-info",
    "MALO": "pl-mal", "NORMAL": "pl-pend", "BUENO": "pl-ok", "SUSPENDIDO": "pl-mal", "PAGADO": "pl-info",
    "ANULADO": "pl-info", "ABIERTA": "pl-mal", "ATENDIDA": "pl-ok", "PREPARADA": "pl-pend", "FIRMADA": "pl-ok",
    "SUSTITUIDA": "pl-info", "SOLICITADA": "pl-pend", "VALIDADA": "pl-ok", "EJECUTADA": "pl-info",
    "BORRADOR": "pl-pend", "APROBADO": "pl-ok",
    "VERDE": "pl-ok", "AMARILLO": "pl-pend", "ROJO": "pl-mal", "EN_CRIANZA": "pl-ok", "FAENADO": "pl-info",
    "CONCLUIDO": "pl-info", "CONTABILIZADA": "pl-info", "PENDIENTE_AUTORIZACION": "pl-pend",
    "PRESENTADO": "pl-pend", "RATIFICADO": "pl-info", "REGISTRADA": "pl-ok", "ANULADA": "pl-info",
    "PENDIENTE_CV": "pl-pend", "OBSERVADA": "pl-mal", "EN_BAJA": "pl-pend", "BAJA": "pl-info", "RETIRADA": "pl-info",
}


@contextmanager
def con_bd() -> Iterator[Any]:
    """Conexión por petición: confirma al terminar y revierte ante error."""
    with conexion() as con:
        yield con


def mostrar_error(exc: Exception) -> None:
    """Muestra un error del ERP con su mensaje en español (y el código)."""
    if isinstance(exc, ErrorPollolandia):
        st.error(f"{exc.mensaje}  \n`{exc.codigo}`")
    else:
        st.error(f"Error inesperado: {exc}")


def chip(estado: str) -> str:
    """Etiqueta de estado coloreada (HTML)."""
    return f'<span class="pl-chip {COLOR_ESTADO.get(estado, "pl-info")}">{estado}</span>'


def bs(valor: Any) -> str:
    """Monto en formato boliviano; vacío para cero en columnas Debe/Haber."""
    return formato_bs(valor) if valor not in (None, "") else ""


def tabla(filas: Iterable[dict[str, Any]], columnas: Sequence[tuple[str, str]],
          montos: Sequence[str] = (), fechas: Sequence[str] = (), ocultar_ceros: bool = False) -> pd.DataFrame:
    """Arma un DataFrame con títulos en español, montos ``Bs 1.234,56`` y fechas ``dd/mm/aaaa``."""
    datos = []
    for f in filas:
        fila = {}
        for clave, titulo in columnas:
            valor = f.get(clave)
            if clave in montos:
                valor = "" if (ocultar_ceros and (valor is None or Decimal(valor) == 0)) else bs(valor)
            elif clave in fechas and valor:
                valor = fecha_pantalla(valor)
            fila[titulo] = "" if valor is None else valor
        datos.append(fila)
    return pd.DataFrame(datos, columns=[t for _, t in columnas])


def codigo_de(opcion: str | None) -> str | None:
    """Extrae el código de una opción ``"1.1.01.03 — Banco…"``."""
    if not opcion:
        return None
    return str(opcion).split(" — ", 1)[0].strip()


def decimal_de(valor: Any) -> Decimal:
    """Convierte la entrada numérica de un widget a ``Decimal`` sin ruido binario."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor == "":
        return Decimal("0")
    return Decimal(str(valor))


def descargar_pdf(etiqueta: str, archivo: str, datos: bytes, clave: str) -> None:
    """Botón de descarga de un PDF oficial ya generado."""
    st.download_button(f"Descargar {etiqueta} (PDF)", datos, file_name=archivo if archivo.endswith(".pdf")
                       else f"{archivo}.pdf", mime="application/pdf", key=clave)


def pdf_bajo_demanda(etiqueta: str, archivo: str, generar: Any, clave: str) -> None:
    """Para documentos extensos (libro diario, mayores de todas las cuentas):
    se generan al pulsar «Preparar» y se ofrecen para descargar. ``clave``
    debe incluir los filtros para no ofrecer un PDF desactualizado."""
    estado = f"_pdf_{clave}"
    if st.button(f"Preparar {etiqueta} (PDF)", key=f"{clave}_preparar"):
        try:
            st.session_state[estado] = generar()
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if estado in st.session_state:
        descargar_pdf(etiqueta, archivo, st.session_state[estado], f"{clave}_descargar")
