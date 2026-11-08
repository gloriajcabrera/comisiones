"""Consultas NetSuite usadas por el calculo actual.

Estas consultas conservan el universo funcional del notebook. La capa de
integracion puede cambiar de ODBC a API sin trasladar SQL al motor financiero.
"""

INVOICES_SQL = """
SELECT t.tranDate, t.dueDate, t.tranId, t.entity, s.entityid,
       l.id AS line_id,
       i.fullName AS Servicio, i.itemType, i.custitem_sm_linea_serv,
       l.rate AS Precio,
       t.foreigntotal AS valor_total_documento, l.quantity,
       g.amount * (-1) AS Valor, l.createdfrom AS createdfrom, t.id, t.status,
       t.custbody_sm_factura_reemplazo, t.custbody_ks_sm_proforma, t.type,
       i.custitem_sm_lineacomision, c.name AS clase, k.name AS cc,
       t.foreignamountunpaid,
       t.foreigntotal * t.exchangerate AS valor_moneda_local,
       t.currency AS moneda_id,
       t.foreigntotal AS valor_moneda_transaccion, b.name AS lote,
       t.custbody_sm_reemplaza_a, t.memo
FROM transaction t
INNER JOIN transactionLine l ON t.id = l.transaction
LEFT JOIN item i ON l.item = i.id
LEFT JOIN classification c ON l.class = c.id
LEFT JOIN employee s ON t.employee = s.id
LEFT JOIN department d ON l.department = d.id
LEFT JOIN CUSTOMRECORD_CSEG1 k ON l.cseg1 = k.id
LEFT JOIN CUSTOMRECORD_CSEG2 b ON l.cseg2 = b.id
LEFT JOIN TransactionAccountingLine g
        ON l.id = g.transactionline AND l.transaction = g.transaction
WHERE t.type = 'CustInvc'
  AND i.custitem_sm_linea_serv NOT IN (18)
  AND t.tranDate >= TO_DATE('2023-01-01', 'YYYY-MM-DD')
"""

COSTS_SQL = INVOICES_SQL.replace(
    "t.type = 'CustInvc'",
    "t.type = 'PurchOrd'\n  AND t.status NOT IN ('H', 'C')",
).replace(
    "\n  AND i.custitem_sm_linea_serv NOT IN (18)",
    "",
)

PROFORMAS_SQL = """
SELECT t.tranDate, t.enddate AS dueDate, t.tranId, t.entity, s.entityid,
       l.id AS line_id,
       i.fullName AS Servicio, i.itemType, i.custitem_sm_linea_serv,
       l.rate AS Precio,
       t.foreigntotal AS valor_total_documento, l.quantity,
       g.amount * (-1) AS Valor, t.custbody_ks_sm_proforma,
       t.custbody_sm_factura_reemplazo,
       l.createdfrom AS createdfrom, t.id, t.status, t.type,
       i.custitem_sm_lineacomision, c.name AS clase, k.name AS cc,
       t.foreignamountunpaid,
       t.foreigntotal * t.exchangerate AS valor_moneda_local,
       t.foreigntotal AS valor_moneda_transaccion, t.currency AS moneda_id,
       b.name AS lote, t.custbody_sm_reemplaza_a, t.memo
FROM transaction t
INNER JOIN transactionLine l ON t.id = l.transaction
LEFT JOIN item i ON l.item = i.id
LEFT JOIN classification c ON l.class = c.id
LEFT JOIN employee s ON t.employee = s.id
LEFT JOIN department d ON l.department = d.id
LEFT JOIN CUSTOMRECORD_CSEG1 k ON l.cseg1 = k.id
LEFT JOIN CUSTOMRECORD_CSEG2 b ON l.cseg2 = b.id
LEFT JOIN TransactionAccountingLine g
        ON l.id = g.transactionline AND l.transaction = g.transaction
WHERE t.type = 'SalesOrd'
  AND i.custitem_sm_linea_serv NOT IN (18)
  AND t.custbody_ks_sm_proforma = 'T'
  AND t.tranDate >= TO_DATE('2023-01-01', 'YYYY-MM-DD')
"""

CREDIT_NOTES_SQL = PROFORMAS_SQL.replace(
    "t.enddate AS dueDate",
    "t.dueDate",
).replace(
    "t.custbody_ks_sm_proforma = 'T'",
    "1 = 1",
).replace(
    "t.type = 'SalesOrd'",
    "t.type = 'CustCred'",
)

PROFORMA_CANCELLATIONS_SQL = """
SELECT t.tranDate, t.tranId, t.id, l.id AS line_id,
       t.memo, g.amount AS Valor,
       t.currency AS moneda_id,
       t.foreigntotal AS valor_moneda_transaccion,
       t.foreigntotal * t.exchangerate AS valor_moneda_local
FROM transaction t
INNER JOIN transactionLine l ON t.id = l.transaction
INNER JOIN TransactionAccountingLine g
        ON l.id = g.transactionline AND l.transaction = g.transaction
WHERE t.type = 'Journal'
  AND t.tranDate >= TO_DATE('2023-01-01', 'YYYY-MM-DD')
  AND t.custbody_ks_sm_tipoje = 6
"""

