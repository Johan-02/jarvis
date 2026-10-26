"""cliente de Groq con la MISMA cara que el cliente de Anthropic (2026-10-26).

POR QUE EXISTE: Jarvis deja de hablar con Anthropic y pasa a hablar con
Groq. El bucle de main.py (bucle_agente) NO se reescribe: este modulo se
presenta como un objeto con `messages.create(model=..., max_tokens=...,
system=..., tools=..., messages=...)` que devuelve algo con `.content`
(lista de bloques con `.type`, `.text`, `.id`, `.name`, `.input`) y
`.stop_reason` ("end_turn", "tool_use" o "max_tokens"). Asi las 13 suites
que falsean main._obtener_cliente_ia siguen valiendo sin tocarlas.

TRES TRAMPAS MEDIDAS (no son teoria, se midieron con la clave real):

1. CLOUDFLARE (HTTP 403 "error code: 1010"): con las cabeceras por defecto
   de urllib la API rechaza la peticion por la FIRMA del User-Agent. Por eso
   AGENTE_USUARIO se manda SIEMPRE, en la constante y no como opcion: sin el,
   el turno no llega ni a empezar. Lo vigila una prueba.

2. LA IMAGEN NO CABE EN UN role:"tool". En el formato de OpenAI el contenido
   de un mensaje `tool` es TEXTO: meter ahi un bloque de imagen es un 400. La
   captura de pantalla (que main.py manda dentro de un tool_result, ver
   _resultado_con_imagen en main.py) se SACA de ese mensaje y viaja en un
   mensaje `user` APARTE, DESPUES de todos los `tool` del turno. El orden
   importa: los `tool` tienen que ir pegados al assistant que pidio las
   herramientas; la imagen va despues.

3. finish_reason ("stop"/"length") es cosa de OpenAI y en Groq no esta
   confirmado con todas las familias de modelos. Aqui se trata con cuidado:
   lo UNICO que se traduce es "length" -> "max_tokens", que es el aviso que
   main.py ya sabe leer (bug B11). Cualquier otro valor cae en "end_turn",
   que es lo correcto cuando el modelo respondio texto. No se rompe nada si
   Groq manda otra cosa.

4. EL LIMITE DE RITMO (HTTP 429) NO ES UN ERROR, ES "ESPERA". Medido con la
   clave real: la capa gratis da 7.000 tokens de entrada por minuto y una
   frase gasta ~2.700, asi que a la segunda frase seguida llega el 429. El
   cuerpo del error DICE cuanto esperar ("Please try again in 21.68s"), y la
   cabecera Retry-After tambien puede traerlo. Antes un 429 subia como
   ErrorCerebro y main.atender caia a palabras clave: Edward perdia la
   respuesta por ir un poco rapido. Ahora se espera lo que diga el error (con
   tope, ver ESPERA_MAXIMA) y se reintenta; 401, 403 y 400 NO se reintentan
   porque son errores de verdad y esperar no los arregla.

EL MODELO: el `model` que recibe create SE RESPETA. Si viene con valor se
manda ESE en el cuerpo del POST; si viene None o vacio se usa MODELO_GROQ,
que es el de por defecto. main.py pasa MODELO_IA, que hoy vale lo mismo que
MODELO_GROQ: si manana alguien cambia una de las dos constantes sola, la
prueba que las ata (tests/test_cerebro.py) falla, en vez de que el cambio se
pierda en silencio como pasaba antes (create ignoraba `model` y el nombre
leido en el codigo dejaba de ser el que de verdad viajaba).

RED: solo stdlib (urllib). NO se instalan `requests` ni el SDK `groq`: una
dependencia nueva es una decision de Edward, y aqui no hace falta.

LA CLAVE NUNCA SALE: no se imprime, no se guarda en ningun mensaje de error y
no se escribe en ningun archivo. Lo vigila una prueba.
"""

import json
import re
import time
from urllib import error as urllib_error
from urllib import request as urllib_request

# endpoint con formato OpenAI (el unico que publica Groq hoy)
URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"

