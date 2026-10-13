"""Tesorería del ERP Pollolandia (Fase 3, Ciclo 1 — Financiamiento).

Implementa POL-04, CI-FIN-01, CI-FIN-02 (Art. 9), CI-FIN-07 y los formatos
FIN-TES-05 a FIN-TES-08, INF-CV-03 y FIN-INF-02:

1. Caja: fondo fijo (1.1.01.05), gastos menores con comprobante, cobros en
   efectivo (1.1.01.01), depósito diario del excedente sobre el tope,
   reposición validada por la GG y arqueo sorpresivo del CV.
2. Bancos (1.1.01.03 / 1.1.01.04): depósitos, pagos con autorización CI-FIN-09,
   transferencias, cargos y abonos bancarios.
3. ``validar_compromiso``: proyecta el saldo bancario y suspende el compromiso
   si queda bajo el 120 % del SME (autorización escrita de la GG).
4. Jerarquía de pagos de 7 niveles (POL-04 Art. 2).
5. Bloqueo del pago de excedentes si el saldo posterior queda bajo el SME.
6. Flujo de caja proyectado a dos ciclos con saldo mínimo diario, alertas
   (FIN-TES-07) y medidas correctivas (FIN-TES-08).
7. Reporte diario de saldo frente al SME con semáforo (FIN-TES-06).
8. Conciliación bancaria mensual (FIN-INF-02) con plazo de días hábiles y
   firma de la GG.

Todas las cifras (topes, SME, umbrales, plazos) se leen de ``parametros``.
Todo hecho económico se registra con ``servicios.contable.crear_asiento``.
Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from db.conexion import (
    a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion,
)
from servicios import auditoria, contable, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import RequisitoAutorizacion, Sesion, TipoDecision
from utils.formato import (
    a_fecha, fecha_cierre_ciclo, fecha_inicio_ciclo_siguiente, fecha_pantalla, formato_bs, redondear_bs,
    sumar_dias_habiles,
)

# --------------------------------------------------------------------------
# Cuentas del Plan de Cuentas v3 que usa la tesorería (estructura, no cifras)
# --------------------------------------------------------------------------
CAJA_FONDO = "1.1.01.05"          # Fondo Rotativo – Caja Chica
CAJA_MN = "1.1.01.01"             # Caja – Efectivo en Moneda Nacional (cobros en efectivo)
BANCOS: tuple[str, ...] = ("1.1.01.03", "1.1.01.04")
EXCEDENTES_POR_PAGAR = "2.1.05.01"
CERO = Decimal("0.00")

ETIQUETA_JERARQUIA: dict[str, str] = {
    "PLANILLA_APORTES": "Planilla del personal estable y aportes a la Gestora Pública",
    "POLLITOS_INSUMOS_NUEVO_CICLO": "Pollitos BB e insumos para el inicio del nuevo ciclo",
    "ALIMENTO_VETERINARIOS_CICLO": "Alimento e insumos veterinarios del ciclo en curso",
    "OBLIGACIONES_RAU": "Obligaciones tributarias (RAU)",
    "CERTIFICACIONES": "Certificaciones (SENASAG/IBNORCA)",
    "CAPEX_APROBADO": "Inversiones CAPEX aprobadas",
    "EXCEDENTES_SOCIOS": "Excedentes a socios",
}

MEDIDAS_CORRECTIVAS: dict[str, str] = {  # POL-04 Art. 4 / FIN-TES-08
    "acelerar_cobranza": "Acelerar la cobranza de ventas a crédito",
    "postergar_capex": "Postergar inversiones CAPEX no críticas",
    "negociar_proveedores": "Negociar condiciones de pago con proveedores",
    "informar_asamblea": "Informar a la Asamblea General para resolución",
}

TIPO_DECISION_COMPROMISO: dict[str, TipoDecision] = {
    "COMPRA": TipoDecision.COMPRA_INSUMOS,
    "PERSONAL_EVENTUAL": TipoDecision.CONTRATACION_EVENTUAL,
    "OTRO": TipoDecision.COMPROMISO_GASTO,
}

CATEGORIAS_FLUJO: dict[str, str] = {
    "INGRESO_VENTAS": "(+) Ingresos esperados por ventas",
    "EGRESO_OPERATIVO": "(–) Egresos operativos programados",
    "CAPEX": "(–) Inversiones CAPEX del período",
    "EXCEDENTES": "(–) Pagos de excedentes comprometidos",
}

TIPOS_PARTIDA: dict[str, str] = {
    "CHEQUE_CIRCULACION": "Cheque en circulación (registrado en libros, no cobrado en el banco)",
    "DEPOSITO_TRANSITO": "Depósito en tránsito (registrado en libros, no acreditado en el banco)",
    "CARGO_NO_REGISTRADO": "Cargo bancario no registrado en libros",
    "ABONO_NO_REGISTRADO": "Abono bancario no registrado en libros (intereses u otros)",
    "ERROR_LIBROS_MAS": "Corrección de libros que aumenta el saldo",
    "ERROR_LIBROS_MENOS": "Corrección de libros que disminuye el saldo",
}


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


def _monto_positivo(valor: Any, campo: str = "monto") -> Decimal:
    importe = redondear_bs(valor)
    if importe <= 0:
        raise DatosInvalidosError(f"El {campo} debe ser mayor a cero.")
    return importe


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    """Parámetros vigentes del ejercicio al que pertenece la fecha."""
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _validar_banco(cuenta: str) -> str:
    if cuenta not in BANCOS:
        raise DatosInvalidosError(f"La cuenta {cuenta!r} no es una cuenta bancaria ({', '.join(BANCOS)}).")
    return cuenta


def _pct(fraccion: Decimal) -> Decimal:
    return (Decimal(fraccion) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def saldo_cuenta(con: sqlite3.Connection, cuenta: str, hasta: Any = None) -> Decimal:
    """Saldo Debe − Haber de una cuenta hasta una fecha (inclusive)."""
    if hasta is None:
        fila = con.execute(
            "SELECT COALESCE(SUM(debe_cent), 0) - COALESCE(SUM(haber_cent), 0) FROM asiento_detalle "
            "WHERE cuenta_codigo = ?", (cuenta,)).fetchone()
    else:
        fila = con.execute(
            """SELECT COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0)
                 FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
                WHERE d.cuenta_codigo = ? AND a.fecha <= ?""", (cuenta, a_fecha(hasta).isoformat())).fetchone()
    return de_centavos(int(fila[0]))


def saldo_bancario(con: sqlite3.Connection, fecha: Any = None) -> Decimal:
    """Saldo bancario disponible según libros (1.1.01.03 + 1.1.01.04)."""
    return sum((saldo_cuenta(con, c, fecha) for c in BANCOS), CERO)


def sme_vigente(con: sqlite3.Connection, fecha: Any) -> Decimal:
    """SME del ejercicio de la fecha (POL-04 Art. 1), con el piso absoluto
    adicional decidido por el usuario como mínimo."""
    p = _params(con, fecha)
    return redondear_bs(max(Decimal(_param(p, "sme")), Decimal(_param(p, "sme_piso_absoluto"))))


def _exigir_autorizacion_desembolso(
    con: sqlite3.Connection, fecha: date, monto: Decimal, autorizacion_id: int | None
) -> RequisitoAutorizacion:
    """Aplica CI-FIN-09 a un desembolso bancario. Si la matriz exige firma,
    la autorización DESEMBOLSO debe estar completa (incluida la mancomunada)."""
    requisito = seguridad.requiere_autorizacion(TipoDecision.DESEMBOLSO, monto, seguridad.contexto_desde_bd(con, fecha))
    if requisito.requiere_firma:
        if autorizacion_id is None:
            firmas = ", ".join(seguridad.NOMBRE_CALIDAD[c] for c in requisito.firmas)
            raise AutorizacionError(
                f"El desembolso de {formato_bs(monto)} requiere autorización de {firmas} (CI-FIN-09): indique "
                "la autorización DESEMBOLSO firmada.", codigo="DESEMBOLSO_SIN_AUTORIZACION",
                detalle={"firmas": list(requisito.firmas)})
        seguridad.verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.DESEMBOLSO, monto=monto)
    elif autorizacion_id is not None:
        seguridad.verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.DESEMBOLSO, monto=monto)
    return requisito


def _insertar_mov_banco(
    con: sqlite3.Connection, sesion: Sesion, *, cuenta: str, tipo: str, fecha: date, monto: Decimal,
    contrapartida: str, descripcion: str, asiento_id: int, beneficiario: str | None = None,
    nro_cheque: str | None = None, documento_ref: str | None = None, categoria_pago: str | None = None,
    autorizacion_id: int | None = None, compromiso_id: int | None = None,
) -> int:
    cur = con.execute(
        """INSERT INTO movimientos_bancarios (cuenta_codigo, tipo, fecha, monto_cent, contrapartida, beneficiario,
               nro_cheque, documento_ref, descripcion, categoria_pago, autorizacion_id, compromiso_id, asiento_id,
               registrado_por, registrado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cuenta, tipo, fecha.isoformat(), a_centavos(monto), contrapartida, beneficiario, nro_cheque, documento_ref,
         descripcion, categoria_pago, autorizacion_id, compromiso_id, asiento_id, sesion.nombre_usuario,
         _ahora_iso()))
    mid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "movimientos_bancarios", "INSERT", mid,
                        despues={"cuenta": cuenta, "tipo": tipo, "fecha": fecha.isoformat(), "monto": monto,
                                 "contrapartida": contrapartida, "asiento_id": asiento_id,
                                 "autorizacion_id": autorizacion_id, "categoria_pago": categoria_pago})
    return mid


# ==========================================================================
# 1. CAJA Y FONDO FIJO (CI-FIN-01)
# ==========================================================================
def fondo_activo(con: sqlite3.Connection) -> sqlite3.Row | None:
    """Fondo de caja vigente (hay uno solo a la vez)."""
    return con.execute("SELECT * FROM fondos_caja WHERE estado = 'ACTIVO'").fetchone()


def _exigir_fondo(con: sqlite3.Connection) -> sqlite3.Row:
    fondo = fondo_activo(con)
    if fondo is None:
        raise ReglaNegocioError("No hay un fondo de caja constituido: constitúyalo primero (CI-FIN-01, Paso 1).",
                                codigo="FONDO_NO_CONSTITUIDO")
    return fondo


def _insertar_mov_caja(
    con: sqlite3.Connection, sesion: Sesion, *, fondo_id: int, tipo: str, fecha: date, monto: Decimal,
    descripcion: str, contrapartida: str, asiento_id: int, comprobante: str | None = None,
    beneficiario: str | None = None, reposicion_id: int | None = None,
) -> int:
    try:
        cur = con.execute(
            """INSERT INTO caja_movimientos (fondo_id, tipo, fecha, monto_cent, comprobante, beneficiario, descripcion,
                   cuenta_contrapartida, asiento_id, reposicion_id, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fondo_id, tipo, fecha.isoformat(), a_centavos(monto), comprobante, beneficiario, descripcion,
             contrapartida, asiento_id, reposicion_id, sesion.nombre_usuario, _ahora_iso()))
    except sqlite3.IntegrityError as exc:
        if "comprobante" in str(exc) or "UNIQUE" in str(exc):
            raise DatosInvalidosError(f"El comprobante {comprobante} ya fue registrado (riesgo 35: comprobantes "
                                      "duplicados).", codigo="COMPROBANTE_DUPLICADO") from exc
        raise
    mid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "caja_movimientos", "INSERT", mid,
                        despues={"tipo": tipo, "fecha": fecha.isoformat(), "monto": monto, "comprobante": comprobante,
                                 "contrapartida": contrapartida, "asiento_id": asiento_id})
    return mid


def constituir_fondo(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, custodio: str, resolucion_ref: str, *,
    cuenta_banco: str = "1.1.01.03", autorizacion_id: int | None = None,
) -> int:
    """Constituye el fondo fijo de caja por el monto de ``tope_caja_bs``
    (CI-FIN-01, Paso 1): asiento 1.1.01.05 contra banco.

    Raises:
        ReglaNegocioError: ya existe un fondo activo.
        AutorizacionError: falta la autorización de desembolso (CI-FIN-09).
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    nombre_custodio = _texto(custodio, "custodio", 120)
    resolucion = _texto(resolucion_ref, "resolución de designación del custodio", 120)
    monto = redondear_bs(_param(_params(con, f), "tope_caja_bs"))
    with transaccion(con):
        if fondo_activo(con) is not None:
            raise ReglaNegocioError("Ya existe un fondo de caja activo.", codigo="FONDO_YA_CONSTITUIDO")
        _exigir_autorizacion_desembolso(con, f, monto, autorizacion_id)
        asiento = contable.crear_asiento(
            con, sesion, f, f"Constitución del fondo fijo de caja (custodio: {nombre_custodio})",
            [{"cuenta": CAJA_FONDO, "debe": monto}, {"cuenta": cuenta_banco, "haber": monto}],
            "TESORERIA", resolucion, tipo="EGRESO")
        cur = con.execute(
            """INSERT INTO fondos_caja (monto_fijo_cent, custodio, resolucion_ref, fecha_constitucion, asiento_id,
                                        registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?)""",
            (a_centavos(monto), nombre_custodio, resolucion, f.isoformat(), asiento.id, sesion.nombre_usuario,
             _ahora_iso()))
        fondo_id = int(cur.lastrowid)
        _insertar_mov_caja(con, sesion, fondo_id=fondo_id, tipo="CONSTITUCION", fecha=f, monto=monto,
                           descripcion="Constitución del fondo fijo", contrapartida=cuenta_banco, asiento_id=asiento.id)
        _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="PAGO", fecha=f, monto=monto,
                            contrapartida=CAJA_FONDO, descripcion="Constitución del fondo fijo de caja",
                            asiento_id=asiento.id, beneficiario=nombre_custodio, documento_ref=resolucion,
                            autorizacion_id=autorizacion_id)
        if autorizacion_id is not None:
            seguridad.consumir_autorizacion(con, sesion, autorizacion_id, "fondos_caja", fondo_id,
                                            tipo_decision=TipoDecision.DESEMBOLSO, monto=monto)
        auditoria.registrar(con, sesion, "fondos_caja", "INSERT", fondo_id,
                            despues={"monto_fijo": monto, "custodio": nombre_custodio, "resolucion": resolucion})
    return fondo_id


