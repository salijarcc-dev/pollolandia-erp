"""Ventas, HORECA y cobranza del ERP Pollolandia (Fase 9, Ciclo 4 — Ingresos).

Implementa POL-05 (créditos a clientes), PC-08 (reconocimiento de ingresos y
devoluciones), CI-FIN-06 (precios y márgenes), CI-FIN-13 (pre-venta del lote),
CI-FIN-15 (cobranza HORECA), PR-FIN-08 y PR-FIN-09, con los formatos
FIN-ING-01 a FIN-ING-08 y CIR-ING-01:

1. Fichas de clientes HORECA, friales, mercado popular y agricultores.
2. Tarifario oficial con aprobación conjunta GG + CA (en acta); toda nota de
   entrega usa el precio vigente; análisis mensual de margen por canal y
   obligación de ajuste tras ``margen_meses_consecutivos`` meses bajo
   ``margen_bruto_canal_min``.
3. Pre-venta del lote: pedido HORECA con ``preventa_dias_min`` días de
   antelación (FIN-ING-01), pedidos indicativos con al menos
   ``preventa_friales_min`` friales, verificación de cobertura total el día
   anterior y canal de respaldo activado por la GG; el despacho no se cierra
   sin cobertura completa.
4. Notas de entrega HORECA y contado (FIN-ING-03/06) con comprobante
   (FIN-ING-04 en el crédito) y exhibición del Certificado RAU; crédito HORECA
   según el ejercicio (``horeca_credito_dias_max``), sin cheques diferidos;
   desde 2028 crédito > 1 día con resolución escrita (FIN-ING-02) y los tres
   requisitos de POL-05 Art. 2.
5. Asientos automáticos: venta (5.1.01.01 carne; 5.1.02.01 alimento;
   5.2.01.04 gallinaza) y costo de ventas al CPP (Fase 7).
6. Cuentas por cobrar (FIN-ING-05), reporte de vencidos a la GG en
   ``cobranza_reporte_horas``, suspensión automática tras
   ``credito_incumplimientos_suspension`` incumplimientos (CIR-ING-01) y
   provisión de incobrables desde 2028.
7. Devoluciones por rechazo de inocuidad (5.1.04.01), parte diario de
   recaudación con depósito (FIN-ING-07).

No importa ``streamlit``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion
from servicios import auditoria, contable, costos, seguridad, tesoreria
from servicios import rau as servicio_rau
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil
from servicios.seguridad import Sesion, TipoDecision
from utils.formato import a_fecha, fecha_pantalla, formato_bs, redondear_bs

# --------------------------------------------------------------------------
# Estructura (Plan de Cuentas v3, POL-05, formatos FIN-ING); no cifras
# --------------------------------------------------------------------------
TIPOS_CLIENTE = {"HORECA": "HORECA (hoteles, restaurantes, catering)", "FRIAL": "Frial",
                 "MERCADO_POPULAR": "Mercado popular / comprador habitual", "AGRICULTOR": "Agricultor (gallinaza)",
                 "OTRO": "Otro comprador"}
PREFIJO_CLIENTE = {"HORECA": "HOR", "FRIAL": "FRI", "MERCADO_POPULAR": "MER", "AGRICULTOR": "AGR", "OTRO": "OTR"}
CANALES = ("HORECA", "FRIAL", "MERCADO_POPULAR")
NOMBRE_CANAL = {"HORECA": "HORECA", "FRIAL": "Friales", "MERCADO_POPULAR": "Mercado popular", "TODOS": "Todos",
                "OTROS": "Otros compradores"}
PRODUCTOS = {"CARNE_ENTERO": "Carne de pollo (entero)", "CARNE_CORTES": "Carne de pollo (cortes)",
             "EXCEDENTE": "Alimento balanceado excedente", "GALLINAZA": "Gallinaza (abono orgánico)"}
UNIDAD = {"CARNE_ENTERO": "kg", "CARNE_CORTES": "kg", "EXCEDENTE": "qq", "GALLINAZA": "carga"}
CARNE = ("CARNE_ENTERO", "CARNE_CORTES")
CUENTA_INGRESO = {"CARNE_ENTERO": "5.1.01.01", "CARNE_CORTES": "5.1.01.01", "EXCEDENTE": "5.1.02.01",
                  "GALLINAZA": "5.2.01.04"}
PRODUCTO_COSTO = {"CARNE_ENTERO": "CARNE", "CARNE_CORTES": "CARNE", "EXCEDENTE": "EXCEDENTE"}
CUENTA_CXC = "1.1.02.01"
CUENTA_DEVOLUCIONES = "5.1.04.01"
# DISCREPANCIA: PC-08 anula el ingreso de la devolución pero no dice qué
# cuenta recibe el importe ya cobrado. Se registra como saldo a favor del
# cliente en 2.1.04.01 (Anticipos de Clientes HORECA Recibidos), que se
# reembolsa por Tesorería o se aplica a la próxima entrega.
CUENTA_SALDO_FAVOR = "2.1.04.01"
CUENTA_PROVISION = "4.1.01.01"
CUENTA_GASTO_INCOBRABLES = "7.1.02.08"
CAJA = tesoreria.CAJA_MN
BANCO = "1.1.01.03"
# Medios de pago aceptados (POL-05 Art. 1; PR-FIN-08 paso 6; PR-FIN-09 paso 3).
# El cheque diferido no figura: lo prohíbe ``cheques_diferidos_permitidos``.
MEDIOS_HORECA = ("EFECTIVO", "TRANSFERENCIA", "DEPOSITO", "CHEQUE_NOMINATIVO")
MEDIOS_CONTADO = ("EFECTIVO", "TRANSFERENCIA", "QR_ACH")
NOMBRE_MEDIO = {"EFECTIVO": "Efectivo", "TRANSFERENCIA": "Transferencia", "QR_ACH": "QR / ACH",
                "DEPOSITO": "Depósito", "CHEQUE_NOMINATIVO": "Cheque nominativo", "CHEQUE_DIFERIDO": "Cheque diferido"}
ORIGEN = "VENTAS"
CERO = Decimal("0.00")
CIEN = Decimal(100)
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


def _entero(valor: Any, campo: str, minimo: int, maximo: int = 10**9) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        raise DatosInvalidosError(f"El campo '{campo}' debe ser un entero entre {minimo} y {maximo}.")
    return valor


def _monto(valor: Any, campo: str) -> Decimal:
    if valor is None or isinstance(valor, bool):
        raise DatosInvalidosError(f"Indique el {campo}.")
    try:
        importe = redondear_bs(valor)
    except Exception as exc:  # noqa: BLE001 - se traduce a un mensaje claro
        raise DatosInvalidosError(f"El {campo} no es un número válido.") from exc
    if importe <= 0:
        raise DatosInvalidosError(f"El {campo} debe ser mayor a cero.")
    return importe


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _exigir_finanzas(con: sqlite3.Connection, sesion: Sesion, accion: str = "CREAR") -> None:
    seguridad.exigir_permiso(con, sesion, "ventas", accion)
    if sesion.rol != "JF":
        raise AutorizacionError("La comercialización la registra la Unidad de Finanzas y Comercialización.",
                                codigo="SOLO_FINANZAS")


def _exigir_gerencia(con: sqlite3.Connection, sesion: Sesion) -> None:
    seguridad.exigir_permiso(con, sesion, "ventas", "AUTORIZAR")
    if sesion.rol != "GG":
        raise AutorizacionError("Corresponde a la Gerencia General.", codigo="SOLO_GERENCIA")


def pct_texto(valor: Any) -> str:
    if valor is None:
        return "—"
    return f"{(Decimal(str(valor)) * CIEN).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} %".replace(".", ",")


def cantidad_texto(cantidad: Any, unidad: str) -> str:
    texto = f"{Decimal(str(cantidad)).normalize():f}".replace(".", ",")
    return f"{texto} {unidad}"


def canal_de(tipo_cliente: str) -> str:
    """Canal del cliente para el tarifario y el margen (CI-FIN-06 Art. 5)."""
    return tipo_cliente if tipo_cliente in CANALES else "OTROS"


# ==========================================================================
# 1. CLIENTES (ficha)
# ==========================================================================
def _cliente(con: sqlite3.Connection, cliente_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el cliente {cliente_id}.")
    return fila


def registrar_cliente(con: sqlite3.Connection, sesion: Sesion, tipo: str, nombre: str, fecha_alta: Any, *,
                      documento: str | None = None, contacto: str | None = None, telefono: str | None = None,
                      direccion: str | None = None, es_socio: bool = False,
                      observacion: str | None = None) -> dict[str, Any]:
    """Abre la ficha del cliente con código correlativo por tipo."""
    _exigir_finanzas(con, sesion)
    if tipo not in TIPOS_CLIENTE:
        raise DatosInvalidosError(f"Tipo de cliente no válido. Use: {', '.join(TIPOS_CLIENTE)}.")
    nom = _texto(nombre, "nombre", 200)
    doc = _opcional(documento, "NIT o C.I.", 30)
    f = a_fecha(fecha_alta)
    if doc and con.execute("SELECT 1 FROM clientes WHERE documento = ?", (doc,)).fetchone():
        raise ReglaNegocioError(f"Ya existe un cliente con el documento {doc}.", codigo="CLIENTE_DUPLICADO")
    n = int(con.execute("SELECT COUNT(*) FROM clientes WHERE tipo = ?", (tipo,)).fetchone()[0]) + 1
    codigo = f"CLI-{PREFIJO_CLIENTE[tipo]}-{n:03d}"
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO clientes (codigo, tipo, nombre, documento, contacto, telefono, direccion, es_socio,
                   fecha_alta, observacion, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (codigo, tipo, nom, doc, _opcional(contacto, "contacto", 120), _opcional(telefono, "teléfono", 40),
             _opcional(direccion, "dirección", 200), int(bool(es_socio)), f.isoformat(),
             _opcional(observacion, "observación", 500), sesion.nombre_usuario, _ahora_iso()))
        cid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "clientes", "INSERT", cid,
                            despues={"codigo": codigo, "tipo": tipo, "nombre": nom, "documento": doc})
    return {"id": cid, "codigo": codigo}


def listar_clientes(con: sqlite3.Connection, sesion: Sesion, tipo: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    sql = "SELECT * FROM clientes"
    args: tuple[Any, ...] = ()
    if tipo is not None:
        sql += " WHERE tipo = ?"
        args = (tipo,)
    return [dict(c) for c in con.execute(sql + " ORDER BY codigo", args)]


def primera_compra(con: sqlite3.Connection, cliente_id: int) -> date | None:
    fila = con.execute("SELECT MIN(fecha) FROM notas_entrega WHERE cliente_id = ?", (cliente_id,)).fetchone()
    return a_fecha(fila[0]) if fila and fila[0] else None


def meses_historial(con: sqlite3.Connection, cliente_id: int, fecha: Any) -> int:
    """Meses completos desde la primera entrega al cliente hasta la fecha."""
    inicio = primera_compra(con, cliente_id)
    if inicio is None:
        return 0
    f = a_fecha(fecha)
    meses = (f.year - inicio.year) * 12 + (f.month - inicio.month)
    if f.day < inicio.day:
        meses -= 1
    return max(meses, 0)


def entrega_semanal(con: sqlite3.Connection, cliente_id: int, fecha: Any) -> Decimal:
    """Promedio de lo entregado por semana con entregas, en las 26 semanas
    previas a la fecha (base del monto máximo de crédito, POL-05 Art. 2)."""
    f = a_fecha(fecha)
    desde = f - timedelta(weeks=26)
    semanas: dict[str, int] = {}
    for n in con.execute("SELECT fecha, total_cent FROM notas_entrega WHERE cliente_id = ? AND fecha > ? AND fecha <= ?",
                         (cliente_id, desde.isoformat(), f.isoformat())):
        iso = a_fecha(n["fecha"]).isocalendar()
        clave = f"{iso[0]}-{iso[1]:02d}"
        semanas[clave] = semanas.get(clave, 0) + int(n["total_cent"])
    if not semanas:
        return CERO
    return redondear_bs(de_centavos(sum(semanas.values())) / len(semanas))


def ficha_cliente(con: sqlite3.Connection, sesion: Sesion, cliente_id: int, fecha: Any = None) -> dict[str, Any]:
    """Ficha con historial comercial, cartera e incumplimientos."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    c = _cliente(con, cliente_id)
    f = a_fecha(fecha) if fecha is not None else date.today()
    notas = con.execute("SELECT COUNT(*), COALESCE(SUM(total_cent), 0) FROM notas_entrega WHERE cliente_id = ?",
                        (cliente_id,)).fetchone()
    pendiente = sum((saldo_cuenta(x) for x in _cuentas_cliente(con, cliente_id) if x["estado"] == "PENDIENTE"), CERO)
    return {**dict(c), "primera_compra": primera_compra(con, cliente_id), "historial_meses":
            meses_historial(con, cliente_id, f), "entregas": int(notas[0]), "total_comprado": de_centavos(int(notas[1])),
            "entrega_semanal": entrega_semanal(con, cliente_id, f), "cxc_pendiente": pendiente,
            "incumplimientos": incumplimientos_consecutivos(con, cliente_id),
            "suspensiones": [dict(s) for s in con.execute(
                "SELECT * FROM suspensiones_credito WHERE cliente_id = ? ORDER BY id", (cliente_id,))]}


