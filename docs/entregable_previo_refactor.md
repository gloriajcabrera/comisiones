# Entregable previo al refactor productivo

Fecha de corte: 2026-10-20.

## 1. Proposito y conclusion

Este documento consolida el analisis obligatorio anterior a cambios estructurales
del sistema de comisiones. La evaluacion distingue entre componentes existentes,
prototipos transitorios y capacidades listas para produccion.

El proyecto tiene una base modular importante y 335 pruebas automatizadas
aprobadas. Sin embargo, aun no puede considerarse productivo: la regresion
financiera estricta puede omitirse cuando falta el bundle historico, PostgreSQL no
es todavia el repositorio operativo de todos los flujos, la interfaz usa datos de
demostracion y siguen coexistiendo el notebook y el puente de archivos anterior.

La prioridad es cerrar la referencia financiera, completar la extraccion del motor
y eliminar los puentes internos antes de ampliar funcionalidades.

## 2. Matriz consolidada de requisitos

Leyenda: `X` indica que la clasificacion aplica. Una capacidad parcial tambien
puede requerir refactor o una definicion funcional.

| Requisito | Ya existe | Existe parcialmente | Falta | Requiere refactor | Requiere definicion funcional | Evidencia en codigo |
|---|:---:|:---:|:---:|:---:|:---:|---|
| 1. Preservar el calculo actual |  | X |  | X |  | `comisionesia.ipynb`, `src/engine.py`, `tests/test_regression_baseline.py` |
| 1. Extraer logica productiva |  | X |  | X |  | `src/normalization.py`, `src/floor_compliance.py`, `src/payment_base.py`, `src/commission_rules.py` |
| 1. Sustituir Excel intermedio |  | X |  | X |  | `commission_app.py`; puente heredado en `local_review_app.py` y `review_bundle.py` |
| 1. Linux, PostgreSQL y ODBC de pruebas |  | X |  | X |  | `src/database.py`, `src/netsuite_provider.py` y `src/runtime.py`; falta validacion desplegada en Linux |
| 2. Ejecucion productiva sin Jupyter | X |  |  |  |  | `commission_app.py`, `src/runtime.py` y `src/commands.py` ejecutan sin notebook; las pruebas arquitectonicas impiden importar el puente heredado |
| 2. Separacion por capas |  | X |  | X |  | Modulos `src/`; algunos repositorios siguen ligados a SQLite |
| 2. Trazabilidad e historia |  | X |  | X |  | Historiales de estados y auditoria parcial |
| 2. Seguridad en backend |  | X |  | X |  | Identidad, permisos y alcance se validan en backend; solicitudes y excepciones comprueban ejecutivo + periodo + documento contra el calculo aprobado; faltan E2E desplegadas |
| 2. Filtrar y ordenar antes de paginar | X |  |  |  |  | `src/result_queries.py`, `tests/test_result_queries.py` |
| 3. Autenticacion AD/LDAPS |  | X |  | X | X | Adaptador `ldap3` compatible con Linux y configuración externa; falta validar contra el AD corporativo real |
| 3. Perfil interno de usuario | X |  |  |  |  | Perfil completo, roles múltiples, estructura vigente, estado laboral y acceso en `src/access_control.py` |
| 3. Alta y busqueda en AD | X |  |  |  | X | Búsqueda LDAP opcional con cuenta técnica externa, permiso backend y precarga revisable; falta validar atributos contra el AD corporativo real |
| 3. Autenticacion local separable | X |  |  |  |  | Proveedor local con hash en memoria y bloqueo interno obligatorio en producción |
| 4. Roles muchos-a-muchos | X |  |  |  |  | `user_roles`, `role_permissions` y persistencia SQLite/PostgreSQL en `src/access_control.py` |
| 4. Alcance por rol y jerarquia | X |  |  | X |  | `AuthorizationService`, `ApplicationSessionService` y UI aplican alcance efectivo sin confiar en el selector visual |
| 5. Jerarquia con vigencias | X |  |  |  |  | `hierarchy_assignments` y `HierarchyService.reassign` preservan historia, validan tipos y resuelven alcance por fecha |
| 6. Pisos/metas y aprobaciones | X |  |  |  |  | Registro manual/archivo, lotes y transiciones conectados a la UI y repositorios SQLite/PostgreSQL |
| 6. Versionamiento de cargas | X |  |  | X |  | Lotes, filas, errores, origen, conteos, fechas e historial consultables desde la UI |
| 7. Solicitudes cabecera/detalle | X |  |  | X |  | Creación múltiple, bandeja por alcance y transiciones con concepto integradas a la UI productiva |
| 7. Excepciones aprobadas | X |  |  | X |  | Propuesta, envío, bandeja, aprobación/rechazo y efecto financiero integrados al motor y la UI |
| 8. Parametros versionados |  | X |  | X | X | `src/rule_parameters.py`; falta catalogo completo y regla del 95 % |
| 9. Motor modular de comisiones |  | X |  | X |  | `src/engine.py`, `src/calculation_service.py` |
| 9. Regresion obligatoria | X |  |  |  |  | Referencia financiera modular y contrato historico versionado se validan siempre; la comparacion con el bundle mutable es solo diagnostica |
| 10. Consulta completa de cumplimiento | X |  |  | X |  | `src/reporting.py` y `src/result_queries.py`; detalle, fechas, solicitudes y alcance backend |
| 10. Consulta completa de pagos | X |  |  | X |  | Trazabilidad, solicitudes, descuentos aplicados y valor neto por ejecutivo-periodo integrados |
| 11. Filtros combinables y paginacion global | X |  |  | X |  | Pagos, resumen y detalle documental filtran y ordenan antes de paginar |
| 12. Periodos, cierre y reapertura | X |  |  | X |  | Flujo, permisos, motivo obligatorio, historial y consultas implementados en `src/business_periods.py`; periodo validado uniformemente en backend |
| 12. Recalculo idempotente | X |  |  | X |  | Firma incluye entradas y excepciones; un cierre permite consultar la misma huella, pero exige reapertura auditada para crear un cálculo diferente |
| 13. Descuento por anulacion sin reemplazo | X |  |  | X |  | Orquestado en `CommissionCalculationService` con deuda histórica e idempotencia; el contexto histórico se suministra explícitamente |
| 14. Ejecutivos retirados |  | X |  | X | X | Historia de estado existe; efecto financiero pendiente |
| 15. Proveedor desacoplado de NetSuite | X |  |  | X |  | `NetSuiteDataProvider`, `NetSuiteOdbcProvider`, `NetSuiteApiProvider` y seleccion externa mediante `NETSUITE_PROVIDER` |
| 15. Proveedor API REST SuiteQL |  | X |  | X | X | OAuth 1.0 HMAC-SHA256, paginacion, limites y reintentos implementados con biblioteca estandar; falta validar el rol y las consultas contra produccion |
| 16. Sincronizacion estable e incremental |  | X |  | X | X | `src/netsuite_sync.py`; snapshot idempotente con IDs estables y repositorios SQLite/PostgreSQL; falta definir y validar el criterio incremental disponible en la cuenta real |
| 16. Permiso independiente de sincronizacion | X |  |  |  |  | Ejecución e historial protegidos por `NETSUITE_SYNC_EXECUTE`, independiente del rol |
| 17. Excel solo como entrada/salida funcional | X |  |  | X |  | Runtime limitado por prueba arquitectónica a cargas funcionales y archivo final; vistas materializadas bajo demanda desde datos canónicos, sin tablas de detalle duplicadas |
| 17. Vistas PostgreSQL reutilizables |  | X |  | X |  | Vistas normales para solicitudes por documento y aplicaciones de descuentos; ampliar solo ante reutilización comprobada |
| 18. Eliminar orden de celdas/globales |  | X |  | X |  | Modulos nuevos; `src/notebook_bridge.py` es transitorio |
| 18. Logging productivo | X |  |  |  |  | `src/observability.py` centraliza formato JSON/texto y saneamiento; calculo, sincronizacion, envio y puente transitorio emiten eventos sin `print()` de diagnostico |
| 19. pandas vectorizado y SQL apropiado |  | X |  | X |  | Pisos/metas valida con mascaras y escritura batch; una prueba arquitectonica impide `SELECT *`; quedan mediciones y optimizaciones puntuales |
| 19. Indices y operaciones batch |  | X |  | X |  | Indices alineados con consultas de jerarquia, auditoria, revisiones, descuentos y archivos; falta medicion en PostgreSQL productivo |
| 20. Runtime Linux |  | X |  | X |  | Entrada productiva incluida en Git, unidad `systemd`, binding web configurable, respaldo PostgreSQL validado y pruebas sin rutas/imports exclusivos de Windows; falta validacion y despliegue real en Linux |
| 20. Dependencias minimas y separadas | X |  |  | X |  | Runtime, ODBC opcional, desarrollo y visor heredado separados; `pyarrow` y Jupyter quedan fuera de produccion |
| 21. Auditoria central append-only | X |  |  |  |  | `AuditQueryService` unifica historiales existentes con `AUDIT_VIEW`, filtros SQL y sin operaciones de edición o eliminación |
| 22. Archivo final versionado | X |  |  | X |  | Versiones, consultas, eventos y acciones separadas implementadas; Excel solo se genera en memoria bajo demanda |
| 22. Correo semiautomatico |  | X |  | X | X | Interfaz de proveedor y pruebas existen; proveedor real pendiente |
| 23. Calidad de datos por severidad |  | X |  | X | X | `src/data_quality.py` valida identidad, duplicados, fechas, importes, consistencia documental, ejecutivo, linea y pagos huerfanos; faltan contratos exhaustivos por proveedor |
| 24. Estados uniformes de procesos | X |  |  |  |  | Sincronizacion y calculo distinguen proceso, completado, completado con advertencias y error, con mensaje auditable |
| 25. Plan integral de pruebas |  | X |  | X |  | 335 pruebas aprobadas, incluida regresion financiera obligatoria, seguridad de documentos, respaldo y diagnostico REST; faltan E2E contra despliegue y validacion Linux |
| 26. Monolito modular sin infraestructura extra | X |  |  |  |  | `src/`; sin microservicios, colas ni caches externas |
| 27. PostgreSQL como fuente operativa |  | X |  | X |  | Todos los repositorios formales tienen implementacion PostgreSQL, el runtime la exige y la UI productiva lo consume; falta validación desplegada |
| 27. Conservacion historica sin borrado normal | X |  |  |  |  | Versiones, vigencias e inactivacion; prueba arquitectonica impide SQL destructivo en el runtime productivo |
| 28. Inventario previo verificable | X |  |  |  |  | Este documento y `docs/contratos_datos.md` |
| 29. Matriz minima de permisos | X |  |  | X |  | Constantes y asignaciones en `src/access_control.py` |
| 30. Estrategia por fases | X |  |  |  |  | Seccion 12 de este documento |
| 31. Criterios de aceptacion productivos |  | X |  | X |  | Ninguna liberacion productiva validada aun |
| 32. Entregable previo completo | X |  |  |  |  | Este documento |

