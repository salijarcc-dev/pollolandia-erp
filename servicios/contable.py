"""Motor contable de partida doble (Fase 2, regla 3 del proyecto).

* :func:`crear_asiento`: valida cuadre exacto en ``Decimal``, cuentas de
  movimiento activas y habilitadas para el ejercicio, período abierto,
  naturaleza del saldo resultante y, en el Grupo 6, centro de costo y sufijo.
  Asigna el correlativo automático por tipo de comprobante.
* :func:`reversar_asiento`: los asientos nunca se borran; se anulan con un
  asiento espejo (tipo AJUSTE) autorizado por la Gerencia General.
* :func:`cerrar_periodo`: cierre mensual secuencial (autoriza la GG).
* Libros: :func:`libro_diario`, :func:`libro_mayor` y
  :func:`balance_comprobacion`.

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from db.conexion import a_centavos, buscar_periodo, de_centavos, obtener_parametro, transaccion
from servicios import auditoria
from servicios.excepciones import (
    AutorizacionError, DatosInvalidosError, PeriodoCerradoError, ReglaNegocioError,
)
from servicios.seguridad import Sesion, TipoDecision, consumir_autorizacion, exigir_permiso, verificar_autorizacion
from utils.formato import a_fecha, fecha_pantalla, formato_bs, redondear_bs

TIPOS_REGISTRABLES: tuple[str, ...] = ("INGRESO", "EGRESO", "TRASPASO", "AJUSTE")
TIPO_REVERSION = "AJUSTE"
TIPOS_NO_REVERSIBLES: tuple[str, ...] = ("APERTURA", "CIERRE")
CERO = Decimal("0.00")
LARGO_POR_NIVEL: dict[int, int] = {1: 1, 2: 3, 3: 6, 4: 9}
NOMBRE_MES: tuple[str, ...] = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                               "septiembre", "octubre", "noviembre", "diciembre")


# ==========================================================================
# ESTRUCTURAS
# ==========================================================================
@dataclass(frozen=True)
class LineaAsiento:
    """Una línea del asiento. ``centro_costo`` y ``sufijo`` solo en el Grupo 6."""

    cuenta: str
    debe: Decimal = CERO
    haber: Decimal = CERO
    centro_costo: str | None = None
    sufijo: str | None = None
    glosa: str | None = None


@dataclass(frozen=True)
class AsientoRegistrado:
    """Resultado de registrar un asiento."""

    id: int
    tipo: str
    numero: int
    fecha: str
    periodo_id: int
    ejercicio: int
    total: Decimal
    lineas: tuple[LineaAsiento, ...]

    @property
    def comprobante(self) -> str:
        return f"{self.tipo}-{self.numero:05d}"


# ==========================================================================
# NORMALIZACIÓN Y VALIDACIÓN
# ==========================================================================
def _monto(valor: Any, campo: str) -> Decimal:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return CERO
    try:
        importe = redondear_bs(valor)
    except DatosInvalidosError as exc:
        raise DatosInvalidosError(f"{campo}: {exc}") from exc
    if importe < 0:
        raise DatosInvalidosError(f"{campo}: el monto no puede ser negativo.")
    return importe


def _codigo_opcional(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise DatosInvalidosError("Código no válido.")
    texto = str(valor).strip()
    return texto or None


def normalizar_linea(dato: LineaAsiento | Mapping[str, Any], numero: int) -> LineaAsiento:
    """Convierte una línea (``LineaAsiento`` o diccionario) a ``LineaAsiento``
    con montos ``Decimal`` redondeados a 2 decimales (ROUND_HALF_UP).

    Raises:
        DatosInvalidosError: cuenta vacía, monto inválido o negativo, o línea
            con debe y haber a la vez (o ninguno).
    """
    if isinstance(dato, LineaAsiento):
        bruto: Mapping[str, Any] = dato.__dict__
    elif isinstance(dato, Mapping):
        bruto = dato
    else:
        raise DatosInvalidosError(f"Línea {numero}: formato no reconocido.")
    cuenta = _codigo_opcional(bruto.get("cuenta"))
    if not cuenta:
        raise DatosInvalidosError(f"Línea {numero}: indique la cuenta.")
    debe = _monto(bruto.get("debe"), f"Línea {numero} (debe)")
    haber = _monto(bruto.get("haber"), f"Línea {numero} (haber)")
    if (debe > 0) == (haber > 0):
        raise DatosInvalidosError(f"Línea {numero}: registre un importe en el Debe o en el Haber, no en ambos ni en ninguno.")
    sufijo = _codigo_opcional(bruto.get("sufijo"))
    if sufijo and sufijo.isdigit() and len(sufijo) == 1:
        sufijo = sufijo.zfill(2)
    glosa = bruto.get("glosa")
    glosa = glosa.strip() if isinstance(glosa, str) and glosa.strip() else None
    return LineaAsiento(cuenta=cuenta, debe=debe, haber=haber,
                        centro_costo=_codigo_opcional(bruto.get("centro_costo")), sufijo=sufijo, glosa=glosa)


def _texto(valor: Any, campo: str, maximo: int) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"El campo '{campo}' es obligatorio.")
    if len(valor.strip()) > maximo:
        raise DatosInvalidosError(f"El campo '{campo}' admite como máximo {maximo} caracteres.")
    return valor.strip()


def _nombre_periodo(periodo: sqlite3.Row) -> str:
    inicio = date.fromisoformat(periodo["fecha_inicio"])
    return f"{NOMBRE_MES[inicio.month - 1]} de {inicio.year}"


def _periodo_abierto(con: sqlite3.Connection, fecha: date) -> sqlite3.Row:
    """Período de la fecha, verificando que él y su ejercicio estén abiertos."""
    periodo = buscar_periodo(con, fecha)
    if periodo["estado"] == "CERRADO":
        raise PeriodoCerradoError(
            f"El período de {_nombre_periodo(periodo)} está cerrado: no admite registros. "
            "Registre la corrección en un período abierto.",
            detalle={"periodo_id": periodo["id"], "fecha": fecha.isoformat()})
    ejercicio = con.execute("SELECT estado FROM ejercicios WHERE ejercicio = ?", (periodo["ejercicio"],)).fetchone()
    if ejercicio is None or ejercicio["estado"] == "CERRADO":
        raise PeriodoCerradoError(f"El ejercicio {periodo['ejercicio']} está cerrado.",
                                  detalle={"ejercicio": periodo["ejercicio"]})
    return periodo


def _ejercicio_alimento(con: sqlite3.Connection, ejercicio: int) -> int:
    return int(obtener_parametro(con, "ejercicio_inicio_alimento_propio", ejercicio))


def _validar_lineas(con: sqlite3.Connection, lineas: list[LineaAsiento], ejercicio: int) -> list[LineaAsiento]:
    """Valida cuentas, habilitación por ejercicio y la matriz centro/sufijo."""
    inicio_alimento = _ejercicio_alimento(con, ejercicio)
    validadas: list[LineaAsiento] = []
    for n, linea in enumerate(lineas, start=1):
        cuenta = con.execute("SELECT * FROM cuentas WHERE codigo = ?", (linea.cuenta,)).fetchone()
        if cuenta is None:
            raise DatosInvalidosError(f"Línea {n}: la cuenta {linea.cuenta} no existe en el Plan de Cuentas.",
                                      codigo="CUENTA_INEXISTENTE")
        if not cuenta["es_movimiento"]:
            raise ReglaNegocioError(
                f"Línea {n}: {linea.cuenta} {cuenta['nombre']} es una cuenta de agrupación (nivel "
                f"{cuenta['nivel']}); solo las cuentas de nivel 4 admiten movimientos.",
                codigo="CUENTA_NO_MOVIMIENTO")
        if not cuenta["activa"]:
            raise ReglaNegocioError(f"Línea {n}: la cuenta {linea.cuenta} está inactiva.", codigo="CUENTA_INACTIVA")
        if cuenta["uso_desde_2027"] and ejercicio < inicio_alimento:
            raise ReglaNegocioError(
                f"Línea {n}: la cuenta {linea.cuenta} {cuenta['nombre']} se habilita desde el ejercicio "
                f"{inicio_alimento} (Plan de Cuentas v3, nota (*)).", codigo="CUENTA_NO_HABILITADA")
        centro = con.execute("SELECT * FROM centros_costo WHERE codigo = ?", (linea.cuenta,)).fetchone()
        if centro is None:
            if linea.centro_costo or linea.sufijo:
                raise ReglaNegocioError(
                    f"Línea {n}: centro de costo y sufijo solo se usan en las cuentas 6.1.01.xx del Grupo 6.",
                    codigo="SUFIJO_FUERA_DE_GRUPO6")
            validadas.append(linea)
            continue
        if linea.centro_costo and linea.centro_costo != linea.cuenta:
            raise DatosInvalidosError(
                f"Línea {n}: el centro de costo ({linea.centro_costo}) debe ser la misma cuenta {linea.cuenta}.")
        if centro["uso_desde_2027"] and ejercicio < inicio_alimento:
            raise ReglaNegocioError(f"Línea {n}: el centro {linea.cuenta} se habilita desde el ejercicio "
                                    f"{inicio_alimento}.", codigo="CUENTA_NO_HABILITADA")
        if not linea.sufijo:
            raise ReglaNegocioError(
                f"Línea {n}: la cuenta {linea.cuenta} es un centro de costo y exige sufijo (elemento del costo).",
                codigo="SUFIJO_OBLIGATORIO")
        sufijo = con.execute("SELECT * FROM sufijos_costo WHERE sufijo = ?", (linea.sufijo,)).fetchone()
        if sufijo is None:
            raise DatosInvalidosError(f"Línea {n}: el sufijo {linea.sufijo} no existe.", codigo="SUFIJO_INEXISTENTE")
        aplica = con.execute("SELECT 1 FROM sufijo_centro WHERE sufijo = ? AND centro_costo = ?",
                             (linea.sufijo, linea.cuenta)).fetchone()
        if aplica is None:
            raise ReglaNegocioError(
                f"Línea {n}: el sufijo {linea.sufijo} ({sufijo['nombre']}) no se aplica al centro {linea.cuenta}.",
                codigo="SUFIJO_NO_APLICABLE")
        if sufijo["uso_desde_2027"] and ejercicio < inicio_alimento:
            raise ReglaNegocioError(f"Línea {n}: el sufijo {linea.sufijo} se habilita desde el ejercicio "
                                    f"{inicio_alimento}.", codigo="CUENTA_NO_HABILITADA")
        validadas.append(LineaAsiento(cuenta=linea.cuenta, debe=linea.debe, haber=linea.haber,
                                      centro_costo=linea.cuenta, sufijo=linea.sufijo, glosa=linea.glosa))
    return validadas


def validar_cuadre(lineas: Iterable[LineaAsiento]) -> Decimal:
    """Exige igualdad exacta Debe = Haber (en ``Decimal``) y total mayor a cero.

    Returns:
        El total del asiento.

    Raises:
        ReglaNegocioError: (``ASIENTO_DESCUADRADO``) con la diferencia.
    """
    lista = list(lineas)
    if len(lista) < 2:
        raise DatosInvalidosError("Un asiento de partida doble necesita al menos dos líneas.")
    debe = sum((l.debe for l in lista), CERO)
    haber = sum((l.haber for l in lista), CERO)
    if debe != haber:
        raise ReglaNegocioError(
            f"El asiento no cuadra: Debe {formato_bs(debe)} y Haber {formato_bs(haber)} "
            f"(diferencia {formato_bs(abs(debe - haber))}).",
            codigo="ASIENTO_DESCUADRADO",
            detalle={"debe": str(debe), "haber": str(haber), "diferencia": str(debe - haber)})
    if debe <= 0:
        raise DatosInvalidosError("El asiento no puede tener importe total cero.")
    return debe


def _validar_naturaleza(con: sqlite3.Connection, lineas: list[LineaAsiento]) -> None:
    """Rechaza que una cuenta quede con saldo contrario a su naturaleza.

    Solo se evalúan las cuentas cuyo movimiento va contra su naturaleza (por
    ejemplo, un crédito a Bancos); las cuentas DUAL del Grupo 8 no se evalúan.
    """
    movimiento: dict[str, int] = {}
    for l in lineas:
        movimiento[l.cuenta] = movimiento.get(l.cuenta, 0) + a_centavos(l.debe) - a_centavos(l.haber)
    for cuenta, mov in movimiento.items():
        info = con.execute("SELECT nombre, naturaleza FROM cuentas WHERE codigo = ?", (cuenta,)).fetchone()
        naturaleza = info["naturaleza"]
        if naturaleza == "DUAL" or (naturaleza == "DEUDORA" and mov >= 0) or (naturaleza == "ACREEDORA" and mov <= 0):
            continue
        actual = int(con.execute(
            "SELECT COALESCE(SUM(debe_cent), 0) - COALESCE(SUM(haber_cent), 0) FROM asiento_detalle "
            "WHERE cuenta_codigo = ?", (cuenta,)).fetchone()[0])
        nuevo = actual + mov
        if (naturaleza == "DEUDORA" and nuevo < 0) or (naturaleza == "ACREEDORA" and nuevo > 0):
            contrario = "acreedor" if naturaleza == "DEUDORA" else "deudor"
            raise ReglaNegocioError(
                f"La cuenta {cuenta} {info['nombre']} es de naturaleza {naturaleza.lower()} y quedaría con saldo "
                f"{contrario} de {formato_bs(de_centavos(abs(nuevo)))} (saldo actual "
                f"{formato_bs(de_centavos(abs(actual)))}).",
                codigo="SALDO_CONTRARIO_NATURALEZA",
                detalle={"cuenta": cuenta, "saldo_actual_cent": actual, "saldo_nuevo_cent": nuevo})


def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _lineas_a_dict(lineas: Iterable[LineaAsiento]) -> list[dict[str, Any]]:
    return [{"cuenta": l.cuenta, "debe": str(l.debe), "haber": str(l.haber), "centro_costo": l.centro_costo,
             "sufijo": l.sufijo, "glosa": l.glosa} for l in lineas]


def _insertar(
    con: sqlite3.Connection, sesion: Sesion, tipo: str, fecha: date, periodo: sqlite3.Row, glosa: str,
    origen: str, documento_ref: str | None, lineas: list[LineaAsiento], total: Decimal,
    reversa_de: int | None = None, autorizacion_id: int | None = None,
) -> AsientoRegistrado:
    numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) + 1 FROM asientos WHERE tipo = ?",
                             (tipo,)).fetchone()[0])
    cur = con.execute(
        """INSERT INTO asientos (tipo, numero, fecha, periodo_id, glosa, origen, documento_ref,
                                 reversa_de, creado_por, creado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (tipo, numero, fecha.isoformat(), periodo["id"], glosa, origen, documento_ref, reversa_de,
         sesion.nombre_usuario, _ahora_iso()))
    asiento_id = int(cur.lastrowid)
    for n, l in enumerate(lineas, start=1):
        con.execute(
            """INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent, haber_cent,
                                            centro_costo, sufijo, glosa_linea)
               VALUES (?,?,?,?,?,?,?,?)""",
            (asiento_id, n, l.cuenta, a_centavos(l.debe), a_centavos(l.haber), l.centro_costo, l.sufijo, l.glosa))
    auditoria.registrar(
        con, sesion, "asientos", "INSERT", asiento_id,
        despues={"tipo": tipo, "numero": numero, "fecha": fecha.isoformat(), "periodo_id": periodo["id"],
                 "glosa": glosa, "origen": origen, "documento_ref": documento_ref, "reversa_de": reversa_de,
                 "autorizacion_id": autorizacion_id, "total": str(total), "lineas": _lineas_a_dict(lineas)})
    return AsientoRegistrado(id=asiento_id, tipo=tipo, numero=numero, fecha=fecha.isoformat(),
                             periodo_id=int(periodo["id"]), ejercicio=int(periodo["ejercicio"]), total=total,
                             lineas=tuple(lineas))


