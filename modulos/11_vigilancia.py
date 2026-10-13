"""Módulo 11 — Consejo de Vigilancia (Fase 12).

Pestañas: Panel de controles del CV, CI-FIN-18 (INF-CV-07 y FIN-INF-05),
Arqueos e inventario sorpresivo, Verificación de activos (INF-CV-01),
Desembolsos (segregación del cofirmante) e Informes INF-CV-02, 05, 06 y 08.

Solo llama a ``servicios/`` y a ``reportes/``. El CV verifica sin aprobar ni
ejecutar; la Gerencia General emite el FIN-INF-05 solo con el INF-CV-07 del mes.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_presupuesto as rep
from reportes.formatos_tesoreria import Contexto
from servicios import indicadores, presupuesto, tesoreria, vigilancia
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}


def _ctx(sesion: Sesion, detalle: str = "") -> Contexto:
    return Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                    cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo=detalle)


def _pdf(nombre: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {nombre} (PDF)", datos, file_name=f"{nombre}.pdf", mime="application/pdf", key=clave)


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


def _es_cv(sesion: Sesion) -> bool:
    return sesion.rol == "CV"


# ==========================================================================
# 1. PANEL
# ==========================================================================
def _tab_panel(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cv_panel_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        panel = vigilancia.panel_cv(con, sesion, ejercicio)
    for c in panel["controles"]:
        st.markdown(f"{chip(c['semaforo'])} **{c['codigo']} — {c['nombre']}**: {c['realizados']} de "
                    f"{c['requeridos']} requeridos a la fecha · {c['frecuencia']}", unsafe_allow_html=True)
    st.caption("Pendiente (amarillo) mientras corre el ejercicio; incumplido (rojo) al cerrarlo. El INF-CV-07 es "
               "mensual y obligatorio antes del FIN-INF-05. INF-CV-04 se registra en «Cierres y estados financieros» "
               "e INF-CV-09 en «RAU, cumplimiento y archivo».")


# ==========================================================================
# 2. CI-FIN-18
# ==========================================================================
def _tab_ci_fin_18(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cv18_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        estado = indicadores.estado_ci_fin_18(con, sesion, ejercicio)
    st.dataframe(tabla([{**e, "dictamen": (e["dictamen"] or "").replace("_", " ").lower()} for e in estado],
                       [("periodo", "Mes"), ("fin_inf_01", "FIN-INF-01"), ("inf_cv_07", "INF-CV-07 (CV)"),
                        ("dictamen", "Dictamen"), ("fin_inf_05", "FIN-INF-05 (GG)")],
                       fechas=("fin_inf_01", "inf_cv_07", "fin_inf_05")), hide_index=True, use_container_width=True)
    hoy = date.today().isoformat()
    cerrados = [e for e in estado if e["fin"] < hoy]
    if _es_cv(sesion):
        pendientes = [e for e in cerrados if not e["inf_cv_07"]]
        if pendientes:
            per = st.selectbox("Mes a fiscalizar", pendientes, format_func=lambda e: e["periodo"], key="cv18_per")
            try:
                with con_bd() as con:
                    previa = indicadores.verificacion_previa(con, sesion, int(per["periodo_id"]))
            except ErrorPollolandia as exc:
                mostrar_error(exc)
                return
            c = st.columns(3)
            for i, k in enumerate(("ICL", "RINC", "ROA")):
                c[i].markdown(f"**{k}** reportado {rep.pct(previa['reportado'].get(k))} · recalculado "
                              f"{rep.pct(previa['recalculo'].get(k))}", unsafe_allow_html=True)
            st.caption(f"Recálculo independiente {'coincide' if previa['coincide'] else 'NO coincide'} con el FIN-INF-04. "
                       f"Margen bruto del mes {rep.pct(previa['margen_bruto_mes'])}; mes anterior "
                       f"{rep.pct(previa['margen_bruto_mes_anterior'])}; presupuesto "
                       f"{rep.pct(previa['margen_bruto_presupuesto'])}. FIN-INF-01: "
                       + (f"registrada ({previa['fin_inf_01']['pendientes']} pendientes)" if previa["fin_inf_01"]
                          else "NO registrada"))
            with st.form("form_cv07"):
                muestra = st.multiselect("Partidas significativas muestreadas (mínimo 3)", previa["partidas_disponibles"],
                                        placeholder="Elija las partidas")
                otra = st.text_input("Otra partida muestreada (opcional)")
                p1 = st.checkbox("Partidas muestreadas con respaldo razonable", value=True)
                p2 = st.checkbox("Recálculo independiente coincide", value=previa["coincide"])
                p3 = st.checkbox("Margen bruto razonable frente al mes anterior y al presupuesto", value=True)
                obs = st.text_area("Observaciones")
                if st.form_submit_button("Emitir INF-CV-07", type="primary"):
                    try:
                        with con_bd() as con:
                            r = indicadores.registrar_inf_cv_07(
                                con, sesion, int(per["periodo_id"]), date.today(),
                                partidas_muestreadas=list(muestra) + ([otra] if otra.strip() else []),
                                partidas_conformes=p1, recalculo_conforme=p2, razonabilidad_conforme=p3,
                                observaciones=obs or None)
                        st.success(f"INF-CV-07 emitido: {r['dictamen'].replace('_', ' ').lower()}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if sesion.rol == "GG":
        listos = [e for e in estado if e["puede_emitir"]]
        sin_cv = [e for e in cerrados if not e["inf_cv_07"] and not e["fin_inf_05"]]
        if sin_cv:
            st.warning("Meses sin INF-CV-07: el FIN-INF-05 no puede emitirse aún (CI-FIN-18): "
                       + ", ".join(e["periodo"] for e in sin_cv))
        if listos:
            per = st.selectbox("FIN-INF-05 a emitir", listos, format_func=lambda e: e["periodo"], key="gg05_per")
            with st.form("form_fin05"):
                st.markdown(f"**Informe Gerencial de Indicadores — {per['periodo']}** (INF-CV-07: "
                            f"{(per['dictamen'] or '').replace('_', ' ').lower()})")
                decisiones: dict[str, Any] = {}
                for k in ("ICL", "RINC", "ROA"):
                    decisiones[k] = {"accion": st.text_input(f"Acción / decisión sobre el {k} (obligatoria si está "
                                                             "en «Malo»)", key=f"dec_{k}")}
                incorporadas = st.text_area("Observaciones del INF-CV-07 incorporadas (si las hubo)")
                if st.form_submit_button("Emitir FIN-INF-05", type="primary"):
                    try:
                        with con_bd() as con:
                            indicadores.emitir_fin_inf_05(con, sesion, int(per["periodo_id"]), date.today(),
                                                          decisiones=decisiones,
                                                          observaciones_incorporadas=incorporadas or None)
                        st.success("FIN-INF-05 emitido.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    with con_bd() as con:
        informes = vigilancia.listar_informes_cv(con, sesion, "INF-CV-07", ejercicio)
    for i in informes:
        _pdf(f"INF-CV-07_{i['periodo']}", rep.inf_cv_07(i, _ctx(sesion)), f"pdf_cv07_{i['id']}")


# ==========================================================================
# 3. ARQUEOS E INVENTARIO SORPRESIVO
# ==========================================================================
def _tab_arqueos(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cv_arq_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        arqueos = vigilancia.arqueos_cv(con, sesion, ejercicio)
        inventarios = vigilancia.listar_inventarios(con, sesion)
    st.subheader("Arqueos sorpresivos de caja (INF-CV-03)")
    st.dataframe(tabla(arqueos, [("fecha", "Fecha"), ("total_fisico", "Total físico"),
                                 ("saldo_contable", "Saldo contable"), ("diferencia", "Diferencia"),
                                 ("custodio_presente", "Custodio"), ("arqueado_por", "Arqueado por")],
                       montos=("total_fisico", "saldo_contable", "diferencia"), fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    if _es_cv(sesion):
        with st.form("form_arqueo"):
            c1, c2 = st.columns(2)
            efectivo = c1.number_input("Efectivo físico contado (Bs)", min_value=0.0, step=10.0, format="%.2f")
            comprobantes = c2.number_input("Comprobantes de gasto no repuestos (Bs)", min_value=0.0, step=10.0,
                                           format="%.2f")
            custodio = st.text_input("Custodio presente")
            obs = st.text_input("Observaciones")
            if st.form_submit_button("Registrar arqueo", type="primary"):
                try:
                    with con_bd() as con:
                        r = tesoreria.registrar_arqueo(con, sesion, date.today(), decimal_de(efectivo),
                                                       decimal_de(comprobantes), custodio, obs or None)
                    st.success(f"Arqueo registrado; diferencia {bs(r.get('diferencia', 0))}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.subheader("Inventario físico sorpresivo")
    for inv in inventarios:
        st.markdown(f"**{fecha_pantalla(inv['fecha'])}** · {inv['items']} insumo(s) · {inv['con_diferencia']} con "
                    f"diferencia · {bs(inv['diferencia'])}" + (" · reportar a la Asamblea" if inv["reportar_ag"] else ""))
    if _es_cv(sesion):
        fecha = st.date_input("Fecha del conteo", date.today(), format="DD/MM/YYYY", key="inv_fecha")
        with con_bd() as con:
            base = vigilancia.insumos_para_conteo(con, sesion, fecha)
        editado = st.data_editor(pd.DataFrame([{"id": b["insumo_id"], "Código": b["codigo"], "Insumo": b["nombre"],
                                                "Unidad": b["unidad"], "Contable": float(b["contable"]),
                                                "Físico": float(b["contable"]), "Contar": False} for b in base]),
                                 hide_index=True, use_container_width=True, key="inv_editor",
                                 disabled=["id", "Código", "Insumo", "Unidad", "Contable"])
        obs = st.text_input("Observación del inventario", key="inv_obs")
        if st.button("Registrar inventario sorpresivo", type="primary", key="inv_btn"):
            conteos = {int(r["id"]): decimal_de(r["Físico"]) for _, r in editado.iterrows() if r["Contar"]}
            try:
                with con_bd() as con:
                    r = vigilancia.registrar_inventario_sorpresivo(con, sesion, fecha, conteos, obs or None)
                (st.warning if r["reportar_ag"] else st.success)(
                    f"Inventario registrado: {r['con_diferencia']} diferencia(s) por {bs(r['diferencia'])}"
                    + (" — reportar a la Asamblea General." if r["reportar_ag"] else "."))
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ==========================================================================
# 4. VERIFICACIÓN DE ACTIVOS (INF-CV-01)
# ==========================================================================
def _tab_activos(sesion: Sesion) -> None:
    with con_bd() as con:
        estado = vigilancia.verificaciones_activos(con, sesion, date.today())
    ultima = estado["ultima"]
    st.markdown(f"{chip('ROJO' if estado['vencida'] else 'VERDE')} Última verificación física: "
                + (fecha_pantalla(ultima["fecha"]) if ultima else "ninguna")
                + f" · plazo {estado['plazo_meses']} meses", unsafe_allow_html=True)
    st.dataframe(tabla(estado["actas"], [("fecha", "Fecha"), ("activos_verificados", "Activos"),
                                         ("faltantes", "Faltantes"), ("tasas_distintas", "Tasas distintas"),
                                         ("verificado_por", "Verificado por")], fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    st.caption("El acta INF-CV-01 se registra activo por activo en «Activos fijos y CAPEX».")


# ==========================================================================
# 5. DESEMBOLSOS
# ==========================================================================
def _tab_desembolsos(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cv_des_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        filas = vigilancia.desembolsos_a_verificar(con, sesion, ejercicio)
    st.dataframe(tabla([{**f, "pago_fecha": f["pago"]["fecha"] if f["pago"] else None,
                         "verif": (f"{fecha_pantalla(f['verificado']['fecha'])} — {f['verificado']['por']}"
                                   if f["verificado"] else "Pendiente"), "mancomunada_txt": "Sí" if f["mancomunada"] else "No"}
                        for f in filas],
                       [("autorizacion_id", "N.º"), ("descripcion", "Descripción"), ("monto", "Monto"),
                        ("mancomunada_txt", "Mancomunada"), ("firmantes", "Firmantes"), ("pago_fecha", "Pago"),
                        ("verif", "Verificación")], montos=("monto",), fechas=("pago_fecha",)),
                 hide_index=True, use_container_width=True)
    st.caption("Segregación de funciones (MOF §3.2): el miembro del CV que cofirmó un desembolso no puede verificarlo.")
    if _es_cv(sesion):
        elegibles = [f for f in filas if f["puedo_verificar"]]
        if elegibles:
            with st.form("form_verif_des"):
                d = st.selectbox("Desembolso a verificar", elegibles,
                                 format_func=lambda x: f"N.º {x['autorizacion_id']} — {x['descripcion']} ({bs(x['monto'])})")
                conforme = st.checkbox("Conforme", value=True)
                obs = st.text_input("Observación (obligatoria si no es conforme)")
                if st.form_submit_button("Registrar verificación", type="primary"):
                    try:
                        with con_bd() as con:
                            vigilancia.registrar_verificacion_desembolso(con, sesion, int(d["autorizacion_id"]),
                                                                         date.today(), conforme, obs or None)
                        st.success("Verificación registrada.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        else:
            st.info("No hay desembolsos que usted pueda verificar (ya verificados o firmados por usted).")


# ==========================================================================
# 6. INFORMES INF-CV-02, 05, 06 Y 08
# ==========================================================================
def _tab_informes(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cv_inf_ej")
    if ejercicio is None:
        return
    codigo = st.selectbox("Informe", ["INF-CV-02", "INF-CV-05", "INF-CV-06", "INF-CV-08"], key="cv_inf_cod",
                          format_func=lambda c: f"{c} — {vigilancia.INFORMES_CV[c]}")
    with con_bd() as con:
        registrados = vigilancia.listar_informes_cv(con, sesion, codigo, ejercicio)
    st.dataframe(tabla([{**r, "dictamen_txt": r["dictamen"].replace("_", " ").lower()} for r in registrados],
                       [("periodo", "Período"), ("fecha", "Fecha"), ("dictamen_txt", "Dictamen"),
                        ("observaciones", "Observaciones"), ("reportado_ag_en", "Reportado a la AG"),
                        ("elaborado_por", "Elaborado por")], fechas=("fecha", "reportado_ag_en")),
                 hide_index=True, use_container_width=True)
    if codigo == "INF-CV-02":
        for r in registrados:
            _pdf(f"INF-CV-02_{r['periodo']}", rep.inf_cv_02(r, _ctx(sesion)), f"pdf_cv02_{r['id']}")
        trimestre = int(st.selectbox("Trimestre del ejercicio", [1, 2, 3, 4], key="cv02_t"))
        with con_bd() as con:
            datos = vigilancia.datos_inf_cv_02(con, sesion, ejercicio, trimestre)
        st.dataframe(tabla([{**c, "pct": rep.pct(c["cumplimiento"]), "cumple_txt": "Sí" if c["cumple"] else "No"}
                            for c in datos["ciclos"]],
                           [("ciclo", "Ciclo"), ("cierre", "Cierre"), ("sme", "SME"), ("saldo", "Saldo real"),
                            ("pct", "Cumplimiento"), ("cumple_txt", "¿Cumple?")], montos=("sme", "saldo"),
                           fechas=("cierre",)), hide_index=True, use_container_width=True)
        if datos["reportar_ag"]:
            st.error(f"{datos['consecutivos']} ciclos consecutivos bajo el SME: reportar a la Asamblea General.")
        if _es_cv(sesion) and st.button("Registrar INF-CV-02", key="btn_cv02"):
            try:
                with con_bd() as con:
                    r = vigilancia.registrar_inf_cv_02(con, sesion, ejercicio, trimestre, date.today())
                st.success(f"INF-CV-02 registrado: {r['dictamen'].replace('_', ' ').lower()}.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        sin_reporte = [r for r in registrados if r["datos"].get("reportar_ag") and not r["reportado_ag_en"]]
        if _es_cv(sesion) and sin_reporte and st.button("Registrar reporte a la Asamblea", key="btn_ag02"):
            try:
                with con_bd() as con:
                    vigilancia.registrar_reporte_ag(con, sesion, int(sin_reporte[0]["id"]), date.today())
                st.success("Reporte registrado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    elif codigo == "INF-CV-05":
        with con_bd() as con:
            datos = vigilancia.datos_inf_cv_05(con, sesion, ejercicio)
        st.dataframe(tabla(datos["filas"], [("concepto", "Concepto"), ("devengado", "Devengado en el ejercicio"),
                                            ("saldo", "Saldo al cierre")], montos=("devengado", "saldo")),
                     hide_index=True, use_container_width=True)
        if _es_cv(sesion):
            with st.form("form_cv05"):
                suf = st.checkbox("Provisiones constituidas y suficientes", value=True)
                dia = st.checkbox("Aportes a la Gestora y a la CNS al día", value=True)
                obs = st.text_area("Observaciones")
                if st.form_submit_button("Registrar INF-CV-05", type="primary"):
                    try:
                        with con_bd() as con:
                            vigilancia.registrar_inf_cv_05(con, sesion, ejercicio, date.today(),
                                                           provisiones_suficientes=suf, aportes_al_dia=dia,
                                                           observaciones=obs or None)
                        st.success("INF-CV-05 registrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    elif codigo == "INF-CV-06":
        precios: dict[str, Any] = {}
        c = st.columns(3)
        for i, canal in enumerate(("HORECA", "FRIAL", "MERCADO_POPULAR")):
            precios[canal] = c[i].number_input(f"Precio de mercado {canal} (Bs/kg)", min_value=0.0, step=0.5,
                                               key=f"pm_{canal}") or None
        with con_bd() as con:
            datos = vigilancia.datos_inf_cv_06(con, sesion, date.today(), precios)
        if datos.get("error"):
            st.info(datos["error"])
        st.dataframe(tabla([{**f, "margen_txt": rep.pct(f["margen"]), "dif": rep.pct(f["diferencia_mercado"])}
                            for f in datos["filas"]],
                           [("canal", "Canal"), ("precio", "Tarifario"), ("costo_unitario", "Costo unitario"),
                            ("margen_txt", "Margen"), ("precio_mercado", "Mercado"), ("dif", "Diferencia vs mercado")],
                           montos=("precio", "costo_unitario", "precio_mercado")), hide_index=True,
                     use_container_width=True)
        if _es_cv(sesion) and datos["filas"] and st.button("Registrar INF-CV-06", key="btn_cv06"):
            try:
                with con_bd() as con:
                    r = vigilancia.registrar_inf_cv_06(con, sesion, date.today(), precios_mercado=precios)
                st.success("INF-CV-06 registrado" + (" con desvío de margen." if r["desviado"] else "."))
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    else:
        try:
            with con_bd() as con:
                datos = vigilancia.datos_inf_cv_08(con, sesion, ejercicio)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
        for c in datos["controles"]:
            st.markdown(f"{chip('VERDE' if c['cumple'] else 'ROJO')} {c['control']}", unsafe_allow_html=True)
        if _es_cv(sesion) and st.button("Registrar INF-CV-08", key="btn_cv08"):
            try:
                with con_bd() as con:
                    r = vigilancia.registrar_inf_cv_08(con, sesion, ejercicio, date.today())
                st.success("INF-CV-08 registrado" + ("." if r["cuadran"] else " con observaciones."))
            except ErrorPollolandia as exc:
                mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 11 — Consejo de Vigilancia."""
    st.header("Consejo de Vigilancia")
    st.caption("El Consejo de Vigilancia verifica sin aprobar ni ejecutar (MOF; Manual Presupuestario P-09).")
    pestañas = st.tabs(["Panel", "CI-FIN-18 (INF-CV-07 / FIN-INF-05)", "Arqueos e inventario", "Activos (INF-CV-01)",
                        "Desembolsos", "Informes INF-CV"])
    funciones = (_tab_panel, _tab_ci_fin_18, _tab_arqueos, _tab_activos, _tab_desembolsos, _tab_informes)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            try:
                funcion(sesion)
            except ErrorPollolandia as exc:
                mostrar_error(exc)