## 3. Mapa de componentes

### Estado actual

```text
NetSuite -- REST SuiteQL/ODBC --> NetSuiteDataProvider --> NetSuiteDataset
                                               |
Entradas aprobadas (pisos/cortes) -------------+--> normalizacion
                                                    |
                                                    v
 relaciones documentales -> cumplimiento -> pagos -> comisiones
                                                    |
                       calculos/estados/repositorios SQLite/PostgreSQL parciales
                                                    |
                    servicios de consulta -> commission_app.py (runtime autenticado)

Ruta transitoria todavia existente:
comisionesia.ipynb -> Excel/Parquet/CSV -> local_review_app.py
```

### Responsabilidades existentes

| Capa | Componentes | Evaluacion |
|---|---|---|
| Integracion | `netsuite_queries.py`, `netsuite_provider.py`, `netsuite_sync.py` | REST SuiteQL y ODBC desacoplados; falta validacion real de la API e incrementalidad |
| Normalizacion | `normalization.py`, `document_relationships.py`, `replacement_chains.py`, `credit_effects.py` | Reutilizable; debe contrastarse por etapa contra notebook |
| Reglas | `floor_compliance.py`, `payment_base.py`, `commission_rules.py`, `cancellation_discounts.py`, `exception_rules.py` | Extraidas; excepciones aprobadas y descuentos por anulacion estan orquestados y probados |
| Calculo | `engine.py`, `calculation_service.py` | Orquestacion, idempotencia, advertencias y recálculo auditable implementados |
| Persistencia | `database.py` y repositorios por modulo | Mezcla PostgreSQL/SQLite; falta convergencia productiva |
| Aplicacion | servicios de flujos, consultas, archivos y `commission_app.py` | UI autenticada conectada al runtime; falta validacion E2E en el despliegue productivo |
| Legado transitorio | notebook, `review_bundle.py`, `local_review_app.py`, `notebook_bridge.py` | Debe retirarse del runtime despues de equivalencia financiera |

