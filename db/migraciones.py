"""Migraciones versionadas y creación de la base de datos desde cero (Fases 1-13).

Uso (un solo comando, desde la raíz del proyecto):

    python -m db.migraciones                 # crea/actualiza la BD y carga semillas
    python -m db.migraciones --reiniciar     # borra la BD y la crea de nuevo
    python -m db.migraciones --estado        # muestra versión y conteos
    python -m db.migraciones --reporte       # escribe el reporte de conciliación

Cada migración se aplica dentro de UNA transacción: si falla, se hace rollback
y la versión no se registra.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import config
from db import semillas
from db.conexion import MEMORIA, conexion, obtener_conexion
from servicios.excepciones import ErrorPollolandia, ReglaNegocioError

log = logging.getLogger(f"{config.LOGGER_RAIZ}.migraciones")

RUTA_ESQUEMA: Path = Path(__file__).resolve().parent / "esquema.sql"
RUTA_ESQUEMA_V2: Path = Path(__file__).resolve().parent / "esquema_v2.sql"
RUTA_ESQUEMA_V3: Path = Path(__file__).resolve().parent / "esquema_v3.sql"
RUTA_ESQUEMA_V4: Path = Path(__file__).resolve().parent / "esquema_v4.sql"
RUTA_ESQUEMA_V5: Path = Path(__file__).resolve().parent / "esquema_v5.sql"
RUTA_ESQUEMA_V6: Path = Path(__file__).resolve().parent / "esquema_v6.sql"
RUTA_ESQUEMA_V7: Path = Path(__file__).resolve().parent / "esquema_v7.sql"
RUTA_ESQUEMA_V8: Path = Path(__file__).resolve().parent / "esquema_v8.sql"
RUTA_ESQUEMA_V9: Path = Path(__file__).resolve().parent / "esquema_v9.sql"
RUTA_ESQUEMA_V10: Path = Path(__file__).resolve().parent / "esquema_v10.sql"
RUTA_ESQUEMA_V11: Path = Path(__file__).resolve().parent / "esquema_v11.sql"
RUTA_ESQUEMA_V12: Path = Path(__file__).resolve().parent / "esquema_v12.sql"
RUTA_ESQUEMA_V13: Path = Path(__file__).resolve().parent / "esquema_v13.sql"

_SQL_VERSION = """
CREATE TABLE IF NOT EXISTS esquema_version (
    version     INTEGER PRIMARY KEY,
    descripcion TEXT NOT NULL,
    aplicada_en TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
)
"""


@dataclass(frozen=True)
class Migracion:
    """Una migración: versión entera creciente y script SQL completo."""

    version: int
    descripcion: str
    sql: str


def obtener_migraciones() -> list[Migracion]:
    """Lista ordenada de migraciones conocidas por el sistema."""
    return [
        Migracion(
            version=1,
            descripcion="Esquema inicial: catálogo, costos, parámetros, períodos, socios, "
            "seguridad, diario, auditoría y adjuntos",
            sql=RUTA_ESQUEMA.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=2,
            descripcion="Fase 2: período cerrado en BD, autorizaciones CI-FIN-09 y firmas con "
            "suplencia CI-FIN-17",
            sql=RUTA_ESQUEMA_V2.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=3,
            descripcion="Fase 3: tesorería (caja, bancos, compromisos, flujo proyectado, alertas, "
            "reporte diario y conciliación bancaria)",
            sql=RUTA_ESQUEMA_V3.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=4,
            descripcion="Fase 4: compras e inventarios (proveedores, kárdex CPP, punto de reorden, acta de "
            "stock, órdenes de compra, recepción, cuentas por pagar, programación de pagos y bajas)",
            sql=RUTA_ESQUEMA_V4.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=5,
            descripcion="Fase 5: personal y planillas (planilla mensual, beneficios sociales, hojas de tiempo, "
            "personal eventual, liquidación final)",
            sql=RUTA_ESQUEMA_V5.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=6,
            descripcion="Fase 6: operativo y trazabilidad (galpones, ciclos y lotes, bitácora diaria, alertas "
            "sanitarias, bajas por mortalidad, disposición sanitaria, muestreo de peso, faenado y QR)",
            sql=RUTA_ESQUEMA_V6.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=7,
            descripcion="Fase 7: costos matriciales (consumos al CPP, CIF, corte de MOD, alimento propio, productos "
            "terminados, cierre del ciclo, informe de costo y presupuesto D.4)",
            sql=RUTA_ESQUEMA_V7.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=8,
            descripcion="Fase 8: activos fijos y CAPEX (plan anual con ROA y SME, inversiones no previstas y "
            "variaciones, activación, ficha individual, depreciación mensual, bajas, informe semestral y "
            "verificación física)",
            sql=RUTA_ESQUEMA_V8.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=9,
            descripcion="Fase 9: ventas, HORECA y cobranza (clientes, tarifario, pre-venta y cobertura del lote, "
            "notas de entrega, crédito, cuentas por cobrar, suspensión, devoluciones, recaudación, margen por "
            "canal y provisión de incobrables)",
            sql=RUTA_ESQUEMA_V9.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=10,
            descripcion="Fase 10: contabilidad general (lista de verificación y paquete de cierre mensual, cierre "
            "anual, estados financieros emitidos, plazos de 60 y 90 días, distribución de excedentes, INF-CV-04 y "
            "liquidaciones por socio)",
            sql=RUTA_ESQUEMA_V10.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=11,
            descripcion="Fase 11: RAU, cumplimiento legal y archivo (calendario fiscal, cuota y liquidación por socio, "
            "Formulario 701, patentes, superficie y CAPEX, balance de masas y programa de excedente, compradores, "
            "servicios a terceros, INF-CV-09, expediente EXP-RAU, fiscalización y archivo contable MAN-ARC-01)",
            sql=RUTA_ESQUEMA_V11.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=12,
            descripcion="Fase 12: presupuesto maestro 2026-2030 y por ciclo, calendario presupuestario, FIN-TES-11 y "
            "FIN-TES-12, POA, informes del Consejo de Vigilancia (INF-CV-02/05/06/07/08), FIN-INF-05 condicionado al "
            "INF-CV-07, inventario sorpresivo y verificación de desembolsos",
            sql=RUTA_ESQUEMA_V12.read_text(encoding="utf-8"),
        ),
        Migracion(
            version=13,
            descripcion="Fase 13: respaldo 3-2-1 con actas de restauración, solicitudes de cambio de parámetros "
            "con historial y datos del POA 2026-2027 (calendario de ciclos, flujo mensual y presupuesto por área)",
            sql=RUTA_ESQUEMA_V13.read_text(encoding="utf-8"),
        ),
    ]


def version_actual(con: sqlite3.Connection) -> int:
    """Versión de esquema aplicada (0 si la BD está vacía)."""
    con.execute(_SQL_VERSION)
    fila = con.execute("SELECT COALESCE(MAX(version), 0) FROM esquema_version").fetchone()
    return int(fila[0])


def aplicar_migraciones(
    con: sqlite3.Connection, migraciones: Sequence[Migracion] | None = None
) -> list[int]:
    """Aplica las migraciones pendientes, cada una en su propia transacción.

    Args:
        con: conexión abierta (``foreign_keys=ON``).
        migraciones: lista alternativa (para pruebas); por defecto las del sistema.

    Returns:
        Versiones aplicadas en esta llamada.

    Raises:
        ReglaNegocioError: si la BD tiene una versión mayor a la conocida, o si
        una migración falla (se hace rollback y se conserva la versión previa).
    """
    lista = sorted(migraciones if migraciones is not None else obtener_migraciones(),
                   key=lambda m: m.version)
    actual = version_actual(con)
    con.commit()
    conocida = max((m.version for m in lista), default=0)
    if actual > conocida:
        raise ReglaNegocioError(
            f"La base de datos está en la versión {actual}, más nueva que el sistema ({conocida}).",
            codigo="BD_VERSION_FUTURA",
        )

    aplicadas: list[int] = []
    for mig in lista:
        if mig.version <= actual:
            continue
        try:
            con.executescript("BEGIN;\n" + mig.sql)
            con.execute(
                "INSERT INTO esquema_version (version, descripcion) VALUES (?, ?)",
                (mig.version, mig.descripcion),
            )
            con.commit()
        except sqlite3.Error as exc:
            if con.in_transaction:
                con.rollback()
            log.exception("Falló la migración %s", mig.version)
            raise ReglaNegocioError(
                f"Falló la migración {mig.version}: {exc}",
                codigo="MIGRACION_FALLIDA",
                detalle={"version": mig.version},
            ) from exc
        aplicadas.append(mig.version)
        log.info("Migración %s aplicada: %s", mig.version, mig.descripcion)
    return aplicadas


def _eliminar_archivos_bd(ruta: Path) -> None:
    """Borra el archivo SQLite y sus auxiliares WAL/SHM."""
    for sufijo in ("", "-wal", "-shm"):
        archivo = Path(str(ruta) + sufijo)
        try:
            if archivo.exists():
                archivo.unlink()
        except OSError as exc:
            raise ReglaNegocioError(
                f"No se pudo borrar {archivo}. Cierre la aplicación que la usa. ({exc})",
                codigo="BD_NO_SE_PUEDE_BORRAR",
            ) from exc


def inicializar(
    ruta: str | Path | None = None, reiniciar: bool = False, con_semillas: bool = True
) -> dict[str, Any]:
    """Crea (o actualiza) la base de datos: migraciones + semillas.

    Es idempotente: ejecutarla otra vez no duplica datos.

    Args:
        ruta: archivo de BD (por defecto ``config.obtener_ruta_bd()``).
        reiniciar: si es ``True`` borra la BD existente antes de crearla.
        con_semillas: si es ``False`` solo crea el esquema.

    Returns:
        Resumen con ruta, migraciones y semillas aplicadas.
    """
    destino = str(ruta) if ruta is not None else str(config.obtener_ruta_bd())
    if reiniciar and destino != MEMORIA:
        _eliminar_archivos_bd(Path(destino))
    if destino != MEMORIA:
        Path(destino).parent.mkdir(parents=True, exist_ok=True)

    con = obtener_conexion(destino)
    try:
        aplicadas = aplicar_migraciones(con)
        semillas_aplicadas: dict[str, int] = {}
        if con_semillas:
            semillas_aplicadas = semillas.cargar_semillas(con)
        return {
            "ruta": destino,
            "version": version_actual(con),
            "migraciones_aplicadas": aplicadas,
            "semillas_aplicadas": semillas_aplicadas,
        }
    finally:
        con.close()


def estado(ruta: str | Path | None = None) -> dict[str, int]:
    """Conteos principales de la BD (para diagnóstico)."""
    consultas = {
        "version": "SELECT COALESCE(MAX(version),0) FROM esquema_version",
        "cuentas_nivel_4": "SELECT COUNT(*) FROM cuentas WHERE nivel = 4",
        "sufijos": "SELECT COUNT(*) FROM sufijos_costo",
        "parametros": "SELECT COUNT(*) FROM parametros",
        "socios": "SELECT COUNT(*) FROM socios",
        "periodos": "SELECT COUNT(*) FROM periodos",
        "asientos": "SELECT COUNT(*) FROM asientos",
        "roles": "SELECT COUNT(*) FROM roles",
        "permisos": "SELECT COUNT(*) FROM permisos",
        "auditoria": "SELECT COUNT(*) FROM auditoria",
        "usuarios": "SELECT COUNT(*) FROM usuarios",
        "autorizaciones": "SELECT COUNT(*) FROM autorizaciones",
        "movimientos_bancarios": "SELECT COUNT(*) FROM movimientos_bancarios",
        "conciliaciones": "SELECT COUNT(*) FROM conciliaciones",
        "insumos": "SELECT COUNT(*) FROM insumos",
        "ordenes_compra": "SELECT COUNT(*) FROM ordenes_compra",
        "kardex": "SELECT COUNT(*) FROM kardex",
        "personal": "SELECT COUNT(*) FROM personal",
        "planillas": "SELECT COUNT(*) FROM planillas",
        "ciclos_produccion": "SELECT COUNT(*) FROM ciclos_produccion",
        "costos_ciclo": "SELECT COUNT(*) FROM costos_ciclo",
        "activos": "SELECT COUNT(*) FROM activos",
        "planes_capex": "SELECT COUNT(*) FROM planes_capex",
        "clientes": "SELECT COUNT(*) FROM clientes",
        "notas_entrega": "SELECT COUNT(*) FROM notas_entrega",
        "cierres_mensuales": "SELECT COUNT(*) FROM cierres_mensuales",
        "distribuciones": "SELECT COUNT(*) FROM distribuciones_excedentes",
        "formularios_701": "SELECT COUNT(*) FROM formularios_701",
        "expedientes_archivo": "SELECT COUNT(*) FROM expedientes_archivo",
        "presupuesto_lineas": "SELECT COUNT(*) FROM presupuesto_lineas",
        "informes_cv": "SELECT COUNT(*) FROM informes_cv",
    }
    with conexion(ruta) as con:
        return {k: int(con.execute(q).fetchone()[0]) for k, q in consultas.items()}


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de línea de comandos."""
    parser = argparse.ArgumentParser(
        prog="python -m db.migraciones",
        description="Crea o actualiza la base de datos del ERP Pollolandia.",
    )
    parser.add_argument("--ruta", help="Archivo SQLite (por defecto datos/pollolandia.db)")
    parser.add_argument("--reiniciar", action="store_true", help="Borra la BD y la crea de nuevo")
    parser.add_argument("--sin-semillas", action="store_true", help="Solo crea el esquema")
    parser.add_argument("--estado", action="store_true", help="Muestra versión y conteos")
    parser.add_argument("--reporte", nargs="?", const="", metavar="ARCHIVO",
                        help="Escribe el reporte de conciliación del catálogo (Markdown)")
    args = parser.parse_args(argv)

    config.configurar_logging()
    try:
        if args.estado:
            for clave, valor in estado(args.ruta).items():
                print(f"{clave:>16}: {valor}")
            return 0
        if args.reporte is not None:
            with conexion(args.ruta) as con:
                destino = semillas.escribir_reporte_conciliacion(con, args.reporte or None)
            print(f"Reporte escrito en: {destino}")
            return 0
        resumen = inicializar(args.ruta, reiniciar=args.reiniciar, con_semillas=not args.sin_semillas)
    except ErrorPollolandia as exc:
        print(f"ERROR [{exc.codigo}]: {exc}", file=sys.stderr)
        return 1

    print(f"Base de datos: {resumen['ruta']}")
    print(f"Versión de esquema: {resumen['version']}")
    print(f"Migraciones aplicadas: {resumen['migraciones_aplicadas'] or 'ninguna (ya estaba al día)'}")
    if resumen["semillas_aplicadas"]:
        for bloque, n in resumen["semillas_aplicadas"].items():
            print(f"  semilla {bloque:<22} {n:>5} registros")
    else:
        print("Semillas: ninguna nueva.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
