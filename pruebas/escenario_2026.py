"""Escenario proyectado del ejercicio 2026 (1-jul-2026 a 30-jun-2027).

Carga, con asientos resumidos por ciclo registrados a través de
``servicios.contable`` (partida doble) y de ``servicios.tesoreria`` (fondo de
caja), las cifras del Manual Presupuestario y de «21. EEFF proyectados y
cálculos auxiliares.xlsx» (hojas «Costo + Ingreso + EERR», «EFE» y «BG»):

* Ingresos: carne Bs 916.500 (5 ciclos × 7.800 kg × Bs 23,50) y gallinaza
  Bs 7.500 → Bs 924.000.
* Costo de ventas Bs 576.631,41; gastos de administración Bs 122.525,20;
  gastos de venta Bs 14.550 → excedente neto Bs 210.293,39.
* Flujo: saldo inicial Bs 138.010; CAPEX Bs 97.000 en el ciclo 5; saldo final
  Bs 291.668,90 (banco Bs 286.668,90 + fondo de caja Bs 5.000).
* Pasivos al 30-jun-2027: sueldos Bs 9.252,74; aportes laborales Bs 1.347,26;
  aportes patronales Bs 1.824,26; servicios Bs 125; indemnización Bs 10.600.

Es un ayudante de pruebas y de la base de demostración: no forma parte de la
aplicación. Las cifras salen de los documentos (regla 8).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from db.conexion import obtener_parametro
from servicios import contable, seguridad, tesoreria

D = Decimal
BANCO = "1.1.01.03"
CENTRO = "6.1.01.01"
INICIO = date(2026, 7, 1)
CICLOS = 5
DIAS_CICLO = 73

# Por ciclo (Manual Presupuestario D.4-1 y hoja «Costo + Ingreso + EERR», columna D).
POLLITOS = D("27000")
ALIMENTO = D("55500")
VETERINARIOS = D("5895")
LIMPIEZA = D("780")
CRIANZA = D("2910")
BOLSAS = {1: D("4800")}            # ciclo 1; ciclos 2-5: Bs 3.600 (EFE: insumos 69.885 y 68.685)
BOLSAS_RESTO = D("3600")
SALARIO_PRODUCCION = D("7920")
AGUINALDO_PRODUCCION = D("660")
INDEMNIZACION_PRODUCCION = D("660")
EVENTUAL = D("4500")
MANTENIMIENTO = D("1500")
ENERGIA = D("300")
DEPRECIACION = (("30", "4.1.02.01", D("1050")), ("31", "4.1.02.05", D("986.25")),
                ("32", "4.1.02.02", D("300")), ("33", "4.1.02.06", D("162")))
SUELDOS_ADMIN = D("17520")
AGUINALDO_ADMIN = D("1460")
INDEMNIZACION_ADMIN = D("1460")
DEPRECIACION_ADMIN = (("7.1.02.04", "4.1.02.07", D("110")), ("7.1.02.05", "4.1.02.08", D("220")),
                      ("7.1.02.06", "4.1.02.09", D("335")), ("7.1.02.07", "4.1.02.10", D("280")))
FLETES = D("2910")
SENASAG = D("500")
RAU = D("24.24")
VENTA_CARNE = D("183300")
VENTA_GALLINAZA = D("1500")
# Aportes patronales: Bs 6.815,16 (producción) y Bs 15.075,96 (administración)
# anuales = 5 × 1.363,032 y 5 × 3.015,192; el centavo de redondeo va al ciclo 5.
APORTE_PRODUCCION = {1: D("1363.03"), 2: D("1363.03"), 3: D("1363.03"), 4: D("1363.03"), 5: D("1363.04")}
APORTE_ADMIN = {1: D("3015.19"), 2: D("3015.19"), 3: D("3015.19"), 4: D("3015.19"), 5: D("3015.20")}
SERVICIOS_PAGADOS = {1: D("300"), 2: D("300"), 3: D("300"), 4: D("300"), 5: D("175")}
CAPEX = (("CAPEX-REF-1", "Maquinaria para alimento balanceado", D("77000")),
         ("CAPEX-REF-2", "Construcción de ambiente para preparación", D("20000")))
# Saldos al 30-jun-2027 de la hoja «BG» (columna 2026).
SUELDOS_POR_PAGAR = D("9252.74")
LABORALES_POR_PAGAR = D("1347.26")
APORTES_POR_PAGAR = D("1824.26")


def _r(valor: Decimal) -> Decimal:
    return valor.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def fechas_ciclo(k: int) -> tuple[date, date]:
    inicio = INICIO + timedelta(days=DIAS_CICLO * (k - 1))
    return inicio, inicio + timedelta(days=DIAS_CICLO - 1)


def _asiento(con: Any, jf: Any, fecha: date, glosa: str, lineas: list[dict[str, Any]], doc: str | None = None,
             tipo: str = "TRASPASO") -> None:
    contable.crear_asiento(con, jf, fecha, glosa, lineas, "ESCENARIO_2026", doc, tipo=tipo)


def _desembolso(con: Any, s: Mapping[str, Any], monto: str, fecha: date) -> int:
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, "Desembolso del escenario", ctx)
    estado = seguridad.firmar_autorizacion(con, s["gg"], aid)
    if estado == "PENDIENTE":
        seguridad.firmar_autorizacion(con, s["cv"], aid)
    return aid


def cargar_escenario_2026(con: Any, s: Mapping[str, Any]) -> dict[str, Decimal]:
    """Registra el ejercicio 2026 proyectado. ``s`` son las sesiones por cargo
    (``s["jf"]`` registra; GG y CV firman el desembolso del fondo de caja)."""
    jf = s["jf"]
    tasa_laboral = D(str(obtener_parametro(con, "aporte_laboral", 2026)))
    tesoreria.constituir_fondo(con, jf, INICIO, "Jefe de Finanzas y Comercialización", "Resolución GG 01/2026",
                               autorizacion_id=_desembolso(con, s, "5000", INICIO))
    lab_prod = _r(SALARIO_PRODUCCION * tasa_laboral)
    lab_admin = _r(SUELDOS_ADMIN * tasa_laboral)
    neto_total = (SALARIO_PRODUCCION - lab_prod + SUELDOS_ADMIN - lab_admin) * CICLOS
    lab_total = (lab_prod + lab_admin) * CICLOS
    aportes_total = sum(APORTE_PRODUCCION.values()) + sum(APORTE_ADMIN.values())
    pagado_neto = pagado_lab = pagado_aportes = D("0")
    for k in range(1, CICLOS + 1):
        inicio, fin = fechas_ciclo(k)
        bolsas = BOLSAS.get(k, BOLSAS_RESTO)
        # Inicio del ciclo: compra de pollitos e insumos (pagados) y consumo al centro de costo.
        _asiento(con, jf, inicio, f"Ciclo {k}: compra de pollitos BB", [
            {"cuenta": "1.1.03.01", "debe": POLLITOS}, {"cuenta": BANCO, "haber": POLLITOS}], tipo="EGRESO")
        insumos = [("1.1.03.02", ALIMENTO, "02"), ("1.1.03.08", VETERINARIOS, "08"), ("1.1.03.09", LIMPIEZA, "09"),
                   ("1.1.03.10", CRIANZA, "10"), ("1.1.03.11", bolsas, "12")]
        total_insumos = sum(m for _, m, _ in insumos)
        _asiento(con, jf, inicio, f"Ciclo {k}: compra de alimento e insumos",
                 [{"cuenta": c, "debe": m} for c, m, _ in insumos] + [{"cuenta": BANCO, "haber": total_insumos}],
                 tipo="EGRESO")
        _asiento(con, jf, inicio, f"Ciclo {k}: consumo de pollitos e insumos al centro de costo",
                 [{"cuenta": CENTRO, "sufijo": "01", "debe": POLLITOS}]
                 + [{"cuenta": CENTRO, "sufijo": suf, "debe": m} for _, m, suf in insumos]
                 + [{"cuenta": "1.1.03.01", "haber": POLLITOS}] + [{"cuenta": c, "haber": m} for c, m, _ in insumos])
        if k == 1:
            _asiento(con, jf, inicio, "Certificación SENASAG", [
                {"cuenta": "7.1.04.01", "debe": SENASAG}, {"cuenta": BANCO, "haber": SENASAG}], tipo="EGRESO")
        # Fin del ciclo: ventas al contado.
        _asiento(con, jf, fin, f"Ciclo {k}: cobro de ventas de pollos faeneados (7.800 kg × Bs 23,50)", [
            {"cuenta": BANCO, "debe": VENTA_CARNE}, {"cuenta": "5.1.01.01", "haber": VENTA_CARNE}], tipo="INGRESO")
        _asiento(con, jf, fin, f"Ciclo {k}: cobro de ventas de gallinaza", [
            {"cuenta": BANCO, "debe": VENTA_GALLINAZA}, {"cuenta": "5.2.01.04", "haber": VENTA_GALLINAZA}],
            tipo="INGRESO")
        # Costos de conversión del ciclo.
        aporte_p = APORTE_PRODUCCION[k]
        _asiento(con, jf, fin, f"Ciclo {k}: planilla de producción (MOD)", [
            {"cuenta": CENTRO, "sufijo": "20", "debe": SALARIO_PRODUCCION},
            {"cuenta": CENTRO, "sufijo": "21", "debe": AGUINALDO_PRODUCCION},
            {"cuenta": CENTRO, "sufijo": "38", "debe": aporte_p},
            {"cuenta": CENTRO, "sufijo": "39", "debe": INDEMNIZACION_PRODUCCION},
            {"cuenta": "2.1.02.01", "haber": SALARIO_PRODUCCION - lab_prod},
            {"cuenta": "2.1.02.05", "haber": lab_prod},
            {"cuenta": "2.1.02.02", "haber": AGUINALDO_PRODUCCION},
            {"cuenta": "2.1.02.03", "haber": aporte_p},
            {"cuenta": "2.2.01.01", "haber": INDEMNIZACION_PRODUCCION}])
        _asiento(con, jf, fin, f"Ciclo {k}: personal eventual de faenado y mantenimiento", [
            {"cuenta": CENTRO, "sufijo": "22", "debe": EVENTUAL}, {"cuenta": CENTRO, "sufijo": "36", "debe": MANTENIMIENTO},
            {"cuenta": BANCO, "haber": EVENTUAL + MANTENIMIENTO}], tipo="EGRESO")
        _asiento(con, jf, fin, f"Ciclo {k}: energía eléctrica y agua", [
            {"cuenta": CENTRO, "sufijo": "37", "debe": ENERGIA}, {"cuenta": "2.1.01.06", "haber": ENERGIA}])
        _asiento(con, jf, fin, f"Ciclo {k}: depreciación de bienes productivos (CIF)",
                 [{"cuenta": CENTRO, "sufijo": suf, "debe": m} for suf, _, m in DEPRECIACION]
                 + [{"cuenta": c, "haber": m} for _, c, m in DEPRECIACION])
        # Cierre del ciclo: traslado a productos terminados y costo de ventas.
        por_sufijo = {"01": POLLITOS, **{suf: m for _, m, suf in insumos}, "20": SALARIO_PRODUCCION,
                      "21": AGUINALDO_PRODUCCION, "38": aporte_p, "39": INDEMNIZACION_PRODUCCION, "22": EVENTUAL,
                      "36": MANTENIMIENTO, "37": ENERGIA, **{suf: m for suf, _, m in DEPRECIACION}}
        costo_ciclo = sum(por_sufijo.values())
        _asiento(con, jf, fin, f"Ciclo {k}: cierre de costos, traslado a productos terminados",
                 [{"cuenta": "1.1.03.12", "debe": costo_ciclo}]
                 + [{"cuenta": CENTRO, "sufijo": suf, "haber": m} for suf, m in sorted(por_sufijo.items())])
        _asiento(con, jf, fin, f"Ciclo {k}: costo de ventas de carne de pollo", [
            {"cuenta": "6.1.02.01", "debe": costo_ciclo}, {"cuenta": "1.1.03.12", "haber": costo_ciclo}])
        # Gastos de administración y venta del ciclo.
        aporte_a = APORTE_ADMIN[k]
        _asiento(con, jf, fin, f"Ciclo {k}: planilla administrativa", [
            {"cuenta": "7.1.01.01", "debe": SUELDOS_ADMIN}, {"cuenta": "7.1.01.02", "debe": AGUINALDO_ADMIN},
            {"cuenta": "7.1.01.03", "debe": aporte_a}, {"cuenta": "7.1.01.04", "debe": INDEMNIZACION_ADMIN},
            {"cuenta": "2.1.02.01", "haber": SUELDOS_ADMIN - lab_admin}, {"cuenta": "2.1.02.05", "haber": lab_admin},
            {"cuenta": "2.1.02.02", "haber": AGUINALDO_ADMIN}, {"cuenta": "2.1.02.03", "haber": aporte_a},
            {"cuenta": "2.2.01.01", "haber": INDEMNIZACION_ADMIN}])
        _asiento(con, jf, fin, f"Ciclo {k}: depreciación y amortización administrativas",
                 [{"cuenta": g, "debe": m} for g, _, m in DEPRECIACION_ADMIN]
                 + [{"cuenta": c, "haber": m} for _, c, m in DEPRECIACION_ADMIN])
        _asiento(con, jf, fin, f"Ciclo {k}: fletes de venta", [
            {"cuenta": "7.1.03.01", "debe": FLETES}, {"cuenta": BANCO, "haber": FLETES}], tipo="EGRESO")
        # Pagos del ciclo.
        if k < CICLOS:
            neto = SALARIO_PRODUCCION - lab_prod + SUELDOS_ADMIN - lab_admin
            lab = lab_prod + lab_admin
            aportes = aporte_p + aporte_a
        else:
            neto = neto_total - pagado_neto - SUELDOS_POR_PAGAR
            lab = lab_total - pagado_lab - LABORALES_POR_PAGAR
            aportes = aportes_total - pagado_aportes - APORTES_POR_PAGAR
        pagado_neto += neto
        pagado_lab += lab
        pagado_aportes += aportes
        _asiento(con, jf, fin, f"Ciclo {k}: pago de sueldos y aportes laborales", [
            {"cuenta": "2.1.02.01", "debe": neto}, {"cuenta": "2.1.02.05", "debe": lab},
            {"cuenta": BANCO, "haber": neto + lab}], tipo="EGRESO")
        _asiento(con, jf, fin, f"Ciclo {k}: pago de aportes patronales", [
            {"cuenta": "2.1.02.03", "debe": aportes}, {"cuenta": BANCO, "haber": aportes}], tipo="EGRESO")
        _asiento(con, jf, fin, f"Ciclo {k}: pago de servicios básicos", [
            {"cuenta": "2.1.01.06", "debe": SERVICIOS_PAGADOS[k]}, {"cuenta": BANCO, "haber": SERVICIOS_PAGADOS[k]}],
            tipo="EGRESO")
        if k == CICLOS:
            aguinaldos = (AGUINALDO_PRODUCCION + AGUINALDO_ADMIN) * CICLOS
            _asiento(con, jf, fin, "Pago de aguinaldos", [
                {"cuenta": "2.1.02.02", "debe": aguinaldos}, {"cuenta": BANCO, "haber": aguinaldos}], tipo="EGRESO")
            _asiento(con, jf, fin, "Impuesto RAU de la gestión", [
                {"cuenta": "7.1.05.02", "debe": RAU}, {"cuenta": BANCO, "haber": RAU}], tipo="EGRESO")
            # DISCREPANCIA: 1.2.01.04/05 (*) rigen desde el ejercicio 2027 y el
            # activo se activa al entrar en uso (D.5); el desembolso del ciclo 5
            # queda en 1.2.03.02 con la referencia a la línea D.5.
            for ref, glosa, monto in CAPEX:
                _asiento(con, jf, fin, f"CAPEX 2026: {glosa} (pendiente de activación)", [
                    {"cuenta": "1.2.03.02", "debe": monto}, {"cuenta": BANCO, "haber": monto}], ref, tipo="EGRESO")
    return {"banco": tesoreria.saldo_bancario(con, date(2027, 6, 30)),
            "caja": tesoreria.saldo_cuenta(con, "1.1.01.05", date(2027, 6, 30))}


def cerrar_meses(con: Any, s: Mapping[str, Any], ejercicio: int = 2026, hasta_numero: int = 11) -> None:
    """FIN-INF-01 (JF) y cierre mensual (GG) de los períodos 1..``hasta_numero``."""
    from servicios import cierre

    for p in con.execute("SELECT id FROM periodos WHERE ejercicio = ? AND numero <= ? ORDER BY numero",
                         (ejercicio, hasta_numero)).fetchall():
        cierre.registrar_lista_verificacion(con, s["jf"], int(p["id"]))
        cierre.cerrar_mes(con, s["gg"], int(p["id"]))


def autorizacion_cierre(con: Any, s: Mapping[str, Any], monto: Any, fecha: Any = "2027-06-30") -> int:
    """Autorización AJUSTE_CONTABLE de la GG para el cierre anual (MOF §3.1)."""
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "AJUSTE_CONTABLE", monto, "Cierre anual del ejercicio", ctx)
    seguridad.firmar_autorizacion(con, s["gg"], aid)
    return aid


def cerrar_ejercicio_completo(con: Any, s: Mapping[str, Any], ejercicio: int = 2026) -> dict[str, Any]:
    """Cierres mensuales, cierre anual, cierre de junio y del ejercicio."""
    from servicios import cierre

    cerrar_meses(con, s, ejercicio, 11)
    previa = cierre.preparar_cierre_anual(con, s["jf"], ejercicio)
    resultado = cierre.ejecutar_cierre_anual(con, s["jf"], ejercicio, autorizacion_cierre(con, s, previa["total"]))
    junio = con.execute("SELECT id FROM periodos WHERE ejercicio = ? AND numero = 12", (ejercicio,)).fetchone()["id"]
    cierre.registrar_lista_verificacion(con, s["jf"], int(junio))
    cierre.cerrar_mes(con, s["gg"], int(junio))
    cierre.cerrar_ejercicio(con, s["gg"], ejercicio)
    return resultado
