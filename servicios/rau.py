"""Régimen Agropecuario Unificado (RAU) y cumplimiento legal y fiscal (Fase 11).

Implementa la POL-07, la Guía CI-FIN-08, el INF-CV-09 y el Plan Estratégico
Tributario PET-RAU-001:

* Calendario fiscal anual con alertas a 30 y 60 días: consulta de la RND antes
  del 30-sep, liquidación individual por socio 20 días hábiles antes del
  vencimiento, Formulario 701 presentado al menos 15 días hábiles antes del
  31-oct, patentes en el mismo plazo y acta de compatibilidad antes del cierre.
* Cuota = hectáreas × cuota por ha, reparto en partes iguales entre los socios
  activos y asiento 7.1.05.02 contra 2.1.03.01, con advertencia de que la
  cuota y la superficie (8 vs 10 ha) están por validar.
* Superficie usada vs declarada y control ante cada CAPEX.
* Indicadores I-1 (autoconsumo) e I-2 (ventas de alimento / ingresos
  operativos) con los umbrales de la sección 1.4 del PET-RAU.
* Balance de masas trimestral del alimento y programa de excedente con tope
  aprobado por el Consejo de Administración; control de cada venta.
* Padrón de compradores no socios; servicios a terceros (alerta roja).
* Revisión trimestral del CV (INF-CV-09, 10 verificaciones con semáforo) e
  informe anual (5 verificaciones del formato INF-CV-09).
* Expediente EXP-RAU A-G con índice maestro y completitud.
* Notificaciones del SIN y plan de respuesta ante fiscalización.

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from db.conexion import a_centavos, de_centavos, obtener_parametros_vigentes, transaccion
from servicios import auditoria, contable, seguridad, tesoreria
from servicios.excepciones import DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil
from servicios.seguridad import Sesion
from utils.formato import (
    a_fecha, dias_habiles_entre, es_dia_habil, fecha_pantalla, formato_bs, redondear_bs, sumar_dias_habiles,
)

CERO = Decimal("0.00")
VERDE, AMARILLO, ROJO = "VERDE", "AMARILLO", "ROJO"
ORDEN_SEMAFORO = {VERDE: 0, AMARILLO: 1, ROJO: 2}
CUENTA_GASTO_RAU = "7.1.05.02"
CUENTA_RAU_POR_PAGAR = "2.1.03.01"
CUENTA_GASTO_PATENTES = "7.1.05.01"
CUENTA_PATENTES_POR_PAGAR = "2.1.03.02"
EVENTOS: dict[str, str] = {
    "CONSULTA_RND": "Consulta de la RND de cuota vigente por hectárea",
    "LIQUIDACION_SOCIOS": "Liquidación individual por socio comunicada",
    "FORM_701": "Presentación y pago del Formulario 701 V.3",
    "PATENTES": "Pago de patentes municipales de los Yungas",
    "ACTA_COMPATIBILIDAD": "Acta de compatibilidad de actividades con el RAU (Consejo de Administración)",
}
TIPOS_SERVICIO: dict[str, str] = {
    "MAQUILA": "Maquila o molienda de insumos de terceros", "FAENADO_ENCARGO": "Faenado por encargo",
    "ALQUILER_PLANTA": "Alquiler de la planta de alimento", "ALQUILER_SILOS": "Alquiler de silos o almacenes",
    "OTRO": "Otro servicio a terceros",
}
TIPOS_NOTIFICACION: dict[str, str] = {
    "ORDEN_VERIFICACION": "Orden de verificación o fiscalización", "REQUERIMIENTO": "Requerimiento de información",
    "VISTA_CARGO": "Vista de cargo (Ley 2492 Art. 96)", "RESOLUCION_DETERMINATIVA": "Resolución determinativa (Art. 99)",
    "INSPECCION": "Inspección en la granja", "EXCLUSION": "Notificación de exclusión del RAU", "OTRA": "Otra",
}
ENTIDADES: dict[str, str] = {"GERENCIA_DISTRITAL": "Gerencia Distrital del SIN", "GRACO": "GRACO", "OTRA": "Otra"}
PASOS: dict[str, str] = {
    "INFORME_24H": "Informe a GG, CA y CV y apertura de la carpeta del caso",
    "ANALISIS": "Análisis del alcance con el asesor (y prórroga si el plazo es insuficiente)",
    "PRORROGA": "Solicitud de prórroga", "RESPUESTA": "Respuesta con documentos foliados y cargo de entrega",
    "DESCARGOS": "Descargos a la Vista de Cargo (decisión del CA)", "RECURSO": "Recurso de alzada o jerárquico (CA)",
    "CIERRE": "Cierre con lecciones aprendidas",
}
# PET-RAU §4.3: respuesta en 72 horas por escenario.
ESCENARIOS: dict[str, tuple[str, str, str]] = {
    "A": ("Observación por venta de alimento («actividad industrial o comercial»)",
          "Suspender ventas a no socios hasta evaluar; reunir carpetas D, E y F; consultar al asesor.",
          "Autoconsumo ≥ 65 %; producción propia; ventas a socios como acto cooperativo; sin maquila ni publicidad."),
    "B": ("Cuestionamiento de superficie o zona",
          "Reunir título, planos y constancia de inscripción; actualizar ante el SIN si hay diferencia.",
          "Carpeta A y registro de superficie; pruebas de uso agropecuario del suelo."),
    "C": ("Exclusión de oficio o migración al régimen general",
          "Convocar al Consejo de Administración; abrir carpeta de caso; preparar descargos o recurso con el asesor.",
          "Art. 4 y Art. 7 DS 24463 aplicados a los hechos; inexistencia de servicios a terceros."),
    "D": ("Compras sin respaldo", "Reunir fichas, certificados y recibos; regularizar proveedores pendientes.",
          "Carpeta E; trazabilidad del origen agropecuario."),
    "E": ("Mora o presentación tardía del Form. 701",
          "Presentar y pagar de inmediato; consultar al asesor sobre sanciones; informar al Consejo de Vigilancia.",
          "Carpeta B; causa documentada."),
}
# Carpeta de Respuesta Rápida (PET-RAU §4.2) → requisitos del índice maestro.
CARPETA_RESPUESTA_RAPIDA: list[tuple[str, tuple[str, ...]]] = [
    ("Certificado RAU y último pago", ("A04",)), ("Formulario 701", ("B01",)), ("Padrón de socios", ("A05",)),
    ("Libro de actas", ("A06",)), ("Certificado SENASAG", ("C07",)),
    ("Bitácora del ciclo y registro de faenado", ("C02", "C05")),
    ("Balance de masas e indicadores I-1 e I-2", ("D02", "D06")), ("Programa de excedente", ("D03",)),
    ("Notas de entrega de alimento", ("D05",)), ("Fichas de proveedores", ("E01",)), ("Patentes", ("B04",)),
]
NOMBRE_TRIMESTRE_EJERCICIO = {1: "julio-septiembre", 2: "octubre-diciembre", 3: "enero-marzo", 4: "abril-junio"}


# ==========================================================================
# UTILIDADES
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 300) -> str:
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
    if minimo is not None and valor < minimo or maximo is not None and valor > maximo:
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
        raise ReglaNegocioError(f"Falta el parámetro vigente «{clave}» (regla 2: ninguna cifra normativa fija).",
                                codigo="PARAMETRO_FALTANTE")
    return p[clave]


def _ejercicio(con: sqlite3.Connection, ejercicio: int) -> sqlite3.Row:
    _entero(ejercicio, "gestión")
    fila = con.execute("SELECT * FROM ejercicios WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la gestión {ejercicio}.")
    return fila


def ejercicio_de(con: sqlite3.Connection, fecha: Any) -> int:
    f = a_fecha(fecha).isoformat()
    fila = con.execute("SELECT ejercicio FROM ejercicios WHERE ? BETWEEN fecha_inicio AND fecha_fin", (f,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"La fecha {fecha_pantalla(f)} no pertenece a ninguna gestión registrada.")
    return int(fila[0])


def _rango(con: sqlite3.Connection, ejercicio: int) -> tuple[date, date]:
    fila = _ejercicio(con, ejercicio)
    return date.fromisoformat(fila["fecha_inicio"]), date.fromisoformat(fila["fecha_fin"])


def peor(semaforos: Iterable[str | None]) -> str:
    """El semáforo más grave de la lista (los ``None`` = no aplica)."""
    valores = [s for s in semaforos if s]
    return max(valores, key=lambda s: ORDEN_SEMAFORO[s]) if valores else VERDE


def _pct(fraccion: Decimal | None) -> str:
    if fraccion is None:
        return "—"
    return f"{(fraccion * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}".replace(".", ",") + " %"


def _fraccion(num: int, den: int) -> Decimal | None:
    if den == 0:
        return None
    return (Decimal(num) / Decimal(den)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _ha(mil: int) -> Decimal:
    return de_mil(int(mil))


def _ha_txt(mil: int) -> str:
    """Hectáreas en formato de pantalla (8; 10,5; 1.250,25)."""
    valor = de_mil(int(mil)).normalize()
    entero, _, dec = f"{valor:f}".partition(".")
    entero = f"{int(entero):,}".replace(",", ".")
    return f"{entero},{dec}" if dec else entero


def _exigir_alguno(con: sqlite3.Connection, sesion: Sesion, recurso: str, acciones: Sequence[str]) -> None:
    if any(seguridad.tiene_permiso(con, sesion.rol, recurso, a) for a in acciones):
        return
    seguridad.exigir_permiso(con, sesion, recurso, acciones[0])


def _slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-")[:60]


# ==========================================================================
# 1. EXPEDIENTE EXP-RAU A-G (PET-RAU §3.3)
# ==========================================================================
def nombre_digital_expediente(fecha: Any, carpeta: str, documento: str) -> str:
    """Nomenclatura digital del expediente: ``AAAA-MM-DD_Carpeta_Documento``."""
    return f"{a_fecha(fecha).isoformat()}_{carpeta}_{_slug(documento)}.pdf"


def _requisito(con: sqlite3.Connection, clave: str) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM exp_rau_requisitos WHERE clave = ?", (clave,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el documento {clave!r} en el índice maestro EXP-RAU.")
    return fila


def _indexar(con: sqlite3.Connection, sesion: Sesion, clave: str, gestion: int, fecha: Any, referencia: str,
             ubicacion: str | None = None, origen: str = "SISTEMA") -> int:
    req = _requisito(con, clave)
    f = a_fecha(fecha)
    cur = con.execute(
        """INSERT INTO exp_rau_documentos (requisito_id, gestion, fecha_documento, referencia, archivo_digital,
               ubicacion_fisica, origen, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
        (int(req["id"]), gestion, f.isoformat(), referencia, nombre_digital_expediente(f, req["carpeta"], req["documento"]),
         ubicacion, origen, sesion.nombre_usuario, _ahora_iso()))
    did = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "exp_rau_documentos", "INSERT", did,
                        despues={"clave": clave, "gestion": gestion, "referencia": referencia, "origen": origen})
    return did


def registrar_documento_expediente(con: sqlite3.Connection, sesion: Sesion, clave: str, gestion: int,
                                   fecha_documento: Any, referencia: str, ubicacion_fisica: str | None = None) -> int:
    """Indexa un documento en la carpeta EXP-RAU que corresponde."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    ref = _texto(referencia, "la referencia del documento", 200)
    with transaccion(con):
        _ejercicio(con, gestion)
        return _indexar(con, sesion, clave, gestion, fecha_documento, ref,
                        _opcional(ubicacion_fisica, "ubicación física", 120), "MANUAL")


def indice_maestro(con: sqlite3.Connection, sesion: Sesion, gestion: int) -> dict[str, Any]:
    """Índice maestro A-G con el porcentaje de completitud (meta ≥ 95 %).

    Los documentos PERMANENTES se cubren con cualquier registro; los ANUALES,
    con un registro de la gestión consultada."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return _indice(con, gestion)


def _indice(con: sqlite3.Connection, gestion: int) -> dict[str, Any]:
    p = _params(con, gestion)
    carpetas: dict[str, dict[str, Any]] = {}
    total = cubiertos = 0
    for r in con.execute("SELECT * FROM exp_rau_requisitos ORDER BY clave"):
        if r["periodicidad"] == "ANUAL":
            docs = con.execute("SELECT * FROM exp_rau_documentos WHERE requisito_id = ? AND gestion = ? ORDER BY id",
                               (r["id"], gestion)).fetchall()
        else:
            docs = con.execute("SELECT * FROM exp_rau_documentos WHERE requisito_id = ? ORDER BY id",
                               (r["id"],)).fetchall()
        cubierto = bool(docs)
        total += 1
        cubiertos += int(cubierto)
        c = carpetas.setdefault(r["carpeta"], {"carpeta": r["carpeta"], "items": [], "total": 0, "cubiertos": 0})
        c["items"].append({"clave": r["clave"], "documento": r["documento"], "responsable": r["responsable"],
                           "periodicidad": r["periodicidad"], "cubierto": cubierto,
                           "documentos": [dict(d) for d in docs]})
        c["total"] += 1
        c["cubiertos"] += int(cubierto)
    for c in carpetas.values():
        c["completitud"] = _fraccion(c["cubiertos"], c["total"])
    completitud = _fraccion(cubiertos, total) or Decimal(0)
    minimo = Decimal(_param(p, "rau_expediente_completitud_min"))
    return {"gestion": gestion, "carpetas": list(carpetas.values()), "total": total, "cubiertos": cubiertos,
            "completitud": completitud, "minimo": minimo, "semaforo": VERDE if completitud >= minimo else AMARILLO}


def _cubiertos(con: sqlite3.Connection, gestion: int) -> set[str]:
    return {i["clave"] for c in _indice(con, gestion)["carpetas"] for i in c["items"] if i["cubierto"]}


# ==========================================================================
# 2. CALENDARIO FISCAL (POL-07 Arts. 2-3; PET-RAU §3.1-3.2)
# ==========================================================================
def limite_anticipacion(vencimiento: date, dias_habiles: int) -> date:
    """Último día hábil que deja al menos ``dias_habiles`` días hábiles
    completos antes del vencimiento (el día de la presentación no se cuenta)."""
    d = vencimiento
    while dias_habiles_entre(d, vencimiento) < dias_habiles or not es_dia_habil(d):
        d -= timedelta(days=1)
    return d


