"""Contabilidad general, cierres y estados financieros (Fase 10, Ciclo 5).

Implementa el Procedimiento PR-FIN-10 (cierre mensual) y PR-FIN-11 (cierre del
ejercicio), los modelos del MAN-006 (Manual de Gestión Contable y Financiera,
§18-19) y el Reglamento de Distribución de Excedentes CI-FIN-02 (POL-01):

* Lista de verificación de cierre mensual FIN-INF-01: cinco verificaciones con
  el número de comprobantes pendientes, que debe llegar a cero.
* Cierre mensual con bloqueo del período (contable.cerrar_periodo) y archivo
  del paquete FIN-INF-03 (consolidado de costeo) y FIN-INF-04 (Estado de
  Resultados escalonado con ICL, RINC y ROA).
* Balance General, Estado de Resultados, Estado de Flujo de Efectivo (método
  directo) y Estado de Evolución del Patrimonio Neto con la estructura del
  MAN-006; Notas 1 a 16 con los saldos reales.
* Cierre anual al 30 de junio: cuentas de resultado a 8.1.01.01 y traslado a
  3.1.03.02 (excedente) o 3.1.03.03 (déficit); cierre del ejercicio.
* Excedente distribuible (Reserva Legal >= 5 %, Educación >= 1 %, Reinversión
  por decisión de Asamblea), INF-CV-04, apropiación, excedente en distribución
  (3.1.03.04 a 2.1.05.01), liquidaciones individuales y pago solo si el saldo
  bancario posterior es >= SME.
* Plazos de 60 días (Consejos) y 90 días (Asamblea, Ley 356 Art. 55) con
  alertas.

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from db.conexion import a_centavos, de_centavos, obtener_parametros_vigentes, transaccion
from servicios import activos, auditoria, contable, planillas, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion, TipoDecision
from utils.formato import a_fecha, fecha_pantalla, formato_bs, redondear_bs

CERO = Decimal("0.00")
CUENTAS_EFECTIVO: tuple[str, ...] = ("1.1.01.01", "1.1.01.02", "1.1.01.03", "1.1.01.04", "1.1.01.05")
CENTROS: tuple[str, ...] = ("6.1.01.01", "6.1.01.02")
CUENTA_RESUMEN = "8.1.01.01"
CUENTA_EXCEDENTE = "3.1.03.02"
CUENTA_DEFICIT = "3.1.03.03"
CUENTA_EN_DISTRIBUCION = "3.1.03.04"
CUENTA_RESERVA_LEGAL = "3.1.02.01"
CUENTA_EDUCACION = "3.1.02.02"
CUENTA_REINVERSION = "3.1.02.03"
CUENTA_APORTES_ADICIONALES = "3.1.01.05"
CUENTA_EXCEDENTES_POR_PAGAR = "2.1.05.01"
TIPOS_FUERA_DE_RESULTADOS: tuple[str, ...] = ("CIERRE",)
CRITERIOS: tuple[str, ...] = ("IGUALITARIO", "PARTICIPACION")
SEMAFOROS: tuple[str, ...] = ("BUENO", "NORMAL", "MALO")
NOMBRE_MES = contable.NOMBRE_MES


# ==========================================================================
# UTILIDADES GENERALES
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 300) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"Indique {campo}.")
    texto = valor.strip()
    if len(texto) > maximo:
        raise DatosInvalidosError(f"El campo {campo} admite hasta {maximo} caracteres.")
    return texto


def _opcional(valor: Any, campo: str, maximo: int = 300) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    return _texto(valor, campo, maximo)


def _entero(valor: Any, campo: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise DatosInvalidosError(f"El campo {campo} debe ser un número entero.")
    return valor


def _params(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio)


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro vigente «{clave}» (regla 2: ninguna cifra normativa fija).",
                                codigo="PARAMETRO_FALTANTE")
    return p[clave]


def _bs(cent: int) -> Decimal:
    return de_centavos(int(cent))


def _pct_texto(fraccion: Any, decimales: int = 2) -> str:
    if fraccion is None:
        return "—"
    valor = (Decimal(fraccion) * 100).quantize(Decimal(10) ** -decimales, rounding=ROUND_HALF_UP)
    return f"{valor:f}".replace(".", ",") + " %"


def _pct_norma(fraccion: Any) -> str:
    """Porcentaje normativo sin ceros sobrantes (0,0833 -> «8,33 %»; 0,05 -> «5 %»)."""
    valor = (Decimal(str(fraccion)) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    return f"{valor:f}".replace(".", ",") + " %"


def _fraccion(num_cent: int, den_cent: int) -> Decimal | None:
    if den_cent == 0:
        return None
    return (Decimal(num_cent) / Decimal(den_cent)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _ejercicio_fila(con: sqlite3.Connection, ejercicio: int) -> sqlite3.Row:
    _entero(ejercicio, "ejercicio")
    fila = con.execute("SELECT * FROM ejercicios WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el ejercicio {ejercicio}.")
    return fila


def rango_ejercicio(con: sqlite3.Connection, ejercicio: int) -> tuple[date, date]:
    """Primer y último día del ejercicio (1-jul a 30-jun)."""
    fila = _ejercicio_fila(con, ejercicio)
    return date.fromisoformat(fila["fecha_inicio"]), date.fromisoformat(fila["fecha_fin"])


def ejercicio_de(con: sqlite3.Connection, fecha: Any) -> int:
    """Ejercicio que contiene la fecha."""
    f = a_fecha(fecha).isoformat()
    fila = con.execute("SELECT ejercicio FROM ejercicios WHERE ? BETWEEN fecha_inicio AND fecha_fin",
                       (f,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"La fecha {fecha_pantalla(f)} no pertenece a ningún ejercicio registrado.")
    return int(fila[0])


def _periodo(con: sqlite3.Connection, periodo_id: int) -> sqlite3.Row:
    _entero(periodo_id, "período")
    fila = con.execute("SELECT * FROM periodos WHERE id = ?", (periodo_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el período {periodo_id}.")
    return fila


def nombre_periodo(periodo: Mapping[str, Any]) -> str:
    inicio = date.fromisoformat(periodo["fecha_inicio"])
    return f"{NOMBRE_MES[inicio.month - 1]} de {inicio.year}"


def _filtro_tipos(excluir: Sequence[str]) -> tuple[str, list[Any]]:
    if not excluir:
        return "", []
    return f" AND a.tipo NOT IN ({','.join('?' * len(excluir))})", list(excluir)


def saldos(con: sqlite3.Connection, hasta: Any, desde: Any = None,
           excluir: Sequence[str] = ()) -> dict[str, int]:
    """Saldo Debe − Haber (centavos) por cuenta, entre fechas, excluyendo tipos
    de asiento (p. ej. CIERRE para el Estado de Resultados)."""
    sql = ("SELECT d.cuenta_codigo, COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0) "
           "FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id WHERE a.fecha <= ?")
    args: list[Any] = [a_fecha(hasta).isoformat()]
    if desde is not None:
        sql += " AND a.fecha >= ?"
        args.append(a_fecha(desde).isoformat())
    extra, valores = _filtro_tipos(excluir)
    sql += extra + " GROUP BY d.cuenta_codigo"
    return {f[0]: int(f[1]) for f in con.execute(sql, args + valores)}


def _movimientos(con: sqlite3.Connection, desde: Any, hasta: Any,
                 excluir: Sequence[str] = ()) -> dict[str, tuple[int, int]]:
    """(Debe, Haber) en centavos por cuenta dentro del rango."""
    sql = ("SELECT d.cuenta_codigo, COALESCE(SUM(d.debe_cent), 0), COALESCE(SUM(d.haber_cent), 0) "
           "FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id WHERE a.fecha BETWEEN ? AND ?")
    extra, valores = _filtro_tipos(excluir)
    sql += extra + " GROUP BY d.cuenta_codigo"
    return {f[0]: (int(f[1]), int(f[2])) for f in con.execute(
        sql, [a_fecha(desde).isoformat(), a_fecha(hasta).isoformat()] + valores)}


def _costo_por_sufijo(con: sqlite3.Connection, desde: Any, hasta: Any) -> dict[str, int]:
    """Costo neto incurrido por sufijo en los centros del Grupo 6, sin los
    traslados a productos terminados (cierre de ciclo) ni el asiento de cierre."""
    filas = con.execute(
        """SELECT d.sufijo, COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0)
             FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
            WHERE a.fecha BETWEEN ? AND ? AND a.tipo <> 'CIERRE' AND d.cuenta_codigo IN (?, ?)
              AND d.sufijo IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM asiento_detalle t WHERE t.asiento_id = a.id
                                 AND t.cuenta_codigo IN ('1.1.03.12', '1.1.03.13') AND t.debe_cent > 0)
            GROUP BY d.sufijo""",
        (a_fecha(desde).isoformat(), a_fecha(hasta).isoformat(), CENTROS[0], CENTROS[1])).fetchall()
    return {f[0]: int(f[1]) for f in filas}


def es_resultado(cuenta: str) -> bool:
    """Cuentas que se cierran al 30 de junio: Grupo 5, costo de ventas
    (6.1.02), Grupo 7 y Grupo 8 (salvo los centros de costo 6.1.01, que son
    producción en proceso)."""
    return cuenta.startswith(("5.", "6.1.02.", "7.", "8."))


def _suma(s: Mapping[str, int], cuentas: Iterable[str]) -> int:
    return sum(s.get(c, 0) for c in cuentas)


def _nombres(con: sqlite3.Connection) -> dict[str, str]:
    return {f[0]: f[1] for f in con.execute("SELECT codigo, nombre FROM cuentas")}


def _fila(concepto: str, monto_cent: int | None, tipo: str = "linea", nota: str = "",
          cuentas: Sequence[str] = ()) -> dict[str, Any]:
    return {"concepto": concepto, "monto": None if monto_cent is None else _bs(monto_cent), "tipo": tipo,
            "nota": nota, "cuentas": list(cuentas)}


# ==========================================================================
# 1. BALANCE GENERAL (MAN-006 §18.1)
# ==========================================================================
# (concepto, cuentas, nota, signo, obligatoria). signo +1 = saldo deudor positivo
# (activo); -1 = saldo acreedor positivo (pasivo y patrimonio) o, en el activo,
# reguladora que se muestra entre paréntesis (valor negativo).
_BG_DISPONIBLE = [
    ("Caja – Efectivo en Moneda Nacional", ("1.1.01.01",), "Nota 3", 1, True),
    ("Caja – Efectivo en Moneda Extranjera", ("1.1.01.02",), "Nota 3", 1, False),
    ("Banco – Cuenta Corriente Moneda Nacional", ("1.1.01.03",), "Nota 3", 1, True),
    ("Banco – Cuenta de Ahorros", ("1.1.01.04",), "Nota 3", 1, True),
    ("Fondo Rotativo – Caja Chica", ("1.1.01.05",), "Nota 3", 1, True),
]
_BG_EXIGIBLE = [
    ("Cuentas por cobrar", ("1.1.02.01",), "", 1, True),
    ("Deterioro Acumulado de Cuentas por cobrar", ("4.1.01.01",), "", 1, False),
]
_BG_INVENTARIOS = [
    ("Inventario – Pollitos BB", ("1.1.03.01",), "", 1, True),
    ("Inventario – Alimento Balanceado Comprado a Terceros", ("1.1.03.02",), "", 1, True),
    ("Inventario – Maíz Amarillo (*)", ("1.1.03.03",), "", 1, True),
    ("Inventario – Torta de Soya (*)", ("1.1.03.04",), "", 1, True),
    ("Inventario – Afrecho de Trigo (*)", ("1.1.03.05",), "", 1, True),
    ("Inventario – Núcleo Vitamínico (*)", ("1.1.03.06",), "", 1, True),
    ("Inventario – Aceite Vegetal (*)", ("1.1.03.07",), "", 1, True),
    ("Inventario – Productos Veterinarios", ("1.1.03.08",), "", 1, True),
    ("Inventario – Insumos de Limpieza y Desinfección", ("1.1.03.09",), "", 1, True),
    ("Inventario – Insumos de Crianza", ("1.1.03.10",), "", 1, True),
    ("Inventario – Insumos de Embalaje y Comercialización", ("1.1.03.11",), "", 1, True),
    ("Inventario – Productos Terminados: Carne de Pollo", ("1.1.03.12",), "", 1, True),
    ("Inventario – Productos Terminados: Alimento Balanceado (*)", ("1.1.03.13",), "", 1, True),
    ("Inventario – Subproductos: Gallinaza (Abono Orgánico)", ("1.1.03.14",), "", 1, True),
    ("Inventario – Materiales de mantenimiento", ("1.1.03.15",), "", 1, True),
    # DISCREPANCIA: el modelo del balance (§18.1) no tiene renglón para la
    # producción en proceso, pero su encabezado remite a las Notas 4 y 5 y la
    # Nota 5 revela los activos biológicos (saldo de los centros 6.1.01). Se
    # muestra el renglón solo cuando tiene saldo.
    ("Activos biológicos – Pollos en engorde (producción en proceso)", CENTROS, "Nota 5", 1, False),
    ("Deterioro Acumulado de Materias Primas", ("4.1.01.02",), "", 1, False),
    ("Deterioro Acumulado de Alimento Balanceado", ("4.1.01.03",), "", 1, False),
    ("Deterioro Acumulado de Carne de Pollo", ("4.1.01.04",), "", 1, False),
]
_BG_ANTICIPOS = [
    ("Anticipos a Proveedores de Insumos", ("1.1.04.01",), "", 1, True),
    ("Gastos Pagados por Adelantado", ("1.1.04.02",), "", 1, True),
    ("Anticipos a personal eventual", ("1.1.04.04",), "", 1, True),
    ("Otros Activos Corrientes", ("1.1.04.03",), "", 1, False),
]
_BG_PPE = [
    ("Galpones de Producción", ("1.2.01.02",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.01",), "", 1, True),
    ("Terrenos", ("1.2.01.01",), "", 1, True),
    ("Alambrados y Cercados", ("1.2.01.03",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.02",), "", 1, True),
    ("Equipos de Producción Avícola", ("1.2.01.06",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.05",), "", 1, True),
    ("Herramientas de Producción", ("1.2.01.07",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.06",), "", 1, True),
    ("Equipos de computación", ("1.2.01.09",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.08",), "", 1, True),
    ("Muebles y enseres", ("1.2.01.08",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.07",), "", 1, True),
    ("Ambiente de Preparación de Alimento Balanceado", ("1.2.01.04",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.03",), "", 1, True),
    ("Maquinaria de Producción de Alimento Balanceado", ("1.2.01.05",), "", 1, True),
    ("Dep Acumulada", ("4.1.02.04",), "", 1, True),
]
_BG_INTANGIBLES = [
    ("Gastos de Constitución y Organización", ("1.2.02.01",), "", 1, True),
    ("Amortización acumulada", ("4.1.02.09",), "", 1, True),
    ("Software y Plataformas Digitales", ("1.2.02.02",), "", 1, True),
    ("Amortización acumulada", ("4.1.02.10",), "", 1, True),
]
_BG_OTROS_NC = [
    ("Depósitos en Garantía", ("1.2.03.01",), "", 1, True),
    # DISCREPANCIA: el modelo muestra «Inversiones a Largo Plazo» entre
    # paréntesis, pero 1.2.03.02 es de naturaleza deudora (Plan v3): se expone
    # en positivo.
    ("Inversiones a Largo Plazo", ("1.2.03.02",), "", 1, True),
]
_BG_CXP = [
    ("Proveedores – Pollitos BB", ("2.1.01.01",), "", -1, True),
    ("Proveedores – Alimento Balanceado (Comprado 2026)", ("2.1.01.02",), "", -1, True),
    ("Proveedores – Materias Primas Alimento (*)", ("2.1.01.03",), "", -1, True),
    ("Proveedores – Productos Veterinarios", ("2.1.01.04",), "", -1, True),
    ("Proveedores – Insumos de Limpieza y Crianza", ("2.1.01.05",), "", -1, True),
    ("Proveedores – Servicios y Otros", ("2.1.01.06",), "", -1, True),
]
_BG_LABORALES = [
    ("Sueldos y Salarios por pagar", ("2.1.02.01",), "", -1, True),
    ("Aportes Laborales por pagar", ("2.1.02.05",), "", -1, True),
    ("Aportes Patronales por pagar", ("2.1.02.03",), "", -1, True),
    # La Nota 9 incluye el aguinaldo y el personal eventual; el modelo del
    # balance no: se muestran cuando tienen saldo.
    ("Aguinaldo por Pagar", ("2.1.02.02",), "", -1, False),
    ("Personal Eventual por Pagar", ("2.1.02.04",), "", -1, False),
]
_BG_TRIBUTARIAS = [
    ("Régimen Agropecuario Unificado por Pagar", ("2.1.03.01",), "", -1, True),
    ("Tributos Municipales por Pagar", ("2.1.03.02",), "", -1, True),
]
_BG_OTROS_PC = [("Anticipos de Clientes HORECA Recibidos", ("2.1.04.01",), "", -1, True)]
_BG_EXCEDENTES = [("Excedentes por pagar Socios – Ejercicio Anterior", ("2.1.05.01",), "", -1, True)]
# DISCREPANCIA: el modelo dice «Previsión para Indemnizaciones»; PC-09 y el
# Plan v3 fijan «Provisión para Indemnización» (conciliación de la Fase 1).
_BG_LABORALES_LP = [("Provisión para Indemnizaciones", ("2.2.01.01",), "", -1, True)]
_CUENTAS_CAPITAL = ("3.1.01.01", "3.1.01.02", "3.1.01.03", "3.1.01.04", "4.3.01.01")
_CUENTAS_RESERVAS = ("3.1.02.01", "3.1.02.02", "3.1.02.03", "3.1.02.04", "3.1.02.05")
_CUENTAS_RESULTADOS_ACUM = ("3.1.03.01", "3.1.03.02", "3.1.03.03", "3.1.03.04")
_CUENTAS_ESPECIALES = ("3.1.04.01", "3.1.04.02")


def _bloque(s: Mapping[str, int], definicion: Sequence[tuple[str, tuple[str, ...], str, int, bool]],
            usadas: set[str]) -> tuple[list[dict[str, Any]], int]:
    filas: list[dict[str, Any]] = []
    total = 0
    for concepto, cuentas, nota, signo, obligatoria in definicion:
        monto = signo * _suma(s, cuentas)
        usadas.update(cuentas)
        total += monto
        if obligatoria or monto != 0:
            filas.append(_fila(concepto, monto, "linea", nota, cuentas))
    return filas, total


def balance_general(con: sqlite3.Connection, sesion: Sesion, fecha_corte: Any) -> dict[str, Any]:
    """Balance General al ``fecha_corte`` con la estructura del MAN-006 §18.1.

    El resultado aún no cerrado del ejercicio (cuentas de resultado con saldo)
    se expone dentro de «Excedentes de Percepción Acumulados» (Nota 12.3).

    Returns:
        ``filas`` (concepto, monto, tipo, nota), totales y ``cuadra``.
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    corte = a_fecha(fecha_corte)
    s = saldos(con, corte)
    usadas: set[str] = set()
    filas: list[dict[str, Any]] = [_fila("ACTIVO", None, "titulo"), _fila("ACTIVO CORRIENTE", None, "titulo")]
    total_ac = 0
    for titulo, nota, definicion in (
            (None, "", _BG_DISPONIBLE),
            ("Exigible – Cuentas por cobrar", "", _BG_EXIGIBLE),
            ("Inventario y existencias", "Notas 4 y 5", _BG_INVENTARIOS),
            ("Pagos anticipados y otros activos corrientes", "", _BG_ANTICIPOS)):
        if titulo:
            filas.append(_fila(titulo, None, "subtitulo", nota))
        bloque, subtotal = _bloque(s, definicion, usadas)
        filas += bloque
        total_ac += subtotal
    otras_ac = _no_clasificadas(s, usadas, ("1.1.", "4.1.01."))
    total_ac += sum(m for _, m in otras_ac)
    filas += [_fila(f"Otras cuentas: {c}", m, "linea", "", (c,)) for c, m in otras_ac]
    filas.append(_fila("TOTAL ACTIVO CORRIENTE", total_ac, "total"))

    filas.append(_fila("ACTIVO NO CORRIENTE", None, "titulo"))
    total_anc = 0
    for titulo, nota, definicion in (
            ("Propiedad planta y equipo", "Nota 6", _BG_PPE),
            ("Activos Intangibles", "Nota 7", _BG_INTANGIBLES),
            ("Otros activos no corrientes", "", _BG_OTROS_NC)):
        filas.append(_fila(titulo, None, "subtitulo", nota))
        bloque, subtotal = _bloque(s, definicion, usadas)
        filas += bloque
        total_anc += subtotal
    otras_anc = _no_clasificadas(s, usadas, ("1.2.", "4.1.02."))
    total_anc += sum(m for _, m in otras_anc)
    filas += [_fila(f"Otras cuentas: {c}", m, "linea", "", (c,)) for c, m in otras_anc]
    filas.append(_fila("TOTAL ACTIVO NO CORRIENTE", total_anc, "total"))
    total_activo = total_ac + total_anc
    filas.append(_fila("TOTAL ACTIVO", total_activo, "total"))

    filas += [_fila("PASIVO", None, "titulo"), _fila("PASIVO CORRIENTE", None, "titulo")]
    total_pc = 0
    for titulo, nota, definicion in (
            ("Cuentas por pagar comerciales", "Nota 8", _BG_CXP),
            ("Obligaciones laborales a Corto Plazo", "Nota 9", _BG_LABORALES),
            ("Obligaciones tributarias corrientes", "Nota 10", _BG_TRIBUTARIAS),
            ("Otros pasivos corrientes", "", _BG_OTROS_PC),
            ("Excedentes por distribuir", "", _BG_EXCEDENTES)):
        filas.append(_fila(titulo, None, "subtitulo", nota))
        bloque, subtotal = _bloque(s, definicion, usadas)
        filas += bloque
        total_pc += subtotal
    otras_pc = [(c, -m) for c, m in _no_clasificadas(s, usadas, ("2.1.",))]
    total_pc += sum(m for _, m in otras_pc)
    filas += [_fila(f"Otras cuentas: {c}", m, "linea", "", (c,)) for c, m in otras_pc]
    filas.append(_fila("TOTAL PASIVO CORRIENTE", total_pc, "total"))
    filas += [_fila("PASIVO NO CORRIENTE", None, "titulo"),
              _fila("Obligaciones laborales a largo Plazo", None, "subtitulo", "Nota 11")]
    bloque, total_pnc = _bloque(s, _BG_LABORALES_LP, usadas)
    filas += bloque
    otras_pnc = [(c, -m) for c, m in _no_clasificadas(s, usadas, ("2.2.",))]
    total_pnc += sum(m for _, m in otras_pnc)
    filas += [_fila(f"Otras cuentas: {c}", m, "linea", "", (c,)) for c, m in otras_pnc]
    filas.append(_fila("TOTAL PASIVO NO CORRIENTE", total_pnc, "total"))
    total_pasivo = total_pc + total_pnc
    filas.append(_fila("TOTAL PASIVO", total_pasivo, "total"))

    resultado_abierto = -sum(m for c, m in s.items() if es_resultado(c))
    capital_fundadores = -s.get("3.1.01.01", 0)
    capital_otros = -_suma(s, _CUENTAS_CAPITAL[1:])
    reservas = -_suma(s, _CUENTAS_RESERVAS)
    acumulados = -_suma(s, _CUENTAS_RESULTADOS_ACUM) + resultado_abierto
    aportes = -s.get(CUENTA_APORTES_ADICIONALES, 0)
    especiales = -_suma(s, _CUENTAS_ESPECIALES)
    usadas.update(_CUENTAS_CAPITAL + _CUENTAS_RESERVAS + _CUENTAS_RESULTADOS_ACUM + _CUENTAS_ESPECIALES
                  + (CUENTA_APORTES_ADICIONALES,))
    filas.append(_fila("PATRIMONIO NETO", None, "titulo", "Nota 12"))
    filas.append(_fila("Certificados de Aportación – Socios Fundadores", capital_fundadores, "linea", "",
                       ("3.1.01.01",)))
    if capital_otros:
        filas.append(_fila("Certificados de Aportación – Nuevos socios, suscritos y en trámite", capital_otros,
                           "linea", "", _CUENTAS_CAPITAL[1:]))
    filas.append(_fila("Reservas", reservas, "linea", "", _CUENTAS_RESERVAS))
    filas.append(_fila("Excedentes de Percepción Acumulados", acumulados, "linea", "", _CUENTAS_RESULTADOS_ACUM))
    filas.append(_fila("Aportes Adicionales de Socios", aportes, "linea", "", (CUENTA_APORTES_ADICIONALES,)))
    if especiales:
        filas.append(_fila("Primas de Admisión, Donaciones y Legados", especiales, "linea", "", _CUENTAS_ESPECIALES))
    otras_pat = [(c, -m) for c, m in _no_clasificadas(s, usadas, ("3.", "4.3."))]
    filas += [_fila(f"Otras cuentas: {c}", m, "linea", "", (c,)) for c, m in otras_pat]
    total_patrimonio = (capital_fundadores + capital_otros + reservas + acumulados + aportes + especiales
                        + sum(m for _, m in otras_pat))
    filas.append(_fila("TOTAL PATRIMONIO", total_patrimonio, "total"))
    filas.append(_fila("TOTAL PASIVO Y PATRIMONIO", total_pasivo + total_patrimonio, "total"))
    diferencia = total_activo - total_pasivo - total_patrimonio
    return {
        "fecha_corte": corte.isoformat(), "filas": filas,
        "total_activo_corriente": _bs(total_ac), "total_activo_no_corriente": _bs(total_anc),
        "total_activo": _bs(total_activo), "total_pasivo_corriente": _bs(total_pc),
        "total_pasivo_no_corriente": _bs(total_pnc), "total_pasivo": _bs(total_pasivo),
        "total_patrimonio": _bs(total_patrimonio), "resultado_no_cerrado": _bs(resultado_abierto),
        "excedentes_acumulados": _bs(acumulados), "reservas": _bs(reservas),
        "cuadra": diferencia == 0, "diferencia": _bs(diferencia),
    }


