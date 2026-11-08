# Persistencia

## Decisión

La aplicación usa un contrato DB-API pequeño en `src/database.py`. SQLite sigue
siendo el motor local y de pruebas; PostgreSQL es el objetivo productivo. El
driver PostgreSQL se importa solamente al activar ese motor, por lo que no se
agrega una dependencia obligatoria durante esta etapa.

## Configuración

- `COMMISSIONS_DB_BACKEND=sqlite` usa `COMMISSIONS_SQLITE_PATH`.
- `COMMISSIONS_DB_BACKEND=postgresql` exige `COMMISSIONS_POSTGRES_DSN`.
- Ninguna credencial ni DSN debe almacenarse en el repositorio.

## Estado de migración

`BusinessPeriodRepository` ya funciona sobre el contrato común. Sus variantes
compatibles son `SqliteBusinessPeriodRepository` y
`PostgresBusinessPeriodRepository`. En PostgreSQL las transiciones bloquean el
periodo con `SELECT ... FOR UPDATE`.

`PersistentCalculationRepository` también usa el contrato común, con variantes
SQLite y PostgreSQL. Solo conserva código, periodo, versión de reglas, estado,
usuario, fechas y error; los resultados permanecen en memoria y no se duplican.
El inicio bloquea la tabla brevemente para impedir dos cálculos simultáneos.

`PersistentPaymentFileRepository` conserva únicamente la versión finalizada y
los eventos de descarga/envío. El resumen se obtiene bajo demanda y el Excel se
construye en memoria; no se persisten archivos ni filas duplicadas. La numeración
de versiones se protege contra concurrencia.

`PersistentRuleParameterRepository` conserva definiciones, versiones, vigencias
y cambios de estado. La creación y aprobación se serializan para impedir números
de versión duplicados y vigencias aprobadas superpuestas.

`PersistentCancellationDiscountRepository` conserva la deuda consolidada y sus
aplicaciones auditables. No replica los pagos de origen. PostgreSQL usa un tipo
decimal para los importes y serializa la aplicación del saldo.

`PersistentReviewRepository` conserva solicitudes, detalles, excepciones e
historiales de estado. Cabecera y detalle representan niveles funcionales
distintos; no se guardan copias completas de resultados calculados.

Los repositorios formales de acceso, insumos y sincronización usan el contrato
común y disponen de variantes SQLite y PostgreSQL seleccionadas por el runtime.

Las operaciones normales no eliminan físicamente información financiera o de
auditoría. Los cambios se representan mediante estado, vigencia, versión o
inactivación. Una prueba arquitectónica rechaza `DELETE FROM`, `DROP TABLE` y
`TRUNCATE TABLE` dentro de los módulos del runtime productivo.

## Persistencia mínima

No se crearán tablas para cada DataFrame. Se persisten datos canónicos, estados,
vigencias, auditoría, ejecuciones y la información financiera necesaria para
reconstruir descuentos. Los resultados derivados se consultan desde el motor o
una vista canónica cuando sea necesario.

## Respaldo y recuperación PostgreSQL

`deploy/postgresql/backup.sh` genera un archivo en formato custom mediante
`pg_dump`, comprueba que su catálogo pueda leerse con `pg_restore --list` y solo
entonces lo publica con su nombre definitivo. El script usa `umask 077`, no
imprime la cadena de conexión y no elimina respaldos anteriores.

La conexión de respaldo se configura con `COMMISSIONS_POSTGRES_BACKUP_DSN` sin
contraseña. La contraseña reside en el archivo señalado por `PGPASSFILE`, fuera
del repositorio, propiedad del usuario operativo y con permisos `0600`.

Ejemplo de ejecución manual o desde un temporizador del sistema:

```bash
set -a
. /etc/servimeters/comisiones.env
set +a
/opt/servimeters/comisiones/deploy/postgresql/backup.sh
```

`deploy/postgresql/verify-backup.sh ARCHIVO.dump` verifica que el contenedor sea
legible. Esta validación estructural no sustituye un ensayo de restauración. De
forma periódica se debe crear una base vacía y desechable, restaurar allí con
`pg_restore --exit-on-error --no-owner --no-privileges`, ejecutar las pruebas de
integridad y eliminar esa base únicamente después de documentar el resultado.
Nunca se debe ensayar una restauración directamente sobre producción.

La retención y copia externa de respaldos se definen operativamente. No se
incluye borrado automático porque la conservación requerida debe acordarse antes
de eliminar cualquier evidencia histórica.
