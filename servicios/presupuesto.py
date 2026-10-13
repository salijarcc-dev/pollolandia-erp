"""Sistema presupuestario (Fase 12): Presupuesto Maestro 2026-2030, presupuesto
por ciclo, calendario, ejecución y variaciones, modificaciones, POA y D.6.

Implementa el Manual del Sistema Presupuestario (SGC-MAN-ADM-001) y el
POA-001:

* Presupuesto Maestro (módulos A a E) y por ciclo (FIN-TES-09 y FIN-TES-10).
* Calendario presupuestario de abril a julio (§2.2) con alertas.
* Ejecución frente al real del mayor con variaciones total, porcentual, de
  precio y de cantidad y semáforo por partida (E.1): ingresos −5 %/−10 %;
  materia prima, CIF y gastos +5 %/+10 %; faenado por DPF; CAPEX 15 %; flujo
  10 % o riesgo bajo el SME. Informe FIN-TES-12 con causa y acción.
* Solicitud de modificación presupuestaria FIN-TES-11 con la autoridad de la
  matriz CI-FIN-09.
* Seguimiento del POA por trimestre (cumplimiento físico ≥ 90 %).
* Alimento propio frente a alimento comprado (D.6).

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from db.conexion import a_centavos, de_centavos, obtener_parametros_vigentes, transaccion
from servicios import auditoria, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil
from servicios.seguridad import Sesion, TipoDecision
from utils.formato import a_fecha, fecha_pantalla, redondear_bs

CERO = Decimal("0.00")
VERDE, AMARILLO, ROJO = "VERDE", "AMARILLO", "ROJO"
ORDEN_SEMAFORO = {VERDE: 0, AMARILLO: 1, ROJO: 2}
CENTRO_CARNE, CENTRO_ALIMENTO = "6.1.01.01", "6.1.01.02"
TIPOS_EXCLUIDOS = ("CIERRE", "APERTURA")
_MPD_CARNE_OTROS = ("03", "04", "05", "06", "07", "08", "09", "10", "12", "13")
_CIF = ("30", "31", "32", "33", "34", "35", "36", "37", "38", "39")

# partida: (nombre, grupo FIN-TES-12, regla de semáforo, criterio del real)
PARTIDAS: dict[str, dict[str, Any]] = {
    "ING_CARNE": {"nombre": "Ingresos por carne", "grupo": "INGRESOS", "regla": "INGRESO"},
    "ING_ALIMENTO": {"nombre": "Ingresos por alimento excedente", "grupo": "INGRESOS", "regla": "INGRESO"},
    "ING_GALLINAZA": {"nombre": "Ingresos por gallinaza", "grupo": "INGRESOS", "regla": "INGRESO"},
    "MP_POLLITOS": {"nombre": "Pollitos BB", "grupo": "COSTOS DE PRODUCCIÓN", "regla": "COSTO"},
    "MP_ALIMENTO": {"nombre": "Alimento", "grupo": "COSTOS DE PRODUCCIÓN", "regla": "COSTO"},
    "MP_OTROS": {"nombre": "Veterinarios, limpieza, crianza y bolsas", "grupo": "COSTOS DE PRODUCCIÓN",
                 "regla": "COSTO"},
    "MOD_FIJA": {"nombre": "Mano de obra directa fija", "grupo": "COSTOS DE PRODUCCIÓN", "regla": "COSTO"},
    "MOD_EVENTUAL": {"nombre": "Faenado — personal eventual (DPF)", "grupo": "COSTOS DE PRODUCCIÓN",
                     "regla": "DPF"},
    "CIF": {"nombre": "CIF de la carne", "grupo": "CIF", "regla": "COSTO"},
    "ALI_MP": {"nombre": "Materia prima del alimento", "grupo": "COSTOS DEL ALIMENTO", "regla": "COSTO"},
    "ALI_MOD": {"nombre": "Mano de obra del alimento", "grupo": "COSTOS DEL ALIMENTO", "regla": "COSTO"},
    "ALI_CIF": {"nombre": "CIF del alimento", "grupo": "COSTOS DEL ALIMENTO", "regla": "COSTO"},
    "GASTOS_ADM": {"nombre": "Gastos de administración", "grupo": "GASTOS Y CAPITAL", "regla": "COSTO"},
    "GASTOS_VENTA": {"nombre": "Gastos de venta", "grupo": "GASTOS Y CAPITAL", "regla": "COSTO"},
    "CAPEX": {"nombre": "CAPEX", "grupo": "GASTOS Y CAPITAL", "regla": "CAPEX"},
}
MODULOS_MAESTRO: dict[str, str] = {
    "VENTAS": "A. Presupuesto de ventas", "PRODUCCION": "B.1 Producción por lotes",
    "MP": "B.2 Materia prima e insumos — carne", "MP_ALIMENTO": "B.3 Materia prima del alimento",
    "MOD": "B.4 Mano de obra directa — carne", "MOD_ALIMENTO": "B.4 Mano de obra directa — alimento",
    "CIF": "B.5 CIF — carne", "CIF_ALIMENTO": "B.5 CIF — alimento", "COSTO": "B.6 Costo de producción",
    "GASTOS": "4.1 Gastos de administración y ventas", "CAPEX": "D.5 Plan CAPEX", "FLUJO": "5.1 Flujo de caja",
    "RESULTADOS": "5.2 Estado de resultados", "BALANCE": "5.3 Balance general",
    "D6": "D.6 Alimento propio frente a comprado", "INDICADORES": "E.3 Cuadro de mando proyectado",
}
# Manual §2.2: (actividad, descripción, parámetro del plazo, rol, (recurso, acción), documento)
ACTIVIDADES_CALENDARIO: list[tuple[int, str, str, str, tuple[str, str], str]] = [
    (1, "Validar supuestos: precios, insumos, pre-ventas HORECA, cuota RAU y parámetros técnicos",
     "presupuesto_cal_supuestos", "JF", ("presupuesto", "CREAR"), "FIN-TES-09 Hoja de supuestos"),
    (2, "Elaborar el proyecto de presupuesto (módulos A a E)", "presupuesto_cal_proyecto", "JF",
     ("presupuesto", "CREAR"), "FIN-TES-10 Presupuesto por ciclo"),
    (3, "Revisar y ajustar el proyecto (Comité de Presupuesto)", "presupuesto_cal_comite", "GG",
     ("presupuesto", "EDITAR"), "Acta del Comité"),
    (4, "Presentar el Plan CAPEX con ROA y compatibilidad con el SME", "presupuesto_cal_capex", "GG",
     ("presupuesto", "EDITAR"), "FIN-TES-01, 02 y 03"),
    (5, "Aprobar el presupuesto y el Plan CAPEX", "presupuesto_cal_aprobacion", "CA", ("presupuesto", "APROBAR"),
     "Acta de Consejo"),
    (6, "Verificar la consistencia y emitir observaciones", "presupuesto_cal_verificacion_cv", "CV",
     ("presupuesto", "VERIFICAR"), "INF-CV-01 y comunicación escrita"),
    (7, "Comunicar el presupuesto y las directrices de reinversión", "presupuesto_cal_comunicacion", "JF",
     ("presupuesto", "CREAR"), "CIR-TES-01"),
]
# FIN-TES-09: (parámetro, unidad, clave en parámetros, fuente)
SUPUESTOS: list[tuple[str, str, str, str]] = [
    ("Precio de venta de carne", "Bs/kg", "precio_carne_kg", "Costo unitario + margen ≥ 20 %; mercado"),
    ("Precio de gallinaza", "Bs/carga", "precio_gallinaza_carga", "Mercado local"),
    ("Precio del alimento excedente", "Bs/qq", "precio_alimento_qq", "Costo + margen ≥ 10 %"),
    ("Rendimiento canal", "kg/ave", "rendimiento_kg_ave", "Registro de faenado FIN-TRA-05"),
    ("Mortalidad esperada", "fracción", "mortalidad_alerta", "Bitácora FIN-TRA-02; PC-03"),
    ("Precio del pollito BB", "Bs/ave", "precio_pollito_bb", "Cotización de proveedores"),
    ("Alimento comprado", "Bs/qq", "precio_alimento_comprado_qq", "Cotización de proveedores"),
    ("Consumo de alimento", "qq/ave", "consumo_alimento_qq_ave", "ICA y bitácora del ciclo anterior"),
    ("Personal eventual de faenado", "Bs/ave", "tarifa_eventual_por_ave", "Tarifa de destajo vigente"),
    ("Indexación salarial", "fracción", "indexacion_salarial", "Política salarial"),
    ("Cuota RAU (POR VALIDAR)", "Bs/ha", "rau_cuota_por_ha", "RND del SIN (UFV)"),
    ("Flete por lote de 1.000 aves", "Bs", "flete_por_lote_bs", "Contrato de transporte"),
]
AREAS_POA: dict[str, str] = {"PRODUCCION": "Producción / Granja", "FAENADO": "Procesamiento / Faenado y Logística",
                             "COMERCIAL": "Comercial y Ventas", "ADMINISTRATIVA": "Administrativa, Financiera y Legal",
                             "GOBERNANZA": "Fortalecimiento Cooperativo / Gobernanza"}
ESTADOS_AVANCE: dict[str, str] = {"CUMPLIDA": "Cumplida", "PARCIAL": "Parcial", "NO_CUMPLIDA": "No cumplida"}


# ==========================================================================
# UTILIDADES
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 500) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"Indique {campo}.")
    texto = valor.strip()
    if len(texto) > maximo:
        raise DatosInvalidosError(f"El campo {campo} admite hasta {maximo} caracteres.")
    return texto


def _opcional(valor: Any, campo: str, maximo: int = 500) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    return _texto(valor, campo, maximo)


def _entero(valor: Any, campo: str, minimo: int | None = None, maximo: int | None = None) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise DatosInvalidosError(f"El campo {campo} debe ser un número entero.")
    if (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo):
        raise DatosInvalidosError(f"El campo {campo} está fuera de rango.")
    return valor


def _decimal(valor: Any, campo: str) -> Decimal:
    try:
        d = valor if isinstance(valor, Decimal) else Decimal(str(valor).replace(",", "."))
    except Exception as exc:  # noqa: BLE001 - se informa el dato inválido
        raise DatosInvalidosError(f"El campo {campo} no es un número válido: {valor!r}.") from exc
    if not d.is_finite():
        raise DatosInvalidosError(f"El campo {campo} no es un número válido.")
    return d


def _params(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio)


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro vigente «{clave}» (regla 2).", codigo="PARAMETRO_FALTANTE")
    return p[clave]


def _ejercicio(con: sqlite3.Connection, ejercicio: int) -> tuple[date, date]:
    _entero(ejercicio, "ejercicio")
    fila = con.execute("SELECT fecha_inicio, fecha_fin FROM ejercicios WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el ejercicio {ejercicio}.")
    return date.fromisoformat(fila[0]), date.fromisoformat(fila[1])


def ejercicio_de(con: sqlite3.Connection, fecha: Any) -> int:
    f = a_fecha(fecha).isoformat()
    fila = con.execute("SELECT ejercicio FROM ejercicios WHERE ? BETWEEN fecha_inicio AND fecha_fin", (f,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"La fecha {fecha_pantalla(f)} no pertenece a ningún ejercicio.")
    return int(fila[0])


def ejercicios(con: sqlite3.Connection) -> list[int]:
    return [int(r[0]) for r in con.execute("SELECT ejercicio FROM ejercicios ORDER BY ejercicio")]


def _fraccion(num: Decimal, den: Decimal) -> Decimal | None:
    if den == 0:
        return None
    return (num / den).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def peor(semaforos: Sequence[str | None]) -> str:
    valores = [s for s in semaforos if s]
    return max(valores, key=lambda s: ORDEN_SEMAFORO[s]) if valores else VERDE


# ==========================================================================
# 1. PRESUPUESTO MAESTRO Y POR CICLO (FIN-TES-09, FIN-TES-10)
# ==========================================================================
def presupuesto_maestro(con: sqlite3.Connection, sesion: Sesion, ejercicio: int,
                        modulo: str | None = None) -> list[dict[str, Any]]:
    """Filas del Presupuesto Maestro del ejercicio (o de un módulo)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    _ejercicio(con, ejercicio)
    sql, args = "SELECT * FROM presupuesto_maestro WHERE ejercicio = ?", [ejercicio]
    if modulo is not None:
        if modulo not in MODULOS_MAESTRO:
            raise DatosInvalidosError(f"Módulo no válido: {modulo!r}.")
        sql += " AND modulo = ?"
        args.append(modulo)
    filas = []
    for r in con.execute(sql + " ORDER BY modulo, orden", args):
        filas.append({**dict(r), "monto": de_centavos(int(r["monto_cent"])),
                      "cantidad": de_mil(r["cantidad_mil"]) if r["cantidad_mil"] is not None else None,
                      "valor": Decimal(r["valor"]) if r["valor"] is not None else None})
    return filas