def _no_clasificadas(s: Mapping[str, int], usadas: set[str], prefijos: tuple[str, ...]) -> list[tuple[str, int]]:
    """Cuentas con saldo no previstas en el modelo (para que el balance cuadre siempre)."""
    resultado = [(c, m) for c, m in sorted(s.items())
                 if m != 0 and c not in usadas and c.startswith(prefijos) and not es_resultado(c)]
    usadas.update(c for c, _ in resultado)
    return resultado


# ==========================================================================
# 2. ESTADO DE RESULTADOS (MAN-006 §18.2) Y FIN-INF-04 (PC-08 escalonado)
# ==========================================================================
_ER_INGRESOS = [("Ventas – Carne de Pollo", "5.1.01.01"), ("Ventas – Alimento Balanceado Excedente", "5.1.02.01")]
_ER_COSTOS = [("Costo de Ventas – Carne de Pollo", "6.1.02.01"),
              ("Costo de Ventas – Alimento Balanceado Excedente", "6.1.02.02")]
_ER_GASTOS: list[tuple[str, str | None, list[tuple[str, str]]]] = [
    ("Gastos de Personal Administrativo", None, [
        ("Sueldos – Personal Administrativo", "7.1.01.01"), ("Aguinaldo – Personal Administrativo", "7.1.01.02"),
        ("Aportes Patronales – Personal Administrativo", "7.1.01.03"),
        ("Indemnización – Personal Administrativo", "7.1.01.04")]),
    ("Gastos Generales Administrativos", None, [
        ("Materiales y Útiles de Oficina", "7.1.02.01"), ("Servicios de Comunicación", "7.1.02.02"),
        ("Gastos de Mantenimiento – Administrativo", "7.1.02.03"), ("Depreciación Muebles y Enseres", "7.1.02.04"),
        ("Depreciación Equipos de computación", "7.1.02.05"), ("Amortización Gastos de Constitución", "7.1.02.06"),
        ("Amortización Software y Plataformas Digitales", "7.1.02.07"),
        ("Pérdida en Cuentas Incobrables", "7.1.02.08")]),
    ("Gastos de Comercialización", None, [("Gastos de Transporte y Flete Rural", "7.1.03.01")]),
    ("Gastos de Certificación y Calidad", None, [
        ("Certificación SENASAG", "7.1.04.01"), ("Auditoría y Certificación IBNORCA", "7.1.04.02")]),
    ("Gastos Legales y de Registro", "subtotal", [
        ("Licencias Municipales y Habilitación", "7.1.05.01"),
        ("Impuesto por el Régimen Agropecuario Unificado", "7.1.05.02")]),
]
_ER_NO_OPERATIVOS = [
    ("Multas y Sanciones", "7.2.01.01"), ("Pérdida por Retiro o Venta de Activos Fijos", "7.2.01.02"),
    ("Gastos Extraordinarios no Previstos", "7.2.01.03"), ("Pérdidas por Mortandad Avícola anormal", "7.2.01.04"),
    ("Pérdidas por Mermas de Producción anormal", "7.2.01.05")]
_ER_OTROS_INGRESOS = [
    ("Ingresos Financieros – Intereses Ganados", "5.2.01.01"), ("Donaciones y Subsidios Recibidos", "5.2.01.02"),
    ("Recuperación de Gastos de Ejercicios Anteriores", "5.2.01.03"),
    ("Ventas – Gallinaza / Abono Orgánico", "5.2.01.04"), ("Otros Ingresos No Operativos", "5.2.01.05")]


def _cifras_resultado(s: Mapping[str, int]) -> dict[str, int]:
    """Cifras escalonadas en centavos a partir de los saldos (Debe − Haber)."""
    carne = -s.get("5.1.01.01", 0)
    alimento = -s.get("5.1.02.01", 0)
    devoluciones = s.get("5.1.04.01", 0)
    gallinaza = -s.get("5.2.01.04", 0)
    ingresos_operativos = carne + alimento - devoluciones
    costo_ventas = _suma(s, ("6.1.02.01", "6.1.02.02"))
    gastos_operacion = sum(m for c, m in s.items() if c.startswith("7.1."))
    no_operativos = sum(m for c, m in s.items() if c.startswith("7.2."))
    otros_ingresos = -sum(m for c, m in s.items() if c.startswith("5.2."))
    otros_resultados = -sum(m for c, m in s.items() if c.startswith("8."))
    otros_operativos = -sum(m for c, m in s.items() if c.startswith("5.1.") and c not in
                            ("5.1.01.01", "5.1.02.01", "5.1.04.01"))
    ingresos_operativos += otros_operativos
    utilidad_bruta = ingresos_operativos - costo_ventas
    ebit = utilidad_bruta - gastos_operacion - no_operativos
    excedente = ebit + otros_ingresos + otros_resultados
    return {
        "carne": carne, "alimento": alimento, "devoluciones": devoluciones, "gallinaza": gallinaza,
        "ingresos_operativos": ingresos_operativos, "costo_ventas": costo_ventas, "utilidad_bruta": utilidad_bruta,
        "gastos_operacion": gastos_operacion, "gastos_no_operativos": no_operativos, "ebit": ebit,
        "otros_ingresos": otros_ingresos, "otros_resultados": otros_resultados, "excedente_neto": excedente,
        "total_ingresos": ingresos_operativos + otros_ingresos,
        "ventas_totales": ingresos_operativos + gallinaza,
    }


def estado_resultados(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, hasta: Any = None) -> dict[str, Any]:
    """Estado de Resultados del ejercicio (MAN-006 §18.2), sin el asiento de cierre.

    Args:
        ejercicio: año del ejercicio (2026 = 1-jul-2026 a 30-jun-2027).
        hasta: corte opcional dentro del ejercicio (por defecto, 30 de junio).
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    inicio, fin = rango_ejercicio(con, ejercicio)
    corte = min(a_fecha(hasta), fin) if hasta is not None else fin
    s = saldos(con, corte, inicio, TIPOS_FUERA_DE_RESULTADOS)
    c = _cifras_resultado(s)
    filas: list[dict[str, Any]] = [_fila("INGRESOS", None, "titulo"), _fila("Ingresos operativos", None, "subtitulo")]
    filas += [_fila(n, -s.get(cta, 0), "linea", "", (cta,)) for n, cta in _ER_INGRESOS]
    filas.append(_fila("Devolución en Ventas", -c["devoluciones"], "linea", "", ("5.1.04.01",)))
    filas += [_fila("Menos:", None, "texto"), _fila("COSTO DE PRODUCCIÓN VENDIDA", None, "titulo"),
              _fila("Costo de ventas", None, "subtitulo")]
    filas += [_fila(n, s.get(cta, 0), "linea", "", (cta,)) for n, cta in _ER_COSTOS]
    filas += [_fila("UTILIDAD BRUTA EN VENTAS", c["utilidad_bruta"], "total"), _fila("Menos:", None, "texto"),
              _fila("GASTOS DE OPERACIÓN", None, "titulo")]
    usadas = {cta for _, cta in _ER_INGRESOS + _ER_COSTOS + _ER_NO_OPERATIVOS + _ER_OTROS_INGRESOS} | {"5.1.04.01"}
    for titulo, modo, lineas in _ER_GASTOS:
        subtotal = _suma(s, [cta for _, cta in lineas])
        filas.append(_fila(titulo, subtotal if modo == "subtotal" else None, "subtitulo"))
        filas += [_fila(n, s.get(cta, 0), "linea", "", (cta,)) for n, cta in lineas]
        usadas.update(cta for _, cta in lineas)
    otros_gastos = [(cta, m) for cta, m in sorted(s.items()) if cta.startswith("7.1.") and cta not in usadas and m]
    filas += [_fila(f"Otros gastos de operación: {cta}", m, "linea", "", (cta,)) for cta, m in otros_gastos]
    filas.append(_fila("GASTOS NO OPERATIVOS", c["gastos_no_operativos"], "titulo"))
    filas.append(_fila("Gastos Financieros y Extraordinarios", None, "subtitulo"))
    filas += [_fila(n, s.get(cta, 0), "linea", "", (cta,)) for n, cta in _ER_NO_OPERATIVOS]
    filas.append(_fila("EXCEDENTE OPERATIVO (EBIT) / DÉFICIT OPERATIVO", c["ebit"], "total"))
    filas += [_fila("Mas:", None, "texto"), _fila("OTROS INGRESOS", None, "titulo")]
    filas += [_fila(n, -s.get(cta, 0), "linea", "", (cta,)) for n, cta in _ER_OTROS_INGRESOS]
    if c["otros_resultados"]:
        # DISCREPANCIA: el modelo no prevé el Grupo 8.2 (REI, AITB, diferencias
        # de cambio); se muestra solo si tiene movimiento.
        filas.append(_fila("Otros resultados (Grupo 8.2)", c["otros_resultados"], "linea", "", ("8.2",)))
    filas.append(_fila("EXCEDENTE/DÉFICIT NETO DEL EJERCICIO", c["excedente_neto"], "resultado"))
    control = -sum(m for cta, m in s.items() if es_resultado(cta))
    return {
        "ejercicio": ejercicio, "desde": inicio.isoformat(), "hasta": corte.isoformat(), "filas": filas,
        **{k: _bs(v) for k, v in c.items()}, "cuadra": control == c["excedente_neto"],
    }


def semaforo(indicador: str, valor: Decimal | None, p: Mapping[str, Any]) -> str | None:
    """Semáforo de ICL, RINC y ROA con los umbrales vigentes (FIN-INF-04)."""
    if valor is None:
        return None
    if indicador == "ICL":
        if valor < Decimal(_param(p, "icl_umbral_bueno")):
            return "BUENO"
        return "MALO" if valor > Decimal(_param(p, "icl_umbral_malo")) else "NORMAL"
    if indicador == "RINC":
        if valor >= Decimal(_param(p, "rinc_umbral_bueno")):
            return "BUENO"
        return "MALO" if valor < Decimal(_param(p, "rinc_umbral_malo")) else "NORMAL"
    if indicador == "ROA":
        if valor > Decimal(_param(p, "roa_umbral_bueno")):
            return "BUENO"
        return "MALO" if valor < Decimal(_param(p, "roa_umbral_malo")) else "NORMAL"
    raise DatosInvalidosError(f"Indicador desconocido: {indicador!r}.")


def umbrales_texto(p: Mapping[str, Any]) -> dict[str, str]:
    """Texto de los umbrales vigentes (columna «Umbral» del FIN-INF-04)."""
    ib, im = Decimal(_param(p, "icl_umbral_bueno")), Decimal(_param(p, "icl_umbral_malo"))
    rb, rm = Decimal(_param(p, "rinc_umbral_bueno")), Decimal(_param(p, "rinc_umbral_malo"))
    ob, om = Decimal(_param(p, "roa_umbral_bueno")), Decimal(_param(p, "roa_umbral_malo"))
    return {
        "ICL": f"Bueno <{_pct_norma(ib)} · Normal {_pct_norma(ib)}-{_pct_norma(im)} · Malo >{_pct_norma(im)}",
        "RINC": f"Bueno ≥{_pct_norma(rb)} · Normal {_pct_norma(rm)}-{_pct_norma(rb)} · Malo <{_pct_norma(rm)}",
        "ROA": f"Bueno >{_pct_norma(ob)} · Normal {_pct_norma(om)}-{_pct_norma(ob)} · Malo <{_pct_norma(om)}",
    }


def _indicadores(c: Mapping[str, int], activo_total_cent: int, p: Mapping[str, Any]) -> dict[str, Any]:
    icl = _fraccion(c["costo_ventas"], c["ventas_totales"])
    rinc = _fraccion(c["alimento"] + c["gallinaza"], c["ventas_totales"])
    roa = _fraccion(c["excedente_neto"], activo_total_cent)
    return {
        "ICL": {"valor": icl, "semaforo": semaforo("ICL", icl, p),
                "formula": "Costo de Producción / Ventas Totales × 100%"},
        "RINC": {"valor": rinc, "semaforo": semaforo("RINC", rinc, p),
                 "formula": "(Alimento + Gallinaza) / Ventas Totales × 100%"},
        "ROA": {"valor": roa, "semaforo": semaforo("ROA", roa, p),
                "formula": "Utilidad Neta / Activos Totales × 100%"},
    }


def _escalonado(c: Mapping[str, int]) -> list[dict[str, Any]]:
    return [
        _fila("Ingresos Operativos Totales", c["ingresos_operativos"]),
        _fila("(–) Costo de Ventas (de FIN-INF-03)", c["costo_ventas"]),
        _fila("(=) Margen Bruto", c["utilidad_bruta"], "total"),
        _fila("(–) Gastos Operativos y pérdidas anormales", c["gastos_operacion"] + c["gastos_no_operativos"]),
        _fila("(=) Excedente/Déficit Operativo (EBIT)", c["ebit"], "total"),
        _fila("(+) Ingresos No Operativos", c["otros_ingresos"] + c["otros_resultados"]),
        _fila("(=) Excedente/Déficit Neto del Período", c["excedente_neto"], "resultado"),
    ]


def estado_resultados_mensual(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> dict[str, Any]:
    """FIN-INF-04: Estado de Resultados escalonado del mes y acumulado del
    ejercicio, con ICL, RINC y ROA y su semáforo (umbrales en parámetros)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    return _fin_inf_04(con, _periodo(con, periodo_id))


def _fin_inf_04(con: sqlite3.Connection, periodo: sqlite3.Row) -> dict[str, Any]:
    ejercicio = int(periodo["ejercicio"])
    inicio_ej, _ = rango_ejercicio(con, ejercicio)
    desde, hasta = date.fromisoformat(periodo["fecha_inicio"]), date.fromisoformat(periodo["fecha_fin"])
    p = _params(con, ejercicio)
    mes = _cifras_resultado(saldos(con, hasta, desde, TIPOS_FUERA_DE_RESULTADOS))
    acumulado = _cifras_resultado(saldos(con, hasta, inicio_ej, TIPOS_FUERA_DE_RESULTADOS))
    s_bg = saldos(con, hasta)
    activo = _activo_total(s_bg)
    return {
        "periodo_id": int(periodo["id"]), "periodo": nombre_periodo(periodo), "ejercicio": ejercicio,
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "mes": {"filas": _escalonado(mes), **{k: _bs(v) for k, v in mes.items()}},
        "acumulado": {"filas": _escalonado(acumulado), **{k: _bs(v) for k, v in acumulado.items()}},
        "activo_total": _bs(activo),
        "indicadores_mes": _indicadores(mes, activo, p),
        "indicadores_acumulado": _indicadores(acumulado, activo, p),
        "umbrales": umbrales_texto(p),
    }


def _activo_total(s: Mapping[str, int]) -> int:
    """Total del activo (Grupo 1 + reguladoras 4.1 + producción en proceso)."""
    return sum(m for c, m in s.items() if c.startswith(("1.", "4.1.")) or c in CENTROS)