def fechas_calendario(con: sqlite3.Connection, gestion: int) -> dict[str, date]:
    """Fechas límite del cumplimiento de la gestión.

    # DISCREPANCIA: la POL-07 habla del Formulario 701 «de cada año»; el
    # PET-RAU §3.2 precisa que la cuota de la gestión N se declara con
    # vencimiento el 31 de octubre de N+1 (a confirmar con el SIN al
    # inscribirse). Se aplica el PET-RAU: la gestión 2026 (1-jul-2026 a
    # 30-jun-2027) vence el 31-oct-2027.
    """
    _, fin = _rango(con, gestion)
    p = _params(con, gestion)
    anio = gestion + 1
    vencimiento = date(anio, int(_param(p, "rau_vencimiento_mes")), int(_param(p, "rau_vencimiento_dia")))
    limite_701 = limite_anticipacion(vencimiento, int(_param(p, "rau_anticipacion_dias_habiles")))
    return {
        "CONSULTA_RND": date(anio, int(_param(p, "rau_consulta_cuota_mes")), int(_param(p, "rau_consulta_cuota_dia"))),
        "LIQUIDACION_SOCIOS": limite_anticipacion(vencimiento, int(_param(p, "rau_liquidacion_socio_dias_habiles"))),
        "FORM_701": limite_701,
        "PATENTES": limite_701,
        "ACTA_COMPATIBILIDAD": fin,
        "VENCIMIENTO": vencimiento,
    }


def evaluar_701(con: sqlite3.Connection, gestion: int, fecha_presentacion: Any) -> dict[str, Any]:
    """Semáforo de la presentación del Formulario 701 (PET-RAU §1.4,
    «Incumplimiento formal»): verde con ≥ 15 días hábiles de anticipación,
    amarillo con menos, rojo fuera del vencimiento."""
    f = a_fecha(fecha_presentacion)
    fechas = fechas_calendario(con, gestion)
    venc = fechas["VENCIMIENTO"]
    minimo = int(_param(_params(con, gestion), "rau_anticipacion_dias_habiles"))
    dias = dias_habiles_entre(f, venc)
    if f > venc:
        semaforo = ROJO
    elif dias < minimo:
        semaforo = AMARILLO
    else:
        semaforo = VERDE
    return {"gestion": gestion, "fecha": f.isoformat(), "vencimiento": venc.isoformat(),
            "limite": fechas["FORM_701"].isoformat(), "dias_habiles_antes": dias, "minimo": minimo,
            "semaforo": semaforo}


def _cumplido(con: sqlite3.Connection, gestion: int, evento: str) -> tuple[str | None, str | None, str | None]:
    """(fecha, referencia, semáforo registrado) del cumplimiento del evento."""
    if evento in ("CONSULTA_RND", "ACTA_COMPATIBILIDAD"):
        f = con.execute("SELECT fecha, referencia FROM cumplimientos_fiscales WHERE gestion = ? AND evento = ?",
                        (gestion, evento)).fetchone()
        return (f["fecha"], f["referencia"], None) if f else (None, None, None)
    if evento == "LIQUIDACION_SOCIOS":
        f = con.execute("SELECT comunicada_en, id FROM liquidaciones_rau WHERE gestion = ?", (gestion,)).fetchone()
        return (f["comunicada_en"], f"Liquidación N.º {f['id']}", None) if f and f["comunicada_en"] else (None, None, None)
    if evento == "FORM_701":
        f = con.execute("SELECT fecha_presentacion, numero_orden, semaforo FROM formularios_701 WHERE gestion = ?",
                        (gestion,)).fetchone()
        return (f["fecha_presentacion"], f["numero_orden"], f["semaforo"]) if f else (None, None, None)
    if evento == "PATENTES":
        f = con.execute("SELECT MAX(fecha_pago) AS fecha, GROUP_CONCAT(comprobante_ref, ', ') AS ref, "
                        "MIN(dentro_plazo) AS ok FROM patentes_municipales WHERE gestion = ?", (gestion,)).fetchone()
        if f and f["fecha"]:
            return f["fecha"], f["ref"], VERDE if int(f["ok"]) else AMARILLO
        return None, None, None
    raise DatosInvalidosError(f"Evento desconocido: {evento!r}.")


def calendario_fiscal(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha: Any = None) -> list[dict[str, Any]]:
    """Calendario de la gestión con el estado de cada obligación y las alertas
    a 30 y 60 días (``rau_alerta_dias`` y ``rau_alerta_temprana_dias``)."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return _calendario(con, gestion, a_fecha(fecha) if fecha is not None else date.today())


def _calendario(con: sqlite3.Connection, gestion: int, referencia: date) -> list[dict[str, Any]]:
    p = _params(con, gestion)
    alerta, temprana = int(_param(p, "rau_alerta_dias")), int(_param(p, "rau_alerta_temprana_dias"))
    fechas = fechas_calendario(con, gestion)
    filas = []
    for evento, descripcion in EVENTOS.items():
        limite = fechas[evento]
        cumplido, referencia_doc, semaforo_reg = _cumplido(con, gestion, evento)
        dias = (limite - referencia).days
        if cumplido:
            if semaforo_reg:
                semaforo = semaforo_reg
            elif a_fecha(cumplido) <= limite:
                semaforo = VERDE
            else:
                semaforo = AMARILLO if a_fecha(cumplido) <= fechas["VENCIMIENTO"] else ROJO
            estado = "CUMPLIDO" if semaforo == VERDE else "CUMPLIDO_CON_OBSERVACION"
        elif dias < 0:
            estado, semaforo = "VENCIDO", ROJO
        elif dias <= alerta:
            estado, semaforo = "ALERTA_30", AMARILLO
        elif dias <= temprana:
            estado, semaforo = "ALERTA_60", AMARILLO
        else:
            estado, semaforo = "PROGRAMADO", VERDE
        filas.append({"gestion": gestion, "evento": evento, "descripcion": descripcion, "limite": limite.isoformat(),
                      "vencimiento": fechas["VENCIMIENTO"].isoformat() if evento in ("FORM_701", "PATENTES") else None,
                      "cumplido_en": cumplido, "referencia": referencia_doc, "dias": dias, "estado": estado,
                      "semaforo": semaforo})
    return filas


def alertas_calendario(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Obligaciones vencidas o dentro de la ventana de 30/60 días."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    alertas = []
    for (g,) in con.execute("SELECT ejercicio FROM ejercicios ORDER BY ejercicio").fetchall():
        for e in _calendario(con, int(g), referencia):
            if e["estado"] in ("VENCIDO", "ALERTA_30", "ALERTA_60"):
                texto = (f"vencido hace {-e['dias']} día(s)" if e["estado"] == "VENCIDO"
                         else f"vence en {e['dias']} día(s)")
                alertas.append({**e, "mensaje": f"Gestión {g}: {e['descripcion']} — límite "
                                                f"{fecha_pantalla(e['limite'])}, {texto}."})
    return alertas


def registrar_consulta_rnd(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha: Any, rnd_ref: str,
                           cuota_ha_confirmada: Any) -> dict[str, Any]:
    """Consulta de la RND de la cuota por hectárea (POL-07 Art. 3, antes del
    30-sep). Si el valor difiere del parámetro, advierte que la GG debe
    actualizar ``rau_cuota_por_ha`` (regla 2)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    f = a_fecha(fecha)
    ref = _texto(rnd_ref, "la RND consultada", 120)
    cuota = redondear_bs(_decimal(cuota_ha_confirmada, "cuota por hectárea confirmada"))
    if cuota <= 0:
        raise DatosInvalidosError("La cuota por hectárea debe ser mayor a cero.")
    with transaccion(con):
        _ejercicio(con, gestion)
        if con.execute("SELECT 1 FROM cumplimientos_fiscales WHERE gestion = ? AND evento = 'CONSULTA_RND'",
                       (gestion,)).fetchone():
            raise ReglaNegocioError("La consulta de la RND de esta gestión ya está registrada.", codigo="YA_REGISTRADO")
        vigente = redondear_bs(_param(_params(con, gestion), "rau_cuota_por_ha"))
        cid = int(con.execute(
            """INSERT INTO cumplimientos_fiscales (gestion, evento, fecha, referencia, valor, detalle, registrado_por,
                   registrado_en) VALUES (?, 'CONSULTA_RND', ?, ?, ?, ?, ?, ?)""",
            (gestion, f.isoformat(), ref, str(cuota), f"Parámetro vigente: {vigente}", sesion.nombre_usuario,
             _ahora_iso())).lastrowid)
        _indexar(con, sesion, "B02", gestion, f, ref)
        auditoria.registrar(con, sesion, "cumplimientos_fiscales", "INSERT", cid,
                            despues={"gestion": gestion, "evento": "CONSULTA_RND", "rnd": ref, "cuota": cuota})
    advertencia = None if cuota == vigente else (
        f"La RND fija {formato_bs(cuota)} por hectárea y el parámetro rau_cuota_por_ha vigente es {formato_bs(vigente)}: la Gerencia "
        "General debe actualizar el parámetro antes de liquidar.")
    limite = fechas_calendario(con, gestion)["CONSULTA_RND"]
    return {"id": cid, "dentro_plazo": f <= limite, "limite": limite.isoformat(), "advertencia": advertencia}


def registrar_acta_compatibilidad(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha: Any, acta_ref: str,
                                  detalle: str) -> int:
    """Revisión anual de compatibilidad de actividades con el RAU (POL-07 Art. 1;
    CI-FIN-08 Paso 1), documentada en acta del Consejo de Administración."""
    seguridad.exigir_permiso(con, sesion, "rau", "APROBAR")
    f = a_fecha(fecha)
    acta = _texto(acta_ref, "el acta del Consejo de Administración", 120)
    texto = _texto(detalle, "el resultado de la revisión", 1000)
    with transaccion(con):
        _ejercicio(con, gestion)
        if con.execute("SELECT 1 FROM cumplimientos_fiscales WHERE gestion = ? AND evento = 'ACTA_COMPATIBILIDAD'",
                       (gestion,)).fetchone():
            raise ReglaNegocioError("El acta de compatibilidad de esta gestión ya está registrada.", codigo="YA_REGISTRADO")
        cid = int(con.execute(
            """INSERT INTO cumplimientos_fiscales (gestion, evento, fecha, referencia, detalle, registrado_por,
                   registrado_en) VALUES (?, 'ACTA_COMPATIBILIDAD', ?, ?, ?, ?, ?)""",
            (gestion, f.isoformat(), acta, texto, sesion.nombre_usuario, _ahora_iso())).lastrowid)
        _indexar(con, sesion, "B05", gestion, f, acta)
        _indexar(con, sesion, "G01", gestion, f, acta)
        auditoria.registrar(con, sesion, "cumplimientos_fiscales", "INSERT", cid,
                            despues={"gestion": gestion, "evento": "ACTA_COMPATIBILIDAD", "acta": acta})
    return cid