## 4. Inventario de datos y DataFrames

| Conjunto | Origen | Clave logica | Transformaciones principales | Consumidores | Destino |
|---|---|---|---|---|---|
| `invoices` / `f` | SuiteQL `INVOICES_SQL` | ID transaccion + linea | Tipos, moneda, documentos | Ventas, relaciones, pagos | Canonicamente sincronizado; DataFrame en calculo |
| `costs` | SuiteQL `COSTS_SQL` | transaccion + linea/lote | Exclusion sin lote | Cumplimiento de piso | Canonicamente sincronizado |
| `proformas` / `p` | SuiteQL `PROFORMAS_SQL` | ID transaccion + linea | Filtro proforma y servicio | Ventas, relaciones, pagos | Canonicamente sincronizado |
| `credit_notes` / `nc` | SuiteQL `CREDIT_NOTES_SQL` | ID NC + linea | Origen, reemplazo, valores | Anulaciones y cadenas | Canonicamente sincronizado |
| `proforma_cancellations` / `ncp` | SuiteQL de diarios | diario + linea | Solo valores de anulacion | Efectos de proforma | Canonicamente sincronizado |
| `invoice_payments` / `pff` | SuiteQL de vinculos | factura + pago + vinculo | Fecha real/contable, valor | Base oficial de pagos | Conviene persistir como evidencia |
| `proforma_payments` / `ppf` | SuiteQL deposito/pago | proforma + pago + vinculo | Fecha real/contable, valor | Base oficial de pagos | Conviene persistir como evidencia |
| `ventas_base` / `ventas_origen` | Documentos normalizados | documento + linea | NC, reemplazo, origen | Piso y pago | Intermedio reproducible |
| `ventas_piso` | Ventas y efectos | documento + linea | Suma/no suma/concepto | Cumplimiento, consulta | Resultado explicable |
| `pagos_documento` | Historias de pago | tipo + documento | Corte, saldos, fecha efectiva | Elegibilidad de pago | Evidencia/resultados |
| `cumplimiento_final` | Ventas, costos, metas | periodo + ejecutivo + linea | Meta, piso, obtenido | Reglas de pago, consulta | Resultado por ejecucion |
| `para_pago_base/full` | Cumplimiento + pagos | documento + linea | Factor, porcentaje, comision | Resumen y archivo final | Resultado por ejecucion |
| `resumen_pago` | Detalle de comisiones/descuentos | periodo + ejecutivo | Agregaciones | UI y archivo final | Vista/consulta derivada |

