"""Prueba de integración de la Fase 13: un ciclo completo de punta a punta.

Apertura → compra → recepción → planilla → bitácora de 73 días con mortalidad
normal → faenado → pre-venta → venta → cierre de costos → cierre mensual →
estados financieros, todo con los servicios reales (``pruebas/simulacion_ciclo``).

Criterio de verificación (Master Plan, Fase 13): el ciclo corre sin errores,
cada asiento cuadra y el balance cuadra al final. Los saldos finales de TODAS
las cuentas se comparan con el escenario del POA, calculado con las cifras
unitarias de los documentos:

* POA §1.1 / §3.1 y Manual Presupuestario B.1: 3.000 aves, 7.800 kg × Bs 23,50
  = Bs 183.300; gallinaza 3 cargas × Bs 500; flete Bs 970 por lote; personal
  eventual Bs 1,50 por ave faenada.
* Manual de Costos §6: MPD Bs 96.885; CIF Bs 4.298,25; salario de granja
  Bs 3.300; corte de MOD = días × salario × 12 / 365 (73 días = Bs 7.920).
* PC-09: aguinaldo e indemnización por doceavos; aporte patronal 17,21 %;
  aporte laboral 12,71 %.

# DISCREPANCIA: el escenario del POA valora la MOD fija del ciclo a 73/365 de
# los sueldos anuales (Bs 10.603,03) y el eventual sobre 3.000 aves; la
# operación real paga planillas mensuales completas desde el 1-jul (base de
# 30 días, PC-09) y jornales sobre las aves efectivamente faenadas (2.940).
# La prueba recalcula esas dos partidas con la regla de la operación real y
# deja explícita la diferencia frente al costo del POA (Bs 116.286,28).
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pruebas import simulacion_ciclo as sim
from servicios import cierre, costos, indicadores, tesoreria

D = Decimal


def _r(valor: Decimal) -> Decimal:
    return valor.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def _saldos(con) -> dict[str, Decimal]:
    return {r[0]: D(int(r[1])) / 100 for r in con.execute(
        "SELECT cuenta_codigo, SUM(debe_cent) - SUM(haber_cent) FROM asiento_detalle GROUP BY cuenta_codigo "
        "HAVING SUM(debe_cent) <> SUM(haber_cent)")}


def _apertura(con) -> dict[str, Decimal]:
    return {r[0]: D(int(r[1])) / 100 for r in con.execute(
        "SELECT d.cuenta_codigo, SUM(d.debe_cent) - SUM(d.haber_cent) FROM asiento_detalle d JOIN asientos a "
        "ON a.id = d.asiento_id WHERE a.tipo = 'APERTURA' GROUP BY d.cuenta_codigo")}


def escenario_poa(con) -> dict[str, Decimal]:
    """Saldos esperados al 30-sep-2026 con las cifras unitarias de los documentos."""
    salario_granja, admin = D("3300"), D("7300")
    aguinaldo_pct = D(1) / D(12)                                # doceavos (PC-09)
    patronal = D("0.1721")
    total_ganado = salario_granja + admin                       # Bs 10.600 por mes
    prov_mes = _r(total_ganado * aguinaldo_pct)                 # Bs 883,33
    patronal_mes = _r(total_ganado * patronal)                  # Bs 1.824,26
    # MOD fija del ciclo 1: planillas de julio y agosto + corte del 1 al 16-sep (16/365).
    mod_mes = salario_granja + _r(salario_granja * aguinaldo_pct) * 2 + _r(salario_granja * patronal)
    dia_granja = salario_granja * 12 / D(365)
    corte = (_r(dia_granja * 16) + _r(dia_granja * 16 * aguinaldo_pct) * 2 + _r(dia_granja * 16 * patronal))
    mod_fija = mod_mes * 2 + corte
    eventual = D(3 * sim.AVES_FAENADAS_LOTE) * D("1.50")        # Bs 4.410
    mpd, cif = D("96885"), D("4298.25")
    costo_ciclo = mpd + mod_fija + eventual + cif
    # Ciclo 2 al 30-sep: pollitos + MOD de septiembre − reversión del corte.
    centro_c2 = D("27000") + mod_mes - corte
    ventas_carne, gallinaza, fletes = D("7800") * D("23.50"), 3 * D("500"), 3 * D("970")
    pago_planilla = total_ganado + patronal_mes                 # líquido + laboral + patronal
    banco = (D("138010") - D("5000") - mpd - D("27000") + ventas_carne + gallinaza - eventual - fletes
             - 3 * pago_planilla)
    esperado = {k: v for k, v in _apertura(con).items() if not k.startswith("1.1.01.03")}
    esperado.update({
        "1.1.01.03": banco, "1.1.01.05": D("5000"),
        "2.1.01.06": -D("1800"),                                # mantenimiento + energía por pagar
        "2.1.02.02": -3 * prov_mes, "2.2.01.01": -3 * prov_mes,
        "4.1.02.01": -D("1050"), "4.1.02.02": -D("300"), "4.1.02.05": -D("986.25"), "4.1.02.06": -D("162"),
        "5.1.01.01": -ventas_carne, "5.2.01.04": -gallinaza,
        "6.1.01.01": centro_c2, "6.1.02.01": costo_ciclo,
        "7.1.01.01": 3 * admin, "7.1.01.02": 3 * _r(admin * aguinaldo_pct), "7.1.01.03": 3 * _r(admin * patronal),
        "7.1.01.04": 3 * _r(admin * aguinaldo_pct), "7.1.03.01": fletes,
    })
    return {k: _r(v) for k, v in esperado.items()}


def test_ciclo_completo_de_punta_a_punta(con, s):
    r = sim.simular_ciclo(con, s)
    assert r.pasos == ["apertura", "compra y recepción", "planilla", "bitácora", "faenado", "pre-venta y venta",
                       "cierre de costos", "cierre mensual"]

    assert not con.in_transaction                     # ningún servicio deja transacciones sin confirmar
    # 1. Cada asiento cuadra y ninguno se borró (los identificadores son correlativos).
    descuadrados = con.execute("SELECT asiento_id FROM asiento_detalle GROUP BY asiento_id "
                               "HAVING SUM(debe_cent) <> SUM(haber_cent)").fetchall()
    assert descuadrados == []
    n, maximo = con.execute("SELECT COUNT(*), MAX(id) FROM asientos").fetchone()
    assert n == maximo and n > 60
    assert con.execute("SELECT COUNT(*) FROM asientos a WHERE NOT EXISTS (SELECT 1 FROM asiento_detalle d "
                       "WHERE d.asiento_id = a.id)").fetchone()[0] == 0

    # 2. Producción: 3.000 ingresadas = 60 muertas (2 %, normal) + 2.940 faenadas.
    c = r.conciliacion
    assert (c["ingresadas"], c["muertas_normal"], c["muertas_anormal"], c["faenadas"], c["cuadra"]) == \
        (3000, 60, 0, 2940, True)
    assert c["pct_normal"] == D("0.0200")
    assert con.execute("SELECT COUNT(*) FROM bitacora_diaria").fetchone()[0] == 69 + 70 + 71

    # 3. Costos: MPD y CIF del Manual; 7.800 kg; centro del ciclo 1 en cero.
    k = r.cierre_costos
    poa = escenario_poa(con)
    assert k["kg"] == D("7800.000")
    assert k["total"] == poa["6.1.02.01"] == D("116753.07")
    assert k["por_elemento"]["CIF"] == D("4298.25")
    assert k["costo_unitario"] == D("14.97")
    # Diferencia frente al costo del POA (Bs 116.286,28): MOD fija +556,79 y eventual −90.
    assert k["total"] - D("116286.28") == D("466.79")
    assert tesoreria.saldo_cuenta(con, "1.1.03.12") == D("0.00")

    # 4. Ventas: 7.800 kg × Bs 23,50 y 3 cargas de gallinaza (ingreso del ciclo POA Bs 184.800).
    carne = sum(n["total"] for n in r.notas if n.get("producto", "CARNE_ENTERO") != "GALLINAZA" and n["total"] > 500)
    assert carne == D("183300.00")
    assert tesoreria.saldo_cuenta(con, "5.1.01.01") == -D("183300.00")
    assert tesoreria.saldo_cuenta(con, "5.2.01.04") == -D("1500.00")

    # 5. Saldos finales de todas las cuentas = escenario del POA.
    assert _saldos(con) == poa

    # 6. Cierres mensuales y estados financieros: el balance cuadra.
    estados = [x[0] for x in con.execute("SELECT estado FROM periodos WHERE ejercicio = 2026 AND numero <= 3")]
    assert estados == ["CERRADO", "CERRADO", "CERRADO"]
    bg = cierre.balance_general(con, s["jf"], date(2026, 9, 30))
    assert bg["cuadra"] is True and bg["diferencia"] == D("0.00")
    assert bg["total_activo"] == bg["total_pasivo"] + bg["total_patrimonio"]
    er = cierre.estado_resultados(con, s["jf"], 2026, date(2026, 9, 30))
    excedente = (D("183300") + D("1500") - poa["6.1.02.01"] - poa["7.1.01.01"] - poa["7.1.01.02"]
                 - poa["7.1.01.03"] - poa["7.1.01.04"] - poa["7.1.03.01"])
    assert er["cuadra"] is True and er["excedente_neto"] == excedente == bg["resultado_no_cerrado"]
    efe = cierre.estado_flujo_efectivo(con, s["jf"], 2026, date(2026, 9, 30))
    assert efe is not None

    # 7. Indicadores del ciclo frente al escenario del POA (ICL del ciclo 1: 116.753 / 184.800).
    ciclo = indicadores.tendencia_ciclos(con, s["jf"], 2026)[0]
    assert ciclo["ventas"] == D("184800.00") and ciclo["ICL"] == D("0.6318")
    assert costos.informe_costo_ciclo(con, s["jf"], r.ciclo_id)["conciliacion"]["faenadas"] == 2940