# ==========================================================================
# REGISTRO
# ==========================================================================
def crear_asiento(
    con: sqlite3.Connection,
    sesion: Sesion,
    fecha: Any,
    glosa: str,
    lineas: Iterable[LineaAsiento | Mapping[str, Any]],
    origen: str,
    documento_ref: str | None = None,
    *,
    tipo: str,
    autorizacion_id: int | None = None,
) -> AsientoRegistrado:
    """Registra un asiento de partida doble.

    Args:
        con: conexión abierta.
        sesion: usuario autenticado (debe tener ``contabilidad/CREAR``: el JF).
        fecha: fecha ISO del hecho económico.
        glosa: descripción del asiento.
        lineas: ``LineaAsiento`` o diccionarios con cuenta, debe, haber,
            centro_costo, sufijo y glosa.
        origen: módulo o proceso que origina el asiento (p. ej. ``MANUAL``).
        documento_ref: documento de respaldo (formato FIN-*, factura, etc.).
        tipo: ``INGRESO``, ``EGRESO``, ``TRASPASO`` o ``AJUSTE``.
        autorizacion_id: obligatorio para AJUSTE (autorización AJUSTE_CONTABLE
            firmada por la GG, MOF §3.1); queda UTILIZADA.

    Raises:
        AutorizacionError: rol sin facultad (la GG no registra; el CV es solo
            lectura) o ajuste sin autorización completa.
        PeriodoCerradoError: período o ejercicio cerrado.
        ReglaNegocioError: asiento descuadrado, cuenta de agrupación, cuenta
            no habilitada, sufijo indebido o saldo contrario a la naturaleza.
        DatosInvalidosError: datos con tipo o formato inválido.
    """
    exigir_permiso(con, sesion, "contabilidad", "CREAR")
    if tipo not in TIPOS_REGISTRABLES:
        raise DatosInvalidosError(f"Tipo de asiento no válido: {tipo!r}. Use {', '.join(TIPOS_REGISTRABLES)}.")
    texto = _texto(glosa, "glosa", 500)
    origen_txt = _texto(origen, "origen", 60)
    ref = documento_ref.strip() if isinstance(documento_ref, str) and documento_ref.strip() else None
    f = a_fecha(fecha)
    normalizadas = [normalizar_linea(l, n) for n, l in enumerate(lineas, start=1)]
    if tipo == "AJUSTE" and autorizacion_id is None:
        raise AutorizacionError("Los asientos de ajuste requieren la autorización de la Gerencia General "
                                "(MOF §3.1): indique la autorización AJUSTE_CONTABLE.", codigo="AJUSTE_SIN_AUTORIZACION")
    if tipo != "AJUSTE" and autorizacion_id is not None:
        raise DatosInvalidosError("En esta fase solo los asientos de ajuste se vinculan a una autorización.")
    with transaccion(con):
        periodo = _periodo_abierto(con, f)
        validadas = _validar_lineas(con, normalizadas, int(periodo["ejercicio"]))
        total = validar_cuadre(validadas)
        if autorizacion_id is not None:
            verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.AJUSTE_CONTABLE, monto=total)
        _validar_naturaleza(con, validadas)
        asiento = _insertar(con, sesion, tipo, f, periodo, texto, origen_txt, ref, validadas, total,
                            autorizacion_id=autorizacion_id)
        if autorizacion_id is not None:
            consumir_autorizacion(con, sesion, autorizacion_id, "asientos", asiento.id,
                                  tipo_decision=TipoDecision.AJUSTE_CONTABLE, monto=total)
    return asiento


