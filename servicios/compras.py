"""Compras del ERP Pollolandia (Fase 4, Ciclo 2 — Egresos / Adquisiciones).

Implementa PR-FIN-03, PET-RAU §2.2, POL-04 Art. 2, CI-FIN-09 y los formatos
FIN-EGR-02 a FIN-EGR-05:

1. Proveedores y fichas de pequeños productores: C.I. y Certificado de
   Inscripción RAU (con su último pago) o Certificado de No Imponibilidad
   vigente; sin ellos el proveedor queda OBSERVADO y no se le compra.
2. Reporte de disponibilidad presupuestaria (FIN-EGR-02): un "No" impide la
   orden de compra.
3. Orden de compra (FIN-EGR-03): programada y emitida solo el día fijado
   (lunes), salvo compra de emergencia autorizada (CI-FIN-16); tres
   cotizaciones de proveedores distintos sobre Bs 5.000; autorización de la
   GG (CI-FIN-09); y ``tesoreria.validar_compromiso`` (vía
   ``registrar_compromiso``): bajo el 120 % del SME queda suspendida hasta la
   autorización escrita de la GG.
4. Recepción (FIN-EGR-04) con verificación de cantidad y calidad y, al
   contabilizarla, asiento automático inventario contra cuenta por pagar
   2.1.01.xx, entrada al kárdex (CPP) y cuenta por pagar.
5. Programación de pagos (FIN-EGR-05) con la jerarquía de POL-04 y firma
   mancomunada sobre Bs 5.000; pago por tesorería.
6. Indicador CAC (Cumplimiento del Calendario de Abastecimiento Crítico).

Las cifras (umbrales, días, número de cotizaciones) se leen de ``parametros``.
Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from db.conexion import (
    a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion,
)
from servicios import auditoria, contable, inventarios, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil, formato_cantidad
from servicios.seguridad import Sesion, TipoDecision
from utils.formato import a_fecha, fecha_pantalla, formato_bs, redondear_bs

# --------------------------------------------------------------------------
# Estructura del Plan de Cuentas v3 (no cifras)
# --------------------------------------------------------------------------
# Cuenta de inventario -> cuenta por pagar comercial (Descripción de Cuentas 2.1.01.xx)
CXP_POR_CUENTA: dict[str, str] = {
    "1.1.03.01": "2.1.01.01",
    "1.1.03.02": "2.1.01.02",
    "1.1.03.03": "2.1.01.03", "1.1.03.04": "2.1.01.03", "1.1.03.05": "2.1.01.03",
    "1.1.03.06": "2.1.01.03", "1.1.03.07": "2.1.01.03",
    "1.1.03.08": "2.1.01.04",
    "1.1.03.09": "2.1.01.05", "1.1.03.10": "2.1.01.05",
    "1.1.03.11": "2.1.01.06", "1.1.03.15": "2.1.01.06",
}
TIPOS_PROVEEDOR: dict[str, str] = {"EMPRESA": "Empresa (emite factura)",
                                   "PEQUENO_PRODUCTOR": "Pequeño productor (recibo con C.I.)"}
CERTIFICADOS: dict[str, str] = {"RAU": "Certificado de Inscripción RAU (con último pago)",
                                "NO_IMPONIBILIDAD": "Certificado de No Imponibilidad RAU"}
CONDICIONES_PAGO = ("CONTADO", "CREDITO")
DESTINOS = ("ACTUAL", "NUEVO")
CERO = Decimal("0.00")


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


def _dia(numero: int) -> str:
    return ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][numero - 1]


def _id(valor: Any, campo: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise DatosInvalidosError(f"Indique {campo}.")
    return valor


def categoria_pago(cuenta_inventario: str, destino_ciclo: str) -> str:
    """Nivel de la jerarquía POL-04 Art. 2 que corresponde a la compra.

    Pollitos BB y los insumos para iniciar el ciclo siguiente van al nivel 2;
    alimento, veterinarios y demás insumos del ciclo en curso, al nivel 3.
    # DISCREPANCIA: la jerarquía de POL-04 no menciona embalaje ni materiales
    # de mantenimiento; se tratan como insumos del ciclo en curso (nivel 3).
    """
    if cuenta_inventario == "1.1.03.01" or destino_ciclo == "NUEVO":
        return "POLLITOS_INSUMOS_NUEVO_CICLO"
    return "ALIMENTO_VETERINARIOS_CICLO"


# ==========================================================================
# 1. PROVEEDORES Y FICHAS DE PEQUEÑOS PRODUCTORES (PET-RAU §2.2)
# ==========================================================================
def evaluar_habilitacion(datos: Mapping[str, Any], fecha: Any) -> tuple[str, str | None]:
    """``(estado, motivo)`` de un proveedor a una fecha.

    * Empresa: NIT (emite factura a nombre de la cooperativa).
    * Pequeño productor: C.I. y certificado RAU (con último pago) o de No
      Imponibilidad, vigente a la fecha. Sin ellos queda OBSERVADO: "no se
      compra hasta que regularice" (PET-RAU §2.2, paso 2).
    """
    if datos.get("estado") == "INACTIVO":
        return "INACTIVO", datos.get("motivo_estado") or "Proveedor dado de baja."
    f = a_fecha(fecha)
    if datos["tipo"] == "EMPRESA":
        return ("HABILITADO", None) if (datos.get("nit") or "").strip() else ("OBSERVADO", "Falta el NIT.")
    faltas = []
    if not (datos.get("ci") or "").strip():
        faltas.append("C.I. del vendedor")
    tipo_cert = datos.get("tipo_certificado")
    if tipo_cert not in CERTIFICADOS or not (datos.get("nro_certificado") or "").strip():
        faltas.append("certificado RAU o de No Imponibilidad")
    else:
        vigencia = datos.get("certificado_vigente_hasta")
        if not vigencia:
            faltas.append("vigencia del certificado")
        elif a_fecha(vigencia) < f:
            faltas.append(f"certificado vigente (venció el {fecha_pantalla(vigencia)})")
        if tipo_cert == "RAU" and not (datos.get("ultimo_pago_rau_ref") or "").strip():
            faltas.append("copia del último pago del RAU")
    if faltas:
        return "OBSERVADO", "Falta: " + "; ".join(faltas) + " (PET-RAU §2.2)."
    return "HABILITADO", None


_CAMPOS_PROVEEDOR = ("nombre", "nit", "ci", "comunidad", "hectareas", "producto", "telefono", "tipo_certificado",
                     "nro_certificado", "certificado_vigente_hasta", "ultimo_pago_rau_ref")


def _normalizar_proveedor(tipo: str, datos: Mapping[str, Any]) -> dict[str, Any]:
    if tipo not in TIPOS_PROVEEDOR:
        raise DatosInvalidosError(f"Tipo de proveedor no válido: {tipo!r}.")
    limpio: dict[str, Any] = {"tipo": tipo}
    for campo in _CAMPOS_PROVEEDOR:
        valor = datos.get(campo)
        if campo == "nombre":
            limpio[campo] = _texto(valor, "nombre o razón social", 150)
        elif campo == "certificado_vigente_hasta":
            limpio[campo] = a_fecha(valor).isoformat() if valor else None
        elif campo == "tipo_certificado":
            if valor is not None and valor not in CERTIFICADOS:
                raise DatosInvalidosError(f"Tipo de certificado no válido: {valor!r}.")
            limpio[campo] = valor
        elif campo == "hectareas":
            if valor in (None, ""):
                limpio[campo] = None
            else:
                ha = Decimal(str(valor))
                if ha <= 0:
                    raise DatosInvalidosError("Las hectáreas cultivadas deben ser mayores a cero.")
                limpio[campo] = str(ha)
        else:
            limpio[campo] = _opcional(valor, campo, 120)
    if tipo == "EMPRESA" and not limpio["nit"]:
        raise DatosInvalidosError("El proveedor empresa requiere NIT.")
    return limpio


def registrar_proveedor(con: sqlite3.Connection, sesion: Sesion, tipo: str, fecha: Any = None,
                        **datos: Any) -> tuple[int, str]:
    """Registra la ficha del proveedor y calcula su habilitación.

    Returns:
        ``(proveedor_id, estado)``; un pequeño productor sin respaldo queda
        OBSERVADO (registro de proveedores observados).
    """
    seguridad.exigir_permiso(con, sesion, "compras", "CREAR")
    desconocidos = [k for k in datos if k not in _CAMPOS_PROVEEDOR]
    if desconocidos:
        raise DatosInvalidosError(f"Campos desconocidos: {', '.join(desconocidos)}.")
    limpio = _normalizar_proveedor(tipo, datos)
    estado, motivo = evaluar_habilitacion(limpio, fecha or date.today())
    for campo in ("nit", "ci"):
        if limpio[campo] and con.execute(f"SELECT 1 FROM proveedores WHERE {campo} = ?", (limpio[campo],)).fetchone():
            raise DatosInvalidosError(f"Ya existe un proveedor con ese {campo.upper()}.", codigo="PROVEEDOR_DUPLICADO")
    with transaccion(con):
        columnas = ["tipo", *_CAMPOS_PROVEEDOR, "estado", "motivo_estado", "registrado_por", "registrado_en"]
        valores = [limpio[c] for c in ["tipo", *_CAMPOS_PROVEEDOR]] + [estado, motivo, sesion.nombre_usuario,
                                                                       _ahora_iso()]
        cur = con.execute(f"INSERT INTO proveedores ({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})",
                          valores)
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "proveedores", "INSERT", pid, despues={**limpio, "estado": estado},
                            detalle=motivo)
    return pid, estado


def actualizar_proveedor(con: sqlite3.Connection, sesion: Sesion, proveedor_id: int, fecha: Any = None,
                         inactivar: bool = False, **cambios: Any) -> str:
    """Actualiza la ficha (p. ej. nuevo certificado) y recalcula su estado."""
    seguridad.exigir_permiso(con, sesion, "compras", "EDITAR")
    actual = _proveedor(con, proveedor_id)
    desconocidos = [k for k in cambios if k not in _CAMPOS_PROVEEDOR]
    if desconocidos:
        raise DatosInvalidosError(f"Campos desconocidos: {', '.join(desconocidos)}.")
    datos = {c: actual[c] for c in _CAMPOS_PROVEEDOR}
    datos.update(cambios)
    limpio = _normalizar_proveedor(actual["tipo"], datos)
    if inactivar:
        estado, motivo = "INACTIVO", "Dado de baja por Finanzas."
    else:
        estado, motivo = evaluar_habilitacion(limpio, fecha or date.today())
    with transaccion(con):
        sets = ", ".join(f"{c} = ?" for c in _CAMPOS_PROVEEDOR)
        con.execute(f"UPDATE proveedores SET {sets}, estado = ?, motivo_estado = ?, actualizado_en = ? WHERE id = ?",
                    (*[limpio[c] for c in _CAMPOS_PROVEEDOR], estado, motivo, _ahora_iso(), proveedor_id))
        auditoria.registrar(con, sesion, "proveedores", "UPDATE", proveedor_id,
                            antes={**{c: actual[c] for c in _CAMPOS_PROVEEDOR}, "estado": actual["estado"]},
                            despues={**limpio, "estado": estado}, detalle=motivo)
    return estado


def _proveedor(con: sqlite3.Connection, proveedor_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM proveedores WHERE id = ?", (_id(proveedor_id, "el proveedor"),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el proveedor {proveedor_id}.")
    return fila


def verificar_proveedor(con: sqlite3.Connection, proveedor_id: int, fecha: Any) -> sqlite3.Row:
    """Bloquea la compra si el proveedor no está habilitado a la fecha
    (incluye certificados vencidos después del registro).

    Raises:
        ReglaNegocioError: ``PROVEEDOR_OBSERVADO`` o ``PROVEEDOR_INACTIVO``.
    """
    fila = _proveedor(con, proveedor_id)
    estado, motivo = evaluar_habilitacion(dict(fila), fecha)
    if estado == "INACTIVO":
        raise ReglaNegocioError(f"El proveedor {fila['nombre']} está dado de baja.", codigo="PROVEEDOR_INACTIVO")
    if estado != "HABILITADO":
        raise ReglaNegocioError(f"Compra bloqueada: el proveedor {fila['nombre']} está observado. {motivo} No se "
                                "compra hasta que regularice o se consulte al asesor tributario.",
                                codigo="PROVEEDOR_OBSERVADO", detalle={"motivo": motivo})
    return fila


def listar_proveedores(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Proveedores con su estado recalculado a la fecha."""
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    f = fecha or date.today()
    filas = []
    for fila in con.execute("SELECT * FROM proveedores ORDER BY nombre"):
        estado, motivo = evaluar_habilitacion(dict(fila), f)
        filas.append({**dict(fila), "estado_a_la_fecha": estado, "motivo_a_la_fecha": motivo})
    return filas


