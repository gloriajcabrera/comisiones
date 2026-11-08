# Autenticacion

La autenticacion externa y la autorizacion interna son capas distintas.
`AuthenticationProvider` valida identidad; `AuthenticationService` exige luego
que exista un perfil interno activo, con permisos efectivos, y registra el
ultimo acceso.

## Desarrollo y pruebas

`LocalTestAuthenticationProvider` permite probar distintos usuarios sin Active
Directory. Debe habilitarse expresamente con `LOCAL_TEST_AUTH_ENABLED=true` y
rechaza su inicializacion cuando `COMMISSIONS_ENV=production`, incluso si otro
componente intenta habilitarlo directamente. Las claves se mantienen solo
en memoria usando PBKDF2 con sal aleatoria; no se guardan en SQLite.

## Produccion

`LdapAuthenticationProvider` recibe un adaptador LDAP/LDAPS. Esta separacion
permite elegir la libreria compatible con el Linux productivo cuando se conozca
la infraestructura corporativa, sin modificar sesiones, usuarios ni permisos.

Configuracion externa prevista:

```text
LDAP_SERVER=
LDAP_PORT=636
LDAP_DOMAIN=
LDAP_BASE_DN=
LDAP_USE_TLS=true
LDAP_TIMEOUT_SECONDS=10
LDAP_SEARCH_USER=
LDAP_SEARCH_PASSWORD=
```

No se almacenan ni replican contrasenas del dominio. Tampoco se han inventado
servidores, DN, cuentas tecnicas o certificados: deben suministrarse para el
despliegue real.

La cuenta de búsqueda es opcional y separada del inicio de sesión. Cuando ambas
variables `LDAP_SEARCH_USER` y `LDAP_SEARCH_PASSWORD` están configuradas, el
administrador puede buscar por usuario, nombre o correo y precargar los atributos
disponibles en el perfil interno. La búsqueda exige `USERS_MANAGE`, escapa el
filtro LDAP y nunca crea el usuario sin la confirmación, roles y observación del
formulario interno.
