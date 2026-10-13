"""Páginas de sistema (Fase 2): inicio, contraseña, usuarios, bitácora y
bandeja de autorizaciones CI-FIN-09 con suplencia CI-FIN-17.

Solo llaman a ``servicios/``.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config
from db.conexion import ejercicio_vigente_o_ultimo, verificar_conexion
from modulos.comun import bs, con_bd, decimal_de, mostrar_error, tabla
from servicios import auditoria, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import (
    DESCRIPCION_DECISION, NOMBRE_CALIDAD, RECURSO_DECISION, Cotizacion, Sesion, Suplencia, TipoDecision,
)
from utils.formato import fecha_pantalla

NOMBRE_ROL = {"GG": "Gerencia General", "JF": "Jefe de Finanzas y Comercialización", "UT": "Unidad Técnica",
              "CA": "Consejo de Administración", "CV": "Consejo de Vigilancia", "AG": "Asamblea General"}


# ------------------------------------------------------------------ inicio
def pagina_inicio(sesion: Sesion) -> None:
    st.header(f"Bienvenida/o, {sesion.nombre_completo}")
    st.caption(f"{NOMBRE_ROL.get(sesion.rol, sesion.rol)}" + (f" · {sesion.cargo}" if sesion.cargo else ""))
    hoy = date.today()
    with con_bd() as con:
        ejercicio = ejercicio_vigente_o_ultimo(con, hoy)
        pendientes = []
        if seguridad.tiene_permiso(con, sesion.rol, "tesoreria", "VER"):
            orden = seguridad.orden_suplencia(con, sesion)
            for a in seguridad.listar_autorizaciones(con, sesion, estado="PENDIENTE"):
                if sesion.rol in a["faltan"] or (orden and "GG" in a["faltan"]):
                    pendientes.append(a)
    c1, c2, c3 = st.columns(3)
    c1.metric("Versión", config.VERSION)
    c2.metric("Ejercicio", str(ejercicio), f"1-jul-{ejercicio} a 30-jun-{ejercicio + 1}", delta_color="off")
    c3.metric("Autorizaciones que esperan su firma", len(pendientes))
    if pendientes:
        st.info("Tiene solicitudes pendientes en «Autorizaciones».")
    estado = verificar_conexion()
    with st.expander("Estado de la base de datos"):
        st.table({"Parámetro": ["Archivo", "Claves foráneas", "Modo de journal", "Versión de SQLite"],
                  "Valor": [estado["ruta"], "Activadas" if estado["foreign_keys"] else "Desactivadas",
                            str(estado["journal_mode"]).upper(), estado["version_sqlite"]]})
    st.caption(config.FASE_ACTUAL)


# -------------------------------------------------------------- contraseña
def pagina_password(sesion: Sesion, forzado: bool = False) -> Sesion | None:
    """Formulario de cambio de contraseña. Devuelve la sesión actualizada al cambiarla."""
    st.header("Cambiar contraseña")
    if forzado:
        st.warning("Debe cambiar su contraseña antes de continuar (contraseña temporal o con más de 90 días, "
                   "MAN-ARC-01 §5.2).")
    with st.form("form_password", clear_on_submit=True):
        actual = st.text_input("Contraseña actual", type="password")
        nueva = st.text_input("Nueva contraseña", type="password",
                              help="Mínimo 8 caracteres, con letras y números.")
        repetida = st.text_input("Repita la nueva contraseña", type="password")
        enviado = st.form_submit_button("Cambiar contraseña", type="primary")
    if enviado:
        if nueva != repetida:
            st.error("Las contraseñas nuevas no coinciden.")
            return None
        try:
            with con_bd() as con:
                actualizada = seguridad.cambiar_password(con, sesion, actual, nueva)
            st.success("Contraseña cambiada.")
            return actualizada
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    return None


# ---------------------------------------------------------------- usuarios
def pagina_usuarios(sesion: Sesion) -> None:
    st.header("Usuarios")
    with con_bd() as con:
        try:
            usuarios = seguridad.listar_usuarios(con, sesion)
        except ErrorPollolandia as exc:
            mostrar_error(exc)
            return
        socios = seguridad.listar_socios(con)
    st.dataframe(tabla(usuarios, [("nombre_usuario", "Usuario"), ("nombre_completo", "Nombre"), ("rol", "Rol"),
                                  ("cargo", "Cargo cooperativo"), ("activo", "Activo"),
                                  ("intentos_fallidos", "Intentos fallidos"), ("bloqueado_hasta", "Bloqueado hasta"),
                                  ("ultimo_cambio_password", "Último cambio de contraseña")]),
                 hide_index=True, use_container_width=True)
    opciones_socio = [None] + socios
    alta, edicion = st.tabs(["Nuevo usuario", "Modificar usuario"])
    with alta:
        with st.form("form_alta", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nombre_usuario = c1.text_input("Usuario")
            rol = c2.selectbox("Rol", seguridad.ROLES_VALIDOS, format_func=lambda r: f"{r} — {NOMBRE_ROL[r]}")
            nombre = st.text_input("Nombre completo")
            socio = st.selectbox("Socio vinculado (obligatorio para miembros de Consejos y suplentes)",
                                 opciones_socio, format_func=lambda x: "— Ninguno —" if x is None else
                                 f"N.º {x['numero']} · {x['nombre_completo']} · {x['cargo_cooperativo'] or 'sin cargo'}")
            temporal = st.text_input("Contraseña temporal", type="password")
            if st.form_submit_button("Crear usuario", type="primary"):
                try:
                    with con_bd() as con:
                        seguridad.crear_usuario(con, sesion, nombre_usuario, nombre, rol, temporal,
                                                socio["id"] if socio else None)
                    st.success(f"Usuario {nombre_usuario} creado. Deberá cambiar la contraseña al ingresar.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with edicion:
        elegido = st.selectbox("Usuario", usuarios, format_func=lambda u: f"{u['nombre_usuario']} · {u['nombre_completo']}")
        c1, c2 = st.columns(2)
        nuevo_rol = c1.selectbox("Rol", seguridad.ROLES_VALIDOS, index=seguridad.ROLES_VALIDOS.index(elegido["rol"]),
                                 key="rol_edicion")
        activo = c2.checkbox("Activo", value=bool(elegido["activo"]), key="activo_edicion")
        if st.button("Guardar cambios"):
            try:
                with con_bd() as con:
                    seguridad.actualizar_usuario(con, sesion, int(elegido["id"]), rol=nuevo_rol, activo=activo)
                st.success("Usuario actualizado.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        temporal = st.text_input("Nueva contraseña temporal (también desbloquea)", type="password", key="temp_edicion")
        if st.button("Restablecer contraseña"):
            try:
                with con_bd() as con:
                    seguridad.restablecer_password(con, sesion, int(elegido["id"]), temporal)
                st.success("Contraseña restablecida; el usuario deberá cambiarla al ingresar.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ---------------------------------------------------------------- bitácora
def pagina_bitacora(sesion: Sesion) -> None:
    st.header("Bitácora de auditoría")
    st.caption("Registro inmutable de toda operación: usuario, rol, fecha, tabla, acción y valores antes y después.")
    c1, c2, c3 = st.columns(3)
    tabla_f = c1.text_input("Tabla", placeholder="asientos, usuarios…")
    usuario_f = c2.text_input("Usuario")
    accion_f = c3.selectbox("Acción", [""] + sorted(auditoria.ACCIONES_VALIDAS))
    c4, c5 = st.columns(2)
    desde = c4.date_input("Desde", None, format="DD/MM/YYYY")
    hasta = c5.date_input("Hasta", None, format="DD/MM/YYYY")
    try:
        with con_bd() as con:
            eventos = auditoria.consultar_bitacora(con, sesion, tabla=tabla_f or None, usuario=usuario_f or None,
                                                   accion=accion_f or None, desde=desde, hasta=hasta)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    st.dataframe(pd.DataFrame([{
        "Fecha y hora": e["fecha_hora"], "Usuario": e["usuario"], "Rol": e["rol"] or "", "Tabla": e["tabla"],
        "Registro": e["registro_id"] or "", "Acción": e["accion"], "Detalle": e["detalle"] or "",
        "Antes": "" if e["valores_antes"] is None else str(e["valores_antes"]),
        "Después": "" if e["valores_despues"] is None else str(e["valores_despues"]),
    } for e in eventos]), hide_index=True, use_container_width=True)


# ---------------------------------------------------------- autorizaciones
def _puede_solicitar(con, rol: str) -> list[TipoDecision]:
    return [t for t in TipoDecision
            if seguridad.tiene_permiso(con, rol, RECURSO_DECISION[t], "CREAR")
            or seguridad.tiene_permiso(con, rol, RECURSO_DECISION[t], "AUTORIZAR")]


def _nueva_solicitud(sesion: Sesion) -> None:
    with con_bd() as con:
        tipos = _puede_solicitar(con, sesion.rol)
    if not tipos:
        st.info("Su rol no inicia solicitudes de autorización.")
        return
    tipo = st.selectbox("Tipo de decisión (CI-FIN-09)", tipos, format_func=lambda t: DESCRIPCION_DECISION[t])
    c1, c2 = st.columns(2)
    fecha = c1.date_input("Fecha de la decisión", date.today(), format="DD/MM/YYYY", key="sol_fecha")
    monto = c2.number_input("Monto (Bs)", min_value=0.0, step=100.0, format="%.2f", key="sol_monto")
    descripcion = st.text_input("Descripción", key="sol_desc")
    referencia = st.text_input("Referencia (formato FIN-*, OC, contrato…)", key="sol_ref")
    datos: dict = {}
    documentos: dict[str, str] = {}
    cotizaciones: list[Cotizacion] = []
    if tipo in (TipoDecision.DESEMBOLSO, TipoDecision.COMPRA_INSUMOS, TipoDecision.CONTRATACION_EVENTUAL,
                TipoDecision.COMPROMISO_GASTO):
        obligatorio = tipo is TipoDecision.COMPROMISO_GASTO
        if obligatorio or st.checkbox("Informar saldo de caja proyectado (control del 120 % del SME)"):
            datos["saldo_proyectado"] = decimal_de(st.number_input("Saldo proyectado después del compromiso (Bs)",
                                                                   min_value=0.0, step=1000.0, format="%.2f"))
    if tipo is TipoDecision.CAPEX_NO_PREVISTO:
        datos["previsto_en_plan"] = st.radio("¿Está en el Plan CAPEX anual aprobado?", [False, True],
                                             format_func=lambda v: "Sí" if v else "No", horizontal=True)
    if tipo is TipoDecision.VARIACION_CAPEX:
        datos["monto_aprobado"] = decimal_de(st.number_input("Monto CAPEX aprobado originalmente (Bs)",
                                                             min_value=0.0, step=1000.0, format="%.2f"))
    if tipo is TipoDecision.CREDITO_HORECA:
        datos["dias_credito"] = int(st.number_input("Días de crédito", min_value=1, max_value=60, value=1))
    if tipo is TipoDecision.DISTRIBUCION_EXCEDENTES:
        documentos["INF-CV-04"] = st.text_input("Referencia del informe previo del CV (INF-CV-04)")
    if tipo is TipoDecision.COMPRA_INSUMOS:
        st.caption("Cotizaciones (obligatorias si la compra supera el umbral de PR-FIN-03)")
        cot = st.data_editor(pd.DataFrame({"Proveedor": ["", "", ""], "Monto": [0.0, 0.0, 0.0], "Documento": ["", "", ""]}),
                             num_rows="dynamic", key="cotizaciones", use_container_width=True)
        cotizaciones = [Cotizacion(str(f["Proveedor"]), decimal_de(f["Monto"]), str(f["Documento"]) or None)
                        for _, f in cot.iterrows() if str(f["Proveedor"] or "").strip()]
    try:
        with con_bd() as con:
            ctx = seguridad.contexto_desde_bd(con, fecha, **datos)
            requisito = seguridad.requiere_autorizacion(tipo, decimal_de(monto), ctx)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    firmas = ", ".join(NOMBRE_CALIDAD[c] for c in requisito.firmas) or "ninguna (queda autorizada al registrarla)"
    detalle = [f"**Firmas exigidas:** {firmas}"]
    if requisito.autorizacion_escrita:
        detalle.append("Autorización **expresa y escrita**.")
    if requisito.autorizacion_especifica:
        detalle.append("Autorización **específica** del Consejo de Administración.")
    if requisito.cotizaciones_minimas:
        detalle.append(f"Mínimo **{requisito.cotizaciones_minimas} cotizaciones**.")
    if requisito.mayoria_calificada:
        detalle.append(f"Mayoría de **{requisito.mayoria_calificada[0]}/{requisito.mayoria_calificada[1]}**.")
    if requisito.dia_semana:
        detalle.append("Solo se autoriza el **lunes**.")
    if requisito.informar_a:
        detalle.append("Se informa al Consejo de Administración.")
    st.info("  \n".join(detalle + [f"· {f}" for f in requisito.fundamentos]))
    if st.button("Registrar solicitud", type="primary"):
        try:
            with con_bd() as con:
                aid = seguridad.solicitar_autorizacion(con, sesion, tipo, decimal_de(monto), descripcion, ctx,
                                                       referencia=referencia or None, documentos=documentos,
                                                       cotizaciones=cotizaciones)
            st.success(f"Solicitud n.º {aid} registrada.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)


def _pendientes(sesion: Sesion) -> None:
    with con_bd() as con:
        orden = seguridad.orden_suplencia(con, sesion)
        todas = seguridad.listar_autorizaciones(con, sesion, estado="PENDIENTE")
    mias = [a for a in todas if sesion.rol in a["faltan"] or (orden and "GG" in a["faltan"])]
    if not mias:
        st.info("No hay solicitudes que esperen su firma.")
        return
    for a in mias:
        req = a["requisito"]
        with st.expander(f"N.º {a['id']} · {DESCRIPCION_DECISION[TipoDecision(a['tipo_decision'])]} · "
                         f"{bs(a['monto'])} · {a['descripcion']}"):
            st.markdown(f"Solicitado por **{a['solicitante']}** el {fecha_pantalla(a['solicitado_en'])}. "
                        f"Faltan: **{', '.join(NOMBRE_CALIDAD[c] for c in a['faltan'])}**.")
            for f in req.fundamentos:
                st.caption(f)
            puede_titular = sesion.rol in a["faltan"]
            usar_suplencia = False
            if orden and "GG" in a["faltan"]:
                usar_suplencia = st.checkbox(
                    f"Firmar como suplente de la Gerencia General (CI-FIN-17, orden {orden})",
                    value=not puede_titular, key=f"sup_{a['id']}", disabled=not puede_titular)
            with st.form(f"firma_{a['id']}"):
                decision = st.radio("Decisión", ["APRUEBA", "RECHAZA"], horizontal=True, key=f"dec_{a['id']}")
                documento = st.text_input("Documento escrito / acta / resolución", key=f"doc_{a['id']}")
                observacion = st.text_input("Observación", key=f"obs_{a['id']}")
                votos_favor = votos_emitidos = None
                if sesion.rol == "AG" and req.mayoria_calificada:
                    c1, c2 = st.columns(2)
                    votos_favor = int(c1.number_input("Votos a favor", min_value=0, step=1, key=f"vf_{a['id']}"))
                    votos_emitidos = int(c2.number_input("Votos emitidos", min_value=1, step=1, key=f"ve_{a['id']}"))
                suplencia = None
                if usar_suplencia:
                    causa = st.text_input("Causa de la suplencia (ausencia o indisponibilidad de la GG)",
                                          key=f"causa_{a['id']}")
                    omision = st.text_input("Motivo por el que no actúa el Presidente del CA", key=f"om_{a['id']}") \
                        if orden == 2 else None
                    suplencia = Suplencia(causa=causa, constancia_ref=documento, motivo_omision_presidente=omision)
                if st.form_submit_button("Firmar", type="primary"):
                    try:
                        with con_bd() as con:
                            estado = seguridad.firmar_autorizacion(
                                con, sesion, int(a["id"]), decision=decision, documento_ref=documento or None,
                                observacion=observacion or None, suplencia=suplencia,
                                votos_favor=votos_favor, votos_emitidos=votos_emitidos)
                        st.success(f"Firma registrada. Estado: {estado}.")
                        if suplencia:
                            st.warning("Comunique la decisión a la Gerencia General dentro de las 24 horas y "
                                       "regístrelo en la pestaña «Suplencias».")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)


def _historial(sesion: Sesion) -> None:
    with con_bd() as con:
        todas = seguridad.listar_autorizaciones(con, sesion)
    if not todas:
        st.info("Aún no hay solicitudes.")
        return
    st.dataframe(pd.DataFrame([{
        "N.º": a["id"], "Decisión": DESCRIPCION_DECISION[TipoDecision(a["tipo_decision"])], "Monto": bs(a["monto"]),
        "Estado": a["estado"], "Descripción": a["descripcion"], "Solicitante": a["solicitante"],
        "Firmas": "; ".join(f"{NOMBRE_CALIDAD[f['calidad']]}: {f['nombre_completo']} ({f['decision']}"
                            f"{', suplencia' if f['es_suplencia'] else ''})" for f in a["firmas"]),
        "Faltan": ", ".join(NOMBRE_CALIDAD[c] for c in a["faltan"]),
    } for a in todas]), hide_index=True, use_container_width=True)


def _suplencias(sesion: Sesion) -> None:
    with con_bd() as con:
        pendientes = seguridad.suplencias_pendientes(con, sesion)
    if not pendientes:
        st.info("No hay autorizaciones por suplencia pendientes de revisión de la GG.")
        return
    for p in pendientes:
        alerta = " · ⚠ comunicación fuera de plazo" if p["comunicacion_fuera_de_plazo"] else ""
        st.markdown(f"**Firma {p['id']}** · solicitud {p['autorizacion_id']} · {p['nombre_completo']} · "
                    f"{bs(p['monto'])} · {p['causa_suplencia']}{alerta}")
        c1, c2 = st.columns(2)
        if p["usuario_id"] == sesion.usuario_id and not p["comunicado_gg_en"]:
            if c1.button("Registrar comunicación a la GG", key=f"com_{p['id']}"):
                try:
                    with con_bd() as con:
                        en_plazo = seguridad.registrar_comunicacion_suplencia(con, sesion, int(p["id"]))
                    (st.success if en_plazo else st.warning)("Comunicación registrada" +
                                                             ("." if en_plazo else " fuera del plazo de 24 h."))
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        if sesion.rol == "GG" and c2.button("Marcar como revisada (CI-FIN-17 Art. 4)", key=f"rev_{p['id']}"):
            try:
                with con_bd() as con:
                    seguridad.revisar_suplencia(con, sesion, int(p["id"]))
                st.success("Suplencia revisada.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)


def pagina_autorizaciones(sesion: Sesion) -> None:
    st.header("Autorizaciones (Matriz CI-FIN-09)")
    pend, nueva, hist, sup = st.tabs(["Pendientes de mi firma", "Nueva solicitud", "Historial", "Suplencias"])
    with pend:
        _pendientes(sesion)
    with nueva:
        _nueva_solicitud(sesion)
    with hist:
        _historial(sesion)
    with sup:
        _suplencias(sesion)
