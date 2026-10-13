"""Fixtures compartidas: aíslan datos y BD en directorios temporales.

Para la Fase 2 se construye UNA base modelo por sesión de pruebas (esquema,
semillas y un usuario por cargo) y cada prueba trabaja sobre una copia.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import config

# bcrypt con costo mínimo: las pruebas verifican la lógica, no la fuerza del hash.
config.BCRYPT_ROUNDS = 4

CLAVE = "Pollo2026"

# clave -> (usuario, nombre, rol, número de socio o None)
USUARIOS_PRUEBA: dict[str, tuple[str, str, str, int | None]] = {
    "gg": ("rflores", "Rocío Del Alba Flores Colque", "GG", 1),
    "jf": ("jfinanzas", "Jefe de Finanzas y Comercialización", "JF", None),
    "ut": ("granja", "Encargado de Granja", "UT", None),
    "ca_presidente": ("gcapia", "Geovanna Capia Morante", "CA", 2),
    "ca_secretario": ("mcanaviri", "Melisa Canaviri Mamani", "CA", 3),
    "ca_tesorero": ("jayllon", "Jhudith Ayllon Copa", "CA", 4),
    "cv": ("palarcon", "Pablo Alejandro Alarcon", "CV", 5),
    "cv2": ("ecachaga", "Elva Danitza Cachaga Alanoca", "CV", 6),
    "ag": ("asamblea", "Secretaría de Asamblea", "AG", 10),
}


@pytest.fixture()
def datos_temporales(tmp_path, monkeypatch):
    """Redirige datos/ y la BD a un directorio temporal."""
    monkeypatch.setenv(config.ENV_DIR_DATOS, str(tmp_path))
    monkeypatch.delenv(config.ENV_RUTA_BD, raising=False)
    return tmp_path


@pytest.fixture(scope="session")
def bd_modelo(tmp_path_factory) -> Path:
    """Base inicializada con un usuario por cargo (contraseña ``CLAVE``)."""
    from db.conexion import obtener_conexion
    from db.migraciones import inicializar
    from servicios import seguridad

    ruta = tmp_path_factory.mktemp("modelo") / "modelo.db"
    inicializar(ruta)
    con = obtener_conexion(ruta)
    try:
        ids_socio = {f["numero"]: f["id"] for f in con.execute("SELECT id, numero FROM socios")}
        usuario, nombre, _, socio = USUARIOS_PRUEBA["gg"]
        seguridad.crear_usuario_inicial(con, usuario, nombre, CLAVE, ids_socio[socio])
        gg = seguridad.iniciar_sesion(con, usuario, CLAVE)
        for clave, (usuario, nombre, rol, socio) in USUARIOS_PRUEBA.items():
            if clave == "gg":
                continue
            seguridad.crear_usuario(con, gg, usuario, nombre, rol, CLAVE,
                                    ids_socio[socio] if socio else None)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    return ruta


@pytest.fixture()
def ruta_prueba(bd_modelo, tmp_path) -> Path:
    destino = tmp_path / "erp.db"
    shutil.copy(bd_modelo, destino)
    return destino


@pytest.fixture()
def con(ruta_prueba):
    from db.conexion import obtener_conexion

    c = obtener_conexion(ruta_prueba)
    yield c
    if c.in_transaction:
        c.rollback()
    c.close()


@pytest.fixture()
def s(con):
    """Sesiones abiertas por cargo: ``s["gg"]``, ``s["jf"]``, ``s["cv"]``…"""
    from servicios import seguridad

    return {clave: seguridad.iniciar_sesion(con, datos[0], CLAVE) for clave, datos in USUARIOS_PRUEBA.items()}
