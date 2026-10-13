"""Pruebas de servicios/inventarios.py (Fase 4).

Cifras de los documentos:
* PC-07 §V.B: CPP = (saldo anterior Bs + entradas Bs) / (unidades anteriores +
  unidades de la entrada), recalculado en cada entrada.
* Manual Presupuestario B.2/P-05: alimento comprado Bs 185/qq; 0,10 qq por
  ave × 3.000 aves = 300 qq por ciclo de 73 días (consumo proyectado
  4,1096 qq/día); pollitos BB Bs 9 por ave.
* CI-FIN-16: PR = consumo diario × días de reposición + stock de seguridad
  (7 días alimento y BB; 5 días veterinarios y limpieza).
* PC-07: rotación máxima de granos 30 días; baja con visto del CV sobre
  Bs 2.000; veterinarios con menos de 30 días de vigencia se rechazan.
* POA: C1 1-jul a 11-sep-2026; C2 12-sep a 23-nov-2026.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from pruebas.ayuda_compras import (
    LUNES, LUNES_2, MIERCOLES, VIERNES, D, comprar, depositar, insumo, tres_proveedores, uno,
)
from reportes import formatos_compras as pdf
from servicios import compras, contable, inventarios, tesoreria
from servicios.compras import LineaOC
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.inventarios import Saldo

CONSUMO_ALIMENTO = D("300") / D("73")          # qq/día proyectado (Manual Presupuestario)


def preparar_pr_alimento(con, s, dias_reposicion=3, fecha="2026-07-01"):
    alimento = insumo(con, "INS-0302")
    inventarios.actualizar_insumo(con, s["jf"], alimento, dias_reposicion=dias_reposicion)
    inventarios.recalcular_punto_reorden(con, s["jf"], alimento, fecha, consumo_diario_proyectado=CONSUMO_ALIMENTO)
    return alimento


# ================================ CALENDARIO =================================
def test_ciclo_de_fecha_segun_poa(con):
    casos = {"2026-07-01": (1, "2026-07-01", "2026-09-11"), "2026-09-11": (1, "2026-07-01", "2026-09-11"),
             "2026-09-12": (2, "2026-09-12", "2026-11-23"), "2026-11-23": (2, "2026-09-12", "2026-11-23"),
             "2027-06-30": (5, "2027-04-19", "2027-06-30")}
    for fecha, (numero, inicio, fin) in casos.items():
        c = inventarios.ciclo_de_fecha(con, fecha)
        assert (c["ciclo"], c["inicio"].isoformat(), c["fin"].isoformat()) == (numero, inicio, fin)
    assert inventarios.ciclo_de_fecha(con, "2028-02-29")["ejercicio"] == 2027


# ================================== CPP ======================================
def test_cpp_con_dos_entradas_a_precios_distintos(con, s):
    depositar(con, s, "100000")
    provs = tres_proveedores(con, s)
    alimento = insumo(con, "INS-0302")
    comprar(con, s, "INS-0302", 100, 185, LUNES, provs)
    comprar(con, s, "INS-0302", 100, 190, LUNES_2, provs)
    saldo = inventarios.saldo_insumo(con, alimento)
    # CPP = (18.500 + 19.000) / (100 + 100) = 187,50
    assert (saldo.cantidad, saldo.valor, saldo.cpp) == (D("200.000"), D("37500.00"), D("187.5000"))
    salida = inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-14", 50)
    assert salida["valor"] == D("9375.00")
    asiento = contable.obtener_asiento(con, s["jf"], salida["asiento_id"])
    assert {(l["cuenta"], l["sufijo"], l["debe"], l["haber"]) for l in asiento["lineas"]} == {
        ("6.1.01.01", "02", D("9375.00"), D("0.00")), ("1.1.03.02", None, D("0.00"), D("9375.00"))}
    # Tercera entrada: CPP = (28.125 + 10.000) / (150 + 50) = 190,625
    comprar(con, s, "INS-0302", 50, 200, "2026-07-20", provs)
    saldo = inventarios.saldo_insumo(con, alimento)
    assert (saldo.cantidad, saldo.valor, saldo.cpp) == (D("200.000"), D("38125.00"), D("190.6250"))
    movimientos = inventarios.kardex(con, s["jf"], alimento)
    assert [m["tipo"] for m in movimientos] == ["ENTRADA_COMPRA", "ENTRADA_COMPRA", "SALIDA_CONSUMO", "ENTRADA_COMPRA"]
    assert [m["cpp"] for m in movimientos] == ["185.0000", "187.5000", "187.5000", "190.6250"]
    assert tesoreria.saldo_cuenta(con, "1.1.03.02") == D("38125.00")
    assert all(f["diferencia"] == 0 for f in inventarios.conciliar_con_contabilidad(con, s["jf"]))
    with pytest.raises(sqlite3.DatabaseError):      # kárdex inmutable
        con.execute("DELETE FROM kardex")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE kardex SET valor_cent = 1")


def test_valoracion_sin_residuos_de_redondeo():
    saldo = Saldo(3000, D("10.00"), inventarios.calcular_cpp(D("10.00"), 3000), None)
    assert saldo.cpp == D("3.3333")
    assert inventarios.valorar_salida(saldo, 1000) == D("3.33")
    assert inventarios.valorar_salida(saldo, 3000) == D("10.00")          # agota: valor remanente exacto
    with pytest.raises(ReglaNegocioError):
        inventarios.valorar_salida(saldo, 3001)
    assert inventarios.a_mil("41.0955") == 41096 and inventarios.formato_cantidad(1234500, "qq") == "1.234,5 qq"
    with pytest.raises(DatosInvalidosError):
        inventarios.a_mil(0)


def test_salidas_validaciones(con, s):
    depositar(con, s, "100000")
    alimento = insumo(con, "INS-0302")
    comprar(con, s, "INS-0302", 10, 185)
    with pytest.raises(ReglaNegocioError) as e:
        inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-07", 11)
    assert e.value.codigo == "EXISTENCIA_INSUFICIENTE"
    with pytest.raises(ReglaNegocioError) as e:
        inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-05", 1)
    assert e.value.codigo == "KARDEX_FECHA_ANTERIOR"
    with pytest.raises(AutorizacionError):           # la UT no registra asientos
        inventarios.registrar_salida(con, s["ut"], alimento, "2026-07-07", 1)
    with pytest.raises(ReglaNegocioError):
        inventarios.registrar_salida(con, s["jf"], insumo(con, "INS-0312"), "2026-07-07", 1)
    # Embalaje: sufijo 12 (bolsas) en carne y 13 (saquillos) en alimento; mantenimiento usa el 36.
    assert inventarios._sufijo_por_defecto(con, "1.1.03.11", "6.1.01.01") == "12"
    assert inventarios._sufijo_por_defecto(con, "1.1.03.11", "6.1.01.02") == "13"
    assert inventarios._sufijo_por_defecto(con, "1.1.03.15", "6.1.01.01") == "36"
    with pytest.raises(DatosInvalidosError) as e:
        inventarios._sufijo_por_defecto(con, "1.1.03.12", "6.1.01.01")
    assert e.value.codigo == "SUFIJO_REQUERIDO"
    inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-07", 10)
    assert inventarios.saldo_insumo(con, alimento).valor == D("0.00")


def test_pollitos_bb_cuenta_transitoria(con, s):
    depositar(con, s, "100000")
    comprar(con, s, "INS-0301", 3000, 9)
    bb = insumo(con, "INS-0301")
    assert inventarios.saldo_insumo(con, bb).cantidad_mil == 0
    assert tesoreria.saldo_cuenta(con, "1.1.03.01") == D("0.00")
    assert contable.libro_mayor(con, s["jf"], "6.1.01.01", sufijo="01")["saldo_final"] == D("27000.00")
    assert [m["tipo"] for m in inventarios.kardex(con, s["jf"], bb)] == ["ENTRADA_COMPRA", "SALIDA_CONSUMO"]


# ============================ PUNTO DE REORDEN ===============================
def test_punto_de_reorden_ci_fin_16(con, s):
    alimento = insumo(con, "INS-0302")
    with pytest.raises(ReglaNegocioError) as e:
        inventarios.calcular_punto_reorden(con, alimento, "2026-07-01", CONSUMO_ALIMENTO)
    assert e.value.codigo == "FALTA_DIAS_REPOSICION"
    inventarios.actualizar_insumo(con, s["jf"], alimento, dias_reposicion=3)
    with pytest.raises(ReglaNegocioError) as e:
        inventarios.calcular_punto_reorden(con, alimento, "2026-07-01")
    assert e.value.codigo == "PR_SIN_BASE"
    calc = inventarios.calcular_punto_reorden(con, alimento, "2026-07-01", CONSUMO_ALIMENTO)
    # 4,1096 qq/día × 3 días = 12,329 + stock de seguridad 7 × 4,1096 = 28,767 → PR 41,096 qq
    assert (calc["consumo_diario"], calc["dias_seguridad"], calc["stock_seguridad"], calc["punto_reorden"]) == \
        (D("4.1096"), 7, D("28.767"), D("41.096"))
    vet = insumo(con, "INS-0308")
    inventarios.actualizar_insumo(con, s["jf"], vet, dias_reposicion=2)
    calc = inventarios.calcular_punto_reorden(con, vet, "2026-07-01", 2)
    assert (calc["dias_seguridad"], calc["punto_reorden"]) == (5, D("14.000"))
    with pytest.raises(ReglaNegocioError) as e:
        inventarios.calcular_punto_reorden(con, insumo(con, "INS-0311"), "2026-07-01", 1)
    assert e.value.codigo == "INSUMO_NO_CRITICO"
    with pytest.raises(AutorizacionError):           # lo recalcula Finanzas (Art. 7)
        inventarios.recalcular_punto_reorden(con, s["ut"], alimento, "2026-07-01", CONSUMO_ALIMENTO)


def test_punto_de_reorden_recalculado_con_el_consumo_del_ciclo_anterior(con, s):
    depositar(con, s, "100000")
    alimento = preparar_pr_alimento(con, s)
    comprar(con, s, "INS-0302", 100, 185)
    inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-20", 40)
    inventarios.registrar_salida(con, s["jf"], alimento, "2026-09-11", 33)       # 73 qq en el ciclo 1
    resultado = inventarios.recalcular_puntos_ciclo(con, s["jf"], "2026-09-12")
    assert [r["insumo"] for r in resultado["recalculados"]] == ["Alimento balanceado comprado"]
    assert {o["codigo"] for o in resultado["omitidos"]} == {"FALTA_DIAS_REPOSICION"}
    pr = inventarios.punto_reorden_vigente(con, alimento, "2026-09-12")
    # 73 qq / 73 días = 1 qq/día → PR = 1 × 3 + 1 × 7 = 10 qq
    assert (pr["fuente"], pr["consumo_diario"], pr["punto_reorden_mil"], pr["ciclo"]) == ("BITACORA", "1.0000", 10000, 2)
    assert pr["base_desde"] == "2026-07-01" and pr["base_hasta"] == "2026-09-11"
    assert len(inventarios.historial_puntos_reorden(con, s["jf"], alimento)) == 2


def test_stock_bajo_el_pr_dispara_compra_de_emergencia(con, s):
    depositar(con, s, "100000")
    alimento = preparar_pr_alimento(con, s)
    provs = tres_proveedores(con, s)
    comprar(con, s, "INS-0302", 50, 185, LUNES, provs)
    assert inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-07", 8)["alerta_id"] is None   # 42 qq
    salida = inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-07", 1)   # 41,000 < PR 41,096
    alerta = inventarios.listar_alertas_stock(con, s["jf"], "ABIERTA")
    assert len(alerta) == 1 and alerta[0]["origen"] == "MONITOREO_CONTINUO" and alerta[0]["existencia_mil"] == 41000
    assert salida["alerta_id"] == alerta[0]["id"]
    salida = inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-07", 1)
    assert salida["alerta_id"] == alerta[0]["id"]            # no se duplica
    # Sin emergencia, una orden en miércoles se rechaza.
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], MIERCOLES, [LineaOC(alimento, 100, 185)])
    assert e.value.codigo == "OC_DIA_NO_PERMITIDO"
    with pytest.raises(AutorizacionError):
        inventarios.autorizar_compra_emergencia(con, s["jf"], alerta[0]["id"], MIERCOLES, "FIN-EGR-01 ext.", "Déficit")
    eid = inventarios.autorizar_compra_emergencia(con, s["gg"], alerta[0]["id"], MIERCOLES,
                                                  "Registro extraordinario 01/2026", "Déficit bajo el PR (CI-FIN-16)")
    assert uno(con, "SELECT estado FROM alertas_stock WHERE id = ?", alerta[0]["id"]) == "EMERGENCIA_AUTORIZADA"
    with pytest.raises(ReglaNegocioError) as e:
        compras.preparar_orden(con, s["jf"], provs[0], MIERCOLES, [LineaOC(insumo(con, "INS-0308"), 1, 10)],
                               emergencia_id=eid)
    assert e.value.codigo == "EMERGENCIA_OTRO_INSUMO"
    res = comprar(con, s, "INS-0302", 100, 185, MIERCOLES, provs, emergencia_id=eid)
    orden = compras.obtener_orden(con, s["jf"], res["orden"]["orden_id"])
    assert orden["es_emergencia"] == 1 and orden["fecha_emision"] == MIERCOLES and orden["emitida_puntual"] == 0
    assert uno(con, "SELECT estado FROM compras_emergencia WHERE id = ?", eid) == "UTILIZADA"
    assert uno(con, "SELECT estado FROM alertas_stock WHERE id = ?", alerta[0]["id"]) == "ATENDIDA"
    assert compras.indicador_cac(con, 2026, 7)["programadas"] == 1        # la emergencia no es programada


def test_suplente_autoriza_emergencia(con, s):
    depositar(con, s, "100000")
    alimento = preparar_pr_alimento(con, s)
    comprar(con, s, "INS-0302", 40, 185)
    alerta = inventarios.verificar_punto_reorden(con, s["jf"], alimento, LUNES, "MONITOREO_CONTINUO")
    with pytest.raises(AutorizacionError):
        inventarios.autorizar_compra_emergencia(con, s["cv"], alerta, LUNES, "Constancia", "Déficit")
    assert inventarios.autorizar_compra_emergencia(con, s["ca_presidente"], alerta, LUNES, "Constancia CI-FIN-17 02",
                                                   "Ausencia de la GG")


def test_proximo_lote_de_pollitos(con, s):
    bb = insumo(con, "INS-0301")
    inventarios.actualizar_insumo(con, s["jf"], bb, dias_reposicion=5)
    inventarios.recalcular_punto_reorden(con, s["jf"], bb, "2026-07-01", consumo_diario_proyectado=D(3000) / D(73))
    pr = inventarios.punto_reorden_vigente(con, bb, "2026-08-20")
    assert pr["punto_reorden_mil"] == 493151                 # 41,0959 × 5 + 41,0959 × 7
    assert inventarios.verificar_punto_reorden(con, s["jf"], bb, "2026-08-20", "MONITOREO_CONTINUO") is None
    alerta = inventarios.verificar_punto_reorden(con, s["jf"], bb, "2026-09-01", "MONITOREO_CONTINUO")
    assert alerta and "Pollitos BB" in uno(con, "SELECT detalle FROM alertas_stock WHERE id = ?", alerta)


# ============================ ACTA DEL VIERNES ===============================
def test_acta_de_stock_del_viernes(con, s):
    depositar(con, s, "100000")
    alimento = preparar_pr_alimento(con, s)
    comprar(con, s, "INS-0302", 50, 185)
    conteos = {insumo(con, c): 0 for c in ("INS-0308", "INS-0309", "INS-0310")}
    with pytest.raises(ReglaNegocioError) as e:
        inventarios.registrar_acta_stock(con, s["ut"], "2026-07-09", {**conteos, alimento: 50})
    assert e.value.codigo == "ACTA_DIA_NO_PERMITIDO" and "viernes" in str(e.value)
    with pytest.raises(DatosInvalidosError) as e:
        inventarios.registrar_acta_stock(con, s["ut"], VIERNES, conteos)
    assert e.value.codigo == "CONTEO_INCOMPLETO"
    acta_id = inventarios.registrar_acta_stock(con, s["ut"], VIERNES, {**conteos, alimento: 40},
                                               "Silo 1 con humedad visible")
    acta = inventarios.obtener_acta(con, s["jf"], acta_id)
    fila = next(l for l in acta["lineas"] if l["insumo_id"] == alimento)
    assert (fila["existencia_fisica_mil"], fila["saldo_contable_mil"], fila["diferencia_mil"], fila["bajo_pr"]) == \
        (40000, 50000, -10000, 1)
    assert "Compra de emergencia" in fila["accion"] and "Investigar diferencia" in fila["accion"]
    assert acta["deficit"] == 1 and len(acta["lineas"]) == 5           # sin las materias primas (*) de 2027
    alerta = inventarios.listar_alertas_stock(con, s["jf"], "ABIERTA")[0]
    assert alerta["origen"] == "ACTA_VIERNES" and alerta["acta_id"] == acta_id
    with pytest.raises(AutorizacionError) as e:
        inventarios.verificar_acta_stock(con, s["ut"], acta_id)
    assert e.value.codigo == "VERIFICACION_MISMA_UNIDAD"
    inventarios.verificar_acta_stock(con, s["jf"], acta_id)
    with pytest.raises(ReglaNegocioError):
        inventarios.registrar_acta_stock(con, s["jf"], VIERNES, {**conteos, alimento: 40})
    inventarios.autorizar_compra_emergencia(con, s["gg"], alerta["id"], VIERNES, f"FIN-EGR-01 n.º {acta_id}",
                                            "Déficit del acta")
    ctx = pdf.Contexto(date(2026, 7, 10), "Encargado de Granja", "Unidad Técnica", "Ciclo 1")
    contenido = pdf.fin_egr_01(inventarios.obtener_acta(con, s["jf"], acta_id), ctx)
    assert contenido.startswith(b"%PDF") and b"FIN-EGR-01" in contenido


# =============================== ROTACIÓN ====================================
def test_rotacion_maxima_de_granos(con, s):
    depositar(con, s, "100000")
    alimento = preparar_pr_alimento(con, s)
    comprar(con, s, "INS-0302", 50, 185)
    inventarios.registrar_salida(con, s["jf"], alimento, "2026-07-07", 5)
    filas = inventarios.evaluar_rotacion(con, "2026-08-05")
    assert (filas[0]["antiguedad_dias"], filas[0]["excede"]) == (30, False)
    assert filas[0]["cobertura_dias"] == D("10.9")                      # 45 qq / 4,1096 qq/día
    assert inventarios.registrar_alertas_rotacion(con, s["ut"], "2026-08-05") == []
    ids = inventarios.registrar_alertas_rotacion(con, s["ut"], "2026-08-06")
    assert len(ids) == 1 and uno(con, "SELECT dias FROM alertas_stock WHERE id = ?", ids[0]) == 31
    assert "PC-07" in uno(con, "SELECT detalle FROM alertas_stock WHERE id = ?", ids[0])
    inventarios.cerrar_alerta(con, s["jf"], ids[0], "Alimento consumido con prioridad")
    with pytest.raises(ReglaNegocioError):
        inventarios.cerrar_alerta(con, s["jf"], ids[0], "Otra vez")


def test_entrada_que_supera_30_dias_de_cobertura_alerta(con, s):
    depositar(con, s, "100000")
    preparar_pr_alimento(con, s)
    comprar(con, s, "INS-0302", 130, 185)          # 130 / 4,1096 = 31,6 días de consumo
    alerta = inventarios.listar_alertas_stock(con, s["jf"], "ABIERTA")
    assert [a["tipo"] for a in alerta] == ["ROTACION_EXCEDIDA"]


# ================================= BAJAS =====================================
def test_bajas_con_visto_del_cv_sobre_2000(con, s):
    depositar(con, s, "100000")
    alimento = insumo(con, "INS-0302")
    comprar(con, s, "INS-0302", 50, 185)
    menor = inventarios.proponer_baja(con, s["ut"], alimento, "2026-07-08", 10, "DETERIORO",
                                      "Sacos con hongos por humedad", "Informe UT 03/2026")
    mayor = inventarios.proponer_baja(con, s["ut"], alimento, "2026-07-08", 11, "DETERIORO",
                                      "Sacos mojados por goteras", "Informe UT 04/2026")
    bajas = {b["id"]: b for b in inventarios.listar_bajas(con, s["jf"])}
    assert (bajas[menor]["valor_estimado"], bajas[menor]["requiere_visto_cv"]) == (D("1850.00"), 0)
    assert (bajas[mayor]["valor_estimado"], bajas[mayor]["requiere_visto_cv"]) == (D("2035.00"), 1)
    with pytest.raises(AutorizacionError) as e:
        inventarios.contabilizar_baja(con, s["jf"], menor, "2026-07-08")
    assert e.value.codigo == "BAJA_SIN_AUTORIZACION"
    with pytest.raises(AutorizacionError):
        inventarios.autorizar_baja(con, s["jf"], menor, "Acta 01")
    inventarios.autorizar_baja(con, s["gg"], menor, "Acta de baja GG 01/2026")
    inventarios.autorizar_baja(con, s["gg"], mayor, "Acta de baja GG 02/2026")
    asiento = inventarios.contabilizar_baja(con, s["jf"], menor, "2026-07-08")
    lineas = {(l["cuenta"], l["debe"], l["haber"]) for l in contable.obtener_asiento(con, s["jf"], asiento)["lineas"]}
    assert lineas == {("7.2.01.03", D("1850.00"), D("0.00")), ("1.1.03.02", D("0.00"), D("1850.00"))}
    with pytest.raises(AutorizacionError) as e:
        inventarios.contabilizar_baja(con, s["jf"], mayor, "2026-07-08")
    assert e.value.codigo == "BAJA_SIN_VISTO_CV"
    with pytest.raises(AutorizacionError):
        inventarios.visar_baja(con, s["gg"], mayor)
    inventarios.visar_baja(con, s["cv"], mayor, "Verificado en almacén")
    inventarios.contabilizar_baja(con, s["jf"], mayor, "2026-07-08")
    assert inventarios.saldo_insumo(con, alimento).cantidad == D("29.000")
    assert tesoreria.saldo_cuenta(con, "7.2.01.03") == D("3885.00")
    with pytest.raises(ReglaNegocioError):
        inventarios.proponer_baja(con, s["ut"], alimento, "2026-07-08", 30, "PERDIDA", "Faltante", "Informe 05")
    ctx = pdf.Contexto(date(2026, 7, 8), "Jefe de Finanzas", "Jefe de Finanzas", "Ciclo 1")
    baja = next(b for b in inventarios.listar_bajas(con, s["jf"]) if b["id"] == mayor)
    assert pdf.acta_baja(baja, ctx, inventarios.CAUSAS_BAJA).startswith(b"%PDF")


def test_catalogo_de_insumos(con, s):
    insumos = inventarios.listar_insumos(con, s["jf"])
    assert {i["cuenta_inventario"] for i in insumos} == {f"1.1.03.{n:02d}" for n in range(1, 16)}
    nuevo = inventarios.crear_insumo(con, s["jf"], "ins-0308b", "Vacuna Newcastle", "1.1.03.08", "dosis",
                                     grupo_critico="VETERINARIO_LIMPIEZA", controla_vencimiento=True, dias_reposicion=4)
    assert uno(con, "SELECT codigo FROM insumos WHERE id = ?", nuevo) == "INS-0308B"
    with pytest.raises(DatosInvalidosError):
        inventarios.crear_insumo(con, s["jf"], "X-1", "Banco", "1.1.01.03", "unidad")
    with pytest.raises(DatosInvalidosError) as e:
        inventarios.crear_insumo(con, s["jf"], "INS-0308B", "Repetido", "1.1.03.08", "dosis")
    assert e.value.codigo == "INSUMO_DUPLICADO"
    with pytest.raises(AutorizacionError):
        inventarios.crear_insumo(con, s["ut"], "X-2", "Otro", "1.1.03.09", "unidad")