# modelo elegido y MEDIDO con la clave real: usa herramientas bien en espanol
# y VE la pantalla (se le mando el PNG de verdad y lo describio)
MODELO_GROQ = "qwen/qwen3.8-27b"

# temperatura baja porque la tarea no es escribir prosa: es ELEGIR UNA
# HERRAMIENTA y rellenar sus argumentos. Con temperatura alta el modelo
# inventa variantes del nombre o duda entre dos acciones parecidas, y cada
# eleccion mala cuesta un turno entero (una llamada mas).
TEMPERATURA = 0.2

# cabecera que Cloudflare exige para no devolver 403 "error code: 1010"
AGENTE_USUARIO = "Jarvis/1.0 (Windows NT 10.0; Win64; x64)"

# segundos de espera de la red. 45 es holgado para una respuesta de voz y
# evita que un corte de internet deje el hilo colgado para siempre.
TIEMPO_ESPERA = 45

# el cuerpo de un error se recorta: un HTML de proxy de 40 KB dentro del
# mensaje no ayuda a nadie y ensucia el log
LARGO_MAXIMO_ERROR = 300

# texto que acompania a la captura cuando viaja en su propio mensaje user
TEXTO_DE_LA_CAPTURA = "(captura de pantalla pedida)"

# nota que se deja en el bloque cuando los argumentos venian rotos
NOTA_ARGUMENTOS_ROTOS = "argumentos JSON ilegales: se mando un input vacio"

# --- LIMITE DE RITMO DE GROQ (HTTP 429), medido con la clave real ----------
# la cabecera Retry-After o el cuerpo del error pueden pedir una espera
# LARGUISIMA; un turno de voz no puede quedarse colgado media hora, asi que la
# espera se recorta a este tope. 2 reintentos de 20 s = 40 s como MAXIMO
ESPERA_MAXIMA = 20.0

# cuantas veces se reintenta antes de rendirse (1 intento + 2 reintentos)
REINTENTOS = 2

# si el error no dice cuanto esperar (ni cabecera ni cuerpo), esto
ESPERA_POR_DEFECTO = 5.0

# 429 = demasiadas peticiones; 503 = servicio no disponible un momento. Los
# dos se pasan solos esperando. 400, 401 y 403 son errores de VERDAD (peticion
# mal armada, clave mala, Cloudflare): esperar no los arregla ni un poco
CODIGOS_DE_RITMO = (429, 503)

# "Please try again in 21.685714285s" -> 21.685714285. Se acepta cualquier
# "in <numero>s" porque es el unico dato con segundos que trae el cuerpo
PATRON_ESPERA = re.compile(r"in\s+([\d.]+)\s*s", re.IGNORECASE)


class ErrorCerebro(Exception):
    """error del cerebro con el codigo HTTP y el mensaje de la API

    main.atender ya atrapa CUALQUIER excepcion y cae al camino de palabras
    clave, asi que con que el texto sea legible ("HTTP 429: rate limit ..."
    y nunca la clave) el turno se salva solo.
    """

    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo


class Bloque:
    """bloque de contenido con la misma forma que el de Anthropic

    `type` es "text" o "tool_use"; los atributos que no aplican quedan en
    None. main.py lee `.text` de los text y `.id`, `.name` y `.input` de los
    tool_use, siempre con getattr, asi que un None no lo rompe.
    """

    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class Respuesta:
    """respuesta del modelo: `.content` (lista de Bloque) y `.stop_reason`"""

    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


def _campo(bloque, nombre, defecto=None):
    """campo de un bloque, venga como dict o como objeto (igual que main.py)

    Hace falta de verdad: los tool_result que arma main.py son dicts, pero el
    content del assistant que se le devuelve a la API son los Bloque de ESTE
    modulo (main.py guarda `respuesta.content` tal cual). Un traductor que
    solo entienda dicts revienta en el primer turno con herramientas.
    """
    if isinstance(bloque, dict):
        return bloque.get(nombre, defecto)
    return getattr(bloque, nombre, defecto)