# ==========================================================================
# 2. DISPONIBILIDAD PRESUPUESTARIA (FIN-EGR-02)
# ==========================================================================
def _ejecutado(con: sqlite3.Connection, ejercicio: int, ciclo: int, cuenta: str, excluir_orden: int | None) -> Decimal:
    """Monto comprometido o ejecutado por órdenes del ciclo en la partida."""
    total = 0
    for fila in con.execute(
            """SELECT o.id, o.estado, l.id AS linea_id, l.total_cent FROM ordenes_compra o
                 JOIN oc_lineas l ON l.orden_id = o.id JOIN insumos i ON i.id = l.insumo_id
                WHERE o.ejercicio = ? AND o.ciclo = ? AND i.cuenta_inventario = ? AND o.estado <> 'ANULADA'""",
            (ejercicio, ciclo, cuenta)):
        if excluir_orden is not None and int(fila["id"]) == excluir_orden:
            continue
        if fila["estado"] in ("RECIBIDA", "CERRADA"):
            recibido = con.execute(
                """SELECT COALESCE(SUM(rl.valor_cent), 0) FROM recepcion_lineas rl JOIN recepciones r
                     ON r.id = rl.recepcion_id WHERE rl.oc_linea_id = ? AND r.estado = 'CONTABILIZADA'""",
                (fila["linea_id"],)).fetchone()[0]
            total += int(recibido)
        else:
            total += int(fila["total_cent"])
    return de_centavos(total)


def disponibilidad(con: sqlite3.Connection, fecha: Any, solicitado: Mapping[str, Any] | None = None,
                   excluir_orden: int | None = None) -> dict[str, Any]:
    """Saldo presupuestario por partida del ciclo de la fecha.

    Con ``solicitado`` (``{cuenta: monto}``) indica si habilita la emisión:
    saldo disponible ≥ monto solicitado en cada partida. Sin ``solicitado``
    lista todas las partidas del ciclo (reporte de consulta).
    """
    f = a_fecha(fecha)
    ciclo = inventarios.ciclo_de_fecha(con, f)
    pedidos = {c: redondear_bs(m) for c, m in (solicitado or {}).items()}
    cuentas = list(pedidos) if pedidos else [r[0] for r in con.execute(
        "SELECT cuenta_inventario FROM partidas_presupuesto WHERE ejercicio = ? AND ciclo = ? "
        "AND cuenta_inventario LIKE '1.1.03.%' ORDER BY cuenta_inventario",
        (ciclo["ejercicio"], ciclo["ciclo"]))]
    lineas = []
    for cuenta in cuentas:
        partida = con.execute(
            "SELECT * FROM partidas_presupuesto WHERE ejercicio = ? AND ciclo = ? AND cuenta_inventario = ?",
            (ciclo["ejercicio"], ciclo["ciclo"], cuenta)).fetchone()
        aprobado = de_centavos(int(partida["monto_cent"])) if partida else CERO
        ejecutado = _ejecutado(con, ciclo["ejercicio"], ciclo["ciclo"], cuenta, excluir_orden)
        saldo = aprobado - ejecutado
        pedido = pedidos.get(cuenta, CERO)
        habilita = partida is not None and saldo >= pedido and (pedido > 0 or saldo >= 0)
        nombre = partida["descripcion"] if partida else con.execute(
            "SELECT nombre FROM cuentas WHERE codigo = ?", (cuenta,)).fetchone()[0]
        lineas.append({"cuenta": cuenta, "partida": nombre, "aprobado": aprobado, "ejecutado": ejecutado,
                       "solicitado": pedido, "saldo": saldo, "saldo_despues": saldo - pedido, "habilita": habilita,
                       "fuente": partida["fuente"] if partida else "Sin partida presupuestaria"})
    return {"fecha": f.isoformat(), "ejercicio": ciclo["ejercicio"], "ciclo": ciclo["ciclo"],
            "lineas": lineas, "habilita": bool(lineas) and all(l["habilita"] for l in lineas)}


