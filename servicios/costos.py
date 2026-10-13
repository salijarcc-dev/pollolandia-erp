"""Costos matriciales del ERP Pollolandia (Fase 7, Ciclo 3 — Transformación).

Implementa PC-03, PC-04, PC-07, PR-FIN-06, CI-FIN-03, el Manual del Sistema de
Costos (§6 a §9) y el Manual Presupuestario (D.4, E.1), con los formatos
FIN-TRA-03, FIN-TRA-06 y FIN-TRA-07:

1. Acumulación diaria en el centro 6.1.01.01 (carne) y, desde 2027, en el
   6.1.01.02 (alimento) por sufijo (estructura del Plan de Cuentas v3).
2. Consumo de inventarios al CPP: alimento comprado (02), veterinarios (08),
   limpieza (09), crianza (10), bolsas (12) y materias primas del alimento
   (03-07); conversión de los kilos de la bitácora a quintales del kárdex.
3. Alimento propio: producción (6.1.01.02 a 1.1.03.13), traslado del consumo
   propio al sufijo 11 contra 1.1.03.13 y ensaquillado del excedente con el
   sufijo 13 (saquillos de yute).
4. CIF de los sufijos 30-37 (depreciaciones, mantenimiento, energía) con
   prorrateo carne/alimento por horas (CI-FIN-03) para los sufijos comunes.
5. Mano de obra (20, 21, 22, 38, 39): la acumulan las planillas (Fase 5); al
   cierre se devengan los días del ciclo no cubiertos por planilla y se
   revierten el día siguiente (corte de MOD).
6. Mayor analítico por sufijo (FIN-TRA-03).
7. Cierre del día 73: 1.1.03.12 contra 6.1.01.01 por el total acumulado,
   dejando el centro en cero; costo unitario Bs/kg (FIN-TRA-07) con variación
   frente al ciclo anterior; Informe de Costo de Ciclo (FIN-TRA-06) con la
   conciliación de aves y la aprobación de la Gerencia General.
8. Costo de ventas al vender: 6.1.02.01 contra 1.1.03.12 (carne) y 6.1.02.02
   contra 1.1.03.13 (alimento excedente), al CPP del producto terminado.
9. Comparación contra el presupuesto D.4 por sufijo con variaciones de precio
   y de cantidad (Manual Presupuestario E.1).

Cifras y umbrales se leen de ``parametros``; la estructura de cuentas y
sufijos, de las tablas ``sufijos_costo`` y ``sufijo_centro``. No importa
``streamlit``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion
from servicios import auditoria, contable, inventarios, planillas, produccion, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil
from servicios.seguridad import Sesion
from utils.formato import a_fecha, fecha_pantalla, formato_bs, redondear_bs

# --------------------------------------------------------------------------
# Estructura del Plan de Cuentas v3 (no cifras)
# --------------------------------------------------------------------------
CENTRO_CARNE = "6.1.01.01"
CENTRO_ALIMENTO = "6.1.01.02"
CENTROS = (CENTRO_CARNE, CENTRO_ALIMENTO)
PT_CARNE = "1.1.03.12"
# DISCREPANCIA: el Manual de Gestión Contable (cuenta 6.1.01.02) dice que el
# excedente se transfiere a 1.1.03.14, que en el Plan v3 es «Subproductos:
# Gallinaza». El Manual de Costos (§8.2), la tabla de sufijos (11 → 1.1.03.13)
# y la Descripción de Cuentas usan 1.1.03.13. Se aplica 1.1.03.13.
PT_ALIMENTO = "1.1.03.13"
COSTO_VENTA = {"CARNE": "6.1.02.01", "EXCEDENTE": "6.1.02.02"}
CUENTA_PRODUCTO = {"CARNE": PT_CARNE, "ALIMENTO": PT_ALIMENTO, "EXCEDENTE": PT_ALIMENTO}
UNIDAD_PRODUCTO = {"CARNE": "kg", "ALIMENTO": "qq", "EXCEDENTE": "qq"}
NOMBRE_PRODUCTO = {"CARNE": "Carne de pollo", "ALIMENTO": "Alimento balanceado (consumo propio)",
                   "EXCEDENTE": "Alimento balanceado excedente ensaquillado"}
INSUMO_ALIMENTO_COMPRADO = "INS-0302"
INSUMO_BOLSAS = "INS-0311"
INSUMO_SAQUILLOS = "INS-0311S"
SUFIJO_ALIMENTO_COMPRADO = "02"
SUFIJO_ALIMENTO_PROPIO = "11"
SUFIJO_SAQUILLOS = "13"
SUFIJOS_MOD_FIJA = {"salario": "20", "aguinaldo": "21", "aporte_patronal": "38", "indemnizacion": "39"}
PASIVO_MOD = {"salario": "2.1.02.01", "aguinaldo": "2.1.02.02", "aporte_patronal": "2.1.02.03",
              "indemnizacion": "2.2.01.01"}
ORIGEN_CIERRE = "COSTOS_CIERRE"
ELEMENTOS = ("MPD", "MOD", "CIF", "PERDIDA")
NOMBRE_ELEMENTO = {"MPD": "Materia Prima Directa", "MOD": "Mano de Obra Directa",
                   "CIF": "Costos Indirectos de Fabricación", "PERDIDA": "Pérdidas normales (sufijos 40-41)"}
CERO = Decimal("0.00")
TASA = Decimal("0.0001")
CIEN = Decimal(100)


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


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _exigir_finanzas(con: sqlite3.Connection, sesion: Sesion, accion: str = "EDITAR") -> None:
    """El costeo lo registra la Unidad de Finanzas y Comercialización (MOF)."""
    seguridad.exigir_permiso(con, sesion, "costos", accion)
    if sesion.rol != "JF":
        raise AutorizacionError("El costeo lo registra la Unidad de Finanzas y Comercialización.",
                                codigo="SOLO_FINANZAS")


def _exigir_gerencia(con: sqlite3.Connection, sesion: Sesion) -> None:
    seguridad.exigir_permiso(con, sesion, "costos", "AUTORIZAR")
    if sesion.rol != "GG":
        raise AutorizacionError("La aprobación corresponde a la Gerencia General.", codigo="SOLO_GERENCIA")


def alimento_propio_vigente(con: sqlite3.Connection, fecha: Any) -> bool:
    """``True`` desde el ejercicio de inicio del alimento propio (2027)."""
    f = a_fecha(fecha)
    return ejercicio_de_fecha_bd(con, f) >= int(_param(_params(con, f), "ejercicio_inicio_alimento_propio"))


def pct_texto(valor: Any) -> str:
    return "—" if valor is None else f"{(Decimal(str(valor)) * CIEN).quantize(Decimal('0.01'))} %".replace(".", ",")


# ==========================================================================
# 0. CATÁLOGO DE SUFIJOS (Plan de Cuentas v3, Grupo 6)
# ==========================================================================
def catalogo_sufijos(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Sufijos con nombre, elemento del costo, centros y contrapartidas."""
    salida: dict[str, dict[str, Any]] = {}
    for s in con.execute("SELECT * FROM sufijos_costo ORDER BY sufijo"):
        centros = [r[0] for r in con.execute("SELECT centro_costo FROM sufijo_centro WHERE sufijo = ? ORDER BY 1",
                                             (s["sufijo"],))]
        salida[s["sufijo"]] = {"sufijo": s["sufijo"], "nombre": s["nombre"], "elemento": s["elemento"],
                               "centros": centros, "contrapartida": s["cuenta_contrapartida"],
                               "contrapartida_2": s["cuenta_contrapartida_2"], "uso_desde_2027": bool(s["uso_desde_2027"])}
    return salida


def _validar_sufijo(con: sqlite3.Connection, centro: str, sufijo: str, fecha: Any) -> dict[str, Any]:
    cat = catalogo_sufijos(con)
    if sufijo not in cat:
        raise DatosInvalidosError(f"No existe el sufijo {sufijo}.")
    s = cat[sufijo]
    if centro not in s["centros"]:
        raise ReglaNegocioError(f"El sufijo {sufijo} no corresponde al centro {centro}.", codigo="SUFIJO_CENTRO")
    if (s["uso_desde_2027"] or centro == CENTRO_ALIMENTO) and not alimento_propio_vigente(con, fecha):
        raise ReglaNegocioError(f"El sufijo {sufijo} del centro {centro} rige desde el ejercicio del alimento propio.",
                                codigo="SUFIJO_NO_VIGENTE")
    return s


def _elemento(cat: Mapping[str, Mapping[str, Any]], sufijo: str | None) -> str:
    if sufijo is None or sufijo not in cat:
        return "MPD"
    return str(cat[sufijo]["elemento"])


