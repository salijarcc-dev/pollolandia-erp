"""Formatos PDF de la Fase 11: RAU, cumplimiento legal y archivo contable.

* Calendario fiscal anual (POL-07; PET-RAU §3.1).
* Liquidación de la cuota RAU por socio (POL-07 Art. 3).
* INF-CV-09 trimestral (10 verificaciones, PET-RAU §4.1) e INF-CV-09 anual
  (5 verificaciones, Anexo 10).
* FIN-TRA-13 Balance de masas trimestral del alimento (propuesto en el PET-RAU).
* Índice maestro del expediente EXP-RAU (A-G).
* Actas de transferencia y destrucción del archivo (MAN-ARC-01).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _tabla
from utils.formato import fecha_pantalla

CICLO_TRV = "Transversal — Cumplimiento legal y fiscal"
CICLO_TRA = "Ciclo 3 — Transformación"
EVENTO_ESTADO = {"CUMPLIDO": "Cumplido", "CUMPLIDO_CON_OBSERVACION": "Cumplido con observación",
                 "VENCIDO": "Vencido", "ALERTA_30": "Alerta 30 días", "ALERTA_60": "Alerta 60 días",
                 "PROGRAMADO": "Programado"}


def _pct(valor: Any) -> str:
    if valor is None:
        return "—"
    return f"{(Decimal(valor) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}".replace(".", ",") + " %"


def _qq(valor: Any) -> str:
    if valor is None:
        return "—"
    return f"{Decimal(valor):,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calendario_fiscal(gestion: int, eventos: Sequence[Mapping[str, Any]], ctx: Contexto) -> bytes:
    """Calendario fiscal anual de la gestión con el estado de cada obligación."""
    cuerpo = _encabezado("CAL-RAU", f"Calendario Fiscal RAU — Gestión {gestion}", "Finanzas", CICLO_TRV) + _contexto(ctx)
    cuerpo += [_p("Obligaciones y fechas límite", E_SUB),
               _tabla(["Obligación", "Límite", "Cumplido", "Referencia", "Estado", "Semáforo"],
                      [[e["descripcion"], fecha_pantalla(e["limite"]),
                        fecha_pantalla(e["cumplido_en"]) if e["cumplido_en"] else "—", e["referencia"] or "—",
                        EVENTO_ESTADO.get(e["estado"], e["estado"]), e["semaforo"]] for e in eventos],
                      [5.6, 2, 2, 3.4, 2.6, 1.8]),
               Spacer(1, 4),
               _p("Vencimiento ordinario del Formulario 701 V.3: 31 de octubre. Presentación con al menos 15 días "
                  "hábiles de anticipación (POL-07 Art. 2); liquidación por socio 20 días hábiles antes y consulta de "
                  "la RND hasta el 30 de septiembre (Art. 3). Alertas a 60 y 30 días.", E_PEQ)]
    cuerpo += _firmas(["Elaborado: Jefe de Finanzas", "Revisado: Gerente General"])
    return _construir("CAL-RAU", cuerpo, CICLO_TRV)


def liquidacion_rau(liq: Mapping[str, Any], ctx: Contexto) -> bytes:
    """Liquidación de la cuota RAU con el reparto en partes iguales."""
    cuerpo = _encabezado("LIQ-RAU", f"Liquidación de la Cuota RAU — Gestión {liq['gestion']}", "Finanzas",
                         CICLO_TRV) + _contexto(ctx)
    cuerpo += [
        _p("Cálculo", E_SUB),
        _tabla(["Concepto", "Valor"], [
            ["Superficie declarada (ha)", _qq(liq["hectareas"])], ["Cuota por hectárea", _bs(liq["cuota_ha"])],
            ["Cuota total (hectáreas × cuota por ha)", _bs(liq["total"])], ["Socios activos", str(liq["socios"])],
            ["Asiento de devengo (7.1.05.02 / 2.1.03.01)", liq.get("asiento") or "Pendiente"],
        ], [12.4, 5], (1,)),
        Spacer(1, 4)]
    if liq.get("advertencias"):
        cuerpo += [_p("ADVERTENCIAS — DATOS POR VALIDAR", E_SUB)] + [_p(f"• {a}") for a in liq["advertencias"]]
        cuerpo.append(Spacer(1, 4))
    cuerpo += [_p("Liquidación individual por socio", E_SUB),
               _tabla(["N.º", "Socio", "C.I.", "Monto Bs", "Firma"],
                      [[str(r["numero"]), r["socio"], f"{r['ci']} {r['ci_expedido']}", _bs(r["monto"]), ""]
                       for r in liq["socios_detalle"]] + [["", "TOTAL", "", _bs(liq["total"]), ""]],
                      [1.2, 6.6, 3, 2.6, 4], (3,)),
               _p(f"Comunicar hasta el {fecha_pantalla(liq['limite_comunicacion'])} (20 días hábiles antes del "
                  "vencimiento). " + (f"Comunicada el {fecha_pantalla(liq['comunicada_en'])}."
                                      if liq.get("comunicada_en") else "Pendiente de comunicación."), E_PEQ)]
    cuerpo += _firmas(["Elaborado: Jefe de Finanzas", "Aprobado: Gerente General"])
    return _construir("LIQ-RAU", cuerpo, CICLO_TRV)


def inf_cv_09_trimestral(revision: Mapping[str, Any], ctx: Contexto) -> bytes:
    """Anexo trimestral al INF-CV-09 (PET-RAU §4.1)."""
    cuerpo = _encabezado("INF-CV-09", f"Revisión Trimestral RAU — T{revision['trimestre']}/{revision['anio']}",
                         "Consejo de Vigilancia", CICLO_TRV) + _contexto(ctx)
    cuerpo += [_p("Verificaciones", E_SUB),
               _tabla(["N.º", "Verificación", "Fuente", "Meta", "Resultado", "Semáforo"],
                      [[str(v["numero"]), v["verificacion"], v["fuente"], v["meta"], v["resultado"], v["semaforo"]]
                       for v in revision["verificaciones"]], [0.8, 4.6, 2.6, 1.8, 5.8, 1.8]),
               Spacer(1, 4), _p(f"Semáforo general: {revision['semaforo']}", E_SUB),
               _p("Todo rojo se reporta a la Gerencia General en 24 horas y al Consejo de Administración en 5 días "
                  "hábiles (PET-RAU §1.4).", E_PEQ)]
    if revision.get("observaciones"):
        cuerpo += [_p("Observaciones", E_SUB), _p(revision["observaciones"])]
    cuerpo += _firmas(["Presidente del Consejo de Vigilancia", "Secretario del Consejo de Vigilancia"])
    return _construir("INF-CV-09", cuerpo, CICLO_TRV)


def inf_cv_09_anual(revision: Mapping[str, Any], ctx: Contexto) -> bytes:
    """INF-CV-09 anual: Formulario 701 y patentes presentados y pagados en plazo."""
    cuerpo = _encabezado("INF-CV-09", f"Verificación Anual de Cumplimiento RAU — Gestión {revision['anio']}",
                         "Consejo de Vigilancia", CICLO_TRV) + _contexto(ctx)
    cuerpo += [_tabla(["Verificación", "Cumple", "Observación"],
                      [[v["verificacion"], "Sí" if v["cumple"] else "No", v["observacion"]]
                       for v in revision["verificaciones"]], [8.4, 1.6, 7.4]),
               Spacer(1, 4), _p(f"Semáforo general: {revision['semaforo']}", E_SUB)]
    if revision.get("observaciones"):
        cuerpo += [_p(revision["observaciones"])]
    cuerpo += _firmas(["Presidente del Consejo de Vigilancia", "Secretario del Consejo de Vigilancia"])
    return _construir("INF-CV-09", cuerpo, CICLO_TRV)


def fin_tra_13(balance: Mapping[str, Any], ctx: Contexto) -> bytes:
    """FIN-TRA-13 Balance de masas trimestral del alimento balanceado."""
    cuerpo = _encabezado("FIN-TRA-13", f"Balance de Masas del Alimento — T{balance['trimestre']} ejercicio "
                         f"{balance['ejercicio']}", "Finanzas / Granja", CICLO_TRA) + _contexto(ctx)
    cuerpo += [_p(f"Período: {fecha_pantalla(balance['desde'])} al {fecha_pantalla(balance['hasta'])}", E_SUB),
               _tabla(["Concepto", "qq"], [
                   ["Producción del trimestre", _qq(balance["producido"])],
                   ["(–) Consumo propio (galpones)", _qq(balance["consumo"])],
                   [f"(–) Merma ({_pct(balance.get('merma_pct'))}; semáforo {balance.get('merma_semaforo') or '—'})",
                    _qq(balance["merma"])],
                   ["(=) Excedente disponible", _qq(balance["excedente"])],
                   ["Consumo proyectado del período siguiente", _qq(balance["consumo_proyectado"])],
                   ["Excedente autorizado para la venta", _qq(balance.get("autorizado"))],
               ], [12.4, 5], (1,)),
               Spacer(1, 4),
               _p("Solo se vende lo que excede el consumo proyectado (R5) y dentro del tope del programa anual aprobado "
                  "por el Consejo de Administración (R6).", E_PEQ)]
    cuerpo += _firmas(["Preparado: Jefe de Finanzas", "Autorizado: Gerente General"],
                      [balance.get("preparado_por") or "", balance.get("firmado_por") or ""])
    return _construir("FIN-TRA-13", cuerpo, CICLO_TRA)


def indice_maestro(indice: Mapping[str, Any], ctx: Contexto) -> bytes:
    """Índice maestro del expediente EXP-RAU con la completitud por carpeta."""
    cuerpo = _encabezado("EXP-RAU", f"Índice Maestro del Expediente RAU — Gestión {indice['gestion']}", "Finanzas",
                         CICLO_TRV) + _contexto(ctx)
    cuerpo += [_p(f"Completitud: {_pct(indice['completitud'])} ({indice['cubiertos']} de {indice['total']}); meta "
                  f"{_pct(indice['minimo'])}", E_SUB)]
    for c in indice["carpetas"]:
        cuerpo += [_p(f"Carpeta {c['carpeta']} — {_pct(c['completitud'])}", E_SUB),
                   _tabla(["Clave", "Documento", "Responsable", "Periodicidad", "Archivado"],
                          [[i["clave"], i["documento"], i["responsable"], i["periodicidad"].capitalize(),
                            ", ".join(d["referencia"] for d in i["documentos"]) or "PENDIENTE"] for i in c["items"]],
                          [1.3, 6.2, 2.8, 2.1, 5])]
    cuerpo += _firmas(["Responsable del expediente: Jefe de Finanzas", "Verificado: Consejo de Vigilancia"])
    return _construir("EXP-RAU", cuerpo, CICLO_TRV)


def acta_archivo(acta: Mapping[str, Any], ctx: Contexto) -> bytes:
    """Acta de transferencia o de destrucción documental (MAN-ARC-01)."""
    codigo = "ACT-TRF" if acta["tipo"] == "TRANSFERENCIA" else "ACT-DES"
    cuerpo = _encabezado(codigo, f"Acta de {acta['tipo_txt']} N.º {acta['numero_txt']}", "Archivo contable",
                         CICLO_TRV) + _contexto(ctx)
    cuerpo += [_p(f"Fecha del acta: {fecha_pantalla(acta['fecha'])}. Método: {acta['metodo']}. Estado: "
                  f"{acta['estado'].lower()}.", E_SUB),
               _tabla(["Expediente", "Título", "Clase", "Vencimiento", "Folios"],
                      [[e["codigo"], e["titulo"], e["clase"], fecha_pantalla(e["vencimiento"]) if e["vencimiento"]
                        else "—", str(e["folios"])] for e in acta["expedientes"]], [4, 6.4, 2.4, 2.6, 2], (4,))]
    if acta.get("observacion"):
        cuerpo += [Spacer(1, 4), _p(acta["observacion"])]
    etiquetas = ["Elaborado: Jefe de Finanzas", "Autorizado: Gerente General"]
    nombres = [acta["elaborado_por"], acta.get("autorizado_por") or ""]
    if acta["tipo"] == "DESTRUCCION":
        etiquetas.append("Visado: Consejo de Vigilancia")
        nombres.append(acta.get("visado_por") or "")
    cuerpo += _firmas(etiquetas, nombres)
    return _construir(codigo, cuerpo, CICLO_TRV)
