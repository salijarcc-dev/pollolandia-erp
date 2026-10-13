"""Indicadores de gestión y verificación previa del Consejo de Vigilancia (Fase 12).

* Cuadro de mando (FIN-TES-13 y Master Plan §1.3): ICL, RINC, ROA, ICA, PMF,
  DPF, CAC, cumplimiento del SME, mortalidad, margen por canal y margen
  operativo, con semáforo según los umbrales vigentes y comparación con lo
  proyectado en el Manual Presupuestario (E.3).
* Tendencias por ciclo y por ejercicio.
* Guía CI-FIN-18: el Consejo de Vigilancia recalcula los indicadores del mes y
  emite el INF-CV-07 ANTES de que la Gerencia General emita el Informe
  Gerencial de Indicadores (FIN-INF-05); sin INF-CV-07 del mes el FIN-INF-05
  se rechaza.

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from db.conexion import de_centavos, obtener_parametros_vigentes, transaccion
from servicios import auditoria, cierre, compras, planillas, presupuesto, produccion, seguridad, tesoreria, ventas
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import de_mil
from servicios.seguridad import Sesion
from utils.formato import a_fecha, fecha_pantalla

VERDE, AMARILLO, ROJO = "VERDE", "AMARILLO", "ROJO"
COLOR = {"BUENO": VERDE, "NORMAL": AMARILLO, "MALO": ROJO, VERDE: VERDE, AMARILLO: AMARILLO, ROJO: ROJO}
NIVEL = {VERDE: "BUENO", AMARILLO: "NORMAL", ROJO: "MALO"}
INDICADORES: dict[str, dict[str, str]] = {
    "ICL": {"nombre": "Índice de costo por ventas", "formula": "Costo de ventas ÷ ventas totales × 100",
            "meta": "< 61 %"},
    "RINC": {"nombre": "Ratio de ingresos no cárnicos", "formula": "(Alimento + gallinaza) ÷ ventas totales × 100",
             "meta": "≥ 15 % (2030)"},
    "ROA": {"nombre": "Retorno sobre activos", "formula": "Excedente neto ÷ activos totales × 100", "meta": "> 18 %"},
    "MARGEN_OPERATIVO": {"nombre": "Margen operativo", "formula": "EBIT ÷ ingresos operativos × 100", "meta": "> 20 %"},
    "SME": {"nombre": "Cumplimiento del SME", "formula": "Saldo de efectivo al inicio del ciclo 1, tras pagar "
                                                         "excedentes ÷ SME × 100", "meta": "≥ 100 %"},
    "ICA": {"nombre": "Conversión alimenticia", "formula": "kg de alimento ÷ kg de carne viva", "meta": "< 2,00"},
    "PMF": {"nombre": "Merma de faenado", "formula": "(Peso vivo − peso canal) ÷ peso vivo × 100", "meta": "< 10 %"},
    "DPF": {"nombre": "Desviación del presupuesto de faenado", "formula": "Jornales reales ÷ presupuesto × 100",
            "meta": "≤ 100 %"},
    "CAC": {"nombre": "Calendario de abastecimiento crítico", "formula": "OC puntuales ÷ OC programadas × 100",
            "meta": "100 %"},
    "MORTALIDAD": {"nombre": "Mortalidad acumulada", "formula": "Aves muertas ÷ aves iniciales × 100",
                   "meta": "≤ 3 %"},
}
CANALES = ("HORECA", "FRIAL", "MERCADO_POPULAR")


# ==========================================================================
# UTILIDADES
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 1000) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"Indique {campo}.")
    texto = valor.strip()
    if len(texto) > maximo:
        raise DatosInvalidosError(f"El campo {campo} admite hasta {maximo} caracteres.")
    return texto


def _opcional(valor: Any, campo: str, maximo: int = 1000) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    return _texto(valor, campo, maximo)


def _params(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio)


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro vigente «{clave}» (regla 2).", codigo="PARAMETRO_FALTANTE")
    return p[clave]


def _fraccion(num: Decimal | int, den: Decimal | int) -> Decimal | None:
    if not den:
        return None
    return (Decimal(num) / Decimal(den)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _rango(con: sqlite3.Connection, ejercicio: int) -> tuple[date, date]:
    return cierre.rango_ejercicio(con, ejercicio)


def _cifras(con: sqlite3.Connection, desde: date, hasta: date) -> dict[str, int]:
    return cierre._cifras_resultado(cierre.saldos(con, hasta, desde, cierre.TIPOS_FUERA_DE_RESULTADOS))


def _proyectado(con: sqlite3.Connection, ejercicio: int) -> dict[str, Decimal]:
    return {r["clave"]: Decimal(r["valor"]) for r in con.execute(
        "SELECT clave, valor FROM presupuesto_maestro WHERE ejercicio = ? AND modulo = 'INDICADORES' AND valor IS NOT "
        "NULL", (ejercicio,))}


def _indicador(clave: str, valor: Decimal | None, nivel: str | None, proyectado: Decimal | None = None,
               detalle: str | None = None) -> dict[str, Any]:
    return {"clave": clave, **INDICADORES[clave], "valor": valor, "nivel": nivel,
            "semaforo": COLOR.get(nivel) if nivel else None, "proyectado": proyectado, "detalle": detalle}


# ==========================================================================
# 1. INDICADORES (semáforos de la sección 1.3)
# ==========================================================================
def semaforo_margen_operativo(p: Mapping[str, Any], valor: Decimal | None) -> str | None:
    """Margen operativo: Malo < 20 %; Normal 20 % a 30 %; Bueno > 30 % (Manual E.3)."""
    if valor is None:
        return None
    if valor < Decimal(_param(p, "margen_operativo_malo")):
        return "MALO"
    return "BUENO" if valor > Decimal(_param(p, "margen_operativo_bueno")) else "NORMAL"


def margen_operativo(c: Mapping[str, int]) -> Decimal | None:
    return _fraccion(c["ebit"], c["ingresos_operativos"])


def saldo_inicio_ejercicio(con: sqlite3.Connection, ejercicio: int) -> Decimal:
    """Saldo de efectivo al inicio del ciclo 1 tras pagar los excedentes del
    ejercicio anterior (Manual E.3): saldo previo al ejercicio + asiento de
    apertura − excedentes pagados durante el ciclo 1."""
    inicio, _ = _rango(con, ejercicio)
    ciclo1 = presupuesto.ciclos_ejercicio(con, ejercicio)[0]
    previo = con.execute(
        "SELECT COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0) FROM asiento_detalle d JOIN asientos a ON "
        "a.id = d.asiento_id WHERE d.cuenta_codigo LIKE '1.1.01.%' AND (a.fecha < ? OR (a.fecha = ? AND a.tipo = "
        "'APERTURA'))", (inicio.isoformat(), inicio.isoformat())).fetchone()[0]
    excedentes = con.execute(
        "SELECT COALESCE(SUM(d.debe_cent), 0) FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id WHERE "
        "d.cuenta_codigo = '2.1.05.01' AND a.fecha BETWEEN ? AND ? AND a.tipo NOT IN ('CIERRE','APERTURA')",
        (ciclo1["desde"].isoformat(), ciclo1["hasta"].isoformat())).fetchone()[0]
    return de_centavos(int(previo) - int(excedentes))


def cumplimiento_sme(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    inicio, _ = _rango(con, ejercicio)
    saldo = saldo_inicio_ejercicio(con, ejercicio)
    sme = tesoreria.sme_vigente(con, inicio)
    valor = _fraccion(saldo, sme)
    return {"saldo": saldo, "sme": sme, "valor": valor,
            "nivel": tesoreria.semaforo_sme(con, inicio, valor) if valor is not None else None}


def _faenados(con: sqlite3.Connection, desde: date, hasta: date) -> dict[str, int]:
    fila = con.execute(
        "SELECT COALESCE(SUM(peso_vivo_mil), 0), COALESCE(SUM(peso_canal_mil), 0), COALESCE(SUM(alimento_kg_mil), 0), "
        "COUNT(*) FROM faenados WHERE fecha BETWEEN ? AND ?", (desde.isoformat(), hasta.isoformat())).fetchone()
    return {"vivo": int(fila[0]), "canal": int(fila[1]), "alimento": int(fila[2]), "lotes": int(fila[3])}


def _mortalidad(con: sqlite3.Connection, desde: date, hasta: date) -> tuple[int, int]:
    aves = muertas = 0
    for c in con.execute("SELECT id, aves_iniciales FROM ciclos_produccion WHERE fecha_inicio BETWEEN ? AND ?",
                         (desde.isoformat(), hasta.isoformat())):
        aves += int(c["aves_iniciales"])
        muertas += produccion.muertas_ciclo(con, int(c["id"]))
    return muertas, aves


def _dpf(con: sqlite3.Connection, desde: date, hasta: date) -> tuple[int, int]:
    fila = con.execute("SELECT COALESCE(SUM(total_cent), 0), COALESCE(SUM(presupuesto_cent), 0) FROM "
                       "liquidaciones_eventuales WHERE fecha BETWEEN ? AND ?",
                       (desde.isoformat(), hasta.isoformat())).fetchone()
    return int(fila[0]), int(fila[1])


def _cac(con: sqlite3.Connection, desde: date, hasta: date) -> tuple[int, int]:
    fila = con.execute(
        "SELECT COALESCE(SUM(emitida_puntual = 1), 0), COUNT(*) FROM ordenes_compra WHERE es_emergencia = 0 AND "
        "estado <> 'ANULADA' AND fecha_programada BETWEEN ? AND ?", (desde.isoformat(), hasta.isoformat())).fetchone()
    return int(fila[0]), int(fila[1])


def margen_por_canal(con: sqlite3.Connection, desde: date, hasta: date) -> list[dict[str, Any]]:
    """Margen bruto por canal: (precio promedio − costo unitario de referencia)
    ÷ precio, con las notas de entrega de carne (netas de devoluciones)."""
    try:
        costo, _origen, etiqueta = ventas.costo_referencia(con, hasta)
    except (ReglaNegocioError, DatosInvalidosError):
        return []
    p = _params(con, presupuesto.ejercicio_de(con, hasta))
    minimo = Decimal(str(_param(p, "margen_bruto_canal_min")))
    filas = []
    for canal in CANALES:
        ingresos = kg = 0
        for n in con.execute("SELECT id, total_cent, cantidad_mil FROM notas_entrega WHERE canal = ? AND fecha BETWEEN "
                             "? AND ? AND producto IN ('CARNE_ENTERO','CARNE_CORTES')",
                             (canal, desde.isoformat(), hasta.isoformat())):
            dev = con.execute("SELECT COALESCE(SUM(monto_cent), 0), COALESCE(SUM(cantidad_mil), 0) FROM devoluciones "
                              "WHERE nota_id = ?", (n["id"],)).fetchone()
            ingresos += int(n["total_cent"]) - int(dev[0])
            kg += int(n["cantidad_mil"]) - int(dev[1])
        if kg <= 0:
            continue
        precio = (de_centavos(ingresos) / de_mil(kg)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        margen = ((precio - costo) / precio).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        filas.append({"canal": canal, "ingresos": de_centavos(ingresos), "kg": de_mil(kg), "precio": precio,
                      "costo_unitario": costo, "referencia": etiqueta, "margen": margen,
                      "semaforo": VERDE if margen >= minimo else ROJO})
    return filas


def _indicadores_ventana(con: sqlite3.Connection, desde: date, hasta: date, ejercicio: int,
                         activo_total: int | None) -> dict[str, dict[str, Any]]:
    p = _params(con, ejercicio)
    c = _cifras(con, desde, hasta)
    base = cierre._indicadores(c, activo_total or 0, p)
    resultado: dict[str, dict[str, Any]] = {}
    for clave in ("ICL", "RINC"):
        resultado[clave] = _indicador(clave, base[clave]["valor"], base[clave]["semaforo"])
    if activo_total:
        resultado["ROA"] = _indicador("ROA", base["ROA"]["valor"], base["ROA"]["semaforo"])
    mo = margen_operativo(c)
    resultado["MARGEN_OPERATIVO"] = _indicador("MARGEN_OPERATIVO", mo, semaforo_margen_operativo(p, mo))
    f = _faenados(con, desde, hasta)
    if f["vivo"]:
        ica = (Decimal(f["alimento"]) / Decimal(f["vivo"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pmf = _fraccion(f["vivo"] - f["canal"], f["vivo"])
        resultado["ICA"] = _indicador("ICA", ica, produccion.semaforo_ica(con, hasta, ica),
                                      detalle=f"{f['lotes']} lote(s) faenado(s)")
        resultado["PMF"] = _indicador("PMF", pmf, produccion.semaforo_pmf(con, hasta, pmf))
    else:
        resultado["ICA"] = _indicador("ICA", None, None, detalle="Sin faenados registrados en el período")
        resultado["PMF"] = _indicador("PMF", None, None, detalle="Sin faenados registrados en el período")
    muertas, aves = _mortalidad(con, desde, hasta)
    if aves:
        mort = _fraccion(muertas, aves)
        sem = produccion.semaforo_mortalidad(con, hasta, mort)
        resultado["MORTALIDAD"] = _indicador("MORTALIDAD", mort, NIVEL[sem], detalle=f"{muertas} de {aves} aves")
    else:
        resultado["MORTALIDAD"] = _indicador("MORTALIDAD", None, None, detalle="Sin ciclos registrados en el período")
    real, presu = _dpf(con, desde, hasta)
    if presu:
        dpf = _fraccion(real, presu)
        resultado["DPF"] = _indicador("DPF", dpf, planillas.semaforo_dpf(con, hasta, dpf * 100))
    else:
        resultado["DPF"] = _indicador("DPF", None, None, detalle="Sin liquidaciones de personal eventual")
    puntuales, programadas = _cac(con, desde, hasta)
    if programadas:
        cac = _fraccion(puntuales, programadas)
        resultado["CAC"] = _indicador("CAC", cac, compras.semaforo_cac(con, hasta, cac * 100),
                                      detalle=f"{puntuales} de {programadas} órdenes")
    else:
        resultado["CAC"] = _indicador("CAC", None, None, detalle="Sin órdenes de compra programadas")
    return resultado


def indicadores_ejercicio(con: sqlite3.Connection, sesion: Sesion, ejercicio: int,
                          hasta: Any = None) -> dict[str, Any]:
    """Cuadro de mando del ejercicio (o acumulado hasta una fecha) frente a lo
    proyectado en el Manual Presupuestario (E.3)."""
    seguridad.exigir_permiso(con, sesion, "dashboard", "VER")
    inicio, fin = _rango(con, ejercicio)
    corte = min(a_fecha(hasta), fin) if hasta is not None else fin
    activo = cierre._activo_total(cierre.saldos(con, corte))
    ind = _indicadores_ventana(con, inicio, corte, ejercicio, activo)
    sme = cumplimiento_sme(con, ejercicio)
    ind["SME"] = _indicador("SME", sme["valor"], sme["nivel"],
                            detalle=f"Saldo Bs {sme['saldo']:,.2f} / SME Bs {sme['sme']:,.2f}".replace(",", "X")
                            .replace(".", ",").replace("X", "."))
    proyectado = _proyectado(con, ejercicio)
    for clave, valor in proyectado.items():
        if clave in ind:
            ind[clave]["proyectado"] = valor
    c = _cifras(con, inicio, corte)
    return {"ejercicio": ejercicio, "desde": inicio.isoformat(), "hasta": corte.isoformat(), "indicadores": ind,
            "margen_canal": margen_por_canal(con, inicio, corte),
            "cifras": {k: de_centavos(v) for k, v in c.items()}, "activo_total": de_centavos(activo)}


def tendencia_ejercicios(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """ICL, RINC, ROA, margen operativo y SME por ejercicio, reales (si hubo
    operaciones) y proyectados."""
    seguridad.exigir_permiso(con, sesion, "dashboard", "VER")
    filas = []
    for e in presupuesto.ejercicios(con):
        inicio, fin = _rango(con, e)
        c = _cifras(con, inicio, fin)
        proyectado = _proyectado(con, e)
        fila: dict[str, Any] = {"ejercicio": e, "operaciones": c["ventas_totales"] > 0}
        if fila["operaciones"]:
            ind = _indicadores_ventana(con, inicio, fin, e, cierre._activo_total(cierre.saldos(con, fin)))
            for clave in ("ICL", "RINC", "ROA", "MARGEN_OPERATIVO"):
                fila[clave] = ind[clave]["valor"]
            fila["SME"] = cumplimiento_sme(con, e)["valor"]
        for clave in ("ICL", "RINC", "ROA", "MARGEN_OPERATIVO", "SME"):
            fila[f"{clave}_proyectado"] = proyectado.get(clave)
        filas.append(fila)
    return filas


def tendencia_ciclos(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    """Indicadores de cada ciclo del ejercicio: ICL, margen operativo, ICA,
    PMF, mortalidad y SME al inicio del ciclo."""
    seguridad.exigir_permiso(con, sesion, "dashboard", "VER")
    filas = []
    for v in presupuesto.ciclos_ejercicio(con, ejercicio):
        c = _cifras(con, v["desde"], v["hasta"])
        ind = _indicadores_ventana(con, v["desde"], v["hasta"], ejercicio, None)
        saldo = tesoreria.saldo_bancario(con, v["desde"] - timedelta(days=1)) + \
            tesoreria.saldo_cuenta(con, "1.1.01.05", v["desde"] - timedelta(days=1))
        if v["numero"] == 1:
            saldo = saldo_inicio_ejercicio(con, ejercicio)
        sme = tesoreria.sme_vigente(con, v["desde"])
        filas.append({"ciclo": v["numero"], "desde": v["desde"].isoformat(), "hasta": v["hasta"].isoformat(),
                      "ventas": de_centavos(c["ventas_totales"]), "costo_ventas": de_centavos(c["costo_ventas"]),
                      "ebit": de_centavos(c["ebit"]),
                      "ICL": ind["ICL"]["valor"], "MARGEN_OPERATIVO": ind["MARGEN_OPERATIVO"]["valor"],
                      "ICA": ind["ICA"]["valor"], "PMF": ind["PMF"]["valor"], "MORTALIDAD": ind["MORTALIDAD"]["valor"],
                      "SME": _fraccion(saldo, sme), "saldo_inicio": saldo})
    return filas


# ==========================================================================
# 2. CI-FIN-18: INF-CV-07 ANTES DEL FIN-INF-05
# ==========================================================================
def _periodo(con: sqlite3.Connection, periodo_id: int) -> sqlite3.Row:
    if isinstance(periodo_id, bool) or not isinstance(periodo_id, int):
        raise DatosInvalidosError("Indique el período.")
    fila = con.execute("SELECT * FROM periodos WHERE id = ?", (periodo_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el período {periodo_id}.")
    return fila


def _clave_periodo(periodo: sqlite3.Row) -> str:
    return periodo["fecha_inicio"][:7]


def verificacion_previa(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> dict[str, Any]:
    """Insumos de la verificación del CV (CI-FIN-18, Pasos 1 a 4): FIN-INF-04
    del mes, recálculo independiente de ICL, RINC y ROA desde las cifras base,
    partidas del FIN-INF-03 para muestrear y razonabilidad del margen bruto
    frente al mes anterior y al presupuesto."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    return _verificacion_previa(con, _periodo(con, periodo_id))


