# Riesgos conocidos y deuda técnica — ERP Pollolandia v1.0.0

## A. Riesgos conocidos

| N.º | Riesgo | Impacto | Mitigación actual | Acción recomendada |
|---|---|---|---|---|
| R1 | **33 parámetros marcados «por validar»** (propuestas que los documentos no fijan: margen de modificación de la GG 10 %, frecuencia del INF-CV-06, plazos de conservación de respaldos, día del semanal, cuota RAU por ha, etc.). | Reglas aplicadas con un valor no ratificado. | Visibles con el filtro *Solo «por validar»* en **Parámetros normativos**. | Ratificarlos o cambiarlos con acta del CA (flujo GG → CA) antes del primer cierre mensual real. |
| R2 | **MOD fija del primer ciclo:** la operación real devenga planillas mensuales completas (base de 30 días, PC-09) desde el 1-jul, mientras el Manual de Costos valora 73/365 del sueldo anual. En la simulación el costo del ciclo es Bs 116.753,07 frente a Bs 116.286,28 del POA (+Bs 556,79 de MOD fija; −Bs 90 de eventual por 60 aves muertas). | Costo unitario Bs 14,97 frente a Bs 14,91. | Anotado como `# DISCREPANCIA` en la prueba de integración. | Que la GG defina si los días previos a la recepción de pollitos (1 al 5-jul) van a gasto o al ciclo. |
| R3 | **Respaldos sin cifrado propio:** los paquetes .zip no se cifran (la biblioteca estándar no cifra). | Una copia robada expone los datos. | MAN-ARC-01 §5.2 exige discos cifrados: se indica usar BitLocker en el disco externo y una carpeta en la nube con autenticación. | Activar BitLocker en el equipo y el disco externo. |
| R4 | **Conectividad limitada en Caranavi:** la copia fuera del sitio puede fallar. | 3-2-1 incompleto. | La copia local siempre se hace; el panel queda en amarillo/rojo y el respaldo indica la copia faltante. | Llevar el disco externo fuera de la granja una vez por semana o usar una carpeta sincronizada. |
| R5 | **Uso en red local:** Streamlit sirve HTTP sin cifrado y SQLite está pensado para un equipo. | Varios usuarios simultáneos en la LAN pueden esperar bloqueos breves; tráfico sin cifrar. | WAL y transacciones `BEGIN IMMEDIATE`. | Usar el ERP en el equipo de la oficina; si se abre en red, poner un proxy con HTTPS. |
| R6 | **Restauración de la base en uso** solo por línea de comandos con la aplicación cerrada. | Error operativo si se ejecuta con el sistema abierto. | Exige `--confirmar`, verifica el paquete y guarda la base anterior. | Documentado en el manual del JF. |
| R7 | **Respaldo automático:** la aplicación lo hace al iniciar (una vez por día y proceso); si el equipo queda encendido varios días, el respaldo diario depende de la tarea programada. | Días sin copia. | `respaldo_automatico.bat` para el Programador de tareas; el panel marca la copia diaria. | Programar la tarea diaria a las 19:00. |
| R8 | **Datos del POA con redondeos:** el POA imprime totales redondeados (p. ej., insumos Bs 321.398 frente a la suma Bs 321.401). | Diferencias de Bs 1 a 3 en los totales. | Se cargan las celdas del POA y las diferencias se registran como observaciones. | Ninguna (informativo). |
| R9 | **Copia incremental por fecha de modificación:** depende del reloj del equipo. | Un documento con fecha antigua podría no entrar en la copia diaria. | La copia completa semanal incluye todo el archivo digital. | Mantener la hora del equipo sincronizada. |
| R10 | **Alertas solo dentro del sistema** (no hay correo ni SMS). | Una alerta sanitaria o de liquidez puede verse tarde. | Alertas visibles al ingresar y en cada módulo. | Evaluar notificaciones por WhatsApp/correo en una versión futura. |

## B. Deuda técnica

| N.º | Deuda | Detalle | Prioridad |
|---|---|---|---|
| D1 | Fichas de activos de apertura **por cuenta**, no por bien. | El desglose individual quedó por validar (Fase 8). La simulación registra la depreciación del ciclo como CIF del Manual de Costos. | Media |
| D2 | Pruebas de interfaz por renderizado. | AppTest verifica que las 26 páginas cargan sin errores para los 6 roles; los flujos de botones se prueban en la capa de servicios. | Baja |
| D3 | Módulos con prefijo numérico (`01_tesoreria.py`…). | Nombres del Master Plan; se cargan con `importlib`. | Baja |
| D4 | Indicadores calculados al vuelo. | Suficiente para 5 ejercicios; con más historia convendría una tabla de cierres de indicadores. | Baja |
| D5 | El archivo digital guarda los PDF como archivos. | No se valida que sean PDF/A (MAN-ARC-01 §5.2). | Media |
| D6 | Pruebas con bcrypt de costo 4. | Solo en las pruebas, por rapidez; producción usa 12. La revisión de seguridad detecta hashes de costo bajo. | Baja |

### Corregido en la Fase 13
- `costos.aplicar_reversiones_pendientes` dejaba la marca de reversión del corte de MOD en una transacción implícita
  sin confirmar (la detectó la prueba de integración). Ahora usa una transacción explícita.
- `cierre.emitir_estados` registraba la emisión fuera de una transacción explícita. Corregido.
- La matriz no permitía a la Asamblea **ver** los parámetros que debe aprobar: se agregó `AG parametros/VER`.