def reporte_disponibilidad(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """FIN-EGR-02 de consulta para todas las partidas del ciclo."""
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    return disponibilidad(con, fecha)


# ==========================================================================
# 3. ORDEN DE COMPRA (FIN-EGR-03)
# ==========================================================================
@dataclass(frozen=True)
class LineaOC:
    """Línea de la orden: insumo, cantidad y precio unitario en Bs."""

    insumo_id: int
    cantidad: Any
    precio_unitario: Any


@dataclass(frozen=True)
class CotizacionOC:
    """Cotización de un proveedor registrado (PR-FIN-03)."""

    proveedor_id: int
    monto: Any
    documento_ref: str


def _orden(con: sqlite3.Connection, orden_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM ordenes_compra WHERE id = ?", (_id(orden_id, "la orden de compra"),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la orden de compra {orden_id}.")
    return fila


def numero_oc(orden: Mapping[str, Any]) -> str:
    """Número visible de la orden: ``OC-2026-0001``."""
    return f"OC-{orden['ejercicio']}-{int(orden['numero']):04d}"


def _validar_cotizaciones(con: sqlite3.Connection, proveedor_id: int, total: Decimal, fecha: date,
                          cotizaciones: Sequence[CotizacionOC], justificacion: str | None) -> list[dict[str, Any]]:
    p = _params(con, fecha)
    umbral = redondear_bs(_param(p, "oc_tres_cotizaciones_sobre_bs"))
    minimo = int(_param(p, "cotizaciones_minimas"))
    lista: list[dict[str, Any]] = []
    vistos: set[int] = set()
    for c in cotizaciones:
        if not isinstance(c, CotizacionOC):
            raise DatosInvalidosError("Cada cotización debe indicar proveedor, monto y documento.")
        pid = _id(c.proveedor_id, "el proveedor de la cotización")
        if pid in vistos:
            raise DatosInvalidosError("Hay dos cotizaciones del mismo proveedor.", codigo="COTIZACION_DUPLICADA")
        vistos.add(pid)
        verificar_proveedor(con, pid, fecha)
        monto = redondear_bs(c.monto)
        if monto <= 0:
            raise DatosInvalidosError("El monto de la cotización debe ser mayor a cero.")
        lista.append({"proveedor_id": pid, "monto": monto, "documento_ref": _texto(c.documento_ref, "documento de la "
                                                                                    "cotización", 60)})
    if total > umbral:
        if len(lista) < minimo:
            raise ReglaNegocioError(
                f"PR-FIN-03: la compra de {formato_bs(total)} supera {formato_bs(umbral)} y requiere cotización con un "
                f"mínimo de {minimo} proveedores aprobados distintos; se presentaron {len(lista)}.",
                codigo="COTIZACIONES_INSUFICIENTES", detalle={"minimo": minimo, "presentadas": len(lista)})
        propia = next((c for c in lista if c["proveedor_id"] == proveedor_id), None)
        if propia is None:
            raise ReglaNegocioError("El proveedor seleccionado debe estar entre los cotizados.",
                                    codigo="PROVEEDOR_NO_COTIZADO")
        menor = min(c["monto"] for c in lista)
        if propia["monto"] > menor and not justificacion:
            raise ReglaNegocioError("El proveedor elegido no presentó la cotización más baja: registre la "
                                    "justificación de la selección.", codigo="JUSTIFICACION_REQUERIDA")
    return lista


def preparar_orden(
    con: sqlite3.Connection, sesion: Sesion, proveedor_id: int, fecha_programada: Any, lineas: Sequence[LineaOC], *,
    condicion_pago: str = "CONTADO", dias_credito: int = 0, destino_ciclo: str = "ACTUAL",
    cotizaciones: Sequence[CotizacionOC] = (), justificacion_seleccion: str | None = None,
    emergencia_id: int | None = None,
) -> dict[str, Any]:
    """La Unidad de Finanzas prepara la orden y hace la verificación previa:

    1. Fecha programada en el día de emisión (lunes), salvo emergencia
       autorizada por la GG (CI-FIN-16 Art. 10).
    2. Proveedor habilitado (fichas RAU de pequeños productores).
    3. Tres cotizaciones sobre el umbral (PR-FIN-03).
    4. Disponibilidad presupuestaria (FIN-EGR-02): un "No" la suspende.
    5. ``tesoreria.registrar_compromiso`` → ``validar_compromiso`` (POL-04
       Art. 2): bajo el 120 % del SME queda suspendida y se crea la
       solicitud de autorización escrita de la GG.
    6. Solicitud de autorización COMPRA_INSUMOS (CI-FIN-09) para la GG.

    Returns:
        ``{orden_id, numero, total, compromiso_id, suspendida, mensaje_sme,
        autorizacion_id, autorizacion_compromiso_id, reporte_id}``.
    """
    seguridad.exigir_permiso(con, sesion, "compras", "CREAR")
    seguridad.exigir_permiso(con, sesion, "tesoreria", "CREAR")
    f = a_fecha(fecha_programada, "fecha programada")
    if condicion_pago not in CONDICIONES_PAGO:
        raise DatosInvalidosError(f"Condición de pago no válida: {condicion_pago!r}.")
    if isinstance(dias_credito, bool) or not isinstance(dias_credito, int) or dias_credito < 0:
        raise DatosInvalidosError("Los días de crédito deben ser un entero no negativo.")
    if condicion_pago == "CREDITO" and dias_credito == 0:
        raise DatosInvalidosError("El crédito pactado debe indicar los días de plazo (no se admiten condiciones "
                                  "informales, PR-FIN-03).")
    if condicion_pago == "CONTADO":
        dias_credito = 0
    if destino_ciclo not in DESTINOS:
        raise DatosInvalidosError(f"Destino de ciclo no válido: {destino_ciclo!r}.")
    p = _params(con, f)
    emergencia = None
    if emergencia_id is not None:
        emergencia = con.execute("SELECT e.*, a.insumo_id FROM compras_emergencia e JOIN alertas_stock a "
                                 "ON a.id = e.alerta_id WHERE e.id = ?", (emergencia_id,)).fetchone()
        if emergencia is None:
            raise DatosInvalidosError(f"No existe la compra de emergencia {emergencia_id}.")
        if emergencia["estado"] != "AUTORIZADA":
            raise ReglaNegocioError("La compra de emergencia ya fue utilizada.", codigo="EMERGENCIA_UTILIZADA")
    else:
        dia = int(_param(p, "dia_semana_orden_compra"))
        if f.isoweekday() != dia:
            raise ReglaNegocioError(
                f"Las órdenes de compra se emiten únicamente los {_dia(dia)} (PR-FIN-03; FIN-EGR-03): el "
                f"{fecha_pantalla(f)} es {_dia(f.isoweekday())}.", codigo="OC_DIA_NO_PERMITIDO")
    proveedor = verificar_proveedor(con, proveedor_id, f)
    if not lineas:
        raise DatosInvalidosError("La orden de compra requiere al menos una línea.")
    inicio_alimento = int(_param(p, "ejercicio_inicio_alimento_propio"))
    ejercicio = ejercicio_de_fecha_bd(con, f)
    detalle: list[dict[str, Any]] = []
    cuentas_cxp: set[str] = set()
    por_partida: dict[str, Decimal] = {}
    insumos_vistos: set[int] = set()
    for n, linea in enumerate(lineas, start=1):
        if not isinstance(linea, LineaOC):
            raise DatosInvalidosError(f"Línea {n}: formato no válido.")
        insumo = inventarios.obtener_insumo(con, linea.insumo_id)
        if int(insumo["id"]) in insumos_vistos:
            raise DatosInvalidosError(f"Línea {n}: el insumo {insumo['nombre']} está repetido.")
        insumos_vistos.add(int(insumo["id"]))
        if not insumo["activo"] or not insumo["comprable"]:
            raise ReglaNegocioError(f"Línea {n}: {insumo['nombre']} no es un insumo comprable.",
                                    codigo="INSUMO_NO_COMPRABLE")
        uso_2027 = con.execute("SELECT uso_desde_2027 FROM cuentas WHERE codigo = ?",
                               (insumo["cuenta_inventario"],)).fetchone()[0]
        if uso_2027 and ejercicio < inicio_alimento:
            raise ReglaNegocioError(f"Línea {n}: {insumo['nombre']} ({insumo['cuenta_inventario']}) se compra desde el "
                                    f"ejercicio {inicio_alimento} (Plan de Cuentas v3, nota (*)).",
                                    codigo="CUENTA_NO_HABILITADA")
        cant = a_mil(linea.cantidad, f"cantidad de la línea {n}")
        precio = redondear_bs(linea.precio_unitario)
        if precio <= 0:
            raise DatosInvalidosError(f"Línea {n}: el precio unitario debe ser mayor a cero.")
        total_linea = redondear_bs(de_mil(cant) * precio)
        if total_linea <= 0:
            raise DatosInvalidosError(f"Línea {n}: el total de la línea debe ser mayor a cero.")
        cuentas_cxp.add(CXP_POR_CUENTA[insumo["cuenta_inventario"]])
        por_partida[insumo["cuenta_inventario"]] = por_partida.get(insumo["cuenta_inventario"], CERO) + total_linea
        detalle.append({"insumo": insumo, "cantidad_mil": cant, "precio": precio, "total": total_linea})
    if len(cuentas_cxp) != 1:
        raise ReglaNegocioError("Todas las líneas de una orden deben corresponder a la misma cuenta por pagar "
                                f"(2.1.01.xx); hay {', '.join(sorted(cuentas_cxp))}. Emita órdenes separadas.",
                                codigo="OC_CUENTAS_MEZCLADAS")
    if emergencia is not None and int(emergencia["insumo_id"]) not in insumos_vistos:
        raise ReglaNegocioError("La compra de emergencia autorizada corresponde a otro insumo.",
                                codigo="EMERGENCIA_OTRO_INSUMO")
    total = sum((d["total"] for d in detalle), CERO)
    justificacion = _opcional(justificacion_seleccion, "justificación de la selección", 500)
    lista_cot = _validar_cotizaciones(con, int(proveedor["id"]), total, f, cotizaciones, justificacion)
    disp = disponibilidad(con, f, por_partida)
    if not disp["habilita"]:
        faltas = [f"{l['partida']} (saldo {formato_bs(l['saldo'])}, solicitado {formato_bs(l['solicitado'])})"
                  for l in disp["lineas"] if not l["habilita"]]
        raise ReglaNegocioError(
            "FIN-EGR-02: sin saldo presupuestario disponible en " + "; ".join(faltas) + ". La emisión queda "
            "suspendida (POL-04 Art. 2).", codigo="PRESUPUESTO_INSUFICIENTE", detalle={"disponibilidad": disp})
    cuenta_cxp = cuentas_cxp.pop()
    primera_cuenta = detalle[0]["insumo"]["cuenta_inventario"]
    categoria = categoria_pago(primera_cuenta, destino_ciclo)
    if any(d["insumo"]["cuenta_inventario"] == "1.1.03.01" for d in detalle):
        destino_ciclo = "NUEVO"
        categoria = "POLLITOS_INSUMOS_NUEVO_CICLO"
    ciclo = inventarios.ciclo_de_fecha(con, f)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO reportes_disponibilidad (fecha, ejercicio, ciclo, habilita, detalle_json, elaborado_por,
                   registrado_en) VALUES (?,?,?,?,?,?,?)""",
            (f.isoformat(), disp["ejercicio"], disp["ciclo"], 1, auditoria.a_json(disp), sesion.nombre_usuario,
             _ahora_iso()))
        reporte_id = int(cur.lastrowid)
        numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) + 1 FROM ordenes_compra WHERE ejercicio = ?",
                                 (ejercicio,)).fetchone()[0])
        cur = con.execute(
            """INSERT INTO ordenes_compra (numero, ejercicio, ciclo, fecha_programada, proveedor_id, cuenta_por_pagar,
                   categoria_pago, condicion_pago, dias_credito, destino_ciclo, total_cent, es_emergencia,
                   emergencia_id, justificacion_seleccion, reporte_disponibilidad_id, estado, preparado_por,
                   preparado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (numero, ejercicio, ciclo["ciclo"], f.isoformat(), int(proveedor["id"]), cuenta_cxp, categoria,
             condicion_pago, dias_credito, destino_ciclo, a_centavos(total), int(emergencia is not None),
             emergencia_id, justificacion, reporte_id, "PREPARADA", sesion.nombre_usuario, _ahora_iso()))
        orden_id = int(cur.lastrowid)
        etiqueta = f"OC-{ejercicio}-{numero:04d}"
        for d in detalle:
            con.execute("INSERT INTO oc_lineas (orden_id, insumo_id, cantidad_mil, precio_unitario_cent, total_cent) "
                        "VALUES (?,?,?,?,?)", (orden_id, int(d["insumo"]["id"]), d["cantidad_mil"],
                                               a_centavos(d["precio"]), a_centavos(d["total"])))
        nombres = {}
        for c in lista_cot:
            con.execute("INSERT INTO oc_cotizaciones (orden_id, proveedor_id, monto_cent, documento_ref) "
                        "VALUES (?,?,?,?)", (orden_id, c["proveedor_id"], a_centavos(c["monto"]), c["documento_ref"]))
            nombres[c["proveedor_id"]] = _proveedor(con, c["proveedor_id"])["nombre"]
        compromiso_id, evaluacion, aut_compromiso = tesoreria.registrar_compromiso(
            con, sesion, total, "COMPRA", f"{etiqueta} {proveedor['nombre']}", f)
        contexto = seguridad.contexto_desde_bd(con, f)
        aut_compra = seguridad.solicitar_autorizacion(
            con, sesion, TipoDecision.COMPRA_INSUMOS, total, f"{etiqueta}: compra a {proveedor['nombre']}", contexto,
            referencia=etiqueta, documentos={"FIN-EGR-02": f"Reporte {reporte_id}"},
            cotizaciones=[seguridad.Cotizacion(nombres[c["proveedor_id"]], c["monto"], c["documento_ref"])
                          for c in lista_cot])
        con.execute("UPDATE ordenes_compra SET compromiso_id = ?, autorizacion_id = ? WHERE id = ?",
                    (compromiso_id, aut_compra, orden_id))
        if emergencia is not None:
            con.execute("UPDATE compras_emergencia SET estado = 'UTILIZADA', orden_compra_id = ? WHERE id = ?",
                        (orden_id, emergencia_id))
            auditoria.registrar(con, sesion, "compras_emergencia", "UPDATE", emergencia_id,
                                antes={"estado": "AUTORIZADA"}, despues={"estado": "UTILIZADA", "orden_id": orden_id})
        auditoria.registrar(con, sesion, "ordenes_compra", "INSERT", orden_id,
                            despues={"numero": etiqueta, "proveedor": proveedor["nombre"], "total": total,
                                     "fecha_programada": f.isoformat(), "condicion_pago": condicion_pago,
                                     "cotizaciones": len(lista_cot), "compromiso_id": compromiso_id,
                                     "suspendida_sme": evaluacion.suspendido, "emergencia_id": emergencia_id},
                            detalle=evaluacion.mensaje)
    return {"orden_id": orden_id, "numero": etiqueta, "total": total, "compromiso_id": compromiso_id,
            "suspendida": evaluacion.suspendido, "mensaje_sme": evaluacion.mensaje, "autorizacion_id": aut_compra,
            "autorizacion_compromiso_id": aut_compromiso, "reporte_id": reporte_id}


def _puede_emitir(con: sqlite3.Connection, sesion: Sesion, fecha: date) -> None:
    if sesion.rol == "GG":
        seguridad.exigir_permiso(con, sesion, "compras", "AUTORIZAR")
        return
    if seguridad.orden_suplencia(con, sesion, fecha) is None:
        seguridad.exigir_permiso(con, sesion, "compras", "AUTORIZAR")
        raise AutorizacionError("La orden de compra la emite la Gerencia General o su suplente (CI-FIN-17).",
                                codigo="SIN_PERMISO")


def emitir_orden(con: sqlite3.Connection, sesion: Sesion, orden_id: int, fecha: Any) -> str:
    """La Gerencia General emite la orden (FIN-EGR-03).

    Raises:
        ReglaNegocioError: ``OC_DIA_NO_PERMITIDO`` si la fecha no es el día de
            emisión (lunes) y la orden no es de emergencia; fecha anterior a la
            programada; orden no preparada.
        AutorizacionError: falta la autorización COMPRA_INSUMOS de la GG o el
            compromiso sigue suspendido por el SME.

    Returns:
        Número de la orden emitida.
    """
    f = a_fecha(fecha)
    _puede_emitir(con, sesion, f)
    with transaccion(con):
        orden = _orden(con, orden_id)
        etiqueta = numero_oc(orden)
        if orden["estado"] != "PREPARADA":
            raise ReglaNegocioError(f"{etiqueta} está {orden['estado']}.", codigo="OC_NO_PREPARADA")
        if not orden["es_emergencia"]:
            dia = int(_param(_params(con, f), "dia_semana_orden_compra"))
            if f.isoweekday() != dia:
                raise ReglaNegocioError(
                    f"Las órdenes de compra se emiten únicamente los {_dia(dia)} (PR-FIN-03): el {fecha_pantalla(f)} "
                    f"es {_dia(f.isoweekday())}. Si hay déficit bajo el punto de reorden, use la compra de emergencia "
                    "(CI-FIN-16).", codigo="OC_DIA_NO_PERMITIDO")
            if f.isoformat() < orden["fecha_programada"]:
                raise ReglaNegocioError(f"{etiqueta} está programada para el {fecha_pantalla(orden['fecha_programada'])}.",
                                        codigo="OC_ANTES_DE_PROGRAMADA")
        compromiso = con.execute("SELECT * FROM compromisos WHERE id = ?", (orden["compromiso_id"],)).fetchone()
        if compromiso is None or compromiso["estado"] == "SUSPENDIDO":
            raise AutorizacionError(
                f"{etiqueta}: el compromiso está suspendido porque el saldo proyectado queda bajo el 120 % del SME "
                f"(POL-04 Art. 2). La GG debe firmar la autorización escrita n.º {compromiso['autorizacion_id'] if compromiso else '—'} "
                "y Finanzas confirmarla en Tesorería.", codigo="COMPROMISO_SUSPENDIDO")
        if compromiso["estado"] not in ("VIGENTE", "AUTORIZADO"):
            raise ReglaNegocioError(f"El compromiso de {etiqueta} está {compromiso['estado']}.",
                                    codigo="COMPROMISO_NO_VIGENTE")
        total = de_centavos(int(orden["total_cent"]))
        seguridad.consumir_autorizacion(con, sesion, int(orden["autorizacion_id"]), "ordenes_compra", orden_id,
                                        tipo_decision=TipoDecision.COMPRA_INSUMOS, monto=total)
        puntual = int(not orden["es_emergencia"] and f.isoformat() == orden["fecha_programada"])
        con.execute("UPDATE ordenes_compra SET estado = 'EMITIDA', fecha_emision = ?, emitida_por = ?, "
                    "emitida_puntual = ?, actualizado_en = ? WHERE id = ?",
                    (f.isoformat(), sesion.nombre_usuario, puntual, _ahora_iso(), orden_id))
        auditoria.registrar(con, sesion, "ordenes_compra", "AUTORIZACION", orden_id, antes={"estado": "PREPARADA"},
                            despues={"estado": "EMITIDA", "fecha_emision": f.isoformat(), "puntual": bool(puntual),
                                     "suplencia": sesion.rol != "GG"})
    return etiqueta


def anular_orden(con: sqlite3.Connection, sesion: Sesion, orden_id: int, motivo: str) -> None:
    """Anula una orden sin recepciones y libera su compromiso de caja."""
    seguridad.exigir_permiso(con, sesion, "compras", "EDITAR")
    causa = _texto(motivo, "motivo", 300)
    with transaccion(con):
        orden = _orden(con, orden_id)
        if orden["estado"] not in ("PREPARADA", "EMITIDA"):
            raise ReglaNegocioError(f"{numero_oc(orden)} está {orden['estado']}; no se puede anular.",
                                    codigo="OC_NO_ANULABLE")
        if con.execute("SELECT 1 FROM recepciones WHERE orden_id = ? AND estado <> 'RECHAZADA'", (orden_id,)).fetchone():
            raise ReglaNegocioError("La orden tiene recepciones registradas: ciérrela.", codigo="OC_CON_RECEPCIONES")
        compromiso = con.execute("SELECT estado FROM compromisos WHERE id = ?", (orden["compromiso_id"],)).fetchone()
        if compromiso is not None and compromiso["estado"] not in ("PAGADO", "ANULADO"):
            tesoreria.anular_compromiso(con, sesion, int(orden["compromiso_id"]), f"{numero_oc(orden)} anulada: {causa}")
        if orden["emergencia_id"]:
            con.execute("UPDATE compras_emergencia SET estado = 'AUTORIZADA', orden_compra_id = NULL WHERE id = ?",
                        (orden["emergencia_id"],))
        con.execute("UPDATE ordenes_compra SET estado = 'ANULADA', motivo_cierre = ?, actualizado_en = ? WHERE id = ?",
                    (causa, _ahora_iso(), orden_id))
        auditoria.registrar(con, sesion, "ordenes_compra", "UPDATE", orden_id, antes={"estado": orden["estado"]},
                            despues={"estado": "ANULADA"}, detalle=causa)


def _actualizar_compromiso(con: sqlite3.Connection, sesion: Sesion, orden_id: int) -> None:
    """Cierra el compromiso de caja cuando la orden terminó y no quedan
    cuentas por pagar pendientes (PAGADO si hubo pagos, ANULADO si nada se
    recibió)."""
    orden = _orden(con, orden_id)
    if orden["estado"] not in ("RECIBIDA", "CERRADA") or not orden["compromiso_id"]:
        return
    compromiso = con.execute("SELECT * FROM compromisos WHERE id = ?", (orden["compromiso_id"],)).fetchone()
    if compromiso is None or compromiso["estado"] in ("PAGADO", "ANULADO"):
        return
    pendientes = con.execute("SELECT COUNT(*) FROM cuentas_por_pagar WHERE orden_id = ? AND estado <> 'PAGADA'",
                             (orden_id,)).fetchone()[0]
    if pendientes:
        return
    pagadas = con.execute("SELECT COUNT(*) FROM cuentas_por_pagar WHERE orden_id = ? AND estado = 'PAGADA'",
                          (orden_id,)).fetchone()[0]
    nuevo = "PAGADO" if pagadas else "ANULADO"
    con.execute("UPDATE compromisos SET estado = ?, actualizado_en = ? WHERE id = ?",
                (nuevo, _ahora_iso(), int(orden["compromiso_id"])))
    auditoria.registrar(con, sesion, "compromisos", "UPDATE", int(orden["compromiso_id"]),
                        antes={"estado": compromiso["estado"]}, despues={"estado": nuevo},
                        detalle=f"{numero_oc(orden)} concluida")


def cerrar_orden(con: sqlite3.Connection, sesion: Sesion, orden_id: int, motivo: str) -> None:
    """Cierra una orden con saldo no entregado (el faltante ya no se recibirá)."""
    seguridad.exigir_permiso(con, sesion, "compras", "EDITAR")
    causa = _texto(motivo, "motivo", 300)
    with transaccion(con):
        orden = _orden(con, orden_id)
        if orden["estado"] not in ("EMITIDA", "RECIBIDA_PARCIAL"):
            raise ReglaNegocioError(f"{numero_oc(orden)} está {orden['estado']}; no se puede cerrar.",
                                    codigo="OC_NO_CERRABLE")
        if con.execute("SELECT 1 FROM recepciones WHERE orden_id = ? AND estado = 'RECIBIDA'", (orden_id,)).fetchone():
            raise ReglaNegocioError("Hay recepciones sin contabilizar en la orden.", codigo="RECEPCION_PENDIENTE")
        con.execute("UPDATE ordenes_compra SET estado = 'CERRADA', motivo_cierre = ?, actualizado_en = ? WHERE id = ?",
                    (causa, _ahora_iso(), orden_id))
        auditoria.registrar(con, sesion, "ordenes_compra", "UPDATE", orden_id, antes={"estado": orden["estado"]},
                            despues={"estado": "CERRADA"}, detalle=causa)
        _actualizar_compromiso(con, sesion, orden_id)


def obtener_orden(con: sqlite3.Connection, sesion: Sesion, orden_id: int) -> dict[str, Any]:
    """Orden con proveedor, líneas, cotizaciones, compromiso, autorización y recepciones."""
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    orden = _orden(con, orden_id)
    proveedor = _proveedor(con, int(orden["proveedor_id"]))
    lineas = []
    for l in con.execute("""SELECT l.*, i.nombre AS insumo, i.unidad, i.cuenta_inventario, i.controla_vencimiento
                              FROM oc_lineas l JOIN insumos i ON i.id = l.insumo_id WHERE l.orden_id = ? ORDER BY l.id""",
                         (orden_id,)):
        lineas.append({**dict(l), "cantidad": de_mil(l["cantidad_mil"]), "precio_unitario":
                       de_centavos(int(l["precio_unitario_cent"])), "total": de_centavos(int(l["total_cent"])),
                       "recibido": de_mil(l["cantidad_recibida_mil"]),
                       "pendiente_mil": int(l["cantidad_mil"]) - int(l["cantidad_recibida_mil"])})
    cotizaciones = [{**dict(c), "monto": de_centavos(int(c["monto_cent"]))} for c in con.execute(
        """SELECT c.*, p.nombre AS proveedor FROM oc_cotizaciones c JOIN proveedores p ON p.id = c.proveedor_id
            WHERE c.orden_id = ? ORDER BY c.monto_cent""", (orden_id,))]
    compromiso = con.execute("SELECT * FROM compromisos WHERE id = ?", (orden["compromiso_id"],)).fetchone() \
        if orden["compromiso_id"] else None
    autorizacion = con.execute("SELECT * FROM autorizaciones WHERE id = ?", (orden["autorizacion_id"],)).fetchone() \
        if orden["autorizacion_id"] else None
    recepciones = [dict(r) for r in con.execute("SELECT * FROM recepciones WHERE orden_id = ? ORDER BY id",
                                                (orden_id,))]
    return {**dict(orden), "etiqueta": numero_oc(orden), "total": de_centavos(int(orden["total_cent"])),
            "proveedor": dict(proveedor), "lineas": lineas, "cotizaciones": cotizaciones,
            "compromiso": dict(compromiso) if compromiso else None,
            "autorizacion": dict(autorizacion) if autorizacion else None, "recepciones": recepciones}


def listar_ordenes(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    sql = ("SELECT o.*, p.nombre AS proveedor, c.estado AS estado_compromiso, a.estado AS estado_autorizacion "
           "FROM ordenes_compra o JOIN proveedores p ON p.id = o.proveedor_id "
           "LEFT JOIN compromisos c ON c.id = o.compromiso_id LEFT JOIN autorizaciones a ON a.id = o.autorizacion_id")
    args: tuple[Any, ...] = ()
    if estado:
        sql, args = sql + " WHERE o.estado = ?", (estado,)
    return [{**dict(f), "etiqueta": numero_oc(f), "total": de_centavos(int(f["total_cent"]))}
            for f in con.execute(sql + " ORDER BY o.id DESC", args)]


# ==========================================================================
# 4. RECEPCIÓN (FIN-EGR-04) Y ASIENTO DE COMPRA
# ==========================================================================
@dataclass(frozen=True)
class LineaRecepcion:
    """Verificación de una línea de la orden al recibirla."""

    oc_linea_id: int
    cantidad_recibida: Any
    calidad_conforme: bool
    fecha_vencimiento: Any = None
    observacion: str | None = None


def _pendiente_linea(con: sqlite3.Connection, linea: sqlite3.Row) -> int:
    """Pendiente = pedido − aceptado contabilizado − aceptado aún sin contabilizar."""
    en_curso = con.execute(
        """SELECT COALESCE(SUM(rl.cantidad_aceptada_mil), 0) FROM recepcion_lineas rl JOIN recepciones r
             ON r.id = rl.recepcion_id WHERE rl.oc_linea_id = ? AND r.estado = 'RECIBIDA'""",
        (int(linea["id"]),)).fetchone()[0]
    return int(linea["cantidad_mil"]) - int(linea["cantidad_recibida_mil"]) - int(en_curso)


def registrar_recepcion(
    con: sqlite3.Connection, sesion: Sesion, orden_id: int, fecha: Any, documento_proveedor: str,
    lineas: Sequence[LineaRecepcion], observaciones: str | None = None,
) -> int:
    """La Unidad Técnica verifica cantidad y calidad frente a la orden antes del
    estibado (FIN-EGR-04).

    * No se recibe más de lo pendiente en la orden.
    * Lo no conforme se rechaza (observación obligatoria) y no ingresa.
    * Productos veterinarios con vigencia menor al mínimo (30 días) se
      rechazan automáticamente (PC-07 §V.B).

    Returns:
        id del acta de recepción (estado RECIBIDA, o RECHAZADA si nada se aceptó).
    """
    seguridad.exigir_permiso(con, sesion, "inventarios", "CREAR")
    f = a_fecha(fecha)
    documento = _texto(documento_proveedor, "factura o recibo del proveedor", 60)
    if not lineas:
        raise DatosInvalidosError("Registre al menos una línea recibida.")
    vencimiento_min = int(_param(_params(con, f), "vencimiento_min_dias_veterinarios"))
    with transaccion(con):
        orden = _orden(con, orden_id)
        etiqueta = numero_oc(orden)
        if orden["estado"] not in ("EMITIDA", "RECIBIDA_PARCIAL"):
            raise ReglaNegocioError(f"{etiqueta} está {orden['estado']}: solo se reciben órdenes emitidas.",
                                    codigo="OC_NO_EMITIDA")
        if f.isoformat() < orden["fecha_emision"]:
            raise ReglaNegocioError("La recepción no puede ser anterior a la emisión de la orden.",
                                    codigo="RECEPCION_ANTERIOR_A_OC")
        filas = []
        vistos: set[int] = set()
        valor_total = CERO
        for n, l in enumerate(lineas, start=1):
            if not isinstance(l, LineaRecepcion):
                raise DatosInvalidosError(f"Línea {n}: formato no válido.")
            oc_linea = con.execute("""SELECT l.*, i.nombre AS insumo, i.unidad, i.controla_vencimiento FROM oc_lineas l
                                        JOIN insumos i ON i.id = l.insumo_id WHERE l.id = ? AND l.orden_id = ?""",
                                   (_id(l.oc_linea_id, "la línea de la orden"), orden_id)).fetchone()
            if oc_linea is None:
                raise DatosInvalidosError(f"Línea {n}: no pertenece a {etiqueta}.")
            if int(oc_linea["id"]) in vistos:
                raise DatosInvalidosError(f"Línea {n}: repetida.")
            vistos.add(int(oc_linea["id"]))
            pendiente = _pendiente_linea(con, oc_linea)
            recibida = a_mil(l.cantidad_recibida, f"cantidad recibida de {oc_linea['insumo']}", permitir_cero=True)
            if recibida > pendiente:
                raise ReglaNegocioError(
                    f"{oc_linea['insumo']}: se reciben {formato_cantidad(recibida, oc_linea['unidad'])} y la orden solo "
                    f"tiene pendiente {formato_cantidad(pendiente, oc_linea['unidad'])}. El exceso no se recibe.",
                    codigo="RECEPCION_EXCEDE_OC")
            conforme = bool(l.calidad_conforme)
            obs = _opcional(l.observacion, "observación", 300)
            vence = None
            if oc_linea["controla_vencimiento"] and recibida > 0:
                if l.fecha_vencimiento is None:
                    raise DatosInvalidosError(f"{oc_linea['insumo']}: registre la fecha de vencimiento (PC-07).")
                vence = a_fecha(l.fecha_vencimiento, "fecha de vencimiento")
                if (vence - f).days < vencimiento_min:
                    conforme = False
                    obs = ((obs + ". ") if obs else "") + (f"Rechazado: vence el {fecha_pantalla(vence)}, menos de "
                                                           f"{vencimiento_min} días (PC-07 §V.B).")
            if not conforme and recibida > 0 and not obs:
                raise DatosInvalidosError(f"{oc_linea['insumo']}: explique la no conformidad de calidad.")
            aceptada = recibida if conforme else 0
            precio = de_centavos(int(oc_linea["precio_unitario_cent"]))
            if aceptada and aceptada == int(oc_linea["cantidad_mil"]) - int(oc_linea["cantidad_recibida_mil"]):
                ya = con.execute(
                    """SELECT COALESCE(SUM(rl.valor_cent), 0) FROM recepcion_lineas rl JOIN recepciones r
                         ON r.id = rl.recepcion_id WHERE rl.oc_linea_id = ? AND r.estado = 'CONTABILIZADA'""",
                    (int(oc_linea["id"]),)).fetchone()[0]
                valor = de_centavos(int(oc_linea["total_cent"]) - int(ya))
            else:
                valor = redondear_bs(de_mil(aceptada) * precio)
            valor_total += valor
            filas.append((int(oc_linea["id"]), pendiente, recibida, aceptada, int(conforme),
                          vence.isoformat() if vence else None, obs, a_centavos(valor), oc_linea["insumo"]))
        estado = "RECIBIDA" if valor_total > 0 else "RECHAZADA"
        cur = con.execute(
            """INSERT INTO recepciones (orden_id, fecha, documento_proveedor, recibido_por, rol_receptor, observaciones,
                   valor_aceptado_cent, estado, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (orden_id, f.isoformat(), documento, sesion.nombre_usuario, sesion.rol,
             _opcional(observaciones, "observaciones", 500), a_centavos(valor_total), estado, _ahora_iso()))
        rid = int(cur.lastrowid)
        for fila in filas:
            con.execute(
                """INSERT INTO recepcion_lineas (recepcion_id, oc_linea_id, cantidad_pendiente_mil, cantidad_recibida_mil,
                       cantidad_aceptada_mil, calidad_conforme, fecha_vencimiento, observacion, valor_cent)
                   VALUES (?,?,?,?,?,?,?,?,?)""", (rid, *fila[:8]))
        auditoria.registrar(con, sesion, "recepciones", "INSERT", rid,
                            despues={"orden": etiqueta, "fecha": f.isoformat(), "documento": documento,
                                     "valor_aceptado": valor_total, "estado": estado,
                                     "lineas": [{"insumo": x[8], "recibida": de_mil(x[2]), "aceptada": de_mil(x[3]),
                                                 "conforme": bool(x[4])} for x in filas]})
    return rid


def contabilizar_recepcion(con: sqlite3.Connection, sesion: Sesion, recepcion_id: int) -> dict[str, Any]:
    """La Unidad de Finanzas registra la recepción: asiento inventario
    (1.1.03.xx) contra cuenta por pagar (2.1.01.xx), entradas al kárdex con
    recálculo del CPP y alta de la cuenta por pagar con su vencimiento.

    Returns:
        ``{asiento_id, cuenta_por_pagar_id, monto, estado_orden}``.
    """
    seguridad.exigir_permiso(con, sesion, "compras", "CREAR")
    with transaccion(con):
        rec = con.execute("SELECT * FROM recepciones WHERE id = ?", (_id(recepcion_id, "la recepción"),)).fetchone()
        if rec is None:
            raise DatosInvalidosError(f"No existe la recepción {recepcion_id}.")
        if rec["estado"] != "RECIBIDA":
            raise ReglaNegocioError(f"La recepción está {rec['estado']}.", codigo="RECEPCION_NO_PENDIENTE")
        orden = _orden(con, int(rec["orden_id"]))
        etiqueta = numero_oc(orden)
        proveedor = _proveedor(con, int(orden["proveedor_id"]))
        f = a_fecha(rec["fecha"])
        lineas = con.execute(
            """SELECT rl.*, l.insumo_id, i.cuenta_inventario, i.nombre AS insumo FROM recepcion_lineas rl
                 JOIN oc_lineas l ON l.id = rl.oc_linea_id JOIN insumos i ON i.id = l.insumo_id
                WHERE rl.recepcion_id = ? AND rl.cantidad_aceptada_mil > 0 ORDER BY rl.id""", (recepcion_id,)).fetchall()
        por_cuenta: dict[str, Decimal] = {}
        for l in lineas:
            por_cuenta[l["cuenta_inventario"]] = por_cuenta.get(l["cuenta_inventario"], CERO) + \
                de_centavos(int(l["valor_cent"]))
        total = sum(por_cuenta.values(), CERO)
        asiento = contable.crear_asiento(
            con, sesion, f, f"Compra según {etiqueta} a {proveedor['nombre']} — FIN-EGR-04 n.º {recepcion_id}",
            [*({"cuenta": c, "debe": m} for c, m in por_cuenta.items()),
             {"cuenta": orden["cuenta_por_pagar"], "haber": total}],
            "COMPRAS", rec["documento_proveedor"], tipo="TRASPASO")
        for l in lineas:
            inventarios.registrar_entrada_compra(
                con, sesion, int(l["insumo_id"]), f, de_mil(l["cantidad_aceptada_mil"]), de_centavos(int(l["valor_cent"])),
                asiento.id, f"{etiqueta} / {rec['documento_proveedor']}", recepcion_id)
            con.execute("UPDATE oc_lineas SET cantidad_recibida_mil = cantidad_recibida_mil + ? WHERE id = ?",
                        (int(l["cantidad_aceptada_mil"]), int(l["oc_linea_id"])))
        completas = con.execute("SELECT COUNT(*) FROM oc_lineas WHERE orden_id = ? AND cantidad_recibida_mil < "
                                "cantidad_mil", (int(orden["id"]),)).fetchone()[0] == 0
        estado_orden = "RECIBIDA" if completas else "RECIBIDA_PARCIAL"
        vencimiento = f + timedelta(days=int(orden["dias_credito"]))
        cur = con.execute(
            """INSERT INTO cuentas_por_pagar (proveedor_id, orden_id, recepcion_id, cuenta_pasivo, categoria_pago, fecha,
                   vencimiento, monto_cent, documento_ref, estado, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (int(proveedor["id"]), int(orden["id"]), recepcion_id, orden["cuenta_por_pagar"], orden["categoria_pago"],
             f.isoformat(), vencimiento.isoformat(), a_centavos(total), rec["documento_proveedor"], "PENDIENTE",
             _ahora_iso()))
        cxp_id = int(cur.lastrowid)
        con.execute("UPDATE recepciones SET estado = 'CONTABILIZADA', registrado_por = ?, asiento_id = ?, "
                    "contabilizado_en = ? WHERE id = ?", (sesion.nombre_usuario, asiento.id, _ahora_iso(), recepcion_id))
        con.execute("UPDATE ordenes_compra SET estado = ?, actualizado_en = ? WHERE id = ?",
                    (estado_orden, _ahora_iso(), int(orden["id"])))
        auditoria.registrar(con, sesion, "cuentas_por_pagar", "INSERT", cxp_id,
                            despues={"orden": etiqueta, "proveedor": proveedor["nombre"], "monto": total,
                                     "cuenta": orden["cuenta_por_pagar"], "vencimiento": vencimiento.isoformat(),
                                     "asiento_id": asiento.id})
        auditoria.registrar(con, sesion, "ordenes_compra", "UPDATE", int(orden["id"]), antes={"estado": orden["estado"]},
                            despues={"estado": estado_orden})
        _actualizar_compromiso(con, sesion, int(orden["id"]))
    return {"asiento_id": asiento.id, "cuenta_por_pagar_id": cxp_id, "monto": total, "estado_orden": estado_orden}


def listar_recepciones(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    sql = ("SELECT r.*, o.ejercicio, o.numero, p.nombre AS proveedor FROM recepciones r JOIN ordenes_compra o "
           "ON o.id = r.orden_id JOIN proveedores p ON p.id = o.proveedor_id")
    args: tuple[Any, ...] = ()
    if estado:
        sql, args = sql + " WHERE r.estado = ?", (estado,)
    return [{**dict(f), "orden": numero_oc(f), "valor_aceptado": de_centavos(int(f["valor_aceptado_cent"]))}
            for f in con.execute(sql + " ORDER BY r.id DESC", args)]


def obtener_recepcion(con: sqlite3.Connection, sesion: Sesion, recepcion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    rec = con.execute("SELECT * FROM recepciones WHERE id = ?", (recepcion_id,)).fetchone()
    if rec is None:
        raise DatosInvalidosError(f"No existe la recepción {recepcion_id}.")
    orden = _orden(con, int(rec["orden_id"]))
    lineas = [{**dict(l), "pedido": de_mil(l["cantidad_mil"]), "pendiente": de_mil(l["cantidad_pendiente_mil"]),
               "recibida": de_mil(l["cantidad_recibida_mil"]), "aceptada": de_mil(l["cantidad_aceptada_mil"]),
               "valor": de_centavos(int(l["valor_cent"]))}
              for l in con.execute("""SELECT rl.*, l.cantidad_mil, i.nombre AS insumo, i.unidad FROM recepcion_lineas rl
                                        JOIN oc_lineas l ON l.id = rl.oc_linea_id JOIN insumos i ON i.id = l.insumo_id
                                       WHERE rl.recepcion_id = ? ORDER BY rl.id""", (recepcion_id,))]
    return {**dict(rec), "orden": numero_oc(orden), "proveedor": _proveedor(con, int(orden["proveedor_id"]))["nombre"],
            "valor_aceptado": de_centavos(int(rec["valor_aceptado_cent"])), "lineas": lineas}


# ==========================================================================
# 5. CUENTAS POR PAGAR Y PROGRAMACIÓN DE PAGOS (FIN-EGR-05)
# ==========================================================================
def listar_cuentas_por_pagar(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    sql = ("SELECT x.*, p.nombre AS proveedor, o.ejercicio, o.numero FROM cuentas_por_pagar x JOIN proveedores p "
           "ON p.id = x.proveedor_id JOIN ordenes_compra o ON o.id = x.orden_id")
    args: tuple[Any, ...] = ()
    if estado:
        sql, args = sql + " WHERE x.estado = ?", (estado,)
    return [{**dict(f), "orden": numero_oc(f), "monto": de_centavos(int(f["monto_cent"]))}
            for f in con.execute(sql + " ORDER BY x.vencimiento, x.id", args)]


def preparar_programacion(con: sqlite3.Connection, sesion: Sesion, fecha: Any,
                          cuentas_ids: Sequence[int] | None = None) -> int:
    """Programa el pago de las cuentas por pagar según la jerarquía de POL-04
    Art. 2 (``tesoreria.ordenar_pagos``) y el vencimiento; marca las que
    requieren firma mancomunada (sobre el umbral de CI-FIN-09)."""
    seguridad.exigir_permiso(con, sesion, "compras", "CREAR")
    f = a_fecha(fecha)
    if cuentas_ids is None:
        filas = con.execute("SELECT * FROM cuentas_por_pagar WHERE estado IN ('PENDIENTE','PROGRAMADA') "
                            "ORDER BY vencimiento, id").fetchall()
    else:
        filas = [con.execute("SELECT * FROM cuentas_por_pagar WHERE id = ?", (_id(i, "la cuenta por pagar"),)).fetchone()
                 for i in cuentas_ids]
        if any(x is None for x in filas):
            raise DatosInvalidosError("Alguna cuenta por pagar indicada no existe.")
        if any(x["estado"] == "PAGADA" for x in filas):
            raise ReglaNegocioError("No se programa una cuenta ya pagada.", codigo="CXP_PAGADA")
    if not filas:
        raise ReglaNegocioError("No hay cuentas por pagar pendientes para programar.", codigo="SIN_CUENTAS_POR_PAGAR")
    nombres = {int(x["id"]): _proveedor(con, int(x["proveedor_id"]))["nombre"] for x in filas}
    pagos = [tesoreria.PagoPendiente(referencia=str(x["id"]), descripcion=x["documento_ref"],
                                     categoria=x["categoria_pago"], monto=de_centavos(int(x["monto_cent"])),
                                     vencimiento=a_fecha(x["vencimiento"]), beneficiario=nombres[int(x["id"])])
             for x in filas]
    orden = tesoreria.ordenar_pagos(con, pagos, f)
    umbral = redondear_bs(_param(_params(con, f), "firma_mancomunada_sobre_bs"))
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO programaciones_pago (fecha, saldo_inicial_cent, sme_cent, total_pagable_cent, estado,
                   elaborado_por, elaborado_en) VALUES (?,?,?,?,?,?,?)""",
            (f.isoformat(), a_centavos(orden["saldo_inicial"]), a_centavos(orden["sme"]),
             a_centavos(orden["total_pagable"]), "PREPARADA", sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        for p in orden["pagos"]:
            con.execute(
                """INSERT INTO programacion_lineas (programacion_id, cuenta_por_pagar_id, nivel, categoria, monto_cent,
                       vencimiento, requiere_mancomunada, pagable, motivo) VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, int(p["referencia"]), p["nivel"], p["categoria"], a_centavos(p["monto"]), p["vencimiento"],
                 int(p["monto"] > umbral), int(p["pagable"]), p["motivo"] or None))
        auditoria.registrar(con, sesion, "programaciones_pago", "INSERT", pid,
                            despues={"fecha": f.isoformat(), "saldo_inicial": orden["saldo_inicial"],
                                     "total_pagable": orden["total_pagable"], "lineas": len(orden["pagos"])})
    return pid


def autorizar_programacion(con: sqlite3.Connection, sesion: Sesion, programacion_id: int) -> None:
    """La Gerencia General autoriza la programación (FIN-EGR-05). Las cuentas
    pagables quedan PROGRAMADAS; la programación anterior queda sustituida."""
    seguridad.exigir_permiso(con, sesion, "compras", "AUTORIZAR")
    with transaccion(con):
        prog = con.execute("SELECT * FROM programaciones_pago WHERE id = ?", (programacion_id,)).fetchone()
        if prog is None:
            raise DatosInvalidosError(f"No existe la programación {programacion_id}.")
        if prog["estado"] != "PREPARADA":
            raise ReglaNegocioError(f"La programación está {prog['estado']}.", codigo="PROGRAMACION_NO_PREPARADA")
        if con.execute("SELECT 1 FROM programacion_lineas pl JOIN cuentas_por_pagar x ON x.id = pl.cuenta_por_pagar_id "
                       "WHERE pl.programacion_id = ? AND x.estado = 'PAGADA'", (programacion_id,)).fetchone():
            raise ReglaNegocioError("La programación incluye cuentas ya pagadas: prepare una nueva.",
                                    codigo="PROGRAMACION_DESACTUALIZADA")
        for anterior in con.execute("SELECT id FROM programaciones_pago WHERE estado IN ('PREPARADA','AUTORIZADA') "
                                    "AND id <> ?", (programacion_id,)).fetchall():
            con.execute("UPDATE programaciones_pago SET estado = 'SUSTITUIDA' WHERE id = ?", (anterior[0],))
            auditoria.registrar(con, sesion, "programaciones_pago", "UPDATE", anterior[0], despues={"estado": "SUSTITUIDA"})
        con.execute("UPDATE cuentas_por_pagar SET estado = 'PENDIENTE' WHERE estado = 'PROGRAMADA'")
        con.execute(
            """UPDATE cuentas_por_pagar SET estado = 'PROGRAMADA' WHERE id IN (SELECT cuenta_por_pagar_id
                 FROM programacion_lineas WHERE programacion_id = ? AND pagable = 1)""", (programacion_id,))
        con.execute("UPDATE programaciones_pago SET estado = 'AUTORIZADA', autorizado_por = ?, autorizado_en = ? "
                    "WHERE id = ?", (sesion.nombre_usuario, _ahora_iso(), programacion_id))
        auditoria.registrar(con, sesion, "programaciones_pago", "AUTORIZACION", programacion_id,
                            antes={"estado": "PREPARADA"}, despues={"estado": "AUTORIZADA"})


def obtener_programacion(con: sqlite3.Connection, sesion: Sesion, programacion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    prog = con.execute("SELECT * FROM programaciones_pago WHERE id = ?", (programacion_id,)).fetchone()
    if prog is None:
        raise DatosInvalidosError(f"No existe la programación {programacion_id}.")
    lineas = [{**dict(l), "monto": de_centavos(int(l["monto_cent"])), "orden": numero_oc(l),
               "nombre_nivel": tesoreria.ETIQUETA_JERARQUIA.get(l["categoria"], l["categoria"])}
              for l in con.execute(
                  """SELECT pl.*, x.estado AS estado_cxp, x.documento_ref, p.nombre AS proveedor, o.ejercicio, o.numero
                       FROM programacion_lineas pl JOIN cuentas_por_pagar x ON x.id = pl.cuenta_por_pagar_id
                       JOIN proveedores p ON p.id = x.proveedor_id JOIN ordenes_compra o ON o.id = x.orden_id
                      WHERE pl.programacion_id = ? ORDER BY pl.nivel, pl.vencimiento, pl.id""", (programacion_id,))]
    return {**dict(prog), "saldo_inicial": de_centavos(int(prog["saldo_inicial_cent"])),
            "sme": de_centavos(int(prog["sme_cent"])), "total_pagable": de_centavos(int(prog["total_pagable_cent"])),
            "lineas": lineas}


def listar_programaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    return [{**dict(f), "total_pagable": de_centavos(int(f["total_pagable_cent"]))}
            for f in con.execute("SELECT * FROM programaciones_pago ORDER BY id DESC")]


def pagar_cuenta_por_pagar(
    con: sqlite3.Connection, sesion: Sesion, cuenta_id: int, fecha: Any, cuenta_banco: str, *,
    autorizacion_id: int | None = None, nro_cheque: str | None = None,
) -> int:
    """Paga una cuenta programada y pagable en la programación autorizada,
    respetando la jerarquía (no se paga un nivel inferior mientras uno superior
    de la misma programación siga impago). El desembolso lo registra
    ``tesoreria.registrar_pago`` con la autorización CI-FIN-09 (GG sobre
    Bs 1.000; firma mancomunada GG + CV sobre Bs 5.000).

    Returns:
        id del movimiento bancario.
    """
    seguridad.exigir_permiso(con, sesion, "compras", "CREAR")
    f = a_fecha(fecha)
    with transaccion(con):
        cxp = con.execute("SELECT * FROM cuentas_por_pagar WHERE id = ?", (_id(cuenta_id, "la cuenta por pagar"),)
                          ).fetchone()
        if cxp is None:
            raise DatosInvalidosError(f"No existe la cuenta por pagar {cuenta_id}.")
        if cxp["estado"] == "PAGADA":
            raise ReglaNegocioError("La cuenta por pagar ya está pagada.", codigo="CXP_PAGADA")
        linea = con.execute(
            """SELECT pl.* FROM programacion_lineas pl JOIN programaciones_pago pp ON pp.id = pl.programacion_id
                WHERE pp.estado = 'AUTORIZADA' AND pl.cuenta_por_pagar_id = ?""", (cuenta_id,)).fetchone()
        if cxp["estado"] != "PROGRAMADA" or linea is None or not linea["pagable"]:
            raise AutorizacionError("La cuenta no está programada como pagable en la programación de pagos autorizada "
                                    "por la Gerencia General (FIN-EGR-05).", codigo="CXP_NO_PROGRAMADA")
        superiores = con.execute(
            """SELECT COUNT(*) FROM programacion_lineas pl JOIN cuentas_por_pagar x ON x.id = pl.cuenta_por_pagar_id
                WHERE pl.programacion_id = ? AND pl.pagable = 1 AND pl.nivel < ? AND x.estado <> 'PAGADA'""",
            (int(linea["programacion_id"]), int(linea["nivel"]))).fetchone()[0]
        if superiores:
            raise ReglaNegocioError("Jerarquía de pagos (POL-04 Art. 2): primero se pagan las cuentas de mayor "
                                    "prioridad de la programación.", codigo="JERARQUIA_PAGOS")
        orden = _orden(con, int(cxp["orden_id"]))
        proveedor = _proveedor(con, int(cxp["proveedor_id"]))
        monto = de_centavos(int(cxp["monto_cent"]))
        mid = tesoreria.registrar_pago(
            con, sesion, f, monto, cuenta_banco, cxp["cuenta_pasivo"],
            f"Pago a {proveedor['nombre']} — {numero_oc(orden)} / {cxp['documento_ref']}", proveedor["nombre"],
            autorizacion_id=autorizacion_id, nro_cheque=nro_cheque, documento_ref=f"CxP-{cuenta_id}",
            categoria_pago=cxp["categoria_pago"])
        con.execute("UPDATE cuentas_por_pagar SET estado = 'PAGADA', movimiento_bancario_id = ?, pagado_en = ? "
                    "WHERE id = ?", (mid, f.isoformat(), cuenta_id))
        auditoria.registrar(con, sesion, "cuentas_por_pagar", "UPDATE", cuenta_id, antes={"estado": cxp["estado"]},
                            despues={"estado": "PAGADA", "movimiento_bancario_id": mid})
        _actualizar_compromiso(con, sesion, int(orden["id"]))
    return mid


# ==========================================================================
# 6. INDICADOR CAC (Mapa de Procesos Integral PO-01; MAN-05 PR-FIN-03 §7)
# ==========================================================================
def semaforo_cac(con: sqlite3.Connection, fecha: Any, porcentaje: Decimal) -> str:
    """Malo < 90 %; Normal 90 % a 99 %; Bueno = 100 % (umbrales en parámetros)."""
    p = _params(con, fecha)
    bueno = Decimal(_param(p, "cac_umbral_bueno")) * 100
    malo = Decimal(_param(p, "cac_umbral_malo")) * 100
    if porcentaje >= bueno:
        return "BUENO"
    if porcentaje < malo:
        return "MALO"
    return "NORMAL"


def indicador_cac(con: sqlite3.Connection, anio: int, mes: int) -> dict[str, Any]:
    """CAC = órdenes emitidas puntualmente (el lunes programado) / órdenes
    programadas del mes × 100. Las compras de emergencia no son programadas y
    las anuladas no cuentan."""
    if not (isinstance(anio, int) and isinstance(mes, int) and 1 <= mes <= 12):
        raise DatosInvalidosError("Indique un año y un mes válidos.")
    inicio = date(anio, mes, 1)
    fin = (date(anio + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1))
    filas = con.execute(
        """SELECT COUNT(*), COALESCE(SUM(emitida_puntual = 1), 0) FROM ordenes_compra
            WHERE es_emergencia = 0 AND estado <> 'ANULADA' AND fecha_programada BETWEEN ? AND ?""",
        (inicio.isoformat(), fin.isoformat())).fetchone()
    programadas, puntuales = int(filas[0]), int(filas[1])
    if programadas == 0:
        return {"anio": anio, "mes": mes, "programadas": 0, "puntuales": 0, "porcentaje": None, "semaforo": None}
    pct = (Decimal(puntuales) * 100 / Decimal(programadas)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"anio": anio, "mes": mes, "programadas": programadas, "puntuales": puntuales, "porcentaje": pct,
            "semaforo": semaforo_cac(con, inicio, pct)}


def serie_cac(con: sqlite3.Connection, desde: Any, hasta: Any) -> list[dict[str, Any]]:
    """CAC mensual entre dos fechas."""
    d, h = a_fecha(desde), a_fecha(hasta)
    anio, mes, resultado = d.year, d.month, []
    while (anio, mes) <= (h.year, h.month):
        resultado.append(indicador_cac(con, anio, mes))
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return resultado


def resumen_compras(con: sqlite3.Connection, sesion: Sesion) -> dict[str, Any]:
    """Cifras de cabecera de la pantalla."""
    seguridad.exigir_permiso(con, sesion, "compras", "VER")
    pend = con.execute("SELECT COUNT(*), COALESCE(SUM(monto_cent), 0) FROM cuentas_por_pagar WHERE estado <> 'PAGADA'"
                       ).fetchone()
    return {
        "ordenes_preparadas": int(con.execute("SELECT COUNT(*) FROM ordenes_compra WHERE estado = 'PREPARADA'")
                                  .fetchone()[0]),
        "ordenes_abiertas": int(con.execute("SELECT COUNT(*) FROM ordenes_compra WHERE estado IN "
                                            "('EMITIDA','RECIBIDA_PARCIAL')").fetchone()[0]),
        "cuentas_por_pagar": int(pend[0]), "saldo_por_pagar": de_centavos(int(pend[1])),
        "alertas_abiertas": int(con.execute("SELECT COUNT(*) FROM alertas_stock WHERE estado IN "
                                            "('ABIERTA','EMERGENCIA_AUTORIZADA')").fetchone()[0]),
    }


def detalle_json(texto: str | None) -> Any:
    """Decodifica el JSON guardado de un reporte FIN-EGR-02."""
    return json.loads(texto) if texto else None
