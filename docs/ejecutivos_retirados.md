# Ejecutivos retirados

El estado laboral se conserva como una secuencia mínima de eventos efectivos:
usuario, estado `ACTIVO` o `RETIRADO`, fecha efectiva, usuario que registra,
fecha técnica y motivo. No se copian ventas, comisiones ni datos del ejecutivo.

El estado laboral es independiente de `is_active`, que controla el acceso a la
aplicación. Registrar un retiro no elimina al usuario ni desactiva su identidad
automáticamente. La operación requiere `USERS_MANAGE` y queda en la auditoría
central.

La consulta histórica toma el último evento vigente en la fecha solicitada; si
no existe ningún evento, el estado se considera `ACTIVO`.

Por ahora este estado no cambia valores financieros. Falta definir si el corte
para impedir un nuevo pago personal se evalúa con fecha de factura, fecha real
de pago o periodo de nómina. Hasta resolverlo, la producción del retirado y las
comisiones de jefe y gerente permanecen intactas y no se inventa una regla.
