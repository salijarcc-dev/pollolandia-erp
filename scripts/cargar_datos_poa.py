"""Carga los datos reales del POA 2026-2027 en la base del ERP (Fase 13).

Uso, desde la raíz del proyecto:

    python -m scripts.cargar_datos_poa              # valida y carga
    python -m scripts.cargar_datos_poa --validar    # solo valida, sin escribir

Carga el calendario de ciclos (C1 a C5; el tramo C2-C5 está dentro del POA),
el flujo de caja mensual de oct-2026 a jun-2027 y el presupuesto por objeto de
gasto y área (ANEXO 12. POA 2026-2027). Es idempotente: una versión ya cargada
no se duplica. Toda carga queda en la bitácora de auditoría (usuario SISTEMA).
"""
from __future__ import annotations

import argparse
import sys

import config
from db.conexion import conexion
from db.migraciones import inicializar
from servicios import poa
from servicios.excepciones import ErrorPollolandia


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga de los datos del POA 2026-2027")
    parser.add_argument("--ruta", help="Ruta de la base de datos (por defecto la configurada)")
    parser.add_argument("--validar", action="store_true", help="Solo valida la transcripción del POA")
    args = parser.parse_args(argv)
    ruta = args.ruta or str(config.obtener_ruta_bd())
    try:
        poa.validar_sin_carga()
        inicializar(ruta)
        with conexion(ruta) as con:
            if args.validar:
                v = poa.validar_datos(con)
                print("Errores:", *(v["errores"] or ["ninguno"]), sep="\n  ")
                print("Observaciones (redondeo del POA):", *(v["observaciones"] or ["ninguna"]), sep="\n  ")
                return 1 if v["errores"] else 0
            r = poa.cargar_datos_poa(con)
    except ErrorPollolandia as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    estado = "ya estaba cargado" if r["ya_cargado"] else "cargado"
    print(f"{r['version']}: {estado} (carga n.º {r['carga_id']}).")
    print(f"  Ciclos del calendario: {r['ciclos']}")
    print(f"  Registros del flujo mensual: {r['registros_flujo']}")
    print(f"  Filas del presupuesto por área: {r['filas_area']}")
    for o in r["observaciones"]:
        print(f"  Observación: {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