def serie_maestro(con: sqlite3.Connection, sesion: Sesion, modulo: str) -> list[dict[str, Any]]:
    """Un módulo del maestro con una columna por ejercicio (2026-2030)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    if modulo not in MODULOS_MAESTRO:
        raise DatosInvalidosError(f"Módulo no válido: {modulo!r}.")
    filas: dict[str, dict[str, Any]] = {}
    for r in con.execute("SELECT * FROM presupuesto_maestro WHERE modulo = ? ORDER BY orden, ejercicio", (modulo,)):
        f = filas.setdefault(r["clave"], {"clave": r["clave"], "concepto": r["concepto"], "unidad": r["unidad"],
                                         "es_total": bool(r["es_total"]), "orden": int(r["orden"])})
        if int(r["monto_cent"]):
            f[str(r["ejercicio"])] = de_centavos(int(r["monto_cent"]))
        elif r["valor"] is not None:
            f[str(r["ejercicio"])] = Decimal(r["valor"])
        elif r["cantidad_mil"] is not None:
            f[str(r["ejercicio"])] = de_mil(int(r["cantidad_mil"]))
        else:
            f[str(r["ejercicio"])] = None if f.get("unidad") in ("qq", "%", "Bs/qq") else de_centavos(0)
    return sorted(filas.values(), key=lambda f: f["orden"])


def cargar_presupuesto_maestro(con: sqlite3.Connection, sesion: Sesion, ejercicio: int,
                               filas: Sequence[Mapping[str, Any]], lineas: Sequence[Mapping[str, Any]]) -> int:
    """Carga el maestro y las metas por ciclo de un ejercicio nuevo (por
    ejemplo el que apruebe el calendario presupuestario). 2026-2030 vienen de
    las semillas; un ejercicio cargado no se reemplaza: se modifica con FIN-TES-11."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "CREAR")
    _ejercicio(con, ejercicio)
    if not filas or not lineas:
        raise DatosInvalidosError("Indique las filas del maestro y las metas por ciclo.")
    with transaccion(con):
        if con.execute("SELECT 1 FROM presupuesto_maestro WHERE ejercicio = ?", (ejercicio,)).fetchone():
            raise ReglaNegocioError(f"El Presupuesto Maestro {ejercicio} ya está cargado: tramite una modificación "
                                    "(FIN-TES-11).", codigo="YA_CARGADO")
        ahora = _ahora_iso()
        for i, f in enumerate(filas, start=1):
            modulo = f.get("modulo")
            if modulo not in MODULOS_MAESTRO:
                raise DatosInvalidosError(f"Fila {i}: módulo no válido {modulo!r}.")
            cantidad = f.get("cantidad")
            con.execute(
                """INSERT INTO presupuesto_maestro (ejercicio, modulo, orden, clave, concepto, unidad, cantidad_mil,
                       valor, monto_cent, es_total, fuente, cargado_por, cargado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ejercicio, modulo, i, _texto(f.get("clave"), f"la clave de la fila {i}", 60),
                 _texto(f.get("concepto"), f"el concepto de la fila {i}", 200), f.get("unidad"),
                 a_mil(cantidad, "cantidad", permitir_cero=True) if cantidad is not None else None,
                 str(_decimal(f["valor"], "valor")) if f.get("valor") is not None else None,
                 a_centavos(redondear_bs(_decimal(f.get("monto", 0), "monto"))), int(bool(f.get("es_total"))),
                 _texto(f.get("fuente", "Presupuesto aprobado por el Consejo de Administración"), "la fuente", 200),
                 sesion.nombre_usuario, ahora))
        for i, l in enumerate(lineas, start=1):
            partida = l.get("partida")
            if partida not in PARTIDAS:
                raise DatosInvalidosError(f"Meta {i}: partida no válida {partida!r}.")
            monto = redondear_bs(_decimal(l.get("monto"), "monto"))
            if monto < 0:
                raise DatosInvalidosError(f"Meta {i}: el monto no puede ser negativo.")
            cantidad = l.get("cantidad")
            cmil = a_mil(cantidad, "cantidad") if cantidad is not None else None
            precio = str(_decimal(l["precio"], "precio")) if cmil else None
            con.execute(
                """INSERT INTO presupuesto_lineas (ejercicio, ciclo, partida, concepto, unidad, cantidad_mil, precio,
                       monto_cent, fuente) VALUES (?,?,?,?,?,?,?,?,?)""",
                (ejercicio, _entero(l.get("ciclo"), "ciclo", 1, 12), partida,
                 l.get("concepto") or PARTIDAS[partida]["nombre"], l.get("unidad") if cmil else None, cmil, precio,
                 a_centavos(monto), "FIN-TES-10 aprobado"))
        auditoria.registrar(con, sesion, "presupuesto_maestro", "INSERT", ejercicio,
                            despues={"ejercicio": ejercicio, "filas": len(filas), "metas": len(lineas)})
    return len(filas) + len(lineas)


def ajustes_aprobados(con: sqlite3.Connection, ejercicio: int, ciclo: int, partida: str) -> int:
    """Suma (centavos) de las modificaciones aprobadas de la línea."""
    return int(con.execute(
        "SELECT COALESCE(SUM(monto_solicitado_cent - monto_aprobado_cent), 0) FROM modificaciones_presupuesto "
        "WHERE ejercicio = ? AND ciclo = ? AND partida = ? AND estado = 'APROBADA'",
        (ejercicio, ciclo, partida)).fetchone()[0])


def _linea(con: sqlite3.Connection, ejercicio: int, ciclo: int, partida: str) -> dict[str, Any]:
    fila = con.execute("SELECT * FROM presupuesto_lineas WHERE ejercicio = ? AND ciclo = ? AND partida = ?",
                       (ejercicio, ciclo, partida)).fetchone()
    ajuste = ajustes_aprobados(con, ejercicio, ciclo, partida)
    base = int(fila["monto_cent"]) if fila else 0
    return {"partida": partida, "concepto": fila["concepto"] if fila else PARTIDAS[partida]["nombre"],
            "unidad": fila["unidad"] if fila else None,
            "cantidad": de_mil(fila["cantidad_mil"]) if fila and fila["cantidad_mil"] else None,
            "precio": Decimal(fila["precio"]) if fila and fila["precio"] else None,
            "base": de_centavos(base), "ajuste": de_centavos(ajuste), "monto": de_centavos(base + ajuste),
            "existe": fila is not None}


def presupuesto_vigente(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    """Metas por ciclo con las modificaciones aprobadas (FIN-TES-11)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    _ejercicio(con, ejercicio)
    claves = {(int(r[0]), r[1]) for r in con.execute(
        "SELECT ciclo, partida FROM presupuesto_lineas WHERE ejercicio = ? UNION SELECT ciclo, partida FROM "
        "modificaciones_presupuesto WHERE ejercicio = ? AND estado = 'APROBADA'", (ejercicio, ejercicio))}
    orden = list(PARTIDAS)
    return [{"ciclo": c, **_linea(con, ejercicio, c, p)}
            for c, p in sorted(claves, key=lambda k: (k[0], orden.index(k[1]) if k[1] in orden else 99))]


def ciclos_ejercicio(con: sqlite3.Connection, ejercicio: int) -> list[dict[str, Any]]:
    """Ventanas de los ciclos del ejercicio: las fechas reales de
    ``ciclos_produccion`` si el ciclo existe; si no, inicio + 73 × (n − 1)."""
    inicio, fin = _ejercicio(con, ejercicio)
    p = _params(con, ejercicio)
    dias, n = int(_param(p, "dias_ciclo")), int(_param(p, "ciclos_por_ejercicio"))
    reales = {int(r["numero"]): r for r in con.execute(
        "SELECT numero, fecha_inicio, fecha_cierre FROM ciclos_produccion WHERE ejercicio = ?", (ejercicio,))}
    ciclos = []
    for k in range(1, n + 1):
        if k in reales:
            desde, hasta, origen = (date.fromisoformat(reales[k]["fecha_inicio"]),
                                    date.fromisoformat(reales[k]["fecha_cierre"]), "REAL")
        else:
            desde = inicio + timedelta(days=dias * (k - 1))
            hasta, origen = min(desde + timedelta(days=dias - 1), fin), "CALENDARIO"
        ciclos.append({"numero": k, "desde": desde, "hasta": hasta, "origen": origen})
    return ciclos


def _saldo_efectivo(con: sqlite3.Connection, hasta: date) -> Decimal:
    fila = con.execute(
        "SELECT COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0) FROM asiento_detalle d JOIN asientos a "
        "ON a.id = d.asiento_id WHERE a.fecha <= ? AND d.cuenta_codigo LIKE '1.1.01.%'", (hasta.isoformat(),)).fetchone()
    return de_centavos(int(fila[0]))


def saldo_proyectado_ciclo(con: sqlite3.Connection, ejercicio: int, ciclo: int) -> Decimal | None:
    """Saldo final proyectado al cierre del ciclo: saldo inicial del flujo
    (5.1) + flujo de operación prorrateado por ciclo − excedentes (ciclo 1) −
    CAPEX (ciclo 5, sección 4.2)."""
    m = {r["clave"]: de_centavos(int(r["monto_cent"])) for r in con.execute(
        "SELECT clave, monto_cent FROM presupuesto_maestro WHERE ejercicio = ? AND modulo = 'FLUJO'", (ejercicio,))}
    if not m:
        return None
    n = int(_param(_params(con, ejercicio), "ciclos_por_ejercicio"))
    operacion = (m["COBROS"] - m["PAGOS_OPERACION"]) * ciclo / n
    capex = sum((_linea(con, ejercicio, c, "CAPEX")["monto"] for c in range(1, ciclo + 1)), CERO)
    return redondear_bs(m["SALDO_INICIAL"] + operacion - m["EXCEDENTES"] - capex)


def presupuesto_ciclo(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, ciclo: int) -> dict[str, Any]:
    """FIN-TES-10: presupuesto del ciclo con cantidad, precio, monto y
    acumulado del año; SME y saldo final proyectado."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    _ejercicio(con, ejercicio)
    _entero(ciclo, "ciclo", 1, 12)
    ventanas = {c["numero"]: c for c in ciclos_ejercicio(con, ejercicio)}
    if ciclo not in ventanas:
        raise DatosInvalidosError(f"El ejercicio {ejercicio} no tiene ciclo {ciclo}.")
    filas = []
    for partida, datos in PARTIDAS.items():
        actual = _linea(con, ejercicio, ciclo, partida)
        acumulado = sum((_linea(con, ejercicio, c, partida)["monto"] for c in range(1, ciclo + 1)), CERO)
        if not actual["existe"] and actual["monto"] == 0 and acumulado == 0:
            continue
        filas.append({**actual, "grupo": datos["grupo"], "nombre": datos["nombre"], "acumulado": acumulado})
    v = ventanas[ciclo]
    return {"ejercicio": ejercicio, "ciclo": ciclo, "desde": v["desde"].isoformat(), "hasta": v["hasta"].isoformat(),
            "filas": filas, "sme": tesoreria.sme_vigente(con, v["desde"]),
            "saldo_final_proyectado": saldo_proyectado_ciclo(con, ejercicio, ciclo)}


def hoja_supuestos(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    """FIN-TES-09: valor base 2026 y valor del ejercicio de cada supuesto,
    leídos de ``parametros`` (más el SME de POL-04)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    _ejercicio(con, ejercicio)
    base, actual = _params(con, 2026), _params(con, ejercicio)
    filas = [{"parametro": nombre, "unidad": unidad, "clave": clave, "base": base.get(clave),
              "valor": actual.get(clave), "fuente": fuente} for nombre, unidad, clave, fuente in SUPUESTOS]
    filas.append({"parametro": "Saldo Mínimo de Efectivo", "unidad": "Bs", "clave": "sme",
                  "base": base.get("sme"), "valor": actual.get("sme"), "fuente": "POL-04"})
    return filas


# ==========================================================================
# 2. CALENDARIO PRESUPUESTARIO (Manual §2.2)
# ==========================================================================
def _limite_hito(con: sqlite3.Connection, ejercicio: int, clave: str) -> date:
    texto = str(_param(_params(con, ejercicio), clave))
    try:
        mes, dia = (int(x) for x in texto.split("-"))
        return date(ejercicio, mes, dia)
    except (ValueError, TypeError) as exc:
        raise ReglaNegocioError(f"El parámetro {clave} debe tener el formato MM-DD.", codigo="PARAMETRO_INVALIDO") from exc


def calendario_presupuestario(con: sqlite3.Connection, sesion: Sesion, ejercicio: int,
                              fecha: Any = None) -> dict[str, Any]:
    """Hitos de preparación del presupuesto del ejercicio (abril a julio del
    año en que inicia) con estado y alerta.

    El calendario del ejercicio 2026 no se aplica (disposición transitoria:
    el Presupuesto Maestro 2026-2030 se ratifica en la primera Asamblea)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    _ejercicio(con, ejercicio)
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    alerta = int(_param(_params(con, ejercicio), "presupuesto_alerta_dias"))
    registrados = {int(r["actividad"]): r for r in con.execute(
        "SELECT * FROM hitos_presupuesto WHERE ejercicio = ?", (ejercicio,))}
    hitos = []
    for numero, descripcion, clave, rol, _permiso, documento in ACTIVIDADES_CALENDARIO:
        limite = _limite_hito(con, ejercicio, clave)
        hecho = registrados.get(numero)
        dias = (limite - referencia).days
        if hecho:
            estado, semaforo = ("CUMPLIDO", VERDE) if hecho["en_plazo"] else ("CUMPLIDO_FUERA_DE_PLAZO", AMARILLO)
        elif dias < 0:
            estado, semaforo = "VENCIDO", ROJO
        elif dias <= alerta:
            estado, semaforo = "POR_VENCER", AMARILLO
        else:
            estado, semaforo = "PROGRAMADO", VERDE
        hitos.append({"actividad": numero, "descripcion": descripcion, "responsable": rol, "documento": documento,
                      "limite": limite.isoformat(), "dias": dias, "estado": estado, "semaforo": semaforo,
                      "fecha": hecho["fecha"] if hecho else None,
                      "documento_ref": hecho["documento_ref"] if hecho else None,
                      "registrado_por": hecho["registrado_por"] if hecho else None})
    transitoria = ejercicio == min(ejercicios(con))
    return {"ejercicio": ejercicio, "hitos": hitos, "transitoria": transitoria,
            "nota": ("Disposición transitoria: el Presupuesto Maestro 2026-2030 se somete a la ratificación de la "
                     "primera Asamblea; el calendario completo rige desde el ejercicio 2027.") if transitoria else None}


def registrar_hito(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, actividad: int, fecha: Any,
                   documento_ref: str) -> dict[str, Any]:
    """Registra el cumplimiento de un hito del calendario, en su orden y por
    el responsable fijado en el manual. El hito 1 archiva la Hoja de
    supuestos FIN-TES-09 con los parámetros vigentes."""
    _entero(actividad, "actividad", 1, len(ACTIVIDADES_CALENDARIO))
    numero, descripcion, clave, rol, (recurso, accion), documento = ACTIVIDADES_CALENDARIO[actividad - 1]
    seguridad.exigir_permiso(con, sesion, recurso, accion)
    if sesion.rol != rol:
        raise AutorizacionError(f"La actividad «{descripcion}» corresponde a {rol} (Manual Presupuestario §2.2).")
    f = a_fecha(fecha)
    ref = _texto(documento_ref, f"la referencia del documento ({documento})", 200)
    with transaccion(con):
        _ejercicio(con, ejercicio)
        if con.execute("SELECT 1 FROM hitos_presupuesto WHERE ejercicio = ? AND actividad = ?",
                       (ejercicio, actividad)).fetchone():
            raise ReglaNegocioError("El hito ya está registrado.", codigo="YA_REGISTRADO")
        if actividad > 1 and not con.execute("SELECT 1 FROM hitos_presupuesto WHERE ejercicio = ? AND actividad = ?",
                                             (ejercicio, actividad - 1)).fetchone():
            raise ReglaNegocioError(f"Registre primero la actividad {actividad - 1} del calendario.",
                                    codigo="SECUENCIA_CALENDARIO")
        limite = _limite_hito(con, ejercicio, clave)
        detalle = None
        if actividad == 1:
            detalle = auditoria.a_json([{k: str(v) if v is not None else None for k, v in s.items()}
                                        for s in _supuestos_sin_permiso(con, ejercicio)])
        hid = int(con.execute(
            """INSERT INTO hitos_presupuesto (ejercicio, actividad, fecha, limite, en_plazo, documento_ref, detalle_json,
                   registrado_por, rol, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, actividad, f.isoformat(), limite.isoformat(), int(f <= limite), ref, detalle,
             sesion.nombre_completo, sesion.rol, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "hitos_presupuesto", "INSERT", hid,
                            despues={"ejercicio": ejercicio, "actividad": actividad, "fecha": f, "limite": limite,
                                     "documento": ref})
    return {"id": hid, "en_plazo": f <= limite, "limite": limite.isoformat()}


def _supuestos_sin_permiso(con: sqlite3.Connection, ejercicio: int) -> list[dict[str, Any]]:
    base, actual = _params(con, 2026), _params(con, ejercicio)
    return [{"parametro": n, "unidad": u, "base": base.get(c), "valor": actual.get(c)} for n, u, c, _f in SUPUESTOS]


def alertas_presupuesto(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Hitos del calendario vencidos o por vencer, FIN-TES-12 pendientes y
    modificaciones por resolver."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    alertas: list[dict[str, Any]] = []
    for e in ejercicios(con):
        if e == min(ejercicios(con)):
            continue
        for h in calendario_presupuestario(con, sesion, e, referencia)["hitos"]:
            if h["estado"] in ("VENCIDO", "POR_VENCER") and (h["dias"] >= -60):
                alertas.append({"nivel": ROJO if h["estado"] == "VENCIDO" else AMARILLO,
                                "mensaje": f"Presupuesto {e}: {h['descripcion']} — límite {fecha_pantalla(h['limite'])} "
                                           f"({h['documento']})."})
    for e in ejercicios(con):
        p = _params(con, e)
        dias = int(_param(p, "presupuesto_informe_ciclo_dias"))
        for c in ciclos_ejercicio(con, e):
            if c["hasta"] >= referencia or (referencia - c["hasta"]).days > 120:
                continue
            if not con.execute("SELECT 1 FROM informes_ejecucion WHERE ejercicio = ? AND tipo = 'CICLO' AND numero = ?",
                               (e, c["numero"])).fetchone():
                limite = c["hasta"] + timedelta(days=dias)
                alertas.append({"nivel": ROJO if referencia > limite else AMARILLO,
                                "mensaje": f"FIN-TES-12 del ciclo {c['numero']} de {e} pendiente (límite "
                                           f"{fecha_pantalla(limite)})."})
    for m in con.execute("SELECT * FROM modificaciones_presupuesto WHERE estado = 'SOLICITADA' ORDER BY id"):
        alertas.append({"nivel": AMARILLO, "mensaje": f"FIN-TES-11 N.º {m['id']} ({PARTIDAS.get(m['partida'], {}).get('nombre', m['partida'])}, "
                                                      f"ciclo {m['ciclo']}/{m['ejercicio']}) pendiente de {m['autoridad']}."})
    return alertas


# ==========================================================================
# 3. EJECUCIÓN Y VARIACIONES (Manual E.1; FIN-TES-12)
# ==========================================================================
def ventana(con: sqlite3.Connection, ejercicio: int, tipo: str, numero: int) -> dict[str, Any]:
    """Ventana de comparación: un ciclo, un semestre (ciclos que cierran en
    jul-dic o ene-jun) o el ejercicio completo."""
    inicio, fin = _ejercicio(con, ejercicio)
    ciclos = ciclos_ejercicio(con, ejercicio)
    if tipo == "CICLO":
        elegidos = [c for c in ciclos if c["numero"] == numero]
        if not elegidos:
            raise DatosInvalidosError(f"El ejercicio {ejercicio} no tiene ciclo {numero}.")
    elif tipo == "SEMESTRE":
        _entero(numero, "semestre", 1, 2)
        corte = date(inicio.year, 12, 31)
        elegidos = [c for c in ciclos if (c["hasta"] <= corte) == (numero == 1)]
        if not elegidos:
            raise DatosInvalidosError("El semestre no tiene ciclos cerrados.")
    elif tipo == "ANUAL":
        elegidos = ciclos
    else:
        raise DatosInvalidosError("El tipo de informe debe ser CICLO, SEMESTRE o ANUAL.")
    desde = inicio if tipo == "ANUAL" else elegidos[0]["desde"]
    hasta = fin if tipo == "ANUAL" else elegidos[-1]["hasta"]
    return {"ejercicio": ejercicio, "tipo": tipo, "numero": numero if tipo != "ANUAL" else 0,
            "ciclos": [c["numero"] for c in elegidos], "desde": desde, "hasta": hasta}


def _movimiento(con: sqlite3.Connection, desde: date, hasta: date, *, prefijos: Sequence[str] = (),
                excluir_prefijos: Sequence[str] = (), centro: str | None = None, sufijos: Sequence[str] = (),
                lado: str = "NETO") -> int:
    """Movimiento (centavos) en la ventana: NETO = Debe − Haber; DEBE; HABER."""
    expr = {"NETO": "COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0)",
            "DEBE": "COALESCE(SUM(d.debe_cent), 0)", "HABER": "COALESCE(SUM(d.haber_cent), 0)"}[lado]
    sql = (f"SELECT {expr} FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id WHERE a.fecha BETWEEN ? AND ? "
           f"AND a.tipo NOT IN ({','.join('?' * len(TIPOS_EXCLUIDOS))})")
    args: list[Any] = [desde.isoformat(), hasta.isoformat(), *TIPOS_EXCLUIDOS]
    if prefijos:
        sql += " AND (" + " OR ".join("d.cuenta_codigo LIKE ?" for _ in prefijos) + ")"
        args += [p + "%" for p in prefijos]
    for p in excluir_prefijos:
        sql += " AND d.cuenta_codigo NOT LIKE ?"
        args.append(p + "%")
    if centro:
        sql += " AND d.cuenta_codigo = ?"
        args.append(centro)
    if sufijos:
        sql += f" AND d.sufijo IN ({','.join('?' * len(sufijos))})"
        args += list(sufijos)
    return int(con.execute(sql, args).fetchone()[0])


def real_partidas(con: sqlite3.Connection, desde: date, hasta: date) -> dict[str, Decimal]:
    """Montos reales del mayor en la ventana (sin asientos de apertura ni de cierre)."""
    m = lambda **kw: _movimiento(con, desde, hasta, **kw)            # noqa: E731
    centro = lambda c, s: m(centro=c, sufijos=s, lado="DEBE")        # noqa: E731
    capex = m(prefijos=("1.2.",), lado="DEBE") - m(prefijos=("1.2.03.",), lado="HABER")
    cent = {
        "ING_CARNE": -m(prefijos=("5.1.01.", "5.1.04.")),
        "ING_ALIMENTO": -m(prefijos=("5.1.02.",)),
        "ING_GALLINAZA": -m(prefijos=("5.2.01.04",)),
        "MP_POLLITOS": centro(CENTRO_CARNE, ("01",)),
        "MP_ALIMENTO": centro(CENTRO_CARNE, ("02", "11")),
        "MP_OTROS": centro(CENTRO_CARNE, _MPD_CARNE_OTROS),
        "MOD_FIJA": centro(CENTRO_CARNE, ("20", "21")),
        "MOD_EVENTUAL": centro(CENTRO_CARNE, ("22",)),
        "CIF": centro(CENTRO_CARNE, _CIF),
        "ALI_MP": centro(CENTRO_ALIMENTO, ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12",
                                           "13")),
        "ALI_MOD": centro(CENTRO_ALIMENTO, ("20", "21", "22")),
        "ALI_CIF": centro(CENTRO_ALIMENTO, _CIF),
        "GASTOS_ADM": m(prefijos=("7.1.",), excluir_prefijos=("7.1.03.",)),
        "GASTOS_VENTA": m(prefijos=("7.1.03.",)),
        "CAPEX": max(capex, 0),
    }
    return {k: de_centavos(v) for k, v in cent.items()}


def cantidades_reales_auto(con: sqlite3.Connection, desde: date, hasta: date) -> dict[str, Decimal]:
    """Cantidades reales disponibles en los registros: kg vendidos (notas de
    entrega, netas de devoluciones), qq de alimento vendidos y aves recibidas."""
    resultado: dict[str, Decimal] = {}
    kg = int(con.execute(
        "SELECT COALESCE(SUM(n.cantidad_mil), 0) - COALESCE((SELECT SUM(v.cantidad_mil) FROM devoluciones v JOIN "
        "notas_entrega x ON x.id = v.nota_id WHERE x.producto IN ('CARNE_ENTERO','CARNE_CORTES') AND x.fecha BETWEEN "
        "? AND ?), 0) FROM notas_entrega n WHERE n.producto IN ('CARNE_ENTERO','CARNE_CORTES') AND n.fecha BETWEEN ? "
        "AND ?", (desde.isoformat(), hasta.isoformat()) * 2).fetchone()[0])
    if kg > 0:
        resultado["ING_CARNE"] = de_mil(kg)
    qq = int(con.execute("SELECT COALESCE(SUM(cantidad_mil), 0) FROM notas_entrega WHERE producto = 'EXCEDENTE' AND "
                         "fecha BETWEEN ? AND ?", (desde.isoformat(), hasta.isoformat())).fetchone()[0])
    if qq > 0:
        resultado["ING_ALIMENTO"] = de_mil(qq)
    aves = int(con.execute("SELECT COALESCE(SUM(aves_iniciales), 0) FROM ciclos_produccion WHERE fecha_inicio BETWEEN "
                           "? AND ?", (desde.isoformat(), hasta.isoformat())).fetchone()[0])
    if aves > 0:
        resultado["MP_POLLITOS"] = Decimal(aves)
        resultado["MOD_EVENTUAL"] = Decimal(aves)
    return resultado


def variacion_precio_cantidad(cantidad_presupuesto: Any, precio_presupuesto: Any, cantidad_real: Any,
                              monto_real: Any) -> dict[str, Decimal]:
    """Variaciones de precio y de cantidad (Manual E.1):

    * De precio: (precio real − precio presupuestado) × cantidad real.
    * De cantidad: (cantidad real − cantidad presupuestada) × precio presupuestado.

    Ejemplo del manual (maíz, un ciclo de 2027): 208 qq a Bs 95,12 frente a
    215 qq a Bs 98,00 → precio Bs 619,20 y cantidad Bs 665,84."""
    qp, pp = _decimal(cantidad_presupuesto, "cantidad presupuestada"), _decimal(precio_presupuesto, "precio presupuestado")
    qr, mr = _decimal(cantidad_real, "cantidad real"), _decimal(monto_real, "monto real")
    if qr <= 0:
        raise DatosInvalidosError("La cantidad real debe ser mayor a cero.")
    precio_real = (mr / qr).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {"precio_real": precio_real, "variacion_precio": redondear_bs((mr / qr - pp) * qr),
            "variacion_cantidad": redondear_bs((qr - qp) * pp)}


def semaforo_partida(p: Mapping[str, Any], regla: str, presupuesto: Decimal, real: Decimal,
                     sme: Decimal | None = None) -> dict[str, Any]:
    """Semáforo de la partida con los umbrales vigentes (Manual E.1).

    * INGRESO: verde hasta −5 %; amarillo de −5 % a −10 %; rojo peor que −10 %.
    * COSTO (materia prima, CIF y gastos): verde hasta +5 %; amarillo hasta +10 %; rojo más.
    * DPF (faenado): real ÷ presupuesto; verde ≤ 100 %; amarillo hasta 105 %; rojo más.
    * CAPEX: verde hasta +15 %; rojo más (o no previsto).
    * FLUJO: verde con desvío hasta 10 % y saldo ≥ SME; rojo en otro caso.
    """
    variacion = real - presupuesto
    pct = _fraccion(variacion, presupuesto) if presupuesto else None
    # Las comparaciones usan la fracción exacta; ``pct`` se redondea solo para mostrarla.
    exacta = variacion / presupuesto if presupuesto else None
    if regla == "INGRESO":
        if not presupuesto:
            semaforo = VERDE
        elif exacta >= -Decimal(_param(p, "presupuesto_ingresos_amarillo")):
            semaforo = VERDE
        elif exacta >= -Decimal(_param(p, "presupuesto_ingresos_rojo")):
            semaforo = AMARILLO
        else:
            semaforo = ROJO
    elif regla == "COSTO":
        if not presupuesto:
            semaforo = VERDE if real == 0 else ROJO
        elif exacta <= Decimal(_param(p, "presupuesto_costos_amarillo")):
            semaforo = VERDE
        elif exacta <= Decimal(_param(p, "presupuesto_costos_rojo")):
            semaforo = AMARILLO
        else:
            semaforo = ROJO
    elif regla == "DPF":
        if not presupuesto:
            semaforo = VERDE if real == 0 else ROJO
        else:
            ratio = real / presupuesto
            if ratio <= Decimal(_param(p, "dpf_umbral_bueno")):
                semaforo = VERDE
            elif ratio <= Decimal(_param(p, "dpf_umbral_malo")):
                semaforo = AMARILLO
            else:
                semaforo = ROJO
    elif regla == "CAPEX":
        if not presupuesto:
            semaforo = VERDE if real == 0 else ROJO
        else:
            semaforo = VERDE if exacta <= Decimal(_param(p, "capex_variacion_pct")) else ROJO
    elif regla == "FLUJO":
        limite = Decimal(_param(p, "desvio_flujo_caja_alerta"))
        fuera = exacta is not None and abs(exacta) > limite
        semaforo = ROJO if fuera or (sme is not None and real < sme) else VERDE
    else:
        raise DatosInvalidosError(f"Regla de semáforo desconocida: {regla!r}.")
    return {"variacion": redondear_bs(variacion), "pct": pct, "semaforo": semaforo}


def calcular_ejecucion(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, tipo: str = "CICLO",
                       numero: int = 1, cantidades_reales: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Ejecución vs presupuesto de la ventana: por partida, variación total y
    porcentual, de precio y de cantidad (si se conoce la cantidad real) y
    semáforo; costo unitario Bs/kg y saldo final de efectivo."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    return _ejecucion(con, ejercicio, tipo, numero, cantidades_reales)


def _ejecucion(con: sqlite3.Connection, ejercicio: int, tipo: str, numero: int,
               cantidades_reales: Mapping[str, Any] | None) -> dict[str, Any]:
    v = ventana(con, ejercicio, tipo, numero)
    p = _params(con, ejercicio)
    real = real_partidas(con, v["desde"], v["hasta"])
    cantidades = cantidades_reales_auto(con, v["desde"], v["hasta"])
    for clave, valor in (cantidades_reales or {}).items():
        if clave not in PARTIDAS:
            raise DatosInvalidosError(f"Partida no válida en las cantidades reales: {clave!r}.")
        if valor is not None and str(valor).strip() != "":
            cantidades[clave] = _decimal(valor, f"cantidad real de {clave}")
    filas = []
    for partida, datos in PARTIDAS.items():
        lineas = [_linea(con, ejercicio, c, partida) for c in v["ciclos"]]
        presu = sum((l["monto"] for l in lineas), CERO)
        cant_p = sum((l["cantidad"] for l in lineas if l["cantidad"] is not None), Decimal(0)) or None
        precio_p = lineas[0]["precio"] if lineas and lineas[0]["precio"] is not None else None
        if presu == 0 and real[partida] == 0:
            continue
        sem = semaforo_partida(p, datos["regla"], presu, real[partida])
        fila = {"partida": partida, "nombre": datos["nombre"], "grupo": datos["grupo"], "regla": datos["regla"],
                "presupuesto": redondear_bs(presu), "real": real[partida], **sem,
                "cantidad_presupuesto": cant_p, "precio_presupuesto": precio_p,
                "cantidad_real": cantidades.get(partida), "precio_real": None,
                "variacion_precio": None, "variacion_cantidad": None,
                "ratio": _fraccion(real[partida], presu) if presu else None}
        if cant_p and precio_p is not None and cantidades.get(partida):
            fila.update(variacion_precio_cantidad(cant_p, precio_p, cantidades[partida], real[partida]))
        filas.append(fila)
    por = {f["partida"]: f for f in filas}
    costo_p = sum((por[k]["presupuesto"] for k in ("MP_POLLITOS", "MP_ALIMENTO", "MP_OTROS", "MOD_FIJA",
                                                     "MOD_EVENTUAL", "CIF") if k in por), CERO)
    costo_r = sum((por[k]["real"] for k in ("MP_POLLITOS", "MP_ALIMENTO", "MP_OTROS", "MOD_FIJA",
                                              "MOD_EVENTUAL", "CIF") if k in por), CERO)
    kg_p = por["ING_CARNE"]["cantidad_presupuesto"] if "ING_CARNE" in por else None
    kg_r = cantidades.get("ING_CARNE")
    unitario = {"presupuesto": (costo_p / kg_p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if kg_p else None,
                "real": (costo_r / kg_r).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if kg_r else None}
    if unitario["presupuesto"] is not None and unitario["real"] is not None:
        unitario.update(semaforo_partida(p, "COSTO", unitario["presupuesto"], unitario["real"]))
    saldo_p = saldo_proyectado_ciclo(con, ejercicio, v["ciclos"][-1])
    if tipo == "ANUAL":
        m = con.execute("SELECT monto_cent FROM presupuesto_maestro WHERE ejercicio = ? AND modulo = 'FLUJO' AND "
                        "clave = 'SALDO_FINAL'", (ejercicio,)).fetchone()
        saldo_p = de_centavos(int(m[0])) if m else saldo_p
    saldo_r = _saldo_efectivo(con, v["hasta"])
    sme = tesoreria.sme_vigente(con, v["hasta"])
    flujo = None
    if saldo_p is not None:
        flujo = {"presupuesto": saldo_p, "real": saldo_r, "sme": sme,
                 **semaforo_partida(p, "FLUJO", saldo_p, saldo_r, sme)}
    semaforos = [f["semaforo"] for f in filas] + ([flujo["semaforo"]] if flujo else [])
    return {"ejercicio": ejercicio, "tipo": tipo, "numero": v["numero"], "ciclos": v["ciclos"],
            "desde": v["desde"].isoformat(), "hasta": v["hasta"].isoformat(), "filas": filas,
            "costo_unitario": unitario, "flujo": flujo,
            "verdes": semaforos.count(VERDE), "amarillos": semaforos.count(AMARILLO), "rojos": semaforos.count(ROJO),
            "semaforo": peor(semaforos)}


def _serializable(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dict):
        return {k: _serializable(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serializable(v) for v in valor]
    return valor


def registrar_informe_ejecucion(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, tipo: str, numero: int,
                                fecha: Any, acciones: Mapping[str, Mapping[str, Any]] | None = None,
                                cantidades_reales: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """FIN-TES-12: la Unidad de Finanzas registra el informe al cierre de la
    ventana. Toda partida en rojo exige causa, responsable y acción; toda
    partida en amarillo, explicación escrita (Manual E.1)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "CREAR")
    f = a_fecha(fecha)
    acciones = acciones or {}
    with transaccion(con):
        calc = _ejecucion(con, ejercicio, tipo, numero, cantidades_reales)
        hasta = date.fromisoformat(calc["hasta"])
        if f < hasta:
            raise DatosInvalidosError(f"El informe se emite tras el cierre de la ventana ({fecha_pantalla(hasta)}).")
        if con.execute("SELECT 1 FROM informes_ejecucion WHERE ejercicio = ? AND tipo = ? AND numero = ?",
                       (ejercicio, tipo, calc["numero"])).fetchone():
            raise ReglaNegocioError("El FIN-TES-12 de esa ventana ya está registrado.", codigo="YA_REGISTRADO")
        p = _params(con, ejercicio)
        dias = int(_param(p, "presupuesto_informe_ciclo_dias" if tipo == "CICLO" else
                          "presupuesto_informe_semestre_dias"))
        limite = hasta + timedelta(days=dias)
        observadas = [(r["partida"], r["nombre"], r["semaforo"]) for r in calc["filas"] if r["semaforo"] != VERDE]
        if calc["flujo"] and calc["flujo"]["semaforo"] != VERDE:
            observadas.append(("FLUJO", "Saldo final de efectivo", calc["flujo"]["semaforo"]))
        registros = []
        for partida, nombre, semaforo in observadas:
            a = acciones.get(partida) or {}
            causa = _opcional(a.get("causa"), "causa", 1000)
            if causa is None:
                raise ReglaNegocioError(f"La partida «{nombre}» está en {semaforo.lower()}: registre la causa "
                                        "(explicación escrita).", codigo="FALTA_CAUSA")
            responsable = _opcional(a.get("responsable"), "responsable", 120)
            accion = _opcional(a.get("accion"), "acción", 1000)
            if semaforo == ROJO and (responsable is None or accion is None):
                raise ReglaNegocioError(f"La partida «{nombre}» está en rojo: registre responsable y acción "
                                        "correctiva (Manual E.1).", codigo="FALTA_ACCION")
            registros.append((partida, semaforo, causa, responsable, accion,
                              a_fecha(a["fecha_compromiso"]).isoformat() if a.get("fecha_compromiso") else None))
        iid = int(con.execute(
            """INSERT INTO informes_ejecucion (ejercicio, tipo, numero, desde, hasta, fecha, limite, fuera_de_plazo,
                   contenido_json, verdes, amarillos, rojos, elaborado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, tipo, calc["numero"], calc["desde"], calc["hasta"], f.isoformat(), limite.isoformat(),
             int(f > limite), json.dumps(_serializable(calc), ensure_ascii=False), calc["verdes"], calc["amarillos"],
             calc["rojos"], sesion.nombre_completo, _ahora_iso())).lastrowid)
        for r in registros:
            con.execute("INSERT INTO acciones_variacion (informe_id, partida, semaforo, causa, responsable, accion, "
                        "fecha_compromiso) VALUES (?,?,?,?,?,?,?)", (iid, *r))
        auditoria.registrar(con, sesion, "informes_ejecucion", "INSERT", iid,
                            despues={"ejercicio": ejercicio, "tipo": tipo, "numero": calc["numero"],
                                     "rojos": calc["rojos"], "amarillos": calc["amarillos"],
                                     "fuera_de_plazo": f > limite})
    return {"id": iid, **calc, "limite": limite.isoformat(), "fuera_de_plazo": f > limite}


def presentar_informe(con: sqlite3.Connection, sesion: Sesion, informe_id: int, fecha: Any) -> None:
    """La Gerencia General presenta el FIN-TES-12 al Consejo de Administración."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "AUTORIZAR")
    f = a_fecha(fecha)
    with transaccion(con):
        i = con.execute("SELECT * FROM informes_ejecucion WHERE id = ?", (informe_id,)).fetchone()
        if i is None:
            raise DatosInvalidosError(f"No existe el informe {informe_id}.")
        if i["presentado_en"]:
            raise ReglaNegocioError("El informe ya fue presentado.", codigo="YA_PRESENTADO")
        if f < date.fromisoformat(i["fecha"]):
            raise DatosInvalidosError("La presentación no puede ser anterior a la elaboración.")
        con.execute("UPDATE informes_ejecucion SET presentado_por = ?, presentado_en = ? WHERE id = ?",
                    (sesion.nombre_completo, f.isoformat(), informe_id))
        auditoria.registrar(con, sesion, "informes_ejecucion", "UPDATE", informe_id, antes={"presentado_en": None},
                            despues={"presentado_por": sesion.nombre_completo, "presentado_en": f})


def listar_informes(con: sqlite3.Connection, sesion: Sesion, ejercicio: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    sql, args = "SELECT * FROM informes_ejecucion", []
    if ejercicio is not None:
        sql += " WHERE ejercicio = ?"
        args.append(ejercicio)
    return [dict(r) for r in con.execute(sql + " ORDER BY ejercicio DESC, tipo, numero", args)]


def obtener_informe(con: sqlite3.Connection, sesion: Sesion, informe_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    i = con.execute("SELECT * FROM informes_ejecucion WHERE id = ?", (informe_id,)).fetchone()
    if i is None:
        raise DatosInvalidosError(f"No existe el informe {informe_id}.")
    acciones = {a["partida"]: dict(a) for a in con.execute("SELECT * FROM acciones_variacion WHERE informe_id = ?",
                                                            (informe_id,))}
    return {**dict(i), "contenido": json.loads(i["contenido_json"]), "acciones": acciones}


# ==========================================================================
# 4. MODIFICACIÓN PRESUPUESTARIA (FIN-TES-11; autoridad CI-FIN-09)
# ==========================================================================
def autoridad_modificacion(con: sqlite3.Connection, ejercicio: int, partida: str, aprobado: Decimal,
                           solicitado: Decimal, fecha: Any) -> tuple[str, list[str]]:
    """Autoridad que aprueba la modificación según la matriz CI-FIN-09.

    * CAPEX no previsto (línea sin monto aprobado): Consejo de Administración (POL-02 Art. 1).
    * Variación CAPEX > 15 % y > Bs 10.000: Asamblea General; > 15 %: se
      informa al CA, que la aprueba (POL-02 Art. 4); en otro caso, GG.
    * Partidas no CAPEX: GG hasta ``presupuesto_modificacion_gg_max``; sobre
      ese porcentaje, el Consejo de Administración, que aprueba el presupuesto.
    """
    # DISCREPANCIA: CI-FIN-09 no fija un límite para modificar partidas no
    # CAPEX; el Manual Presupuestario (P-08) remite a la matriz y el POA §5.4
    # dice que la meta del presupuesto la aprueba el CA. Se parametriza el
    # margen de la GG (``presupuesto_modificacion_gg_max``, POR VALIDAR).
    contexto = seguridad.contexto_desde_bd(con, fecha, previsto_en_plan=aprobado > 0,
                                           monto_aprobado=aprobado if aprobado > 0 else None)
    if partida == "CAPEX":
        if aprobado <= 0:
            req = seguridad.requiere_autorizacion(TipoDecision.CAPEX_NO_PREVISTO, solicitado, contexto)
            return "CA", list(req.fundamentos)
        req = seguridad.requiere_autorizacion(TipoDecision.VARIACION_CAPEX, solicitado, contexto)
        if "AG" in req.firmas:
            return "AG", list(req.fundamentos)
        if "CA" in req.informar_a:
            return "CA", list(req.fundamentos) + ["Manual Presupuestario P-08: el CA aprueba el presupuesto modificado"]
        return "GG", list(req.fundamentos) or ["POL-02 Art. 4: variación dentro del 15 %: Gerencia General"]
    p = _params(con, ejercicio)
    limite = Decimal(_param(p, "presupuesto_modificacion_gg_max"))
    if aprobado <= 0:
        return "CA", ["Partida sin monto aprobado: el Consejo de Administración aprueba el presupuesto (P-08)"]
    pct = abs(solicitado - aprobado) / aprobado
    if pct > limite:
        return "CA", [f"Variación de {pct * 100:.1f} % sobre el {limite * 100:.0f} % que puede aprobar la GG: "
                      "Consejo de Administración (P-08; POA §5.4)".replace(".", ",", 1)]
    return "GG", [f"Variación de {pct * 100:.1f} % dentro del margen de la Gerencia General".replace(".", ",", 1)]


def solicitar_modificacion(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, ciclo: int, partida: str,
                           monto_solicitado: Any, *, motivo: str, fuente_recursos: str, efecto_flujo: str,
                           fecha: Any, informe_id: int | None = None) -> dict[str, Any]:
    """FIN-TES-11 solicitado por la Gerencia General. El monto aprobado es el
    vigente de la línea (con modificaciones previas)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "AUTORIZAR")
    if partida not in PARTIDAS:
        raise DatosInvalidosError(f"Partida no válida: {partida!r}.")
    _entero(ciclo, "ciclo", 1, 12)
    solicitado = redondear_bs(_decimal(monto_solicitado, "monto solicitado"))
    if solicitado < 0:
        raise DatosInvalidosError("El monto solicitado no puede ser negativo.")
    f = a_fecha(fecha)
    textos = (_texto(motivo, "el motivo y la desviación que la origina", 1000),
              _texto(fuente_recursos, "la fuente de recursos", 300), _texto(efecto_flujo, "el efecto en el flujo y el SME",
                                                                             500))
    with transaccion(con):
        _ejercicio(con, ejercicio)
        if con.execute("SELECT 1 FROM modificaciones_presupuesto WHERE ejercicio = ? AND ciclo = ? AND partida = ? AND "
                       "estado = 'SOLICITADA'", (ejercicio, ciclo, partida)).fetchone():
            raise ReglaNegocioError("Ya hay una modificación pendiente para esa partida y ciclo.",
                                    codigo="MODIFICACION_PENDIENTE")
        if informe_id is not None and con.execute("SELECT 1 FROM informes_ejecucion WHERE id = ?",
                                                  (informe_id,)).fetchone() is None:
            raise DatosInvalidosError(f"No existe el FIN-TES-12 {informe_id}.")
        aprobado = _linea(con, ejercicio, ciclo, partida)["monto"]
        if solicitado == aprobado:
            raise DatosInvalidosError("El monto solicitado es igual al aprobado.")
        autoridad, fundamentos = autoridad_modificacion(con, ejercicio, partida, aprobado, solicitado, f)
        pct = _fraccion(solicitado - aprobado, aprobado) if aprobado else None
        mid = int(con.execute(
            """INSERT INTO modificaciones_presupuesto (ejercicio, ciclo, partida, concepto, monto_aprobado_cent,
                   monto_solicitado_cent, variacion_pct, motivo, informe_id, fuente_recursos, efecto_flujo, autoridad,
                   fundamentos, fecha, solicitado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, ciclo, partida, PARTIDAS[partida]["nombre"], a_centavos(aprobado), a_centavos(solicitado),
             str(pct) if pct is not None else None, textos[0], informe_id, textos[1], textos[2], autoridad,
             json.dumps(fundamentos, ensure_ascii=False), f.isoformat(), sesion.nombre_completo,
             _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "modificaciones_presupuesto", "INSERT", mid,
                            despues={"partida": partida, "ciclo": ciclo, "aprobado": aprobado,
                                     "solicitado": solicitado, "autoridad": autoridad})
    return {"id": mid, "autoridad": autoridad, "fundamentos": fundamentos, "aprobado": aprobado,
            "solicitado": solicitado, "variacion_pct": pct}


def verificar_modificacion(con: sqlite3.Connection, sesion: Sesion, modificacion_id: int) -> None:
    """La Unidad de Finanzas verifica la solicitud (bloque «Verificado por»)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "EDITAR")
    if sesion.rol != "JF":
        raise AutorizacionError("La verificación del FIN-TES-11 corresponde a la Unidad de Finanzas.")
    with transaccion(con):
        m = _modificacion(con, modificacion_id)
        if m["estado"] != "SOLICITADA" or m["verificado_por"]:
            raise ReglaNegocioError("La solicitud no está pendiente de verificación.", codigo="ESTADO_INVALIDO")
        con.execute("UPDATE modificaciones_presupuesto SET verificado_por = ? WHERE id = ?",
                    (sesion.nombre_completo, modificacion_id))
        auditoria.registrar(con, sesion, "modificaciones_presupuesto", "UPDATE", modificacion_id,
                            antes={"verificado_por": None}, despues={"verificado_por": sesion.nombre_completo})


def _modificacion(con: sqlite3.Connection, modificacion_id: int) -> sqlite3.Row:
    m = con.execute("SELECT * FROM modificaciones_presupuesto WHERE id = ?", (modificacion_id,)).fetchone()
    if m is None:
        raise DatosInvalidosError(f"No existe la modificación {modificacion_id}.")
    return m


def resolver_modificacion(con: sqlite3.Connection, sesion: Sesion, modificacion_id: int, aprobar: bool, fecha: Any,
                          acta_ref: str | None = None, observacion: str | None = None) -> str:
    """Resuelve la solicitud la autoridad que fija CI-FIN-09: la GG
    (presupuesto/AUTORIZAR), el Consejo de Administración o la Asamblea
    (presupuesto/APROBAR, con acta)."""
    f = a_fecha(fecha)
    with transaccion(con):
        m = _modificacion(con, modificacion_id)
        if m["estado"] != "SOLICITADA":
            raise ReglaNegocioError("La solicitud ya fue resuelta.", codigo="ESTADO_INVALIDO")
        if not m["verificado_por"]:
            raise ReglaNegocioError("La Unidad de Finanzas debe verificar la solicitud antes de resolverla.",
                                    codigo="SIN_VERIFICAR")
        accion = "AUTORIZAR" if m["autoridad"] == "GG" else "APROBAR"
        seguridad.exigir_permiso(con, sesion, "presupuesto", accion)
        if sesion.rol != m["autoridad"]:
            raise AutorizacionError(f"Según CI-FIN-09 esta modificación la resuelve {m['autoridad']}.")
        acta = _opcional(acta_ref, "el acta", 120)
        if m["autoridad"] in ("CA", "AG") and acta is None:
            raise DatosInvalidosError("Indique el acta del órgano que resuelve.")
        nota = _opcional(observacion, "observación", 1000)
        if not aprobar and nota is None:
            raise DatosInvalidosError("Indique el motivo del rechazo.")
        if aprobar:
            vigente = _linea(con, int(m["ejercicio"]), int(m["ciclo"]), m["partida"])["monto"]
            if a_centavos(vigente) != int(m["monto_aprobado_cent"]):
                raise ReglaNegocioError("El monto vigente de la partida cambió desde la solicitud: tramite una nueva.",
                                        codigo="MONTO_DESACTUALIZADO")
        estado = "APROBADA" if aprobar else "RECHAZADA"
        con.execute("UPDATE modificaciones_presupuesto SET estado = ?, resuelto_por = ?, resuelto_en = ?, acta_ref = ?, "
                    "observacion = ? WHERE id = ?", (estado, sesion.nombre_completo, f.isoformat(), acta, nota,
                                                     modificacion_id))
        auditoria.registrar(con, sesion, "modificaciones_presupuesto", "AUTORIZACION", modificacion_id,
                            antes={"estado": "SOLICITADA"},
                            despues={"estado": estado, "resuelto_por": sesion.nombre_completo, "acta": acta})
    return estado


def listar_modificaciones(con: sqlite3.Connection, sesion: Sesion, ejercicio: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    sql, args = "SELECT * FROM modificaciones_presupuesto", []
    if ejercicio is not None:
        sql += " WHERE ejercicio = ?"
        args.append(ejercicio)
    return [{**dict(m), "aprobado": de_centavos(int(m["monto_aprobado_cent"])),
             "solicitado": de_centavos(int(m["monto_solicitado_cent"])), "fundamentos": json.loads(m["fundamentos"])}
            for m in con.execute(sql + " ORDER BY id DESC", args)]


# ==========================================================================
# 5. POA: ACTIVIDADES POR TRIMESTRE Y CUMPLIMIENTO FÍSICO
# ==========================================================================
def trimestre_ejercicio(con: sqlite3.Connection, ejercicio: int, trimestre: int) -> tuple[date, date]:
    """Trimestre del ejercicio: 1 jul-sep, 2 oct-dic, 3 ene-mar, 4 abr-jun."""
    _entero(trimestre, "trimestre", 1, 4)
    inicio, _ = _ejercicio(con, ejercicio)
    mes = inicio.month + 3 * (trimestre - 1)
    anio, mes = inicio.year + (mes - 1) // 12, (mes - 1) % 12 + 1
    sig = mes + 3
    sig_anio, sig_mes = anio + (sig - 1) // 12, (sig - 1) % 12 + 1
    return date(anio, mes, 1), date(sig_anio, sig_mes, 1) - timedelta(days=1)


def _trimestres(texto: str) -> list[int]:
    valores = sorted({int(t) for t in texto.split(",") if t.strip()})
    if not valores or any(t < 1 or t > 4 for t in valores):
        raise DatosInvalidosError("Los trimestres programados deben estar entre 1 y 4.")
    return valores


def registrar_actividad(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, codigo: str, area: str,
                        descripcion: str, indicador: str, responsable: str, trimestres: Sequence[int]) -> int:
    """Agrega una actividad al POA del ejercicio (Matriz de programación)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "CREAR")
    if area not in AREAS_POA:
        raise DatosInvalidosError(f"Área no válida: {area!r}.")
    programados = _trimestres(",".join(str(_entero(t, "trimestre", 1, 4)) for t in trimestres))
    with transaccion(con):
        _ejercicio(con, ejercicio)
        cod = _texto(codigo, "el código", 10).upper()
        if con.execute("SELECT 1 FROM poa_actividades WHERE ejercicio = ? AND codigo = ?", (ejercicio, cod)).fetchone():
            raise ReglaNegocioError("Ya existe una actividad con ese código.", codigo="YA_REGISTRADO")
        aid = int(con.execute(
            """INSERT INTO poa_actividades (ejercicio, codigo, area, descripcion, indicador, responsable, trimestres,
                   fuente, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, cod, area, _texto(descripcion, "la descripción", 300), _texto(indicador, "el indicador", 300),
             _texto(responsable, "el responsable", 120), ",".join(str(t) for t in programados),
             "Matriz de seguimiento del POA", sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "poa_actividades", "INSERT", aid,
                            despues={"ejercicio": ejercicio, "codigo": cod, "trimestres": programados})
    return aid


def registrar_avance(con: sqlite3.Connection, sesion: Sesion, actividad_id: int, trimestre: int, fecha: Any,
                     estado: str, evidencia: str | None = None, observacion: str | None = None) -> dict[str, Any]:
    """Avance trimestral de una actividad. Cuenta como cumplida en plazo si se
    marca CUMPLIDA con evidencia hasta el último día del trimestre."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "CREAR")
    if estado not in ESTADOS_AVANCE:
        raise DatosInvalidosError("El estado debe ser CUMPLIDA, PARCIAL o NO_CUMPLIDA.")
    f = a_fecha(fecha)
    prueba = _opcional(evidencia, "evidencia", 300)
    if estado == "CUMPLIDA" and prueba is None:
        raise DatosInvalidosError("Una actividad cumplida requiere su evidencia (documento o formato).")
    with transaccion(con):
        a = con.execute("SELECT * FROM poa_actividades WHERE id = ?", (actividad_id,)).fetchone()
        if a is None:
            raise DatosInvalidosError(f"No existe la actividad {actividad_id}.")
        if trimestre not in _trimestres(a["trimestres"]):
            raise ReglaNegocioError(f"La actividad {a['codigo']} no está programada en el trimestre {trimestre}.",
                                    codigo="NO_PROGRAMADA")
        if con.execute("SELECT 1 FROM poa_avances WHERE actividad_id = ? AND trimestre = ?",
                       (actividad_id, trimestre)).fetchone():
            raise ReglaNegocioError("El avance de ese trimestre ya está registrado.", codigo="YA_REGISTRADO")
        desde, hasta = trimestre_ejercicio(con, int(a["ejercicio"]), trimestre)
        if f < desde:
            raise DatosInvalidosError("El avance no puede registrarse antes del inicio del trimestre.")
        en_plazo = estado == "CUMPLIDA" and f <= hasta
        vid = int(con.execute(
            """INSERT INTO poa_avances (actividad_id, trimestre, fecha, estado, en_plazo, evidencia, observacion,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (actividad_id, trimestre, f.isoformat(), estado, int(en_plazo), prueba,
             _opcional(observacion, "observación", 500), sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "poa_avances", "INSERT", vid,
                            despues={"actividad": a["codigo"], "trimestre": trimestre, "estado": estado,
                                     "en_plazo": en_plazo})
    return {"id": vid, "en_plazo": en_plazo}


def listar_poa(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    filas = []
    for a in con.execute("SELECT * FROM poa_actividades WHERE ejercicio = ? ORDER BY area, id", (ejercicio,)):
        avances = {int(v["trimestre"]): dict(v) for v in con.execute(
            "SELECT * FROM poa_avances WHERE actividad_id = ?", (a["id"],))}
        filas.append({**dict(a), "programados": _trimestres(a["trimestres"]), "avances": avances,
                      "area_txt": AREAS_POA[a["area"]]})
    return filas


def semaforo_poa(p: Mapping[str, Any], fraccion: Decimal | None) -> str | None:
    """Cumplimiento físico: verde ≥ 90 %; amarillo 75 % a 89 %; rojo < 75 %."""
    if fraccion is None:
        return None
    if fraccion >= Decimal(_param(p, "poa_cumplimiento_meta")):
        return VERDE
    return AMARILLO if fraccion >= Decimal(_param(p, "poa_cumplimiento_rojo")) else ROJO


def cumplimiento_poa(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any = None) -> dict[str, Any]:
    """Cumplimiento físico del POA (actividades cumplidas en plazo ÷
    programadas) por trimestre, por área y acumulado de los trimestres ya
    vencidos a la fecha; un área bajo el umbral rojo exige plan de recuperación."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    p = _params(con, ejercicio)
    actividades = listar_poa(con, sesion, ejercicio)
    trimestres = []
    acumulado_prog = acumulado_cump = 0
    areas: dict[str, list[int]] = {}
    for t in range(1, 5):
        desde, hasta = trimestre_ejercicio(con, ejercicio, t)
        programadas = [a for a in actividades if t in a["programados"]]
        cumplidas = [a for a in programadas if a["avances"].get(t, {}).get("en_plazo")]
        vencido = referencia > hasta
        frac = _fraccion(Decimal(len(cumplidas)), Decimal(len(programadas))) if programadas else None
        trimestres.append({"trimestre": t, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
                           "programadas": len(programadas), "cumplidas": len(cumplidas), "cumplimiento": frac,
                           "vencido": vencido, "semaforo": semaforo_poa(p, frac) if vencido else None})
        if vencido:
            acumulado_prog += len(programadas)
            acumulado_cump += len(cumplidas)
            for a in programadas:
                par = areas.setdefault(a["area"], [0, 0])
                par[0] += 1
                par[1] += int(bool(a["avances"].get(t, {}).get("en_plazo")))
    frac_total = _fraccion(Decimal(acumulado_cump), Decimal(acumulado_prog)) if acumulado_prog else None
    por_area = []
    for area, (prog, cump) in areas.items():
        frac = _fraccion(Decimal(cump), Decimal(prog))
        sem = semaforo_poa(p, frac)
        por_area.append({"area": area, "area_txt": AREAS_POA[area], "programadas": prog, "cumplidas": cump,
                         "cumplimiento": frac, "semaforo": sem, "plan_recuperacion": sem == ROJO})
    return {"ejercicio": ejercicio, "trimestres": trimestres, "areas": por_area, "programadas": acumulado_prog,
            "cumplidas": acumulado_cump, "cumplimiento": frac_total, "semaforo": semaforo_poa(p, frac_total),
            "meta": Decimal(_param(p, "poa_cumplimiento_meta"))}


# ==========================================================================
# 6. ALIMENTO PROPIO FRENTE A COMPRADO (D.6)
# ==========================================================================
def alimento_propio_vs_comprado(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """D.6: presupuesto del manual y real del ejercicio (consumo propio del
    kárdex de alimento valorado al precio de compra vigente, costo propio
    transferido y margen del excedente vendido según el mayor)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    inicio, fin = _ejercicio(con, ejercicio)
    presu = {r["clave"]: r for r in con.execute(
        "SELECT * FROM presupuesto_maestro WHERE ejercicio = ? AND modulo = 'D6'", (ejercicio,))}
    # Manual D.6: precio de compra de 2026 sin indexar (último valor vigente hasta el ejercicio).
    fila_precio = con.execute("SELECT valor FROM parametros WHERE clave = 'precio_alimento_comprado_qq' AND "
                              "ejercicio_desde <= ? ORDER BY ejercicio_desde DESC LIMIT 1", (ejercicio,)).fetchone()
    if fila_precio is None:
        raise ReglaNegocioError("Falta el parámetro «precio_alimento_comprado_qq» (regla 2).",
                                codigo="PARAMETRO_FALTANTE")
    precio = Decimal(str(fila_precio[0]))
    fila = con.execute(
        "SELECT COALESCE(SUM(cantidad_mil), 0), COALESCE(SUM(valor_cent), 0) FROM kardex_terminados WHERE "
        "producto = 'ALIMENTO' AND tipo = 'SALIDA_CONSUMO_PROPIO' AND fecha BETWEEN ? AND ?",
        (inicio.isoformat(), fin.isoformat())).fetchone()
    qq, costo_propio = de_mil(int(fila[0])), de_centavos(int(fila[1]))
    costo_compra = redondear_bs(qq * precio)
    ahorro = costo_compra - costo_propio
    ventas = de_centavos(-_movimiento(con, inicio, fin, prefijos=("5.1.02.",)))
    costo_ventas = de_centavos(_movimiento(con, inicio, fin, prefijos=("6.1.02.02",)))
    margen = ventas - costo_ventas

    def pm(clave: str) -> Decimal | None:
        r = presu.get(clave)
        if r is None:
            return None
        if r["valor"] is not None:
            return Decimal(r["valor"])
        if r["cantidad_mil"] is not None and not int(r["monto_cent"]):
            return de_mil(int(r["cantidad_mil"]))
        return de_centavos(int(r["monto_cent"]))

    presupuesto = {k: pm(k) for k in ("CONSUMO_QQ", "COSTO_COMPRA", "COSTO_PROPIO", "UNITARIO_PROPIO", "AHORRO",
                                      "AHORRO_PCT", "MARGEN_EXCEDENTE", "BENEFICIO_TOTAL")}
    real = {"CONSUMO_QQ": qq, "COSTO_COMPRA": costo_compra, "COSTO_PROPIO": costo_propio,
            "UNITARIO_PROPIO": (costo_propio / qq).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if qq else None,
            "AHORRO": ahorro, "AHORRO_PCT": _fraccion(ahorro, costo_compra) if costo_compra else None,
            "MARGEN_EXCEDENTE": margen, "BENEFICIO_TOTAL": ahorro + margen}
    conceptos = {r["clave"]: r["concepto"] for r in presu.values()}
    return {"ejercicio": ejercicio, "precio_compra": precio, "presupuesto": presupuesto, "real": real,
            "conceptos": conceptos, "aplica": bool(presupuesto.get("COSTO_PROPIO"))}


def resumen_presupuesto(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """Totales del ejercicio: presupuesto vigente, real y semáforo general."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    calc = _ejecucion(con, ejercicio, "ANUAL", 0, None)
    return {"ejecucion": calc, "modificaciones": len(listar_modificaciones(con, sesion, ejercicio)),
            "informes": len(listar_informes(con, sesion, ejercicio))}

