"""Pruebas de la Fase 11: archivo contable (servicios/archivo.py, MAN-ARC-01)."""
from __future__ import annotations

from datetime import date

import pytest

from servicios import archivo, rau
from servicios.excepciones import AutorizacionError, DatosInvalidosError, ReglaNegocioError


def test_nomenclatura_man_arc_01():
    assert archivo.codigo_gestion(2026) == "G2026-27" and archivo.codigo_gestion(2026, True) == "G2627"
    assert archivo.codigo_gestion(2099, True) == "G9900"
    assert archivo.ruta_archivo(2026, "ING", "pr-fin-09") == "AF-FIN/G2026-27/ING/PR-FIN-09"
    assert archivo.codigo_expediente("ING", 2026, 1) == "EXP-ING-G2627-001"
    assert archivo.codigo_expediente("TRA", 2026, 12) == "EXP-C03-G2627-012"
    assert archivo.codigo_caja(2026, 7) == "CAJ-2026-007"
    assert archivo.nombre_digital("FIN-ING-02", "2026-08-15", 1, "1.0", "APR") == "FIN-ING-02_20260815_0001_v1.0_APR.pdf"
    with pytest.raises(DatosInvalidosError):
        archivo.nombre_digital("FIN-ING-02", "2026-08-15", 1, "1", "APR")
    with pytest.raises(DatosInvalidosError):
        archivo.nombre_digital("FIN ING", "2026-08-15", 1, "1.0", "APR")
    with pytest.raises(DatosInvalidosError):
        archivo.codigo_expediente("XXX", 2026, 1)


def test_clases_de_retencion_y_vencimientos(con, s):
    assert archivo.calcular_vencimiento(con, 2026, "T10", "CIERRE") == date(2037, 6, 30)
    assert archivo.calcular_vencimiento(con, 2026, "T5", "CIERRE") == date(2032, 6, 30)
    assert archivo.calcular_vencimiento(con, 2026, "DESECHO", "CIERRE") == date(2029, 6, 30)
    assert archivo.calcular_vencimiento(con, 2026, "PERMANENTE", "CIERRE") is None
    assert archivo.calcular_vencimiento(con, 2026, "VIDA_UTIL_10", "BAJA", "2040-03-15") == date(2050, 3, 15)
    assert archivo.calcular_vencimiento(con, 2026, "T5", "SUSTITUCION") is None
    assert archivo.sumar_anios(date(2028, 2, 29), 1) == date(2029, 2, 28)
    tabla = archivo.tabla_retencion(con, s["ut"], "TES")
    assert len(tabla) == 12 and tabla[0]["codigo"].startswith("FIN-TES")
    with pytest.raises(AutorizacionError):
        archivo.tabla_retencion(con, s["ag"])


def test_expediente_foliacion_por_tomos_y_cierre(con, s):
    e = archivo.abrir_expediente(con, s["jf"], ciclo="ING", gestion=2026, titulo="Notas de entrega HORECA",
                                 procedimiento="PR-FIN-09", codigo_documental="FIN-ING-03")
    assert e["codigo"] == "EXP-ING-G2627-001" and e["ruta"] == "AF-FIN/G2026-27/ING/PR-FIN-09"
    d1 = archivo.registrar_documento(con, s["jf"], e["id"], codigo_formato="FIN-ING-03", fecha="2026-08-01",
                                     descripcion="Notas 1-50", folios=150)
    d2 = archivo.registrar_documento(con, s["jf"], e["id"], codigo_formato="FIN-ING-03", fecha="2026-08-31",
                                     descripcion="Notas 51-80", folios=60)
    assert (d1["tomo"], d1["folio_desde"], d1["folio_hasta"]) == (1, 1, 150)
    assert (d2["tomo"], d2["folio_desde"], d2["folio_hasta"]) == (2, 151, 210)
    assert d2["nombre_digital"] == "FIN-ING-03_20260831_0002_v1.0_APR.pdf"
    with pytest.raises(ReglaNegocioError):
        archivo.registrar_documento(con, s["jf"], e["id"], codigo_formato="FIN-ING-03", fecha="2026-09-01",
                                    descripcion="Demasiado", folios=201)
    r = archivo.cerrar_expediente(con, s["jf"], e["id"], "2027-06-30", caja="CAJ-2027-001")
    assert r["vencimiento"] is not None
    with pytest.raises(ReglaNegocioError):
        archivo.registrar_documento(con, s["jf"], e["id"], codigo_formato="FIN-ING-03", fecha="2027-07-01",
                                    descripcion="Tarde", folios=1)
    with pytest.raises(AutorizacionError):
        archivo.abrir_expediente(con, s["cv"], ciclo="ING", gestion=2026, titulo="X", procedimiento="PR-FIN-09",
                                 codigo_documental="FIN-ING-03")


