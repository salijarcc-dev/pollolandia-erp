"""Pruebas de utils/formato.py con cifras de los documentos."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from servicios.excepciones import DatosInvalidosError
from utils import formato as f


# ----------------------------- montos -----------------------------------
@pytest.mark.parametrize(
    "valor, esperado",
    [
        (1234.56, "Bs 1.234,56"),
        (Decimal("0"), "Bs 0,00"),
        (24.24, "Bs 24,24"),
        (534300, "Bs 534.300,00"),
        (1234567.895, "Bs 1.234.567,90"),
        (Decimal("0.005"), "Bs 0,01"),
        (-50, "-Bs 50,00"),
        ("136413", "Bs 136.413,00"),
    ],
)
def test_formato_bs(valor, esperado):
    assert f.formato_bs(valor) == esperado


def test_formato_bs_sin_simbolo():
    assert f.formato_bs(1234.5, con_simbolo=False) == "1.234,50"


def test_redondeo_half_up():
    assert f.redondear_bs(Decimal("2.675")) == Decimal("2.68")
    assert f.redondear_bs(Decimal("2.665")) == Decimal("2.67")


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Bs 1.234,56", "1234.56"),
        ("1234,56", "1234.56"),
        ("1234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1.234.567,89", "1234567.89"),
        ("1.234", "1234.00"),
        ("1.5", "1.50"),
        ("3,03", "3.03"),
        ("Bs 24,24", "24.24"),
        ("-Bs 1.234,56", "-1234.56"),
        ("Bs -1.234,56", "-1234.56"),
        ("(Bs 500,00)", "-500.00"),
        ("  184.800 Bs ", "184800.00"),
    ],
)
def test_parsear_bs(texto, esperado):
    assert f.parsear_bs(texto) == Decimal(esperado)


@pytest.mark.parametrize("texto", ["", "   ", "abc", "1,234", "0.123", "1.2.34", "Bs", None, True])
def test_parsear_bs_invalido(texto):
    with pytest.raises(DatosInvalidosError):
        f.parsear_bs(texto)


@pytest.mark.parametrize("valor", ["0", "24.24", "-1234567.89", "534300", "0.01"])
def test_ida_y_vuelta(valor):
    d = Decimal(valor)
    assert f.parsear_bs(f.formato_bs(d)) == d


def test_a_decimal():
    assert f.a_decimal(0.1) == Decimal("0.1")
    assert f.a_decimal(" 5.25 ") == Decimal("5.25")
    for malo in (None, True, "abc", float("nan"), float("inf"), [1]):
        with pytest.raises(DatosInvalidosError):
            f.a_decimal(malo)


# ----------------------------- fechas -----------------------------------
def test_fechas():
    assert f.a_fecha("2026-07-01") == date(2026, 7, 1)
    assert f.a_fecha("2026-07-01T10:30:00") == date(2026, 7, 1)
    assert f.a_fecha(datetime(2026, 7, 1, 8)) == date(2026, 7, 1)
    assert f.fecha_iso(date(2026, 10, 9)) == "2026-10-09"
    assert f.fecha_pantalla("2026-10-09") == "09/10/2026"
    assert f.parsear_fecha_pantalla("31/10/2026") == date(2026, 10, 31)
    for malo in ("2026-13-01", "ayer", 20260701, None):
        with pytest.raises(DatosInvalidosError):
            f.a_fecha(malo)
    with pytest.raises(DatosInvalidosError):
        f.parsear_fecha_pantalla("")


def test_ejercicio():
    assert f.ejercicio_de_fecha("2026-06-30") == 2025
    assert f.ejercicio_de_fecha("2026-07-01") == 2026
    assert f.ejercicio_de_fecha("2027-06-30") == 2026
    assert f.inicio_ejercicio(2026) == date(2026, 7, 1)
    assert f.fin_ejercicio(2026) == date(2027, 6, 30)


# ------------------------- calendario hábil -----------------------------
def test_feriados_2026():
    fer = f.feriados_bolivia(2026)
    esperados = {
        date(2026, 1, 1), date(2026, 1, 22), date(2026, 2, 16), date(2026, 2, 17),
        date(2026, 4, 3), date(2026, 5, 1), date(2026, 6, 4), date(2026, 6, 21),
        date(2026, 8, 6), date(2026, 11, 2), date(2026, 12, 25),
    }
    assert fer == esperados


def test_pascua():
    assert f._pascua(2026) == date(2026, 4, 5)
    assert f._pascua(2027) == date(2027, 3, 28)


def test_feriado_adicional():
    assert f.es_dia_habil("2026-10-14")
    assert not f.es_dia_habil("2026-10-14", feriados_adicionales=["2026-10-14"])


def test_es_dia_habil():
    assert f.es_dia_habil("2026-07-01")          # miércoles
    assert not f.es_dia_habil("2026-07-04")      # sábado
    assert f.es_dia_habil("2026-07-04", sabado_habil=True)
    assert not f.es_dia_habil("2026-07-05")      # domingo
    assert not f.es_dia_habil("2026-08-06")      # feriado
    assert not f.es_dia_habil("2026-04-03")      # Viernes Santo


def test_sumar_y_restar_dias_habiles():
    assert f.sumar_dias_habiles("2026-07-03", 1) == date(2026, 7, 6)
    assert f.sumar_dias_habiles("2026-07-03", 0) == date(2026, 7, 3)
    assert f.sumar_dias_habiles("2026-07-06", -1) == date(2026, 7, 3)
    # Formulario 701: 15 días hábiles antes del 31-oct-2026 (sábado)
    assert f.restar_dias_habiles("2026-10-31", 15) == date(2026, 10, 12)
    # Salta feriado: 5-ago-2026 (miér) + 1 hábil = 7-ago (jue 6 es feriado)
    assert f.sumar_dias_habiles("2026-08-05", 1) == date(2026, 8, 7)


def test_dias_habiles_entre():
    assert f.dias_habiles_entre("2026-07-01", "2026-07-08") == 5
    assert f.dias_habiles_entre("2026-07-08", "2026-07-01") == -5
    assert f.dias_habiles_entre("2026-07-01", "2026-07-01") == 0


# ------------------------------ ciclo -----------------------------------
@pytest.mark.parametrize(
    "inicio, cierre",
    [
        ("2026-07-01", date(2026, 9, 11)),
        ("2026-09-12", date(2026, 11, 23)),
        ("2026-11-24", date(2027, 2, 4)),
        ("2027-02-05", date(2027, 4, 18)),
        ("2027-04-19", date(2027, 6, 30)),
    ],
)
def test_calendario_poa(inicio, cierre):
    assert f.fecha_cierre_ciclo(inicio) == cierre
    assert f.fecha_inicio_ciclo_siguiente(inicio) == cierre + (date(2026, 1, 2) - date(2026, 1, 1))


def test_cierre_no_inclusivo():
    assert f.fecha_cierre_ciclo("2026-07-01", inclusivo=False) == date(2026, 9, 12)


def test_cinco_ciclos_cubren_el_ejercicio():
    inicio = date(2026, 7, 1)
    for _ in range(5):
        cierre = f.fecha_cierre_ciclo(inicio)
        inicio = f.fecha_inicio_ciclo_siguiente(inicio)
    assert cierre == f.fin_ejercicio(2026)


@pytest.mark.parametrize("dias", [0, -1, 73.5, "73", True])
def test_dias_ciclo_invalidos(dias):
    with pytest.raises(DatosInvalidosError):
        f.fecha_cierre_ciclo("2026-07-01", dias)
