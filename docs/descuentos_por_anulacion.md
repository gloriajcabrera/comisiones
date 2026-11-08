# Descuentos por anulacion sin reemplazo

## Regla implementada

El candidato nace de una factura o proforma anulada en un periodo posterior que
ya fue clasificada por el motor con `requiere_recalculo_piso = True` y que no
tiene documento de reemplazo.

El descuento solo se genera cuando una comparación explícita entre el cálculo
histórico y el recálculo demuestra que una combinación de ejecutivo, periodo y
línea pasó de cumplir a no cumplir. La deuda corresponde a las comisiones que
habían sido pagadas para las líneas cuyo cumplimiento se perdió.

Se conservan:

- Documentos anulados y notas crédito que originaron el recálculo.
- Periodo original y periodo de detección.
- Cálculo histórico y recálculo comparado.
- Líneas afectadas y documentos cuyas comisiones se revierten.
- Comisión de ejecutivo, jefe y gerente.
- Deuda total, valor aplicado, saldo y estado.

Estos elementos se guardan en columnas explícitas. No se conserva una copia
adicional del descuento completo en JSON.

## Aplicación

La creación es idempotente por ejecutivo, periodo original, cálculo fuente y
recálculo. Repetir el mismo proceso no duplica la deuda.

Las aplicaciones pueden ser parciales. Cada una registra periodo de pago,
valor, usuario, fecha y observación. La deuda total nunca se reduce ni se
reescribe; solo aumentan el valor aplicado y disminuye el saldo. No se permite
aplicar un valor superior al saldo pendiente.

Este componente no decide cuánto puede descontarse de una nómina concreta. Ese
límite y el manejo externo de saldos superiores a la comisión disponible están
fuera de su responsabilidad, conforme al alcance funcional.

## Integración con el cálculo

`CommissionCalculationService.execute` acepta un
`CancellationRecalculationContext` únicamente cuando se está comparando un
periodo histórico. El contexto exige el cumplimiento anterior, las comisiones
realmente pagadas, el periodo de detección y el identificador del cálculo
histórico.

La ejecución normal conserva el tratamiento original. En un recálculo por
anulación, los documentos marcados con `requiere_recalculo_piso` y sin reemplazo
pasan explícitamente a `no suma`; luego se reconstruyen cumplimiento y comisión.
Solo si la comparación demuestra pérdida de piso se crea y persiste una deuda.
Los insumos históricos forman parte de la clave de idempotencia para impedir que
comparaciones financieras diferentes reutilicen una ejecución anterior.

El resumen de pagos consume `list_applications(periodo)`, que expone solo los
valores ya aplicados con las columnas `executive`, `period` y `amount`. Una deuda
pendiente no se descuenta automáticamente y el periodo original se conserva como
trazabilidad separada.
