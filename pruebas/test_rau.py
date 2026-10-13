"""Pruebas de la Fase 11: RAU y cumplimiento legal y fiscal (servicios/rau.py).

Casos de borde tomados de los documentos:
* PET-RAU §1.4 y §5: autoconsumo proyectado 2030 de 63,9 % → amarillo.
* POL-07 Art. 2: Formulario 701 con menos de 15 días hábiles → amarillo.
* DS 24463 Art. 7 / PET-RAU §1.4: cualquier servicio a terceros → rojo.
* Manual Presupuestario P-03: 8 ha × Bs 3,03 = Bs 24,24; Bs 2,02 por socio (12 socios).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from servicios import rau, tesoreria, ventas
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError

BANCO = "1.1.01.03"


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def fondear(con, s, fecha="2026-07-10", monto="1000"):
    tesoreria.registrar_deposito(con, s["jf"], fecha, monto, BANCO, "5.1.01.01", "Venta de carne de pollo")


def kardex(con, fecha, tipo, qq):
    con.execute("INSERT INTO kardex_terminados (producto, fecha, tipo, cantidad_mil, valor_cent, saldo_cantidad_mil, "
                "saldo_valor_cent, cpp, registrado_por, registrado_en) VALUES ('ALIMENTO',?,?,?,0,?,0,'0','test',?)",
                (fecha, tipo, int(qq * 1000), int(qq * 1000), fecha))


# ============================ CALENDARIO =====================================
def test_calendario_fechas_y_alertas_30_60(con, s):
    f = rau.fechas_calendario(con, 2026)
    assert f["VENCIMIENTO"] == date(2027, 10, 31)
    assert f["CONSULTA_RND"] == date(2027, 9, 30)
    assert f["FORM_701"] == date(2027, 10, 8) == f["PATENTES"]          # 15 días hábiles completos antes
    assert f["LIQUIDACION_SOCIOS"] == date(2027, 10, 1)                 # 20 días hábiles completos antes
    assert f["ACTA_COMPATIBILIDAD"] == date(2027, 6, 30)
    cal = {e["evento"]: e for e in rau.calendario_fiscal(con, s["jf"], 2026, "2027-09-01")}
    assert cal["CONSULTA_RND"]["estado"] == "ALERTA_30"                 # 29 días
    assert cal["FORM_701"]["estado"] == "ALERTA_60"                     # 37 días
    assert cal["ACTA_COMPATIBILIDAD"]["estado"] == "VENCIDO" and cal["ACTA_COMPATIBILIDAD"]["semaforo"] == "ROJO"
    lejano = {e["evento"]: e for e in rau.calendario_fiscal(con, s["jf"], 2026, "2027-05-01")}
    assert lejano["FORM_701"]["estado"] == "PROGRAMADO"
    alertas = rau.alertas_calendario(con, s["ut"], "2027-09-01")
    assert any("Formulario 701" in a["mensaje"] and "vence en 37" in a["mensaje"] for a in alertas)


def test_form_701_con_menos_de_15_dias_habiles_marca_amarillo(con):
    assert rau.evaluar_701(con, 2026, "2027-10-01")["semaforo"] == "VERDE"
    al_limite = rau.evaluar_701(con, 2026, "2027-10-08")
    assert (al_limite["dias_habiles_antes"], al_limite["semaforo"]) == (15, "VERDE")
    tarde = rau.evaluar_701(con, 2026, "2027-10-11")
    assert (tarde["dias_habiles_antes"], tarde["semaforo"]) == (14, "AMARILLO")
    assert rau.evaluar_701(con, 2026, "2027-10-29")["semaforo"] == "AMARILLO"
    assert rau.evaluar_701(con, 2026, "2027-11-02")["semaforo"] == "ROJO"


# ============================ CUOTA Y FORMULARIO 701 =========================
def test_cuota_8_ha_por_3_03_reparto_en_12_y_advertencia_visible(con):
    c = rau.calcular_cuota(con, 2026)
    assert (c["hectareas"], c["cuota_ha"], c["total"]) == (Decimal("8"), Decimal("3.03"), Decimal("24.24"))
    assert c["socios"] == 12 and c["por_socio"] == Decimal("2.02")
    assert sum(r["monto"] for r in c["reparto"]) == Decimal("24.24")
    texto = " ".join(c["advertencias"])
    assert "Cuota POR VALIDAR" in texto and "Superficie POR VALIDAR" in texto and "10 ha" in texto


def test_cuota_con_10_ha_declaradas_reparte_centavos_sobrantes(con, s):
    rau.registrar_superficie_declarada(con, s["jf"], "2026-07-05", "10", "Yungas - Caranavi", "Constancia SIN 001")
    c = rau.calcular_cuota(con, 2026)
    assert c["total"] == Decimal("30.30")
    montos = sorted(r["monto"] for r in c["reparto"])
    assert montos.count(Decimal("2.53")) == 6 and montos.count(Decimal("2.52")) == 6
    assert not any("Superficie POR VALIDAR" in a for a in c["advertencias"])


def test_liquidacion_asiento_7_1_05_02_contra_2_1_03_01_y_form_701(con, s):
    fondear(con, s)
    liq = rau.registrar_liquidacion(con, s["jf"], 2026, "2027-06-20", fecha_devengo="2027-06-30")
    assert liq["asiento"] and liq["total"] == Decimal("24.24")
    lineas = con.execute("SELECT d.cuenta_codigo, d.debe_cent, d.haber_cent FROM asiento_detalle d JOIN asientos a "
                         "ON a.id = d.asiento_id WHERE a.documento_ref = 'RAU-2026' ORDER BY d.debe_cent DESC").fetchall()
    assert [tuple(x) for x in lineas] == [("7.1.05.02", 2424, 0), ("2.1.03.01", 0, 2424)]
    assert uno(con, "SELECT COUNT(*) FROM liquidaciones_rau_socios") == 12
    com = rau.comunicar_liquidacion(con, s["jf"], 2026, "2027-09-28")
    assert com["dentro_plazo"] is True
    with pytest.raises(ReglaNegocioError):
        rau.registrar_liquidacion(con, s["jf"], 2026, "2027-06-21")

    r = rau.registrar_formulario_701(con, s["jf"], 2026, "2027-10-11", "701-000123", "Boleta BNB 555")
    assert r["semaforo"] == "AMARILLO" and r["dias_habiles_antes"] == 14
    assert tesoreria.saldo_cuenta(con, "2.1.03.01", "2027-10-31") == Decimal("0.00")
    cal = {e["evento"]: e for e in rau.calendario_fiscal(con, s["jf"], 2026, "2027-10-12")}
    assert cal["FORM_701"]["semaforo"] == "AMARILLO" and cal["FORM_701"]["estado"] == "CUMPLIDO_CON_OBSERVACION"
    assert cal["LIQUIDACION_SOCIOS"]["estado"] == "CUMPLIDO"
    idx = rau.indice_maestro(con, s["jf"], 2026)
    cubiertos = {i["clave"] for c in idx["carpetas"] for i in c["items"] if i["cubierto"]}
    assert {"B01", "B03"} <= cubiertos
    anual = rau.verificaciones_anuales(con, 2026)
    assert anual[0]["cumple"] is False and "14 días hábiles" in anual[0]["observacion"]


def test_patentes_y_consulta_rnd(con, s):
    fondear(con, s)
    r = rau.registrar_consulta_rnd(con, s["jf"], 2026, "2027-09-15", "RND 102700000010", "3.15")
    assert r["dentro_plazo"] and "actualizar el parámetro" in r["advertencia"]
    assert any("actualice el parámetro" in a for a in rau.calcular_cuota(con, 2026)["advertencias"])
    p = rau.registrar_patente(con, s["jf"], 2026, "Caranavi", "2027-10-05", "150", "Recibo GAM 77")
    assert p["dentro_plazo"] is True
    assert uno(con, "SELECT COUNT(*) FROM asiento_detalle WHERE cuenta_codigo = '7.1.05.01'") == 1
    with pytest.raises(ReglaNegocioError):
        rau.registrar_patente(con, s["jf"], 2026, "Caranavi", "2027-10-06", "10", "Recibo GAM 78")


def test_permisos_rau(con, s):
    with pytest.raises(AutorizacionError):
        rau.registrar_liquidacion(con, s["cv"], 2026, "2027-06-20")
    with pytest.raises(AutorizacionError):
        rau.calendario_fiscal(con, s["ag"], 2026)
    with pytest.raises(AutorizacionError):
        rau.aprobar_programa_excedente(con, s["jf"], 2027, "100", canal_socios=True, canal_no_socios=False,
                                       acta_ca_ref="Acta CA 1", fecha="2027-07-05")
    assert rau.calendario_fiscal(con, s["ut"], 2026)


# ============================ SUPERFICIE Y CAPEX =============================
def test_control_de_superficie_ante_capex(con, s):
    rau.registrar_superficie_usada(con, s["jf"], "Galpones 1 y 2", "6", "2026-07-01")
    assert rau.controlar_capex(con, s["jf"], "2026-08-01", "1", "Galpón 3")["semaforo"] == "VERDE"
    amarillo = rau.controlar_capex(con, s["jf"], "2026-08-01", "3", "Planta de alimento")
    assert amarillo["semaforo"] == "AMARILLO" and "actualizar ante el SIN" in amarillo["accion"]
    rau.registrar_superficie_usada(con, s["jf"], "Depósitos", "3", "2026-08-15")
    rojo = rau.controlar_capex(con, s["jf"], "2026-09-01", "0", "Silo")
    assert rojo["semaforo"] == "ROJO" and rojo["usada"] == Decimal("9")
    with pytest.raises(DatosInvalidosError):
        rau.controlar_capex(con, s["jf"], "2026-09-01", "1", "X", capex_linea_id=999999)


# ============================ INDICADORES ====================================
def test_autoconsumo_63_9_por_ciento_2030_marca_amarillo(con):
    i1 = rau.evaluar_i1(con, "4693", "3000", 2030)
    assert i1["valor"] == Decimal("0.6392") and i1["semaforo"] == "AMARILLO"
    assert rau.evaluar_i1(con, "4693", "3051", 2030)["semaforo"] == "VERDE"
    assert rau.evaluar_i1(con, "4693", "2800", 2030)["semaforo"] == "ROJO"
    assert rau.evaluar_i1(con, "4693", "3000", 2026)["semaforo"] is None        # sin alimento propio en 2026
    with pytest.raises(DatosInvalidosError):
        rau.evaluar_i1(con, "100", "101", 2030)


def test_i2_umbral_15_y_20_por_ciento(con):
    assert rau.evaluar_i2(con, "15000", "100000", 2028)["semaforo"] == "VERDE"
    assert rau.evaluar_i2(con, "15010", "100000", 2028)["semaforo"] == "AMARILLO"
    assert rau.evaluar_i2(con, "20000", "100000", 2028)["semaforo"] == "AMARILLO"
    assert rau.evaluar_i2(con, "20010", "100000", 2028)["semaforo"] == "ROJO"


# ============================ BALANCE DE MASAS Y EXCEDENTE ===================
def test_balance_de_masas_programa_y_venta_de_excedente(con, s):
    kardex(con, "2027-07-15", "ENTRADA_PRODUCCION", 1000)
    kardex(con, "2027-08-15", "SALIDA_CONSUMO_PROPIO", 650)
    assert rau.calcular_balance_masas(con, 2027, 1, "40")["merma_semaforo"] == "AMARILLO"
    assert rau.calcular_balance_masas(con, 2027, 1, "60")["merma_semaforo"] == "ROJO"
    b = rau.preparar_balance_masas(con, s["jf"], 2027, 1, merma_qq="20", consumo_proyectado_qq="100")
    assert (b["excedente"], b["merma_semaforo"], b["autorizable"]) == (Decimal("330"), "VERDE", Decimal("230"))
    with pytest.raises(ReglaNegocioError, match="R6"):
        rau.firmar_balance_masas(con, s["gg"], b["id"], "200")
    rau.aprobar_programa_excedente(con, s["ca_presidente"], 2027, "150", canal_socios=True, canal_no_socios=True,
                                   acta_ca_ref="Acta CA 7/2027", fecha="2027-07-05")
    with pytest.raises(ReglaNegocioError, match="R5"):
        rau.firmar_balance_masas(con, s["gg"], b["id"], "231")
    with pytest.raises(ReglaNegocioError, match="tope"):
        rau.firmar_balance_masas(con, s["gg"], b["id"], "200")
    rau.firmar_balance_masas(con, s["gg"], b["id"], "150")

    agri = ventas.registrar_cliente(con, s["jf"], "AGRICULTOR", "Juan Quispe (cafetal)", "2027-07-01")["id"]
    with pytest.raises(ReglaNegocioError, match="R3"):
        rau.verificar_venta_excedente(con, agri, "2027-10-05", "10")
    rau.autorizar_comprador(con, s["gg"], agri, 2027, comunidad="Villa Elvira", actividad="Cafetal y gallinas",
                            fecha="2027-07-10")
    assert rau.verificar_venta_excedente(con, agri, "2027-10-05", "10")["autorizado"] == Decimal("150")
    with pytest.raises(ReglaNegocioError, match="R4"):
        rau.verificar_venta_excedente(con, agri, "2027-10-05", "10", dias_credito=7)
    with pytest.raises(ReglaNegocioError, match="R6"):
        rau.verificar_venta_excedente(con, agri, "2027-10-05", "151")
    padron = rau.padron_compradores(con, s["jf"], 2027)
    assert any(c["condicion"] == "No socio autorizado" for c in padron["compradores"])


# ============================ SERVICIOS A TERCEROS E INF-CV-09 ===============
def test_servicio_a_terceros_marca_rojo_y_escala(con, s):
    r = rau.registrar_servicio_tercero(con, s["cv"], "2027-08-02", "MAQUILA", "Vecino Mamani",
                                       "Molienda de maíz de un tercero en la planta")
    assert r["semaforo"] == "ROJO" and "ALERTA ROJA" in r["mensaje"]
    assert r["limite_ca"] == "2027-08-10"                         # 5 días hábiles (6-ago es feriado nacional)
    assert any(a["nivel"] == "ROJO" and a["origen"] == "Servicio a terceros" for a in rau.alertas_rau(con, s["gg"],
                                                                                                      "2027-08-03"))
    rev = rau.registrar_revision_trimestral(con, s["cv"], 2027, 3, "2027-10-05", compras_sin_respaldo_pct="0",
                                            padron_atraso_trimestres=0)
    v4 = rev["verificaciones"][3]
    assert len(rev["verificaciones"]) == 10 and v4["semaforo"] == "ROJO" and v4["resultado"] == "1 caso(s)"
    assert rev["semaforo"] == "ROJO" and rev["escalamiento"]["ca_hasta"]
    with pytest.raises(ReglaNegocioError):
        rau.resolver_servicio_ca(con, s["ca_presidente"], r["id"], "2027-08-05", "Acta CA 9", "Suspender maquila")
    rau.informar_servicio_gg(con, s["gg"], r["id"], "2027-08-02")
    rau.resolver_servicio_ca(con, s["ca_presidente"], r["id"], "2027-08-05", "Acta CA 9", "Suspender maquila")
    assert rau.listar_servicios(con, s["jf"])[0]["estado"] == "CERRADO"
    with pytest.raises(ReglaNegocioError):
        rau.registrar_revision_trimestral(con, s["cv"], 2027, 3, "2027-10-06", compras_sin_respaldo_pct="0",
                                          padron_atraso_trimestres=0)


def test_revision_trimestral_semaforos_de_entradas_del_cv(con, s):
    v = rau.verificaciones_trimestrales(con, 2026, 3, compras_sin_respaldo_pct="0.05", padron_atraso_trimestres=2)
    assert [x["numero"] for x in v] == list(range(1, 11))
    assert v[5]["semaforo"] == "AMARILLO" and v[6]["semaforo"] == "ROJO" and v[3]["semaforo"] == "VERDE"
    assert rau.verificaciones_trimestrales(con, 2026, 3, compras_sin_respaldo_pct="0.11")[5]["semaforo"] == "ROJO"
    with pytest.raises(DatosInvalidosError):
        rau.registrar_revision_trimestral(con, s["cv"], 2026, 3, "2026-09-15", compras_sin_respaldo_pct="0",
                                          padron_atraso_trimestres=0)
    with pytest.raises(AutorizacionError):
        rau.registrar_revision_trimestral(con, s["jf"], 2026, 3, "2026-10-05", compras_sin_respaldo_pct="0",
                                          padron_atraso_trimestres=0)


# ============================ EXPEDIENTE EXP-RAU ============================
def test_indice_maestro_y_completitud(con, s):
    idx = rau.indice_maestro(con, s["jf"], 2026)
    assert idx["total"] == 39 and idx["cubiertos"] == 0 and idx["semaforo"] == "AMARILLO"
    for clave in ("A01", "A02", "A03"):
        rau.registrar_documento_expediente(con, s["jf"], clave, 2026, "2026-07-01", f"Documento {clave}",
                                           "Estante 1")
    idx = rau.indice_maestro(con, s["jf"], 2026)
    assert idx["cubiertos"] == 3 and idx["completitud"] == Decimal("0.0769")
    doc = idx["carpetas"][0]["items"][0]["documentos"][0]
    assert doc["archivo_digital"].startswith("2026-07-01_A_")
    with pytest.raises(DatosInvalidosError):
        rau.registrar_documento_expediente(con, s["jf"], "Z99", 2026, "2026-07-01", "x")


# ============================ FISCALIZACIÓN ==================================
def test_plan_de_respuesta_ante_fiscalizacion(con, s):
    plan = rau.registrar_notificacion(con, s["jf"], fecha_recepcion="2027-03-01", entidad="GRACO",
                                      tipo="REQUERIMIENTO", numero="REQ-2027-15", funcionario="Lic. Pérez",
                                      gestiones=[2026], vencimiento="2027-03-11", escenario="A")
    nid = plan["notificacion"]["id"]
    assert [p["paso"] for p in plan["pasos"]] == ["INFORME_24H", "ANALISIS", "RESPUESTA"]
    assert plan["pasos"][1]["limite"] == "2027-03-04" and plan["escenario"]["codigo"] == "A"
    assert any("GRACO" in a for a in plan["advertencias"])
    assert len(plan["carpeta_respuesta_rapida"]) == 11
    rau.registrar_accion_fiscalizacion(con, s["jf"], nid, "INFORME_24H", "2027-03-02", "Informe a GG, CA y CV")
    with pytest.raises(DatosInvalidosError):
        rau.registrar_accion_fiscalizacion(con, s["jf"], nid, "PRORROGA", "2027-03-03", "Prórroga", "Nota 1",
                                           "2027-03-10")
    rau.registrar_accion_fiscalizacion(con, s["jf"], nid, "PRORROGA", "2027-03-03", "Prórroga", "Nota 1", "2027-03-18")
    with pytest.raises(ReglaNegocioError):
        rau.cerrar_notificacion(con, s["cv"], nid, "2027-03-20", "Sin respuesta aún")
    rau.registrar_accion_fiscalizacion(con, s["jf"], nid, "RESPUESTA", "2027-03-15", "Entrega foliada", "Cargo 22")
    plan = rau.plan_respuesta(con, s["jf"], nid, "2027-03-16")
    assert plan["notificacion"]["vencimiento"] == "2027-03-18"
    assert [p["estado"] for p in plan["pasos"]] == ["CUMPLIDO", "VENCIDO", "CUMPLIDO"]
    rau.cerrar_notificacion(con, s["cv"], nid, "2027-03-25", "Mantener carpetas D y E al día")
    with pytest.raises(ReglaNegocioError):
        rau.registrar_accion_fiscalizacion(con, s["jf"], nid, "RESPUESTA", "2027-03-26", "Otra")
    rau.registrar_revision_buzon(con, s["jf"], "2027-03-26", False)
    with pytest.raises(DatosInvalidosError):
        rau.registrar_revision_buzon(con, s["jf"], "2027-03-27", True)


def test_informe_anual_y_acta_de_compatibilidad(con, s):
    rau.registrar_certificado(con, s["jf"], "RAU-0001/2026", "2026-07-02")
    rau.registrar_acta_compatibilidad(con, s["ca_presidente"], 2026, "2027-06-15", "Acta CA 12/2027",
                                      "Sin actividades incompatibles")
    inf = rau.registrar_informe_anual(con, s["cv"], 2026, "2027-11-10")
    cumple = [v["cumple"] for v in inf["verificaciones"]]
    assert cumple == [False, False, False, True, True] and inf["semaforo"] != "VERDE"
    assert rau.certificado_vigente(con, "2027-01-01")["numero"] == "RAU-0001/2026"


# ============================ FORMATOS PDF ===================================
def test_formatos_pdf_rau_y_archivo(con, s):
    from reportes import formatos_rau as rep
    from reportes.formatos_tesoreria import Contexto
    from servicios import archivo

    ctx = Contexto(fecha=date(2027, 10, 15), elaborado_por=s["jf"].nombre_completo, cargo="JF")
    pdfs = [rep.calendario_fiscal(2026, rau.calendario_fiscal(con, s["jf"], 2026, "2027-09-01"), ctx)]
    rau.registrar_liquidacion(con, s["jf"], 2026, "2027-06-20", fecha_devengo="2027-06-30")
    pdfs.append(rep.liquidacion_rau(rau.liquidacion(con, s["jf"], 2026), ctx))
    rev = rau.registrar_revision_trimestral(con, s["cv"], 2027, 3, "2027-10-05", compras_sin_respaldo_pct="0",
                                            padron_atraso_trimestres=0)
    pdfs.append(rep.inf_cv_09_trimestral({**rev, "anio": 2027, "trimestre": 3}, ctx))
    anual = rau.registrar_informe_anual(con, s["cv"], 2026, "2027-11-10")
    pdfs.append(rep.inf_cv_09_anual({**anual, "anio": 2026}, ctx))
    kardex(con, "2027-07-15", "ENTRADA_PRODUCCION", 500)
    kardex(con, "2027-08-15", "SALIDA_CONSUMO_PROPIO", 400)
    rau.preparar_balance_masas(con, s["jf"], 2027, 1, merma_qq="5", consumo_proyectado_qq="50")
    pdfs.append(rep.fin_tra_13(rau.listar_balances(con, s["jf"], 2027)[0], ctx))
    pdfs.append(rep.indice_maestro(rau.indice_maestro(con, s["jf"], 2026), ctx))
    e = archivo.abrir_expediente(con, s["jf"], ciclo="TES", gestion=2026, titulo="Plan CAPEX", procedimiento="PR-FIN-01",
                                 codigo_documental="FIN-TES-01")
    archivo.cerrar_expediente(con, s["jf"], e["id"], "2027-06-30")
    acta = archivo.preparar_acta(con, s["jf"], "TRANSFERENCIA", "2027-08-01", "Archivo histórico", [e["id"]])
    pdfs.append(rep.acta_archivo(archivo.acta(con, s["jf"], acta["id"]), ctx))
    for pdf in pdfs:
        assert pdf.startswith(b"%PDF") and b"P\\341gina 1 de" in pdf


def test_nota_de_entrega_de_excedente_exige_programa_rau(con, s):
    agri = ventas.registrar_cliente(con, s["jf"], "AGRICULTOR", "Juan Quispe (cafetal)", "2027-07-01")["id"]
    with pytest.raises(ReglaNegocioError) as exc:
        ventas.registrar_nota(con, s["jf"], agri, "2027-10-05", "EXCEDENTE", "10", hora_entrega="10:30",
                              medio_pago="EFECTIVO", comprobante_ref="REC-1", certificado_rau_exhibido=True,
                              certificado_rau_ref="Certificado RAU N.º 0001/2026", recibido_por="Juan Quispe",
                              entregado_por="Encargado de Granja")
    assert exc.value.codigo == "SIN_PROGRAMA_EXCEDENTE"
