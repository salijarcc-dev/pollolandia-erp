"""Revisión de seguridad automatizada (Fase 13).

Verifica, contra la base en uso, los controles de acceso que exigen los
documentos y que el sistema implementa desde las Fases 2-3:

* MAN-ARC-01 §5.2: usuario y contraseña personales con cambio cada 90 días
  (``password_caducidad_dias``); Consejo de Vigilancia solo lectura; baja de
  usuarios al término de su cargo; registro de accesos.
* Bloqueo por intentos fallidos (``login_intentos_max``, ``login_bloqueo_minutos``)
  y cierre de sesión por inactividad (``sesion_inactividad_minutos``).
* MOF §3.4 / CI-FIN-10: segregación de funciones (el JF no autoriza, la GG no
  registra asientos, la UT no autoriza, el CV no ejecuta ni aprueba).
* Integridad: contraseñas con bcrypt, bitácora inmutable, claves foráneas y
  journal WAL.

Cada control devuelve semáforo VERDE / AMARILLO / ROJO y el detalle; el
resultado se muestra en «Administración · Seguridad» y se documenta en
``docs/revision_seguridad.md``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

import config
from db.conexion import ejercicio_de_fecha_bd, obtener_parametros_vigentes
from servicios import seguridad
from servicios.seguridad import ACCIONES_DECISION, ACCIONES_REGISTRO, RECURSOS_SOLO_LECTURA_EXCEPTUADOS, Sesion

VERDE, AMARILLO, ROJO = "VERDE", "AMARILLO", "ROJO"
RECURSOS_REVISADOS: tuple[str, ...] = (
    "tesoreria", "compras", "inventarios", "planillas", "produccion", "costos", "activos", "ventas", "contabilidad",
    "rau", "presupuesto", "vigilancia", "dashboard", "parametros", "usuarios", "auditoria", "respaldos",
)
TRIGGERS_BITACORA = ("trg_auditoria_no_update", "trg_auditoria_no_delete")


def _costo_bcrypt(hash_: str) -> int | None:
    partes = hash_.split("$")
    if len(partes) >= 4 and partes[1] in ("2a", "2b", "2y") and partes[2].isdigit():
        return int(partes[2])
    return None


def revision(con: sqlite3.Connection, sesion: Sesion, ahora: datetime | None = None) -> dict[str, Any]:
    """Ejecuta los controles y devuelve ``{fecha, controles, semaforo, resumen}``."""
    seguridad.exigir_permiso(con, sesion, "auditoria", "VER")
    momento = ahora or datetime.now()
    p = obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, momento.date()))
    controles: list[dict[str, Any]] = []

    def control(area: str, nombre: str, color: str, detalle: str, fuente: str) -> None:
        controles.append({"area": area, "control": nombre, "semaforo": color, "detalle": detalle, "fuente": fuente})

    caducidad = int(p["password_caducidad_dias"])
    control("Contraseñas", "Cambio obligatorio de contraseña", VERDE if 0 < caducidad <= 90 else ROJO,
            f"Cada {caducidad} días (parámetro password_caducidad_dias).", "MAN-ARC-01 §5.2")
    usuarios = con.execute("SELECT * FROM usuarios WHERE activo = 1 ORDER BY nombre_usuario").fetchall()
    vencidas = [u["nombre_usuario"] for u in usuarios
                if datetime.fromisoformat(u["ultimo_cambio_password"]) + timedelta(days=caducidad) <= momento]
    control("Contraseñas", "Contraseñas vencidas", VERDE if not vencidas else AMARILLO,
            ("Ninguna." if not vencidas else
             f"{len(vencidas)} usuario(s) deberán cambiarla al ingresar: {', '.join(vencidas)}."), "MAN-ARC-01 §5.2")
    pendientes = [u["nombre_usuario"] for u in usuarios if u["debe_cambiar_password"]]
    control("Contraseñas", "Contraseñas temporales sin cambiar", VERDE if not pendientes else AMARILLO,
            "Ninguna." if not pendientes else f"Primer ingreso pendiente: {', '.join(pendientes)}.",
            "Política de contraseñas (Fase 3)")
    costos = [(u["nombre_usuario"], _costo_bcrypt(u["password_hash"])) for u in usuarios]
    sin_bcrypt = [n for n, c in costos if c is None]
    debiles = [n for n, c in costos if c is not None and c < config.BCRYPT_ROUNDS]
    control("Contraseñas", "Contraseñas cifradas con bcrypt", ROJO if sin_bcrypt else (AMARILLO if debiles else VERDE),
            "Todas con bcrypt de costo ≥ " + str(config.BCRYPT_ROUNDS) + "." if not (sin_bcrypt or debiles) else
            f"Sin bcrypt: {', '.join(sin_bcrypt) or 'ninguno'}; costo bajo: {', '.join(debiles) or 'ninguno'} "
            "(se corrige con un restablecimiento de contraseña).", "Fase 3 (bcrypt)")
    maximo, minutos = int(p["login_intentos_max"]), int(p["login_bloqueo_minutos"])
    control("Acceso", "Bloqueo por intentos fallidos", VERDE if 0 < maximo <= 5 and minutos > 0 else ROJO,
            f"{maximo} intentos fallidos bloquean el usuario {minutos} minutos.", "Política de acceso (Fase 3)")
    bloqueados = [u["nombre_usuario"] for u in usuarios
                  if u["bloqueado_hasta"] and datetime.fromisoformat(u["bloqueado_hasta"]) > momento]
    control("Acceso", "Usuarios bloqueados ahora", VERDE if not bloqueados else AMARILLO,
            "Ninguno." if not bloqueados else ", ".join(bloqueados), "Política de acceso (Fase 3)")
    inactividad = int(p["sesion_inactividad_minutos"])
    control("Acceso", "Cierre de sesión por inactividad", VERDE if 0 < inactividad <= 60 else ROJO,
            f"{inactividad} minutos.", "Política de acceso (Fase 3)")
    accesos = int(con.execute("SELECT COUNT(*) FROM auditoria WHERE accion IN ('LOGIN','LOGOUT')").fetchone()[0])
    control("Acceso", "Registro de accesos", VERDE if accesos else AMARILLO,
            f"{accesos} evento(s) de ingreso o salida en la bitácora.", "MAN-ARC-01 §5.2")
    # Consejo de Vigilancia: solo lectura (salvo sus propios informes y la verificación de respaldos).
    prohibidas = ACCIONES_REGISTRO | frozenset({"AUTORIZAR", "APROBAR"})
    escrituras = [f"{r}/{a}" for r in RECURSOS_REVISADOS for a in sorted(prohibidas)
                  if r not in RECURSOS_SOLO_LECTURA_EXCEPTUADOS and seguridad.tiene_permiso(con, "CV", r, a)]
    control("Segregación", "Consejo de Vigilancia solo lectura", VERDE if not escrituras else ROJO,
            "El CV solo registra sus informes (vigilancia), verifica y cofirma desembolsos." if not escrituras
            else f"Permisos de escritura indebidos: {', '.join(escrituras)}.", "MAN-ARC-01 §5.2; MOF §3.4")
    neutralizados = [f"{f['rol']} {f['recurso']}/{f['accion']}" for f in
                     con.execute("SELECT rol, recurso, accion FROM permisos ORDER BY rol, recurso, accion")
                     if seguridad.incompatibilidad(f["rol"], f["recurso"], f["accion"])]
    control("Segregación", "Permisos incompatibles en la matriz", VERDE if not neutralizados else AMARILLO,
            "Ninguno." if not neutralizados else
            f"Bloqueados por la segregación aunque figuran en la tabla: {', '.join(neutralizados)}.", "CI-FIN-10")
    reglas = [("JF", "tesoreria", "AUTORIZAR"), ("JF", "compras", "AUTORIZAR"), ("GG", "contabilidad", "CREAR"),
              ("UT", "tesoreria", "AUTORIZAR"), ("CV", "contabilidad", "CREAR"), ("CV", "presupuesto", "APROBAR")]
    violadas = [f"{r}/{rec}/{a}" for r, rec, a in reglas if seguridad.tiene_permiso(con, r, rec, a)]
    control("Segregación", "Incompatibilidades del MOF", VERDE if not violadas else ROJO,
            "JF no autoriza; GG no registra asientos; UT no autoriza; CV no registra ni aprueba." if not violadas
            else f"Permitido indebidamente: {', '.join(violadas)}.", "MOF §3.4; CI-FIN-10")
    decisiones_jf = [a for a in ACCIONES_DECISION for r in RECURSOS_REVISADOS if seguridad.tiene_permiso(con, "JF", r, a)]
    control("Segregación", "El Jefe de Finanzas no firma", VERDE if not decisiones_jf else ROJO,
            "Sin permisos de autorización, aprobación ni cofirma." if not decisiones_jf else ", ".join(decisiones_jf),
            "MOF §3.4")
    triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    faltan = [t for t in TRIGGERS_BITACORA if t not in triggers]
    control("Integridad", "Bitácora de auditoría inmutable", VERDE if not faltan else ROJO,
            "Disparadores contra modificación y borrado presentes." if not faltan else f"Faltan: {', '.join(faltan)}.",
            "Regla 5 del proyecto")
    fk = int(con.execute("PRAGMA foreign_keys").fetchone()[0])
    modo = str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    control("Integridad", "Claves foráneas y journal WAL", VERDE if fk == 1 and modo in ("wal", "memory") else ROJO,
            f"foreign_keys={fk}; journal_mode={modo}.", "Stack obligatorio")
    sin_socio = [u["nombre_usuario"] for u in usuarios if u["rol"] in ("CA", "CV", "AG") and u["socio_id"] is None
                 and u["rol"] != "AG"]
    control("Usuarios", "Miembros de los Consejos vinculados a su socio", VERDE if not sin_socio else AMARILLO,
            "Todos vinculados." if not sin_socio else f"Sin socio: {', '.join(sin_socio)}.", "CI-FIN-17; Ley 356")
    peor = ROJO if any(c["semaforo"] == ROJO for c in controles) else (
        AMARILLO if any(c["semaforo"] == AMARILLO for c in controles) else VERDE)
    return {"fecha": momento.replace(microsecond=0).isoformat(), "controles": controles, "semaforo": peor,
            "resumen": {s: sum(1 for c in controles if c["semaforo"] == s) for s in (VERDE, AMARILLO, ROJO)}}