def registrar_gasto_menor(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, cuenta_gasto: str, comprobante: str,
    beneficiario: str, descripcion: str, *, sufijo: str | None = None,
) -> int:
    """Gasto menor pagado con el fondo (CI-FIN-01, Paso 3): asiento a la cuenta
    de gasto contra 1.1.01.05, con comprobante obligatorio y único.

    # DISCREPANCIA: el Manual de Procesos (FICHA-FIN-01, paso 3) dice que el
    # custodio "autoriza" los gastos menores; CI-FIN-01 (Preguntas
    # frecuentes) dice que la autorización y la validación previa a la
    # reposición corresponden a la Gerencia General, nunca al custodio. Se
    # aplica CI-FIN-01: el JF registra el gasto con su comprobante y la GG lo
    # valida al revisar la reposición (validar_reposicion).

    Raises:
        ReglaNegocioError: monto mayor al gasto menor máximo, fondo inexistente
            o insuficiente, o cuenta de disponible.
        DatosInvalidosError: comprobante faltante o duplicado.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    importe = _monto_positivo(monto)
    limite = redondear_bs(_param(_params(con, f), "gasto_menor_max_bs"))
    if importe > limite:
        raise ReglaNegocioError(
            f"El gasto de {formato_bs(importe)} supera el máximo de {formato_bs(limite)} por operación con caja "
            "(POL-04 Art. 3): tramítelo por el procedimiento ordinario de compras (PR-FIN-03).",
            codigo="GASTO_MENOR_EXCEDE")
    nro = _texto(comprobante, "comprobante", 40)
    if cuenta_gasto.startswith("1.1.01"):
        raise ReglaNegocioError("Un gasto menor no puede imputarse a una cuenta de disponible.",
                                codigo="CUENTA_NO_PERMITIDA")
    quien = _texto(beneficiario, "beneficiario", 120)
    detalle = _texto(descripcion, "descripción", 300)
    with transaccion(con):
        fondo = _exigir_fondo(con)
        disponible = saldo_cuenta(con, CAJA_FONDO)
        if importe > disponible:
            raise ReglaNegocioError(f"El fondo solo tiene {formato_bs(disponible)} disponibles: solicite la "
                                    "reposición (CI-FIN-01, Paso 5).", codigo="FONDO_INSUFICIENTE")
        asiento = contable.crear_asiento(
            con, sesion, f, f"Gasto menor: {detalle}",
            [{"cuenta": cuenta_gasto, "debe": importe, "sufijo": sufijo}, {"cuenta": CAJA_FONDO, "haber": importe}],
            "CAJA", nro, tipo="EGRESO")
        return _insertar_mov_caja(con, sesion, fondo_id=int(fondo["id"]), tipo="GASTO_MENOR", fecha=f, monto=importe,
                                  descripcion=detalle, contrapartida=cuenta_gasto, asiento_id=asiento.id,
                                  comprobante=nro, beneficiario=quien)


def registrar_ingreso_efectivo(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, cuenta_ingreso: str, documento_ref: str,
    descripcion: str,
) -> int:
    """Cobro en efectivo que queda en custodia junto al fondo (CI-FIN-01,
    Paso 2): asiento 1.1.01.01 contra la cuenta de ingreso o de cobro."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    importe = _monto_positivo(monto)
    if not (cuenta_ingreso.startswith("5.") or cuenta_ingreso.startswith("1.1.02") or cuenta_ingreso.startswith("2.1.04")):
        raise ReglaNegocioError("El cobro en efectivo se imputa a ingresos (Grupo 5), cuentas por cobrar (1.1.02) o "
                                "anticipos de clientes (2.1.04).", codigo="CUENTA_NO_PERMITIDA")
    doc = _texto(documento_ref, "documento de respaldo", 60)
    detalle = _texto(descripcion, "descripción", 300)
    with transaccion(con):
        fondo = _exigir_fondo(con)
        asiento = contable.crear_asiento(
            con, sesion, f, f"Cobro en efectivo: {detalle}",
            [{"cuenta": CAJA_MN, "debe": importe}, {"cuenta": cuenta_ingreso, "haber": importe}],
            "CAJA", doc, tipo="INGRESO")
        return _insertar_mov_caja(con, sesion, fondo_id=int(fondo["id"]), tipo="INGRESO_EFECTIVO", fecha=f,
                                  monto=importe, descripcion=detalle, contrapartida=cuenta_ingreso,
                                  asiento_id=asiento.id, comprobante=doc)