Los DataFrames intermedios no requieren una tabla individual. Se persisten los
datos canónicos, estados, versiones, ejecuciones, evidencia de pago cuando sea
necesaria para descuentos y resultados publicados que deban conservar historia.

## 5. Archivos Excel y puentes

| Uso | Archivo/codigo | Clasificacion | Accion |
|---|---|---|---|
| Pisos y metas | `pisos.xlsx`, `src/input_store.py` | Entrada funcional | Conservar carga XLSX/CSV versionada; persistir filas aprobadas |
| Fechas de corte | `periodos_corte_2025_2026.xlsx/.csv` | Entrada funcional | Conservar carga/manual; persistir estado aprobado |
| Referencias historicas | `reemplazosProfAnuladas.xlsx`, `comisionespagadas.xlsx` | Entrada heredada | Determinar sustituto NetSuite/BD antes de retirar |
| Resultados generales/anuales | Exportaciones del notebook | Puente o exportacion exploratoria | Retirar del runtime; ofrecer descarga bajo demanda si el negocio la solicita |
| Bundle de revision | `review_bundle.py` y `local_review_app.py` | Puente interno | Sustituir por servicios y consultas a PostgreSQL |
| Archivo final de pago | `src/payment_files.py` | Salida funcional | Conservar versionado y generado bajo accion autorizada |

