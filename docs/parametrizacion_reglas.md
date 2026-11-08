# Parametrizacion versionada de reglas

## Alcance implementado

La parametrizacion cubre constantes que ya existian en el calculo. No introduce
reglas financieras nuevas. Cada valor puede tener versiones con vigencia,
estado y trazabilidad de cambios.

| Codigo | Valor estandar | Unidad |
| --- | ---: | --- |
| `payment_full_days` | 30 | dias |
| `payment_half_days` | 60 | dias |
| `payment_factor_full` | 1.00 | factor |
| `payment_factor_half` | 0.50 | factor |
| `payment_factor_late` | 0.00 | factor |
| `product_first_payment_threshold` | 0.50 | proporcion |
| `product_first_payment_days` | 60 | dias |
| `default_commission_executive` | 0.04 | proporcion |
| `default_commission_manager` | 0.01 | proporcion |
| `default_commission_director` | 0.02 | proporcion |
| `subcontract_factor` | 0.50 | factor |
| `balance_tolerance_rate` | 0.000001 | proporcion |
| `balance_tolerance_minimum` | 1.00 | moneda |
| `include_proformas` | verdadero | bandera |

## Flujo y vigencia

Las versiones pasan por `BORRADOR`, `PENDIENTE_APROBACION` y `APROBADO` o
`RECHAZADO`. Solo las aprobadas y vigentes afectan el calculo. Dos versiones
aprobadas del mismo parametro no pueden tener vigencias superpuestas.

El permiso backend requerido es `PARAMETERS_CHANGE`. La implementacion inicial
permite que un parametrizador apruebe su propia propuesta, de acuerdo con la
especificacion funcional.

Deshabilitar un parametro desactiva su sobrescritura aprobada y restablece el
valor estandar validado. No elimina historia ni suprime la regla financiera.
La accion exige un motivo explicito y conserva un evento append-only con valor
anterior, valor nuevo, usuario y fecha, visible en la auditoria central.

## Compatibilidad

Se conservan nombres historicos como `primer_pago_50_o_mas` para evitar romper
consumidores actuales. Su valor se calcula con `product_first_payment_threshold`,
por lo que el nombre no debe interpretarse como un 50 por ciento fijo.

La huella idempotente de cada ejecucion incluye todos los valores efectivos de
parametrizacion. Un cambio de parametros genera una ejecucion nueva aunque los
datos de origen no hayan cambiado.

La posible regla de tomar la fecha del pago anterior cuando el ultimo pago sea
menor a un porcentaje configurable no forma parte de este catalogo. Su efecto
financiero sigue pendiente de aprobacion funcional y no debe mostrarse como una
regla vigente ni como un umbral del 95 por ciento.

## Persistencia

`PersistentRuleParameterRepository` funciona sobre la capa común de base de
datos. `SqliteRuleParameterRepository` se conserva para pruebas y
`PostgresRuleParameterRepository` para producción. La numeración y aprobación
se serializan para impedir versiones repetidas o vigencias superpuestas por
operaciones concurrentes. Esta migración no modifica valores estándar ni reglas.
