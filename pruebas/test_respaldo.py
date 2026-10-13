"""Pruebas del respaldo 3-2-1 y de la prueba de restauración (Fase 13).

MAN-ARC-01 §5.2: 3 copias (equipo, disco externo, fuera del sitio), 2 medios,
1 fuera del sitio; copia incremental diaria, completa semanal y de cierre
mensual y anual; prueba de restauración semestral con acta verificada por el
Consejo de Vigilancia.
"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

import pytest

import config
from db.conexion import obtener_conexion
from pruebas import escenario_2026 as esc
from reportes import formatos_admin
from reportes.formatos_tesoreria import Contexto
from servicios import respaldo
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

CTX = Contexto(fecha=date(2026, 10, 12), elaborado_por="Jefe de Finanzas", cargo="Jefe de Finanzas", ciclo="")


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    """Datos, archivo digital y los tres destinos en carpetas temporales."""
    monkeypatch.setenv(config.ENV_DIR_DATOS, str(tmp_path / "datos"))
    monkeypatch.setenv(config.ENV_DIR_ARCHIVO, str(tmp_path / "archivo"))
    monkeypatch.setenv(config.ENV_RESPALDO_EXTERNO, str(tmp_path / "disco_externo"))
    monkeypatch.setenv(config.ENV_RESPALDO_FUERA_SITIO, str(tmp_path / "nube"))
    (tmp_path / "archivo" / "ING").mkdir(parents=True)
    (tmp_path / "archivo" / "ING" / "FIN-ING-03_0001.pdf").write_bytes(b"%PDF-1.4 nota de entrega 1")
    return tmp_path


def test_respaldo_diario_en_tres_copias_e_incremental(con, s, entorno):
    r = respaldo.crear_respaldo(con, s["jf"], "DIARIO", "2026-10-12")
    assert r["copias"] == 3 and r["referencia"] == "2026-10-12" and r["incremental"] is True
    assert r["documentos"] == 1
    for ubicacion in respaldo.UBICACIONES:
        ruta = Path(r["ubicaciones"][ubicacion])
        assert ruta.exists() and respaldo.sha256_archivo(ruta) == r["sha256"]
    with zipfile.ZipFile(r["ubicaciones"]["LOCAL"]) as z:
        manifiesto = json.loads(z.read("manifiesto.json"))
        assert "bd/pollolandia.db" in z.namelist() and "archivo/ING/FIN-ING-03_0001.pdf" in z.namelist()
    assert manifiesto["conteos"]["asientos"] == 1 and manifiesto["conteos"]["debe_cent"] == \
        manifiesto["conteos"]["haber_cent"] == 53_430_000           # apertura: capital Bs 534.300
    with pytest.raises(ReglaNegocioError) as e:
        respaldo.crear_respaldo(con, s["jf"], "DIARIO", "2026-10-12")
    assert e.value.codigo == "RESPALDO_DUPLICADO"
    # El día siguiente solo lleva los documentos nuevos (incremental).
    (entorno / "archivo" / "ING" / "FIN-ING-03_0002.pdf").write_bytes(b"%PDF-1.4 nota de entrega 2")
    r2 = respaldo.crear_respaldo(con, None, "DIARIO", "2026-10-13")
    with zipfile.ZipFile(r2["ubicaciones"]["LOCAL"]) as z:
        documentos = [n for n in z.namelist() if n.startswith("archivo/")]
    assert documentos == ["archivo/ING/FIN-ING-03_0002.pdf"] and r2["documentos"] == 1
    # El semanal es completo.
    semanal = respaldo.crear_respaldo(con, s["gg"], "SEMANAL", "2026-10-18")
    assert semanal["documentos"] == 2 and semanal["incremental"] is False and semanal["referencia"] == "2026-W42"
    assert con.execute("SELECT COUNT(*) FROM auditoria WHERE tabla = 'respaldos' AND accion = 'INSERT'"
                       ).fetchone()[0] == 3
    with pytest.raises(AutorizacionError):
        respaldo.crear_respaldo(con, s["ut"], "MANUAL")
    with pytest.raises(AutorizacionError):
        respaldo.crear_respaldo(con, s["cv"], "MANUAL")


def test_sin_destinos_externos_la_copia_local_basta_pero_el_panel_alerta(con, s, entorno, monkeypatch):
    monkeypatch.delenv(config.ENV_RESPALDO_EXTERNO)
    monkeypatch.delenv(config.ENV_RESPALDO_FUERA_SITIO)
    r = respaldo.crear_respaldo(con, s["jf"], "MANUAL", "2026-10-12")
    assert r["copias"] == 1 and r["ubicaciones"]["EXTERNO"] is None
    panel = {c["control"]: c["semaforo"] for c in respaldo.estado_321(con, s["cv"], "2026-10-12")["controles"]}
    assert panel["Destinos configurados (3 copias)"] == "ROJO"
    assert panel["Copia fuera del sitio (1)"] == "ROJO"
    assert panel["Copias del último respaldo"] == "ROJO"
    assert panel["Prueba de restauración semestral"] == "ROJO"


def test_pendientes_diario_semanal_mensual_y_anual(con, s, entorno):
    domingo, lunes = date(2026, 10, 18), date(2026, 10, 19)               # respaldo_dia_semanal = 7 (domingo)
    assert respaldo.respaldos_pendientes(con, lunes) == [("DIARIO", None), ("SEMANAL", None)]   # nunca hubo semanal
    respaldo.crear_respaldo(con, None, "SEMANAL", lunes)
    assert respaldo.respaldos_pendientes(con, lunes) == [("DIARIO", None)]
    esc.cargar_escenario_2026(con, s)
    esc.cerrar_meses(con, s, 2026, 2)
    # Domingo 25-oct es de la misma semana ISO (W43) que el semanal del lunes 19: no se repite.
    assert respaldo.respaldos_pendientes(con, domingo + date.resolution * 7) == \
        [("DIARIO", None), ("MENSUAL", "2026-07"), ("MENSUAL", "2026-08")]
    r = respaldo.ejecutar_automatico(con, "2026-11-01")                     # domingo de la semana W44
    assert [(x["tipo"], x["referencia"]) for x in r["respaldos"]] == \
        [("DIARIO", "2026-11-01"), ("SEMANAL", "2026-W44"), ("MENSUAL", "2026-07"), ("MENSUAL", "2026-08")]
    assert r["errores"] == []
    assert respaldo.respaldos_pendientes(con, "2026-11-01") == []


def test_cierre_anual_genera_el_respaldo_anual(con, s, entorno):
    esc.cargar_escenario_2026(con, s)
    esc.cerrar_ejercicio_completo(con, s)
    pendientes = respaldo.respaldos_pendientes(con, "2027-07-05")
    assert ("ANUAL", "E2026") in pendientes and sum(1 for t, _ in pendientes if t == "MENSUAL") == 12
    anual = respaldo.crear_respaldo(con, None, "ANUAL", "2027-07-05", "E2026")
    assert anual["conteos"]["asientos"] == con.execute("SELECT COUNT(*) FROM asientos").fetchone()[0]


def test_prueba_de_restauracion_conforme_acta_y_visto_del_cv(con, s, entorno):
    esc.cargar_escenario_2026(con, s)
    r = respaldo.crear_respaldo(con, s["jf"], "SEMANAL", "2026-10-18")
    acta = respaldo.probar_restauracion(con, s["jf"], r["id"], "FUERA_SITIO", "2026-10-19")
    assert acta["resultado"] == "CONFORME" and len(acta["verificaciones"]) == 12
    assert all(v["cumple"] for v in acta["verificaciones"])
    assert any("integrity_check" in v["control"] for v in acta["verificaciones"])
    with pytest.raises(AutorizacionError):
        respaldo.verificar_acta(con, s["jf"], acta["id"])
    with pytest.raises(AutorizacionError):
        respaldo.verificar_acta(con, s["gg"], acta["id"])
    respaldo.verificar_acta(con, s["cv"], acta["id"])
    with pytest.raises(ReglaNegocioError):
        respaldo.verificar_acta(con, s["cv2"], acta["id"])
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE actas_restauracion SET resultado = 'FALLIDA' WHERE id = ?", (acta["id"],))
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM respaldos")
    pdf = formatos_admin.acta_restauracion(respaldo.obtener_acta(con, s["cv"], acta["id"]), CTX)
    assert pdf.startswith(b"%PDF") and b"P\\341gina 1 de" in pdf
    panel = {c["control"]: c["semaforo"] for c in respaldo.estado_321(con, s["gg"], "2026-10-19")["controles"]}
    assert panel["Prueba de restauración semestral"] == "VERDE"
    assert panel["Copias del último respaldo"] == "VERDE" and panel["Destinos configurados (3 copias)"] == "VERDE"
    # Seis meses después la prueba vuelve a ser exigible.
    vencido = {c["control"]: c["semaforo"] for c in respaldo.estado_321(con, s["gg"], "2027-04-19")["controles"]}
    assert vencido["Prueba de restauración semestral"] == "ROJO"
    assert respaldo.proxima_prueba(con, "2026-10-19") == date(2027, 4, 19)


def test_copia_alterada_da_acta_fallida_que_exige_observacion(con, s, entorno):
    r = respaldo.crear_respaldo(con, s["jf"], "MANUAL", "2026-10-12")
    externo = Path(r["ubicaciones"]["EXTERNO"])
    externo.write_bytes(externo.read_bytes()[:-200] + b"x" * 200)          # copia dañada en el disco externo
    acta = respaldo.probar_restauracion(con, s["jf"], r["id"], "EXTERNO", "2026-10-12")
    assert acta["resultado"] == "FALLIDA"
    assert not next(v for v in acta["verificaciones"] if "SHA-256" in v["control"])["cumple"]
    with pytest.raises(DatosInvalidosError):
        respaldo.verificar_acta(con, s["cv"], acta["id"])
    respaldo.verificar_acta(con, s["cv"], acta["id"], "Reemplazar el disco externo y repetir la prueba")
    # La copia local sigue sana.
    assert respaldo.probar_restauracion(con, s["jf"], r["id"], "LOCAL", "2026-10-12")["resultado"] == "CONFORME"


def test_depuracion_por_plazo_conserva_el_registro(con, s, entorno):
    r = respaldo.crear_respaldo(con, None, "DIARIO", "2026-09-01")
    assert respaldo.depurar(con, "2026-10-01") == []                       # 30 días: aún vigente
    assert respaldo.depurar(con, "2026-10-02") == [r["id"]]
    assert all(not Path(p).exists() for p in r["ubicaciones"].values())
    fila = con.execute("SELECT estado, sha256 FROM respaldos WHERE id = ?", (r["id"],)).fetchone()
    assert fila["estado"] == "DEPURADO" and fila["sha256"] == r["sha256"]
    with pytest.raises(ReglaNegocioError):
        respaldo.probar_restauracion(con, s["jf"], r["id"])


def test_destinos_independientes_solo_por_la_gg(con, s, entorno, monkeypatch):
    monkeypatch.delenv(config.ENV_RESPALDO_EXTERNO)
    monkeypatch.delenv(config.ENV_RESPALDO_FUERA_SITIO)
    with pytest.raises(AutorizacionError):
        respaldo.guardar_destinos(con, s["jf"], str(entorno / "e"), str(entorno / "n"))
    with pytest.raises(DatosInvalidosError):
        respaldo.guardar_destinos(con, s["gg"], "relativo/disco", None)
    with pytest.raises(ReglaNegocioError):
        respaldo.guardar_destinos(con, s["gg"], str(config.obtener_directorio_respaldos() / "sub"), None)
    with pytest.raises(ReglaNegocioError):
        respaldo.guardar_destinos(con, s["gg"], str(entorno / "x"), str(entorno / "x"))
    d = respaldo.guardar_destinos(con, s["gg"], str(entorno / "usb"), str(entorno / "onedrive"))
    assert d["EXTERNO"] == (entorno / "usb").resolve() and d["FUERA_SITIO"] == (entorno / "onedrive").resolve()
    assert respaldo.crear_respaldo(con, s["jf"], "MANUAL", "2026-10-12")["copias"] == 3


def test_restaurar_la_base_en_uso_guarda_la_anterior(con, s, entorno, tmp_path):
    r = respaldo.crear_respaldo(con, s["jf"], "MANUAL", "2026-10-12")
    destino = tmp_path / "produccion.db"
    otra = obtener_conexion(destino)
    otra.execute("CREATE TABLE prueba (x INTEGER)")
    otra.commit()
    otra.close()
    resultado = respaldo.restaurar_produccion(Path(r["ubicaciones"]["LOCAL"]), destino)
    assert resultado["copia_previa"] and Path(resultado["copia_previa"]).exists()
    restaurada = sqlite3.connect(destino)
    try:
        assert restaurada.execute("SELECT COUNT(*) FROM asientos").fetchone()[0] == 1
        assert restaurada.execute("SELECT COUNT(*) FROM auditoria WHERE detalle LIKE 'Restauración de la base%'"
                                  ).fetchone()[0] == 1
    finally:
        restaurada.close()
    danado = tmp_path / "danado.zip"
    danado.write_bytes(b"no es un zip")
    with pytest.raises(ReglaNegocioError) as e:
        respaldo.restaurar_produccion(danado, destino)
    assert e.value.codigo == "RESPALDO_INVALIDO"