def _recalculo(c: Mapping[str, int], activo: int) -> dict[str, Decimal | None]:
    """Recálculo independiente (Paso 3) desde las cifras del Estado de Resultados."""
    ventas_totales = c["carne"] + c["alimento"] - c["devoluciones"] + c["gallinaza"]
    return {"ICL": _fraccion(c["costo_ventas"], ventas_totales),
            "RINC": _fraccion(c["alimento"] + c["gallinaza"], ventas_totales),
            "ROA": _fraccion(c["excedente_neto"], activo)}


def _verificacion_previa(con: sqlite3.Connection, periodo: sqlite3.Row) -> dict[str, Any]:
    desde, hasta = date.fromisoformat(periodo["fecha_inicio"]), date.fromisoformat(periodo["fecha_fin"])
    ejercicio = int(periodo["ejercicio"])
    fin_inf_04 = cierre._fin_inf_04(con, periodo)
    fin_inf_03 = cierre._fin_inf_03(con, periodo)
    inicio_ej, _ = _rango(con, ejercicio)
    acumulado = _cifras(con, inicio_ej, hasta)
    activo = cierre._activo_total(cierre.saldos(con, hasta))
    recalculo = _recalculo(acumulado, activo)
    reportado = {k: v["valor"] for k, v in fin_inf_04["indicadores_acumulado"].items()}
    coincide = all(recalculo[k] == reportado.get(k) for k in recalculo)
    mes = _cifras(con, desde, hasta)
    anterior_fin = desde - timedelta(days=1)
    anterior = _cifras(con, anterior_fin.replace(day=1), anterior_fin)
    margen_mes = _fraccion(mes["utilidad_bruta"], mes["ingresos_operativos"])
    margen_ant = _fraccion(anterior["utilidad_bruta"], anterior["ingresos_operativos"])
    ventanas = [c for c in presupuesto.ciclos_ejercicio(con, ejercicio) if desde <= c["hasta"] <= hasta]
    margen_presu = None
    if ventanas:
        ing = sum((presupuesto._linea(con, ejercicio, c["numero"], p)["monto"] for c in ventanas
                   for p in ("ING_CARNE", "ING_ALIMENTO")), Decimal(0))
        cos = sum((presupuesto._linea(con, ejercicio, c["numero"], p)["monto"] for c in ventanas
                   for p in ("MP_POLLITOS", "MP_ALIMENTO", "MP_OTROS", "MOD_FIJA", "MOD_EVENTUAL", "CIF")), Decimal(0))
        margen_presu = _fraccion(ing - cos, ing) if ing else None
    lista = con.execute("SELECT * FROM listas_cierre WHERE periodo_id = ? ORDER BY id DESC LIMIT 1",
                        (periodo["id"],)).fetchone()
    partidas = [f"{c['ciclo']}: costo Bs {c['costo_total']} ({c['kg_producidos']} kg)" for c in fin_inf_03["ciclos"]]
    partidas += [f"Costo de ventas de carne 6.1.02.01: Bs {fin_inf_03['costo_ventas_carne']}",
                 f"Costo de ventas de alimento 6.1.02.02: Bs {fin_inf_03['costo_ventas_alimento']}",
                 f"Kg vendidos según notas de entrega: {fin_inf_03['kg_vendidos']}"]
    for cuenta, nombre, monto in con.execute(
            "SELECT d.cuenta_codigo, c.nombre, SUM(d.debe_cent + d.haber_cent) AS m FROM asiento_detalle d JOIN asientos a "
            "ON a.id = d.asiento_id JOIN cuentas c ON c.codigo = d.cuenta_codigo WHERE a.fecha BETWEEN ? AND ? AND "
            "a.tipo NOT IN ('CIERRE','APERTURA') AND (d.cuenta_codigo LIKE '5.%' OR d.cuenta_codigo LIKE '6.%' OR "
            "d.cuenta_codigo LIKE '7.%') GROUP BY d.cuenta_codigo ORDER BY m DESC LIMIT 6",
            (desde.isoformat(), hasta.isoformat())):
        partidas.append(f"{cuenta} {nombre}: movimiento Bs {de_centavos(int(monto))}")
    return {"periodo_id": int(periodo["id"]), "periodo": cierre.nombre_periodo(periodo), "clave": _clave_periodo(periodo),
            "ejercicio": ejercicio, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "fin_inf_01": None if lista is None else {"fecha": lista["fecha"], "pendientes": int(lista["pendientes"])},
            "fin_inf_03": fin_inf_03, "partidas_disponibles": partidas,
            "reportado": reportado, "recalculo": recalculo, "coincide": coincide,
            "semaforos": {k: v["semaforo"] for k, v in fin_inf_04["indicadores_acumulado"].items()},
            "margen_bruto_mes": margen_mes, "margen_bruto_mes_anterior": margen_ant,
            "margen_bruto_presupuesto": margen_presu}


