# Consultas funcionales

`ResultQueryService` consulta resultados mediante `CalculationResultProvider`.
No exige ni crea tablas duplicadas de cumplimiento o comisión. El proveedor
puede entregar el resultado activo en memoria o una vista canónica de
PostgreSQL. La autorización se resuelve en backend con la jerarquía vigente en
la fecha consultada.

## Seguridad

- Un ejecutivo obtiene solo sus registros aunque omita filtros en la solicitud.
- Jefes y gerentes quedan limitados a su alcance jerárquico vigente.
- Revisor, parametrizador y administrador pueden tener alcance global según sus
  permisos efectivos.
- Solicitar explícitamente un ejecutivo fuera del alcance genera denegación; no
  se confía en IDs ni filtros enviados por la interfaz.

## Filtros y paginación

Los filtros por ejecutivo, periodo, documento, línea, decisión y cumplimiento se
aplican sobre el conjunto completo antes del conteo y de la paginación. El
ordenamiento también se ejecuta antes de cortar la página.

El detalle documental de cumplimiento permite además filtrar por documento
origen, nota crédito, documento de reemplazo y si suma o no al piso. Esta vista
aplica el mismo alcance jerárquico de backend que pagos y cumplimiento resumido.

Las columnas permitidas para ordenar están en listas blancas separadas por tipo
de consulta. El tamaño de página está limitado entre 1 y 500 registros. Cada
respuesta informa total, página, tamaño y cantidad total de páginas.

El servicio devuelve directamente las columnas del proveedor, sin una copia
adicional en `payload_json`.

## Trazabilidad de documentos

El detalle de cumplimiento expone documento y fecha, documento origen y fecha,
nota crédito y fecha, reemplazo y fecha, lote, servicio, línea, valor local,
indicación de si suma y su motivo. El detalle de pagos añade documento de pago,
tipo, valor pagado local, fecha real, fecha contable, días, factor, decisión y
motivo.

Cuando no existe evidencia de pago, valor pagado, fechas, días y factor se
devuelven vacíos. Un cero se conserva únicamente si existe una operación y la
regla aplicada produjo explícitamente porcentaje o factor cero.

Ambas vistas pueden incorporar la solicitud de revisión más reciente por
ejecutivo, periodo y documento, incluyendo ID, estado y concepto final. Esta
información se consulta desde las tablas de flujo e historia; no se persiste una
copia adicional del resultado financiero.

## Descuentos

El resumen permite abrir una vista separada de descuentos. Esta consulta
conserva el alcance jerárquico y muestra periodo original, periodo de detección,
facturas anuladas, notas crédito, comisiones afectadas, deuda total, valor ya
aplicado y saldo pendiente. Una deuda pendiente no se resta del pago hasta que
exista una aplicación registrada para un periodo de pago.

El valor a pagar del resumen se calcula como comisión menos aplicaciones de
descuento del mismo ejecutivo y periodo de pago. El saldo total de una deuda no
se descuenta anticipadamente ni se confunde con el importe efectivamente
aplicado en ese periodo.

## Vistas operativas

Las relaciones reutilizadas se consultan mediante vistas normales, no
materializadas: `vw_review_document_context` obtiene la solicitud más reciente
por factura y `vw_discount_applications` relaciona cada aplicación con su deuda
de origen. Las vistas no duplican datos ni requieren procesos de refresco y son
compatibles con SQLite y PostgreSQL.

## Moneda

Todas las columnas monetarias de las vistas funcionales se expresan en moneda
local: valor del documento, valor obtenido, meta, piso, pago, saldo, comisiones,
descuentos y valor a pagar. La moneda de transaccion y la tasa documental no se
exponen como valores operativos; se usan internamente para reconciliar pagos y
convertir una sola vez a moneda local.
