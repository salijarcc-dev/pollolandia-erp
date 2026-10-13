"""Respaldo automático 3-2-1 y prueba de restauración con acta (Fase 13).

Fuente: Manual de Archivo Contable MAN-ARC-01 §5.2 y §2.3.

* **Regla 3-2-1:** cada respaldo se guarda en 3 copias (equipo de trabajo,
  disco externo local y copia fuera de la granja o en la nube), en 2 tipos de
  medio, 1 de ellas fuera del sitio. Dada la conectividad limitada de la zona,
  la copia LOCAL es la principal: si un destino no está disponible, el respaldo
  se hace igual y el panel lo muestra en amarillo o rojo.
* **Frecuencia:** copia diaria (base de datos completa e **incremental** del
  archivo digital: solo los documentos nuevos o modificados), copia
  **completa semanal** y copias de **cierre mensual** (por cada período cerrado)
  y **anual** (por cada ejercicio cerrado). Las copias anuales se conservan el
  plazo del documento (``archivo_retencion_t10_anios``).
* **Prueba de restauración:** semestral (``respaldo_prueba_restauracion_meses``),
  con acta y verificación del Consejo de Vigilancia.
* **Responsables:** la Unidad de Finanzas mantiene los respaldos (JF), la
  Gerencia General configura los destinos y el CV verifica las actas.

Cada paquete ``.zip`` contiene la base de datos (copia consistente con la API
de respaldo de SQLite), los documentos del archivo digital y un
``manifiesto.json`` con la huella SHA-256 de cada archivo y los conteos de
control (asientos, sumas del Debe y el Haber, socios, parámetros, bitácora).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import config
from db.conexion import ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion
from servicios import auditoria, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion
from utils.formato import a_fecha, fecha_pantalla

USUARIO_SISTEMA = "SISTEMA"
UBICACIONES: tuple[str, ...] = ("LOCAL", "EXTERNO", "FUERA_SITIO")
NOMBRE_UBICACION = {"LOCAL": "Equipo de trabajo", "EXTERNO": "Disco externo local",
                    "FUERA_SITIO": "Fuera de la granja / nube"}
MEDIO = {"LOCAL": "DISCO_DEL_EQUIPO", "EXTERNO": "DISCO_EXTERNO", "FUERA_SITIO": "FUERA_DEL_SITIO"}
TIPOS: tuple[str, ...] = ("DIARIO", "SEMANAL", "MENSUAL", "ANUAL", "MANUAL")
TABLAS_CONTROL: tuple[str, ...] = ("asientos", "asiento_detalle", "socios", "usuarios", "parametros", "auditoria",
                                   "periodos", "cuentas")
NOMBRE_BD_PAQUETE = "bd/pollolandia.db"
NOMBRE_MANIFIESTO = "manifiesto.json"
VERDE, AMARILLO, ROJO = "VERDE", "AMARILLO", "ROJO"


# ==========================================================================
# UTILIDADES
# ==========================================================================
def _ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _param(con: sqlite3.Connection, fecha: date, clave: str) -> Any:
    ejercicio = ejercicio_de_fecha_bd(con, fecha)
    p = obtener_parametros_vigentes(con, ejercicio)
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro {clave!r} vigente para el ejercicio {ejercicio}.",
                                codigo="PARAMETRO_FALTANTE")
    return p[clave]


def sha256_archivo(ruta: Path) -> str:
    """Huella SHA-256 de un archivo (lectura por bloques)."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _usuario(sesion: Sesion | None) -> tuple[str, str | None]:
    return (sesion.nombre_usuario, sesion.rol) if sesion is not None else (USUARIO_SISTEMA, None)


def _registrar(con: sqlite3.Connection, sesion: Sesion | None, tabla: str, accion: str, registro_id: Any,
               antes: Any = None, despues: Any = None, detalle: str | None = None) -> None:
    usuario, rol = _usuario(sesion)
    auditoria.registrar_evento(con, usuario=usuario, rol=rol, tabla=tabla, accion=accion, registro_id=registro_id,
                               antes=antes, despues=despues, detalle=detalle)


# ==========================================================================
# 1. DESTINOS DE LA REGLA 3-2-1
# ==========================================================================
def _ruta_config() -> Path:
    return config.obtener_directorio_datos() / config.NOMBRE_CONFIG_RESPALDO


def destinos() -> dict[str, Path | None]:
    """Carpeta de cada copia. LOCAL siempre existe (``<datos>/respaldos``);
    EXTERNO y FUERA_SITIO salen de la variable de entorno o, si no hay, de
    ``respaldo_destinos.json``; ``None`` si no están configurados."""
    guardado: dict[str, str] = {}
    ruta = _ruta_config()
    if ruta.exists():
        try:
            guardado = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            guardado = {}
    resultado: dict[str, Path | None] = {"LOCAL": config.obtener_directorio_respaldos()}
    for ubicacion, variable in (("EXTERNO", config.ENV_RESPALDO_EXTERNO),
                                ("FUERA_SITIO", config.ENV_RESPALDO_FUERA_SITIO)):
        valor = os.environ.get(variable) or guardado.get(ubicacion)
        resultado[ubicacion] = Path(valor).expanduser().resolve() if valor else None
    return resultado


