"""Personal y planillas del ERP Pollolandia (Fase 5, Ciclo 2 — Egresos / Planillas).

Implementa PC-09, PR-FIN-04, PR-FIN-05, POL-06, CI-FIN-03, CI-FIN-14, CI-FIN-17
y los formatos CIR-EGR-01, FIN-EGR-06 a FIN-EGR-13 y CONT-EGR-01:

1. Personal fijo (Gerente General, Jefe de Finanzas, Encargado de Granja) con
   contrato, fecha de ingreso, afiliación e historial salarial.
2. Planilla mensual (FIN-EGR-09): haber, bonos, total ganado, aporte laboral
   12,71 % (2.1.02.05, retención que no es costo) y líquido pagable.
3. Beneficios sociales (FIN-EGR-10): aguinaldo (2.1.02.02), indemnización
   (2.2.01.01) y aporte patronal 17,21 % (2.1.02.03). Imputación del personal
   de granja a los sufijos 20, 21, 38 y 39 y del administrativo a 7.1.01.01-04.
4. Prorrateo de la mano de obra polivalente entre 6.1.01.01 y 6.1.01.02 desde
   2027 con las hojas de tiempo firmadas (FIN-EGR-12), conciliación pagado vs
   absorbido (FIN-EGR-13) y verificación cruzada de horas (CI-FIN-14).
5. Personal eventual: circular de cierre (CIR-EGR-01) con antelación mínima,
   autorización solo en lunes con verificación presupuestaria y suplencia
   (FIN-EGR-06), contrato (CONT-EGR-01) y liquidación por destajo con DPF y
   semáforo (FIN-EGR-08).
6. Liquidación final de beneficios dentro del plazo (FIN-EGR-11).
7. Alertas de pago legal de sueldos, aportes, aguinaldo y liquidaciones.

Las tasas, plazos y tarifas se leen de ``parametros``. Todo hecho económico se
registra con ``servicios.contable.crear_asiento``. No importa ``streamlit``.
"""
from __future__ import annotations

import calendar
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import (
    a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion,
)
from servicios import auditoria, contable, inventarios, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion, TipoDecision
from utils.formato import a_decimal, a_fecha, es_dia_habil, fecha_pantalla, formato_bs, redondear_bs

# --------------------------------------------------------------------------
# Estructura del Plan de Cuentas v3 (no cifras)
# --------------------------------------------------------------------------
SUELDOS_POR_PAGAR = "2.1.02.01"
AGUINALDO_POR_PAGAR = "2.1.02.02"
PATRONAL_POR_PAGAR = "2.1.02.03"
EVENTUAL_POR_PAGAR = "2.1.02.04"
LABORAL_POR_PAGAR = "2.1.02.05"
PROVISION_INDEMNIZACION = "2.2.01.01"
CENTRO_CARNE = "6.1.01.01"
CENTRO_ALIMENTO = "6.1.01.02"
SUFIJO = {"salario": "20", "aguinaldo": "21", "eventual": "22", "patronal": "38", "indemnizacion": "39"}
GASTO_ADMIN = {"salario": "7.1.01.01", "aguinaldo": "7.1.01.02", "patronal": "7.1.01.03",
               "indemnizacion": "7.1.01.04"}
AREAS = {"GRANJA": "Granja (costo de producción)", "ADMINISTRACION": "Administración (gasto)"}
CONTRATOS = ("INDEFINIDO", "PLAZO_FIJO")
MESES_ANIO = 12            # hecho del calendario, no regla normativa
CERO = Decimal("0.00")
TASA = Decimal("0.0001")


# ==========================================================================
# UTILITARIOS
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 300) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"El campo '{campo}' es obligatorio.")
    if len(valor.strip()) > maximo:
        raise DatosInvalidosError(f"El campo '{campo}' admite como máximo {maximo} caracteres.")
    return valor.strip()


def _opcional(valor: Any, campo: str, maximo: int = 300) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    return _texto(valor, campo, maximo)


def _entero(valor: Any, campo: str, minimo: int = 0, maximo: int | None = None) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise DatosInvalidosError(f"El campo '{campo}' debe ser un número entero.")
    if valor < minimo or (maximo is not None and valor > maximo):
        rango = f"entre {minimo} y {maximo}" if maximo is not None else f"mayor o igual a {minimo}"
        raise DatosInvalidosError(f"El campo '{campo}' debe estar {rango}.")
    return valor


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _dia(numero: int) -> str:
    return ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][numero - 1]


