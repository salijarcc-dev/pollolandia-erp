"""Operativo y trazabilidad del ciclo de 73 días (Fase 6, Ciclo 3 — Transformación).

Implementa POL-03, PC-03, PC-04, PR-FIN-06, CI-FIN-05, CI-FIN-11, CI-FIN-12
y los formatos FIN-TRA-01, FIN-TRA-02, FIN-TRA-04, FIN-TRA-05 y FIN-TRA-11:

1. Calendario de ciclos: 5 ciclos por ejercicio de 73 días (el día de
   recepción es el día 1), galpones con su superficie y lotes de hasta 1.000
   aves por galpón con densidad máxima de 8 aves/m².
2. Apertura del ciclo (FIN-TRA-01) sobre el traslado de los pollitos BB que
   la recepción (FIN-EGR-04) ya registró el mismo día: 1.1.03.01 a
   6.1.01.01 sufijo 01.
3. Bitácora diaria (FIN-TRA-02) con fase (inicio, crecimiento, acabado),
   alimento en kg, aves muertas con causa presunta y novedades; mortalidad
   ACUMULADA sobre las aves iniciales con semáforo (verde, amarillo, rojo).
4. Alertas sanitarias del mismo día a la Gerencia (CI-FIN-11, Pasos 4 y 5).
5. Baja por mortalidad (FIN-TRA-04, CI-FIN-05): normal (hasta 5 % acumulado)
   al sufijo 40 dentro del centro; anormal (> 5 %) a 7.2.01.04 con informe
   veterinario y autorización de la Gerencia General.
6. Disposición sanitaria de las aves muertas (FIN-TRA-11).
7. Muestreo semanal de peso (POL-03 Art. 2) y revisión semanal de la bitácora.
8. Faenado por lote (FIN-TRA-05) bloqueado antes del día 70, con peso vivo,
   peso canal, merma, puntos de control CI-FIN-12, PMF e ICA con semáforo.
9. Conciliación aves ingresadas = muertas + faenadas y conclusión del ciclo.
10. URL y código QR «Vida Digna» del lote con datos tomados solo de los
    registros del ciclo (POL-03 Art. 3).

Las cifras (73, 70, 5, 1.000, 8, 3 %, 5 %, 10 %, 20 %, 2,00, 2,10, fases y
periodicidad del muestreo) se leen de ``parametros``. No importa ``streamlit``.

# DISCREPANCIA: los documentos usan «lote» para dos cosas: el lote de crianza
# del ciclo (FIN-TRA-01 «N° de Ciclo / Lote», FIN-TRA-04 «% acumulado del
# lote») y el lote de faenado de 1.000 aves (POA B3, CI-FIN-12). Aquí el
# porcentaje de mortalidad y su clasificación se calculan sobre las aves
# iniciales del ciclo (base de conciliación de FIN-TRA-01) y «lote» es la
# fracción de hasta 1.000 aves alojada en un galpón y faenada junta.
"""
from __future__ import annotations

import html
import math
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion
from servicios import auditoria, contable, inventarios, seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import a_mil, de_mil
from servicios.seguridad import Sesion
from utils.formato import a_decimal, a_fecha, fecha_pantalla, inicio_ejercicio, redondear_bs

# --------------------------------------------------------------------------
# Estructura del Plan de Cuentas v3 (no cifras)
# --------------------------------------------------------------------------
CENTRO_CARNE = "6.1.01.01"
CUENTA_POLLITOS = "1.1.03.01"
SUFIJO_POLLITOS = "01"
SUFIJO_MORTALIDAD_NORMAL = "40"
CUENTA_MORTALIDAD_ANORMAL = "7.2.01.04"
CUENTAS_ALIMENTACION = ("1.1.03.02", "1.1.03.03", "1.1.03.04", "1.1.03.05", "1.1.03.06", "1.1.03.07", "1.1.03.13")
FASES = ("INICIO", "CRECIMIENTO", "ACABADO")
NOMBRE_FASE = {"INICIO": "Inicio", "CRECIMIENTO": "Crecimiento", "ACABADO": "Acabado"}
TIPOS_ALERTA = {
    "MORTALIDAD_ALERTA": "Mortalidad acumulada sobre el umbral de alerta",
    "MORTALIDAD_ANORMAL": "Mortalidad acumulada anormal",
    "SIGNOS_CLINICOS": "Signos clínicos o desvío de bioseguridad",
    "PMF_MALO": "Merma de faenado en rojo",
    "INOCUIDAD": "Punto de control CI-FIN-12 no cumplido",
    "OTRA": "Otra novedad sanitaria",
}
MEDIDAS = {
    "CONSULTA_VETERINARIA": "Consulta veterinaria extraordinaria",
    "AISLAMIENTO": "Aislamiento de aves sintomáticas",
    "REFUERZO_DESINFECCION": "Refuerzo de las medidas de desinfección",
    "SIN_MEDIDA": "Sin medida extraordinaria",
    "OTRA": "Otra medida",
}
METODOS_DISPOSICION = {
    "ENTIERRO_CAL": "Entierro sanitario con cal",
    "INCINERACION": "Incineración",
    "COMPOSTAJE": "Compostaje controlado",
    "OTRO": "Otro método",
}
# Puntos de control del Instructivo CI-FIN-12 (requisitos previos y Pasos 2 a 7).
PUNTOS_CI_FIN_12 = {
    "agua_hielo": "Agua potable e hielo suficientes para el choque térmico (requisito previo)",
    "bioseguridad_personal": "Limpieza profunda y equipo de bioseguridad del personal verificados (requisito previo)",
    "aturdimiento": "Aturdimiento efectivo antes del sacrificio (Paso 2)",
    "escaldado": "Temperatura del agua de escaldado controlada (Paso 3)",
    "eviscerado": "Eviscerado higiénico y vísceras con patología separadas (Paso 4)",
    "control_carcasa": "Control visual y técnico de cada carcasa (Paso 5)",
    "choque_termico": "Choque térmico hasta temperatura segura antes del embolsado (Paso 6)",
    "embolsado_qr": "Embolsado con bolsa personalizada y código QR (Paso 7)",
}
CERO = Decimal("0.00")
TASA = Decimal("0.0001")
KG = Decimal("0.001")
CIEN = Decimal(100)


# ==========================================================================
# UTILITARIOS
# ==========================================================================
def _ahora(ahora: datetime | None = None) -> datetime:
    return (ahora or datetime.now()).replace(microsecond=0)


