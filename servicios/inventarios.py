"""Inventarios del ERP Pollolandia (Fase 4, Ciclo 2 — Egresos / Adquisiciones).

Implementa PC-07, CI-FIN-16 y el formato FIN-EGR-01:

1. Catálogo de insumos sobre las cuentas 1.1.03.01 a 1.1.03.15.
2. Sistema perpetuo con Costo Promedio Ponderado recalculado en cada entrada:
   CPP = (saldo anterior en Bs + entrada en Bs) / (unidades anteriores +
   unidades de la entrada). Las salidas y bajas se valúan al CPP vigente.
3. Salidas de consumo a los centros de costo (6.1.01.xx con su sufijo), con
   asiento automático; el traslado inmediato de los pollitos BB (cuenta
   transitoria 1.1.03.01) al sufijo 01.
4. Punto de reorden (CI-FIN-16 Arts. 5-7): PR = consumo diario promedio ×
   días de reposición + stock de seguridad (7 días alimento y pollitos BB; 5
   días veterinarios, limpieza y crianza), recalculado al inicio de cada ciclo
   con el consumo del ciclo anterior.
5. Monitoreo continuo (Art. 8) y acta de stock del viernes (FIN-EGR-01):
   físico vs contable vs PR, alertas y compra de emergencia autorizada por la
   Gerencia General (Arts. 10-11; suplencia CI-FIN-17).
6. Rotación máxima de granos (30 días) con alerta.
7. Bajas por deterioro, vencimiento o pérdida: acta de la GG y visto del CV
   cuando el monto supera el umbral (Bs 2.000).

Las cifras (días, umbrales, cuentas de gasto) se leen de ``parametros``.
Todo hecho económico se registra con ``servicios.contable.crear_asiento``.
Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import (
    a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion,
)
from servicios import auditoria, contable, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion
from utils.formato import a_decimal, a_fecha, fecha_pantalla, formato_bs, inicio_ejercicio, redondear_bs

# --------------------------------------------------------------------------
# Estructura (no cifras): cuentas y clasificaciones del Plan de Cuentas v3
# --------------------------------------------------------------------------
PREFIJO_INVENTARIO = "1.1.03."
CENTRO_CARNE = "6.1.01.01"
CENTRO_ALIMENTO = "6.1.01.02"
CERO = Decimal("0.00")
MIL = Decimal(1000)
CANTIDAD = Decimal("0.001")
TASA = Decimal("0.0001")

# Grupo crítico CI-FIN-16 -> parámetro con los días de stock de seguridad (Art. 6)
PARAM_DIAS_SEGURIDAD: dict[str, str] = {
    "ALIMENTO_BB": "stock_seguridad_dias_alimento_bb",
    "VETERINARIO_LIMPIEZA": "stock_seguridad_dias_otros",
}
NOMBRE_GRUPO: dict[str, str] = {
    "ALIMENTO_BB": "Pollitos BB y alimento balanceado",
    "VETERINARIO_LIMPIEZA": "Productos veterinarios, limpieza y crianza",
}
CAUSAS_BAJA: dict[str, str] = {
    "DETERIORO": "Deterioro (humedad, contaminación o daño físico)",
    "VENCIMIENTO": "Vencimiento",
    "PERDIDA": "Pérdida ordinaria de calidad o faltante",
}
ROLES_ACTA = ("UT", "JF")


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


def a_mil(cantidad: Any, campo: str = "cantidad", permitir_cero: bool = False) -> int:
    """Cantidad física a milésimas enteras (3 decimales, ROUND_HALF_UP)."""
    valor = a_decimal(cantidad, campo).quantize(CANTIDAD, rounding=ROUND_HALF_UP)
    if valor < 0 or (valor == 0 and not permitir_cero):
        raise DatosInvalidosError(f"La {campo} debe ser mayor a cero.")
    return int(valor * MIL)


def de_mil(milesimas: int | None) -> Decimal | None:
    """Milésimas enteras a cantidad ``Decimal`` con 3 decimales."""
    if milesimas is None:
        return None
    return (Decimal(int(milesimas)) / MIL).quantize(CANTIDAD)


def formato_cantidad(milesimas: int | None, unidad: str = "") -> str:
    """Cantidad con coma decimal y separador de miles: ``1.234,5 qq``."""
    if milesimas is None:
        return "—"
    valor = de_mil(milesimas).normalize()
    entero, _, decimales = f"{valor:f}".partition(".")
    signo = "-" if entero.startswith("-") else ""
    entero = entero.lstrip("-")
    grupos = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)
    texto = signo + ".".join(grupos) + ("," + decimales if decimales else "")
    return f"{texto} {unidad}".strip()


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _dia_semana(numero: int) -> str:
    return ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][numero - 1]


# ==========================================================================
# 0. CALENDARIO DE CICLOS
# ==========================================================================
def ciclo_de_fecha(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    """Ciclo productivo al que pertenece una fecha.

    El ejercicio empieza en ``mes_inicio_ejercicio`` y se divide en
    ``ciclos_por_ejercicio`` ciclos de ``dias_ciclo`` días; el día de inicio es
    el día 1 (criterio del POA: C1 1-jul a 11-sep-2026, C2 12-sep a 23-nov). El
    último ciclo absorbe el día 366 de los ejercicios bisiestos.
    """
    f = a_fecha(fecha)
    ejercicio = ejercicio_de_fecha_bd(con, f)
    p = obtener_parametros_vigentes(con, ejercicio)
    dias = int(_param(p, "dias_ciclo"))
    ciclos = int(_param(p, "ciclos_por_ejercicio"))
    inicio_ej = inicio_ejercicio(ejercicio, int(_param(p, "mes_inicio_ejercicio")))
    numero = min(ciclos, (f - inicio_ej).days // dias + 1)
    inicio = inicio_ej + timedelta(days=(numero - 1) * dias)
    if numero == ciclos:
        fin = inicio_ejercicio(ejercicio + 1, int(_param(p, "mes_inicio_ejercicio"))) - timedelta(days=1)
    else:
        fin = inicio + timedelta(days=dias - 1)
    return {"ejercicio": ejercicio, "ciclo": numero, "inicio": inicio, "fin": fin,
            "etiqueta": f"Ciclo {numero} del ejercicio {ejercicio}"}


# ==========================================================================
# 1. CATÁLOGO DE INSUMOS
# ==========================================================================
def obtener_insumo(con: sqlite3.Connection, insumo_id: int) -> sqlite3.Row:
    """Fila del insumo o ``DatosInvalidosError``."""
    if isinstance(insumo_id, bool) or not isinstance(insumo_id, int):
        raise DatosInvalidosError("Indique el insumo.")
    fila = con.execute("SELECT * FROM insumos WHERE id = ?", (insumo_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el insumo {insumo_id}.")
    return fila


def _validar_cuenta_inventario(con: sqlite3.Connection, cuenta: str) -> str:
    if not isinstance(cuenta, str) or not cuenta.startswith(PREFIJO_INVENTARIO):
        raise DatosInvalidosError(f"La cuenta {cuenta!r} no es una cuenta de inventario (1.1.03.xx).")
    fila = con.execute("SELECT es_movimiento FROM cuentas WHERE codigo = ?", (cuenta,)).fetchone()
    if fila is None or not fila[0]:
        raise DatosInvalidosError(f"La cuenta {cuenta} no existe o no es de movimiento en el Plan de Cuentas v3.")
    return cuenta


def crear_insumo(
    con: sqlite3.Connection, sesion: Sesion, codigo: str, nombre: str, cuenta_inventario: str, unidad: str, *,
    grupo_critico: str | None = None, es_grano: bool = False, controla_vencimiento: bool = False,
    transferencia_inmediata: bool = False, comprable: bool = True, dias_reposicion: int | None = None,
) -> int:
    """Da de alta un insumo sobre una cuenta 1.1.03.xx (Unidad de Finanzas)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "EDITAR")
    cod = _texto(codigo, "código", 30).upper()
    nom = _texto(nombre, "nombre", 120)
    cuenta = _validar_cuenta_inventario(con, cuenta_inventario)
    uni = _texto(unidad, "unidad", 20)
    if grupo_critico is not None and grupo_critico not in PARAM_DIAS_SEGURIDAD:
        raise DatosInvalidosError(f"Grupo crítico desconocido: {grupo_critico!r}.")
    if dias_reposicion is not None and (isinstance(dias_reposicion, bool) or not isinstance(dias_reposicion, int)
                                        or dias_reposicion <= 0):
        raise DatosInvalidosError("Los días de reposición deben ser un entero positivo.")
    if con.execute("SELECT 1 FROM insumos WHERE codigo = ?", (cod,)).fetchone():
        raise DatosInvalidosError(f"Ya existe un insumo con el código {cod}.", codigo="INSUMO_DUPLICADO")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO insumos (codigo, nombre, cuenta_inventario, unidad, grupo_critico, es_grano,
                   controla_vencimiento, transferencia_inmediata, comprable, dias_reposicion, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cod, nom, cuenta, uni, grupo_critico, int(bool(es_grano)), int(bool(controla_vencimiento)),
             int(bool(transferencia_inmediata)), int(bool(comprable)), dias_reposicion, sesion.nombre_usuario,
             _ahora_iso()))
        iid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "insumos", "INSERT", iid,
                            despues={"codigo": cod, "nombre": nom, "cuenta": cuenta, "unidad": uni,
                                     "grupo_critico": grupo_critico, "dias_reposicion": dias_reposicion})
    return iid


