# Composicion del runtime

`src.runtime.build_runtime()` es el punto de composicion de la aplicacion fuera
de Jupyter. Construye repositorios y servicios con un unico backend operativo.

## Desarrollo y pruebas

```text
COMMISSIONS_ENV=development
COMMISSIONS_DB_BACKEND=sqlite
COMMISSIONS_SQLITE_PATH=app_data/commissions.db
```

SQLite se conserva para desarrollo y pruebas. Todos los repositorios formales
comparten el archivo configurado; `input_store.py` no forma parte de esta
composicion porque pertenece al puente heredado.

## Produccion

```text
COMMISSIONS_ENV=production
COMMISSIONS_DB_BACKEND=postgresql
COMMISSIONS_POSTGRES_DSN=<configuracion externa>
LOCAL_TEST_AUTH_ENABLED=false
```

El runtime rechaza iniciar produccion con SQLite o con autenticacion local de
pruebas habilitada. Los secretos no se incluyen en archivos del repositorio.

El proveedor de NetSuite se inyecta mediante `NetSuiteDataProvider`.
`NETSUITE_PROVIDER=api` selecciona REST SuiteQL y `NETSUITE_PROVIDER=odbc`
mantiene SuiteAnalytics Connect como alternativa controlada. El cambio de
transporte no modifica cálculo, repositorios ni servicios de aplicación.

La construccion no consulta NetSuite. La extraccion ocurre solo al ejecutar la
accion autorizada de sincronizacion. El calculo productivo consume el snapshot
persistido y las versiones aprobadas de pisos, metas y fechas de corte.

## Ejecucion sin notebook

```powershell
python -m src.commands production-check
python -m src.commands sync --actor-id 7 --type FULL
python -m src.commands calculate --actor-id 7 --period 2026-09
```

`production-check` valida PostgreSQL, LDAP, ODBC, SMTP opcional y los límites
de seguridad productivos sin abrir conexiones ni imprimir secretos. Debe
ejecutarse en el servidor después de configurar el archivo de entorno y antes
de habilitar la unidad `systemd`.

La sincronizacion valida el permiso independiente `NETSUITE_SYNC_EXECUTE`. El
calculo usa exclusivamente el snapshot persistido y los insumos aprobados; no
acepta rutas de Excel ni DataFrames externos desde la linea de comandos. Ambos
comandos aceptan `--run-code` para trazabilidad. El resultado operativo se
entrega como JSON en `stdout` y los logs se emiten por `stderr`.

Una ejecucion de calculo termina en `COMPLETADO_CON_ADVERTENCIAS` cuando el
resultado contiene documentos o cumplimientos para revisar, o movimientos de
pago sin documento base. La advertencia no modifica decisiones ni valores:
conserva conteos y concepto para que el revisor atienda los casos antes del
cierre. Una repeticion idempotente reutiliza ese mismo estado y mensaje.

## Limite actual

`commission_app.py` ya construye este runtime y usa su autenticacion configurada.
Falta validar el flujo completo contra PostgreSQL y LDAP reales en Linux.
`local_review_app.py`, `input_store.py`, `notebook_bridge.py` y
`review_bundle.py` siguen siendo componentes transitorios, no dependencias del
runtime productivo.
