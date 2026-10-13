"""Revisión de seguridad de la Fase 13.

MAN-ARC-01 §5.2: contraseñas personales con cambio cada 90 días; Consejo de
Vigilancia solo lectura. Política de acceso: 3 intentos fallidos bloquean
15 minutos; sesión cerrada a los 30 minutos de inactividad. MOF §3.4: el JF no
autoriza, la GG no registra asientos.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import config
from pruebas.conftest import CLAVE
from reportes import formatos_admin
from reportes.formatos_tesoreria import Contexto
from servicios import contable, parametros, respaldo, revision_seguridad, seguridad
from servicios.excepciones import AutorizacionError

D = Decimal
CTX = Contexto(fecha=datetime(2026, 10, 12).date(), elaborado_por="Gerencia", cargo="Gerente General", ciclo="")


def _control(rev, nombre):
    return next(c for c in rev["controles"] if c["control"] == nombre)


def test_revision_base_cv_solo_lectura_y_segregacion(con, s):
    rev = revision_seguridad.revision(con, s["gg"], datetime(2026, 10, 12, 9, 0))
    assert _control(rev, "Consejo de Vigilancia solo lectura")["semaforo"] == "VERDE"
    assert _control(rev, "Incompatibilidades del MOF")["semaforo"] == "VERDE"
    assert _control(rev, "El Jefe de Finanzas no firma")["semaforo"] == "VERDE"
    assert _control(rev, "Bitácora de auditoría inmutable")["semaforo"] == "VERDE"
    assert _control(rev, "Cambio obligatorio de contraseña")["detalle"].startswith("Cada 90 días")
    assert "3 intentos fallidos bloquean el usuario 15 minutos" in _control(rev, "Bloqueo por intentos fallidos")["detalle"]
    assert _control(rev, "Cierre de sesión por inactividad")["detalle"] == "30 minutos."
    assert _control(rev, "Contraseñas cifradas con bcrypt")["semaforo"] == "VERDE"
    pdf = formatos_admin.informe_seguridad(rev, CTX)
    assert pdf.startswith(b"%PDF") and b"P\\341gina 1 de" in pdf
    # El CV también puede revisarla (fiscaliza); la UT no.
    assert revision_seguridad.revision(con, s["cv"])["controles"]
    with pytest.raises(AutorizacionError):
        revision_seguridad.revision(con, s["ut"])


def test_hash_con_costo_menor_al_configurado_se_informa(con, s, monkeypatch):
    # Las pruebas crean los usuarios con costo 4; producción usa 12 (config.BCRYPT_ROUNDS).
    monkeypatch.setattr(config, "BCRYPT_ROUNDS", 12)
    control = _control(revision_seguridad.revision(con, s["gg"]), "Contraseñas cifradas con bcrypt")
    assert control["semaforo"] == "AMARILLO" and "rflores" in control["detalle"]


def test_contrasenas_vencidas_y_usuarios_bloqueados(con, s):
    cambio = datetime.fromisoformat(con.execute("SELECT MAX(ultimo_cambio_password) FROM usuarios").fetchone()[0])
    antes = revision_seguridad.revision(con, s["gg"], cambio + timedelta(days=89))
    assert _control(antes, "Contraseñas vencidas")["semaforo"] == "VERDE"
    despues = revision_seguridad.revision(con, s["gg"], cambio + timedelta(days=90))
    assert _control(despues, "Contraseñas vencidas")["semaforo"] == "AMARILLO"
    assert "rflores" in _control(despues, "Contraseñas vencidas")["detalle"]
    # La caducidad obliga a cambiarla en el ingreso.
    assert seguridad.iniciar_sesion(con, "rflores", CLAVE, ahora=cambio + timedelta(days=90)).debe_cambiar_password
    ahora = datetime(2026, 10, 12, 8, 0)
    for _ in range(3):
        with pytest.raises(AutorizacionError):
            seguridad.iniciar_sesion(con, "granja", "Mala12345", ahora=ahora)
    rev = revision_seguridad.revision(con, s["gg"], ahora + timedelta(minutes=5))
    assert _control(rev, "Usuarios bloqueados ahora")["detalle"] == "granja"
    rev = revision_seguridad.revision(con, s["gg"], ahora + timedelta(minutes=16))
    assert _control(rev, "Usuarios bloqueados ahora")["semaforo"] == "VERDE"


def test_permiso_indebido_queda_neutralizado_y_se_informa(con, s):
    con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES ('CV', 'contabilidad', 'CREAR', 'error')")
    con.commit()
    rev = revision_seguridad.revision(con, s["gg"])
    assert _control(rev, "Consejo de Vigilancia solo lectura")["semaforo"] == "VERDE"   # la segregación lo bloquea
    neutralizado = _control(rev, "Permisos incompatibles en la matriz")
    assert neutralizado["semaforo"] == "AMARILLO" and "CV contabilidad/CREAR" in neutralizado["detalle"]
    with pytest.raises(AutorizacionError):
        contable.crear_asiento(con, s["cv"], "2026-10-12", "Intento del CV",
                               [{"cuenta": "1.1.01.05", "debe": D("1")}, {"cuenta": "1.1.01.03", "haber": D("1")}],
                               "PRUEBA", None, tipo="TRASPASO")


def test_cv_solo_lectura_en_las_funciones_de_la_fase_13(con, s):
    with pytest.raises(AutorizacionError):
        parametros.solicitar_cambio(con, s["cv"], "sme", "1", 2026, "x", "y")
    with pytest.raises(AutorizacionError):
        respaldo.crear_respaldo(con, s["cv"], "MANUAL")
    with pytest.raises(AutorizacionError):
        respaldo.guardar_destinos(con, s["cv"], None, None)
    assert seguridad.tiene_permiso(con, "CV", "respaldos", "VERIFICAR")
    assert seguridad.tiene_permiso(con, "CV", "parametros", "VER")
    assert not seguridad.tiene_permiso(con, "CV", "parametros", "EDITAR")
    assert not seguridad.tiene_permiso(con, "JF", "respaldos", "AUTORIZAR")
