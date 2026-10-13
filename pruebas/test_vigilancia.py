"""Pruebas de la Fase 12: panel del Consejo de Vigilancia (servicios/vigilancia.py).

* MOF §3.2: el miembro del CV que cofirmó un desembolso no puede verificarlo.
* INF-CV-02: saldo al cierre del ciclo 1 de 2026 (Bs 181.396,78) frente al
  SME de Bs 136.413 → 133 %, cumple.
* Inventario sorpresivo: diferencias físicas contra el kárdex al CPP.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from pruebas import ayuda_compras as ac
from pruebas import escenario_2026 as esc
from servicios import seguridad, tesoreria, vigilancia
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal


@pytest.fixture()
def cargado(con, s):
    esc.cargar_escenario_2026(con, s)
    return con


def _desembolso_mancomunado(con, s, monto="6000", fecha="2026-08-03"):
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, "Pago de insumos", ctx)
    assert seguridad.firmar_autorizacion(con, s["gg"], aid) == "PENDIENTE"
    assert seguridad.firmar_autorizacion(con, s["cv"], aid) == "AUTORIZADA"
    return aid


def test_cofirmante_del_cv_no_puede_verificar_el_desembolso(con, s):
    aid = _desembolso_mancomunado(con, s)
    fila = next(d for d in vigilancia.desembolsos_a_verificar(con, s["cv"], 2026) if d["autorizacion_id"] == aid)
    assert fila["mancomunada"] is True and fila["puedo_verificar"] is False
    with pytest.raises(AutorizacionError) as exc:
        vigilancia.registrar_verificacion_desembolso(con, s["cv"], aid, "2026-08-10", True)
    assert exc.value.codigo == "COFIRMANTE_NO_VERIFICA"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO verificaciones_desembolso (autorizacion_id, fecha, conforme, usuario_id, verificado_por, "
                    "registrado_en) VALUES (?, '2026-08-10', 1, ?, 'x', 'x')", (aid, s["cv"].usuario_id))
    with pytest.raises(AutorizacionError):
        vigilancia.registrar_verificacion_desembolso(con, s["gg"], aid, "2026-08-10", True)
    with pytest.raises(DatosInvalidosError):
        vigilancia.registrar_verificacion_desembolso(con, s["cv2"], aid, "2026-08-10", False)
    vigilancia.registrar_verificacion_desembolso(con, s["cv2"], aid, "2026-08-10", True)
    with pytest.raises(ReglaNegocioError):
        vigilancia.registrar_verificacion_desembolso(con, s["cv2"], aid, "2026-08-11", True)
    panel = {c["codigo"]: c for c in vigilancia.panel_cv(con, s["cv"], 2026, "2026-09-01")["controles"]}
    assert panel["DESEMBOLSOS"]["realizados"] == 1 and panel["DESEMBOLSOS"]["semaforo"] == "VERDE"


def test_inventario_sorpresivo_con_diferencia_se_reporta(con, s):
    ac.depositar(con, s, "300000")
    ac.comprar(con, s, "INS-0302", 100, 185)
    insumo = ac.insumo(con, "INS-0302")
    base = {i["insumo_id"]: i for i in vigilancia.insumos_para_conteo(con, s["cv"], "2026-07-10")}
    assert base[insumo]["contable"] == D("100") and base[insumo]["cpp"] == D("185")
    with pytest.raises(AutorizacionError):
        vigilancia.registrar_inventario_sorpresivo(con, s["jf"], "2026-07-10", {insumo: "98"})
    r = vigilancia.registrar_inventario_sorpresivo(con, s["cv"], "2026-07-10", {insumo: "98"}, "Faltan 2 qq")
    assert r["con_diferencia"] == 1 and r["diferencia"] == D("-370.00") and r["reportar_ag"] is True
    sin = vigilancia.registrar_inventario_sorpresivo(con, s["cv2"], "2026-07-11", {insumo: "100"})
    assert sin["reportar_ag"] is False
    assert vigilancia.listar_inventarios(con, s["ca_presidente"])[0]["detalle"][0]["fisico"] == D("100")
    with pytest.raises(DatosInvalidosError):
        vigilancia.registrar_inventario_sorpresivo(con, s["cv"], "2026-07-12", {})


def test_inf_cv_02_liquidez_trimestral(cargado, s):
    con = cargado
    d = vigilancia.datos_inf_cv_02(con, s["cv"], 2026, 1)
    assert [(c["ciclo"], c["saldo"], c["cumplimiento"]) for c in d["ciclos"]] == [(1, D("181396.78"), D("1.3298"))]
    assert d["reportar_ag"] is False
    with pytest.raises(DatosInvalidosError):
        vigilancia.registrar_inf_cv_02(con, s["cv"], 2026, 1, "2026-09-15")
    r = vigilancia.registrar_inf_cv_02(con, s["cv"], 2026, 1, "2026-10-03")
    assert r["dictamen"] == "CONFORME"
    with pytest.raises(ReglaNegocioError):
        vigilancia.registrar_inf_cv_02(con, s["cv"], 2026, 1, "2026-10-04")
    vigilancia.registrar_reporte_ag(con, s["cv"], r["id"], "2026-10-20")
    with pytest.raises(ReglaNegocioError):
        vigilancia.registrar_reporte_ag(con, s["cv"], r["id"], "2026-10-21")
    assert vigilancia.listar_informes_cv(con, s["gg"], "INF-CV-02")[0]["reportado_ag_en"] == "2026-10-20"
    t4 = vigilancia.datos_inf_cv_02(con, s["cv"], 2026, 4)
    assert [c["ciclo"] for c in t4["ciclos"]] == [4, 5]


def test_inf_cv_05_inf_cv_06_e_inf_cv_08(cargado, s):
    con = cargado
    d5 = {f["concepto"][:28]: f for f in vigilancia.datos_inf_cv_05(con, s["cv"], 2026)["filas"]}
    assert d5["Provisión para indemnización"]["saldo"] == D("10600.00")
    assert d5["Provisión para indemnización"]["devengado"] == D("10600.00")
    r5 = vigilancia.registrar_inf_cv_05(con, s["cv"], 2026, "2027-07-20", provisiones_suficientes=True,
                                        aportes_al_dia=True)
    assert r5["dictamen"] == "CONFORME"
    with pytest.raises(ReglaNegocioError):
        vigilancia.registrar_inf_cv_06(con, s["cv"], "2027-01-15", precios_mercado={})   # sin tarifario aprobado
    with pytest.raises(DatosInvalidosError):
        vigilancia.registrar_inf_cv_08(con, s["cv"], 2026, "2027-06-30")
    r8 = vigilancia.registrar_inf_cv_08(con, s["cv"], 2026, "2027-08-20")
    assert r8["cuadran"] is True


def test_panel_cv_pendiente_y_vencido(cargado, s):
    con = cargado
    tesoreria.registrar_arqueo(con, s["cv"], "2026-10-15", "5000", "0", "Jefe de Finanzas y Comercialización")
    medio = {c["codigo"]: c for c in vigilancia.panel_cv(con, s["cv"], 2026, "2026-12-01")["controles"]}
    assert medio["INF-CV-03"]["realizados"] == 1 and medio["INF-CV-03"]["semaforo"] == "AMARILLO"
    assert medio["INF-CV-07"]["requeridos"] == 5 and medio["INF-CV-07"]["semaforo"] == "ROJO"
    fin = {c["codigo"]: c for c in vigilancia.panel_cv(con, s["cv"], 2026, "2027-07-20")["controles"]}
    assert fin["INF-CV-03"]["semaforo"] == "ROJO" and fin["INVENTARIO"]["semaforo"] == "ROJO"
    with pytest.raises(AutorizacionError):
        vigilancia.panel_cv(con, s["ut"], 2026)
