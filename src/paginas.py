"""Páginas públicas: política de privacidad y eliminación de datos.

Meta pide estas dos URLs para publicar una app de WhatsApp.
Los plazos se leen de config, así la política siempre dice lo mismo
que hace el código (si cambias DIAS_CONSERVAR_ARCHIVO, la página cambia).
"""

from src import config

ACTUALIZADO = "24 de noviembre de 2026"
PLAZO_BORRADO_DIAS = 30  # compromiso para atender solicitudes de borrado

_ESTILO = """
:root { --fondo:#ffffff; --texto:#1f2328; --suave:#59636e; --borde:#d1d9e0; --acento:#1a7f37; }
@media (prefers-color-scheme: dark) {
  :root { --fondo:#0d1117; --texto:#e6edf3; --suave:#9198a1; --borde:#3d444d; --acento:#3fb950; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--fondo); color:var(--texto);
       font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 680px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.6rem; line-height:1.3; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 32px 0 8px; }
p, li { margin: 0 0 10px; }
ul, ol { padding-left: 22px; }
.fecha { color: var(--suave); font-size: .9rem; margin-bottom: 24px; }
.caja { border:1px solid var(--borde); border-left:4px solid var(--acento);
        border-radius:6px; padding:12px 16px; margin:16px 0; }
a { color: var(--acento); }
footer { margin-top:40px; padding-top:16px; border-top:1px solid var(--borde);
         color:var(--suave); font-size:.9rem; }
"""


def _pagina(titulo: str, cuerpo: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} · {config.NOMBRE_NEGOCIO}</title>
<style>{_ESTILO}</style>
</head>
<body>
<main>
{cuerpo}
<footer>
  {config.NOMBRE_NEGOCIO} · Lima, Perú ·
  <a href="/privacidad">Política de privacidad</a> ·
  <a href="/eliminacion-datos">Eliminación de datos</a>
</footer>
</main>
</body>
</html>"""


def privacidad() -> str:
    dias_activa = config.DIAS_REINICIO_HISTORIAL
    dias_archivo = config.DIAS_CONSERVAR_ARCHIVO
    maximo = dias_activa + dias_archivo
    n = config.NOMBRE_NEGOCIO
    return _pagina("Política de privacidad", f"""
<h1>Política de privacidad</h1>
<p class="fecha">Última actualización: {ACTUALIZADO}</p>

<p>Esta política explica qué datos guarda el asistente automático de WhatsApp de
<strong>{n}</strong> (Coro {n}, Casa Musical {n} y Agrupación Musical {n}),
para qué los usamos y cómo puedes pedir que los borremos.</p>

<h2>Qué datos guardamos</h2>
<ul>
  <li><strong>Tu número de WhatsApp.</strong></li>
  <li><strong>Los mensajes de la conversación</strong>: lo que escribes y lo que te respondemos
      (el asistente o una persona del equipo).</li>
  <li>Si envías una imagen, audio, video o documento, <strong>no guardamos el archivo</strong>:
      solo anotamos que lo enviaste y el texto que lo acompañe.</li>
</ul>
<p>WhatsApp también nos muestra tu nombre de perfil. No lo guardamos en nuestra base de datos;
solo aparece en los avisos internos que recibe nuestro equipo cuando una persona debe atenderte.</p>

<h2>Para qué los usamos</h2>
<ul>
  <li>Responder tus consultas sobre nuestros servicios.</li>
  <li>Preparar cotizaciones y gestionar tus pedidos o reservas.</li>
  <li>Pasar tu conversación a una persona del equipo cuando hace falta
      (por ejemplo, para confirmar una fecha o verificar un pago).</li>
</ul>
<p>No vendemos tus datos ni los usamos para publicidad.</p>

<h2>Quién procesa tus datos</h2>
<ul>
  <li><strong>WhatsApp (Meta)</strong>, que transmite los mensajes.
      Ver la <a href="https://www.whatsapp.com/legal/privacy-policy" rel="noopener">política de privacidad de WhatsApp</a>.</li>
  <li><strong>Claude, de Anthropic</strong>, el servicio de inteligencia artificial que redacta
      las respuestas automáticas. Para hacerlo recibe la conversación.
      Ver la <a href="https://www.anthropic.com/legal/privacy" rel="noopener">política de privacidad de Anthropic</a>.</li>
  <li>El <strong>proveedor de alojamiento en la nube</strong> donde funciona nuestro servidor.</li>
  <li>Las <strong>personas del equipo de {n}</strong> que atienden los casos que el asistente les pasa.</li>
