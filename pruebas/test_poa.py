"""Pruebas de la carga y el seguimiento de los datos del POA 2026-2027 (Fase 13).

Cifras del POA (ANEXO 12): ciclos de 73 días C1-C5 (C2 del 12-sep al
23-nov-2026, 54 días dentro del POA; C5 del 19-abr al 30-jun-2027); ingreso de
los ciclos dentro del POA Bs 739.200; flujo de oct-2026 a jun-2027 con saldo
inicial Bs 116.825 y final Bs 291.669; CAPEX Bs 97.000 en junio.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from db import datos_poa
from pruebas import escenario_2026 as esc
from pruebas import simulacion_ciclo as sim
from scripts import cargar_datos_poa
from servicios import poa
from servicios.excepciones import AutorizacionError, ReglaNegocioError

D = Decimal


def test_validacion_del_poa_sin_errores_con_observaciones_de_redondeo(con):
    v = poa.validar_datos(con)
    assert v["errores"] == []
    texto = " ".join(v["observaciones"])
    assert "2026-10: redondeo de Bs 1" in texto and "2027-03: redondeo de Bs 1" in texto
    assert "suma de los meses Bs 321401 frente a Bs 321398" in texto           # pollitos e insumos
    assert v["totales"]["COBROS_CARNE"] == D("733200") and v["totales"]["CAPEX"] == D("97000")
    assert v["totales"]["egresos_previo"] == D("205985")


def test_carga_idempotente_e_inmutable(con, s):
    with pytest.raises(AutorizacionError):
        poa.cargar_datos_poa(con, s["ut"])
    r = poa.cargar_datos_poa(con, s["gg"])
    assert (r["ya_cargado"], r["ciclos"], r["registros_flujo"], r["filas_area"]) == (False, 5, 90, 22)
    assert poa.cargar_datos_poa(con)["ya_cargado"] is True
    assert con.execute("SELECT COUNT(*) FROM poa_cargas").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE tabla = 'poa_cargas'").fetchone()[0] == 1
    c2 = con.execute("SELECT * FROM poa_calendario WHERE ciclo = 2").fetchone()
    assert (c2["inicio"], c2["cierre"], c2["dias_en_poa"]) == ("2026-09-12", "2026-11-23", 54)
    assert con.execute("SELECT SUM(ingreso_cent) FROM poa_calendario WHERE dias_en_poa > 0").fetchone()[0] == 73_920_000
    junio = con.execute("SELECT monto_cent FROM poa_flujo_mensual WHERE mes = '2027-06' AND concepto = 'SALDO_FINAL'"
                        ).fetchone()[0]
    assert junio == 29_166_900
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE poa_flujo_mensual SET monto_cent = 0")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM poa_calendario")


def test_datos_alterados_se_rechazan(con, monkeypatch):
    monkeypatch.setitem(datos_poa.FLUJO_MENSUAL, "MOD", ("3363", "9755", "3363", "3363", "7538", "3363", "7212",
                                                        "1962", "9699"))
    with pytest.raises(ReglaNegocioError) as e:
        poa.cargar_datos_poa(con)
    assert e.value.codigo == "POA_INVALIDO" and "2026-11" in str(e.value)
    assert con.execute("SELECT COUNT(*) FROM poa_cargas").fetchone()[0] == 0


def test_calendario_frente_al_ciclo_real(con, s):
    with pytest.raises(ReglaNegocioError):
        poa.calendario_vs_real(con, s["jf"])
    poa.cargar_datos_poa(con)
    sim.simular_ciclo(con, s)
    cal = {c["ciclo"]: c for c in poa.calendario_vs_real(con, s["ca_presidente"])}
    # El ciclo 1 real empezó el lunes 6-jul (OC solo los lunes): 5 días después del POA.
    assert (cal[1]["inicio_real"], cal[1]["desfase_dias"], cal[1]["estado_real"]) == ("2026-07-06", 5, "CONCLUIDO")
    assert (cal[2]["inicio_real"], cal[2]["desfase_dias"]) == ("2026-09-21", 9)
    assert cal[3]["estado_real"] == "SIN_INICIAR" and cal[3]["ingreso_poa"] == D("184800.00")


def test_flujo_mensual_frente_a_lo_real(con, s):
    poa.cargar_datos_poa(con)
    esc.cargar_escenario_2026(con, s)
    flujo = {f["mes"]: f for f in poa.flujo_poa_vs_real(con, s["jf"])}
    assert flujo["2026-10"]["saldo_poa"] == D("69055") and flujo["2027-06"]["saldo_poa"] == D("291669")
    assert flujo["2027-06"]["pagos_poa"] == D("25871") + D("97000")
    # Escenario contable 2026: saldo final real de efectivo al 30-jun-2027 = Bs 291.668,90 (banco + caja).
    assert flujo["2027-06"]["saldo_real"] == D("291668.90") and flujo["2027-06"]["desvio"] == D("-0.10")
    assert flujo["2026-11"]["cobros_real"] == D("184800.00")                  # cierre del C2 (23-nov)


def test_script_de_carga(tmp_path, capsys):
    ruta = tmp_path / "poa.db"
    assert cargar_datos_poa.main(["--ruta", str(ruta), "--validar"]) == 0
    assert "Observaciones" in capsys.readouterr().out
    assert cargar_datos_poa.main(["--ruta", str(ruta)]) == 0
    salida = capsys.readouterr().out
    assert "cargado" in salida and "Ciclos del calendario: 5" in salida
    assert cargar_datos_poa.main(["--ruta", str(ruta)]) == 0
    assert "ya estaba cargado" in capsys.readouterr().out