def reversar_asiento(
    con: sqlite3.Connection,
    sesion: Sesion,
    asiento_id: int,
    motivo: str,
    *,
    autorizacion_id: int,
    fecha: Any = None,
) -> AsientoRegistrado:
    """Anula un asiento con otro espejo (Debe ↔ Haber); el original queda
    REVERSADO y nunca se borra.

    Args:
        asiento_id: asiento a reversar.
        motivo: causa de la reversión (queda en la glosa y la bitácora).
        autorizacion_id: autorización AJUSTE_CONTABLE firmada por la GG.
        fecha: fecha de la reversión; por defecto, la del asiento original
            (si su período está cerrado, indique una fecha en período abierto).

    Raises:
        AutorizacionError: rol sin facultad o autorización incompleta.
        PeriodoCerradoError: la fecha de reversión cae en período cerrado.
        ReglaNegocioError: asiento ya reversado, de apertura/cierre, o que es
            a su vez una reversión.
    """
    exigir_permiso(con, sesion, "contabilidad", "REVERSAR")
    causa = _texto(motivo, "motivo", 300)
    if isinstance(asiento_id, bool) or not isinstance(asiento_id, int):
        raise DatosInvalidosError("Indique el número interno del asiento a reversar.")
    with transaccion(con):
        original = con.execute("SELECT * FROM asientos WHERE id = ?", (asiento_id,)).fetchone()
        if original is None:
            raise DatosInvalidosError(f"No existe el asiento {asiento_id}.")
        comprobante = f"{original['tipo']}-{int(original['numero']):05d}"
        if original["estado"] == "REVERSADO":
            raise ReglaNegocioError(f"El asiento {comprobante} ya fue reversado.", codigo="ASIENTO_YA_REVERSADO")
        if original["tipo"] in TIPOS_NO_REVERSIBLES:
            raise ReglaNegocioError(f"Un asiento de {original['tipo'].lower()} no se reversa; corrija con un "
                                    "asiento de ajuste.", codigo="ASIENTO_NO_REVERSIBLE")
        if original["reversa_de"] is not None:
            raise ReglaNegocioError("Un asiento de reversión no se reversa; registre un asiento nuevo.",
                                    codigo="ASIENTO_NO_REVERSIBLE")
        f = a_fecha(fecha) if fecha is not None else date.fromisoformat(original["fecha"])
        if f < date.fromisoformat(original["fecha"]):
            raise DatosInvalidosError("La reversión no puede tener fecha anterior al asiento original.")
        periodo = _periodo_abierto(con, f)
        espejo = [
            LineaAsiento(cuenta=d["cuenta_codigo"], debe=de_centavos(int(d["haber_cent"])),
                         haber=de_centavos(int(d["debe_cent"])), centro_costo=d["centro_costo"],
                         sufijo=d["sufijo"], glosa=d["glosa_linea"])
            for d in con.execute("SELECT * FROM asiento_detalle WHERE asiento_id = ? ORDER BY linea", (asiento_id,))
        ]
        total = validar_cuadre(espejo)
        verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.AJUSTE_CONTABLE, monto=total)
        _validar_naturaleza(con, espejo)
        reversion = _insertar(
            con, sesion, TIPO_REVERSION, f, periodo, f"Reversión de {comprobante}: {causa}", "REVERSION",
            original["documento_ref"], espejo, total, reversa_de=asiento_id, autorizacion_id=autorizacion_id)
        con.execute("UPDATE asientos SET estado = 'REVERSADO' WHERE id = ?", (asiento_id,))
        auditoria.registrar(con, sesion, "asientos", "REVERSA", asiento_id,
                            antes={"estado": "VIGENTE"},
                            despues={"estado": "REVERSADO", "reversion_id": reversion.id,
                                     "reversion": reversion.comprobante, "motivo": causa},
                            detalle=f"Reversión de {comprobante}")
        consumir_autorizacion(con, sesion, autorizacion_id, "asientos", reversion.id,
                              tipo_decision=TipoDecision.AJUSTE_CONTABLE, monto=total)
    return reversion


