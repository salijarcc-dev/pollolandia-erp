"""Datos del Presupuesto Maestro 2026-2030 y del POA 2026-27 (Fase 12).

Transcripción de las tablas del Manual del Sistema Presupuestario
(SGC-MAN-ADM-001: módulos A, B, C, D y E.3) y de la matriz de programación del
POA-001. Las cifras se cargan en ``presupuesto_maestro`` y, de ellas, se
derivan las metas por ciclo de ``presupuesto_lineas`` (FIN-TES-10, tablas D.1,
D.2 y D.4). Montos en Bs a valores nominales.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterator

EJERCICIOS: tuple[int, ...] = (2026, 2027, 2028, 2029, 2030)
FUENTE_MAESTRO = "Manual del Sistema Presupuestario (Presupuesto Maestro 2026-2030)"
CICLOS = 5
_ = None


def _d(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# Cada fila: (clave, concepto, unidad, cantidades, valor, montos, es_total)
# cantidades / montos: lista de 5 valores (2026..2030) o None; valor: lista o None.
MAESTRO: dict[str, list[tuple[Any, ...]]] = {
    "VENTAS": [
        ("ING_CARNE", "Ventas de carne de pollo", "kg", [39000, 39000, 52000, 65000, 78000],
         [23.50] * 5, [916500, 916500, 1222000, 1527500, 1833000], 0),
        ("ING_ALIMENTO", "Ventas de alimento balanceado excedente", "qq", [_, 585, 870, 1150, 1695],
         [_, 180, 180, 180, 180], [0, 105300, 156600, 207000, 305100], 0),
        ("ING_OPERATIVOS", "INGRESOS OPERATIVOS", None, None, None,
         [916500, 1021800, 1378600, 1734500, 2138100], 1),
        ("ING_GALLINAZA", "Ventas de gallinaza (ingreso no operativo 5.2.01.04)", "carga", [15, 15, 20, 25, 30],
         [500] * 5, [7500, 7500, 10000, 12500, 15000], 0),
        ("TOTAL_INGRESOS", "TOTAL INGRESOS ANUALES", None, None, None,
         [924000, 1029300, 1388600, 1747000, 2153100], 1),
    ],
    "PRODUCCION": [
        ("AVES_CICLO", "Aves por ciclo productivo", "ave", [3000, 3000, 4000, 5000, 6000], _, _, 0),
        ("MORTALIDAD_CICLO", "Mortalidad esperada (igual o menor a 3 %)", "ave", [90, 90, 120, 150, 180], _, _, 0),
        ("FAENADAS_CICLO", "Aves faenadas por ciclo", "ave", [2910, 2910, 3880, 4850, 5820], _, _, 0),
        ("RENDIMIENTO", "Rendimiento canal (kg/ave)", "kg/ave", _, [2.6] * 5, _, 0),
        ("KG_CICLO", "Kg de carne por ciclo (según Excel)", "kg", [7800, 7800, 10400, 13000, 15600], _, _, 0),
        ("KG_ANIO", "Kg de carne por año (5 ciclos)", "kg", [39000, 39000, 52000, 65000, 78000], _, _, 0),
        ("QQ_SEMANA", "qq de alimento producido por semana", "qq", [_, 40, 55, 70, 90], _, _, 0),
        ("QQ_CICLO", "qq de alimento por ciclo (73 días)", "qq", [_, 417, 574, 730, 939], _, _, 0),
        ("QQ_CONSUMO_CICLO", "qq de consumo interno por ciclo (0,10 qq por ave)", "qq",
         [300, 300, 400, 500, 600], _, _, 0),
        ("QQ_EXCEDENTE_CICLO", "qq de excedente para venta por ciclo", "qq", [_, 117, 174, 230, 339], _, _, 0),
    ],
    "MP": [
        ("POLLITOS", "Pollitos BB (Bs 9 por ave)", None, _, _, [135000, 135000, 180000, 225000, 270000], 0),
        ("ALIMENTO_COMPRADO", "Alimento comprado (2026)", None, _, _, [277500, 0, 0, 0, 0], 0),
        ("ALIMENTO_PRODUCIDO", "Alimento producido (desde 2027)", None, _, _, [0, 207077, 272761, 339431, 414710], 0),
        ("VETERINARIOS", "Productos veterinarios", None, _, _, [29475, 27375, 36500, 45625, 54750], 0),
        ("LIMPIEZA", "Insumos de limpieza", None, _, _, [3900, 3150, 4200, 5250, 6300], 0),
        ("CRIANZA", "Insumos de crianza", None, _, _, [14550, 11550, 15400, 19250, 23100], 0),
        ("BOLSAS", "Bolsas personalizadas", None, _, _, [19200, 18000, 24000, 30000, 36000], 0),
        ("TOTAL", "TOTAL MP + insumos", None, _, _, [479625, 402152, 532861, 664556, 804860], 1),
    ],
    "MP_ALIMENTO": [
        ("MAIZ", "Maíz amarillo (Bs 95,12/qq)", None, _, _, [0, 98925, 136021, 173118, 222580], 0),
        ("SOYA", "Torta de soya (Bs 149,84/qq)", None, _, _, [0, 87654, 120524, 153394, 197221], 0),
        ("AFRECHO", "Afrecho de trigo (Bs 167,23/qq)", None, _, _, [0, 9197, 12646, 16095, 20694], 0),
        ("NUCLEO", "Núcleo vitamínico (Bs 18/kg)", None, _, _, [0, 49724, 68371, 87017, 111879], 0),
        ("ACEITE", "Aceite vegetal (Bs 12,01/litro)", None, _, _, [0, 14233, 19570, 24907, 32024], 0),
        ("TOTAL", "TOTAL MP alimento", None, _, _, [0, 259733, 357132, 454532, 584398], 1),
    ],
    "MOD": [
        ("SALARIO", "Salario Encargado de Cría", None, _, _, [39600, 31185, 72310, 71628, 69193], 0),
        ("AGUINALDO", "Aguinaldo (1/12 mensual)", None, _, _, [3300, 2599, 6026, 5969, 5766], 0),
        ("EVENTUAL", "Personal eventual (Bs 1,5 por ave)", None, _, _, [22500, 22500, 30000, 37500, 45000], 0),
        ("TOTAL", "TOTAL MOD carne", None, _, _, [65400, 56284, 108336, 115097, 119959], 1),
    ],
    "MOD_ALIMENTO": [
        ("SALARIO", "Salario (porción del Encargado asignada a alimento)", None, _, _,
         [0, 10395, 15008, 20056, 27075], 0),
        ("AGUINALDO", "Aguinaldo (1/12)", None, _, _, [0, 866, 1251, 1671, 2256], 0),
        ("TOTAL", "TOTAL MOD alimento", None, _, _, [0, 11261, 16258, 21727, 29332], 1),
    ],
    "CIF": [
        ("DEP_GALPONES", "Depreciación galpones", None, _, _, [5250, 5250, 7000, 8750, 10500], 0),
        ("DEP_EQUIPOS", "Depreciación equipos de producción", None, _, _, [4931, 4931, 6575, 8219, 9863], 0),
        ("DEP_HERRAMIENTAS", "Depreciación herramientas", None, _, _, [810, 810, 1620, 1620, 1620], 0),
        ("DEP_ALAMBRADOS", "Depreciación alambrados", None, _, _, [1500] * 5, 0),
        ("MANTENIMIENTO", "Mantenimiento y limpieza", None, _, _, [7500, 7500, 10000, 12500, 15000], 0),
        ("APORTES", "Aportes patronales (17,21 %)", None, _, _, [6815, 5367, 12445, 12327, 11908], 0),
        ("INDEMNIZACION", "Indemnización (8,33 %)", None, _, _, [3300, 2599, 6026, 5969, 5766], 0),
        ("ENERGIA", "Energía eléctrica y agua", None, _, _, [1500, 1500, 2000, 2500, 3000], 0),
        ("TOTAL", "TOTAL CIF carne", None, _, _, [31606, 29457, 47165, 53385, 59157], 1),
    ],
    "CIF_ALIMENTO": [
        ("DEP_MAQUINARIA", "Depreciación maquinaria de alimento", None, _, _, [0, 9625, 9625, 9625, 19250], 0),
        ("DEP_AMBIENTE", "Depreciación ambiente de preparación", None, _, _, [0, 1000, 1000, 1000, 2000], 0),
        ("MANTENIMIENTO", "Mantenimiento y limpieza", None, _, _, [0, 750, 750, 750, 1500], 0),
        ("APORTES", "Aportes patronales (17,21 %) sobre MOD", None, _, _, [0, 1789, 2583, 3452, 4660], 0),
        ("INDEMNIZACION", "Indemnización (8,33 %) sobre MOD", None, _, _, [0, 866, 1251, 1671, 2256], 0),
        ("ENERGIA", "Energía eléctrica y agua", None, _, _, [0, 2813, 2813, 2813, 5625], 0),
        ("TOTAL", "TOTAL CIF alimento", None, _, _, [0, 16843, 18021, 19310, 35291], 1),
    ],
    "COSTO": [
        ("COSTO_CARNE", "TOTAL COSTO PRODUCCIÓN CARNE", "kg", [39000, 39000, 52000, 65000, 78000],
         [14.79, 12.51, 13.24, 12.82, 12.62], [576631, 487892, 688362, 833038, 983975], 1),
        ("COSTO_ALIMENTO", "TOTAL COSTO DE PRODUCCIÓN ALIMENTO", "qq", [_, 2086, 2868, 3650, 4693],
         [_, 138.05, 136.38, 135.77, 138.24], [0, 287837, 391412, 495570, 649021], 1),
        ("ALIMENTO_TRANSFERIDO", "Transferido al costo de la carne (consumo propio)", None, _, _,
         [0, 207077, 272761, 339431, 414710], 0),
        ("ALIMENTO_EXCEDENTE", "Costo del excedente para la venta", None, _, _, [0, 80760, 118651, 156138, 234311], 0),
        ("SAQUILLOS", "(+) Saquillos de yute (Bs 10 por qq vendido)", None, _, _, [0, 5850, 8700, 11500, 16950], 0),
        ("CV_ALIMENTO", "Costo de ventas del alimento excedente", None, _, _, [0, 86610, 127351, 167638, 251261], 1),
    ],
    "GASTOS": [
        ("SUELDOS", "Sueldos Gerente + Jefe de área financiera", None, _, _, [87600, 91980, 96579, 101408, 106478], 0),
        ("APORTES", "Aportes patronales (17,21 %)", None, _, _, [15076, 15830, 16621, 17452, 18325], 0),
        ("AGUINALDO", "Aguinaldo (8,33 %)", None, _, _, [7300, 7665, 8048, 8451, 8873], 0),
        ("INDEMNIZACION", "Indemnización (8,33 %)", None, _, _, [7300, 7665, 8048, 8451, 8873], 0),
        ("DEP_MUEBLES", "Depreciación muebles y enseres", None, _, _, [550] * 5, 0),
        ("DEP_COMPUTO", "Depreciación equipos de cómputo", None, _, _, [1100, 1100, 1100, 1100, 0], 0),
        ("AMORT_CONSTITUCION", "Amortización costos de constitución", None, _, _, [1675, 1675, 1675, 1675, 0], 0),
        ("AMORT_SOFTWARE", "Amortización software contable", None, _, _, [1400] * 5, 0),
        ("IBNORCA", "Auditoría y certificación IBNORCA", None, _, _, [0, 0, 0, 0, 3800], 0),
        ("RAU", "Impuesto RAU (Bs 3,03 × 8 ha, POR VALIDAR)", None, _, _, [24.24] * 5, 0),
        ("SENASAG", "Certificación SENASAG (quinquenal)", None, _, _, [500, 0, 0, 0, 0], 0),
        ("TOTAL_ADM", "GASTOS DE ADMINISTRACIÓN", None, _, _, "SUMA:SUELDOS..SENASAG", 1),
        ("FLETE", "Flete de venta (Bs 970 por lote de 1.000 aves)", None, _, _, [14550, 14550, 19400, 24250, 29100], 0),
        ("TOTAL_VENTA", "GASTOS DE VENTA", None, _, _, "SUMA:FLETE..FLETE", 1),
        ("TOTAL", "TOTAL GASTOS OPERATIVOS", None, _, _, "SUMA:TOTAL_ADM+TOTAL_VENTA", 1),
    ],
    "CAPEX": [
        ("MAQUINARIA", "Maquinaria para alimento balanceado", None, _, _, [77000, 0, 0, 77000, 0], 0),
        ("AMBIENTE", "Construcción de ambiente de preparación", None, _, _, [20000, 0, 0, 20000, 0], 0),
        ("GALPON", "Construcción de 1 galpón adicional", None, _, _, [0, 35000, 35000, 35000, 0], 0),
        ("EQUIPO", "Compra de equipo adicional para 1.000 aves", None, _, _, [0, 13150, 13150, 13150, 0], 0),
        ("HERRAMIENTAS", "Compra de herramientas adicionales para 3.000 aves", None, _, _, [0, 3240, 0, 3240, 0], 0),
        ("TOTAL", "TOTAL REINVERSIONES", None, _, _, [97000, 51390, 48150, 148390, 0], 1),
    ],
    "FLUJO": [
        ("SALDO_INICIAL", "Saldo inicial de efectivo (banco y caja)", None, _, _,
         [138010, 291669, 446708, 618312, 723564], 0),
        ("COBROS", "(+) Cobros por ventas", None, _, _, [924000, 1029300, 1388600, 1747000, 2153100], 0),
        ("PAGOS_OPERACION", "(–) Pagos de operación (insumos, planillas, gastos, RAU)", None, _, _,
         [673341, 677114, 916831, 1112968, 1347863], 0),
        ("FLUJO_OPERACION", "Flujo neto de operación", None, _, _, [250659, 352186, 471769, 634032, 805237], 1),
        ("CAPEX", "(–) Inversión en activos fijos (CAPEX)", None, _, _, [97000, 51390, 48150, 148390, 0], 0),
        ("EXCEDENTES", "(–) Pago de excedentes del ejercicio anterior a socios", None, _, _,
         [0, 145756, 252015, 380390, 434788], 0),
        ("VARIACION", "Variación neta del efectivo", None, _, _, [153659, 155039, 171604, 105252, 370449], 1),
        ("SALDO_FINAL", "SALDO FINAL DE EFECTIVO", None, _, _, [291669, 446708, 618312, 723564, 1094012], 1),
    ],
    "RESULTADOS": [
        ("ING_CARNE", "Ventas de carne de pollo", None, _, _, [916500, 916500, 1222000, 1527500, 1833000], 0),
        ("ING_ALIMENTO", "Ventas de alimento balanceado excedente", None, _, _, [0, 105300, 156600, 207000, 305100], 0),
        ("ING_OPERATIVOS", "INGRESOS OPERATIVOS", None, _, _, [916500, 1021800, 1378600, 1734500, 2138100], 1),
        ("CV_CARNE", "(–) Costo de ventas — carne de pollo", None, _, _, [576631, 487892, 688362, 833038, 983975], 0),
        ("CV_ALIMENTO", "(–) Costo de ventas — alimento excedente", None, _, _, [0, 86610, 127351, 167638, 251261], 0),
        ("MARGEN_BRUTO", "MARGEN BRUTO", None, _, _, [339869, 447298, 562887, 733823, 902864], 1),
        ("GASTOS_ADM", "(–) Gastos de administración", None, _, _, [122525, 127889, 134046, 140511, 148324], 0),
        ("GASTOS_VENTA", "(–) Gastos de venta", None, _, _, [14550, 14550, 19400, 24250, 29100], 0),
        ("EBIT", "EXCEDENTE OPERATIVO (EBIT)", None, _, _, [202793, 304859, 409441, 569063, 725440], 1),
        ("GALLINAZA", "(+) Ingresos no operativos (gallinaza)", None, _, _, [7500, 7500, 10000, 12500, 15000], 0),
        ("EXCEDENTE_NETO", "EXCEDENTE NETO DEL EJERCICIO", None, _, _, [210293, 312359, 419441, 581563, 740440], 1),
    ],
    "BALANCE": [
        ("EFECTIVO", "Efectivo (banco y caja)", None, _, _, [291669, 446708, 618312, 723564, 1094012], 0),
        ("ACTIVO_CORRIENTE", "ACTIVO CORRIENTE", None, _, _, [291669, 446708, 618312, 723564, 1094012], 1),
        ("PPE", "Propiedad, planta y equipo e intangibles, netos", None, _, _,
         [476074, 499623, 515728, 628679, 581996], 0),
        ("ACTIVO_NO_CORRIENTE", "ACTIVO NO CORRIENTE", None, _, _, [476074, 499623, 515728, 628679, 581996], 1),
        ("TOTAL_ACTIVO", "TOTAL ACTIVO", None, _, _, [767743, 946331, 1134039, 1352243, 1676009], 1),
        ("EXCEDENTES_POR_PAGAR", "Excedentes por pagar a socios", None, _, _,
         [145756, 252015, 380390, 434788, 805237], 0),
        ("OBLIGACIONES", "Obligaciones laborales y por servicios", None, _, _, [12549, 13405, 18363, 19303, 20522], 0),
        ("PASIVO_CORRIENTE", "PASIVO CORRIENTE", None, _, _, [158306, 265420, 398753, 454091, 825759], 1),
        ("INDEMNIZACION", "Provisión para indemnización (no corriente)", None, _, _,
         [10600, 21730, 37055, 53146, 70041], 0),
        ("TOTAL_PASIVO", "TOTAL PASIVO", None, _, _, [168906, 287150, 435808, 507237, 895800], 1),
        ("CAPITAL", "Capital cooperativo (certificados de aportación)", None, _, _, [534300] * 5, 0),
        ("EXCEDENTES_ACUMULADOS", "Excedentes acumulados", None, _, _, [64537, 124881, 163931, 310705, 245908], 0),
        ("TOTAL_PATRIMONIO", "TOTAL PATRIMONIO", None, _, _, [598837, 659181, 698231, 845005, 780208], 1),
        ("TOTAL_PASIVO_PATRIMONIO", "TOTAL PASIVO + PATRIMONIO", None, _, _,
         [767743, 946331, 1134039, 1352243, 1676009], 1),
    ],
    "D6": [
        ("CONSUMO_QQ", "Consumo interno de alimento (qq por año)", "qq", [_, 1500, 2000, 2500, 3000], _, _, 0),
        ("COSTO_COMPRA", "Costo si se comprara a Bs 185 por qq", None, _, _, [0, 277500, 370000, 462500, 555000], 0),
        ("COSTO_PROPIO", "Costo del alimento propio transferido a la carne", None, _, _,
         [0, 207077, 272761, 339431, 414710], 0),
        ("UNITARIO_PROPIO", "Costo unitario propio (Bs por qq)", "Bs/qq", _, [_, 138.05, 136.38, 135.77, 138.24], _, 0),
        ("AHORRO", "AHORRO POR CONSUMO PROPIO", None, _, _, [0, 70423, 97239, 123069, 140290], 1),
        ("AHORRO_PCT", "Ahorro sobre el costo de compra", "%", _, [_, 0.254, 0.263, 0.266, 0.253], _, 0),
        ("MARGEN_EXCEDENTE", "Margen del excedente vendido: (Bs 180 – costo con saquillo) × qq", None, _, _,
         [0, 18690, 29249, 39362, 53839], 0),
        ("BENEFICIO_TOTAL", "Beneficio total de la planta (ahorro + margen del excedente)", None, _, _,
         [0, 89113, 126488, 162430, 194129], 1),
    ],
    "INDICADORES": [
        ("ICL", "ICL (costo de ventas ÷ ventas totales)", "%", _, [0.624, 0.558, 0.587, 0.573, 0.574], _, 0),
        ("RINC", "RINC (ingresos no cárnicos)", "%", _, [_, 0.110, 0.120, 0.126, 0.149], _, 0),
        ("ROA", "ROA (excedente neto ÷ activos totales)", "%", _, [0.274, 0.330, 0.370, 0.430, 0.442], _, 0),
        ("MARGEN_OPERATIVO", "Margen operativo (EBIT ÷ ingresos operativos)", "%", _,
         [0.221, 0.298, 0.297, 0.328, 0.339], _, 0),
        ("SME", "Cumplimiento del SME", "%", _, [1.01, 1.41, 1.43, 1.47, 1.49], _, 0),
    ],
}


def _cent(valor: Any) -> int:
    return int((Decimal(str(valor)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _resolver_montos(modulo: str, filas: list[tuple[Any, ...]]) -> dict[str, list[int]]:
    """Montos en centavos por clave, resolviendo las filas «SUMA:» (totales
    exactos con decimales, p. ej. gastos de administración con el RAU de Bs 24,24)."""
    claves = [f[0] for f in filas]
    resultado: dict[str, list[int]] = {}
    for f in filas:
        clave, montos = f[0], f[5]
        if montos is None:
            continue
        if isinstance(montos, str):
            regla = montos.split(":", 1)[1]
            if "+" in regla:
                partes = regla.split("+")
            else:
                desde, hasta = regla.split("..")
                partes = claves[claves.index(desde):claves.index(hasta) + 1]
            resultado[clave] = [sum(resultado[p][i] for p in partes) for i in range(len(EJERCICIOS))]
        else:
            resultado[clave] = [_cent(m) if m is not None else 0 for m in montos]
    return resultado


def filas_maestro() -> Iterator[dict[str, Any]]:
    """Filas de ``presupuesto_maestro`` para 2026-2030."""
    for modulo, filas in MAESTRO.items():
        montos = _resolver_montos(modulo, filas)
        for orden, f in enumerate(filas, start=1):
            clave, concepto, unidad, cantidades, valores, _m, es_total = f
            for i, ejercicio in enumerate(EJERCICIOS):
                cantidad = cantidades[i] if cantidades else None
                valor = valores[i] if valores else None
                yield {"ejercicio": ejercicio, "modulo": modulo, "orden": orden, "clave": clave,
                       "concepto": concepto, "unidad": unidad,
                       "cantidad_mil": None if cantidad is None else int(Decimal(str(cantidad)) * 1000),
                       "valor": None if valor is None else str(_d(valor)),
                       "monto_cent": montos.get(clave, [0] * 5)[i], "es_total": es_total}


def valor_maestro(modulo: str, clave: str, ejercicio: int, campo: str = "monto") -> Any:
    """Valor de la tabla del manual (``monto`` en centavos, ``cantidad`` o ``valor``)."""
    i = EJERCICIOS.index(ejercicio)
    filas = MAESTRO[modulo]
    if campo == "monto":
        return _resolver_montos(modulo, filas).get(clave, [0] * 5)[i]
    for f in filas:
        if f[0] == clave:
            datos = f[3] if campo == "cantidad" else f[4]
            return None if not datos else _d(datos[i])
    raise KeyError(f"{modulo}.{clave}")


def _repartir(total_cent: int, partes: int = CICLOS) -> list[int]:
    """Reparte en partes iguales; los centavos sobrantes van al último ciclo."""
    base = total_cent // partes
    return [base] * (partes - 1) + [total_cent - base * (partes - 1)]


def _precio(monto_cent: int, cantidad_mil: int) -> str:
    return str((Decimal(monto_cent) / 100 / (Decimal(cantidad_mil) / 1000)).quantize(Decimal("0.0001"),
                                                                                     rounding=ROUND_HALF_UP))


def lineas_ciclo() -> Iterator[dict[str, Any]]:
    """Metas por ciclo (FIN-TES-10) derivadas del maestro: D.1/D.2 (ingresos)
    y D.4 (costos). Los gastos de operación y el CAPEX se asignan como en el
    manual: gastos en 5 partes iguales; CAPEX en el ciclo 5 (sección 4.2)."""
    fuente = "Manual Presupuestario D.1, D.2 y D.4 (FIN-TES-10)"
    for e in EJERCICIOS:
        m = lambda mod, clave: valor_maestro(mod, clave, e)          # noqa: E731
        q = lambda mod, clave: valor_maestro(mod, clave, e, "cantidad")  # noqa: E731
        v = lambda mod, clave: valor_maestro(mod, clave, e, "valor")  # noqa: E731
        aves = int(q("PRODUCCION", "AVES_CICLO"))
        partidas: list[tuple[str, str, str | None, Any, list[int], Any]] = []
        # Ingresos
        partidas.append(("ING_CARNE", "Carne de pollo (kg)", "kg", q("PRODUCCION", "KG_CICLO"),
                         _repartir(m("VENTAS", "ING_CARNE")), v("VENTAS", "ING_CARNE")))
        if m("VENTAS", "ING_ALIMENTO"):
            partidas.append(("ING_ALIMENTO", "Alimento excedente (qq)", "qq", q("PRODUCCION", "QQ_EXCEDENTE_CICLO"),
                             _repartir(m("VENTAS", "ING_ALIMENTO")), v("VENTAS", "ING_ALIMENTO")))
        partidas.append(("ING_GALLINAZA", "Gallinaza (cargas)", "carga", q("VENTAS", "ING_GALLINAZA") / CICLOS,
                         _repartir(m("VENTAS", "ING_GALLINAZA")), v("VENTAS", "ING_GALLINAZA")))
        # Materia prima de la carne
        partidas.append(("MP_POLLITOS", "Pollitos BB", "ave", aves, _repartir(m("MP", "POLLITOS")), None))
        alimento = m("MP", "ALIMENTO_COMPRADO") + m("MP", "ALIMENTO_PRODUCIDO")
        concepto_alimento = "Alimento comprado" if m("MP", "ALIMENTO_COMPRADO") else "Alimento producido (sufijo 11)"
        partidas.append(("MP_ALIMENTO", concepto_alimento, "qq", q("PRODUCCION", "QQ_CONSUMO_CICLO"),
                         _repartir(alimento), None))
        otros = [m("MP", k) for k in ("VETERINARIOS", "LIMPIEZA", "CRIANZA")]
        bolsas = m("MP", "BOLSAS")
        if e == 2026:
            # D.4-1: bolsas Bs 4.800 en el ciclo 1 y Bs 3.600 en los ciclos 2 a 5.
            bolsas_ciclo = [480000] + [360000] * 4
        else:
            bolsas_ciclo = _repartir(bolsas)
        base_otros = _repartir(sum(otros))
        partidas.append(("MP_OTROS", "Veterinarios, limpieza, crianza y bolsas", None, None,
                         [base_otros[i] + bolsas_ciclo[i] for i in range(CICLOS)], None))
        # MOD y CIF de la carne
        partidas.append(("MOD_FIJA", "Mano de obra directa fija (salario y aguinaldo)", None, None,
                         _repartir(m("MOD", "SALARIO") + m("MOD", "AGUINALDO")), None))
        partidas.append(("MOD_EVENTUAL", "Personal eventual de faenado (DPF)", "ave", aves,
                         _repartir(m("MOD", "EVENTUAL")), None))
        # DISCREPANCIA: el Plan de Cuentas clasifica los sufijos 38 (aporte
        # patronal) y 39 (indemnización) como MOD; el Manual Presupuestario
        # (B.4 y B.5) los presupuesta dentro de los CIF. El presupuesto sigue
        # al manual y la ejecución compara con los mismos sufijos.
        partidas.append(("CIF", "Costos indirectos de fabricación", None, None, _repartir(m("CIF", "TOTAL")), None))
        # Centro de costos del alimento (desde 2027)
        if m("MP_ALIMENTO", "TOTAL"):
            partidas.append(("ALI_MP", "Materia prima del alimento", "qq", q("COSTO", "COSTO_ALIMENTO") / CICLOS,
                             _repartir(m("MP_ALIMENTO", "TOTAL")), None))
            partidas.append(("ALI_MOD", "Mano de obra del alimento", None, None,
                             _repartir(m("MOD_ALIMENTO", "TOTAL")), None))
            partidas.append(("ALI_CIF", "CIF del alimento", None, None, _repartir(m("CIF_ALIMENTO", "TOTAL")), None))
        # Gastos de operación y CAPEX
        partidas.append(("GASTOS_ADM", "Gastos de administración", None, None, _repartir(m("GASTOS", "TOTAL_ADM")),
                         None))
        partidas.append(("GASTOS_VENTA", "Gastos de venta (fletes)", None, None, _repartir(m("GASTOS", "TOTAL_VENTA")),
                         None))
        capex = m("CAPEX", "TOTAL")
        partidas.append(("CAPEX", "CAPEX del ciclo", None, None, [0, 0, 0, 0, capex], None))
        for partida, concepto, unidad, cantidad, montos, precio_fijo in partidas:
            for ciclo in range(1, CICLOS + 1):
                monto = montos[ciclo - 1]
                if partida == "CAPEX" and monto == 0:
                    continue
                cantidad_mil = None if cantidad is None else int((Decimal(str(cantidad)) * 1000).to_integral_value())
                precio = None
                if cantidad_mil:
                    precio = str(precio_fijo) if precio_fijo is not None else _precio(monto, cantidad_mil)
                yield {"ejercicio": e, "ciclo": ciclo, "partida": partida, "concepto": concepto,
                       "unidad": unidad if cantidad_mil else None, "cantidad_mil": cantidad_mil or None,
                       "precio": precio, "monto_cent": monto, "fuente": fuente}


# --------------------------------------------------------------------------
# POA 2026-27 (POA-001 §2). Trimestres del ejercicio: 1 jul-sep, 2 oct-dic,
# 3 ene-mar, 4 abr-jun. El POA cubre oct-2026 a jun-2027: su T1 es el
# trimestre 2 del ejercicio, T2 el 3 y T3 el 4.
# --------------------------------------------------------------------------
POA_2026: list[tuple[str, str, str, str, str, str]] = [
    ("A1", "PRODUCCION", "Preparar los 3 galpones antes de cada recepción (limpieza, desinfección, cama y calefacción)",
     "3 galpones listos por ciclo en C3, C4 y C5; FIN-TRA-01 firmada", "EG", "2,3,4"),
    ("A2", "PRODUCCION", "Recibir los pollitos BB con certificado sanitario y abrir el centro de costos el mismo día",
     "9.000 pollitos BB a Bs 9,00; 100 % con certificado sanitario", "EG; JFC", "2,3,4"),
    ("A3", "PRODUCCION", "Aplicar el calendario de vacunación y registrar cada aplicación en la bitácora FIN-TRA-02",
     "100 % de aplicaciones registradas", "EG; médico veterinario", "2,3,4"),
    ("A4", "PRODUCCION", "Aplicar la bioseguridad (CI-FIN-11) y reportar las alertas a la GG el mismo día",
     "0 alertas reportadas fuera del día; libro de visitas al día", "EG", "2,3,4"),
    ("A5", "PRODUCCION", "Controlar el consumo de alimento por fase, el ICA por lote y el almacén (PC-07)",
     "ICA < 2,00 por ciclo; 0 días sin alimento", "EG; JFC", "2,3,4"),
    ("A6", "PRODUCCION", "Gestionar la mortalidad con registro diario y disposición sanitaria (FIN-TRA-04/11)",
     "Mortalidad ≤ 3 % por lote; conciliación de aves en 4 de 4 cierres", "EG; GG", "2,3,4"),
    ("A7", "PRODUCCION", "Muestrear el peso cada semana y verificar los parámetros de POL-03",
     "2,6 kg/ave al día 73; ciclo de 73 días (nunca < 70)", "EG", "2,3,4"),
    ("A8", "PRODUCCION", "Capacitar a la Unidad Técnica en parámetros de crianza y registros",
     "2 capacitaciones en el período", "GG", "2,4"),
    ("A9", "PRODUCCION", "Supervisar la obra del ambiente de preparación y el montaje de la maquinaria con prueba piloto",
     "Ambiente y maquinaria instalados y probados al 30-jun-2027", "EG; GG; JFC", "4"),
    ("B1", "FAENADO", "Emitir la Circular de Aviso de Cierre de Ciclo (CIR-EGR-01)",
     "4 circulares con al menos 7 días de antelación (propuesta)", "EG", "2,3,4"),
    ("B2", "FAENADO", "Contratar el personal eventual el lunes de la semana de faenado (FIN-EGR-06, CONT-EGR-01)",
     "4 contrataciones; Bs 1,50/ave; DPF ≤ 100 %", "GG; JFC", "2,3,4"),
    ("B3", "FAENADO", "Faenar el ciclo en 3 lotes de 1.000 aves y registrar peso vivo, canal y merma (FIN-TRA-05)",
     "12 lotes faenados; ≥ 2.910 aves por ciclo; PMF < 10 %", "EG; JFC", "2,3,4"),
    ("B4", "FAENADO", "Cumplir los puntos de control de inocuidad y cadena de frío (CI-FIN-12)",
     "12 de 12 lotes con «Cumple CI-FIN-12»", "EG; GG", "2,3,4"),
    ("B5", "FAENADO", "Embolsar con bolsas personalizadas y código QR «Vida Digna» del lote",
     "100 % de bolsas con QR", "EG; GG", "2,3,4"),
    ("B6", "FAENADO", "Despachar el mismo día con nota de entrega (FIN-ING-03 / FIN-ING-06)",
     "100 % del lote despachado el día del faenado; 0 entregas sin nota", "JFC; EG", "2,3,4"),
    ("B7", "FAENADO", "Recolectar la gallinaza al cierre de cada ciclo y venderla",
     "12 cargas (3 por ciclo) a Bs 500", "EG; JFC", "2,3,4"),
    ("B8", "FAENADO", "Analizar el rendimiento de canal y las mermas al cierre de cada ciclo",
     "4 de 4 Informes de Costo de Ciclo con PMF y acciones", "JFC; GG", "2,3,4"),
    ("C1", "COMERCIAL", "Captar clientes HORECA de los Yungas con visitas comerciales",
     "≥ 6 clientes HORECA con pedido confirmado (propuesta)", "JFC; GG", "2,3,4"),
    ("C2", "COMERCIAL", "Formalizar convenios de comercialización con cláusulas de pago y pre-venta",
     "≥ 3 convenios al 31-mar-2027 (propuesta)", "GG; CA", "2,3"),
    ("C3", "COMERCIAL", "Pre-vender cada lote con cobertura total verificada el día anterior (CI-FIN-13)",
     "12 de 12 lotes con 100 % de destino confirmado", "JFC", "2,3,4"),
    ("C4", "COMERCIAL", "Abrir el canal de mercados populares y friales con fichas de comprador",
     "≥ 2 friales por lote; 100 % de ventas al contado con comprobante", "JFC", "2,3,4"),
    ("C5", "COMERCIAL", "Gestionar el aval técnico de una facultad de veterinaria o agronomía",
     "1 aval emitido al 31-mar-2027", "GG; CA", "2,3"),
    ("C6", "COMERCIAL", "Realizar visitas guiadas a la granja como promoción de lanzamiento",
     "4 visitas (propuesta)", "GG; EG", "2,3,4"),
    ("C7", "COMERCIAL", "Aplicar el tarifario y analizar cada mes el margen por canal (CI-FIN-06, FIN-ING-08)",
     "9 análisis mensuales; margen bruto ≥ 20 % por canal", "GG; CA", "2,3,4"),
    ("C8", "COMERCIAL", "Controlar el crédito HORECA (máximo 1 día, comprobante, reporte de vencidos)",
     "0 créditos > 1 día; 0 cuentas vencidas sin gestión", "JFC; GG", "2,3,4"),
    ("C9", "COMERCIAL", "Preparar la venta de alimento excedente 2027 y proponer el Programa de excedente",
     "≥ 5 compradores fichados; programa aprobado por el CA", "JFC; CA", "4"),
    ("D1", "ADMINISTRATIVA", "Obtener o confirmar la personería jurídica ante AFCOOP",
     "Personería y licencia emitidas al 31-dic-2026", "GG; Presidente del CA", "2"),
    ("D2", "ADMINISTRATIVA", "Inscribirse en el SIN (NIT y RAU) y conciliar la superficie (10 vs 8 ha)",
     "Certificado RAU vigente; superficie conciliada", "JFC; GG", "2"),
    ("D3", "ADMINISTRATIVA", "Tramitar la licencia de funcionamiento, la patente municipal y la ficha ambiental",
     "3 trámites con comprobante al 31-dic-2026", "GG", "2"),
    ("D4", "ADMINISTRATIVA", "Confirmar el registro y certificación de inocuidad SENASAG",
     "Certificado vigente al 31-dic-2026", "GG", "2"),
    ("D5", "ADMINISTRATIVA", "Implantar el expediente EXP-RAU, el calendario fiscal y la consulta tributaria",
     "Expediente ≥ 95 %; consulta presentada antes del 30-jun-2027", "JFC; GG; CA", "2,3,4"),
    ("D6", "ADMINISTRATIVA", "Afiliar al personal fijo en Gestora, CNS y Ministerio de Trabajo y suscribir contratos",
     "3 de 3 trabajadores afiliados al 31-oct-2026", "JFC; GG", "2"),
    ("D7", "ADMINISTRATIVA", "Elaborar la planilla mensual con aportes y provisiones (PC-09)",
     "9 planillas pagadas en plazo; 0 multas", "JFC; GG; CV", "2,3,4"),
    ("D8", "ADMINISTRATIVA", "Habilitar el sistema contable y conciliar el capital con el libro de socios",
     "Sistema operativo; capital 100 % conciliado", "JFC", "2"),
    ("D9", "ADMINISTRATIVA", "Difundir e implantar los manuales, políticas e instrumentos CI-FIN",
     "18 de 18 instrumentos aprobados", "JFC; GG", "2,3"),
    ("D10", "ADMINISTRATIVA", "Controlar el abastecimiento: stock del viernes, OC del lunes y punto de reorden",
     "CAC ≥ 90 %; 0 desabastecimientos", "GG; JFC; EG", "2,3,4"),
    ("D11", "ADMINISTRATIVA", "Gestionar la tesorería frente al SME (FIN-TES-05/06)",
     "SME ≥ 100 % al inicio de C3, C4 y C5", "GG; JFC", "2,3,4"),
    ("D12", "ADMINISTRATIVA", "Cerrar cada mes con el INF-CV-07 antes del FIN-INF-05 (CI-FIN-18)",
     "9 cierres; 9 conciliaciones; 9 INF-CV-07", "JFC; CV; GG", "2,3,4"),
    ("D13", "ADMINISTRATIVA", "Emitir el FIN-TES-12 por ciclo y el cuadro de mando FIN-TES-13",
     "4 informes; 100 % de rojos con causa y acción", "JFC; GG", "2,3,4"),
    ("D14", "ADMINISTRATIVA", "Ejecutar el CAPEX 2026 (Bs 97.000) con activación y verificación física",
     "2 inversiones dentro de ±15 %", "GG; CA; JFC", "4"),
    ("D15", "ADMINISTRATIVA", "Elaborar el presupuesto 2027 y el Plan CAPEX 2027-28 según el calendario",
     "Presupuesto y Plan CAPEX aprobados por el CA hasta el 15-jun-2027", "GG; JFC; CA; CV", "4"),
    ("D16", "ADMINISTRATIVA", "Cerrar el ejercicio al 30-jun-2027 con inventario, arqueo y conciliaciones",
     "Cierre listo al 30-jun; EEFF en plazo (Ley 356 Art. 55)", "JFC; GG; CV", "4"),
    ("E1", "GOBERNANZA", "Realizar la Asamblea General de aprobación de instrumentos, presupuesto y POA",
     "1 asamblea con acta firmada", "Presidente del CA; GG", "2"),
    ("E2", "GOBERNANZA", "Realizar la Asamblea de rendición semestral (propuesta)",
     "1 asamblea en marzo con informe y acta", "Presidente del CA; GG", "3"),
    ("E3", "GOBERNANZA", "Reunir mensualmente al Consejo de Administración",
     "9 reuniones con acta", "Presidente del CA; Secretario", "2,3,4"),
    ("E4", "GOBERNANZA", "Reunir mensualmente al Consejo de Vigilancia y emitir INF-CV-07 e INF-CV-02",
     "9 INF-CV-07; 3 INF-CV-02", "Presidente del CV", "2,3,4"),
    ("E5", "GOBERNANZA", "Ejecutar las verificaciones del CV (arqueos, inventario sorpresivo, activos, bienestar animal)",
     "2 arqueos; 1 inventario sorpresivo; 1 verificación de activos", "CV", "2,3,4"),
    ("E6", "GOBERNANZA", "Capacitar a los socios en principios cooperativos (Ley 356 Art. 6)",
     "3 talleres; asistencia ≥ 10 de 12 socios", "Presidente del CA; Secretario; JFC", "2,3,4"),
    ("E7", "GOBERNANZA", "Mantener al día el libro de actas y el registro de socios",
     "Libros actualizados al cierre de cada trimestre", "Secretario del CA", "2,3,4"),
]
