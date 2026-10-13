# ERP Pollolandia

ERP de la **Cooperativa Agropecuaria Pollolandia R.L.** (Caranavi, La Paz, Bolivia).
Aplicación web: se abre en el navegador (Streamlit) y guarda los datos en SQLite.

Estado: **Fase 13 – Integración y puesta en marcha** (v1.0.0, versión de entrega).

| Capa | Carpeta | Regla |
|---|---|---|
| Datos | `db/` | Esquema (v1 a v13), semillas, migraciones, datos del POA |
| Lógica | `servicios/` | Lógica pura; **no** importa `streamlit` |
| Presentación | `app.py` + `modulos/` | Solo llama a `servicios/` |

## Uso rápido en Windows (doble clic)

1. Instale **Python 3.11 o superior** (marque *Add python.exe to PATH*).
2. Haga doble clic en **`iniciar_erp.bat`**. La primera vez crea el entorno y
   instala las dependencias; luego abre el ERP en `http://localhost:8501`.
3. En el primer ingreso aparece **Configuración inicial**: cree el usuario de la
   Gerencia General (socio n.º 1). Con ese usuario, en *Administración · Usuarios*,
   dé de alta al Jefe de Finanzas, la Unidad Técnica y los miembros de los
   Consejos (vincule cada miembro a su socio: el Presidente y el Tesorero del CA
   son los suplentes de la GG según CI-FIN-17).

