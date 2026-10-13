"""Pruebas de servicios/costos.py (Fase 7).

Cifras de los documentos (Manual del Sistema de Costos §6 y §7; Manual
Presupuestario D.4-1 y B.1):
* Ciclo 2026 de 3.000 aves: MPD Bs 96.885 (pollitos 27.000; alimento 55.500 =
  300 qq × Bs 185; veterinarios 5.895; limpieza 780; crianza 2.910; bolsas
  4.800); MOD Bs 15.103,03 (7.920 + 660 + 1.363,03 + 660 + eventual 4.500);
  CIF Bs 4.298,25 (galpones 1.050; equipos 986,25; herramientas 162;
  alambrados 300; mantenimiento 1.500; energía 300); total Bs 116.286,28;
  7.800 kg; Bs 14,91 por kg.
* Presupuesto D.4-1 ciclo 1 de 2026: Bs 116.286.
* Alimento 2027: 417 qq por ciclo (B.1) con costo Bs 57.567,31 (D.4-2) =
  Bs 138,05 por qq; consumo propio 300 qq = Bs 41.415 (D.4-1 2027); excedente
  con saquillo de Bs 10 = Bs 148,05 por qq (Manual de Costos §6.4).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from pruebas import ayuda_compras as a
from reportes import formatos_costos as pdf
from servicios import contable, costos, planillas, produccion as pr, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal
TARDE = datetime(2026, 9, 30, 18, 0)
TODO_CUMPLE = {k: True for k in pr.PUNTOS_CI_FIN_12}
CIF_2026 = [("30", "1050", "Depreciación galpones"), ("31", "986.25", "Depreciación equipos de producción"),
            ("33", "162", "Depreciación herramientas"), ("32", "300", "Depreciación alambrados"),
            ("36", "1500", "Mantenimiento y limpieza"), ("37", "300", "Energía eléctrica y agua")]


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def insumo(con, codigo):
    return a.insumo(con, codigo)


def abrir_ciclo_2026(con, s, compras_mpd=True):
    """Ciclo 1 de 2026: recepción el lunes 6-jul (día 1), día 73 el 16-sep."""
    a.depositar(con, s, "300000")
    provs = a.tres_proveedores(con, s)
    planillas.registrar_personal(con, s["jf"], "Encargado de Granja", "6012345", "Encargado de Granja", "GRANJA",
                                 "3300", "2026-07-01", "INDEFINIDO", "Contrato 01/2026", polivalente=True)
    a.comprar(con, s, "INS-0301", 3000, 9, a.LUNES, provs)
    if compras_mpd:
        for codigo, cantidad, precio in (("INS-0302", 300, 185), ("INS-0308", 393, 15), ("INS-0309", 78, 10),
                                         ("INS-0310", 291, 10), ("INS-0311", 3000, "1.60")):
            a.comprar(con, s, codigo, cantidad, precio, a.LUNES, provs,
                      vencimiento="2027-06-30" if codigo == "INS-0308" else None)
    galpones = [pr.registrar_galpon(con, s["ut"], f"G{i}", f"Galpón {i}", "125") for i in (1, 2, 3)]
    kardex = pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"]
    cid = pr.abrir_ciclo(con, s["jf"], kardex, {g: 1000 for g in galpones})
    lotes = [int(r[0]) for r in con.execute("SELECT id FROM lotes WHERE ciclo_id = ? ORDER BY codigo", (cid,))]
    return cid, lotes, provs


def eventual_4500(con, s):
    """Faenado del lote: 5 jornaleros × 600 aves × Bs 1,50 (Fase 5)."""
    cid = planillas.emitir_circular(con, s["ut"], "2026-09-01", "2026-09-09", "L-2026-01", 3000, "0")
    planillas.acusar_circular(con, s["gg"], cid)
    planillas.acusar_circular(con, s["jf"], cid)
    sol = planillas.solicitar_contratacion(con, s["jf"], cid, "2026-09-07", 5)
    planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-07")
    contratos = [planillas.registrar_contrato(con, s["jf"], sol["id"], f"Jornalero {n}", f"80000{n}", "2026-09-07")
                 for n in range(1, 6)]
    planillas.liquidar_jornales(con, s["jf"], sol["id"], "2026-09-10", {c: 600 for c in contratos})


def ciclo_completo_2026(con, s, con_planillas=False):
    """Acumula el ciclo de referencia del Manual de Costos y lo concluye."""
    cid, lotes, provs = abrir_ciclo_2026(con, s)
    for codigo, cantidad in (("INS-0302", 300), ("INS-0308", 393), ("INS-0309", 78), ("INS-0310", 291)):
        costos.consumir_insumo(con, s["jf"], insumo(con, codigo), "2026-07-20", cantidad, ciclo_id=cid)
    for lote in lotes:
        pr.registrar_bitacora(con, s["ut"], lote, "2026-07-15", "4600", 0, ahora=TARDE)
    if con_planillas:
        for mes in (7, 8):
            pid = planillas.preparar_planilla(con, s["jf"], 2026, mes)
            planillas.autorizar_planilla(con, s["gg"], pid)
            planillas.contabilizar_planilla(con, s["jf"], pid)
    eventual_4500(con, s)
    costos.consumir_insumo(con, s["jf"], insumo(con, "INS-0311"), "2026-09-13", 3000, ciclo_id=cid)
    for sufijo, monto, concepto in CIF_2026:
        costos.registrar_cif(con, s["jf"], sufijo, "2026-09-15", monto, concepto, ciclo_id=cid)
    for lote, fecha in zip(lotes, ("2026-09-13", "2026-09-14", "2026-09-15")):
        pr.registrar_faenado(con, s["ut"], lote, fecha, 1000, "2860", "2600", TODO_CUMPLE, "Juan Mamani", ahora=TARDE)
    pr.concluir_ciclo(con, s["jf"], cid)
    return cid, lotes, provs


# ===================== CRITERIO DEL MASTER PLAN: CICLO 2026 ==================
def test_ciclo_2026_reproduce_116_286_y_14_91_por_kg(con, s):
    cid, _, _ = ciclo_completo_2026(con, s)
    mayor = costos.mayor_analitico(con, s["jf"], cid, "2026-09-16")
    # Antes del cierre: MPD completa; de la MOD solo el eventual (la fija entra con el corte del día 73).
    assert (mayor["por_elemento"]["MPD"], mayor["por_elemento"]["MOD"]) == (D("96885.00"), D("4500.00"))
    with pytest.raises(AutorizacionError):
        costos.cerrar_ciclo(con, s["gg"], cid)
    r = costos.cerrar_ciclo(con, s["jf"], cid)
    assert r["fecha"].isoformat() == "2026-09-16"
    assert (r["por_elemento"]["MPD"], r["por_elemento"]["MOD"], r["por_elemento"]["CIF"]) == \
        (D("96885.00"), D("15103.03"), D("4298.25"))
    assert (r["total"], r["kg"], r["costo_unitario"]) == (D("116286.28"), D("7800.000"), D("14.91"))
    assert r["por_sufijo"] == {"01": D("27000.00"), "02": D("55500.00"), "08": D("5895.00"), "09": D("780.00"),
                               "10": D("2910.00"), "12": D("4800.00"), "20": D("7920.00"), "21": D("660.00"),
                               "22": D("4500.00"), "30": D("1050.00"), "31": D("986.25"), "32": D("300.00"),
                               "33": D("162.00"), "36": D("1500.00"), "37": D("300.00"), "38": D("1363.03"),
                               "39": D("660.00")}
    # Sin planillas en el ciclo, el corte devenga los 73 días de MOD fija (Bs 10.603,03).
    assert (r["corte_mod"]["dias"], r["corte_mod"]["total"]) == (73, D("10603.03"))
    assert tesoreria.saldo_cuenta(con, "6.1.01.01") == D("0.00")          # centro en cero
    assert tesoreria.saldo_cuenta(con, "1.1.03.12") == D("116286.28")
    asiento = contable.obtener_asiento(con, s["jf"], r["asiento_id"])
    assert [(l["cuenta"], l["debe"]) for l in asiento["lineas"] if l["debe"]] == [("1.1.03.12", D("116286.28"))]
    inf = costos.informe_costo_ciclo(con, s["jf"], cid)
    assert (inf["conciliacion"]["ingresadas"], inf["conciliacion"]["faenadas"], inf["ica"]) == (3000, 3000, D("1.61"))
    assert inf["kg_vivos"] == D("8580.000") and inf["variacion"] is None
    with pytest.raises(ReglaNegocioError):
        costos.cerrar_ciclo(con, s["jf"], cid)
    with pytest.raises(AutorizacionError):
        costos.aprobar_informe(con, s["jf"], cid)
    costos.aprobar_informe(con, s["gg"], cid, "Costo coherente con el presupuesto")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE costos_ciclo SET total_cent = 1 WHERE ciclo_id = ?", (cid,))


def test_comparacion_contra_presupuesto_d4_con_variaciones(con, s):
    cid, _, _ = ciclo_completo_2026(con, s)
    costos.cerrar_ciclo(con, s["jf"], cid)
    comp = costos.comparar_presupuesto(con, s["jf"], cid)
    assert (comp["presupuesto"], comp["real"], comp["variacion"], comp["semaforo"]) == \
        (D("116286.00"), D("116286.28"), D("0.28"), "VERDE")
    filas = {f["sufijo"]: f for f in comp["filas"]}
    assert (filas["01"]["cantidad_real"], filas["01"]["precio_real"], filas["01"]["variacion"]) == \
        (D("3000"), D("9.0000"), D("0.00"))
    assert (filas["02"]["cantidad_presupuesto"], filas["02"]["cantidad_real"], filas["02"]["variacion_precio"],
            filas["02"]["variacion_cantidad"]) == (D("300.000"), D("300.000"), D("0.00"), D("0.00"))
    assert (filas["22"]["cantidad_real"], filas["22"]["precio_real"]) == (D("3000"), D("1.5000"))
    assert (filas["31"]["variacion"], filas["38"]["variacion"]) == (D("0.25"), D("0.03"))
    assert costos.semaforo_variacion(con, "2026-07-06", D("0.06")) == "AMARILLO"
    assert costos.semaforo_variacion(con, "2026-07-06", D("0.1001")) == "ROJO"


def test_variacion_de_precio_y_cantidad_del_alimento(con, s):
    cid, lotes, provs = abrir_ciclo_2026(con, s, compras_mpd=False)
    a.comprar(con, s, "INS-0302", 295, 188, a.LUNES, provs)                # 295 qq a Bs 188 = 55.460
    costos.consumir_insumo(con, s["jf"], insumo(con, "INS-0302"), "2026-07-20", 295, ciclo_id=cid)
    fila = next(f for f in costos.comparar_presupuesto(con, s["jf"], cid)["filas"] if f["sufijo"] == "02")
    # Precio: (188 − 185) × 295 = 885 (desfavorable); cantidad: (295 − 300) × 185 = −925 (favorable)
    assert (fila["real"], fila["variacion"], fila["variacion_precio"], fila["variacion_cantidad"]) == \
        (D("55460.00"), D("-40.00"), D("885.00"), D("-925.00"))
    assert fila["variacion_pct"] == D("-0.0007") and fila["semaforo"] == "VERDE"


def test_costo_de_ventas_de_la_carne_al_cpp(con, s):
    cid, _, _ = ciclo_completo_2026(con, s)
    costos.cerrar_ciclo(con, s["jf"], cid)
    v1 = costos.registrar_costo_venta(con, s["jf"], "CARNE", "2026-09-16", "2600", "FIN-ING-03 N.º 1")
    assert v1["valor"] == D("38762.09")                                   # 116.286,28 × 2.600 / 7.800
    v2 = costos.registrar_costo_venta(con, s["jf"], "CARNE", "2026-09-16", "5200", "FIN-ING-06 N.º 1")
    assert v2["valor"] == D("77524.19")                                   # el último saca el saldo exacto
    assert tesoreria.saldo_cuenta(con, "6.1.02.01") == D("116286.28")
    assert tesoreria.saldo_cuenta(con, "1.1.03.12") == D("0.00")
    with pytest.raises(ReglaNegocioError) as e:
        costos.registrar_costo_venta(con, s["jf"], "CARNE", "2026-09-16", "1", "FIN-ING-06 N.º 2")
    assert e.value.codigo == "EXISTENCIA_INSUFICIENTE"
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM kardex_terminados")


# ======================= CORTE DE MOD Y CICLO SIGUIENTE ======================
def test_corte_de_mod_con_planillas_y_reversion_en_el_ciclo_siguiente(con, s):
    cid, _, provs = ciclo_completo_2026(con, s, con_planillas=True)
    r = costos.cerrar_ciclo(con, s["jf"], cid)
    # Julio y agosto ya están en el centro; se devengan del 1 al 16 de septiembre (16 de 73 días).
    lineas = {l["sufijo"]: l["monto"] for l in r["corte_mod"]["lineas"]}
    assert r["corte_mod"]["dias"] == 16
    assert lineas == {"20": D("1735.89"), "21": D("144.66"), "38": D("298.75"), "39": D("144.66")}
    assert r["por_sufijo"]["20"] == D("6600.00") + D("1735.89")
    assert uno(con, "SELECT reversion_asiento_id FROM cortes_mod WHERE ciclo_id = ?", cid) is None
    # Llegan los pollitos del ciclo 2: la reversión entra con fecha 17-sep (día siguiente al cierre).
    a.comprar(con, s, "INS-0301", 3000, 9, "2026-09-21", provs)
    aplicadas = costos.aplicar_reversiones_pendientes(con, s["jf"])
    asiento = contable.obtener_asiento(con, s["jf"], aplicadas[0])
    assert asiento["fecha"] == "2026-09-17"
    assert {(l["sufijo"], l["haber"]) for l in asiento["lineas"] if l["haber"]} == \
        {("20", D("1735.89")), ("21", D("144.66")), ("38", D("298.75")), ("39", D("144.66"))}
    assert tesoreria.saldo_cuenta(con, "6.1.01.01") == D("27000.00") - D("2323.96")
    assert costos.aplicar_reversiones_pendientes(con, s["jf"]) == []


# ============================ ALIMENTO PROPIO 2027 ===========================
def test_alimento_propio_138_05_por_qq_y_excedente_con_saquillo_148_05(con, s):
    a.depositar(con, s, "300000", fecha="2027-07-02")
    provs = a.tres_proveedores(con, s)
    lunes = "2027-07-05"
    a.comprar(con, s, "INS-0303", 208, "95.12", lunes, provs)              # maíz del ciclo 1 de 2027 (D.4-2)
    a.comprar(con, s, "INS-0311S", 117, 10, lunes, provs)                  # saquillos de yute
    a.comprar(con, s, "INS-0301", 3000, 9, lunes, provs)
    galpones = [pr.registrar_galpon(con, s["ut"], f"G{i}", f"Galpón {i}", "125") for i in (1, 2, 3)]
    cid = pr.abrir_ciclo(con, s["jf"], pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"],
                         {g: 1000 for g in galpones})
    c = costos.consumir_insumo(con, s["jf"], insumo(con, "INS-0303"), "2027-07-06", 208, centro="6.1.01.02")
    assert (c["sufijo"], c["valor"]) == ("03", D("19784.96"))
    cif = costos.registrar_cif(con, s["jf"], "34", "2027-07-06", "37782.35", "Depreciación y demás CIF de la planta")
    assert (cif["carne"], cif["alimento"]) == (D("0.00"), D("37782.35"))
    prod = costos.registrar_produccion_alimento(con, s["jf"], "2027-07-07", 417)
    assert (prod["costo"], prod["costo_qq"]) == (D("57567.31"), D("138.0511"))
    assert tesoreria.saldo_cuenta(con, "6.1.01.02") == D("0.00")
    propio = costos.trasladar_alimento_propio(con, s["jf"], cid, "2027-07-08", 300)
    assert propio["valor"] == D("41415.33")                               # D.4-1 2027: Bs 41.415
    asiento = contable.obtener_asiento(con, s["jf"], propio["asiento_id"])
    assert [(l["cuenta"], l["sufijo"]) for l in asiento["lineas"] if l["debe"]] == [("6.1.01.01", "11")]
    assert [l["cuenta"] for l in asiento["lineas"] if l["haber"]] == ["1.1.03.13"]
    ex = costos.ensaquillar_excedente(con, s["jf"], "2027-07-08", 117)
    assert (ex["costo_base"], ex["costo_saquillos"], ex["costo_total"], ex["costo_qq"]) == \
        (D("16151.98"), D("1170.00"), D("17321.98"), D("148.05"))
    assert costos.saldo_terminado(con, "ALIMENTO")["cantidad"] == D("0.000")
    venta = costos.registrar_costo_venta(con, s["jf"], "EXCEDENTE", "2027-07-09", 117, "FIN-ING-06 N.º 5")
    assert venta["valor"] == D("17321.98")
    assert tesoreria.saldo_cuenta(con, "6.1.02.02") == D("17321.98")
    assert tesoreria.saldo_cuenta(con, "1.1.03.13") == D("0.00")
    with pytest.raises(DatosInvalidosError):
        costos.ensaquillar_excedente(con, s["jf"], "2027-07-09", "0.5")


def test_cif_comun_prorrateado_75_25_y_reglas_de_sufijo(con, s):
    r = costos.registrar_cif(con, s["jf"], "37", "2027-07-31", "1000", "Energía eléctrica de julio")
    assert (r["pct_carne"], r["base"], r["carne"], r["alimento"]) == (D("0.75"), "DEFECTO", D("750.00"), D("250.00"))
    r26 = costos.registrar_cif(con, s["jf"], "37", "2026-07-31", "300", "Energía eléctrica de julio")
    assert (r26["base"], r26["carne"], r26["alimento"]) == ("DIRECTO", D("300.00"), D("0.00"))
    with pytest.raises(ReglaNegocioError) as e:                           # sufijo (*) antes de 2027
        costos.registrar_cif(con, s["jf"], "34", "2026-07-31", "100", "Depreciación maquinaria")
    assert e.value.codigo == "SUFIJO_NO_VIGENTE"
    with pytest.raises(ReglaNegocioError) as e:
        costos.registrar_cif(con, s["jf"], "30", "2026-07-31", "100", "Galpones", contrapartida="2.1.01.06")
    assert e.value.codigo == "CONTRAPARTIDA_NO_VALIDA"
    with pytest.raises(DatosInvalidosError):
        costos.registrar_cif(con, s["jf"], "20", "2026-07-31", "100", "No es CIF")
    with pytest.raises(AutorizacionError):
        costos.registrar_cif(con, s["gg"], "37", "2026-07-31", "100", "Energía")


# ===================== CONSUMOS Y BITÁCORA (kg → qq) =========================
def test_alimento_de_la_bitacora_en_kg_se_valora_en_qq(con, s):
    cid, lotes, provs = abrir_ciclo_2026(con, s, compras_mpd=False)
    a.comprar(con, s, "INS-0302", 300, 185, a.LUNES, provs)
    for lote in lotes:
        pr.registrar_bitacora(con, s["ut"], lote, "2026-07-10", "4600", 0, ahora=TARDE)
    r = costos.consumir_alimento_bitacora(con, s["jf"], cid, "2026-07-10")
    # 13.800 kg ÷ 46 kg/qq = 300 qq × Bs 185
    assert (r["kg"], r["qq"], r["sufijo"], r["valor"]) == (D("13800.000"), D("300.000"), "02", D("55500.00"))
    with pytest.raises(ReglaNegocioError) as e:
        costos.consumir_alimento_bitacora(con, s["jf"], cid, "2026-07-10")
    assert e.value.codigo == "BITACORA_YA_VALORADA"
    with pytest.raises(ReglaNegocioError) as e:
        costos.consumir_insumo(con, s["jf"], insumo(con, "INS-0301"), "2026-07-10", 1, ciclo_id=cid)
    assert e.value.codigo == "POLLITOS_SE_TRASLADAN"
    with pytest.raises(ReglaNegocioError) as e:
        costos.consumir_insumo(con, s["jf"], insumo(con, "INS-0311S"), "2026-07-10", 1, ciclo_id=cid)
    assert e.value.codigo == "SAQUILLOS_SOLO_EXCEDENTE"
    with pytest.raises(ReglaNegocioError) as e:                           # el ciclo aún no se concluye
        costos.cerrar_ciclo(con, s["jf"], cid)
    assert e.value.codigo == "CICLO_NO_CONCLUIDO"
    assert costos.listar_consumos(con, s["jf"], cid)[0]["origen"] == "BITACORA"


def test_formatos_pdf_de_costos(con, s):
    from datetime import date
    cid, _, _ = ciclo_completo_2026(con, s)
    costos.cerrar_ciclo(con, s["jf"], cid)
    ctx = pdf.Contexto(fecha=date(2026, 9, 16), elaborado_por="Jefe de Finanzas", cargo="JFC", ciclo="1 (2026)")
    documentos = [pdf.fin_tra_03(costos.mayor_analitico(con, s["jf"], cid), ctx),
                  pdf.fin_tra_06(costos.informe_costo_ciclo(con, s["jf"], cid), ctx),
                  pdf.fin_tra_07(costos.informe_costo_ciclo(con, s["jf"], cid), ctx),
                  pdf.comparacion_d4(costos.comparar_presupuesto(con, s["jf"], cid), ctx)]
    for datos in documentos:
        assert datos.startswith(b"%PDF") and b"P\\341gina 1 de" in datos


def test_ciclos_superpuestos_no_mezclan_costos(con, s):
    """El ciclo 2 recibe pollitos el 14-sep, antes del día 73 del ciclo 1 (16-sep):
    el cierre del ciclo 1 traslada solo su costo y el centro conserva el del ciclo 2."""
    cid, _, provs = ciclo_completo_2026(con, s)
    a.comprar(con, s, "INS-0301", 3000, 9, "2026-09-14", provs)
    galpones = [int(r[0]) for r in con.execute("SELECT id FROM galpones ORDER BY codigo")]
    c2 = pr.abrir_ciclo(con, s["jf"], pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"],
                        {g: 1000 for g in galpones})
    r = costos.cerrar_ciclo(con, s["jf"], cid)
    assert (r["total"], r["costo_unitario"]) == (D("116286.28"), D("14.91"))
    assert tesoreria.saldo_cuenta(con, "6.1.01.01") == D("27000.00")
    assert pr.costo_ciclo_por_sufijo(con, c2, "2026-09-30") == {"01": D("27000.00")}
    assert costos.mayor_analitico(con, s["jf"], c2, "2026-09-30")["total"] == D("27000.00")