def control_custodia(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    """Efectivo en custodia al cierre del día y excedente a depositar
    (CI-FIN-01, Paso 4)."""
    f = a_fecha(fecha)
    tope = redondear_bs(_param(_params(con, f), "tope_caja_bs"))
    ventas = saldo_cuenta(con, CAJA_MN, f)
    fondo = saldo_cuenta(con, CAJA_FONDO, f)
    total = ventas + fondo
    excedente = max(total - tope, CERO)
    return {"fecha": f.isoformat(), "efectivo_ventas": ventas, "fondo_disponible": fondo, "total_custodia": total,
            "tope": tope, "excedente": excedente, "deposito_obligatorio": excedente > 0}


def depositar_excedente(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, boleta_ref: str,
    cuenta_banco: str = "1.1.01.03",
) -> int:
    """Depósito del efectivo en custodia que excede el tope (CI-FIN-01, Paso 4).
    El depósito debe dejar el efectivo en custodia igual o por debajo del tope."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    importe = _monto_positivo(monto)
    boleta = _texto(boleta_ref, "boleta de depósito", 60)
    with transaccion(con):
        fondo = _exigir_fondo(con)
        control = control_custodia(con, f)
        if importe > control["efectivo_ventas"]:
            raise ReglaNegocioError(f"Solo hay {formato_bs(control['efectivo_ventas'])} de cobros en efectivo para "
                                    "depositar.", codigo="DEPOSITO_EXCEDE_EFECTIVO")
        if control["total_custodia"] - importe > control["tope"]:
            raise ReglaNegocioError(
                f"El depósito debe dejar el efectivo en custodia en {formato_bs(control['tope'])} o menos: "
                f"deposite al menos {formato_bs(control['excedente'])}.", codigo="DEPOSITO_INSUFICIENTE")
        asiento = contable.crear_asiento(
            con, sesion, f, "Depósito diario del excedente de caja",
            [{"cuenta": cuenta_banco, "debe": importe}, {"cuenta": CAJA_MN, "haber": importe}],
            "CAJA", boleta, tipo="TRASPASO")
        mid = _insertar_mov_caja(con, sesion, fondo_id=int(fondo["id"]), tipo="DEPOSITO_EXCEDENTE", fecha=f,
                                 monto=importe, descripcion="Depósito diario del excedente", contrapartida=cuenta_banco,
                                 asiento_id=asiento.id, comprobante=boleta)
        _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="DEPOSITO", fecha=f, monto=importe,
                            contrapartida=CAJA_MN, descripcion="Depósito del excedente de caja",
                            asiento_id=asiento.id, documento_ref=boleta)
    return mid


def dias_con_exceso(con: sqlite3.Connection, desde: Any, hasta: Any) -> list[dict[str, Any]]:
    """Días en que el efectivo en custodia cerró por encima del tope (control
    del límite diario que verifica el arqueo, CI-FIN-01 Paso 7)."""
    d, h = a_fecha(desde), a_fecha(hasta)
    fechas = [r[0] for r in con.execute(
        """SELECT DISTINCT a.fecha FROM asiento_detalle x JOIN asientos a ON a.id = x.asiento_id
            WHERE x.cuenta_codigo IN (?, ?) AND a.fecha BETWEEN ? AND ? ORDER BY a.fecha""",
        (CAJA_MN, CAJA_FONDO, d.isoformat(), h.isoformat()))]
    resultado = []
    for texto in fechas:
        control = control_custodia(con, texto)
        if control["excedente"] > 0:
            resultado.append({"fecha": texto, "total_custodia": control["total_custodia"],
                              "excedente": control["excedente"]})
    return resultado


def gastos_pendientes_reposicion(con: sqlite3.Connection, hasta: Any = None) -> list[dict[str, Any]]:
    """Gastos menores aún no repuestos (sin reposición o con reposición no ejecutada)."""
    sql = """SELECT m.* FROM caja_movimientos m LEFT JOIN reposiciones_caja r ON r.id = m.reposicion_id
              WHERE m.tipo = 'GASTO_MENOR' AND (m.reposicion_id IS NULL OR r.estado <> 'EJECUTADA')"""
    args: tuple[Any, ...] = ()
    if hasta is not None:
        sql += " AND m.fecha <= ?"
        args = (a_fecha(hasta).isoformat(),)
    return [{**dict(f), "monto": de_centavos(int(f["monto_cent"]))} for f in con.execute(sql + " ORDER BY m.id", args)]


def solicitar_reposicion(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> int:
    """Agrupa los gastos menores pendientes en una solicitud de reposición
    que la GG debe validar (CI-FIN-01, Paso 5)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    with transaccion(con):
        fondo = _exigir_fondo(con)
        pendientes = [g for g in gastos_pendientes_reposicion(con, f) if g["reposicion_id"] is None]
        if not pendientes:
            raise ReglaNegocioError("No hay gastos menores pendientes de reposición.", codigo="SIN_GASTOS")
        total = sum((g["monto"] for g in pendientes), CERO)
        cur = con.execute(
            """INSERT INTO reposiciones_caja (fondo_id, fecha_solicitud, total_cent, solicitado_por)
               VALUES (?,?,?,?)""", (fondo["id"], f.isoformat(), a_centavos(total), sesion.nombre_usuario))
        rid = int(cur.lastrowid)
        con.executemany("UPDATE caja_movimientos SET reposicion_id = ? WHERE id = ?",
                        [(rid, g["id"]) for g in pendientes])
        auditoria.registrar(con, sesion, "reposiciones_caja", "INSERT", rid,
                            despues={"total": total, "comprobantes": [g["comprobante"] for g in pendientes]})
    return rid


def validar_reposicion(
    con: sqlite3.Connection, sesion: Sesion, reposicion_id: int, *, aprobar: bool = True,
    observacion: str | None = None,
) -> str:
    """La GG revisa los comprobantes y valida o rechaza la reposición
    (CI-FIN-01, Paso 5). Al rechazarla, los gastos quedan libres para una
    nueva solicitud una vez corregidos."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "AUTORIZAR")
    nota = _opcional(observacion, "observación")
    if not aprobar and not nota:
        raise DatosInvalidosError("Indique el motivo del rechazo de la reposición.")
    with transaccion(con):
        fila = con.execute("SELECT * FROM reposiciones_caja WHERE id = ?", (reposicion_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la reposición {reposicion_id}.")
        if fila["estado"] != "SOLICITADA":
            raise ReglaNegocioError(f"La reposición ya está {fila['estado']}.", codigo="REPOSICION_NO_PENDIENTE")
        if fila["solicitado_por"] == sesion.nombre_usuario:
            raise AutorizacionError("Quien solicita la reposición no puede validarla.", codigo="AUTOFIRMA")
        estado = "VALIDADA" if aprobar else "RECHAZADA"
        con.execute("UPDATE reposiciones_caja SET estado = ?, validado_por = ?, validado_en = ?, observacion = ? "
                    "WHERE id = ?", (estado, sesion.nombre_usuario, _ahora_iso(), nota, reposicion_id))
        if not aprobar:
            con.execute("UPDATE caja_movimientos SET reposicion_id = NULL WHERE reposicion_id = ?", (reposicion_id,))
        auditoria.registrar(con, sesion, "reposiciones_caja", "AUTORIZACION", reposicion_id,
                            antes={"estado": "SOLICITADA"}, despues={"estado": estado, "observacion": nota})
    return estado


def ejecutar_reposicion(
    con: sqlite3.Connection, sesion: Sesion, reposicion_id: int, fecha: Any, cuenta_banco: str = "1.1.01.03",
    *, autorizacion_id: int | None = None,
) -> int:
    """Repone el fondo por cheque o transferencia al custodio (1.1.01.05
    contra banco). La validación de la GG es la autorización del desembolso;
    si el total superara el umbral de firma mancomunada se exige además la
    autorización DESEMBOLSO completa."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    with transaccion(con):
        fila = con.execute("SELECT * FROM reposiciones_caja WHERE id = ?", (reposicion_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la reposición {reposicion_id}.")
        if fila["estado"] != "VALIDADA":
            raise AutorizacionError("La reposición debe estar validada por la Gerencia General (CI-FIN-01, Paso 5).",
                                    codigo="REPOSICION_NO_VALIDADA")
        total = de_centavos(int(fila["total_cent"]))
        mancomunada = redondear_bs(_param(_params(con, f), "firma_mancomunada_sobre_bs"))
        if total > mancomunada:
            _exigir_autorizacion_desembolso(con, f, total, autorizacion_id)
        fondo = con.execute("SELECT * FROM fondos_caja WHERE id = ?", (fila["fondo_id"],)).fetchone()
        asiento = contable.crear_asiento(
            con, sesion, f, f"Reposición del fondo de caja (solicitud n.º {reposicion_id})",
            [{"cuenta": CAJA_FONDO, "debe": total}, {"cuenta": cuenta_banco, "haber": total}],
            "CAJA", f"Reposición {reposicion_id}", tipo="EGRESO")
        _insertar_mov_caja(con, sesion, fondo_id=int(fila["fondo_id"]), tipo="REPOSICION", fecha=f, monto=total,
                           descripcion=f"Reposición n.º {reposicion_id}", contrapartida=cuenta_banco,
                           asiento_id=asiento.id)
        _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="PAGO", fecha=f, monto=total,
                            contrapartida=CAJA_FONDO, descripcion=f"Reposición del fondo de caja n.º {reposicion_id}",
                            asiento_id=asiento.id, beneficiario=fondo["custodio"], autorizacion_id=autorizacion_id)
        con.execute("UPDATE reposiciones_caja SET estado = 'EJECUTADA', cuenta_banco = ?, asiento_id = ?, "
                    "ejecutado_en = ? WHERE id = ?", (cuenta_banco, asiento.id, _ahora_iso(), reposicion_id))
        if autorizacion_id is not None and total > mancomunada:
            seguridad.consumir_autorizacion(con, sesion, autorizacion_id, "reposiciones_caja", reposicion_id,
                                            tipo_decision=TipoDecision.DESEMBOLSO, monto=total)
        auditoria.registrar(con, sesion, "reposiciones_caja", "UPDATE", reposicion_id,
                            antes={"estado": "VALIDADA"}, despues={"estado": "EJECUTADA", "asiento_id": asiento.id})
    return asiento.id


def saldo_contable_caja(con: sqlite3.Connection, fecha: Any) -> Decimal:
    """Saldo contable del fondo para el arqueo: fondo disponible + gastos
    pendientes de reposición + cobros en efectivo en custodia (equivale al
    fondo fijo más el efectivo de ventas no depositado)."""
    f = a_fecha(fecha)
    pendientes = sum((g["monto"] for g in gastos_pendientes_reposicion(con, f)), CERO)
    return saldo_cuenta(con, CAJA_FONDO, f) + pendientes + saldo_cuenta(con, CAJA_MN, f)


def registrar_arqueo(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, efectivo_contado: Any, comprobantes_no_repuestos: Any,
    custodio_presente: str, observaciones: str | None = None,
) -> dict[str, Any]:
    """Acta de arqueo sorpresivo del CV (INF-CV-03). La diferencia se informa;
    nunca se compensa con ingresos futuros (CI-FIN-01)."""
    seguridad.exigir_permiso(con, sesion, "vigilancia", "CREAR")
    f = a_fecha(fecha)
    efectivo = redondear_bs(efectivo_contado)
    comprobantes = redondear_bs(comprobantes_no_repuestos)
    if efectivo < 0 or comprobantes < 0:
        raise DatosInvalidosError("Los montos del arqueo no pueden ser negativos.")
    presente = _texto(custodio_presente, "custodio presente", 120)
    with transaccion(con):
        fondo = _exigir_fondo(con)
        fisico = efectivo + comprobantes
        contable_ = saldo_contable_caja(con, f)
        diferencia = fisico - contable_
        anterior = con.execute("SELECT MAX(fecha) FROM arqueos_caja WHERE fecha < ?", (f.isoformat(),)).fetchone()[0]
        desde = date.fromisoformat(anterior) + timedelta(days=1) if anterior else date.fromisoformat(
            fondo["fecha_constitucion"])
        excesos = dias_con_exceso(con, desde, f)
        cur = con.execute(
            """INSERT INTO arqueos_caja (fecha, fondo_id, efectivo_contado_cent, comprobantes_no_repuestos_cent,
                   total_fisico_cent, saldo_contable_cent, diferencia_cent, limite_diario_cumplido, dias_con_exceso,
                   custodio_presente, observaciones, arqueado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), fondo["id"], a_centavos(efectivo), a_centavos(comprobantes), a_centavos(fisico),
             a_centavos(contable_), a_centavos(diferencia), 0 if excesos else 1, len(excesos), presente,
             _opcional(observaciones, "observaciones", 500), sesion.nombre_usuario, _ahora_iso()))
        aid = int(cur.lastrowid)
        resultado = {"id": aid, "fecha": f.isoformat(), "efectivo_contado": efectivo,
                     "comprobantes_no_repuestos": comprobantes, "total_fisico": fisico, "saldo_contable": contable_,
                     "diferencia": diferencia, "limite_diario_cumplido": not excesos, "dias_con_exceso": excesos}
        auditoria.registrar(con, sesion, "arqueos_caja", "INSERT", aid, despues=resultado,
                            detalle="Arqueo sorpresivo INF-CV-03" + (" con diferencia" if diferencia else ""))
    return resultado


def arqueos_del_ejercicio(con: sqlite3.Connection, ejercicio: int) -> dict[str, Any]:
    """Arqueos realizados en el ejercicio frente al mínimo anual (``arqueos_por_anio``)."""
    fila = con.execute("SELECT fecha_inicio, fecha_fin FROM ejercicios WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el ejercicio {ejercicio}.")
    n = int(con.execute("SELECT COUNT(*) FROM arqueos_caja WHERE fecha BETWEEN ? AND ?",
                        (fila[0], fila[1])).fetchone()[0])
    minimo = int(_param(obtener_parametros_vigentes(con, ejercicio), "arqueos_por_anio"))
    return {"ejercicio": ejercicio, "realizados": n, "minimo": minimo, "cumple": n >= minimo}


# ==========================================================================
# 2. BANCOS
# ==========================================================================
def registrar_cuenta_bancaria(
    con: sqlite3.Connection, sesion: Sesion, cuenta_codigo: str, banco: str, numero_cuenta: str, moneda: str = "BOB",
) -> int:
    """Datos bancarios de 1.1.01.03 (cuenta corriente) o 1.1.01.04 (ahorros)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    _validar_banco(cuenta_codigo)
    nombre = _texto(banco, "banco", 80)
    numero = _texto(numero_cuenta, "número de cuenta", 40)
    with transaccion(con):
        try:
            cur = con.execute(
                """INSERT INTO cuentas_bancarias (cuenta_codigo, banco, numero_cuenta, moneda, registrado_por,
                                                  registrado_en) VALUES (?,?,?,?,?,?)""",
                (cuenta_codigo, nombre, numero, moneda, sesion.nombre_usuario, _ahora_iso()))
        except sqlite3.IntegrityError as exc:
            raise DatosInvalidosError(f"La cuenta {cuenta_codigo} ya tiene datos bancarios registrados.") from exc
        cid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "cuentas_bancarias", "INSERT", cid,
                            despues={"cuenta": cuenta_codigo, "banco": nombre, "numero": numero, "moneda": moneda})
    return cid


def registrar_deposito(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, cuenta_banco: str, contrapartida: str,
    descripcion: str, documento_ref: str | None = None,
) -> int:
    """Depósito o acreditación en banco (asiento de ingreso)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    importe = _monto_positivo(monto)
    if contrapartida.startswith("1.1.01"):
        raise ReglaNegocioError("Para mover fondos entre caja y bancos use el depósito de excedente o la "
                                "transferencia.", codigo="CUENTA_NO_PERMITIDA")
    detalle = _texto(descripcion, "descripción")
    doc = _opcional(documento_ref, "documento", 60)
    with transaccion(con):
        asiento = contable.crear_asiento(
            con, sesion, f, detalle, [{"cuenta": cuenta_banco, "debe": importe},
                                      {"cuenta": contrapartida, "haber": importe}], "TESORERIA", doc, tipo="INGRESO")
        return _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="DEPOSITO", fecha=f, monto=importe,
                                   contrapartida=contrapartida, descripcion=detalle, asiento_id=asiento.id,
                                   documento_ref=doc)


def registrar_pago(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, cuenta_banco: str, contrapartida: str,
    descripcion: str, beneficiario: str, *, autorizacion_id: int | None = None, nro_cheque: str | None = None,
    documento_ref: str | None = None, categoria_pago: str | None = None, compromiso_id: int | None = None,
    sufijo: str | None = None,
) -> int:
    """Pago bancario con la autorización que exige CI-FIN-09 (GG sobre el
    umbral; GG + miembro del CV sobre el de firma mancomunada).

    Raises:
        AutorizacionError: falta autorización o está incompleta.
        ReglaNegocioError: compromiso suspendido, excedentes (usar
            :func:`pagar_excedentes`) o saldo bancario insuficiente.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    importe = _monto_positivo(monto)
    if categoria_pago == "EXCEDENTES_SOCIOS" or contrapartida == EXCEDENTES_POR_PAGAR:
        raise ReglaNegocioError("Los excedentes se pagan con «pagar_excedentes», que verifica el SME (POL-04 Art. 2).",
                                codigo="USAR_PAGO_EXCEDENTES")
    if categoria_pago is not None and categoria_pago not in ETIQUETA_JERARQUIA:
        raise DatosInvalidosError(f"Categoría de pago desconocida: {categoria_pago!r}.")
    if contrapartida.startswith("1.1.01"):
        raise ReglaNegocioError("Para mover fondos entre bancos o hacia la caja use la transferencia o la reposición.",
                                codigo="CUENTA_NO_PERMITIDA")
    detalle = _texto(descripcion, "descripción")
    quien = _texto(beneficiario, "beneficiario", 120)
    with transaccion(con):
        compromiso = None
        if compromiso_id is not None:
            compromiso = con.execute("SELECT * FROM compromisos WHERE id = ?", (compromiso_id,)).fetchone()
            if compromiso is None:
                raise DatosInvalidosError(f"No existe el compromiso {compromiso_id}.")
            if compromiso["estado"] == "SUSPENDIDO":
                raise AutorizacionError("El compromiso está suspendido hasta la autorización escrita de la GG "
                                        "(POL-04 Art. 2).", codigo="COMPROMISO_SUSPENDIDO")
            if compromiso["estado"] not in ("VIGENTE", "AUTORIZADO"):
                raise ReglaNegocioError(f"El compromiso está {compromiso['estado']}.", codigo="COMPROMISO_NO_VIGENTE")
            if importe > de_centavos(int(compromiso["monto_cent"])):
                raise ReglaNegocioError("El pago supera el monto del compromiso.", codigo="PAGO_EXCEDE_COMPROMISO")
        _exigir_autorizacion_desembolso(con, f, importe, autorizacion_id)
        disponible = saldo_cuenta(con, cuenta_banco, f)
        if importe > disponible:
            raise ReglaNegocioError(f"Saldo insuficiente en {cuenta_banco}: disponible {formato_bs(disponible)}.",
                                    codigo="SALDO_INSUFICIENTE")
        asiento = contable.crear_asiento(
            con, sesion, f, detalle, [{"cuenta": contrapartida, "debe": importe, "sufijo": sufijo},
                                      {"cuenta": cuenta_banco, "haber": importe}],
            "TESORERIA", documento_ref or nro_cheque, tipo="EGRESO")
        mid = _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="PAGO", fecha=f, monto=importe,
                                  contrapartida=contrapartida, descripcion=detalle, asiento_id=asiento.id,
                                  beneficiario=quien, nro_cheque=_opcional(nro_cheque, "cheque", 30),
                                  documento_ref=_opcional(documento_ref, "documento", 60), categoria_pago=categoria_pago,
                                  autorizacion_id=autorizacion_id, compromiso_id=compromiso_id)
        if autorizacion_id is not None:
            seguridad.consumir_autorizacion(con, sesion, autorizacion_id, "movimientos_bancarios", mid,
                                            tipo_decision=TipoDecision.DESEMBOLSO, monto=importe)
        if compromiso is not None:
            con.execute("UPDATE compromisos SET estado = 'PAGADO', actualizado_en = ? WHERE id = ?",
                        (_ahora_iso(), compromiso_id))
            auditoria.registrar(con, sesion, "compromisos", "UPDATE", compromiso_id,
                                antes={"estado": compromiso["estado"]}, despues={"estado": "PAGADO", "pago_id": mid})
    return mid


def registrar_pago_compuesto(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, cuenta_banco: str, lineas: Sequence[tuple[str, Any]],
    descripcion: str, beneficiario: str, *, autorizacion_id: int | None = None, nro_cheque: str | None = None,
    documento_ref: str | None = None, categoria_pago: str | None = None,
) -> int:
    """Un solo desembolso que cancela varias cuentas (p. ej. aportes laborales
    2.1.02.05 y patronales 2.1.02.03 a la Gestora y la CNS), con la
    autorización CI-FIN-09 sobre el total. El movimiento bancario registra la
    primera cuenta como contrapartida y el asiento detalla todas.

    Raises:
        AutorizacionError: falta autorización o está incompleta.
        ReglaNegocioError: saldo insuficiente, cuenta no permitida o excedentes.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    if not lineas:
        raise DatosInvalidosError("Indique al menos una cuenta a cancelar.")
    if categoria_pago is not None and categoria_pago not in ETIQUETA_JERARQUIA:
        raise DatosInvalidosError(f"Categoría de pago desconocida: {categoria_pago!r}.")
    detalle_lineas: list[tuple[str, Decimal]] = []
    for cuenta, monto in lineas:
        if cuenta.startswith("1.1.01") or cuenta == EXCEDENTES_POR_PAGAR:
            raise ReglaNegocioError(f"La cuenta {cuenta} no se cancela con un pago compuesto.",
                                    codigo="CUENTA_NO_PERMITIDA")
        importe_linea = redondear_bs(monto)
        if importe_linea > 0:
            detalle_lineas.append((cuenta, importe_linea))
    if not detalle_lineas:
        raise DatosInvalidosError("El pago no tiene montos positivos.")
    importe = sum((m for _, m in detalle_lineas), CERO)
    detalle = _texto(descripcion, "descripción")
    quien = _texto(beneficiario, "beneficiario", 120)
    with transaccion(con):
        _exigir_autorizacion_desembolso(con, f, importe, autorizacion_id)
        disponible = saldo_cuenta(con, cuenta_banco, f)
        if importe > disponible:
            raise ReglaNegocioError(f"Saldo insuficiente en {cuenta_banco}: disponible {formato_bs(disponible)}.",
                                    codigo="SALDO_INSUFICIENTE")
        asiento = contable.crear_asiento(
            con, sesion, f, detalle, [*({"cuenta": c, "debe": m} for c, m in detalle_lineas),
                                      {"cuenta": cuenta_banco, "haber": importe}],
            "TESORERIA", documento_ref or nro_cheque, tipo="EGRESO")
        mid = _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="PAGO", fecha=f, monto=importe,
                                  contrapartida=detalle_lineas[0][0], descripcion=detalle, asiento_id=asiento.id,
                                  beneficiario=quien, nro_cheque=_opcional(nro_cheque, "cheque", 30),
                                  documento_ref=_opcional(documento_ref, "documento", 60), categoria_pago=categoria_pago,
                                  autorizacion_id=autorizacion_id)
        if autorizacion_id is not None:
            seguridad.consumir_autorizacion(con, sesion, autorizacion_id, "movimientos_bancarios", mid,
                                            tipo_decision=TipoDecision.DESEMBOLSO, monto=importe)
    return mid


def registrar_transferencia(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, origen: str, destino: str, descripcion: str,
) -> tuple[int, int]:
    """Transferencia entre las cuentas bancarias propias (no es un desembolso a terceros)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(origen)
    _validar_banco(destino)
    if origen == destino:
        raise DatosInvalidosError("La cuenta de origen y la de destino deben ser distintas.")
    importe = _monto_positivo(monto)
    detalle = _texto(descripcion, "descripción")
    with transaccion(con):
        asiento = contable.crear_asiento(
            con, sesion, f, detalle, [{"cuenta": destino, "debe": importe}, {"cuenta": origen, "haber": importe}],
            "TESORERIA", tipo="TRASPASO")
        salida = _insertar_mov_banco(con, sesion, cuenta=origen, tipo="TRANSFERENCIA_SALIDA", fecha=f, monto=importe,
                                     contrapartida=destino, descripcion=detalle, asiento_id=asiento.id)
        entrada = _insertar_mov_banco(con, sesion, cuenta=destino, tipo="TRANSFERENCIA_ENTRADA", fecha=f,
                                      monto=importe, contrapartida=origen, descripcion=detalle, asiento_id=asiento.id)
    return salida, entrada


def _registrar_ajuste_bancario(
    con: sqlite3.Connection, sesion: Sesion, fecha: date, monto: Decimal, cuenta_banco: str, tipo: str,
    descripcion: str, conciliacion_id: int | None,
) -> tuple[int, int]:
    """Cargo o abono bancario detectado en la conciliación (CI-FIN-07)."""
    p = _params(con, fecha)
    if tipo == "CARGO_BANCARIO":
        contrapartida = str(_param(p, "cuenta_cargos_bancarios"))
        lineas = [{"cuenta": contrapartida, "debe": monto}, {"cuenta": cuenta_banco, "haber": monto}]
        tipo_asiento = "EGRESO"
    else:
        contrapartida = str(_param(p, "cuenta_intereses_bancarios"))
        lineas = [{"cuenta": cuenta_banco, "debe": monto}, {"cuenta": contrapartida, "haber": monto}]
        tipo_asiento = "INGRESO"
    asiento = contable.crear_asiento(con, sesion, fecha, descripcion, lineas, "CONCILIACION",
                                     f"Conciliación {conciliacion_id}" if conciliacion_id else None, tipo=tipo_asiento)
    mid = _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo=tipo, fecha=fecha, monto=monto,
                              contrapartida=contrapartida, descripcion=descripcion, asiento_id=asiento.id)
    if conciliacion_id is not None:
        con.execute("UPDATE movimientos_bancarios SET conciliacion_id = ? WHERE id = ?", (conciliacion_id, mid))
    return mid, asiento.id


