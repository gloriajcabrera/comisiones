# Periodos, cierres y reaperturas

El flujo implementado es:

`ABIERTO -> EN_REVISION -> FINALIZADO -> CERRADO`

Los usuarios con `PERIOD_CLOSE` pueden crear y avanzar periodos. La reapertura
desde `CERRADO` hasta `ABIERTO` exige `PERIOD_REOPEN` y un motivo no vacio.
Cada transición conserva estado anterior, estado nuevo, usuario, fecha y motivo.

Cuando el repositorio de periodos se conecta a `CommissionCalculationService`,
solo se permite recalcular periodos `ABIERTO` o `EN_REVISION`. Un periodo
`FINALIZADO` o `CERRADO` no admite recálculo. La reapertura explícita disponible
actualmente opera desde `CERRADO` hasta `ABIERTO`; el tratamiento de ajustes
directos sobre `FINALIZADO` permanece pendiente de definición funcional. La
integración es opcional durante la migración para no romper consumidores que
todavía no administran periodos en la base operativa.

La consulta puede reutilizar un cálculo completado con la misma huella de
datos porque no crea ni modifica resultados. Un cálculo nuevo, incluidos los
casos en que cambian insumos, parámetros o excepciones, exige reapertura. El
periodo es obligatorio y se valida en backend con formato `AAAA-MM`.
