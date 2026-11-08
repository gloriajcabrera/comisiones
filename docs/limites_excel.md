# Límites de Excel

El runtime productivo no usa Excel para comunicar sincronización,
normalización, cálculo, persistencia e interfaz.

Las únicas fronteras permitidas dentro de `src/` son:

- `input_store.py`: carga funcional y versionada de pisos, metas y cortes en
  Excel o CSV.
- `payment_files.py`: generación en memoria del archivo final de pago cuando
  un usuario autorizado decide descargarlo o enviarlo.

`local_review_app.py` y `celda_exportacion_producto.py` son herramientas
heredadas para contraste durante la migración. Ambas rechazan su ejecución con
`COMMISSIONS_ENV=production` y no son dependencias del runtime nuevo.

`tests/test_architecture_boundaries.py` impide incorporar nuevas lecturas,
escrituras o librerías Excel fuera de las fronteras aprobadas.
