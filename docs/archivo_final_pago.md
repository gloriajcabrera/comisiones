# Archivo final de pago

El proceso finaliza una versión de pago mediante una acción explícita de un
usuario con `PAYMENT_FILE_GENERATE`. Se persisten la referencia al cálculo,
el periodo, la versión, el estado, el usuario, las fechas y el total final por
beneficiario (identificación, nombre y valor). Este corte mínimo garantiza que
una versión histórica no cambie por modificaciones posteriores. No se copian
facturas ni resultados detallados y no se crea ni almacena un Excel durante
esta operación.

Cada finalización crea una versión nueva con periodo, cálculo asociado, usuario,
fecha y estado. Las versiones anteriores no se sobrescriben.

La descarga requiere `PAYMENT_FILE_DOWNLOAD`. En ese momento se consultan los
totales inmutables de la versión y se construye el libro completamente en memoria. El
software devuelve nombre y bytes al navegador; no escribe el archivo en el
servidor. El libro descargable contiene `Pago` y `Trazabilidad`, y cada descarga
queda auditada.

El envío requiere por separado `PAYMENT_FILE_SEND`. El proveedor de correo se
inyecta mediante la interfaz `MailProvider`, por lo que el dominio no depende de
SMTP, Microsoft 365 ni otro proveedor concreto. Cada intento registra usuario,
destinatarios, fecha, estado, referencia del proveedor o error.

El Excel no es fuente de datos, almacenamiento ni mecanismo de intercambio
interno. Es únicamente una representación descargable o adjunta generada a
voluntad desde la versión persistida.

## Configuración opcional de correo

El adaptador SMTP usa únicamente configuración externa. Si
`COMMISSIONS_SMTP_HOST` no está definido, el envío permanece deshabilitado y las
demás acciones del archivo continúan disponibles.

- `COMMISSIONS_SMTP_HOST`: servidor SMTP.
- `COMMISSIONS_SMTP_PORT`: puerto; usa `587` por defecto.
- `COMMISSIONS_SMTP_FROM`: remitente obligatorio cuando se configura servidor.
- `COMMISSIONS_SMTP_USERNAME` y `COMMISSIONS_SMTP_PASSWORD`: credenciales
  opcionales; deben configurarse juntas.
- `COMMISSIONS_SMTP_STARTTLS`: `true` por defecto.
- `COMMISSIONS_SMTP_SSL`: `false` por defecto; no puede combinarse con STARTTLS.
- `COMMISSIONS_SMTP_TIMEOUT`: segundos de espera; usa `30` por defecto.

Las credenciales no se persisten ni se muestran en auditoría. El adaptador es
reemplazable por el proveedor corporativo definitivo sin modificar el servicio
de archivos de pago.
