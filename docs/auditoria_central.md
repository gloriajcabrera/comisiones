# Auditoria central

`AuditQueryService` ofrece una lectura unificada y de solo consulta sobre los
historiales append-only ya existentes. No crea una copia de eventos ni permite
actualizarlos o eliminarlos.

## Seguridad

La consulta exige `AUDIT_VIEW` en backend. Inicialmente el permiso pertenece
solo al rol `ADMINISTRADOR`; ocultar la pantalla no sustituye esta validacion.

## Cobertura

- Usuarios, roles, permisos, estados laborales y jerarquia.
- Periodos y reaperturas.
- Solicitudes de revision y excepciones.
- Parametros, pisos, metas y fechas de corte.
- Sincronizaciones y calculos.
- Generacion, descarga y envio de archivos de pago.
- Creacion y aplicacion de descuentos por anulaciones.

Cada fila normaliza fecha UTC, actor, accion, tipo e identificador de entidad,
valor anterior, valor nuevo y motivo. Los datos financieros completos y los
destinatarios de correo no se replican en la consulta.

Los filtros por accion, entidad, actor, fechas y texto se aplican en SQL sobre
el conjunto completo. El conteo y el ordenamiento ocurren antes de `LIMIT` y
`OFFSET`; los campos de orden usan una lista blanca.

Cuando el limite `Hasta` se proporciona como fecha sin hora, incluye todos los
eventos de ese dia. Un timestamp explicito conserva su hora exacta como limite.
