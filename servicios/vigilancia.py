"""Panel del Consejo de Vigilancia (Fase 12).

El CV verifica sin aprobar ni ejecutar (MOF §2; Manual Presupuestario P-09):

* Panel de cumplimiento de sus controles del ejercicio: INF-CV-01 (activos),
  INF-CV-02 (liquidez trimestral), INF-CV-03 (arqueos), inventario sorpresivo,
  INF-CV-04 (excedentes), INF-CV-05 (provisiones laborales), INF-CV-06
  (precios), INF-CV-07 (fiscalización mensual, CI-FIN-18), INF-CV-08 (EEFF) e
  INF-CV-09 (RAU), con la frecuencia fijada en ``parametros``.
* Inventario físico sorpresivo de insumos contra el kárdex (diferencias a la
  Asamblea General).
* Verificación de desembolsos con firma mancomunada: el miembro del CV que
  cofirmó un desembolso NO puede ser quien lo verifica (segregación, MOF §3.2).
* INF-CV-02, INF-CV-05, INF-CV-06 e INF-CV-08 con datos del sistema.

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import a_centavos, de_centavos, obtener_parametros_vigentes, transaccion
from servicios import activos, auditoria, cierre, presupuesto, seguridad, tesoreria, ventas
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil
from servicios.seguridad import Sesion
from utils.formato import a_fecha, fecha_pantalla

VERDE, AMARILLO, ROJO = "VERDE", "AMARILLO", "ROJO"
DICTAMENES = ("CONFORME", "CON_OBSERVACIONES")
INFORMES_CV: dict[str, str] = {
    "INF-CV-01": "Acta de Verificación Física de Activos", "INF-CV-02": "Informe Trimestral de Fiscalización de Liquidez",
    "INF-CV-03": "Acta de Arqueo de Caja", "INF-CV-04": "Informe Previo de Fiscalización (excedentes)",
    "INF-CV-05": "Informe de Verificación de Provisiones Laborales", "INF-CV-06": "Informe de Revisión de Precios",
    "INF-CV-07": "Informe de Fiscalización Mensual (CI-FIN-18)",
    "INF-CV-08": "Informe de Verificación del Consejo de Vigilancia (EEFF)",
    "INF-CV-09": "Informe de Fiscalización Tributaria (RAU)",
}


# ==========================================================================
# UTILIDADES
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _opcional(valor: Any, campo: str, maximo: int = 2000) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if not isinstance(valor, str):
        raise DatosInvalidosError(f"El campo {campo} debe ser texto.")
    texto = valor.strip()
    if len(texto) > maximo:
        raise DatosInvalidosError(f"El campo {campo} admite hasta {maximo} caracteres.")
    return texto


def _params(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio)


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro vigente «{clave}» (regla 2).", codigo="PARAMETRO_FALTANTE")
    return p[clave]


def _serial(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, dict):
        return {k: _serial(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serial(v) for v in valor]
    return valor


def _exigir_cv(con: sqlite3.Connection, sesion: Sesion, accion: str = "CREAR") -> None:
    seguridad.exigir_permiso(con, sesion, "vigilancia", accion)
    if sesion.rol != "CV":
        raise AutorizacionError("Este registro corresponde a un miembro del Consejo de Vigilancia.")


def _insertar_informe(con: sqlite3.Connection, sesion: Sesion, codigo: str, ejercicio: int, periodo: str,
                      fecha: date, datos: Mapping[str, Any], dictamen: str, observaciones: str | None) -> int:
    if dictamen not in DICTAMENES:
        raise DatosInvalidosError("El dictamen debe ser CONFORME o CON_OBSERVACIONES.")
    if dictamen == "CON_OBSERVACIONES" and not observaciones:
        raise DatosInvalidosError("Detalle las observaciones.")
    if con.execute("SELECT 1 FROM informes_cv WHERE codigo = ? AND periodo = ?", (codigo, periodo)).fetchone():
        raise ReglaNegocioError(f"El {codigo} de {periodo} ya está registrado.", codigo="YA_REGISTRADO")
    iid = int(con.execute(
        """INSERT INTO informes_cv (codigo, ejercicio, periodo, fecha, datos_json, dictamen, observaciones, elaborado_por,
               usuario_id, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (codigo, ejercicio, periodo, fecha.isoformat(), json.dumps(_serial(datos), ensure_ascii=False), dictamen,
         observaciones, sesion.nombre_completo, sesion.usuario_id, _ahora_iso())).lastrowid)
    auditoria.registrar(con, sesion, "informes_cv", "INSERT", iid,
                        despues={"codigo": codigo, "periodo": periodo, "dictamen": dictamen})
    return iid