# ==========================================================================
# 3. FIN-INF-03 — CONSOLIDADO DE COSTEO MENSUAL
# ==========================================================================
def consolidado_costeo(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> dict[str, Any]:
    """FIN-INF-03: costo real de cada ciclo cerrado en el mes (Informe de Costo
    de Ciclo FIN-TRA-06) y costo de ventas del período según el mayor."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    return _fin_inf_03(con, _periodo(con, periodo_id))


def _fin_inf_03(con: sqlite3.Connection, periodo: sqlite3.Row) -> dict[str, Any]:
    desde, hasta = periodo["fecha_inicio"], periodo["fecha_fin"]
    filas = []
    for k in con.execute(
            """SELECT k.*, c.ejercicio, c.numero FROM costos_ciclo k JOIN ciclos_produccion c ON c.id = k.ciclo_id
                WHERE k.fecha_cierre BETWEEN ? AND ? ORDER BY k.fecha_cierre, k.id""", (desde, hasta)):
        kg = Decimal(int(k["kg_producidos_mil"])) / 1000
        filas.append({"ciclo": f"Ciclo {k['numero']}/{k['ejercicio']}", "fecha_cierre": k["fecha_cierre"],
                      "costo_total": _bs(k["total_cent"]), "kg_producidos": kg,
                      "costo_unitario": Decimal(str(k["costo_unitario"])) if k["costo_unitario"] else None})
    kg_vendidos = Decimal(int(con.execute(
        """SELECT COALESCE(SUM(cantidad_mil), 0) FROM notas_entrega
            WHERE fecha BETWEEN ? AND ? AND producto IN ('CARNE_ENTERO','CARNE_CORTES')""",
        (desde, hasta)).fetchone()[0])) / 1000
    mov = _movimientos(con, desde, hasta, TIPOS_FUERA_DE_RESULTADOS)
    costo_carne = mov.get("6.1.02.01", (0, 0))[0] - mov.get("6.1.02.01", (0, 0))[1]
    costo_alimento = mov.get("6.1.02.02", (0, 0))[0] - mov.get("6.1.02.02", (0, 0))[1]
    return {
        "periodo_id": int(periodo["id"]), "periodo": nombre_periodo(periodo), "desde": desde, "hasta": hasta,
        "ciclos": filas, "costo_ciclos": sum((f["costo_total"] for f in filas), CERO),
        "kg_vendidos": kg_vendidos, "costo_ventas_carne": _bs(costo_carne),
        "costo_ventas_alimento": _bs(costo_alimento), "costo_ventas": _bs(costo_carne + costo_alimento),
    }


# ==========================================================================
# 4. FIN-INF-01 — LISTA DE VERIFICACIÓN DE CIERRE MENSUAL
# ==========================================================================
VERIFICACIONES: tuple[tuple[str, str], ...] = (
    ("TESORERIA", "Comprobantes de Tesorería (Ciclo 1) registrados"),
    ("EGRESOS", "Comprobantes de Egresos y Planillas (Ciclo 2) registrados"),
    ("PRODUCCION", "Cierres de ciclo productivo (Ciclo 3, FIN-TRA-06) incorporados"),
    ("VENTAS", "Comprobantes de Ventas (Ciclo 4) registrados"),
    ("CODIFICACION", "Codificación conforme al Plan de Cuentas verificada"),
)


def _pend_tesoreria(con: sqlite3.Connection, desde: str, hasta: str) -> list[str]:
    return [f"Reposición de caja n.º {r['id']} del {fecha_pantalla(r['fecha_solicitud'])} en estado "
            f"{r['estado'].lower()} (sin asiento)"
            for r in con.execute("SELECT * FROM reposiciones_caja WHERE estado IN ('SOLICITADA','VALIDADA') "
                                 "AND substr(fecha_solicitud, 1, 10) <= ? ORDER BY id", (hasta,))]


def _pend_egresos(con: sqlite3.Connection, desde: str, hasta: str) -> list[str]:
    detalle = [f"Recepción n.º {r['id']} del {fecha_pantalla(r['fecha'])} sin contabilizar"
               for r in con.execute("SELECT id, fecha FROM recepciones WHERE estado = 'RECIBIDA' AND fecha <= ? "
                                    "ORDER BY id", (hasta,))]
    fin = date.fromisoformat(hasta)
    detalle += [f"Planilla de {NOMBRE_MES[r['mes'] - 1]} {r['anio']} en estado {r['estado'].lower()}"
                for r in con.execute("SELECT anio, mes, estado FROM planillas WHERE estado IN ('PREPARADA','AUTORIZADA') "
                                     "AND (anio * 100 + mes) <= ? ORDER BY anio, mes", (fin.year * 100 + fin.month,))]
    detalle += [f"Baja de inventario n.º {r['id']} autorizada sin contabilizar"
                for r in con.execute("SELECT id FROM bajas_inventario WHERE estado = 'AUTORIZADA' AND fecha <= ?",
                                     (hasta,))]
    detalle += [f"Liquidación final n.º {r['id']} autorizada sin contabilizar"
                for r in con.execute("SELECT id FROM liquidaciones_finales WHERE estado = 'AUTORIZADA' "
                                     "AND fecha_preparacion <= ?", (hasta,))]
    detalle += [f"Baja de activo fijo n.º {r['id']} autorizada sin contabilizar"
                for r in con.execute("SELECT id FROM bajas_activos WHERE estado = 'AUTORIZADA' AND fecha <= ?",
                                     (hasta,))]
    detalle += [f"Depreciación del mes {m} sin registrar (PR-FIN-07)" for m in activos.meses_pendientes(con, fin)]
    return detalle


def _pend_produccion(con: sqlite3.Connection, desde: str, hasta: str) -> list[str]:
    detalle = [f"Ciclo {r['numero']}/{r['ejercicio']} faenado sin concluir (cierre previsto "
               f"{fecha_pantalla(r['fecha_cierre'])})"
               for r in con.execute("SELECT * FROM ciclos_produccion WHERE estado = 'FAENADO' AND fecha_cierre <= ?",
                                    (hasta,))]
    detalle += [f"Ciclo {r['numero']}/{r['ejercicio']} concluido sin Informe de Costo (FIN-TRA-06)"
                for r in con.execute(
                    """SELECT * FROM ciclos_produccion c WHERE c.estado = 'CONCLUIDO' AND c.fecha_cierre <= ?
                         AND NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = c.id)""", (hasta,))]
    detalle += [f"Baja por mortalidad n.º {r['id']} del {fecha_pantalla(r['fecha'])} sin contabilizar"
                for r in con.execute("SELECT id, fecha FROM bajas_mortalidad WHERE estado <> 'CONTABILIZADA' "
                                     "AND fecha <= ?", (hasta,))]
    detalle += [f"Corte de MOD n.º {r['id']} sin reversión"
                for r in con.execute("SELECT id FROM cortes_mod WHERE reversion_asiento_id IS NULL AND fecha < ?",
                                     (hasta,))]
    return detalle


def _pend_ventas(con: sqlite3.Connection, desde: str, hasta: str) -> list[str]:
    detalle: list[str] = []
    if con.execute("SELECT 1 FROM costos_ciclo WHERE fecha_cierre <= ?", (hasta,)).fetchone():
        detalle += [f"Nota {r['tipo']}-{r['numero']} del {fecha_pantalla(r['fecha'])} sin costo de ventas"
                    for r in con.execute(
                        """SELECT tipo, numero, fecha FROM notas_entrega WHERE asiento_costo_id IS NULL AND fecha <= ?
                             AND producto IN ('CARNE_ENTERO','CARNE_CORTES','EXCEDENTE') ORDER BY fecha""", (hasta,))]
    detalle += [f"Parte de recaudación del {fecha_pantalla(r['fecha'])} sin verificar"
                for r in con.execute("SELECT fecha FROM partes_recaudacion WHERE verificado_por IS NULL AND fecha <= ?",
                                     (hasta,))]
    return detalle


def _pend_codificacion(con: sqlite3.Connection, desde: str, hasta: str) -> list[str]:
    detalle = [f"Asiento {r['tipo']}-{int(r['numero']):05d} descuadrado"
               for r in con.execute(
                   """SELECT v.tipo, v.numero FROM v_asientos_descuadrados v JOIN asientos a ON a.id = v.asiento_id
                       WHERE a.fecha BETWEEN ? AND ?""", (desde, hasta))]
    detalle += [f"Asiento {r['tipo']}-{int(r['numero']):05d}: cuenta {r['cuenta_codigo']} inactiva o de agrupación"
                for r in con.execute(
                    """SELECT a.tipo, a.numero, d.cuenta_codigo FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
                         JOIN cuentas c ON c.codigo = d.cuenta_codigo
                        WHERE a.fecha BETWEEN ? AND ? AND (c.activa = 0 OR c.es_movimiento = 0)""", (desde, hasta))]
    detalle += [f"Asiento {r['tipo']}-{int(r['numero']):05d}: centro {r['cuenta_codigo']} sin sufijo"
                for r in con.execute(
                    """SELECT a.tipo, a.numero, d.cuenta_codigo FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
                        WHERE a.fecha BETWEEN ? AND ? AND d.cuenta_codigo IN (?, ?) AND d.sufijo IS NULL""",
                    (desde, hasta, CENTROS[0], CENTROS[1]))]
    return detalle


_FUNCIONES_PENDIENTES = {"TESORERIA": _pend_tesoreria, "EGRESOS": _pend_egresos, "PRODUCCION": _pend_produccion,
                         "VENTAS": _pend_ventas, "CODIFICACION": _pend_codificacion}


def _verificar_mes(con: sqlite3.Connection, periodo: sqlite3.Row) -> dict[str, Any]:
    desde, hasta = periodo["fecha_inicio"], periodo["fecha_fin"]
    filas = []
    for clave, texto in VERIFICACIONES:
        detalle = _FUNCIONES_PENDIENTES[clave](con, desde, hasta)
        filas.append({"clave": clave, "verificacion": texto, "cumple": not detalle, "pendientes": len(detalle),
                      "detalle": detalle})
    return {"periodo_id": int(periodo["id"]), "periodo": nombre_periodo(periodo), "ejercicio": int(periodo["ejercicio"]),
            "desde": desde, "hasta": hasta, "filas": filas, "pendientes": sum(f["pendientes"] for f in filas),
            "estado_periodo": periodo["estado"]}


def lista_verificacion(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> dict[str, Any]:
    """FIN-INF-01 calculada en el momento (no registra nada)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    return _verificar_mes(con, _periodo(con, periodo_id))


def registrar_lista_verificacion(con: sqlite3.Connection, sesion: Sesion, periodo_id: int,
                                 fecha: Any = None) -> dict[str, Any]:
    """Registra la FIN-INF-01 firmada por la Unidad de Finanzas (PR-FIN-10 paso 1)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "EDITAR")
    f = a_fecha(fecha) if fecha is not None else date.today()
    with transaccion(con):
        periodo = _periodo(con, periodo_id)
        if periodo["estado"] == "CERRADO":
            raise ReglaNegocioError(f"El período de {nombre_periodo(periodo)} ya está cerrado.",
                                    codigo="PERIODO_YA_CERRADO")
        lista = _verificar_mes(con, periodo)
        cargo = sesion.cargo or {"JF": "Jefe de Finanzas y Comercialización"}.get(sesion.rol, sesion.rol)
        cur = con.execute(
            """INSERT INTO listas_cierre (periodo_id, fecha, detalle_json, pendientes, elaborado_por, cargo,
                                          registrado_en) VALUES (?,?,?,?,?,?,?)""",
            (periodo_id, f.isoformat(), auditoria.a_json(lista["filas"]), lista["pendientes"], sesion.nombre_completo,
             cargo, _ahora_iso()))
        lid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "listas_cierre", "INSERT", lid,
                            despues={"periodo": lista["periodo"], "pendientes": lista["pendientes"]},
                            detalle="FIN-INF-01 Lista de Verificación de Cierre Mensual")
    return {**lista, "id": lid, "fecha": f.isoformat(), "elaborado_por": sesion.nombre_completo, "cargo": cargo}


def ultima_lista(con: sqlite3.Connection, periodo_id: int) -> dict[str, Any] | None:
    fila = con.execute("SELECT * FROM listas_cierre WHERE periodo_id = ? ORDER BY id DESC LIMIT 1",
                       (periodo_id,)).fetchone()
    if fila is None:
        return None
    return {**dict(fila), "filas": json.loads(fila["detalle_json"])}


def cerrar_mes(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> dict[str, Any]:
    """Cierre mensual (PR-FIN-10) con bloqueo del período.

    Exige la FIN-INF-01 registrada con pendientes en cero y lo vuelve a
    comprobar al cerrar (ningún cierre se consolida con comprobantes pendientes
    sin resolver). En junio exige además el cierre anual registrado. Archiva el
    paquete FIN-INF-03/FIN-INF-04 y cierra el período (no se reabre).

    Raises:
        AutorizacionError: lo ejecuta la Gerencia General (``contabilidad/AUTORIZAR``).
        ReglaNegocioError: ``SIN_LISTA_VERIFICACION``, ``PENDIENTES_CIERRE``,
            ``CIERRE_ANUAL_PENDIENTE`` o las reglas de ``contable.cerrar_periodo``.
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "AUTORIZAR")
    with transaccion(con):
        periodo = _periodo(con, periodo_id)
        nombre = nombre_periodo(periodo)
        lista = ultima_lista(con, periodo_id)
        if lista is None:
            raise ReglaNegocioError(f"Falta la Lista de Verificación de Cierre Mensual (FIN-INF-01) de {nombre}: "
                                    "la prepara la Unidad de Finanzas.", codigo="SIN_LISTA_VERIFICACION")
        if int(lista["pendientes"]) > 0:
            raise ReglaNegocioError(f"La FIN-INF-01 de {nombre} registra {lista['pendientes']} comprobante(s) "
                                    "pendiente(s): debe llegar a cero antes del cierre.", codigo="PENDIENTES_CIERRE")
        actual = _verificar_mes(con, periodo)
        if actual["pendientes"] > 0:
            raise ReglaNegocioError(
                f"Desde la FIN-INF-01 surgieron {actual['pendientes']} pendiente(s) en {nombre}: "
                + "; ".join(d for f in actual["filas"] for d in f["detalle"][:3]),
                codigo="PENDIENTES_CIERRE", detalle={"filas": actual["filas"]})
        if int(periodo["numero"]) == 12 and con.execute(
                "SELECT 1 FROM cierres_anuales WHERE ejercicio = ?", (periodo["ejercicio"],)).fetchone() is None:
            raise ReglaNegocioError("Junio es el último mes del ejercicio: registre primero el cierre anual "
                                    "(cuentas de resultado a 8.1.01.01).", codigo="CIERRE_ANUAL_PENDIENTE")
        costeo = _fin_inf_03(con, periodo)
        resultados = _fin_inf_04(con, periodo)
        contable.cerrar_periodo(con, sesion, periodo_id)
        cur = con.execute(
            """INSERT INTO cierres_mensuales (periodo_id, lista_id, costeo_json, resultados_json, cerrado_por,
                                              cerrado_en) VALUES (?,?,?,?,?,?)""",
            (periodo_id, int(lista["id"]), auditoria.a_json(costeo), auditoria.a_json(resultados),
             sesion.nombre_usuario, _ahora_iso()))
        cid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "cierres_mensuales", "INSERT", cid,
                            despues={"periodo": nombre, "lista_id": lista["id"],
                                     "excedente_mes": resultados["mes"]["excedente_neto"]},
                            detalle=f"Cierre mensual de {nombre} (PR-FIN-10)")
    return {"id": cid, "periodo": nombre, "costeo": costeo, "resultados": resultados}