_EDITABLES = ("nombre", "unidad", "dias_reposicion", "activo", "grupo_critico", "es_grano", "controla_vencimiento")


def actualizar_insumo(con: sqlite3.Connection, sesion: Sesion, insumo_id: int, **cambios: Any) -> None:
    """Modifica datos operativos del insumo (p. ej. días de reposición del proveedor)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "EDITAR")
    actual = obtener_insumo(con, insumo_id)
    desconocidos = [k for k in cambios if k not in _EDITABLES]
    if desconocidos or not cambios:
        raise DatosInvalidosError(f"Campos no editables o vacíos: {', '.join(desconocidos) or 'ninguno'}.")
    nuevos: dict[str, Any] = {}
    for clave, valor in cambios.items():
        if clave in ("nombre", "unidad"):
            nuevos[clave] = _texto(valor, clave, 120)
        elif clave == "dias_reposicion":
            if valor is not None and (isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0):
                raise DatosInvalidosError("Los días de reposición deben ser un entero positivo.")
            nuevos[clave] = valor
        elif clave == "grupo_critico":
            if valor is not None and valor not in PARAM_DIAS_SEGURIDAD:
                raise DatosInvalidosError(f"Grupo crítico desconocido: {valor!r}.")
            nuevos[clave] = valor
        else:
            nuevos[clave] = int(bool(valor))
    with transaccion(con):
        sets = ", ".join(f"{k} = ?" for k in nuevos)
        con.execute(f"UPDATE insumos SET {sets}, actualizado_en = ? WHERE id = ?",
                    (*nuevos.values(), _ahora_iso(), insumo_id))
        auditoria.registrar(con, sesion, "insumos", "UPDATE", insumo_id,
                            antes={k: actual[k] for k in nuevos}, despues=nuevos)


def listar_insumos(con: sqlite3.Connection, sesion: Sesion, solo_activos: bool = True,
                   solo_comprables: bool = False) -> list[dict[str, Any]]:
    """Insumos con su cuenta contable."""
    if not seguridad.tiene_permiso(con, sesion.rol, "compras", "VER"):
        seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    sql =("SELECT i.*, c.nombre AS nombre_cuenta, c.uso_desde_2027 FROM insumos i "
           "JOIN cuentas c ON c.codigo = i.cuenta_inventario WHERE 1 = 1")
    if solo_activos:
        sql += " AND i.activo = 1"
    if solo_comprables:
        sql += " AND i.comprable = 1"
    return [dict(f) for f in con.execute(sql + " ORDER BY i.cuenta_inventario, i.codigo")]


# ==========================================================================
# 2. KÁRDEX PERPETUO Y COSTO PROMEDIO PONDERADO (PC-07 §V.B)
# ==========================================================================
@dataclass(frozen=True)
class Saldo:
    """Saldo perpetuo de un insumo."""

    cantidad_mil: int
    valor: Decimal
    cpp: Decimal
    ultima_fecha: str | None

    @property
    def cantidad(self) -> Decimal:
        return de_mil(self.cantidad_mil)


def saldo_insumo(con: sqlite3.Connection, insumo_id: int) -> Saldo:
    """Saldo del kárdex (último movimiento)."""
    fila = con.execute("SELECT * FROM kardex WHERE insumo_id = ? ORDER BY id DESC LIMIT 1", (insumo_id,)).fetchone()
    if fila is None:
        return Saldo(0, CERO, Decimal("0"), None)
    return Saldo(int(fila["saldo_cantidad_mil"]), de_centavos(int(fila["saldo_valor_cent"])), Decimal(fila["cpp"]),
                 fila["fecha"])


def calcular_cpp(valor: Decimal, cantidad_mil: int) -> Decimal:
    """CPP = valor / cantidad, con 4 decimales (informativo; los montos van a 2)."""
    if cantidad_mil <= 0:
        return Decimal("0")
    return (Decimal(valor) * MIL / Decimal(cantidad_mil)).quantize(TASA, rounding=ROUND_HALF_UP)


def valorar_salida(saldo: Saldo, cantidad_mil: int) -> Decimal:
    """Valor de una salida al CPP vigente. Si la salida agota el saldo, toma
    el valor remanente exacto (sin residuos de redondeo)."""
    if cantidad_mil > saldo.cantidad_mil:
        raise ReglaNegocioError("La salida supera la existencia del kárdex.", codigo="EXISTENCIA_INSUFICIENTE")
    if cantidad_mil == saldo.cantidad_mil:
        return saldo.valor
    return redondear_bs(saldo.valor * Decimal(cantidad_mil) / Decimal(saldo.cantidad_mil))


def _exigir_orden_cronologico(saldo: Saldo, fecha: date) -> None:
    if saldo.ultima_fecha and fecha.isoformat() < saldo.ultima_fecha:
        raise ReglaNegocioError(
            f"El kárdex perpetuo no admite movimientos anteriores al último registrado "
            f"({fecha_pantalla(saldo.ultima_fecha)}).", codigo="KARDEX_FECHA_ANTERIOR")


def _insertar_kardex(
    con: sqlite3.Connection, sesion: Sesion, insumo: sqlite3.Row, *, fecha: date, tipo: str, cantidad_mil: int,
    valor: Decimal, asiento_id: int, documento_ref: str | None = None, recepcion_id: int | None = None,
    baja_id: int | None = None, centro_costo: str | None = None, sufijo: str | None = None,
) -> int:
    saldo = saldo_insumo(con, int(insumo["id"]))
    _exigir_orden_cronologico(saldo, fecha)
    if tipo == "ENTRADA_COMPRA":
        nuevo_mil = saldo.cantidad_mil + cantidad_mil
        nuevo_valor = saldo.valor + valor
    else:
        if cantidad_mil > saldo.cantidad_mil:
            raise ReglaNegocioError(
                f"Existencia insuficiente de {insumo['nombre']}: hay "
                f"{formato_cantidad(saldo.cantidad_mil, insumo['unidad'])}.", codigo="EXISTENCIA_INSUFICIENTE")
        nuevo_mil = saldo.cantidad_mil - cantidad_mil
        nuevo_valor = saldo.valor - valor
    if nuevo_mil == 0:
        nuevo_valor = CERO
    cpp = calcular_cpp(nuevo_valor, nuevo_mil) if nuevo_mil else (saldo.cpp if tipo != "ENTRADA_COMPRA" else
                                                                  calcular_cpp(valor, cantidad_mil))
    unitario = calcular_cpp(valor, cantidad_mil)
    cur = con.execute(
        """INSERT INTO kardex (insumo_id, fecha, tipo, cantidad_mil, valor_cent, costo_unitario, saldo_cantidad_mil,
               saldo_valor_cent, cpp, documento_ref, recepcion_id, baja_id, centro_costo, sufijo, asiento_id,
               registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (int(insumo["id"]), fecha.isoformat(), tipo, cantidad_mil, a_centavos(valor), str(unitario), nuevo_mil,
         a_centavos(nuevo_valor), str(cpp), documento_ref, recepcion_id, baja_id, centro_costo, sufijo, asiento_id,
         sesion.nombre_usuario, _ahora_iso()))
    kid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "kardex", "INSERT", kid,
                        antes={"saldo_cantidad": de_mil(saldo.cantidad_mil), "saldo_valor": saldo.valor,
                               "cpp": saldo.cpp},
                        despues={"insumo": insumo["codigo"], "tipo": tipo, "fecha": fecha.isoformat(),
                                 "cantidad": de_mil(cantidad_mil), "valor": valor,
                                 "saldo_cantidad": de_mil(nuevo_mil), "saldo_valor": nuevo_valor, "cpp": cpp,
                                 "asiento_id": asiento_id})
    return kid