# ==========================================================================
# 2. TARIFARIO (CI-FIN-06)
# ==========================================================================
def costo_referencia(con: sqlite3.Connection, fecha: Any) -> tuple[Decimal, str, str]:
    """Costo unitario Bs/kg de referencia: el del último ciclo cerrado
    (FIN-TRA-07, CI-FIN-06 Art. 6). Sin ciclo cerrado, el presupuestado D.4
    del ciclo 1 del ejercicio (costo ÷ aves × rendimiento).

    # DISCREPANCIA: CI-FIN-06 Art. 6 prohíbe fijar precios sin el costo del
    # último ciclo cerrado, pero el primer tarifario se aprueba antes de cerrar
    # el primer ciclo. Para ese caso se usa el costo presupuestado D.4
    # (Bs 116.286 ÷ 7.800 kg = Bs 14,91/kg en 2026) y se deja indicado.
    """
    f = a_fecha(fecha)
    fila = con.execute(
        """SELECT cc.costo_unitario, c.numero, c.ejercicio FROM costos_ciclo cc
             JOIN ciclos_produccion c ON c.id = cc.ciclo_id
            WHERE cc.fecha_cierre <= ? ORDER BY cc.fecha_cierre DESC, cc.id DESC LIMIT 1""", (f.isoformat(),)).fetchone()
    if fila is not None:
        return (Decimal(fila["costo_unitario"]), "CICLO_CERRADO",
                f"Ciclo {fila['numero']} del ejercicio {fila['ejercicio']} (FIN-TRA-07)")
    ejercicio = ejercicio_de_fecha_bd(con, f)
    p = obtener_parametros_vigentes(con, ejercicio)
    total = con.execute("SELECT COALESCE(SUM(monto_cent), 0) FROM presupuesto_costos WHERE ejercicio = ? AND ciclo = 1 "
                        "AND centro_costo = ?", (ejercicio, costos.CENTRO_CARNE)).fetchone()[0]
    kg = Decimal(int(_param(p, "aves_por_ciclo"))) * Decimal(str(_param(p, "rendimiento_kg_ave")))
    if not total or kg <= 0:
        raise ReglaNegocioError("No hay costo de ciclo cerrado ni presupuesto D.4 para fijar precios (CI-FIN-06 "
                                "Art. 6).", codigo="SIN_COSTO_REFERENCIA")
    return (redondear_bs(de_centavos(int(total)) / kg), "PRESUPUESTO_D4",
            f"Presupuesto D.4 del ejercicio {ejercicio} (sin ciclo cerrado)")


def proponer_tarifario(con: sqlite3.Connection, sesion: Sesion, vigente_desde: Any,
                       lineas: list[tuple[str, str, Any]], motivo: str) -> dict[str, Any]:
    """La Gerencia General propone el tarifario (producto, canal, precio) con
    el costo de referencia; queda vigente cuando el CA lo aprueba en acta
    (aprobación conjunta, CI-FIN-06 Art. 8)."""
    _exigir_gerencia(con, sesion)
    f = a_fecha(vigente_desde)
    texto = _texto(motivo, "motivo", 500)
    if not lineas:
        raise DatosInvalidosError("El tarifario debe tener al menos un precio.")
    vistos = set()
    normalizadas = []
    for producto, canal, precio in lineas:
        if producto not in PRODUCTOS:
            raise DatosInvalidosError(f"Producto no válido: {producto!r}.")
        if canal not in (*CANALES, "TODOS"):
            raise DatosInvalidosError(f"Canal no válido: {canal!r}.")
        if (producto, canal) in vistos:
            raise DatosInvalidosError(f"Precio repetido para {PRODUCTOS[producto]} en {NOMBRE_CANAL[canal]}.")
        vistos.add((producto, canal))
        normalizadas.append((producto, canal, _monto(precio, f"precio de {PRODUCTOS[producto]}")))
    costo, origen, _ = costo_referencia(con, f)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO tarifarios (vigente_desde, motivo, costo_referencia, origen_costo, propuesto_por, propuesto_en)
               VALUES (?,?,?,?,?,?)""", (f.isoformat(), texto, str(costo), origen, sesion.nombre_usuario, _ahora_iso()))
        tid = int(cur.lastrowid)
        for producto, canal, precio in normalizadas:
            con.execute("INSERT INTO tarifario_lineas (tarifario_id, producto, canal, unidad, precio_cent) "
                        "VALUES (?,?,?,?,?)", (tid, producto, canal, UNIDAD[producto], a_centavos(precio)))
        auditoria.registrar(con, sesion, "tarifarios", "INSERT", tid,
                            despues={"vigente_desde": f.isoformat(), "costo_referencia": costo, "origen": origen,
                                     "lineas": [(p, c, str(v)) for p, c, v in normalizadas]})
    return {"id": tid, "costo_referencia": costo, "origen_costo": origen}


def resolver_tarifario(con: sqlite3.Connection, sesion: Sesion, tarifario_id: int, decision: str, acta_ref: str,
                       observacion: str | None = None) -> str:
    """El Consejo de Administración aprueba o rechaza el tarifario propuesto."""
    seguridad.exigir_permiso(con, sesion, "ventas", "APROBAR")
    if sesion.rol != "CA":
        raise AutorizacionError("El tarifario lo aprueba el Consejo de Administración con la Gerencia (CI-FIN-06 "
                                "Art. 8).", codigo="SOLO_CA")
    if decision not in ("APRUEBA", "RECHAZA"):
        raise DatosInvalidosError("La decisión debe ser APRUEBA o RECHAZA.")
    t = _tarifario(con, tarifario_id)
    if t["estado"] != "PROPUESTO":
        raise ReglaNegocioError(f"El tarifario ya está {t['estado'].lower()}.", codigo="TARIFARIO_RESUELTO")
    acta = _texto(acta_ref, "acta del Consejo de Administración", 120)
    nuevo = "APROBADO" if decision == "APRUEBA" else "RECHAZADO"
    with transaccion(con):
        con.execute("UPDATE tarifarios SET estado = ?, aprobado_ca_por = ?, acta_ca = ?, aprobado_en = ?, "
                    "observacion_ca = ? WHERE id = ?",
                    (nuevo, sesion.nombre_usuario, acta, _ahora_iso(), _opcional(observacion, "observación", 500),
                     tarifario_id))
        auditoria.registrar(con, sesion, "tarifarios", "AUTORIZACION", tarifario_id, antes={"estado": "PROPUESTO"},
                            despues={"estado": nuevo, "acta": acta})
    return nuevo


def _tarifario(con: sqlite3.Connection, tarifario_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM tarifarios WHERE id = ?", (tarifario_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el tarifario {tarifario_id}.")
    return fila


def tarifario_vigente(con: sqlite3.Connection, fecha: Any) -> sqlite3.Row | None:
    """Último tarifario aprobado con vigencia hasta la fecha."""
    return con.execute("SELECT * FROM tarifarios WHERE estado = 'APROBADO' AND vigente_desde <= ? "
                       "ORDER BY vigente_desde DESC, id DESC LIMIT 1", (a_fecha(fecha).isoformat(),)).fetchone()


def precio_vigente(con: sqlite3.Connection, producto: str, canal: str, fecha: Any) -> tuple[Decimal, int]:
    """Precio del tarifario vigente para el producto y canal (o «todos los
    canales»). Ninguna venta se entrega a otro precio (CI-FIN-06 Art. 9)."""
    t = tarifario_vigente(con, fecha)
    if t is None:
        raise ReglaNegocioError("No hay tarifario aprobado por la GG y el CA vigente a la fecha (CI-FIN-06 Art. 8).",
                                codigo="SIN_TARIFARIO")
    for c in (canal, "TODOS"):
        fila = con.execute("SELECT precio_cent FROM tarifario_lineas WHERE tarifario_id = ? AND producto = ? AND canal = ?",
                           (t["id"], producto, c)).fetchone()
        if fila is not None:
            return de_centavos(int(fila[0])), int(t["id"])
    raise ReglaNegocioError(f"El tarifario vigente no fija precio de {PRODUCTOS[producto].lower()} para el canal "
                            f"{NOMBRE_CANAL.get(canal, canal)}.", codigo="SIN_PRECIO")


def detalle_tarifario(con: sqlite3.Connection, sesion: Sesion, tarifario_id: int) -> dict[str, Any]:
    """Tarifario con su margen bruto proyectado por línea de carne."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    t = _tarifario(con, tarifario_id)
    costo = Decimal(t["costo_referencia"])
    minimo = Decimal(str(_param(_params(con, t["vigente_desde"]), "margen_bruto_canal_min")))
    lineas = []
    for l in con.execute("SELECT * FROM tarifario_lineas WHERE tarifario_id = ? ORDER BY producto, canal",
                         (tarifario_id,)):
        precio = de_centavos(int(l["precio_cent"]))
        margen = ((precio - costo) / precio).quantize(TASA, rounding=ROUND_HALF_UP) if l["producto"] in CARNE else None
        lineas.append({**dict(l), "precio": precio, "margen": margen,
                       "bajo_umbral": margen is not None and margen < minimo})
    return {**dict(t), "costo": costo, "margen_minimo": minimo, "lineas": lineas}