def crear_asiento_cierre(
    con: sqlite3.Connection,
    sesion: Sesion,
    fecha: Any,
    glosa: str,
    lineas: Iterable[LineaAsiento | Mapping[str, Any]],
    documento_ref: str | None,
    *,
    autorizacion_id: int,
) -> AsientoRegistrado:
    """Registra un asiento de tipo CIERRE (cierre anual del 30 de junio).

    Aplica las mismas validaciones que :func:`crear_asiento` (cuadre exacto,
    cuentas de movimiento, período abierto y naturaleza). La autorización
    AJUSTE_CONTABLE de la Gerencia General (MOF §3.1: los asientos de ajuste y
    cierre los autoriza la GG) se verifica aquí y la consume quien llama, una
    vez registrados todos los asientos del cierre. Los asientos de cierre no se
    reversan (``TIPOS_NO_REVERSIBLES``).

    Raises:
        AutorizacionError: rol sin ``contabilidad/CREAR`` o autorización incompleta.
        PeriodoCerradoError: período o ejercicio cerrado.
        ReglaNegocioError: asiento descuadrado o saldo contrario a la naturaleza.
    """
    exigir_permiso(con, sesion, "contabilidad", "CREAR")
    texto = _texto(glosa, "glosa", 500)
    ref = documento_ref.strip() if isinstance(documento_ref, str) and documento_ref.strip() else None
    f = a_fecha(fecha)
    normalizadas = [normalizar_linea(l, n) for n, l in enumerate(lineas, start=1)]
    with transaccion(con):
        periodo = _periodo_abierto(con, f)
        validadas = _validar_lineas(con, normalizadas, int(periodo["ejercicio"]))
        total = validar_cuadre(validadas)
        verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.AJUSTE_CONTABLE)
        _validar_naturaleza(con, validadas)
        return _insertar(con, sesion, "CIERRE", f, periodo, texto, "CIERRE_ANUAL", ref, validadas, total,
                         autorizacion_id=autorizacion_id)


