# Sincronizacion de NetSuite

La identidad persistente se construye con IDs estables de NetSuite. El hash del
contenido solo detecta si una fila cambió. Por ello, modificar un valor actualiza
el registro existente y no crea otro registro seguido de una desactivación.

La persistencia usa un contrato transaccional común. SQLite queda disponible para
desarrollo y pruebas mediante `SqliteNetSuiteSyncRepository`; producción utiliza
`PostgresNetSuiteSyncRepository`. El servicio de sincronización y el motor no
conocen cuál implementación está activa.

Claves utilizadas:

- Documentos y costos: `id` de transacción + `line_id` de TransactionLine.
- Pagos de facturas: `factura_id` + `pago_id` + `tipo_pago`.
- Pagos de proformas: `proforma_id` + `deposito_id` + `pago_id` + `tipo_vinculo`.

Si una extracción no vacía carece de esos identificadores o contiene una clave
nula, la sincronización termina en `ERROR` y conserva el diagnóstico. No se
fabrica una identidad usando valores financieros mutables.

Las relaciones repetidas con la misma clave conservan un índice de ocurrencia.
La primera ejecución posterior a este cambio puede reemplazar las antiguas claves
basadas en hash; es una migración controlada del staging, no una duplicación de la
fuente operativa.

Cada snapshot se publica dentro de una transacción. Una falla revierte todas las
filas de esa captura y la ejecución queda en `ERROR`; tampoco se permiten dos
sincronizaciones activas simultáneamente.

`NetSuiteSyncService` exige siempre un usuario interno y valida en backend el
permiso independiente `NETSUITE_SYNC_EXECUTE` antes de crear la ejecución. El ID
del actor queda registrado en `netsuite_sync_runs.executed_by`. Ningún rol,
incluido Administrador, recibe este permiso automáticamente.
