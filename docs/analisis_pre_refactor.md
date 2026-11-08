# Analisis previo al refactor del sistema de comisiones

> Documento historico con fecha de corte 2026-10-08. La evaluacion consolidada
> y vigente se encuentra en `docs/entregable_previo_refactor.md`.

Fecha de corte del analisis: 2026-10-08.

## 1. Conclusion ejecutiva

El proyecto ya contiene un motor financiero amplio y funcional en `comisionesia.ipynb`, una aplicacion local de revision en Streamlit y componentes iniciales para versionar insumos y publicar resultados. Esa base debe reutilizarse; no conviene reescribir el calculo.

La solucion aun no cumple las condiciones para operar como aplicacion productiva. El calculo depende del orden de ejecucion y de DataFrames globales del notebook; la aplicacion consume un paquete Parquet/CSV generado por el notebook; no hay pruebas automatizadas; la identidad es un texto libre; no existen roles, permisos ni alcance jerarquico en backend; y el historial de insumos elimina versiones antiguas al superar cinco archivos.

La ruta segura es congelar primero los resultados actuales mediante pruebas de regresion, extraer por capas sin cambiar reglas, introducir persistencia relacional y, solo despues, sustituir el puente de archivos y el notebook como orquestador productivo.

## 2. Evidencia revisada

- `comisionesia.ipynb`: 50 celdas de codigo; JSON valido y todas las celdas compilan al 2026-10-08.
- `comisionesia.ipynb:129`, `:140`, `:170`: tres entradas Excel historicas.
- `comisionesia.ipynb:277`, `:2347`, `:4829`, `:6907`, `:8907`, `:9713`, `:11200`: siete ejecuciones ODBC/SuiteQL principales.
- `comisionesia.ipynb:26309-26390`, `:26660-26661`: exportaciones Excel funcionales y de resultados.
- `review_bundle.py`: serializa DataFrames en Parquet o CSV y crea un manifiesto JSON para la aplicacion.
- `local_review_app.py:746-810`: la aplicacion depende del paquete publicado desde el notebook.
- `local_review_app.py:79-100`: la unica entidad funcional propia es `approvals` en SQLite.
- `local_review_app.py:127-158`: la decision se actualiza mediante UPSERT; no existe historial de estados.
- `local_review_app.py:405-454`, `:811-850`: filtra el conjunto antes de paginar, comportamiento que debe conservarse.
- `local_review_app.py:876-890`: el usuario es texto libre; no hay autenticacion ni autorizacion.
- `src/input_store.py:79-149`: esquema inicial para versiones de insumos y ejecuciones.
- `src/input_store.py`: conserva versiones de insumos; el limite aplica solo a la cantidad mostrada.
- `src/notebook_bridge.py`: hidrata variables globales del notebook y mantiene el acoplamiento al contexto interactivo.
- `requirements-review-app.txt`: dependencias actuales limitadas a Streamlit, pandas, openpyxl y pyarrow.
- No se encontraron archivos de pruebas automatizadas.

## 3. Mapa actual de componentes

```text
NetSuite --ODBC/SuiteQL--> notebook
Excel de insumos --------> notebook
                            |
                            +--> DataFrames globales y reglas financieras
                            +--> Excel de resultados
                            +--> Parquet/CSV + manifest
                                      |
                                      v
                            Streamlit local de revision
                                      |
                                      v
                            SQLite: insumos y decisiones
```

Componentes identificados:

| Componente | Responsabilidad actual | Observacion |
|---|---|---|
| `comisionesia.ipynb` | Extraccion, normalizacion, reglas, calculo, diagnostico y exportacion | Demasiadas responsabilidades y dependencia del orden de celdas |
| `src/input_store.py` | Versiones de pisos/cortes y tablas preliminares de ejecucion | Transitorio; conserva historia y debe migrar su estado operativo a PostgreSQL |
| `src/input_loader.py` | Carga insumos activos | Reutilizable tras separar almacenamiento de interfaz |
| `src/notebook_bridge.py` | Inserta insumos en el espacio global del notebook | Adaptador transitorio, no debe quedar en produccion |
| `src/export_helpers.py` | Preparacion de tipos para exportar | Reutilizable solo en bordes de exportacion |
| `review_bundle.py` | Puente notebook-aplicacion mediante archivos | Debe sustituirse por repositorios/servicios y PostgreSQL |
| `local_review_app.py` | Consulta, filtros, paginacion, carga de insumos y decisiones | Prototipo funcional sin seguridad ni flujos formales |

