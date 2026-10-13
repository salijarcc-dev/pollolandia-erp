"""Módulo 6 — Activos fijos y CAPEX (Fase 8).

Pestañas: Libro de activos, Plan CAPEX, Activación, Depreciación, Bajas,
Verificación física, Informe semestral y Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. La Gerencia General elabora el
plan, autoriza activaciones y emite actas de baja; la Unidad de Finanzas
analiza el ROA, registra activos, depreciaciones y bajas; el Consejo de
Administración aprueba el plan; la Asamblea lo ratifica; el Consejo de
Vigilancia verifica bajas y existencias físicas.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_activos as pdf
from servicios import activos
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
ESTADO_PLAN = {"BORRADOR": "Borrador", "PRESENTADO": "Presentado al CA", "APROBADO": "Aprobado por el CA",
               "RATIFICADO": "Ratificado por la Asamblea"}
ESTADO_ACTIVO = {"VIGENTE": "Vigente", "EN_BAJA": "Acta de baja en trámite", "BAJA": "Dado de baja"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def _ctx(sesion: Sesion, detalle: str = "") -> pdf.Contexto:
    return pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                        cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo=detalle)


def _descargar(codigo: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                       mime="application/pdf", key=clave)


def _ejercicio_actual() -> int:
    hoy = date.today()
    return hoy.year if hoy.month >= 7 else hoy.year - 1


def _resumen(sesion: Sesion) -> None:
    with con_bd() as con:
        r = activos.resumen_activos(con, sesion, date.today())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Activos vigentes", r["activos"])
    c2.metric("Costo histórico", bs(r["costo"]))
    c3.metric("Depreciación acumulada", bs(r["acumulada"]))
    c4.metric("Valor neto en libros", bs(r["valor_neto"]))
    plan = r["plan"]
    if plan:
        st.markdown(f"Plan CAPEX {plan['ejercicio']}: {chip(plan['estado'])} · total {bs(plan['total'])} · ejecutado "
                    f"{bs(plan['ejecutado'])} ({activos.pct_texto(plan['ejecucion_pct'])})", unsafe_allow_html=True)
    for alerta in r["alertas"]:
        st.warning(alerta)


# ------------------------------------------------------------- libro de activos
def _tab_libro(sesion: Sesion) -> None:
    hoy = date.today()
    if sesion.rol == "JF":
        with con_bd() as con:
            hay_apertura = activos.hay_fichas_apertura(con)
        if not hay_apertura:
            st.info("Abra las fichas FIN-TRA-09 del activo no corriente del balance de apertura (1-jul-2026). "
                    "No genera asiento: el costo ya está en la apertura.")
            if st.button("Abrir fichas del balance de apertura", type="primary"):
                try:
                    with con_bd() as con:
                        creadas = activos.registrar_activos_apertura(con, sesion)
                    st.success(f"{len(creadas)} fichas abiertas: " + ", ".join(c["codigo_interno"] for c in creadas))
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    corte = st.date_input("Situación al", hoy, format="DD/MM/YYYY", key="lb_corte")
    with con_bd() as con:
        libro = activos.libro_activos(con, sesion, corte)
        conciliacion = activos.conciliar_libro(con, sesion, corte)
    if not libro:
        st.info("Aún no hay activos registrados.")
        return
    for a in libro:
        a["tasa_txt"] = activos.pct_texto(a["tasa"])
        a["estado_txt"] = ESTADO_ACTIVO[a["estado"]]
        a["destino_txt"] = f"{a['destino'].capitalize()} · {a['destino_cuota']}"
        a["verif"] = fecha_pantalla(a["ultima_verificacion"]["fecha"]) if a["ultima_verificacion"] else "—"
    st.markdown("**Libro de inventario de activos fijos**")
    st.dataframe(tabla(libro, [("codigo_interno", "Código"), ("descripcion", "Descripción"),
                               ("fecha_activacion", "Activación"), ("costo", "Costo"), ("tasa_txt", "Tasa anual"),
                               ("acumulada", "Dep. acumulada"), ("valor_neto", "Valor neto"),
                               ("destino_txt", "Destino de la cuota"), ("verif", "Última verificación"),
                               ("estado_txt", "Estado")],
                         montos=("costo", "acumulada", "valor_neto"), fechas=("fecha_activacion",)),
                 hide_index=True, use_container_width=True)
    elegido = st.selectbox("Ficha individual (FIN-TRA-09)", libro, key="lb_ficha",
                           format_func=lambda a: f"{a['codigo_interno']} — {a['descripcion']}")
    with con_bd() as con:
        ficha = activos.ficha_activo(con, sesion, int(elegido["id"]), corte)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Costo", bs(ficha["costo"]))
    c2.metric("Vida útil / tasa", f"{ficha['vida_util'] or '—'} años · {activos.pct_texto(ficha['tasa'])}"
              if ficha["vida_util"] else "—", help=None if ficha["vida_util"] else "No se deprecia (PC-06)")
    c3.metric("Cuota anual", bs(ficha["cuota_anual"]), help=f"Cuota mensual {bs(ficha['cuota_mensual'])}")
    c4.metric("Valor neto", bs(ficha["valor_neto"]), help=f"Acumulada {bs(ficha['acumulada'])}")
    st.markdown(f"{chip(ficha['estado'])} · {ficha['categoria_nombre']} · cuenta {ficha['cuenta_activo']} · "
                f"parámetro `{ficha['clave_tasa']}` · {ficha['ubicacion']}", unsafe_allow_html=True)
    if ficha["cuotas"]:
        st.dataframe(tabla(ficha["cuotas"], [("periodo", "Período"), ("fecha", "Fecha"), ("cuota", "Cuota"),
                                             ("acumulada", "Acumulada"), ("valor_neto", "Valor neto")],
                           montos=("cuota", "acumulada", "valor_neto"), fechas=("fecha",)),
                     hide_index=True, use_container_width=True, height=220)
    _descargar("FIN-TRA-09", pdf.fin_tra_09(ficha, _ctx(sesion)), "pdf_ficha")
    st.markdown("**Conciliación libro de inventario ↔ mayor**")
    st.dataframe(tabla([c for c in conciliacion if c["fichas"] or c["mayor"]],
                       [("cuenta", "Cuenta"), ("concepto", "Concepto"), ("fichas", "Fichas"), ("libro", "Libro"),
                        ("mayor", "Mayor"), ("diferencia", "Diferencia")], montos=("libro", "mayor", "diferencia")),
                 hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ plan CAPEX
def _tab_plan(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        planes = activos.listar_planes(con, sesion)
    if sesion.rol == "GG":
        with st.expander("Abrir el Plan CAPEX de un ejercicio (FIN-TES-01)", expanded=not planes):
            with st.form("form_plan"):
                c1, c2, c3 = st.columns(3)
                ejercicio = c1.number_input("Ejercicio", min_value=2026, max_value=2030, value=_ejercicio_actual() + 1,
                                            step=1)
                fecha = c2.date_input("Fecha de elaboración", hoy, format="DD/MM/YYYY")
                importar = c3.checkbox("Importar el presupuesto D.5", value=True)
                if st.form_submit_button("Abrir plan", type="primary"):
                    try:
                        with con_bd() as con:
                            r = activos.crear_plan(con, sesion, int(ejercicio), fecha, importar_d5=importar)
                        st.success(f"Plan abierto con {len(r['lineas'])} línea(s) del D.5. Plazo "
                                   f"{fecha_pantalla(r['fecha_limite'])}" + (" — fuera de plazo." if r["fuera_de_plazo"]
                                                                           else "."))
                        st.rerun()
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if not planes:
        st.info("No hay planes CAPEX registrados.")
        return
    plan_sel = st.selectbox("Plan", planes, key="pl_sel",
                            format_func=lambda p: f"Ejercicio {p['ejercicio']} · {ESTADO_PLAN[p['estado']]}")
    with con_bd() as con:
        plan = activos.detalle_plan(con, sesion, int(plan_sel["id"]))
    pid = int(plan["id"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Inversión del plan", bs(plan["total"]),
              help=f"Prevista {bs(plan['total_previsto'])} · no prevista {bs(plan['total_no_previsto'])}")
    c2.metric("Depreciación anual adicional", bs(plan["depreciacion_anual"]),
              help=f"Mensual {bs(plan['depreciacion_mensual'])}")
    c3.metric("Impacto en el costo unitario", f"Bs {str(plan['bs_por_kg']).replace('.', ',')}/kg")
    c4.metric("Ejecutado", bs(plan["ejecutado"]), help=activos.pct_texto(plan["ejecucion_pct"]))
    st.markdown(f"Estado: {chip(plan['estado'])} · elaborado el {fecha_pantalla(plan['fecha_elaboracion'])} · plazo "
                f"{fecha_pantalla(plan['fecha_limite'])}" + (" · **fuera de plazo**" if plan["fuera_de_plazo"] else "")
                + " · fuente: 100 % recursos propios", unsafe_allow_html=True)
    if plan["observacion_ca"]:
        st.warning(f"Observación del CA: {plan['observacion_ca']}")
    filas = []
    for l in plan["lineas"]:
        filas.append({**l, "tipo": "Prevista" if l["previsto"] else "No prevista (CA)",
                      "dep": l["impacto"]["anual"], "kg": str(l["impacto"]["bs_por_kg"]).replace(".", ","),
                      "destino": l["impacto"]["destino"], "ciclo_txt": f"Ciclo {l['ciclo']}"})
    st.dataframe(tabla(filas, [("id", "N°"), ("activo", "Activo"), ("categoria_nombre", "Categoría PC-06"),
                               ("costo", "Costo estimado"), ("proveedor_referencia", "Proveedor referencial"),
                               ("ciclo_txt", "Ciclo"), ("fecha_estimada", "Fecha prevista"), ("dep", "Dep. anual"),
                               ("kg", "Bs/kg"), ("destino", "Destino"), ("tipo", "Tipo"), ("ejecutado", "Ejecutado"),
                               ("estado", "Estado")],
                       montos=("costo", "dep", "ejecutado"), fechas=("fecha_estimada",)),
                 hide_index=True, use_container_width=True)
    categorias = list(activos.CATEGORIAS.values())
    if sesion.rol == "GG" and plan["estado"] == "BORRADOR":
        with st.form("form_linea", clear_on_submit=True):
            st.markdown("**Agregar un activo previsto**")
            c1, c2, c3 = st.columns([3, 2, 1])
            activo = c1.text_input("Activo a adquirir o construir")
            cat = c2.selectbox("Categoría (PC-06)", categorias, format_func=lambda c: c.nombre)
            costo = c3.number_input("Costo estimado (Bs)", min_value=0.0, step=100.0, format="%.2f")
            c4, c5, c6 = st.columns([2, 1, 1])
            proveedor = c4.text_input("Proveedor referencial")
            ciclo = c5.number_input("Ciclo", min_value=1, max_value=12, value=5, step=1)
            fecha = c6.date_input("Fecha prevista", hoy, format="DD/MM/YYYY")
            justificacion = st.text_area("Justificación técnica (capacidad productiva o reducción de costos)")
            if st.form_submit_button("Agregar al plan"):
                try:
                    with con_bd() as con:
                        activos.agregar_linea(con, sesion, pid, activo, cat.clave, decimal_de(costo), justificacion,
                                              int(ciclo), fecha, proveedor_referencia=proveedor or None)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        vigentes = [l for l in plan["lineas"] if l["estado"] == "VIGENTE"]
        if vigentes:
            c1, c2, c3 = st.columns([3, 3, 1])
            quitar = c1.selectbox("Retirar línea", vigentes, format_func=lambda l: f"{l['id']} · {l['activo']}",
                                  key="pl_quitar")
            motivo = c2.text_input("Motivo del retiro", key="pl_motivo")
            c3.write("")
            if c3.button("Retirar"):
                try:
                    with con_bd() as con:
                        activos.retirar_linea(con, sesion, int(quitar["id"]), motivo)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    # FIN-TES-02: análisis de la Unidad de Finanzas
    st.subheader("ROA proyectado y compatibilidad con el SME (FIN-TES-02)")
    if plan["roa"] is not None:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ROA con la inversión", activos.pct_texto(plan["roa"]),
                  help=f"Utilidad {bs(plan['utilidad_neta'])} ÷ activos {bs(plan['activos_totales'] + plan['inversion_total'])}")
        c2.markdown(f"Semáforo<br>{chip(plan['roa_semaforo'])}", unsafe_allow_html=True)
        c3.metric("Saldo tras la inversión", bs(plan["saldo_post_inversion"]), help=f"SME {bs(plan['sme'])}")
        c4.markdown("Compatible con el SME<br>" + chip("VERDE" if plan["compatible_sme"] else "ROJO"),
                    unsafe_allow_html=True)
        if plan["bajo_umbral_compromiso"]:
            st.warning("El saldo posterior queda bajo el umbral de compromiso del SME: el desembolso exigirá "
                       "autorización escrita de la Gerencia General (POL-04 Art. 2).")
    elif sesion.rol != "JF":
        st.info("Pendiente del análisis de la Unidad de Finanzas.")
    if sesion.rol == "JF" and plan["estado"] == "BORRADOR":
        with st.form("form_roa"):
            c1, c2, c3, c4 = st.columns(4)
            utilidad = c1.number_input("Utilidad neta anual proyectada (Bs)", min_value=0.0, step=1000.0,
                                       format="%.2f")
            fecha = c2.date_input("Fecha del análisis", hoy, format="DD/MM/YYYY")
            activos_tot = c3.number_input("Activo total (0 = según libros)", min_value=0.0, step=1000.0, format="%.2f")
            saldo = c4.number_input("Saldo bancario proyectado (0 = actual)", min_value=0.0, step=1000.0,
                                    format="%.2f")
            if st.form_submit_button("Calcular ROA y SME", type="primary"):
                try:
                    with con_bd() as con:
                        r = activos.analizar_plan(con, sesion, pid, fecha, decimal_de(utilidad),
                                                  activos_totales=decimal_de(activos_tot) if activos_tot else None,
                                                  saldo_proyectado=decimal_de(saldo) if saldo else None)
                    st.success(f"ROA {activos.pct_texto(r['roa'])}; "
                               + ("compatible" if r["compatible_sme"] else "NO compatible") + " con el SME.")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    # FIN-TES-03 y aprobaciones
    if sesion.rol == "GG" and plan["estado"] == "BORRADOR":
        with st.form("form_presentar"):
            st.markdown("**Presentar al Consejo de Administración (FIN-TES-03)**")
            resumen = st.text_area("Resumen ejecutivo del plan")
            recomendacion = st.text_area("Recomendación de la Gerencia General")
            if st.form_submit_button("Presentar al CA", type="primary"):
                try:
                    with con_bd() as con:
                        activos.presentar_plan(con, sesion, pid, resumen, recomendacion, hoy)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if sesion.rol == "CA" and plan["estado"] == "PRESENTADO":
        with st.form("form_ca"):
            st.markdown("**Resolución del Consejo de Administración**")
            decision = st.radio("Decisión", ["APRUEBA", "OBSERVA"], horizontal=True,
                                format_func=lambda d: "Aprobar" if d == "APRUEBA" else "Observar y devolver")
            acta = st.text_input("Acta del Consejo de Administración")
            obs = st.text_area("Observaciones")
            if st.form_submit_button("Registrar resolución", type="primary"):
                try:
                    with con_bd() as con:
                        activos.resolver_plan_ca(con, sesion, pid, decision, acta, obs or None, hoy)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if sesion.rol == "AG" and plan["estado"] == "APROBADO":
        with st.form("form_ag"):
            acta = st.text_input("Acta de la Asamblea General")
            if st.form_submit_button("Ratificar montos del plan", type="primary"):
                try:
                    with con_bd() as con:
                        activos.ratificar_plan(con, sesion, pid, acta, hoy)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if sesion.rol == "GG" and plan["estado"] in ("APROBADO", "RATIFICADO"):
        st.subheader("Inversión no prevista (POL-02 Art. 1)")
        st.caption("Toda inversión fuera del plan requiere al Consejo de Administración (Matriz CI-FIN-09); sobre "
                   "el umbral de POL-02 la autorización es específica. Solicítela y, una vez firmada por el CA en "
                   "«Autorizaciones», incorpórela al plan.")
        with st.form("form_no_prevista"):
            c1, c2, c3 = st.columns([3, 2, 1])
            activo = c1.text_input("Activo")
            cat = c2.selectbox("Categoría (PC-06)", categorias, format_func=lambda c: c.nombre, key="np_cat")
            costo = c3.number_input("Costo (Bs)", min_value=0.0, step=100.0, format="%.2f", key="np_costo")
            c4, c5, c6 = st.columns([1, 1, 2])
            ciclo = c4.number_input("Ciclo", min_value=1, max_value=12, value=1, step=1, key="np_ciclo")
            fecha = c5.date_input("Fecha prevista", hoy, format="DD/MM/YYYY", key="np_fecha")
            aut = c6.number_input("N.º de autorización del CA (0 = solicitarla)", min_value=0, step=1)
            justificacion = st.text_area("Justificación técnica", key="np_just")
            if st.form_submit_button("Solicitar / incorporar"):
                try:
                    with con_bd() as con:
                        if int(aut) == 0:
                            nueva = activos.solicitar_no_prevista(con, sesion, decimal_de(costo),
                                                                  f"CAPEX no previsto: {activo}. {justificacion}", fecha)
                            st.success(f"Solicitud n.º {nueva} enviada al Consejo de Administración.")
                        else:
                            activos.agregar_no_prevista(con, sesion, pid, activo, cat.clave, decimal_de(costo),
                                                        justificacion, int(ciclo), fecha, int(aut))
                            st.success("Inversión no prevista incorporada al plan.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ------------------------------------------------------------------- activación
def _tab_activacion(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        planes = [p for p in activos.listar_planes(con, sesion) if p["estado"] in ("APROBADO", "RATIFICADO")]
        lineas = []
        for p in planes:
            for l in activos.detalle_plan(con, sesion, int(p["id"]))["lineas"]:
                if l["estado"] == "VIGENTE":
                    lineas.append({**l, "ejercicio": p["ejercicio"]})
    if sesion.rol == "GG":
        st.subheader("Autorizar la activación (FIN-TRA-08)")
        if not lineas:
            st.info("No hay planes CAPEX aprobados por el Consejo de Administración.")
        else:
            linea = st.selectbox("Línea del plan", lineas, key="ac_linea",
                                 format_func=lambda l: f"{l['ejercicio']} · {l['activo']} · aprobado {bs(l['costo'])} · "
                                                       f"autorizado {bs(l['autorizado'])}")
            cat = activos.CATEGORIAS[linea["categoria"]]
            with st.form("form_activacion"):
                descripcion = st.text_input("Descripción del bien", value=linea["activo"])
                c1, c2, c3, c4 = st.columns(4)
                precio = c1.number_input("Precio (Bs)", min_value=0.0, value=float(linea["costo"]), step=100.0,
                                         format="%.2f")
                flete = c2.number_input("Fletes (Bs)", min_value=0.0, step=10.0, format="%.2f")
                instalacion = c3.number_input("Instalación (Bs)", min_value=0.0, step=10.0, format="%.2f")
                fecha = c4.date_input("Disponible para su uso", hoy, format="DD/MM/YYYY")
                c5, c6, c7 = st.columns(3)
                destinos = [cat.destino] if cat.destino else list(activos.DESTINOS)
                destino = c5.selectbox("Destino", destinos, format_func=str.capitalize)
                proveedor = c6.text_input("Proveedor")
                documento = c7.text_input("Factura / documento")
                ubicacion = st.text_input("Ubicación", value=activos.UBICACION_GRANJA)
                c8, c9, c10 = st.columns(3)
                informe = c8.text_input("Informe al CA (si la variación supera el umbral)")
                aut = c9.number_input("Autorización de la Asamblea (variación)", min_value=0, step=1)
                acta_saldo = c10.text_input("Aprobación de la Asamblea (costo > saldo bancario)")
                st.caption(f"{cat.nombre}: cuenta {cat.cuenta_activo}; cuota a "
                           + (f"{cat.centro_costo}-{cat.sufijo}" if cat.centro_costo else (cat.cuenta_gasto or "—"))
                           + f" contra {cat.cuenta_depreciacion or '—'}.")
                if st.form_submit_button("Autorizar activación", type="primary"):
                    try:
                        with con_bd() as con:
                            r = activos.autorizar_activacion(
                                con, sesion, int(linea["id"]), descripcion=descripcion, destino=destino,
                                precio=decimal_de(precio), flete=decimal_de(flete), instalacion=decimal_de(instalacion),
                                fecha_disponible=fecha, proveedor=proveedor, documento_ref=documento,
                                ubicacion=ubicacion, informe_ca_ref=informe or None,
                                autorizacion_variacion_id=int(aut) or None, aprobacion_ag_saldo_ref=acta_saldo or None)
                        v = r["variacion"]
                        st.success(f"Activación n.º {r['id']} autorizada por {bs(r['costo'])}; variación "
                                   f"{activos.pct_texto(v['variacion_pct'])}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
            with st.expander("Evaluar una variación antes de autorizar (POL-02 Art. 4)"):
                c1, c2 = st.columns(2)
                costo = c1.number_input("Costo total previsto (Bs)", min_value=0.0, value=float(linea["costo"]),
                                        step=100.0, format="%.2f", key="ac_eval")
                try:
                    with con_bd() as con:
                        v = activos.evaluar_variacion(con, int(linea["id"]), decimal_de(costo) or Decimal("0.01"), hoy)
                    c2.markdown(f"Variación {bs(v['variacion'])} ({activos.pct_texto(v['variacion_pct'])}) · "
                                f"informa al CA: **{'Sí' if v['informa_ca'] else 'No'}** · Asamblea: "
                                f"**{'Sí' if v['requiere_ag'] else 'No'}**")
                    if v["requiere_ag"] and st.button("Solicitar la nueva aprobación de la Asamblea"):
                        with con_bd() as con:
                            nueva = activos.solicitar_variacion(con, sesion, int(linea["id"]), decimal_de(costo),
                                                                f"Variación CAPEX: {linea['activo']}", hoy)
                        st.success(f"Solicitud n.º {nueva} enviada a la Asamblea General.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        lista = activos.listar_activaciones(con, sesion)
        variaciones = activos.listar_variaciones(con, sesion)
    pendientes = [a for a in lista if a["estado"] == "AUTORIZADA"]
    if sesion.rol == "JF" and pendientes:
        st.subheader("Registrar activos autorizados (FIN-TRA-09)")
        act = st.selectbox("Activación autorizada", pendientes, key="ac_reg",
                           format_func=lambda a: f"n.º {a['id']} · {a['descripcion']} · {bs(a['costo'])} · disponible "
                                                 f"{fecha_pantalla(a['fecha_disponible'])}")
        if st.button("Registrar activo y abrir su ficha", type="primary"):
            try:
                with con_bd() as con:
                    r = activos.registrar_activo(con, sesion, int(act["id"]))
                st.success(f"Ficha {r['codigo_interno']} abierta; asiento {r['asiento_id']} ({r['destino_cuota']}).")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if sesion.rol == "GG" and pendientes:
        with st.expander("Anular una activación aún no registrada"):
            act = st.selectbox("Activación", pendientes, key="ac_anular",
                               format_func=lambda a: f"n.º {a['id']} · {a['descripcion']}")
            motivo = st.text_input("Motivo", key="ac_motivo")
            if st.button("Anular activación"):
                try:
                    with con_bd() as con:
                        activos.anular_activacion(con, sesion, int(act["id"]), motivo)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.markdown("**Activaciones (FIN-TRA-08)**")
    for a in lista:
        a["destino_txt"] = a["destino"].capitalize()
    st.dataframe(tabla(lista, [("id", "N°"), ("ejercicio", "Plan"), ("descripcion", "Bien"), ("costo", "Costo"),
                               ("fecha_disponible", "Disponible"), ("destino_txt", "Destino"),
                               ("autorizado_por", "Autorizó"), ("estado", "Estado"), ("codigo_interno", "Ficha")],
                       montos=("costo",), fechas=("fecha_disponible",)), hide_index=True, use_container_width=True)
    if variaciones:
        st.markdown("**Variaciones frente al monto aprobado**")
        for v in variaciones:
            v["pct_txt"] = activos.pct_texto(v["pct"])
            v["ca"] = v["informe_ca_ref"] or ("—" if not v["informa_ca"] else "")
            v["ag"] = f"Autorización {v['autorizacion_id']}" if v["requiere_ag"] else "No requerida"
        st.dataframe(tabla(variaciones, [("activo", "Activo"), ("aprobado", "Aprobado"), ("ejecutado", "Ejecutado"),
                                         ("variacion", "Variación"), ("pct_txt", "%"), ("ca", "Informe al CA"),
                                         ("ag", "Asamblea")], montos=("aprobado", "ejecutado", "variacion")),
                     hide_index=True, use_container_width=True)


# ---------------------------------------------------------------- depreciación
def _tab_depreciacion(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        pendientes = activos.meses_pendientes(con, hoy)
    if pendientes:
        st.warning("Meses con cuotas pendientes: " + ", ".join(pendientes))
    c1, c2 = st.columns(2)
    defecto = pendientes[0] if pendientes else f"{hoy.year:04d}-{hoy.month:02d}"
    anio = c1.number_input("Año", min_value=2026, max_value=2031, value=int(defecto[:4]), step=1, key="dp_anio")
    mes = c2.selectbox("Mes", list(range(1, 13)), index=int(defecto[5:]) - 1, key="dp_mes",
                       format_func=lambda m: MESES[m - 1].capitalize())
    with con_bd() as con:
        planilla = activos.planilla_depreciacion(con, sesion, int(anio), int(mes))
    st.markdown(f"**Planilla FIN-TRA-10 de {MESES[int(mes) - 1]} de {int(anio)}** — cuota al "
                f"{fecha_pantalla(planilla['fecha'])}")
    if planilla["filas"]:
        for f in planilla["filas"]:
            f["tasa_txt"] = activos.pct_texto(f["tasa"])
        st.dataframe(tabla(planilla["filas"], [("codigo_interno", "Código"), ("descripcion", "Activo"),
                                               ("costo", "Costo"), ("tasa_txt", "Tasa"), ("numero", "Cuota n.º"),
                                               ("cuota", "Cuota del mes"), ("acumulada", "Acumulada"),
                                               ("valor_neto", "Valor neto"), ("destino", "Débito"),
                                               ("credito", "Crédito")],
                           montos=("costo", "cuota", "acumulada", "valor_neto")),
                     hide_index=True, use_container_width=True)
        st.markdown(f"Total del mes: **{bs(planilla['total'])}**")
    else:
        st.info("No hay cuotas pendientes para este mes.")
    if planilla["pendientes_previos"]:
        st.error("Primero registre: " + ", ".join(planilla["pendientes_previos"]))
    if sesion.rol == "JF" and planilla["filas"]:
        if st.button("Registrar depreciación del mes", type="primary"):
            try:
                with con_bd() as con:
                    r = activos.depreciar_mes(con, sesion, int(anio), int(mes))
                st.success(f"Asiento {r['asiento_id']}: {bs(r['total'])} de {r['activos']} activos.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    with con_bd() as con:
        historial = activos.listar_depreciaciones(con, sesion)
    if historial:
        st.markdown("**Depreciaciones registradas**")
        for h in historial:
            h["mes_txt"] = f"{MESES[h['mes'] - 1].capitalize()} {h['anio']}"
        st.dataframe(tabla(historial, [("mes_txt", "Mes"), ("fecha", "Fecha"), ("activos", "Activos"),
                                       ("total", "Total"), ("asiento_id", "Asiento"), ("registrado_por", "Registró")],
                           montos=("total",), fechas=("fecha",)), hide_index=True, use_container_width=True)


# ------------------------------------------------------------------------ bajas
def _tab_bajas(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        vigentes = [a for a in activos.libro_activos(con, sesion) if a["estado"] == "VIGENTE"]
        bajas = activos.listar_bajas(con, sesion)
        umbral = activos.umbral_baja_cv(con, hoy)
    st.caption(f"Con valor neto mayor a {bs(umbral)} la baja requiere la verificación del "
               "Consejo de Vigilancia (PR-FIN-07). La pérdida va a 7.2.01.02.")
    if sesion.rol == "GG" and vigentes:
        with st.form("form_baja"):
            st.markdown("**Acta de baja de activo fijo**")
            activo = st.selectbox("Activo", vigentes, format_func=lambda a: f"{a['codigo_interno']} — {a['descripcion']} "
                                                                             f"· neto {bs(a['valor_neto'])}")
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha de baja", hoy, format="DD/MM/YYYY")
            causa = c2.selectbox("Causa", list(activos.CAUSAS_BAJA), format_func=lambda k: activos.CAUSAS_BAJA[k])
            acta = c3.text_input("N.º de acta")
            detalle = st.text_input("Detalle de la causa")
            destino = st.text_input("Destino final del bien")
            c4, c5 = st.columns(2)
            valor = c4.number_input("Valor de realización (Bs)", min_value=0.0, step=10.0, format="%.2f")
            cuenta = c5.selectbox("Cuenta de cobro", list(activos.CUENTAS_COBRO))
            if st.form_submit_button("Emitir acta", type="primary"):
                try:
                    with con_bd() as con:
                        r = activos.emitir_acta_baja(con, sesion, int(activo["id"]), fecha, causa, detalle, destino, acta,
                                                     valor_realizacion=decimal_de(valor),
                                                     cuenta_cobro=cuenta if valor > 0 else None)
                    st.success(f"Acta emitida: valor neto {bs(r['valor_neto'])}; "
                               + ("requiere verificación del CV." if r["requiere_cv"] else "lista para su registro."))
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    pend_cv = [b for b in bajas if b["estado"] == "PENDIENTE_CV"]
    if sesion.rol == "CV" and pend_cv:
        with st.form("form_verif_baja"):
            st.markdown("**Verificación del Consejo de Vigilancia**")
            b = st.selectbox("Acta", pend_cv, format_func=lambda x: f"{x['acta_ref']} · {x['codigo_interno']} · neto "
                                                                    f"{bs(x['valor_neto'])}")
            conforme = st.radio("Resultado", [True, False], horizontal=True,
                                format_func=lambda v: "Conforme" if v else "Observada")
            obs = st.text_area("Observación")
            if st.form_submit_button("Registrar verificación", type="primary"):
                try:
                    with con_bd() as con:
                        activos.verificar_baja(con, sesion, int(b["id"]), conforme, obs)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    listas = [b for b in bajas if b["estado"] == "AUTORIZADA"]
    if sesion.rol == "JF" and listas:
        b = st.selectbox("Acta autorizada", listas, key="bj_reg",
                         format_func=lambda x: f"{x['acta_ref']} · {x['codigo_interno']} · resultado {bs(x['resultado'])}")
        if st.button("Contabilizar la baja", type="primary"):
            try:
                with con_bd() as con:
                    r = activos.contabilizar_baja(con, sesion, int(b["id"]))
                st.success(f"Asiento {r['asiento_id']}: resultado {bs(r['resultado'])}.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if bajas:
        for b in bajas:
            b["causa_txt"] = activos.CAUSAS_BAJA[b["causa"]]
        st.dataframe(tabla(bajas, [("acta_ref", "Acta"), ("codigo_interno", "Código"), ("descripcion", "Activo"),
                                   ("fecha", "Fecha"), ("causa_txt", "Causa"), ("valor_neto", "Valor neto"),
                                   ("valor_realizacion", "Realización"), ("resultado", "Resultado"),
                                   ("estado", "Estado")],
                           montos=("valor_neto", "valor_realizacion", "resultado"), fechas=("fecha",)),
                     hide_index=True, use_container_width=True)
        elegida = st.selectbox("Acta para descargar", bajas, key="bj_pdf",
                               format_func=lambda x: f"{x['acta_ref']} · {x['codigo_interno']}")
        _descargar("ACTA-BAJA", pdf.acta_baja(elegida, _ctx(sesion)), "pdf_baja")


# ---------------------------------------------------------- verificación física
def _tab_verificacion(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        estado = activos.estado_verificacion(con, hoy)
        actas = activos.listar_verificaciones(con, sesion)
    ultima = estado["ultima"]
    st.markdown("Última verificación: " + (f"{fecha_pantalla(ultima['fecha'])} ({ultima['verificado_por']})"
                                           if ultima else "ninguna")
                + f" · plazo máximo {estado['plazo_meses']} meses · "
                + chip("ROJO" if estado["vencida"] else "VERDE"), unsafe_allow_html=True)
    if sesion.rol == "CV":
        fecha = st.date_input("Fecha de la inspección", hoy, format="DD/MM/YYYY", key="vf_fecha")
        with con_bd() as con:
            vigentes = activos.activos_a_verificar(con, fecha)
        if not vigentes:
            st.info("No hay activos vigentes que verificar.")
        else:
            base = pd.DataFrame([{"activo_id": int(a["id"]), "Código": a["codigo_interno"],
                                  "Activo": a["descripcion"], "Existe": True, "Estado": "BUENO",
                                  "Ubicación verificada": a["ubicacion"], "Observación": ""} for a in vigentes])
            editado = st.data_editor(
                base, hide_index=True, use_container_width=True, disabled=["activo_id", "Código", "Activo"],
                column_config={"activo_id": None, "Estado": st.column_config.SelectboxColumn(
                    options=list(activos.ESTADOS_CONSERVACION))}, key="vf_editor")
            obs = st.text_area("Observación general", key="vf_obs")
            if st.button("Registrar acta INF-CV-01", type="primary"):
                filas = [{"activo_id": r["activo_id"], "existe": bool(r["Existe"]),
                          "estado_conservacion": r["Estado"] if r["Existe"] else None,
                          "ubicacion_verificada": r["Ubicación verificada"], "observacion": r["Observación"]}
                         for r in editado.to_dict("records")]
                try:
                    with con_bd() as con:
                        r = activos.registrar_verificacion(con, sesion, fecha, activos.items_desde(filas), obs or None)
                    st.success(f"Acta registrada: {r['activos']} activos, {r['faltantes']} no encontrados, "
                               f"{r['tasas_distintas']} con tasa distinta de PC-06.")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if actas:
        st.dataframe(tabla(actas, [("id", "N°"), ("fecha", "Fecha"), ("activos_verificados", "Activos"),
                                   ("faltantes", "No encontrados"), ("tasas_distintas", "Tasas ≠ PC-06"),
                                   ("verificado_por", "Verificó")], fechas=("fecha",)),
                     hide_index=True, use_container_width=True)
        elegida = st.selectbox("Acta", actas, key="vf_pdf", format_func=lambda a: f"n.º {a['id']} · "
                                                                                  f"{fecha_pantalla(a['fecha'])}")
        with con_bd() as con:
            acta = activos.obtener_verificacion(con, sesion, int(elegida["id"]))
        _descargar("INF-CV-01", pdf.inf_cv_01(acta, _ctx(sesion)), "pdf_infcv01")


# ------------------------------------------------------------ informe semestral
def _tab_informe(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        planes = activos.listar_planes(con, sesion)
        registrados = activos.listar_informes(con, sesion)
    if not planes:
        st.info("No hay planes CAPEX.")
        return
    c1, c2 = st.columns(2)
    ejercicio = c1.selectbox("Ejercicio", [p["ejercicio"] for p in planes], key="is_ej")
    semestre = c2.radio("Semestre", [1, 2], horizontal=True, key="is_sem",
                        format_func=lambda s: "Primero (julio a diciembre)" if s == 1 else "Segundo (enero a junio)")
    try:
        with con_bd() as con:
            inf = activos.informe_semestral(con, sesion, int(ejercicio), int(semestre))
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    st.markdown(f"Del {fecha_pantalla(inf['desde'])} al {fecha_pantalla(inf['hasta'])} · se presenta en "
                f"{MESES[inf['mes_presentacion'].month - 1]} de {inf['mes_presentacion'].year} · ejecución "
                f"{activos.pct_texto(inf['ejecucion_pct'])}")
    for f in inf["filas"]:
        f["pct"] = activos.pct_texto(f["variacion_pct"]) if f["variacion_pct"] is not None else "—"
        f["supera_txt"] = "Sí" if f["supera"] else "No"
    st.dataframe(tabla(inf["filas"], [("activo", "Activo"), ("aprobado", "Monto aprobado"),
                                      ("ejecutado", "Monto ejecutado"), ("pct", "Variación"),
                                      ("supera_txt", "¿Supera 15 %?"), ("observacion", "Observación / acción")],
                       montos=("aprobado", "ejecutado")), hide_index=True, use_container_width=True)
    _descargar("FIN-TES-04", pdf.fin_tes_04(inf, _ctx(sesion, f"Semestre {semestre} ({ejercicio})")), "pdf_fintes04")
    if sesion.rol == "GG":
        with st.form("form_informe"):
            fecha = st.date_input("Fecha de presentación al CA", hoy, format="DD/MM/YYYY")
            obs = st.text_area("Observación")
            if st.form_submit_button("Presentar informe semestral", type="primary"):
                try:
                    with con_bd() as con:
                        r = activos.registrar_informe_semestral(con, sesion, int(ejercicio), int(semestre), fecha,
                                                                obs or None)
                    st.success("Informe presentado" + (" fuera del mes previsto." if r["fuera_de_plazo"] else "."))
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if registrados:
        st.markdown("**Informes presentados**")
        for r in registrados:
            r["plazo"] = "Fuera de plazo" if r["fuera_de_plazo"] else "En plazo"
        st.dataframe(tabla(registrados, [("ejercicio", "Ejercicio"), ("semestre", "Semestre"), ("fecha", "Fecha"),
                                         ("aprobado", "Aprobado"), ("ejecutado", "Ejecutado"), ("plazo", "Plazo"),
                                         ("presentado_por", "Presentó")], montos=("aprobado", "ejecutado"),
                           fechas=("fecha",)), hide_index=True, use_container_width=True)


# --------------------------------------------------------------------- formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Formatos del ciclo de activos fijos y CAPEX en PDF, con el bloque de firmas.")
    with con_bd() as con:
        planes = activos.listar_planes(con, sesion)
        acts = activos.listar_activaciones(con, sesion)
        libro = activos.libro_activos(con, sesion)
    opciones = {"FIN-TES-01": "Plan Anual de Inversiones (CAPEX)", "FIN-TES-02": "Hoja de Cálculo de Indicadores (ROA)",
                "FIN-TES-03": "Informe Técnico-Financiero de Inversiones",
                "FIN-TRA-08": "Formulario de Activación de Activo Fijo",
                "FIN-TRA-10": "Planilla Mensual de Depreciación", "LIB-AF": "Libro de Inventario de Activos Fijos"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda k: f"{k} — {opciones[k]}", key="fm_cod_a")
    try:
        datos: bytes | None = None
        if codigo in ("FIN-TES-01", "FIN-TES-02", "FIN-TES-03"):
            if not planes:
                st.info("No hay planes CAPEX.")
                return
            plan = st.selectbox("Plan", planes, key="fm_plan", format_func=lambda p: f"Ejercicio {p['ejercicio']}")
            with con_bd() as con:
                detalle = activos.detalle_plan(con, sesion, int(plan["id"]))
            funcion = {"FIN-TES-01": pdf.fin_tes_01, "FIN-TES-02": pdf.fin_tes_02, "FIN-TES-03": pdf.fin_tes_03}[codigo]
            datos = funcion(detalle, _ctx(sesion, f"Ejercicio {plan['ejercicio']}"))
        elif codigo == "FIN-TRA-08":
            if not acts:
                st.info("No hay activaciones.")
                return
            act = st.selectbox("Activación", acts, key="fm_act", format_func=lambda a: f"n.º {a['id']} · "
                                                                                       f"{a['descripcion']}")
            datos = pdf.fin_tra_08(act, _ctx(sesion))
        elif codigo == "FIN-TRA-10":
            c1, c2 = st.columns(2)
            anio = c1.number_input("Año", min_value=2026, max_value=2031, value=date.today().year, step=1, key="fm_an")
            mes = c2.selectbox("Mes", list(range(1, 13)), key="fm_mes", format_func=lambda m: MESES[m - 1].capitalize())
            with con_bd() as con:
                planilla = activos.planilla_depreciacion(con, sesion, int(anio), int(mes))
            datos = pdf.fin_tra_10(planilla, _ctx(sesion))
        else:
            datos = pdf.libro_inventario(libro, _ctx(sesion))
        if datos is not None:
            _descargar(codigo, datos, "pdf_formato_a")
    except ErrorPollolandia as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 6 — Activos fijos y CAPEX."""
    st.header("Activos fijos y CAPEX")
    _resumen(sesion)
    pestañas = st.tabs(["Libro de activos", "Plan CAPEX", "Activación", "Depreciación", "Bajas", "Verificación física",
                        "Informe semestral", "Formatos"])
    funciones = (_tab_libro, _tab_plan, _tab_activacion, _tab_depreciacion, _tab_bajas, _tab_verificacion,
                 _tab_informe, _tab_formatos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
