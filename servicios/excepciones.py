"""Excepciones propias del ERP Pollolandia.

Todas heredan de :class:`ErrorPollolandia` para poder capturarlas en bloque
desde la capa de presentación y mostrar mensajes claros en español.
"""
from __future__ import annotations

from typing import Any


class ErrorPollolandia(Exception):
    """Excepción base del ERP.

    Atributos:
        mensaje: texto claro en español para el usuario.
        codigo: identificador corto y estable (útil para auditoría y pruebas).
        detalle: datos adicionales opcionales (diccionario) para diagnóstico.
    """

    codigo_defecto: str = "ERROR"

    def __init__(
        self,
        mensaje: str,
        *,
        codigo: str | None = None,
        detalle: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.mensaje: str = mensaje
        self.codigo: str = codigo or self.codigo_defecto
        self.detalle: dict[str, Any] = dict(detalle or {})

    def __str__(self) -> str:
        return self.mensaje

    def __repr__(self) -> str:
        return f"{type(self).__name__}(codigo={self.codigo!r}, mensaje={self.mensaje!r})"


class ReglaNegocioError(ErrorPollolandia):
    """Se intentó una operación que viola una regla normativa o de política
    (por ejemplo, orden de compra en día distinto de lunes)."""

    codigo_defecto = "REGLA_NEGOCIO"


class AutorizacionError(ErrorPollolandia):
    """Falta autorización, el rol no tiene facultad o la firma mancomunada
    está incompleta (CI-FIN-09, POL-04)."""

    codigo_defecto = "AUTORIZACION"


class PeriodoCerradoError(ErrorPollolandia):
    """Se intentó registrar o modificar un hecho en un período contable cerrado."""

    codigo_defecto = "PERIODO_CERRADO"


class DatosInvalidosError(ErrorPollolandia):
    """Dato de entrada con tipo, formato o rango inválido."""

    codigo_defecto = "DATOS_INVALIDOS"