`openpyxl` puede ser la unica dependencia Excel si cubre cargas funcionales y
archivo final. No se justifica agregar `xlsxwriter` mientras no exista una
necesidad que `openpyxl` no resuelva.

## 6. Inventario ODBC

El catalogo central esta en `src/netsuite_queries.py`:

1. `INVOICES_SQL`: facturas `CustInvc` y lineas comerciales.
2. `COSTS_SQL`: ordenes de compra activas para costo por lote.
3. `PROFORMAS_SQL`: `SalesOrd` marcadas como proforma.
4. `CREDIT_NOTES_SQL`: notas credito `CustCred`.
5. `PROFORMA_CANCELLATIONS_SQL`: diarios de anulacion de proformas.
6. `PROFORMA_PAYMENTS_SQL`: deposito relacionado y vinculos de pago.
7. `INVOICE_PAYMENTS_SQL`: pagos, diarios, aplicaciones y notas credito.

Pendientes: contratos exhaustivos de columnas, ID estable por registro,
paginacion/consulta incremental, fecha de ultima modificacion y reconciliacion de
conteos y valores por extraccion.

## 7. Reglas financieras actuales que deben congelarse

1. Facturas y proformas conservan su tipo y participan segun el comportamiento validado.
2. La fecha efectiva de pago prefiere fecha real y usa contabilizacion como respaldo.
3. Se reconocen los tipos de pago y vinculos actualmente incluidos en SuiteQL.
4. Moneda local y moneda de transaccion no se mezclan sin tasa/documento definido.
5. NC, anulaciones y reemplazos conservan documento origen, NC y documento vigente.
6. Un pago positivo por link impide inferir que dos facturas independientes son reemplazos.
7. En cadenas se conserva el periodo aplicable del origen y el valor pagado del documento vigente.
8. Si origen, NC y reemplazo comparten fecha, se rompe la cadena y el reemplazo se trata como original.
9. Cumplir RETIE/LAP habilita las demas lineas, incluido RITEL; cumplir solo RITEL habilita solo RITEL.
10. Para otras lineas, cumplir una principal habilita lineas no configuradas al porcentaje vigente.
11. Las lineas NA pueden sumar, pero su factor y valor final de comision son cero.
12. Sin pago recibido se muestra pendiente; factor/porcentaje cero produce pago en cero.
13. Proformas reemplazadas usan el pago real relacionado, no la fecha de emision del reemplazo.
14. Una anulacion sin reemplazo recalcula el piso historico y puede generar descuento total trazable.
15. La regla del ultimo pago inferior al 5 % permanece pendiente y no se activa sin definicion y regresion.

## 8. Pruebas de regresion

La suite inicial ejecutada el 2026-10-12 termino con `147 OK` y `1 skipped`. El
control omitido dependia de un bundle local mutable. Se incorporo entonces
`financial_reference_v1.json`: una referencia autocontenida que ejecuta siempre
el motor completo y fija entradas, etapas, cumplimiento, decisiones, fechas,
factores, comisiones y totales. La comparacion del bundle local queda como
diagnostico explicito porque corresponde a cortes cambiantes.

Al forzar la comparacion del bundle disponible contra el corte historico se
detectaron 16 diferencias, incluida `SM72210`, que cambio de `PAGA 100%` a
`NO PAGA`. La causa fue la propagacion de `NC5226` al reemplazo despues de cortar
la cadena por compartir fecha. El motor fue corregido para conservar la NC en el
origen anulado y reiniciar el reemplazo como documento original que suma. La
regresion especifica esta aprobada; queda pendiente regenerar de forma explicita
el mismo corte para reconciliar el bundle completo, sin sobrescribir la referencia.

