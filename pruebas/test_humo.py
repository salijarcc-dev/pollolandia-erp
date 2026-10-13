"""Pruebas de humo: entorno (Fase 0) y aplicación web con login y menú por rol (Fase 2)."""
from __future__ import annotations

import importlib

import pytest

import config
from db.conexion import conexion, obtener_conexion, verificar_conexion


@pytest.mark.parametrize(
    "modulo",
    ["config", "db.conexion", "servicios.excepciones", "utils.formato", "servicios.auditoria",
     "servicios.seguridad", "servicios.contable", "servicios.tesoreria", "reportes.formatos_tesoreria",
     "servicios.inventarios", "servicios.compras", "reportes.formatos_compras", "modulos.02_compras",
     "servicios.planillas", "reportes.formatos_planillas", "modulos.03_planillas",
     "servicios.produccion", "reportes.formatos_produccion", "modulos.04_produccion",
     "servicios.costos", "reportes.formatos_costos", "modulos.05_costos",
     "servicios.activos", "reportes.formatos_activos", "modulos.06_activos",
     "servicios.ventas", "reportes.formatos_ventas", "modulos.07_ventas",
     "servicios.cierre", "reportes.estados", "modulos.08_contabilidad",
     "servicios.rau", "servicios.archivo", "reportes.formatos_rau", "modulos.09_rau",
     "servicios.presupuesto", "servicios.indicadores", "servicios.vigilancia", "reportes.formatos_presupuesto",
     "modulos.10_presupuesto", "modulos.11_vigilancia", "modulos.12_dashboard",
     "servicios.respaldo", "servicios.parametros", "servicios.poa", "servicios.revision_seguridad",
     "reportes.formatos_admin", "modulos.administracion", "scripts.cargar_datos_poa",
     "scripts.respaldo_automatico", "scripts.restaurar_respaldo"],
)
def test_modulos_importan(modulo):
    assert importlib.import_module(modulo)


def test_ruta_bd_usa_directorio_temporal(datos_temporales):
    ruta = config.obtener_ruta_bd()
    assert ruta.parent == datos_temporales.resolve()
    assert ruta.name == config.NOMBRE_BD


def test_ruta_bd_por_variable_de_entorno(tmp_path, monkeypatch):
    destino = tmp_path / "otra" / "x.db"
    monkeypatch.setenv(config.ENV_RUTA_BD, str(destino))
    assert config.obtener_ruta_bd() == destino.resolve()
    assert destino.parent.exists()


def test_conexion_activa_foreign_keys_y_wal(datos_temporales):
    estado = verificar_conexion()
    assert estado["ok"] is True
    assert estado["foreign_keys"] is True
    assert estado["journal_mode"].lower() == "wal"
    assert estado["error"] is None


def test_verificar_conexion_no_lanza_con_ruta_invalida(tmp_path):
    estado = verificar_conexion(tmp_path / "no_existe" / "x.db")
    assert estado["ok"] is False
    assert estado["error"]


def test_conexion_en_memoria():
    con = obtener_conexion(":memory:")
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    con.close()


def test_fk_impide_huerfanos_y_rollback(datos_temporales):
    import sqlite3

    with conexion() as con:
        con.execute("CREATE TABLE padre (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE hijo (id INTEGER PRIMARY KEY, p INTEGER REFERENCES padre(id))")
    with pytest.raises(sqlite3.IntegrityError):
        with conexion() as con:
            con.execute("INSERT INTO hijo (p) VALUES (99)")
    with conexion() as con:
        assert con.execute("SELECT COUNT(*) FROM hijo").fetchone()[0] == 0


def test_logging_idempotente(datos_temporales):
    import logging

    logger = logging.getLogger(config.LOGGER_RAIZ)
    previo = getattr(logger, "_pollolandia_configurado", False)
    try:
        logger._pollolandia_configurado = False  # type: ignore[attr-defined]
        for h in list(logger.handlers):
            logger.removeHandler(h)
        config.configurar_logging()
        n = len(logger.handlers)
        config.configurar_logging()
        assert len(logger.handlers) == n == 2
        assert (datos_temporales / "logs" / "pollolandia.log").exists()
    finally:
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
        logger._pollolandia_configurado = previo  # type: ignore[attr-defined]


def _app():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(config.BASE_DIR / "app.py"), default_timeout=60)


def _ingresar(usuario, clave):
    """Abre una sesión nueva del navegador e ingresa. (AppTest conserva los
    widgets de la pantalla anterior tras ``st.rerun``; una sesión nueva evita
    ese artefacto de la herramienta de pruebas.)"""
    at = _app().run()
    at.text_input[0].input(usuario)
    at.text_input[1].input(clave)
    at.button[0].click().run()
    assert not at.exception
    return at


