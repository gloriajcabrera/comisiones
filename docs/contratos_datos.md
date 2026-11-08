# Contratos iniciales de datos

Estos contratos describen las columnas minimas que hoy consume el calculo. No cambian la semantica de las consultas. Antes de extraer cada consulta del notebook se debe confirmar el contrato contra una ejecucion real y agregar una prueba de esquema.

## Documentos: `f` y `p`

Clave de cabecera esperada: `id`. Clave visible: `tranId`. Una transaccion puede tener varias lineas.

Columnas minimas:

- `tranDate`, `dueDate`, `tranId`, `id`, `type`, `status`.
- `entity`, `entityid`.
- `Servicio`, `Precio`, `quantity`, `Valor`.
- `valor_total_documento`, `valor_moneda_local`, `valor_moneda_transaccion`, `moneda_id`.
- `foreignamountunpaid` cuando NetSuite lo suministra.
- `createdfrom`, `custbody_sm_reemplaza_a`, `custbody_sm_factura_reemplazo`, `custbody_ks_sm_proforma`.
- `custitem_sm_lineacomision`, `clase`, `cc`, `lote`, `memo`.

Invariantes iniciales:

- `id` y `tranId` no deben ser nulos.
- `valor_total_documento` debe ser constante dentro del documento.
- `dueDate` debe ser constante dentro del documento.
- Las distintas clases dentro de un documento son validas y no deben colapsarse antes de aplicar reglas.
- `f` y `p` comparten estructura logica, pero conservan el tipo de documento.
- Todos los valores funcionales expuestos por consultas, reportes, interfaz y
  archivo final se expresan en moneda local. Los importes de transaccion y la
  tasa se conservan solo para conciliacion tecnica interna.
- La sincronizacion advierte numeros visibles vacios, valores monetarios no
  numericos y cambios de total o vencimiento entre lineas del mismo documento.
- Si las columnas vienen en la extraccion, ejecutivo o linea de comision vacios
  se registran como advertencias de revision sin eliminar el documento.
- Un mismo ID de documento con numeros visibles distintos entre sus lineas se
  registra como inconsistencia para conciliacion.

## Notas credito: `nc` y `ncp`

Columnas minimas:

- Identificador y numero de la NC: `id`, `tranId`, `tranDate`, `type`.
- Documento anulado: `createdfrom` o su clave equivalente.
- Documento de reemplazo: `custbody_sm_factura_reemplazo` y numero resuelto.
- Servicio, lote, clase, linea y valores aplicados.
- Memo y campos utilizados para diagnosticar la relacion.

Invariantes iniciales:

- Los importes deben conservar su signo de NetSuite y normalizarse una sola vez.
- La NC debe permanecer visible en toda cadena de reemplazo.
- Un ciclo o autorrelacion no se resuelve silenciosamente: se marca para revision.
- La cadena puede cortarse por la regla validada de documentos con la misma fecha.

## Pagos: `pff` y `ppf`

Columnas confirmadas:

- Facturas: `factura_id`, `factura`, `fecha_factura`, `cliente_id`, `cliente`, `pago_id`, `documento_pago`, `tipo_pago`, `fecha_contabilizacion`, `fecha_real`, `fecha_pago`, `valor_pago`, `memo_pago`.
- Proformas: `proforma_id`, `proforma`, `fecha_proforma`, `cliente_id`, `cliente`, `deposito_id`, `deposito`, `fecha_deposito`, `pago_id`, `documento_pago`, `tipo_pago`, `fecha_transaccion`, `fecha_real_transaccion`, `fecha_pago`, `memo_pago`, `valor_pagado_proforma`, `tipo_vinculo`.

Invariantes iniciales:

- `fecha_pago` se construye con la fecha real cuando existe y, en caso contrario, con la fecha contable/transaccion definida en la consulta.
- Se admiten varias aplicaciones del mismo pago al mismo documento; no son duplicados solo por compartir `pago_id`.
- Los tipos observados incluyen `CustPymt`, `Journal`, `CustCred` y `DepAppl`.
- Los valores de NC llegan positivos en la base de pagos y su efecto se clasifica por `tipo_pago`.
- Los movimientos posteriores al corte se excluyen de comparaciones historicas, no de la fuente sincronizada.
- Una referencia de pago cuyo documento padre no aparece en el conjunto
  sincronizado se registra como advertencia para conciliacion; no se elimina ni
  bloquea automaticamente porque el origen puede haberse consultado por rangos.

## Resultados congelados

La referencia inicial esta en `tests/fixtures/regression_baseline_2026-10-08.json` y corresponde al bundle generado el `2026-10-08T09:11:37`.

La referencia contiene:

- Conteos de filas y columnas por conjunto publicado.
- Sumas de control de piso, comisiones, descuentos y pago.
- Agregados de documentos conocidos para reemplazos, NC, fechas de pago y habilitacion por linea.

Esta referencia prueba equivalencia con el estado actual. No declara que todos los casos sean funcionalmente correctos. Los casos que siguen en `PARA REVISAR` requieren una expectativa de negocio aprobada antes de convertirla en una asercion funcional.

La suite valida siempre la referencia financiera autocontenida y la estructura
del corte historico versionado. La comparacion contra un bundle local mutable es
un diagnostico voluntario y no una dependencia del sistema ni de la regresion
obligatoria.

## Ejecucion

```powershell
python -m unittest discover -s tests -v
```

Si cambia intencionalmente una regla financiera, no se sobrescribe la referencia de inmediato. Primero se documenta el caso, se aprueba el nuevo resultado y se crea una nueva version fechada del fixture.
