"""Datos del Plan Operativo Anual 2026-2027 (ANEXO 12. POA 2026-2027).

Transcripción literal de las tablas del POA que carga
``scripts/cargar_datos_poa.py`` (servicio ``servicios/poa.py``):

* §1.1 Calendario de ciclos (73 días desde el 1-jul-2026; el cierre es la
  fecha estimada de faenado y venta). El POA cubre el 1-oct-2026 al
  30-jun-2027: el tramo final del ciclo 2 (54 de 73 días) y los ciclos 3, 4 y
  5 completos.
* §3.1 Presupuesto por objeto de gasto y área (ejercicio, previo al POA del
  1-jul al 30-sep, y asignado al POA del 1-oct al 30-jun). Base caja,
  redondeada.
* §3.2 Flujo de caja proyectado mensual (oct-2026 a jun-2027). Base caja,
  redondeada; el saldo inicial de octubre (Bs 116.825) es una estimación a
  reemplazar por el saldo bancario real.

Los montos van en bolivianos como texto para conservar la exactitud.
"""
from __future__ import annotations

VERSION_POA = "POA 2026-2027 (ANEXO 12) v1"
FUENTE_POA = "ANEXO 12. POA 2026-2027: §1.1 calendario de ciclos, §3.1 presupuesto por área y §3.2 flujo mensual"
EJERCICIO_POA = 2026

# (ciclo, inicio, cierre (día 73), aves, tramo dentro del POA, días dentro del POA, ingreso presupuestado)
CALENDARIO: tuple[tuple[int, str, str, int, str, int, str], ...] = (
    (1, "2026-07-01", "2026-09-11", 3000, "Fuera del POA (cierra antes del 1-oct)", 0, "184800"),
    (2, "2026-09-12", "2026-11-23", 3000, "Parcial: 1-oct a 23-nov (54 de 73 días)", 54, "184800"),
    (3, "2026-11-24", "2027-02-04", 3000, "Completo", 73, "184800"),
    (4, "2027-02-05", "2027-04-18", 3000, "Completo", 73, "184800"),
    (5, "2027-04-19", "2027-06-30", 3000, "Completo (incluye CAPEX de Bs 97.000)", 73, "184800"),
)
TOTAL_INGRESO_POA = "739200"          # C2 a C5: 4 cierres de ciclo dentro del POA

MESES: tuple[str, ...] = ("2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04", "2027-05",
                          "2027-06")

# §3.2: concepto → montos de oct-2026 a jun-2027 (en el orden de MESES).
FLUJO_MENSUAL: dict[str, tuple[str, ...]] = {
    "SALDO_INICIAL": ("116825", "69055", "172109", "124340", "110913", "214833", "167063", "272180", "229740"),
    "COBROS_CARNE": ("0", "183300", "0", "0", "183300", "0", "183300", "0", "183300"),
    "COBROS_GALLINAZA": ("0", "1500", "0", "0", "1500", "0", "1500", "0", "1500"),
    "INSUMOS": ("34343", "61343", "34343", "0", "61343", "34343", "61343", "34343", "0"),
    "MOD": ("3363", "7755", "3363", "3363", "7538", "3363", "7212", "1962", "9699"),
    "CIF": ("1343", "1300", "1343", "1343", "1213", "1343", "1186", "1049", "1015"),
    "ADMINISTRACION": ("8720", "8439", "8720", "8720", "7877", "8720", "7033", "5087", "12247"),
    "COMERCIALIZACION": ("0", "2910", "0", "0", "2910", "0", "2910", "0", "2910"),
    "CAPEX": ("0", "0", "0", "0", "0", "0", "0", "0", "97000"),
    "SALDO_FINAL": ("69055", "172109", "124340", "110913", "214833", "167063", "272180", "229740", "291669"),
}
NOMBRE_CONCEPTO: dict[str, str] = {
    "SALDO_INICIAL": "Saldo inicial de efectivo", "COBROS_CARNE": "(+) Cobros de carne",
    "COBROS_GALLINAZA": "(+) Cobros de gallinaza",
    "INSUMOS": "(–) Pollitos BB e insumos (alimento, veterinarios, limpieza, crianza, bolsas)",
    "MOD": "(–) Mano de obra directa (sueldo, aguinaldo, eventual)",
    "CIF": "(–) CIF desembolsables (mantenimiento, aportes, servicios)",
    "ADMINISTRACION": "(–) Gastos de administración", "COMERCIALIZACION": "(–) Comercialización (fletes)",
    "CAPEX": "(–) CAPEX", "SALDO_FINAL": "Saldo final de efectivo",
}
INGRESOS = ("COBROS_CARNE", "COBROS_GALLINAZA")
EGRESOS_OPERACION = ("INSUMOS", "MOD", "CIF", "ADMINISTRACION", "COMERCIALIZACION")
# Totales del POA (columna «Total POA» de §3.2).
TOTALES_FLUJO: dict[str, str] = {
    "COBROS_CARNE": "733200", "COBROS_GALLINAZA": "6000", "INSUMOS": "321398", "MOD": "47619", "CIF": "11136",
    "ADMINISTRACION": "75564", "COMERCIALIZACION": "11640", "CAPEX": "97000",
}
TOTAL_INGRESOS_FLUJO = "739200"
TOTAL_EGRESOS_OPERACION = "467356"
SALDO_FINAL_POA = "291669"

