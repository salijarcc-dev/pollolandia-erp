"""Módulo 5 — Costos matriciales (Fase 7, Ciclo 3 — Transformación).

Pestañas: Consumos y CIF, Mayor por sufijo, Alimento propio, Cierre e
informe, Presupuesto D.4, Productos terminados y Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. Finanzas registra el costeo;
la Gerencia General aprueba el Informe de Costo de Ciclo; los demás cargos
consultan.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_costos as pdf
from servicios import costos, inventarios, produccion, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
SEMAFORO = {"VERDE": "Verde", "AMARILLO": "Amarillo", "ROJO": "Rojo"}


def _dec(valor, decimales: int = 2) -> str:
    if valor is None:
        return "—"
    return f"{Decimal(str(valor)):,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ciclo(sesion: Sesion, clave: str, solo_cerrados: bool = False) -> dict | None:
    with con_bd() as con:
        ciclos = produccion.listar_ciclos(con, sesion)
        cerrados = {c["ciclo_id"] for c in costos.listar_cierres(con, sesion)}
    if solo_cerrados:
        ciclos = [c for c in ciclos if c["id"] in cerrados]
    if not ciclos:
        st.info("No hay ciclos con cierre de costos." if solo_cerrados else "Aún no hay ciclos productivos abiertos.")
        return None
    c = st.selectbox("Ciclo", ciclos, key=clave,
                     format_func=lambda x: f"Ciclo {x['numero']} · {x['ejercicio']} · desde "
                                           f"{fecha_pantalla(x['fecha_inicio'])} · "
                                           + ("costeado" if x["id"] in cerrados else x["estado"].capitalize()))
    return {**c, "costeado": c["id"] in cerrados}


def _resumen(sesion: Sesion) -> None:
    with con_bd() as con:
        r = costos.resumen_costos(con, sesion, date.today())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Costo del ciclo en curso", bs(r["acumulado"]) if r["acumulado"] is not None else "—",
              help=f"Ciclo {r['ciclo']['numero']} · {r['ciclo']['ejercicio']}" if r["ciclo"] else None)
    c2.metric("Último costo unitario", f"{bs(r['ultimo_unitario'])}/kg" if r["ultimo_unitario"] else "—",
              help=r["ultimo_ciclo"])
    c3.metric("Carne en inventario", f"{_dec(r['carne']['cantidad'], 1)} kg", help=bs(r["carne"]["valor"]))
    c4.metric("Alimento excedente", f"{_dec(r['excedente']['cantidad'], 1)} qq", help=bs(r["excedente"]["valor"]))


# ------------------------------------------------------------ consumos y CIF
def _tab_consumos(sesion: Sesion) -> None:
    hoy = date.today()
    es_jf = sesion.rol == "JF"
    c = _ciclo(sesion, "cs_ciclo")
    with con_bd() as con:
        insumos = [i for i in inventarios.existencias(con, sesion, hoy)
                   if i["cantidad_mil"] > 0 and i["codigo"] not in ("INS-0301", costos.INSUMO_SAQUILLOS)]
        cat = costos.catalogo_sufijos(con)
        p = seguridad.contexto_desde_bd(con, hoy).parametros
    if es_jf and c and not c["costeado"]:
        st.subheader("Consumo de inventario al CPP")
        with st.form("form_consumo", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            ins = c1.selectbox("Insumo", insumos, format_func=lambda i: f"{i['nombre']} · "
                               f"{inventarios.formato_cantidad(i['cantidad_mil'], i['unidad'])} · CPP {bs(i['cpp'])}")
            fecha = c2.date_input("Fecha", hoy, format="DD/MM/YYYY")
            cantidad = c3.number_input("Cantidad", min_value=0.0, step=1.0, format="%g")
            centro = c4.selectbox("Centro", list(costos.CENTROS))
            documento = st.text_input("Documento de respaldo (bitácora, vale de salida)")
            if st.form_submit_button("Registrar consumo", type="primary"):
                try:
                    with con_bd() as con:
                        r = costos.consumir_insumo(con, sesion, int(ins["insumo_id"]), fecha, decimal_de(cantidad),
                                                   centro=centro, ciclo_id=int(c["id"]) if centro == costos.CENTRO_CARNE
                                                   else None, documento_ref=documento or None)
                    st.success(f"Consumo a {centro}-{r['sufijo']}: {bs(r['valor'])} (CPP {bs(r['cpp'])}).")
                    if r["alerta_id"]:
                        st.warning("La existencia quedó bajo el punto de reorden (CI-FIN-16).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        a, b = st.columns([3, 1])
        hasta = a.date_input("Valorar el alimento de la bitácora hasta", hoy, format="DD/MM/YYYY", key="cs_bit")
        b.write("")
        if b.button("Valorar alimento (kg → qq)"):
            try:
                with con_bd() as con:
                    r = costos.consumir_alimento_bitacora(con, sesion, int(c["id"]), hasta)
                st.success(f"{_dec(r['kg'], 1)} kg = {_dec(r['qq'], 3)} qq al sufijo {r['sufijo']}: {bs(r['valor'])}.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        st.caption(f"Conversión: {p['kg_por_quintal']} kg por quintal. Desde el ejercicio "
                   f"{p['ejercicio_inicio_alimento_propio']} el alimento de las aves sale del inventario de alimento "
                   "producido (sufijo 11).")
    if es_jf:
        st.subheader("Costos indirectos de fabricación (sufijos 30-37)")
        cif_sufijos = [s for s, d in cat.items() if d["elemento"] == "CIF"]
        with st.form("form_cif", clear_on_submit=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            sufijo = c1.selectbox("Sufijo", cif_sufijos, format_func=lambda x: f"{x} — {cat[x]['nombre']}")
            fecha = c2.date_input("Fecha", hoy, format="DD/MM/YYYY", key="cif_f")
            monto = c3.number_input("Monto (Bs)", min_value=0.0, step=10.0, format="%.2f")
            concepto = st.text_input("Concepto")
            contra = st.text_input("Contrapartida (vacío = la del Plan de Cuentas para el sufijo)")
            documento = st.text_input("Documento de respaldo", key="cif_doc")
            if st.form_submit_button("Registrar CIF"):
                try:
                    with con_bd() as con:
                        r = costos.registrar_cif(con, sesion, sufijo, fecha, decimal_de(monto), concepto,
                                                 contrapartida=contra or None, documento_ref=documento or None)
                    st.success(f"CIF registrado: carne {bs(r['carne'])}, alimento {bs(r['alimento'])} "
                               f"({r['base'].lower()}).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        st.caption("Los sufijos comunes (36, 37) se prorratean desde 2027 por las horas de las hojas de tiempo "
                   "(CI-FIN-03). La depreciación mensual por activo llegará con la Fase 8.")
    with con_bd() as con:
        consumos = costos.listar_consumos(con, sesion, int(c["id"]) if c else None)
        cif = costos.listar_cif(con, sesion)
    for x in consumos:
        x["cant"] = _dec(x["cantidad"], 3)
    st.markdown("**Consumos valorados**")
    st.dataframe(tabla(consumos, [("fecha", "Fecha"), ("insumo", "Insumo / producto"), ("cant", "Cantidad"),
                                  ("centro_costo", "Centro"), ("sufijo", "Sufijo"), ("valor", "Valor"),
                                  ("origen", "Origen"), ("asiento_id", "Asiento")], montos=("valor",), fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    st.markdown("**CIF registrados**")
    st.dataframe(tabla(cif, [("fecha", "Fecha"), ("sufijo", "Sufijo"), ("concepto", "Concepto"), ("monto", "Monto"),
                             ("carne", "Carne"), ("alimento", "Alimento"), ("base_prorrateo", "Base"),
                             ("contrapartida", "Contrapartida")], montos=("monto", "carne", "alimento"),
                       fechas=("fecha",)), hide_index=True, use_container_width=True)


# ------------------------------------------------------------ mayor por sufijo
def _tab_mayor(sesion: Sesion) -> None:
    c = _ciclo(sesion, "my_ciclo")
    if c is None:
        return
    with con_bd() as con:
        m = costos.mayor_analitico(con, sesion, int(c["id"]))
    for f in m["filas"]:
        f["elemento_txt"] = costos.NOMBRE_ELEMENTO[f["elemento"]]
    st.caption(("Desde el " + fecha_pantalla(m["desde"]) if m["desde"] else "Desde la apertura") +
               f" hasta el {fecha_pantalla(m['hasta'])}, sin el traslado del cierre (FIN-TRA-03).")
    cols = st.columns(4)
    for col, e in zip(cols, costos.ELEMENTOS):
        col.metric(costos.NOMBRE_ELEMENTO[e], bs(m["por_elemento"][e]))
    st.dataframe(tabla(m["filas"] + [{"sufijo": "", "nombre": "TOTAL", "elemento_txt": "", "monto": m["total"]}],
                       [("sufijo", "Sufijo"), ("nombre", "Concepto"), ("elemento_txt", "Elemento"), ("monto", "Acumulado")],
                       montos=("monto",)), hide_index=True, use_container_width=True)
    with st.expander(f"Movimientos del centro 6.1.01.01 ({len(m['movimientos'])})"):
        st.dataframe(tabla(m["movimientos"], [("fecha", "Fecha"), ("asiento_id", "Asiento"), ("sufijo", "Sufijo"),
                                              ("glosa", "Glosa"), ("debe", "Debe"), ("haber", "Haber")],
                           montos=("debe", "haber"), fechas=("fecha",), ocultar_ceros=True),
                     hide_index=True, use_container_width=True)


# ------------------------------------------------------------ alimento propio
def _tab_alimento(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        vigente = costos.alimento_propio_vigente(con, hoy)
        saldo_a = costos.saldo_terminado(con, "ALIMENTO")
        saldo_e = costos.saldo_terminado(con, "EXCEDENTE")
        centro = costos.saldos_centro(con, costos.CENTRO_ALIMENTO, hoy)
        p = seguridad.contexto_desde_bd(con, hoy).parametros
    st.caption("Manual de Costos §6.4: el quintal producido va al consumo propio (sufijo 11, al costo y sin margen) o "
               "al excedente, que suma el saquillo de yute (sufijo 13) y se vende con costo 6.1.02.02.")
    a, b, d = st.columns(3)
    a.metric("Costo acumulado en 6.1.01.02", bs(sum(centro.values(), Decimal("0"))))
    b.metric("Alimento producido en inventario", f"{_dec(saldo_a['cantidad'], 1)} qq",
             help=f"CPP {bs(saldo_a['cpp'])} por qq")
    d.metric("Excedente ensaquillado", f"{_dec(saldo_e['cantidad'], 1)} qq", help=f"CPP {bs(saldo_e['cpp'])} por qq")
    if not vigente:
        st.info(f"El centro de alimento balanceado rige desde el ejercicio {p['ejercicio_inicio_alimento_propio']}.")
        return
    if sesion.rol != "JF":
        return
    c1, c2, c3 = st.columns(3)
    with c1.form("form_prod"):
        st.markdown("**Producción del lote**")
        fecha = st.date_input("Fecha", hoy, format="DD/MM/YYYY", key="al_pf")
        qq = st.number_input("Quintales producidos", min_value=0.0, step=1.0, format="%g", key="al_pq")
        if st.form_submit_button("Registrar producción"):
            try:
                with con_bd() as con:
                    r = costos.registrar_produccion_alimento(con, sesion, fecha, decimal_de(qq))
                st.success(f"{_dec(r['qq'], 1)} qq a {bs(r['costo_qq'])} por qq.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    with c2.form("form_propio"):
        st.markdown("**Consumo propio (sufijo 11)**")
        with con_bd() as con:
            abiertos = [x for x in produccion.listar_ciclos(con, sesion)
                        if x["id"] not in {k["ciclo_id"] for k in costos.listar_cierres(con, sesion)}]
        ciclo = st.selectbox("Ciclo", abiertos, format_func=lambda x: f"C{x['numero']} · {x['ejercicio']}") \
            if abiertos else None
        fecha = st.date_input("Fecha", hoy, format="DD/MM/YYYY", key="al_cf")
        qq = st.number_input("Quintales", min_value=0.0, step=1.0, format="%g", key="al_cq")
        if st.form_submit_button("Trasladar a 6.1.01.01-11"):
            try:
                if ciclo is None:
                    raise ValueError("No hay un ciclo de carne abierto.")
                with con_bd() as con:
                    r = costos.trasladar_alimento_propio(con, sesion, int(ciclo["id"]), fecha, decimal_de(qq))
                st.success(f"Trasladado al costo: {bs(r['valor'])}.")
            except (ErrorPollolandia, ValueError) as exc:
                mostrar_error(exc)
    with c3.form("form_saq"):
        st.markdown("**Ensaquillado del excedente (sufijo 13)**")
        fecha = st.date_input("Fecha", hoy, format="DD/MM/YYYY", key="al_sf")
        qq = st.number_input("Quintales", min_value=0.0, step=1.0, format="%g", key="al_sq")
        if st.form_submit_button("Ensaquillar"):
            try:
                with con_bd() as con:
                    r = costos.ensaquillar_excedente(con, sesion, fecha, decimal_de(qq))
                st.success(f"Excedente a {bs(r['costo_qq'])} por qq (alimento {bs(r['costo_base'])} + saquillos "
                           f"{bs(r['costo_saquillos'])}).")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ------------------------------------------------------------ cierre e informe
def _tab_cierre(sesion: Sesion) -> None:
    c = _ciclo(sesion, "ci_ciclo")
    if c is None:
        return
    if not c["costeado"]:
        with con_bd() as con:
            m = costos.mayor_analitico(con, sesion, int(c["id"]))
            fecha_corte = max(date.fromisoformat(c["fecha_cierre"]), date.today()) \
                if c["estado"] != "CONCLUIDO" else date.fromisoformat(c["fecha_cierre"])
            corte = costos.calcular_corte_mod(con, int(c["id"]), fecha_corte)
        st.markdown(f"Estado operativo: {chip(c['estado'])} · costo acumulado {bs(m['total'])} · corte de MOD estimado "
                    f"{bs(corte['total'])} ({corte['dias']} días sin planilla)", unsafe_allow_html=True)
        if c["estado"] != "CONCLUIDO":
            st.info("El cierre de costos exige el ciclo operativo concluido (faenado, bajas y conciliación de aves).")
        elif sesion.rol == "JF" and st.button("Cerrar el ciclo (1.1.03.12 contra 6.1.01.01)", type="primary"):
            try:
                with con_bd() as con:
                    r = costos.cerrar_ciclo(con, sesion, int(c["id"]))
                st.success(f"Ciclo cerrado: {bs(r['total'])} · {_dec(r['kg'], 1)} kg · {bs(r['costo_unitario'])} por kg.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        return
    with con_bd() as con:
        inf = costos.informe_costo_ciclo(con, sesion, int(c["id"]))
    conc = inf["conciliacion"]
    a, b, d, e = st.columns(4)
    a.metric("Costo total del ciclo", bs(inf["total"]))
    b.metric("Kg de producto terminado", _dec(inf["kg_producidos"], 1))
    d.metric("Costo unitario", f"{bs(inf['costo_unitario'])}/kg",
             delta=f"{costos.pct_texto(inf['variacion_pct'])} vs ciclo anterior" if inf["variacion_pct"] is not None
             else None, delta_color="inverse")
    e.metric("ICA del ciclo", _dec(inf["ica"]))
    filas = [{"c": costos.NOMBRE_ELEMENTO[k], "m": v} for k, v in inf["por_elemento"].items() if v]
    st.dataframe(tabla(filas + [{"c": "TOTAL", "m": inf["total"]}], [("c", "Elemento"), ("m", "Monto")], montos=("m",)),
                 hide_index=True, use_container_width=True)
    st.markdown(f"**Conciliación de aves:** ingresadas {conc['ingresadas']} = mortalidad normal {conc['muertas_normal']} "
                f"+ anormal {conc['muertas_anormal']} + faenadas {conc['faenadas']} + faltante {conc['faltante']}.")
    if inf["mortalidad_anormal_monto"]:
        st.caption(f"Mortalidad anormal reconocida en 7.2.01.04, fuera del costo: {bs(inf['mortalidad_anormal_monto'])}.")
    if inf["aprobado_por"]:
        st.success(f"Aprobado por la Gerencia General ({inf['aprobado_por']}, {fecha_pantalla(inf['aprobado_en'][:10])}).")
    elif sesion.rol == "GG":
        obs = st.text_input("Observación de la Gerencia", key="ci_obs")
        if st.button("Aprobar el Informe de Costo de Ciclo", type="primary"):
            try:
                with con_bd() as con:
                    costos.aprobar_informe(con, sesion, int(c["id"]), obs or None)
                st.success("Informe aprobado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    with con_bd() as con:
        cierres = costos.listar_cierres(con, sesion)
    for k in cierres:
        k["ciclo_txt"] = f"C{k['numero']} · {k['ejercicio']}"
        k["kg_txt"] = _dec(k["kg"], 1)
        k["var"] = costos.pct_texto(k["variacion_pct"]) if k["variacion_pct"] is not None else "—"
    st.markdown("**Ciclos costeados**")
    st.dataframe(tabla(cierres, [("ciclo_txt", "Ciclo"), ("fecha_cierre", "Cierre"), ("total", "Costo total"),
                                 ("kg_txt", "Kg"), ("costo_unitario", "Bs/kg"), ("var", "Variación"),
                                 ("aprobado_por", "Aprobado por")], montos=("total", "costo_unitario"),
                       fechas=("fecha_cierre",)), hide_index=True, use_container_width=True)


# ------------------------------------------------------------ presupuesto D.4
def _tab_presupuesto(sesion: Sesion) -> None:
    c = _ciclo(sesion, "pp_ciclo")
    if c is None:
        return
    with con_bd() as con:
        comp = costos.comparar_presupuesto(con, sesion, int(c["id"]))
    if not comp["con_presupuesto"]:
        st.info("No hay presupuesto D.4 cargado para el ejercicio de este ciclo.")
    a, b, d = st.columns(3)
    a.metric("Presupuesto D.4", bs(comp["presupuesto"]))
    b.metric("Real del ciclo", bs(comp["real"]))
    d.metric("Variación", bs(comp["variacion"]), delta=costos.pct_texto(comp["variacion_pct"])
             if comp["variacion_pct"] is not None else None, delta_color="inverse")
    for f in comp["filas"]:
        f["pct"] = costos.pct_texto(f["variacion_pct"]) if f["variacion_pct"] is not None else "—"
        dec = 0 if f["unidad"] == "ave" else 2
        f["qp"] = _dec(f["cantidad_presupuesto"], dec) if f["cantidad_presupuesto"] is not None else ""
        f["qr"] = _dec(f["cantidad_real"], dec) if f["cantidad_real"] is not None else ""
        f["sem"] = SEMAFORO[f["semaforo"]]
    st.dataframe(tabla(comp["filas"], [("sufijo", "Suf."), ("concepto", "Concepto"), ("qp", "Q presup."),
                                       ("precio_presupuesto", "P presup."), ("presupuesto", "Presupuesto"),
                                       ("qr", "Q real"), ("precio_real", "P real"), ("real", "Real"),
                                       ("variacion", "Variación"), ("pct", "%"), ("variacion_precio", "Var. precio"),
                                       ("variacion_cantidad", "Var. cantidad"), ("sem", "Semáforo")],
                       montos=("precio_presupuesto", "presupuesto", "precio_real", "real", "variacion",
                               "variacion_precio", "variacion_cantidad")), hide_index=True, use_container_width=True)
    st.caption("Variación de precio = (P real − P presupuestado) × Q real; de cantidad = (Q real − Q presupuestada) × "
               "P presupuestado. En costos, positiva es desfavorable (Manual Presupuestario E.1). La mortalidad normal "
               "se reclasifica al sufijo 40 (sin partida en D.4) desde los demás sufijos en proporción a su saldo.")


# ------------------------------------------------------- productos terminados
def _tab_terminados(sesion: Sesion) -> None:
    producto = st.selectbox("Producto", list(costos.CUENTA_PRODUCTO), format_func=costos.NOMBRE_PRODUCTO.get,
                            key="pt_prod")
    with con_bd() as con:
        movimientos = costos.kardex_productos(con, sesion, producto)
        saldo = costos.saldo_terminado(con, producto)
    unidad = costos.UNIDAD_PRODUCTO[producto]
    st.caption(f"Cuenta {costos.CUENTA_PRODUCTO[producto]} · saldo {_dec(saldo['cantidad'], 3)} {unidad} · "
               f"{bs(saldo['valor'])} · CPP {bs(saldo['cpp'])} por {unidad}")
    for m in movimientos:
        m["cant"] = _dec(m["cantidad"], 3)
        m["saldo_cant"] = _dec(m["saldo_cantidad"], 3)
        m["tipo_txt"] = m["tipo"].replace("_", " ").capitalize()
    st.dataframe(tabla(movimientos, [("fecha", "Fecha"), ("tipo_txt", "Movimiento"), ("cant", f"Cantidad ({unidad})"),
                                     ("valor", "Valor"), ("saldo_cant", "Saldo"), ("saldo_valor", "Saldo valorado"),
                                     ("cpp", "CPP"), ("documento_ref", "Documento")],
                       montos=("valor", "saldo_valor", "cpp"), fechas=("fecha",)), hide_index=True, use_container_width=True)
    if sesion.rol == "JF" and producto in costos.COSTO_VENTA:
        with st.form("form_venta", clear_on_submit=True):
            st.markdown(f"**Costo de ventas ({costos.COSTO_VENTA[producto]})** — lo registrará la venta (Fase 9)")
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha", date.today(), format="DD/MM/YYYY")
            cantidad = c2.number_input(f"Cantidad vendida ({unidad})", min_value=0.0, step=1.0, format="%g")
            nota = c3.text_input("Nota de entrega")
            if st.form_submit_button("Registrar costo de ventas"):
                try:
                    with con_bd() as con:
                        r = costos.registrar_costo_venta(con, sesion, producto, fecha, decimal_de(cantidad), nota)
                    st.success(f"Costo de ventas {bs(r['valor'])} (CPP {bs(r['cpp'])}).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# -------------------------------------------------------------------- formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Formatos del sistema de costos en PDF, con el bloque de firmas.")
    c = _ciclo(sesion, "fm_ciclo_c")
    if c is None:
        return
    ctx = pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo, cargo=CARGO_ROL.get(sesion.rol, sesion.rol),
                       ciclo=f"{c['numero']} ({c['ejercicio']})")
    opciones = {"FIN-TRA-03": "Mayor Analítico por Sufijo", "FIN-TRA-06": "Informe de Costo de Ciclo",
                "FIN-TRA-07": "Cálculo de Costo Unitario", "PPTO-D4": "Comparación contra el presupuesto D.4"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda k: f"{k} — {opciones[k]}", key="fm_cod_c")
    try:
        with con_bd() as con:
            cid = int(c["id"])
            datos: bytes | None = None
            if codigo == "FIN-TRA-03":
                datos = pdf.fin_tra_03(costos.mayor_analitico(con, sesion, cid), ctx)
            elif codigo == "PPTO-D4":
                datos = pdf.comparacion_d4(costos.comparar_presupuesto(con, sesion, cid), ctx)
            elif c["costeado"]:
                inf = costos.informe_costo_ciclo(con, sesion, cid)
                datos = (pdf.fin_tra_06 if codigo == "FIN-TRA-06" else pdf.fin_tra_07)(inf, ctx)
        if datos is None:
            st.info("Este formato se emite con el cierre de costos del ciclo.")
        else:
            st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                               mime="application/pdf", type="primary")
    except ErrorPollolandia as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 5 — Costos matriciales."""
    st.header("Costos matriciales · Ciclo 3")
    _resumen(sesion)
    pestañas = st.tabs(["Consumos y CIF", "Mayor por sufijo", "Alimento propio", "Cierre e informe", "Presupuesto D.4",
                        "Productos terminados", "Formatos"])
    funciones = (_tab_consumos, _tab_mayor, _tab_alimento, _tab_cierre, _tab_presupuesto, _tab_terminados, _tab_formatos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
