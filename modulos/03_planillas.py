"""Módulo 3 — Personal y planillas (Fase 5, Ciclo 2). Pantalla con pestañas:
Personal, Planilla mensual, Hojas de tiempo, Personal eventual, Liquidación
final, Alertas y Formatos.

Solo llama a ``servicios/`` y a ``reportes/``. Finanzas elabora y registra; la
Gerencia General autoriza; la Unidad Técnica emite la circular y las hojas de
tiempo; el Consejo de Vigilancia fiscaliza y verifica horas (CI-FIN-14).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import streamlit as st

from modulos.comun import bs, chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_planillas as pdf
from servicios import planillas, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla

NOMBRE_BANCO = {"1.1.01.03": "1.1.01.03 — Banco cuenta corriente", "1.1.01.04": "1.1.01.04 — Banco caja de ahorros"}
CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
         "Noviembre", "Diciembre"]


SEMAFORO = {"BUENO": "Bueno", "NORMAL": "Normal", "MALO": "Malo"}


def _miles(valor) -> str:
    """Entero con separador de miles boliviano: 3.000."""
    return "" if valor is None else f"{int(valor):,}".replace(",", ".")


def _pct_txt(valor) -> str:
    return "" if valor is None else f"{Decimal(str(valor)):.2f} %".replace(".", ",")


def _periodos(filas: list[dict]) -> list[dict]:
    """Agrega la columna «periodo» (Septiembre 2026) para mostrar mes y año."""
    for f in filas:
        f["periodo"] = f"{MESES[int(f['mes']) - 1]} {f['anio']}"
    return filas


def _puede(con, sesion: Sesion, accion: str) -> bool:
    return seguridad.tiene_permiso(con, sesion.rol, "planillas", accion)


def _autorizacion_desembolso(con, sesion: Sesion, monto: Decimal, fecha: date, clave: str) -> int | None:
    requisito = seguridad.requiere_autorizacion("DESEMBOLSO", monto, seguridad.contexto_desde_bd(con, fecha))
    if not requisito.requiere_firma:
        return None
    firmas = ", ".join(seguridad.NOMBRE_CALIDAD[c] for c in requisito.firmas)
    disponibles = [a for a in seguridad.listar_autorizaciones(con, sesion, estado="AUTORIZADA", tipo_decision="DESEMBOLSO")
                   if a["monto"] >= monto]
    if not disponibles:
        st.warning(f"El pago de {bs(monto)} requiere autorización de {firmas}: solicítela en «Autorizaciones».")
        return None
    return int(st.selectbox(f"Autorización DESEMBOLSO ({firmas})", disponibles, key=clave,
                            format_func=lambda a: f"N.º {a['id']} · {bs(a['monto'])}")["id"])


# -------------------------------------------------------------------- resumen
def _resumen(sesion: Sesion) -> None:
    with con_bd() as con:
        r = planillas.resumen_planillas(con, sesion, date.today())
    c1, c2, c3, c4 = st.columns([1, 1.5, 1.5, 1])
    c1.metric("Personal activo", r["personal_activo"])
    c2.metric("Provisión de indemnización", bs(r["provision_indemnizacion"]))
    c3.metric("Aguinaldo provisionado", bs(r["provision_aguinaldo"]))
    c4.metric("Pagos vencidos", r["vencidas"])
    if r["vencidas"]:
        st.error("Hay obligaciones laborales vencidas: revise «Alertas» (riesgo 38, multas).")
    elif r["por_vencer"]:
        st.warning(f"{r['por_vencer']} pago(s) legal(es) por vencer en los próximos días.")


# -------------------------------------------------------------------- personal
def _tab_personal(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        lista = planillas.listar_personal(con, sesion, hoy)
        edita = _puede(con, sesion, "EDITAR")
        p = seguridad.contexto_desde_bd(con, hoy).parametros
    for t in lista:
        t["area_txt"] = planillas.AREAS[t["area"]].split(" (")[0] + (" · polivalente" if t["polivalente"] else "")
    st.dataframe(tabla(lista, [("nombre", "Trabajador"), ("ci", "C.I."), ("cargo", "Cargo"), ("area_txt", "Área"),
                               ("salario", "Salario mensual"), ("fecha_ingreso", "Ingreso"), ("contrato_ref", "Contrato"),
                               ("estado", "Estado"), ("fecha_retiro", "Retiro")], montos=("salario",),
                       fechas=("fecha_ingreso", "fecha_retiro")), hide_index=True, use_container_width=True)
    with con_bd() as con:
        c = planillas.costo_mod_ciclo(con, Decimal(p["salario_encargado_granja_mensual"]), hoy)
    st.caption(f"Costo de mano de obra fija por ciclo del Encargado de Granja (Manual de Costos §6.2): salario "
               f"{bs(c['salario'])} + aguinaldo {bs(c['aguinaldo'])} + patronal {bs(c['aporte_patronal'])} + "
               f"indemnización {bs(c['indemnizacion'])} = {bs(c['total'])}.")
    if not edita:
        return
    with st.expander("Registrar trabajador con contrato"):
        with st.form("form_personal", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            nombre = c1.text_input("Nombre completo")
            ci = c2.text_input("C.I.")
            cargo = c3.text_input("Cargo")
            area = c1.selectbox("Área", list(planillas.AREAS), format_func=planillas.AREAS.get)
            polivalente = c2.checkbox("Polivalente (carne y alimento)")
            salario = c3.number_input("Salario mensual (Bs)", min_value=0.0, step=50.0, format="%.2f")
            ingreso = c1.date_input("Fecha de ingreso", hoy, format="DD/MM/YYYY")
            contrato = c2.selectbox("Tipo de contrato", list(planillas.CONTRATOS))
            ref = c3.text_input("Referencia del contrato")
            nua = c1.text_input("NUA (Gestora Pública)")
            cns = c2.text_input("Matrícula CNS")
            if st.form_submit_button("Registrar", type="primary"):
                try:
                    with con_bd() as con:
                        planillas.registrar_personal(con, sesion, nombre, ci, cargo, area, decimal_de(salario), ingreso,
                                                     contrato, ref, polivalente=polivalente, nua_gestora=nua or None,
                                                     matricula_cns=cns or None)
                    st.success("Trabajador registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    activos = [t for t in lista if t["estado"] == "ACTIVO"]
    if activos:
        c1, c2 = st.columns(2)
        with c1.form("form_salario"):
            st.markdown("**Nuevo salario (con vigencia)**")
            t = st.selectbox("Trabajador", activos, format_func=lambda x: x["nombre"], key="sal_t")
            desde = st.date_input("Vigente desde", hoy, format="DD/MM/YYYY", key="sal_d")
            monto = st.number_input("Salario mensual (Bs)", min_value=0.0, step=50.0, format="%.2f", key="sal_m")
            motivo = st.text_input("Motivo (p. ej. indexación 5 %)", key="sal_mot")
            if st.form_submit_button("Guardar salario"):
                try:
                    with con_bd() as con:
                        planillas.actualizar_salario(con, sesion, int(t["id"]), desde, decimal_de(monto), motivo)
                    st.success("Salario actualizado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with c2.form("form_retiro"):
            st.markdown("**Retiro (inicia el plazo de la liquidación final)**")
            t = st.selectbox("Trabajador", activos, format_func=lambda x: x["nombre"], key="ret_t")
            fecha = st.date_input("Fecha de retiro", hoy, format="DD/MM/YYYY", key="ret_f")
            motivo = st.text_input("Motivo", key="ret_m")
            if st.form_submit_button("Registrar retiro"):
                try:
                    with con_bd() as con:
                        planillas.registrar_retiro(con, sesion, int(t["id"]), fecha, motivo)
                    st.success(f"Retiro registrado: la liquidación final vence el "
                               f"{fecha_pantalla(fecha + timedelta(days=int(p['liquidacion_final_dias'])))}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ------------------------------------------------------------ planilla mensual
def _tab_planilla(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        edita = _puede(con, sesion, "EDITAR")
        autoriza = _puede(con, sesion, "AUTORIZAR")
        lista = planillas.listar_planillas(con, sesion)
        personal = [t for t in planillas.listar_personal(con, sesion, hoy) if t["estado"] == "ACTIVO"]
    if edita:
        with st.expander("Preparar planilla del mes (FIN-EGR-09 y FIN-EGR-10)", expanded=not lista):
            c1, c2 = st.columns(2)
            anio = int(c1.number_input("Año", min_value=2026, max_value=2100, value=hoy.year, step=1, key="pl_a"))
            mes = c2.selectbox("Mes", list(range(1, 13)), index=hoy.month - 1, format_func=lambda m: MESES[m - 1],
                               key="pl_m")
            bonos = {}
            for t in personal:
                bonos[int(t["id"])] = decimal_de(st.number_input(f"Bonos de {t['nombre']} (Bs)", min_value=0.0, step=10.0,
                                                                 format="%.2f", key=f"pl_b{t['id']}"))
            try:
                with con_bd() as con:
                    previa = planillas.calcular_planilla(con, anio, mes, bonos)
                filas = [{"nombre": l["nombre"], "area": l["area"], "dias": l["calculo"].dias,
                          "total": l["calculo"].total_ganado, "laboral": l["calculo"].aporte_laboral,
                          "liquido": l["calculo"].liquido, "patronal": l["calculo"].aporte_patronal,
                          "aguinaldo": l["calculo"].aguinaldo, "indemnizacion": l["calculo"].indemnizacion,
                          "carne": f"{Decimal(l['pct_carne']) * 100:.2f} % ({l['fuente']})".replace(".", ",")}
                         for l in previa["lineas"]]
                st.dataframe(tabla(filas, [("nombre", "Trabajador"), ("area", "Área"), ("dias", "Días"),
                                           ("total", "Total ganado"), ("laboral", "Aporte laboral 12,71 %"),
                                           ("liquido", "Líquido"), ("patronal", "Patronal 17,21 %"),
                                           ("aguinaldo", "Aguinaldo"), ("indemnizacion", "Indemnización"),
                                           ("carne", "A carne")],
                                   montos=("total", "laboral", "liquido", "patronal", "aguinaldo", "indemnizacion")),
                             hide_index=True, use_container_width=True)
                if st.button("Registrar planilla", type="primary"):
                    with con_bd() as con:
                        pid = planillas.preparar_planilla(con, sesion, anio, mes, bonos)
                    st.success(f"Planilla n.º {pid} preparada para la autorización de la Gerencia General.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    _periodos(lista)
    st.dataframe(tabla(lista, [("id", "N.º"), ("periodo", "Periodo"), ("total_ganado", "Total ganado"),
                               ("liquido", "Líquido"), ("estado", "Estado"), ("sueldos_pagados_en", "Sueldos pagados"),
                               ("aportes_pagados_en", "Aportes pagados")], montos=("total_ganado", "liquido"),
                       fechas=("sueldos_pagados_en", "aportes_pagados_en")), hide_index=True, use_container_width=True)
    vigentes = [x for x in lista if x["estado"] != "ANULADA"]
    if not vigentes:
        return
    elegida = st.selectbox("Planilla", vigentes, format_func=lambda x: f"N.º {x['id']} · {MESES[x['mes'] - 1]} "
                           f"{x['anio']} · {x['estado']}", key="pl_sel")
    with con_bd() as con:
        p = planillas.obtener_planilla(con, sesion, int(elegida["id"]))
    st.markdown(f"Estado: {chip(p['estado'])} · vence el pago de sueldos {fecha_pantalla(p['vencimientos']['sueldos'])} "
                f"y de aportes {fecha_pantalla(p['vencimientos']['aportes'])}", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    if p["estado"] == "PREPARADA" and autoriza and c1.button("Autorizar planilla", type="primary"):
        try:
            with con_bd() as con:
                planillas.autorizar_planilla(con, sesion, int(p["id"]))
            st.success("Planilla autorizada.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if p["estado"] == "AUTORIZADA" and edita and c1.button("Contabilizar (asiento de devengo)", type="primary"):
        try:
            with con_bd() as con:
                aid = planillas.contabilizar_planilla(con, sesion, int(p["id"]))
            st.success(f"Asiento n.º {aid} registrado.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    if p["estado"] in ("PREPARADA", "AUTORIZADA") and edita:
        motivo = c3.text_input("Motivo de anulación", key="pl_anu")
        if c3.button("Anular planilla"):
            try:
                with con_bd() as con:
                    planillas.anular_planilla(con, sesion, int(p["id"]), motivo)
                st.success("Planilla anulada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if p["estado"] == "CONTABILIZADA" and edita:
        a, b = st.columns(2)
        with a:
            st.markdown(f"**Pago de sueldos** · {bs(p['liquido'])}")
            if p["pago_sueldos_mov_id"]:
                st.caption(f"Pagados el {fecha_pantalla(p['sueldos_pagados_en'])}.")
            else:
                fecha = st.date_input("Fecha", hoy, format="DD/MM/YYYY", key="ps_f")
                banco = st.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="ps_b")
                with con_bd() as con:
                    aut = _autorizacion_desembolso(con, sesion, p["liquido"], fecha, "ps_aut")
                if st.button("Pagar sueldos"):
                    try:
                        with con_bd() as con:
                            planillas.pagar_sueldos(con, sesion, int(p["id"]), fecha, banco, autorizacion_id=aut)
                        st.success("Sueldos pagados.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        with b:
            aportes = p["aporte_laboral"] + p["aporte_patronal"]
            st.markdown(f"**Aportes a la Gestora y la CNS** · {bs(aportes)}")
            if p["pago_aportes_mov_id"]:
                st.caption(f"Pagados el {fecha_pantalla(p['aportes_pagados_en'])}.")
            else:
                fecha = st.date_input("Fecha", hoy, format="DD/MM/YYYY", key="pa_f")
                banco = st.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="pa_b")
                with con_bd() as con:
                    aut = _autorizacion_desembolso(con, sesion, aportes, fecha, "pa_aut")
                if st.button("Pagar aportes"):
                    try:
                        with con_bd() as con:
                            planillas.pagar_aportes(con, sesion, int(p["id"]), fecha, banco, autorizacion_id=aut)
                        st.success("Aportes pagados.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)


# ---------------------------------------------------------- hojas de tiempo
def _tab_hojas(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        registra = _puede(con, sesion, "CREAR")
        edita = _puede(con, sesion, "EDITAR")
        verifica = _puede(con, sesion, "VERIFICAR")
        polivalentes = [t for t in planillas.listar_personal(con, sesion, hoy) if t["polivalente"]
                        and t["estado"] == "ACTIVO"]
        hojas = planillas.listar_hojas(con, sesion, desde=hoy - timedelta(days=60))
        conciliaciones = planillas.listar_conciliaciones_mod(con, sesion)
        contabilizadas = [x for x in planillas.listar_planillas(con, sesion) if x["estado"] == "CONTABILIZADA"]
    st.caption("POL-06 y CI-FIN-03: desde 2027 el costo del personal polivalente se prorratea entre 6.1.01.01 (carne) y "
               "6.1.01.02 (alimento) con las horas de las hojas firmadas; sin hojas se aplica el 75 % por defecto.")
    if registra and polivalentes:
        with st.form("form_hoja", clear_on_submit=True):
            c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 1])
            t = c1.selectbox("Trabajador", polivalentes, format_func=lambda x: x["nombre"])
            fecha = c2.date_input("Día", hoy, format="DD/MM/YYYY")
            hc = c3.number_input("Horas crianza/faenado", min_value=0, max_value=24, step=1)
            ha = c4.number_input("Horas planta de alimento", min_value=0, max_value=24, step=1)
            firmada = c5.checkbox("Firmada por el trabajador", value=True)
            if st.form_submit_button("Registrar hoja (FIN-EGR-12)"):
                try:
                    with con_bd() as con:
                        planillas.registrar_hoja_tiempo(con, sesion, int(t["id"]), fecha, int(hc), int(ha), firmada)
                    st.success("Hoja registrada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for h in hojas:
        h["firma"] = "Firmada" if h["firmada_trabajador"] else ("Regularizada" if h["regularizacion"] else "SIN FIRMA")
    st.dataframe(tabla(hojas, [("fecha", "Día"), ("nombre", "Trabajador"), ("horas_crianza", "Crianza/faenado"),
                               ("horas_alimento", "Planta"), ("firma", "Firma"), ("validado_por", "Validado por")],
                       fechas=("fecha",)), hide_index=True, use_container_width=True)
    sin_firma = [h for h in hojas if h["firma"] == "SIN FIRMA"]
    if registra and sin_firma:
        h = st.selectbox("Hoja sin firma", sin_firma, format_func=lambda x: f"{fecha_pantalla(x['fecha'])} · {x['nombre']}")
        constancia = st.text_input("Constancia de la omisión", key="hoja_reg")
        if st.button("Regularizar hoja"):
            try:
                with con_bd() as con:
                    planillas.regularizar_hoja(con, sesion, int(h["id"]), constancia)
                st.success("Hoja regularizada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)

    st.subheader("Conciliación pagado vs absorbido (FIN-EGR-13)")
    _periodos(conciliaciones)
    st.dataframe(tabla(conciliaciones, [("periodo", "Periodo"), ("costo_pagado", "Costo pagado"),
                                        ("absorbido_carne", "Absorbido carne"), ("absorbido_alimento", "Absorbido alimento"),
                                        ("diferencia", "Diferencia"), ("fiscalizado_por", "Fiscalizado por")],
                       montos=("costo_pagado", "absorbido_carne", "absorbido_alimento", "diferencia")),
                 hide_index=True, use_container_width=True)
    pendientes = [x for x in contabilizadas if x["id"] not in {c["planilla_id"] for c in conciliaciones}]
    if edita and pendientes:
        x = st.selectbox("Planilla por conciliar", pendientes, format_func=lambda p: f"{MESES[p['mes'] - 1]} {p['anio']}")
        obs = st.text_input("Observación CI-FIN-14", key="conc_obs")
        if st.button("Conciliar"):
            try:
                with con_bd() as con:
                    r = planillas.conciliar_mano_obra(con, sesion, int(x["id"]), obs or None)
                (st.success if r["diferencia"] == 0 else st.error)(f"Diferencia: {bs(r['diferencia'])}.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    por_fiscalizar = [c for c in conciliaciones if not c["fiscalizado_por"]]
    if verifica and por_fiscalizar:
        c = st.selectbox("Conciliación por fiscalizar", por_fiscalizar,
                         format_func=lambda x: f"{MESES[x['mes'] - 1]} {x['anio']}")
        if st.button("Fiscalizar (Consejo de Vigilancia)"):
            try:
                with con_bd() as con:
                    planillas.fiscalizar_conciliacion(con, sesion, int(c["id"]))
                st.success("Conciliación fiscalizada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)

    if verifica:
        st.subheader("Verificación cruzada de horas (CI-FIN-14)")
        c1, c2 = st.columns(2)
        desde = c1.date_input("Desde", hoy - timedelta(days=180), format="DD/MM/YYYY", key="v_d")
        hasta = c2.date_input("Hasta", hoy, format="DD/MM/YYYY", key="v_h")
        with con_bd() as con:
            semanas = planillas.semanas_con_horas(con, desde, hasta)
        if not semanas:
            st.info("No hay horas de planta registradas en el periodo.")
            return
        st.caption(f"{len(semanas)} semanas con horas de planta, ordenadas por prioridad de muestreo (mayor proporción a "
                   "alimento). La muestra mínima es el 20 %.")
        with st.form("form_verif"):
            a, b = st.columns(2)
            minimo = a.number_input("Mínimo h/qq razonable", min_value=0.0, step=0.05, value=0.15, format="%.2f")
            maximo = b.number_input("Máximo h/qq razonable", min_value=0.0, step=0.05, value=0.35, format="%.2f")
            criterio = st.text_input("Criterio documentado", value="Ensacado de 40 qq por semana (PO-03)")
            muestra = {}
            for s in semanas:
                cols = st.columns([2, 1, 1])
                incluir = cols[0].checkbox(f"Semana del {fecha_pantalla(s['semana_inicio'])} — {s['horas_alimento']} h "
                                           "de planta", key=f"v_s{s['semana_inicio']}")
                qq = cols[1].number_input("Quintales ensacados", min_value=0.0, step=1.0, key=f"v_q{s['semana_inicio']}")
                if incluir:
                    muestra[s["semana_inicio"]] = decimal_de(qq)
            if st.form_submit_button("Registrar verificación"):
                try:
                    with con_bd() as con:
                        r = planillas.registrar_verificacion_horas(con, sesion, desde, hasta, decimal_de(minimo),
                                                                   decimal_de(maximo), criterio, muestra)
                    (st.warning if r["observadas"] else st.success)(
                        f"Verificación n.º {r['id']}: {len(r['observadas'])} semana(s) observada(s).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ----------------------------------------------------------- personal eventual
def _tab_eventual(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        edita = _puede(con, sesion, "EDITAR")
        autoriza = sesion.rol == "GG" or seguridad.orden_suplencia(con, sesion, hoy) is not None
        lista = planillas.listar_eventuales(con, sesion)
        p = seguridad.contexto_desde_bd(con, hoy).parametros
    st.caption(f"PR-FIN-04: circular con {p['circular_cierre_dias_antelacion']} días de antelación, autorización el lunes "
               f"de la semana de faenado, contrato CONT-EGR-01 y liquidación a {bs(p['tarifa_eventual_por_ave'])} por ave.")
    if sesion.rol == "UT":
        with st.form("form_circular", clear_on_submit=True):
            st.markdown("**Circular de aviso de cierre (CIR-EGR-01)**")
            c1, c2, c3, c4, c5 = st.columns(5)
            emision = c1.date_input("Emisión", hoy, format="DD/MM/YYYY")
            faenado = c2.date_input("Faenado (día 73)", hoy + timedelta(days=7), format="DD/MM/YYYY")
            lote = c3.text_input("Lote")
            aves = c4.number_input("Aves a procesar", min_value=1, step=100, value=3000)
            mortalidad = c5.number_input("Mortalidad acumulada (%)", min_value=0.0, max_value=99.0, step=0.1)
            if st.form_submit_button("Emitir circular"):
                try:
                    with con_bd() as con:
                        planillas.emitir_circular(con, sesion, emision, faenado, lote, int(aves), decimal_de(mortalidad))
                    st.success("Circular emitida.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    for x in lista:
        x["circular"] = f"{x['numero']}/{x['anio']}"
        x["aves_txt"] = _miles(x["aves"])
        x["dpf_txt"] = _pct_txt(x["dpf"])
        x["semaforo_txt"] = SEMAFORO.get(x["semaforo"] or "", "")
        x["acuses"] = ("GG " if x["acuse_gg_por"] else "") + ("JF" if x["acuse_jf_por"] else "") or "Pendiente"
    st.dataframe(tabla(lista, [("circular", "Circular"), ("lote", "Lote"), ("fecha_faenado", "Faenado"), ("aves_txt", "Aves"),
                               ("acuses", "Acuses"), ("estado_autorizacion", "FIN-EGR-06"), ("contratos", "Contratos"),
                               ("presupuesto", "Presupuesto"), ("total", "Liquidado"), ("dpf_txt", "DPF"),
                               ("semaforo_txt", "Semáforo")], montos=("presupuesto", "total"), fechas=("fecha_faenado",)),
                 hide_index=True, use_container_width=True)
    if not lista:
        return
    x = st.selectbox("Circular", lista, format_func=lambda c: f"{c['circular']} · lote {c['lote']} · "
                     f"{fecha_pantalla(c['fecha_faenado'])}", key="ev_sel")
    if sesion.rol in ("GG", "JF") and not x[f"acuse_{sesion.rol.lower()}_por"]:
        if st.button("Acusar recibo de la circular"):
            try:
                with con_bd() as con:
                    planillas.acusar_circular(con, sesion, int(x["id"]))
                st.success("Acuse registrado.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if x["autorizacion_eventual_id"] is None:
        if edita:
            faenado = date.fromisoformat(x["fecha_faenado"])
            lunes = faenado - timedelta(days=faenado.weekday())
            with con_bd() as con:
                disp = planillas.disponibilidad_eventual(con, faenado, planillas.costo_eventual(con, int(x["aves"]), faenado))
            st.caption(f"FIN-EGR-02 · partida de personal eventual del ciclo {disp['ciclo']}: aprobado {bs(disp['aprobado'])},"
                       f" ejecutado {bs(disp['ejecutado'])}, saldo {bs(disp['saldo'])}, se requieren {bs(disp['solicitado'])}.")
            c1, c2 = st.columns(2)
            trabajadores = c1.number_input("Trabajadores a contratar", min_value=1, step=1, value=5, key="ev_t")
            if c2.button(f"Solicitar autorización para el lunes {fecha_pantalla(lunes)}", type="primary"):
                try:
                    with con_bd() as con:
                        r = planillas.solicitar_contratacion(con, sesion, int(x["id"]), lunes, int(trabajadores))
                    st.success(f"Solicitud registrada por {bs(r['presupuesto'])}.")
                    (st.warning if r["suspendida"] else st.info)(r["mensaje_sme"])
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        return
    with con_bd() as con:
        ev = planillas.obtener_eventual(con, sesion, int(x["autorizacion_eventual_id"]))
    if ev["estado"] == "SOLICITADA" and autoriza:
        st.info(f"La autorización se firma el lunes {fecha_pantalla(ev['fecha_lunes'])} (CI-FIN-09, POL-06 Art. 2).")
        suplencia = None
        if sesion.rol != "GG":
            causa = st.text_input("Causa de la suplencia (CI-FIN-17)", key="ev_causa")
            constancia = st.text_input("Constancia escrita", key="ev_const")
            suplencia = seguridad.Suplencia(causa, constancia)
        if st.button("Autorizar contratación (FIN-EGR-06)", type="primary"):
            try:
                with con_bd() as con:
                    estado = planillas.autorizar_contratacion(con, sesion, int(ev["id"]), hoy, suplencia)
                st.success(f"Estado: {estado}.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    if ev["estado"] == "AUTORIZADA" and edita:
        with st.form("form_contrato", clear_on_submit=True):
            st.markdown(f"**Contrato CONT-EGR-01** ({len(ev['contratos'])} de {ev['trabajadores']})")
            c1, c2 = st.columns(2)
            nombre = c1.text_input("Nombre del trabajador eventual")
            ci = c2.text_input("C.I.")
            if st.form_submit_button("Registrar contrato"):
                try:
                    with con_bd() as con:
                        planillas.registrar_contrato(con, sesion, int(ev["id"]), nombre, ci, hoy)
                    st.success("Contrato registrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        if ev["contratos"]:
            with st.form("form_liquidacion"):
                st.markdown("**Liquidación de jornales (FIN-EGR-08)**")
                unidades = {int(k["id"]): int(st.number_input(f"Aves faenadas por {k['nombre']}", min_value=0, step=10,
                                                              key=f"lq_{k['id']}")) for k in ev["contratos"]}
                if st.form_submit_button("Liquidar", type="primary"):
                    try:
                        with con_bd() as con:
                            r = planillas.liquidar_jornales(con, sesion, int(ev["id"]), hoy, unidades)
                        (st.success if r["semaforo"] == "BUENO" else st.warning)(
                            f"Total {bs(r['total'])} · DPF {_pct_txt(r['dpf'])} · {SEMAFORO[r['semaforo']]}.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if x["liquidacion_id"]:
        st.markdown(f"Liquidación: {bs(x['total'])} · DPF {x['dpf_txt']} {chip(x['semaforo'])}",
                    unsafe_allow_html=True)
        if not x["pago_autorizado_por"] and _puede_autorizar(sesion):
            nota = st.text_input("Justificación (obligatoria si el DPF es Malo)", key="lq_j")
            if st.button("Autorizar pago de jornales"):
                try:
                    with con_bd() as con:
                        planillas.autorizar_pago_jornales(con, sesion, int(x["liquidacion_id"]), nota or None)
                    st.success("Pago autorizado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        if x["pago_autorizado_por"] and not x["movimiento_bancario_id"] and edita:
            c1, c2 = st.columns(2)
            banco = c1.selectbox("Cuenta", list(NOMBRE_BANCO), format_func=NOMBRE_BANCO.get, key="lq_b")
            with con_bd() as con:
                aut = _autorizacion_desembolso(con, sesion, x["total"], hoy, "lq_aut")
            if c2.button("Pagar jornales"):
                try:
                    with con_bd() as con:
                        planillas.pagar_jornales(con, sesion, int(x["liquidacion_id"]), hoy, banco, autorizacion_id=aut)
                    st.success("Jornales pagados.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


def _puede_autorizar(sesion: Sesion) -> bool:
    with con_bd() as con:
        return _puede(con, sesion, "AUTORIZAR")


# ------------------------------------------------------------ liquidación final
def _tab_liquidacion(sesion: Sesion) -> None:
    hoy = date.today()
    with con_bd() as con:
        edita = _puede(con, sesion, "EDITAR")
        autoriza = _puede(con, sesion, "AUTORIZAR")
        liquidaciones = planillas.listar_liquidaciones_finales(con, sesion)
        con_liq = {l["personal_id"] for l in liquidaciones}
        retirados = [t for t in planillas.listar_personal(con, sesion, hoy) if t["estado"] == "RETIRADO"
                     and t["id"] not in con_liq]
        p = seguridad.contexto_desde_bd(con, hoy).parametros
    st.caption(f"FIN-EGR-11: dentro de los {p['liquidacion_final_dias']} días del retiro, para contratos de más de "
               f"{p['liquidacion_final_antiguedad_min_dias']} días. Fuera de plazo se registra la causa y se reporta al CV.")
    if edita and retirados:
        t = st.selectbox("Trabajador retirado", retirados, format_func=lambda x: f"{x['nombre']} · retiro "
                         f"{fecha_pantalla(x['fecha_retiro'])}")
        c1, c2 = st.columns(2)
        vacaciones = c1.number_input("Días de vacación no gozados", min_value=0.0, step=0.5, key="lf_v")
        fecha = c2.date_input("Fecha de preparación", hoy, format="DD/MM/YYYY", key="lf_f")
        try:
            with con_bd() as con:
                calc = planillas.calcular_liquidacion_final(con, int(t["id"]), decimal_de(vacaciones))
            st.dataframe(tabla([
                {"c": "Indemnización", "m": calc["indemnizacion"]}, {"c": "Vacaciones no gozadas", "m": calc["vacaciones"]},
                {"c": "Aguinaldo proporcional", "m": calc["aguinaldo"]},
                {"c": f"Salario proporcional ({calc['salario_dias']} días)", "m": calc["salario"]},
                {"c": "(–) Aporte laboral", "m": calc["aporte_laboral"]}, {"c": "Total líquido", "m": calc["total"]}],
                [("c", "Concepto"), ("m", "Monto")], montos=("m",)), hide_index=True, use_container_width=True)
            limite = date.fromisoformat(t["fecha_retiro"]) + timedelta(days=int(p["liquidacion_final_dias"]))
            causa = None
            if fecha > limite:
                causa = st.text_input(f"Causa del retraso (venció el {fecha_pantalla(limite)})", key="lf_c")
            if st.button("Preparar liquidación final", type="primary"):
                with con_bd() as con:
                    planillas.preparar_liquidacion_final(con, sesion, int(t["id"]), fecha, decimal_de(vacaciones), causa)
                st.success("Liquidación preparada.")
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    for l in liquidaciones:
        l["plazo"] = "En plazo" if l["dentro_de_plazo"] else "Fuera de plazo"
    st.dataframe(tabla(liquidaciones, [("nombre", "Trabajador"), ("fecha_retiro", "Retiro"),
                                       ("fecha_preparacion", "Preparada"), ("plazo", "Plazo"), ("total", "Total"),
                                       ("estado", "Estado")], montos=("total",), fechas=("fecha_retiro", "fecha_preparacion")),
                 hide_index=True, use_container_width=True)
    abiertas = [l for l in liquidaciones if l["estado"] != "CONTABILIZADA"]
    if abiertas:
        l = st.selectbox("Liquidación", abiertas, format_func=lambda x: f"{x['nombre']} · {x['estado']}")
        if l["estado"] == "PREPARADA" and autoriza and st.button("Autorizar liquidación final"):
            try:
                with con_bd() as con:
                    planillas.autorizar_liquidacion_final(con, sesion, int(l["id"]))
                st.success("Liquidación autorizada.")
            except ErrorPollolandia as exc:
                mostrar_error(exc)
        if l["estado"] == "AUTORIZADA" and edita and st.button("Contabilizar liquidación final", type="primary"):
            try:
                with con_bd() as con:
                    planillas.contabilizar_liquidacion_final(con, sesion, int(l["id"]), hoy)
                st.success("Liquidación contabilizada; el pago se registra en Tesorería (2.1.02.01).")
            except ErrorPollolandia as exc:
                mostrar_error(exc)


# --------------------------------------------------------------------- alertas
def _tab_alertas(sesion: Sesion) -> None:
    fecha = st.date_input("Fecha de control", date.today(), format="DD/MM/YYYY", key="al_f")
    with con_bd() as con:
        seguridad.exigir_permiso(con, sesion, "planillas", "VER")
        alertas = planillas.alertas_pago(con, fecha)
    if not alertas:
        st.success("No hay obligaciones laborales pendientes.")
        return
    for a in alertas:
        a["estado_txt"] = {"VENCIDO": "VENCIDO", "POR_VENCER": "Por vencer", "AL_DIA": "Al día"}[a["estado"]]
        a["tipo_txt"] = {"SUELDOS": "Sueldos", "APORTES": "Aportes Gestora/CNS", "AGUINALDO": "Aguinaldo",
                         "LIQUIDACION_FINAL": "Liquidación final"}.get(a["tipo"], a["tipo"])
    st.dataframe(tabla(alertas, [("estado_txt", "Estado"), ("tipo_txt", "Obligación"), ("referencia", "Referencia"),
                                 ("vencimiento", "Vence"), ("monto", "Monto"), ("detalle", "Detalle")],
                       montos=("monto",), fechas=("vencimiento",)), hide_index=True, use_container_width=True)
    st.caption("Los plazos de sueldos, aportes y aguinaldo son parámetros POR VALIDAR (no figuran en los documentos).")


# -------------------------------------------------------------------- formatos
def _tab_formatos(sesion: Sesion) -> None:
    st.caption("Formatos del Ciclo 2 (planillas) en PDF, con el bloque de firmas.")
    ctx = pdf.Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo, cargo=CARGO_ROL.get(sesion.rol, sesion.rol),
                       ciclo=st.text_input("N.º de ciclo (opcional)", key="fmtp_ciclo"))
    opciones = {"FIN-EGR-09": "Planilla de Sueldos", "FIN-EGR-10": "Planilla de Beneficios Sociales",
                "FIN-EGR-11": "Liquidación Final de Beneficios", "FIN-EGR-12": "Hojas de Tiempo Firmadas",
                "FIN-EGR-13": "Reporte de Absorción de Mano de Obra", "CIR-EGR-01": "Circular de Aviso de Cierre",
                "FIN-EGR-06": "Autorización de Personal Eventual", "CONT-EGR-01": "Contrato Eventual",
                "FIN-EGR-08": "Liquidación de Jornales Eventuales"}
    codigo = st.selectbox("Formato", list(opciones), format_func=lambda c: f"{c} — {opciones[c]}", key="fmtp_cod")
    try:
        with con_bd() as con:
            datos: bytes | None = None
            if codigo in ("FIN-EGR-09", "FIN-EGR-10"):
                lista = [x for x in planillas.listar_planillas(con, sesion) if x["estado"] != "ANULADA"]
                if lista:
                    x = st.selectbox("Planilla", lista, format_func=lambda p: f"{MESES[p['mes'] - 1]} {p['anio']}")
                    p = planillas.obtener_planilla(con, sesion, int(x["id"]))
                    datos = (pdf.fin_egr_09 if codigo == "FIN-EGR-09" else pdf.fin_egr_10)(p, ctx)
            elif codigo == "FIN-EGR-11":
                lista = planillas.listar_liquidaciones_finales(con, sesion)
                if lista:
                    x = st.selectbox("Liquidación", lista, format_func=lambda l: l["nombre"])
                    datos = pdf.fin_egr_11(planillas.obtener_liquidacion_final(con, sesion, int(x["id"])), ctx)
            elif codigo == "FIN-EGR-12":
                lista = [t for t in planillas.listar_personal(con, sesion) if t["polivalente"]]
                if lista:
                    t = st.selectbox("Trabajador", lista, format_func=lambda x: x["nombre"])
                    c1, c2 = st.columns(2)
                    desde = c1.date_input("Desde", date.today() - timedelta(days=6), format="DD/MM/YYYY", key="f12d")
                    hasta = c2.date_input("Hasta", date.today(), format="DD/MM/YYYY", key="f12h")
                    hojas = planillas.listar_hojas(con, sesion, int(t["id"]), desde, hasta)
                    if hojas:
                        datos = pdf.fin_egr_12(hojas, t["nombre"], ctx)
            elif codigo == "FIN-EGR-13":
                lista = planillas.listar_conciliaciones_mod(con, sesion)
                if lista:
                    datos = pdf.fin_egr_13(lista, ctx)
            else:
                lista = [x for x in planillas.listar_eventuales(con, sesion)
                         if codigo == "CIR-EGR-01" or x["autorizacion_eventual_id"]]
                if codigo == "FIN-EGR-08":
                    lista = [x for x in lista if x["liquidacion_id"]]
                if lista:
                    x = st.selectbox("Circular / faenado", lista, format_func=lambda c: f"{c['numero']}/{c['anio']} · "
                                     f"lote {c['lote']}")
                    if codigo == "CIR-EGR-01":
                        datos = pdf.cir_egr_01(planillas.obtener_circular(con, sesion, int(x["id"])), ctx)
                    elif codigo == "FIN-EGR-08":
                        datos = pdf.fin_egr_08(planillas.obtener_liquidacion_eventual(con, sesion,
                                                                                      int(x["liquidacion_id"])), ctx)
                    else:
                        ev = planillas.obtener_eventual(con, sesion, int(x["autorizacion_eventual_id"]))
                        if codigo == "FIN-EGR-06":
                            datos = pdf.fin_egr_06(ev, ctx)
                        elif ev["contratos"]:
                            k = st.selectbox("Contrato", ev["contratos"], format_func=lambda c: c["nombre"])
                            datos = pdf.cont_egr_01(ev, k, ctx)
        if datos is None:
            st.info("Aún no hay registros para este formato.")
        else:
            st.download_button(f"Descargar {codigo} en PDF", datos, file_name=f"{codigo}_{date.today().isoformat()}.pdf",
                               mime="application/pdf", type="primary")
    except (ErrorPollolandia, ValueError) as exc:
        mostrar_error(exc)


def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 3 — Personal y planillas."""
    st.header("Personal y planillas · Ciclo 2")
    _resumen(sesion)
    pestañas = st.tabs(["Personal", "Planilla mensual", "Hojas de tiempo", "Personal eventual", "Liquidación final",
                        "Alertas", "Formatos"])
    funciones = (_tab_personal, _tab_planilla, _tab_hojas, _tab_eventual, _tab_liquidacion, _tab_alertas, _tab_formatos)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            funcion(sesion)
