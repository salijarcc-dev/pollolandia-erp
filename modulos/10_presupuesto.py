"""Módulo 10 — Presupuesto (Fase 12).

Pestañas: Presupuesto Maestro 2026-2030, Presupuesto por ciclo (FIN-TES-09 y
FIN-TES-10), Calendario presupuestario, Ejecución y variaciones (FIN-TES-12),
Modificaciones (FIN-TES-11), POA y Alimento propio frente a comprado (D.6).

Solo llama a ``servicios/`` y a ``reportes/``. La Unidad de Finanzas elabora
y registra; la Gerencia General solicita modificaciones y presenta informes;
el Consejo de Administración y la Asamblea resuelven según CI-FIN-09; el
Consejo de Vigilancia verifica.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_presupuesto as rep
from reportes.formatos_tesoreria import Contexto
from servicios import poa, presupuesto, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
ESTADO_HITO = {"CUMPLIDO": "VERDE", "CUMPLIDO_FUERA_DE_PLAZO": "AMARILLO", "VENCIDO": "ROJO", "POR_VENCER": "AMARILLO",
               "PROGRAMADO": "VERDE"}


def _ctx(sesion: Sesion, detalle: str = "") -> Contexto:
    return Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                    cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo=detalle)


def _pdf(nombre: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {nombre} (PDF)", datos, file_name=f"{nombre}.pdf", mime="application/pdf", key=clave)


def _xlsx(nombre: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {nombre} (Excel)", datos, file_name=f"{nombre}.xlsx", key=clave,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _puede(sesion: Sesion, recurso: str, accion: str) -> bool:
    with con_bd() as con:
        return seguridad.tiene_permiso(con, sesion.rol, recurso, accion)


def _ejercicio(clave: str, texto: str = "Ejercicio (julio a junio)") -> int | None:
    with con_bd() as con:
        lista = presupuesto.ejercicios(con)
    if not lista:
        st.info("No hay ejercicios registrados.")
        return None
    hoy = date.today()
    actual = hoy.year if hoy.month >= 7 else hoy.year - 1
    return st.selectbox(texto, lista, index=lista.index(actual) if actual in lista else 0, key=clave,
                        format_func=lambda e: f"{e}-{(e + 1) % 100:02d}")


def _valor(v: Any, unidad: str | None = None) -> str:
    if v is None:
        return "—"
    if unidad == "%":
        return rep.pct(v)
    if isinstance(v, Decimal) and unidad not in (None, "Bs"):
        return rep.num(v, 2 if v != v.to_integral_value() else 0)
    return bs(v)


def _valor_maestro(modulo: str, fila: dict[str, Any], v: Any) -> str:
    """Formato de la celda: porcentaje, cantidad física o monto en Bs."""
    if v is None:
        return "—"
    if fila["unidad"] == "%":
        return rep.pct(v)
    if modulo == "PRODUCCION" or fila["unidad"] in ("kg/ave", "Bs/qq") or fila["clave"] == "CONSUMO_QQ":
        return rep.num(v, 2 if v != v.to_integral_value() else 0)
    return bs(v)


def _alertas(sesion: Sesion) -> None:
    try:
        with con_bd() as con:
            alertas = presupuesto.alertas_presupuesto(con, sesion)
    except ErrorPollolandia:
        return
    for a in alertas[:8]:
        (st.error if a["nivel"] == "ROJO" else st.warning)(a["mensaje"])


# ==========================================================================
# 1. PRESUPUESTO MAESTRO
# ==========================================================================
def _tab_maestro(sesion: Sesion) -> None:
    modulo = st.selectbox("Módulo del Plan Maestro", list(presupuesto.MODULOS_MAESTRO),
                          format_func=lambda m: presupuesto.MODULOS_MAESTRO[m], key="pm_modulo")
    with con_bd() as con:
        serie = presupuesto.serie_maestro(con, sesion, modulo)
    anios = ["2026", "2027", "2028", "2029", "2030"]
    datos = [{"Concepto": ("▸ " if f["es_total"] else "   ") + f["concepto"], "Unidad": f["unidad"] or "",
              **{a: _valor_maestro(modulo, f, f.get(a)) for a in anios}} for f in serie]
    st.dataframe(pd.DataFrame(datos), hide_index=True, use_container_width=True)
    if modulo in ("RESULTADOS", "FLUJO", "VENTAS"):
        clave = {"RESULTADOS": "EXCEDENTE_NETO", "FLUJO": "SALDO_FINAL", "VENTAS": "TOTAL_INGRESOS"}[modulo]
        fila = next((f for f in serie if f["clave"] == clave), None)
        if fila:
            st.bar_chart(pd.DataFrame({"Ejercicio": anios, fila["concepto"]: [float(fila[a]) for a in anios]})
                         .set_index("Ejercicio"))
    if st.button("Preparar Excel del Presupuesto Maestro", key="pm_excel"):
        with con_bd() as con:
            series = {m: presupuesto.serie_maestro(con, sesion, m) for m in presupuesto.MODULOS_MAESTRO}
        _xlsx("Presupuesto_Maestro_2026-2030", rep.excel_maestro(series, presupuesto.MODULOS_MAESTRO), "pm_xlsx")
    st.caption("Fuente: Manual del Sistema Presupuestario (SGC-MAN-ADM-001). Los ingresos se presupuestan sobre aves "
               "ingresadas (7.800 kg por ciclo), como en el Excel; el Comité debe decidir el ajuste por mortalidad.")


# ==========================================================================
# 2. PRESUPUESTO POR CICLO (FIN-TES-09 / FIN-TES-10)
# ==========================================================================
def _tab_ciclo(sesion: Sesion) -> None:
    ejercicio = _ejercicio("pc_ej")
    if ejercicio is None:
        return
    ciclo = st.selectbox("Ciclo", [1, 2, 3, 4, 5], key="pc_ciclo")
    try:
        with con_bd() as con:
            datos = presupuesto.presupuesto_ciclo(con, sesion, ejercicio, int(ciclo))
            supuestos = presupuesto.hoja_supuestos(con, sesion, ejercicio)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    st.markdown(f"**FIN-TES-10 · Ciclo {ciclo} del ejercicio {ejercicio}** — {fecha_pantalla(datos['desde'])} al "
                f"{fecha_pantalla(datos['hasta'])}")
    filas_ciclo = [{**f, "cantidad_txt": rep.num(f["cantidad"]) if f.get("cantidad") is not None else "",
                    "precio_txt": rep.num(f["precio"]) if f.get("precio") not in (None, "") else ""}
                   for f in datos["filas"]]
    st.dataframe(tabla(filas_ciclo, [("grupo", "Grupo"), ("nombre", "Elemento"), ("cantidad_txt", "Cantidad"),
                                     ("precio_txt", "Precio unitario"), ("monto", "Presupuesto del ciclo"),
                                        ("acumulado", "Acumulado del año"), ("ajuste", "Modificaciones")],
                       montos=("monto", "acumulado", "ajuste"), ocultar_ceros=True),
                 hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    c1.metric("Saldo mínimo de efectivo (SME)", bs(datos["sme"]))
    c2.metric("Saldo final proyectado", bs(datos["saldo_final_proyectado"]) if datos["saldo_final_proyectado"]
              else "—")
    _pdf(f"FIN-TES-10_{ejercicio}_C{ciclo}", rep.fin_tes_10(datos, _ctx(sesion, f"Ciclo {ciclo}")), "pdf_t10")
    st.subheader("FIN-TES-09 · Hoja de supuestos")
    st.dataframe(pd.DataFrame([{"Parámetro": f["parametro"], "Unidad": f["unidad"],
                                "Base 2026": "—" if f["base"] is None else str(f["base"]).replace(".", ","),
                                f"Ejercicio {ejercicio}": "—" if f["valor"] is None else str(f["valor"]).replace(".", ","),
                                "Fuente": f["fuente"]} for f in supuestos]), hide_index=True, use_container_width=True)
    _pdf(f"FIN-TES-09_{ejercicio}", rep.fin_tes_09(ejercicio, supuestos, _ctx(sesion)), "pdf_t09")


# ==========================================================================
# 3. CALENDARIO PRESUPUESTARIO
# ==========================================================================
def _tab_calendario(sesion: Sesion) -> None:
    ejercicio = _ejercicio("cal_ej", "Presupuesto del ejercicio")
    if ejercicio is None:
        return
    with con_bd() as con:
        cal = presupuesto.calendario_presupuestario(con, sesion, ejercicio)
    if cal["nota"]:
        st.info(cal["nota"])
    for h in cal["hitos"]:
        hecho = f" · cumplido el {fecha_pantalla(h['fecha'])} ({h['documento_ref']})" if h["fecha"] else ""
        st.markdown(f"{chip(ESTADO_HITO[h['estado']])} **{h['actividad']}. {h['descripcion']}** — {h['responsable']} · "
                    f"límite {fecha_pantalla(h['limite'])} · {h['documento']}{hecho}", unsafe_allow_html=True)
    pendientes = [h for h in cal["hitos"] if not h["fecha"]]
    if pendientes and not cal["transitoria"]:
        siguiente = pendientes[0]
        if siguiente["responsable"] == sesion.rol:
            with st.form("form_hito"):
                st.markdown(f"**Registrar: {siguiente['descripcion']}**")
                fecha = st.date_input("Fecha", date.today(), format="DD/MM/YYYY")
                documento = st.text_input(f"Referencia ({siguiente['documento']})")
                if st.form_submit_button("Registrar hito", type="primary"):
                    try:
                        with con_bd() as con:
                            r = presupuesto.registrar_hito(con, sesion, ejercicio, siguiente["actividad"], fecha,
                                                           documento)
                        (st.success if r["en_plazo"] else st.warning)(
                            "Hito registrado " + ("en plazo." if r["en_plazo"] else "FUERA de plazo."))
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        else:
            st.caption(f"Siguiente hito a cargo de {siguiente['responsable']}.")


# ==========================================================================
# 4. EJECUCIÓN Y VARIACIONES (FIN-TES-12)
# ==========================================================================
def _tab_ejecucion(sesion: Sesion) -> None:
    ejercicio = _ejercicio("ej_ej")
    if ejercicio is None:
        return
    c1, c2, c3 = st.columns(3)
    tipo = c1.selectbox("Informe", ["CICLO", "SEMESTRE", "ANUAL"], key="ej_tipo",
                        format_func=lambda t: {"CICLO": "Por ciclo", "SEMESTRE": "Semestral", "ANUAL": "Anual"}[t])
    numero = 0
    if tipo == "CICLO":
        numero = int(c2.selectbox("Ciclo", [1, 2, 3, 4, 5], key="ej_ciclo"))
    elif tipo == "SEMESTRE":
        numero = int(c2.selectbox("Semestre", [1, 2], key="ej_sem", format_func=lambda x: "Julio-diciembre" if x == 1
                                  else "Enero-junio"))
    kg = c3.number_input("Kg vendidos reales (opcional, para precio y volumen)", min_value=0.0, step=10.0,
                         key="ej_kg")
    cantidades = {"ING_CARNE": decimal_de(kg)} if kg else None
    try:
        with con_bd() as con:
            calc = presupuesto.calcular_ejecucion(con, sesion, ejercicio, tipo, numero, cantidades)
            informes = presupuesto.listar_informes(con, sesion, ejercicio)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    st.markdown(f"Período {fecha_pantalla(calc['desde'])} al {fecha_pantalla(calc['hasta'])} · semáforo general "
                f"{chip(calc['semaforo'])} · verdes {calc['verdes']}, amarillos {calc['amarillos']}, rojos "
                f"{calc['rojos']}", unsafe_allow_html=True)
    filas = [{**f, "pct_txt": rep.pct(f["pct"])} for f in calc["filas"]]
    if calc["flujo"]:
        fl = calc["flujo"]
        filas.append({"nombre": "Saldo final de efectivo", "presupuesto": fl["presupuesto"], "real": fl["real"],
                      "variacion": fl["variacion"], "pct_txt": rep.pct(fl["pct"]), "semaforo": fl["semaforo"],
                      "variacion_precio": None, "variacion_cantidad": None})
    st.dataframe(tabla(filas, [("nombre", "Partida"), ("presupuesto", "Presupuesto"), ("real", "Real"),
                               ("variacion", "Variación"), ("pct_txt", "Variación %"), ("semaforo", "Semáforo"),
                               ("variacion_precio", "Var. precio"), ("variacion_cantidad", "Var. cantidad")],
                       montos=("presupuesto", "real", "variacion", "variacion_precio", "variacion_cantidad")),
                 hide_index=True, use_container_width=True)
    u = calc["costo_unitario"]
    st.caption(f"Costo unitario Bs/kg: presupuesto {rep.num(u.get('presupuesto'))}, real {rep.num(u.get('real'))} "
               "(el real requiere los kg vendidos).")
    _pdf(f"FIN-TES-12_{ejercicio}_{tipo}_{numero}", rep.fin_tes_12(calc, _ctx(sesion)), "pdf_t12_prev")
    _xlsx(f"FIN-TES-12_{ejercicio}_{tipo}_{numero}", rep.excel_fin_tes_12(calc), "xlsx_t12")
    ya = next((i for i in informes if i["tipo"] == tipo and i["numero"] == calc["numero"]), None)
    if ya:
        st.success(f"FIN-TES-12 registrado el {fecha_pantalla(ya['fecha'])}"
                   + (" (fuera de plazo)" if ya["fuera_de_plazo"] else "")
                   + (f"; presentado el {fecha_pantalla(ya['presentado_en'])}" if ya["presentado_en"] else ""))
        if not ya["presentado_en"] and _puede(sesion, "presupuesto", "AUTORIZAR"):
            if st.button("Presentar al Consejo de Administración", key="btn_presentar"):
                try:
                    with con_bd() as con:
                        presupuesto.presentar_informe(con, sesion, int(ya["id"]), date.today())
                    st.success("Informe presentado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    elif _puede(sesion, "presupuesto", "CREAR"):
        observadas = [f for f in filas if f["semaforo"] != "VERDE"]
        with st.form("form_t12"):
            st.markdown("**Registrar el FIN-TES-12** (rojo: causa, responsable y acción; amarillo: explicación)")
            acciones: dict[str, dict[str, Any]] = {}
            for f in observadas:
                clave = f.get("partida", "FLUJO")
                st.markdown(f"{chip(f['semaforo'])} {f['nombre']}", unsafe_allow_html=True)
                causa = st.text_input("Causa", key=f"causa_{clave}")
                responsable = accion = ""
                if f["semaforo"] == "ROJO":
                    responsable = st.text_input("Responsable", key=f"resp_{clave}")
                    accion = st.text_input("Acción correctiva", key=f"acc_{clave}")
                acciones[clave] = {"causa": causa, "responsable": responsable, "accion": accion}
            fecha = st.date_input("Fecha del informe", date.today(), format="DD/MM/YYYY")
            if st.form_submit_button("Registrar informe", type="primary"):
                try:
                    with con_bd() as con:
                        r = presupuesto.registrar_informe_ejecucion(con, sesion, ejercicio, tipo, numero, fecha,
                                                                    acciones, cantidades)
                    st.success(f"FIN-TES-12 registrado (límite {fecha_pantalla(r['limite'])}).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 5. MODIFICACIONES (FIN-TES-11)
# ==========================================================================
def _tab_modificaciones(sesion: Sesion) -> None:
    ejercicio = _ejercicio("mod_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        mods = presupuesto.listar_modificaciones(con, sesion, ejercicio)
    st.dataframe(tabla([{**m, "pct": rep.pct(m["variacion_pct"])} for m in mods],
                       [("id", "N.º"), ("concepto", "Partida"), ("ciclo", "Ciclo"), ("aprobado", "Aprobado"),
                        ("solicitado", "Solicitado"), ("pct", "Variación"), ("autoridad", "Autoridad"),
                        ("estado", "Estado"), ("verificado_por", "Verificó"), ("resuelto_por", "Resolvió")],
                       montos=("aprobado", "solicitado")), hide_index=True, use_container_width=True)
    for m in mods:
        _pdf(f"FIN-TES-11_{m['id']}", rep.fin_tes_11(m, _ctx(sesion)), f"pdf_t11_{m['id']}")
    if _puede(sesion, "presupuesto", "AUTORIZAR"):
        with st.form("form_t11"):
            st.markdown("**Solicitar modificación (Gerencia General)** — la autoridad se fija según CI-FIN-09")
            c1, c2, c3 = st.columns(3)
            partida = c1.selectbox("Partida", list(presupuesto.PARTIDAS),
                                   format_func=lambda p: presupuesto.PARTIDAS[p]["nombre"])
            ciclo = c2.selectbox("Ciclo", [1, 2, 3, 4, 5])
            monto = c3.number_input("Monto solicitado (Bs)", min_value=0.0, step=100.0, format="%.2f")
            motivo = st.text_area("Motivo y desviación que la origina (FIN-TES-12)")
            fuente = st.text_input("Fuente de recursos", "Recursos propios")
            efecto = st.text_input("Efecto en el flujo de caja y en el SME")
            if st.form_submit_button("Solicitar", type="primary"):
                try:
                    with con_bd() as con:
                        r = presupuesto.solicitar_modificacion(con, sesion, ejercicio, int(ciclo), partida,
                                                               decimal_de(monto), motivo=motivo, fuente_recursos=fuente,
                                                               efecto_flujo=efecto, fecha=date.today())
                    st.success(f"Solicitud N.º {r['id']}: resuelve {r['autoridad']}. " + "; ".join(r["fundamentos"]))
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    pendientes = [m for m in mods if m["estado"] == "SOLICITADA"]
    if sesion.rol == "JF":
        por_verificar = [m for m in pendientes if not m["verificado_por"]]
        if por_verificar:
            with st.form("form_verif_t11"):
                m = st.selectbox("Solicitud por verificar", por_verificar, format_func=lambda x: f"N.º {x['id']} — "
                                 f"{x['concepto']} ciclo {x['ciclo']}")
                if st.form_submit_button("Verificar (Unidad de Finanzas)"):
                    try:
                        with con_bd() as con:
                            presupuesto.verificar_modificacion(con, sesion, int(m["id"]))
                        st.success("Solicitud verificada.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    mias = [m for m in pendientes if m["autoridad"] == sesion.rol and m["verificado_por"]]
    if mias:
        with st.form("form_resolver_t11"):
            m = st.selectbox("Solicitud a resolver", mias, format_func=lambda x: f"N.º {x['id']} — {x['concepto']} "
                             f"({bs(x['aprobado'])} → {bs(x['solicitado'])})")
            acta = st.text_input("Acta (obligatoria para CA y Asamblea)")
            observacion = st.text_input("Observación (obligatoria si se rechaza)")
            c1, c2 = st.columns(2)
            aprobar = c1.form_submit_button("Aprobar", type="primary")
            rechazar = c2.form_submit_button("Rechazar")
            if aprobar or rechazar:
                try:
                    with con_bd() as con:
                        estado = presupuesto.resolver_modificacion(con, sesion, int(m["id"]), aprobar, date.today(),
                                                                   acta or None, observacion or None)
                    st.success(f"Solicitud {estado.lower()}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 6. POA
# ==========================================================================
def _tab_poa(sesion: Sesion) -> None:
    ejercicio = _ejercicio("poa_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        actividades = presupuesto.listar_poa(con, sesion, ejercicio)
        cumplimiento = presupuesto.cumplimiento_poa(con, sesion, ejercicio)
    if not actividades:
        st.info("No hay actividades del POA cargadas para este ejercicio.")
        return
    c = st.columns(5)
    c[0].markdown(f"**Acumulado**<br>{rep.pct(cumplimiento['cumplimiento'])} "
                  f"{chip(cumplimiento['semaforo']) if cumplimiento['semaforo'] else ''}", unsafe_allow_html=True)
    for i, t in enumerate(cumplimiento["trimestres"], start=1):
        c[i].markdown(f"**T{t['trimestre']}** ({fecha_pantalla(t['desde'])[:5]}–{fecha_pantalla(t['hasta'])[:5]})<br>"
                      f"{t['cumplidas']}/{t['programadas']} · {rep.pct(t['cumplimiento'])} "
                      f"{chip(t['semaforo']) if t['semaforo'] else ''}", unsafe_allow_html=True)
    for a in cumplimiento["areas"]:
        if a["plan_recuperacion"]:
            st.error(f"{a['area_txt']}: cumplimiento {rep.pct(a['cumplimiento'])} — requiere plan de recuperación "
                     "aprobado por el CA (POA §5.1).")
    filas = []
    for a in actividades:
        fila = {"codigo": a["codigo"], "area": a["area_txt"], "descripcion": a["descripcion"],
                "indicador": a["indicador"], "responsable": a["responsable"]}
        for t in range(1, 5):
            av = a["avances"].get(t)
            fila[f"T{t}"] = ("—" if t not in a["programados"] else
                             (presupuesto.ESTADOS_AVANCE[av["estado"]] + ("" if av["en_plazo"] or av["estado"] != "CUMPLIDA"
                                                                         else " (tarde)") if av else "Programada"))
        filas.append(fila)
    st.dataframe(pd.DataFrame(filas).rename(columns={"codigo": "N.º", "area": "Área", "descripcion": "Actividad",
                                                     "indicador": "Meta física", "responsable": "Responsable"}),
                 hide_index=True, use_container_width=True)
    if _puede(sesion, "presupuesto", "CREAR"):
        with st.form("form_avance"):
            st.markdown("**Registrar avance trimestral**")
            act = st.selectbox("Actividad", actividades, format_func=lambda x: f"{x['codigo']} — {x['descripcion'][:80]}")
            c1, c2, c3 = st.columns(3)
            trimestre = int(c1.selectbox("Trimestre del ejercicio", [1, 2, 3, 4]))
            estado = c2.selectbox("Estado", list(presupuesto.ESTADOS_AVANCE),
                                  format_func=lambda x: presupuesto.ESTADOS_AVANCE[x])
            fecha = c3.date_input("Fecha", date.today(), format="DD/MM/YYYY")
            evidencia = st.text_input("Evidencia (formato o documento)")
            if st.form_submit_button("Registrar avance", type="primary"):
                try:
                    with con_bd() as con:
                        r = presupuesto.registrar_avance(con, sesion, int(act["id"]), trimestre, fecha, estado,
                                                         evidencia or None)
                    st.success("Avance registrado" + (" en plazo." if r["en_plazo"] else "."))
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 7. D.6 ALIMENTO PROPIO VS COMPRADO
# ==========================================================================
def _tab_d6(sesion: Sesion) -> None:
    ejercicio = _ejercicio("d6_ej")
    if ejercicio is None:
        return
    with con_bd() as con:
        d6 = presupuesto.alimento_propio_vs_comprado(con, sesion, ejercicio)
        serie = presupuesto.serie_maestro(con, sesion, "D6")
    if not d6["aplica"]:
        st.info("En este ejercicio el alimento se compra a terceros: la planta propia opera desde 2027.")
    else:
        filas = []
        for clave, concepto in d6["conceptos"].items():
            unidad = "%" if clave == "AHORRO_PCT" else ("qq" if clave == "CONSUMO_QQ" else None)
            filas.append({"Concepto": concepto, "Presupuesto": _valor(d6["presupuesto"].get(clave), unidad),
                          "Real": _valor(d6["real"].get(clave), unidad)})
        st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
        st.caption(f"Precio de compra de referencia: {bs(d6['precio_compra'])} por qq (Manual D.6, sin indexar).")
    anios = ["2027", "2028", "2029", "2030"]
    datos = {f["clave"]: f for f in serie}
    st.bar_chart(pd.DataFrame({"Ejercicio": anios,
                               "Ahorro por consumo propio": [float(datos["AHORRO"][a]) for a in anios],
                               "Margen del excedente": [float(datos["MARGEN_EXCEDENTE"][a]) for a in anios]})
                 .set_index("Ejercicio"))


def _tab_flujo_poa(sesion: Sesion) -> None:
    """Datos del POA 2026-2027: calendario C1-C5 y flujo mensual frente a lo real."""
    with con_bd() as con:
        cargado = poa.carga_vigente(con) is not None
    if not cargado:
        st.info("Los datos del POA (calendario de ciclos, flujo mensual y presupuesto por área) aún no se cargaron.")
        if _puede(sesion, "presupuesto", "CREAR") and st.button("Cargar datos del POA 2026-2027", type="primary",
                                                                key="poa_cargar"):
            try:
                with con_bd() as con:
                    r = poa.cargar_datos_poa(con, sesion)
                st.success(f"{r['version']} cargado: {r['ciclos']} ciclos, {r['registros_flujo']} registros de flujo.")
                for o in r["observaciones"]:
                    st.caption(f"Observación: {o}")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        return
    with con_bd() as con:
        cal = poa.calendario_vs_real(con, sesion)
        flujo = poa.flujo_poa_vs_real(con, sesion)
        area = poa.presupuesto_area(con, sesion)
    st.subheader("Calendario de ciclos (POA §1.1) frente a Producción")
    st.dataframe(tabla([{**c, "real": c["estado_real"].replace("_", " ").lower(),
                         "desfase": "" if c["desfase_dias"] is None else f"{c['desfase_dias']:+d} días"} for c in cal],
                       [("ciclo", "Ciclo"), ("inicio_poa", "Inicio POA"), ("cierre_poa", "Cierre POA"),
                        ("tramo", "Tramo"), ("ingreso_poa", "Ingreso POA"), ("inicio_real", "Inicio real"),
                        ("real", "Estado"), ("desfase", "Desfase")], montos=("ingreso_poa",),
                       fechas=("inicio_poa", "cierre_poa", "inicio_real")), hide_index=True, use_container_width=True)
    st.subheader("Flujo de caja mensual (POA §3.2) frente a lo real")
    st.dataframe(tabla(flujo, [("mes", "Mes"), ("cobros_poa", "Cobros POA"), ("cobros_real", "Cobros reales"),
                               ("pagos_poa", "Pagos POA"), ("pagos_real", "Pagos reales"), ("saldo_poa", "Saldo POA"),
                               ("saldo_real", "Saldo real"), ("desvio", "Desvío")],
                       montos=("cobros_poa", "cobros_real", "pagos_poa", "pagos_real", "saldo_poa", "saldo_real",
                               "desvio")), hide_index=True, use_container_width=True)
    st.line_chart(pd.DataFrame({"Saldo POA": [float(f["saldo_poa"]) for f in flujo],
                                "Saldo real": [float(f["saldo_real"]) if f["saldo_real"] is not None else None
                                               for f in flujo]}, index=[f["mes"] for f in flujo]))
    with st.expander("Presupuesto por objeto de gasto y área (POA §3.1)"):
        st.dataframe(tabla(area, [("grupo", "Grupo"), ("objeto", "Objeto de gasto"), ("area", "Área"),
                                  ("ejercicio", "Ejercicio 2026-27"), ("previo", "Previo al POA"),
                                  ("poa", "Asignado POA")], montos=("ejercicio", "previo", "poa")),
                     hide_index=True, use_container_width=True)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 10 — Presupuesto."""
    st.header("Presupuesto Maestro, ejecución y POA")
    _alertas(sesion)
    pestañas = st.tabs(["Presupuesto Maestro", "Por ciclo (FIN-TES-10)", "Calendario", "Ejecución (FIN-TES-12)",
                        "Modificaciones (FIN-TES-11)", "POA", "Flujo POA vs real", "Alimento propio (D.6)"])
    funciones = (_tab_maestro, _tab_ciclo, _tab_calendario, _tab_ejecucion, _tab_modificaciones, _tab_poa,
                 _tab_flujo_poa, _tab_d6)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            try:
                funcion(sesion)
            except ErrorPollolandia as exc:
                mostrar_error(exc)