def listar_tarifarios(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [dict(t) for t in con.execute("SELECT * FROM tarifarios ORDER BY vigente_desde DESC, id DESC")]


def lineas_desde_presupuesto(con: sqlite3.Connection, fecha: Any) -> list[tuple[str, str, Decimal]]:
    """Precios del Manual Presupuestario §3.1 como punto de partida de la
    propuesta (carne, alimento excedente desde su ejercicio y gallinaza)."""
    p = _params(con, fecha)
    lineas: list[tuple[str, str, Decimal]] = [("CARNE_ENTERO", "TODOS", Decimal(str(_param(p, "precio_carne_kg"))))]
    if "precio_alimento_qq" in p:
        lineas.append(("EXCEDENTE", "TODOS", Decimal(str(p["precio_alimento_qq"]))))
    lineas.append(("GALLINAZA", "TODOS", Decimal(str(_param(p, "precio_gallinaza_carga")))))
    return lineas


# ==========================================================================
# 3. PRE-VENTA DEL LOTE: DESPACHO, PEDIDOS Y COBERTURA (CI-FIN-13)
# ==========================================================================
def _despacho(con: sqlite3.Connection, despacho_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM despachos WHERE id = ?", (despacho_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el despacho {despacho_id}.")
    return fila


def aves_disponibles_lote(con: sqlite3.Connection, lote_id: int) -> int:
    """Aves iniciales del lote menos las muertas de la bitácora."""
    lote = con.execute("SELECT aves_iniciales FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    if lote is None:
        raise DatosInvalidosError(f"No existe el lote {lote_id}.")
    muertas = con.execute("SELECT COALESCE(SUM(aves_muertas), 0) FROM bitacora_diaria WHERE lote_id = ?",
                          (lote_id,)).fetchone()[0]
    return int(lote["aves_iniciales"]) - int(muertas)


def lotes_sin_despacho(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Lotes de crianza que aún no tienen despacho, con sus aves disponibles."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [{**dict(l), "aves_disponibles": aves_disponibles_lote(con, int(l["id"]))} for l in con.execute(
        "SELECT l.*, c.ejercicio, c.numero AS ciclo FROM lotes l JOIN ciclos_produccion c ON c.id = l.ciclo_id "
        "WHERE l.id NOT IN (SELECT lote_id FROM despachos) ORDER BY l.codigo")]


def crear_despacho(con: sqlite3.Connection, sesion: Sesion, lote_id: int, fecha_faenado: Any = None,
                   aves: int | None = None) -> dict[str, Any]:
    """Abre la pre-venta del lote de faenado. La fecha se toma de la circular
    CIR-EGR-01 del lote si existe; si no, se indica. Las aves por defecto son
    las vivas según la bitácora."""
    _exigir_finanzas(con, sesion)
    lote = con.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    if lote is None:
        raise DatosInvalidosError(f"No existe el lote {lote_id}.")
    if con.execute("SELECT 1 FROM despachos WHERE lote_id = ?", (lote_id,)).fetchone():
        raise ReglaNegocioError(f"El lote {lote['codigo']} ya tiene despacho.", codigo="DESPACHO_EXISTENTE")
    circular = con.execute("SELECT fecha_faenado FROM circulares_cierre WHERE lote = ? ORDER BY id DESC LIMIT 1",
                           (lote["codigo"],)).fetchone()
    if circular is not None:
        f, origen = a_fecha(circular["fecha_faenado"]), "CIR-EGR-01"
    elif fecha_faenado is not None:
        f, origen = a_fecha(fecha_faenado), "MANUAL"
    else:
        raise DatosInvalidosError(f"Indique la fecha estimada de faenado del lote {lote['codigo']} (no hay "
                                  "circular CIR-EGR-01).")
    cantidad = aves_disponibles_lote(con, lote_id) if aves is None else _entero(aves, "aves", 1)
    if cantidad <= 0:
        raise ReglaNegocioError("El lote no tiene aves disponibles.", codigo="LOTE_SIN_AVES")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO despachos (lote_id, ciclo_id, fecha_faenado, origen_fecha, aves, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?)""",
            (lote_id, int(lote["ciclo_id"]), f.isoformat(), origen, cantidad, sesion.nombre_usuario, _ahora_iso()))
        did = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "despachos", "INSERT", did,
                            despues={"lote": lote["codigo"], "fecha_faenado": f.isoformat(), "origen": origen,
                                     "aves": cantidad})
    return {"id": did, "fecha_faenado": f, "aves": cantidad, "origen_fecha": origen}


def registrar_pedido(con: sqlite3.Connection, sesion: Sesion, despacho_id: int, cliente_id: int, aves: int,
                     fecha_pedido: Any, *, producto: str = "CARNE_ENTERO",
                     observacion: str | None = None) -> dict[str, Any]:
    """Registro de Pedido (FIN-ING-01). El pedido HORECA debe llegar con
    ``preventa_dias_min`` días de antelación al faenado; el de friales y
    mercado popular es indicativo (CI-FIN-13 Arts. 4 y 6)."""
    _exigir_finanzas(con, sesion)
    d = _despacho(con, despacho_id)
    if d["estado"] != "ABIERTO":
        raise ReglaNegocioError("El despacho del lote ya está cerrado.", codigo="DESPACHO_CERRADO")
    c = _cliente(con, cliente_id)
    if c["tipo"] in ("AGRICULTOR",):
        raise ReglaNegocioError("Los agricultores compran gallinaza, no carne del lote.", codigo="CLIENTE_NO_VALIDO")
    if producto not in CARNE:
        raise DatosInvalidosError("El pedido del lote es de carne (entero o cortes).")
    cantidad = _entero(aves, "aves", 1)
    fp = a_fecha(fecha_pedido)
    ff = a_fecha(d["fecha_faenado"])
    if fp > ff:
        raise ReglaNegocioError("El pedido es posterior a la fecha de faenado.", codigo="PEDIDO_TARDIO")
    p = _params(con, ff)
    anticipacion = (ff - fp).days
    tipo = "HORECA" if c["tipo"] == "HORECA" else "INDICATIVO"
    cumple = anticipacion >= int(_param(p, "preventa_dias_min")) if tipo == "HORECA" else True
    kg = Decimal(cantidad) * Decimal(str(_param(p, "rendimiento_kg_ave")))
    numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) FROM pedidos").fetchone()[0]) + 1
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO pedidos (numero, despacho_id, cliente_id, tipo, aves, kg_estimado_mil, producto, fecha_pedido,
                   fecha_faenado, anticipacion_dias, cumple_anticipacion, observacion, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (numero, despacho_id, cliente_id, tipo, cantidad, a_mil(kg), producto, fp.isoformat(), ff.isoformat(),
             anticipacion, int(cumple), _opcional(observacion, "observación", 300), sesion.nombre_usuario,
             _ahora_iso()))
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "pedidos", "INSERT", pid,
                            despues={"numero": numero, "cliente": c["codigo"], "tipo": tipo, "aves": cantidad,
                                     "anticipacion": anticipacion, "cumple": cumple})
    return {"id": pid, "numero": numero, "tipo": tipo, "anticipacion": anticipacion, "cumple": cumple,
            "activa_respaldo": tipo == "HORECA" and not cumple}


def anular_pedido(con: sqlite3.Connection, sesion: Sesion, pedido_id: int, motivo: str) -> None:
    _exigir_finanzas(con, sesion, "EDITAR")
    p = con.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if p is None:
        raise DatosInvalidosError(f"No existe el pedido {pedido_id}.")
    if p["estado"] != "VIGENTE":
        raise ReglaNegocioError("El pedido ya está anulado.", codigo="PEDIDO_ANULADO")
    texto = _texto(motivo, "motivo", 300)
    with transaccion(con):
        con.execute("UPDATE pedidos SET estado = 'ANULADO', motivo_anulacion = ? WHERE id = ?", (texto, pedido_id))
        auditoria.registrar(con, sesion, "pedidos", "UPDATE", pedido_id, antes={"estado": "VIGENTE"},
                            despues={"estado": "ANULADO"}, detalle=texto)


def cobertura(con: sqlite3.Connection, despacho_id: int) -> dict[str, Any]:
    """Suma de volúmenes comprometidos (CI-FIN-13 Art. 8): HORECA confirmado
    a tiempo + pedidos indicativos (+ HORECA fuera de plazo). Cubre si la suma
    alcanza ``cobertura_lote_min`` del lote y, cuando HORECA no basta, si hay
    al menos ``preventa_friales_min`` compradores indicativos distintos."""
    d = _despacho(con, despacho_id)
    p = _params(con, d["fecha_faenado"])
    minimo = Decimal(str(_param(p, "cobertura_lote_min")))
    friales_min = int(_param(p, "preventa_friales_min"))
    filas = con.execute("SELECT * FROM pedidos WHERE despacho_id = ? AND estado = 'VIGENTE'", (despacho_id,)).fetchall()
    horeca = sum(int(f["aves"]) for f in filas if f["tipo"] == "HORECA" and f["cumple_anticipacion"])
    tardio = sum(int(f["aves"]) for f in filas if f["tipo"] == "HORECA" and not f["cumple_anticipacion"])
    indicativo = sum(int(f["aves"]) for f in filas if f["tipo"] == "INDICATIVO")
    compradores = len({int(f["cliente_id"]) for f in filas if f["tipo"] == "INDICATIVO"})
    aves = int(d["aves"])
    objetivo = Decimal(aves) * minimo
    total = horeca + tardio + indicativo
    respaldo_requerido = Decimal(horeca) < objetivo
    cubre = Decimal(total) >= objetivo and (not respaldo_requerido or compradores >= friales_min)
    return {"despacho_id": despacho_id, "aves": aves, "horeca": horeca, "horeca_fuera_plazo": tardio,
            "indicativo": indicativo, "compradores_indicativos": compradores, "total": total,
            "cobertura": (Decimal(total) / Decimal(aves)).quantize(TASA, rounding=ROUND_HALF_UP),
            "cubre": cubre, "respaldo_requerido": respaldo_requerido,
            "respaldo_activado": d["respaldo_activado_por"] is not None, "remanente": max(aves - total, 0),
            "friales_min": friales_min}


def verificar_cobertura(con: sqlite3.Connection, sesion: Sesion, despacho_id: int, fecha: Any,
                        observacion: str | None = None) -> dict[str, Any]:
    """Verificación del día anterior al faenado (CI-FIN-13 Art. 8). Con
    remanente sin destino, el resultado indica que se informe de inmediato a
    la Gerencia General."""
    _exigir_finanzas(con, sesion)
    d = _despacho(con, despacho_id)
    f = a_fecha(fecha)
    ff = a_fecha(d["fecha_faenado"])
    if f > ff:
        raise ReglaNegocioError("La cobertura se verifica antes del faenado.", codigo="VERIFICACION_TARDIA")
    dias = int(_param(_params(con, ff), "cobertura_verificacion_dias_antes"))
    en_plazo = f <= ff - timedelta(days=dias)
    c = cobertura(con, despacho_id)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO verificaciones_cobertura (despacho_id, fecha, aves_lote, horeca_aves, indicativo_aves,
                   otros_aves, compradores_indicativos, cobertura, cubre, respaldo_requerido, en_plazo, remanente_aves,
                   observacion, verificado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (despacho_id, f.isoformat(), c["aves"], c["horeca"], c["indicativo"], c["horeca_fuera_plazo"],
             c["compradores_indicativos"], str(c["cobertura"]), int(c["cubre"]), int(c["respaldo_requerido"]),
             int(en_plazo), c["remanente"], _opcional(observacion, "observación", 500), sesion.nombre_usuario,
             _ahora_iso()))
        vid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "verificaciones_cobertura", "INSERT", vid,
                            despues={"despacho_id": despacho_id, "cobertura": c["cobertura"], "cubre": c["cubre"],
                                     "en_plazo": en_plazo})
    mensaje = ("Cobertura completa del lote." if c["cubre"] else
               f"Cobertura incompleta: {c['remanente']} aves sin destino o menos de {c['friales_min']} compradores "
               "indicativos. Informe de inmediato a la Gerencia General (CI-FIN-13 Art. 8).")
    return {"id": vid, "en_plazo": en_plazo, "mensaje": mensaje, **c}


def activar_respaldo(con: sqlite3.Connection, sesion: Sesion, despacho_id: int, motivo: str) -> None:
    """La GG activa sin trámite adicional el canal de contado a mercado
    popular y friales cuando HORECA no cubre el lote (CI-FIN-13 Art. 5)."""
    _exigir_gerencia(con, sesion)
    d = _despacho(con, despacho_id)
    if d["estado"] != "ABIERTO":
        raise ReglaNegocioError("El despacho ya está cerrado.", codigo="DESPACHO_CERRADO")
    if d["respaldo_activado_por"]:
        raise ReglaNegocioError("El canal de respaldo ya está activado.", codigo="RESPALDO_ACTIVO")
    if not cobertura(con, despacho_id)["respaldo_requerido"]:
        raise ReglaNegocioError("Los pedidos HORECA confirmados cubren el lote: no se requiere respaldo.",
                                codigo="RESPALDO_INNECESARIO")
    texto = _texto(motivo, "motivo", 300)
    with transaccion(con):
        con.execute("UPDATE despachos SET respaldo_activado_por = ?, respaldo_activado_en = ?, respaldo_motivo = ? "
                    "WHERE id = ?", (sesion.nombre_usuario, _ahora_iso(), texto, despacho_id))
        auditoria.registrar(con, sesion, "despachos", "AUTORIZACION", despacho_id,
                            despues={"respaldo": True, "motivo": texto}, detalle="CI-FIN-13 Art. 5")


def cerrar_despacho(con: sqlite3.Connection, sesion: Sesion, despacho_id: int, fecha: Any) -> None:
    """Cierra la distribución del lote. Exige una verificación con cobertura
    completa, que la cobertura actual siga completa y, si HORECA no bastó, el
    canal de respaldo activado por la GG.

    Raises:
        ReglaNegocioError: ``SIN_VERIFICACION``, ``COBERTURA_INCOMPLETA`` o
            ``RESPALDO_NO_ACTIVADO``.
    """
    _exigir_finanzas(con, sesion, "EDITAR")
    d = _despacho(con, despacho_id)
    if d["estado"] != "ABIERTO":
        raise ReglaNegocioError("El despacho ya está cerrado.", codigo="DESPACHO_CERRADO")
    ultima = con.execute("SELECT * FROM verificaciones_cobertura WHERE despacho_id = ? ORDER BY id DESC LIMIT 1",
                         (despacho_id,)).fetchone()
    if ultima is None:
        raise ReglaNegocioError("Falta la verificación de cobertura del día anterior al faenado (CI-FIN-13 Art. 8).",
                                codigo="SIN_VERIFICACION")
    c = cobertura(con, despacho_id)
    if not ultima["cubre"] or not c["cubre"]:
        raise ReglaNegocioError(
            f"El lote no tiene cobertura completa ({pct_texto(c['cobertura'])}, {c['remanente']} aves sin destino): "
            "no se puede cerrar el despacho (CI-FIN-13 Art. 8).", codigo="COBERTURA_INCOMPLETA")
    if c["respaldo_requerido"] and not c["respaldo_activado"]:
        raise ReglaNegocioError("HORECA no cubrió el lote: la GG debe activar el canal de respaldo (CI-FIN-13 Art. 5).",
                                codigo="RESPALDO_NO_ACTIVADO")
    f = a_fecha(fecha)
    with transaccion(con):
        con.execute("UPDATE despachos SET estado = 'CERRADO', cerrado_por = ?, cerrado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, f.isoformat(), despacho_id))
        auditoria.registrar(con, sesion, "despachos", "UPDATE", despacho_id, antes={"estado": "ABIERTO"},
                            despues={"estado": "CERRADO", "cobertura": c["cobertura"]})


def listar_despachos(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    salida = []
    for d in con.execute("SELECT d.*, l.codigo AS lote FROM despachos d JOIN lotes l ON l.id = d.lote_id "
                         "ORDER BY d.fecha_faenado DESC, d.id DESC"):
        salida.append({**dict(d), **{k: v for k, v in cobertura(con, int(d["id"])).items() if k != "despacho_id"}})
    return salida


def listar_pedidos(con: sqlite3.Connection, sesion: Sesion, despacho_id: int | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    sql = ("SELECT p.*, c.nombre AS cliente, c.tipo AS tipo_cliente FROM pedidos p JOIN clientes c ON c.id = p.cliente_id")
    args: tuple[Any, ...] = ()
    if despacho_id is not None:
        sql += " WHERE p.despacho_id = ?"
        args = (despacho_id,)
    return [{**dict(p), "kg_estimado": de_mil(int(p["kg_estimado_mil"]))} for p in con.execute(sql + " ORDER BY p.numero",
                                                                                                 args)]


def listar_verificaciones(con: sqlite3.Connection, sesion: Sesion, despacho_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [dict(v) for v in con.execute("SELECT * FROM verificaciones_cobertura WHERE despacho_id = ? ORDER BY id",
                                         (despacho_id,))]


# ==========================================================================
# 4. RESOLUCIÓN DE CRÉDITO (FIN-ING-02; POL-05 Art. 2)
# ==========================================================================
def autorizar_credito(con: sqlite3.Connection, sesion: Sesion, cliente_id: int, fecha: Any, plazo_dias: int,
                      monto_maximo: Any, evaluacion: str) -> dict[str, Any]:
    """Resolución escrita de la GG para crédito > 1 día, solo en el régimen
    que lo admite (``horeca_credito_dias_max`` > 1, desde 2028). Verifica las
    tres condiciones acumulativas: historial ≥ ``horeca_historial_meses_min``,
    plazo ≤ máximo y monto ≤ ``horeca_monto_max_entregas`` entregas semanales."""
    _exigir_gerencia(con, sesion)
    c = _cliente(con, cliente_id)
    f = a_fecha(fecha)
    p = _params(con, f)
    maximo = int(_param(p, "horeca_credito_dias_max"))
    if maximo <= 1:
        raise ReglaNegocioError(f"En el ejercicio {ejercicio_de_fecha_bd(con, f)} el crédito HORECA es de {maximo} día "
                                "como máximo (POL-05 Art. 1): no corresponde resolución de crédito.",
                                codigo="REGIMEN_SIN_CREDITO_EXTENDIDO")
    if c["tipo"] != "HORECA":
        raise ReglaNegocioError("El crédito se otorga solo a clientes HORECA (POL-05).", codigo="CLIENTE_NO_HORECA")
    if c["estado_credito"] == "SUSPENDIDO":
        raise ReglaNegocioError("La línea de crédito del cliente está suspendida (CIR-ING-01).",
                                codigo="CREDITO_SUSPENDIDO")
    plazo = _entero(plazo_dias, "plazo", 2, maximo)
    monto = _monto(monto_maximo, "monto máximo")
    historial = meses_historial(con, cliente_id, f)
    minimo = int(_param(p, "horeca_historial_meses_min"))
    if historial < minimo:
        raise ReglaNegocioError(f"El cliente tiene {historial} meses de historial; POL-05 Art. 2 exige {minimo}.",
                                codigo="HISTORIAL_INSUFICIENTE")
    semanal = entrega_semanal(con, cliente_id, f)
    tope = semanal * int(_param(p, "horeca_monto_max_entregas"))
    if monto > tope:
        raise ReglaNegocioError(f"El monto ({formato_bs(monto)}) supera la entrega semanal del cliente "
                                f"({formato_bs(tope)}) (POL-05 Art. 2).", codigo="MONTO_EXCEDE_ENTREGA")
    texto = _texto(evaluacion, "evaluación de capacidad de pago", 1000)
    with transaccion(con):
        con.execute("UPDATE resoluciones_credito SET estado = 'REVOCADA' WHERE cliente_id = ? AND estado = 'VIGENTE'",
                    (cliente_id,))
        cur = con.execute(
            """INSERT INTO resoluciones_credito (cliente_id, fecha, plazo_dias, monto_maximo_cent, historial_meses,
                   entrega_semanal_cent, evaluacion, autorizada_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (cliente_id, f.isoformat(), plazo, a_centavos(monto), historial, a_centavos(semanal), texto,
             sesion.nombre_usuario, _ahora_iso()))
        rid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "resoluciones_credito", "AUTORIZACION", rid,
                            despues={"cliente": c["codigo"], "plazo": plazo, "monto_maximo": monto,
                                     "historial_meses": historial, "entrega_semanal": semanal})
    return {"id": rid, "historial_meses": historial, "entrega_semanal": semanal}


