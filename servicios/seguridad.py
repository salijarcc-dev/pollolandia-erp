"""Seguridad del ERP Pollolandia (Fase 2).

Contenido:

1. Autenticación con bcrypt: inicio y cierre de sesión, bloqueo por intentos,
   caducidad de contraseña (MAN-ARC-01 §5.2), cambio y restablecimiento.
2. Administración de usuarios (solo Gerencia General).
3. Permisos por rol (tabla ``permisos``) con segregación de funciones
   (MOF §3.4, CI-FIN-10): el JF no autoriza ni firma desembolsos, la GG no
   registra transacciones contables, la UT no registra ni autoriza pagos y el
   CV es solo lectura (salvo sus informes y la cofirma de desembolsos).
4. ``requiere_autorizacion``: Matriz de Autorización CI-FIN-09 (Anexo 10),
   fila por fila, con los umbrales leídos de ``parametros``.
5. Solicitudes, firmas y verificación de autorizaciones, incluida la firma
   mancomunada (POL-04 Art. 5) y la suplencia de la Gerencia General
   (CI-FIN-17: Presidente del CA y, en su defecto, Tesorero del CA, con
   constancia escrita obligatoria, comunicación en 24 h y revisión posterior).

Lógica pura: este módulo no importa ``streamlit``.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

import bcrypt

import config
from db.conexion import (
    a_centavos, de_centavos, ejercicio_de_fecha_bd, ejercicio_vigente_o_ultimo,
    obtener_parametros_vigentes, transaccion,
)
from servicios import auditoria
from servicios.excepciones import (
    AutorizacionError, DatosInvalidosError, ReglaNegocioError,
)
from utils.formato import a_fecha, formato_bs, redondear_bs

# ==========================================================================
# CONSTANTES ESTRUCTURALES (no normativas: los montos están en ``parametros``)
# ==========================================================================
ROLES_VALIDOS: tuple[str, ...] = ("GG", "JF", "UT", "CA", "CV", "AG")
ACCIONES_REGISTRO: frozenset[str] = frozenset({"CREAR", "EDITAR", "REVERSAR"})
ACCIONES_DECISION: frozenset[str] = frozenset({"AUTORIZAR", "APROBAR", "COFIRMAR"})
RECURSOS_SOLO_LECTURA_EXCEPTUADOS: frozenset[str] = frozenset({"vigilancia"})

FORMATO_FECHA_HORA = "%Y-%m-%dT%H:%M:%S"
MENSAJE_CREDENCIALES = "Usuario o contraseña incorrectos."
PATRON_USUARIO = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
BCRYPT_MAX_BYTES = 72


@dataclass(frozen=True)
class Incompatibilidad:
    """Combinación rol/acción prohibida por la segregación de funciones.

    ``recursos`` vacío significa "todos los recursos"; ``excepto`` lista los
    recursos a los que no se aplica.
    """

    rol: str
    acciones: frozenset[str]
    recursos: frozenset[str]
    excepto: frozenset[str]
    motivo: str

    def aplica(self, rol: str, recurso: str, accion: str) -> bool:
        if rol != self.rol or accion not in self.acciones or recurso in self.excepto:
            return False
        return not self.recursos or recurso in self.recursos


# Segregación de funciones (MOF-FIN-001 §2 y §3.4; CI-FIN-10). Se evalúa además
# de la tabla ``permisos``: aunque alguien agregue un permiso indebido, la
# incompatibilidad lo sigue bloqueando.
INCOMPATIBILIDADES: tuple[Incompatibilidad, ...] = (
    Incompatibilidad("JF", ACCIONES_DECISION, frozenset(), frozenset(),
                     "El Jefe de Finanzas no autoriza, aprueba ni firma desembolsos (MOF §3.4; CI-FIN-10)."),
    Incompatibilidad("GG", ACCIONES_REGISTRO, frozenset({"contabilidad"}), frozenset(),
                     "La Gerencia General no registra transacciones contables (MOF §3.4)."),
    Incompatibilidad("UT", ACCIONES_REGISTRO | ACCIONES_DECISION, frozenset({"contabilidad", "tesoreria"}),
                     frozenset(), "La Unidad Técnica no registra contablemente ni autoriza pagos (MOF §2)."),
    Incompatibilidad("UT", ACCIONES_DECISION, frozenset(), frozenset(),
                     "La Unidad Técnica no autoriza ni aprueba operaciones (MOF §2)."),
    Incompatibilidad("CV", ACCIONES_REGISTRO | frozenset({"AUTORIZAR", "APROBAR"}), frozenset(),
                     RECURSOS_SOLO_LECTURA_EXCEPTUADOS,
                     "El Consejo de Vigilancia fiscaliza sin ejecutar, aprobar ni registrar (MOF §3.4)."),
)


# ==========================================================================
# UTILITARIOS
# ==========================================================================
def _ahora(ahora: datetime | None = None) -> datetime:
    """Fecha y hora local sin microsegundos (inyectable en pruebas)."""
    return (ahora or datetime.now()).replace(microsecond=0)


def _iso(momento: datetime) -> str:
    return momento.strftime(FORMATO_FECHA_HORA)


def _de_iso(texto: str) -> datetime:
    return datetime.fromisoformat(texto)


def _texto_obligatorio(valor: Any, campo: str, maximo: int = 500) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"El campo '{campo}' es obligatorio.")
    texto = valor.strip()
    if len(texto) > maximo:
        raise DatosInvalidosError(f"El campo '{campo}' admite como máximo {maximo} caracteres.")
    return texto


def _texto_opcional(valor: Any, campo: str, maximo: int = 500) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    return _texto_obligatorio(valor, campo, maximo)


def _param(parametros: Mapping[str, Any], clave: str) -> Any:
    if clave not in parametros:
        raise ReglaNegocioError(
            f"Falta el parámetro '{clave}' vigente para el ejercicio.",
            codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave},
        )
    return parametros[clave]


def _parametros_seguridad(con: sqlite3.Connection, ahora: datetime) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_vigente_o_ultimo(con, ahora.date()))


# ==========================================================================
# 1. CONTRASEÑAS (bcrypt)
# ==========================================================================
def generar_hash(password: str) -> str:
    """Devuelve el hash bcrypt (texto) de una contraseña."""
    if not isinstance(password, str) or not password:
        raise DatosInvalidosError("La contraseña no puede estar vacía.")
    datos = password.encode("utf-8")
    if len(datos) > BCRYPT_MAX_BYTES:
        raise DatosInvalidosError(f"La contraseña no puede superar {BCRYPT_MAX_BYTES} bytes.")
    return bcrypt.hashpw(datos, bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)).decode("ascii")


def verificar_hash(password: str, hash_guardado: str) -> bool:
    """``True`` si la contraseña corresponde al hash. Nunca lanza excepciones."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hash_guardado.encode("ascii"))
    except (ValueError, TypeError, AttributeError):
        return False


def validar_politica_password(
    password: str, parametros: Mapping[str, Any], nombre_usuario: str | None = None
) -> None:
    """Aplica la política de contraseñas leída de ``parametros``.

    Raises:
        DatosInvalidosError: con el motivo concreto del rechazo.
    """
    if not isinstance(password, str):
        raise DatosInvalidosError("La contraseña debe ser texto.")
    minimo = int(_param(parametros, "password_longitud_min"))
    if len(password) < minimo:
        raise DatosInvalidosError(f"La contraseña debe tener al menos {minimo} caracteres.")
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise DatosInvalidosError(f"La contraseña no puede superar {BCRYPT_MAX_BYTES} bytes.")
    if bool(_param(parametros, "password_requiere_letra_y_numero")):
        if not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", password) or not re.search(r"\d", password):
            raise DatosInvalidosError("La contraseña debe combinar letras y números.")
    if nombre_usuario and password.strip().lower() == nombre_usuario.strip().lower():
        raise DatosInvalidosError("La contraseña no puede ser igual al nombre de usuario.")


# ==========================================================================
# 2. SESIÓN
# ==========================================================================
@dataclass(frozen=True)
class Sesion:
    """Identidad del usuario autenticado que viaja a cada servicio."""

    usuario_id: int
    nombre_usuario: str
    nombre_completo: str
    rol: str
    socio_id: int | None
    cargo: str | None
    inicio: str
    debe_cambiar_password: bool = False


def _fila_usuario(con: sqlite3.Connection, *, nombre: str | None = None, uid: int | None = None) -> sqlite3.Row | None:
    sql = """SELECT u.*, s.cargo_cooperativo AS cargo
               FROM usuarios u LEFT JOIN socios s ON s.id = u.socio_id
              WHERE """ + ("u.nombre_usuario = ?" if nombre is not None else "u.id = ?")
    return con.execute(sql, (nombre if nombre is not None else uid,)).fetchone()


def hay_usuarios(con: sqlite3.Connection) -> bool:
    """``True`` si ya existe al menos un usuario (sistema inicializado)."""
    return bool(con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0])