def guardar_destinos(con: sqlite3.Connection, sesion: Sesion, externo: str | None,
                     fuera_sitio: str | None) -> dict[str, Path | None]:
    """La Gerencia General configura el disco externo y la carpeta fuera del
    sitio. Cada destino debe ser una ruta absoluta distinta de la copia local
    y de la otra (si no, no serían copias independientes)."""
    seguridad.exigir_permiso(con, sesion, "respaldos", "AUTORIZAR")
    local = config.obtener_directorio_respaldos().resolve()
    nuevos: dict[str, str] = {}
    for ubicacion, valor in (("EXTERNO", externo), ("FUERA_SITIO", fuera_sitio)):
        if valor is None or not str(valor).strip():
            continue
        ruta = Path(str(valor).strip()).expanduser()
        if not ruta.is_absolute():
            raise DatosInvalidosError(f"El destino {NOMBRE_UBICACION[ubicacion]} debe ser una ruta absoluta "
                                      "(por ejemplo E:\\Respaldos o D:\\OneDrive\\Pollolandia).")
        ruta = ruta.resolve()
        if ruta == local or local in ruta.parents or ruta in local.parents:
            raise ReglaNegocioError(f"El destino {NOMBRE_UBICACION[ubicacion]} no puede estar dentro de la copia "
                                    "local: la regla 3-2-1 exige copias independientes.", codigo="DESTINO_NO_INDEPENDIENTE")
        nuevos[ubicacion] = str(ruta)
    if len(set(nuevos.values())) < len(nuevos):
        raise ReglaNegocioError("El disco externo y la copia fuera del sitio deben ser carpetas distintas.",
                                codigo="DESTINO_NO_INDEPENDIENTE")
    anteriores = {k: (str(v) if v else None) for k, v in destinos().items() if k != "LOCAL"}
    with transaccion(con):
        ruta = _ruta_config()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(nuevos, ensure_ascii=False, indent=2), encoding="utf-8")
        _registrar(con, sesion, "respaldos", "UPDATE", "destinos", antes=anteriores, despues=nuevos,
                   detalle="Configuración de destinos de la regla 3-2-1 (MAN-ARC-01 §5.2)")
    return destinos()


# ==========================================================================
# 2. CONTEOS DE CONTROL
# ==========================================================================
def conteos_control(con: sqlite3.Connection) -> dict[str, Any]:
    """Conteos y sumas que el acta de restauración compara (no dependen del
    orden físico de las filas)."""
    conteos: dict[str, Any] = {}
    for tabla in TABLAS_CONTROL:
        conteos[tabla] = int(con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0])
    debe, haber = con.execute("SELECT COALESCE(SUM(debe_cent), 0), COALESCE(SUM(haber_cent), 0) "
                              "FROM asiento_detalle").fetchone()
    conteos["debe_cent"], conteos["haber_cent"] = int(debe), int(haber)
    conteos["version_esquema"] = int(con.execute("SELECT COALESCE(MAX(version), 0) FROM esquema_version").fetchone()[0])
    conteos["ultimo_asiento"] = int(con.execute("SELECT COALESCE(MAX(id), 0) FROM asientos").fetchone()[0])
    return conteos


# ==========================================================================
# 3. CREACIÓN DE RESPALDOS
# ==========================================================================
def _referencia(tipo: str, fecha: date, referencia: str | None) -> str:
    if tipo == "DIARIO":
        return fecha.isoformat()
    if tipo == "SEMANAL":
        anio, semana, _ = fecha.isocalendar()
        return f"{anio}-W{semana:02d}"
    if tipo == "MANUAL":
        return referencia or datetime.now().strftime("%Y-%m-%dT%H%M%S")
    if not referencia:
        raise DatosInvalidosError(f"El respaldo {tipo.lower()} exige la referencia del período o ejercicio cerrado.")
    return referencia


def _documentos(desde: datetime | None) -> list[Path]:
    """Documentos del archivo digital; con ``desde`` solo los modificados
    después de esa fecha y hora (copia incremental)."""
    raiz = config.obtener_directorio_archivo()
    if not raiz.exists():
        return []
    limite = desde.timestamp() if desde else None
    return sorted(p for p in raiz.rglob("*") if p.is_file()
                  and (limite is None or p.stat().st_mtime > limite))


