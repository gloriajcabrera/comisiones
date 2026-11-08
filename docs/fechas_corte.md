# Fechas de corte

Las fechas de corte tienen un flujo persistente independiente del archivo usado
para cargarlas:

`BORRADOR -> PENDIENTE_VERIFICACION -> VERIFICADO -> PENDIENTE_APROBACION -> APROBADO`

El Revisor puede registrar manualmente o cargar un DataFrame proveniente de CSV o
Excel. El Parametrizador aprueba. Solo las versiones aprobadas se entregan al
motor mediante `load_approved_cuts()`.

Cada fila conserva inicio, fin y periodo de corte. Se rechazan fechas inválidas,
periodos duplicados, rangos invertidos y solapamientos, incluso cuando el conflicto
aparece entre una actualización parcial y una versión aprobada anterior.

SQLite está disponible para pruebas y PostgreSQL para producción. Los archivos
son entradas funcionales opcionales; no son la fuente activa ni un puente entre
el cálculo y la aplicación.

La entrada productiva `CommissionCalculationService.execute_approved_inputs()`
obtiene pisos, metas y cortes directamente de sus repositorios aprobados. El
método que recibe DataFrames explícitos permanece para pruebas automatizadas y
comparaciones de regresión, no para la ejecución normal de la aplicación.