def iniciar_sesion(
    con: sqlite3.Connection, nombre_usuario: str, password: str, ahora: datetime | None = None
) -> Sesion:
    """Autentica con bcrypt y devuelve la :class:`Sesion`.

    * Tras ``login_intentos_max`` fallos consecutivos el usuario queda
      bloqueado ``login_bloqueo_minutos``.
    * Si la contraseña superó ``password_caducidad_dias`` (MAN-ARC-01: 90 días)
      la sesión se abre con ``debe_cambiar_password=True``.
    * Todo intento (exitoso o no) queda en la bitácora.

    Raises:
        DatosInvalidosError: si faltan usuario o contraseña.
        AutorizacionError: credenciales inválidas (``CREDENCIALES_INVALIDAS``),
            usuario bloqueado (``USUARIO_BLOQUEADO``) o inactivo (``USUARIO_INACTIVO``).
    """
    momento = _ahora(ahora)
    nombre = _texto_obligatorio(nombre_usuario, "usuario", 30)
    if not isinstance(password, str) or not password:
        raise DatosInvalidosError("Ingrese la contraseña.")
    params = _parametros_seguridad(con, momento)
    fila = _fila_usuario(con, nombre=nombre)

    def fallo(detalle: str, registro: Any = None, rol: str | None = None) -> None:
        with transaccion(con):
            auditoria.registrar_evento(con, usuario=nombre, rol=rol, tabla="usuarios", accion="LOGIN",
                                       registro_id=registro, detalle=detalle)

    if fila is None:
        fallo("Intento fallido: usuario inexistente")
        raise AutorizacionError(MENSAJE_CREDENCIALES, codigo="CREDENCIALES_INVALIDAS")
    if not fila["activo"]:
        fallo("Intento rechazado: usuario desactivado", fila["id"], fila["rol"])
        raise AutorizacionError("El usuario está desactivado. Consulte a la Gerencia General.",
                                codigo="USUARIO_INACTIVO")
    bloqueo = _de_iso(fila["bloqueado_hasta"]) if fila["bloqueado_hasta"] else None
    if bloqueo and momento < bloqueo:
        fallo("Intento rechazado: usuario bloqueado", fila["id"], fila["rol"])
        raise AutorizacionError(
            f"El usuario está bloqueado hasta las {bloqueo.strftime('%H:%M')} del "
            f"{bloqueo.strftime('%d/%m/%Y')} por intentos fallidos.",
            codigo="USUARIO_BLOQUEADO", detalle={"bloqueado_hasta": _iso(bloqueo)},
        )

    if not verificar_hash(password, fila["password_hash"]):
        maximo = int(_param(params, "login_intentos_max"))
        base = 0 if bloqueo else int(fila["intentos_fallidos"])
        intentos = base + 1
        nuevo_bloqueo = None
        if intentos >= maximo:
            nuevo_bloqueo = _iso(momento + timedelta(minutes=int(_param(params, "login_bloqueo_minutos"))))
        with transaccion(con):
            con.execute("UPDATE usuarios SET intentos_fallidos = ?, bloqueado_hasta = ? WHERE id = ?",
                        (intentos, nuevo_bloqueo, fila["id"]))
            auditoria.registrar_evento(
                con, usuario=nombre, rol=fila["rol"], tabla="usuarios", accion="LOGIN",
                registro_id=fila["id"],
                antes={"intentos_fallidos": fila["intentos_fallidos"], "bloqueado_hasta": fila["bloqueado_hasta"]},
                despues={"intentos_fallidos": intentos, "bloqueado_hasta": nuevo_bloqueo},
                detalle=f"Intento fallido {intentos}/{maximo}" + (" - usuario bloqueado" if nuevo_bloqueo else ""),
            )
        if nuevo_bloqueo:
            raise AutorizacionError(
                f"{MENSAJE_CREDENCIALES} Se superaron {maximo} intentos: el usuario quedó bloqueado "
                f"{int(_param(params, 'login_bloqueo_minutos'))} minutos.",
                codigo="USUARIO_BLOQUEADO", detalle={"bloqueado_hasta": nuevo_bloqueo},
            )
        raise AutorizacionError(MENSAJE_CREDENCIALES, codigo="CREDENCIALES_INVALIDAS",
                                detalle={"intentos": intentos, "maximo": maximo})

    caducidad = int(_param(params, "password_caducidad_dias"))
    caducada = _de_iso(fila["ultimo_cambio_password"]) + timedelta(days=caducidad) <= momento
    debe_cambiar = bool(fila["debe_cambiar_password"]) or caducada
    with transaccion(con):
        con.execute("UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?",
                    (fila["id"],))
        auditoria.registrar_evento(
            con, usuario=fila["nombre_usuario"], rol=fila["rol"], tabla="usuarios", accion="LOGIN",
            registro_id=fila["id"],
            detalle="Inicio de sesión" + (" (contraseña caducada: debe cambiarla)" if caducada else ""),
        )
    return Sesion(
        usuario_id=int(fila["id"]), nombre_usuario=fila["nombre_usuario"],
        nombre_completo=fila["nombre_completo"], rol=fila["rol"], socio_id=fila["socio_id"],
        cargo=fila["cargo"], inicio=_iso(momento), debe_cambiar_password=debe_cambiar,
    )


def cerrar_sesion(con: sqlite3.Connection, sesion: Sesion, motivo: str = "Cierre de sesión") -> None:
    """Registra el cierre de sesión en la bitácora."""
    with transaccion(con):
        auditoria.registrar(con, sesion, "usuarios", "LOGOUT", sesion.usuario_id, detalle=motivo)


def sesion_vencida(ultimo_acceso: datetime, minutos: int, ahora: datetime | None = None) -> bool:
    """``True`` si pasaron más de ``minutos`` desde el último acceso."""
    if isinstance(minutos, bool) or not isinstance(minutos, int) or minutos <= 0:
        raise DatosInvalidosError("Los minutos de inactividad deben ser un entero positivo.")
    return _ahora(ahora) - ultimo_acceso > timedelta(minutes=minutos)


def minutos_inactividad(con: sqlite3.Connection, ahora: datetime | None = None) -> int:
    """Minutos de inactividad permitidos (parámetro ``sesion_inactividad_minutos``)."""
    return int(_param(_parametros_seguridad(con, _ahora(ahora)), "sesion_inactividad_minutos"))


def cambiar_password(
    con: sqlite3.Connection, sesion: Sesion, actual: str, nueva: str, ahora: datetime | None = None
) -> Sesion:
    """Cambia la contraseña del propio usuario y devuelve la sesión actualizada.

    Raises:
        AutorizacionError: si la contraseña actual no es correcta.
        DatosInvalidosError: si la nueva no cumple la política o repite la actual.
    """
    momento = _ahora(ahora)
    fila = _fila_usuario(con, uid=sesion.usuario_id)
    if fila is None or not fila["activo"]:
        raise AutorizacionError("La sesión ya no es válida.", codigo="SESION_INVALIDA")
    if not verificar_hash(actual or "", fila["password_hash"]):
        raise AutorizacionError("La contraseña actual no es correcta.", codigo="CREDENCIALES_INVALIDAS")
    if nueva == actual:
        raise DatosInvalidosError("La nueva contraseña debe ser distinta de la actual.")
    validar_politica_password(nueva, _parametros_seguridad(con, momento), fila["nombre_usuario"])
    with transaccion(con):
        con.execute(
            """UPDATE usuarios SET password_hash = ?, ultimo_cambio_password = ?,
                                   debe_cambiar_password = 0 WHERE id = ?""",
            (generar_hash(nueva), _iso(momento), sesion.usuario_id))
        auditoria.registrar(con, sesion, "usuarios", "UPDATE", sesion.usuario_id,
                            antes={"password": "(anterior)"},
                            despues={"password": "(cambiada)", "ultimo_cambio_password": _iso(momento)},
                            detalle="Cambio de contraseña por el propio usuario")
    return Sesion(**{**sesion.__dict__, "debe_cambiar_password": False})


# ==========================================================================
# 3. PERMISOS Y SEGREGACIÓN DE FUNCIONES
# ==========================================================================
def incompatibilidad(rol: str, recurso: str, accion: str) -> str | None:
    """Motivo de la incompatibilidad CI-FIN-10 aplicable, o ``None``."""
    for regla in INCOMPATIBILIDADES:
        if regla.aplica(rol, recurso, accion):
            return regla.motivo
    return None


def motivo_denegacion(con: sqlite3.Connection, rol: str, recurso: str, accion: str) -> tuple[str, str] | None:
    """``(codigo, mensaje)`` si el rol no puede realizar la acción; ``None`` si puede."""
    motivo = incompatibilidad(rol, recurso, accion)
    if motivo:
        return "SEGREGACION_FUNCIONES", motivo
    fila = con.execute("SELECT solo_lectura_transaccional FROM roles WHERE codigo = ?", (rol,)).fetchone()
    if fila is None:
        return "ROL_INEXISTENTE", f"El rol {rol!r} no existe."
    if fila[0] and accion in ACCIONES_REGISTRO and recurso not in RECURSOS_SOLO_LECTURA_EXCEPTUADOS:
        return "SOLO_LECTURA", f"El rol {rol} es de solo lectura transaccional."
    existe = con.execute("SELECT 1 FROM permisos WHERE rol = ? AND recurso = ? AND accion = ?",
                         (rol, recurso, accion)).fetchone()
    if not existe:
        return "SIN_PERMISO", f"El rol {rol} no tiene permiso para {accion} en {recurso}."
    return None


