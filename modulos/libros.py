"""Páginas del motor contable (Fase 2): registro y reversión de asientos,
períodos y libros diario, mayor y balance de comprobación.

Solo llaman a ``servicios/`` y a ``reportes/``. Desde la Fase 10 el cierre del
período exige la lista de verificación FIN-INF-01 con pendientes en cero
(servicios.cierre). Cada pantalla ofrece su PDF oficial (``reportes.pdf_contable``):
comprobantes, libro diario, libro mayor (por cuenta y masivo), balance de
comprobación de 4 y 8 columnas, estado de los períodos y plan de cuentas.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from db.conexion import ejercicio_vigente_o_ultimo
from modulos.comun import bs, chip, codigo_de, con_bd, decimal_de, descargar_pdf, mostrar_error, pdf_bajo_demanda, tabla
from reportes import pdf_contable as pc
from servicios import cierre, contable, identidad, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

TIPOS = {"INGRESO": "Ingreso", "EGRESO": "Egreso", "TRASPASO": "Traspaso", "AJUSTE": "Ajuste (autoriza la GG)"}


def _rango_por_defecto(con) -> tuple[date, date]:
    hoy = date.today()
    ejercicio = ejercicio_vigente_o_ultimo(con, hoy)
    fila = con.execute("SELECT fecha_inicio, fecha_fin FROM ejercicios WHERE ejercicio = ?", (ejercicio,)).fetchone()
    inicio, fin = date.fromisoformat(fila[0]), date.fromisoformat(fila[1])
    return inicio, min(max(hoy, inicio), fin)


def _membrete_emision(sesion: Sesion) -> tuple[pc.Membrete, pc.Emision]:
    with con_bd() as con:
        return identidad.membrete(con), identidad.emision(sesion)


def _pdf_comprobante(sesion: Sesion, asiento_id: int, clave: str) -> None:
    """Botón con el comprobante contable oficial de un asiento."""
    with con_bd() as con:
        a = contable.obtener_asiento(con, sesion, asiento_id)
    m, e = _membrete_emision(sesion)
    descargar_pdf(f"comprobante {a['comprobante']}", f"Comprobante_{a['comprobante']}", pc.comprobante(a, m, e), clave)


def _filtro_fechas(con, clave: str) -> tuple[date, date]:
    inicio, fin = _rango_por_defecto(con)
    c1, c2 = st.columns(2)
    desde = c1.date_input("Desde", inicio, format="DD/MM/YYYY", key=f"{clave}_desde")
    hasta = c2.date_input("Hasta", fin, format="DD/MM/YYYY", key=f"{clave}_hasta")
    return desde, hasta


# ------------------------------------------------------------------ diario
def pagina_diario(sesion: Sesion) -> None:
    st.header("Libro diario")
    with con_bd() as con:
        desde, hasta = _filtro_fechas(con, "diario")
        incluir = st.checkbox("Incluir asientos reversados y sus reversiones", value=True)
        try:
            asientos = contable.libro_diario(con, sesion, desde, hasta, incluir_reversados=incluir)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
    if not asientos:
        st.info("No hay asientos en el rango elegido.")
        return
    total = sum((a["total_debe"] for a in asientos), Decimal("0"))
    st.caption(f"{len(asientos)} asientos · total Debe = Haber {bs(total)}")
    m, e = _membrete_emision(sesion)
    firma = f"{desde}_{hasta}_{int(incluir)}_{len(asientos)}"
    c1, c2, c3 = st.columns(3)
    with c1:
        pdf_bajo_demanda("Libro Diario", f"Libro_Diario_{desde}_{hasta}",
                         lambda: pc.libro_diario(asientos, desde.isoformat(), hasta.isoformat(), m, e, incluir),
                         f"diario_{firma}")
    with c2:
        pdf_bajo_demanda("comprobantes del período", f"Comprobantes_{desde}_{hasta}",
                         lambda: pc.comprobantes(asientos, desde.isoformat(), hasta.isoformat(), m, e),
                         f"comprobantes_{firma}")
    with c3:
        elegido = st.selectbox("Comprobante a imprimir", asientos, key="diario_comp",
                               format_func=lambda a: f"{a['comprobante']} · {fecha_pantalla(a['fecha'])}")
        descargar_pdf(f"comprobante {elegido['comprobante']}", f"Comprobante_{elegido['comprobante']}",
                      pc.comprobante(elegido, m, e), f"diario_comp_pdf_{elegido['id']}")
    for a in asientos:
        titulo = f"{a['comprobante']} · {fecha_pantalla(a['fecha'])} · {a['glosa']} · {bs(a['total_debe'])}"
        with st.expander(titulo):
            st.markdown(f"{chip(a['estado'])} &nbsp; Origen: **{a['origen']}** · Documento: "
                        f"{a['documento_ref'] or '—'} · Registrado por {a['creado_por']}", unsafe_allow_html=True)
            if a["reversa_de"]:
                st.caption(f"Reversión del asiento interno n.º {a['reversa_de']}")
            st.dataframe(tabla(a["lineas"], [("linea", "N.º"), ("cuenta", "Cuenta"), ("nombre", "Denominación"),
                                             ("sufijo", "Sufijo"), ("debe", "Debe"), ("haber", "Haber"),
                                             ("glosa", "Glosa")], montos=("debe", "haber"), ocultar_ceros=True),
                         hide_index=True, use_container_width=True)


# ------------------------------------------------------------------- mayor
def pagina_mayor(sesion: Sesion) -> None:
    st.header("Libro mayor")
    with con_bd() as con:
        cuentas = contable.listar_cuentas_movimiento(con)
        opciones = [f"{c['codigo']} — {c['nombre']}" for c in cuentas]
        elegida = st.selectbox("Cuenta", opciones, index=opciones.index(next(o for o in opciones
                                                                          if o.startswith("1.1.01.03"))))
        codigo = codigo_de(elegida)
        sufijo = None
        if any(c["codigo"] == codigo and c["es_centro"] for c in cuentas):
            sufijos = ["(todos)"] + [f"{x['sufijo']} — {x['nombre']}" for x in contable.listar_sufijos(con, codigo)]
            eleccion = st.selectbox("Sufijo (elemento del costo)", sufijos)
            sufijo = None if eleccion == "(todos)" else codigo_de(eleccion)
        desde, hasta = _filtro_fechas(con, "mayor")
        try:
            mayor = contable.libro_mayor(con, sesion, codigo, desde, hasta, sufijo)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo inicial", bs(mayor["saldo_inicial"]))
    c2.metric("Debe", bs(mayor["total_debe"]))
    c3.metric("Haber", bs(mayor["total_haber"]))
    c4.metric("Saldo final", bs(mayor["saldo_final"]))
    st.caption(f"Naturaleza {str(mayor['naturaleza']).lower()}: el saldo se calcula "
               f"{'Haber − Debe' if mayor['naturaleza'] == 'ACREEDORA' else 'Debe − Haber'}.")
    st.dataframe(tabla(mayor["movimientos"], [("fecha", "Fecha"), ("comprobante", "Comprobante"),
                                               ("glosa", "Glosa"), ("sufijo", "Sufijo"), ("debe", "Debe"),
                                               ("haber", "Haber"), ("saldo", "Saldo"), ("estado", "Estado")],
                       montos=("debe", "haber", "saldo"), fechas=("fecha",), ocultar_ceros=False),
                 hide_index=True, use_container_width=True)
    m, e = _membrete_emision(sesion)
    c1, c2 = st.columns(2)
    with c1:
        descargar_pdf(f"Mayor {codigo}" + (f" sufijo {sufijo}" if sufijo else ""),
                      f"Mayor_{codigo}{'_' + sufijo if sufijo else ''}_{desde}_{hasta}",
                      pc.libro_mayor(mayor, desde.isoformat(), hasta.isoformat(), sufijo, m, e), "mayor_pdf")
    with c2:
        por_sufijo = st.checkbox("Abrir los centros de costo por sufijo", value=True, key="mayor_masivo_suf")

        def _masivo() -> bytes:
            with con_bd() as con2:
                lista = contable.mayores(con2, sesion, desde, hasta, por_sufijo)
            return pc.libro_mayor_masivo(lista, desde.isoformat(), hasta.isoformat(), m, e)

        pdf_bajo_demanda("Mayor de todas las cuentas", f"Libro_Mayor_{desde}_{hasta}", _masivo,
                         f"mayor_masivo_{desde}_{hasta}_{int(por_sufijo)}")


# ----------------------------------------------------------------- balance
def pagina_balance(sesion: Sesion) -> None:
    st.header("Balance de comprobación de sumas y saldos")
    with con_bd() as con:
        desde, hasta = _filtro_fechas(con, "balance")
        nivel = st.radio("Nivel", [4, 3, 2, 1], horizontal=True,
                         format_func=lambda n: {4: "Cuenta", 3: "Subgrupo", 2: "Clasificación", 1: "Grupo"}[n])
        try:
            b = contable.balance_comprobacion(con, sesion, desde, hasta, nivel)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
    filas = b["filas"] + [{"codigo": "", "nombre": "TOTALES", **b["totales"]}]
    st.dataframe(tabla(filas, [("codigo", "Código"), ("nombre", "Denominación"), ("debe", "Sumas Debe"),
                               ("haber", "Sumas Haber"), ("saldo_deudor", "Saldo deudor"),
                               ("saldo_acreedor", "Saldo acreedor")],
                       montos=("debe", "haber", "saldo_deudor", "saldo_acreedor"), ocultar_ceros=True),
                 hide_index=True, use_container_width=True)
    if b["cuadra_sumas"] and b["cuadra_saldos"]:
        st.success(f"El balance cuadra: sumas {bs(b['totales']['debe'])} y saldos {bs(b['totales']['saldo_deudor'])}.")
    else:
        st.error("El balance NO cuadra: revise los asientos descuadrados.")
    m, e = _membrete_emision(sesion)
    c1, c2 = st.columns(2)
    with c1:
        descargar_pdf("Balance de 4 columnas (sumas y saldos)", f"Balance_Comprobacion_4col_{desde}_{hasta}",
                      pc.balance_comprobacion(b, m, e), "balance_pdf4")
    with c2:
        with con_bd() as con:
            ocho = contable.balance_ocho_columnas(con, sesion, desde, hasta)
        descargar_pdf("Balance de 8 columnas", f"Balance_Comprobacion_8col_{desde}_{hasta}",
                      pc.balance_ocho_columnas(ocho, m, e), "balance_pdf8")
        st.caption("8 columnas: sumas, saldos, inventario (grupos 1-4) y resultados (grupos 5-8), con el "
                   f"resultado del período {bs(ocho['resultado']['monto'])} "
                   f"({'cuadra' if ocho['cuadra_resultado'] else 'NO cuadra'}).")


# --------------------------------------------------------------- registrar
def _opciones_sufijo(con, ejercicio: int) -> list[str]:
    vistos: dict[str, str] = {}
    for centro in ("6.1.01.01", "6.1.01.02"):
        for x in contable.listar_sufijos(con, centro, ejercicio):
            if x["habilitado"]:
                vistos.setdefault(x["sufijo"], f"{x['sufijo']} — {x['nombre']}")
    return [vistos[k] for k in sorted(vistos)]


def pagina_registrar(sesion: Sesion) -> None:
    st.header("Registrar asiento")
    st.caption("Partida doble: el asiento solo se registra si Debe = Haber exactamente. Las cuentas 6.1.01.xx "
               "(centros de costo) exigen sufijo.")
    with con_bd() as con:
        c1, c2, c3 = st.columns([1, 1, 2])
        tipo = c1.selectbox("Tipo de comprobante", list(TIPOS), format_func=TIPOS.get)
        fecha = c2.date_input("Fecha", date.today(), format="DD/MM/YYYY")
        documento = c3.text_input("Documento de respaldo", placeholder="FIN-EGR-04, factura, recibo…")
        glosa = st.text_input("Glosa", max_chars=500)
        try:
            ejercicio = ejercicio_vigente_o_ultimo(con, fecha)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
        cuentas = [c for c in contable.listar_cuentas_movimiento(con, ejercicio) if c["habilitada"]]
        opciones_cuenta = [f"{c['codigo']} — {c['nombre']}" for c in cuentas]
        opciones_sufijo = _opciones_sufijo(con, ejercicio)
        autorizacion_id = None
        if tipo == "AJUSTE":
            disponibles = [a for a in seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                                       tipo_decision="AJUSTE_CONTABLE")]
            if not disponibles:
                st.warning("No hay autorizaciones de ajuste vigentes. Solicítela en «Autorizaciones» y espere la "
                           "firma de la Gerencia General.")
            else:
                elegida = st.selectbox("Autorización de la GG", disponibles,
                                       format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])} · {a['descripcion']}")
                autorizacion_id = int(elegida["id"])

    plantilla = pd.DataFrame({"Cuenta": [None, None], "Debe": [0.0, 0.0], "Haber": [0.0, 0.0],
                              "Sufijo": [None, None], "Glosa de línea": ["", ""]})
    lineas = st.data_editor(
        plantilla, num_rows="dynamic", use_container_width=True, key="editor_asiento",
        column_config={
            "Cuenta": st.column_config.SelectboxColumn("Cuenta", options=opciones_cuenta, width="large", required=True),
            "Debe": st.column_config.NumberColumn("Debe (Bs)", min_value=0.0, step=0.01, format="%.2f"),
            "Haber": st.column_config.NumberColumn("Haber (Bs)", min_value=0.0, step=0.01, format="%.2f"),
            "Sufijo": st.column_config.SelectboxColumn("Sufijo (Grupo 6)", options=opciones_sufijo),
            "Glosa de línea": st.column_config.TextColumn("Glosa de línea"),
        })
    validas = lineas.dropna(subset=["Cuenta"])
    total_debe = sum((decimal_de(v).quantize(Decimal("0.01")) for v in validas["Debe"]), Decimal("0"))
    total_haber = sum((decimal_de(v).quantize(Decimal("0.01")) for v in validas["Haber"]), Decimal("0"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Debe", bs(total_debe))
    c2.metric("Total Haber", bs(total_haber))
    c3.metric("Diferencia", bs(total_debe - total_haber))

    if st.button("Registrar asiento", type="primary", disabled=total_debe == 0):
        datos = [{"cuenta": codigo_de(f["Cuenta"]), "debe": decimal_de(f["Debe"]), "haber": decimal_de(f["Haber"]),
                  "sufijo": codigo_de(f["Sufijo"]) if isinstance(f["Sufijo"], str) else None,
                  "glosa": f["Glosa de línea"] or None} for _, f in validas.iterrows()]
        try:
            with con_bd() as con:
                asiento = contable.crear_asiento(con, sesion, fecha, glosa, datos, "MANUAL", documento or None,
                                                 tipo=tipo, autorizacion_id=autorizacion_id)
            st.success(f"Asiento {asiento.comprobante} registrado por {bs(asiento.total)}.")
            st.session_state["ultimo_asiento_registrado"] = int(asiento.id)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if st.session_state.get("ultimo_asiento_registrado"):
        _pdf_comprobante(sesion, int(st.session_state["ultimo_asiento_registrado"]), "registrar_comp_pdf")


# ---------------------------------------------------------------- reversar
def pagina_reversar(sesion: Sesion) -> None:
    st.header("Reversar asiento")
    st.caption("Los asientos no se borran: se anulan con un asiento espejo de ajuste, autorizado por la GG "
               "(MOF §3.1). El original queda con estado REVERSADO.")
    with con_bd() as con:
        try:
            vigentes = [a for a in contable.libro_diario(con, sesion, incluir_reversados=False)
                        if a["tipo"] not in contable.TIPOS_NO_REVERSIBLES]
            autorizaciones = seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                             tipo_decision="AJUSTE_CONTABLE")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
    if not vigentes:
        st.info("No hay asientos reversibles.")
        return
    asiento = st.selectbox("Asiento", vigentes, format_func=lambda a: f"{a['comprobante']} · "
                           f"{fecha_pantalla(a['fecha'])} · {a['glosa']} · {bs(a['total_debe'])}")
    motivo = st.text_input("Motivo de la reversión", max_chars=300)
    usar_fecha = st.checkbox("Reversar con otra fecha (si el período original está cerrado)")
    fecha = st.date_input("Fecha de la reversión", date.today(), format="DD/MM/YYYY") if usar_fecha else None
    validas = [a for a in autorizaciones if a["monto"] >= asiento["total_debe"]]
    if not validas:
        st.warning(f"Se necesita una autorización AJUSTE_CONTABLE firmada por la GG por al menos "
                   f"{bs(asiento['total_debe'])}.")
        return
    autorizacion = st.selectbox("Autorización de la GG", validas,
                                format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])} · {a['descripcion']}")
    if st.button("Reversar", type="primary"):
        try:
            with con_bd() as con:
                r = contable.reversar_asiento(con, sesion, int(asiento["id"]), motivo,
                                              autorizacion_id=int(autorizacion["id"]), fecha=fecha)
            st.success(f"Asiento {asiento['comprobante']} reversado con {r.comprobante}.")
            st.session_state["ultima_reversion"] = int(r.id)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if st.session_state.get("ultima_reversion"):
        _pdf_comprobante(sesion, int(st.session_state["ultima_reversion"]), "reversar_comp_pdf")


# ---------------------------------------------------------------- períodos
def pagina_periodos(sesion: Sesion) -> None:
    st.header("Períodos contables")
    with con_bd() as con:
        ejercicios = [f[0] for f in con.execute("SELECT ejercicio FROM ejercicios ORDER BY ejercicio")]
        actual = ejercicio_vigente_o_ultimo(con, date.today())
        ejercicio = st.selectbox("Ejercicio (1-jul a 30-jun)", ejercicios, index=ejercicios.index(actual))
        periodos = contable.listar_periodos(con, ejercicio)
        puede_cerrar = seguridad.tiene_permiso(con, sesion.rol, "contabilidad", "AUTORIZAR")
    st.dataframe(tabla([{**p, "nombre": contable.describir_periodo(p)} for p in periodos],
                       [("numero", "N.º"), ("nombre", "Período"), ("estado", "Estado"), ("asientos", "Asientos"),
                        ("cerrado_por", "Cerrado por"), ("cerrado_en", "Cerrado en")]),
                 hide_index=True, use_container_width=True)
    m, e = _membrete_emision(sesion)
    c1, c2 = st.columns(2)
    with c1:
        descargar_pdf(f"estado de los períodos {ejercicio}", f"Periodos_{ejercicio}",
                      pc.periodos(int(ejercicio), [{**p, "nombre": contable.describir_periodo(p)} for p in periodos],
                                  m, e), "periodos_pdf")
    with c2:
        def _plan() -> bytes:
            with con_bd() as con2:
                return pc.plan_cuentas(contable.plan_de_cuentas(con2, sesion), m, e)

        pdf_bajo_demanda("Plan de Cuentas", "Plan_de_Cuentas", _plan, "plan_cuentas")
    if not puede_cerrar:
        st.caption("El cierre de períodos lo ejecuta la Gerencia General.")
        return
    abiertos = [p for p in periodos if p["estado"] == "ABIERTO"]
    if not abiertos:
        st.info("Todos los períodos del ejercicio están cerrados.")
        return
    st.subheader("Cerrar período")
    st.caption("Los períodos se cierran en orden y no se reabren. Exige la FIN-INF-01 registrada por la Unidad de "
               "Finanzas con pendientes en cero (pestaña «Cierre mensual» de Cierres y estados financieros).")
    periodo = st.selectbox("Período a cerrar", abiertos[:1], format_func=contable.describir_periodo)
    confirmar = st.checkbox("Confirmo que el período está conciliado y revisado")
    if st.button("Cerrar período", type="primary", disabled=not confirmar):
        try:
            with con_bd() as con:
                cierre.cerrar_mes(con, sesion, int(periodo["id"]))
            st.success(f"Período de {contable.describir_periodo(periodo)} cerrado.")
            st.rerun()
        except ErrorPollolandia as exc:
            mostrar_error(exc)
