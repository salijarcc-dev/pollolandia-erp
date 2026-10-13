"""Semillas de las Fases 1 a 5: catálogo contable, costos, parámetros,
calendario, socios, roles/permisos, balance de apertura, conciliación de
discrepancias, parámetros de seguridad y suplencia (Fase 2), de tesorería
(Fase 3) y de compras e inventarios (Fase 4: catálogo de insumos y partidas
presupuestarias de compras) y de planillas (Fase 5: parámetros de planillas y
partidas del personal eventual).

Fuentes: Plan de Cuentas v3 (maestro), Descripción de Cuentas, PC-01..PC-10,
POL-01..POL-07, MAN-05, MAN-006, Manual de Costos, Manual Presupuestario, POA,
PET-RAU, MOF, CI-FIN-xx y Acta de Constitución.

Las semillas son idempotentes: cada bloque se registra en ``semillas_aplicadas``
y no se repite. Todo el conjunto se carga en UNA transacción (rollback si falla).
"""
from __future__ import annotations

import calendar
import json
import logging
import sqlite3
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import config
from db.conexion import a_centavos, convertir_valor_parametro
from servicios.excepciones import DatosInvalidosError

log = logging.getLogger(f"{config.LOGGER_RAIZ}.semillas")

USUARIO_SISTEMA = "SISTEMA"
EJERCICIO_INICIAL = 2026
EJERCICIOS_PLAN = (2026, 2027, 2028, 2029, 2030)
FECHA_APERTURA = "2026-07-01"

# ==========================================================================
# (a) PLAN DE CUENTAS v3
# ==========================================================================
GRUPOS: dict[int, str] = {
    1: "ACTIVO", 2: "PASIVO", 3: "PATRIMONIO",
    4: "REGULADORAS DE ACTIVO, PASIVO Y PATRIMONIO", 5: "INGRESOS", 6: "COSTOS",
    7: "GASTOS", 8: "OTRAS CUENTAS DE RESULTADO", 9: "CUENTAS DE ORDEN",
}

CLASIFICACIONES: dict[str, str] = {
    "1.1": "ACTIVO CORRIENTE", "1.2": "ACTIVO NO CORRIENTE",
    "2.1": "PASIVO CORRIENTE", "2.2": "PASIVO NO CORRIENTE",
    "3.1": "PATRIMONIO COOPERATIVO",
    "4.1": "REGULADORAS DE ACTIVO", "4.2": "REGULADORAS DE PASIVO",
    "4.3": "REGULADORAS DE PATRIMONIO",
    "5.1": "INGRESOS OPERATIVOS", "5.2": "INGRESOS NO OPERATIVOS",
    "6.1": "COSTOS OPERATIVOS DE PRODUCCIÓN",
    "7.1": "GASTOS OPERATIVOS", "7.2": "GASTOS NO OPERATIVOS",
    "8.1": "RESUMEN DE PÉRDIDAS Y GANANCIAS", "8.2": "AJUSTE Y REEXPRESIONES",
    "9.1": "CUENTAS DE ORDEN DEUDORAS", "9.2": "CUENTAS DE ORDEN ACREEDORAS",
}

# subgrupo -> (nombre, [(nn, nombre de la cuenta)])
SUBGRUPOS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "1.1.01": ("DISPONIBLE", [
        ("01", "Caja – Efectivo en Moneda Nacional"),
        ("02", "Caja – Efectivo en Moneda Extranjera"),
        ("03", "Banco – Cuenta Corriente Moneda Nacional"),
        ("04", "Banco – Cuenta de Ahorros"),
        ("05", "Fondo Rotativo – Caja Chica"),
    ]),
    "1.1.02": ("EXIGIBLE - CUENTAS POR COBRAR", [
        ("01", "Cuentas por Cobrar"),
    ]),
    # DISCREPANCIA: el Plan v3 del Manual de Gestión Contable codifica
    # 1.1.03.14 = "Materiales de mantenimiento" y no tiene cuenta de Gallinaza;
    # Plan_de_Cuentas_Pollolandia.xlsx, 23. Plan de Cuentas Contable.xlsx y la
    # nota de inventarios de MAN-006 usan 1.1.03.14 = Gallinaza y 1.1.03.15 =
    # Materiales de mantenimiento. Se mantiene esta última codificación
    # (conciliación n.º 24, confirmada por el usuario el 11/10/2026).
    "1.1.03": ("INVENTARIOS Y EXISTENCIAS", [
        ("01", "Inventario – Pollitos BB"),
        ("02", "Inventario – Alimento Balanceado Comprado a Terceros"),
        ("03", "Inventario – Maíz Amarillo (*)"),
        ("04", "Inventario – Torta de Soya (*)"),
        ("05", "Inventario – Afrecho de Trigo (*)"),
        ("06", "Inventario – Núcleo Vitamínico (*)"),
        ("07", "Inventario – Aceite Vegetal (*)"),
        ("08", "Inventario – Productos Veterinarios"),
        ("09", "Inventario – Insumos de Limpieza y Desinfección"),
        ("10", "Inventario – Insumos de Crianza"),
        ("11", "Inventario – Insumos de Embalaje y Comercialización"),
        ("12", "Inventario – Productos Terminados: Carne de Pollo"),
        ("13", "Inventario – Productos Terminados: Alimento Balanceado (*)"),
        ("14", "Inventario – Subproductos: Gallinaza (Abono Orgánico)"),
        ("15", "Inventario – Materiales de mantenimiento"),
    ]),
    "1.1.04": ("PAGOS ANTICIPADOS Y OTROS ACTIVOS CORRIENTES", [
        ("01", "Anticipos a Proveedores de Insumos"),
        ("02", "Gastos Pagados por Adelantado"),
        ("03", "Otros Activos Corrientes"),
        ("04", "Anticipos a personal eventual"),
    ]),
    "1.2.01": ("PROPIEDAD, PLANTA Y EQUIPO", [
        ("01", "Terrenos"),
        ("02", "Galpones de Producción"),
        ("03", "Alambrados y Cercados"),
        ("04", "Ambiente de Preparación de Alimento Balanceado (*)"),
        ("05", "Maquinaria de Producción de Alimento Balanceado (*)"),
        ("06", "Equipos de Producción Avícola"),
        ("07", "Herramientas de Producción"),
        ("08", "Muebles y Enseres"),
        ("09", "Equipos de Computación"),
    ]),
    "1.2.02": ("ACTIVOS INTANGIBLES", [
        ("01", "Gastos de Constitución y Organización"),
        ("02", "Software y Plataformas Digitales"),
    ]),
    "1.2.03": ("OTROS ACTIVOS NO CORRIENTES", [
        ("01", "Depósitos en Garantía"),
        ("02", "Inversiones a Largo Plazo"),
    ]),
    "2.1.01": ("CUENTAS POR PAGAR COMERCIALES", [
        ("01", "Proveedores – Pollitos BB"),
        ("02", "Proveedores – Alimento Balanceado (Comprado 2026)"),
        ("03", "Proveedores – Materias Primas Alimento (*)"),
        ("04", "Proveedores – Productos Veterinarios"),
        ("05", "Proveedores – Insumos de Limpieza y Crianza"),
        ("06", "Proveedores – Servicios y Otros"),
    ]),
    "2.1.02": ("OBLIGACIONES LABORALES – CORTO PLAZO", [
        ("01", "Sueldos y Salarios por Pagar"),
        ("02", "Aguinaldo por Pagar"),
        ("03", "Aportes Patronales por Pagar"),
        ("04", "Personal Eventual por Pagar"),
        ("05", "Aportes Laborales por Pagar"),
    ]),
    "2.1.03": ("OBLIGACIONES TRIBUTARIAS CORRIENTES", [
        ("01", "Régimen Agropecuario Unificado por Pagar"),
        ("02", "Tributos Municipales por Pagar"),
    ]),
    "2.1.04": ("OTROS PASIVOS CORRIENTES", [
        ("01", "Anticipos de Clientes HORECA Recibidos"),
    ]),
    "2.1.05": ("EXCEDENTES POR DISTRIBUIR", [
        ("01", "Excedentes por pagar Socios – Ejercicio Anterior"),
    ]),
    "2.2.01": ("OBLIGACIONES LABORALES – LARGO PLAZO", [
        ("01", "Provisión para Indemnización"),
    ]),
    "3.1.01": ("CAPITAL COOPERATIVO", [
        ("01", "Certificados de Aportación – Socios Fundadores"),
        ("02", "Certificados de Aportación – Nuevos Socios"),
        ("03", "Capital en Trámite de Registro"),
        ("04", "Certificados de Aportación Suscritos"),
        ("05", "Aportes Adicionales de Socios"),
    ]),
    "3.1.02": ("RESERVAS COOPERATIVAS", [
        ("01", "Fondo de Reserva Legal"),
        ("02", "Fondo de Educación Cooperativa"),
        ("03", "Reserva de Reinversión y Expansión"),
        ("04", "Reserva Especial para Contingencias"),
        ("05", "Reserva por Revalúo Técnico"),
    ]),
    "3.1.03": ("RESULTADOS DE LA GESTIÓN", [
        ("01", "Excedentes de Percepción Acumulados"),
        ("02", "Excedente del Ejercicio"),
        ("03", "Déficit del Ejercicio"),
        ("04", "Excedente en Distribución – Decisión Asamblea"),
    ]),
    "3.1.04": ("APORTES ESPECIALES Y DONACIONES", [
        ("01", "Primas de Admisión"),
        ("02", "Donaciones y Legados"),
    ]),
    "4.1.01": ("ACTIVO CORRIENTE", [
        ("01", "Deterioro Acumulado de Cuentas por cobrar"),
        ("02", "Deterioro Acumulado de Materias Primas"),
        ("03", "Deterioro Acumulado de Alimento Balanceado"),
        ("04", "Deterioro Acumulado de Carne de Pollo"),
    ]),
    "4.1.02": ("ACTIVO NO CORRIENTE", [
        ("01", "Deprec. Acumulada – Galpones de Producción"),
        ("02", "Deprec. Acumulada – Alambrados y Cercados"),
        ("03", "Deprec. Acumulada – Ambiente Preparación Alimento (*)"),
        ("04", "Deprec. Acumulada – Maquinaria Alimento Balanceado (*)"),
        ("05", "Deprec. Acumulada – Equipos de Producción Avícola"),
        ("06", "Deprec. Acumulada – Herramientas de Producción"),
        ("07", "Deprec. Acumulada – Muebles y Enseres"),
        ("08", "Deprec. Acumulada – Equipos de Computación"),
        ("09", "Amort. Acumulada – Gastos de Constitución"),
        ("10", "Amort. Acumulada – Software y Plataformas Digitales"),
    ]),
    "4.3.01": ("REGULADORAS DE CAPITAL COOPERATIVO", [
        ("01", "Certificados de Aportación No Pagados"),
    ]),
    "5.1.01": ("VENTAS – CARNE DE POLLO", [
        ("01", "Ventas – Carne de pollo"),
    ]),
    "5.1.02": ("VENTAS – ALIMENTO BALANCEADO (*)", [
        ("01", "Ventas – Alimento Balanceado Excedente"),
    ]),
    # DISCREPANCIA: el Plan v3 titula este subgrupo "5.1.03" pero codifica su
    # cuenta como 5.1.04.01; se conserva el código de la cuenta (usado por PC-08 y MAN-05).
    "5.1.04": ("REGULADORAS DE INGRESOS OPERATIVOS", [
        ("01", "Devoluciones en Ventas"),
    ]),
    "5.2.01": ("OTROS INGRESOS", [
        ("01", "Ingresos Financieros – Intereses Ganados"),
        ("02", "Donaciones y Subsidios Recibidos"),
        ("03", "Recuperación de Gastos de Ejercicios Anteriores"),
        ("04", "Ventas – Gallinaza / Abono Orgánico"),
        ("05", "Otros Ingresos No Operativos"),
    ]),
    "6.1.01": ("CENTROS DE COSTO – PRODUCCIÓN EN PROCESO", [
        ("01", "Centro de costo – Producción de Carne de Pollo"),
        ("02", "Centro de Costo – Producción de Alimento Balanceado (*)"),
    ]),
    "6.1.02": ("COSTO DE PRODUCCION VENDIDA", [
        ("01", "Costo de Ventas – Carne de Pollo"),
        ("02", "Costo de Ventas – Alimento Balanceado Excedente"),
    ]),
    "7.1.01": ("GASTOS DE PERSONAL ADMINISTRATIVO", [
        ("01", "Sueldos – Personal Administrativo"),
        ("02", "Aguinaldo – Personal Administrativo"),
        ("03", "Aportes Patronales – Personal Administrativo"),
        ("04", "Indemnización – Personal Administrativo"),
    ]),
    "7.1.02": ("GASTOS GENERALES ADMINISTRATIVOS", [
        ("01", "Materiales y Útiles de Oficina"),
        ("02", "Servicios de Comunicación"),
        ("03", "Gastos de Mantenimiento – Administrativo"),
        ("04", "Depreciación Muebles y Enseres"),
        ("05", "Depreciación Equipos de Computación"),
        ("06", "Amortización Gastos de Constitución"),
        ("07", "Amortización Software y Plataformas Digitales"),
        ("08", "Pérdida en Cuentas Incobrables"),
    ]),
    "7.1.03": ("GASTOS DE COMERCIALIZACIÓN", [
        ("01", "Gastos de Transporte y Flete Rural"),
    ]),
    "7.1.04": ("GASTOS DE CERTIFICACIÓN Y CALIDAD", [
        ("01", "Certificación SENASAG"),
        ("02", "Auditoría y Certificación IBNORCA"),
    ]),
    "7.1.05": ("GASTOS LEGALES Y DE REGISTRO", [
        ("01", "Licencias Municipales y Habilitación"),
        ("02", "Impuesto por el Régimen Agropecuario Unificado"),
    ]),
    "7.2.01": ("GASTOS FINANCIEROS Y EXTRAORDINARIOS", [
        ("01", "Multas y Sanciones"),
        ("02", "Pérdida por Retiro o Venta de Activos Fijos"),
        ("03", "Gastos Extraordinarios No Previstos"),
        ("04", "Pérdidas por Mortandad Avícola anormal"),
        ("05", "Pérdidas por Mermas de Producción anormal"),
    ]),
    "8.1.01": ("RESUMEN DE RESULTADOS", [
        ("01", "Resumen de Déficit y Excedentes del Ejercicio"),
    ]),
    "8.2.01": ("RESULTADO POR EXPOSICIÓN A VARIABLES MONETARIAS", [
        ("01", "REI – Resultado por Exposición a la Inflación"),
        ("02", "AITB – Ajuste por Inflación y Tenencia de Bienes"),
    ]),
    "8.2.02": ("DIFERENCIAS DE CAMBIO", [
        ("01", "Diferencias de Cambio"),
    ]),
    "9.1.01": ("COMPROMISOS Y CONTINGENCIAS ACTIVAS", [
        ("01", "Bienes Recibidos en Consignación"),
        ("02", "Garantías de Socios Recibidas"),
    ]),
    "9.2.01": ("CONTINGENCIAS PASIVAS Y COMPROMISOS FUTUROS", [
        ("01", "Responsabilidad por Bienes en Consignación"),
        ("02", "Acreedores por Garantías de Socios"),
    ]),
}

# Cuentas marcadas con (*) en el Plan v3: uso exclusivo desde el ejercicio 2027.
USO_DESDE_2027: frozenset[str] = frozenset({
    "1.1.03.03", "1.1.03.04", "1.1.03.05", "1.1.03.06", "1.1.03.07", "1.1.03.13",
    "1.2.01.04", "1.2.01.05", "2.1.01.03", "4.1.02.03", "4.1.02.04",
    "5.1.02", "5.1.02.01", "6.1.01.02",
})

# Cuenta que el Plan v3 omite pero exigen otros documentos (Descripción, MAN-05,
# POL-04, CI-FIN-01): se incorpora con origen DESCRIPCION.
COMPLEMENTO_DESCRIPCION: frozenset[str] = frozenset({"1.1.01.05"})

NATURALEZA_EXCEPCIONES: dict[str, str] = {
    "3.1.03.03": "DEUDORA",   # Déficit del Ejercicio
    "4.3.01.01": "DEUDORA",   # Certificados de Aportación No Pagados
    "5.1.04.01": "DEUDORA",   # Devoluciones en Ventas (reguladora de ingresos)
}


def naturaleza_de(codigo: str) -> str:
    """Naturaleza de una cuenta de movimiento según la Descripción de Cuentas."""
    if codigo in NATURALEZA_EXCEPCIONES:
        return NATURALEZA_EXCEPCIONES[codigo]
    grupo = codigo[0]
    if grupo == "1":
        return "DEUDORA"
    if grupo in ("2", "3"):
        return "ACREEDORA"
    if grupo == "4":
        return "ACREEDORA" if codigo.startswith("4.1") else "DEUDORA"
    if grupo == "5":
        return "ACREEDORA"
    if grupo in ("6", "7"):
        return "DEUDORA"
    if grupo == "8":
        return "DUAL"
    return "DEUDORA" if codigo.startswith("9.1") else "ACREEDORA"



def clasificacion_de(codigo: str) -> str:
    """CORRIENTE / NO_CORRIENTE / OPERATIVO / NO_OPERATIVO / NA según el código."""
    nivel = {1: 1, 3: 2, 6: 3, 9: 4}[len(codigo)]
    grupo = codigo[0]
    if nivel == 1:
        return "NA"
    if grupo in ("1", "2"):
        return "CORRIENTE" if codigo[2] == "1" else "NO_CORRIENTE"
    if grupo in ("5", "6", "7"):
        return "OPERATIVO" if codigo[2] == "1" else "NO_OPERATIVO"
    if grupo == "4" and codigo.startswith("4.1") and nivel >= 3:
        return "CORRIENTE" if codigo[4:6] == "01" else "NO_CORRIENTE"
    return "NA"


def construir_filas_cuentas() -> list[dict[str, Any]]:
    """Genera las filas de ``cuentas`` (niveles 1 a 4) en orden padre -> hijo."""
    filas: list[dict[str, Any]] = []
    for g, nombre in GRUPOS.items():
        filas.append(dict(codigo=str(g), nombre=nombre, nivel=1, grupo=g, padre=None,
                          naturaleza=None, mov=0, origen="PLAN_V3", obs=None))
    for cod, nombre in CLASIFICACIONES.items():
        filas.append(dict(codigo=cod, nombre=nombre, nivel=2, grupo=int(cod[0]), padre=cod[0],
                          naturaleza=None, mov=0, origen="PLAN_V3", obs=None))
    for sub, (nombre, cuentas) in SUBGRUPOS.items():
        filas.append(dict(codigo=sub, nombre=nombre, nivel=3, grupo=int(sub[0]), padre=sub[:3],
                          naturaleza=None, mov=0, origen="PLAN_V3", obs=None))
        for nn, nom in cuentas:
            cod = f"{sub}.{nn}"
            comp = cod in COMPLEMENTO_DESCRIPCION
            filas.append(dict(
                codigo=cod, nombre=nom, nivel=4, grupo=int(sub[0]), padre=sub,
                naturaleza=naturaleza_de(cod), mov=1,
                origen="DESCRIPCION" if comp else "PLAN_V3",
                obs="Ausente en el Plan v3; incorporada de la Descripción de Cuentas/MAN-05/POL-04."
                if comp else None))
    filas.sort(key=lambda f: (f["nivel"], f["codigo"]))
    return filas


