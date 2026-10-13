"""Restaura la base en uso desde un paquete de respaldo (Fase 13).

USE ESTE SCRIPT CON LA APLICACIÓN CERRADA. Antes de reemplazar la base:

1. verifica el paquete (huellas SHA-256, integridad, claves foráneas, conteos
   de control y cuadre de todos los asientos);
2. guarda la base actual como ``pollolandia.antes_de_restaurar_<marca>.db``.

Uso:

    python -m scripts.restaurar_respaldo RUTA_DEL_PAQUETE.zip [--ruta RUTA_BD] --confirmar

Para una prueba sin tocar la base en uso, use la pantalla
«Administración · Respaldos» (prueba de restauración con acta).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from servicios import respaldo
from servicios.excepciones import ErrorPollolandia


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restauración de la base del ERP Pollolandia")
    parser.add_argument("paquete", help="Paquete .zip del respaldo")
    parser.add_argument("--ruta", help="Base de datos a reemplazar (por defecto la configurada)")
    parser.add_argument("--confirmar", action="store_true", help="Confirma que la aplicación está cerrada")
    args = parser.parse_args(argv)
    if not args.confirmar:
        print("Agregue --confirmar para indicar que la aplicación está cerrada.", file=sys.stderr)
        return 2
    ruta = Path(args.ruta) if args.ruta else config.obtener_ruta_bd()
    try:
        r = respaldo.restaurar_produccion(Path(args.paquete), ruta)
    except ErrorPollolandia as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for v in r["verificaciones"]:
        print(f"[{'OK' if v['cumple'] else 'FALLA'}] {v['control']} {v['detalle']}")
    print(f"Base restaurada: {r['restaurada']}")
    if r["copia_previa"]:
        print(f"Copia de la base anterior: {r['copia_previa']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
