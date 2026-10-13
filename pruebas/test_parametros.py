"""Pruebas de la administración de parámetros con historial (Fase 13).

Casos con cifras de los documentos: el SME de Bs 136.413 (Manual
Presupuestario / POA §3.3) y la caducidad de contraseñas de 90 días
(MAN-ARC-01 §5.2). La GG propone, el CA o la AG aprueban con acta; ninguna
vigencia se borra; no se modifican ejercicios cerrados.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from db.conexion import obtener_parametro
from pruebas import escenario_2026 as esc
from servicios import parametros
from servicios.excepciones import AutorizacionError, DatosInvalidosError, PeriodoCerradoError, ReglaNegocioError

D = Decimal


def test_cambio_desde_un_ejercicio_futuro_conserva_la_vigencia_anterior(con, s):
    anterior = obtener_parametro(con, "password_caducidad_dias", 2026)
    assert anterior == 90
    with pytest.raises(AutorizacionError):
        parametros.solicitar_cambio(con, s["jf"], "password_caducidad_dias", "60", 2027, "Acta", "Más seguridad")
    sol = parametros.solicitar_cambio(con, s["gg"], "password_caducidad_dias", "60", 2027,
                                      "Resolución GG 05/2027", "Endurecer la política de contraseñas")
    assert (sol["estado"], sol["valor_anterior"], sol["valor_nuevo"]) == ("SOLICITADA", "90", "60")
    assert obtener_parametro(con, "password_caducidad_dias", 2027) == 90      # aún no rige
    with pytest.raises(ReglaNegocioError) as e:
        parametros.solicitar_cambio(con, s["gg"], "password_caducidad_dias", "45", 2027, "x", "y")
    assert e.value.codigo == "SOLICITUD_PENDIENTE"
    with pytest.raises(AutorizacionError):
        parametros.resolver_cambio(con, s["gg"], sol["id"], True, "Acta")
    with pytest.raises(AutorizacionError):
        parametros.resolver_cambio(con, s["cv"], sol["id"], True, "Acta")
    with pytest.raises(DatosInvalidosError):
        parametros.resolver_cambio(con, s["ca_presidente"], sol["id"], True)
    assert parametros.resolver_cambio(con, s["ca_presidente"], sol["id"], True, "Acta CA 9/2027") == "APROBADA"
    assert obtener_parametro(con, "password_caducidad_dias", 2026) == 90
    assert obtener_parametro(con, "password_caducidad_dias", 2027) == 60
    assert obtener_parametro(con, "password_caducidad_dias", 2030) == 60
    h = parametros.historial(con, s["cv"], "password_caducidad_dias")
    assert [(v["valor"], v["ejercicio_desde"], v["ejercicio_hasta"]) for v in h["vigencias"]] == \
        [("90", 2026, 2026), ("60", 2027, None)]
    assert h["solicitudes"][0]["acta_ref"] == "Acta CA 9/2027"
    assert {e["accion"] for e in h["eventos"]} >= {"INSERT", "UPDATE", "AUTORIZACION"}
    with pytest.raises(ReglaNegocioError):
        parametros.resolver_cambio(con, s["ca_presidente"], sol["id"], False, observacion="tarde")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM parametros WHERE clave = 'password_caducidad_dias'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE solicitudes_parametro SET valor_nuevo = '1' WHERE id = ?", (sol["id"],))


def test_cambio_en_el_mismo_ejercicio_reemplaza_el_valor_con_historia(con, s):
    sme = obtener_parametro(con, "sme", 2026)
    assert sme == D("136413")
    sol = parametros.solicitar_cambio(con, s["gg"], "sme", "140000", 2026, "Acta AG 3/2026",
                                      "Recalcular el SME con los egresos reales")
    assert parametros.resolver_cambio(con, s["ag"], sol["id"], True, "Acta AG 3/2026") == "APROBADA"
    assert obtener_parametro(con, "sme", 2026) == D("140000")
    h = parametros.historial(con, s["gg"], "sme")
    evento = next(e for e in h["eventos"] if e["tabla"] == "parametros" and e["accion"] == "UPDATE")
    assert '"136413"' in evento["valores_antes"] and '"140000"' in evento["valores_despues"]


def test_validaciones_rechazo_y_ejercicio_cerrado(con, s):
    with pytest.raises(DatosInvalidosError):
        parametros.solicitar_cambio(con, s["gg"], "no_existe", "1", 2026, "x", "y")
    with pytest.raises(DatosInvalidosError):
        parametros.solicitar_cambio(con, s["gg"], "login_intentos_max", "tres", 2026, "x", "y")
    with pytest.raises(ReglaNegocioError) as e:
        parametros.solicitar_cambio(con, s["gg"], "login_intentos_max", "3", 2026, "x", "y")
    assert e.value.codigo == "SIN_CAMBIO"
    with pytest.raises(DatosInvalidosError):
        parametros.solicitar_cambio(con, s["gg"], "login_intentos_max", "4", 2026, "", "y")
    sol = parametros.solicitar_cambio(con, s["gg"], "login_intentos_max", "5", 2026, "Propuesta", "Menos bloqueos")
    with pytest.raises(DatosInvalidosError):
        parametros.resolver_cambio(con, s["ca_presidente"], sol["id"], False)
    assert parametros.resolver_cambio(con, s["ca_presidente"], sol["id"], False,
                                      observacion="Se mantiene el límite de 3 intentos") == "RECHAZADA"
    assert obtener_parametro(con, "login_intentos_max", 2026) == 3
    esc.cargar_escenario_2026(con, s)
    esc.cerrar_ejercicio_completo(con, s)
    with pytest.raises(PeriodoCerradoError):
        parametros.solicitar_cambio(con, s["gg"], "login_intentos_max", "4", 2026, "x", "y")


def test_listado_marca_los_valores_por_validar(con, s):
    filas = parametros.listar_parametros(con, s["jf"], 2026)
    assert len(filas) == con.execute("SELECT COUNT(DISTINCT clave) FROM parametros WHERE ejercicio_desde <= 2026 AND "
                                     "(ejercicio_hasta IS NULL OR ejercicio_hasta >= 2026)").fetchone()[0]
    marcados = parametros.listar_parametros(con, s["jf"], 2026, solo_por_validar=True)
    assert marcados and all(f["por_validar"] for f in marcados)
    assert any(f["clave"] == "respaldo_retencion_diaria_dias" for f in marcados)
    assert [f["clave"] for f in parametros.listar_parametros(con, s["jf"], 2026, "caducidad")] == \
        ["password_caducidad_dias"]
    with pytest.raises(AutorizacionError):
        parametros.listar_parametros(con, s["ut"], 2026)
