"""Módulo 7 — Ventas, HORECA y cobranza (Fase 9, Ciclo 4 — Ingresos).

Pestañas: Clientes, Tarifario, Pre-venta del lote, Notas de entrega, Cartera y
cobranza, Recaudación y devoluciones, Margen por canal y Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. La Unidad de Finanzas registra
pedidos, notas, cobros y partes; la Gerencia General propone el tarifario,
activa el respaldo, autoriza créditos y suspensiones; el Consejo de
Administración aprueba el tarifario; los demás cargos consultan.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_ventas as pdf
from servicios import ventas
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def _ctx(sesion: Sesion, detalle: str = "") -> pdf.Contexto:
    return pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                        cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo=detalle)


def _descargar(codigo: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                       mime="application/pdf", key=clave)


def _resumen(sesion: Sesion) -> None:
    with con_bd() as con:
        r = ventas.resumen_ventas(con, sesion, date.today())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ventas del mes", bs(r["ventas_mes"]))
    c2.metric("Cuentas por cobrar", bs(r["cxc"]))
    c3.metric("Vencido", bs(r["vencido"]), help=f"{r['vencidas']} cuenta(s)")
    c4.metric("Despachos abiertos", r["despachos_abiertos"])
    for alerta in r["alertas"]:
        st.warning(alerta)


# ------------------------------------------------------------------- clientes
def _tab_clientes(sesion: Sesion) -> None:
    hoy = date.today()
    if sesion.rol == "JF":
        with st.form("form_cliente", clear_on_submit=True):
            st.markdown("**Ficha de cliente**")
            c1, c2, c3 = st.columns([1, 2, 1])
            tipo = c1.selectbox("Tipo", list(ventas.TIPOS_CLIENTE), format_func=lambda k: ventas.TIPOS_CLIENTE[k])
            nombre = c2.text_input("Nombre o razón social")
            documento = c3.text_input("NIT / C.I.")
            c4, c5, c6 = st.columns(3)
            contacto = c4.text_input("Contacto")
            telefono = c5.text_input("Teléfono")
            alta = c6.date_input("Fecha de alta", hoy, format="DD/MM/YYYY")
            direccion = st.text_input("Dirección")
            socio = st.checkbox("Es socio de la cooperativa")
            if st.form_submit_button("Registrar cliente", type="primary"):
                try:
                    with con_bd() as con:
                        r = ventas.registrar_cliente(con, sesion, tipo, nombre, alta, documento=documento or None,
                                                     contacto=contacto or None, telefono=telefono or None,
                                                     direccion=direccion or None, es_socio=socio)
                    st.success(f"Cliente {r['codigo']} registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        lista = ventas.listar_clientes(con, sesion)
    if not lista:
        st.info("Aún no hay clientes registrados.")
        return
    for c in lista:
        c["tipo_txt"] = ventas.TIPOS_CLIENTE[c["tipo"]]
        c["credito"] = "Suspendido (CIR-ING-01)" if c["estado_credito"] == "SUSPENDIDO" else "Habilitado"
    st.dataframe(tabla(lista, [("codigo", "Código"), ("nombre", "Cliente"), ("tipo_txt", "Tipo"),
                               ("documento", "NIT / C.I."), ("telefono", "Teléfono"), ("fecha_alta", "Alta"),
                               ("credito", "Línea de crédito")], fechas=("fecha_alta",)),
                 hide_index=True, use_container_width=True)
    elegido = st.selectbox("Ficha", lista, key="cl_ficha", format_func=lambda c: f"{c['codigo']} — {c['nombre']}")
    with con_bd() as con:
        f = ventas.ficha_cliente(con, sesion, int(elegido["id"]), hoy)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Historial", f"{f['historial_meses']} meses",
              help=f"Primera compra: {fecha_pantalla(f['primera_compra']) if f['primera_compra'] else '—'}")
    c2.metric("Comprado", bs(f["total_comprado"]), help=f"{f['entregas']} entrega(s)")
    c3.metric("Entrega semanal promedio", bs(f["entrega_semanal"]))
    c4.metric("Por cobrar", bs(f["cxc_pendiente"]), help=f"Incumplimientos consecutivos: {f['incumplimientos']}")
    st.markdown(chip("SUSPENDIDO" if f["estado_credito"] == "SUSPENDIDO" else "VIGENTE"), unsafe_allow_html=True)


# ------------------------------------------------------------------ tarifario
def _tab_tarifario(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        vigente = ventas.tarifario_vigente(con, hoy)
        lista = ventas.listar_tarifarios(con, sesion)
        costo, _origen, etiqueta = ventas.costo_referencia(con, hoy)
        base = ventas.lineas_desde_presupuesto(con, hoy)
    st.caption(f"Costo de referencia: {bs(costo)}/kg — {etiqueta}. Todo ajuste requiere la aprobación conjunta de la "
               "Gerencia General y el Consejo de Administración (CI-FIN-06 Art. 8).")
    if vigente is not None:
        with con_bd() as con:
            det = ventas.detalle_tarifario(con, sesion, int(vigente["id"]))
        st.markdown(f"**Tarifario vigente desde {fecha_pantalla(det['vigente_desde'])}** ({det['acta_ca']})")
        _lineas_tarifario(det)
    if sesion.rol == "GG":
        with st.form("form_tarifario"):
            st.markdown("**Proponer tarifario**")
            c1, c2 = st.columns(2)
            desde = c1.date_input("Vigente desde", hoy, format="DD/MM/YYYY")
            motivo = c2.text_input("Motivo", value="Revisión mensual de márgenes (FIN-ING-08)")
            precios = {}
            st.caption("Deje en cero los precios que no correspondan. «Todos» se aplica a los canales sin precio propio.")
            sugerido = {(p, c): v for p, c, v in base}
            for producto in ventas.PRODUCTOS:
                cols = st.columns(4)
                for col, canal in zip(cols, ("TODOS", *ventas.CANALES)):
                    precios[(producto, canal)] = col.number_input(
                        f"{ventas.PRODUCTOS[producto]} · {ventas.NOMBRE_CANAL[canal]} (Bs/{ventas.UNIDAD[producto]})",
                        min_value=0.0, value=float(sugerido.get((producto, canal), 0)), step=0.5, format="%.2f",
                        key=f"tf_{producto}_{canal}")
            if st.form_submit_button("Proponer al Consejo de Administración", type="primary"):
                lineas = [(p, c, decimal_de(v)) for (p, c), v in precios.items() if v > 0]
                try:
                    with con_bd() as con:
                        r = ventas.proponer_tarifario(con, sesion, desde, lineas, motivo)
                    st.success(f"Tarifario n.º {r['id']} propuesto (costo de referencia {bs(r['costo_referencia'])}/kg).")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    propuestos = [t for t in lista if t["estado"] == "PROPUESTO"]
    if propuestos:
        st.markdown("**Propuestas pendientes**")
        for t in propuestos:
            with con_bd() as con:
                det = ventas.detalle_tarifario(con, sesion, int(t["id"]))
            st.markdown(f"Tarifario n.º {t['id']} · vigente desde {fecha_pantalla(t['vigente_desde'])} · {t['motivo']}")
            _lineas_tarifario(det)
            if sesion.rol == "CA":
                with st.form(f"form_ca_{t['id']}"):
                    decision = st.radio("Decisión", ["APRUEBA", "RECHAZA"], horizontal=True,
                                        format_func=lambda d: "Aprobar" if d == "APRUEBA" else "Rechazar")
                    acta = st.text_input("Acta del Consejo de Administración")
                    obs = st.text_input("Observación")
                    if st.form_submit_button("Registrar resolución", type="primary"):
                        try:
                            with con_bd() as con:
                                ventas.resolver_tarifario(con, sesion, int(t["id"]), decision, acta, obs or None)
                            st.rerun()
                        except ErrorPollolandia as exc:
                            mostrar_error(exc)
    if lista:
        st.markdown("**Historial de tarifarios**")
        st.dataframe(tabla(lista, [("id", "N°"), ("vigente_desde", "Vigente desde"), ("motivo", "Motivo"),
                                   ("estado", "Estado"), ("acta_ca", "Acta CA"), ("propuesto_por", "Propuso")],
                           fechas=("vigente_desde",)), hide_index=True, use_container_width=True)


def _lineas_tarifario(det: dict) -> None:
    filas = []
    for l in det["lineas"]:
        filas.append({"producto": ventas.PRODUCTOS[l["producto"]], "canal": ventas.NOMBRE_CANAL[l["canal"]],
                      "unidad": l["unidad"], "precio": l["precio"],
                      "margen": ventas.pct_texto(l["margen"]) if l["margen"] is not None else "—",
                      "bajo": ("Sí" if l["bajo_umbral"] else "No") if l["margen"] is not None else "—"})
    st.dataframe(tabla(filas, [("producto", "Producto"), ("canal", "Canal"), ("unidad", "Unidad"), ("precio", "Precio"),
                               ("margen", "Margen bruto"), ("bajo", "¿Bajo el 20 %?")], montos=("precio",)),
                 hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ pre-venta
def _tab_preventa(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        despachos = ventas.listar_despachos(con, sesion)
        lotes = ventas.lotes_sin_despacho(con, sesion)
        clientes = [c for c in ventas.listar_clientes(con, sesion) if c["tipo"] != "AGRICULTOR"]
    if sesion.rol == "JF" and lotes:
        with st.form("form_despacho"):
            st.markdown("**Abrir la pre-venta de un lote (CI-FIN-13)**")
            c1, c2, c3 = st.columns([2, 1, 1])
            lote = c1.selectbox("Lote", lotes, format_func=lambda l: f"{l['codigo']} · {l['aves_disponibles']} aves")
            fecha = c2.date_input("Faenado estimado (si no hay circular)", hoy, format="DD/MM/YYYY")
            aves = c3.number_input("Aves (0 = las vivas)", min_value=0, step=10)
            if st.form_submit_button("Abrir despacho", type="primary"):
                try:
                    with con_bd() as con:
                        r = ventas.crear_despacho(con, sesion, int(lote["id"]), fecha, int(aves) or None)
                    st.success(f"Despacho abierto: faenado {fecha_pantalla(r['fecha_faenado'])} ({r['origen_fecha']}), "
                               f"{r['aves']} aves.")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if not despachos:
        st.info("No hay despachos de lotes.")
        return
    d = st.selectbox("Despacho", despachos, key="pv_desp",
                     format_func=lambda x: f"Lote {x['lote']} · faenado {fecha_pantalla(x['fecha_faenado'])} · "
                                           f"{x['estado'].lower()} · cobertura {ventas.pct_texto(x['cobertura'])}")
    did = int(d["id"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Aves del lote", d["aves"])
    c2.metric("HORECA a tiempo", d["horeca"], help=f"Fuera de plazo: {d['horeca_fuera_plazo']}")
    c3.metric("Indicativos", d["indicativo"], help=f"{d['compradores_indicativos']} comprador(es)")
    c4.metric("Cobertura", ventas.pct_texto(d["cobertura"]), help=f"Remanente: {d['remanente']} aves")
    st.markdown(f"{chip('VERDE' if d['cubre'] else 'ROJO')} · respaldo: "
                + ("activado por la GG" if d["respaldo_activado"] else ("requerido" if d["respaldo_requerido"]
                                                                       else "no requerido")), unsafe_allow_html=True)
    abierto = d["estado"] == "ABIERTO"
    if sesion.rol == "JF" and abierto:
        with st.form("form_pedido", clear_on_submit=True):
            st.markdown("**Registro de pedido (FIN-ING-01)**")
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            cliente = c1.selectbox("Cliente", clientes, format_func=lambda c: f"{c['nombre']} · "
                                                                              f"{ventas.TIPOS_CLIENTE[c['tipo']]}")
            aves = c2.number_input("Aves", min_value=1, step=10)
            fecha = c3.date_input("Fecha del pedido", hoy, format="DD/MM/YYYY")
            producto = c4.selectbox("Producto", list(ventas.CARNE), format_func=lambda p: ventas.PRODUCTOS[p])
            if st.form_submit_button("Registrar pedido"):
                try:
                    with con_bd() as con:
                        r = ventas.registrar_pedido(con, sesion, did, int(cliente["id"]), int(aves), fecha,
                                                    producto=producto)
                    if r["activa_respaldo"]:
                        st.warning(f"Pedido n.º {r['numero']} con {r['anticipacion']} día(s) de antelación: no cumple el "
                                   "mínimo de CI-FIN-13 Art. 4; corresponde el canal de respaldo (Art. 5).")
                    else:
                        st.success(f"Pedido n.º {r['numero']} registrado ({r['tipo'].lower()}).")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        c1, c2 = st.columns(2)
        fecha_v = c1.date_input("Fecha de verificación", hoy, format="DD/MM/YYYY", key="pv_fv")
        c2.write("")
        if c2.button("Verificar cobertura (Art. 8)"):
            try:
                with con_bd() as con:
                    v = ventas.verificar_cobertura(con, sesion, did, fecha_v)
                (st.success if v["cubre"] else st.error)(v["mensaje"] + ("" if v["en_plazo"] else
                                                                         " Verificación fuera del plazo del Art. 8."))
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if st.button("Cerrar despacho del lote", type="primary"):
            try:
                with con_bd() as con:
                    ventas.cerrar_despacho(con, sesion, did, hoy)
                st.success("Despacho cerrado.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if sesion.rol == "GG" and abierto and d["respaldo_requerido"] and not d["respaldo_activado"]:
        with st.form("form_respaldo"):
            motivo = st.text_input("Motivo", value="Pedidos HORECA insuficientes al plazo de pre-venta")
            if st.form_submit_button("Activar canal de respaldo (CI-FIN-13 Art. 5)", type="primary"):
                try:
                    with con_bd() as con:
                        ventas.activar_respaldo(con, sesion, did, motivo)
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        pedidos = ventas.listar_pedidos(con, sesion, did)
        verifs = ventas.listar_verificaciones(con, sesion, did)
    for p in pedidos:
        p["cumple"] = ("Sí" if p["cumple_anticipacion"] else "No") if p["tipo"] == "HORECA" else "Indicativo"
    st.dataframe(tabla(pedidos, [("numero", "N°"), ("cliente", "Cliente"), ("tipo", "Tipo"), ("aves", "Aves"),
                                 ("fecha_pedido", "Pedido"), ("anticipacion_dias", "Antelación (días)"),
                                 ("cumple", "¿Cumple 3 días?"), ("estado", "Estado")], fechas=("fecha_pedido",)),
                 hide_index=True, use_container_width=True)
    if verifs:
        for v in verifs:
            v["cob"] = ventas.pct_texto(v["cobertura"])
            v["res"] = "Completa" if v["cubre"] else "Incompleta"
            v["plazo"] = "Sí" if v["en_plazo"] else "No"
        st.dataframe(tabla(verifs, [("fecha", "Verificación"), ("horeca_aves", "HORECA"), ("indicativo_aves", "Indicativos"),
                                    ("compradores_indicativos", "Compradores"), ("cob", "Cobertura"), ("res", "Resultado"),
                                    ("plazo", "En plazo")], fechas=("fecha",)), hide_index=True, use_container_width=True)


# ------------------------------------------------------------------- notas
def _tab_notas(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        clientes = ventas.listar_clientes(con, sesion)
        despachos = [d for d in ventas.listar_despachos(con, sesion) if d["estado"] == "ABIERTO"]
    if sesion.rol == "JF" and clientes:
        with st.form("form_nota"):
            st.markdown("**Nota de entrega (FIN-ING-03 HORECA / FIN-ING-06 contado)**")
            c1, c2, c3 = st.columns([3, 2, 1])
            cliente = c1.selectbox("Cliente", clientes, format_func=lambda c: f"{c['nombre']} · "
                                                                              f"{ventas.TIPOS_CLIENTE[c['tipo']]}")
            producto = c2.selectbox("Producto", list(ventas.PRODUCTOS), format_func=lambda p: ventas.PRODUCTOS[p])
            cantidad = c3.number_input("Cantidad (kg / qq / cargas)", min_value=0.0, step=1.0, format="%g")
            c4, c5, c6, c7 = st.columns(4)
            fecha = c4.date_input("Fecha", hoy, format="DD/MM/YYYY")
            hora = c5.text_input("Hora de entrega", value=datetime.now().strftime("%H:%M"))
            medio = c6.selectbox("Medio de pago", list(dict.fromkeys(ventas.MEDIOS_HORECA + ventas.MEDIOS_CONTADO)),
                                 format_func=lambda m: ventas.NOMBRE_MEDIO[m])
            dias = c7.number_input("Días de crédito (HORECA)", min_value=0, max_value=30, step=1)
            despacho = st.selectbox("Despacho del lote (carne)", [None] + despachos,
                                    format_func=lambda d: "—" if d is None else
                                    f"Lote {d['lote']} · faenado {fecha_pantalla(d['fecha_faenado'])}")
            c8, c9 = st.columns(2)
            comprobante = c8.text_input("Comprobante (FIN-ING-04 en crédito; de pago en contado)")
            rau_ref = c9.text_input("Certificado RAU exhibido (número)")
            rau = st.checkbox("Se exhibió el Certificado de Inscripción al RAU y el comprobante del último pago")
            c10, c11 = st.columns(2)
            recibe = c10.text_input("Recibido conforme por")
            entrega = c11.text_input("Entregado por (Unidad Técnica)")
            if st.form_submit_button("Registrar nota de entrega", type="primary"):
                try:
                    with con_bd() as con:
                        r = ventas.registrar_nota(con, sesion, int(cliente["id"]), fecha, producto, decimal_de(cantidad),
                                                  hora_entrega=hora, medio_pago=medio, comprobante_ref=comprobante,
                                                  certificado_rau_exhibido=rau, certificado_rau_ref=rau_ref,
                                                  recibido_por=recibe, entregado_por=entrega,
                                                  despacho_id=int(despacho["id"]) if despacho else None,
                                                  dias_credito=int(dias))
                    st.success(f"{r['documento']}: {bs(r['total'])} al precio del tarifario ({bs(r['precio'])})."
                               + (f" Vence el {fecha_pantalla(r['vencimiento'])}." if r["vencimiento"] else "")
                               + ("" if r["costo"] is not None or producto == "GALLINAZA"
                                  else " Costo de ventas pendiente del cierre del ciclo."))
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        notas = ventas.listar_notas(con, sesion)
        pendientes = ventas.costos_pendientes(con, sesion)
    if pendientes and sesion.rol == "JF":
        c1, c2 = st.columns([3, 1])
        fecha_c = c1.date_input("Contabilizar costos pendientes al", hoy, format="DD/MM/YYYY", key="nt_fc")
        c2.write("")
        if c2.button(f"Registrar costo ({len(pendientes)} nota/s)"):
            try:
                with con_bd() as con:
                    r = ventas.contabilizar_costos_pendientes(con, sesion, fecha_c)
                st.success(f"Costo de ventas de {r['notas']} nota(s): {bs(r['costo'])}.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    for n in notas:
        n["doc"] = f"{'FIN-ING-03' if n['tipo'] == 'HORECA' else 'FIN-ING-06'} n.º {n['numero']}"
        n["cant"] = ventas.cantidad_texto(n["cantidad"], n["unidad"])
        n["prod"] = ventas.PRODUCTOS[n["producto"]]
        n["cond"] = f"Crédito {n['dias_credito']} d" if n["condicion"] == "CREDITO" else "Contado"
        n["medio"] = ventas.NOMBRE_MEDIO[n["medio_pago"]]
    st.dataframe(tabla(notas, [("doc", "Documento"), ("fecha", "Fecha"), ("cliente", "Cliente"), ("prod", "Producto"),
                               ("cant", "Cantidad"), ("precio", "Precio"), ("total", "Total"), ("cond", "Condición"),
                               ("medio", "Medio"), ("costo", "Costo de ventas")],
                       montos=("precio", "total", "costo"), fechas=("fecha",)), hide_index=True, use_container_width=True)


# ------------------------------------------------------------------ cartera
def _tab_cartera(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        filas = ventas.cartera(con, sesion, hoy)
        suspensiones = ventas.listar_suspensiones(con, sesion)
        clientes = ventas.listar_clientes(con, sesion, "HORECA")
    pendientes = [f for f in filas if f["estado"] == "PENDIENTE"]
    if sesion.rol == "JF":
        c1, c2 = st.columns([3, 1])
        fecha_r = c1.date_input("Revisión diaria de la cartera al", hoy, format="DD/MM/YYYY", key="ct_rev")
        c2.write("")
        if c2.button("Revisar y reportar vencidos"):
            try:
                with con_bd() as con:
                    r = ventas.revisar_cartera(con, sesion, fecha_r)
                st.success(f"{len(r['vencidas'])} cuenta(s) vencida(s) reportada(s) a la Gerencia General"
                           + (f"; {len(r['suspensiones'])} línea(s) suspendida(s) automáticamente." if r["suspensiones"]
                              else "."))
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if pendientes:
            with st.form("form_cobro", clear_on_submit=True):
                st.markdown("**Cobro (comprobante de ingreso a caja o banco)**")
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                cuenta = c1.selectbox("Cuenta", pendientes, format_func=lambda x: f"{x['cliente']} · nota {x['nota']} · "
                                                                                  f"saldo {bs(x['saldo'])}")
                monto = c2.number_input("Monto (Bs)", min_value=0.0, step=10.0, format="%.2f")
                fecha = c3.date_input("Fecha", hoy, format="DD/MM/YYYY")
                medio = c4.selectbox("Medio", list(ventas.MEDIOS_HORECA), format_func=lambda m: ventas.NOMBRE_MEDIO[m])
                comprobante = st.text_input("Comprobante de ingreso")
                if st.form_submit_button("Registrar cobro", type="primary"):
                    try:
                        with con_bd() as con:
                            r = ventas.registrar_cobro(con, sesion, int(cuenta["id"]), fecha, decimal_de(monto), medio,
                                                       comprobante)
                        st.success("Cobro registrado" + ("." if r["en_plazo"] else " fuera de plazo (incumplimiento)."))
                        st.rerun()
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    for f in filas:
        f["venc"] = "Sí" if f["vencido"] or f["incumplida"] else "No"
        f["reporte"] = ("—" if not f["reportada_en"] else
                        f"{fecha_pantalla(f['reportada_en'])} ({'en plazo' if f['reporte_en_plazo'] else 'fuera de plazo'})")
    st.markdown("**Registro de cuentas por cobrar (FIN-ING-05)**")
    st.dataframe(tabla(filas, [("cliente", "Cliente"), ("nota", "Nota"), ("monto", "Monto"), ("saldo", "Saldo"),
                               ("vencimiento", "Vence"), ("venc", "¿Vencido?"), ("dias_mora", "Mora (días)"),
                               ("fecha_cobro", "Cobro"), ("reporte", "Reporte a la GG"),
                               ("incumplimientos", "Incumplimientos")],
                       montos=("monto", "saldo"), fechas=("vencimiento", "fecha_cobro")),
                 hide_index=True, use_container_width=True)
    if suspensiones:
        st.markdown("**Suspensiones de línea (CIR-ING-01)**")
        for s in suspensiones:
            s["circ"] = f"CIR-ING-01/{s['numero']}/{s['anio']}"
            s["firma"] = s["firmada_por"] or "Pendiente"
            s["rest"] = fecha_pantalla(s["restablecida_en"]) if s["restablecida_en"] else "—"
        st.dataframe(tabla(suspensiones, [("circ", "Circular"), ("cliente", "Cliente"), ("fecha", "Fecha"),
                                          ("firma", "Firma GG"), ("rest", "Restablecida")], fechas=("fecha",)),
                     hide_index=True, use_container_width=True)
    if sesion.rol == "GG":
        sin_firma = [s for s in suspensiones if not s["firmada_por"]]
        if sin_firma:
            s = st.selectbox("Circular por firmar", sin_firma, key="ct_firma", format_func=lambda x: f"{x['circ']} · "
                                                                                                   f"{x['cliente']}")
            if st.button("Firmar circular CIR-ING-01"):
                try:
                    with con_bd() as con:
                        ventas.firmar_suspension(con, sesion, int(s["id"]))
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        suspendidos = [c for c in clientes if c["estado_credito"] == "SUSPENDIDO"]
        if suspendidos:
            with st.form("form_restablecer"):
                c = st.selectbox("Cliente suspendido", suspendidos, format_func=lambda x: x["nombre"])
                motivo = st.text_input("Motivo del restablecimiento")
                if st.form_submit_button("Restablecer línea de crédito"):
                    try:
                        with con_bd() as con:
                            ventas.restablecer_credito(con, sesion, int(c["id"]), hoy, motivo)
                        st.rerun()
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        with st.expander("Resolución de crédito > 1 día (FIN-ING-02, desde 2028)"):
            with st.form("form_resolucion"):
                c1, c2, c3 = st.columns([3, 1, 1])
                c = c1.selectbox("Cliente HORECA", clientes, format_func=lambda x: x["nombre"]) if clientes else None
                plazo = c2.number_input("Plazo (días)", min_value=2, max_value=30, value=7)
                monto = c3.number_input("Monto máximo (Bs)", min_value=0.0, step=100.0, format="%.2f")
                evaluacion = st.text_area("Evaluación de la capacidad de pago")
                if st.form_submit_button("Autorizar crédito") and c is not None:
                    try:
                        with con_bd() as con:
                            r = ventas.autorizar_credito(con, sesion, int(c["id"]), hoy, int(plazo), decimal_de(monto),
                                                         evaluacion)
                        st.success(f"Resolución n.º {r['id']} ({r['historial_meses']} meses de historial).")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if sesion.rol == "JF":
        with st.expander("Provisión de incobrables (desde 2028)"):
            fecha_p = st.date_input("Fecha de cálculo", hoy, format="DD/MM/YYYY", key="ct_prov")
            try:
                with con_bd() as con:
                    p = ventas.calcular_provision(con, sesion, fecha_p)
                st.markdown(f"Saldo de deudores {bs(p['saldo_deudores'])} · requerida {bs(p['requerida'])} · registrada "
                            f"{bs(p['anterior'])} · ajuste {bs(p['ajuste'])}")
                if p["ajuste"] != 0 and st.button("Registrar ajuste de provisión"):
                    with con_bd() as con:
                        ventas.registrar_provision(con, sesion, fecha_p)
                    st.success("Provisión registrada.")
            except ErrorPollolandia as exc:
                st.info(exc.mensaje)


# ------------------------------------------------------- recaudación y devoluciones
def _tab_recaudacion(sesion: Sesion) -> None:
    hoy = date.today()
    fecha = st.date_input("Fecha del parte diario (FIN-ING-07)", hoy, format="DD/MM/YYYY", key="rc_fecha")
    with con_bd() as con:
        parte = ventas.preparar_parte(con, sesion, fecha)
        partes = ventas.listar_partes(con, sesion)
        notas = ventas.listar_notas(con, sesion)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Carne al contado", bs(parte["carne"]))
    c2.metric("Gallinaza y alimento", bs(parte["gallinaza"] + parte["alimento"]))
    c3.metric("Cobros HORECA", bs(parte["cobros"]))
    c4.metric("Efectivo a depositar", bs(parte["efectivo"]), help=f"Ya en banco: {bs(parte['bancarizado'])}")
    if sesion.rol == "JF" and not parte["registrado"] and parte["total"] > 0:
        c1, c2 = st.columns([3, 1])
        boleta = c1.text_input("Boleta de depósito del efectivo del día")
        c2.write("")
        if c2.button("Registrar parte y depósito", type="primary"):
            try:
                with con_bd() as con:
                    ventas.registrar_parte(con, sesion, fecha, boleta or None)
                st.success("Parte registrado y efectivo depositado.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    _descargar("FIN-ING-07", pdf.fin_ing_07(parte, _ctx(sesion)), "pdf_finingo07")
    if partes:
        st.dataframe(tabla(partes, [("fecha", "Fecha"), ("total", "Total"), ("efectivo", "Efectivo"),
                                    ("depositado", "Depositado"), ("boleta_ref", "Boleta"),
                                    ("verificado_por", "Verificó (GG)")],
                           montos=("total", "efectivo", "depositado"), fechas=("fecha",)),
                     hide_index=True, use_container_width=True)
        sin_verificar = [p for p in partes if not p["verificado_por"]]
        if sesion.rol == "GG" and sin_verificar:
            p = st.selectbox("Parte por verificar", sin_verificar, format_func=lambda x: fecha_pantalla(x["fecha"]))
            if st.button("Verificar depósito"):
                try:
                    with con_bd() as con:
                        ventas.verificar_parte(con, sesion, int(p["id"]))
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.subheader("Devolución por rechazo de inocuidad (PC-08)")
    devolubles = [n for n in notas if n["producto"] != "GALLINAZA"]
    if sesion.rol == "JF" and devolubles:
        with st.form("form_devolucion"):
            c1, c2, c3 = st.columns([3, 1, 1])
            n = c1.selectbox("Nota", devolubles, format_func=lambda x: f"{'FIN-ING-03' if x['tipo'] == 'HORECA' else 'FIN-ING-06'} "
                                                                     f"n.º {x['numero']} · {x['cliente']} · {bs(x['total'])}")
            cantidad = c2.number_input("Cantidad rechazada", min_value=0.0, step=1.0, format="%g")
            fecha_d = c3.date_input("Fecha", hoy, format="DD/MM/YYYY", key="dv_f")
            motivo = st.text_input("Motivo del rechazo")
            informe = st.text_input("Informe CI-FIN-12")
            if st.form_submit_button("Registrar devolución"):
                try:
                    with con_bd() as con:
                        r = ventas.registrar_devolucion(con, sesion, int(n["id"]), fecha_d, decimal_de(cantidad), motivo,
                                                        informe)
                    st.success(f"Ingreso anulado: {bs(r['monto'])} (cuenta por cobrar {bs(r['a_cuenta_cobrar'])}, saldo "
                               f"a favor {bs(r['saldo_favor'])}).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        devs = ventas.listar_devoluciones(con, sesion)
    if devs:
        st.dataframe(tabla(devs, [("fecha", "Fecha"), ("cliente", "Cliente"), ("numero", "Nota"), ("cantidad", "Cantidad"),
                                  ("monto", "Monto"), ("motivo", "Motivo"), ("informe_ref", "Informe")],
                           montos=("monto",), fechas=("fecha",)), hide_index=True, use_container_width=True)


# --------------------------------------------------------------------- margen
def _tab_margen(sesion: Sesion) -> None:
    hoy = date.today()
    c1, c2 = st.columns(2)
    anio = c1.number_input("Año", min_value=2026, max_value=2031, value=hoy.year, step=1, key="mg_an")
    mes = c2.selectbox("Mes", list(range(1, 13)), index=hoy.month - 1, key="mg_mes",
                       format_func=lambda m: MESES[m - 1].capitalize())
    try:
        with con_bd() as con:
            r = ventas.calcular_margen(con, sesion, int(anio), int(mes))
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        r = None
    if r:
        st.caption(f"Costo unitario de referencia {bs(r['costo_unitario'])}/kg — {r['ciclo_referencia']}; margen mínimo "
                   f"{ventas.pct_texto(r['margen_minimo'])}.")
        filas = [{"canal": ventas.NOMBRE_CANAL[l["canal"]], "ingresos": l["ingresos"],
                  "kg": f"{Decimal(l['kg']).normalize():f}".replace(".", ","),
                  "precio": l["precio_promedio"] if l["precio_promedio"] is not None else None,
                  "margen": ventas.pct_texto(l["margen"]) if l["margen"] is not None else "Sin ventas",
                  "bajo": "Sí" if l["bajo_umbral"] else "No"} for l in r["lineas"]]
        st.dataframe(tabla(filas, [("canal", "Canal"), ("ingresos", "Ingresos del mes"), ("kg", "Kg"),
                                   ("precio", "Precio promedio"), ("margen", "Margen bruto"), ("bajo", "¿Bajo el 20 %?")],
                           montos=("ingresos", "precio")), hide_index=True, use_container_width=True)
        if sesion.rol == "GG" and st.button("Registrar análisis FIN-ING-08", type="primary"):
            try:
                with con_bd() as con:
                    a = ventas.registrar_analisis_margen(con, sesion, int(anio), int(mes))
                if a["ajuste_requerido"]:
                    st.error("Dos meses consecutivos bajo el mínimo en " + ", ".join(a["canales_ajuste"]) +
                             ": proponga el ajuste del tarifario al Consejo de Administración.")
                else:
                    st.success("Análisis registrado.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    with con_bd() as con:
        historial = ventas.listar_analisis(con, sesion)
    if historial:
        for a in historial:
            a["periodo"] = f"{MESES[a['mes'] - 1].capitalize()} {a['anio']}"
            a["ajuste"] = f"Sí ({a['canales_ajuste']})" if a["ajuste_requerido"] else "No"
        st.dataframe(tabla(historial, [("periodo", "Mes"), ("costo_unitario", "Costo Bs/kg"),
                                       ("ciclo_referencia", "Referencia"), ("ajuste", "¿Ajuste requerido?"),
                                       ("elaborado_por", "Elaboró")]), hide_index=True, use_container_width=True)
        elegido = st.selectbox("Análisis para descargar", historial, key="mg_pdf", format_func=lambda a: a["periodo"])
        _descargar("FIN-ING-08", pdf.fin_ing_08(elegido, _ctx(sesion)), "pdf_fining08")


# -------------------------------------------------------------------- formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Formatos del ciclo de ingresos en PDF, con el bloque de firmas.")
    hoy = date.today()
    with con_bd() as con:
        notas = ventas.listar_notas(con, sesion)
        despachos = ventas.listar_despachos(con, sesion)
        suspensiones = ventas.listar_suspensiones(con, sesion)
        resoluciones = ventas.listar_resoluciones(con, sesion)
        tarifarios = ventas.listar_tarifarios(con, sesion)
    opciones = {"FIN-ING-01": "Registro de Pedido", "FIN-ING-02": "Resolución de Autorización de Crédito",
                "FIN-ING-03": "Nota de Entrega (HORECA)", "FIN-ING-04": "Comprobante de Entrega y Compromiso de Pago",
                "FIN-ING-05": "Registro de Cuentas por Cobrar", "FIN-ING-06": "Nota de Entrega (Contado)",
                "CIR-ING-01": "Suspensión de Línea de Crédito", "CI-FIN-13": "Verificación de cobertura del lote",
                "TARIFARIO": "Tarifario oficial"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda k: f"{k} — {opciones[k]}", key="fm_cod_v")
    try:
        datos: bytes | None = None
        with con_bd() as con:
            if codigo in ("FIN-ING-01", "CI-FIN-13"):
                if not despachos:
                    st.info("No hay despachos.")
                    return
                d = st.selectbox("Despacho", despachos, key="fm_desp", format_func=lambda x: f"Lote {x['lote']}")
                datos = (pdf.fin_ing_01(ventas.listar_pedidos(con, sesion, int(d["id"])), _ctx(sesion, d["lote"]))
                         if codigo == "FIN-ING-01" else
                         pdf.cobertura_ci_fin_13(ventas.cobertura(con, int(d["id"])), d, _ctx(sesion, d["lote"])))
            elif codigo in ("FIN-ING-03", "FIN-ING-04", "FIN-ING-06"):
                candidatas = [n for n in notas if (n["tipo"] == "HORECA") == (codigo != "FIN-ING-06")
                              and (codigo != "FIN-ING-04" or n["condicion"] == "CREDITO")]
                if not candidatas:
                    st.info("No hay notas de ese tipo.")
                    return
                n = st.selectbox("Nota", candidatas, key="fm_nota",
                                 format_func=lambda x: f"n.º {x['numero']} · {x['cliente']} · {bs(x['total'])}")
                funcion = {"FIN-ING-03": pdf.fin_ing_03, "FIN-ING-04": pdf.fin_ing_04, "FIN-ING-06": pdf.fin_ing_06}[codigo]
                datos = funcion(ventas.obtener_nota(con, sesion, int(n["id"])), _ctx(sesion))
            elif codigo == "FIN-ING-05":
                datos = pdf.fin_ing_05(ventas.cartera(con, sesion, hoy), _ctx(sesion))
            elif codigo == "FIN-ING-02":
                if not resoluciones:
                    st.info("No hay resoluciones de crédito (régimen desde 2028).")
                    return
                r = st.selectbox("Resolución", resoluciones, key="fm_res", format_func=lambda x: f"n.º {x['id']} · "
                                                                                                 f"{x['cliente']}")
                datos = pdf.fin_ing_02(r, _ctx(sesion))
            elif codigo == "CIR-ING-01":
                if not suspensiones:
                    st.info("No hay circulares de suspensión.")
                    return
                s = st.selectbox("Circular", suspensiones, key="fm_cir",
                                 format_func=lambda x: f"{x['numero']}/{x['anio']} · {x['cliente']}")
                datos = pdf.cir_ing_01(s, _ctx(sesion))
            else:
                if not tarifarios:
                    st.info("No hay tarifarios.")
                    return
                t = st.selectbox("Tarifario", tarifarios, key="fm_tar",
                                 format_func=lambda x: f"n.º {x['id']} · desde {fecha_pantalla(x['vigente_desde'])} · "
                                                       f"{x['estado'].lower()}")
                datos = pdf.tarifario(ventas.detalle_tarifario(con, sesion, int(t["id"])), _ctx(sesion))
        if datos is not None:
            _descargar(codigo, datos, "pdf_formato_v")
    except ErrorPollolandia as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 7 — Ventas, HORECA y cobranza."""
    st.header("Ventas, HORECA y cobranza · Ciclo 4")
    _resumen(sesion)
    pestañas = st.tabs(["Clientes", "Tarifario", "Pre-venta del lote", "Notas de entrega", "Cartera y cobranza",
                        "Recaudación y devoluciones", "Margen por canal", "Formatos"])
    funciones = (_tab_clientes, _tab_tarifario, _tab_preventa, _tab_notas, _tab_cartera, _tab_recaudacion, _tab_margen,
                 _tab_formatos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