def listar_resoluciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [{**dict(r), "monto_maximo": de_centavos(int(r["monto_maximo_cent"])),
             "entrega_semanal": de_centavos(int(r["entrega_semanal_cent"]))}
            for r in con.execute("SELECT r.*, c.nombre AS cliente, c.codigo AS codigo_cliente FROM resoluciones_credito r "
                                 "JOIN clientes c ON c.id = r.cliente_id ORDER BY r.id DESC")]


def resolucion_vigente(con: sqlite3.Connection, cliente_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM resoluciones_credito WHERE cliente_id = ? AND estado = 'VIGENTE' "
                       "ORDER BY id DESC LIMIT 1", (cliente_id,)).fetchone()


# ==========================================================================
# 5. NOTAS DE ENTREGA, ASIENTOS DE VENTA Y COSTO (FIN-ING-03/04/06; PC-08)
# ==========================================================================
def _cuentas_cliente(con: sqlite3.Connection, cliente_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM cuentas_cobrar WHERE cliente_id = ? ORDER BY vencimiento, id",
                       (cliente_id,)).fetchall()


def saldo_cuenta(cuenta: Mapping[str, Any]) -> Decimal:
    """Saldo pendiente de una cuenta por cobrar."""
    return de_centavos(int(cuenta["monto_cent"]) - int(cuenta["cobrado_cent"]) - int(cuenta["devuelto_cent"]))


def cxc_total(con: sqlite3.Connection) -> Decimal:
    fila = con.execute("SELECT COALESCE(SUM(monto_cent - cobrado_cent - devuelto_cent), 0) FROM cuentas_cobrar "
                       "WHERE estado = 'PENDIENTE'").fetchone()
    return de_centavos(int(fila[0]))


def ingresos_ciclo(con: sqlite3.Connection, ciclo_id: int) -> Decimal:
    """Ingresos de carne de los despachos del ciclo (base del 5 % de POL-05)."""
    fila = con.execute("SELECT COALESCE(SUM(n.total_cent), 0) FROM notas_entrega n JOIN despachos d "
                       "ON d.id = n.despacho_id WHERE d.ciclo_id = ?", (ciclo_id,)).fetchone()
    return de_centavos(int(fila[0]))


def _validar_medio(con: sqlite3.Connection, medio: str, tipo: str, fecha: date) -> None:
    if medio == "CHEQUE_DIFERIDO" and not bool(_param(_params(con, fecha), "cheques_diferidos_permitidos")):
        raise ReglaNegocioError("No se aceptan cheques diferidos (POL-05 Art. 1).", codigo="CHEQUE_DIFERIDO")
    permitidos = MEDIOS_HORECA if tipo == "HORECA" else MEDIOS_CONTADO
    if medio not in permitidos:
        raise ReglaNegocioError(f"Medio de pago no admitido para la venta {tipo.lower()}: use "
                                f"{', '.join(NOMBRE_MEDIO[m] for m in permitidos)}.", codigo="MEDIO_NO_ADMITIDO")


