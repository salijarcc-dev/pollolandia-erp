"""Activos fijos y CAPEX del ERP Pollolandia (Fase 8, Ciclo 1 — Tesorería y
Ciclo 3 — Transformación).

Implementa POL-02 (Inversiones y CAPEX), PC-05 y PC-06 (activo fijo,
depreciación y amortización), PR-FIN-01 (Plan CAPEX) y PR-FIN-07 (activación,
depreciación y baja), con los formatos FIN-TES-01 a FIN-TES-04, FIN-TRA-08 a
FIN-TRA-10 e INF-CV-01:

1. Plan anual CAPEX (FIN-TES-01) elaborado por la Gerencia General antes del
   plazo de ``capex_plan_*_limite``; financiamiento 100 % con recursos
   propios; importación opcional del presupuesto D.5.
2. ROA proyectado = Utilidad neta anual ÷ (Activos totales + inversión) y
   compatibilidad con el SME vigente (FIN-TES-02); informe técnico-financiero
   al CA (FIN-TES-03); aprobación del CA y ratificación de la Asamblea.
3. Inversión no prevista: la Matriz CI-FIN-09 exige al CA (autorización
   específica sobre ``capex_no_previsto_ca_sobre_bs``). Variación sobre el
   monto aprobado: sobre ``capex_variacion_pct`` se informa al CA; si además
   supera ``capex_variacion_monto_bs`` exige nueva aprobación de la Asamblea.
   Ningún CAPEX compromete pagos sobre el saldo bancario sin la Asamblea.
4. Activación autorizada por la GG con destino OPERATIVO o ADMINISTRATIVO
   (FIN-TRA-08) y ficha individual con código interno (FIN-TRA-09) al costo
   histórico (precio + flete + instalación).
5. Depreciación y amortización mensual en línea recta desde el último día del
   mes de activación, valor residual cero (FIN-TRA-10): CIF en los sufijos
   30-35 de los centros 6.1.01.01 / 6.1.01.02 o gasto 7.1.02.04-07, contra las
   cuentas reguladoras 4.1.02.01-10.
6. Baja con acta de la GG; verificación del CV si el valor neto supera
   ``baja_activo_cv_sobre_bs``; resultado en 7.2.01.02 (pérdida).
7. Informe semestral de ejecución CAPEX (FIN-TES-04) y acta de verificación
   física anual del CV (INF-CV-01).

Las tasas, topes y plazos se leen de ``parametros``; la estructura de cuentas,
centros y sufijos sigue el Plan de Cuentas v3. No importa ``streamlit``.
"""
from __future__ import annotations

import calendar
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from db.conexion import a_centavos, de_centavos, ejercicio_de_fecha_bd, obtener_parametros_vigentes, transaccion
from servicios import auditoria, contable, costos, seguridad, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Sesion, TipoDecision
from utils.formato import a_fecha, fecha_pantalla, formato_bs, redondear_bs

# --------------------------------------------------------------------------
# Estructura del Plan de Cuentas v3 y de PC-06 (no cifras)
# --------------------------------------------------------------------------
CENTRO_CARNE = costos.CENTRO_CARNE
CENTRO_ALIMENTO = costos.CENTRO_ALIMENTO
OPERATIVO = "OPERATIVO"
ADMINISTRATIVO = "ADMINISTRATIVO"
DESTINOS = (OPERATIVO, ADMINISTRATIVO)
# Cuenta por pagar del activo adquirido: el pago lo hace Tesorería (Fase 3)
# con los controles de CI-FIN-09 (desembolso y firma mancomunada).
CUENTA_POR_PAGAR = "2.1.01.06"
CUENTA_PERDIDA_BAJA = "7.2.01.02"
# DISCREPANCIA: PC-06 §V.C reconoce "ganancia o pérdida" por retiro, pero el
# Plan de Cuentas v3 solo tiene 7.2.01.02 (Pérdida por Retiro o Venta de
# Activos Fijos). La ganancia se registra en 5.2.01.05 (Otros Ingresos No
# Operativos), la cuenta de ingresos no operativos residual del plan.
CUENTA_GANANCIA_BAJA = "5.2.01.05"
CUENTAS_COBRO = ("1.1.01.01", "1.1.01.03", "1.1.01.04")
ORIGEN = "ACTIVOS"
FUENTE = "RECURSOS_PROPIOS"
CAUSAS_BAJA = {"VENTA": "Venta", "OBSOLESCENCIA": "Obsolescencia", "DETERIORO": "Deterioro",
               "SINIESTRO": "Siniestro", "ROBO": "Robo o hurto", "FIN_VIDA_UTIL": "Fin de la vida útil",
               "OTRA": "Otra causa"}
ESTADOS_CONSERVACION = ("BUENO", "REGULAR", "MALO")
CERO = Decimal("0.00")
CIEN = Decimal(100)


@dataclass(frozen=True)
class Categoria:
    """Categoría de activo según PC-06 §V.A/§V.C y el Plan de Cuentas v3."""

    clave: str
    nombre: str
    prefijo: str
    cuenta_activo: str
    cuenta_depreciacion: str | None
    destino: str | None            # None: admite ambos destinos (terrenos)
    centro_costo: str | None
    sufijo: str | None
    cuenta_gasto: str | None
    intangible: bool = False

    @property
    def deprecia(self) -> bool:
        return self.cuenta_depreciacion is not None


CATEGORIAS: dict[str, Categoria] = {c.clave: c for c in (
    Categoria("terrenos", "Terrenos", "TER", "1.2.01.01", None, None, None, None, None),
    Categoria("galpones", "Galpones de producción", "GAL", "1.2.01.02", "4.1.02.01", OPERATIVO, CENTRO_CARNE,
              "30", None),
    Categoria("alambrados", "Alambrados y cercados", "ALA", "1.2.01.03", "4.1.02.02", OPERATIVO, CENTRO_CARNE,
              "32", None),
    Categoria("ambiente_preparacion", "Ambiente de preparación de alimento", "AMB", "1.2.01.04", "4.1.02.03",
              OPERATIVO, CENTRO_ALIMENTO, "35", None),
    Categoria("maquinaria_alimento", "Maquinaria de alimento balanceado", "MAQ", "1.2.01.05", "4.1.02.04",
              OPERATIVO, CENTRO_ALIMENTO, "34", None),
    Categoria("equipos_produccion", "Equipos de producción avícola", "EQP", "1.2.01.06", "4.1.02.05", OPERATIVO,
              CENTRO_CARNE, "31", None),
    Categoria("herramientas", "Herramientas de producción", "HER", "1.2.01.07", "4.1.02.06", OPERATIVO,
              CENTRO_CARNE, "33", None),
    Categoria("muebles_enseres", "Muebles y enseres", "MUE", "1.2.01.08", "4.1.02.07", ADMINISTRATIVO, None, None,
              "7.1.02.04"),
    Categoria("equipos_computacion", "Equipos de computación", "COM", "1.2.01.09", "4.1.02.08", ADMINISTRATIVO,
              None, None, "7.1.02.05"),
    Categoria("gastos_constitucion", "Gastos de constitución y organización", "CON", "1.2.02.01", "4.1.02.09",
              ADMINISTRATIVO, None, None, "7.1.02.06", True),
    Categoria("software", "Software y plataformas digitales", "SOF", "1.2.02.02", "4.1.02.10", ADMINISTRATIVO,
              None, None, "7.1.02.07", True),
)}
CATEGORIA_POR_CUENTA: dict[str, str] = {c.cuenta_activo: c.clave for c in CATEGORIAS.values()}

# Fichas del balance de apertura (1-jul-2026): una por cuenta del activo no
# corriente, con la descripción del Diagnóstico Financiero y del D.5.
FECHA_APERTURA = "2026-07-01"
UBICACION_GRANJA = "Granja Pollolandia — Caranavi"
DESCRIPCION_APERTURA: dict[str, str] = {
    "1.2.01.01": "Terreno de la granja avícola (Caranavi)",
    "1.2.01.02": "Galpones de producción (3 galpones de 1.000 aves, balance de apertura)",
    "1.2.01.03": "Alambrados y cercados perimetrales",
    "1.2.01.06": "Equipos de producción avícola (comederos, bebederos, estufas, balanzas) para 3.000 aves",
    "1.2.01.07": "Herramientas de producción (carretilla, pala, mochila, manguera, cuchillo)",
    "1.2.01.08": "Muebles y enseres de oficina",
    "1.2.01.09": "Equipos de computación",
    "1.2.02.01": "Gastos de constitución y organización",
    "1.2.02.02": "Software contable",
}
# Clave de tasa de cada ficha de apertura distinta de la de PC-06 (ver la
# DISCREPANCIA del parámetro amort_tasa_software_apertura en db/semillas.py).
CLAVE_TASA_APERTURA: dict[str, str] = {"software": "amort_tasa_software_apertura"}


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


def _monto(valor: Any, campo: str, permitir_cero: bool = False) -> Decimal:
    if valor is None or isinstance(valor, bool):
        raise DatosInvalidosError(f"Indique el {campo}.")
    try:
        importe = redondear_bs(valor)
    except Exception as exc:  # noqa: BLE001 - se traduce a un mensaje claro
        raise DatosInvalidosError(f"El {campo} no es un número válido.") from exc
    if importe < 0 or (importe == 0 and not permitir_cero):
        raise DatosInvalidosError(f"El {campo} debe ser mayor a cero.")
    return importe


