# Conciliacion financiera entre cortes 2026-10-08 y 2026-10-09

## Conclusion

La comparacion no certifica equivalencia financiera porque los insumos no
corresponden al mismo corte. La referencia congelada fue generada con datos del
2026-10-08 y el paquete local disponible corresponde al 2026-10-09. Por esta
razon no se debe reemplazar el baseline ni clasificar todas las diferencias como
regresiones del motor.

## Diferencias detectadas

La comparacion produjo 16 diferencias. Las principales son:

| Resultado | Referencia 2026-10-08 | Paquete 2026-10-09 |
| --- | ---: | ---: |
| Filas de cumplimiento | 5.241 | 5.246 |
| Total para piso en cumplimiento | 158.244.558.037,74 | 158.042.158.126,21 |
| Filas de detalle de pago | 96.260 | 96.349 |
| Comision ejecutivos | 434.567.060,00 | 435.887.391,70 |
| Comision jefes | 103.798.085,10 | 104.338.216,10 |
| Comision gerentes | 220.355.740,60 | 221.436.002,60 |
| Filas para revision | 83.693 | 83.691 |

Estas variaciones agregadas requieren una nueva ejecucion con insumos del mismo
corte antes de atribuirlas a reglas o codigo.

## Caso SM72209 / NC5226 / SM72210

El paquete del 2026-10-09 corta correctamente la cadena de la factura original
`SM72209`, anulada por `NC5226` y reemplazada el mismo dia por `SM72210`. Sin
embargo, tambien conserva incorrectamente `NC5226` en `SM72210`. Como resultado,
el archivo heredado marca el reemplazo como `no suma` y `NO PAGA`, aunque tiene
pago registrado y debe tratarse como un nuevo documento origen.

El motor modular ya contiene la regla que limpia la nota credito heredada cuando
factura original, nota credito y reemplazo tienen la misma fecha. Se agrego una
prueba de regresion con los documentos y valores reales para impedir que
`SM72210` vuelva a heredar `NC5226` y para comprobar que suma 9.250.000 al piso.

## Condicion para certificar equivalencia

Se debe reconstruir la salida del motor modular y la salida de referencia usando
exactamente la misma extraccion de NetSuite, pisos, metas, fechas de corte y
parametros. Solo entonces se compararan filas, documentos, decisiones, factores,
comisiones y totales. Cualquier diferencia restante debera explicarse por caso
antes de aprobar el cambio.
