"""Archivo contable y financiero (MAN-ARC-01, Fase 11).

* Clases de retención: permanente, 10 años, 5 años, vida útil + 10 años y
  desecho a 2 años; los plazos se leen de ``parametros`` (regla 2).
* Nomenclatura de expedientes: ruta ``AF-FIN/G2026-27/ING/PR-FIN-09``,
  expediente ``EXP-ING-G2627-001``, caja ``CAJ-2026-001`` y archivo digital
  ``[CÓDIGO]_[AAAAMMDD]_[CORR]_v[X.Y]_[ESTADO].pdf``.
* Foliación continua con tomos de hasta 200 folios.
* Libro de préstamo (los originales requieren autorización de la Gerencia).
* Actas de transferencia al archivo histórico y de destrucción (la
  destrucción ordinaria la autoriza la GG y la visa el Consejo de Vigilancia;
  la simplificada de documentos de desecho solo la autoriza la GG).

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from typing import Any, Sequence

from db.conexion import obtener_parametros_vigentes, transaccion
from servicios import auditoria, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion
from utils.formato import a_fecha, fecha_pantalla

CICLOS: dict[str, str] = {
    "TES": "Tesorería e inversiones", "EGR": "Egresos", "TRA": "Transformación (producción)",
    "ING": "Ingresos", "INF": "Información gerencial", "TRV": "Transversal (cumplimiento legal y fiscal)",
    "EEFF": "Estados financieros",
}
CLASES: dict[str, str] = {
    "PERMANENTE": "Conservación permanente", "T10": "10 años", "T5": "5 años",
    "VIDA_UTIL_10": "Vida útil del bien + 10 años", "DESECHO": "Desecho a los 2 años",
}
COMPUTOS: dict[str, str] = {
    "CIERRE": "Desde el cierre de la gestión (30 de junio)", "SUSTITUCION": "Desde la sustitución del documento",
    "VIGENCIA": "Desde el fin de su vigencia", "BAJA": "Desde la baja del bien",
}
ESTADOS_DOCUMENTO: dict[str, str] = {"BOR": "Borrador", "REV": "En revisión", "APR": "Aprobado", "OBS": "Observado"}
TIPOS_ACTA: dict[str, str] = {
    "TRANSFERENCIA": "Transferencia al archivo histórico", "DESTRUCCION": "Destrucción (GG autoriza, CV visa)",
    "DESTRUCCION_SIMPLIFICADA": "Destrucción simplificada de documentos de desecho (GG)",
}
# El ciclo de producción se identifica como C03 en la nomenclatura (MAN-ARC-01 §2).
CODIGO_CICLO_NOMENCLATURA = {"TRA": "C03"}


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


def _entero(valor: Any, campo: str, minimo: int = 0) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        raise DatosInvalidosError(f"El campo {campo} debe ser un entero mayor o igual a {minimo}.")
    return valor


def _ejercicio(con: sqlite3.Connection, gestion: int) -> sqlite3.Row:
    _entero(gestion, "gestión", 2000)
    fila = con.execute("SELECT * FROM ejercicios WHERE ejercicio = ?", (gestion,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la gestión {gestion}.")
    return fila


def _param(con: sqlite3.Connection, gestion: int, clave: str) -> int:
    p = obtener_parametros_vigentes(con, gestion)
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro vigente «{clave}».", codigo="PARAMETRO_FALTANTE")
    return int(p[clave])


def sumar_anios(fecha: date, anios: int) -> date:
    """Suma años calendario (el 29-feb pasa al 28-feb si el año no es bisiesto)."""
    try:
        return fecha.replace(year=fecha.year + anios)
    except ValueError:
        return fecha.replace(year=fecha.year + anios, day=28)


def _expediente(con: sqlite3.Connection, expediente_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM expedientes_archivo WHERE id = ?", (expediente_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el expediente {expediente_id}.")
    return fila


# ==========================================================================
# 1. NOMENCLATURA (MAN-ARC-01 §2)
# ==========================================================================
def codigo_gestion(gestion: int, corto: bool = False) -> str:
    """``G2026-27`` (o ``G2627`` en forma corta) para la gestión 1-jul-2026 a 30-jun-2027."""
    _entero(gestion, "gestión", 2000)
    siguiente = (gestion + 1) % 100
    return f"G{gestion % 100:02d}{siguiente:02d}" if corto else f"G{gestion}-{siguiente:02d}"


def ruta_archivo(gestion: int, ciclo: str, procedimiento: str) -> str:
    """Ruta física y digital: ``AF-FIN/G2026-27/ING/PR-FIN-09``."""
    if ciclo not in CICLOS:
        raise DatosInvalidosError(f"Ciclo no válido: {ciclo!r}.")
    proc = _texto(procedimiento, "el procedimiento", 30).upper()
    return f"AF-FIN/{codigo_gestion(gestion)}/{ciclo}/{proc}"


def codigo_expediente(ciclo: str, gestion: int, correlativo: int) -> str:
    """``EXP-ING-G2627-001``; el ciclo de producción usa ``C03``."""
    if ciclo not in CICLOS:
        raise DatosInvalidosError(f"Ciclo no válido: {ciclo!r}.")
    _entero(correlativo, "correlativo", 1)
    return f"EXP-{CODIGO_CICLO_NOMENCLATURA.get(ciclo, ciclo)}-{codigo_gestion(gestion, True)}-{correlativo:03d}"


def codigo_caja(anio: int, numero: int) -> str:
    """``CAJ-2026-001``."""
    _entero(anio, "año", 2000)
    _entero(numero, "número de caja", 1)
    return f"CAJ-{anio}-{numero:03d}"


def nombre_digital(codigo_formato: str, fecha: Any, correlativo: int, version: str, estado: str) -> str:
    """``[CÓDIGO]_[AAAAMMDD]_[CORR]_v[X.Y]_[ESTADO].pdf`` (p. ej.
    ``FIN-ING-02_20260815_0001_v1.0_APR.pdf``)."""
    codigo = _texto(codigo_formato, "el código del formato", 30).upper()
    if not re.fullmatch(r"[A-Z0-9-]+", codigo):
        raise DatosInvalidosError("El código del formato solo admite letras, números y guiones.")
    if not re.fullmatch(r"\d+\.\d+", version or ""):
        raise DatosInvalidosError("La versión se indica como X.Y (por ejemplo 1.0).")
    if estado not in ESTADOS_DOCUMENTO:
        raise DatosInvalidosError("El estado del documento debe ser BOR, REV, APR u OBS.")
    _entero(correlativo, "correlativo", 1)
    return f"{codigo}_{a_fecha(fecha).strftime('%Y%m%d')}_{correlativo:04d}_v{version}_{estado}.pdf"


# ==========================================================================
# 2. TABLA DE RETENCIÓN Y VENCIMIENTO
# ==========================================================================
def tabla_retencion(con: sqlite3.Connection, sesion: Sesion, ciclo: str | None = None) -> list[dict[str, Any]]:
    """Tabla de retención documental (MAN-ARC-01 §3) con los plazos vigentes."""
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    sql, args = "SELECT * FROM retencion_documental", ()
    if ciclo:
        sql, args = sql + " WHERE ciclo = ?", (ciclo,)
    return [{**dict(r), "clase_txt": CLASES[r["clase"]], "computo_txt": COMPUTOS[r["computo"]]}
            for r in con.execute(sql + " ORDER BY ciclo, codigo", args)]


def anios_retencion(con: sqlite3.Connection, gestion: int, clase: str) -> int | None:
    """Años de conservación de la clase (``None`` = permanente)."""
    claves = {"T10": "archivo_retencion_t10_anios", "T5": "archivo_retencion_t5_anios",
              "DESECHO": "archivo_retencion_desecho_anios",
              "VIDA_UTIL_10": "archivo_retencion_vida_util_adicional_anios"}
    if clase == "PERMANENTE":
        return None
    if clase not in claves:
        raise DatosInvalidosError(f"Clase de retención no válida: {clase!r}.")
    return _param(con, gestion, claves[clase])


def calcular_vencimiento(con: sqlite3.Connection, gestion: int, clase: str, computo: str,
                         fecha_base: Any = None) -> date | None:
    """Fecha desde la que el expediente puede destruirse.

    * Permanente: nunca vence.
    * Cómputo desde el cierre: 30 de junio de la gestión + años de la clase.
    * Sustitución, vigencia o baja del bien: fecha base + años de la clase
      (vida útil + 10: la fecha base es la baja del bien).
    """
    if computo not in COMPUTOS:
        raise DatosInvalidosError(f"Cómputo no válido: {computo!r}.")
    anios = anios_retencion(con, gestion, clase)
    if anios is None:
        return None
    if computo == "CIERRE":
        return sumar_anios(date.fromisoformat(_ejercicio(con, gestion)["fecha_fin"]), anios)
    if fecha_base is None:
        return None
    return sumar_anios(a_fecha(fecha_base), anios)


# ==========================================================================
# 3. EXPEDIENTES Y FOLIACIÓN
# ==========================================================================
def abrir_expediente(con: sqlite3.Connection, sesion: Sesion, *, ciclo: str, gestion: int, titulo: str,
                     procedimiento: str, codigo_documental: str | None = None, tipo: str = "SERIE",
                     clase: str | None = None, computo: str | None = None, caja: str | None = None,
                     fecha_inicio: Any = None) -> dict[str, Any]:
    """Abre un expediente con su código, ruta y clase de retención.

    Si se indica ``codigo_documental`` (p. ej. ``FIN-ING-02``), la clase y el
    cómputo se toman de la tabla de retención; si no, deben indicarse."""
    seguridad.exigir_permiso(con, sesion, "archivo", "CREAR")
    if ciclo not in CICLOS:
        raise DatosInvalidosError(f"Ciclo no válido: {ciclo!r}.")
    nombre = _texto(titulo, "el título del expediente", 200)
    proc = _texto(procedimiento, "el procedimiento", 30).upper()
    with transaccion(con):
        _ejercicio(con, gestion)
        if codigo_documental:
            ret = con.execute("SELECT * FROM retencion_documental WHERE codigo = ?", (codigo_documental,)).fetchone()
            if ret is None:
                raise DatosInvalidosError(f"El código {codigo_documental!r} no está en la tabla de retención.")
            clase, computo = ret["clase"], ret["computo"]
        if clase not in CLASES:
            raise DatosInvalidosError("Indique la clase de retención (o el código documental).")
        if computo not in COMPUTOS:
            raise DatosInvalidosError("Indique el cómputo del plazo (o el código documental).")
        if caja is not None and not re.fullmatch(r"CAJ-\d{4}-\d{3}", caja):
            raise DatosInvalidosError("La caja sigue el formato CAJ-AAAA-NNN.")
        n = int(con.execute("SELECT COUNT(*) FROM expedientes_archivo WHERE ciclo = ? AND gestion = ?",
                            (ciclo, gestion)).fetchone()[0]) + 1
        codigo = codigo_expediente(ciclo, gestion, n)
        vencimiento = calcular_vencimiento(con, gestion, clase, computo) if computo == "CIERRE" else None
        inicio = a_fecha(fecha_inicio).isoformat() if fecha_inicio is not None else None
        eid = int(con.execute(
            """INSERT INTO expedientes_archivo (codigo, ciclo, gestion, correlativo, tipo, titulo, procedimiento,
                   codigo_documental, clase, computo, vencimiento, ruta, caja, fecha_inicio, registrado_por,
                   registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (codigo, ciclo, gestion, f"{n:03d}", _texto(tipo, "el tipo", 40).upper(), nombre, proc, codigo_documental,
             clase, computo, vencimiento.isoformat() if vencimiento else None, ruta_archivo(gestion, ciclo, proc), caja,
             inicio, sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "expedientes_archivo", "INSERT", eid,
                            despues={"codigo": codigo, "titulo": nombre, "clase": clase, "computo": computo,
                                     "vencimiento": vencimiento})
    return {"id": eid, "codigo": codigo, "ruta": ruta_archivo(gestion, ciclo, proc), "clase": clase,
            "vencimiento": vencimiento.isoformat() if vencimiento else None}


def registrar_documento(con: sqlite3.Connection, sesion: Sesion, expediente_id: int, *, codigo_formato: str,
                        fecha: Any, descripcion: str, folios: int, version: str = "1.0",
                        estado_documento: str = "APR") -> dict[str, Any]:
    """Agrega un documento al expediente con foliación continua. Cada tomo
    admite hasta ``archivo_folios_max_legajo`` folios; si el documento no cabe
    en el tomo en curso, se abre uno nuevo."""
    seguridad.exigir_permiso(con, sesion, "archivo", "CREAR")
    _entero(folios, "folios", 1)
    texto = _texto(descripcion, "la descripción", 300)
    with transaccion(con):
        exp = _expediente(con, expediente_id)
        if exp["estado"] != "ABIERTO":
            raise ReglaNegocioError("Solo se agregan documentos a expedientes abiertos.", codigo="EXPEDIENTE_CERRADO")
        maximo = _param(con, int(exp["gestion"]), "archivo_folios_max_legajo")
        if folios > maximo:
            raise ReglaNegocioError(f"Un documento de {folios} folios supera el tomo de {maximo} folios: divídalo.",
                                    codigo="TOMO_EXCEDIDO")
        ultimo = con.execute("SELECT * FROM documentos_archivo WHERE expediente_id = ? ORDER BY id DESC LIMIT 1",
                             (expediente_id,)).fetchone()
        tomo, desde = 1, 1
        if ultimo is not None:
            tomo, desde = int(ultimo["tomo"]), int(ultimo["folio_hasta"]) + 1
            usados = int(con.execute("SELECT COALESCE(SUM(folios), 0) FROM documentos_archivo WHERE expediente_id = ? "
                                     "AND tomo = ?", (expediente_id, tomo)).fetchone()[0])
            if usados + folios > maximo:
                tomo += 1
        correlativo = int(con.execute("SELECT COUNT(*) FROM documentos_archivo WHERE expediente_id = ? AND "
                                      "codigo_formato = ?", (expediente_id, codigo_formato.upper())).fetchone()[0]) + 1
        nombre = nombre_digital(codigo_formato, fecha, correlativo, version, estado_documento)
        hasta = desde + folios - 1
        did = int(con.execute(
            """INSERT INTO documentos_archivo (expediente_id, codigo_formato, fecha, correlativo, version,
                   estado_documento, descripcion, folios, folio_desde, folio_hasta, tomo, nombre_digital,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (expediente_id, codigo_formato.upper(), a_fecha(fecha).isoformat(), correlativo, version, estado_documento,
             texto, folios, desde, hasta, tomo, nombre, sesion.nombre_usuario, _ahora_iso())).lastrowid)
        con.execute("UPDATE expedientes_archivo SET folios = ? WHERE id = ?", (hasta, expediente_id))
        auditoria.registrar(con, sesion, "documentos_archivo", "INSERT", did,
                            despues={"expediente": exp["codigo"], "nombre_digital": nombre, "folios": f"{desde}-{hasta}",
                                     "tomo": tomo})
    return {"id": did, "nombre_digital": nombre, "folio_desde": desde, "folio_hasta": hasta, "tomo": tomo}


def cerrar_expediente(con: sqlite3.Connection, sesion: Sesion, expediente_id: int, fecha_fin: Any, *,
                      fecha_base: Any = None, caja: str | None = None) -> dict[str, Any]:
    """Cierra el expediente y fija su vencimiento. Para cómputos desde la
    sustitución, la vigencia o la baja del bien se exige la fecha base."""
    seguridad.exigir_permiso(con, sesion, "archivo", "EDITAR")
    f = a_fecha(fecha_fin)
    with transaccion(con):
        exp = _expediente(con, expediente_id)
        if exp["estado"] != "ABIERTO":
            raise ReglaNegocioError("El expediente no está abierto.", codigo="EXPEDIENTE_CERRADO")
        if exp["computo"] != "CIERRE" and exp["clase"] != "PERMANENTE" and fecha_base is None:
            raise DatosInvalidosError(f"Indique la fecha base del plazo ({COMPUTOS[exp['computo']].lower()}).")
        if caja is not None and not re.fullmatch(r"CAJ-\d{4}-\d{3}", caja):
            raise DatosInvalidosError("La caja sigue el formato CAJ-AAAA-NNN.")
        base = a_fecha(fecha_base) if fecha_base is not None else None
        venc = calcular_vencimiento(con, int(exp["gestion"]), exp["clase"], exp["computo"], base)
        con.execute("UPDATE expedientes_archivo SET estado = 'CERRADO', fecha_fin = ?, fecha_base = ?, vencimiento = ?, "
                    "caja = COALESCE(?, caja), cerrado_por = ?, cerrado_en = ? WHERE id = ?",
                    (f.isoformat(), base.isoformat() if base else None, venc.isoformat() if venc else None, caja,
                     sesion.nombre_completo, _ahora_iso(), expediente_id))
        auditoria.registrar(con, sesion, "expedientes_archivo", "UPDATE", expediente_id,
                            antes={"estado": "ABIERTO", "vencimiento": exp["vencimiento"]},
                            despues={"estado": "CERRADO", "fecha_fin": f, "vencimiento": venc})
    return {"vencimiento": venc.isoformat() if venc else None}


def registrar_bloqueo_legal(con: sqlite3.Connection, sesion: Sesion, expediente_id: int, motivo: str | None) -> None:
    """Bloqueo (o desbloqueo con ``None``) por fiscalización, litigio o
    auditoría: impide la destrucción aunque el plazo haya vencido."""
    seguridad.exigir_permiso(con, sesion, "archivo", "AUTORIZAR")
    texto = _opcional(motivo, "el motivo del bloqueo", 300)
    with transaccion(con):
        exp = _expediente(con, expediente_id)
        con.execute("UPDATE expedientes_archivo SET bloqueo_legal = ? WHERE id = ?", (texto, expediente_id))
        auditoria.registrar(con, sesion, "expedientes_archivo", "UPDATE", expediente_id,
                            antes={"bloqueo_legal": exp["bloqueo_legal"]}, despues={"bloqueo_legal": texto})


def listar_expedientes(con: sqlite3.Connection, sesion: Sesion, gestion: int | None = None,
                       estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    sql, args = "SELECT * FROM expedientes_archivo WHERE 1 = 1", []
    if gestion is not None:
        sql += " AND gestion = ?"
        args.append(gestion)
    if estado:
        sql += " AND estado = ?"
        args.append(estado)
    hoy = date.today().isoformat()
    return [{**dict(e), "clase_txt": CLASES[e["clase"]],
             "prestado": bool(con.execute("SELECT 1 FROM prestamos_archivo WHERE expediente_id = ? AND "
                                          "estado = 'PRESTADO'", (e["id"],)).fetchone()),
             "vencido": bool(e["vencimiento"]) and e["vencimiento"] <= hoy}
            for e in con.execute(sql + " ORDER BY gestion DESC, ciclo, correlativo", args)]


def documentos_expediente(con: sqlite3.Connection, sesion: Sesion, expediente_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    return [dict(d) for d in con.execute("SELECT * FROM documentos_archivo WHERE expediente_id = ? ORDER BY id",
                                         (expediente_id,))]


# ==========================================================================
# 4. LIBRO DE PRÉSTAMO
# ==========================================================================
def prestar_expediente(con: sqlite3.Connection, sesion: Sesion, expediente_id: int, *, solicitante: str, cargo: str,
                       motivo: str, original: bool, fecha_salida: Any, devolucion_prevista: Any,
                       sesion_autoriza: Sesion | None = None) -> int:
    """Registra la salida de un expediente. Los originales requieren la
    autorización de la Gerencia General (``sesion_autoriza`` con permiso
    archivo/AUTORIZAR, o el registro hecho por la propia Gerencia)."""
    if not seguridad.tiene_permiso(con, sesion.rol, "archivo", "AUTORIZAR"):
        seguridad.exigir_permiso(con, sesion, "archivo", "CREAR")
    salida, prevista = a_fecha(fecha_salida), a_fecha(devolucion_prevista)
    if prevista < salida:
        raise DatosInvalidosError("La devolución prevista no puede ser anterior a la salida.")
    autorizado_por = None
    if original:
        autoriza = sesion_autoriza or sesion
        if not seguridad.tiene_permiso(con, autoriza.rol, "archivo", "AUTORIZAR"):
            raise AutorizacionError("El préstamo de originales requiere la autorización de la Gerencia General.")
        autorizado_por = autoriza.nombre_completo
    with transaccion(con):
        exp = _expediente(con, expediente_id)
        if exp["estado"] in ("TRANSFERIDO", "DESTRUIDO"):
            raise ReglaNegocioError(f"El expediente está {exp['estado'].lower()}.", codigo="EXPEDIENTE_NO_DISPONIBLE")
        if con.execute("SELECT 1 FROM prestamos_archivo WHERE expediente_id = ? AND estado = 'PRESTADO'",
                       (expediente_id,)).fetchone():
            raise ReglaNegocioError("El expediente ya está prestado: registre primero su devolución.",
                                    codigo="YA_PRESTADO")
        pid = int(con.execute(
            """INSERT INTO prestamos_archivo (expediente_id, solicitante, cargo, motivo, original, fecha_salida,
                   devolucion_prevista, autorizado_por, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (expediente_id, _texto(solicitante, "el solicitante", 120), _texto(cargo, "el cargo", 80),
             _texto(motivo, "el motivo", 300), int(bool(original)), salida.isoformat(), prevista.isoformat(),
             autorizado_por, sesion.nombre_usuario, _ahora_iso())).lastrowid)
        auditoria.registrar(con, sesion, "prestamos_archivo", "INSERT", pid,
                            despues={"expediente": exp["codigo"], "solicitante": solicitante, "original": original,
                                     "autorizado_por": autorizado_por, "devolucion_prevista": prevista})
    return pid


def devolver_expediente(con: sqlite3.Connection, sesion: Sesion, prestamo_id: int, fecha_devolucion: Any) -> dict[str, Any]:
    """Registra la devolución (el archivo verifica la integridad de la foliación)."""
    seguridad.exigir_permiso(con, sesion, "archivo", "CREAR")
    f = a_fecha(fecha_devolucion)
    with transaccion(con):
        p = con.execute("SELECT * FROM prestamos_archivo WHERE id = ?", (prestamo_id,)).fetchone()
        if p is None or p["estado"] != "PRESTADO":
            raise ReglaNegocioError("El préstamo no está pendiente de devolución.", codigo="NO_PRESTADO")
        if f < date.fromisoformat(p["fecha_salida"]):
            raise DatosInvalidosError("La devolución no puede ser anterior a la salida.")
        con.execute("UPDATE prestamos_archivo SET estado = 'DEVUELTO', fecha_devolucion = ?, recibido_por = ? "
                    "WHERE id = ?", (f.isoformat(), sesion.nombre_completo, prestamo_id))
        atraso = max((f - date.fromisoformat(p["devolucion_prevista"])).days, 0)
        auditoria.registrar(con, sesion, "prestamos_archivo", "UPDATE", prestamo_id, antes={"estado": "PRESTADO"},
                            despues={"estado": "DEVUELTO", "fecha_devolucion": f, "dias_atraso": atraso})
    return {"dias_atraso": atraso}


def libro_prestamos(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None,
                    solo_pendientes: bool = False) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    referencia = (a_fecha(fecha) if fecha is not None else date.today()).isoformat()
    sql = ("SELECT p.*, e.codigo, e.titulo FROM prestamos_archivo p JOIN expedientes_archivo e ON e.id = p.expediente_id")
    if solo_pendientes:
        sql += " WHERE p.estado = 'PRESTADO'"
    return [{**dict(p), "atrasado": p["estado"] == "PRESTADO" and p["devolucion_prevista"] < referencia}
            for p in con.execute(sql + " ORDER BY p.fecha_salida DESC, p.id DESC")]


# ==========================================================================
# 5. ACTAS DE TRANSFERENCIA Y DESTRUCCIÓN
# ==========================================================================
def _gestiones_fiscalizadas(con: sqlite3.Connection) -> set[int]:
    """Gestiones alcanzadas por notificaciones del SIN no cerradas."""
    gestiones: set[int] = set()
    for (texto,) in con.execute("SELECT gestiones FROM notificaciones_fiscales WHERE estado <> 'CERRADA'"):
        gestiones |= {int(g) for g in str(texto).split(",") if g.strip()}
    return gestiones


def impedimentos(con: sqlite3.Connection, exp: sqlite3.Row, tipo: str, fecha: date) -> list[str]:
    """Motivos por los que el expediente no puede incluirse en el acta."""
    motivos: list[str] = []
    if exp["estado"] != "CERRADO":
        motivos.append(f"está {exp['estado'].lower()} (solo expedientes cerrados)")
    if con.execute("SELECT 1 FROM prestamos_archivo WHERE expediente_id = ? AND estado = 'PRESTADO'",
                   (exp["id"],)).fetchone():
        motivos.append("está prestado")
    if tipo == "TRANSFERENCIA":
        if exp["clase"] != "PERMANENTE":
            motivos.append("solo los expedientes de conservación permanente se transfieren al archivo histórico")
        return motivos
    if exp["clase"] == "PERMANENTE":
        motivos.append("es de conservación permanente")
    if tipo == "DESTRUCCION_SIMPLIFICADA" and exp["clase"] != "DESECHO":
        motivos.append("la destrucción simplificada solo procede con documentos de desecho")
    if tipo == "DESTRUCCION" and exp["clase"] == "DESECHO":
        motivos.append("los documentos de desecho se destruyen con acta simplificada")
    if not exp["vencimiento"]:
        motivos.append("no tiene fecha de vencimiento (falta la fecha base)")
    elif date.fromisoformat(exp["vencimiento"]) > fecha:
        motivos.append(f"su plazo vence el {fecha_pantalla(exp['vencimiento'])}")
    if exp["bloqueo_legal"]:
        motivos.append(f"tiene bloqueo legal ({exp['bloqueo_legal']})")
    if int(exp["gestion"]) in _gestiones_fiscalizadas(con):
        motivos.append("su gestión está alcanzada por una fiscalización abierta")
    return motivos


def expedientes_elegibles(con: sqlite3.Connection, sesion: Sesion, tipo: str, fecha: Any = None) -> list[dict[str, Any]]:
    """Expedientes que pueden incluirse en un acta del tipo indicado."""
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    if tipo not in TIPOS_ACTA:
        raise DatosInvalidosError(f"Tipo de acta no válido: {tipo!r}.")
    f = a_fecha(fecha) if fecha is not None else date.today()
    en_acta = {int(r[0]) for r in con.execute(
        "SELECT d.expediente_id FROM acta_archivo_detalle d JOIN actas_archivo a ON a.id = d.acta_id "
        "WHERE a.estado <> 'EJECUTADA'")}
    return [dict(e) for e in con.execute("SELECT * FROM expedientes_archivo WHERE estado = 'CERRADO' ORDER BY codigo")
            if not impedimentos(con, e, tipo, f) and int(e["id"]) not in en_acta]


def preparar_acta(con: sqlite3.Connection, sesion: Sesion, tipo: str, fecha: Any, metodo: str,
                  expediente_ids: Sequence[int], observacion: str | None = None) -> dict[str, Any]:
    """Finanzas prepara el acta con la relación de expedientes."""
    seguridad.exigir_permiso(con, sesion, "archivo", "CREAR")
    if tipo not in TIPOS_ACTA:
        raise DatosInvalidosError(f"Tipo de acta no válido: {tipo!r}.")
    if not expediente_ids:
        raise DatosInvalidosError("Seleccione al menos un expediente.")
    f = a_fecha(fecha)
    forma = _texto(metodo, "el método (destino del traslado o forma de destrucción)", 200)
    with transaccion(con):
        en_acta = {int(r[0]) for r in con.execute(
            "SELECT d.expediente_id FROM acta_archivo_detalle d JOIN actas_archivo a ON a.id = d.acta_id "
            "WHERE a.estado <> 'EJECUTADA'")}
        for eid in sorted(set(expediente_ids)):
            exp = _expediente(con, eid)
            motivos = impedimentos(con, exp, tipo, f)
            if eid in en_acta:
                motivos.append("ya figura en otra acta en trámite")
            if motivos:
                raise ReglaNegocioError(f"El expediente {exp['codigo']} no puede incluirse: {'; '.join(motivos)}.",
                                        codigo="EXPEDIENTE_NO_ELEGIBLE")
        numero = int(con.execute("SELECT COALESCE(MAX(numero), 0) FROM actas_archivo WHERE tipo = ? AND anio = ?",
                                 (tipo, f.year)).fetchone()[0]) + 1
        aid = int(con.execute(
            """INSERT INTO actas_archivo (tipo, numero, anio, fecha, metodo, observacion, elaborado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tipo, numero, f.year, f.isoformat(), forma, _opcional(observacion, "observación", 1000),
             sesion.nombre_completo, _ahora_iso())).lastrowid)
        for eid in sorted(set(expediente_ids)):
            con.execute("INSERT INTO acta_archivo_detalle (acta_id, expediente_id) VALUES (?, ?)", (aid, eid))
        auditoria.registrar(con, sesion, "actas_archivo", "INSERT", aid,
                            despues={"tipo": tipo, "numero": numero, "expedientes": sorted(set(expediente_ids))})
    return {"id": aid, "numero": f"{numero:03d}/{f.year}"}


def _acta(con: sqlite3.Connection, acta_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM actas_archivo WHERE id = ?", (acta_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el acta {acta_id}.")
    return fila


def autorizar_acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int) -> None:
    """La Gerencia General autoriza el acta."""
    seguridad.exigir_permiso(con, sesion, "archivo", "AUTORIZAR")
    with transaccion(con):
        a = _acta(con, acta_id)
        if a["estado"] != "PREPARADA":
            raise ReglaNegocioError("El acta no está pendiente de autorización.", codigo="ESTADO_INVALIDO")
        con.execute("UPDATE actas_archivo SET estado = 'AUTORIZADA', autorizado_por = ?, autorizado_en = ? WHERE id = ?",
                    (sesion.nombre_completo, _ahora_iso(), acta_id))
        auditoria.registrar(con, sesion, "actas_archivo", "AUTORIZACION", acta_id, antes={"estado": "PREPARADA"},
                            despues={"estado": "AUTORIZADA", "autorizado_por": sesion.nombre_completo})


def visar_acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int) -> None:
    """El Consejo de Vigilancia visa el acta de destrucción ordinaria."""
    seguridad.exigir_permiso(con, sesion, "archivo", "VERIFICAR")
    with transaccion(con):
        a = _acta(con, acta_id)
        if a["tipo"] != "DESTRUCCION":
            raise ReglaNegocioError("Solo el acta de destrucción ordinaria requiere el visado del Consejo de Vigilancia.",
                                    codigo="NO_REQUIERE_VISADO")
        if a["estado"] != "AUTORIZADA":
            raise ReglaNegocioError("El acta debe estar autorizada por la Gerencia General.", codigo="ESTADO_INVALIDO")
        con.execute("UPDATE actas_archivo SET estado = 'VISADA', visado_por = ?, visado_en = ? WHERE id = ?",
                    (sesion.nombre_completo, _ahora_iso(), acta_id))
        auditoria.registrar(con, sesion, "actas_archivo", "AUTORIZACION", acta_id, antes={"estado": "AUTORIZADA"},
                            despues={"estado": "VISADA", "visado_por": sesion.nombre_completo})


def ejecutar_acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int, fecha: Any) -> dict[str, Any]:
    """Ejecuta el traslado o la destrucción y actualiza los expedientes. Las
    condiciones se verifican de nuevo a la fecha de ejecución."""
    seguridad.exigir_permiso(con, sesion, "archivo", "EDITAR")
    f = a_fecha(fecha)
    with transaccion(con):
        a = _acta(con, acta_id)
        requerido = "VISADA" if a["tipo"] == "DESTRUCCION" else "AUTORIZADA"
        if a["estado"] != requerido:
            raise ReglaNegocioError(f"El acta debe estar {requerido.lower()} antes de ejecutarse.",
                                    codigo="ESTADO_INVALIDO")
        if f < date.fromisoformat(a["fecha"]):
            raise DatosInvalidosError("La ejecución no puede ser anterior a la fecha del acta.")
        nuevo = "TRANSFERIDO" if a["tipo"] == "TRANSFERENCIA" else "DESTRUIDO"
        expedientes = con.execute("SELECT e.* FROM acta_archivo_detalle d JOIN expedientes_archivo e "
                                  "ON e.id = d.expediente_id WHERE d.acta_id = ? ORDER BY e.codigo", (acta_id,)).fetchall()
        for exp in expedientes:
            motivos = impedimentos(con, exp, a["tipo"], f)
            if motivos:
                raise ReglaNegocioError(f"El expediente {exp['codigo']} ya no cumple las condiciones: "
                                        f"{'; '.join(motivos)}.", codigo="EXPEDIENTE_NO_ELEGIBLE")
        for exp in expedientes:
            con.execute("UPDATE expedientes_archivo SET estado = ? WHERE id = ?", (nuevo, exp["id"]))
            auditoria.registrar(con, sesion, "expedientes_archivo", "UPDATE", int(exp["id"]),
                                antes={"estado": "CERRADO"}, despues={"estado": nuevo, "acta": acta_id})
        con.execute("UPDATE actas_archivo SET estado = 'EJECUTADA', ejecutado_en = ? WHERE id = ?",
                    (f.isoformat(), acta_id))
        auditoria.registrar(con, sesion, "actas_archivo", "UPDATE", acta_id, antes={"estado": a["estado"]},
                            despues={"estado": "EJECUTADA", "ejecutado_en": f, "expedientes": len(expedientes)})
    return {"expedientes": len(expedientes), "estado": nuevo}


def listar_actas(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    return [{**dict(a), "tipo_txt": TIPOS_ACTA[a["tipo"]], "numero_txt": f"{int(a['numero']):03d}/{a['anio']}",
             "expedientes": [dict(e) for e in con.execute(
                 "SELECT e.id, e.codigo, e.titulo, e.clase, e.vencimiento, e.folios FROM acta_archivo_detalle d "
                 "JOIN expedientes_archivo e ON e.id = d.expediente_id WHERE d.acta_id = ? ORDER BY e.codigo",
                 (a["id"],))]}
            for a in con.execute("SELECT * FROM actas_archivo ORDER BY fecha DESC, id DESC")]


def acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int) -> dict[str, Any]:
    for a in listar_actas(con, sesion):
        if a["id"] == acta_id:
            return a
    raise DatosInvalidosError(f"No existe el acta {acta_id}.")


def resumen_archivo(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> dict[str, Any]:
    """Expedientes por estado, préstamos atrasados y expedientes vencidos."""
    seguridad.exigir_permiso(con, sesion, "archivo", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    por_estado = {r[0]: int(r[1]) for r in con.execute("SELECT estado, COUNT(*) FROM expedientes_archivo GROUP BY estado")}
    atrasados = [p for p in libro_prestamos(con, sesion, f, True) if p["atrasado"]]
    vencidos = int(con.execute("SELECT COUNT(*) FROM expedientes_archivo WHERE estado = 'CERRADO' AND vencimiento <= ?",
                               (f.isoformat(),)).fetchone()[0])
    return {"por_estado": por_estado, "prestamos_atrasados": atrasados, "vencidos": vencidos}