def _sufijo_por_defecto(con: sqlite3.Connection, cuenta: str, centro: str) -> str:
    filas = con.execute(
        """SELECT s.sufijo FROM sufijos_costo s JOIN sufijo_centro sc ON sc.sufijo = s.sufijo
            WHERE sc.centro_costo = ? AND (s.cuenta_contrapartida = ? OR s.cuenta_contrapartida_2 = ?)
            ORDER BY s.sufijo""", (centro, cuenta, cuenta)).fetchall()
    if len(filas) != 1:
        raise DatosInvalidosError(
            f"Indique el sufijo del costo: la cuenta {cuenta} tiene {len(filas)} sufijos posibles en {centro}.",
            codigo="SUFIJO_REQUERIDO")
    return str(filas[0][0])


def registrar_entrada_compra(
    con: sqlite3.Connection, sesion: Sesion, insumo_id: int, fecha: Any, cantidad: Any, valor: Any, asiento_id: int,
    documento_ref: str, recepcion_id: int | None = None,
) -> int:
    """Entrada al kárdex por compra (la usa ``compras`` al contabilizar la
    recepción; el asiento inventario contra cuenta por pagar ya existe).

    Recalcula el CPP. Si el insumo es de traslado inmediato (pollitos BB,
    cuenta transitoria 1.1.03.01), lo transfiere el mismo día al centro de
    costo con el sufijo 01. Cierra las alertas de stock que la entrada repone y
    alerta si un grano supera los días máximos de stock.

    Returns:
        id del movimiento de entrada.
    """
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    insumo = obtener_insumo(con, insumo_id)
    f = a_fecha(fecha)
    cant = a_mil(cantidad)
    importe = redondear_bs(valor)
    if importe <= 0:
        raise DatosInvalidosError("El valor de la entrada debe ser mayor a cero.")
    with transaccion(con):
        debitado = con.execute(
            "SELECT COALESCE(SUM(debe_cent), 0) FROM asiento_detalle WHERE asiento_id = ? AND cuenta_codigo = ?",
            (asiento_id, insumo["cuenta_inventario"])).fetchone()[0]
        if int(debitado) < a_centavos(importe):
            raise ReglaNegocioError(f"El asiento {asiento_id} no debita {formato_bs(importe)} a la cuenta "
                                    f"{insumo['cuenta_inventario']}.", codigo="ASIENTO_NO_CORRESPONDE")
        kid = _insertar_kardex(con, sesion, insumo, fecha=f, tipo="ENTRADA_COMPRA", cantidad_mil=cant, valor=importe,
                               asiento_id=asiento_id, documento_ref=documento_ref, recepcion_id=recepcion_id)
        if insumo["transferencia_inmediata"]:
            _salida(con, sesion, insumo, f, cant, CENTRO_CARNE, None, documento_ref,
                    f"Traslado inmediato de {insumo['nombre']} al centro de costo (cuenta transitoria, PC-03)",
                    monitorear=False)
        _cerrar_alertas_repuestas(con, sesion, insumo, f)
        if insumo["es_grano"]:
            _alertar_rotacion(con, sesion, insumo, f, "MONITOREO_CONTINUO")
    return kid


def _salida(
    con: sqlite3.Connection, sesion: Sesion, insumo: sqlite3.Row, fecha: date, cantidad_mil: int, centro: str,
    sufijo: str | None, documento_ref: str | None, glosa: str, monitorear: bool = True,
) -> dict[str, Any]:
    saldo = saldo_insumo(con, int(insumo["id"]))
    _exigir_orden_cronologico(saldo, fecha)
    if cantidad_mil > saldo.cantidad_mil:
        raise ReglaNegocioError(
            f"Existencia insuficiente de {insumo['nombre']}: hay {formato_cantidad(saldo.cantidad_mil, insumo['unidad'])}"
            f" y se pide {formato_cantidad(cantidad_mil, insumo['unidad'])}.", codigo="EXISTENCIA_INSUFICIENTE")
    suf = sufijo or _sufijo_por_defecto(con, insumo["cuenta_inventario"], centro)
    valor = valorar_salida(saldo, cantidad_mil)
    if valor <= 0:
        raise ReglaNegocioError("La salida no tiene valor contable (saldo sin costo).", codigo="SALIDA_SIN_VALOR")
    asiento = contable.crear_asiento(
        con, sesion, fecha, glosa,
        [{"cuenta": centro, "debe": valor, "centro_costo": centro, "sufijo": suf},
         {"cuenta": insumo["cuenta_inventario"], "haber": valor}],
        "INVENTARIOS", documento_ref, tipo="TRASPASO")
    kid = _insertar_kardex(con, sesion, insumo, fecha=fecha, tipo="SALIDA_CONSUMO", cantidad_mil=cantidad_mil,
                           valor=valor, asiento_id=asiento.id, documento_ref=documento_ref, centro_costo=centro,
                           sufijo=suf)
    alerta = verificar_punto_reorden(con, sesion, int(insumo["id"]), fecha, "MONITOREO_CONTINUO") \
        if monitorear and insumo["grupo_critico"] else None
    return {"kardex_id": kid, "asiento_id": asiento.id, "valor": valor, "alerta_id": alerta,
            "cpp": saldo.cpp, "saldo": saldo_insumo(con, int(insumo["id"]))}


def registrar_salida(
    con: sqlite3.Connection, sesion: Sesion, insumo_id: int, fecha: Any, cantidad: Any, *,
    centro_costo: str = CENTRO_CARNE, sufijo: str | None = None, documento_ref: str | None = None,
    glosa: str | None = None,
) -> dict[str, Any]:
    """Salida de consumo al centro de costo, valuada al CPP (PC-07 §V.B).

    En la Fase 7 (costos) el consumo diario del ciclo usará esta función. Tras
    la salida se monitorea el punto de reorden (CI-FIN-16 Art. 8): si la
    existencia queda bajo el PR se emite la alerta de compra de emergencia.

    Returns:
        ``{kardex_id, asiento_id, valor, alerta_id, cpp, saldo}``.
    """
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    insumo = obtener_insumo(con, insumo_id)
    if not insumo["comprable"]:
        raise ReglaNegocioError("Los productos terminados y subproductos salen por venta (Fase 8), no por consumo.",
                                codigo="SALIDA_NO_PERMITIDA")
    if centro_costo not in (CENTRO_CARNE, CENTRO_ALIMENTO):
        raise DatosInvalidosError("El centro de costo debe ser 6.1.01.01 o 6.1.01.02.")
    f = a_fecha(fecha)
    cant = a_mil(cantidad)
    texto = _opcional(glosa, "glosa", 300) or f"Consumo de {insumo['nombre']} ({formato_cantidad(cant, insumo['unidad'])})"
    with transaccion(con):
        return _salida(con, sesion, insumo, f, cant, centro_costo, sufijo, _opcional(documento_ref, "documento", 60),
                       texto)


