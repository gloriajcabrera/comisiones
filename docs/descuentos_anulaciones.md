# Descuentos por anulaciones sin reemplazo

## Regla preservada

Una anulación posterior sin reemplazo genera deuda solamente cuando la factura
había contado en un periodo previamente cumplido, el recálculo hace perder el
piso y existen comisiones pagadas asociadas a las líneas afectadas. La deuda
incluye los valores históricos pagados a ejecutivo, jefe y gerente.

No se genera descuento cuando existe una factura de reemplazo o cuando el
ejecutivo conserva el cumplimiento después del recálculo.

## Persistencia mínima

Se conserva un registro consolidado por ejecutivo, periodo original, cálculo
original y recálculo. Incluye documentos anulados, notas crédito, líneas
afectadas, documentos pagados, componentes de comisión, deuda total, valor
aplicado, saldo y estado.

Cada aplicación conserva periodo de pago, valor, usuario, fecha y observación.
No se duplican los pagos originales ni se guarda un JSON paralelo.

## Portabilidad

`PersistentCancellationDiscountRepository` funciona con SQLite y PostgreSQL.
PostgreSQL usa `NUMERIC(20, 6)` para importes. Guardar de nuevo el mismo descuento
devuelve su ID existente; la aplicación del saldo se serializa para impedir
descuentos concurrentes superiores a la deuda.
