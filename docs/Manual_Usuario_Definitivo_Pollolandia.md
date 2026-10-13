# Manual Maestro de Usuario y Guía Operativa Definitiva

## ERP Pollolandia v1.0.1 — Cooperativa Agropecuaria Pollolandia R.L.

**Caranavi, Los Yungas · La Paz · Bolivia**

| Dato | Valor |
|---|---|
| Sistema | ERP Pollolandia (aplicación web local con Streamlit + SQLite) |
| Versión | 1.0.1 (Fases 0 a 13 + exportación oficial a PDF de la contabilidad) |
| Esquema de base de datos | Versión 13 (migraciones 1 a 13) |
| Ejercicio contable | 1 de julio al 30 de junio (PC-01, Ley 356) |
| Ciclo productivo | 73 días, 5 ciclos por ejercicio, lotes de 1.000 aves (POL-03, POA) |
| Pruebas automáticas | 596 pruebas `pytest` |
| Documentos fuente | Plan de Cuentas v3, Descripción de Cuentas, PC-01 a PC-10, POL-01 a POL-07, MAN-05, MAN-006, MAN-ARC-01, Manual de Costos, Manual Presupuestario, POA 2026-2027, PET-RAU 2026-2030, CI-FIN-01 a CI-FIN-18, formatos FIN-xx e INF-CV-xx, MOF del Área Financiera |

> **Cómo leer este manual.** Las rutas de pantalla se escriben así: **Grupo · Página → Pestaña → botón**. Por
> ejemplo, **Ciclo 2 · Compras e inventarios → Órdenes de compra → Emitir orden**. Los nombres de campos y botones
> están copiados de la aplicación. Las cifras normativas (plazos, montos, porcentajes) son los valores vigentes al
> entregar el sistema; el valor que **siempre manda** es el registrado en **Administración · Parámetros normativos**.

---

## Índice

