# Integracion con NetSuite

## Contrato actual

`src.netsuite_provider.NetSuiteDataProvider` es el limite entre NetSuite y el
motor financiero. El calculo recibe un `NetSuiteDataset` con siete DataFrames:

- facturas;
- costos;
- proformas;
- notas credito;
- anulaciones de proformas;
- pagos de facturas;
- pagos de proformas.

Las implementaciones actuales son `NetSuiteOdbcProvider` y
`NetSuiteApiProvider`. Ambas reutilizan el mismo `QUERY_CATALOG`, por lo que el
motor recibe el mismo contrato sin conocer el transporte. Las consultas se conservan
en `src/netsuite_queries.py` y mantienen el universo funcional del notebook.
No deben importarse desde los modulos de reglas o calculo.

## Configuracion ODBC

La conexion se configura externamente:

```text
NETSUITE_ODBC_DSN=NetSuite2
NETSUITE_ODBC_USER=usuario-corporativo
NETSUITE_ODBC_PASSWORD=secreto
NETSUITE_ODBC_TIMEOUT_SECONDS=20
```

La API REST SuiteQL se selecciona con:

```text
NETSUITE_PROVIDER=api
NETSUITE_ACCOUNT_ID=<cuenta>
NETSUITE_CONSUMER_KEY=<secreto externo>
NETSUITE_CONSUMER_SECRET=<secreto externo>
NETSUITE_TOKEN_ID=<secreto externo>
NETSUITE_TOKEN_SECRET=<secreto externo>
NETSUITE_API_TIMEOUT_SECONDS=30
NETSUITE_API_PAGE_SIZE=1000
NETSUITE_API_MAX_RESULTS=100000
NETSUITE_API_MAX_RETRIES=3
```

El proveedor firma cada solicitud con OAuth 1.0 HMAC-SHA256, exige el encabezado
`Prefer: transient`, pagina mediante `limit` y `offset`, y reintenta únicamente
respuestas `429` y `5xx`. Errores de autenticación, permisos o consulta no se
reintentan. Los secretos se excluyen de representaciones y salidas operativas.

REST SuiteQL limita una consulta a 100.000 resultados. Una extracción mayor
debe dividirse por fechas u otra clave estable y reconciliarse fuera de
NetSuite; el proveedor genera un error explícito antes de exceder ese límite.

Estas variables no se guardan en el repositorio. El DSN mantiene la
configuracion propia del driver y puede variar entre Windows y Linux sin
cambiar las reglas de negocio.

Uso desde Python:

```python
from src.netsuite_provider import netsuite_provider_from_environment

dataset = netsuite_provider_from_environment().load_dataset()
```

## Validacion escalonada en produccion

Las credenciales deben configurarse directamente en el entorno del servidor o
en su archivo de entorno protegido. No deben pegarse en chats, comandos que
queden en el historial ni archivos versionados.

1. Configurar `NETSUITE_PROVIDER=api` y las cinco variables de autenticacion.
2. Validar la configuracion completa sin abrir conexiones:

   ```text
   python -m src.commands production-check
   ```

   Continuar solo si responde con `"status": "READY"`.

3. Probar autenticacion y permiso SuiteQL con una unica fila, sin sincronizar:

   ```text
   python -m src.commands netsuite-check
   ```

   Continuar solo si responde con `"status": "READY"` y
   `"mode": "authentication"`.

4. Validar una fila de cada consulta requerida por el calculo:

   ```text
   python -m src.commands netsuite-check --catalog
   ```

   La salida solo muestra nombre del conjunto, estado, cantidad de filas de
   muestra y cantidad de columnas. No muestra valores comerciales.

5. Revisar que los siete conjuntos aparezcan en estado `OK`. Ante un error de
   autenticacion, permisos o sintaxis se debe detener el proceso y corregir el
   rol o la consulta; no se debe iniciar una sincronizacion parcial.

6. Solo despues de aprobar los pasos anteriores, ejecutar una sincronizacion
   completa mediante un usuario interno con `NETSUITE_SYNC_EXECUTE`.

