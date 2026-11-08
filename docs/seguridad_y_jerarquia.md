# Seguridad y jerarquia comercial

## Principio de autorizacion

`AuthorizationService` valida identidad activa, permisos efectivos y alcance
jerarquico en backend. Los identificadores enviados por una pantalla se tratan
solo como filtros solicitados: nunca demuestran que el usuario tenga acceso.

Al crear una solicitud o proponer una excepcion documental, el backend valida
primero el alcance jerarquico y luego reconstruye la vista del calculo aprobado
del periodo. La combinacion ejecutivo + periodo + documento debe existir en esa
vista. Por tanto, cambiar manualmente el ejecutivo, la factura o los parametros
de la llamada no permite asociar documentos ajenos. La denegacion no revela a
que otro ejecutivo pertenece el documento.

Las excepciones separan autoría y decisión. Solo el usuario que creó una
excepción en estado `PROPUESTA` puede enviarla a aprobación; alterar el
`exception_id` no permite enviar la propuesta de otra persona. La aprobación,
el rechazo y la desactivación continúan reservados al permiso independiente
`EXCEPTION_APPROVE`.

Un usuario puede tener varios roles. Sus permisos son la union de roles activos
y permisos directos; una denegacion directa prevalece sobre lo heredado.

## Roles iniciales

- `EJECUTIVO`: consulta propia y solicitudes de revision.
- `JEFE`: consulta propia/equipo y registro de pisos o metas.
- `GERENTE`: consulta propia y jerarquia gerente-jefe-ejecutivo.
- `REVISOR`: consulta global, verificacion, excepciones, cierre y archivo.
- `PARAMETRIZADOR`: consulta global, reglas y aprobaciones.
- `ADMINISTRADOR`: administracion y capacidades funcionales auditadas.

`NETSUITE_SYNC_EXECUTE`, `CALCULATION_EXECUTE` y `PAYMENT_FILE_SEND` son
permisos independientes. No se conceden automaticamente por tener un rol,
incluido Administrador. Consultar una ejecución financiera ya completada no
requiere `CALCULATION_EXECUTE`; crear un recálculo sí lo exige en backend.

Una prueba contractual compara el conjunto completo de permisos de cada rol,
no solo permisos aislados. Esto evita ampliar capacidades silenciosamente al
modificar la matriz o agregar acciones nuevas.

## Jerarquia con vigencias

Las relaciones `EJECUTIVO_JEFE` y `JEFE_GERENTE` tienen fecha inicial y final.
No se permite que una entidad tenga relaciones del mismo tipo con vigencias
superpuestas. Las consultas historicas calculan el alcance usando la fecha del
periodo consultado, no la estructura comercial actual.

La reasignacion se realiza mediante `HierarchyService.reassign`. Requiere el
permiso `USERS_MANAGE`, fecha efectiva y motivo. Cierra la relacion anterior el
dia previo, crea la nueva vigencia en la misma transaccion y registra
`HIERARCHY_REASSIGNED`, sin sobrescribir ni eliminar la relacion historica.
Las asignaciones futuras y los tipos de entidad incompatibles se rechazan para
evitar periodos ambiguos.

Un ejecutivo solo obtiene sus codigos vinculados. Un jefe obtiene sus
ejecutivos directos vigentes. Un gerente obtiene los ejecutivos de sus jefes
vigentes. Revisor, Parametrizador y Administrador tienen alcance global por
permiso; un Gerente solo lo obtiene si combina uno de esos roles.

Las pruebas cubren expresamente Gerente sin alcance global, Gerente+Revisor y
Gerente+Parametrizador para impedir que la combinacion de roles se resuelva solo
en la interfaz.

## Auditoria

Creacion e inactivacion de usuarios, asignacion de roles, permisos directos y
cambios jerarquicos generan eventos append-only en `audit_events`. La capa no
expone operaciones para editar o eliminar estos eventos.

`AuditQueryService` reúne este registro y los historiales de los demás módulos
sin duplicarlos. La consulta requiere `AUDIT_VIEW`, permiso asignado inicialmente
solo a Administrador, y aplica filtros y paginacion en backend.

## Persistencia

El repositorio está disponible como `SqliteAccessRepository` para pruebas y
`PostgresAccessRepository` para producción. Usuarios, roles muchos-a-muchos,
permisos directos, jerarquías con vigencia, estados laborales y auditoría
conservan el mismo comportamiento. `AuthorizationService` no depende del motor.

## Pendiente

La autenticacion y busqueda LDAP/LDAPS ya tienen proveedores desacoplados. Falta
validar servidor, certificados y atributos contra el Active Directory
corporativo real. La aplicacion nunca almacena la contrasena del dominio.

## Aislamiento de sesion

El cierre de sesion elimina identidad, resultados materializados, filtros,
navegacion, busquedas de directorio y borradores administrativos. No se mantiene
una lista parcial de claves, para evitar que un modulo futuro deje datos del
usuario anterior disponibles al siguiente inicio de sesion en el mismo navegador.
