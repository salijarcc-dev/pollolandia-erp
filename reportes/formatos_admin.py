"""Formatos de la administración del sistema (Fase 13).

* Acta de Prueba de Restauración de Respaldo (MAN-ARC-01 §5.2): resultado de
  cada verificación, firma de la Unidad de Finanzas y visto del Consejo de
  Vigilancia.
* Informe de Revisión de Seguridad (controles de acceso y segregación).
"""
from __future__ import annotations

from typing import Any, Mapping

from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import E_PEQ, E_SUB, Contexto, _contexto, _construir, _encabezado, _firmas, _p, _tabla
from utils.formato import fecha_pantalla

CICLO_ARCHIVO = "Transversal — Archivo contable y seguridad de la información"
NOMBRE_UBICACION = {"LOCAL": "Equipo de trabajo", "EXTERNO": "Disco externo local",
                    "FUERA_SITIO": "Fuera de la granja / nube"}


def acta_restauracion(acta: Mapping[str, Any], ctx: Contexto) -> bytes:
    """ACTA-RES: prueba de restauración de un respaldo."""
    cuerpo = _encabezado("ACTA-RES", f"Acta de Prueba de Restauración de Respaldo N.º {acta['id']}",
                         "Unidad de Finanzas y Comercialización", CICLO_ARCHIVO) + _contexto(ctx)
    cuerpo += [_tabla(["Dato", "Detalle"], [
        ["Respaldo", f"{acta['tipo'].capitalize()} {acta['referencia']} del {fecha_pantalla(acta['fecha_respaldo'])}"],
        ["Paquete", acta["archivo"]],
        ["Huella SHA-256 registrada", acta["sha256"]],
        ["Copia restaurada", NOMBRE_UBICACION.get(acta["ubicacion"], acta["ubicacion"])],
        ["Copias del respaldo (regla 3-2-1)", f"{acta['copias']} de 3"],
        ["Fecha de la prueba", fecha_pantalla(acta["fecha"])],
        ["Duración", f"{acta['duracion_ms'] / 1000:.1f} s".replace(".", ",")],
        ["Resultado", "CONFORME [X]   FALLIDA [ ]" if acta["resultado"] == "CONFORME" else "CONFORME [ ]   FALLIDA [X]"],
    ], [5.5, 11.9]), Spacer(1, 4), _p("Verificaciones realizadas (restauración en carpeta temporal)", E_SUB)]
    cuerpo += [_tabla(["N.º", "Control", "Resultado", "Detalle"],
                      [[str(n), v["control"], "[X] Cumple" if v["cumple"] else "[ ] No cumple", v["detalle"]]
                       for n, v in enumerate(acta["verificaciones"], start=1)], [1, 6.6, 2.4, 7.4])]
    cuerpo += [Spacer(1, 4),
               _p("La restauración se hizo en una carpeta temporal sin tocar la base en uso. La prueba es semestral y "
                  "la verifica el Consejo de Vigilancia (MAN-ARC-01 §5.2).", E_PEQ)]
    if acta.get("observacion_cv"):
        cuerpo += [_p("Observación del Consejo de Vigilancia", E_SUB), _p(acta["observacion_cv"])]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Verificado por (Consejo de Vigilancia)"],
                      [acta["elaborado_por"], acta.get("verificado_por") or ""])
    return _construir("ACTA-RES", cuerpo, CICLO_ARCHIVO)


def informe_seguridad(rev: Mapping[str, Any], ctx: Contexto) -> bytes:
    """REV-SEG: revisión de seguridad con semáforo por control."""
    cuerpo = _encabezado("REV-SEG", "Informe de Revisión de Seguridad del ERP", "Gerencia General",
                         CICLO_ARCHIVO) + _contexto(ctx)
    r = rev["resumen"]
    cuerpo += [_p(f"Controles en verde: {r['VERDE']} · amarillo: {r['AMARILLO']} · rojo: {r['ROJO']}.", E_SUB),
               _tabla(["Área", "Control", "Semáforo", "Detalle", "Fuente"],
                      [[c["area"], c["control"], c["semaforo"].capitalize(), c["detalle"], c["fuente"]]
                       for c in rev["controles"]], [2.2, 3.8, 1.8, 6.6, 3])]
    cuerpo += _firmas(["Revisado por (Gerencia General)", "Visto (Consejo de Vigilancia)"])
    return _construir("REV-SEG", cuerpo, CICLO_ARCHIVO)
