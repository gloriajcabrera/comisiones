# Interfaz y reportes

## Fuente de datos

La interfaz nueva está en `commission_app.py`. No lee Excel, CSV, Parquet,
pickle, manifiestos ni paquetes generados por el notebook.

`InMemoryCalculationResultProvider` expone funciones sobre un único
`CommissionResult` canónico:

- `commission_results`: detalle de pagos y comisiones.
- `floor_results`: cumplimiento de pisos.
- `review_results`: filtro de pagos con decisión `PARA REVISAR`.
- `payment_summary`: agregación por ejecutivo y periodo, incorporando descuentos.

`Para revisión` y `Resumen` no son tablas ni archivos adicionales. Se derivan
al consultar, evitando duplicar información.

## Modo de demostración

`COMMISSION_APP_DEMO=true` habilita datos ficticios incluidos en código para
validar diseño, navegación, filtros y paginación. Está identificado visualmente
y no consulta archivos históricos. Sin esta variable, la aplicación se detiene
si no recibe un cálculo activo desde el servicio de aplicación.

El modo demo no sustituye autenticación ni seguridad. La integración productiva
debe inyectar el resultado calculado desde PostgreSQL/NetSuite sincronizado y
aplicar `ResultQueryService` para limitar el alcance por usuario.