def listar_informes_cv(con: sqlite3.Connection, sesion: Sesion, codigo: str | None = None,
                       ejercicio: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    sql, args = "SELECT * FROM informes_cv WHERE 1 = 1", []
    if codigo:
        sql += " AND codigo = ?"
        args.append(codigo)
    if ejercicio is not None:
        sql += " AND ejercicio = ?"
        args.append(ejercicio)
    return [{**dict(r), "datos": json.loads(r["datos_json"])}
            for r in con.execute(sql + " ORDER BY fecha DESC, id DESC", args)]


# ==========================================================================
# 1. INF-CV-02: LIQUIDEZ TRIMESTRAL (SME AL CIERRE DE CADA CICLO)
# ==========================================================================
def _cumplimiento_ciclos(con: sqlite3.Connection, hasta: date) -> list[dict[str, Any]]:
    """Cumplimiento del SME al cierre de todos los ciclos cerrados hasta la fecha."""
    filas = []
    for e in presupuesto.ejercicios(con):
        for c in presupuesto.ciclos_ejercicio(con, e):
            if c["hasta"] > hasta:
                continue
            saldo = tesoreria.saldo_bancario(con, c["hasta"])
            sme = tesoreria.sme_vigente(con, c["hasta"])
            pct = (saldo / sme).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if sme else None
            filas.append({"ejercicio": e, "ciclo": c["numero"], "cierre": c["hasta"], "sme": sme, "saldo": saldo,
                          "cumplimiento": pct, "cumple": pct is not None and pct >= 1})
    return filas


def datos_inf_cv_02(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, trimestre: int) -> dict[str, Any]:
    """Ciclos que cierran en el trimestre con SME, saldo real al cierre y
    cumplimiento; dos ciclos consecutivos bajo el SME obligan a reportar a la
    Asamblea General (PR-FIN-02 Paso 8)."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    desde, hasta = presupuesto.trimestre_ejercicio(con, ejercicio, trimestre)
    historia = _cumplimiento_ciclos(con, hasta)
    del_trimestre = [f for f in historia if desde <= f["cierre"] <= hasta]
    n = int(_param(_params(con, ejercicio), "inf_cv_02_incumplimientos_reporte"))
    consecutivos = racha = 0
    for f in historia:
        racha = racha + 1 if not f["cumple"] else 0
        consecutivos = max(consecutivos, racha) if f["cierre"] >= desde else consecutivos
    return {"ejercicio": ejercicio, "trimestre": trimestre, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "ciclos": del_trimestre, "consecutivos": consecutivos, "reportar_ag": consecutivos >= n,
            "minimo_reporte": n}


def registrar_inf_cv_02(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, trimestre: int, fecha: Any,
                        observaciones: str | None = None) -> dict[str, Any]:
    _exigir_cv(con, sesion)
    f = a_fecha(fecha)
    with transaccion(con):
        datos = datos_inf_cv_02(con, sesion, ejercicio, trimestre)
        if f < date.fromisoformat(datos["hasta"]):
            raise DatosInvalidosError("El INF-CV-02 se emite al cierre del trimestre.")
        cumplen = all(c["cumple"] for c in datos["ciclos"])
        obs = _opcional(observaciones, "observaciones")
        dictamen = "CONFORME" if cumplen else "CON_OBSERVACIONES"
        if not cumplen and obs is None:
            obs = "Ciclos con saldo bajo el SME: " + ", ".join(
                f"C{c['ciclo']}/{c['ejercicio']} ({fecha_pantalla(c['cierre'])})" for c in datos["ciclos"] if not c["cumple"])
        iid = _insertar_informe(con, sesion, "INF-CV-02", ejercicio, f"{ejercicio}-T{trimestre}", f, datos, dictamen,
                                obs)
    return {"id": iid, "dictamen": dictamen, "reportar_ag": datos["reportar_ag"]}


def registrar_reporte_ag(con: sqlite3.Connection, sesion: Sesion, informe_id: int, fecha: Any) -> None:
    """Fecha en que el informe se reportó a la Asamblea General."""
    _exigir_cv(con, sesion)
    f = a_fecha(fecha)
    with transaccion(con):
        r = con.execute("SELECT * FROM informes_cv WHERE id = ?", (informe_id,)).fetchone()
        if r is None:
            raise DatosInvalidosError(f"No existe el informe {informe_id}.")
        if r["reportado_ag_en"]:
            raise ReglaNegocioError("El informe ya fue reportado a la Asamblea.", codigo="YA_REPORTADO")
        con.execute("UPDATE informes_cv SET reportado_ag_en = ? WHERE id = ?", (f.isoformat(), informe_id))
        auditoria.registrar(con, sesion, "informes_cv", "UPDATE", informe_id, antes={"reportado_ag_en": None},
                            despues={"reportado_ag_en": f})


# ==========================================================================
# 2. INF-CV-05: PROVISIONES LABORALES (ANUAL)
# ==========================================================================
def datos_inf_cv_05(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """Saldos y devengo del ejercicio de aguinaldo, indemnización y aportes."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    inicio, fin = cierre.rango_ejercicio(con, ejercicio)
    s = cierre.saldos(con, fin)

    def devengo(cuentas: tuple[str, ...], sufijo: str | None) -> Decimal:
        sql = ("SELECT COALESCE(SUM(d.debe_cent), 0) FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id "
               "WHERE a.fecha BETWEEN ? AND ? AND a.tipo NOT IN ('CIERRE','APERTURA') AND (d.cuenta_codigo IN (" +
               ",".join("?" * len(cuentas)) + ")" + (" OR d.sufijo = ?" if sufijo else "") + ")")
        args = [inicio.isoformat(), fin.isoformat(), *cuentas] + ([sufijo] if sufijo else [])
        return de_centavos(int(con.execute(sql, args).fetchone()[0]))

    filas = [
        {"concepto": "Provisión de aguinaldo (2.1.02.02)", "devengado": devengo(("7.1.01.02",), "21"),
         "saldo": de_centavos(-s.get("2.1.02.02", 0))},
        {"concepto": "Provisión para indemnización (2.2.01.01)", "devengado": devengo(("7.1.01.04",), "39"),
         "saldo": de_centavos(-s.get("2.2.01.01", 0))},
        {"concepto": "Aportes patronales por pagar (2.1.02.03)", "devengado": devengo(("7.1.01.03",), "38"),
         "saldo": de_centavos(-s.get("2.1.02.03", 0))},
        {"concepto": "Aportes laborales retenidos (2.1.02.05)", "devengado": None,
         "saldo": de_centavos(-s.get("2.1.02.05", 0))},
    ]
    return {"ejercicio": ejercicio, "corte": fin.isoformat(), "filas": filas}


def registrar_inf_cv_05(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any, *,
                        provisiones_suficientes: bool, aportes_al_dia: bool,
                        observaciones: str | None = None) -> dict[str, Any]:
    _exigir_cv(con, sesion)
    f = a_fecha(fecha)
    with transaccion(con):
        datos = {**datos_inf_cv_05(con, sesion, ejercicio), "provisiones_suficientes": provisiones_suficientes,
                 "aportes_al_dia": aportes_al_dia}
        dictamen = "CONFORME" if provisiones_suficientes and aportes_al_dia else "CON_OBSERVACIONES"
        iid = _insertar_informe(con, sesion, "INF-CV-05", ejercicio, str(ejercicio), f, datos, dictamen,
                                _opcional(observaciones, "observaciones"))
    return {"id": iid, "dictamen": dictamen}


# ==========================================================================
# 3. INF-CV-06: REVISIÓN DE PRECIOS (CI-FIN-06 Art. 10)
# ==========================================================================
def datos_inf_cv_06(con: sqlite3.Connection, sesion: Sesion, fecha: Any,
                    precios_mercado: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Tarifario vigente frente al costo unitario del último ciclo (o D.4) y,
    si el CV los registra, frente a los precios de mercado por canal."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    f = a_fecha(fecha)
    ejercicio = presupuesto.ejercicio_de(con, f)
    p = _params(con, ejercicio)
    minimo = Decimal(str(_param(p, "margen_bruto_canal_min")))
    try:
        costo, _origen, referencia = ventas.costo_referencia(con, f)
    except (ReglaNegocioError, DatosInvalidosError) as exc:
        return {"fecha": f.isoformat(), "filas": [], "error": str(exc)}
    filas = []
    for canal in ("HORECA", "FRIAL", "MERCADO_POPULAR"):
        try:
            precio, _tid = ventas.precio_vigente(con, "CARNE_ENTERO", canal, f)
        except ReglaNegocioError:
            continue
        margen = ((precio - costo) / precio).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        mercado = (precios_mercado or {}).get(canal)
        mercado_d = Decimal(str(mercado)) if mercado not in (None, "") else None
        filas.append({"canal": canal, "precio": precio, "costo_unitario": costo, "margen": margen,
                      "bajo_meta": margen < minimo, "precio_mercado": mercado_d,
                      "diferencia_mercado": ((precio - mercado_d) / mercado_d).quantize(
                          Decimal("0.0001"), rounding=ROUND_HALF_UP) if mercado_d else None})
    return {"fecha": f.isoformat(), "referencia": referencia, "margen_minimo": minimo, "filas": filas, "error": None}


def registrar_inf_cv_06(con: sqlite3.Connection, sesion: Sesion, fecha: Any, *, precios_mercado: Mapping[str, Any],
                        observaciones: str | None = None) -> dict[str, Any]:
    _exigir_cv(con, sesion)
    f = a_fecha(fecha)
    with transaccion(con):
        datos = datos_inf_cv_06(con, sesion, f, precios_mercado)
        if not datos["filas"]:
            raise ReglaNegocioError("No hay tarifario vigente o costo de referencia para revisar los precios.",
                                    codigo="SIN_TARIFARIO")
        desviado = any(r["bajo_meta"] for r in datos["filas"])
        obs = _opcional(observaciones, "observaciones")
        if desviado and obs is None:
            obs = "Margen bajo la meta institucional en: " + ", ".join(r["canal"] for r in datos["filas"]
                                                                       if r["bajo_meta"])
        ejercicio = presupuesto.ejercicio_de(con, f)
        iid = _insertar_informe(con, sesion, "INF-CV-06", ejercicio, f.isoformat()[:7], f, datos,
                                "CON_OBSERVACIONES" if desviado or obs else "CONFORME", obs)
    return {"id": iid, "desviado": desviado}


# ==========================================================================
# 4. INF-CV-08: VERIFICACIÓN DE LOS ESTADOS FINANCIEROS
# ==========================================================================
def datos_inf_cv_08(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """Controles de cuadre de los estados del ejercicio (MAN-006)."""
    seguridad.exigir_permiso(con, sesion, "contabilidad", "VER")
    _, fin = cierre.rango_ejercicio(con, ejercicio)
    bg = cierre.balance_general(con, sesion, fin)
    er = cierre.estado_resultados(con, sesion, ejercicio)
    efe = cierre.estado_flujo_efectivo(con, sesion, ejercicio)
    eepn = cierre.estado_evolucion_patrimonio(con, sesion, ejercicio)
    return {"ejercicio": ejercicio, "controles": [
        {"control": "Balance General: activo = pasivo + patrimonio", "cumple": bool(bg["cuadra"])},
        {"control": "Estado de Resultados escalonado cuadra con el mayor", "cumple": bool(er["cuadra"])},
        {"control": "Flujo de Efectivo: saldo final = efectivo del balance", "cumple": bool(efe["cuadra"])},
        {"control": "Evolución del Patrimonio cuadra con el balance", "cumple": bool(eepn["cuadra"])},
    ], "excedente_neto": er["excedente_neto"], "total_activo": bg["total_activo"]}


def registrar_inf_cv_08(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any,
                        observaciones: str | None = None) -> dict[str, Any]:
    _exigir_cv(con, sesion)
    f = a_fecha(fecha)
    with transaccion(con):
        _, fin = cierre.rango_ejercicio(con, ejercicio)
        if f <= fin:
            raise DatosInvalidosError("El INF-CV-08 se emite después del cierre del ejercicio (30 de junio).")
        datos = datos_inf_cv_08(con, sesion, ejercicio)
        cumple = all(c["cumple"] for c in datos["controles"])
        obs = _opcional(observaciones, "observaciones")
        iid = _insertar_informe(con, sesion, "INF-CV-08", ejercicio, str(ejercicio), f, datos,
                                "CONFORME" if cumple and not obs else "CON_OBSERVACIONES",
                                obs or (None if cumple else "Estados financieros que no cuadran."))
    return {"id": iid, "cuadran": cumple}


# ==========================================================================
# 5. INVENTARIO SORPRESIVO (MOF; Manual de Gestión Contable, inventarios)
# ==========================================================================
def insumos_para_conteo(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> list[dict[str, Any]]:
    """Insumos activos con su existencia contable (kárdex) a la fecha."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    f = a_fecha(fecha).isoformat()
    filas = []
    for ins in con.execute("SELECT id, codigo, nombre, unidad FROM insumos WHERE activo = 1 ORDER BY codigo"):
        k = con.execute("SELECT saldo_cantidad_mil, cpp FROM kardex WHERE insumo_id = ? AND fecha <= ? "
                        "ORDER BY fecha DESC, id DESC LIMIT 1", (ins["id"], f)).fetchone()
        filas.append({"insumo_id": int(ins["id"]), "codigo": ins["codigo"], "nombre": ins["nombre"],
                      "unidad": ins["unidad"], "contable": de_mil(int(k["saldo_cantidad_mil"])) if k else Decimal(0),
                      "cpp": Decimal(k["cpp"]) if k else Decimal(0)})
    return filas


def registrar_inventario_sorpresivo(con: sqlite3.Connection, sesion: Sesion, fecha: Any,
                                    conteos: Mapping[int, Any], observacion: str | None = None) -> dict[str, Any]:
    """El CV coteja las existencias físicas con el registro contable; toda
    diferencia se reporta a la Asamblea General."""
    _exigir_cv(con, sesion)
    if not conteos:
        raise DatosInvalidosError("Registre al menos un conteo físico.")
    f = a_fecha(fecha)
    with transaccion(con):
        base = {r["insumo_id"]: r for r in insumos_para_conteo(con, sesion, f)}
        detalle, total, con_dif = [], 0, 0
        for insumo_id, fisico in conteos.items():
            if insumo_id not in base:
                raise DatosInvalidosError(f"El insumo {insumo_id} no existe o no está activo.")
            fisico_mil = a_mil(fisico, "cantidad física", permitir_cero=True)
            contable_mil = a_mil(base[insumo_id]["contable"], "cantidad contable", permitir_cero=True)
            dif_mil = fisico_mil - contable_mil
            dif_cent = a_centavos((Decimal(dif_mil) / 1000 * base[insumo_id]["cpp"]).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP))
            total += dif_cent
            con_dif += int(dif_mil != 0)
            detalle.append((insumo_id, contable_mil, fisico_mil, dif_mil, str(base[insumo_id]["cpp"]), dif_cent))
        ejercicio = presupuesto.ejercicio_de(con, f)
        iid = int(con.execute(
            """INSERT INTO inventarios_sorpresivos (ejercicio, fecha, items, con_diferencia, diferencia_cent, observacion,
                   reportar_ag, verificado_por, usuario_id, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, f.isoformat(), len(detalle), con_dif, total, _opcional(observacion, "observación"),
             int(con_dif > 0), sesion.nombre_completo, sesion.usuario_id, _ahora_iso())).lastrowid)
        for d in detalle:
            con.execute("""INSERT INTO inventario_sorpresivo_detalle (inventario_id, insumo_id, contable_mil, fisico_mil,
                               diferencia_mil, cpp, diferencia_cent) VALUES (?,?,?,?,?,?,?)""", (iid, *d))
        auditoria.registrar(con, sesion, "inventarios_sorpresivos", "INSERT", iid,
                            despues={"fecha": f, "items": len(detalle), "con_diferencia": con_dif,
                                     "diferencia": de_centavos(total)})
    return {"id": iid, "items": len(detalle), "con_diferencia": con_dif, "diferencia": de_centavos(total),
            "reportar_ag": con_dif > 0}


def listar_inventarios(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    filas = []
    for r in con.execute("SELECT * FROM inventarios_sorpresivos ORDER BY fecha DESC, id DESC"):
        detalle = [{**dict(d), "contable": de_mil(int(d["contable_mil"])), "fisico": de_mil(int(d["fisico_mil"])),
                    "diferencia": de_mil(int(d["diferencia_mil"])), "diferencia_bs": de_centavos(int(d["diferencia_cent"]))}
                   for d in con.execute("SELECT d.*, i.codigo, i.nombre FROM inventario_sorpresivo_detalle d JOIN insumos "
                                        "i ON i.id = d.insumo_id WHERE d.inventario_id = ? ORDER BY i.codigo", (r["id"],))]
        filas.append({**dict(r), "diferencia": de_centavos(int(r["diferencia_cent"])), "detalle": detalle})
    return filas

def arqueos_cv(con: sqlite3.Connection, sesion: Sesion, ejercicio: int | None = None) -> list[dict[str, Any]]:
    """Arqueos sorpresivos de caja (INF-CV-03) para el panel del CV, con los
    montos en bolivianos; filtra por ejercicio si se indica."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    sql, args = "SELECT * FROM arqueos_caja", []
    if ejercicio is not None:
        inicio, fin = cierre.rango_ejercicio(con, ejercicio)
        sql += " WHERE fecha BETWEEN ? AND ?"
        args = [inicio.isoformat(), fin.isoformat()]
    return [{**dict(a), "total_fisico": de_centavos(int(a["total_fisico_cent"])),
             "saldo_contable": de_centavos(int(a["saldo_contable_cent"])),
             "diferencia": de_centavos(int(a["diferencia_cent"]))}
            for a in con.execute(sql + " ORDER BY fecha DESC, id DESC", args)]


def verificaciones_activos(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Estado de la verificación física de activos (INF-CV-01): última acta,
    plazo de ``verificacion_fisica_meses`` y actas registradas."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    estado = activos.estado_verificacion(con, a_fecha(fecha))
    actas = [dict(v) for v in con.execute("SELECT * FROM verificaciones_fisicas ORDER BY fecha DESC, id DESC")]
    return {**estado, "actas": actas}


# ==========================================================================
# 6. VERIFICACIÓN DE DESEMBOLSOS (segregación del cofirmante)
# ==========================================================================
def desembolsos_a_verificar(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    """Autorizaciones de desembolso del ejercicio con sus firmas, el pago
    asociado y si ya fueron verificadas; indica si el usuario actual puede
    verificarlas (no firmó)."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    filas = []
    for a in con.execute("SELECT * FROM autorizaciones WHERE tipo_decision = 'DESEMBOLSO' AND ejercicio = ? AND "
                         "estado IN ('AUTORIZADA','UTILIZADA') ORDER BY id", (ejercicio,)):
        firmas = [dict(f) for f in con.execute(
            "SELECT f.calidad, f.usuario_id, u.nombre_completo FROM autorizacion_firmas f JOIN usuarios u ON "
            "u.id = f.usuario_id WHERE f.autorizacion_id = ? ORDER BY f.id", (a["id"],))]
        mov = con.execute("SELECT fecha, monto_cent, beneficiario, descripcion FROM movimientos_bancarios WHERE "
                          "autorizacion_id = ? ORDER BY id LIMIT 1", (a["id"],)).fetchone()
        ver = con.execute("SELECT * FROM verificaciones_desembolso WHERE autorizacion_id = ?", (a["id"],)).fetchone()
        filas.append({"autorizacion_id": int(a["id"]), "monto": de_centavos(int(a["monto_cent"])),
                      "descripcion": a["descripcion"], "estado": a["estado"],
                      "mancomunada": any(f["calidad"] == "CV" for f in firmas),
                      "firmantes": ", ".join(f"{f['nombre_completo']} ({f['calidad']})" for f in firmas),
                      "cofirmante_cv": next((f["nombre_completo"] for f in firmas if f["calidad"] == "CV"), None),
                      "pago": None if mov is None else {"fecha": mov["fecha"], "monto": de_centavos(int(mov["monto_cent"])),
                                                         "beneficiario": mov["beneficiario"]},
                      "verificado": None if ver is None else {"fecha": ver["fecha"], "por": ver["verificado_por"],
                                                               "conforme": bool(ver["conforme"])},
                      "puedo_verificar": ver is None and all(f["usuario_id"] != sesion.usuario_id for f in firmas)})
    return filas


def registrar_verificacion_desembolso(con: sqlite3.Connection, sesion: Sesion, autorizacion_id: int, fecha: Any,
                                      conforme: bool, observacion: str | None = None) -> int:
    """El CV verifica un desembolso; quien lo firmó (en particular el miembro
    del CV que cofirmó la firma mancomunada) no puede verificarlo."""
    _exigir_cv(con, sesion, "VERIFICAR")
    f = a_fecha(fecha)
    obs = _opcional(observacion, "observación")
    if not conforme and obs is None:
        raise DatosInvalidosError("Detalle la observación del desembolso no conforme.")
    with transaccion(con):
        a = con.execute("SELECT * FROM autorizaciones WHERE id = ? AND tipo_decision = 'DESEMBOLSO'",
                        (autorizacion_id,)).fetchone()
        if a is None:
            raise DatosInvalidosError(f"No existe la autorización de desembolso {autorizacion_id}.")
        if a["estado"] not in ("AUTORIZADA", "UTILIZADA"):
            raise ReglaNegocioError("Solo se verifican desembolsos autorizados.", codigo="ESTADO_INVALIDO")
        if con.execute("SELECT 1 FROM autorizacion_firmas WHERE autorizacion_id = ? AND usuario_id = ?",
                       (autorizacion_id, sesion.usuario_id)).fetchone():
            raise AutorizacionError("Segregación de funciones: el miembro del Consejo de Vigilancia que cofirmó el "
                                    "desembolso no puede verificarlo; debe hacerlo otro miembro.",
                                    codigo="COFIRMANTE_NO_VERIFICA")
        if con.execute("SELECT 1 FROM verificaciones_desembolso WHERE autorizacion_id = ?",
                       (autorizacion_id,)).fetchone():
            raise ReglaNegocioError("El desembolso ya fue verificado.", codigo="YA_REGISTRADO")
        vid = int(con.execute(
            """INSERT INTO verificaciones_desembolso (autorizacion_id, fecha, conforme, observacion, usuario_id,
                   verificado_por, registrado_en) VALUES (?,?,?,?,?,?,?)""",
            (autorizacion_id, f.isoformat(), int(bool(conforme)), obs, sesion.usuario_id, sesion.nombre_completo,
             _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "verificaciones_desembolso", "INSERT", vid,
                            despues={"autorizacion": autorizacion_id, "conforme": conforme})
    return vid


# ==========================================================================
# 7. PANEL DEL CONSEJO DE VIGILANCIA
# ==========================================================================
def panel_cv(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any = None) -> dict[str, Any]:
    """Cumplimiento de los controles del CV en el ejercicio frente a la
    frecuencia mínima fijada en ``parametros``. Pendiente = amarillo mientras
    corre el ejercicio; incumplido = rojo al terminar."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "VER")
    inicio, fin = cierre.rango_ejercicio(con, ejercicio)
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    p = _params(con, ejercicio)
    rango = (inicio.isoformat(), fin.isoformat())
    cuenta = lambda sql, args=rango: int(con.execute(sql, args).fetchone()[0])  # noqa: E731
    meses_cerrados = cuenta("SELECT COUNT(*) FROM periodos WHERE ejercicio = ? AND fecha_fin < ?",
                            (ejercicio, referencia.isoformat()))
    trimestres = sum(1 for t in range(1, 5) if presupuesto.trimestre_ejercicio(con, ejercicio, t)[1] < referencia)
    controles = [
        ("INF-CV-01", "Verificación física de activos", cuenta(
            "SELECT COUNT(*) FROM verificaciones_fisicas WHERE fecha BETWEEN ? AND ?"),
         1 if int(_param(p, "verificacion_fisica_meses")) <= 12 else 0, "Anual"),
        ("INF-CV-02", "Fiscalización de liquidez", cuenta(
            "SELECT COUNT(*) FROM informes_cv WHERE codigo = 'INF-CV-02' AND ejercicio = ?", (ejercicio,)),
         min(int(_param(p, "cv_inf_cv_02_anual")), trimestres) if referencia <= fin else int(
             _param(p, "cv_inf_cv_02_anual")), "Trimestral"),
        ("INF-CV-03", "Arqueos sorpresivos de caja", cuenta(
            "SELECT COUNT(*) FROM arqueos_caja WHERE fecha BETWEEN ? AND ?"), int(_param(p, "arqueos_por_anio")),
         "Al menos 2 por año"),
        ("INVENTARIO", "Inventario físico sorpresivo", cuenta(
            "SELECT COUNT(*) FROM inventarios_sorpresivos WHERE fecha BETWEEN ? AND ?"),
         int(_param(p, "cv_inventario_sorpresivo_anual")), "Al menos 1 por año"),
        ("INF-CV-04", "Informe previo de excedentes", cuenta(
            "SELECT COUNT(*) FROM informes_cv_04 i JOIN distribuciones_excedentes d ON d.id = i.distribucion_id WHERE "
            "d.ejercicio = ?", (ejercicio,)),
         cuenta("SELECT COUNT(*) FROM distribuciones_excedentes WHERE ejercicio = ?", (ejercicio,)) and 1,
         "Por cada propuesta de distribución"),
        ("INF-CV-05", "Provisiones laborales", cuenta(
            "SELECT COUNT(*) FROM informes_cv WHERE codigo = 'INF-CV-05' AND ejercicio = ?", (ejercicio,)),
         int(_param(p, "cv_inf_cv_05_anual")), "Anual"),
        ("INF-CV-06", "Revisión de precios", cuenta(
            "SELECT COUNT(*) FROM informes_cv WHERE codigo = 'INF-CV-06' AND ejercicio = ?", (ejercicio,)),
         int(_param(p, "cv_inf_cv_06_anual")), "Periódica (semestral)"),
        ("INF-CV-07", "Fiscalización mensual (CI-FIN-18)", cuenta(
            "SELECT COUNT(*) FROM informes_cv WHERE codigo = 'INF-CV-07' AND ejercicio = ?", (ejercicio,)),
         meses_cerrados, "Mensual, antes del FIN-INF-05"),
        ("INF-CV-08", "Verificación de los EEFF", cuenta(
            "SELECT COUNT(*) FROM informes_cv WHERE codigo = 'INF-CV-08' AND ejercicio = ?", (ejercicio,)),
         1 if referencia > fin else 0, "Anual, tras el cierre"),
        ("INF-CV-09", "Fiscalización tributaria RAU", cuenta(
            "SELECT COUNT(*) FROM revisiones_rau WHERE tipo = 'TRIMESTRAL' AND fecha BETWEEN ? AND ?"),
         min(int(_param(p, "cv_inf_cv_09_anual")), trimestres) if referencia <= fin else int(
             _param(p, "cv_inf_cv_09_anual")), "Trimestral"),
    ]
    filas = []
    for codigo, nombre, realizados, requeridos, frecuencia in controles:
        requeridos = int(requeridos or 0)
        if realizados >= requeridos:
            semaforo = VERDE
        else:
            semaforo = ROJO if referencia > fin or codigo == "INF-CV-07" else AMARILLO
        filas.append({"codigo": codigo, "nombre": nombre, "frecuencia": frecuencia, "realizados": realizados,
                      "requeridos": requeridos, "semaforo": semaforo})
    desembolsos = desembolsos_a_verificar(con, sesion, ejercicio)
    mancomunados = [d for d in desembolsos if d["mancomunada"]]
    verificados = [d for d in mancomunados if d["verificado"]]
    filas.append({"codigo": "DESEMBOLSOS", "nombre": "Desembolsos mancomunados verificados por otro miembro",
                  "frecuencia": "Por desembolso", "realizados": len(verificados), "requeridos": len(mancomunados),
                  "semaforo": VERDE if len(verificados) >= len(mancomunados) else AMARILLO})
    return {"ejercicio": ejercicio, "fecha": referencia.isoformat(), "controles": filas}