El comando de diagnostico usa exclusivamente solicitudes `POST` al endpoint de
consulta SuiteQL. No crea, modifica ni elimina registros en NetSuite, y tampoco
escribe en la base operativa de la aplicacion.

### Interpretacion de errores

| Codigo | Significado | Accion |
|---|---|---|
| `AUTHENTICATION_FAILED` | NetSuite rechazo el token o la firma | Revisar cuenta, integration record y token |
| `PERMISSION_DENIED` | El rol no puede consultar una tabla o campo | Ajustar permisos del rol; no cambiar la regla financiera |
| `QUERY_REJECTED` | SuiteQL rechazo la consulta indicada | Revisar compatibilidad de la consulta del conjunto reportado |
| `RATE_LIMITED` | NetSuite aplico limite temporal | Esperar y reintentar; el proveedor ya aplica reintentos acotados |
| `SERVICE_UNAVAILABLE` | NetSuite respondio con error temporal `5xx` | Reintentar despues; no sincronizar parcialmente |
| `CONFIGURATION_OR_CONNECTION_ERROR` | Faltan variables o no hay conectividad | Corregir entorno, DNS, TLS o red antes de continuar |

Los errores terminan con codigo de proceso `2`, conservan una salida JSON apta
para automatizacion y pasan por saneamiento para no exponer secretos.

## Limites de esta etapa

- El notebook aun no fue conectado al proveedor nuevo.
- El proveedor REST SuiteQL esta implementado sobre el contrato oficial, pero
  falta comprobar en produccion que el rol del token pueda ejecutar todas las
  consultas y acceder a sus tablas, campos y relaciones.
- La extraccion actual es completa desde 2021. `src.netsuite_sync` ya permite
  conservar fotografias completas de forma idempotente y auditable en SQLite.
  La consulta incremental depende del criterio de ultima modificacion que se
  valide para cada conjunto de datos en la cuenta real.
- La credencial que historicamente quedo escrita en el notebook debe retirarse
  y rotarse antes de publicar o desplegar el repositorio.

## Pruebas

Las pruebas usan conexiones y transporte HTTP simulados: no requieren NetSuite,
ODBC ni secretos. Verifican el contrato, la firma OAuth, paginacion, limites,
reintentos, ejecucion completa del catalogo, cierre de conexion y limpiezas que
hoy realiza el notebook.

## Staging de sincronizacion

`NetSuiteSyncService` coordina proveedor y repositorio. La implementacion
`SqliteNetSuiteSyncRepository` se usa en desarrollo y pruebas, conserva el JSON
original de cada fila y nunca elimina fisicamente registros ausentes: los marca
como inactivos. Una clave compuesta por contenido y numero de ocurrencia evita
colapsar aplicaciones duplicadas validas.

Cada ejecucion registra usuario, tipo, inicio, fin, estado y conteos de creados,
actualizados, sin cambio e inactivados. Solo se admite una sincronizacion en
estado `EN_PROCESO`. Esta tabla es staging auditable y no reemplaza el modelo
relacional operativo que se implementara para PostgreSQL.

El repositorio implementa tambien `NetSuiteDataProvider`. Por ello,
`calculate_from_provider()` puede ejecutar el motor sobre la fotografia activa
sin abrir una conexion a NetSuite. Las pantallas y consultas deben seguir este
camino; el proveedor NetSuite seleccionado se reserva para la sincronizacion
solicitada por un usuario autorizado.

## Ejecuciones de calculo

`CommissionCalculationService` ejecuta el motor y persiste en una transaccion
los resultados consultables de cumplimiento, pagos y comisiones. Cada corrida
registra usuario, periodo, version de reglas, estado, fechas y mensaje de error.

La clave de idempotencia incorpora la fotografia sincronizada completa, pisos,
metas, cortes, periodo y version de reglas. Si todo es identico se reutiliza la
corrida completada; si cambia cualquier insumo se crea una version nueva sin
alterar la anterior. Un error deja la cabecera en `ERROR` y no publica filas
parciales.