def cerrar_periodo(con: sqlite3.Connection, sesion: Sesion, periodo_id: int) -> None:
    """Cierra un período mensual (lo ejecuta la Gerencia General, que autoriza
    los cierres según MOF §3.1). Los períodos se cierran en orden y un período
    cerrado no se reabre.

    Desde la Fase 10 el cierre de la aplicación pasa por
    :func:`servicios.cierre.cerrar_mes`, que exige antes la lista de
    verificación FIN-INF-01 con pendientes en cero y archiva el paquete
    FIN-INF-03/04; esta función es el bloqueo contable de bajo nivel.

    Raises:
        AutorizacionError: el rol no tiene ``contabilidad/AUTORIZAR``.
        ReglaNegocioError: período ya cerrado, períodos anteriores abiertos o
            asientos descuadrados en el período.
    """
    exigir_permiso(con, sesion, "contabilidad", "AUTORIZAR")
    with transaccion(con):
        periodo = con.execute("SELECT * FROM periodos WHERE id = ?", (periodo_id,)).fetchone()
        if periodo is None:
            raise DatosInvalidosError(f"No existe el período {periodo_id}.")
        nombre = _nombre_periodo(periodo)
        if periodo["estado"] == "CERRADO":
            raise ReglaNegocioError(f"El período de {nombre} ya está cerrado.", codigo="PERIODO_YA_CERRADO")
        previo = con.execute(
            "SELECT * FROM periodos WHERE fecha_fin < ? AND estado = 'ABIERTO' ORDER BY fecha_inicio LIMIT 1",
            (periodo["fecha_inicio"],)).fetchone()
        if previo is not None:
            raise ReglaNegocioError(f"Primero debe cerrarse el período de {_nombre_periodo(previo)}.",
                                    codigo="PERIODO_ANTERIOR_ABIERTO")
        descuadrados = con.execute(
            """SELECT v.tipo, v.numero FROM v_asientos_descuadrados v JOIN asientos a ON a.id = v.asiento_id
                WHERE a.periodo_id = ?""", (periodo_id,)).fetchall()
        if descuadrados:
            raise ReglaNegocioError("Hay asientos descuadrados en el período: " +
                                    ", ".join(f"{d[0]}-{d[1]}" for d in descuadrados), codigo="PERIODO_DESCUADRADO")
        momento = _ahora_iso()
        con.execute("UPDATE periodos SET estado = 'CERRADO', cerrado_en = ?, cerrado_por = ? WHERE id = ?",
                    (momento, sesion.nombre_usuario, periodo_id))
        auditoria.registrar(con, sesion, "periodos", "CIERRE", periodo_id,
                            antes={"estado": "ABIERTO"},
                            despues={"estado": "CERRADO", "cerrado_en": momento, "cerrado_por": sesion.nombre_usuario},
                            detalle=f"Cierre del período de {nombre}")


