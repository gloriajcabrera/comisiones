# Calidad e integridad de datos

`src.data_quality` centraliza la validacion previa a la persistencia de una
sincronizacion de NetSuite. La validacion no corrige ni transforma valores de
negocio silenciosamente.

## Severidades

- `ERROR`: impide guardar el snapshot y la ejecucion termina en `ERROR`.
- `ADVERTENCIA`: permite guardar los datos y la ejecucion termina en
  `COMPLETADO_CON_ADVERTENCIAS`.

## Controles iniciales

- Columnas requeridas para la identidad estable de cada conjunto.
- Identificadores nulos o vacios, tratados como error bloqueante.
- Identidades repetidas, conservadas pero reportadas como advertencia.
- Fechas no interpretables en columnas de fecha, reportadas como advertencia.

Cada incidencia identifica conjunto, codigo, severidad, explicacion y cantidad
de filas afectadas. El resumen se conserva en la ejecucion de sincronizacion;
no contiene filas completas, valores financieros ni credenciales.

Los controles se ampliaran solo cuando exista un contrato de datos confirmado.
Una advertencia no debe convertirse en error si pudiera excluir operaciones
financieras validas sin una definicion funcional aprobada.
