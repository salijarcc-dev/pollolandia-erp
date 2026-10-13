"""Pruebas de servicios/auditoria.py (Fase 2)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from servicios import auditoria
from servicios.excepciones import AutorizacionError, DatosInvalidosError


def test_registrar_evento_serializa_tipos_del_erp(con):
    eid = auditoria.registrar_evento(
        con, usuario="jfinanzas", rol="JF", tabla="asientos", accion="INSERT", registro_id=7,
        antes=None, despues={"total": Decimal("27000.00"), "fecha": date(2026, 7, 1), "cuentas": ("1.1.01.03",)},
        detalle="prueba")
    fila = con.execute("SELECT * FROM auditoria WHERE id=?", (eid,)).fetchone()
    assert fila["registro_id"] == "7" and fila["valores_antes"] is None
    assert json.loads(fila["valores_despues"]) == {"cuentas": ["1.1.01.03"], "fecha": "2026-07-01", "total": "27000.00"}
    assert fila["fecha_hora"]
    con.rollback()


@pytest.mark.parametrize("kwargs", [
    {"usuario": "", "tabla": "x", "accion": "INSERT"},
    {"usuario": "u", "tabla": " ", "accion": "INSERT"},
    {"usuario": "u", "tabla": "x", "accion": "BORRAR"},
])
def test_registrar_evento_valida(con, kwargs):
    with pytest.raises(DatosInvalidosError):
        auditoria.registrar_evento(con, rol=None, **kwargs)


def test_consultar_bitacora_permisos_y_filtros(con, s):
    with pytest.raises(AutorizacionError):
        auditoria.consultar_bitacora(con, s["jf"])
    logins = auditoria.consultar_bitacora(con, s["cv"], tabla="usuarios", accion="LOGIN", usuario="jfinanzas")
    assert logins and all(e["usuario"] == "jfinanzas" and e["accion"] == "LOGIN" for e in logins)
    altas = auditoria.consultar_bitacora(con, s["gg"], tabla="usuarios", accion="INSERT")
    assert len(altas) == 9 and all("password_hash" not in json.dumps(e["valores_despues"]) for e in altas)
    with pytest.raises(DatosInvalidosError):
        auditoria.consultar_bitacora(con, s["gg"], limite=0)
    with pytest.raises(DatosInvalidosError):
        auditoria.consultar_bitacora(con, s["gg"], accion="NADA")


def test_bitacora_inmutable(con):
    with pytest.raises(sqlite3.IntegrityError, match="inmutable"):
        con.execute("UPDATE auditoria SET usuario='otro'")
    with pytest.raises(sqlite3.IntegrityError, match="inmutable"):
        con.execute("DELETE FROM auditoria")
    con.rollback()


def test_diferencias():
    assert auditoria.diferencias({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {
        "b": {"antes": 2, "despues": 3}, "c": {"antes": None, "despues": 4}}
    assert auditoria.diferencias(None, None) == {}


def test_cierre_de_sesion_queda_registrado(con, s):
    from servicios import seguridad
    seguridad.cerrar_sesion(con, s["cv"])
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE accion='LOGOUT' AND usuario='palarcon'").fetchone()[0] == 1