def _iso(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%S")


def _texto(valor: Any, campo: str, maximo: int = 500) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise DatosInvalidosError(f"El campo '{campo}' es obligatorio.")
    if len(valor.strip()) > maximo:
        raise DatosInvalidosError(f"El campo '{campo}' admite como máximo {maximo} caracteres.")
    return valor.strip()


def _opcional(valor: Any, campo: str, maximo: int = 500) -> str | None:
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    return _texto(valor, campo, maximo)


def _entero(valor: Any, campo: str, minimo: int = 0, maximo: int | None = None) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise DatosInvalidosError(f"El campo '{campo}' debe ser un número entero.")
    if valor < minimo or (maximo is not None and valor > maximo):
        rango = f"entre {minimo} y {maximo}" if maximo is not None else f"mayor o igual a {minimo}"
        raise DatosInvalidosError(f"El campo '{campo}' debe estar {rango}.")
    return valor


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _pct(valor: Decimal) -> Decimal:
    return valor.quantize(TASA, rounding=ROUND_HALF_UP)


def pct_texto(valor: Any) -> str:
    """Proporción como porcentaje de pantalla: 0.051 -> ``5,10 %``."""
    if valor is None:
        return "—"
    return f"{(Decimal(str(valor)) * CIEN).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} %".replace(".", ",")


def kg_texto(milesimas: int | None) -> str:
    """Kilogramos de pantalla: ``1.234,5 kg``."""
    return inventarios.formato_cantidad(milesimas, "kg")


def _exigir_unidad_tecnica(con: sqlite3.Connection, sesion: Sesion, accion: str = "CREAR") -> None:
    """Registros de campo: los lleva la Unidad Técnica (FIN-TRA-02, 05, 11;
    CI-FIN-11; POL-03 Art. 2). Finanzas los usa, no los produce (CI-FIN-10)."""
    seguridad.exigir_permiso(con, sesion, "produccion", accion)
    if sesion.rol != "UT":
        raise AutorizacionError("Este registro de campo corresponde a la Unidad Técnica (Encargado de Granja).",
                                codigo="SOLO_UNIDAD_TECNICA")


def _exigir_finanzas(con: sqlite3.Connection, sesion: Sesion, accion: str = "EDITAR") -> None:
    seguridad.exigir_permiso(con, sesion, "produccion", accion)
    if sesion.rol != "JF":
        raise AutorizacionError("Este registro corresponde a la Unidad de Finanzas y Comercialización.",
                                codigo="SOLO_FINANZAS")


def _exigir_gerencia(con: sqlite3.Connection, sesion: Sesion) -> None:
    seguridad.exigir_permiso(con, sesion, "produccion", "AUTORIZAR")
    if sesion.rol != "GG":
        raise AutorizacionError("La decisión corresponde a la Gerencia General.", codigo="SOLO_GERENCIA")


# ==========================================================================
# 0. CALENDARIO DE CICLOS (POL-03 Art. 1; PC-04; Manual Presupuestario P-01)
# ==========================================================================
def dia_del_ciclo(con: sqlite3.Connection, inicio: Any, fecha: Any) -> int:
    """Día del ciclo de una fecha. Con ``ciclo_cierre_inclusivo`` el día de
    recepción es el día 1 (criterio del POA)."""
    i, f = a_fecha(inicio), a_fecha(fecha)
    p = _params(con, i)
    return (f - i).days + (1 if bool(_param(p, "ciclo_cierre_inclusivo")) else 0)


def fecha_de_cierre(con: sqlite3.Connection, inicio: Any) -> date:
    """Fecha del día ``dias_ciclo`` (día 73) contada desde la recepción."""
    i = a_fecha(inicio)
    p = _params(con, i)
    dias = int(_param(p, "dias_ciclo"))
    return i + timedelta(days=dias - 1 if bool(_param(p, "ciclo_cierre_inclusivo")) else dias)


def fase_de_dia(con: sqlite3.Connection, fecha: Any, dia: int) -> str:
    """Fase del ciclo: inicio (1-21), crecimiento (22-50) o acabado (51 en
    adelante, hasta el faenado), según ``fase_inicio_dia_fin`` y
    ``fase_crecimiento_dia_fin``."""
    p = _params(con, fecha)
    if dia <= int(_param(p, "fase_inicio_dia_fin")):
        return "INICIO"
    if dia <= int(_param(p, "fase_crecimiento_dia_fin")):
        return "CRECIMIENTO"
    return "ACABADO"


def calendario_ciclos(con: sqlite3.Connection, ejercicio: int) -> list[dict[str, Any]]:
    """Ciclos programados del ejercicio (inicio y día 73 del POA) y, si ya
    se abrió, el ciclo real con sus fechas, aves y estado."""
    _entero(ejercicio, "ejercicio", 2000, 2100)
    p = obtener_parametros_vigentes(con, ejercicio)
    dias = int(_param(p, "dias_ciclo"))
    ciclos = int(_param(p, "ciclos_por_ejercicio"))
    inicio_ej = inicio_ejercicio(ejercicio, int(_param(p, "mes_inicio_ejercicio")))
    filas = []
    for n in range(1, ciclos + 1):
        inicio = inicio_ej + timedelta(days=(n - 1) * dias)
        real = con.execute("SELECT * FROM ciclos_produccion WHERE ejercicio = ? AND numero = ?",
                           (ejercicio, n)).fetchone()
        filas.append({
            "ejercicio": ejercicio, "numero": n, "inicio_programado": inicio,
            "cierre_programado": fecha_de_cierre(con, inicio),
            "aves_programadas": int(_param(p, "aves_por_ciclo")),
            "ciclo_id": int(real["id"]) if real else None,
            "inicio_real": real["fecha_inicio"] if real else None,
            "cierre_real": real["fecha_cierre"] if real else None,
            "aves_iniciales": int(real["aves_iniciales"]) if real else None,
            "estado": real["estado"] if real else "PROGRAMADO",
        })
    return filas


# ==========================================================================
# 1. GALPONES Y DENSIDAD (POL-03 Art. 1 (i))
# ==========================================================================
def _superficie_cm2(superficie_m2: Any) -> int:
    valor = a_decimal(superficie_m2, "superficie").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if valor <= 0:
        raise DatosInvalidosError("La superficie del galpón debe ser mayor a cero.")
    return int(valor * CIEN)


def densidad(aves: int, superficie_cm2: int) -> Decimal:
    """Aves por metro cuadrado (2 decimales)."""
    return (Decimal(aves) * CIEN / Decimal(superficie_cm2)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def capacidad_galpon(con: sqlite3.Connection, galpon: Mapping[str, Any], fecha: Any) -> int:
    """Aves que admite el galpón sin superar la densidad máxima."""
    p = _params(con, fecha)
    maximo = Decimal(str(_param(p, "densidad_max_aves_m2")))
    return int(math.floor(Decimal(int(galpon["superficie_cm2"])) / CIEN * maximo))


def registrar_galpon(con: sqlite3.Connection, sesion: Sesion, codigo: str, nombre: str, superficie_m2: Any,
                     fotos_url: str | None = None) -> int:
    """Alta de un galpón con su superficie útil (base del control de densidad)."""
    seguridad.exigir_permiso(con, sesion, "produccion", "EDITAR")
    cod = _texto(codigo, "código", 20).upper()
    nom = _texto(nombre, "nombre", 120)
    sup = _superficie_cm2(superficie_m2)
    fotos = _opcional(fotos_url, "fotografías", 500)
    with transaccion(con):
        if con.execute("SELECT 1 FROM galpones WHERE codigo = ?", (cod,)).fetchone():
            raise DatosInvalidosError(f"Ya existe el galpón {cod}.")
        cur = con.execute("INSERT INTO galpones (codigo, nombre, superficie_cm2, fotos_url, registrado_por, "
                          "registrado_en) VALUES (?,?,?,?,?,?)",
                          (cod, nom, sup, fotos, sesion.nombre_usuario, _iso(_ahora())))
        gid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "galpones", "INSERT", gid,
                            despues={"codigo": cod, "nombre": nom, "superficie_m2": Decimal(sup) / CIEN,
                                     "fotos_url": fotos})
    return gid


def actualizar_galpon(con: sqlite3.Connection, sesion: Sesion, galpon_id: int, *, fotos_url: str | None = None,
                      activo: bool | None = None) -> None:
    """Actualiza las fotografías (POL-03 Art. 3 i) o desactiva el galpón."""
    seguridad.exigir_permiso(con, sesion, "produccion", "EDITAR")
    with transaccion(con):
        g = _galpon(con, galpon_id)
        cambios: dict[str, Any] = {}
        if fotos_url is not None:
            cambios["fotos_url"] = _opcional(fotos_url, "fotografías", 500)
        if activo is not None:
            if not activo and con.execute("SELECT 1 FROM lotes WHERE galpon_id = ? AND estado = 'EN_CRIANZA'",
                                          (galpon_id,)).fetchone():
                raise ReglaNegocioError("El galpón tiene un lote en crianza: no se puede desactivar.",
                                        codigo="GALPON_OCUPADO")
            cambios["activo"] = int(bool(activo))
        if not cambios:
            return
        antes = {k: g[k] for k in cambios}
        sets = ", ".join(f"{k} = ?" for k in cambios)
        con.execute(f"UPDATE galpones SET {sets}, actualizado_en = ? WHERE id = ?",
                    (*cambios.values(), _iso(_ahora()), galpon_id))
        auditoria.registrar(con, sesion, "galpones", "UPDATE", galpon_id, antes=antes, despues=cambios)


def _galpon(con: sqlite3.Connection, galpon_id: int) -> sqlite3.Row:
    if isinstance(galpon_id, bool) or not isinstance(galpon_id, int):
        raise DatosInvalidosError("Indique el galpón.")
    g = con.execute("SELECT * FROM galpones WHERE id = ?", (galpon_id,)).fetchone()
    if g is None:
        raise DatosInvalidosError(f"No existe el galpón {galpon_id}.")
    return g


def listar_galpones(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> list[dict[str, Any]]:
    """Galpones con superficie, capacidad a densidad máxima y lote en crianza."""
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    filas = []
    for g in con.execute("SELECT * FROM galpones ORDER BY codigo"):
        lote = con.execute("SELECT codigo, aves_iniciales FROM lotes WHERE galpon_id = ? AND estado = 'EN_CRIANZA'",
                           (int(g["id"]),)).fetchone()
        filas.append({**dict(g), "superficie_m2": Decimal(int(g["superficie_cm2"])) / CIEN,
                      "capacidad": capacidad_galpon(con, g, f), "lote_actual": lote["codigo"] if lote else None,
                      "aves_lote": int(lote["aves_iniciales"]) if lote else None})
    return filas


# ==========================================================================
# 2. APERTURA DEL CICLO (FIN-TRA-01; PC-03 §V.A)
# ==========================================================================
def _insumo_pollitos(con: sqlite3.Connection) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM insumos WHERE cuenta_inventario = ? AND transferencia_inmediata = 1 "
                       "ORDER BY id LIMIT 1", (CUENTA_POLLITOS,)).fetchone()
    if fila is None:
        raise ReglaNegocioError("No existe el insumo de pollitos BB (cuenta 1.1.03.01).", codigo="SIN_INSUMO_POLLITOS")
    return fila


def recepciones_pollitos_disponibles(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Traslados de pollitos BB al centro 6.1.01.01 (sufijo 01) hechos el día
    de la recepción y aún no asignados a un ciclo."""
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    insumo = _insumo_pollitos(con)
    filas = []
    for k in con.execute(
            """SELECT k.* FROM kardex k WHERE k.insumo_id = ? AND k.tipo = 'SALIDA_CONSUMO' AND k.centro_costo = ?
                  AND k.sufijo = ? AND NOT EXISTS (SELECT 1 FROM ciclos_produccion c WHERE c.kardex_id = k.id)
                ORDER BY k.fecha, k.id""", (int(insumo["id"]), CENTRO_CARNE, SUFIJO_POLLITOS)):
        filas.append({"kardex_id": int(k["id"]), "fecha": k["fecha"], "aves": int(de_mil(int(k["cantidad_mil"]))),
                      "valor": de_centavos(int(k["valor_cent"])), "asiento_id": int(k["asiento_id"]),
                      "documento_ref": k["documento_ref"], "proveedor": _proveedor_de_traslado(con, k)})
    return filas


def _proveedor_de_traslado(con: sqlite3.Connection, salida: sqlite3.Row) -> str | None:
    fila = con.execute(
        """SELECT p.nombre FROM kardex e JOIN recepciones r ON r.id = e.recepcion_id
             JOIN ordenes_compra o ON o.id = r.orden_id JOIN proveedores p ON p.id = o.proveedor_id
            WHERE e.insumo_id = ? AND e.tipo = 'ENTRADA_COMPRA' AND e.fecha = ? AND e.id < ?
              AND COALESCE(e.documento_ref, '') = COALESCE(?, '')
            ORDER BY e.id DESC LIMIT 1""",
        (int(salida["insumo_id"]), salida["fecha"], int(salida["id"]), salida["documento_ref"])).fetchone()
    return fila["nombre"] if fila else None


def abrir_ciclo(con: sqlite3.Connection, sesion: Sesion, kardex_id: int, asignacion: Mapping[int, int],
                certificado_sanitario: str | None = None) -> int:
    """Abre el ciclo productivo (FIN-TRA-01) y distribuye las aves en lotes.

    El costo de los pollitos ya se trasladó a 6.1.01.01 sufijo 01 el mismo día
    de la recepción (``inventarios.registrar_entrada_compra``, PC-03); la
    apertura vincula ese asiento y fija la fecha del día 1 y del día 73.

    Args:
        kardex_id: traslado de pollitos BB al centro (de
            :func:`recepciones_pollitos_disponibles`).
        asignacion: ``{galpon_id: aves}``; cada lote hasta ``lote_aves`` y con
            densidad no mayor a ``densidad_max_aves_m2``.
        certificado_sanitario: certificado sanitario del proveedor (POL-03
            Art. 2 i).

    Returns:
        id del ciclo.
    """
    _exigir_finanzas(con, sesion, "CREAR")
    if isinstance(kardex_id, bool) or not isinstance(kardex_id, int):
        raise DatosInvalidosError("Indique la recepción de pollitos BB.")
    if not asignacion:
        raise DatosInvalidosError("Asigne las aves a por lo menos un galpón.")
    certificado = _opcional(certificado_sanitario, "certificado sanitario", 120)
    with transaccion(con):
        insumo = _insumo_pollitos(con)
        k = con.execute("SELECT * FROM kardex WHERE id = ?", (kardex_id,)).fetchone()
        if k is None or int(k["insumo_id"]) != int(insumo["id"]) or k["tipo"] != "SALIDA_CONSUMO" \
                or k["centro_costo"] != CENTRO_CARNE or k["sufijo"] != SUFIJO_POLLITOS:
            raise ReglaNegocioError("El movimiento no es un traslado de pollitos BB a 6.1.01.01 sufijo 01.",
                                    codigo="TRASLADO_NO_VALIDO")
        if con.execute("SELECT 1 FROM ciclos_produccion WHERE kardex_id = ?", (kardex_id,)).fetchone():
            raise ReglaNegocioError("Esa recepción de pollitos ya abrió un ciclo.", codigo="TRASLADO_YA_ASIGNADO")
        abierto = con.execute("SELECT ejercicio, numero FROM ciclos_produccion WHERE estado = 'ABIERTO'").fetchone()
        if abierto:
            raise ReglaNegocioError(f"El ciclo {abierto['numero']} del ejercicio {abierto['ejercicio']} sigue en "
                                    "crianza: se faena antes de abrir el siguiente.", codigo="CICLO_ABIERTO")
        inicio = a_fecha(k["fecha"])
        p = _params(con, inicio)
        aves = int(de_mil(int(k["cantidad_mil"])))
        lote_max = int(_param(p, "lote_aves"))
        maximo = Decimal(str(_param(p, "densidad_max_aves_m2")))
        total = 0
        lotes = []
        for galpon_id, n in asignacion.items():
            g = _galpon(con, galpon_id)
            n = _entero(n, f"aves del galpón {g['codigo']}", 1)
            if not g["activo"]:
                raise ReglaNegocioError(f"El galpón {g['codigo']} está inactivo.", codigo="GALPON_INACTIVO")
            if con.execute("SELECT 1 FROM lotes WHERE galpon_id = ? AND estado = 'EN_CRIANZA'",
                           (galpon_id,)).fetchone():
                raise ReglaNegocioError(f"El galpón {g['codigo']} tiene un lote en crianza.", codigo="GALPON_OCUPADO")
            if n > lote_max:
                raise ReglaNegocioError(f"El lote del galpón {g['codigo']} tiene {n} aves: el máximo por lote es "
                                        f"{lote_max} (lotes de faenado).", codigo="LOTE_EXCEDE_TAMANO")
            d = densidad(n, int(g["superficie_cm2"]))
            if d > maximo:
                raise ReglaNegocioError(
                    f"Densidad de {str(d).replace('.', ',')} aves/m² en el galpón {g['codigo']}: el máximo es "
                    f"{maximo} aves/m² (POL-03 Art. 1). Capacidad: {capacidad_galpon(con, g, inicio)} aves.",
                    codigo="DENSIDAD_EXCEDIDA")
            total += n
            lotes.append((g, n, d))
        if total != aves:
            raise ReglaNegocioError(f"Se asignaron {total} aves y se recibieron {aves}: la distribución debe "
                                    "coincidir con los pollitos recibidos.", codigo="ASIGNACION_NO_CUADRA")
        ciclo = inventarios.ciclo_de_fecha(con, inicio)
        if con.execute("SELECT 1 FROM ciclos_produccion WHERE ejercicio = ? AND numero = ?",
                       (ciclo["ejercicio"], ciclo["ciclo"])).fetchone():
            raise ReglaNegocioError(f"Ya existe el ciclo {ciclo['ciclo']} del ejercicio {ciclo['ejercicio']}.",
                                    codigo="CICLO_DUPLICADO")
        cierre = fecha_de_cierre(con, inicio)
        cur = con.execute(
            """INSERT INTO ciclos_produccion (ejercicio, numero, fecha_inicio, fecha_cierre, aves_iniciales, insumo_id,
                   proveedor, certificado_sanitario, costo_pollitos_cent, kardex_id, asiento_id, estado,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ciclo["ejercicio"], ciclo["ciclo"], inicio.isoformat(), cierre.isoformat(), aves, int(insumo["id"]),
             _proveedor_de_traslado(con, k), certificado, int(k["valor_cent"]), kardex_id, int(k["asiento_id"]),
             "ABIERTO", sesion.nombre_usuario, _iso(_ahora())))
        cid = int(cur.lastrowid)
        for g, n, d in lotes:
            codigo = f"{ciclo['ejercicio']}-C{ciclo['ciclo']}-{g['codigo']}"
            token = f"{codigo}-{secrets.token_hex(3).upper()}"
            con.execute("INSERT INTO lotes (ciclo_id, codigo, galpon_id, aves_iniciales, densidad, qr_token, estado) "
                        "VALUES (?,?,?,?,?,?,?)", (cid, codigo, int(g["id"]), n, str(d), token, "EN_CRIANZA"))
        auditoria.registrar(con, sesion, "ciclos_produccion", "INSERT", cid,
                            despues={"ejercicio": ciclo["ejercicio"], "numero": ciclo["ciclo"],
                                     "fecha_inicio": inicio.isoformat(), "fecha_cierre": cierre.isoformat(),
                                     "aves": aves, "costo_pollitos": de_centavos(int(k["valor_cent"])),
                                     "asiento_id": int(k["asiento_id"]),
                                     "lotes": {g["codigo"]: n for g, n, _ in lotes}})
    return cid


def confirmar_recepcion(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> None:
    """Firma «Recepción conforme» de la Unidad Técnica en la FIN-TRA-01."""
    _exigir_unidad_tecnica(con, sesion)
    with transaccion(con):
        c = _ciclo(con, ciclo_id)
        if c["recepcion_conforme_por"]:
            raise ReglaNegocioError("La recepción ya fue confirmada.", codigo="RECEPCION_CONFIRMADA")
        momento = _iso(_ahora())
        con.execute("UPDATE ciclos_produccion SET recepcion_conforme_por = ?, recepcion_conforme_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, momento, ciclo_id))
        auditoria.registrar(con, sesion, "ciclos_produccion", "UPDATE", ciclo_id, antes={"recepcion_conforme_por": None},
                            despues={"recepcion_conforme_por": sesion.nombre_usuario})


def _ciclo(con: sqlite3.Connection, ciclo_id: int) -> sqlite3.Row:
    if isinstance(ciclo_id, bool) or not isinstance(ciclo_id, int):
        raise DatosInvalidosError("Indique el ciclo.")
    c = con.execute("SELECT * FROM ciclos_produccion WHERE id = ?", (ciclo_id,)).fetchone()
    if c is None:
        raise DatosInvalidosError(f"No existe el ciclo {ciclo_id}.")
    return c


def _lote(con: sqlite3.Connection, lote_id: int) -> sqlite3.Row:
    if isinstance(lote_id, bool) or not isinstance(lote_id, int):
        raise DatosInvalidosError("Indique el lote.")
    l = con.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
    if l is None:
        raise DatosInvalidosError(f"No existe el lote {lote_id}.")
    return l


# ==========================================================================
# 3. BITÁCORA DIARIA (FIN-TRA-02) Y MORTALIDAD ACUMULADA
# ==========================================================================
def semaforo_mortalidad(con: sqlite3.Connection, fecha: Any, pct: Decimal) -> str:
    """Verde hasta ``mortalidad_alerta`` (3 %), amarillo hasta
    ``mortalidad_anormal`` (5 %), rojo por encima (POA §5.2)."""
    p = _params(con, fecha)
    if pct <= Decimal(str(_param(p, "mortalidad_alerta"))):
        return "VERDE"
    if pct <= Decimal(str(_param(p, "mortalidad_anormal"))):
        return "AMARILLO"
    return "ROJO"


def muertas_ciclo(con: sqlite3.Connection, ciclo_id: int, hasta: Any = None) -> int:
    """Aves muertas acumuladas del ciclo (todas sus lotes) hasta una fecha."""
    sql = ("SELECT COALESCE(SUM(b.aves_muertas), 0) FROM bitacora_diaria b JOIN lotes l ON l.id = b.lote_id "
           "WHERE l.ciclo_id = ?")
    args: list[Any] = [ciclo_id]
    if hasta is not None:
        sql += " AND b.fecha <= ?"
        args.append(a_fecha(hasta).isoformat())
    return int(con.execute(sql, args).fetchone()[0])


def muertas_lote(con: sqlite3.Connection, lote_id: int) -> int:
    return int(con.execute("SELECT COALESCE(SUM(aves_muertas), 0) FROM bitacora_diaria WHERE lote_id = ?",
                           (lote_id,)).fetchone()[0])


def mortalidad_ciclo(con: sqlite3.Connection, ciclo_id: int, hasta: Any = None) -> dict[str, Any]:
    """Mortalidad ACUMULADA = muertas acumuladas ÷ aves vivas al inicio del
    ciclo (CI-FIN-05 actividad 2; regla de oro: nunca el % del día aislado)."""
    c = _ciclo(con, ciclo_id)
    muertas = muertas_ciclo(con, ciclo_id, hasta)
    pct = _pct(Decimal(muertas) / Decimal(int(c["aves_iniciales"])))
    fecha = a_fecha(hasta) if hasta is not None else a_fecha(c["fecha_inicio"])
    return {"aves_iniciales": int(c["aves_iniciales"]), "muertas": muertas, "vivas": int(c["aves_iniciales"]) - muertas,
            "pct": pct, "semaforo": semaforo_mortalidad(con, fecha, pct)}


def registrar_bitacora(
    con: sqlite3.Connection, sesion: Sesion, lote_id: int, fecha: Any, alimento_kg: Any, aves_muertas: int, *,
    causa_presunta: str | None = None, novedad_sanitaria: str | None = None, tratamiento: str | None = None,
    signos_alerta: bool = False, cumple_ci_fin_11: bool = True, ahora: datetime | None = None,
) -> dict[str, Any]:
    """Registra el día del lote en la Bitácora Diaria de Ciclo (FIN-TRA-02).

    Calcula el día y la fase del ciclo, la mortalidad acumulada del ciclo y su
    semáforo. Si la mortalidad cruza el umbral de alerta (3 %) o el anormal
    (5 %), o se marcan signos clínicos o un incumplimiento de CI-FIN-11, emite
    la alerta sanitaria a la Gerencia el mismo día (CI-FIN-11 Paso 4).

    Returns:
        ``{id, dia, fase, muertas_acumuladas, pct, semaforo, alertas}``.
    """
    _exigir_unidad_tecnica(con, sesion)
    momento = _ahora(ahora)
    f = a_fecha(fecha)
    muertas = _entero(aves_muertas, "aves muertas", 0)
    kg_mil = a_mil(alimento_kg, "cantidad de alimento", permitir_cero=True)
    causa = _opcional(causa_presunta, "causa presunta", 300)
    novedad = _opcional(novedad_sanitaria, "novedad sanitaria", 500)
    trat = _opcional(tratamiento, "tratamiento", 300)
    if muertas > 0 and causa is None:
        raise DatosInvalidosError("Indique la causa presunta de la mortalidad (FIN-TRA-02).")
    if not cumple_ci_fin_11 and novedad is None:
        raise DatosInvalidosError("Describa en la novedad sanitaria qué punto de control de CI-FIN-11 no se cumplió.")
    if f > momento.date():
        raise DatosInvalidosError("La bitácora no admite fechas futuras.")
    with transaccion(con):
        lote = _lote(con, lote_id)
        if lote["estado"] != "EN_CRIANZA":
            raise ReglaNegocioError("El lote ya fue faenado: su bitácora está cerrada.", codigo="LOTE_FAENADO")
        c = _ciclo(con, int(lote["ciclo_id"]))
        dia = dia_del_ciclo(con, c["fecha_inicio"], f)
        if dia < 1:
            raise ReglaNegocioError("La fecha es anterior a la recepción de los pollitos.", codigo="FECHA_ANTERIOR_CICLO")
        posterior = con.execute(
            "SELECT MAX(b.fecha) FROM bitacora_diaria b JOIN lotes l ON l.id = b.lote_id WHERE l.ciclo_id = ?",
            (int(c["id"]),)).fetchone()[0]
        if posterior and posterior > f.isoformat():
            raise ReglaNegocioError(f"Ya hay bitácora del {fecha_pantalla(posterior)}: los días se registran en orden "
                                    "para que el % acumulado sea exacto.", codigo="BITACORA_FUERA_DE_ORDEN")
        if con.execute("SELECT 1 FROM bitacora_diaria WHERE lote_id = ? AND fecha = ?",
                       (lote_id, f.isoformat())).fetchone():
            raise DatosInvalidosError(f"El lote {lote['codigo']} ya tiene bitácora del {fecha_pantalla(f)}.")
        vivas = int(lote["aves_iniciales"]) - muertas_lote(con, lote_id)
        if muertas > vivas:
            raise ReglaNegocioError(f"El lote tiene {vivas} aves vivas: no pueden morir {muertas}.",
                                    codigo="MUERTAS_EXCEDEN_VIVAS")
        previo = muertas_ciclo(con, int(c["id"]), f)
        acumuladas = previo + muertas
        iniciales = Decimal(int(c["aves_iniciales"]))
        pct_previo = _pct(Decimal(previo) / iniciales)
        pct = _pct(Decimal(acumuladas) / iniciales)
        semaforo = semaforo_mortalidad(con, f, pct)
        fase = fase_de_dia(con, f, dia)
        cur = con.execute(
            """INSERT INTO bitacora_diaria (lote_id, fecha, dia_ciclo, fase, alimento_kg_mil, aves_muertas,
                   causa_presunta, novedad_sanitaria, tratamiento, signos_alerta, cumple_ci_fin_11, muertas_acum_ciclo,
                   mortalidad_acum, semaforo, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lote_id, f.isoformat(), dia, fase, kg_mil, muertas, causa, novedad, trat, int(bool(signos_alerta)),
             int(bool(cumple_ci_fin_11)), acumuladas, str(pct), semaforo, sesion.nombre_usuario, _iso(momento)))
        bid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "bitacora_diaria", "INSERT", bid,
                            despues={"lote": lote["codigo"], "fecha": f.isoformat(), "dia": dia, "fase": fase,
                                     "alimento_kg": de_mil(kg_mil), "aves_muertas": muertas, "causa": causa,
                                     "muertas_acumuladas": acumuladas, "pct_acumulado": pct, "semaforo": semaforo})
        p = _params(con, f)
        alerta, anormal = Decimal(str(_param(p, "mortalidad_alerta"))), Decimal(str(_param(p, "mortalidad_anormal")))
        alertas = []
        if pct_previo <= anormal < pct:
            alertas.append(_alerta_automatica(con, sesion, c, lote, bid, f, momento, "MORTALIDAD_ANORMAL",
                                              f"Mortalidad acumulada de {pct_texto(pct)} supera el {pct_texto(anormal)}: "
                                              "corresponde informe veterinario y baja anormal (CI-FIN-05).", pct))
        elif pct_previo <= alerta < pct:
            alertas.append(_alerta_automatica(con, sesion, c, lote, bid, f, momento, "MORTALIDAD_ALERTA",
                                              f"Mortalidad acumulada de {pct_texto(pct)} supera la alerta de "
                                              f"{pct_texto(alerta)} (POA §5.2).", pct))
        if signos_alerta or not cumple_ci_fin_11:
            alertas.append(_alerta_automatica(con, sesion, c, lote, bid, f, momento, "SIGNOS_CLINICOS",
                                              novedad or "Signos clínicos de alerta registrados en la bitácora.", pct))
    return {"id": bid, "dia": dia, "fase": fase, "muertas_acumuladas": acumuladas, "pct": pct, "semaforo": semaforo,
            "alertas": alertas}


def listar_bitacora(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int,
                    lote_id: int | None = None) -> list[dict[str, Any]]:
    """Días registrados del ciclo (o de un lote), del más reciente al más antiguo."""
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    sql = ("SELECT b.*, l.codigo AS lote, (SELECT m.id FROM bajas_mortalidad m WHERE m.bitacora_id = b.id) AS baja_id "
           "FROM bitacora_diaria b JOIN lotes l ON l.id = b.lote_id WHERE l.ciclo_id = ?")
    args: list[Any] = [ciclo_id]
    if lote_id is not None:
        sql += " AND b.lote_id = ?"
        args.append(lote_id)
    return [{**dict(f), "alimento_kg": de_mil(int(f["alimento_kg_mil"])), "mortalidad_acum": Decimal(f["mortalidad_acum"])}
            for f in con.execute(sql + " ORDER BY b.fecha DESC, b.id DESC", args)]


def revisar_semana(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, semana: int,
                   observacion: str | None = None) -> int:
    """Revisión semanal de la bitácora por la Gerencia General (POL-03 Art. 2;
    firma «Revisado por» de la FIN-TRA-02)."""
    _exigir_gerencia(con, sesion)
    _entero(semana, "semana", 1)
    obs = _opcional(observacion, "observación", 500)
    with transaccion(con):
        c = _ciclo(con, ciclo_id)
        p = _params(con, c["fecha_inicio"])
        dias = int(_param(p, "muestreo_peso_dias"))
        desde = a_fecha(c["fecha_inicio"]) + timedelta(days=(semana - 1) * dias)
        hasta = desde + timedelta(days=dias - 1)
        hay = con.execute("SELECT COUNT(*) FROM bitacora_diaria b JOIN lotes l ON l.id = b.lote_id "
                          "WHERE l.ciclo_id = ? AND b.fecha BETWEEN ? AND ?",
                          (ciclo_id, desde.isoformat(), hasta.isoformat())).fetchone()[0]
        if not hay:
            raise ReglaNegocioError(f"La semana {semana} no tiene días registrados.", codigo="SEMANA_SIN_BITACORA")
        if con.execute("SELECT 1 FROM revisiones_bitacora WHERE ciclo_id = ? AND semana = ?",
                       (ciclo_id, semana)).fetchone():
            raise ReglaNegocioError(f"La semana {semana} ya fue revisada.", codigo="SEMANA_REVISADA")
        cur = con.execute("INSERT INTO revisiones_bitacora (ciclo_id, semana, observacion, revisado_por, revisado_en) "
                          "VALUES (?,?,?,?,?)", (ciclo_id, semana, obs, sesion.nombre_usuario, _iso(_ahora())))
        rid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "revisiones_bitacora", "INSERT", rid,
                            despues={"ciclo_id": ciclo_id, "semana": semana, "observacion": obs})
    return rid


def listar_revisiones(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    return [dict(f) for f in con.execute("SELECT * FROM revisiones_bitacora WHERE ciclo_id = ? ORDER BY semana",
                                         (ciclo_id,))]


# ==========================================================================
# 4. ALERTAS SANITARIAS (CI-FIN-11, Pasos 4 y 5)
# ==========================================================================
def _insertar_alerta(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, lote_id: int | None,
                     bitacora_id: int | None, origen: str, tipo: str, deteccion: date, momento: datetime,
                     descripcion: str, pct: Decimal | None, causa_demora: str | None) -> int:
    mismo_dia = int(deteccion == momento.date())
    if not mismo_dia and causa_demora is None:
        raise DatosInvalidosError("CI-FIN-11 Paso 4: la alerta se reporta el mismo día; indique la causa de la demora.")
    cur = con.execute(
        """INSERT INTO alertas_sanitarias (ciclo_id, lote_id, bitacora_id, origen, tipo, fecha_deteccion, fecha_reporte,
               mismo_dia, causa_demora, descripcion, mortalidad_acum, reportado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ciclo_id, lote_id, bitacora_id, origen, tipo, deteccion.isoformat(), _iso(momento), mismo_dia,
         None if mismo_dia else causa_demora, descripcion, str(pct) if pct is not None else None,
         sesion.nombre_usuario))
    aid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "alertas_sanitarias", "INSERT", aid,
                        despues={"tipo": tipo, "origen": origen, "fecha_deteccion": deteccion.isoformat(),
                                 "mismo_dia": bool(mismo_dia), "descripcion": descripcion})
    return aid


def _alerta_automatica(con: sqlite3.Connection, sesion: Sesion, ciclo: sqlite3.Row, lote: sqlite3.Row,
                       bitacora_id: int | None, deteccion: date, momento: datetime, tipo: str, descripcion: str,
                       pct: Decimal | None) -> int:
    demora = None if deteccion == momento.date() else \
        f"La bitácora del {fecha_pantalla(deteccion)} se registró el {fecha_pantalla(momento.date())}."
    return _insertar_alerta(con, sesion, int(ciclo["id"]), int(lote["id"]), bitacora_id, "AUTOMATICA", tipo,
                            deteccion, momento, f"Lote {lote['codigo']}: {descripcion}", pct, demora)


def reportar_alerta(con: sqlite3.Connection, sesion: Sesion, lote_id: int, fecha_deteccion: Any, tipo: str,
                    descripcion: str, causa_demora: str | None = None, ahora: datetime | None = None) -> int:
    """Reporte de la Unidad Técnica a la Gerencia ante cualquier señal de
    alerta, el mismo día y con independencia del % acumulado (CI-FIN-11 Paso 4)."""
    _exigir_unidad_tecnica(con, sesion)
    if tipo not in TIPOS_ALERTA:
        raise DatosInvalidosError(f"Tipo de alerta no válido. Use: {', '.join(TIPOS_ALERTA)}.")
    momento = _ahora(ahora)
    deteccion = a_fecha(fecha_deteccion)
    if deteccion > momento.date():
        raise DatosInvalidosError("La fecha de detección no puede ser futura.")
    texto = _texto(descripcion, "descripción", 500)
    demora = _opcional(causa_demora, "causa de la demora", 300)
    with transaccion(con):
        lote = _lote(con, lote_id)
        c = _ciclo(con, int(lote["ciclo_id"]))
        pct = mortalidad_ciclo(con, int(c["id"]), deteccion)["pct"]
        return _insertar_alerta(con, sesion, int(c["id"]), lote_id, None, "UNIDAD_TECNICA", tipo, deteccion, momento,
                                f"Lote {lote['codigo']}: {texto}", pct, demora)


def atender_alerta(con: sqlite3.Connection, sesion: Sesion, alerta_id: int, medida: str, decision: str) -> None:
    """Decisión de la Gerencia sobre la alerta (CI-FIN-11 Paso 5): consulta
    veterinaria extraordinaria, aislamiento o refuerzo de la desinfección."""
    _exigir_gerencia(con, sesion)
    if medida not in MEDIDAS:
        raise DatosInvalidosError(f"Medida no válida. Use: {', '.join(MEDIDAS)}.")
    texto = _texto(decision, "decisión", 500)
    with transaccion(con):
        a = con.execute("SELECT * FROM alertas_sanitarias WHERE id = ?", (alerta_id,)).fetchone()
        if a is None:
            raise DatosInvalidosError(f"No existe la alerta {alerta_id}.")
        if a["atendida_por"]:
            raise ReglaNegocioError("La alerta ya fue atendida.", codigo="ALERTA_ATENDIDA")
        con.execute("UPDATE alertas_sanitarias SET medida = ?, decision = ?, atendida_por = ?, atendida_en = ? "
                    "WHERE id = ?", (medida, texto, sesion.nombre_usuario, _iso(_ahora()), alerta_id))
        auditoria.registrar(con, sesion, "alertas_sanitarias", "UPDATE", alerta_id, antes={"atendida_por": None},
                            despues={"medida": medida, "decision": texto})


def listar_alertas(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int | None = None,
                   pendientes: bool = False) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    sql, args = "SELECT * FROM alertas_sanitarias WHERE 1 = 1", []
    if ciclo_id is not None:
        sql += " AND ciclo_id = ?"
        args.append(ciclo_id)
    if pendientes:
        sql += " AND atendida_por IS NULL"
    return [dict(f) for f in con.execute(sql + " ORDER BY fecha_deteccion DESC, id DESC", args)]


# ==========================================================================
# 5. BAJA POR MORTALIDAD (FIN-TRA-04; CI-FIN-05; PC-03)
# ==========================================================================
ORIGEN_CIERRE_COSTOS = "COSTOS_CIERRE"


def _etiquetas_ciclo(con: sqlite3.Connection) -> dict[int, int]:
    """Asientos que pertenecen expresamente a un ciclo: apertura (traslado de
    pollitos), bajas, consumos y CIF registrados con su ciclo, corte de MOD y
    cierre de costos (Fase 7)."""
    etiquetas: dict[int, int] = {}
    consultas = ["SELECT asiento_id, id FROM ciclos_produccion",
                 "SELECT asiento_id, ciclo_id FROM bajas_mortalidad WHERE asiento_id IS NOT NULL"]
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "consumos_costo" in tablas:
        consultas += ["SELECT asiento_id, ciclo_id FROM consumos_costo WHERE ciclo_id IS NOT NULL",
                      "SELECT asiento_id, ciclo_id FROM cif_registrados WHERE ciclo_id IS NOT NULL",
                      "SELECT asiento_id, ciclo_id FROM cortes_mod WHERE asiento_id IS NOT NULL",
                      "SELECT asiento_id, ciclo_id FROM costos_ciclo"]
    for sql in consultas:
        for asiento_id, ciclo_id in con.execute(sql):
            etiquetas[int(asiento_id)] = int(ciclo_id)
    return etiquetas


def _ciclo_por_fecha(ciclos: list[tuple[int, str, str]], fecha: str) -> int | None:
    """Ciclo al que se imputa un costo sin etiqueta: el más antiguo cuyo
    período (recepción a cierre) contiene la fecha; si cae entre ciclos
    (vacío sanitario), el ciclo siguiente; si es posterior a todos, el último."""
    contienen = [c for c in ciclos if c[1] <= fecha <= c[2]]
    if contienen:
        return contienen[0][0]
    siguientes = [c for c in ciclos if c[1] > fecha]
    if siguientes:
        return siguientes[0][0]
    return ciclos[-1][0] if ciclos else None


def lineas_centro_ciclo(con: sqlite3.Connection, ciclo_id: int, hasta: Any,
                        incluir_cierre: bool = False) -> list[dict[str, Any]]:
    """Líneas del centro 6.1.01.01 imputadas al ciclo hasta la fecha.

    Cada línea se imputa al ciclo de su etiqueta (apertura, baja, consumo,
    CIF, corte de MOD o cierre) o, si no la tiene (planillas, jornales
    eventuales, asientos manuales), al ciclo vigente en su fecha; así dos
    ciclos que se superponen unos días no se mezclan. Sin ``incluir_cierre``
    se omite el traslado del día 73.
    """
    _ciclo(con, ciclo_id)
    h = a_fecha(hasta).isoformat()
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    ciclos = []
    for c in con.execute("SELECT id, fecha_inicio, fecha_cierre FROM ciclos_produccion ORDER BY fecha_inicio, id"):
        fin = c["fecha_cierre"]
        if "costos_ciclo" in tablas:
            cerrado = con.execute("SELECT fecha_cierre FROM costos_ciclo WHERE ciclo_id = ?", (c["id"],)).fetchone()
            if cerrado:
                fin = max(fin, cerrado["fecha_cierre"])
        ciclos.append((int(c["id"]), c["fecha_inicio"], fin))
    etiquetas = _etiquetas_ciclo(con)
    lineas = []
    for f in con.execute(
            """SELECT d.asiento_id, a.fecha, a.origen, a.glosa, d.linea, d.sufijo, d.debe_cent, d.haber_cent
                 FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
                WHERE d.cuenta_codigo = ? AND a.fecha <= ? ORDER BY a.fecha, a.id, d.linea""", (CENTRO_CARNE, h)):
        if f["origen"] == ORIGEN_CIERRE_COSTOS and not incluir_cierre:
            continue
        destino = etiquetas.get(int(f["asiento_id"])) or _ciclo_por_fecha(ciclos, f["fecha"])
        if destino == ciclo_id:
            lineas.append({"asiento_id": int(f["asiento_id"]), "fecha": f["fecha"], "glosa": f["glosa"],
                           "sufijo": f["sufijo"], "debe": de_centavos(int(f["debe_cent"])),
                           "haber": de_centavos(int(f["haber_cent"]))})
    return lineas


def costo_ciclo_por_sufijo(con: sqlite3.Connection, ciclo_id: int, hasta: Any,
                           incluir_cierre: bool = False) -> dict[str, Decimal]:
    """Costo del ciclo en el centro 6.1.01.01 por sufijo hasta la fecha (el
    centro es el «activo transitorio acumulador» del lote, PC-03), según la
    imputación de :func:`lineas_centro_ciclo`."""
    sumas: dict[str, Decimal] = {}
    for l in lineas_centro_ciclo(con, ciclo_id, hasta, incluir_cierre):
        sumas[l["sufijo"]] = sumas.get(l["sufijo"], CERO) + l["debe"] - l["haber"]
    return {k: v for k, v in sorted(sumas.items()) if v != 0}


def clasificar_mortalidad(con: sqlite3.Connection, fecha: Any, pct_acumulado: Decimal) -> str:
    """NORMAL si el % ACUMULADO no supera ``mortalidad_anormal`` (5 %); si lo
    supera, ANORMAL (CI-FIN-05; regla de oro)."""
    p = _params(con, fecha)
    return "NORMAL" if pct_acumulado <= Decimal(str(_param(p, "mortalidad_anormal"))) else "ANORMAL"


def _repartir(monto: Decimal, saldos: Mapping[str, Decimal]) -> list[tuple[str, Decimal]]:
    """Reparte el crédito entre los sufijos del centro en proporción a su
    saldo (mayor residuo en centavos)."""
    base = {s: v for s, v in saldos.items() if s != SUFIJO_MORTALIDAD_NORMAL and v > 0}
    total = sum(base.values(), CERO)
    if total <= 0:
        raise ReglaNegocioError("El centro 6.1.01.01 no tiene costo acumulado en el ciclo.", codigo="CENTRO_SIN_COSTO")
    cent = a_centavos(monto)
    partes = {s: cent * a_centavos(v) // a_centavos(total) for s, v in base.items()}
    resto = cent - sum(partes.values())
    orden = sorted(base, key=lambda s: (-(cent * a_centavos(base[s]) % a_centavos(total)), s))
    for s in orden[:resto]:
        partes[s] += 1
    return [(s, de_centavos(c)) for s, c in sorted(partes.items()) if c > 0]


def lineas_asiento_baja(clasificacion: str, monto: Decimal, saldos: Mapping[str, Decimal]) -> list[dict[str, Any]]:
    """Normal: 6.1.01.01-40 contra los sufijos del centro (reclasificación
    interna; el costo queda absorbido en el lote). Anormal: 7.2.01.04 contra
    los sufijos del centro (sale del costo del lote, gasto no operativo)."""
    if clasificacion == "NORMAL":
        lineas = [{"cuenta": CENTRO_CARNE, "debe": monto, "centro_costo": CENTRO_CARNE,
                   "sufijo": SUFIJO_MORTALIDAD_NORMAL}]
    else:
        lineas = [{"cuenta": CUENTA_MORTALIDAD_ANORMAL, "debe": monto}]
    for sufijo, parte in _repartir(monto, saldos):
        lineas.append({"cuenta": CENTRO_CARNE, "haber": parte, "centro_costo": CENTRO_CARNE, "sufijo": sufijo})
    return lineas


def bitacoras_sin_baja(con: sqlite3.Connection, ciclo_id: int) -> list[dict[str, Any]]:
    """Días con aves muertas que aún no tienen Registro de Baja (FIN-TRA-04)."""
    return [dict(f) for f in con.execute(
        """SELECT b.*, l.codigo AS lote FROM bitacora_diaria b JOIN lotes l ON l.id = b.lote_id
            WHERE l.ciclo_id = ? AND b.aves_muertas > 0
              AND NOT EXISTS (SELECT 1 FROM bajas_mortalidad m WHERE m.bitacora_id = b.id)
            ORDER BY b.fecha, b.id""", (ciclo_id,))]


def _contabilizar(con: sqlite3.Connection, sesion: Sesion, baja: sqlite3.Row, lote_codigo: str) -> int:
    saldos = costo_ciclo_por_sufijo(con, int(baja["ciclo_id"]), baja["fecha"])
    monto = de_centavos(int(baja["monto_cent"]))
    clase = baja["clasificacion"]
    glosa = (f"Baja por mortalidad {clase.lower()} de {baja['aves']} aves del lote {lote_codigo} "
             f"({pct_texto(baja['pct_acumulado'])} acumulado) — FIN-TRA-04")
    asiento = contable.crear_asiento(con, sesion, baja["fecha"], glosa, lineas_asiento_baja(clase, monto, saldos),
                                     "PRODUCCION", f"FIN-TRA-04 N.º {baja['id']}", tipo="TRASPASO")
    con.execute("UPDATE bajas_mortalidad SET estado = 'CONTABILIZADA', asiento_id = ? WHERE id = ?",
                (asiento.id, int(baja["id"])))
    auditoria.registrar(con, sesion, "bajas_mortalidad", "UPDATE", int(baja["id"]), antes={"estado": baja["estado"]},
                        despues={"estado": "CONTABILIZADA", "asiento_id": asiento.id,
                                 "cuenta": CUENTA_MORTALIDAD_ANORMAL if clase == "ANORMAL" else
                                 f"{CENTRO_CARNE}-{SUFIJO_MORTALIDAD_NORMAL}"})
    return asiento.id


def registrar_baja(con: sqlite3.Connection, sesion: Sesion, bitacora_id: int,
                   informe_veterinario: str | None = None) -> dict[str, Any]:
    """Registro de Baja por Mortalidad (FIN-TRA-04) de un día de la bitácora.

    Clasifica con el % ACUMULADO del ciclo a esa fecha. Costo por ave = costo
    acumulado del centro ÷ aves vivas al inicio (CI-FIN-05 actividad 3a).
    NORMAL: asiento inmediato al sufijo 40. ANORMAL: exige el informe
    veterinario y queda pendiente de la autorización de la Gerencia General;
    luego se contabiliza en 7.2.01.04 (:func:`contabilizar_baja`).

    # DISCREPANCIA: CI-FIN-05 no dice si, al superarse el 5 %, las bajas ya
    # absorbidas como normales se reclasifican. Por el Principio de Prudencia
    # (NC-1: se reconoce cuando se produce, sin rehacer lo anterior) solo las
    # bajas cuyo acumulado supera el 5 % van a 7.2.01.04; las anteriores quedan
    # en el sufijo 40.

    Returns:
        ``{id, clasificacion, pct, costo_por_ave, monto, estado, asiento_id}``.
    """
    _exigir_finanzas(con, sesion, "EDITAR")
    informe = _opcional(informe_veterinario, "informe veterinario", 120)
    with transaccion(con):
        b = con.execute("SELECT * FROM bitacora_diaria WHERE id = ?", (bitacora_id,)).fetchone()
        if b is None:
            raise DatosInvalidosError(f"No existe el día de bitácora {bitacora_id}.")
        if int(b["aves_muertas"]) == 0:
            raise ReglaNegocioError("Ese día no registra aves muertas.", codigo="SIN_MORTALIDAD")
        if con.execute("SELECT 1 FROM bajas_mortalidad WHERE bitacora_id = ?", (bitacora_id,)).fetchone():
            raise ReglaNegocioError("Ese día ya tiene su Registro de Baja.", codigo="BAJA_DUPLICADA")
        lote = _lote(con, int(b["lote_id"]))
        c = _ciclo(con, int(lote["ciclo_id"]))
        anteriores = [x for x in bitacoras_sin_baja(con, int(c["id"]))
                      if (x["fecha"], x["id"]) < (b["fecha"], int(b["id"]))]
        if anteriores:
            raise ReglaNegocioError(f"Registre primero la baja del {fecha_pantalla(anteriores[0]['fecha'])} "
                                    f"(lote {anteriores[0]['lote']}): las bajas siguen el orden de los decesos.",
                                    codigo="BAJAS_EN_ORDEN")
        pct = Decimal(b["mortalidad_acum"])
        clase = clasificar_mortalidad(con, b["fecha"], pct)
        if clase == "ANORMAL" and informe is None:
            raise ReglaNegocioError(
                f"Mortalidad acumulada de {pct_texto(pct)}: es ANORMAL y exige el informe veterinario de respaldo "
                "(CI-FIN-05, FIN-TRA-04).", codigo="INFORME_VETERINARIO_REQUERIDO")
        saldos = costo_ciclo_por_sufijo(con, int(c["id"]), b["fecha"])
        costo = sum(saldos.values(), CERO)
        costo_ave = (costo / Decimal(int(c["aves_iniciales"]))).quantize(TASA, rounding=ROUND_HALF_UP)
        monto = redondear_bs(costo_ave * int(b["aves_muertas"]))
        estado = "CONTABILIZADA" if clase == "NORMAL" else "PENDIENTE_AUTORIZACION"
        cur = con.execute(
            """INSERT INTO bajas_mortalidad (ciclo_id, bitacora_id, fecha, aves, muertas_acumuladas, pct_acumulado,
                   clasificacion, costo_acumulado_cent, costo_por_ave, monto_cent, informe_veterinario, estado,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(c["id"]), bitacora_id, b["fecha"], int(b["aves_muertas"]), int(b["muertas_acum_ciclo"]), str(pct),
             clase, a_centavos(costo), str(costo_ave), a_centavos(monto), informe,
             "AUTORIZADA" if clase == "NORMAL" else "PENDIENTE_AUTORIZACION", sesion.nombre_usuario, _iso(_ahora())))
        baja_id = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "bajas_mortalidad", "INSERT", baja_id,
                            despues={"lote": lote["codigo"], "fecha": b["fecha"], "aves": int(b["aves_muertas"]),
                                     "pct_acumulado": pct, "clasificacion": clase, "costo_por_ave": costo_ave,
                                     "monto": monto, "informe_veterinario": informe})
        asiento_id = None
        if clase == "NORMAL":
            asiento_id = _contabilizar(con, sesion, con.execute("SELECT * FROM bajas_mortalidad WHERE id = ?",
                                                                (baja_id,)).fetchone(), lote["codigo"])
    return {"id": baja_id, "clasificacion": clase, "pct": pct, "costo_por_ave": costo_ave, "monto": monto,
            "estado": estado, "asiento_id": asiento_id}


def autorizar_baja_anormal(con: sqlite3.Connection, sesion: Sesion, baja_id: int) -> None:
    """Autorización de la Gerencia General de la baja anormal, con el informe
    veterinario a la vista (FIN-TRA-04; CI-FIN-05 actividad 3b)."""
    _exigir_gerencia(con, sesion)
    with transaccion(con):
        b = _baja(con, baja_id)
        if b["clasificacion"] != "ANORMAL":
            raise ReglaNegocioError("Solo la mortalidad anormal requiere autorización.", codigo="BAJA_NORMAL")
        if b["estado"] != "PENDIENTE_AUTORIZACION":
            raise ReglaNegocioError("La baja ya fue autorizada.", codigo="BAJA_AUTORIZADA")
        momento = _iso(_ahora())
        con.execute("UPDATE bajas_mortalidad SET estado = 'AUTORIZADA', autorizado_por = ?, autorizado_en = ? "
                    "WHERE id = ?", (sesion.nombre_usuario, momento, baja_id))
        auditoria.registrar(con, sesion, "bajas_mortalidad", "UPDATE", baja_id, antes={"estado": b["estado"]},
                            despues={"estado": "AUTORIZADA", "informe_veterinario": b["informe_veterinario"]})


def contabilizar_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int) -> int:
    """Asiento de la baja anormal autorizada: 7.2.01.04 contra 6.1.01.01
    (gasto no operativo del período, fuera del Margen Bruto)."""
    _exigir_finanzas(con, sesion, "EDITAR")
    with transaccion(con):
        b = _baja(con, baja_id)
        if b["estado"] == "CONTABILIZADA":
            raise ReglaNegocioError("La baja ya está contabilizada.", codigo="BAJA_CONTABILIZADA")
        if b["estado"] != "AUTORIZADA":
            raise AutorizacionError("La baja por mortalidad anormal requiere la autorización de la Gerencia General.",
                                    codigo="BAJA_SIN_AUTORIZACION")
        lote = con.execute("SELECT l.codigo FROM bitacora_diaria x JOIN lotes l ON l.id = x.lote_id WHERE x.id = ?",
                           (int(b["bitacora_id"]),)).fetchone()
        return _contabilizar(con, sesion, b, lote["codigo"])


def _baja(con: sqlite3.Connection, baja_id: int) -> sqlite3.Row:
    if isinstance(baja_id, bool) or not isinstance(baja_id, int):
        raise DatosInvalidosError("Indique la baja.")
    b = con.execute("SELECT * FROM bajas_mortalidad WHERE id = ?", (baja_id,)).fetchone()
    if b is None:
        raise DatosInvalidosError(f"No existe la baja {baja_id}.")
    return b


def listar_bajas(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    return [{**dict(f), "monto": de_centavos(int(f["monto_cent"])), "pct_acumulado": Decimal(f["pct_acumulado"]),
             "costo_por_ave": Decimal(f["costo_por_ave"])}
            for f in con.execute("""SELECT m.*, l.codigo AS lote FROM bajas_mortalidad m
                                      JOIN bitacora_diaria b ON b.id = m.bitacora_id JOIN lotes l ON l.id = b.lote_id
                                     WHERE m.ciclo_id = ? ORDER BY m.fecha, m.id""", (ciclo_id,))]


# ==========================================================================
# 6. DISPOSICIÓN SANITARIA (FIN-TRA-11)
# ==========================================================================
def dispuestas_lote(con: sqlite3.Connection, lote_id: int) -> int:
    return int(con.execute("SELECT COALESCE(SUM(aves), 0) FROM disposiciones_sanitarias WHERE lote_id = ?",
                           (lote_id,)).fetchone()[0])


def registrar_disposicion(con: sqlite3.Connection, sesion: Sesion, lote_id: int, fecha: Any, aves: int, metodo: str,
                          responsable: str, detalle_metodo: str | None = None) -> int:
    """Bitácora de Disposición Sanitaria (FIN-TRA-11): aves muertas dispuestas
    con su método; no puede superar las muertas registradas del lote."""
    _exigir_unidad_tecnica(con, sesion)
    if metodo not in METODOS_DISPOSICION:
        raise DatosInvalidosError(f"Método no válido. Use: {', '.join(METODOS_DISPOSICION)}.")
    n = _entero(aves, "aves dispuestas", 1)
    resp = _texto(responsable, "responsable", 120)
    detalle = _opcional(detalle_metodo, "detalle del método", 300)
    if metodo == "OTRO" and detalle is None:
        raise DatosInvalidosError("Describa el método de disposición.")
    f = a_fecha(fecha)
    with transaccion(con):
        lote = _lote(con, lote_id)
        pendientes = muertas_lote(con, lote_id) - dispuestas_lote(con, lote_id)
        if n > pendientes:
            raise ReglaNegocioError(f"El lote {lote['codigo']} tiene {pendientes} aves muertas sin disponer.",
                                    codigo="DISPOSICION_EXCEDE")
        c = _ciclo(con, int(lote["ciclo_id"]))
        if f < a_fecha(c["fecha_inicio"]):
            raise ReglaNegocioError("La fecha es anterior al ciclo.", codigo="FECHA_ANTERIOR_CICLO")
        cur = con.execute(
            """INSERT INTO disposiciones_sanitarias (lote_id, fecha, aves, metodo, detalle_metodo, responsable,
                   registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (lote_id, f.isoformat(), n, metodo, detalle, resp, sesion.nombre_usuario, _iso(_ahora())))
        did = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "disposiciones_sanitarias", "INSERT", did,
                            despues={"lote": lote["codigo"], "fecha": f.isoformat(), "aves": n, "metodo": metodo,
                                     "responsable": resp})
    return did


def supervisar_disposicion(con: sqlite3.Connection, sesion: Sesion, disposicion_id: int) -> None:
    """Visto «Supervisado por (Gerencia General)» de la FIN-TRA-11."""
    _exigir_gerencia(con, sesion)
    with transaccion(con):
        d = con.execute("SELECT * FROM disposiciones_sanitarias WHERE id = ?", (disposicion_id,)).fetchone()
        if d is None:
            raise DatosInvalidosError(f"No existe la disposición {disposicion_id}.")
        if d["supervisado_por"]:
            raise ReglaNegocioError("La disposición ya fue supervisada.", codigo="DISPOSICION_SUPERVISADA")
        con.execute("UPDATE disposiciones_sanitarias SET supervisado_por = ?, supervisado_en = ? WHERE id = ?",
                    (sesion.nombre_usuario, _iso(_ahora()), disposicion_id))
        auditoria.registrar(con, sesion, "disposiciones_sanitarias", "UPDATE", disposicion_id,
                            antes={"supervisado_por": None}, despues={"supervisado_por": sesion.nombre_usuario})


def listar_disposiciones(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    return [dict(f) for f in con.execute(
        """SELECT d.*, l.codigo AS lote FROM disposiciones_sanitarias d JOIN lotes l ON l.id = d.lote_id
            WHERE l.ciclo_id = ? ORDER BY d.fecha DESC, d.id DESC""", (ciclo_id,))]


# ==========================================================================
# 7. MUESTREO SEMANAL DE PESO (POL-03 Art. 2 v; POA A7)
# ==========================================================================
def semana_del_ciclo(con: sqlite3.Connection, inicio: Any, fecha: Any) -> int:
    p = _params(con, inicio)
    return (dia_del_ciclo(con, inicio, fecha) - 1) // int(_param(p, "muestreo_peso_dias")) + 1


def registrar_muestreo(con: sqlite3.Connection, sesion: Sesion, lote_id: int, fecha: Any, aves_muestra: int,
                       peso_total_kg: Any, observacion: str | None = None) -> dict[str, Any]:
    """Pesaje semanal de una muestra representativa del lote: un registro por
    semana del ciclo."""
    _exigir_unidad_tecnica(con, sesion)
    n = _entero(aves_muestra, "aves de la muestra", 1)
    peso = a_mil(peso_total_kg, "peso de la muestra")
    obs = _opcional(observacion, "observación", 300)
    f = a_fecha(fecha)
    with transaccion(con):
        lote = _lote(con, lote_id)
        if lote["estado"] != "EN_CRIANZA":
            raise ReglaNegocioError("El lote ya fue faenado.", codigo="LOTE_FAENADO")
        c = _ciclo(con, int(lote["ciclo_id"]))
        dia = dia_del_ciclo(con, c["fecha_inicio"], f)
        if dia < 1:
            raise ReglaNegocioError("La fecha es anterior a la recepción.", codigo="FECHA_ANTERIOR_CICLO")
        vivas = int(lote["aves_iniciales"]) - muertas_lote(con, lote_id)
        if n > vivas:
            raise ReglaNegocioError(f"La muestra no puede superar las {vivas} aves vivas del lote.",
                                    codigo="MUESTRA_EXCEDE")
        semana = semana_del_ciclo(con, c["fecha_inicio"], f)
        if con.execute("SELECT 1 FROM muestreos_peso WHERE lote_id = ? AND semana = ?", (lote_id, semana)).fetchone():
            raise DatosInvalidosError(f"El lote {lote['codigo']} ya tiene el muestreo de la semana {semana}.")
        promedio = (de_mil(peso) / n).quantize(KG, rounding=ROUND_HALF_UP)
        cur = con.execute(
            """INSERT INTO muestreos_peso (lote_id, fecha, dia_ciclo, semana, aves_muestra, peso_total_mil,
                   peso_promedio, observacion, registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (lote_id, f.isoformat(), dia, semana, n, peso, str(promedio), obs, sesion.nombre_usuario, _iso(_ahora())))
        mid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "muestreos_peso", "INSERT", mid,
                            despues={"lote": lote["codigo"], "semana": semana, "dia": dia, "aves_muestra": n,
                                     "peso_total_kg": de_mil(peso), "peso_promedio_kg": promedio})
    return {"id": mid, "semana": semana, "dia": dia, "peso_promedio": promedio}


def cumplimiento_muestreo(con: sqlite3.Connection, lote_id: int, hasta: Any) -> dict[str, Any]:
    """Semanas completas del ciclo hasta la fecha (o el faenado) frente a las
    semanas con muestreo registrado (meta POA A7: 100 %)."""
    lote = _lote(con, lote_id)
    c = _ciclo(con, int(lote["ciclo_id"]))
    f = a_fecha(hasta)
    faenado = con.execute("SELECT fecha FROM faenados WHERE lote_id = ?", (lote_id,)).fetchone()
    if faenado:
        f = min(f, a_fecha(faenado["fecha"]))
    p = _params(con, c["fecha_inicio"])
    dias = int(_param(p, "muestreo_peso_dias"))
    transcurridos = dia_del_ciclo(con, c["fecha_inicio"], f)
    esperadas = max(0, transcurridos // dias)
    registradas = {int(r["semana"]) for r in con.execute("SELECT semana FROM muestreos_peso WHERE lote_id = ?",
                                                          (lote_id,))}
    faltantes = [s for s in range(1, esperadas + 1) if s not in registradas]
    cumplimiento = _pct(Decimal(esperadas - len(faltantes)) / Decimal(esperadas)) if esperadas else Decimal(1)
    return {"esperadas": esperadas, "registradas": len(registradas), "faltantes": faltantes,
            "cumplimiento": cumplimiento}


def listar_muestreos(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    return [{**dict(f), "peso_total": de_mil(int(f["peso_total_mil"])), "peso_promedio": Decimal(f["peso_promedio"])}
            for f in con.execute("""SELECT m.*, l.codigo AS lote FROM muestreos_peso m JOIN lotes l ON l.id = m.lote_id
                                     WHERE l.ciclo_id = ? ORDER BY m.semana DESC, l.codigo""", (ciclo_id,))]


# ==========================================================================
# 8. FAENADO POR LOTE (FIN-TRA-05; CI-FIN-12; POL-03 Art. 1)
# ==========================================================================
def semaforo_pmf(con: sqlite3.Connection, fecha: Any, pmf: Decimal) -> str:
    """PMF: Bueno < 10 %, Normal 10-20 %, Malo > 20 % (KPI-18)."""
    p = _params(con, fecha)
    if pmf < Decimal(str(_param(p, "pmf_umbral_bueno"))):
        return "BUENO"
    if pmf <= Decimal(str(_param(p, "pmf_umbral_malo"))):
        return "NORMAL"
    return "MALO"


def semaforo_ica(con: sqlite3.Connection, fecha: Any, ica: Decimal) -> str:
    """ICA: Bueno < 2,00, Normal 2,00-2,10, Malo > 2,10 (PR-FIN-06 §7)."""
    p = _params(con, fecha)
    if ica < Decimal(str(_param(p, "ica_umbral_bueno"))):
        return "BUENO"
    if ica <= Decimal(str(_param(p, "ica_umbral_malo"))):
        return "NORMAL"
    return "MALO"


def alimento_lote(con: sqlite3.Connection, lote_id: int) -> int:
    """Kilogramos de alimento consumidos por el lote según la bitácora (milésimas)."""
    return int(con.execute("SELECT COALESCE(SUM(alimento_kg_mil), 0) FROM bitacora_diaria WHERE lote_id = ?",
                           (lote_id,)).fetchone()[0])


def autorizar_faenado_anticipado(con: sqlite3.Connection, sesion: Sesion, lote_id: int,
                                 informe_veterinario: str) -> None:
    """Excepción de POL-03 Art. 1: faenar antes del día mínimo solo por
    indicación veterinaria fundada en riesgo sanitario, autorizada por la GG."""
    _exigir_gerencia(con, sesion)
    informe = _texto(informe_veterinario, "informe veterinario", 120)
    with transaccion(con):
        lote = _lote(con, lote_id)
        if lote["estado"] != "EN_CRIANZA":
            raise ReglaNegocioError("El lote ya fue faenado.", codigo="LOTE_FAENADO")
        momento = _iso(_ahora())
        con.execute("UPDATE lotes SET faenado_anticipado_informe = ?, faenado_anticipado_por = ?, "
                    "faenado_anticipado_en = ? WHERE id = ?", (informe, sesion.nombre_usuario, momento, lote_id))
        auditoria.registrar(con, sesion, "lotes", "UPDATE", lote_id, antes={"faenado_anticipado_informe": None},
                            despues={"faenado_anticipado_informe": informe})


def registrar_faenado(
    con: sqlite3.Connection, sesion: Sesion, lote_id: int, fecha: Any, aves_faenadas: int, peso_vivo_kg: Any,
    peso_canal_kg: Any, checklist: Mapping[str, bool], supervisor_eventual: str, *, carcasas_separadas: int = 0,
    explicacion_faltante: str | None = None, observacion: str | None = None, ahora: datetime | None = None,
) -> dict[str, Any]:
    """Registro de Faenado del lote (FIN-TRA-05).

    Bloquea el faenado antes de ``dias_ciclo_minimo`` (día 70) salvo
    autorización por indicación veterinaria. Calcula la merma (peso vivo −
    peso canal), el PMF y su semáforo, el ICA del lote (kg de alimento de la
    bitácora ÷ kg de carne viva) y el cumplimiento de los puntos de control de
    CI-FIN-12. Un PMF en rojo o un punto de control incumplido se reporta de
    inmediato a la Gerencia.

    Returns:
        ``{id, dia, merma, pmf, semaforo_pmf, ica, semaforo_ica, cumple_ci_fin_12, alertas}``.
    """
    _exigir_unidad_tecnica(con, sesion)
    momento = _ahora(ahora)
    f = a_fecha(fecha)
    n = _entero(aves_faenadas, "aves faenadas", 1)
    vivo = a_mil(peso_vivo_kg, "peso vivo")
    canal = a_mil(peso_canal_kg, "peso canal")
    separadas = _entero(carcasas_separadas, "carcasas separadas", 0)
    supervisor = _texto(supervisor_eventual, "supervisor del personal eventual", 120)
    explicacion = _opcional(explicacion_faltante, "explicación del faltante", 500)
    obs = _opcional(observacion, "observación", 500)
    if canal > vivo:
        raise DatosInvalidosError("El peso canal no puede superar el peso vivo.")
    faltan = [k for k in PUNTOS_CI_FIN_12 if k not in checklist]
    if faltan:
        raise DatosInvalidosError("Marque todos los puntos de control de CI-FIN-12: falta "
                                  f"«{PUNTOS_CI_FIN_12[faltan[0]]}».")
    if f > momento.date():
        raise DatosInvalidosError("El faenado no admite fechas futuras.")
    with transaccion(con):
        lote = _lote(con, lote_id)
        if lote["estado"] != "EN_CRIANZA":
            raise ReglaNegocioError("El lote ya fue faenado.", codigo="LOTE_FAENADO")
        c = _ciclo(con, int(lote["ciclo_id"]))
        p = _params(con, c["fecha_inicio"])
        dia = dia_del_ciclo(con, c["fecha_inicio"], f)
        minimo = int(_param(p, "dias_ciclo_minimo"))
        if dia < minimo and not lote["faenado_anticipado_informe"]:
            raise ReglaNegocioError(
                f"Faenado bloqueado: el lote está en el día {dia} y POL-03 Art. 1 prohíbe faenar antes del día "
                f"{minimo}, salvo indicación veterinaria autorizada por la Gerencia General.",
                codigo="FAENADO_ANTES_DIA_MINIMO", detalle={"dia": dia, "minimo": minimo})
        fuera = int(dia > int(_param(p, "dias_ciclo")))
        if fuera and obs is None:
            raise DatosInvalidosError(f"El faenado del día {dia} excede los {_param(p, 'dias_ciclo')} días del ciclo: "
                                      "explique la causa en la observación.")
        vivas = int(lote["aves_iniciales"]) - muertas_lote(con, lote_id)
        if n > vivas:
            raise ReglaNegocioError(f"El lote tiene {vivas} aves vivas según la bitácora: no se pueden faenar {n}.",
                                    codigo="FAENADAS_EXCEDEN_VIVAS")
        faltante = vivas - n
        if faltante and explicacion is None:
            raise DatosInvalidosError(f"Faltan {faltante} aves entre las vivas de la bitácora y las faenadas: explique "
                                      "el faltante (riesgo 39).")
        kg_alimento = alimento_lote(con, lote_id)
        if kg_alimento == 0:
            raise ReglaNegocioError("La bitácora del lote no registra alimento consumido: sin ese dato el ICA no "
                                    "tiene respaldo (FIN-TRA-02).", codigo="SIN_ALIMENTO_BITACORA")
        merma = vivo - canal
        pmf = _pct(Decimal(merma) / Decimal(vivo))
        sem_pmf = semaforo_pmf(con, f, pmf)
        ica = (Decimal(kg_alimento) / Decimal(vivo)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sem_ica = semaforo_ica(con, f, ica)
        puntos = {k: bool(checklist[k]) for k in PUNTOS_CI_FIN_12}
        cumple = int(all(puntos.values()))
        cur = con.execute(
            """INSERT INTO faenados (lote_id, fecha, dia_ciclo, aves_vivas_lote, aves_faenadas, faltante_aves,
                   explicacion_faltante, peso_vivo_mil, peso_canal_mil, merma_mil, carcasas_separadas, pmf, semaforo_pmf,
                   alimento_kg_mil, ica, semaforo_ica, checklist_json, cumple_ci_fin_12, observacion,
                   supervisor_eventual, fuera_de_plazo, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lote_id, f.isoformat(), dia, vivas, n, faltante, explicacion if faltante else None, vivo, canal, merma,
             separadas, str(pmf), sem_pmf, kg_alimento, str(ica), sem_ica, auditoria.a_json(puntos), cumple, obs,
             supervisor, fuera, sesion.nombre_usuario, _iso(momento)))
        fid = int(cur.lastrowid)
        con.execute("UPDATE lotes SET estado = 'FAENADO' WHERE id = ?", (lote_id,))
        pendientes = con.execute("SELECT COUNT(*) FROM lotes WHERE ciclo_id = ? AND estado = 'EN_CRIANZA'",
                                 (int(c["id"]),)).fetchone()[0]
        if not pendientes:
            con.execute("UPDATE ciclos_produccion SET estado = 'FAENADO' WHERE id = ?", (int(c["id"]),))
        auditoria.registrar(con, sesion, "faenados", "INSERT", fid,
                            despues={"lote": lote["codigo"], "fecha": f.isoformat(), "dia": dia, "aves_faenadas": n,
                                     "faltante": faltante, "peso_vivo_kg": de_mil(vivo), "peso_canal_kg": de_mil(canal),
                                     "merma_kg": de_mil(merma), "pmf": pmf, "semaforo_pmf": sem_pmf, "ica": ica,
                                     "semaforo_ica": sem_ica, "cumple_ci_fin_12": bool(cumple)})
        alertas = []
        if sem_pmf == "MALO":
            alertas.append(_alerta_automatica(con, sesion, c, lote, None, f, momento, "PMF_MALO",
                                              f"PMF de {pct_texto(pmf)} en el faenado: se reporta de inmediato a la "
                                              "Gerencia (FIN-TRA-05).", None))
        if not cumple:
            fallidos = "; ".join(PUNTOS_CI_FIN_12[k] for k, v in puntos.items() if not v)
            alertas.append(_alerta_automatica(con, sesion, c, lote, None, f, momento, "INOCUIDAD",
                                              f"Incumplimiento de CI-FIN-12: {fallidos}. Las carcasas observadas se "
                                              "separan y no se comercializan.", None))
    return {"id": fid, "dia": dia, "aves_faenadas": n, "faltante": faltante, "merma": de_mil(merma), "pmf": pmf,
            "semaforo_pmf": sem_pmf, "ica": ica, "semaforo_ica": sem_ica, "cumple_ci_fin_12": bool(cumple),
            "alertas": alertas}


def listar_faenados(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    filas = []
    for f in con.execute("""SELECT x.*, l.codigo AS lote FROM faenados x JOIN lotes l ON l.id = x.lote_id
                             WHERE l.ciclo_id = ? ORDER BY x.fecha, l.codigo""", (ciclo_id,)):
        filas.append({**dict(f), "peso_vivo": de_mil(int(f["peso_vivo_mil"])), "peso_canal": de_mil(int(f["peso_canal_mil"])),
                      "merma": de_mil(int(f["merma_mil"])), "alimento_kg": de_mil(int(f["alimento_kg_mil"])),
                      "pmf": Decimal(f["pmf"]), "ica": Decimal(f["ica"])})
    return filas


# ==========================================================================
# 9. CONCILIACIÓN DE AVES Y CONCLUSIÓN DEL CICLO (FIN-TRA-06, riesgo 39)
# ==========================================================================
def conciliar_aves(con: sqlite3.Connection, ciclo_id: int) -> dict[str, Any]:
    """Aves ingresadas = mortalidad normal + mortalidad anormal + faenadas
    (+ faltantes explicados). ``cuadra`` es verdadero si no hay diferencia."""
    c = _ciclo(con, ciclo_id)
    normal = int(con.execute("SELECT COALESCE(SUM(aves), 0) FROM bajas_mortalidad WHERE ciclo_id = ? "
                             "AND clasificacion = 'NORMAL'", (ciclo_id,)).fetchone()[0])
    anormal = int(con.execute("SELECT COALESCE(SUM(aves), 0) FROM bajas_mortalidad WHERE ciclo_id = ? "
                              "AND clasificacion = 'ANORMAL'", (ciclo_id,)).fetchone()[0])
    muertas = muertas_ciclo(con, ciclo_id)
    sin_baja = muertas - normal - anormal
    faenadas = int(con.execute("SELECT COALESCE(SUM(x.aves_faenadas), 0) FROM faenados x JOIN lotes l "
                               "ON l.id = x.lote_id WHERE l.ciclo_id = ?", (ciclo_id,)).fetchone()[0])
    lotes_pendientes = int(con.execute("SELECT COUNT(*) FROM lotes WHERE ciclo_id = ? AND estado = 'EN_CRIANZA'",
                                       (ciclo_id,)).fetchone()[0])
    ingresadas = int(c["aves_iniciales"])
    en_crianza = ingresadas - muertas - faenadas if lotes_pendientes else 0
    diferencia = ingresadas - muertas - faenadas - en_crianza
    iniciales = Decimal(ingresadas)
    return {"ingresadas": ingresadas, "muertas_normal": normal, "muertas_anormal": anormal,
            "muertas_sin_baja": sin_baja, "muertas": muertas, "faenadas": faenadas, "en_crianza": en_crianza,
            "faltante": diferencia, "lotes_pendientes": lotes_pendientes,
            "pct_normal": _pct(Decimal(normal) / iniciales), "pct_anormal": _pct(Decimal(anormal) / iniciales),
            "cuadra": diferencia == 0 and lotes_pendientes == 0 and sin_baja == 0}


def autorizar_faltante(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, motivo: str) -> None:
    """La Gerencia General toma conocimiento del faltante de aves explicado en
    el faenado y autoriza concluir el ciclo con esa diferencia documentada."""
    _exigir_gerencia(con, sesion)
    texto = _texto(motivo, "motivo", 500)
    with transaccion(con):
        c = _ciclo(con, ciclo_id)
        r = conciliar_aves(con, ciclo_id)
        if r["faltante"] <= 0:
            raise ReglaNegocioError("El ciclo no tiene faltante de aves.", codigo="SIN_FALTANTE")
        con.execute("UPDATE ciclos_produccion SET faltante_autorizado_por = ?, faltante_motivo = ? WHERE id = ?",
                    (sesion.nombre_usuario, texto, ciclo_id))
        auditoria.registrar(con, sesion, "ciclos_produccion", "UPDATE", ciclo_id,
                            antes={"faltante_autorizado_por": c["faltante_autorizado_por"]},
                            despues={"faltante": r["faltante"], "motivo": texto})


def concluir_ciclo(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int) -> dict[str, Any]:
    """Concluye el ciclo operativo: todos los lotes faenados, todas las bajas
    registradas y contabilizadas y la conciliación de aves cuadrada (o el
    faltante autorizado por la GG). El traslado del costo a Productos
    Terminados (día 73) corresponde a la Fase 7 (costos)."""
    _exigir_finanzas(con, sesion, "EDITAR")
    with transaccion(con):
        c = _ciclo(con, ciclo_id)
        if c["estado"] == "CONCLUIDO":
            raise ReglaNegocioError("El ciclo ya está concluido.", codigo="CICLO_CONCLUIDO")
        r = conciliar_aves(con, ciclo_id)
        if r["lotes_pendientes"]:
            raise ReglaNegocioError(f"Faltan {r['lotes_pendientes']} lote(s) por faenar.", codigo="LOTES_EN_CRIANZA")
        if r["muertas_sin_baja"]:
            raise ReglaNegocioError(f"{r['muertas_sin_baja']} aves muertas no tienen Registro de Baja (FIN-TRA-04).",
                                    codigo="MORTALIDAD_SIN_BAJA")
        pendientes = con.execute("SELECT COUNT(*) FROM bajas_mortalidad WHERE ciclo_id = ? AND estado <> "
                                 "'CONTABILIZADA'", (ciclo_id,)).fetchone()[0]
        if pendientes:
            raise ReglaNegocioError(f"{pendientes} baja(s) anormal(es) sin autorizar o sin contabilizar.",
                                    codigo="BAJAS_PENDIENTES")
        if r["faltante"] and not c["faltante_autorizado_por"]:
            raise ReglaNegocioError(
                f"La conciliación no cuadra: faltan {r['faltante']} aves (ingresadas {r['ingresadas']} = muertas "
                f"{r['muertas']} + faenadas {r['faenadas']} + faltante). Requiere la toma de conocimiento de la GG.",
                codigo="CONCILIACION_NO_CUADRA")
        momento = _iso(_ahora())
        con.execute("UPDATE ciclos_produccion SET estado = 'CONCLUIDO', conciliacion_json = ?, concluido_por = ?, "
                    "concluido_en = ? WHERE id = ?", (auditoria.a_json(r), sesion.nombre_usuario, momento, ciclo_id))
        auditoria.registrar(con, sesion, "ciclos_produccion", "UPDATE", ciclo_id, antes={"estado": c["estado"]},
                            despues={"estado": "CONCLUIDO", "conciliacion": r})
    return r


# ==========================================================================
# 10. TRAZABILIDAD — URL Y CÓDIGO QR «VIDA DIGNA» (POL-03 Art. 3)
# ==========================================================================
def url_lote(con: sqlite3.Connection, lote: Mapping[str, Any]) -> str:
    """Dirección pública del lote: ``qr_url_base`` + código QR del lote."""
    c = _ciclo(con, int(lote["ciclo_id"]))
    base = str(_param(_params(con, c["fecha_inicio"]), "qr_url_base"))
    return base.rstrip("/") + "/" + str(lote["qr_token"])


def datos_trazabilidad(con: sqlite3.Connection, lote_id: int) -> dict[str, Any]:
    """Contenido del QR del lote tomado SOLO de los registros del ciclo
    (POL-03 Art. 3: queda prohibido publicar información no respaldada).

    Exige el faenado registrado. Devuelve el número de lote, la fecha de
    faenado, los días de vida («Este pollo vivió N días», desde la bitácora),
    los insumos de alimentación con movimiento en el kárdex del ciclo, el
    alimento consumido, la mortalidad acumulada, el último peso muestreado, el
    galpón (superficie, densidad, fotografías) y los tratamientos registrados.
    """
    lote = _lote(con, lote_id)
    f = con.execute("SELECT * FROM faenados WHERE lote_id = ?", (lote_id,)).fetchone()
    if f is None:
        raise ReglaNegocioError("El QR se genera con el faenado registrado: aún no hay fecha de faenado ni días de vida "
                                "respaldados.", codigo="QR_SIN_FAENADO")
    c = _ciclo(con, int(lote["ciclo_id"]))
    g = _galpon(con, int(lote["galpon_id"]))
    dias_bitacora = int(con.execute("SELECT COUNT(*) FROM bitacora_diaria WHERE lote_id = ?", (lote_id,)).fetchone()[0])
    insumos = [r["nombre"] for r in con.execute(
        f"""SELECT DISTINCT i.nombre FROM kardex k JOIN insumos i ON i.id = k.insumo_id
             WHERE i.cuenta_inventario IN ({",".join("?" * len(CUENTAS_ALIMENTACION))}) AND k.fecha BETWEEN ? AND ?
             ORDER BY i.nombre""", (*CUENTAS_ALIMENTACION, c["fecha_inicio"], f["fecha"]))]
    tratamientos = [r["tratamiento"] for r in con.execute(
        "SELECT DISTINCT tratamiento FROM bitacora_diaria WHERE lote_id = ? AND tratamiento IS NOT NULL ORDER BY fecha",
        (lote_id,))]
    muestreo = con.execute("SELECT * FROM muestreos_peso WHERE lote_id = ? ORDER BY semana DESC LIMIT 1",
                           (lote_id,)).fetchone()
    mort = mortalidad_ciclo(con, int(c["id"]), f["fecha"])
    canal_ave = (de_mil(int(f["peso_canal_mil"])) / int(f["aves_faenadas"])).quantize(KG, rounding=ROUND_HALF_UP)
    return {
        "lote": lote["codigo"], "url": url_lote(con, lote), "ciclo": f"Ciclo {c['numero']} del ejercicio {c['ejercicio']}",
        "fecha_recepcion": c["fecha_inicio"], "fecha_faenado": f["fecha"], "dias_vida": int(f["dia_ciclo"]),
        "leyenda": f"Este pollo vivió {int(f['dia_ciclo'])} días",
        "galpon": g["nombre"], "superficie_m2": Decimal(int(g["superficie_cm2"])) / CIEN,
        "densidad": Decimal(lote["densidad"]), "fotos_url": g["fotos_url"],
        "insumos_alimentacion": insumos, "alimento_kg": de_mil(alimento_lote(con, lote_id)),
        "tratamientos": tratamientos, "mortalidad_acumulada": mort["pct"],
        "peso_muestreo": Decimal(muestreo["peso_promedio"]) if muestreo else None,
        "semana_muestreo": int(muestreo["semana"]) if muestreo else None,
        "peso_canal_promedio": canal_ave, "aves_faenadas": int(f["aves_faenadas"]),
        "cumple_ci_fin_12": bool(f["cumple_ci_fin_12"]),
        "respaldo": {"bitacora_dias": dias_bitacora, "faenado_id": int(f["id"]), "ciclo_id": int(c["id"]),
                     "kardex_apertura": int(c["kardex_id"])},
    }


def qr_svg(texto: str, lado: int = 180) -> str:
    """Código QR en SVG (reportlab, sin dependencias externas)."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    valor = _texto(texto, "texto del QR", 1000)
    widget = QrCodeWidget(valor)
    x0, y0, x1, y1 = widget.getBounds()
    dibujo = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0), 0, 0])
    dibujo.add(widget)
    return renderSVG.drawToString(dibujo)


def pagina_trazabilidad_html(datos: Mapping[str, Any]) -> str:
    """Página pública del lote para la plataforma QR que mantiene la Gerencia
    General (POL-03 Art. 3). Solo muestra datos de :func:`datos_trazabilidad`."""
    e = html.escape
    insumos = "".join(f"<li>{e(i)}</li>" for i in datos["insumos_alimentacion"]) or \
        "<li>Sin insumos de alimentación registrados en el kárdex del ciclo</li>"
    trat = "".join(f"<li>{e(t)}</li>" for t in datos["tratamientos"]) or "<li>Sin tratamientos registrados</li>"
    fotos = (f'<p><a href="{e(datos["fotos_url"])}">Fotografías reales del galpón</a></p>' if datos["fotos_url"]
             else "<p>Fotografías del galpón: pendientes de publicación por la Gerencia General.</p>")
    peso = (f"{str(datos['peso_muestreo']).replace('.', ',')} kg (muestreo de la semana {datos['semana_muestreo']})"
            if datos["peso_muestreo"] is not None else "sin muestreo registrado")
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lote {e(datos['lote'])} · Vida Digna</title>
<style>
:root{{--tinta:#1f2a24;--verde:#2f6b3f;--fondo:#f7f5ef;--borde:#d9d4c7}}
body{{margin:0;background:var(--fondo);color:var(--tinta);font-family:Georgia,'Times New Roman',serif}}
main{{max-width:640px;margin:0 auto;padding:24px 16px}}
h1{{color:var(--verde);font-size:1.6rem;margin:0 0 4px}}
.leyenda{{font-size:1.25rem;margin:12px 0 20px}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;border-top:1px solid var(--borde);padding-top:12px}}
dt{{font-weight:bold}} dd{{margin:0}}
small{{color:#5d655f}}
</style></head><body><main>
<h1>Pollo Vida Digna · Lote {e(datos['lote'])}</h1>
<small>Cooperativa Agropecuaria Pollolandia R.L. · Caranavi, Los Yungas</small>
<p class="leyenda">{e(datos['leyenda'])}</p>
<dl>
<dt>Recepción</dt><dd>{e(fecha_pantalla(datos['fecha_recepcion']))}</dd>
<dt>Faenado</dt><dd>{e(fecha_pantalla(datos['fecha_faenado']))}</dd>
<dt>Galpón</dt><dd>{e(datos['galpon'])} · {str(datos['densidad']).replace('.', ',')} aves/m²</dd>
<dt>Alimento consumido</dt><dd>{e(kg_texto(int(datos['alimento_kg'] * 1000)))}</dd>
<dt>Mortalidad del ciclo</dt><dd>{e(pct_texto(datos['mortalidad_acumulada']))}</dd>
<dt>Peso vivo muestreado</dt><dd>{e(peso)}</dd>
<dt>Inocuidad (CI-FIN-12)</dt><dd>{'Cumple' if datos['cumple_ci_fin_12'] else 'Con observaciones'}</dd>
</dl>
<h2>Insumos de alimentación</h2><ul>{insumos}</ul>
<h2>Vacunas y tratamientos</h2><ul>{trat}</ul>
{fotos}
<small>Datos tomados de la bitácora del ciclo ({datos['respaldo']['bitacora_dias']} días registrados) y del registro de
faenado. Verificación semestral del Consejo de Vigilancia (POL-03 Art. 3).</small>
</main></body></html>
"""


# ==========================================================================
# 11. CONSULTAS Y RESUMEN
# ==========================================================================
def listar_ciclos(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    filas = []
    for c in con.execute("SELECT * FROM ciclos_produccion ORDER BY fecha_inicio DESC"):
        m = mortalidad_ciclo(con, int(c["id"]))
        filas.append({**dict(c), "costo_pollitos": de_centavos(int(c["costo_pollitos_cent"])), "muertas": m["muertas"],
                      "mortalidad": m["pct"], "semaforo": semaforo_mortalidad(con, c["fecha_inicio"], m["pct"])})
    return filas


def obtener_ciclo(con: sqlite3.Connection, sesion: Sesion, ciclo_id: int, fecha: Any = None) -> dict[str, Any]:
    """Ciclo con sus lotes (aves vivas, día, fase), mortalidad y conciliación."""
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    c = _ciclo(con, ciclo_id)
    f = a_fecha(fecha) if fecha is not None else date.today()
    dia = dia_del_ciclo(con, c["fecha_inicio"], f)
    lotes = []
    for l in con.execute("""SELECT l.*, g.codigo AS galpon, g.nombre AS galpon_nombre, g.superficie_cm2
                              FROM lotes l JOIN galpones g ON g.id = l.galpon_id WHERE l.ciclo_id = ? ORDER BY l.codigo""",
                         (ciclo_id,)):
        muertas = muertas_lote(con, int(l["id"]))
        faenado = con.execute("SELECT * FROM faenados WHERE lote_id = ?", (int(l["id"]),)).fetchone()
        lotes.append({**dict(l), "muertas": muertas, "vivas": int(l["aves_iniciales"]) - muertas,
                      "dispuestas": dispuestas_lote(con, int(l["id"])), "alimento_kg": de_mil(alimento_lote(con, int(l["id"]))),
                      "faenado_fecha": faenado["fecha"] if faenado else None,
                      "pmf": Decimal(faenado["pmf"]) if faenado else None,
                      "ica": Decimal(faenado["ica"]) if faenado else None,
                      "muestreo": cumplimiento_muestreo(con, int(l["id"]), f)})
    return {**dict(c), "costo_pollitos": de_centavos(int(c["costo_pollitos_cent"])), "dia_actual": dia,
            "fase_actual": fase_de_dia(con, c["fecha_inicio"], max(dia, 1)), "lotes": lotes,
            "mortalidad": mortalidad_ciclo(con, ciclo_id), "conciliacion": conciliar_aves(con, ciclo_id),
            "costo_centro": costo_ciclo_por_sufijo(con, ciclo_id, max(f, a_fecha(c["fecha_inicio"])))}


def obtener_lote(con: sqlite3.Connection, sesion: Sesion, lote_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    lote = _lote(con, lote_id)
    return {**dict(lote), "url": url_lote(con, lote), "muertas": muertas_lote(con, lote_id)}


def resumen_produccion(con: sqlite3.Connection, sesion: Sesion, fecha: Any = None) -> dict[str, Any]:
    """Cifras de cabecera: ciclo en curso, día, aves vivas, mortalidad y alertas."""
    seguridad.exigir_permiso(con, sesion, "produccion", "VER")
    f = a_fecha(fecha) if fecha is not None else date.today()
    c = con.execute("SELECT * FROM ciclos_produccion WHERE estado IN ('ABIERTO','FAENADO') "
                    "ORDER BY fecha_inicio DESC LIMIT 1").fetchone()
    pendientes = int(con.execute("SELECT COUNT(*) FROM alertas_sanitarias WHERE atendida_por IS NULL").fetchone()[0])
    if c is None:
        return {"ciclo": None, "alertas_pendientes": pendientes}
    m = mortalidad_ciclo(con, int(c["id"]))
    return {"ciclo": dict(c), "dia": dia_del_ciclo(con, c["fecha_inicio"], f), "vivas": m["vivas"], "mortalidad": m["pct"],
            "semaforo": semaforo_mortalidad(con, c["fecha_inicio"], m["pct"]), "alertas_pendientes": pendientes,
            "bajas_pendientes": len(bitacoras_sin_baja(con, int(c["id"])))}