def _entero(valor: Any, campo: str, minimo: int, maximo: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        raise DatosInvalidosError(f"El campo '{campo}' debe ser un entero entre {minimo} y {maximo}.")
    return valor


def _params(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    return obtener_parametros_vigentes(con, ejercicio_de_fecha_bd(con, fecha))


def _param(p: Mapping[str, Any], clave: str) -> Any:
    if clave not in p:
        raise ReglaNegocioError(f"Falta el parámetro '{clave}' vigente para el ejercicio.",
                                codigo="PARAMETRO_NO_ENCONTRADO", detalle={"clave": clave})
    return p[clave]


def _ejercicio(con: sqlite3.Connection, ejercicio: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM ejercicios WHERE ejercicio = ?", (ejercicio,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el ejercicio {ejercicio}.")
    return fila


def _categoria(clave: Any) -> Categoria:
    if clave not in CATEGORIAS:
        raise DatosInvalidosError(f"Categoría de activo no válida: {clave!r}. Use una de PC-06: "
                                  f"{', '.join(CATEGORIAS)}.")
    return CATEGORIAS[clave]


def fin_de_mes(anio: int, mes: int) -> date:
    """Último día del mes (fecha de la cuota mensual, PC-06 §V.A)."""
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def _periodo(anio: int, mes: int) -> str:
    return f"{anio:04d}-{mes:02d}"


def _meses_entre(desde: date, anio: int, mes: int) -> int:
    """Número de cuota (1 = mes de activación) del mes ``anio-mes``."""
    return (anio - desde.year) * 12 + (mes - desde.month) + 1


def _mes_siguiente(anio: int, mes: int) -> tuple[int, int]:
    return (anio + 1, 1) if mes == 12 else (anio, mes + 1)


def pct_texto(valor: Any, decimales: int = 2) -> str:
    """Fracción como porcentaje de pantalla (0,05 → «5,00 %»)."""
    if valor is None:
        return "—"
    cuant = Decimal(1).scaleb(-decimales)
    return f"{(Decimal(str(valor)) * CIEN).quantize(cuant, rounding=ROUND_HALF_UP)} %".replace(".", ",")


def _exigir(con: sqlite3.Connection, sesion: Sesion, accion: str, rol: str, mensaje: str, codigo: str) -> None:
    seguridad.exigir_permiso(con, sesion, "activos", accion)
    if sesion.rol != rol:
        raise AutorizacionError(mensaje, codigo=codigo)


def _exigir_finanzas(con: sqlite3.Connection, sesion: Sesion, accion: str = "CREAR") -> None:
    _exigir(con, sesion, accion, "JF", "El registro de activos corresponde a la Unidad de Finanzas y "
            "Comercialización (PR-FIN-07).", "SOLO_FINANZAS")


def _exigir_gerencia(con: sqlite3.Connection, sesion: Sesion) -> None:
    _exigir(con, sesion, "AUTORIZAR", "GG", "Corresponde a la Gerencia General (POL-02; PR-FIN-01; PR-FIN-07).",
            "SOLO_GERENCIA")


# ==========================================================================
# 1. TASAS Y CÁLCULO DE LA DEPRECIACIÓN (PC-06)
# ==========================================================================
def clave_tasa(categoria: str, apertura: bool = False) -> str:
    """Parámetro que fija la tasa anual de la categoría."""
    _categoria(categoria)
    if apertura and categoria in CLAVE_TASA_APERTURA:
        return CLAVE_TASA_APERTURA[categoria]
    return f"deprec_tasa_{categoria}"


def tasa_vigente(con: sqlite3.Connection, categoria: str, fecha: Any, apertura: bool = False) -> Decimal:
    """Tasa anual vigente a la fecha (``parametros``, PC-06)."""
    return Decimal(str(_param(_params(con, fecha), clave_tasa(categoria, apertura))))


def acumulada_objetivo(costo: Decimal, tasa: Decimal, meses: int) -> Decimal:
    """Depreciación acumulada tras ``meses`` cuotas: costo × tasa × meses ÷ 12,
    redondeada ROUND_HALF_UP y limitada al costo (valor residual cero).

    Calcular la cuota como diferencia de acumulados hace que cada 12 cuotas
    sumen exactamente la cuota anual (p. ej. 9.625,00 y no 9.624,96).
    """
    if meses <= 0 or tasa <= 0:
        return CERO
    return min(redondear_bs(costo), redondear_bs(Decimal(costo) * Decimal(tasa) * meses / 12))


def cuota_mes(costo: Decimal, tasa: Decimal, numero: int) -> Decimal:
    """Cuota del mes ``numero`` (1 = mes de activación)."""
    return acumulada_objetivo(costo, tasa, numero) - acumulada_objetivo(costo, tasa, numero - 1)


def cuota_anual(costo: Any, tasa: Any) -> Decimal:
    """Depreciación anual en línea recta: costo × tasa (limitada al costo)."""
    return acumulada_objetivo(redondear_bs(costo), Decimal(str(tasa)), 12)


def vida_util_anios(tasa: Decimal) -> Decimal | None:
    """Vida útil implícita en la tasa (1 ÷ tasa), o ``None`` si no deprecia."""
    return None if tasa <= 0 else (Decimal(1) / tasa).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def destino_texto(fila: Mapping[str, Any]) -> str:
    """Cuenta de destino de la cuota: centro-sufijo o cuenta de gasto."""
    fila = dict(fila)
    if fila.get("centro_costo"):
        return f"{fila['centro_costo']}-{fila['sufijo']}"
    if fila.get("cuenta_gasto"):
        return str(fila["cuenta_gasto"])
    return "No se deprecia"


def _validar_destino(categoria: Categoria, destino: Any) -> str:
    if destino not in DESTINOS:
        raise DatosInvalidosError("El destino debe ser OPERATIVO o ADMINISTRATIVO (FIN-TRA-08).")
    if categoria.destino is not None and destino != categoria.destino:
        donde = (f"CIF en {categoria.centro_costo}-{categoria.sufijo}" if categoria.centro_costo
                 else f"gasto {categoria.cuenta_gasto}")
        raise ReglaNegocioError(
            f"{categoria.nombre}: PC-06 asigna su depreciación a {donde}, por lo que el destino es "
            f"{categoria.destino.lower()}.", codigo="DESTINO_NO_CORRESPONDE")
    return str(destino)


def _validar_vigencia(con: sqlite3.Connection, categoria: Categoria, fecha: date) -> None:
    """Las cuentas y sufijos marcados (*) del Plan de Cuentas v3 (planta de
    alimento) rigen desde ``ejercicio_inicio_alimento_propio``."""
    ejercicio = ejercicio_de_fecha_bd(con, fecha)
    inicio = int(_param(obtener_parametros_vigentes(con, ejercicio), "ejercicio_inicio_alimento_propio"))
    marcada = con.execute("SELECT uso_desde_2027 FROM cuentas WHERE codigo = ?", (categoria.cuenta_activo,)).fetchone()
    if (marcada and marcada[0]) or categoria.centro_costo == CENTRO_ALIMENTO:
        if ejercicio < inicio:
            raise ReglaNegocioError(
                f"{categoria.nombre}: la cuenta {categoria.cuenta_activo} y el centro {CENTRO_ALIMENTO} rigen desde "
                f"el ejercicio {inicio}; el activo se activa cuando entra en uso (D.5: opera desde el año "
                "siguiente a su compra).", codigo="CATEGORIA_NO_VIGENTE")


# ==========================================================================
# 2. FICHA INDIVIDUAL Y LIBRO DE INVENTARIO (FIN-TRA-09)
# ==========================================================================
def _siguiente_codigo(con: sqlite3.Connection, categoria: Categoria) -> str:
    n = int(con.execute("SELECT COUNT(*) FROM activos WHERE categoria = ?", (categoria.clave,)).fetchone()[0])
    return f"AF-{categoria.prefijo}-{n + 1:03d}"


def _insertar_activo(con: sqlite3.Connection, sesion: Sesion, *, categoria: Categoria, descripcion: str,
                     destino: str, costo: Decimal, tasa: Decimal, clave: str, fecha: date, origen: str,
                     asiento_id: int, ubicacion: str, activacion_id: int | None = None,
                     proveedor: str | None = None, documento_ref: str | None = None) -> dict[str, Any]:
    codigo = _siguiente_codigo(con, categoria)
    centro = categoria.centro_costo if categoria.deprecia and destino == OPERATIVO else None
    sufijo = categoria.sufijo if centro else None
    gasto = categoria.cuenta_gasto if categoria.deprecia and centro is None else None
    cur = con.execute(
        """INSERT INTO activos (codigo_interno, descripcion, categoria, cuenta_activo, cuenta_depreciacion, destino,
               centro_costo, sufijo, cuenta_gasto, costo_cent, tasa_anual, clave_tasa, fecha_activacion, origen,
               activacion_id, asiento_id, proveedor, documento_ref, ubicacion, registrado_por, registrado_en)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (codigo, descripcion, categoria.clave, categoria.cuenta_activo, categoria.cuenta_depreciacion, destino,
         centro, sufijo, gasto, a_centavos(costo), str(tasa), clave, fecha.isoformat(), origen, activacion_id,
         asiento_id, proveedor, documento_ref, ubicacion, sesion.nombre_usuario, _ahora_iso()))
    aid = int(cur.lastrowid)
    datos = {"codigo_interno": codigo, "descripcion": descripcion, "categoria": categoria.clave, "destino": destino,
             "costo": costo, "tasa_anual": str(tasa), "clave_tasa": clave, "fecha_activacion": fecha.isoformat(),
             "origen": origen, "destino_cuota": destino_texto({"centro_costo": centro, "sufijo": sufijo,
                                                               "cuenta_gasto": gasto})}
    auditoria.registrar(con, sesion, "activos", "INSERT", aid, despues=datos)
    return {"id": aid, **datos}


def registrar_activos_apertura(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    """Abre las fichas FIN-TRA-09 del activo no corriente del balance de
    apertura (1-jul-2026), una por cuenta, al costo registrado en el asiento
    de apertura (no genera asiento nuevo). Se ejecuta una sola vez.

    Raises:
        ReglaNegocioError: las fichas de apertura ya existen o no hay asiento
            de apertura.
    """
    _exigir_finanzas(con, sesion)
    if hay_fichas_apertura(con):
        raise ReglaNegocioError("Las fichas del balance de apertura ya fueron registradas.",
                                codigo="APERTURA_YA_REGISTRADA")
    apertura = con.execute("SELECT id, fecha FROM asientos WHERE tipo = 'APERTURA' ORDER BY id LIMIT 1").fetchone()
    if apertura is None:
        raise ReglaNegocioError("No existe el asiento de apertura.", codigo="SIN_APERTURA")
    fecha = a_fecha(apertura["fecha"])
    creadas = []
    with transaccion(con):
        filas = con.execute(
            """SELECT cuenta_codigo, SUM(debe_cent) - SUM(haber_cent) AS saldo FROM asiento_detalle
                WHERE asiento_id = ? AND (cuenta_codigo LIKE '1.2.01.%' OR cuenta_codigo LIKE '1.2.02.%')
                GROUP BY cuenta_codigo ORDER BY cuenta_codigo""", (int(apertura["id"]),)).fetchall()
        for f in filas:
            if int(f["saldo"]) <= 0 or f["cuenta_codigo"] not in CATEGORIA_POR_CUENTA:
                continue
            cat = CATEGORIAS[CATEGORIA_POR_CUENTA[f["cuenta_codigo"]]]
            clave = clave_tasa(cat.clave, apertura=True)
            tasa = Decimal(str(_param(_params(con, fecha), clave)))
            destino = cat.destino or OPERATIVO
            creadas.append(_insertar_activo(
                con, sesion, categoria=cat, descripcion=DESCRIPCION_APERTURA.get(f["cuenta_codigo"], cat.nombre),
                destino=destino, costo=de_centavos(int(f["saldo"])), tasa=tasa, clave=clave, fecha=fecha,
                origen="APERTURA", asiento_id=int(apertura["id"]), ubicacion=UBICACION_GRANJA,
                documento_ref="Balance de apertura al 01/07/2026"))
        if not creadas:
            raise ReglaNegocioError("El balance de apertura no tiene activo no corriente.", codigo="SIN_ACTIVOS")
        auditoria.registrar(con, sesion, "activos", "CARGA_INICIAL", None,
                            despues={"fichas": [c["codigo_interno"] for c in creadas]},
                            detalle="Fichas FIN-TRA-09 del balance de apertura")
    return creadas


def hay_fichas_apertura(con: sqlite3.Connection) -> bool:
    """``True`` si ya se abrieron las fichas del balance de apertura."""
    return con.execute("SELECT 1 FROM activos WHERE origen = 'APERTURA'").fetchone() is not None


def umbral_baja_cv(con: sqlite3.Connection, fecha: Any) -> Decimal:
    """Valor neto sobre el cual la baja exige la verificación del CV (PR-FIN-07)."""
    return Decimal(str(_param(_params(con, fecha), "baja_activo_cv_sobre_bs")))


def _activo(con: sqlite3.Connection, activo_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM activos WHERE id = ?", (activo_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el activo {activo_id}.")
    return fila


def _baja_vigente(con: sqlite3.Connection, activo_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM bajas_activos WHERE activo_id = ? AND estado <> 'OBSERVADA'",
                       (activo_id,)).fetchone()


def _acumulada(con: sqlite3.Connection, activo_id: int, hasta: Any = None) -> Decimal:
    sql = "SELECT COALESCE(SUM(cuota_cent), 0) FROM depreciaciones WHERE activo_id = ?"
    args: list[Any] = [activo_id]
    if hasta is not None:
        sql += " AND fecha <= ?"
        args.append(a_fecha(hasta).isoformat())
    return de_centavos(int(con.execute(sql, args).fetchone()[0]))


def _ultima_verificacion(con: sqlite3.Connection, activo_id: int) -> dict[str, Any] | None:
    fila = con.execute(
        """SELECT v.id, v.fecha, v.verificado_por, d.existe, d.estado_conservacion, d.observacion
             FROM verificacion_detalle d JOIN verificaciones_fisicas v ON v.id = d.verificacion_id
            WHERE d.activo_id = ? ORDER BY v.fecha DESC, v.id DESC LIMIT 1""", (activo_id,)).fetchone()
    return dict(fila) if fila else None


def _a_dict_activo(con: sqlite3.Connection, a: sqlite3.Row, hasta: Any = None) -> dict[str, Any]:
    costo = de_centavos(int(a["costo_cent"]))
    tasa = Decimal(a["tasa_anual"])
    acumulada = _acumulada(con, int(a["id"]), hasta)
    baja = _baja_vigente(con, int(a["id"]))
    dado_de_baja = baja is not None and baja["estado"] == "CONTABILIZADA" and (
        hasta is None or baja["fecha"] <= a_fecha(hasta).isoformat())
    cat = CATEGORIAS[a["categoria"]]
    return {
        **dict(a), "costo": costo, "tasa": tasa, "vida_util": vida_util_anios(tasa),
        "cuota_mensual": cuota_mes(costo, tasa, 1), "cuota_anual": cuota_anual(costo, tasa),
        "acumulada": acumulada, "valor_neto": costo - acumulada, "categoria_nombre": cat.nombre,
        "destino_cuota": destino_texto(dict(a)), "intangible": cat.intangible,
        "estado": "BAJA" if dado_de_baja else ("EN_BAJA" if baja is not None else "VIGENTE"),
        "baja": dict(baja) if baja else None, "ultima_verificacion": _ultima_verificacion(con, int(a["id"])),
        "depreciado_total": cat.deprecia and acumulada >= costo,
    }


def ficha_activo(con: sqlite3.Connection, sesion: Sesion, activo_id: int, hasta: Any = None) -> dict[str, Any]:
    """Ficha individual FIN-TRA-09: código interno, descripción, fecha de
    activación, costo, vida útil y tasa, depreciación acumulada, valor neto,
    última verificación física y el detalle de cuotas registradas."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    a = _activo(con, activo_id)
    datos = _a_dict_activo(con, a, hasta)
    sql = "SELECT * FROM depreciaciones WHERE activo_id = ?"
    args: list[Any] = [activo_id]
    if hasta is not None:
        sql += " AND fecha <= ?"
        args.append(a_fecha(hasta).isoformat())
    datos["cuotas"] = [{**dict(d), "cuota": de_centavos(int(d["cuota_cent"])),
                        "acumulada": de_centavos(int(d["acumulada_cent"])),
                        "valor_neto": de_centavos(int(d["valor_neto_cent"]))}
                       for d in con.execute(sql + " ORDER BY periodo", args)]
    datos["verificaciones"] = [dict(v) for v in con.execute(
        """SELECT v.fecha, v.verificado_por, d.existe, d.estado_conservacion, d.ubicacion_verificada, d.observacion
             FROM verificacion_detalle d JOIN verificaciones_fisicas v ON v.id = d.verificacion_id
            WHERE d.activo_id = ? ORDER BY v.fecha""", (activo_id,))]
    return datos


def libro_activos(con: sqlite3.Connection, sesion: Sesion, hasta: Any = None,
                  incluir_bajas: bool = True) -> list[dict[str, Any]]:
    """Libro de inventario de activos fijos (registro individualizado, POL-02)."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    sql = "SELECT * FROM activos"
    args: list[Any] = []
    if hasta is not None:
        sql += " WHERE fecha_activacion <= ?"
        args.append(a_fecha(hasta).isoformat())
    filas = [_a_dict_activo(con, a, hasta) for a in con.execute(sql + " ORDER BY cuenta_activo, codigo_interno", args)]
    return filas if incluir_bajas else [f for f in filas if f["estado"] != "BAJA"]


def conciliar_libro(con: sqlite3.Connection, sesion: Sesion, hasta: Any) -> list[dict[str, Any]]:
    """Compara el libro de inventario (fichas) con el mayor: costo por cuenta
    1.2.0x y depreciación acumulada por cuenta 4.1.02.xx."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    h = a_fecha(hasta)
    fichas = libro_activos(con, sesion, h)
    filas = []
    for cat in CATEGORIAS.values():
        propias = [f for f in fichas if f["categoria"] == cat.clave and f["estado"] != "BAJA"]
        costo_libro = sum((f["costo"] for f in propias), CERO)
        costo_mayor = tesoreria.saldo_cuenta(con, cat.cuenta_activo, h)
        filas.append({"cuenta": cat.cuenta_activo, "concepto": cat.nombre, "fichas": len(propias),
                      "libro": costo_libro, "mayor": costo_mayor, "diferencia": costo_mayor - costo_libro})
        if cat.cuenta_depreciacion:
            dep_libro = sum((f["acumulada"] for f in propias), CERO)
            dep_mayor = -tesoreria.saldo_cuenta(con, cat.cuenta_depreciacion, h)
            filas.append({"cuenta": cat.cuenta_depreciacion, "concepto": f"Depreciación acumulada — {cat.nombre}",
                          "fichas": len(propias), "libro": dep_libro, "mayor": dep_mayor,
                          "diferencia": dep_mayor - dep_libro})
    return filas


# ==========================================================================
# 3. DEPRECIACIÓN Y AMORTIZACIÓN MENSUAL (FIN-TRA-10)
# ==========================================================================
def _cuotas_registradas(con: sqlite3.Connection, activo_id: int) -> dict[str, int]:
    return {r["periodo"]: int(r["cuota_cent"]) for r in con.execute(
        "SELECT periodo, cuota_cent FROM depreciaciones WHERE activo_id = ?", (activo_id,))}


def _activos_del_mes(con: sqlite3.Connection, anio: int, mes: int) -> list[dict[str, Any]]:
    """Activos con cuota en el mes: activados hasta el fin de mes, que
    deprecian, sin acta de baja vigente fechada hasta ese mes y sin cuota del
    mes. Señala la primera cuota faltante de meses anteriores."""
    fin = fin_de_mes(anio, mes)
    periodo = _periodo(anio, mes)
    resultado = []
    for a in con.execute("SELECT * FROM activos WHERE fecha_activacion <= ? AND cuenta_depreciacion IS NOT NULL "
                         "ORDER BY codigo_interno", (fin.isoformat(),)):
        tasa = Decimal(a["tasa_anual"])
        if tasa <= 0:
            continue
        baja = _baja_vigente(con, int(a["id"]))
        if baja is not None and baja["fecha"] <= fin.isoformat():
            continue
        registradas = _cuotas_registradas(con, int(a["id"]))
        if periodo in registradas:
            continue
        costo = de_centavos(int(a["costo_cent"]))
        acumulada = de_centavos(sum(registradas.values()))
        if acumulada >= costo:
            continue
        activacion = a_fecha(a["fecha_activacion"])
        numero = _meses_entre(activacion, anio, mes)
        faltante = None
        y, m = activacion.year, activacion.month
        while (y, m) < (anio, mes):
            if _periodo(y, m) not in registradas:
                faltante = _periodo(y, m)
                break
            y, m = _mes_siguiente(y, m)
        cuota = min(cuota_mes(costo, tasa, numero), costo - acumulada) if faltante is None else CERO
        resultado.append({"activo": a, "numero": numero, "cuota": cuota, "acumulada_previa": acumulada,
                          "costo": costo, "tasa": tasa, "faltante": faltante})
    return resultado


def _ciclo_costeado_en(con: sqlite3.Connection, fecha: date) -> sqlite3.Row | None:
    """Ciclo con cierre de costos cuyo período (recepción a cierre) contiene la fecha."""
    return con.execute(
        """SELECT c.id, c.numero, c.ejercicio FROM costos_ciclo cc JOIN ciclos_produccion c ON c.id = cc.ciclo_id
            WHERE c.fecha_inicio <= ? AND cc.fecha_cierre >= ? ORDER BY c.fecha_inicio LIMIT 1""",
        (fecha.isoformat(), fecha.isoformat())).fetchone()


def planilla_depreciacion(con: sqlite3.Connection, sesion: Sesion, anio: int, mes: int) -> dict[str, Any]:
    """Vista previa de la planilla FIN-TRA-10 del mes (sin registrar)."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    _entero(mes, "mes", 1, 12)
    _entero(anio, "año", 2000, 9998)
    filas = []
    for x in _activos_del_mes(con, anio, mes):
        a = x["activo"]
        filas.append({"activo_id": int(a["id"]), "codigo_interno": a["codigo_interno"], "descripcion": a["descripcion"],
                      "costo": x["costo"], "tasa": x["tasa"], "cuota": x["cuota"],
                      "acumulada": x["acumulada_previa"] + x["cuota"],
                      "valor_neto": x["costo"] - x["acumulada_previa"] - x["cuota"],
                      "destino": destino_texto(a), "credito": a["cuenta_depreciacion"], "numero": x["numero"],
                      "faltante": x["faltante"]})
    registradas = [{**dict(r), "total": de_centavos(int(r["total_cent"]))} for r in con.execute(
        "SELECT * FROM depreciacion_meses WHERE anio = ? AND mes = ? ORDER BY id", (anio, mes))]
    registrado = []
    for d in con.execute(
            """SELECT d.*, a.codigo_interno, a.descripcion, a.costo_cent, a.tasa_anual, a.centro_costo, a.sufijo,
                      a.cuenta_gasto, a.cuenta_depreciacion
                 FROM depreciaciones d JOIN activos a ON a.id = d.activo_id
                WHERE d.periodo = ? ORDER BY a.codigo_interno""", (_periodo(anio, mes),)):
        registrado.append({"activo_id": int(d["activo_id"]), "codigo_interno": d["codigo_interno"],
                           "descripcion": d["descripcion"], "costo": de_centavos(int(d["costo_cent"])),
                           "tasa": Decimal(d["tasa_anual"]), "cuota": de_centavos(int(d["cuota_cent"])),
                           "acumulada": de_centavos(int(d["acumulada_cent"])),
                           "valor_neto": de_centavos(int(d["valor_neto_cent"])), "destino": destino_texto(dict(d)),
                           "credito": d["cuenta_depreciacion"], "numero": int(d["meses_transcurridos"])})
    return {"anio": anio, "mes": mes, "fecha": fin_de_mes(anio, mes), "filas": filas,
            "total": sum((f["cuota"] for f in filas), CERO), "registradas": registradas, "registrado": registrado,
            "total_registrado": sum((f["cuota"] for f in registrado), CERO),
            "pendientes_previos": sorted({f["faltante"] for f in filas if f["faltante"]})}


def depreciar_mes(con: sqlite3.Connection, sesion: Sesion, anio: int, mes: int) -> dict[str, Any]:
    """Registra la depreciación y amortización del mes (FIN-TRA-10) en un
    asiento con fecha del último día del mes: CIF en los sufijos 30-35 de los
    centros de costo (destino operativo) o gasto 7.1.02.04-07 (administrativo),
    contra las cuentas reguladoras 4.1.02.xx.

    Raises:
        ReglaNegocioError: meses anteriores sin depreciar, mes ya registrado,
            nada que depreciar o fecha dentro de un ciclo ya costeado.
        PeriodoCerradoError: período contable cerrado.
    """
    _exigir_finanzas(con, sesion)
    _entero(mes, "mes", 1, 12)
    _entero(anio, "año", 2000, 9998)
    fecha = fin_de_mes(anio, mes)
    nombre_mes = f"{contable.NOMBRE_MES[mes - 1]} de {anio}"
    candidatos = _activos_del_mes(con, anio, mes)
    faltantes = sorted({c["faltante"] for c in candidatos if c["faltante"]})
    if faltantes:
        primero = next(c for c in candidatos if c["faltante"] == faltantes[0])
        raise ReglaNegocioError(
            f"Registre primero la depreciación de {faltantes[0]} (activo {primero['activo']['codigo_interno']}): "
            "las cuotas son mensuales y consecutivas desde el mes de activación (PC-06).",
            codigo="DEPRECIACION_PENDIENTE", detalle={"pendientes": faltantes})
    candidatos = [c for c in candidatos if c["cuota"] > 0]
    if not candidatos:
        ya = con.execute("SELECT 1 FROM depreciacion_meses WHERE anio = ? AND mes = ?", (anio, mes)).fetchone()
        if ya:
            raise ReglaNegocioError(f"La depreciación de {nombre_mes} ya está registrada.", codigo="MES_YA_DEPRECIADO")
        raise ReglaNegocioError(f"No hay activos que depreciar en {nombre_mes}.", codigo="SIN_DEPRECIACION")
    if any(c["activo"]["centro_costo"] == CENTRO_CARNE for c in candidatos):
        cerrado = _ciclo_costeado_en(con, fecha)
        if cerrado is not None:
            raise ReglaNegocioError(
                f"El {fecha_pantalla(fecha)} pertenece al ciclo {cerrado['numero']} del ejercicio "
                f"{cerrado['ejercicio']}, que ya tiene cierre de costos: la depreciación del mes debe registrarse "
                "antes del cierre del ciclo (FIN-TRA-06).", codigo="CICLO_COSTEADO")
    debitos: dict[tuple[str, str | None], Decimal] = {}
    creditos: dict[str, Decimal] = {}
    for c in candidatos:
        a = c["activo"]
        clave = (a["centro_costo"] or a["cuenta_gasto"], a["sufijo"])
        debitos[clave] = debitos.get(clave, CERO) + c["cuota"]
        creditos[a["cuenta_depreciacion"]] = creditos.get(a["cuenta_depreciacion"], CERO) + c["cuota"]
    lineas: list[dict[str, Any]] = []
    for (cuenta, sufijo), monto in sorted(debitos.items(), key=lambda x: (x[0][0], x[0][1] or "")):
        linea: dict[str, Any] = {"cuenta": cuenta, "debe": monto}
        if sufijo:
            linea.update(centro_costo=cuenta, sufijo=sufijo)
        lineas.append(linea)
    lineas += [{"cuenta": cuenta, "haber": monto} for cuenta, monto in sorted(creditos.items())]
    total = sum(creditos.values(), CERO)
    with transaccion(con):
        asiento = contable.crear_asiento(
            con, sesion, fecha, f"Depreciación y amortización de {nombre_mes} (PC-06, línea recta)", lineas,
            ORIGEN, f"FIN-TRA-10 {_periodo(anio, mes)}", tipo="TRASPASO")
        cur = con.execute(
            "INSERT INTO depreciacion_meses (anio, mes, fecha, total_cent, asiento_id, registrado_por, registrado_en) "
            "VALUES (?,?,?,?,?,?,?)",
            (anio, mes, fecha.isoformat(), a_centavos(total), asiento.id, sesion.nombre_usuario, _ahora_iso()))
        mes_id = int(cur.lastrowid)
        for c in candidatos:
            acumulada = c["acumulada_previa"] + c["cuota"]
            con.execute(
                """INSERT INTO depreciaciones (activo_id, mes_id, periodo, fecha, cuota_cent, acumulada_cent,
                       valor_neto_cent, meses_transcurridos) VALUES (?,?,?,?,?,?,?,?)""",
                (int(c["activo"]["id"]), mes_id, _periodo(anio, mes), fecha.isoformat(), a_centavos(c["cuota"]),
                 a_centavos(acumulada), a_centavos(c["costo"] - acumulada), c["numero"]))
        auditoria.registrar(con, sesion, "depreciacion_meses", "INSERT", mes_id,
                            despues={"periodo": _periodo(anio, mes), "total": total, "activos": len(candidatos),
                                     "asiento_id": asiento.id})
    return {"id": mes_id, "asiento_id": asiento.id, "fecha": fecha, "total": total, "activos": len(candidatos),
            "lineas": lineas}


def meses_pendientes(con: sqlite3.Connection, hasta: Any) -> list[str]:
    """Meses cerrados (hasta el fin del mes anterior a ``hasta``, o el mes de
    ``hasta`` si es su último día) con cuotas aún no registradas."""
    h = a_fecha(hasta)
    anio, mes = h.year, h.month
    if h != fin_de_mes(anio, mes):
        anio, mes = (anio - 1, 12) if mes == 1 else (anio, mes - 1)
    pendientes: set[str] = set()
    for a in con.execute("SELECT * FROM activos WHERE cuenta_depreciacion IS NOT NULL AND fecha_activacion <= ?",
                         (fin_de_mes(anio, mes).isoformat(),)):
        if Decimal(a["tasa_anual"]) <= 0:
            continue
        registradas = _cuotas_registradas(con, int(a["id"]))
        costo_cent = int(a["costo_cent"])
        acumulada = sum(registradas.values())
        baja = _baja_vigente(con, int(a["id"]))
        y, m = a_fecha(a["fecha_activacion"]).year, a_fecha(a["fecha_activacion"]).month
        while (y, m) <= (anio, mes) and acumulada < costo_cent:
            if baja is not None and baja["fecha"] <= fin_de_mes(y, m).isoformat():
                break
            if _periodo(y, m) not in registradas:
                pendientes.add(_periodo(y, m))
            y, m = _mes_siguiente(y, m)
    return sorted(pendientes)


def listar_depreciaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return [{**dict(r), "total": de_centavos(int(r["total_cent"])),
             "activos": int(con.execute("SELECT COUNT(*) FROM depreciaciones WHERE mes_id = ?", (r["id"],)).fetchone()[0])}
            for r in con.execute("SELECT * FROM depreciacion_meses ORDER BY anio DESC, mes DESC, id DESC")]


def depreciacion_del_periodo(con: sqlite3.Connection, activo_id: int, desde: Any, hasta: Any) -> Decimal:
    """Suma de cuotas registradas de un activo entre dos fechas (inclusive)."""
    return de_centavos(int(con.execute(
        "SELECT COALESCE(SUM(cuota_cent), 0) FROM depreciaciones WHERE activo_id = ? AND fecha BETWEEN ? AND ?",
        (activo_id, a_fecha(desde).isoformat(), a_fecha(hasta).isoformat())).fetchone()[0]))


# ==========================================================================
# 4. PLAN ANUAL CAPEX (FIN-TES-01) Y ANÁLISIS ROA / SME (FIN-TES-02, -03)
# ==========================================================================
def _plan(con: sqlite3.Connection, plan_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM planes_capex WHERE id = ?", (plan_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el Plan CAPEX {plan_id}.")
    return fila


def _linea(con: sqlite3.Connection, linea_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM capex_lineas WHERE id = ?", (linea_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la línea CAPEX {linea_id}.")
    return fila


def fecha_limite_plan(con: sqlite3.Connection, ejercicio: int) -> date:
    """Plazo de POL-02 Art. 1: el plan del ejercicio se elabora antes del
    ``capex_plan_dia_limite``/``capex_plan_mes_limite`` del año en que inicia."""
    p = obtener_parametros_vigentes(con, ejercicio)
    return date(ejercicio, int(_param(p, "capex_plan_mes_limite")), int(_param(p, "capex_plan_dia_limite")))


def crear_plan(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, fecha: Any,
               importar_d5: bool = False) -> dict[str, Any]:
    """La Gerencia General abre el Plan CAPEX del ejercicio (FIN-TES-01). Con
    ``importar_d5`` carga como líneas previstas el presupuesto D.5 del
    ejercicio (que la GG puede retirar o complementar antes de presentarlo).

    Raises:
        ReglaNegocioError: ya existe el plan del ejercicio.
    """
    _exigir_gerencia(con, sesion)
    _entero(ejercicio, "ejercicio", 2000, 9998)
    _ejercicio(con, ejercicio)
    f = a_fecha(fecha)
    if con.execute("SELECT 1 FROM planes_capex WHERE ejercicio = ?", (ejercicio,)).fetchone():
        raise ReglaNegocioError(f"Ya existe el Plan CAPEX del ejercicio {ejercicio}.", codigo="PLAN_EXISTENTE")
    limite = fecha_limite_plan(con, ejercicio)
    fuera = f > limite
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO planes_capex (ejercicio, fecha_elaboracion, fecha_limite, fuera_de_plazo, elaborado_por,
                   registrado_en) VALUES (?,?,?,?,?,?)""",
            (ejercicio, f.isoformat(), limite.isoformat(), int(fuera), sesion.nombre_usuario, _ahora_iso()))
        pid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "planes_capex", "INSERT", pid,
                            despues={"ejercicio": ejercicio, "fecha": f.isoformat(), "fecha_limite": limite.isoformat(),
                                     "fuera_de_plazo": fuera})
        lineas = []
        if importar_d5:
            ejer = _ejercicio(con, ejercicio)
            for r in con.execute("SELECT * FROM capex_referencia WHERE ejercicio = ? ORDER BY orden", (ejercicio,)):
                lineas.append(_insertar_linea(
                    con, sesion, pid, ejercicio, activo=r["activo"], categoria=r["categoria"],
                    costo=de_centavos(int(r["monto_cent"])), justificacion=r["justificacion"], ciclo=int(r["ciclo"]),
                    fecha_estimada=a_fecha(ejer["fecha_fin"]), proveedor=None, previsto=True,
                    referencia_d5_id=int(r["id"])))
    return {"id": pid, "fecha_limite": limite, "fuera_de_plazo": fuera, "lineas": lineas}


def _insertar_linea(con: sqlite3.Connection, sesion: Sesion, plan_id: int, ejercicio: int, *, activo: str,
                    categoria: str, costo: Decimal, justificacion: str, ciclo: int, fecha_estimada: date,
                    proveedor: str | None, previsto: bool, autorizacion_id: int | None = None,
                    referencia_d5_id: int | None = None) -> int:
    ejer = _ejercicio(con, ejercicio)
    if fecha_estimada < a_fecha(ejer["fecha_inicio"]):
        raise DatosInvalidosError("La fecha estimada de ejecución no puede ser anterior al inicio del ejercicio.")
    cur = con.execute(
        """INSERT INTO capex_lineas (plan_id, activo, categoria, costo_estimado_cent, proveedor_referencia,
               justificacion, fuente, ciclo, fecha_estimada, previsto, autorizacion_id, referencia_d5_id,
               registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (plan_id, activo, categoria, a_centavos(costo), proveedor, justificacion, FUENTE, ciclo,
         fecha_estimada.isoformat(), int(previsto), autorizacion_id, referencia_d5_id, sesion.nombre_usuario,
         _ahora_iso()))
    lid = int(cur.lastrowid)
    auditoria.registrar(con, sesion, "capex_lineas", "INSERT", lid,
                        despues={"plan_id": plan_id, "activo": activo, "categoria": categoria, "costo": costo,
                                 "fuente": FUENTE, "ciclo": ciclo, "previsto": previsto,
                                 "autorizacion_id": autorizacion_id})
    return lid


def _validar_datos_linea(activo: Any, categoria: Any, costo: Any, justificacion: Any, ciclo: Any,
                         fecha_estimada: Any, proveedor: Any, fuente: Any) -> dict[str, Any]:
    if fuente != FUENTE:
        raise ReglaNegocioError("POL-02 Art. 2: el CAPEX se financia 100 % con recursos propios; la deuda bancaria "
                                "exige a la Asamblea General con mayoría calificada y no se registra en este plan.",
                                codigo="FUENTE_NO_PERMITIDA")
    cat = _categoria(categoria)
    return {"activo": _texto(activo, "activo", 200), "categoria": cat.clave, "costo": _monto(costo, "costo estimado"),
            "justificacion": _texto(justificacion, "justificación técnica", 500),
            "ciclo": _entero(ciclo, "ciclo", 1, 12), "fecha_estimada": a_fecha(fecha_estimada),
            "proveedor": _opcional(proveedor, "proveedor referencial", 200)}


def agregar_linea(con: sqlite3.Connection, sesion: Sesion, plan_id: int, activo: str, categoria: str, costo: Any,
                  justificacion: str, ciclo: int, fecha_estimada: Any, *, proveedor_referencia: str | None = None,
                  fuente: str = FUENTE) -> int:
    """Agrega un activo previsto al Plan CAPEX en borrador: activo, costo
    estimado, proveedor referencial, justificación técnica, fuente (recursos
    propios), ciclo y fecha de ejecución (POL-02 Art. 1)."""
    _exigir_gerencia(con, sesion)
    plan = _plan(con, plan_id)
    if plan["estado"] != "BORRADOR":
        raise ReglaNegocioError("El plan ya fue presentado: las inversiones nuevas son no previstas (POL-02 Art. 1).",
                                codigo="PLAN_NO_EDITABLE")
    d = _validar_datos_linea(activo, categoria, costo, justificacion, ciclo, fecha_estimada, proveedor_referencia,
                             fuente)
    with transaccion(con):
        return _insertar_linea(con, sesion, plan_id, int(plan["ejercicio"]), activo=d["activo"],
                               categoria=d["categoria"], costo=d["costo"], justificacion=d["justificacion"],
                               ciclo=d["ciclo"], fecha_estimada=d["fecha_estimada"], proveedor=d["proveedor"],
                               previsto=True)


def retirar_linea(con: sqlite3.Connection, sesion: Sesion, linea_id: int, motivo: str) -> None:
    """Retira una línea del plan en borrador (no se borra: queda en la bitácora)."""
    _exigir_gerencia(con, sesion)
    linea = _linea(con, linea_id)
    if _plan(con, int(linea["plan_id"]))["estado"] != "BORRADOR" or linea["estado"] != "VIGENTE":
        raise ReglaNegocioError("Solo se retiran líneas vigentes de un plan en borrador.", codigo="PLAN_NO_EDITABLE")
    texto = _texto(motivo, "motivo", 300)
    with transaccion(con):
        con.execute("UPDATE capex_lineas SET estado = 'RETIRADA' WHERE id = ?", (linea_id,))
        auditoria.registrar(con, sesion, "capex_lineas", "UPDATE", linea_id, antes={"estado": "VIGENTE"},
                            despues={"estado": "RETIRADA"}, detalle=texto)


def kg_anuales(con: sqlite3.Connection, ejercicio: int) -> Decimal:
    """Kg de carne del ejercicio: aves por ciclo × ciclos × rendimiento (POA)."""
    p = obtener_parametros_vigentes(con, ejercicio)
    return (Decimal(int(_param(p, "aves_por_ciclo"))) * int(_param(p, "ciclos_por_ejercicio"))
            * Decimal(str(_param(p, "rendimiento_kg_ave"))))


def impacto_linea(con: sqlite3.Connection, categoria: str, costo: Decimal, ejercicio: int) -> dict[str, Any]:
    """Impacto en la depreciación mensual/anual y en el costo unitario Bs/kg
    (POL-02 Art. 1 inciso vi). El activo opera desde el ejercicio siguiente
    (D.5); si ese ejercicio no tiene parámetros, se usa el del plan."""
    cat = _categoria(categoria)
    operacion = ejercicio + 1
    try:
        p = obtener_parametros_vigentes(con, operacion)
        _param(p, "aves_por_ciclo")
        tasa = Decimal(str(_param(p, clave_tasa(cat.clave))))
    except ReglaNegocioError:
        operacion = ejercicio
        tasa = Decimal(str(_param(obtener_parametros_vigentes(con, ejercicio), clave_tasa(cat.clave))))
    anual = cuota_anual(costo, tasa)
    kg = kg_anuales(con, operacion)
    por_kg = (anual / kg).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) \
        if cat.centro_costo and kg > 0 else Decimal("0.0000")
    return {"tasa": tasa, "mensual": cuota_mes(costo, tasa, 1), "anual": anual, "kg_anuales": kg,
            "bs_por_kg": por_kg, "ejercicio_operacion": operacion,
            "destino": destino_texto({"centro_costo": cat.centro_costo, "sufijo": cat.sufijo,
                                      "cuenta_gasto": cat.cuenta_gasto}) if cat.deprecia else "No se deprecia"}


def _ejecutado_linea(con: sqlite3.Connection, linea_id: int, hasta: Any = None, solo_registradas: bool = False,
                     excluir_activacion: int | None = None) -> Decimal:
    sql = "SELECT COALESCE(SUM(costo_cent), 0) FROM activaciones WHERE capex_linea_id = ? AND estado <> 'ANULADA'"
    args: list[Any] = [linea_id]
    if solo_registradas:
        sql += " AND estado = 'REGISTRADA'"
    if hasta is not None:
        sql += " AND fecha_disponible <= ?"
        args.append(a_fecha(hasta).isoformat())
    if excluir_activacion is not None:
        sql += " AND id <> ?"
        args.append(excluir_activacion)
    return de_centavos(int(con.execute(sql, args).fetchone()[0]))


def detalle_plan(con: sqlite3.Connection, sesion: Sesion, plan_id: int) -> dict[str, Any]:
    """Plan CAPEX con sus líneas, impacto en depreciación y costo unitario,
    ejecución y el análisis FIN-TES-02."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    plan = _plan(con, plan_id)
    ejercicio = int(plan["ejercicio"])
    lineas = []
    for l in con.execute("SELECT * FROM capex_lineas WHERE plan_id = ? ORDER BY previsto DESC, id", (plan_id,)):
        costo = de_centavos(int(l["costo_estimado_cent"]))
        ejecutado = _ejecutado_linea(con, int(l["id"]), solo_registradas=True)
        lineas.append({**dict(l), "costo": costo, "categoria_nombre": CATEGORIAS[l["categoria"]].nombre,
                       "impacto": impacto_linea(con, l["categoria"], costo, ejercicio), "ejecutado": ejecutado,
                       "autorizado": _ejecutado_linea(con, int(l["id"]))})
    vigentes = [l for l in lineas if l["estado"] == "VIGENTE"]
    datos = dict(plan)
    for campo in ("utilidad_neta", "activos_totales", "inversion_total", "saldo_proyectado", "sme",
                  "saldo_post_inversion"):
        valor = plan[f"{campo}_cent"]
        datos[campo] = de_centavos(int(valor)) if valor is not None else None
    datos["roa"] = Decimal(plan["roa"]) if plan["roa"] is not None else None
    datos.update(lineas=lineas, total_previsto=sum((l["costo"] for l in vigentes if l["previsto"]), CERO),
                 total_no_previsto=sum((l["costo"] for l in vigentes if not l["previsto"]), CERO),
                 total=sum((l["costo"] for l in vigentes), CERO),
                 depreciacion_anual=sum((l["impacto"]["anual"] for l in vigentes), CERO),
                 depreciacion_mensual=sum((l["impacto"]["mensual"] for l in vigentes), CERO),
                 bs_por_kg=sum((l["impacto"]["bs_por_kg"] for l in vigentes), Decimal("0.0000")),
                 ejecutado=sum((l["ejecutado"] for l in vigentes), CERO))
    datos["ejecucion_pct"] = (datos["ejecutado"] / datos["total"]).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) \
        if datos["total"] > 0 else None
    return datos


def activos_totales_libros(con: sqlite3.Connection, fecha: Any) -> Decimal:
    """Activo total neto según libros: Grupo 1 menos las reguladoras de activo (4.1)."""
    f = a_fecha(fecha).isoformat()
    fila = con.execute(
        """SELECT COALESCE(SUM(d.debe_cent), 0) - COALESCE(SUM(d.haber_cent), 0)
             FROM asiento_detalle d JOIN asientos a ON a.id = d.asiento_id
            WHERE a.fecha <= ? AND (d.cuenta_codigo LIKE '1.%' OR d.cuenta_codigo LIKE '4.1.%')""", (f,)).fetchone()
    return de_centavos(int(fila[0]))


def semaforo_roa(con: sqlite3.Connection, fecha: Any, roa: Decimal) -> str:
    """ROA: rojo bajo ``roa_umbral_malo``; verde sobre ``roa_umbral_bueno`` (MAN-05)."""
    p = _params(con, fecha)
    if roa < Decimal(str(_param(p, "roa_umbral_malo"))):
        return "ROJO"
    if roa > Decimal(str(_param(p, "roa_umbral_bueno"))):
        return "VERDE"
    return "AMARILLO"


def calcular_roa_sme(con: sqlite3.Connection, ejercicio: int, fecha: Any, utilidad_neta: Any, inversion: Any,
                     activos_totales: Any = None, saldo_proyectado: Any = None) -> dict[str, Any]:
    """FIN-TES-02: ROA proyectado = Utilidad neta anual ÷ (Activos totales +
    nueva inversión) y compatibilidad con el SME del ejercicio: el saldo
    bancario proyectado menos la inversión no debe quedar bajo el SME
    (POL-04 Art. 1); bajo el ``sme_umbral_compromiso`` exige autorización
    escrita de la GG (POL-04 Art. 2)."""
    f = a_fecha(fecha)
    utilidad = redondear_bs(utilidad_neta)
    invertir = _monto(inversion, "monto de la inversión", permitir_cero=True)
    activos = activos_totales_libros(con, f) if activos_totales is None else _monto(activos_totales, "activo total")
    base = activos + invertir
    if base <= 0:
        raise DatosInvalidosError("El activo total más la inversión debe ser mayor a cero.")
    roa = (utilidad / base).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    inicio = a_fecha(_ejercicio(con, ejercicio)["fecha_inicio"])
    sme = tesoreria.sme_vigente(con, inicio)
    saldo = tesoreria.saldo_bancario(con, f) if saldo_proyectado is None else redondear_bs(saldo_proyectado)
    post = saldo - invertir
    factor = Decimal(str(_param(obtener_parametros_vigentes(con, ejercicio), "sme_umbral_compromiso")))
    umbral = redondear_bs(sme * factor)
    return {"utilidad_neta": utilidad, "activos_totales": activos, "inversion": invertir, "activos_con_inversion": base,
            "roa": roa, "semaforo": semaforo_roa(con, inicio, roa), "saldo_proyectado": saldo, "sme": sme,
            "saldo_post_inversion": post, "compatible_sme": post >= sme, "umbral_compromiso": umbral,
            "bajo_umbral_compromiso": post < umbral}


def analizar_plan(con: sqlite3.Connection, sesion: Sesion, plan_id: int, fecha: Any, utilidad_neta: Any, *,
                  activos_totales: Any = None, saldo_proyectado: Any = None) -> dict[str, Any]:
    """La Unidad de Finanzas calcula el ROA proyectado con la inversión del
    plan y su compatibilidad con el SME (FIN-TES-02; PR-FIN-01 paso 2)."""
    _exigir_finanzas(con, sesion, "EDITAR")
    plan = _plan(con, plan_id)
    if plan["estado"] != "BORRADOR":
        raise ReglaNegocioError("El análisis se hace antes de presentar el plan al CA.", codigo="PLAN_NO_EDITABLE")
    total = de_centavos(int(con.execute(
        "SELECT COALESCE(SUM(costo_estimado_cent), 0) FROM capex_lineas WHERE plan_id = ? AND estado = 'VIGENTE'",
        (plan_id,)).fetchone()[0]))
    if total <= 0:
        raise ReglaNegocioError("El plan no tiene inversiones vigentes que analizar.", codigo="PLAN_VACIO")
    r = calcular_roa_sme(con, int(plan["ejercicio"]), fecha, utilidad_neta, total, activos_totales, saldo_proyectado)
    f = a_fecha(fecha)
    with transaccion(con):
        con.execute(
            """UPDATE planes_capex SET analisis_fecha = ?, utilidad_neta_cent = ?, activos_totales_cent = ?,
                   inversion_total_cent = ?, roa = ?, roa_semaforo = ?, saldo_proyectado_cent = ?, sme_cent = ?,
                   saldo_post_inversion_cent = ?, compatible_sme = ?, bajo_umbral_compromiso = ?, analizado_por = ?
               WHERE id = ?""",
            (f.isoformat(), a_centavos(r["utilidad_neta"]), a_centavos(r["activos_totales"]), a_centavos(total),
             str(r["roa"]), r["semaforo"], a_centavos(r["saldo_proyectado"]), a_centavos(r["sme"]),
             a_centavos(r["saldo_post_inversion"]), int(r["compatible_sme"]), int(r["bajo_umbral_compromiso"]),
             sesion.nombre_usuario, plan_id))
        auditoria.registrar(con, sesion, "planes_capex", "UPDATE", plan_id,
                            antes={"roa": plan["roa"], "compatible_sme": plan["compatible_sme"]},
                            despues={"roa": r["roa"], "semaforo": r["semaforo"], "compatible_sme": r["compatible_sme"],
                                     "inversion": total, "fecha": f.isoformat()})
    return r


def presentar_plan(con: sqlite3.Connection, sesion: Sesion, plan_id: int, resumen: str, recomendacion: str,
                   fecha: Any = None) -> None:
    """La GG presenta el plan y el análisis al CA (FIN-TES-03)."""
    _exigir_gerencia(con, sesion)
    plan = _plan(con, plan_id)
    if plan["estado"] != "BORRADOR":
        raise ReglaNegocioError(f"El plan está {plan['estado'].lower()}.", codigo="PLAN_NO_EDITABLE")
    if plan["roa"] is None:
        raise ReglaNegocioError("Falta el análisis de ROA y SME de la Unidad de Finanzas (FIN-TES-02).",
                                codigo="SIN_ANALISIS")
    total = int(con.execute("SELECT COALESCE(SUM(costo_estimado_cent), 0) FROM capex_lineas WHERE plan_id = ? "
                            "AND estado = 'VIGENTE'", (plan_id,)).fetchone()[0])
    if total != int(plan["inversion_total_cent"]):
        raise ReglaNegocioError("Las líneas del plan cambiaron después del análisis: actualice el FIN-TES-02.",
                                codigo="ANALISIS_DESACTUALIZADO")
    f = a_fecha(fecha) if fecha is not None else date.today()
    with transaccion(con):
        con.execute("UPDATE planes_capex SET estado = 'PRESENTADO', resumen = ?, recomendacion = ?, "
                    "presentado_por = ?, presentado_en = ? WHERE id = ?",
                    (_texto(resumen, "resumen", 1000), _texto(recomendacion, "recomendación", 1000),
                     sesion.nombre_usuario, f.isoformat(), plan_id))
        auditoria.registrar(con, sesion, "planes_capex", "UPDATE", plan_id, antes={"estado": "BORRADOR"},
                            despues={"estado": "PRESENTADO", "fecha": f.isoformat()})


def resolver_plan_ca(con: sqlite3.Connection, sesion: Sesion, plan_id: int, decision: str, acta_ref: str,
                     observacion: str | None = None, fecha: Any = None) -> str:
    """El Consejo de Administración aprueba el plan (``APRUEBA``) o lo devuelve
    con observaciones a borrador (``OBSERVA``) (PR-FIN-01 paso 4)."""
    seguridad.exigir_permiso(con, sesion, "activos", "APROBAR")
    if sesion.rol != "CA":
        raise AutorizacionError("La aprobación del Plan CAPEX corresponde al Consejo de Administración.",
                                codigo="SOLO_CA")
    if decision not in ("APRUEBA", "OBSERVA"):
        raise DatosInvalidosError("La decisión debe ser APRUEBA u OBSERVA.")
    plan = _plan(con, plan_id)
    if plan["estado"] != "PRESENTADO":
        raise ReglaNegocioError("El CA resuelve planes presentados por la Gerencia (FIN-TES-03).",
                                codigo="PLAN_NO_PRESENTADO")
    acta = _texto(acta_ref, "acta del Consejo de Administración", 120)
    obs = _opcional(observacion, "observación", 1000)
    if decision == "OBSERVA" and not obs:
        raise DatosInvalidosError("Registre las observaciones del CA.")
    f = a_fecha(fecha) if fecha is not None else date.today()
    nuevo = "APROBADO" if decision == "APRUEBA" else "BORRADOR"
    with transaccion(con):
        if nuevo == "APROBADO":
            con.execute("UPDATE planes_capex SET estado = 'APROBADO', aprobado_por = ?, acta_ca = ?, aprobado_en = ?, "
                        "observacion_ca = ? WHERE id = ?", (sesion.nombre_usuario, acta, f.isoformat(), obs, plan_id))
        else:
            con.execute("UPDATE planes_capex SET estado = 'BORRADOR', observacion_ca = ?, presentado_por = NULL, "
                        "presentado_en = NULL WHERE id = ?", (f"{acta}: {obs}", plan_id))
        auditoria.registrar(con, sesion, "planes_capex", "AUTORIZACION", plan_id, antes={"estado": "PRESENTADO"},
                            despues={"estado": nuevo, "acta": acta, "observacion": obs})
    return nuevo


def ratificar_plan(con: sqlite3.Connection, sesion: Sesion, plan_id: int, acta_ref: str, fecha: Any = None) -> None:
    """La Asamblea General ratifica los montos del plan aprobado (D.5; PR-FIN-01)."""
    seguridad.exigir_permiso(con, sesion, "activos", "APROBAR")
    if sesion.rol != "AG":
        raise AutorizacionError("La ratificación corresponde a la Asamblea General.", codigo="SOLO_AG")
    plan = _plan(con, plan_id)
    if plan["estado"] != "APROBADO":
        raise ReglaNegocioError("La Asamblea ratifica planes aprobados por el CA.", codigo="PLAN_NO_APROBADO")
    acta = _texto(acta_ref, "acta de la Asamblea", 120)
    f = a_fecha(fecha) if fecha is not None else date.today()
    with transaccion(con):
        con.execute("UPDATE planes_capex SET estado = 'RATIFICADO', ratificado_por = ?, acta_ag = ?, ratificado_en = ? "
                    "WHERE id = ?", (sesion.nombre_usuario, acta, f.isoformat(), plan_id))
        auditoria.registrar(con, sesion, "planes_capex", "AUTORIZACION", plan_id, antes={"estado": "APROBADO"},
                            despues={"estado": "RATIFICADO", "acta": acta})


def plan_del_ejercicio(con: sqlite3.Connection, ejercicio: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM planes_capex WHERE ejercicio = ?", (ejercicio,)).fetchone()


def listar_planes(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return [dict(p) for p in con.execute("SELECT * FROM planes_capex ORDER BY ejercicio DESC")]


def referencia_d5(con: sqlite3.Connection, ejercicio: int | None = None) -> list[dict[str, Any]]:
    """Presupuesto CAPEX D.5 del Manual Presupuestario."""
    sql = "SELECT * FROM capex_referencia"
    args: tuple[Any, ...] = ()
    if ejercicio is not None:
        sql += " WHERE ejercicio = ?"
        args = (ejercicio,)
    return [{**dict(r), "monto": de_centavos(int(r["monto_cent"]))} for r in con.execute(sql + " ORDER BY ejercicio, orden",
                                                                                          args)]


# ==========================================================================
# 5. INVERSIÓN NO PREVISTA Y VARIACIONES (POL-02 Art. 1 y 4; CI-FIN-09)
# ==========================================================================
def requisito_no_prevista(con: sqlite3.Connection, monto: Any, fecha: Any) -> seguridad.RequisitoAutorizacion:
    """Requisito de la Matriz CI-FIN-09 para una inversión fuera del plan."""
    contexto = seguridad.contexto_desde_bd(con, fecha, previsto_en_plan=False)
    return seguridad.requiere_autorizacion(TipoDecision.CAPEX_NO_PREVISTO, _monto(monto, "monto"), contexto)


def solicitar_no_prevista(con: sqlite3.Connection, sesion: Sesion, monto: Any, descripcion: str,
                          fecha: Any) -> int:
    """La GG solicita al CA la autorización de una inversión no prevista; la
    resolución la firma el CA en el módulo de Autorizaciones."""
    _exigir_gerencia(con, sesion)
    contexto = seguridad.contexto_desde_bd(con, fecha, previsto_en_plan=False)
    return seguridad.solicitar_autorizacion(con, sesion, TipoDecision.CAPEX_NO_PREVISTO, _monto(monto, "monto"),
                                            descripcion, contexto, referencia="FIN-TES-01")


def agregar_no_prevista(con: sqlite3.Connection, sesion: Sesion, plan_id: int, activo: str, categoria: str,
                        costo: Any, justificacion: str, ciclo: int, fecha_estimada: Any, autorizacion_id: int, *,
                        proveedor_referencia: str | None = None, fuente: str = FUENTE) -> int:
    """Incorpora al plan aprobado una inversión no prevista con la
    autorización CAPEX_NO_PREVISTO completa (CA; específica sobre
    ``capex_no_previsto_ca_sobre_bs``), que queda UTILIZADA.

    Raises:
        ReglaNegocioError: el plan aún está en borrador (la inversión se agrega
            como prevista) o la autorización no corresponde.
        AutorizacionError: autorización incompleta o rechazada.
    """
    _exigir_gerencia(con, sesion)
    plan = _plan(con, plan_id)
    if plan["estado"] in ("BORRADOR",):
        raise ReglaNegocioError("El plan está en borrador: agregue la inversión como prevista.",
                                codigo="PLAN_EN_BORRADOR")
    d = _validar_datos_linea(activo, categoria, costo, justificacion, ciclo, fecha_estimada, proveedor_referencia,
                             fuente)
    if isinstance(autorizacion_id, bool) or not isinstance(autorizacion_id, int):
        requisito = requisito_no_prevista(con, d["costo"], d["fecha_estimada"])
        raise AutorizacionError(
            "Inversión no contemplada en el Plan CAPEX: requiere la autorización del Consejo de Administración"
            + (" (autorización específica)" if requisito.autorizacion_especifica else "") + " — POL-02 Art. 1.",
            codigo="AUTORIZACION_REQUERIDA", detalle={"requisito": requisito.a_dict()})
    seguridad.verificar_autorizacion(con, autorizacion_id, tipo_decision=TipoDecision.CAPEX_NO_PREVISTO,
                                     monto=d["costo"])
    with transaccion(con):
        lid = _insertar_linea(con, sesion, plan_id, int(plan["ejercicio"]), activo=d["activo"],
                              categoria=d["categoria"], costo=d["costo"], justificacion=d["justificacion"],
                              ciclo=d["ciclo"], fecha_estimada=d["fecha_estimada"], proveedor=d["proveedor"],
                              previsto=False, autorizacion_id=autorizacion_id)
        seguridad.consumir_autorizacion(con, sesion, autorizacion_id, "capex_lineas", lid,
                                        tipo_decision=TipoDecision.CAPEX_NO_PREVISTO, monto=d["costo"])
    return lid


def evaluar_variacion(con: sqlite3.Connection, linea_id: int, costo: Any, fecha: Any,
                      excluir_activacion: int | None = None) -> dict[str, Any]:
    """Mide la variación del monto ejecutado (acumulado de la línea con esta
    activación) frente al aprobado y aplica la Matriz CI-FIN-09: sobre
    ``capex_variacion_pct`` se informa al CA; si además supera
    ``capex_variacion_monto_bs``, nueva aprobación de la Asamblea."""
    linea = _linea(con, linea_id)
    aprobado = de_centavos(int(linea["costo_estimado_cent"]))
    ejecutado = _ejecutado_linea(con, linea_id, excluir_activacion=excluir_activacion) + _monto(costo, "costo")
    variacion = ejecutado - aprobado
    pct = (variacion / aprobado).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    requisito = None
    if variacion != 0:
        contexto = seguridad.contexto_desde_bd(con, fecha, monto_aprobado=aprobado)
        requisito = seguridad.requiere_autorizacion(TipoDecision.VARIACION_CAPEX, ejecutado, contexto)
    return {"aprobado": aprobado, "ejecutado": ejecutado, "variacion": variacion, "variacion_pct": pct,
            "informa_ca": bool(requisito and "CA" in requisito.informar_a),
            "requiere_ag": bool(requisito and "AG" in requisito.firmas),
            "requisito": requisito.a_dict() if requisito else None}


def solicitar_variacion(con: sqlite3.Connection, sesion: Sesion, linea_id: int, costo: Any, descripcion: str,
                        fecha: Any) -> int:
    """La GG solicita a la Asamblea la nueva aprobación de una variación mayor
    (POL-02 Art. 4). El monto de la solicitud es el ejecutado acumulado."""
    _exigir_gerencia(con, sesion)
    v = evaluar_variacion(con, linea_id, costo, fecha)
    if not v["requiere_ag"]:
        raise ReglaNegocioError("La variación no requiere nueva aprobación de la Asamblea.",
                                codigo="VARIACION_SIN_ASAMBLEA")
    contexto = seguridad.contexto_desde_bd(con, fecha, monto_aprobado=v["aprobado"])
    return seguridad.solicitar_autorizacion(con, sesion, TipoDecision.VARIACION_CAPEX, v["ejecutado"], descripcion,
                                            contexto, referencia=f"Línea CAPEX {linea_id}")


# ==========================================================================
# 6. ACTIVACIÓN (FIN-TRA-08) Y REGISTRO DEL ACTIVO (FIN-TRA-09)
# ==========================================================================
def autorizar_activacion(con: sqlite3.Connection, sesion: Sesion, linea_id: int, *, descripcion: str, destino: str,
                         precio: Any, fecha_disponible: Any, proveedor: str, documento_ref: str,
                         flete: Any = 0, instalacion: Any = 0, ubicacion: str = UBICACION_GRANJA,
                         informe_ca_ref: str | None = None, autorizacion_variacion_id: int | None = None,
                         aprobacion_ag_saldo_ref: str | None = None) -> dict[str, Any]:
    """La Gerencia General autoriza la activación del bien (FIN-TRA-08) con su
    destino y costo histórico total (precio + flete + instalación).

    Controles: la línea pertenece a un plan aprobado por el CA (o es una
    inversión no prevista autorizada); destino según PC-06; vigencia de las
    cuentas de la planta de alimento; variación frente al monto aprobado
    (informe al CA / Asamblea) y saldo bancario disponible (Asamblea).

    Raises:
        ReglaNegocioError, AutorizacionError, DatosInvalidosError.
    """
    _exigir_gerencia(con, sesion)
    linea = _linea(con, linea_id)
    if linea["estado"] != "VIGENTE":
        raise ReglaNegocioError("La línea CAPEX fue retirada del plan.", codigo="LINEA_RETIRADA")
    plan = _plan(con, int(linea["plan_id"]))
    if plan["estado"] not in ("APROBADO", "RATIFICADO"):
        raise ReglaNegocioError(
            f"El Plan CAPEX {plan['ejercicio']} está {plan['estado'].lower()}: toda inversión ejecutada requiere la "
            "aprobación previa del Consejo de Administración (POL-02 Art. 1; PR-FIN-01).", codigo="PLAN_NO_APROBADO")
    cat = _categoria(linea["categoria"])
    dest = _validar_destino(cat, destino)
    f = a_fecha(fecha_disponible)
    if f < a_fecha(_ejercicio(con, int(plan["ejercicio"]))["fecha_inicio"]):
        raise DatosInvalidosError("La fecha de disponibilidad es anterior al ejercicio del plan.")
    _validar_vigencia(con, cat, f)
    if cat.centro_costo and dest == OPERATIVO:
        costos._validar_sufijo(con, cat.centro_costo, cat.sufijo, f)
    texto = _texto(descripcion, "descripción del bien", 200)
    prov = _texto(proveedor, "proveedor", 200)
    doc = _texto(documento_ref, "factura o documento de respaldo", 120)
    lugar = _texto(ubicacion, "ubicación", 200)
    p_precio = _monto(precio, "precio")
    p_flete = _monto(flete, "flete", permitir_cero=True)
    p_inst = _monto(instalacion, "costo de instalación", permitir_cero=True)
    costo = p_precio + p_flete + p_inst
    variacion = evaluar_variacion(con, linea_id, costo, f)
    informe = _opcional(informe_ca_ref, "informe al CA", 120)
    if variacion["informa_ca"] and not informe:
        raise ReglaNegocioError(
            f"La variación de {pct_texto(variacion['variacion_pct'])} supera el umbral de POL-02 Art. 4: registre la "
            "referencia del informe al Consejo de Administración.", codigo="INFORME_CA_REQUERIDO",
            detalle={"variacion": variacion})
    if variacion["requiere_ag"]:
        if autorizacion_variacion_id is None:
            raise AutorizacionError(
                f"La variación ({formato_bs(variacion['variacion'])}, {pct_texto(variacion['variacion_pct'])}) supera "
                "el porcentaje y el monto de POL-02 Art. 4: requiere nueva aprobación de la Asamblea General.",
                codigo="AUTORIZACION_REQUERIDA", detalle={"variacion": variacion})
        seguridad.verificar_autorizacion(con, autorizacion_variacion_id, tipo_decision=TipoDecision.VARIACION_CAPEX,
                                         monto=variacion["ejecutado"])
    saldo = tesoreria.saldo_bancario(con, f)
    acta_saldo = _opcional(aprobacion_ag_saldo_ref, "aprobación de la Asamblea por saldo bancario", 120)
    if costo > saldo and not acta_saldo:
        raise ReglaNegocioError(
            f"El costo ({formato_bs(costo)}) supera el saldo bancario disponible ({formato_bs(saldo)}): ninguna "
            "inversión CAPEX compromete pagos sobre el saldo sin aprobación previa de la Asamblea (PR-FIN-01).",
            codigo="SALDO_BANCARIO_INSUFICIENTE")
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO activaciones (capex_linea_id, descripcion, categoria, destino, precio_cent, flete_cent,
                   instalacion_cent, costo_cent, fecha_disponible, proveedor, documento_ref, ubicacion,
                   saldo_bancario_cent, aprobacion_ag_saldo_ref, autorizado_por, autorizado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (linea_id, texto, cat.clave, dest, a_centavos(p_precio), a_centavos(p_flete), a_centavos(p_inst),
             a_centavos(costo), f.isoformat(), prov, doc, lugar, a_centavos(saldo), acta_saldo,
             sesion.nombre_usuario, _ahora_iso()))
        act_id = int(cur.lastrowid)
        if variacion["variacion"] != 0:
            con.execute(
                """INSERT INTO capex_variaciones (activacion_id, capex_linea_id, aprobado_cent, ejecutado_cent,
                       variacion_cent, variacion_pct, informa_ca, informe_ca_ref, requiere_ag, autorizacion_id,
                       registrado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (act_id, linea_id, a_centavos(variacion["aprobado"]), a_centavos(variacion["ejecutado"]),
                 a_centavos(variacion["variacion"]), str(variacion["variacion_pct"]), int(variacion["informa_ca"]),
                 informe, int(variacion["requiere_ag"]),
                 autorizacion_variacion_id if variacion["requiere_ag"] else None, sesion.nombre_usuario,
                 _ahora_iso()))
            if variacion["requiere_ag"]:
                seguridad.consumir_autorizacion(con, sesion, int(autorizacion_variacion_id), "activaciones", act_id,
                                                tipo_decision=TipoDecision.VARIACION_CAPEX,
                                                monto=variacion["ejecutado"])
        auditoria.registrar(con, sesion, "activaciones", "AUTORIZACION", act_id,
                            despues={"linea_id": linea_id, "descripcion": texto, "destino": dest, "costo": costo,
                                     "fecha_disponible": f.isoformat(), "variacion": variacion["variacion"],
                                     "variacion_pct": variacion["variacion_pct"], "saldo_bancario": saldo})
    return {"id": act_id, "costo": costo, "variacion": variacion, "saldo_bancario": saldo}


def anular_activacion(con: sqlite3.Connection, sesion: Sesion, activacion_id: int, motivo: str) -> None:
    """La GG anula una activación autorizada que aún no se registró."""
    _exigir_gerencia(con, sesion)
    act = _activacion(con, activacion_id)
    if act["estado"] != "AUTORIZADA":
        raise ReglaNegocioError(f"La activación está {act['estado'].lower()}.", codigo="ACTIVACION_NO_PENDIENTE")
    texto = _texto(motivo, "motivo", 300)
    with transaccion(con):
        con.execute("UPDATE activaciones SET estado = 'ANULADA', motivo_anulacion = ? WHERE id = ?",
                    (texto, activacion_id))
        auditoria.registrar(con, sesion, "activaciones", "UPDATE", activacion_id, antes={"estado": "AUTORIZADA"},
                            despues={"estado": "ANULADA"}, detalle=texto)


def _activacion(con: sqlite3.Connection, activacion_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM activaciones WHERE id = ?", (activacion_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe la activación {activacion_id}.")
    return fila


def registrar_activo(con: sqlite3.Connection, sesion: Sesion, activacion_id: int) -> dict[str, Any]:
    """La Unidad de Finanzas registra el activo autorizado al costo histórico
    total en su cuenta 1.2.0x, en el mes en que está disponible para su uso,
    contra la cuenta por pagar (el pago lo hace Tesorería), y abre su ficha
    FIN-TRA-09 con código interno (PR-FIN-07 pasos 2-3)."""
    _exigir_finanzas(con, sesion)
    act = _activacion(con, activacion_id)
    if act["estado"] != "AUTORIZADA":
        raise ReglaNegocioError(f"La activación está {act['estado'].lower()}.", codigo="ACTIVACION_NO_PENDIENTE")
    cat = _categoria(act["categoria"])
    f = a_fecha(act["fecha_disponible"])
    costo = de_centavos(int(act["costo_cent"]))
    clave = clave_tasa(cat.clave)
    tasa = Decimal(str(_param(_params(con, f), clave)))
    with transaccion(con):
        asiento = contable.crear_asiento(
            con, sesion, f, f"Activación: {act['descripcion']} (FIN-TRA-08 n.º {activacion_id})",
            [{"cuenta": cat.cuenta_activo, "debe": costo}, {"cuenta": CUENTA_POR_PAGAR, "haber": costo}],
            ORIGEN, act["documento_ref"], tipo="TRASPASO")
        ficha = _insertar_activo(con, sesion, categoria=cat, descripcion=act["descripcion"], destino=act["destino"],
                                 costo=costo, tasa=tasa, clave=clave, fecha=f, origen="CAPEX", asiento_id=asiento.id,
                                 ubicacion=act["ubicacion"], activacion_id=activacion_id, proveedor=act["proveedor"],
                                 documento_ref=act["documento_ref"])
        con.execute("UPDATE activaciones SET estado = 'REGISTRADA', activo_id = ? WHERE id = ?",
                    (ficha["id"], activacion_id))
        auditoria.registrar(con, sesion, "activaciones", "UPDATE", activacion_id, antes={"estado": "AUTORIZADA"},
                            despues={"estado": "REGISTRADA", "activo_id": ficha["id"], "asiento_id": asiento.id})
    return {**ficha, "asiento_id": asiento.id}


def listar_activaciones(con: sqlite3.Connection, sesion: Sesion, estado: str | None = None) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    sql = ("SELECT a.*, l.activo AS linea, p.ejercicio, f.codigo_interno FROM activaciones a "
           "JOIN capex_lineas l ON l.id = a.capex_linea_id JOIN planes_capex p ON p.id = l.plan_id "
           "LEFT JOIN activos f ON f.id = a.activo_id")
    args: tuple[Any, ...] = ()
    if estado is not None:
        sql += " WHERE a.estado = ?"
        args = (estado,)
    return [{**dict(r), "costo": de_centavos(int(r["costo_cent"]))} for r in con.execute(sql + " ORDER BY a.id DESC", args)]


def listar_variaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return [{**dict(r), "aprobado": de_centavos(int(r["aprobado_cent"])),
             "ejecutado": de_centavos(int(r["ejecutado_cent"])), "variacion": de_centavos(int(r["variacion_cent"])),
             "pct": Decimal(r["variacion_pct"])}
            for r in con.execute("SELECT v.*, l.activo FROM capex_variaciones v JOIN capex_lineas l "
                                 "ON l.id = v.capex_linea_id ORDER BY v.id DESC")]


# ==========================================================================
# 7. BAJA DE ACTIVOS (PR-FIN-07 pasos 6-7)
# ==========================================================================
def emitir_acta_baja(con: sqlite3.Connection, sesion: Sesion, activo_id: int, fecha: Any, causa: str,
                     detalle_causa: str, destino_final: str, acta_ref: str, *, valor_realizacion: Any = 0,
                     cuenta_cobro: str | None = None) -> dict[str, Any]:
    """La GG emite el acta de baja (causa y destino final). Si el valor neto
    en libros supera ``baja_activo_cv_sobre_bs`` exige la verificación previa
    del Consejo de Vigilancia.

    Raises:
        ReglaNegocioError: activo ya dado de baja, cuotas de meses anteriores
            pendientes o cuenta de cobro no válida.
    """
    _exigir_gerencia(con, sesion)
    a = _activo(con, activo_id)
    if _baja_vigente(con, activo_id) is not None:
        raise ReglaNegocioError(f"El activo {a['codigo_interno']} ya tiene un acta de baja.", codigo="BAJA_EXISTENTE")
    f = a_fecha(fecha)
    if f < a_fecha(a["fecha_activacion"]):
        raise DatosInvalidosError("La fecha de baja es anterior a la activación.")
    if causa not in CAUSAS_BAJA:
        raise DatosInvalidosError(f"Causa de baja no válida. Use: {', '.join(CAUSAS_BAJA)}.")
    if a["cuenta_depreciacion"] and Decimal(a["tasa_anual"]) > 0:
        anio, mes = (f.year - 1, 12) if f.month == 1 else (f.year, f.month - 1)
        pendientes = _pendientes_activo(con, a, anio, mes)
        if pendientes:
            raise ReglaNegocioError(
                f"Registre la depreciación hasta {pendientes[-1]} antes de la baja: el valor neto debe estar al día.",
                codigo="DEPRECIACION_PENDIENTE", detalle={"pendientes": pendientes})
    realizacion = _monto(valor_realizacion, "valor de realización", permitir_cero=True)
    cobro = None
    if realizacion > 0:
        if cuenta_cobro not in CUENTAS_COBRO:
            raise DatosInvalidosError(f"Indique la cuenta donde ingresa el valor de realización: "
                                      f"{', '.join(CUENTAS_COBRO)}.")
        cobro = cuenta_cobro
    costo = de_centavos(int(a["costo_cent"]))
    acumulada = _acumulada(con, activo_id)
    neto = costo - acumulada
    limite = umbral_baja_cv(con, f)
    requiere_cv = neto > limite
    estado = "PENDIENTE_CV" if requiere_cv else "AUTORIZADA"
    acta = _texto(acta_ref, "número de acta de baja", 120)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO bajas_activos (activo_id, fecha, causa, detalle_causa, destino_final,
                   valor_realizacion_cent, cuenta_cobro, costo_cent, acumulada_cent, valor_neto_cent, resultado_cent,
                   acta_ref, autorizado_por, autorizado_en, requiere_cv, estado)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (activo_id, f.isoformat(), causa, _texto(detalle_causa, "detalle de la causa", 500),
             _texto(destino_final, "destino final", 300), a_centavos(realizacion), cobro, a_centavos(costo),
             a_centavos(acumulada), a_centavos(neto), a_centavos(realizacion - neto), acta, sesion.nombre_usuario,
             _ahora_iso(), int(requiere_cv), estado))
        bid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "bajas_activos", "AUTORIZACION", bid,
                            despues={"activo": a["codigo_interno"], "causa": causa, "valor_neto": neto,
                                     "valor_realizacion": realizacion, "requiere_cv": requiere_cv, "estado": estado})
    return {"id": bid, "valor_neto": neto, "acumulada": acumulada, "resultado": realizacion - neto,
            "requiere_cv": requiere_cv, "estado": estado, "limite_cv": limite}


def _pendientes_activo(con: sqlite3.Connection, a: Mapping[str, Any], anio: int, mes: int) -> list[str]:
    """Meses del activo, desde su activación hasta ``anio-mes``, sin cuota
    registrada (se detiene al completar el costo)."""
    registradas = _cuotas_registradas(con, int(a["id"]))
    acumulada = sum(registradas.values())
    inicio = a_fecha(a["fecha_activacion"])
    y, m = inicio.year, inicio.month
    faltan = []
    while (y, m) <= (anio, mes) and acumulada < int(a["costo_cent"]):
        if _periodo(y, m) not in registradas:
            faltan.append(_periodo(y, m))
        y, m = _mes_siguiente(y, m)
    return faltan


def _baja(con: sqlite3.Connection, baja_id: int) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM bajas_activos WHERE id = ?", (baja_id,)).fetchone()
    if fila is None:
        raise DatosInvalidosError(f"No existe el acta de baja {baja_id}.")
    return fila


def verificar_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int, conforme: bool, observacion: str) -> str:
    """El Consejo de Vigilancia verifica la baja (valor neto sobre el umbral):
    conforme la deja AUTORIZADA; no conforme la deja OBSERVADA (la GG debe
    emitir una nueva acta)."""
    seguridad.exigir_permiso(con, sesion, "activos", "VERIFICAR")
    if sesion.rol != "CV":
        raise AutorizacionError("La verificación de la baja corresponde al Consejo de Vigilancia.", codigo="SOLO_CV")
    b = _baja(con, baja_id)
    if b["estado"] != "PENDIENTE_CV":
        raise ReglaNegocioError(f"El acta está {b['estado'].lower()}: no espera verificación.",
                                codigo="BAJA_NO_PENDIENTE")
    if not isinstance(conforme, bool):
        raise DatosInvalidosError("Indique si la verificación es conforme.")
    texto = _texto(observacion, "observación de la verificación", 500)
    nuevo = "AUTORIZADA" if conforme else "OBSERVADA"
    with transaccion(con):
        con.execute("UPDATE bajas_activos SET verificado_por = ?, verificado_en = ?, conforme_cv = ?, "
                    "observacion_cv = ?, estado = ? WHERE id = ?",
                    (sesion.nombre_usuario, _ahora_iso(), int(conforme), texto, nuevo, baja_id))
        auditoria.registrar(con, sesion, "bajas_activos", "AUTORIZACION", baja_id, antes={"estado": "PENDIENTE_CV"},
                            despues={"estado": nuevo, "conforme": conforme}, detalle=texto)
    return nuevo


def contabilizar_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int) -> dict[str, Any]:
    """La Unidad de Finanzas elimina el valor bruto y la depreciación
    acumulada y registra la pérdida en 7.2.01.02 (o la ganancia), actualizando
    el libro de inventario (PR-FIN-07 paso 7; PC-06 §V.C)."""
    _exigir_finanzas(con, sesion)
    b = _baja(con, baja_id)
    if b["estado"] == "PENDIENTE_CV":
        raise AutorizacionError("La baja requiere la verificación previa del Consejo de Vigilancia (PR-FIN-07).",
                                codigo="VERIFICACION_CV_PENDIENTE")
    if b["estado"] != "AUTORIZADA":
        raise ReglaNegocioError(f"El acta está {b['estado'].lower()}.", codigo="BAJA_NO_AUTORIZADA")
    a = _activo(con, int(b["activo_id"]))
    acumulada_hoy = _acumulada(con, int(a["id"]))
    if a_centavos(acumulada_hoy) != int(b["acumulada_cent"]):
        raise ReglaNegocioError("La depreciación acumulada cambió desde el acta: emita una nueva acta de baja.",
                                codigo="ACTA_DESACTUALIZADA")
    costo = de_centavos(int(b["costo_cent"]))
    acumulada = de_centavos(int(b["acumulada_cent"]))
    realizacion = de_centavos(int(b["valor_realizacion_cent"]))
    resultado = de_centavos(int(b["resultado_cent"]))
    lineas: list[dict[str, Any]] = []
    if acumulada > 0:
        lineas.append({"cuenta": a["cuenta_depreciacion"], "debe": acumulada})
    if realizacion > 0:
        lineas.append({"cuenta": b["cuenta_cobro"], "debe": realizacion})
    if resultado < 0:
        lineas.append({"cuenta": CUENTA_PERDIDA_BAJA, "debe": -resultado})
    lineas.append({"cuenta": a["cuenta_activo"], "haber": costo})
    if resultado > 0:
        lineas.append({"cuenta": CUENTA_GANANCIA_BAJA, "haber": resultado})
    f = a_fecha(b["fecha"])
    with transaccion(con):
        asiento = contable.crear_asiento(
            con, sesion, f, f"Baja de {a['codigo_interno']} {a['descripcion']} — {CAUSAS_BAJA[b['causa']]}", lineas,
            ORIGEN, b["acta_ref"], tipo="INGRESO" if realizacion > 0 else "TRASPASO")
        con.execute("UPDATE bajas_activos SET estado = 'CONTABILIZADA', asiento_id = ?, contabilizado_por = ?, "
                    "contabilizado_en = ? WHERE id = ?", (asiento.id, sesion.nombre_usuario, _ahora_iso(), baja_id))
        auditoria.registrar(con, sesion, "bajas_activos", "UPDATE", baja_id, antes={"estado": "AUTORIZADA"},
                            despues={"estado": "CONTABILIZADA", "asiento_id": asiento.id, "resultado": resultado})
    return {"asiento_id": asiento.id, "resultado": resultado, "lineas": lineas}


def listar_bajas(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return [{**dict(r), "valor_neto": de_centavos(int(r["valor_neto_cent"])),
             "valor_realizacion": de_centavos(int(r["valor_realizacion_cent"])),
             "resultado": de_centavos(int(r["resultado_cent"])), "costo": de_centavos(int(r["costo_cent"])),
             "acumulada": de_centavos(int(r["acumulada_cent"]))}
            for r in con.execute("SELECT b.*, a.codigo_interno, a.descripcion FROM bajas_activos b "
                                 "JOIN activos a ON a.id = b.activo_id ORDER BY b.id DESC")]


def obtener_baja(con: sqlite3.Connection, sesion: Sesion, baja_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return next(b for b in listar_bajas(con, sesion) if b["id"] == _baja(con, baja_id)["id"])


# ==========================================================================
# 8. INFORME SEMESTRAL DE EJECUCIÓN CAPEX (FIN-TES-04)
# ==========================================================================
def _rango_semestre(con: sqlite3.Connection, ejercicio: int, semestre: int) -> tuple[date, date, date]:
    """(desde, hasta, mes de presentación) del semestre del ejercicio: el
    primero cubre los seis primeros meses y se presenta en el mes
    ``capex_informe_mes_semestre_1`` siguiente (enero); el segundo, los seis
    últimos y se presenta en ``capex_informe_mes_semestre_2`` (julio)."""
    ej = _ejercicio(con, ejercicio)
    inicio, fin = a_fecha(ej["fecha_inicio"]), a_fecha(ej["fecha_fin"])
    p = obtener_parametros_vigentes(con, ejercicio)
    mes = inicio.month + 5
    hasta_1 = fin_de_mes(inicio.year + (mes - 1) // 12, (mes - 1) % 12 + 1)
    if semestre == 1:
        desde, hasta, clave = inicio, hasta_1, "capex_informe_mes_semestre_1"
    else:
        desde, hasta, clave = date.fromordinal(hasta_1.toordinal() + 1), fin, "capex_informe_mes_semestre_2"
    mes_presentacion = int(_param(p, clave))
    anio = hasta.year + (1 if mes_presentacion <= hasta.month else 0)
    return desde, hasta, date(anio, mes_presentacion, 1)


def informe_semestral(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, semestre: int) -> dict[str, Any]:
    """FIN-TES-04: por activo del plan, monto aprobado, ejecutado al cierre del
    semestre, variación %, si supera el umbral de POL-02 Art. 4 y observación."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    _entero(semestre, "semestre", 1, 2)
    plan = plan_del_ejercicio(con, ejercicio)
    if plan is None:
        raise ReglaNegocioError(f"No existe el Plan CAPEX del ejercicio {ejercicio}.", codigo="SIN_PLAN")
    desde, hasta, presentacion = _rango_semestre(con, ejercicio, semestre)
    limite = Decimal(str(_param(obtener_parametros_vigentes(con, ejercicio), "capex_variacion_pct")))
    filas = []
    for l in con.execute("SELECT * FROM capex_lineas WHERE plan_id = ? AND estado = 'VIGENTE' ORDER BY previsto DESC, id",
                         (int(plan["id"]),)):
        aprobado = de_centavos(int(l["costo_estimado_cent"]))
        ejecutado = _ejecutado_linea(con, int(l["id"]), hasta, solo_registradas=True)
        en_semestre = ejecutado - _ejecutado_linea(con, int(l["id"]), date.fromordinal(desde.toordinal() - 1),
                                                   solo_registradas=True)
        variacion = (ejecutado - aprobado) / aprobado if ejecutado > 0 else None
        supera = variacion is not None and abs(variacion) > limite
        observaciones = []
        if not l["previsto"]:
            observaciones.append("Inversión no prevista autorizada por el CA")
        if ejecutado == 0:
            observaciones.append("Pendiente de ejecución")
        vars_ = con.execute("SELECT * FROM capex_variaciones WHERE capex_linea_id = ? ORDER BY id DESC LIMIT 1",
                            (l["id"],)).fetchone()
        if supera and vars_ is not None:
            if vars_["requiere_ag"]:
                observaciones.append("Nueva aprobación de la Asamblea (POL-02 Art. 4)")
            elif vars_["informa_ca"]:
                observaciones.append(f"Informada al CA: {vars_['informe_ca_ref']}")
        elif ejecutado > 0:
            observaciones.append("Ejecutado dentro del margen aprobado")
        filas.append({"linea_id": int(l["id"]), "activo": l["activo"], "previsto": bool(l["previsto"]),
                      "aprobado": aprobado, "ejecutado": ejecutado, "en_semestre": en_semestre,
                      "variacion_pct": variacion.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                      if variacion is not None else None, "supera": supera, "observacion": "; ".join(observaciones)})
    aprobado_total = sum((f["aprobado"] for f in filas), CERO)
    ejecutado_total = sum((f["ejecutado"] for f in filas), CERO)
    return {"ejercicio": ejercicio, "semestre": semestre, "desde": desde, "hasta": hasta,
            "mes_presentacion": presentacion, "plan_estado": plan["estado"], "filas": filas,
            "aprobado": aprobado_total, "ejecutado": ejecutado_total,
            "ejecucion_pct": (ejecutado_total / aprobado_total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if aprobado_total > 0 else None, "limite_variacion": limite}


def registrar_informe_semestral(con: sqlite3.Connection, sesion: Sesion, ejercicio: int, semestre: int, fecha: Any,
                                observacion: str | None = None) -> dict[str, Any]:
    """La GG presenta el FIN-TES-04 (enero y julio). Se guarda una copia fija
    del contenido; fuera del mes de presentación queda marcado."""
    _exigir_gerencia(con, sesion)
    inf = informe_semestral(con, sesion, ejercicio, semestre)
    f = a_fecha(fecha)
    if f <= inf["hasta"]:
        raise ReglaNegocioError(f"El semestre cierra el {fecha_pantalla(inf['hasta'])}; el informe se presenta "
                                "después.", codigo="SEMESTRE_ABIERTO")
    if con.execute("SELECT 1 FROM informes_capex WHERE ejercicio = ? AND semestre = ?", (ejercicio, semestre)).fetchone():
        raise ReglaNegocioError("El informe de ese semestre ya fue presentado.", codigo="INFORME_EXISTENTE")
    p = inf["mes_presentacion"]
    fuera = (f.year, f.month) != (p.year, p.month)
    contenido = json.dumps({"filas": inf["filas"], "plan_estado": inf["plan_estado"]}, default=str, ensure_ascii=False)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO informes_capex (ejercicio, semestre, desde, hasta, fecha, fuera_de_plazo, aprobado_cent,
                   ejecutado_cent, contenido_json, observacion, presentado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ejercicio, semestre, inf["desde"].isoformat(), inf["hasta"].isoformat(), f.isoformat(), int(fuera),
             a_centavos(inf["aprobado"]), a_centavos(inf["ejecutado"]), contenido,
             _opcional(observacion, "observación", 1000), sesion.nombre_usuario, _ahora_iso()))
        iid = int(cur.lastrowid)
        auditoria.registrar(con, sesion, "informes_capex", "INSERT", iid,
                            despues={"ejercicio": ejercicio, "semestre": semestre, "aprobado": inf["aprobado"],
                                     "ejecutado": inf["ejecutado"], "fuera_de_plazo": fuera})
    return {"id": iid, "fuera_de_plazo": fuera, **inf}


def listar_informes(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return [{**dict(r), "aprobado": de_centavos(int(r["aprobado_cent"])),
             "ejecutado": de_centavos(int(r["ejecutado_cent"])), "contenido": json.loads(r["contenido_json"])}
            for r in con.execute("SELECT * FROM informes_capex ORDER BY ejercicio DESC, semestre DESC")]


# ==========================================================================
# 9. VERIFICACIÓN FÍSICA DE ACTIVOS (INF-CV-01)
# ==========================================================================
@dataclass(frozen=True)
class ItemVerificacion:
    """Resultado de la inspección de un activo por el Consejo de Vigilancia."""

    activo_id: int
    existe: bool
    estado_conservacion: str | None = None
    ubicacion_verificada: str | None = None
    observacion: str | None = None


def activos_a_verificar(con: sqlite3.Connection, fecha: Any) -> list[sqlite3.Row]:
    """Activos vigentes a la fecha (activados y sin baja contabilizada)."""
    f = a_fecha(fecha).isoformat()
    return con.execute(
        """SELECT a.* FROM activos a WHERE a.fecha_activacion <= ? AND NOT EXISTS (
               SELECT 1 FROM bajas_activos b WHERE b.activo_id = a.id AND b.estado = 'CONTABILIZADA' AND b.fecha <= ?)
            ORDER BY a.codigo_interno""", (f, f)).fetchall()


def registrar_verificacion(con: sqlite3.Connection, sesion: Sesion, fecha: Any, items: Sequence[ItemVerificacion],
                           observacion: str | None = None) -> dict[str, Any]:
    """Acta INF-CV-01: el CV verifica la existencia física y el estado de cada
    activo vigente y que la tasa de la ficha coincida con la aprobada en PC-06
    (MOF; PR-FIN-07 paso 5). Debe cubrir todos los activos vigentes."""
    seguridad.exigir_permiso(con, sesion, "activos", "VERIFICAR")
    if sesion.rol != "CV":
        raise AutorizacionError("La verificación física corresponde al Consejo de Vigilancia.", codigo="SOLO_CV")
    f = a_fecha(fecha)
    vigentes = {int(a["id"]): a for a in activos_a_verificar(con, f)}
    if not vigentes:
        raise ReglaNegocioError("No hay activos vigentes que verificar.", codigo="SIN_ACTIVOS")
    recibidos: dict[int, ItemVerificacion] = {}
    for it in items:
        if not isinstance(it, ItemVerificacion):
            raise DatosInvalidosError("Cada ítem debe ser un ItemVerificacion.")
        if it.activo_id not in vigentes:
            raise DatosInvalidosError(f"El activo {it.activo_id} no está vigente a la fecha de verificación.")
        if it.activo_id in recibidos:
            raise DatosInvalidosError(f"El activo {vigentes[it.activo_id]['codigo_interno']} figura dos veces.")
        if it.existe and it.estado_conservacion not in ESTADOS_CONSERVACION:
            raise DatosInvalidosError(f"Indique el estado de conservación de {vigentes[it.activo_id]['codigo_interno']} "
                                      "(BUENO, REGULAR o MALO).")
        recibidos[it.activo_id] = it
    faltan = [vigentes[i]["codigo_interno"] for i in vigentes if i not in recibidos]
    if faltan:
        raise ReglaNegocioError(f"El acta debe cubrir todos los activos vigentes; faltan: {', '.join(faltan)}.",
                                codigo="VERIFICACION_INCOMPLETA", detalle={"faltan": faltan})
    filas = []
    for aid, it in recibidos.items():
        a = vigentes[aid]
        politica = tasa_vigente(con, a["categoria"], f)
        ficha = Decimal(a["tasa_anual"])
        filas.append((aid, it, de_centavos(int(a["costo_cent"])) - _acumulada(con, aid, f), ficha, politica))
    faltantes = sum(1 for _, it, _, _, _ in filas if not it.existe)
    distintas = sum(1 for *_, ficha, politica in filas if ficha != politica)
    with transaccion(con):
        cur = con.execute(
            """INSERT INTO verificaciones_fisicas (ejercicio, fecha, activos_verificados, faltantes, tasas_distintas,
                   observacion, verificado_por, registrado_en) VALUES (?,?,?,?,?,?,?,?)""",
            (ejercicio_de_fecha_bd(con, f), f.isoformat(), len(filas), faltantes, distintas,
             _opcional(observacion, "observación", 1000), sesion.nombre_usuario, _ahora_iso()))
        vid = int(cur.lastrowid)
        for aid, it, neto, ficha, politica in filas:
            con.execute(
                """INSERT INTO verificacion_detalle (verificacion_id, activo_id, existe, estado_conservacion,
                       ubicacion_verificada, valor_neto_cent, tasa_ficha, tasa_politica, tasa_coincide, observacion)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (vid, aid, int(bool(it.existe)), it.estado_conservacion if it.existe else None,
                 _opcional(it.ubicacion_verificada, "ubicación verificada", 200), a_centavos(neto), str(ficha),
                 str(politica), int(ficha == politica), _opcional(it.observacion, "observación", 500)))
        auditoria.registrar(con, sesion, "verificaciones_fisicas", "INSERT", vid,
                            despues={"fecha": f.isoformat(), "activos": len(filas), "faltantes": faltantes,
                                     "tasas_distintas": distintas})
    return {"id": vid, "activos": len(filas), "faltantes": faltantes, "tasas_distintas": distintas}


def obtener_verificacion(con: sqlite3.Connection, sesion: Sesion, verificacion_id: int) -> dict[str, Any]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    v = con.execute("SELECT * FROM verificaciones_fisicas WHERE id = ?", (verificacion_id,)).fetchone()
    if v is None:
        raise DatosInvalidosError(f"No existe el acta de verificación {verificacion_id}.")
    detalle = [{**dict(d), "valor_neto": de_centavos(int(d["valor_neto_cent"]))} for d in con.execute(
        """SELECT d.*, a.codigo_interno, a.descripcion, a.ubicacion, a.categoria FROM verificacion_detalle d
             JOIN activos a ON a.id = d.activo_id WHERE d.verificacion_id = ? ORDER BY a.codigo_interno""",
        (verificacion_id,))]
    return {**dict(v), "detalle": detalle}


def listar_verificaciones(con: sqlite3.Connection, sesion: Sesion) -> list[dict[str, Any]]:
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    return [dict(v) for v in con.execute("SELECT * FROM verificaciones_fisicas ORDER BY fecha DESC, id DESC")]


def estado_verificacion(con: sqlite3.Connection, fecha: Any) -> dict[str, Any]:
    """Última verificación física y si venció el plazo de ``verificacion_fisica_meses``."""
    f = a_fecha(fecha)
    meses = int(_param(_params(con, f), "verificacion_fisica_meses"))
    ultima = con.execute("SELECT * FROM verificaciones_fisicas WHERE fecha <= ? ORDER BY fecha DESC LIMIT 1",
                         (f.isoformat(),)).fetchone()
    referencia = a_fecha(ultima["fecha"]) if ultima else None
    if referencia is None:
        primera = con.execute("SELECT MIN(fecha_activacion) FROM activos").fetchone()[0]
        referencia = a_fecha(primera) if primera else None
    vencida = False
    if referencia is not None:
        transcurridos = (f.year - referencia.year) * 12 + (f.month - referencia.month) - (1 if f.day < referencia.day else 0)
        vencida = transcurridos >= meses
    return {"ultima": dict(ultima) if ultima else None, "plazo_meses": meses, "vencida": vencida}


# ==========================================================================
# 10. RESUMEN DEL MÓDULO
# ==========================================================================
def resumen_activos(con: sqlite3.Connection, sesion: Sesion, fecha: Any) -> dict[str, Any]:
    """Indicadores del módulo y alertas de control."""
    seguridad.exigir_permiso(con, sesion, "activos", "VER")
    f = a_fecha(fecha)
    libro = [a for a in libro_activos(con, sesion, f) if a["estado"] != "BAJA"]
    ejercicio = ejercicio_de_fecha_bd(con, f)
    plan = plan_del_ejercicio(con, ejercicio)
    alertas = []
    pendientes = meses_pendientes(con, f)
    if pendientes:
        alertas.append(f"Depreciación pendiente: {', '.join(pendientes[:6])}" + ("…" if len(pendientes) > 6 else ""))
    verif = estado_verificacion(con, f)
    if libro and verif["vencida"]:
        alertas.append(f"Verificación física vencida: el CV debe verificar los activos al menos cada "
                       f"{verif['plazo_meses']} meses (INF-CV-01).")
    siguiente = plan_del_ejercicio(con, ejercicio + 1)
    try:
        limite = fecha_limite_plan(con, ejercicio + 1)
        if siguiente is None and f > limite:
            alertas.append(f"Plan CAPEX {ejercicio + 1} no elaborado (plazo {fecha_pantalla(limite)}, POL-02 Art. 1).")
    except (ReglaNegocioError, DatosInvalidosError):
        pass
    if not hay_fichas_apertura(con):
        alertas.append("Falta abrir las fichas del balance de apertura.")
    pend_cv = int(con.execute("SELECT COUNT(*) FROM bajas_activos WHERE estado = 'PENDIENTE_CV'").fetchone()[0])
    if pend_cv:
        alertas.append(f"{pend_cv} acta(s) de baja esperan la verificación del CV.")
    detalle = detalle_plan(con, sesion, int(plan["id"])) if plan else None
    return {"activos": len(libro), "costo": sum((a["costo"] for a in libro), CERO),
            "acumulada": sum((a["acumulada"] for a in libro), CERO),
            "valor_neto": sum((a["valor_neto"] for a in libro), CERO),
            "plan": detalle, "verificacion": verif, "pendientes": pendientes, "alertas": alertas}


def categorias_para(destino: str | None = None) -> list[Categoria]:
    """Categorías de PC-06 (filtradas por destino)."""
    return [c for c in CATEGORIAS.values() if destino is None or c.destino in (None, destino)]


def items_desde(filas: Iterable[Mapping[str, Any]]) -> list[ItemVerificacion]:
    """Convierte filas de pantalla en ítems de verificación."""
    return [ItemVerificacion(int(f["activo_id"]), bool(f["existe"]), f.get("estado_conservacion") or None,
                             f.get("ubicacion_verificada") or None, f.get("observacion") or None) for f in filas]
