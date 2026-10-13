"""Módulo 8 — Contabilidad general y estados financieros (Fase 10, Ciclo 5).

Pestañas: Cierre mensual (FIN-INF-01), Costeo e indicadores (FIN-INF-03 y
FIN-INF-04), Estados financieros (MAN-006 §18), Notas 1-16, Cierre anual,
Excedentes (CI-FIN-02) y Plazos 60/90 días.

Solo llama a ``servicios/`` y a ``reportes/``. La Unidad de Finanzas prepara
listas, cierre anual, propuestas y pagos; la Gerencia General cierra meses y
el ejercicio, presenta los estados y firma liquidaciones; el Consejo de
Vigilancia emite el INF-CV-04; la Asamblea aprueba (firma en «Autorizaciones»).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Sequence

import pandas as pd
import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import estados as rep
from reportes import pdf_contable as pc
from servicios import cierre, contable, identidad, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
ESTADO_PLAZO = {"CUMPLIDO": "BUENO", "CUMPLIDO_FUERA_DE_PLAZO": "NORMAL", "EN_PLAZO": "ABIERTO",
                "POR_VENCER": "NORMAL", "VENCIDO": "MALO"}


def _ctx(sesion: Sesion, detalle: str = "") -> rep.Contexto:
    return rep.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                        cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo=detalle)


def _pdf(nombre: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {nombre} (PDF)", datos, file_name=f"{nombre}.pdf", mime="application/pdf", key=clave)


def _me(sesion: Sesion) -> tuple[pc.Membrete, pc.Emision]:
    """Membrete institucional y datos de emisión para los PDF oficiales."""
    with con_bd() as con:
        return identidad.membrete(con), identidad.emision(sesion)


def _mek(sesion: Sesion) -> dict[str, Any]:
    m, e = _me(sesion)
    return {"membrete": m, "emision": e}


def _xlsx(nombre: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {nombre} (Excel)", datos, file_name=f"{nombre}.xlsx", key=clave,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _df_estado(filas: Sequence[dict[str, Any]], con_notas: bool = False) -> pd.DataFrame:
    datos = []
    for f in filas:
        sangria = "  " if f["tipo"] == "linea" else (" " if f["tipo"] == "subtitulo" else "")
        fila = {"Concepto": sangria + f["concepto"]}
        if con_notas:
            fila["Notas"] = f.get("nota", "")
        fila["Bs"] = rep.numero(f["monto"])
        datos.append(fila)
    return pd.DataFrame(datos)


def _pct(valor: Any) -> str:
    return "—" if valor is None else f"{Decimal(str(valor)) * 100:.2f} %".replace(".", ",")


def _ejercicio(clave: str) -> int | None:
    with con_bd() as con:
        lista = cierre.ejercicios(con)
    if not lista:
        st.info("No hay ejercicios registrados.")
        return None
    hoy = date.today().isoformat()
    actual = next((i for i, e in enumerate(lista) if e["fecha_inicio"] <= hoy <= e["fecha_fin"]), 0)
    elegido = st.selectbox("Ejercicio (1-jul a 30-jun)", lista, index=actual, key=clave,
                           format_func=lambda e: f"{e['ejercicio']} · {fecha_pantalla(e['fecha_inicio'])} al "
                                                 f"{fecha_pantalla(e['fecha_fin'])} · {e['estado'].lower()}")
    return int(elegido["ejercicio"])


def _alertas(sesion: Sesion) -> None:
    try:
        with con_bd() as con:
            alertas = cierre.alertas_plazos(con, sesion)
    except ErrorPollolandia:
        return
    for a in alertas:
        (st.error if a["estado"] == "VENCIDO" else st.warning)(a["mensaje"])


# ------------------------------------------------------------- cierre mensual
def _tab_cierre_mensual(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cm_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        periodos = cierre.periodos_ejercicio(con, sesion, ejercicio)
    for p in periodos:
        p["lista"] = "—" if p["lista_pendientes"] is None else f"{p['lista_pendientes']} pendiente(s)"
    st.dataframe(tabla(periodos, [("numero", "N.º"), ("nombre", "Período"), ("estado", "Estado"),
                                  ("asientos", "Asientos"), ("lista", "Última FIN-INF-01"),
                                  ("lista_fecha", "Fecha lista"), ("cerrado_por", "Cerrado por")],
                       fechas=("lista_fecha",)), hide_index=True, use_container_width=True)
    abiertos = [p for p in periodos if p["estado"] == "ABIERTO"]
    if not abiertos:
        st.success("Todos los períodos del ejercicio están cerrados.")
        return
    periodo = abiertos[0]
    st.subheader(f"FIN-INF-01 · {periodo['nombre']}")
    with con_bd() as con:
        lista = cierre.lista_verificacion(con, sesion, int(periodo["id"]))
    st.dataframe(pd.DataFrame([{"Verificación": f["verificacion"], "Cumple": "Sí" if f["cumple"] else "No",
                                "N° de comprobantes pendientes": f["pendientes"]} for f in lista["filas"]]),
                 hide_index=True, use_container_width=True)
    for f in lista["filas"]:
        for d in f["detalle"]:
            st.caption(f"• {d}")
    st.markdown(f"Pendientes totales: {chip('BUENO' if lista['pendientes'] == 0 else 'MALO')} "
                f"**{lista['pendientes']}**", unsafe_allow_html=True)
    _pdf(f"FIN-INF-01_{periodo['nombre'].replace(' ', '_')}", rep.fin_inf_01(lista, _ctx(sesion, periodo["nombre"]), **_mek(sesion)),
         "cm_pdf")
    c1, c2 = st.columns(2)
    if sesion.rol == "JF" and c1.button("Registrar FIN-INF-01 (Verificado por la Unidad de Finanzas)", key="cm_reg"):
        try:
            with con_bd() as con:
                r = cierre.registrar_lista_verificacion(con, sesion, int(periodo["id"]))
            st.success(f"FIN-INF-01 registrada con {r['pendientes']} pendiente(s).")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if sesion.rol == "GG":
        confirmar = c2.checkbox("Confirmo la conciliación del mes y el cierre (el período no se reabre)", key="cm_conf")
        if c2.button("Cerrar el mes", type="primary", disabled=not confirmar, key="cm_cerrar"):
            try:
                with con_bd() as con:
                    r = cierre.cerrar_mes(con, sesion, int(periodo["id"]))
                st.success(f"Período de {r['periodo']} cerrado; paquete FIN-INF-03/04 archivado.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ------------------------------------------------------ costeo e indicadores
def _tab_indicadores(sesion: Sesion) -> None:
    ejercicio = _ejercicio("ind_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        periodos = cierre.periodos_ejercicio(con, sesion, ejercicio)
    hoy = date.today().isoformat()
    defecto = next((i for i, p in enumerate(periodos) if p["fecha_inicio"] <= hoy <= p["fecha_fin"]), 0)
    periodo = st.selectbox("Mes", periodos, index=defecto, key="ind_mes",
                           format_func=lambda p: f"{p['nombre']} · {p['estado'].lower()}")
    with con_bd() as con:
        costeo = cierre.consolidado_costeo(con, sesion, int(periodo["id"]))
        informe = cierre.estado_resultados_mensual(con, sesion, int(periodo["id"]))
        archivado = cierre.paquete_cierre(con, sesion, int(periodo["id"]))
    if archivado:
        st.caption(f"Mes cerrado el {fecha_pantalla(archivado['cerrado_en'])}: el paquete archivado conserva las cifras "
                   "del cierre.")
    st.subheader("FIN-INF-03 · Consolidado de costeo mensual")
    if costeo["ciclos"]:
        st.dataframe(tabla(costeo["ciclos"], [("ciclo", "Ciclo"), ("fecha_cierre", "Cierre"),
                                              ("costo_total", "Costo total"), ("kg_producidos", "Kg terminados"),
                                              ("costo_unitario", "Bs/kg")], montos=("costo_total",),
                           fechas=("fecha_cierre",)), hide_index=True, use_container_width=True)
    else:
        st.caption("Sin ciclos con Informe de Costo cerrado en el mes.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Costo de ventas del mes", bs(costeo["costo_ventas"]))
    c2.metric("Kg vendidos (notas)", f"{costeo['kg_vendidos']:,.3f}".replace(",", "X").replace(".", ",")
              .replace("X", "."))
    c3.metric("Costo de ciclos cerrados", bs(costeo["costo_ciclos"]))
    st.subheader("FIN-INF-04 · Estado de resultados mensual e indicadores")
    st.dataframe(pd.DataFrame([{"Concepto": m["concepto"], "Mes": rep.numero(m["monto"]),
                                "Acumulado del ejercicio": rep.numero(a["monto"])}
                               for m, a in zip(informe["mes"]["filas"], informe["acumulado"]["filas"])]),
                 hide_index=True, use_container_width=True)
    columnas = st.columns(3)
    for col, clave in zip(columnas, ("ICL", "RINC", "ROA")):
        m, a = informe["indicadores_mes"][clave], informe["indicadores_acumulado"][clave]
        col.markdown(f"**{clave}** · {m['formula']}")
        col.markdown(f"Mes: **{_pct(m['valor'])}** {chip(m['semaforo']) if m['semaforo'] else ''}  \n"
                     f"Acumulado: **{_pct(a['valor'])}** {chip(a['semaforo']) if a['semaforo'] else ''}",
                     unsafe_allow_html=True)
        col.caption(informe["umbrales"][clave])
    ctx = _ctx(sesion, periodo["nombre"])
    d1, d2, d3 = st.columns(3)
    with d1:
        _pdf("FIN-INF-03", rep.fin_inf_03(costeo, ctx, **_mek(sesion)), "ind_pdf3")
    with d2:
        _pdf("FIN-INF-04", rep.fin_inf_04(informe, ctx, **_mek(sesion)), "ind_pdf4")
    with d3:
        _xlsx("FIN-INF-04", rep.fin_inf_04_excel(informe), "ind_xls4")


# -------------------------------------------------------- estados financieros
def _tab_estados(sesion: Sesion) -> None:
    ejercicio = _ejercicio("ef_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        inicio, fin = cierre.rango_ejercicio(con, ejercicio)
        cerrado = any(e["ejercicio"] == ejercicio and e["cierre_anual"] for e in cierre.ejercicios(con))
    defecto = fin if cerrado else min(fin, max(inicio, date.today()))
    corte = st.date_input("Fecha de corte", defecto, min_value=inicio, max_value=fin,
                          format="DD/MM/YYYY", key="ef_corte")
    try:
        with con_bd() as con:
            bg = cierre.balance_general(con, sesion, corte)
            er = cierre.estado_resultados(con, sesion, ejercicio, corte)
            efe = cierre.estado_flujo_efectivo(con, sesion, ejercicio, corte)
            ee = cierre.estado_evolucion_patrimonio(con, sesion, ejercicio, corte)
            ind = cierre.indicadores_anuales(con, sesion, ejercicio)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total activo", bs(bg["total_activo"]))
    c2.metric("Ingresos totales", bs(er["total_ingresos"]))
    c3.metric("Excedente neto", bs(er["excedente_neto"]))
    c4.metric("Saldo final de efectivo", bs(efe["saldo_final"]))
    controles = [("Balance (Activo = Pasivo + Patrimonio)", bg["cuadra"]), ("Resultados", er["cuadra"]),
                 ("Flujo = disponibilidades", efe["cuadra"]), ("EEPN = patrimonio", ee["cuadra"])]
    st.markdown(" · ".join(f"{n}: {chip('BUENO' if ok else 'MALO')}" for n, ok in controles), unsafe_allow_html=True)
    t1, t2, t3, t4 = st.tabs(["Balance General", "Estado de Resultados", "Flujo de Efectivo", "Evolución del Patrimonio"])
    with t1:
        st.dataframe(_df_estado(bg["filas"], True), hide_index=True, use_container_width=True, height=620)
        _pdf("Balance_General", rep.balance_pdf(bg, *_me(sesion)), "ef_bg")
    with t2:
        st.dataframe(_df_estado(er["filas"]), hide_index=True, use_container_width=True, height=620)
        st.caption(" · ".join(f"{k}: {_pct(v['valor'])} ({(v['semaforo'] or '—').lower()})" for k, v in ind.items()))
        _pdf("Estado_de_Resultados", rep.resultados_pdf(er, *_me(sesion)), "ef_er")
    with t3:
        st.dataframe(_df_estado(efe["filas"]), hide_index=True, use_container_width=True, height=620)
        _pdf("Flujo_de_Efectivo", rep.flujo_pdf(efe, *_me(sesion)), "ef_efe")
    with t4:
        st.dataframe(pd.DataFrame([{"Concepto": f["concepto"], **{n: rep.numero(f[c]) for c, n in ee["columnas"]},
                                    "Total": rep.numero(f["total"])} for f in ee["filas"]]),
                     hide_index=True, use_container_width=True)
        _pdf("Evolucion_Patrimonio", rep.patrimonio_pdf(ee, *_me(sesion)), "ef_ee")
    if corte == fin:
        with con_bd() as con:
            paquete = cierre.estados_financieros(con, sesion, ejercicio)
        d1, d2 = st.columns(2)
        with d1:
            _pdf(f"EEFF_{ejercicio}", rep.paquete_pdf(paquete, None, *_me(sesion)), "ef_paq")
        with d2:
            _xlsx(f"EEFF_{ejercicio}", rep.paquete_excel(paquete), "ef_xls")


# --------------------------------------------------------------------- notas
def _tab_notas(sesion: Sesion) -> None:
    ejercicio = _ejercicio("nt_ej")
    if ejercicio is None:
        return
    hechos = st.text_area("Nota 16 — hechos posteriores (vacío = no se identificaron)", key="nt_hechos")
    with con_bd() as con:
        notas = cierre.generar_notas(con, sesion, ejercicio, hechos_posteriores=hechos or None)
        _, fin = cierre.rango_ejercicio(con, ejercicio)
    _pdf(f"Notas_EEFF_{ejercicio}", rep.notas_pdf(notas, fin.isoformat(), *_me(sesion)), "nt_pdf")
    for nota in notas:
        with st.expander(f"NOTA {nota['numero']} — {nota['titulo']}"):
            for b in nota["bloques"]:
                if b["tipo"] == "parrafo":
                    st.write(b["texto"])
                elif b["tipo"] == "subtitulo":
                    st.markdown(f"**{b['texto']}**")
                else:
                    st.dataframe(pd.DataFrame(b["filas"], columns=b["encabezados"]), hide_index=True,
                                 use_container_width=True)


# --------------------------------------------------------------- cierre anual
def _tab_cierre_anual(sesion: Sesion) -> None:
    ejercicio = _ejercicio("ca_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        previa = cierre.preparar_cierre_anual(con, sesion, ejercicio)
    if previa["registrado"]:
        r = previa["registrado"]
        st.success(f"Cierre anual registrado el {fecha_pantalla(r['registrado_en'])} por {r['registrado_por']}: "
                   f"resultado {bs(Decimal(r['resultado_cent']) / 100)}.")
        m, e = _me(sesion)
        columnas = st.columns(2)
        for i, clave in enumerate(("asiento_cierre_id", "asiento_traslado_id")):
            if r.get(clave):
                with con_bd() as con:
                    a = contable.obtener_asiento(con, sesion, int(r[clave]))
                with columnas[i]:
                    _pdf(f"Comprobante_{a['comprobante']}", pc.comprobante(a, m, e), f"ca_comp_{clave}")
    st.dataframe(pd.DataFrame([{"Requisito": r["requisito"], "Cumple": "Sí" if r["cumple"] else "No",
                                "Detalle": r["detalle"]} for r in previa["requisitos"]]),
                 hide_index=True, use_container_width=True)
    if previa["lineas"]:
        st.markdown(f"**Asiento de cierre al {fecha_pantalla(previa['fecha'])}** (resultado "
                    f"{'excedente' if previa['es_excedente'] else 'déficit'} {bs(previa['resultado'])}; total "
                    f"{bs(previa['total'])})")
        st.dataframe(tabla(previa["lineas"], [("cuenta", "Cuenta"), ("debe", "Debe"), ("haber", "Haber")],
                           montos=("debe", "haber"), ocultar_ceros=True), hide_index=True, use_container_width=True)
    if sesion.rol == "JF" and previa["listo"]:
        c1, c2 = st.columns(2)
        if c1.button("Solicitar autorización de la GG (AJUSTE_CONTABLE)", key="ca_sol"):
            try:
                with con_bd() as con:
                    ctx = seguridad.contexto_desde_bd(con, previa["fecha"])
                    aid = seguridad.solicitar_autorizacion(con, sesion, "AJUSTE_CONTABLE", previa["total"],
                                                           f"Cierre anual del ejercicio {ejercicio}", ctx,
                                                           referencia=f"CIERRE-{ejercicio}")
                st.success(f"Solicitud N.º {aid} enviada: la Gerencia General la firma en «Autorizaciones».")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        with con_bd() as con:
            validas = [a for a in seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                                  tipo_decision="AJUSTE_CONTABLE")
                       if Decimal(a["monto"]) >= Decimal(previa["total"])]
        if validas:
            aut = c2.selectbox("Autorización firmada", validas, key="ca_aut",
                               format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])} · {a['descripcion']}")
            if c2.button("Ejecutar cierre anual", type="primary", key="ca_ejec"):
                try:
                    with con_bd() as con:
                        r = cierre.ejecutar_cierre_anual(con, sesion, ejercicio, int(aut["id"]))
                    st.success(f"Cierre anual registrado: {r['asiento_cierre']} y {r['asiento_traslado'] or '—'}.")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if sesion.rol == "GG" and previa["registrado"] and not previa["registrado"].get("ejercicio_cerrado_en"):
        if st.button("Cerrar el ejercicio (requiere junio cerrado)", key="ca_ej_cerrar"):
            try:
                with con_bd() as con:
                    cierre.cerrar_ejercicio(con, sesion, ejercicio)
                st.success(f"Ejercicio {ejercicio} cerrado.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# ------------------------------------------------------------------ excedentes
def _tab_excedentes(sesion: Sesion) -> None:
    with con_bd() as con:
        distribuciones = cierre.listar_distribuciones(con, sesion)
        ejercicios = [e for e in cierre.ejercicios(con) if e["cierre_anual"]]
    for d in distribuciones:
        d["ejercicio_txt"] = str(d["ejercicio"])
    if distribuciones:
        st.dataframe(tabla(distribuciones, [("id", "N.º"), ("ejercicio_txt", "Ejercicio"), ("estado", "Estado"),
                                            ("excedente", "Excedente neto"), ("reserva_legal", "Reserva Legal"),
                                            ("educacion", "Educación"), ("reinversion", "Reinversión"),
                                            ("distribuible", "Distribuible"), ("inf_cv_04", "INF-CV-04"),
                                            ("acta_ref", "Acta AG")],
                           montos=("excedente", "reserva_legal", "educacion", "reinversion", "distribuible")),
                     hide_index=True, use_container_width=True)
    if sesion.rol == "JF" and ejercicios:
        with st.expander("Proponer distribución (CI-FIN-02 Art. 6)"):
            ej = st.selectbox("Ejercicio con cierre anual", [e["ejercicio"] for e in ejercicios], key="ex_ej")
            c1, c2, c3, c4 = st.columns(4)
            legal = c1.number_input("Reserva Legal (%)", min_value=0.0, max_value=100.0, value=5.0, step=0.5, key="ex_l")
            educ = c2.number_input("Educación (%)", min_value=0.0, max_value=100.0, value=1.0, step=0.5, key="ex_e")
            reinv = c3.number_input("Reinversión (Bs, Asamblea)", min_value=0.0, value=0.0, step=100.0, key="ex_r")
            capit = c4.number_input("Capitalización (Bs)", min_value=0.0, value=0.0, step=100.0, key="ex_c")
            fecha = st.date_input("Fecha del informe", date.today(), format="DD/MM/YYYY", key="ex_f")
            try:
                with con_bd() as con:
                    prop = cierre.calcular_distribucion(con, int(ej), pct_reserva_legal=decimal_de(legal) / 100,
                                                        pct_educacion=decimal_de(educ) / 100,
                                                        reinversion=decimal_de(reinv), capitalizacion=decimal_de(capit))
                st.markdown(f"Excedente {bs(prop.excedente)} − Reserva Legal {bs(prop.reserva_legal)} − Educación "
                            f"{bs(prop.educacion)} − Reinversión {bs(prop.reinversion)} − Capitalización "
                            f"{bs(prop.capitalizacion)} = **Distribuible {bs(prop.distribuible)}** "
                            f"({prop.criterio.lower()}, {len(prop.reparto)} socios)")
                if st.button("Registrar propuesta", type="primary", key="ex_prop"):
                    with con_bd() as con:
                        r = cierre.proponer_distribucion(con, sesion, int(ej), fecha,
                                                         pct_reserva_legal=decimal_de(legal) / 100,
                                                         pct_educacion=decimal_de(educ) / 100,
                                                         reinversion=decimal_de(reinv), capitalizacion=decimal_de(capit))
                    st.success(f"Propuesta N.º {r['id']} registrada; pasa al Consejo de Vigilancia (INF-CV-04).")
                    st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    vigentes = [d for d in distribuciones if d["estado"] != "ANULADA"]
    if not vigentes:
        st.info("No hay distribuciones vigentes.")
        return
    elegida = st.selectbox("Distribución", vigentes, key="ex_sel",
                           format_func=lambda d: f"N.º {d['id']} · ejercicio {d['ejercicio']} · {d['estado'].lower()}")
    with con_bd() as con:
        d = cierre.obtener_distribucion(con, sesion, int(elegida["id"]))
    _flujo_distribucion(sesion, d)
    st.markdown("**Liquidaciones individuales (CI-FIN-02 Art. 8)**")
    st.dataframe(tabla(d["liquidaciones"], [("socio_numero", "N.º"), ("socio", "Socio"), ("bruto", "Bruto"),
                                            ("deducciones", "Deducciones"), ("neto", "Neto"), ("estado", "Estado")],
                       montos=("bruto", "deducciones", "neto")), hide_index=True, use_container_width=True)
    socio = st.selectbox("Liquidación a descargar", d["liquidaciones"], key="ex_liq",
                         format_func=lambda l: f"{l['socio_numero']} · {l['socio']}")
    _pdf(f"Liquidacion_socio_{socio['socio_numero']}", rep.liquidacion_socio(d, socio, _ctx(sesion), **_mek(sesion)), "ex_liq_pdf")
    if d["informe_cv_04"]:
        _pdf("INF-CV-04", rep.inf_cv_04(d, _ctx(sesion), **_mek(sesion)), "ex_cv04")


def _flujo_distribucion(sesion: Sesion, d: dict[str, Any]) -> None:
    hoy = date.today()
    did = int(d["id"])
    if d["estado"] == "PROPUESTA" and sesion.rol == "CV":
        st.markdown("**INF-CV-04 · Informe Previo de Fiscalización**")
        conforme = st.checkbox("La Reserva de Reinversión es conforme al Plan CAPEX", key="cv_conf")
        fecha = st.date_input("Fecha del informe", hoy, format="DD/MM/YYYY", key="cv_f")
        with con_bd() as con:
            verif = cierre.verificaciones_inf_cv_04(con, did, fecha, conforme)
        st.dataframe(pd.DataFrame([{"Verificación": v["verificacion"], "Cumple": "Sí" if v["cumple"] else "No",
                                    "Observación": v["observacion"]} for v in verif]),
                     hide_index=True, use_container_width=True)
        dictamen = st.radio("Dictamen", ["FAVORABLE", "CON_OBSERVACIONES"], horizontal=True, key="cv_dict")
        obs = st.text_area("Observaciones", key="cv_obs")
        if st.button("Emitir INF-CV-04", type="primary", key="cv_emitir"):
            try:
                with con_bd() as con:
                    r = cierre.registrar_inf_cv_04(con, sesion, did, fecha, dictamen=dictamen,
                                                   reinversion_conforme=conforme, observaciones=obs or None)
                st.success(f"{r['codigo']} emitido ({r['dictamen'].lower()}).")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if d["estado"] in ("PROPUESTA", "VERIFICADA_CV") and sesion.rol == "JF":
        motivo = st.text_input("Motivo para anular la propuesta", key="ex_anular_m")
        if st.button("Anular propuesta", key="ex_anular"):
            try:
                with con_bd() as con:
                    cierre.anular_distribucion(con, sesion, did, motivo)
                st.success("Propuesta anulada.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if d["estado"] == "VERIFICADA_CV" and sesion.rol == "JF":
        codigo = d["informe_cv_04"]["codigo"]
        st.markdown(f"**Decisión de la Asamblea** (documento previo: {codigo})")
        c1, c2 = st.columns(2)
        if c1.button("Solicitar autorización de la Asamblea", key="ex_sol_ag"):
            try:
                with con_bd() as con:
                    ctx = seguridad.contexto_desde_bd(con, hoy)
                    aid = seguridad.solicitar_autorizacion(con, sesion, "DISTRIBUCION_EXCEDENTES", d["distribuible"],
                                                           f"Distribución del excedente {d['ejercicio']}", ctx,
                                                           documentos={"INF-CV-04": codigo})
                    if Decimal(d["capitalizacion"]) > 0:
                        seguridad.solicitar_autorizacion(con, sesion, "CAPITALIZACION_EXCEDENTES", d["capitalizacion"],
                                                         f"Capitalización del excedente {d['ejercicio']}", ctx)
                st.success(f"Solicitud N.º {aid} enviada a la Asamblea (firma en «Autorizaciones» con el acta).")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        with con_bd() as con:
            asambleas = seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                        tipo_decision="DISTRIBUCION_EXCEDENTES")
            capitales = seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                        tipo_decision="CAPITALIZACION_EXCEDENTES")
        if asambleas:
            aut = c2.selectbox("Autorización de la Asamblea", asambleas, key="ex_aut",
                               format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")
            cap = None
            if Decimal(d["capitalizacion"]) > 0 and capitales:
                cap = c2.selectbox("Autorización de capitalización", capitales, key="ex_cap",
                                   format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")
            acta = c2.text_input("Acta de la Asamblea", key="ex_acta")
            fecha = c2.date_input("Fecha de la Asamblea", hoy, format="DD/MM/YYYY", key="ex_fa")
            if c2.button("Registrar apropiación y excedente en distribución", type="primary", key="ex_aprobar"):
                try:
                    with con_bd() as con:
                        r = cierre.aprobar_distribucion(con, sesion, did, fecha, autorizacion_asamblea_id=int(aut["id"]),
                                                        acta_ref=acta,
                                                        autorizacion_capitalizacion_id=int(cap["id"]) if cap else None)
                    st.success(f"Asientos {r['asiento_apropiacion']} y {r['asiento_distribucion'] or '—'} registrados.")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if d["estado"] == "APROBADA" and sesion.rol == "GG":
        if st.button("Firmar las liquidaciones individuales", type="primary", key="ex_firmar"):
            try:
                with con_bd() as con:
                    n = cierre.firmar_liquidaciones(con, sesion, did)
                st.success(f"{n} liquidación(es) firmadas.")
                st.rerun()
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if d["estado"] == "APROBADA" and sesion.rol == "JF":
        st.markdown("**Pago del excedente (CI-FIN-02 Art. 9: solo si el saldo posterior ≥ SME)**")
        fecha = st.date_input("Fecha de pago", hoy, format="DD/MM/YYYY", key="ex_fp")
        with con_bd() as con:
            control = cierre.verificar_pago_distribucion(con, did, fecha)
            desembolsos = [a for a in seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA",
                                                                      tipo_decision="DESEMBOLSO")
                           if Decimal(a["monto"]) >= Decimal(d["distribuible"])]
        st.markdown(f"Saldo bancario {bs(control['saldo_actual'])} − pago {bs(control['monto'])} = "
                    f"{bs(control['saldo_posterior'])} frente a SME {bs(control['sme'])}: "
                    f"{chip('BUENO' if control['permitido'] else 'MALO')}", unsafe_allow_html=True)
        for o in control["obligaciones_vencidas"]:
            st.warning(o)
        if not control["en_ventana"]:
            st.caption(f"Fuera del primer ciclo del ejercicio siguiente (hasta {fecha_pantalla(control['fin_ventana'])}).")
        c1, c2 = st.columns(2)
        if c1.button("Solicitar autorización de desembolso (GG + CV)", key="ex_sol_des"):
            try:
                with con_bd() as con:
                    ctx = seguridad.contexto_desde_bd(con, fecha)
                    aid = seguridad.solicitar_autorizacion(con, sesion, "DESEMBOLSO", d["distribuible"],
                                                           f"Pago de excedentes {d['ejercicio']}", ctx)
                st.success(f"Solicitud N.º {aid} enviada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if desembolsos:
            aut = c2.selectbox("Autorización de desembolso", desembolsos, key="ex_des",
                               format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")
            if c2.button("Pagar excedentes", type="primary", key="ex_pagar"):
                try:
                    with con_bd() as con:
                        r = cierre.pagar_distribucion(con, sesion, did, fecha, "1.1.01.03",
                                                      autorizacion_desembolso_id=int(aut["id"]))
                    st.success(f"Pago de {bs(r['monto'])} registrado.")
                    st.rerun()
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# --------------------------------------------------------------------- plazos
def _tab_plazos(sesion: Sesion) -> None:
    with con_bd() as con:
        plazos = cierre.plazos_eeff(con, sesion)
        emitidos = cierre.estados_emitidos(con, sesion)
    if not plazos:
        st.info("Aún no terminó ningún ejercicio: los plazos corren desde el 30 de junio.")
    for p in plazos:
        st.markdown(
            f"**Ejercicio {p['ejercicio']}** (cierre {fecha_pantalla(p['fecha_cierre'])})  \n"
            f"Consejos (60 días): límite {fecha_pantalla(p['limite_consejos'])} "
            f"{chip(ESTADO_PLAZO[p['estado_consejos']])} {p['estado_consejos'].replace('_', ' ').lower()}"
            + (f" · presentado el {fecha_pantalla(p['fecha_consejos'])}" if p["fecha_consejos"] else "") + "  \n"
            f"Asamblea (90 días): límite {fecha_pantalla(p['limite_asamblea'])} "
            f"{chip(ESTADO_PLAZO[p['estado_asamblea']])} {p['estado_asamblea'].replace('_', ' ').lower()}"
            + (f" · aprobado el {fecha_pantalla(p['fecha_asamblea'])}" if p["fecha_asamblea"] else ""),
            unsafe_allow_html=True)
    pendientes_c = [p for p in plazos if p["cierre_anual"] and not p["fecha_consejos"]]
    if sesion.rol == "GG" and pendientes_c:
        with st.form("form_presentacion"):
            st.markdown("**Presentación de los EEFF al CA y al CV (PR-FIN-11)**")
            p = st.selectbox("Ejercicio", pendientes_c, format_func=lambda x: str(x["ejercicio"]))
            fecha = st.date_input("Fecha de presentación", date.today(), format="DD/MM/YYYY")
            acta = st.text_input("Acta del Consejo de Administración")
            hechos = st.text_area("Hechos posteriores (Nota 16)")
            if st.form_submit_button("Registrar presentación y emitir el paquete", type="primary"):
                try:
                    with con_bd() as con:
                        r = cierre.registrar_presentacion_consejos(con, sesion, int(p["ejercicio"]), fecha, acta,
                                                                   hechos or None)
                    st.success(f"Presentado {'dentro' if r['dentro_plazo'] else 'FUERA'} del plazo; huella "
                               f"{r['huella'][:16]}…")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    pendientes_a = [p for p in plazos if p["fecha_consejos"] and not p["fecha_asamblea"]]
    if sesion.rol == "AG" and pendientes_a:
        with st.form("form_asamblea"):
            st.markdown("**Aprobación de los EEFF por la Asamblea (Ley 356 Art. 55)**")
            p = st.selectbox("Ejercicio", pendientes_a, format_func=lambda x: str(x["ejercicio"]))
            fecha = st.date_input("Fecha de la Asamblea", date.today(), format="DD/MM/YYYY")
            acta = st.text_input("Acta de la Asamblea")
            informe = st.text_input("Referencia del INF-CV-08 (Consejo de Vigilancia)")
            if st.form_submit_button("Registrar aprobación", type="primary"):
                try:
                    with con_bd() as con:
                        r = cierre.registrar_aprobacion_asamblea(con, sesion, int(p["ejercicio"]), fecha, acta, informe)
                    st.success(f"Aprobación registrada {'dentro' if r['dentro_plazo'] else 'FUERA'} del plazo.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for e in emitidos:
        e["ejercicio_txt"] = str(e["ejercicio"])
    if emitidos:
        st.markdown("**Paquetes de estados emitidos (congelados con huella SHA-256)**")
        st.dataframe(tabla(emitidos, [("id", "N.º"), ("ejercicio_txt", "Ejercicio"), ("motivo", "Motivo"),
                                      ("emitido_por", "Emitido por"), ("emitido_en", "Fecha"), ("huella", "Huella")],
                           fechas=("emitido_en",)), hide_index=True, use_container_width=True)
        elegido = st.selectbox("Paquete", emitidos, key="pl_paq", format_func=lambda e: f"N.º {e['id']} · {e['ejercicio']}")
        with con_bd() as con:
            emitido = cierre.obtener_estado_emitido(con, sesion, int(elegido["id"]))
        st.caption("Integridad verificada." if emitido["integro"] else "¡La huella no coincide con el contenido!")
        _pdf(f"EEFF_emitido_{elegido['id']}", rep.paquete_pdf(emitido["paquete"], emitido["huella"], *_me(sesion)), "pl_pdf")


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 8 — Contabilidad general y estados financieros."""
    st.header("Contabilidad general y estados financieros · Ciclo 5")
    _alertas(sesion)
    pestañas = st.tabs(["Cierre mensual", "Costeo e indicadores", "Estados financieros", "Notas 1-16",
                        "Cierre anual", "Excedentes", "Plazos 60/90 días"])
    funciones = (_tab_cierre_mensual, _tab_indicadores, _tab_estados, _tab_notas, _tab_cierre_anual,
                 _tab_excedentes, _tab_plazos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