PROFORMA_PAYMENTS_SQL = """
SELECT pf.id AS proforma_id, pf.tranid AS proforma,
       pf.trandate AS fecha_proforma, pf.entity AS cliente_id,
       BUILTIN.DF(pf.entity) AS cliente, dep.id AS deposito_id,
       dep.tranid AS deposito, dep.trandate AS fecha_deposito,
       pay.id AS pago_id, pay.tranid AS documento_pago,
       pay.type AS tipo_pago, pay.trandate AS fecha_transaccion,
       pay.custbody_ks_fecrealtrans AS fecha_real_transaccion,
       NVL(pay.custbody_ks_fecrealtrans, pay.trandate) AS fecha_pago,
       pay.memo AS memo_pago,
       l.foreignAmount AS valor_pagado_proforma, l.linkType AS tipo_vinculo,
       l.previousLine AS previous_line, l.nextLine AS next_line
FROM transaction pf
INNER JOIN transaction dep
        ON dep.id = pf.custbody_sm_docuemtnos_relacionados
INNER JOIN previoustransactionlinelink l
        ON l.previousDoc = dep.id AND l.linkType = 'Payment'
INNER JOIN transaction pay ON pay.id = l.nextDoc
WHERE pf.type = 'SalesOrd'
  AND pf.custbody_ks_sm_proforma = 'T'
  AND pf.trandate >= TO_DATE('2023-01-01', 'YYYY-MM-DD')
  AND pay.type IN ('CustPymt', 'Journal', 'DepAppl')
"""

# SuiteAnalytics REST times out when it plans the complete four-table join
# above. The API provider reads the same relation in two bounded stages and
# merges them by deposito_id; ODBC keeps using the validated original query.
PROFORMA_PAYMENT_DOCUMENTS_SQL = """
SELECT pf.id AS proforma_id, pf.tranid AS proforma,
       pf.trandate AS fecha_proforma, pf.entity AS cliente_id,
       BUILTIN.DF(pf.entity) AS cliente, dep.id AS deposito_id,
       dep.tranid AS deposito, dep.trandate AS fecha_deposito
FROM transaction pf
INNER JOIN transaction dep
        ON dep.id = pf.custbody_sm_docuemtnos_relacionados
WHERE pf.type = 'SalesOrd'
  AND pf.custbody_ks_sm_proforma = 'T'
  AND pf.trandate >= TO_DATE('2023-01-01', 'YYYY-MM-DD')
"""

PROFORMA_PAYMENT_LINKS_SQL = """
SELECT l.previousDoc AS deposito_id,
       pay.id AS pago_id, pay.tranid AS documento_pago,
       pay.type AS tipo_pago, pay.trandate AS fecha_transaccion,
       pay.custbody_ks_fecrealtrans AS fecha_real_transaccion,
       NVL(pay.custbody_ks_fecrealtrans, pay.trandate) AS fecha_pago,
       pay.memo AS memo_pago,
       l.foreignAmount AS valor_pagado_proforma, l.linkType AS tipo_vinculo,
       l.previousLine AS previous_line, l.nextLine AS next_line
FROM previoustransactionlinelink l
INNER JOIN transaction pay ON pay.id = l.nextDoc
WHERE l.linkType = 'Payment'
  AND pay.type IN ('CustPymt', 'Journal', 'DepAppl')
"""

INVOICE_PAYMENTS_SQL = """
SELECT fac.id AS factura_id, fac.tranid AS factura,
       fac.trandate AS fecha_factura, fac.entity AS cliente_id,
       BUILTIN.DF(fac.entity) AS cliente, pay.id AS pago_id,
       pay.tranid AS documento_pago, pay.type AS tipo_pago,
       pay.trandate AS fecha_contabilizacion,
       pay.custbody_ks_fecrealtrans AS fecha_real,
       NVL(pay.custbody_ks_fecrealtrans, pay.trandate) AS fecha_pago,
       l.foreignAmount AS valor_pago, pay.memo AS memo_pago,
       l.previousLine AS previous_line, l.nextLine AS next_line
FROM transaction fac
INNER JOIN previoustransactionlinelink l
        ON l.previousDoc = fac.id AND l.linkType = 'Payment'
INNER JOIN transaction pay ON pay.id = l.nextDoc
WHERE fac.type = 'CustInvc'
  AND fac.trandate >= TO_DATE('2023-01-01', 'YYYY-MM-DD')
  AND pay.type IN ('CustPymt', 'Journal', 'DepAppl', 'CustCred')
ORDER BY NVL(pay.custbody_ks_fecrealtrans, pay.trandate) DESC,
         pay.id DESC, fac.tranid
"""

QUERY_CATALOG = {
    "invoices": INVOICES_SQL,
    "costs": COSTS_SQL,
    "proformas": PROFORMAS_SQL,
    "credit_notes": CREDIT_NOTES_SQL,
    "proforma_cancellations": PROFORMA_CANCELLATIONS_SQL,
    "proforma_payments": PROFORMA_PAYMENTS_SQL,
    "invoice_payments": INVOICE_PAYMENTS_SQL,
}