def tiene_permiso(con: sqlite3.Connection, rol: str, recurso: str, accion: str) -> bool:
    """``True`` si la tabla ``permisos`` lo concede y ninguna regla de
    segregación ni de solo lectura lo impide."""
    return motivo_denegacion(con, rol, recurso, accion) is None


def exigir_permiso(con: sqlite3.Connection, sesion: Sesion, recurso: str, accion: str) -> None:
    """Valida que la sesión siga vigente y que su rol pueda realizar la acción.

    Los rechazos se registran en la bitácora cuando no hay una transacción en
    curso (si la hubiera, el rechazo la revierte y no tendría sentido guardarlo).

    Raises:
        AutorizacionError: ``SESION_INVALIDA``, ``SEGREGACION_FUNCIONES``,
            ``SOLO_LECTURA``, ``SIN_PERMISO`` o ``ROL_INEXISTENTE``.
    """
    fila = con.execute("SELECT activo, rol FROM usuarios WHERE id = ?", (sesion.usuario_id,)).fetchone()
    if fila is None or not fila["activo"] or fila["rol"] != sesion.rol:
        raise AutorizacionError("La sesión ya no es válida: vuelva a ingresar.", codigo="SESION_INVALIDA")
    denegado = motivo_denegacion(con, sesion.rol, recurso, accion)
    if denegado is None:
        return
    codigo, mensaje = denegado
    if not con.in_transaction:
        with transaccion(con):
            auditoria.registrar(con, sesion, recurso, "OTRO", detalle=f"Acceso denegado ({accion}): {mensaje}")
    raise AutorizacionError(mensaje, codigo=codigo, detalle={"recurso": recurso, "accion": accion})


def permisos_de_rol(con: sqlite3.Connection, rol: str) -> dict[str, frozenset[str]]:
    """Permisos efectivos del rol (descontadas incompatibilidades), por recurso."""
    resultado: dict[str, set[str]] = {}
    for f in con.execute("SELECT recurso, accion FROM permisos WHERE rol = ?", (rol,)):
        if tiene_permiso(con, rol, f["recurso"], f["accion"]):
            resultado.setdefault(f["recurso"], set()).add(f["accion"])
    return {k: frozenset(v) for k, v in resultado.items()}


# ==========================================================================
# 4. ADMINISTRACIÓN DE USUARIOS
# ==========================================================================
def _validar_nombre_usuario(nombre: str) -> str:
    if not isinstance(nombre, str) or not PATRON_USUARIO.fullmatch(nombre.strip()):
        raise DatosInvalidosError(
            "El usuario debe tener de 3 a 30 caracteres: letras, números, punto, guion o guion bajo.")
    return nombre.strip()


def _validar_socio(con: sqlite3.Connection, socio_id: int | None, excluir_usuario: int | None = None) -> None:
    if socio_id is None:
        return
    if isinstance(socio_id, bool) or not isinstance(socio_id, int):
        raise DatosInvalidosError("El socio debe indicarse por su identificador numérico.")
    if con.execute("SELECT 1 FROM socios WHERE id = ?", (socio_id,)).fetchone() is None:
        raise DatosInvalidosError(f"No existe el socio con identificador {socio_id}.")
    otro = con.execute("SELECT nombre_usuario FROM usuarios WHERE socio_id = ? AND activo = 1 AND id <> ?",
                       (socio_id, excluir_usuario or 0)).fetchone()
    if otro:
        raise ReglaNegocioError(f"El socio ya tiene el usuario activo '{otro[0]}'.", codigo="SOCIO_CON_USUARIO")


def _insertar_usuario(con: sqlite3.Connection, nombre: str, completo: str, rol: str, socio_id: int | None,
                      password: str, debe_cambiar: bool, momento: datetime) -> int:
    try:
        cur = con.execute(
            """INSERT INTO usuarios (nombre_usuario, nombre_completo, rol, socio_id, password_hash,
                                     ultimo_cambio_password, debe_cambiar_password, creado_en)
               VALUES (?,?,?,?,?,?,?,?)""",
            (nombre, completo, rol, socio_id, generar_hash(password), _iso(momento),
             1 if debe_cambiar else 0, _iso(momento)))
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            raise DatosInvalidosError(f"El usuario '{nombre}' ya existe.") from exc
        raise
    return int(cur.lastrowid)


def crear_usuario_inicial(
    con: sqlite3.Connection, nombre_usuario: str, nombre_completo: str, password: str,
    socio_id: int | None = None, ahora: datetime | None = None,
) -> int:
    """Crea el primer usuario del sistema con rol Gerencia General.

    Solo funciona con la tabla ``usuarios`` vacía; después, los usuarios los
    crea la Gerencia General con :func:`crear_usuario`.

    Raises:
        ReglaNegocioError: (``SISTEMA_YA_INICIALIZADO``) si ya hay usuarios.
    """
    momento = _ahora(ahora)
    nombre = _validar_nombre_usuario(nombre_usuario)
    completo = _texto_obligatorio(nombre_completo, "nombre completo", 120)
    validar_politica_password(password, _parametros_seguridad(con, momento), nombre)
    with transaccion(con):
        if hay_usuarios(con):
            raise ReglaNegocioError("El sistema ya tiene usuarios: el alta la hace la Gerencia General.",
                                    codigo="SISTEMA_YA_INICIALIZADO")
        _validar_socio(con, socio_id)
        uid = _insertar_usuario(con, nombre, completo, "GG", socio_id, password, False, momento)
        auditoria.registrar_evento(
            con, usuario=auditoria.USUARIO_SISTEMA, rol=None, tabla="usuarios", accion="INSERT",
            registro_id=uid, despues={"nombre_usuario": nombre, "nombre_completo": completo, "rol": "GG",
                                      "socio_id": socio_id},
            detalle="Usuario inicial de la Gerencia General")
    return uid


def crear_usuario(
    con: sqlite3.Connection, sesion: Sesion, nombre_usuario: str, nombre_completo: str, rol: str,
    password_temporal: str, socio_id: int | None = None, ahora: datetime | None = None,
) -> int:
    """Alta de usuario por la Gerencia General. El usuario deberá cambiar la
    contraseña temporal en su primer ingreso."""
    exigir_permiso(con, sesion, "usuarios", "CREAR")
    momento = _ahora(ahora)
    nombre = _validar_nombre_usuario(nombre_usuario)
    completo = _texto_obligatorio(nombre_completo, "nombre completo", 120)
    if rol not in ROLES_VALIDOS:
        raise DatosInvalidosError(f"Rol no válido: {rol!r}. Use uno de {', '.join(ROLES_VALIDOS)}.")
    validar_politica_password(password_temporal, _parametros_seguridad(con, momento), nombre)
    with transaccion(con):
        _validar_socio(con, socio_id)
        uid = _insertar_usuario(con, nombre, completo, rol, socio_id, password_temporal, True, momento)
        auditoria.registrar(con, sesion, "usuarios", "INSERT", uid,
                            despues={"nombre_usuario": nombre, "nombre_completo": completo, "rol": rol,
                                     "socio_id": socio_id, "debe_cambiar_password": True})
    return uid


def actualizar_usuario(
    con: sqlite3.Connection, sesion: Sesion, usuario_id: int, *, rol: str | None = None,
    activo: bool | None = None, nombre_completo: str | None = None, socio_id: int | None = None,
) -> None:
    """Modifica rol, estado, nombre o socio de un usuario (Gerencia General).

    Raises:
        ReglaNegocioError: si el usuario intenta desactivarse o cambiar su propio rol.
    """
    exigir_permiso(con, sesion, "usuarios", "EDITAR")
    with transaccion(con):
        fila = _fila_usuario(con, uid=usuario_id)
        if fila is None:
            raise DatosInvalidosError(f"No existe el usuario {usuario_id}.")
        if usuario_id == sesion.usuario_id and ((activo is False) or (rol is not None and rol != fila["rol"])):
            raise ReglaNegocioError("Nadie puede desactivarse ni cambiar su propio rol.", codigo="AUTOGESTION")
        cambios: dict[str, Any] = {}
        if rol is not None:
            if rol not in ROLES_VALIDOS:
                raise DatosInvalidosError(f"Rol no válido: {rol!r}.")
            cambios["rol"] = rol
        if activo is not None:
            cambios["activo"] = 1 if activo else 0
        if nombre_completo is not None:
            cambios["nombre_completo"] = _texto_obligatorio(nombre_completo, "nombre completo", 120)
        if socio_id is not None:
            _validar_socio(con, socio_id, excluir_usuario=usuario_id)
            cambios["socio_id"] = socio_id
        if not cambios:
            raise DatosInvalidosError("No se indicó ningún cambio.")
        antes = {k: fila[k] for k in cambios}
        con.execute(f"UPDATE usuarios SET {', '.join(f'{k} = ?' for k in cambios)} WHERE id = ?",
                    (*cambios.values(), usuario_id))
        auditoria.registrar(con, sesion, "usuarios", "UPDATE", usuario_id, antes=antes, despues=cambios)


