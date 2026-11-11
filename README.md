# FitLife Studio · prototipo de la Evaluación Sumativa 3

Aplicación web interna para demostrar la gestión de socios, vigencias, pagos, acceso y caja. Requiere Python 3.11 o superior con la biblioteca estándar y un navegador moderno. Se verificó con Python 3.14 y Microsoft Edge. Funciona en el equipo que lo ejecuta. Los importes iniciales son de demostración.

## Inicio en Windows

Abre PowerShell en esta carpeta y ejecuta:

    python -B app.py init-admin
    python -B app.py seed-demo
    python -B app.py serve

En el primer paso, elige un nombre de usuario y una contraseña de al menos 12 caracteres. El paso seed-demo es opcional y requiere una base sin socios. Las siguientes veces basta ejecutar serve. Abre http://127.0.0.1:8765/ e inicia sesión con la cuenta que creaste. Si el puerto está ocupado, usa serve --port 8770 y abre http://127.0.0.1:8770/. Usa la IP indicada para que el origen coincida con la validación del servidor. Detén el proceso con Ctrl+C. No se requiere pip ni una base instalada por separado.

La base y sus archivos temporales se crean en datos/. Para una demostración aislada puedes elegir otro archivo mediante $env:FITLIFE_DB = 'C:\ruta\demo.sqlite3' antes de iniciar. Configura el reloj y la zona horaria del equipo en Santiago de Chile. Se usan fechas calendario locales y montos enteros en CLP. El proceso de inicio inserta un catálogo inicial de cinco planes con tarifas referenciales. Administración puede editarlas.

## Recorrido de demostración

1. En **Control de acceso**, valida 10000001-6 (vigente) y 10000002-4 (vencido). La actividad reciente conserva ambas decisiones. Solo el permiso cuenta como asistencia.
2. En **Socios**, busca 10000003-2, abre su ficha y confirma su pago pendiente tras marcar la verificación externa. Abre el comprobante interno imprimible.
3. En la ficha de 10000004-0 solicita una pausa de 20 días con inicio de hoy y marca que verificaste que el viaje dura más de 15 días. En **Congelamientos**, apruébala desde Administración e inspecciona la fecha extendida.
4. En **Usuarios y auditoría**, crea una cuenta Recepción y úsala en otro perfil de navegador. Registra un cobro y luego una observación. Desde Administración registra una compensación parcial. Caja conserva el cobro y el ajuste como movimientos separados, en el turno de cada operador.
5. En **Caja**, consulta fecha, turno AM/PM y cajero. Cierra el turno cuando corresponda. Si hay diferencia de efectivo, se exige una nota. Para el cierre de día, Administración selecciona todos los cajeros y cierra primero cada turno con movimientos. Un cierre bloquea nuevas contabilizaciones en el período.
6. En **Contingencia**, comprueba que haya una copia vigente. Mantén la aplicación abierta y activa el modo *Offline* de DevTools. Valida un ingreso y registra un cobro a un socio existente. Comprueba la cola. Restablece la conexión para sincronizar o pulsa **Reconectar y sincronizar** si se cayó el proceso local. Al reconectar con la misma cuenta, revisa los resultados y la actividad de acceso.
7. En **Informes**, consulta vencimientos, ingresos por mes/medio/concepto y entradas por hora. El informe cuenta entradas y no mide ocupación instantánea.

Los cuatro RUT anteriores, los montos y los contactos de seed-demo son sintéticos. El POS y la cuenta bancaria siguen siendo externos. Las referencias de tarjeta son códigos del POS. El comprobante es interno y no tiene validez tributaria. Los ajustes registran la corrección, sin enviar dinero ni operar sobre el POS. Las ventas de pase diario se confirman al contado o por POS durante el día de uso.

## Contingencia y recuperación

La PWA cachea únicamente su interfaz. La copia de contingencia dura 30 minutos desde la última descarga. Incluye RUT, períodos, pausas y catálogo sin contactos. La cola, separada por cuenta, admite 300 comandos locales. Las decisiones se muestran como provisionales. Si no hay copia vigente, documenta la operación por el procedimiento manual del gimnasio. Trabaja en una sola pestaña y un perfil de navegador dedicado por puesto. Mantén los datos del navegador hasta reconciliar la cola. El servidor valida cada comando y un reenvío con el mismo identificador devuelve su resultado original. Una discrepancia de vigencia aparece en actividad como revisión. Un conflicto de precio o caja cerrada se conserva en cola hasta que Administración lo concilie. Retira un registro solo tras documentar esa conciliación.

Para copiar una base activa, usa python -B app.py backup --output "C:\ruta\fitlife-respaldo.sqlite3". La ruta debe ser nueva. Para comprobar o recuperar una copia, apaga el servidor, conserva intacto el archivo original y arranca con FITLIFE_DB apuntando a una copia independiente del respaldo. Conserva respaldos fuera del equipo y protegidos si se autoriza un piloto.

## Estructura y límites

- app.py expone las rutas HTTP/JSON y los comandos de administración local.
- security.py gestiona credenciales scrypt, bloqueo temporal, sesiones y roles.
- operations.py contiene socios, catálogo, congelamiento, validación de acceso y reconciliación.
- finance.py lleva vigencias, pagos, compensaciones, cierres e informes.
- storage.py y schema.sql definen persistencia, validadores, transacciones e inmutabilidad de registros históricos.
- web/ contiene la aplicación y el service worker, sin bibliotecas externas.

La configuración entregada sirve para un entorno académico local con datos sintéticos. El servidor escucha solo en 127.0.0.1 y usa HTTP. Un piloto con datos reales requiere reemplazar el servidor HTTP de demostración, configurar TLS, endurecer el acceso al equipo, cifrar almacenamiento y copias y formalizar la gestión de privacidad, retención y alertas.