# ==========================================================================
# 1. SALDOS Y MAYOR ANALÍTICO POR SUFIJO (FIN-TRA-03)
# ==========================================================================
def saldos_centro(con: sqlite3.Connection, centro: str, hasta: Any, desde: Any = None,
                  excluir_cierres: bool = False) -> dict[str, Decimal]:
    """Saldo del centro por sufijo (Debe − Haber) en el rango de fechas."""
    sql = ("SELECT d.sufijo, COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0) AS saldo "
           "FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id WHERE d.cuenta_codigo = ? AND a.fecha <= ?")
    args: list[Any] = [centro, a_fecha(hasta).isoformat()]
    if desde is not None:
        sql += " AND a.fecha >= ?"
        args.append(a_fecha(desde).isoformat())
    if excluir_cierres:
        sql += " AND a.origen <> ?"
        args.append(ORIGEN_CIERRE)
    filas = con.execute(sql + " GROUP BY d.sufijo ORDER BY d.sufijo", args).fetchall()
    return {f["sufijo"]: de_centavos(int(f["saldo"])) for f in filas if int(f["saldo"]) != 0}


def _ciclo(con: sqlite3.Connection, ciclo_id: int) -> sqlite3.Row:
    if isinstance(ciclo_id, bool) or not isinstance(ciclo_id, int):
        raise DatosInvalidosError("Indique el ciclo.")
    c = con.execute("SELECT * FROM ciclos_produccion WHERE id = ?", (ciclo_id,)).fetchone()
    if c is None:
        raise DatosInvalidosError(f"No existe el ciclo {ciclo_id}.")
    return c


def _cierre(con: sqlite3.Connection, ciclo_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM costos_ciclo WHERE ciclo_id = ?", (ciclo_id,)).fetchone()


def ventana_ciclo(con: sqlite3.Connection, ciclo_id: int, hasta: Any = None) -> tuple[date, date]:
    """Rango de fechas del ciclo: desde la recepción de los pollitos hasta su
    cierre de costos (o la fecha indicada)."""
    c = _ciclo(con, ciclo_id)
    desde = a_fecha(c["fecha_inicio"])
    cierre = _cierre(con, ciclo_id)
    if cierre is not None:
        fin = a_fecha(cierre["fecha_cierre"])
        if hasta is not None:
            fin = min(fin, a_fecha(hasta))
    else:
        fin = a_fecha(hasta) if hasta is not None else date.today()
    return desde, fin


def mayor_analitico(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, hasta: Any = None) -> dict[str, Any]:
    """Mayor Analítico por Sufijo (FIN-TRA-03) del centro 6.1.01.01 en el
    ciclo: acumulado por sufijo y elemento, antes del traslado del cierre.
    Las líneas se imputan al ciclo con ``produccion.lineas_centro_ciclo``."""
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    desde, fin = ventana_ciclo(con, ciclo_id, hasta)
    cat = catalogo_sufijos(con)
    movimientos = produccion.lineas_centro_ciclo(con, ciclo_id, fin)
    saldos: dict[str, Decimal] = {}
    for m in movimientos:
        saldos[m["sufijo"]] = saldos.get(m["sufijo"], CERO) + m["debe"] - m["haber"]
    filas = [{"sufijo": s, "nombre": cat[s]["nombre"] if s in cat else "—", "elemento": _elemento(cat, s),
              "monto": v} for s, v in sorted(saldos.items()) if v != 0]
    por_elemento = {e: sum((f["monto"] for f in filas if f["elemento"] == e), CERO) for e in ELEMENTOS}
    return {"desde": desde, "hasta": fin, "filas": filas, "por_elemento": por_elemento,
            "total": sum((f["monto"] for f in filas), CERO), "movimientos": movimientos}


# ==========================================================================
# 2. CONSUMO DE INVENTARIOS AL CPP (PC-07; Manual de Costos §6.1)
# ==========================================================================
def _ciclo_de_fecha(con: sqlite3.Connection, fecha: date) -> sqlite3.Row | None:
    """Ciclo en curso a la fecha (desde su recepción y aún sin cierre de costos)."""
    return con.execute(
        """SELECT c.* FROM ciclos_produccion c WHERE c.fecha_inicio <= ?
              AND NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = c.id)
            ORDER BY c.fecha_inicio DESC LIMIT 1""", (fecha.isoformat(),)).fetchone()


def _exigir_ciclo_abierto(con: sqlite3.Connection, ciclo_id: int | None, fecha: date) -> int | None:
    if ciclo_id is None:
        c = _ciclo_de_fecha(con, fecha)
        return int(c["id"]) if c else None
    c = _ciclo(con, ciclo_id)
    if _cierre(con, ciclo_id) is not None:
        raise ReglaNegocioError(f"El ciclo {c['numero']} del ejercicio {c['ejercicio']} ya tiene su cierre de costos.",
                                codigo="CICLO_COSTEADO")
    if fecha < a_fecha(c["fecha_inicio"]):
        raise ReglaNegocioError("La fecha es anterior a la recepción de los pollitos del ciclo.",
                                codigo="FECHA_ANTERIOR_CICLO")
    return ciclo_id


def consumir_insumo(con: sqlite3.Connection, sesion: Sesion, insumo_id: int, fecha: Any, cantidad: Any, *,
                    centro: str = CENTRO_CARNE, ciclo_id: int | None = None,
                    documento_ref: str | None = None) -> dict[str, Any]:
    """Consumo de un insumo del kárdex al CPP con el sufijo de su cuenta:
    alimento comprado 02, veterinarios 08, limpieza 09, crianza 10, bolsas 12
    (centro 6.1.01.01) o materias primas del alimento 03-07 (6.1.01.02).

    Returns:
        ``{id, sufijo, valor, cpp, kardex_id, asiento_id, alerta_id}``.
    """
    _exigir_finanzas(con, sesion, "CREAR")
    if centro not in CENTROS:
        raise DatosInvalidosError("El centro debe ser 6.1.01.01 o 6.1.01.02.")
    f = a_fecha(fecha)
    doc = _opcional(documento_ref, "documento", 60)
    with transaccion(con):
        aplicar_reversiones_pendientes(con, sesion)
        insumo = inventarios.obtener_insumo(con, insumo_id)
        if insumo["transferencia_inmediata"]:
            raise ReglaNegocioError("Los pollitos BB se trasladan al centro el día de la recepción (sufijo 01).",
                                    codigo="POLLITOS_SE_TRASLADAN")
        if insumo["codigo"] == INSUMO_SAQUILLOS:
            raise ReglaNegocioError("Los saquillos de yute se cargan al ensaquillar el excedente (sufijo 13).",
                                    codigo="SAQUILLOS_SOLO_EXCEDENTE")
        if insumo["codigo"] == INSUMO_ALIMENTO_COMPRADO and alimento_propio_vigente(con, f):
            raise ReglaNegocioError("Desde el alimento propio, las aves consumen el alimento producido (sufijo 11).",
                                    codigo="ALIMENTO_COMPRADO_NO_VIGENTE")
        cid = _exigir_ciclo_abierto(con, ciclo_id, f) if centro == CENTRO_CARNE else ciclo_id
        sufijos = [s for s, d in catalogo_sufijos(con).items()
                   if centro in d["centros"] and insumo["cuenta_inventario"] in (d["contrapartida"], d["contrapartida_2"])
                   and d["elemento"] == "MPD"]
        if len(sufijos) != 1:
            raise ReglaNegocioError(f"El insumo {insumo['nombre']} no tiene sufijo de consumo en el centro {centro}.",
                                    codigo="SIN_SUFIJO_CONSUMO")
        sufijo = sufijos[0]
        _validar_sufijo(con, centro, sufijo, f)
        r = inventarios.registrar_salida(con, sesion, insumo_id, f, cantidad, centro_costo=centro, sufijo=sufijo,
                                         documento_ref=doc,
                                         glosa=f"Consumo de {insumo['nombre']} al CPP — {centro}-{sufijo}")
        cur = con.execute(
            """INSERT INTO consumos_costo (ciclo_id, centro_costo, sufijo, insumo_id, fecha, cantidad_mil, valor_cent,
                   kardex_id, asiento_id, origen, documento_ref, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, centro, sufijo, insumo_id, f.isoformat(), a_mil(cantidad), a_centavos(r["valor"]), r["kardex_id"],
             r["asiento_id"], "MANUAL", doc, sesion.nombre_usuario, _ahora_iso()))
        rid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "consumos_costo", "INSERT", rid,
                            despues={"insumo": insumo["codigo"], "centro": centro, "sufijo": sufijo, "fecha": f.isoformat(),
                                     "cantidad": de_mil(a_mil(cantidad)), "valor": r["valor"], "ciclo_id": cid})
    return {"id": rid, "sufijo": sufijo, "valor": r["valor"], "cpp": r["cpp"], "kardex_id": r["kardex_id"],
            "asiento_id": r["asiento_id"], "alerta_id": r["alerta_id"]}


def consumir_alimento_bitacora(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, hasta: Any) -> dict[str, Any]:
    """Valora el alimento que la bitácora (FIN-TRA-02) registró en kg desde el
    último corte: kg ÷ ``kg_por_quintal`` = qq; antes del alimento propio sale
    del kárdex del alimento comprado (sufijo 02) y desde 2027 del inventario
    de alimento producido (sufijo 11, contra 1.1.03.13).

    Returns:
        ``{id, desde, hasta, kg, qq, sufijo, valor}``.
    """
    _exigir_finanzas(con, sesion, "CREAR")
    h = a_fecha(hasta)
    with transaccion(con):
        aplicar_reversiones_pendientes(con, sesion)
        c = _ciclo(con, ciclo_id)
        _exigir_ciclo_abierto(con, ciclo_id, h)
        ultimo = con.execute("SELECT MAX(bitacora_hasta) FROM consumos_costo WHERE ciclo_id = ? AND origen = 'BITACORA'",
                             (ciclo_id,)).fetchone()[0]
        desde = a_fecha(ultimo) + timedelta(days=1) if ultimo else a_fecha(c["fecha_inicio"])
        if h < desde:
            raise ReglaNegocioError(f"El alimento de la bitácora ya está valorado hasta el {fecha_pantalla(ultimo)}.",
                                    codigo="BITACORA_YA_VALORADA")
        kg_mil = int(con.execute(
            """SELECT COALESCE(SUM(b.alimento_kg_mil), 0) FROM bitacora_diaria b JOIN lotes l ON l.id = b.lote_id
                WHERE l.ciclo_id = ? AND b.fecha BETWEEN ? AND ?""",
            (ciclo_id, desde.isoformat(), h.isoformat())).fetchone()[0])
        if kg_mil == 0:
            raise ReglaNegocioError("La bitácora no registra alimento en el período.", codigo="SIN_ALIMENTO_BITACORA")
        kg_qq = Decimal(int(_param(_params(con, h), "kg_por_quintal")))
        qq = (de_mil(kg_mil) / kg_qq).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        glosa = (f"Alimento consumido según la bitácora del {fecha_pantalla(desde)} al {fecha_pantalla(h)}: "
                 f"{de_mil(kg_mil)} kg = {qq} qq")
        if alimento_propio_vigente(con, h):
            documento = f"FIN-TRA-02 {desde.isoformat()}/{h.isoformat()}"
            valor, _ = _valor_salida(con, "ALIMENTO", a_mil(qq))
            asiento = contable.crear_asiento(
                con, sesion, h, glosa + " (sufijo 11)",
                [{"cuenta": CENTRO_CARNE, "debe": valor, "centro_costo": CENTRO_CARNE,
                  "sufijo": SUFIJO_ALIMENTO_PROPIO}, {"cuenta": PT_ALIMENTO, "haber": valor}],
                "COSTOS", documento, tipo="TRASPASO")
            _insertar_terminado(con, sesion, "ALIMENTO", h, "SALIDA_CONSUMO_PROPIO", a_mil(qq), valor, ciclo_id,
                                documento, asiento.id)
            sufijo, kardex_id, insumo_id, producto = SUFIJO_ALIMENTO_PROPIO, None, None, "ALIMENTO"
            asiento_id = asiento.id
        else:
            insumo = con.execute("SELECT * FROM insumos WHERE codigo = ?", (INSUMO_ALIMENTO_COMPRADO,)).fetchone()
            r = inventarios.registrar_salida(con, sesion, int(insumo["id"]), h, qq, centro_costo=CENTRO_CARNE,
                                             sufijo=SUFIJO_ALIMENTO_COMPRADO,
                                             documento_ref=f"FIN-TRA-02 {desde.isoformat()}/{h.isoformat()}", glosa=glosa)
            sufijo, valor, kardex_id, insumo_id, producto = SUFIJO_ALIMENTO_COMPRADO, r["valor"], r["kardex_id"], \
                int(insumo["id"]), None
            asiento_id = r["asiento_id"]
        cur = con.execute(
            """INSERT INTO consumos_costo (ciclo_id, centro_costo, sufijo, producto, insumo_id, fecha, cantidad_mil,
                   valor_cent, kardex_id, asiento_id, origen, bitacora_desde, bitacora_hasta, kg_bitacora_mil,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ciclo_id, CENTRO_CARNE, sufijo, producto, insumo_id, h.isoformat(), a_mil(qq), a_centavos(valor), kardex_id,
             asiento_id, "BITACORA", desde.isoformat(), h.isoformat(), kg_mil, sesion.nombre_usuario, _ahora_iso()))
        rid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "consumos_costo", "INSERT", rid,
                            despues={"ciclo_id": ciclo_id, "desde": desde.isoformat(), "hasta": h.isoformat(),
                                     "kg": de_mil(kg_mil), "qq": qq, "sufijo": sufijo, "valor": valor})
    return {"id": rid, "desde": desde, "hasta": h, "kg": de_mil(kg_mil), "qq": qq, "sufijo": sufijo, "valor": valor}