def _texto_de_sistema(sistema):
    """texto plano del system prompt (string, o lista de bloques como admite
    Anthropic; None si no hay)"""
    if sistema is None:
        return None
    if isinstance(sistema, str):
        return sistema
    if isinstance(sistema, list):
        partes = [str(_campo(bloque, "text") or "") for bloque in sistema
                  if _campo(bloque, "type") == "text"]
        return "\n".join(parte for parte in partes if parte)
    return str(sistema)


def _texto_de_tool_result(contenido):
    """TEXTO de un `content` de tool_result (puede ser string o lista)

    De una lista se juntan los bloques de texto; las imagenes NO entran aqui
    (viajan en su propio mensaje, ver _imagenes_de_tool_result).
    """
    if contenido is None:
        return ""
    if isinstance(contenido, str):
        return contenido
    if not isinstance(contenido, list):
        return str(contenido)
    partes = []
    for bloque in contenido:
        if isinstance(bloque, str):
            partes.append(bloque)
            continue
        if _campo(bloque, "type") == "text":
            partes.append(str(_campo(bloque, "text") or ""))
    return "\n".join(parte for parte in partes if parte)


def _imagenes_de_tool_result(contenido):
    """URLs data: de las imagenes de un tool_result, en orden

    La forma del bloque la fija main.py (_bloque_de_imagen): image + source
    base64 con media_type y data. Se devuelve ya el data URL que entiende el
    formato OpenAI.
    """
    if not isinstance(contenido, list):
        return []
    urls = []
    for bloque in contenido:
        if _campo(bloque, "type") != "image":
            continue
        fuente = _campo(bloque, "source") or {}
        datos = _campo(fuente, "data")
        if not datos:
            continue
        media = _campo(fuente, "media_type") or "image/png"
        urls.append("data:%s;base64,%s" % (media, datos))
    return urls


def _assistant_a_openai(bloques):
    """mensaje assistant de Anthropic -> assistant con tool_calls de OpenAI

    El texto va en `content` (None si el modelo solo pidio herramientas) y
    cada tool_use pasa a un tool_call. Los argumentos son un STRING JSON en
    este formato: si se mandara el dict, la API contesta 400.
    """
    textos = []
    llamadas = []
    for bloque in bloques:
        tipo = _campo(bloque, "type")
        if tipo == "text":
            textos.append(str(_campo(bloque, "text") or ""))
            continue
        if tipo != "tool_use":
            continue
        entrada = _campo(bloque, "input")
        if not isinstance(entrada, dict):
            entrada = {}
        llamadas.append({
            "id": _campo(bloque, "id") or "",
            "type": "function",
            "function": {
                "name": _campo(bloque, "name") or "",
                "arguments": json.dumps(entrada, ensure_ascii=False),
            },
        })
    texto = "".join(textos)
    mensaje = {"role": "assistant", "content": texto if texto else None}
    if llamadas:
        mensaje["tool_calls"] = llamadas
    return mensaje


