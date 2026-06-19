# Comisiones

Proyecto para cálculo, validación y revisión de comisiones.

## Contenido principal

- `comisionesia.ipynb`: notebook principal con la lógica actual.
- `local_review_app.py`: app local en Streamlit para revisión de resultados.
- `review_bundle.py`: helper para exportar dataframes hacia la app local.
- `requirements-review-app.txt`: dependencias de la app local.

## Flujo general

1. Ejecutar el notebook principal para construir bases y resultados.
2. Generar reportes y exportes desde las celdas finales.
3. Si se requiere revisión local, exportar los dataframes con `review_bundle.py`.
4. Abrir la app Streamlit para revisar aprobaciones y observaciones.

## Qué no se versiona

Este repositorio está pensado para versionar código y lógica, no datos ni exportes.

Se excluyen:

- archivos Excel y CSV de entrada/salida
- carpetas de exportes y resultados
- estado local de la app
- logs y archivos temporales

## Publicación

Antes de publicar en GitHub, revisar en GitHub Desktop que el commit incluya solo:

- notebooks que sí quieras conservar
- scripts `.py`
- `.gitignore`
- `README.md`

Si más adelante quieres versionar una plantilla o archivo puntual de datos, se puede agregar manualmente como excepción.