def _opciones_menu(at) -> str:
    """Las opciones del menú tras el login (se vuelve a ejecutar la app con la sesión)."""
    at.run()
    assert not at.exception
    return " | ".join(at.sidebar.radio[0].options)


def test_app_configuracion_inicial_y_login(datos_temporales):
    at = _app().run()
    assert not at.exception
    assert "Pollolandia" in at.title[0].value
    assert any("Configuración inicial" in x.value for x in at.subheader)
    at.text_input[0].input("rflores")
    at.text_input[1].input("Pollo2026")
    at.text_input[2].input("Pollo2026")
    at.button[0].click().run()
    assert not at.exception
    assert any("correcta" in x.value for x in at.success)           # continúa en la pantalla de login
    assert any("Ya puede ingresar" in x.value for x in at.info)
    mala = _ingresar("rflores", "Clave-mala1")
    assert any("incorrectos" in x.value for x in mala.error)
    at = _ingresar("rflores", "Pollo2026")
    assert at.session_state["sesion"].rol == "GG"
    opciones = _opciones_menu(at)
    assert "Usuarios" in opciones and "Bitácora" in opciones and "Libro diario" in opciones
    assert "Registrar asiento" not in opciones                      # la GG no registra


def _cambiar_password_obligatorio(at, actual, nueva):
    at.run()
    assert any("Debe cambiar su contraseña" in x.value for x in at.warning)
    at.text_input[0].input(actual)
    at.text_input[1].input(nueva)
    at.text_input[2].input(nueva)
    next(b for b in at.button if b.label == "Cambiar contraseña").click().run()
    assert not at.exception
    assert at.session_state["sesion"].debe_cambiar_password is False


def test_app_menu_por_rol_y_cambio_obligatorio(datos_temporales):
    from db.conexion import conexion
    from servicios import seguridad

    _app().run()  # prepara la base
    with conexion() as con:
        seguridad.crear_usuario_inicial(con, "rflores", "Rocío Del Alba Flores Colque", "Pollo2026")
        gg = seguridad.iniciar_sesion(con, "rflores", "Pollo2026")
        seguridad.crear_usuario(con, gg, "palarcon", "Pablo Alejandro Alarcon", "CV", "Temporal2026")
        seguridad.crear_usuario(con, gg, "jfinanzas", "Jefe de Finanzas", "JF", "Temporal2026")
    cv = _ingresar("palarcon", "Temporal2026")
    _cambiar_password_obligatorio(cv, "Temporal2026", "Vigilancia2026")
    opciones = _opciones_menu(cv)
    assert "Libro diario" in opciones and "Bitácora" in opciones
    assert "Registrar asiento" not in opciones and "Usuarios" not in opciones   # CV solo lectura
    jf = _ingresar("jfinanzas", "Temporal2026")
    _cambiar_password_obligatorio(jf, "Temporal2026", "Finanzas2026")
    opciones = _opciones_menu(jf)
    assert "Registrar asiento" in opciones and "Reversar asiento" in opciones and "Tesorería" in opciones
    assert "Compras e inventarios" in opciones and "Personal y planillas" in opciones
    assert "Producción y trazabilidad" in opciones and "Costos matriciales" in opciones
    assert "Activos fijos y CAPEX" in opciones and "Ventas, HORECA y cobranza" in opciones
    assert "Cierres y estados financieros" in opciones and "RAU, cumplimiento y archivo" in opciones
    assert "Presupuesto y POA" in opciones and "Consejo de Vigilancia" in opciones
    assert "Tablero de indicadores" in opciones
    assert "Respaldos 3-2-1" in opciones and "Parámetros normativos" in opciones
    assert "Revisión de seguridad" not in opciones
    assert "Usuarios" not in opciones and "Bitácora" not in opciones


@pytest.mark.parametrize("pagina", ["diario", "mayor", "balance", "registrar", "reversar", "periodos",
                                    "tesoreria", "compras", "planillas", "produccion", "costos", "activos", "ventas",
                                    "contabilidad", "rau", "presupuesto", "vigilancia", "dashboard",
                                    "autorizaciones", "password"])
def test_app_paginas_del_jf_cargan(datos_temporales, pagina):
    from db.conexion import conexion
    from servicios import seguridad

    _app().run()
    with conexion() as con:
        seguridad.crear_usuario_inicial(con, "rflores", "Rocío Del Alba Flores Colque", "Pollo2026")
        gg = seguridad.iniciar_sesion(con, "rflores", "Pollo2026")
        uid = seguridad.crear_usuario(con, gg, "jfinanzas", "Jefe de Finanzas", "JF", "Temporal2026")
        con.execute("UPDATE usuarios SET debe_cambiar_password = 0 WHERE id = ?", (uid,))
    at = _ingresar("jfinanzas", "Temporal2026")
    at.session_state["pagina"] = pagina
    at.run()
    assert not at.exception
    assert at.header