# ==========================================================================
# CONSULTAS DE CATÁLOGO
# ==========================================================================
def listar_cuentas_movimiento(con: sqlite3.Connection, ejercicio: int | None = None) -> list[dict[str, Any]]:
    """Cuentas de nivel 4 activas, indicando si son centro de costo y si están
    habilitadas en el ejercicio (cuentas (*) desde el ejercicio parametrizado)."""
    inicio = _ejercicio_alimento(con, ejercicio) if ejercicio is not None else None
    filas = con.execute(
        """SELECT c.codigo, c.nombre, c.naturaleza, c.uso_desde_2027,
                  EXISTS (SELECT 1 FROM centros_costo cc WHERE cc.codigo = c.codigo) AS es_centro
             FROM cuentas c WHERE c.es_movimiento = 1 AND c.activa = 1 ORDER BY c.codigo""").fetchall()
    return [{"codigo": f["codigo"], "nombre": f["nombre"], "naturaleza": f["naturaleza"],
             "es_centro": bool(f["es_centro"]),
             "habilitada": inicio is None or not f["uso_desde_2027"] or ejercicio >= inicio}
            for f in filas]


def listar_sufijos(con: sqlite3.Connection, centro_costo: str, ejercicio: int | None = None) -> list[dict[str, Any]]:
    """Sufijos aplicables a un centro de costo (y si están habilitados en el ejercicio)."""
    inicio = _ejercicio_alimento(con, ejercicio) if ejercicio is not None else None
    filas = con.execute(
        """SELECT s.sufijo, s.nombre, s.elemento, s.uso_desde_2027 FROM sufijos_costo s
             JOIN sufijo_centro sc ON sc.sufijo = s.sufijo WHERE sc.centro_costo = ? ORDER BY s.sufijo""",
        (centro_costo,)).fetchall()
    return [{"sufijo": f["sufijo"], "nombre": f["nombre"], "elemento": f["elemento"],
             "habilitado": inicio is None or not f["uso_desde_2027"] or ejercicio >= inicio} for f in filas]


def listar_periodos(con: sqlite3.Connection, ejercicio: int | None = None) -> list[dict[str, Any]]:
    """Períodos con su estado y cantidad de asientos."""
    sql = """SELECT p.*, (SELECT COUNT(*) FROM asientos a WHERE a.periodo_id = p.id) AS asientos
               FROM periodos p"""
    args: tuple[Any, ...] = ()
    if ejercicio is not None:
        sql += " WHERE p.ejercicio = ?"
        args = (ejercicio,)
    return [dict(f) for f in con.execute(sql + " ORDER BY p.fecha_inicio", args)]


# ==========================================================================
# LIBROS
# ==========================================================================
def _rango(desde: Any, hasta: Any) -> tuple[str | None, str | None]:
    d = a_fecha(desde, "desde").isoformat() if desde is not None else None
    h = a_fecha(hasta, "hasta").isoformat() if hasta is not None else None
    if d and h and d > h:
        raise DatosInvalidosError("La fecha inicial no puede ser posterior a la final.")
    return d, h


def obtener_asiento(con: sqlite3.Connection, sesion: Sesion, asiento_id: int) -> dict[str, Any]:
    """Un asiento con sus líneas (requiere ``contabilidad/VER``)."""
    exigir_permiso(con, sesion, "contabilidad", "VER")
    diario = _diario(con, "a.id = ?", (asiento_id,))
    if not diario:
        raise DatosInvalidosError(f"No existe el asiento {asiento_id}.")
    return diario[0]


