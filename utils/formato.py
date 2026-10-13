"""Utilitarios de formato, fechas y calendario hábil de Bolivia (Fase 0).

* Montos: siempre ``Decimal`` con 2 decimales y redondeo ROUND_HALF_UP.
  Formato de pantalla boliviano: ``Bs 1.234,56``.
* Fechas: ISO ``AAAA-MM-DD`` internamente; ``dd/mm/aaaa`` en pantalla.
* Días hábiles: lunes a viernes, excluyendo feriados nacionales de Bolivia.
* Ciclo: fecha de cierre del ciclo productivo de 73 días.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Iterable

import config
from servicios.excepciones import DatosInvalidosError

CENTAVO: Decimal = Decimal(1).scaleb(-config.DECIMALES_BS)  # Decimal('0.01')

# ==========================================================================
# MONTOS
# ==========================================================================


def a_decimal(valor: Any, campo: str = "valor") -> Decimal:
    """Convierte ``int``, ``float``, ``str`` (punto decimal) o ``Decimal`` a
    ``Decimal`` finito. Los ``float`` se convierten vía ``str`` para evitar
    ruido binario (0.1 -> Decimal('0.1')).

    Raises:
        DatosInvalidosError: si el valor es ``None``, ``bool``, no numérico,
        ``NaN`` o infinito.
    """
    if valor is None or isinstance(valor, bool):
        raise DatosInvalidosError(f"El campo '{campo}' no es un monto válido: {valor!r}.")
    try:
        if isinstance(valor, Decimal):
            d = valor
        elif isinstance(valor, int):
            d = Decimal(valor)
        elif isinstance(valor, float):
            d = Decimal(str(valor))
        elif isinstance(valor, str):
            d = Decimal(valor.strip())
        else:
            raise DatosInvalidosError(
                f"El campo '{campo}' tiene un tipo no soportado: {type(valor).__name__}."
            )
    except InvalidOperation as exc:
        raise DatosInvalidosError(f"El campo '{campo}' no es un número válido: {valor!r}.") from exc
    if not d.is_finite():
        raise DatosInvalidosError(f"El campo '{campo}' debe ser un número finito: {valor!r}.")
    return d


def redondear_bs(valor: Any) -> Decimal:
    """Redondea a 2 decimales con ROUND_HALF_UP (regla 6)."""
    return a_decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def formato_bs(valor: Any, con_simbolo: bool = True) -> str:
    """Formatea un monto al estilo boliviano: ``Bs 1.234,56``.

    Los negativos se muestran como ``-Bs 1.234,56``.
    """
    d = redondear_bs(valor)
    negativo = d < 0
    entero, fraccion = f"{abs(d):.{config.DECIMALES_BS}f}".split(".")
    entero_agrupado = f"{int(entero):,}".replace(",", config.SEPARADOR_MILES)
    numero = f"{entero_agrupado}{config.SEPARADOR_DECIMAL}{fraccion}"
    if con_simbolo:
        numero = f"{config.SIMBOLO_MONEDA} {numero}"
    return f"-{numero}" if negativo else numero


def _normalizar_numero(s: str, original: str) -> tuple[str, str]:
    """Devuelve (parte_entera, parte_decimal) de una cadena con separadores."""

    def error(motivo: str) -> DatosInvalidosError:
        return DatosInvalidosError(f"No se pudo interpretar el monto {original!r}: {motivo}.")

    tiene_punto, tiene_coma = "." in s, "," in s
    if tiene_punto and tiene_coma:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        mil = "." if dec == "," else ","
    elif tiene_coma:
        dec, mil = (None, ",") if s.count(",") > 1 else (",", None)
    elif tiene_punto:
        if s.count(".") > 1:
            dec, mil = None, "."
        else:
            # Formato boliviano: "1.234" son miles; "1.5" / "1.50" son decimales.
            dec, mil = (None, ".") if len(s.split(".")[1]) == 3 else (".", None)
    else:
        dec = mil = None

    if dec:
        if s.count(dec) != 1:
            raise error("separador decimal repetido")
        entero, fraccion = s.split(dec)
    else:
        entero, fraccion = s, ""

    if mil:
        grupos = entero.split(mil)
        if not (1 <= len(grupos[0]) <= 3 and all(len(g) == 3 for g in grupos[1:])):
            raise error("separadores de miles mal ubicados")
        if grupos[0] == "0":
            raise error("ambiguo (¿miles o decimales?)")
        entero = "".join(grupos)

    if len(fraccion) > config.DECIMALES_BS:
        raise error(f"más de {config.DECIMALES_BS} decimales o formato ambiguo")
    if not entero.isdigit() or (fraccion and not fraccion.isdigit()):
        raise error("contiene caracteres no numéricos")
    return entero, fraccion


def parsear_bs(texto: Any) -> Decimal:
    """Interpreta un monto escrito por una persona y devuelve ``Decimal``.

    Acepta: ``"Bs 1.234,56"``, ``"1234,56"``, ``"1234.56"``, ``"1,234.56"``,
    ``"-Bs 50,00"``, ``"(Bs 500,00)"`` (negativo contable) y ``"1.234"``
    (= 1234, convención boliviana de miles).

    Raises:
        DatosInvalidosError: si el texto vacío, no numérico o ambiguo
        (p. ej. ``"1,234"`` o ``"0.123"``).
    """
    if isinstance(texto, (int, float, Decimal)) and not isinstance(texto, bool):
        return redondear_bs(texto)
    if not isinstance(texto, str):
        raise DatosInvalidosError(f"El monto debe ser texto o número: {texto!r}.")

    s = texto.strip()
    if not s:
        raise DatosInvalidosError("El monto está vacío.")

    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    s = re.sub(r"bs\.?", "", s, flags=re.IGNORECASE)
    s = s.replace("\u00a0", "").replace(" ", "")
    if s.startswith("-"):
        negativo, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]

    if not re.fullmatch(r"\d[\d.,]*", s):
        raise DatosInvalidosError(f"No se pudo interpretar el monto {texto!r}.")

    entero, fraccion = _normalizar_numero(s, texto)
    valor = Decimal(f"{entero}.{fraccion or '0'}")
    return redondear_bs(-valor if negativo else valor)


# ==========================================================================
# FECHAS
# ==========================================================================


def a_fecha(valor: Any, campo: str = "fecha") -> date:
    """Convierte ``date``, ``datetime`` o texto ISO (``AAAA-MM-DD``) a ``date``.

    Raises:
        DatosInvalidosError: si no es una fecha válida.
    """
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        texto = valor.strip()
        if len(texto) > 10 and texto[10] in " T":
            texto = texto[:10]
        try:
            return date.fromisoformat(texto)
        except ValueError as exc:
            raise DatosInvalidosError(
                f"El campo '{campo}' no es una fecha ISO válida (AAAA-MM-DD): {valor!r}."
            ) from exc
    raise DatosInvalidosError(f"El campo '{campo}' no es una fecha: {valor!r}.")


def fecha_iso(valor: Any) -> str:
    """Devuelve la fecha en ISO ``AAAA-MM-DD``."""
    return a_fecha(valor).isoformat()


def fecha_pantalla(valor: Any) -> str:
    """Devuelve la fecha como ``dd/mm/aaaa`` para mostrar en pantalla."""
    return a_fecha(valor).strftime(config.FORMATO_FECHA_PANTALLA)


def parsear_fecha_pantalla(texto: str) -> date:
    """Interpreta ``dd/mm/aaaa`` (o ISO) y devuelve ``date``."""
    if not isinstance(texto, str) or not texto.strip():
        raise DatosInvalidosError("La fecha está vacía.")
    t = texto.strip()
    try:
        return datetime.strptime(t, config.FORMATO_FECHA_PANTALLA).date()
    except ValueError:
        return a_fecha(t)


# ==========================================================================
# EJERCICIO CONTABLE (1-jul a 30-jun)
# ==========================================================================


def ejercicio_de_fecha(fecha: Any, mes_inicio: int = config.MES_INICIO_EJERCICIO) -> int:
    """Año que identifica al ejercicio de una fecha (ejercicio 2026 =
    1-jul-2026 a 30-jun-2027)."""
    f = a_fecha(fecha)
    return f.year if f.month >= mes_inicio else f.year - 1


def inicio_ejercicio(ejercicio: int, mes_inicio: int = config.MES_INICIO_EJERCICIO) -> date:
    """Primer día del ejercicio."""
    return date(ejercicio, mes_inicio, 1)


def fin_ejercicio(ejercicio: int, mes_inicio: int = config.MES_INICIO_EJERCICIO) -> date:
    """Último día del ejercicio."""
    return date(ejercicio + 1, mes_inicio, 1) - timedelta(days=1)


# ==========================================================================
# CALENDARIO HÁBIL DE BOLIVIA
# ==========================================================================


def _pascua(anio: int) -> date:
    """Domingo de Pascua (algoritmo gregoriano anónimo de Meeus/Jones/Butcher)."""
    a, b, c = anio % 19, anio // 100, anio % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(anio, mes, dia)


@lru_cache(maxsize=64)
def _feriados_base(anio: int) -> frozenset[date]:
    pascua = _pascua(anio)
    fijos = [(1, 1), (1, 22), (5, 1), (6, 21), (8, 6), (11, 2), (12, 25)]
    moviles = [
        pascua - timedelta(days=48),  # Lunes de Carnaval
        pascua - timedelta(days=47),  # Martes de Carnaval
        pascua - timedelta(days=2),   # Viernes Santo
        pascua + timedelta(days=60),  # Corpus Christi (jueves)
    ]
    return frozenset([date(anio, m, d) for m, d in fijos] + moviles)


def feriados_bolivia(anio: int, adicionales: Iterable[Any] = ()) -> frozenset[date]:
    """Feriados nacionales de Bolivia de un año.

    Incluye: Año Nuevo (1-ene), Estado Plurinacional (22-ene), Lunes y Martes
    de Carnaval, Viernes Santo, Día del Trabajo (1-may), Corpus Christi, Año
    Nuevo Andino Amazónico (21-jun), Día de la Patria (6-ago), Todos Santos
    (2-nov) y Navidad (25-dic).

    Los feriados que el Gobierno decrete adicionalmente (o los departamentales)
    se pasan en ``adicionales``. VALIDAR cada gestión contra los decretos.
    """
    extra = {a_fecha(x) for x in adicionales if a_fecha(x).year == anio}
    return _feriados_base(anio) | extra


def es_dia_habil(
    fecha: Any, feriados_adicionales: Iterable[Any] = (), sabado_habil: bool = False
) -> bool:
    """``True`` si la fecha es día hábil (lun-vie, o lun-sáb si ``sabado_habil``)
    y no es feriado nacional."""
    f = a_fecha(fecha)
    limite = 6 if sabado_habil else 5
    if f.weekday() >= limite:
        return False
    if f in _feriados_base(f.year):
        return False
    return f not in {a_fecha(x) for x in feriados_adicionales}


def sumar_dias_habiles(
    fecha: Any,
    dias: int,
    feriados_adicionales: Iterable[Any] = (),
    sabado_habil: bool = False,
) -> date:
    """Avanza (``dias`` > 0) o retrocede (``dias`` < 0) esa cantidad de días
    hábiles. Con ``dias == 0`` devuelve la misma fecha."""
    actual = a_fecha(fecha)
    adicionales = tuple(feriados_adicionales)
    paso = 1 if dias >= 0 else -1
    restantes = abs(int(dias))
    while restantes:
        actual += timedelta(days=paso)
        if es_dia_habil(actual, adicionales, sabado_habil):
            restantes -= 1
    return actual


def restar_dias_habiles(
    fecha: Any,
    dias: int,
    feriados_adicionales: Iterable[Any] = (),
    sabado_habil: bool = False,
) -> date:
    """Retrocede ``dias`` días hábiles (p. ej. 15 días hábiles antes del
    31-oct para el Formulario 701)."""
    return sumar_dias_habiles(fecha, -abs(int(dias)), feriados_adicionales, sabado_habil)


def dias_habiles_entre(
    inicio: Any,
    fin: Any,
    feriados_adicionales: Iterable[Any] = (),
    sabado_habil: bool = False,
) -> int:
    """Cuenta los días hábiles en el intervalo ``(inicio, fin]`` (excluye
    ``inicio``, incluye ``fin``). Si ``fin < inicio`` el resultado es negativo."""
    f_ini, f_fin = a_fecha(inicio), a_fecha(fin)
    adicionales = tuple(feriados_adicionales)
    if f_fin >= f_ini:
        desde, hasta, signo = f_ini, f_fin, 1
    else:
        desde, hasta, signo = f_fin, f_ini, -1
    total, cursor = 0, desde
    while cursor < hasta:
        cursor += timedelta(days=1)
        if es_dia_habil(cursor, adicionales, sabado_habil):
            total += 1
    return signo * total


# ==========================================================================
# CICLO PRODUCTIVO
# ==========================================================================


def fecha_cierre_ciclo(
    inicio: Any,
    dias_ciclo: int = config.DIAS_CICLO_ESTANDAR,
    inclusivo: bool = True,
) -> date:
    """Fecha de cierre (faenado/venta) de un ciclo de ``dias_ciclo`` días.

    # DISCREPANCIA: el Master Plan dice "cierre = inicio + 73", pero el POA
    # (calendario de ciclos) cuenta el día de recepción como día 1: C1 inicia
    # 1-jul-2026 y cierra el 11-sep-2026 (= inicio + 72); C2 inicia el 12-sep.
    # Por defecto se aplica el criterio del POA (``inclusivo=True``).
    # Con ``inclusivo=False`` se obtiene inicio + dias_ciclo (12-sep-2026).

    Args:
        inicio: fecha de recepción de los pollitos BB (día 1 si ``inclusivo``).
        dias_ciclo: duración del ciclo (a futuro se lee de ``parametros``).
        inclusivo: ``True`` -> el día de inicio es el día 1.

    Raises:
        DatosInvalidosError: si ``dias_ciclo`` no es un entero positivo.
    """
    if isinstance(dias_ciclo, bool) or not isinstance(dias_ciclo, int) or dias_ciclo <= 0:
        raise DatosInvalidosError(f"Los días del ciclo deben ser un entero positivo: {dias_ciclo!r}.")
    desplazamiento = dias_ciclo - 1 if inclusivo else dias_ciclo
    return a_fecha(inicio, "inicio") + timedelta(days=desplazamiento)


def fecha_inicio_ciclo_siguiente(
    inicio: Any, dias_ciclo: int = config.DIAS_CICLO_ESTANDAR, inclusivo: bool = True
) -> date:
    """Fecha de inicio del ciclo siguiente (día posterior al cierre, según POA)."""
    cierre = fecha_cierre_ciclo(inicio, dias_ciclo, inclusivo)
    return cierre + timedelta(days=1) if inclusivo else cierre