def paquete_cierre(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> dict[str, Any] | None:
    """Paquete archivado del cierre mensual (PR-FIN-10 paso 7)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    fila = con.execute("SELECT * FROM cierres_mensuales WHERE periodo_id = ?", (periodo_id,)).fetchone()
    if fila is None:
        return None
    return {**dict(fila), "costeo": json.loads(fila["costeo_json"]), "resultados": json.loads(fila["resultados_json"])}


def periodos_ejercicio(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    """Períodos del ejercicio con su estado, lista y cierre."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    filas = []
    for p in con.execute("SELECT * FROM periodos WHERE ejercicio = ? ORDER BY numero", (ejercicio,)):
        lista = ultima_lista(con, int(p["id"]))
        filas.append({**dict(p), "nombre": nombre_periodo(p),
                      "lista_pendientes": None if lista is None else int(lista["pendientes"]),
                      "lista_fecha": None if lista is None else lista["fecha"],
                      "asientos": int(con.execute("SELECT COUNT(*) FROM asientos WHERE periodo_id = ?",
                                                  (p["id"],)).fetchone()[0])})
    return filas


# ==========================================================================
# 5. ESTADO DE FLUJO DE EFECTIVO — MÉTODO DIRECTO (MAN-006 §18.3)
# ==========================================================================
EFE_COBROS = [("COBRO_POLLOS", "Cobro de Ventas de Pollos Faeneados"),
              ("COBRO_ALIMENTO", "Cobro de Ventas de Alimento balanceado"),
              ("COBRO_GALLINAZA", "Cobro de Ventas de Gallinaza")]
EFE_ANTERIOR = [("ANT_SUELDOS", "Pago de Sueldos devengados"),
                ("ANT_APORTES", "Pago de Aportes patronales devengados"),
                ("ANT_SERVICIOS", "Pago de Servicios basicos devengados")]
EFE_PRODUCCION = [("MP", "Compra de Materia Prima"), ("MP_ALIMENTO", "Compra de Materia Prima Alimento"),
                  ("INSUMOS", "Compra de Insumos y suministros"), ("SAQUILLOS", "Compra de Saquillos para Venta"),
                  ("P_SUELDOS", "Pago de Sueldos"), ("P_AGUINALDOS", "Pago de Aguinaldos"),
                  ("P_EVENTUAL", "Pago a Personal Eventual"), ("P_MANTENIMIENTO", "Pago de Mantenimiento y Limpieza"),
                  ("P_APORTES", "Pago de Aportes Patronales"), ("P_SERVICIOS", "Pago de Servicios Básicos")]
EFE_OPERACION = [("O_SUELDOS", "Pago de Sueldos"), ("O_AGUINALDOS", "Pago de Aguinaldos"),
                 ("O_APORTES", "Pago de Aportes Patronales"), ("O_SENASAG", "Certificación SENASAG"),
                 ("O_RAU", "Impuesto RAU"), ("O_FLETES", "Pago de fletes de venta")]
EFE_INVERSION = [("INV_MAQUINARIA", "Maquinaria para alimento balanceado"),
                 ("INV_AMBIENTE", "Construcción de ambiente para preparación"),
                 ("INV_GALPON", "Construcción de 1 galpón adicional"),
                 ("INV_EQUIPO", "Compra de equipo adicional para 1000 aves"),
                 ("INV_HERRAMIENTAS", "Compra de Herramientas adicional para 3000 aves")]
EFE_FINANCIACION = [("FIN_UTILIDADES", "Pago de Utilidades")]
# DISCREPANCIA: el modelo §18.3 no tiene renglón para otros cobros y pagos
# (intereses, IBNORCA, materiales de oficina, indemnizaciones pagadas), otras
# inversiones ni aportes de capital en efectivo. Para que el saldo final cuadre
# con las disponibilidades, se agregan estos renglones SOLO cuando tienen monto.
EFE_EXTRAS = {"COBRO_OTROS": "Otros cobros de operación", "O_OTROS": "Otros pagos de operación",
              "INV_OTRAS": "Otras inversiones (neto de ventas de activos)",
              "FIN_APORTES": "Aportes de capital recibidos en efectivo"}
_CUENTA_A_INVERSION = {"1.2.01.05": "INV_MAQUINARIA", "1.2.01.04": "INV_AMBIENTE", "1.2.01.02": "INV_GALPON",
                       "1.2.01.06": "INV_EQUIPO", "1.2.01.07": "INV_HERRAMIENTAS"}
_SUFIJO_A_RUBRO = {"01": "MP", "03": "MP_ALIMENTO", "04": "MP_ALIMENTO", "05": "MP_ALIMENTO", "06": "MP_ALIMENTO",
                   "07": "MP_ALIMENTO", "02": "INSUMOS", "08": "INSUMOS", "09": "INSUMOS", "10": "INSUMOS",
                   "11": "INSUMOS", "12": "INSUMOS", "13": "SAQUILLOS", "20": "P_SUELDOS", "21": "P_AGUINALDOS",
                   "22": "P_EVENTUAL", "36": "P_MANTENIMIENTO", "37": "P_SERVICIOS", "38": "P_APORTES",
                   "39": "O_OTROS"}
_CUENTA_A_RUBRO = {
    "5.1.01.01": "COBRO_POLLOS", "1.1.02.01": "COBRO_POLLOS", "2.1.04.01": "COBRO_POLLOS",
    "5.1.04.01": "COBRO_POLLOS", "5.1.02.01": "COBRO_ALIMENTO", "5.2.01.04": "COBRO_GALLINAZA",
    "1.1.03.01": "MP", "2.1.01.01": "MP", "2.1.01.03": "MP_ALIMENTO", "1.1.03.02": "INSUMOS",
    "1.1.03.08": "INSUMOS", "1.1.03.09": "INSUMOS", "1.1.03.10": "INSUMOS", "1.1.03.11": "INSUMOS",
    "2.1.01.02": "INSUMOS", "2.1.01.04": "INSUMOS", "2.1.01.05": "INSUMOS", "1.1.04.01": "INSUMOS",
    "1.1.03.15": "P_MANTENIMIENTO", "2.1.02.04": "P_EVENTUAL", "1.1.04.04": "P_EVENTUAL",
    "2.1.02.01": "*SUELDOS", "2.1.02.05": "*SUELDOS", "2.1.02.02": "*AGUINALDOS", "2.1.02.03": "*APORTES",
    "2.1.01.06": "*SERVICIOS", "7.1.01.01": "O_SUELDOS", "7.1.01.02": "O_AGUINALDOS", "7.1.01.03": "O_APORTES",
    "7.1.04.01": "O_SENASAG", "7.1.05.02": "O_RAU", "2.1.03.01": "O_RAU", "7.1.03.01": "O_FLETES",
    "2.1.05.01": "FIN_UTILIDADES", "3.1.03.04": "FIN_UTILIDADES",
}
_PRORRATEO = {"*SUELDOS": ("P_SUELDOS", "O_SUELDOS", "20", "7.1.01.01"),
              "*AGUINALDOS": ("P_AGUINALDOS", "O_AGUINALDOS", "21", "7.1.01.02"),
              "*APORTES": ("P_APORTES", "O_APORTES", "38", "7.1.01.03")}
_ANTERIOR = {"*SUELDOS": "ANT_SUELDOS", "*APORTES": "ANT_APORTES", "*SERVICIOS": "ANT_SERVICIOS"}
_CUENTAS_ANTERIOR = {"*SUELDOS": ("2.1.02.01", "2.1.02.05"), "*APORTES": ("2.1.02.03",),
                     "*SERVICIOS": ("2.1.01.06",)}
_PATRON_CAPEX = re.compile(r"CAPEX-REF-(\d+)")


def rubro_flujo(con: sqlite3.Connection, cuenta: str, sufijo: str | None, documento_ref: str | None) -> str:
    """Renglón del flujo directo al que va el efectivo según la contrapartida."""
    if cuenta in CENTROS:
        return _SUFIJO_A_RUBRO.get(sufijo or "", "INSUMOS")
    if cuenta in _CUENTA_A_RUBRO:
        return _CUENTA_A_RUBRO[cuenta]
    if cuenta.startswith("1.1.03.0") and cuenta in {f"1.1.03.0{i}" for i in range(3, 8)}:
        return "MP_ALIMENTO"
    if cuenta.startswith("1.1.03."):
        return "INSUMOS"
    if cuenta in _CUENTA_A_INVERSION:
        return _CUENTA_A_INVERSION[cuenta]
    if cuenta == "1.2.03.02" and documento_ref:
        coincide = _PATRON_CAPEX.search(documento_ref)
        if coincide:
            ref = con.execute("SELECT categoria FROM capex_referencia WHERE id = ?",
                              (int(coincide.group(1)),)).fetchone()
            if ref is not None:
                categoria = activos.CATEGORIAS.get(ref["categoria"])
                cuenta_activo = categoria.cuenta_activo if categoria is not None else None
                if cuenta_activo in _CUENTA_A_INVERSION:
                    return _CUENTA_A_INVERSION[cuenta_activo]
    if cuenta.startswith(("1.2.", "4.1.02.")):
        return "INV_OTRAS"
    if cuenta.startswith(("3.1.01.", "3.1.04.", "4.3.")):
        return "FIN_APORTES"
    if cuenta.startswith("5."):
        return "COBRO_OTROS"
    if cuenta.startswith("6.1.02."):
        return "INSUMOS"
    return "O_OTROS"


def estado_flujo_efectivo(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, hasta: Any = None) -> dict[str, Any]:
    """Estado de Flujo de Efectivo por el método directo (MAN-006 §18.3).

    Cada asiento que mueve disponibilidades (1.1.01.xx) se clasifica por sus
    contrapartidas. Los pagos de pasivos laborales y de servicios existentes al
    inicio del ejercicio se exponen primero como «Pagos Anterior gestión»; el
    resto de sueldos, aguinaldos y aportes se reparte entre producción y
    operación en proporción a su devengo del ejercicio (sufijos 20/21/38 frente
    a 7.1.01.01/02/03).
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    inicio, fin = rango_ejercicio(con, ejercicio)
    corte = min(a_fecha(hasta), fin) if hasta is not None else fin
    previos = saldos(con, inicio - timedelta(days=1))
    apertura_ids = [int(f[0]) for f in con.execute(
        "SELECT id FROM asientos WHERE tipo = 'APERTURA' AND fecha BETWEEN ? AND ?",
        (inicio.isoformat(), corte.isoformat()))]
    s_apertura = {c: 0 for c in CUENTAS_EFECTIVO}
    for aid in apertura_ids:
        for d in con.execute("SELECT cuenta_codigo, debe_cent, haber_cent FROM asiento_detalle WHERE asiento_id = ?",
                             (aid,)):
            s_apertura[d[0]] = s_apertura.get(d[0], 0) + int(d[1]) - int(d[2])
    saldo_inicial = _suma(previos, CUENTAS_EFECTIVO) + _suma(s_apertura, CUENTAS_EFECTIVO)
    pendiente_anterior = {}
    for grupo, cuentas in _CUENTAS_ANTERIOR.items():
        pasivo = -_suma(previos, cuentas) - sum(s_apertura.get(c, 0) for c in cuentas)
        pendiente_anterior[grupo] = max(pasivo, 0)
    montos: dict[str, int] = {}

    def sumar(rubro: str, entrada: int) -> None:
        montos[rubro] = montos.get(rubro, 0) + entrada

    asientos = con.execute(
        """SELECT a.id, a.documento_ref FROM asientos a WHERE a.fecha BETWEEN ? AND ? AND a.tipo NOT IN ('APERTURA','CIERRE')
              AND EXISTS (SELECT 1 FROM asiento_detalle d WHERE d.asiento_id = a.id AND d.cuenta_codigo IN (?,?,?,?,?))
            ORDER BY a.fecha, a.id""", (inicio.isoformat(), corte.isoformat(), *CUENTAS_EFECTIVO)).fetchall()
    for a in asientos:
        lineas = con.execute("SELECT cuenta_codigo, debe_cent, haber_cent, sufijo FROM asiento_detalle "
                             "WHERE asiento_id = ? ORDER BY linea", (a["id"],)).fetchall()
        efectivo = sum(int(l[1]) - int(l[2]) for l in lineas if l[0] in CUENTAS_EFECTIVO)
        if efectivo == 0:
            continue
        for l in lineas:
            if l[0] in CUENTAS_EFECTIVO:
                continue
            aporte = int(l[2]) - int(l[1])
            if aporte == 0:
                continue
            rubro = rubro_flujo(con, l[0], l[3], a["documento_ref"])
            if rubro in _ANTERIOR and aporte < 0 and pendiente_anterior.get(rubro, 0) > 0:
                tomado = min(-aporte, pendiente_anterior[rubro])
                pendiente_anterior[rubro] -= tomado
                sumar(_ANTERIOR[rubro], -tomado)
                aporte += tomado
                if aporte == 0:
                    continue
            if rubro == "*SERVICIOS":
                rubro = "P_SERVICIOS"
            sumar(rubro, aporte)
    por_sufijo = _costo_por_sufijo(con, inicio, corte)
    gastos = saldos(con, corte, inicio, TIPOS_FUERA_DE_RESULTADOS)
    for grupo, (prod, oper, sufijo, cuenta) in _PRORRATEO.items():
        total = montos.pop(grupo, 0)
        if total == 0:
            continue
        devengo_prod = max(por_sufijo.get(sufijo, 0), 0)
        devengo_oper = max(gastos.get(cuenta, 0), 0)
        base = devengo_prod + devengo_oper
        parte_prod = 0 if base == 0 else int((Decimal(total) * devengo_prod / base).to_integral_value(ROUND_HALF_UP))
        sumar(prod, parte_prod)
        sumar(oper, total - parte_prod)

    def linea(clave: str, nombre: str, egreso: bool) -> dict[str, Any]:
        valor = montos.get(clave, 0)
        return _fila(nombre, -valor if egreso else valor, "linea")

    filas: list[dict[str, Any]] = [_fila("SALDO INICIAL DE EFECTIVO", saldo_inicial, "total"),
                                   _fila("ACTIVIDADES DE OPERACIÓN", None, "titulo"),
                                   _fila("Ingresos de Efectivo", None, "subtitulo")]
    filas += [linea(k, n, False) for k, n in EFE_COBROS]
    if montos.get("COBRO_OTROS"):
        filas.append(linea("COBRO_OTROS", EFE_EXTRAS["COBRO_OTROS"], False))
    ingresos = sum(montos.get(k, 0) for k, _ in EFE_COBROS) + montos.get("COBRO_OTROS", 0)
    filas.append(_fila("Total ingresos de Operación", ingresos, "total"))
    filas.append(_fila("Pagos Anterior gestión", None, "subtitulo"))
    filas += [linea(k, n, True) for k, n in EFE_ANTERIOR]
    filas.append(_fila("Egresos de Producción", None, "subtitulo"))
    filas += [linea(k, n, True) for k, n in EFE_PRODUCCION]
    egresos_oper = -sum(montos.get(k, 0) for k, _ in EFE_OPERACION) - montos.get("O_OTROS", 0)
    filas.append(_fila("Egresos de Operación", egresos_oper, "subtitulo"))
    filas += [linea(k, n, True) for k, n in EFE_OPERACION]
    if montos.get("O_OTROS"):
        filas.append(linea("O_OTROS", EFE_EXTRAS["O_OTROS"], True))
    egresos = -sum(montos.get(k, 0) for k, _ in EFE_ANTERIOR + EFE_PRODUCCION) + egresos_oper
    filas.append(_fila("Total Egresos de Operación", egresos, "total"))
    flujo_operativo = ingresos - egresos
    filas.append(_fila("FLUJO NETO DE EFECTIVO OPERATIVO", flujo_operativo, "total"))
    acumulado1 = saldo_inicial + flujo_operativo
    filas.append(_fila("SALDO ACUMULADO", acumulado1, "total"))
    filas.append(_fila("ACTIVIDADES DE INVERSION", None, "titulo"))
    filas += [linea(k, n, True) for k, n in EFE_INVERSION]
    if montos.get("INV_OTRAS"):
        filas.append(linea("INV_OTRAS", EFE_EXTRAS["INV_OTRAS"], True))
    inversion = -sum(montos.get(k, 0) for k, _ in EFE_INVERSION) - montos.get("INV_OTRAS", 0)
    filas.append(_fila("Total Egresos de Inversión", inversion, "total"))
    acumulado2 = acumulado1 - inversion
    filas.append(_fila("SALDO ACUMULADO", acumulado2, "total"))
    filas.append(_fila("ACTIVIDADES DE FINANCIACION", None, "titulo"))
    filas += [linea(k, n, True) for k, n in EFE_FINANCIACION]
    if montos.get("FIN_APORTES"):
        filas.append(linea("FIN_APORTES", EFE_EXTRAS["FIN_APORTES"], False))
    financiacion = montos.get("FIN_UTILIDADES", 0) + montos.get("FIN_APORTES", 0)
    saldo_final = acumulado2 + financiacion
    filas.append(_fila("SALDO ACUMULADO", saldo_final, "total"))
    real = _suma(saldos(con, corte), CUENTAS_EFECTIVO)
    no_clasificado = sum(v for k, v in montos.items() if k.startswith("*"))
    return {
        "ejercicio": ejercicio, "desde": inicio.isoformat(), "hasta": corte.isoformat(), "filas": filas,
        "saldo_inicial": _bs(saldo_inicial), "ingresos_operacion": _bs(ingresos), "egresos_operacion": _bs(egresos),
        "flujo_operativo": _bs(flujo_operativo), "inversion": _bs(inversion), "financiacion": _bs(financiacion),
        "saldo_final": _bs(saldo_final), "disponibilidades_segun_libros": _bs(real),
        "cuadra": saldo_final == real and no_clasificado == 0,
        "montos": {k: _bs(v) for k, v in sorted(montos.items())},
    }


# ==========================================================================
# 6. ESTADO DE EVOLUCIÓN DEL PATRIMONIO NETO (MAN-006 §18.4)
# ==========================================================================
# DISCREPANCIA: el modelo §18.4 solo tiene las columnas Capital Social, Res.
# Educación, Excedentes Ej. Ant. y Total, pero la Nota 12.2 exige la Reserva
# Legal y CI-FIN-02 la Reserva de Reinversión. Para que el EEPN concilie con el
# Balance, las columnas «Reserva Legal» y «Otras reservas» se agregan solo
# cuando tienen saldo o movimiento.
COLUMNAS_EEPN = (("capital", "Capital Social"), ("reserva_legal", "Reserva Legal"),
                 ("educacion", "Res. Educación"), ("otras_reservas", "Otras reservas"),
                 ("excedentes", "Excedentes Ej. Ant."))
_COLUMNAS_OPCIONALES = ("reserva_legal", "otras_reservas")


def _columna_patrimonio(cuenta: str) -> str | None:
    if cuenta.startswith(("3.1.01.", "3.1.04.")) or cuenta == "4.3.01.01":
        return "capital"
    if cuenta == CUENTA_RESERVA_LEGAL:
        return "reserva_legal"
    if cuenta == CUENTA_EDUCACION:
        return "educacion"
    if cuenta.startswith("3.1.02."):
        return "otras_reservas"
    if cuenta.startswith("3.1.03.") or es_resultado(cuenta):
        return "excedentes"
    return None


def estado_evolucion_patrimonio(con: sqlite3.Connection, sesion: Sesion, ejercicio: int,
                                hasta: Any = None) -> dict[str, Any]:
    """EEPN del ejercicio (MAN-006 §18.4): saldo al 1-jul, aportes de capital,
    excedente del ejercicio (con su apropiación a reservas), distribución a
    socios y saldo al 30-jun. Concilia con el patrimonio del Balance General."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    inicio, fin = rango_ejercicio(con, ejercicio)
    corte = min(a_fecha(hasta), fin) if hasta is not None else fin
    claves = [c for c, _ in COLUMNAS_EEPN]
    filas_mov = {f: {c: 0 for c in claves} for f in ("inicial", "aportes", "excedente", "distribucion")}
    previos = saldos(con, inicio - timedelta(days=1))
    for cuenta, m in previos.items():
        col = _columna_patrimonio(cuenta)
        if col:
            filas_mov["inicial"][col] -= m
    asientos = con.execute(
        "SELECT id, tipo FROM asientos WHERE fecha BETWEEN ? AND ? AND tipo <> 'CIERRE' ORDER BY fecha, id",
        (inicio.isoformat(), corte.isoformat())).fetchall()
    for a in asientos:
        lineas = con.execute("SELECT cuenta_codigo, debe_cent, haber_cent FROM asiento_detalle WHERE asiento_id = ?",
                             (a["id"],)).fetchall()
        relevantes = [(l[0], int(l[2]) - int(l[1])) for l in lineas if _columna_patrimonio(l[0])]
        if not relevantes:
            continue
        cuentas = {l[0] for l in lineas}
        if a["tipo"] == "APERTURA":
            fila = "inicial"
        elif CUENTA_EXCEDENTES_POR_PAGAR in cuentas and CUENTA_EN_DISTRIBUCION in cuentas:
            fila = "distribucion"
        elif any(c.startswith("3.1.02.") for c in cuentas):
            fila = "excedente"
        elif any(c.startswith(("3.1.01.", "3.1.04.")) or c == "4.3.01.01" for c in cuentas):
            fila = "aportes"
        else:
            fila = "excedente"
        for cuenta, monto in relevantes:
            filas_mov[fila][_columna_patrimonio(cuenta)] += monto
    final = {c: sum(filas_mov[f][c] for f in filas_mov) for c in claves}
    visibles = [c for c in claves if c not in _COLUMNAS_OPCIONALES
                or any(filas_mov[f][c] for f in filas_mov)]
    etiquetas = dict(COLUMNAS_EEPN)

    def fila(concepto: str, valores: Mapping[str, int], tipo: str = "linea") -> dict[str, Any]:
        return {"concepto": concepto, "tipo": tipo, **{c: _bs(valores[c]) for c in visibles},
                "total": _bs(sum(valores[c] for c in claves))}

    filas = [
        fila(f"Saldo al {fecha_pantalla(inicio)}", filas_mov["inicial"], "total"),
        fila("(+) Aportes de capital", filas_mov["aportes"]),
        fila("(+) Excedente del ejercicio", filas_mov["excedente"]),
        fila("(-) Distribución a socios", filas_mov["distribucion"]),
        fila(f"Saldo al {fecha_pantalla(corte)}", final, "total"),
    ]
    bg = balance_general(con, sesion, corte)
    total_final = sum(final.values())
    return {"ejercicio": ejercicio, "desde": inicio.isoformat(), "hasta": corte.isoformat(),
            "columnas": [(c, etiquetas[c]) for c in visibles], "filas": filas, "total_final": _bs(total_final),
            "cuadra": _bs(total_final) == bg["total_patrimonio"]}


# ==========================================================================
# 7. INDICADORES ANUALES Y PAQUETE DE ESTADOS
# ==========================================================================
def indicadores_anuales(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """ICL, RINC y ROA del ejercicio con el Estado de Resultados y el Balance."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    inicio, fin = rango_ejercicio(con, ejercicio)
    c = _cifras_resultado(saldos(con, fin, inicio, TIPOS_FUERA_DE_RESULTADOS))
    return _indicadores(c, _activo_total(saldos(con, fin)), _params(con, ejercicio))


def estados_financieros(con: sqlite3.Connection, sesion: Sesion, ejercicio: int,
                        fecha_aprobacion_ca: Any = None, hechos_posteriores: str | None = None) -> dict[str, Any]:
    """Paquete completo: Balance, Resultados, Flujo, EEPN, Notas 1-16 e indicadores."""
    _, fin = rango_ejercicio(con, ejercicio)
    return {
        "ejercicio": ejercicio, "fecha_corte": fin.isoformat(),
        "balance": balance_general(con, sesion, fin),
        "resultados": estado_resultados(con, sesion, ejercicio),
        "flujo": estado_flujo_efectivo(con, sesion, ejercicio),
        "patrimonio": estado_evolucion_patrimonio(con, sesion, ejercicio),
        "notas": generar_notas(con, sesion, ejercicio, fecha_aprobacion_ca, hechos_posteriores),
        "indicadores": indicadores_anuales(con, sesion, ejercicio),
        "umbrales": umbrales_texto(_params(con, ejercicio)),
    }


# ==========================================================================
# 8. NOTAS 1 A 16 (MAN-006 §19)
# ==========================================================================
def _parrafo(texto: str) -> dict[str, Any]:
    return {"tipo": "parrafo", "texto": texto}


def _subtitulo(texto: str) -> dict[str, Any]:
    return {"tipo": "subtitulo", "texto": texto}


def _tabla(encabezados: Sequence[str], filas: Sequence[Sequence[Any]], alinear: Sequence[int] = (),
           totales: int = 1) -> dict[str, Any]:
    return {"tipo": "tabla", "encabezados": list(encabezados), "filas": [list(f) for f in filas],
            "alinear": list(alinear), "totales": totales}


def _f(cent: int) -> str:
    return formato_bs(_bs(cent))


def _fp(cent: int) -> str:
    """Monto entre paréntesis para reguladoras (depreciación acumulada)."""
    return f"({formato_bs(_bs(abs(cent)))})" if cent else formato_bs(CERO)


def generar_notas(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha_aprobacion_ca: Any = None,
                  hechos_posteriores: str | None = None) -> list[dict[str, Any]]:
    """Notas 1 a 16 del MAN-006 §19 con los espacios [Bs XXX.XXX] completados con
    los saldos reales del cierre y las tasas de los parámetros vigentes."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    inicio, fin = rango_ejercicio(con, ejercicio)
    p = _params(con, ejercicio)
    s = saldos(con, fin)
    previos = saldos(con, inicio - timedelta(days=1))
    mov = _movimientos(con, inicio, fin, TIPOS_FUERA_DE_RESULTADOS)
    apertura_mov = {}
    for d in con.execute(
            """SELECT d.cuenta_codigo, d.debe_cent, d.haber_cent FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
                WHERE a.tipo = 'APERTURA' AND a.fecha BETWEEN ? AND ?""", (inicio.isoformat(), fin.isoformat())):
        apertura_mov[d[0]] = apertura_mov.get(d[0], 0) + int(d[1]) - int(d[2])
    er = _cifras_resultado(saldos(con, fin, inicio, TIPOS_FUERA_DE_RESULTADOS))
    presentacion = con.execute("SELECT * FROM presentaciones_eeff WHERE ejercicio = ?", (ejercicio,)).fetchone()
    aprobacion_ca = (fecha_pantalla(fecha_aprobacion_ca) if fecha_aprobacion_ca is not None else
                     fecha_pantalla(presentacion["fecha_consejos"]) if presentacion and presentacion["fecha_consejos"]
                     else "[pendiente de aprobación por el Consejo de Administración]")
    periodo_txt = f"{fecha_pantalla(inicio)} y el {fecha_pantalla(fin)}"
    socios = int(con.execute("SELECT COUNT(*) FROM socios WHERE estado = 'ACTIVO'").fetchone()[0])
    notas: list[dict[str, Any]] = []

    # NOTA 1
    notas.append({"numero": 1, "titulo": "NATURALEZA, CONSTITUCION Y OBJETO SOCIAL", "bloques": [
        _subtitulo("1.1 Constitución legal y antecedentes"),
        _parrafo("La Cooperativa Agropecuaria Pollolandia R.L. (en adelante «la Cooperativa») fue constituida el 1 de "
                 "mayo de 2026 en la localidad de Caranavi, Provincia Caranavi, Departamento de La Paz, Estado "
                 f"Plurinacional de Bolivia, mediante Acta de Constitución suscrita por {socios} socios fundadores, al "
                 "amparo de la Ley N.° 356 de 11 de abril de 2013 — Ley General de Cooperativas — y su Decreto Supremo "
                 "Reglamentario N.° 1995 de 21 de mayo de 2014."),
        _parrafo("La Cooperativa se encuentra inscrita ante la Autoridad de Fiscalización y Control de Cooperativas "
                 "(AFCOOP) y registrada ante el Servicio de Impuestos Nacionales (SIN), empadronada en el Régimen "
                 "Agropecuario Unificado (RAU) conforme se describe en el numeral 1.3. [NIT: dato registral a completar "
                 "por la Gerencia; no es un saldo contable.] En su calidad de sociedad cooperativa, se sujeta "
                 "supletoriamente al Código de Comercio en cuanto a llevar registros contables regulares y formular "
                 "estados financieros anuales."),
        _parrafo("El domicilio legal y el centro de operaciones productivas se ubican en la región de Los Yungas, "
                 "Provincia Caranavi, Departamento de La Paz."),
        _subtitulo("1.2 Objeto social y actividades"),
        _parrafo(f"El objeto principal es la producción, crianza, engorde, faenado, comercialización y distribución de "
                 f"pollo criollo de ciclo natural de {_param(p, 'dias_ciclo')} días, bajo el sello «Vida Digna», "
                 "orientado a los mercados HORECA, familiar y de mercados populares de Los Yungas. Como actividad "
                 "complementaria, en el marco de la integración vertical aprobada por la Asamblea, la Cooperativa "
                 "incorpora la elaboración de alimento balanceado artesanal para autoconsumo y venta a terceros."),
        _subtitulo("1.3 Régimen tributario"),
        _parrafo("La Cooperativa está empadronada en el Régimen Agropecuario Unificado (RAU), regulado por el D.S. "
                 "N.° 24463, el D.S. N.° 0099 y la Ley N.° 843 (T.O.), que sustituye al IVA, IT e IUE por una alícuota "
                 "fija anual según la superficie declarada. Las obligaciones formales del RAU se detallan en la Nota 10."),
        _subtitulo("1.4 Estructura societaria y gobierno cooperativo"),
        _parrafo(f"La Cooperativa está integrada por {socios} socios fundadores con responsabilidad limitada al monto de "
                 "sus aportes (Ley N.° 356, Art. 32 y siguientes). El gobierno interno se estructura en la Asamblea "
                 "General de Socios, el Consejo de Administración y el Consejo de Vigilancia."),
        _subtitulo("1.5 Ejercicio económico y período cubierto"),
        _parrafo(f"El ejercicio económico comprende del 1 de julio al 30 de junio. Los presentes estados financieros "
                 f"corresponden al ejercicio comprendido entre el {periodo_txt}. Fueron aprobados para su emisión por "
                 f"el Consejo de Administración el {aprobacion_ca} y quedan sujetos a la aprobación de la Asamblea "
                 "General Ordinaria de Socios (Nota 16)."),
    ]})

    # NOTA 2
    tasa_intangible = Decimal(_param(p, activos.clave_tasa("software")))
    notas.append({"numero": 2, "titulo": "BASES DE PRESENTACIÓN Y POLITICAS CONTABLES", "bloques": [
        _subtitulo("2.1 Declaración de cumplimiento"),
        _parrafo("Los estados financieros se preparan de acuerdo con las Normas de Contabilidad Generalmente "
                 "Aceptadas en Bolivia (NCGA) emitidas por el CTNAC del CAUB; las NIIF se aplican de forma "
                 "estrictamente supletoria (excepto NIIF S1 y S2)."),
        _subtitulo("2.2 Moneda de cuenta"),
        _parrafo("Los estados financieros están expresados en Bolivianos (Bs), moneda de cuenta y funcional (NC 1)."),
        _subtitulo("2.3 Base contable y de registro"),
        _parrafo("Partida doble y base de lo devengado."),
        _subtitulo("2.4 Efectos de la inflación"),
        _parrafo("El ajuste integral por inflación está suspendido (Resolución CTNAC N.° 03/2020); los estados no "
                 "incorporan reexpresión."),
        _subtitulo("2.5 Uso de estimaciones contables"),
        _parrafo("Las estimaciones más significativas son la vida útil del activo fijo, la mortalidad esperada del "
                 "ciclo, la previsión para incobrables y el valor neto de realización de los inventarios."),
        _subtitulo("2.6 Disponibilidades"),
        _parrafo("Se registran a su valor nominal y se sujetan a la política de Saldo Mínimo de Caja (Nota 3)."),
        _subtitulo("2.7 Inventarios"),
        _parrafo(f"Se valúan al Costo Promedio Ponderado o al valor neto de realización, el menor. Las mermas y la "
                 f"mortandad normales (hasta el {_pct_norma(_param(p, 'mortalidad_anormal'))} de las unidades "
                 "ingresadas) se absorben en el costo; las anormales se reconocen como gasto del ejercicio."),
        _subtitulo("2.8 Activos biológicos"),
        _parrafo(f"Los pollos en engorde se valúan a su costo de producción acumulado (excepción de medición al costo "
                 f"de la NIC 41 aplicada supletoriamente). El ciclo biológico es de {_param(p, 'dias_ciclo')} días."),
        _subtitulo("2.9 Propiedad, planta y equipo"),
        _parrafo("Costo histórico; depreciación en línea recta desde el mes de habilitación con las tasas de la Nota 6; "
                 "los terrenos no se deprecian."),
        _subtitulo("2.10 Activos intangibles"),
        _parrafo(f"Costo histórico, amortización lineal a la tasa del {_pct_norma(tasa_intangible)} anual."),
        _subtitulo("2.11 Beneficios sociales al personal"),
        _parrafo(f"Aguinaldo provisionado mensualmente al {_pct_norma(_param(p, 'aguinaldo_pct'))} del salario bruto; "
                 f"indemnización provisionada al {_pct_norma(_param(p, 'indemnizacion_pct'))} desde el inicio de la "
                 f"relación laboral (D.S. N.° 110 y N.° 190); aportes laborales retenidos del "
                 f"{_pct_norma(_param(p, 'aporte_laboral'))} y aportes patronales del "
                 f"{_pct_norma(_param(p, 'aporte_patronal'))} (Notas 9 y 11)."),
        _subtitulo("2.12 Reconocimiento de ingresos"),
        _parrafo("Los ingresos se reconocen con la transferencia de riesgos y beneficios, que coincide con la entrega "
                 "física del producto (Nota 13)."),
        _subtitulo("2.13 Cambios de política contable"),
        _parrafo("Las políticas se aplicaron de manera uniforme en el ejercicio; solo se modifican por resolución de "
                 "Asamblea con registro del impacto en notas."),
    ]})

    # NOTA 3
    caja = s.get("1.1.01.01", 0) + s.get("1.1.01.02", 0)
    nota3 = [
        ["Caja — Efectivo en Moneda Nacional", "1.1.01.01", _f(s.get("1.1.01.01", 0))],
        ["Banco — Cuenta Corriente Moneda Nacional", "1.1.01.03", _f(s.get("1.1.01.03", 0))],
        ["Banco — Cuenta de Ahorros", "1.1.01.04", _f(s.get("1.1.01.04", 0))],
        # DISCREPANCIA: la tabla de la Nota 3 omite el Fondo Rotativo (1.1.01.05),
        # que el balance sí remite a la Nota 3: se incluye para que el total
        # coincida con las disponibilidades del balance.
        ["Fondo Rotativo — Caja Chica", "1.1.01.05", _f(s.get("1.1.01.05", 0))],
    ]
    if s.get("1.1.01.02", 0):
        nota3.insert(1, ["Caja — Efectivo en Moneda Extranjera", "1.1.01.02", _f(s.get("1.1.01.02", 0))])
    total_disp = _suma(s, CUENTAS_EFECTIVO)
    nota3.append(["TOTAL DISPONIBILIDADES", "1.1.01", _f(total_disp)])
    sme = tesoreria.sme_vigente(con, fin)
    del caja
    notas.append({"numero": 3, "titulo": "DISPONIBILIDADES", "bloques": [
        _parrafo(f"El rubro Disponibilidades al {fecha_pantalla(fin)} se compone de la siguiente manera:"),
        _tabla(["Concepto", "Cuenta", "Saldo (Bs)"], nota3, (2,)),
        _parrafo("Los saldos en caja y bancos son de libre disponibilidad para las operaciones corrientes."),
        _parrafo(f"La Política de Saldo Mínimo de Caja exige un saldo bancario igual o superior al costo del ciclo "
                 f"siguiente con un margen de seguridad de {_pct_norma(_param(p, 'sme_margen_seguridad_ciclos'))} del "
                 f"ciclo. El SME vigente del ejercicio es {formato_bs(sme)} y el saldo bancario al cierre es "
                 f"{formato_bs(tesoreria.saldo_bancario(con, fin))}."),
    ]})

    # NOTA 4
    def inv(cuentas: Sequence[str]) -> int:
        return _suma(s, cuentas)

    nota4 = [
        ["Materias primas e insumos para alimento balanceado (maíz, soya, afrecho, núcleo vitamínico, aceite vegetal)",
         "1.1.03.03 a 1.1.03.07", _f(inv([f"1.1.03.0{i}" for i in range(3, 8)]))],
        # La tabla del modelo no tiene renglón para pollitos BB ni alimento
        # comprado (1.1.03.01/02), que el balance sí expone: se agregan.
        ["Pollitos BB y alimento balanceado comprado a terceros", "1.1.03.01 / 1.1.03.02",
         _f(inv(["1.1.03.01", "1.1.03.02"]))],
        ["Productos veterinarios e insumos de sanidad animal", "1.1.03.08", _f(inv(["1.1.03.08"]))],
        ["Insumos de limpieza, desinfección y crianza", "1.1.03.09 / 1.1.03.10", _f(inv(["1.1.03.09", "1.1.03.10"]))],
        ["Insumos de embalaje y comercialización", "1.1.03.11", _f(inv(["1.1.03.11"]))],
        ["Productos terminados — Carne de pollo", "1.1.03.12", _f(inv(["1.1.03.12"]))],
        ["Productos terminados — Alimento balanceado", "1.1.03.13", _f(inv(["1.1.03.13"]))],
        ["Subproductos — Gallinaza (abono orgánico)", "1.1.03.14", _f(inv(["1.1.03.14"]))],
        ["Materiales de mantenimiento", "1.1.03.15", _f(inv(["1.1.03.15"]))],
        ["TOTAL INVENTARIOS", "", _f(inv([f"1.1.03.{i:02d}" for i in range(1, 16)]))],
    ]
    notas.append({"numero": 4, "titulo": "INVENTARIOS", "bloques": [
        _parrafo(f"Al {fecha_pantalla(fin)}, la composición de las existencias valuadas al Costo Promedio Ponderado "
                 "es la siguiente:"),
        _tabla(["Categoría de inventario", "Cuenta", "Saldo (Bs)"], nota4, (2,)),
        _parrafo(f"Inventario perpetuo al CPP o valor neto de realización, el menor. Las mermas y mortandad normales "
                 f"(hasta el {_pct_norma(_param(p, 'mortalidad_anormal'))}) se absorben en el costo; las anormales se "
                 "reconocen como gasto. La Administración no identificó ajustes por deterioro."),
    ]})

    # NOTA 5
    aves_ini, aves_bajas = _aves_en_proceso(con, fin)
    biologicos = _suma(s, CENTROS)
    costo_pollitos = int(con.execute(
        """SELECT COALESCE(SUM(costo_pollitos_cent), 0) FROM ciclos_produccion c
            WHERE c.fecha_inicio <= ? AND NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = c.id)""",
        (fin.isoformat(),)).fetchone()[0])
    costo_mortalidad = int(con.execute(
        """SELECT COALESCE(SUM(m.monto_cent), 0) FROM bajas_mortalidad m JOIN ciclos_produccion c ON c.id = m.ciclo_id
            WHERE m.clasificacion = 'NORMAL' AND NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = c.id)
              AND m.fecha <= ?""", (fin.isoformat(),)).fetchone()[0])
    directos = biologicos - costo_pollitos + costo_mortalidad
    notas.append({"numero": 5, "titulo": "ACTIVOS BIOLOGICOS", "bloques": [
        _parrafo(f"El saldo representa el costo acumulado de los lotes en engorde al cierre (ciclo natural de "
                 f"{_param(p, 'dias_ciclo')} días):"),
        _tabla(["Concepto", "Unidades", "Importe (Bs)"], [
            ["Costo inicial de los lotes (pollitos BB incorporados al proceso)", f"{aves_ini:,}".replace(",", "."),
             _f(costo_pollitos)],
            ["Más: costos directos de crianza acumulados", "—", _f(directos)],
            ["Subtotal costos incurridos", f"{aves_ini:,}".replace(",", "."), _f(costo_pollitos + directos)],
            ["Menos: baja por mortalidad normal del período (absorbida en el costo)",
             f"({aves_bajas:,})".replace(",", "."), _fp(costo_mortalidad)],
            ["SALDO DE ACTIVOS BIOLÓGICOS AL CIERRE", f"{aves_ini - aves_bajas:,}".replace(",", "."), _f(biologicos)],
        ], (1, 2)),
        _parrafo("El costo de cada lote comprende pollitos BB, alimento, mano de obra directa, sanidad animal y una "
                 "porción razonable de los costos indirectos. Al cierre de cada ciclo el activo biológico se transfiere "
                 "a Productos Terminados — Carne de Pollo (Nota 4)."),
    ]})

    # NOTA 6
    categorias_ppe = [("Terrenos", "terrenos"), ("Galpones de producción", "galpones"),
                      ("Alambrados y cercados", "alambrados"),
                      ("Ambiente de preparación de alimento balanceado", "ambiente_preparacion"),
                      ("Maquinaria de producción de alimento balanceado", "maquinaria_alimento"),
                      ("Equipos de producción avícola", "equipos_produccion"),
                      ("Herramientas de producción", "herramientas"), ("Muebles y enseres", "muebles_enseres"),
                      ("Equipos de computación", "equipos_computacion")]
    filas61 = []
    tot = [0] * 6
    for nombre, clave in categorias_ppe:
        cat = activos.CATEGORIAS[clave]
        inicial = previos.get(cat.cuenta_activo, 0) + apertura_mov.get(cat.cuenta_activo, 0)
        debe, haber = mov.get(cat.cuenta_activo, (0, 0))
        altas = debe - max(apertura_mov.get(cat.cuenta_activo, 0), 0)
        bajas = haber
        final = inicial + altas - bajas
        acumulada = -s.get(cat.cuenta_depreciacion, 0) if cat.cuenta_depreciacion else 0
        neto = final - acumulada
        for i, v in enumerate((inicial, altas, bajas, final, acumulada, neto)):
            tot[i] += v
        filas61.append([nombre, _f(inicial), _f(altas), _f(bajas), _f(final), _fp(acumulada), _f(neto)])
    filas61.append(["TOTALES", _f(tot[0]), _f(tot[1]), _f(tot[2]), _f(tot[3]), _fp(tot[4]), _f(tot[5])])
    filas62 = []
    total_dep = 0
    for nombre, clave in categorias_ppe[1:] + [categorias_ppe[0]]:
        cat = activos.CATEGORIAS[clave]
        tasa = Decimal(_param(p, activos.clave_tasa(clave))) if cat.cuenta_depreciacion else CERO
        vida = "No aplica" if tasa == 0 else f"{(Decimal(1) / tasa).quantize(Decimal('1'), ROUND_HALF_UP)} años"
        acumulada = -s.get(cat.cuenta_depreciacion, 0) if cat.cuenta_depreciacion else 0
        total_dep += acumulada
        filas62.append([nombre, vida, _pct_norma(tasa), "—" if tasa == 0 else _fp(acumulada)])
    filas62.append(["TOTAL DEPRECIACIÓN ACUMULADA", "", "", _fp(total_dep)])
    capex = _tabla_capex(con)
    transito = s.get("1.2.03.02", 0)
    bloques6 = [
        _parrafo(f"Movimiento del activo fijo y su depreciación acumulada en el ejercicio terminado el "
                 f"{fecha_pantalla(fin)}."),
        _subtitulo("6.1 Valores brutos"),
        _tabla(["Categoría", "Saldo inicial", "Altas", "Bajas", "Saldo Final (Costo Bruto)",
                "Depreciación Acumulada", "Valor Neto en Libros"], filas61, (1, 2, 3, 4, 5, 6)),
        _parrafo("Saldo Final (Costo Bruto) = Saldo Inicial + Altas – Bajas. Valor Neto en Libros = Saldo Final – "
                 "Depreciación Acumulada."),
        _subtitulo("6.2 Tasas de depreciación y depreciación acumulada"),
        _tabla(["Categoría", "Vida útil", "Tasa anual", "Dep. acumulada al cierre (Bs)"], filas62, (2, 3)),
        _parrafo(f"La cuota mensual es costo × tasa anual ÷ 12. La porción productiva se incorpora como CIF y la "
                 f"administrativa como gasto operativo (Nota 14). El valor neto en libros del activo fijo al "
                 f"{fecha_pantalla(fin)} asciende a {_f(tot[5])}. No se registraron indicios de deterioro."),
        _subtitulo("6.3 Política de inversiones (CAPEX) y plan de expansión"),
        _parrafo("Las inversiones se rigen por la Política de Inversiones y CAPEX (POL-02) y el Plan Maestro "
                 "Presupuestario 2026-2030, financiadas con recursos propios:"),
        capex,
    ]
    if transito:
        # DISCREPANCIA: las cuentas (*) de la planta de alimento (1.2.01.04/05)
        # rigen desde el ejercicio 2027 (Plan v3) y el activo se activa cuando
        # entra en uso (D.5); el desembolso del ciclo 5 de 2026 queda en
        # 1.2.03.02 hasta su activación (ver servicios/activos.py).
        bloques6.append(_parrafo(
            f"Inversiones CAPEX pagadas y pendientes de activación (registradas en 1.2.03.02 hasta que el bien entra "
            f"en uso en el ejercicio siguiente, D.5): {_f(transito)}."))
    notas.append({"numero": 6, "titulo": "ACTIVO FIJO (PROPIEDAD, PLANTA Y EQUIPO)", "bloques": bloques6})

    # NOTA 7
    filas7 = [["Certificaciones sanitarias y de origen (SENASAG / sello «Vida Digna»)", _f(0), _fp(0), _f(0)]]
    tot7 = [0, 0, 0]
    for nombre, cuenta, regul in (("Gastos de constitución y organización", "1.2.02.01", "4.1.02.09"),
                                  ("Software y plataformas digitales de gestión", "1.2.02.02", "4.1.02.10")):
        costo, amort = s.get(cuenta, 0), -s.get(regul, 0)
        tot7 = [tot7[0] + costo, tot7[1] + amort, tot7[2] + costo - amort]
        filas7.append([nombre, _f(costo), _fp(amort), _f(costo - amort)])
    filas7.append(["TOTAL INTANGIBLES", _f(tot7[0]), _fp(tot7[1]), _f(tot7[2])])
    notas.append({"numero": 7, "titulo": "ACTIVOS INTANGIBLES", "bloques": [
        _parrafo(f"Saldo al {fecha_pantalla(fin)}, amortizado linealmente a la tasa del {_pct_norma(tasa_intangible)} "
                 "anual (las certificaciones sanitarias se registran como gasto de certificación y no tienen cuenta "
                 "de intangible en el Plan v3):"),
        _tabla(["Concepto", "Costo histórico (Bs)", "Amortización acumulada (Bs)", "Valor neto (Bs)"], filas7, (1, 2, 3)),
        _parrafo("No se identificaron indicios de deterioro de los activos intangibles al cierre del ejercicio."),
    ]})

    # NOTA 8
    filas8 = [["Proveedores — pollitos BB", "2.1.01.01", _f(-s.get("2.1.01.01", 0))],
              ["Proveedores — alimento balanceado y materias primas (maíz, soya, afrecho)", "2.1.01.02 / 2.1.01.03",
               _f(-_suma(s, ("2.1.01.02", "2.1.01.03")))],
              ["Proveedores — productos veterinarios y vacunas", "2.1.01.04", _f(-s.get("2.1.01.04", 0))],
              ["Proveedores — insumos de limpieza y crianza", "2.1.01.05", _f(-s.get("2.1.01.05", 0))],
              ["Proveedores — servicios y otros", "2.1.01.06", _f(-s.get("2.1.01.06", 0))],
              ["TOTAL CUENTAS POR PAGAR COMERCIALES", "", _f(-_suma(s, [f"2.1.01.0{i}" for i in range(1, 7)]))]]
    notas.append({"numero": 8, "titulo": "CUENTAS POR PAGAR COMERCIALES", "bloques": [
        _parrafo(f"Obligaciones a corto plazo con proveedores al {fecha_pantalla(fin)}, sin intereses:"),
        _tabla(["Proveedor / concepto", "Cuenta", "Saldo (Bs)"], filas8, (2,)),
        _parrafo("No existen garantías otorgadas ni cláusulas de exclusividad asociadas a estas obligaciones."),
    ]})

    # NOTA 9
    filas9 = [["Sueldos y salarios devengados por pagar", "2.1.02.01", "Sueldo bruto devengado",
               _f(-s.get("2.1.02.01", 0))],
              ["Aguinaldo de Navidad provisionado", "2.1.02.02",
               f"{_pct_norma(_param(p, 'aguinaldo_pct'))} mensual del salario bruto", _f(-s.get("2.1.02.02", 0))],
              ["Aportes laborales retenidos por pagar", "2.1.02.05",
               f"{_pct_norma(_param(p, 'aporte_laboral'))} del Total Ganado", _f(-s.get("2.1.02.05", 0))],
              ["Aportes patronales por pagar", "2.1.02.03", f"{_pct_norma(_param(p, 'aporte_patronal'))} del salario bruto",
               _f(-s.get("2.1.02.03", 0))],
              ["Personal eventual por pagar", "2.1.02.04", "Según planilla eventual del ciclo",
               _f(-s.get("2.1.02.04", 0))],
              ["TOTAL OBLIGACIONES LABORALES A CORTO PLAZO", "", "", _f(-_suma(s, [f"2.1.02.0{i}" for i in range(1, 6)]))]]
    notas.append({"numero": 9, "titulo": "OBLIGACIONES LABORALES Y SOCIALES A CORTO PLAZO", "bloques": [
        _parrafo("Obligaciones laborales y sociales de exigibilidad corriente con el personal de granja y "
                 "administración (Ley General del Trabajo):"),
        _tabla(["Concepto", "Cuenta", "Base de cálculo", "Saldo (Bs)"], filas9, (3,)),
        _parrafo("La porción de cargas laborales del personal de granja se incorpora al costo de producción (Nota 14); "
                 "la del personal administrativo se reconoce como gasto de administración."),
    ]})

    # NOTA 10
    filas10 = [["Régimen Agropecuario Unificado (RAU) por pagar — cuota anual", "2.1.03.01", _f(-s.get("2.1.03.01", 0))],
               ["Patentes y tributos municipales por pagar", "2.1.03.02", _f(-s.get("2.1.03.02", 0))],
               ["TOTAL OBLIGACIONES TRIBUTARIAS CORRIENTES", "", _f(-_suma(s, ("2.1.03.01", "2.1.03.02")))]]
    notas.append({"numero": 10, "titulo": "OBLIGACIONES TRIBUTARIAS CORRIENTES", "bloques": [
        _parrafo(f"Obligaciones tributarias al {fecha_pantalla(fin)} como contribuyente del RAU:"),
        _tabla(["Concepto", "Cuenta", "Saldo (Bs)"], filas10, (2,)),
        _parrafo(f"El RAU sustituye al IVA, IT e IUE (Formulario 701). El gasto del ejercicio por el RAU es "
                 f"{_f(s_mov(mov, '7.1.05.02'))} y por licencias municipales {_f(s_mov(mov, '7.1.05.01'))}. No existen "
                 "contingencias tributarias conocidas (Nota 15)."),
    ]})

    # NOTA 11
    notas.append({"numero": 11, "titulo": "OBLIGACIONES LABORALES A LARGO PLAZO", "bloques": [
        _parrafo(f"La indemnización por tiempo de servicios se provisiona mensualmente al "
                 f"{_pct_norma(_param(p, 'indemnizacion_pct'))} del salario bruto desde el inicio de la relación "
                 "laboral (D.S. N.° 110 y N.° 190):"),
        _tabla(["Concepto", "Cuenta", "Base de cálculo", "Saldo (Bs)"], [
            ["Provisión para indemnización por tiempo de servicios", "2.2.01.01",
             f"{_pct_norma(_param(p, 'indemnizacion_pct'))} mensual del salario bruto", _f(-s.get("2.2.01.01", 0))]],
            (3,), totales=0),
        _parrafo("El saldo acumulado representa el 100 % de la obligación legal devengada al cierre."),
    ]})

    # NOTA 12
    capital = -s.get("3.1.01.01", 0)
    efectivo_aporte = int(con.execute(
        f"""SELECT COALESCE(SUM(d.debe_cent - d.haber_cent), 0) FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
             WHERE a.tipo = 'APERTURA' AND d.cuenta_codigo IN ({','.join('?' * len(CUENTAS_EFECTIVO))})""",
        CUENTAS_EFECTIVO).fetchone()[0])
    especie = capital - efectivo_aporte
    reserva_legal = -s.get(CUENTA_RESERVA_LEGAL, 0)
    educacion = -s.get(CUENTA_EDUCACION, 0)
    acumulados = -_suma(s, _CUENTAS_RESULTADOS_ACUM) - sum(m for c, m in s.items() if es_resultado(c))
    notas.append({"numero": 12, "titulo": "PATRIMONIO NETO", "bloques": [
        _subtitulo("12.1 Capital social"),
        # DISCREPANCIA: el modelo dice «Bs 534.293» (Diagnóstico Financiero); el
        # Acta de Constitución integra Bs 534.300 (decisión 1.4 n.º 9): se usa el
        # saldo contable de 3.1.01.01.
        _parrafo(f"El capital social asciende a {_f(capital)}, aportado por los socios fundadores conforme al Acta de "
                 "Constitución del 1 de mayo de 2026, integrado por aportes en efectivo y en especie:"),
        _tabla(["Concepto", "Cuenta", "Importe (Bs)"], [
            ["Certificados de aportación — socios fundadores (efectivo)", "3.1.01.01", _f(efectivo_aporte)],
            ["Aportes en especie (terreno e infraestructura productiva)", "3.1.01.01", _f(especie)],
            [f"CAPITAL SOCIAL AL {fecha_pantalla(fin)}", "", _f(capital)]], (2,)),
        _subtitulo("12.2 Reservas obligatorias"),
        _parrafo("Del excedente neto corresponde destinar, con carácter obligatorio y previo a cualquier distribución, "
                 "los siguientes porcentajes mínimos (Ley N.° 356; POL-01 Art. 1):"),
        _tabla(["Reserva", "Cuenta", "Porcentaje mínimo", "Saldo (Bs)"], [
            ["Fondo de Reserva Legal (irrepartible)", "3.1.02.01",
             f"{_pct_norma(_param(p, 'reserva_legal_min'))} del excedente neto", _f(reserva_legal)],
            ["Fondo de Educación Cooperativa", "3.1.02.02",
             f"{_pct_norma(_param(p, 'fondo_educacion_min'))} del excedente neto", _f(educacion)],
            ["TOTAL RESERVAS OBLIGATORIAS", "", "", _f(reserva_legal + educacion)]], (3,)),
        _parrafo("El remanente, deducidas las reservas obligatorias y la Reserva de Reinversión y Expansión que apruebe "
                 "la Asamblea, queda disponible para su distribución entre los socios o su capitalización."),
        _subtitulo("12.3 Resultados acumulados"),
        _parrafo(f"Al {fecha_pantalla(fin)}, el excedente pendiente de resolución por la Asamblea General se mantiene "
                 f"en Excedentes de Percepción Acumulados (3.1.03) por {_f(acumulados)}. El excedente neto del "
                 f"ejercicio, {_f(er['excedente_neto'])}, se expone en el Estado de Resultados y en el EEPN."),
    ]})

    # NOTA 13
    total13 = er["carne"] + er["alimento"] + er["gallinaza"]

    def pct13(valor: int) -> str:
        return _pct_texto(_fraccion(valor, total13), 1) if total13 else "—"

    notas.append({"numero": 13, "titulo": "INGRESOS OPERATIVOS", "bloques": [
        _parrafo("Los ingresos se reconocen con la entrega física del producto (Nota 2.12). Los ingresos del ejercicio "
                 f"terminado el {fecha_pantalla(fin)} se desagregan así:"),
        _tabla(["Línea de negocio", "Cuenta", "Importe (Bs)", "% del total"], [
            ["Venta de carne pollo", "5.1.01.01", _f(er["carne"]), pct13(er["carne"])],
            ["Venta de alimento balanceado excedente", "5.1.02.01", _f(er["alimento"]), pct13(er["alimento"])],
            ["Venta de Gallinaza- abono organico", "5.2.01.04", _f(er["gallinaza"]), pct13(er["gallinaza"])],
            ["TOTAL INGRESOS OPERATIVOS", "", _f(total13), "100,0 %" if total13 else "—"]], (2, 3)),
        _parrafo(f"Las ventas se realizan al contado, con la única excepción del sector HORECA, con crédito máximo de "
                 f"{_param(p, 'horeca_credito_dias_max')} día(s) calendario desde la entrega (POL-05), vigente en el "
                 f"ejercicio. Devoluciones en ventas del ejercicio: {_f(er['devoluciones'])}."),
    ]})

    # NOTA 14
    suf = _costo_por_sufijo(con, inicio, fin)
    alimento = sum(suf.get(k, 0) for k in ("02", "03", "04", "05", "06", "07", "11"))
    mod = sum(suf.get(k, 0) for k in ("20", "21", "22", "38", "39"))
    sanidad = suf.get("08", 0)
    pollitos = suf.get("01", 0)
    depreciacion = sum(suf.get(k, 0) for k in ("30", "31", "32", "33", "34", "35"))
    otros_cif = sum(suf.values()) - alimento - mod - sanidad - pollitos - depreciacion
    produccion = sum(suf.values())
    variacion = er["costo_ventas"] - produccion
    gastos = saldos(con, fin, inicio, TIPOS_FUERA_DE_RESULTADOS)
    admin = _suma(gastos, [f"7.1.01.0{i}" for i in range(1, 5)] + [f"7.1.02.0{i}" for i in range(1, 9)])
    comercial = gastos.get("7.1.03.01", 0)
    certificacion = _suma(gastos, ("7.1.04.01", "7.1.04.02"))
    tributos = _suma(gastos, ("7.1.05.01", "7.1.05.02"))
    filas14 = [
        ["Costo de producción — alimento balanceado consumido en crianza", _f(alimento)],
        ["Costo de producción — mano de obra directa de granja", _f(mod)],
        ["Costo de producción — sanidad animal e insumos veterinarios", _f(sanidad)],
        ["Costo de pollitos BB incorporados a producción", _f(pollitos)],
        ["Depreciación de galpones y equipos productivos (Nota 6)", _f(depreciacion)],
        # El modelo no detalla limpieza, crianza, embalaje, mantenimiento,
        # energía ni mortalidad normal; se agrupan para que el total cuadre.
        ["Otros costos de producción (limpieza, crianza, embalaje, mantenimiento, energía y mortalidad normal)",
         _f(otros_cif)],
    ]
    if variacion:
        filas14.append(["Variación de inventarios de productos terminados y producción en proceso", _f(variacion)])
    filas14 += [
        ["Total costo de producción / costo de ventas", _f(er["costo_ventas"])],
        ["Gastos de administración (sueldos administrativos, servicios, depreciación de bienes de administración)",
         _f(admin)],
        ["Gastos de comercialización (transporte, empaque, promoción)", _f(comercial)],
        ["Gastos de certificación y calidad (SENASAG, auditorías)", _f(certificacion)],
        ["Impuesto por el Régimen Agropecuario Unificado y patentes municipales (Nota 10)", _f(tributos)],
        ["TOTAL COSTOS Y GASTOS OPERATIVOS", _f(er["costo_ventas"] + admin + comercial + certificacion + tributos)],
    ]
    # DISCREPANCIA: el MAN-006 titula la Nota 14 «OBLIGACIONES LABORALES A LARGO
    # PLAZO» (repite la Nota 11), pero su contenido son los costos de
    # producción y gastos operativos: se corrige el título.
    notas.append({"numero": 14, "titulo": "COSTOS DE PRODUCCIÓN Y GASTOS OPERATIVOS", "bloques": [
        _parrafo("Los costos se determinan por absorción, con la estructura matricial de centros de costo y sufijos "
                 "del Grupo 6:"),
        _tabla(["Concepto", "Importe (Bs)"], filas14, (1,)),
        _parrafo(f"Excedente bruto del ejercicio: {_f(er['utilidad_bruta'])}; excedente neto: "
                 f"{_f(er['excedente_neto'])}. Por el RAU, el excedente no está sujeto al IUE."),
    ]})

    # NOTA 15
    notas.append({"numero": 15, "titulo": "CONTINGENCIAS Y COMPROMISOS", "bloques": [
        _subtitulo("15.1 Contingencias"),
        _parrafo("La Administración no tiene conocimiento de litigios, reclamos ni procesos que pudieran derivar en "
                 "pasivos contingentes significativos; no se otorgaron avales ni garantías a terceros."),
        _subtitulo("15.2 Compromisos"),
        _parrafo("Existen compromisos de compra recurrente de insumos sin cláusulas de exclusividad. Conforme a la "
                 "POL-02, las inversiones se financian con recursos propios; no existen hipotecas ni prendas sobre el "
                 "activo fijo. " + _compromisos_texto(con, fin)),
    ]})

    # NOTA 16
    texto16 = (hechos_posteriores.strip() if isinstance(hechos_posteriores, str) and hechos_posteriores.strip()
               else "La Administración no ha identificado hechos posteriores que requieran revelación.")
    notas.append({"numero": 16, "titulo": "HECHOS POSTERIORES AL CIERRE", "bloques": [
        _parrafo(f"Entre el {fecha_pantalla(fin)} y el {aprobacion_ca}, fecha de aprobación por el Consejo de "
                 f"Administración: {texto16}"),
        _parrafo("Los hechos que se describan son no ajustables y se revelan a título informativo."),
    ]})
    return notas


def s_mov(mov: Mapping[str, tuple[int, int]], cuenta: str) -> int:
    debe, haber = mov.get(cuenta, (0, 0))
    return debe - haber


def _aves_en_proceso(con: sqlite3.Connection, fecha: date) -> tuple[int, int]:
    """Aves iniciales y bajas normales de los ciclos aún sin cierre de costos."""
    fila = con.execute(
        """SELECT COALESCE(SUM(c.aves_iniciales), 0),
                  COALESCE((SELECT SUM(m.aves) FROM bajas_mortalidad m JOIN ciclos_produccion x ON x.id = m.ciclo_id
                             WHERE NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = x.id)
                               AND m.fecha <= ?), 0)
             FROM ciclos_produccion c
            WHERE c.fecha_inicio <= ? AND NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = c.id)""",
        (fecha.isoformat(), fecha.isoformat())).fetchone()
    return int(fila[0]), int(fila[1])


def _tabla_capex(con: sqlite3.Connection) -> dict[str, Any]:
    ejercicios = [int(f[0]) for f in con.execute("SELECT DISTINCT ejercicio FROM capex_referencia ORDER BY ejercicio")]
    conceptos: dict[str, dict[int, int]] = {}
    nombres = {"maquinaria_alimento": "Maquinaria para alimento balanceado",
               "ambiente_preparacion": "Construcción de ambiente de preparación",
               "galpones": "Construcción de galpón adicional",
               "equipos_produccion": "Equipo adicional (por cada 1.000 aves)",
               "herramientas": "Herramientas adicionales (por cada 3.000 aves)"}
    for f in con.execute("SELECT ejercicio, categoria, SUM(monto_cent) FROM capex_referencia GROUP BY ejercicio, categoria"):
        conceptos.setdefault(f[1], {})[int(f[0])] = int(f[2])
    filas = []
    totales = {e: 0 for e in ejercicios}
    for clave, nombre in nombres.items():
        if clave not in conceptos:
            continue
        fila = [nombre]
        for e in ejercicios:
            v = conceptos[clave].get(e, 0)
            totales[e] += v
            fila.append(_f(v) if v else "—")
        filas.append(fila)
    filas.append(["TOTAL REINVERSIONES (Bs)"] + [_f(totales[e]) for e in ejercicios])
    return _tabla(["Concepto"] + [str(e) for e in ejercicios], filas, tuple(range(1, len(ejercicios) + 1)))


def _compromisos_texto(con: sqlite3.Connection, fecha: date) -> str:
    fila = con.execute("SELECT COUNT(*), COALESCE(SUM(monto_cent), 0) FROM compromisos "
                       "WHERE estado IN ('VIGENTE','AUTORIZADO','SUSPENDIDO') AND fecha <= ?",
                       (fecha.isoformat(),)).fetchone()
    if int(fila[0]) == 0:
        return "Al cierre no existen compromisos de gasto no recurrente pendientes de pago."
    return (f"Al cierre existen {int(fila[0])} compromiso(s) de gasto no recurrente pendiente(s) de pago por "
            f"{_f(int(fila[1]))} (FIN-TES-04).")


# ==========================================================================
# 9. CIERRE ANUAL (30 de junio) Y CIERRE DEL EJERCICIO
# ==========================================================================
def preparar_cierre_anual(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """Vista previa del cierre anual: requisitos, líneas del asiento de cierre
    a 8.1.01.01 y traslado del resultado a 3.1.03.02 / 3.1.03.03."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    fila = _ejercicio_fila(con, ejercicio)
    inicio, fin = rango_ejercicio(con, ejercicio)
    periodos = con.execute("SELECT * FROM periodos WHERE ejercicio = ? ORDER BY numero", (ejercicio,)).fetchall()
    abiertos_previos = [nombre_periodo(p) for p in periodos if int(p["numero"]) < 12 and p["estado"] != "CERRADO"]
    junio = next((p for p in periodos if int(p["numero"]) == 12), None)
    lista_junio = _verificar_mes(con, junio) if junio is not None else None
    registrado = con.execute("SELECT * FROM cierres_anuales WHERE ejercicio = ?", (ejercicio,)).fetchone()
    s = saldos(con, fin)
    lineas, resultado = _lineas_cierre(s)
    requisitos = [
        {"requisito": "Períodos de julio a mayo cerrados (PR-FIN-10)", "cumple": not abiertos_previos,
         "detalle": ", ".join(abiertos_previos)},
        {"requisito": "FIN-INF-01 de junio con pendientes en cero",
         "cumple": lista_junio is not None and lista_junio["pendientes"] == 0,
         "detalle": "" if lista_junio is None else f"{lista_junio['pendientes']} pendiente(s)"},
        {"requisito": "Junio abierto (el cierre anual se registra antes de cerrar junio)",
         "cumple": junio is not None and junio["estado"] == "ABIERTO", "detalle": ""},
        {"requisito": "Cierre anual no registrado", "cumple": registrado is None, "detalle": ""},
        {"requisito": "Ejercicio abierto", "cumple": fila["estado"] == "ABIERTO", "detalle": ""},
    ]
    total = sum(l["debe"] for l in lineas)
    return {"ejercicio": ejercicio, "desde": inicio.isoformat(), "fecha": fin.isoformat(), "lineas": lineas,
            "total": total, "resultado": _bs(resultado), "es_excedente": resultado >= 0,
            "requisitos": requisitos, "listo": all(r["cumple"] for r in requisitos),
            "registrado": dict(registrado) if registrado else None}


def _lineas_cierre(s: Mapping[str, int]) -> tuple[list[dict[str, Any]], int]:
    lineas: list[dict[str, Any]] = []
    for cuenta in sorted(c for c, m in s.items() if es_resultado(c) and c != CUENTA_RESUMEN and m != 0):
        m = s[cuenta]
        lineas.append({"cuenta": cuenta, "debe": _bs(-m) if m < 0 else CERO, "haber": _bs(m) if m > 0 else CERO})
    resultado = -sum(m for c, m in s.items() if es_resultado(c) and c != CUENTA_RESUMEN)
    if resultado > 0:
        lineas.append({"cuenta": CUENTA_RESUMEN, "debe": CERO, "haber": _bs(resultado)})
    elif resultado < 0:
        lineas.append({"cuenta": CUENTA_RESUMEN, "debe": _bs(-resultado), "haber": CERO})
    return lineas, resultado


def ejecutar_cierre_anual(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, autorizacion_id: int) -> dict[str, Any]:
    """Cierre anual al 30 de junio (PR-FIN-11; MOF §3.1).

    1. Asiento CIERRE: todas las cuentas de resultado (Grupos 5, 6.1.02, 7 y
       8) contra 8.1.01.01 Resumen de Déficit y Excedentes.
    2. Asiento CIERRE de traslado: 8.1.01.01 a 3.1.03.02 Excedente del
       Ejercicio (o 3.1.03.03 Déficit).

    Lo registra la Unidad de Finanzas con la autorización AJUSTE_CONTABLE de la
    Gerencia General por al menos el total del asiento de cierre.

    Raises:
        ReglaNegocioError: requisitos incumplidos (``CIERRE_ANUAL_NO_LISTO``) o
            ``SIN_MOVIMIENTOS_RESULTADO``.
        AutorizacionError: autorización incompleta o insuficiente.
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "CREAR")
    _entero(autorizacion_id, "autorización")
    with transaccion(con):
        previa = preparar_cierre_anual(con, sesion, ejercicio)
        faltan = [f"{r['requisito']}{' (' + r['detalle'] + ')' if r['detalle'] else ''}"
                  for r in previa["requisitos"] if not r["cumple"]]
        if faltan:
            raise ReglaNegocioError("No se puede ejecutar el cierre anual: " + "; ".join(faltan) + ".",
                                    codigo="CIERRE_ANUAL_NO_LISTO", detalle={"faltan": faltan})
        if not previa["lineas"]:
            raise ReglaNegocioError("No hay cuentas de resultado con saldo que cerrar.", codigo="SIN_MOVIMIENTOS_RESULTADO")
        total = previa["total"]
        try:
            seguridad.verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.AJUSTE_CONTABLE,
                                             monto=total)
        except ReglaNegocioError as exc:
            if exc.codigo == "MONTO_EXCEDE_AUTORIZADO":
                raise AutorizacionError(f"La autorización de la GG debe cubrir el total del asiento de cierre "
                                        f"({formato_bs(total)}).", codigo="AUTORIZACION_INSUFICIENTE") from exc
            raise
        fin = date.fromisoformat(previa["fecha"])
        ref = f"CIERRE-{ejercicio}"
        asiento = contable.crear_asiento_cierre(
            con, sesion, fin, f"Cierre anual del ejercicio {ejercicio}: cuentas de resultado a 8.1.01.01",
            previa["lineas"], ref, autorizacion_id=autorizacion_id)
        resultado = a_centavos(previa["resultado"])
        traslado = None
        if resultado != 0:
            monto = _bs(abs(resultado))
            lineas = ([{"cuenta": CUENTA_RESUMEN, "debe": monto}, {"cuenta": CUENTA_EXCEDENTE, "haber": monto}]
                      if resultado > 0 else
                      [{"cuenta": CUENTA_DEFICIT, "debe": monto}, {"cuenta": CUENTA_RESUMEN, "haber": monto}])
            traslado = contable.crear_asiento_cierre(
                con, sesion, fin, f"Traslado del {'excedente' if resultado > 0 else 'déficit'} del ejercicio "
                f"{ejercicio} a {CUENTA_EXCEDENTE if resultado > 0 else CUENTA_DEFICIT}", lineas, ref,
                autorizacion_id=autorizacion_id)
        cur = con.execute(
            """INSERT INTO cierres_anuales (ejercicio, fecha, resultado_cent, asiento_cierre_id, asiento_traslado_id,
                                            autorizacion_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (ejercicio, fin.isoformat(), resultado, asiento.id, traslado.id if traslado else None, autorizacion_id,
             sesion.nombre_usuario, _ahora_iso()))
        cid = int(cur.lastrowid)
        seguridad.consumir_autorizacion(con, sesion, autorizacion_id, "cierres_anuales", cid,
                                        tipo_decision=TipoDecision.AJUSTE_CONTABLE, monto=total)
        _crear_presentacion(con, ejercicio, fin)
        auditoria.registrar(con, sesion, "cierres_anuales", "INSERT", cid,
                            despues={"ejercicio": ejercicio, "resultado": _bs(resultado),
                                     "asiento_cierre": asiento.comprobante,
                                     "asiento_traslado": traslado.comprobante if traslado else None},
                            detalle=f"Cierre anual del ejercicio {ejercicio}")
    return {"id": cid, "resultado": _bs(resultado), "asiento_cierre": asiento.comprobante,
            "asiento_traslado": traslado.comprobante if traslado else None}


def _crear_presentacion(con: sqlite3.Connection, ejercicio: int, fin: date) -> None:
    p = _params(con, ejercicio)
    limite_c = fin + timedelta(days=int(_param(p, "eeff_consejos_dias")))
    limite_a = fin + timedelta(days=int(_param(p, "eeff_asamblea_dias")))
    if con.execute("SELECT 1 FROM presentaciones_eeff WHERE ejercicio = ?", (ejercicio,)).fetchone() is None:
        con.execute("""INSERT INTO presentaciones_eeff (ejercicio, fecha_cierre, limite_consejos, limite_asamblea,
                                                        registrado_en) VALUES (?,?,?,?,?)""",
                    (ejercicio, fin.isoformat(), limite_c.isoformat(), limite_a.isoformat(), _ahora_iso()))


def cerrar_ejercicio(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> None:
    """Cierra el ejercicio (no se reabre). Exige los 12 períodos cerrados y el
    cierre anual registrado. Lo ejecuta la Gerencia General."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "AUTORIZAR")
    with transaccion(con):
        fila = _ejercicio_fila(con, ejercicio)
        if fila["estado"] == "CERRADO":
            raise ReglaNegocioError(f"El ejercicio {ejercicio} ya está cerrado.", codigo="EJERCICIO_CERRADO")
        cierre = con.execute("SELECT * FROM cierres_anuales WHERE ejercicio = ?", (ejercicio,)).fetchone()
        if cierre is None:
            raise ReglaNegocioError("Registre primero el cierre anual.", codigo="CIERRE_ANUAL_PENDIENTE")
        abiertos = con.execute("SELECT COUNT(*) FROM periodos WHERE ejercicio = ? AND estado <> 'CERRADO'",
                               (ejercicio,)).fetchone()[0]
        if int(abiertos):
            raise ReglaNegocioError(f"Quedan {abiertos} período(s) abiertos en el ejercicio {ejercicio}.",
                                    codigo="PERIODOS_ABIERTOS")
        momento = _ahora_iso()
        con.execute("UPDATE ejercicios SET estado = 'CERRADO', cerrado_en = ?, cerrado_por = ? WHERE ejercicio = ?",
                    (momento, sesion.nombre_usuario, ejercicio))
        con.execute("UPDATE cierres_anuales SET ejercicio_cerrado_por = ?, ejercicio_cerrado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, momento, cierre["id"]))
        auditoria.registrar(con, sesion, "ejercicios", "CIERRE", ejercicio, antes={"estado": "ABIERTO"},
                            despues={"estado": "CERRADO", "cerrado_en": momento},
                            detalle=f"Cierre del ejercicio {ejercicio}")


# ==========================================================================
# 10. PLAZOS DE 60 Y 90 DÍAS (PR-FIN-11; Ley 356 Art. 55)
# ==========================================================================
def _estado_plazo(limite: date, cumplido: str | None, referencia: date, alerta: int) -> tuple[str, int]:
    if cumplido:
        return ("CUMPLIDO" if a_fecha(cumplido) <= limite else "CUMPLIDO_FUERA_DE_PLAZO"), 0
    dias = (limite - referencia).days
    if dias < 0:
        return "VENCIDO", dias
    return ("POR_VENCER" if dias <= alerta else "EN_PLAZO"), dias


def plazos_eeff(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Plazos de presentación a los Consejos (60 días) y de aprobación de la
    Asamblea (90 días) de cada ejercicio terminado o con cierre anual
    registrado, con su estado de alerta."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    resultado = []
    for e in con.execute("SELECT * FROM ejercicios WHERE fecha_fin < ? OR ejercicio IN "
                         "(SELECT ejercicio FROM presentaciones_eeff) ORDER BY ejercicio", (referencia.isoformat(),)):
        p = _params(con, int(e["ejercicio"]))
        fin = date.fromisoformat(e["fecha_fin"])
        alerta = int(_param(p, "eeff_alerta_dias"))
        pres = con.execute("SELECT * FROM presentaciones_eeff WHERE ejercicio = ?", (e["ejercicio"],)).fetchone()
        limite_c = date.fromisoformat(pres["limite_consejos"]) if pres else \
            fin + timedelta(days=int(_param(p, "eeff_consejos_dias")))
        limite_a = date.fromisoformat(pres["limite_asamblea"]) if pres else \
            fin + timedelta(days=int(_param(p, "eeff_asamblea_dias")))
        est_c, dias_c = _estado_plazo(limite_c, pres["fecha_consejos"] if pres else None, referencia, alerta)
        est_a, dias_a = _estado_plazo(limite_a, pres["fecha_asamblea"] if pres else None, referencia, alerta)
        resultado.append({
            "ejercicio": int(e["ejercicio"]), "fecha_cierre": fin.isoformat(), "cierre_anual": pres is not None,
            "limite_consejos": limite_c.isoformat(), "estado_consejos": est_c, "dias_consejos": dias_c,
            "fecha_consejos": pres["fecha_consejos"] if pres else None,
            "limite_asamblea": limite_a.isoformat(), "estado_asamblea": est_a, "dias_asamblea": dias_a,
            "fecha_asamblea": pres["fecha_asamblea"] if pres else None,
            "acta_ca_ref": pres["acta_ca_ref"] if pres else None,
            "acta_asamblea_ref": pres["acta_asamblea_ref"] if pres else None,
            "inf_cv_08_ref": pres["inf_cv_08_ref"] if pres else None,
        })
    return resultado


def alertas_plazos(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Alertas de los plazos de 60 y 90 días vencidos o por vencer."""
    alertas = []
    for p in plazos_eeff(con, sesion, fecha):
        for clave, nombre in (("consejos", "Presentación de EEFF a los Consejos (PR-FIN-11)"),
                              ("asamblea", "Aprobación de EEFF por la Asamblea (Ley 356 Art. 55)")):
            estado = p[f"estado_{clave}"]
            if estado in ("VENCIDO", "POR_VENCER"):
                dias = p[f"dias_{clave}"]
                texto = (f"vencido hace {-dias} día(s)" if estado == "VENCIDO" else f"vence en {dias} día(s)")
                alertas.append({"ejercicio": p["ejercicio"], "plazo": clave, "estado": estado,
                                "limite": p[f"limite_{clave}"], "dias": dias,
                                "mensaje": f"Ejercicio {p['ejercicio']}: {nombre} — límite "
                                           f"{fecha_pantalla(p[f'limite_{clave}'])}, {texto}."})
    return alertas


def _huella(contenido: str) -> str:
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def emitir_estados(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, motivo: str,
                   fecha_aprobacion_ca: Any = None, hechos_posteriores: str | None = None) -> dict[str, Any]:
    """Congela el paquete de estados financieros (JSON con huella SHA-256)."""
    paquete = estados_financieros(con, sesion, ejercicio, fecha_aprobacion_ca, hechos_posteriores)
    if not (paquete["balance"]["cuadra"] and paquete["resultados"]["cuadra"] and paquete["flujo"]["cuadra"]
            and paquete["patrimonio"]["cuadra"]):
        raise ReglaNegocioError("Los estados no concilian (balance, resultados, flujo o patrimonio): revise antes de "
                                "emitir.", codigo="ESTADOS_NO_CONCILIAN")
    contenido = auditoria.a_json(paquete)
    huella = _huella(contenido)
    # Fase 13: emisión y bitácora en una transacción explícita.
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO estados_emitidos (ejercicio, fecha_corte, motivo, contenido_json, huella, emitido_por,
                   emitido_en) VALUES (?,?,?,?,?,?,?)""",
            (ejercicio, paquete["fecha_corte"], motivo, contenido, huella, sesion.nombre_usuario, _ahora_iso()))
        eid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "estados_emitidos", "INSERT", eid,
                            despues={"ejercicio": ejercicio, "motivo": motivo, "huella": huella})
    return {"id": eid, "huella": huella, "paquete": paquete}


def registrar_presentacion_consejos(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any,
                                    acta_ca_ref: str, hechos_posteriores: str | None = None) -> dict[str, Any]:
    """La Gerencia General presenta los EEFF al CA y al CV (PR-FIN-11, 60 días).
    Emite y congela el paquete con las Notas 1-16."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "AUTORIZAR")
    f = a_fecha(fecha)
    acta = _texto(acta_ca_ref, "el acta del Consejo de Administración", 120)
    with transaccion(con):
        fila = _ejercicio_fila(con, ejercicio)
        if fila["estado"] != "CERRADO":
            raise ReglaNegocioError("Cierre primero el ejercicio (cierre anual y junio).", codigo="EJERCICIO_ABIERTO")
        pres = con.execute("SELECT * FROM presentaciones_eeff WHERE ejercicio = ?", (ejercicio,)).fetchone()
        if pres["fecha_consejos"]:
            raise ReglaNegocioError("Los estados ya fueron presentados a los Consejos.", codigo="YA_PRESENTADO")
        if f <= date.fromisoformat(fila["fecha_fin"]):
            raise DatosInvalidosError("La presentación debe ser posterior al cierre del 30 de junio.")
        emitido = emitir_estados(con, sesion, ejercicio, "PRESENTACION_CONSEJOS", f, hechos_posteriores)
        dentro = f <= date.fromisoformat(pres["limite_consejos"])
        con.execute("""UPDATE presentaciones_eeff SET fecha_consejos = ?, acta_ca_ref = ?, estado_emitido_id = ?,
                                presentado_por = ?, dentro_plazo_consejos = ? WHERE id = ?""",
                    (f.isoformat(), acta, emitido["id"], sesion.nombre_usuario, int(dentro), pres["id"]))
        auditoria.registrar(con, sesion, "presentaciones_eeff", "UPDATE", int(pres["id"]),
                            antes={"fecha_consejos": None},
                            despues={"fecha_consejos": f.isoformat(), "acta": acta, "dentro_plazo": dentro,
                                     "huella": emitido["huella"]})
    return {"dentro_plazo": dentro, "limite": pres["limite_consejos"], "huella": emitido["huella"],
            "estado_emitido_id": emitido["id"]}


def registrar_aprobacion_asamblea(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any,
                                  acta_ref: str, inf_cv_08_ref: str) -> dict[str, Any]:
    """La Asamblea General aprueba los EEFF (Ley 356 Art. 55, 90 días), con el
    Informe de Verificación del Consejo de Vigilancia INF-CV-08."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "APROBAR")
    f = a_fecha(fecha)
    acta = _texto(acta_ref, "el acta de la Asamblea", 120)
    informe = _texto(inf_cv_08_ref, "la referencia del INF-CV-08", 120)
    with transaccion(con):
        pres = con.execute("SELECT * FROM presentaciones_eeff WHERE ejercicio = ?", (ejercicio,)).fetchone()
        if pres is None or not pres["fecha_consejos"]:
            raise ReglaNegocioError("Los estados deben presentarse antes a los Consejos (PR-FIN-11).",
                                    codigo="SIN_PRESENTACION")
        if pres["fecha_asamblea"]:
            raise ReglaNegocioError("La Asamblea ya aprobó los estados de este ejercicio.", codigo="YA_APROBADO")
        if f < date.fromisoformat(pres["fecha_consejos"]):
            raise DatosInvalidosError("La aprobación no puede ser anterior a la presentación a los Consejos.")
        dentro = f <= date.fromisoformat(pres["limite_asamblea"])
        con.execute("""UPDATE presentaciones_eeff SET fecha_asamblea = ?, acta_asamblea_ref = ?, inf_cv_08_ref = ?,
                                aprobado_por = ?, dentro_plazo_asamblea = ? WHERE id = ?""",
                    (f.isoformat(), acta, informe, sesion.nombre_usuario, int(dentro), pres["id"]))
        auditoria.registrar(con, sesion, "presentaciones_eeff", "UPDATE", int(pres["id"]),
                            antes={"fecha_asamblea": None},
                            despues={"fecha_asamblea": f.isoformat(), "acta": acta, "inf_cv_08": informe,
                                     "dentro_plazo": dentro})
    return {"dentro_plazo": dentro, "limite": pres["limite_asamblea"]}


def estados_emitidos(con: sqlite3.Connection, sesion: Sesion, ejercicio: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    sql = "SELECT id, ejercicio, fecha_corte, motivo, huella, emitido_por, emitido_en FROM estados_emitidos"
    args: tuple[Any, ...] = ()
    if ejercicio is not None:
        sql += " WHERE ejercicio = ?"
        args = (ejercicio,)
    return [dict(f) for f in con.execute(sql + " ORDER BY id", args)]


def obtener_estado_emitido(con: sqlite3.Connection, sesion: Sesion, estado_id: int) -> dict[str, Any]:
    """Paquete congelado; verifica que la huella coincida con el contenido."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    fila = con.execute("SELECT * FROM estados_emitidos WHERE id = ?", (estado_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el paquete emitido {estado_id}.")
    return {**dict(fila), "integro": _huella(fila["contenido_json"]) == fila["huella"],
            "paquete": json.loads(fila["contenido_json"])}


# ==========================================================================
# 11. DISTRIBUCIÓN DE EXCEDENTES (POL-01; CI-FIN-02) Y LIQUIDACIONES
# ==========================================================================
@dataclass(frozen=True)
class PropuestaDistribucion:
    """Cálculo del excedente distribuible (CI-FIN-02 Art. 5)."""

    ejercicio: int
    excedente: Decimal
    pct_reserva_legal: Decimal
    reserva_legal: Decimal
    pct_educacion: Decimal
    educacion: Decimal
    reinversion: Decimal
    capitalizacion: Decimal
    distribuible: Decimal
    criterio: str
    reparto: tuple[tuple[int, str, Decimal, Decimal], ...]

    def a_dict(self) -> dict[str, Any]:
        return {"ejercicio": self.ejercicio, "excedente": self.excedente,
                "pct_reserva_legal": self.pct_reserva_legal, "reserva_legal": self.reserva_legal,
                "pct_educacion": self.pct_educacion, "educacion": self.educacion, "reinversion": self.reinversion,
                "pct_reinversion": _fraccion(a_centavos(self.reinversion), a_centavos(self.excedente)),
                "capitalizacion": self.capitalizacion, "distribuible": self.distribuible, "criterio": self.criterio,
                "reparto": [{"socio_id": s, "socio": n, "base": b, "monto": m} for s, n, b, m in self.reparto]}


def _decimal(valor: Any, campo: str) -> Decimal:
    try:
        d = Decimal(str(valor).replace(",", ".")) if not isinstance(valor, Decimal) else valor
    except Exception as exc:  # noqa: BLE001 - se informa el dato inválido
        raise DatosInvalidosError(f"El campo {campo} no es un número válido: {valor!r}.") from exc
    if not d.is_finite():
        raise DatosInvalidosError(f"El campo {campo} no es un número válido.")
    return d


def excedente_del_ejercicio(con: sqlite3.Connection, ejercicio: int) -> Decimal:
    """Excedente neto trasladado a 3.1.03.02 por el cierre anual."""
    fila = con.execute("SELECT resultado_cent FROM cierres_anuales WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if fila is None:
        raise ReglaNegocioError(f"El ejercicio {ejercicio} no tiene cierre anual: el excedente se determina con el "
                                "Estado de Resultados del cierre (CI-FIN-02 Art. 5).", codigo="CIERRE_ANUAL_PENDIENTE")
    return _bs(int(fila[0]))


def _repartir(total_cent: int, bases: Sequence[tuple[int, str, Decimal]]) -> list[tuple[int, str, Decimal, Decimal]]:
    """Reparto proporcional exacto al centavo (método del mayor residuo)."""
    suma = sum((b for _, _, b in bases), Decimal(0))
    if suma <= 0:
        raise DatosInvalidosError("La base de reparto debe ser mayor a cero.")
    brutos = [(sid, nombre, base, Decimal(total_cent) * base / suma) for sid, nombre, base in bases]
    enteros = [(sid, nombre, base, int(v), v - int(v)) for sid, nombre, base, v in brutos]
    resto = total_cent - sum(e[3] for e in enteros)
    orden = sorted(range(len(enteros)), key=lambda i: (-enteros[i][4], i))
    asignado = [e[3] for e in enteros]
    for i in orden[:resto]:
        asignado[i] += 1
    return [(e[0], e[1], e[2], _bs(asignado[i])) for i, e in enumerate(enteros)]


def calcular_distribucion(
    con: sqlite3.Connection, ejercicio: int, *, pct_reserva_legal: Any = None, pct_educacion: Any = None,
    reinversion: Any = 0, capitalizacion: Any = 0, criterio: str | None = None,
    pesos: Mapping[int, Any] | None = None,
) -> PropuestaDistribucion:
    """Excedente distribuible = excedente neto − Reserva Legal (>= mínimo) −
    Fondo de Educación (>= mínimo) − Reinversión (Asamblea) − capitalización.

    Args:
        pct_reserva_legal / pct_educacion: fracciones (0,05 = 5 %); por defecto
            los mínimos vigentes (``reserva_legal_min``, ``fondo_educacion_min``).
        reinversion: monto de la Reserva de Reinversión y Expansión (Bs).
        capitalizacion: parte del distribuible a 3.1.01.05 (CI-FIN-02 Art. 11).
        criterio: IGUALITARIO (CI-FIN-02 Art. 7) o PARTICIPACION con ``pesos``.

    Raises:
        ReglaNegocioError: reservas bajo el mínimo (``RESERVA_BAJO_MINIMO``),
            deducciones que superan el excedente o ejercicio sin excedente.
    """
    _entero(ejercicio, "ejercicio")
    excedente = excedente_del_ejercicio(con, ejercicio)
    if excedente <= 0:
        raise ReglaNegocioError(f"El ejercicio {ejercicio} cerró con déficit ({formato_bs(excedente)}): no hay "
                                "excedente que distribuir.", codigo="SIN_EXCEDENTE")
    p = _params(con, ejercicio)
    minimo_legal = Decimal(_param(p, "reserva_legal_min"))
    minimo_educ = Decimal(_param(p, "fondo_educacion_min"))
    pct_legal = minimo_legal if pct_reserva_legal is None else _decimal(pct_reserva_legal, "porcentaje de Reserva Legal")
    pct_educ = minimo_educ if pct_educacion is None else _decimal(pct_educacion, "porcentaje del Fondo de Educación")
    for nombre, valor, minimo, norma in (("Reserva Legal", pct_legal, minimo_legal, "Ley 356; POL-01 Art. 1"),
                                         ("Fondo de Educación Cooperativa", pct_educ, minimo_educ, "POL-01 Art. 1")):
        if valor > 1:
            raise DatosInvalidosError(f"El porcentaje de {nombre} se indica como fracción (0,05 = 5 %).")
        if valor < minimo:
            raise ReglaNegocioError(f"{nombre}: {_pct_texto(valor)} es menor al mínimo de {_pct_norma(minimo)} "
                                    f"({norma}).", codigo="RESERVA_BAJO_MINIMO")
    reinv = redondear_bs(_decimal(reinversion, "reinversión"))
    capit = redondear_bs(_decimal(capitalizacion, "capitalización"))
    if reinv < 0 or capit < 0:
        raise DatosInvalidosError("La reinversión y la capitalización no pueden ser negativas.")
    legal = redondear_bs(excedente * pct_legal)
    educ = redondear_bs(excedente * pct_educ)
    distribuible = excedente - legal - educ - reinv - capit
    if distribuible < 0:
        raise ReglaNegocioError(f"Las reservas, la reinversión y la capitalización ({formato_bs(legal + educ + reinv + capit)}) "
                                f"superan el excedente neto ({formato_bs(excedente)}).", codigo="DEDUCCIONES_EXCEDEN")
    crit = criterio or str(_param(p, "excedentes_criterio_distribucion"))
    if crit not in CRITERIOS:
        raise DatosInvalidosError(f"Criterio de distribución no válido: {crit!r}. Use IGUALITARIO o PARTICIPACION.")
    socios = con.execute("SELECT id, nombre_completo FROM socios WHERE estado = 'ACTIVO' ORDER BY numero").fetchall()
    if not socios:
        raise ReglaNegocioError("No hay socios activos.", codigo="SIN_SOCIOS")
    if crit == "IGUALITARIO":
        if pesos:
            raise DatosInvalidosError("El criterio igualitario no admite pesos por socio.")
        bases = [(int(s["id"]), s["nombre_completo"], Decimal(1)) for s in socios]
    else:
        if not pesos:
            raise DatosInvalidosError("El criterio de participación exige el peso de cada socio aprobado por la "
                                      "Asamblea (CI-FIN-02 Art. 7).")
        activos_ids = {int(s["id"]): s["nombre_completo"] for s in socios}
        bases = []
        for sid, peso in pesos.items():
            if sid not in activos_ids:
                raise DatosInvalidosError(f"El socio {sid} no está activo.")
            valor = _decimal(peso, "peso del socio")
            if valor < 0:
                raise DatosInvalidosError("Los pesos no pueden ser negativos.")
            bases.append((sid, activos_ids[sid], valor))
    reparto = _repartir(a_centavos(distribuible), bases) if distribuible > 0 else \
        [(sid, n, b, CERO) for sid, n, b in bases]
    return PropuestaDistribucion(ejercicio, excedente, pct_legal, legal, pct_educ, educ, reinv, capit, distribuible,
                                 crit, tuple(reparto))


def proponer_distribucion(
    con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any, *, pct_reserva_legal: Any = None,
    pct_educacion: Any = None, reinversion: Any = 0, capitalizacion: Any = 0, criterio: str | None = None,
    pesos: Mapping[int, Any] | None = None,
) -> dict[str, Any]:
    """Informe técnico-financiero de propuesta de distribución (CI-FIN-02
    Art. 6): registra la propuesta y las liquidaciones individuales PENDIENTES."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "CREAR")
    f = a_fecha(fecha)
    with transaccion(con):
        _, fin = rango_ejercicio(con, ejercicio)
        if f <= fin:
            raise DatosInvalidosError("La propuesta se elabora después del cierre del 30 de junio.")
        if con.execute("SELECT 1 FROM distribuciones_excedentes WHERE ejercicio = ? AND estado <> 'ANULADA'",
                       (ejercicio,)).fetchone():
            raise ReglaNegocioError(f"Ya existe una distribución vigente del ejercicio {ejercicio}: anúlela para "
                                    "proponer otra.", codigo="DISTRIBUCION_EXISTENTE")
        prop = calcular_distribucion(con, ejercicio, pct_reserva_legal=pct_reserva_legal, pct_educacion=pct_educacion,
                                     reinversion=reinversion, capitalizacion=capitalizacion, criterio=criterio,
                                     pesos=pesos)
        tasa = Decimal(_param(_params(con, ejercicio), "excedentes_rc_iva_tasa"))
        cur = con.execute(
            """INSERT INTO distribuciones_excedentes (ejercicio, fecha_propuesta, excedente_cent, pct_reserva_legal,
                   reserva_legal_cent, pct_educacion, educacion_cent, reinversion_cent, capitalizacion_cent,
                   distribuible_cent, criterio, propuesto_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, f.isoformat(), a_centavos(prop.excedente), str(prop.pct_reserva_legal),
             a_centavos(prop.reserva_legal), str(prop.pct_educacion), a_centavos(prop.educacion),
             a_centavos(prop.reinversion), a_centavos(prop.capitalizacion), a_centavos(prop.distribuible),
             prop.criterio, sesion.nombre_usuario, _ahora_iso()))
        did = int(cur.lastrowid)
        for sid, _, base, monto in prop.reparto:
            bruto = a_centavos(monto)
            deduccion = a_centavos(redondear_bs(monto * tasa))
            con.execute("""INSERT INTO liquidaciones_socios (distribucion_id, socio_id, base, bruto_cent,
                                                             deducciones_cent, neto_cent) VALUES (?,?,?,?,?,?)""",
                        (did, sid, str(base), bruto, deduccion, bruto - deduccion))
        auditoria.registrar(con, sesion, "distribuciones_excedentes", "INSERT", did, despues=prop.a_dict(),
                            detalle=f"Propuesta de distribución del excedente {ejercicio} (CI-FIN-02 Art. 6)")
    return {"id": did, **prop.a_dict()}


def _distribucion(con: sqlite3.Connection, distribucion_id: int) -> sqlite3.Row:
    _entero(distribucion_id, "distribución")
    fila = con.execute("SELECT * FROM distribuciones_excedentes WHERE id = ?", (distribucion_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la distribución {distribucion_id}.")
    return fila


def anular_distribucion(con: sqlite3.Connection, sesion: Sesion, distribucion_id: int, motivo: str) -> None:
    """Anula una propuesta aún no aprobada por la Asamblea (sin asientos)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "EDITAR")
    causa = _texto(motivo, "el motivo de la anulación", 300)
    with transaccion(con):
        d = _distribucion(con, distribucion_id)
        if d["estado"] not in ("PROPUESTA", "VERIFICADA_CV"):
            raise ReglaNegocioError("Solo se anula una distribución aún no aprobada por la Asamblea.",
                                    codigo="DISTRIBUCION_NO_ANULABLE")
        con.execute("UPDATE liquidaciones_socios SET estado = 'ANULADA' WHERE distribucion_id = ?", (distribucion_id,))
        con.execute("UPDATE distribuciones_excedentes SET estado = 'ANULADA', motivo_anulacion = ? WHERE id = ?",
                    (causa, distribucion_id))
        auditoria.registrar(con, sesion, "distribuciones_excedentes", "UPDATE", distribucion_id,
                            antes={"estado": d["estado"]}, despues={"estado": "ANULADA", "motivo": causa})


def obligaciones_vencidas(con: sqlite3.Connection, fecha: Any) -> list[str]:
    """Obligaciones laborales o tributarias vencidas e impagas (CI-FIN-02 Art. 12 iii)."""
    f = a_fecha(fecha)
    detalle = [f"Cuenta por pagar n.º {r['id']} ({r['categoria_pago']}) vencida el {fecha_pantalla(r['vencimiento'])}"
               for r in con.execute(
                   """SELECT id, categoria_pago, vencimiento FROM cuentas_por_pagar
                       WHERE estado <> 'PAGADA' AND categoria_pago IN ('PLANILLA_APORTES','OBLIGACIONES_RAU')
                         AND vencimiento < ?""", (f.isoformat(),))]
    for pl in con.execute("SELECT * FROM planillas WHERE estado = 'CONTABILIZADA' AND "
                          "(sueldos_pagados_en IS NULL OR aportes_pagados_en IS NULL)"):
        venc = planillas.vencimientos_planilla(con, int(pl["anio"]), int(pl["mes"]))
        if pl["sueldos_pagados_en"] is None and venc["sueldos"] < f:
            detalle.append(f"Sueldos de {NOMBRE_MES[pl['mes'] - 1]} {pl['anio']} vencidos el "
                           f"{fecha_pantalla(venc['sueldos'])}")
        if pl["aportes_pagados_en"] is None and venc["aportes"] < f:
            detalle.append(f"Aportes de {NOMBRE_MES[pl['mes'] - 1]} {pl['anio']} vencidos el "
                           f"{fecha_pantalla(venc['aportes'])}")
    return detalle


def codigo_inf_cv_04(anio: int, numero: int) -> str:
    return f"INF-CV-04 N.º {numero}/{anio}"


def verificaciones_inf_cv_04(con: sqlite3.Connection, distribucion_id: int, fecha: Any,
                             reinversion_conforme: bool) -> list[dict[str, Any]]:
    """Las cinco verificaciones del INF-CV-04 (CI-FIN-02 Art. 13)."""
    d = _distribucion(con, distribucion_id)
    p = _params(con, int(d["ejercicio"]))
    f = a_fecha(fecha)
    excedente = int(d["excedente_cent"])
    pct_legal = _fraccion(int(d["reserva_legal_cent"]), excedente) or CERO
    pct_educ = _fraccion(int(d["educacion_cent"]), excedente) or CERO
    control = tesoreria.verificar_pago_excedentes(con, f, _bs(int(d["distribuible_cent"]))) \
        if int(d["distribuible_cent"]) > 0 else {"permitido": True, "saldo_posterior": tesoreria.saldo_bancario(con, f),
                                                  "sme": tesoreria.sme_vigente(con, f)}
    vencidas = obligaciones_vencidas(con, f)
    minimo_legal = Decimal(_param(p, "reserva_legal_min"))
    minimo_educ = Decimal(_param(p, "fondo_educacion_min"))
    return [
        {"verificacion": f"Reserva Legal constituida (mínimo {_pct_norma(minimo_legal)})",
         "cumple": Decimal(str(d["pct_reserva_legal"])) >= minimo_legal,
         "observacion": f"{formato_bs(_bs(int(d['reserva_legal_cent'])))} ({_pct_texto(pct_legal)})"},
        {"verificacion": f"Fondo de Educación Cooperativa constituido (mínimo {_pct_norma(minimo_educ)})",
         "cumple": Decimal(str(d["pct_educacion"])) >= minimo_educ,
         "observacion": f"{formato_bs(_bs(int(d['educacion_cent'])))} ({_pct_texto(pct_educ)})"},
        {"verificacion": "Reserva de Reinversión y Expansión conforme al Plan CAPEX", "cumple": bool(reinversion_conforme),
         "observacion": formato_bs(_bs(int(d["reinversion_cent"])))},
        {"verificacion": "Saldo bancario post-distribución ≥ SME vigente", "cumple": bool(control["permitido"]),
         "observacion": f"Saldo posterior {formato_bs(control['saldo_posterior'])} frente a SME "
                        f"{formato_bs(control['sme'])} al {fecha_pantalla(f)}"},
        {"verificacion": "Ausencia de obligaciones laborales o tributarias vencidas", "cumple": not vencidas,
         "observacion": "; ".join(vencidas) if vencidas else "Sin obligaciones vencidas"},
    ]


def registrar_inf_cv_04(con: sqlite3.Connection, sesion: Sesion, distribucion_id: int, fecha: Any, *,
                        dictamen: str, reinversion_conforme: bool, observaciones: str | None = None) -> dict[str, Any]:
    """Informe Previo de Fiscalización del Consejo de Vigilancia (INF-CV-04),
    previo e indispensable a la aprobación de la Asamblea (CI-FIN-02 Art. 13).

    El dictamen FAVORABLE solo procede si las cinco verificaciones se cumplen.
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VERIFICAR")
    if dictamen not in ("FAVORABLE", "CON_OBSERVACIONES"):
        raise DatosInvalidosError("El dictamen debe ser FAVORABLE o CON_OBSERVACIONES.")
    obs = _opcional(observaciones, "observaciones", 1000)
    f = a_fecha(fecha)
    with transaccion(con):
        d = _distribucion(con, distribucion_id)
        if d["estado"] != "PROPUESTA":
            raise ReglaNegocioError("El INF-CV-04 se emite sobre una propuesta pendiente.", codigo="DISTRIBUCION_NO_PROPUESTA")
        verif = verificaciones_inf_cv_04(con, distribucion_id, f, reinversion_conforme)
        if dictamen == "FAVORABLE" and not all(v["cumple"] for v in verif):
            raise ReglaNegocioError("El dictamen no puede ser favorable con verificaciones incumplidas: "
                                    + "; ".join(v["verificacion"] for v in verif if not v["cumple"]) + ".",
                                    codigo="DICTAMEN_INCONSISTENTE")
        if dictamen == "CON_OBSERVACIONES" and not obs:
            raise DatosInvalidosError("Detalle las observaciones del Consejo de Vigilancia.")
        numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) + 1 FROM informes_cv_04 WHERE anio = ?",
                                 (f.year,)).fetchone()[0])
        cur = con.execute(
            """INSERT INTO informes_cv_04 (numero, anio, distribucion_id, fecha, verificaciones_json, dictamen,
                                           observaciones, elaborado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (numero, f.year, distribucion_id, f.isoformat(), auditoria.a_json(verif), dictamen, obs,
             sesion.nombre_completo, _ahora_iso()))
        iid = int(cur.lastrowid)
        con.execute("UPDATE distribuciones_excedentes SET estado = 'VERIFICADA_CV', inf_cv_04_id = ? WHERE id = ?",
                    (iid, distribucion_id))
        codigo = codigo_inf_cv_04(f.year, numero)
        auditoria.registrar(con, sesion, "informes_cv_04", "INSERT", iid,
                            despues={"codigo": codigo, "dictamen": dictamen, "verificaciones": verif})
    return {"id": iid, "codigo": codigo, "dictamen": dictamen, "verificaciones": verif}


def _documento_en_autorizacion(con: sqlite3.Connection, autorizacion_id: int, codigo: str) -> bool:
    fila = con.execute("SELECT documentos_json FROM autorizaciones WHERE id = ?", (autorizacion_id,)).fetchone()
    if fila is None or not fila[0]:
        return False
    docs = json.loads(fila[0]).get("documentos", {})
    return docs.get("INF-CV-04") == codigo


def aprobar_distribucion(
    con: sqlite3.Connection, sesion: Sesion, distribucion_id: int, fecha: Any, *, autorizacion_asamblea_id: int,
    acta_ref: str, autorizacion_capitalizacion_id: int | None = None,
) -> dict[str, Any]:
    """Registra la decisión de la Asamblea: apropiación del excedente a las
    reservas (3.1.02.01/02/03), capitalización (3.1.01.05) y excedente en
    distribución (3.1.03.04), y su traslado a 2.1.05.01 Excedentes por pagar.

    La autorización DISTRIBUCION_EXCEDENTES (firma de la AG con el INF-CV-04
    como documento previo) se verifica aquí y se consume al pagar.

    Raises:
        ReglaNegocioError: sin INF-CV-04 (``SIN_INF_CV_04``) o autorización que
            no remite a ese informe.
        AutorizacionError: autorización incompleta.
    """
    seguridad.exigir_permiso(con, sesion, "contabilidad", "CREAR")
    acta = _texto(acta_ref, "el acta de la Asamblea", 120)
    f = a_fecha(fecha)
    with transaccion(con):
        d = _distribucion(con, distribucion_id)
        if d["estado"] != "VERIFICADA_CV":
            raise ReglaNegocioError("La Asamblea aprueba la distribución con el INF-CV-04 previo del Consejo de "
                                    "Vigilancia (CI-FIN-02 Art. 13).", codigo="SIN_INF_CV_04")
        informe = con.execute("SELECT * FROM informes_cv_04 WHERE id = ?", (d["inf_cv_04_id"],)).fetchone()
        codigo = codigo_inf_cv_04(int(informe["anio"]), int(informe["numero"]))
        if f < date.fromisoformat(informe["fecha"]):
            raise DatosInvalidosError("La aprobación no puede ser anterior al INF-CV-04.")
        distribuible = _bs(int(d["distribuible_cent"]))
        seguridad.verificar_autorizacion(con, autorizacion_asamblea_id,
                                         tipo_decision=TipoDecision.DISTRIBUCION_EXCEDENTES, monto=distribuible)
        if not _documento_en_autorizacion(con, autorizacion_asamblea_id, codigo):
            raise ReglaNegocioError(f"La autorización de la Asamblea debe remitir al {codigo}.",
                                    codigo="AUTORIZACION_SIN_INF_CV_04")
        capitalizacion = _bs(int(d["capitalizacion_cent"]))
        if capitalizacion > 0:
            if autorizacion_capitalizacion_id is None:
                raise AutorizacionError("La capitalización requiere la decisión de la Asamblea con mayoría calificada "
                                        "(CI-FIN-02 Art. 11).", codigo="CAPITALIZACION_SIN_AUTORIZACION")
            seguridad.verificar_autorizacion(con, autorizacion_capitalizacion_id,
                                             tipo_decision=TipoDecision.CAPITALIZACION_EXCEDENTES, monto=capitalizacion)
        ej = int(d["ejercicio"])
        excedente = _bs(int(d["excedente_cent"]))
        lineas = [{"cuenta": CUENTA_EXCEDENTE, "debe": excedente - capitalizacion}]
        for cuenta, cent in ((CUENTA_RESERVA_LEGAL, d["reserva_legal_cent"]), (CUENTA_EDUCACION, d["educacion_cent"]),
                             (CUENTA_REINVERSION, d["reinversion_cent"]),
                             (CUENTA_EN_DISTRIBUCION, d["distribuible_cent"])):
            if int(cent) > 0:
                lineas.append({"cuenta": cuenta, "haber": _bs(int(cent))})
        apropiacion = contable.crear_asiento(
            con, sesion, f, f"Apropiación del excedente {ej} según Asamblea ({acta}): reservas y excedente en "
            "distribución", lineas, "EXCEDENTES", acta, tipo="TRASPASO")
        asiento_cap = None
        if capitalizacion > 0:
            asiento_cap = contable.crear_asiento(
                con, sesion, f, f"Capitalización del excedente {ej} en Aportes Adicionales de Socios ({acta})",
                [{"cuenta": CUENTA_EXCEDENTE, "debe": capitalizacion},
                 {"cuenta": CUENTA_APORTES_ADICIONALES, "haber": capitalizacion}], "EXCEDENTES", acta, tipo="TRASPASO")
            seguridad.consumir_autorizacion(con, sesion, autorizacion_capitalizacion_id, "distribuciones_excedentes",
                                            distribucion_id, tipo_decision=TipoDecision.CAPITALIZACION_EXCEDENTES,
                                            monto=capitalizacion)
        asiento_dist = None
        if distribuible > 0:
            asiento_dist = contable.crear_asiento(
                con, sesion, f, f"Excedente {ej} en distribución a Excedentes por pagar a socios ({acta})",
                [{"cuenta": CUENTA_EN_DISTRIBUCION, "debe": distribuible},
                 {"cuenta": CUENTA_EXCEDENTES_POR_PAGAR, "haber": distribuible}], "EXCEDENTES", acta, tipo="TRASPASO")
        estado = "APROBADA" if distribuible > 0 else "PAGADA"
        con.execute(
            """UPDATE distribuciones_excedentes SET estado = ?, autorizacion_asamblea_id = ?,
                   autorizacion_capitalizacion_id = ?, acta_ref = ?, fecha_aprobacion = ?, asiento_apropiacion_id = ?,
                   asiento_capitalizacion_id = ?, asiento_distribucion_id = ?, aprobado_por = ? WHERE id = ?""",
            (estado, autorizacion_asamblea_id, autorizacion_capitalizacion_id, acta, f.isoformat(), apropiacion.id,
             asiento_cap.id if asiento_cap else None, asiento_dist.id if asiento_dist else None,
             sesion.nombre_usuario, distribucion_id))
        if distribuible == 0:
            seguridad.consumir_autorizacion(con, sesion, autorizacion_asamblea_id, "distribuciones_excedentes",
                                            distribucion_id, tipo_decision=TipoDecision.DISTRIBUCION_EXCEDENTES,
                                            monto=distribuible)
        auditoria.registrar(con, sesion, "distribuciones_excedentes", "AUTORIZACION", distribucion_id,
                            antes={"estado": d["estado"]},
                            despues={"estado": estado, "acta": acta, "apropiacion": apropiacion.comprobante,
                                     "distribucion": asiento_dist.comprobante if asiento_dist else None})
    return {"estado": estado, "asiento_apropiacion": apropiacion.comprobante,
            "asiento_capitalizacion": asiento_cap.comprobante if asiento_cap else None,
            "asiento_distribucion": asiento_dist.comprobante if asiento_dist else None}


def firmar_liquidaciones(con: sqlite3.Connection, sesion: Sesion, distribucion_id: int) -> int:
    """La Gerencia General firma las liquidaciones individuales (CI-FIN-02 Art. 8)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "AUTORIZAR")
    with transaccion(con):
        d = _distribucion(con, distribucion_id)
        if d["estado"] != "APROBADA":
            raise ReglaNegocioError("Las liquidaciones se firman con la distribución aprobada por la Asamblea.",
                                    codigo="DISTRIBUCION_NO_APROBADA")
        momento = _ahora_iso()
        n = con.execute("UPDATE liquidaciones_socios SET estado = 'FIRMADA', firmada_por = ?, firmada_en = ? "
                        "WHERE distribucion_id = ? AND estado = 'PENDIENTE'",
                        (sesion.nombre_completo, momento, distribucion_id)).rowcount
        if n == 0:
            raise ReglaNegocioError("No hay liquidaciones pendientes de firma.", codigo="SIN_LIQUIDACIONES_PENDIENTES")
        auditoria.registrar(con, sesion, "liquidaciones_socios", "AUTORIZACION", distribucion_id,
                            antes={"estado": "PENDIENTE"}, despues={"estado": "FIRMADA", "cantidad": n})
    return n


def verificar_pago_distribucion(con: sqlite3.Connection, distribucion_id: int, fecha: Any) -> dict[str, Any]:
    """¿Se puede pagar hoy? SME posterior, obligaciones vencidas y ventana de pago."""
    d = _distribucion(con, distribucion_id)
    f = a_fecha(fecha)
    monto = _bs(int(d["distribuible_cent"]))
    control = tesoreria.verificar_pago_excedentes(con, f, monto)
    vencidas = obligaciones_vencidas(con, f)
    _, fin = rango_ejercicio(con, int(d["ejercicio"]))
    meses = int(_param(_params(con, int(d["ejercicio"])), "excedentes_pago_meses"))
    anio, mes = fin.year + (fin.month + meses - 1) // 12, (fin.month + meses - 1) % 12 + 1
    ventana = activos.fin_de_mes(anio, mes)
    return {**control, "obligaciones_vencidas": vencidas, "fin_ventana": ventana.isoformat(),
            "en_ventana": f <= ventana, "permitido": bool(control["permitido"]) and not vencidas}


def pagar_distribucion(con: sqlite3.Connection, sesion: Sesion, distribucion_id: int, fecha: Any, cuenta_banco: str, *,
                       autorizacion_desembolso_id: int, medio: str = "TRANSFERENCIA") -> dict[str, Any]:
    """Paga el excedente distribuible (2.1.05.01 contra banco) solo si el saldo
    bancario posterior es >= SME (POL-04; CI-FIN-02 Arts. 9, 10 y 12).

    Raises:
        ReglaNegocioError: ``PAGO_EXCEDENTES_BAJO_SME`` (se posterga un ciclo),
            ``OBLIGACIONES_VENCIDAS``, liquidaciones sin firmar.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    with transaccion(con):
        d = _distribucion(con, distribucion_id)
        if d["estado"] != "APROBADA":
            raise ReglaNegocioError("Solo se paga una distribución aprobada y aún no pagada.",
                                    codigo="DISTRIBUCION_NO_APROBADA")
        sin_firma = int(con.execute("SELECT COUNT(*) FROM liquidaciones_socios WHERE distribucion_id = ? AND "
                                    "estado <> 'FIRMADA'", (distribucion_id,)).fetchone()[0])
        if sin_firma:
            raise ReglaNegocioError(f"Faltan {sin_firma} liquidación(es) individual(es) por firmar por la Gerencia "
                                    "General (CI-FIN-02 Art. 8).", codigo="LIQUIDACIONES_SIN_FIRMA")
        vencidas = obligaciones_vencidas(con, f)
        if vencidas:
            raise ReglaNegocioError("CI-FIN-02 Art. 12 (iii): existen obligaciones laborales o tributarias vencidas: "
                                    + "; ".join(vencidas[:5]) + ".", codigo="OBLIGACIONES_VENCIDAS")
        monto = _bs(int(d["distribuible_cent"]))
        mid = tesoreria.pagar_excedentes(
            con, sesion, f, monto, cuenta_banco, autorizacion_asamblea_id=int(d["autorizacion_asamblea_id"]),
            autorizacion_desembolso_id=autorizacion_desembolso_id, medio=medio,
            documento_ref=f"Distribución excedente {d['ejercicio']} ({d['acta_ref']})")
        momento = _ahora_iso()
        con.execute("UPDATE liquidaciones_socios SET estado = 'PAGADA', pagada_en = ? WHERE distribucion_id = ?",
                    (f.isoformat(), distribucion_id))
        con.execute("UPDATE distribuciones_excedentes SET estado = 'PAGADA', movimiento_pago_id = ?, fecha_pago = ?, "
                    "pagado_por = ? WHERE id = ?", (mid, f.isoformat(), sesion.nombre_usuario, distribucion_id))
        auditoria.registrar(con, sesion, "distribuciones_excedentes", "UPDATE", distribucion_id,
                            antes={"estado": "APROBADA"},
                            despues={"estado": "PAGADA", "monto": monto, "movimiento_bancario": mid, "medio": medio,
                                     "registrado_en": momento})
    return {"movimiento_id": mid, "monto": monto}


def listar_distribuciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    filas = []
    for d in con.execute("SELECT * FROM distribuciones_excedentes ORDER BY id DESC"):
        informe = con.execute("SELECT * FROM informes_cv_04 WHERE id = ?", (d["inf_cv_04_id"],)).fetchone() \
            if d["inf_cv_04_id"] else None
        filas.append({**dict(d), **{k: _bs(int(d[f"{k}_cent"])) for k in (
            "excedente", "reserva_legal", "educacion", "reinversion", "capitalizacion", "distribuible")},
            "inf_cv_04": codigo_inf_cv_04(int(informe["anio"]), int(informe["numero"])) if informe else None,
            "dictamen": informe["dictamen"] if informe else None})
    return filas


def obtener_distribucion(con: sqlite3.Connection, sesion: Sesion, distribucion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    d = _distribucion(con, distribucion_id)
    fila = next(x for x in listar_distribuciones(con, sesion) if x["id"] == distribucion_id)
    informe = con.execute("SELECT * FROM informes_cv_04 WHERE id = ?", (d["inf_cv_04_id"],)).fetchone() \
        if d["inf_cv_04_id"] else None
    return {**fila, "liquidaciones": liquidaciones(con, sesion, distribucion_id),
            "informe_cv_04": None if informe is None else {
                **dict(informe), "codigo": codigo_inf_cv_04(int(informe["anio"]), int(informe["numero"])),
                "verificaciones": json.loads(informe["verificaciones_json"])}}


def liquidaciones(con: sqlite3.Connection, sesion: Sesion, distribucion_id: int) -> list[dict[str, Any]]:
    """Liquidaciones individuales por socio (CI-FIN-02 Art. 8)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    return [{**dict(l), "bruto": _bs(int(l["bruto_cent"])), "deducciones": _bs(int(l["deducciones_cent"])),
             "neto": _bs(int(l["neto_cent"]))}
            for l in con.execute(
                """SELECT l.*, s.numero AS socio_numero, s.nombre_completo AS socio, s.ci, s.ci_expedido, s.certificados
                     FROM liquidaciones_socios l JOIN socios s ON s.id = l.socio_id
                    WHERE l.distribucion_id = ? ORDER BY s.numero""", (distribucion_id,))]


def ejercicios(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ejercicios registrados con su estado y cierre anual."""
    return [{**dict(e), "cierre_anual": con.execute("SELECT 1 FROM cierres_anuales WHERE ejercicio = ?",
                                                    (e["ejercicio"],)).fetchone() is not None}
            for e in con.execute("SELECT * FROM ejercicios ORDER BY ejercicio")]
