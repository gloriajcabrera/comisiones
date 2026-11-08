# Logging operativo en Linux

La aplicacion usa `logging` de la biblioteca estandar y escribe a `stdout`.
Esto permite que systemd, Docker u otro supervisor del servidor capture los
eventos sin agregar servicios ni dependencias.

## Configuracion

- `COMMISSIONS_LOG_LEVEL`: nivel minimo. Valor predeterminado: `INFO`.
- `COMMISSIONS_LOG_FORMAT`: `json` o `text`. Valor predeterminado: `json`.

El punto de entrada productivo llama una sola vez a
`src.observability.configure_logging()` al construir el runtime almacenado en
la caché de recursos de Streamlit.
El ejecutor por comandos dirige los logs a `stderr` y reserva `stdout` para su
resultado JSON, de modo que pueda integrarse con scripts sin mezclar canales.

## Eventos incluidos

- Inicio, terminacion y error del calculo.
- Inicio, terminacion y error de la sincronizacion con NetSuite.
- Error del envio del archivo final de pago.

Los eventos incluyen identificadores operativos como ejecucion, periodo,
estado y usuario cuando aplican. No incluyen filas financieras, contenido de
archivos ni credenciales. Antes de persistir o registrar una excepcion se
eliminan saltos de linea, se ocultan secretos conocidos y se limita el mensaje
a 2.000 caracteres.

## Continuidad operativa

El procedimiento de respaldo y ensayo seguro de recuperación de PostgreSQL se
documenta en `docs/persistencia.md`. La validación de un archivo no modifica la
base productiva y una restauración de prueba siempre debe usar una base vacía y
desechable.
