"""Identidad institucional para los reportes oficiales.

El membrete de los PDF contables se arma con datos de la base (regla 2: nada
fijo en el código):

* Nombre y ubicación de la entidad: ``config`` (identidad del sistema).
* NIT: parámetro ``entidad_nit`` (MAN-006 Nota 1.1 deja el NIT por completar:
  «[XXXXXXXXX]»; se registra en «Parámetros normativos» con acta del CA).
* RAU: el último Certificado RAU registrado en el módulo RAU (Fase 11); si no
  hay, «En trámite».
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

import config
from db.conexion import ejercicio_de_fecha_bd, obtener_parametros_vigentes
from reportes.pdf_contable import Emision, Membrete
from servicios.excepciones import ReglaNegocioError
from servicios.seguridad import Sesion

NOMBRE_CARGO = {"GG": "Gerencia General", "JF": "Jefe de Finanzas y Comercialización", "UT": "Unidad Técnica",
                "CA": "Consejo de Administración", "CV": "Consejo de Vigilancia", "AG": "Asamblea General"}


def membrete(con: sqlite3.Connection, fecha: date | None = None) -> Membrete:
    """Membrete vigente a la fecha (NIT del parámetro y último Certificado RAU)."""
    f = fecha or date.today()
    try:
        p = obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, f))
    except ReglaNegocioError:
        p = {}
    nit = str(p.get("entidad_nit") or "Por registrar")
    fila = con.execute("SELECT numero FROM certificados_rau WHERE fecha_emision <= ? ORDER BY fecha_emision DESC, "
                       "id DESC LIMIT 1", (f.isoformat(),)).fetchone()
    ubicacion = str(p.get("entidad_ubicacion") or "Caranavi, La Paz – Bolivia")
    return Membrete(entidad=config.NOMBRE_ENTIDAD.upper(), ubicacion=ubicacion, nit=nit,
                    rau=fila[0] if fila else "En trámite")


def emision(sesion: Sesion, momento: datetime | None = None) -> Emision:
    """Datos de emisión para el pie de página (usuario y cargo de la sesión)."""
    return Emision(usuario=sesion.nombre_completo, cargo=sesion.cargo or NOMBRE_CARGO.get(sesion.rol, sesion.rol),
                   momento=(momento or datetime.now()).replace(microsecond=0))