Casos obligatorios para la referencia congelada:

| Caso | Comportamiento protegido |
|---|---|
| `SM72209 -> NC5226 -> SM72210` | Mismo periodo; paga origen con valor del reemplazo |
| `PR-9553 -> SM72586`, `RC-019486` | Fecha del recibo, no emision del reemplazo |
| `PR-9551 -> SM72791 -> NC5317 -> SM73590` | Ultimo reemplazo sin pago |
| `PR-8420`, `PR-9599` | Documento origen no se reemplaza a si mismo |
| `SM72083`, `SM70201` | Link de pago implica facturas independientes |
| `SM71824`, `SM60283` | Un pago positivo vinculado impide inferir reemplazo entre facturas independientes |
| `SM65917 -> NC5247 -> SM72582` | NC visible en campos y concepto |
| `SM59616` | Un ciclo se marca para revision sin interrumpir el calculo |
| `SM71487`, `SM71632`, `SM71061` | Habilitacion por linea principal/RETIE-LAP |
| Linea `NA` | Suma segun regla, comision final cero |
| Moneda extranjera | Comparacion coherente de total, pago y saldo |
| Sin pago, pago parcial y factor cero | Conceptos y valores distintos |
| Anulacion sin reemplazo | Recalculo y descuento total trazable |

Cada fixture debe fijar entradas, periodo, linea, documentos, pagos, NC, fechas,
dias, factor, porcentaje, comision, concepto y totales consolidados.

Las regresiones de pago distinguen expresamente tres situaciones: un documento
sin movimientos conserva vacios sus campos de pago; un pago parcial conserva el
movimiento recibido pero no crea fecha de pago completo ni comision; un factor
cero representa un pago recibido fuera de plazo y no un pago pendiente. La regla
propuesta del ultimo pago inferior al 5 % no altera todavia este comportamiento.

El caso equivalente a `PR-9553 -> SM72586 -> RC-019486` ya cuenta con una
regresion integral en `tests/test_engine.py`: valida base de comision original,
fuente de pago vigente, periodo original, vencimiento base y fechas real/contable
del recibo.

La cadena equivalente a `PR-9551 -> SM72791 -> NC5317 -> SM73590` tambien esta
protegida de extremo a extremo: la proforma permanece como base, la NC es visible,
el ultimo reemplazo es la fuente vigente y, al estar abierto y sin movimientos,
no muestra fechas de pago ni pasa a revision por la sola existencia de la NC; su
concepto es `Pendiente recibir pago de la factura`.

El caso equivalente a `SM70201` y `SM72083` tambien tiene regresion integral:
dos facturas semejantes con pagos positivos vinculados permanecen independientes,
con base, fuente de pago, piso y decision propios; el fallback no puede convertir
una en reemplazo de la otra.

La cadena equivalente a `SM65917 -> NC5247 -> SM72582` protege la visibilidad de
la NC en origen, reemplazo, resumen de anulaciones y observaciones. El reemplazo
usa su pago real, conserva la base original y no suma nuevamente al piso. En el
escenario congelado el plazo desde el vencimiento original produce factor cero y
`PAGO EN CERO`; mostrar la NC no puede alterar esa decision financiera.

Los casos equivalentes a `PR-8420` y `PR-9599` protegen a las proformas como
documentos origen: no pueden marcarse como reemplazo de si mismas, conservan su
base y suman en su periodo. Solo las facturas creadas posteriormente quedan como
reemplazos, no suman otra vez y aportan la fuente de pago de cada cadena.

Las reglas de lineas tambien cuentan con una regresion integral: RETIE/LAP
cumplida habilita RITEL, lineas no configuradas y NA. Las lineas pagables usan por
defecto 4 % para ejecutivo, 1 % para jefe y 2 % para gerente. NA conserva su
produccion para piso, pero su factor y las tres comisiones son cero. La prueba
unitaria complementaria confirma que RITEL cumplida por si sola no habilita las
demas lineas.

