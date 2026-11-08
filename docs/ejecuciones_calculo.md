# Ejecuciones de cálculo

`calculation_runs` conserva la identidad del proceso, periodo, versión de
reglas, estado, usuario, fechas y mensaje. `calculation_run_metrics` mantiene
una única fila por ejecución con cantidades de comisiones, cumplimientos,
documentos de pago, descuentos, advertencias y errores.

Esta persistencia es metadato operativo y de auditoría. No guarda copias de
facturas, líneas, DataFrames ni resultados financieros. La finalización del
proceso y sus métricas se escriben en la misma transacción.

Cuando una ejecución completada se reutiliza por idempotencia, el servicio
devuelve los conteos y advertencias originales. Una ejecución fallida registra
`error_count = 1` y mantiene en cero los conteos no producidos.

La consulta administrativa filtra por periodo y estado antes de paginar. Los
indices sobre `period + status + started_at` y `status + started_at` respaldan
esa consulta y la deteccion de una ejecucion activa sin agregar cache externa.

La interfaz separa consultar de recalcular. La consulta reconstruye en memoria
solo si ya existe una ejecución completada con la misma huella de datos,
parámetros, excepciones e insumos aprobados. Si la huella cambió, no crea una
ejecución y solicita un recálculo. El recálculo exige el permiso independiente
`CALCULATION_EXECUTE` y un periodo explícito en formato `AAAA-MM`.

Un periodo cerrado puede reutilizar un resultado completado con la misma
huella porque esa consulta no modifica información. Cualquier cálculo nuevo o
con insumos diferentes exige que el periodo esté abierto o en revisión; después
del cierre debe existir una reapertura explícita, motivada y auditada.