# ==========================================================================
# (b) SUFIJOS Y CENTROS DE COSTO
# ==========================================================================
C_CARNE = "6.1.01.01"
C_ALIM = "6.1.01.02"
AMBOS = (C_CARNE, C_ALIM)

# (sufijo, nombre, elemento, centros, contrapartida, contrapartida_2, uso_2027, observación)
SUFIJOS: list[tuple[str, str, str, tuple[str, ...], str | None, str | None, int, str | None]] = [
    ("01", "Costo – Pollitos BB", "MPD", (C_CARNE,), "1.1.03.01", None, 0, None),
    ("02", "Costo – Alimento Balanceado Comprado a Terceros", "MPD", (C_CARNE,), "1.1.03.02", None, 0, None),
    ("03", "Costo – Maíz Amarillo (*)", "MPD", (C_ALIM,), "1.1.03.03", None, 1, None),
    ("04", "Costo – Torta de Soya (*)", "MPD", (C_ALIM,), "1.1.03.04", None, 1, None),
    ("05", "Costo – Afrecho de Trigo (*)", "MPD", (C_ALIM,), "1.1.03.05", None, 1, None),
    ("06", "Costo – Núcleo Vitamínico (*)", "MPD", (C_ALIM,), "1.1.03.06", None, 1, None),
    ("07", "Costo – Aceite Vegetal (*)", "MPD", (C_ALIM,), "1.1.03.07", None, 1, None),
    ("08", "Costo – Productos Veterinarios", "MPD", (C_CARNE,), "1.1.03.08", None, 0, None),
    ("09", "Costo – Insumos Limpieza y desinfección", "MPD", (C_CARNE,), "1.1.03.09", None, 0, None),
    ("10", "Costo – Insumos de Crianza", "MPD", (C_CARNE,), "1.1.03.10", None, 0, None),
    ("11", "Costo – Alimento Balanceado Producido (*)", "MPD", (C_CARNE,), "1.1.03.13", None, 1,
     "Traslado interno del alimento propio al costo de las aves."),
    ("12", "Costo – Bolsas personalizadas", "MPD", (C_CARNE,), "1.1.03.11", None, 0,
     "Embalaje del pollo faenado: es materia prima directa (PC-03), no gasto de venta."),
    ("13", "Costo – Saquillos de Yute (*)", "MPD", (C_ALIM,), "1.1.03.11", None, 1,
     "Solo para el excedente destinado a la venta."),
    ("20", "Mano de Obra Directa – Salario Operarios", "MOD", AMBOS, "2.1.02.01", "2.1.02.05", 0,
     "Sueldo neto a 2.1.02.01 y aporte laboral retenido a 2.1.02.05. Prorrateo por horas desde 2027."),
    ("21", "Mano de Obra Directa – Aguinaldo Operarios", "MOD", AMBOS, "2.1.02.02", None, 0, None),
    ("22", "Mano de Obra Directa – Personal Eventual", "MOD", (C_CARNE,), "2.1.02.04", None, 0, None),
    ("30", "CIF – Depreciación Galpones", "CIF", (C_CARNE,), "4.1.02.01", None, 0, None),
    ("31", "CIF – Depreciación Equipos de Producción", "CIF", (C_CARNE,), "4.1.02.05", None, 0, None),
    ("32", "CIF – Depreciación Alambrados", "CIF", (C_CARNE,), "4.1.02.02", None, 0, None),
    ("33", "CIF – Depreciación Herramientas", "CIF", (C_CARNE,), "4.1.02.06", None, 0, None),
    ("34", "CIF – Depreciación Maquinaria Alimento (*)", "CIF", (C_ALIM,), "4.1.02.04", None, 1, None),
    ("35", "CIF – Depreciación Ambiente Preparación (*)", "CIF", (C_ALIM,), "4.1.02.03", None, 1, None),
    ("36", "CIF – Mantenimiento y Reparaciones de Instalaciones", "CIF", AMBOS, "2.1.01.06", "1.1.03.15", 0,
     "Contrapartida según se pague el servicio (2.1.01.06) o se consuma material (1.1.03.15)."),
    ("37", "CIF – Energía Eléctrica y Agua Potable", "CIF", AMBOS, "2.1.01.06", None, 0,
     "Se prorratea entre centros por horas de mano de obra."),
    ("38", "Mano de Obra Directa – Aporte Patronal Operarios", "MOD", AMBOS, "2.1.02.03", None, 0, None),
    ("39", "Mano de Obra Directa – Indemnización Operarios", "MOD", AMBOS, "2.2.01.01", None, 0, None),
    ("40", "Pérdidas por Mortandad Avícola normal", "PERDIDA", (C_CARNE,), None, None, 0,
     "Reclasificación interna entre sufijos del centro (hasta 5 % acumulado)."),
    ("41", "Pérdidas por Mermas de Producción normal", "PERDIDA", (C_ALIM,), None, None, 1,
     "Reclasificación interna entre sufijos del centro (hasta 5 % acumulado)."),
]

# (código, nombre, cuenta producto terminado, cuenta costo de ventas, uso 2027)
CENTROS_COSTO: list[tuple[str, str, str, str, int]] = [
    (C_CARNE, "Producción de Carne de Pollo", "1.1.03.12", "6.1.02.01", 0),
    (C_ALIM, "Producción de Alimento Balanceado", "1.1.03.13", "6.1.02.02", 1),
]


# ==========================================================================
# (c) PARÁMETROS NORMATIVOS
# ==========================================================================
# (clave, valor, tipo, ejercicio_desde, ejercicio_hasta, fuente, descripción)
Param = tuple[str, str, str, int, "int | None", str, str]


def _p(clave: str, valor: Any, tipo: str, fuente: str, desc: str,
       desde: int = 2026, hasta: int | None = None) -> Param:
    return (clave, str(valor), tipo, desde, hasta, fuente, desc)


def _por_ejercicio(clave: str, valores: dict[int, Any], tipo: str, fuente: str, desc: str) -> list[Param]:
    return [_p(clave, v, tipo, fuente, desc, e, e) for e, v in valores.items()]


def construir_parametros() -> list[Param]:
    """Todos los parámetros normativos con su fuente documental."""
    P: list[Param] = []
    # --- Calendario y ciclo productivo -----------------------------------
    P += [
        _p("mes_inicio_ejercicio", 7, "ENTERO", "PC-01 §V.C; Manual Presupuestario P-01", "Ejercicio 1-jul a 30-jun"),
        _p("dias_ciclo", 73, "ENTERO", "POL-03 Art. 1; PC-04", "Duración del ciclo de crianza natural (días)"),
        _p("dias_ciclo_minimo", 70, "ENTERO", "POL-03 Art. 1", "Prohibido faenar antes de este día (salvo indicación veterinaria)"),
        _p("ciclos_por_ejercicio", 5, "ENTERO", "Manual Presupuestario P-01", "Ciclos productivos por ejercicio"),
        _p("ciclo_cierre_inclusivo", 1, "BOOLEANO", "POA §1.1 (calendario de ciclos); decisión Fase 0",
           "El día de recepción es el día 1: cierre = inicio + 72 (DISCREPANCIA con Master Plan 'inicio + 73')"),
        _p("lote_aves", 1000, "ENTERO", "Mapa de Procesos Integral PO-04", "Aves por lote de faenado"),
        _p("rendimiento_kg_ave", "2.6", "DECIMAL", "Manual Presupuestario P-05", "Kg de carne por ave"),
        _p("consumo_alimento_qq_ave", "0.10", "DECIMAL", "Manual Presupuestario P-05", "Quintales de alimento por ave"),
        _p("densidad_max_aves_m2", 8, "ENTERO", "POL-03 Art. 1", "Densidad máxima (aves/m²)"),
        _p("luz_natural_horas_min", 8, "ENTERO", "POL-03 Art. 1", "Horas mínimas de luz natural"),
    ]
    P += _por_ejercicio("aves_por_ciclo", {2026: 3000, 2027: 3000, 2028: 4000, 2029: 5000, 2030: 6000},
                        "ENTERO", "Negocio Ganadero §9; Manual Presupuestario §3.1", "Aves por ciclo productivo")
    # --- Mortalidad, merma y umbrales técnicos ----------------------------
    P += [
        _p("mortalidad_alerta", "0.03", "DECIMAL", "Manual Presupuestario P-05; PEI KPI", "Alerta operativa de mortalidad acumulada"),
        _p("mortalidad_anormal", "0.05", "DECIMAL", "PC-03; CI-FIN-05", "Sobre este % acumulado la mortalidad es anormal (7.2.01.04)"),
        _p("merma_alimento_anormal", "0.05", "DECIMAL", "PC-07; CI-FIN-05", "Sobre este % la merma de alimento es anormal (7.2.01.05)"),
        _p("ica_umbral_bueno", "2.00", "DECIMAL", "MAN-05 PR-FIN-06; Manual Presupuestario E.3", "ICA < valor = Bueno"),
        _p("ica_umbral_malo", "2.10", "DECIMAL", "MAN-05 PR-FIN-06; Manual Presupuestario E.3", "ICA > valor = Malo"),
        _p("pmf_umbral_bueno", "0.10", "DECIMAL", "Mapa de Procesos Integral; MAN-05 PR-FIN-04", "PMF < valor = Bueno"),
        _p("pmf_umbral_malo", "0.20", "DECIMAL", "Mapa de Procesos Integral; MAN-05 PR-FIN-04", "PMF > valor = Malo"),
        _p("cac_umbral_bueno", "1.00", "DECIMAL", "Mapa de Procesos Integral; MAN-05 PR-FIN-03", "CAC = 100 % = Bueno"),
        _p("cac_umbral_malo", "0.90", "DECIMAL", "Mapa de Procesos Integral; MAN-05 PR-FIN-03", "CAC < 90 % = Malo"),
        _p("dpf_umbral_bueno", "1.00", "DECIMAL", "Mapa de Procesos Financiero FO-02", "DPF <= 100 % = Bueno"),
        _p("dpf_umbral_malo", "1.05", "DECIMAL", "Mapa de Procesos Financiero FO-02", "DPF > 105 % = Malo"),
    ]
    # --- Indicadores financieros ------------------------------------------
    P += [
        _p("icl_umbral_bueno", "0.61", "DECIMAL", "MAN-05 PR-FIN-10 (decisión 1.4 n.º 6)", "ICL < valor = Bueno"),
        _p("icl_umbral_malo", "0.63", "DECIMAL", "MAN-05 PR-FIN-10 (decisión 1.4 n.º 6)", "ICL > valor = Malo"),
        _p("rinc_umbral_malo", "0.05", "DECIMAL", "MAN-05 PR-FIN-09", "RINC < valor = Malo"),
        _p("rinc_umbral_bueno", "0.15", "DECIMAL", "MAN-05 PR-FIN-09; Negocio Ganadero", "RINC >= valor = Bueno (meta 2030)"),
        _p("roa_umbral_malo", "0.10", "DECIMAL", "MAN-05 PR-FIN-01", "ROA < valor = Malo"),
        _p("roa_umbral_bueno", "0.18", "DECIMAL", "MAN-05 PR-FIN-01", "ROA > valor = Bueno"),
        _p("margen_bruto_canal_min", "0.20", "DECIMAL", "CI-FIN-06 Art. 7; Negocio Ganadero", "Margen bruto mínimo por canal"),
        _p("margen_operativo_malo", "0.20", "DECIMAL", "Manual Presupuestario E.3 (propuesto)", "Margen operativo < valor = Malo"),
        _p("margen_operativo_bueno", "0.30", "DECIMAL", "Manual Presupuestario E.3 (propuesto)", "Margen operativo > valor = Bueno"),
        _p("sme_cumplimiento_malo", "1.00", "DECIMAL", "MAN-05 PR-FIN-02", "Cumplimiento SME < valor = Malo"),
        _p("sme_cumplimiento_bueno", "1.50", "DECIMAL", "MAN-05 PR-FIN-02", "Cumplimiento SME > valor = Bueno"),
        _p("cumplimiento_fisico_poa_meta", "0.90", "DECIMAL", "POA §5.2", "Meta de cumplimiento físico del POA"),
        _p("cumplimiento_fisico_poa_alerta", "0.75", "DECIMAL", "POA §5.2 (propuesta)", "Bajo este nivel = rojo"),
        _p("variacion_ingresos_amarillo", "0.05", "DECIMAL", "Manual Presupuestario E.1 (propuesto)", "Variación desfavorable de ingresos: amarillo desde"),
        _p("variacion_ingresos_rojo", "0.10", "DECIMAL", "Manual Presupuestario E.1 (propuesto)", "Variación desfavorable de ingresos: rojo desde"),
        _p("variacion_costos_amarillo", "0.05", "DECIMAL", "Manual Presupuestario E.1 (propuesto)", "Variación de MP/CIF/gastos: amarillo desde"),
        _p("variacion_costos_rojo", "0.10", "DECIMAL", "Manual Presupuestario E.1 (propuesto)", "Variación de MP/CIF/gastos: rojo desde"),
        _p("desvio_flujo_caja_alerta", "0.10", "DECIMAL", "POL-04 Art. 4; FIN-TES-07", "Desvío del flujo proyectado que activa alerta"),
    ]
    # --- Liquidez (SME) y caja ---------------------------------------------
    P += _por_ejercicio("sme", {2026: "136413", 2027: "145912", 2028: "194693", 2029: "237922", 2030: "288775"},
                        "DECIMAL", "POL-04 Art. 1; Manual Presupuestario E.3 (decisión 1.4 n.º 1)",
                        "Saldo Mínimo de Efectivo = costo del ciclo siguiente (Bs)")
    P += [
        _p("sme_piso_absoluto", "15000", "DECIMAL", "Decisión del usuario (confirmación Fase 0, punto 2); NO figura en POL-04",
           "Piso absoluto adicional del SME (Bs)"),
        _p("sme_umbral_compromiso", "1.20", "DECIMAL", "POL-04 Art. 2; MAN-05 PR-FIN-02", "Saldo proyectado < 120 % del SME exige autorización escrita de GG"),
        _p("sme_margen_seguridad_ciclos", "0.5", "DECIMAL", "POL-04 Art. 1", "Margen de seguridad al cierre de ciclo (fracción del costo del ciclo siguiente)"),
        _p("tope_caja_bs", "5000", "DECIMAL", "POL-04 Art. 3; CI-FIN-01", "Fondo y tope diario de efectivo en custodia (Bs)"),
        _p("gasto_menor_max_bs", "500", "DECIMAL", "POL-04 Art. 3; CI-FIN-01", "Gasto menor máximo por operación con caja (Bs)"),
        _p("arqueos_por_anio", 2, "ENTERO", "CI-FIN-01 Paso 7; INF-CV-03", "Arqueos sorpresivos mínimos por año"),
        _p("conciliacion_dias_habiles", 5, "ENTERO", "POL-04 Art. 5; CI-FIN-07", "Plazo de la conciliación bancaria (días hábiles)"),
        _p("pago_efectivo_excedente_max_bs", "5000", "DECIMAL", "POL-01 Art. 3; CI-FIN-02 Art. 10", "Máximo en efectivo por socio al pagar excedentes (Bs)"),
    ]
    # --- Límites de autorización (CI-FIN-09) --------------------------------
    P += [
        _p("autoriza_desembolso_gg_sobre_bs", "1000", "DECIMAL", "POL-04 Art. 2; CI-FIN-09", "Desembolsos mayores requieren autorización de GG (Bs)"),
        _p("firma_mancomunada_sobre_bs", "5000", "DECIMAL", "POL-04 Art. 5; CI-FIN-09", "Desembolsos mayores requieren GG + miembro del CV (Bs)"),
        _p("capex_no_previsto_ca_sobre_bs", "20000", "DECIMAL", "POL-02 Art. 1; CI-FIN-09", "CAPEX no previsto mayor requiere Consejo de Administración (Bs)"),
        _p("capex_variacion_pct", "0.15", "DECIMAL", "POL-02 Art. 4; CI-FIN-09", "Variación de CAPEX que se informa al CA"),
        _p("capex_variacion_monto_bs", "10000", "DECIMAL", "POL-02 Art. 4; CI-FIN-09", "Con la variación porcentual, exige nueva aprobación de la Asamblea (Bs)"),
        _p("oc_tres_cotizaciones_sobre_bs", "5000", "DECIMAL", "PR-FIN-03; CI-FIN-09", "Compras mayores requieren 3 cotizaciones (Bs)"),
        _p("cotizaciones_minimas", 3, "ENTERO", "PR-FIN-03", "Número de cotizaciones exigidas"),
        _p("baja_activo_cv_sobre_bs", "5000", "DECIMAL", "PC-05; PR-FIN-07", "Baja de activo con valor neto mayor requiere verificación del CV (Bs)"),
        _p("baja_inventario_cv_sobre_bs", "2000", "DECIMAL", "PC-07", "Baja de inventario mayor requiere visto del CV (Bs)"),
        _p("mayoria_calificada_numerador", 2, "ENTERO", "POL-01 Art. 4; POL-02 Art. 2", "Mayoría calificada de la Asamblea (numerador)"),
        _p("mayoria_calificada_denominador", 3, "ENTERO", "POL-01 Art. 4; POL-02 Art. 2", "Mayoría calificada de la Asamblea (denominador)"),
        _p("dia_semana_orden_compra", 1, "ENTERO", "PR-FIN-03; Mapa Integral PO-01", "Día de emisión de órdenes de compra (ISO: 1 = lunes)"),
        _p("dia_semana_control_stock", 5, "ENTERO", "PR-FIN-03; Mapa Integral PO-01", "Día del control de stock físico (ISO: 5 = viernes)"),
        _p("dia_semana_contratacion_eventual", 1, "ENTERO", "POL-06 Art. 2; PR-FIN-04", "Día de contratación del personal eventual (ISO: 1 = lunes)"),
    ]
    # --- Cargas laborales y personal ---------------------------------------
    P += [
        _p("aporte_patronal", "0.1721", "DECIMAL", "PC-09; POA §1.2", "Aporte patronal (Gestora Pública y CNS)"),
        _p("aporte_laboral", "0.1271", "DECIMAL", "PC-09", "Aporte laboral retenido (no es costo de la cooperativa)"),
        _p("aguinaldo_pct", "0.0833", "DECIMAL", "PC-09", "Provisión mensual de aguinaldo"),
        _p("indemnizacion_pct", "0.0833", "DECIMAL", "PC-09", "Provisión mensual de indemnización"),
        _p("aguinaldo_mes_pago", 12, "ENTERO", "Ley General del Trabajo (decisión 1.4 n.º 10)",
           "Mes de pago del aguinaldo (el Excel lo paga en el ciclo 5; DISCREPANCIA)"),
        _p("liquidacion_final_dias", 15, "ENTERO", "PC-09; PR-FIN-05", "Plazo de liquidación final (días)"),
        _p("indexacion_salarial", "0.05", "DECIMAL", "Manual Presupuestario P-05", "Indexación salarial anual"),
        _p("salario_encargado_granja_mensual", "3300", "DECIMAL", "Manual de Costos §6.2", "Sueldo mensual del Encargado de Granja en 2026 (Bs)", ),
        _p("tarifa_eventual_por_ave", "1.50", "DECIMAL", "POA B2; Manual de Costos §6.2", "Tarifa de faenado por ave (Bs)"),
        _p("prorrateo_mod_carne_defecto", "0.75", "DECIMAL", "POL-06; Manual Presupuestario B.4",
           "Porción por defecto de la MOD a carne (el resto a alimento); se ajusta con hojas de tiempo", 2027),
        _p("circular_cierre_dias_antelacion", 7, "ENTERO", "POA B1 (propuesta)", "Antelación de la circular de cierre de ciclo (días)"),
    ]
    # --- Precios y comercial ------------------------------------------------
    P += [
        _p("precio_carne_kg", "23.50", "DECIMAL", "Manual Presupuestario §3.1", "Precio de la carne (Bs/kg)"),
        _p("precio_gallinaza_carga", "500", "DECIMAL", "Manual Presupuestario §3.1", "Precio de la gallinaza (Bs/carga)"),
        _p("precio_alimento_qq", "180", "DECIMAL", "Manual Presupuestario §3.1", "Precio del alimento excedente (Bs/qq)", 2027),
        _p("margen_min_alimento", "0.10", "DECIMAL", "Manual Presupuestario P-04", "Margen mínimo del alimento sobre el costo", 2027),
        _p("precio_pollito_bb", "9.00", "DECIMAL", "Manual Presupuestario B.2", "Precio del pollito BB (Bs/ave)"),
        _p("precio_alimento_comprado_qq", "185", "DECIMAL", "Manual Presupuestario B.2", "Alimento comprado a terceros (Bs/qq)", 2026, 2026),
        _p("flete_por_lote_bs", "970", "DECIMAL", "Manual Presupuestario §4.1", "Flete de venta por lote de 1.000 aves (Bs)"),
        _p("preventa_dias_min", 3, "ENTERO", "CI-FIN-13 Art. 4", "Antelación mínima del pedido HORECA (días calendario)"),
        _p("preventa_friales_min", 2, "ENTERO", "CI-FIN-13 Art. 6", "Friales con pedido indicativo como mínimo"),
        _p("cheques_diferidos_permitidos", 0, "BOOLEANO", "POL-05 Art. 1", "No se aceptan cheques diferidos"),
        _p("credito_incumplimientos_suspension", 2, "ENTERO", "POL-05 Art. 2; CIR-ING-01", "Incumplimientos consecutivos que suspenden la línea"),
        _p("cobranza_reporte_horas", 24, "ENTERO", "POL-05 Art. 1; CI-FIN-15", "Plazo para reportar cuentas vencidas (horas)"),
        _p("provision_incobrables_pct", "0.05", "DECIMAL", "POL-05 Art. 3", "Provisión mínima sobre deudores", 2028),
        _p("provision_dudoso_dias_mora", 30, "ENTERO", "POL-05 Art. 3", "Mora desde la cual se provisiona al 100 %", 2028),
    ]
    # Crédito HORECA por ejercicio (POL-05)
    P += [
        _p("horeca_credito_dias_max", 1, "ENTERO", "POL-05 Art. 1", "Crédito HORECA máximo (días)", 2026, 2027),
        _p("horeca_credito_dias_max", 7, "ENTERO", "POL-05 Art. 2", "Crédito HORECA máximo (días)", 2028),
        _p("horeca_historial_meses_min", 6, "ENTERO", "POL-05 Art. 2", "Historial comercial mínimo para crédito > 1 día", 2028),
        _p("horeca_monto_max_entregas", 1, "ENTERO", "POL-05 Art. 2", "Monto máximo de crédito = entregas semanales", 2028),
        _p("horeca_cxc_max_pct_ingresos", "0.05", "DECIMAL", "POL-05 Art. 2", "Saldo total de CxC / ingresos operativos del ciclo", 2028),
    ]
    # --- Excedentes, plazos legales y capital ------------------------------
    P += [
        _p("valor_certificado_bs", "100", "DECIMAL", "Acta de Constitución, Punto 4", "Valor nominal del certificado de aportación (Bs)"),
        _p("reserva_legal_min", "0.05", "DECIMAL", "POL-01 Art. 1; Ley 356", "Reserva Legal mínima sobre el excedente neto"),
        _p("fondo_educacion_min", "0.01", "DECIMAL", "POL-01 Art. 1", "Fondo de Educación Cooperativa mínimo"),
        _p("eeff_consejos_dias", 60, "ENTERO", "PC-01; PR-FIN-11", "Plazo para presentar EEFF a los Consejos (días desde el cierre)"),
        _p("eeff_asamblea_dias", 90, "ENTERO", "Ley 356 Art. 55; PR-FIN-11", "Plazo para aprobar EEFF en Asamblea (días desde el cierre)"),
        _p("metodo_valuacion_inventarios", "CPP", "TEXTO", "PC-07 (decisión 1.4 n.º 3)", "Costo Promedio Ponderado (no PEPS)"),
        _p("ventas_sobre_aves_ingresadas", 1, "BOOLEANO", "Manual Presupuestario B.1 (decisión 1.4 n.º 8)",
           "1 = ingresos presupuestados como el Excel (kg sobre aves ingresadas, sin descontar 3 % de mortalidad)"),
    ]
    # --- Inventarios --------------------------------------------------------
    P += [
        _p("stock_seguridad_dias_alimento_bb", 7, "ENTERO", "CI-FIN-16 Art. 6", "Stock de seguridad de alimento y pollitos BB (días)"),
        _p("stock_seguridad_dias_otros", 5, "ENTERO", "CI-FIN-16 Art. 6", "Stock de seguridad de veterinarios y limpieza (días)"),
        _p("rotacion_granos_dias_max", 30, "ENTERO", "PC-07", "Rotación máxima de granos (días de stock)"),
        _p("vencimiento_min_dias_veterinarios", 30, "ENTERO", "PC-07", "Se rechazan productos veterinarios con vigencia menor (días)"),
    ]
    # --- RAU -----------------------------------------------------------------
    P += [
        _p("rau_hectareas", 8, "ENTERO", "Manual Presupuestario P-03 (decisión 1.4 n.º 7)",
           "Hectáreas del RAU. POR VALIDAR: el balance de apertura usa 10 ha"),
        _p("rau_cuota_por_ha", "3.03", "DECIMAL", "Manual Presupuestario P-03; PET-RAU",
           "Cuota por hectárea (Bs). POR VALIDAR para la zona/subzona de Caranavi (Anexo II DS 24463)"),
        _p("rau_vencimiento_mes", 10, "ENTERO", "POL-07 Art. 2; DS 24463", "Mes del vencimiento ordinario del Formulario 701"),
        _p("rau_vencimiento_dia", 31, "ENTERO", "POL-07 Art. 2; DS 24463", "Día del vencimiento ordinario del Formulario 701"),
        _p("rau_anticipacion_dias_habiles", 15, "ENTERO", "POL-07 Art. 2", "Presentar el Formulario 701 con esta antelación (días hábiles)"),
        _p("rau_liquidacion_socio_dias_habiles", 20, "ENTERO", "POL-07 Art. 3", "Comunicar la liquidación por socio con esta antelación (días hábiles)"),
        _p("rau_consulta_cuota_mes", 9, "ENTERO", "POL-07 Art. 3", "Mes límite para consultar la RND de cuota"),
        _p("rau_consulta_cuota_dia", 30, "ENTERO", "POL-07 Art. 3", "Día límite para consultar la RND de cuota"),
        _p("rau_alerta_dias", 30, "ENTERO", "POL-07 Art. 2", "Alerta previa a vencimientos tributarios (días calendario)"),
        _p("certificacion_alerta_dias", 60, "ENTERO", "CI-FIN-04; FIN-EGR-14", "Alerta previa a vencimiento de certificaciones (días)"),
        _p("rau_i1_autoconsumo_verde_min", "0.65", "DECIMAL", "PET-RAU §1.3-1.4", "I-1 autoconsumo de alimento: verde desde", 2027),
        _p("rau_i1_autoconsumo_rojo_max", "0.60", "DECIMAL", "PET-RAU §1.4", "I-1: rojo por debajo de", 2027),
        _p("rau_i2_ventas_alimento_amarillo", "0.15", "DECIMAL", "PET-RAU §1.4", "I-2 ventas de alimento / ingresos operativos: amarillo desde", 2027),
        _p("rau_i2_ventas_alimento_rojo", "0.20", "DECIMAL", "PET-RAU §1.4", "I-2: rojo sobre", 2027),
        _p("rau_no_socios_amarillo", 5, "ENTERO", "PET-RAU §1.4", "No socios con 3+ compras al año: amarillo sobre", 2027),
        _p("rau_no_socios_rojo", 10, "ENTERO", "PET-RAU §1.4", "No socios con 3+ compras al año: rojo sobre", 2027),
    ]
    # --- Depreciación y amortización (PC-06) -------------------------------
    for cat, tasa, vida in [
        ("galpones", "0.05", 20), ("ambiente_preparacion", "0.05", 20),
        ("maquinaria_alimento", "0.125", 8), ("equipos_produccion", "0.125", 8),
        ("herramientas", "0.25", 4), ("alambrados", "0.10", 10),
        ("muebles_enseres", "0.10", 10), ("equipos_computacion", "0.25", 4),
        ("gastos_constitucion", "0.25", 4), ("software", "0.25", 4),
    ]:
        P.append(_p(f"deprec_tasa_{cat}", tasa, "DECIMAL", "PC-06 §V.A y §V.C", f"Tasa anual de depreciación/amortización: {cat} ({vida} años)"))
    return P