## Uso desde la terminal (alternativa)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m db.migraciones            # crea/actualiza la BD (también lo hace la app al abrir)
streamlit run app.py                # abre el ERP en el navegador
pytest                              # 596 pruebas
```

Otros comandos: `python -m db.migraciones --estado`, `--reiniciar`, `--reporte`.

```powershell
python -m scripts.cargar_datos_poa              # carga el POA 2026-2027 (calendario, flujo mensual, áreas)
python -m scripts.cargar_datos_poa --validar    # solo valida la transcripción
python -m scripts.respaldo_automatico           # respaldos 3-2-1 pendientes de hoy
python -m scripts.respaldo_automatico --manual  # copia completa inmediata
python -m scripts.restaurar_respaldo PAQUETE.zip --confirmar   # con la aplicación CERRADA
pytest pruebas/test_integracion.py              # ciclo completo de punta a punta
```

Programe `respaldo_automatico.bat` en el Programador de tareas de Windows (diario). Documentación de usuario en
`docs/`: **manual maestro de usuario y guía operativa** (`docs/Manual_Usuario_Definitivo_Pollolandia.md`), manual por rol (`docs/manual_usuario/`), checklist de aceptación, revisión de seguridad y riesgos.

## Qué incluye la Fase 2

- **Motor contable** (`servicios/contable.py`): asientos de partida doble con cuadre
  exacto en `Decimal`, solo cuentas de nivel 4 activas, cuentas (*) desde 2027,
  período abierto, control de naturaleza, centro de costo y sufijo en el Grupo 6,
  correlativo por tipo (ingreso, egreso, traspaso, ajuste), reversión (nunca se
  borra), cierre mensual secuencial, libro diario, mayor y balance de comprobación.
- **Seguridad** (`servicios/seguridad.py`): login con bcrypt, bloqueo por intentos,
  caducidad de 90 días, menú por rol, segregación de funciones (MOF §3.4, CI-FIN-10),
  Matriz de Autorización **CI-FIN-09** completa, firma mancomunada y suplencia
  **CI-FIN-17** con constancia obligatoria.
- **Bitácora** (`servicios/auditoria.py`): inmutable, con valores antes y después.

## Qué incluye la Fase 3 (Tesorería · Ciclo 1)

Menú **Ciclo 1 · Tesorería**, con pestañas Caja, Bancos, SME y flujo, Conciliación y Formatos:

- **Caja (CI-FIN-01):** fondo fijo en 1.1.01.05, gasto menor con comprobante único y tope, cobros en
  efectivo, depósito diario del excedente sobre el tope, reposición validada por la GG y arqueo
  sorpresivo del CV (INF-CV-03) con diferencia y control del límite diario.
- **Bancos:** depósitos, pagos con la Matriz CI-FIN-09 (GG sobre Bs 1.000; GG + CV sobre Bs 5.000),
  transferencias, cargos y abonos.
- **SME (POL-04):** `validar_compromiso` suspende todo compromiso que deje el saldo bajo el 120 % del
  SME hasta la autorización escrita de la GG; jerarquía de pagos de 7 niveles; bloqueo del pago de
  excedentes bajo el SME.
- **Flujo y alertas:** flujo proyectado a dos ciclos con saldo mínimo diario (FIN-TES-05), alerta
  por riesgo de SME o desvío mayor al 10 % (FIN-TES-07), medida correctiva de la GG fiscalizada por
  el CV (FIN-TES-08) y reporte diario con semáforo (FIN-TES-06).
- **Conciliación (FIN-INF-02, CI-FIN-07):** cheques en circulación, depósitos en tránsito, cargos y
  abonos no registrados; plazo de 5 días hábiles con feriados; firma de la GG solo si cuadra.
- **Formatos PDF** con bloque de firmas: FIN-TES-05, 06, 07, 08, INF-CV-03, FIN-INF-02 y
  Comprobante de Reposición de Caja Chica.

## Qué incluye la Fase 4 (Compras e inventarios · Ciclo 2)

Menú **Ciclo 2 · Compras e inventarios**, con pestañas Proveedores, Inventarios, Stock y alertas, Órdenes de
compra, Recepción, Pagos, Bajas y Formatos:

- **Proveedores (PET-RAU §2.2):** fichas de pequeños productores con C.I. y certificado RAU (con último pago) o de
  No Imponibilidad vigente; sin ellos el proveedor queda OBSERVADO y la compra se bloquea.
- **Inventarios (PC-07):** cuentas 1.1.03.01 a 1.1.03.15, kárdex perpetuo y Costo Promedio Ponderado recalculado
  en cada entrada; salidas de consumo a 6.1.01.xx con su sufijo; pollitos BB como cuenta transitoria.
- **Punto de reorden (CI-FIN-16):** consumo diario × días de reposición + stock de seguridad (7 días alimento y
  pollitos BB; 5 días veterinarios, limpieza y crianza), recalculado cada ciclo con el consumo del ciclo anterior.
- **Acta del viernes (FIN-EGR-01)**, monitoreo de cualquier día, alertas y compra de emergencia autorizada por la GG
  (o su suplente CI-FIN-17). Rotación máxima de granos de 30 días con alerta.
- **Orden de compra (FIN-EGR-03):** solo los lunes (salvo emergencia), 3 cotizaciones sobre Bs 5.000,
  disponibilidad presupuestaria (FIN-EGR-02), `validar_compromiso` de Tesorería y autorización de la GG.
- **Recepción (FIN-EGR-04)** con cantidad y calidad; asiento automático inventario contra 2.1.01.xx.
- **Programación de pagos (FIN-EGR-05)** con jerarquía POL-04 y firma mancomunada sobre Bs 5.000.
- **Bajas** con acta de la GG y visto del CV sobre Bs 2.000. **Indicador CAC** (Malo < 90 %, Normal 90-99 %,
  Bueno 100 %).

## Qué incluye la Fase 5 (Personal y planillas · Ciclo 2)

Menú **Ciclo 2 · Personal y planillas**, con pestañas Personal, Planilla mensual, Hojas de tiempo, Personal
eventual, Liquidación final, Alertas y Formatos:

- **Personal fijo:** GG, JF y Encargado de Granja con contrato, fecha de ingreso, salario con vigencia y retiro.
- **Planilla mensual (FIN-EGR-09):** haber básico, bonos, aporte laboral 12,71 % (2.1.02.05, retención, no costo) y
  líquido pagable; autoriza la GG y el asiento se registra el último día del mes.
- **Beneficios sociales (FIN-EGR-10):** aguinaldo 8,33 % (2.1.02.02), indemnización 8,33 % (2.2.01.01) y aporte
  patronal 17,21 % (2.1.02.03). Personal de granja a 6.1.01.01 con sufijos 20, 21, 38 y 39; administrativo a
  7.1.01.01 a 7.1.01.04.
- **Prorrateo 75 % / 25 % (FIN-EGR-12)** entre 6.1.01.01 y 6.1.01.02 desde 2027, con las hojas de tiempo firmadas
  (o regularizadas); sin hojas se aplica el 75 % por defecto.
- **Conciliación pagado vs absorbido (FIN-EGR-13)** fiscalizada por el CV y **verificación cruzada de horas contra
  producción (CI-FIN-14)** sobre una muestra mínima del 20 % de las semanas.
- **Personal eventual:** circular CIR-EGR-01 con 7 días de antelación y acuse de GG y JF; autorización FIN-EGR-06 solo
  el lunes de la semana de faenado, con verificación presupuestaria, compromiso de Tesorería y suplencia CI-FIN-17;
  contrato CONT-EGR-01; liquidación FIN-EGR-08 a Bs 1,50 por ave con DPF y semáforo (Bueno ≤ 100 %, Normal hasta
  105 %, Malo > 105 %, que exige justificación).
- **Liquidación final (FIN-EGR-11)** dentro de los 15 días del retiro, aplicando las provisiones acumuladas.
- **Alertas de pago legal:** sueldos, aportes a la Gestora y la CNS, aguinaldo y liquidaciones finales.

## Qué incluye la Fase 6 (Producción y trazabilidad · Ciclo 3)

Menú **Ciclo 3 · Producción y trazabilidad**, con pestañas Ciclos y galpones, Bitácora diaria, Alertas sanitarias,
Bajas por mortalidad, Disposición y peso, Faenado, Conciliación y QR, y Formatos:

- **Calendario:** 5 ciclos de 73 días por ejercicio (el día de recepción es el día 1), galpones con su superficie y
  lotes de hasta 1.000 aves por galpón con densidad máxima de 8 aves/m².
- **Apertura (FIN-TRA-01):** vincula el traslado de los pollitos BB que la recepción ya hizo el mismo día
  (1.1.03.01 a 6.1.01.01 sufijo 01) y fija el día 1 y el día 73.
- **Bitácora diaria (FIN-TRA-02):** fase (inicio 1-21, crecimiento 22-50, acabado 51-73), alimento en kg, aves
  muertas con causa presunta, novedades y tratamientos; mortalidad ACUMULADA sobre las aves iniciales con semáforo
  (verde ≤ 3 %, amarillo hasta 5 %, rojo > 5 %) y revisión semanal de la Gerencia.
- **Alertas sanitarias (CI-FIN-11):** automáticas al cruzar el 3 % y el 5 % o ante signos clínicos, y reportes de la
  Unidad Técnica; se exigen el mismo día (o la causa de la demora) y la decisión de la Gerencia.
- **Baja por mortalidad (FIN-TRA-04):** normal al sufijo 40 dentro del centro; anormal (> 5 % acumulado) a 7.2.01.04
  con informe veterinario y autorización de la Gerencia General.
- **Disposición sanitaria (FIN-TRA-11)** y **muestreo semanal de peso** con control de semanas faltantes.
- **Faenado (FIN-TRA-05):** bloqueado antes del día 70 (salvo indicación veterinaria autorizada); peso vivo, peso
  canal, merma, puntos de control CI-FIN-12, PMF (Bueno < 10 %, Malo > 20 %) e ICA (Bueno < 2,00, Malo > 2,10).
- **Conciliación** aves ingresadas = muertas + faenadas y conclusión del ciclo operativo.
- **QR «Vida Digna»:** URL, código QR y página del lote con datos tomados solo de los registros del ciclo.

## Qué incluye la Fase 7 (Costos matriciales · Ciclo 3)

Menú **Ciclo 3 · Costos matriciales**, con pestañas Consumos y CIF, Mayor por sufijo, Alimento propio, Cierre e
informe, Presupuesto D.4, Productos terminados y Formatos:

- **Acumulación por sufijo** en 6.1.01.01 y, desde 2027, en 6.1.01.02 (cada línea se imputa a su ciclo aunque dos
  ciclos se superpongan unos días).
- **Consumos al CPP:** alimento 02, veterinarios 08, limpieza 09, crianza 10, bolsas 12 y materias primas 03-07; el
  alimento de la bitácora en kg se valora en qq (46 kg por qq).
- **Alimento propio (2027):** producción 6.1.01.02 → 1.1.03.13, consumo propio al sufijo 11 y ensaquillado del
  excedente con el sufijo 13 (Bs 138,05 y Bs 148,05 por qq).
- **CIF 30-37** contra la cuenta que el Plan asigna al sufijo; 36 y 37 se prorratean por horas desde 2027.
- **Corte de MOD fija** al día 73 por los días sin planilla, con reversión en el ciclo siguiente.
- **Cierre del día 73:** 1.1.03.12 contra 6.1.01.01 dejando en cero el costo del ciclo; costo unitario Bs/kg con
  variación frente al ciclo anterior; Informe de Costo de Ciclo con conciliación de aves y aprobación de la GG.
- **Costo de ventas** al CPP (6.1.02.01 carne; 6.1.02.02 alimento excedente).
- **Presupuesto D.4** 2026 y 2027 por sufijo, con variaciones de precio y de cantidad y semáforo (5 % y 10 %).

## Qué incluye la Fase 8 (Activos fijos y CAPEX · Ciclos 1 y 3)

Menú **Ciclo 1 y 3 · Activos fijos y CAPEX**, con pestañas Libro de activos, Plan CAPEX, Activación,
Depreciación, Bajas, Verificación física, Informe semestral y Formatos:

- **Plan CAPEX (FIN-TES-01):** lo abre la GG (plazo 31 de mayo, marca si está fuera de plazo), con importación del
  D.5; fuente única «recursos propios»; impacto en la depreciación y en Bs/kg.
- **ROA y SME (FIN-TES-02/03):** el JF calcula ROA = utilidad ÷ (activos + inversión) con semáforo y la
  compatibilidad con el SME; la GG presenta al CA, que aprueba u observa; la Asamblea ratifica.
- **No previstos y variaciones:** fuera del plan exige autorización del CA (específica sobre Bs 20.000); variación
  > 15 % exige informe al CA y, si además supera Bs 10.000, autorización de la Asamblea. Costo sobre el saldo
  bancario exige acta de la Asamblea.
- **Activación (FIN-TRA-08) y ficha (FIN-TRA-09):** la GG autoriza con destino operativo/administrativo; el JF
  registra al costo histórico (precio + flete + instalación) contra 2.1.01.06 y abre la ficha con código
  AF-XXX-nnn. Botón para abrir las fichas del balance de apertura.
- **Depreciación mensual (FIN-TRA-10):** línea recta desde el mes de activación, cuotas consecutivas, a
  6.1.01.01-30/31/32/33, 6.1.01.02-34/35 o 7.1.02.04-07, contra 4.1.02.01-10; conciliación libro ↔ mayor. Desde
  esta fase la depreciación ya no se registra como CIF manual cuando existe la ficha.
- **Bajas:** acta de la GG; verificación del CV si el valor neto supera Bs 5.000; pérdida a 7.2.01.02.
- **FIN-TES-04** semestral (enero y julio) e **INF-CV-01** anual del CV, con control de tasas frente a PC-06.

## Qué incluye la Fase 9 (Ventas, HORECA y cobranza · Ciclo 4)

Menú **Ciclo 4 · Ventas, HORECA y cobranza**, con pestañas Clientes, Tarifario, Pre-venta del lote, Notas de
entrega, Cartera y cobranza, Recaudación y devoluciones, Margen por canal y Formatos:

- **Clientes** con ficha (HORECA, friales, mercado popular, agricultores), historial y entrega semanal promedio.
- **Tarifario** propuesto por la GG y aprobado por el CA en acta; toda nota usa el precio vigente; margen
  proyectado frente al costo del último ciclo cerrado (o el D.4 si aún no hay cierre).
- **Pre-venta (CI-FIN-13):** pedidos FIN-ING-01 con control de 3 días; pedidos indicativos con 2 friales como
  mínimo; verificación de cobertura del 100 % el día anterior; canal de respaldo activado por la GG; el despacho no
  se cierra sin cobertura completa.
- **Notas FIN-ING-03/06:** comprobante obligatorio (FIN-ING-04 en crédito), Certificado RAU exhibido, sin cheques
  diferidos; crédito HORECA de 1 día en 2026-27; desde 2028 hasta 7 días con resolución FIN-ING-02.
- **Asientos automáticos:** 5.1.01.01 / 5.1.02.01 / 5.2.01.04 por caja, banco o 1.1.02.01; costo de ventas al CPP
  (la carne vendida antes del cierre del ciclo queda con costo pendiente y se contabiliza al cerrar).
- **Cobranza (CI-FIN-15):** revisión diaria, reporte de vencidos a la GG en 24 horas, suspensión automática al
  segundo incumplimiento consecutivo (CIR-ING-01) y restablecimiento por la GG; provisión de incobrables desde 2028.
- **Devoluciones por inocuidad** (5.1.04.01), **parte diario FIN-ING-07** con depósito del efectivo y
  **FIN-ING-08** mensual con alerta de ajuste tras dos meses de margen bajo el 20 %.

## Qué incluye la Fase 10 (Contabilidad general y estados financieros · Ciclo 5)

Menú **Ciclo 5 · Cierres y estados financieros**, con pestañas Cierre mensual, Costeo e indicadores, Estados
financieros, Notas 1-16, Cierre anual, Excedentes y Plazos 60/90 días:

- **FIN-INF-01:** cinco verificaciones (tesorería, egresos y planillas, cierres de ciclo, ventas, codificación)
  con el número de comprobantes pendientes; la registra la Unidad de Finanzas y el mes solo se cierra con cero.
- **Cierre mensual** por la GG: bloquea el período (no se reabre) y archiva el paquete FIN-INF-03/04.
- **FIN-INF-03** (costo de cada ciclo cerrado y costo de ventas del mes) y **FIN-INF-04** escalonado con ICL,
  RINC y ROA del mes y acumulados, con semáforo según los umbrales de `parametros`.
- **Estados del MAN-006:** Balance General, Estado de Resultados, Flujo de Efectivo por método directo y
  Evolución del Patrimonio, con controles de cuadre; **Notas 1 a 16** con saldos reales; PDF y Excel.
- **Cierre anual (30 de junio):** cuentas de resultado a 8.1.01.01 y traslado a 3.1.03.02 (o 3.1.03.03), con
  autorización de la GG; luego se cierra junio y el ejercicio.
- **Excedentes (CI-FIN-02):** Reserva Legal >= 5 %, Educación >= 1 %, Reinversión por decisión de Asamblea;
  INF-CV-04 del CV; apropiación y 3.1.03.04 a 2.1.05.01; liquidaciones por socio firmadas por la GG; pago solo si
  el saldo bancario posterior es >= SME y no hay obligaciones laborales o tributarias vencidas.
- **Plazos:** presentación a los Consejos en 60 días y aprobación de la Asamblea en 90 días, con alertas; el
  paquete presentado queda congelado con huella SHA-256.

## Qué incluye la Fase 11 (RAU, cumplimiento legal y archivo · Transversal)

Menú **Transversal · RAU, cumplimiento y archivo**, con nueve pestañas:

- **Calendario fiscal** de cada gestión: consulta de la RND (30-sep), liquidación por socio (20 días hábiles antes),
  Formulario 701 y patentes (al menos 15 días hábiles antes del 31-oct) y acta de compatibilidad (30-jun), con
  alertas a 60 y 30 días y semáforo.
- **Cuota y Form. 701:** cuota = hectáreas × cuota por ha (8 × Bs 3,03 = Bs 24,24), reparto en 12 partes iguales
  (Bs 2,02 por socio), asiento 7.1.05.02 / 2.1.03.01 y **advertencia visible** de que la cuota y las 8 vs 10 ha
  están por validar. El 701 con menos de 15 días hábiles queda en amarillo; después del 31-oct, en rojo.
- **Superficie y CAPEX:** superficie usada frente a declarada y control ante cada línea CAPEX (verde, amarillo si la
  inversión obliga a actualizar la inscripción, rojo si ya se superó).
- **Alimento:** I-1 (autoconsumo >= 65 %) e I-2 (ventas de alimento / ingresos <= 15 %), balance de masas
  trimestral (producción − consumo − merma = excedente) firmado por la GG y programa de excedente con tope del CA.
  Cada nota de entrega de alimento verifica programa, tope, comprador autorizado y venta al contado.
- **Compradores y terceros:** padrón de compradores no socios autorizados; todo servicio a terceros dispara alerta
  roja (GG en 24 horas, CA en 5 días hábiles).
- **Revisión CV:** INF-CV-09 trimestral con las 10 verificaciones del PET-RAU y semáforo; INF-CV-09 anual.
- **Expediente EXP-RAU** A–G (39 documentos) con índice maestro y porcentaje de completitud (meta 95 %).
- **Fiscalización:** registro de notificaciones, plan de respuesta (24 horas, 3 días, vencimiento y prórroga),
  escenarios A–E, Carpeta de Respuesta Rápida, cierre con lecciones aprendidas y revisión semanal del buzón.
- **Archivo contable (MAN-ARC-01):** tabla de retención (72 documentos; permanente, 10 años, 5 años, vida útil
  + 10 y desecho a 2 años), expedientes `EXP-ING-G2627-001`, rutas `AF-FIN/G2026-27/ING/PR-FIN-09`, foliación en
  tomos de 200 folios, libro de préstamo y actas de transferencia y de destrucción (GG autoriza, CV visa).

## Qué incluye la Fase 12 (Presupuesto, Consejo de Vigilancia y tablero)

Menú **Información gerencial · Presupuesto y POA** (siete pestañas):

- **Presupuesto Maestro 2026-2030** cargado del Manual Presupuestario: ventas, producción por lotes, MP, MOD, CIF,
  gastos, CAPEX, flujo, resultados, balance, D.6 e indicadores proyectados (625 cifras), con descarga en Excel.
- **Por ciclo (FIN-TES-10)** y **hoja de supuestos (FIN-TES-09)**: 334 líneas por ciclo y partida, modificaciones
  aprobadas incluidas, SME y saldo final proyectado.
- **Calendario presupuestario** abril-julio (30-abr supuestos → 15-may proyecto → 24-may comité → 31-may CAPEX →
  15-jun aprobación CA → 25-jun verificación CV → 1-jul comunicación), con secuencia, rol responsable y alertas.
- **Ejecución (FIN-TES-12):** presupuesto frente a real por ciclo, semestre o año con variación total, porcentual,
  de precio y de cantidad y semáforo por partida (ingresos −5/−10 %; MP, MOD y CIF +5/+10 %; DPF; CAPEX 15 %;
  flujo 10 % y SME). Un amarillo exige causa; un rojo, además, responsable, acción y fecha. La GG presenta el informe.
- **Modificaciones (FIN-TES-11):** la GG solicita, la Unidad de Finanzas verifica y resuelve la autoridad de
  CI-FIN-09 (GG, CA o AG con acta).
- **POA:** 49 actividades por área y trimestre; cumplimiento físico >= 90 % (verde), 75-90 % (amarillo).
- **Alimento propio vs comprado (D.6).**

Menú **Control interno · Consejo de Vigilancia** (seis pestañas): panel de los controles INF-CV-01 a 09, inventario
sorpresivo y desembolsos con semáforo; **CI-FIN-18** (el CV emite el INF-CV-07 tras la FIN-INF-01, con recálculo
independiente y al menos 3 partidas muestreadas; la GG emite el FIN-INF-05 **solo si existe el INF-CV-07 del mes**:
lo bloquea el servicio y un disparador en la BD); arqueos e inventario sorpresivo; verificación de activos
(INF-CV-01); **desembolsos** (el miembro del CV que cofirmó no puede verificar: servicio + disparador); informes
INF-CV-02 (liquidez trimestral frente al SME), 05, 06 y 08.

Menú **Información gerencial · Tablero de indicadores**: ICL, RINC, ROA, margen operativo, SME, ICA, PMF, DPF, CAC y
mortalidad con los semáforos de la sección 1.3 y lo proyectado; margen por canal; tendencias por ciclo y por
ejercicio (real frente a proyectado); FIN-TES-13 y FIN-INF-05 en PDF. Con el escenario 2026: ICL 62,4 % (Normal),
ROA 27,4 %, margen operativo 22,1 % y SME 101 %.

## Qué incluye la Fase 13 (Integración y puesta en marcha)

- **`app.py` definitivo:** los 12 módulos del Master Plan, contabilidad, control interno y administración, con el
  menú filtrado por rol (GG 24 páginas, JF 23, UT 10, CA 21, CV 23, AG 14) y respaldo automático del día al iniciar.
- **Prueba de integración** (`pruebas/test_integracion.py` y `pruebas/simulacion_ciclo.py`): ciclo 1 de 2026 con los
  servicios reales — apertura (banco Bs 138.010), compras con cotizaciones y autorización escrita bajo el 120 % del
  SME, recepción, pagos, planillas de julio a septiembre, **bitácora de 73 días** con mortalidad normal de 2 %
  (60 aves), bajas y disposición, faenado de 2.940 aves (7.800 kg), pre-venta con cobertura CI-FIN-13, venta
  (Bs 183.300 + gallinaza Bs 1.500), cierre de costos, inicio del ciclo 2 con reversión del corte de MOD, cierres
  mensuales y estados. Verifica que **cada asiento cuadra**, que el **balance cuadra** y que el saldo de **todas las
  cuentas** coincide con el escenario del POA calculado con las cifras unitarias de los documentos.
- **Carga de datos reales del POA** (`scripts/cargar_datos_poa.py`, `servicios/poa.py`, `db/datos_poa.py`):
  calendario C1-C5 (C2-C5 dentro del POA), flujo mensual oct-2026 a jun-2027 y presupuesto por área, con validación
  de la cadena de saldos; pestaña **Flujo POA vs real** en Presupuesto.
- **Respaldo 3-2-1** (`servicios/respaldo.py`, MAN-ARC-01 §5.2): 3 copias (equipo, disco externo, fuera del sitio),
  diaria (base + incremental del archivo digital), completa semanal, de cierre mensual y anual; depuración por plazo;
  **prueba de restauración** en carpeta temporal con 12 verificaciones y **acta ACTA-RES** con visto del CV;
  restauración de la base en uso por línea de comandos.
- **Administración de parámetros con historial:** la GG propone, el CA o la AG aprueban con acta; la vigencia
  anterior se cierra y nunca se borra; bitácora con valores antes y después.
- **Revisión de seguridad automatizada** (REV-SEG): 90 días, bloqueo por intentos, inactividad, bcrypt, CV solo
  lectura, segregación, bitácora inmutable, claves foráneas y WAL.
- **Documentación:** manual por rol, checklist de aceptación por módulo, riesgos y deuda técnica.

## Reportes contables en PDF oficial (v1.0.1)

Todos los reportes del módulo de contabilidad se descargan en PDF con el membrete institucional unificado
(`reportes/pdf_contable.py`): «COOPERATIVA AGROPECUARIA POLLOLANDIA R.L.», «Caranavi, La Paz – Bolivia | NIT | RAU»
en cada página, tablas con bordes y encabezado resaltado, montos `Bs 1.234,56` alineados a la derecha y pie con fecha
y hora de emisión, usuario y «Página x de y». El NIT sale del parámetro `entidad_nit` (por registrar con acta del CA)
y el RAU del último Certificado RAU registrado.

| Pantalla | PDF |
|---|---|
| Libro diario | Libro Diario del período; todos los comprobantes del período; comprobante individual |
| Libro mayor | Mayor analítico de la cuenta (y sufijo); mayor de todas las cuentas (centros abiertos por sufijo) |
| Balance de comprobación | 4 columnas (sumas y saldos) y 8 columnas (sumas, saldos, inventario y resultados) |
| Registrar / Reversar asiento | Comprobante del asiento registrado o de la reversión |
| Períodos contables | Estado de los períodos; Plan de Cuentas |
| Cierres y estados financieros | Balance General, Resultados, Flujo, Evolución del Patrimonio, Notas, paquete EEFF, FIN-INF-01/03/04, INF-CV-04, liquidación del socio y comprobantes del cierre anual |

Los demás formatos del sistema (FIN-xx de otros módulos) agregan la fecha de emisión al pie. Las exportaciones Excel
existentes se conservan junto al PDF.

## Roles y lo que ve cada uno

| Rol | Puede |
|---|---|
| GG | Autorizar y firmar, cerrar períodos, administrar usuarios, ver bitácora. **No registra asientos.** |
| JF | Registrar y reversar asientos, solicitar autorizaciones. **No autoriza ni firma.** |
| UT | Contar el stock del viernes, recibir insumos (FIN-EGR-04), proponer bajas, emitir la circular de cierre y las hojas de tiempo; lleva la bitácora, las alertas sanitarias, la disposición, el peso y el faenado. |
| CA | Aprobar decisiones de su competencia; Presidente y Tesorero suplen a la GG. |
| CV | Solo lectura en la contabilidad; cofirma desembolsos mayores a Bs 5.000; emite los INF-CV, arqueos, inventarios sorpresivos y verificaciones de desembolsos que no cofirmó. |
| AG | Aprobar con acta y mayoría calificada cuando corresponde. |

## Parámetros por validar

Los valores de longitud de contraseña (8), intentos (3), bloqueo (15 min) e
inactividad (30 min) no figuran en los documentos: están en la tabla `parametros`
marcados *Propuesta Fase 2 (POR VALIDAR)* y se cambian sin tocar el código. Lo mismo ocurre con la
cuenta de comisiones bancarias (`cuenta_cargos_bancarios` = 7.2.01.03), porque el Plan v3 no tiene
una cuenta específica. También la cuenta de pérdida por bajas de inventario
(`cuenta_perdida_baja_inventario` = 7.2.01.03) y los días de reposición de cada proveedor, que se registran por
insumo antes de calcular el punto de reorden.

**Fase 5:** los documentos no fijan los plazos legales de pago. Quedan como parámetros POR VALIDAR:
`sueldos_pago_dias_despues_mes` (0, el último día del mes), `aportes_pago_ultimo_dia_habil_mes_siguiente` (1),
`aguinaldo_pago_dia_limite` (20 de diciembre) y `alerta_pago_planillas_dias` (5 días de aviso).

**Fase 6:** la dirección de la plataforma QR (`qr_url_base`) es una propuesta POR VALIDAR. La superficie de cada
galpón se registra en la pantalla (los documentos no la dan).

**Fase 7:** el peso del quintal (`kg_por_quintal` = 46) se deduce del POA (R8). Desde la Fase 8 la depreciación sale de las
fichas de activos (FIN-TRA-10). El presupuesto D.4 está cargado para 2026 y 2027.

**Fase 8:** el software contable de apertura se amortiza al 20 % (Diagnóstico Financiero y prueba de la fase),
no al 25 % de PC-06 (`amort_tasa_software_apertura`, VALIDAR). La ganancia por venta de activos va a 5.2.01.05
(el Plan v3 no tiene cuenta de ganancia). Las fichas de apertura son una por cuenta (no por bien): el desglose
individual queda POR VALIDAR.

**Fase 9:** el primer tarifario usa el costo presupuestado D.4 (Bs 14,91/kg) porque aún no hay ciclo cerrado;
la gallinaza no tiene costo de ventas (subproducto no valuado); el saldo a favor por devoluciones va a 2.1.04.01;
el número del Certificado RAU se registra en cada nota hasta que la Fase 11 lleve el registro del certificado.

**Fase 10:** la anticipación de la alerta de los plazos de 60/90 días (`eeff_alerta_dias` = 15) y la tasa de
deducción de la liquidación del socio (`excedentes_rc_iva_tasa` = 0, «RC-IVA cuando corresponda») son POR VALIDAR.
El CAPEX de la planta de alimento pagado en el ciclo 5 de 2026 queda en 1.2.03.02 hasta su activación en 2027,
porque las cuentas (*) rigen desde 2027.

**Fase 11:** la cuota por hectárea de la subzona de Caranavi (`rau_cuota_por_ha` = 3,03) y la superficie
(`rau_hectareas` = 8 frente a 10 ha del balance de apertura) están POR VALIDAR; se confirman con la consulta de la
RND y la constancia de inscripción del SIN. También: que el Formulario 701 de la gestión N vence el 31-oct de N+1;
el monto y el municipio de las patentes; que el I-2 exacto de 15 % es verde; y los permisos del nuevo recurso
`archivo` (propuesta). Los umbrales del PET-RAU rigen desde 2027 (en 2026 no hay alimento propio).

**Fase 12:** el margen de modificación presupuestaria que aprueba la GG sin el CA
(`presupuesto_modificacion_gg_max` = 10 %), la frecuencia semestral del INF-CV-06 (`cv_inf_cv_06_anual` = 2) y la
alerta de 15 días del calendario son POR VALIDAR. Los sufijos 38/39 (aportes e indemnización) se presupuestan como
CIF, como en el Manual (DISCREPANCIA con el Plan de Cuentas, que los trata como MOD). El saldo final proyectado por ciclo prorratea linealmente el flujo
anual; los ingresos presupuestados se calculan sobre aves ingresadas (observación del Manual B.1). El Manual tiene
diferencias de redondeo de Bs 1 en CIF 2028-2029 y MP 2029: se conservan sus totales.

**Fase 13:** los plazos de conservación de las copias diarias (30 días), semanales (8 semanas) y mensuales
(24 meses) y el día de la copia semanal (domingo) son POR VALIDAR; la regla 3-2-1, la frecuencia y la prueba
semestral salen de MAN-ARC-01 §5.2. La simulación reparte los sueldos administrativos (Bs 7.300 por mes, POA §3.1)
en Bs 4.000 para la GG y Bs 3.300 para el JF (supuesto; los documentos dan el total).

## Convenciones

- Montos en `Decimal` (2 decimales, ROUND_HALF_UP) y en centavos enteros en la BD; pantalla `Bs 1.234,56`.
- Fechas ISO `AAAA-MM-DD`; pantalla `dd/mm/aaaa`.
- Ninguna cifra normativa en el código: todo se lee de `parametros` con vigencia por ejercicio.
- Discrepancias entre documentos: comentario `# DISCREPANCIA:` y tabla `conciliacion_catalogo`.
