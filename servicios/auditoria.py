"""Bitácora de auditoría inmutable (Fase 2, regla 5 del proyecto).

Toda operación del ERP deja constancia de: usuario, rol, fecha y hora, tabla,
registro, acción y valores antes y después (JSON). La tabla ``auditoria`` es
inmutable por triggers (no admite UPDATE ni DELETE).

Este módulo es lógica pura: no importa ``streamlit``.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

from servicios.excepciones import DatosInvalidosError
from utils.formato import a_fecha

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado
    from servicios.seguridad import Sesion

ACCIONES_VALIDAS: frozenset[str] = frozenset({
    "INSERT", "UPDATE", "DELETE", "REVERSA", "LOGIN", "LOGOUT", "AUTORIZACION",
    "CIERRE", "CARGA_INICIAL", "OTRO",
})

USUARIO_SISTEMA = "SISTEMA"


def _a_serializable(valor: Any) -> Any:
    """Convierte tipos del ERP a tipos que JSON puede representar sin pérdida."""
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, sqlite3.Row):
        return {k: valor[k] for k in valor.keys()}
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        return dataclasses.asdict(valor)
    if isinstance(valor, (set, frozenset, tuple)):
        return list(valor)
    raise TypeError(f"Tipo no serializable en auditoría: {type(valor).__name__}")


def a_json(valor: Any) -> str | None:
    """Serializa a JSON (UTF-8 legible). ``None`` se guarda como NULL."""
    if valor is None:
        return None
    return json.dumps(valor, ensure_ascii=False, default=_a_serializable, sort_keys=True)


def registrar_evento(
    con: sqlite3.Connection,
    *,
    usuario: str,
    rol: str | None,
    tabla: str,
    accion: str,
    registro_id: Any = None,
    antes: Any = None,
    despues: Any = None,
    detalle: str | None = None,
) -> int:
    """Escribe un evento en la bitácora (dentro de la transacción en curso).

    No confirma la transacción: el evento queda atado a la operación que lo
    origina, de modo que si ésta se revierte, el evento también.

    Raises:
        DatosInvalidosError: si faltan usuario/tabla o la acción no es válida.

    Returns:
        ``id`` del evento registrado.
    """
    if not isinstance(usuario, str) or not usuario.strip():
        raise DatosInvalidosError("La auditoría exige el usuario que realiza la operación.")
    if not isinstance(tabla, str) or not tabla.strip():
        raise DatosInvalidosError("La auditoría exige la tabla afectada.")
    if accion not in ACCIONES_VALIDAS:
        raise DatosInvalidosError(f"Acción de auditoría no válida: {accion!r}.")
    cur = con.execute(
        """INSERT INTO auditoria (usuario, rol, tabla, registro_id, accion,
                                  valores_antes, valores_despues, detalle)
           VALUES (?,?,?,?,?,?,?,?)""",
        (usuario.strip(), rol, tabla.strip(), None if registro_id is None else str(registro_id),
         accion, a_json(antes), a_json(despues), detalle),
    )
    return int(cur.lastrowid)


def registrar(
    con: sqlite3.Connection,
    sesion: "Sesion",
    tabla: str,
    accion: str,
    registro_id: Any = None,
    antes: Any = None,
    despues: Any = None,
    detalle: str | None = None,
) -> int:
    """Atajo de :func:`registrar_evento` que toma usuario y rol de la sesión."""
    return registrar_evento(
        con, usuario=sesion.nombre_usuario, rol=sesion.rol, tabla=tabla, accion=accion,
        registro_id=registro_id, antes=antes, despues=despues, detalle=detalle,
    )


def diferencias(antes: Mapping[str, Any] | None, despues: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Campos que cambiaron entre dos estados: ``{campo: {"antes": x, "despues": y}}``."""
    a = dict(antes or {})
    d = dict(despues or {})
    return {
        k: {"antes": a.get(k), "despues": d.get(k)}
        for k in sorted(set(a) | set(d))
        if a.get(k) != d.get(k)
    }


def consultar_bitacora(
    con: sqlite3.Connection,
    sesion: "Sesion",
    *,
    tabla: str | None = None,
    usuario: str | None = None,
    accion: str | None = None,
    desde: Any = None,
    hasta: Any = None,
    limite: int = 500,
) -> list[dict[str, Any]]:
    """Consulta la bitácora con filtros. Requiere permiso ``auditoria/VER``
    (Gerencia General y Consejo de Vigilancia).

    Raises:
        AutorizacionError: si el rol no puede ver la bitácora.
        DatosInvalidosError: si el límite o las fechas no son válidos.
    """
    from servicios.seguridad import exigir_permiso  # import diferido: evita ciclo

    exigir_permiso(con, sesion, "auditoria", "VER")
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= 10_000:
        raise DatosInvalidosError("El límite de la consulta debe estar entre 1 y 10.000 registros.")
    condiciones: list[str] = []
    args: list[Any] = []
    if tabla:
        condiciones.append("tabla = ?")
        args.append(tabla)
    if usuario:
        condiciones.append("usuario = ?")
        args.append(usuario)
    if accion:
        if accion not in ACCIONES_VALIDAS:
            raise DatosInvalidosError(f"Acción de auditoría no válida: {accion!r}.")
        condiciones.append("accion = ?")
        args.append(accion)
    if desde is not None:
        condiciones.append("fecha_hora >= ?")
        args.append(a_fecha(desde, "desde").isoformat())
    if hasta is not None:
        condiciones.append("fecha_hora < date(?, '+1 day')")
        args.append(a_fecha(hasta, "hasta").isoformat())
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    filas = con.execute(
        f"""SELECT id, fecha_hora, usuario, rol, tabla, registro_id, accion,
                   valores_antes, valores_despues, detalle
              FROM auditoria {where} ORDER BY id DESC LIMIT ?""",
        (*args, limite),
    ).fetchall()
    return [
        {
            **{k: f[k] for k in f.keys() if k not in ("valores_antes", "valores_despues")},
            "valores_antes": json.loads(f["valores_antes"]) if f["valores_antes"] else None,
            "valores_despues": json.loads(f["valores_despues"]) if f["valores_despues"] else None,
        }
        for f in filas
    ]