# ==========================================================================
# (d) SOCIOS (Acta de Constitución, 1-may-2026)
# ==========================================================================
# (n.º, nombre, C.I., certificados, cargo)
SOCIOS: list[tuple[int, str, str, int, str | None]] = [
    (1, "Rocío Del Alba Flores Colque", "9105875", 446, "Gerente General (provisional)"),
    (2, "Geovanna Capia Morante", "10947604", 446, "Presidente del Consejo de Administración"),
    (3, "Melisa Canaviri Mamani", "12924924", 445, "Secretario del Consejo de Administración"),
    (4, "Jhudith Ayllon Copa", "11086390", 445, "Tesorero del Consejo de Administración"),
    (5, "Pablo Alejandro Alarcon", "14378652", 446, "Presidente del Consejo de Vigilancia"),
    (6, "Elva Danitza Cachaga Alanoca", "7058564", 445, "Secretario del Consejo de Vigilancia"),
    (7, "Alan Alberth Chavez Colmena", "8324812", 445, "Vocal del Consejo de Vigilancia"),
    (8, "Adrian Mauricio Chura Chuquimia", "12959899", 445, "Vocal del Consejo de Vigilancia"),
    (9, "Valentina Febe Laruta Orosco", "10913867", 445, "Vocal del Consejo de Vigilancia"),
    (10, "Leonardo Fabio Oblitas Flores", "12734511", 445, None),
    (11, "Brayan Joaquin Rodriguez Llusco", "14184262", 445, None),
    (12, "Anahi Lucia Vera Galvez", "12927885", 445, None),
]
FECHA_CONSTITUCION = "2026-05-01"
CAPITAL_ACTA_BS = 534300
CERTIFICADOS_ACTA = 5343

# ==========================================================================
# (f) ROLES Y PERMISOS (MOF-FIN-001, CI-FIN-09, CI-FIN-10)
# ==========================================================================
ROLES: list[tuple[str, str, str, int]] = [
    ("GG", "Gerente General",
     "Dirige y autoriza (OC, desembolsos, planillas, bajas, cierres). No registra transacciones contables.", 0),
    ("JF", "Jefe de Finanzas y Comercialización – Contador General",
     "Registra, costea y concilia; opera la caja. No autoriza ni firma desembolsos.", 0),
    ("UT", "Unidad Técnica (Encargado de Granja)",
     "Produce la evidencia de campo (bitácora, faenado, recepción). No registra contablemente ni autoriza pagos.", 0),
    ("CA", "Consejo de Administración",
     "Aprueba presupuesto, CAPEX, políticas, tarifario y compatibilidad RAU.", 0),
    ("CV", "Consejo de Vigilancia",
     "Fiscaliza sin aprobar ni ejecutar; cofirma desembolsos mayores a Bs 5.000. Escribe solo sus informes.", 1),
    ("AG", "Asamblea General",
     "Máxima autoridad: aprueba EEFF, excedentes, deuda y variaciones mayores de CAPEX.", 1),
]

RECURSOS: tuple[str, ...] = (
    "tesoreria", "compras", "inventarios", "planillas", "produccion", "costos", "activos",
    "ventas", "contabilidad", "rau", "presupuesto", "vigilancia", "dashboard", "parametros",
    "usuarios", "auditoria",
)

_TODOS_VER = {r: ["VER"] for r in RECURSOS if r not in ("usuarios", "auditoria")}


def _mezclar(base: dict[str, list[str]], extra: dict[str, list[str]]) -> dict[str, list[str]]:
    resultado = {k: list(v) for k, v in base.items()}
    for recurso, acciones in extra.items():
        actuales = resultado.setdefault(recurso, [])
        actuales.extend(a for a in acciones if a not in actuales)
    return resultado


# PROPUESTA a validar con la Gerencia: deriva del MOF (autoridad por cargo),
# CI-FIN-09 (matriz de autorización) y CI-FIN-10 (segregación de funciones).
PERMISOS: dict[str, dict[str, list[str]]] = {
    "GG": {
        "tesoreria": ["VER", "AUTORIZAR"], "compras": ["VER", "CREAR", "AUTORIZAR"],
        "inventarios": ["VER", "AUTORIZAR"], "planillas": ["VER", "AUTORIZAR"],
        "produccion": ["VER", "AUTORIZAR"], "costos": ["VER", "AUTORIZAR"],
        "activos": ["VER", "AUTORIZAR"], "ventas": ["VER", "AUTORIZAR"],
        "contabilidad": ["VER", "AUTORIZAR"], "rau": ["VER", "AUTORIZAR"],
        "presupuesto": ["VER", "CREAR", "EDITAR"], "vigilancia": ["VER"], "dashboard": ["VER"],
        "parametros": ["VER", "EDITAR"], "usuarios": ["VER", "CREAR", "EDITAR"], "auditoria": ["VER"],
    },
    "JF": {
        **{r: ["VER", "CREAR", "EDITAR"] for r in (
            "tesoreria", "compras", "inventarios", "planillas", "produccion", "costos",
            "activos", "ventas", "rau", "presupuesto")},
        "contabilidad": ["VER", "CREAR", "EDITAR", "REVERSAR"],
        "vigilancia": ["VER"], "dashboard": ["VER"], "parametros": ["VER"],
    },
    "UT": {
        "produccion": ["VER", "CREAR", "EDITAR"], "inventarios": ["VER", "CREAR"],
        "compras": ["VER"], "planillas": ["VER", "CREAR"], "ventas": ["VER", "CREAR"],
        "costos": ["VER"], "activos": ["VER"], "dashboard": ["VER"],
    },
    "CA": _mezclar(_TODOS_VER, {
        "presupuesto": ["APROBAR"], "activos": ["APROBAR"], "ventas": ["APROBAR"],
        "rau": ["APROBAR"], "contabilidad": ["APROBAR"], "parametros": ["APROBAR"],
    }),
    "CV": _mezclar(_TODOS_VER, {
        **{r: ["VERIFICAR"] for r in (
            "tesoreria", "compras", "inventarios", "planillas", "produccion", "costos",
            "activos", "ventas", "contabilidad", "rau", "presupuesto")},
        "tesoreria": ["VERIFICAR", "COFIRMAR"],
        "vigilancia": ["CREAR", "EDITAR", "VERIFICAR"], "auditoria": ["VER"],
    }),
    "AG": _mezclar({r: ["VER"] for r in ("dashboard", "contabilidad", "presupuesto", "vigilancia", "tesoreria")}, {
        "contabilidad": ["APROBAR"], "presupuesto": ["APROBAR"], "activos": ["APROBAR"],
        "parametros": ["APROBAR"], "tesoreria": ["APROBAR"],
    }),
}

FUENTE_PERMISOS = "MOF-FIN-001 §2; CI-FIN-09; CI-FIN-10 (matriz propuesta)"

# ==========================================================================
# (e) BALANCE DE APERTURA (1-jul-2026)
# ==========================================================================
# (cuenta, nombre en documentos origen, monto Bs) — todo al DEBE; el capital al HABER.
# DISCREPANCIA: Bancos = 138.010 (Manual Presupuestario, saldo inicial de efectivo)
# y no 138.003 (Diagnóstico Financiero) para que el activo iguale el capital del
# acta (Bs 534.300; el Diagnóstico muestra 534.293, Bs 7 menos).
APERTURA_DEBE: list[tuple[str, str, int]] = [
    ("1.1.01.03", "Bancos", 138010),
    ("1.2.01.01", "Terrenos", 210000),
    ("1.2.01.02", "Galpones", 105000),
    ("1.2.01.03", "Alambrados, tranqueras y vallas", 15000),
    ("1.2.01.06", "Equipos agrícolas (= Equipos de Producción Avícola)", 39450),
    ("1.2.01.07", "Herramientas", 3240),
    ("1.2.01.09", "Equipos de computación", 4400),
    ("1.2.01.08", "Muebles y enseres", 5500),
    ("1.2.02.01", "Costos de constitución", 6700),
    ("1.2.02.02", "Software contable", 7000),
]
APERTURA_HABER: tuple[str, int] = ("3.1.01.01", CAPITAL_ACTA_BS)