def listar_consumos(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    sql = ("SELECT c.*, i.nombre AS insumo FROM consumos_costo c LEFT JOIN insumos i ON i.id = c.insumo_id WHERE 1 = 1")
    args: list[Any] = []
    if ciclo_id is not None:
        sql += " AND c.ciclo_id = ?"
        args.append(ciclo_id)
    return [{**dict(f), "cantidad": de_mil(int(f["cantidad_mil"])), "valor": de_centavos(int(f["valor_cent"])),
             "insumo": f["insumo"] or NOMBRE_PRODUCTO.get(f["producto"] or "", "")}
            for f in con.execute(sql + " ORDER BY c.fecha DESC, c.id DESC", args)]


# ==========================================================================
# 3. COSTOS INDIRECTOS DE FABRICACIÓN (sufijos 30-37; CI-FIN-03)
# ==========================================================================
def pct_carne_cif(con: sqlite3.Connection, fecha: Any) -> tuple[Decimal, str]:
    """Proporción de un CIF común (36, 37) que va a carne: 100 % antes del
    alimento propio; después, horas de crianza ÷ horas totales de las hojas
    de tiempo del mes (CI-FIN-03) o, sin hojas, el porcentaje por defecto."""
    f = a_fecha(fecha)
    if not alimento_propio_vigente(con, f):
        return Decimal(1), "DIRECTO"
    inicio = f.replace(day=1)
    fila = con.execute("SELECT COALESCE(SUM(horas_crianza), 0), COALESCE(SUM(horas_alimento), 0) FROM hojas_tiempo "
                       "WHERE fecha BETWEEN ? AND ? AND (firmada_trabajador = 1 OR regularizacion IS NOT NULL)",
                       (inicio.isoformat(), f.isoformat())).fetchone()
    total = int(fila[0]) + int(fila[1])
    if total:
        return (Decimal(int(fila[0])) / Decimal(total)).quantize(TASA, rounding=ROUND_HALF_UP), "HORAS"
    return Decimal(str(_param(_params(con, f), "prorrateo_mod_carne_defecto"))), "DEFECTO"


def registrar_cif(con: sqlite3.Connection, sesion: Sesion, sufijo: str, fecha: Any, monto: Any, concepto: str, *,
                  contrapartida: str | None = None, ciclo_id: int | None = None,
                  documento_ref: str | None = None) -> dict[str, Any]:
    """CIF del período con su sufijo (30-37) contra la cuenta que el Plan de
    Cuentas asigna al sufijo (depreciación acumulada, proveedores de
    servicios o materiales de mantenimiento). Los sufijos comunes a ambos
    centros (36, 37) se prorratean con :func:`pct_carne_cif`.

    Desde la Fase 8 la depreciación de los sufijos 30-35 la genera el
    registro de activos fijos (``servicios.activos.depreciar_mes``, FIN-TRA-10):
    si existe una ficha de activo con ese sufijo, aquí se rechaza para no
    duplicar la cuota. Sin fichas (bases anteriores a la Fase 8) se admite el
    monto del cuadro de PC-06.
    """
    _exigir_finanzas(con, sesion, "CREAR")
    f = a_fecha(fecha)
    importe = redondear_bs(monto)
    if importe <= 0:
        raise DatosInvalidosError("El monto del CIF debe ser mayor a cero.")
    texto = _texto(concepto, "concepto", 200)
    doc = _opcional(documento_ref, "documento", 60)
    cat = catalogo_sufijos(con)
    if sufijo not in cat or cat[sufijo]["elemento"] != "CIF":
        raise DatosInvalidosError("Indique un sufijo de CIF (30 a 37).")
    s = cat[sufijo]
    if str(s["contrapartida"] or "").startswith("4.1.02") and con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'activos'").fetchone() and con.execute(
            "SELECT 1 FROM activos WHERE sufijo = ?", (sufijo,)).fetchone():
        raise ReglaNegocioError(
            f"La depreciación del sufijo {sufijo} la registra el módulo de Activos fijos (FIN-TRA-10) a partir de "
            "las fichas de los activos; no se registra como CIF manual.", codigo="DEPRECIACION_POR_ACTIVOS")
    permitidas = [c for c in (s["contrapartida"], s["contrapartida_2"]) if c]
    cuenta = contrapartida or permitidas[0]
    if cuenta not in permitidas:
        raise ReglaNegocioError(f"El sufijo {sufijo} se registra contra {' o '.join(permitidas)}.",
                                codigo="CONTRAPARTIDA_NO_VALIDA")
    with transaccion(con):
        aplicar_reversiones_pendientes(con, sesion)
        if len(s["centros"]) == 1:
            centro = s["centros"][0]
            _validar_sufijo(con, centro, sufijo, f)
            pct, base = (Decimal(1) if centro == CENTRO_CARNE else Decimal(0)), "DIRECTO"
        else:
            pct, base = pct_carne_cif(con, f)
        carne = redondear_bs(importe * pct)
        alimento = importe - carne
        cid = _exigir_ciclo_abierto(con, ciclo_id, f) if carne > 0 else ciclo_id
        lineas = []
        if carne > 0:
            lineas.append({"cuenta": CENTRO_CARNE, "debe": carne, "centro_costo": CENTRO_CARNE, "sufijo": sufijo})
        if alimento > 0:
            _validar_sufijo(con, CENTRO_ALIMENTO, sufijo, f)
            lineas.append({"cuenta": CENTRO_ALIMENTO, "debe": alimento, "centro_costo": CENTRO_ALIMENTO, "sufijo": sufijo})
        lineas.append({"cuenta": cuenta, "haber": importe})
        asiento = contable.crear_asiento(con, sesion, f, f"{texto} — CIF sufijo {sufijo}", lineas, "COSTOS", doc,
                                         tipo="TRASPASO")
        cur = con.execute(
            """INSERT INTO cif_registrados (ciclo_id, sufijo, fecha, monto_cent, monto_carne_cent, monto_alimento_cent,
                   pct_carne, base_prorrateo, contrapartida, concepto, documento_ref, asiento_id, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, sufijo, f.isoformat(), a_centavos(importe), a_centavos(carne), a_centavos(alimento), str(pct), base,
             cuenta, texto, doc, asiento.id, sesion.nombre_usuario, _ahora_iso()))
        rid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "cif_registrados", "INSERT", rid,
                            despues={"sufijo": sufijo, "fecha": f.isoformat(), "monto": importe, "carne": carne,
                                     "alimento": alimento, "base": base, "contrapartida": cuenta})
    return {"id": rid, "asiento_id": asiento.id, "carne": carne, "alimento": alimento, "pct_carne": pct, "base": base}


def listar_cif(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    return [{**dict(f), "monto": de_centavos(int(f["monto_cent"])), "carne": de_centavos(int(f["monto_carne_cent"])),
             "alimento": de_centavos(int(f["monto_alimento_cent"]))}
            for f in con.execute("SELECT * FROM cif_registrados ORDER BY fecha DESC, id DESC")]


# ==========================================================================
# 4. CORTE DE LA MANO DE OBRA FIJA AL CIERRE (sufijos 20, 21, 38, 39)
# ==========================================================================
def calcular_corte_mod(con: sqlite3.Connection, ciclo_id: int, fecha_cierre: Any) -> dict[str, Any]:
    """Mano de obra fija del ciclo aún no devengada por las planillas.

    Las planillas (Fase 5) se devengan a fin de mes; el ciclo de 73 días corta
    a mitad de mes. Se devengan los días del ciclo posteriores al último mes
    con planilla contabilizada, a razón del costo de MOD fija por ciclo del
    Manual de Costos (``planillas.costo_mod_ciclo``) × días no cubiertos ÷
    ``dias_ciclo``. El devengo se revierte el día siguiente al cierre, cuando
    la planilla del mes entra al ciclo siguiente.
    """
    c = _ciclo(con, ciclo_id)
    cierre = a_fecha(fecha_cierre)
    inicio = a_fecha(c["fecha_inicio"])
    dias_ciclo = int(_param(_params(con, inicio), "dias_ciclo"))
    ultima = con.execute("SELECT anio, mes FROM planillas WHERE estado = 'CONTABILIZADA' ORDER BY anio DESC, mes DESC "
                         "LIMIT 1").fetchone()
    cubierto_hasta = None
    if ultima:
        fin_mes = date(int(ultima["anio"]), int(ultima["mes"]), 28)
        while (fin_mes + timedelta(days=1)).month == fin_mes.month:
            fin_mes += timedelta(days=1)
        cubierto_hasta = fin_mes
    desde = inicio if cubierto_hasta is None or cubierto_hasta < inicio else cubierto_hasta + timedelta(days=1)
    dias = max(0, (cierre - desde).days + 1) if desde <= cierre else 0
    dias = min(dias, dias_ciclo)
    lineas: list[dict[str, Any]] = []
    total = CERO
    if dias:
        for t in con.execute("SELECT * FROM personal WHERE area = 'GRANJA' AND fecha_ingreso <= ? "
                             "AND (fecha_retiro IS NULL OR fecha_retiro >= ?)", (cierre.isoformat(), desde.isoformat())):
            salario = planillas.salario_vigente(con, int(t["id"]), cierre)
            estandar = planillas.costo_mod_ciclo(con, salario, cierre)
            pct = Decimal(planillas.prorrateo_trabajador(con, t, cierre.year, cierre.month)["pct_carne"])
            for concepto, sufijo in SUFIJOS_MOD_FIJA.items():
                monto = redondear_bs(Decimal(estandar[concepto]) * dias / dias_ciclo)
                carne = redondear_bs(monto * pct)
                lineas.append({"personal": t["nombre"], "concepto": concepto, "sufijo": sufijo, "monto": monto,
                               "carne": carne, "alimento": monto - carne})
                total += monto
    return {"desde": desde if dias else None, "hasta": cierre, "dias": dias, "dias_ciclo": dias_ciclo,
            "lineas": lineas, "total": total}


def _asiento_corte(con: sqlite3.Connection, sesion: Sesion, corte: Mapping[str, Any], fecha: date, glosa: str,
                   reverso: bool, documento: str) -> int:
    debe: dict[tuple[str, str], Decimal] = {}
    haber: dict[str, Decimal] = {}
    for l in corte["lineas"]:
        for centro, parte in ((CENTRO_CARNE, l["carne"]), (CENTRO_ALIMENTO, l["alimento"])):
            if parte > 0:
                debe[(centro, l["sufijo"])] = debe.get((centro, l["sufijo"]), CERO) + parte
        concepto = next(k for k, v in SUFIJOS_MOD_FIJA.items() if v == l["sufijo"])
        haber[PASIVO_MOD[concepto]] = haber.get(PASIVO_MOD[concepto], CERO) + l["monto"]
    lado_c, lado_p = ("haber", "debe") if reverso else ("debe", "haber")
    lineas = [{"cuenta": c, lado_c: m, "centro_costo": c, "sufijo": s} for (c, s), m in debe.items()]
    lineas += [{"cuenta": cta, lado_p: m} for cta, m in haber.items()]
    return contable.crear_asiento(con, sesion, fecha, glosa, lineas, "COSTOS", documento, tipo="TRASPASO").id


def aplicar_reversiones_pendientes(con: sqlite3.Connection, sesion: Sesion, obligatorio: bool = False) -> list[int]:
    """Revierte los cortes de MOD de ciclos ya cerrados, con fecha del día
    siguiente a su cierre (primer día del ciclo que recibe la planilla del
    mes). Se aplica en cuanto el centro tiene saldo suficiente, porque una
    cuenta de costo no puede quedar con saldo acreedor; es obligatoria antes
    del cierre del ciclo siguiente."""
    aplicadas = []
    for k in con.execute("SELECT * FROM cortes_mod WHERE monto_cent > 0 AND reversion_asiento_id IS NULL ORDER BY id"):
        lineas = [{**l, "monto": Decimal(str(l["monto"])), "carne": Decimal(str(l["carne"])),
                   "alimento": Decimal(str(l["alimento"]))} for l in json.loads(k["detalle_json"])]
        carne = sum((l["carne"] for l in lineas), CERO)
        alimento = sum((l["alimento"] for l in lineas), CERO)
        disponible_c = de_centavos(int(con.execute(
            "SELECT COALESCE(SUM(debe_cent), 0) - COALESCE(SUM(haber_cent), 0) FROM asiento_detalle "
            "WHERE cuenta_codigo = ?", (CENTRO_CARNE,)).fetchone()[0]))
        disponible_a = de_centavos(int(con.execute(
            "SELECT COALESCE(SUM(debe_cent), 0) - COALESCE(SUM(haber_cent), 0) FROM asiento_detalle "
            "WHERE cuenta_codigo = ?", (CENTRO_ALIMENTO,)).fetchone()[0]))
        if disponible_c < carne or disponible_a < alimento:
            if obligatorio:
                raise ReglaNegocioError("No se puede revertir el corte de MOD del ciclo anterior: el centro no tiene "
                                        "saldo suficiente.", codigo="REVERSION_MOD_PENDIENTE")
            continue
        c = _ciclo(con, int(k["ciclo_id"]))
        fecha = a_fecha(k["fecha"]) + timedelta(days=1)
        # Fase 13: asiento, marca del corte y bitácora en UNA transacción
        # (antes la marca quedaba en una transacción implícita sin confirmar).
        with transaccion(con):
            aid = _asiento_corte(con, sesion, {"lineas": lineas}, fecha,
                                 f"Reversión del corte de MOD del ciclo {c['numero']}/{c['ejercicio']}", True,
                                 f"Reversión corte MOD ciclo {c['numero']}/{c['ejercicio']}")
            con.execute("UPDATE cortes_mod SET reversion_asiento_id = ? WHERE id = ?", (aid, int(k["id"])))
            auditoria.registrar(con, sesion, "cortes_mod", "UPDATE", int(k["id"]),
                                antes={"reversion_asiento_id": None},
                                despues={"reversion_asiento_id": aid, "fecha": fecha.isoformat()})
        aplicadas.append(aid)
    return aplicadas


# ==========================================================================
# 5. PRODUCTOS TERMINADOS (1.1.03.12 / 1.1.03.13) — KÁRDEX AL CPP
# ==========================================================================
def saldo_terminado(con: sqlite3.Connection, producto: str) -> dict[str, Any]:
    """Saldo del producto terminado: cantidad, valor y CPP."""
    if producto not in CUENTA_PRODUCTO:
        raise DatosInvalidosError(f"Producto no válido. Use: {', '.join(CUENTA_PRODUCTO)}.")
    f = con.execute("SELECT * FROM kardex_terminados WHERE producto = ? ORDER BY id DESC LIMIT 1", (producto,)).fetchone()
    if f is None:
        return {"cantidad_mil": 0, "cantidad": Decimal("0.000"), "valor": CERO, "cpp": Decimal("0"), "fecha": None}
    return {"cantidad_mil": int(f["saldo_cantidad_mil"]), "cantidad": de_mil(int(f["saldo_cantidad_mil"])),
            "valor": de_centavos(int(f["saldo_valor_cent"])), "cpp": Decimal(f["cpp"]), "fecha": f["fecha"]}


def _insertar_terminado(con: sqlite3.Connection, sesion: Sesion, producto: str, fecha: date, tipo: str,
                        cantidad_mil: int, valor: Decimal, ciclo_id: int | None, documento: str | None,
                        asiento_id: int | None) -> int:
    saldo = saldo_terminado(con, producto)
    if saldo["fecha"] and saldo["fecha"] > fecha.isoformat():
        raise ReglaNegocioError(f"El kárdex de {NOMBRE_PRODUCTO[producto].lower()} tiene movimientos posteriores al "
                                f"{fecha_pantalla(fecha)}.", codigo="KARDEX_FUERA_DE_ORDEN")
    entrada = tipo.startswith("ENTRADA")
    nuevo_mil = saldo["cantidad_mil"] + cantidad_mil if entrada else saldo["cantidad_mil"] - cantidad_mil
    nuevo_valor = saldo["valor"] + valor if entrada else saldo["valor"] - valor
    cpp = (nuevo_valor / de_mil(nuevo_mil)).quantize(TASA, rounding=ROUND_HALF_UP) if nuevo_mil else Decimal("0")
    cur = con.execute(
        """INSERT INTO kardex_terminados (producto, fecha, tipo, cantidad_mil, valor_cent, saldo_cantidad_mil,
               saldo_valor_cent, cpp, ciclo_id, documento_ref, asiento_id, registrado_por, registrado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (producto, fecha.isoformat(), tipo, cantidad_mil, a_centavos(valor), nuevo_mil, a_centavos(nuevo_valor), str(cpp),
         ciclo_id, documento, asiento_id, sesion.nombre_usuario, _ahora_iso()))
    kid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "kardex_terminados", "INSERT", kid,
                        antes={"saldo_cantidad": saldo["cantidad"], "saldo_valor": saldo["valor"]},
                        despues={"producto": producto, "tipo": tipo, "cantidad": de_mil(cantidad_mil), "valor": valor,
                                 "saldo_cantidad": de_mil(nuevo_mil), "saldo_valor": nuevo_valor, "cpp": cpp})
    return kid


