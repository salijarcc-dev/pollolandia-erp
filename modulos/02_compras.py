"""Módulo 2 — Compras e inventarios (Fase 4, Ciclo 2). Pantalla con pestañas:
Proveedores, Inventarios, Stock y alertas, Órdenes de compra, Recepción,
Pagos, Bajas y Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. Cada acción se muestra según
los permisos del rol: Finanzas prepara y registra, la Gerencia General
autoriza y emite, la Unidad Técnica cuenta y recibe, el CV visa las bajas.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_compras as pdf
from servicios import compras, inventarios, seguridad, tesoreria
from servicios.compras import CotizacionOC, LineaOC, LineaRecepcion
from servicios.excepciones import ErrorPollolandia
from servicios.inventarios import formato_cantidad
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

NOMBRE_BANCO = {"1.1.01.03": "1.1.01.03 — Banco cuenta corriente", "1.1.01.04": "1.1.01.04 — Banco caja de ahorros"}
CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
ESTADO_PROVEEDOR = {"HABILITADO": "pl-ok", "OBSERVADO": "pl-mal", "INACTIVO": "pl-info"}


def _puede(con, sesion: Sesion, recurso: str, accion: str) -> bool:
    return seguridad.tiene_permiso(con, sesion.rol, recurso, accion)


def _pct_txt(valor: Decimal | None) -> str:
    return "—" if valor is None else f"{Decimal(valor):.2f} %".replace(".", ",")


def _proximo_dia(dia_iso: int, desde: date) -> date:
    return desde + timedelta(days=(dia_iso - desde.isoweekday()) % 7)


def _nombre_insumo(i: dict) -> str:
    return f"{i['codigo']} — {i['nombre']} ({i['unidad']})"


# -------------------------------------------------------------------- resumen
def _resumen(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        r = compras.resumen_compras(con, sesion)
        cac = compras.indicador_cac(con, hoy.year, hoy.month)
    c1, c2, c3, c4 = st.columns([1, 1, 2, 1])
    c1.metric("Órdenes por emitir", r["ordenes_preparadas"])
    c2.metric("Órdenes en curso", r["ordenes_abiertas"])
    c3.metric("Cuentas por pagar", bs(r["saldo_por_pagar"]))
    c4.metric("Alertas de stock", r["alertas_abiertas"])
    if cac["semaforo"]:
        st.markdown(f"Indicador CAC {hoy.month:02d}/{hoy.year}: {_pct_txt(cac['porcentaje'])} {chip(cac['semaforo'])} "
                    f"({cac['puntuales']} de {cac['programadas']} órdenes emitidas el lunes programado)",
                    unsafe_allow_html=True)
    else:
        st.caption(f"Indicador CAC {hoy.month:02d}/{hoy.year}: sin órdenes programadas en el mes.")
    if r["alertas_abiertas"]:
        st.error("Hay insumos bajo el punto de reorden o con rotación excedida: revise «Stock y alertas» (CI-FIN-16).")


# ----------------------------------------------------------------- proveedores
def _tab_proveedores(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        lista = compras.listar_proveedores(con, sesion, hoy)
        registra = _puede(con, sesion, "compras", "CREAR") and sesion.rol != "GG"
        edita = _puede(con, sesion, "compras", "EDITAR")
    for p in lista:
        p["tipo_txt"] = compras.TIPOS_PROVEEDOR[p["tipo"]].split(" (")[0]
        p["certificado"] = (f"{p['tipo_certificado'] or '—'} {p['nro_certificado'] or ''}").strip()
    st.dataframe(tabla(lista, [("nombre", "Proveedor"), ("tipo_txt", "Tipo"), ("nit", "NIT"), ("ci", "C.I."),
                               ("comunidad", "Comunidad"), ("certificado", "Certificado"),
                               ("certificado_vigente_hasta", "Vigente hasta"), ("estado_a_la_fecha", "Estado"),
                               ("motivo_a_la_fecha", "Observación")], fechas=("certificado_vigente_hasta",)),
                 hide_index=True, use_container_width=True)
    observados = [p for p in lista if p["estado_a_la_fecha"] == "OBSERVADO"]
    if observados:
        st.warning(f"{len(observados)} proveedor(es) observado(s): no se les compra hasta que regularicen "
                   "(PET-RAU §2.2).")
    if registra:
        with st.expander("Registrar proveedor o ficha de pequeño productor"):
            with st.form("form_proveedor", clear_on_submit=True):
                tipo = st.radio("Tipo", list(compras.TIPOS_PROVEEDOR), format_func=compras.TIPOS_PROVEEDOR.get,
                                horizontal=True)
                c1, c2, c3 = st.columns(3)
                nombre = c1.text_input("Nombre o razón social")
                nit = c2.text_input("NIT (empresa)")
                ci = c3.text_input("C.I. (pequeño productor)")
                comunidad = c1.text_input("Comunidad")
                hectareas = c2.text_input("Hectáreas cultivadas")
                producto = c3.text_input("Producto")
                cert = c1.selectbox("Certificado", [None, *compras.CERTIFICADOS],
                                    format_func=lambda c: "— Ninguno —" if c is None else compras.CERTIFICADOS[c])
                nro = c2.text_input("N.º de certificado")
                vigencia = c3.date_input("Vigente hasta", value=None, format="DD/MM/YYYY")
                pago = st.text_input("Copia del último pago RAU (referencia)")
                if st.form_submit_button("Registrar", type="primary"):
                    try:
                        with con_bd() as con:
                            _, estado = compras.registrar_proveedor(
                                con, sesion, tipo, hoy, nombre=nombre, nit=nit or None, ci=ci or None,
                                comunidad=comunidad or None, hectareas=hectareas or None, producto=producto or None,
                                tipo_certificado=cert, nro_certificado=nro or None, certificado_vigente_hasta=vigencia,
                                ultimo_pago_rau_ref=pago or None)
                        (st.success if estado == "HABILITADO" else st.warning)(f"Proveedor registrado: {estado}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if edita and lista:
        with st.expander("Actualizar certificado de un proveedor"):
            elegido = st.selectbox("Proveedor", lista, format_func=lambda p: f"{p['nombre']} · {p['estado_a_la_fecha']}",
                                   key="prov_upd")
            with st.form("form_prov_upd"):
                c1, c2, c3 = st.columns(3)
                opciones_cert = [None, *compras.CERTIFICADOS]
                cert = c1.selectbox("Certificado", opciones_cert, key="pu_cert",
                                    index=opciones_cert.index(elegido["tipo_certificado"]),
                                    format_func=lambda c: "— Ninguno —" if c is None else compras.CERTIFICADOS[c])
                nro = c2.text_input("N.º de certificado", value=elegido["nro_certificado"] or "")
                vigencia = c3.date_input("Vigente hasta", value=date.fromisoformat(elegido["certificado_vigente_hasta"])
                                         if elegido["certificado_vigente_hasta"] else None, format="DD/MM/YYYY")
                pago = st.text_input("Copia del último pago RAU", value=elegido["ultimo_pago_rau_ref"] or "")
                baja = st.checkbox("Dar de baja al proveedor")
                if st.form_submit_button("Guardar"):
                    try:
                        with con_bd() as con:
                            estado = compras.actualizar_proveedor(
                                con, sesion, int(elegido["id"]), hoy, inactivar=baja, tipo_certificado=cert,
                                nro_certificado=nro or None, certificado_vigente_hasta=vigencia,
                                ultimo_pago_rau_ref=pago or None)
                        st.success(f"Ficha actualizada: {estado}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)


# ----------------------------------------------------------------- inventarios
def _tab_inventarios(sesion: Sesion) -> None:
    hoy = date.today()
    fecha = st.date_input("Fecha de consulta", hoy, format="DD/MM/YYYY", key="inv_fecha")
    with con_bd() as con:
        filas = inventarios.existencias(con, sesion, fecha)
        conciliacion = inventarios.conciliar_con_contabilidad(con, sesion)
        insumos = inventarios.listar_insumos(con, sesion)
        registra = _puede(con, sesion, "inventarios", "EDITAR") and _puede(con, sesion, "contabilidad", "CREAR")
        ciclo = inventarios.ciclo_de_fecha(con, fecha)
    st.caption(f"{ciclo['etiqueta']}: {fecha_pantalla(ciclo['inicio'])} a {fecha_pantalla(ciclo['fin'])}. Sistema "
               "perpetuo con Costo Promedio Ponderado recalculado en cada entrada (PC-07).")
    for f in filas:
        f["existencia"] = formato_cantidad(f["cantidad_mil"], f["unidad"])
        f["pr"] = formato_cantidad(f["punto_reorden_mil"], f["unidad"]) if f["punto_reorden_mil"] is not None else "—"
        f["estado_pr"] = "BAJO EL PR" if f["bajo_pr"] else ("" if f["punto_reorden_mil"] is None else "Cubierto")
        f["cpp_txt"] = bs(f["cpp"]) if f["cantidad_mil"] else "—"
    st.dataframe(tabla(filas, [("cuenta", "Cuenta"), ("nombre", "Insumo"), ("existencia", "Existencia"),
                               ("cpp_txt", "CPP"), ("valor", "Valor"), ("pr", "Punto de reorden"),
                               ("estado_pr", "Estado")], montos=("valor",)), hide_index=True, use_container_width=True)
    diferencias = [c for c in conciliacion if c["diferencia"] != 0]
    if diferencias:
        st.error("El kárdex no coincide con la contabilidad en: " + ", ".join(c["cuenta"] for c in diferencias))
    else:
        st.caption("Kárdex conciliado con los saldos contables de las cuentas 1.1.03.xx.")

    st.subheader("Kárdex por insumo")
    elegido = st.selectbox("Insumo", insumos, format_func=_nombre_insumo, key="kx_ins")
    with con_bd() as con:
        movimientos = inventarios.kardex(con, sesion, int(elegido["id"]))
    for m in movimientos:
        m["entrada"] = formato_cantidad(m["cantidad_mil"]) if m["tipo"] == "ENTRADA_COMPRA" else ""
        m["salida"] = "" if m["tipo"] == "ENTRADA_COMPRA" else formato_cantidad(m["cantidad_mil"])
        m["saldo_cant"] = formato_cantidad(m["saldo_cantidad_mil"])
        m["cpp_bs"] = bs(Decimal(m["cpp"]))
    st.dataframe(tabla(movimientos, [("fecha", "Fecha"), ("tipo", "Movimiento"), ("documento_ref", "Documento"),
                                     ("entrada", "Entrada"), ("salida", "Salida"), ("valor", "Valor"),
                                     ("saldo_cant", "Saldo"), ("saldo_valor", "Saldo Bs"), ("cpp_bs", "CPP")],
                       montos=("valor", "saldo_valor"), fechas=("fecha",)), hide_index=True, use_container_width=True)

    if not registra:
        return
    c1, c2 = st.columns(2)
    with c1.form("form_salida", clear_on_submit=True):
        st.markdown("**Salida de consumo al centro de costo**")
        consumibles = [i for i in insumos if i["comprable"] and not i["transferencia_inmediata"]]
        ins = st.selectbox("Insumo", consumibles, format_func=_nombre_insumo, key="sal_ins")
        f_sal = st.date_input("Fecha", hoy, format="DD/MM/YYYY", key="sal_f")
        cantidad = st.number_input("Cantidad", min_value=0.0, step=1.0, format="%g", key="sal_c")
        centro = st.selectbox("Centro de costo", [inventarios.CENTRO_CARNE, inventarios.CENTRO_ALIMENTO], key="sal_cc")
        documento = st.text_input("Documento (bitácora FIN-TRA-02)", key="sal_doc")
        if st.form_submit_button("Registrar salida"):
            try:
                with con_bd() as con:
                    r = inventarios.registrar_salida(con, sesion, int(ins["id"]), f_sal, decimal_de(cantidad),
                                                     centro_costo=centro, documento_ref=documento or None)
                st.success(f"Salida valuada al CPP: {bs(r['valor'])}.")
                if r["alerta_id"]:
                    st.error("La existencia quedó bajo el punto de reorden: se emitió la alerta de compra de "
                             "emergencia (CI-FIN-16 Art. 8).")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    with c2.form("form_pr"):
        st.markdown("**Punto de reorden (CI-FIN-16)**")
        criticos = [i for i in insumos if i["grupo_critico"]]
        ins = st.selectbox("Insumo crítico", criticos, format_func=_nombre_insumo, key="pr_ins")
        f_pr = st.date_input("Inicio de vigencia (inicio del ciclo)", ciclo["inicio"], format="DD/MM/YYYY", key="pr_f")
        dias = st.number_input("Días de reposición del proveedor", min_value=1, step=1,
                               value=int(ins["dias_reposicion"] or 1), key="pr_d")
        proyectado = st.number_input("Consumo diario proyectado (solo si no hay consumo del ciclo anterior)",
                                     min_value=0.0, step=0.1, format="%.4f", key="pr_p")
        if st.form_submit_button("Calcular y registrar"):
            try:
                with con_bd() as con:
                    if int(dias) != (ins["dias_reposicion"] or 0):
                        inventarios.actualizar_insumo(con, sesion, int(ins["id"]), dias_reposicion=int(dias))
                    pid = inventarios.recalcular_punto_reorden(con, sesion, int(ins["id"]), f_pr,
                                                               decimal_de(proyectado) if proyectado else None)
                    pr = con.execute("SELECT * FROM puntos_reorden WHERE id = ?", (pid,)).fetchone()
                st.success(f"PR = {formato_cantidad(pr['punto_reorden_mil'], ins['unidad'])} (consumo diario "
                           f"{pr['consumo_diario'].replace('.', ',')}, fuente {pr['fuente']}).")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if st.button("Recalcular los puntos de reorden del ciclo con el consumo del ciclo anterior"):
        try:
            with con_bd() as con:
                r = inventarios.recalcular_puntos_ciclo(con, sesion, ciclo["inicio"])
            st.success(f"{len(r['recalculados'])} punto(s) recalculado(s).")
            for o in r["omitidos"]:
                st.caption(f"{o['insumo']}: {o['motivo']}")
        except ErrorPollolandia as exc:
            mostrar_error(exc)


# ------------------------------------------------------------- stock y alertas
def _tab_stock(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        cuenta = _puede(con, sesion, "inventarios", "CREAR") and sesion.rol in inventarios.ROLES_ACTA
        autoriza = sesion.rol == "GG" or seguridad.orden_suplencia(con, sesion, hoy) is not None
        cierra = _puede(con, sesion, "inventarios", "EDITAR")
        dia_control = int(seguridad.contexto_desde_bd(con, hoy).parametros["dia_semana_control_stock"])
        actas = inventarios.listar_actas(con, sesion)
        alertas = inventarios.listar_alertas_stock(con, sesion)
        rotacion = inventarios.evaluar_rotacion(con, hoy)
    st.subheader("Acta de control de stock semanal (FIN-EGR-01)")
    if cuenta:
        viernes = hoy - timedelta(days=(hoy.isoweekday() - dia_control) % 7)
        f_acta = st.date_input("Viernes del control", viernes, format="DD/MM/YYYY", key="acta_f")
        with con_bd() as con:
            criticos = [dict(i) for i in inventarios.criticos_vigentes(con, f_acta)]
            saldos = {i["id"]: inventarios.saldo_insumo(con, i["id"]) for i in criticos}
        with st.form("form_acta"):
            conteos = {}
            for i in criticos:
                if i["transferencia_inmediata"]:
                    st.caption(f"{i['nombre']}: existencia equivalente calculada (lote asegurado + días restantes "
                               "del ciclo).")
                    continue
                conteos[i["id"]] = st.number_input(
                    f"{i['nombre']} — existencia física ({i['unidad']})", min_value=0.0, step=1.0, format="%g",
                    value=float(saldos[i["id"]].cantidad), key=f"acta_{i['id']}")
            obs = st.text_input("Observaciones (humedad, estado de silos)")
            if st.form_submit_button("Registrar acta", type="primary"):
                try:
                    with con_bd() as con:
                        aid = inventarios.registrar_acta_stock(con, sesion, f_acta,
                                                               {k: decimal_de(v) for k, v in conteos.items()}, obs)
                        acta = inventarios.obtener_acta(con, sesion, aid)
                    (st.error if acta["deficit"] else st.success)(
                        "Acta registrada" + (": hay déficit frente al punto de reorden; la Gerencia General debe "
                                             "autorizar la compra de emergencia hoy." if acta["deficit"] else "."))
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.dataframe(tabla(actas, [("fecha", "Fecha"), ("ciclo", "Ciclo"), ("deficit", "Déficit"),
                               ("elaborado_por", "Elaborada por"), ("verificado_por", "Verificada por"),
                               ("estado", "Estado")], fechas=("fecha",)), hide_index=True, use_container_width=True)
    pendientes = [a for a in actas if a["estado"] == "REGISTRADA" and a["rol_elaborador"] != sesion.rol]
    if cuenta and pendientes:
        a = st.selectbox("Acta por verificar", pendientes, format_func=lambda x: fecha_pantalla(x["fecha"]))
        if st.button("Verificar acta (otra unidad)"):
            try:
                with con_bd() as con:
                    inventarios.verificar_acta_stock(con, sesion, int(a["id"]))
                st.success("Acta verificada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)

    st.subheader("Alertas de stock (CI-FIN-16) y rotación de granos (PC-07)")
    if cuenta and st.button("Monitorear stock hoy (cualquier día, Art. 8)"):
        try:
            with con_bd() as con:
                ids = inventarios.monitorear_stock(con, sesion, hoy)
            st.info(f"{len(ids)} alerta(s) abiertas tras el monitoreo.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    for a in alertas:
        a["existencia"] = formato_cantidad(a["existencia_mil"], a["unidad"])
        a["referencia"] = formato_cantidad(a["referencia_mil"], a["unidad"]) if a["referencia_mil"] is not None else "—"
    st.dataframe(tabla(alertas, [("fecha", "Fecha"), ("insumo", "Insumo"), ("tipo", "Tipo"), ("origen", "Origen"),
                                 ("existencia", "Existencia"), ("referencia", "Punto de reorden"),
                                 ("estado", "Estado"), ("detalle", "Detalle")], fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    abiertas = [a for a in alertas if a["estado"] == "ABIERTA" and a["tipo"] == "BAJO_PUNTO_REORDEN"]
    if autoriza and abiertas:
        with st.form("form_emergencia"):
            st.markdown("**Autorizar compra de emergencia (CI-FIN-16 Art. 11)**")
            a = st.selectbox("Alerta", abiertas, format_func=lambda x: f"N.º {x['id']} · {x['insumo']} · {x['existencia']}")
            constancia = st.text_input("Constancia (FIN-EGR-01 o registro extraordinario)")
            motivo = st.text_input("Justificación")
            if st.form_submit_button("Autorizar emergencia", type="primary"):
                try:
                    with con_bd() as con:
                        inventarios.autorizar_compra_emergencia(con, sesion, int(a["id"]), hoy, constancia, motivo)
                    st.success("Compra de emergencia autorizada: Finanzas puede preparar la orden sin esperar el lunes.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    otras = [a for a in alertas if a["estado"] == "ABIERTA" and a["tipo"] == "ROTACION_EXCEDIDA"]
    if cierra and otras:
        a = st.selectbox("Alerta de rotación", otras, format_func=lambda x: f"N.º {x['id']} · {x['insumo']}")
        nota = st.text_input("Explicación del cierre", key="cierre_rot")
        if st.button("Cerrar alerta de rotación"):
            try:
                with con_bd() as con:
                    inventarios.cerrar_alerta(con, sesion, int(a["id"]), nota)
                st.success("Alerta cerrada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if rotacion:
        st.dataframe(pd.DataFrame([{"Grano": r["insumo"],
                                    "Existencia": formato_cantidad(inventarios.a_mil(r["existencia"], permitir_cero=True),
                                                                   r["unidad"]),
                                    "Antigüedad (días)": r["antiguedad_dias"],
                                    "Cobertura (días)": "—" if r["cobertura_dias"] is None
                                    else str(r["cobertura_dias"]).replace(".", ","),
                                    "Máximo": r["maximo_dias"], "Excede": "Sí" if r["excede"] else "No"}
                                   for r in rotacion]), hide_index=True, use_container_width=True)


# ---------------------------------------------------------- órdenes de compra
def _form_preparar(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        proveedores = [p for p in compras.listar_proveedores(con, sesion, hoy) if p["estado_a_la_fecha"] == "HABILITADO"]
        insumos = inventarios.listar_insumos(con, sesion, solo_comprables=True)
        emergencias = inventarios.emergencias_disponibles(con)
        p = seguridad.contexto_desde_bd(con, hoy).parametros
    if not proveedores:
        st.info("Registre al menos un proveedor habilitado.")
        return
    umbral = Decimal(p["oc_tres_cotizaciones_sobre_bs"])
    st.markdown("**Preparar orden de compra (verificación previa de Finanzas)**")
    c1, c2, c3 = st.columns(3)
    emergencia = c1.selectbox("Compra de emergencia", [None, *emergencias],
                              format_func=lambda e: "— Programada (lunes) —" if e is None
                              else f"Emergencia n.º {e['id']} · {e['insumo']}", key="oc_em")
    lunes = _proximo_dia(int(p["dia_semana_orden_compra"]), hoy)
    fecha = c2.date_input("Fecha programada", hoy if emergencia else lunes, format="DD/MM/YYYY", key="oc_f")
    proveedor = c3.selectbox("Proveedor", proveedores, format_func=lambda x: x["nombre"], key="oc_p")
    n = st.number_input("Líneas", min_value=1, max_value=6, value=1, step=1, key="oc_n")
    lineas = []
    for k in range(int(n)):
        a, b, c = st.columns([3, 1, 1])
        ins = a.selectbox(f"Insumo {k + 1}", insumos, format_func=_nombre_insumo, key=f"oc_i{k}")
        cant = b.number_input("Cantidad", min_value=0.0, step=1.0, format="%g", key=f"oc_c{k}")
        precio = c.number_input("Precio unitario (Bs)", min_value=0.0, step=1.0, format="%.2f", key=f"oc_u{k}")
        lineas.append(LineaOC(int(ins["id"]), decimal_de(cant), decimal_de(precio)))
    total = sum((l.cantidad * l.precio_unitario for l in lineas), Decimal(0))
    st.caption(f"Total estimado: {bs(total)}" + (f" — supera {bs(umbral)}: adjunte {p['cotizaciones_minimas']} "
                                                  "cotizaciones (PR-FIN-03)." if total > umbral else "."))
    cotizaciones = []
    if total > umbral:
        for k in range(int(p["cotizaciones_minimas"])):
            a, b, c = st.columns([3, 1, 1])
            prov = a.selectbox(f"Cotización {k + 1}", proveedores, format_func=lambda x: x["nombre"], key=f"cot_p{k}",
                               index=min(k, len(proveedores) - 1))
            monto = b.number_input("Monto (Bs)", min_value=0.0, step=10.0, format="%.2f", key=f"cot_m{k}")
            doc = c.text_input("Documento", key=f"cot_d{k}")
            cotizaciones.append(CotizacionOC(int(prov["id"]), decimal_de(monto), doc))
    c1, c2, c3 = st.columns(3)
    condicion = c1.selectbox("Condición de pago", list(compras.CONDICIONES_PAGO), key="oc_cp")
    dias = c2.number_input("Días de crédito pactado", min_value=0, step=1, key="oc_dc")
    destino = c3.selectbox("Destino", list(compras.DESTINOS), key="oc_dest",
                           format_func=lambda d: "Ciclo en curso" if d == "ACTUAL" else "Inicio del nuevo ciclo")
    justificacion = st.text_input("Justificación si no se elige la cotización más baja", key="oc_j")
    if st.button("Preparar orden y solicitar autorización", type="primary"):
        try:
            with con_bd() as con:
                r = compras.preparar_orden(con, sesion, int(proveedor["id"]), fecha, lineas, condicion_pago=condicion,
                                           dias_credito=int(dias), destino_ciclo=destino, cotizaciones=cotizaciones,
                                           justificacion_seleccion=justificacion or None,
                                           emergencia_id=int(emergencia["id"]) if emergencia else None)
            st.success(f"{r['numero']} preparada por {bs(r['total'])}. Autorización n.º {r['autorizacion_id']} enviada a "
                       "la Gerencia General.")
            (st.warning if r["suspendida"] else st.info)(r["mensaje_sme"])
        except ErrorPollolandia as exc:
            mostrar_error(exc)


def _tab_ordenes(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        prepara = _puede(con, sesion, "compras", "CREAR") and _puede(con, sesion, "tesoreria", "CREAR")
        edita = _puede(con, sesion, "compras", "EDITAR")
        emite = sesion.rol == "GG" or seguridad.orden_suplencia(con, sesion, hoy) is not None
        ordenes = compras.listar_ordenes(con, sesion)
        disp = compras.reporte_disponibilidad(con, sesion, hoy)
    st.subheader(f"Disponibilidad presupuestaria del ciclo {disp['ciclo']} (FIN-EGR-02)")
    st.dataframe(tabla(disp["lineas"], [("cuenta", "Cuenta"), ("partida", "Partida"), ("aprobado", "Aprobado"),
                                        ("ejecutado", "Ejecutado"), ("saldo", "Saldo disponible")],
                       montos=("aprobado", "ejecutado", "saldo")), hide_index=True, use_container_width=True)
    if prepara:
        with st.expander("Nueva orden de compra (FIN-EGR-03)", expanded=False):
            _form_preparar(sesion)
    st.subheader("Órdenes de compra")
    st.dataframe(tabla(ordenes, [("etiqueta", "Orden"), ("fecha_programada", "Programada"), ("proveedor", "Proveedor"),
                                 ("total", "Total"), ("estado_autorizacion", "Autorización GG"),
                                 ("estado_compromiso", "Compromiso SME"), ("fecha_emision", "Emitida"),
                                 ("estado", "Estado")], montos=("total",), fechas=("fecha_programada", "fecha_emision")),
                 hide_index=True, use_container_width=True)
    activas = [o for o in ordenes if o["estado"] in ("PREPARADA", "EMITIDA", "RECIBIDA_PARCIAL")]
    if not activas:
        return
    o = st.selectbox("Orden", activas, format_func=lambda x: f"{x['etiqueta']} · {x['proveedor']} · {x['estado']}",
                     key="oc_sel")
    with con_bd() as con:
        detalle = compras.obtener_orden(con, sesion, int(o["id"]))
    st.markdown(f"Autorización CI-FIN-09: {chip(detalle['autorizacion']['estado'])} · Compromiso de caja: "
                f"{chip(detalle['compromiso']['estado'])}", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    if o["estado"] == "PREPARADA" and detalle["autorizacion"]["estado"] == "PENDIENTE":
        if sesion.rol == "GG" and c1.button("Firmar autorización de compra"):
            try:
                with con_bd() as con:
                    seguridad.firmar_autorizacion(con, sesion, int(detalle["autorizacion_id"]))
                st.success("Autorización firmada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        elif emite and sesion.rol != "GG":
            c1.caption("El suplente firma en «Autorizaciones» con la constancia CI-FIN-17.")
    if o["estado"] == "PREPARADA" and detalle["compromiso"]["estado"] == "SUSPENDIDO":
        st.warning(f"Compromiso suspendido por el SME: la GG firma la autorización escrita n.º "
                   f"{detalle['compromiso']['autorizacion_id']} en «Autorizaciones» y Finanzas la confirma aquí.")
        if prepara and c2.button("Confirmar compromiso autorizado"):
            try:
                with con_bd() as con:
                    tesoreria.confirmar_compromiso(con, sesion, int(detalle["compromiso_id"]))
                st.success("Compromiso confirmado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if o["estado"] == "PREPARADA" and emite:
        f_emision = c3.date_input("Fecha de emisión", hoy, format="DD/MM/YYYY", key="oc_fe")
        if c3.button("Emitir orden", type="primary"):
            try:
                with con_bd() as con:
                    compras.emitir_orden(con, sesion, int(o["id"]), f_emision)
                st.success(f"{o['etiqueta']} emitida.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if edita:
        motivo = c4.text_input("Motivo (anular o cerrar)", key="oc_mot")
        if o["estado"] in ("PREPARADA", "EMITIDA") and c4.button("Anular orden"):
            try:
                with con_bd() as con:
                    compras.anular_orden(con, sesion, int(o["id"]), motivo)
                st.success("Orden anulada; compromiso liberado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if o["estado"] in ("EMITIDA", "RECIBIDA_PARCIAL") and c4.button("Cerrar con faltante"):
            try:
                with con_bd() as con:
                    compras.cerrar_orden(con, sesion, int(o["id"]), motivo)
                st.success("Orden cerrada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ------------------------------------------------------------------ recepción
def _tab_recepcion(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        recibe = _puede(con, sesion, "inventarios", "CREAR")
        contabiliza = _puede(con, sesion, "compras", "CREAR") and _puede(con, sesion, "contabilidad", "CREAR")
        abiertas = [o for o in compras.listar_ordenes(con, sesion) if o["estado"] in ("EMITIDA", "RECIBIDA_PARCIAL")]
        recepciones = compras.listar_recepciones(con, sesion)
    if recibe and abiertas:
        o = st.selectbox("Orden emitida", abiertas, format_func=lambda x: f"{x['etiqueta']} · {x['proveedor']}",
                         key="rec_o")
        with con_bd() as con:
            detalle = compras.obtener_orden(con, sesion, int(o["id"]))
        with st.form("form_recepcion"):
            st.markdown("**Acta de recepción (FIN-EGR-04)**")
            c1, c2 = st.columns(2)
            fecha = c1.date_input("Fecha de recepción", hoy, format="DD/MM/YYYY")
            documento = c2.text_input("Factura o recibo del proveedor")
            lineas = []
            for l in detalle["lineas"]:
                if l["pendiente_mil"] <= 0:
                    continue
                st.caption(f"{l['insumo']}: pendiente {formato_cantidad(l['pendiente_mil'], l['unidad'])}")
                a, b, c, d = st.columns([1, 1, 1, 2])
                cant = a.number_input("Recibida", min_value=0.0, step=1.0, format="%g",
                                      value=float(l["pendiente_mil"]) / 1000, key=f"rl_c{l['id']}")
                ok = b.checkbox("Calidad conforme", value=True, key=f"rl_ok{l['id']}")
                vence = c.date_input("Vencimiento", value=None, format="DD/MM/YYYY", key=f"rl_v{l['id']}") \
                    if l["controla_vencimiento"] else None
                obs = d.text_input("Observación", key=f"rl_o{l['id']}")
                lineas.append(LineaRecepcion(int(l["id"]), decimal_de(cant), ok, vence, obs or None))
            observaciones = st.text_input("Observaciones generales (humedad, pureza, pesaje)")
            if st.form_submit_button("Registrar recepción", type="primary"):
                try:
                    with con_bd() as con:
                        rid = compras.registrar_recepcion(con, sesion, int(o["id"]), fecha, documento, lineas,
                                                          observaciones or None)
                        r = compras.obtener_recepcion(con, sesion, rid)
                    (st.success if r["estado"] == "RECIBIDA" else st.error)(
                        f"Recepción n.º {rid}: {r['estado']} · valor aceptado {bs(r['valor_aceptado'])}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.dataframe(tabla(recepciones, [("id", "N.º"), ("fecha", "Fecha"), ("orden", "Orden"), ("proveedor", "Proveedor"),
                                     ("documento_proveedor", "Documento"), ("valor_aceptado", "Valor aceptado"),
                                     ("recibido_por", "Recibido por"), ("estado", "Estado")],
                       montos=("valor_aceptado",), fechas=("fecha",)), hide_index=True, use_container_width=True)
    pendientes = [r for r in recepciones if r["estado"] == "RECIBIDA"]
    if contabiliza and pendientes:
        r = st.selectbox("Recepción por contabilizar", pendientes,
                         format_func=lambda x: f"N.º {x['id']} · {x['orden']} · {bs(x['valor_aceptado'])}")
        if st.button("Contabilizar (inventario contra cuenta por pagar)", type="primary"):
            try:
                with con_bd() as con:
                    res = compras.contabilizar_recepcion(con, sesion, int(r["id"]))
                st.success(f"Asiento n.º {res['asiento_id']} y cuenta por pagar de {bs(res['monto'])} registrados.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ---------------------------------------------------------------------- pagos
def _tab_pagos(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        prepara = _puede(con, sesion, "compras", "CREAR") and _puede(con, sesion, "tesoreria", "CREAR")
        autoriza = _puede(con, sesion, "compras", "AUTORIZAR")
        cuentas = compras.listar_cuentas_por_pagar(con, sesion)
        programaciones = compras.listar_programaciones(con, sesion)
    for x in cuentas:
        x["prioridad"] = tesoreria.ETIQUETA_JERARQUIA.get(x["categoria_pago"], x["categoria_pago"])
    st.dataframe(tabla(cuentas, [("id", "CxP"), ("proveedor", "Proveedor"), ("orden", "Orden"),
                                 ("cuenta_pasivo", "Cuenta"), ("monto", "Monto"), ("vencimiento", "Vence"),
                                 ("prioridad", "Prioridad POL-04"), ("estado", "Estado")],
                       montos=("monto",), fechas=("vencimiento",)), hide_index=True, use_container_width=True)
    if prepara and st.button("Preparar programación de pagos (FIN-EGR-05)"):
        try:
            with con_bd() as con:
                pid = compras.preparar_programacion(con, sesion, hoy)
            st.success(f"Programación n.º {pid} preparada para la Gerencia General.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    vigentes = [p for p in programaciones if p["estado"] in ("PREPARADA", "AUTORIZADA")]
    if not vigentes:
        return
    p = st.selectbox("Programación", vigentes, format_func=lambda x: f"N.º {x['id']} · {fecha_pantalla(x['fecha'])} · "
                     f"{x['estado']}")
    with con_bd() as con:
        detalle = compras.obtener_programacion(con, sesion, int(p["id"]))
    for l in detalle["lineas"]:
        l["mancomunada"] = "Sí" if l["requiere_mancomunada"] else "No"
        l["pagable_txt"] = "Sí" if l["pagable"] else f"No — {l['motivo']}"
    st.dataframe(tabla(detalle["lineas"], [("nivel", "Nivel"), ("proveedor", "Proveedor"),
                                           ("cuenta_por_pagar_id", "CxP"), ("monto", "Monto"), ("vencimiento", "Vence"),
                                           ("mancomunada", "Firma mancomunada"), ("pagable_txt", "Pagable"),
                                           ("estado_cxp", "Estado")], montos=("monto",), fechas=("vencimiento",)),
                 hide_index=True, use_container_width=True)
    if detalle["estado"] == "PREPARADA" and autoriza and st.button("Autorizar programación", type="primary"):
        try:
            with con_bd() as con:
                compras.autorizar_programacion(con, sesion, int(p["id"]))
            st.success("Programación autorizada.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    pagables = [l for l in detalle["lineas"] if detalle["estado"] == "AUTORIZADA" and l["pagable"]
                and l["estado_cxp"] == "PROGRAMADA"]
    if prepara and pagables:
        l = st.selectbox("Cuenta a pagar", pagables, format_func=lambda x: f"CxP-{x['cuenta_por_pagar_id']} · "
                         f"{x['proveedor']} · {bs(x['monto'])}")
        c1, c2, c3 = st.columns(3)
        fecha = c1.date_input("Fecha de pago", hoy, format="DD/MM/YYYY", key="pg_f")
        banco = c2.selectbox("Cuenta bancaria", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="pg_b")
        cheque = c3.text_input("N.º de cheque", key="pg_ch")
        with con_bd() as con:
            requisito = seguridad.requiere_autorizacion("DESEMBOLSO", l["monto"], seguridad.contexto_desde_bd(con, fecha))
            disponibles = [a for a in seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                                      tipo_decision="DESEMBOLSO")
                           if a["monto"] >= l["monto"]] if requisito.requiere_firma else []
        autorizacion = None
        if requisito.requiere_firma:
            firmas = ", ".join(seguridad.NOMBRE_CALIDAD[c] for c in requisito.firmas)
            if disponibles:
                autorizacion = int(st.selectbox(f"Autorización DESEMBOLSO ({firmas})", disponibles,
                                                format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")["id"])
            else:
                st.warning(f"El pago requiere autorización de {firmas}: solicítela en «Autorizaciones».")
        if st.button("Pagar", type="primary"):
            try:
                with con_bd() as con:
                    compras.pagar_cuenta_por_pagar(con, sesion, int(l["cuenta_por_pagar_id"]), fecha, banco,
                                                   autorizacion_id=autorizacion, nro_cheque=cheque or None)
                st.success("Pago registrado en Tesorería.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ---------------------------------------------------------------------- bajas
def _tab_bajas(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        propone = _puede(con, sesion, "inventarios", "CREAR")
        autoriza = _puede(con, sesion, "inventarios", "AUTORIZAR")
        visa = _puede(con, sesion, "inventarios", "VERIFICAR")
        contabiliza = _puede(con, sesion, "inventarios", "EDITAR") and _puede(con, sesion, "contabilidad", "CREAR")
        bajas = inventarios.listar_bajas(con, sesion)
        insumos = [i for i in inventarios.listar_insumos(con, sesion)
                   if inventarios.saldo_insumo(con, int(i["id"])).cantidad_mil > 0]
        umbral = Decimal(seguridad.contexto_desde_bd(con, hoy).parametros["baja_inventario_cv_sobre_bs"])
    st.caption(f"PC-07 §V.C: acta firmada por la Gerencia General; visto del Consejo de Vigilancia sobre {bs(umbral)}.")
    if propone and insumos:
        with st.form("form_baja", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ins = c1.selectbox("Insumo", insumos, format_func=_nombre_insumo)
            cantidad = c2.number_input("Cantidad", min_value=0.0, step=1.0, format="%g")
            causa = c3.selectbox("Causa", list(inventarios.CAUSAS_BAJA), format_func=inventarios.CAUSAS_BAJA.get)
            descripcion = st.text_input("Descripción")
            informe = st.text_input("Informe técnico de respaldo")
            if st.form_submit_button("Proponer baja"):
                try:
                    with con_bd() as con:
                        inventarios.proponer_baja(con, sesion, int(ins["id"]), hoy, decimal_de(cantidad), causa,
                                                  descripcion, informe)
                    st.success("Baja propuesta a la Gerencia General.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for b in bajas:
        b["cant"] = formato_cantidad(b["cantidad_mil"], b["unidad"])
        b["visto"] = "Requerido" if b["requiere_visto_cv"] and not b["visto_cv_por"] else (b["visto_cv_por"] or "—")
    st.dataframe(tabla(bajas, [("id", "N.º"), ("fecha", "Fecha"), ("insumo", "Insumo"), ("cant", "Cantidad"),
                               ("causa", "Causa"), ("valor_estimado", "Valor estimado"), ("acta_ref", "Acta GG"),
                               ("visto", "Visto CV"), ("estado", "Estado")], montos=("valor_estimado",),
                       fechas=("fecha",)), hide_index=True, use_container_width=True)
    abiertas = [b for b in bajas if b["estado"] in ("PROPUESTA", "AUTORIZADA")]
    if not abiertas:
        return
    b = st.selectbox("Baja", abiertas, format_func=lambda x: f"N.º {x['id']} · {x['insumo']} · {x['estado']}")
    c1, c2, c3 = st.columns(3)
    if autoriza and b["estado"] == "PROPUESTA":
        acta = c1.text_input("Acta de baja firmada", key="bj_acta")
        if c1.button("Autorizar baja"):
            try:
                with con_bd() as con:
                    inventarios.autorizar_baja(con, sesion, int(b["id"]), acta)
                st.success("Baja autorizada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if visa and not b["visto_cv_por"] and c2.button("Registrar visto del CV"):
        try:
            with con_bd() as con:
                inventarios.visar_baja(con, sesion, int(b["id"]))
            st.success("Visto registrado.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if contabiliza and b["estado"] == "AUTORIZADA" and c3.button("Contabilizar baja", type="primary"):
        try:
            with con_bd() as con:
                inventarios.contabilizar_baja(con, sesion, int(b["id"]), hoy)
            st.success("Baja contabilizada al CPP.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)


# ------------------------------------------------------------------- formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Formatos del Ciclo 2 en PDF, con el bloque de firmas para su impresión y archivo.")
    ctx = pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                       cargo=CARGO_ROL.get(sesion.rol, sesion.rol),
                       ciclo=st.text_input("N.º de ciclo (opcional)", key="fmtc_ciclo"))
    opciones = {"FIN-EGR-01": "Acta de Control de Stock Semanal", "FIN-EGR-02": "Reporte de Disponibilidad Presupuestaria",
                "FIN-EGR-03": "Orden de Compra", "FIN-EGR-04": "Acta de Recepción de Insumos",
                "FIN-EGR-05": "Programación de Pagos a Proveedores", "ACTA-BAJA": "Acta de Baja de Inventario"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda c: f"{c} — {opciones[c]}", key="fmtc_cod")
    try:
        with con_bd() as con:
            datos: bytes | None = None
            if codigo == "FIN-EGR-01":
                actas = inventarios.listar_actas(con, sesion)
                if actas:
                    a = st.selectbox("Acta", actas, format_func=lambda x: fecha_pantalla(x["fecha"]))
                    datos = pdf.fin_egr_01(inventarios.obtener_acta(con, sesion, int(a["id"])), ctx)
            elif codigo == "FIN-EGR-02":
                fecha = st.date_input("Fecha", date.today(), format="DD/MM/YYYY", key="fmtc_f2")
                datos = pdf.fin_egr_02(compras.reporte_disponibilidad(con, sesion, fecha), ctx)
            elif codigo == "FIN-EGR-03":
                ordenes = [o for o in compras.listar_ordenes(con, sesion) if o["estado"] != "ANULADA"]
                if ordenes:
                    o = st.selectbox("Orden", ordenes, format_func=lambda x: f"{x['etiqueta']} · {x['proveedor']}")
                    datos = pdf.fin_egr_03(compras.obtener_orden(con, sesion, int(o["id"])), ctx)
            elif codigo == "FIN-EGR-04":
                recepciones = compras.listar_recepciones(con, sesion)
                if recepciones:
                    r = st.selectbox("Recepción", recepciones, format_func=lambda x: f"N.º {x['id']} · {x['orden']}")
                    datos = pdf.fin_egr_04(compras.obtener_recepcion(con, sesion, int(r["id"])), ctx)
            elif codigo == "FIN-EGR-05":
                programaciones = compras.listar_programaciones(con, sesion)
                if programaciones:
                    p = st.selectbox("Programación", programaciones,
                                     format_func=lambda x: f"N.º {x['id']} · {fecha_pantalla(x['fecha'])} · {x['estado']}")
                    datos = pdf.fin_egr_05(compras.obtener_programacion(con, sesion, int(p["id"])), ctx)
            else:
                bajas = inventarios.listar_bajas(con, sesion)
                if bajas:
                    b = st.selectbox("Baja", bajas, format_func=lambda x: f"N.º {x['id']} · {x['insumo']}")
                    datos = pdf.acta_baja(b, ctx, inventarios.CAUSAS_BAJA)
        if datos is None:
            st.info("Aún no hay registros para este formato.")
        else:
            st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                               mime="application/pdf", type="primary")
    except (ErrorPollolandia, ValueError) as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 2 — Compras e inventarios."""
    st.header("Compras e inventarios · Ciclo 2")
    _resumen(sesion)
    pestañas = st.tabs(["Proveedores", "Inventarios", "Stock y alertas", "Órdenes de compra", "Recepción", "Pagos",
                        "Bajas", "Formatos"])
    funciones = (_tab_proveedores, _tab_inventarios, _tab_stock, _tab_ordenes, _tab_recepcion, _tab_pagos, _tab_bajas,
                 _tab_formatos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