def test_expediente_por_sustitucion_exige_fecha_base(con, s):
    cod = con.execute("SELECT codigo FROM retencion_documental WHERE computo = 'SUSTITUCION' LIMIT 1").fetchone()[0]
    ciclo = con.execute("SELECT ciclo FROM retencion_documental WHERE codigo = ?", (cod,)).fetchone()[0]
    e = archivo.abrir_expediente(con, s["jf"], ciclo=ciclo, gestion=2026, titulo="Documento sustituible",
                                 procedimiento="PR-FIN-01", codigo_documental=cod)
    with pytest.raises(DatosInvalidosError):
        archivo.cerrar_expediente(con, s["jf"], e["id"], "2027-06-30")
    assert archivo.cerrar_expediente(con, s["jf"], e["id"], "2027-06-30", fecha_base="2028-01-10")["vencimiento"]


def test_libro_de_prestamo(con, s):
    e = archivo.abrir_expediente(con, s["jf"], ciclo="EGR", gestion=2026, titulo="Órdenes de compra",
                                 procedimiento="PR-FIN-05", clase="T10", computo="CIERRE")
    with pytest.raises(AutorizacionError):
        archivo.prestar_expediente(con, s["jf"], e["id"], solicitante="Auditor externo", cargo="Auditor",
                                   motivo="Auditoría", original=True, fecha_salida="2027-08-01",
                                   devolucion_prevista="2027-08-05")
    pid = archivo.prestar_expediente(con, s["jf"], e["id"], solicitante="Auditor externo", cargo="Auditor",
                                     motivo="Auditoría", original=True, fecha_salida="2027-08-01",
                                     devolucion_prevista="2027-08-05", sesion_autoriza=s["gg"])
    with pytest.raises(ReglaNegocioError):
        archivo.prestar_expediente(con, s["jf"], e["id"], solicitante="Otro", cargo="X", motivo="Y", original=False,
                                   fecha_salida="2027-08-02", devolucion_prevista="2027-08-03")
    assert archivo.libro_prestamos(con, s["cv"], "2027-08-10", True)[0]["atrasado"] is True
    assert archivo.devolver_expediente(con, s["jf"], pid, "2027-08-08")["dias_atraso"] == 3
    with pytest.raises(ReglaNegocioError):
        archivo.devolver_expediente(con, s["jf"], pid, "2027-08-09")


def _cerrado(con, s, clase, gestion=2026, titulo="Expediente"):
    e = archivo.abrir_expediente(con, s["jf"], ciclo="TES", gestion=gestion, titulo=titulo,
                                 procedimiento="PR-FIN-01", clase=clase, computo="CIERRE")
    archivo.cerrar_expediente(con, s["jf"], e["id"], "2027-06-30")
    return e["id"]


