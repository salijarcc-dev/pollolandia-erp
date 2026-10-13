"""Respaldo automático 3-2-1 (Fase 13; MAN-ARC-01 §5.2).

Uso (lo ejecuta la tarea programada de Windows ``respaldo_automatico.bat``
y también la aplicación al iniciar):

    python -m scripts.respaldo_automatico            # respaldos pendientes de hoy
    python -m scripts.respaldo_automatico --manual   # copia completa inmediata

Hace la copia diaria (base completa + incremental del archivo digital), la
completa semanal y las de cierre mensual y anual que falten, las copia a los
tres destinos configurados y depura las vencidas.
"""
from __future__ import annotations

import argparse
import sys

import config
from db.conexion import conexion
from db.migraciones import inicializar
from servicios import respaldo
from servicios.excepciones import ErrorPollolandia


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Respaldo automático 3-2-1 del ERP Pollolandia")
    parser.add_argument("--ruta", help="Ruta de la base de datos (por defecto la configurada)")
    parser.add_argument("--manual", action="store_true", help="Copia completa inmediata (tipo MANUAL)")
    args = parser.parse_args(argv)
    config.configurar_logging()
    ruta = args.ruta or str(config.obtener_ruta_bd())
    try:
        inicializar(ruta)
        with conexion(ruta) as con:
            if args.manual:
                r = respaldo.crear_respaldo(con, None, "MANUAL")
                print(f"Respaldo manual {r['archivo']}: {r['copias']} de 3 copias.")
                return 0
            resultado = respaldo.ejecutar_automatico(con)
    except ErrorPollolandia as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for r in resultado["respaldos"]:
        print(f"{r['tipo']:<8} {r['referencia']:<12} {r['copias']} de 3 copias  {r['archivo']}")
        for ubicacion, error in r["errores"].items():
            print(f"         sin copia en {ubicacion}: {error}")
    for e in resultado["errores"]:
        print(f"ERROR {e['tipo']} {e['referencia'] or ''}: {e['error']}", file=sys.stderr)
    if resultado["depurados"]:
        print(f"Depurados por plazo vencido: {len(resultado['depurados'])}")
    if not resultado["respaldos"] and not resultado["errores"]:
        print("No hay respaldos pendientes para hoy.")
    return 1 if resultado["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
