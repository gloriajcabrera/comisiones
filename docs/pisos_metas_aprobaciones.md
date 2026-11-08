# Pisos, metas y aprobaciones

El flujo conserva versiones completas y parciales sin sobrescribir historia:

`BORRADOR -> PENDIENTE_VERIFICACION -> VERIFICADO -> PENDIENTE_APROBACION -> APROBADO`

La clave funcional es periodo, ejecutivo y línea de comisión. Una carga parcial
aprobada reemplaza únicamente las claves incluidas; las demás conservan su última
versión aprobada.

`SqliteTargetsRepository` se usa en pruebas y `PostgresTargetsRepository` en
producción. Ambos reutilizan `PersistentTargetsRepository`, por lo que validación,
versiones, errores, estados y selección de la última fila aprobada son idénticos.

Las filas y los errores se insertan en lote dentro de la misma transacción que la
cabecera y el primer estado. No se almacena una copia JSON de las filas válidas.
El archivo XLSX o CSV es una entrada funcional opcional; después de la carga, el
software consume las filas aprobadas desde la base de datos.
