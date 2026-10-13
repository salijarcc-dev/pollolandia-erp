"""Pruebas de las excepciones propias."""
from __future__ import annotations

import pytest

from servicios.excepciones import (
    AutorizacionError,
    DatosInvalidosError,
    ErrorPollolandia,
    PeriodoCerradoError,
    ReglaNegocioError,
)


@pytest.mark.parametrize(
    "clase, codigo",
    [
        (ReglaNegocioError, "REGLA_NEGOCIO"),
        (AutorizacionError, "AUTORIZACION"),
        (PeriodoCerradoError, "PERIODO_CERRADO"),
        (DatosInvalidosError, "DATOS_INVALIDOS"),
    ],
)
def test_codigo_y_herencia(clase, codigo):
    exc = clase("mensaje de prueba", detalle={"campo": 1})
    assert isinstance(exc, ErrorPollolandia)
    assert exc.codigo == codigo
    assert str(exc) == "mensaje de prueba"
    assert exc.detalle == {"campo": 1}


def test_codigo_personalizado():
    assert ReglaNegocioError("x", codigo="OC_NO_LUNES").codigo == "OC_NO_LUNES"


def test_se_captura_por_base():
    with pytest.raises(ErrorPollolandia):
        raise PeriodoCerradoError("Período cerrado")