## 4. Matriz de requisitos

Leyenda: `Si` marca la columna aplicable. Una fila puede existir parcialmente y requerir refactor al mismo tiempo.

| Requisito | Ya existe | Existe parcialmente | Falta | Requiere refactor | Requiere definicion funcional | Evidencia en codigo |
|---|---:|---:|---:|---:|---:|---|
| Calculo actual de comisiones | Si |  |  | Si |  | Celdas productivas 44-56 del notebook |
| Ejecucion sin Jupyter |  |  | Si | Si |  | Notebook es el orquestador principal |
| Separacion integracion/normalizacion/reglas/persistencia |  | Si |  | Si |  | Algunas utilidades en `src/`; reglas siguen en notebook |
| Proveedor abstracto de NetSuite |  |  | Si | Si |  | ODBC ejecutado directamente en notebook |
| Sincronizacion incremental e idempotente |  |  | Si | Si | Si | No hay repositorio sincronizado ni contrato API |
| PostgreSQL como fuente operativa |  | Si |  | Si |  | Contrato en `src/database.py`; periodos migrados, repositorios restantes pendientes |
| SQLite para pruebas | Si |  |  | Si |  | `app_data/review_state.db` |
| Eliminacion de Excel como puente interno |  | Si |  | Si |  | Exportacion Parquet/CSV; aun hay Excel y bundle intermedio |
| Excel como entrada/salida funcional | Si |  |  | Si |  | Pisos/cortes y archivos finales |
| Autenticacion AD local compatible con Linux |  |  | Si |  | Si | No existe autenticacion; faltan datos LDAP/LDAPS |
| Autenticacion local de pruebas separable |  |  | Si |  | Si | Usuario actual es texto libre |
| Perfil interno de usuario |  |  | Si |  |  | No existe entidad de usuario |
| Roles muchos-a-muchos |  |  | Si |  |  | No existen roles ni asociaciones |
| Permisos independientes |  |  | Si |  |  | No existe autorizacion de backend |
| Jerarquia con vigencias |  |  | Si |  |  | No existe modelo jerarquico persistido |
| Aislamiento del ejecutivo en backend |  |  | Si |  |  | Filtros UI sin control de alcance |
| Pisos/metas por ejecutivo, linea y periodo | Si |  |  | Si |  | DataFrame `pisos`; falta modelo versionado completo |
| Flujo de verificacion/aprobacion de insumos |  | Si |  | Si | Si | Existe decision simple, no flujo de estados |
| Historia completa de insumos |  | Si |  | Si |  | Se conservan solo cinco versiones |
| Solicitudes cabecera/detalle |  |  | Si |  |  | No existen entidades ni estados |
| Excepciones aprobadas |  |  | Si | Si |  | Reglas puntuales no estan modeladas como excepciones |
| Parametros versionados y vigentes |  |  | Si | Si | Si | Constantes permanecen en codigo/notebook |
| Consultas explicables hasta factura/regla |  | Si |  | Si |  | Hay detalle en DataFrames, no trazabilidad persistida |
| Filtros antes de paginar | Si |  |  |  |  | `apply_filters` y posterior `iloc` |
| Ordenamiento global antes de paginar |  | Si |  | Si |  | No hay servicio/query uniforme de ordenamiento |
| Periodos, cierre y reapertura |  |  | Si |  | Si | No existe flujo persistido |
| Recalculo idempotente y auditable |  |  | Si | Si |  | Tablas preliminares no utilizadas |
| Anulacion sin reemplazo y descuentos |  | Si |  | Si | Si | Relaciones NC existen; descuento historico no esta formalizado |
| Ejecutivos retirados con historia |  |  | Si |  | Si | No existe perfil/fecha efectiva de retiro |
| Auditoria central inmutable |  | Si |  | Si |  | Hay timestamps parciales; decisiones se sobrescriben |
| Archivo final de pago versionado |  | Si |  | Si | Si | Se genera Excel, sin ciclo de vida persistido |
| Correo semiautomatico desacoplado |  |  | Si |  | Si | No existe proveedor ni configuracion corporativa |
| Compatibilidad Linux |  | Si |  | Si |  | Python es portable; flujo actual depende de notebook/ODBC local |
| Secretos fuera del repositorio |  | Si |  | Si |  | Debe validarse configuracion integral antes de despliegue |
| Pruebas unitarias y de regresion |  |  | Si |  |  | No se encontraron pruebas |
| Validacion de datos por severidad |  | Si |  | Si | Si | Diagnosticos dispersos en notebook |