def _copiar_bd(con: sqlite3.Connection, destino: Path) -> None:
    """Copia consistente de la base con la API de respaldo de SQLite. Se lee
    con una conexión nueva al mismo archivo, de modo que el respaldo contiene
    solo lo confirmado (nunca el trabajo a medias de una transacción abierta)."""
    fila = con.execute("PRAGMA database_list").fetchone()
    archivo = fila[2] if fila is not None else ""
    fuente = sqlite3.connect(archivo) if archivo else con
    copia = sqlite3.connect(destino)
    try:
        fuente.backup(copia)
    finally:
        copia.close()
        if fuente is not con:
            fuente.close()


def _armar_paquete(con: sqlite3.Connection, carpeta: Path, nombre: str, tipo: str, fecha: date, referencia: str,
                   incremental: bool, desde: datetime | None) -> tuple[Path, dict[str, Any], int]:
    bd = carpeta / "pollolandia.db"
    _copiar_bd(con, bd)
    copia = sqlite3.connect(bd)
    try:
        conteos = conteos_control(copia)
    finally:
        copia.close()
    documentos = _documentos(desde if incremental else None)
    raiz = config.obtener_directorio_archivo()
    archivos = {NOMBRE_BD_PAQUETE: sha256_archivo(bd)}
    paquete = carpeta / nombre
    with zipfile.ZipFile(paquete, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(bd, NOMBRE_BD_PAQUETE)
        for doc in documentos:
            interno = "archivo/" + doc.relative_to(raiz).as_posix()
            z.write(doc, interno)
            archivos[interno] = sha256_archivo(doc)
        manifiesto = {"sistema": config.NOMBRE_SISTEMA, "entidad": config.NOMBRE_ENTIDAD, "version": config.VERSION,
                      "tipo": tipo, "fecha": fecha.isoformat(), "referencia": referencia, "incremental": incremental,
                      "documentos_desde": desde.isoformat() if (incremental and desde) else None,
                      "conteos": conteos, "archivos": archivos, "creado_en": _ahora_iso()}
        z.writestr(NOMBRE_MANIFIESTO, json.dumps(manifiesto, ensure_ascii=False, indent=2))
    return paquete, conteos, len(documentos)


def crear_respaldo(con: sqlite3.Connection, sesion: Sesion | None, tipo: str, fecha: Any = None,
                   referencia: str | None = None) -> dict[str, Any]:
    """Crea un respaldo y lo copia a los destinos configurados.

    Args:
        sesion: usuario que lo ordena (JF o GG); ``None`` para el respaldo
            automático (se registra como ``SISTEMA``).
        tipo: ``DIARIO`` (incremental del archivo digital), ``SEMANAL``,
            ``MENSUAL`` o ``ANUAL`` (completos) o ``MANUAL``.
        referencia: período (``AAAA-MM``) para el mensual y ejercicio
            (``E2026``) para el anual.

    Returns:
        ``{id, tipo, referencia, archivo, sha256, copias, ubicaciones, ...}``.
    """
    if sesion is not None:
        seguridad.exigir_permiso(con, sesion, "respaldos", "CREAR")
    if tipo not in TIPOS:
        raise DatosInvalidosError(f"Tipo de respaldo no válido: {tipo!r}. Use {', '.join(TIPOS)}.")
    f = a_fecha(fecha) if fecha is not None else date.today()
    ref = _referencia(tipo, f, referencia)
    if con.execute("SELECT 1 FROM respaldos WHERE tipo = ? AND referencia = ?", (tipo, ref)).fetchone():
        raise ReglaNegocioError(f"Ya existe el respaldo {tipo.lower()} {ref}.", codigo="RESPALDO_DUPLICADO")
    incremental = tipo == "DIARIO"
    # creado_en guarda el instante (con microsegundos) previo al armado del
    # paquete: la copia incremental siguiente toma lo modificado después.
    ultimo = con.execute("SELECT MAX(creado_en) FROM respaldos").fetchone()[0]
    desde = datetime.fromisoformat(ultimo) if ultimo else None
    momento = datetime.now()
    marca = momento.strftime("%Y%m%dT%H%M%S")
    nombre = f"POLLOLANDIA_{tipo}_{ref.replace(':', '')}_{marca}.zip"
    ubicaciones: dict[str, str | None] = {u: None for u in UBICACIONES}
    with tempfile.TemporaryDirectory(prefix="pollolandia_respaldo_") as tmp:
        paquete, conteos, n_docs = _armar_paquete(con, Path(tmp), nombre, tipo, f, ref, incremental, desde)
        huella = sha256_archivo(paquete)
        tamano = paquete.stat().st_size
        errores: dict[str, str] = {}
        for ubicacion, carpeta in destinos().items():
            if carpeta is None:
                continue
            try:
                destino = carpeta / tipo.lower()
                destino.mkdir(parents=True, exist_ok=True)
                final = destino / nombre
                shutil.copy2(paquete, final)
                if sha256_archivo(final) != huella:
                    raise OSError("la copia no coincide con la huella SHA-256")
                ubicaciones[ubicacion] = str(final)
            except OSError as exc:
                errores[ubicacion] = str(exc)
    if ubicaciones["LOCAL"] is None:
        raise ReglaNegocioError(f"No se pudo escribir la copia local del respaldo: {errores.get('LOCAL')}",
                                codigo="RESPALDO_SIN_COPIA_LOCAL")
    copias = sum(1 for v in ubicaciones.values() if v)
    usuario, _ = _usuario(sesion)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO respaldos (tipo, fecha, referencia, archivo, sha256, bytes, version_esquema, documentos,
                   incremental, conteos_json, ubicaciones_json, copias, creado_por, creado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tipo, f.isoformat(), ref, nombre, huella, tamano, int(conteos["version_esquema"]), n_docs,
             int(incremental), json.dumps(conteos, sort_keys=True), json.dumps(ubicaciones, sort_keys=True), copias,
             usuario, momento.isoformat(timespec="microseconds")))
        rid = int(cur.lastrowid)
        resultado = {"id": rid, "tipo": tipo, "referencia": ref, "archivo": nombre, "sha256": huella, "bytes": tamano,
                     "documentos": n_docs, "incremental": incremental, "copias": copias, "ubicaciones": ubicaciones,
                     "errores": errores, "conteos": conteos}
        _registrar(con, sesion, "respaldos", "INSERT", rid, despues={k: v for k, v in resultado.items()
                                                                      if k != "conteos"},
                   detalle=f"Respaldo {tipo.lower()} {ref} en {copias} de 3 copias (regla 3-2-1)")
    return resultado