# ==========================================================================
# CONCILIACIÓN DE DISCREPANCIAS ENTRE DOCUMENTOS
# ==========================================================================
# (tipo, tema, detalle, decisión, fuente)
CONCILIACION: list[tuple[str, str, str, str, str]] = [
    ("CATALOGO", "Código de Devoluciones en Ventas",
     "Plan v3: encabezado «5.1.03 Reguladoras de ingresos operativos» pero cuenta 5.1.04.01. "
     "Descripción de Cuentas: 5.1.03.01 (y subgrupo «5.3.03»). PC-08 y MAN-05 usan 5.1.04.01.",
     "Se usa 5.1.04.01 (Plan v3 como maestro). El subgrupo se creó como 5.1.04 para que coincida con el código de la cuenta.",
     "Plan v3; Descripción de Cuentas; PC-08; MAN-05"),
    ("CATALOGO", "Previsión vs. Provisión para indemnización (2.2.01.01)",
     "Plan v3, Descripción y PC-09 dicen «Provisión»; MAN-006 (modelo de balance, Nota 11), Diagnóstico Financiero y el análisis de riesgos dicen «Previsión».",
     "Nombre oficial: «Provisión para Indemnización» (PC-09 corrige la terminología). Los estados de la Fase 10 deben mostrar «Provisión».",
     "Plan v3; PC-09; MAN-006"),
    ("CATALOGO", "Numeración de 4.1.01 (deterioro de activo corriente)",
     "PC-07 numera 4.1.01.01 = Materias Primas, .02 = Alimento, .03 = Carne. El Plan v3 incluye además Cuentas por Cobrar como 4.1.01.01 y desplaza el resto (.02, .03, .04).",
     "Se usa la numeración del Plan v3 (maestro). PC-07 debe corregirse.",
     "Plan v3; PC-07; POL-05 Art. 3"),
    ("CATALOGO", "Fondo Rotativo – Caja Chica (1.1.01.05)",
     "Ausente en el Plan v3 (Disponible llega a 1.1.01.04) pero exigido por la Descripción de Cuentas, MAN-05 FICHA-FIN-01, CI-FIN-01 y POL-04.",
     "Se incorporó como 1.1.01.05 con origen DESCRIPCION (único complemento al Plan v3).",
     "Descripción de Cuentas; MAN-05; CI-FIN-01; POL-04"),
    ("CATALOGO", "Ajuste de Capital (3.1.01.06) y Ajuste de Reservas Patrimoniales (3.1.02.06)",
     "Están en la Descripción de Cuentas pero no en el Plan v3.",
     "No se incorporaron: el ajuste por inflación está suspendido (Res. CTNAC 03/2020). Pueden activarse con una migración si el CTNAC lo reanuda.",
     "Descripción de Cuentas; PC-02"),
    ("CATALOGO", "Gastos de Embalaje y Presentación (7.1.03.02)",
     "Existe en la Descripción de Cuentas; no está en el Plan v3. PC-03 establece que el embalaje es materia prima directa (sufijo 12).",
     "No se incorporó; el embalaje se costea con el sufijo 12.",
     "Descripción de Cuentas; PC-03; Plan v3"),
    ("CATALOGO", "Nombres de 7.1.01.01 y 7.1.01.03",
     "Plan v3: «Sueldos – Personal Administrativo» y «Aportes Patronales – Personal Administrativo». Descripción: «Sueldos – Gerencia General y Administración» y «Beneficios Sociales – Personal Administrativo».",
     "Se usan los nombres del Plan v3.",
     "Plan v3; Descripción de Cuentas"),
    ("CATALOGO", "Códigos de subgrupo erróneos en la Descripción de Cuentas",
     "La Descripción asigna 5.2.01.03 al subgrupo «5.4.01» y Devoluciones al «5.3.03».",
     "Se usan los códigos jerárquicos del Plan v3 (el código de la cuenta define su subgrupo).",
     "Descripción de Cuentas; Plan v3"),
    ("CATALOGO", "Cantidad de sufijos de costo",
     "El Master Plan y las instrucciones hablan de «41 sufijos» (códigos 01 a 41), pero el Plan v3 define 28: 01-13, 20-22 y 30-41. No existen 14-19 ni 23-29.",
     "Se cargaron los 28 sufijos existentes (códigos 01 a 41). No se inventaron sufijos.",
     "Plan v3 (tablas de sufijos); Manual de Costos"),
    ("CATALOGO", "Clasificación de los sufijos 38 y 39 (aporte patronal e indemnización del operario)",
     "Plan v3 y Manual de Costos los llaman «Mano de Obra Directa» (MOD). El Manual Presupuestario (B.5) los presenta dentro de los CIF.",
     "Elemento MOD en el catálogo (Plan v3, principio de Uniformidad). La presentación presupuestaria puede agruparlos como CIF.",
     "Plan v3; Manual de Costos §6.2; Manual Presupuestario B.5"),
    ("CATALOGO", "Centro aplicable del sufijo 41 (mermas de producción normal)",
     "Plan v3 y PC-07: solo 6.1.01.02 (alimento). Descripción de Cuentas: «ambos centros».",
     "Se asignó solo a 6.1.01.02, según Plan v3 y PC-07.",
     "Plan v3; PC-07; Descripción de Cuentas"),
    ("CATALOGO", "Nombre de las cuentas de Equipos",
     "El Diagnóstico Financiero y el balance de apertura usan «Equipos agrícolas»; el Plan v3 usa «Equipos de Producción Avícola» (1.2.01.06).",
     "Se mapeó a 1.2.01.06.",
     "Diagnóstico Financiero; Plan v3"),
    ("APERTURA", "Capital social: Bs 534.300 vs. Bs 534.293",
     "El Acta de Constitución integra Bs 534.300 (5.343 certificados). El Diagnóstico Financiero muestra Bs 534.293 de activo y capital.",
     "Capital de Bs 534.300 según el acta (decisión 1.4 n.º 9). La diferencia de Bs 7 se absorbe en Bancos.",
     "Acta de Constitución; Diagnóstico Financiero; Manual Presupuestario §5.3"),
    ("APERTURA", "Saldo en Bancos de apertura: Bs 138.010 vs. Bs 138.003",
     "El Manual Presupuestario (flujo de caja) inicia con Bs 138.010; el Diagnóstico Financiero muestra Bs 138.003.",
     "Se usa Bs 138.010 para que el activo (Bs 534.300) iguale al capital del acta.",
     "Manual Presupuestario §5.1; Diagnóstico Financiero"),
    ("APERTURA", "Aportes en efectivo (acta) vs. aportes en especie (MAN-006)",
     "El acta dice que los 12 socios pagaron en efectivo; MAN-006 (Nota 12.1) habla de aportes en efectivo y en especie (terreno e infraestructura), y el balance de apertura registra Bs 396.290 de activos fijos.",
     "Se registra el capital íntegro en 3.1.01.01 y los activos tal como figuran en el balance de apertura. VALIDAR con la Asamblea el origen (efectivo vs. especie) antes de emitir las Notas.",
     "Acta de Constitución; MAN-006 Nota 12; Diagnóstico Financiero"),
    ("NORMATIVA", "Saldo Mínimo de Efectivo (SME)",
     "POL-04 define el SME como el costo del ciclo siguiente (Bs 136.400 en el texto; Bs 136.413 en el Manual Presupuestario). MAN-05 PR-FIN-02 lista valores iguales al ingreso por ciclo (Bs 184.800). El usuario pidió además un piso de Bs 15.000.",
     "SME por ejercicio de POL-04/Manual Presupuestario (136.413; 145.912; 194.693; 237.922; 288.775) y parámetro adicional sme_piso_absoluto = 15.000 por decisión del usuario.",
     "POL-04; MAN-05; Manual Presupuestario; decisión Fase 0"),
    ("NORMATIVA", "Umbral del ICL",
     "MAN-05: Bueno < 61 %, Normal 61-63 %, Malo > 63 %. Negocio Ganadero y hoja Indicadores del Excel: 62,05 % único.",
     "Se usa MAN-05 (parametrizable).",
     "MAN-05 PR-FIN-10; Negocio Ganadero"),
    ("NORMATIVA", "Escala del ICA",
     "El texto del Mapa Integral y MAN-05 usan Bueno < 2,00, Normal 2,00-2,10, Malo > 2,10; la tabla final del mismo Mapa usa 1,80/1,95.",
     "Se usan 2,00/2,10 (texto, MAN-05 y Manual Presupuestario).",
     "Mapa de Procesos Integral; MAN-05; Manual Presupuestario"),
    ("NORMATIVA", "Superficie y cuota RAU",
     "El balance de apertura registra 10 ha; el presupuesto RAU usa 8 ha. La cuota de Bs 3,03/ha figura en la RND como cuota de la zona Altiplano.",
     "Parámetros rau_hectareas = 8 y rau_cuota_por_ha = 3,03 editables, marcados POR VALIDAR.",
     "Manual Presupuestario P-03; PET-RAU"),
    ("NORMATIVA", "Valuación de inventarios",
     "PC-07 fija Costo Promedio Ponderado; el Marco Normativo (Anexo 1) y el Informe Técnico mencionan PEPS.",
     "CPP (parámetro metodo_valuacion_inventarios).",
     "PC-07; Anexo 1"),
    ("NORMATIVA", "Fecha de cierre del ciclo",
     "Master Plan: «cierre = inicio + 73». POA: el día de recepción es el día 1 (C1: 1-jul a 11-sep-2026).",
     "Criterio inclusivo del POA (parámetro ciclo_cierre_inclusivo = 1). Confirmado por el usuario en la Fase 0.",
     "POA §1.1; Master Plan §1.3"),
    ("NORMATIVA", "Ingresos sobre aves ingresadas",
     "El presupuesto calcula 7.800 kg por ciclo sin descontar 3 % de mortalidad (Bs 5.499 por ciclo).",
     "Se replica el Excel (parámetro ventas_sobre_aves_ingresadas = 1); el ERP mostrará la brecha frente a kg reales.",
     "Manual Presupuestario B.1; POA §3.3"),
    ("NORMATIVA", "Pago del aguinaldo",
     "El Excel lo paga en el ciclo 5; la ley lo fija en diciembre.",
     "Se provisiona mensualmente (PC-09); el mes de pago es el parámetro aguinaldo_mes_pago = 12.",
     "PC-09; POA §3.3"),
]


# ==========================================================================
# (g) FASE 2: PARÁMETROS DE SEGURIDAD Y SUPLENCIA
# ==========================================================================
_PROPUESTA = ("Propuesta Fase 2 (POR VALIDAR): ningún documento fija la cifra; "
              "Master Plan Fase 13 exige bloqueo por intentos y MAN-ARC-01 §5.2 contraseñas personales")


def construir_parametros_fase2() -> list[Param]:
    """Parámetros que usa la Fase 2 (login, sesión y suplencia CI-FIN-17)."""
    return [
        _p("password_caducidad_dias", 90, "ENTERO", "MAN-ARC-01 §5.2 (Permisos: cambio cada 90 días)",
           "Días de vigencia de una contraseña antes de exigir su cambio"),
        _p("password_longitud_min", 8, "ENTERO", _PROPUESTA, "Longitud mínima de la contraseña"),
        _p("password_requiere_letra_y_numero", 1, "BOOLEANO", _PROPUESTA,
           "La contraseña debe combinar letras y números"),
        _p("login_intentos_max", 3, "ENTERO", _PROPUESTA,
           "Intentos fallidos consecutivos que bloquean al usuario"),
        _p("login_bloqueo_minutos", 15, "ENTERO", _PROPUESTA, "Minutos de bloqueo tras superar los intentos"),
        _p("sesion_inactividad_minutos", 30, "ENTERO", _PROPUESTA,
           "Minutos sin actividad tras los cuales la sesión se cierra"),
        _p("suplencia_cargo_orden_1", "Presidente del Consejo de Administración", "TEXTO",
           "CI-FIN-17 Art. 1; POL-06 Art. 2", "Primer suplente de la Gerencia General"),
        _p("suplencia_cargo_orden_2", "Tesorero del Consejo de Administración", "TEXTO",
           "CI-FIN-17 Art. 1; POL-06 Art. 2", "Segundo suplente de la Gerencia General (en defecto del primero)"),
        _p("suplencia_comunicacion_horas", 24, "ENTERO", "CI-FIN-17 Art. 2",
           "Plazo para comunicar a la Gerencia General una autorización ejercida por suplencia"),
        _p("ejercicio_inicio_alimento_propio", 2027, "ENTERO",
           "Plan de Cuentas v3, nota (*); PC-04; Manual Presupuestario P-01",
           "Primer ejercicio en que se habilitan las cuentas, sufijos y centro marcados con (*)"),
    ]


# (tipo, tema, detalle, decisión, fuente) — complemento del cierre de la Fase 1 (O-1).
CONCILIACION_FASE2: list[tuple[str, str, str, str, str]] = [
    ("CATALOGO", "Codificación de 1.1.03.14 y 1.1.03.15",
     "El Plan v3 del Manual de Gestión Contable codifica 1.1.03.14 = Materiales de mantenimiento y no tiene "
     "cuenta de Gallinaza. Plan_de_Cuentas_Pollolandia.xlsx y MAN-006 (nota de inventarios) usan 1.1.03.14 = "
     "Gallinaza y 1.1.03.15 = Materiales de mantenimiento; 23. Plan de Cuentas Contable.xlsx tiene Gallinaza en "
     ".14 y no tiene .15. El Manual remite además el alimento terminado a 1.1.03.14 (debe ser 1.1.03.13).",
     "Se mantiene 1.1.03.14 = Gallinaza y 1.1.03.15 = Materiales de mantenimiento, porque la gallinaza es un "
     "subproducto inventariable que vende la cooperativa. El centro 6.1.01.02 usa 1.1.03.13. Pendiente de "
     "confirmación del usuario.",
     "Plan v3 (Manual §III.5); Plan_de_Cuentas_Pollolandia.xlsx; 23. Plan de Cuentas Contable.xlsx; MAN-006"),
    ("CATALOGO", "Clasificación 4.2 Reguladoras de pasivo sin subcuentas",
     "El Plan v3 incluye la clasificación 4.2 sin subgrupos ni cuentas; los Excel del plan no la incluyen.",
     "Se conserva 4.2 como nodo de nivel 2 sin cuentas de movimiento (no admite asientos).",
     "Plan v3 (Manual §III.5); Plan_de_Cuentas_Pollolandia.xlsx"),
]

# Fuentes que se agregan a discrepancias ya registradas (orden -> texto adicional).
FUENTES_ADICIONALES: dict[int, str] = {
    1: "Plan_de_Cuentas_Pollolandia.xlsx y 23. Plan de Cuentas Contable.xlsx (usan 5.1.03.01)",
    5: "Plan_de_Cuentas_Pollolandia.xlsx y 23. Plan de Cuentas Contable.xlsx (las incluyen)",
}


# ==========================================================================
# (h) FASE 3: PARÁMETROS DE TESORERÍA
# ==========================================================================
def construir_parametros_fase3() -> list[Param]:
    """Parámetros que usa la Fase 3 (tesorería). Los topes de caja, el SME,
    el umbral del 120 %, el desvío del 10 % y el plazo de conciliación ya
    existen desde la Fase 1."""
    return [
        _p("jerarquia_pagos",
           "PLANILLA_APORTES,POLLITOS_INSUMOS_NUEVO_CICLO,ALIMENTO_VETERINARIOS_CICLO,OBLIGACIONES_RAU,"
           "CERTIFICACIONES,CAPEX_APROBADO,EXCEDENTES_SOCIOS", "TEXTO", "POL-04 Art. 2; PR-FIN-02",
           "Orden de prelación de pagos en restricción de liquidez (de mayor a menor prioridad)"),
        _p("cuenta_cargos_bancarios", "7.2.01.03", "TEXTO",
           "Propuesta Fase 3 (POR VALIDAR): el Plan de Cuentas v3 no tiene cuenta de comisiones bancarias",
           "Cuenta de gasto para cargos bancarios no registrados (CI-FIN-07)"),
        _p("cuenta_intereses_bancarios", "5.2.01.01", "TEXTO", "Plan de Cuentas v3 (5.2.01.01)",
           "Cuenta de ingreso para intereses ganados abonados por el banco"),
        _p("partida_transito_alerta_dias", 30, "ENTERO", "CI-FIN-07 (Recomendaciones)",
           "Partidas en tránsito con mayor antigüedad se investigan de inmediato"),
        _p("conciliacion_diferencia_reporte_horas", 48, "ENTERO", "CI-FIN-07 (Preguntas frecuentes)",
           "Plazo para explicar una diferencia antes de reportarla al Consejo de Vigilancia"),
    ]


def _cargar_parametros_fase3(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase3())


def _cargar_conciliacion_fase3(con: sqlite3.Connection) -> int:
    """Registra la confirmación del usuario sobre 1.1.03.14/15 (n.º 24)."""
    con.execute(
        "UPDATE conciliacion_catalogo SET decision = ? WHERE tema = 'Codificación de 1.1.03.14 y 1.1.03.15'",
        ("Se mantiene 1.1.03.14 = Gallinaza y 1.1.03.15 = Materiales de mantenimiento, como subcuentas de "
         "inventarios especializados y materiales de granja dentro del activo realizable. El centro 6.1.01.02 "
         "usa 1.1.03.13. Confirmado por el usuario el 11/10/2026.",))
    return 1


# ==========================================================================
# (i) FASE 4: COMPRAS E INVENTARIOS
# ==========================================================================
def construir_parametros_fase4() -> list[Param]:
    """Parámetros nuevos de la Fase 4. Los de stock de seguridad (7 y 5 días),
    rotación de granos (30 días), vencimiento de veterinarios (30 días), baja
    con visto del CV (Bs 2.000), día de OC (lunes) y de control de stock
    (viernes), 3 cotizaciones sobre Bs 5.000 y umbrales del CAC ya existen
    desde la Fase 1."""
    return [
        _p("cuenta_perdida_baja_inventario", "7.2.01.03", "TEXTO",
           "Propuesta Fase 4 (POR VALIDAR): PC-07 manda reconocer el deterioro como gasto no operativo, pero el "
           "Plan de Cuentas v3 no tiene una cuenta específica de pérdida por bajas de inventario",
           "Cuenta de gasto no operativo para bajas de inventario por deterioro, vencimiento o pérdida"),
    ]


# Catálogo inicial de insumos: uno por cuenta 1.1.03.xx (estructura del Plan v3,
# no cifras). (código, nombre, cuenta, unidad, grupo crítico CI-FIN-16, grano,
# controla vencimiento, transferencia inmediata, comprable)
# DISCREPANCIA: CI-FIN-16 Art. 6 fija 5 días de stock de seguridad para
# "productos veterinarios e insumos de limpieza" y no menciona los insumos de
# crianza, que sí están en su alcance (Art. 2) y en la fila "Insumos de Limpieza
# y Crianza" del FIN-EGR-01. Se aplican 5 días también a crianza.
INSUMOS_INICIALES: list[tuple[str, str, str, str, str | None, int, int, int, int]] = [
    ("INS-0301", "Pollitos BB (próximo lote)", "1.1.03.01", "ave", "ALIMENTO_BB", 0, 0, 1, 1),
    ("INS-0302", "Alimento balanceado comprado", "1.1.03.02", "qq", "ALIMENTO_BB", 1, 0, 0, 1),
    ("INS-0303", "Maíz amarillo", "1.1.03.03", "qq", "ALIMENTO_BB", 1, 0, 0, 1),
    ("INS-0304", "Torta de soya", "1.1.03.04", "qq", "ALIMENTO_BB", 1, 0, 0, 1),
    ("INS-0305", "Afrecho de trigo", "1.1.03.05", "qq", "ALIMENTO_BB", 1, 0, 0, 1),
    ("INS-0306", "Núcleo vitamínico", "1.1.03.06", "kg", "ALIMENTO_BB", 0, 0, 0, 1),
    ("INS-0307", "Aceite vegetal", "1.1.03.07", "litro", "ALIMENTO_BB", 0, 0, 0, 1),
    ("INS-0308", "Productos veterinarios (vacunas y medicamentos)", "1.1.03.08", "unidad",
     "VETERINARIO_LIMPIEZA", 0, 1, 0, 1),
    ("INS-0309", "Insumos de limpieza y desinfección", "1.1.03.09", "unidad", "VETERINARIO_LIMPIEZA", 0, 0, 0, 1),
    ("INS-0310", "Insumos de crianza (viruta, cama)", "1.1.03.10", "unidad", "VETERINARIO_LIMPIEZA", 0, 0, 0, 1),
    ("INS-0311", "Bolsas personalizadas", "1.1.03.11", "unidad", None, 0, 0, 0, 1),
    ("INS-0311S", "Saquillos de yute", "1.1.03.11", "unidad", None, 0, 0, 0, 1),
    ("INS-0312", "Carne de pollo (producto terminado)", "1.1.03.12", "kg", None, 0, 0, 0, 0),
    ("INS-0313", "Alimento balanceado producido (producto terminado)", "1.1.03.13", "qq", None, 0, 0, 0, 0),
    ("INS-0314", "Gallinaza (subproducto)", "1.1.03.14", "carga", None, 0, 0, 0, 0),
    ("INS-0315", "Materiales de mantenimiento", "1.1.03.15", "unidad", None, 0, 0, 0, 1),
]