def _valor_salida(con: sqlite3.Connection, producto: str, cantidad_mil: int) -> tuple[Decimal, Decimal]:
    """Valor al CPP de una salida del producto terminado (la última salida
    lleva el saldo exacto para que el inventario quede en cero)."""
    saldo = saldo_terminado(con, producto)
    if cantidad_mil > saldo["cantidad_mil"]:
        raise ReglaNegocioError(
            f"Existencia insuficiente de {NOMBRE_PRODUCTO[producto].lower()}: hay "
            f"{inventarios.formato_cantidad(saldo['cantidad_mil'], UNIDAD_PRODUCTO[producto])} y se piden "
            f"{inventarios.formato_cantidad(cantidad_mil, UNIDAD_PRODUCTO[producto])}.", codigo="EXISTENCIA_INSUFICIENTE")
    if cantidad_mil == saldo["cantidad_mil"]:
        return saldo["valor"], saldo["cpp"]
    valor = redondear_bs(saldo["valor"] * Decimal(cantidad_mil) / Decimal(saldo["cantidad_mil"]))
    return valor, saldo["cpp"]


def kardex_productos(con: sqlite3.Connection, sesion: Sesion, producto: str) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    if producto not in CUENTA_PRODUCTO:
        raise DatosInvalidosError(f"Producto no válido. Use: {', '.join(CUENTA_PRODUCTO)}.")
    return [{**dict(f), "cantidad": de_mil(int(f["cantidad_mil"])), "valor": de_centavos(int(f["valor_cent"])),
             "saldo_cantidad": de_mil(int(f["saldo_cantidad_mil"])), "saldo_valor": de_centavos(int(f["saldo_valor_cent"])),
             "cpp": Decimal(f["cpp"])}
            for f in con.execute("SELECT * FROM kardex_terminados WHERE producto = ? ORDER BY id", (producto,))]