def _diario(con: sqlite3.Connection, condicion: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    cabeceras = con.execute(
        f"SELECT a.* FROM asientos a WHERE {condicion} ORDER BY a.fecha, a.id", args).fetchall()
    resultado = []
    for a in cabeceras:
        lineas = [
            {"linea": d["linea"], "cuenta": d["cuenta_codigo"], "nombre": d["nombre"],
             "debe": de_centavos(int(d["debe_cent"])), "haber": de_centavos(int(d["haber_cent"])),
             "centro_costo": d["centro_costo"], "sufijo": d["sufijo"], "glosa": d["glosa_linea"]}
            for d in con.execute(
                """SELECT d.*, c.nombre FROM asiento_detalle d JOIN cuentas c ON c.codigo = d.cuenta_codigo
                    WHERE d.asiento_id = ? ORDER BY d.linea""", (a["id"],))
        ]
        resultado.append({
            "id": a["id"], "tipo": a["tipo"], "numero": a["numero"],
            "comprobante": f"{a['tipo']}-{int(a['numero']):05d}", "fecha": a["fecha"], "glosa": a["glosa"],
            "origen": a["origen"], "documento_ref": a["documento_ref"], "estado": a["estado"],
            "reversa_de": a["reversa_de"], "creado_por": a["creado_por"], "creado_en": a["creado_en"],
            "lineas": lineas,
            "total_debe": sum((l["debe"] for l in lineas), CERO),
            "total_haber": sum((l["haber"] for l in lineas), CERO),
        })
    return resultado


def libro_diario(
    con: sqlite3.Connection, sesion: Sesion, desde: Any = None, hasta: Any = None,
    incluir_reversados: bool = True,
) -> list[dict[str, Any]]:
    """Libro diario cronológico con sus líneas. Los asientos reversados se
    muestran (con estado REVERSADO) junto con su reversión, salvo que se pida
    excluirlos."""
    exigir_permiso(con, sesion, "contabilidad", "VER")
    d, h = _rango(desde, hasta)
    condiciones, args = ["1 = 1"], []
    if d:
        condiciones.append("a.fecha >= ?")
        args.append(d)
    if h:
        condiciones.append("a.fecha <= ?")
        args.append(h)
    if not incluir_reversados:
        condiciones.append("a.estado = 'VIGENTE' AND a.reversa_de IS NULL")
    return _diario(con, " AND ".join(condiciones), tuple(args))


def _signo(naturaleza: str | None) -> int:
    return -1 if naturaleza == "ACREEDORA" else 1


def libro_mayor(
    con: sqlite3.Connection, sesion: Sesion, cuenta: str, desde: Any = None, hasta: Any = None,
    sufijo: str | None = None,
) -> dict[str, Any]:
    """Mayor de una cuenta de movimiento con saldo acumulado según su
    naturaleza (deudora: Debe − Haber; acreedora: Haber − Debe).

    Para un centro de costo puede filtrarse por ``sufijo``.
    """
    exigir_permiso(con, sesion, "contabilidad", "VER")
    info = con.execute("SELECT * FROM cuentas WHERE codigo = ?", (cuenta,)).fetchone()
    if info is None:
        raise DatosInvalidosError(f"La cuenta {cuenta} no existe.")
    if not info["es_movimiento"]:
        raise ReglaNegocioError("El mayor se consulta por cuenta de movimiento (nivel 4).", codigo="CUENTA_NO_MOVIMIENTO")
    d, h = _rango(desde, hasta)
    signo = _signo(info["naturaleza"])
    filtro_suf, args_suf = ("", ()) if not sufijo else (" AND d.sufijo = ?", (str(sufijo).zfill(2),))
    inicial = 0
    if d:
        fila = con.execute(
            f"""SELECT COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0)
                  FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
                 WHERE d.cuenta_codigo = ? AND a.fecha < ?{filtro_suf}""", (cuenta, d, *args_suf)).fetchone()
        inicial = int(fila[0]) * signo
    condiciones, args = ["d.cuenta_codigo = ?"], [cuenta]
    if d:
        condiciones.append("a.fecha >= ?")
        args.append(d)
    if h:
        condiciones.append("a.fecha <= ?")
        args.append(h)
    filas = con.execute(
        f"""SELECT a.id, a.tipo, a.numero, a.fecha, a.glosa, a.estado, d.debe_cent, d.haber_cent, d.sufijo,
                   d.glosa_linea
              FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
             WHERE {' AND '.join(condiciones)}{filtro_suf} ORDER BY a.fecha, a.id, d.linea""",
        (*args, *args_suf)).fetchall()
    saldo = inicial
    movimientos = []
    total_debe = total_haber = 0
    for f in filas:
        saldo += (int(f["debe_cent"]) - int(f["haber_cent"])) * signo
        total_debe += int(f["debe_cent"])
        total_haber += int(f["haber_cent"])
        movimientos.append({
            "asiento_id": f["id"], "comprobante": f"{f['tipo']}-{int(f['numero']):05d}", "fecha": f["fecha"],
            "glosa": f["glosa_linea"] or f["glosa"], "estado": f["estado"], "sufijo": f["sufijo"],
            "debe": de_centavos(int(f["debe_cent"])), "haber": de_centavos(int(f["haber_cent"])),
            "saldo": de_centavos(saldo)})
    return {"cuenta": cuenta, "nombre": info["nombre"], "naturaleza": info["naturaleza"],
            "saldo_inicial": de_centavos(inicial), "movimientos": movimientos,
            "total_debe": de_centavos(total_debe), "total_haber": de_centavos(total_haber),
            "saldo_final": de_centavos(saldo)}


def balance_comprobacion(
    con: sqlite3.Connection, sesion: Sesion, desde: Any = None, hasta: Any = None, nivel: int = 4,
) -> dict[str, Any]:
    """Balance de comprobación de sumas y saldos, agregable a nivel 1-4.

    Returns:
        ``{"filas": [...], "totales": {...}, "cuadra_sumas": bool,
        "cuadra_saldos": bool, "desde": ..., "hasta": ..., "nivel": n}``.
    """
    exigir_permiso(con, sesion, "contabilidad", "VER")
    if nivel not in LARGO_POR_NIVEL:
        raise DatosInvalidosError("El nivel del balance debe ser 1, 2, 3 o 4.")
    d, h = _rango(desde, hasta)
    largo = LARGO_POR_NIVEL[nivel]
    condiciones, args = ["1 = 1"], []
    if d:
        condiciones.append("a.fecha >= ?")
        args.append(d)
    if h:
        condiciones.append("a.fecha <= ?")
        args.append(h)
    filas = con.execute(
        f"""SELECT substr(d.cuenta_codigo, 1, ?) AS codigo, SUM(d.debe_cent) AS debe, SUM(d.haber_cent) AS haber
              FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
             WHERE {' AND '.join(condiciones)} GROUP BY 1 ORDER BY 1""", (largo, *args)).fetchall()
    resultado = []
    tot = {"debe": 0, "haber": 0, "saldo_deudor": 0, "saldo_acreedor": 0}
    for f in filas:
        nombre = con.execute("SELECT nombre FROM cuentas WHERE codigo = ?", (f["codigo"],)).fetchone()
        neto = int(f["debe"]) - int(f["haber"])
        fila = {"codigo": f["codigo"], "nombre": nombre[0] if nombre else "", "nivel": nivel,
                "debe": int(f["debe"]), "haber": int(f["haber"]),
                "saldo_deudor": neto if neto > 0 else 0, "saldo_acreedor": -neto if neto < 0 else 0}
        for k in tot:
            tot[k] += fila[k]
        resultado.append({**fila, **{k: de_centavos(fila[k]) for k in tot}})
    return {"filas": resultado, "totales": {k: de_centavos(v) for k, v in tot.items()},
            "cuadra_sumas": tot["debe"] == tot["haber"],
            "cuadra_saldos": tot["saldo_deudor"] == tot["saldo_acreedor"],
            "desde": d, "hasta": h, "nivel": nivel}


def asientos_descuadrados(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Diagnóstico: asientos con menos de dos líneas o con Debe ≠ Haber."""
    return [dict(f) for f in con.execute("SELECT * FROM v_asientos_descuadrados")]


def describir_periodo(periodo: Mapping[str, Any]) -> str:
    """Texto legible de un período: ``julio de 2026 (01/07/2026 – 31/07/2026)``."""
    inicio = date.fromisoformat(periodo["fecha_inicio"])
    return (f"{NOMBRE_MES[inicio.month - 1]} de {inicio.year} "
            f"({fecha_pantalla(periodo['fecha_inicio'])} – {fecha_pantalla(periodo['fecha_fin'])})")


# ==========================================================================
# REPORTES DE LIBROS (exportación PDF)
# ==========================================================================
GRUPOS_INVENTARIO = ("1", "2", "3", "4")
GRUPOS_RESULTADO = ("5", "6", "7", "8")


def balance_ocho_columnas(con: sqlite3.Connection, sesion: Sesion, desde: Any = None,
                          hasta: Any = None) -> dict[str, Any]:
    """Balance de comprobación de 8 columnas a nivel de cuenta.

    Sumas (Debe, Haber) y saldos (deudor, acreedor) del balance de 4 columnas;
    los saldos de los grupos 1 a 4 pasan al inventario (Activo / Pasivo y
    Patrimonio, con las reguladoras del grupo 4 por su saldo) y los de los
    grupos 5 a 8 a resultados (Pérdidas / Ganancias). Las cuentas de orden
    (grupo 9) solo figuran en sumas y saldos. El excedente (o déficit) del
    período se agrega para igualar ambos pares de columnas y se verifica que
    sea el mismo en el inventario y en resultados.
    """
    base = balance_comprobacion(con, sesion, desde, hasta, 4)
    columnas = ("debe", "haber", "saldo_deudor", "saldo_acreedor", "activo", "pasivo_patrimonio", "perdidas",
                "ganancias")
    tot = {c: CERO for c in columnas}
    filas = []
    for f in base["filas"]:
        grupo = str(f["codigo"])[:1]
        fila = {**f, "activo": CERO, "pasivo_patrimonio": CERO, "perdidas": CERO, "ganancias": CERO}
        if grupo in GRUPOS_INVENTARIO:
            fila["activo"], fila["pasivo_patrimonio"] = f["saldo_deudor"], f["saldo_acreedor"]
        elif grupo in GRUPOS_RESULTADO:
            fila["perdidas"], fila["ganancias"] = f["saldo_deudor"], f["saldo_acreedor"]
        for c in columnas:
            tot[c] += fila[c]
        filas.append(fila)
    resultado_resultados = tot["ganancias"] - tot["perdidas"]
    resultado_inventario = tot["activo"] - tot["pasivo_patrimonio"]
    excedente = resultado_resultados >= 0
    resultado = {"monto": resultado_resultados, "debe": None, "haber": None, "saldo_deudor": None,
                 "saldo_acreedor": None,
                 "activo": None if excedente else -resultado_inventario,
                 "pasivo_patrimonio": resultado_inventario if excedente else None,
                 "perdidas": resultado_resultados if excedente else None,
                 "ganancias": None if excedente else -resultado_resultados}
    finales = {c: tot[c] + (resultado[c] or CERO) for c in columnas}
    return {"filas": filas, "totales": tot, "resultado": resultado, "finales": finales,
            "cuadra_sumas": base["cuadra_sumas"], "cuadra_saldos": base["cuadra_saldos"],
            "cuadra_resultado": resultado_inventario == resultado_resultados
            and finales["activo"] == finales["pasivo_patrimonio"] and finales["perdidas"] == finales["ganancias"],
            "desde": base["desde"], "hasta": base["hasta"], "nivel": 4}


def mayores(con: sqlite3.Connection, sesion: Sesion, desde: Any = None, hasta: Any = None,
            por_sufijo: bool = False) -> list[dict[str, Any]]:
    """Mayor analítico de todas las cuentas con saldo inicial o movimiento en
    el período; con ``por_sufijo`` los centros de costo se abren por sufijo."""
    exigir_permiso(con, sesion, "contabilidad", "VER")
    d, h = _rango(desde, hasta)
    condicion, args = ("a.fecha <= ?", (h,)) if h else ("1 = 1", ())
    cuentas = con.execute(
        f"""SELECT DISTINCT d.cuenta_codigo, d.sufijo,
                   EXISTS (SELECT 1 FROM centros_costo cc WHERE cc.codigo = d.cuenta_codigo) AS es_centro
              FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
             WHERE {condicion} ORDER BY d.cuenta_codigo, d.sufijo""", args).fetchall()
    vistos: list[tuple[str, str | None]] = []
    for f in cuentas:
        clave = (f["cuenta_codigo"], f["sufijo"] if por_sufijo and f["es_centro"] else None)
        if clave not in vistos:
            vistos.append(clave)
    resultado = []
    for cuenta, sufijo in vistos:
        m = libro_mayor(con, sesion, cuenta, d, h, sufijo)
        if m["movimientos"] or m["saldo_inicial"]:
            resultado.append({**m, "sufijo": sufijo})
    return resultado


def plan_de_cuentas(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Plan de Cuentas v3 completo (cuentas activas)."""
    exigir_permiso(con, sesion, "contabilidad", "VER")
    return [dict(f) for f in con.execute("SELECT * FROM cuentas WHERE activa = 1 ORDER BY codigo")]