# ==========================================================================
# 4. RESPALDO AUTOMÁTICO Y DEPURACIÓN
# ==========================================================================
def respaldos_pendientes(con: sqlite3.Connection, fecha: Any = None) -> list[tuple[str, str | None]]:
    """``[(tipo, referencia)]`` que corresponden a la fecha: diario de hoy,
    completo semanal (el día fijado o si pasó una semana sin él), mensual por
    cada período cerrado y anual por cada ejercicio cerrado sin respaldo."""
    f = a_fecha(fecha) if fecha is not None else date.today()
    pendientes: list[tuple[str, str | None]] = []
    existe = lambda t, r: con.execute("SELECT 1 FROM respaldos WHERE tipo = ? AND referencia = ?",  # noqa: E731
                                      (t, r)).fetchone() is not None
    if not existe("DIARIO", f.isoformat()):
        pendientes.append(("DIARIO", None))
    dia = int(_param(con, f, "respaldo_dia_semanal"))
    ultimo_semanal = con.execute("SELECT MAX(fecha) FROM respaldos WHERE tipo = 'SEMANAL'").fetchone()[0]
    semana = _referencia("SEMANAL", f, None)
    vencido = ultimo_semanal is None or (f - date.fromisoformat(ultimo_semanal)).days >= 7
    if not existe("SEMANAL", semana) and (f.isoweekday() == dia or vencido):
        pendientes.append(("SEMANAL", None))
    for p in con.execute("SELECT fecha_inicio FROM periodos WHERE estado = 'CERRADO' ORDER BY fecha_inicio"):
        ref = str(p[0])[:7]
        if not existe("MENSUAL", ref):
            pendientes.append(("MENSUAL", ref))
    for e in con.execute("SELECT ejercicio FROM ejercicios WHERE estado = 'CERRADO' ORDER BY ejercicio"):
        ref = f"E{int(e[0])}"
        if not existe("ANUAL", ref):
            pendientes.append(("ANUAL", ref))
    return pendientes


def ejecutar_automatico(con: sqlite3.Connection, fecha: Any = None) -> dict[str, Any]:
    """Hace los respaldos pendientes del día y depura los vencidos. Lo llama
    la aplicación al iniciar y la tarea programada de Windows
    (``respaldo_automatico.bat``). Un error en un respaldo no detiene los demás."""
    f = a_fecha(fecha) if fecha is not None else date.today()
    hechos, errores = [], []
    for tipo, ref in respaldos_pendientes(con, f):
        try:
            hechos.append(crear_respaldo(con, None, tipo, f, ref))
        except (ReglaNegocioError, DatosInvalidosError, OSError, sqlite3.Error) as exc:
            errores.append({"tipo": tipo, "referencia": ref, "error": str(exc)})
    depurados = depurar(con, f)
    return {"fecha": f.isoformat(), "respaldos": hechos, "errores": errores, "depurados": depurados}