# ==========================================================================
# 6. ALIMENTO PROPIO (6.1.01.02; sufijos 11 y 13; desde 2027)
# ==========================================================================
def registrar_produccion_alimento(con: sqlite3.Connection, sesion: Sesion, fecha: Any, qq: Any) -> dict[str, Any]:
    """Producción de alimento balanceado: traslada el costo acumulado en
    6.1.01.02 a 1.1.03.13 (Inventario de Productos Terminados: Alimento) y
    fija el costo por quintal (Manual de Costos §7.2).

    Returns:
        ``{id, qq, costo, costo_qq, asiento_id}``.
    """
    _exigir_finanzas(con, sesion, "CREAR")
    f = a_fecha(fecha)
    qq_mil = a_mil(qq, "cantidad de quintales")
    with transaccion(con):
        if not alimento_propio_vigente(con, f):
            raise ReglaNegocioError("El centro 6.1.01.02 rige desde el ejercicio del alimento propio.",
                                    codigo="ALIMENTO_NO_VIGENTE")
        saldos = saldos_centro(con, CENTRO_ALIMENTO, f)
        total = sum(saldos.values(), CERO)
        if total <= 0:
            raise ReglaNegocioError("El centro 6.1.01.02 no tiene costo acumulado para trasladar.",
                                    codigo="CENTRO_SIN_COSTO")
        if any(v < 0 for v in saldos.values()):
            raise ReglaNegocioError("Un sufijo del centro 6.1.01.02 tiene saldo acreedor: revise sus asientos.",
                                    codigo="SUFIJO_ACREEDOR")
        costo_qq = (total / de_mil(qq_mil)).quantize(TASA, rounding=ROUND_HALF_UP)
        lineas = [{"cuenta": PT_ALIMENTO, "debe": total}]
        lineas += [{"cuenta": CENTRO_ALIMENTO, "haber": v, "centro_costo": CENTRO_ALIMENTO, "sufijo": s}
                   for s, v in saldos.items()]
        asiento = contable.crear_asiento(con, sesion, f, f"Producción de {de_mil(qq_mil)} qq de alimento balanceado "
                                                         f"a {formato_bs(costo_qq)} por qq (6.1.01.02 → 1.1.03.13)",
                                         lineas, "COSTOS", "Producción de alimento", tipo="TRASPASO")
        kid = _insertar_terminado(con, sesion, "ALIMENTO", f, "ENTRADA_PRODUCCION", qq_mil, total, None,
                                  "Producción de alimento", asiento.id)
        cur = con.execute(
            """INSERT INTO producciones_alimento (fecha, qq_mil, costo_cent, costo_qq, por_sufijo_json, asiento_id,
                   kardex_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), qq_mil, a_centavos(total), str(costo_qq), auditoria.a_json(saldos), asiento.id, kid,
             sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "producciones_alimento", "INSERT", pid,
                            despues={"fecha": f.isoformat(), "qq": de_mil(qq_mil), "costo": total, "costo_qq": costo_qq})
    return {"id": pid, "qq": de_mil(qq_mil), "costo": total, "costo_qq": costo_qq, "asiento_id": asiento.id}


def trasladar_alimento_propio(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, fecha: Any,
                              qq: Any) -> dict[str, Any]:
    """Consumo propio de alimento producido: 6.1.01.01 sufijo 11 contra
    1.1.03.13, al costo y sin margen (PET-RAU §2.1 paso 4)."""
    _exigir_finanzas(con, sesion, "CREAR")
    f = a_fecha(fecha)
    qq_mil = a_mil(qq, "cantidad de quintales")
    with transaccion(con):
        aplicar_reversiones_pendientes(con, sesion)
        _exigir_ciclo_abierto(con, ciclo_id, f)
        _validar_sufijo(con, CENTRO_CARNE, SUFIJO_ALIMENTO_PROPIO, f)
        valor, cpp = _valor_salida(con, "ALIMENTO", qq_mil)
        asiento = contable.crear_asiento(
            con, sesion, f, f"Alimento producido consumido por las aves: {de_mil(qq_mil)} qq (sufijo 11)",
            [{"cuenta": CENTRO_CARNE, "debe": valor, "centro_costo": CENTRO_CARNE, "sufijo": SUFIJO_ALIMENTO_PROPIO},
             {"cuenta": PT_ALIMENTO, "haber": valor}], "COSTOS", "Consumo propio de alimento", tipo="TRASPASO")
        _insertar_terminado(con, sesion, "ALIMENTO", f, "SALIDA_CONSUMO_PROPIO", qq_mil, valor, ciclo_id,
                            "Consumo propio de alimento", asiento.id)
        r = {"valor": valor, "cpp": cpp}
        cur = con.execute(
            """INSERT INTO consumos_costo (ciclo_id, centro_costo, sufijo, producto, fecha, cantidad_mil, valor_cent,
                   asiento_id, origen, documento_ref, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ciclo_id, CENTRO_CARNE, SUFIJO_ALIMENTO_PROPIO, "ALIMENTO", f.isoformat(), qq_mil, a_centavos(r["valor"]),
             asiento.id, "MANUAL", "Consumo propio de alimento", sesion.nombre_usuario, _ahora_iso()))
        rid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "consumos_costo", "INSERT", rid,
                            despues={"producto": "ALIMENTO", "qq": de_mil(qq_mil), "valor": r["valor"], "ciclo_id": ciclo_id})
    return {"id": rid, "valor": r["valor"], "cpp": r["cpp"], "asiento_id": asiento.id}


