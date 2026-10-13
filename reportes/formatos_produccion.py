"""Formatos PDF del Ciclo 3 — Transformación (Fase 6), con reportlab.

Replican los formatos del Anexo 7 (Ciclo de Transformación) y del Anexo 6:
FIN-TRA-01 (Ficha de Apertura de Ciclo), FIN-TRA-02 (Bitácora Diaria de
Ciclo), FIN-TRA-04 (Registro de Baja por Mortalidad), FIN-TRA-05 (Registro de
Faenado), FIN-TRA-11 (Bitácora de Disposición Sanitaria), el Reporte de Alerta
Sanitaria (CI-FIN-11, Pasos 4 y 5) y la etiqueta QR «Vida Digna» del lote
(POL-03 Art. 3), con bloque de firmas y riesgos mitigados (MAN-FIN-001).

Reciben los diccionarios de ``servicios.produccion`` y devuelven bytes. No
importan ``streamlit``.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Sequence

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import cm
from reportlab.platypus import Spacer

from reportes.formatos_tesoreria import (
    E_NORMAL, E_PEQ, E_SUB, Contexto, _bs, _construir, _contexto, _encabezado, _firmas, _p, _si_no, _tabla,
)
from servicios.produccion import (
    MEDIDAS, METODOS_DISPOSICION, NOMBRE_FASE, PUNTOS_CI_FIN_12, TIPOS_ALERTA, kg_texto, pct_texto,
)
from utils.formato import fecha_pantalla

PIE = "Ciclo 3 — Transformación"
TRANSFORMACION = "Ciclo 3 — Transformación (Producción)"

__all__ = ["Contexto", "fin_tra_01", "fin_tra_02", "fin_tra_04", "fin_tra_05", "fin_tra_11", "alerta_ci_fin_11",
           "etiqueta_qr"]


def _riesgos(nota: str, filas: Sequence[Sequence[str]]) -> list[Any]:
    return [Spacer(1, 8), _p("Riesgos Mitigados — Referencia al Mapa de Riesgos (MAN-FIN-001)", E_SUB),
            _p(nota, E_PEQ), Spacer(1, 3),
            _tabla(["N°", "Riesgo (síntesis)", "Nivel", "Tipo de Control", "Objetivo COSO"], filas,
                   [1, 8.6, 2.2, 2.6, 3])]


def _n(valor: Any) -> str:
    return f"{int(valor):,}".replace(",", ".")


def _kg(valor: Any) -> str:
    return kg_texto(int(Decimal(str(valor)) * 1000))


_R6 = ["6", "Falla de inocuidad o de técnica en el faenado que eleve la merma más allá del rango normal.", "Alto"]
_R8 = ["8", "Mortalidad anormal por brote no detectado ni reportado, con pérdida absorbida indebidamente en el costo.",
       "Alto"]
_R9 = ["9", "Foco infeccioso o falla de bioseguridad no reportado oportunamente por la Unidad Técnica a la Gerencia.",
       "Alto"]
_R22 = ["22", "Error en la acumulación diaria de sufijos que distorsione el costo unitario del lote.", "Moderado"]
_R23 = ["23", "Clasificación incorrecta entre pérdida normal y anormal para evitar el gasto no operativo.", "Moderado"]
_R39 = ["39", "Cierre del ciclo sin conciliación física del número de aves, ocultando faltantes no reportados.", "Bajo"]


# ==========================================================================
# FIN-TRA-01 — Ficha de Apertura de Ciclo Productivo
# ==========================================================================
def fin_tra_01(ciclo: dict[str, Any], ctx: Contexto) -> bytes:
    lotes = ", ".join(f"{l['codigo']} ({_n(l['aves_iniciales'])} aves, {str(l['densidad']).replace('.', ',')} aves por m2)"
                      for l in ciclo["lotes"])
    cuerpo = _encabezado("FIN-TRA-01", "Ficha de Apertura de Ciclo Productivo", "Unidad de Finanzas y Comercialización",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Registra la recepción de los pollitos BB en la cuenta 1.1.03.01 y la transferencia íntegra, el mismo "
                  "día, al centro de costos 6.1.01.01 (sufijo 01), abriendo formalmente el ciclo.", E_NORMAL),
               Spacer(1, 4),
               _tabla(["Dato", "Detalle"], [
                   ["N° de Ciclo / Lote", f"Ciclo {ciclo['numero']} del ejercicio {ciclo['ejercicio']} · {lotes}"],
                   ["Fecha de recepción de pollitos BB", fecha_pantalla(ciclo["fecha_inicio"])],
                   ["Cantidad de pollitos BB recibidos", _n(ciclo["aves_iniciales"])],
                   ["Proveedor", ciclo.get("proveedor") or "—"],
                   ["Certificado sanitario del proveedor", ciclo.get("certificado_sanitario") or "—"],
                   ["Precio de adquisición unitario (Bs)",
                    _bs(Decimal(ciclo["costo_pollitos"]) / int(ciclo["aves_iniciales"]))],
                   ["Fletes directamente atribuibles (Bs)", "Incluidos en el costo de la recepción (FIN-EGR-04)"],
                   ["Costo total transferido a 6.1.01.01 — sufijo 01 (Bs)", _bs(ciclo["costo_pollitos"])],
                   ["Asiento del traslado", f"N.º {ciclo['asiento_id']}"],
                   ["Fecha estimada de cierre (día 73)", fecha_pantalla(ciclo["fecha_cierre"])]], [7, 10.4])]
    cuerpo += _firmas(["Elaborado por (Unidad de Finanzas)", "Recepción conforme (Unidad Técnica)"],
                      [ciclo["registrado_por"], ciclo.get("recepcion_conforme_por") or ""])
    cuerpo += _riesgos("Formato de apertura del «activo transitorio acumulador» del ciclo.",
                       [_R8 + ["Detección", "Verificación y Evaluación"], _R22 + ["Preventivo", "Clasificación"],
                        _R39 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TRA-01", cuerpo, PIE)


# ==========================================================================
# FIN-TRA-02 — Bitácora Diaria de Ciclo
# ==========================================================================
def fin_tra_02(ciclo: dict[str, Any], bitacora: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    filas = [[str(b["dia_ciclo"]), b["lote"], NOMBRE_FASE[b["fase"]], _kg(b["alimento_kg"]), str(b["aves_muertas"]),
              b.get("causa_presunta") or "", b.get("novedad_sanitaria") or ("Cumple CI-FIN-11" if b["cumple_ci_fin_11"]
                                                                            else ""), pct_texto(b["mortalidad_acum"])]
             for b in sorted(bitacora, key=lambda x: (x["fecha"], x["id"]))]
    m = ciclo["mortalidad"]
    cuerpo = _encabezado("FIN-TRA-02", "Bitácora Diaria de Ciclo", "Unidad Técnica", TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Registro diario, en el galpón, del consumo de alimento por fase, la mortalidad con causa presunta y "
                  "las novedades sanitarias del lote, conforme al Instructivo CI-FIN-11.", E_NORMAL), Spacer(1, 4),
               _tabla(["Día", "Lote", "Fase", "Alimento (kg)", "Muertas", "Causa presunta", "Novedad sanitaria",
                       "% acum."], filas or [["—"] * 8], [1, 2.3, 2, 2.2, 1.4, 3, 3.6, 1.9], (3, 4, 7)),
               Spacer(1, 4),
               _p(f"% de mortalidad acumulada al día de hoy: {pct_texto(m['pct'])} ({_n(m['muertas'])} aves muertas "
                  f"acumuladas ÷ {_n(m['aves_iniciales'])} aves vivas al inicio × 100).", E_NORMAL)]
    cuerpo += _firmas(["Registrado por (Unidad Técnica)", "Revisado por (Gerencia General, semanal)"])
    cuerpo += _riesgos("Registro primario que alimenta todo el costeo del ciclo y el criterio de absorción CI-FIN-05.",
                       [_R8 + ["Detección", "Verificación y Evaluación"], _R22 + ["Preventivo", "Clasificación"]])
    return _construir("FIN-TRA-02", cuerpo, PIE)


# ==========================================================================
# FIN-TRA-04 — Registro de Baja por Mortalidad
# ==========================================================================
def fin_tra_04(ciclo: dict[str, Any], bajas: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    filas = [[fecha_pantalla(b["fecha"]), b["lote"], str(b["aves"]), pct_texto(b["pct_acumulado"]),
              "Normal [X]  Anormal [ ]" if b["clasificacion"] == "NORMAL" else "Normal [ ]  Anormal [X]",
              _bs(b["costo_por_ave"]), _bs(b["monto"]), _si_no(bool(b.get("informe_veterinario")))]
             for b in bajas]
    anormales = [b for b in bajas if b["clasificacion"] == "ANORMAL"]
    autorizacion = "; ".join(f"{b['autorizado_por'] or 'pendiente'} ({(b['autorizado_en'] or '')[:10]}) — informe "
                             f"{b['informe_veterinario']}" for b in anormales) or "No aplica (sin bajas anormales)"
    cuerpo = _encabezado("FIN-TRA-04", "Registro de Baja por Mortalidad", "Unidad de Finanzas y Comercialización",
                         "Ciclo 3 — Transformación / FICHA-FIN-05") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Da de baja las aves muertas al momento del deceso, clasificando la pérdida como normal (sufijo 40) o "
                  "anormal (cuenta 7.2.01.04), conforme al Manual CI-FIN-05. Costo por ave = costo acumulado del centro "
                  "÷ aves vivas al inicio.", E_NORMAL), Spacer(1, 4),
               _tabla(["Fecha del deceso", "Lote", "N° aves", "% acum.", "Clasificación", "Costo/ave (Bs)",
                       "Monto (Bs)", "Informe veterinario"], filas or [["—"] * 8],
                      [2.2, 2.2, 1.4, 1.6, 3.2, 2.1, 2.2, 2.5], (2, 3, 5, 6)),
               Spacer(1, 4), _p(f"Si es Anormal, autorización de Gerencia General: {autorizacion}.", E_NORMAL)]
    cuerpo += _firmas(["Registrado por (Unidad de Finanzas)", "Autorizado por (Gerencia General, si es anormal)"],
                      [bajas[0]["registrado_por"] if bajas else "", anormales[0]["autorizado_por"] or ""
                       if anormales else ""])
    cuerpo += _riesgos("Formato central de clasificación normal/anormal (regla de oro: % acumulado del ciclo).",
                       [_R9 + ["Preventivo", "Autorización"], _R23 + ["Preventivo", "Clasificación"],
                        _R8 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-TRA-04", cuerpo, PIE)


# ==========================================================================
# FIN-TRA-05 — Registro de Faenado
# ==========================================================================
def fin_tra_05(ciclo: dict[str, Any], faenados: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    filas = [[f["lote"], fecha_pantalla(f["fecha"]), str(f["dia_ciclo"]), _n(f["aves_faenadas"]), _kg(f["peso_vivo"]),
              _kg(f["peso_canal"]), _kg(f["merma"]), f"{pct_texto(f['pmf'])} {f['semaforo_pmf'].capitalize()}",
              f"{str(f['ica']).replace('.', ',')} {f['semaforo_ica'].capitalize()}", _si_no(bool(f["cumple_ci_fin_12"]))]
             for f in faenados]
    fallos = []
    for f in faenados:
        for clave, ok in json.loads(f["checklist_json"]).items():
            if not ok:
                fallos.append(f"{f['lote']}: {PUNTOS_CI_FIN_12[clave]}")
    c = ciclo["conciliacion"]
    cuerpo = _encabezado("FIN-TRA-05", "Registro de Faenado", "Unidad Técnica / Personal Eventual",
                         "Ciclo 2 — Egresos (Planillas) / Ciclo 3 — Transformación") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Registro técnico de peso vivo, peso canal y merma del lote faenado, bajo las normas sanitarias y "
                  "puntos de control de inocuidad y cadena de frío (Instructivo CI-FIN-12). PMF = (peso vivo − peso "
                  "canal) ÷ peso vivo; ICA = kg de alimento ÷ kg de carne viva.", E_NORMAL), Spacer(1, 4),
               _tabla(["N° de Lote", "Fecha", "Día", "Aves", "Peso vivo", "Peso canal", "Merma", "PMF", "ICA",
                       "Cumple CI-FIN-12"], filas or [["—"] * 10],
                      [2.1, 1.8, 0.9, 1.3, 1.9, 1.9, 1.6, 2.1, 1.6, 2.2], (2, 3, 4, 5, 6)),
               Spacer(1, 4),
               _p("Puntos de control incumplidos: " + ("; ".join(fallos) if fallos else "ninguno") + ".", E_NORMAL),
               _p(f"Conciliación de aves del ciclo: ingresadas {_n(c['ingresadas'])} = muertas {_n(c['muertas'])} + "
                  f"faenadas {_n(c['faenadas'])} + en crianza {_n(c['en_crianza'])} + faltante {_n(c['faltante'])}.",
                  E_NORMAL)]
    cuerpo += _firmas(["Registrado por (Unidad Técnica)", "Verificado por (Personal Eventual — Supervisor)"],
                      [faenados[0]["registrado_por"] if faenados else "",
                       faenados[0]["supervisor_eventual"] if faenados else ""])
    cuerpo += _riesgos("Un PMF por encima del umbral «Malo» (> 20 %) se reporta de inmediato a la Gerencia General.",
                       [_R6 + ["Preventivo", "Protección Física"]])
    return _construir("FIN-TRA-05", cuerpo, PIE)


# ==========================================================================
# FIN-TRA-11 — Bitácora de Disposición Sanitaria
# ==========================================================================
def fin_tra_11(ciclo: dict[str, Any], disposiciones: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    filas = [[fecha_pantalla(d["fecha"]), d["lote"], str(d["aves"]),
              METODOS_DISPOSICION[d["metodo"]] + (f" ({d['detalle_metodo']})" if d.get("detalle_metodo") else ""),
              d["responsable"], d.get("supervisado_por") or ""] for d in disposiciones]
    cuerpo = _encabezado("FIN-TRA-11", "Bitácora de Disposición Sanitaria", "Unidad Técnica",
                         "Ciclo 3 — Transformación / FICHA-FIN-05") + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Registro del método de disposición sanitaria de las aves muertas durante el ciclo (CI-FIN-11), como "
                  "respaldo del Registro de Baja por Mortalidad (FIN-TRA-04).", E_NORMAL), Spacer(1, 4),
               _tabla(["Fecha", "Lote", "N° de aves dispuestas", "Método de disposición", "Responsable",
                       "Supervisado (GG)"], filas or [["—"] * 6], [2.2, 2.4, 2.4, 4.4, 3.2, 2.8], (2,))]
    cuerpo += _firmas(["Registrado por (Unidad Técnica)", "Supervisado por (Gerencia General)"])
    cuerpo += _riesgos("Control de protección física y bioseguridad sobre el manejo de aves muertas.",
                       [_R9 + ["Preventivo", "Autorización"], _R23 + ["Detección", "Verificación y Evaluación"]])
    return _construir("FIN-TRA-11", cuerpo, PIE)


# ==========================================================================
# CI-FIN-11 — Reporte de Alerta Sanitaria (Pasos 4 y 5)
# ==========================================================================
def alerta_ci_fin_11(ciclo: dict[str, Any], alertas: Sequence[dict[str, Any]], ctx: Contexto) -> bytes:
    filas = [[fecha_pantalla(a["fecha_deteccion"]), a["fecha_reporte"][:16].replace("T", " "),
              "Sí" if a["mismo_dia"] else f"No — {a.get('causa_demora') or ''}", TIPOS_ALERTA[a["tipo"]],
              a["descripcion"], (MEDIDAS[a["medida"]] + ": " + (a.get("decision") or "")) if a.get("medida")
              else "Pendiente de decisión"] for a in alertas]
    cuerpo = _encabezado("CI-FIN-11", "Reporte de Alerta Sanitaria", "Unidad Técnica → Gerencia General",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p("Paso 4: ante cualquier señal de alerta se reporta de inmediato —el mismo día— a la Gerencia General, "
                  "con independencia del porcentaje acumulado. Paso 5: la Gerencia decide la medida y la deja en la "
                  "Bitácora Diaria de Ciclo.", E_NORMAL), Spacer(1, 4),
               _tabla(["Detección", "Reporte", "¿Mismo día?", "Tipo", "Descripción", "Decisión de la Gerencia"],
                      filas or [["—"] * 6], [1.9, 2.4, 2.2, 3, 4.4, 3.5])]
    cuerpo += _firmas(["Reportado por (Unidad Técnica)", "Recibido y decidido por (Gerencia General)"])
    cuerpo += _riesgos("El reporte del mismo día convierte la detección temprana en obligación inmediata.",
                       [_R8 + ["Detección", "Verificación y Evaluación"], _R9 + ["Preventivo", "Autorización"]])
    return _construir("CI-FIN-11", cuerpo, PIE)


# ==========================================================================
# Etiqueta QR «Vida Digna» del lote (POL-03 Art. 3; CI-FIN-12 Paso 7)
# ==========================================================================
def _qr(url: str, lado: float = 5 * cm) -> Drawing:
    widget = QrCodeWidget(url)
    x0, y0, x1, y1 = widget.getBounds()
    dibujo = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0), 0, 0])
    dibujo.add(widget)
    return dibujo


def etiqueta_qr(datos: dict[str, Any], ctx: Contexto) -> bytes:
    """Ficha del código QR del lote con los datos publicados (solo registros)."""
    peso = (f"{str(datos['peso_muestreo']).replace('.', ',')} kg (semana {datos['semana_muestreo']})"
            if datos["peso_muestreo"] is not None else "Sin muestreo registrado")
    cuerpo = _encabezado("QR-VD", "Código QR «Vida Digna» del Lote", "Gerencia General (plataforma QR)",
                         TRANSFORMACION) + _contexto(ctx)
    cuerpo += [_p("Cuerpo de Datos", E_SUB),
               _p(f"{datos['leyenda']}. Lote {datos['lote']} · {datos['ciclo']}.", E_NORMAL), Spacer(1, 4),
               _qr(datos["url"]), _p(datos["url"], E_PEQ), Spacer(1, 6),
               _tabla(["Dato publicado (POL-03 Art. 3)", "Valor", "Respaldo"], [
                   ["Número de lote y fecha de faenado", f"{datos['lote']} · {fecha_pantalla(datos['fecha_faenado'])}",
                    "FIN-TRA-05"],
                   ["Cronograma de vida", f"Recepción {fecha_pantalla(datos['fecha_recepcion'])}; "
                                          f"{datos['dias_vida']} días", "FIN-TRA-01 / FIN-TRA-02"],
                   ["Insumos de alimentación", ", ".join(datos["insumos_alimentacion"]) or "Sin registros en el kárdex",
                    "Kárdex del ciclo"],
                   ["Alimento consumido", _kg(datos["alimento_kg"]), "FIN-TRA-02"],
                   ["Galpón", f"{datos['galpon']} · {str(datos['densidad']).replace('.', ',')} aves por m2", "FIN-TRA-01"],
                   ["Fotografías del galpón", datos["fotos_url"] or "Pendientes de publicación", "Plataforma QR (GG)"],
                   ["Mortalidad acumulada del ciclo", pct_texto(datos["mortalidad_acumulada"]), "FIN-TRA-02"],
                   ["Peso vivo por muestreo", peso, "Registro de peso (POL-03 Art. 2)"],
                   ["Peso canal promedio por ave", f"{str(datos['peso_canal_promedio']).replace('.', ',')} kg",
                    "FIN-TRA-05"],
                   ["Vacunas y tratamientos", "; ".join(datos["tratamientos"]) or "Sin registros", "FIN-TRA-02"],
                   ["Inocuidad CI-FIN-12", "Cumple" if datos["cumple_ci_fin_12"] else "Con observaciones",
                    "FIN-TRA-05"]], [5, 8.4, 4])]
    cuerpo += _firmas(["Publicado por (Gerencia General)", "Verificación semestral (Consejo de Vigilancia)"])
    return _construir("QR-VD", cuerpo, PIE)