# ==========================================================================
# 3. CERTIFICADO RAU Y SUPERFICIE (PET-RAU §1.2 y §1.4)
# ==========================================================================
def registrar_certificado(con: sqlite3.Connection, sesion: Sesion, numero: str, fecha_emision: Any,
                          vigente_hasta: Any = None, ultimo_pago_ref: str | None = None) -> int:
    """Certificado de Inscripción al RAU (se exhibe en cada venta, DS 24463)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    nro = _texto(numero, "el número del certificado", 60)
    f = a_fecha(fecha_emision)
    hasta = a_fecha(vigente_hasta) if vigente_hasta is not None else None
    if hasta is not None and hasta < f:
        raise DatosInvalidosError("La vigencia no puede ser anterior a la emisión.")
    with transaccion(con):
        if con.execute("SELECT 1 FROM certificados_rau WHERE numero = ?", (nro,)).fetchone():
            raise ReglaNegocioError("El certificado ya está registrado.", codigo="YA_REGISTRADO")
        cid = int(con.execute(
            """INSERT INTO certificados_rau (numero, fecha_emision, vigente_hasta, ultimo_pago_ref, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?)""",
            (nro, f.isoformat(), hasta.isoformat() if hasta else None, _opcional(ultimo_pago_ref, "último pago", 120),
             sesion.nombre_usuario, _ahora_iso())).lastrowid)
        _indexar(con, sesion, "A04", ejercicio_de(con, f), f, f"Certificado RAU N.º {nro}")
        auditoria.registrar(con, sesion, "certificados_rau", "INSERT", cid, despues={"numero": nro, "emision": f})
    return cid


def certificado_vigente(con: sqlite3.Connection, fecha: Any) -> dict[str, Any] | None:
    f = a_fecha(fecha).isoformat()
    fila = con.execute("SELECT * FROM certificados_rau WHERE fecha_emision <= ? AND (vigente_hasta IS NULL OR "
                       "vigente_hasta >= ?) ORDER BY fecha_emision DESC LIMIT 1", (f, f)).fetchone()
    return dict(fila) if fila else None


def registrar_superficie_declarada(con: sqlite3.Connection, sesion: Sesion, fecha: Any, hectareas: Any, zona: str,
                                   constancia_ref: str) -> int:
    """Superficie inscrita en el SIN (constancia de inscripción o actualización)."""
    _exigir_alguno(con, sesion, "rau", ("CREAR", "AUTORIZAR"))
    f = a_fecha(fecha)
    ha = a_mil(hectareas, "hectáreas")
    if ha <= 0:
        raise DatosInvalidosError("Las hectáreas deben ser mayores a cero.")
    with transaccion(con):
        sid = int(con.execute(
            """INSERT INTO superficies_declaradas (fecha, hectareas_mil, zona, constancia_ref, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?)""",
            (f.isoformat(), ha, _texto(zona, "la zona/subzona", 120), _texto(constancia_ref, "la constancia del SIN", 120),
             sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "superficies_declaradas", "INSERT", sid,
                            despues={"fecha": f, "hectareas": de_mil(ha), "constancia": constancia_ref})
    return sid


def registrar_superficie_usada(con: sqlite3.Connection, sesion: Sesion, componente: str, hectareas: Any, desde: Any,
                               documento_ref: str | None = None) -> int:
    """Superficie ocupada por un componente (galpones, planta, depósitos…)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    ha = a_mil(hectareas, "hectáreas")
    if ha <= 0:
        raise DatosInvalidosError("Las hectáreas deben ser mayores a cero.")
    with transaccion(con):
        uid = int(con.execute(
            """INSERT INTO superficies_usadas (componente, hectareas_mil, desde, documento_ref, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?)""",
            (_texto(componente, "el componente", 120), ha, a_fecha(desde).isoformat(),
             _opcional(documento_ref, "documento", 120), sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "superficies_usadas", "INSERT", uid,
                            despues={"componente": componente, "hectareas": de_mil(ha), "desde": desde})
    return uid


def finalizar_superficie_usada(con: sqlite3.Connection, sesion: Sesion, superficie_id: int, hasta: Any) -> None:
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    with transaccion(con):
        fila = con.execute("SELECT * FROM superficies_usadas WHERE id = ?", (superficie_id,)).fetchone()
        if fila is None:
            raise DatosInvalidosError(f"No existe la superficie {superficie_id}.")
        h = a_fecha(hasta)
        if h < date.fromisoformat(fila["desde"]):
            raise DatosInvalidosError("La fecha de término no puede ser anterior al inicio.")
        con.execute("UPDATE superficies_usadas SET hasta = ? WHERE id = ?", (h.isoformat(), superficie_id))
        auditoria.registrar(con, sesion, "superficies_usadas", "UPDATE", superficie_id, antes={"hasta": None},
                            despues={"hasta": h})


def estado_superficie(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    """Superficie usada vs declarada. Si no hay constancia registrada se usa
    ``rau_hectareas`` (POR VALIDAR)."""
    f = a_fecha(fecha)
    usada = int(con.execute("SELECT COALESCE(SUM(hectareas_mil), 0) FROM superficies_usadas WHERE desde <= ? AND "
                            "(hasta IS NULL OR hasta >= ?)", (f.isoformat(), f.isoformat())).fetchone()[0])
    decl = con.execute("SELECT * FROM superficies_declaradas WHERE fecha <= ? ORDER BY fecha DESC, id DESC LIMIT 1",
                       (f.isoformat(),)).fetchone()
    if decl is not None:
        declarada, fuente = int(decl["hectareas_mil"]), f"Constancia SIN {decl['constancia_ref']}"
    else:
        declarada = a_mil(_param(_params(con, ejercicio_de(con, f)), "rau_hectareas"), "hectáreas")
        fuente = "Parámetro rau_hectareas (POR VALIDAR: sin constancia de inscripción registrada)"
    return {"fecha": f.isoformat(), "usada": _ha(usada), "declarada": _ha(declarada), "fuente_declarada": fuente,
            "constancia": decl is not None, "semaforo": ROJO if usada > declarada else VERDE,
            "usada_mil": usada, "declarada_mil": declarada}


def controlar_capex(con: sqlite3.Connection, sesion: Sesion, fecha: Any, hectareas_adicionales: Any, descripcion: str,
                    capex_linea_id: int | None = None) -> dict[str, Any]:
    """Prueba de compatibilidad RAU de una inversión (PET-RAU §1.2 y §1.4):
    rojo si la superficie ocupada ya supera la declarada; amarillo si con la
    inversión la superará sin actualizar la inscripción; verde si no."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    f = a_fecha(fecha)
    adicional = a_mil(hectareas_adicionales, "hectáreas adicionales", permitir_cero=True)
    if adicional < 0:
        raise DatosInvalidosError("Las hectáreas adicionales no pueden ser negativas.")
    detalle = _texto(descripcion, "la descripción de la inversión", 300)
    with transaccion(con):
        if capex_linea_id is not None:
            if con.execute("SELECT 1 FROM capex_lineas WHERE id = ?", (capex_linea_id,)).fetchone() is None:
                raise DatosInvalidosError(f"No existe la línea CAPEX {capex_linea_id}.")
        sup = estado_superficie(con, f)
        if sup["usada_mil"] > sup["declarada_mil"]:
            semaforo = ROJO
            accion = "Superficie ocupada mayor a la declarada: actualizar de inmediato la inscripción ante el SIN."
        elif sup["usada_mil"] + adicional > sup["declarada_mil"]:
            semaforo = AMARILLO
            accion = (f"La inversión lleva la superficie a {_ha_txt(sup['usada_mil'] + adicional)} ha frente a "
                      f"{_ha_txt(sup['declarada_mil'])} ha declaradas: actualizar ante el SIN antes de ejecutarla.")
        else:
            semaforo = VERDE
            accion = "Compatible: la superficie declarada cubre la inversión."
        cid = int(con.execute(
            """INSERT INTO controles_superficie (fecha, capex_linea_id, descripcion, hectareas_adicionales_mil,
                   usadas_mil, declaradas_mil, semaforo, accion, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), capex_linea_id, detalle, adicional, sup["usada_mil"], sup["declarada_mil"], semaforo, accion,
             sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "controles_superficie", "INSERT", cid,
                            despues={"descripcion": detalle, "adicional": de_mil(adicional), "semaforo": semaforo})
    return {"id": cid, "semaforo": semaforo, "accion": accion, "usada": sup["usada"], "declarada": sup["declarada"],
            "proyectada": _ha(sup["usada_mil"] + adicional)}


def capex_sin_control(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Líneas CAPEX vigentes que aún no pasaron el control de superficie."""
    return [dict(f) for f in con.execute(
        """SELECT l.id, l.activo, l.categoria, l.fecha_estimada, p.ejercicio FROM capex_lineas l
             JOIN planes_capex p ON p.id = l.plan_id
            WHERE l.estado = 'VIGENTE' AND NOT EXISTS (SELECT 1 FROM controles_superficie c WHERE c.capex_linea_id = l.id)
            ORDER BY l.fecha_estimada""")]


# ==========================================================================
# 4. CUOTA, LIQUIDACIÓN POR SOCIO, FORMULARIO 701 Y PATENTES
# ==========================================================================
def advertencias_cuota(con: sqlite3.Connection, gestion: int, hectareas_mil: int, desde_constancia: bool) -> list[str]:
    """Advertencias visibles de que la cuota y la superficie están por validar."""
    adv: list[str] = []
    filas = {f["clave"]: f for f in con.execute(
        "SELECT clave, valor, fuente_documental, descripcion FROM parametros WHERE clave IN ('rau_hectareas','rau_cuota_por_ha') "
        "AND ejercicio_desde <= ? AND (ejercicio_hasta IS NULL OR ejercicio_hasta >= ?)", (gestion, gestion))}
    consulta = con.execute("SELECT valor, referencia FROM cumplimientos_fiscales WHERE gestion = ? AND "
                           "evento = 'CONSULTA_RND'", (gestion,)).fetchone()
    cuota = filas.get("rau_cuota_por_ha")
    if consulta is None and cuota is not None:
        adv.append(f"Cuota POR VALIDAR: {formato_bs(Decimal(cuota['valor']))} por hectárea (fuente: "
                   f"{cuota['fuente_documental']}). En la RND 102500000038 ese valor figura como cuota pecuaria de la "
                   "zona Altiplano; confirme el valor de la subzona de Caranavi en el Anexo II vigente del DS 24463 "
                   "y registre la consulta de la RND.")
    elif consulta is not None and cuota is not None and Decimal(consulta["valor"]) != Decimal(cuota["valor"]):
        adv.append(f"La RND consultada ({consulta['referencia']}) fija {formato_bs(Decimal(consulta['valor']))}/ha y "
                   f"el parámetro es {formato_bs(Decimal(cuota['valor']))}/ha: actualice el parámetro rau_cuota_por_ha.")
    ha = filas.get("rau_hectareas")
    if not desde_constancia and ha is not None:
        adv.append(f"Superficie POR VALIDAR: se usan {_ha_txt(hectareas_mil)} ha del parámetro rau_hectareas "
                   "(Manual Presupuestario P-03), pero el balance de apertura usa 10 ha. Registre la constancia de "
                   "inscripción del SIN para fijar la superficie (8 vs 10 ha).")
    return adv


def calcular_cuota(con: sqlite3.Connection, gestion: int) -> dict[str, Any]:
    """Cuota total = hectáreas × cuota por ha; reparto en partes iguales entre
    los socios activos (POL-07 Art. 3; PET-RAU §3.2 paso 3)."""
    _, fin = _rango(con, gestion)
    p = _params(con, gestion)
    sup = estado_superficie(con, fin)
    ha_mil = sup["declarada_mil"]
    cuota_ha = redondear_bs(_param(p, "rau_cuota_por_ha"))
    total = redondear_bs(_ha(ha_mil) * cuota_ha)
    socios = con.execute("SELECT id, numero, nombre_completo FROM socios WHERE estado = 'ACTIVO' ORDER BY numero").fetchall()
    if not socios:
        raise ReglaNegocioError("No hay socios activos para liquidar.", codigo="SIN_SOCIOS")
    total_cent = a_centavos(total)
    base, resto = divmod(total_cent, len(socios))
    reparto = [{"socio_id": int(s["id"]), "numero": int(s["numero"]), "socio": s["nombre_completo"],
                "monto": de_centavos(base + (1 if i < resto else 0))} for i, s in enumerate(socios)]
    return {"gestion": gestion, "hectareas": _ha(ha_mil), "hectareas_mil": ha_mil, "cuota_ha": cuota_ha,
            "total": total, "socios": len(socios), "por_socio": de_centavos(base), "reparto": reparto,
            "fuente_superficie": sup["fuente_declarada"],
            "advertencias": advertencias_cuota(con, gestion, ha_mil, sup["constancia"])}


def registrar_liquidacion(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha: Any, *,
                          fecha_devengo: Any = None) -> dict[str, Any]:
    """Registra la liquidación RAU de la gestión y, si se indica
    ``fecha_devengo`` (dentro de la gestión, período abierto), el asiento de
    devengo 7.1.05.02 contra 2.1.03.01 (PC-01; PET-RAU §3.2)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    f = a_fecha(fecha)
    with transaccion(con):
        if con.execute("SELECT 1 FROM liquidaciones_rau WHERE gestion = ?", (gestion,)).fetchone():
            raise ReglaNegocioError(f"La liquidación RAU de la gestión {gestion} ya existe.", codigo="YA_REGISTRADO")
        calc = calcular_cuota(con, gestion)
        asiento = None
        if fecha_devengo is not None:
            fd = a_fecha(fecha_devengo)
            inicio, fin = _rango(con, gestion)
            if not inicio <= fd <= fin:
                raise DatosInvalidosError(f"El devengo se registra dentro de la gestión {gestion} "
                                          f"({fecha_pantalla(inicio)} al {fecha_pantalla(fin)}).")
            asiento = contable.crear_asiento(
                con, sesion, fd, f"Devengo de la cuota RAU de la gestión {gestion}: {_ha_txt(calc['hectareas_mil'])} ha × "
                f"{formato_bs(calc['cuota_ha'])}", [{"cuenta": CUENTA_GASTO_RAU, "debe": calc["total"]},
                                          {"cuenta": CUENTA_RAU_POR_PAGAR, "haber": calc["total"]}],
                "RAU", f"RAU-{gestion}", tipo="TRASPASO")
        lid = int(con.execute(
            """INSERT INTO liquidaciones_rau (gestion, fecha, hectareas_mil, cuota_ha, total_cent, socios, advertencias,
                   asiento_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (gestion, f.isoformat(), calc["hectareas_mil"], str(calc["cuota_ha"]), a_centavos(calc["total"]),
             calc["socios"], json.dumps(calc["advertencias"], ensure_ascii=False), asiento.id if asiento else None,
             sesion.nombre_usuario, _ahora_iso())).lastrowid)
        for r in calc["reparto"]:
            con.execute("INSERT INTO liquidaciones_rau_socios (liquidacion_id, socio_id, monto_cent) VALUES (?,?,?)",
                        (lid, r["socio_id"], a_centavos(r["monto"])))
        auditoria.registrar(con, sesion, "liquidaciones_rau", "INSERT", lid,
                            despues={"gestion": gestion, "hectareas": calc["hectareas"], "cuota_ha": calc["cuota_ha"],
                                     "total": calc["total"], "asiento": asiento.comprobante if asiento else None,
                                     "advertencias": calc["advertencias"]})
    return {"id": lid, **calc, "asiento": asiento.comprobante if asiento else None}


def devengar_liquidacion(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha_devengo: Any) -> str:
    """Asiento de devengo de una liquidación registrada sin él."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    fd = a_fecha(fecha_devengo)
    with transaccion(con):
        liq = con.execute("SELECT * FROM liquidaciones_rau WHERE gestion = ?", (gestion,)).fetchone()
        if liq is None:
            raise ReglaNegocioError("Registre primero la liquidación.", codigo="SIN_LIQUIDACION")
        if liq["asiento_id"]:
            raise ReglaNegocioError("La cuota ya está devengada.", codigo="YA_DEVENGADA")
        inicio, fin = _rango(con, gestion)
        if not inicio <= fd <= fin:
            raise DatosInvalidosError(f"El devengo se registra dentro de la gestión {gestion}.")
        total = de_centavos(int(liq["total_cent"]))
        asiento = contable.crear_asiento(
            con, sesion, fd, f"Devengo de la cuota RAU de la gestión {gestion}",
            [{"cuenta": CUENTA_GASTO_RAU, "debe": total}, {"cuenta": CUENTA_RAU_POR_PAGAR, "haber": total}],
            "RAU", f"RAU-{gestion}", tipo="TRASPASO")
        con.execute("UPDATE liquidaciones_rau SET asiento_id = ? WHERE id = ?", (asiento.id, liq["id"]))
        auditoria.registrar(con, sesion, "liquidaciones_rau", "UPDATE", int(liq["id"]), antes={"asiento_id": None},
                            despues={"asiento": asiento.comprobante})
    return asiento.comprobante


def comunicar_liquidacion(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha: Any) -> dict[str, Any]:
    """Comunicación de la liquidación individual a los socios (20 días hábiles
    antes del vencimiento, POL-07 Art. 3)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    f = a_fecha(fecha)
    with transaccion(con):
        liq = con.execute("SELECT * FROM liquidaciones_rau WHERE gestion = ?", (gestion,)).fetchone()
        if liq is None:
            raise ReglaNegocioError("Registre primero la liquidación.", codigo="SIN_LIQUIDACION")
        if liq["comunicada_en"]:
            raise ReglaNegocioError("La liquidación ya fue comunicada.", codigo="YA_COMUNICADA")
        con.execute("UPDATE liquidaciones_rau SET comunicada_en = ? WHERE id = ?", (f.isoformat(), liq["id"]))
        _indexar(con, sesion, "B03", gestion, f, f"Liquidación RAU N.º {liq['id']} ({liq['socios']} socios)")
        auditoria.registrar(con, sesion, "liquidaciones_rau", "UPDATE", int(liq["id"]), antes={"comunicada_en": None},
                            despues={"comunicada_en": f})
    limite = fechas_calendario(con, gestion)["LIQUIDACION_SOCIOS"]
    return {"dentro_plazo": f <= limite, "limite": limite.isoformat()}


def liquidacion(con: sqlite3.Connection, sesion: Sesion, gestion: int) -> dict[str, Any] | None:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    liq = con.execute("SELECT * FROM liquidaciones_rau WHERE gestion = ?", (gestion,)).fetchone()
    if liq is None:
        return None
    socios = [{**dict(r), "monto": de_centavos(int(r["monto_cent"]))} for r in con.execute(
        """SELECT l.*, s.numero, s.nombre_completo AS socio, s.ci, s.ci_expedido FROM liquidaciones_rau_socios l
             JOIN socios s ON s.id = l.socio_id WHERE l.liquidacion_id = ? ORDER BY s.numero""", (liq["id"],))]
    asiento = con.execute("SELECT tipo, numero FROM asientos WHERE id = ?", (liq["asiento_id"],)).fetchone() \
        if liq["asiento_id"] else None
    return {**dict(liq), "total": de_centavos(int(liq["total_cent"])), "hectareas": _ha(liq["hectareas_mil"]),
            "advertencias": json.loads(liq["advertencias"]), "socios_detalle": socios,
            "asiento": f"{asiento['tipo']}-{int(asiento['numero']):05d}" if asiento else None,
            "limite_comunicacion": fechas_calendario(con, gestion)["LIQUIDACION_SOCIOS"].isoformat()}


def registrar_formulario_701(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha_presentacion: Any,
                             numero_orden: str, comprobante_ref: str, *, cuenta_banco: str = "1.1.01.03",
                             autorizacion_id: int | None = None) -> dict[str, Any]:
    """Presentación y pago del Formulario 701 V.3 (POL-07 Art. 2).

    El pago va contra 2.1.03.01 si la cuota está devengada; si no (gestión ya
    cerrada sin devengo), contra 7.1.05.02 del ejercicio del pago.
    Devuelve el semáforo de la anticipación (amarillo con menos de 15 días
    hábiles; rojo después del 31-oct)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    orden = _texto(numero_orden, "el número de orden del formulario", 60)
    comprobante = _texto(comprobante_ref, "el comprobante de pago", 120)
    f = a_fecha(fecha_presentacion)
    with transaccion(con):
        if con.execute("SELECT 1 FROM formularios_701 WHERE gestion = ?", (gestion,)).fetchone():
            raise ReglaNegocioError(f"El Formulario 701 de la gestión {gestion} ya está registrado.", codigo="YA_REGISTRADO")
        liq = con.execute("SELECT * FROM liquidaciones_rau WHERE gestion = ?", (gestion,)).fetchone()
        if liq is None:
            raise ReglaNegocioError("Registre primero la liquidación de la cuota (hectáreas × cuota por ha).",
                                    codigo="SIN_LIQUIDACION")
        evaluacion = evaluar_701(con, gestion, f)
        monto = de_centavos(int(liq["total_cent"]))
        # DISCREPANCIA: si la gestión se cerró sin devengar la cuota, el pago se
        # reconoce como gasto del ejercicio en que se paga (PC-01 prefiere el
        # devengo anual en 7.1.05.02 contra 2.1.03.01).
        contrapartida = CUENTA_RAU_POR_PAGAR if liq["asiento_id"] else CUENTA_GASTO_RAU
        mid = tesoreria.registrar_pago(
            con, sesion, f, monto, cuenta_banco, contrapartida, f"Formulario 701 V.3 RAU gestión {gestion} ({orden})",
            "Servicio de Impuestos Nacionales", autorizacion_id=autorizacion_id, documento_ref=comprobante,
            categoria_pago="OBLIGACIONES_RAU")
        fid = int(con.execute(
            """INSERT INTO formularios_701 (gestion, fecha_presentacion, vencimiento, limite_anticipacion,
                   dias_habiles_antes, semaforo, hectareas_mil, cuota_ha, monto_cent, numero_orden, comprobante_ref,
                   movimiento_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gestion, f.isoformat(), evaluacion["vencimiento"], evaluacion["limite"], evaluacion["dias_habiles_antes"],
             evaluacion["semaforo"], int(liq["hectareas_mil"]), liq["cuota_ha"], int(liq["total_cent"]), orden,
             comprobante, mid, sesion.nombre_usuario, _ahora_iso())).lastrowid)
        cert = certificado_vigente(con, f)
        if cert is not None:
            con.execute("UPDATE certificados_rau SET ultimo_pago_ref = ? WHERE id = ?",
                        (f"Form. 701 {orden} ({comprobante})", cert["id"]))
        _indexar(con, sesion, "B01", gestion, f, f"Form. 701 {orden} y comprobante {comprobante}")
        auditoria.registrar(con, sesion, "formularios_701", "INSERT", fid,
                            despues={**evaluacion, "monto": monto, "orden": orden, "contrapartida": contrapartida})
    return {"id": fid, **evaluacion, "monto": monto, "movimiento_id": mid}


def registrar_patente(con: sqlite3.Connection, sesion: Sesion, gestion: int, municipio: str, fecha_pago: Any,
                      monto: Any, comprobante_ref: str, *, cuenta_banco: str = "1.1.01.03",
                      autorizacion_id: int | None = None) -> dict[str, Any]:
    """Pago de patentes municipales en el mismo plazo del Formulario 701
    (POL-07; CI-FIN-08 Paso 4)."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    f = a_fecha(fecha_pago)
    lugar = _texto(municipio, "el municipio", 120)
    importe = redondear_bs(_decimal(monto, "monto"))
    if importe <= 0:
        raise DatosInvalidosError("El monto debe ser mayor a cero.")
    comprobante = _texto(comprobante_ref, "el comprobante", 120)
    with transaccion(con):
        _ejercicio(con, gestion)
        if con.execute("SELECT 1 FROM patentes_municipales WHERE gestion = ? AND municipio = ?",
                       (gestion, lugar)).fetchone():
            raise ReglaNegocioError("La patente de ese municipio y gestión ya está registrada.", codigo="YA_REGISTRADO")
        limite = fechas_calendario(con, gestion)["PATENTES"]
        por_pagar = -tesoreria.saldo_cuenta(con, CUENTA_PATENTES_POR_PAGAR, f)
        contrapartida = CUENTA_PATENTES_POR_PAGAR if por_pagar >= importe else CUENTA_GASTO_PATENTES
        mid = tesoreria.registrar_pago(
            con, sesion, f, importe, cuenta_banco, contrapartida, f"Patentes municipales {lugar} gestión {gestion}",
            f"Gobierno Autónomo Municipal de {lugar}", autorizacion_id=autorizacion_id, documento_ref=comprobante,
            categoria_pago="OBLIGACIONES_RAU")
        pid = int(con.execute(
            """INSERT INTO patentes_municipales (gestion, municipio, fecha_pago, limite, dentro_plazo, monto_cent,
                   comprobante_ref, movimiento_id, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (gestion, lugar, f.isoformat(), limite.isoformat(), int(f <= limite), a_centavos(importe), comprobante, mid,
             sesion.nombre_usuario, _ahora_iso())).lastrowid)
        _indexar(con, sesion, "B04", gestion, f, f"Patentes {lugar} ({comprobante})")
        auditoria.registrar(con, sesion, "patentes_municipales", "INSERT", pid,
                            despues={"gestion": gestion, "municipio": lugar, "monto": importe,
                                     "dentro_plazo": f <= limite})
    return {"id": pid, "dentro_plazo": f <= limite, "limite": limite.isoformat()}


# ==========================================================================
# 5. INDICADORES I-1 E I-2 (PET-RAU §1.3-1.4)
# ==========================================================================
def semaforo_i1(valor: Decimal | None, p: Mapping[str, Any]) -> str | None:
    """I-1: verde ≥ 65 %; amarillo 60 % a 65 %; rojo < 60 %."""
    if valor is None or "rau_i1_autoconsumo_verde_min" not in p:
        return None
    if valor >= Decimal(p["rau_i1_autoconsumo_verde_min"]):
        return VERDE
    return ROJO if valor < Decimal(_param(p, "rau_i1_autoconsumo_rojo_max")) else AMARILLO


def semaforo_i2(valor: Decimal | None, p: Mapping[str, Any]) -> str | None:
    """I-2: verde ≤ 15 % (meta); amarillo de 15 % a 20 %; rojo > 20 %.

    # DISCREPANCIA: la tabla 1.4 da «15 % a 20 %» como amarillo y la meta del
    # §5 es «≤ 15 %»; el valor exacto de 15 % se toma como verde (cumple la meta).
    """
    if valor is None or "rau_i2_ventas_alimento_amarillo" not in p:
        return None
    if valor > Decimal(_param(p, "rau_i2_ventas_alimento_rojo")):
        return ROJO
    return AMARILLO if valor > Decimal(p["rau_i2_ventas_alimento_amarillo"]) else VERDE


def evaluar_i1(con: sqlite3.Connection, producido_qq: Any, autoconsumo_qq: Any, ejercicio: int) -> dict[str, Any]:
    """I-1 = autoconsumo / producción anual de alimento (qq)."""
    prod, auto = a_mil(producido_qq, "qq producidos", permitir_cero=True), a_mil(autoconsumo_qq, "qq de autoconsumo", permitir_cero=True)
    if prod < 0 or auto < 0 or auto > prod:
        raise DatosInvalidosError("El autoconsumo debe estar entre cero y la producción.")
    valor = _fraccion(auto, prod)
    return {"indicador": "I-1", "producido": de_mil(prod), "autoconsumo": de_mil(auto), "valor": valor,
            "semaforo": semaforo_i1(valor, _params(con, ejercicio))}


def evaluar_i2(con: sqlite3.Connection, ventas_alimento: Any, ingresos_operativos: Any, ejercicio: int) -> dict[str, Any]:
    """I-2 = ventas de alimento / ingresos operativos."""
    ventas, ingresos = a_centavos(_decimal(ventas_alimento, "ventas")), a_centavos(_decimal(ingresos_operativos, "ingresos"))
    if ventas < 0 or ingresos < 0:
        raise DatosInvalidosError("Las cifras no pueden ser negativas.")
    valor = _fraccion(ventas, ingresos)
    return {"indicador": "I-2", "ventas_alimento": de_centavos(ventas), "ingresos_operativos": de_centavos(ingresos),
            "valor": valor, "semaforo": semaforo_i2(valor, _params(con, ejercicio))}


def _kardex_alimento(con: sqlite3.Connection, desde: date, hasta: date) -> tuple[int, int]:
    fila = con.execute(
        """SELECT COALESCE(SUM(CASE WHEN tipo = 'ENTRADA_PRODUCCION' THEN cantidad_mil END), 0),
                  COALESCE(SUM(CASE WHEN tipo = 'SALIDA_CONSUMO_PROPIO' THEN cantidad_mil END), 0)
             FROM kardex_terminados WHERE producto = 'ALIMENTO' AND fecha BETWEEN ? AND ?""",
        (desde.isoformat(), hasta.isoformat())).fetchone()
    return int(fila[0]), int(fila[1])


def indicadores_rau(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, hasta: Any = None) -> dict[str, Any]:
    """I-1 e I-2 del ejercicio con datos del kárdex y del mayor, y número de
    compradores no socios recurrentes."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return _indicadores(con, ejercicio, hasta)


def _indicadores(con: sqlite3.Connection, ejercicio: int, hasta: Any = None) -> dict[str, Any]:
    inicio, fin = _rango(con, ejercicio)
    corte = min(a_fecha(hasta), fin) if hasta is not None else fin
    prod, auto = _kardex_alimento(con, inicio, corte)
    i1 = evaluar_i1(con, de_mil(prod), de_mil(auto), ejercicio) if prod else \
        {"indicador": "I-1", "producido": Decimal(0), "autoconsumo": de_mil(auto), "valor": None, "semaforo": None}
    s = {f[0]: int(f[1]) for f in con.execute(
        """SELECT d.cuenta_codigo, COALESCE(SUM(d.haber_cent), 0) - COALESCE(SUM(d.debe_cent), 0)
             FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
            WHERE a.fecha BETWEEN ? AND ? AND a.tipo <> 'CIERRE' AND d.cuenta_codigo LIKE '5.1.%'
            GROUP BY d.cuenta_codigo""", (inicio.isoformat(), corte.isoformat()))}
    ingresos = sum(s.values())
    i2 = evaluar_i2(con, de_centavos(s.get("5.1.02.01", 0)), de_centavos(ingresos), ejercicio) if ingresos > 0 else \
        {"indicador": "I-2", "ventas_alimento": CERO, "ingresos_operativos": CERO, "valor": None, "semaforo": None}
    recurrentes = _no_socios_recurrentes(con, ejercicio, corte)
    return {"ejercicio": ejercicio, "hasta": corte.isoformat(), "I1": i1, "I2": i2, "no_socios": recurrentes}


def _no_socios_recurrentes(con: sqlite3.Connection, ejercicio: int, hasta: date) -> dict[str, Any]:
    inicio, _ = _rango(con, ejercicio)
    p = _params(con, ejercicio)
    if "rau_no_socio_compras_recurrente" not in p:
        return {"cantidad": 0, "semaforo": None, "minimo_compras": None}
    minimo = int(p["rau_no_socio_compras_recurrente"])
    cantidad = int(con.execute(
        """SELECT COUNT(*) FROM (SELECT n.cliente_id FROM notas_entrega n JOIN clientes c ON c.id = n.cliente_id
            WHERE n.producto = 'EXCEDENTE' AND c.es_socio = 0 AND n.fecha BETWEEN ? AND ?
            GROUP BY n.cliente_id HAVING COUNT(*) >= ?)""", (inicio.isoformat(), hasta.isoformat(), minimo)).fetchone()[0])
    if cantidad > int(_param(p, "rau_no_socios_rojo")):
        semaforo = ROJO
    elif cantidad > int(_param(p, "rau_no_socios_amarillo")):
        semaforo = AMARILLO
    else:
        semaforo = VERDE
    return {"cantidad": cantidad, "semaforo": semaforo, "minimo_compras": minimo}


# ==========================================================================
# 6. PROGRAMA DE EXCEDENTE, BALANCE DE MASAS Y COMPRADORES (PET-RAU §1.3, §2.1)
# ==========================================================================
def aprobar_programa_excedente(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, tope_qq: Any, *,
                               canal_socios: bool, canal_no_socios: bool, acta_ca_ref: str, fecha: Any) -> int:
    """Programa anual de excedente con tope en qq y canales (R6), aprobado por
    el Consejo de Administración."""
    seguridad.exigir_permiso(con, sesion, "rau", "APROBAR")
    tope = a_mil(tope_qq, "tope en qq", permitir_cero=True)
    if tope < 0:
        raise DatosInvalidosError("El tope no puede ser negativo.")
    if not (canal_socios or canal_no_socios):
        raise DatosInvalidosError("Indique al menos un canal de venta del excedente.")
    acta = _texto(acta_ca_ref, "el acta del Consejo de Administración", 120)
    f = a_fecha(fecha)
    with transaccion(con):
        _ejercicio(con, ejercicio)
        if con.execute("SELECT 1 FROM programas_excedente WHERE ejercicio = ?", (ejercicio,)).fetchone():
            raise ReglaNegocioError("El programa de excedente de ese ejercicio ya está aprobado.", codigo="YA_REGISTRADO")
        pid = int(con.execute(
            """INSERT INTO programas_excedente (ejercicio, tope_qq_mil, canal_socios, canal_no_socios, acta_ca_ref, fecha,
                   aprobado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (ejercicio, tope, int(canal_socios), int(canal_no_socios), acta, f.isoformat(), sesion.nombre_completo,
             _ahora_iso())).lastrowid)
        _indexar(con, sesion, "D03", ejercicio, f, f"Programa de excedente {ejercicio}: {_ha_txt(tope)} qq ({acta})")
        auditoria.registrar(con, sesion, "programas_excedente", "INSERT", pid,
                            despues={"ejercicio": ejercicio, "tope_qq": de_mil(tope), "socios": canal_socios,
                                     "no_socios": canal_no_socios, "acta": acta})
    return pid


def trimestre_ejercicio(con: sqlite3.Connection, ejercicio: int, trimestre: int) -> tuple[date, date]:
    """T1 jul-sep, T2 oct-dic, T3 ene-mar, T4 abr-jun del ejercicio."""
    _entero(trimestre, "trimestre", 1, 4)
    inicio, _ = _rango(con, ejercicio)
    mes = inicio.month + 3 * (trimestre - 1)
    anio = inicio.year + (mes - 1) // 12
    mes = (mes - 1) % 12 + 1
    desde = date(anio, mes, 1)
    sig_mes, sig_anio = (mes + 3 - 1) % 12 + 1, anio + (mes + 3 - 1) // 12
    return desde, date(sig_anio, sig_mes, 1) - timedelta(days=1)


def calcular_balance_masas(con: sqlite3.Connection, ejercicio: int, trimestre: int, merma_qq: Any = 0) -> dict[str, Any]:
    """Producción − consumo propio − merma = excedente disponible (kárdex)."""
    desde, hasta = trimestre_ejercicio(con, ejercicio, trimestre)
    prod, cons = _kardex_alimento(con, desde, hasta)
    merma = a_mil(merma_qq, "merma (qq)", permitir_cero=True)
    if merma < 0:
        raise DatosInvalidosError("La merma no puede ser negativa.")
    excedente = prod - cons - merma
    p = _params(con, ejercicio)
    pct = _fraccion(merma, prod)
    semaforo = None
    if pct is not None and "rau_merma_amarillo" in p:
        if pct > Decimal(_param(p, "merma_alimento_anormal")):
            semaforo = ROJO
        elif pct >= Decimal(p["rau_merma_amarillo"]):
            semaforo = AMARILLO
        else:
            semaforo = VERDE
    return {"ejercicio": ejercicio, "trimestre": trimestre, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "producido": de_mil(prod), "consumo": de_mil(cons), "merma": de_mil(merma), "excedente": de_mil(excedente),
            "merma_pct": pct, "merma_semaforo": semaforo, "producido_mil": prod, "consumo_mil": cons,
            "merma_mil": merma, "excedente_mil": excedente}


def preparar_balance_masas(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, trimestre: int, *, merma_qq: Any,
                           consumo_proyectado_qq: Any) -> dict[str, Any]:
    """Balance de masas trimestral (FIN-TRA-13 propuesto) preparado por Finanzas."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    proyectado = a_mil(consumo_proyectado_qq, "consumo proyectado (qq)", permitir_cero=True)
    if proyectado < 0:
        raise DatosInvalidosError("El consumo proyectado no puede ser negativo.")
    with transaccion(con):
        if con.execute("SELECT 1 FROM balances_masas WHERE ejercicio = ? AND trimestre = ?",
                       (ejercicio, trimestre)).fetchone():
            raise ReglaNegocioError("El balance de masas de ese trimestre ya existe.", codigo="YA_REGISTRADO")
        b = calcular_balance_masas(con, ejercicio, trimestre, merma_qq)
        bid = int(con.execute(
            """INSERT INTO balances_masas (ejercicio, trimestre, desde, hasta, producido_mil, consumo_mil, merma_mil,
                   excedente_mil, consumo_proyectado_mil, merma_pct, merma_semaforo, preparado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, trimestre, b["desde"], b["hasta"], b["producido_mil"], b["consumo_mil"], b["merma_mil"],
             b["excedente_mil"], proyectado, str(b["merma_pct"]) if b["merma_pct"] is not None else None,
             b["merma_semaforo"], sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "balances_masas", "INSERT", bid,
                            despues={k: v for k, v in b.items() if not k.endswith("_mil")})
    return {"id": bid, **b, "consumo_proyectado": de_mil(proyectado),
            "autorizable": de_mil(max(b["excedente_mil"] - proyectado, 0))}


def _autorizado_acumulado(con: sqlite3.Connection, ejercicio: int, excluir_id: int | None = None) -> int:
    return int(con.execute("SELECT COALESCE(SUM(autorizado_mil), 0) FROM balances_masas WHERE ejercicio = ? AND "
                           "firmado_por IS NOT NULL AND id <> ?", (ejercicio, excluir_id or 0)).fetchone()[0])


def firmar_balance_masas(con: sqlite3.Connection, sesion: Sesion, balance_id: int, autorizado_qq: Any) -> dict[str, Any]:
    """La Gerencia General firma el balance y autoriza el excedente a vender,
    sin superar lo que excede el consumo proyectado (R5) ni el tope del
    programa aprobado (R6)."""
    seguridad.exigir_permiso(con, sesion, "rau", "AUTORIZAR")
    autorizado = a_mil(autorizado_qq, "excedente autorizado (qq)", permitir_cero=True)
    if autorizado < 0:
        raise DatosInvalidosError("El excedente autorizado no puede ser negativo.")
    with transaccion(con):
        b = con.execute("SELECT * FROM balances_masas WHERE id = ?", (balance_id,)).fetchone()
        if b is None:
            raise DatosInvalidosError(f"No existe el balance de masas {balance_id}.")
        if b["firmado_por"]:
            raise ReglaNegocioError("El balance ya está firmado.", codigo="YA_FIRMADO")
        disponible = max(int(b["excedente_mil"]) - int(b["consumo_proyectado_mil"]), 0)
        if autorizado > disponible:
            raise ReglaNegocioError(f"R5: solo puede venderse lo que supera el consumo proyectado: máximo "
                                    f"{_ha_txt(disponible)} qq.", codigo="EXCEDENTE_NO_REAL")
        if autorizado > 0:
            programa = con.execute("SELECT * FROM programas_excedente WHERE ejercicio = ?", (b["ejercicio"],)).fetchone()
            if programa is None:
                raise ReglaNegocioError("R6: no hay programa de excedente aprobado por el Consejo de Administración.",
                                        codigo="SIN_PROGRAMA_EXCEDENTE")
            acumulado = _autorizado_acumulado(con, int(b["ejercicio"]), balance_id)
            if acumulado + autorizado > int(programa["tope_qq_mil"]):
                raise ReglaNegocioError(f"R6: el excedente autorizado acumulado ({_ha_txt(acumulado + autorizado)} qq) "
                                        f"supera el tope del programa ({_ha_txt(int(programa['tope_qq_mil']))} qq).",
                                        codigo="TOPE_EXCEDENTE")
        momento = _ahora_iso()
        con.execute("UPDATE balances_masas SET autorizado_mil = ?, firmado_por = ?, firmado_en = ? WHERE id = ?",
                    (autorizado, sesion.nombre_completo, momento, balance_id))
        _indexar(con, sesion, "D02", int(b["ejercicio"]), date.fromisoformat(b["hasta"]),
                 f"Balance de masas T{b['trimestre']} {b['ejercicio']}")
        auditoria.registrar(con, sesion, "balances_masas", "AUTORIZACION", balance_id, antes={"firmado_por": None},
                            despues={"autorizado_qq": de_mil(autorizado), "firmado_por": sesion.nombre_completo})
    return {"autorizado": de_mil(autorizado)}


def listar_balances(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [{**dict(b), **{k: de_mil(int(b[f"{k}_mil"])) for k in ("producido", "consumo", "merma", "excedente",
                                                                 "consumo_proyectado")},
             "autorizado": de_mil(int(b["autorizado_mil"])) if b["autorizado_mil"] is not None else None}
            for b in con.execute("SELECT * FROM balances_masas WHERE ejercicio = ? ORDER BY trimestre", (ejercicio,))]


def programa_excedente(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any] | None:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    p = con.execute("SELECT * FROM programas_excedente WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if p is None:
        return None
    inicio, fin = _rango(con, ejercicio)
    vendido = _vendido(con, inicio, fin)
    return {**dict(p), "tope": de_mil(int(p["tope_qq_mil"])), "vendido": de_mil(vendido),
            "autorizado": de_mil(_autorizado_acumulado(con, ejercicio)),
            "saldo_tope": de_mil(int(p["tope_qq_mil"]) - vendido)}


def _vendido(con: sqlite3.Connection, desde: date, hasta: date) -> int:
    return int(con.execute("SELECT COALESCE(SUM(cantidad_mil), 0) FROM notas_entrega WHERE producto = 'EXCEDENTE' "
                           "AND fecha BETWEEN ? AND ?", (desde.isoformat(), hasta.isoformat())).fetchone()[0])


def autorizar_comprador(con: sqlite3.Connection, sesion: Sesion, cliente_id: int, ejercicio: int, *, comunidad: str,
                        actividad: str, fecha: Any) -> int:
    """Autorización de la GG a un productor agropecuario local no socio (R3)."""
    seguridad.exigir_permiso(con, sesion, "rau", "AUTORIZAR")
    f = a_fecha(fecha)
    with transaccion(con):
        _ejercicio(con, ejercicio)
        c = con.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
        if c is None:
            raise DatosInvalidosError(f"No existe el cliente {cliente_id}.")
        if c["es_socio"]:
            raise ReglaNegocioError("Los socios compran como acto cooperativo: no requieren autorización.",
                                    codigo="ES_SOCIO")
        if c["tipo"] != "AGRICULTOR":
            raise ReglaNegocioError("R3: los no socios solo pueden ser productores agropecuarios locales (cliente de "
                                    "tipo agricultor).", codigo="NO_ES_PRODUCTOR")
        if con.execute("SELECT 1 FROM compradores_autorizados WHERE cliente_id = ? AND ejercicio = ? AND "
                       "estado = 'VIGENTE'", (cliente_id, ejercicio)).fetchone():
            raise ReglaNegocioError("El comprador ya está autorizado en ese ejercicio.", codigo="YA_REGISTRADO")
        aid = int(con.execute(
            """INSERT INTO compradores_autorizados (cliente_id, ejercicio, comunidad, actividad, fecha, autorizado_por,
                   registrado_en) VALUES (?,?,?,?,?,?,?)""",
            (cliente_id, ejercicio, _texto(comunidad, "la comunidad", 120), _texto(actividad, "la actividad", 200),
             f.isoformat(), sesion.nombre_completo, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "compradores_autorizados", "INSERT", aid,
                            despues={"cliente": c["nombre"], "ejercicio": ejercicio})
    return aid


def revocar_comprador(con: sqlite3.Connection, sesion: Sesion, autorizacion_id: int, motivo: str) -> None:
    seguridad.exigir_permiso(con, sesion, "rau", "AUTORIZAR")
    causa = _texto(motivo, "el motivo", 300)
    with transaccion(con):
        fila = con.execute("SELECT * FROM compradores_autorizados WHERE id = ?", (autorizacion_id,)).fetchone()
        if fila is None or fila["estado"] != "VIGENTE":
            raise ReglaNegocioError("La autorización no está vigente.", codigo="NO_VIGENTE")
        con.execute("UPDATE compradores_autorizados SET estado = 'REVOCADA', motivo_revocacion = ? WHERE id = ?",
                    (causa, autorizacion_id))
        auditoria.registrar(con, sesion, "compradores_autorizados", "UPDATE", autorizacion_id,
                            antes={"estado": "VIGENTE"}, despues={"estado": "REVOCADA", "motivo": causa})


def verificar_venta_excedente(con: sqlite3.Connection, cliente_id: int, fecha: Any, cantidad_qq: Any,
                              dias_credito: int = 0) -> dict[str, Any]:
    """Controles RAU de cada venta de alimento excedente (R3 a R6):
    programa aprobado, canal, comprador no socio autorizado, sin crédito,
    dentro del excedente autorizado en los balances de masas y del tope."""
    f = a_fecha(fecha)
    ejercicio = ejercicio_de(con, f)
    cantidad = a_mil(cantidad_qq, "cantidad (qq)")
    programa = con.execute("SELECT * FROM programas_excedente WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if programa is None:
        raise ReglaNegocioError("R6: no hay programa de excedente aprobado por el Consejo de Administración para el "
                                f"ejercicio {ejercicio}.", codigo="SIN_PROGRAMA_EXCEDENTE")
    if dias_credito:
        raise ReglaNegocioError("R4: el alimento se vende al contado, sin cartera de crédito propia.",
                                codigo="ALIMENTO_SIN_CREDITO")
    cliente = con.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
    if cliente is None:
        raise DatosInvalidosError(f"No existe el cliente {cliente_id}.")
    if cliente["es_socio"]:
        if not programa["canal_socios"]:
            raise ReglaNegocioError("El programa de excedente no habilita el canal de socios.", codigo="CANAL_NO_HABILITADO")
    else:
        if not programa["canal_no_socios"]:
            raise ReglaNegocioError("El programa de excedente solo habilita la venta a socios.",
                                    codigo="CANAL_NO_HABILITADO")
        if con.execute("SELECT 1 FROM compradores_autorizados WHERE cliente_id = ? AND ejercicio = ? AND "
                       "estado = 'VIGENTE'", (cliente_id, ejercicio)).fetchone() is None:
            raise ReglaNegocioError("R3: el comprador no socio no está autorizado por la Gerencia General.",
                                    codigo="COMPRADOR_NO_AUTORIZADO")
    inicio, fin = _rango(con, ejercicio)
    vendido = _vendido(con, inicio, fin)
    if vendido + cantidad > int(programa["tope_qq_mil"]):
        raise ReglaNegocioError(f"R6: la venta lleva el excedente vendido a {_ha_txt(vendido + cantidad)} qq, sobre el "
                                f"tope aprobado de {_ha_txt(int(programa['tope_qq_mil']))} qq.", codigo="TOPE_EXCEDENTE")
    autorizado = _autorizado_acumulado(con, ejercicio)
    if vendido + cantidad > autorizado:
        raise ReglaNegocioError(f"R5: el excedente autorizado en los balances de masas firmados es "
                                f"{_ha_txt(autorizado)} qq y ya se vendieron {_ha_txt(vendido)} qq.",
                                codigo="EXCEDENTE_NO_AUTORIZADO")
    return {"ejercicio": ejercicio, "vendido": de_mil(vendido), "tope": de_mil(int(programa["tope_qq_mil"])),
            "autorizado": de_mil(autorizado)}


def padron_compradores(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> dict[str, Any]:
    """Padrón de compradores de alimento (socios y no socios autorizados) con
    compras del ejercicio e indicador de no socios recurrentes."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    inicio, fin = _rango(con, ejercicio)
    filas = []
    for c in con.execute(
            """SELECT c.id, c.codigo, c.nombre, c.documento, c.es_socio, c.tipo,
                      (SELECT COUNT(*) FROM notas_entrega n WHERE n.cliente_id = c.id AND n.producto = 'EXCEDENTE'
                          AND n.fecha BETWEEN ? AND ?) AS compras,
                      (SELECT COALESCE(SUM(n.cantidad_mil), 0) FROM notas_entrega n WHERE n.cliente_id = c.id
                          AND n.producto = 'EXCEDENTE' AND n.fecha BETWEEN ? AND ?) AS qq_mil,
                      (SELECT COALESCE(SUM(n.total_cent), 0) FROM notas_entrega n WHERE n.cliente_id = c.id
                          AND n.producto = 'EXCEDENTE' AND n.fecha BETWEEN ? AND ?) AS total_cent,
                      (SELECT a.id FROM compradores_autorizados a WHERE a.cliente_id = c.id AND a.ejercicio = ?
                          AND a.estado = 'VIGENTE') AS autorizacion_id,
                      (SELECT a.comunidad FROM compradores_autorizados a WHERE a.cliente_id = c.id AND a.ejercicio = ?
                          AND a.estado = 'VIGENTE') AS comunidad
                 FROM clientes c ORDER BY c.es_socio DESC, c.nombre""",
            (inicio.isoformat(), fin.isoformat()) * 3 + (ejercicio, ejercicio)):
        if not (c["compras"] or c["autorizacion_id"] or (c["es_socio"] and c["tipo"] == "AGRICULTOR")):
            continue
        filas.append({**dict(c), "qq": de_mil(int(c["qq_mil"])), "total": de_centavos(int(c["total_cent"])),
                      "condicion": "Socio" if c["es_socio"] else
                      ("No socio autorizado" if c["autorizacion_id"] else "No socio SIN autorización")})
    return {"ejercicio": ejercicio, "compradores": filas, "no_socios": _no_socios_recurrentes(con, ejercicio, fin)}


# ==========================================================================
# 7. SERVICIOS A TERCEROS (DS 24463 Art. 7; PET-RAU §1.4: rojo con cualquier caso)
# ==========================================================================
def registrar_servicio_tercero(con: sqlite3.Connection, sesion: Sesion, fecha: Any, tipo: str, tercero: str,
                               descripcion: str) -> dict[str, Any]:
    """Registra un servicio a terceros detectado. Dispara alerta ROJA: se
    reporta a la GG en 24 horas y al CA en 5 días hábiles."""
    _exigir_alguno(con, sesion, "rau", ("CREAR", "VERIFICAR", "AUTORIZAR"))
    if tipo not in TIPOS_SERVICIO:
        raise DatosInvalidosError(f"Tipo de servicio no válido: {tipo!r}.")
    f = a_fecha(fecha)
    p = _params(con, ejercicio_de(con, f))
    limite_gg = f + timedelta(hours=int(_param(p, "rau_alerta_roja_gg_horas")))
    limite_ca = sumar_dias_habiles(f, int(_param(p, "rau_alerta_roja_ca_dias_habiles")))
    with transaccion(con):
        sid = int(con.execute(
            """INSERT INTO servicios_terceros (fecha, tipo, tercero, descripcion, detectado_por, limite_gg, limite_ca,
                   registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (f.isoformat(), tipo, _texto(tercero, "el tercero", 120), _texto(descripcion, "la descripción", 1000),
             sesion.nombre_completo, limite_gg.isoformat(), limite_ca.isoformat(), _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "servicios_terceros", "INSERT", sid,
                            despues={"fecha": f, "tipo": tipo, "tercero": tercero, "semaforo": ROJO})
    return {"id": sid, "semaforo": ROJO, "limite_gg": limite_gg.isoformat(), "limite_ca": limite_ca.isoformat(),
            "mensaje": ("ALERTA ROJA: servicio a terceros (DS 24463 Art. 7, causal de exclusión del RAU). Informar a la "
                        f"Gerencia General hasta el {fecha_pantalla(limite_gg)} y resolver en el Consejo de "
                        f"Administración hasta el {fecha_pantalla(limite_ca)}.")}


def informar_servicio_gg(con: sqlite3.Connection, sesion: Sesion, servicio_id: int, fecha: Any) -> None:
    """La Gerencia General toma conocimiento de la alerta roja."""
    seguridad.exigir_permiso(con, sesion, "rau", "AUTORIZAR")
    with transaccion(con):
        s = con.execute("SELECT * FROM servicios_terceros WHERE id = ?", (servicio_id,)).fetchone()
        if s is None or s["estado"] != "ABIERTO":
            raise ReglaNegocioError("El caso no está abierto.", codigo="NO_ABIERTO")
        f = a_fecha(fecha)
        con.execute("UPDATE servicios_terceros SET informado_gg_en = ?, estado = 'INFORMADO' WHERE id = ?",
                    (f.isoformat(), servicio_id))
        auditoria.registrar(con, sesion, "servicios_terceros", "UPDATE", servicio_id, antes={"estado": "ABIERTO"},
                            despues={"estado": "INFORMADO", "informado_gg_en": f, "en_plazo": f <= a_fecha(s["limite_gg"])})


def resolver_servicio_ca(con: sqlite3.Connection, sesion: Sesion, servicio_id: int, fecha: Any, acta_ref: str,
                         medida: str) -> None:
    """El Consejo de Administración resuelve la medida de contención (§4.3)."""
    seguridad.exigir_permiso(con, sesion, "rau", "APROBAR")
    with transaccion(con):
        s = con.execute("SELECT * FROM servicios_terceros WHERE id = ?", (servicio_id,)).fetchone()
        if s is None or s["estado"] != "INFORMADO":
            raise ReglaNegocioError("El caso debe estar informado a la Gerencia General.", codigo="NO_INFORMADO")
        f = a_fecha(fecha)
        con.execute("UPDATE servicios_terceros SET resuelto_ca_en = ?, acta_ca_ref = ?, medida = ?, estado = 'CERRADO' "
                    "WHERE id = ?", (f.isoformat(), _texto(acta_ref, "el acta", 120), _texto(medida, "la medida", 1000),
                                     servicio_id))
        auditoria.registrar(con, sesion, "servicios_terceros", "UPDATE", servicio_id, antes={"estado": "INFORMADO"},
                            despues={"estado": "CERRADO", "acta": acta_ref, "medida": medida,
                                     "en_plazo": f <= a_fecha(s["limite_ca"])})


def listar_servicios(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [{**dict(s), "tipo_txt": TIPOS_SERVICIO[s["tipo"]], "semaforo": ROJO}
            for s in con.execute("SELECT * FROM servicios_terceros ORDER BY fecha DESC, id DESC")]


# ==========================================================================
# 8. NOTIFICACIONES Y PLAN DE RESPUESTA ANTE FISCALIZACIÓN (PET-RAU §4.2-4.3)
# ==========================================================================
def registrar_notificacion(con: sqlite3.Connection, sesion: Sesion, *, fecha_recepcion: Any, entidad: str, tipo: str,
                           numero: str, funcionario: str, gestiones: Sequence[int], vencimiento: Any,
                           escenario: str | None = None) -> dict[str, Any]:
    """Registro de notificaciones (recepción con sello y fecha) y apertura de la
    carpeta del caso (G04). Devuelve el plan de respuesta con sus plazos."""
    _exigir_alguno(con, sesion, "rau", ("CREAR", "AUTORIZAR"))
    if entidad not in ENTIDADES:
        raise DatosInvalidosError(f"Entidad no válida: {entidad!r}.")
    if tipo not in TIPOS_NOTIFICACION:
        raise DatosInvalidosError(f"Tipo de notificación no válido: {tipo!r}.")
    if escenario is not None and escenario not in ESCENARIOS:
        raise DatosInvalidosError("El escenario debe ser A, B, C, D o E.")
    if not gestiones:
        raise DatosInvalidosError("Indique las gestiones alcanzadas.")
    lista = sorted({_entero(g, "gestión") for g in gestiones})
    f = a_fecha(fecha_recepcion)
    venc = a_fecha(vencimiento)
    if venc < f:
        raise DatosInvalidosError("El vencimiento no puede ser anterior a la recepción.")
    with transaccion(con):
        nid = int(con.execute(
            """INSERT INTO notificaciones_fiscales (fecha_recepcion, entidad, tipo, numero, funcionario, gestiones,
                   plazo_dias, vencimiento, escenario, recibido_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (f.isoformat(), entidad, tipo, _texto(numero, "el número de la orden o requerimiento", 80),
             _texto(funcionario, "el funcionario actuante", 120), ",".join(str(g) for g in lista), (venc - f).days,
             venc.isoformat(), escenario, sesion.nombre_completo, _ahora_iso())).lastrowid)
        _indexar(con, sesion, "G04", ejercicio_de(con, f), f, f"Caso de fiscalización N.º {nid} ({numero})")
        auditoria.registrar(con, sesion, "notificaciones_fiscales", "INSERT", nid,
                            despues={"entidad": entidad, "tipo": tipo, "numero": numero, "gestiones": lista,
                                     "vencimiento": venc})
    plan = plan_respuesta(con, sesion, nid, f)
    if entidad == "GRACO":
        plan["advertencias"].append("La GRACO atiende a Grandes Contribuyentes, categoría que no corresponde a la "
                                    "cooperativa: verifique primero la competencia (PET-RAU §4.2).")
    return plan


def registrar_accion_fiscalizacion(con: sqlite3.Connection, sesion: Sesion, notificacion_id: int, paso: str,
                                   fecha: Any, detalle: str, documento_ref: str | None = None,
                                   nuevo_vencimiento: Any = None) -> int:
    """Registra un paso del plan de respuesta (informe 24 h, análisis, prórroga,
    respuesta, descargos o recurso)."""
    _exigir_alguno(con, sesion, "rau", ("CREAR", "AUTORIZAR", "APROBAR"))
    if paso not in PASOS or paso == "CIERRE":
        raise DatosInvalidosError(f"Paso no válido: {paso!r} (el cierre usa «cerrar_notificacion»).")
    f = a_fecha(fecha)
    with transaccion(con):
        n = con.execute("SELECT * FROM notificaciones_fiscales WHERE id = ?", (notificacion_id,)).fetchone()
        if n is None:
            raise DatosInvalidosError(f"No existe la notificación {notificacion_id}.")
        if n["estado"] == "CERRADA":
            raise ReglaNegocioError("El caso está cerrado.", codigo="CASO_CERRADO")
        if f < date.fromisoformat(n["fecha_recepcion"]):
            raise DatosInvalidosError("La acción no puede ser anterior a la recepción.")
        if paso in ("DESCARGOS", "RECURSO") and not _opcional(documento_ref, "documento"):
            raise DatosInvalidosError("Los descargos y recursos los decide el Consejo de Administración: indique el acta.")
        nuevo = a_fecha(nuevo_vencimiento) if nuevo_vencimiento is not None else None
        if paso == "PRORROGA":
            if nuevo is None or nuevo <= date.fromisoformat(n["vencimiento"]):
                raise DatosInvalidosError("La prórroga requiere un nuevo vencimiento posterior al actual.")
        aid = int(con.execute(
            """INSERT INTO acciones_fiscalizacion (notificacion_id, paso, fecha, detalle, documento_ref, nuevo_vencimiento,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (notificacion_id, paso, f.isoformat(), _texto(detalle, "el detalle", 1000),
             _opcional(documento_ref, "documento", 120), nuevo.isoformat() if nuevo else None, sesion.nombre_usuario,
             _ahora_iso())).lastrowid)
        cambios: dict[str, Any] = {}
        if paso == "PRORROGA":
            cambios["vencimiento"] = nuevo.isoformat()
        if paso in ("RESPUESTA", "DESCARGOS", "RECURSO"):
            cambios["estado"] = "RESPONDIDA"
        if cambios:
            con.execute(f"UPDATE notificaciones_fiscales SET {', '.join(f'{k} = ?' for k in cambios)} WHERE id = ?",
                        (*cambios.values(), notificacion_id))
        auditoria.registrar(con, sesion, "acciones_fiscalizacion", "INSERT", aid,
                            antes={k: n[k] for k in cambios}, despues={"paso": paso, "fecha": f, **cambios})
    return aid


def cerrar_notificacion(con: sqlite3.Connection, sesion: Sesion, notificacion_id: int, fecha: Any,
                        lecciones: str) -> None:
    """Cierre con informe de lecciones aprendidas del Consejo de Vigilancia."""
    _exigir_alguno(con, sesion, "rau", ("VERIFICAR", "AUTORIZAR"))
    texto = _texto(lecciones, "las lecciones aprendidas", 2000)
    f = a_fecha(fecha)
    with transaccion(con):
        n = con.execute("SELECT * FROM notificaciones_fiscales WHERE id = ?", (notificacion_id,)).fetchone()
        if n is None:
            raise DatosInvalidosError(f"No existe la notificación {notificacion_id}.")
        if n["estado"] != "RESPONDIDA":
            raise ReglaNegocioError("El caso se cierra después de la respuesta, descargos o recurso.",
                                    codigo="SIN_RESPUESTA")
        con.execute("INSERT INTO acciones_fiscalizacion (notificacion_id, paso, fecha, detalle, registrado_por, "
                    "registrado_en) VALUES (?, 'CIERRE', ?, ?, ?, ?)",
                    (notificacion_id, f.isoformat(), texto, sesion.nombre_usuario, _ahora_iso()))
        con.execute("UPDATE notificaciones_fiscales SET estado = 'CERRADA', lecciones = ?, cerrado_por = ?, "
                    "cerrado_en = ? WHERE id = ?", (texto, sesion.nombre_completo, f.isoformat(), notificacion_id))
        auditoria.registrar(con, sesion, "notificaciones_fiscales", "UPDATE", notificacion_id,
                            antes={"estado": "RESPONDIDA"}, despues={"estado": "CERRADA", "lecciones": texto})


def plan_respuesta(con: sqlite3.Connection, sesion: Sesion, notificacion_id: int, fecha: Any = None) -> dict[str, Any]:
    """Plan de respuesta del caso: pasos con plazo y estado, escenario y
    disponibilidad de la Carpeta de Respuesta Rápida."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    n = con.execute("SELECT * FROM notificaciones_fiscales WHERE id = ?", (notificacion_id,)).fetchone()
    if n is None:
        raise DatosInvalidosError(f"No existe la notificación {notificacion_id}.")
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    recepcion = date.fromisoformat(n["fecha_recepcion"])
    p = _params(con, ejercicio_de(con, recepcion))
    acciones = [dict(a) for a in con.execute("SELECT * FROM acciones_fiscalizacion WHERE notificacion_id = ? "
                                             "ORDER BY fecha, id", (notificacion_id,))]
    hechos = {}
    for a in acciones:
        hechos.setdefault(a["paso"], a["fecha"])
    limites = {
        "INFORME_24H": recepcion + timedelta(hours=int(_param(p, "rau_fiscalizacion_informe_horas"))),
        "ANALISIS": recepcion + timedelta(days=int(_param(p, "rau_fiscalizacion_analisis_dias"))),
        "RESPUESTA": date.fromisoformat(n["vencimiento"]),
    }
    pasos = []
    for paso in ("INFORME_24H", "ANALISIS", "RESPUESTA"):
        limite = limites[paso]
        hecho = hechos.get(paso) or (hechos.get("DESCARGOS") or hechos.get("RECURSO") if paso == "RESPUESTA" else None)
        if hecho:
            estado = "CUMPLIDO" if a_fecha(hecho) <= limite else "CUMPLIDO_FUERA_DE_PLAZO"
        else:
            estado = "VENCIDO" if referencia > limite else "PENDIENTE"
        pasos.append({"paso": paso, "descripcion": PASOS[paso], "limite": limite.isoformat(), "cumplido_en": hecho,
                      "estado": estado})
    gestiones = [int(g) for g in n["gestiones"].split(",")]
    cubiertos: set[str] = set()
    for g in gestiones:
        cubiertos |= _cubiertos(con, g)
    carpeta = [{"documento": nombre, "disponible": all(c in cubiertos for c in claves), "claves": list(claves)}
               for nombre, claves in CARPETA_RESPUESTA_RAPIDA]
    escenario = ESCENARIOS.get(n["escenario"]) if n["escenario"] else None
    return {"notificacion": dict(n), "gestiones": gestiones, "pasos": pasos, "acciones": acciones,
            "escenario": None if escenario is None else {"codigo": n["escenario"], "senal": escenario[0],
                                                         "respuesta_72h": escenario[1], "argumento": escenario[2]},
            "carpeta_respuesta_rapida": carpeta, "advertencias": []}


def listar_notificaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [dict(n) for n in con.execute("SELECT * FROM notificaciones_fiscales ORDER BY fecha_recepcion DESC, id DESC")]


def registrar_revision_buzon(con: sqlite3.Connection, sesion: Sesion, fecha: Any, novedades: bool,
                             detalle: str | None = None) -> int:
    """Registro semanal de la revisión del buzón y notificaciones del SIN."""
    seguridad.exigir_permiso(con, sesion, "rau", "CREAR")
    f = a_fecha(fecha)
    texto = _opcional(detalle, "detalle", 500)
    if novedades and not texto:
        raise DatosInvalidosError("Describa las novedades encontradas.")
    with transaccion(con):
        rid = int(con.execute("INSERT INTO revisiones_buzon (fecha, novedades, detalle, revisado_por, registrado_en) "
                              "VALUES (?,?,?,?,?)", (f.isoformat(), int(bool(novedades)), texto, sesion.nombre_completo,
                                                     _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "revisiones_buzon", "INSERT", rid, despues={"fecha": f, "novedades": novedades})
    return rid


# ==========================================================================
# 9. REVISIÓN TRIMESTRAL DEL CV (INF-CV-09, PET-RAU §4.1) E INFORME ANUAL
# ==========================================================================
def trimestre_calendario(anio: int, trimestre: int) -> tuple[date, date]:
    """Trimestre calendario: cierre de marzo, junio, septiembre y diciembre."""
    _entero(trimestre, "trimestre", 1, 4)
    desde = date(anio, 3 * (trimestre - 1) + 1, 1)
    hasta = date(anio + (1 if trimestre == 4 else 0), 1 if trimestre == 4 else 3 * trimestre + 1, 1) - timedelta(days=1)
    return desde, hasta


def verificaciones_trimestrales(con: sqlite3.Connection, anio: int, trimestre: int, *,
                                compras_sin_respaldo_pct: Any = 0, padron_atraso_trimestres: int = 0) -> list[dict[str, Any]]:
    """Las 10 verificaciones del anexo trimestral al INF-CV-09 con semáforo.

    Las verificaciones 6 (muestra de compras) y 7 (atraso del padrón y del
    libro de actas) incorporan el resultado de la revisión del CV."""
    desde, hasta = trimestre_calendario(anio, trimestre)
    ejercicio = ejercicio_de(con, hasta)
    p = _params(con, ejercicio)
    gestion_declarada = anio - 1
    v: list[dict[str, Any]] = []

    sup = estado_superficie(con, hasta)
    v.append({"numero": 1, "verificacion": "Superficie usada ≤ superficie declarada en el SIN.",
              "fuente": "Plano, título, constancia SIN", "meta": "100%",
              "resultado": f"Usada {_ha_txt(sup['usada_mil'])} ha; declarada {_ha_txt(sup['declarada_mil'])} ha ({sup['fuente_declarada']})",
              "semaforo": sup["semaforo"] if sup["constancia"] or sup["semaforo"] == ROJO else AMARILLO})

    consulta = con.execute("SELECT * FROM cumplimientos_fiscales WHERE gestion = ? AND evento = 'CONSULTA_RND'",
                           (gestion_declarada,)).fetchone() if con.execute(
        "SELECT 1 FROM ejercicios WHERE ejercicio = ?", (gestion_declarada,)).fetchone() else None
    cuota_param = redondear_bs(_param(p, "rau_cuota_por_ha"))
    if consulta is not None:
        sem2 = VERDE if Decimal(consulta["valor"]) == cuota_param else AMARILLO
        res2 = f"RND {consulta['referencia']}: {formato_bs(Decimal(consulta['valor']))}/ha (parámetro {formato_bs(cuota_param)}/ha)"
    else:
        limite = date(anio, int(_param(p, "rau_consulta_cuota_mes")), int(_param(p, "rau_consulta_cuota_dia")))
        sem2 = ROJO if hasta > limite and gestion_declarada >= int(con.execute(
            "SELECT MIN(ejercicio) FROM ejercicios").fetchone()[0]) else AMARILLO
        res2 = f"Sin consulta de RND registrada; cuota presupuestada {formato_bs(cuota_param)}/ha POR VALIDAR"
    v.append({"numero": 2, "verificacion": "Cuota vigente de la subzona y su efecto en la liquidación.",
              "fuente": "RND del año", "meta": "Valor confirmado", "resultado": res2, "semaforo": sem2})

    ind = _indicadores(con, ejercicio, hasta)
    sem3 = peor([ind["I1"]["semaforo"], ind["I2"]["semaforo"]])
    res3 = (f"I-1 {_pct(ind['I1']['valor'])}; I-2 {_pct(ind['I2']['valor'])}"
            if ind["I1"]["semaforo"] or ind["I2"]["semaforo"] else "No aplica: sin producción ni venta de alimento")
    v.append({"numero": 3, "verificacion": "I-1 y I-2 dentro de los umbrales.", "fuente": "FIN-INF-04",
              "meta": "Verde", "resultado": res3, "semaforo": sem3})

    servicios = int(con.execute("SELECT COUNT(*) FROM servicios_terceros WHERE fecha BETWEEN ? AND ?",
                                (desde.isoformat(), hasta.isoformat())).fetchone()[0])
    v.append({"numero": 4, "verificacion": "Cero servicios a terceros (maquila, faenado, alquileres).",
              "fuente": "Notas de entrega, contratos", "meta": "0 casos", "resultado": f"{servicios} caso(s)",
              "semaforo": ROJO if servicios else VERDE})

    sin_aut = int(con.execute(
        """SELECT COUNT(*) FROM notas_entrega n JOIN clientes c ON c.id = n.cliente_id
            WHERE n.producto = 'EXCEDENTE' AND c.es_socio = 0 AND n.fecha BETWEEN ? AND ?
              AND NOT EXISTS (SELECT 1 FROM compradores_autorizados a WHERE a.cliente_id = c.id
                                 AND a.ejercicio = ? AND a.estado = 'VIGENTE')""",
        (desde.isoformat(), hasta.isoformat(), ejercicio)).fetchone()[0])
    sem5 = peor([ROJO if sin_aut else VERDE, ind["no_socios"]["semaforo"]])
    v.append({"numero": 5, "verificacion": "Compradores de alimento identificados; no socios con autorización.",
              "fuente": "Fichas, FIN-ING-06", "meta": "100%",
              "resultado": f"{sin_aut} venta(s) a no socios sin autorización; {ind['no_socios']['cantidad']} no socio(s) "
                           "recurrente(s)", "semaforo": sem5})

    pct = _decimal(compras_sin_respaldo_pct, "porcentaje de compras sin respaldo")
    if pct < 0 or pct > 1:
        raise DatosInvalidosError("El porcentaje de compras sin respaldo se indica como fracción entre 0 y 1.")
    rojo6 = Decimal(_param(p, "rau_compras_sin_respaldo_rojo"))
    v.append({"numero": 6, "verificacion": "Compras a pequeños productores con respaldo completo.",
              "fuente": "Muestra de notas de compra", "meta": "100%",
              "resultado": f"{_pct(pct)} del valor muestreado sin documentación completa",
              "semaforo": VERDE if pct == 0 else (ROJO if pct > rojo6 else AMARILLO)})

    atraso = _entero(padron_atraso_trimestres, "trimestres de atraso del padrón", 0)
    idx = _indice(con, ejercicio)
    sem7 = peor([idx["semaforo"], VERDE if atraso == 0 else (AMARILLO if atraso == 1 else ROJO)])
    v.append({"numero": 7, "verificacion": "Padrón de socios, libro de actas y expediente A–G actualizados.",
              "fuente": "Índice maestro", "meta": "≥ 95% completo",
              "resultado": f"Expediente {_pct(idx['completitud'])} completo; padrón con {atraso} trimestre(s) de atraso",
              "semaforo": sem7})

    vencidos, observados = [], []
    for (g,) in con.execute("SELECT ejercicio FROM ejercicios ORDER BY ejercicio").fetchall():
        for e in _calendario(con, int(g), hasta):
            # Solo cuenta lo cumplido hasta el cierre del trimestre revisado.
            cumplido = e["cumplido_en"] is not None and a_fecha(e["cumplido_en"]) <= hasta
            if not cumplido and date.fromisoformat(e["limite"]) < hasta:
                vencidos.append(f"{e['descripcion']} (gestión {g})")
            elif cumplido and e["estado"] == "CUMPLIDO_CON_OBSERVACION" and a_fecha(e["cumplido_en"]) >= desde:
                observados.append(f"{e['descripcion']} (gestión {g}, cumplido con observación)")
    cert = certificado_vigente(con, hasta)
    sem8 = peor([ROJO if vencidos else VERDE, AMARILLO if observados else VERDE, VERDE if cert else AMARILLO])
    v.append({"numero": 8, "verificacion": "Calendario fiscal: Form. 701, patentes y certificados al día.",
              "fuente": "FIN-EGR-14; carpeta B", "meta": "0 vencimientos",
              "resultado": ("; ".join(vencidos + observados) or "Sin vencimientos")
              + ("" if cert else "; sin Certificado RAU vigente registrado"), "semaforo": sem8})

    sin_atender = int(con.execute("SELECT COUNT(*) FROM notificaciones_fiscales WHERE estado = 'ABIERTA' AND "
                                  "vencimiento < ?", (hasta.isoformat(),)).fetchone()[0])
    ultima = con.execute("SELECT MAX(fecha) FROM revisiones_buzon WHERE fecha <= ?", (hasta.isoformat(),)).fetchone()[0]
    dias_buzon = int(_param(p, "rau_buzon_revision_dias"))
    buzon_ok = ultima is not None and (hasta - date.fromisoformat(ultima)).days <= dias_buzon
    v.append({"numero": 9, "verificacion": "Revisión del buzón y notificaciones del SIN (Oficina Virtual) y del "
                                           "correo registrado.", "fuente": "Registro semanal de Finanzas",
              "meta": "0 notificaciones sin atender",
              "resultado": f"{sin_atender} notificación(es) vencida(s) sin atender; última revisión del buzón: "
                           f"{fecha_pantalla(ultima) if ultima else 'ninguna'}",
              "semaforo": peor([ROJO if sin_atender else VERDE, VERDE if buzon_ok else AMARILLO])})

    pendientes = capex_sin_control(con)
    rojos = int(con.execute("SELECT COUNT(*) FROM controles_superficie WHERE semaforo = 'ROJO' AND fecha BETWEEN ? AND ?",
                            (desde.isoformat(), hasta.isoformat())).fetchone()[0])
    v.append({"numero": 10, "verificacion": "Nuevas actividades o inversiones evaluadas contra el RAU antes de "
                                            "ejecutarse.", "fuente": "Actas del Consejo de Administración",
              "meta": "100%", "resultado": f"{len(pendientes)} línea(s) CAPEX sin control de superficie; "
                                           f"{rojos} control(es) en rojo",
              "semaforo": peor([ROJO if rojos else VERDE, AMARILLO if pendientes else VERDE])})
    return v


def registrar_revision_trimestral(con: sqlite3.Connection, sesion: Sesion, anio: int, trimestre: int, fecha: Any, *,
                                  compras_sin_respaldo_pct: Any, padron_atraso_trimestres: int,
                                  observaciones: str | None = None) -> dict[str, Any]:
    """El Consejo de Vigilancia registra la revisión trimestral (INF-CV-09).
    Todo rojo se escala: GG en 24 horas y CA en 5 días hábiles."""
    seguridad.exigir_permiso(con, sesion, "rau", "VERIFICAR")
    f = a_fecha(fecha)
    _, hasta = trimestre_calendario(anio, trimestre)
    if f < hasta:
        raise DatosInvalidosError(f"La revisión se ejecuta al cierre del trimestre ({fecha_pantalla(hasta)}).")
    with transaccion(con):
        if con.execute("SELECT 1 FROM revisiones_rau WHERE tipo = 'TRIMESTRAL' AND anio = ? AND trimestre = ?",
                       (anio, trimestre)).fetchone():
            raise ReglaNegocioError("La revisión de ese trimestre ya está registrada.", codigo="YA_REGISTRADO")
        verif = verificaciones_trimestrales(con, anio, trimestre, compras_sin_respaldo_pct=compras_sin_respaldo_pct,
                                            padron_atraso_trimestres=padron_atraso_trimestres)
        semaforo = peor(v["semaforo"] for v in verif)
        rid = int(con.execute(
            """INSERT INTO revisiones_rau (tipo, anio, trimestre, fecha, verificaciones_json, semaforo, observaciones,
                   elaborado_por, registrado_en) VALUES ('TRIMESTRAL',?,?,?,?,?,?,?,?)""",
            (anio, trimestre, f.isoformat(), auditoria.a_json(verif), semaforo, _opcional(observaciones, "observaciones",
                                                                                         2000),
             sesion.nombre_completo, _ahora_iso())).lastrowid)
        _indexar(con, sesion, "G02", ejercicio_de(con, hasta), f, f"INF-CV-09 trimestral T{trimestre}/{anio}")
        auditoria.registrar(con, sesion, "revisiones_rau", "INSERT", rid,
                            despues={"anio": anio, "trimestre": trimestre, "semaforo": semaforo})
    p = _params(con, ejercicio_de(con, hasta))
    escalamiento = None
    if semaforo == ROJO:
        escalamiento = {"gg_hasta": (f + timedelta(hours=int(_param(p, "rau_alerta_roja_gg_horas")))).isoformat(),
                        "ca_hasta": sumar_dias_habiles(f, int(_param(p, "rau_alerta_roja_ca_dias_habiles"))).isoformat()}
    return {"id": rid, "semaforo": semaforo, "verificaciones": verif, "escalamiento": escalamiento}


def verificaciones_anuales(con: sqlite3.Connection, gestion: int) -> list[dict[str, Any]]:
    """Las 5 verificaciones del formato INF-CV-09 (Anexo 10)."""
    f701 = con.execute("SELECT * FROM formularios_701 WHERE gestion = ?", (gestion,)).fetchone()
    consulta = con.execute("SELECT * FROM cumplimientos_fiscales WHERE gestion = ? AND evento = 'CONSULTA_RND'",
                           (gestion,)).fetchone()
    patentes = con.execute("SELECT COUNT(*) AS n, MIN(dentro_plazo) AS ok FROM patentes_municipales WHERE gestion = ?",
                           (gestion,)).fetchone()
    acta = con.execute("SELECT * FROM cumplimientos_fiscales WHERE gestion = ? AND evento = 'ACTA_COMPATIBILIDAD'",
                       (gestion,)).fetchone()
    inicio, fin = _rango(con, gestion)
    cert = certificado_vigente(con, fin)
    sin_cert = int(con.execute("SELECT COUNT(*) FROM notas_entrega WHERE fecha BETWEEN ? AND ? AND "
                               "(certificado_rau_ref IS NULL OR trim(certificado_rau_ref) = '')",
                               (inicio.isoformat(), fin.isoformat())).fetchone()[0])
    return [
        {"verificacion": "Formulario 701 V.3 presentado ≥15 días hábiles antes del 31 de octubre",
         "cumple": bool(f701) and f701["semaforo"] == VERDE,
         "observacion": (f"Presentado el {fecha_pantalla(f701['fecha_presentacion'])} con {f701['dias_habiles_antes']} "
                         "días hábiles de anticipación") if f701 else "No registrado"},
        {"verificacion": "Cuota RAU actualizada conforme a la RND del SIN vigente (variación UFV)",
         "cumple": consulta is not None,
         "observacion": f"{consulta['referencia']}: {formato_bs(Decimal(consulta['valor']))}/ha" if consulta else "Sin consulta de RND"},
        {"verificacion": "Patentes municipales de la región de los Yungas pagadas dentro de plazo",
         "cumple": bool(patentes["n"]) and bool(patentes["ok"]),
         "observacion": f"{patentes['n']} pago(s) registrado(s)" if patentes["n"] else "Sin pago registrado"},
        {"verificacion": "Certificado de Inscripción al RAU vigente y exhibido en cada venta",
         "cumple": cert is not None and sin_cert == 0,
         "observacion": (f"Certificado {cert['numero']}" if cert else "Sin certificado vigente")
         + f"; {sin_cert} nota(s) sin constancia de exhibición"},
        {"verificacion": "Revisión anual de compatibilidad de actividades con el RAU documentada en acta (Consejo de "
                         "Administración)", "cumple": acta is not None,
         "observacion": acta["referencia"] if acta else "Sin acta registrada"},
    ]


def registrar_informe_anual(con: sqlite3.Connection, sesion: Sesion, gestion: int, fecha: Any,
                            observaciones: str | None = None) -> dict[str, Any]:
    """INF-CV-09 anual: Formulario 701 y patentes presentados y pagados en plazo."""
    seguridad.exigir_permiso(con, sesion, "rau", "VERIFICAR")
    f = a_fecha(fecha)
    with transaccion(con):
        _ejercicio(con, gestion)
        if con.execute("SELECT 1 FROM revisiones_rau WHERE tipo = 'ANUAL' AND anio = ?", (gestion,)).fetchone():
            raise ReglaNegocioError("El INF-CV-09 anual de esa gestión ya está registrado.", codigo="YA_REGISTRADO")
        verif = verificaciones_anuales(con, gestion)
        f701 = con.execute("SELECT semaforo FROM formularios_701 WHERE gestion = ?", (gestion,)).fetchone()
        if all(v["cumple"] for v in verif):
            semaforo = VERDE
        elif f701 is None or f701["semaforo"] == ROJO:
            semaforo = ROJO
        else:
            semaforo = AMARILLO
        rid = int(con.execute(
            """INSERT INTO revisiones_rau (tipo, anio, trimestre, fecha, verificaciones_json, semaforo, observaciones,
                   elaborado_por, registrado_en) VALUES ('ANUAL',?,0,?,?,?,?,?,?)""",
            (gestion, f.isoformat(), auditoria.a_json(verif), semaforo, _opcional(observaciones, "observaciones", 2000),
             sesion.nombre_completo, _ahora_iso())).lastrowid)
        _indexar(con, sesion, "B06", gestion, f, f"INF-CV-09 anual gestión {gestion}")
        auditoria.registrar(con, sesion, "revisiones_rau", "INSERT", rid, despues={"gestion": gestion,
                                                                                  "semaforo": semaforo})
    return {"id": rid, "semaforo": semaforo, "verificaciones": verif}


def listar_revisiones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [{**dict(r), "verificaciones": json.loads(r["verificaciones_json"])}
            for r in con.execute("SELECT * FROM revisiones_rau ORDER BY anio DESC, trimestre DESC, id DESC")]


# ==========================================================================
# 10. ALERTAS Y RESUMEN
# ==========================================================================
def alertas_rau(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Alertas consolidadas: calendario, servicios a terceros, superficie,
    notificaciones y revisiones trimestrales pendientes."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    referencia = a_fecha(fecha) if fecha is not None else date.today()
    alertas = [{"nivel": ROJO if a["estado"] == "VENCIDO" else AMARILLO, "origen": "Calendario fiscal",
                "mensaje": a["mensaje"]} for a in alertas_calendario(con, sesion, referencia)]
    for s in con.execute("SELECT * FROM servicios_terceros WHERE estado <> 'CERRADO'"):
        alertas.append({"nivel": ROJO, "origen": "Servicio a terceros",
                        "mensaje": f"Caso N.º {s['id']} ({TIPOS_SERVICIO[s['tipo']].lower()} para {s['tercero']}): "
                                   f"informar a la GG hasta {fecha_pantalla(s['limite_gg'])} y resolver en el CA hasta "
                                   f"{fecha_pantalla(s['limite_ca'])}."})
    try:
        sup = estado_superficie(con, referencia)
        if sup["semaforo"] == ROJO:
            alertas.append({"nivel": ROJO, "origen": "Superficie",
                            "mensaje": f"Superficie ocupada {_ha_txt(sup['usada_mil'])} ha mayor a la declarada {_ha_txt(sup['declarada_mil'])} ha."})
    except DatosInvalidosError:
        pass
    for n in con.execute("SELECT * FROM notificaciones_fiscales WHERE estado = 'ABIERTA'"):
        dias = (date.fromisoformat(n["vencimiento"]) - referencia).days
        alertas.append({"nivel": ROJO if dias < 0 else AMARILLO, "origen": "Fiscalización",
                        "mensaje": f"Notificación {n['numero']} ({TIPOS_NOTIFICACION[n['tipo']]}): "
                                   + (f"plazo vencido hace {-dias} día(s)." if dias < 0 else f"vence en {dias} día(s).")})
    anio, mes = referencia.year, referencia.month
    t_anterior = (mes - 1) // 3
    previo = (anio, t_anterior) if t_anterior else (anio - 1, 4)
    try:
        _, hasta = trimestre_calendario(*previo)
        if con.execute("SELECT 1 FROM ejercicios WHERE ? BETWEEN fecha_inicio AND fecha_fin",
                       (hasta.isoformat(),)).fetchone() and not con.execute(
                "SELECT 1 FROM revisiones_rau WHERE tipo = 'TRIMESTRAL' AND anio = ? AND trimestre = ?", previo).fetchone():
            alertas.append({"nivel": AMARILLO, "origen": "Consejo de Vigilancia",
                            "mensaje": f"Falta la revisión trimestral INF-CV-09 del T{previo[1]}/{previo[0]} "
                                       f"(cierre {fecha_pantalla(hasta)})."})
    except DatosInvalidosError:
        pass
    return alertas


def listar_controles_superficie(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [{**dict(c), "adicional": _ha(c["hectareas_adicionales_mil"]), "usadas": _ha(c["usadas_mil"]),
             "declaradas": _ha(c["declaradas_mil"])}
            for c in con.execute("SELECT * FROM controles_superficie ORDER BY fecha DESC, id DESC")]


def listar_superficies(con: sqlite3.Connection, sesion: Sesion) -> dict[str, list[dict[str, Any]]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return {"declaradas": [{**dict(d), "hectareas": _ha(d["hectareas_mil"])} for d in con.execute(
                "SELECT * FROM superficies_declaradas ORDER BY fecha DESC, id DESC")],
            "usadas": [{**dict(u), "hectareas": _ha(u["hectareas_mil"])} for u in con.execute(
                "SELECT * FROM superficies_usadas ORDER BY desde DESC, id DESC")]}


def listar_compradores_autorizados(con: sqlite3.Connection, sesion: Sesion, ejercicio: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [dict(a) for a in con.execute(
        "SELECT a.*, c.nombre, c.codigo FROM compradores_autorizados a JOIN clientes c ON c.id = a.cliente_id "
        "WHERE a.ejercicio = ? ORDER BY c.nombre", (ejercicio,))]


def formulario_701(con: sqlite3.Connection, sesion: Sesion, gestion: int) -> dict[str, Any] | None:
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    f = con.execute("SELECT * FROM formularios_701 WHERE gestion = ?", (gestion,)).fetchone()
    return None if f is None else {**dict(f), "monto": de_centavos(int(f["monto_cent"])),
                                   "hectareas": _ha(f["hectareas_mil"])}




# ==========================================================================
# 11. CONSULTAS DE APOYO PARA LAS PANTALLAS
# ==========================================================================
def gestiones(con: sqlite3.Connection) -> list[int]:
    """Gestiones registradas (ejercicios de julio a junio)."""
    return [int(f[0]) for f in con.execute("SELECT ejercicio FROM ejercicios ORDER BY ejercicio")]


def clientes_alimento(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Clientes que pueden comprar alimento excedente (socios y agricultores)."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return [dict(c) for c in con.execute("SELECT id, codigo, nombre, tipo, es_socio FROM clientes WHERE es_socio = 1 "
                                         "OR tipo = 'AGRICULTOR' ORDER BY nombre")]


def lineas_capex_pendientes(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Líneas CAPEX vigentes sin control de superficie."""
    seguridad.exigir_permiso(con, sesion, "rau", "VER")
    return capex_sin_control(con)