def _vencido(con: sqlite3.Connection, fila: sqlite3.Row, f: date) -> bool:
    creado = date.fromisoformat(fila["fecha"])
    if fila["tipo"] == "DIARIO":
        return (f - creado).days > int(_param(con, f, "respaldo_retencion_diaria_dias"))
    if fila["tipo"] == "SEMANAL":
        return (f - creado).days > 7 * int(_param(con, f, "respaldo_retencion_semanal_semanas"))
    if fila["tipo"] == "MENSUAL":
        meses = (f.year - creado.year) * 12 + f.month - creado.month
        return meses > int(_param(con, f, "respaldo_retencion_mensual_meses"))
    if fila["tipo"] == "ANUAL":
        return f.year - creado.year > int(_param(con, f, "archivo_retencion_t10_anios"))
    return False                                          # los manuales no se depuran solos


def depurar(con: sqlite3.Connection, fecha: Any = None) -> list[int]:
    """Borra los paquetes vencidos de los tres destinos y marca el registro
    como DEPURADO (el registro y su huella se conservan)."""
    f = a_fecha(fecha) if fecha is not None else date.today()
    depurados = []
    for fila in con.execute("SELECT * FROM respaldos WHERE estado = 'VIGENTE' ORDER BY id").fetchall():
        if not _vencido(con, fila, f):
            continue
        for ruta in json.loads(fila["ubicaciones_json"]).values():
            if ruta and Path(ruta).exists():
                Path(ruta).unlink()
        with transaccion(con):
            con.execute("UPDATE respaldos SET estado = 'DEPURADO', depurado_en = ? WHERE id = ?",
                        (_ahora_iso(), int(fila["id"])))
            _registrar(con, None, "respaldos", "UPDATE", int(fila["id"]), antes={"estado": "VIGENTE"},
                       despues={"estado": "DEPURADO"}, detalle="Depuración por plazo de conservación vencido")
        depurados.append(int(fila["id"]))
    return depurados