def movimientos_banco(
    con: sqlite3.Connection, sesion: Sesion, cuenta: str | None = None, desde: Any = None, hasta: Any = None,
) -> list[dict[str, Any]]:
    """Movimientos bancarios registrados por tesorería."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    condiciones, args = ["1 = 1"], []
    if cuenta:
        condiciones.append("cuenta_codigo = ?")
        args.append(_validar_banco(cuenta))
    if desde is not None:
        condiciones.append("fecha >= ?")
        args.append(a_fecha(desde).isoformat())
    if hasta is not None:
        condiciones.append("fecha <= ?")
        args.append(a_fecha(hasta).isoformat())
    return [{**dict(f), "monto": de_centavos(int(f["monto_cent"]))} for f in con.execute(
        f"SELECT * FROM movimientos_bancarios WHERE {' AND '.join(condiciones)} ORDER BY fecha, id", args)]


# ==========================================================================
# 3. COMPROMISOS Y SALDO MÍNIMO DE EFECTIVO (POL-04 Art. 2)
# ==========================================================================
@dataclass(frozen=True)
class EvaluacionCompromiso:
    """Resultado de proyectar un compromiso sobre el saldo bancario."""

    fecha: str
    tipo: str
    monto: Decimal
    saldo_bancario: Decimal
    compromisos_previos: Decimal
    saldo_proyectado: Decimal
    sme: Decimal
    umbral: Decimal
    cumplimiento: Decimal
    suspendido: bool
    requisito: RequisitoAutorizacion
    mensaje: str


def compromisos_pendientes(con: sqlite3.Connection) -> Decimal:
    """Compromisos vigentes o autorizados aún no pagados (los suspendidos no
    cuentan hasta que la GG los autorice)."""
    fila = con.execute("SELECT COALESCE(SUM(monto_cent), 0) FROM compromisos WHERE estado IN ('VIGENTE','AUTORIZADO')"
                       ).fetchone()
    return de_centavos(int(fila[0]))


def validar_compromiso(con: sqlite3.Connection, monto: Any, tipo: str, fecha: Any = None) -> EvaluacionCompromiso:
    """Proyecta el saldo bancario descontando los compromisos pendientes y el
    compromiso evaluado; si queda bajo el ``sme_umbral_compromiso`` (120 %)
    del SME vigente, el compromiso se suspende y exige autorización expresa y
    escrita de la Gerencia General (POL-04 Art. 2; PR-FIN-02).

    # DISCREPANCIA: el POA (§3.3) aplica el 120 % al saldo de inicio de ciclo
    # y deja al CA precisar la regla; POL-04 Art. 2 y PR-FIN-02 lo aplican al
    # saldo proyectado "una vez descontado el compromiso evaluado". Prevalece
    # la política de origen (CI-FIN-09, Cap. II): se evalúa el saldo bancario
    # vigente a la fecha menos los compromisos pendientes y el nuevo.

    Args:
        monto: importe del compromiso.
        tipo: ``COMPRA``, ``PERSONAL_EVENTUAL`` u ``OTRO`` (gasto no recurrente).
        fecha: fecha de evaluación (por defecto, hoy).
    """
    if tipo not in TIPO_DECISION_COMPROMISO:
        raise DatosInvalidosError(f"Tipo de compromiso no válido: {tipo!r}. Use {', '.join(TIPO_DECISION_COMPROMISO)}.")
    f = a_fecha(fecha) if fecha is not None else date.today()
    importe = _monto_positivo(monto)
    saldo = saldo_bancario(con, f)
    previos = compromisos_pendientes(con)
    proyectado = saldo - previos - importe
    p = _params(con, f)
    sme = sme_vigente(con, f)
    factor = Decimal(_param(p, "sme_umbral_compromiso"))
    umbral = redondear_bs(sme * factor)
    contexto = seguridad.contexto_desde_bd(con, f, saldo_proyectado=proyectado)
    requisito = seguridad.requiere_autorizacion(TipoDecision.COMPROMISO_GASTO, importe, contexto)
    suspendido = requisito.autorizacion_escrita
    cumplimiento = _pct(proyectado / sme) if sme else CERO
    if suspendido:
        mensaje = (f"Compromiso SUSPENDIDO: el saldo proyectado ({formato_bs(proyectado)}) queda bajo el "
                   f"{_pct(factor):f} % del SME ({formato_bs(umbral)}). Requiere autorización expresa y escrita de la "
                   "Gerencia General (POL-04 Art. 2).")
    else:
        mensaje = (f"Compromiso procedente: el saldo proyectado ({formato_bs(proyectado)}) supera el umbral de "
                   f"{formato_bs(umbral)}.")
    return EvaluacionCompromiso(fecha=f.isoformat(), tipo=tipo, monto=importe, saldo_bancario=saldo,
                                compromisos_previos=previos, saldo_proyectado=proyectado, sme=sme, umbral=umbral,
                                cumplimiento=cumplimiento, suspendido=suspendido, requisito=requisito, mensaje=mensaje)


def registrar_compromiso(
    con: sqlite3.Connection, sesion: Sesion, monto: Any, tipo: str, descripcion: str, fecha: Any = None,
) -> tuple[int, EvaluacionCompromiso, int | None]:
    """Registra un compromiso de gasto no recurrente. Si queda suspendido,
    crea la solicitud de autorización escrita para la GG.

    Returns:
        ``(compromiso_id, evaluación, autorizacion_id o None)``.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    detalle = _texto(descripcion, "descripción")
    evaluacion = validar_compromiso(con, monto, tipo, fecha)
    f = a_fecha(evaluacion.fecha)
    with transaccion(con):
        autorizacion_id = None
        if evaluacion.suspendido:
            contexto = seguridad.contexto_desde_bd(con, f, saldo_proyectado=evaluacion.saldo_proyectado)
            autorizacion_id = seguridad.solicitar_autorizacion(
                con, sesion, TipoDecision.COMPROMISO_GASTO, evaluacion.monto,
                f"Compromiso suspendido por SME: {detalle}", contexto, referencia=f"Compromiso {tipo}")
        cur = con.execute(
            """INSERT INTO compromisos (tipo, fecha, monto_cent, descripcion, saldo_bancario_cent,
                   compromisos_previos_cent, saldo_proyectado_cent, sme_cent, umbral_cent, estado, autorizacion_id,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tipo, f.isoformat(), a_centavos(evaluacion.monto), detalle, a_centavos(evaluacion.saldo_bancario),
             a_centavos(evaluacion.compromisos_previos), a_centavos(evaluacion.saldo_proyectado),
             a_centavos(evaluacion.sme), a_centavos(evaluacion.umbral),
             "SUSPENDIDO" if evaluacion.suspendido else "VIGENTE", autorizacion_id, sesion.nombre_usuario,
             _ahora_iso()))
        cid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "compromisos", "INSERT", cid,
                            despues={"tipo": tipo, "monto": evaluacion.monto,
                                     "saldo_proyectado": evaluacion.saldo_proyectado, "umbral": evaluacion.umbral,
                                     "estado": "SUSPENDIDO" if evaluacion.suspendido else "VIGENTE",
                                     "autorizacion_id": autorizacion_id}, detalle=evaluacion.mensaje)
    return cid, evaluacion, autorizacion_id


def confirmar_compromiso(con: sqlite3.Connection, sesion: Sesion, compromiso_id: int) -> str:
    """Levanta la suspensión cuando la GG firmó la autorización escrita."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    with transaccion(con):
        fila = con.execute("SELECT * FROM compromisos WHERE id = ?", (compromiso_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe el compromiso {compromiso_id}.")
        if fila["estado"] != "SUSPENDIDO":
            raise ReglaNegocioError(f"El compromiso está {fila['estado']}, no suspendido.", codigo="COMPROMISO_NO_SUSPENDIDO")
        monto = de_centavos(int(fila["monto_cent"]))
        seguridad.consumir_autorizacion(con, sesion, int(fila["autorizacion_id"]), "compromisos", compromiso_id,
                                        tipo_decision=TipoDecision.COMPROMISO_GASTO, monto=monto)
        con.execute("UPDATE compromisos SET estado = 'AUTORIZADO', actualizado_en = ? WHERE id = ?",
                    (_ahora_iso(), compromiso_id))
        auditoria.registrar(con, sesion, "compromisos", "UPDATE", compromiso_id,
                            antes={"estado": "SUSPENDIDO"}, despues={"estado": "AUTORIZADO"})
    return "AUTORIZADO"


def anular_compromiso(con: sqlite3.Connection, sesion: Sesion, compromiso_id: int, motivo: str) -> None:
    """Anula un compromiso no pagado (libera el saldo proyectado)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "EDITAR")
    causa = _texto(motivo, "motivo")
    with transaccion(con):
        fila = con.execute("SELECT * FROM compromisos WHERE id = ?", (compromiso_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe el compromiso {compromiso_id}.")
        if fila["estado"] in ("PAGADO", "ANULADO"):
            raise ReglaNegocioError(f"El compromiso ya está {fila['estado']}.", codigo="COMPROMISO_NO_VIGENTE")
        con.execute("UPDATE compromisos SET estado = 'ANULADO', actualizado_en = ? WHERE id = ?",
                    (_ahora_iso(), compromiso_id))
        auditoria.registrar(con, sesion, "compromisos", "UPDATE", compromiso_id, antes={"estado": fila["estado"]},
                            despues={"estado": "ANULADO"}, detalle=causa)


def listar_compromisos(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    sql, args = "SELECT * FROM compromisos", ()
    if estado:
        sql, args = sql + " WHERE estado = ?", (estado,)
    return [{**dict(f), **{k.replace("_cent", ""): de_centavos(int(f[k])) for k in f.keys() if k.endswith("_cent")}}
            for f in con.execute(sql + " ORDER BY id DESC", args)]


# ==========================================================================
# 4. JERARQUÍA DE PAGOS (POL-04 Art. 2)
# ==========================================================================
@dataclass(frozen=True)
class PagoPendiente:
    """Un pago del lote a priorizar."""

    referencia: str
    descripcion: str
    categoria: str
    monto: Decimal
    vencimiento: date | None = None
    beneficiario: str = ""


def jerarquia_vigente(con: sqlite3.Connection, fecha: Any) -> list[str]:
    """Categorías de pago en orden de prioridad (parámetro ``jerarquia_pagos``)."""
    orden = [c.strip() for c in str(_param(_params(con, fecha), "jerarquia_pagos")).split(",") if c.strip()]
    desconocidas = [c for c in orden if c not in ETIQUETA_JERARQUIA]
    if desconocidas or len(orden) != len(set(orden)):
        raise ReglaNegocioError(f"El parámetro jerarquia_pagos es inválido: {', '.join(desconocidas) or 'duplicados'}.",
                                codigo="PARAMETRO_INVALIDO")
    return orden


def ordenar_pagos(
    con: sqlite3.Connection, pagos: Sequence[PagoPendiente], fecha: Any = None, saldo_disponible: Any = None,
) -> dict[str, Any]:
    """Ordena un lote de pagos según la jerarquía de 7 niveles y determina
    cuáles pueden pagarse con el saldo disponible.

    * Se respeta el orden estricto: ningún pago de menor prioridad se marca
      pagable mientras uno de mayor prioridad quede sin cubrir (alterar el
      orden exige justificación de la GG ante el CV, POL-04 Art. 2).
    * Los excedentes a socios además exigen que el saldo posterior no quede
      bajo el SME.
    """
    f = a_fecha(fecha) if fecha is not None else date.today()
    orden = jerarquia_vigente(con, f)
    saldo = redondear_bs(saldo_disponible) if saldo_disponible is not None else saldo_bancario(con, f)
    sme = sme_vigente(con, f)
    for p in pagos:
        if p.categoria not in orden:
            raise DatosInvalidosError(f"Categoría de pago desconocida: {p.categoria!r}.")
        if redondear_bs(p.monto) <= 0:
            raise DatosInvalidosError(f"El pago {p.referencia} debe tener monto positivo.")
    clasificados = sorted(pagos, key=lambda p: (orden.index(p.categoria), p.vencimiento or date.max))
    restante = saldo
    bloqueado = False
    filas = []
    for p in clasificados:
        monto = redondear_bs(p.monto)
        nivel = orden.index(p.categoria) + 1
        motivo = ""
        pagable = False
        if bloqueado:
            motivo = "Pendiente: hay un pago de mayor prioridad sin cubrir."
        elif monto > restante:
            motivo = f"Saldo insuficiente ({formato_bs(restante)})."
            bloqueado = True
        elif p.categoria == "EXCEDENTES_SOCIOS" and restante - monto < sme:
            motivo = f"Bloqueado: el saldo posterior quedaría bajo el SME ({formato_bs(sme)})."
            bloqueado = True
        else:
            pagable = True
            restante -= monto
        filas.append({"nivel": nivel, "categoria": p.categoria, "nombre_nivel": ETIQUETA_JERARQUIA[p.categoria],
                      "referencia": p.referencia, "descripcion": p.descripcion, "beneficiario": p.beneficiario,
                      "monto": monto, "vencimiento": p.vencimiento.isoformat() if p.vencimiento else None,
                      "pagable": pagable, "saldo_despues": restante if pagable else None, "motivo": motivo})
    total = sum((x["monto"] for x in filas if x["pagable"]), CERO)
    return {"fecha": f.isoformat(), "saldo_inicial": saldo, "sme": sme, "pagos": filas, "total_pagable": total,
            "saldo_final": restante}


# ==========================================================================
# 5. EXCEDENTES (POL-04 Art. 2; CI-FIN-02 Arts. 9, 10 y 12)
# ==========================================================================
def verificar_pago_excedentes(con: sqlite3.Connection, fecha: Any, monto: Any) -> dict[str, Any]:
    """¿El saldo bancario posterior al pago queda igual o sobre el SME?"""
    f = a_fecha(fecha)
    importe = _monto_positivo(monto)
    saldo = saldo_bancario(con, f)
    posterior = saldo - importe
    sme = sme_vigente(con, f)
    return {"fecha": f.isoformat(), "monto": importe, "saldo_actual": saldo, "saldo_posterior": posterior,
            "sme": sme, "permitido": posterior >= sme}


def pagar_excedentes(
    con: sqlite3.Connection, sesion: Sesion, fecha: Any, monto: Any, cuenta_banco: str, *,
    autorizacion_asamblea_id: int, autorizacion_desembolso_id: int, medio: str = "TRANSFERENCIA",
    documento_ref: str | None = None,
) -> int:
    """Paga los excedentes aprobados por la Asamblea (2.1.05.01 contra banco).

    Raises:
        ReglaNegocioError: ``PAGO_EXCEDENTES_BAJO_SME`` si el saldo posterior
            queda bajo el SME; efectivo por socio sobre el máximo.
        AutorizacionError: falta la aprobación de la Asamblea o la firma del
            desembolso (mancomunada si corresponde).
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    _validar_banco(cuenta_banco)
    if medio not in ("TRANSFERENCIA", "CHEQUE", "EFECTIVO"):
        raise DatosInvalidosError("El medio de pago debe ser TRANSFERENCIA, CHEQUE o EFECTIVO.")
    importe = _monto_positivo(monto)
    with transaccion(con):
        control = verificar_pago_excedentes(con, f, importe)
        if not control["permitido"]:
            raise ReglaNegocioError(
                f"Pago de excedentes BLOQUEADO: el saldo bancario posterior ({formato_bs(control['saldo_posterior'])}) "
                f"quedaría bajo el SME ({formato_bs(control['sme'])}). POL-04 Art. 2 y CI-FIN-02 Art. 9: se posterga "
                "por un ciclo y se informa a los socios.", codigo="PAGO_EXCEDENTES_BAJO_SME", detalle={
                    k: str(v) for k, v in control.items()})
        if medio == "EFECTIVO":
            socios = int(con.execute("SELECT COUNT(*) FROM socios WHERE estado = 'ACTIVO'").fetchone()[0])
            por_socio = redondear_bs(importe / socios)
            limite = redondear_bs(_param(_params(con, f), "pago_efectivo_excedente_max_bs"))
            if por_socio > limite:
                raise ReglaNegocioError(f"CI-FIN-02 Art. 10: el pago en efectivo por socio ({formato_bs(por_socio)}) "
                                        f"supera {formato_bs(limite)}: use transferencia o cheque.",
                                        codigo="EFECTIVO_EXCEDE_LIMITE")
        seguridad.verificar_autorizacion(con, autorizacion_asamblea_id,
                                         tipo_decision=TipoDecision.DISTRIBUCION_EXCEDENTES, monto=importe)
        _exigir_autorizacion_desembolso(con, f, importe, autorizacion_desembolso_id)
        asiento = contable.crear_asiento(
            con, sesion, f, "Pago de excedentes a socios del ejercicio anterior",
            [{"cuenta": EXCEDENTES_POR_PAGAR, "debe": importe}, {"cuenta": cuenta_banco, "haber": importe}],
            "TESORERIA", documento_ref, tipo="EGRESO")
        mid = _insertar_mov_banco(con, sesion, cuenta=cuenta_banco, tipo="PAGO", fecha=f, monto=importe,
                                  contrapartida=EXCEDENTES_POR_PAGAR, descripcion=f"Pago de excedentes ({medio.lower()})",
                                  asiento_id=asiento.id, beneficiario="Socios", documento_ref=documento_ref,
                                  categoria_pago="EXCEDENTES_SOCIOS", autorizacion_id=autorizacion_desembolso_id)
        seguridad.consumir_autorizacion(con, sesion, autorizacion_asamblea_id, "movimientos_bancarios", mid,
                                        tipo_decision=TipoDecision.DISTRIBUCION_EXCEDENTES, monto=importe)
        seguridad.consumir_autorizacion(con, sesion, autorizacion_desembolso_id, "movimientos_bancarios", mid,
                                        tipo_decision=TipoDecision.DESEMBOLSO, monto=importe)
    return mid


# ==========================================================================
# 6. FLUJO DE CAJA PROYECTADO (FIN-TES-05), ALERTAS (07) Y MEDIDAS (08)
# ==========================================================================
@dataclass(frozen=True)
class LineaFlujo:
    """Movimiento proyectado de efectivo (monto siempre positivo)."""

    fecha: date
    categoria: str
    descripcion: str
    monto: Decimal


def _limites_ciclos(con: sqlite3.Connection, inicio: date) -> tuple[date, date]:
    p = _params(con, inicio)
    dias = int(_param(p, "dias_ciclo"))
    inclusivo = bool(_param(p, "ciclo_cierre_inclusivo"))
    fin_actual = fecha_cierre_ciclo(inicio, dias, inclusivo)
    siguiente = fecha_inicio_ciclo_siguiente(inicio, dias, inclusivo)
    return fin_actual, fecha_cierre_ciclo(siguiente, dias, inclusivo)


def crear_flujo(
    con: sqlite3.Connection, sesion: Sesion, fecha_elaboracion: Any, inicio_ciclo: Any, saldo_inicial: Any,
    lineas: Iterable[LineaFlujo | Mapping[str, Any]], descripcion: str,
) -> int:
    """Registra el flujo de caja proyectado con horizonte de dos ciclos
    (FIN-TES-05, POL-04 Art. 4). Las fechas de cierre salen de ``dias_ciclo``
    y del criterio de cierre inclusivo del POA."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    elaboracion = a_fecha(fecha_elaboracion)
    inicio = a_fecha(inicio_ciclo)
    saldo = redondear_bs(saldo_inicial)
    texto = _texto(descripcion, "descripción", 200)
    fin_actual, fin_siguiente = _limites_ciclos(con, inicio)
    normalizadas: list[LineaFlujo] = []
    for n, l in enumerate(lineas, start=1):
        datos = l.__dict__ if isinstance(l, LineaFlujo) else l
        fecha = a_fecha(datos.get("fecha"), f"fecha de la línea {n}")
        categoria = datos.get("categoria")
        if categoria not in CATEGORIAS_FLUJO:
            raise DatosInvalidosError(f"Línea {n}: categoría no válida {categoria!r}.")
        if fecha < elaboracion or fecha > fin_siguiente:
            raise DatosInvalidosError(f"Línea {n}: la fecha {fecha_pantalla(fecha)} está fuera del horizonte "
                                      f"({fecha_pantalla(elaboracion)} al {fecha_pantalla(fin_siguiente)}).")
        normalizadas.append(LineaFlujo(fecha, categoria, _texto(datos.get("descripcion"), f"descripción línea {n}"),
                                       _monto_positivo(datos.get("monto"), f"monto de la línea {n}")))
    if not normalizadas:
        raise DatosInvalidosError("El flujo proyectado necesita al menos una línea.")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO flujos_proyectados (ejercicio, fecha_elaboracion, inicio_ciclo, fin_ciclo_actual,
                   fin_ciclo_siguiente, saldo_inicial_cent, descripcion, elaborado_por)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ejercicio_de_fecha_bd(con, inicio), elaboracion.isoformat(), inicio.isoformat(), fin_actual.isoformat(),
             fin_siguiente.isoformat(), a_centavos(saldo), texto, sesion.nombre_usuario))
        fid = int(cur.lastrowid)
        con.executemany(
            "INSERT INTO flujo_lineas (flujo_id, fecha, categoria, descripcion, monto_cent) VALUES (?,?,?,?,?)",
            [(fid, l.fecha.isoformat(), l.categoria, l.descripcion, a_centavos(l.monto)) for l in normalizadas])
        auditoria.registrar(con, sesion, "flujos_proyectados", "INSERT", fid,
                            despues={"inicio_ciclo": inicio.isoformat(), "fin_ciclo_actual": fin_actual.isoformat(),
                                     "fin_ciclo_siguiente": fin_siguiente.isoformat(), "saldo_inicial": saldo,
                                     "lineas": len(normalizadas)})
    return fid


def _signo_categoria(categoria: str) -> int:
    return 1 if categoria == "INGRESO_VENTAS" else -1


def evaluar_flujo(con: sqlite3.Connection, flujo_id: int) -> dict[str, Any]:
    """Saldo diario proyectado, mínimo diario y su fecha, totales FIN-TES-05
    por ciclo, cumplimiento del SME y margen de seguridad (0,5 ciclos)."""
    cab = con.execute("SELECT * FROM flujos_proyectados WHERE id = ?", (flujo_id,)).fetchone()
    if cab is None:
        raise DatosInvalidosError(f"No existe el flujo proyectado {flujo_id}.")
    lineas = con.execute("SELECT * FROM flujo_lineas WHERE flujo_id = ? ORDER BY fecha, id", (flujo_id,)).fetchall()
    elaboracion = date.fromisoformat(cab["fecha_elaboracion"])
    fin_actual = date.fromisoformat(cab["fin_ciclo_actual"])
    fin_siguiente = date.fromisoformat(cab["fin_ciclo_siguiente"])
    por_dia: dict[date, int] = {}
    totales = {"ACTUAL": {k: 0 for k in CATEGORIAS_FLUJO}, "SIGUIENTE": {k: 0 for k in CATEGORIAS_FLUJO}}
    for l in lineas:
        d = date.fromisoformat(l["fecha"])
        por_dia[d] = por_dia.get(d, 0) + _signo_categoria(l["categoria"]) * int(l["monto_cent"])
        totales["ACTUAL" if d <= fin_actual else "SIGUIENTE"][l["categoria"]] += int(l["monto_cent"])
    sme_cache: dict[int, Decimal] = {}

    def sme_de(d: date) -> Decimal:
        ej = ejercicio_de_fecha_bd(con, d)
        if ej not in sme_cache:
            sme_cache[ej] = sme_vigente(con, d)
        return sme_cache[ej]

    saldo = int(cab["saldo_inicial_cent"])
    serie = []
    minimo, fecha_minimo = None, None
    dias_bajo_sme = 0
    d = elaboracion
    while d <= fin_siguiente:
        saldo += por_dia.get(d, 0)
        sme_d = sme_de(d)
        serie.append({"fecha": d.isoformat(), "saldo": de_centavos(saldo), "sme": sme_d})
        if de_centavos(saldo) < sme_d:
            dias_bajo_sme += 1
        if minimo is None or saldo < minimo:
            minimo, fecha_minimo = saldo, d
        d += timedelta(days=1)
    saldo_fin = {x["fecha"]: x["saldo"] for x in serie}
    p = _params(con, fin_actual)
    margen = Decimal(_param(p, "sme_margen_seguridad_ciclos"))
    ciclos = {}
    for clave, fin in (("ACTUAL", fin_actual), ("SIGUIENTE", fin_siguiente)):
        sme_c = sme_de(fin)
        final = saldo_fin[fin.isoformat()]
        ciclos[clave] = {
            "fin": fin.isoformat(),
            **{cat: de_centavos(v) for cat, v in totales[clave].items()},
            "saldo_final": final, "sme": sme_c, "cumplimiento": _pct(final / sme_c),
            "margen_seguridad_cumplido": final >= redondear_bs(sme_c * (1 + margen)),
        }
    sme_min = sme_de(fecha_minimo)
    return {
        "flujo_id": flujo_id, "descripcion": cab["descripcion"], "estado": cab["estado"],
        "fecha_elaboracion": cab["fecha_elaboracion"], "inicio_ciclo": cab["inicio_ciclo"],
        "saldo_inicial": de_centavos(int(cab["saldo_inicial_cent"])), "serie": serie, "ciclos": ciclos,
        "saldo_minimo": de_centavos(minimo), "fecha_minimo": fecha_minimo.isoformat(), "sme_en_minimo": sme_min,
        "riesgo_bajo_sme": de_centavos(minimo) < sme_min, "dias_bajo_sme": dias_bajo_sme,
        "elaborado_por": cab["elaborado_por"], "aprobado_por": cab["aprobado_por"],
    }


def saldo_proyectado_en(con: sqlite3.Connection, flujo_id: int, fecha: Any) -> Decimal:
    """Saldo proyectado por el flujo al cierre de una fecha del horizonte."""
    f = a_fecha(fecha).isoformat()
    for punto in evaluar_flujo(con, flujo_id)["serie"]:
        if punto["fecha"] == f:
            return punto["saldo"]
    raise DatosInvalidosError(f"La fecha {fecha_pantalla(f)} está fuera del horizonte del flujo.")


def aprobar_flujo(con: sqlite3.Connection, sesion: Sesion, flujo_id: int) -> None:
    """La Gerencia General aprueba el flujo proyectado (PR-FIN-02, paso 2)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "AUTORIZAR")
    with transaccion(con):
        cab = con.execute("SELECT * FROM flujos_proyectados WHERE id = ?", (flujo_id,)).fetchone()
        if cab is None:
            raise DatosInvalidosError(f"No existe el flujo proyectado {flujo_id}.")
        if cab["estado"] == "APROBADO":
            raise ReglaNegocioError("El flujo ya está aprobado.", codigo="FLUJO_YA_APROBADO")
        con.execute("UPDATE flujos_proyectados SET estado = 'APROBADO', aprobado_por = ?, aprobado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), flujo_id))
        auditoria.registrar(con, sesion, "flujos_proyectados", "AUTORIZACION", flujo_id,
                            antes={"estado": "BORRADOR"}, despues={"estado": "APROBADO"})


def _insertar_alerta(
    con: sqlite3.Connection, sesion: Sesion, *, fecha: date, origen: str, descripcion: str, riesgo: bool,
    flujo_id: int | None = None, porcentaje: Decimal | None = None, punto: str | None = None,
    medida: str | None = None,
) -> int:
    cur = con.execute(
        """INSERT INTO alertas_liquidez (fecha, origen, flujo_id, descripcion, porcentaje_desvio, punto_horizonte,
               riesgo_bajo_sme, medida_sugerida, emitida_por, emitida_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (fecha.isoformat(), origen, flujo_id, descripcion, None if porcentaje is None else str(porcentaje), punto,
         1 if riesgo else 0, medida, sesion.nombre_usuario, _ahora_iso()))
    aid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "alertas_liquidez", "INSERT", aid,
                        despues={"origen": origen, "descripcion": descripcion, "riesgo_bajo_sme": riesgo,
                                 "porcentaje": porcentaje, "punto": punto})
    return aid


def emitir_alerta_flujo(con: sqlite3.Connection, sesion: Sesion, flujo_id: int, fecha: Any = None) -> int | None:
    """Emite FIN-TES-07 si el flujo proyecta caer bajo el SME en algún punto
    del horizonte. Devuelve el id de la alerta o ``None`` si no corresponde."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    ev = evaluar_flujo(con, flujo_id)
    if not ev["riesgo_bajo_sme"]:
        return None
    f = a_fecha(fecha) if fecha is not None else date.fromisoformat(ev["fecha_elaboracion"])
    faltante = _pct((ev["sme_en_minimo"] - ev["saldo_minimo"]) / ev["sme_en_minimo"])
    with transaccion(con):
        existente = con.execute("SELECT id FROM alertas_liquidez WHERE flujo_id = ? AND origen = 'FLUJO_SME'",
                                (flujo_id,)).fetchone()
        if existente:
            return int(existente[0])
        return _insertar_alerta(
            con, sesion, fecha=f, origen="FLUJO_SME", flujo_id=flujo_id, riesgo=True, porcentaje=faltante,
            punto=ev["fecha_minimo"],
            descripcion=(f"Saldo mínimo diario proyectado de {formato_bs(ev['saldo_minimo'])} el "
                         f"{fecha_pantalla(ev['fecha_minimo'])}, bajo el SME de {formato_bs(ev['sme_en_minimo'])} "
                         f"({ev['dias_bajo_sme']} días del horizonte bajo el SME)."),
            medida="Evaluar las medidas de POL-04 Art. 4 (FIN-TES-08) antes del punto crítico.")


def evaluar_desvio(
    con: sqlite3.Connection, sesion: Sesion, flujo_id: int, fecha_corte: Any, saldo_real: Any = None,
    emitir: bool = True,
) -> dict[str, Any]:
    """Compara el saldo real (libros) con el proyectado a una fecha; si el
    desvío supera ``desvio_flujo_caja_alerta`` (10 %) emite FIN-TES-07."""
    f = a_fecha(fecha_corte)
    proyectado = saldo_proyectado_en(con, flujo_id, f)
    real = redondear_bs(saldo_real) if saldo_real is not None else saldo_bancario(con, f)
    if proyectado == 0:
        raise ReglaNegocioError("El saldo proyectado es cero: no se puede medir el desvío porcentual.",
                                codigo="DESVIO_INDEFINIDO")
    desvio = (real - proyectado) / abs(proyectado)
    limite = Decimal(_param(_params(con, f), "desvio_flujo_caja_alerta"))
    sme = sme_vigente(con, f)
    resultado = {"fecha": f.isoformat(), "saldo_real": real, "saldo_proyectado": proyectado,
                 "desvio": _pct(desvio), "limite": _pct(limite), "alerta": abs(desvio) > limite,
                 "riesgo_bajo_sme": real < sme, "alerta_id": None}
    if resultado["alerta"] and emitir:
        seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
        with transaccion(con):
            resultado["alerta_id"] = _insertar_alerta(
                con, sesion, fecha=f, origen="DESVIO_FLUJO", flujo_id=flujo_id, riesgo=resultado["riesgo_bajo_sme"],
                porcentaje=resultado["desvio"], punto=f.isoformat(),
                descripcion=(f"Saldo real {formato_bs(real)} frente a {formato_bs(proyectado)} proyectado "
                             f"({resultado['desvio']} %), supera el ±{resultado['limite']} % (POL-04 III)."),
                medida="Revisar cobranzas y egresos del período; ver medidas de FIN-TES-08.")
    return resultado


def recibir_alerta(con: sqlite3.Connection, sesion: Sesion, alerta_id: int) -> None:
    """La GG deja constancia de haber recibido la alerta (FIN-TES-07)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "AUTORIZAR")
    with transaccion(con):
        fila = con.execute("SELECT * FROM alertas_liquidez WHERE id = ?", (alerta_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la alerta {alerta_id}.")
        if fila["recibida_por"]:
            raise ReglaNegocioError("La alerta ya fue recibida.", codigo="YA_REGISTRADO")
        con.execute("UPDATE alertas_liquidez SET recibida_por = ?, recibida_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), alerta_id))
        auditoria.registrar(con, sesion, "alertas_liquidez", "UPDATE", alerta_id,
                            despues={"recibida_por": sesion.nombre_usuario})


def registrar_medida_correctiva(
    con: sqlite3.Connection, sesion: Sesion, alerta_id: int, medidas: Iterable[str], fecha_aplicacion: Any,
    resultado: str | None = None,
) -> int:
    """La GG registra la medida adoptada ante la alerta (FIN-TES-08, POL-04 Art. 4)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "AUTORIZAR")
    elegidas = set(medidas)
    desconocidas = elegidas - set(MEDIDAS_CORRECTIVAS)
    if desconocidas or not elegidas:
        raise DatosInvalidosError("Elija al menos una medida de POL-04 Art. 4: " + ", ".join(MEDIDAS_CORRECTIVAS))
    f = a_fecha(fecha_aplicacion)
    with transaccion(con):
        alerta = con.execute("SELECT * FROM alertas_liquidez WHERE id = ?", (alerta_id,)).fetchone()
        if alerta is None:
            raise DatosInvalidosError(f"No existe la alerta {alerta_id}.")
        if alerta["estado"] == "ATENDIDA":
            raise ReglaNegocioError("La alerta ya tiene medida correctiva.", codigo="YA_REGISTRADO")
        cur = con.execute(
            f"""INSERT INTO medidas_correctivas (alerta_id, {', '.join(MEDIDAS_CORRECTIVAS)}, fecha_aplicacion,
                    resultado, adoptada_por, adoptada_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alerta_id, *[1 if m in elegidas else 0 for m in MEDIDAS_CORRECTIVAS], f.isoformat(),
             _opcional(resultado, "resultado", 500), sesion.nombre_usuario, _ahora_iso()))
        mid = int(cur.lastrowid)
        con.execute("UPDATE alertas_liquidez SET estado = 'ATENDIDA', recibida_por = COALESCE(recibida_por, ?), "
                    "recibida_en = COALESCE(recibida_en, ?) WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), alerta_id))
        auditoria.registrar(con, sesion, "medidas_correctivas", "INSERT", mid,
                            despues={"alerta_id": alerta_id, "medidas": sorted(elegidas), "fecha": f.isoformat()})
    return mid


def fiscalizar_medida(con: sqlite3.Connection, sesion: Sesion, medida_id: int) -> None:
    """El CV fiscaliza la medida correctiva (FIN-TES-08, firma del CV)."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VERIFICAR")
    with transaccion(con):
        fila = con.execute("SELECT * FROM medidas_correctivas WHERE id = ?", (medida_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la medida {medida_id}.")
        if fila["fiscalizada_por"]:
            raise ReglaNegocioError("La medida ya fue fiscalizada.", codigo="YA_REGISTRADO")
        con.execute("UPDATE medidas_correctivas SET fiscalizada_por = ?, fiscalizada_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), medida_id))
        auditoria.registrar(con, sesion, "medidas_correctivas", "UPDATE", medida_id,
                            despues={"fiscalizada_por": sesion.nombre_usuario})


def listar_alertas(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    sql = """SELECT a.*, m.id AS medida_id, m.acelerar_cobranza, m.postergar_capex, m.negociar_proveedores,
                    m.informar_asamblea, m.fecha_aplicacion, m.resultado, m.adoptada_por, m.fiscalizada_por
               FROM alertas_liquidez a LEFT JOIN medidas_correctivas m ON m.alerta_id = a.id"""
    args: tuple[Any, ...] = ()
    if estado:
        sql, args = sql + " WHERE a.estado = ?", (estado,)
    return [dict(f) for f in con.execute(sql + " ORDER BY a.id DESC", args)]


# ==========================================================================
# 7. REPORTE DIARIO DE SALDO (FIN-TES-06)
# ==========================================================================
def semaforo_sme(con: sqlite3.Connection, fecha: Any, cumplimiento: Decimal) -> str:
    """MALO < ``sme_cumplimiento_malo``; BUENO > ``sme_cumplimiento_bueno``; NORMAL en el rango."""
    p = _params(con, fecha)
    malo = Decimal(_param(p, "sme_cumplimiento_malo"))
    bueno = Decimal(_param(p, "sme_cumplimiento_bueno"))
    if cumplimiento < malo:
        return "MALO"
    if cumplimiento > bueno:
        return "BUENO"
    return "NORMAL"


def reporte_diario(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    """Saldo bancario del día frente al SME vigente, con semáforo."""
    f = a_fecha(fecha)
    saldo = saldo_bancario(con, f)
    sme = sme_vigente(con, f)
    cumplimiento = saldo / sme
    semaforo = semaforo_sme(con, f, cumplimiento)
    return {"fecha": f.isoformat(), "saldo": saldo, "sme": sme, "cumplimiento": _pct(cumplimiento),
            "semaforo": semaforo, "requiere_alerta": semaforo == "MALO"}


def registrar_reporte_diario(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """La GG deja constancia del monitoreo diario (PR-FIN-02, paso 4). Si el
    semáforo está en rojo se emite la alerta FIN-TES-07 correspondiente."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "AUTORIZAR")
    datos = reporte_diario(con, fecha)
    f = a_fecha(fecha)
    with transaccion(con):
        if con.execute("SELECT 1 FROM reportes_saldo_diario WHERE fecha = ?", (f.isoformat(),)).fetchone():
            raise ReglaNegocioError(f"Ya existe el reporte del {fecha_pantalla(f)}.", codigo="YA_REGISTRADO")
        alerta_id = None
        if datos["requiere_alerta"]:
            alerta_id = _insertar_alerta(
                con, sesion, fecha=f, origen="REPORTE_DIARIO", riesgo=True,
                porcentaje=Decimal(100) - datos["cumplimiento"], punto=f.isoformat(),
                descripcion=(f"Saldo bancario de {formato_bs(datos['saldo'])} = {datos['cumplimiento']} % del SME "
                             f"({formato_bs(datos['sme'])}): semáforo rojo."),
                medida="Activar las medidas de POL-04 Art. 4 e informar al Consejo de Vigilancia.")
        cur = con.execute(
            """INSERT INTO reportes_saldo_diario (fecha, saldo_cent, sme_cent, cumplimiento, semaforo, requiere_alerta,
                   alerta_id, monitoreado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), a_centavos(datos["saldo"]), a_centavos(datos["sme"]), str(datos["cumplimiento"]),
             datos["semaforo"], 1 if datos["requiere_alerta"] else 0, alerta_id, sesion.nombre_usuario, _ahora_iso()))
        auditoria.registrar(con, sesion, "reportes_saldo_diario", "INSERT", cur.lastrowid, despues=datos)
    return {**datos, "id": int(cur.lastrowid), "alerta_id": alerta_id}


def serie_saldo_diario(con: sqlite3.Connection, desde: Any, hasta: Any) -> list[dict[str, Any]]:
    """Reporte diario calculado para cada día de un rango (para FIN-TES-06)."""
    d, h = a_fecha(desde), a_fecha(hasta)
    if d > h:
        raise DatosInvalidosError("La fecha inicial no puede ser posterior a la final.")
    if (h - d).days > 400:
        raise DatosInvalidosError("El rango del reporte diario no puede superar 400 días.")
    resultado = []
    while d <= h:
        resultado.append(reporte_diario(con, d))
        d += timedelta(days=1)
    return resultado


# ==========================================================================
# 8. CONCILIACIÓN BANCARIA (FIN-INF-02, CI-FIN-07)
# ==========================================================================
@dataclass(frozen=True)
class PartidaConciliacion:
    """Partida de conciliación. ``monto`` siempre positivo; el tipo define el signo."""

    tipo: str
    descripcion: str
    fecha: date
    monto: Decimal
    movimiento_id: int | None = None


def calcular_conciliacion(
    saldo_libros: Decimal, saldo_extracto: Decimal, partidas: Sequence[PartidaConciliacion],
) -> dict[str, Any]:
    """Conciliación de doble saldo hacia un saldo conciliado común.

    Lado libros: saldo − cargos no registrados + abonos no registrados ±
    correcciones de libros. Lado banco: saldo del extracto + depósitos en
    tránsito − cheques en circulación.

    # DISCREPANCIA: el formato FIN-INF-02 rotula "(+) Cheques en circulación"
    # sobre el saldo del extracto, y el ejemplo de CI-FIN-07 (libros 45.000;
    # extracto 44.200; cheque 1.200; cargo 400) no llega a un saldo común
    # (45.000 − 400 = 44.600 frente a 44.200 + 1.200 = 45.400). Se aplica la
    # técnica estándar: el cheque registrado en libros y no cobrado se RESTA
    # del extracto; el depósito en tránsito se SUMA.
    """
    suma = {t: CERO for t in TIPOS_PARTIDA}
    for p in partidas:
        if p.tipo not in TIPOS_PARTIDA:
            raise DatosInvalidosError(f"Tipo de partida no válido: {p.tipo!r}.")
        suma[p.tipo] += redondear_bs(p.monto)
    libros = (redondear_bs(saldo_libros) - suma["CARGO_NO_REGISTRADO"] + suma["ABONO_NO_REGISTRADO"]
              + suma["ERROR_LIBROS_MAS"] - suma["ERROR_LIBROS_MENOS"])
    banco = redondear_bs(saldo_extracto) + suma["DEPOSITO_TRANSITO"] - suma["CHEQUE_CIRCULACION"]
    return {"saldo_libros": redondear_bs(saldo_libros), "saldo_extracto": redondear_bs(saldo_extracto),
            "sumas": suma, "saldo_conciliado_libros": libros, "saldo_conciliado_banco": banco,
            "diferencia": libros - banco, "cuadra": libros == banco}


def _fin_de_mes(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def preparar_conciliacion(
    con: sqlite3.Connection, sesion: Sesion, cuenta_banco: str, anio: int, mes: int, saldo_extracto: Any,
    partidas: Sequence[PartidaConciliacion], fecha_elaboracion: Any = None,
) -> dict[str, Any]:
    """El JF prepara la conciliación mensual (CI-FIN-07, pasos 1-3). Calcula
    el plazo con ``conciliacion_dias_habiles`` días hábiles desde el fin de mes
    (feriados de Bolivia) y señala las partidas en tránsito antiguas y los
    desembolsos del mes sobre el umbral de firma mancomunada.

    Si existe una conciliación preparada (no firmada) del mismo mes, queda
    SUSTITUIDA por la nueva versión.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    _validar_banco(cuenta_banco)
    if isinstance(anio, bool) or not isinstance(anio, int) or isinstance(mes, bool) or not isinstance(mes, int) \
            or not 1 <= mes <= 12:
        raise DatosInvalidosError("Indique un año y un mes válidos.")
    fin_mes = _fin_de_mes(anio, mes)
    elaboracion = a_fecha(fecha_elaboracion) if fecha_elaboracion is not None else date.today()
    if elaboracion <= fin_mes:
        raise ReglaNegocioError("La conciliación se elabora después del cierre del mes, con el extracto del mes.",
                                codigo="MES_NO_CERRADO")
    p = _params(con, fin_mes)
    plazo = int(_param(p, "conciliacion_dias_habiles"))
    limite = sumar_dias_habiles(fin_mes, plazo)
    partidas_norm = [PartidaConciliacion(x.tipo, _texto(x.descripcion, "descripción de la partida"), a_fecha(x.fecha),
                                         _monto_positivo(x.monto, "monto de la partida"), x.movimiento_id)
                     for x in partidas]
    libros = saldo_cuenta(con, cuenta_banco, fin_mes)
    calculo = calcular_conciliacion(libros, redondear_bs(saldo_extracto), partidas_norm)
    dias_alerta = int(_param(p, "partida_transito_alerta_dias"))
    antiguas = [{"descripcion": x.descripcion, "fecha": x.fecha.isoformat(), "monto": x.monto,
                 "dias": (fin_mes - x.fecha).days}
                for x in partidas_norm if x.tipo in ("CHEQUE_CIRCULACION", "DEPOSITO_TRANSITO")
                and (fin_mes - x.fecha).days > dias_alerta]
    mancomunada = redondear_bs(_param(p, "firma_mancomunada_sobre_bs"))
    sin_mancomunada = []
    for m in con.execute(
            """SELECT * FROM movimientos_bancarios WHERE cuenta_codigo = ? AND tipo = 'PAGO' AND fecha BETWEEN ? AND ?
                  AND monto_cent > ?""",
            (cuenta_banco, date(anio, mes, 1).isoformat(), fin_mes.isoformat(), a_centavos(mancomunada))):
        firmas = set()
        if m["autorizacion_id"]:
            firmas = {f[0] for f in con.execute(
                "SELECT calidad FROM autorizacion_firmas WHERE autorizacion_id = ? AND decision = 'APRUEBA'",
                (m["autorizacion_id"],))}
        if not {"GG", "CV"} <= firmas:
            sin_mancomunada.append({"movimiento_id": m["id"], "fecha": m["fecha"],
                                    "monto": de_centavos(int(m["monto_cent"])), "descripcion": m["descripcion"]})
    with transaccion(con):
        firmada = con.execute("SELECT id FROM conciliaciones WHERE cuenta_codigo = ? AND anio = ? AND mes = ? "
                              "AND estado = 'FIRMADA'", (cuenta_banco, anio, mes)).fetchone()
        if firmada:
            raise ReglaNegocioError("La conciliación de ese mes ya está firmada por la GG.", codigo="CONCILIACION_FIRMADA")
        previa = con.execute("SELECT id FROM conciliaciones WHERE cuenta_codigo = ? AND anio = ? AND mes = ? "
                             "AND estado = 'PREPARADA'", (cuenta_banco, anio, mes)).fetchone()
        if previa:
            con.execute("UPDATE conciliaciones SET estado = 'SUSTITUIDA' WHERE id = ?", (previa[0],))
            auditoria.registrar(con, sesion, "conciliaciones", "UPDATE", previa[0], antes={"estado": "PREPARADA"},
                                despues={"estado": "SUSTITUIDA"})
        cur = con.execute(
            """INSERT INTO conciliaciones (cuenta_codigo, anio, mes, fecha_corte, saldo_libros_cent, saldo_extracto_cent,
                   saldo_conciliado_libros_cent, saldo_conciliado_banco_cent, diferencia_cent, fecha_elaboracion,
                   fecha_limite, dentro_plazo, elaborado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cuenta_banco, anio, mes, fin_mes.isoformat(), a_centavos(calculo["saldo_libros"]),
             a_centavos(calculo["saldo_extracto"]), a_centavos(calculo["saldo_conciliado_libros"]),
             a_centavos(calculo["saldo_conciliado_banco"]), a_centavos(calculo["diferencia"]),
             elaboracion.isoformat(), limite.isoformat(), 1 if elaboracion <= limite else 0, sesion.nombre_usuario))
        cid = int(cur.lastrowid)
        con.executemany(
            """INSERT INTO conciliacion_partidas (conciliacion_id, tipo, descripcion, fecha, monto_cent, movimiento_id)
               VALUES (?,?,?,?,?,?)""",
            [(cid, x.tipo, x.descripcion, x.fecha.isoformat(), a_centavos(x.monto), x.movimiento_id)
             for x in partidas_norm])
        en_transito = {x.movimiento_id for x in partidas_norm if x.movimiento_id}
        for m in con.execute("SELECT id FROM movimientos_bancarios WHERE cuenta_codigo = ? AND fecha BETWEEN ? AND ? "
                             "AND conciliacion_id IS NULL", (cuenta_banco, date(anio, mes, 1).isoformat(),
                                                             fin_mes.isoformat())).fetchall():
            if m[0] not in en_transito:
                con.execute("UPDATE movimientos_bancarios SET conciliacion_id = ? WHERE id = ?", (cid, m[0]))
        resultado = {**calculo, "id": cid, "cuenta": cuenta_banco, "anio": anio, "mes": mes,
                     "fecha_corte": fin_mes.isoformat(), "fecha_elaboracion": elaboracion.isoformat(),
                     "fecha_limite": limite.isoformat(), "dentro_plazo": elaboracion <= limite,
                     "partidas_antiguas": antiguas, "desembolsos_sin_mancomunada": sin_mancomunada}
        auditoria.registrar(con, sesion, "conciliaciones", "INSERT", cid,
                            despues={k: v for k, v in resultado.items() if k != "sumas"})
    return resultado


def regularizar_partidas(con: sqlite3.Connection, sesion: Sesion, conciliacion_id: int, fecha: Any) -> list[int]:
    """Registra en libros los cargos y abonos bancarios no registrados de la
    conciliación (CI-FIN-07: "se registra el cargo bancario en libros")."""
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha)
    creados = []
    with transaccion(con):
        cab = con.execute("SELECT * FROM conciliaciones WHERE id = ?", (conciliacion_id,)).fetchone()
        if cab is None:
            raise DatosInvalidosError(f"No existe la conciliación {conciliacion_id}.")
        if cab["estado"] != "PREPARADA":
            raise ReglaNegocioError(f"La conciliación está {cab['estado']}.", codigo="CONCILIACION_NO_PREPARADA")
        for p in con.execute("""SELECT * FROM conciliacion_partidas WHERE conciliacion_id = ? AND asiento_id IS NULL
                                 AND tipo IN ('CARGO_NO_REGISTRADO','ABONO_NO_REGISTRADO')""",
                             (conciliacion_id,)).fetchall():
            tipo = "CARGO_BANCARIO" if p["tipo"] == "CARGO_NO_REGISTRADO" else "ABONO_BANCARIO"
            _, asiento_id = _registrar_ajuste_bancario(con, sesion, f, de_centavos(int(p["monto_cent"])),
                                                       cab["cuenta_codigo"], tipo, p["descripcion"], conciliacion_id)
            con.execute("UPDATE conciliacion_partidas SET asiento_id = ? WHERE id = ?", (asiento_id, p["id"]))
            creados.append(asiento_id)
    return creados


def firmar_conciliacion(con: sqlite3.Connection, sesion: Sesion, conciliacion_id: int, fecha_firma: Any = None) -> bool:
    """La GG revisa y firma la conciliación (CI-FIN-07, paso 4).

    Returns:
        ``True`` si la firma quedó dentro del plazo de días hábiles.

    Raises:
        ReglaNegocioError: la conciliación no cuadra o tiene cargos/abonos sin
            registrar en libros.
    """
    seguridad.exigir_permiso(con, sesion, "tesoreria", "AUTORIZAR")
    firma = a_fecha(fecha_firma) if fecha_firma is not None else date.today()
    with transaccion(con):
        cab = con.execute("SELECT * FROM conciliaciones WHERE id = ?", (conciliacion_id,)).fetchone()
        if cab is None:
            raise DatosInvalidosError(f"No existe la conciliación {conciliacion_id}.")
        if cab["estado"] != "PREPARADA":
            raise ReglaNegocioError(f"La conciliación está {cab['estado']}.", codigo="CONCILIACION_NO_PREPARADA")
        if cab["elaborado_por"] == sesion.nombre_usuario:
            raise AutorizacionError("Quien elabora la conciliación no puede firmarla (CI-FIN-07).", codigo="AUTOFIRMA")
        if int(cab["diferencia_cent"]) != 0:
            horas = int(_param(_params(con, cab["fecha_corte"]), "conciliacion_diferencia_reporte_horas"))
            raise ReglaNegocioError(
                f"La conciliación no cuadra (diferencia {formato_bs(de_centavos(int(cab['diferencia_cent'])))}): no se "
                f"firma. Si no se explica en {horas} horas se reporta al Consejo de Vigilancia (CI-FIN-07).",
                codigo="CONCILIACION_NO_CUADRA")
        pendientes = con.execute(
            """SELECT COUNT(*) FROM conciliacion_partidas WHERE conciliacion_id = ? AND asiento_id IS NULL
                AND tipo IN ('CARGO_NO_REGISTRADO','ABONO_NO_REGISTRADO')""", (conciliacion_id,)).fetchone()[0]
        if pendientes:
            raise ReglaNegocioError("Registre en libros los cargos y abonos bancarios antes de firmar.",
                                    codigo="PARTIDAS_SIN_REGULARIZAR")
        en_plazo = firma <= date.fromisoformat(cab["fecha_limite"])
        con.execute("UPDATE conciliaciones SET estado = 'FIRMADA', firmado_por = ?, firmado_en = ?, "
                    "firma_dentro_plazo = ? WHERE id = ?",
                    (sesion.nombre_usuario, firma.isoformat(), 1 if en_plazo else 0, conciliacion_id))
        auditoria.registrar(con, sesion, "conciliaciones", "AUTORIZACION", conciliacion_id,
                            antes={"estado": "PREPARADA"},
                            despues={"estado": "FIRMADA", "firmado_en": firma.isoformat(), "dentro_plazo": en_plazo})
    return en_plazo


def obtener_conciliacion(con: sqlite3.Connection, sesion: Sesion, conciliacion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    cab = con.execute("SELECT * FROM conciliaciones WHERE id = ?", (conciliacion_id,)).fetchone()
    if cab is None:
        raise DatosInvalidosError(f"No existe la conciliación {conciliacion_id}.")
    partidas = [{**dict(p), "monto": de_centavos(int(p["monto_cent"]))} for p in con.execute(
        "SELECT * FROM conciliacion_partidas WHERE conciliacion_id = ? ORDER BY id", (conciliacion_id,))]
    return {**dict(cab), **{k.replace("_cent", ""): de_centavos(int(cab[k])) for k in cab.keys() if k.endswith("_cent")},
            "partidas": partidas}


def listar_conciliaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    return [{**dict(c), **{k.replace("_cent", ""): de_centavos(int(c[k])) for k in c.keys() if k.endswith("_cent")}}
            for c in con.execute("SELECT * FROM conciliaciones ORDER BY anio DESC, mes DESC, id DESC")]


def listar_arqueos(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    return [{**dict(a), **{k.replace("_cent", ""): de_centavos(int(a[k])) for k in a.keys() if k.endswith("_cent")}}
            for a in con.execute("SELECT * FROM arqueos_caja ORDER BY fecha DESC, id DESC")]


def listar_reposiciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    return [{**dict(r), "total": de_centavos(int(r["total_cent"]))}
            for r in con.execute("SELECT * FROM reposiciones_caja ORDER BY id DESC")]


def movimientos_caja(con: sqlite3.Connection, sesion: Sesion, desde: Any = None, hasta: Any = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    condiciones, args = ["1 = 1"], []
    if desde is not None:
        condiciones.append("fecha >= ?")
        args.append(a_fecha(desde).isoformat())
    if hasta is not None:
        condiciones.append("fecha <= ?")
        args.append(a_fecha(hasta).isoformat())
    return [{**dict(m), "monto": de_centavos(int(m["monto_cent"]))} for m in con.execute(
        f"SELECT * FROM caja_movimientos WHERE {' AND '.join(condiciones)} ORDER BY fecha, id", args)]


def listar_flujos(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "tesoreria", "VER")
    return [{**dict(f), "saldo_inicial": de_centavos(int(f["saldo_inicial_cent"]))}
            for f in con.execute("SELECT * FROM flujos_proyectados ORDER BY id DESC")]
