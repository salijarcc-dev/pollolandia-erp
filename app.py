"""ERP Pollolandia - punto de entrada Streamlit (versión definitiva, Fase 13).

* Prepara la base de datos al arrancar (migraciones y semillas idempotentes) y
  ejecuta una vez por día el respaldo automático 3-2-1 (MAN-ARC-01 §5.2).
* Configuración inicial: si no hay usuarios, crea el de la Gerencia General.
* Login con bcrypt, bloqueo por intentos y caducidad de contraseña.
* Control de sesión en ``st.session_state`` con cierre por inactividad.
* Menú lateral filtrado por los permisos efectivos del rol: los 12 módulos del
  Master Plan (Tesorería a Tablero) más contabilidad, control interno y
  administración (usuarios, parámetros, respaldos, seguridad y bitácora).

Se ejecuta con ``streamlit run app.py`` y se usa desde el navegador.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

import streamlit as st

import config
from db.conexion import verificar_conexion
from db.migraciones import inicializar
from modulos import administracion, libros, sistema
from modulos.comun import ESTILO, con_bd, mostrar_error
from servicios import respaldo, seguridad
from servicios.excepciones import ErrorPollolandia
from servicios.seguridad import Sesion

# El Master Plan nombra los módulos con prefijo numérico (01_tesoreria.py…),
# que no es un identificador válido para ``import``: se cargan por nombre.
tesoreria_ui = importlib.import_module("modulos.01_tesoreria")
compras_ui = importlib.import_module("modulos.02_compras")
planillas_ui = importlib.import_module("modulos.03_planillas")
produccion_ui = importlib.import_module("modulos.04_produccion")
costos_ui = importlib.import_module("modulos.05_costos")
activos_ui = importlib.import_module("modulos.06_activos")
ventas_ui = importlib.import_module("modulos.07_ventas")
contabilidad_ui = importlib.import_module("modulos.08_contabilidad")
rau_ui = importlib.import_module("modulos.09_rau")
presupuesto_ui = importlib.import_module("modulos.10_presupuesto")
vigilancia_ui = importlib.import_module("modulos.11_vigilancia")
dashboard_ui = importlib.import_module("modulos.12_dashboard")

config.configurar_logging()
st.set_page_config(page_title=config.NOMBRE_SISTEMA, page_icon="🐔", layout="wide")
st.markdown(ESTILO, unsafe_allow_html=True)


@dataclass(frozen=True)
class Pagina:
    """Entrada del menú: visible si el rol tiene ``accion`` sobre ``recurso``."""

    clave: str
    titulo: str
    grupo: str
    recurso: str | None
    accion: str | None
    mostrar: Callable[[Sesion], object]


PAGINAS: tuple[Pagina, ...] = (
    Pagina("inicio", "Inicio", "General", None, None, sistema.pagina_inicio),
    Pagina("diario", "Libro diario", "Contabilidad", "contabilidad", "VER", libros.pagina_diario),
    Pagina("mayor", "Libro mayor", "Contabilidad", "contabilidad", "VER", libros.pagina_mayor),
    Pagina("balance", "Balance de comprobación", "Contabilidad", "contabilidad", "VER", libros.pagina_balance),
    Pagina("registrar", "Registrar asiento", "Contabilidad", "contabilidad", "CREAR", libros.pagina_registrar),
    Pagina("reversar", "Reversar asiento", "Contabilidad", "contabilidad", "REVERSAR", libros.pagina_reversar),
    Pagina("periodos", "Períodos contables", "Contabilidad", "contabilidad", "VER", libros.pagina_periodos),
    Pagina("tesoreria", "Tesorería", "Ciclo 1", "tesoreria", "VER", tesoreria_ui.pagina),
    Pagina("compras", "Compras e inventarios", "Ciclo 2", "compras", "VER", compras_ui.pagina),
    Pagina("planillas", "Personal y planillas", "Ciclo 2", "planillas", "VER", planillas_ui.pagina),
    Pagina("produccion", "Producción y trazabilidad", "Ciclo 3", "produccion", "VER", produccion_ui.pagina),
    Pagina("costos", "Costos matriciales", "Ciclo 3", "costos", "VER", costos_ui.pagina),
    Pagina("activos", "Activos fijos y CAPEX", "Ciclo 1 y 3", "activos", "VER", activos_ui.pagina),
    Pagina("ventas", "Ventas, HORECA y cobranza", "Ciclo 4", "ventas", "VER", ventas_ui.pagina),
    Pagina("contabilidad", "Cierres y estados financieros", "Ciclo 5", "contabilidad", "VER", contabilidad_ui.pagina),
    Pagina("rau", "RAU, cumplimiento y archivo", "Transversal", "rau", "VER", rau_ui.pagina),
    Pagina("presupuesto", "Presupuesto y POA", "Información gerencial", "presupuesto", "VER", presupuesto_ui.pagina),
    Pagina("vigilancia", "Consejo de Vigilancia", "Control interno", "vigilancia", "VER", vigilancia_ui.pagina),
    Pagina("dashboard", "Tablero de indicadores", "Información gerencial", "dashboard", "VER", dashboard_ui.pagina),
    Pagina("autorizaciones", "Autorizaciones", "Control interno", "tesoreria", "VER", sistema.pagina_autorizaciones),
    Pagina("usuarios", "Usuarios", "Administración", "usuarios", "VER", sistema.pagina_usuarios),
    Pagina("parametros", "Parámetros normativos", "Administración", "parametros", "VER",
           administracion.pagina_parametros),
    Pagina("respaldos", "Respaldos 3-2-1", "Administración", "respaldos", "VER", administracion.pagina_respaldos),
    Pagina("seguridad", "Revisión de seguridad", "Administración", "auditoria", "VER", administracion.pagina_seguridad),
    Pagina("bitacora", "Bitácora de auditoría", "Administración", "auditoria", "VER", sistema.pagina_bitacora),
    Pagina("password", "Cambiar contraseña", "Mi cuenta", None, None, sistema.pagina_password),
)

MODULOS_FUTUROS: tuple[str, ...] = ()


@st.cache_resource(show_spinner="Preparando la base de datos…")
def preparar_bd(ruta: str) -> dict:
    """Aplica migraciones y semillas pendientes una vez por proceso y ruta."""
    return inicializar(ruta)


@st.cache_resource(show_spinner="Respaldo automático del día…")
def respaldo_del_dia(ruta: str, dia: str) -> dict:
    """Respaldo automático 3-2-1 una vez por proceso, ruta y día. Un fallo no
    impide usar el sistema: se informa en «Administración · Respaldos»."""
    try:
        with con_bd() as con:
            return respaldo.ejecutar_automatico(con, dia)
    except (ErrorPollolandia, OSError) as exc:
        return {"fecha": dia, "respaldos": [], "errores": [{"tipo": "AUTOMATICO", "referencia": None,
                                                             "error": str(exc)}], "depurados": []}


def encabezado() -> None:
    st.title(f"🐔 {config.NOMBRE_SISTEMA}")
    st.caption(f"{config.NOMBRE_ENTIDAD} · {config.UBICACION}")


def pantalla_configuracion_inicial() -> bool:
    """Formulario del primer usuario (GG). Devuelve ``True`` cuando se creó."""
    st.subheader("Configuración inicial")
    st.write("La base de datos está lista y todavía no tiene usuarios. Cree el usuario de la Gerencia General; "
             "después, la Gerencia dará de alta al resto del personal y de los Consejos.")
    with con_bd() as con:
        socios = seguridad.listar_socios(con)
    gerente = next((s for s in socios if (s["cargo_cooperativo"] or "").startswith("Gerente")), socios[0])
    with st.form("form_inicial"):
        socio = st.selectbox("Socio que ejerce la Gerencia General", socios, index=socios.index(gerente),
                             format_func=lambda s: f"N.º {s['numero']} · {s['nombre_completo']}")
        usuario = st.text_input("Usuario", placeholder="por ejemplo, rflores")
        clave = st.text_input("Contraseña", type="password", help="Mínimo 8 caracteres, con letras y números.")
        repetida = st.text_input("Repita la contraseña", type="password")
        enviado = st.form_submit_button("Crear usuario de la Gerencia General", type="primary")
    if enviado:
        if clave != repetida:
            st.error("Las contraseñas no coinciden.")
            return False
        try:
            with con_bd() as con:
                seguridad.crear_usuario_inicial(con, usuario, socio["nombre_completo"], clave, socio["id"])
            st.session_state["aviso_ok"] = "Usuario de la Gerencia General creado. Ya puede ingresar."
            return True
        except ErrorPollolandia as exc:
            mostrar_error(exc)
    return False


def pantalla_login() -> Sesion | None:
    """Formulario de ingreso. Devuelve la sesión cuando las credenciales son válidas."""
    estado = verificar_conexion()
    if estado["ok"]:
        st.success("Conexión a SQLite correcta.")
    for clave, mostrar in (("aviso", st.warning), ("aviso_ok", st.info)):
        texto = st.session_state.pop(clave, None)
        if texto:
            mostrar(texto)
    _, centro, _ = st.columns([1, 2, 1])
    with centro:
        with st.form("form_login"):
            st.subheader("Ingreso al sistema")
            usuario = st.text_input("Usuario")
            clave = st.text_input("Contraseña", type="password")
            entrar = st.form_submit_button("Ingresar", type="primary", use_container_width=True)
        if entrar:
            try:
                with con_bd() as con:
                    sesion = seguridad.iniciar_sesion(con, usuario, clave)
                st.session_state["sesion"] = sesion
                st.session_state["ultimo_acceso"] = datetime.now()
                st.session_state["pagina"] = "inicio"
                return sesion
            except ErrorPollolandia as exc:
                mostrar_error(exc)
    return None


def cerrar(sesion: Sesion, motivo: str, aviso: str | None = None) -> None:
    """Cierra la sesión (bitácora) y limpia ``st.session_state``."""
    try:
        with con_bd() as con:
            seguridad.cerrar_sesion(con, sesion, motivo)
    except ErrorPollolandia:
        pass
    for clave in list(st.session_state.keys()):
        del st.session_state[clave]
    if aviso:
        st.session_state["aviso"] = aviso


def sesion_valida() -> Sesion | None:
    """Devuelve la sesión activa o ``None`` (cerrándola si venció por inactividad)."""
    sesion: Sesion | None = st.session_state.get("sesion")
    if sesion is None:
        return None
    with con_bd() as con:
        minutos = seguridad.minutos_inactividad(con)
    ultimo = st.session_state.get("ultimo_acceso", datetime.now())
    if seguridad.sesion_vencida(ultimo, minutos):
        cerrar(sesion, f"Cierre automático por {minutos} minutos de inactividad",
               f"La sesión se cerró por {minutos} minutos de inactividad. Vuelva a ingresar.")
        return None
    st.session_state["ultimo_acceso"] = datetime.now()
    return sesion


def barra_usuario(sesion: Sesion) -> bool:
    """Datos del usuario y botón de salida. Devuelve ``True`` si cerró la sesión."""
    with st.sidebar:
        st.title(config.NOMBRE_SISTEMA)
        st.markdown(f"**{sesion.nombre_completo}**  \n{sistema.NOMBRE_ROL.get(sesion.rol, sesion.rol)}"
                    + (f"  \n_{sesion.cargo}_" if sesion.cargo else ""))
        if st.button("Cerrar sesión", use_container_width=True):
            cerrar(sesion, "Cierre de sesión por el usuario", "Sesión cerrada.")
            return True
    return False


def menu(sesion: Sesion) -> Pagina:
    """Menú lateral con las páginas que el rol puede ver."""
    with con_bd() as con:
        permisos = seguridad.permisos_de_rol(con, sesion.rol)
    visibles = [p for p in PAGINAS if p.recurso is None or p.accion in permisos.get(p.recurso, frozenset())]
    with st.sidebar:
        st.divider()
        claves = [p.clave for p in visibles]
        actual = st.session_state.get("pagina", "inicio")
        elegida = st.radio("Menú", claves, index=claves.index(actual) if actual in claves else 0,
                           format_func=lambda c: next(f"{p.grupo} · {p.titulo}" for p in visibles if p.clave == c),
                           label_visibility="collapsed")
        st.session_state["pagina"] = elegida
        st.divider()
        if MODULOS_FUTUROS:
            with st.expander("Próximos módulos"):
                for m in MODULOS_FUTUROS:
                    st.caption(m)
        st.caption(f"v{config.VERSION} · {config.FASE_ACTUAL}")
    return next(p for p in visibles if p.clave == elegida)


def main() -> None:
    encabezado()
    estado = verificar_conexion()
    if not estado["ok"]:
        st.error(f"No se pudo conectar a la base de datos: {estado['error']}")
        st.stop()
    try:
        preparar_bd(estado["ruta"])
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        st.stop()
    respaldo_del_dia(estado["ruta"], date.today().isoformat())

    # Cada pantalla previa al menú se dibuja en un contenedor que se vacía al
    # completarse, para continuar en la misma ejecución sin recargar la página.
    with con_bd() as con:
        inicializado = seguridad.hay_usuarios(con)
    if not inicializado:
        contenedor = st.empty()
        with contenedor.container():
            creado = pantalla_configuracion_inicial()
        if not creado:
            return
        contenedor.empty()

    sesion = sesion_valida()
    if sesion is None:
        contenedor = st.empty()
        with contenedor.container():
            sesion = pantalla_login()
        if sesion is None:
            return
        contenedor.empty()

    if barra_usuario(sesion):
        pantalla_login()
        return

    if sesion.debe_cambiar_password:
        contenedor = st.empty()
        with contenedor.container():
            actualizada = sistema.pagina_password(sesion, forzado=True)
        if actualizada is None:
            return
        st.session_state["sesion"] = sesion = actualizada
        contenedor.empty()

    pagina = menu(sesion)
    try:
        resultado = pagina.mostrar(sesion)
        if pagina.clave == "password" and isinstance(resultado, Sesion):
            st.session_state["sesion"] = resultado
    except ErrorPollolandia as exc:
        mostrar_error(exc)
        if exc.codigo == "SESION_INVALIDA":
            cerrar(sesion, "Sesión invalidada (usuario desactivado o rol cambiado)",
                   "Su sesión ya no es válida. Vuelva a ingresar.")


main()