def restablecer_password(
    con: sqlite3.Connection, sesion: Sesion, usuario_id: int, password_temporal: str,
    ahora: datetime | None = None,
) -> None:
    """La Gerencia General asigna una contraseña temporal y desbloquea al usuario."""
    exigir_permiso(con, sesion, "usuarios", "EDITAR")
    momento = _ahora(ahora)
    fila = _fila_usuario(con, uid=usuario_id)
    if fila is None:
        raise DatosInvalidosError(f"No existe el usuario {usuario_id}.")
    validar_politica_password(password_temporal, _parametros_seguridad(con, momento), fila["nombre_usuario"])
    with transaccion(con):
        con.execute(
            """UPDATE usuarios SET password_hash = ?, ultimo_cambio_password = ?, debe_cambiar_password = 1,
                                   intentos_fallidos = 0, bloqueado_hasta = NULL WHERE id = ?""",
            (generar_hash(password_temporal), _iso(momento), usuario_id))
        auditoria.registrar(con, sesion, "usuarios", "UPDATE", usuario_id,
                            antes={"bloqueado_hasta": fila["bloqueado_hasta"],
                                   "intentos_fallidos": fila["intentos_fallidos"]},
                            despues={"password": "(temporal)", "debe_cambiar_password": True,
                                     "bloqueado_hasta": None, "intentos_fallidos": 0},
                            detalle="Restablecimiento de contraseña por la Gerencia General")