def _a_openai_mensajes(mensajes, sistema=None):
    """mensajes de Anthropic -> mensajes de OpenAI (lista nueva)

    Reglas:
      - el system va PRIMERO, como un mensaje role:"system";
      - user con texto plano pasa igual;
      - assistant con tool_use -> content + tool_calls (ver arriba);
      - CADA tool_result es UN mensaje role:"tool" con su tool_call_id, y
        todos van seguidos, como exige el formato: el tool_call_id es lo que
        ata el resultado con la peticion que lo origino;
      - las imagenes de un tool_result NO caben en un role:"tool", asi que se
        sacan y viajan DESPUES de todos los tool del turno, en un unico
        mensaje user con image_url (ver el punto 2 del encabezado).
    """
    salida = []
    texto_sistema = _texto_de_sistema(sistema)
    if texto_sistema:
        salida.append({"role": "system", "content": texto_sistema})
    for mensaje in mensajes or []:
        rol = _campo(mensaje, "role")
        contenido = _campo(mensaje, "content")
        if isinstance(contenido, str):
            salida.append({"role": rol, "content": contenido})
            continue
        bloques = contenido or []
        if rol == "assistant":
            salida.append(_assistant_a_openai(bloques))
            continue
        # aqui solo puede haber un user (o algo raro): se separan los
        # tool_result de los bloques de texto y de las imagenes
        imagenes = []
        textos = []
        for bloque in bloques:
            if _campo(bloque, "type") == "text":
                textos.append(str(_campo(bloque, "text") or ""))
                continue
            if _campo(bloque, "type") != "tool_result":
                continue
            interior = _campo(bloque, "content")
            salida.append({
                "role": "tool",
                "tool_call_id": _campo(bloque, "tool_use_id") or "",
                "content": _texto_de_tool_result(interior),
            })
            imagenes.extend(_imagenes_de_tool_result(interior))
        texto = "\n".join(parte for parte in textos if parte)
        if imagenes:
            partes = [{"type": "text", "text": TEXTO_DE_LA_CAPTURA}]
            if texto:
                partes.append({"type": "text", "text": texto})
            partes.extend({"type": "image_url",
                           "image_url": {"url": url}} for url in imagenes)
            salida.append({"role": "user", "content": partes})
        elif texto:
            salida.append({"role": "user", "content": texto})
    return salida


def _a_openai_herramientas(definiciones):
    """herramientas de Anthropic -> funciones de OpenAI (lista nueva)

    El esquema de parametros NO se toca: lo que main/herramientas.py genera
    en `input_schema` se manda tal cual en `parameters`. Reescribirlo aqui
    seria un segundo catalogo escondido (y se perderia el `required`, los
    tipos y la regla de que `confirmado` no aparece nunca).
    """
    salida = []
    for definicion in definiciones or []:
        esquema = _campo(definicion, "input_schema")
        if not isinstance(esquema, dict):
            esquema = {"type": "object", "properties": {}}
        salida.append({
            "type": "function",
            "function": {
                "name": _campo(definicion, "name") or "",
                "description": _campo(definicion, "description") or "",
                "parameters": esquema,
            },
        })
    return salida


def _entrada_de_argumentos(argumentos):
    """(dict, nota) a partir del `arguments` de un tool_call

    `arguments` es un STRING JSON en el formato OpenAI. Si viene roto NO se
    revienta el turno: se devuelve {} y una nota. Un input vacio lo rechaza
    el motor con un tool_result claro ("falta el parametro obligatorio"), y
    el modelo se corrige en la vuelta siguiente; tumbar el turno entero por
    una coma de mas seria peor.
    """
    if isinstance(argumentos, dict):
        return argumentos, None
    if not argumentos:
        return {}, None
    try:
        valor = json.loads(argumentos)
    except (TypeError, ValueError):
        return {}, NOTA_ARGUMENTOS_ROTOS
    if not isinstance(valor, dict):
        return {}, NOTA_ARGUMENTOS_ROTOS + " (no era un objeto)"
    return valor, None


def _de_openai_respuesta(datos):
    """respuesta de Groq -> Respuesta (content de Bloque y stop_reason)

    stop_reason: "tool_use" si el modelo pidio herramientas, "max_tokens" si
    finish_reason dice "length" (unico valor que se traduce, ver el punto 3
    del encabezado) y "end_turn" en cualquier otro caso.
    """
    elecciones = _campo(datos, "choices")
    if not isinstance(elecciones, list) or not elecciones:
        raise ErrorCerebro("la respuesta de Groq no trae 'choices'")
    eleccion = elecciones[0]
    mensaje = _campo(eleccion, "message") or {}
    llamadas = _campo(mensaje, "tool_calls") or []
    bloques = []
    texto = _campo(mensaje, "content")
    if texto:
        bloques.append(Bloque("text", text=str(texto)))
    for llamada in llamadas:
        funcion = _campo(llamada, "function") or {}
        entrada, nota = _entrada_de_argumentos(_campo(funcion, "arguments"))
        bloques.append(Bloque(
            "tool_use",
            text=nota,
            id=_campo(llamada, "id"),
            name=_campo(funcion, "name"),
            input=entrada,
        ))
    if llamadas:
        parada = "tool_use"
    elif _campo(eleccion, "finish_reason") == "length":
        parada = "max_tokens"
    else:
        parada = "end_turn"
    if not bloques:
        # el modelo no dijo NADA (content vacio y sin herramientas):
        # preguntar_a_ia lee respuesta.content[0].text y un IndexError aqui
        # se convertiria en "la IA no esta disponible". Un bloque de texto
        # vacio deja el turno en blanco, que es la verdad.
        bloques.append(Bloque("text", text=""))
    return Respuesta(bloques, parada)