@pytest.mark.parametrize("pagina", ["inicio", "usuarios", "bitacora", "tesoreria", "compras", "planillas", "produccion", "costos", "activos",
                                    "ventas", "contabilidad", "rau", "presupuesto", "vigilancia",
                                    "dashboard", "autorizaciones", "periodos", "parametros", "respaldos",
                                    "seguridad"])
def test_app_paginas_de_la_gg_cargan(datos_temporales, pagina):
    from db.conexion import conexion
    from servicios import seguridad

    _app().run()
    with conexion() as con:
        seguridad.crear_usuario_inicial(con, "rflores", "Rocío Del Alba Flores Colque", "Pollo2026")
    at = _ingresar("rflores", "Pollo2026")
    at.session_state["pagina"] = pagina
    at.run()
    assert not at.exception
    assert at.header


def test_app_pagina_de_compras_para_la_unidad_tecnica(datos_temporales):
    from db.conexion import conexion
    from servicios import seguridad

    _app().run()
    with conexion() as con:
        seguridad.crear_usuario_inicial(con, "rflores", "Rocío Del Alba Flores Colque", "Pollo2026")
        gg = seguridad.iniciar_sesion(con, "rflores", "Pollo2026")
        uid = seguridad.crear_usuario(con, gg, "granja", "Encargado de Granja", "UT", "Temporal2026")
        con.execute("UPDATE usuarios SET debe_cambiar_password = 0 WHERE id = ?", (uid,))
    at = _ingresar("granja", "Temporal2026")
    assert "Compras e inventarios" in _opciones_menu(at)
    at.session_state["pagina"] = "compras"
    at.run()
    assert not at.exception
    assert at.header


@pytest.mark.parametrize("pagina", ["planillas", "produccion", "costos", "activos", "ventas", "rau"])
@pytest.mark.parametrize("rol,usuario", [("UT", "granja"), ("CV", "vigilancia")])
def test_app_pagina_de_planillas_para_ut_y_cv(datos_temporales, rol, usuario, pagina):
    from db.conexion import conexion
    from servicios import seguridad

    _app().run()
    with conexion() as con:
        seguridad.crear_usuario_inicial(con, "rflores", "Rocío Del Alba Flores Colque", "Pollo2026")
        gg = seguridad.iniciar_sesion(con, "rflores", "Pollo2026")
        uid = seguridad.crear_usuario(con, gg, usuario, f"Usuario {rol}", rol, "Temporal2026")
        con.execute("UPDATE usuarios SET debe_cambiar_password = 0 WHERE id = ?", (uid,))
    at = _ingresar(usuario, "Temporal2026")
    opciones = _opciones_menu(at)
    assert "Personal y planillas" in opciones and "Producción y trazabilidad" in opciones
    at.session_state["pagina"] = pagina
    at.run()
    assert not at.exception
    assert at.header


@pytest.mark.parametrize("rol,usuario,paginas,ocultas", [
    ("UT", "granja", ["dashboard"], ["Presupuesto y POA", "Consejo de Vigilancia"]),
    ("CV", "vigilancia", ["presupuesto", "vigilancia", "dashboard", "parametros", "respaldos", "seguridad"], []),
    ("CA", "gcapia", ["presupuesto", "vigilancia", "dashboard", "parametros", "respaldos"], []),
    ("AG", "asamblea", ["parametros", "dashboard"], ["Respaldos 3-2-1", "Revisión de seguridad"]),
])
def test_app_paginas_fase12_por_rol(datos_temporales, rol, usuario, paginas, ocultas):
    from db.conexion import conexion
    from servicios import seguridad

    _app().run()
    with conexion() as con:
        seguridad.crear_usuario_inicial(con, "rflores", "Rocío Del Alba Flores Colque", "Pollo2026")
        gg = seguridad.iniciar_sesion(con, "rflores", "Pollo2026")
        uid = seguridad.crear_usuario(con, gg, usuario, f"Usuario {rol}", rol, "Temporal2026")
        con.execute("UPDATE usuarios SET debe_cambiar_password = 0 WHERE id = ?", (uid,))
    at = _ingresar(usuario, "Temporal2026")
    opciones = _opciones_menu(at)
    assert "Tablero de indicadores" in opciones
    assert all(o not in opciones for o in ocultas)
    for pagina in paginas:
        at.session_state["pagina"] = pagina
        at.run()
        assert not at.exception, (pagina, at.exception)
        assert at.header
