# Checklist de aceptación por módulo — ERP Pollolandia v1.0.0

Cómo usarlo: la comisión de recepción (GG, JF y un miembro del CV) marca cada punto después de verificarlo en la
pantalla indicada, con la base de demostración o con datos reales. La columna **Prueba** cita la prueba automática que
ya lo verifica (`pytest pruebas/<archivo>`). Cada fila se firma al final.

## 0. Instalación, acceso y seguridad
- [ ] `iniciar_erp.bat` instala y abre el sistema; la configuración inicial crea la GG. — *test_humo*
- [ ] El menú de cada rol muestra solo sus opciones (GG 24, JF 23, UT 10, CA 21, CV 23, AG 14 páginas). — *test_humo*
- [ ] Contraseña temporal o con 90 días obliga a cambiarla; 3 intentos fallidos bloquean 15 minutos; 30 minutos de
      inactividad cierran la sesión. — *test_seguridad, test_revision_seguridad*
- [ ] El CV no puede registrar ni aprobar en ningún módulo (solo sus informes y la verificación). — *test_seguridad,
      test_revision_seguridad*
- [ ] **Revisión de seguridad** sin controles en rojo; REV-SEG descargado y firmado. — *test_revision_seguridad*
- [ ] La bitácora registra cada operación con valores antes y después y no se puede modificar. — *test_auditoria*

## 1. Tesorería (Ciclo 1)
- [ ] Fondo de caja Bs 5.000 con custodio y resolución; reposición y depósito del excedente. — *test_tesoreria*
- [ ] Desembolso > Bs 1.000 exige la GG; > Bs 5.000 la firma mancomunada GG + CV. — *test_seguridad*
- [ ] Compromiso bajo el 120 % del SME queda suspendido hasta la autorización escrita. — *test_tesoreria*
- [ ] Conciliación bancaria FIN-INF-02 y alerta de liquidez FIN-TES-07. — *test_tesoreria*

## 2. Compras e inventarios (Ciclo 2)
- [ ] OC solo los lunes; tres cotizaciones sobre Bs 5.000; recepción FIN-EGR-04; CPP recalculado. — *test_compras*
- [ ] Punto de reorden y compra de emergencia CI-FIN-16; programación de pagos con jerarquía POL-04. — *test_compras*

## 3. Personal y planillas (Ciclo 2)
- [ ] Planilla con aporte laboral 12,71 %, patronal 17,21 %, aguinaldo e indemnización por doceavos. — *test_planillas*
- [ ] Personal eventual: circular, autorización del lunes, liquidación Bs 1,50 por ave, DPF. — *test_planillas*

## 4. Producción y trazabilidad (Ciclo 3)
- [ ] Bitácora diaria con mortalidad acumulada y semáforo 3 % / 5 %; alerta el mismo día. — *test_produccion*
- [ ] Mortalidad 5,1 % va a 7.2.01.04; faenado antes del día 70 rechazado; QR del lote. — *test_produccion*

## 5. Costos matriciales (Ciclo 3)
- [ ] Ciclo 2026 de 3.000 aves: Bs 116.286,28 y Bs 14,91/kg; centro en cero al día 73. — *test_costos*
- [ ] Corte de MOD y su reversión en el ciclo siguiente. — *test_costos, test_integracion*

## 6. Activos fijos y CAPEX
- [ ] Plan CAPEX, activación, depreciación mensual, bajas con visto del CV, verificación física anual. — *test_activos*

## 7. Ventas, HORECA y cobranza (Ciclo 4)
- [ ] Tarifario aprobado por el CA; pre-venta con cobertura 100 % (CI-FIN-13); nota con Certificado RAU. — *test_ventas*
- [ ] Crédito HORECA solo desde 2028; parte diario y depósito del efectivo. — *test_ventas*

## 8. Contabilidad general y estados (Ciclo 5)
- [ ] FIN-INF-01 con pendientes en cero antes del cierre mensual. — *test_cierre, test_integracion*
- [ ] Escenario 2026: excedente neto Bs 210.293,39; balance cuadrado; EEFF 60/90 días. — *test_cierre*

## 9. RAU, cumplimiento y archivo
- [ ] Calendario fiscal, cuota Bs 24,24, Form. 701, I-1/I-2, EXP-RAU, archivo MAN-ARC-01. — *test_rau, test_archivo*

## 10. Presupuesto y POA
- [ ] Presupuesto Maestro 2026-2030 y por ciclo; semáforos de ejecución; FIN-TES-11/12. — *test_presupuesto*
- [ ] **Datos del POA cargados:** 5 ciclos, 90 registros de flujo, 22 filas por área; flujo POA vs real. — *test_poa*

## 11. Consejo de Vigilancia
- [ ] FIN-INF-05 sin INF-CV-07 rechazado; el cofirmante no verifica su desembolso. — *test_indicadores, test_vigilancia*

## 12. Tablero de indicadores
- [ ] Indicadores 2026: ICL 62,4 % (Normal), ROA 27,4 %, margen operativo 22,1 %; tendencias. — *test_indicadores*

## 13. Integración de punta a punta (criterio de la Fase 13)
- [ ] Ciclo completo apertura → compra → recepción → planilla → bitácora de 73 días (mortalidad normal 2 %) → faenado
      → pre-venta → venta → cierre de costos → cierre mensual → estados: corre sin errores, **cada asiento cuadra** y
      **el balance cuadra**; los saldos finales de todas las cuentas coinciden con el escenario del POA. —
      *test_integracion*

## 14. Administración
- [ ] **Parámetros:** la GG propone, el CA o la AG aprueban con acta; la vigencia anterior se conserva; no se cambian
      ejercicios cerrados. — *test_parametros*
- [ ] **Respaldos 3-2-1:** copia diaria en 3 destinos, semanal completa, mensual y anual por cierre; panel en verde.
      — *test_respaldo, test_scripts*
- [ ] **Prueba de restauración** con acta ACTA-RES conforme y visto del CV; copia alterada → acta fallida. —
      *test_respaldo*
- [ ] `respaldo_automatico.bat` programado en Windows y probado una vez manualmente.

| Rol | Nombre | Firma | Fecha |
|---|---|---|---|
| Gerencia General | | | |
| Jefe de Finanzas y Comercialización | | | |
| Consejo de Vigilancia | | | |
