# Revisión de seguridad — ERP Pollolandia v1.0.0 (Fase 13)

La revisión es **automática y repetible**: **Administración · Revisión de seguridad** (GG y CV) ejecuta los controles
contra la base en uso y emite el informe REV-SEG en PDF (`servicios/revision_seguridad.py`).

## Requisitos verificados

| Requisito | Fuente | Implementación | Verificación |
|---|---|---|---|
| Usuario y contraseña personales | MAN-ARC-01 §5.2 | Un usuario por persona; los miembros de los Consejos se vinculan a su socio (un usuario por socio) | test_seguridad |
| Cambio de contraseña cada 90 días | MAN-ARC-01 §5.2 | `password_caducidad_dias` = 90; al día 90 el ingreso obliga a cambiarla | test_seguridad, test_revision_seguridad |
| Política de contraseñas | Fase 3 | Mínimo 8 caracteres, letras y números, distinta de la anterior; temporal obliga a cambiar | test_seguridad |
| Bloqueo por intentos | Fase 3 | 3 intentos → 15 minutos (`login_intentos_max`, `login_bloqueo_minutos`); mismo mensaje para usuario inexistente | test_seguridad, test_revision_seguridad |
| Cierre por inactividad | Fase 3 | 30 minutos (`sesion_inactividad_minutos`) | test_seguridad |
| Contraseñas cifradas | Fase 3 | bcrypt costo 12 (`config.BCRYPT_ROUNDS`); la revisión detecta hashes de costo menor | test_revision_seguridad |
| **CV solo lectura** | MAN-ARC-01 §5.2; MOF §3.4 | Rol con `solo_lectura_transaccional` + incompatibilidad que bloquea CREAR/EDITAR/REVERSAR/AUTORIZAR/APROBAR aunque alguien agregue el permiso; excepción: sus informes (vigilancia) | test_seguridad, test_revision_seguridad |
| Segregación de funciones | MOF §3.4; CI-FIN-10 | JF no autoriza ni firma; GG no registra asientos; UT no autoriza | test_seguridad |
| Registro de accesos | MAN-ARC-01 §5.2 | LOGIN/LOGOUT y rechazos en la bitácora | test_seguridad |
| Bitácora inmutable | Regla 5 | Disparadores contra UPDATE/DELETE | test_auditoria |
| Baja de usuarios al término del cargo | MAN-ARC-01 §5.2 | Desactivación (invalida la sesión abierta) | test_seguridad |
| Respaldos y prueba de restauración | MAN-ARC-01 §5.2 | 3-2-1 con huellas SHA-256 y acta con visto del CV | test_respaldo |
| Cambios de parámetros controlados | Regla 2; matriz | GG propone, CA/AG aprueban con acta; sin borrado de vigencias | test_parametros |

## Resultado sobre la base de demostración
Todos los controles en **verde**, salvo los que dependen de la operación (contraseñas temporales sin cambiar o
vencidas, usuarios bloqueados), que se muestran en amarillo con el nombre de cada usuario.

## Recomendaciones (ver riesgos R3 y R5)
1. Cifrar con BitLocker el equipo de la oficina y el disco externo de respaldos.
2. Usar el sistema en el equipo de la oficina; si se abre en la red local, publicar detrás de un proxy con HTTPS.
3. Ejecutar la revisión cada mes y archivar el REV-SEG firmado por la GG y visado por el CV.
