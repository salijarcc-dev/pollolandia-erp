"""Módulo 9 — RAU, cumplimiento legal y fiscal y archivo contable (Fase 11).

Pestañas: Calendario fiscal, Cuota y Form. 701, Superficie y CAPEX, Alimento
(I-1, I-2 y balance de masas), Compradores y servicios a terceros, Revisión
del CV (INF-CV-09), Expediente EXP-RAU, Fiscalización y Archivo contable.

Solo llama a ``servicios/`` y a ``reportes/``. Finanzas registra; la Gerencia
General autoriza compradores, firma balances y conoce las alertas rojas; el
Consejo de Administración aprueba el programa de excedente, el acta de
compatibilidad y resuelve los servicios a terceros; el Consejo de Vigilancia
ejecuta la revisión trimestral y anual y visa las destrucciones.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import streamlit as st

from modulos.comun import chip, con_bd, decimal_de, mostrar_error, tabla
from reportes import formatos_rau as rep
from reportes.formatos_tesoreria import Contexto
from servicios import archivo, rau, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion
from utils.formato import fecha_pantalla, formato_bs

CARGO_ROL = {"GG": "Gerente General", "JF": "Jefe de Finanzas y Comercialización", "CV": "Consejo de Vigilancia",
             "CA": "Consejo de Administración", "AG": "Asamblea General", "UT": "Unidad Técnica"}
ESTADO_TXT = {"CUMPLIDO": "Cumplido", "CUMPLIDO_CON_OBSERVACION": "Cumplido con observación", "VENCIDO": "Vencido",
              "ALERTA_30": "Alerta 30 días", "ALERTA_60": "Alerta 60 días", "PROGRAMADO": "Programado",
              "PENDIENTE": "Pendiente", "CUMPLIDO_FUERA_DE_PLAZO": "Cumplido fuera de plazo"}


# ==========================================================================
# APOYO
# ==========================================================================
def _ctx(sesion: Sesion, detalle: str = "") -> Contexto:
    return Contexto(fecha=date.today(), elaborado_por=sesion.nombre_completo,
                    cargo=CARGO_ROL.get(sesion.rol, sesion.rol), ciclo=detalle)


def _pdf(nombre: str, datos: bytes, clave: str) -> None:
    st.download_button(f"Descargar {nombre} (PDF)", datos, file_name=f"{nombre}.pdf", mime="application/pdf", key=clave)


def _puede(sesion: Sesion, recurso: str, accion: str) -> bool:
    with con_bd() as con:
        return seguridad.tiene_permiso(con, sesion.rol, recurso, accion)


def _gestion(clave: str, texto: str = "Gestión (julio a junio)") -> int | None:
    with con_bd() as con:
        lista = rau.gestiones(con)
    if not lista:
        st.info("No hay gestiones registradas.")
        return None
    hoy = date.today()
    actual = hoy.year if hoy.month >= 7 else hoy.year - 1
    indice = lista.index(actual) if actual in lista else 0
    return st.selectbox(texto, lista, index=indice, key=clave, format_func=lambda g: f"{g}-{(g + 1) % 100:02d}")


def _semaforo(valor: str | None) -> str:
    return chip(valor) if valor else chip("NO APLICA")


def _ha(valor: Any) -> str:
    return f"{Decimal(valor):,.3f}".replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")


def _pct(valor: Any) -> str:
    if valor is None:
        return "—"
    return f"{Decimal(valor) * 100:.1f} %".replace(".", ",")


def _alertas(sesion: Sesion) -> None:
    try:
        with con_bd() as con:
            alertas = rau.alertas_rau(con, sesion)
    except ErrorPollolandia:
        return
    for a in alertas[:12]:
        (st.error if a["nivel"] == "ROJO" else st.warning)(f"**{a['origen']}:** {a['mensaje']}")
    if len(alertas) > 12:
        st.caption(f"… y {len(alertas) - 12} alerta(s) más en el calendario.")


# ==========================================================================
# 1. CALENDARIO FISCAL
# ==========================================================================
def _tab_calendario(sesion: Sesion) -> None:
    gestion = _gestion("cal_gestion")
    if gestion is None:
        return
    with con_bd() as con:
        eventos = rau.calendario_fiscal(con, sesion, gestion)
        cert = rau.certificado_vigente(con, date.today())
    st.markdown(f"**Calendario de la gestión {gestion}-{(gestion + 1) % 100:02d}** — el Formulario 701 de esta "
                f"gestión vence el {fecha_pantalla(date(gestion + 1, 10, 31))} (PET-RAU §3.2).")
    for e in eventos:
        cumplido = f" · cumplido el {fecha_pantalla(e['cumplido_en'])}" if e["cumplido_en"] else ""
        referencia = f" ({e['referencia']})" if e["referencia"] else ""
        st.markdown(f"{_semaforo(e['semaforo'])} **{e['descripcion']}** — límite {fecha_pantalla(e['limite'])} · "
                    f"{ESTADO_TXT.get(e['estado'], e['estado'])}{cumplido}{referencia}", unsafe_allow_html=True)
    _pdf(f"CAL-RAU_{gestion}", rep.calendario_fiscal(gestion, eventos, _ctx(sesion)), "pdf_cal")
    st.caption(f"Certificado RAU vigente: {cert['numero'] if cert else 'no registrado'}"
               + (f" · último pago: {cert['ultimo_pago_ref']}" if cert and cert["ultimo_pago_ref"] else ""))

    if _puede(sesion, "rau", "CREAR"):
        c1, c2 = st.columns(2)
        with c1, st.form("form_rnd"):
            st.markdown("**Consulta de la RND de cuota (antes del 30-sep)**")
            fecha = st.date_input("Fecha de la consulta", date.today(), format="DD/MM/YYYY")
            rnd = st.text_input("RND consultada")
            cuota = st.number_input("Cuota por ha confirmada (Bs)", min_value=0.0, step=0.01, format="%.2f")
            if st.form_submit_button("Registrar consulta", type="primary"):
                try:
                    with con_bd() as con:
                        r = rau.registrar_consulta_rnd(con, sesion, gestion, fecha, rnd, decimal_de(cuota))
                    st.success("Consulta registrada " + ("dentro" if r["dentro_plazo"] else "FUERA") + " del plazo.")
                    if r["advertencia"]:
                        st.warning(r["advertencia"])
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with c2, st.form("form_certificado"):
            st.markdown("**Certificado de Inscripción al RAU**")
            numero = st.text_input("Número del certificado")
            emision = st.date_input("Fecha de emisión", date.today(), format="DD/MM/YYYY")
            if st.form_submit_button("Registrar certificado"):
                try:
                    with con_bd() as con:
                        rau.registrar_certificado(con, sesion, numero, emision)
                    st.success("Certificado registrado e indexado en la carpeta A.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if _puede(sesion, "rau", "APROBAR"):
        with st.form("form_compat"):
            st.markdown("**Acta de compatibilidad de actividades con el RAU (Consejo de Administración)**")
            fecha = st.date_input("Fecha del acta", date.today(), format="DD/MM/YYYY", key="compat_f")
            acta = st.text_input("Acta del CA")
            detalle = st.text_area("Resultado de la revisión")
            if st.form_submit_button("Registrar acta", type="primary"):
                try:
                    with con_bd() as con:
                        rau.registrar_acta_compatibilidad(con, sesion, gestion, fecha, acta, detalle)
                    st.success("Acta registrada (carpetas B y G).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 2. CUOTA, LIQUIDACIÓN, FORMULARIO 701 Y PATENTES
# ==========================================================================
def _tab_cuota(sesion: Sesion) -> None:
    gestion = _gestion("cuota_gestion")
    if gestion is None:
        return
    try:
        with con_bd() as con:
            calc = rau.calcular_cuota(con, gestion)
            liq = rau.liquidacion(con, sesion, gestion)
            f701 = rau.formulario_701(con, sesion, gestion)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        return
    advertencias = liq["advertencias"] if liq else calc["advertencias"]
    for a in advertencias:
        st.warning(f"⚠ {a}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hectáreas", _ha(liq["hectareas"] if liq else calc["hectareas"]))
    c2.metric("Cuota por ha", formato_bs(Decimal(liq["cuota_ha"]) if liq else calc["cuota_ha"]))
    c3.metric("Cuota total", formato_bs(liq["total"] if liq else calc["total"]))
    c4.metric("Por socio (12 partes)", formato_bs(calc["por_socio"]))
    st.caption(f"Superficie: {calc['fuente_superficie']}.")

    if liq is None:
        st.info("Liquidación aún no registrada. Vista previa del reparto:")
        st.dataframe(tabla(calc["reparto"], [("numero", "N.º"), ("socio", "Socio"), ("monto", "Bs")], montos=("monto",)),
                     hide_index=True, use_container_width=True)
        if _puede(sesion, "rau", "CREAR"):
            with st.form("form_liq"):
                st.markdown("**Registrar la liquidación** (asiento 7.1.05.02 contra 2.1.03.01)")
                fecha = st.date_input("Fecha de la liquidación", date.today(), format="DD/MM/YYYY")
                devengar = st.checkbox("Registrar el asiento de devengo", value=True)
                fd = st.date_input("Fecha del devengo (dentro de la gestión, período abierto)",
                                   date(gestion + 1, 6, 30), format="DD/MM/YYYY")
                if st.form_submit_button("Registrar liquidación", type="primary"):
                    try:
                        with con_bd() as con:
                            r = rau.registrar_liquidacion(con, sesion, gestion, fecha,
                                                          fecha_devengo=fd if devengar else None)
                        st.success(f"Liquidación N.º {r['id']} registrada" + (f"; asiento {r['asiento']}."
                                                                             if r["asiento"] else "."))
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    else:
        st.markdown(f"**Liquidación N.º {liq['id']}** del {fecha_pantalla(liq['fecha'])} · asiento "
                    f"{liq['asiento'] or 'pendiente'} · comunicar hasta {fecha_pantalla(liq['limite_comunicacion'])}"
                    + (f" · comunicada el {fecha_pantalla(liq['comunicada_en'])}" if liq["comunicada_en"] else ""))
        st.dataframe(tabla(liq["socios_detalle"], [("numero", "N.º"), ("socio", "Socio"), ("ci", "C.I."),
                                                   ("monto", "Bs")], montos=("monto",)),
                     hide_index=True, use_container_width=True)
        _pdf(f"LIQ-RAU_{gestion}", rep.liquidacion_rau(liq, _ctx(sesion)), "pdf_liq")
        if _puede(sesion, "rau", "CREAR"):
            c1, c2 = st.columns(2)
            if not liq["asiento"]:
                with c1, st.form("form_devengo"):
                    fd = st.date_input("Fecha del devengo", date(gestion + 1, 6, 30), format="DD/MM/YYYY")
                    if st.form_submit_button("Registrar devengo"):
                        try:
                            with con_bd() as con:
                                st.success(f"Asiento {rau.devengar_liquidacion(con, sesion, gestion, fd)}.")
                        except ErrorPollolandia as exc:
                            mostrar_error(exc)
            if not liq["comunicada_en"]:
                with c2, st.form("form_comunicar"):
                    fc = st.date_input("Fecha de comunicación a los socios", date.today(), format="DD/MM/YYYY")
                    if st.form_submit_button("Registrar comunicación"):
                        try:
                            with con_bd() as con:
                                r = rau.comunicar_liquidacion(con, sesion, gestion, fc)
                            (st.success if r["dentro_plazo"] else st.warning)(
                                "Comunicada " + ("dentro" if r["dentro_plazo"] else "FUERA") + " del plazo.")
                        except ErrorPollolandia as exc:
                            mostrar_error(exc)

    st.subheader("Formulario 701 V.3")
    if f701:
        st.markdown(f"{_semaforo(f701['semaforo'])} Presentado el {fecha_pantalla(f701['fecha_presentacion'])} con "
                    f"{f701['dias_habiles_antes']} días hábiles de anticipación · orden {f701['numero_orden']} · "
                    f"{formato_bs(f701['monto'])}", unsafe_allow_html=True)
    elif liq and _puede(sesion, "rau", "CREAR"):
        fecha = st.date_input("Fecha de presentación y pago", date.today(), format="DD/MM/YYYY", key="f701_fecha")
        with con_bd() as con:
            ev = rau.evaluar_701(con, gestion, fecha)
        st.markdown(f"Anticipación: **{ev['dias_habiles_antes']} días hábiles** (mínimo {ev['minimo']}; límite "
                    f"{fecha_pantalla(ev['limite'])}) {_semaforo(ev['semaforo'])}", unsafe_allow_html=True)
        with st.form("form_701"):
            orden = st.text_input("N.º de orden del formulario")
            comprobante = st.text_input("Comprobante de pago")
            if st.form_submit_button("Registrar presentación y pago", type="primary"):
                try:
                    with con_bd() as con:
                        r = rau.registrar_formulario_701(con, sesion, gestion, fecha, orden, comprobante)
                    (st.success if r["semaforo"] == "VERDE" else st.warning)(
                        f"Formulario registrado: semáforo {r['semaforo']} ({r['dias_habiles_antes']} días hábiles).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    else:
        st.caption("Registre la liquidación antes del Formulario 701.")

    if _puede(sesion, "rau", "CREAR"):
        with st.form("form_patente"):
            st.markdown("**Pago de patentes municipales** (mismo plazo del Formulario 701)")
            municipio = st.text_input("Municipio", "Caranavi")
            fecha = st.date_input("Fecha de pago", date.today(), format="DD/MM/YYYY", key="pat_f")
            monto = st.number_input("Monto (Bs)", min_value=0.0, step=1.0, format="%.2f")
            comprobante = st.text_input("Comprobante", key="pat_c")
            if st.form_submit_button("Registrar pago de patentes"):
                try:
                    with con_bd() as con:
                        r = rau.registrar_patente(con, sesion, gestion, municipio, fecha, decimal_de(monto), comprobante)
                    (st.success if r["dentro_plazo"] else st.warning)(
                        "Pago registrado " + ("dentro" if r["dentro_plazo"] else "FUERA") + " del plazo.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 3. SUPERFICIE Y CAPEX
# ==========================================================================
def _tab_superficie(sesion: Sesion) -> None:
    with con_bd() as con:
        sup = rau.estado_superficie(con, date.today()) if rau.gestiones(con) else None
        listas = rau.listar_superficies(con, sesion)
        controles = rau.listar_controles_superficie(con, sesion)
        pendientes = rau.lineas_capex_pendientes(con, sesion)
    if sup:
        st.markdown(f"{_semaforo(sup['semaforo'])} Superficie usada **{_ha(sup['usada'])} ha** frente a declarada "
                    f"**{_ha(sup['declarada'])} ha** — {sup['fuente_declarada']}", unsafe_allow_html=True)
        if not sup["constancia"]:
            st.warning("Superficie POR VALIDAR (8 vs 10 ha): registre la constancia de inscripción del SIN.")
    c1, c2 = st.columns(2)
    c1.markdown("**Superficie declarada**")
    c1.dataframe(tabla(listas["declaradas"], [("fecha", "Fecha"), ("hectareas", "ha"), ("zona", "Zona"),
                                              ("constancia_ref", "Constancia")], fechas=("fecha",)),
                 hide_index=True, use_container_width=True)
    c2.markdown("**Superficie usada por componente**")
    c2.dataframe(tabla(listas["usadas"], [("componente", "Componente"), ("hectareas", "ha"), ("desde", "Desde"),
                                          ("hasta", "Hasta")], fechas=("desde", "hasta")),
                 hide_index=True, use_container_width=True)
    if _puede(sesion, "rau", "CREAR"):
        c1, c2 = st.columns(2)
        with c1, st.form("form_sup_decl"):
            fecha = st.date_input("Fecha de la constancia", date.today(), format="DD/MM/YYYY")
            ha = st.number_input("Hectáreas declaradas", min_value=0.0, step=0.5, format="%.3f")
            zona = st.text_input("Zona / subzona", "Yungas - Caranavi")
            constancia = st.text_input("Constancia del SIN")
            if st.form_submit_button("Registrar superficie declarada"):
                try:
                    with con_bd() as con:
                        rau.registrar_superficie_declarada(con, sesion, fecha, decimal_de(ha), zona, constancia)
                    st.success("Superficie declarada registrada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with c2, st.form("form_sup_usada"):
            componente = st.text_input("Componente (galpones, planta, depósitos…)")
            ha = st.number_input("Hectáreas ocupadas", min_value=0.0, step=0.1, format="%.3f", key="su_ha")
            desde = st.date_input("Desde", date.today(), format="DD/MM/YYYY")
            if st.form_submit_button("Registrar superficie usada"):
                try:
                    with con_bd() as con:
                        rau.registrar_superficie_usada(con, sesion, componente, decimal_de(ha), desde)
                    st.success("Superficie usada registrada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        with st.form("form_capex_rau"):
            st.markdown("**Control de superficie ante una inversión (CAPEX)**")
            opciones = [None] + pendientes
            linea = st.selectbox("Línea CAPEX", opciones, format_func=lambda x: "— Sin línea (otra inversión) —"
                                 if x is None else f"{x['activo']} ({x['categoria']}, {fecha_pantalla(x['fecha_estimada'])})")
            ha = st.number_input("Hectáreas adicionales que ocupará", min_value=0.0, step=0.1, format="%.3f",
                                 key="cx_ha")
            descripcion = st.text_input("Descripción", value="")
            if st.form_submit_button("Evaluar y registrar", type="primary"):
                try:
                    with con_bd() as con:
                        r = rau.controlar_capex(con, sesion, date.today(), decimal_de(ha),
                                                descripcion or (linea["activo"] if linea else ""),
                                                linea["id"] if linea else None)
                    {"VERDE": st.success, "AMARILLO": st.warning, "ROJO": st.error}[r["semaforo"]](
                        f"{r['semaforo']}: {r['accion']}")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if pendientes:
        st.warning(f"{len(pendientes)} línea(s) CAPEX vigentes aún sin control de superficie.")
    st.markdown("**Controles registrados**")
    st.dataframe(tabla(controles, [("fecha", "Fecha"), ("descripcion", "Inversión"), ("adicional", "ha adicionales"),
                                   ("usadas", "ha usadas"), ("declaradas", "ha declaradas"), ("semaforo", "Semáforo"),
                                   ("accion", "Acción")], fechas=("fecha",)), hide_index=True, use_container_width=True)


# ==========================================================================
# 4. ALIMENTO: I-1, I-2, PROGRAMA DE EXCEDENTE Y BALANCE DE MASAS
# ==========================================================================
def _tab_alimento(sesion: Sesion) -> None:
    ejercicio = _gestion("al_ej", "Ejercicio")
    if ejercicio is None:
        return
    with con_bd() as con:
        ind = rau.indicadores_rau(con, sesion, ejercicio)
        programa = rau.programa_excedente(con, sesion, ejercicio)
        balances = rau.listar_balances(con, sesion, ejercicio)
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**I-1 Autoconsumo** (meta ≥ 65 %)<br>{_pct(ind['I1']['valor'])} {_semaforo(ind['I1']['semaforo'])}",
                unsafe_allow_html=True)
    c2.markdown(f"**I-2 Ventas de alimento / ingresos** (meta ≤ 15 %)<br>{_pct(ind['I2']['valor'])} "
                f"{_semaforo(ind['I2']['semaforo'])}", unsafe_allow_html=True)
    c3.markdown(f"**No socios con 3+ compras**<br>{ind['no_socios']['cantidad']} "
                f"{_semaforo(ind['no_socios']['semaforo'])}", unsafe_allow_html=True)
    with st.expander("Evaluar cifras proyectadas (I-1 e I-2)"):
        e1, e2 = st.columns(2)
        prod = e1.number_input("Producción anual (qq)", min_value=0.0, value=4693.0, step=1.0)
        auto = e1.number_input("Autoconsumo (qq)", min_value=0.0, value=3000.0, step=1.0)
        ventas_a = e2.number_input("Ventas de alimento (Bs)", min_value=0.0, value=0.0, step=100.0)
        ingresos = e2.number_input("Ingresos operativos (Bs)", min_value=0.0, value=0.0, step=100.0)
        try:
            with con_bd() as con:
                i1 = rau.evaluar_i1(con, decimal_de(prod), decimal_de(auto), ejercicio) if prod else None
                i2 = rau.evaluar_i2(con, decimal_de(ventas_a), decimal_de(ingresos), ejercicio) if ingresos else None
            if i1:
                st.markdown(f"I-1 = {_pct(i1['valor'])} {_semaforo(i1['semaforo'])}", unsafe_allow_html=True)
            if i2:
                st.markdown(f"I-2 = {_pct(i2['valor'])} {_semaforo(i2['semaforo'])}", unsafe_allow_html=True)
        except ErrorPollolandia as exc:
            mostrar_error(exc)

    st.subheader("Programa anual de excedente")
    if programa:
        canales = ", ".join(c for c, ok in (("socios", programa["canal_socios"]),
                                            ("no socios autorizados", programa["canal_no_socios"])) if ok)
        st.markdown(f"Tope **{_ha(programa['tope'])} qq** · canales: {canales} · {programa['acta_ca_ref']} · "
                    f"autorizado en balances {_ha(programa['autorizado'])} qq · vendido {_ha(programa['vendido'])} qq "
                    f"· saldo del tope {_ha(programa['saldo_tope'])} qq")
    else:
        st.warning("Sin programa de excedente aprobado: no se puede vender alimento (R6).")
        if _puede(sesion, "rau", "APROBAR"):
            with st.form("form_programa"):
                tope = st.number_input("Tope del ejercicio (qq)", min_value=0.0, step=10.0, format="%.3f")
                socios = st.checkbox("Canal socios", value=True)
                no_socios = st.checkbox("Canal no socios autorizados (productores locales)")
                acta = st.text_input("Acta del Consejo de Administración")
                if st.form_submit_button("Aprobar programa", type="primary"):
                    try:
                        with con_bd() as con:
                            rau.aprobar_programa_excedente(con, sesion, ejercicio, decimal_de(tope), canal_socios=socios,
                                                           canal_no_socios=no_socios, acta_ca_ref=acta,
                                                           fecha=date.today())
                        st.success("Programa aprobado e indexado (D03).")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)

    st.subheader("Balance de masas trimestral (FIN-TRA-13)")
    st.dataframe(tabla(balances, [("trimestre", "T"), ("producido", "Producción qq"), ("consumo", "Consumo qq"),
                                  ("merma", "Merma qq"), ("merma_semaforo", "Merma"), ("excedente", "Excedente qq"),
                                  ("consumo_proyectado", "Consumo proyectado"), ("autorizado", "Autorizado qq"),
                                  ("firmado_por", "Firmado por")]), hide_index=True, use_container_width=True)
    for b in balances:
        _pdf(f"FIN-TRA-13_{ejercicio}_T{b['trimestre']}", rep.fin_tra_13(b, _ctx(sesion)), f"pdf_bm_{b['id']}")
    hechos = {b["trimestre"] for b in balances}
    if _puede(sesion, "rau", "CREAR"):
        libres = [t for t in (1, 2, 3, 4) if t not in hechos]
        if libres:
            with st.form("form_balance"):
                t = st.selectbox("Trimestre", libres, format_func=lambda x: f"T{x} ({rau.NOMBRE_TRIMESTRE_EJERCICIO[x]})")
                merma = st.number_input("Merma del trimestre (qq)", min_value=0.0, step=1.0, format="%.3f")
                proyectado = st.number_input("Consumo proyectado del período siguiente (qq)", min_value=0.0, step=1.0,
                                             format="%.3f")
                if st.form_submit_button("Preparar balance", type="primary"):
                    try:
                        with con_bd() as con:
                            r = rau.preparar_balance_masas(con, sesion, ejercicio, t, merma_qq=decimal_de(merma),
                                                           consumo_proyectado_qq=decimal_de(proyectado))
                        st.success(f"Excedente {_ha(r['excedente'])} qq; autorizable {_ha(r['autorizable'])} qq.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    sin_firma = [b for b in balances if not b["firmado_por"]]
    if sin_firma and _puede(sesion, "rau", "AUTORIZAR"):
        with st.form("form_firma_balance"):
            b = st.selectbox("Balance", sin_firma, format_func=lambda x: f"T{x['trimestre']} — excedente "
                             f"{_ha(x['excedente'])} qq")
            autorizado = st.number_input("Excedente autorizado para la venta (qq)", min_value=0.0, step=1.0,
                                         format="%.3f")
            if st.form_submit_button("Firmar y autorizar", type="primary"):
                try:
                    with con_bd() as con:
                        rau.firmar_balance_masas(con, sesion, int(b["id"]), decimal_de(autorizado))
                    st.success("Balance firmado e indexado (D02).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 5. COMPRADORES Y SERVICIOS A TERCEROS
# ==========================================================================
def _tab_compradores(sesion: Sesion) -> None:
    ejercicio = _gestion("comp_ej", "Ejercicio")
    if ejercicio is None:
        return
    with con_bd() as con:
        padron = rau.padron_compradores(con, sesion, ejercicio)
        clientes = rau.clientes_alimento(con, sesion)
        autorizados = rau.listar_compradores_autorizados(con, sesion, ejercicio)
        servicios = rau.listar_servicios(con, sesion)
    st.subheader("Padrón de compradores de alimento")
    ns = padron["no_socios"]
    st.markdown(f"No socios con {ns['minimo_compras'] or 3}+ compras: **{ns['cantidad']}** {_semaforo(ns['semaforo'])}",
                unsafe_allow_html=True)
    st.dataframe(tabla(padron["compradores"], [("codigo", "Código"), ("nombre", "Comprador"), ("condicion", "Condición"),
                                               ("comunidad", "Comunidad"), ("compras", "Compras"), ("qq", "qq"),
                                               ("total", "Bs")], montos=("total",)),
                 hide_index=True, use_container_width=True)
    if _puede(sesion, "rau", "AUTORIZAR"):
        no_socios = [c for c in clientes if not c["es_socio"]]
        c1, c2 = st.columns(2)
        with c1, st.form("form_autorizar_comprador"):
            st.markdown("**Autorizar productor local no socio (R3)**")
            cli = st.selectbox("Cliente agricultor", no_socios, format_func=lambda c: f"{c['codigo']} — {c['nombre']}")
            comunidad = st.text_input("Comunidad")
            actividad = st.text_input("Actividad agropecuaria")
            if st.form_submit_button("Autorizar") and cli:
                try:
                    with con_bd() as con:
                        rau.autorizar_comprador(con, sesion, int(cli["id"]), ejercicio, comunidad=comunidad,
                                                actividad=actividad, fecha=date.today())
                    st.success("Comprador autorizado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        vigentes = [a for a in autorizados if a["estado"] == "VIGENTE"]
        with c2, st.form("form_revocar"):
            st.markdown("**Revocar autorización**")
            a = st.selectbox("Autorización", vigentes, format_func=lambda x: x["nombre"])
            motivo = st.text_input("Motivo")
            if st.form_submit_button("Revocar") and a:
                try:
                    with con_bd() as con:
                        rau.revocar_comprador(con, sesion, int(a["id"]), motivo)
                    st.success("Autorización revocada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)

    st.subheader("Servicios a terceros (alerta roja con cualquier caso)")
    if not servicios:
        st.success("Sin servicios a terceros registrados (meta: 0 casos).")
    for sv in servicios:
        st.error(f"Caso N.º {sv['id']} · {fecha_pantalla(sv['fecha'])} · {sv['tipo_txt']} para {sv['tercero']} · "
                 f"estado {sv['estado'].lower()} · GG hasta {fecha_pantalla(sv['limite_gg'])} · CA hasta "
                 f"{fecha_pantalla(sv['limite_ca'])}" + (f" · medida: {sv['medida']}" if sv["medida"] else ""))
    if any(_puede(sesion, "rau", a) for a in ("CREAR", "VERIFICAR", "AUTORIZAR")):
        with st.form("form_servicio"):
            st.markdown("**Registrar servicio a terceros detectado**")
            fecha = st.date_input("Fecha", date.today(), format="DD/MM/YYYY", key="sv_f")
            tipo = st.selectbox("Tipo", list(rau.TIPOS_SERVICIO), format_func=lambda t: rau.TIPOS_SERVICIO[t])
            tercero = st.text_input("Tercero")
            descripcion = st.text_area("Descripción")
            if st.form_submit_button("Registrar (alerta roja)"):
                try:
                    with con_bd() as con:
                        r = rau.registrar_servicio_tercero(con, sesion, fecha, tipo, tercero, descripcion)
                    st.error(r["mensaje"])
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    abiertos = [sv for sv in servicios if sv["estado"] == "ABIERTO"]
    if abiertos and _puede(sesion, "rau", "AUTORIZAR"):
        with st.form("form_informar"):
            sv = st.selectbox("Caso", abiertos, format_func=lambda x: f"N.º {x['id']} — {x['tercero']}")
            if st.form_submit_button("Tomar conocimiento (Gerencia General)"):
                try:
                    with con_bd() as con:
                        rau.informar_servicio_gg(con, sesion, int(sv["id"]), date.today())
                    st.success("Caso informado a la Gerencia General.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    informados = [sv for sv in servicios if sv["estado"] == "INFORMADO"]
    if informados and _puede(sesion, "rau", "APROBAR"):
        with st.form("form_resolver"):
            sv = st.selectbox("Caso", informados, format_func=lambda x: f"N.º {x['id']} — {x['tercero']}")
            acta = st.text_input("Acta del Consejo de Administración")
            medida = st.text_area("Medida de contención")
            if st.form_submit_button("Resolver", type="primary"):
                try:
                    with con_bd() as con:
                        rau.resolver_servicio_ca(con, sesion, int(sv["id"]), date.today(), acta, medida)
                    st.success("Caso cerrado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 6. REVISIÓN DEL CONSEJO DE VIGILANCIA (INF-CV-09)
# ==========================================================================
def _tab_revision(sesion: Sesion) -> None:
    hoy = date.today()
    c1, c2, c3, c4 = st.columns(4)
    anio = int(c1.number_input("Año", min_value=2026, max_value=2031, value=max(2026, min(hoy.year, 2031)), step=1))
    trimestre = int(c2.selectbox("Trimestre calendario", [1, 2, 3, 4], index=max((hoy.month - 1) // 3 - 1, 0)))
    pct = c3.number_input("% compras sin respaldo (muestra)", min_value=0.0, max_value=100.0, step=0.5)
    atraso = int(c4.selectbox("Atraso del padrón/actas (trimestres)", [0, 1, 2]))
    try:
        with con_bd() as con:
            verif = rau.verificaciones_trimestrales(con, anio, trimestre, compras_sin_respaldo_pct=decimal_de(pct) / 100,
                                                    padron_atraso_trimestres=atraso)
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        verif = []
    for v in verif:
        st.markdown(f"{_semaforo(v['semaforo'])} **{v['numero']}. {v['verificacion']}** — {v['resultado']} "
                    f"<span style='color:#5d6a62'>(meta: {v['meta']}; fuente: {v['fuente']})</span>",
                    unsafe_allow_html=True)
    if verif:
        st.markdown(f"Semáforo general: {_semaforo(rau.peor(v['semaforo'] for v in verif))}", unsafe_allow_html=True)
    if verif and _puede(sesion, "rau", "VERIFICAR"):
        with st.form("form_rev_trim"):
            obs = st.text_area("Observaciones del Consejo de Vigilancia")
            if st.form_submit_button("Registrar revisión trimestral (INF-CV-09)", type="primary"):
                try:
                    with con_bd() as con:
                        r = rau.registrar_revision_trimestral(
                            con, sesion, anio, trimestre, hoy, compras_sin_respaldo_pct=decimal_de(pct) / 100,
                            padron_atraso_trimestres=atraso, observaciones=obs or None)
                    msg = f"Revisión registrada: semáforo {r['semaforo']}."
                    if r["escalamiento"]:
                        st.error(msg + f" Escalar a la GG hasta {fecha_pantalla(r['escalamiento']['gg_hasta'])} y al CA "
                                       f"hasta {fecha_pantalla(r['escalamiento']['ca_hasta'])}.")
                    else:
                        st.success(msg)
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    st.subheader("Verificación anual (INF-CV-09)")
    gestion = _gestion("rev_gestion")
    if gestion is not None:
        with con_bd() as con:
            anual = rau.verificaciones_anuales(con, gestion)
        for v in anual:
            st.markdown(f"{chip('VERDE' if v['cumple'] else 'ROJO')} {v['verificacion']} — {v['observacion']}",
                        unsafe_allow_html=True)
        if _puede(sesion, "rau", "VERIFICAR"):
            if st.button("Registrar INF-CV-09 anual", key="btn_anual"):
                try:
                    with con_bd() as con:
                        r = rau.registrar_informe_anual(con, sesion, gestion, hoy)
                    st.success(f"Informe anual registrado: semáforo {r['semaforo']}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    with con_bd() as con:
        revisiones = rau.listar_revisiones(con, sesion)
    if revisiones:
        st.subheader("Revisiones registradas")
        st.dataframe(tabla([{**r, "periodo": f"T{r['trimestre']}/{r['anio']}" if r["tipo"] == "TRIMESTRAL"
                             else f"Gestión {r['anio']}"} for r in revisiones],
                           [("tipo", "Tipo"), ("periodo", "Período"), ("fecha", "Fecha"), ("semaforo", "Semáforo"),
                            ("elaborado_por", "Elaborado por")], fechas=("fecha",)),
                     hide_index=True, use_container_width=True)
        for r in revisiones:
            datos = rep.inf_cv_09_trimestral(r, _ctx(sesion)) if r["tipo"] == "TRIMESTRAL" else \
                rep.inf_cv_09_anual(r, _ctx(sesion))
            _pdf(f"INF-CV-09_{r['tipo']}_{r['anio']}_{r['trimestre']}", datos, f"pdf_rev_{r['id']}")


# ==========================================================================
# 7. EXPEDIENTE EXP-RAU
# ==========================================================================
def _tab_expediente(sesion: Sesion) -> None:
    gestion = _gestion("exp_gestion")
    if gestion is None:
        return
    with con_bd() as con:
        idx = rau.indice_maestro(con, sesion, gestion)
    st.progress(float(idx["completitud"]), text=f"Completitud {_pct(idx['completitud'])} "
                                                f"({idx['cubiertos']} de {idx['total']}; meta {_pct(idx['minimo'])})")
    _pdf(f"EXP-RAU_{gestion}", rep.indice_maestro(idx, _ctx(sesion)), "pdf_exp")
    for c in idx["carpetas"]:
        with st.expander(f"Carpeta {c['carpeta']} — {_pct(c['completitud'])}"):
            st.dataframe(tabla([{**i, "estado": "Archivado" if i["cubierto"] else "PENDIENTE",
                                 "refs": "; ".join(d["referencia"] for d in i["documentos"])} for i in c["items"]],
                               [("clave", "Clave"), ("documento", "Documento"), ("responsable", "Responsable"),
                                ("periodicidad", "Periodicidad"), ("estado", "Estado"), ("refs", "Referencias")]),
                         hide_index=True, use_container_width=True)
    if _puede(sesion, "rau", "CREAR"):
        claves = [(i["clave"], i["documento"]) for c in idx["carpetas"] for i in c["items"]]
        with st.form("form_exp_doc"):
            st.markdown("**Indexar documento** (nombre digital AAAA-MM-DD_Carpeta_Documento)")
            clave = st.selectbox("Documento", claves, format_func=lambda x: f"{x[0]} — {x[1]}")
            fecha = st.date_input("Fecha del documento", date.today(), format="DD/MM/YYYY")
            referencia = st.text_input("Referencia")
            ubicacion = st.text_input("Ubicación física")
            if st.form_submit_button("Indexar"):
                try:
                    with con_bd() as con:
                        rau.registrar_documento_expediente(con, sesion, clave[0], gestion, fecha, referencia,
                                                           ubicacion or None)
                    st.success("Documento indexado.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 8. FISCALIZACIÓN
# ==========================================================================
def _tab_fiscalizacion(sesion: Sesion) -> None:
    with con_bd() as con:
        notificaciones = rau.listar_notificaciones(con, sesion)
    with st.expander("Escenarios de riesgo y respuesta en 72 horas (PET-RAU §4.3)"):
        for codigo, (senal, respuesta, argumento) in rau.ESCENARIOS.items():
            st.markdown(f"**{codigo}. {senal}** — {respuesta} *Argumento:* {argumento}")
    st.dataframe(tabla(notificaciones, [("id", "N.º"), ("fecha_recepcion", "Recepción"), ("entidad", "Entidad"),
                                        ("tipo", "Tipo"), ("numero", "Número"), ("gestiones", "Gestiones"),
                                        ("vencimiento", "Vencimiento"), ("estado", "Estado")],
                       fechas=("fecha_recepcion", "vencimiento")), hide_index=True, use_container_width=True)
    puede_registrar = _puede(sesion, "rau", "CREAR") or _puede(sesion, "rau", "AUTORIZAR")
    if puede_registrar:
        with st.form("form_notif"):
            st.markdown("**Registrar notificación recibida** (sello y fecha de recepción)")
            c1, c2 = st.columns(2)
            recepcion = c1.date_input("Fecha de recepción", date.today(), format="DD/MM/YYYY")
            entidad = c1.selectbox("Entidad", list(rau.ENTIDADES), format_func=lambda e: rau.ENTIDADES[e])
            tipo = c1.selectbox("Tipo", list(rau.TIPOS_NOTIFICACION), format_func=lambda t: rau.TIPOS_NOTIFICACION[t])
            numero = c2.text_input("Número de orden o requerimiento")
            funcionario = c2.text_input("Funcionario actuante")
            gestiones_txt = c2.text_input("Gestiones alcanzadas (separadas por coma)", str(date.today().year - 1))
            vencimiento = c1.date_input("Vencimiento del plazo", date.today(), format="DD/MM/YYYY")
            escenario = c2.selectbox("Escenario", [None, *rau.ESCENARIOS], format_func=lambda e: "—" if e is None else e)
            if st.form_submit_button("Registrar y abrir carpeta del caso", type="primary"):
                try:
                    lista = [int(g) for g in gestiones_txt.replace(" ", "").split(",") if g]
                    with con_bd() as con:
                        plan = rau.registrar_notificacion(con, sesion, fecha_recepcion=recepcion, entidad=entidad,
                                                          tipo=tipo, numero=numero, funcionario=funcionario,
                                                          gestiones=lista, vencimiento=vencimiento, escenario=escenario)
                    st.success(f"Caso N.º {plan['notificacion']['id']} abierto.")
                    for a in plan["advertencias"]:
                        st.warning(a)
                except ValueError:
                    st.error("Las gestiones deben ser años separados por coma.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
    if not notificaciones:
        st.info("Sin notificaciones registradas.")
    else:
        n = st.selectbox("Caso", notificaciones, format_func=lambda x: f"N.º {x['id']} — {x['numero']} ({x['estado']})")
        with con_bd() as con:
            plan = rau.plan_respuesta(con, sesion, int(n["id"]))
        for p in plan["pasos"]:
            estado = {"CUMPLIDO": "VERDE", "PENDIENTE": "AMARILLO", "VENCIDO": "ROJO"}.get(p["estado"], "AMARILLO")
            st.markdown(f"{chip(estado)} **{p['descripcion']}** — límite {fecha_pantalla(p['limite'])} · "
                        f"{ESTADO_TXT.get(p['estado'], p['estado'])}", unsafe_allow_html=True)
        if plan["escenario"]:
            st.info(f"Escenario {plan['escenario']['codigo']}: {plan['escenario']['respuesta_72h']}")
        st.markdown("**Carpeta de Respuesta Rápida**")
        st.dataframe(tabla([{**c, "estado": "Disponible" if c["disponible"] else "FALTA"}
                            for c in plan["carpeta_respuesta_rapida"]],
                           [("documento", "Documento"), ("estado", "Estado")]), hide_index=True, use_container_width=True)
        st.dataframe(tabla(plan["acciones"], [("fecha", "Fecha"), ("paso", "Paso"), ("detalle", "Detalle"),
                                              ("documento_ref", "Documento")], fechas=("fecha",)),
                     hide_index=True, use_container_width=True)
        if n["estado"] != "CERRADA" and (puede_registrar or _puede(sesion, "rau", "APROBAR")):
            with st.form("form_accion"):
                paso = st.selectbox("Paso", [p for p in rau.PASOS if p != "CIERRE"], format_func=lambda p: rau.PASOS[p])
                fecha = st.date_input("Fecha", date.today(), format="DD/MM/YYYY", key="acc_f")
                detalle = st.text_area("Detalle")
                documento = st.text_input("Documento / acta / cargo de entrega")
                nuevo = st.date_input("Nuevo vencimiento (solo prórroga)", date.today(), format="DD/MM/YYYY")
                if st.form_submit_button("Registrar paso"):
                    try:
                        with con_bd() as con:
                            rau.registrar_accion_fiscalizacion(con, sesion, int(n["id"]), paso, fecha, detalle,
                                                               documento or None, nuevo if paso == "PRORROGA" else None)
                        st.success("Paso registrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        if n["estado"] == "RESPONDIDA" and (_puede(sesion, "rau", "VERIFICAR") or _puede(sesion, "rau", "AUTORIZAR")):
            with st.form("form_cierre_caso"):
                lecciones = st.text_area("Lecciones aprendidas")
                if st.form_submit_button("Cerrar caso"):
                    try:
                        with con_bd() as con:
                            rau.cerrar_notificacion(con, sesion, int(n["id"]), date.today(), lecciones)
                        st.success("Caso cerrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if _puede(sesion, "rau", "CREAR"):
        with st.form("form_buzon"):
            st.markdown("**Revisión semanal del buzón y notificaciones del SIN**")
            fecha = st.date_input("Fecha de revisión", date.today(), format="DD/MM/YYYY", key="buz_f")
            novedades = st.checkbox("Hubo novedades")
            detalle = st.text_input("Detalle de las novedades")
            if st.form_submit_button("Registrar revisión"):
                try:
                    with con_bd() as con:
                        rau.registrar_revision_buzon(con, sesion, fecha, novedades, detalle or None)
                    st.success("Revisión del buzón registrada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# 9. ARCHIVO CONTABLE
# ==========================================================================
def _tab_archivo(sesion: Sesion) -> None:
    if not _puede(sesion, "archivo", "VER"):
        st.info("Su rol no tiene acceso al archivo contable.")
        return
    with con_bd() as con:
        resumen = archivo.resumen_archivo(con, sesion)
        expedientes = archivo.listar_expedientes(con, sesion)
        actas = archivo.listar_actas(con, sesion)
        prestamos = archivo.libro_prestamos(con, sesion)
    c1, c2, c3 = st.columns(3)
    c1.metric("Expedientes", sum(resumen["por_estado"].values()))
    c2.metric("Vencidos (destruibles)", resumen["vencidos"])
    c3.metric("Préstamos atrasados", len(resumen["prestamos_atrasados"]))
    with st.expander("Tabla de retención documental (MAN-ARC-01 §3)"):
        ciclo = st.selectbox("Ciclo", [None, *archivo.CICLOS], format_func=lambda c: "Todos" if c is None else
                             f"{c} — {archivo.CICLOS[c]}", key="ret_ciclo")
        with con_bd() as con:
            ret = archivo.tabla_retencion(con, sesion, ciclo)
        st.dataframe(tabla(ret, [("codigo", "Código"), ("documento", "Documento"), ("procedimiento", "Proc."),
                                 ("clase_txt", "Plazo"), ("computo_txt", "Cómputo"), ("disposicion", "Disposición")]),
                     hide_index=True, use_container_width=True)
    st.subheader("Expedientes")
    st.dataframe(tabla(expedientes, [("codigo", "Código"), ("titulo", "Título"), ("ruta", "Ruta"),
                                     ("clase_txt", "Retención"), ("folios", "Folios"), ("caja", "Caja"),
                                     ("vencimiento", "Vence"), ("estado", "Estado"), ("bloqueo_legal", "Bloqueo")],
                       fechas=("vencimiento",)), hide_index=True, use_container_width=True)
    puede_crear = _puede(sesion, "archivo", "CREAR")
    if puede_crear:
        with con_bd() as con:
            gestiones = rau.gestiones(con)
            codigos = archivo.tabla_retencion(con, sesion)
        with st.form("form_abrir_exp"):
            st.markdown("**Abrir expediente** (código EXP-[CICLO]-[G2627]-[N.º])")
            doc = st.selectbox("Documento de la tabla de retención", codigos,
                               format_func=lambda r: f"{r['codigo']} — {r['documento']} ({r['clase_txt']})")
            gestion = st.selectbox("Gestión", gestiones, format_func=lambda g: archivo.codigo_gestion(g))
            titulo = st.text_input("Título del expediente")
            if st.form_submit_button("Abrir", type="primary") and doc:
                try:
                    with con_bd() as con:
                        r = archivo.abrir_expediente(con, sesion, ciclo=doc["ciclo"], gestion=int(gestion),
                                                     titulo=titulo or doc["documento"],
                                                     procedimiento=doc["procedimiento"],
                                                     codigo_documental=doc["codigo"])
                    st.success(f"Expediente {r['codigo']} abierto en {r['ruta']}.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        abiertos = [e for e in expedientes if e["estado"] == "ABIERTO"]
        if abiertos:
            with st.form("form_doc_arch"):
                st.markdown("**Agregar documento** (foliación continua, tomos de 200 folios)")
                e = st.selectbox("Expediente", abiertos, format_func=lambda x: f"{x['codigo']} — {x['titulo']}")
                c1, c2 = st.columns(2)
                formato = c1.text_input("Código del formato", e["codigo_documental"] if e else "")
                fecha = c1.date_input("Fecha", date.today(), format="DD/MM/YYYY", key="docarch_f")
                folios = int(c2.number_input("Folios", min_value=1, max_value=200, value=1, step=1))
                estado = c2.selectbox("Estado", list(archivo.ESTADOS_DOCUMENTO), index=2,
                                      format_func=lambda x: archivo.ESTADOS_DOCUMENTO[x])
                descripcion = st.text_input("Descripción")
                if st.form_submit_button("Agregar documento"):
                    try:
                        with con_bd() as con:
                            r = archivo.registrar_documento(con, sesion, int(e["id"]), codigo_formato=formato,
                                                            fecha=fecha, descripcion=descripcion, folios=folios,
                                                            estado_documento=estado)
                        st.success(f"{r['nombre_digital']} · folios {r['folio_desde']}-{r['folio_hasta']} · "
                                   f"tomo {r['tomo']}")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
    if abiertos_cerrar := [e for e in expedientes if e["estado"] == "ABIERTO"]:
        if _puede(sesion, "archivo", "EDITAR"):
            with st.form("form_cerrar_exp"):
                st.markdown("**Cerrar expediente**")
                e = st.selectbox("Expediente", abiertos_cerrar, format_func=lambda x: x["codigo"], key="cerr_e")
                fin = st.date_input("Fecha de cierre", date.today(), format="DD/MM/YYYY")
                usar_base = st.checkbox("Indicar fecha base (sustitución, vigencia o baja del bien)")
                base = st.date_input("Fecha base", date.today(), format="DD/MM/YYYY")
                caja = st.text_input("Caja (CAJ-AAAA-NNN)")
                if st.form_submit_button("Cerrar"):
                    try:
                        with con_bd() as con:
                            r = archivo.cerrar_expediente(con, sesion, int(e["id"]), fin,
                                                          fecha_base=base if usar_base else None, caja=caja or None)
                        st.success("Expediente cerrado; vence " + (fecha_pantalla(r["vencimiento"]) if r["vencimiento"]
                                                                   else "nunca (permanente)") + ".")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)

    st.subheader("Libro de préstamo")
    st.dataframe(tabla(prestamos, [("codigo", "Expediente"), ("solicitante", "Solicitante"), ("motivo", "Motivo"),
                                   ("fecha_salida", "Salida"), ("devolucion_prevista", "Devolución prevista"),
                                   ("autorizado_por", "Autorizó"), ("fecha_devolucion", "Devuelto"),
                                   ("estado", "Estado")], fechas=("fecha_salida", "devolucion_prevista",
                                                                  "fecha_devolucion")),
                 hide_index=True, use_container_width=True)
    if puede_crear or _puede(sesion, "archivo", "AUTORIZAR"):
        disponibles = [e for e in expedientes if e["estado"] in ("ABIERTO", "CERRADO") and not e["prestado"]]
        if disponibles:
            with st.form("form_prestamo"):
                e = st.selectbox("Expediente", disponibles, format_func=lambda x: x["codigo"], key="prest_e")
                solicitante = st.text_input("Solicitante")
                cargo = st.text_input("Cargo")
                motivo = st.text_input("Motivo")
                original = st.checkbox("Préstamo de originales (requiere a la Gerencia General)")
                prevista = st.date_input("Devolución prevista", date.today(), format="DD/MM/YYYY")
                if st.form_submit_button("Registrar préstamo"):
                    try:
                        with con_bd() as con:
                            archivo.prestar_expediente(con, sesion, int(e["id"]), solicitante=solicitante, cargo=cargo,
                                                       motivo=motivo, original=original, fecha_salida=date.today(),
                                                       devolucion_prevista=prevista)
                        st.success("Préstamo registrado.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        pendientes = [p for p in prestamos if p["estado"] == "PRESTADO"]
        if pendientes and puede_crear:
            with st.form("form_devolucion"):
                p = st.selectbox("Préstamo", pendientes, format_func=lambda x: f"{x['codigo']} — {x['solicitante']}")
                if st.form_submit_button("Registrar devolución"):
                    try:
                        with con_bd() as con:
                            r = archivo.devolver_expediente(con, sesion, int(p["id"]), date.today())
                        st.success(f"Devuelto con {r['dias_atraso']} día(s) de atraso.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)

    st.subheader("Actas de transferencia y destrucción")
    for a in actas:
        st.markdown(f"**{a['tipo_txt']} N.º {a['numero_txt']}** · {fecha_pantalla(a['fecha'])} · {a['metodo']} · "
                    f"estado {a['estado'].lower()} · {len(a['expedientes'])} expediente(s)")
        _pdf(f"ACTA_{a['tipo']}_{a['numero']}_{a['anio']}", rep.acta_archivo(a, _ctx(sesion)), f"pdf_acta_{a['id']}")
    if puede_crear:
        tipo = st.selectbox("Tipo de acta", list(archivo.TIPOS_ACTA), format_func=lambda t: archivo.TIPOS_ACTA[t])
        with con_bd() as con:
            elegibles = archivo.expedientes_elegibles(con, sesion, tipo)
        if elegibles:
            with st.form("form_acta"):
                sel = st.multiselect("Expedientes elegibles", elegibles, format_func=lambda x: f"{x['codigo']} — "
                                     f"{x['titulo']}")
                metodo = st.text_input("Método (destino del traslado o forma de destrucción)")
                obs = st.text_input("Observación")
                if st.form_submit_button("Preparar acta"):
                    try:
                        with con_bd() as con:
                            r = archivo.preparar_acta(con, sesion, tipo, date.today(), metodo, [int(x["id"]) for x in sel],
                                                      obs or None)
                        st.success(f"Acta N.º {r['numero']} preparada.")
                    except ErrorPollolandia as exc:
                        mostrar_error(exc)
        else:
            st.caption("Sin expedientes elegibles para ese tipo de acta.")
    for a in actas:
        if a["estado"] == "PREPARADA" and _puede(sesion, "archivo", "AUTORIZAR"):
            if st.button(f"Autorizar acta {a['numero_txt']} ({a['tipo'].lower()})", key=f"aut_acta_{a['id']}"):
                try:
                    with con_bd() as con:
                        archivo.autorizar_acta(con, sesion, int(a["id"]))
                    st.success("Acta autorizada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        if a["estado"] == "AUTORIZADA" and a["tipo"] == "DESTRUCCION" and _puede(sesion, "archivo", "VERIFICAR"):
            if st.button(f"Visar acta {a['numero_txt']}", key=f"vis_acta_{a['id']}"):
                try:
                    with con_bd() as con:
                        archivo.visar_acta(con, sesion, int(a["id"]))
                    st.success("Acta visada.")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)
        listo = (a["estado"] == "VISADA") or (a["estado"] == "AUTORIZADA" and a["tipo"] != "DESTRUCCION")
        if listo and _puede(sesion, "archivo", "EDITAR"):
            if st.button(f"Ejecutar acta {a['numero_txt']}", key=f"eje_acta_{a['id']}"):
                try:
                    with con_bd() as con:
                        r = archivo.ejecutar_acta(con, sesion, int(a["id"]), date.today())
                    st.success(f"{r['expedientes']} expediente(s) {r['estado'].lower()}(s).")
                except ErrorPollolandia as exc:
                    mostrar_error(exc)


# ==========================================================================
# PÁGINA
# ==========================================================================
def pagina(sesion: Sesion) -> None:
    """Pantalla del Módulo 9 — RAU, cumplimiento legal y archivo contable."""
    st.header("RAU, cumplimiento legal y archivo · Transversal")
    st.caption("Advertencia: la cuota por hectárea (Bs 3,03) y la superficie (8 ha frente a 10 ha del balance de "
               "apertura) están POR VALIDAR con el SIN y la constancia de inscripción.")
    _alertas(sesion)
    pestañas = st.tabs(["Calendario fiscal", "Cuota y Form. 701", "Superficie y CAPEX", "Alimento I-1/I-2",
                        "Compradores y terceros", "Revisión CV", "Expediente EXP-RAU", "Fiscalización",
                        "Archivo contable"])
    funciones = (_tab_calendario, _tab_cuota, _tab_superficie, _tab_alimento, _tab_compradores, _tab_revision,
                 _tab_expediente, _tab_fiscalizacion, _tab_archivo)
    for pestaña, funcion in zip(pestañas, funciones):
        with pestaña:
            try:
                funcion(sesion)
            except ErrorPollolandia as exc:
                mostrar_error(exc)