La moneda extranjera tiene una regresion integral con total tecnico de transaccion
100, tasa 4.000 y valor local 400.000. La conciliacion interna confirma el pago
completo y convierte una sola vez. Todas las salidas funcionales muestran moneda
local: documento y pago 400.000, saldo cero y comisiones de 16.000, 4.000 y 8.000
para ejecutivo, jefe y gerente. Los importes de transaccion no se exponen en UI,
reportes ni archivo final.

## 9. Modelo de datos minimo propuesto

El modelo evita replicar cada DataFrame y prioriza estados, fechas, historia y
evidencia imprescindible:

- Identidad: `users`, `roles`, `permissions`, `user_roles`, `role_permissions`.
- Jerarquia: `commercial_entities`, `hierarchy_assignments`, `employment_status_history`.
- Sincronizacion: `netsuite_sync_runs`, `netsuite_sync_errors`, `netsuite_sync_checkpoints` y almacenamiento canonico por ID estable.
- Insumos: `input_batches`, `input_batch_rows`, `input_batch_errors`, `targets_floors` versionado.
- Configuracion: `rule_definitions`, `rule_parameter_versions`, historiales de aprobacion.
- Operacion: `business_periods`, `calculation_runs` y referencias a version de datos/reglas.
- Evidencia financiera: pagos normalizados necesarios para trazabilidad y descuentos.
- Resultados publicados: cumplimiento, comisiones y descuentos por ejecucion cuando la historia no pueda reconstruirse sin alteracion.
- Flujos: solicitudes, detalles, excepciones, archivos de pago y sus historiales.
- Auditoria: `audit_events` append-only.

Vistas normales candidatas: cumplimiento vigente, detalle explicable de piso,
detalle explicable de pago, resumen de pago por periodo/ejecutivo, descuentos y
jerarquia vigente. No usar vistas materializadas hasta medir una necesidad.

## 10. Matriz de permisos

| Accion | Ejecutivo | Jefe | Gerente | Revisor | Parametrizador | Administrador |
|---|---|---|---|---|---|---|
| Consultar propia informacion | Si | Si | Si | Si | Si | Si |
| Consultar equipo vigente | No | Si | Si | Si | Si | Si |
| Consultar cualquier ejecutivo | No | No | Solo con rol adicional | Si | Si | Si |
| Crear solicitud | Si | Segun alcance | Segun alcance | Si | Si | Si |
| Registrar piso/meta | No | Si | No | No | Si configurable | Si |
| Verificar piso/meta | No | No | No | Si | Si | Si |
| Aprobar piso/meta | No | No | No | No | Si | Si |
| Cambiar parametros | No | No | No | No | Si | Si |
| Proponer excepcion | No | No | No | Si | Si | Si |
| Aprobar excepcion | No | No | No | No | Si | Si |
| Sincronizar NetSuite | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso |
| Generar archivo | No | No | No | Si | Si | Si |
| Descargar/enviar archivo | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso |
| Cerrar periodo | No | No | No | Si | Si | Si |
| Reabrir periodo | No | No | No | No | Por permiso | Si |
| Administrar usuarios | No | No | No | No | No | Si |

El backend resuelve identidad, permisos, jerarquia y vigencia. Un ID enviado por
la interfaz nunca constituye autorizacion.

## 11. Flujos de estados

- Insumo: `BORRADOR -> PENDIENTE_VERIFICACION -> VERIFICADO -> PENDIENTE_APROBACION -> APROBADO`.
- Solicitud: `SOLICITADA -> EN_REVISION -> PENDIENTE_APROBACION -> APROBADA/RECHAZADA -> RESUELTA -> CERRADA`.
- Excepcion: `PROPUESTA -> PENDIENTE_APROBACION -> APROBADA/RECHAZADA -> INACTIVA`.
- Periodo: `ABIERTO -> EN_REVISION -> FINALIZADO -> CERRADO`; reabrir exige motivo y auditoria.
- Proceso: `PENDIENTE -> EN_PROCESO -> COMPLETADO/COMPLETADO_CON_ADVERTENCIAS/ERROR`.
- Archivo: `GENERADO -> APROBADO -> ENVIADO`, conservando cada version y error de envio.