def kardex(con: sqlite3.Connection, sesion: Sesion, insumo_id: int, desde: Any = None,
           hasta: Any = None) -> list[dict[str, Any]]:
    """Movimientos del kárdex de un insumo, con cantidades y montos decodificados."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    obtener_insumo(con, insumo_id)
    sql, args = "SELECT * FROM kardex WHERE insumo_id = ?", [insumo_id]
    if desde is not None:
        sql += " AND fecha >= ?"
        args.append(a_fecha(desde).isoformat())
    if hasta is not None:
        sql += " AND fecha <= ?"
        args.append(a_fecha(hasta).isoformat())
    filas = []
    for f in con.execute(sql + " ORDER BY id", args):
        d = dict(f)
        entrada = f["tipo"] == "ENTRADA_COMPRA"
        d.update(cantidad=de_mil(f["cantidad_mil"]), valor=de_centavos(int(f["valor_cent"])),
                 saldo_cantidad=de_mil(f["saldo_cantidad_mil"]), saldo_valor=de_centavos(int(f["saldo_valor_cent"])),
                 entrada_cantidad=de_mil(f["cantidad_mil"]) if entrada else None,
                 salida_cantidad=None if entrada else de_mil(f["cantidad_mil"]),
                 entrada_valor=de_centavos(int(f["valor_cent"])) if entrada else None,
                 salida_valor=None if entrada else de_centavos(int(f["valor_cent"])))
        filas.append(d)
    return filas


def existencias(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Existencias perpetuas por insumo con CPP, PR vigente y estado frente al PR."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    filas = []
    for ins in con.execute("SELECT * FROM insumos WHERE activo = 1 ORDER BY cuenta_inventario, codigo"):
        saldo = saldo_insumo(con, int(ins["id"]))
        pr = punto_reorden_vigente(con, int(ins["id"]), f)
        existencia = existencia_para_reorden(con, ins, f) if ins["grupo_critico"] else saldo.cantidad_mil
        filas.append({
            "insumo_id": int(ins["id"]), "codigo": ins["codigo"], "nombre": ins["nombre"],
            "cuenta": ins["cuenta_inventario"], "unidad": ins["unidad"], "grupo_critico": ins["grupo_critico"],
            "cantidad_mil": saldo.cantidad_mil, "cantidad": saldo.cantidad, "valor": saldo.valor, "cpp": saldo.cpp,
            "existencia_reorden_mil": existencia,
            "punto_reorden_mil": int(pr["punto_reorden_mil"]) if pr else None,
            "bajo_pr": bool(pr) and existencia < int(pr["punto_reorden_mil"]),
        })
    return filas


def conciliar_con_contabilidad(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Compara, por cuenta 1.1.03.xx, el valor del kárdex con el saldo contable
    (control del riesgo 16: registro que no coincide)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    resultado = []
    for cuenta in con.execute("SELECT codigo, nombre FROM cuentas WHERE codigo LIKE '1.1.03.%' AND nivel = 4 "
                              "ORDER BY codigo"):
        valor_kardex = sum((saldo_insumo(con, int(i[0])).valor for i in con.execute(
            "SELECT id FROM insumos WHERE cuenta_inventario = ?", (cuenta[0],))), CERO)
        contable_cent = con.execute(
            "SELECT COALESCE(SUM(debe_cent), 0) - COALESCE(SUM(haber_cent), 0) FROM asiento_detalle "
            "WHERE cuenta_codigo = ?", (cuenta[0],)).fetchone()[0]
        saldo_contable = de_centavos(int(contable_cent))
        resultado.append({"cuenta": cuenta[0], "nombre": cuenta[1], "kardex": valor_kardex,
                          "contable": saldo_contable, "diferencia": saldo_contable - valor_kardex})
    return resultado


# ==========================================================================
# 3. PUNTO DE REORDEN (CI-FIN-16 Arts. 5-7)
# ==========================================================================
def punto_reorden_vigente(con: sqlite3.Connection, insumo_id: int, fecha: Any = None) -> sqlite3.Row | None:
    """Último PR calculado con vigencia hasta la fecha."""
    f = a_fecha(fecha) if fecha is not None else date.today()
    return con.execute(
        "SELECT * FROM puntos_reorden WHERE insumo_id = ? AND vigente_desde <= ? ORDER BY vigente_desde DESC, id DESC "
        "LIMIT 1", (insumo_id, f.isoformat())).fetchone()


def consumo_en_periodo(con: sqlite3.Connection, insumo_id: int, desde: Any, hasta: Any) -> int:
    """Milésimas consumidas (salidas de consumo) entre dos fechas inclusive."""
    fila = con.execute(
        "SELECT COALESCE(SUM(cantidad_mil), 0) FROM kardex WHERE insumo_id = ? AND tipo = 'SALIDA_CONSUMO' "
        "AND fecha BETWEEN ? AND ?", (insumo_id, a_fecha(desde).isoformat(), a_fecha(hasta).isoformat())).fetchone()
    return int(fila[0])


def calcular_punto_reorden(
    con: sqlite3.Connection, insumo_id: int, fecha: Any, consumo_diario_proyectado: Any = None,
) -> dict[str, Any]:
    """Calcula el PR sin registrarlo.

    * Base: el consumo del ciclo inmediatamente anterior (bitácora/salidas,
      Art. 7) dividido entre los días de ese ciclo. Si no hay consumo previo
      (primer cálculo, Disposición Transitoria) se usa el consumo diario
      proyectado que se indique.
    * Stock de seguridad = consumo diario × días del grupo (Art. 6).
    * PR = consumo diario × días de reposición del proveedor + stock de seguridad.

    Raises:
        ReglaNegocioError: insumo no crítico, sin días de reposición o sin base.
    """
    insumo = obtener_insumo(con, insumo_id)
    if not insumo["grupo_critico"]:
        raise ReglaNegocioError(f"{insumo['nombre']} no es un insumo crítico de CI-FIN-16.",
                                codigo="INSUMO_NO_CRITICO")
    if not insumo["dias_reposicion"]:
        raise ReglaNegocioError(
            f"Registre el tiempo de reposición del proveedor de {insumo['nombre']} (días entre la orden de compra y "
            "la recepción, CI-FIN-16 Art. 4 iii) antes de calcular el punto de reorden.",
            codigo="FALTA_DIAS_REPOSICION")
    f = a_fecha(fecha)
    ciclo = ciclo_de_fecha(con, f)
    try:
        anterior = ciclo_de_fecha(con, ciclo["inicio"] - timedelta(days=1))
    except ReglaNegocioError as exc:
        if exc.codigo != "EJERCICIO_NO_EXISTE":
            raise
        anterior = {"inicio": ciclo["inicio"] - timedelta(days=(ciclo["fin"] - ciclo["inicio"]).days + 1),
                    "fin": ciclo["inicio"] - timedelta(days=1)}   # antes del primer ejercicio: sin consumo
    dias_base = (anterior["fin"] - anterior["inicio"]).days + 1
    consumo_mil = consumo_en_periodo(con, insumo_id, anterior["inicio"], anterior["fin"])
    if consumo_mil > 0:
        fuente = "BITACORA"
        diario = (Decimal(consumo_mil) / MIL / Decimal(dias_base)).quantize(TASA, rounding=ROUND_HALF_UP)
    elif consumo_diario_proyectado is not None:
        fuente = "PROYECTADO"
        diario = a_decimal(consumo_diario_proyectado, "consumo diario proyectado").quantize(TASA, rounding=ROUND_HALF_UP)
        if diario <= 0:
            raise DatosInvalidosError("El consumo diario proyectado debe ser mayor a cero.")
    else:
        raise ReglaNegocioError(
            f"No hay consumo de {insumo['nombre']} en el ciclo anterior ({fecha_pantalla(anterior['inicio'])} a "
            f"{fecha_pantalla(anterior['fin'])}): indique el consumo diario proyectado (Disposición Transitoria).",
            codigo="PR_SIN_BASE")
    p = obtener_parametros_vigentes(con, ciclo["ejercicio"])
    dias_seg = int(_param(p, PARAM_DIAS_SEGURIDAD[insumo["grupo_critico"]]))
    dias_rep = int(insumo["dias_reposicion"])
    stock_seg = (diario * dias_seg).quantize(CANTIDAD, rounding=ROUND_HALF_UP)
    pr = (diario * dias_rep).quantize(CANTIDAD, rounding=ROUND_HALF_UP) + stock_seg
    return {"insumo_id": insumo_id, "insumo": insumo["nombre"], "unidad": insumo["unidad"],
            "ejercicio": ciclo["ejercicio"], "ciclo": ciclo["ciclo"], "fuente": fuente,
            "base_desde": anterior["inicio"].isoformat() if fuente == "BITACORA" else None,
            "base_hasta": anterior["fin"].isoformat() if fuente == "BITACORA" else None,
            "consumo_total_mil": consumo_mil if fuente == "BITACORA" else None, "consumo_diario": diario,
            "dias_reposicion": dias_rep, "dias_seguridad": dias_seg, "stock_seguridad": stock_seg,
            "stock_seguridad_mil": int(stock_seg * MIL), "punto_reorden": pr, "punto_reorden_mil": int(pr * MIL)}


def recalcular_punto_reorden(
    con: sqlite3.Connection, sesion: Sesion, insumo_id: int, fecha: Any, consumo_diario_proyectado: Any = None,
) -> int:
    """Registra el PR del ciclo (la Unidad de Finanzas lo recalcula al inicio
    de cada ciclo, CI-FIN-16 Art. 7). Queda en el historial inmutable."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "EDITAR")
    calculo = calcular_punto_reorden(con, insumo_id, fecha, consumo_diario_proyectado)
    f = a_fecha(fecha)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO puntos_reorden (insumo_id, vigente_desde, ejercicio, ciclo, fuente, base_desde, base_hasta,
                   consumo_total_mil, consumo_diario, dias_reposicion, dias_seguridad, stock_seguridad_mil,
                   punto_reorden_mil, calculado_por, calculado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (insumo_id, f.isoformat(), calculo["ejercicio"], calculo["ciclo"], calculo["fuente"],
             calculo["base_desde"], calculo["base_hasta"], calculo["consumo_total_mil"], str(calculo["consumo_diario"]),
             calculo["dias_reposicion"], calculo["dias_seguridad"], calculo["stock_seguridad_mil"],
             calculo["punto_reorden_mil"], sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "puntos_reorden", "INSERT", pid,
                            despues={k: v for k, v in calculo.items() if k != "insumo"})
    return pid


def recalcular_puntos_ciclo(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, list[dict[str, Any]]]:
    """Recalcula el PR de todos los insumos críticos con el consumo del ciclo
    anterior. Los que no tienen base o días de reposición quedan informados."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "EDITAR")
    hechos, omitidos = [], []
    for ins in criticos_vigentes(con, fecha):
        try:
            pid = recalcular_punto_reorden(con, sesion, int(ins["id"]), fecha)
            hechos.append({"insumo": ins["nombre"], "punto_reorden_id": pid})
        except ReglaNegocioError as exc:
            omitidos.append({"insumo": ins["nombre"], "motivo": exc.mensaje, "codigo": exc.codigo})
    return {"recalculados": hechos, "omitidos": omitidos}


def historial_puntos_reorden(con: sqlite3.Connection, sesion: Sesion, insumo_id: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    sql = ("SELECT p.*, i.nombre AS insumo, i.unidad FROM puntos_reorden p JOIN insumos i ON i.id = p.insumo_id")
    args: tuple[Any, ...] = ()
    if insumo_id is not None:
        sql, args = sql + " WHERE p.insumo_id = ?", (insumo_id,)
    return [dict(f) for f in con.execute(sql + " ORDER BY p.vigente_desde DESC, p.id DESC", args)]


def existencia_para_reorden(con: sqlite3.Connection, insumo: sqlite3.Row, fecha: date) -> int:
    """Existencia que se compara con el PR.

    # DISCREPANCIA: CI-FIN-16 aplica la fórmula del PR también a los pollitos
    # BB, pero la Descripción de Cuentas (1.1.03.01) y PC-03 hacen de esa
    # cuenta una cuenta transitoria con saldo cero (traslado inmediato al
    # centro 6.1.01.01). Para el "próximo lote" (fila del FIN-EGR-01) la
    # existencia equivalente es: pollitos asegurados en órdenes emitidas aún
    # no recibidas + días que faltan para cerrar el ciclo en curso × consumo
    # diario del PR. Así la alerta salta cuando faltan menos días que los de
    # reposición más el stock de seguridad sin el próximo lote asegurado.
    """
    saldo = saldo_insumo(con, int(insumo["id"]))
    if not insumo["transferencia_inmediata"]:
        return saldo.cantidad_mil
    asegurados = con.execute(
        """SELECT COALESCE(SUM(l.cantidad_mil - l.cantidad_recibida_mil), 0) FROM oc_lineas l
             JOIN ordenes_compra o ON o.id = l.orden_id
            WHERE l.insumo_id = ? AND o.estado IN ('EMITIDA','RECIBIDA_PARCIAL')""", (int(insumo["id"]),)).fetchone()[0]
    pr = punto_reorden_vigente(con, int(insumo["id"]), fecha)
    if pr is None:
        return saldo.cantidad_mil + int(asegurados)
    ciclo = ciclo_de_fecha(con, fecha)
    dias_restantes = (ciclo["fin"] - fecha).days
    cobertura = (Decimal(pr["consumo_diario"]) * dias_restantes).quantize(CANTIDAD, rounding=ROUND_HALF_UP)
    return saldo.cantidad_mil + int(asegurados) + int(cobertura * MIL)


def criticos_vigentes(con: sqlite3.Connection, fecha: Any) -> list[sqlite3.Row]:
    """Insumos críticos activos cuya cuenta está habilitada en el ejercicio
    (las materias primas (*) del alimento propio rigen desde 2027)."""
    f = a_fecha(fecha)
    ejercicio = ejercicio_de_fecha_bd(con, f)
    inicio = int(_param(obtener_parametros_vigentes(con, ejercicio), "ejercicio_inicio_alimento_propio"))
    return con.execute(
        """SELECT i.* FROM insumos i JOIN cuentas c ON c.codigo = i.cuenta_inventario
            WHERE i.activo = 1 AND i.grupo_critico IS NOT NULL AND (c.uso_desde_2027 = 0 OR ? >= ?)
            ORDER BY i.cuenta_inventario, i.codigo""", (ejercicio, inicio)).fetchall()


# ==========================================================================
# 4. ALERTAS, MONITOREO CONTINUO Y COMPRA DE EMERGENCIA (CI-FIN-16 Arts. 8-11)
# ==========================================================================
def _alerta_abierta(con: sqlite3.Connection, insumo_id: int, tipo: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM alertas_stock WHERE insumo_id = ? AND tipo = ? AND estado IN "
                       "('ABIERTA','EMERGENCIA_AUTORIZADA')", (insumo_id, tipo)).fetchone()


def _insertar_alerta(
    con: sqlite3.Connection, sesion: Sesion, insumo: sqlite3.Row, fecha: date, tipo: str, origen: str,
    existencia_mil: int, referencia_mil: int | None, detalle: str, dias: int | None = None, acta_id: int | None = None,
) -> int:
    previa = _alerta_abierta(con, int(insumo["id"]), tipo)
    if previa is not None:
        return int(previa["id"])
    cur = con.execute(
        """INSERT INTO alertas_stock (insumo_id, fecha, tipo, origen, existencia_mil, referencia_mil, dias, acta_id,
               detalle, estado, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (int(insumo["id"]), fecha.isoformat(), tipo, origen, existencia_mil, referencia_mil, dias, acta_id, detalle,
         "ABIERTA", sesion.nombre_usuario, _ahora_iso()))
    aid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "alertas_stock", "INSERT", aid,
                        despues={"insumo": insumo["codigo"], "tipo": tipo, "origen": origen,
                                 "existencia": de_mil(existencia_mil), "referencia": de_mil(referencia_mil)},
                        detalle=detalle)
    return aid


