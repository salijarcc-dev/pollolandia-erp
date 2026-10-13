"""Módulo 4 — Operativo y trazabilidad del ciclo de 73 días (Fase 6, Ciclo 3).

Pestañas: Ciclos y galpones, Bitácora diaria, Alertas sanitarias, Bajas por
mortalidad, Disposición y peso, Faenado, Conciliación y QR, Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. La Unidad Técnica registra el
campo (bitácora, alertas, disposición, peso, faenado); Finanzas abre el ciclo
y clasifica las bajas; la Gerencia General decide, autoriza y revisa.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_produccion as pdf
from servicios import produccion as pr
from servicios import seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
SEMAFORO = {"VERDE": "Verde", "AMARILLO": "Amarillo", "ROJO": "Rojo", "BUENO": "Bueno", "NORMAL": "Normal",
            "MALO": "Malo"}


def _n(valor) -> str:
    return "" if valor is None else f"{int(valor):,}".replace(",", ".")


def _dec(valor, decimales: int = 2) -> str:
    return "" if valor is None else f"{Decimal(str(valor)):,.{decimales}f}".replace(",", "X").replace(".", ",").replace(
        "X", ".")


def _ciclo_elegido(sesion: Sesion, clave: str) -> dict | None:
    with con_bd() as con:
        ciclos = pr.listar_ciclos(con, sesion)
    if not ciclos:
        st.info("Aún no hay ciclos abiertos. Finanzas abre el ciclo en «Ciclos y galpones» con la recepción de pollitos.")
        return None
    elegido = st.selectbox("Ciclo", ciclos, key=clave,
                           format_func=lambda c: f"Ciclo {c['numero']} · {c['ejercicio']} · desde "
                                                 f"{fecha_pantalla(c['fecha_inicio'])} · {c['estado'].capitalize()}")
    with con_bd() as con:
        return pr.obtener_ciclo(con, sesion, int(elegido["id"]), date.today())


# -------------------------------------------------------------------- resumen
def _resumen(sesion: Sesion) -> None:
    with con_bd() as con:
        r = pr.resumen_produccion(con, sesion, date.today())
    c1, c2, c3, c4 = st.columns(4)
    if r["ciclo"] is None:
        c1.metric("Ciclo en curso", "—")
    else:
        c = r["ciclo"]
        c1.metric("Ciclo en curso", f"C{c['numero']} · {c['ejercicio']}", help=f"Día {r['dia']} del ciclo")
        c2.metric("Aves vivas", _n(r["vivas"]))
        c3.metric("Mortalidad acumulada", pr.pct_texto(r["mortalidad"]), help=SEMAFORO[r["semaforo"]])
    c4.metric("Alertas sin atender", r["alertas_pendientes"])
    if r["alertas_pendientes"]:
        st.warning("Hay alertas sanitarias pendientes de decisión de la Gerencia General (CI-FIN-11 Paso 5).")


# -------------------------------------------------------- ciclos y galpones
def _tab_ciclos(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        edita = seguridad.tiene_permiso(con, sesion.rol, "produccion", "EDITAR")
        p = seguridad.contexto_desde_bd(con, hoy).parametros
        ejercicio = seguridad.contexto_desde_bd(con, hoy).ejercicio
        calendario = pr.calendario_ciclos(con, ejercicio)
        galpones = pr.listar_galpones(con, sesion, hoy)
        recepciones = pr.recepciones_pollitos_disponibles(con, sesion)
    st.subheader(f"Calendario del ejercicio {ejercicio}")
    for c in calendario:
        c["estado_txt"] = c["estado"].capitalize()
    st.dataframe(tabla(calendario, [("numero", "Ciclo"), ("inicio_programado", "Inicio programado"),
                                    ("cierre_programado", "Día 73 programado"), ("aves_programadas", "Aves"),
                                    ("inicio_real", "Recepción real"), ("cierre_real", "Día 73 real"),
                                    ("aves_iniciales", "Aves recibidas"), ("estado_txt", "Estado")],
                       fechas=("inicio_programado", "cierre_programado", "inicio_real", "cierre_real")),
                 hide_index=True, use_container_width=True)
    st.caption(f"POL-03 Art. 1: ciclo de {p['dias_ciclo']} días (el día de recepción es el día 1), nunca menos de "
               f"{p['dias_ciclo_minimo']}; {p['ciclos_por_ejercicio']} ciclos por ejercicio; lotes de hasta "
               f"{_n(p['lote_aves'])} aves y densidad máxima de {p['densidad_max_aves_m2']} aves/m².")
    st.subheader("Galpones")
    st.dataframe(tabla(galpones, [("codigo", "Código"), ("nombre", "Nombre"), ("superficie_m2", "Superficie (m²)"),
                                  ("capacidad", "Capacidad a densidad máxima"), ("lote_actual", "Lote en crianza"),
                                  ("aves_lote", "Aves del lote")]), hide_index=True, use_container_width=True)
    if edita:
        with st.expander("Registrar galpón"):
            with st.form("form_galpon", clear_on_submit=True):
                c1, c2, c3 = st.columns([1, 2, 1])
                codigo = c1.text_input("Código", placeholder="G1")
                nombre = c2.text_input("Nombre", placeholder="Galpón 1")
                superficie = c3.number_input("Superficie útil (m²)", min_value=0.0, step=5.0, format="%g")
                fotos = st.text_input("Enlace a las fotografías reales del galpón (QR «Vida Digna»)")
                if st.form_submit_button("Registrar galpón"):
                    try:
                        with con_bd() as con:
                            pr.registrar_galpon(con, sesion, codigo, nombre, decimal_de(superficie), fotos or None)
                        st.success("Galpón registrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if sesion.rol != "JF":
        return
    st.subheader("Apertura del ciclo (FIN-TRA-01)")
    if not recepciones:
        st.info("No hay recepciones de pollitos BB pendientes de asignar. La recepción (FIN-EGR-04) traslada el costo "
                "a 6.1.01.01 sufijo 01 el mismo día; luego se abre aquí el ciclo.")
        return
    rec = st.selectbox("Recepción de pollitos BB", recepciones, key="ap_rec",
                       format_func=lambda r: f"{fecha_pantalla(r['fecha'])} · {_n(r['aves'])} aves · {bs(r['valor'])}"
                                             f" · {r['proveedor'] or 'proveedor s/d'}")
    libres = [g for g in galpones if g["activo"] and not g["lote_actual"]]
    if not libres:
        st.warning("No hay galpones libres: registre o libere un galpón.")
        return
    asignacion = {}
    cols = st.columns(min(len(libres), 4))
    for i, g in enumerate(libres):
        n = cols[i % len(cols)].number_input(f"Aves en {g['codigo']} (cap. {_n(g['capacidad'])})", min_value=0,
                                             max_value=int(p["lote_aves"]), step=50, key=f"ap_g{g['id']}")
        if n:
            asignacion[int(g["id"])] = int(n)
    certificado = st.text_input("Certificado sanitario del proveedor", key="ap_cert")
    st.caption(f"Asignadas {_n(sum(asignacion.values()))} de {_n(rec['aves'])} aves.")
    if st.button("Abrir ciclo", type="primary"):
        try:
            with con_bd() as con:
                cid = pr.abrir_ciclo(con, sesion, int(rec["kardex_id"]), asignacion, certificado or None)
                c = pr.obtener_ciclo(con, sesion, cid)
            st.success(f"Ciclo {c['numero']} abierto: día 1 el {fecha_pantalla(c['fecha_inicio'])}, día 73 el "
                       f"{fecha_pantalla(c['fecha_cierre'])}.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)


# ----------------------------------------------------------- bitácora diaria
def _tab_bitacora(sesion: Sesion) -> None:
    c = _ciclo_elegido(sesion, "bit_ciclo")
    if c is None:
        return
    m = c["mortalidad"]
    st.markdown(f"Día {c['dia_actual']} · fase {pr.NOMBRE_FASE[c['fase_actual']].lower()} · mortalidad acumulada "
                f"**{pr.pct_texto(m['pct'])}** {chip(m['semaforo'])} ({_n(m['muertas'])} de {_n(m['aves_iniciales'])} "
                "aves)", unsafe_allow_html=True)
    lotes = [l for l in c["lotes"] if l["estado"] == "EN_CRIANZA"]
    if sesion.rol == "UT" and lotes:
        with st.form("form_bitacora", clear_on_submit=True):
            st.markdown("**Registrar el día (FIN-TRA-02)**")
            c1, c2, c3, c4 = st.columns(4)
            lote = c1.selectbox("Lote", lotes, format_func=lambda l: f"{l['codigo']} · {_n(l['vivas'])} vivas")
            fecha = c2.date_input("Fecha", date.today(), format="DD/MM/YYYY")
            alimento = c3.number_input("Alimento consumido (kg)", min_value=0.0, step=5.0, format="%g")
            muertas = c4.number_input("Aves muertas del día", min_value=0, step=1)
            causa = st.text_input("Causa presunta (obligatoria si hubo muertes)")
            novedad = st.text_input("Novedad sanitaria")
            tratamiento = st.text_input("Vacuna o tratamiento aplicado")
            a, b = st.columns(2)
            signos = a.checkbox("Signos de alerta (postración, secreción, diarrea inusual)")
            cumple = b.checkbox("Se cumplieron los puntos de control de CI-FIN-11", value=True)
            if st.form_submit_button("Registrar día", type="primary"):
                try:
                    with con_bd() as con:
                        r = pr.registrar_bitacora(con, sesion, int(lote["id"]), fecha, decimal_de(alimento), int(muertas),
                                                  causa_presunta=causa or None, novedad_sanitaria=novedad or None,
                                                  tratamiento=tratamiento or None, signos_alerta=signos,
                                                  cumple_ci_fin_11=cumple)
                    st.success(f"Día {r['dia']} ({pr.NOMBRE_FASE[r['fase']].lower()}): mortalidad acumulada "
                               f"{pr.pct_texto(r['pct'])} · {SEMAFORO[r['semaforo']]}.")
                    if r["alertas"]:
                        st.warning(f"Se reportaron {len(r['alertas'])} alerta(s) a la Gerencia General el mismo día "
                                   "(CI-FIN-11).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        filas = pr.listar_bitacora(con, sesion, int(c["id"]))
        revisiones = pr.listar_revisiones(con, sesion, int(c["id"]))
    for f in filas:
        f["fase_txt"] = pr.NOMBRE_FASE[f["fase"]]
        f["kg"] = _dec(f["alimento_kg"], 1)
        f["pct"] = pr.pct_texto(f["mortalidad_acum"])
        f["sem"] = SEMAFORO[f["semaforo"]]
    st.dataframe(tabla(filas, [("fecha", "Fecha"), ("dia_ciclo", "Día"), ("fase_txt", "Fase"), ("lote", "Lote"),
                               ("kg", "Alimento (kg)"), ("aves_muertas", "Muertas"), ("causa_presunta", "Causa presunta"),
                               ("novedad_sanitaria", "Novedad"), ("pct", "% acumulado"), ("sem", "Semáforo")],
                       fechas=("fecha",)), hide_index=True, use_container_width=True)
    if sesion.rol == "GG" and filas:
        st.markdown("**Revisión semanal de la Gerencia General**")
        revisadas = {r["semana"] for r in revisiones}
        semanas = sorted({(int(f["dia_ciclo"]) - 1) // 7 + 1 for f in filas} - revisadas)
        if semanas:
            a, b, d = st.columns([1, 3, 1])
            semana = a.selectbox("Semana", semanas, key="rev_sem")
            obs = b.text_input("Observación", key="rev_obs")
            if d.button("Revisar semana"):
                try:
                    with con_bd() as con:
                        pr.revisar_semana(con, sesion, int(c["id"]), int(semana), obs or None)
                    st.success("Revisión registrada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        else:
            st.caption("Todas las semanas con bitácora están revisadas.")


# ------------------------------------------------------------------ alertas
def _tab_alertas(sesion: Sesion) -> None:
    c = _ciclo_elegido(sesion, "al_ciclo")
    if c is None:
        return
    lotes = [l for l in c["lotes"] if l["estado"] == "EN_CRIANZA"]
    if sesion.rol == "UT" and lotes:
        with st.form("form_alerta", clear_on_submit=True):
            st.markdown("**Reporte de alerta a la Gerencia (CI-FIN-11 Paso 4: el mismo día)**")
            c1, c2, c3 = st.columns(3)
            lote = c1.selectbox("Lote", lotes, format_func=lambda l: l["codigo"])
            deteccion = c2.date_input("Fecha de detección", date.today(), format="DD/MM/YYYY")
            tipo = c3.selectbox("Tipo", ["SIGNOS_CLINICOS", "OTRA"], format_func=pr.TIPOS_ALERTA.get)
            descripcion = st.text_area("Descripción de la señal de alerta")
            demora = st.text_input("Causa de la demora (solo si no se reporta el mismo día)")
            if st.form_submit_button("Reportar alerta", type="primary"):
                try:
                    with con_bd() as con:
                        pr.reportar_alerta(con, sesion, int(lote["id"]), deteccion, tipo, descripcion, demora or None)
                    st.success("Alerta reportada a la Gerencia General.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        alertas = pr.listar_alertas(con, sesion, int(c["id"]))
    for a in alertas:
        a["tipo_txt"] = pr.TIPOS_ALERTA[a["tipo"]]
        a["dia_txt"] = "Sí" if a["mismo_dia"] else "No"
        a["origen_txt"] = "Automática" if a["origen"] == "AUTOMATICA" else "Unidad Técnica"
        a["decision_txt"] = f"{pr.MEDIDAS[a['medida']]}: {a['decision']}" if a["medida"] else "Pendiente"
    st.dataframe(tabla(alertas, [("fecha_deteccion", "Detección"), ("tipo_txt", "Tipo"), ("origen_txt", "Origen"),
                                 ("descripcion", "Descripción"), ("dia_txt", "¿Mismo día?"),
                                 ("decision_txt", "Decisión de la Gerencia")], fechas=("fecha_deteccion",)),
                 hide_index=True, use_container_width=True)
    pendientes = [a for a in alertas if not a["atendida_por"]]
    if sesion.rol == "GG" and pendientes:
        st.markdown("**Decisión de la Gerencia (CI-FIN-11 Paso 5)**")
        a = st.selectbox("Alerta", pendientes, key="al_sel",
                         format_func=lambda x: f"{fecha_pantalla(x['fecha_deteccion'])} · {x['tipo_txt']}")
        c1, c2 = st.columns([1, 2])
        medida = c1.selectbox("Medida", list(pr.MEDIDAS), format_func=pr.MEDIDAS.get, key="al_med")
        decision = c2.text_input("Decisión y constancia", key="al_dec")
        if st.button("Registrar decisión", type="primary"):
            try:
                with con_bd() as con:
                    pr.atender_alerta(con, sesion, int(a["id"]), medida, decision)
                st.success("Decisión registrada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ------------------------------------------------------- bajas por mortalidad
def _tab_bajas(sesion: Sesion) -> None:
    c = _ciclo_elegido(sesion, "bj_ciclo")
    if c is None:
        return
    with con_bd() as con:
        pendientes = pr.bitacoras_sin_baja(con, int(c["id"]))
        bajas = pr.listar_bajas(con, sesion, int(c["id"]))
        p = seguridad.contexto_desde_bd(con, c["fecha_inicio"]).parametros
    st.caption(f"CI-FIN-05 (regla de oro): se clasifica con el % ACUMULADO del ciclo. Hasta "
               f"{pr.pct_texto(p['mortalidad_anormal'])} es normal (sufijo 40, absorbida en el costo del lote); por "
               "encima es anormal (7.2.01.04, gasto no operativo) con informe veterinario y autorización de la GG.")
    if sesion.rol == "JF" and pendientes:
        b = pendientes[0]
        st.markdown(f"**Siguiente baja (FIN-TRA-04):** {fecha_pantalla(b['fecha'])} · lote {b['lote']} · "
                    f"{b['aves_muertas']} aves · {pr.pct_texto(b['mortalidad_acum'])} acumulado")
        anormal = Decimal(b["mortalidad_acum"]) > Decimal(str(p["mortalidad_anormal"]))
        informe = st.text_input("Informe veterinario (obligatorio si es anormal)", key="bj_inf") if anormal else None
        if st.button("Registrar baja", type="primary"):
            try:
                with con_bd() as con:
                    r = pr.registrar_baja(con, sesion, int(b["id"]), informe or None)
                st.success(f"Baja {r['clasificacion'].lower()}: {bs(r['monto'])} ({bs(r['costo_por_ave'])} por ave). "
                           + ("Asiento registrado." if r["asiento_id"] else "Pendiente de autorización de la GG."))
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    for b in bajas:
        b["pct"] = pr.pct_texto(b["pct_acumulado"])
        b["destino"] = "6.1.01.01-40" if b["clasificacion"] == "NORMAL" else "7.2.01.04"
        b["estado_txt"] = b["estado"].replace("_", " ").capitalize()
    st.dataframe(tabla(bajas, [("fecha", "Fecha"), ("lote", "Lote"), ("aves", "Aves"), ("pct", "% acumulado"),
                               ("clasificacion", "Clasificación"), ("costo_por_ave", "Costo por ave"),
                               ("monto", "Monto"), ("destino", "Cuenta"), ("informe_veterinario", "Informe"),
                               ("estado_txt", "Estado"), ("asiento_id", "Asiento")],
                       montos=("costo_por_ave", "monto"), fechas=("fecha",)), hide_index=True, use_container_width=True)
    por_autorizar = [b for b in bajas if b["estado"] == "PENDIENTE_AUTORIZACION"]
    if sesion.rol == "GG" and por_autorizar:
        b = st.selectbox("Baja anormal por autorizar", por_autorizar, key="bj_aut",
                         format_func=lambda x: f"{fecha_pantalla(x['fecha'])} · {x['aves']} aves · informe "
                                               f"{x['informe_veterinario']}")
        if st.button("Autorizar baja anormal", type="primary"):
            try:
                with con_bd() as con:
                    pr.autorizar_baja_anormal(con, sesion, int(b["id"]))
                st.success("Baja autorizada: Finanzas la contabiliza en 7.2.01.04.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    autorizadas = [b for b in bajas if b["estado"] == "AUTORIZADA"]
    if sesion.rol == "JF" and autorizadas:
        b = st.selectbox("Baja anormal autorizada", autorizadas, key="bj_cont",
                         format_func=lambda x: f"{fecha_pantalla(x['fecha'])} · {x['aves']} aves · {bs(x['monto'])}")
        if st.button("Contabilizar en 7.2.01.04"):
            try:
                with con_bd() as con:
                    aid = pr.contabilizar_baja(con, sesion, int(b["id"]))
                st.success(f"Asiento n.º {aid} registrado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if c["costo_centro"]:
        st.caption("Costo acumulado del centro 6.1.01.01 en el ciclo, por sufijo: " +
                   " · ".join(f"{s}: {bs(v)}" for s, v in c["costo_centro"].items()))


# ---------------------------------------------------- disposición y peso
def _tab_disposicion(sesion: Sesion) -> None:
    c = _ciclo_elegido(sesion, "dp_ciclo")
    if c is None:
        return
    with con_bd() as con:
        disposiciones = pr.listar_disposiciones(con, sesion, int(c["id"]))
        muestreos = pr.listar_muestreos(con, sesion, int(c["id"]))
        p = seguridad.contexto_desde_bd(con, c["fecha_inicio"]).parametros
    st.subheader("Disposición sanitaria (FIN-TRA-11)")
    resumen = [{"lote": l["codigo"], "muertas": l["muertas"], "dispuestas": l["dispuestas"],
                "pendientes": l["muertas"] - l["dispuestas"]} for l in c["lotes"]]
    st.dataframe(tabla(resumen, [("lote", "Lote"), ("muertas", "Aves muertas"), ("dispuestas", "Dispuestas"),
                                 ("pendientes", "Por disponer")]), hide_index=True, use_container_width=True)
    con_pendientes = [l for l in c["lotes"] if l["muertas"] > l["dispuestas"]]
    if sesion.rol == "UT" and con_pendientes:
        with st.form("form_disp", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            lote = c1.selectbox("Lote", con_pendientes, format_func=lambda l: l["codigo"])
            fecha = c2.date_input("Fecha", date.today(), format="DD/MM/YYYY")
            aves = c3.number_input("Aves dispuestas", min_value=1, step=1)
            metodo = c4.selectbox("Método", list(pr.METODOS_DISPOSICION), format_func=pr.METODOS_DISPOSICION.get)
            a, b = st.columns(2)
            responsable = a.text_input("Responsable", value=sesion.nombre_completo)
            detalle = b.text_input("Detalle del método (si es «Otro»)")
            if st.form_submit_button("Registrar disposición"):
                try:
                    with con_bd() as con:
                        pr.registrar_disposicion(con, sesion, int(lote["id"]), fecha, int(aves), metodo, responsable,
                                                 detalle or None)
                    st.success("Disposición registrada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for d in disposiciones:
        d["metodo_txt"] = pr.METODOS_DISPOSICION[d["metodo"]]
    st.dataframe(tabla(disposiciones, [("fecha", "Fecha"), ("lote", "Lote"), ("aves", "Aves"),
                                       ("metodo_txt", "Método"), ("responsable", "Responsable"),
                                       ("supervisado_por", "Supervisado por")], fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    sin_visto = [d for d in disposiciones if not d["supervisado_por"]]
    if sesion.rol == "GG" and sin_visto:
        d = st.selectbox("Disposición por supervisar", sin_visto, key="dp_sup",
                         format_func=lambda x: f"{fecha_pantalla(x['fecha'])} · {x['lote']} · {x['aves']} aves")
        if st.button("Dar visto (Gerencia General)"):
            try:
                with con_bd() as con:
                    pr.supervisar_disposicion(con, sesion, int(d["id"]))
                st.success("Visto registrado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)

    st.subheader("Muestreo semanal de peso (POL-03 Art. 2)")
    cumplimiento = [{"lote": l["codigo"], "esperadas": l["muestreo"]["esperadas"],
                     "registradas": l["muestreo"]["registradas"],
                     "faltantes": ", ".join(str(s) for s in l["muestreo"]["faltantes"]) or "—",
                     "cumpl": pr.pct_texto(l["muestreo"]["cumplimiento"])} for l in c["lotes"]]
    st.dataframe(tabla(cumplimiento, [("lote", "Lote"), ("esperadas", "Semanas cumplidas"),
                                      ("registradas", "Con muestreo"), ("faltantes", "Semanas sin muestreo"),
                                      ("cumpl", "Cumplimiento")]), hide_index=True, use_container_width=True)
    lotes = [l for l in c["lotes"] if l["estado"] == "EN_CRIANZA"]
    if sesion.rol == "UT" and lotes:
        with st.form("form_peso", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            lote = c1.selectbox("Lote", lotes, format_func=lambda l: l["codigo"])
            fecha = c2.date_input("Fecha", date.today(), format="DD/MM/YYYY")
            aves = c3.number_input("Aves de la muestra", min_value=1, step=1, value=20)
            peso = c4.number_input("Peso total de la muestra (kg)", min_value=0.0, step=0.5, format="%g")
            if st.form_submit_button("Registrar muestreo"):
                try:
                    with con_bd() as con:
                        r = pr.registrar_muestreo(con, sesion, int(lote["id"]), fecha, int(aves), decimal_de(peso))
                    st.success(f"Semana {r['semana']}: peso promedio {_dec(r['peso_promedio'], 3)} kg.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for m in muestreos:
        m["prom"] = _dec(m["peso_promedio"], 3)
        m["total"] = _dec(m["peso_total"], 1)
    st.dataframe(tabla(muestreos, [("semana", "Semana"), ("fecha", "Fecha"), ("dia_ciclo", "Día"), ("lote", "Lote"),
                                   ("aves_muestra", "Aves"), ("total", "Peso total (kg)"),
                                   ("prom", "Peso promedio (kg)")], fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    st.caption(f"Peso de referencia al día {p['dias_ciclo']}: {_dec(p['rendimiento_kg_ave'], 1)} kg por ave (POA A7).")


# -------------------------------------------------------------------- faenado
def _tab_faenado(sesion: Sesion) -> None:
    c = _ciclo_elegido(sesion, "fa_ciclo")
    if c is None:
        return
    with con_bd() as con:
        faenados = pr.listar_faenados(con, sesion, int(c["id"]))
        p = seguridad.contexto_desde_bd(con, c["fecha_inicio"]).parametros
    minimo = date.fromisoformat(c["fecha_inicio"]) + timedelta(days=int(p["dias_ciclo_minimo"]) - 1)
    st.caption(f"Faenado habilitado desde el día {p['dias_ciclo_minimo']} ({fecha_pantalla(minimo)}); día "
               f"{p['dias_ciclo']}: {fecha_pantalla(c['fecha_cierre'])}. PMF: Bueno < "
               f"{pr.pct_texto(p['pmf_umbral_bueno'])}, Malo > {pr.pct_texto(p['pmf_umbral_malo'])}. ICA: Bueno < "
               f"{_dec(p['ica_umbral_bueno'])}, Malo > {_dec(p['ica_umbral_malo'])}.")
    lotes = [l for l in c["lotes"] if l["estado"] == "EN_CRIANZA"]
    if sesion.rol == "GG" and lotes:
        with st.expander("Faenado anticipado por indicación veterinaria (POL-03 Art. 1)"):
            lote = st.selectbox("Lote", lotes, format_func=lambda l: l["codigo"], key="fa_ant_l")
            informe = st.text_input("Informe veterinario que funda el riesgo sanitario", key="fa_ant_i")
            if st.button("Autorizar faenado anticipado"):
                try:
                    with con_bd() as con:
                        pr.autorizar_faenado_anticipado(con, sesion, int(lote["id"]), informe)
                    st.success("Faenado anticipado autorizado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if sesion.rol == "UT" and lotes:
        with st.form("form_faenado", clear_on_submit=True):
            st.markdown("**Registro de Faenado (FIN-TRA-05)**")
            c1, c2, c3 = st.columns(3)
            lote = c1.selectbox("Lote", lotes, format_func=lambda l: f"{l['codigo']} · {_n(l['vivas'])} vivas")
            fecha = c2.date_input("Fecha de faenado", date.today(), format="DD/MM/YYYY")
            aves = c3.number_input("Aves faenadas", min_value=1, step=1, value=1000)
            c1, c2, c3 = st.columns(3)
            vivo = c1.number_input("Peso vivo total (kg)", min_value=0.0, step=10.0, format="%g")
            canal = c2.number_input("Peso canal total (kg)", min_value=0.0, step=10.0, format="%g")
            separadas = c3.number_input("Carcasas separadas (no comercializables)", min_value=0, step=1)
            st.markdown("Puntos de control de inocuidad y cadena de frío (CI-FIN-12)")
            checklist = {k: st.checkbox(v, key=f"fa_{k}") for k, v in pr.PUNTOS_CI_FIN_12.items()}
            a, b = st.columns(2)
            supervisor = a.text_input("Supervisor del personal eventual")
            faltante = b.text_input("Explicación del faltante de aves (si las faenadas son menos que las vivas)")
            observacion = st.text_input("Observación (obligatoria si se faena después del día 73)")
            if st.form_submit_button("Registrar faenado", type="primary"):
                try:
                    with con_bd() as con:
                        r = pr.registrar_faenado(con, sesion, int(lote["id"]), fecha, int(aves), decimal_de(vivo),
                                                 decimal_de(canal), checklist, supervisor, carcasas_separadas=int(separadas),
                                                 explicacion_faltante=faltante or None, observacion=observacion or None)
                    st.success(f"Día {r['dia']}: merma {_dec(r['merma'], 1)} kg · PMF {pr.pct_texto(r['pmf'])} "
                               f"({SEMAFORO[r['semaforo_pmf']]}) · ICA {_dec(r['ica'])} ({SEMAFORO[r['semaforo_ica']]}).")
                    if r["alertas"]:
                        st.warning("Se reportó a la Gerencia General: PMF en rojo o punto de control incumplido.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for f in faenados:
        f["vivo"] = _dec(f["peso_vivo"], 1)
        f["canal"] = _dec(f["peso_canal"], 1)
        f["merma_txt"] = _dec(f["merma"], 1)
        f["pmf_txt"] = f"{pr.pct_texto(f['pmf'])} · {SEMAFORO[f['semaforo_pmf']]}"
        f["ica_txt"] = f"{_dec(f['ica'])} · {SEMAFORO[f['semaforo_ica']]}"
        f["ci12"] = "Cumple" if f["cumple_ci_fin_12"] else "No cumple"
    st.dataframe(tabla(faenados, [("lote", "Lote"), ("fecha", "Fecha"), ("aves_faenadas", "Aves"),
                                  ("vivo", "Peso vivo (kg)"), ("canal", "Peso canal (kg)"), ("merma_txt", "Merma (kg)"),
                                  ("pmf_txt", "PMF"), ("ica_txt", "ICA"), ("ci12", "CI-FIN-12")], fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    if any(f["faltante_aves"] for f in faenados):
        st.warning("Faltantes de aves en el faenado: " + "; ".join(
            f"{f['lote']}: {f['faltante_aves']} ({f['explicacion_faltante']})" for f in faenados if f["faltante_aves"]))


# --------------------------------------------------------- conciliación y QR
def _tab_conciliacion(sesion: Sesion) -> None:
    c = _ciclo_elegido(sesion, "cq_ciclo")
    if c is None:
        return
    r = c["conciliacion"]
    st.subheader("Conciliación de aves (FIN-TRA-06, riesgo 39)")
    filas = [{"c": "Aves ingresadas (FIN-TRA-01)", "n": r["ingresadas"]},
             {"c": f"Mortalidad normal ({pr.pct_texto(r['pct_normal'])})", "n": r["muertas_normal"]},
             {"c": f"Mortalidad anormal ({pr.pct_texto(r['pct_anormal'])})", "n": r["muertas_anormal"]},
             {"c": "Muertas sin Registro de Baja", "n": r["muertas_sin_baja"]},
             {"c": "Aves faenadas (FIN-TRA-05)", "n": r["faenadas"]},
             {"c": "Aves en crianza", "n": r["en_crianza"]},
             {"c": "Faltante", "n": r["faltante"]}]
    for f in filas:
        f["n"] = _n(f["n"])
    st.dataframe(tabla(filas, [("c", "Concepto"), ("n", "Aves")]), hide_index=True, use_container_width=True)
    st.markdown(f"Estado del ciclo: {chip(c['estado'])} · " +
                ("la conciliación **cuadra**." if r["cuadra"] else "la conciliación aún **no cuadra**."),
                unsafe_allow_html=True)
    if sesion.rol == "GG" and r["faltante"] > 0 and not c["faltante_autorizado_por"] and c["estado"] != "CONCLUIDO":
        motivo = st.text_input("Toma de conocimiento del faltante (motivo)", key="cq_falt")
        if st.button("Registrar toma de conocimiento"):
            try:
                with con_bd() as con:
                    pr.autorizar_faltante(con, sesion, int(c["id"]), motivo)
                st.success("Registrado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if sesion.rol == "JF" and c["estado"] == "FAENADO":
        if st.button("Concluir ciclo operativo", type="primary"):
            try:
                with con_bd() as con:
                    pr.concluir_ciclo(con, sesion, int(c["id"]))
                st.success("Ciclo concluido. El traslado del costo a Productos Terminados es de la Fase 7.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    st.subheader("Trazabilidad — código QR «Vida Digna» (POL-03 Art. 3)")
    faenados = [l for l in c["lotes"] if l["estado"] == "FAENADO"]
    if not faenados:
        st.info("El QR de un lote se genera con su faenado registrado (fecha y días de vida respaldados).")
        return
    lote = st.selectbox("Lote", faenados, format_func=lambda l: l["codigo"], key="cq_lote")
    try:
        with con_bd() as con:
            datos = pr.datos_trazabilidad(con, int(lote["id"]))
        a, b = st.columns([1, 2])
        a.markdown(pr.qr_svg(datos["url"]).split("?>", 1)[-1], unsafe_allow_html=True)
        b.markdown(f"**{datos['leyenda']}**  \nLote {datos['lote']} · faenado el {fecha_pantalla(datos['fecha_faenado'])}")
        b.code(datos["url"], language=None)
        b.caption("Insumos de alimentación: " + (", ".join(datos["insumos_alimentacion"]) or "sin registros") +
                  f" · alimento {_dec(datos['alimento_kg'], 1)} kg · mortalidad {pr.pct_texto(datos['mortalidad_acumulada'])}"
                  f" · {datos['respaldo']['bitacora_dias']} días de bitácora.")
        b.download_button("Descargar página de trazabilidad (HTML)", pr.pagina_trazabilidad_html(datos),
                          file_name=f"vida_digna_{datos['lote']}.html", mime="text/html")
    except ErrorPollolandia as exc:
        mostrar_error(exc)


# -------------------------------------------------------------------- formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Formatos del Ciclo 3 (transformación) en PDF, con el bloque de firmas.")
    c = _ciclo_elegido(sesion, "fm_ciclo")
    if c is None:
        return
    ctx = pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo, cargo=CARGO_ROL.get(sesion.rol, sesion.rol),
                       ciclo=f"{c['numero']} ({c['ejercicio']})")
    opciones = {"FIN-TRA-01": "Ficha de Apertura de Ciclo Productivo", "FIN-TRA-02": "Bitácora Diaria de Ciclo",
                "FIN-TRA-04": "Registro de Baja por Mortalidad", "FIN-TRA-05": "Registro de Faenado",
                "FIN-TRA-11": "Bitácora de Disposición Sanitaria", "CI-FIN-11": "Reporte de Alerta Sanitaria",
                "QR-VD": "Código QR «Vida Digna» del lote"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda k: f"{k} — {opciones[k]}", key="fm_cod")
    try:
        with con_bd() as con:
            cid = int(c["id"])
            datos: bytes | None = None
            if codigo == "FIN-TRA-01":
                datos = pdf.fin_tra_01(c, ctx)
            elif codigo == "FIN-TRA-02":
                datos = pdf.fin_tra_02(c, pr.listar_bitacora(con, sesion, cid), ctx)
            elif codigo == "FIN-TRA-04":
                datos = pdf.fin_tra_04(c, pr.listar_bajas(con, sesion, cid), ctx)
            elif codigo == "FIN-TRA-05":
                datos = pdf.fin_tra_05(c, pr.listar_faenados(con, sesion, cid), ctx)
            elif codigo == "FIN-TRA-11":
                datos = pdf.fin_tra_11(c, pr.listar_disposiciones(con, sesion, cid), ctx)
            elif codigo == "CI-FIN-11":
                datos = pdf.alerta_ci_fin_11(c, pr.listar_alertas(con, sesion, cid), ctx)
            else:
                faenados = [l for l in c["lotes"] if l["estado"] == "FAENADO"]
                if faenados:
                    lote = st.selectbox("Lote", faenados, format_func=lambda l: l["codigo"], key="fm_lote")
                    datos = pdf.etiqueta_qr(pr.datos_trazabilidad(con, int(lote["id"])), ctx)
        if datos is None:
            st.info("Aún no hay lotes faenados para este formato.")
        else:
            st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                               mime="application/pdf", type="primary")
    except (ErrorPollolandia, ValueError) as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 4 — Operativo y trazabilidad del ciclo."""
    st.header("Producción y trazabilidad · Ciclo 3")
    _resumen(sesion)
    pestañas = st.tabs(["Ciclos y galpones", "Bitácora diaria", "Alertas sanitarias", "Bajas por mortalidad",
                        "Disposición y peso", "Faenado", "Conciliación y QR", "Formatos"])
    funciones = (_tab_ciclos, _tab_bitacora, _tab_alertas, _tab_bajas, _tab_disposicion, _tab_faenado,
                 _tab_conciliacion, _tab_formatos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
