"""Administración de parámetros normativos con historial (Fase 13).

Regla 2 del proyecto: toda cifra normativa (tasas, topes, plazos, SME) vive en
la tabla ``parametros`` con vigencia por ejercicio. Este servicio permite
cambiarlas sin tocar el código y sin perder la historia:

* La Gerencia General **propone** el cambio (permiso ``parametros/EDITAR``) con
  el valor nuevo, el ejercicio desde el que rige, la fuente documental y el
  motivo.
* El Consejo de Administración o la Asamblea General lo **aprueban o rechazan**
  (``parametros/APROBAR``) con la referencia del acta.
* Al aprobarse, la vigencia anterior se cierra en el ejercicio previo y se
  inserta la nueva; ninguna vigencia se borra (disparador
  ``trg_p13_parametros_no_delete``). Si el valor nuevo rige desde el mismo
  ejercicio que el vigente, se reemplaza el valor y la historia queda en la
  solicitud y en la bitácora (valores antes y después).
* No se cambian parámetros de ejercicios cerrados (``PeriodoCerradoError``).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from db.conexion import convertir_valor_parametro, transaccion
from servicios import auditoria, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, PeriodoCerradoError, ReglaNegocioError
from servicios.seguridad import Sesion

MARCAS_POR_VALIDAR = ("VALIDAR", "propuest", "Propuest", "PROPUEST")


def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 500) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"Indique {campo}.")
    texto = valor.strip()
    if len(texto) > maximo:
        raise DatosInvalidosError(f"El campo {campo} admite hasta {maximo} caracteres.")
    return texto


def por_validar(fuente: str | None) -> bool:
    """``True`` si la fuente marca el valor como propuesta o «POR VALIDAR»."""
    return bool(fuente) and any(m in fuente for m in MARCAS_POR_VALIDAR)


def _vigente(con: sqlite3.Connection, clave: str, ejercicio: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM parametros WHERE clave = ? AND ejercicio_desde <= ? AND "
                       "(ejercicio_hasta IS NULL OR ejercicio_hasta >= ?)", (clave, ejercicio, ejercicio)).fetchone()


def listar_parametros(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, filtro: str | None = None,
                      solo_por_validar: bool = False) -> list[dict[str, Any]]:
    """Parámetros vigentes en el ejercicio con su tipo, fuente y vigencia."""
    seguridad.exigir_permiso(con, sesion, "parametros", "VER")
    filas = []
    texto = (filtro or "").strip().lower()
    for r in con.execute("SELECT * FROM parametros WHERE ejercicio_desde <= ? AND (ejercicio_hasta IS NULL OR "
                         "ejercicio_hasta >= ?) ORDER BY clave", (ejercicio, ejercicio)):
        marca = por_validar(r["fuente_documental"])
        if solo_por_validar and not marca:
            continue
        if texto and texto not in r["clave"].lower() and texto not in (r["descripcion"] or "").lower():
            continue
        vigencias = int(con.execute("SELECT COUNT(*) FROM parametros WHERE clave = ?", (r["clave"],)).fetchone()[0])
        pendiente = con.execute("SELECT id FROM solicitudes_parametro WHERE clave = ? AND estado = 'SOLICITADA'",
                                (r["clave"],)).fetchone()
        filas.append({**dict(r), "por_validar": marca, "vigencias": vigencias,
                      "solicitud_pendiente": int(pendiente[0]) if pendiente else None})
    return filas


def historial(con: sqlite3.Connection, sesion: Sesion, clave: str) -> dict[str, Any]:
    """Todas las vigencias de la clave, sus solicitudes de cambio y los
    eventos de la bitácora (valores antes y después)."""
    seguridad.exigir_permiso(con, sesion, "parametros", "VER")
    vigencias = [dict(r) for r in con.execute("SELECT * FROM parametros WHERE clave = ? ORDER BY ejercicio_desde",
                                              (clave,))]
    if not vigencias:
        raise DatosInvalidosError(f"No existe el parámetro {clave!r}.")
    ids = [str(v["id"]) for v in vigencias]
    solicitudes = [dict(r) for r in con.execute("SELECT * FROM solicitudes_parametro WHERE clave = ? ORDER BY id DESC",
                                                (clave,))]
    marcas = ",".join("?" * len(ids))
    eventos = [dict(r) for r in con.execute(
        f"SELECT * FROM auditoria WHERE (tabla = 'parametros' AND registro_id IN ({marcas})) OR "
        "(tabla = 'solicitudes_parametro' AND registro_id IN (SELECT CAST(id AS TEXT) FROM solicitudes_parametro "
        "WHERE clave = ?)) ORDER BY id DESC", (*ids, clave))]
    return {"clave": clave, "vigencias": vigencias, "solicitudes": solicitudes, "eventos": eventos}


def solicitar_cambio(con: sqlite3.Connection, sesion: Sesion, clave: str, valor_nuevo: Any, ejercicio_desde: int,
                     fuente_documental: str, motivo: str) -> dict[str, Any]:
    """La Gerencia General propone un valor nuevo desde un ejercicio."""
    seguridad.exigir_permiso(con, sesion, "parametros", "EDITAR")
    clave = _texto(clave, "la clave del parámetro", 100)
    fuente = _texto(fuente_documental, "la fuente documental")
    razon = _texto(motivo, "el motivo del cambio", 1000)
    try:
        desde = int(ejercicio_desde)
    except (TypeError, ValueError) as exc:
        raise DatosInvalidosError("El ejercicio desde el que rige debe ser un año (p. ej. 2027).") from exc
    ejercicio = con.execute("SELECT estado FROM ejercicios WHERE ejercicio = ?", (desde,)).fetchone()
    if ejercicio is None:
        raise DatosInvalidosError(f"El ejercicio {desde} no está registrado en el calendario.")
    if ejercicio["estado"] == "CERRADO":
        raise PeriodoCerradoError(f"El ejercicio {desde} está cerrado: sus parámetros ya no se modifican.",
                                  codigo="EJERCICIO_CERRADO")
    referencia = con.execute("SELECT tipo FROM parametros WHERE clave = ? ORDER BY ejercicio_desde DESC LIMIT 1",
                             (clave,)).fetchone()
    if referencia is None:
        raise DatosInvalidosError(f"No existe el parámetro {clave!r}. Los parámetros nuevos se crean con una "
                                  "migración documentada.")
    tipo = referencia["tipo"]
    texto = str(valor_nuevo).strip() if valor_nuevo is not None else ""
    if not texto:
        raise DatosInvalidosError("Indique el valor nuevo.")
    convertido = convertir_valor_parametro(texto, tipo, clave)
    if tipo == "DECIMAL":
        texto = str(convertido)
    actual = _vigente(con, clave, desde)
    if actual is not None and convertir_valor_parametro(actual["valor"], tipo, clave) == convertido:
        raise ReglaNegocioError(f"El valor propuesto es igual al vigente en {desde}.", codigo="SIN_CAMBIO")
    if con.execute("SELECT 1 FROM solicitudes_parametro WHERE clave = ? AND estado = 'SOLICITADA'", (clave,)).fetchone():
        raise ReglaNegocioError(f"Ya hay una solicitud pendiente para {clave}.", codigo="SOLICITUD_PENDIENTE")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO solicitudes_parametro (clave, valor_anterior, valor_nuevo, tipo, ejercicio_desde,
                   fuente_documental, motivo, solicitado_por, solicitado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (clave, actual["valor"] if actual else None, texto, tipo, desde, fuente, razon, sesion.nombre_usuario,
             _ahora_iso()))
        sid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "solicitudes_parametro", "INSERT", sid,
                            antes={"valor": actual["valor"] if actual else None},
                            despues={"clave": clave, "valor": texto, "ejercicio_desde": desde, "motivo": razon},
                            detalle=f"Propuesta de cambio de {clave}")
    return obtener_solicitud(con, sesion, sid)


def obtener_solicitud(con: sqlite3.Connection, sesion: Sesion, solicitud_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "parametros", "VER")
    fila = con.execute("SELECT * FROM solicitudes_parametro WHERE id = ?", (int(solicitud_id),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la solicitud {solicitud_id}.")
    return dict(fila)


def listar_solicitudes(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "parametros", "VER")
    sql, args = "SELECT * FROM solicitudes_parametro", []
    if estado:
        sql, args = sql + " WHERE estado = ?", [estado]
    return [dict(r) for r in con.execute(sql + " ORDER BY id DESC", args)]


def _aplicar(con: sqlite3.Connection, sesion: Sesion, sol: sqlite3.Row, acta: str) -> int:
    """Inserta la vigencia nueva cerrando la anterior. Devuelve el id vigente."""
    clave, desde = sol["clave"], int(sol["ejercicio_desde"])
    actual = _vigente(con, clave, desde)
    if actual is not None and int(actual["ejercicio_desde"]) == desde:
        con.execute("UPDATE parametros SET valor = ?, fuente_documental = ? WHERE id = ?",
                    (sol["valor_nuevo"], sol["fuente_documental"], int(actual["id"])))
        auditoria.registrar(con, sesion, "parametros", "UPDATE", int(actual["id"]),
                            antes={"clave": clave, "valor": actual["valor"], "fuente": actual["fuente_documental"]},
                            despues={"clave": clave, "valor": sol["valor_nuevo"], "fuente": sol["fuente_documental"]},
                            detalle=f"Cambio aprobado (solicitud {sol['id']}, {acta})")
        return int(actual["id"])
    if actual is not None:
        hasta_anterior = actual["ejercicio_hasta"]
        con.execute("UPDATE parametros SET ejercicio_hasta = ? WHERE id = ?", (desde - 1, int(actual["id"])))
        auditoria.registrar(con, sesion, "parametros", "UPDATE", int(actual["id"]),
                            antes={"clave": clave, "ejercicio_hasta": hasta_anterior},
                            despues={"clave": clave, "ejercicio_hasta": desde - 1},
                            detalle=f"Cierre de vigencia por la solicitud {sol['id']}")
        descripcion = actual["descripcion"]
    else:
        hasta_anterior = None
        siguiente = con.execute("SELECT MIN(ejercicio_desde) FROM parametros WHERE clave = ? AND ejercicio_desde > ?",
                                (clave, desde)).fetchone()[0]
        if siguiente is not None:
            hasta_anterior = int(siguiente) - 1
        descripcion = con.execute("SELECT descripcion FROM parametros WHERE clave = ? ORDER BY ejercicio_desde DESC "
                                  "LIMIT 1", (clave,)).fetchone()[0]
    cur = con.execute(
        """INSERT INTO parametros (clave, valor, tipo, ejercicio_desde, ejercicio_hasta, fuente_documental,
               descripcion) VALUES (?,?,?,?,?,?,?)""",
        (clave, sol["valor_nuevo"], sol["tipo"], desde, hasta_anterior, sol["fuente_documental"], descripcion))
    nuevo = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "parametros", "INSERT", nuevo, antes=None,
                        despues={"clave": clave, "valor": sol["valor_nuevo"], "ejercicio_desde": desde,
                                 "ejercicio_hasta": hasta_anterior, "fuente": sol["fuente_documental"]},
                        detalle=f"Vigencia nueva aprobada (solicitud {sol['id']})")
    return nuevo


def resolver_cambio(con: sqlite3.Connection, sesion: Sesion, solicitud_id: int, aprueba: bool,
                    acta_ref: str | None = None, observacion: str | None = None) -> str:
    """El CA o la AG aprueban (con acta) o rechazan (con observación)."""
    seguridad.exigir_permiso(con, sesion, "parametros", "APROBAR")
    if sesion.rol not in ("CA", "AG"):
        raise AutorizacionError("Los cambios de parámetros los aprueba el Consejo de Administración o la Asamblea.",
                                codigo="APROBADOR_INVALIDO")
    acta = acta_ref.strip() if isinstance(acta_ref, str) and acta_ref.strip() else None
    obs = observacion.strip() if isinstance(observacion, str) and observacion.strip() else None
    if aprueba and acta is None:
        raise DatosInvalidosError("La aprobación exige la referencia del acta del CA o de la AG.")
    if not aprueba and obs is None:
        raise DatosInvalidosError("El rechazo exige una observación.")
    with transaccion(con):
        sol = con.execute("SELECT * FROM solicitudes_parametro WHERE id = ?", (int(solicitud_id),)).fetchone()
        if sol is None:
            raise DatosInvalidosError(f"No existe la solicitud {solicitud_id}.")
        if sol["estado"] != "SOLICITADA":
            raise ReglaNegocioError(f"La solicitud ya está {sol['estado'].lower()}.", codigo="SOLICITUD_RESUELTA")
        cerrado = con.execute("SELECT estado FROM ejercicios WHERE ejercicio = ?", (sol["ejercicio_desde"],)).fetchone()
        if cerrado is not None and cerrado["estado"] == "CERRADO":
            raise PeriodoCerradoError(f"El ejercicio {sol['ejercicio_desde']} se cerró mientras la solicitud estaba "
                                      "pendiente.", codigo="EJERCICIO_CERRADO")
        estado = "APROBADA" if aprueba else "RECHAZADA"
        pid = _aplicar(con, sesion, sol, acta or "") if aprueba else None
        con.execute("UPDATE solicitudes_parametro SET estado = ?, resuelto_por = ?, resuelto_rol = ?, resuelto_en = ?, "
                    "acta_ref = ?, observacion = ?, parametro_id = ? WHERE id = ?",
                    (estado, sesion.nombre_usuario, sesion.rol, _ahora_iso(), acta, obs, pid, int(solicitud_id)))
        auditoria.registrar(con, sesion, "solicitudes_parametro", "AUTORIZACION", int(solicitud_id),
                            antes={"estado": "SOLICITADA"}, despues={"estado": estado, "acta": acta, "observacion": obs})
    return estado