def test_acta_de_destruccion_gg_autoriza_cv_visa(con, s):
    vencido = _cerrado(con, s, "T5", titulo="Indicadores ROA")          # vence 30-jun-2032
    permanente = _cerrado(con, s, "PERMANENTE", titulo="CAPEX")
    with pytest.raises(ReglaNegocioError, match="vence"):
        archivo.preparar_acta(con, s["jf"], "DESTRUCCION", "2030-01-10", "Trituración", [vencido])
    with pytest.raises(ReglaNegocioError, match="permanente"):
        archivo.preparar_acta(con, s["jf"], "DESTRUCCION", "2032-07-10", "Trituración", [permanente])
    assert [e["id"] for e in archivo.expedientes_elegibles(con, s["jf"], "DESTRUCCION", "2032-07-10")] == [vencido]
    acta = archivo.preparar_acta(con, s["jf"], "DESTRUCCION", "2032-07-10", "Trituración", [vencido])
    assert acta["numero"] == "001/2032"
    with pytest.raises(ReglaNegocioError):
        archivo.ejecutar_acta(con, s["jf"], acta["id"], "2032-07-15")
    with pytest.raises(AutorizacionError):
        archivo.autorizar_acta(con, s["jf"], acta["id"])
    archivo.autorizar_acta(con, s["gg"], acta["id"])
    with pytest.raises(ReglaNegocioError):
        archivo.ejecutar_acta(con, s["jf"], acta["id"], "2032-07-15")         # falta el visado del CV
    archivo.visar_acta(con, s["cv"], acta["id"])
    assert archivo.ejecutar_acta(con, s["jf"], acta["id"], "2032-07-15")["estado"] == "DESTRUIDO"
    assert con.execute("SELECT estado FROM expedientes_archivo WHERE id = ?", (vencido,)).fetchone()[0] == "DESTRUIDO"
    with pytest.raises(Exception):
        con.execute("DELETE FROM expedientes_archivo WHERE id = ?", (vencido,))


def test_destruccion_bloqueada_por_fiscalizacion_o_bloqueo_legal(con, s):
    a = _cerrado(con, s, "DESECHO", titulo="Borradores")                # vence 30-jun-2029
    rau.registrar_notificacion(con, s["jf"], fecha_recepcion="2029-07-01", entidad="GERENCIA_DISTRITAL",
                               tipo="ORDEN_VERIFICACION", numero="OV-1", funcionario="Lic. Rojas", gestiones=[2026],
                               vencimiento="2029-07-20")
    with pytest.raises(ReglaNegocioError, match="fiscalización"):
        archivo.preparar_acta(con, s["jf"], "DESTRUCCION_SIMPLIFICADA", "2029-07-10", "Reciclaje", [a])
    b = _cerrado(con, s, "DESECHO", gestion=2027, titulo="Borradores 2027")   # vence 30-jun-2030
    archivo.registrar_bloqueo_legal(con, s["gg"], b, "Litigio laboral")
    with pytest.raises(ReglaNegocioError, match="bloqueo legal"):
        archivo.preparar_acta(con, s["jf"], "DESTRUCCION_SIMPLIFICADA", "2030-07-10", "Reciclaje", [b])
    archivo.registrar_bloqueo_legal(con, s["gg"], b, None)
    acta = archivo.preparar_acta(con, s["jf"], "DESTRUCCION_SIMPLIFICADA", "2030-07-10", "Reciclaje", [b])
    archivo.autorizar_acta(con, s["gg"], acta["id"])
    with pytest.raises(ReglaNegocioError):
        archivo.visar_acta(con, s["cv"], acta["id"])                       # la simplificada no se visa
    assert archivo.ejecutar_acta(con, s["jf"], acta["id"], "2030-07-11")["expedientes"] == 1


def test_acta_de_transferencia_solo_permanentes(con, s):
    perm = _cerrado(con, s, "PERMANENTE", titulo="Plan CAPEX")
    t10 = _cerrado(con, s, "T10", titulo="Informes")
    with pytest.raises(ReglaNegocioError, match="permanente"):
        archivo.preparar_acta(con, s["jf"], "TRANSFERENCIA", "2027-08-01", "Archivo histórico", [t10])
    acta = archivo.preparar_acta(con, s["jf"], "TRANSFERENCIA", "2027-08-01", "Archivo histórico", [perm])
    with pytest.raises(ReglaNegocioError, match="otra acta"):
        archivo.preparar_acta(con, s["jf"], "TRANSFERENCIA", "2027-08-02", "Archivo histórico", [perm])
    archivo.autorizar_acta(con, s["gg"], acta["id"])
    assert archivo.ejecutar_acta(con, s["jf"], acta["id"], "2027-08-03")["estado"] == "TRANSFERIDO"
    assert archivo.listar_actas(con, s["ca_presidente"])[0]["expedientes"][0]["id"] == perm
    res = archivo.resumen_archivo(con, s["jf"], "2027-08-03")
    assert res["por_estado"] == {"CERRADO": 1, "TRANSFERIDO": 1}
