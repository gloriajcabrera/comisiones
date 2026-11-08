# Dependencias y despliegue

## Perfiles de instalacion

- `requirements.txt`: runtime productivo de la aplicacion, PostgreSQL y la
  unica libreria de Excel permitida para cargas y descargas funcionales.
- `requirements-odbc.txt`: agrega `pyodbc` solamente en ambientes que extraen
  datos de NetSuite por ODBC. En Linux requiere ademas el driver ODBC de
  NetSuite configurado en el sistema operativo.
- `requirements-dev.txt`: herramientas necesarias para ejecutar el codigo y
  las pruebas actuales. Las pruebas usan `unittest` de la biblioteca estandar.
- `requirements-review-app.txt`: compatibilidad temporal con el visor y los
  bundles Parquet heredados; no corresponde al runtime productivo.

`pyarrow` no se instala en produccion. Jupyter tampoco es una dependencia del
sistema: los notebooks se conservan unicamente como referencia funcional y
fuente de casos de regresion durante la migracion.

## Instalacion

Runtime con PostgreSQL:

```bash
python -m pip install -r requirements.txt
```

Runtime que tambien sincroniza por ODBC:

```bash
python -m pip install -r requirements-odbc.txt
```

La configuracion se suministra mediante variables de entorno. No se deben
guardar DSN, usuarios, contrasenas ni cadenas de conexion en estos archivos.

Las pruebas arquitectonicas revisan el codigo productivo para impedir rutas
absolutas de Windows, credenciales literales y dependencias exclusivas de
Windows. Estas pruebas complementan, pero no reemplazan, la validacion del
servicio, PostgreSQL y el driver ODBC en un servidor Linux real.

Los objetos de configuracion ODBC, LDAP y SMTP excluyen las contrasenas de su
representacion para reducir el riesgo de exposicion accidental en logs o
diagnosticos.

## Servicio Linux

`deploy/systemd/comisiones.service.example` contiene una unidad `systemd` de
referencia con ejecución headless, reinicio ante fallos y aislamiento básico.
`deploy/comisiones.env.example` enumera las variables consumidas por el runtime
sin incluir secretos. En el servidor se debe copiar y ajustar fuera del
repositorio, por ejemplo en `/etc/servimeters/comisiones.env`, con permisos de
lectura limitados al usuario del servicio.

Antes de cada inicio, `ExecStartPre` ejecuta `production-check`; si PostgreSQL,
LDAP, NetSuite o los límites de seguridad no están correctamente configurados, la
aplicación no inicia. `StateDirectory` crea `/var/lib/servimeters-comisiones`
con el propietario del servicio y `UMask=0027` restringe los archivos nuevos.

La direccion y el puerto se controlan con `STREAMLIT_SERVER_ADDRESS` y
`STREAMLIT_SERVER_PORT`. La plantilla escucha en `0.0.0.0:8501` para permitir
acceso desde la red interna; el firewall del servidor debe aceptar ese puerto
solo desde las redes autorizadas. Si se usa un proxy inverso corporativo, debe
cambiarse la direccion a `127.0.0.1` y exponer unicamente el proxy con TLS.

Las rutas `/opt/servimeters/comisiones`, `/etc/servimeters/comisiones.env` y
`/var/lib/servimeters-comisiones` son convenciones de despliegue y pueden
adaptarse antes de instalar la unidad. La plantilla no sustituye las pruebas
reales de PostgreSQL, LDAP/LDAPS y del driver ODBC en el servidor objetivo.