def registrar_nota(con: sqlite3.Connection, sesion: Sesion, cliente_id: int, fecha: Any, producto: str, cantidad: Any,
                   *, hora_entrega: str, medio_pago: str, comprobante_ref: str | None,
                   certificado_rau_exhibido: bool, certificado_rau_ref: str | None, recibido_por: str,
                   entregado_por: str, despacho_id: int | None = None, pedido_id: int | None = None,
                   dias_credito: int = 0) -> dict[str, Any]:
    """Nota de entrega HORECA (FIN-ING-03) o al contado (FIN-ING-06).

    Controles: comprobante obligatorio (FIN-ING-04 en el crédito; de pago en
    el contado); exhibición del Certificado RAU registrada; precio del
    tarifario vigente; medios de pago de POL-05 sin cheques diferidos; crédito
    solo HORECA, con el máximo del ejercicio y, sobre 1 día, resolución
    FIN-ING-02 y los límites de POL-05 Art. 2 (Matriz CI-FIN-09); cliente
    suspendido solo al contado. Genera el asiento de venta y el costo de
    ventas (la carne entra al kárdex al cierre del ciclo: si aún no hay
    existencia, el costo queda pendiente).

    Raises:
        ReglaNegocioError: ``COMPROBANTE_REQUERIDO``, ``RAU_NO_EXHIBIDO``,
            ``CREDITO_EXCEDE_PLAZO`` y demás reglas.
    """
    _exigir_finanzas(con, sesion)
    c = _cliente(con, cliente_id)
    f = a_fecha(fecha)
    if producto not in PRODUCTOS:
        raise DatosInvalidosError(f"Producto no válido. Use: {', '.join(PRODUCTOS)}.")
    comprobante = _opcional(comprobante_ref, "comprobante", 60)
    tipo = "HORECA" if c["tipo"] == "HORECA" and producto in CARNE else "CONTADO"
    dias = _entero(dias_credito, "días de crédito", 0, 365)
    if comprobante is None:
        raise ReglaNegocioError(
            "No se entrega producto sin comprobante: " + ("el Comprobante de Entrega y Compromiso de Pago (FIN-ING-04) "
                                                          "firmado por el cliente" if dias else "el comprobante de pago")
            + " (POL-05 Art. 1; PR-FIN-08 paso 4).", codigo="COMPROBANTE_REQUERIDO")
    if not certificado_rau_exhibido:
        raise ReglaNegocioError("Al no emitir factura (RAU), la venta exige exhibir el Certificado de Inscripción al RAU "
                                "y el comprobante del último pago (PR-FIN-08 §4; PR-FIN-09 paso 3).",
                                codigo="RAU_NO_EXHIBIDO")
    rau = _texto(certificado_rau_ref, "referencia del Certificado RAU exhibido", 120)
    hora = _texto(hora_entrega, "hora de entrega", 5)
    recibe = _texto(recibido_por, "recibido conforme por", 120)
    entrega = _texto(entregado_por, "entregado por", 120)
    _validar_medio(con, medio_pago, tipo, f)
    canal = canal_de(c["tipo"])
    if producto == "EXCEDENTE" and not costos.alimento_propio_vigente(con, f):
        raise ReglaNegocioError("El alimento excedente se vende desde el ejercicio del alimento propio.",
                                codigo="EXCEDENTE_NO_VIGENTE")
    if producto == "EXCEDENTE":
        # Fase 11 (PET-RAU R3-R6): programa aprobado, comprador autorizado,
        # contado y dentro del excedente autorizado en el balance de masas.
        servicio_rau.verificar_venta_excedente(con, cliente_id, f, cantidad, dias_credito)
    despacho = None
    if producto in CARNE:
        if despacho_id is None:
            raise DatosInvalidosError("Indique el despacho (lote de faenado) de la carne entregada.")
        despacho = _despacho(con, despacho_id)
        if despacho["estado"] != "ABIERTO":
            raise ReglaNegocioError("El despacho del lote ya está cerrado.", codigo="DESPACHO_CERRADO")
    if pedido_id is not None:
        pedido = con.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
        if pedido is None or pedido["estado"] != "VIGENTE" or int(pedido["cliente_id"]) != cliente_id \
                or (despacho_id is not None and int(pedido["despacho_id"]) != despacho_id):
            raise ReglaNegocioError("El pedido no corresponde al cliente o al despacho.", codigo="PEDIDO_NO_VALIDO")
    cant_mil = a_mil(cantidad, "cantidad")
    precio, tarifario_id = precio_vigente(con, producto, canal, f)
    total = redondear_bs(de_mil(cant_mil) * precio)
    resolucion = None
    vencimiento = None
    if dias > 0:
        if tipo != "HORECA":
            raise ReglaNegocioError("Fuera del canal HORECA las ventas son exclusivamente al contado (POL-05 Art. 1).",
                                    codigo="SOLO_CONTADO")
        if c["estado_credito"] == "SUSPENDIDO":
            raise ReglaNegocioError("La línea de crédito del cliente está suspendida (CIR-ING-01): venta al contado.",
                                    codigo="CREDITO_SUSPENDIDO")
        proyectada = cxc_total(con) + total
        ingresos = ingresos_ciclo(con, int(despacho["ciclo_id"])) + total if despacho is not None else total
        semanal = entrega_semanal(con, cliente_id, f)
        contexto = seguridad.contexto_desde_bd(con, f, dias_credito=dias,
                                               monto_entrega_semanal=semanal if semanal > 0 else None,
                                               cxc_total_proyectada=proyectada, ingresos_ciclo=ingresos)
        requisito = seguridad.requiere_autorizacion(TipoDecision.CREDITO_HORECA, total, contexto)
        if requisito.autorizacion_escrita:
            resolucion = resolucion_vigente(con, cliente_id)
            if resolucion is None:
                raise AutorizacionError(f"Crédito de {dias} días: requiere la Resolución de Autorización de Crédito "
                                        "(FIN-ING-02) de la Gerencia General.", codigo="RESOLUCION_REQUERIDA")
            if dias > int(resolucion["plazo_dias"]) or total > de_centavos(int(resolucion["monto_maximo_cent"])):
                raise ReglaNegocioError("El plazo o el monto superan los de la resolución FIN-ING-02.",
                                        codigo="EXCEDE_RESOLUCION")
        vencimiento = f + timedelta(days=dias)
    cuenta_ingreso = CUENTA_INGRESO[producto]
    numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) FROM notas_entrega WHERE tipo = ?",
                             (tipo,)).fetchone()[0]) + 1
    codigo_doc = f"{'FIN-ING-03' if tipo == 'HORECA' else 'FIN-ING-06'} n.º {numero}"
    glosa = f"{PRODUCTOS[producto]}: {cantidad_texto(de_mil(cant_mil), UNIDAD[producto])} a {c['nombre']} ({codigo_doc})"
    with transaccion(con):
        if dias > 0:
            cuenta_cobro = CUENTA_CXC
            asiento_id = contable.crear_asiento(
                con, sesion, f, f"Venta a crédito de {dias} día(s) — {glosa}",
                [{"cuenta": CUENTA_CXC, "debe": total}, {"cuenta": cuenta_ingreso, "haber": total}],
                ORIGEN, comprobante, tipo="TRASPASO").id
        elif medio_pago == "EFECTIVO":
            cuenta_cobro = CAJA
            mov = tesoreria.registrar_ingreso_efectivo(con, sesion, f, total, cuenta_ingreso, comprobante, glosa)
            asiento_id = int(con.execute("SELECT asiento_id FROM caja_movimientos WHERE id = ?", (mov,)).fetchone()[0])
        else:
            cuenta_cobro = BANCO
            mov = tesoreria.registrar_deposito(con, sesion, f, total, BANCO, cuenta_ingreso, glosa, comprobante)
            asiento_id = int(con.execute("SELECT asiento_id FROM movimientos_bancarios WHERE id = ?",
                                         (mov,)).fetchone()[0])
        cur = con.execute(
            """INSERT INTO notas_entrega (tipo, numero, cliente_id, canal, despacho_id, pedido_id, fecha, hora_entrega,
                   producto, unidad, cantidad_mil, precio_cent, total_cent, tarifario_id, condicion, dias_credito,
                   vencimiento, medio_pago, comprobante_ref, certificado_rau_ref, resolucion_id, cuenta_ingreso,
                   cuenta_cobro, asiento_venta_id, recibido_por, entregado_por, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tipo, numero, cliente_id, canal, despacho_id, pedido_id, f.isoformat(), hora, producto, UNIDAD[producto],
             cant_mil, a_centavos(precio), a_centavos(total), tarifario_id, "CREDITO" if dias else "CONTADO", dias,
             vencimiento.isoformat() if vencimiento else None, medio_pago, comprobante, rau,
             int(resolucion["id"]) if resolucion is not None else None, cuenta_ingreso, cuenta_cobro, asiento_id,
             recibe, entrega, sesion.nombre_usuario, _ahora_iso()))
        nid = int(cur.lastrowid)
        cuenta_id = None
        if dias > 0:
            cuenta_id = int(con.execute(
                "INSERT INTO cuentas_cobrar (nota_id, cliente_id, monto_cent, fecha_entrega, vencimiento) "
                "VALUES (?,?,?,?,?)", (nid, cliente_id, a_centavos(total), f.isoformat(),
                                       vencimiento.isoformat())).lastrowid)
            auditoria.registrar(con, sesion, "cuentas_cobrar", "INSERT", cuenta_id,
                                despues={"nota": codigo_doc, "monto": total, "vencimiento": vencimiento.isoformat()})
        auditoria.registrar(con, sesion, "notas_entrega", "INSERT", nid,
                            despues={"documento": codigo_doc, "cliente": c["codigo"], "producto": producto,
                                     "cantidad": de_mil(cant_mil), "precio": precio, "total": total,
                                     "condicion": "CREDITO" if dias else "CONTADO", "medio": medio_pago,
                                     "certificado_rau": rau})
        costo = _registrar_costo(con, sesion, nid, obligatorio=producto == "EXCEDENTE")
    return {"id": nid, "tipo": tipo, "numero": numero, "documento": codigo_doc, "precio": precio, "total": total,
            "asiento_id": asiento_id, "cuenta_id": cuenta_id, "vencimiento": vencimiento, "costo": costo}


def _registrar_costo(con: sqlite3.Connection, sesion: Sesion, nota_id: int, obligatorio: bool = False,
                     fecha: Any = None) -> Decimal | None:
    """Costo de ventas de la nota al CPP (costos.registrar_costo_venta). La
    gallinaza no tiene costo (subproducto no valuado). Sin existencia de carne
    (antes del cierre del ciclo) el costo queda pendiente.

    # DISCREPANCIA: PC-08 reconoce el costo de ventas al vender, pero la carne
    # entra a 1.1.03.12 con el cierre de costos del día 73 (PC-03/PC-04),
    # después de las primeras entregas del lote. Esas notas quedan con costo
    # pendiente y se contabilizan con la fecha en que hay existencia
    # (``contabilizar_costos_pendientes``).
    # DISCREPANCIA: la gallinaza (1.1.03.14) no tiene costo asignado en el
    # Manual de Costos; su venta no genera costo de ventas.
    """
    n = con.execute("SELECT * FROM notas_entrega WHERE id = ?", (nota_id,)).fetchone()
    if n["producto"] not in PRODUCTO_COSTO or n["asiento_costo_id"] is not None:
        return None
    producto = PRODUCTO_COSTO[n["producto"]]
    f = a_fecha(fecha) if fecha is not None else a_fecha(n["fecha"])
    saldo = costos.saldo_terminado(con, producto)
    cantidad = int(n["cantidad_mil"])
    disponible = saldo["cantidad_mil"] >= cantidad and (saldo["fecha"] is None or saldo["fecha"] <= f.isoformat())
    if not disponible:
        if obligatorio:
            raise ReglaNegocioError("No hay existencia suficiente del producto a la fecha para la venta.",
                                    codigo="EXISTENCIA_INSUFICIENTE")
        return None
    r = costos.registrar_costo_venta(con, sesion, producto, f, de_mil(cantidad),
                                     f"{'FIN-ING-03' if n['tipo'] == 'HORECA' else 'FIN-ING-06'} n.º {n['numero']}")
    con.execute("UPDATE notas_entrega SET costo_cent = ?, asiento_costo_id = ? WHERE id = ?",
                (a_centavos(r["valor"]), r["asiento_id"], nota_id))
    auditoria.registrar(con, sesion, "notas_entrega", "UPDATE", nota_id, antes={"asiento_costo_id": None},
                        despues={"costo": r["valor"], "asiento_costo_id": r["asiento_id"]})
    return r["valor"]


def costos_pendientes(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [{**dict(n), "cantidad": de_mil(int(n["cantidad_mil"])), "total": de_centavos(int(n["total_cent"]))}
            for n in con.execute("SELECT n.*, c.nombre AS cliente FROM notas_entrega n JOIN clientes c "
                                 "ON c.id = n.cliente_id WHERE n.asiento_costo_id IS NULL AND n.producto IN "
                                 "('CARNE_ENTERO','CARNE_CORTES','EXCEDENTE') ORDER BY n.fecha, n.id")]


def contabilizar_costos_pendientes(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Registra, con la fecha indicada, el costo de ventas de las notas que
    lo tenían pendiente (cuando la carne ya entró al kárdex con el cierre)."""
    _exigir_finanzas(con, sesion)
    f = a_fecha(fecha)
    total = CERO
    notas = 0
    with transaccion(con):
        for n in costos_pendientes(con, sesion):
            if a_fecha(n["fecha"]) > f:
                continue
            valor = _registrar_costo(con, sesion, int(n["id"]), obligatorio=True, fecha=f)
            if valor is not None:
                total += valor
                notas += 1
    if notas == 0:
        raise ReglaNegocioError("No hay notas con costo pendiente hasta esa fecha.", codigo="SIN_PENDIENTES")
    return {"notas": notas, "costo": total}


def obtener_nota(con: sqlite3.Connection, sesion: Sesion, nota_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    n = con.execute("SELECT n.*, c.nombre AS cliente, c.codigo AS codigo_cliente, c.documento FROM notas_entrega n "
                    "JOIN clientes c ON c.id = n.cliente_id WHERE n.id = ?", (nota_id,)).fetchone()
    if n is None:
        raise DatosInvalidosError(f"No existe la nota {nota_id}.")
    cuenta = con.execute("SELECT * FROM cuentas_cobrar WHERE nota_id = ?", (nota_id,)).fetchone()
    return {**dict(n), "cantidad": de_mil(int(n["cantidad_mil"])), "precio": de_centavos(int(n["precio_cent"])),
            "total": de_centavos(int(n["total_cent"])),
            "costo": de_centavos(int(n["costo_cent"])) if n["costo_cent"] is not None else None,
            "cuenta": dict(cuenta) if cuenta else None}


def listar_notas(con: sqlite3.Connection, sesion: Sesion, desde: Any = None, hasta: Any = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    sql = "SELECT n.*, c.nombre AS cliente FROM notas_entrega n JOIN clientes c ON c.id = n.cliente_id WHERE 1 = 1"
    args: list[Any] = []
    if desde is not None:
        sql += " AND n.fecha >= ?"
        args.append(a_fecha(desde).isoformat())
    if hasta is not None:
        sql += " AND n.fecha <= ?"
        args.append(a_fecha(hasta).isoformat())
    return [{**dict(n), "cantidad": de_mil(int(n["cantidad_mil"])), "precio": de_centavos(int(n["precio_cent"])),
             "total": de_centavos(int(n["total_cent"])),
             "costo": de_centavos(int(n["costo_cent"])) if n["costo_cent"] is not None else None}
            for n in con.execute(sql + " ORDER BY n.fecha DESC, n.id DESC", args)]


# ==========================================================================
# 6. CUENTAS POR COBRAR, COBROS Y SUSPENSIÓN (FIN-ING-05; CI-FIN-15; CIR-ING-01)
# ==========================================================================
def _cuenta(con: sqlite3.Connection, cuenta_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM cuentas_cobrar WHERE id = ?", (cuenta_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la cuenta por cobrar {cuenta_id}.")
    return fila


def registrar_cobro(con: sqlite3.Connection, sesion: Sesion, cuenta_id: int, fecha: Any, monto: Any, medio_pago: str,
                    comprobante_ref: str) -> dict[str, Any]:
    """Cobro de la cuenta con comprobante de ingreso a caja o banco (CI-FIN-15
    Paso 5). El pago posterior al vencimiento cuenta como incumplimiento."""
    _exigir_finanzas(con, sesion)
    cta = _cuenta(con, cuenta_id)
    if cta["estado"] != "PENDIENTE":
        raise ReglaNegocioError(f"La cuenta está {cta['estado'].lower()}.", codigo="CUENTA_NO_PENDIENTE")
    f = a_fecha(fecha)
    if f < a_fecha(cta["fecha_entrega"]):
        raise DatosInvalidosError("El cobro no puede ser anterior a la entrega.")
    _validar_medio(con, medio_pago, "HORECA", f)
    importe = _monto(monto, "monto cobrado")
    saldo = saldo_cuenta(cta)
    if importe > saldo:
        raise ReglaNegocioError(f"El cobro supera el saldo pendiente ({formato_bs(saldo)}).", codigo="COBRO_EXCEDE")
    doc = _texto(comprobante_ref, "comprobante de ingreso", 60)
    en_plazo = f <= a_fecha(cta["vencimiento"])
    n = con.execute("SELECT numero FROM notas_entrega WHERE id = ?", (cta["nota_id"],)).fetchone()
    glosa = f"Cobro de la nota FIN-ING-03 n.º {n['numero']}"
    with transaccion(con):
        if medio_pago == "EFECTIVO":
            mov = tesoreria.registrar_ingreso_efectivo(con, sesion, f, importe, CUENTA_CXC, doc, glosa)
            asiento_id = int(con.execute("SELECT asiento_id FROM caja_movimientos WHERE id = ?", (mov,)).fetchone()[0])
        else:
            mov = tesoreria.registrar_deposito(con, sesion, f, importe, BANCO, CUENTA_CXC, glosa, doc)
            asiento_id = int(con.execute("SELECT asiento_id FROM movimientos_bancarios WHERE id = ?",
                                         (mov,)).fetchone()[0])
        cur = con.execute(
            """INSERT INTO cobros (cuenta_id, fecha, monto_cent, medio_pago, comprobante_ref, en_plazo, asiento_id,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (cuenta_id, f.isoformat(), a_centavos(importe), medio_pago, doc, int(en_plazo), asiento_id,
             sesion.nombre_usuario, _ahora_iso()))
        cobrado = int(cta["cobrado_cent"]) + a_centavos(importe)
        completa = cobrado + int(cta["devuelto_cent"]) == int(cta["monto_cent"])
        incumplida = 1 if (cta["incumplida"] or not en_plazo) else 0
        con.execute("UPDATE cuentas_cobrar SET cobrado_cent = ?, estado = ?, fecha_cobro = ?, incumplida = ? WHERE id = ?",
                    (cobrado, "COBRADA" if completa else "PENDIENTE", f.isoformat() if completa else None, incumplida,
                     cuenta_id))
        auditoria.registrar(con, sesion, "cuentas_cobrar", "UPDATE", cuenta_id,
                            antes={"cobrado": de_centavos(int(cta["cobrado_cent"])), "estado": cta["estado"]},
                            despues={"cobrado": de_centavos(cobrado), "estado": "COBRADA" if completa else "PENDIENTE",
                                     "en_plazo": en_plazo, "cobro_id": int(cur.lastrowid)})
        suspension = _evaluar_suspension(con, sesion, int(cta["cliente_id"]), f)
    return {"id": int(cur.lastrowid), "en_plazo": en_plazo, "completa": completa, "suspension_id": suspension}


def incumplimientos_consecutivos(con: sqlite3.Connection, cliente_id: int) -> int:
    """Incumplimientos consecutivos más recientes del cliente desde su último
    restablecimiento: cuentas decididas (cobradas o vencidas) ordenadas por
    vencimiento; un pago a tiempo corta la serie."""
    c = _cliente(con, cliente_id)
    desde = c["restablecido_en"]
    decididas = [x for x in _cuentas_cliente(con, cliente_id)
                 if (desde is None or x["fecha_entrega"] > desde) and (x["estado"] == "COBRADA" or x["incumplida"])]
    serie = 0
    for x in reversed(decididas):
        if not x["incumplida"]:
            break
        serie += 1
    return serie


def _evaluar_suspension(con: sqlite3.Connection, sesion: Sesion, cliente_id: int, fecha: date) -> int | None:
    """Suspende automáticamente la línea al alcanzar
    ``credito_incumplimientos_suspension`` incumplimientos consecutivos y
    prepara la circular CIR-ING-01 para la firma de la GG."""
    c = _cliente(con, cliente_id)
    if c["estado_credito"] == "SUSPENDIDO":
        return None
    limite = int(_param(_params(con, fecha), "credito_incumplimientos_suspension"))
    serie = incumplimientos_consecutivos(con, cliente_id)
    if serie < limite:
        return None
    desde = c["restablecido_en"]
    ultimas = [x for x in _cuentas_cliente(con, cliente_id)
               if (desde is None or x["fecha_entrega"] > desde) and x["incumplida"]][-limite:]
    detalle = [{"vencimiento": x["vencimiento"], "monto": str(de_centavos(int(x["monto_cent"]))),
                "fecha_cobro": x["fecha_cobro"]} for x in ultimas]
    numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) FROM suspensiones_credito WHERE anio = ?",
                             (fecha.year,)).fetchone()[0]) + 1
    cur = con.execute(
        "INSERT INTO suspensiones_credito (numero, anio, cliente_id, fecha, detalle_json, registrado_en) "
        "VALUES (?,?,?,?,?,?)", (numero, fecha.year, cliente_id, fecha.isoformat(), json.dumps(detalle), _ahora_iso()))
    con.execute("UPDATE clientes SET estado_credito = 'SUSPENDIDO' WHERE id = ?", (cliente_id,))
    sid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "clientes", "UPDATE", cliente_id, antes={"estado_credito": "HABILITADO"},
                        despues={"estado_credito": "SUSPENDIDO", "circular": f"CIR-ING-01/{numero}/{fecha.year}"},
                        detalle=f"Suspensión automática por {serie} incumplimientos consecutivos (POL-05 Art. 2)")
    return sid


def revisar_cartera(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Revisión diaria (CI-FIN-15 Pasos 2 y 4): marca como incumplidas las
    cuentas vencidas no cobradas, registra su reporte a la Gerencia General
    (en plazo si es dentro de ``cobranza_reporte_horas`` del vencimiento) y
    aplica la suspensión automática."""
    _exigir_finanzas(con, sesion, "EDITAR")
    f = a_fecha(fecha)
    horas = int(_param(_params(con, f), "cobranza_reporte_horas"))
    vencidas = []
    suspensiones = []
    with transaccion(con):
        for x in con.execute("SELECT * FROM cuentas_cobrar WHERE estado = 'PENDIENTE' AND vencimiento < ? "
                             "ORDER BY vencimiento, id", (f.isoformat(),)).fetchall():
            reporte = x["reportada_en"] or f.isoformat()
            en_plazo = x["reporte_en_plazo"] if x["reportada_en"] else int(
                (f - a_fecha(x["vencimiento"])).days <= max(horas // 24, 1))
            con.execute("UPDATE cuentas_cobrar SET incumplida = 1, reportada_en = ?, reporte_en_plazo = ? WHERE id = ?",
                        (reporte, en_plazo, x["id"]))
            if not x["reportada_en"]:
                auditoria.registrar(con, sesion, "cuentas_cobrar", "UPDATE", x["id"],
                                    antes={"incumplida": x["incumplida"]},
                                    despues={"incumplida": 1, "reportada_en": reporte, "en_plazo": bool(en_plazo)},
                                    detalle="Reporte de cuenta vencida a la Gerencia General (CI-FIN-15 Paso 4)")
            vencidas.append(int(x["id"]))
        for cliente_id in {int(r[0]) for r in con.execute(
                "SELECT DISTINCT cliente_id FROM cuentas_cobrar WHERE incumplida = 1")}:
            sid = _evaluar_suspension(con, sesion, cliente_id, f)
            if sid:
                suspensiones.append(sid)
    return {"vencidas": vencidas, "suspensiones": suspensiones}


def cartera(con: sqlite3.Connection, sesion: Sesion, fecha: Any, solo_pendientes: bool = False) -> list[dict[str, Any]]:
    """Registro de Cuentas por Cobrar por Cliente (FIN-ING-05)."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    f = a_fecha(fecha)
    filas = []
    for x in con.execute("SELECT cc.*, c.nombre AS cliente, c.codigo AS codigo_cliente, n.numero AS nota "
                         "FROM cuentas_cobrar cc JOIN clientes c ON c.id = cc.cliente_id JOIN notas_entrega n "
                         "ON n.id = cc.nota_id ORDER BY cc.vencimiento, cc.id"):
        saldo = saldo_cuenta(x)
        if solo_pendientes and x["estado"] != "PENDIENTE":
            continue
        vencido = x["estado"] == "PENDIENTE" and a_fecha(x["vencimiento"]) < f
        filas.append({**dict(x), "monto": de_centavos(int(x["monto_cent"])), "cobrado": de_centavos(int(x["cobrado_cent"])),
                      "saldo": saldo, "vencido": vencido,
                      "dias_mora": max((f - a_fecha(x["vencimiento"])).days, 0) if x["estado"] == "PENDIENTE" else 0,
                      "incumplimientos": incumplimientos_consecutivos(con, int(x["cliente_id"]))})
    return filas


def firmar_suspension(con: sqlite3.Connection, sesion: Sesion, suspension_id: int) -> None:
    """La GG firma la circular CIR-ING-01 (la suspensión ya rige)."""
    _exigir_gerencia(con, sesion)
    s = con.execute("SELECT * FROM suspensiones_credito WHERE id = ?", (suspension_id,)).fetchone()
    if s is None:
        raise DatosInvalidosError(f"No existe la circular {suspension_id}.")
    if s["firmada_por"]:
        raise ReglaNegocioError("La circular ya está firmada.", codigo="CIRCULAR_FIRMADA")
    with transaccion(con):
        con.execute("UPDATE suspensiones_credito SET firmada_por = ?, firmada_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), suspension_id))
        auditoria.registrar(con, sesion, "suspensiones_credito", "AUTORIZACION", suspension_id,
                            despues={"firmada": True})


def restablecer_credito(con: sqlite3.Connection, sesion: Sesion, cliente_id: int, fecha: Any, motivo: str) -> None:
    """La GG autoriza expresamente el restablecimiento de la línea (CIR-ING-01);
    la cuenta de incumplimientos vuelve a cero."""
    _exigir_gerencia(con, sesion)
    c = _cliente(con, cliente_id)
    if c["estado_credito"] != "SUSPENDIDO":
        raise ReglaNegocioError("La línea del cliente no está suspendida.", codigo="NO_SUSPENDIDO")
    f = a_fecha(fecha)
    texto = _texto(motivo, "motivo", 300)
    with transaccion(con):
        con.execute("UPDATE clientes SET estado_credito = 'HABILITADO', restablecido_en = ? WHERE id = ?",
                    (f.isoformat(), cliente_id))
        con.execute("UPDATE suspensiones_credito SET restablecida_por = ?, restablecida_en = ?, "
                    "motivo_restablecimiento = ? WHERE cliente_id = ? AND restablecida_por IS NULL",
                    (sesion.nombre_usuario, f.isoformat(), texto, cliente_id))
        auditoria.registrar(con, sesion, "clientes", "AUTORIZACION", cliente_id, antes={"estado_credito": "SUSPENDIDO"},
                            despues={"estado_credito": "HABILITADO"}, detalle=texto)


def listar_suspensiones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [{**dict(s), "detalle": json.loads(s["detalle_json"])} for s in con.execute(
        "SELECT s.*, c.nombre AS cliente, c.codigo AS codigo_cliente FROM suspensiones_credito s JOIN clientes c "
        "ON c.id = s.cliente_id ORDER BY s.id DESC")]


# ==========================================================================
# 7. DEVOLUCIONES POR RECHAZO DE INOCUIDAD (PC-08 §V.C)
# ==========================================================================
def registrar_devolucion(con: sqlite3.Connection, sesion: Sesion, nota_id: int, fecha: Any, cantidad: Any,
                         motivo: str, informe_ref: str) -> dict[str, Any]:
    """Anula el ingreso de lo rechazado (5.1.04.01): reduce la cuenta por
    cobrar pendiente y deja el resto como saldo a favor del cliente. El
    producto rechazado se separa y no se comercializa (CI-FIN-12), por lo que
    su costo permanece en costo de ventas."""
    _exigir_finanzas(con, sesion)
    n = con.execute("SELECT * FROM notas_entrega WHERE id = ?", (nota_id,)).fetchone()
    if n is None:
        raise DatosInvalidosError(f"No existe la nota {nota_id}.")
    if n["producto"] == "GALLINAZA":
        raise ReglaNegocioError("La devolución por inocuidad aplica a la carne y al alimento.",
                                codigo="DEVOLUCION_NO_APLICA")
    f = a_fecha(fecha)
    if f < a_fecha(n["fecha"]):
        raise DatosInvalidosError("La devolución no puede ser anterior a la entrega.")
    cant = a_mil(cantidad, "cantidad devuelta")
    devuelto = int(con.execute("SELECT COALESCE(SUM(cantidad_mil), 0) FROM devoluciones WHERE nota_id = ?",
                               (nota_id,)).fetchone()[0])
    if devuelto + cant > int(n["cantidad_mil"]):
        raise ReglaNegocioError("La devolución supera lo entregado.", codigo="DEVOLUCION_EXCEDE")
    total_cent = int(n["total_cent"])
    if devuelto + cant == int(n["cantidad_mil"]):
        ya = int(con.execute("SELECT COALESCE(SUM(monto_cent), 0) FROM devoluciones WHERE nota_id = ?",
                             (nota_id,)).fetchone()[0])
        monto = de_centavos(total_cent - ya)
    else:
        monto = redondear_bs(de_centavos(total_cent) * Decimal(cant) / Decimal(int(n["cantidad_mil"])))
    cta = con.execute("SELECT * FROM cuentas_cobrar WHERE nota_id = ?", (nota_id,)).fetchone()
    a_cxc = min(monto, saldo_cuenta(cta)) if cta is not None and cta["estado"] == "PENDIENTE" else CERO
    favor = monto - a_cxc
    texto = _texto(motivo, "motivo del rechazo", 500)
    informe = _texto(informe_ref, "informe de rechazo (CI-FIN-12)", 120)
    lineas: list[dict[str, Any]] = [{"cuenta": CUENTA_DEVOLUCIONES, "debe": monto}]
    if a_cxc > 0:
        lineas.append({"cuenta": CUENTA_CXC, "haber": a_cxc})
    if favor > 0:
        lineas.append({"cuenta": CUENTA_SALDO_FAVOR, "haber": favor})
    with transaccion(con):
        asiento = contable.crear_asiento(
            con, sesion, f, f"Devolución por rechazo de inocuidad: nota "
                            f"{'FIN-ING-03' if n['tipo'] == 'HORECA' else 'FIN-ING-06'} n.º {n['numero']}", lineas,
            ORIGEN, informe, tipo="TRASPASO")
        cur = con.execute(
            """INSERT INTO devoluciones (nota_id, fecha, cantidad_mil, monto_cent, a_cuenta_cobrar_cent, saldo_favor_cent,
                   motivo, informe_ref, asiento_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (nota_id, f.isoformat(), cant, a_centavos(monto), a_centavos(a_cxc), a_centavos(favor), texto, informe,
             asiento.id, sesion.nombre_usuario, _ahora_iso()))
        if a_cxc > 0:
            nuevo = int(cta["devuelto_cent"]) + a_centavos(a_cxc)
            completa = int(cta["cobrado_cent"]) + nuevo == int(cta["monto_cent"])
            con.execute("UPDATE cuentas_cobrar SET devuelto_cent = ?, estado = ?, fecha_cobro = ? WHERE id = ?",
                        (nuevo, ("COBRADA" if int(cta["cobrado_cent"]) else "ANULADA") if completa else "PENDIENTE",
                         f.isoformat() if completa and int(cta["cobrado_cent"]) else cta["fecha_cobro"], cta["id"]))
        did = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "devoluciones", "INSERT", did,
                            despues={"nota_id": nota_id, "cantidad": de_mil(cant), "monto": monto, "a_cxc": a_cxc,
                                     "saldo_favor": favor, "informe": informe})
    return {"id": did, "monto": monto, "a_cuenta_cobrar": a_cxc, "saldo_favor": favor, "asiento_id": asiento.id}


def listar_devoluciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [{**dict(d), "cantidad": de_mil(int(d["cantidad_mil"])), "monto": de_centavos(int(d["monto_cent"]))}
            for d in con.execute("SELECT d.*, n.tipo, n.numero, c.nombre AS cliente FROM devoluciones d JOIN notas_entrega "
                                 "n ON n.id = d.nota_id JOIN clientes c ON c.id = n.cliente_id ORDER BY d.id DESC")]


# ==========================================================================
# 8. PARTE DIARIO DE RECAUDACIÓN (FIN-ING-07)
# ==========================================================================
def preparar_parte(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Consolidación de lo recaudado en el día: ventas al contado por
    producto y cobros de cuentas por cobrar; efectivo a depositar."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    f = a_fecha(fecha).isoformat()
    montos = {"carne": 0, "alimento": 0, "gallinaza": 0, "cobros": 0}
    efectivo = 0
    for n in con.execute("SELECT producto, medio_pago, total_cent FROM notas_entrega WHERE fecha = ? AND "
                         "condicion = 'CONTADO'", (f,)):
        clave = "carne" if n["producto"] in CARNE else ("alimento" if n["producto"] == "EXCEDENTE" else "gallinaza")
        montos[clave] += int(n["total_cent"])
        if n["medio_pago"] == "EFECTIVO":
            efectivo += int(n["total_cent"])
    for c in con.execute("SELECT medio_pago, monto_cent FROM cobros WHERE fecha = ?", (f,)):
        montos["cobros"] += int(c["monto_cent"])
        if c["medio_pago"] == "EFECTIVO":
            efectivo += int(c["monto_cent"])
    total = sum(montos.values())
    return {"fecha": f, **{k: de_centavos(v) for k, v in montos.items()}, "total": de_centavos(total),
            "efectivo": de_centavos(efectivo), "bancarizado": de_centavos(total - efectivo),
            "registrado": con.execute("SELECT * FROM partes_recaudacion WHERE fecha = ?", (f,)).fetchone() is not None}


def registrar_parte(con: sqlite3.Connection, sesion: Sesion, fecha: Any, boleta_ref: str | None) -> dict[str, Any]:
    """Registra el parte diario y deposita el efectivo del día en la cuenta
    corriente (política de no mantener fondos en cuentas personales)."""
    _exigir_finanzas(con, sesion)
    p = preparar_parte(con, sesion, fecha)
    if p["registrado"]:
        raise ReglaNegocioError("El parte de ese día ya fue registrado.", codigo="PARTE_EXISTENTE")
    if p["total"] <= 0:
        raise ReglaNegocioError("No hay recaudación en esa fecha.", codigo="SIN_RECAUDACION")
    boleta = _opcional(boleta_ref, "boleta de depósito", 60)
    if p["efectivo"] > 0 and boleta is None:
        raise DatosInvalidosError("Indique la boleta del depósito del efectivo del día.")
    f = a_fecha(fecha)
    with transaccion(con):
        mov = tesoreria.depositar_excedente(con, sesion, f, p["efectivo"], boleta) if p["efectivo"] > 0 else None
        cur = con.execute(
            """INSERT INTO partes_recaudacion (fecha, carne_cent, alimento_cent, gallinaza_cent, cobros_cent, total_cent,
                   efectivo_cent, bancarizado_cent, depositado_cent, boleta_ref, caja_movimiento_id, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), a_centavos(p["carne"]), a_centavos(p["alimento"]), a_centavos(p["gallinaza"]),
             a_centavos(p["cobros"]), a_centavos(p["total"]), a_centavos(p["efectivo"]), a_centavos(p["bancarizado"]),
             a_centavos(p["efectivo"]), boleta, mov, sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "partes_recaudacion", "INSERT", pid,
                            despues={"fecha": f.isoformat(), "total": p["total"], "depositado": p["efectivo"],
                                     "boleta": boleta})
    return {"id": pid, **p}


def verificar_parte(con: sqlite3.Connection, sesion: Sesion, parte_id: int) -> None:
    """La Gerencia General verifica el depósito del parte (FIN-ING-07)."""
    _exigir_gerencia(con, sesion)
    p = con.execute("SELECT * FROM partes_recaudacion WHERE id = ?", (parte_id,)).fetchone()
    if p is None:
        raise DatosInvalidosError(f"No existe el parte {parte_id}.")
    if p["verificado_por"]:
        raise ReglaNegocioError("El parte ya fue verificado.", codigo="PARTE_VERIFICADO")
    with transaccion(con):
        con.execute("UPDATE partes_recaudacion SET verificado_por = ?, verificado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), parte_id))
        auditoria.registrar(con, sesion, "partes_recaudacion", "AUTORIZACION", parte_id, despues={"verificado": True})


def listar_partes(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    return [{**dict(p), **{k: de_centavos(int(p[f"{k}_cent"])) for k in
                          ("carne", "alimento", "gallinaza", "cobros", "total", "efectivo", "bancarizado", "depositado")}}
            for p in con.execute("SELECT * FROM partes_recaudacion ORDER BY fecha DESC")]


# ==========================================================================
# 9. ANÁLISIS DE MARGEN POR CANAL (FIN-ING-08; CI-FIN-06 Arts. 5-7)
# ==========================================================================
def _fin_mes(anio: int, mes: int) -> date:
    siguiente = date(anio + (mes == 12), 1 if mes == 12 else mes + 1, 1)
    return siguiente - timedelta(days=1)


def calcular_margen(con: sqlite3.Connection, sesion: Sesion, anio: int, mes: int) -> dict[str, Any]:
    """Margen bruto mensual por canal: (precio promedio − costo unitario del
    último ciclo cerrado) ÷ precio, con los ingresos netos de devoluciones."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    _entero(mes, "mes", 1, 12)
    _entero(anio, "año", 2000, 9998)
    desde, hasta = date(anio, mes, 1), _fin_mes(anio, mes)
    costo, origen, etiqueta = costo_referencia(con, hasta)
    if origen != "CICLO_CERRADO":
        raise ReglaNegocioError("El margen se calcula con el costo unitario del último ciclo cerrado (CI-FIN-06 Art. 6) "
                                "y aún no hay ciclo cerrado.", codigo="SIN_COSTO_CICLO")
    minimo = Decimal(str(_param(_params(con, hasta), "margen_bruto_canal_min")))
    lineas = []
    for canal in CANALES:
        ingresos = 0
        kg = 0
        for n in con.execute("SELECT id, total_cent, cantidad_mil FROM notas_entrega WHERE canal = ? AND fecha BETWEEN "
                             "? AND ? AND producto IN ('CARNE_ENTERO','CARNE_CORTES')",
                             (canal, desde.isoformat(), hasta.isoformat())):
            dev = con.execute("SELECT COALESCE(SUM(monto_cent), 0), COALESCE(SUM(cantidad_mil), 0) FROM devoluciones "
                              "WHERE nota_id = ?", (n["id"],)).fetchone()
            ingresos += int(n["total_cent"]) - int(dev[0])
            kg += int(n["cantidad_mil"]) - int(dev[1])
        precio = (de_centavos(ingresos) / de_mil(kg)).quantize(TASA, rounding=ROUND_HALF_UP) if kg > 0 else None
        margen = ((precio - costo) / precio).quantize(TASA, rounding=ROUND_HALF_UP) if precio else None
        lineas.append({"canal": canal, "ingresos": de_centavos(ingresos), "kg": de_mil(kg), "precio_promedio": precio,
                       "margen": margen, "bajo_umbral": margen is not None and margen < minimo})
    return {"anio": anio, "mes": mes, "costo_unitario": costo, "ciclo_referencia": etiqueta, "margen_minimo": minimo,
            "lineas": lineas}


def registrar_analisis_margen(con: sqlite3.Connection, sesion: Sesion, anio: int, mes: int) -> dict[str, Any]:
    """La GG registra el FIN-ING-08 del mes. Si un canal queda bajo el mínimo
    durante ``margen_meses_consecutivos`` meses seguidos, el análisis marca la
    obligación de someter un ajuste del tarifario a la GG y al CA (Art. 7)."""
    _exigir_gerencia(con, sesion)
    r = calcular_margen(con, sesion, anio, mes)
    if con.execute("SELECT 1 FROM analisis_margen WHERE anio = ? AND mes = ?", (anio, mes)).fetchone():
        raise ReglaNegocioError("El análisis de ese mes ya fue registrado.", codigo="ANALISIS_EXISTENTE")
    meses = int(_param(_params(con, _fin_mes(anio, mes)), "margen_meses_consecutivos"))
    canales_ajuste = []
    for l in r["lineas"]:
        if not l["bajo_umbral"]:
            continue
        y, m = anio, mes
        consecutivos = 1
        for _ in range(meses - 1):
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            previo = con.execute(
                "SELECT ml.bajo_umbral FROM analisis_margen a JOIN analisis_margen_lineas ml ON ml.analisis_id = a.id "
                "WHERE a.anio = ? AND a.mes = ? AND ml.canal = ?", (y, m, l["canal"])).fetchone()
            if previo is None or not previo[0]:
                break
            consecutivos += 1
        if consecutivos >= meses:
            canales_ajuste.append(l["canal"])
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO analisis_margen (anio, mes, costo_unitario, ciclo_referencia, ajuste_requerido, canales_ajuste,
                   elaborado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (anio, mes, str(r["costo_unitario"]), r["ciclo_referencia"], int(bool(canales_ajuste)),
             ",".join(canales_ajuste) or None, sesion.nombre_usuario, _ahora_iso()))
        aid = int(cur.lastrowid)
        for l in r["lineas"]:
            con.execute(
                "INSERT INTO analisis_margen_lineas (analisis_id, canal, ingresos_cent, kg_mil, precio_promedio, margen, "
                "bajo_umbral) VALUES (?,?,?,?,?,?,?)",
                (aid, l["canal"], a_centavos(l["ingresos"]), a_mil(l["kg"]) if l["kg"] > 0 else 0,
                 str(l["precio_promedio"]) if l["precio_promedio"] is not None else None,
                 str(l["margen"]) if l["margen"] is not None else None, int(l["bajo_umbral"])))
        auditoria.registrar(con, sesion, "analisis_margen", "INSERT", aid,
                            despues={"periodo": f"{anio}-{mes:02d}", "costo": r["costo_unitario"],
                                     "ajuste_requerido": bool(canales_ajuste), "canales": canales_ajuste})
    return {"id": aid, "ajuste_requerido": bool(canales_ajuste), "canales_ajuste": canales_ajuste, **r}


def ajuste_tarifario_pendiente(con: sqlite3.Connection) -> dict[str, Any] | None:
    """Último análisis que exigió ajuste sin un tarifario aprobado con
    vigencia posterior al mes analizado."""
    a = con.execute("SELECT * FROM analisis_margen WHERE ajuste_requerido = 1 ORDER BY anio DESC, mes DESC LIMIT 1"
                    ).fetchone()
    if a is None:
        return None
    aprobado = con.execute("SELECT 1 FROM tarifarios WHERE estado = 'APROBADO' AND vigente_desde > ?",
                           (_fin_mes(int(a["anio"]), int(a["mes"])).isoformat(),)).fetchone()
    return None if aprobado else dict(a)


def listar_analisis(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    salida = []
    for a in con.execute("SELECT * FROM analisis_margen ORDER BY anio DESC, mes DESC"):
        lineas = [{**dict(l), "ingresos": de_centavos(int(l["ingresos_cent"])), "kg": de_mil(int(l["kg_mil"])),
                   "margen": Decimal(l["margen"]) if l["margen"] is not None else None,
                   "precio_promedio": Decimal(l["precio_promedio"]) if l["precio_promedio"] is not None else None}
                  for l in con.execute("SELECT * FROM analisis_margen_lineas WHERE analisis_id = ? ORDER BY id", (a["id"],))]
        salida.append({**dict(a), "lineas": lineas})
    return salida


# ==========================================================================
# 10. PROVISIÓN DE INCOBRABLES (POL-05 Art. 3; PR-FIN-08; desde 2028)
# ==========================================================================
def calcular_provision(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Provisión requerida: ``provision_incobrables_pct`` del saldo de
    deudores y ``provision_incobrables_pct_mora`` de las cuentas con mora
    mayor a ``provision_dudoso_dias_mora`` días. Rige desde 2028."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    f = a_fecha(fecha)
    p = _params(con, f)
    if "provision_incobrables_pct" not in p:
        raise ReglaNegocioError(f"La provisión de incobrables rige desde 2028 (POL-05 Art. 3); el ejercicio "
                                f"{ejercicio_de_fecha_bd(con, f)} no la exige.", codigo="PROVISION_NO_VIGENTE")
    pct = Decimal(str(p["provision_incobrables_pct"]))
    pct_mora = Decimal(str(_param(p, "provision_incobrables_pct_mora")))
    dias = int(_param(p, "provision_dudoso_dias_mora"))
    detalle = []
    requerida = CERO
    saldo_total = CERO
    for x in con.execute("SELECT * FROM cuentas_cobrar WHERE estado = 'PENDIENTE' AND fecha_entrega <= ?",
                         (f.isoformat(),)):
        saldo = saldo_cuenta(x)
        if saldo <= 0:
            continue
        mora = max((f - a_fecha(x["vencimiento"])).days, 0)
        tasa = pct_mora if mora > dias else pct
        monto = redondear_bs(saldo * tasa)
        requerida += monto
        saldo_total += saldo
        detalle.append({"cuenta_id": int(x["id"]), "saldo": str(saldo), "mora": mora, "tasa": str(tasa),
                        "provision": str(monto)})
    anterior = -tesoreria.saldo_cuenta(con, CUENTA_PROVISION, f)
    return {"fecha": f, "saldo_deudores": saldo_total, "requerida": requerida, "anterior": anterior,
            "ajuste": requerida - anterior, "detalle": detalle}


def registrar_provision(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Ajusta la provisión: 7.1.02.08 contra 4.1.01.01 por la diferencia (o la
    reversa si la requerida disminuye)."""
    _exigir_finanzas(con, sesion)
    r = calcular_provision(con, sesion, fecha)
    ajuste = r["ajuste"]
    with transaccion(con):
        asiento_id = None
        if ajuste != 0:
            lineas = ([{"cuenta": CUENTA_GASTO_INCOBRABLES, "debe": ajuste}, {"cuenta": CUENTA_PROVISION, "haber": ajuste}]
                      if ajuste > 0 else
                      [{"cuenta": CUENTA_PROVISION, "debe": -ajuste}, {"cuenta": CUENTA_GASTO_INCOBRABLES, "haber": -ajuste}])
            asiento_id = contable.crear_asiento(con, sesion, r["fecha"], "Provisión para cuentas incobrables (POL-05 Art. 3)",
                                                lineas, ORIGEN, "FIN-ING-05", tipo="TRASPASO").id
        cur = con.execute(
            """INSERT INTO provisiones_incobrables (fecha, saldo_deudores_cent, requerida_cent, anterior_cent, ajuste_cent,
                   detalle_json, asiento_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (r["fecha"].isoformat(), a_centavos(r["saldo_deudores"]), a_centavos(r["requerida"]),
             a_centavos(r["anterior"]), a_centavos(ajuste), json.dumps(r["detalle"]), asiento_id, sesion.nombre_usuario,
             _ahora_iso()))
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "provisiones_incobrables", "INSERT", pid,
                            despues={"requerida": r["requerida"], "ajuste": ajuste, "asiento_id": asiento_id})
    return {"id": pid, "asiento_id": asiento_id, **r}


# ==========================================================================
# 11. RESUMEN DEL MÓDULO
# ==========================================================================
def resumen_ventas(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Indicadores y alertas del ciclo de ingresos."""
    seguridad.exigir_permiso(con, sesion, "ventas", "VER")
    f = a_fecha(fecha)
    desde = date(f.year, f.month, 1)
    ventas_mes = de_centavos(int(con.execute(
        "SELECT COALESCE(SUM(total_cent), 0) FROM notas_entrega WHERE fecha BETWEEN ? AND ?",
        (desde.isoformat(), f.isoformat())).fetchone()[0]))
    pendientes = cartera(con, sesion, f, solo_pendientes=True)
    vencidas = [x for x in pendientes if x["vencido"]]
    alertas = []
    sin_reportar = [x for x in vencidas if not x["reportada_en"]]
    if sin_reportar:
        alertas.append(f"{len(sin_reportar)} cuenta(s) vencida(s) sin reportar a la Gerencia General (CI-FIN-15).")
    for d in con.execute("SELECT d.id, d.fecha_faenado, l.codigo FROM despachos d JOIN lotes l ON l.id = d.lote_id "
                         "WHERE d.estado = 'ABIERTO' AND d.fecha_faenado >= ? AND d.fecha_faenado <= ?",
                         (f.isoformat(), (f + timedelta(days=3)).isoformat())):
        c = cobertura(con, int(d["id"]))
        if not c["cubre"]:
            alertas.append(f"Lote {d['codigo']} (faenado {fecha_pantalla(d['fecha_faenado'])}): cobertura "
                           f"{pct_texto(c['cobertura'])} (CI-FIN-13).")
    ajuste = ajuste_tarifario_pendiente(con)
    if ajuste:
        alertas.append(f"Margen bajo el mínimo dos meses seguidos ({ajuste['canales_ajuste']}): someter ajuste del "
                       "tarifario a la GG y al CA (CI-FIN-06 Art. 7).")
    pendientes_costo = int(con.execute("SELECT COUNT(*) FROM notas_entrega WHERE asiento_costo_id IS NULL AND producto "
                                       "IN ('CARNE_ENTERO','CARNE_CORTES','EXCEDENTE')").fetchone()[0])
    if pendientes_costo:
        alertas.append(f"{pendientes_costo} nota(s) con costo de ventas pendiente del cierre del ciclo.")
    sin_firma = int(con.execute("SELECT COUNT(*) FROM suspensiones_credito WHERE firmada_por IS NULL").fetchone()[0])
    if sin_firma:
        alertas.append(f"{sin_firma} circular(es) CIR-ING-01 esperan la firma de la Gerencia General.")
    if tarifario_vigente(con, f) is None:
        alertas.append("No hay tarifario aprobado vigente: no se puede vender (CI-FIN-06 Art. 9).")
    return {"ventas_mes": ventas_mes, "cxc": sum((x["saldo"] for x in pendientes), CERO),
            "vencido": sum((x["saldo"] for x in vencidas), CERO), "vencidas": len(vencidas),
            "despachos_abiertos": int(con.execute("SELECT COUNT(*) FROM despachos WHERE estado = 'ABIERTO'").fetchone()[0]),
            "alertas": alertas}