def listar_usuarios(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Usuarios sin el hash de contraseña (Gerencia General)."""
    exigir_permiso(con, sesion, "usuarios", "VER")
    filas = con.execute(
        """SELECT u.id, u.nombre_usuario, u.nombre_completo, u.rol, u.socio_id, s.cargo_cooperativo AS cargo,
                  u.activo, u.intentos_fallidos, u.bloqueado_hasta, u.ultimo_cambio_password,
                  u.debe_cambiar_password, u.creado_en
             FROM usuarios u LEFT JOIN socios s ON s.id = u.socio_id ORDER BY u.id""").fetchall()
    return [dict(f) for f in filas]


def listar_socios(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Socios (para vincularlos a usuarios)."""
    return [dict(f) for f in con.execute(
        "SELECT id, numero, nombre_completo, cargo_cooperativo, estado FROM socios ORDER BY numero")]


# ==========================================================================
# 5. MATRIZ DE AUTORIZACIÓN CI-FIN-09
# ==========================================================================
class TipoDecision(str, Enum):
    """Filas de la Matriz de Autorización CI-FIN-09 (Anexo 10, Capítulo II)."""

    CAPEX_NO_PREVISTO = "CAPEX_NO_PREVISTO"
    VARIACION_CAPEX = "VARIACION_CAPEX"
    FINANCIAMIENTO_DEUDA_CAPEX = "FINANCIAMIENTO_DEUDA_CAPEX"
    DESEMBOLSO = "DESEMBOLSO"
    COMPROMISO_GASTO = "COMPROMISO_GASTO"
    COMPRA_INSUMOS = "COMPRA_INSUMOS"
    CONTRATACION_EVENTUAL = "CONTRATACION_EVENTUAL"
    CREDITO_HORECA = "CREDITO_HORECA"
    CANAL_RESPALDO = "CANAL_RESPALDO"
    DISTRIBUCION_EXCEDENTES = "DISTRIBUCION_EXCEDENTES"
    CAPITALIZACION_EXCEDENTES = "CAPITALIZACION_EXCEDENTES"
    PRESUPUESTO_CERTIFICACIONES = "PRESUPUESTO_CERTIFICACIONES"
    NUEVA_ACTIVIDAD_RAU = "NUEVA_ACTIVIDAD_RAU"
    AJUSTE_CONTABLE = "AJUSTE_CONTABLE"
    OTRA = "OTRA"


DESCRIPCION_DECISION: dict[TipoDecision, str] = {
    TipoDecision.CAPEX_NO_PREVISTO: "Inversión CAPEX no contemplada en el Plan anual",
    TipoDecision.VARIACION_CAPEX: "Variación sobre el monto aprobado de un activo CAPEX",
    TipoDecision.FINANCIAMIENTO_DEUDA_CAPEX: "Financiamiento de CAPEX mediante deuda bancaria",
    TipoDecision.DESEMBOLSO: "Desembolso bancario",
    TipoDecision.COMPROMISO_GASTO: "Compromiso de gasto no recurrente",
    TipoDecision.COMPRA_INSUMOS: "Compra de insumos (orden de compra)",
    TipoDecision.CONTRATACION_EVENTUAL: "Contratación de personal eventual para faenado",
    TipoDecision.CREDITO_HORECA: "Crédito a cliente HORECA superior a 1 día",
    TipoDecision.CANAL_RESPALDO: "Activación de canal de venta al contado de respaldo",
    TipoDecision.DISTRIBUCION_EXCEDENTES: "Distribución de excedentes cooperativos",
    TipoDecision.CAPITALIZACION_EXCEDENTES: "Capitalización de excedentes",
    TipoDecision.PRESUPUESTO_CERTIFICACIONES: "Presupuesto anual de certificaciones SENASAG/IBNORCA",
    TipoDecision.NUEVA_ACTIVIDAD_RAU: "Nueva actividad con eventual impacto en el RAU",
    TipoDecision.AJUSTE_CONTABLE: "Asiento de ajuste o reversión contable",
    TipoDecision.OTRA: "Decisión no prevista en la Matriz (escalamiento por defecto)",
}

# Recurso de la tabla ``permisos`` sobre el que se ejerce cada decisión.
RECURSO_DECISION: dict[TipoDecision, str] = {
    TipoDecision.CAPEX_NO_PREVISTO: "activos",
    TipoDecision.VARIACION_CAPEX: "activos",
    TipoDecision.FINANCIAMIENTO_DEUDA_CAPEX: "activos",
    TipoDecision.DESEMBOLSO: "tesoreria",
    TipoDecision.COMPROMISO_GASTO: "tesoreria",
    TipoDecision.COMPRA_INSUMOS: "compras",
    TipoDecision.CONTRATACION_EVENTUAL: "planillas",
    TipoDecision.CREDITO_HORECA: "ventas",
    TipoDecision.CANAL_RESPALDO: "ventas",
    TipoDecision.DISTRIBUCION_EXCEDENTES: "contabilidad",
    TipoDecision.CAPITALIZACION_EXCEDENTES: "contabilidad",
    TipoDecision.PRESUPUESTO_CERTIFICACIONES: "presupuesto",
    TipoDecision.NUEVA_ACTIVIDAD_RAU: "rau",
    TipoDecision.AJUSTE_CONTABLE: "contabilidad",
    TipoDecision.OTRA: "tesoreria",
}

NOMBRE_CALIDAD: dict[str, str] = {
    "GG": "Gerencia General", "CV": "Consejo de Vigilancia",
    "CA": "Consejo de Administración", "AG": "Asamblea General",
}


def accion_de_calidad(calidad: str, recurso: str) -> tuple[str, str]:
    """``(recurso, acción)`` que debe tener quien firma en esa calidad."""
    if calidad == "GG":
        return recurso, "AUTORIZAR"
    if calidad == "CV":
        return "tesoreria", "COFIRMAR"
    return recurso, "APROBAR"


@dataclass(frozen=True)
class RequisitoAutorizacion:
    """Resultado de aplicar la Matriz CI-FIN-09 a una decisión concreta."""

    tipo_decision: str
    monto: Decimal
    firmas: tuple[str, ...] = ()
    autorizacion_escrita: bool = False
    autorizacion_especifica: bool = False
    cotizaciones_minimas: int = 0
    mayoria_calificada: tuple[int, int] | None = None
    dia_semana: int | None = None
    documentos_previos: tuple[str, ...] = ()
    informar_a: tuple[str, ...] = ()
    fundamentos: tuple[str, ...] = ()

    @property
    def requiere_firma(self) -> bool:
        return bool(self.firmas)

    @property
    def firma_mancomunada(self) -> bool:
        return "GG" in self.firmas and "CV" in self.firmas

    def a_dict(self) -> dict[str, Any]:
        return {
            "tipo_decision": self.tipo_decision, "monto": str(self.monto), "firmas": list(self.firmas),
            "autorizacion_escrita": self.autorizacion_escrita,
            "autorizacion_especifica": self.autorizacion_especifica,
            "cotizaciones_minimas": self.cotizaciones_minimas,
            "mayoria_calificada": list(self.mayoria_calificada) if self.mayoria_calificada else None,
            "dia_semana": self.dia_semana, "documentos_previos": list(self.documentos_previos),
            "informar_a": list(self.informar_a), "fundamentos": list(self.fundamentos),
        }

    @classmethod
    def desde_dict(cls, d: Mapping[str, Any]) -> "RequisitoAutorizacion":
        return cls(
            tipo_decision=d["tipo_decision"], monto=Decimal(d["monto"]), firmas=tuple(d["firmas"]),
            autorizacion_escrita=bool(d["autorizacion_escrita"]),
            autorizacion_especifica=bool(d["autorizacion_especifica"]),
            cotizaciones_minimas=int(d["cotizaciones_minimas"]),
            mayoria_calificada=tuple(d["mayoria_calificada"]) if d.get("mayoria_calificada") else None,
            dia_semana=d.get("dia_semana"), documentos_previos=tuple(d.get("documentos_previos") or ()),
            informar_a=tuple(d.get("informar_a") or ()), fundamentos=tuple(d.get("fundamentos") or ()),
        )


@dataclass(frozen=True)
class ContextoDecision:
    """Datos del caso que la Matriz necesita para decidir.

    ``parametros`` son los vigentes del ejercicio (ver :func:`contexto_desde_bd`).
    """

    ejercicio: int
    parametros: Mapping[str, Any]
    fecha: date | None = None
    saldo_proyectado: Decimal | None = None
    previsto_en_plan: bool | None = None
    monto_aprobado: Decimal | None = None
    dias_credito: int | None = None
    monto_entrega_semanal: Decimal | None = None
    cxc_total_proyectada: Decimal | None = None
    ingresos_ciclo: Decimal | None = None


def contexto_desde_bd(con: sqlite3.Connection, fecha: Any, **datos: Any) -> ContextoDecision:
    """Arma el contexto con el ejercicio de la fecha y sus parámetros vigentes."""
    f = a_fecha(fecha)
    ejercicio = ejercicio_de_fecha_bd(con, f)
    return ContextoDecision(ejercicio=ejercicio, parametros=obtener_parametros_vigentes(con, ejercicio),
                            fecha=f, **datos)


def _pct(fraccion: Decimal) -> str:
    valor = (Decimal(fraccion) * 100).normalize()
    return f"{valor:f}".replace(".", ",") + " %"


def _evaluar_sme(p: Mapping[str, Any], saldo_proyectado: Decimal) -> str | None:
    """Fundamento si el saldo proyectado queda bajo el umbral del SME, o ``None``."""
    sme = max(Decimal(_param(p, "sme")), Decimal(_param(p, "sme_piso_absoluto")))
    factor = Decimal(_param(p, "sme_umbral_compromiso"))
    umbral = redondear_bs(sme * factor)
    saldo = redondear_bs(saldo_proyectado)
    if saldo < umbral:
        return (f"POL-04 Art. 2: el saldo proyectado ({formato_bs(saldo)}) queda bajo el {_pct(factor)} del "
                f"SME ({formato_bs(umbral)}) → autorización expresa y escrita de la Gerencia General")
    return None


def requiere_autorizacion(
    tipo_decision: TipoDecision | str, monto: Any, contexto: ContextoDecision
) -> RequisitoAutorizacion:
    """Aplica la Matriz de Autorización CI-FIN-09 (Anexo 10, Capítulo II).

    Los umbrales salen de ``contexto.parametros`` (regla 2). Las comparaciones
    son estrictas ("mayor a"), como en la matriz: un desembolso de Bs 1.000,00
    no requiere a la GG y uno de Bs 1.000,01 sí.

    Args:
        tipo_decision: fila de la matriz (:class:`TipoDecision` o su texto).
        monto: importe en Bs (``Decimal``, ``int``, ``float`` o texto).
        contexto: datos del caso (saldo proyectado, plan CAPEX, etc.).

    Raises:
        DatosInvalidosError: tipo desconocido, monto negativo o falta un dato
            del contexto que la fila necesita.
        ReglaNegocioError: la decisión no está permitida (p. ej. crédito HORECA
            con más días que el máximo vigente).
    """
    try:
        tipo = TipoDecision(tipo_decision)
    except ValueError as exc:
        raise DatosInvalidosError(f"Tipo de decisión desconocido: {tipo_decision!r}.") from exc
    importe = redondear_bs(monto)
    if importe < 0:
        raise DatosInvalidosError("El monto de la decisión no puede ser negativo.")
    p = contexto.parametros
    firmas: list[str] = []
    fundamentos: list[str] = []
    escrita = especifica = False
    cotizaciones = 0
    mayoria: tuple[int, int] | None = None
    dia: int | None = None
    documentos: tuple[str, ...] = ()
    informar: list[str] = []

    def exigir(calidad: str, fundamento: str) -> None:
        if calidad not in firmas:
            firmas.append(calidad)
        fundamentos.append(fundamento)

    def chequear_sme(obligatorio: bool) -> None:
        nonlocal escrita
        if contexto.saldo_proyectado is None:
            if obligatorio:
                raise DatosInvalidosError("Para evaluar el compromiso se requiere el saldo proyectado de caja.")
            return
        motivo = _evaluar_sme(p, Decimal(contexto.saldo_proyectado))
        if motivo:
            escrita = True
            exigir("GG", motivo)

    mayoria_param = (int(_param(p, "mayoria_calificada_numerador")),
                     int(_param(p, "mayoria_calificada_denominador")))

    if tipo is TipoDecision.DESEMBOLSO:
        limite_gg = Decimal(_param(p, "autoriza_desembolso_gg_sobre_bs"))
        limite_mancomunada = Decimal(_param(p, "firma_mancomunada_sobre_bs"))
        if importe > limite_gg:
            exigir("GG", f"POL-04 Art. 2: desembolso bancario mayor a {formato_bs(limite_gg)} → Gerencia General")
        if importe > limite_mancomunada:
            exigir("CV", f"POL-04 Art. 5: desembolso mayor a {formato_bs(limite_mancomunada)} → firma "
                         "mancomunada Gerencia General + miembro del Consejo de Vigilancia")
        chequear_sme(False)
    elif tipo is TipoDecision.COMPROMISO_GASTO:
        chequear_sme(True)
    elif tipo is TipoDecision.COMPRA_INSUMOS:
        limite = Decimal(_param(p, "oc_tres_cotizaciones_sobre_bs"))
        exigir("GG", "MOF §3.2 y PR-FIN-03: la orden de compra la autoriza la Gerencia General")
        if importe > limite:
            cotizaciones = int(_param(p, "cotizaciones_minimas"))
            fundamentos.append(f"PR-FIN-03: compra mayor a {formato_bs(limite)} → cotización con mínimo "
                               f"{cotizaciones} proveedores + Gerencia General")
        chequear_sme(False)
    elif tipo is TipoDecision.CONTRATACION_EVENTUAL:
        dia = int(_param(p, "dia_semana_contratacion_eventual"))
        exigir("GG", "POL-06 Art. 2: Gerencia General, exclusivamente el día fijado; suplencia según CI-FIN-17")
        chequear_sme(False)
    elif tipo is TipoDecision.CAPEX_NO_PREVISTO:
        if contexto.previsto_en_plan is None:
            raise DatosInvalidosError("Indique si la inversión está contemplada en el Plan CAPEX anual.")
        if contexto.previsto_en_plan:
            fundamentos.append("POL-02 Art. 1: inversión contemplada en el Plan CAPEX aprobado; no requiere "
                               "autorización adicional de la Matriz")
        else:
            exigir("CA", "POL-02 Art. 1: CAPEX no contemplado en el Plan anual (cualquier monto) → Consejo de "
                         "Administración")
            limite = Decimal(_param(p, "capex_no_previsto_ca_sobre_bs"))
            if importe > limite:
                especifica = True
                fundamentos.append(f"POL-02 Art. 1: CAPEX no previsto mayor a {formato_bs(limite)} → "
                                   "autorización específica del Consejo de Administración")
    elif tipo is TipoDecision.VARIACION_CAPEX:
        if contexto.monto_aprobado is None or redondear_bs(contexto.monto_aprobado) <= 0:
            raise DatosInvalidosError("Indique el monto CAPEX aprobado (mayor a cero) para medir la variación.")
        aprobado = redondear_bs(contexto.monto_aprobado)
        variacion = abs(importe - aprobado)
        fraccion = variacion / aprobado
        limite_pct = Decimal(_param(p, "capex_variacion_pct"))
        limite_monto = Decimal(_param(p, "capex_variacion_monto_bs"))
        if fraccion > limite_pct:
            informar.append("CA")
            fundamentos.append(f"POL-02 Art. 4: variación de {_pct(fraccion.quantize(Decimal('0.0001')))} "
                               f"supera el {_pct(limite_pct)} → se informa al Consejo de Administración")
            if variacion > limite_monto:
                exigir("AG", f"POL-02 Art. 4: variación mayor al {_pct(limite_pct)} y a {formato_bs(limite_monto)} "
                             f"({formato_bs(variacion)}) → nueva aprobación de la Asamblea General")
    elif tipo is TipoDecision.FINANCIAMIENTO_DEUDA_CAPEX:
        mayoria = mayoria_param
        exigir("AG", f"POL-02 Art. 2: financiamiento con deuda bancaria → Asamblea General (mayoría de "
                     f"{mayoria[0]}/{mayoria[1]})")
    elif tipo is TipoDecision.CREDITO_HORECA:
        dias = contexto.dias_credito
        if isinstance(dias, bool) or not isinstance(dias, int) or dias < 1:
            raise DatosInvalidosError("Indique los días de crédito (entero mayor o igual a 1).")
        maximo = int(_param(p, "horeca_credito_dias_max"))
        if dias > maximo:
            raise ReglaNegocioError(
                f"POL-05: el crédito HORECA máximo vigente en el ejercicio {contexto.ejercicio} es de {maximo} "
                f"día(s); se solicitaron {dias}.", codigo="CREDITO_EXCEDE_PLAZO")
        if dias > 1:
            entregas = int(_param(p, "horeca_monto_max_entregas"))
            if contexto.monto_entrega_semanal is not None and \
                    importe > redondear_bs(contexto.monto_entrega_semanal) * entregas:
                raise ReglaNegocioError("POL-05 Art. 2: el crédito no puede superar el monto de la entrega semanal.",
                                        codigo="CREDITO_EXCEDE_MONTO")
            if contexto.cxc_total_proyectada is not None and contexto.ingresos_ciclo:
                limite = Decimal(_param(p, "horeca_cxc_max_pct_ingresos"))
                if redondear_bs(contexto.cxc_total_proyectada) / redondear_bs(contexto.ingresos_ciclo) > limite:
                    raise ReglaNegocioError(
                        f"POL-05 Art. 2: las cuentas por cobrar superarían el {_pct(limite)} de los ingresos del ciclo.",
                        codigo="CREDITO_EXCEDE_CXC")
            escrita = True
            exigir("GG", f"POL-05 Art. 2: crédito HORECA de {dias} días (máx. {maximo}) → resolución escrita de "
                         "la Gerencia General")
    elif tipo is TipoDecision.CANAL_RESPALDO:
        exigir("GG", "CI-FIN-13 Art. 5: pedidos HORECA insuficientes → Gerencia General (activación sin trámite "
                     "adicional)")
    elif tipo is TipoDecision.DISTRIBUCION_EXCEDENTES:
        documentos = ("INF-CV-04",)
        exigir("AG", "POL-01 Art. 5; CI-FIN-02 Art. 13: Asamblea General, con informe previo del Consejo de "
                     "Vigilancia (INF-CV-04)")
    elif tipo is TipoDecision.CAPITALIZACION_EXCEDENTES:
        mayoria = mayoria_param
        exigir("AG", f"POL-01 Art. 4: capitalización de excedentes → Asamblea General (mayoría de "
                     f"{mayoria[0]}/{mayoria[1]})")
    elif tipo is TipoDecision.PRESUPUESTO_CERTIFICACIONES:
        exigir("CA", "POL-03 Art. 4: presupuesto anual de certificaciones → Consejo de Administración")
    elif tipo is TipoDecision.NUEVA_ACTIVIDAD_RAU:
        exigir("CA", "POL-07 Art. 1: nueva actividad con impacto en el RAU → Consejo de Administración, previa "
                     "opinión tributaria si hay duda")
    elif tipo is TipoDecision.AJUSTE_CONTABLE:
        exigir("GG", "MOF §3.1 (Clasificación): los asientos de ajuste y cierre los autoriza la Gerencia General")
    elif tipo is TipoDecision.OTRA:
        escrita = True
        exigir("GG", "CI-FIN-09 Art. 6: decisión no prevista → Gerencia General, con constancia escrita del "
                     "criterio aplicado")

    return RequisitoAutorizacion(
        tipo_decision=tipo.value, monto=importe, firmas=tuple(firmas), autorizacion_escrita=escrita,
        autorizacion_especifica=especifica, cotizaciones_minimas=cotizaciones, mayoria_calificada=mayoria,
        dia_semana=dia, documentos_previos=documentos, informar_a=tuple(informar),
        fundamentos=tuple(fundamentos),
    )


# ==========================================================================
# 6. SOLICITUDES Y FIRMAS
# ==========================================================================
@dataclass(frozen=True)
class Suplencia:
    """Constancia de suplencia de la Gerencia General (CI-FIN-17 Art. 2)."""

    causa: str
    constancia_ref: str
    motivo_omision_presidente: str | None = None


@dataclass(frozen=True)
class Cotizacion:
    """Cotización de un proveedor (PR-FIN-03)."""

    proveedor: str
    monto: Decimal
    documento_ref: str | None = None


def _requisito_de(fila: sqlite3.Row) -> RequisitoAutorizacion:
    return RequisitoAutorizacion.desde_dict(json.loads(fila["requisito_json"]))


def solicitar_autorizacion(
    con: sqlite3.Connection,
    sesion: Sesion,
    tipo_decision: TipoDecision | str,
    monto: Any,
    descripcion: str,
    contexto: ContextoDecision,
    *,
    referencia: str | None = None,
    documentos: Mapping[str, str] | None = None,
    cotizaciones: Sequence[Cotizacion] | None = None,
    ahora: datetime | None = None,
) -> int:
    """Registra una solicitud de autorización y su requisito según CI-FIN-09.

    Si la matriz no exige firma, la solicitud queda directamente AUTORIZADA.

    Raises:
        AutorizacionError: el rol no puede solicitar decisiones de ese recurso.
        ReglaNegocioError: faltan cotizaciones o documentos previos exigidos.
    """
    tipo = TipoDecision(tipo_decision)
    recurso = RECURSO_DECISION[tipo]
    if not (tiene_permiso(con, sesion.rol, recurso, "CREAR") or tiene_permiso(con, sesion.rol, recurso, "AUTORIZAR")):
        exigir_permiso(con, sesion, recurso, "CREAR")
    else:
        exigir_permiso(con, sesion, recurso, "VER")
    texto = _texto_obligatorio(descripcion, "descripción", 500)
    ref = _texto_opcional(referencia, "referencia", 120)
    requisito = requiere_autorizacion(tipo, monto, contexto)

    lista_cot = list(cotizaciones or [])
    if requisito.cotizaciones_minimas:
        proveedores = {c.proveedor.strip().lower() for c in lista_cot
                       if isinstance(c, Cotizacion) and c.proveedor and c.proveedor.strip()}
        if len(proveedores) < requisito.cotizaciones_minimas:
            raise ReglaNegocioError(
                f"PR-FIN-03: se requieren al menos {requisito.cotizaciones_minimas} cotizaciones de proveedores "
                f"distintos; se presentaron {len(proveedores)}.", codigo="COTIZACIONES_INSUFICIENTES")
    docs = {k: v.strip() for k, v in (documentos or {}).items() if isinstance(v, str) and v.strip()}
    faltantes = [d for d in requisito.documentos_previos if d not in docs]
    if faltantes:
        raise ReglaNegocioError(f"Falta el documento previo exigido: {', '.join(faltantes)}.",
                                codigo="DOCUMENTO_PREVIO_FALTANTE", detalle={"faltantes": faltantes})
    momento = _ahora(ahora)
    estado = "PENDIENTE" if requisito.requiere_firma else "AUTORIZADA"
    documentos_json = auditoria.a_json({
        "documentos": docs,
        "cotizaciones": [{"proveedor": c.proveedor, "monto": str(redondear_bs(c.monto)),
                          "documento_ref": c.documento_ref} for c in lista_cot],
    })
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO autorizaciones (tipo_decision, monto_cent, ejercicio, descripcion, referencia,
                                           requisito_json, documentos_json, estado, solicitado_por_id,
                                           solicitado_en, resuelto_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tipo.value, a_centavos(requisito.monto), contexto.ejercicio, texto, ref,
             json.dumps(requisito.a_dict(), ensure_ascii=False), documentos_json, estado, sesion.usuario_id,
             _iso(momento), None if requisito.requiere_firma else _iso(momento)))
        aid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "autorizaciones", "INSERT", aid,
                            despues={"tipo_decision": tipo.value, "monto": requisito.monto, "estado": estado,
                                     "requisito": requisito.a_dict()},
                            detalle=texto)
    return aid


