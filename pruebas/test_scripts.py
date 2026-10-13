"""Pruebas de los scripts de línea de comandos de la Fase 13 (respaldo
automático y restauración de la base en uso)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import config
from scripts import respaldo_automatico, restaurar_respaldo


def test_respaldo_automatico_y_restauracion_por_linea_de_comandos(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(config.ENV_DIR_DATOS, str(tmp_path / "datos"))
    monkeypatch.setenv(config.ENV_DIR_ARCHIVO, str(tmp_path / "archivo"))
    monkeypatch.setenv(config.ENV_RESPALDO_EXTERNO, str(tmp_path / "usb"))
    monkeypatch.setenv(config.ENV_RESPALDO_FUERA_SITIO, str(tmp_path / "nube"))
    ruta = tmp_path / "erp.db"
    assert respaldo_automatico.main(["--ruta", str(ruta)]) == 0
    salida = capsys.readouterr().out
    assert "DIARIO" in salida and "3 de 3 copias" in salida
    assert respaldo_automatico.main(["--ruta", str(ruta)]) == 0
    assert "No hay respaldos pendientes" in capsys.readouterr().out
    assert respaldo_automatico.main(["--ruta", str(ruta), "--manual"]) == 0
    capsys.readouterr()
    paquete = next((tmp_path / "nube" / "diario").glob("*.zip"))
    assert restaurar_respaldo.main([str(paquete), "--ruta", str(ruta)]) == 2          # falta --confirmar
    assert restaurar_respaldo.main([str(paquete), "--ruta", str(ruta), "--confirmar"]) == 0
    salida = capsys.readouterr().out
    assert "[OK] PRAGMA integrity_check" in salida and "Copia de la base anterior" in salida
    assert list(tmp_path.glob("erp.antes_de_restaurar_*.db"))
    con = sqlite3.connect(ruta)
    try:
        assert con.execute("SELECT MAX(version) FROM esquema_version").fetchone()[0] == 13
    finally:
        con.close()
    assert restaurar_respaldo.main([str(Path(tmp_path / "no_existe.zip")), "--ruta", str(ruta), "--confirmar"]) == 1