## 5. DataFrames y consultas principales

### Fuentes NetSuite

| Nombre | Significado observado | Consumidor principal |
|---|---|---|
| `f` | Lineas de facturas, fechas, cliente, clase, moneda y total | Base de ventas y documentos |
| `p` | Lineas de proformas con estructura equivalente | Base de ventas y documentos |
| `nc` | Notas credito de facturas y relaciones de reemplazo | Anulaciones, reemplazos y saldo |
| `ncp` | Notas credito/efectos asociados a proformas | Anulaciones y reemplazos |
| `pff` | Pagos vinculados a facturas | Base oficial de pagos |
| `ppf` | Pagos vinculados a proformas | Base oficial de pagos |
| `c` | Consulta auxiliar historica de cartera/documentos | Validaciones y comparaciones |

Las siete consultas se ejecutan directamente desde el notebook. Deben migrar sin cambiar su semantica a `NetSuiteOdbcProvider`; cada resultado requiere un contrato de columnas y tipos antes de implementar `NetSuiteApiProvider`.

### Transformaciones/resultados

| Conjunto | Proposito |
|---|---|
| `ventas_base` / `ventas_origen` | Normalizacion de documentos, origen, NC y reemplazos |
| `ventas_piso` | Estado de cada linea/documento para cumplimiento |
| `pagos_documento` | Base unificada de pagos de factura y proforma |
| `cumplimiento_final` | Cumplimiento por ejecutivo, linea y periodo |
| `para_pago_base` / `para_pago_full` | Reglas de elegibilidad, factor y comision |
| `detalle_reporte`, `cumpl_reporte`, `resumen_pago` | Salidas de consulta y pago |

No se recomienda crear una tabla por cada DataFrame. Los intermedios efimeros permanecen en pandas; fuentes normalizadas, ejecuciones, resultados, explicaciones y estados deben persistirse.

## 6. Reglas actuales que deben congelarse

Este inventario es descriptivo y no redefine las formulas:

1. Facturas y proformas se consideran documentos comerciales equivalentes para el calculo general, conservando su tipo.
2. La fecha oficial de pago usa `NVL(fecha_real, fecha_contabilizacion/trandate)` segun la consulta validada.
3. Se contemplan `CustPymt`, `Journal`, `CustCred` y `DepAppl` de acuerdo con los vinculos NetSuite existentes.
4. Los totales y saldos distinguen moneda de transaccion y moneda local mediante tasa de cambio.
5. Notas credito, anulaciones y cadenas de reemplazo conservan documento origen, NC y documento vigente.
6. Un pago positivo vinculado impide inferir reemplazo por la relacion auxiliar cuando son facturas independientes.
7. En reemplazos se conserva el periodo/origen aplicable y se usa el valor efectivamente pagado del documento vigente segun los casos validados.
8. Si origen, NC y reemplazo ocurren el mismo dia, se corta la cadena: la primera factura queda anulada y el reemplazo se trata como original.
9. `RETIE/LAP` cumplido habilita pago para otras lineas, incluso RITEL; RITEL cumplido por si solo habilita unicamente RITEL.
10. Para las demas lineas, cumplir una linea principal habilita lineas no configuradas al porcentaje por defecto vigente.
11. Las lineas `NA` suman donde corresponda, pero su factor y comision final son cero.
12. Debe diferenciarse documento pendiente de recibir pago de documento cuyo pago calculado es cero.
13. La regla de ultimo pago inferior al umbral configurable (inicialmente 5 %) fue definida conceptualmente, pero no debe activarse sin caso de regresion y confirmacion de alcance.
14. Los porcentajes de ejecutivo, jefe y gerente deben conservar lo implementado; cualquier vacio o ambiguedad financiera se documenta antes de cambiarlo.

