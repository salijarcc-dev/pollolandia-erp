"""Carga y seguimiento de los datos del POA 2026-2027 (Fase 13).

``cargar_datos_poa`` valida y registra, en una sola transacción y con
bitácora, el calendario de ciclos (C1 a C5, con el tramo C2-C5 dentro del
POA), el flujo de caja mensual de oct-2026 a jun-2027 y el presupuesto por
objeto de gasto y área (``db/datos_poa.py``). Es idempotente por versión: una
versión ya cargada no se vuelve a cargar.

Validaciones antes de cargar (tolerancia de redondeo ``poa_flujo_tolerancia_bs``;
el POA declara «base caja, redondeada»):

* Calendario: ciclos de ``dias_ciclo`` días, consecutivos y dentro del ejercicio.
* Flujo: saldo inicial de cada mes = saldo final del anterior (exacto) y saldo
  final = inicial + cobros − egresos − CAPEX (con tolerancia).
* Presupuesto por área: ejercicio = previo + POA en cada fila (con tolerancia).

Las diferencias de redondeo entre las sumas y los totales impresos del POA se
informan como observaciones (# DISCREPANCIA: el POA imprime totales redondeados;
se conservan las cifras de cada celda y se informa la diferencia).

El seguimiento compara el calendario con los ciclos reales y el flujo mensual
con los movimientos reales de efectivo (1.1.01.xx).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from db import datos_poa as datos
from db.conexion import a_centavos, de_centavos, obtener_parametros_vigentes, transaccion
from servicios import auditoria, seguridad
from servicios.excepciones import DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion

USUARIO_SISTEMA = "SISTEMA"
DISPONIBLES = ("1.1.01.01", "1.1.01.02", "1.1.01.03", "1.1.01.04", "1.1.01.05")


def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _d(texto: str) -> Decimal:
    return Decimal(texto)


# ==========================================================================
# 1. VALIDACIÓN
# ==========================================================================
def validar_datos(con: sqlite3.Connection) -> dict[str, Any]:
    """Valida la transcripción del POA. Devuelve ``{errores, observaciones,
    totales}``; con errores la carga se rechaza."""
    p = obtener_parametros_vigentes(con, datos.EJERCICIO_POA)
    tolerancia = Decimal(str(p["poa_flujo_tolerancia_bs"]))
    dias = int(p["dias_ciclo"])
    errores: list[str] = []
    observaciones: list[str] = []
    ej = con.execute("SELECT fecha_inicio, fecha_fin FROM ejercicios WHERE ejercicio = ?",
                     (datos.EJERCICIO_POA,)).fetchone()
    if ej is None:
        errores.append(f"El ejercicio {datos.EJERCICIO_POA} no está en el calendario.")
        return {"errores": errores, "observaciones": observaciones, "totales": {}}
    anterior: date | None = None
    for ciclo, inicio, cierre, aves, _tramo, en_poa, _ingreso in datos.CALENDARIO:
        i, c = date.fromisoformat(inicio), date.fromisoformat(cierre)
        if (c - i).days + 1 != dias:
            errores.append(f"C{ciclo}: {(c - i).days + 1} días; el ciclo es de {dias} días.")
        if anterior is not None and i != anterior + timedelta(days=1):
            errores.append(f"C{ciclo}: no empieza al día siguiente del cierre del ciclo anterior.")
        if inicio < ej["fecha_inicio"] or cierre > ej["fecha_fin"]:
            errores.append(f"C{ciclo}: fuera del ejercicio {datos.EJERCICIO_POA}.")
        if aves <= 0 or en_poa < 0 or en_poa > dias:
            errores.append(f"C{ciclo}: aves o días dentro del POA inválidos.")
        anterior = c
    ingreso_poa = sum(_d(x[6]) for x in datos.CALENDARIO if x[5] > 0)
    if ingreso_poa != _d(datos.TOTAL_INGRESO_POA):
        errores.append(f"Ingreso de los ciclos dentro del POA Bs {ingreso_poa} ≠ Bs {datos.TOTAL_INGRESO_POA}.")
    f = {k: [_d(x) for x in v] for k, v in datos.FLUJO_MENSUAL.items()}
    if any(len(v) != len(datos.MESES) for v in f.values()):
        errores.append("El flujo mensual no tiene un valor por mes.")
        return {"errores": errores, "observaciones": observaciones, "totales": {}}
    for n, mes in enumerate(datos.MESES):
        if n and f["SALDO_INICIAL"][n] != f["SALDO_FINAL"][n - 1]:
            errores.append(f"{mes}: el saldo inicial no es el saldo final del mes anterior.")
        calculado = (f["SALDO_INICIAL"][n] + sum(f[k][n] for k in datos.INGRESOS)
                     - sum(f[k][n] for k in datos.EGRESOS_OPERACION) - f["CAPEX"][n])
        diferencia = calculado - f["SALDO_FINAL"][n]
        if abs(diferencia) > tolerancia:
            errores.append(f"{mes}: saldo final calculado Bs {calculado} ≠ Bs {f['SALDO_FINAL'][n]}.")
        elif diferencia:
            observaciones.append(f"{mes}: redondeo de Bs {diferencia} en el saldo final (base caja, redondeada).")
    totales: dict[str, Any] = {}
    for k, impreso in datos.TOTALES_FLUJO.items():
        suma = sum(f[k])
        totales[k] = suma
        if suma != _d(impreso):
            observaciones.append(f"Total {datos.NOMBRE_CONCEPTO[k]}: suma de los meses Bs {suma} frente a "
                                 f"Bs {impreso} impreso en el POA.")
    if f["SALDO_FINAL"][-1] != _d(datos.SALDO_FINAL_POA):
        errores.append(f"El saldo final de junio no es Bs {datos.SALDO_FINAL_POA}.")
    for n, (_g, objeto, _a, ejercicio, previo, poa) in enumerate(datos.PRESUPUESTO_AREA, start=1):
        dif = _d(ejercicio) - _d(previo) - _d(poa)
        if abs(dif) > tolerancia:
            errores.append(f"Fila {n} ({objeto}): ejercicio ≠ previo + POA (diferencia Bs {dif}).")
        elif dif:
            observaciones.append(f"Fila {n} ({objeto}): redondeo de Bs {dif} entre previo y POA.")
    for grupo_total, columna, impreso in (("ejercicio", 3, datos.TOTAL_EGRESOS_EJERCICIO),
                                          ("previo", 4, datos.TOTAL_EGRESOS_PREVIO),
                                          ("POA", 5, datos.TOTAL_EGRESOS_POA)):
        suma = sum(_d(x[columna]) for x in datos.PRESUPUESTO_AREA if x[0] in ("COSTOS_DIRECTOS", "GASTOS"))
        totales[f"egresos_{grupo_total}"] = suma
        if abs(suma - _d(impreso)) > tolerancia:
            observaciones.append(f"Total egresos de operación ({grupo_total}): suma Bs {suma} frente a "
                                 f"Bs {impreso} impreso.")
    capex = sum(_d(x[5]) for x in datos.PRESUPUESTO_AREA if x[0] == "CAPEX")
    if capex != _d(datos.TOTAL_CAPEX):
        errores.append(f"CAPEX del POA Bs {capex} ≠ Bs {datos.TOTAL_CAPEX}.")
    return {"errores": errores, "observaciones": observaciones, "totales": totales}


# ==========================================================================
# 2. CARGA
# ==========================================================================
def cargar_datos_poa(con: sqlite3.Connection, sesion: Sesion | None = None) -> dict[str, Any]:
    """Carga el POA (idempotente por versión). ``sesion`` es la GG o el JF
    desde la pantalla; ``None`` desde el script (usuario ``SISTEMA``)."""
    if sesion is not None:
        seguridad.exigir_permiso(con, sesion, "presupuesto", "CREAR")
    existente = con.execute("SELECT id FROM poa_cargas WHERE version = ?", (datos.VERSION_POA,)).fetchone()
    if existente is not None:
        return {"carga_id": int(existente[0]), "ya_cargado": True, **resumen(con, int(existente[0]))}
    validacion = validar_datos(con)
    if validacion["errores"]:
        raise ReglaNegocioError("Los datos del POA no superan la validación: " + " ".join(validacion["errores"]),
                                codigo="POA_INVALIDO", detalle=validacion)
    usuario = sesion.nombre_usuario if sesion else USUARIO_SISTEMA
    with transaccion(con):
        cur = con.execute("INSERT INTO poa_cargas (version, fuente, resumen_json, cargado_por, cargado_en) "
                          "VALUES (?,?,?,?,?)",
                          (datos.VERSION_POA, datos.FUENTE_POA,
                           json.dumps({"observaciones": validacion["observaciones"],
                                       "totales": {k: str(v) for k, v in validacion["totales"].items()}},
                                      ensure_ascii=False), usuario, _ahora_iso()))
        cid = int(cur.lastrowid)
        for ciclo, inicio, cierre, aves, tramo, en_poa, ingreso in datos.CALENDARIO:
            con.execute("INSERT INTO poa_calendario (carga_id, ejercicio, ciclo, inicio, cierre, aves, tramo, "
                        "dias_en_poa, ingreso_cent) VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid, datos.EJERCICIO_POA, ciclo, inicio, cierre, aves, tramo, en_poa, a_centavos(_d(ingreso))))
        for orden, (concepto, montos) in enumerate(datos.FLUJO_MENSUAL.items(), start=1):
            for mes, monto in zip(datos.MESES, montos):
                con.execute("INSERT INTO poa_flujo_mensual (carga_id, mes, orden, concepto, monto_cent) "
                            "VALUES (?,?,?,?,?)", (cid, mes, orden, concepto, a_centavos(_d(monto))))
        for orden, (grupo, objeto, area, ejercicio, previo, poa) in enumerate(datos.PRESUPUESTO_AREA, start=1):
            con.execute("INSERT INTO poa_presupuesto_area (carga_id, orden, grupo, objeto, area, ejercicio_cent, "
                        "previo_cent, poa_cent) VALUES (?,?,?,?,?,?,?,?)",
                        (cid, orden, grupo, objeto, area, a_centavos(_d(ejercicio)), a_centavos(_d(previo)),
                         a_centavos(_d(poa))))
        auditoria.registrar_evento(
            con, usuario=usuario, rol=sesion.rol if sesion else None, tabla="poa_cargas", accion="CARGA_INICIAL",
            registro_id=cid, despues={"version": datos.VERSION_POA, "ciclos": len(datos.CALENDARIO),
                                      "meses": len(datos.MESES), "filas_area": len(datos.PRESUPUESTO_AREA),
                                      "observaciones": len(validacion["observaciones"])},
            detalle="Carga de los datos del POA 2026-2027 (calendario, flujo mensual y presupuesto por área)")
    return {"carga_id": cid, "ya_cargado": False, **resumen(con, cid)}


def carga_vigente(con: sqlite3.Connection) -> int | None:
    fila = con.execute("SELECT MAX(id) FROM poa_cargas").fetchone()
    return int(fila[0]) if fila and fila[0] is not None else None


def resumen(con: sqlite3.Connection, carga_id: int) -> dict[str, Any]:
    carga = con.execute("SELECT * FROM poa_cargas WHERE id = ?", (carga_id,)).fetchone()
    contenido = json.loads(carga["resumen_json"])
    return {"version": carga["version"], "cargado_en": carga["cargado_en"], "cargado_por": carga["cargado_por"],
            "ciclos": int(con.execute("SELECT COUNT(*) FROM poa_calendario WHERE carga_id = ?", (carga_id,)).fetchone()[0]),
            "registros_flujo": int(con.execute("SELECT COUNT(*) FROM poa_flujo_mensual WHERE carga_id = ?",
                                               (carga_id,)).fetchone()[0]),
            "filas_area": int(con.execute("SELECT COUNT(*) FROM poa_presupuesto_area WHERE carga_id = ?",
                                          (carga_id,)).fetchone()[0]),
            "observaciones": contenido.get("observaciones", [])}


# ==========================================================================
# 3. CONSULTAS Y SEGUIMIENTO
# ==========================================================================
def _exigir_carga(con: sqlite3.Connection) -> int:
    cid = carga_vigente(con)
    if cid is None:
        raise ReglaNegocioError("Los datos del POA aún no se cargaron (scripts/cargar_datos_poa.py o la pantalla de "
                                "Presupuesto).", codigo="POA_SIN_CARGAR")
    return cid


def calendario_vs_real(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Ciclos planificados del POA frente a los ciclos abiertos en Producción."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    cid = _exigir_carga(con)
    filas = []
    for p in con.execute("SELECT * FROM poa_calendario WHERE carga_id = ? ORDER BY ciclo", (cid,)):
        real = con.execute("SELECT fecha_inicio, fecha_cierre, aves_iniciales, estado FROM ciclos_produccion WHERE "
                           "ejercicio = ? AND numero = ?", (p["ejercicio"], p["ciclo"])).fetchone()
        desfase = None if real is None else (date.fromisoformat(real["fecha_inicio"]) -
                                             date.fromisoformat(p["inicio"])).days
        filas.append({"ciclo": int(p["ciclo"]), "inicio_poa": p["inicio"], "cierre_poa": p["cierre"],
                      "aves_poa": int(p["aves"]), "tramo": p["tramo"], "dias_en_poa": int(p["dias_en_poa"]),
                      "ingreso_poa": de_centavos(int(p["ingreso_cent"])),
                      "inicio_real": real["fecha_inicio"] if real else None,
                      "cierre_real": real["fecha_cierre"] if real else None,
                      "aves_real": int(real["aves_iniciales"]) if real else None,
                      "estado_real": real["estado"] if real else "SIN_INICIAR", "desfase_dias": desfase})
    return filas


def flujo_poa(con: sqlite3.Connection, sesion: Sesion) -> dict[str, Any]:
    """Flujo mensual del POA como tabla (concepto × mes)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    cid = _exigir_carga(con)
    tabla: dict[str, dict[str, Decimal]] = {}
    for r in con.execute("SELECT concepto, mes, monto_cent FROM poa_flujo_mensual WHERE carga_id = ? ORDER BY orden, mes",
                         (cid,)):
        tabla.setdefault(r["concepto"], {})[r["mes"]] = de_centavos(int(r["monto_cent"]))
    return {"meses": list(datos.MESES), "conceptos": [(k, datos.NOMBRE_CONCEPTO[k]) for k in datos.FLUJO_MENSUAL],
            "valores": tabla}