def ensaquillar_excedente(con: sqlite3.Connection, sesion: Sesion, fecha: Any, qq: Any) -> dict[str, Any]:
    """Pone el excedente en condiciones de venta: consume los saquillos de
    yute al CPP con el sufijo 13 (6.1.01.02-13 contra 1.1.03.11) y suma ese
    costo al del alimento, que pasa al inventario del excedente ensaquillado
    (Manual de Costos §6.4 y §8.2 paso 4).

    Returns:
        ``{qq, costo_base, costo_saquillos, costo_total, costo_qq}``.
    """
    _exigir_finanzas(con, sesion, "CREAR")
    f = a_fecha(fecha)
    qq_mil = a_mil(qq, "cantidad de quintales")
    with transaccion(con):
        _validar_sufijo(con, CENTRO_ALIMENTO, SUFIJO_SAQUILLOS, f)
        por_qq = int(_param(_params(con, f), "saquillos_por_qq"))
        if (qq_mil * por_qq) % 1000:
            raise DatosInvalidosError("Los saquillos son unidades enteras: ensaquille quintales completos.")
        saquillos = qq_mil * por_qq // 1000
        insumo = con.execute("SELECT * FROM insumos WHERE codigo = ?", (INSUMO_SAQUILLOS,)).fetchone()
        valor_base, _ = _valor_salida(con, "ALIMENTO", qq_mil)
        s = inventarios.registrar_salida(con, sesion, int(insumo["id"]), f, saquillos, centro_costo=CENTRO_ALIMENTO,
                                         sufijo=SUFIJO_SAQUILLOS, documento_ref="Ensaquillado del excedente",
                                         glosa=f"Saquillos de yute para {de_mil(qq_mil)} qq de excedente (sufijo 13)")
        asiento = contable.crear_asiento(
            con, sesion, f, f"Costo de ensaquillado incorporado al alimento excedente ({de_mil(qq_mil)} qq)",
            [{"cuenta": PT_ALIMENTO, "debe": s["valor"]},
             {"cuenta": CENTRO_ALIMENTO, "haber": s["valor"], "centro_costo": CENTRO_ALIMENTO,
              "sufijo": SUFIJO_SAQUILLOS}], "COSTOS", "Ensaquillado del excedente", tipo="TRASPASO")
        total = valor_base + s["valor"]
        _insertar_terminado(con, sesion, "ALIMENTO", f, "SALIDA_ENSAQUILLADO", qq_mil, valor_base, None,
                            "Ensaquillado del excedente", asiento.id)
        _insertar_terminado(con, sesion, "EXCEDENTE", f, "ENTRADA_ENSAQUILLADO", qq_mil, total, None,
                            "Ensaquillado del excedente", asiento.id)
    costo_qq = (total / de_mil(qq_mil)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"qq": de_mil(qq_mil), "costo_base": valor_base, "costo_saquillos": s["valor"], "costo_total": total,
            "costo_qq": costo_qq}


# ==========================================================================
# 7. COSTO DE VENTAS (6.1.02.01 / 6.1.02.02)
# ==========================================================================
def registrar_costo_venta(con: sqlite3.Connection, sesion: Sesion, producto: str, fecha: Any, cantidad: Any,
                          documento_ref: str) -> dict[str, Any]:
    """Costo de las unidades vendidas al CPP: carne 6.1.02.01 contra 1.1.03.12;
    alimento excedente 6.1.02.02 contra 1.1.03.13. Lo invoca la venta (Fase 9)."""
    _exigir_finanzas(con, sesion, "CREAR")
    if producto not in COSTO_VENTA:
        raise DatosInvalidosError("Se venden la carne (CARNE) o el alimento excedente (EXCEDENTE).")
    f = a_fecha(fecha)
    cant = a_mil(cantidad)
    doc = _texto(documento_ref, "nota de entrega", 60)
    with transaccion(con):
        valor, cpp = _valor_salida(con, producto, cant)
        asiento = contable.crear_asiento(
            con, sesion, f, f"Costo de ventas: {inventarios.formato_cantidad(cant, UNIDAD_PRODUCTO[producto])} de "
                            f"{NOMBRE_PRODUCTO[producto].lower()} al CPP",
            [{"cuenta": COSTO_VENTA[producto], "debe": valor}, {"cuenta": CUENTA_PRODUCTO[producto], "haber": valor}],
            "COSTOS", doc, tipo="TRASPASO")
        _insertar_terminado(con, sesion, producto, f, "SALIDA_VENTA", cant, valor, None, doc, asiento.id)
    return {"valor": valor, "cpp": cpp, "asiento_id": asiento.id}


