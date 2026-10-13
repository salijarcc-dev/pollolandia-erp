"""Administración del sistema (Fase 13): parámetros con historial, respaldos
3-2-1 con actas de restauración y revisión de seguridad.

Solo llama a ``servicios/`` y a ``reportes/``.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from modulos.comun import chip, con_bd, mostrar_error, tabla
from reportes import formatos_admin as rep
from reportes.formatos_tesoreria import Contexto
from servicios import parametros, respaldo, revision_seguridad, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}


def _ctx(sesion: Sesion) -> Contexto:
    return Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                    cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo="")


def _puede(sesion: Sesion, recurso: str, accion: str) -> bool:
    with con_bd() as con:
        return seguridad.tiene_permiso(con, sesion.rol, recurso, accion)


# ==========================================================================
# PARÁMETROS
# ==========================================================================
def pagina_parametros(sesion: Sesion) -> None:
    """Parámetros normativos vigentes, historial y cambios GG → CA/AG."""
    st.header("Parámetros normativos")
    st.caption("Toda cifra normativa vive en esta tabla con vigencia por ejercicio (regla 2). La Gerencia General "
               "propone el cambio y el Consejo de Administración o la Asamblea lo aprueban con acta; ninguna vigencia "
               "se borra.")
    with con_bd() as con:
        ejercicios = [int(r[0]) for r in con.execute("SELECT ejercicio FROM ejercicios ORDER BY ejercicio")]
    hoy = date.today()
    actual = hoy.year if hoy.month >= 7 else hoy.year - 1
    c1, c2, c3 = st.columns([1, 2, 1])
    ejercicio = c1.selectbox("Ejercicio", ejercicios, index=ejercicios.index(actual) if actual in ejercicios else 0,
                             format_func=lambda e: f"{e}-{(e + 1) % 100:02d}", key="par_ej")
    filtro = c2.text_input("Buscar por clave o descripción", key="par_filtro")
    solo = c3.checkbox("Solo «por validar»", key="par_solo")
    tabs = st.tabs(["Vigentes", "Historial", "Solicitudes de cambio"])
    with tabs[0]:
        with con_bd() as con:
            filas = parametros.listar_parametros(con, sesion, int(ejercicio), filtro, solo)
        st.caption(f"{len(filas)} parámetro(s); {sum(1 for f in filas if f['por_validar'])} marcado(s) por validar.")
        st.dataframe(tabla([{**f, "marca": "Por validar" if f["por_validar"] else "",
                             "hasta": f["ejercicio_hasta"] or "abierta",
                             "pendiente": f"N.º {f['solicitud_pendiente']}" if f["solicitud_pendiente"] else ""}
                            for f in filas],
                           [("clave", "Clave"), ("valor", "Valor"), ("tipo", "Tipo"), ("ejercicio_desde", "Desde"),
                            ("hasta", "Hasta"), ("descripcion", "Descripción"), ("fuente_documental", "Fuente"),
                            ("marca", "Estado"), ("pendiente", "Solicitud")]),
                     hide_index=True, use_container_width=True)
        if _puede(sesion, "parametros", "EDITAR") and filas:
            with st.form("form_solicitud_par"):
                st.markdown("**Proponer un cambio (Gerencia General)**")
                elegido = st.selectbox("Parámetro", filas, format_func=lambda f: f"{f['clave']} = {f['valor']}")
                a, b = st.columns(2)
                valor = a.text_input("Valor nuevo")
                desde = b.selectbox("Rige desde el ejercicio", ejercicios,
                                    index=ejercicios.index(int(ejercicio)) if int(ejercicio) in ejercicios else 0)
                fuente = st.text_input("Fuente documental (documento, artículo, acta)")
                motivo = st.text_area("Motivo")
                if st.form_submit_button("Enviar al Consejo de Administración", type="primary"):
                    try:
                        with con_bd() as con:
                            r = parametros.solicitar_cambio(con, sesion, elegido["clave"], valor, int(desde), fuente,
                                                            motivo)
                        st.success(f"Solicitud N.º {r['id']} registrada; espera la aprobación del CA o de la AG.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    with tabs[1]:
        with con_bd() as con:
            claves = [r[0] for r in con.execute("SELECT DISTINCT clave FROM parametros ORDER BY clave")]
        clave = st.selectbox("Parámetro", claves, key="par_hist")
        if clave:
            with con_bd() as con:
                h = parametros.historial(con, sesion, clave)
            st.markdown("**Vigencias**")
            st.dataframe(tabla([{**v, "hasta": v["ejercicio_hasta"] or "abierta"} for v in h["vigencias"]],
                               [("valor", "Valor"), ("ejercicio_desde", "Desde"), ("hasta", "Hasta"),
                                ("fuente_documental", "Fuente"), ("descripcion", "Descripción")]),
                         hide_index=True, use_container_width=True)
            st.markdown("**Solicitudes**")
            st.dataframe(tabla(h["solicitudes"], [("id", "N.º"), ("valor_anterior", "Anterior"),
                                                  ("valor_nuevo", "Nuevo"), ("ejercicio_desde", "Desde"),
                                                  ("estado", "Estado"), ("solicitado_por", "Solicitó"),
                                                  ("resuelto_por", "Resolvió"), ("acta_ref", "Acta"),
                                                  ("motivo", "Motivo")]), hide_index=True, use_container_width=True)
            st.markdown("**Bitácora (valores antes y después)**")
            st.dataframe(tabla(h["eventos"], [("fecha_hora", "Fecha y hora"), ("usuario", "Usuario"), ("rol", "Rol"),
                                              ("accion", "Acción"), ("valores_antes", "Antes"),
                                              ("valores_despues", "Después"), ("detalle", "Detalle")]),
                         hide_index=True, use_container_width=True)
    with tabs[2]:
        with con_bd() as con:
            solicitudes = parametros.listar_solicitudes(con, sesion)
        st.dataframe(tabla(solicitudes, [("id", "N.º"), ("clave", "Clave"), ("valor_anterior", "Anterior"),
                                         ("valor_nuevo", "Nuevo"), ("ejercicio_desde", "Desde"), ("estado", "Estado"),
                                         ("solicitado_por", "Solicitó"), ("resuelto_por", "Resolvió"),
                                         ("acta_ref", "Acta"), ("observacion", "Observación")]),
                     hide_index=True, use_container_width=True)
        pendientes = [x for x in solicitudes if x["estado"] == "SOLICITADA"]
        if pendientes and _puede(sesion, "parametros", "APROBAR"):
            with st.form("form_resolver_par"):
                sol = st.selectbox("Solicitud", pendientes,
                                   format_func=lambda x: f"N.º {x['id']} · {x['clave']}: {x['valor_anterior']} → "
                                                         f"{x['valor_nuevo']} desde {x['ejercicio_desde']}")
                st.caption(f"Motivo: {sol['motivo']} · Fuente: {sol['fuente_documental']}")
                decision = st.radio("Decisión", ["Aprobar", "Rechazar"], horizontal=True)
                acta = st.text_input("Acta (obligatoria para aprobar)")
                obs = st.text_input("Observación (obligatoria para rechazar)")
                if st.form_submit_button("Registrar decisión", type="primary"):
                    try:
                        with con_bd() as con:
                            estado = parametros.resolver_cambio(con, sesion, int(sol["id"]), decision == "Aprobar",
                                                                acta, obs)
                        st.success(f"Solicitud {estado.lower()}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)


# ==========================================================================
# RESPALDOS 3-2-1
# ==========================================================================
def pagina_respaldos(sesion: Sesion) -> None:
    """Cumplimiento 3-2-1, respaldos, pruebas de restauración y destinos."""
    st.header("Respaldos 3-2-1")
    st.caption("MAN-ARC-01 §5.2: 3 copias (equipo, disco externo, fuera de la granja o nube), 2 medios, 1 fuera del "
               "sitio; copia diaria, completa semanal y de cierre mensual y anual; prueba de restauración semestral "
               "con acta verificada por el Consejo de Vigilancia.")
    tabs = st.tabs(["Cumplimiento", "Respaldos", "Pruebas de restauración", "Destinos"])
    with tabs[0]:
        with con_bd() as con:
            e = respaldo.estado_321(con, sesion)
        st.markdown(f"{chip(e['semaforo'])} **Estado general de los respaldos**", unsafe_allow_html=True)
        for c in e["controles"]:
            st.markdown(f"{chip(c['semaforo'])} **{c['control']}** — {c['detalle']}", unsafe_allow_html=True)
    with tabs[1]:
        with con_bd() as con:
            lista = respaldo.listar_respaldos(con, sesion)
        st.dataframe(tabla([{**r, "copias_txt": f"{r['copias']} de 3",
                             "tam": f"{r['bytes'] / 1024:,.0f} KB".replace(",", "."),
                             "inc": "Incremental" if r["incremental"] else "Completo"} for r in lista],
                           [("fecha", "Fecha"), ("tipo", "Tipo"), ("referencia", "Referencia"), ("inc", "Alcance"),
                            ("documentos", "Documentos"), ("copias_txt", "Copias"), ("tam", "Tamaño"),
                            ("estado", "Estado"), ("creado_por", "Creado por"), ("archivo", "Paquete")],
                           fechas=("fecha",)), hide_index=True, use_container_width=True)
        if _puede(sesion, "respaldos", "CREAR"):
            a, b = st.columns(2)
            if a.button("Ejecutar respaldos pendientes de hoy", type="primary", key="resp_auto"):
                try:
                    with con_bd() as con:
                        r = respaldo.ejecutar_automatico(con)
                    st.success(f"{len(r['respaldos'])} respaldo(s) creados; {len(r['depurados'])} depurado(s).")
                    for err in r["errores"]:
                        st.error(f"{err['tipo']}: {err['error']}")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
            if b.button("Respaldo completo manual ahora", key="resp_manual"):
                try:
                    with con_bd() as con:
                        r = respaldo.crear_respaldo(con, sesion, "MANUAL")
                    st.success(f"Respaldo {r['archivo']} en {r['copias']} de 3 copias.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with tabs[2]:
        with con_bd() as con:
            actas = respaldo.listar_actas(con, sesion)
            vigentes = [r for r in respaldo.listar_respaldos(con, sesion) if r["estado"] == "VIGENTE"]
        st.dataframe(tabla([{**a, "visto": a["verificado_por"] or "Pendiente"} for a in actas],
                           [("id", "Acta"), ("fecha", "Fecha"), ("tipo", "Respaldo"), ("referencia", "Referencia"),
                            ("ubicacion", "Copia"), ("resultado", "Resultado"), ("elaborado_por", "Elaboró"),
                            ("visto", "Visto del CV")], fechas=("fecha",)), hide_index=True, use_container_width=True)
        for a in actas:
            st.download_button(f"Descargar acta N.º {a['id']} (PDF)", rep.acta_restauracion(a, _ctx(sesion)),
                               file_name=f"ACTA-RES_{a['id']}.pdf", mime="application/pdf", key=f"acta_{a['id']}")
        if vigentes and _puede(sesion, "respaldos", "CREAR"):
            with st.form("form_prueba"):
                elegido = st.selectbox("Respaldo a restaurar", vigentes,
                                       format_func=lambda r: f"{r['tipo']} {r['referencia']} ({r['copias']} copias)")
                ubicacion = st.selectbox("Copia a restaurar", respaldo.UBICACIONES,
                                         format_func=lambda u: respaldo.NOMBRE_UBICACION[u])
                if st.form_submit_button("Probar restauración y levantar acta", type="primary"):
                    try:
                        with con_bd() as con:
                            acta = respaldo.probar_restauracion(con, sesion, int(elegido["id"]), ubicacion)
                        (st.success if acta["resultado"] == "CONFORME" else st.error)(
                            f"Acta N.º {acta['id']}: restauración {acta['resultado'].lower()}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        sin_visto = [a for a in actas if not a["verificado_por"]]
        if sin_visto and _puede(sesion, "respaldos", "VERIFICAR"):
            with st.form("form_visto"):
                acta = st.selectbox("Acta a verificar", sin_visto,
                                    format_func=lambda a: f"N.º {a['id']} · {a['tipo']} {a['referencia']} · "
                                                          f"{a['resultado']}")
                obs = st.text_input("Observación del Consejo de Vigilancia")
                if st.form_submit_button("Registrar visto", type="primary"):
                    try:
                        with con_bd() as con:
                            respaldo.verificar_acta(con, sesion, int(acta["id"]), obs or None)
                        st.success("Visto registrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    with tabs[3]:
        d = respaldo.destinos()
        st.table(pd.DataFrame([{"Copia": respaldo.NOMBRE_UBICACION[u], "Carpeta": str(d[u]) if d[u] else "Sin configurar"}
                               for u in respaldo.UBICACIONES]))
        if _puede(sesion, "respaldos", "AUTORIZAR"):
            with st.form("form_destinos"):
                externo = st.text_input("Disco externo local (p. ej. E:\\RespaldosPollolandia)",
                                        value=str(d["EXTERNO"] or ""))
                fuera = st.text_input("Copia fuera de la granja o carpeta sincronizada con la nube",
                                      value=str(d["FUERA_SITIO"] or ""))
                if st.form_submit_button("Guardar destinos", type="primary"):
                    try:
                        with con_bd() as con:
                            respaldo.guardar_destinos(con, sesion, externo or None, fuera or None)
                        st.success("Destinos guardados.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        st.caption("Las variables de entorno POLLOLANDIA_RESPALDO_EXTERNO y POLLOLANDIA_RESPALDO_FUERA_SITIO tienen "
                   "prioridad. Use discos cifrados (BitLocker) para los respaldos (MAN-ARC-01 §5.2).")


# ==========================================================================
# REVISIÓN DE SEGURIDAD
# ==========================================================================
def pagina_seguridad(sesion: Sesion) -> None:
    """Controles de acceso y segregación con semáforo."""
    st.header("Revisión de seguridad")
    with con_bd() as con:
        rev = revision_seguridad.revision(con, sesion)
    r = rev["resumen"]
    st.markdown(f"{chip(rev['semaforo'])} **{r['VERDE']} controles en verde, {r['AMARILLO']} en amarillo y "
                f"{r['ROJO']} en rojo** — {fecha_pantalla(rev['fecha'][:10])}", unsafe_allow_html=True)
    for area in dict.fromkeys(c["area"] for c in rev["controles"]):
        st.subheader(area)
        for c in [x for x in rev["controles"] if x["area"] == area]:
            st.markdown(f"{chip(c['semaforo'])} **{c['control']}** — {c['detalle']} _({c['fuente']})_",
                        unsafe_allow_html=True)
    st.download_button("Descargar informe REV-SEG (PDF)", rep.informe_seguridad(rev, _ctx(sesion)),
                       file_name="REV-SEG.pdf", mime="application/pdf", key="pdf_revseg")