# §3.1: (grupo, objeto de gasto, área responsable, ejercicio 2026-27, previo al POA, asignado POA)
PRESUPUESTO_AREA: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("INGRESOS", "Ventas de carne de pollo (7.800 kg por ciclo × Bs 23,50)", "Comercial", "916500", "183300", "733200"),
    ("INGRESOS", "Ventas de gallinaza (3 cargas por ciclo × Bs 500)", "Comercial", "7500", "1500", "6000"),
    ("COSTOS_DIRECTOS", "Pollitos BB (Bs 9,00 por ave)", "Producción / Granja", "135000", "54000", "81000"),
    ("COSTOS_DIRECTOS", "Insumo: Alimento balanceado comprado a terceros", "Producción / Granja", "277500", "83250",
     "194250"),
    ("COSTOS_DIRECTOS", "Insumo: Productos veterinarios", "Producción / Granja", "29475", "8843", "20633"),
    ("COSTOS_DIRECTOS", "Insumo: Insumos de limpieza y desinfección", "Producción / Granja", "3900", "1170", "2730"),
    ("COSTOS_DIRECTOS", "Insumo: Insumos de crianza", "Producción / Granja", "14550", "4365", "10185"),
    ("COSTOS_DIRECTOS", "Insumo: Bolsas personalizadas", "Producción / Granja", "19200", "6600", "12600"),
    ("COSTOS_DIRECTOS", "Mano de obra directa: sueldo del Encargado de Granja", "Producción / Granja", "36300", "9981",
     "26319"),
    ("COSTOS_DIRECTOS", "Mano de obra directa: aguinaldo (según Excel, pagado en C5)", "Producción / Granja", "3300",
     "0", "3300"),
    ("COSTOS_DIRECTOS", "Personal eventual de faenado (Bs 1,50 por ave)", "Faenado y Logística", "22500", "4500",
     "18000"),
    ("COSTOS_DIRECTOS", "Mantenimiento y limpieza", "Producción / Granja", "7500", "1890", "5610"),
    ("COSTOS_DIRECTOS", "Aportes patronales del personal de producción (17,21%)", "Producción / Granja", "6247",
     "1718", "4529"),
    ("COSTOS_DIRECTOS", "Energía eléctrica y agua potable", "Producción / Granja", "1375", "378", "997"),
    ("GASTOS", "Sueldos Gerente General y Jefe de Finanzas", "Administrativa y Financiera", "80300", "22080", "58220"),
    ("GASTOS", "Aguinaldo del personal administrativo (según Excel, pagado en C5)", "Administrativa y Financiera",
     "7300", "0", "7300"),
    ("GASTOS", "Aportes patronales del personal administrativo (17,21%)", "Administrativa y Financiera", "13820",
     "3800", "10020"),
    ("GASTOS", "Certificación SENASAG (Bs 500 cada 5 años)", "Administrativa y Financiera", "500", "500", "0"),
    ("GASTOS", "Impuesto RAU (8 ha × Bs 3,03; por validar)", "Administrativa y Financiera", "24.24", "0", "24.24"),
    ("GASTOS", "Flete de venta (Bs 970 por lote de 1.000 aves)", "Comercial / Logística", "14550", "2910", "11640"),
    ("CAPEX", "Maquinaria para alimento balanceado", "Administrativa / Producción", "77000", "0", "77000"),
    ("CAPEX", "Construcción de ambiente de preparación de alimento", "Administrativa / Producción", "20000", "0",
     "20000"),
)
# Totales de §3.1.
TOTAL_EGRESOS_EJERCICIO = "673341"     # «Total egresos de operación» del ejercicio
TOTAL_EGRESOS_PREVIO = "205985"
TOTAL_EGRESOS_POA = "467356"
TOTAL_CAPEX = "97000"