def _orden_prelacion(con: sqlite3.Connection, sesion: Sesion, parametros: Mapping[str, Any]) -> int | None:
    """1 o 2 según el cargo del socio vinculado al usuario (CI-FIN-17 Art. 1)."""
    fila = con.execute("""SELECT s.cargo_cooperativo FROM usuarios u JOIN socios s ON s.id = u.socio_id
                           WHERE u.id = ?""", (sesion.usuario_id,)).fetchone()
    cargo = (fila[0] or "").strip().lower() if fila else ""
    if not cargo:
        return None
    if cargo == str(_param(parametros, "suplencia_cargo_orden_1")).strip().lower():
        return 1
    if cargo == str(_param(parametros, "suplencia_cargo_orden_2")).strip().lower():
        return 2
    return None


def orden_suplencia(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> int | None:
    """Orden de prelación CI-FIN-17 del usuario (1 Presidente, 2 Tesorero del
    CA) o ``None`` si no puede suplir a la Gerencia General."""
    if sesion.rol in ("JF", "UT", "GG"):
        return None
    momento = a_fecha(fecha) if fecha is not None else _ahora().date()
    params = obtener_parametros_vigentes(con, ejercicio_vigente_o_ultimo(con, momento))
    return _orden_prelacion(con, sesion, params)


def _firmas_de(con: sqlite3.Connection, autorizacion_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT f.*, u.nombre_usuario, u.nombre_completo FROM autorizacion_firmas f
             JOIN usuarios u ON u.id = f.usuario_id WHERE f.autorizacion_id = ? ORDER BY f.id""",
        (autorizacion_id,)).fetchall()


def firmar_autorizacion(
    con: sqlite3.Connection,
    sesion: Sesion,
    autorizacion_id: int,
    *,
    decision: str = "APRUEBA",
    documento_ref: str | None = None,
    observacion: str | None = None,
    suplencia: Suplencia | None = None,
    votos_favor: int | None = None,
    votos_emitidos: int | None = None,
    ahora: datetime | None = None,
) -> str:
    """Firma (aprueba o rechaza) una solicitud en la calidad que corresponde al rol.

    * GG firma como Gerencia General; un miembro del CV cofirma (firma
      mancomunada); CA y AG aprueban con referencia al acta.
    * Con ``suplencia`` el firmante actúa como Gerencia General si su cargo es
      Presidente del CA (orden 1) o Tesorero del CA (orden 2, solo con motivo
      por el que no actúa el Presidente). La constancia es obligatoria.
    * El JF y la UT nunca firman (segregación de funciones).

    Returns:
        Estado resultante: ``PENDIENTE``, ``AUTORIZADA`` o ``RECHAZADA``.

    Raises:
        AutorizacionError: segregación, firma no requerida, autofirma o
            suplencia no habilitada.
        DatosInvalidosError: falta la constancia o documento obligatorio.
        ReglaNegocioError: solicitud no pendiente, firma duplicada, día no
            permitido o mayoría calificada no alcanzada.
    """
    if decision not in ("APRUEBA", "RECHAZA"):
        raise DatosInvalidosError("La decisión debe ser APRUEBA o RECHAZA.")
    momento = _ahora(ahora)
    fila = con.execute("SELECT * FROM autorizaciones WHERE id = ?", (autorizacion_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la solicitud de autorización {autorizacion_id}.")
    tipo = TipoDecision(fila["tipo_decision"])
    recurso = RECURSO_DECISION[tipo]
    requisito = _requisito_de(fila)
    params = obtener_parametros_vigentes(con, int(fila["ejercicio"]))

    orden: int | None = None
    if suplencia is not None:
        calidad = "GG"
        exigir_permiso(con, sesion, recurso, "VER")
        if sesion.rol in ("JF", "UT", "GG"):
            raise AutorizacionError("La suplencia de la Gerencia General solo la ejercen el Presidente o el "
                                    "Tesorero del Consejo de Administración (CI-FIN-17 Art. 1).",
                                    codigo="SUPLENCIA_NO_HABILITADA")
        orden = _orden_prelacion(con, sesion, params)
        if orden is None:
            raise AutorizacionError(
                "Su cargo no figura en el orden de prelación de CI-FIN-17 (Presidente y, en su defecto, Tesorero "
                "del Consejo de Administración).", codigo="SUPLENCIA_NO_HABILITADA")
        causa = _texto_opcional(suplencia.causa, "causa de la suplencia")
        constancia = _texto_opcional(suplencia.constancia_ref, "constancia de la suplencia", 120)
        if not causa or not constancia:
            raise DatosInvalidosError("CI-FIN-17 Art. 2: la suplencia exige dejar constancia escrita de la causa "
                                      "(causa y referencia del documento).", codigo="CONSTANCIA_OBLIGATORIA")
        omision = _texto_opcional(suplencia.motivo_omision_presidente, "motivo de omisión del Presidente")
        if orden == 2 and not omision:
            raise AutorizacionError("CI-FIN-17 Art. 1: el Tesorero del CA suple solo en defecto del Presidente; "
                                    "registre por qué no actúa el Presidente.", codigo="SUPLENCIA_ORDEN")
        if not tiene_permiso(con, "GG", recurso, "AUTORIZAR"):
            raise AutorizacionError("La Gerencia General no tiene esa facultad; la suplencia no la amplía "
                                    "(CI-FIN-17 Art. 3).", codigo="SUPLENCIA_NO_HABILITADA")
        documento = constancia
    else:
        if sesion.rol not in NOMBRE_CALIDAD:
            exigir_permiso(con, sesion, recurso, "AUTORIZAR")  # lanza SEGREGACION_FUNCIONES para JF y UT
            raise AutorizacionError("Su rol no firma autorizaciones.", codigo="SIN_PERMISO")
        calidad = sesion.rol
        causa = omision = None
        documento = _texto_opcional(documento_ref, "documento de respaldo", 120)

    if calidad not in requisito.firmas:
        raise AutorizacionError(
            f"Esta decisión no requiere la firma de {NOMBRE_CALIDAD[calidad]}; exige: "
            f"{', '.join(NOMBRE_CALIDAD[c] for c in requisito.firmas) or 'ninguna firma'}.",
            codigo="FIRMA_NO_REQUERIDA")
    if suplencia is None:
        exigir_permiso(con, sesion, *accion_de_calidad(calidad, recurso))
    if int(fila["solicitado_por_id"]) == sesion.usuario_id:
        raise AutorizacionError("Quien solicita no puede autorizar su propia solicitud (CI-FIN-10).",
                                codigo="AUTOFIRMA")
    if fila["estado"] != "PENDIENTE":
        raise ReglaNegocioError(f"La solicitud ya está {fila['estado']}.", codigo="SOLICITUD_NO_PENDIENTE")
    firmas = _firmas_de(con, autorizacion_id)
    if any(f["calidad"] == calidad for f in firmas):
        raise ReglaNegocioError(f"La solicitud ya tiene la firma de {NOMBRE_CALIDAD[calidad]}.",
                                codigo="FIRMA_DUPLICADA")
    if any(int(f["usuario_id"]) == sesion.usuario_id for f in firmas):
        raise AutorizacionError("Una misma persona no puede firmar dos veces la misma solicitud.",
                                codigo="FIRMA_DUPLICADA")
    necesita_documento = calidad in ("CA", "AG") or (
        calidad == "GG" and (requisito.autorizacion_escrita or requisito.autorizacion_especifica))
    if necesita_documento and not documento:
        raise DatosInvalidosError(
            f"La firma de {NOMBRE_CALIDAD[calidad]} exige la referencia del documento escrito (acta o "
            "resolución).", codigo="DOCUMENTO_OBLIGATORIO")
    if decision == "APRUEBA" and calidad == "AG" and requisito.mayoria_calificada:
        num, den = requisito.mayoria_calificada
        if isinstance(votos_favor, bool) or isinstance(votos_emitidos, bool) or \
                not isinstance(votos_favor, int) or not isinstance(votos_emitidos, int) or votos_emitidos <= 0 \
                or not 0 <= votos_favor <= votos_emitidos:
            raise DatosInvalidosError("Registre los votos a favor y los votos emitidos de la Asamblea.")
        if votos_favor * den < num * votos_emitidos:
            raise ReglaNegocioError(
                f"Se requiere mayoría de {num}/{den}: {votos_favor} de {votos_emitidos} votos no la alcanza.",
                codigo="MAYORIA_CALIFICADA_NO_ALCANZADA")
    if decision == "APRUEBA" and requisito.dia_semana and momento.isoweekday() != requisito.dia_semana:
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        raise ReglaNegocioError(f"Esta autorización solo puede otorgarse el día {dias[requisito.dia_semana - 1]}.",
                                codigo="DIA_NO_PERMITIDO")

    with transaccion(con):
        cur = con.execute(
            """INSERT INTO autorizacion_firmas (autorizacion_id, calidad, usuario_id, rol_usuario, decision,
                   documento_ref, observacion, es_suplencia, orden_prelacion, causa_suplencia,
                   motivo_omision_presidente, votos_favor, votos_emitidos, firmado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (autorizacion_id, calidad, sesion.usuario_id, sesion.rol, decision, documento,
             _texto_opcional(observacion, "observación"), 1 if suplencia else 0, orden, causa, omision,
             votos_favor, votos_emitidos, _iso(momento)))
        firmadas = {f["calidad"] for f in firmas if f["decision"] == "APRUEBA"}
        if decision == "APRUEBA":
            firmadas.add(calidad)
        if decision == "RECHAZA":
            nuevo = "RECHAZADA"
        elif set(requisito.firmas) <= firmadas:
            nuevo = "AUTORIZADA"
        else:
            nuevo = "PENDIENTE"
        if nuevo != "PENDIENTE":
            con.execute("UPDATE autorizaciones SET estado = ?, resuelto_en = ? WHERE id = ?",
                        (nuevo, _iso(momento), autorizacion_id))
        auditoria.registrar(
            con, sesion, "autorizacion_firmas", "AUTORIZACION", cur.lastrowid,
            antes={"estado": fila["estado"]},
            despues={"autorizacion_id": autorizacion_id, "calidad": calidad, "decision": decision,
                     "es_suplencia": bool(suplencia), "orden_prelacion": orden, "documento_ref": documento,
                     "estado": nuevo},
            detalle=("Firma por suplencia CI-FIN-17: " + (causa or "")) if suplencia else None)
    return nuevo


def faltantes_de(con: sqlite3.Connection, autorizacion_id: int) -> tuple[str, ...]:
    """Calidades de firma que aún faltan en una solicitud."""
    fila = con.execute("SELECT * FROM autorizaciones WHERE id = ?", (autorizacion_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la solicitud de autorización {autorizacion_id}.")
    hechas = {f["calidad"] for f in _firmas_de(con, autorizacion_id) if f["decision"] == "APRUEBA"}
    return tuple(c for c in _requisito_de(fila).firmas if c not in hechas)


def verificar_autorizacion(
    con: sqlite3.Connection, autorizacion_id: int, *, tipo_decision: TipoDecision | str | None = None,
    monto: Any = None,
) -> dict[str, Any]:
    """Comprueba que una autorización esté completa y sea aplicable.

    Raises:
        AutorizacionError: ``FIRMA_MANCOMUNADA_INCOMPLETA``,
            ``AUTORIZACION_INCOMPLETA`` o ``AUTORIZACION_RECHAZADA``.
        ReglaNegocioError: otro tipo de decisión, monto mayor al autorizado o
            autorización ya utilizada.
    """
    if isinstance(autorizacion_id, bool) or not isinstance(autorizacion_id, int):
        raise DatosInvalidosError("Indique el número de la autorización.")
    fila = con.execute("SELECT * FROM autorizaciones WHERE id = ?", (autorizacion_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la autorización {autorizacion_id}.")
    if tipo_decision is not None and fila["tipo_decision"] != TipoDecision(tipo_decision).value:
        raise ReglaNegocioError(
            f"La autorización {autorizacion_id} corresponde a {fila['tipo_decision']}, no a "
            f"{TipoDecision(tipo_decision).value}.", codigo="AUTORIZACION_OTRO_TIPO")
    autorizado = de_centavos(int(fila["monto_cent"]))
    if monto is not None and redondear_bs(monto) > autorizado:
        raise ReglaNegocioError(
            f"El monto ({formato_bs(monto)}) supera el autorizado ({formato_bs(autorizado)}).",
            codigo="MONTO_EXCEDE_AUTORIZADO")
    if fila["estado"] == "RECHAZADA":
        raise AutorizacionError(f"La autorización {autorizacion_id} fue rechazada.", codigo="AUTORIZACION_RECHAZADA")
    if fila["estado"] == "UTILIZADA":
        raise ReglaNegocioError(f"La autorización {autorizacion_id} ya fue utilizada.",
                                codigo="AUTORIZACION_YA_UTILIZADA")
    if fila["estado"] == "PENDIENTE":
        requisito = _requisito_de(fila)
        faltan = faltantes_de(con, autorizacion_id)
        nombres = ", ".join(NOMBRE_CALIDAD[c] for c in faltan)
        if requisito.firma_mancomunada:
            raise AutorizacionError(
                f"Firma mancomunada incompleta (POL-04 Art. 5): falta la firma de {nombres}.",
                codigo="FIRMA_MANCOMUNADA_INCOMPLETA", detalle={"faltan": list(faltan)})
        raise AutorizacionError(f"Autorización incompleta: falta la firma de {nombres}.",
                                codigo="AUTORIZACION_INCOMPLETA", detalle={"faltan": list(faltan)})
    return dict(fila)


def consumir_autorizacion(
    con: sqlite3.Connection, sesion: Sesion, autorizacion_id: int, tabla: str, registro_id: Any, *,
    tipo_decision: TipoDecision | str, monto: Any, ahora: datetime | None = None,
) -> None:
    """Marca la autorización como UTILIZADA por un registro (uso único)."""
    verificar_autorizacion(con, autorizacion_id, tipo_decision=tipo_decision, monto=monto)
    momento = _ahora(ahora)
    with transaccion(con):
        con.execute(
            """UPDATE autorizaciones SET estado = 'UTILIZADA', utilizado_tabla = ?, utilizado_id = ?,
                                         utilizado_en = ? WHERE id = ?""",
            (tabla, str(registro_id), _iso(momento), autorizacion_id))
        auditoria.registrar(con, sesion, "autorizaciones", "UPDATE", autorizacion_id,
                            antes={"estado": "AUTORIZADA"},
                            despues={"estado": "UTILIZADA", "utilizado_tabla": tabla,
                                     "utilizado_id": str(registro_id)})


def listar_autorizaciones(
    con: sqlite3.Connection, sesion: Sesion, estado: str | None = None, tipo_decision: str | None = None,
) -> list[dict[str, Any]]:
    """Solicitudes con su requisito, firmas registradas y firmas faltantes."""
    exigir_permiso(con, sesion, "tesoreria", "VER")
    condiciones, args = [], []
    if estado:
        condiciones.append("a.estado = ?")
        args.append(estado)
    if tipo_decision:
        condiciones.append("a.tipo_decision = ?")
        args.append(TipoDecision(tipo_decision).value)
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    filas = con.execute(
        f"""SELECT a.*, u.nombre_completo AS solicitante FROM autorizaciones a
              JOIN usuarios u ON u.id = a.solicitado_por_id {where} ORDER BY a.id DESC""", args).fetchall()
    resultado = []
    for f in filas:
        requisito = _requisito_de(f)
        firmas = [dict(x) for x in _firmas_de(con, int(f["id"]))]
        hechas = {x["calidad"] for x in firmas if x["decision"] == "APRUEBA"}
        resultado.append({
            **{k: f[k] for k in f.keys() if k not in ("requisito_json", "monto_cent")},
            "monto": de_centavos(int(f["monto_cent"])),
            "requisito": requisito,
            "firmas": firmas,
            "faltan": [c for c in requisito.firmas if c not in hechas],
        })
    return resultado


# ==========================================================================
# 7. SEGUIMIENTO DE LA SUPLENCIA (CI-FIN-17 Arts. 2 y 4)
# ==========================================================================
def registrar_comunicacion_suplencia(
    con: sqlite3.Connection, sesion: Sesion, firma_id: int, ahora: datetime | None = None
) -> bool:
    """El suplente deja constancia de haber comunicado su decisión a la GG.

    Returns:
        ``True`` si se comunicó dentro del plazo (``suplencia_comunicacion_horas``).
    """
    momento = _ahora(ahora)
    fila = con.execute(
        """SELECT f.*, a.ejercicio FROM autorizacion_firmas f JOIN autorizaciones a ON a.id = f.autorizacion_id
            WHERE f.id = ?""", (firma_id,)).fetchone()
    if fila is None or not fila["es_suplencia"]:
        raise DatosInvalidosError(f"La firma {firma_id} no es una firma por suplencia.")
    if int(fila["usuario_id"]) != sesion.usuario_id:
        raise AutorizacionError("Solo el suplente que firmó registra la comunicación a la GG.",
                                codigo="SIN_PERMISO")
    if fila["comunicado_gg_en"]:
        raise ReglaNegocioError("La comunicación ya fue registrada.", codigo="YA_REGISTRADO")
    horas = int(_param(obtener_parametros_vigentes(con, int(fila["ejercicio"])), "suplencia_comunicacion_horas"))
    en_plazo = momento - _de_iso(fila["firmado_en"]) <= timedelta(hours=horas)
    with transaccion(con):
        con.execute("UPDATE autorizacion_firmas SET comunicado_gg_en = ? WHERE id = ?", (_iso(momento), firma_id))
        auditoria.registrar(con, sesion, "autorizacion_firmas", "UPDATE", firma_id,
                            antes={"comunicado_gg_en": None},
                            despues={"comunicado_gg_en": _iso(momento), "en_plazo": en_plazo},
                            detalle=f"Comunicación a la GG (CI-FIN-17 Art. 2, plazo {horas} h)")
    return en_plazo


def revisar_suplencia(con: sqlite3.Connection, sesion: Sesion, firma_id: int, ahora: datetime | None = None) -> None:
    """La Gerencia General revisa a su reincorporación una autorización
    ejercida por suplencia (CI-FIN-17 Art. 4)."""
    if sesion.rol != "GG":
        raise AutorizacionError("Solo la Gerencia General revisa las suplencias (CI-FIN-17 Art. 4).",
                                codigo="SIN_PERMISO")
    exigir_permiso(con, sesion, "tesoreria", "VER")
    momento = _ahora(ahora)
    fila = con.execute("SELECT * FROM autorizacion_firmas WHERE id = ?", (firma_id,)).fetchone()
    if fila is None or not fila["es_suplencia"]:
        raise DatosInvalidosError(f"La firma {firma_id} no es una firma por suplencia.")
    if fila["revisado_gg_en"]:
        raise ReglaNegocioError("La suplencia ya fue revisada.", codigo="YA_REGISTRADO")
    with transaccion(con):
        con.execute("UPDATE autorizacion_firmas SET revisado_gg_en = ?, revisado_gg_por = ? WHERE id = ?",
                    (_iso(momento), sesion.nombre_usuario, firma_id))
        auditoria.registrar(con, sesion, "autorizacion_firmas", "UPDATE", firma_id,
                            antes={"revisado_gg_en": None},
                            despues={"revisado_gg_en": _iso(momento), "revisado_gg_por": sesion.nombre_usuario},
                            detalle="Revisión de suplencia por la GG (CI-FIN-17 Art. 4)")


def suplencias_pendientes(con: sqlite3.Connection, sesion: Sesion,
                          ahora: datetime | None = None) -> list[dict[str, Any]]:
    """Firmas por suplencia aún no revisadas por la GG, con alerta de plazo."""
    exigir_permiso(con, sesion, "tesoreria", "VER")
    momento = _ahora(ahora)
    filas = con.execute(
        """SELECT f.*, u.nombre_completo, a.tipo_decision, a.monto_cent, a.descripcion, a.ejercicio
             FROM autorizacion_firmas f JOIN usuarios u ON u.id = f.usuario_id
             JOIN autorizaciones a ON a.id = f.autorizacion_id
            WHERE f.es_suplencia = 1 AND f.revisado_gg_en IS NULL ORDER BY f.id""").fetchall()
    resultado = []
    for f in filas:
        horas = int(_param(obtener_parametros_vigentes(con, int(f["ejercicio"])), "suplencia_comunicacion_horas"))
        limite = _de_iso(f["firmado_en"]) + timedelta(hours=horas)
        comunicado = _de_iso(f["comunicado_gg_en"]) if f["comunicado_gg_en"] else None
        resultado.append({
            **{k: f[k] for k in f.keys() if k != "monto_cent"},
            "monto": de_centavos(int(f["monto_cent"])),
            "comunicacion_fuera_de_plazo": (comunicado or momento) > limite,
        })
    return resultado