1. [Estructura general y normativa](#1-estructura-general-y-normativa)
2. [Guía de accesos, perfiles y seguridad](#2-guía-de-accesos-perfiles-y-seguridad)
3. [Primeros pasos y puesta en marcha](#3-primeros-pasos-y-puesta-en-marcha)
4. [Convenciones de pantalla](#4-convenciones-de-pantalla)
5. [Módulo de Contabilidad](#5-módulo-de-contabilidad)
6. [Ciclos productivos y Tesorería (Ciclos 1 al 5)](#6-ciclos-productivos-y-tesorería-ciclos-1-al-5)
7. [RAU, cumplimiento legal y archivo](#7-rau-cumplimiento-legal-y-archivo)
8. [Presupuesto, POA y Tablero de indicadores](#8-presupuesto-poa-y-tablero-de-indicadores)
9. [Consejo de Vigilancia](#9-consejo-de-vigilancia)
10. [Autorizaciones (matriz CI-FIN-09)](#10-autorizaciones-matriz-ci-fin-09)
11. [Administración del sistema](#11-administración-del-sistema)
12. [Protocolo de respaldo, mantenimiento y pruebas](#12-protocolo-de-respaldo-mantenimiento-y-pruebas)
13. [Calendario operativo integrado](#13-calendario-operativo-integrado)
14. [Mensajes frecuentes y solución de problemas](#14-mensajes-frecuentes-y-solución-de-problemas)
15. [Anexos](#15-anexos)

---

## 1. Estructura general y normativa

### 1.1 Qué es el ERP Pollolandia

Es el sistema de gestión integral de la cooperativa. Registra la operación completa del negocio avícola —compra de
pollitos e insumos, crianza de 73 días, faenado, venta y cobranza— y la convierte automáticamente en contabilidad
de partida doble, costos por ciclo, presupuesto, indicadores y estados financieros, con los controles internos de
los instrumentos CI-FIN y la fiscalización del Consejo de Vigilancia.

Se usa desde el navegador del equipo de la oficina (`http://localhost:8501`). Los datos quedan en un único archivo de
base de datos SQLite (`datos/pollolandia.db`) que se respalda con la regla 3-2-1.

### 1.2 Arquitectura de tres capas

```
┌───────────────────────────────────────────────────────────────┐
│  PRESENTACIÓN   app.py + modulos/      (pantallas Streamlit)  │
│   - Menú por rol, formularios, tablas, botones de descarga    │
│   - NO contiene reglas de negocio: solo llama a servicios/    │
├───────────────────────────────────────────────────────────────┤
│  LÓGICA         servicios/             (Python puro)          │
│   - Reglas normativas, validaciones, autorizaciones,          │
│     asientos contables, cálculos de costos e indicadores      │
│   - No importa streamlit: se prueba con pytest                │
├───────────────────────────────────────────────────────────────┤
│  DATOS          db/                    (SQLite)               │
│   - Esquema versionado (v1 a v13), semillas, migraciones      │
│   - Claves foráneas ON, journal WAL, disparadores que impiden │
│     borrar o alterar registros contables y de control         │
└───────────────────────────────────────────────────────────────┘
        reportes/  → formatos PDF (ReportLab) y Excel (openpyxl)
        utils/     → formato de montos (Bs 1.234,56) y fechas
        scripts/   → carga del POA, respaldo y restauración
        pruebas/   → 596 pruebas automáticas
```

| Carpeta | Contenido principal |
|---|---|
| `app.py` | Punto de entrada: prepara la base, hace el respaldo automático del día, login, menú por rol |
| `modulos/` | `01_tesoreria.py` … `12_dashboard.py` (los 12 módulos), `libros.py` (diario, mayor, balance, asientos, períodos), `sistema.py` (inicio, usuarios, bitácora, autorizaciones), `administracion.py` (parámetros, respaldos, seguridad), `comun.py` |
| `servicios/` | `contable.py`, `tesoreria.py`, `compras.py`, `inventarios.py`, `planillas.py`, `produccion.py`, `costos.py`, `activos.py`, `ventas.py`, `cierre.py`, `rau.py`, `archivo.py`, `presupuesto.py`, `indicadores.py`, `vigilancia.py`, `seguridad.py`, `auditoria.py`, `parametros.py`, `respaldo.py`, `poa.py`, `revision_seguridad.py`, `identidad.py` |
| `db/` | `esquema.sql` y `esquema_v2.sql` … `esquema_v13.sql`, `semillas.py`, `semillas_presupuesto.py`, `datos_poa.py`, `migraciones.py`, `conexion.py` |
| `reportes/` | `pdf_contable.py` (base institucional y libros), `estados.py` (estados financieros y FIN-INF), `formatos_*.py` (formatos de cada ciclo), `formatos_admin.py` (acta de restauración y revisión de seguridad) |
| `docs/` | Este manual, manuales por rol, checklist de aceptación, revisión de seguridad, riesgos y deuda técnica |

**Cómo se integran.** Cuando un usuario pulsa, por ejemplo, **Registrar faenado**, la pantalla (`modulos/04_produccion.py`)
envía los datos al servicio (`servicios/produccion.py`). El servicio verifica el permiso del rol, valida las reglas
(día ≥ 70, checklist CI-FIN-12, aves disponibles), abre una transacción, guarda el registro, escribe la bitácora de
auditoría con valores antes y después y, si corresponde, genera el asiento a través de `servicios/contable.py`. Si
algo falla, se revierte todo (rollback) y la pantalla muestra el mensaje en español con su código.

### 1.3 Reglas innegociables del sistema

| Regla | Qué significa para el usuario |
|---|---|
| **Partida doble estricta** | Ningún asiento se registra si Debe ≠ Haber. Todo hecho económico (compra, planilla, venta, baja, depreciación…) genera su asiento automáticamente. |
| **Nada se borra** | Los asientos se **reversan** con un asiento espejo autorizado por la GG; las vigencias de parámetros se **cierran**; los formatos emitidos quedan inmutables (disparadores en la base). |
| **Cifras normativas en parámetros** | Tasas, topes, plazos y el SME se leen de la tabla `parametros` con vigencia por ejercicio. Cambiarlos exige el flujo GG → CA/AG con acta. |
| **Bitácora de auditoría** | Toda operación queda registrada: usuario, rol, fecha y hora, tabla, acción, valores antes y después. La bitácora no se puede modificar. |
| **Montos exactos** | Montos con 2 decimales y redondeo ROUND_HALF_UP; se guardan en centavos enteros. Pantalla: `Bs 1.234,56`. Fechas ISO internamente y `dd/mm/aaaa` en pantalla. |
| **Segregación de funciones** | El JF registra pero no autoriza; la GG autoriza pero no registra asientos; la UT no autoriza; el CV fiscaliza sin ejecutar (MOF §3.4, CI-FIN-10). Se aplica aunque alguien agregue un permiso indebido. |
| **Períodos cerrados** | No se registra en un período cerrado (`PeriodoCerradoError`); los períodos se cierran en orden y no se reabren. |

### 1.4 Cumplimiento normativo y contable

| Norma / documento | Dónde se aplica en el sistema |
|---|---|
| **Plan de Cuentas v3** y Descripción de Cuentas | Catálogo de cuentas cargado en la base (grupos 1 a 9); cuentas (*) habilitadas desde el ejercicio 2027 (centro de costo de alimento 6.1.01.02); exportable en PDF (**Períodos contables → Plan de Cuentas**). |
| **PC-01 a PC-10** (políticas contables) | Ejercicio 1-jul/30-jun; CPP perpetuo (PC-07); costeo por absorción y sufijos (PC-03/PC-04); depreciación lineal por categoría (PC-06); provisiones laborales por doceavos (PC-09); devoluciones (PC-08). |
| **POL-01 a POL-07** | Ciclo de 73 días y densidad (POL-03); tesorería, SME, 120 % y jerarquía de pagos (POL-04); crédito HORECA (POL-05); CAPEX (POL-02); RAU (POL-07). |
| **MAN-05** (procesos y procedimientos) | PR-FIN-01 a PR-FIN-11: OC solo los lunes, eventual los lunes, cierre mensual, EEFF. |
| **MAN-006** (gestión contable y financiera) | Estructura del Balance General, Estado de Resultados escalonado, Flujo de Efectivo método directo, Evolución del Patrimonio (§18) y Notas 1-16 (§19). |
| **MAN-ARC-01** (archivo contable) | Tabla de retención (72 documentos), expedientes, foliación, préstamos, actas; respaldo 3-2-1, prueba de restauración semestral, contraseñas cada 90 días, CV solo lectura (§5.2). |
| **Manual del Sistema de Costos** | Centros 6.1.01.01/02 con sufijos 01 a 40; cierre del día 73 a 1.1.03.12; costo Bs/kg (ciclo 2026: Bs 116.286,28 y Bs 14,91/kg). |
| **Manual del Sistema Presupuestario** | Presupuesto Maestro 2026-2030, presupuesto por ciclo, calendario abril-julio, FIN-TES-09 a FIN-TES-13, semáforos de ejecución, indicadores de la sección 1.3. |
| **POA 2026-2027** | 49 actividades por área y trimestre; calendario C1-C5; flujo mensual oct-2026 a jun-2027; presupuesto por área. |
| **PET-RAU 2026-2030** | Calendario fiscal, cuota por hectárea, Form. 701, indicadores I-1 (autoconsumo ≥ 65 %) e I-2 (ventas de alimento ≤ 15 % de ingresos), expediente EXP-RAU A-G, plan de respuesta ante fiscalización. |
| **CI-FIN-01 a CI-FIN-18** | Caja chica (01), excedentes (02), compras (03), eventual (04), ventas y crédito (06, 13, 15), autorizaciones (09), segregación (10), bioseguridad (11), faenado (12), horas (14), compras de emergencia (16), suplencias (17) y **verificación previa de indicadores por el CV (18)**. |
| **Ley 356 y DS 1995** | EEFF a los Consejos en 60 días y a la Asamblea en 90 (Art. 55); reservas Legal ≥ 5 % y Educación ≥ 1 %; mayoría calificada 2/3. |
| **DS 24463, DS 0099, DS 24988 (RAU)** | No hay IVA/IT/IUE mensual; cuota anual por hectárea; exhibición del Certificado RAU en cada venta; prohibición de servicios a terceros. |

---

## 2. Guía de accesos, perfiles y seguridad

### 2.1 Roles del sistema

El ERP tiene seis roles. La tabla relaciona cada uno con la denominación de uso cotidiano.

| Rol (código) | Denominación cotidiana | Quién es | Naturaleza |
|---|---|---|---|
| **GG** | Gerencia General | Gerente General (socio designado) y, como suplentes, el Presidente y el Tesorero del CA (CI-FIN-17) | Autoriza y firma; no registra asientos |
| **JF** | Contabilidad / Finanzas | Jefe de la Unidad de Finanzas y Comercialización (Contador General; cajero por designación) | Registra todo; no autoriza ni firma |
| **UT** | Operaciones / Granja | Encargado de Granja (Unidad Técnica; 1 por cada 3.000 aves) | Registra la operación de la granja; no contabiliza ni autoriza |
| **CA** | Consejo de Administración | Presidente, Secretario y Tesorero del CA | Aprueba lo que le asigna CI-FIN-09 |
| **CV** | Consejo de Vigilancia | Miembros del CV | Fiscaliza: **solo lectura**, salvo sus propios informes, verificaciones y cofirmas |
| **AG** | Asamblea General | Secretaría de la Asamblea | Aprueba EEFF, excedentes, deuda y variaciones mayores de CAPEX |

### 2.2 Menú visible por rol

| Página del menú | GG | JF | UT | CA | CV | AG |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| General · Inicio | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Contabilidad · Libro diario / Libro mayor / Balance de comprobación / Períodos contables | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Contabilidad · Registrar asiento / Reversar asiento | – | ✔ | – | – | – | – |
| Ciclo 1 · Tesorería | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Ciclo 2 · Compras e inventarios / Personal y planillas | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Ciclo 3 · Producción y trazabilidad / Costos matriciales | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Ciclo 1 y 3 · Activos fijos y CAPEX | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Ciclo 4 · Ventas, HORECA y cobranza | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Ciclo 5 · Cierres y estados financieros | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Transversal · RAU, cumplimiento y archivo | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Información gerencial · Presupuesto y POA | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Control interno · Consejo de Vigilancia | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Información gerencial · Tablero de indicadores | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Control interno · Autorizaciones | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Administración · Usuarios | ✔ | – | – | – | – | – |
| Administración · Parámetros normativos | ✔ | ✔ | – | ✔ | ✔ | ✔ |
| Administración · Respaldos 3-2-1 | ✔ | ✔ | – | ✔ | ✔ | – |
| Administración · Revisión de seguridad / Bitácora de auditoría | ✔ | – | – | – | ✔ | – |
| Mi cuenta · Cambiar contraseña | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| **Total de páginas** | 24 | 23 | 10 | 21 | 23 | 14 |

Que una página sea visible no significa que todos sus botones lo sean: dentro de cada pantalla, los botones de
registrar, autorizar o aprobar solo aparecen si el rol tiene ese permiso.

### 2.3 Lo que cada rol puede hacer (permisos de escritura)

| Rol | Puede registrar o decidir |
|---|---|
| GG | **Autorizar** en tesorería, compras, inventarios, planillas, producción, costos, activos, ventas, contabilidad (cierre de mes), RAU y archivo; **crear/editar** presupuesto y usuarios; **proponer** cambios de parámetros; **configurar** respaldos. |
| JF | **Crear/editar** en tesorería, compras, inventarios, planillas, producción, costos, activos, ventas, RAU, archivo y presupuesto; **crear, editar y reversar** asientos; **crear** respaldos y actas de restauración. |
| UT | **Crear** en inventarios (stock, recepción), planillas (circular de cierre, hojas de tiempo), producción (bitácora, alertas, faenado, disposición, muestreo), ventas (despacho) y RAU; **editar** producción. |
| CA | **Aprobar** presupuesto, activos (CAPEX), ventas (tarifario), RAU, contabilidad (presentación de EEFF) y parámetros. |
| CV | **Crear/editar** solo en vigilancia (sus informes INF-CV); **verificar** en todos los ciclos; **cofirmar** desembolsos; **verificar** actas de restauración. |
| AG | **Aprobar** contabilidad (EEFF), presupuesto, activos, parámetros y tesorería (excedentes). |

### 2.4 Políticas de seguridad

| Política | Valor vigente | Fuente | Comportamiento en el sistema |
|---|---|---|---|
| Caducidad de contraseña | **90 días** (`password_caducidad_dias`) | MAN-ARC-01 §5.2 | Al ingresar con una contraseña de 90 días o más, el sistema obliga a cambiarla antes de mostrar el menú. |
| Contraseña temporal | Primer ingreso | Política de acceso | Todo usuario nuevo o restablecido debe cambiarla en su primer ingreso. |
| Longitud y composición | Mínimo **8** caracteres, con **letras y números**, distinta de la anterior | Política de acceso | Si no cumple, el formulario lo rechaza con el motivo. |
| Bloqueo por intentos | **3** intentos fallidos → bloqueo de **15 minutos** | Política de acceso | El mensaje es el mismo para usuario inexistente o clave errada; la GG puede desbloquear restableciendo la contraseña. |
| Inactividad | **30 minutos** | Política de acceso | La sesión se cierra sola y pide ingresar de nuevo. |
| Cifrado de contraseñas | bcrypt de costo 12 | Fase 3 | Las contraseñas nunca se guardan en texto. |
| CV solo lectura | Permanente | MAN-ARC-01 §5.2; MOF §3.4 | El rol tiene «solo lectura transaccional» y una incompatibilidad que bloquea registrar, reversar, autorizar y aprobar fuera de sus informes. |
| Registro de accesos | Permanente | MAN-ARC-01 §5.2 | Ingresos, salidas e intentos fallidos quedan en la bitácora. |
| Baja de usuarios | Al término del cargo | MAN-ARC-01 §5.2 | La GG desactiva al usuario; su sesión abierta queda invalidada de inmediato. |

### 2.5 Ingreso, cambio de contraseña y cierre de sesión

1. Abra el sistema (doble clic en `iniciar_erp.bat`). Aparece «Conexión a SQLite correcta» y el formulario
   **Ingreso al sistema**.
2. Escriba **Usuario** (no distingue mayúsculas) y **Contraseña** y pulse **Ingresar**.
3. Si el sistema muestra «Debe cambiar su contraseña antes de continuar…», complete **Contraseña actual**,
   **Nueva contraseña** y **Repita la nueva contraseña** y pulse **Cambiar contraseña**.
4. Para cambiarla en cualquier momento: **Mi cuenta · Cambiar contraseña**.
5. Para salir: botón **Cerrar sesión** en la barra lateral (queda registrado en la bitácora).

---

## 3. Primeros pasos y puesta en marcha

### 3.1 Instalación en Windows

1. Instale **Python 3.11 o superior** marcando *Add python.exe to PATH*.
2. Copie la carpeta `pollolandia_erp` al equipo de la oficina (por ejemplo, `C:\Pollolandia\pollolandia_erp`).
3. Doble clic en **`iniciar_erp.bat`**. La primera vez crea el entorno virtual `.venv` e instala las dependencias
   (`requirements.txt`); luego abre el navegador en `http://localhost:8501`.
4. Al arrancar, el sistema aplica las migraciones pendientes y las semillas (plan de cuentas, parámetros,
   calendario, socios, roles, balance de apertura, presupuesto maestro, POA) y ejecuta el **respaldo automático
   del día**.

Alternativa por terminal (PowerShell, desde la carpeta del proyecto):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m db.migraciones            # crea o actualiza la base de datos
streamlit run app.py                # abre el ERP
```

### 3.2 Configuración inicial (una sola vez)

1. **Usuario de la Gerencia General.** Si la base no tiene usuarios, aparece **Configuración inicial**: elija el
   **Socio que ejerce la Gerencia General**, escriba **Usuario**, **Contraseña** y **Repita la contraseña** y pulse
   **Crear usuario de la Gerencia General**.
2. **Resto de usuarios.** Con el usuario de la GG: **Administración · Usuarios → Nuevo usuario**. Complete
   **Usuario**, **Rol**, **Nombre completo**, **Socio vinculado** (obligatorio para miembros de los Consejos y para
   los suplentes) y **Contraseña temporal**; pulse **Crear usuario**. Un socio solo puede tener un usuario.
3. **NIT de la cooperativa.** **Administración · Parámetros normativos → Vigentes**: busque `entidad_nit`, proponga
   el valor real con la fuente (certificado del SIN) y el CA lo aprueba con acta. Desde entonces aparece en el
   membrete de todos los PDF contables.
4. **Certificado RAU.** **RAU, cumplimiento y archivo → Calendario fiscal → Registrar certificado** (número y fecha
   de emisión). También se imprime en el membrete.
5. **Datos del POA.** **Presupuesto y POA → Flujo POA vs real → Cargar datos del POA 2026-2027**, o por consola
   `python -m scripts.cargar_datos_poa`.
6. **Destinos de respaldo.** **Administración · Respaldos 3-2-1 → Destinos**: disco externo y carpeta fuera de la
   granja o sincronizada con la nube; luego programe `respaldo_automatico.bat` (sección 12).
7. **Fondo de caja chica.** **Tesorería → Caja → Constituir el fondo fijo** (sección 6.1).
8. **Fichas de activos de apertura.** **Activos fijos y CAPEX → Libro de activos → Abrir fichas del balance de
   apertura**.
9. **Revisión de seguridad.** **Administración · Revisión de seguridad**: deje los controles en verde.

### 3.3 La pantalla de Inicio

Muestra **Versión**, **Ejercicio** vigente (1-jul a 30-jun) y **Autorizaciones que esperan su firma**. El
desplegable **Estado de la base de datos** indica el archivo, claves foráneas activas, modo WAL y versión de SQLite.

---

## 4. Convenciones de pantalla

| Elemento | Significado |
|---|---|
| Menú lateral | «Grupo · Página». El grupo indica el ciclo transaccional (Ciclo 1 Tesorería, Ciclo 2 Egresos, Ciclo 3 Transformación, Ciclo 4 Ingresos, Ciclo 5 Información, Transversal, Control interno, Administración). |
| Pestañas | Cada página se divide en pestañas por proceso. Todas las pestañas se calculan al abrir la página. |
| Chips de color | **VERDE / BUENO** cumple; **AMARILLO / NORMAL** alerta o pendiente; **ROJO / MALO** incumple o vencido. |
| Recuadro rojo | Una regla no se cumple (mensaje y código, por ejemplo `OC_DIA_NO_PERMITIDO`) o una alerta de plazo (por ejemplo «FIN-TES-12 del ciclo 1 pendiente»). No es una falla del sistema. |
| Recuadro verde | La operación se registró. |
| Montos | `Bs 1.234,56` en pantalla y PDF. En los campos numéricos se escribe con punto decimal (`1234.56`). |
| Fechas | Selector `DD/MM/AAAA`. |
| Botón «Descargar … (PDF)» | Descarga inmediata del formato o reporte. |
| Botón «Preparar … (PDF)» | Para documentos extensos (libro diario, mayor de todas las cuentas, plan de cuentas): primero se prepara y después aparece el botón de descarga. |
| Formatos (pestaña) | En cada ciclo, la pestaña **Formatos** permite elegir el formato FIN-xx/INF-CV-xx y descargarlo en PDF; el campo **N.º de ciclo (opcional)** se imprime en los datos de contexto. |


---

## 5. Módulo de Contabilidad

La contabilidad se reparte en dos lugares del menú:

* **Contabilidad · …** (archivo `modulos/libros.py`): Libro diario, Libro mayor, Balance de comprobación,
  Registrar asiento, Reversar asiento y Períodos contables.
* **Ciclo 5 · Cierres y estados financieros** (archivo `modulos/08_contabilidad.py`): cierre mensual, costeo e
  indicadores, estados financieros, notas, cierre anual, excedentes y plazos de 60/90 días.

> **Importante.** La gran mayoría de los asientos **no se escriben a mano**: los generan los ciclos (compras,
> planillas, ventas, costos, activos, tesorería, RAU). El registro manual se reserva para hechos que no tienen un
> ciclo propio y para ajustes autorizados.

### 5.1 Apertura del ejercicio

1. **Balance de apertura.** Al crear la base, el sistema registra el comprobante **APERTURA-00001** del 1-jul-2026
   con los saldos del Balance de Apertura (por ejemplo, Banco 1.1.01.03 Bs 138.010 y Capital Social Bs 534.300).
   Es un asiento normal: cuadra, figura en el diario y puede imprimirse.
2. **Períodos.** Se abren los 12 períodos mensuales del ejercicio (julio = período 1 … junio = período 12).
   Consulte su estado en **Contabilidad · Períodos contables** (selector **Ejercicio (1-jul a 30-jun)**).
3. **Plan de cuentas.** El catálogo v3 queda cargado con sus nueve grupos: 1 Activo, 2 Pasivo, 3 Patrimonio,
   4 Reguladoras de activo, pasivo y patrimonio (depreciaciones acumuladas, previsiones), 5 Ingresos, 6 Costos
   (centros de costo con sufijo), 7 Gastos, 8 Otras cuentas de resultado y 9 Cuentas de orden. Las cuentas marcadas «Desde 2027» (por ejemplo, el centro 6.1.01.02 de la planta de
   alimento) no aceptan movimientos antes del 1-jul-2027.
4. **Ejercicios siguientes.** La apertura del nuevo ejercicio se produce con el **cierre anual** (sección 5.11):
   las cuentas de resultado se cierran contra el excedente y los saldos patrimoniales continúan.

### 5.2 Tipos de comprobante

| Tipo | Uso | Quién lo origina |
|---|---|---|
| **APERTURA** | Saldos iniciales del ejercicio | Automático (semilla / cierre anual) |
| **INGRESO** | Entradas de dinero: cobros, ventas al contado, depósitos | Ventas, Tesorería o registro manual del JF |
| **EGRESO** | Salidas de dinero: pagos a proveedores, sueldos, aportes, jornales, fletes | Compras, Planillas, Tesorería o registro manual |
| **TRASPASO** | Movimientos sin efectivo: devengos, consumos al CPP, CIF, cierre de costos, depreciación, provisiones | Ciclos y registro manual |
| **AJUSTE** | Correcciones, ajustes de cierre y **reversiones** (el espejo de un asiento anulado es un AJUSTE con glosa «Reversión de …») | Solo el JF **con autorización AJUSTE_CONTABLE firmada por la GG** |
| **CIERRE** | Cierre anual de resultados | Pestaña **Cierre anual** |

Los comprobantes APERTURA y CIERRE no se pueden reversar.

> El «Libro **Diario**» no es un tipo de comprobante: es el registro cronológico de **todos** los comprobantes
> anteriores (sección 5.5).

### 5.3 Registrar un asiento manual (rol JF)

Ruta: **Contabilidad · Registrar asiento**.

1. **Tipo de comprobante**: elija INGRESO, EGRESO, TRASPASO o AJUSTE.
2. **Fecha**: debe caer en un período **abierto** del ejercicio; si no, aparece `PeriodoCerradoError`.
3. **Documento de respaldo**: número de factura, recibo, contrato o formato FIN-xx (obligatorio; MAN-ARC-01).
4. **Glosa**: descripción del hecho económico (obligatoria).
5. **Autorización de la GG**: solo aparece y es obligatoria para AJUSTE. Antes, el JF solicita la autorización en
   **Control interno · Autorizaciones → Nueva solicitud** con el tipo AJUSTE_CONTABLE y la GG la firma.
6. **Editor de líneas** (tabla editable; añada filas con el signo «+» al pie):

   | Columna | Cómo llenarla |
   |---|---|
   | **Cuenta** | Elija la cuenta de detalle (código y nombre). Solo se aceptan cuentas imputables y vigentes. |
   | **Debe** / **Haber** | Monto en Bs con punto decimal. Cada línea lleva Debe **o** Haber, nunca ambos. |
   | **Sufijo (Grupo 6)** | Obligatorio en las cuentas de los centros de costo 6.1.01.01/02 y vacío en las demás. Sufijos del Manual de Costos: **01-13 materia prima directa** (01 pollitos BB, 02 alimento comprado, 08 veterinarios, 09 limpieza, 10 insumos de crianza, 11 alimento propio (*), 12 bolsas), **20-22, 38-39 mano de obra directa** (salario, aguinaldo, eventual, aporte patronal, indemnización), **30-37 CIF** (depreciaciones, mantenimiento, energía y agua) y **40-41 pérdidas normales** (mortandad y mermas hasta 5 %). Los marcados (*) rigen desde 2027. |
   | **Glosa de línea** | Detalle opcional de la línea. |

7. Controle los indicadores que se actualizan solos: **Total Debe**, **Total Haber** y **Diferencia**. La
   diferencia debe ser **Bs 0,00**.
8. Pulse **Registrar asiento**. Si todo es correcto aparece el mensaje verde con el número (por ejemplo
   «Asiento TRASPASO-00042 registrado») y el botón **Descargar comprobante TRASPASO-00042 (PDF)**.

**Errores habituales:** «El asiento no cuadra» (Diferencia ≠ 0), «La cuenta X exige sufijo», «La cuenta X no admite
sufijo», «Cuenta no vigente en la fecha», «Período cerrado», «Falta la autorización AJUSTE_CONTABLE».

### 5.4 Reversar un asiento (rol JF con autorización de la GG)

Los asientos **nunca se borran ni se editan**. Para anular uno:

1. El JF pide en **Autorizaciones → Nueva solicitud** la autorización de tipo «Asiento de ajuste o reversión contable» (AJUSTE_CONTABLE)
   indicando el asiento en **Referencia**; la GG la firma.
2. **Contabilidad · Reversar asiento** → **Asiento**: elija el comprobante (solo aparecen los no reversados).
3. **Motivo de la reversión**: obligatorio; queda en la glosa del espejo y en la bitácora.
4. Si el período original está cerrado marque **Reversar con otra fecha (si el período original está cerrado)** y
   elija **Fecha de la reversión** en un período abierto.
5. **Autorización de la GG**: seleccione la autorización firmada.
6. Pulse **Reversar**. Se crea un comprobante **AJUSTE** con glosa «Reversión de <comprobante>: <motivo>» y Debe y Haber invertidos; ambos quedan vinculados y
   se ofrece el botón **Descargar comprobante … (PDF)** del comprobante de reversión.

Después de reversar, registre el asiento correcto (sección 5.3).

### 5.5 Libro diario

Ruta: **Contabilidad · Libro diario** (GG, JF, CA, CV, AG).

1. Elija **Desde** y **Hasta**.
2. Opcional: **Incluir asientos reversados y sus reversiones** (por defecto se muestran todos los comprobantes
   vigentes; marcar la casilla añade los pares anulados para revisión).
3. La tabla muestra fecha, número, tipo, glosa, documento y totales de cada comprobante; al desplegar uno se ve su
   detalle por cuenta.
4. **Exportación oficial en PDF**:
   * **Preparar Libro Diario (PDF)** → **Descargar Libro Diario (PDF)**: libro completo del rango, con todas las
     líneas, totales por comprobante, «TOTALES DEL PERÍODO» y el recuadro **Totales de verificación** («Control:
     Debe = Haber — CUADRA»).
   * **Preparar comprobantes del período (PDF)**: un comprobante por página, listo para archivar con su respaldo.
   * **Comprobante a imprimir**: elija uno y pulse su botón de descarga directa.

### 5.6 Libro mayor

Ruta: **Contabilidad · Libro mayor**.

1. **Desde** / **Hasta**.
2. **Cuenta**: elija la cuenta.
3. **Sufijo (elemento del costo)**: solo para cuentas del Grupo 6; permite ver el mayor de un elemento
   (por ejemplo 6.1.01.01-11 alimento balanceado).
4. Lea los indicadores: **Saldo inicial** (acumulado antes de «Desde»), **Debe**, **Haber**, **Saldo final**.
   La tabla muestra cada movimiento con su saldo acumulado.
5. **Descargar Mayor <cuenta> (PDF)** (por ejemplo, «Descargar Mayor 1.1.01.03 (PDF)»): bloque «SALDO INICIAL», movimientos y «TOTALES Y SALDO FINAL».
6. **Mayor de todas las cuentas**: marque **Abrir los centros de costo por sufijo** si desea un mayor por cada
   sufijo de los centros de costo y pulse **Preparar Mayor de todas las cuentas (PDF)** → **Descargar**.

### 5.7 Balance de comprobación de sumas y saldos

Ruta: **Contabilidad · Balance de comprobación**.

1. **Desde** / **Hasta**.
2. **Nivel**: **Cuenta**, **Subgrupo**, **Clasificación** o **Grupo** (agregación en pantalla).
3. Columnas: Sumas Debe, Sumas Haber, Saldo deudor, Saldo acreedor; los totales deben coincidir dos a dos
   (aparece «CUADRA»).
4. Descargas oficiales:
   * **Balance de 4 columnas (sumas y saldos)** (PDF).
   * **Balance de 8 columnas** (PDF): sumas, saldos, **Inventario** (Activo / Pasivo y Patrimonio) y
     **Resultados** (Pérdidas / Ganancias), con la fila **EXCEDENTE DEL PERÍODO** y «TOTALES IGUALADOS». En el
     escenario 2026 el excedente es **Bs 210.293,39**.

### 5.8 Períodos contables

Ruta: **Contabilidad · Períodos contables**.

* **Ejercicio (1-jul a 30-jun)**: lista los 12 períodos con su estado (ABIERTO / CERRADO), fecha y usuario de cierre.
* **Descargar estado de los períodos <ejercicio> (PDF)**.
* **Preparar Plan de Cuentas (PDF)** → descarga del catálogo completo (código, nombre, naturaleza, sufijos,
  vigencia «Desde 2027»).
* **Cerrar período** (GG): elija **Período a cerrar** (siempre el más antiguo abierto), marque **Confirmo que el
  período está conciliado y revisado** y pulse **Cerrar período**. Lo habitual es cerrar el mes desde
  **Cierres y estados financieros → Cierre mensual** (sección 5.10), que verifica la lista FIN-INF-01 antes.

### 5.9 La exportación oficial a PDF (ReportLab)

Todos los PDF contables comparten el **membrete institucional**:

```
COOPERATIVA AGROPECUARIA POLLOLANDIA R.L.
Caranavi, La Paz – Bolivia  |  NIT: <parámetro entidad_nit>  |  RAU: <último certificado registrado>
<TÍTULO DEL DOCUMENTO>
<Subtítulo: período, cuenta o fecha de corte>
───────────────────────────────── (línea verde)
...
Emitido el 05/07/2027 10:30 por <Nombre> (<Cargo>)   <código>   Página 1 de N
```

| Documento | Dónde se descarga | Contenido distintivo |
|---|---|---|
| Comprobante (Ingreso/Egreso/Traspaso/Ajuste/Apertura/Reversión) | Registrar asiento, Reversar asiento, Libro diario | Ficha del comprobante, líneas con sufijo, TOTALES, «Partida doble — CUADRA», firmas **Elaborado por: Contabilidad**, **Autorizado por: Gerencia General**, **Vº Bº Consejo de Vigilancia** |
| Comprobantes del período | Libro diario | Un comprobante por página |
| Libro Diario | Libro diario | Totales por comprobante y del período, totales de verificación |
| Libro Mayor analítico | Libro mayor | Saldo inicial, movimientos, saldo acumulado y final |
| Mayor de todas las cuentas | Libro mayor | Una sección por cuenta (y por sufijo si se elige) |
| Balance de 4 columnas | Balance de comprobación | Sumas y saldos |
| Balance de 8 columnas | Balance de comprobación | Sumas, saldos, inventario y resultados |
| Estado de los períodos | Períodos contables | Estado y responsables de cierre |
| Plan de Cuentas | Períodos contables | Catálogo v3 completo |
| Estados financieros | Cierres y EEFF → Estados financieros | Balance General, Estado de Resultados, Flujo de Efectivo, Evolución del Patrimonio, con «Expresado en Bolivianos», recuadro **Notas y observaciones** y firmas **Representante Legal – Gerente General**, **Contador General**, **Presidente del Consejo de Administración** y **Vº Bº Consejo de Vigilancia (INF-CV-08)** |
| Notas 1-16 | Cierres y EEFF → Notas 1-16 | Notas a los EEFF (MAN-006 §19) |
| Paquete de EEFF | Cierres y EEFF → Plazos 60/90 días | Todo lo anterior en un PDF con la **Huella SHA-256** del paquete emitido |
| FIN-INF-01 / FIN-INF-03 / FIN-INF-04 | Cierre mensual / Costeo e indicadores | Lista de verificación, consolidado de costeo, resultados mensuales |

**Para que el membrete salga completo**, registre el NIT (parámetro `entidad_nit`) y el Certificado RAU
(sección 7.1). Mientras no existan, el PDF muestra «NIT: Por registrar» y «RAU: En trámite».

### 5.10 Cierre mensual (PR-FIN-08, FIN-INF-01)

Ruta: **Ciclo 5 · Cierres y estados financieros → Cierre mensual**. El sistema propone el período abierto más antiguo.

1. Revise la **lista de verificación FIN-INF-01**: cada fila es un control con su estado (por ejemplo,
   conciliación bancaria firmada, reposición de caja, planilla contabilizada, depreciación del mes, reversión del
   corte de MOD aplicada, consumos valorados, sin asientos descuadrados). Las filas en rojo indican qué falta y en
   qué módulo resolverlo.
2. JF: pulse **Registrar FIN-INF-01 (Verificado por la Unidad de Finanzas)**.
3. GG: marque **Confirmo la conciliación del mes y el cierre (el período no se reabre)** y pulse **Cerrar el mes**.
4. Descargue el FIN-INF-01 en PDF para el archivo.

> Los meses se cierran en orden. Un mes con pendientes no se puede cerrar: el sistema dice cuál falta.

### 5.11 Costeo e indicadores mensuales

**Cierres y estados financieros → Costeo e indicadores**: elija el **Mes** y consulte **FIN-INF-03 · Consolidado de
costeo mensual** y **FIN-INF-04 · Estado de resultados mensual e indicadores** (ICL, RINC, margen operativo del
mes). Ambos se descargan en PDF y Excel.

### 5.12 Estados financieros (MAN-006 §18)

**Cierres y estados financieros → Estados financieros** → elija la **Fecha de corte**. Sub-pestañas:

| Sub-pestaña | Contenido | Control |
|---|---|---|
| **Balance General** | Activo corriente/no corriente, pasivo, patrimonio, resultado no cerrado | Activo = Pasivo + Patrimonio («cuadra») |
| **Estado de Resultados** | Ingresos por línea (carne, alimento, gallinaza), costo de ventas, gastos, excedente neto | Excedente = resultado del Balance |
| **Flujo de Efectivo** | Método directo: operación, inversión y financiamiento | Variación = saldo final − saldo inicial de efectivo |
| **Evolución del Patrimonio** | Capital, reservas, excedentes | Saldo final = patrimonio del Balance |

Cada sub-pestaña ofrece **Descargar … (PDF)** y **(Excel)**.

**Notas 1-16**: revise las notas generadas y complete **Nota 16 — hechos posteriores (vacío = no se identificaron)**.

### 5.13 Cierre anual

**Cierres y estados financieros → Cierre anual** (después de cerrar junio):

1. JF: **Solicitar autorización de la GG (AJUSTE_CONTABLE)**; la GG firma en Autorizaciones.
2. JF: **Autorización firmada** → **Ejecutar cierre anual** (asiento de CIERRE de las cuentas de resultado).
3. GG: **Cerrar el ejercicio (requiere junio cerrado)**.

### 5.14 Distribución de excedentes (Ley 356, CI-FIN-02)

**Cierres y estados financieros → Excedentes**:

1. JF: **Ejercicio con cierre anual**, **Reserva Legal (%)** (mínimo 5 %), **Educación (%)** (mínimo 1 %),
   **Reinversión (Bs, Asamblea)**, **Capitalización (Bs)**, **Fecha del informe** → **Registrar propuesta**.
2. CV: revise, marque **La Reserva de Reinversión es conforme al Plan CAPEX**, elija **Dictamen**, escriba
   **Observaciones** → **Emitir INF-CV-04**.
3. JF: **Solicitar autorización de la Asamblea**; la AG aprueba en Autorizaciones (mayoría de 2/3).
4. JF: seleccione **Autorización de la Asamblea** (y **Autorización de capitalización** si corresponde), **Acta de la
   Asamblea**, **Fecha de la Asamblea** → **Registrar apropiación y excedente en distribución**.
5. GG: **Firmar las liquidaciones individuales**.
6. JF: **Fecha de pago** → **Solicitar autorización de desembolso (GG + CV)** → elija la autorización → **Pagar
   excedentes**. Las liquidaciones por socio se descargan en **Liquidación a descargar**.

### 5.15 Plazos de 60 y 90 días (Ley 356 Art. 55)

**Cierres y estados financieros → Plazos 60/90 días**:

1. Presentación a los Consejos (máximo **60 días** después del 30-jun): **Ejercicio**, **Fecha de presentación**,
   **Acta del Consejo de Administración**, **Hechos posteriores (Nota 16)** → **Registrar presentación y emitir el
   paquete**. Se congela el paquete de EEFF con su huella SHA-256.
2. Aprobación de la Asamblea (máximo **90 días**): **Fecha de la Asamblea**, **Acta de la Asamblea**,
   **Referencia del INF-CV-08 (Consejo de Vigilancia)** → **Registrar aprobación**.
3. **Paquete**: descarga del PDF emitido. Los semáforos muestran los días restantes de cada plazo.

---

## 6. Ciclos productivos y Tesorería (Ciclos 1 al 5)

### 6.0 El ciclo de negocio de punta a punta

```
 Tesorería (Ciclo 1)        Egresos (Ciclo 2)              Transformación (Ciclo 3)            Ingresos (Ciclo 4)          Información (Ciclo 5)
 ──────────────────         ─────────────────              ────────────────────────            ──────────────────          ─────────────────────
 Fondo fijo de caja    →    OC del lunes (3 cotiz.)   →    Apertura del ciclo (día 1)     →    Pre-venta (≥ 3 días)   →    Cierre de costos (día 73)
 SME y flujo FIN-TES-05     Recepción y calidad            Bitácora diaria (CI-FIN-11)         Notas de entrega + RAU      Cierre mensual FIN-INF-01
 Pagos (jerarquía POL-04)   Programación FIN-EGR-05        Muestreo de peso semanal            Cartera HORECA (1 día)      EEFF y notas
 Conciliación (5 días)      Planilla mensual              Bajas y disposición                 Parte diario + depósito     Indicadores CI-FIN-18
                            Eventual (circular, lunes)     Faenado (día ≥ 70; 73)             Margen por canal FIN-ING-08
```

**Datos del ciclo estándar 2026 (POA §1.1, Manual de Costos §6):** 3.000 aves en 3 lotes de 1.000; 73 días;
mortalidad normal hasta 3 %; 7.800 kg de carne (2,6 kg/ave); precio Bs 23,50/kg → Bs 183.300; gallinaza 3 cargas ×
Bs 500; costo del ciclo POA Bs 116.286,28 (Bs 14,91/kg); flete Bs 970 por lote; jornal eventual Bs 1,50 por ave
faenada.

---

### 6.1 Tesorería · Ciclo 1 (POL-04, CI-FIN-01, PR-FIN-01/02)

Ruta: **Ciclo 1 · Tesorería**. Pestañas: **Caja · Bancos · SME y flujo · Conciliación · Formatos**.

#### 6.1.1 Caja chica (fondo fijo, CI-FIN-01)

| Regla | Valor vigente |
|---|---|
| Tope del fondo fijo | Bs 5.000 (`tope_caja_bs`) |
| Gasto menor máximo por comprobante | Bs 500 (`gasto_menor_max_bs`) |
| Arqueos sorpresivos del CV por año | 2 (`arqueos_por_anio`) |

**Constituir el fondo (una vez):** pestaña **Caja** → **Constituir el fondo fijo de Bs 5.000,00** → **Fecha**,
**Cuenta de origen** (banco), **Custodio designado**, **Resolución de designación del custodio** → **Constituir fondo**
(EGRESO banco → 1.1.01.05 Caja chica).

**Operación diaria** (elija el **Día de control**):

| Operación | Campos | Botón | Efecto |
|---|---|---|---|
| Gasto menor | **Monto (Bs)** (≤ Bs 500), **Cuenta de gasto**, **Sufijo (si es centro de costo)**, **N.º de comprobante**, **Beneficiario**, **Concepto** | **Registrar gasto** | Queda pendiente de reposición; no genera asiento hasta reponer |
| Cobro en efectivo | **Monto (Bs)**, **Cuenta de ingreso**, **Nota de entrega / recibo**, **Concepto** | **Registrar cobro** | El efectivo cobrado debe depositarse |
| Depósito del efectivo | **Monto (Bs)**, **Cuenta**, **Boleta de depósito** | **Registrar depósito** | Traspasa a banco |

**Reposición del fondo:** JF **Solicitar reposición de los gastos pendientes** → otra unidad revisa y pulsa **Validar**
(o **Rechazar** con **Observación**) → JF elige **Cuenta** bancaria y **Ejecutar reposición** (EGRESO a los gastos,
con sufijo cuando corresponde). La pantalla muestra el semáforo del fondo: efectivo + comprobantes = Bs 5.000.

**Arqueo sorpresivo (INF-CV-03):** lo registra el CV aquí o en Vigilancia (sección 9.3).

#### 6.1.2 Bancos

Sub-pestañas **Depósito**, **Pago** y **Transferencia**:

| Sub-pestaña | Campos | Botón |
|---|---|---|
| **Depósito** | **Fecha**, **Monto (Bs)**, **Cuenta**, **Contrapartida**, **Descripción**, **Documento** | **Registrar depósito** |
| **Pago** | **Fecha**, **Monto (Bs)**, **Cuenta**, **Cuenta del gasto o de la deuda**, **Sufijo (si es centro de costo)**, **Categoría (jerarquía POL-04)**, **Compromiso asociado**, **Beneficiario**, **N.º de cheque (opcional)**, **Descripción**, **Autorización (…)** | **Registrar pago** |
| **Transferencia** | **Fecha**, **Monto (Bs)**, **Origen**, **Destino**, **Descripción** | **Registrar transferencia** |

**Autorización de desembolsos (Matriz CI-FIN-09):** hasta Bs 1.000 no se necesita firma; **más de Bs 1.000** firma de
la GG; **más de Bs 5.000** firma **mancomunada GG + CV**. El selector **Autorización (…)** indica entre paréntesis las
firmas requeridas. Si el saldo proyectado queda bajo el **120 % del SME** se exige además autorización escrita.

**Compromisos de gasto no recurrente (POL-04 Art. 2):** **Monto del compromiso (Bs)**, **Tipo**, **Fecha**,
**Descripción del compromiso** → **Registrar compromiso**. Si el saldo cae bajo el 120 % del SME el compromiso queda
**suspendido** hasta el botón **Levantar suspensión del compromiso n.º …** (autorización escrita de la GG).

**Jerarquía de pagos en restricción de liquidez:** escriba **Saldo disponible para el lote (Bs)** y pulse **Ordenar
lote**: el sistema ordena las cuentas por pagar según la prioridad POL-04 (sueldos y aportes, insumos críticos,
proveedores, etc.).

**Pago de excedentes a socios:** **Monto total (Bs)**, **Fecha de pago**, **Aprobación de la Asamblea**,
**Autorización del desembolso**, **Cuenta**, **Medio** → **Pagar excedentes** (normalmente se ejecuta desde
Cierres → Excedentes, sección 5.14).

#### 6.1.3 SME y flujo de caja

* **Reporte diario de saldo (FIN-TES-06):** **Desde/Hasta**; muestra saldo diario frente al SME (Bs 136.413) y al
  umbral del 120 %. La GG pulsa **Registrar el monitoreo de hoy (Gerencia General)**.
* **Flujo de caja proyectado a dos ciclos (FIN-TES-05):** **Fecha de elaboración**, **Inicio del ciclo actual**,
  **Saldo inicial (Bs)**, tabla editable de ingresos y egresos por semana, **Descripción** → **Guardar flujo**. La GG
  revisa y pulsa **Aprobar flujo (Gerencia General)**. Si el saldo proyectado cae bajo el SME aparece **Emitir alerta
  FIN-TES-07**.
* **Comparar con el saldo real:** **Fecha de corte** → **Comparar con el saldo real**. Un desvío mayor al **10 %**
  (`desvio_flujo_caja_alerta`) se marca en rojo.
* **Alertas de liquidez y medidas correctivas (FIN-TES-07 / 08):** el CV pulsa **Fiscalizar (CV)**; la GG registra
  **Medidas (POL-04 Art. 4)**, **Fecha de aplicación** y **Resultado obtenido** → **Registrar medida**.

#### 6.1.4 Conciliación bancaria (plazo: 5 días hábiles)

1. **Cuenta**, **Año**, **Mes**, **Saldo del extracto (Bs)**, **Fecha de elaboración** → **Preparar conciliación**.
2. Revise las partidas conciliatorias (depósitos en tránsito, cheques pendientes, cargos y abonos del banco no
   registrados).
3. Si hay cargos/abonos bancarios: **Registrar en libros los cargos y abonos bancarios** (genera el asiento).
4. GG: **Firmar conciliación (Gerencia General)**. Sin conciliación firmada no se cierra el mes.

#### 6.1.5 Formatos de Tesorería

**Formatos** → **Formato** (FIN-TES-01 a FIN-TES-08, INF-CV-03, reposición, conciliación) → elija el registro
(**Flujo**, **Alerta**, **Arqueo**, **Conciliación**, **Reposición**, o **Desde/Hasta**) → **Descargar <código> en PDF**.

---

### 6.2 Compras e inventarios · Ciclo 2 (PR-FIN-03, CI-FIN-03, CI-FIN-16)

Ruta: **Ciclo 2 · Compras e inventarios**. Pestañas: **Proveedores · Inventarios · Stock y alertas · Órdenes de
compra · Recepción · Pagos · Bajas · Formatos**.

| Regla | Valor vigente |
|---|---|
| Día de emisión de órdenes de compra | **Lunes** (`dia_semana_orden_compra` = 1) |
| Día del control de stock | **Viernes** (`dia_semana_control_stock` = 5) |
| Cotizaciones obligatorias | **3** cuando la compra supera **Bs 5.000** |
| Autorización de la OC | Siempre la GG (MOF §3.2) |
| Valoración del inventario | Costo promedio ponderado perpetuo (PC-07) |

#### 6.2.1 Proveedores

**Tipo** («Empresa (emite factura)» o «Pequeño productor (recibo con C.I.)») → **Nombre o razón social**, **NIT (empresa)** o **C.I. (pequeño
productor)**, **Comunidad**, **Hectáreas cultivadas**, **Producto**, **Certificado** («Certificado de Inscripción RAU (con último pago)», «Certificado de No Imponibilidad RAU» o «— Ninguno —»),
**N.º de certificado**, **Vigente hasta**, **Copia del último pago RAU (referencia)** → **Registrar**.
Para actualizar o dar de baja: **Proveedor** → campos → **Dar de baja al proveedor** (opcional) → **Guardar**.
Un proveedor con certificado vencido aparece en rojo y no se puede usar en nuevas órdenes.

#### 6.2.2 Inventarios (kárdex)

* **Fecha de consulta**: existencias y CPP de cada insumo.
* **Kárdex por insumo**: entradas, salidas y saldo valorado.
* **Registrar salida**: **Insumo**, **Fecha**, **Cantidad**, **Centro de costo**, **Documento (bitácora FIN-TRA-02)**.
* **Punto de reorden**: **Insumo crítico**, **Inicio de vigencia (inicio del ciclo)**, **Días de reposición del
  proveedor**, **Consumo diario proyectado** → **Calcular y registrar**; o **Recalcular los puntos de reorden del ciclo
  con el consumo del ciclo anterior**.

#### 6.2.3 Stock y alertas (control de los viernes)

1. UT: **Acta de control de stock semanal (FIN-EGR-01)** → **Viernes del control** → existencia física de cada
   insumo → **Observaciones (humedad, estado de silos)** → **Registrar acta**. Si no es viernes, el sistema lo rechaza.
2. Otra unidad: **Acta por verificar** → **Verificar acta (otra unidad)**.
3. **Monitorear stock hoy (cualquier día, Art. 8)**: genera alertas cuando un insumo crítico baja de su punto de reorden.
4. **Compra de emergencia (CI-FIN-16):** sobre una **Alerta**, la GG escribe **Constancia** y **Justificación** →
   **Autorizar emergencia**. Esa alerta habilita una OC fuera del lunes.
5. **Rotación de granos (PC-07):** **Alerta de rotación** → **Explicación del cierre** → **Cerrar alerta de rotación**.

#### 6.2.4 Órdenes de compra (solo los lunes)

1. JF, **Preparar orden** (formulario): **Compra de emergencia** (si la hay), **Fecha programada** (lunes),
   **Proveedor**, **Líneas** → por cada línea **Insumo**, **Cantidad**, **Precio unitario (Bs)**; **Cotización 1..3**
   (proveedor, **Monto (Bs)**, **Documento**), **Condición de pago**, **Días de crédito pactado**, **Destino**,
   **Justificación si no se elige la cotización más baja** → **Preparar orden y solicitar autorización**.
2. Revise **Disponibilidad presupuestaria del ciclo (FIN-EGR-02)** en la cabecera.
3. GG: **Orden** → **Firmar autorización de compra**.
4. JF: **Confirmar compromiso autorizado** (control del 120 % del SME).
5. JF: **Fecha de emisión** → **Emitir orden** (FIN-EGR-03).
6. Para cancelar: **Motivo** → **Anular orden** o **Cerrar con faltante**.

#### 6.2.5 Recepción

1. UT: **Orden emitida**, **Fecha de recepción**, **Factura o recibo del proveedor**; por línea **Recibida**,
   **Calidad conforme**, **Vencimiento**, **Observación**; **Observaciones generales (humedad, pureza, pesaje)** →
   **Registrar recepción** (FIN-EGR-04).
2. JF: **Recepción por contabilizar** → **Contabilizar (inventario contra cuenta por pagar)**: TRASPASO
   1.1.03.xx contra 2.1.01.xx y recálculo del CPP.

#### 6.2.6 Pagos a proveedores

1. JF: **Preparar programación de pagos (FIN-EGR-05)** (ordena por vencimiento y jerarquía POL-04).
2. GG: **Programación** → **Autorizar programación**.
3. JF: **Cuenta a pagar**, **Fecha de pago**, **Cuenta bancaria**, **N.º de cheque**, **Autorización DESEMBOLSO (…)**
   → **Pagar** (EGRESO).

#### 6.2.7 Bajas de inventario

**Insumo**, **Cantidad**, **Causa**, **Descripción**, **Informe técnico de respaldo** → **Proponer baja** → GG
**Acta de baja firmada** → **Autorizar baja** → CV **Registrar visto del CV** → JF **Contabilizar baja**.

---

### 6.3 Personal y planillas · Ciclo 2 (PC-09, POL-06, CI-FIN-04, CI-FIN-14)

Ruta: **Ciclo 2 · Personal y planillas**. Pestañas: **Personal · Planilla mensual · Hojas de tiempo · Personal
eventual · Liquidación final · Alertas · Formatos**.

| Regla | Valor vigente |
|---|---|
| Aporte laboral (retención) | 12,71 % |
| Aporte patronal | 17,21 % |
| Aguinaldo e indemnización | 1/12 cada uno por mes (8,33 %) |
| Salario del Encargado de Granja | Bs 3.300 mensuales |
| Contratación de eventuales | Solo los **lunes**, autorizada por la GG |
| Jornal eventual | Bs 1,50 por ave faenada |

#### 6.3.1 Personal

**Registrar**: **Nombre completo**, **C.I.**, **Cargo**, **Área**, **Polivalente (carne y alimento)**, **Salario
mensual (Bs)**, **Fecha de ingreso**, **Tipo de contrato**, **Referencia del contrato**, **NUA (Gestora Pública)**,
**Matrícula CNS**. **Guardar salario** para indexaciones (con **Vigente desde** y **Motivo**). **Registrar retiro** con
**Fecha de retiro** y **Motivo** (habilita la liquidación final).

#### 6.3.2 Planilla mensual

1. JF: **Año**, **Mes**, **Bonos de <trabajador> (Bs)** → **Registrar planilla** (calcula líquido, aportes,
   aguinaldo e indemnización por doceavos).
2. GG: **Planilla** → **Autorizar planilla**.
3. JF: **Contabilizar (asiento de devengo)**: sueldos de granja al centro 6.1.01.01 con sufijos 20/21/38/39;
   administración a 7.1.01.xx; pasivos 2.1.02.xx y 2.2.01.01.
4. JF: **Fecha**, **Cuenta** → **Pagar sueldos**; y **Pagar aportes** (Gestora y CNS).
5. Si hubo error antes de contabilizar: **Motivo de anulación** → **Anular planilla**.

> **Corte de MOD del ciclo:** al cerrar un ciclo, la MOD fija se corta al día del cierre (días × salario × 12 / 365)
> y se revierte al mes siguiente. Si la reversión está pendiente, el cierre mensual lo indica.

#### 6.3.3 Hojas de tiempo (FIN-EGR-12/13, CI-FIN-14)

* **Registrar hoja (FIN-EGR-12)**: **Trabajador**, **Día**, **Horas crianza/faenado**, **Horas planta de alimento**,
  **Firmada por el trabajador**. Hojas sin firma: **Constancia de la omisión** → **Regularizar hoja**.
* **Conciliación pagado vs absorbido (FIN-EGR-13)**: **Planilla por conciliar**, **Observación CI-FIN-14** →
  **Conciliar**; el CV **Fiscalizar (Consejo de Vigilancia)**.
* **Verificación cruzada de horas (CI-FIN-14)**: **Desde/Hasta**, **Mínimo/Máximo h/qq razonable**, **Criterio
  documentado**, semanas y **Quintales ensacados** → **Registrar verificación**.

#### 6.3.4 Personal eventual para el faenado (POL-06, CI-FIN-04)

1. UT emite la circular: **Emisión**, **Faenado (día 73)**, **Lote**, **Aves a procesar**, **Mortalidad acumulada
   (%)** → **Emitir circular**.
2. JF: **Circular** → **Acusar recibo de la circular**.
3. JF: **Trabajadores a contratar** → **Solicitar autorización para el lunes dd/mm/aaaa**.
4. GG (el lunes; si la GG falta, suplente con **Causa de la suplencia (CI-FIN-17)** y **Constancia escrita**) →
   **Autorizar contratación (FIN-EGR-06)**.
5. JF: **Nombre del trabajador eventual**, **C.I.** → **Registrar contrato** (uno por trabajador).
6. Después del faenado: **Aves faenadas por <trabajador>** → **Liquidar** (Bs 1,50 por ave).
7. GG: **Justificación (obligatoria si el DPF es Malo)** → **Autorizar pago de jornales**.
8. JF: **Cuenta** → **Pagar jornales** (sufijo 22 del centro de costo).

#### 6.3.5 Liquidación final

**Trabajador retirado**, **Días de vacación no gozados**, **Fecha de preparación** (si se excede el plazo legal,
**Causa del retraso**) → **Preparar liquidación final** → GG **Autorizar liquidación final** → JF **Contabilizar
liquidación final**.

#### 6.3.6 Alertas

**Fecha de control**: contratos por vencer, aportes impagos, hojas sin firmar, liquidaciones fuera de plazo.

---

### 6.4 Producción y trazabilidad · Ciclo 3 (POL-03, CI-FIN-11, CI-FIN-12)

Ruta: **Ciclo 3 · Producción y trazabilidad**. Pestañas: **Ciclos y galpones · Bitácora diaria · Alertas sanitarias ·
Bajas por mortalidad · Disposición y peso · Faenado · Conciliación y QR · Formatos**.

| Regla | Valor vigente |
|---|---|
| Duración del ciclo | 73 días; faenado no antes del día **70** (salvo faenado anticipado autorizado) |
| Fases | Inicio hasta el día 21; Crecimiento hasta el día 50; Engorde hasta el faenado |
| Densidad máxima | 8 aves/m² |
| Mortalidad | Alerta al **3 %**; anormal sobre el **5 %** acumulado |
| Muestreo de peso | Cada 7 días |
| Aves por ciclo / lote | 3.000 / 1.000 |

#### 6.4.1 Ciclos y galpones

1. **Calendario del ejercicio**: ciclos C1-C5 con fechas de inicio y faenado.
2. **Galpones** (una vez): **Código**, **Nombre**, **Superficie útil (m²)**, **Enlace a las fotografías reales del
   galpón (QR «Vida Digna»)** → **Registrar galpón**. La capacidad = superficie × 8 aves/m².
3. **Apertura del ciclo (FIN-TRA-01)**: **Recepción de pollitos BB** (recepción contabilizada de la OC de pollitos),
   **Aves en <galpón>** (no más que su capacidad), **Certificado sanitario del proveedor** → **Abrir ciclo**. Se crean
   los lotes (uno por galpón) y el día 1 del ciclo.

#### 6.4.2 Bitácora diaria (FIN-TRA-02, CI-FIN-11)

UT, todos los días por lote: **Lote**, **Fecha**, **Alimento consumido (kg)**, **Aves muertas del día**, **Causa
presunta (obligatoria si hubo muertes)**, **Novedad sanitaria**, **Vacuna o tratamiento aplicado**, **Signos de
alerta (postración, secreción, diarrea inusual)**, **Se cumplieron los puntos de control de CI-FIN-11** → **Registrar
día**. Los días no se pueden saltar ni repetir. Revisión semanal: **Semana**, **Observación** → **Revisar semana**.

#### 6.4.3 Alertas sanitarias

**Lote**, **Fecha de detección**, **Tipo**, **Descripción de la señal de alerta**, **Causa de la demora** (si no se
reporta el mismo día) → **Reportar alerta**. La GG: **Alerta**, **Medida**, **Decisión y constancia** → **Registrar
decisión**.

#### 6.4.4 Bajas por mortalidad

* Mortalidad **normal** (≤ 5 % acumulado): se registra desde la bitácora y se reclasifica al sufijo 40 dentro del
  centro de costo.
* Mortalidad **anormal**: **Informe veterinario (obligatorio si es anormal)** → **Registrar baja** → GG **Autorizar
  baja anormal** → JF **Contabilizar en 7.2.01.04** (pérdida fuera del costo del ciclo).

#### 6.4.5 Disposición y peso

* **Disposición sanitaria (FIN-TRA-11)**: **Lote**, **Fecha**, **Aves dispuestas**, **Método** (Entierro sanitario con cal,
  Incineración, Compostaje controlado, Otro método), **Responsable**, **Detalle del método (si es «Otro»)** → **Registrar disposición**;
  la GG **Dar visto (Gerencia General)**.
* **Muestreo semanal de peso (POL-03 Art. 2)**: **Lote**, **Fecha**, **Aves de la muestra**, **Peso total de la
  muestra (kg)** → **Registrar muestreo**. El sistema compara con la curva esperada de la fase.

#### 6.4.6 Faenado (CI-FIN-12)

1. Si hay riesgo sanitario antes del día 70: **Informe veterinario que funda el riesgo sanitario** → **Autorizar
   faenado anticipado** (GG).
2. UT: **Lote**, **Fecha de faenado**, **Aves faenadas**, **Peso vivo total (kg)**, **Peso canal total (kg)**,
   **Carcasas separadas (no comercializables)**, **Supervisor del personal eventual**, **Explicación del faltante de
   aves**, **Observación (obligatoria si se faena después del día 73)** → **Registrar faenado**.
3. El sistema calcula **PMF** (merma de faenado) e **ICA** (conversión alimenticia) del lote.

#### 6.4.7 Conciliación de aves y QR

* **Conciliación de aves (FIN-TRA-06)**: ingresadas = muertas normales + muertas anormales + dispuestas + faenadas.
  Si hay faltante: **Toma de conocimiento del faltante (motivo)** → **Registrar toma de conocimiento**.
* **Concluir ciclo operativo**: cierra la operación del ciclo (requisito para el cierre de costos).
* **Trazabilidad — código QR «Vida Digna»**: **Lote** → **Descargar página de trazabilidad (HTML)** (origen, fechas,
  galpón, sanidad y fotografías del galpón).

---

### 6.5 Costos matriciales · Ciclo 3 (Manual de Costos, PC-03/PC-04/PC-07)

Ruta: **Ciclo 3 · Costos matriciales**. Pestañas: **Consumos y CIF · Mayor por sufijo · Alimento propio · Cierre e
informe · Presupuesto D.4 · Productos terminados · Formatos**. Arriba elija el **Ciclo**.

1. **Consumo de inventario al CPP**: **Insumo**, **Fecha**, **Cantidad**, **Centro**, **Documento de respaldo
   (bitácora, vale de salida)** → **Registrar consumo** (TRASPASO inventario → 6.1.01.01-sufijo).
2. **Valorar alimento (kg → qq)**: **Valorar el alimento de la bitácora hasta** → convierte los kg de la bitácora a
   quintales (46 kg/qq) y los carga al sufijo 02 (u 11 desde 2027) al CPP.
3. **CIF (sufijos 30-37)**: **Sufijo**, **Fecha**, **Monto (Bs)**, **Concepto**, **Contrapartida** (vacío = la del
   Plan de Cuentas), **Documento de respaldo** → **Registrar CIF**. Las depreciaciones (30-33) se cargan solas con la
   depreciación mensual.
4. **Mayor por sufijo**: matriz del centro 6.1.01.01 por elemento (MPD, MOD, CIF) frente al presupuesto.
5. **Alimento propio** (desde 2027): **Registrar producción** (quintales), **Trasladar a 6.1.01.01-11**,
   **Ensaquillar** el excedente para la venta.
6. **Cierre e informe** (día 73, ciclo operativo concluido): JF **Cerrar el ciclo (1.1.03.12 contra 6.1.01.01)** →
   el costo acumulado pasa a productos en proceso/terminados y se calcula el costo por kg. GG **Observación de la
   Gerencia** → **Aprobar el Informe de Costo de Ciclo**.
7. **Presupuesto D.4**: costo presupuestado del ciclo por elemento frente a lo real.
8. **Productos terminados**: **Producto**, **Fecha**, **Cantidad vendida**, **Nota de entrega** → **Registrar costo
   de ventas** (6.1.02.01).

> En la prueba de integración el ciclo 1 cuesta **Bs 116.753,07** (Bs 14,97/kg) frente a **Bs 116.286,28** del POA.
> `# DISCREPANCIA:` la diferencia (+Bs 466,79) proviene de la MOD fija (planillas mensuales completas, +556,79) y
> del eventual sobre aves efectivamente faenadas (2.940 aves, −90).

---

### 6.6 Activos fijos y CAPEX (POL-02, PC-06)

Ruta: **Ciclo 1 y 3 · Activos fijos y CAPEX**. Pestañas: **Libro de activos · Plan CAPEX · Activación · Depreciación ·
Bajas · Verificación física · Informe semestral · Formatos**.

* **Libro de activos**: **Abrir fichas del balance de apertura** (una vez); **Situación al**; **Ficha individual
  (FIN-TRA-09)**.
* **Plan CAPEX**: **Ejercicio**, **Fecha de elaboración**, **Importar el presupuesto D.5** → **Abrir plan**; **Agregar
  al plan** (activo, **Categoría (PC-06)**, costo, proveedor, ciclo, fecha, **Justificación técnica**); **Calcular ROA y
  SME (FIN-TES-02)**; **Presentar al CA**; CA **Registrar resolución**; AG **Ratificar montos del plan**.
  Inversión no prevista: **Solicitar / incorporar** (CA; específica sobre Bs 20.000).
* **Activación (FIN-TRA-08)**: **Línea del plan**, precio, fletes, instalación, **Disponible para su uso**, destino,
  factura, ubicación → **Autorizar activación**. Variación mayor al 15 % y a Bs 10.000 → **Solicitar la nueva
  aprobación de la Asamblea**. Luego **Registrar activo y abrir su ficha**.
* **Depreciación**: **Año**, **Mes** → **Registrar depreciación del mes** (lineal; a CIF 30-33 o gasto).
* **Bajas**: **Emitir acta** → CV **Registrar verificación** → **Contabilizar la baja**.
* **Verificación física** (cada 12 meses): **Registrar acta INF-CV-01**.
* **Informe semestral**: **Presentar informe semestral** al CA.

---

### 6.7 Ventas, HORECA y cobranza · Ciclo 4 (POL-05, CI-FIN-06/13/15, PC-08)

Ruta: **Ciclo 4 · Ventas, HORECA y cobranza**. Pestañas: **Clientes · Tarifario · Pre-venta del lote · Notas de
entrega · Cartera y cobranza · Recaudación y devoluciones · Margen por canal · Formatos**.

| Regla | Valor vigente |
|---|---|
| Precio de referencia carne | Bs 23,50/kg |
| Gallinaza | Bs 500 por carga |
| Crédito HORECA máximo | 1 día |
| Pre-venta | Mínimo 3 días antes del faenado y al menos 2 friales |
| Suspensión de crédito | 2 incumplimientos |
| Margen bruto mínimo por canal | 20 % |

1. **Clientes**: **Tipo** (HORECA, Frial, Mercado popular / comprador habitual, Agricultor (gallinaza), Otro comprador), **Nombre o razón social**, **NIT /
   C.I.**, **Contacto**, **Teléfono**, **Fecha de alta**, **Dirección**, **Es socio de la cooperativa** → **Registrar
   cliente**.
2. **Tarifario**: **Vigente desde**, **Motivo**, precio por producto y canal → **Proponer al Consejo de
   Administración**; CA **Decisión**, **Acta** → **Registrar resolución**. Solo rige el tarifario aprobado.
3. **Pre-venta del lote (CI-FIN-13)**:
   * **Lote**, **Faenado estimado**, **Aves** → **Abrir despacho**.
   * **Despacho**, **Cliente**, **Aves**, **Fecha del pedido**, **Producto** → **Registrar pedido**.
   * **Fecha de verificación** → **Verificar cobertura (Art. 8)** (pedidos ≥ aves, mínimo 2 friales, ≥ 3 días antes).
   * Si falta cobertura: **Motivo** → **Activar canal de respaldo (CI-FIN-13 Art. 5)** (GG).
   * Después de emitir las notas: **Cerrar despacho del lote**.
4. **Notas de entrega (FIN-ING-04)**: **Cliente**, **Producto**, **Cantidad (kg / qq / cargas)**, **Fecha**, **Hora
   de entrega**, **Medio de pago**, **Días de crédito (HORECA)**, **Despacho del lote (carne)**, **Comprobante**,
   **Certificado RAU exhibido (número)**, casilla **Se exhibió el Certificado de Inscripción al RAU y el comprobante del
   último pago** (obligatoria, DS 24463), **Recibido conforme por**, **Entregado por (Unidad Técnica)** → **Registrar
   nota de entrega** (INGRESO o cuenta por cobrar). Luego **Contabilizar costos pendientes al** → **Registrar costo**.
5. **Cartera y cobranza**: **Revisar y reportar vencidos** (diario); **Registrar cobro**; **Firmar circular
   CIR-ING-01** (suspensión); **Restablecer línea de crédito**; **Autorizar crédito** (GG, máximo 1 día);
   **Registrar ajuste de provisión**.
6. **Recaudación y devoluciones**: **Fecha del parte diario (FIN-ING-07)**, **Boleta de depósito del efectivo del
   día** → **Registrar parte y depósito**; otra unidad **Verificar depósito**. **Devolución por rechazo de inocuidad
   (PC-08)**: **Nota**, **Cantidad rechazada**, **Fecha**, **Motivo del rechazo**, **Informe CI-FIN-12** → **Registrar
   devolución**.
7. **Margen por canal**: **Año**, **Mes** → **Registrar análisis FIN-ING-08**. Un canal bajo el 20 % se marca en rojo.

---

### 6.8 Cierre de costos y cierre del período (resumen de la secuencia)

| Paso | Dónde | Quién |
|---|---|---|
| 1. Concluir ciclo operativo | Producción → Conciliación y QR | UT |
| 2. Registrar costo de las notas | Ventas → Notas de entrega | JF |
| 3. Cerrar el ciclo y aprobar el informe | Costos → Cierre e informe | JF / GG |
| 4. Depreciación del mes | Activos → Depreciación | JF |
| 5. Conciliación bancaria firmada | Tesorería → Conciliación | JF / GG |
| 6. Reposición de caja | Tesorería → Caja | JF |
| 7. FIN-INF-01 y cierre del mes | Cierres → Cierre mensual | JF / GG |
| 8. INF-CV-07 y FIN-INF-05 | Vigilancia → CI-FIN-18 | CV / GG |

---

## 7. RAU, cumplimiento legal y archivo

Ruta: **Transversal · RAU, cumplimiento y archivo**. Pestañas: **Calendario fiscal · Cuota y Form. 701 · Superficie
y CAPEX · Alimento I-1/I-2 · Compradores y terceros · Revisión CV · Expediente EXP-RAU · Fiscalización · Archivo
contable**.

| Regla (PET-RAU 2026-2030, POL-07) | Valor vigente |
|---|---|
| Cuota RAU por hectárea | Bs 3,03 (`rau_cuota_por_ha`) |
| Hectáreas declaradas | 8 (`rau_hectareas`) → cuota anual Bs 24,24 |
| Vencimiento del Form. 701 | 31 de octubre |
| Anticipación de la preparación | 15 días hábiles |
| Alertas de vencimiento | 60 y 30 días antes |
| Completitud mínima del expediente EXP-RAU | 95 % |
| I-1 Autoconsumo del alimento propio | ≥ 65 % de la producción (desde 2027) |
| I-2 Ventas de alimento / ingresos operativos | ≤ 15 % (desde 2027) |

### 7.1 Calendario fiscal

La parte superior muestra el **calendario de obligaciones** del ejercicio con semáforo (verde a tiempo, amarillo
dentro de 60/30 días, rojo vencido): consulta de la RND anual, liquidación de la cuota, Form. 701, patentes
municipales, revisiones trimestrales del CV, verificación anual.

1. **Consulta de la RND / cuota**: **Fecha de la consulta**, **RND consultada**, **Cuota por ha confirmada (Bs)** →
   **Registrar consulta**. Si la cuota confirmada difiere del parámetro, el sistema avisa para proponer el cambio de
   parámetro (sección 11.1).
2. **Certificado RAU**: **Número del certificado**, **Fecha de emisión** → **Registrar certificado**. El número
   pasa al membrete de los PDF contables y a la verificación de las notas de entrega.
3. **Acta del CA**: **Fecha del acta**, **Acta del CA**, **Resultado de la revisión** → **Registrar acta**.

### 7.2 Cuota por hectárea y Formulario 701

1. **Liquidación**: **Fecha de la liquidación** (el sistema calcula hectáreas × cuota = Bs 24,24) y, si se desea,
   **Registrar el asiento de devengo** con **Fecha del devengo (dentro de la gestión, período abierto)** → **Registrar
   liquidación**.
2. **Devengo** por separado: **Fecha del devengo** → **Registrar devengo**.
3. **Comunicación a los socios**: **Fecha de comunicación a los socios** → **Registrar comunicación**.
4. **Formulario 701 V.3**: **Fecha de presentación y pago**, **N.º de orden del formulario**, **Comprobante de pago** →
   **Registrar presentación y pago** (EGRESO del pasivo devengado). Después del 31-oct el semáforo queda rojo.
5. **Patentes municipales**: **Municipio**, **Fecha de pago**, **Monto (Bs)**, **Comprobante** → **Registrar pago de
   patentes**.

### 7.3 Superficie y CAPEX

* **Superficie declarada**: **Fecha de la constancia**, **Hectáreas declaradas**, **Zona / subzona**, **Constancia
  del SIN** → **Registrar superficie declarada**.
* **Superficie usada**: **Componente (galpones, planta, depósitos…)**, **Hectáreas ocupadas**, **Desde** →
  **Registrar superficie usada**. Si la usada supera la declarada aparece alerta roja.
* **Evaluación de líneas CAPEX**: **Línea CAPEX**, **Hectáreas adicionales que ocupará**, **Descripción** →
  **Evaluar y registrar** (indica si la inversión obliga a actualizar la superficie ante el SIN).

### 7.4 Alimento propio: indicadores I-1 / I-2 y balance de masas

1. **Simulación de indicadores**: **Producción anual (qq)**, **Autoconsumo (qq)**, **Ventas de alimento (Bs)**,
   **Ingresos operativos (Bs)** → la pantalla muestra I-1 = autoconsumo ÷ producción e I-2 = ventas de alimento ÷
   ingresos operativos, con semáforo frente al 65 % y al 15 %.
2. **Programa anual de excedente**: **Tope del ejercicio (qq)**, **Canal socios**, **Canal no socios autorizados
   (productores locales)**, **Acta del Consejo de Administración** → **Aprobar programa**.
3. **Balance de masas trimestral (FIN-TRA-13)**: **Trimestre**, **Merma del trimestre (qq)**, **Consumo proyectado del
   período siguiente (qq)** → **Preparar balance** (producción = autoconsumo + ventas + merma + existencias). La GG:
   **Balance**, **Excedente autorizado para la venta (qq)** → **Firmar y autorizar**. Ninguna venta de alimento supera
   el excedente autorizado.

### 7.5 Compradores y terceros

* **Padrón de compradores de alimento**: **Cliente agricultor**, **Comunidad**, **Actividad agropecuaria** →
  **Autorizar**; **Revocar** con **Motivo**.
* **Servicios a terceros (alerta roja con cualquier caso)**: el RAU prohíbe prestar servicios a terceros. Cualquier
  caso se registra (**Fecha**, **Tipo**, **Tercero**, **Descripción** → **Registrar (alerta roja)**), la GG **Tomar
  conocimiento (Gerencia General)** y el CA **Resolver** con **Acta** y **Medida de contención**.

### 7.6 Revisión del Consejo de Vigilancia (INF-CV-09)

* Trimestral: **Año**, **Trimestre calendario**, **% compras sin respaldo (muestra)**, **Atraso del padrón/actas
  (trimestres)**, **Observaciones del Consejo de Vigilancia** → **Registrar revisión trimestral (INF-CV-09)**.
* Anual: **Registrar INF-CV-09 anual**. La tabla **Revisiones registradas** queda como evidencia.

### 7.7 Expediente EXP-RAU (secciones A a G)

**Documento** (de la lista del PET-RAU), **Fecha del documento**, **Referencia**, **Ubicación física** → **Indexar**.
El indicador de completitud debe ser ≥ 95 %; los documentos faltantes aparecen en rojo.

### 7.8 Fiscalización (plan de respuesta del PET-RAU)

1. **Registrar y abrir carpeta del caso**: **Fecha de recepción**, **Entidad** (SIN, municipio…), **Tipo**, **Número de
   orden o requerimiento**, **Funcionario actuante**, **Gestiones alcanzadas**, **Vencimiento del plazo**,
   **Escenario**.
2. **Registrar paso**: **Caso**, **Paso** (recepción, designación, recopilación, entrega, descargos, prórroga…),
   **Fecha**, **Detalle**, **Documento / acta / cargo de entrega**, **Nuevo vencimiento (solo prórroga)**.
3. **Cerrar caso** con **Lecciones aprendidas**.
4. **Buzón**: revisión periódica del buzón tributario (**Fecha de revisión**, **Hubo novedades**, **Detalle**) →
   **Registrar revisión**.

### 7.9 Archivo contable (MAN-ARC-01)

| Regla | Valor vigente |
|---|---|
| Retención | 10 años (T10, Código de Comercio) o 5 años (T5, Ley 2492 Arts. 59-60) según la tabla de retención de 72 documentos; documentos de apoyo desechables a los 2 años; respaldos de activos: vida útil del bien + 10 años |
| Folios por expediente | Máximo 200 |
| Préstamo de originales | Requiere la GG |

1. **Expedientes**: **Documento de la tabla de retención**, **Gestión**, **Título del expediente** → **Abrir**.
2. **Agregar documento**: **Expediente**, **Código del formato**, **Fecha**, **Folios**, **Estado**, **Descripción** →
   **Agregar documento** (el sistema folia correlativamente y avisa al llegar a 200).
3. **Cerrar** el expediente: **Fecha de cierre**, opcional **Indicar fecha base** (sustitución, vigencia o baja del
   bien), **Caja (CAJ-AAAA-NNN)** → **Cerrar**. Desde la fecha de cierre corre el plazo de retención.
4. **Libro de préstamo**: **Expediente**, **Solicitante**, **Cargo**, **Motivo**, **Préstamo de originales**,
   **Devolución prevista** → **Registrar préstamo**; al volver, **Registrar devolución**. Los vencidos se marcan en rojo.
5. **Actas de transferencia y destrucción**: **Tipo de acta**, **Expedientes elegibles** (solo los que cumplieron su
   plazo), **Método**, **Observación** → **Preparar acta** → GG **Autorizar acta** → CV **Visar acta** → JF **Ejecutar
   acta**.

---

## 8. Presupuesto, POA y Tablero de indicadores

### 8.1 Presupuesto Maestro, ejecución y POA

Ruta: **Información gerencial · Presupuesto y POA**. Pestañas: **Presupuesto Maestro · Por ciclo (FIN-TES-10) ·
Calendario · Ejecución (FIN-TES-12) · Modificaciones (FIN-TES-11) · POA · Flujo POA vs real · Alimento propio (D.6)**.

#### 8.1.1 Carga del presupuesto

El Presupuesto Maestro 2026-2030 (Manual Presupuestario, módulos A a E) y el presupuesto por ciclo se cargan con las
semillas al crear la base; los datos del POA con el botón **Cargar datos del POA 2026-2027** (pestaña **Flujo POA vs
real**) o con `python -m scripts.cargar_datos_poa`. La carga es idempotente: repetirla no duplica datos.

* **Presupuesto Maestro**: **Módulo del Plan Maestro** (supuestos, ingresos, costos, CAPEX, EEFF proyectados) →
  tabla por ejercicio 2026-2030; **Preparar Excel del Presupuesto Maestro** → descarga.
* **Por ciclo (FIN-TES-10)**: **Ciclo** → **FIN-TES-09 · Hoja de supuestos** (aves, mortalidad, kg, precios) y el
  presupuesto de ingresos, costos por elemento y flujo del ciclo; descarga en PDF y Excel.
* **Alimento propio (D.6)**: presupuesto de la planta de alimento (desde 2027).

#### 8.1.2 Calendario presupuestario (Manual Presupuestario §2.2)

| Hito | Fecha límite | Responsable |
|---|---|---|
| Validar supuestos (FIN-TES-09) | 30-abr | JF |
| Proyecto de presupuesto (FIN-TES-10) | 15-may | JF |
| Revisión del Comité de Presupuesto | 24-may | GG |
| Plan CAPEX con ROA y SME | 31-may | GG |
| Aprobación del Consejo de Administración | 15-jun | CA |
| Verificación del Consejo de Vigilancia | 25-jun | CV |
| Comunicación del presupuesto (CIR-TES-01) | 01-jul | GG |

En **Calendario**, el sistema muestra el siguiente hito pendiente; complete **Fecha** y **Referencia (<documento>)** y
pulse **Registrar hito**. Los hitos vencidos se marcan en rojo.

#### 8.1.3 Ejecución presupuestaria (FIN-TES-12) y semáforos

1. **Informe**: Ciclo, Semestre o Año; elija **Ciclo** o **Semestre**; opcional **Kg vendidos reales (opcional, para
   precio y volumen)**.
2. La tabla compara presupuesto vs real por partida con la **variación en Bs y %** y el semáforo:

   | Partida | Verde | Amarillo | Rojo |
   |---|---|---|---|
   | Ingresos | desvío ≥ −5 % | entre −5 % y −10 % | peor que −10 % |
   | Costos y gastos | desvío ≤ +5 % | entre +5 % y +10 % | mayor que +10 % |

   Con kg reales, la variación de ingresos se descompone en **efecto precio** y **efecto volumen**.
3. Para cada partida en amarillo o rojo: **Causa**, **Responsable**, **Acción correctiva**; **Fecha del informe** →
   **Registrar informe**. Plazo: **10 días** después del cierre del ciclo (31 días para el semestral).
4. GG: **Presentar al Consejo de Administración**.

#### 8.1.4 Modificaciones presupuestarias (FIN-TES-11)

1. JF: **Partida**, **Ciclo**, **Monto solicitado (Bs)**, **Motivo y desviación que la origina (FIN-TES-12)**,
   **Fuente de recursos**, **Efecto en el flujo de caja y en el SME** → **Solicitar**.
2. JF/Unidad de Finanzas: **Solicitud por verificar** → **Verificar (Unidad de Finanzas)**. El sistema determina la
   autoridad: **GG** hasta el 10 % de la partida; **CA** sobre ese margen o en partidas sin monto aprobado; **AG**
   cuando la Matriz CI-FIN-09 lo exige (CAPEX con variación > 15 % y > Bs 10.000).
3. Autoridad: **Solicitud a resolver**, **Acta (obligatoria para CA y Asamblea)**, **Observación (obligatoria si se
   rechaza)** → **Aprobar** o **Rechazar**.

#### 8.1.5 POA 2026-2027

* **POA**: 49 actividades por área. **Actividad**, **Trimestre del ejercicio**, **Estado**, **Fecha**, **Evidencia
  (formato o documento)** → **Registrar avance**. Cumplimiento físico: meta **90 %**; bajo **75 %** rojo.
* **Flujo POA vs real**: **Calendario de ciclos (POA §1.1) frente a Producción** (fechas de C1-C5 planificadas vs
  aperturas y faenados reales) y **Flujo de caja mensual (POA §3.2) frente a lo real** (oct-2026 a jun-2027).

### 8.2 Tablero de indicadores

Ruta: **Información gerencial · Tablero de indicadores**. Elija **Ejercicio (julio a junio)**. Pestañas:
**Cuadro de mando (FIN-TES-13) · Tendencias · Informe gerencial (CI-FIN-18)**.

#### 8.2.1 Indicadores y semáforos

| Indicador | Fórmula | Bueno (verde) | Malo (rojo) | Meta documental |
|---|---|---|---|---|
| **ICL** Índice de costo por ventas | Costo de ventas ÷ ventas totales | ≤ 61 % | > 63 % | < 61 % |
| **RINC** Ingresos no cárnicos | (Alimento + gallinaza) ÷ ventas totales | ≥ 15 % | < 5 % | ≥ 15 % (2030) |
| **ROA** Retorno sobre activos | Excedente neto ÷ activos totales | ≥ 18 % | < 10 % | > 18 % |
| **Margen operativo** | EBIT ÷ ingresos operativos | ≥ 30 % | < 20 % | > 20 % |
| **SME** Cumplimiento del saldo mínimo | Saldo de efectivo al inicio del ciclo 1 (tras pagar excedentes) ÷ SME (Bs 136.413) | ≥ 150 % | < 100 % | ≥ 100 % |
| **ICA** Conversión alimenticia | kg de alimento ÷ kg de carne viva | ≤ 2,00 | > 2,10 | < 2,00 |
| **PMF** Merma de faenado | (Peso vivo − peso canal) ÷ peso vivo | ≤ 10 % | > 20 % | < 10 % |
| **DPF** Desviación del presupuesto de faenado | Jornales reales ÷ presupuesto | ≤ 100 % | > 105 % | ≤ 100 % |
| **CAC** Calendario de abastecimiento crítico | OC puntuales ÷ OC programadas | 100 % | < 90 % | 100 % |
| **Mortalidad** acumulada | Aves muertas ÷ aves iniciales | ≤ 3 % | > 5 % | ≤ 3 % |
| **Margen bruto por canal** | (Venta − costo) ÷ venta por canal (HORECA, Frial, Mercado popular) | ≥ 20 % | < 20 % | ≥ 20 % |

Entre los límites «bueno» y «malo» el indicador queda **NORMAL (amarillo)**. Ejemplo de la prueba de integración:
ICL del ciclo 1 = 116.753,07 ÷ 184.800 = **63,18 %** → ROJO (MALO): exige acción en el FIN-INF-05.

#### 8.2.2 Cómo leer el tablero

* **Cuadro de mando (FIN-TES-13)**: una tarjeta por indicador con valor, semáforo, fórmula y meta; tabla **Margen
  bruto por canal**; **Descargar FIN-TES-13 (PDF)**.
* **Tendencias**: **Por ciclo** (ventas, ICL, margen operativo, ICA, PMF, mortalidad, SME de cada ciclo) y **Por
  ejercicio — real frente a proyectado (2026-2030)** (ICL, RINC, ROA, margen operativo y SME frente a los EEFF
  proyectados).
* **Informe gerencial (CI-FIN-18)**: lista los FIN-INF-05 emitidos por mes, con su verificación previa INF-CV-07;
  **Descargar FIN-INF-05 <período> (PDF)**. Un indicador del mes **no se muestra como oficial** hasta que el CV emite
  el INF-CV-07 (sección 9.2).

---

## 9. Consejo de Vigilancia

Ruta: **Control interno · Consejo de Vigilancia**. Elija **Ejercicio (julio a junio)**. Pestañas: **Panel · CI-FIN-18
(INF-CV-07 / FIN-INF-05) · Arqueos e inventario · Activos (INF-CV-01) · Desembolsos · Informes INF-CV**.

> **Principio de independencia.** El CV **no registra operaciones**, no reversa asientos, no autoriza ni aprueba. Su
> rol es de solo lectura sobre toda la contabilidad y los ciclos, y solo escribe sus propios informes, verificaciones,
> la cofirma de desembolsos mayores a Bs 5.000 y el visto de las actas de restauración y de archivo.

| Obligación del CV | Frecuencia (parámetro) |
|---|---|
| Arqueos sorpresivos de caja (INF-CV-03) | 2 por año |
| Inventario físico sorpresivo | 1 por año |
| Verificación física de activos (INF-CV-01) | Cada 12 meses |
| Verificación previa de indicadores (INF-CV-07) | Mensual, antes del FIN-INF-05 |
| Informe de cumplimiento (INF-CV-02) | 4 por año (trimestral) |
| Informe de precios de mercado (INF-CV-06) | 2 por año |
| Laborales y aportes (INF-CV-05) | Anual |
| Dictamen de los EEFF (INF-CV-08) | Anual, antes de la Asamblea |
| Distribución de excedentes (INF-CV-04) | Anual |
| RAU (INF-CV-09) | Trimestral y anual |

### 9.1 Panel

Resumen del ejercicio con semáforos: arqueos e inventarios realizados frente a los exigidos, INF-CV pendientes por
trimestre, desembolsos pendientes de verificar, meses con INF-CV-07 pendiente y alertas abiertas de liquidez, RAU y
archivo.

### 9.2 Flujo de bloqueo CI-FIN-18 (INF-CV-07 → FIN-INF-05)

El objetivo del CI-FIN-18 es que **ningún indicador del mes se comunique como definitivo sin la verificación previa
e independiente del CV**. El sistema lo impone con estas reglas:

```
 Cierre del mes          Verificación previa del CV           Comunicación de la GG
 ─────────────           ──────────────────────────           ─────────────────────
 FIN-INF-01 sin    ──►   INF-CV-07 (≥ 3 partidas,     ──►     FIN-INF-05
 pendientes              recálculo, razonabilidad)            (acción obligatoria si un
                                                              indicador está en «Malo»)
      ✗ sin FIN-INF-01 → SIN_FIN_INF_01            ✗ sin INF-CV-07 → SIN_INF_CV_07
      ✗ pendientes → PENDIENTES_CIERRE             ✗ observaciones no incorporadas → OBSERVACIONES_SIN_INCORPORAR
      ✗ < 3 partidas → MUESTRA_INSUFICIENTE        ✗ «Malo» sin acción → FALTA_DECISION
      ✗ FIN-INF-05 ya emitido → SECUENCIA_INVERTIDA
```

**Paso a paso del CV (INF-CV-07):**

1. Pestaña **CI-FIN-18 (INF-CV-07 / FIN-INF-05)** → **Mes a fiscalizar** (solo meses con FIN-INF-01 registrada).
2. Revise los datos que el sistema presenta: FIN-INF-03 (consolidado de costeo), FIN-INF-04 (resultados) y el
   **recálculo independiente** de los indicadores con su indicación «coincide / no coincide».
3. **Partidas significativas muestreadas (mínimo 3)**: elija al menos tres partidas del FIN-INF-03; opcional **Otra
   partida muestreada**.
4. Marque las tres conclusiones: **Partidas muestreadas con respaldo razonable**, **Recálculo independiente
   coincide**, **Margen bruto razonable frente al mes anterior y al presupuesto**.
5. Si alguna no se marca, **Observaciones** es obligatoria (dictamen «CON_OBSERVACIONES»).
6. **Emitir INF-CV-07**. Solo puede emitirse después del último día del mes.

**Paso a paso de la GG (FIN-INF-05):**

1. **FIN-INF-05 a emitir**: solo aparecen los meses con INF-CV-07.
2. Para cada indicador en «Malo»: **Acción / decisión sobre el <indicador> (obligatoria si está en «Malo»)**.
3. Si el INF-CV-07 tuvo observaciones: **Observaciones del INF-CV-07 incorporadas (si las hubo)**.
4. **Emitir FIN-INF-05**. Desde ese momento el informe aparece en **Tablero → Informe gerencial (CI-FIN-18)**.

> «Aunque ello retrase el informe» (CI-FIN-18): si el CV no emitió el INF-CV-07, la GG **no puede** emitir el
> FIN-INF-05; el sistema no ofrece atajos.

### 9.3 Arqueos e inventario

* **Arqueos sorpresivos de caja (INF-CV-03)**: **Efectivo físico contado (Bs)**, **Comprobantes de gasto no
  repuestos (Bs)**, **Custodio presente**, **Observaciones** → **Registrar arqueo**. El sistema compara efectivo +
  comprobantes con el fondo fijo (Bs 5.000) y declara sobrante o faltante.
* **Inventario físico sorpresivo**: **Fecha del conteo** → tabla editable con la existencia según kárdex y la
  columna **Contar** (cantidad física) → **Observación del inventario** → **Registrar inventario sorpresivo**. Las
  diferencias se muestran valoradas al CPP.

### 9.4 Activos (INF-CV-01)

Consulte la situación de las verificaciones físicas (la verificación se registra en **Activos fijos y CAPEX →
Verificación física**) y descargue las actas.

### 9.5 Desembolsos

**Desembolso a verificar** (autorizados en el ejercicio) → **Conforme** u **Observación (obligatoria si no es
conforme)** → **Registrar verificación**. El miembro del CV que cofirmó un desembolso no puede verificarlo
(`COFIRMANTE_NO_VERIFICA`).

### 9.6 Informes INF-CV

**Informe** →

| Informe | Campos | Botón |
|---|---|---|
| INF-CV-02 Cumplimiento trimestral | **Trimestre del ejercicio** | **Registrar INF-CV-02**; luego **Registrar reporte a la Asamblea** |
| INF-CV-05 Laborales | **Provisiones constituidas y suficientes**, **Aportes a la Gestora y a la CNS al día**, **Observaciones** | **Registrar INF-CV-05** |
| INF-CV-06 Precios | **Precio de mercado <canal> (Bs/kg)** para HORECA, Frial y Mercado popular | **Registrar INF-CV-06** |
| INF-CV-08 Dictamen de EEFF | — (toma el paquete emitido) | **Registrar INF-CV-08** |

Cada informe registrado se descarga con **Descargar <informe> (PDF)**.

---

## 10. Autorizaciones (matriz CI-FIN-09)

Ruta: **Control interno · Autorizaciones (Matriz CI-FIN-09)**. Pestañas: **Pendientes de mi firma · Nueva solicitud ·
Historial · Suplencias**. La mayoría de las solicitudes nacen solas desde los ciclos (OC, contratación, desembolsos);
esta página sirve para firmar y para las decisiones sin pantalla propia.

### 10.1 Matriz de autorización vigente

| Tipo de decisión | Quién firma | Condición |
|---|---|---|
| Desembolso bancario | GG; **GG + CV** | Más de Bs 1.000; más de Bs 5.000 (firma mancomunada) |
| Compromiso de gasto no recurrente | GG (escrita) | Saldo proyectado bajo el 120 % del SME |
| Compra de insumos (OC) | GG | Siempre; 3 cotizaciones sobre Bs 5.000 |
| Contratación de personal eventual | GG | Solo el lunes; suplencia CI-FIN-17 |
| CAPEX no previsto | CA | Cualquier monto; autorización específica sobre Bs 20.000 |
| Variación de CAPEX | Informar al CA; **AG** | > 15 %; > 15 % y > Bs 10.000 |
| Financiamiento de CAPEX con deuda | AG | Mayoría de 2/3 |
| Crédito HORECA | GG (resolución escrita) | Más de 1 día (hasta el máximo vigente) |
| Canal de respaldo | GG | Pedidos HORECA insuficientes |
| Distribución de excedentes | AG | Con INF-CV-04 previo |
| Capitalización de excedentes | AG | Mayoría de 2/3 |
| Presupuesto de certificaciones | CA | Anual |
| Nueva actividad con impacto RAU | CA | Previa evaluación |
| Ajuste o reversión contable | GG | Todo AJUSTE y toda reversión |
| Otra (no prevista) | Escalamiento por defecto | — |

### 10.2 Solicitar

**Nueva solicitud** → **Tipo de decisión (CI-FIN-09)**, **Fecha de la decisión**, **Monto (Bs)**, **Descripción**,
**Referencia (formato FIN-*, OC, contrato…)** y, según el tipo, **Informar saldo de caja proyectado (control del
120 % del SME)** + **Saldo proyectado después del compromiso (Bs)**, **¿Está en el Plan CAPEX anual aprobado?**,
**Monto CAPEX aprobado originalmente (Bs)**, **Días de crédito**, **Referencia del informe previo del CV (INF-CV-04)** →
**Registrar solicitud**. El sistema muestra las firmas requeridas y el fundamento normativo.

### 10.3 Firmar

**Pendientes de mi firma** → cada solicitud con sus fundamentos → **Decisión** (Aprobar/Rechazar), **Documento escrito
/ acta / resolución** (obligatorio cuando la matriz exige forma escrita), **Observación**; para AG/CA **Votos a
favor** y **Votos emitidos** (mayoría de 2/3 cuando corresponde) → **Firmar**.

**Suplencia de la GG (CI-FIN-17):** el Presidente del CA (orden 1) o el Tesorero del CA (orden 2) marcan **Firmar como
suplente de la Gerencia General (CI-FIN-17, orden n)** e indican **Causa de la suplencia** (y, para el orden 2,
**Motivo por el que no actúa el Presidente del CA**). Luego, en **Suplencias**, se pulsa **Registrar comunicación a
la GG** y la GG **Marcar como revisada (CI-FIN-17 Art. 4)**.

**Historial**: todas las solicitudes con su estado, firmantes y fechas.

---

## 11. Administración del sistema

### 11.1 Parámetros normativos (regla 2)

Ruta: **Administración · Parámetros normativos**. Filtros: **Ejercicio**, **Buscar por clave o descripción**, **Solo
«por validar»**. Pestañas: **Vigentes · Historial · Solicitudes de cambio**.

* **Vigentes**: clave, valor, tipo, fuente documental, vigencia y estado (VALIDADO / POR VALIDAR).
* **Historial**: **Parámetro** → todas sus vigencias (ninguna se borra; la anterior se cierra cuando rige la nueva).
* **Proponer un cambio** (GG): **Parámetro**, **Valor nuevo**, **Rige desde el ejercicio**, **Fuente documental
  (documento, artículo, acta)**, **Motivo** → **Enviar al Consejo de Administración**.
* **Decidir** (CA o AG): **Solicitudes de cambio** → **Solicitud**, **Decisión**, **Acta (obligatoria para aprobar)**,
  **Observación (obligatoria para rechazar)** → **Registrar decisión**. Al aprobar, el valor rige desde el ejercicio
  indicado y queda en la bitácora.

### 11.2 Usuarios (solo GG)

**Nuevo usuario** (sección 3.2) y **Modificar usuario**: **Usuario**, **Rol**, **Activo** → **Guardar cambios**; o
**Nueva contraseña temporal (también desbloquea)** → **Restablecer contraseña**. El sistema impide quitar el último
usuario activo de la GG y asignar roles incompatibles al mismo socio.

### 11.3 Bitácora de auditoría (GG y CV)

Filtros **Tabla**, **Usuario**, **Acción** (INSERT, UPDATE, DELETE, REVERSA, LOGIN, LOGOUT, AUTORIZACION, CIERRE,
CARGA_INICIAL, OTRO), **Desde**,
**Hasta**. Cada fila muestra fecha y hora, usuario, rol, tabla, registro, acción y los valores **antes** y **después**
(JSON). La bitácora es inmutable (disparadores de la base).

### 11.4 Revisión de seguridad (GG y CV)

Ruta: **Administración · Revisión de seguridad**. Controles automáticos con semáforo, agrupados por área:

| Área | Controles |
|---|---|
| Contraseñas | Cambio obligatorio de contraseña (≤ 90 días) · Contraseñas vencidas · Contraseñas temporales sin cambiar · Contraseñas cifradas con bcrypt |
| Acceso | Bloqueo por intentos fallidos · Usuarios bloqueados ahora · Cierre de sesión por inactividad · Registro de accesos |
| Segregación | Consejo de Vigilancia solo lectura · Permisos incompatibles en la matriz · Incompatibilidades del MOF · El Jefe de Finanzas no firma |
| Integridad | Bitácora de auditoría inmutable · Claves foráneas y journal WAL |
| Usuarios | Miembros de los Consejos vinculados a su socio |

Todos deben estar en verde; cada fila indica el detalle y la fuente normativa. **Descargar informe REV-SEG (PDF)**
para el archivo. El estado de los respaldos y de la prueba de restauración se controla en **Respaldos 3-2-1 →
Cumplimiento** (sección 12).

---

## 12. Protocolo de respaldo, mantenimiento y pruebas

### 12.1 La regla 3-2-1 (MAN-ARC-01 §5.2)

| Exigencia | Cómo la cumple el sistema |
|---|---|
| **3 copias** | Cada respaldo se guarda en tres ubicaciones: **LOCAL** (equipo de trabajo, `datos/respaldos`), **EXTERNO** (disco externo) y **FUERA_SITIO** (carpeta fuera de la granja o sincronizada con la nube) |
| **2 medios distintos** | Disco del equipo + disco externo |
| **1 copia fuera del sitio** | Destino FUERA_SITIO |
| Prueba de restauración | Cada **6 meses**, con acta ACTA-RES y visto del CV |

**Tipos de respaldo:**

| Tipo | Cuándo | Contenido | Retención |
|---|---|---|---|
| DIARIO | Todos los días (al abrir el ERP o con el .bat) | Base completa + archivo digital **incremental** | 45 días (valor vigente; la semilla trae 30) |
| SEMANAL | Domingo (`respaldo_dia_semanal` = 7), o el primer día disponible si se omitió | Completo | 8 semanas |
| MENSUAL | Al cerrar cada mes | Completo, referenciado al período | 24 meses |
| ANUAL | Al cerrar el ejercicio | Completo | Permanente |
| MANUAL | A pedido | Completo | Igual que el semanal |

Cada **paquete** `.zip` contiene la instantánea de la base (`pollolandia.db` tomada con la API de respaldo de SQLite,
sin detener el sistema), los documentos del archivo digital y un **manifiesto** con la huella **SHA-256** de cada
archivo y los **conteos de control** (asientos, líneas, Debe y Haber totales, eventos de bitácora, versión del
esquema). El sistema depura solo los respaldos vencidos según la retención; los anuales nunca se depuran.

### 12.2 Configuración de los destinos (GG, una vez)

**Administración · Respaldos 3-2-1 → Destinos** → **Disco externo local (p. ej. E:\RespaldosPollolandia)** y **Copia
fuera de la granja o carpeta sincronizada con la nube** (deben ser carpetas distintas) → **Guardar destinos**. Si un
destino no está disponible al respaldar (disco desconectado), la copia LOCAL se hace igual y el destino ausente queda
como pendiente en **Cumplimiento**.

### 12.3 Respaldo automático con `respaldo_automatico.bat`

1. Ejecute `iniciar_erp.bat` al menos una vez (crea el entorno `.venv`).
2. Programe la tarea diaria en Windows (Símbolo del sistema, desde la carpeta del ERP):

   ```bat
   schtasks /create /tn "Pollolandia respaldo" /tr "C:\Pollolandia\pollolandia_erp\respaldo_automatico.bat" /sc daily /st 19:00
   ```

3. El .bat activa el entorno y ejecuta `python -m scripts.respaldo_automatico`, que hace los respaldos pendientes del
   día (diario, semanal, mensual y anual según corresponda). El registro queda en `datos\logs\respaldo_automatico.log`.
4. Respaldo completo inmediato por consola: `python -m scripts.respaldo_automatico --manual`.
5. Además, cada vez que alguien abre el ERP se ejecuta el respaldo del día si falta.

### 12.4 Pantalla de respaldos

Ruta: **Administración · Respaldos 3-2-1** (GG, JF, CA, CV). Pestañas:

* **Cumplimiento**: semáforos de último diario, último semanal, mensuales/anuales pendientes, copias por ubicación,
  prueba de restauración vigente (≤ 6 meses) y visto del CV.
* **Respaldos**: lista de paquetes (tipo, fecha, referencia, tamaño, SHA-256, ubicaciones, estado). Botones
  **Ejecutar respaldos pendientes de hoy** y **Respaldo completo manual ahora**.
* **Pruebas de restauración** (JF, cada 6 meses):
  1. **Respaldo a restaurar** y **Copia a restaurar** (LOCAL, EXTERNO o FUERA_SITIO; conviene alternar).
  2. **Probar restauración y levantar acta**. El sistema restaura en una carpeta temporal **sin tocar la base en uso**
     y ejecuta 12 verificaciones: el paquete existe; huella SHA-256 igual a la registrada; zip íntegro; contiene base y
     manifiesto; cada archivo coincide con su huella; documentos del archivo restaurados; `PRAGMA integrity_check`;
     `PRAGMA foreign_key_check` sin huérfanos; versión de esquema igual; conteos de control iguales; todos los asientos
     cuadran; Debe total = Haber total.
  3. Se genera el **ACTA-RES** con el resultado (CONFORME / NO CONFORME): **Descargar acta N.º n (PDF)**.
  4. CV: **Acta a verificar**, **Observación del Consejo de Vigilancia** → **Registrar visto**.
* **Destinos**: sección 12.2.

### 12.5 Restauración real de la base (emergencia)

Solo cuando la base en uso está dañada o perdida, con autorización de la GG:

1. Cierre el ERP (cierre la ventana de `iniciar_erp.bat`).
2. Elija el paquete más reciente con acta de restauración CONFORME o verifíquelo primero con una prueba.
3. En la carpeta del ERP, con el entorno activo:

   ```powershell
   .venv\Scripts\Activate.ps1
   python -m scripts.restaurar_respaldo "E:\RespaldosPollolandia\<paquete>.zip" --confirmar
   ```

   `--confirmar` declara que la aplicación está cerrada (sin él, el script no hace nada). El script verifica el
   paquete (huellas SHA-256, integridad, claves foráneas, conteos de control y cuadre de todos los asientos), guarda
   la base actual como `pollolandia.antes_de_restaurar_<marca>.db` y la reemplaza. Muestra cada verificación como
   `[OK]` o `[FALLA]`. `--ruta` permite indicar otra base. Para verificar un paquete sin reemplazar nada, use la
   prueba de restauración de la pantalla (sección 12.4).
4. Abra el ERP; las migraciones se aplican solas si el paquete es de una versión anterior.
5. Los hechos registrados después del respaldo deben volver a registrarse desde sus documentos físicos.

### 12.6 Mantenimiento periódico

| Tarea | Frecuencia | Responsable | Dónde |
|---|---|---|---|
| Verificar que el respaldo diario se hizo | Diaria | JF | Respaldos → Cumplimiento |
| Conectar el disco externo / revisar la carpeta fuera del sitio | Semanal | JF | Explorador de Windows |
| Revisión de seguridad | Mensual | GG / CV | Revisión de seguridad |
| Revisar la bitácora | Mensual | CV | Bitácora de auditoría |
| Prueba de restauración + visto del CV | Cada 6 meses | JF / CV | Respaldos → Pruebas de restauración |
| Cambio de contraseñas | Cada 90 días (forzado) | Todos | Mi cuenta |
| Baja de usuarios que dejan el cargo | Al ocurrir | GG | Usuarios → Modificar usuario |
| Parámetros del nuevo ejercicio (RND, SME, salarios) | Antes del 1-jul | GG / CA | Parámetros normativos |
| Actualizar dependencias | Solo con nueva versión validada | Soporte | `pip install -r requirements.txt` |

### 12.7 Pruebas automáticas (pytest)

**Ejecutar todas** (PowerShell, desde la carpeta del ERP):

```powershell
.venv\Scripts\Activate.ps1
python -m pytest                       # 596 pruebas; resultado esperado: todas en verde
python -m pytest -q -p no:warnings     # salida compacta
python -m pytest pruebas/test_integracion.py -v    # ciclo completo de punta a punta
python -m pytest -k "respaldo or restauracion"     # solo respaldos
python -m pytest --lf                  # repetir solo las que fallaron
```

La configuración está en `pytest.ini` (`testpaths = pruebas`). Cada prueba usa una base temporal en memoria o en una
carpeta temporal: **nunca toca la base de producción**.

**Mapa de pruebas:**

| Archivo | Pruebas | Qué valida |
|---|---:|---|
| `test_db.py` | 78 | Esquema v1-v13, claves foráneas, WAL, semillas, disparadores de inmutabilidad, migraciones |
| `test_humo.py` | 117 | Cada página carga para cada rol (Streamlit AppTest), botones de PDF |
| `test_formato.py` | 58 | Formato Bs 1.234,56, fechas, redondeo ROUND_HALF_UP |
| `test_seguridad.py` | 47 | Login, bcrypt, bloqueo, caducidad, permisos, segregación, matriz CI-FIN-09, suplencias |
| `test_contable.py` | 39 | Partida doble, sufijos, períodos, reversión, libros, balance |
| `test_presupuesto.py` | 34 | Presupuesto Maestro, calendario, ejecución, semáforos, modificaciones |
| `test_tesoreria.py` | 32 | Caja chica, bancos, SME 120 %, flujo, conciliación, jerarquía de pagos |
| `test_rau.py` | 18 | Cuota Bs 3,03/ha, Form. 701, I-1/I-2, balance de masas, expediente, fiscalización |
| `test_compras.py` | 16 | OC del lunes, 3 cotizaciones, recepción, programación de pagos |
| `test_inventarios.py` | 15 | CPP, kárdex, puntos de reorden, control de los viernes |
| `test_planillas.py` | 15 | Aportes 12,71 %/17,21 %, doceavos, eventual Bs 1,50, liquidación |
| `test_produccion.py` | 13 | Ciclo de 73 días, densidad, mortalidad 3 %/5 %, faenado, conciliación de aves |
| `test_activos.py` | 12 | CAPEX, activación, depreciación, bajas |
| `test_cierre.py` | 12 | FIN-INF-01, cierre mensual y anual, EEFF, excedentes, plazos 60/90 |
| `test_costos.py` | 10 | Consumos, CIF, cierre del ciclo, costo Bs/kg |
| `test_ventas.py` | 10 | Pre-venta, notas con RAU, crédito HORECA, devoluciones, margen |
| `test_respaldo.py` | 9 | 3-2-1, incremental, retención, 12 verificaciones, acta, visto del CV |
| `test_archivo.py` / `test_auditoria.py` | 8 / 8 | MAN-ARC-01; bitácora inmutable |
| `test_pdf_contable.py` | 7 | Membrete, pie, comprobantes, diario, mayor, balance 4/8 columnas (excedente Bs 210.293,39), EEFF |
| `test_excepciones.py`, `test_indicadores.py`, `test_poa.py` | 6 c/u | Excepciones propias; ICL/RINC/ROA/ICA/PMF; POA |
| `test_vigilancia.py`, `test_revision_seguridad.py` | 5 c/u | CI-FIN-18 e INF-CV; REV-SEG |
| `test_parametros.py`, `test_formatos_presupuesto.py` | 4 c/u | Cambios de parámetros con acta; formatos FIN-TES |
| `test_integracion.py` | 1 | Ciclo completo: apertura → compra → planilla → bitácora → faenado → venta → costos → cierre → EEFF; costo Bs 116.753,07; ICL 63,18 % |
| `test_scripts.py` | 1 | Scripts de carga del POA, respaldo y restauración |

**Si una prueba falla:** no use el sistema en producción con esa versión; guarde la salida
(`python -m pytest > resultado_pruebas.txt`) y envíela al soporte técnico junto con la versión (`config.py`).

---

## 13. Calendario operativo integrado

| Cuándo | Qué | Quién | Módulo |
|---|---|---|---|
| **Diario** | Bitácora de cada lote | UT | Producción → Bitácora diaria |
| Diario | Monitoreo de saldo (FIN-TES-06) | GG | Tesorería → SME y flujo |
| Diario | Parte de recaudación y depósito (FIN-ING-07) | JF | Ventas → Recaudación |
| Diario | Revisión de cartera vencida | JF | Ventas → Cartera |
| Diario | Respaldo automático | Sistema | — |
| **Lunes** | Órdenes de compra; contratación de eventuales | JF / GG | Compras; Planillas |
| **Viernes** | Acta de control de stock (FIN-EGR-01) | UT | Compras → Stock y alertas |
| **Domingo** | Respaldo semanal completo | Sistema | — |
| **Cada 7 días** | Muestreo de peso por lote | UT | Producción → Disposición y peso |
| **Día 70-73 del ciclo** | Circular, pre-venta (≥ 3 días antes), faenado, notas | UT / JF | Planillas, Ventas, Producción |
| **Cierre de ciclo** | Concluir ciclo, cerrar costos, informe FIN-TES-12 (≤ 10 días) | UT / JF / GG | Producción, Costos, Presupuesto |
| **Mensual** | Planilla, depreciación, conciliación (≤ 5 días hábiles), reposición de caja, FIN-INF-01, cierre del mes | JF / GG | Varios |
| Mensual | INF-CV-07 → FIN-INF-05 | CV → GG | Vigilancia |
| **Trimestral** | INF-CV-02, INF-CV-09, balance de masas | CV / JF / GG | Vigilancia, RAU |
| **Semestral** | Prueba de restauración; informe semestral de activos; ejecución semestral (enero y julio) | JF / CV / GG | Respaldos, Activos, Presupuesto |
| **30-abr a 1-jul** | Calendario presupuestario | JF / GG / CA / CV | Presupuesto → Calendario |
| **Antes del 1-jul** | Parámetros del nuevo ejercicio | GG / CA | Parámetros |
| **30-jun** | Cierre del ejercicio | JF / GG | Cierres |
| **≤ 29-ago (60 días)** | EEFF a los Consejos | GG / CA | Cierres → Plazos |
| **≤ 28-sep (90 días)** | Asamblea: EEFF, INF-CV-08, excedentes | AG / CV | Cierres → Plazos / Excedentes |
| **31-oct** | Formulario 701 RAU | JF | RAU → Cuota y Form. 701 |

---

## 14. Mensajes frecuentes y solución de problemas

| Mensaje o síntoma | Causa | Qué hacer |
|---|---|---|
| «Usuario o contraseña incorrectos.» | Datos errados | Revise mayúsculas de la contraseña. Al 3.er intento se bloquea 15 minutos. |
| «El usuario está bloqueado hasta las hh:mm…» (`USUARIO_BLOQUEADO`) | 3 intentos fallidos | Espere 15 minutos o pida a la GG **Restablecer contraseña**. |
| «Debe cambiar su contraseña» | Contraseña temporal o de 90 días | Cámbiela en el formulario que aparece. |
| «La sesión se cerró por 30 minutos de inactividad. Vuelva a ingresar.» | 30 minutos sin uso | Vuelva a ingresar; lo no guardado se pierde. |
| «Su rol no tiene permiso…» (`AutorizacionError`) | Acción ajena al rol | Pida que la realice el rol correspondiente (sección 2.3). |
| «Incompatibilidad de funciones» | Segregación MOF | La misma persona no puede registrar y autorizar. |
| «El período … está cerrado» (`PeriodoCerradoError`) | Fecha en mes cerrado | Use una fecha de un período abierto o reverse con otra fecha. |
| «El asiento no cuadra» | Debe ≠ Haber | Corrija las líneas hasta Diferencia = Bs 0,00. |
| `OC_DIA_NO_PERMITIDO` | OC fuera del lunes | Emita el lunes o tramite una compra de emergencia CI-FIN-16. |
| «Se requieren 3 cotizaciones» | Compra > Bs 5.000 | Registre las tres cotizaciones. |
| «Saldo bajo el 120 % del SME» | Liquidez | Solicite autorización escrita de la GG; el compromiso queda suspendido. |
| «Prohibido faenar antes del día 70» | POL-03 | Espere o tramite el faenado anticipado con informe veterinario. |
| «Falta el Certificado RAU exhibido» | DS 24463 | Marque la casilla e indique el número del certificado. |
| `SIN_INF_CV_07` | CI-FIN-18 | El CV debe emitir antes el INF-CV-07 del mes. |
| `FALTA_DECISION` | Indicador «Malo» sin acción | Escriba la acción gerencial del indicador. |
| `PARAMETRO_FALTANTE` | Falta un parámetro vigente para el ejercicio | La GG lo propone y el CA lo aprueba. |
| La pestaña muestra un recuadro rojo de alerta de plazo | Obligación vencida o próxima | Complete la tarea indicada; no es un error. |
| El navegador no abre | Streamlit no inició | Abra `http://localhost:8501`; revise la ventana negra del .bat. |
| «database is locked» | Otro proceso usa la base | Cierre otras copias del ERP o programas que abran el .db; reintente. |
| El respaldo no llegó al disco externo | Disco desconectado | Conéctelo y pulse **Ejecutar respaldos pendientes de hoy**. |

Todo error queda registrado; los mensajes llevan un **código** (por ejemplo `MUESTRA_INSUFICIENTE`) que conviene
indicar al pedir soporte.

---

## 15. Anexos

### 15.1 Parámetros normativos principales (ejercicio 2026)

| Área | Parámetro | Valor |
|---|---|---|
| Autorizaciones | `autoriza_desembolso_gg_sobre_bs` / `firma_mancomunada_sobre_bs` | Bs 1.000 / Bs 5.000 |
| Compras | `oc_tres_cotizaciones_sobre_bs` / `cotizaciones_minimas` | Bs 5.000 / 3 |
| Compras | `dia_semana_orden_compra` / `dia_semana_control_stock` / `dia_semana_contratacion_eventual` | 1 (lunes) / 5 (viernes) / 1 (lunes) |
| Tesorería | `sme` / `sme_umbral_compromiso` | Bs 136.413 / 1,20 |
| Tesorería | `sme_cumplimiento_bueno` / `sme_cumplimiento_malo` | 1,50 / 1,00 |
| Tesorería | `tope_caja_bs` / `gasto_menor_max_bs` | Bs 5.000 / Bs 500 |
| Tesorería | `desvio_flujo_caja_alerta` / `conciliacion_dias_habiles` / `arqueos_por_anio` | 10 % / 5 / 2 |
| Producción | `dias_ciclo` / `dias_ciclo_minimo` | 73 / 70 |
| Producción | `densidad_max_aves_m2` / `aves_por_ciclo` / `lote_aves` | 8 / 3.000 / 1.000 |
| Producción | `mortalidad_alerta` / `mortalidad_anormal` | 3 % / 5 % |
| Producción | `muestreo_peso_dias` / `rendimiento_kg_ave` / `kg_por_quintal` | 7 / 2,6 / 46 |
| Indicadores | ICA bueno/malo · PMF · ICL · RINC · ROA · Margen operativo | 2,00/2,10 · 10/20 % · 61/63 % · 15/5 % · 18/10 % · 30/20 % |
| Indicadores | DPF · CAC · `margen_bruto_canal_min` | 100/105 % · 100/90 % · 20 % |
| Ventas | `precio_carne_kg` / `precio_gallinaza_carga` | Bs 23,50 / Bs 500 |
| Ventas | `tarifa_eventual_por_ave` / `flete_por_lote_bs` | Bs 1,50 / Bs 970 |
| Ventas | `horeca_credito_dias_max` / `preventa_dias_min` / `preventa_friales_min` / `credito_incumplimientos_suspension` | 1 / 3 / 2 / 2 |
| Planillas | `aporte_laboral` / `aporte_patronal` / aguinaldo e indemnización | 12,71 % / 17,21 % / 8,33 % |
| Planillas | `salario_encargado_granja_mensual` | Bs 3.300 |
| Presupuesto | Semáforo ingresos / costos | −5/−10 % · +5/+10 % |
| Presupuesto | `capex_variacion_pct` / `capex_variacion_monto_bs` / `capex_no_previsto_ca_sobre_bs` | 15 % / Bs 10.000 / Bs 20.000 |
| Presupuesto | `presupuesto_informe_ciclo_dias` / POA meta / POA rojo | 10 / 90 % / 75 % |
| EEFF | `eeff_consejos_dias` / `eeff_asamblea_dias` | 60 / 90 |
| EEFF | `reserva_legal_min` / `fondo_educacion_min` | 5 % / 1 % |
| RAU | `rau_cuota_por_ha` / `rau_hectareas` | Bs 3,03 / 8 |
| RAU | Vencimiento Form. 701 / anticipación / alertas | 31-oct / 15 días hábiles / 60 y 30 días |
| RAU | Completitud EXP-RAU / I-1 / I-2 | 95 % / ≥ 65 % / ≤ 15 % (desde 2027) |
| Archivo | Retención T10 / T5 / desecho / folios | 10 / 5 / 2 años / 200 |
| Vigilancia | `cv07_partidas_min` / INF-CV-02 / INF-CV-06 / inventario sorpresivo / `verificacion_fisica_meses` | 3 / 4 al año / 2 al año / 1 al año / 12 |
| Seguridad | `password_caducidad_dias` / `login_intentos_max` / `login_bloqueo_minutos` | 90 / 3 / 15 |
| Seguridad | `sesion_inactividad_minutos` / `password_longitud_min` | 30 / 8 |
| Respaldos | Copias / medios / fuera del sitio / prueba de restauración | 3 / 2 / 1 / cada 6 meses |
| Respaldos | Retención diaria / semanal / mensual | 45 días / 8 semanas / 24 meses |
| Identidad | `entidad_nit` / `entidad_ubicacion` | «Por registrar» / «Caranavi, La Paz – Bolivia» |

> Los valores de esta tabla son los cargados al entregar el sistema. La fuente oficial es siempre **Administración ·
> Parámetros normativos → Vigentes** del ejercicio que corresponda.

### 15.2 Catálogo de formatos por módulo

| Módulo | Formatos |
|---|---|
| Tesorería | FIN-TES-01 a FIN-TES-08 (caja, bancos, flujo FIN-TES-05, saldo FIN-TES-06, alerta FIN-TES-07, medidas FIN-TES-08), INF-CV-03, reposición, conciliación |
| Compras e inventarios | FIN-EGR-01 (stock), FIN-EGR-02 (disponibilidad), FIN-EGR-03 (OC), FIN-EGR-04 (recepción), FIN-EGR-05 (programación de pagos), acta de baja |
| Personal y planillas | Planilla, FIN-EGR-06 (eventual), FIN-EGR-12 (hoja de tiempo), FIN-EGR-13 (conciliación de horas), circular de cierre, contratos, liquidación final |
| Producción | FIN-TRA-01 (apertura), FIN-TRA-02 (bitácora), FIN-TRA-06 (conciliación de aves), FIN-TRA-11 (disposición), faenado CI-FIN-12, página QR |
| Costos | Informe de Costo de Ciclo, mayor por sufijo, presupuesto D.4 |
| Activos | Plan CAPEX, FIN-TES-02 (ROA/SME), FIN-TRA-08 (activación), FIN-TRA-09 (ficha), depreciación mensual, actas de baja, INF-CV-01, informe semestral |
| Ventas | FIN-ING-04 (nota de entrega), FIN-ING-07 (parte diario), FIN-ING-08 (margen por canal), despacho, tarifario, CIR-ING-01, resoluciones de crédito |
| Contabilidad | Comprobantes, Libro Diario, Libro Mayor, Balance 4 y 8 columnas, Plan de Cuentas, períodos, FIN-INF-01, FIN-INF-03, FIN-INF-04, EEFF, Notas 1-16, paquete con huella SHA-256, liquidaciones de excedentes |
| RAU y archivo | Liquidación de cuota, Form. 701, FIN-TRA-13 (balance de masas), INF-CV-09, EXP-RAU, carpeta de fiscalización, actas de transferencia y destrucción |
| Presupuesto | FIN-TES-09, FIN-TES-10, FIN-TES-11, FIN-TES-12, Excel del Presupuesto Maestro |
| Vigilancia y tablero | INF-CV-02, 04, 05, 06, 07, 08; FIN-INF-05; FIN-TES-13 |
| Administración | ACTA-RES (restauración), REV-SEG (revisión de seguridad) |

### 15.3 Glosario

| Sigla | Significado |
|---|---|
| AG / CA / CV | Asamblea General / Consejo de Administración / Consejo de Vigilancia |
| GG / JF / UT | Gerencia General / Jefe de Finanzas y Comercialización / Unidad Técnica (Encargado de Granja) |
| CPP | Costo promedio ponderado |
| CIF / MOD / MPD | Costos indirectos de fabricación / Mano de obra directa / Materia prima directa |
| SME | Saldo mínimo de efectivo (Bs 136.413) |
| RAU | Régimen Agropecuario Unificado |
| HORECA | Hoteles, restaurantes y catering |
| ICL, RINC, ROA, ICA, PMF, DPF, CAC | Indicadores de la sección 8.2.1 |
| OC | Orden de compra |
| EEFF | Estados financieros |
| WAL | Modo de diario de SQLite que permite lecturas simultáneas |

### 15.4 Documentos relacionados

* `docs/manual_usuario/GG.md`, `JF.md`, `UT.md`, `CA.md`, `CV.md`, `AG.md`: guías rápidas por rol.
* `docs/checklist_aceptacion.md`: lista de aceptación del sistema.
* `docs/revision_seguridad.md`: resultado de la revisión de seguridad de la Fase 13.
* `docs/riesgos_y_deuda_tecnica.md`: riesgos conocidos y mejoras pendientes.
* `README.md`: instalación y estructura técnica.

---

*Manual Maestro de Usuario y Guía Operativa Definitiva — ERP Pollolandia v1.0.1. Las cifras normativas citadas
provienen de la tabla `parametros`; ante cualquier diferencia prevalece el valor vigente registrado en el sistema.*