def _fin_de_mes(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def _pct(valor: Decimal) -> Decimal:
    return (Decimal(valor) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ultimo_dia_habil(anio: int, mes: int) -> date:
    """Último día hábil del mes (lunes a viernes sin feriados nacionales)."""
    f = _fin_de_mes(anio, mes)
    while not es_dia_habil(f):
        f -= timedelta(days=1)
    return f


def dias_360(inicio: Any, fin: Any) -> int:
    """Días comerciales (30/360) entre dos fechas, ambos extremos incluidos."""
    i, f = a_fecha(inicio), a_fecha(fin)
    if f < i:
        return 0
    d1, d2 = min(i.day, 30), min(f.day, 30)
    return (f.year - i.year) * 360 + (f.month - i.month) * 30 + (d2 - d1) + 1


# ==========================================================================
# 1. TASAS Y CÁLCULO (PC-09; Manual de Costos §6.2)
# ==========================================================================
@dataclass(frozen=True)
class Tasas:
    """Tasas vigentes de la planilla en un ejercicio."""

    laboral: Decimal
    patronal: Decimal
    aguinaldo: Decimal
    indemnizacion: Decimal
    doceavos: bool
    dias_mes: int


def tasas_vigentes(con: sqlite3.Connection, fecha: Any) -> Tasas:
    p = _params(con, fecha)
    return Tasas(laboral=Decimal(_param(p, "aporte_laboral")), patronal=Decimal(_param(p, "aporte_patronal")),
                 aguinaldo=Decimal(_param(p, "aguinaldo_pct")), indemnizacion=Decimal(_param(p, "indemnizacion_pct")),
                 doceavos=bool(_param(p, "provision_base_doceavos")), dias_mes=int(_param(p, "dias_mes_comercial")))


def provision(base: Decimal, pct: Decimal, doceavos: bool) -> Decimal:
    """Provisión de aguinaldo o indemnización: 1/12 del salario (un mes por
    año) o el porcentaje redondeado de PC-09, según el parámetro."""
    if doceavos:
        return redondear_bs(Decimal(base) / MESES_ANIO)
    return redondear_bs(Decimal(base) * pct)


@dataclass(frozen=True)
class CalculoLinea:
    """Una línea de la planilla FIN-EGR-09 / FIN-EGR-10."""

    salario_mensual: Decimal
    dias: int
    haber_basico: Decimal
    bonos: Decimal
    total_ganado: Decimal
    aporte_laboral: Decimal
    liquido: Decimal
    aporte_patronal: Decimal
    aguinaldo: Decimal
    indemnizacion: Decimal

    @property
    def costo_total(self) -> Decimal:
        """Costo para la cooperativa: total ganado + patronal + provisiones
        (el aporte laboral es retención del trabajador, no costo)."""
        return self.total_ganado + self.aporte_patronal + self.aguinaldo + self.indemnizacion


def calcular_linea(salario_mensual: Any, bonos: Any, dias: int, tasas: Tasas) -> CalculoLinea:
    """Haber básico (proporcional a los días si el mes es incompleto), total
    ganado, aporte laboral sobre el total ganado, líquido pagable y cargas
    sociales sobre el salario bruto."""
    salario = redondear_bs(salario_mensual)
    extra = redondear_bs(bonos or 0)
    if salario <= 0:
        raise DatosInvalidosError("El salario mensual debe ser mayor a cero.")
    if extra < 0:
        raise DatosInvalidosError("Los bonos no pueden ser negativos.")
    _entero(dias, "días trabajados", 0, 31)
    haber = salario if dias >= tasas.dias_mes else redondear_bs(salario * dias / tasas.dias_mes)
    total = haber + extra
    laboral = redondear_bs(total * tasas.laboral)
    return CalculoLinea(salario_mensual=salario, dias=dias, haber_basico=haber, bonos=extra, total_ganado=total,
                        aporte_laboral=laboral, liquido=total - laboral,
                        aporte_patronal=redondear_bs(total * tasas.patronal),
                        aguinaldo=provision(total, tasas.aguinaldo, tasas.doceavos),
                        indemnizacion=provision(total, tasas.indemnizacion, tasas.doceavos))


def costo_mod_ciclo(con: sqlite3.Connection, salario_mensual: Any, fecha: Any) -> dict[str, Decimal]:
    """Mano de obra fija imputable a un ciclo (Manual de Costos §6.2).

    # DISCREPANCIA: el Manual de Costos muestra Bs 7.920 con la fórmula
    # "3.300 × (73/30)" (que daría Bs 8.030) y el patronal Bs 1.363,03 con
    # "(7.920 + 660) × 17,21 %" (que daría Bs 1.476,62). Las cifras que
    # reproducen el Manual de Costos y el Manual Presupuestario son:
    # salario del ciclo = salario mensual × 12 / ciclos por ejercicio (2,4
    # meses = 39.600 / 5) y patronal = salario del ciclo × 17,21 %. Se aplican
    # esas (las cifras prevalecen sobre la fórmula descriptiva).
    """
    p = _params(con, fecha)
    tasas = tasas_vigentes(con, fecha)
    ciclos = int(_param(p, "ciclos_por_ejercicio"))
    salario = redondear_bs(Decimal(redondear_bs(salario_mensual)) * MESES_ANIO / ciclos)
    aguinaldo = provision(salario, tasas.aguinaldo, tasas.doceavos)
    patronal = redondear_bs(salario * tasas.patronal)
    indemnizacion = provision(salario, tasas.indemnizacion, tasas.doceavos)
    return {"salario": salario, "aguinaldo": aguinaldo, "aporte_patronal": patronal,
            "indemnizacion": indemnizacion, "total": salario + aguinaldo + patronal + indemnizacion}


def costo_eventual(con: sqlite3.Connection, aves: int, fecha: Any) -> Decimal:
    """Presupuesto de faena: aves × tarifa de destajo vigente (Bs 1,50 por ave)."""
    _entero(aves, "aves", 1)
    return redondear_bs(Decimal(aves) * Decimal(_param(_params(con, fecha), "tarifa_eventual_por_ave")))


# ==========================================================================
# 2. PERSONAL FIJO
# ==========================================================================
def _personal(con: sqlite3.Connection, personal_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM personal WHERE id = ?", (_entero(personal_id, "trabajador", 1),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el trabajador {personal_id}.")
    return fila


def registrar_personal(
    con: sqlite3.Connection, sesion: Sesion, nombre: str, ci: str, cargo: str, area: str, salario_mensual: Any,
    fecha_ingreso: Any, tipo_contrato: str, contrato_ref: str, *, polivalente: bool = False,
    nua_gestora: str | None = None, matricula_cns: str | None = None,
) -> int:
    """Alta del trabajador fijo con contrato, fecha de ingreso y salario."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    if area not in AREAS:
        raise DatosInvalidosError(f"Área no válida: {area!r}.")
    if tipo_contrato not in CONTRATOS:
        raise DatosInvalidosError(f"Tipo de contrato no válido: {tipo_contrato!r}.")
    if polivalente and area != "GRANJA":
        raise DatosInvalidosError("Solo el personal de granja es polivalente (CI-FIN-03).")
    salario = redondear_bs(salario_mensual)
    if salario <= 0:
        raise DatosInvalidosError("El salario mensual debe ser mayor a cero.")
    ingreso = a_fecha(fecha_ingreso, "fecha de ingreso")
    datos = {"nombre": _texto(nombre, "nombre", 120), "ci": _texto(ci, "C.I.", 20), "cargo": _texto(cargo, "cargo", 80),
             "contrato_ref": _texto(contrato_ref, "contrato", 60)}
    if con.execute("SELECT 1 FROM personal WHERE ci = ?", (datos["ci"],)).fetchone():
        raise DatosInvalidosError("Ya existe un trabajador con ese C.I.", codigo="PERSONAL_DUPLICADO")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO personal (nombre, ci, cargo, area, polivalente, tipo_contrato, contrato_ref, fecha_ingreso,
                   nua_gestora, matricula_cns, estado, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datos["nombre"], datos["ci"], datos["cargo"], area, int(bool(polivalente)), tipo_contrato,
             datos["contrato_ref"], ingreso.isoformat(), _opcional(nua_gestora, "NUA", 30),
             _opcional(matricula_cns, "matrícula CNS", 30), "ACTIVO", sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        con.execute("INSERT INTO historial_salarios (personal_id, vigente_desde, salario_cent, motivo, registrado_por, "
                    "registrado_en) VALUES (?,?,?,?,?,?)",
                    (pid, ingreso.isoformat(), a_centavos(salario), "Salario de contratación", sesion.nombre_usuario,
                     _ahora_iso()))
        auditoria.registrar(con, sesion, "personal", "INSERT", pid,
                            despues={**datos, "area": area, "polivalente": bool(polivalente), "salario": salario,
                                     "fecha_ingreso": ingreso.isoformat(), "tipo_contrato": tipo_contrato})
    return pid


def actualizar_salario(con: sqlite3.Connection, sesion: Sesion, personal_id: int, vigente_desde: Any,
                       salario_mensual: Any, motivo: str) -> int:
    """Nuevo salario con vigencia (p. ej. indexación anual del 5 %)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    trabajador = _personal(con, personal_id)
    desde = a_fecha(vigente_desde)
    salario = redondear_bs(salario_mensual)
    if salario <= 0:
        raise DatosInvalidosError("El salario mensual debe ser mayor a cero.")
    if desde.isoformat() < trabajador["fecha_ingreso"]:
        raise DatosInvalidosError("El salario no puede regir antes del ingreso.")
    anterior = salario_vigente(con, personal_id, desde)
    with transaccion(con):
        cur = con.execute("INSERT INTO historial_salarios (personal_id, vigente_desde, salario_cent, motivo, "
                          "registrado_por, registrado_en) VALUES (?,?,?,?,?,?)",
                          (personal_id, desde.isoformat(), a_centavos(salario), _texto(motivo, "motivo"),
                           sesion.nombre_usuario, _ahora_iso()))
        auditoria.registrar(con, sesion, "historial_salarios", "INSERT", cur.lastrowid,
                            antes={"salario": anterior}, despues={"salario": salario, "vigente_desde": desde.isoformat()})
    return int(cur.lastrowid)


def salario_vigente(con: sqlite3.Connection, personal_id: int, fecha: Any) -> Decimal:
    fila = con.execute("SELECT salario_cent FROM historial_salarios WHERE personal_id = ? AND vigente_desde <= ? "
                       "ORDER BY vigente_desde DESC LIMIT 1", (personal_id, a_fecha(fecha).isoformat())).fetchone()
    if fila is None:
        raise ReglaNegocioError("El trabajador no tiene salario vigente a esa fecha.", codigo="SIN_SALARIO")
    return de_centavos(int(fila[0]))


def registrar_retiro(con: sqlite3.Connection, sesion: Sesion, personal_id: int, fecha_retiro: Any, motivo: str) -> None:
    """Desvinculación: inicia el plazo de la liquidación final (FIN-EGR-11)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    trabajador = _personal(con, personal_id)
    f = a_fecha(fecha_retiro, "fecha de retiro")
    if trabajador["estado"] != "ACTIVO":
        raise ReglaNegocioError("El trabajador ya está retirado.", codigo="PERSONAL_RETIRADO")
    if f.isoformat() < trabajador["fecha_ingreso"]:
        raise DatosInvalidosError("La fecha de retiro es anterior al ingreso.")
    with transaccion(con):
        con.execute("UPDATE personal SET estado = 'RETIRADO', fecha_retiro = ?, motivo_retiro = ?, actualizado_en = ? "
                    "WHERE id = ?", (f.isoformat(), _texto(motivo, "motivo del retiro"), _ahora_iso(), personal_id))
        auditoria.registrar(con, sesion, "personal", "UPDATE", personal_id, antes={"estado": "ACTIVO"},
                            despues={"estado": "RETIRADO", "fecha_retiro": f.isoformat()})


def listar_personal(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    filas = []
    for p in con.execute("SELECT * FROM personal ORDER BY area DESC, nombre"):
        try:
            salario = salario_vigente(con, int(p["id"]), max(f.isoformat(), p["fecha_ingreso"]))
        except ReglaNegocioError:
            salario = None
        filas.append({**dict(p), "salario": salario})
    return filas


# ==========================================================================
# 3. HOJAS DE TIEMPO Y PRORRATEO (FIN-EGR-12; CI-FIN-03; POL-06)
# ==========================================================================
def registrar_hoja_tiempo(con: sqlite3.Connection, sesion: Sesion, personal_id: int, fecha: Any, horas_crianza: int,
                          horas_alimento: int, firmada_trabajador: bool) -> int:
    """Registro diario del polivalente, validado por el Encargado de Granja.
    Debe llenarse el mismo día (no se reconstruye al cierre de la semana)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "CREAR")
    trabajador = _personal(con, personal_id)
    if trabajador["area"] != "GRANJA" or not trabajador["polivalente"]:
        raise ReglaNegocioError("Solo el personal polivalente de granja llena hojas de tiempo (CI-FIN-03).",
                                codigo="NO_POLIVALENTE")
    f = a_fecha(fecha)
    hc, ha = _entero(horas_crianza, "horas de crianza/faenado", 0, 24), _entero(horas_alimento, "horas de planta", 0, 24)
    if hc + ha == 0 or hc + ha > 24:
        raise DatosInvalidosError("El total de horas del día debe estar entre 1 y 24.")
    if con.execute("SELECT 1 FROM hojas_tiempo WHERE personal_id = ? AND fecha = ?", (personal_id, f.isoformat())
                   ).fetchone():
        raise DatosInvalidosError("Ya existe la hoja de tiempo de ese día.", codigo="HOJA_DUPLICADA")
    with transaccion(con):
        cur = con.execute("INSERT INTO hojas_tiempo (personal_id, fecha, horas_crianza, horas_alimento, firmada_trabajador, "
                          "validado_por, registrado_en) VALUES (?,?,?,?,?,?,?)",
                          (personal_id, f.isoformat(), hc, ha, int(bool(firmada_trabajador)), sesion.nombre_usuario,
                           _ahora_iso()))
        auditoria.registrar(con, sesion, "hojas_tiempo", "INSERT", cur.lastrowid,
                            despues={"personal_id": personal_id, "fecha": f.isoformat(), "crianza": hc, "alimento": ha,
                                     "firmada": bool(firmada_trabajador)})
    return int(cur.lastrowid)


def regularizar_hoja(con: sqlite3.Connection, sesion: Sesion, hoja_id: int, constancia: str) -> None:
    """Hoja sin firma: el Encargado deja constancia y estima las horas con el
    trabajador al día siguiente (CI-FIN-03, preguntas frecuentes)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "CREAR")
    texto = _texto(constancia, "constancia de regularización")
    with transaccion(con):
        hoja = con.execute("SELECT * FROM hojas_tiempo WHERE id = ?", (hoja_id,)).fetchone()
        if hoja is None:
            raise DatosInvalidosError(f"No existe la hoja {hoja_id}.")
        if hoja["firmada_trabajador"] or hoja["regularizacion"]:
            raise ReglaNegocioError("La hoja ya está firmada o regularizada.", codigo="HOJA_EN_REGLA")
        con.execute("UPDATE hojas_tiempo SET regularizacion = ? WHERE id = ?", (texto, hoja_id))
        auditoria.registrar(con, sesion, "hojas_tiempo", "UPDATE", hoja_id, antes={"regularizacion": None},
                            despues={"regularizacion": texto})


def horas_periodo(con: sqlite3.Connection, personal_id: int, desde: Any, hasta: Any) -> dict[str, int]:
    """Horas válidas (firmadas o regularizadas) y hojas sin regularizar."""
    validas = con.execute(
        """SELECT COALESCE(SUM(horas_crianza), 0), COALESCE(SUM(horas_alimento), 0) FROM hojas_tiempo
            WHERE personal_id = ? AND fecha BETWEEN ? AND ? AND (firmada_trabajador = 1 OR regularizacion IS NOT NULL)""",
        (personal_id, a_fecha(desde).isoformat(), a_fecha(hasta).isoformat())).fetchone()
    pendientes = con.execute(
        "SELECT COUNT(*) FROM hojas_tiempo WHERE personal_id = ? AND fecha BETWEEN ? AND ? AND firmada_trabajador = 0 "
        "AND regularizacion IS NULL", (personal_id, a_fecha(desde).isoformat(), a_fecha(hasta).isoformat())).fetchone()
    return {"crianza": int(validas[0]), "alimento": int(validas[1]), "sin_regularizar": int(pendientes[0])}


def prorrateo_trabajador(con: sqlite3.Connection, trabajador: Mapping[str, Any], anio: int, mes: int) -> dict[str, Any]:
    """Porción del costo del trabajador que va a carne (6.1.01.01).

    Desde el ejercicio de inicio del alimento propio, el polivalente se
    prorratea por horas (crianza/faenado ÷ total); sin hojas válidas en el mes
    se usa el porcentaje por defecto (75 %). Antes de 2027 todo va a carne.
    """
    fin = _fin_de_mes(anio, mes)
    p = _params(con, fin)
    ejercicio = ejercicio_de_fecha_bd(con, fin)
    if trabajador["area"] != "GRANJA" or not trabajador["polivalente"] or \
            ejercicio < int(_param(p, "ejercicio_inicio_alimento_propio")):
        return {"pct_carne": Decimal("1"), "fuente": "NO_APLICA", "crianza": 0, "alimento": 0}
    horas = horas_periodo(con, int(trabajador["id"]), date(anio, mes, 1), fin)
    total = horas["crianza"] + horas["alimento"]
    if total > 0:
        pct = (Decimal(horas["crianza"]) / Decimal(total)).quantize(TASA, rounding=ROUND_HALF_UP)
        return {"pct_carne": pct, "fuente": "HOJAS_TIEMPO", **horas}
    return {"pct_carne": Decimal(_param(p, "prorrateo_mod_carne_defecto")), "fuente": "DEFECTO", **horas}


def listar_hojas(con: sqlite3.Connection, sesion: Sesion, personal_id: int | None = None, desde: Any = None,
                 hasta: Any = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    sql, args = "SELECT h.*, p.nombre FROM hojas_tiempo h JOIN personal p ON p.id = h.personal_id WHERE 1 = 1", []
    if personal_id is not None:
        sql += " AND h.personal_id = ?"
        args.append(personal_id)
    if desde is not None:
        sql += " AND h.fecha >= ?"
        args.append(a_fecha(desde).isoformat())
    if hasta is not None:
        sql += " AND h.fecha <= ?"
        args.append(a_fecha(hasta).isoformat())
    return [dict(f) for f in con.execute(sql + " ORDER BY h.fecha DESC", args)]


# ==========================================================================
# 4. PLANILLA MENSUAL (FIN-EGR-09) Y BENEFICIOS SOCIALES (FIN-EGR-10)
# ==========================================================================
def _dias_en_mes(trabajador: Mapping[str, Any], anio: int, mes: int, dias_mes: int) -> int:
    inicio, fin = date(anio, mes, 1), _fin_de_mes(anio, mes)
    desde = max(inicio, a_fecha(trabajador["fecha_ingreso"]))
    hasta = min(fin, a_fecha(trabajador["fecha_retiro"])) if trabajador["fecha_retiro"] else fin
    if hasta < desde:
        return 0
    if desde == inicio and hasta == fin:
        return dias_mes
    return min(dias_mes, (hasta - desde).days + 1)


def calcular_planilla(con: sqlite3.Connection, anio: int, mes: int, bonos: Mapping[int, Any] | None = None,
                      dias: Mapping[int, int] | None = None) -> dict[str, Any]:
    """Vista previa de la planilla del mes (no registra nada)."""
    _entero(anio, "año", 2000, 2100)
    _entero(mes, "mes", 1, 12)
    fin = _fin_de_mes(anio, mes)
    tasas = tasas_vigentes(con, fin)
    lineas = []
    for t in con.execute("SELECT * FROM personal WHERE fecha_ingreso <= ? AND (fecha_retiro IS NULL OR fecha_retiro >= ?) "
                         "ORDER BY area DESC, nombre", (fin.isoformat(), date(anio, mes, 1).isoformat())):
        n = (dias or {}).get(int(t["id"]), _dias_en_mes(t, anio, mes, tasas.dias_mes))
        if n == 0:
            continue
        salario = salario_vigente(con, int(t["id"]), min(fin.isoformat(), t["fecha_retiro"] or fin.isoformat()))
        calculo = calcular_linea(salario, (bonos or {}).get(int(t["id"]), 0), n, tasas)
        prorrateo = prorrateo_trabajador(con, t, anio, mes)
        lineas.append({"personal_id": int(t["id"]), "nombre": t["nombre"], "cargo": t["cargo"], "area": t["area"],
                       "calculo": calculo, **prorrateo})
    if not lineas:
        raise ReglaNegocioError("No hay personal activo en el mes.", codigo="PLANILLA_SIN_PERSONAL")
    totales = {k: sum((getattr(l["calculo"], k) for l in lineas), CERO)
               for k in ("total_ganado", "aporte_laboral", "liquido", "aporte_patronal", "aguinaldo", "indemnizacion")}
    return {"anio": anio, "mes": mes, "ejercicio": ejercicio_de_fecha_bd(con, fin), "tasas": tasas, "lineas": lineas,
            "totales": totales}


def preparar_planilla(con: sqlite3.Connection, sesion: Sesion, anio: int, mes: int,
                      bonos: Mapping[int, Any] | None = None, dias: Mapping[int, int] | None = None) -> int:
    """La Unidad de Finanzas confecciona la planilla del mes (FIN-EGR-09/10)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    calculo = calcular_planilla(con, anio, mes, bonos, dias)
    if con.execute("SELECT 1 FROM planillas WHERE anio = ? AND mes = ? AND estado <> 'ANULADA'", (anio, mes)).fetchone():
        raise ReglaNegocioError(f"Ya existe la planilla de {mes:02d}/{anio}.", codigo="PLANILLA_DUPLICADA")
    t = calculo["totales"]
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO planillas (anio, mes, ejercicio, total_ganado_cent, aporte_laboral_cent, liquido_cent,
                   aporte_patronal_cent, aguinaldo_cent, indemnizacion_cent, estado, elaborado_por, elaborado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (anio, mes, calculo["ejercicio"], a_centavos(t["total_ganado"]), a_centavos(t["aporte_laboral"]),
             a_centavos(t["liquido"]), a_centavos(t["aporte_patronal"]), a_centavos(t["aguinaldo"]),
             a_centavos(t["indemnizacion"]), "PREPARADA", sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        for l in calculo["lineas"]:
            c: CalculoLinea = l["calculo"]
            con.execute(
                """INSERT INTO planilla_lineas (planilla_id, personal_id, area, dias_trabajados, salario_mensual_cent,
                       haber_basico_cent, bonos_cent, total_ganado_cent, aporte_laboral_cent, liquido_cent,
                       aporte_patronal_cent, aguinaldo_cent, indemnizacion_cent, pct_carne, fuente_prorrateo,
                       horas_crianza, horas_alimento) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, l["personal_id"], l["area"], c.dias, a_centavos(c.salario_mensual), a_centavos(c.haber_basico),
                 a_centavos(c.bonos), a_centavos(c.total_ganado), a_centavos(c.aporte_laboral), a_centavos(c.liquido),
                 a_centavos(c.aporte_patronal), a_centavos(c.aguinaldo), a_centavos(c.indemnizacion),
                 str(l["pct_carne"]), l["fuente"], l["crianza"], l["alimento"]))
        auditoria.registrar(con, sesion, "planillas", "INSERT", pid,
                            despues={"periodo": f"{mes:02d}/{anio}", **t, "trabajadores": len(calculo["lineas"])})
    return pid


def _planilla(con: sqlite3.Connection, planilla_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM planillas WHERE id = ?", (_entero(planilla_id, "planilla", 1),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la planilla {planilla_id}.")
    return fila


def autorizar_planilla(con: sqlite3.Connection, sesion: Sesion, planilla_id: int) -> None:
    """La Gerencia General autoriza la planilla (bloque de firmas FIN-EGR-09)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "AUTORIZAR")
    with transaccion(con):
        p = _planilla(con, planilla_id)
        if p["estado"] != "PREPARADA":
            raise ReglaNegocioError(f"La planilla está {p['estado']}.", codigo="PLANILLA_NO_PREPARADA")
        if p["elaborado_por"] == sesion.nombre_usuario:
            raise AutorizacionError("Quien elabora la planilla no la autoriza (CI-FIN-10).", codigo="AUTOFIRMA")
        con.execute("UPDATE planillas SET estado = 'AUTORIZADA', autorizado_por = ?, autorizado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), planilla_id))
        auditoria.registrar(con, sesion, "planillas", "AUTORIZACION", planilla_id, antes={"estado": "PREPARADA"},
                            despues={"estado": "AUTORIZADA"})


def anular_planilla(con: sqlite3.Connection, sesion: Sesion, planilla_id: int, motivo: str) -> None:
    """Anula una planilla no contabilizada (p. ej. bono mal digitado)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    causa = _texto(motivo, "motivo")
    with transaccion(con):
        p = _planilla(con, planilla_id)
        if p["estado"] not in ("PREPARADA", "AUTORIZADA"):
            raise ReglaNegocioError("Una planilla contabilizada no se anula: reverse su asiento.",
                                    codigo="PLANILLA_CONTABILIZADA")
        con.execute("UPDATE planillas SET estado = 'ANULADA', motivo_anulacion = ? WHERE id = ?", (causa, planilla_id))
        auditoria.registrar(con, sesion, "planillas", "UPDATE", planilla_id, antes={"estado": p["estado"]},
                            despues={"estado": "ANULADA"}, detalle=causa)


def _partir(monto: Decimal, pct: Decimal) -> tuple[Decimal, Decimal]:
    carne = redondear_bs(monto * pct)
    return carne, monto - carne


def lineas_asiento_planilla(con: sqlite3.Connection, planilla_id: int) -> list[dict[str, Any]]:
    """Líneas del asiento de devengo: costo por sufijo (granja, con prorrateo)
    o gasto administrativo 7.1.01.01-04, contra 2.1.02.01, 2.1.02.05,
    2.1.02.02, 2.1.02.03 y 2.2.01.01. El aporte laboral es retención: se
    acredita a 2.1.02.05 dentro del total ganado (no suma costo)."""
    debe: dict[tuple[str, str | None], Decimal] = {}

    def sumar(cuenta: str, sufijo: str | None, monto: Decimal) -> None:
        if monto > 0:
            debe[(cuenta, sufijo)] = debe.get((cuenta, sufijo), CERO) + monto

    for l in con.execute("SELECT * FROM planilla_lineas WHERE planilla_id = ?", (planilla_id,)):
        montos = {"salario": de_centavos(int(l["total_ganado_cent"])),
                  "aguinaldo": de_centavos(int(l["aguinaldo_cent"])),
                  "patronal": de_centavos(int(l["aporte_patronal_cent"])),
                  "indemnizacion": de_centavos(int(l["indemnizacion_cent"]))}
        for concepto, monto in montos.items():
            if l["area"] == "ADMINISTRACION":
                sumar(GASTO_ADMIN[concepto], None, monto)
            else:
                carne, alimento = _partir(monto, Decimal(l["pct_carne"]))
                sumar(CENTRO_CARNE, SUFIJO[concepto], carne)
                sumar(CENTRO_ALIMENTO, SUFIJO[concepto], alimento)
    p = _planilla(con, planilla_id)
    lineas = [{"cuenta": c, "debe": m, "centro_costo": c if s else None, "sufijo": s} for (c, s), m in debe.items()]
    for cuenta, campo in ((SUELDOS_POR_PAGAR, "liquido_cent"), (LABORAL_POR_PAGAR, "aporte_laboral_cent"),
                          (AGUINALDO_POR_PAGAR, "aguinaldo_cent"), (PATRONAL_POR_PAGAR, "aporte_patronal_cent"),
                          (PROVISION_INDEMNIZACION, "indemnizacion_cent")):
        if int(p[campo]) > 0:
            lineas.append({"cuenta": cuenta, "haber": de_centavos(int(p[campo]))})
    return lineas


def contabilizar_planilla(con: sqlite3.Connection, sesion: Sesion, planilla_id: int) -> int:
    """Asiento de devengo al último día del mes (Comprobante Diario de Planillas)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    with transaccion(con):
        p = _planilla(con, planilla_id)
        if p["estado"] != "AUTORIZADA":
            raise AutorizacionError("La planilla debe estar autorizada por la Gerencia General.",
                                    codigo="PLANILLA_SIN_AUTORIZACION")
        fin = _fin_de_mes(int(p["anio"]), int(p["mes"]))
        asiento = contable.crear_asiento(
            con, sesion, fin, f"Planilla de sueldos y beneficios sociales {int(p['mes']):02d}/{p['anio']} "
                              "(FIN-EGR-09/10)", lineas_asiento_planilla(con, planilla_id),
            "PLANILLAS", f"FIN-EGR-09 {int(p['mes']):02d}/{p['anio']}", tipo="TRASPASO")
        con.execute("UPDATE planillas SET estado = 'CONTABILIZADA', asiento_id = ?, contabilizado_en = ? WHERE id = ?",
                    (asiento.id, _ahora_iso(), planilla_id))
        auditoria.registrar(con, sesion, "planillas", "UPDATE", planilla_id, antes={"estado": "AUTORIZADA"},
                            despues={"estado": "CONTABILIZADA", "asiento_id": asiento.id})
    return asiento.id


def vencimientos_planilla(con: sqlite3.Connection, anio: int, mes: int) -> dict[str, date]:
    """Vencimientos legales del pago de sueldos y de aportes (parámetros)."""
    fin = _fin_de_mes(anio, mes)
    p = _params(con, fin)
    sueldos = fin + timedelta(days=int(_param(p, "sueldos_pago_dias_despues_mes")))
    sig_anio, sig_mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    if bool(_param(p, "aportes_pago_ultimo_dia_habil_mes_siguiente")):
        aportes = ultimo_dia_habil(sig_anio, sig_mes)
    else:
        aportes = _fin_de_mes(sig_anio, sig_mes)
    return {"sueldos": sueldos, "aportes": aportes}


def pagar_sueldos(con: sqlite3.Connection, sesion: Sesion, planilla_id: int, fecha: Any, cuenta_banco: str, *,
                  autorizacion_id: int | None = None, nro_cheque: str | None = None) -> int:
    """Pago del líquido (2.1.02.01) por transferencia, con la autorización
    CI-FIN-09 de la GG (y mancomunada sobre Bs 5.000). Nivel 1 de POL-04."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    with transaccion(con):
        p = _planilla(con, planilla_id)
        if p["estado"] != "CONTABILIZADA":
            raise ReglaNegocioError("Primero contabilice la planilla.", codigo="PLANILLA_NO_CONTABILIZADA")
        if p["pago_sueldos_mov_id"]:
            raise ReglaNegocioError("Los sueldos de esta planilla ya se pagaron.", codigo="SUELDOS_PAGADOS")
        mid = tesoreria.registrar_pago(
            con, sesion, fecha, de_centavos(int(p["liquido_cent"])), cuenta_banco, SUELDOS_POR_PAGAR,
            f"Pago de sueldos {int(p['mes']):02d}/{p['anio']}", "Personal fijo (planilla FIN-EGR-09)",
            autorizacion_id=autorizacion_id, nro_cheque=nro_cheque, documento_ref=f"Planilla {planilla_id}",
            categoria_pago="PLANILLA_APORTES")
        con.execute("UPDATE planillas SET pago_sueldos_mov_id = ?, sueldos_pagados_en = ? WHERE id = ?",
                    (mid, a_fecha(fecha).isoformat(), planilla_id))
        auditoria.registrar(con, sesion, "planillas", "UPDATE", planilla_id, antes={"pago_sueldos_mov_id": None},
                            despues={"pago_sueldos_mov_id": mid})
    return mid


def pagar_aportes(con: sqlite3.Connection, sesion: Sesion, planilla_id: int, fecha: Any, cuenta_banco: str, *,
                  autorizacion_id: int | None = None) -> int:
    """Pago a la Gestora Pública y la CNS: aporte laboral retenido (2.1.02.05)
    + aporte patronal (2.1.02.03), en un solo desembolso."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    with transaccion(con):
        p = _planilla(con, planilla_id)
        if p["estado"] != "CONTABILIZADA":
            raise ReglaNegocioError("Primero contabilice la planilla.", codigo="PLANILLA_NO_CONTABILIZADA")
        if p["pago_aportes_mov_id"]:
            raise ReglaNegocioError("Los aportes de esta planilla ya se pagaron.", codigo="APORTES_PAGADOS")
        mid = tesoreria.registrar_pago_compuesto(
            con, sesion, fecha, cuenta_banco,
            [(LABORAL_POR_PAGAR, de_centavos(int(p["aporte_laboral_cent"]))),
             (PATRONAL_POR_PAGAR, de_centavos(int(p["aporte_patronal_cent"])))],
            f"Aportes laborales y patronales {int(p['mes']):02d}/{p['anio']}", "Gestora Pública y CNS",
            autorizacion_id=autorizacion_id, documento_ref=f"Aportes planilla {planilla_id}",
            categoria_pago="PLANILLA_APORTES")
        con.execute("UPDATE planillas SET pago_aportes_mov_id = ?, aportes_pagados_en = ? WHERE id = ?",
                    (mid, a_fecha(fecha).isoformat(), planilla_id))
        auditoria.registrar(con, sesion, "planillas", "UPDATE", planilla_id, antes={"pago_aportes_mov_id": None},
                            despues={"pago_aportes_mov_id": mid})
    return mid


def obtener_planilla(con: sqlite3.Connection, sesion: Sesion, planilla_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    p = _planilla(con, planilla_id)
    lineas = []
    for l in con.execute("""SELECT l.*, p.nombre, p.cargo, p.ci FROM planilla_lineas l JOIN personal p
                              ON p.id = l.personal_id WHERE l.planilla_id = ? ORDER BY l.area DESC, p.nombre""",
                         (planilla_id,)):
        d = dict(l)
        for k in list(d):
            if k.endswith("_cent"):
                d[k[:-5]] = de_centavos(int(d[k]))
        lineas.append(d)
    datos = {**dict(p), "lineas": lineas, "vencimientos": vencimientos_planilla(con, int(p["anio"]), int(p["mes"]))}
    for k in list(datos):
        if k.endswith("_cent") and datos[k] is not None:
            datos[k[:-5]] = de_centavos(int(datos[k]))
    return datos


def listar_planillas(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    return [{**dict(f), "total_ganado": de_centavos(int(f["total_ganado_cent"])),
             "liquido": de_centavos(int(f["liquido_cent"]))}
            for f in con.execute("SELECT * FROM planillas ORDER BY anio DESC, mes DESC, id DESC")]


# ==========================================================================
# 5. CONCILIACIÓN PAGADO VS ABSORBIDO (FIN-EGR-13) Y CI-FIN-14
# ==========================================================================
def conciliar_mano_obra(con: sqlite3.Connection, sesion: Sesion, planilla_id: int,
                        observacion_ci_fin_14: str | None = None) -> dict[str, Any]:
    """Costo de planilla del personal de granja (total ganado + patronal +
    aguinaldo + indemnización) frente a lo absorbido por los centros de costo
    con los sufijos 20, 21, 38 y 39 en el asiento de la planilla."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    p = _planilla(con, planilla_id)
    if p["estado"] != "CONTABILIZADA":
        raise ReglaNegocioError("Solo se concilia una planilla contabilizada.", codigo="PLANILLA_NO_CONTABILIZADA")
    if con.execute("SELECT 1 FROM conciliaciones_mod WHERE planilla_id = ?", (planilla_id,)).fetchone():
        raise ReglaNegocioError("La planilla ya tiene su conciliación FIN-EGR-13.", codigo="CONCILIACION_EXISTE")
    pagado = int(con.execute(
        "SELECT COALESCE(SUM(total_ganado_cent + aporte_patronal_cent + aguinaldo_cent + indemnizacion_cent), 0) "
        "FROM planilla_lineas WHERE planilla_id = ? AND area = 'GRANJA'", (planilla_id,)).fetchone()[0])
    sufijos = tuple(SUFIJO[k] for k in ("salario", "aguinaldo", "patronal", "indemnizacion"))
    absorbido = {c: int(con.execute(
        f"SELECT COALESCE(SUM(debe_cent - haber_cent), 0) FROM asiento_detalle WHERE asiento_id = ? AND cuenta_codigo = ? "
        f"AND sufijo IN ({','.join('?' * len(sufijos))})", (int(p["asiento_id"]), c, *sufijos)).fetchone()[0])
        for c in (CENTRO_CARNE, CENTRO_ALIMENTO)}
    diferencia = pagado - absorbido[CENTRO_CARNE] - absorbido[CENTRO_ALIMENTO]
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO conciliaciones_mod (planilla_id, costo_pagado_cent, absorbido_carne_cent,
                   absorbido_alimento_cent, diferencia_cent, observacion_ci_fin_14, elaborado_por, elaborado_en)
               VALUES (?,?,?,?,?,?,?,?)""",
            (planilla_id, pagado, absorbido[CENTRO_CARNE], absorbido[CENTRO_ALIMENTO], diferencia,
             _opcional(observacion_ci_fin_14, "observación CI-FIN-14", 500), sesion.nombre_usuario, _ahora_iso()))
        cid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "conciliaciones_mod", "INSERT", cid,
                            despues={"planilla_id": planilla_id, "pagado": de_centavos(pagado),
                                     "carne": de_centavos(absorbido[CENTRO_CARNE]),
                                     "alimento": de_centavos(absorbido[CENTRO_ALIMENTO]),
                                     "diferencia": de_centavos(diferencia)})
    return {"id": cid, "pagado": de_centavos(pagado), "carne": de_centavos(absorbido[CENTRO_CARNE]),
            "alimento": de_centavos(absorbido[CENTRO_ALIMENTO]), "diferencia": de_centavos(diferencia)}


def fiscalizar_conciliacion(con: sqlite3.Connection, sesion: Sesion, conciliacion_id: int) -> None:
    """Fiscalización del Consejo de Vigilancia sobre el FIN-EGR-13."""
    seguridad.exigir_permiso(con, sesion, "planillas", "VERIFICAR")
    with transaccion(con):
        fila = con.execute("SELECT * FROM conciliaciones_mod WHERE id = ?", (conciliacion_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la conciliación {conciliacion_id}.")
        if fila["fiscalizado_por"]:
            raise ReglaNegocioError("La conciliación ya fue fiscalizada.", codigo="YA_FISCALIZADA")
        con.execute("UPDATE conciliaciones_mod SET fiscalizado_por = ?, fiscalizado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), conciliacion_id))
        auditoria.registrar(con, sesion, "conciliaciones_mod", "AUTORIZACION", conciliacion_id,
                            despues={"fiscalizado_por": sesion.nombre_usuario})


def listar_conciliaciones_mod(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    filas = []
    for f in con.execute("SELECT c.*, p.anio, p.mes FROM conciliaciones_mod c JOIN planillas p ON p.id = c.planilla_id "
                         "ORDER BY p.anio DESC, p.mes DESC"):
        d = dict(f)
        for k in ("costo_pagado", "absorbido_carne", "absorbido_alimento", "diferencia"):
            d[k] = de_centavos(int(f[f"{k}_cent"]))
        filas.append(d)
    return filas


def semanas_con_horas(con: sqlite3.Connection, desde: Any, hasta: Any) -> list[dict[str, Any]]:
    """Semanas (lunes) con horas de planta de alimento en el periodo, ordenadas
    por mayor proporción hacia el alimento (prioridad de muestreo CI-FIN-14)."""
    d, h = a_fecha(desde), a_fecha(hasta)
    semanas: dict[date, list[int]] = {}
    for f in con.execute("SELECT fecha, horas_crianza, horas_alimento FROM hojas_tiempo WHERE fecha BETWEEN ? AND ?",
                         (d.isoformat(), h.isoformat())):
        dia = a_fecha(f["fecha"])
        lunes = dia - timedelta(days=dia.weekday())
        acumulado = semanas.setdefault(lunes, [0, 0])
        acumulado[0] += int(f["horas_crianza"])
        acumulado[1] += int(f["horas_alimento"])
    filas = [{"semana_inicio": k.isoformat(), "horas_crianza": v[0], "horas_alimento": v[1],
              "pct_alimento": (Decimal(v[1]) / Decimal(v[0] + v[1])).quantize(TASA) if v[0] + v[1] else Decimal(0)}
             for k, v in semanas.items() if v[1] > 0]
    return sorted(filas, key=lambda x: (-x["pct_alimento"], x["semana_inicio"]))


def registrar_verificacion_horas(
    con: sqlite3.Connection, sesion: Sesion, desde: Any, hasta: Any, criterio_min_horas_qq: Any,
    criterio_max_horas_qq: Any, criterio_detalle: str, quintales_por_semana: Mapping[str, Any],
) -> dict[str, Any]:
    """Verificación cruzada semestral del Consejo de Vigilancia (CI-FIN-14):
    muestra mínima del 20 % de las semanas con horas de planta; compara las
    horas declaradas con los quintales ensacados (bitácora de ensacado) según
    el criterio de razonabilidad documentado; marca las semanas fuera de rango."""
    seguridad.exigir_permiso(con, sesion, "planillas", "VERIFICAR")
    d, h = a_fecha(desde), a_fecha(hasta)
    minimo = a_decimal(criterio_min_horas_qq, "criterio mínimo")
    maximo = a_decimal(criterio_max_horas_qq, "criterio máximo")
    if minimo <= 0 or maximo < minimo:
        raise DatosInvalidosError("El criterio de razonabilidad horas/quintal debe ser un rango positivo.")
    criterio = _texto(criterio_detalle, "criterio documentado", 500)
    semanas = {s["semana_inicio"]: s for s in semanas_con_horas(con, d, h)}
    if not semanas:
        raise ReglaNegocioError("No hay hojas de tiempo con horas de planta en el periodo.", codigo="SIN_HOJAS")
    p = _params(con, h)
    requeridas = math.ceil(len(semanas) * Decimal(_param(p, "ci_fin_14_muestra_min")))
    muestra = {a_fecha(k).isoformat(): v for k, v in quintales_por_semana.items()}
    ajenas = [k for k in muestra if k not in semanas]
    if ajenas:
        raise DatosInvalidosError(f"Semanas sin horas de planta en el periodo: {', '.join(ajenas)}.")
    if len(muestra) < requeridas:
        raise ReglaNegocioError(f"CI-FIN-14: la muestra debe cubrir al menos {requeridas} de {len(semanas)} semanas "
                                "(20 %).", codigo="MUESTRA_INSUFICIENTE")
    resultados = []
    for semana, qq in muestra.items():
        horas = semanas[semana]["horas_alimento"]
        quintales = inventarios.a_mil(qq, "quintales ensacados", permitir_cero=True)
        if quintales == 0:
            ratio, observada, detalle = None, True, "Horas declaradas sin quintales ensacados."
        else:
            ratio = (Decimal(horas) * 1000 / Decimal(quintales)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            observada = not (minimo <= ratio <= maximo)
            detalle = (f"{ratio} h/qq fuera del rango {minimo}-{maximo}" if observada else None)
        resultados.append((semana, horas, quintales, str(ratio) if ratio is not None else None, int(observada), detalle))
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO verificaciones_horas (desde, hasta, criterio_min_horas_qq, criterio_max_horas_qq,
                   criterio_detalle, semanas_periodo, semanas_muestra, observaciones, verificado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (d.isoformat(), h.isoformat(), str(minimo), str(maximo), criterio, len(semanas), len(muestra),
             sum(r[4] for r in resultados), sesion.nombre_usuario, _ahora_iso()))
        vid = int(cur.lastrowid)
        for r in resultados:
            con.execute("INSERT INTO verificacion_semanas (verificacion_id, semana_inicio, horas_alimento, quintales_mil, "
                        "horas_por_qq, observada, detalle) VALUES (?,?,?,?,?,?,?)", (vid, *r))
        auditoria.registrar(con, sesion, "verificaciones_horas", "INSERT", vid,
                            despues={"periodo": f"{d.isoformat()} a {h.isoformat()}", "muestra": len(muestra),
                                     "observaciones": sum(r[4] for r in resultados)})
    return {"id": vid, "semanas_periodo": len(semanas), "muestra": len(muestra), "requeridas": requeridas,
            "observadas": [r[0] for r in resultados if r[4]]}


def listar_verificaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    return [dict(f) for f in con.execute("SELECT * FROM verificaciones_horas ORDER BY id DESC")]


# ==========================================================================
# 6. PERSONAL EVENTUAL (PR-FIN-04)
# ==========================================================================
def emitir_circular(con: sqlite3.Connection, sesion: Sesion, fecha_emision: Any, fecha_faenado: Any, lote: str,
                    aves: int, mortalidad_pct: Any) -> int:
    """La Unidad Técnica avisa el cierre del ciclo con la antelación mínima
    (CIR-EGR-01; 7 días)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "CREAR")
    if sesion.rol != "UT":
        raise AutorizacionError("La circular de cierre la emite la Unidad Técnica (CIR-EGR-01).", codigo="SIN_PERMISO")
    emision, faenado = a_fecha(fecha_emision), a_fecha(fecha_faenado)
    _entero(aves, "aves", 1)
    mortalidad = a_decimal(mortalidad_pct, "mortalidad acumulada")
    if mortalidad < 0 or mortalidad >= 100:
        raise DatosInvalidosError("La mortalidad acumulada debe estar entre 0 y 100 %.")
    antelacion = int(_param(_params(con, emision), "circular_cierre_dias_antelacion"))
    if (faenado - emision).days < antelacion:
        raise ReglaNegocioError(
            f"La circular debe emitirse con al menos {antelacion} días de antelación al faenado: del "
            f"{fecha_pantalla(emision)} al {fecha_pantalla(faenado)} hay {(faenado - emision).days}.",
            codigo="CIRCULAR_SIN_ANTELACION")
    numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) + 1 FROM circulares_cierre WHERE anio = ?",
                             (emision.year,)).fetchone()[0])
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO circulares_cierre (numero, anio, fecha_emision, fecha_faenado, lote, aves, mortalidad_pct,
                   emitida_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (numero, emision.year, emision.isoformat(), faenado.isoformat(), _texto(lote, "lote", 40), aves,
             str(mortalidad), sesion.nombre_usuario, _ahora_iso()))
        cid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "circulares_cierre", "INSERT", cid,
                            despues={"numero": f"CIR-EGR-01/{numero}/{emision.year}", "faenado": faenado.isoformat(),
                                     "aves": aves})
    return cid


def acusar_circular(con: sqlite3.Connection, sesion: Sesion, circular_id: int) -> None:
    """Acuse de recibo de la Gerencia General o de Finanzas."""
    if sesion.rol not in ("GG", "JF"):
        raise AutorizacionError("Acusan recibo la Gerencia General y la Unidad de Finanzas.", codigo="SIN_PERMISO")
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    campo = "acuse_gg" if sesion.rol == "GG" else "acuse_jf"
    with transaccion(con):
        c = con.execute("SELECT * FROM circulares_cierre WHERE id = ?", (circular_id,)).fetchone()
        if c is None:
            raise DatosInvalidosError(f"No existe la circular {circular_id}.")
        if c[f"{campo}_por"]:
            raise ReglaNegocioError("El acuse de recibo ya está registrado.", codigo="ACUSE_DUPLICADO")
        con.execute(f"UPDATE circulares_cierre SET {campo}_por = ?, {campo}_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), circular_id))
        auditoria.registrar(con, sesion, "circulares_cierre", "UPDATE", circular_id, despues={f"{campo}_por":
                                                                                              sesion.nombre_usuario})


def disponibilidad_eventual(con: sqlite3.Connection, fecha: Any, monto: Any = 0,
                            excluir: int | None = None) -> dict[str, Any]:
    """Saldo de la partida de personal eventual del ciclo (FIN-EGR-02)."""
    ciclo = inventarios.ciclo_de_fecha(con, fecha)
    partida = con.execute("SELECT * FROM partidas_presupuesto WHERE ejercicio = ? AND ciclo = ? AND cuenta_inventario = ?",
                          (ciclo["ejercicio"], ciclo["ciclo"], EVENTUAL_POR_PAGAR)).fetchone()
    aprobado = de_centavos(int(partida["monto_cent"])) if partida else CERO
    ejecutado = 0
    for a in con.execute("SELECT a.*, l.total_cent AS liquidado FROM autorizaciones_eventuales a LEFT JOIN "
                         "liquidaciones_eventuales l ON l.autorizacion_eventual_id = a.id WHERE a.ejercicio = ? "
                         "AND a.ciclo = ? AND a.estado <> 'ANULADA'", (ciclo["ejercicio"], ciclo["ciclo"])):
        if excluir is not None and int(a["id"]) == excluir:
            continue
        ejecutado += int(a["liquidado"]) if a["liquidado"] is not None else int(a["presupuesto_cent"])
    saldo = aprobado - de_centavos(ejecutado)
    pedido = redondear_bs(monto)
    return {"ejercicio": ciclo["ejercicio"], "ciclo": ciclo["ciclo"], "partida": "Personal eventual de faenado",
            "aprobado": aprobado, "ejecutado": de_centavos(ejecutado), "saldo": saldo, "solicitado": pedido,
            "habilita": partida is not None and saldo >= pedido}


def solicitar_contratacion(con: sqlite3.Connection, sesion: Sesion, circular_id: int, fecha_lunes: Any,
                           trabajadores: int) -> dict[str, Any]:
    """Finanzas verifica la partida, registra el compromiso de caja
    (``validar_compromiso``) y solicita la autorización de la GG para el
    lunes de la semana de faenado (FIN-EGR-06)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    lunes = a_fecha(fecha_lunes)
    _entero(trabajadores, "trabajadores", 1)
    p = _params(con, lunes)
    dia = int(_param(p, "dia_semana_contratacion_eventual"))
    if lunes.isoweekday() != dia:
        raise ReglaNegocioError(f"La contratación de personal eventual se autoriza el {_dia(dia)} (POL-06 Art. 2).",
                                codigo="EVENTUAL_DIA_NO_PERMITIDO")
    circular = con.execute("SELECT * FROM circulares_cierre WHERE id = ?", (circular_id,)).fetchone()
    if circular is None:
        raise DatosInvalidosError(f"No existe la circular {circular_id}.")
    if not (circular["acuse_gg_por"] and circular["acuse_jf_por"]):
        raise ReglaNegocioError("Falta el acuse de recibo de la circular (Gerencia General y Finanzas).",
                                codigo="CIRCULAR_SIN_ACUSE")
    faenado = a_fecha(circular["fecha_faenado"])
    if not (lunes <= faenado < lunes + timedelta(days=7)):
        raise ReglaNegocioError("La autorización corresponde al lunes de la semana del faenado "
                                f"({fecha_pantalla(faenado)}).", codigo="LUNES_INCORRECTO")
    if con.execute("SELECT 1 FROM autorizaciones_eventuales WHERE circular_id = ?", (circular_id,)).fetchone():
        raise ReglaNegocioError("La circular ya tiene una solicitud de contratación.", codigo="SOLICITUD_EXISTE")
    aves = int(circular["aves"])
    tarifa = redondear_bs(_param(p, "tarifa_eventual_por_ave"))
    presupuesto = costo_eventual(con, aves, lunes)
    disp = disponibilidad_eventual(con, faenado, presupuesto)
    if not disp["habilita"]:
        raise ReglaNegocioError(
            f"FIN-EGR-02: la partida de personal eventual tiene saldo {formato_bs(disp['saldo'])} y se requieren "
            f"{formato_bs(presupuesto)}.", codigo="PRESUPUESTO_INSUFICIENTE", detalle=disp)
    with transaccion(con):
        compromiso_id, evaluacion, _ = tesoreria.registrar_compromiso(
            con, sesion, presupuesto, "PERSONAL_EVENTUAL", f"Faenado lote {circular['lote']} ({aves} aves)", lunes)
        aut = seguridad.solicitar_autorizacion(
            con, sesion, TipoDecision.CONTRATACION_EVENTUAL, presupuesto,
            f"Contratación de {trabajadores} eventuales para el faenado del lote {circular['lote']}",
            seguridad.contexto_desde_bd(con, lunes), referencia=f"CIR-EGR-01/{circular['numero']}/{circular['anio']}")
        cur = con.execute(
            """INSERT INTO autorizaciones_eventuales (circular_id, fecha_lunes, trabajadores, aves, tarifa_cent,
                   presupuesto_cent, ejercicio, ciclo, disponibilidad_json, compromiso_id, autorizacion_id, estado,
                   solicitado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (circular_id, lunes.isoformat(), trabajadores, aves, a_centavos(tarifa), a_centavos(presupuesto),
             disp["ejercicio"], disp["ciclo"], auditoria.a_json(disp), compromiso_id, aut, "SOLICITADA",
             sesion.nombre_usuario, _ahora_iso()))
        eid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "autorizaciones_eventuales", "INSERT", eid,
                            despues={"circular_id": circular_id, "trabajadores": trabajadores, "aves": aves,
                                     "presupuesto": presupuesto, "suspendido_sme": evaluacion.suspendido},
                            detalle=evaluacion.mensaje)
    return {"id": eid, "presupuesto": presupuesto, "autorizacion_id": aut, "compromiso_id": compromiso_id,
            "suspendida": evaluacion.suspendido, "mensaje_sme": evaluacion.mensaje}


def _eventual(con: sqlite3.Connection, eid: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM autorizaciones_eventuales WHERE id = ?", (eid,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la autorización de personal eventual {eid}.")
    return fila


def autorizar_contratacion(con: sqlite3.Connection, sesion: Sesion, autorizacion_eventual_id: int, fecha: Any,
                           suplencia: seguridad.Suplencia | None = None) -> str:
    """La GG (o su suplente con constancia, CI-FIN-17) autoriza el lunes
    correspondiente. La matriz CI-FIN-09 rechaza la firma en otro día.

    Returns:
        Estado resultante (AUTORIZADA o SOLICITADA si falta otra firma).
    """
    f = a_fecha(fecha)
    with transaccion(con):
        ev = _eventual(con, autorizacion_eventual_id)
        if ev["estado"] != "SOLICITADA":
            raise ReglaNegocioError(f"La solicitud está {ev['estado']}.", codigo="SOLICITUD_NO_PENDIENTE")
        if f.isoformat() != ev["fecha_lunes"]:
            raise ReglaNegocioError(f"La contratación se autoriza el lunes {fecha_pantalla(ev['fecha_lunes'])} "
                                    "(POL-06 Art. 2).", codigo="EVENTUAL_DIA_NO_PERMITIDO")
        compromiso = con.execute("SELECT estado, autorizacion_id FROM compromisos WHERE id = ?",
                                 (ev["compromiso_id"],)).fetchone()
        if compromiso is not None and compromiso["estado"] == "SUSPENDIDO":
            raise AutorizacionError("El compromiso de caja está suspendido por el SME: la GG firma la autorización "
                                    f"escrita n.º {compromiso['autorizacion_id']} y Finanzas la confirma.",
                                    codigo="COMPROMISO_SUSPENDIDO")
        estado = seguridad.firmar_autorizacion(con, sesion, int(ev["autorizacion_id"]), suplencia=suplencia,
                                               ahora=datetime.combine(f, datetime.min.time()).replace(hour=8))
        if estado == "AUTORIZADA":
            seguridad.consumir_autorizacion(con, sesion, int(ev["autorizacion_id"]), "autorizaciones_eventuales",
                                            autorizacion_eventual_id, tipo_decision=TipoDecision.CONTRATACION_EVENTUAL,
                                            monto=de_centavos(int(ev["presupuesto_cent"])))
            con.execute("UPDATE autorizaciones_eventuales SET estado = 'AUTORIZADA', autorizado_por = ?, "
                        "por_suplencia = ?, actualizado_en = ? WHERE id = ?",
                        (sesion.nombre_usuario, int(suplencia is not None), _ahora_iso(), autorizacion_eventual_id))
            auditoria.registrar(con, sesion, "autorizaciones_eventuales", "AUTORIZACION", autorizacion_eventual_id,
                                antes={"estado": "SOLICITADA"},
                                despues={"estado": "AUTORIZADA", "suplencia": suplencia is not None})
            return "AUTORIZADA"
        return "SOLICITADA"


def registrar_contrato(con: sqlite3.Connection, sesion: Sesion, autorizacion_eventual_id: int, nombre: str, ci: str,
                       fecha: Any) -> int:
    """Contrato por obra o servicio determinado (CONT-EGR-01; Ley 356 Art. 18)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    with transaccion(con):
        ev = _eventual(con, autorizacion_eventual_id)
        if ev["estado"] != "AUTORIZADA":
            raise AutorizacionError("Sin autorización FIN-EGR-06 no se contrata personal eventual.",
                                    codigo="EVENTUAL_SIN_AUTORIZACION")
        actuales = con.execute("SELECT COUNT(*) FROM contratos_eventuales WHERE autorizacion_eventual_id = ?",
                               (autorizacion_eventual_id,)).fetchone()[0]
        if actuales >= int(ev["trabajadores"]):
            raise ReglaNegocioError(f"Ya se contrataron los {ev['trabajadores']} trabajadores autorizados.",
                                    codigo="CUPO_COMPLETO")
        documento = _texto(ci, "C.I.", 20)
        if con.execute("SELECT 1 FROM contratos_eventuales WHERE autorizacion_eventual_id = ? AND ci = ?",
                       (autorizacion_eventual_id, documento)).fetchone():
            raise DatosInvalidosError("Ese trabajador ya tiene contrato para este faenado.", codigo="CONTRATO_DUPLICADO")
        cur = con.execute("INSERT INTO contratos_eventuales (autorizacion_eventual_id, nombre, ci, fecha, registrado_por, "
                          "registrado_en) VALUES (?,?,?,?,?,?)",
                          (autorizacion_eventual_id, _texto(nombre, "nombre", 120), documento, a_fecha(fecha).isoformat(),
                           sesion.nombre_usuario, _ahora_iso()))
        auditoria.registrar(con, sesion, "contratos_eventuales", "INSERT", cur.lastrowid,
                            despues={"autorizacion_eventual_id": autorizacion_eventual_id, "nombre": nombre, "ci": ci})
    return int(cur.lastrowid)


def semaforo_dpf(con: sqlite3.Connection, fecha: Any, dpf_pct: Decimal) -> str:
    """Bueno ≤ 100 %; Normal hasta 105 %; Malo > 105 % (parámetros)."""
    p = _params(con, fecha)
    if dpf_pct <= Decimal(_param(p, "dpf_umbral_bueno")) * 100:
        return "BUENO"
    if dpf_pct <= Decimal(_param(p, "dpf_umbral_malo")) * 100:
        return "NORMAL"
    return "MALO"


def liquidar_jornales(con: sqlite3.Connection, sesion: Sesion, autorizacion_eventual_id: int, fecha: Any,
                      unidades_por_contrato: Mapping[int, int]) -> dict[str, Any]:
    """Liquidación por destajo (FIN-EGR-08): unidades × tarifa, DPF = egresos
    reales / presupuesto de faena × 100 y asiento al sufijo 22 contra 2.1.02.04."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    f = a_fecha(fecha)
    with transaccion(con):
        ev = _eventual(con, autorizacion_eventual_id)
        if ev["estado"] != "AUTORIZADA":
            raise ReglaNegocioError(f"La autorización está {ev['estado']}.", codigo="EVENTUAL_NO_AUTORIZADA")
        contratos = {int(c["id"]): c for c in con.execute(
            "SELECT * FROM contratos_eventuales WHERE autorizacion_eventual_id = ?", (autorizacion_eventual_id,))}
        if not contratos:
            raise ReglaNegocioError("No hay contratos CONT-EGR-01 registrados.", codigo="SIN_CONTRATOS")
        ajenos = [k for k in unidades_por_contrato if k not in contratos]
        if ajenos:
            raise DatosInvalidosError(f"Contratos ajenos a esta autorización: {ajenos}.")
        tarifa = de_centavos(int(ev["tarifa_cent"]))
        lineas = []
        for cid, unidades in unidades_por_contrato.items():
            _entero(unidades, "unidades faenadas", 0)
            lineas.append((cid, unidades, redondear_bs(Decimal(unidades) * tarifa)))
        unidades_total = sum(x[1] for x in lineas)
        total = sum((x[2] for x in lineas), CERO)
        if total <= 0:
            raise DatosInvalidosError("La liquidación debe tener unidades faenadas.")
        presupuesto = de_centavos(int(ev["presupuesto_cent"]))
        dpf = (total * 100 / presupuesto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        semaforo = semaforo_dpf(con, f, dpf)
        asiento = contable.crear_asiento(
            con, sesion, f, f"Jornales de faenado — FIN-EGR-08 ({unidades_total} aves × {formato_bs(tarifa)})",
            [{"cuenta": CENTRO_CARNE, "debe": total, "centro_costo": CENTRO_CARNE, "sufijo": SUFIJO["eventual"]},
             {"cuenta": EVENTUAL_POR_PAGAR, "haber": total}], "PLANILLAS", f"FIN-EGR-08 aut. {autorizacion_eventual_id}",
            tipo="TRASPASO")
        cur = con.execute(
            """INSERT INTO liquidaciones_eventuales (autorizacion_eventual_id, fecha, unidades, total_cent, presupuesto_cent,
                   dpf, semaforo, asiento_id, liquidado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (autorizacion_eventual_id, f.isoformat(), unidades_total, a_centavos(total), a_centavos(presupuesto), str(dpf),
             semaforo, asiento.id, sesion.nombre_usuario, _ahora_iso()))
        lid = int(cur.lastrowid)
        for cid, unidades, monto in lineas:
            con.execute("INSERT INTO liquidacion_eventual_lineas (liquidacion_id, contrato_id, unidades, tarifa_cent, "
                        "total_cent) VALUES (?,?,?,?,?)", (lid, cid, unidades, a_centavos(tarifa), a_centavos(monto)))
        con.execute("UPDATE autorizaciones_eventuales SET estado = 'LIQUIDADA', actualizado_en = ? WHERE id = ?",
                    (_ahora_iso(), autorizacion_eventual_id))
        auditoria.registrar(con, sesion, "liquidaciones_eventuales", "INSERT", lid,
                            despues={"unidades": unidades_total, "total": total, "presupuesto": presupuesto, "dpf": dpf,
                                     "semaforo": semaforo, "asiento_id": asiento.id})
    return {"id": lid, "total": total, "presupuesto": presupuesto, "dpf": dpf, "semaforo": semaforo,
            "asiento_id": asiento.id}


def autorizar_pago_jornales(con: sqlite3.Connection, sesion: Sesion, liquidacion_id: int,
                            justificacion: str | None = None) -> None:
    """La GG autoriza el pago verificando el DPF; con semáforo MALO exige
    justificación del sobrecosto (PR-FIN-04 Paso 8)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "AUTORIZAR")
    with transaccion(con):
        liq = con.execute("SELECT * FROM liquidaciones_eventuales WHERE id = ?", (liquidacion_id,)).fetchone()
        if liq is None:
            raise DatosInvalidosError(f"No existe la liquidación {liquidacion_id}.")
        if liq["pago_autorizado_por"]:
            raise ReglaNegocioError("El pago ya está autorizado.", codigo="PAGO_YA_AUTORIZADO")
        nota = _opcional(justificacion, "justificación", 500)
        if liq["semaforo"] == "MALO" and not nota:
            raise ReglaNegocioError(f"DPF de {liq['dpf']} % (Malo > 105 %): justifique el sobrecosto antes de autorizar.",
                                    codigo="DPF_SIN_JUSTIFICACION")
        con.execute("UPDATE liquidaciones_eventuales SET pago_autorizado_por = ?, pago_autorizado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), liquidacion_id))
        auditoria.registrar(con, sesion, "liquidaciones_eventuales", "AUTORIZACION", liquidacion_id,
                            despues={"pago_autorizado_por": sesion.nombre_usuario}, detalle=nota)


def pagar_jornales(con: sqlite3.Connection, sesion: Sesion, liquidacion_id: int, fecha: Any, cuenta_banco: str, *,
                   autorizacion_id: int | None = None) -> int:
    """Pago de la liquidación de jornales (2.1.02.04) con CI-FIN-09.

    # DISCREPANCIA: la jerarquía de POL-04 cita la planilla del personal
    # estable, no la del eventual; el pago se registra sin categoría.
    """
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    with transaccion(con):
        liq = con.execute("SELECT * FROM liquidaciones_eventuales WHERE id = ?", (liquidacion_id,)).fetchone()
        if liq is None:
            raise DatosInvalidosError(f"No existe la liquidación {liquidacion_id}.")
        if not liq["pago_autorizado_por"]:
            raise AutorizacionError("La GG debe autorizar el pago de la liquidación (PR-FIN-04 Paso 8).",
                                    codigo="PAGO_SIN_AUTORIZACION")
        if liq["movimiento_bancario_id"]:
            raise ReglaNegocioError("La liquidación ya está pagada.", codigo="LIQUIDACION_PAGADA")
        ev = _eventual(con, int(liq["autorizacion_eventual_id"]))
        mid = tesoreria.registrar_pago(
            con, sesion, fecha, de_centavos(int(liq["total_cent"])), cuenta_banco, EVENTUAL_POR_PAGAR,
            f"Pago de jornales de faenado — FIN-EGR-08 n.º {liquidacion_id}", "Personal eventual de faenado",
            autorizacion_id=autorizacion_id, documento_ref=f"FIN-EGR-08 {liquidacion_id}")
        con.execute("UPDATE liquidaciones_eventuales SET movimiento_bancario_id = ?, pagado_en = ? WHERE id = ?",
                    (mid, a_fecha(fecha).isoformat(), liquidacion_id))
        compromiso = con.execute("SELECT estado FROM compromisos WHERE id = ?", (ev["compromiso_id"],)).fetchone()
        if compromiso is not None and compromiso["estado"] in ("VIGENTE", "AUTORIZADO"):
            con.execute("UPDATE compromisos SET estado = 'PAGADO', actualizado_en = ? WHERE id = ?",
                        (_ahora_iso(), ev["compromiso_id"]))
            auditoria.registrar(con, sesion, "compromisos", "UPDATE", ev["compromiso_id"],
                                antes={"estado": compromiso["estado"]}, despues={"estado": "PAGADO"})
        auditoria.registrar(con, sesion, "liquidaciones_eventuales", "UPDATE", liquidacion_id,
                            antes={"movimiento_bancario_id": None}, despues={"movimiento_bancario_id": mid})
    return mid


def listar_eventuales(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Circulares con su solicitud, contratos y liquidación."""
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    filas = []
    for c in con.execute(
            """SELECT c.*, a.id AS autorizacion_eventual_id, a.estado AS estado_autorizacion, a.trabajadores,
                      a.presupuesto_cent, a.autorizacion_id, a.fecha_lunes, a.por_suplencia,
                      l.id AS liquidacion_id, l.total_cent, l.dpf, l.semaforo, l.pago_autorizado_por,
                      l.movimiento_bancario_id,
                      (SELECT COUNT(*) FROM contratos_eventuales k WHERE k.autorizacion_eventual_id = a.id) AS contratos
                 FROM circulares_cierre c LEFT JOIN autorizaciones_eventuales a ON a.circular_id = c.id
                 LEFT JOIN liquidaciones_eventuales l ON l.autorizacion_eventual_id = a.id
                ORDER BY c.fecha_faenado DESC"""):
        d = dict(c)
        d["presupuesto"] = de_centavos(int(c["presupuesto_cent"])) if c["presupuesto_cent"] is not None else None
        d["total"] = de_centavos(int(c["total_cent"])) if c["total_cent"] is not None else None
        filas.append(d)
    return filas


def obtener_liquidacion_eventual(con: sqlite3.Connection, sesion: Sesion, liquidacion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    liq = con.execute("SELECT * FROM liquidaciones_eventuales WHERE id = ?", (liquidacion_id,)).fetchone()
    if liq is None:
        raise DatosInvalidosError(f"No existe la liquidación {liquidacion_id}.")
    lineas = [{**dict(l), "tarifa": de_centavos(int(l["tarifa_cent"])), "total": de_centavos(int(l["total_cent"]))}
              for l in con.execute("""SELECT l.*, k.nombre, k.ci FROM liquidacion_eventual_lineas l JOIN contratos_eventuales k
                                        ON k.id = l.contrato_id WHERE l.liquidacion_id = ? ORDER BY k.nombre""",
                                   (liquidacion_id,))]
    return {**dict(liq), "total": de_centavos(int(liq["total_cent"])), "presupuesto":
            de_centavos(int(liq["presupuesto_cent"])), "lineas": lineas}


def obtener_eventual(con: sqlite3.Connection, sesion: Sesion, autorizacion_eventual_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    ev = _eventual(con, autorizacion_eventual_id)
    circular = con.execute("SELECT * FROM circulares_cierre WHERE id = ?", (ev["circular_id"],)).fetchone()
    contratos = [dict(k) for k in con.execute("SELECT * FROM contratos_eventuales WHERE autorizacion_eventual_id = ? "
                                              "ORDER BY id", (autorizacion_eventual_id,))]
    return {**dict(ev), "presupuesto": de_centavos(int(ev["presupuesto_cent"])), "tarifa":
            de_centavos(int(ev["tarifa_cent"])), "circular": dict(circular), "contratos": contratos}


def obtener_circular(con: sqlite3.Connection, sesion: Sesion, circular_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    c = con.execute("SELECT * FROM circulares_cierre WHERE id = ?", (circular_id,)).fetchone()
    if c is None:
        raise DatosInvalidosError(f"No existe la circular {circular_id}.")
    return dict(c)


# ==========================================================================
# 7. LIQUIDACIÓN FINAL DE BENEFICIOS (FIN-EGR-11)
# ==========================================================================
def calcular_liquidacion_final(con: sqlite3.Connection, personal_id: int, vacaciones_dias: Any) -> dict[str, Any]:
    """Indemnización (1 mes por año, proporcional, sobre el promedio de los
    últimos 3 meses), aguinaldo proporcional del año, vacaciones no gozadas y
    salario proporcional del último mes no incluido en planilla (30/360)."""
    t = _personal(con, personal_id)
    if t["estado"] != "RETIRADO" or not t["fecha_retiro"]:
        raise ReglaNegocioError("Registre primero el retiro del trabajador.", codigo="SIN_RETIRO")
    retiro, ingreso = a_fecha(t["fecha_retiro"]), a_fecha(t["fecha_ingreso"])
    p = _params(con, retiro)
    tasas = tasas_vigentes(con, retiro)
    servicio = dias_360(ingreso, retiro)
    minimo = int(_param(p, "liquidacion_final_antiguedad_min_dias"))
    if (retiro - ingreso).days + 1 <= minimo:
        raise ReglaNegocioError(f"La liquidación final corresponde a contratos de más de {minimo} días (PC-09).",
                                codigo="NO_CORRESPONDE_LIQUIDACION")
    ultimos = [de_centavos(int(r[0])) for r in con.execute(
        """SELECT l.total_ganado_cent FROM planilla_lineas l JOIN planillas p ON p.id = l.planilla_id
            WHERE l.personal_id = ? AND p.estado = 'CONTABILIZADA' AND l.dias_trabajados >= ?
            ORDER BY p.anio DESC, p.mes DESC LIMIT 3""", (personal_id, tasas.dias_mes))]
    promedio = redondear_bs(sum(ultimos, CERO) / len(ultimos)) if ultimos else salario_vigente(con, personal_id, retiro)
    anio_base = tasas.dias_mes * MESES_ANIO
    indemnizacion = redondear_bs(promedio * servicio / anio_base)
    inicio_anio = max(date(retiro.year, 1, 1), ingreso)
    aguinaldo = redondear_bs(promedio * dias_360(inicio_anio, retiro) / anio_base)
    vac = a_decimal(vacaciones_dias, "días de vacación pendientes")
    if vac < 0:
        raise DatosInvalidosError("Los días de vacación no pueden ser negativos.")
    vacaciones = redondear_bs(promedio / tasas.dias_mes * vac)
    en_planilla = con.execute(
        """SELECT 1 FROM planilla_lineas l JOIN planillas p ON p.id = l.planilla_id WHERE l.personal_id = ?
            AND p.anio = ? AND p.mes = ? AND p.estado <> 'ANULADA'""", (personal_id, retiro.year, retiro.month)).fetchone()
    salario_dias = 0 if en_planilla else min(retiro.day, tasas.dias_mes)
    salario = redondear_bs(salario_vigente(con, personal_id, retiro) * salario_dias / tasas.dias_mes)
    laboral = redondear_bs(salario * tasas.laboral)
    total = indemnizacion + aguinaldo + vacaciones + salario - laboral
    return {"personal": dict(t), "dias_servicio": servicio, "promedio": promedio, "indemnizacion": indemnizacion,
            "aguinaldo": aguinaldo, "vacaciones_dias": vac, "vacaciones": vacaciones, "salario_dias": salario_dias,
            "salario": salario, "aporte_laboral": laboral, "total": total}


def preparar_liquidacion_final(con: sqlite3.Connection, sesion: Sesion, personal_id: int, fecha_preparacion: Any,
                               vacaciones_dias: Any = 0, causa_retraso: str | None = None) -> int:
    """Prepara el FIN-EGR-11; fuera de los 15 días exige la causa del retraso
    (que se reporta al Consejo de Vigilancia)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    calculo = calcular_liquidacion_final(con, personal_id, vacaciones_dias)
    f = a_fecha(fecha_preparacion)
    retiro = a_fecha(calculo["personal"]["fecha_retiro"])
    if f < retiro:
        raise DatosInvalidosError("La liquidación no puede prepararse antes del retiro.")
    limite = retiro + timedelta(days=int(_param(_params(con, retiro), "liquidacion_final_dias")))
    en_plazo = f <= limite
    causa = _opcional(causa_retraso, "causa del retraso", 500)
    if not en_plazo and not causa:
        raise ReglaNegocioError(f"Plazo vencido el {fecha_pantalla(limite)}: registre la causa del retraso para el "
                                "informe al Consejo de Vigilancia (FIN-EGR-11).", codigo="LIQUIDACION_FUERA_DE_PLAZO")
    if con.execute("SELECT 1 FROM liquidaciones_finales WHERE personal_id = ?", (personal_id,)).fetchone():
        raise ReglaNegocioError("El trabajador ya tiene liquidación final.", codigo="LIQUIDACION_EXISTE")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO liquidaciones_finales (personal_id, fecha_retiro, fecha_preparacion, fecha_limite,
                   dentro_de_plazo, causa_retraso, dias_servicio, promedio_cent, indemnizacion_cent, vacaciones_dias,
                   vacaciones_cent, aguinaldo_cent, salario_dias, salario_cent, aporte_laboral_cent, total_cent, estado,
                   elaborado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (personal_id, retiro.isoformat(), f.isoformat(), limite.isoformat(), int(en_plazo), causa,
             calculo["dias_servicio"], a_centavos(calculo["promedio"]), a_centavos(calculo["indemnizacion"]),
             str(calculo["vacaciones_dias"]), a_centavos(calculo["vacaciones"]), a_centavos(calculo["aguinaldo"]),
             calculo["salario_dias"], a_centavos(calculo["salario"]), a_centavos(calculo["aporte_laboral"]),
             a_centavos(calculo["total"]), "PREPARADA", sesion.nombre_usuario, _ahora_iso()))
        lid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "liquidaciones_finales", "INSERT", lid,
                            despues={k: v for k, v in calculo.items() if k != "personal"},
                            detalle=None if en_plazo else f"Fuera de plazo: {causa}")
    return lid


def autorizar_liquidacion_final(con: sqlite3.Connection, sesion: Sesion, liquidacion_id: int) -> None:
    seguridad.exigir_permiso(con, sesion, "planillas", "AUTORIZAR")
    with transaccion(con):
        liq = con.execute("SELECT * FROM liquidaciones_finales WHERE id = ?", (liquidacion_id,)).fetchone()
        if liq is None:
            raise DatosInvalidosError(f"No existe la liquidación {liquidacion_id}.")
        if liq["estado"] != "PREPARADA":
            raise ReglaNegocioError(f"La liquidación está {liq['estado']}.", codigo="LIQUIDACION_NO_PREPARADA")
        con.execute("UPDATE liquidaciones_finales SET estado = 'AUTORIZADA', autorizado_por = ?, autorizado_en = ? "
                    "WHERE id = ?", (sesion.nombre_usuario, _ahora_iso(), liquidacion_id))
        auditoria.registrar(con, sesion, "liquidaciones_finales", "AUTORIZACION", liquidacion_id,
                            antes={"estado": "PREPARADA"}, despues={"estado": "AUTORIZADA"})


def _acumulado(con: sqlite3.Connection, personal_id: int, campo: str, anio: int | None = None) -> Decimal:
    sql = (f"SELECT COALESCE(SUM(l.{campo}), 0) FROM planilla_lineas l JOIN planillas p ON p.id = l.planilla_id "
           "WHERE l.personal_id = ? AND p.estado = 'CONTABILIZADA'")
    args: list[Any] = [personal_id]
    if anio is not None:
        sql += " AND p.anio = ?"
        args.append(anio)
    return de_centavos(int(con.execute(sql, args).fetchone()[0]))


def contabilizar_liquidacion_final(con: sqlite3.Connection, sesion: Sesion, liquidacion_id: int, fecha: Any) -> int:
    """Asiento: la indemnización y el aguinaldo se aplican contra las
    provisiones acumuladas del trabajador (2.2.01.01 y 2.1.02.02) y el
    exceso va al costo o gasto; vacaciones y salario al costo o gasto; contra
    sueldos por pagar (2.1.02.01) y aporte laboral (2.1.02.05)."""
    seguridad.exigir_permiso(con, sesion, "planillas", "EDITAR")
    f = a_fecha(fecha)
    with transaccion(con):
        liq = con.execute("SELECT * FROM liquidaciones_finales WHERE id = ?", (liquidacion_id,)).fetchone()
        if liq is None:
            raise DatosInvalidosError(f"No existe la liquidación {liquidacion_id}.")
        if liq["estado"] != "AUTORIZADA":
            raise AutorizacionError("La liquidación final la autoriza la Gerencia General.",
                                    codigo="LIQUIDACION_SIN_AUTORIZACION")
        t = _personal(con, int(liq["personal_id"]))
        granja = t["area"] == "GRANJA"

        def costo(concepto: str) -> dict[str, Any]:
            if granja:
                return {"cuenta": CENTRO_CARNE, "centro_costo": CENTRO_CARNE, "sufijo": SUFIJO[concepto]}
            return {"cuenta": GASTO_ADMIN[concepto]}

        indemn = de_centavos(int(liq["indemnizacion_cent"]))
        aguin = de_centavos(int(liq["aguinaldo_cent"]))
        prov_ind = min(indemn, _acumulado(con, int(t["id"]), "indemnizacion_cent"))
        prov_agu = min(aguin, _acumulado(con, int(t["id"]), "aguinaldo_cent", a_fecha(liq["fecha_retiro"]).year))
        lineas: list[dict[str, Any]] = []
        if prov_ind > 0:
            lineas.append({"cuenta": PROVISION_INDEMNIZACION, "debe": prov_ind})
        if indemn - prov_ind > 0:
            lineas.append({**costo("indemnizacion"), "debe": indemn - prov_ind})
        if prov_agu > 0:
            lineas.append({"cuenta": AGUINALDO_POR_PAGAR, "debe": prov_agu})
        if aguin - prov_agu > 0:
            lineas.append({**costo("aguinaldo"), "debe": aguin - prov_agu})
        sueldo = de_centavos(int(liq["vacaciones_cent"])) + de_centavos(int(liq["salario_cent"]))
        if sueldo > 0:
            lineas.append({**costo("salario"), "debe": sueldo})
        laboral = de_centavos(int(liq["aporte_laboral_cent"]))
        lineas.append({"cuenta": SUELDOS_POR_PAGAR, "haber": de_centavos(int(liq["total_cent"]))})
        if laboral > 0:
            lineas.append({"cuenta": LABORAL_POR_PAGAR, "haber": laboral})
        asiento = contable.crear_asiento(con, sesion, f, f"Liquidación final de beneficios de {t['nombre']} (FIN-EGR-11)",
                                         lineas, "PLANILLAS", f"FIN-EGR-11 n.º {liquidacion_id}", tipo="TRASPASO")
        con.execute("UPDATE liquidaciones_finales SET estado = 'CONTABILIZADA', asiento_id = ? WHERE id = ?",
                    (asiento.id, liquidacion_id))
        auditoria.registrar(con, sesion, "liquidaciones_finales", "UPDATE", liquidacion_id,
                            antes={"estado": "AUTORIZADA"}, despues={"estado": "CONTABILIZADA", "asiento_id": asiento.id})
    return asiento.id


def obtener_liquidacion_final(con: sqlite3.Connection, sesion: Sesion, liquidacion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    liq = con.execute("SELECT l.*, p.nombre, p.ci, p.cargo, p.fecha_ingreso FROM liquidaciones_finales l JOIN personal p "
                      "ON p.id = l.personal_id WHERE l.id = ?", (liquidacion_id,)).fetchone()
    if liq is None:
        raise DatosInvalidosError(f"No existe la liquidación {liquidacion_id}.")
    d = dict(liq)
    for k in list(d):
        if k.endswith("_cent"):
            d[k[:-5]] = de_centavos(int(d[k]))
    return d


def listar_liquidaciones_finales(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    return [{**dict(f), "total": de_centavos(int(f["total_cent"]))} for f in con.execute(
        "SELECT l.*, p.nombre FROM liquidaciones_finales l JOIN personal p ON p.id = l.personal_id ORDER BY l.id DESC")]


# ==========================================================================
# 8. ALERTAS DE PAGO LEGAL
# ==========================================================================
def alertas_pago(con: sqlite3.Connection, fecha: Any) -> list[dict[str, Any]]:
    """Obligaciones laborales pendientes con su vencimiento legal: sueldos y
    aportes de cada planilla contabilizada, aguinaldo y liquidaciones finales.

    Estado: VENCIDO (fecha posterior al vencimiento), POR_VENCER (dentro de los
    días de alerta) o AL_DIA.
    """
    f = a_fecha(fecha)
    p = _params(con, f)
    aviso = int(_param(p, "alerta_pago_planillas_dias"))

    def estado(vence: date) -> str:
        if f > vence:
            return "VENCIDO"
        return "POR_VENCER" if (vence - f).days <= aviso else "AL_DIA"

    alertas = []
    for pl in con.execute("SELECT * FROM planillas WHERE estado = 'CONTABILIZADA' ORDER BY anio, mes"):
        venc = vencimientos_planilla(con, int(pl["anio"]), int(pl["mes"]))
        periodo = f"{int(pl['mes']):02d}/{pl['anio']}"
        if not pl["pago_sueldos_mov_id"]:
            alertas.append({"tipo": "SUELDOS", "referencia": f"Planilla {periodo}", "vencimiento": venc["sueldos"],
                            "monto": de_centavos(int(pl["liquido_cent"])), "estado": estado(venc["sueldos"]),
                            "detalle": "Líquido pagable del personal fijo (2.1.02.01)."})
        if not pl["pago_aportes_mov_id"]:
            alertas.append({"tipo": "APORTES", "referencia": f"Planilla {periodo}", "vencimiento": venc["aportes"],
                            "monto": de_centavos(int(pl["aporte_laboral_cent"]) + int(pl["aporte_patronal_cent"])),
                            "estado": estado(venc["aportes"]),
                            "detalle": "Aportes laborales y patronales a la Gestora Pública y la CNS (riesgo 38)."})
    saldo_aguinaldo = -tesoreria.saldo_cuenta(con, AGUINALDO_POR_PAGAR, f)
    if saldo_aguinaldo > 0:
        venc = date(f.year, int(_param(p, "aguinaldo_mes_pago")), int(_param(p, "aguinaldo_pago_dia_limite")))
        alertas.append({"tipo": "AGUINALDO", "referencia": f"Aguinaldo {venc.year}", "vencimiento": venc,
                        "monto": saldo_aguinaldo, "estado": estado(venc),
                        "detalle": "Aguinaldo de navidad provisionado (2.1.02.02)."})
    dias_liq = int(_param(p, "liquidacion_final_dias"))
    for t in con.execute("SELECT * FROM personal WHERE estado = 'RETIRADO' AND id NOT IN "
                         "(SELECT personal_id FROM liquidaciones_finales)"):
        venc = a_fecha(t["fecha_retiro"]) + timedelta(days=dias_liq)
        alertas.append({"tipo": "LIQUIDACION_FINAL", "referencia": t["nombre"], "vencimiento": venc, "monto": None,
                        "estado": estado(venc), "detalle": f"FIN-EGR-11 dentro de {dias_liq} días del retiro."})
    return sorted(alertas, key=lambda a: (a["vencimiento"], a["tipo"]))


def resumen_planillas(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Cifras de cabecera de la pantalla."""
    seguridad.exigir_permiso(con, sesion, "planillas", "VER")
    f = a_fecha(fecha)
    alertas = alertas_pago(con, f)
    return {
        "personal_activo": int(con.execute("SELECT COUNT(*) FROM personal WHERE estado = 'ACTIVO'").fetchone()[0]),
        "provision_indemnizacion": -tesoreria.saldo_cuenta(con, PROVISION_INDEMNIZACION, f),
        "provision_aguinaldo": -tesoreria.saldo_cuenta(con, AGUINALDO_POR_PAGAR, f),
        "vencidas": sum(1 for a in alertas if a["estado"] == "VENCIDO"),
        "por_vencer": sum(1 for a in alertas if a["estado"] == "POR_VENCER"),
    }
