# Pisos y metas

Los pisos y metas se administran como lotes versionados. Una nueva carga nunca
sobrescribe ni elimina las versiones anteriores.

## Flujo

`BORRADOR -> PENDIENTE_VERIFICACION -> VERIFICADO -> PENDIENTE_APROBACION -> APROBADO`

- Jefe o usuario con `TARGET_REGISTER` registra y envia.
- Revisor o usuario con `TARGET_VERIFY` verifica y solicita aprobacion.
- Parametrizador o usuario con `TARGET_APPROVE` aprueba.
- La autoaprobacion del Parametrizador solo ocurre cuando el servicio la recibe
  expresamente como configuracion.

Cada transición conserva usuario, fecha, estado anterior, estado nuevo y
observacion. Una carga con errores permanece en borrador y no puede avanzar.

## Versionamiento

Cada lote guarda version, origen, huella, cantidades totales/validas/con error,
creador, aprobador y fechas. Las filas se identifican por
`periodo + ejecutivo + linea` dentro de cada lote.

Cada fila válida persiste únicamente periodo, ejecutivo, línea, meta, piso y las
columnas explícitas de comisión que consume el motor: jefe, gerente y tasas por
rol. No se conserva una segunda copia de la fila en JSON. Los errores guardan
número de fila y mensajes de validación, pero no duplican el registro original.

Las cargas parciales son validas: al consultar la configuracion aprobada se toma
la version aprobada mas reciente de cada clave y se conservan las claves no
incluidas en la carga nueva. Esto permite agregar ejecutivos o corregir una sola
linea sin reemplazar toda la historia.

La validacion de periodo, ejecutivo, linea, meta, piso y claves duplicadas usa
mascaras vectorizadas de pandas. Los mensajes se agrupan por fila invalida sin
`iterrows()`, y las filas validas y los errores se escriben mediante operaciones
batch. Esto conserva todos los motivos de rechazo sin commits fila a fila.