## 12. Estrategia de migracion controlada

### Fase 1: cerrar referencia financiera

1. Seleccionar un periodo representativo y conservar sus fuentes y parametros.
2. Convertir los casos conocidos en fixtures autocontenidos.
3. Hacer obligatoria la comparacion por documento, ejecutivo, linea, periodo y total.
4. Impedir que la prueba estricta se omita en el pipeline de aceptacion.

### Fase 2: completar el motor modular

1. Comparar salida por salida del notebook contra los modulos.
2. Mantener la cobertura de regresion de excepciones y descuentos ya integrados en la orquestacion e idempotencia.
3. Mantener el notebook solo como arnes temporal de contraste.
4. No modificar formulas ante diferencias; investigar y documentar primero.

### Fase 3: PostgreSQL y eliminacion de puentes

1. Completar repositorios PostgreSQL y migraciones.
2. Corregir sincronizacion para usar IDs estables y hash solo como deteccion de cambios.
3. Sustituir bundle/Excel interno por servicios, tablas y vistas.
4. Publicar calculos atomicamente y conservar ejecuciones/auditoria.

### Fase 4: seguridad y operacion

1. Conectar autenticacion local, permisos y jerarquia a todas las acciones.
2. Completar flujos de aprobacion, periodos, archivos y correo.
3. Integrar LDAP/LDAPS cuando se disponga de configuracion corporativa.
4. Probar instalacion, servicio, logs, proveedor NetSuite y recuperacion en Linux.

### Fase 5: validacion de la API

Validar `NetSuiteApiProvider` con el rol autorizado de produccion, primero mediante
una consulta minima de solo lectura y despues con cada consulta del catalogo. El
motor, los servicios y la interfaz no deben cambiar por el origen seleccionado.

## 13. Operaciones costosas y deuda tecnica

- Lecturas repetidas y exportaciones del notebook.
- Copias y normalizaciones repetidas entre etapas.
- Medicion de lotes de sincronizacion para decidir si sus escrituras por registro
  requieren una estrategia batch adicional sin perder metricas de cambios.
- Migracion controlada de las antiguas claves de staging basadas en hash hacia IDs estables.
- Repositorios con capacidades desiguales entre SQLite y PostgreSQL.
- Los indices operativos se derivaron de consultas existentes; falta medir planes,
  tiempos y volumenes en PostgreSQL antes de proponer optimizaciones adicionales.
- Interfaz de demostracion y aplicaciones heredadas coexistiendo con el motor.
- Comparacion diagnostica opcional con el bundle historico mutable; no forma
  parte de la regresion obligatoria ni del runtime productivo.

Antes de optimizar se deben medir tiempo, memoria, filas y consultas por etapa.
No se recomienda Redis, Celery, Kafka, RabbitMQ, Elasticsearch ni microservicios.

## 14. Definiciones funcionales pendientes

1. Regla exacta del umbral del 95 % y porcentaje configurable del ultimo pago.
2. Porcentajes vigentes y vacios para ejecutivo, jefe y gerente.
3. Efecto exacto del retiro sobre pagos personales posteriores.
4. Autoaprobacion de pisos/metas por parametrizador.
5. Ajustes permitidos en `FINALIZADO`: actualmente se bloquea el recálculo y
   solo `CERRADO` puede reabrirse directamente.
6. Configuracion corporativa de AD/LDAPS y correo.
7. Acceso real del rol API a tablas, campos y relaciones usados por las consultas
   actuales, junto con el criterio de sincronizacion incremental disponible.
8. Retencion fisica de archivos descargables sin borrar registros auditables.

## 15. Criterio de salida de esta etapa

Este entregable queda completo como diagnostico. El refactor financiero solo
debe avanzar cuando exista una referencia congelada obligatoria. El primer
incremento ejecutable es crear esa referencia, corregir la prueba omitida y
demostrar equivalencia antes de retirar el notebook o el puente heredado.
