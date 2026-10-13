# Manual de usuario del ERP Pollolandia (v1.0.0)

Cooperativa Agropecuaria Pollolandia R.L. — Caranavi, Los Yungas, La Paz.

> La guía completa, módulo por módulo y paso a paso, está en el [Manual Maestro de Usuario y Guía Operativa Definitiva](../Manual_Usuario_Definitivo_Pollolandia.md). Estas guías rápidas lo resumen por rol.

Este manual se divide por rol. Cada usuario ve **solo** las opciones de su rol (menú lateral filtrado por la matriz de
permisos y la segregación de funciones del MOF y de CI-FIN-10).

| Rol | Manual | Quién es |
|---|---|---|
| GG | [Gerencia General](GG.md) | Gerente General (y sus suplentes del CA, CI-FIN-17) |
| JF | [Jefe de Finanzas y Comercialización](JF.md) | Contador general, cajero por designación |
| UT | [Unidad Técnica](UT.md) | Encargado de Granja (1 por cada 3.000 aves) |
| CA | [Consejo de Administración](CA.md) | Presidente, Secretario y Tesorero del CA |
| CV | [Consejo de Vigilancia](CV.md) | Miembros del CV |
| AG | [Asamblea General](AG.md) | Secretaría de la Asamblea |

## Lo común a todos los roles

### Abrir el sistema
1. Doble clic en **`iniciar_erp.bat`** (la primera vez instala todo). Se abre el navegador en `http://localhost:8501`.
2. Escriba su **usuario** y **contraseña** y pulse **Ingresar**.
3. Si su contraseña es temporal o tiene **90 días** o más (MAN-ARC-01 §5.2), el sistema le pide cambiarla antes de
   continuar: mínimo 8 caracteres, con letras y números, distinta de la anterior.

### Reglas de acceso
- **3 intentos fallidos** bloquean el usuario **15 minutos**; la GG puede restablecer la contraseña antes.
- La sesión se cierra sola tras **30 minutos** sin actividad.
- Todas las operaciones quedan en la **bitácora de auditoría** (usuario, rol, fecha, tabla, acción, valores antes y
  después). Nada se borra: los asientos se **reversan**, las vigencias de parámetros se **cierran**.
- Los valores citados en este manual (plazos, montos, porcentajes) son los vigentes al entregar el sistema; el valor
  que rige siempre es el de **Administración · Parámetros normativos**.

### Montos, fechas y documentos
- Montos en `Bs 1.234,56`; fechas `dd/mm/aaaa`. En los formularios se escribe con punto decimal (`1234.56`).
- Cada formato (FIN-xx, INF-CV-xx, ACTA-RES, REV-SEG) se descarga en PDF con el botón **Descargar … (PDF)** de la
  pestaña **Formatos** o de la propia pantalla; algunos también en Excel.

### Cuando aparece un mensaje en rojo
El sistema explica qué regla no se cumple y qué hacer (por ejemplo, «Las órdenes de compra se emiten únicamente los
lunes (PR-FIN-03)»). No es una falla del sistema: corrija el dato o pida la autorización indicada.

## Puesta en marcha (una sola vez)
1. **Configuración inicial:** al abrir por primera vez, cree el usuario de la Gerencia General (socio n.º 1).
2. La GG crea los demás usuarios en **Administración · Usuarios** y vincula a cada miembro de los Consejos con su socio.
3. **Datos del POA:** en **Presupuesto y POA → Flujo POA vs real** pulse *Cargar datos del POA 2026-2027* (o ejecute
   `python -m scripts.cargar_datos_poa`).
4. **Respaldos:** la GG configura el disco externo y la carpeta fuera de la granja en **Administración · Respaldos 3-2-1
   → Destinos**, y programa `respaldo_automatico.bat` en el Programador de tareas de Windows (diario, 19:00).
5. Revise **Administración · Revisión de seguridad** y deje todos los controles en verde.