def _limites_mes(mes: str) -> tuple[date, date]:
    anio, m = int(mes[:4]), int(mes[5:])
    inicio = date(anio, m, 1)
    fin = (date(anio + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    return inicio, fin


def flujo_poa_vs_real(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Por mes del POA: cobros, pagos y saldo final proyectados frente a los
    reales (movimientos de 1.1.01.xx sin traspasos entre disponibilidades)."""
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    f = flujo_poa(con, sesion)["valores"]
    marcas = ",".join("?" * len(DISPONIBLES))
    filas = []
    for mes in datos.MESES:
        desde, hasta = _limites_mes(mes)
        cobros = pagos = 0
        for a in con.execute(
                f"SELECT d.asiento_id, SUM(d.debe_cent) AS debe, SUM(d.haber_cent) AS haber FROM asiento_detalle d "
                f"JOIN asientos a ON a.id = d.asiento_id WHERE a.fecha BETWEEN ? AND ? AND a.tipo NOT IN "
                f"('APERTURA','CIERRE') AND d.cuenta_codigo IN ({marcas}) GROUP BY d.asiento_id",
                (desde.isoformat(), hasta.isoformat(), *DISPONIBLES)):
            neto = int(a["debe"]) - int(a["haber"])
            cobros += max(neto, 0)
            pagos += max(-neto, 0)
        saldo = con.execute(f"SELECT COALESCE(SUM(d.debe_cent) - SUM(d.haber_cent), 0) FROM asiento_detalle d JOIN "
                            f"asientos a ON a.id = d.asiento_id WHERE a.fecha <= ? AND d.cuenta_codigo IN ({marcas})",
                            (hasta.isoformat(), *DISPONIBLES)).fetchone()[0]
        hay_real = con.execute("SELECT 1 FROM asientos WHERE fecha BETWEEN ? AND ? AND tipo <> 'APERTURA' LIMIT 1",
                               (desde.isoformat(), hasta.isoformat())).fetchone() is not None
        cobros_poa = sum(f[k][mes] for k in datos.INGRESOS)
        pagos_poa = sum(f[k][mes] for k in datos.EGRESOS_OPERACION) + f["CAPEX"][mes]
        saldo_real = de_centavos(int(saldo)) if hay_real else None
        filas.append({"mes": mes, "cobros_poa": cobros_poa, "pagos_poa": pagos_poa, "saldo_poa": f["SALDO_FINAL"][mes],
                      "cobros_real": de_centavos(cobros) if hay_real else None,
                      "pagos_real": de_centavos(pagos) if hay_real else None, "saldo_real": saldo_real,
                      "desvio": (saldo_real - f["SALDO_FINAL"][mes]) if saldo_real is not None else None})
    return filas


def presupuesto_area(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "presupuesto", "VER")
    cid = _exigir_carga(con)
    return [{**dict(r), "ejercicio": de_centavos(int(r["ejercicio_cent"])), "previo": de_centavos(int(r["previo_cent"])),
             "poa": de_centavos(int(r["poa_cent"]))}
            for r in con.execute("SELECT * FROM poa_presupuesto_area WHERE carga_id = ? ORDER BY orden", (cid,))]


def validar_sin_carga() -> None:
    """Comprueba que los datos transcritos tengan la forma esperada (útil para
    el script antes de abrir la base)."""
    if len(datos.CALENDARIO) != 5 or len(datos.MESES) != 9:
        raise DatosInvalidosError("La transcripción del POA no tiene 5 ciclos y 9 meses.")
