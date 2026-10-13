"""Configuración central del ERP Pollolandia (Fases 0-13).

Contiene rutas, constantes de formato y la configuración de logging.

IMPORTANTE (regla 2 del proyecto): las cifras NORMATIVAS (tasas, topes, plazos,
SME, etc.) NO viven aquí; se leerán de la tabla ``parametros`` desde la Fase 1.
Las pocas constantes de este archivo son (a) técnicas/de formato o (b) VALORES
SEMILLA marcados con ``# SEMILLA:`` que la Fase 1 migra a ``parametros`` y que
las funciones reciben como argumento para no depender de ellos.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# --------------------------------------------------------------------------
# Identidad
# --------------------------------------------------------------------------
NOMBRE_SISTEMA: str = "ERP Pollolandia"
NOMBRE_ENTIDAD: str = "Cooperativa Agropecuaria Pollolandia R.L."
UBICACION: str = "Caranavi, Los Yungas · La Paz · Bolivia"
VERSION: str = "1.0.1"
FASE_ACTUAL: str = "Fase 13 - Integración y puesta en marcha"

# --------------------------------------------------------------------------
# Rutas (pathlib)
# --------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent
DATOS_DIR: Path = BASE_DIR / "datos"
ARCHIVO_DIR: Path = BASE_DIR / "archivo"
NOMBRE_BD: str = "pollolandia.db"

# Variables de entorno opcionales (útiles para pruebas y respaldos)
ENV_DIR_DATOS: str = "POLLOLANDIA_DATOS"
ENV_RUTA_BD: str = "POLLOLANDIA_BD"
ENV_DIR_ARCHIVO: str = "POLLOLANDIA_ARCHIVO"
# Destinos de la regla 3-2-1 (MAN-ARC-01 §5.2). La copia LOCAL vive en
# ``<datos>/respaldos``; las otras dos se configuran por máquina (disco externo
# y carpeta fuera del sitio o sincronizada con la nube) en la pantalla de
# respaldos o con estas variables de entorno, que tienen prioridad.
ENV_RESPALDO_EXTERNO: str = "POLLOLANDIA_RESPALDO_EXTERNO"
ENV_RESPALDO_FUERA_SITIO: str = "POLLOLANDIA_RESPALDO_FUERA_SITIO"
NOMBRE_CONFIG_RESPALDO: str = "respaldo_destinos.json"

# --------------------------------------------------------------------------
# Formato de montos y fechas (regla 6)
# --------------------------------------------------------------------------
SIMBOLO_MONEDA: str = "Bs"
DECIMALES_BS: int = 2
SEPARADOR_MILES: str = "."
SEPARADOR_DECIMAL: str = ","
FORMATO_FECHA_PANTALLA: str = "%d/%m/%Y"

# --------------------------------------------------------------------------
# Calendario contable
# --------------------------------------------------------------------------
# Ejercicio 1-jul a 30-jun (PC-01, Ley 356).
# SEMILLA: se migra a parametros('mes_inicio_ejercicio') en la Fase 1.
MES_INICIO_EJERCICIO: int = 7

# SEMILLA: se migra a parametros('dias_ciclo') en la Fase 1 (POL-03, PC-04).
DIAS_CICLO_ESTANDAR: int = 73

# --------------------------------------------------------------------------
# Seguridad técnica (no normativa)
# --------------------------------------------------------------------------
# Costo del hash bcrypt. Es un parámetro técnico de rendimiento, no una regla
# de la cooperativa; las reglas de contraseña (longitud, caducidad, intentos)
# se leen de la tabla ``parametros``.
BCRYPT_ROUNDS: int = 12

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOGGER_RAIZ: str = "pollolandia"
LOG_MAX_BYTES: int = 1_000_000
LOG_COPIAS: int = 5
LOG_FORMATO: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def obtener_directorio_datos() -> Path:
    """Devuelve el directorio de datos (``datos/`` o el indicado por
    la variable de entorno ``POLLOLANDIA_DATOS``)."""
    valor = os.environ.get(ENV_DIR_DATOS)
    return Path(valor).expanduser().resolve() if valor else DATOS_DIR


def obtener_directorio_archivo() -> Path:
    """Archivo digital (PDF/A escaneados, MAN-ARC-01 §5.2): ``archivo/`` o el
    indicado por la variable ``POLLOLANDIA_ARCHIVO``."""
    valor = os.environ.get(ENV_DIR_ARCHIVO)
    return Path(valor).expanduser().resolve() if valor else ARCHIVO_DIR


def obtener_directorio_respaldos() -> Path:
    """Copia LOCAL de la regla 3-2-1 (equipo de trabajo): ``<datos>/respaldos``."""
    return obtener_directorio_datos() / "respaldos"


def asegurar_directorios() -> None:
    """Crea los directorios de trabajo si no existen."""
    directorio = obtener_directorio_datos()
    for ruta in (directorio, directorio / "logs", obtener_directorio_archivo()):
        ruta.mkdir(parents=True, exist_ok=True)


def obtener_ruta_bd() -> Path:
    """Devuelve la ruta del archivo SQLite.

    Prioridad: variable ``POLLOLANDIA_BD`` > ``<directorio de datos>/pollolandia.db``.
    Crea el directorio contenedor si no existe (no crea el archivo).
    """
    valor = os.environ.get(ENV_RUTA_BD)
    ruta = Path(valor).expanduser().resolve() if valor else obtener_directorio_datos() / NOMBRE_BD
    ruta.parent.mkdir(parents=True, exist_ok=True)
    return ruta


def configurar_logging(nivel: int = logging.INFO, ruta_log: Path | None = None) -> logging.Logger:
    """Configura el logging rotativo a archivo (y consola). Es idempotente:
    llamarla varias veces (p. ej. en cada recarga de Streamlit) no duplica
    manejadores.

    Args:
        nivel: nivel mínimo de registro.
        ruta_log: archivo de log alternativo (por defecto ``datos/logs/pollolandia.log``).

    Returns:
        El logger raíz del sistema (``pollolandia``).
    """
    logger = logging.getLogger(LOGGER_RAIZ)
    if getattr(logger, "_pollolandia_configurado", False):
        return logger

    asegurar_directorios()
    destino = Path(ruta_log) if ruta_log else obtener_directorio_datos() / "logs" / "pollolandia.log"
    destino.parent.mkdir(parents=True, exist_ok=True)

    formato = logging.Formatter(LOG_FORMATO)
    archivo = RotatingFileHandler(
        destino, maxBytes=LOG_MAX_BYTES, backupCount=LOG_COPIAS, encoding="utf-8"
    )
    archivo.setFormatter(formato)
    consola = logging.StreamHandler()
    consola.setFormatter(formato)

    logger.setLevel(nivel)
    logger.addHandler(archivo)
    logger.addHandler(consola)
    logger.propagate = False
    logger._pollolandia_configurado = True  # type: ignore[attr-defined]
    logger.info("Logging iniciado (%s v%s)", NOMBRE_SISTEMA, VERSION)
    return logger