</ul>

<h2>Cuánto tiempo los guardamos</h2>
<ul>
  <li>Mientras conversas con nosotros, la conversación se usa para atenderte.</li>
  <li>Si pasan <strong>{dias_activa} días</strong> sin mensajes, la conversación se archiva
      y la siguiente vez que escribas empezamos de cero.</li>
  <li>Las conversaciones archivadas se <strong>borran definitivamente a los {dias_archivo} días</strong>.</li>
</ul>
<p>En total, tus mensajes se borran como máximo unos <strong>{maximo} días</strong> después de tu
último mensaje. La excepción son las conversaciones que esperan la atención de una persona del
equipo (por ejemplo, un pago por verificar): se conservan hasta resolver tu caso.</p>
<p>Nuestro servidor también genera registros técnicos temporales que pueden incluir tu número
y fragmentos de mensajes; los usamos solo para corregir errores.</p>
<p>Las copias que existen en tu propio teléfono y en los servidores de WhatsApp no dependen de
nosotros.</p>

<h2>Tus derechos y cómo pedir que borremos tus datos</h2>
<p>De acuerdo con la Ley N.º 29733, Ley de Protección de Datos Personales del Perú, puedes pedir
acceder a tus datos, corregirlos o eliminarlos.</p>
<div class="caja">
  Escríbenos al <strong>mismo número de WhatsApp</strong> en el que conversaste con nosotros,
  por ejemplo: <em>“Quiero que borren mis datos”</em>. Atendemos la solicitud en un plazo máximo
  de {PLAZO_BORRADO_DIAS} días.
</div>
<p>Más detalles en <a href="/eliminacion-datos">Eliminación de datos</a>.</p>

<h2>Cambios</h2>
<p>Si cambiamos esta política, publicaremos la nueva versión en esta misma página con su fecha
de actualización.</p>
""")


def eliminacion_datos() -> str:
    n = config.NOMBRE_NEGOCIO
    return _pagina("Eliminación de datos", f"""
<h1>Cómo eliminar tus datos</h1>
<p class="fecha">Última actualización: {ACTUALIZADO}</p>

<p>Puedes pedir que <strong>{n}</strong> borre los datos que guarda su asistente de WhatsApp:
tu número y los mensajes de tus conversaciones.</p>

<h2>Pasos</h2>
<ol>
  <li>Abre WhatsApp y entra al <strong>mismo chat</strong> en el que conversaste con {n}.</li>
  <li>Escribe un mensaje como: <em>“Quiero que borren mis datos”</em>.</li>
  <li>Recibirás una respuesta indicando que tu solicitud quedó registrada.</li>
  <li>Una persona del equipo borrará tus datos en un plazo máximo de
      <strong>{PLAZO_BORRADO_DIAS} días</strong> y te lo confirmará por el mismo chat.</li>
</ol>
<p>No necesitas dar motivos ni enviar documentos.</p>

<h2>Qué se borra</h2>
<ul>
  <li>Tu número de WhatsApp en nuestra base de datos.</li>
  <li>Todas tus conversaciones guardadas, incluidas las archivadas.</li>
</ul>

<h2>Qué no podemos borrar nosotros</h2>
<ul>
  <li>Las copias de la conversación que están en tu teléfono y en los servidores de WhatsApp.
      Puedes borrar el chat desde tu app.</li>
  <li>Los registros técnicos temporales de nuestro servidor, que se eliminan solos con el tiempo.</li>
  <li>Si hiciste una compra o reserva, la información que debamos conservar por obligación legal
      (por ejemplo, comprobantes de pago).</li>
</ul>

<div class="caja">
  Ten en cuenta que, al confirmarte el borrado por WhatsApp, ese último mensaje vuelve a
  quedar registrado; si escribes de nuevo después, empezamos una conversación nueva.
</div>

<p>Consulta también nuestra <a href="/privacidad">política de privacidad</a>.</p>
""")