# ==========================================================================
# 8. CIERRE DEL CICLO (día 73) E INFORME DE COSTO (FIN-TRA-06, FIN-TRA-07)
# ==========================================================================
def cerrar_ciclo(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> dict[str, Any]:
    """Cierre de costos del ciclo (PC-03, PC-04; PR-FIN-06 actividad 6).

    Exige el ciclo operativo concluido (Fase 6: lotes faenados, bajas
    contabilizadas y aves conciliadas). Devenga los días de MOD fija no
    cubiertos por planilla, traslada el saldo total de 6.1.01.01 a 1.1.03.12
    por sufijo dejando el centro en cero, ingresa los kg de carne al kárdex de
    productos terminados, revierte el devengo de MOD el día siguiente y emite
    el Informe de Costo de Ciclo con el costo unitario Bs/kg y su variación.

    # DISCREPANCIA: el Manual de Costos (§7.1) habla de «2,6 kg de peso vivo
    # promedio» y el Manual Presupuestario (B.1) de «rendimiento canal 2,6
    # kg/ave». El costo unitario (FIN-TRA-07, PR-FIN-06) divide entre los kg
    # de PRODUCTO TERMINADO; se usan los kg de peso canal del faenado
    # (FIN-TRA-05) y se informa también el peso vivo.
    """
    _exigir_finanzas(con, sesion, "EDITAR")
    with transaccion(con):
        c = _ciclo(con, ciclo_id)
        if _cierre(con, ciclo_id) is not None:
            raise ReglaNegocioError("El ciclo ya tiene su cierre de costos.", codigo="CICLO_COSTEADO")
        if c["estado"] != "CONCLUIDO":
            raise ReglaNegocioError("Concluya primero el ciclo operativo (faenado, bajas y conciliación de aves).",
                                    codigo="CICLO_NO_CONCLUIDO")
        previo_abierto = con.execute(
            """SELECT numero, ejercicio FROM ciclos_produccion p WHERE p.fecha_inicio < ?
                  AND NOT EXISTS (SELECT 1 FROM costos_ciclo k WHERE k.ciclo_id = p.id)""",
            (c["fecha_inicio"],)).fetchone()
        if previo_abierto:
            raise ReglaNegocioError(f"Cierre primero el ciclo {previo_abierto['numero']} del ejercicio "
                                    f"{previo_abierto['ejercicio']}.", codigo="CIERRE_EN_ORDEN")
        ultimo_faenado = con.execute("SELECT MAX(x.fecha) FROM faenados x JOIN lotes l ON l.id = x.lote_id "
                                     "WHERE l.ciclo_id = ?", (ciclo_id,)).fetchone()[0]
        fecha = max(a_fecha(c["fecha_cierre"]), a_fecha(ultimo_faenado))
        aplicar_reversiones_pendientes(con, sesion, obligatorio=True)
        corte = calcular_corte_mod(con, ciclo_id, fecha)
        asiento_corte = reverso_corte = None
        if corte["total"] > 0:
            asiento_corte = _asiento_corte(con, sesion, corte, fecha,
                                           f"Corte de MOD fija: {corte['dias']} días del ciclo no cubiertos por planilla",
                                           False, f"Corte MOD ciclo {c['numero']}/{c['ejercicio']}")
        saldos = produccion.costo_ciclo_por_sufijo(con, ciclo_id, fecha)
        total = sum(saldos.values(), CERO)
        if total <= 0:
            raise ReglaNegocioError("El centro 6.1.01.01 no tiene costo acumulado del ciclo.", codigo="CENTRO_SIN_COSTO")
        if any(v < 0 for v in saldos.values()):
            raise ReglaNegocioError("Un sufijo del centro tiene saldo acreedor: revise los asientos del ciclo.",
                                    codigo="SUFIJO_ACREEDOR", detalle={k: str(v) for k, v in saldos.items() if v < 0})
        lineas = [{"cuenta": PT_CARNE, "debe": total}]
        lineas += [{"cuenta": CENTRO_CARNE, "haber": v, "centro_costo": CENTRO_CARNE, "sufijo": s}
                   for s, v in saldos.items()]
        asiento = contable.crear_asiento(
            con, sesion, fecha, f"Cierre del ciclo {c['numero']} del ejercicio {c['ejercicio']}: traslado del costo "
                                "acumulado a Productos Terminados (FIN-TRA-06)", lineas, ORIGEN_CIERRE,
            f"FIN-TRA-06 C{c['numero']}/{c['ejercicio']}", tipo="TRASPASO")
        kg = con.execute("SELECT COALESCE(SUM(x.peso_canal_mil), 0), COALESCE(SUM(x.peso_vivo_mil), 0), "
                         "COALESCE(SUM(x.alimento_kg_mil), 0) FROM faenados x JOIN lotes l ON l.id = x.lote_id "
                         "WHERE l.ciclo_id = ?", (ciclo_id,)).fetchone()
        kg_canal, kg_vivo, kg_alimento = int(kg[0]), int(kg[1]), int(kg[2])
        kid = _insertar_terminado(con, sesion, "CARNE", fecha, "ENTRADA_PRODUCCION", kg_canal, total, ciclo_id,
                                  f"FIN-TRA-06 C{c['numero']}/{c['ejercicio']}", asiento.id)
        con.execute(
            """INSERT INTO cortes_mod (ciclo_id, fecha, dias_no_cubiertos, dias_ciclo, monto_cent, detalle_json,
                   asiento_id, reversion_asiento_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ciclo_id, fecha.isoformat(), corte["dias"], corte["dias_ciclo"], a_centavos(corte["total"]),
             auditoria.a_json(corte["lineas"]), asiento_corte, reverso_corte, sesion.nombre_usuario, _ahora_iso()))
        cat = catalogo_sufijos(con)
        por_elemento = {e: sum((v for s, v in saldos.items() if _elemento(cat, s) == e), CERO) for e in ELEMENTOS}
        unitario = (total / de_mil(kg_canal)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        anterior = con.execute(
            """SELECT k.* FROM costos_ciclo k JOIN ciclos_produccion p ON p.id = k.ciclo_id WHERE p.fecha_inicio < ?
                ORDER BY p.fecha_inicio DESC LIMIT 1""", (c["fecha_inicio"],)).fetchone()
        variacion = variacion_pct = None
        if anterior is not None:
            variacion = unitario - Decimal(anterior["costo_unitario"])
            variacion_pct = (variacion / Decimal(anterior["costo_unitario"])).quantize(TASA, rounding=ROUND_HALF_UP)
        ica = (Decimal(kg_alimento) / Decimal(kg_vivo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        conciliacion = produccion.conciliar_aves(con, ciclo_id)
        con.execute(
            """INSERT INTO costos_ciclo (ciclo_id, fecha_cierre, total_cent, mpd_cent, mod_cent, cif_cent, perdidas_cent,
                   por_sufijo_json, kg_producidos_mil, kg_vivos_mil, costo_unitario, ciclo_anterior_id,
                   costo_unitario_anterior, variacion_unitaria, variacion_pct, ica, conciliacion_json, asiento_id,
                   kardex_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ciclo_id, fecha.isoformat(), a_centavos(total), a_centavos(por_elemento["MPD"]),
             a_centavos(por_elemento["MOD"]), a_centavos(por_elemento["CIF"]), a_centavos(por_elemento["PERDIDA"]),
             auditoria.a_json(saldos), kg_canal, kg_vivo, str(unitario),
             int(anterior["ciclo_id"]) if anterior else None, anterior["costo_unitario"] if anterior else None,
             str(variacion) if variacion is not None else None, str(variacion_pct) if variacion_pct is not None else None,
             str(ica), auditoria.a_json(conciliacion), asiento.id, kid, sesion.nombre_usuario, _ahora_iso()))
        if produccion.costo_ciclo_por_sufijo(con, ciclo_id, fecha, incluir_cierre=True):
            raise ReglaNegocioError("El costo del ciclo en 6.1.01.01 no quedó en cero.", codigo="CENTRO_NO_CERO")
        auditoria.registrar(con, sesion, "costos_ciclo", "INSERT", ciclo_id,
                            despues={"fecha_cierre": fecha.isoformat(), "total": total, "por_elemento": por_elemento,
                                     "kg_canal": de_mil(kg_canal), "costo_unitario": unitario,
                                     "variacion": variacion, "corte_mod": corte["total"], "asiento_id": asiento.id})
    return {"total": total, "por_elemento": por_elemento, "por_sufijo": saldos, "kg": de_mil(kg_canal),
            "costo_unitario": unitario, "variacion": variacion, "variacion_pct": variacion_pct, "corte_mod": corte,
            "asiento_id": asiento.id, "fecha": fecha}


def aprobar_informe(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, observacion: str | None = None) -> None:
    """Aprobación del Informe de Costo de Ciclo por la Gerencia General
    (PR-FIN-06 actividad 5; Manual de Costos §9.3)."""
    _exigir_gerencia(con, sesion)
    obs = _opcional(observacion, "observación", 500)
    with transaccion(con):
        k = _cierre(con, ciclo_id)
        if k is None:
            raise ReglaNegocioError("El ciclo aún no tiene cierre de costos.", codigo="SIN_CIERRE")
        if k["aprobado_por"]:
            raise ReglaNegocioError("El informe ya fue aprobado.", codigo="INFORME_APROBADO")
        con.execute("UPDATE costos_ciclo SET aprobado_por = ?, aprobado_en = ?, observacion_gg = ? WHERE ciclo_id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), obs, ciclo_id))
        auditoria.registrar(con, sesion, "costos_ciclo", "UPDATE", ciclo_id, antes={"aprobado_por": None},
                            despues={"aprobado_por": sesion.nombre_usuario, "observacion": obs})


def informe_costo_ciclo(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> dict[str, Any]:
    """Contenido del Informe de Costo de Ciclo (FIN-TRA-06) y del Cálculo de
    Costo Unitario (FIN-TRA-07)."""
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    c = _ciclo(con, ciclo_id)
    k = _cierre(con, ciclo_id)
    if k is None:
        raise ReglaNegocioError("El ciclo aún no tiene cierre de costos.", codigo="SIN_CIERRE")
    cat = catalogo_sufijos(con)
    saldos = {s: Decimal(v) for s, v in json.loads(k["por_sufijo_json"]).items()}
    conc = json.loads(k["conciliacion_json"])
    anormal = con.execute("SELECT COALESCE(SUM(monto_cent), 0) FROM bajas_mortalidad WHERE ciclo_id = ? "
                          "AND clasificacion = 'ANORMAL' AND estado = 'CONTABILIZADA'", (ciclo_id,)).fetchone()[0]
    corte = con.execute("SELECT * FROM cortes_mod WHERE ciclo_id = ?", (ciclo_id,)).fetchone()
    return {
        "ciclo": dict(c), "fecha_cierre": k["fecha_cierre"], "total": de_centavos(int(k["total_cent"])),
        "por_elemento": {"MPD": de_centavos(int(k["mpd_cent"])), "MOD": de_centavos(int(k["mod_cent"])),
                         "CIF": de_centavos(int(k["cif_cent"])), "PERDIDA": de_centavos(int(k["perdidas_cent"]))},
        "por_sufijo": [{"sufijo": s, "nombre": cat[s]["nombre"] if s in cat else "—", "elemento": _elemento(cat, s),
                        "monto": v} for s, v in saldos.items()],
        "kg_producidos": de_mil(int(k["kg_producidos_mil"])), "kg_vivos": de_mil(int(k["kg_vivos_mil"])),
        "costo_unitario": Decimal(k["costo_unitario"]),
        "costo_unitario_anterior": Decimal(k["costo_unitario_anterior"]) if k["costo_unitario_anterior"] else None,
        "variacion": Decimal(k["variacion_unitaria"]) if k["variacion_unitaria"] else None,
        "variacion_pct": Decimal(k["variacion_pct"]) if k["variacion_pct"] else None,
        "ica": Decimal(k["ica"]) if k["ica"] else None, "conciliacion": conc,
        "mortalidad_anormal_monto": de_centavos(int(anormal)),
        "corte_mod": {"dias": int(corte["dias_no_cubiertos"]), "monto": de_centavos(int(corte["monto_cent"]))}
        if corte else None,
        "asiento_id": int(k["asiento_id"]), "aprobado_por": k["aprobado_por"], "aprobado_en": k["aprobado_en"],
        "observacion_gg": k["observacion_gg"], "elaborado_por": k["registrado_por"],
    }


def listar_cierres(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    return [{**dict(f), "total": de_centavos(int(f["total_cent"])), "kg": de_mil(int(f["kg_producidos_mil"])),
             "costo_unitario": Decimal(f["costo_unitario"]),
             "variacion_pct": Decimal(f["variacion_pct"]) if f["variacion_pct"] else None}
            for f in con.execute("""SELECT k.*, c.numero, c.ejercicio FROM costos_ciclo k
                                      JOIN ciclos_produccion c ON c.id = k.ciclo_id ORDER BY c.fecha_inicio DESC""")]


# ==========================================================================
# 9. COMPARACIÓN CONTRA EL PRESUPUESTO D.4 (Manual Presupuestario E.1)
# ==========================================================================
def semaforo_variacion(con: sqlite3.Connection, fecha: Any, variacion_pct: Decimal | None) -> str:
    """Variación desfavorable de costos: verde hasta 5 %, amarillo hasta 10 %,
    rojo por encima (parámetros ``variacion_costos_amarillo/rojo``)."""
    if variacion_pct is None:
        return "VERDE"
    p = _params(con, fecha)
    if variacion_pct <= Decimal(str(_param(p, "variacion_costos_amarillo"))):
        return "VERDE"
    if variacion_pct <= Decimal(str(_param(p, "variacion_costos_rojo"))):
        return "AMARILLO"
    return "ROJO"


def _cantidad_real(con: sqlite3.Connection, ciclo: sqlite3.Row, sufijo: str, desde: date,
                   hasta: date) -> Decimal | None:
    """Cantidad física real del ciclo para la variación de cantidad."""
    if sufijo == "01":
        return Decimal(int(ciclo["aves_iniciales"]))
    if sufijo == "22":
        n = con.execute("SELECT COALESCE(SUM(unidades), 0) FROM liquidaciones_eventuales WHERE fecha BETWEEN ? AND ?",
                        (desde.isoformat(), hasta.isoformat())).fetchone()[0]
        return Decimal(int(n)) if n else None
    n = con.execute("SELECT COALESCE(SUM(cantidad_mil), 0) FROM consumos_costo WHERE ciclo_id = ? AND sufijo = ?",
                    (int(ciclo["id"]), sufijo)).fetchone()[0]
    return de_mil(int(n)) if n else None


def comparar_presupuesto(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> dict[str, Any]:
    """Real del ciclo frente al presupuesto D.4 por sufijo: variación total,
    porcentual, de precio ((P real − P presup.) × Q real) y de cantidad
    ((Q real − Q presup.) × P presup.), con semáforo de la variación
    desfavorable."""
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    c = _ciclo(con, ciclo_id)
    mayor = mayor_analitico(con, sesion, ciclo_id)
    real = {f["sufijo"]: f["monto"] for f in mayor["filas"]}
    cat = catalogo_sufijos(con)
    presupuesto = {p["sufijo"]: p for p in con.execute(
        "SELECT * FROM presupuesto_costos WHERE ejercicio = ? AND ciclo = ? AND centro_costo = ? ORDER BY sufijo",
        (c["ejercicio"], c["numero"], CENTRO_CARNE))}
    filas = []
    for sufijo in sorted(set(real) | set(presupuesto)):
        p = presupuesto.get(sufijo)
        monto_p = de_centavos(int(p["monto_cent"])) if p else CERO
        monto_r = real.get(sufijo, CERO)
        variacion = monto_r - monto_p
        pct = (variacion / monto_p).quantize(TASA, rounding=ROUND_HALF_UP) if monto_p else None
        q_p = de_mil(int(p["cantidad_mil"])) if p and p["cantidad_mil"] else None
        precio_p = Decimal(p["precio"]) if p and p["precio"] else None
        q_r = _cantidad_real(con, c, sufijo, mayor["desde"], mayor["hasta"]) if q_p is not None else None
        precio_r = (monto_r / q_r).quantize(TASA, rounding=ROUND_HALF_UP) if q_r else None
        var_precio = redondear_bs((precio_r - precio_p) * q_r) if q_r and precio_p is not None else None
        var_cantidad = redondear_bs((q_r - q_p) * precio_p) if q_r and q_p is not None else None
        filas.append({"sufijo": sufijo, "concepto": p["concepto"] if p else cat.get(sufijo, {}).get("nombre", ""),
                      "elemento": _elemento(cat, sufijo), "unidad": p["unidad"] if p else None,
                      "cantidad_presupuesto": q_p, "precio_presupuesto": precio_p, "presupuesto": monto_p,
                      "cantidad_real": q_r, "precio_real": precio_r, "real": monto_r, "variacion": variacion,
                      "variacion_pct": pct, "variacion_precio": var_precio, "variacion_cantidad": var_cantidad,
                      "semaforo": semaforo_variacion(con, c["fecha_inicio"], pct)})
    total_p = sum((f["presupuesto"] for f in filas), CERO)
    total_r = sum((f["real"] for f in filas), CERO)
    pct_total = ((total_r - total_p) / total_p).quantize(TASA, rounding=ROUND_HALF_UP) if total_p else None
    return {"ciclo": dict(c), "filas": filas, "presupuesto": total_p, "real": total_r, "variacion": total_r - total_p,
            "variacion_pct": pct_total, "semaforo": semaforo_variacion(con, c["fecha_inicio"], pct_total),
            "con_presupuesto": bool(presupuesto)}


# ==========================================================================
# 10. RESUMEN
# ==========================================================================
def resumen_costos(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> dict[str, Any]:
    """Cifras de cabecera: costo acumulado del ciclo en curso, último costo
    unitario y existencias de productos terminados."""
    seguridad.exigir_permiso(con, sesion, "costos", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    c = _ciclo_de_fecha(con, f)
    acumulado = None
    if c is not None:
        acumulado = mayor_analitico(con, sesion, int(c["id"]), f)["total"]
    ultimo = con.execute("SELECT k.costo_unitario, c.numero, c.ejercicio FROM costos_ciclo k JOIN ciclos_produccion c "
                         "ON c.id = k.ciclo_id ORDER BY c.fecha_inicio DESC LIMIT 1").fetchone()
    return {"ciclo": dict(c) if c else None, "acumulado": acumulado,
            "ultimo_unitario": Decimal(ultimo["costo_unitario"]) if ultimo else None,
            "ultimo_ciclo": f"C{ultimo['numero']} · {ultimo['ejercicio']}" if ultimo else None,
            "carne": saldo_terminado(con, "CARNE"), "alimento": saldo_terminado(con, "ALIMENTO"),
            "excedente": saldo_terminado(con, "EXCEDENTE")}