# Partidas del presupuesto de compras por ciclo (FIN-EGR-02). 2026: Manual
# Presupuestario D.4-1 (Carne de pollo 2026, por ciclo). 2027: Manual
# Presupuestario B.2 y B.3 (anual), repartido en 5 ciclos iguales según la nota
# de D.4. El Presupuesto Maestro completo y sus modificaciones son de la Fase 12.
_D41 = "Manual Presupuestario D.4-1 (2026, por ciclo)"
_B23 = "Manual Presupuestario B.2/B.3 (2027, anual ÷ 5 ciclos, nota D.4)"
PARTIDAS_COMPRAS: list[tuple[int, str, str, dict[int, str], str]] = [
    (2026, "1.1.03.01", "Pollitos BB", {c: "27000" for c in range(1, 6)}, _D41),
    (2026, "1.1.03.02", "Alimento balanceado comprado", {c: "55500" for c in range(1, 6)}, _D41),
    (2026, "1.1.03.08", "Productos veterinarios", {c: "5895" for c in range(1, 6)}, _D41),
    (2026, "1.1.03.09", "Insumos de limpieza", {c: "780" for c in range(1, 6)}, _D41),
    (2026, "1.1.03.10", "Insumos de crianza", {c: "2910" for c in range(1, 6)}, _D41),
    (2026, "1.1.03.11", "Bolsas personalizadas", {1: "4800", 2: "3600", 3: "3600", 4: "3600", 5: "3600"}, _D41),
    (2026, "1.1.03.15", "Mantenimiento y limpieza (CIF)", {c: "1500" for c in range(1, 6)},
     _D41 + "; la partida incluye servicios, se asigna a materiales de mantenimiento"),
    (2027, "1.1.03.01", "Pollitos BB", {c: "27000" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.03", "Maíz amarillo", {c: "19785" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.04", "Torta de soya", {c: "17530.80" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.05", "Afrecho de trigo", {c: "1839.40" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.06", "Núcleo vitamínico", {c: "9944.80" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.07", "Aceite vegetal", {c: "2846.60" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.08", "Productos veterinarios", {c: "5475" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.09", "Insumos de limpieza", {c: "630" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.10", "Insumos de crianza", {c: "2310" for c in range(1, 6)}, _B23),
    (2027, "1.1.03.11", "Bolsas personalizadas", {c: "3600" for c in range(1, 6)}, _B23),
]


def _cargar_parametros_fase4(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase4())


def _cargar_insumos(con: sqlite3.Connection) -> int:
    for codigo, nombre, cuenta, unidad, grupo, grano, venc, inmediata, comprable in INSUMOS_INICIALES:
        con.execute(
            """INSERT INTO insumos (codigo, nombre, cuenta_inventario, unidad, grupo_critico, es_grano,
                   controla_vencimiento, transferencia_inmediata, comprable, registrado_por, registrado_en)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (codigo, nombre, cuenta, unidad, grupo, grano, venc, inmediata, comprable, USUARIO_SISTEMA, _ahora()))
    return len(INSUMOS_INICIALES)


def _cargar_partidas_compras(con: sqlite3.Connection) -> int:
    n = 0
    for ejercicio, cuenta, descripcion, montos, fuente in PARTIDAS_COMPRAS:
        for ciclo, monto in montos.items():
            con.execute(
                """INSERT INTO partidas_presupuesto (ejercicio, ciclo, cuenta_inventario, descripcion, monto_cent,
                       fuente) VALUES (?,?,?,?,?,?)""", (ejercicio, ciclo, cuenta, descripcion, a_centavos(monto), fuente))
            n += 1
    return n


# ==========================================================================
# (j) FASE 5: PERSONAL Y PLANILLAS
# ==========================================================================
_POR_VALIDAR = "Propuesta Fase 5 (POR VALIDAR): el plazo no figura en los documentos"


def construir_parametros_fase5() -> list[Param]:
    """Parámetros nuevos de la Fase 5. Las tasas (12,71 %, 17,21 %, 8,33 %),
    la tarifa de Bs 1,50 por ave, el plazo de 15 días de la liquidación final,
    los 7 días de la circular, el lunes de contratación, el prorrateo 75 %
    (desde 2027) y los umbrales del DPF ya existen desde la Fase 1."""
    return [
        # DISCREPANCIA: PC-09 dice 8,33 % y a la vez "equivalente a 1/12 del sueldo
        # mensual"; el Manual de Costos y el Manual Presupuestario (B.4) calculan
        # Bs 660 = 7.920 / 12 (con 8,33 % serían Bs 659,74). Se aplica 1/12 (DS 1802;
        # "un mes de sueldo por año"); con 0 se usaría el porcentaje redondeado.
        _p("provision_base_doceavos", 1, "BOOLEANO", "PC-09 §V.A (1/12); DS 1802; Manual Presupuestario B.4",
           "1 = aguinaldo e indemnización como 1/12 exacto del salario; 0 = porcentaje aguinaldo_pct/indemnizacion_pct"),
        _p("dias_mes_comercial", 30, "ENTERO", "Ley General del Trabajo (mes comercial); FIN-EGR-11",
           "Días del mes para haber proporcional, vacaciones y liquidación final"),
        _p("liquidacion_final_antiguedad_min_dias", 90, "ENTERO", "PC-09 §II; FIN-EGR-11",
           "Antigüedad mínima (días) para la liquidación final de beneficios"),
        _p("ci_fin_14_muestra_min", "0.20", "DECIMAL", "CI-FIN-14 Paso 1",
           "Muestra mínima de semanas en la verificación cruzada de horas"),
        _p("sueldos_pago_dias_despues_mes", 0, "ENTERO", _POR_VALIDAR + " (LGT Art. 53: periodos no mayores a un mes)",
           "Días después del fin de mes para pagar sueldos (0 = último día del mes)"),
        _p("aportes_pago_ultimo_dia_habil_mes_siguiente", 1, "BOOLEANO", _POR_VALIDAR + " (Gestora Pública y CNS)",
           "1 = los aportes del mes vencen el último día hábil del mes siguiente"),
        _p("aguinaldo_pago_dia_limite", 20, "ENTERO", _POR_VALIDAR + " (DS 1802; mes en aguinaldo_mes_pago)",
           "Día límite de pago del aguinaldo en el mes de pago"),
        _p("alerta_pago_planillas_dias", 5, "ENTERO", _POR_VALIDAR,
           "Días de anticipación de la alerta de pago legal de planillas"),
    ]


def construir_parametros_fase6() -> list[Param]:
    """Parámetros nuevos de la Fase 6. Ya existen desde la Fase 1: ciclo de 73
    días (``dias_ciclo``), mínimo de 70 (``dias_ciclo_minimo``), 5 ciclos,
    lote de 1.000 aves, densidad de 8 aves/m², umbrales de mortalidad (3 % y
    5 %), del PMF (10 % y 20 %) y del ICA (2,00 y 2,10)."""
    return [
        _p("fase_inicio_dia_fin", 21, "ENTERO", "CI-FIN-11 Alcance y Paso 2; Master Plan Fase 6",
           "Último día de la fase de inicio (días 1-21, vigilancia sanitaria reforzada)"),
        _p("fase_crecimiento_dia_fin", 50, "ENTERO", "Master Plan Fase 6; PR-FIN-06 §2 (subfases del ciclo)",
           "Último día de la fase de crecimiento (días 22-50); el acabado va del 51 al 73"),
        _p("muestreo_peso_dias", 7, "ENTERO", "POL-03 Art. 2 (v); POA A7",
           "Periodicidad del muestreo de peso (días): una muestra por semana del ciclo"),
        _p("qr_url_base", "https://vidadigna.pollolandia.bo/lote/", "TEXTO",
           "Propuesta Fase 6 (POR VALIDAR): la dirección no figura en los documentos; POL-03 Art. 3",
           "Dirección base de la página de trazabilidad del lote (se agrega el código del lote)"),
    ]


def _cargar_parametros_fase6(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase6())


def construir_parametros_fase7() -> list[Param]:
    """Parámetros nuevos de la Fase 7 (los umbrales de variación del 5 % y
    10 % y la proporción del 75 % a carne ya existen desde la Fase 1)."""
    return [
        # DISCREPANCIA: ningún documento fija el peso del quintal; el POA (R8)
        # equipara 300 qq por ciclo con un ICA de 1,77 sobre 7.800 kg, lo que
        # supone 46 kg por qq (quintal boliviano de 100 libras).
        _p("kg_por_quintal", 46, "ENTERO", "POA 2026-2027 R8 (300 qq ↔ ICA 1,77 sobre 7.800 kg)",
           "Kilogramos por quintal para convertir el alimento de la bitácora (kg) al kárdex (qq)"),
        _p("saquillos_por_qq", 1, "ENTERO", "Manual de Costos §6.4 (585 qq × Bs 10 de saquillo)",
           "Saquillos de yute por quintal de alimento excedente ensaquillado (sufijo 13)"),
    ]


def _cargar_parametros_fase7(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase7())


# Presupuesto de costos por ciclo y sufijo (Manual Presupuestario D.4-1 y D.4-2).
# (sufijo, concepto, unidad, precio o None, monto por ciclo; para 2026 el ciclo 1
# lleva Bs 4.800 de bolsas y los ciclos 2 a 5 Bs 3.600).
# DISCREPANCIA: D.4 presenta aportes patronales (38) e indemnización (39) dentro
# de los CIF; el Manual de Costos §6.2 y el Plan de Cuentas los clasifican como
# MOD. El presupuesto se guarda por sufijo (no por elemento), de modo que la
# comparación es la misma; el elemento se muestra según el Manual de Costos.
_C = "6.1.01.01"
_A = "6.1.01.02"
PRESUPUESTO_COSTOS: dict[int, list[tuple[str, str, str, str | None, str | None, str]]] = {
    2026: [
        (_C, "01", "Pollitos BB", "ave", "9", "27000"), (_C, "02", "Alimento comprado", "qq", "185", "55500"),
        (_C, "08", "Productos veterinarios", None, None, "5895"), (_C, "09", "Insumos de limpieza", None, None, "780"),
        (_C, "10", "Insumos de crianza", None, None, "2910"), (_C, "12", "Bolsas personalizadas", None, None, "BOLSAS"),
        (_C, "20", "Salario Encargado de Cría", None, None, "7920"), (_C, "21", "Aguinaldo", None, None, "660"),
        (_C, "22", "Personal eventual de faenado", "ave", "1.5", "4500"),
        (_C, "38", "Aportes patronales", None, None, "1363"), (_C, "39", "Indemnización", None, None, "660"),
        (_C, "30", "Depreciación galpones", None, None, "1050"), (_C, "31", "Depreciación equipos", None, None, "986"),
        (_C, "33", "Depreciación herramientas", None, None, "162"), (_C, "32", "Depreciación alambrados", None, None, "300"),
        (_C, "36", "Mantenimiento y limpieza", None, None, "1500"), (_C, "37", "Energía eléctrica y agua", None, None, "300"),
    ],
    2027: [
        (_C, "01", "Pollitos BB", "ave", "9", "27000"), (_C, "11", "Alimento producido", "qq", "138.05", "41415"),
        (_C, "08", "Productos veterinarios", None, None, "5475"), (_C, "09", "Insumos de limpieza", None, None, "630"),
        (_C, "10", "Insumos de crianza", None, None, "2310"), (_C, "12", "Bolsas personalizadas", None, None, "3600"),
        (_C, "20", "Salario Encargado de Cría (75 %)", None, None, "6237"), (_C, "21", "Aguinaldo", None, None, "520"),
        (_C, "22", "Personal eventual de faenado", "ave", "1.5", "4500"),
        (_C, "38", "Aportes patronales", None, None, "1073"), (_C, "39", "Indemnización", None, None, "520"),
        (_C, "30", "Depreciación galpones", None, None, "1050"), (_C, "31", "Depreciación equipos", None, None, "986"),
        (_C, "33", "Depreciación herramientas", None, None, "162"), (_C, "32", "Depreciación alambrados", None, None, "300"),
        (_C, "36", "Mantenimiento y limpieza", None, None, "1500"), (_C, "37", "Energía eléctrica y agua", None, None, "300"),
        (_A, "03", "Maíz amarillo", "qq", "95.12", "19785"), (_A, "04", "Torta de soya", "qq", "149.84", "17531"),
        (_A, "05", "Afrecho de trigo", "qq", "167.23", "1839"), (_A, "06", "Núcleo vitamínico", "kg", "18", "9945"),
        (_A, "07", "Aceite vegetal", "litro", "12.01", "2847"),
        (_A, "20", "Salario (porción asignada al alimento)", None, None, "2079"), (_A, "21", "Aguinaldo", None, None, "173"),
        (_A, "34", "Depreciación maquinaria de alimento", None, None, "1925"),
        (_A, "35", "Depreciación ambiente de preparación", None, None, "200"),
        (_A, "36", "Mantenimiento y limpieza", None, None, "150"), (_A, "38", "Aportes patronales", None, None, "358"),
        (_A, "39", "Indemnización", None, None, "173"), (_A, "37", "Energía eléctrica y agua", None, None, "563"),
    ],
}


def _cargar_presupuesto_costos(con: sqlite3.Connection) -> int:
    n = 0
    for ejercicio, filas in PRESUPUESTO_COSTOS.items():
        fuente = f"Manual Presupuestario D.4 ({ejercicio}, por ciclo)"
        for ciclo in range(1, 6):
            for centro, sufijo, concepto, unidad, precio, monto in filas:
                if monto == "BOLSAS":
                    monto = "4800" if ciclo == 1 else "3600"
                cantidad = None
                if precio is not None:
                    cantidad = int((Decimal(monto) / Decimal(precio)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                                   * 1000)
                con.execute(
                    """INSERT INTO presupuesto_costos (ejercicio, ciclo, centro_costo, sufijo, concepto, unidad,
                           cantidad_mil, precio, monto_cent, fuente) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (ejercicio, ciclo, centro, sufijo, concepto, unidad if precio is not None else None, cantidad,
                     precio, a_centavos(monto), fuente))
                n += 1
    return n


def construir_parametros_fase8() -> list[Param]:
    """Parámetros nuevos de la Fase 8 (las tasas de PC-06, los umbrales CAPEX
    de POL-02, el de baja con CV y los del ROA ya existen desde la Fase 1)."""
    return [
        _p("capex_plan_mes_limite", 5, "ENTERO", "POL-02 Art. 1; PR-FIN-01 paso 1",
           "Mes límite para elaborar el Plan CAPEX del ejercicio siguiente"),
        _p("capex_plan_dia_limite", 31, "ENTERO", "POL-02 Art. 1; PR-FIN-01 paso 1",
           "Día límite para elaborar el Plan CAPEX del ejercicio siguiente"),
        _p("capex_ciclo_ejecucion", 5, "ENTERO", "Manual Presupuestario D.5 (ejecución: ciclo 5 de cada año)",
           "Ciclo productivo en que se ejecuta el CAPEX del plan"),
        _p("capex_informe_mes_semestre_1", 1, "ENTERO", "PR-FIN-01; FIN-TES-04 (enero y julio)",
           "Mes de presentación del informe CAPEX del primer semestre (julio a diciembre)"),
        _p("capex_informe_mes_semestre_2", 7, "ENTERO", "PR-FIN-01; FIN-TES-04 (enero y julio)",
           "Mes de presentación del informe CAPEX del segundo semestre (enero a junio)"),
        _p("verificacion_fisica_meses", 12, "ENTERO", "PR-FIN-07 paso 5; INF-CV-01; MOF (verificación física anual)",
           "Plazo máximo entre verificaciones físicas de activos por el CV (meses)"),
        _p("deprec_tasa_terrenos", "0", "DECIMAL", "PC-06 §V.A (Terrenos: no aplica)",
           "Tasa anual de depreciación: terrenos (no se deprecian)"),
        # DISCREPANCIA: PC-06 §V.C y la Descripción de Cuentas (1.2.02.02) fijan
        # el software al 25 % anual (4 años); el Diagnóstico Financiero (balance
        # proyectado: 7.000 → 5.600 → 4.200 → 2.800 → 1.400 → 0) y el caso de
        # prueba de la Fase 8 (Bs 7.000 → Bs 1.400 por año) amortizan el
        # software contable de apertura en 5 años (20 %). Se aplica el 20 % solo
        # al software del balance de apertura (criterio técnico documentado);
        # todo software nuevo usa deprec_tasa_software (25 %, PC-06).
        _p("amort_tasa_software_apertura", "0.20", "DECIMAL",
           "Diagnóstico Financiero (balance proyectado del software contable); caso de prueba Fase 8",
           "Tasa anual de amortización del software contable del balance de apertura (5 años)"),
    ]


def _cargar_parametros_fase8(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase8())


# Plan CAPEX 2026-2030 del Manual Presupuestario D.5 (coincide con el Negocio
# Ganadero). (ejercicio, activo, categoría PC-06, monto Bs, justificación).
# DISCREPANCIA: el Manual de Gestión Contable (§6.3) omite las herramientas
# adicionales de 2027 y 2029 (Bs 3.240); el D.5 y el Negocio Ganadero las
# incluyen. Se aplica el D.5.
_J_MAQ = "Planta propia de alimento balanceado: sustituye el alimento comprado (ahorro del 25 % por qq, D.6)"
_J_AMB = "Ambiente para la preparación del alimento balanceado de la planta propia"
_J_GAL = "Galpón adicional de 1.000 aves por ciclo (escalonamiento de 3.000 a 6.000 aves)"
_J_EQP = "Equipo de crianza (comederos, bebederos, estufas, balanzas) para 1.000 aves adicionales"
_J_HER = "Herramientas adicionales para 3.000 aves"
CAPEX_D5: list[tuple[int, str, str, str, str]] = [
    (2026, "Maquinaria para alimento balanceado", "maquinaria_alimento", "77000", _J_MAQ),
    (2026, "Construcción de ambiente de preparación", "ambiente_preparacion", "20000", _J_AMB),
    (2027, "Construcción de 1 galpón adicional", "galpones", "35000", _J_GAL),
    (2027, "Compra de equipo adicional para 1.000 aves", "equipos_produccion", "13150", _J_EQP),
    (2027, "Compra de herramientas adicionales para 3.000 aves", "herramientas", "3240", _J_HER),
    (2028, "Construcción de 1 galpón adicional", "galpones", "35000", _J_GAL),
    (2028, "Compra de equipo adicional para 1.000 aves", "equipos_produccion", "13150", _J_EQP),
    (2029, "Maquinaria para alimento balanceado (segunda línea)", "maquinaria_alimento", "77000",
     "Duplica la capacidad de la planta para llegar a 90 qq semanales en 2030"),
    (2029, "Construcción de ambiente de preparación (segundo)", "ambiente_preparacion", "20000",
     "Duplica la capacidad de la planta para llegar a 90 qq semanales en 2030"),
    (2029, "Construcción de 1 galpón adicional", "galpones", "35000", _J_GAL),
    (2029, "Compra de equipo adicional para 1.000 aves", "equipos_produccion", "13150", _J_EQP),
    (2029, "Compra de herramientas adicionales para 3.000 aves", "herramientas", "3240", _J_HER),
]


def _cargar_capex_d5(con: sqlite3.Connection) -> int:
    ciclo = 5
    fila = con.execute("SELECT valor FROM parametros WHERE clave = 'capex_ciclo_ejecucion'").fetchone()
    if fila is not None:
        ciclo = int(fila[0])
    orden: dict[int, int] = {}
    for ejercicio, activo, categoria, monto, justificacion in CAPEX_D5:
        orden[ejercicio] = orden.get(ejercicio, 0) + 1
        con.execute(
            """INSERT INTO capex_referencia (ejercicio, orden, activo, categoria, monto_cent, ciclo, justificacion,
                   fuente) VALUES (?,?,?,?,?,?,?,?)""",
            (ejercicio, orden[ejercicio], activo, categoria, a_centavos(monto), ciclo, justificacion,
             "Manual Presupuestario D.5 (Plan CAPEX 2026-2030); Negocio Ganadero"))
    return len(CAPEX_D5)


# La Asamblea ratifica el Plan CAPEX (PR-FIN-01 paso 5) y aprueba variaciones
# mayores (POL-02 Art. 4): necesita consultar el módulo de activos, no solo
# aprobar. Se agrega la lectura sin ampliar ninguna facultad de registro.
PERMISOS_FASE8: list[tuple[str, str, str, str]] = [
    ("AG", "activos", "VER", "PR-FIN-01 paso 5; POL-02 Art. 4 (ratificación del Plan CAPEX por la Asamblea)"),
]


def _cargar_permisos_fase8(con: sqlite3.Connection) -> int:
    for rol, recurso, accion, fuente in PERMISOS_FASE8:
        con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES (?,?,?,?)", (rol, recurso, accion, fuente))
    return len(PERMISOS_FASE8)


def construir_parametros_fase9() -> list[Param]:
    """Parámetros nuevos de la Fase 9. El crédito HORECA por ejercicio, la
    pre-venta de 3 días con 2 friales, el margen del 20 %, los cheques
    diferidos, el reporte en 24 horas, los 2 incumplimientos y la provisión
    del 5 % con mora de 30 días ya existen desde la Fase 1."""
    return [
        _p("margen_meses_consecutivos", 2, "ENTERO", "CI-FIN-06 Art. 7",
           "Meses consecutivos con margen bajo el mínimo que obligan a ajustar el tarifario"),
        _p("cobertura_lote_min", "1.00", "DECIMAL", "CI-FIN-13 Art. 8",
           "Fracción del lote que deben cubrir los pedidos antes del faenado (100 %)"),
        _p("cobertura_verificacion_dias_antes", 1, "ENTERO", "CI-FIN-13 Art. 8",
           "La cobertura se verifica a más tardar este número de días antes del faenado"),
        _p("provision_incobrables_pct_mora", "1.00", "DECIMAL", "PR-FIN-08 §4; POL-05 Art. 3",
           "Provisión de cuentas con mora mayor al plazo (100 %)", 2028),
    ]


def _cargar_parametros_fase9(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase9())


def construir_parametros_fase10() -> list[Param]:
    """Parámetros nuevos de la Fase 10. La Reserva Legal (5 %), el Fondo de
    Educación (1 %) y los plazos de 60 y 90 días ya existen desde la Fase 1."""
    return [
        _p("eeff_alerta_dias", 15, "ENTERO",
           "Propuesta Fase 10 (POR VALIDAR): PR-FIN-11 fija los plazos de 60 y 90 días pero no la anticipación "
           "de la alerta", "Días antes del vencimiento de los plazos de 60/90 días en que se emite la alerta"),
        _p("excedentes_criterio_distribucion", "IGUALITARIO", "TEXTO", "CI-FIN-02 Art. 7; POL-01 Art. 2",
           "Criterio por defecto de reparto del excedente distribuible entre los socios fundadores"),
        _p("excedentes_pago_meses", 3, "ENTERO", "CI-FIN-02 Art. 9 (primer ciclo del ejercicio siguiente, "
           "julio-septiembre)", "Meses desde el cierre en que se espera pagar los excedentes"),
        _p("excedentes_rc_iva_tasa", "0", "DECIMAL",
           "POL-01 Art. 2 («RC-IVA cuando corresponda»); POR VALIDAR: los documentos no fijan la tasa ni el "
           "supuesto de retención", "Tasa de deducción aplicada a la liquidación individual del socio"),
    ]


def _cargar_parametros_fase10(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase10())


# ==========================================================================
# FASE 11 — RAU, CUMPLIMIENTO LEGAL Y ARCHIVO CONTABLE
# ==========================================================================
def construir_parametros_fase11() -> list[Param]:
    """Parámetros nuevos de la Fase 11. Ya existen desde la Fase 1: hectáreas
    y cuota por ha (POR VALIDAR), vencimiento 31-oct, 15 y 20 días hábiles,
    consulta de RND al 30-sep, alerta de 30 días, I-1, I-2 y no socios."""
    return [
        _p("rau_alerta_temprana_dias", 60, "ENTERO", "Master Plan Fase 11 (alertas a 30 y 60 días); FIN-EGR-14",
           "Alerta temprana previa a los vencimientos del calendario fiscal (días calendario)"),
        _p("rau_no_socio_compras_recurrente", 3, "ENTERO", "PET-RAU §1.4",
           "Compras en el año a partir de las cuales un no socio es comprador recurrente", 2027),
        _p("rau_merma_amarillo", "0.03", "DECIMAL", "PET-RAU §1.4 (merma de alimento, sufijo 41)",
           "Merma de alimento: amarillo desde (el rojo es merma_alimento_anormal)", 2027),
        _p("rau_compras_sin_respaldo_rojo", "0.10", "DECIMAL", "PET-RAU §1.4",
           "Porcentaje del valor comprado sin documentación completa: rojo sobre"),
        _p("rau_expediente_completitud_min", "0.95", "DECIMAL", "PET-RAU §4.1 (verificación 7) y §5",
           "Completitud mínima del expediente EXP-RAU A-G"),
        _p("rau_alerta_roja_gg_horas", 24, "ENTERO", "PET-RAU §1.4",
           "Toda alerta roja se reporta a la Gerencia General en este plazo (horas)"),
        _p("rau_alerta_roja_ca_dias_habiles", 5, "ENTERO", "PET-RAU §1.4 y §4.3",
           "Toda alerta roja se reporta y resuelve en el Consejo de Administración en este plazo (días hábiles)"),
        _p("rau_fiscalizacion_informe_horas", 24, "ENTERO", "PET-RAU §4.2",
           "Informar a GG, CA y CV la notificación del SIN en este plazo (horas)"),
        _p("rau_fiscalizacion_analisis_dias", 3, "ENTERO", "PET-RAU §4.2",
           "Analizar alcance y pedir prórroga dentro de estos días desde la recepción"),
        _p("rau_buzon_revision_dias", 7, "ENTERO", "PET-RAU §4.1 (verificación 9: registro semanal)",
           "Frecuencia máxima de revisión del buzón y notificaciones del SIN (días)"),
        _p("archivo_retencion_t10_anios", 10, "ENTERO", "MAN-ARC-01 §2.2; Código de Comercio",
           "Clase Temporal 10 años (soporte contable, tributario y laboral)"),
        _p("archivo_retencion_t5_anios", 5, "ENTERO", "MAN-ARC-01 §2.2; Ley 2492 Arts. 59-60",
           "Clase Temporal 5 años (control interno, indicadores y fiscalización)"),
        _p("archivo_retencion_desecho_anios", 2, "ENTERO", "MAN-ARC-01 §2.2",
           "Clase Desecho (alertas diarias y circulares consolidadas)"),
        _p("archivo_retencion_vida_util_adicional_anios", 10, "ENTERO", "MAN-ARC-01 §2.2",
           "Activos fijos: vida útil (o baja) más estos años"),
        _p("archivo_folios_max_legajo", 200, "ENTERO", "MAN-ARC-01 §4.3",
           "Folios máximos por legajo; al superarlos se abre el tomo siguiente"),
    ]


def _cargar_parametros_fase11(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase11())


# Tabla de retención documental del MAN-ARC-01 §3 (código, documento,
# procedimiento, clase, cómputo, criterio de organización, disposición, ciclo).
_DS = "Destrucción segura al vencer el plazo"
_DG = "Digitalización + destrucción segura al vencer el plazo"
_HI = "Digitalización (PDF/A) + transferencia a archivo histórico"
_DA = "Destrucción segura (acta simplificada)"
RETENCION_DOCUMENTAL: list[tuple[str, str, str, str, str, str, str, str]] = [
    ("FIN-TES-01", "Plan Anual de Inversiones (CAPEX)", "PR-FIN-01", "PERMANENTE", "CIERRE", "Crono-numérico (gestión + correlativo)", _HI, "TES"),
    ("FIN-TES-02", "Hoja de Cálculo de Indicadores (ROA)", "PR-FIN-01", "T5", "CIERRE", "Cronológico (por gestión)", _DS, "TES"),
    ("FIN-TES-03", "Informe Técnico-Financiero de Inversiones", "PR-FIN-01", "T10", "CIERRE", "Crono-numérico", _DG, "TES"),
    ("FIN-TES-04", "Informe Semestral de Ejecución CAPEX", "PR-FIN-01", "T10", "CIERRE", "Cronológico (por semestre)", _DG, "TES"),
    ("INF-CV-01", "Acta de Verificación Física de Activos", "PR-FIN-01 / PR-FIN-07", "T10", "CIERRE", "Crono-numérico (por año de verificación)", _DG, "TES"),
    ("FIN-TES-05", "Flujo de Caja Proyectado", "PR-FIN-02", "T5", "CIERRE", "Cronológico (por ciclo productivo)", _DS, "TES"),
    ("FIN-TES-06", "Reporte Diario de Saldo Bancario", "PR-FIN-02", "DESECHO", "CIERRE", "Cronológico (por mes)", _DA, "TES"),
    ("FIN-TES-07", "Alerta de Riesgo de Liquidez", "PR-FIN-02", "T5", "CIERRE", "Crono-numérico", _DS, "TES"),
    ("FIN-TES-08", "Registro de Medida Correctiva Adoptada", "PR-FIN-02", "T5", "CIERRE", "Crono-numérico", _DS, "TES"),
    ("INF-CV-02", "Informe Trimestral de Fiscalización de Liquidez", "PR-FIN-02", "T5", "CIERRE", "Cronológico (por trimestre)", _DS, "TES"),
    ("INF-CV-03", "Acta de Arqueo de Caja", "FICHA-FIN-01", "T5", "CIERRE", "Crono-numérico (fecha de arqueo)", _DS, "TES"),
    ("INF-CV-04", "Informe Previo de Fiscalización (excedentes)", "FICHA-FIN-02", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "TES"),
    ("FIN-EGR-01", "Acta de Control de Stock Semanal", "PR-FIN-03", "T5", "CIERRE", "Cronológico (por semana)", _DS, "EGR"),
    ("FIN-EGR-02", "Reporte de Disponibilidad Presupuestaria", "PR-FIN-03 / PR-FIN-04", "T5", "CIERRE", "Crono-numérico", _DS, "EGR"),
    ("FIN-EGR-03", "Orden de Compra", "PR-FIN-03", "T10", "CIERRE", "Numérico (correlativo de O.C.)", _DG, "EGR"),
    ("FIN-EGR-04", "Acta de Recepción de Insumos", "PR-FIN-03", "T10", "CIERRE", "Numérico (vinculada a la O.C.)", _DG, "EGR"),
    ("FIN-EGR-05", "Programación de Pagos a Proveedores", "PR-FIN-03", "T5", "CIERRE", "Cronológico (por mes)", _DS, "EGR"),
    ("CIR-EGR-01", "Circular de Aviso de Cierre de Ciclo Productivo", "PR-FIN-04", "DESECHO", "CIERRE", "Crono-numérico", _DA, "EGR"),
    ("FIN-EGR-06", "Autorización de Contratación de Personal Eventual", "PR-FIN-04", "T10", "CIERRE", "Crono-numérico", _DG, "EGR"),
    ("FIN-EGR-07", "Registro de Carcasas y Cálculo de PMF", "PR-FIN-04", "T10", "CIERRE", "Por lote / ciclo productivo", _DG, "EGR"),
    ("FIN-EGR-08", "Liquidación de Jornales Eventuales", "PR-FIN-04", "T10", "CIERRE", "Por lote / ciclo productivo", _DG, "EGR"),
    ("FIN-TRA-05", "Registro de Faenado", "PR-FIN-04 / PR-FIN-06", "T10", "CIERRE", "Por lote / ciclo productivo", _DG, "EGR"),
    ("CONT-EGR-01", "Contrato Eventual por Servicio de Faenado", "PR-FIN-04", "T10", "CIERRE", "Alfabético (trabajador) y cronológico", _DG, "EGR"),
    ("FIN-EGR-09", "Planilla de Sueldos", "PR-FIN-05", "T10", "CIERRE", "Cronológico (por mes)", _DG, "EGR"),
    ("FIN-EGR-10", "Planilla de Beneficios Sociales", "PR-FIN-05", "T10", "CIERRE", "Cronológico (por mes)", _DG, "EGR"),
    ("FIN-EGR-11", "Liquidación Final de Beneficios", "PR-FIN-05", "T10", "CIERRE", "Alfabético (por trabajador)", _DG, "EGR"),
    ("INF-CV-05", "Informe de Verificación de Provisiones Laborales", "PR-FIN-05", "T5", "CIERRE", "Cronológico (por gestión)", _DS, "EGR"),
    ("FIN-EGR-12", "Hojas de Tiempo Firmadas", "FICHA-FIN-03", "T10", "CIERRE", "Alfabético (trabajador) y cronológico (semana)", _DG, "EGR"),
    ("FIN-EGR-13", "Reporte de Absorción de Mano de Obra", "FICHA-FIN-03", "T5", "CIERRE", "Cronológico (por mes)", _DS, "EGR"),
    ("FIN-EGR-14", "Calendario de Certificaciones", "FICHA-FIN-04", "T5", "SUSTITUCION", "Cronológico (por vencimiento)", _DS, "EGR"),
    ("FIN-TRA-01", "Ficha de Apertura de Ciclo Productivo", "PR-FIN-06", "T10", "CIERRE", "Por lote / ciclo productivo", _DG, "TRA"),
    ("FIN-TRA-02", "Bitácora Diaria de Ciclo", "PR-FIN-06", "T10", "CIERRE", "Cronológico (por lote, día 1–73)", _DG, "TRA"),
    ("FIN-TRA-03", "Mayor Analítico por Sufijo", "PR-FIN-06", "T10", "CIERRE", "Por centro de costo y sufijo", _DG, "TRA"),
    ("FIN-TRA-04", "Registro de Baja por Mortalidad", "PR-FIN-06 / FICHA-FIN-05", "T10", "CIERRE", "Cronológico (por lote)", _DG, "TRA"),
    ("FIN-TRA-06", "Informe de Costo de Ciclo", "PR-FIN-06", "PERMANENTE", "CIERRE", "Por lote / ciclo productivo", _HI, "TRA"),
    ("FIN-TRA-07", "Cálculo de Costo Unitario", "PR-FIN-06", "T10", "CIERRE", "Por lote / ciclo productivo", _DG, "TRA"),
    ("FIN-TRA-08", "Formulario de Activación de Activo Fijo", "PR-FIN-07", "VIDA_UTIL_10", "BAJA", "Por activo (código interno)", _DG, "TRA"),
    ("FIN-TRA-09", "Ficha Individual de Activo Fijo", "PR-FIN-07", "VIDA_UTIL_10", "BAJA", "Por activo (código interno)", _DG, "TRA"),
    ("FIN-TRA-10", "Planilla Mensual de Depreciación", "PR-FIN-07", "T10", "CIERRE", "Cronológico (por mes)", _DG, "TRA"),
    ("FIN-TRA-11", "Bitácora de Disposición Sanitaria", "FICHA-FIN-05", "T5", "CIERRE", "Cronológico (por lote)", _DS, "TRA"),
    ("FIN-ING-01", "Registro de Pedido", "PR-FIN-08", "T5", "CIERRE", "Crono-numérico", _DS, "ING"),
    ("FIN-ING-02", "Resolución de Autorización de Crédito", "PR-FIN-08", "T10", "CIERRE", "Alfabético (por cliente)", _DG, "ING"),
    ("FIN-ING-03", "Nota de Entrega (HORECA)", "PR-FIN-08", "T10", "CIERRE", "Numérico (correlativo)", _DG, "ING"),
    ("FIN-ING-04", "Comprobante de Entrega y Compromiso de Pago", "PR-FIN-08", "T10", "CIERRE", "Alfabético (cliente) y cronológico", _DG, "ING"),
    ("FIN-ING-05", "Registro de Cuentas por Cobrar por Cliente", "PR-FIN-08", "T10", "CIERRE", "Alfabético (por cliente)", _DG, "ING"),
    ("CIR-ING-01", "Notificación de Suspensión de Línea de Crédito", "PR-FIN-08", "T5", "CIERRE", "Alfabético (por cliente)", _DS, "ING"),
    ("FIN-ING-06", "Nota de Entrega (Venta al Contado)", "PR-FIN-09", "T10", "CIERRE", "Numérico (correlativo)", _DG, "ING"),
    ("FIN-ING-07", "Parte Diario de Recaudación", "PR-FIN-09", "T10", "CIERRE", "Crono-numérico (por fecha)", _DG, "ING"),
    ("FIN-ING-08", "Análisis de Margen por Canal", "FICHA-FIN-06", "T5", "CIERRE", "Cronológico (por mes)", _DS, "ING"),
    ("INF-CV-06", "Informe de Revisión de Precios", "FICHA-FIN-06", "T5", "CIERRE", "Cronológico (por gestión)", _DS, "ING"),
    ("FIN-INF-01", "Lista de Verificación de Cierre Mensual", "PR-FIN-10", "T5", "CIERRE", "Cronológico (por mes)", _DS, "INF"),
    ("FIN-INF-02", "Conciliación Bancaria del Mes", "PR-FIN-10 / FICHA-FIN-07", "T10", "CIERRE", "Por cuenta bancaria y mes", _DG, "INF"),
    ("FIN-INF-03", "Consolidado de Costeo Mensual", "PR-FIN-10", "T10", "CIERRE", "Cronológico (por mes)", _DG, "INF"),
    ("FIN-INF-04", "Estado de Resultados Mensual e Indicadores", "PR-FIN-10", "T10", "CIERRE", "Cronológico (por mes)", _DG, "INF"),
    ("FIN-INF-05", "Informe Gerencial de Indicadores", "PR-FIN-10", "T5", "CIERRE", "Cronológico (por mes)", _DS, "INF"),
    ("INF-CV-07", "Informe de Fiscalización Mensual", "PR-FIN-10", "T5", "CIERRE", "Cronológico (por mes)", _DS, "INF"),
    ("INF-CV-08", "Informe de Verificación del Consejo de Vigilancia", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "INF"),
    ("INF-CV-09", "Informe de Fiscalización Tributaria", "FICHA-FIN-08", "T10", "CIERRE", "Cronológico (por gestión)", _DG, "TRV"),
    ("RAU-701", "Formulario 701 V.3 RAU y comprobante de pago", "FICHA-FIN-08", "T10", "CIERRE", "Cronológico (por gestión)", _DG, "TRV"),
    ("RAU-CERT", "Certificado de Inscripción al RAU, NIT y patentes municipales", "FICHA-FIN-08", "T10", "VIGENCIA", "Cronológico (por vigencia)", _DG, "TRV"),
    ("EEFF-01", "Balance General anual (cierre 30 de junio)", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("EEFF-02", "Estado de Resultados anual (escalonado)", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("EEFF-03", "Estado de Flujos de Efectivo anual (método directo)", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("EEFF-04", "Estado de Cambios en el Patrimonio", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("EEFF-05", "Notas a los Estados Financieros", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("EEFF-06", "Informe de auditoría externa (Ley N° 356, Arts. 53–54)", "PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("EEFF-07", "Copia de actas que aprueban EEFF, CAPEX y excedentes", "PR-FIN-01 / PR-FIN-11", "PERMANENTE", "CIERRE", "Cronológico (por fecha de acta)", _HI, "EEFF"),
    ("EEFF-08", "Estados financieros intermedios (Balance y Flujo mensual)", "PR-FIN-10", "T10", "CIERRE", "Cronológico (por mes)", _DG, "EEFF"),
    ("EEFF-09", "Estados financieros proyectados 2026–2030", "PR-FIN-01", "T10", "SUSTITUCION", "Por versión aprobada", _DG, "EEFF"),
    ("LIB-01", "Libros Diario, Mayor e Inventarios y Balances", "PR-FIN-10", "PERMANENTE", "CIERRE", "Cronológico (por gestión)", _HI, "EEFF"),
    ("LIB-02", "Comprobantes contables de ingreso, egreso y traspaso con sus respaldos", "PR-FIN-10", "T10", "CIERRE", "Crono-numérico (correlativo del sistema contable)", _DG, "EEFF"),
    ("LIB-03", "Libro de Inventario de Activos Fijos", "PR-FIN-07", "VIDA_UTIL_10", "BAJA", "Por activo (código interno)", _DG, "EEFF"),
]


def _cargar_retencion(con: sqlite3.Connection) -> int:
    for fila in RETENCION_DOCUMENTAL:
        con.execute("""INSERT INTO retencion_documental (codigo, documento, procedimiento, clase, computo, criterio,
                                                          disposicion, ciclo) VALUES (?,?,?,?,?,?,?,?)""", fila)
    return len(RETENCION_DOCUMENTAL)


# Índice maestro del expediente EXP-RAU A-G (PET-RAU §3.3, contenido mínimo).
# (carpeta, clave, documento, responsable, periodicidad ANUAL/PERMANENTE)
_GG, _FI, _UT, _UF = "Gerencia General", "Finanzas", "Unidad Técnica", "Unidad Técnica; Finanzas"
EXP_RAU_REQUISITOS: list[tuple[str, str, str, str, str]] = [
    ("A", "A01", "Acta de constitución y estatuto", _GG, "PERMANENTE"),
    ("A", "A02", "Personería jurídica AFCOOP", _GG, "PERMANENTE"),
    ("A", "A03", "NIT", _GG, "PERMANENTE"),
    ("A", "A04", "Certificado de Inscripción RAU", _GG, "PERMANENTE"),
    ("A", "A05", "Padrón de socios", _GG, "ANUAL"),
    ("A", "A06", "Libro de actas", _GG, "PERMANENTE"),
    ("A", "A07", "Título o contrato de la tierra", _GG, "PERMANENTE"),
    ("B", "B01", "Formulario 701 y comprobante de pago", _FI, "ANUAL"),
    ("B", "B02", "RND de la cuota del año", _FI, "ANUAL"),
    ("B", "B03", "Liquidaciones individuales por socio", _FI, "ANUAL"),
    ("B", "B04", "Patentes municipales", _FI, "ANUAL"),
    ("B", "B05", "Acta de compatibilidad RAU (Consejo de Administración)", _FI, "ANUAL"),
    ("B", "B06", "INF-CV-09 Informe de Fiscalización Tributaria", _FI, "ANUAL"),
    ("C", "C01", "Registro de ingreso de lote con certificado sanitario y zoosanitario", _UT, "ANUAL"),
    ("C", "C02", "Bitácora diaria FIN-TRA-02", _UT, "ANUAL"),
    ("C", "C03", "Registro de vacunación", _UT, "ANUAL"),
    ("C", "C04", "FIN-TRA-04 y FIN-TRA-11 (bajas y disposición sanitaria)", _UT, "ANUAL"),
    ("C", "C05", "FIN-TRA-05 Registro de faenado", _UT, "ANUAL"),
    ("C", "C06", "Registros de peso", _UT, "ANUAL"),
    ("C", "C07", "Certificado de inocuidad SENASAG", _UT, "PERMANENTE"),
    ("C", "C08", "Libro de visitas", _UT, "PERMANENTE"),
    ("D", "D01", "Registro de fórmulas y ensacado (FIN-TRA-12)", _UF, "ANUAL"),
    ("D", "D02", "Balance de masas (FIN-TRA-13)", _UF, "ANUAL"),
    ("D", "D03", "Programa de excedente aprobado", _UF, "ANUAL"),
    ("D", "D04", "Padrón y fichas de compradores", _UF, "ANUAL"),
    ("D", "D05", "Notas de entrega FIN-ING-06 del alimento", _UF, "ANUAL"),
    ("D", "D06", "Indicadores I-1 e I-2", _UF, "ANUAL"),
    ("E", "E01", "Fichas de proveedores", _FI, "PERMANENTE"),
    ("E", "E02", "Certificados RAU o de No Imponibilidad de proveedores", _FI, "ANUAL"),
    ("E", "E03", "Notas de compra", _FI, "ANUAL"),
    ("E", "E04", "FIN-EGR-03 y FIN-EGR-04", _FI, "ANUAL"),
    ("F", "F01", "Informes de costo de ciclo (FIN-TRA-06)", _FI, "ANUAL"),
    ("F", "F02", "Estados financieros", _FI, "ANUAL"),
    ("F", "F03", "Conciliaciones bancarias", _FI, "ANUAL"),
    ("F", "F04", "Hojas de tiempo (FIN-EGR-12)", _FI, "ANUAL"),
    ("G", "G01", "Actas del Consejo de Administración sobre RAU", _GG, "ANUAL"),
    ("G", "G02", "Informes del Consejo de Vigilancia", _GG, "ANUAL"),
    ("G", "G03", "Consulta tributaria y respuesta", _GG, "PERMANENTE"),
    ("G", "G04", "Carpeta de casos de fiscalización", _GG, "PERMANENTE"),
]


def _cargar_exp_rau(con: sqlite3.Connection) -> int:
    for fila in EXP_RAU_REQUISITOS:
        con.execute("INSERT INTO exp_rau_requisitos (carpeta, clave, documento, responsable, periodicidad) "
                    "VALUES (?,?,?,?,?)", fila)
    return len(EXP_RAU_REQUISITOS)


_F11 = "PET-RAU §3-4; MAN-ARC-01 §2.3 (propuesta Fase 11)"
PERMISOS_FASE11: list[tuple[str, str, str, str]] = [
    ("UT", "rau", "VER", _F11), ("UT", "rau", "CREAR", _F11),
    ("JF", "archivo", "VER", _F11), ("JF", "archivo", "CREAR", _F11), ("JF", "archivo", "EDITAR", _F11),
    ("GG", "archivo", "VER", _F11), ("GG", "archivo", "AUTORIZAR", _F11),
    ("CV", "archivo", "VER", _F11), ("CV", "archivo", "VERIFICAR", _F11),
    ("CA", "archivo", "VER", _F11), ("UT", "archivo", "VER", _F11),
]


def _cargar_permisos_fase11(con: sqlite3.Connection) -> int:
    for rol, recurso, accion, fuente in PERMISOS_FASE11:
        con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES (?,?,?,?)", (rol, recurso, accion, fuente))
    return len(PERMISOS_FASE11)


# ==========================================================================
# FASE 12: PRESUPUESTO MAESTRO, POA, CONSEJO DE VIGILANCIA E INDICADORES
# ==========================================================================
_MP = "Manual Presupuestario"


def construir_parametros_fase12() -> list[Param]:
    """Parámetros nuevos de la Fase 12. Ya existen: umbrales de ICL, RINC, ROA,
    ICA, PMF, CAC, DPF, margen operativo, SME (100 %/150 %), mortalidad,
    margen por canal, variación CAPEX (15 % y Bs 10.000), desvío del flujo
    (10 %), arqueos por año (2) y verificación física anual (12 meses)."""
    return [
        _p("presupuesto_ingresos_amarillo", "0.05", "DECIMAL", f"{_MP} E.1 (propuesto)",
           "Ingresos por ciclo: amarillo con una caída mayor a este porcentaje"),
        _p("presupuesto_ingresos_rojo", "0.10", "DECIMAL", f"{_MP} E.1 (propuesto)",
           "Ingresos por ciclo: rojo con una caída mayor a este porcentaje"),
        _p("presupuesto_costos_amarillo", "0.05", "DECIMAL", f"{_MP} E.1 (propuesto)",
           "Materia prima, CIF y gastos: amarillo sobre este exceso"),
        _p("presupuesto_costos_rojo", "0.10", "DECIMAL", f"{_MP} E.1 (propuesto)",
           "Materia prima, CIF y gastos: rojo sobre este exceso"),
        _p("presupuesto_modificacion_gg_max", "0.10", "DECIMAL",
           "Propuesta Fase 12 (POR VALIDAR): CI-FIN-09 no fija límite para partidas no CAPEX",
           "Modificación de una partida no CAPEX que la Gerencia General aprueba sola (sobre esto: CA)"),
        _p("presupuesto_informe_ciclo_dias", 10, "ENTERO", f"{_MP} §2.2 n.º 9; E.1 paso 1",
           "FIN-TES-12 dentro de estos días tras el cierre del ciclo"),
        _p("presupuesto_informe_semestre_dias", 31, "ENTERO", f"{_MP} §2.2 n.º 10 (enero y julio)",
           "FIN-TES-12 semestral/anual dentro de estos días tras el cierre del semestre"),
        _p("presupuesto_alerta_dias", 15, "ENTERO", "Propuesta Fase 12 (POR VALIDAR)",
           "Alerta previa a los hitos del calendario presupuestario (días)"),
        _p("presupuesto_cal_supuestos", "04-30", "TEXTO", f"{_MP} §2.2 n.º 1", "Validar supuestos (FIN-TES-09) hasta"),
        _p("presupuesto_cal_proyecto", "05-15", "TEXTO", f"{_MP} §2.2 n.º 2", "Proyecto de presupuesto (FIN-TES-10) hasta"),
        _p("presupuesto_cal_comite", "05-24", "TEXTO", f"{_MP} §2.2 n.º 3", "Revisión del Comité de Presupuesto hasta"),
        _p("presupuesto_cal_capex", "05-31", "TEXTO", f"{_MP} §2.2 n.º 4", "Plan CAPEX con ROA y SME antes de"),
        _p("presupuesto_cal_aprobacion", "06-15", "TEXTO", f"{_MP} §2.2 n.º 5", "Aprobación del Consejo de Administración hasta"),
        _p("presupuesto_cal_verificacion_cv", "06-25", "TEXTO", f"{_MP} §2.2 n.º 6", "Verificación del Consejo de Vigilancia hasta"),
        _p("presupuesto_cal_comunicacion", "07-01", "TEXTO", f"{_MP} §2.2 n.º 7", "Comunicación del presupuesto (CIR-TES-01)"),
        _p("poa_cumplimiento_meta", "0.90", "DECIMAL", "POA-001 §5.2", "Cumplimiento físico del POA: verde desde"),
        _p("poa_cumplimiento_rojo", "0.75", "DECIMAL", "POA-001 §5.2 (propuesta)", "Cumplimiento físico del POA: rojo bajo"),
        _p("cv_inventario_sorpresivo_anual", 1, "ENTERO", "MOF; Manual de Gestión Contable (inventarios)",
           "Inventarios sorpresivos mínimos del CV por ejercicio"),
        _p("cv_inf_cv_02_anual", 4, "ENTERO", "INF-CV-02 (trimestral); POA E4", "INF-CV-02 por ejercicio"),
        _p("cv_inf_cv_05_anual", 1, "ENTERO", "PR-FIN-05 paso 7", "INF-CV-05 por ejercicio"),
        _p("cv_inf_cv_06_anual", 2, "ENTERO", "CI-FIN-06 Art. 10 («periódica»; propuesta semestral, POR VALIDAR)",
           "INF-CV-06 por ejercicio"),
        _p("cv_inf_cv_09_anual", 4, "ENTERO", "PET-RAU §4.1", "INF-CV-09 trimestrales por ejercicio"),
        _p("cv07_partidas_min", 3, "ENTERO", "CI-FIN-18 Paso 2", "Partidas mínimas del FIN-INF-03 muestreadas por el CV"),
        _p("inf_cv_02_incumplimientos_reporte", 2, "ENTERO", "PR-FIN-02 Paso 8; INF-CV-02",
           "Ciclos consecutivos bajo el SME que obligan a reportar a la Asamblea"),
    ]


def _cargar_parametros_fase12(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase12())


def _cargar_presupuesto_maestro(con: sqlite3.Connection) -> int:
    from db.semillas_presupuesto import FUENTE_MAESTRO, filas_maestro, lineas_ciclo

    ahora = datetime.now().replace(microsecond=0).isoformat()
    n = 0
    for f in filas_maestro():
        con.execute(
            """INSERT INTO presupuesto_maestro (ejercicio, modulo, orden, clave, concepto, unidad, cantidad_mil, valor,
                   monto_cent, es_total, fuente, cargado_por, cargado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f["ejercicio"], f["modulo"], f["orden"], f["clave"], f["concepto"], f["unidad"], f["cantidad_mil"],
             f["valor"], f["monto_cent"], f["es_total"], FUENTE_MAESTRO, "SISTEMA", ahora))
        n += 1
    for f in lineas_ciclo():
        con.execute(
            """INSERT INTO presupuesto_lineas (ejercicio, ciclo, partida, concepto, unidad, cantidad_mil, precio,
                   monto_cent, fuente) VALUES (?,?,?,?,?,?,?,?,?)""",
            (f["ejercicio"], f["ciclo"], f["partida"], f["concepto"], f["unidad"], f["cantidad_mil"], f["precio"],
             f["monto_cent"], f["fuente"]))
        n += 1
    return n


def _cargar_poa(con: sqlite3.Connection) -> int:
    from db.semillas_presupuesto import POA_2026

    ahora = datetime.now().replace(microsecond=0).isoformat()
    for codigo, area, descripcion, indicador, responsable, trimestres in POA_2026:
        con.execute(
            """INSERT INTO poa_actividades (ejercicio, codigo, area, descripcion, indicador, responsable, trimestres,
                   fuente, registrado_por, registrado_en) VALUES (2026,?,?,?,?,?,?,?,?,?)""",
            (codigo, area, descripcion, indicador, responsable, trimestres, "POA-001 v1.0 §2", "SISTEMA", ahora))
    return len(POA_2026)


_F12 = "Manual Presupuestario P-08 y FIN-TES-11 (propuesta Fase 12)"
PERMISOS_FASE12: list[tuple[str, str, str, str]] = [
    ("GG", "presupuesto", "AUTORIZAR", _F12),
]


def _cargar_permisos_fase12(con: sqlite3.Connection) -> int:
    for rol, recurso, accion, fuente in PERMISOS_FASE12:
        con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES (?,?,?,?)", (rol, recurso, accion, fuente))
    return len(PERMISOS_FASE12)


# ==========================================================================
# FASE 13: RESPALDO 3-2-1, ADMINISTRACIÓN DE PARÁMETROS Y CARGA DEL POA
# ==========================================================================
_ARC = "Manual de Archivo Contable MAN-ARC-01 §5.2"


def construir_parametros_fase13() -> list[Param]:
    """Parámetros de la Fase 13. Ya existen: ``password_caducidad_dias`` (90),
    ``login_intentos_max`` (3), ``login_bloqueo_minutos`` (15),
    ``sesion_inactividad_minutos`` (30) y ``archivo_retencion_t10_anios`` (10),
    que fija la conservación de las copias de cierre anual."""
    return [
        _p("respaldo_copias_minimas", "3", "ENTERO", f"{_ARC} (regla 3-2-1)",
           "Copias de cada respaldo: equipo de trabajo, disco externo local y copia fuera de la granja"),
        _p("respaldo_medios_minimos", "2", "ENTERO", f"{_ARC} (regla 3-2-1)",
           "Tipos de medio distintos en que se guardan las copias"),
        _p("respaldo_fuera_sitio_minimas", "1", "ENTERO", f"{_ARC} (regla 3-2-1)",
           "Copias guardadas fuera del sitio (fuera de la granja o en la nube)"),
        _p("respaldo_prueba_restauracion_meses", "6", "ENTERO", f"{_ARC} (prueba semestral con acta)",
           "Meses máximos entre dos pruebas de restauración con acta verificada por el CV"),
        _p("respaldo_dia_semanal", "7", "ENTERO", f"{_ARC} (copia completa semanal; día POR VALIDAR)",
           "Día ISO de la copia completa semanal (1 = lunes ... 7 = domingo)"),
        _p("respaldo_retencion_diaria_dias", "30", "ENTERO", f"{_ARC} (plazo POR VALIDAR)",
           "Días que se conservan las copias diarias antes de depurarlas"),
        _p("respaldo_retencion_semanal_semanas", "8", "ENTERO", f"{_ARC} (plazo POR VALIDAR)",
           "Semanas que se conservan las copias completas semanales"),
        _p("respaldo_retencion_mensual_meses", "24", "ENTERO", f"{_ARC} (plazo POR VALIDAR)",
           "Meses que se conservan las copias de cierre mensual"),
        _p("poa_flujo_tolerancia_bs", "1", "DECIMAL", "POA 2026-2027 §3.2 («base caja, redondeada»)",
           "Diferencia de redondeo admitida al validar la cadena de saldos del flujo mensual del POA"),
    ]


def _cargar_parametros_fase13(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase13())


_F13 = f"{_ARC} §2.3 y §5.2; matriz de permisos (propuesta Fase 13)"
PERMISOS_FASE13: list[tuple[str, str, str, str]] = [
    ("GG", "respaldos", "VER", _F13), ("GG", "respaldos", "CREAR", _F13), ("GG", "respaldos", "AUTORIZAR", _F13),
    ("JF", "respaldos", "VER", _F13), ("JF", "respaldos", "CREAR", _F13),
    ("CV", "respaldos", "VER", _F13), ("CV", "respaldos", "VERIFICAR", _F13),
    ("CA", "respaldos", "VER", _F13),
    # La AG aprueba cambios de parámetros (matriz base): necesita verlos.
    ("AG", "parametros", "VER", _F13),
]


def _cargar_permisos_fase13(con: sqlite3.Connection) -> int:
    for rol, recurso, accion, fuente in PERMISOS_FASE13:
        con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES (?,?,?,?)", (rol, recurso, accion, fuente))
    return len(PERMISOS_FASE13)


# ==========================================================================
# IDENTIDAD INSTITUCIONAL DE LOS REPORTES OFICIALES (membrete de los PDF)
# ==========================================================================
def construir_parametros_identidad() -> list[Param]:
    """Datos del membrete. El NIT no figura en los documentos (MAN-006 Nota 1.1
    lo deja como «[XXXXXXXXX]»): se registra con el flujo GG → CA."""
    return [
        _p("entidad_nit", "Por registrar", "TEXTO", "MAN-006 Nota 1.1 (NIT por completar; POR VALIDAR)",
           "NIT de la cooperativa impreso en el membrete de los reportes oficiales"),
        _p("entidad_ubicacion", "Caranavi, La Paz – Bolivia", "TEXTO", "MAN-006 Nota 1.1 (domicilio en Caranavi)",
           "Ubicación impresa en el membrete de los reportes oficiales"),
    ]


def _cargar_parametros_identidad(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_identidad())


# Partida del personal eventual de faenado por ciclo (Manual Presupuestario
# D.4-1: Bs 4.500 por ciclo en 2026 y 2027 = 3.000 aves × Bs 1,50). Se registra
# sobre la cuenta 2.1.02.04 (Personal Eventual por Pagar) con sufijo 22.
PARTIDAS_EVENTUAL: list[tuple[int, dict[int, str], str]] = [
    (2026, {c: "4500" for c in range(1, 6)}, "Manual Presupuestario D.4-1 (2026, por ciclo)"),
    (2027, {c: "4500" for c in range(1, 6)}, "Manual Presupuestario D.4-1 (2027, por ciclo)"),
]


def _cargar_parametros_fase5(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase5())


def _cargar_partidas_eventual(con: sqlite3.Connection) -> int:
    n = 0
    for ejercicio, montos, fuente in PARTIDAS_EVENTUAL:
        for ciclo, monto in montos.items():
            con.execute(
                """INSERT INTO partidas_presupuesto (ejercicio, ciclo, cuenta_inventario, descripcion, monto_cent,
                       fuente) VALUES (?,?,?,?,?,?)""",
                (ejercicio, ciclo, "2.1.02.04", "Personal eventual de faenado (sufijo 22)", a_centavos(monto), fuente))
            n += 1
    return n


# ==========================================================================
# CARGA
# ==========================================================================
def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _auditar(con: sqlite3.Connection, tabla: str, registro_id: str | None, despues: Any,
             detalle: str, accion: str = "CARGA_INICIAL") -> None:
    """Escribe en la bitácora de auditoría (regla 5)."""
    con.execute(
        """INSERT INTO auditoria (usuario, rol, tabla, registro_id, accion, valores_antes,
                                  valores_despues, detalle)
           VALUES (?, NULL, ?, ?, ?, NULL, ?, ?)""",
        (USUARIO_SISTEMA, tabla, registro_id, accion,
         json.dumps(despues, ensure_ascii=False, default=str), detalle),
    )


def _cargar_cuentas(con: sqlite3.Connection) -> int:
    filas = construir_filas_cuentas()
    for f in filas:
        codigo = f["codigo"]
        if f["nivel"] == 1:
            clasif = "NA"
        else:
            clasif = clasificacion_de(codigo)
        con.execute(
            """INSERT INTO cuentas (codigo, nombre, nivel, grupo, clasificacion, naturaleza,
                                    es_movimiento, uso_desde_2027, padre_codigo, origen, observacion)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (codigo, f["nombre"], f["nivel"], f["grupo"], clasif, f["naturaleza"], f["mov"],
             1 if codigo in USO_DESDE_2027 else 0, f["padre"], f["origen"], f["obs"]),
        )
    return len(filas)


def _cargar_sufijos_y_centros(con: sqlite3.Connection) -> int:
    for cod, nombre, pt, cv, uso in CENTROS_COSTO:
        con.execute(
            "INSERT INTO centros_costo (codigo, nombre, cuenta_producto_term, cuenta_costo_ventas, uso_desde_2027)"
            " VALUES (?,?,?,?,?)", (cod, nombre, pt, cv, uso))
    for suf, nombre, elem, centros, c1, c2, uso, obs in SUFIJOS:
        con.execute(
            """INSERT INTO sufijos_costo (sufijo, nombre, elemento, cuenta_contrapartida,
                                          cuenta_contrapartida_2, uso_desde_2027, observacion)
               VALUES (?,?,?,?,?,?,?)""", (suf, nombre, elem, c1, c2, uso, obs))
        for centro in centros:
            con.execute("INSERT INTO sufijo_centro (sufijo, centro_costo) VALUES (?,?)", (suf, centro))
    return len(CENTROS_COSTO) + len(SUFIJOS)


def _cargar_parametros(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros())


def _cargar_calendario(con: sqlite3.Connection) -> int:
    n = 0
    for ej in EJERCICIOS_PLAN:
        con.execute("INSERT INTO ejercicios (ejercicio, fecha_inicio, fecha_fin) VALUES (?,?,?)",
                    (ej, date(ej, 7, 1).isoformat(), date(ej + 1, 6, 30).isoformat()))
        for numero in range(1, 13):
            mes = (6 + numero - 1) % 12 + 1
            anio = ej if mes >= 7 else ej + 1
            ultimo = calendar.monthrange(anio, mes)[1]
            con.execute(
                "INSERT INTO periodos (ejercicio, numero, fecha_inicio, fecha_fin) VALUES (?,?,?,?)",
                (ej, numero, date(anio, mes, 1).isoformat(), date(anio, mes, ultimo).isoformat()))
            n += 1
    return n + len(EJERCICIOS_PLAN)


def _cargar_socios(con: sqlite3.Connection) -> int:
    total_cert = sum(s[3] for s in SOCIOS)
    if len(SOCIOS) != 12 or total_cert != CERTIFICADOS_ACTA:
        raise DatosInvalidosError(
            f"Los socios no concuerdan con el acta: {len(SOCIOS)} socios, {total_cert} certificados "
            f"(esperado 12 y {CERTIFICADOS_ACTA}).")
    valor_cert = a_centavos(
        next(p[1] for p in construir_parametros() if p[0] == "valor_certificado_bs"))
    for numero, nombre, ci, cert, cargo in SOCIOS:
        con.execute(
            """INSERT INTO socios (numero, nombre_completo, ci, ci_expedido, certificados,
                                   aporte_cent, cargo_cooperativo, fecha_ingreso)
               VALUES (?,?,?,?,?,?,?,?)""",
            (numero, nombre, ci, "LP", cert, cert * valor_cert, cargo, FECHA_CONSTITUCION))
    return len(SOCIOS)


def _cargar_roles_permisos(con: sqlite3.Connection) -> int:
    for cod, nombre, desc, solo_lectura in ROLES:
        con.execute("INSERT INTO roles (codigo, nombre, descripcion, solo_lectura_transaccional)"
                    " VALUES (?,?,?,?)", (cod, nombre, desc, solo_lectura))
    n = len(ROLES)
    for rol, recursos in PERMISOS.items():
        for recurso, acciones in recursos.items():
            for accion in acciones:
                con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES (?,?,?,?)",
                            (rol, recurso, accion, FUENTE_PERMISOS))
                n += 1
    return n


def _cargar_apertura(con: sqlite3.Connection) -> int:
    periodo = con.execute(
        "SELECT id FROM periodos WHERE fecha_inicio <= ? AND fecha_fin >= ?",
        (FECHA_APERTURA, FECHA_APERTURA)).fetchone()
    if periodo is None:
        raise DatosInvalidosError("No existe el período del balance de apertura.")
    total_debe = sum(m for _, _, m in APERTURA_DEBE)
    if total_debe != APERTURA_HABER[1]:
        raise DatosInvalidosError(
            f"El balance de apertura no cuadra: debe {total_debe} y haber {APERTURA_HABER[1]}.")
    cur = con.execute(
        """INSERT INTO asientos (tipo, numero, fecha, periodo_id, glosa, origen, documento_ref,
                                 creado_por, creado_en)
           VALUES ('APERTURA', 1, ?, ?, ?, 'CARGA_INICIAL', ?, ?, ?)""",
        (FECHA_APERTURA, periodo["id"],
         "Balance de apertura al 1-jul-2026 (capital según Acta de Constitución)",
         "Acta de Constitución; Diagnóstico Financiero", USUARIO_SISTEMA, _ahora()))
    asiento_id = cur.lastrowid
    linea = 0
    for cuenta, nombre, monto in APERTURA_DEBE:
        linea += 1
        con.execute(
            "INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, debe_cent, glosa_linea)"
            " VALUES (?,?,?,?,?)", (asiento_id, linea, cuenta, a_centavos(monto), nombre))
    linea += 1
    con.execute(
        "INSERT INTO asiento_detalle (asiento_id, linea, cuenta_codigo, haber_cent, glosa_linea)"
        " VALUES (?,?,?,?,?)",
        (asiento_id, linea, APERTURA_HABER[0], a_centavos(APERTURA_HABER[1]),
         "Capital social: 5.343 certificados"))
    _auditar(con, "asientos", str(asiento_id),
             {"tipo": "APERTURA", "numero": 1, "fecha": FECHA_APERTURA,
              "total_debe_cent": a_centavos(total_debe), "total_haber_cent": a_centavos(APERTURA_HABER[1])},
             "Balance de apertura")
    return linea + 1


def _cargar_conciliacion(con: sqlite3.Connection) -> int:
    for orden, (tipo, tema, detalle, decision, fuente) in enumerate(CONCILIACION, start=1):
        con.execute(
            "INSERT INTO conciliacion_catalogo (orden, tipo, tema, detalle, decision, fuente)"
            " VALUES (?,?,?,?,?,?)", (orden, tipo, tema, detalle, decision, fuente))
    return len(CONCILIACION)


def _insertar_parametros(con: sqlite3.Connection, params: list[Param]) -> int:
    for clave, valor, tipo, desde, hasta, fuente, desc in params:
        convertir_valor_parametro(valor, tipo, clave)
        con.execute(
            """INSERT INTO parametros (clave, valor, tipo, ejercicio_desde, ejercicio_hasta,
                                       fuente_documental, descripcion) VALUES (?,?,?,?,?,?,?)""",
            (clave, valor, tipo, desde, hasta, fuente, desc))
    return len(params)


def _cargar_parametros_fase2(con: sqlite3.Connection) -> int:
    return _insertar_parametros(con, construir_parametros_fase2())


def _cargar_conciliacion_fase2(con: sqlite3.Connection) -> int:
    orden = int(con.execute("SELECT COALESCE(MAX(orden), 0) FROM conciliacion_catalogo").fetchone()[0])
    for tipo, tema, detalle, decision, fuente in CONCILIACION_FASE2:
        orden += 1
        con.execute(
            "INSERT INTO conciliacion_catalogo (orden, tipo, tema, detalle, decision, fuente)"
            " VALUES (?,?,?,?,?,?)", (orden, tipo, tema, detalle, decision, fuente))
    for num, extra in FUENTES_ADICIONALES.items():
        con.execute("UPDATE conciliacion_catalogo SET fuente = fuente || '; ' || ? WHERE orden = ?",
                    (extra, num))
    return len(CONCILIACION_FASE2) + len(FUENTES_ADICIONALES)


# Orden obligatorio por dependencias de claves foráneas.
_BLOQUES = (
    ("catalogo_cuentas", _cargar_cuentas, "cuentas"),
    ("sufijos_centros", _cargar_sufijos_y_centros, "sufijos_costo/centros_costo"),
    ("parametros", _cargar_parametros, "parametros"),
    ("calendario", _cargar_calendario, "ejercicios/periodos"),
    ("socios", _cargar_socios, "socios"),
    ("roles_permisos", _cargar_roles_permisos, "roles/permisos"),
    ("apertura", _cargar_apertura, "asientos"),
    ("conciliacion", _cargar_conciliacion, "conciliacion_catalogo"),
    ("parametros_fase2", _cargar_parametros_fase2, "parametros"),
    ("conciliacion_fase2", _cargar_conciliacion_fase2, "conciliacion_catalogo"),
    ("parametros_fase3", _cargar_parametros_fase3, "parametros"),
    ("conciliacion_fase3", _cargar_conciliacion_fase3, "conciliacion_catalogo"),
    ("parametros_fase4", _cargar_parametros_fase4, "parametros"),
    ("insumos_fase4", _cargar_insumos, "insumos"),
    ("partidas_compras_fase4", _cargar_partidas_compras, "partidas_presupuesto"),
    ("parametros_fase5", _cargar_parametros_fase5, "parametros"),
    ("partidas_eventual_fase5", _cargar_partidas_eventual, "partidas_presupuesto"),
    ("parametros_fase6", _cargar_parametros_fase6, "parametros"),
    ("parametros_fase7", _cargar_parametros_fase7, "parametros"),
    ("presupuesto_costos_fase7", _cargar_presupuesto_costos, "presupuesto_costos"),
    ("parametros_fase8", _cargar_parametros_fase8, "parametros"),
    ("capex_d5_fase8", _cargar_capex_d5, "capex_referencia"),
    ("permisos_fase8", _cargar_permisos_fase8, "permisos"),
    ("parametros_fase9", _cargar_parametros_fase9, "parametros"),
    ("parametros_fase10", _cargar_parametros_fase10, "parametros"),
    ("parametros_fase11", _cargar_parametros_fase11, "parametros"),
    ("retencion_fase11", _cargar_retencion, "retencion_documental"),
    ("exp_rau_fase11", _cargar_exp_rau, "exp_rau_requisitos"),
    ("permisos_fase11", _cargar_permisos_fase11, "permisos"),
    ("parametros_fase12", _cargar_parametros_fase12, "parametros"),
    ("presupuesto_maestro_fase12", _cargar_presupuesto_maestro, "presupuesto_maestro/presupuesto_lineas"),
    ("poa_fase12", _cargar_poa, "poa_actividades"),
    ("permisos_fase12", _cargar_permisos_fase12, "permisos"),
    ("parametros_fase13", _cargar_parametros_fase13, "parametros"),
    ("permisos_fase13", _cargar_permisos_fase13, "permisos"),
    ("parametros_identidad", _cargar_parametros_identidad, "parametros"),
)


def cargar_semillas(con: sqlite3.Connection) -> dict[str, int]:
    """Carga los bloques de semillas pendientes en UNA transacción.

    Es idempotente: los bloques ya aplicados se omiten. Ante cualquier error se
    hace rollback completo y se relanza la excepción.

    Returns:
        ``{bloque: registros}`` de los bloques aplicados en esta llamada.
    """
    aplicadas: dict[str, int] = {}
    try:
        for clave, funcion, tabla in _BLOQUES:
            ya = con.execute("SELECT 1 FROM semillas_aplicadas WHERE clave = ?", (clave,)).fetchone()
            if ya:
                continue
            registros = funcion(con)
            con.execute("INSERT INTO semillas_aplicadas (clave, aplicada_en, registros) VALUES (?,?,?)",
                        (clave, _ahora(), registros))
            _auditar(con, tabla, None, {"bloque": clave, "registros": registros},
                     f"Carga inicial del bloque '{clave}'")
            aplicadas[clave] = registros
        con.commit()
    except Exception:
        con.rollback()
        log.exception("Falló la carga de semillas; se hizo rollback")
        raise
    if aplicadas:
        log.info("Semillas aplicadas: %s", aplicadas)
    return aplicadas


# ==========================================================================
# REPORTE DE CONCILIACIÓN
# ==========================================================================
def generar_reporte_conciliacion(con: sqlite3.Connection) -> str:
    """Devuelve el reporte de conciliación del catálogo en Markdown, leyendo la BD."""
    filas = con.execute(
        "SELECT orden, tipo, tema, detalle, decision, fuente FROM conciliacion_catalogo ORDER BY orden"
    ).fetchall()
    resumen = {r["codigo"]: r["n"] for r in con.execute(
        "SELECT 'n4' AS codigo, COUNT(*) AS n FROM cuentas WHERE nivel = 4 AND origen = 'PLAN_V3' "
        "UNION ALL SELECT 'comp', COUNT(*) FROM cuentas WHERE origen = 'DESCRIPCION' "
        "UNION ALL SELECT 'suf', COUNT(*) FROM sufijos_costo")}
    lineas = [
        "# Reporte de conciliación del catálogo y de las discrepancias entre documentos",
        "",
        f"*{config.NOMBRE_ENTIDAD} · generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}*",
        "",
        f"- Cuentas de movimiento del Plan v3: **{resumen['n4']}** (más {resumen['comp']} complemento de la Descripción de Cuentas).",
        f"- Sufijos de costo cargados: **{resumen['suf']}** (códigos 01 a 41).",
        f"- Discrepancias registradas: **{len(filas)}**.",
        "",
    ]
    titulos = {"CATALOGO": "A. Catálogo de cuentas y sufijos",
               "APERTURA": "B. Balance de apertura y capital",
               "NORMATIVA": "C. Normas, parámetros y políticas"}
    for tipo, titulo in titulos.items():
        grupo = [f for f in filas if f["tipo"] == tipo]
        if not grupo:
            continue
        lineas += [f"## {titulo}", "", "| # | Tema | Inconsistencia | Decisión adoptada | Fuentes |",
                   "|---|------|----------------|-------------------|---------|"]
        for f in grupo:
            cel = lambda t: str(t).replace("|", "\\|").replace("\n", " ")  # noqa: E731
            lineas.append(f"| {f['orden']} | {cel(f['tema'])} | {cel(f['detalle'])} | "
                          f"{cel(f['decision'])} | {cel(f['fuente'])} |")
        lineas.append("")
    return "\n".join(lineas)


def escribir_reporte_conciliacion(con: sqlite3.Connection, ruta: str | Path | None = None) -> Path:
    """Escribe el reporte en ``archivo/reporte_conciliacion_catalogo.md`` (o ``ruta``)."""
    destino = Path(ruta) if ruta else config.ARCHIVO_DIR / "reporte_conciliacion_catalogo.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(generar_reporte_conciliacion(con), encoding="utf-8")
    return destino
