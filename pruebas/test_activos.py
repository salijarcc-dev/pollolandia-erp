"""Pruebas de la Fase 8 — Activos fijos y CAPEX (servicios/activos.py).

Casos de borde con las cifras de los documentos:
* PC-06 / D.5: galpón Bs 105.000 → Bs 5.250/año (5 %, sufijo 30); maquinaria
  Bs 77.000 → Bs 9.625/año (12,5 %, 6.1.01.02-34); equipo Bs 13.150 →
  Bs 1.643,75/año (12,5 %, sufijo 31); software contable de apertura
  Bs 7.000 → Bs 1.400/año (Diagnóstico Financiero; ver DISCREPANCIA).
* POL-02: no previsto > Bs 20.000 (CA, autorización específica); variación
  > 15 % se informa al CA; > 15 % y > Bs 10.000 exige la Asamblea.
* PR-FIN-07: baja con valor neto > Bs 5.000 exige la verificación del CV.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from reportes import formatos_activos as pdf
from servicios import activos, contable, costos, seguridad, tesoreria
from servicios.activos import ItemVerificacion
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

D = Decimal


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def depreciar(con, s, desde: tuple[int, int], meses: int) -> None:
    anio, mes = desde
    for _ in range(meses):
        activos.depreciar_mes(con, s["jf"], anio, mes)
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)


def ficha_de(con, s, cuenta: str) -> dict:
    aid = uno(con, "SELECT id FROM activos WHERE cuenta_activo = ? ORDER BY id LIMIT 1", cuenta)
    return activos.ficha_activo(con, s["jf"], aid)


def plan_aprobado(con, s, ejercicio: int, fecha: str, utilidad="145750", ratificar=False) -> int:
    """Plan del D.5 analizado por Finanzas, presentado por la GG y aprobado por el CA."""
    pid = activos.crear_plan(con, s["gg"], ejercicio, fecha, importar_d5=True)["id"]
    activos.analizar_plan(con, s["jf"], pid, fecha, utilidad)
    activos.presentar_plan(con, s["gg"], pid, "Plan CAPEX según D.5", "Aprobar con recursos propios", fecha)
    assert activos.resolver_plan_ca(con, s["ca_presidente"], pid, "APRUEBA", f"Acta CA {ejercicio}-01", fecha=fecha) \
        == "APROBADO"
    if ratificar:
        activos.ratificar_plan(con, s["ag"], pid, f"Acta AG {ejercicio}-01", fecha)
    return pid


def linea(con, plan_id: int, categoria: str) -> int:
    return int(uno(con, "SELECT id FROM capex_lineas WHERE plan_id = ? AND categoria = ? ORDER BY id LIMIT 1",
                   plan_id, categoria))


# ======================= CÁLCULO (PC-06 / D.5) =================================
def test_cuotas_anuales_de_los_documentos(con):
    assert activos.cuota_anual("105000", activos.tasa_vigente(con, "galpones", "2026-07-31")) == D("5250.00")
    assert activos.cuota_anual("77000", activos.tasa_vigente(con, "maquinaria_alimento", "2027-07-31")) == D("9625.00")
    assert activos.cuota_anual("13150", activos.tasa_vigente(con, "equipos_produccion", "2027-07-31")) == D("1643.75")
    assert activos.cuota_anual("7000", activos.tasa_vigente(con, "software", "2026-07-31", apertura=True)) \
        == D("1400.00")
    # Software nuevo: PC-06 (25 %); terrenos no se deprecian.
    assert activos.cuota_anual("7000", activos.tasa_vigente(con, "software", "2026-07-31")) == D("1750.00")
    assert activos.tasa_vigente(con, "terrenos", "2026-07-31") == D("0")
    # Doce cuotas suman exactamente la anual (sin residuos de redondeo).
    cuotas = [activos.cuota_mes(D("77000"), D("0.125"), n) for n in range(1, 13)]
    assert cuotas[0] == D("802.08") and sum(cuotas) == D("9625.00")
    assert sum(activos.cuota_mes(D("13150"), D("0.125"), n) for n in range(1, 13)) == D("1643.75")
    # Valor residual cero: la cuota 49 de una herramienta (4 años) es cero.
    assert activos.acumulada_objetivo(D("3240"), D("0.25"), 60) == D("3240.00")
    assert activos.cuota_mes(D("3240"), D("0.25"), 49) == D("0.00")
    assert activos.vida_util_anios(D("0.125")) == D("8.00")
    assert activos.fin_de_mes(2027, 2) .isoformat() == "2027-02-28"


# ======================= APERTURA Y DEPRECIACIÓN MENSUAL =======================
def test_galpon_105000_deprecia_5250_al_sufijo_30_y_software_1400(con, s):
    creadas = activos.registrar_activos_apertura(con, s["jf"])
    assert len(creadas) == 9
    assert {c["codigo_interno"] for c in creadas} >= {"AF-GAL-001", "AF-SOF-001", "AF-TER-001"}
    with pytest.raises(ReglaNegocioError) as e:
        activos.registrar_activos_apertura(con, s["jf"])
    assert e.value.codigo == "APERTURA_YA_REGISTRADA"
    # Libro de inventario = mayor (apertura).
    assert all(f["diferencia"] == 0 for f in activos.conciliar_libro(con, s["jf"], "2026-07-01"))

    # Cuotas consecutivas: no se puede saltar un mes.
    with pytest.raises(ReglaNegocioError) as e:
        activos.depreciar_mes(con, s["jf"], 2026, 8)
    assert e.value.codigo == "DEPRECIACION_PENDIENTE"
    r = activos.depreciar_mes(con, s["jf"], 2026, 7)
    assert r["fecha"].isoformat() == "2026-07-31" and r["activos"] == 8          # el terreno no se deprecia
    asiento = contable.obtener_asiento(con, s["jf"], r["asiento_id"])
    debitos = {(l["cuenta"], l["sufijo"]): l["debe"] for l in asiento["lineas"] if l["debe"]}
    assert debitos[("6.1.01.01", "30")] == D("437.50")                           # 105.000 × 5 % ÷ 12
    assert debitos[("7.1.02.07", None)] == D("116.67")                           # software 7.000 × 20 % ÷ 12
    with pytest.raises(ReglaNegocioError) as e:
        activos.depreciar_mes(con, s["jf"], 2026, 7)
    assert e.value.codigo == "MES_YA_DEPRECIADO"
    depreciar(con, s, (2026, 8), 11)                                             # agosto 2026 a junio 2027

    galpon = ficha_de(con, s, "1.2.01.02")
    assert (galpon["costo"], galpon["acumulada"], galpon["valor_neto"]) == (D("105000.00"), D("5250.00"),
                                                                            D("99750.00"))
    assert (galpon["destino_cuota"], galpon["vida_util"], len(galpon["cuotas"])) == ("6.1.01.01-30", D("20.00"), 12)
    software = ficha_de(con, s, "1.2.02.02")
    assert (software["acumulada"], software["valor_neto"], software["destino_cuota"]) == \
        (D("1400.00"), D("5600.00"), "7.1.02.07")
    assert ficha_de(con, s, "1.2.02.01")["acumulada"] == D("1675.00")             # constitución 25 %
    assert ficha_de(con, s, "1.2.01.09")["valor_neto"] == D("3300.00")            # Diagnóstico Financiero
    assert ficha_de(con, s, "1.2.01.01")["acumulada"] == D("0.00")
    assert costos.saldos_centro(con, "6.1.01.01", "2027-06-30")["30"] == D("5250.00")
    assert tesoreria.saldo_cuenta(con, "4.1.02.01") == D("-5250.00")
    assert tesoreria.saldo_cuenta(con, "4.1.02.10") == D("-1400.00")
    assert tesoreria.saldo_cuenta(con, "7.1.02.07") == D("1400.00")
    assert all(f["diferencia"] == 0 for f in activos.conciliar_libro(con, s["jf"], "2027-06-30"))
    assert activos.meses_pendientes(con, "2027-06-30") == []
    # Con fichas, la depreciación ya no se registra como CIF manual (Fase 7).
    with pytest.raises(ReglaNegocioError) as e:
        costos.registrar_cif(con, s["jf"], "30", "2027-06-30", "100", "Depreciación galpones")
    assert e.value.codigo == "DEPRECIACION_POR_ACTIVOS"
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE activos SET costo_cent = 1 WHERE codigo_interno = 'AF-GAL-001'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM depreciaciones")


def test_depreciacion_no_entra_a_un_ciclo_ya_costeado(con, s):
    from pruebas.test_costos import ciclo_completo_2026

    cid, _, _ = ciclo_completo_2026(con, s)
    costos.cerrar_ciclo(con, s["jf"], cid)
    activos.registrar_activos_apertura(con, s["jf"])
    with pytest.raises(ReglaNegocioError) as e:
        activos.depreciar_mes(con, s["jf"], 2026, 7)
    assert e.value.codigo == "CICLO_COSTEADO"


# ======================= PLAN CAPEX, ROA, SME Y MAQUINARIA =====================
def test_plan_capex_roa_sme_y_maquinaria_77000_a_sufijo_34(con, s):
    plan = activos.crear_plan(con, s["gg"], 2026, "2026-07-15", importar_d5=True)
    pid = plan["id"]
    assert plan["fuera_de_plazo"] and plan["fecha_limite"].isoformat() == "2026-05-31"
    assert len(plan["lineas"]) == 2
    with pytest.raises(ReglaNegocioError) as e:
        activos.crear_plan(con, s["gg"], 2026, "2026-07-15")
    assert e.value.codigo == "PLAN_EXISTENTE"
    with pytest.raises(AutorizacionError):
        activos.crear_plan(con, s["jf"], 2027, "2027-05-01")
    with pytest.raises(ReglaNegocioError) as e:
        activos.agregar_linea(con, s["gg"], pid, "Camión", "equipos_produccion", "90000", "Distribución", 5,
                              "2027-05-01", fuente="DEUDA_BANCARIA")
    assert e.value.codigo == "FUENTE_NO_PERMITIDA"
    with pytest.raises(ReglaNegocioError) as e:
        activos.presentar_plan(con, s["gg"], pid, "Resumen", "Recomendación")
    assert e.value.codigo == "SIN_ANALISIS"

    det = activos.detalle_plan(con, s["gg"], pid)
    assert det["total"] == D("97000.00") and det["depreciacion_anual"] == D("10625.00")   # 9.625 + 1.000
    maq = next(l for l in det["lineas"] if l["categoria"] == "maquinaria_alimento")
    assert (maq["fuente"], maq["ciclo"], maq["impacto"]["anual"], maq["impacto"]["destino"]) == \
        ("RECURSOS_PROPIOS", 5, D("9625.00"), "6.1.01.02-34")
    assert maq["impacto"]["bs_por_kg"] == D("0.2468")                    # 9.625 ÷ (3.000 × 5 × 2,6 kg) de 2027

    with pytest.raises(AutorizacionError):
        activos.analizar_plan(con, s["gg"], pid, "2026-07-15", "145750")
    r = activos.analizar_plan(con, s["jf"], pid, "2026-07-15", "145750")
    # ROA = 145.750 ÷ (534.300 + 97.000) = 23,09 % (verde > 18 %).
    assert (r["activos_totales"], r["roa"], r["semaforo"]) == (D("534300.00"), D("0.2309"), "VERDE")
    # SME: 138.010 − 97.000 = 41.010 < 136.413 → no compatible.
    assert (r["saldo_post_inversion"], r["sme"], r["compatible_sme"]) == (D("41010.00"), D("136413.00"), False)
    activos.presentar_plan(con, s["gg"], pid, "Planta de alimento", "Aprobar sujeto a la caja del ciclo 5",
                           "2026-07-16")
    with pytest.raises(AutorizacionError):
        activos.resolver_plan_ca(con, s["gg"], pid, "APRUEBA", "Acta")
    assert activos.resolver_plan_ca(con, s["ca_presidente"], pid, "OBSERVA", "Acta CA 2/2026",
                                    "Revisar el flujo del ciclo 5") == "BORRADOR"
    activos.presentar_plan(con, s["gg"], pid, "Planta de alimento", "Ejecutar con excedentes del ciclo 4")
    with pytest.raises(ReglaNegocioError) as e:
        activos.ratificar_plan(con, s["ag"], pid, "Acta AG")
    assert e.value.codigo == "PLAN_NO_APROBADO"
    assert activos.resolver_plan_ca(con, s["ca_presidente"], pid, "APRUEBA", "Acta CA 3/2026") == "APROBADO"
    activos.ratificar_plan(con, s["ag"], pid, "Acta AG 1/2026")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE planes_capex SET resumen = 'x' WHERE id = ?", (pid,))

    l_maq = linea(con, pid, "maquinaria_alimento")
    datos = dict(descripcion="Molino y mezcladora de alimento balanceado", destino="OPERATIVO", precio="75000",
                 flete="1500", instalacion="500", proveedor="Metalmecánica Yungas", documento_ref="FAC-0815")
    # La cuenta 1.2.01.05 y el centro 6.1.01.02 rigen desde 2027 (opera desde el año siguiente).
    with pytest.raises(ReglaNegocioError) as e:
        activos.autorizar_activacion(con, s["gg"], l_maq, fecha_disponible="2027-05-20", **datos)
    assert e.value.codigo == "CATEGORIA_NO_VIGENTE"
    with pytest.raises(ReglaNegocioError) as e:
        activos.autorizar_activacion(con, s["gg"], l_maq, fecha_disponible="2027-07-10",
                                     **{**datos, "destino": "ADMINISTRATIVO"})
    assert e.value.codigo == "DESTINO_NO_CORRESPONDE"
    act = activos.autorizar_activacion(con, s["gg"], l_maq, fecha_disponible="2027-07-10", **datos)
    assert act["costo"] == D("77000.00") and act["variacion"]["variacion"] == D("0.00")
    with pytest.raises(AutorizacionError):
        activos.registrar_activo(con, s["gg"], act["id"])
    ficha = activos.registrar_activo(con, s["jf"], act["id"])
    assert (ficha["codigo_interno"], ficha["destino_cuota"]) == ("AF-MAQ-001", "6.1.01.02-34")
    assert tesoreria.saldo_cuenta(con, "1.2.01.05") == D("77000.00")
    assert tesoreria.saldo_cuenta(con, "2.1.01.06") == D("-77000.00")
    with pytest.raises(ReglaNegocioError):
        activos.registrar_activo(con, s["jf"], act["id"])

    depreciar(con, s, (2027, 7), 12)
    f = activos.ficha_activo(con, s["jf"], ficha["id"])
    assert (f["acumulada"], f["valor_neto"], f["cuotas"][0]["cuota"]) == (D("9625.00"), D("67375.00"), D("802.08"))
    assert costos.saldos_centro(con, "6.1.01.02", "2028-06-30")["34"] == D("9625.00")
    assert tesoreria.saldo_cuenta(con, "4.1.02.04") == D("-9625.00")
    assert activos.detalle_plan(con, s["gg"], pid)["ejecutado"] == D("77000.00")


# ======================= EQUIPO, VARIACIONES Y NO PREVISTOS =====================
def test_equipo_13150_deprecia_1643_75_y_variaciones_pol02(con, s):
    pid = plan_aprobado(con, s, 2027, "2027-05-20")
    assert activos.detalle_plan(con, s["gg"], pid)["fuera_de_plazo"] == 0
    l_eqp, l_gal = linea(con, pid, "equipos_produccion"), linea(con, pid, "galpones")
    act = activos.autorizar_activacion(con, s["gg"], l_eqp, descripcion="Comederos, bebederos y estufas para 1.000 "
                                       "aves", destino="OPERATIVO", precio="13150", fecha_disponible="2027-07-20",
                                       proveedor="Avícola La Paz S.A.", documento_ref="FAC-1001")
    ficha = activos.registrar_activo(con, s["jf"], act["id"])
    assert ficha["destino_cuota"] == "6.1.01.01-31"
    depreciar(con, s, (2027, 7), 12)
    f = activos.ficha_activo(con, s["jf"], ficha["id"])
    assert (f["acumulada"], f["valor_neto"]) == (D("1643.75"), D("11506.25"))
    assert costos.saldos_centro(con, "6.1.01.01", "2028-06-30")["31"] == D("1643.75")

    galpon = dict(descripcion="Galpón 4 (1.000 aves)", destino="OPERATIVO", fecha_disponible="2027-08-10",
                  proveedor="Constructora Caranavi", documento_ref="FAC-2001")
    # +17,14 % (Bs 6.000): se informa al CA, sin Asamblea.
    v = activos.evaluar_variacion(con, l_gal, "41000", "2027-08-10")
    assert (v["variacion_pct"], v["informa_ca"], v["requiere_ag"]) == (D("0.1714"), True, False)
    with pytest.raises(ReglaNegocioError) as e:
        activos.autorizar_activacion(con, s["gg"], l_gal, precio="41000", **galpon)
    assert e.value.codigo == "INFORME_CA_REQUERIDO"
    r = activos.autorizar_activacion(con, s["gg"], l_gal, precio="41000", informe_ca_ref="Nota GG 12/2027", **galpon)
    var = activos.listar_variaciones(con, s["gg"])[0]
    assert (var["informa_ca"], var["requiere_ag"], var["variacion"]) == (1, 0, D("6000.00"))
    activos.anular_activacion(con, s["gg"], r["id"], "Se renegoció el presupuesto")
    # +31,43 % (Bs 11.000 > Bs 10.000): nueva aprobación de la Asamblea.
    with pytest.raises(AutorizacionError) as e:
        activos.autorizar_activacion(con, s["gg"], l_gal, precio="46000", informe_ca_ref="Nota GG 13/2027", **galpon)
    assert e.value.codigo == "AUTORIZACION_REQUERIDA"
    aut = activos.solicitar_variacion(con, s["gg"], l_gal, "46000", "Galpón 4: mayor costo de materiales", "2027-08-05")
    assert seguridad.firmar_autorizacion(con, s["ag"], aut, documento_ref="Acta AG extraordinaria 2/2027") \
        == "AUTORIZADA"
    r = activos.autorizar_activacion(con, s["gg"], l_gal, precio="46000", informe_ca_ref="Nota GG 13/2027",
                                     autorizacion_variacion_id=aut, **galpon)
    assert r["variacion"]["requiere_ag"] is True
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id = ?", aut) == "UTILIZADA"
    # Una variación de 15 % exacto no se informa (la regla es "mayor a").
    assert activos.evaluar_variacion(con, linea(con, pid, "herramientas"), "3726", "2027-08-10")["informa_ca"] is False


def test_inversion_no_prevista_y_saldo_bancario(con, s):
    pid = plan_aprobado(con, s, 2027, "2027-05-20", ratificar=True)
    with pytest.raises(ReglaNegocioError) as e:
        activos.agregar_linea(con, s["gg"], pid, "Balanza de plataforma", "equipos_produccion", "8000", "Pesaje", 2,
                              "2027-09-01")
    assert e.value.codigo == "PLAN_NO_EDITABLE"
    assert activos.requisito_no_prevista(con, "15000", "2027-09-01").firmas == ("CA",)
    assert not activos.requisito_no_prevista(con, "20000", "2027-09-01").autorizacion_especifica
    assert activos.requisito_no_prevista(con, "20000.01", "2027-09-01").autorizacion_especifica
    with pytest.raises(AutorizacionError) as e:
        activos.agregar_no_prevista(con, s["gg"], pid, "Generador eléctrico", "equipos_produccion", "150000",
                                    "Respaldo ante cortes de energía", 2, "2027-09-01", None)
    assert e.value.codigo == "AUTORIZACION_REQUERIDA"
    aut = activos.solicitar_no_prevista(con, s["gg"], "150000", "Generador eléctrico de respaldo", "2027-09-01")
    with pytest.raises(AutorizacionError):
        activos.agregar_no_prevista(con, s["gg"], pid, "Generador eléctrico", "equipos_produccion", "150000",
                                    "Respaldo ante cortes de energía", 2, "2027-09-01", aut)
    seguridad.firmar_autorizacion(con, s["ca_presidente"], aut, documento_ref="Acta CA 9/2027")
    lid = activos.agregar_no_prevista(con, s["gg"], pid, "Generador eléctrico", "equipos_produccion", "150000",
                                      "Respaldo ante cortes de energía", 2, "2027-09-01", aut)
    assert uno(con, "SELECT previsto FROM capex_lineas WHERE id = ?", lid) == 0
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id = ?", aut) == "UTILIZADA"
    datos = dict(descripcion="Generador 20 kVA", destino="OPERATIVO", precio="150000", fecha_disponible="2027-09-10",
                 proveedor="Energía Andina", documento_ref="FAC-3001")
    # Saldo bancario de Bs 138.010 < Bs 150.000: requiere la Asamblea.
    with pytest.raises(ReglaNegocioError) as e:
        activos.autorizar_activacion(con, s["gg"], lid, **datos)
    assert e.value.codigo == "SALDO_BANCARIO_INSUFICIENTE"
    r = activos.autorizar_activacion(con, s["gg"], lid, aprobacion_ag_saldo_ref="Acta AG 4/2027", **datos)
    assert r["saldo_bancario"] == D("138010.00")
    inf = activos.informe_semestral(con, s["gg"], 2027, 1)
    assert inf["filas"][-1]["observacion"].startswith("Inversión no prevista")


def test_plan_sin_aprobar_no_se_ejecuta(con, s):
    pid = activos.crear_plan(con, s["gg"], 2027, "2027-05-20", importar_d5=True)["id"]
    with pytest.raises(ReglaNegocioError) as e:
        activos.autorizar_activacion(con, s["gg"], linea(con, pid, "galpones"), descripcion="Galpón",
                                     destino="OPERATIVO", precio="35000", fecha_disponible="2027-08-01",
                                     proveedor="X", documento_ref="F-1")
    assert e.value.codigo == "PLAN_NO_APROBADO"
    activos.retirar_linea(con, s["gg"], linea(con, pid, "herramientas"), "Se compran en 2029")
    activos.analizar_plan(con, s["jf"], pid, "2027-05-20", "200000")
    assert activos.detalle_plan(con, s["gg"], pid)["total"] == D("48150.00")


# ======================= BAJAS (PR-FIN-07) ====================================
def test_baja_con_verificacion_del_cv_sobre_5000(con, s):
    activos.registrar_activos_apertura(con, s["jf"])
    depreciar(con, s, (2026, 7), 6)                                           # julio a diciembre 2026
    computo = ficha_de(con, s, "1.2.01.09")                                   # 4.400 − 550 = 3.850
    muebles = ficha_de(con, s, "1.2.01.08")                                   # 5.500 − 275 = 5.225
    assert (computo["valor_neto"], muebles["valor_neto"]) == (D("3850.00"), D("5225.00"))
    with pytest.raises(ReglaNegocioError) as e:
        activos.emitir_acta_baja(con, s["gg"], computo["id"], "2027-02-10", "OBSOLESCENCIA", "Equipos sin soporte",
                                 "Reciclaje", "Acta GG 1/2027")
    assert e.value.codigo == "DEPRECIACION_PENDIENTE"                         # falta enero 2027
    with pytest.raises(AutorizacionError):
        activos.emitir_acta_baja(con, s["jf"], computo["id"], "2027-01-15", "OBSOLESCENCIA", "x", "y", "z")
    b1 = activos.emitir_acta_baja(con, s["gg"], computo["id"], "2027-01-15", "OBSOLESCENCIA",
                                  "Equipos sin soporte del fabricante", "Reciclaje electrónico", "Acta GG 1/2027")
    assert (b1["valor_neto"], b1["requiere_cv"], b1["estado"]) == (D("3850.00"), False, "AUTORIZADA")
    r1 = activos.contabilizar_baja(con, s["jf"], b1["id"])
    assert r1["resultado"] == D("-3850.00")
    assert tesoreria.saldo_cuenta(con, "7.2.01.02") == D("3850.00")
    assert (tesoreria.saldo_cuenta(con, "1.2.01.09"), tesoreria.saldo_cuenta(con, "4.1.02.08")) == (D("0.00"), D("0.00"))

    b2 = activos.emitir_acta_baja(con, s["gg"], muebles["id"], "2027-01-20", "VENTA", "Renovación del mobiliario",
                                  "Vendido a un socio", "Acta GG 2/2027", valor_realizacion="6000",
                                  cuenta_cobro="1.1.01.03")
    assert (b2["valor_neto"], b2["requiere_cv"], b2["estado"], b2["limite_cv"]) == \
        (D("5225.00"), True, "PENDIENTE_CV", D("5000"))
    with pytest.raises(AutorizacionError) as e:
        activos.contabilizar_baja(con, s["jf"], b2["id"])
    assert e.value.codigo == "VERIFICACION_CV_PENDIENTE"
    with pytest.raises(AutorizacionError):
        activos.verificar_baja(con, s["gg"], b2["id"], True, "Conforme")
    assert activos.verificar_baja(con, s["cv"], b2["id"], True, "Bien inspeccionado; precio de mercado") == "AUTORIZADA"
    r2 = activos.contabilizar_baja(con, s["jf"], b2["id"])
    assert r2["resultado"] == D("775.00")
    assert tesoreria.saldo_cuenta(con, "5.2.01.05") == D("-775.00")             # ganancia (acreedora)
    assert tesoreria.saldo_cuenta(con, "1.2.01.08") == D("0.00")
    # Tras la baja no se deprecian en enero; el libro sigue conciliado.
    r = activos.depreciar_mes(con, s["jf"], 2027, 1)
    assert r["activos"] == 6
    assert all(f["diferencia"] == 0 for f in activos.conciliar_libro(con, s["jf"], "2027-01-31"))
    assert ficha_de(con, s, "1.2.01.08")["estado"] == "BAJA"
    with pytest.raises(ReglaNegocioError) as e:
        activos.emitir_acta_baja(con, s["gg"], muebles["id"], "2027-02-01", "OTRA", "x", "y", "z")
    assert e.value.codigo == "BAJA_EXISTENTE"


def test_baja_observada_por_el_cv_no_se_contabiliza(con, s):
    activos.registrar_activos_apertura(con, s["jf"])
    galpon = ficha_de(con, s, "1.2.01.02")
    b = activos.emitir_acta_baja(con, s["gg"], galpon["id"], "2026-07-20", "SINIESTRO", "Daño por tormenta",
                                 "Demolición", "Acta GG 3/2026")
    assert b["estado"] == "PENDIENTE_CV"
    assert activos.verificar_baja(con, s["cv"], b["id"], False, "No hay informe técnico del daño") == "OBSERVADA"
    with pytest.raises(ReglaNegocioError):
        activos.contabilizar_baja(con, s["jf"], b["id"])
    with pytest.raises(DatosInvalidosError):
        activos.emitir_acta_baja(con, s["gg"], galpon["id"], "2026-07-20", "INCENDIO", "x", "y", "z")


# ======================= INF-CV-01 Y FIN-TES-04 ================================
def test_verificacion_fisica_y_informe_semestral(con, s):
    activos.registrar_activos_apertura(con, s["jf"])
    assert activos.estado_verificacion(con, "2027-07-01")["vencida"] is True
    vigentes = activos.activos_a_verificar(con, "2027-06-30")
    items = [ItemVerificacion(int(a["id"]), True, "BUENO", "Granja") for a in vigentes]
    with pytest.raises(AutorizacionError):
        activos.registrar_verificacion(con, s["jf"], "2027-06-30", items)
    with pytest.raises(ReglaNegocioError) as e:
        activos.registrar_verificacion(con, s["cv"], "2027-06-30", items[:-1])
    assert e.value.codigo == "VERIFICACION_INCOMPLETA"
    items[0] = ItemVerificacion(int(vigentes[0]["id"]), False, observacion="No se encontró")
    r = activos.registrar_verificacion(con, s["cv"], "2027-06-30", items, "Verificación anual")
    # El software de apertura (20 %) difiere de PC-06 (25 %): el acta lo señala.
    assert (r["activos"], r["faltantes"], r["tasas_distintas"]) == (9, 1, 1)
    assert activos.estado_verificacion(con, "2027-07-01")["vencida"] is False
    acta = activos.obtener_verificacion(con, s["cv"], r["id"])
    assert next(d for d in acta["detalle"] if d["categoria"] == "software")["tasa_coincide"] == 0

    pid = plan_aprobado(con, s, 2027, "2027-05-20")
    act = activos.autorizar_activacion(con, s["gg"], linea(con, pid, "equipos_produccion"), descripcion="Equipo",
                                       destino="OPERATIVO", precio="13150", fecha_disponible="2027-07-20",
                                       proveedor="Avícola La Paz S.A.", documento_ref="FAC-1001")
    activos.registrar_activo(con, s["jf"], act["id"])
    inf = activos.informe_semestral(con, s["gg"], 2027, 1)
    assert (inf["desde"].isoformat(), inf["hasta"].isoformat(), inf["mes_presentacion"].isoformat()) == \
        ("2027-07-01", "2027-12-31", "2028-01-01")
    assert (inf["aprobado"], inf["ejecutado"], inf["ejecucion_pct"]) == (D("51390.00"), D("13150.00"), D("0.2559"))
    with pytest.raises(ReglaNegocioError) as e:
        activos.registrar_informe_semestral(con, s["gg"], 2027, 1, "2027-12-15")
    assert e.value.codigo == "SEMESTRE_ABIERTO"
    reg = activos.registrar_informe_semestral(con, s["gg"], 2027, 1, "2028-01-10", "Ejecución del primer semestre")
    assert reg["fuera_de_plazo"] is False
    with pytest.raises(ReglaNegocioError):
        activos.registrar_informe_semestral(con, s["gg"], 2027, 1, "2028-01-11")
    s2 = activos.informe_semestral(con, s["gg"], 2027, 2)
    assert (s2["desde"].isoformat(), s2["mes_presentacion"].isoformat()) == ("2028-01-01", "2028-07-01")


def test_segregacion_de_funciones(con, s):
    with pytest.raises(AutorizacionError):
        activos.registrar_activos_apertura(con, s["gg"])
    with pytest.raises(AutorizacionError):
        activos.depreciar_mes(con, s["ut"], 2026, 7)
    with pytest.raises(AutorizacionError):
        activos.crear_plan(con, s["cv"], 2027, "2027-05-01")
    assert activos.libro_activos(con, s["ut"]) == []
    with pytest.raises(DatosInvalidosError):
        activos.depreciar_mes(con, s["jf"], 2026, 13)


# ======================= FORMATOS PDF ========================================
def test_formatos_pdf(con, s):
    activos.registrar_activos_apertura(con, s["jf"])
    depreciar(con, s, (2026, 7), 2)
    ctx = pdf.Contexto(fecha="2026-09-01", elaborado_por="Jefe de Finanzas", cargo="JF")
    pid = plan_aprobado(con, s, 2027, "2027-05-20")
    plan = activos.detalle_plan(con, s["gg"], pid)
    act = activos.autorizar_activacion(con, s["gg"], linea(con, pid, "equipos_produccion"), descripcion="Equipo",
                                       destino="OPERATIVO", precio="13150", fecha_disponible="2027-07-20",
                                       proveedor="Avícola La Paz S.A.", documento_ref="FAC-1001")
    galpon = ficha_de(con, s, "1.2.01.02")
    b = activos.emitir_acta_baja(con, s["gg"], galpon["id"], "2026-09-15", "SINIESTRO", "Daño", "Demolición",
                                 "Acta GG 4/2026")
    activos.registrar_verificacion(con, s["cv"], "2026-09-30", [ItemVerificacion(int(a["id"]), True, "BUENO")
                                                                for a in activos.activos_a_verificar(con, "2026-09-30")])
    documentos = [
        pdf.fin_tes_01(plan, ctx), pdf.fin_tes_02(plan, ctx), pdf.fin_tes_03(plan, ctx),
        pdf.fin_tes_04(activos.informe_semestral(con, s["gg"], 2027, 1), ctx),
        pdf.fin_tra_08(activos.listar_activaciones(con, s["gg"])[0], ctx),
        pdf.fin_tra_09(activos.ficha_activo(con, s["jf"], galpon["id"]), ctx),
        pdf.fin_tra_10(activos.planilla_depreciacion(con, s["jf"], 2026, 8), ctx),
        pdf.acta_baja(activos.obtener_baja(con, s["gg"], b["id"]), ctx),
        pdf.inf_cv_01(activos.obtener_verificacion(con, s["cv"], 1), ctx),
        pdf.libro_inventario(activos.libro_activos(con, s["jf"], "2026-09-30"), ctx),
    ]
    assert act["id"] > 0
    for doc in documentos:
        assert doc.startswith(b"%PDF") and b"P\\341gina 1 de" in doc, documentos.index(doc)
