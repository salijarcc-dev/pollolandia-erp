"""Pruebas de la Fase 12: indicadores y CI-FIN-18 (servicios/indicadores.py).

Cifras del Manual Presupuestario (E.3) para el ejercicio 2026: ICL 62,4 %
(Normal), ROA 27,4 % (Bueno), margen operativo 22,1 % (Normal) y SME 101 %
(Normal). CI-FIN-18: emitir el FIN-INF-05 sin el INF-CV-07 del mes es rechazado.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from db.conexion import obtener_parametros_vigentes
from pruebas import escenario_2026 as esc
from servicios import cierre, indicadores
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal
DECISIONES = {"RINC": {"accion": "Previsto en el presupuesto"}, "ROA": {"accion": "Se evalúa al cierre anual"}}
MUESTRA = ["Costo de ventas de carne 6.1.02.01", "6.1.01.01 Centro de costos carne", "5.1.01.01 Venta de carne"]


def periodo(con, ejercicio, numero):
    return int(con.execute("SELECT id FROM periodos WHERE ejercicio = ? AND numero = ?", (ejercicio, numero)).fetchone()[0])


@pytest.fixture()
def cargado(con, s):
    esc.cargar_escenario_2026(con, s)
    return con


def test_indicadores_2026_coinciden_con_el_manual(cargado, s):
    d = indicadores.indicadores_ejercicio(cargado, s["ut"], 2026)
    i = d["indicadores"]
    assert (i["ICL"]["valor"], i["ICL"]["nivel"], i["ICL"]["semaforo"]) == (D("0.6241"), "NORMAL", "AMARILLO")
    assert (i["ROA"]["valor"], i["ROA"]["nivel"]) == (D("0.2739"), "BUENO")
    assert (i["MARGEN_OPERATIVO"]["valor"], i["MARGEN_OPERATIVO"]["nivel"]) == (D("0.2213"), "NORMAL")
    assert (i["SME"]["valor"], i["SME"]["nivel"]) == (D("1.0117"), "NORMAL")                  # Bs 138.010 / 136.413
    assert (i["ICL"]["proyectado"], i["ROA"]["proyectado"], i["MARGEN_OPERATIVO"]["proyectado"]) == \
        (D("0.624"), D("0.274"), D("0.221"))
    assert i["RINC"]["nivel"] == "MALO" and i["ICA"]["valor"] is None
    assert d["cifras"]["ebit"] == D("202793.39") and d["cifras"]["ingresos_operativos"] == D("916500.00")


def test_semaforo_margen_operativo_bordes(con):
    p = obtener_parametros_vigentes(con, 2026)
    assert [indicadores.semaforo_margen_operativo(p, D(v)) for v in ("0.1999", "0.20", "0.30", "0.3001")] == \
        ["MALO", "NORMAL", "NORMAL", "BUENO"]
    assert indicadores.semaforo_margen_operativo(p, None) is None


def test_tendencias_por_ciclo_y_por_ejercicio(cargado, s):
    ciclos = indicadores.tendencia_ciclos(cargado, s["jf"], 2026)
    assert [c["ciclo"] for c in ciclos] == [1, 2, 3, 4, 5]
    assert ciclos[0]["ICL"] == D("0.6293") and ciclos[0]["ventas"] == D("184800.00")
    assert ciclos[2]["SME"] == D("1.7336")                     # POA §3.3: inicio del C3 al 173 % del SME
    anios = indicadores.tendencia_ejercicios(cargado, s["ag"])
    assert anios[0]["ejercicio"] == 2026 and anios[0]["ROA"] == D("0.2739")
    assert anios[1]["operaciones"] is False and anios[1]["ICL_proyectado"] == D("0.558")


def test_fin_inf_05_sin_inf_cv_07_es_rechazado(cargado, s):
    con = cargado
    sep = periodo(con, 2026, 3)
    with pytest.raises(ReglaNegocioError) as exc:
        indicadores.emitir_fin_inf_05(con, s["gg"], sep, "2026-10-08")
    assert exc.value.codigo == "SIN_INF_CV_07" and "INF-CV-07" in exc.value.mensaje
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO informes_gerenciales (periodo_id, inf_cv_07_id, fecha, indicadores_json, decisiones_json, "
                    "emitido_por, registrado_en) VALUES (?, 999, '2026-10-08', '{}', '{}', 'x', 'x')", (sep,))


def test_secuencia_ci_fin_18_inf_cv_07_y_fin_inf_05(cargado, s):
    con = cargado
    sep = periodo(con, 2026, 3)
    with pytest.raises(ReglaNegocioError, match="FIN-INF-01"):
        indicadores.registrar_inf_cv_07(con, s["cv"], sep, "2026-10-05", partidas_muestreadas=MUESTRA,
                                        partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=True)
    cierre.registrar_lista_verificacion(con, s["jf"], sep, "2026-10-02")
    with pytest.raises(DatosInvalidosError):
        indicadores.registrar_inf_cv_07(con, s["cv"], sep, "2026-09-29", partidas_muestreadas=MUESTRA,
                                        partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=True)
    with pytest.raises(ReglaNegocioError, match="al menos 3"):
        indicadores.registrar_inf_cv_07(con, s["cv"], sep, "2026-10-05", partidas_muestreadas=MUESTRA[:2],
                                        partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=True)
    with pytest.raises(AutorizacionError):
        indicadores.registrar_inf_cv_07(con, s["jf"], sep, "2026-10-05", partidas_muestreadas=MUESTRA,
                                        partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=True)
    previa = indicadores.verificacion_previa(con, s["cv"], sep)
    assert previa["coincide"] is True and previa["recalculo"]["ICL"] == previa["reportado"]["ICL"]
    assert len(previa["partidas_disponibles"]) >= 3
    r = indicadores.registrar_inf_cv_07(con, s["cv"], sep, "2026-10-05", partidas_muestreadas=MUESTRA,
                                        partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=True)
    assert r["dictamen"] == "CONFORME"
    with pytest.raises(AutorizacionError):
        indicadores.emitir_fin_inf_05(con, s["jf"], sep, "2026-10-08")
    with pytest.raises(ReglaNegocioError, match="RINC"):                      # RINC en «Malo» exige acción
        indicadores.emitir_fin_inf_05(con, s["gg"], sep, "2026-10-08")
    f = indicadores.emitir_fin_inf_05(con, s["gg"], sep, "2026-10-08", decisiones={
        "RINC": {"accion": "Sin alimento propio hasta 2027: se acepta el desvío previsto en el presupuesto"},
        "ROA": {"accion": "ROA acumulado de un solo ciclo; se evalúa al cierre anual (meta > 18 %)"}})
    assert f["indicadores"]["ICL"]["umbral"] == "NORMAL" and f["indicadores"]["RINC"]["desviacion"] is True
    with pytest.raises(ReglaNegocioError):
        indicadores.emitir_fin_inf_05(con, s["gg"], sep, "2026-10-09", decisiones={"RINC": {"accion": "x"}})
    estado = {e["periodo_id"]: e for e in indicadores.estado_ci_fin_18(con, s["ca_presidente"], 2026)}
    assert estado[sep]["inf_cv_07"] == "2026-10-05" and estado[sep]["fin_inf_05"] == "2026-10-08"
    assert indicadores.listar_fin_inf_05(con, s["ag"])[0]["periodo_id"] == sep


def test_inf_cv_07_con_observaciones_exige_incorporarlas(cargado, s):
    con = cargado
    nov = periodo(con, 2026, 5)
    cierre.registrar_lista_verificacion(con, s["jf"], nov, "2026-12-02")
    with pytest.raises(DatosInvalidosError):
        indicadores.registrar_inf_cv_07(con, s["cv2"], nov, "2026-12-04", partidas_muestreadas=MUESTRA,
                                        partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=False)
    indicadores.registrar_inf_cv_07(con, s["cv2"], nov, "2026-12-04", partidas_muestreadas=MUESTRA,
                                    partidas_conformes=True, recalculo_conforme=True, razonabilidad_conforme=False,
                                    observaciones="Margen bruto menor al del mes anterior sin explicación")
    with pytest.raises(ReglaNegocioError, match="incorporaron"):
        indicadores.emitir_fin_inf_05(con, s["gg"], nov, "2026-12-06", decisiones=DECISIONES)
    indicadores.emitir_fin_inf_05(con, s["gg"], nov, "2026-12-06", decisiones={"RINC": {"accion": "Previsto"}},
                                  observaciones_incorporadas="Se explicó el cambio por el mantenimiento del ciclo 2")