def _mensaje_corto_de_error(error):
    """texto legible del cuerpo de un HTTPError, recortado y SIN la clave

    Groq devuelve el motivo dentro de {"error": {"message": ...}}: se saca de
    ahi cuando se puede, y si no, se manda el cuerpo crudo recortado. El
    Authorization no aparece en el cuerpo, pero se recorta igual: un HTML de
    proxy no ayuda a nadie.
    """
    try:
        crudo = error.read()
    except Exception:
        crudo = b""
    if isinstance(crudo, bytes):
        crudo = crudo.decode("utf-8", "replace")
    texto = str(crudo or "")
    try:
        datos = json.loads(texto)
    except (TypeError, ValueError):
        datos = None
    if isinstance(datos, dict):
        detalle = datos.get("error")
        if isinstance(detalle, dict) and detalle.get("message"):
            texto = str(detalle["message"])
        elif datos.get("message"):
            texto = str(datos["message"])
    if not texto.strip():
        texto = "sin detalle en el cuerpo del error"
    return texto[:LARGO_MAXIMO_ERROR]


def _segundos_de_espera(error, detalle):
    """cuantos segundos pide esperar un 429/503, ya recortados al tope

    Tres intentos, en este orden:
      1) la cabecera Retry-After, si viene y es un NUMERO (una fecha no vale:
         no se parsea);
      2) el cuerpo del error ("Please try again in 21.68s"), con PATRON_ESPERA;
      3) ESPERA_POR_DEFECTO si no hay forma de saberlo.

    `detalle` es el cuerpo YA LEIDO por _mensaje_corto_de_error: leerlo otra
    vez devolveria vacio, porque el flujo de un HTTPError se consume al leer.

    Nunca devuelve mas de ESPERA_MAXIMA: un 429 puede pedir 90 s y el turno de
    voz tiene que contestar en menos de un minuto.
    """
    cabecera = None
    try:
        if error.headers:
            cabecera = error.headers.get("Retry-After")
    except Exception:
        cabecera = None
    if cabecera is not None:
        try:
            return min(float(str(cabecera).strip()), ESPERA_MAXIMA)
        except (TypeError, ValueError):
            pass
    encontrado = PATRON_ESPERA.search(str(detalle or ""))
    if encontrado:
        try:
            return min(float(encontrado.group(1)), ESPERA_MAXIMA)
        except (TypeError, ValueError):
            pass
    return min(ESPERA_POR_DEFECTO, ESPERA_MAXIMA)


