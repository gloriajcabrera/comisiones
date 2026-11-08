# Solicitudes de revisión y excepciones

## Solicitudes

Una solicitud tiene una cabecera con solicitante, ejecutivo, periodo, motivo
general y estado. Sus detalles conservan documento, observación particular,
resultado previo, resultado final y concepto. Esta separación no duplica datos:
permite una solicitud para varias facturas con contexto común y decisión propia
por documento.

Cada transición se registra en un historial inmutable con estado anterior,
nuevo estado, usuario, fecha y concepto. Las transiciones inválidas se rechazan.

## Excepciones

Las excepciones son puntuales. Una excepción de piso aplica al ejecutivo y
periodo; una excepción de pago exige además un documento. Solo una excepción
aprobada y activa puede afectar consultas o cálculos. Propuesta, aprobación,
rechazo y desactivación conservan historial.

La autorización y el alcance jerárquico se validan en el servicio de backend,
no mediante filtros de interfaz.

## Persistencia

`PersistentReviewRepository` funciona sobre SQLite y PostgreSQL. Las variantes
son `SqliteReviewRepository` y `PostgresReviewRepository`. Las transiciones se
serializan para impedir dos decisiones concurrentes sobre el mismo estado.
