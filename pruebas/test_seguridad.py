"""Pruebas de servicios/seguridad.py (Fase 2).

Bordes tomados de los documentos: desembolso > Bs 1.000 (POL-04 Art. 2),
> Bs 5.000 firma mancomunada (POL-04 Art. 5), CAPEX no previsto > Bs 20.000
(POL-02 Art. 1), variación > 15 % y > Bs 10.000 (POL-02 Art. 4), SME 2026 de
Bs 136.413 con umbral del 120 % = Bs 163.695,60 (POL-04 Art. 2), OC > Bs 5.000
con 3 cotizaciones (PR-FIN-03), crédito HORECA 1 día (2026-2027) y 7 días desde
2028 (POL-05), mayoría de 2/3 (POL-02 Art. 2), suplencia CI-FIN-17 y cambio de
contraseña cada 90 días (MAN-ARC-01 §5.2).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from pruebas.conftest import CLAVE
from servicios import seguridad
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError
from servicios.seguridad import Cotizacion, Suplencia, TipoDecision, requiere_autorizacion

LUNES = datetime(2026, 7, 6, 9, 0, 0)
MARTES = datetime(2026, 7, 7, 9, 0, 0)


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


@pytest.fixture()
def ctx(con):
    return seguridad.contexto_desde_bd(con, "2026-07-06")


def solicitar(con, s, tipo, monto, ctx, quien="jf", **kw):
    return seguridad.solicitar_autorizacion(con, s[quien], tipo, monto, f"Solicitud {tipo}", ctx, **kw)


# ======================== Matriz CI-FIN-09 (pura) =========================
@pytest.mark.parametrize("monto, firmas", [
    ("1000.00", ()), ("1000.01", ("GG",)), ("5000.00", ("GG",)), ("5000.01", ("GG", "CV")),
])
def test_desembolso_umbrales(ctx, monto, firmas):
    r = requiere_autorizacion("DESEMBOLSO", monto, ctx)
    assert r.firmas == firmas
    assert r.firma_mancomunada == (firmas == ("GG", "CV"))


def test_compromiso_bajo_120_por_ciento_del_sme(ctx):
    from dataclasses import replace
    bajo = requiere_autorizacion("COMPROMISO_GASTO", 1, replace(ctx, saldo_proyectado=Decimal("163695.59")))
    assert bajo.firmas == ("GG",) and bajo.autorizacion_escrita
    assert "Bs 163.695,60" in bajo.fundamentos[0] and "120 %" in bajo.fundamentos[0]
    justo = requiere_autorizacion("COMPROMISO_GASTO", 1, replace(ctx, saldo_proyectado=Decimal("163695.60")))
    assert justo.firmas == () and not justo.autorizacion_escrita
    with pytest.raises(DatosInvalidosError):
        requiere_autorizacion("COMPROMISO_GASTO", 1, ctx)
    desembolso = requiere_autorizacion("DESEMBOLSO", 800, replace(ctx, saldo_proyectado=Decimal("100000")))
    assert desembolso.firmas == ("GG",) and desembolso.autorizacion_escrita


def test_capex_no_previsto(ctx):
    from dataclasses import replace
    no_previsto = replace(ctx, previsto_en_plan=False)
    assert requiere_autorizacion("CAPEX_NO_PREVISTO", 15000, no_previsto).firmas == ("CA",)
    assert not requiere_autorizacion("CAPEX_NO_PREVISTO", "20000.00", no_previsto).autorizacion_especifica
    assert requiere_autorizacion("CAPEX_NO_PREVISTO", "20000.01", no_previsto).autorizacion_especifica
    assert requiere_autorizacion("CAPEX_NO_PREVISTO", 50000, replace(ctx, previsto_en_plan=True)).firmas == ()
    with pytest.raises(DatosInvalidosError):
        requiere_autorizacion("CAPEX_NO_PREVISTO", 100, ctx)


@pytest.mark.parametrize("aprobado, nuevo, firmas, informar", [
    ("60000", "70000.00", (), ("CA",)),          # 16,7 % pero variación = 10.000 (no mayor)
    ("60000", "70000.01", ("AG",), ("CA",)),     # > 15 % y > 10.000
    ("100000", "114000", (), ()),                # 14 %
    ("100000", "85000", (), ()),                 # 15 % exacto (no mayor)
    ("100000", "84000", ("AG",), ("CA",)),       # disminución de 16 % y 16.000
])
def test_variacion_capex(ctx, aprobado, nuevo, firmas, informar):
    from dataclasses import replace
    r = requiere_autorizacion("VARIACION_CAPEX", nuevo, replace(ctx, monto_aprobado=Decimal(aprobado)))
    assert (r.firmas, r.informar_a) == (firmas, informar)


def test_orden_de_compra_y_cotizaciones(ctx):
    assert requiere_autorizacion("COMPRA_INSUMOS", "5000.00", ctx).cotizaciones_minimas == 0
    r = requiere_autorizacion("COMPRA_INSUMOS", "5000.01", ctx)
    assert r.firmas == ("GG",) and r.cotizaciones_minimas == 3


def test_otras_filas_de_la_matriz(ctx):
    assert requiere_autorizacion("CONTRATACION_EVENTUAL", 1500, ctx).dia_semana == 1
    deuda = requiere_autorizacion("FINANCIAMIENTO_DEUDA_CAPEX", 80000, ctx)
    assert deuda.firmas == ("AG",) and deuda.mayoria_calificada == (2, 3)
    assert requiere_autorizacion("CAPITALIZACION_EXCEDENTES", 1, ctx).mayoria_calificada == (2, 3)
    assert requiere_autorizacion("DISTRIBUCION_EXCEDENTES", 1, ctx).documentos_previos == ("INF-CV-04",)
    assert requiere_autorizacion("PRESUPUESTO_CERTIFICACIONES", 1, ctx).firmas == ("CA",)
    assert requiere_autorizacion("NUEVA_ACTIVIDAD_RAU", 0, ctx).firmas == ("CA",)
    assert requiere_autorizacion("CANAL_RESPALDO", 0, ctx).firmas == ("GG",)
    assert requiere_autorizacion("AJUSTE_CONTABLE", 1, ctx).firmas == ("GG",)
    otra = requiere_autorizacion(TipoDecision.OTRA, 1, ctx)
    assert otra.firmas == ("GG",) and otra.autorizacion_escrita
    with pytest.raises(DatosInvalidosError):
        requiere_autorizacion("INVENTADA", 1, ctx)
    with pytest.raises(DatosInvalidosError):
        requiere_autorizacion("DESEMBOLSO", -1, ctx)


def test_credito_horeca_por_ejercicio(con, ctx):
    from dataclasses import replace
    assert requiere_autorizacion("CREDITO_HORECA", 500, replace(ctx, dias_credito=1)).firmas == ()
    with pytest.raises(ReglaNegocioError) as e:
        requiere_autorizacion("CREDITO_HORECA", 500, replace(ctx, dias_credito=2))
    assert e.value.codigo == "CREDITO_EXCEDE_PLAZO"
    ctx28 = seguridad.contexto_desde_bd(con, "2028-08-07", dias_credito=7)
    r = requiere_autorizacion("CREDITO_HORECA", 500, ctx28)
    assert r.firmas == ("GG",) and r.autorizacion_escrita
    with pytest.raises(ReglaNegocioError):
        requiere_autorizacion("CREDITO_HORECA", 500, replace(ctx28, dias_credito=8))
    with pytest.raises(ReglaNegocioError) as e:
        requiere_autorizacion("CREDITO_HORECA", 500, replace(ctx28, monto_entrega_semanal=Decimal("400")))
    assert e.value.codigo == "CREDITO_EXCEDE_MONTO"


# ===================== Firmas: segregación y mancomunada ==================
def test_firma_mancomunada_incompleta_rechazada(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "6000", ctx)
    with pytest.raises(AutorizacionError) as e:
        seguridad.firmar_autorizacion(con, s["jf"], aid)
    assert e.value.codigo == "SEGREGACION_FUNCIONES"
    assert seguridad.firmar_autorizacion(con, s["gg"], aid) == "PENDIENTE"
    with pytest.raises(AutorizacionError) as e:
        seguridad.verificar_autorizacion(con, aid, tipo_decision="DESEMBOLSO", monto="6000")
    assert e.value.codigo == "FIRMA_MANCOMUNADA_INCOMPLETA" and e.value.detalle["faltan"] == ["CV"]
    assert "Consejo de Vigilancia" in str(e.value)
    assert seguridad.firmar_autorizacion(con, s["cv"], aid) == "AUTORIZADA"
    assert seguridad.verificar_autorizacion(con, aid, tipo_decision="DESEMBOLSO", monto="6000")["estado"] == "AUTORIZADA"


def test_mancomunada_sin_la_gg(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "5000.01", ctx)
    seguridad.firmar_autorizacion(con, s["cv"], aid)
    with pytest.raises(AutorizacionError) as e:
        seguridad.verificar_autorizacion(con, aid)
    assert e.value.codigo == "FIRMA_MANCOMUNADA_INCOMPLETA" and e.value.detalle["faltan"] == ["GG"]


def test_jf_intentando_autorizar_rechazado(con, s, ctx):
    for tipo, monto in (("DESEMBOLSO", "2000"), ("COMPRA_INSUMOS", "800"), ("AJUSTE_CONTABLE", "10")):
        aid = solicitar(con, s, tipo, monto, ctx)
        with pytest.raises(AutorizacionError) as e:
            seguridad.firmar_autorizacion(con, s["jf"], aid)
        assert e.value.codigo == "SEGREGACION_FUNCIONES" and "Jefe de Finanzas" in str(e.value)
    assert uno(con, "SELECT COUNT(*) FROM autorizacion_firmas") == 0
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE usuario='jfinanzas' AND detalle LIKE 'Acceso denegado%'") == 3


def test_ut_y_cv_fuera_de_su_rol(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "3000", ctx)
    with pytest.raises(AutorizacionError) as e:
        seguridad.firmar_autorizacion(con, s["ut"], aid)
    assert e.value.codigo == "SEGREGACION_FUNCIONES"
    with pytest.raises(AutorizacionError) as e:
        seguridad.firmar_autorizacion(con, s["cv"], aid)  # Bs 3.000 no requiere cofirma
    assert e.value.codigo == "FIRMA_NO_REQUERIDA"


def test_autofirma_rechazo_y_duplicados(con, s, ctx):
    propia = solicitar(con, s, "DESEMBOLSO", "2000", ctx, quien="gg")
    with pytest.raises(AutorizacionError) as e:
        seguridad.firmar_autorizacion(con, s["gg"], propia)
    assert e.value.codigo == "AUTOFIRMA"
    aid = solicitar(con, s, "DESEMBOLSO", "7000", ctx)
    seguridad.firmar_autorizacion(con, s["cv"], aid)
    with pytest.raises(ReglaNegocioError) as e:
        seguridad.firmar_autorizacion(con, s["cv2"], aid)
    assert e.value.codigo == "FIRMA_DUPLICADA"
    assert seguridad.firmar_autorizacion(con, s["gg"], aid, decision="RECHAZA", observacion="Sin respaldo") == "RECHAZADA"
    with pytest.raises(AutorizacionError) as e:
        seguridad.verificar_autorizacion(con, aid)
    assert e.value.codigo == "AUTORIZACION_RECHAZADA"
    with pytest.raises(ReglaNegocioError):
        seguridad.firmar_autorizacion(con, s["cv2"], aid)


def test_sin_firma_requerida_queda_autorizada(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "800", ctx)
    assert uno(con, "SELECT estado FROM autorizaciones WHERE id=?", aid) == "AUTORIZADA"


def test_autorizacion_escrita_y_actas(con, s, ctx):
    from dataclasses import replace
    aid = solicitar(con, s, "COMPROMISO_GASTO", "20000", replace(ctx, saldo_proyectado=Decimal("150000")))
    with pytest.raises(DatosInvalidosError) as e:
        seguridad.firmar_autorizacion(con, s["gg"], aid)
    assert e.value.codigo == "DOCUMENTO_OBLIGATORIO"
    assert seguridad.firmar_autorizacion(con, s["gg"], aid, documento_ref="Nota GG-012/2026") == "AUTORIZADA"
    capex = solicitar(con, s, "CAPEX_NO_PREVISTO", "25000", replace(ctx, previsto_en_plan=False))
    with pytest.raises(DatosInvalidosError):
        seguridad.firmar_autorizacion(con, s["ca_presidente"], capex)
    assert seguridad.firmar_autorizacion(con, s["ca_presidente"], capex, documento_ref="Acta CA 07/2026") == "AUTORIZADA"


def test_mayoria_calificada_de_la_asamblea(con, s, ctx):
    aid = solicitar(con, s, "FINANCIAMIENTO_DEUDA_CAPEX", "80000", ctx)
    with pytest.raises(ReglaNegocioError) as e:
        seguridad.firmar_autorizacion(con, s["ag"], aid, documento_ref="Acta AG 02/2026",
                                      votos_favor=7, votos_emitidos=12)
    assert e.value.codigo == "MAYORIA_CALIFICADA_NO_ALCANZADA"
    with pytest.raises(DatosInvalidosError):
        seguridad.firmar_autorizacion(con, s["ag"], aid, documento_ref="Acta AG 02/2026")
    assert seguridad.firmar_autorizacion(con, s["ag"], aid, documento_ref="Acta AG 02/2026",
                                         votos_favor=8, votos_emitidos=12) == "AUTORIZADA"


def test_documento_previo_y_cotizaciones(con, s, ctx):
    with pytest.raises(ReglaNegocioError) as e:
        solicitar(con, s, "DISTRIBUCION_EXCEDENTES", "210293", ctx)
    assert e.value.codigo == "DOCUMENTO_PREVIO_FALTANTE"
    assert solicitar(con, s, "DISTRIBUCION_EXCEDENTES", "210293", ctx, documentos={"INF-CV-04": "INF-CV-04 n.º 1"})
    dos = [Cotizacion("Avícola Sofía", Decimal("6000")), Cotizacion("Pío Lindo", Decimal("6100"))]
    with pytest.raises(ReglaNegocioError) as e:
        solicitar(con, s, "COMPRA_INSUMOS", "6000", ctx, cotizaciones=dos)
    assert e.value.codigo == "COTIZACIONES_INSUFICIENTES"
    repetida = dos + [Cotizacion("avícola sofía ", Decimal("5900"))]
    with pytest.raises(ReglaNegocioError):
        solicitar(con, s, "COMPRA_INSUMOS", "6000", ctx, cotizaciones=repetida)
    tres = dos + [Cotizacion("Granja Caranavi", Decimal("6050"))]
    assert solicitar(con, s, "COMPRA_INSUMOS", "6000", ctx, cotizaciones=tres)


def test_contratacion_eventual_solo_los_lunes(con, s, ctx):
    aid = solicitar(con, s, "CONTRATACION_EVENTUAL", "1500", ctx)
    with pytest.raises(ReglaNegocioError) as e:
        seguridad.firmar_autorizacion(con, s["gg"], aid, ahora=MARTES)
    assert e.value.codigo == "DIA_NO_PERMITIDO" and "lunes" in str(e.value)
    assert seguridad.firmar_autorizacion(con, s["gg"], aid, ahora=LUNES) == "AUTORIZADA"


def test_ut_no_solicita_desembolsos(con, s, ctx):
    with pytest.raises(AutorizacionError):
        solicitar(con, s, "DESEMBOLSO", "100", ctx, quien="ut")


# =========================== Suplencia CI-FIN-17 ==========================
CONSTANCIA = Suplencia(causa="GG en trámite ante SENASAG en La Paz", constancia_ref="FIN-EGR-06 n.º 14")


def test_suplencia_del_presidente_del_ca(con, s, ctx):
    aid = solicitar(con, s, "CONTRATACION_EVENTUAL", "1500", ctx)
    assert seguridad.firmar_autorizacion(con, s["ca_presidente"], aid, suplencia=CONSTANCIA, ahora=LUNES) == "AUTORIZADA"
    f = con.execute("SELECT * FROM autorizacion_firmas WHERE autorizacion_id=?", (aid,)).fetchone()
    assert (f["calidad"], f["es_suplencia"], f["orden_prelacion"], f["documento_ref"]) == ("GG", 1, 1, "FIN-EGR-06 n.º 14")


def test_suplencia_exige_constancia(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "3000", ctx)
    with pytest.raises(DatosInvalidosError) as e:
        seguridad.firmar_autorizacion(con, s["ca_presidente"], aid,
                                      suplencia=Suplencia(causa="Ausencia", constancia_ref=" "))
    assert e.value.codigo == "CONSTANCIA_OBLIGATORIA"
    with pytest.raises(DatosInvalidosError):
        seguridad.firmar_autorizacion(con, s["ca_presidente"], aid,
                                      suplencia=Suplencia(causa="", constancia_ref="FIN-EGR-06 n.º 15"))


def test_tesorero_solo_en_defecto_del_presidente(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "3000", ctx)
    with pytest.raises(AutorizacionError) as e:
        seguridad.firmar_autorizacion(con, s["ca_tesorero"], aid, suplencia=CONSTANCIA)
    assert e.value.codigo == "SUPLENCIA_ORDEN"
    con_motivo = Suplencia(CONSTANCIA.causa, CONSTANCIA.constancia_ref, "Presidente del CA de viaje")
    assert seguridad.firmar_autorizacion(con, s["ca_tesorero"], aid, suplencia=con_motivo) == "AUTORIZADA"
    assert uno(con, "SELECT orden_prelacion FROM autorizacion_firmas WHERE autorizacion_id=?", aid) == 2


@pytest.mark.parametrize("quien", ["ca_secretario", "cv", "gg", "jf"])
def test_suplencia_fuera_del_orden_de_prelacion(con, s, ctx, quien):
    aid = solicitar(con, s, "DESEMBOLSO", "3000", ctx, quien="gg" if quien == "jf" else "jf")
    with pytest.raises(AutorizacionError) as e:
        seguridad.firmar_autorizacion(con, s[quien], aid, suplencia=CONSTANCIA)
    assert e.value.codigo in ("SUPLENCIA_NO_HABILITADA", "SEGREGACION_FUNCIONES")


def test_suplente_y_cv_completan_la_mancomunada(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "6000", ctx)
    assert seguridad.firmar_autorizacion(con, s["ca_presidente"], aid, suplencia=CONSTANCIA) == "PENDIENTE"
    assert seguridad.firmar_autorizacion(con, s["cv"], aid) == "AUTORIZADA"


def test_seguimiento_de_la_suplencia(con, s, ctx):
    a1 = solicitar(con, s, "DESEMBOLSO", "3000", ctx)
    seguridad.firmar_autorizacion(con, s["ca_presidente"], a1, suplencia=CONSTANCIA, ahora=LUNES)
    a2 = solicitar(con, s, "DESEMBOLSO", "2500", ctx)
    seguridad.firmar_autorizacion(con, s["ca_presidente"], a2, suplencia=CONSTANCIA, ahora=LUNES)
    f1, f2 = [r[0] for r in con.execute("SELECT id FROM autorizacion_firmas ORDER BY id")]
    with pytest.raises(AutorizacionError):
        seguridad.registrar_comunicacion_suplencia(con, s["gg"], f1)
    assert seguridad.registrar_comunicacion_suplencia(con, s["ca_presidente"], f1, ahora=LUNES + timedelta(hours=3))
    assert not seguridad.registrar_comunicacion_suplencia(con, s["ca_presidente"], f2, ahora=LUNES + timedelta(hours=25))
    pendientes = {p["id"]: p for p in seguridad.suplencias_pendientes(con, s["cv"], ahora=LUNES + timedelta(days=2))}
    assert not pendientes[f1]["comunicacion_fuera_de_plazo"] and pendientes[f2]["comunicacion_fuera_de_plazo"]
    with pytest.raises(AutorizacionError):
        seguridad.revisar_suplencia(con, s["ca_presidente"], f1)
    seguridad.revisar_suplencia(con, s["gg"], f1)
    assert [p["id"] for p in seguridad.suplencias_pendientes(con, s["gg"])] == [f2]


def test_la_bd_protege_firmas_y_autorizaciones(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "3000", ctx)
    uid = s["ca_presidente"].usuario_id
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("""INSERT INTO autorizacion_firmas (autorizacion_id, calidad, usuario_id, rol_usuario, decision,
                       es_suplencia, orden_prelacion, causa_suplencia, firmado_en)
                       VALUES (?, 'GG', ?, 'CA', 'APRUEBA', 1, 1, 'ausencia', '2026-07-06T09:00:00')""", (aid, uid))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE autorizaciones SET monto_cent = 1 WHERE id = ?", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE autorizaciones SET estado = 'UTILIZADA' WHERE id = ?", (aid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM autorizaciones WHERE id = ?", (aid,))
    con.rollback()


def test_listar_autorizaciones(con, s, ctx):
    aid = solicitar(con, s, "DESEMBOLSO", "6000", ctx)
    seguridad.firmar_autorizacion(con, s["gg"], aid)
    fila = seguridad.listar_autorizaciones(con, s["cv"], estado="PENDIENTE")[0]
    assert fila["id"] == aid and fila["faltan"] == ["CV"] and fila["monto"] == Decimal("6000.00")
    with pytest.raises(AutorizacionError):
        seguridad.listar_autorizaciones(con, s["ut"])


# ============================ Permisos y SoD ==============================
def test_segregacion_resiste_permisos_indebidos(con):
    con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES ('JF','tesoreria','AUTORIZAR','prueba')")
    con.execute("INSERT INTO permisos (rol, recurso, accion, fuente) VALUES ('GG','contabilidad','CREAR','prueba')")
    assert not seguridad.tiene_permiso(con, "JF", "tesoreria", "AUTORIZAR")
    assert not seguridad.tiene_permiso(con, "GG", "contabilidad", "CREAR")
    assert seguridad.motivo_denegacion(con, "JF", "tesoreria", "AUTORIZAR")[0] == "SEGREGACION_FUNCIONES"
    con.rollback()


def test_cv_solo_lectura(con):
    permisos = seguridad.permisos_de_rol(con, "CV")
    assert "COFIRMAR" in permisos["tesoreria"]
    assert all(not ({"CREAR", "EDITAR", "REVERSAR", "AUTORIZAR", "APROBAR"} & acciones)
               for recurso, acciones in permisos.items() if recurso != "vigilancia")
    assert seguridad.tiene_permiso(con, "CV", "vigilancia", "CREAR")
    assert not seguridad.tiene_permiso(con, "CV", "contabilidad", "CREAR")


# ============================== Login =====================================
def test_login_correcto(con):
    sql = "SELECT COUNT(*) FROM auditoria WHERE accion='LOGIN' AND usuario='rflores' AND detalle='Inicio de sesión'"
    antes = uno(con, sql)
    sesion = seguridad.iniciar_sesion(con, "RFLORES", CLAVE)  # el usuario no distingue mayúsculas
    assert (sesion.rol, sesion.cargo, sesion.debe_cambiar_password) == ("GG", "Gerente General (provisional)", False)
    assert uno(con, sql) == antes + 1
    assert uno(con, "SELECT password_hash FROM usuarios WHERE nombre_usuario='rflores'").startswith("$2b$")


def test_bloqueo_por_intentos(con):
    ahora = datetime(2026, 10, 12, 8, 0, 0)
    for _ in range(2):
        with pytest.raises(AutorizacionError) as e:
            seguridad.iniciar_sesion(con, "jfinanzas", "Clave9999", ahora=ahora)
        assert e.value.codigo == "CREDENCIALES_INVALIDAS"
    with pytest.raises(AutorizacionError) as e:
        seguridad.iniciar_sesion(con, "jfinanzas", "Clave9999", ahora=ahora)
    assert e.value.codigo == "USUARIO_BLOQUEADO" and "15 minutos" in str(e.value)
    with pytest.raises(AutorizacionError) as e:
        seguridad.iniciar_sesion(con, "jfinanzas", CLAVE, ahora=ahora + timedelta(minutes=14))
    assert e.value.codigo == "USUARIO_BLOQUEADO"
    sesion = seguridad.iniciar_sesion(con, "jfinanzas", CLAVE, ahora=ahora + timedelta(minutes=16))
    assert sesion.rol == "JF"
    assert uno(con, "SELECT intentos_fallidos FROM usuarios WHERE nombre_usuario='jfinanzas'") == 0


def test_usuario_inexistente_mismo_mensaje(con):
    with pytest.raises(AutorizacionError) as e:
        seguridad.iniciar_sesion(con, "nadie", CLAVE)
    assert str(e.value) == seguridad.MENSAJE_CREDENCIALES
    assert uno(con, "SELECT COUNT(*) FROM auditoria WHERE usuario='nadie' AND accion='LOGIN'") == 1


def test_usuario_desactivado_y_sesion_invalidada(con, s):
    seguridad.actualizar_usuario(con, s["gg"], s["jf"].usuario_id, activo=False)
    with pytest.raises(AutorizacionError) as e:
        seguridad.iniciar_sesion(con, "jfinanzas", CLAVE)
    assert e.value.codigo == "USUARIO_INACTIVO"
    with pytest.raises(AutorizacionError) as e:
        seguridad.exigir_permiso(con, s["jf"], "contabilidad", "VER")
    assert e.value.codigo == "SESION_INVALIDA"
    with pytest.raises(ReglaNegocioError):
        seguridad.actualizar_usuario(con, s["gg"], s["gg"].usuario_id, activo=False)


def test_caducidad_de_90_dias(con):
    cambio = datetime.fromisoformat(uno(con, "SELECT ultimo_cambio_password FROM usuarios WHERE nombre_usuario='rflores'"))
    assert not seguridad.iniciar_sesion(con, "rflores", CLAVE, ahora=cambio + timedelta(days=89)).debe_cambiar_password
    assert seguridad.iniciar_sesion(con, "rflores", CLAVE, ahora=cambio + timedelta(days=90)).debe_cambiar_password


def test_usuario_nuevo_debe_cambiar_y_politica(con, s):
    assert s["jf"].debe_cambiar_password
    with pytest.raises(AutorizacionError):
        seguridad.cambiar_password(con, s["jf"], "otra", "NuevaClave1")
    for mala, motivo in (("Corta1", "al menos 8"), ("SoloLetras", "letras y números"), (CLAVE, "distinta")):
        with pytest.raises(DatosInvalidosError, match=motivo):
            seguridad.cambiar_password(con, s["jf"], CLAVE, mala)
    nueva = seguridad.cambiar_password(con, s["jf"], CLAVE, "Caranavi2026")
    assert not nueva.debe_cambiar_password
    assert not seguridad.iniciar_sesion(con, "jfinanzas", "Caranavi2026").debe_cambiar_password


def test_administracion_de_usuarios(con, s):
    with pytest.raises(AutorizacionError):
        seguridad.crear_usuario(con, s["jf"], "nuevo", "Nuevo", "UT", CLAVE)
    with pytest.raises(DatosInvalidosError, match="ya existe"):
        seguridad.crear_usuario(con, s["gg"], "granja", "Otro", "UT", CLAVE)
    socio_7 = uno(con, "SELECT id FROM socios WHERE numero=7")
    seguridad.crear_usuario(con, s["gg"], "achavez", "Alan Alberth Chavez Colmena", "CV", CLAVE, socio_7)
    with pytest.raises(ReglaNegocioError) as e:
        seguridad.crear_usuario(con, s["gg"], "achavez2", "Duplicado", "CV", CLAVE, socio_7)
    assert e.value.codigo == "SOCIO_CON_USUARIO"
    with pytest.raises(ReglaNegocioError) as e:
        seguridad.crear_usuario_inicial(con, "otro", "Otro", CLAVE)
    assert e.value.codigo == "SISTEMA_YA_INICIALIZADO"
    usuarios = seguridad.listar_usuarios(con, s["gg"])
    assert all("password_hash" not in u for u in usuarios) and len(usuarios) == 10


def test_restablecer_desbloquea(con, s):
    ahora = datetime(2026, 10, 12, 8, 0, 0)
    for _ in range(3):
        with pytest.raises(AutorizacionError):
            seguridad.iniciar_sesion(con, "granja", "Mala12345", ahora=ahora)
    seguridad.restablecer_password(con, s["gg"], s["ut"].usuario_id, "Temporal2026")
    sesion = seguridad.iniciar_sesion(con, "granja", "Temporal2026", ahora=ahora)
    assert sesion.debe_cambiar_password


def test_sesion_vencida():
    base = datetime(2026, 10, 12, 8, 0, 0)
    assert not seguridad.sesion_vencida(base, 30, ahora=base + timedelta(minutes=30))
    assert seguridad.sesion_vencida(base, 30, ahora=base + timedelta(minutes=31))
    with pytest.raises(DatosInvalidosError):
        seguridad.sesion_vencida(base, 0)