def verificar_punto_reorden(con: sqlite3.Connection, sesion: Sesion, insumo_id: int, fecha: Any, origen: str,
                            acta_id: int | None = None, existencia_mil: int | None = None) -> int | None:
    """Compara la existencia con el PR vigente; si queda por debajo emite (o
    reutiliza) la alerta escrita a Finanzas (CI-FIN-16 Art. 8).

    Returns:
        id de la alerta abierta o ``None`` si la existencia cubre el PR.
    """
    insumo = obtener_insumo(con, insumo_id)
    f = a_fecha(fecha)
    pr = punto_reorden_vigente(con, insumo_id, f)
    if pr is None:
        return None
    existencia = existencia_para_reorden(con, insumo, f) if existencia_mil is None else existencia_mil
    punto = int(pr["punto_reorden_mil"])
    if existencia >= punto:
        return None
    detalle = (f"{insumo['nombre']}: existencia {formato_cantidad(existencia, insumo['unidad'])} bajo el punto de "
               f"reorden {formato_cantidad(punto, insumo['unidad'])} (CI-FIN-16 Art. 8). Activar compra de emergencia "
               "sin esperar el lunes (Art. 10).")
    with transaccion(con):
        return _insertar_alerta(con, sesion, insumo, f, "BAJO_PUNTO_REORDEN", origen, existencia, punto, detalle,
                                acta_id=acta_id)


