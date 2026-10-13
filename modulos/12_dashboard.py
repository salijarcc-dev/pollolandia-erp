"""Módulo 12 — Tablero de indicadores (Fase 12).

Cuadro de mando (FIN-TES-13) con los semáforos de la sección 1.3 del Manual
Presupuestario: ICL, RINC, ROA, margen operativo, SME, ICA, PMF, DPF, CAC y
mortalidad; margen por canal; tendencias por ciclo y por ejercicio (real frente
a proyectado) y estado del flujo CI-FIN-18 con los FIN-INF-05 emitidos.

Solo llama a ``servicios/`` y a ``reportes/``; los umbrales se leen de
``parametros`` en la capa de servicios.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st

from modulos.comun import chip, con_bd, mostrar_error, tabla
from reportes import formatos_presupuesto as rep
from reportes.formatos_tesoreria import Contexto
from servicios import indicadores, presupuesto
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
ORDEN = ("ICL", "RINC", "ROA", "MARGEN_OPERATIVO", "SME", "ICA", "PMF", "DPF", "CAC", "MORTALIDAD")
NIVEL_TEXTO = {"BUENO": "Bueno", "NORMAL": "Normal", "MALO": "Malo"}
CANAL_TEXTO = {"HORECA": "HORECA", "FRIAL": "Friales", "MERCADO_POPULAR": "Mercado popular"}


def _ctx(sesion: Sesion) -> Contexto:
    return Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                    cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo="")


def _ejercicio(clave: str) -> int | None:
    with con_bd() as con:
        lista = presupuesto.ejercicios(con)
    if not lista:
        st.info("No hay ejercicios registrados.")
        return None
    hoy = date.today()
    actual = hoy.year if hoy.month >= 7 else hoy.year - 1
    return st.selectbox("Ejercicio (julio a junio)", lista, index=lista.index(actual) if actual in lista else 0,
                        key=clave, format_func=lambda e: f"{e}-{(e + 1) % 100:02d}")


def _formato(clave: str, valor: Decimal | None) -> str:
    if valor is None:
        return "—"
    return rep.num(valor) if clave == "ICA" else rep.pct(valor)


def _porcentaje(valor: Any) -> float | None:
    """Fracción → porcentaje para los gráficos (None si no hay dato)."""
    return None if valor is None else float(Decimal(valor) * 100)


# ==========================================================================
# 1. CUADRO DE MANDO
# ==========================================================================
def _tab_cuadro(sesion: Sesion) -> None:
    ejercicio = _ejercicio("db_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        datos = indicadores.indicadores_ejercicio(con, sesion, ejercicio, date.today())
        anterior = None
        if ejercicio - 1 in presupuesto.ejercicios(con):
            anterior = indicadores.indicadores_ejercicio(con, sesion, ejercicio - 1)
    st.caption(f"Acumulado del {fecha_pantalla(datos['desde'])} al {fecha_pantalla(datos['hasta'])}. "
               "Semáforos de la sección 1.3; proyectado según el Manual Presupuestario (E.3).")
    ind = datos["indicadores"]
    claves = [c for c in ORDEN if c in ind]
    for n, clave in enumerate(claves):
        if n % 5 == 0:
            columnas = st.columns(5)
        i = ind[clave]
        with columnas[n % 5]:
            st.metric(i["nombre"], _formato(clave, i["valor"]),
                      help=f"{i['formula']}. Meta {i['meta']}." + (f" {i['detalle']}." if i.get("detalle") else ""))
            texto = NIVEL_TEXTO.get(i["nivel"] or "", "Sin dato")
            proy = f" · proyectado {_formato(clave, i['proyectado'])}" if i.get("proyectado") is not None else ""
            st.markdown((chip(i["semaforo"]) + " " if i["semaforo"] else "") + f"{texto}{proy}",
                        unsafe_allow_html=True)
    st.subheader("Margen bruto por canal")
    canales = [{**c, "canal_txt": CANAL_TEXTO.get(c["canal"], c["canal"]), "margen_txt": rep.pct(c["margen"]),
                "semaforo_txt": "Cumple la meta" if c["semaforo"] == "VERDE" else "Bajo la meta"}
               for c in datos["margen_canal"]]
    if canales:
        st.dataframe(tabla(canales, [("canal_txt", "Canal"), ("kg", "Kg vendidos"), ("ingresos", "Ingresos"),
                                     ("precio", "Precio promedio"), ("costo_unitario", "Costo unitario"),
                                     ("margen_txt", "Margen bruto"), ("semaforo_txt", "Semáforo")],
                           montos=("ingresos", "precio", "costo_unitario")), hide_index=True, use_container_width=True)
        st.bar_chart(pd.DataFrame({"Margen bruto (%)": [_porcentaje(c["margen"]) for c in canales]},
                                  index=[c["canal_txt"] for c in canales]))
    else:
        st.info("Sin notas de entrega de carne o sin costo de referencia en el período.")
    st.download_button("Descargar FIN-TES-13 (PDF)", rep.fin_tes_13(datos, _ctx(sesion), anterior),
                       file_name=f"FIN-TES-13_{ejercicio}.pdf", mime="application/pdf", key="pdf_fin_tes_13")


# ==========================================================================
# 2. TENDENCIAS
# ==========================================================================
def _tab_tendencias(sesion: Sesion) -> None:
    ejercicio = _ejercicio("db_tend_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        ciclos = indicadores.tendencia_ciclos(con, sesion, ejercicio)
        anios = indicadores.tendencia_ejercicios(con, sesion)
    st.subheader(f"Por ciclo — ejercicio {ejercicio}-{(ejercicio + 1) % 100:02d}")
    if ciclos:
        indice = [f"C{c['ciclo']}" for c in ciclos]
        st.line_chart(pd.DataFrame({"ICL (%)": [_porcentaje(c["ICL"]) for c in ciclos],
                                    "Margen operativo (%)": [_porcentaje(c["MARGEN_OPERATIVO"]) for c in ciclos],
                                    "Mortalidad (%)": [_porcentaje(c["MORTALIDAD"]) for c in ciclos],
                                    "PMF (%)": [_porcentaje(c["PMF"]) for c in ciclos]}, index=indice))
        st.line_chart(pd.DataFrame({"SME al inicio del ciclo (%)": [_porcentaje(c["SME"]) for c in ciclos]},
                                   index=indice))
        st.dataframe(tabla([{**c, "icl": rep.pct(c["ICL"]), "mo": rep.pct(c["MARGEN_OPERATIVO"]),
                             "ica": rep.num(c["ICA"]) if c["ICA"] is not None else "—", "pmf": rep.pct(c["PMF"]),
                             "mort": rep.pct(c["MORTALIDAD"]), "sme": rep.pct(c["SME"])} for c in ciclos],
                           [("ciclo", "Ciclo"), ("desde", "Desde"), ("hasta", "Hasta"), ("ventas", "Ventas"),
                            ("costo_ventas", "Costo de ventas"), ("ebit", "EBIT"), ("icl", "ICL"),
                            ("mo", "Margen operativo"), ("ica", "ICA"), ("pmf", "PMF"), ("mort", "Mortalidad"),
                            ("sme", "SME"), ("saldo_inicio", "Saldo al inicio")],
                           montos=("ventas", "costo_ventas", "ebit", "saldo_inicio"), fechas=("desde", "hasta")),
                     hide_index=True, use_container_width=True)
    st.subheader("Por ejercicio — real frente a proyectado (2026-2030)")
    indice = [f"{a['ejercicio']}-{(a['ejercicio'] + 1) % 100:02d}" for a in anios]
    for clave, nombre in (("ICL", "ICL"), ("ROA", "ROA"), ("MARGEN_OPERATIVO", "Margen operativo"), ("RINC", "RINC"),
                          ("SME", "SME")):
        st.markdown(f"**{nombre} (%)**")
        st.line_chart(pd.DataFrame({"Real": [_porcentaje(a.get(clave)) for a in anios],
                                    "Proyectado": [_porcentaje(a.get(f"{clave}_proyectado")) for a in anios]},
                                   index=indice))
    st.caption("Los ejercicios sin operaciones solo muestran lo proyectado.")


# ==========================================================================
# 3. INFORME GERENCIAL (CI-FIN-18)
# ==========================================================================
def _tab_gerencial(sesion: Sesion) -> None:
    ejercicio = _ejercicio("db_ger_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        estado = indicadores.estado_ci_fin_18(con, sesion, ejercicio)
        emitidos = indicadores.listar_fin_inf_05(con, sesion)
    hoy = date.today().isoformat()
    filas = []
    for e in estado:
        if e["fin_inf_05"]:
            color, texto = "VERDE", "FIN-INF-05 emitido"
        elif e["inf_cv_07"]:
            color, texto = "AMARILLO", "Listo para emitir"
        elif e["fin"] < hoy:
            color, texto = "ROJO", "Bloqueado: falta el INF-CV-07"
        elif e["fin"][:7] == hoy[:7]:
            color, texto = None, "Mes en curso"
        else:
            color, texto = None, "Mes futuro"
        filas.append({**e, "estado_txt": texto, "color": color})
    for f in filas:
        st.markdown((chip(f["color"]) + " " if f["color"] else "") + f"**{f['periodo']}** — {f['estado_txt']}",
                    unsafe_allow_html=True)
    st.caption("CI-FIN-18: el FIN-INF-05 de la Gerencia General solo puede emitirse con el INF-CV-07 del mismo mes "
               "(registro en «Consejo de Vigilancia»).")
    for g in [g for g in emitidos if g["fecha_inicio"] >= f"{ejercicio}-07-01"
              and g["fecha_fin"] <= f"{ejercicio + 1}-06-30"]:
        st.download_button(f"Descargar FIN-INF-05 {g['periodo']} (PDF)", rep.fin_inf_05(g, _ctx(sesion)),
                           file_name=f"FIN-INF-05_{g['fecha_inicio'][:7]}.pdf", mime="application/pdf",
                           key=f"pdf_fin_inf_05_{g['id']}")


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 12 — Tablero de indicadores."""
    st.header("Tablero de indicadores")
    pestañas = st.tabs(["Cuadro de mando (FIN-TES-13)", "Tendencias", "Informe gerencial (CI-FIN-18)"])
    for pestaña, funcion in zip(pestañas, (_tab_cuadro, _tab_tendencias, _tab_gerencial)):
        with pestaña:
            try:
                funcion(sesion)
            except ErrorPollolandia as exc:
                mostrar_error(exc)
