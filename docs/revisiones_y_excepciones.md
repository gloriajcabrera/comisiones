# Solicitudes de revision y excepciones

## Solicitudes

Una solicitud tiene cabecera por solicitante, ejecutivo y periodo, y uno o mas
detalles de factura. Cada detalle conserva observacion particular, resultado
previo, resultado final y concepto.

Estados:

`SOLICITADA -> EN_REVISION -> PENDIENTE_APROBACION -> APROBADA/RECHAZADA -> RESUELTA -> CERRADA`

El ejecutivo solo puede solicitar sobre sus documentos. Jefes y gerentes quedan
limitados por la jerarquia vigente del periodo. Revisor, Parametrizador y
Administrador administran el flujo mediante `REVIEW_REQUEST_MANAGE`.

## Excepciones

Las excepciones de piso aplican a ejecutivo y periodo. Las excepciones de pago
requieren ademas una factura concreta. No cambian reglas generales.

Las cuatro acciones financieras disponibles son:

- `FLOOR_FORCE_COMPLY`: dar por cumplido el piso del ejecutivo y periodo.
- `FLOOR_FORCE_INCLUDE`: cambiar una factura concreta de no suma a suma.
- `PAYMENT_FORCE_PAY`: habilitar el pago completo de una factura concreta.
- `COMMISSION_VALUE_EXECUTIVE`, `COMMISSION_VALUE_MANAGER` o
  `COMMISSION_VALUE_DIRECTOR`: fijar en moneda local el valor de comisión del rol
  indicado para una factura concreta.

`exception_type` conserva la categoría `PISO` o `PAGO`; `rule_code` identifica
la acción concreta. Esto evita duplicar flujos y mantiene una sola aprobación.

Estados:

`PROPUESTA -> PENDIENTE_APROBACION -> APROBADA/RECHAZADA`; una excepcion
aprobada puede pasar posteriormente a `INACTIVA`.

El Revisor propone y el Parametrizador aprueba o rechaza. Solo
`active_approved_exceptions()` entrega excepciones capaces de afectar un calculo.
Toda transicion conserva actor, fecha y concepto.

No pueden coexistir dos excepciones aprobadas para la misma regla, ejecutivo,
periodo y factura. La segunda aprobación se rechaza para impedir que el orden de
lectura determine un resultado financiero. Antes de proponer, el backend valida
el formato del periodo, el código de acción y, cuando corresponde, que el valor
de comisión sea numérico, finito y no negativo.

El servicio de cálculo consulta directamente las excepciones aprobadas. Las
propuestas, pendientes, rechazadas e inactivas no llegan al motor. El conjunto
aprobado forma parte de la clave de idempotencia de la ejecución.