def monitorear_stock(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> list[int]:
    """Monitoreo de cualquier día (no solo el viernes): revisa todos los
    insumos críticos frente al PR y los granos frente a la rotación máxima."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    f = a_fecha(fecha)
    alertas: list[int] = []
    for ins in con.execute("SELECT * FROM insumos WHERE activo = 1 AND (grupo_critico IS NOT NULL OR es_grano = 1)"):
        if ins["grupo_critico"]:
            aid = verificar_punto_reorden(con, sesion, int(ins["id"]), f, "MONITOREO_CONTINUO")
            if aid:
                alertas.append(aid)
        if ins["es_grano"]:
            with transaccion(con):
                aid = _alertar_rotacion(con, sesion, ins, f, "REVISION_ROTACION")
            if aid:
                alertas.append(aid)
    return alertas


def _cerrar_alertas_repuestas(con: sqlite3.Connection, sesion: Sesion, insumo: sqlite3.Row, fecha: date) -> None:
    alerta = _alerta_abierta(con, int(insumo["id"]), "BAJO_PUNTO_REORDEN")
    if alerta is None:
        return
    pr = punto_reorden_vigente(con, int(insumo["id"]), fecha)
    existencia = existencia_para_reorden(con, insumo, fecha)
    if pr is not None and existencia < int(pr["punto_reorden_mil"]):
        return
    detalle = f"Existencia repuesta: {formato_cantidad(existencia, insumo['unidad'])} el {fecha_pantalla(fecha)}."
    con.execute("UPDATE alertas_stock SET estado = 'ATENDIDA', cerrada_en = ?, cierre_detalle = ? WHERE id = ?",
                (_ahora_iso(), detalle, int(alerta["id"])))
    auditoria.registrar(con, sesion, "alertas_stock", "UPDATE", int(alerta["id"]), antes={"estado": alerta["estado"]},
                        despues={"estado": "ATENDIDA"}, detalle=detalle)


def cerrar_alerta(con: sqlite3.Connection, sesion: Sesion, alerta_id: int, detalle: str) -> None:
    """Cierra una alerta abierta con la explicación de Finanzas (p. ej. rotación regularizada)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "EDITAR")
    texto = _texto(detalle, "detalle del cierre")
    with transaccion(con):
        fila = con.execute("SELECT * FROM alertas_stock WHERE id = ?", (alerta_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la alerta {alerta_id}.")
        if fila["estado"] != "ABIERTA":
            raise ReglaNegocioError(f"La alerta está {fila['estado']}.", codigo="ALERTA_NO_ABIERTA")
        con.execute("UPDATE alertas_stock SET estado = 'CERRADA', cerrada_en = ?, cierre_detalle = ? WHERE id = ?",
                    (_ahora_iso(), texto, alerta_id))
        auditoria.registrar(con, sesion, "alertas_stock", "UPDATE", alerta_id, antes={"estado": "ABIERTA"},
                            despues={"estado": "CERRADA"}, detalle=texto)


def listar_alertas_stock(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    sql = ("SELECT a.*, i.nombre AS insumo, i.unidad, e.id AS emergencia_id, e.estado AS estado_emergencia "
           "FROM alertas_stock a JOIN insumos i ON i.id = a.insumo_id LEFT JOIN compras_emergencia e "
           "ON e.alerta_id = a.id")
    args: tuple[Any, ...] = ()
    if estado:
        sql, args = sql + " WHERE a.estado = ?", (estado,)
    return [dict(f) for f in con.execute(sql + " ORDER BY a.id DESC", args)]


def autorizar_compra_emergencia(
    con: sqlite3.Connection, sesion: Sesion, alerta_id: int, fecha: Any, constancia_ref: str, justificacion: str,
) -> int:
    """La Gerencia General (o su suplente CI-FIN-17) autoriza de forma
    excepcional e inmediata la compra de emergencia, con constancia del
    déficit en el FIN-EGR-01 o registro extraordinario (CI-FIN-16 Art. 11).

    Raises:
        AutorizacionError: quien autoriza no es la GG ni su suplente.
        ReglaNegocioError: la alerta no es de punto de reorden o no está abierta.
    """
    f = a_fecha(fecha)
    if sesion.rol == "GG":
        seguridad.exigir_permiso(con, sesion, "compras", "AUTORIZAR")
    elif seguridad.orden_suplencia(con, sesion, f) is None:
        seguridad.exigir_permiso(con, sesion, "compras", "AUTORIZAR")
        raise AutorizacionError("Solo la Gerencia General o su suplente (CI-FIN-17) autoriza la compra de emergencia.",
                                codigo="SIN_PERMISO")
    constancia = _texto(constancia_ref, "constancia (FIN-EGR-01 o registro extraordinario)", 120)
    motivo = _texto(justificacion, "justificación", 500)
    with transaccion(con):
        alerta = con.execute("SELECT * FROM alertas_stock WHERE id = ?", (alerta_id,)).fetchone()
        if alerta is None:
            raise DatosInvalidosError(f"No existe la alerta {alerta_id}.")
        if alerta["tipo"] != "BAJO_PUNTO_REORDEN":
            raise ReglaNegocioError("La compra de emergencia solo procede ante un déficit frente al punto de reorden.",
                                    codigo="ALERTA_NO_REORDEN")
        if alerta["estado"] != "ABIERTA":
            raise ReglaNegocioError(f"La alerta está {alerta['estado']}.", codigo="ALERTA_NO_ABIERTA")
        cur = con.execute(
            """INSERT INTO compras_emergencia (alerta_id, fecha, autorizado_por_id, rol_autorizador, constancia_ref,
                   justificacion, estado, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (alerta_id, f.isoformat(), sesion.usuario_id, sesion.rol, constancia, motivo, "AUTORIZADA", _ahora_iso()))
        eid = int(cur.lastrowid)
        con.execute("UPDATE alertas_stock SET estado = 'EMERGENCIA_AUTORIZADA' WHERE id = ?", (alerta_id,))
        auditoria.registrar(con, sesion, "compras_emergencia", "AUTORIZACION", eid,
                            antes={"alerta_estado": "ABIERTA"},
                            despues={"alerta_id": alerta_id, "estado": "AUTORIZADA", "constancia": constancia,
                                     "suplencia": sesion.rol != "GG"}, detalle=motivo)
    return eid


def emergencias_disponibles(con: sqlite3.Connection, insumo_id: int | None = None) -> list[dict[str, Any]]:
    """Compras de emergencia autorizadas aún no utilizadas en una orden."""
    sql = ("SELECT e.*, a.insumo_id, i.nombre AS insumo FROM compras_emergencia e JOIN alertas_stock a "
           "ON a.id = e.alerta_id JOIN insumos i ON i.id = a.insumo_id WHERE e.estado = 'AUTORIZADA'")
    args: tuple[Any, ...] = ()
    if insumo_id is not None:
        sql, args = sql + " AND a.insumo_id = ?", (insumo_id,)
    return [dict(f) for f in con.execute(sql + " ORDER BY e.id", args)]


# ==========================================================================
# 5. ACTA DE CONTROL DE STOCK SEMANAL (FIN-EGR-01)
# ==========================================================================
def registrar_acta_stock(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, conteos: Mapping[int, Any], observaciones: str | None = None,
) -> int:
    """Control de stock físico concurrente del viernes (PR-FIN-03): coteja el
    conteo físico con el saldo contable y con el PR vigente.

    ``conteos`` = ``{insumo_id: existencia física}`` para cada insumo crítico
    activo. Para los pollitos BB (próximo lote) la existencia es la
    equivalente de :func:`existencia_para_reorden` y no se cuenta.

    Raises:
        ReglaNegocioError: la fecha no es el día de control (viernes) o ya hay acta.
        DatosInvalidosError: falta el conteo de algún insumo crítico.
    """
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    if sesion.rol not in ROLES_ACTA:
        raise AutorizacionError("El acta de stock la elaboran la Unidad Técnica y la Unidad de Finanzas.",
                                codigo="SIN_PERMISO")
    f = a_fecha(fecha)
    dia = int(_param(_params(con, f), "dia_semana_control_stock"))
    if f.isoweekday() != dia:
        raise ReglaNegocioError(f"El control de stock físico se realiza el {_dia_semana(dia)} (PR-FIN-03); "
                                f"el {fecha_pantalla(f)} es {_dia_semana(f.isoweekday())}.",
                                codigo="ACTA_DIA_NO_PERMITIDO")
    if con.execute("SELECT 1 FROM actas_stock WHERE fecha = ?", (f.isoformat(),)).fetchone():
        raise ReglaNegocioError(f"Ya existe el acta de stock del {fecha_pantalla(f)}.", codigo="ACTA_DUPLICADA")
    criticos = criticos_vigentes(con, f)
    faltan = [i["nombre"] for i in criticos if not i["transferencia_inmediata"] and int(i["id"]) not in conteos]
    if faltan:
        raise DatosInvalidosError(f"Falta la existencia física de: {', '.join(faltan)}.", codigo="CONTEO_INCOMPLETO")
    ciclo = ciclo_de_fecha(con, f)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO actas_stock (fecha, ejercicio, ciclo, deficit, elaborado_por, rol_elaborador, observaciones,
                   estado, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), ciclo["ejercicio"], ciclo["ciclo"], 0, sesion.nombre_usuario, sesion.rol,
             _opcional(observaciones, "observaciones", 500), "REGISTRADA", _ahora_iso()))
        acta_id = int(cur.lastrowid)
        deficit = False
        lineas = []
        for ins in criticos:
            saldo = saldo_insumo(con, int(ins["id"]))
            if ins["transferencia_inmediata"]:
                fisico = existencia_para_reorden(con, ins, f)
                contable_mil = fisico
            else:
                fisico = a_mil(conteos[int(ins["id"])], f"existencia de {ins['nombre']}", permitir_cero=True)
                contable_mil = saldo.cantidad_mil
            pr = punto_reorden_vigente(con, int(ins["id"]), f)
            punto = int(pr["punto_reorden_mil"]) if pr else None
            bajo = punto is not None and fisico < punto
            diferencia = fisico - contable_mil
            acciones = []
            if bajo:
                deficit = True
                acciones.append("Compra de emergencia (CI-FIN-16 Art. 10)")
            if diferencia:
                acciones.append(f"Investigar diferencia de {formato_cantidad(diferencia, ins['unidad'])} (riesgo 16)")
            if punto is None:
                acciones.append("Calcular el punto de reorden (CI-FIN-16 Art. 7)")
            accion = "; ".join(acciones) or "Sin novedad"
            con.execute(
                """INSERT INTO acta_stock_lineas (acta_id, insumo_id, existencia_fisica_mil, saldo_contable_mil,
                       diferencia_mil, punto_reorden_mil, bajo_pr, accion) VALUES (?,?,?,?,?,?,?,?)""",
                (acta_id, int(ins["id"]), fisico, contable_mil, diferencia, punto, int(bajo), accion))
            if bajo:
                _insertar_alerta(
                    con, sesion, ins, f, "BAJO_PUNTO_REORDEN", "ACTA_VIERNES", fisico, punto,
                    f"Acta FIN-EGR-01 del {fecha_pantalla(f)}: {ins['nombre']} con "
                    f"{formato_cantidad(fisico, ins['unidad'])} bajo el PR {formato_cantidad(punto, ins['unidad'])}.",
                    acta_id=acta_id)
            lineas.append({"insumo": ins["codigo"], "fisico": de_mil(fisico), "contable": de_mil(contable_mil),
                           "punto_reorden": de_mil(punto), "bajo_pr": bajo})
        if deficit:
            con.execute("UPDATE actas_stock SET deficit = 1 WHERE id = ?", (acta_id,))
        auditoria.registrar(con, sesion, "actas_stock", "INSERT", acta_id,
                            despues={"fecha": f.isoformat(), "deficit": deficit, "lineas": lineas})
    return acta_id


def verificar_acta_stock(con: sqlite3.Connection, sesion: Sesion, acta_id: int) -> None:
    """Firma de verificación de la otra unidad (control concurrente UT + Finanzas)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    with transaccion(con):
        acta = con.execute("SELECT * FROM actas_stock WHERE id = ?", (acta_id,)).fetchone()
        if acta is None:
            raise DatosInvalidosError(f"No existe el acta {acta_id}.")
        if acta["estado"] != "REGISTRADA":
            raise ReglaNegocioError("El acta ya está verificada.", codigo="ACTA_YA_VERIFICADA")
        if sesion.rol not in ROLES_ACTA or sesion.rol == acta["rol_elaborador"]:
            raise AutorizacionError("La verifica la otra unidad: si la elaboró la Unidad Técnica, verifica Finanzas, "
                                    "y viceversa (PR-FIN-03).", codigo="VERIFICACION_MISMA_UNIDAD")
        con.execute("UPDATE actas_stock SET estado = 'VERIFICADA', verificado_por = ?, rol_verificador = ?, "
                    "verificado_en = ? WHERE id = ?", (sesion.nombre_usuario, sesion.rol, _ahora_iso(), acta_id))
        auditoria.registrar(con, sesion, "actas_stock", "UPDATE", acta_id, antes={"estado": "REGISTRADA"},
                            despues={"estado": "VERIFICADA", "verificado_por": sesion.nombre_usuario})


def obtener_acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int) -> dict[str, Any]:
    """Acta con sus líneas y las compras de emergencia autorizadas sobre ella."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    acta = con.execute("SELECT * FROM actas_stock WHERE id = ?", (acta_id,)).fetchone()
    if acta is None:
        raise DatosInvalidosError(f"No existe el acta {acta_id}.")
    lineas = [dict(l) for l in con.execute(
        """SELECT l.*, i.nombre AS insumo, i.unidad, i.grupo_critico FROM acta_stock_lineas l
             JOIN insumos i ON i.id = l.insumo_id WHERE l.acta_id = ? ORDER BY i.cuenta_inventario""", (acta_id,))]
    emergencias = [dict(e) for e in con.execute(
        """SELECT e.*, u.nombre_completo AS autorizado_por, i.nombre AS insumo FROM compras_emergencia e
             JOIN alertas_stock a ON a.id = e.alerta_id JOIN insumos i ON i.id = a.insumo_id
             JOIN usuarios u ON u.id = e.autorizado_por_id
            WHERE a.acta_id = ? OR (a.insumo_id IN (SELECT insumo_id FROM acta_stock_lineas WHERE acta_id = ?
                                                    AND bajo_pr = 1) AND e.fecha = ?)""",
        (acta_id, acta_id, acta["fecha"]))]
    return {**dict(acta), "lineas": lineas, "emergencias": emergencias}


def listar_actas(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    return [dict(f) for f in con.execute("SELECT * FROM actas_stock ORDER BY fecha DESC")]


# ==========================================================================
# 6. ROTACIÓN DE GRANOS (PC-07: máximo 30 días de stock)
# ==========================================================================
def antiguedad_stock(con: sqlite3.Connection, insumo_id: int, fecha: Any) -> int | None:
    """Días de la entrada más antigua aún no consumida (flujo físico PEPS).

    El costo sigue siendo CPP; la antigüedad física se mide asumiendo que se
    consume primero lo que ingresó primero, como exige la rotación acelerada.
    """
    f = a_fecha(fecha)
    salidas = int(con.execute("SELECT COALESCE(SUM(cantidad_mil), 0) FROM kardex WHERE insumo_id = ? AND tipo <> "
                              "'ENTRADA_COMPRA' AND fecha <= ?", (insumo_id, f.isoformat())).fetchone()[0])
    acumulado = 0
    for entrada in con.execute("SELECT fecha, cantidad_mil FROM kardex WHERE insumo_id = ? AND tipo = 'ENTRADA_COMPRA' "
                               "AND fecha <= ? ORDER BY id", (insumo_id, f.isoformat())):
        acumulado += int(entrada["cantidad_mil"])
        if acumulado > salidas:
            return (f - a_fecha(entrada["fecha"])).days
    return None


def cobertura_dias(con: sqlite3.Connection, insumo_id: int, fecha: Any) -> Decimal | None:
    """Días de stock = existencia / consumo diario del PR vigente."""
    pr = punto_reorden_vigente(con, insumo_id, fecha)
    saldo = saldo_insumo(con, insumo_id)
    if pr is None or saldo.cantidad_mil == 0 or Decimal(pr["consumo_diario"]) <= 0:
        return None
    return (saldo.cantidad / Decimal(pr["consumo_diario"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def evaluar_rotacion(con: sqlite3.Connection, fecha: Any) -> list[dict[str, Any]]:
    """Antigüedad y cobertura de los granos frente al máximo de días."""
    f = a_fecha(fecha)
    maximo = int(_param(_params(con, f), "rotacion_granos_dias_max"))
    filas = []
    for ins in con.execute("SELECT * FROM insumos WHERE activo = 1 AND es_grano = 1 ORDER BY codigo"):
        saldo = saldo_insumo(con, int(ins["id"]))
        if saldo.cantidad_mil == 0:
            continue
        antig = antiguedad_stock(con, int(ins["id"]), f)
        cobertura = cobertura_dias(con, int(ins["id"]), f)
        excede = (antig is not None and antig > maximo) or (cobertura is not None and cobertura > maximo)
        filas.append({"insumo_id": int(ins["id"]), "insumo": ins["nombre"], "unidad": ins["unidad"],
                      "existencia": saldo.cantidad, "antiguedad_dias": antig, "cobertura_dias": cobertura,
                      "maximo_dias": maximo, "excede": excede})
    return filas


def _alertar_rotacion(con: sqlite3.Connection, sesion: Sesion, insumo: sqlite3.Row, fecha: date,
                      origen: str) -> int | None:
    maximo = int(_param(_params(con, fecha), "rotacion_granos_dias_max"))
    saldo = saldo_insumo(con, int(insumo["id"]))
    if saldo.cantidad_mil == 0:
        return None
    antig = antiguedad_stock(con, int(insumo["id"]), fecha)
    cobertura = cobertura_dias(con, int(insumo["id"]), fecha)
    motivos = []
    if antig is not None and antig > maximo:
        motivos.append(f"la entrada más antigua tiene {antig} días")
    if cobertura is not None and cobertura > maximo:
        motivos.append(f"la existencia cubre {str(cobertura).replace('.', ',')} días de consumo")
    if not motivos:
        return None
    detalle = (f"{insumo['nombre']}: {' y '.join(motivos)}; la rotación máxima de granos es de {maximo} días por la "
               "humedad de los Yungas (PC-07). Priorizar su consumo e inspeccionar el silo.")
    return _insertar_alerta(con, sesion, insumo, fecha, "ROTACION_EXCEDIDA", origen, saldo.cantidad_mil, None, detalle,
                            dias=antig if antig is not None else int(cobertura or 0))


def registrar_alertas_rotacion(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> list[int]:
    """Revisión de rotación de todos los granos (inspección quincenal, PC-07)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    f = a_fecha(fecha)
    ids = []
    with transaccion(con):
        for ins in con.execute("SELECT * FROM insumos WHERE activo = 1 AND es_grano = 1").fetchall():
            aid = _alertar_rotacion(con, sesion, ins, f, "REVISION_ROTACION")
            if aid:
                ids.append(aid)
    return ids


# ==========================================================================
# 7. BAJAS DE INVENTARIO (PC-07 §V.C)
# ==========================================================================
def _umbral_visto_cv(con: sqlite3.Connection, fecha: date) -> Decimal:
    return redondear_bs(_param(_params(con, fecha), "baja_inventario_cv_sobre_bs"))


def proponer_baja(
    con: sqlite3.Connection, sesion: Sesion, insumo_id: int, fecha: Any, cantidad: Any, causa: str, descripcion: str,
    informe_tecnico_ref: str,
) -> int:
    """Propone la baja con informe técnico (la Unidad Técnica informa el
    deterioro; Finanzas propone la baja a la GG, MOF)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    if causa not in CAUSAS_BAJA:
        raise DatosInvalidosError(f"Causa de baja no válida: {causa!r}. Use {', '.join(CAUSAS_BAJA)}.")
    insumo = obtener_insumo(con, insumo_id)
    f = a_fecha(fecha)
    cant = a_mil(cantidad)
    saldo = saldo_insumo(con, insumo_id)
    if cant > saldo.cantidad_mil:
        raise ReglaNegocioError(f"La baja supera la existencia ({formato_cantidad(saldo.cantidad_mil, insumo['unidad'])}).",
                                codigo="EXISTENCIA_INSUFICIENTE")
    detalle = _texto(descripcion, "descripción", 500)
    informe = _texto(informe_tecnico_ref, "informe técnico", 120)
    estimado = valorar_salida(saldo, cant)
    umbral = _umbral_visto_cv(con, f)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO bajas_inventario (insumo_id, fecha, cantidad_mil, causa, descripcion, informe_tecnico_ref,
                   valor_estimado_cent, requiere_visto_cv, estado, propuesto_por, propuesto_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (insumo_id, f.isoformat(), cant, causa, detalle, informe, a_centavos(estimado), int(estimado > umbral),
             "PROPUESTA", sesion.nombre_usuario, _ahora_iso()))
        bid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "bajas_inventario", "INSERT", bid,
                            despues={"insumo": insumo["codigo"], "cantidad": de_mil(cant), "causa": causa,
                                     "valor_estimado": estimado, "requiere_visto_cv": estimado > umbral})
    return bid


def _baja(con: sqlite3.Connection, baja_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM bajas_inventario WHERE id = ?", (baja_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la baja {baja_id}.")
    return fila


def autorizar_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int, acta_ref: str, aprobar: bool = True,
                   observacion: str | None = None) -> str:
    """La Gerencia General autoriza (o rechaza) la baja mediante acta firmada (PC-07 §V.C)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "AUTORIZAR")
    acta = _texto(acta_ref, "acta de baja firmada", 120)
    with transaccion(con):
        baja = _baja(con, baja_id)
        if baja["estado"] != "PROPUESTA":
            raise ReglaNegocioError(f"La baja está {baja['estado']}.", codigo="BAJA_NO_PROPUESTA")
        nuevo = "AUTORIZADA" if aprobar else "RECHAZADA"
        con.execute("UPDATE bajas_inventario SET estado = ?, autorizado_por = ?, autorizado_en = ?, acta_ref = ?, "
                    "observacion = COALESCE(?, observacion) WHERE id = ?",
                    (nuevo, sesion.nombre_usuario, _ahora_iso(), acta, _opcional(observacion, "observación", 500),
                     baja_id))
        auditoria.registrar(con, sesion, "bajas_inventario", "AUTORIZACION", baja_id, antes={"estado": "PROPUESTA"},
                            despues={"estado": nuevo, "acta_ref": acta})
    return nuevo


def visar_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int, observacion: str | None = None) -> None:
    """Visto del Consejo de Vigilancia sobre la baja (obligatorio sobre el umbral)."""
    seguridad.exigir_permiso(con, sesion, "inventarios", "VERIFICAR")
    with transaccion(con):
        baja = _baja(con, baja_id)
        if baja["estado"] not in ("PROPUESTA", "AUTORIZADA"):
            raise ReglaNegocioError(f"La baja está {baja['estado']}.", codigo="BAJA_NO_VISABLE")
        if baja["visto_cv_por"]:
            raise ReglaNegocioError("La baja ya tiene el visto del Consejo de Vigilancia.", codigo="VISTO_DUPLICADO")
        con.execute("UPDATE bajas_inventario SET visto_cv_por = ?, visto_cv_en = ?, "
                    "observacion = COALESCE(?, observacion) WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), _opcional(observacion, "observación", 500), baja_id))
        auditoria.registrar(con, sesion, "bajas_inventario", "AUTORIZACION", baja_id, antes={"visto_cv_por": None},
                            despues={"visto_cv_por": sesion.nombre_usuario})


def contabilizar_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int, fecha: Any) -> int:
    """Registra la baja autorizada al CPP vigente: gasto no operativo
    (parámetro ``cuenta_perdida_baja_inventario``) contra el inventario.

    # DISCREPANCIA: PC-07 §V.C menciona las reguladoras 4.1.01.0x de
    # deterioro (con numeración distinta al Plan v3, conciliación n.º 3). Esas
    # cuentas regulan existencias deterioradas que se conservan; la baja retira
    # físicamente el bien, por lo que se acredita directamente el inventario.

    Raises:
        AutorizacionError: falta el acta de la GG o el visto del CV sobre el umbral.
    """
    seguridad.exigir_permiso(con, sesion, "inventarios", "EDITAR")
    f = a_fecha(fecha)
    with transaccion(con):
        baja = _baja(con, baja_id)
        if baja["estado"] != "AUTORIZADA":
            raise AutorizacionError("La baja requiere el acta firmada por la Gerencia General (PC-07 §V.C).",
                                    codigo="BAJA_SIN_AUTORIZACION")
        insumo = obtener_insumo(con, int(baja["insumo_id"]))
        saldo = saldo_insumo(con, int(insumo["id"]))
        cant = int(baja["cantidad_mil"])
        if cant > saldo.cantidad_mil:
            raise ReglaNegocioError("La baja supera la existencia actual.", codigo="EXISTENCIA_INSUFICIENTE")
        valor = valorar_salida(saldo, cant)
        umbral = _umbral_visto_cv(con, f)
        if valor > umbral and not baja["visto_cv_por"]:
            raise AutorizacionError(
                f"La baja de {formato_bs(valor)} supera {formato_bs(umbral)}: requiere el visto del Consejo de "
                "Vigilancia (PC-07).", codigo="BAJA_SIN_VISTO_CV")
        cuenta_gasto = str(_param(_params(con, f), "cuenta_perdida_baja_inventario"))
        glosa = (f"Baja de inventario por {CAUSAS_BAJA[baja['causa']].lower()}: {insumo['nombre']} "
                 f"{formato_cantidad(cant, insumo['unidad'])} (acta {baja['acta_ref']})")
        asiento = contable.crear_asiento(
            con, sesion, f, glosa, [{"cuenta": cuenta_gasto, "debe": valor},
                                    {"cuenta": insumo["cuenta_inventario"], "haber": valor}],
            "INVENTARIOS", baja["acta_ref"], tipo="EGRESO")
        kid = _insertar_kardex(con, sesion, insumo, fecha=f, tipo="BAJA", cantidad_mil=cant, valor=valor,
                               asiento_id=asiento.id, documento_ref=baja["acta_ref"], baja_id=baja_id)
        con.execute("UPDATE bajas_inventario SET estado = 'CONTABILIZADA', valor_cent = ?, asiento_id = ?, "
                    "kardex_id = ?, contabilizado_en = ? WHERE id = ?",
                    (a_centavos(valor), asiento.id, kid, _ahora_iso(), baja_id))
        auditoria.registrar(con, sesion, "bajas_inventario", "UPDATE", baja_id, antes={"estado": "AUTORIZADA"},
                            despues={"estado": "CONTABILIZADA", "valor": valor, "asiento_id": asiento.id})
        if insumo["grupo_critico"]:
            verificar_punto_reorden(con, sesion, int(insumo["id"]), f, "MONITOREO_CONTINUO")
    return asiento.id


def listar_bajas(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "inventarios", "VER")
    sql = "SELECT b.*, i.nombre AS insumo, i.unidad FROM bajas_inventario b JOIN insumos i ON i.id = b.insumo_id"
    args: tuple[Any, ...] = ()
    if estado:
        sql, args = sql + " WHERE b.estado = ?", (estado,)
    filas = []
    for f in con.execute(sql + " ORDER BY b.id DESC", args):
        d = dict(f)
        d.update(cantidad=de_mil(f["cantidad_mil"]), valor_estimado=de_centavos(int(f["valor_estimado_cent"])),
                 valor=de_centavos(int(f["valor_cent"])) if f["valor_cent"] is not None else None)
        filas.append(d)
    return filas
