"""Pruebas de servicios/produccion.py (Fase 6).

Cifras de los documentos:
* POL-03 Art. 1: ciclo de 73 días, prohibido faenar antes del día 70,
  densidad máxima de 8 aves/m²; lotes de faenado de 1.000 aves (PO-04).
* POA: el día de recepción es el día 1 (C1 1-jul a 11-sep-2026; C5 desde el
  19-abr-2027); 3.000 aves por ciclo; pollitos a Bs 9 (Bs 27.000 por ciclo,
  Manual Presupuestario D.4-1).
* CI-FIN-05 / FIN-TRA-04: mortalidad acumulada ≤ 5 % al sufijo 40; > 5 % a
  7.2.01.04 con informe veterinario y autorización de la GG. Semáforo: verde
  ≤ 3 %, amarillo 3-5 %, rojo > 5 %.
* KPI-18 PMF: Bueno < 10 %, Normal 10-20 %, Malo > 20 %. PR-FIN-06 ICA: Bueno
  < 2,00, Normal 2,00-2,10, Malo > 2,10.
* Fases (Master Plan): inicio 1-21, crecimiento 22-50, acabado 51-73.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal

import pytest

from pruebas import ayuda_compras as a
from reportes import formatos_produccion as pdf
from servicios import contable, produccion as pr, tesoreria
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal
TARDE = datetime(2026, 9, 30, 18, 0)        # momento de registro posterior a todas las fechas de prueba
TODO_CUMPLE = {k: True for k in pr.PUNTOS_CI_FIN_12}


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def en(fecha: str) -> datetime:
    """Registro el mismo día del hecho, a las 18:00."""
    return datetime.fromisoformat(fecha + "T18:00:00")


def ciclo(con, s, aves=3000, superficie="125"):
    """Ciclo 1 de 2026: pollitos recibidos el lunes 6-jul-2026 (día 1) y
    distribuidos en lotes de 1.000 aves en galpones de 125 m² (8 aves/m²)."""
    a.depositar(con, s, "100000")
    a.comprar(con, s, "INS-0301", aves, 9, a.LUNES)
    galpones = [pr.registrar_galpon(con, s["ut"], f"G{i}", f"Galpón {i}", superficie,
                                    fotos_url=f"https://fotos.pollolandia.bo/g{i}" if i == 1 else None)
                for i in range(1, aves // 1000 + 1)]
    kardex = pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"]
    cid = pr.abrir_ciclo(con, s["jf"], kardex, {g: 1000 for g in galpones}, "SENASAG-CERT-0042")
    lotes = [int(r[0]) for r in con.execute("SELECT id FROM lotes WHERE ciclo_id = ? ORDER BY codigo", (cid,))]
    return cid, lotes, galpones


def dia(n: int) -> str:
    """Fecha ISO del día n del ciclo que inicia el 6-jul-2026."""
    return date.fromordinal(date(2026, 7, 6).toordinal() + n - 1).isoformat()


# ============================== CALENDARIO ===================================
def test_calendario_de_cinco_ciclos_de_73_dias(con):
    cal = pr.calendario_ciclos(con, 2026)
    assert len(cal) == 5
    assert (cal[0]["inicio_programado"], cal[0]["cierre_programado"]) == (date(2026, 7, 1), date(2026, 9, 11))
    assert cal[1]["inicio_programado"] == date(2026, 9, 12)
    assert (cal[4]["inicio_programado"], cal[4]["cierre_programado"]) == (date(2027, 4, 19), date(2027, 6, 30))
    assert {c["estado"] for c in cal} == {"PROGRAMADO"} and cal[0]["aves_programadas"] == 3000
    assert [pr.fase_de_dia(con, "2026-07-06", n) for n in (1, 21, 22, 50, 51, 73)] == \
        ["INICIO", "INICIO", "CRECIMIENTO", "CRECIMIENTO", "ACABADO", "ACABADO"]


# =============================== APERTURA ====================================
def test_apertura_traslada_pollitos_a_6_1_01_01_sufijo_01_el_mismo_dia(con, s):
    cid, lotes, _ = ciclo(con, s)
    c = pr.obtener_ciclo(con, s["jf"], cid, "2026-07-06")
    assert (c["ejercicio"], c["numero"], c["fecha_inicio"], c["fecha_cierre"]) == (2026, 1, "2026-07-06", "2026-09-16")
    assert c["aves_iniciales"] == 3000 and c["costo_pollitos"] == D("27000.00") and c["dia_actual"] == 1
    assert c["proveedor"] == "Agroveterinaria Caranavi S.R.L."
    asiento = contable.obtener_asiento(con, s["jf"], c["asiento_id"])
    assert asiento["fecha"] == "2026-07-06"
    debe = [(l["cuenta"], l["sufijo"], l["debe"]) for l in asiento["lineas"] if l["debe"]]
    assert debe == [("6.1.01.01", "01", D("27000.00"))]
    assert [l["cuenta"] for l in asiento["lineas"] if l["haber"]] == ["1.1.03.01"]
    assert tesoreria.saldo_cuenta(con, "1.1.03.01") == D("0.00")        # cuenta transitoria
    assert [l["codigo"] for l in c["lotes"]] == ["2026-C1-G1", "2026-C1-G2", "2026-C1-G3"]
    assert {l["densidad"] for l in c["lotes"]} == {"8.00"}
    assert pr.recepciones_pollitos_disponibles(con, s["jf"]) == []
    assert pr.calendario_ciclos(con, 2026)[0]["estado"] == "ABIERTO"
    pr.confirmar_recepcion(con, s["ut"], cid)
    with pytest.raises(ReglaNegocioError):
        pr.confirmar_recepcion(con, s["ut"], cid)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE ciclos_produccion SET aves_iniciales = 2900 WHERE id = ?", (cid,))


def test_apertura_controla_densidad_tamano_de_lote_y_distribucion(con, s):
    a.depositar(con, s, "100000")
    a.comprar(con, s, "INS-0301", 2000, 9, a.LUNES)
    chico = pr.registrar_galpon(con, s["ut"], "G1", "Galpón 1", "120")      # 1.000 / 120 = 8,33 aves/m²
    grande = pr.registrar_galpon(con, s["ut"], "G2", "Galpón 2", "200")
    otro = pr.registrar_galpon(con, s["ut"], "G3", "Galpón 3", "130")
    kardex = pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"]
    with pytest.raises(ReglaNegocioError) as e:
        pr.abrir_ciclo(con, s["jf"], kardex, {chico: 1000, grande: 1000})
    assert e.value.codigo == "DENSIDAD_EXCEDIDA"
    with pytest.raises(ReglaNegocioError) as e:
        pr.abrir_ciclo(con, s["jf"], kardex, {grande: 1001, otro: 999})
    assert e.value.codigo == "LOTE_EXCEDE_TAMANO"
    with pytest.raises(ReglaNegocioError) as e:
        pr.abrir_ciclo(con, s["jf"], kardex, {grande: 1000, otro: 900})
    assert e.value.codigo == "ASIGNACION_NO_CUADRA"
    with pytest.raises(AutorizacionError):                              # Finanzas abre el ciclo
        pr.abrir_ciclo(con, s["ut"], kardex, {grande: 1000, otro: 1000})
    assert pr.listar_galpones(con, s["ut"], "2026-07-06")[0]["capacidad"] == 960
    cid = pr.abrir_ciclo(con, s["jf"], kardex, {grande: 1000, otro: 1000})
    with pytest.raises(ReglaNegocioError) as e:
        pr.abrir_ciclo(con, s["jf"], kardex, {chico: 960})
    assert e.value.codigo == "TRASLADO_YA_ASIGNADO"
    with pytest.raises(ReglaNegocioError):
        pr.actualizar_galpon(con, s["ut"], grande, activo=False)
    assert cid > 0


# ======================= BITÁCORA, SEMÁFORO Y ALERTAS ========================
def test_bitacora_mortalidad_acumulada_con_semaforo_y_alertas(con, s):
    cid, lotes, _ = ciclo(con, s)
    r = pr.registrar_bitacora(con, s["ut"], lotes[0], dia(5), "45", 90, causa_presunta="Onfalitis", ahora=en(dia(5)))
    assert (r["dia"], r["fase"], r["pct"], r["semaforo"], r["alertas"]) == (5, "INICIO", D("0.0300"), "VERDE", [])
    r = pr.registrar_bitacora(con, s["ut"], lotes[1], dia(22), "120", 1, causa_presunta="Traumatismo",
                              ahora=en(dia(22)))
    assert (r["fase"], r["pct"], r["semaforo"]) == ("CRECIMIENTO", D("0.0303"), "AMARILLO")
    assert len(r["alertas"]) == 1                                         # cruzó el 3 %
    r = pr.registrar_bitacora(con, s["ut"], lotes[2], dia(51), "160", 59, causa_presunta="Ascitis",
                              ahora=en(dia(51)))
    assert (r["fase"], r["muertas_acumuladas"], r["pct"], r["semaforo"]) == ("ACABADO", 150, D("0.0500"), "AMARILLO")
    assert r["alertas"] == []                                             # 5 % exacto aún no es anormal
    r = pr.registrar_bitacora(con, s["ut"], lotes[2], dia(52), "160", 3, causa_presunta="Ascitis", ahora=en(dia(52)))
    assert (r["pct"], r["semaforo"]) == (D("0.0510"), "ROJO")
    tipos = [x["tipo"] for x in pr.listar_alertas(con, s["gg"], cid)]
    assert tipos == ["MORTALIDAD_ANORMAL", "MORTALIDAD_ALERTA"]
    assert pr.mortalidad_ciclo(con, cid)["vivas"] == 2847
    with pytest.raises(ReglaNegocioError) as e:                           # orden cronológico
        pr.registrar_bitacora(con, s["ut"], lotes[0], dia(30), "100", 0, ahora=TARDE)
    assert e.value.codigo == "BITACORA_FUERA_DE_ORDEN"
    with pytest.raises(DatosInvalidosError):                              # causa presunta obligatoria
        pr.registrar_bitacora(con, s["ut"], lotes[0], dia(53), "100", 2, ahora=TARDE)
    with pytest.raises(AutorizacionError):                                # registro de la Unidad Técnica
        pr.registrar_bitacora(con, s["jf"], lotes[0], dia(53), "100", 0, ahora=TARDE)
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_bitacora(con, s["ut"], lotes[0], dia(53), "100", 911, causa_presunta="X", ahora=TARDE)
    assert e.value.codigo == "MUERTAS_EXCEDEN_VIVAS"
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE bitacora_diaria SET aves_muertas = 0")


def test_alerta_sanitaria_el_mismo_dia_y_decision_de_la_gerencia(con, s):
    cid, lotes, _ = ciclo(con, s)
    r = pr.registrar_bitacora(con, s["ut"], lotes[0], dia(3), "30", 0, signos_alerta=True,
                              novedad_sanitaria="Secreción nasal en 12 aves", ahora=en(dia(3)))
    automatica = pr.listar_alertas(con, s["gg"], cid)[0]
    assert (automatica["id"], automatica["tipo"], automatica["mismo_dia"]) == (r["alertas"][0], "SIGNOS_CLINICOS", 1)
    with pytest.raises(DatosInvalidosError):                              # reporte tardío sin causa
        pr.reportar_alerta(con, s["ut"], lotes[1], dia(4), "SIGNOS_CLINICOS", "Postración", ahora=en(dia(5)))
    tarde = pr.reportar_alerta(con, s["ut"], lotes[1], dia(4), "SIGNOS_CLINICOS", "Postración",
                               causa_demora="Sin señal telefónica en la granja", ahora=en(dia(5)))
    assert uno(con, "SELECT mismo_dia FROM alertas_sanitarias WHERE id = ?", tarde) == 0
    with pytest.raises(AutorizacionError):
        pr.atender_alerta(con, s["ut"], tarde, "AISLAMIENTO", "Aislar")
    pr.atender_alerta(con, s["gg"], automatica["id"], "CONSULTA_VETERINARIA", "Visita del veterinario mañana")
    with pytest.raises(ReglaNegocioError):
        pr.atender_alerta(con, s["gg"], automatica["id"], "AISLAMIENTO", "Otra")
    assert len(pr.listar_alertas(con, s["gg"], cid, pendientes=True)) == 1
    pr.revisar_semana(con, s["gg"], cid, 1, "Sin observaciones")
    with pytest.raises(ReglaNegocioError):
        pr.revisar_semana(con, s["gg"], cid, 2)                           # semana sin bitácora


# ========================= BAJA POR MORTALIDAD ===============================
def test_mortalidad_acumulada_de_5_1_va_a_7_2_01_04_y_no_al_sufijo_40(con, s):
    cid, lotes, _ = ciclo(con, s)
    normal = pr.registrar_bitacora(con, s["ut"], lotes[0], dia(5), "45", 60, causa_presunta="Onfalitis",
                                   ahora=en(dia(5)))
    b1 = pr.registrar_baja(con, s["jf"], normal["id"])
    assert (b1["clasificacion"], b1["pct"], b1["costo_por_ave"], b1["monto"], b1["estado"]) == \
        ("NORMAL", D("0.0200"), D("9.0000"), D("540.00"), "CONTABILIZADA")
    asiento = contable.obtener_asiento(con, s["jf"], b1["asiento_id"])
    assert [(l["cuenta"], l["sufijo"], l["debe"]) for l in asiento["lineas"] if l["debe"]] == \
        [("6.1.01.01", "40", D("540.00"))]
    brote = pr.registrar_bitacora(con, s["ut"], lotes[1], dia(15), "90", 93, causa_presunta="Coccidiosis",
                                  ahora=en(dia(15)))
    assert brote["pct"] == D("0.0510")
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_baja(con, s["jf"], brote["id"])
    assert e.value.codigo == "INFORME_VETERINARIO_REQUERIDO"
    b2 = pr.registrar_baja(con, s["jf"], brote["id"], "INF-VET-01/2026")
    assert (b2["clasificacion"], b2["monto"], b2["estado"], b2["asiento_id"]) == \
        ("ANORMAL", D("837.00"), "PENDIENTE_AUTORIZACION", None)
    with pytest.raises(AutorizacionError):
        pr.contabilizar_baja(con, s["jf"], b2["id"])                      # sin autorización de la GG
    with pytest.raises(AutorizacionError):
        pr.autorizar_baja_anormal(con, s["jf"], b2["id"])
    pr.autorizar_baja_anormal(con, s["gg"], b2["id"])
    asiento = contable.obtener_asiento(con, s["jf"], pr.contabilizar_baja(con, s["jf"], b2["id"]))
    assert [(l["cuenta"], l["sufijo"], l["debe"]) for l in asiento["lineas"] if l["debe"]] == \
        [("7.2.01.04", None, D("837.00"))]
    assert not any(l["sufijo"] == "40" for l in asiento["lineas"])
    assert tesoreria.saldo_cuenta(con, "7.2.01.04") == D("837.00")
    # El centro conserva la mortalidad normal y pierde la anormal: 27.000 − 837.
    assert pr.costo_ciclo_por_sufijo(con, cid, dia(15)) == {"01": D("25623.00"), "40": D("540.00")}
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE bajas_mortalidad SET clasificacion = 'NORMAL' WHERE id = ?", (b2["id"],))


def test_mortalidad_de_5_por_ciento_exacto_es_normal_y_bajas_en_orden(con, s):
    cid, lotes, _ = ciclo(con, s)
    d1 = pr.registrar_bitacora(con, s["ut"], lotes[0], dia(10), "60", 100, causa_presunta="Calor", ahora=en(dia(10)))
    d2 = pr.registrar_bitacora(con, s["ut"], lotes[1], dia(11), "60", 50, causa_presunta="Calor", ahora=en(dia(11)))
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_baja(con, s["jf"], d2["id"])
    assert e.value.codigo == "BAJAS_EN_ORDEN"
    pr.registrar_baja(con, s["jf"], d1["id"])
    b = pr.registrar_baja(con, s["jf"], d2["id"])
    assert (b["pct"], b["clasificacion"], b["monto"]) == (D("0.0500"), "NORMAL", D("450.00"))
    assert tesoreria.saldo_cuenta(con, "7.2.01.04") == D("0.00")
    with pytest.raises(ReglaNegocioError):
        pr.registrar_baja(con, s["jf"], d2["id"])                         # duplicada
    assert pr.bitacoras_sin_baja(con, cid) == []


# ===================== DISPOSICIÓN Y MUESTREO DE PESO ========================
def test_disposicion_sanitaria_y_muestreo_semanal(con, s):
    cid, lotes, _ = ciclo(con, s)
    pr.registrar_bitacora(con, s["ut"], lotes[0], dia(2), "25", 8, causa_presunta="Deshidratación", ahora=en(dia(2)))
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_disposicion(con, s["ut"], lotes[0], dia(2), 9, "ENTIERRO_CAL", "Encargado de Granja")
    assert e.value.codigo == "DISPOSICION_EXCEDE"
    with pytest.raises(DatosInvalidosError):
        pr.registrar_disposicion(con, s["ut"], lotes[0], dia(2), 8, "OTRO", "Encargado de Granja")
    did = pr.registrar_disposicion(con, s["ut"], lotes[0], dia(2), 8, "ENTIERRO_CAL", "Encargado de Granja")
    pr.supervisar_disposicion(con, s["gg"], did)
    m1 = pr.registrar_muestreo(con, s["ut"], lotes[0], dia(7), 20, "3.5")
    assert (m1["semana"], m1["dia"], m1["peso_promedio"]) == (1, 7, D("0.175"))
    with pytest.raises(DatosInvalidosError):
        pr.registrar_muestreo(con, s["ut"], lotes[0], dia(6), 20, "3.4")  # misma semana
    m2 = pr.registrar_muestreo(con, s["ut"], lotes[0], dia(21), 20, "16")
    assert m2["semana"] == 3
    cumplimiento = pr.cumplimiento_muestreo(con, lotes[0], dia(21))
    assert (cumplimiento["esperadas"], cumplimiento["faltantes"], cumplimiento["cumplimiento"]) == (3, [2], D("0.6667"))
    with pytest.raises(ReglaNegocioError):
        pr.registrar_muestreo(con, s["ut"], lotes[1], dia(14), 1001, "1500")


# =============================== FAENADO =====================================
def test_faenado_en_dia_69_rechazado_y_dia_70_con_pmf_e_ica(con, s):
    cid, lotes, _ = ciclo(con, s)
    pr.registrar_bitacora(con, s["ut"], lotes[0], dia(60), "5160", 0, ahora=en(dia(60)))
    assert dia(69) == "2026-09-12" and dia(70) == "2026-09-13"
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_faenado(con, s["ut"], lotes[0], dia(69), 1000, "2600", "2340", TODO_CUMPLE, "Juan Mamani",
                             ahora=TARDE)
    assert e.value.codigo == "FAENADO_ANTES_DIA_MINIMO" and e.value.detalle["dia"] == 69
    with pytest.raises(DatosInvalidosError):                              # faltan puntos de control
        pr.registrar_faenado(con, s["ut"], lotes[0], dia(70), 1000, "2600", "2340", {"agua_hielo": True},
                             "Juan Mamani", ahora=TARDE)
    r = pr.registrar_faenado(con, s["ut"], lotes[0], dia(70), 1000, "2600", "2340", TODO_CUMPLE, "Juan Mamani",
                             ahora=TARDE)
    # Merma 260 kg = 10,00 % (Normal: Bueno es < 10 %); ICA 5.160 / 2.600 = 1,98 (Bueno)
    assert (r["dia"], r["merma"], r["pmf"], r["semaforo_pmf"]) == (70, D("260.000"), D("0.1000"), "NORMAL")
    assert (r["ica"], r["semaforo_ica"], r["cumple_ci_fin_12"], r["alertas"]) == (D("1.98"), "BUENO", True, [])
    assert pr.semaforo_pmf(con, dia(70), D("0.0999")) == "BUENO"
    assert pr.semaforo_pmf(con, dia(70), D("0.2000")) == "NORMAL" and pr.semaforo_pmf(con, dia(70), D("0.2001")) == "MALO"
    assert [pr.semaforo_ica(con, dia(70), D(x)) for x in ("1.99", "2.00", "2.10", "2.11")] == \
        ["BUENO", "NORMAL", "NORMAL", "MALO"]
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_bitacora(con, s["ut"], lotes[0], dia(71), "0", 0, ahora=TARDE)
    assert e.value.codigo == "LOTE_FAENADO"
    # Segundo lote: PMF en rojo e incumplimiento del choque térmico se reportan a la GG.
    malo = dict(TODO_CUMPLE, choque_termico=False)
    pr.registrar_bitacora(con, s["ut"], lotes[1], dia(61), "5000", 0, ahora=TARDE)
    r2 = pr.registrar_faenado(con, s["ut"], lotes[1], dia(71), 1000, "2500", "1990", malo, "Juan Mamani",
                              carcasas_separadas=40, ahora=TARDE)
    assert (r2["pmf"], r2["semaforo_pmf"], r2["ica"], r2["semaforo_ica"], r2["cumple_ci_fin_12"]) == \
        (D("0.2040"), "MALO", D("2.00"), "NORMAL", False)
    assert {x["tipo"] for x in pr.listar_alertas(con, s["gg"], cid, pendientes=True)} == {"PMF_MALO", "INOCUIDAD"}
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE faenados SET peso_canal_mil = 0")


def test_faenado_anticipado_solo_con_indicacion_veterinaria_de_la_gg(con, s):
    cid, lotes, _ = ciclo(con, s)
    with pytest.raises(AutorizacionError):
        pr.autorizar_faenado_anticipado(con, s["ut"], lotes[0], "INF-VET-07/2026")
    pr.autorizar_faenado_anticipado(con, s["gg"], lotes[0], "INF-VET-07/2026")
    pr.registrar_bitacora(con, s["ut"], lotes[0], dia(60), "5000", 0, ahora=TARDE)
    r = pr.registrar_faenado(con, s["ut"], lotes[0], dia(69), 1000, "2550", "2300", TODO_CUMPLE, "Juan Mamani",
                             ahora=TARDE)
    assert r["dia"] == 69
    with pytest.raises(DatosInvalidosError):                              # más allá del día 73 sin explicación
        pr.registrar_faenado(con, s["ut"], lotes[1], dia(74), 1000, "2600", "2340", TODO_CUMPLE, "Juan Mamani",
                             ahora=TARDE)


# ============================ CONCILIACIÓN Y QR ==============================
def test_conciliacion_ingresadas_igual_muertas_mas_faenadas_y_qr(con, s):
    cid, lotes, _ = ciclo(con, s)
    provs = tuple(int(r[0]) for r in con.execute("SELECT id FROM proveedores ORDER BY id"))
    a.comprar(con, s, "INS-0302", 10, 185, a.LUNES_2, provs)              # alimento del ciclo en el kárdex
    b = pr.registrar_bitacora(con, s["ut"], lotes[0], dia(5), "45", 30, causa_presunta="Onfalitis",
                              tratamiento="Vacuna Newcastle (día 5)", ahora=en(dia(5)))
    pr.registrar_bitacora(con, s["ut"], lotes[1], dia(6), "45", 15, causa_presunta="Onfalitis", ahora=en(dia(6)))
    pr.registrar_bitacora(con, s["ut"], lotes[2], dia(7), "40", 0, ahora=en(dia(7)))
    pr.registrar_muestreo(con, s["ut"], lotes[0], dia(70), 30, "78.6")
    with pytest.raises(ReglaNegocioError) as e:
        pr.datos_trazabilidad(con, lotes[0])
    assert e.value.codigo == "QR_SIN_FAENADO"
    pr.registrar_faenado(con, s["ut"], lotes[0], dia(70), 970, "2522", "2290", TODO_CUMPLE, "Juan Mamani", ahora=TARDE)
    with pytest.raises(DatosInvalidosError):                              # faltante sin explicación
        pr.registrar_faenado(con, s["ut"], lotes[1], dia(71), 980, "2548", "2300", TODO_CUMPLE, "Juan Mamani",
                             ahora=TARDE)
    pr.registrar_faenado(con, s["ut"], lotes[1], dia(71), 985, "2561", "2310", TODO_CUMPLE, "Juan Mamani", ahora=TARDE)
    pr.registrar_faenado(con, s["ut"], lotes[2], dia(72), 1000, "2600", "2350", TODO_CUMPLE, "Juan Mamani", ahora=TARDE)
    r = pr.conciliar_aves(con, cid)
    assert (r["ingresadas"], r["muertas"], r["faenadas"], r["faltante"], r["muertas_sin_baja"]) == \
        (3000, 45, 2955, 0, 45)
    assert not r["cuadra"]
    with pytest.raises(ReglaNegocioError) as e:
        pr.concluir_ciclo(con, s["jf"], cid)
    assert e.value.codigo == "MORTALIDAD_SIN_BAJA"
    for x in pr.bitacoras_sin_baja(con, cid):
        pr.registrar_baja(con, s["jf"], int(x["id"]))
    final = pr.concluir_ciclo(con, s["jf"], cid)
    assert (final["ingresadas"], final["muertas_normal"] + final["muertas_anormal"] + final["faenadas"]) == (3000, 3000)
    assert final["cuadra"] and uno(con, "SELECT estado FROM ciclos_produccion WHERE id = ?", cid) == "CONCLUIDO"
    # QR con datos veraces de los registros del ciclo
    datos = pr.datos_trazabilidad(con, lotes[0])
    assert datos["url"].startswith("https://vidadigna.pollolandia.bo/lote/2026-C1-G1-")
    assert (datos["leyenda"], datos["fecha_faenado"], datos["dias_vida"]) == ("Este pollo vivió 70 días", dia(70), 70)
    assert datos["insumos_alimentacion"] == ["Alimento balanceado comprado"]
    assert (datos["alimento_kg"], datos["mortalidad_acumulada"], datos["peso_muestreo"]) == \
        (D("45.000"), D("0.0150"), D("2.620"))
    assert datos["tratamientos"] == ["Vacuna Newcastle (día 5)"] and datos["fotos_url"] == "https://fotos.pollolandia.bo/g1"
    assert datos["respaldo"]["bitacora_dias"] == 1 and b["id"] > 0
    assert "<svg" in pr.qr_svg(datos["url"])
    pagina = pr.pagina_trazabilidad_html(datos)
    assert "Este pollo vivió 70 días" in pagina and "06/07/2026" in pagina


def test_faltante_de_aves_requiere_conocimiento_de_la_gg(con, s):
    cid, lotes, _ = ciclo(con, s, aves=1000)
    with pytest.raises(ReglaNegocioError) as e:
        pr.registrar_faenado(con, s["ut"], lotes[0], dia(70), 1000, "2590", "2330", TODO_CUMPLE, "Juan Mamani",
                             ahora=TARDE)
    assert e.value.codigo == "SIN_ALIMENTO_BITACORA"
    pr.registrar_bitacora(con, s["ut"], lotes[0], dia(10), "50", 0, ahora=TARDE)
    pr.registrar_faenado(con, s["ut"], lotes[0], dia(70), 996, "2590", "2330", TODO_CUMPLE, "Juan Mamani",
                         explicacion_faltante="4 aves no halladas en el conteo de captura", ahora=TARDE)
    r = pr.conciliar_aves(con, cid)
    assert (r["ingresadas"], r["faenadas"], r["faltante"], r["cuadra"]) == (1000, 996, 4, False)
    with pytest.raises(ReglaNegocioError) as e:
        pr.concluir_ciclo(con, s["jf"], cid)
    assert e.value.codigo == "CONCILIACION_NO_CUADRA"
    pr.autorizar_faltante(con, s["gg"], cid, "Informe de la UT; se reporta al Consejo de Vigilancia")
    assert pr.concluir_ciclo(con, s["jf"], cid)["faltante"] == 4


def test_formatos_pdf_de_produccion(con, s):
    cid, lotes, _ = ciclo(con, s)
    b = pr.registrar_bitacora(con, s["ut"], lotes[0], dia(5), "45", 20, causa_presunta="Onfalitis", ahora=en(dia(5)))
    pr.registrar_baja(con, s["jf"], b["id"])
    pr.registrar_disposicion(con, s["ut"], lotes[0], dia(5), 20, "ENTIERRO_CAL", "Encargado de Granja")
    pr.registrar_faenado(con, s["ut"], lotes[0], dia(70), 980, "2548", "2300", TODO_CUMPLE, "Juan Mamani", ahora=TARDE)
    ctx = pdf.Contexto(fecha=date(2026, 9, 13), elaborado_por="Jefe de Finanzas", cargo="JFC", ciclo="1")
    c = pr.obtener_ciclo(con, s["jf"], cid, dia(70))
    documentos = [
        pdf.fin_tra_01(c, ctx), pdf.fin_tra_02(c, pr.listar_bitacora(con, s["jf"], cid), ctx),
        pdf.fin_tra_04(c, pr.listar_bajas(con, s["jf"], cid), ctx),
        pdf.fin_tra_05(c, pr.listar_faenados(con, s["jf"], cid), ctx),
        pdf.fin_tra_11(c, pr.listar_disposiciones(con, s["jf"], cid), ctx),
        pdf.alerta_ci_fin_11(c, pr.listar_alertas(con, s["jf"], cid), ctx),
        pdf.etiqueta_qr(pr.datos_trazabilidad(con, lotes[0]), ctx),
    ]
    for datos in documentos:
        assert datos.startswith(b"%PDF") and b"P\\341gina 1 de" in datos
