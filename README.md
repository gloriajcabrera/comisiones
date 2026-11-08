# Comisiones

Evolución del cálculo actual de comisiones hacia una aplicación modular,
auditable y compatible con Linux y PostgreSQL.

## Flujo objetivo

1. NetSuite se sincroniza mediante un proveedor desacoplado (ODBC actualmente;
   REST SuiteQL u ODBC según `NETSUITE_PROVIDER`).
2. Los datos canónicos sincronizados se normalizan y procesan directamente.
3. El motor calcula cumplimiento, pagos, comisiones y descuentos sin Excel
   intermedio.
4. La aplicación consulta resultados con seguridad de backend, jerarquía y
   vigencias.
5. El Excel final se genera en memoria solo cuando un usuario autorizado lo
   descarga o envía.

PostgreSQL será la fuente operativa de producción. SQLite se mantiene para
pruebas y desarrollo local. Los DataFrames son estructuras de procesamiento,
no fuentes permanentes de verdad.

La conexión se configura mediante `COMMISSIONS_DB_BACKEND`,
`COMMISSIONS_SQLITE_PATH` o `COMMISSIONS_POSTGRES_DSN`; los secretos no se
guardan en el repositorio.

Las dependencias del runtime estan en `requirements.txt`. Los ambientes que
sincronizan por ODBC usan `requirements-odbc.txt`; `pyarrow` queda limitado a
las herramientas heredadas descritas en `requirements-review-app.txt`. Consulte
`docs/dependencias_y_despliegue.md`.

`src.runtime.build_runtime()` compone los repositorios y servicios sin ejecutar
el notebook ni consultar NetSuite durante el arranque. En
`COMMISSIONS_ENV=production` exige PostgreSQL y deshabilita la autenticacion
local de pruebas. Consulte `docs/runtime_productivo.md`.

`runtime.calculation_views.refresh()` es la acción explícita que calcula o
reconstruye las vistas consultables en memoria. Una ejecución idempotente se
reutiliza sin insertar otra corrida ni persistir copias del detalle financiero.

La interfaz productiva autentica mediante el proveedor configurado, carga el
perfil interno y aplica roles, permisos y alcance jerarquico en backend. En
produccion `COMMISSIONS_AUTH_MODE=ldap` es obligatorio; el modo local solo se
habilita fuera de produccion mediante variables de entorno.

La operación semiautomática puede iniciarse sin Jupyter mediante
`python -m src.commands sync ...` y `python -m src.commands calculate ...`.

La referencia financiera puede verificarse en modo solo lectura. El comando
rechaza primero los bundles de un corte diferente y nunca actualiza el baseline:

```powershell
python -m src.commands regression-check --bundle app_data --baseline tests/fixtures/regression_baseline_2026-10-08.json
```

## Componentes

- `src/`: integración, normalización, reglas, cálculo, persistencia y servicios.
- `tests/`: pruebas unitarias y de regresión del comportamiento financiero.
- `docs/`: análisis previo, contratos, reglas, seguridad y estrategia de migración.
  El entregable consolidado vigente es `docs/entregable_previo_refactor.md`.
- `local_review_app.py`: interfaz local transitoria durante la migración.
- `commission_app.py`: interfaz nueva basada en funciones de reporte, sin archivos puente.
- `comisionesia.ipynb`: referencia funcional y herramienta de contraste; no forma
  parte del runtime productivo objetivo.

## Excel

Excel se admite únicamente como entrada funcional solicitada por el negocio
(por ejemplo, una carga inicial de pisos o cortes) y como descarga final bajo
demanda. No se usa para comunicar etapas internas del cálculo. Las cargas se
versionan sin eliminación física automática; solo una versión queda activa.

## Persistencia mínima

La aplicación evita guardar copias de resultados financieros que pueden
reconstruirse. Persiste datos canónicos sincronizados y el estado necesario:
usuarios, permisos, jerarquías, vigencias, aprobaciones, parámetros, periodos,
auditoría, ejecuciones y deudas/descuentos. Los pagos pueden conservarse como
evidencia para recalcular descuentos.

## Verificación

```powershell
python -W error::FutureWarning -W error::UserWarning -m unittest discover -s tests -v
```