## 7. Casos de regresion obligatorios

Crear fixtures pequenos a partir de datos anonimizados o snapshots controlados, con entradas y salidas esperadas:

| Caso | Comportamiento que protege |
|---|---|
| `SM72209 -> NC5226 -> SM72210` | Anulacion y reemplazo en el mismo periodo; paga origen con valor pagado del reemplazo |
| `PR-9553 -> SM72586`, pago `RC-019486` | La fecha de pago proviene del recibo, no de la emision de la factura reemplazo |
| `PR-9551 -> SM72791 -> NC5317 -> SM73590` | Cadena con factura anulada y reemplazo final aun sin pago |
| `PR-8420`, `PR-9599` | Un documento origen no puede marcarse como reemplazo de si mismo |
| `SM72083`, `SM70201` | Pagos por link hacen documentos independientes, no reemplazo inferido |
| `SM65917 -> NC5247 -> SM72582` | NC visible y trazable tanto en campos como en concepto |
| `SM71487`, `SM71632` | Linea sin configuracion habilitada por cumplimiento principal, sin falsa revision |
| `SM71061` | RETIE/LAP habilita RITEL aunque RITEL no cumpla por si solo |
| Linea `NA` | Produccion/piso segun regla, comision final cero |
| Documento en moneda extranjera | Total, pago y saldo comparados en la moneda correcta |
| Pago parcial y pago cero | Conceptos distintos: pendiente por recibir vs pago en cero |
| Anulacion sin reemplazo | Recalculo del periodo original y descuento total trazable |

Cada prueba debe fijar: documentos incluidos, periodo, linea, cumplimiento, valor base, pagos/NC, fecha efectiva, dias, factor, comision, motivo y total consolidado.

## 8. Arquitectura objetivo

Se recomienda un monolito modular, no microservicios:

```text
NetSuiteDataProvider
  +-- NetSuiteOdbcProvider (desarrollo/transicion)
  +-- NetSuiteApiProvider  (produccion futura)
             |
             v
Sincronizacion -> normalizacion -> PostgreSQL
                                     |
              motor de reglas/calculo por periodo
                                     |
                    resultados + explicaciones
                                     |
          servicios de aplicacion y autorizacion
                                     |
                  interfaz web / exportaciones
```

Modulos propuestos:

- `integrations.netsuite`: contratos, ODBC, API futura y checkpoints.
- `normalization`: esquemas, tipos, claves y relaciones documentales.
- `domain`: reglas puras de piso, cumplimiento, pago, reemplazos, NC, factores y descuentos.
- `calculation`: orquestacion idempotente por periodo y version de reglas.
- `repositories`: persistencia PostgreSQL/SQLite sin logica financiera.
- `application`: sincronizacion, solicitudes, aprobaciones, excepciones, cierres y archivos.
- `auth`: AD/local, usuarios, roles, permisos y alcance jerarquico.
- `web`: presentacion; nunca decide autorizacion ni reglas financieras.

## 9. Modelo de datos propuesto

### Identidad y autorizacion

- `users`, `roles`, `permissions`, `user_roles`, `role_permissions`.
- `commercial_entities` para ejecutivo/jefe/gerente vinculables al usuario.
- `hierarchy_assignments` con origen, destino, tipo, inicio, fin y auditoria.
- `employment_status_history` para estado y retiro efectivo.

