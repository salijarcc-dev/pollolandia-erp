"""Simulación de un ciclo productivo completo con los servicios reales (Fase 13).

Recorre, en el orden de los procesos de la cooperativa, el ciclo 1 del
ejercicio 2026 (3.000 aves, 73 días, recepción el lunes 6-jul-2026 y día 73 el
16-sep-2026): apertura, compras y recepción, planillas, bitácora diaria con
mortalidad normal, faenado, pre-venta (pedidos y cobertura CI-FIN-13), venta,
cierre de costos, cierres mensuales y estados financieros.

Cifras tomadas de los documentos:

* Balance de apertura (Acta de Constitución): banco Bs 138.010; capital
  Bs 534.300.
* Manual de Costos §6-7 y Manual Presupuestario D.4-1: pollitos 3.000 × Bs 9;
  alimento 300 qq × Bs 185; veterinarios Bs 5.895; limpieza Bs 780; crianza
  Bs 2.910; bolsas 3.000 × Bs 1,60; eventual 3.000 aves × Bs 1,50; CIF
  Bs 4.298,25; 7.800 kg a Bs 14,91/kg.
* POA §1.1 y §3.1: ingreso del ciclo Bs 184.800 (7.800 kg × Bs 23,50 y 3
  cargas de gallinaza × Bs 500); flete Bs 970 por lote de 1.000 aves.
* POA §3.1: sueldos GG + JF Bs 7.300 por mes (el aguinaldo administrativo
  anual es Bs 7.300 = un sueldo). El reparto 4.000 / 3.300 entre los dos
  cargos es un supuesto de la simulación (los documentos dan solo el total).
* Mortalidad normal: 60 aves (2 %) frente al umbral de 3 % (POL-03 / CI-FIN-11).

Es un ayudante de pruebas y de la base de demostración: no forma parte de la
aplicación.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping

from servicios import cierre, compras, costos, planillas, produccion as pr, seguridad, tesoreria, ventas
from servicios.compras import CotizacionOC, LineaOC, LineaRecepcion

D = Decimal
BANCO = "1.1.01.03"
DIA_1 = date(2026, 7, 6)            # lunes de recepción de los pollitos BB
INICIO_CICLO_2 = date(2026, 9, 21)  # primer lunes libre tras el día 73 (16-sep)
RAU = "Certificado RAU N.º 0001/2026 y Form. 701 pagado"
CIF_2026 = (("30", "1050", "Depreciación galpones"), ("31", "986.25", "Depreciación equipos de producción"),
            ("33", "162", "Depreciación herramientas"), ("32", "300", "Depreciación alambrados"),
            ("36", "1500", "Mantenimiento y limpieza"), ("37", "300", "Energía eléctrica y agua"))
# (código de insumo, cantidad, precio unitario) — Manual de Costos §6.
COMPRAS_CICLO = (("INS-0301", 3000, "9"), ("INS-0302", 300, "185"), ("INS-0308", 393, "15"),
                 ("INS-0309", 78, "10"), ("INS-0310", 291, "10"), ("INS-0311", 3000, "1.60"))
# Mortalidad normal por lote: (día del ciclo, aves, causa presunta) → 20 aves por lote, 60 en total (2 %).
MORTALIDAD_LOTE = ((3, 4, "Onfalitis"), (8, 3, "Deshidratación"), (15, 3, "Traumatismo"),
                   (25, 3, "Ascitis"), (40, 3, "Ascitis"), (55, 2, "Muerte súbita"), (65, 2, "Muerte súbita"))
ALIMENTO_KG_LOTE = 4600              # 13.800 kg = 300 qq × 46 kg (POA R8)
DIAS_FAENADO = (70, 71, 72)          # un lote por día (CI-FIN-12: nunca antes del día 70)
AVES_FAENADAS_LOTE = 980
PESO_VIVO_LOTE = "2860"
PESO_CANAL_LOTE = "2600"             # 3 × 2.600 = 7.800 kg (Manual Presupuestario B.1)
CHECKLIST = {k: True for k in pr.PUNTOS_CI_FIN_12}


def dia(n: int) -> date:
    """Fecha del día ``n`` del ciclo (día 1 = 6-jul-2026)."""
    return DIA_1 + timedelta(days=n - 1)


def tarde(f: date) -> datetime:
    """Registro a las 18:00 del mismo día (la bitácora se llena en el día)."""
    return datetime(f.year, f.month, f.day, 18, 0)


@dataclass
class Resultado:
    """Datos que la prueba de integración verifica."""

    ciclo_id: int = 0
    ciclo_2_id: int = 0
    lotes: list[int] = field(default_factory=list)
    cierre_costos: dict[str, Any] = field(default_factory=dict)
    conciliacion: dict[str, Any] = field(default_factory=dict)
    planillas: list[int] = field(default_factory=list)
    notas: list[dict[str, Any]] = field(default_factory=list)
    pasos: list[str] = field(default_factory=list)


def _autorizar_desembolso(con: Any, s: Mapping[str, Any], monto: Any, fecha: Any, motivo: str) -> int:
    """CI-FIN-09: GG sobre Bs 1.000; GG + CV sobre Bs 5.000."""
    ctx = seguridad.contexto_desde_bd(con, fecha)
    aid = seguridad.solicitar_autorizacion(con, s["jf"], "DESEMBOLSO", monto, motivo, ctx)
    if seguridad.firmar_autorizacion(con, s["gg"], aid) == "PENDIENTE":
        seguridad.firmar_autorizacion(con, s["cv"], aid)
    return aid


def levantar_compromisos_suspendidos(con: Any, s: Mapping[str, Any]) -> int:
    """POL-04 Art. 2: un compromiso que deja el saldo proyectado bajo el 120 %
    del SME queda suspendido; la GG firma la autorización escrita y Finanzas la
    confirma. Devuelve cuántos compromisos se levantaron."""
    n = 0
    for c in con.execute("SELECT id, autorizacion_id FROM compromisos WHERE estado = 'SUSPENDIDO' ORDER BY id").fetchall():
        seguridad.firmar_autorizacion(con, s["gg"], int(c["autorizacion_id"]),
                                      documento_ref=f"Autorización escrita GG {int(c['id'])}/2026")
        tesoreria.confirmar_compromiso(con, s["jf"], int(c["id"]))
        n += 1
    return n


def _proveedores(con: Any, s: Mapping[str, Any]) -> tuple[int, int, int]:
    datos = (("Agroveterinaria Caranavi S.R.L.", "1020304011"), ("Insumos Yungas Ltda.", "2030405022"),
             ("Avícola La Paz S.A.", "3040506033"))
    ids = []
    for nombre, nit in datos:
        pid, estado = compras.registrar_proveedor(con, s["jf"], "EMPRESA", "2026-07-01", nombre=nombre, nit=nit,
                                                  producto="Insumos avícolas")
        assert estado == "HABILITADO", estado
        ids.append(pid)
    return ids[0], ids[1], ids[2]


def comprar(con: Any, s: Mapping[str, Any], provs: tuple[int, int, int], codigo: str, cantidad: Any, precio: Any,
            fecha: date) -> dict[str, Any]:
    """OC del lunes (JF prepara, GG firma y emite; tres cotizaciones sobre
    Bs 5.000), recepción de la UT (FIN-EGR-04) y contabilización del JF."""
    insumo = int(con.execute("SELECT id FROM insumos WHERE codigo = ?", (codigo,)).fetchone()[0])
    total = (D(str(cantidad)) * D(str(precio))).quantize(D("0.01"))
    cotiz = [CotizacionOC(provs[0], total, "COT-001"), CotizacionOC(provs[1], total + 100, "COT-002"),
             CotizacionOC(provs[2], total + 250, "COT-003")] if total > 5000 else []
    r = compras.preparar_orden(con, s["jf"], provs[0], fecha, [LineaOC(insumo, cantidad, precio)], cotizaciones=cotiz)
    levantar_compromisos_suspendidos(con, s)
    seguridad.firmar_autorizacion(con, s["gg"], r["autorizacion_id"])
    compras.emitir_orden(con, s["gg"], r["orden_id"], fecha)
    linea = int(con.execute("SELECT id FROM oc_lineas WHERE orden_id = ?", (r["orden_id"],)).fetchone()[0])
    rid = compras.registrar_recepcion(con, s["ut"], r["orden_id"], fecha, f"FAC-{codigo}",
                                      [LineaRecepcion(linea, cantidad, True,
                                                      "2027-06-30" if codigo == "INS-0308" else None)])
    contable = compras.contabilizar_recepcion(con, s["jf"], rid)
    return {"orden_id": r["orden_id"], "recepcion_id": rid, **contable}


def pagar_proveedores(con: Any, s: Mapping[str, Any], fecha: date) -> list[int]:
    """Programación de pagos (FIN-EGR-05) autorizada por la GG y pago de cada
    cuenta en el orden de la jerarquía de POL-04."""
    pid = compras.preparar_programacion(con, s["jf"], fecha)
    compras.autorizar_programacion(con, s["gg"], pid)
    movimientos = []
    lineas = con.execute("SELECT pl.cuenta_por_pagar_id, pl.monto_cent FROM programacion_lineas pl WHERE "
                         "pl.programacion_id = ? AND pl.pagable = 1 ORDER BY pl.nivel, pl.id", (pid,)).fetchall()
    for l in lineas:
        monto = D(int(l["monto_cent"])) / 100
        aid = _autorizar_desembolso(con, s, monto, fecha, "Pago a proveedor de insumos") if monto > 1000 else None
        movimientos.append(compras.pagar_cuenta_por_pagar(con, s["jf"], int(l["cuenta_por_pagar_id"]), fecha, BANCO,
                                                          autorizacion_id=aid))
    return movimientos


def registrar_personal(con: Any, s: Mapping[str, Any]) -> None:
    """Personal fijo desde el 1-jul-2026 (POA D6): Encargado de Granja Bs 3.300
    (Manual de Costos) y la planta administrativa (Bs 7.300 por mes, POA §3.1)."""
    planillas.registrar_personal(con, s["jf"], "Encargado de Granja", "6012345", "Encargado de Granja", "GRANJA",
                                 "3300", "2026-07-01", "INDEFINIDO", "Contrato 01/2026", polivalente=True)
    planillas.registrar_personal(con, s["jf"], "Rocío Del Alba Flores Colque", "4012345", "Gerente General",
                                 "ADMINISTRACION", "4000", "2026-07-01", "INDEFINIDO", "Contrato 02/2026")
    planillas.registrar_personal(con, s["jf"], "Jefe de Finanzas y Comercialización", "5012345",
                                 "Jefe de Finanzas y Comercialización", "ADMINISTRACION", "3300", "2026-07-01",
                                 "INDEFINIDO", "Contrato 03/2026")


def planilla_del_mes(con: Any, s: Mapping[str, Any], mes: int, pago: date) -> int:
    """Planilla FIN-EGR-09/10: JF prepara, GG autoriza, JF contabiliza y paga
    sueldos y aportes con la autorización CI-FIN-09."""
    pid = planillas.preparar_planilla(con, s["jf"], 2026, mes)
    planillas.autorizar_planilla(con, s["gg"], pid)
    planillas.contabilizar_planilla(con, s["jf"], pid)
    p = planillas.obtener_planilla(con, s["jf"], pid)
    liquido = D(str(p["liquido"]))
    planillas.pagar_sueldos(con, s["jf"], pid, pago, BANCO,
                            autorizacion_id=_autorizar_desembolso(con, s, liquido, pago, f"Sueldos {mes:02d}/2026"))
    aportes = D(str(p["aporte_laboral"])) + D(str(p["aporte_patronal"]))
    planillas.pagar_aportes(con, s["jf"], pid, pago, BANCO,
                            autorizacion_id=_autorizar_desembolso(con, s, aportes, pago, f"Aportes {mes:02d}/2026"))
    return pid


def _alimento_dia(n: int, ultimo: int) -> list[int]:
    """Reparte 4.600 kg por lote proporcionalmente al día de vida (consumo
    creciente por fase); el último día recibe el residuo."""
    pesos = list(range(1, ultimo + 1))
    total = sum(pesos)
    kg = [ALIMENTO_KG_LOTE * p // total for p in pesos]
    kg[-1] += ALIMENTO_KG_LOTE - sum(kg)
    return kg


def bitacora_73_dias(con: Any, s: Mapping[str, Any], ciclo_id: int, lotes: list[int]) -> dict[str, int]:
    """Bitácora FIN-TRA-02 de cada lote desde el día 1 hasta la víspera de su
    faenado, con la mortalidad normal; baja FIN-TRA-04 de cada día con muertas,
    disposición sanitaria FIN-TRA-11, muestreo semanal de peso y valoración
    mensual del alimento consumido (13.800 kg = 300 qq)."""
    mortalidad = {d: (aves, causa) for d, aves, causa in MORTALIDAD_LOTE}
    registros = 0
    ultimo = {lote: DIAS_FAENADO[i] - 1 for i, lote in enumerate(lotes)}
    kg = {lote: _alimento_dia(0, ultimo[lote]) for lote in lotes}
    valoraciones = (date(2026, 7, 31), date(2026, 8, 31))
    for n in range(1, max(ultimo.values()) + 1):
        f = dia(n)
        for lote in lotes:
            if n > ultimo[lote]:
                continue
            muertas, causa = mortalidad.get(n, (0, None))
            pr.registrar_bitacora(con, s["ut"], lote, f, str(kg[lote][n - 1]), muertas, causa_presunta=causa,
                                  ahora=tarde(f))
            registros += 1
            if n % 7 == 0:
                pr.registrar_muestreo(con, s["ut"], lote, f, 30, str(D("0.035") * n * 30))
        if f in valoraciones:
            costos.consumir_alimento_bitacora(con, s["jf"], ciclo_id, f)
    costos.consumir_alimento_bitacora(con, s["jf"], ciclo_id, dia(max(ultimo.values())))
    for b in pr.bitacoras_sin_baja(con, ciclo_id):
        pr.registrar_baja(con, s["jf"], int(b["id"]))
    for lote in lotes:
        pr.registrar_disposicion(con, s["ut"], lote, dia(66), pr.muertas_lote(con, lote), "ENTIERRO_CAL",
                                 "Encargado de Granja")
    return {"registros": registros}


def personal_eventual(con: Any, s: Mapping[str, Any], fecha_pago: date) -> int:
    """Circular de cierre (UT), autorización del lunes (GG), contratos,
    liquidación de 2.940 aves × Bs 1,50 y pago autorizado (PR-FIN-04)."""
    cid = planillas.emitir_circular(con, s["ut"], "2026-09-01", dia(DIAS_FAENADO[0]).isoformat(), "L-2026-01",
                                    3 * AVES_FAENADAS_LOTE, "0")
    planillas.acusar_circular(con, s["gg"], cid)
    planillas.acusar_circular(con, s["jf"], cid)
    sol = planillas.solicitar_contratacion(con, s["jf"], cid, "2026-09-07", 5)
    levantar_compromisos_suspendidos(con, s)
    planillas.autorizar_contratacion(con, s["gg"], sol["id"], "2026-09-07")
    contratos = [planillas.registrar_contrato(con, s["jf"], sol["id"], f"Jornalero {n}", f"80000{n}", "2026-09-07")
                 for n in range(1, 6)]
    liq = planillas.liquidar_jornales(con, s["jf"], sol["id"], dia(DIAS_FAENADO[-1]).isoformat(),
                                      {c: 3 * AVES_FAENADAS_LOTE // 5 for c in contratos})
    liq_id = int(liq["id"]) if isinstance(liq, dict) else int(liq)
    planillas.autorizar_pago_jornales(con, s["gg"], liq_id)
    monto = D(int(con.execute("SELECT total_cent FROM liquidaciones_eventuales WHERE id = ?",
                              (liq_id,)).fetchone()[0])) / 100
    planillas.pagar_jornales(con, s["jf"], liq_id, fecha_pago, BANCO,
                             autorizacion_id=_autorizar_desembolso(con, s, monto, fecha_pago, "Jornales de faenado"))
    return liq_id


def clientes(con: Any, s: Mapping[str, Any]) -> dict[str, int]:
    return {
        "hotel": ventas.registrar_cliente(con, s["jf"], "HORECA", "Hotel Esmeralda Coroico", "2026-07-01",
                                          documento="4455667")["id"],
        "frial1": ventas.registrar_cliente(con, s["jf"], "FRIAL", "Frial Caranavi", "2026-07-01")["id"],
        "frial2": ventas.registrar_cliente(con, s["jf"], "FRIAL", "Frial Los Yungas", "2026-07-01")["id"],
        "mercado": ventas.registrar_cliente(con, s["jf"], "MERCADO_POPULAR", "Puesto 14 Mercado Central",
                                            "2026-07-01")["id"],
        "agricultor": ventas.registrar_cliente(con, s["jf"], "AGRICULTOR", "Juan Quispe (cafetal)", "2026-07-01")["id"],
    }


def _nota(con: Any, s: Mapping[str, Any], cliente: int, fecha: date, producto: str, cantidad: Any,
          despacho: int | None, medio: str, comprobante: str) -> dict[str, Any]:
    return ventas.registrar_nota(con, s["jf"], cliente, fecha, producto, cantidad, hora_entrega="10:30",
                                 medio_pago=medio, comprobante_ref=comprobante, certificado_rau_exhibido=True,
                                 certificado_rau_ref=RAU, recibido_por="Encargado de compras",
                                 entregado_por="Encargado de Granja", despacho_id=despacho)


def preventa_y_venta(con: Any, s: Mapping[str, Any], lotes: list[int], c: Mapping[str, int]) -> list[dict[str, Any]]:
    """Pre-venta por lote (CI-FIN-13: pedidos con 3 días de anticipación,
    cobertura del 100 % con dos friales indicativos), cierre del despacho y
    notas de entrega de 2.600 kg por lote; gallinaza 3 cargas al contado."""
    notas = []
    for i, lote in enumerate(lotes):
        faenado = dia(DIAS_FAENADO[i])
        d = ventas.crear_despacho(con, s["jf"], lote, faenado)["id"]
        pedido = faenado - timedelta(days=5)
        ventas.registrar_pedido(con, s["jf"], d, c["hotel"], 400, pedido)
        ventas.registrar_pedido(con, s["jf"], d, c["frial1"], 300, pedido)
        ventas.registrar_pedido(con, s["jf"], d, c["frial2"], 200, pedido)
        ventas.registrar_pedido(con, s["jf"], d, c["mercado"], AVES_FAENADAS_LOTE - 900, pedido)
        v = ventas.verificar_cobertura(con, s["jf"], d, faenado - timedelta(days=1))
        if not v["cubre"] or v.get("respaldo_requerido"):
            ventas.activar_respaldo(con, s["gg"], d, "Cobertura con friales y mercado popular (CI-FIN-13)")
        for cliente, kg, ref in ((c["hotel"], "1000", "TRF"), (c["frial1"], "800", "QR"), (c["frial2"], "500", "QR"),
                                 (c["mercado"], "300", "TRF")):
            notas.append(_nota(con, s, cliente, faenado, "CARNE_ENTERO", kg, d,
                               "TRANSFERENCIA" if ref == "TRF" else "QR_ACH", f"{ref}-{lote}-{cliente}"))
        ventas.cerrar_despacho(con, s["jf"], d, faenado)
        notas.append(_nota(con, s, c["agricultor"], faenado, "GALLINAZA", "1", None, "EFECTIVO", f"REC-{lote}"))
        parte = ventas.registrar_parte(con, s["jf"], faenado, f"BOL-{faenado:%m%d}")
        ventas.verificar_parte(con, s["gg"], int(parte["id"]))
    return notas


def simular_ciclo(con: Any, s: Mapping[str, Any]) -> Resultado:
    """Ejecuta el ciclo completo y devuelve los datos para verificar."""
    r = Resultado()
    # 1. Apertura: fondo de caja de Bs 5.000 (CI-FIN-01) sobre el balance de apertura.
    tesoreria.constituir_fondo(con, s["jf"], date(2026, 7, 1), "Jefe de Finanzas y Comercialización",
                               "Resolución GG 01/2026",
                               autorizacion_id=_autorizar_desembolso(con, s, "5000", date(2026, 7, 1), "Fondo de caja"))
    r.pasos.append("apertura")
    registrar_personal(con, s)
    tarifario = ventas.proponer_tarifario(con, s["gg"], "2026-07-01",
                                          [("CARNE_ENTERO", "TODOS", "23.50"), ("GALLINAZA", "TODOS", "500")],
                                          "Tarifario del Manual Presupuestario §3.1")
    ventas.resolver_tarifario(con, s["ca_presidente"], tarifario["id"], "APRUEBA", "Acta CA 2/2026")
    # 2. Compras y recepción del lunes de inicio.
    provs = _proveedores(con, s)
    for codigo, cantidad, precio in COMPRAS_CICLO:
        comprar(con, s, provs, codigo, cantidad, precio, DIA_1)
    pagar_proveedores(con, s, DIA_1 + timedelta(days=2))
    r.pasos.append("compra y recepción")
    # 3. Apertura del ciclo (FIN-TRA-01) y consumos de insumos.
    galpones = [pr.registrar_galpon(con, s["ut"], f"G{i}", f"Galpón {i}", "125") for i in (1, 2, 3)]
    kardex = pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"]
    r.ciclo_id = pr.abrir_ciclo(con, s["jf"], kardex, {g: 1000 for g in galpones}, "SENASAG-CERT-0042")
    r.lotes = [int(x[0]) for x in con.execute("SELECT id FROM lotes WHERE ciclo_id = ? ORDER BY codigo", (r.ciclo_id,))]
    for codigo, cantidad in (("INS-0308", 393), ("INS-0309", 78), ("INS-0310", 291)):
        insumo = int(con.execute("SELECT id FROM insumos WHERE codigo = ?", (codigo,)).fetchone()[0])
        costos.consumir_insumo(con, s["jf"], insumo, DIA_1, cantidad, ciclo_id=r.ciclo_id)
    # 4. Planillas de julio y agosto.
    r.planillas.append(planilla_del_mes(con, s, 7, date(2026, 7, 31)))
    r.planillas.append(planilla_del_mes(con, s, 8, date(2026, 8, 31)))
    r.pasos.append("planilla")
    # 5. Bitácora de 73 días con mortalidad normal.
    bitacora_73_dias(con, s, r.ciclo_id, r.lotes)
    r.pasos.append("bitácora")
    # 6. Faenado (días 70-72), personal eventual, bolsas y CIF del ciclo.
    for lote, n in zip(r.lotes, DIAS_FAENADO):
        pr.registrar_faenado(con, s["ut"], lote, dia(n), AVES_FAENADAS_LOTE, PESO_VIVO_LOTE, PESO_CANAL_LOTE,
                             CHECKLIST, "Juan Mamani", ahora=tarde(dia(n)))
    bolsas = int(con.execute("SELECT id FROM insumos WHERE codigo = 'INS-0311'").fetchone()[0])
    costos.consumir_insumo(con, s["jf"], bolsas, dia(DIAS_FAENADO[0]), 3000, ciclo_id=r.ciclo_id)
    for sufijo, monto, concepto in CIF_2026:
        costos.registrar_cif(con, s["jf"], sufijo, dia(72), monto, concepto, ciclo_id=r.ciclo_id)
    r.pasos.append("faenado")
    # 7. Pre-venta y venta.
    c = clientes(con, s)
    r.notas = preventa_y_venta(con, s, r.lotes, c)
    personal_eventual(con, s, dia(73))
    for i in range(3):
        tesoreria.registrar_pago(con, s["jf"], dia(DIAS_FAENADO[i]), "970", BANCO, "7.1.03.01",
                                 f"Flete de venta del lote {i + 1} (Bs 970 por lote, POA §3.1)", "Transportes Yungas")
    r.pasos.append("pre-venta y venta")
    # 8. Conclusión operativa y cierre de costos del día 73.
    r.conciliacion = pr.concluir_ciclo(con, s["jf"], r.ciclo_id)
    r.cierre_costos = costos.cerrar_ciclo(con, s["jf"], r.ciclo_id)
    ventas.contabilizar_costos_pendientes(con, s["jf"], dia(73))
    costos.aprobar_informe(con, s["gg"], r.ciclo_id, "Costo coherente con el presupuesto D.4")
    r.pasos.append("cierre de costos")
    # 9. Inicio del ciclo 2 (lunes 21-sep): pollitos BB, reversión del corte de MOD
    #    y planilla de septiembre (la MOD de septiembre entra al centro del ciclo 2).
    comprar(con, s, provs, "INS-0301", 3000, "9", INICIO_CICLO_2)
    kardex2 = pr.recepciones_pollitos_disponibles(con, s["jf"])[0]["kardex_id"]
    r.ciclo_2_id = pr.abrir_ciclo(con, s["jf"], kardex2, {g: 1000 for g in galpones}, "SENASAG-CERT-0043")
    costos.aplicar_reversiones_pendientes(con, s["jf"])
    pagar_proveedores(con, s, INICIO_CICLO_2 + timedelta(days=2))
    r.planillas.append(planilla_del_mes(con, s, 9, date(2026, 9, 30)))
    # 10. Cierres mensuales de julio, agosto y septiembre (FIN-INF-01 y cierre de la GG).
    for numero, fecha in ((1, date(2026, 8, 3)), (2, date(2026, 9, 2)), (3, date(2026, 10, 2))):
        pid = int(con.execute("SELECT id FROM periodos WHERE ejercicio = 2026 AND numero = ?", (numero,)).fetchone()[0])
        cierre.registrar_lista_verificacion(con, s["jf"], pid, fecha)
        cierre.cerrar_mes(con, s["gg"], pid)
    r.pasos.append("cierre mensual")
    return r
