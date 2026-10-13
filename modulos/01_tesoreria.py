"""Módulo 1 — Tesorería (Fase 3). Pantalla con pestañas: Caja, Bancos,
SME y flujo, Conciliación y Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. Cada acción se muestra según
los permisos efectivos del rol (el JF registra, la GG autoriza y firma, el CV
arquea y fiscaliza).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from db.conexion import ejercicio_vigente_o_ultimo
from modulos.comun import bs, chip, codigo_de, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_tesoreria as pdf
from servicios import contable, seguridad, tesoreria
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

NOMBRE_BANCO = {"1.1.01.03": "1.1.01.03 — Banco cuenta corriente", "1.1.01.04": "1.1.01.04 — Banco caja de ahorros"}
CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}


def _pct_txt(valor: Decimal) -> str:
    """Porcentaje con coma decimal: ``96,63 %``."""
    return f"{Decimal(valor):.2f} %".replace(".", ",")


def _indice(opciones: list[str], prefijo: str) -> int:
    return next((i for i, o in enumerate(opciones) if o.startswith(prefijo)), 0)


def _puede(con, sesion: Sesion, recurso: str, accion: str) -> bool:
    return seguridad.tiene_permiso(con, sesion.rol, recurso, accion)


def _autorizaciones_desembolso(con, sesion: Sesion, minimo: Decimal) -> list[dict]:
    return [a for a in seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA", tipo_decision="DESEMBOLSO")
            if a["monto"] >= minimo]


def _elegir_autorizacion(con, sesion: Sesion, monto: Decimal, fecha: date, clave: str) -> int | None:
    """Selector de autorización DESEMBOLSO cuando la Matriz CI-FIN-09 la exige."""
    if monto <= 0:
        return None
    requisito = seguridad.requiere_autorizacion("DESEMBOLSO", monto, seguridad.contexto_desde_bd(con, fecha))
    if not requisito.requiere_firma:
        st.caption("Este monto no requiere autorización de la Matriz CI-FIN-09.")
        return None
    firmas = ", ".join(seguridad.NOMBRE_CALIDAD[c] for c in requisito.firmas)
    disponibles = _autorizaciones_desembolso(con, sesion, monto)
    if not disponibles:
        st.warning(f"Requiere autorización de {firmas}. Solicítela en «Autorizaciones».")
        return None
    elegida = st.selectbox(f"Autorización ({firmas})", disponibles, key=clave,
                           format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])} · {a['descripcion']}")
    return int(elegida["id"])


# ------------------------------------------------------------------- resumen
def _resumen() -> None:
    hoy = date.today()
    with con_bd() as con:
        try:
            r = tesoreria.reporte_diario(con, hoy)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
        custodia = tesoreria.control_custodia(con, hoy) if tesoreria.fondo_activo(con) else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Saldo bancario (libros)", bs(r["saldo"]))
    c2.metric("SME vigente", bs(r["sme"]))
    c3.metric("Cumplimiento del SME", _pct_txt(r["cumplimiento"]))
    c4.metric("Efectivo en custodia", bs(custodia["total_custodia"]) if custodia else "Sin fondo")
    st.markdown(f"Semáforo FIN-TES-06: {chip(r['semaforo'])}", unsafe_allow_html=True)
    if custodia and custodia["deposito_obligatorio"]:
        st.error(f"Hay {bs(custodia['excedente'])} sobre el tope de caja: deposítelos hoy (CI-FIN-01, Paso 4).")


# ---------------------------------------------------------------------- caja
def _tab_caja(sesion: Sesion) -> None:
    with con_bd() as con:
        fondo = tesoreria.fondo_activo(con)
        registra = _puede(con, sesion, "tesoreria", "CREAR")
        autoriza = _puede(con, sesion, "tesoreria", "AUTORIZAR")
        arquea = _puede(con, sesion, "vigilancia", "CREAR")
        cuentas = [c for c in contable.listar_cuentas_movimiento(con, ejercicio_vigente_o_ultimo(con, date.today()))
                   if c["habilitada"]]
    if fondo is None:
        st.info("No hay fondo de caja constituido (CI-FIN-01, Paso 1).")
        if not registra:
            return
        with con_bd() as con:
            tope = Decimal(seguridad.contexto_desde_bd(con, date.today()).parametros["tope_caja_bs"])
            with st.form("form_fondo"):
                st.subheader(f"Constituir el fondo fijo de {bs(tope)}")
                c1, c2 = st.columns(2)
                fecha = c1.date_input("Fecha", date.today(), format="DD/MM/YYYY", key="fondo_fecha")
                banco = c2.selectbox("Cuenta de origen", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get)
                custodio = st.text_input("Custodio designado")
                resolucion = st.text_input("Resolución de designación del custodio")
                autorizacion = _elegir_autorizacion(con, sesion, tope, fecha, "fondo_aut")
                enviar = st.form_submit_button("Constituir fondo", type="primary")
        if enviar:
            try:
                with con_bd() as con:
                    tesoreria.constituir_fondo(con, sesion, fecha, custodio, resolucion, cuenta_banco=banco,
                                               autorizacion_id=autorizacion)
                st.success("Fondo constituido.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        return

    st.caption(f"Fondo fijo de {bs(Decimal(fondo['monto_fijo_cent']) / 100)} · custodio {fondo['custodio']} · "
               f"{fondo['resolucion_ref']}")
    dia = st.date_input("Día de control", date.today(), format="DD/MM/YYYY", key="caja_dia")
    with con_bd() as con:
        control = tesoreria.control_custodia(con, dia)
    c1, c2, c3 = st.columns(3)
    c1.metric("Fondo disponible", bs(control["fondo_disponible"]))
    c2.metric("Cobros en efectivo", bs(control["efectivo_ventas"]))
    c3.metric("Excedente a depositar", bs(control["excedente"]))

    opciones_gasto = [f"{c['codigo']} — {c['nombre']}" for c in cuentas
                      if c["codigo"].startswith("7.") or c["es_centro"]]
    opciones_ingreso = [f"{c['codigo']} — {c['nombre']}" for c in cuentas
                        if c["codigo"].startswith("5.") or c["codigo"].startswith("1.1.02")]
    if registra:
        g, i, dpt = st.columns(3)
        with g.form("form_gasto", clear_on_submit=True):
            st.markdown("**Gasto menor**")
            monto = st.number_input("Monto (Bs)", min_value=0.0, step=10.0, format="%.2f", key="gm_monto")
            cuenta = st.selectbox("Cuenta de gasto", opciones_gasto, key="gm_cuenta",
                                  index=_indice(opciones_gasto, "7.1.02.01"))
            sufijo = st.text_input("Sufijo (si es centro de costo)", key="gm_sufijo")
            comprobante = st.text_input("N.º de comprobante", key="gm_comp")
            beneficiario = st.text_input("Beneficiario", key="gm_benef")
            detalle = st.text_input("Concepto", key="gm_det")
            if st.form_submit_button("Registrar gasto"):
                try:
                    with con_bd() as con:
                        tesoreria.registrar_gasto_menor(con, sesion, dia, decimal_de(monto), codigo_de(cuenta),
                                                        comprobante, beneficiario, detalle, sufijo=sufijo or None)
                    st.success("Gasto registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with i.form("form_cobro", clear_on_submit=True):
            st.markdown("**Cobro en efectivo**")
            monto = st.number_input("Monto (Bs)", min_value=0.0, step=10.0, format="%.2f", key="ce_monto")
            cuenta = st.selectbox("Cuenta de ingreso", opciones_ingreso, key="ce_cuenta",
                                  index=_indice(opciones_ingreso, "5.1.01.01"))
            documento = st.text_input("Nota de entrega / recibo", key="ce_doc")
            detalle = st.text_input("Concepto", key="ce_det")
            if st.form_submit_button("Registrar cobro"):
                try:
                    with con_bd() as con:
                        tesoreria.registrar_ingreso_efectivo(con, sesion, dia, decimal_de(monto), codigo_de(cuenta),
                                                             documento, detalle)
                    st.success("Cobro registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with dpt.form("form_deposito_caja", clear_on_submit=True):
            st.markdown("**Depósito del excedente**")
            monto = st.number_input("Monto (Bs)", min_value=0.0, value=float(control["excedente"]), step=10.0,
                                    format="%.2f", key="dc_monto")
            banco = st.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="dc_banco")
            boleta = st.text_input("Boleta de depósito", key="dc_boleta")
            if st.form_submit_button("Registrar depósito"):
                try:
                    with con_bd() as con:
                        tesoreria.depositar_excedente(con, sesion, dia, decimal_de(monto), boleta, banco)
                    st.success("Depósito registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)

    st.subheader("Reposición del fondo")
    with con_bd() as con:
        pendientes = tesoreria.gastos_pendientes_reposicion(con)
        reposiciones = tesoreria.listar_reposiciones(con, sesion)
    st.dataframe(tabla(pendientes, [("comprobante", "Comprobante"), ("fecha", "Fecha"), ("beneficiario", "Beneficiario"),
                                    ("descripcion", "Concepto"), ("cuenta_contrapartida", "Cuenta"), ("monto", "Monto"),
                                    ("reposicion_id", "Solicitud")], montos=("monto",), fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    if registra and st.button("Solicitar reposición de los gastos pendientes"):
        try:
            with con_bd() as con:
                rid = tesoreria.solicitar_reposicion(con, sesion, dia)
            st.success(f"Solicitud de reposición n.º {rid} enviada a la Gerencia General.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    for r in [x for x in reposiciones if x["estado"] in ("SOLICITADA", "VALIDADA")]:
        st.markdown(f"Solicitud n.º {r['id']} · {bs(r['total'])} · {chip(r['estado'])}", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        if r["estado"] == "SOLICITADA" and autoriza:
            nota = c3.text_input("Observación", key=f"rep_obs_{r['id']}")
            if c1.button("Validar", key=f"rep_ok_{r['id']}"):
                try:
                    with con_bd() as con:
                        tesoreria.validar_reposicion(con, sesion, int(r["id"]), observacion=nota or None)
                    st.success("Reposición validada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
            if c2.button("Rechazar", key=f"rep_no_{r['id']}"):
                try:
                    with con_bd() as con:
                        tesoreria.validar_reposicion(con, sesion, int(r["id"]), aprobar=False, observacion=nota)
                    st.warning("Reposición rechazada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        if r["estado"] == "VALIDADA" and registra:
            banco = c1.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key=f"rep_b_{r['id']}")
            if c2.button("Ejecutar reposición", key=f"rep_ej_{r['id']}"):
                try:
                    with con_bd() as con:
                        tesoreria.ejecutar_reposicion(con, sesion, int(r["id"]), dia, banco)
                    st.success("Fondo repuesto.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)

    st.subheader("Arqueo sorpresivo (INF-CV-03)")
    with con_bd() as con:
        resumen = tesoreria.arqueos_del_ejercicio(con, ejercicio_vigente_o_ultimo(con, dia))
        arqueos = tesoreria.listar_arqueos(con, sesion)
    st.caption(f"Ejercicio {resumen['ejercicio']}: {resumen['realizados']} arqueos de {resumen['minimo']} mínimos.")
    if arquea:
        with st.form("form_arqueo"):
            c1, c2 = st.columns(2)
            efectivo = c1.number_input("Efectivo físico contado (Bs)", min_value=0.0, step=10.0, format="%.2f")
            comprobantes = c2.number_input("Comprobantes de gasto no repuestos (Bs)", min_value=0.0, step=10.0,
                                           format="%.2f")
            presente = st.text_input("Custodio presente", value=fondo["custodio"])
            observaciones = st.text_input("Observaciones")
            if st.form_submit_button("Registrar acta de arqueo", type="primary"):
                try:
                    with con_bd() as con:
                        a = tesoreria.registrar_arqueo(con, sesion, dia, decimal_de(efectivo), decimal_de(comprobantes),
                                                       presente, observaciones or None)
                    (st.success if a["diferencia"] == 0 else st.error)(
                        f"Diferencia física − contable: {bs(a['diferencia'])}. Límite diario cumplido: "
                        f"{'sí' if a['limite_diario_cumplido'] else 'no'}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.dataframe(tabla(arqueos, [("fecha", "Fecha"), ("total_fisico", "Total físico"),
                                 ("saldo_contable", "Saldo contable"), ("diferencia", "Diferencia"),
                                 ("limite_diario_cumplido", "Límite cumplido"), ("arqueado_por", "Arqueado por")],
                       montos=("total_fisico", "saldo_contable", "diferencia"), fechas=("fecha",)),
                 hide_index=True, use_container_width=True)


# -------------------------------------------------------------------- bancos
def _tab_bancos(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        registra = _puede(con, sesion, "tesoreria", "CREAR")
        saldos = {c: tesoreria.saldo_cuenta(con, c) for c in NOMBRE_BANCO}
        cuentas = [c for c in contable.listar_cuentas_movimiento(con, ejercicio_vigente_o_ultimo(con, hoy))
                   if c["habilitada"] and not c["codigo"].startswith("1.1.01")]
        movimientos = tesoreria.movimientos_banco(con, sesion)
        compromisos = tesoreria.listar_compromisos(con, sesion)
    c1, c2 = st.columns(2)
    for col, (cuenta, saldo) in zip((c1, c2), saldos.items()):
        col.metric(NOMBRE_BANCO[cuenta], bs(saldo))
    opciones = [f"{c['codigo']} — {c['nombre']}" for c in cuentas]

    if registra:
        dep, pago, trf = st.tabs(["Depósito", "Pago", "Transferencia"])
        with dep:
            with st.form("form_deposito", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                fecha = c1.date_input("Fecha", hoy, format="DD/MM/YYYY", key="dep_f")
                monto = c2.number_input("Monto (Bs)", min_value=0.0, step=100.0, format="%.2f", key="dep_m")
                banco = c3.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="dep_b")
                contra = st.selectbox("Contrapartida", opciones, key="dep_c",
                                      index=next((i for i, o in enumerate(opciones) if o.startswith("5.1.01.01")), 0))
                detalle = st.text_input("Descripción", key="dep_d")
                doc = st.text_input("Documento", key="dep_doc")
                if st.form_submit_button("Registrar depósito"):
                    try:
                        with con_bd() as con:
                            tesoreria.registrar_deposito(con, sesion, fecha, decimal_de(monto), banco, codigo_de(contra),
                                                         detalle, doc or None)
                        st.success("Depósito registrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        with pago:
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha", hoy, format="DD/MM/YYYY", key="pag_f")
            monto = decimal_de(c2.number_input("Monto (Bs)", min_value=0.0, step=100.0, format="%.2f", key="pag_m"))
            banco = c3.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="pag_b")
            contra = st.selectbox("Cuenta del gasto o de la deuda", opciones, key="pag_c")
            sufijo = st.text_input("Sufijo (si es centro de costo)", key="pag_s")
            categoria = st.selectbox("Categoría (jerarquía POL-04)", [None] + list(tesoreria.ETIQUETA_JERARQUIA)[:-1],
                                     format_func=lambda c: "— Sin categoría —" if c is None
                                     else tesoreria.ETIQUETA_JERARQUIA[c], key="pag_cat")
            vigentes = [c for c in compromisos if c["estado"] in ("VIGENTE", "AUTORIZADO")]
            compromiso = st.selectbox("Compromiso asociado", [None] + vigentes, key="pag_comp",
                                      format_func=lambda c: "— Ninguno —" if c is None
                                      else f"N.º {c['id']} · {bs(c['monto'])} · {c['descripcion']}")
            beneficiario = st.text_input("Beneficiario", key="pag_ben")
            cheque = st.text_input("N.º de cheque (opcional)", key="pag_ch")
            detalle = st.text_input("Descripción", key="pag_d")
            with con_bd() as con:
                autorizacion = _elegir_autorizacion(con, sesion, monto, fecha, "pag_aut")
            if st.button("Registrar pago", type="primary"):
                try:
                    with con_bd() as con:
                        tesoreria.registrar_pago(con, sesion, fecha, monto, banco, codigo_de(contra), detalle,
                                                 beneficiario, autorizacion_id=autorizacion, nro_cheque=cheque or None,
                                                 categoria_pago=categoria, sufijo=sufijo or None,
                                                 compromiso_id=int(compromiso["id"]) if compromiso else None)
                    st.success("Pago registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with trf:
            with st.form("form_transferencia", clear_on_submit=True):
                c1, c2 = st.columns(2)
                fecha = c1.date_input("Fecha", hoy, format="DD/MM/YYYY", key="trf_f")
                monto = c2.number_input("Monto (Bs)", min_value=0.0, step=100.0, format="%.2f", key="trf_m")
                origen = c1.selectbox("Origen", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="trf_o")
                destino = c2.selectbox("Destino", list(NOMBRE_BANCO)[::-1], format_func=NOMBRE_BANCO.get, key="trf_d")
                detalle = st.text_input("Descripción", key="trf_det")
                if st.form_submit_button("Registrar transferencia"):
                    try:
                        with con_bd() as con:
                            tesoreria.registrar_transferencia(con, sesion, fecha, decimal_de(monto), origen, destino,
                                                              detalle)
                        st.success("Transferencia registrada.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)

    st.subheader("Movimientos bancarios")
    st.dataframe(tabla(movimientos, [("fecha", "Fecha"), ("cuenta_codigo", "Cuenta"), ("tipo", "Tipo"),
                                     ("descripcion", "Descripción"), ("beneficiario", "Beneficiario"),
                                     ("nro_cheque", "Cheque"), ("monto", "Monto"), ("conciliacion_id", "Conciliación")],
                       montos=("monto",), fechas=("fecha",)), hide_index=True, use_container_width=True)

    st.subheader("Compromisos de gasto no recurrente (POL-04 Art. 2)")
    c1, c2, c3 = st.columns(3)
    monto = decimal_de(c1.number_input("Monto del compromiso (Bs)", min_value=0.0, step=100.0, format="%.2f",
                                       key="comp_m"))
    tipo = c2.selectbox("Tipo", list(tesoreria.TIPO_DECISION_COMPROMISO), key="comp_t",
                        format_func={"COMPRA": "Compra", "PERSONAL_EVENTUAL": "Personal eventual",
                                     "OTRO": "Otro gasto no recurrente"}.get)
    fecha = c3.date_input("Fecha", hoy, format="DD/MM/YYYY", key="comp_f")
    if monto > 0:
        try:
            with con_bd() as con:
                ev = tesoreria.validar_compromiso(con, monto, tipo, fecha)
            (st.error if ev.suspendido else st.success)(
                f"{ev.mensaje}  \nSaldo {bs(ev.saldo_bancario)} − compromisos previos {bs(ev.compromisos_previos)} − "
                f"este compromiso {bs(ev.monto)} = {bs(ev.saldo_proyectado)} ({_pct_txt(ev.cumplimiento)} del SME).")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if registra:
        detalle = st.text_input("Descripción del compromiso", key="comp_d")
        if st.button("Registrar compromiso", disabled=monto <= 0):
            try:
                with con_bd() as con:
                    cid, ev, aid = tesoreria.registrar_compromiso(con, sesion, monto, tipo, detalle, fecha)
                st.success(f"Compromiso n.º {cid} registrado ({'suspendido: solicitud de autorización n.º ' + str(aid) if aid else 'vigente'}).")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    st.dataframe(tabla(compromisos, [("id", "N.º"), ("fecha", "Fecha"), ("tipo", "Tipo"), ("descripcion", "Descripción"),
                                     ("monto", "Monto"), ("saldo_proyectado", "Saldo proyectado"),
                                     ("umbral", "Umbral 120 %"), ("estado", "Estado"), ("autorizacion_id", "Autorización")],
                       montos=("monto", "saldo_proyectado", "umbral"), fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    if registra:
        for c in [x for x in compromisos if x["estado"] == "SUSPENDIDO"]:
            if st.button(f"Levantar suspensión del compromiso n.º {c['id']} (autorización escrita firmada)",
                         key=f"conf_{c['id']}"):
                try:
                    with con_bd() as con:
                        tesoreria.confirmar_compromiso(con, sesion, int(c["id"]))
                    st.success("Compromiso autorizado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)

    st.subheader("Jerarquía de pagos en restricción de liquidez")
    st.caption("Cargue el lote de pagos pendientes: se ordena por los 7 niveles de POL-04 Art. 2 y se marca lo que "
               "alcanza a pagarse con el saldo disponible, sin saltar prioridades.")
    lote = st.data_editor(pd.DataFrame({"Referencia": [""], "Descripción": [""], "Categoría": [None], "Monto": [0.0],
                                        "Vencimiento": [None]}), num_rows="dynamic", key="lote_pagos",
                          use_container_width=True, column_config={
                              "Categoría": st.column_config.SelectboxColumn(options=list(tesoreria.ETIQUETA_JERARQUIA)),
                              "Monto": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
                              "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY")})
    saldo_lote = st.number_input("Saldo disponible para el lote (Bs)", min_value=0.0,
                                 value=float(sum(saldos.values(), Decimal(0))), step=1000.0, format="%.2f")
    if st.button("Ordenar lote"):
        try:
            pagos = [tesoreria.PagoPendiente(str(f["Referencia"]), str(f["Descripción"]), f["Categoría"],
                                             decimal_de(f["Monto"]),
                                             f["Vencimiento"] if isinstance(f["Vencimiento"], date) else None)
                     for _, f in lote.iterrows() if f["Categoría"] and decimal_de(f["Monto"]) > 0]
            with con_bd() as con:
                plan = tesoreria.ordenar_pagos(con, pagos, hoy, decimal_de(saldo_lote))
            st.dataframe(tabla(plan["pagos"], [("nivel", "Nivel"), ("nombre_nivel", "Prioridad"),
                                               ("referencia", "Referencia"), ("descripcion", "Descripción"),
                                               ("monto", "Monto"), ("pagable", "Pagable"),
                                               ("saldo_despues", "Saldo después"), ("motivo", "Observación")],
                               montos=("monto", "saldo_despues")), hide_index=True, use_container_width=True)
            st.info(f"Total pagable {bs(plan['total_pagable'])}; saldo final {bs(plan['saldo_final'])}.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)

    st.subheader("Pago de excedentes a socios")
    c1, c2 = st.columns(2)
    monto = decimal_de(c1.number_input("Monto total (Bs)", min_value=0.0, step=1000.0, format="%.2f", key="exc_m"))
    fecha = c2.date_input("Fecha de pago", hoy, format="DD/MM/YYYY", key="exc_f")
    if monto > 0:
        with con_bd() as con:
            control = tesoreria.verificar_pago_excedentes(con, fecha, monto)
        (st.success if control["permitido"] else st.error)(
            f"Saldo posterior {bs(control['saldo_posterior'])} frente al SME {bs(control['sme'])}: "
            + ("procede." if control["permitido"] else "BLOQUEADO (POL-04 Art. 2; CI-FIN-02 Art. 9)."))
        if registra and control["permitido"]:
            with con_bd() as con:
                asambleas = seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                            tipo_decision="DISTRIBUCION_EXCEDENTES")
                desembolsos = _autorizaciones_desembolso(con, sesion, monto)
            if not asambleas or not desembolsos:
                st.warning("Se necesitan la aprobación de la Asamblea (DISTRIBUCION_EXCEDENTES) y la autorización "
                           "del desembolso con firma mancomunada.")
            else:
                ag = st.selectbox("Aprobación de la Asamblea", asambleas,
                                  format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")
                de = st.selectbox("Autorización del desembolso", desembolsos,
                                  format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")
                banco = st.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="exc_b")
                medio = st.radio("Medio", ["TRANSFERENCIA", "CHEQUE", "EFECTIVO"], horizontal=True)
                if st.button("Pagar excedentes", type="primary"):
                    try:
                        with con_bd() as con:
                            tesoreria.pagar_excedentes(con, sesion, fecha, monto, banco, medio=medio,
                                                       autorizacion_asamblea_id=int(ag["id"]),
                                                       autorizacion_desembolso_id=int(de["id"]))
                        st.success("Excedentes pagados.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)


# ------------------------------------------------------------- SME y flujo
def _grafico_serie(serie: list[dict], titulo: str) -> None:
    datos = pd.DataFrame({"Fecha": [pd.Timestamp(p["fecha"]) for p in serie],
                          "Saldo": [float(p["saldo"]) for p in serie],
                          "SME": [float(p["sme"]) for p in serie]}).set_index("Fecha")
    st.caption(titulo)
    # Streamlit asigna los colores en orden alfabético de las series: SME, Saldo.
    st.line_chart(datos, color=["#a3322a", "#1f5c43"])


def _tab_sme_flujo(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        registra = _puede(con, sesion, "tesoreria", "CREAR")
        autoriza = _puede(con, sesion, "tesoreria", "AUTORIZAR")
        fiscaliza = _puede(con, sesion, "tesoreria", "VERIFICAR")

    st.subheader("Reporte diario de saldo (FIN-TES-06)")
    c1, c2 = st.columns(2)
    desde = c1.date_input("Desde", hoy - timedelta(days=30), format="DD/MM/YYYY", key="rd_desde")
    hasta = c2.date_input("Hasta", hoy, format="DD/MM/YYYY", key="rd_hasta")
    try:
        with con_bd() as con:
            serie = tesoreria.serie_saldo_diario(con, desde, hasta)
        _grafico_serie(serie, "Saldo bancario según libros frente al SME vigente")
        ultimo = serie[-1]
        st.markdown(f"{fecha_pantalla(ultimo['fecha'])}: {bs(ultimo['saldo'])} = {_pct_txt(ultimo['cumplimiento'])} del SME "
                    f"{chip(ultimo['semaforo'])}", unsafe_allow_html=True)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
    if autoriza and st.button("Registrar el monitoreo de hoy (Gerencia General)"):
        try:
            with con_bd() as con:
                r = tesoreria.registrar_reporte_diario(con, sesion, hoy)
            (st.error if r["requiere_alerta"] else st.success)(
                f"Monitoreo registrado: {r['semaforo']}" + (" — se emitió la alerta FIN-TES-07." if r["alerta_id"] else "."))
        except ErrorPollolandia as exc:
            mostrar_error(exc)

    st.subheader("Flujo de caja proyectado a dos ciclos (FIN-TES-05)")
    if registra:
        with st.expander("Elaborar un nuevo flujo"):
            with con_bd() as con:
                saldo_hoy = tesoreria.saldo_bancario(con, hoy)
            c1, c2, c3 = st.columns(3)
            elaboracion = c1.date_input("Fecha de elaboración", hoy, format="DD/MM/YYYY", key="fl_el")
            inicio = c2.date_input("Inicio del ciclo actual", hoy, format="DD/MM/YYYY", key="fl_in")
            saldo = c3.number_input("Saldo inicial (Bs)", value=float(saldo_hoy), step=1000.0, format="%.2f",
                                    key="fl_sal")
            descripcion = st.text_input("Descripción", key="fl_desc")
            lineas = st.data_editor(
                pd.DataFrame({"Fecha": [None], "Categoría": [None], "Descripción": [""], "Monto": [0.0]}),
                num_rows="dynamic", use_container_width=True, key="fl_lineas", column_config={
                    "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Categoría": st.column_config.SelectboxColumn(options=list(tesoreria.CATEGORIAS_FLUJO)),
                    "Monto": st.column_config.NumberColumn(min_value=0.0, format="%.2f")})
            if st.button("Guardar flujo"):
                try:
                    datos = [{"fecha": f["Fecha"], "categoria": f["Categoría"], "descripcion": f["Descripción"],
                              "monto": decimal_de(f["Monto"])} for _, f in lineas.iterrows()
                             if f["Categoría"] and isinstance(f["Fecha"], date)]
                    with con_bd() as con:
                        fid = tesoreria.crear_flujo(con, sesion, elaboracion, inicio, decimal_de(saldo), datos,
                                                    descripcion)
                    st.success(f"Flujo n.º {fid} guardado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        flujos = tesoreria.listar_flujos(con, sesion)
    if flujos:
        elegido = st.selectbox("Flujo", flujos, format_func=lambda f: f"N.º {f['id']} · {f['descripcion']} · "
                               f"{fecha_pantalla(f['fecha_elaboracion'])} · {f['estado']}")
        with con_bd() as con:
            ev = tesoreria.evaluar_flujo(con, int(elegido["id"]))
        c1, c2, c3 = st.columns(3)
        c1.metric("Saldo mínimo diario", bs(ev["saldo_minimo"]))
        c1.caption(f"el {fecha_pantalla(ev['fecha_minimo'])}")
        c2.metric("SME en esa fecha", bs(ev["sme_en_minimo"]))
        c3.metric("Días bajo el SME", ev["dias_bajo_sme"])
        if ev["riesgo_bajo_sme"]:
            st.error("El flujo proyecta caer bajo el SME: emita la alerta FIN-TES-07.")
        _grafico_serie(ev["serie"], "Saldo diario proyectado frente al SME")
        filas = [{"concepto": texto, "actual": ev["ciclos"]["ACTUAL"][cat], "siguiente": ev["ciclos"]["SIGUIENTE"][cat]}
                 for cat, texto in tesoreria.CATEGORIAS_FLUJO.items()]
        filas += [{"concepto": "(=) Saldo final proyectado", "actual": ev["ciclos"]["ACTUAL"]["saldo_final"],
                   "siguiente": ev["ciclos"]["SIGUIENTE"]["saldo_final"]},
                  {"concepto": "SME del ejercicio", "actual": ev["ciclos"]["ACTUAL"]["sme"],
                   "siguiente": ev["ciclos"]["SIGUIENTE"]["sme"]}]
        st.dataframe(tabla(filas, [("concepto", "Concepto"), ("actual", f"Ciclo actual (al {fecha_pantalla(ev['ciclos']['ACTUAL']['fin'])})"),
                                   ("siguiente", f"Ciclo siguiente (al {fecha_pantalla(ev['ciclos']['SIGUIENTE']['fin'])})")],
                           montos=("actual", "siguiente")), hide_index=True, use_container_width=True)
        c1, c2, c3 = st.columns(3)
        if registra and ev["riesgo_bajo_sme"] and c1.button("Emitir alerta FIN-TES-07"):
            try:
                with con_bd() as con:
                    aid = tesoreria.emitir_alerta_flujo(con, sesion, int(elegido["id"]), hoy)
                st.success(f"Alerta n.º {aid} emitida.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if autoriza and ev["estado"] == "BORRADOR" and c2.button("Aprobar flujo (Gerencia General)"):
            try:
                with con_bd() as con:
                    tesoreria.aprobar_flujo(con, sesion, int(elegido["id"]))
                st.success("Flujo aprobado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if registra:
            with c3.popover("Medir desvío"):
                corte = st.date_input("Fecha de corte", hoy, format="DD/MM/YYYY", key="dv_f")
                if st.button("Comparar con el saldo real"):
                    try:
                        with con_bd() as con:
                            r = tesoreria.evaluar_desvio(con, sesion, int(elegido["id"]), corte)
                        (st.error if r["alerta"] else st.success)(
                            f"Real {bs(r['saldo_real'])} vs proyectado {bs(r['saldo_proyectado'])}: {_pct_txt(r['desvio'])}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)

    st.subheader("Alertas de liquidez y medidas correctivas (FIN-TES-07 / 08)")
    with con_bd() as con:
        alertas = tesoreria.listar_alertas(con, sesion)
    if not alertas:
        st.info("No hay alertas registradas.")
    for a in alertas:
        with st.expander(f"Alerta n.º {a['id']} · {fecha_pantalla(a['fecha'])} · {a['origen']} · {a['estado']}"):
            st.write(a["descripcion"])
            if a["medida_id"]:
                elegidas = [t for k, t in tesoreria.MEDIDAS_CORRECTIVAS.items() if a[k]]
                st.markdown("Medidas adoptadas: " + "; ".join(elegidas) + f". Resultado: {a['resultado'] or '—'}")
                if fiscaliza and not a["fiscalizada_por"] and st.button("Fiscalizar (CV)", key=f"fis_{a['id']}"):
                    try:
                        with con_bd() as con:
                            tesoreria.fiscalizar_medida(con, sesion, int(a["medida_id"]))
                        st.success("Medida fiscalizada.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
            elif autoriza:
                with st.form(f"medida_{a['id']}"):
                    medidas = st.multiselect("Medidas (POL-04 Art. 4)", list(tesoreria.MEDIDAS_CORRECTIVAS),
                                             format_func=tesoreria.MEDIDAS_CORRECTIVAS.get)
                    aplicacion = st.date_input("Fecha de aplicación", hoy, format="DD/MM/YYYY")
                    resultado = st.text_input("Resultado obtenido")
                    if st.form_submit_button("Registrar medida"):
                        try:
                            with con_bd() as con:
                                tesoreria.registrar_medida_correctiva(con, sesion, int(a["id"]), medidas, aplicacion,
                                                                      resultado or None)
                            st.success("Medida registrada.")
                        except ErrorPollolandia as exc:
                            mostrar_error(exc)


# --------------------------------------------------------------- conciliación
def _tab_conciliacion(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        registra = _puede(con, sesion, "tesoreria", "CREAR")
        autoriza = _puede(con, sesion, "tesoreria", "AUTORIZAR")
        lista = tesoreria.listar_conciliaciones(con, sesion)
    if registra:
        with st.expander("Preparar conciliación del mes (CI-FIN-07)"):
            mes_anterior = hoy.replace(day=1) - timedelta(days=1)
            c1, c2, c3, c4 = st.columns(4)
            banco = c1.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="cb_b")
            anio = int(c2.number_input("Año", value=mes_anterior.year, step=1, key="cb_a"))
            mes = int(c3.number_input("Mes", min_value=1, max_value=12, value=mes_anterior.month, step=1, key="cb_m"))
            extracto = c4.number_input("Saldo del extracto (Bs)", step=100.0, format="%.2f", key="cb_e")
            elaboracion = st.date_input("Fecha de elaboración", hoy, format="DD/MM/YYYY", key="cb_f")
            partidas = st.data_editor(
                pd.DataFrame({"Tipo": [None], "Descripción": [""], "Fecha": [None], "Monto": [0.0]}),
                num_rows="dynamic", use_container_width=True, key="cb_partidas", column_config={
                    "Tipo": st.column_config.SelectboxColumn(options=list(tesoreria.TIPOS_PARTIDA)),
                    "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Monto": st.column_config.NumberColumn(min_value=0.0, format="%.2f")})
            st.caption("Cheques en circulación se restan del extracto; depósitos en tránsito se suman; cargos y abonos "
                       "no registrados ajustan los libros.")
            if st.button("Preparar conciliación", type="primary"):
                try:
                    datos = [tesoreria.PartidaConciliacion(f["Tipo"], f["Descripción"], f["Fecha"], decimal_de(f["Monto"]))
                             for _, f in partidas.iterrows() if f["Tipo"] and isinstance(f["Fecha"], date)]
                    with con_bd() as con:
                        r = tesoreria.preparar_conciliacion(con, sesion, banco, anio, mes, decimal_de(extracto), datos,
                                                            elaboracion)
                    (st.success if r["cuadra"] else st.error)(
                        f"Saldo conciliado libros {bs(r['saldo_conciliado_libros'])} · banco "
                        f"{bs(r['saldo_conciliado_banco'])} · diferencia {bs(r['diferencia'])}. Plazo hasta el "
                        f"{fecha_pantalla(r['fecha_limite'])} ({'dentro' if r['dentro_plazo'] else 'FUERA'} de plazo).")
                    for p in r["partidas_antiguas"]:
                        st.warning(f"Partida en tránsito de {p['dias']} días: {p['descripcion']} — investíguela.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.dataframe(tabla([{**c, "anio": str(c["anio"]), "mes": f"{c['mes']:02d}"} for c in lista],
                       [("id", "N.º"), ("cuenta_codigo", "Cuenta"), ("mes", "Mes"), ("anio", "Año"),
                               ("saldo_libros", "Saldo libros"), ("saldo_extracto", "Saldo extracto"),
                               ("diferencia", "Diferencia"), ("fecha_limite", "Plazo"), ("estado", "Estado"),
                               ("firmado_por", "Firmado por")],
                       montos=("saldo_libros", "saldo_extracto", "diferencia"), fechas=("fecha_limite",)),
                 hide_index=True, use_container_width=True)
    preparadas = [c for c in lista if c["estado"] == "PREPARADA"]
    if not preparadas:
        return
    elegida = st.selectbox("Conciliación preparada", preparadas,
                           format_func=lambda c: f"N.º {c['id']} · {c['cuenta_codigo']} · {c['mes']:02d}/{c['anio']}")
    c1, c2 = st.columns(2)
    if registra and c1.button("Registrar en libros los cargos y abonos bancarios"):
        try:
            with con_bd() as con:
                creados = tesoreria.regularizar_partidas(con, sesion, int(elegida["id"]), elegida["fecha_corte"])
            st.success(f"{len(creados)} asiento(s) registrados.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if autoriza and c2.button("Firmar conciliación (Gerencia General)", type="primary"):
        try:
            with con_bd() as con:
                en_plazo = tesoreria.firmar_conciliacion(con, sesion, int(elegida["id"]))
            (st.success if en_plazo else st.warning)("Conciliación firmada" + ("." if en_plazo else " fuera de plazo."))
        except ErrorPollolandia as exc:
            mostrar_error(exc)


# ------------------------------------------------------------------ formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Genere los formatos del Ciclo 1 en PDF, con el bloque de firmas para su impresión y archivo.")
    ctx = pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                       cargo=CARGO_ROL.get(sesion.rol, sesion.rol),
                       ciclo=st.text_input("N.º de ciclo (opcional)", key="fmt_ciclo"))
    opciones = {"FIN-TES-05": "Flujo de Caja Proyectado", "FIN-TES-06": "Reporte Diario de Saldo Bancario",
                "FIN-TES-07": "Alerta de Riesgo de Liquidez", "FIN-TES-08": "Registro de Medida Correctiva",
                "INF-CV-03": "Acta de Arqueo de Caja", "FIN-INF-02": "Conciliación Bancaria del Mes",
                "REPOSICION": "Comprobante de Reposición de Caja Chica"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda c: f"{c} — {opciones[c]}")
    try:
        with con_bd() as con:
            datos: bytes | None = None
            if codigo == "FIN-TES-05":
                flujos = tesoreria.listar_flujos(con, sesion)
                if flujos:
                    f = st.selectbox("Flujo", flujos, format_func=lambda x: f"N.º {x['id']} · {x['descripcion']}")
                    datos = pdf.fin_tes_05(tesoreria.evaluar_flujo(con, int(f["id"])), ctx, f["aprobado_por"] or "")
            elif codigo == "FIN-TES-06":
                c1, c2 = st.columns(2)
                desde = c1.date_input("Desde", date.today() - timedelta(days=6), format="DD/MM/YYYY", key="f6d")
                hasta = c2.date_input("Hasta", date.today(), format="DD/MM/YYYY", key="f6h")
                datos = pdf.fin_tes_06(tesoreria.serie_saldo_diario(con, desde, hasta), ctx)
            elif codigo in ("FIN-TES-07", "FIN-TES-08"):
                alertas = tesoreria.listar_alertas(con, sesion)
                if codigo == "FIN-TES-08":
                    alertas = [a for a in alertas if a["medida_id"]]
                if alertas:
                    a = st.selectbox("Alerta", alertas, format_func=lambda x: f"N.º {x['id']} · {x['descripcion'][:70]}")
                    datos = pdf.fin_tes_07(a, ctx) if codigo == "FIN-TES-07" else \
                        pdf.fin_tes_08(a, ctx, tesoreria.MEDIDAS_CORRECTIVAS)
            elif codigo == "INF-CV-03":
                arqueos = tesoreria.listar_arqueos(con, sesion)
                if arqueos:
                    a = st.selectbox("Arqueo", arqueos, format_func=lambda x: f"{fecha_pantalla(x['fecha'])} · "
                                     f"diferencia {bs(x['diferencia'])}")
                    fondo = tesoreria.fondo_activo(con)
                    tope = Decimal(fondo["monto_fijo_cent"]) / 100 if fondo else Decimal(0)
                    datos = pdf.inf_cv_03({**a, "dias_con_exceso": []}, pdf.Contexto(
                        date.fromisoformat(a["fecha"]), a["arqueado_por"], "Consejo de Vigilancia", ctx.ciclo), tope)
            elif codigo == "FIN-INF-02":
                conciliaciones = tesoreria.listar_conciliaciones(con, sesion)
                if conciliaciones:
                    c = st.selectbox("Conciliación", conciliaciones,
                                     format_func=lambda x: f"N.º {x['id']} · {x['mes']:02d}/{x['anio']} · {x['estado']}")
                    datos = pdf.fin_inf_02(tesoreria.obtener_conciliacion(con, sesion, int(c["id"])), ctx)
            else:
                reposiciones = tesoreria.listar_reposiciones(con, sesion)
                if reposiciones:
                    r = st.selectbox("Reposición", reposiciones, format_func=lambda x: f"N.º {x['id']} · {bs(x['total'])}")
                    gastos = [g for g in tesoreria.movimientos_caja(con, sesion)
                              if g["tipo"] == "GASTO_MENOR" and g["reposicion_id"] == r["id"]]
                    fondo = con.execute("SELECT custodio FROM fondos_caja WHERE id = ?", (r["fondo_id"],)).fetchone()
                    datos = pdf.comprobante_reposicion(r, gastos, ctx, fondo[0] if fondo else "")
        if datos is None:
            st.info("Aún no hay registros para este formato.")
        else:
            st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                               mime="application/pdf", type="primary")
    except (ErrorPollolandia, ValueError) as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 1 — Tesorería."""
    st.header("Tesorería · Ciclo 1")
    _resumen()
    caja, bancos, sme, conciliacion, formatos = st.tabs(["Caja", "Bancos", "SME y flujo", "Conciliación", "Formatos"])
    with caja:
        _tab_caja(sesion)
    with bancos:
        _tab_bancos(sesion)
    with sme:
        _tab_sme_flujo(sesion)
    with conciliacion:
        _tab_conciliacion(sesion)
    with formatos:
        _tab_formatos(sesion)