### NetSuite normalizado

- `netsuite_transactions` para cabecera estable y moneda.
- `netsuite_transaction_lines` para servicio, lote, clase, centro y valores.
- `netsuite_transaction_links` para pagos, aplicaciones, NC y reemplazos.
- `netsuite_sync_runs`, `netsuite_sync_errors`, `netsuite_sync_checkpoints`.

### Configuracion e insumos

- `business_periods` y su historial de estados.
- `input_batches`, `input_batch_rows`, `input_batch_errors`.
- `targets_floors` versionado por ejecutivo, linea y vigencia.
- `rule_definitions`, `rule_parameter_versions` y aprobaciones.

### Calculo y trazabilidad

- `calculation_runs` con version de reglas, insumos y estado.
- `calculation_documents` con documento origen/vigente, resultado y concepto.
- `calculation_runs` conserva solo estado, fechas y referencias de cada ejecucion;
  el detalle se consulta mediante un proveedor sin duplicarlo.
- `calculation_payments` y `calculation_discounts`.
- `calculation_rule_traces` para regla aplicada, entrada y resultado.

### Flujos y auditoria

- `review_requests`, `review_request_items`, `review_request_status_history`.
- `exceptions`, `exception_status_history`.
- `approval_tasks`, `approval_status_history`.
- `payment_files`, `payment_file_deliveries`.
- `audit_events` append-only con actor, accion, entidad, antes/despues, motivo y contexto.

SQLite puede implementar un subconjunto compatible para pruebas. PostgreSQL debe usar restricciones, claves unicas e indices por ID NetSuite, documento, periodo, ejecutivo, linea, jerarquia, fecha y estado.

## 10. Matriz minima de permisos implementable

| Accion | Ejecutivo | Jefe | Gerente | Revisor | Parametrizador | Administrador |
|---|---|---|---|---|---|---|
| Consultar propia informacion | Si | Si | Si | Si | Si | Si |
| Consultar equipo vigente | No | Si | Si | Si | Si | Si |
| Consultar cualquier ejecutivo | No | No | Solo con rol adicional | Si | Si | Si |
| Crear solicitud | Si | Segun alcance | Segun alcance | Si | Si | Si |
| Registrar piso/meta | No | Si | No | No | Si, configurable | Si |
| Verificar piso/meta | No | No | No | Si | Si | Si |
| Aprobar piso/meta | No | No | No | No | Si | Si |
| Cambiar parametros | No | No | No | No | Si | Si |
| Proponer excepcion | No | No | No | Si | Si | Si |
| Aprobar excepcion | No | No | No | No | Si | Si |
| Sincronizar NetSuite | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso |
| Generar archivo de pago | No | No | No | Si | Si | Si |
| Enviar archivo | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso | Por permiso |
| Cerrar periodo | No | No | No | Si | Si | Si |
| Reabrir periodo | No | No | No | No | Por permiso | Si |
| Administrar usuarios | No | No | No | No | No | Si |

La autorizacion debe resolver usuario, roles, permisos, jerarquia vigente y periodo en backend. Los IDs recibidos de la interfaz solo son filtros, nunca prueba de acceso.

## 11. Flujos de estado

- Insumo: `BORRADOR -> PENDIENTE_VERIFICACION -> VERIFICADO -> PENDIENTE_APROBACION -> APROBADO`.
- Solicitud: `SOLICITADA -> EN_REVISION -> PENDIENTE_APROBACION -> APROBADA/RECHAZADA -> RESUELTA -> CERRADA`.
- Excepcion: `PROPUESTA -> PENDIENTE_APROBACION -> APROBADA/RECHAZADA -> INACTIVA`.
- Periodo: `ABIERTO -> EN_REVISION -> FINALIZADO -> CERRADO`; reapertura crea evento y motivo, no borra el cierre.
- Proceso: `PENDIENTE -> EN_PROCESO -> COMPLETADO/COMPLETADO_CON_ADVERTENCIAS/ERROR`.
- Archivo de pago: `GENERADO -> APROBADO -> ENVIADO` con versiones inmutables; los fallos se registran sin reemplazar envios previos.