# ==========================================================================
# 5. PRUEBA DE RESTAURACIÓN Y ACTA
# ==========================================================================
def _verificar_paquete(ruta: Path, fila: Mapping[str, Any] | None, carpeta: Path) -> tuple[list[dict[str, Any]], Path | None]:
    """Verificaciones del acta sobre un paquete; devuelve la lista y la ruta de
    la base restaurada en ``carpeta`` (o ``None`` si no se pudo extraer)."""
    v: list[dict[str, Any]] = []

    def chk(control: str, cumple: bool, detalle: str = "") -> bool:
        v.append({"control": control, "cumple": bool(cumple), "detalle": detalle})
        return bool(cumple)

    if not chk("El paquete existe en la ubicación", ruta.exists(), str(ruta)):
        return v, None
    huella = sha256_archivo(ruta)
    if fila is not None:
        chk("La huella SHA-256 coincide con la registrada", huella == fila["sha256"], huella)
    try:
        with zipfile.ZipFile(ruta) as z:
            corrupto = z.testzip()
            if not chk("El archivo comprimido está íntegro", corrupto is None, corrupto or "sin errores"):
                return v, None
            nombres = z.namelist()
            if not chk("Contiene la base de datos y el manifiesto",
                       NOMBRE_BD_PAQUETE in nombres and NOMBRE_MANIFIESTO in nombres):
                return v, None
            manifiesto = json.loads(z.read(NOMBRE_MANIFIESTO).decode("utf-8"))
            z.extractall(carpeta)
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError) as exc:
        chk("El archivo comprimido está íntegro", False, str(exc))
        return v, None
    distintos = [n for n, h in manifiesto["archivos"].items() if sha256_archivo(carpeta / n) != h]
    chk("Cada archivo coincide con su huella del manifiesto", not distintos,
        f"{len(manifiesto['archivos'])} archivo(s)" + (f"; distintos: {', '.join(distintos)}" if distintos else ""))
    documentos = sum(1 for n in manifiesto["archivos"] if n.startswith("archivo/"))
    chk("Documentos del archivo digital restaurados", True, f"{documentos} documento(s)")
    bd = carpeta / NOMBRE_BD_PAQUETE
    con = sqlite3.connect(bd)
    try:
        integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
        chk("PRAGMA integrity_check", integridad == "ok", integridad)
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        chk("PRAGMA foreign_key_check sin huérfanos", not fk, f"{len(fk)} referencia(s) huérfana(s)")
        conteos = conteos_control(con)
        if fila is not None:
            chk("Versión de esquema igual a la registrada", conteos["version_esquema"] == fila["version_esquema"],
                str(conteos["version_esquema"]))
        diferencias = {k: (manifiesto["conteos"].get(k), conteos.get(k)) for k in manifiesto["conteos"]
                       if manifiesto["conteos"].get(k) != conteos.get(k)}
        chk("Conteos de control iguales a los del respaldo", not diferencias,
            "; ".join(f"{k}: {a} → {b}" for k, (a, b) in diferencias.items()) or
            f"{conteos['asientos']} asientos; {conteos['auditoria']} eventos de bitácora")
        descuadres = con.execute("SELECT COUNT(*) FROM (SELECT asiento_id FROM asiento_detalle GROUP BY asiento_id "
                                 "HAVING SUM(debe_cent) <> SUM(haber_cent))").fetchone()[0]
        chk("Todos los asientos restaurados cuadran (partida doble)", descuadres == 0,
            f"{descuadres} asiento(s) descuadrado(s)")
        chk("Debe total = Haber total", conteos["debe_cent"] == conteos["haber_cent"],
            f"Bs {conteos['debe_cent'] / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    finally:
        con.close()
    return v, bd


def probar_restauracion(con: sqlite3.Connection, sesion: Sesion, respaldo_id: int, ubicacion: str = "LOCAL",
                        fecha: Any = None) -> dict[str, Any]:
    """Restaura el respaldo en una carpeta temporal (sin tocar la base en uso),
    ejecuta las verificaciones y registra el acta. El Consejo de Vigilancia la
    verifica después (``verificar_acta``)."""
    seguridad.exigir_permiso(con, sesion, "respaldos", "CREAR")
    if ubicacion not in UBICACIONES:
        raise DatosInvalidosError(f"Ubicación no válida: {ubicacion!r}.")
    fila = con.execute("SELECT * FROM respaldos WHERE id = ?", (int(respaldo_id),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el respaldo {respaldo_id}.")
    if fila["estado"] != "VIGENTE":
        raise ReglaNegocioError("El respaldo fue depurado: elija uno vigente.", codigo="RESPALDO_DEPURADO")
    ruta = json.loads(fila["ubicaciones_json"]).get(ubicacion)
    if not ruta:
        raise ReglaNegocioError(f"El respaldo no tiene copia en {NOMBRE_UBICACION[ubicacion]}.",
                                codigo="SIN_COPIA_EN_UBICACION")
    f = a_fecha(fecha) if fecha is not None else date.today()
    inicio = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pollolandia_restauracion_") as tmp:
        verificaciones, _ = _verificar_paquete(Path(ruta), fila, Path(tmp))
    duracion = int((time.perf_counter() - inicio) * 1000)
    resultado = "CONFORME" if verificaciones and all(x["cumple"] for x in verificaciones) else "FALLIDA"
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO actas_restauracion (respaldo_id, fecha, ubicacion, resultado, verificaciones_json,
                   duracion_ms, elaborado_por, usuario_id, registrado_en) VALUES (?,?,?,?,?,?,?,?,?)""",
            (int(respaldo_id), f.isoformat(), ubicacion, resultado, json.dumps(verificaciones, ensure_ascii=False),
             duracion, sesion.nombre_completo, sesion.usuario_id, _ahora_iso()))
        aid = int(cur.lastrowid)
        _registrar(con, sesion, "actas_restauracion", "INSERT", aid,
                   despues={"respaldo_id": int(respaldo_id), "ubicacion": ubicacion, "resultado": resultado,
                            "controles": len(verificaciones)},
                   detalle=f"Prueba de restauración del respaldo {fila['tipo'].lower()} {fila['referencia']}")
    return obtener_acta(con, sesion, aid)


def verificar_acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int, observacion: str | None = None) -> None:
    """Visto del Consejo de Vigilancia sobre el acta de restauración
    (MAN-ARC-01 §5.2). Un acta FALLIDA exige observación."""
    seguridad.exigir_permiso(con, sesion, "respaldos", "VERIFICAR")
    if sesion.rol != "CV":
        raise AutorizacionError("El acta de restauración la verifica un miembro del Consejo de Vigilancia.",
                                codigo="SOLO_CV")
    fila = con.execute("SELECT * FROM actas_restauracion WHERE id = ?", (int(acta_id),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el acta {acta_id}.")
    if fila["verificado_por"]:
        raise ReglaNegocioError("El acta ya fue verificada por el Consejo de Vigilancia.", codigo="ACTA_VERIFICADA")
    obs = observacion.strip() if isinstance(observacion, str) and observacion.strip() else None
    if fila["resultado"] == "FALLIDA" and obs is None:
        raise DatosInvalidosError("Un acta con restauración fallida exige la observación del Consejo de Vigilancia.")
    with transaccion(con):
        con.execute("UPDATE actas_restauracion SET verificado_por = ?, verificado_en = ?, observacion_cv = ? "
                    "WHERE id = ?", (sesion.nombre_completo, _ahora_iso(), obs, int(acta_id)))
        _registrar(con, sesion, "actas_restauracion", "UPDATE", int(acta_id), antes={"verificado_por": None},
                   despues={"verificado_por": sesion.nombre_completo, "observacion": obs})


def obtener_acta(con: sqlite3.Connection, sesion: Sesion, acta_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "respaldos", "VER")
    fila = con.execute("SELECT a.*, r.tipo, r.referencia, r.archivo, r.sha256, r.fecha AS fecha_respaldo, r.copias "
                       "FROM actas_restauracion a JOIN respaldos r ON r.id = a.respaldo_id WHERE a.id = ?",
                       (int(acta_id),)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el acta {acta_id}.")
    return {**dict(fila), "verificaciones": json.loads(fila["verificaciones_json"])}


def listar_actas(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "respaldos", "VER")
    return [obtener_acta(con, sesion, int(r[0])) for r in
            con.execute("SELECT id FROM actas_restauracion ORDER BY fecha DESC, id DESC").fetchall()]


def listar_respaldos(con: sqlite3.Connection, sesion: Sesion, tipo: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "respaldos", "VER")
    sql, args = "SELECT * FROM respaldos", []
    if tipo:
        sql, args = sql + " WHERE tipo = ?", [tipo]
    return [{**dict(r), "ubicaciones": json.loads(r["ubicaciones_json"]), "conteos": json.loads(r["conteos_json"])}
            for r in con.execute(sql + " ORDER BY creado_en DESC, id DESC", args)]


# ==========================================================================
# 6. ESTADO DE CUMPLIMIENTO 3-2-1
# ==========================================================================
def estado_321(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> dict[str, Any]:
    """Panel de cumplimiento: destinos, copias del último respaldo, frecuencia
    y prueba de restauración semestral con visto del CV."""
    seguridad.exigir_permiso(con, sesion, "respaldos", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    minimas = int(_param(con, f, "respaldo_copias_minimas"))
    fuera = int(_param(con, f, "respaldo_fuera_sitio_minimas"))
    medios = int(_param(con, f, "respaldo_medios_minimos"))
    meses = int(_param(con, f, "respaldo_prueba_restauracion_meses"))
    d = destinos()
    configurados = [u for u in UBICACIONES if d.get(u)]
    controles = []

    def control(nombre: str, color: str, detalle: str) -> None:
        controles.append({"control": nombre, "semaforo": color, "detalle": detalle})

    control("Destinos configurados (3 copias)", VERDE if len(configurados) >= minimas else
            (AMARILLO if len(configurados) == minimas - 1 else ROJO),
            ", ".join(f"{NOMBRE_UBICACION[u]}: {d[u] or 'sin configurar'}" for u in UBICACIONES))
    n_medios = len({MEDIO[u] for u in configurados})
    control("Tipos de medio (2)", VERDE if n_medios >= medios else ROJO, f"{n_medios} medio(s) distinto(s)")
    control("Copia fuera del sitio (1)", VERDE if sum(1 for u in configurados if u == "FUERA_SITIO") >= fuera else ROJO,
            str(d.get("FUERA_SITIO") or "sin configurar"))
    ultimo = con.execute("SELECT * FROM respaldos WHERE estado = 'VIGENTE' ORDER BY creado_en DESC, id DESC "
                         "LIMIT 1").fetchone()
    if ultimo is None:
        control("Último respaldo", ROJO, "No hay respaldos registrados")
    else:
        color = VERDE if int(ultimo["copias"]) >= minimas else (AMARILLO if int(ultimo["copias"]) == minimas - 1 else ROJO)
        control("Copias del último respaldo", color,
                f"{ultimo['tipo'].capitalize()} {ultimo['referencia']}: {ultimo['copias']} de {minimas} copias")
    diario = con.execute("SELECT MAX(fecha) FROM respaldos WHERE tipo = 'DIARIO'").fetchone()[0]
    control("Copia diaria", VERDE if diario == f.isoformat() else (
        AMARILLO if diario and (f - date.fromisoformat(diario)).days <= 1 else ROJO),
        f"Última: {fecha_pantalla(diario) if diario else 'ninguna'}")
    semanal = con.execute("SELECT MAX(fecha) FROM respaldos WHERE tipo = 'SEMANAL'").fetchone()[0]
    control("Copia completa semanal", VERDE if semanal and (f - date.fromisoformat(semanal)).days < 7 else ROJO,
            f"Última: {fecha_pantalla(semanal) if semanal else 'ninguna'}")
    sin_mensual = [r for t, r in respaldos_pendientes(con, f) if t in ("MENSUAL", "ANUAL")]
    control("Copias de cierre mensual y anual", VERDE if not sin_mensual else ROJO,
            "Al día" if not sin_mensual else f"Pendientes: {', '.join(str(x) for x in sin_mensual)}")
    acta = con.execute("SELECT * FROM actas_restauracion WHERE resultado = 'CONFORME' ORDER BY fecha DESC LIMIT 1"
                       ).fetchone()
    if acta is None:
        control("Prueba de restauración semestral", ROJO, "Sin actas de restauración conformes")
    else:
        fa = date.fromisoformat(acta["fecha"])
        transcurridos = (f.year - fa.year) * 12 + f.month - fa.month - (1 if f.day < fa.day else 0)
        color = ROJO if transcurridos >= meses else (VERDE if acta["verificado_por"] else AMARILLO)
        control("Prueba de restauración semestral", color,
                f"Última conforme: {fecha_pantalla(acta['fecha'])}" +
                (f", verificada por {acta['verificado_por']}" if acta["verificado_por"] else ", pendiente del visto del CV"))
    peor = ROJO if any(c["semaforo"] == ROJO for c in controles) else (
        AMARILLO if any(c["semaforo"] == AMARILLO for c in controles) else VERDE)
    return {"fecha": f.isoformat(), "destinos": {k: (str(v) if v else None) for k, v in d.items()},
            "controles": controles, "semaforo": peor}


# ==========================================================================
# 7. RESTAURACIÓN DE LA BASE EN USO (solo desde la línea de comandos)
# ==========================================================================
def restaurar_produccion(ruta_paquete: Path, ruta_bd: Path) -> dict[str, Any]:
    """Reemplaza la base en uso por la del paquete, después de verificarlo y
    de guardar la base actual como ``*.antes_de_restaurar_<marca>.db``.

    Se usa con la aplicación CERRADA (``scripts/restaurar_respaldo.py``). Deja
    un evento en la bitácora de la base restaurada.
    """
    ruta_paquete, ruta_bd = Path(ruta_paquete), Path(ruta_bd)
    with tempfile.TemporaryDirectory(prefix="pollolandia_restaurar_") as tmp:
        verificaciones, bd = _verificar_paquete(ruta_paquete, None, Path(tmp))
        if bd is None or not all(x["cumple"] for x in verificaciones):
            fallas = "; ".join(x["control"] for x in verificaciones if not x["cumple"])
            raise ReglaNegocioError(f"El respaldo no supera las verificaciones: {fallas}.", codigo="RESPALDO_INVALIDO")
        marca = datetime.now().strftime("%Y%m%dT%H%M%S")
        previa = None
        if ruta_bd.exists():
            previa = ruta_bd.with_name(f"{ruta_bd.stem}.antes_de_restaurar_{marca}{ruta_bd.suffix}")
            origen = sqlite3.connect(ruta_bd)
            copia = sqlite3.connect(previa)
            try:
                origen.backup(copia)
            finally:
                copia.close()
                origen.close()
            for sufijo in ("-wal", "-shm"):
                extra = ruta_bd.with_name(ruta_bd.name + sufijo)
                if extra.exists():
                    extra.unlink()
        shutil.copy2(bd, ruta_bd)
    con = sqlite3.connect(ruta_bd)
    try:
        con.execute("INSERT INTO auditoria (usuario, rol, tabla, registro_id, accion, valores_antes, valores_despues, "
                    "detalle) VALUES (?, NULL, 'respaldos', NULL, 'OTRO', NULL, ?, ?)",
                    (USUARIO_SISTEMA, json.dumps({"paquete": ruta_paquete.name, "previa": str(previa) if previa else None},
                                                 ensure_ascii=False),
                     "Restauración de la base en uso desde un respaldo verificado"))
        con.commit()
    finally:
        con.close()
    return {"verificaciones": verificaciones, "copia_previa": str(previa) if previa else None,
            "restaurada": str(ruta_bd)}


def ultimo_respaldo_automatico(con: sqlite3.Connection) -> str | None:
    """Fecha del último respaldo diario (lo usa la pantalla de inicio)."""
    return con.execute("SELECT MAX(fecha) FROM respaldos WHERE tipo = 'DIARIO'").fetchone()[0]


def dias_desde(fecha_iso: str | None, hoy: date | None = None) -> int | None:
    if not fecha_iso:
        return None
    return ((hoy or date.today()) - date.fromisoformat(fecha_iso)).days


def proxima_prueba(con: sqlite3.Connection, fecha: Any = None) -> date | None:
    """Fecha límite de la próxima prueba de restauración (6 meses desde la
    última conforme)."""
    f = a_fecha(fecha) if fecha is not None else date.today()
    ultima = con.execute("SELECT MAX(fecha) FROM actas_restauracion WHERE resultado = 'CONFORME'").fetchone()[0]
    if not ultima:
        return None
    meses = int(_param(con, f, "respaldo_prueba_restauracion_meses"))
    u = date.fromisoformat(ultima)
    mes = u.month - 1 + meses
    anio, mes = u.year + mes // 12, mes % 12 + 1
    dia = min(u.day, [31, 29 if anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0) else 28, 31, 30, 31, 30, 31,
                      31, 30, 31, 30, 31][mes - 1])
    return date(anio, mes, dia)