def _pedir_a_groq(cuerpo, clave):
    """POST al endpoint y devuelve los datos ya decodificados

    Cabeceras obligatorias (el User-Agent no es opcional: sin el, Cloudflare
    responde 403 "error code: 1010"). HTTPError y URLError se convierten en
    ErrorCerebro con un mensaje legible: para arriba nunca sube un traceback
    de urllib, sube un texto que Edward puede leer y entender.

    RITMO (punto 4 del encabezado): si el codigo esta en CODIGOS_DE_RITMO se
    espera lo que pida el error (_segundos_de_espera) y se reintenta hasta
    REINTENTOS veces. Cada espera se IMPRIME, para que en el log se vea que el
    bot no se colgo sino que estaba esperando. Agotados los reintentos sube
    ErrorCerebro como siempre.
    """
    datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
    intentos = 0
    while True:
        peticion = urllib_request.Request(URL_GROQ, data=datos, method="POST")
        peticion.add_header("User-Agent", AGENTE_USUARIO)
        peticion.add_header("Accept", "application/json")
        peticion.add_header("Content-Type", "application/json")
        peticion.add_header("Authorization", "Bearer " + (clave or ""))
        try:
            with urllib_request.urlopen(
                    peticion, timeout=TIEMPO_ESPERA) as respuesta:
                crudo = respuesta.read()
        except urllib_error.HTTPError as error:
            # HTTPError es subclase de URLError: tiene que ir PRIMERO
            # el cuerpo se lee UNA sola vez: sirve para decidir la espera y
            # para el mensaje final
            detalle = _mensaje_corto_de_error(error)
            if error.code in CODIGOS_DE_RITMO and intentos < REINTENTOS:
                espera = _segundos_de_espera(error, detalle)
                intentos += 1
                print("aviso: Groq dio %s, espero %.1f s y reintento "
                      "(%d de %d)" % (error.code, espera, intentos, REINTENTOS))
                time.sleep(espera)
                continue
            raise ErrorCerebro("HTTP %s: %s" % (error.code, detalle),
                               error.code) from None
        except urllib_error.URLError as error:
            motivo = getattr(error, "reason", error)
            raise ErrorCerebro("sin conexion con Groq: %s" % (motivo,)) from None
        except OSError as error:
            # el timeout del socket no siempre llega envuelto en URLError
            raise ErrorCerebro("sin conexion con Groq: %s" % (error,)) from None
        break
    try:
        return json.loads(crudo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErrorCerebro("respuesta de Groq ilegible (no es JSON)") from None


class _Mensajes:
    """el objeto `messages` del cliente: imita messages.create de Anthropic

    Existe solo para que `cliente.messages.create(...)` siga funcionando. Los
    CINCO nombres de la firma son los que usa main.py por nombre (model,
    max_tokens, system, tools, messages) y no se cambian: cambiarlos obliga a
    tocar main.py y a las 13 suites que lo falsean.
    """

    def __init__(self, cliente):
        self._cliente = cliente

    def create(self, model=None, max_tokens=1024, system=None, tools=None,
               messages=None):
        """una llamada al modelo; devuelve Respuesta

        `model` SE RESPETA: si viene con valor se manda ESE, y si viene None
        o vacio se usa MODELO_GROQ (el de por defecto). Junto a `max_tokens`
        y `system`, no se manda nada que la otra API no espere.
        """
        return self._cliente.preguntar(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )


class ClienteGroq:
    """cliente de Groq con la cara del de Anthropic

    NO hace red al construirse: se puede crear con una clave muerta sin que
    nada explote (igual que el cliente viejo, que se construye dentro de
    main._obtener_cliente_ia y solo cuando se pregunta algo).
    """

    def __init__(self, clave):
        self.clave = clave
        self.url = URL_GROQ
        self.messages = _Mensajes(self)

    def preguntar(self, model=None, max_tokens=1024, system=None, tools=None,
                  messages=None):
        """arma el cuerpo OpenAI, llama a Groq y traduce la respuesta

        `model` es el que se manda; si viene vacio se cae en MODELO_GROQ para
        que un create() sin modelo siga funcionando igual que siempre.
        """
        cuerpo = {
            "model": model or MODELO_GROQ,
            "max_tokens": max_tokens,
            "temperature": TEMPERATURA,
            "messages": _a_openai_mensajes(messages, system),
        }
        traducidas = _a_openai_herramientas(tools)
        if traducidas:
            # sin herramientas NO se manda la clave: un `tools: []` vacio es
            # al menos ruido y algunas APIs lo rechazan
            cuerpo["tools"] = traducidas
        return _de_openai_respuesta(_pedir_a_groq(cuerpo, self.clave))