def _serial(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dict):
        return {k: _serial(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serial(v) for v in valor]
    return valor


def registrar_inf_cv_07(con: sqlite3.Connection, sesion: Sesion, periodo_id: int, fecha: Any, *,
                        partidas_muestreadas: Sequence[str], partidas_conformes: bool, recalculo_conforme: bool,
                        razonabilidad_conforme: bool, observaciones: str | None = None) -> dict[str, Any]:
    """INF-CV-07 (CI-FIN-18 Paso 5): el Consejo de Vigilancia consigna su
    verificación del mes ANTES de la comunicación oficial de los indicadores.

    Requisitos: FIN-INF-01 registrada sin pendientes y al menos
    ``cv07_partidas_min`` partidas significativas del FIN-INF-03 muestreadas."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "CREAR")
    f = a_fecha(fecha)
    muestra = [m.strip() for m in partidas_muestreadas if isinstance(m, str) and m.strip()]
    with transaccion(con):
        periodo = _periodo(con, periodo_id)
        hasta = date.fromisoformat(periodo["fecha_fin"])
        if f < hasta:
            raise DatosInvalidosError(f"El INF-CV-07 se emite tras el cierre del mes ({fecha_pantalla(hasta)}).")
        p = _params(con, int(periodo["ejercicio"]))
        minimo = int(_param(p, "cv07_partidas_min"))
        if len(set(muestra)) < minimo:
            raise ReglaNegocioError(f"CI-FIN-18 Paso 2: seleccione al menos {minimo} partidas significativas del "
                                    "FIN-INF-03.", codigo="MUESTRA_INSUFICIENTE")
        datos = _verificacion_previa(con, periodo)
        if datos["fin_inf_01"] is None:
            raise ReglaNegocioError("CI-FIN-18 requiere la Lista de Verificación de Cierre (FIN-INF-01) del mismo "
                                    "período.", codigo="SIN_FIN_INF_01")
        if datos["fin_inf_01"]["pendientes"]:
            raise ReglaNegocioError("La FIN-INF-01 del mes tiene comprobantes pendientes.", codigo="PENDIENTES_CIERRE")
        if con.execute("SELECT 1 FROM informes_gerenciales WHERE periodo_id = ?", (periodo_id,)).fetchone():
            raise ReglaNegocioError("El FIN-INF-05 del mes ya fue emitido: el INF-CV-07 debe ser previo.",
                                    codigo="SECUENCIA_INVERTIDA")
        clave = _clave_periodo(periodo)
        if con.execute("SELECT 1 FROM informes_cv WHERE codigo = 'INF-CV-07' AND periodo = ?", (clave,)).fetchone():
            raise ReglaNegocioError("El INF-CV-07 de ese mes ya está registrado.", codigo="YA_REGISTRADO")
        conforme = partidas_conformes and recalculo_conforme and razonabilidad_conforme
        obs = _opcional(observaciones, "observaciones", 2000)
        if not conforme and obs is None:
            raise DatosInvalidosError("Detalle las observaciones de la verificación.")
        contenido = {**datos, "muestra": muestra,
                     "resultados": {"partidas": partidas_conformes, "recalculo": recalculo_conforme,
                                    "razonabilidad": razonabilidad_conforme},
                     "emitido_antes_de_comunicar": True}
        contenido.pop("fin_inf_03", None)
        iid = int(con.execute(
            """INSERT INTO informes_cv (codigo, ejercicio, periodo, periodo_id, fecha, datos_json, dictamen, observaciones,
                   elaborado_por, usuario_id, registrado_en) VALUES ('INF-CV-07',?,?,?,?,?,?,?,?,?,?)""",
            (int(periodo["ejercicio"]), clave, periodo_id, f.isoformat(), json.dumps(_serial(contenido),
                                                                                     ensure_ascii=False),
             "CONFORME" if conforme else "CON_OBSERVACIONES", obs, sesion.nombre_completo, sesion.usuario_id,
             _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "informes_cv", "INSERT", iid,
                            despues={"codigo": "INF-CV-07", "periodo": clave, "dictamen":
                                     "CONFORME" if conforme else "CON_OBSERVACIONES", "muestra": muestra})
    return {"id": iid, "dictamen": "CONFORME" if conforme else "CON_OBSERVACIONES", "coincide": datos["coincide"]}


def inf_cv_07_del_mes(con: sqlite3.Connection, periodo_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM informes_cv WHERE codigo = 'INF-CV-07' AND periodo_id = ?",
                       (periodo_id,)).fetchone()


def emitir_fin_inf_05(con: sqlite3.Connection, sesion: Sesion, periodo_id: int, fecha: Any, *,
                      decisiones: Mapping[str, Mapping[str, Any]] | None = None,
                      observaciones_incorporadas: str | None = None) -> dict[str, Any]:
    """FIN-INF-05 (CI-FIN-18 Paso 6): la Gerencia General comunica los
    indicadores como definitivos SOLO si existe el INF-CV-07 del mes; si el
    INF-CV-07 tuvo observaciones, deben constar como incorporadas. Todo
    indicador en «Malo» exige una acción o decisión gerencial."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "AUTORIZAR")
    if sesion.rol != "GG":
        raise AutorizacionError("El Informe Gerencial de Indicadores lo emite la Gerencia General (PR-FIN-10 paso 6).")
    f = a_fecha(fecha)
    decisiones = decisiones or {}
    with transaccion(con):
        periodo = _periodo(con, periodo_id)
        cv07 = inf_cv_07_del_mes(con, periodo_id)
        if cv07 is None:
            raise ReglaNegocioError(
                f"CI-FIN-18: no se puede emitir el FIN-INF-05 de {cierre.nombre_periodo(periodo)} sin el Informe de "
                "Fiscalización Mensual (INF-CV-07) del Consejo de Vigilancia del mismo período, aunque ello retrase el "
                "informe.", codigo="SIN_INF_CV_07")
        if f < date.fromisoformat(cv07["fecha"]):
            raise DatosInvalidosError("El FIN-INF-05 no puede ser anterior al INF-CV-07.")
        if con.execute("SELECT 1 FROM informes_gerenciales WHERE periodo_id = ?", (periodo_id,)).fetchone():
            raise ReglaNegocioError("El FIN-INF-05 de ese mes ya fue emitido.", codigo="YA_REGISTRADO")
        incorporadas = _opcional(observaciones_incorporadas, "observaciones incorporadas", 2000)
        if cv07["dictamen"] == "CON_OBSERVACIONES" and incorporadas is None:
            raise ReglaNegocioError("El INF-CV-07 tiene observaciones: indique cómo se incorporaron (CI-FIN-18 Paso 6).",
                                    codigo="OBSERVACIONES_SIN_INCORPORAR")
        datos = cierre._fin_inf_04(con, periodo)
        indicadores = {}
        for clave, v in datos["indicadores_acumulado"].items():
            d = decisiones.get(clave) or {}
            accion = _opcional(d.get("accion"), f"la acción para {clave}", 500)
            desviacion = bool(d.get("desviacion", v["semaforo"] == "MALO"))
            if v["semaforo"] == "MALO" and accion is None:
                raise ReglaNegocioError(f"El {clave} está en «Malo»: registre la acción o decisión gerencial.",
                                        codigo="FALTA_DECISION")
            indicadores[clave] = {"valor": v["valor"], "umbral": v["semaforo"], "desviacion": desviacion,
                                  "accion": accion}
        iid = int(con.execute(
            """INSERT INTO informes_gerenciales (periodo_id, inf_cv_07_id, fecha, indicadores_json,
                   observaciones_incorporadas, decisiones_json, emitido_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (periodo_id, int(cv07["id"]), f.isoformat(), json.dumps(_serial(indicadores), ensure_ascii=False),
             incorporadas, json.dumps(_serial(decisiones), ensure_ascii=False), sesion.nombre_completo,
             _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "informes_gerenciales", "INSERT", iid,
                            despues={"periodo": _clave_periodo(periodo), "inf_cv_07": int(cv07["id"]),
                                     "indicadores": _serial(indicadores)})
    return {"id": iid, "indicadores": indicadores}


def estado_ci_fin_18(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    """Secuencia de cada mes: FIN-INF-01 → INF-CV-07 → FIN-INF-05."""
    seguridad.exigir_permiso(con, sesion, "dashboard", "VER")
    filas = []
    for per in con.execute("SELECT * FROM periodos WHERE ejercicio = ? ORDER BY numero", (ejercicio,)):
        lista = con.execute("SELECT fecha, pendientes FROM listas_cierre WHERE periodo_id = ? ORDER BY id DESC LIMIT 1",
                            (per["id"],)).fetchone()
        cv07 = inf_cv_07_del_mes(con, int(per["id"]))
        gg = con.execute("SELECT fecha, emitido_por FROM informes_gerenciales WHERE periodo_id = ?",
                         (per["id"],)).fetchone()
        filas.append({"periodo_id": int(per["id"]), "periodo": cierre.nombre_periodo(per), "fin": per["fecha_fin"],
                      "fin_inf_01": lista["fecha"] if lista else None,
                      "inf_cv_07": cv07["fecha"] if cv07 else None, "dictamen": cv07["dictamen"] if cv07 else None,
                      "fin_inf_05": gg["fecha"] if gg else None,
                      "puede_emitir": cv07 is not None and gg is None})
    return filas


def listar_fin_inf_05(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "dashboard", "VER")
    return [{**dict(r), "indicadores": json.loads(r["indicadores_json"]), "periodo": cierre.nombre_periodo(r)}
            for r in con.execute("SELECT g.*, p.fecha_inicio, p.fecha_fin FROM informes_gerenciales g JOIN periodos p "
                                 "ON p.id = g.periodo_id ORDER BY p.fecha_inicio DESC")]
