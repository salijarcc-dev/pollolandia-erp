"""Conexión a SQLite, transacciones, conversión de montos y lectura de parámetros (Fases 0-2).

Reglas técnicas obligatorias: ``PRAGMA foreign_keys=ON`` y ``journal_mode=WAL``.
Los montos se guardan como ENTEROS EN CENTAVOS (columnas ``*_cent``).
"""
from __future__ import annotations

import itertools
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import config
from servicios.excepciones import DatosInvalidosError, ErrorPollolandia, ReglaNegocioError
from utils.formato import a_fecha, redondear_bs

log = logging.getLogger(f"{config.LOGGER_RAIZ}.db")

MEMORIA = ":memory:"


def obtener_conexion(ruta: str | Path | None = None) -> sqlite3.Connection:
    """Abre una conexión SQLite con claves foráneas y WAL activados.

    Args:
        ruta: archivo de BD; por defecto ``config.obtener_ruta_bd()``.
              Admite ``":memory:"`` (sin WAL).

    Raises:
        ErrorPollolandia: (código ``BD_CONEXION``) si no se puede abrir.
    """
    destino = str(ruta) if ruta is not None else str(config.obtener_ruta_bd())
    try:
        con = sqlite3.connect(destino, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        if destino != MEMORIA:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA synchronous = NORMAL")
        return con
    except sqlite3.Error as exc:
        log.exception("No se pudo abrir la base de datos %s", destino)
        raise ErrorPollolandia(
            f"No se pudo abrir la base de datos: {exc}",
            codigo="BD_CONEXION",
            detalle={"ruta": destino},
        ) from exc


@contextmanager
def conexion(ruta: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Contexto transaccional: ``commit`` si todo va bien, ``rollback`` ante
    cualquier excepción, y siempre cierra la conexión."""
    con = obtener_conexion(ruta)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


_contador_savepoint = itertools.count(1)


@contextmanager
def transaccion(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Unidad de trabajo atómica sobre una conexión ya abierta.

    * Si no hay transacción activa, abre ``BEGIN IMMEDIATE`` (bloquea escritores
      concurrentes, lo que protege el correlativo de asientos), confirma al
      terminar y hace ``rollback`` ante cualquier excepción.
    * Si ya hay una transacción activa (llamada anidada), usa un ``SAVEPOINT``
      para deshacer solo su propio trabajo sin romper la transacción externa.
    """
    if con.in_transaction:
        nombre = f"sp_pollolandia_{next(_contador_savepoint)}"
        con.execute(f"SAVEPOINT {nombre}")
        try:
            yield con
            con.execute(f"RELEASE SAVEPOINT {nombre}")
        except Exception:
            con.execute(f"ROLLBACK TO SAVEPOINT {nombre}")
            con.execute(f"RELEASE SAVEPOINT {nombre}")
            raise
    else:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise


def verificar_conexion(ruta: str | Path | None = None) -> dict[str, Any]:
    """Diagnóstico de la BD. Nunca lanza excepciones.

    Returns:
        Diccionario con: ``ok``, ``ruta``, ``existia``, ``foreign_keys``,
        ``journal_mode``, ``version_sqlite`` y ``error`` (``None`` si todo bien).
    """
    destino = str(ruta) if ruta is not None else str(config.obtener_ruta_bd())
    resultado: dict[str, Any] = {
        "ok": False,
        "ruta": destino,
        "existia": destino == MEMORIA or Path(destino).exists(),
        "foreign_keys": None,
        "journal_mode": None,
        "version_sqlite": sqlite3.sqlite_version,
        "error": None,
    }
    try:
        with conexion(destino) as con:
            resultado["foreign_keys"] = bool(con.execute("PRAGMA foreign_keys").fetchone()[0])
            resultado["journal_mode"] = str(con.execute("PRAGMA journal_mode").fetchone()[0])
            con.execute("SELECT 1").fetchone()
        resultado["ok"] = True
    except Exception as exc:  # noqa: BLE001 - diagnóstico: se reporta, no se propaga
        resultado["error"] = str(exc)
        log.error("Verificación de BD fallida: %s", exc)
    return resultado


# ==========================================================================
# MONTOS EN CENTAVOS
# ==========================================================================


def a_centavos(valor: Any) -> int:
    """Convierte un monto (Decimal, int, float o str) a centavos enteros,
    con redondeo ROUND_HALF_UP. ``Bs 1.234,56`` -> ``123456``."""
    return int(redondear_bs(valor).scaleb(config.DECIMALES_BS))


def de_centavos(centavos: int) -> Decimal:
    """Convierte centavos enteros a ``Decimal`` con 2 decimales."""
    if isinstance(centavos, bool) or not isinstance(centavos, int):
        raise DatosInvalidosError(f"Los centavos deben ser un entero: {centavos!r}.")
    return Decimal(centavos).scaleb(-config.DECIMALES_BS)


# ==========================================================================
# PARÁMETROS NORMATIVOS (con vigencia por ejercicio)
# ==========================================================================

_VERDADEROS = {"1", "true", "si", "sí", "verdadero"}
_FALSOS = {"0", "false", "no", "falso"}


def convertir_valor_parametro(valor: str, tipo: str, clave: str = "parámetro") -> int | Decimal | str | bool:
    """Convierte el texto almacenado al tipo declarado.

    Raises:
        DatosInvalidosError: si el valor no corresponde al tipo.
    """
    try:
        if tipo == "ENTERO":
            return int(valor)
        if tipo == "DECIMAL":
            return Decimal(valor)
        if tipo == "BOOLEANO":
            texto = str(valor).strip().lower()
            if texto in _VERDADEROS:
                return True
            if texto in _FALSOS:
                return False
            raise ValueError(valor)
        if tipo == "TEXTO":
            return str(valor)
    except (ValueError, ArithmeticError) as exc:
        raise DatosInvalidosError(
            f"El valor {valor!r} del parámetro '{clave}' no es de tipo {tipo}."
        ) from exc
    raise DatosInvalidosError(f"Tipo de parámetro desconocido: {tipo!r}.")


def obtener_parametro(
    con: sqlite3.Connection, clave: str, ejercicio: int
) -> int | Decimal | str | bool:
    """Lee el valor vigente de un parámetro para un ejercicio.

    Args:
        con: conexión abierta.
        clave: nombre del parámetro (p. ej. ``'sme'``).
        ejercicio: año del ejercicio (2026 = 1-jul-2026 a 30-jun-2027).

    Raises:
        ReglaNegocioError: (código ``PARAMETRO_NO_ENCONTRADO``) si no hay valor vigente.
    """
    fila = con.execute(
        """SELECT valor, tipo FROM parametros
            WHERE clave = ? AND ejercicio_desde <= ?
              AND (ejercicio_hasta IS NULL OR ejercicio_hasta >= ?)""",
        (clave, ejercicio, ejercicio),
    ).fetchone()
    if fila is None:
        raise ReglaNegocioError(
            f"No existe el parámetro '{clave}' vigente para el ejercicio {ejercicio}.",
            codigo="PARAMETRO_NO_ENCONTRADO",
            detalle={"clave": clave, "ejercicio": ejercicio},
        )
    return convertir_valor_parametro(fila["valor"], fila["tipo"], clave)


def obtener_parametros_vigentes(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    """Devuelve todos los parámetros vigentes de un ejercicio como diccionario."""
    filas = con.execute(
        """SELECT clave, valor, tipo FROM parametros
            WHERE ejercicio_desde <= ? AND (ejercicio_hasta IS NULL OR ejercicio_hasta >= ?)
            ORDER BY clave""",
        (ejercicio, ejercicio),
    ).fetchall()
    return {f["clave"]: convertir_valor_parametro(f["valor"], f["tipo"], f["clave"]) for f in filas}


def buscar_periodo(con: sqlite3.Connection, fecha: Any) -> sqlite3.Row:
    """Devuelve el período contable (fila de ``periodos``) que contiene la fecha.

    Raises:
        ReglaNegocioError: (``PERIODO_NO_EXISTE``) si la fecha no pertenece a ningún período.
    """
    f: date = a_fecha(fecha)
    fila = con.execute(
        "SELECT * FROM periodos WHERE fecha_inicio <= ? AND fecha_fin >= ?",
        (f.isoformat(), f.isoformat()),
    ).fetchone()
    if fila is None:
        raise ReglaNegocioError(
            f"No existe un período contable para la fecha {f.isoformat()}.",
            codigo="PERIODO_NO_EXISTE",
        )
    return fila


def ejercicio_de_fecha_bd(con: sqlite3.Connection, fecha: Any) -> int:
    """Devuelve el ejercicio (tabla ``ejercicios``) que contiene la fecha.

    Sustituye al cálculo con constantes de ``config`` (observación O-2 del
    cierre de la Fase 1): el calendario vigente es el que está en la base.

    Raises:
        ReglaNegocioError: (``EJERCICIO_NO_EXISTE``) si la fecha no pertenece a
        ningún ejercicio registrado.
    """
    f: date = a_fecha(fecha)
    fila = con.execute(
        "SELECT ejercicio FROM ejercicios WHERE fecha_inicio <= ? AND fecha_fin >= ?",
        (f.isoformat(), f.isoformat()),
    ).fetchone()
    if fila is None:
        raise ReglaNegocioError(
            f"La fecha {f.isoformat()} no pertenece a ningún ejercicio registrado.",
            codigo="EJERCICIO_NO_EXISTE",
        )
    return int(fila["ejercicio"])


def ejercicio_vigente_o_ultimo(con: sqlite3.Connection, fecha: Any) -> int:
    """Ejercicio de la fecha; si la fecha está fuera del calendario, el último
    ejercicio que empezó antes de ella (o el primero registrado).

    Se usa solo para leer parámetros técnicos de sesión (login), que deben
    funcionar aunque el reloj del equipo quede fuera del calendario contable.
    """
    try:
        return ejercicio_de_fecha_bd(con, fecha)
    except ReglaNegocioError:
        f = a_fecha(fecha).isoformat()
        fila = con.execute(
            "SELECT MAX(ejercicio) FROM ejercicios WHERE fecha_inicio <= ?", (f,)
        ).fetchone()
        if fila and fila[0] is not None:
            return int(fila[0])
        fila = con.execute("SELECT MIN(ejercicio) FROM ejercicios").fetchone()
        if fila and fila[0] is not None:
            return int(fila[0])
        raise