Todo cambio de estado debe insertar historial; la cabecera puede mantener el estado actual como proyeccion, pero nunca ser la unica evidencia.

## 12. Estrategia de migracion

### Fase 0: congelar la referencia

1. Definir un periodo de referencia y capturar fuentes, parametros y resultados actuales.
2. Crear los casos de regresion anteriores y reconciliar totales agregados.
3. Documentar contratos de columnas de `f`, `p`, `nc`, `ncp`, `pff` y `ppf`.
4. Separar exportaciones funcionales de archivos usados como puente.

### Fase 1: extraer sin cambiar resultados

1. Extraer consultas a `NetSuiteOdbcProvider`.
2. Extraer normalizacion y reglas en funciones puras por bloques.
3. Mantener el notebook como arnes temporal que llama los nuevos modulos.
4. Comparar cada etapa y el total final contra la referencia.

### Fase 2: persistencia y ejecucion

1. Introducir repositorios SQLite/PostgreSQL y migraciones de esquema.
2. Persistir fuentes normalizadas, ejecuciones, resultados y trazas.
3. Crear comando/servicio de sincronizacion y calculo idempotente.
4. Cambiar Streamlit para consultar servicios/repositorios, no bundles.

### Fase 3: seguridad y flujos

1. Implementar autenticacion local de pruebas y luego AD/LDAPS.
2. Implementar usuarios, roles, permisos y jerarquia vigente.
3. Incorporar solicitudes, aprobaciones, excepciones, cierres y auditoria.
4. Probar acceso directo a servicios/endpoints con identidades de cada rol.

### Fase 4: operacion productiva

1. Versionar archivo de pago y envio semiautomatico.
2. Ejecutar en Linux con configuracion y secretos externos.
3. Preparar `NetSuiteApiProvider` solo cuando exista contrato real.
4. Retirar notebook y bundles del runtime productivo despues de la aceptacion financiera.

## 13. Definiciones pendientes antes de implementar

Estas decisiones no deben inferirse porque pueden afectar seguridad, operacion o dinero:

- Datos reales de AD/LDAPS, atributos disponibles y politica de TLS.
- Framework web definitivo y mecanismo de sesiones.
- Regla exacta y vigente de porcentajes para ejecutivo, jefe y gerente.
- Alcance final de la regla parametrizable del pago que completa el 95 %.
- Tratamiento exacto de nuevos pagos para ejecutivos retirados.
- Quien puede autoaprobar pisos/metas y bajo que configuracion.
- Reglas de reapertura y ajustes de periodos cerrados.
- Proveedor, remitente y controles del correo corporativo.
- Contrato real de la API NetSuite, cuando este disponible.
- Politica de retencion de archivos fisicos; los registros financieros y de auditoria no se eliminan.

## 14. Riesgos prioritarios

1. Cambiar reglas mientras se extraen del notebook sin pruebas de regresion.
2. Perder historia por el limite actual de cinco versiones.
3. Considerar los filtros de interfaz como seguridad.
4. Llevar a PostgreSQL todos los DataFrames sin distinguir estado persistente de intermedios.
5. Mantener dos motores de calculo durante la migracion.
6. Confundir exportacion funcional Excel con puente interno Excel/archivo.
7. Implementar la API NetSuite antes de disponer de su contrato.

## 15. Primer incremento recomendado

El primer incremento no debe incluir pantallas nuevas ni cambios de formula. Debe producir:

1. Contratos de datos para las seis fuentes principales.
2. Fixtures de regresion con los casos conocidos.
3. Un modulo de normalizacion puro y probado.
4. Un `NetSuiteDataProvider` con implementacion ODBC que entregue los mismos DataFrames.
5. Comparacion automatica de resultados por documento, ejecutivo, linea, periodo y total.

Solo cuando esa comparacion sea equivalente se extrae el siguiente bloque financiero.
