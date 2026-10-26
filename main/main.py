# importamos la librerias
import datetime # importamos la libreria de fecha y hora
import sounddevice as sd # importamos la libreria de grabacion de audio
import wave # importamos la libreria de manejo de archivos de audio
import speech_recognition as sr # importamos la libreria de reconocimiento de voz
import os # importamos la libreria de manejo de archivos y directorios
from dotenv import load_dotenv # importamos la libreria de manejo de variables de entorno
from anthropic import Anthropic # importamos la libreria de Anthropic para interactuar con la API de IA
import webbrowser # importamos la libreria de manejo de navegadores web
import edge_tts # importamos la libreria de texto a voz de Microsoft
import asyncio # importamos la libreria de manejo de asincronismo
import base64 # codificamos la captura de pantalla para la API (FASE H2)
from playsound import playsound # importamos la libreria de reproduccion de audio
import time # importamos la libreria de manejo de tiempo
import numpy as np # importamos la libreria de manejo de arreglos y operaciones matematicas
import json # importamos la libreria de manejo de archivos JSON
import sys # importamos la libreria de manejo del propio proceso
import urllib.parse # importamos la libreria para codificar urls
import tempfile # importamos la libreria de archivos temporales del sistema
import unicodedata # importamos la libreria para quitar acentos del texto
import inspect # importamos la libreria para leer firmas de funciones (FASE G2)


# el parser de ordenes vive en main/reconocedor.py y tiene que poder
# importarse igual al ejecutar el archivo suelto (python main/main.py, cuyo
# sys.path[0] es la carpeta main/) que al importarlo como paquete
# (import main.main, que es como lo importan las pruebas).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main.reconocedor import interpretar, SIGNOS, Accion
from main import motor
from main import herramientas # generador de herramientas para la API (FASE G1)


# definimos el nombre del asistente
NOMBRE_ASISTENTE = "Jarvis"
FRASE_ACTIVACION_INICIAL = ["jarvis despierta","jarvis, despierta","despierta jarvis", "arriba jarvis", "jarvis es hora de trabajar","a trabajar jarvis"]
FRASE_ACTIVACION_NORMAL = ["hey jarvis", "jarvis","mira jarvis","oye jarvis", "escucha jarvis"]

# modelo de la IA (FASE G2). El haiku 4.5 tenia retiro garantizado el
# 15/10/2026 y esa fecha ya paso: haiku-5-5 es el actual y el mas barato.
# En una constante nombrada para que cambiar de modelo sea UNA linea.
MODELO_IA = "claude-haiku-5-5"


# definimos el nombre del archivo de memoria
# la memoria.json esta en la RAIZ del proyecto (jarvis/memoria.json), NO en
# main/ (alli solo esta el .env). Por eso se sube DOS niveles desde __file__:
# con la ruta relativa de antes ("memoria.json") el programa creaba un
# memoria.json VACIO en el directorio desde el que se arrancara y perdia el
# nombre de Edward y sus notas.
archivo_memoria = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memoria.json")

# definimos la funcion cargar_memoria que carga la memoria del asistente desde un archivo JSON
def cargar_memoria():
    if os.path.exists(archivo_memoria):
        try:
            with open(archivo_memoria, "r", encoding="utf-8") as archivo:
                return json.load(archivo)
        except (json.JSONDecodeError, OSError):
            # un memoria.json corrupto o ilegible no puede matar el arranque:
            # se devuelven los valores por defecto y se avisa, en vez de
            # tragarselo en silencio
            print("aviso: memoria.json ilegible, uso los valores por defecto")
            return {"nombre_usuario": None, "notas": []}
    else:
        return {"nombre_usuario": None, "notas": []}

    
def guardar_memoria(datos):
    # escritura atomica: primero a un temporal y luego os.replace. Si se corta
    # a mitad de escritura, el memoria.json de Edward queda INTACTO en vez de
    # quedar a medias (en modo "w" python trunca el archivo ANTES de escribir).
    archivo_temporal = archivo_memoria + ".tmp"
    try:
        with open(archivo_temporal, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=4)
        os.replace(archivo_temporal, archivo_memoria)
    finally:
        # si algo fallo antes del replace, el temporal no se queda huerfano
        if os.path.exists(archivo_temporal):
            os.remove(archivo_temporal)


def _cargar_memoria_estricta():
    """lee la memoria SIN tapar un archivo roto. Devuelve (datos, error).

    cargar_memoria() devuelve los valores por defecto cuando el json esta
    roto: eso esta bien para ARRANCAR (bug B9), pero seria CATASTROFICO como
    paso previo a guardar, porque reemplazaria el archivo por esos valores y
    las 17 notas de Edward se perderian. Aqui un archivo ilegible es un ERROR
    con su motivo, y recordar/olvidar (FASE H3) no tocan el archivo.

    Es la UNICA puerta de lectura que valida antes de escribir: el motor no
    lee el archivo por su cuenta, se la pide a esta funcion.
    """
    if not os.path.exists(archivo_memoria):
        return {"nombre_usuario": None, "notas": []}, None
    try:
        with open(archivo_memoria, "r", encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
        return None, "memoria.json no es un json valido (%s)" % error
    if not isinstance(datos, dict):
        return None, "memoria.json no contiene un objeto json"
    notas = datos.get("notas")
    if notas is None:
        notas = []
    if not isinstance(notas, list) or not all(
            isinstance(nota, str) for nota in notas):
        return None, "el campo notas de memoria.json no es una lista de textos"
    datos["notas"] = notas
    datos.setdefault("nombre_usuario", None)
    return datos, None


# FASE H3: el motor agrega y quita notas por estas DOS funciones y por
# ninguna otra ruta. Se conectan aqui, despues de definirlas: el motor no sabe
# donde vive el archivo (no puede apuntar a otro) y la escritura sigue siendo
# la atomica de la FASE B (guardar_memoria).
motor.conectar_memoria(_cargar_memoria_estricta, guardar_memoria)


# cargamos las variables de entorno desde el archivo .env
ruta_env = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ruta_env) 

# obtenemos la clave de API de Anthropic
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

# el cliente de la IA se crea SOLO cuando se pregunta algo, nunca al importar
# el modulo: construirlo con una clave muerta o ausente haria el modulo
# ingobernable y las pruebas no podrian ni importarlo.
_cliente_ia = None

def _obtener_cliente_ia():
    global _cliente_ia
    if _cliente_ia is None:
        _cliente_ia = Anthropic(api_key=anthropic_api_key)
    return _cliente_ia

# como se formatea la memoria del usuario cuando se le inyecta a la IA
# (decision de diseno de la FASE D, bug B5). Elegimos bloque de TEXTO PLANO y
# no JSON por dos motivos: el system prompt ya es prosa y un JSON entre
# medias le cuesta mas al modelo leerlo, y si un dia se imprime el prompt
# para depurar, Edward lo entiende sin traducirlo. Formato:
#
#   Memoria del usuario (ya esta incluida en este mensaje):
#   - Nombre: Yohan            (o "desconocido" si aun no lo hay)
#   - Nota 1: le gusta el chocolate
#   - Nota 2: aprende django
#
# Las notas van NUMERADAS y van TODAS, sin recortar: recortar seria decidir
# por Edward lo que la IA puede saber de el.
def _texto_de_memoria(memoria):
    nombre = memoria.get("nombre_usuario")
    notas = memoria.get("notas") or []
    lineas = ["Memoria del usuario (ya esta incluida en este mensaje):"]
    lineas.append("- Nombre: " + (str(nombre) if nombre else "desconocido"))
    for indice, nota in enumerate(notas, start=1):
        lineas.append("- Nota %d: %s" % (indice, nota))
    if not notas:
        lineas.append("- Notas: ninguna anotada todavia")
    return "\n".join(lineas)


# system prompt de la IA: identidad, memoria del usuario y reglas de VERDAD.
# Vive en su propia funcion porque lo comparten preguntar_a_ia (camino
# simple) y bucle_agente (FASE G2, con herramientas): un solo texto, un solo
# sitio donde corregirlo.
#
# LAS REGLAS DE ABAJO DICEN LA VERDAD (FASE G2): el modelo TIENE
# herramientas reales (las genera main/herramientas.py desde el REGISTRO del
# motor) y tiene que usarlas; lo que esta fuera de sus herramientas lo dice
# honestamente y NUNCA afirma haber ejecutado algo que no ejecuto. Antes
# estas lineas pedian lo CONTRARIO: pedian confirmar como si ya lo hubiera
# hecho, cuando el motor todavia no existia. Era mentirle a Edward en voz.
# Con el motor y sus 41 acciones reales, esas lineas se borraron.
def _system_de(memoria):
    """system prompt completo de la IA para la memoria dada"""
    return f"""Eres {NOMBRE_ASISTENTE}, el asistente personal de voz de tu usuario. Te identificas como hombre.

      {_texto_de_memoria(memoria)}

      Aclaración importante: tú SÍ tienes acceso a la información de memoria que se te proporciona arriba (nombre y notas del usuario) — nunca digas que no tienes acceso a esa información, ya está incluida en este mensaje.

      Tienes herramientas reales para actuar en la PC del usuario (abrir aplicaciones y páginas, decir la hora, manejar el volumen, revisar disco, CPU y procesos, etcétera): úsalas cuando la petición corresponda a una de ellas, en lugar de describir lo que harías. Si algo queda fuera de lo que tus herramientas permiten, dilo con honestidad: no inventes capacidades ni prometas acciones que no vas a ejecutar. Nunca afirmes haber hecho algo que no ejecutaste: sin resultado real, no digas "listo" ni "hecho".

Tu personalidad:
- Hablas con calma y seguridad, como alguien que siempre tiene todo bajo control.
- Usas un humor sutil y seco de vez en cuando, sin exagerar.
- Eres directo y eficiente, pero cercano, no frío.
- Te diriges al usuario de forma respetuosa (puedes usar "usted" ocasionalmente, sin ser demasiado formal todo el tiempo).

Reglas de formato:
- Respondes siempre en texto plano, sin símbolos de markdown (nada de #, *, **, guiones de lista) y sin emojis.
- Tus respuestas son breves y naturales, y lo mas resumido, como si hablaras en voz alta, no como si escribieras un documento."""


# definimos la funcion preguntar_a_ia que recibe un texto y devuelve la
# respuesta de la IA. Es el camino SIMPLE de un solo turno, SIN herramientas:
# es el que usa la accion consultar_ia. El bucle con herramientas es
# bucle_agente, mas abajo (FASE G2).
def preguntar_a_ia(texto_usuario, memoria=None):
    # la memoria viaja DENTRO del system prompt, que es donde el propio
    # prompt prometia al modelo que estaba: antes decia "tienes acceso a la
    # informacion de memoria que se te proporciona arriba (nombre y notas del
    # usuario)" y no se le pasaba NADA, o sea que le mentiamos al modelo y
    # Jarvis acababa diciendole a Edward que conocia su nombre sin conocerlo
    # (bug B5). Se carga sola si no se la pasa: quien pregunta no tiene que
    # acordarse de cargarla.
    if memoria is None:
        memoria = cargar_memoria()
    respuesta = _obtener_cliente_ia().messages.create(
        model=MODELO_IA,
        # 200 tokens eran unas 150 palabras: cortaban las respuestas de un
        # modelo de 64K de contexto a media frase. 1024 da para una respuesta
        # larga de voz y aun asi es barata (bug B11)
        max_tokens=1024,
        system=_system_de(memoria),

        messages=[
            {"role": "user", "content": texto_usuario}
        ]
    )
    texto = respuesta.content[0].text
    # si la IA se quedo sin tokens, el texto sale A MEDIAS. En vez de leerle
    # a Edward una frase cortada como si estuviera completa, se avisa por los
    # DOS canales: en consola, para quien tenga la terminal, y al final del
    # propio texto, que es lo que luego se lee en voz (bug B11)
    if getattr(respuesta, "stop_reason", None) == "max_tokens":
        print("aviso: la respuesta de la IA se corto por el limite de "
              "max_tokens; puede venir incompleta")
        texto += " Aviso: mi respuesta se cortó por límite de texto."
    return texto


# definimos la funcion hablar que recibe un texto y lo convierte a voz usando la voz de GonzaloNeural

def hablar(texto):
    print("hablando: " + texto)
    # el mp3 vive en el temp del sistema, NUNCA en el directorio de trabajo:
    # jarvis se arranca desde la raiz del proyecto y ahi se acumulaban los mp3
    # huerfanos (186 archivos, 7 MB sueltos; un dia entero de uso, ~1.150
    # archivos y 43 MB). El nombre lleva el pid y la hora para que dos
    # habladas simultaneas no pisen el mismo archivo (bug B14)
    nombre_archivo = os.path.join(
        tempfile.gettempdir(),
        "jarvis_voz_%s_%s.mp3" % (os.getpid(), time.time()))

    async def generar_voz():
        comunicador = edge_tts.Communicate(texto, "es-CO-GonzaloNeural", rate="+15%")
        await comunicador.save(nombre_archivo)

    try:
        asyncio.run(generar_voz())
        playsound(nombre_archivo)
    except Exception as error:
        # SIN INTERNET edge_tts revienta (ConnectionError) y un playsound
        # roto tambien: NINGUNO de los dos puede MATAR a Jarvis. Antes esta
        # excepcion se propagaba y con un corte de wifi el programa moria en
        # la primera frase. Ahora se avisa por consola CON TIPO Y MENSAJE
        # (nunca en silencio) y se sigue: no hay voz, pero Jarvis sigue vivo
        # y por lo menos avisa en pantalla.
        #
        # OJO: es `except Exception`, NO `except:` a secas. KeyboardInterrupt
        # (Ctrl+C) es BaseException, no Exception, y por eso SIGUE SALIENDO
        # afuera: Edward tiene que poder parar el programa (regla B3). Si
        # esto se convirtiera en `except:` el Ctrl+C se tragaria y Jarvis ya
        # no se podria detener.
        print("aviso: no pude hablar: %s: %s"
              % (type(error).__name__, error))
    finally:
        # el mp3 se borra SIEMPRE, tambien si edge_tts o playsound revientan.
        # playsound 1.2.2 a veces se queja si el archivo desaparece a el, y
        # por eso el borrado es tolerante al error en vez de tragarlo o de
        # dejar el archivo tirado
        try:
            if os.path.exists(nombre_archivo):
                os.remove(nombre_archivo)
        except OSError:
            print("aviso: no se pudo borrar el mp3 de voz %s" % nombre_archivo)

# definimos la funcion grabar_hasta_silencio que graba audio hasta detectar un
# periodo de silencio, o hasta un TOPE DURADO de 20 segundos (bug B7).
# Por que 20 segundos: una orden de voz normal entra en menos de 10 segundos,
# y 20 deja holgura comoda para una instruccion larga. Sin tope, un ruido de
# fondo continuo por encima del umbral de 1300 dejaba a Jarvis en
# "Escuchando..." para siempre y los bloques crecian ~317 MB por hora. Si se
# llega al tope se devuelve LO GRABADO y se AVISA: mejor una frase cortada y
# anunciada que una charla perdida en silencio.
def grabar_hasta_silencio(frecuencia=44100, umbral_silencio=1300,
                          segundos_silencio=2, max_segundos=20):
    bloques = []
    silencio_contador = 0
    transcurrido = 0.0
    duracion_bloque = 0.5  # medio segundo por bloque
    while True:
        bloque = sd.rec(int(duracion_bloque * frecuencia), samplerate=frecuencia, channels=1, dtype='int16')
        sd.wait()
        bloques.append(bloque)
        transcurrido += duracion_bloque
        volumen = np.abs(bloque).mean()

        if volumen < umbral_silencio:
            silencio_contador += duracion_bloque
        else:
            silencio_contador = 0

        if silencio_contador >= segundos_silencio:
            break

        if transcurrido >= max_segundos:
            print("aviso: grabacion cortada por el tope de %.1f segundos sin "
                  "detectar silencio; puede haberse tragado el final de su "
                  "frase" % max_segundos)
            break

    audio_completo = np.concatenate(bloques)
    return audio_completo


# momento del dia de una hora dada. Funcion PURA: recibe el instante como
# argumento y no llama a datetime.now() ella sola, asi que el reloj se puede
# inyectar en las pruebas y nunca depende de la hora real del sistema.
# Antes esto era una constante de modulo (ahora/momento_dia/hora_actual),
# evaluada UNA sola vez al importar, asi que si Jarvis corria dos horas
# seguia mintiendo con la hora vieja.
def momento_del_dia(momento):
    if momento.hour < 12:
        return "mañana"
    elif momento.hour < 18:
        return "tarde"
    else:
        return "noche" # definimos el momento del dia

# definimos la frecuencia de muestreo para la grabación de audio
frecuencia = 44100  # calidad de audio estándar
r = sr.Recognizer() # inicializamos el reconocedor de voz


# FASE G4: la confirmacion de una accion destructiva se escucha por
# MICROFONO, no por teclado. Esta es la funcion que POR DEFECTO reciben
# bucle_agente y atender como `escuchar`; el teclado (input) sigue
# disponible SOLO inyectado, para las pruebas.
def escuchar_confirmacion():
    """escucha por MICROFONO la respuesta a la pregunta de confirmacion

    Devuelve el TEXTO entendido por Google (es-ES), o None cuando NO se
    entiende NADA: silencio, voz ilegible (sr.UnknownValueError), sin
    microfono (grabar_hasta_silencio revienta) o sin internet
    (sr.RequestError).

    LA REGLA DE SEGURIDAD DE LA FASE G4: None significa RECHAZAR, nunca
    aceptar. Un silencio JAMAS se interpreta como "si". Quien decide si se
    ejecuta una accion destructiva es _acepta_confirmacion, y solo acepta
    con claridad; si esta funcion falla por cualquier motivo, el unico
    camino posible es NO ejecutar. Por eso se captura cualquier Exception
    del audio y se devuelve None con aviso IMPRESO, sin tragarse el error
    en silencio (un Ctrl+C sigue saliendo: es BaseException, no Exception).
    """
    print("Escuchando su confirmacion...")
    try:
        audio = grabar_hasta_silencio(frecuencia)
        # el patron de siempre (grabar + recognize_google es-ES), pero SIN
        # escribir un wav al disco: el AudioData va directo al reconocedor,
        # sin dejar archivos en el directorio de trabajo
        datos = sr.AudioData(audio.tobytes(), frecuencia, 2)
        return r.recognize_google(datos, language="es-ES")
    except (sr.UnknownValueError, sr.RequestError) as error:
        print("aviso: no se reconocio la confirmacion (%s)"
              % type(error).__name__)
        return None
    except Exception as error:
        # microfono ausente o roto (PortAudioError) u otro fallo de audio:
        # se imprime tipo y mensaje y se devuelve None = RECHAZAR
        print("aviso: fallo el microfono de confirmacion (%s: %s)"
              % (type(error).__name__, error))
        return None


# ---------------------------------------------------------------------------
# LOS WAVS DE GRABACION (arreglo del 2026-10-25, misma familia que el B14 de
# hablar). Los tres wavs que Jarvis escribe para reconocer voz
# (nombre_usuario.wav, grabacion.wav y escucha_activacion.wav) eran RUTAS
# RELATIVAS: caian en el directorio de trabajo (la raiz del proyecto al
# arrancar), NUNCA se borraban y DOS INSTANCIAS se pisaban el mismo archivo.
# Ahora van al temp del sistema, con nombre unico por grabacion, y se
# borran apenas se leen (tolerando el error, igual que el mp3). El
# comportamiento visible NO cambia: sigue siendo grabar -> reconocer -> hablar.
# ---------------------------------------------------------------------------

# crea el wav UNICO de una grabacion y devuelve su ruta en el temp del
# sistema. mkstemp garantiza la unicidad tambien entre DOS procesos de
# Jarvis a la vez; el archivo sale creado vacio, wave.open lo re-escribe y
# quien lo borra siempre es _borrar_wav, en el finally de cada grabacion
def _ruta_wav_unico(prefijo):
    descriptor, ruta = tempfile.mkstemp(
        prefix="jarvis_%s_" % prefijo, suffix=".wav")
    os.close(descriptor)
    return ruta


# borra el wav de una grabacion tolerando el error (mismo criterio que el
# mp3 de hablar: mejor un aviso impreso que un archivo basura retenido)
def _borrar_wav(ruta):
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        print("aviso: no se pudo borrar el wav %s" % ruta)


# definimos la funcion pedir_nombre: pregunta y graba el nombre del usuario
# SOLO la primera vez, si la memoria todavia no lo tiene. Es codigo de
# arranque, por eso vive en una funcion y no a nivel de modulo: importar el
# archivo no tiene que pedir microfono ni hablar.
def pedir_nombre(memoria):
    hablar("Antes de comenzar, ¿cómo desea que lo llame mi señor?")
    audio_nombre = grabar_hasta_silencio(frecuencia)
    # la MISMA variable para escribir y para leer: el wav vive un solo
    # instante en el temp del sistema y no puede quedarse basura ni pisarse
    # entre dos instancias de Jarvis
    ruta_wav = _ruta_wav_unico("nombre_usuario")
    try:
        with wave.open(ruta_wav, "wb") as archivo:
            archivo.setnchannels(1)
            archivo.setsampwidth(2)
            archivo.setframerate(frecuencia)
            archivo.writeframes(audio_nombre.tobytes())

        try:
            with sr.AudioFile(ruta_wav) as source:
                datos_audio = r.record(source)
                nombre_reconocido = r.recognize_google(datos_audio, language="es-ES")
                print("Nombre reconocido: " + nombre_reconocido)

                memoria["nombre_usuario"] = nombre_reconocido
                guardar_memoria(memoria)  # guardamos el nombre del usuario en la memoria
                hablar("Perfecto, a partir de ahora su nombre quedará registrado por siempre en mi memoria señor " + nombre_reconocido + ". ¿Desea que haga algo más por usted?")

        # solo los errores RECONOCIMIENTO de voz: sin internet (RequestError) o
        # sin voz entendible (UnknownValueError). Un Ctrl+C (KeyboardInterrupt)
        # NO entra aqui y tiene que salir afuera.
        except (sr.UnknownValueError, sr.RequestError):
            hablar("No logré escucharlo bien. Intentemos de nuevo la próxima vez que me inicie.")
    finally:
        # el wav se borra SIEMPRE, tambien si el reconocimiento revienta
        _borrar_wav(ruta_wav)

# ---------------------------------------------------------------------------
# CONEXION CON EL MOTOR (FASE E2a). Tres piezas:
#
#   1. TRADUCCION_MOTOR: el reconocedor nombra sus acciones "hora", "abrir",
#      "reproduce" y "consultar_ia"; el REGISTRO del motor las llama
#      "hora_actual", "volumen_leer" y "papelera_vaciar". Un UNICO
#      diccionario explicito traduce de un vocabulario al otro. Se eligio el
#      diccionario (y no renombrar en el reconocedor) porque los dos
#      modulos conservan SU vocabulario y el corte entre ellos queda visible
#      en un solo sitio: si un dia el motor renombra una accion, solo se
#      toca esta tabla y nada mas.
#   2. ejecutar_accion: si el nombre traducido esta en REGISTRO, manda la
#      accion al motor (motor.ejecutar) y su Resultado se traduce en voz o
#      en aviso de consola. Si NO esta, manda la cadena if/elif de siempre.
#   3. _accion_pendiente: una accion destructiva NO se ejecuta; se guarda y
#      Jarvis pregunta en voz. La frase siguiente del usuario es la
#      respuesta, y la resuelve resolver_confirmacion.
#
# REGLA DE LIMPIEZA (no negociable): _accion_pendiente se borra ANTES de
# decidir cualquier cosa, asi que en las TRES ramas posibles (aceptar,
# rechazar, frase normal) el estado queda limpio y NUNCA puede quedar una
# accion zombi esperando confirmacion para siempre.
# ---------------------------------------------------------------------------

# nombre con el que el reconocedor llama a la accion -> nombre con el que la
# llama el REGISTRO del motor. Las demas acciones del reconocedor ("abrir",
# "reproduce", "consultar_ia") no estan en el REGISTRO y siguen su camino de
# siempre sin pasar por el motor
TRADUCCION_MOTOR = {"hora": "hora_actual"}

# accion (YA traducida, lista para el motor) esperando la confirmacion de
# Edward, o None si no hay ninguna
_accion_pendiente = None

# lo que Edward puede decir para aceptar o rechazar una accion pendiente. Se
# comparan PALABRAS sueltas dentro de la frase: en voz real Edward no dice
# solo la palabra de la lista ("si, hazlo" o "dale pues"), y por eso "y si"
# no hace falta en la lista: el "si" ya casa como palabra suelta
FRASES_ACEPTAR = {"si", "dale", "ok", "hazlo"}
FRASES_RECHAZAR = {"no", "para", "cancela"}


def _sin_acentos(texto):
    """quita los acentos de un texto (se baja a minusculas tambien)

    Sin esto, "si" con tilde (que el reconocedor de Google devuelve a veces)
    no casaria con FRASES_ACEPTAR y la confirmacion de Edward se perdia en
    silencio.
    """
    return "".join(
        letra for letra in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(letra) != "Mn")


def _palabras_de(texto):
    """palabras de la frase, en minusculas, sin acentos y sin signos

    La limpieza de signos es la misma del reconocedor (SIGNOS): "hazlo?"
    tiene que casar con "hazlo".
    """
    return [palabra.strip(SIGNOS)
            for palabra in _sin_acentos(texto).split()]


def _decir_resultado(resultado):
    """lo que main.py hace con un Resultado del MOTOR: hablar o avisar

    El motor NUNCA habla ni imprime (esa es su regla), asi que quien le
    cuenta la cosa a Edward es main.py, aqui y en ejecutar_accion. Un
    resultado fallido se avisa en consola y NO se lee en voz: es un error
    tecnico, no una respuesta para Edward.
    """
    if resultado.ok:
        hablar(resultado.detalle)
    else:
        print("aviso: no se ejecuto la accion: " + resultado.detalle)


def olvidar_pendiente():
    """borra la accion pendiente de confirmacion, si la hay"""
    global _accion_pendiente
    _accion_pendiente = None


def resolver_confirmacion(texto):
    """resuelve la frase del usuario si hay una accion esperando confirmacion

    Devuelve True si la frase fue la RESPUESTA a la pregunta de confirmacion
    (entonces procesar_orden NO la procesa como orden normal), y False si no
    hay pendiente o la frase es una orden normal, que se procesa igual que
    siempre.

    Reglas, decididas por Edward ("preguntar solo lo que no se deshace"):
      - palabra de aceptacion -> motor.ejecutar(pendiente, confirmado=True)
      - palabra de rechazo -> no se ejecuta NADA
      - cualquier otra frase -> se ignora como respuesta y se procesa como
        orden normal
    En las tres ramas el pendiente queda BORRADO antes de nada: no puede
    quedar zombi. Si la frase trae palabras de las DOS listas a la vez
    ("no, dale"), gana el RECHAZO: ante la duda, no se destruye nada.
    """
    global _accion_pendiente
    if _accion_pendiente is None:
        return False
    pendiente = _accion_pendiente
    _accion_pendiente = None  # se limpia ANTES de cualquier cosa
    palabras = set(_palabras_de(texto))
    if FRASES_RECHAZAR & palabras:
        return True
    if FRASES_ACEPTAR & palabras:
        _decir_resultado(motor.ejecutar(pendiente, confirmado=True))
        return True
    return False


# definimos la funcion ejecutar_accion, que ejecuta UNA accion devuelta por el
# reconocedor. Es el unico sitio del programa que abre el navegador o le pasa
# algo a la IA; el reconocedor jamas llega aqui.
def ejecutar_accion(accion):
    global _accion_pendiente
    # FASE E2a: si el nombre (YA traducido) esta en el REGISTRO del motor,
    # la ejecucion es del MOTOR: el decide si se puede (freno de las
    # destructivas incluido) y devuelve su Resultado, que aqui se traduce en
    # voz o en aviso. Si NO esta en el REGISTRO, la accion se resuelve con
    # la cadena if/elif de abajo, SIN NADA cambiado respecto de antes.
    nombre_motor = TRADUCCION_MOTOR.get(accion.nombre, accion.nombre)
    if nombre_motor in motor.REGISTRO:
        resultado = motor.ejecutar(Accion(nombre_motor, accion.argumentos))
        if resultado.detalle == motor.REQUIERE_CONFIRMACION:
            # destructiva sin confirmar: NO se ejecuta NADA. Se guarda la
            # accion (ya traducida) y Jarvis pregunta en voz; la respuesta
            # del usuario se resuelve en resolver_confirmacion, que es la
            # unica que puede llamar al motor con confirmado=True
            _accion_pendiente = Accion(nombre_motor, accion.argumentos)
            hablar("Esa accion no se puede deshacer, señor. "
                   "Diga si quiere que la ejecute.")
            return
        _decir_resultado(resultado)
        return
    # la cadena if/elif de abajo es el camino de TODA accion fuera del
    # REGISTRO. La rama "hora" hoy queda SOMBRREADA por el motor (el
    # traductor la manda a hora_actual), pero se deja intacta como estaba:
    # si un dia "hora" sale del REGISTRO, el asistente no se queda sin poder
    # decir la hora
    if accion.nombre == "hora":
        # la hora se calcula AQUI, en el momento de decirla, y no como una
        # constante de modulo: si no, mentiria si el programa lleva horas
        # corriendo
        ahora = datetime.datetime.now()
        hablar("la hora actual es: " + ahora.strftime("%H:%M") + " de la " + momento_del_dia(ahora))
    elif accion.nombre == "abrir":
        sitio, url = accion.argumentos
        hablar("Abriendo " + sitio + " en el navegador")
        webbrowser.open(url)
    elif accion.nombre == "reproduce":
        busqueda = accion.argumentos[0]
        hablar("Reproduciendo " + busqueda)
        # se abre la busqueda de YouTube en el navegador. La busqueda se
        # codifica con urllib.parse.quote: los acentos y la ENIE rompen la
        # url si se concatenan crudas.
        webbrowser.open(
            "https://www.youtube.com/results?search_query=" +
            urllib.parse.quote(busqueda))
    elif accion.nombre == "consultar_ia":
        respuesta_ia = preguntar_a_ia(accion.argumentos[0])
        print("Respuesta de la IA: " + respuesta_ia)
        hablar(respuesta_ia)
    else:
        # una accion que el motor todavia no sabe ejecutar se AVISA en
        # pantalla, no se traga en silencio
        print("aviso: no se ejecuto la accion " + accion.nombre)


# ---------------------------------------------------------------------------
# EL BUCLE AGENTICO (FASE G2). Tres piezas:
#
#   1. bucle_agente: le manda el texto al modelo CON las herramientas
#      (generadas por main/herramientas.py desde motor.REGISTRO). Si el
#      modelo responde con tool_use, CADA peticion se ejecuta en el MOTOR
#      (motor.ejecutar) y su resultado se le devuelve como tool_result en
#      otra llamada. Se repite hasta que el modelo devuelva TEXTO, que es lo
#      que se le dice a Edward, o hasta TOPE_BUCLE_AGENTE iteraciones.
#
#   2. LA CONFIRMACION no la decide ni el bucle ni el modelo. Cuando el
#      motor devuelve REQUIERE_CONFIRMACION, EL PROGRAMA le pregunta a Edward
#      CON VOZ, escucha su respuesta con `escuchar` y solo entonces vuelve a
#      llamar a motor.ejecutar(..., confirmado=True) si Edward acepto. Ese
#      resultado (o el rechazo) es lo unico que llega al modelo. Motivo:
#      `confirmado` no esta en NINGUN input_schema (lo garantiza
#      tests/test_herramientas.py); si ademas lo decidiera el modelo no
#      habria freno alguno: podria apagar la PC sin preguntar nunca. El
#      freno vive en motor.ejecutar y lo maneja EL PROGRAMA.
#
#   3. atender: la puerta de entrada. Si HAY CLAVE usa el bucle; si no la
#      hay, o la API revienta (401, sin red), el bucle NO se usa y se cae al
#      camino de HOY: interpretar() por palabras clave con las 41 acciones
#      del REGISTRO. El modo offline no depende de red ni de clave.
#
# FASE G3: procesar_orden SI llama a atender. La frase reconocida pasa
# PRIMERO por resolver_confirmacion (E2a); si no era la respuesta a un
# pendiente, va a atender, que elige el camino (con clave el bucle, sin
# clave interpretar). LA CONFIRMACION SE RESUELVE UNA SOLA VEZ POR FRASE, y
# los dos circuitos viven en caminos MUTUAMENTE EXCLUSIVOS:
#   - SIN clave (o API caida): atender cae a ejecutar_accion, que guarda el
#     pendiente y pregunta; la frase siguiente la resuelve
#     resolver_confirmacion, que corre ANTES de atender en procesar_orden.
#   - CON clave: _ejecutar_herramienta pregunta y escucha el mismo dentro
#     del bucle (G2) y NUNCA deja pendiente, asi que resolver_confirmacion
#     no tiene nada que resolver en ese camino.
# Por eso una destructiva pregunta UNA sola vez sea cual sea el camino, y
# tests/test_fase_g3.py lo CUENTA con un hablar que suma las llamadas: si
# alguna vez sale 2, los dos circuitos estarian vivos a la vez y la prueba
# revienta.
# ---------------------------------------------------------------------------

# tope de iteraciones del bucle agente. POR QUE 5: cinco llamadas alcanzan
# una cadena razonable (pedir una herramienta, recibir el resultado, pedir
# otra y responder) y a la vez es un techo contra un modelo que pide
# herramientas sin parar: cada iteracion cuesta una llamada a la API y
# tiempo de voz, y sin tope un modelo en bucle dejaria a Jarvis hablando
# para siempre. Al llegar al tope se devuelve un texto HONESTO diciendo que
# se corto, no una respuesta inventada.
TOPE_BUCLE_AGENTE = 5

# pregunta de confirmacion que EL PROGRAMA (nunca el modelo) le hace a Edward
# con voz, cuando el motor frena una accion destructiva
PREGUNTA_CONFIRMACION = (
    "Atención, señor: la acción %s no se puede deshacer. "
    "¿Quiere que la ejecute? Diga sí o no.")

# FASE G4: tope de reintentos de ESCUCHA cuando la respuesta no se entiende
# (silencio, voz ilegible, microfono o red caidos). POR QUE 2: son tres
# intentos en total, holgados para una voz cansada o un ruido de fondo, y a
# la vez un techo que impide quedar preguntando para siempre. Al agotarse se
# RECHAZA y se dice claramente: NUNCA se acepta por silencio.
TOPE_REINTENTOS_CONFIRMACION = 2

# re-pregunta hablada entre intento y intento de escucha
PREGUNTA_REINTENTO = (
    "Disculpe, no logré escucharlo bien. ¿Sí o no, por favor?")

# aviso hablado cuando se agotan los reintentos sin entender NADA. Tiene
# que decir sin ambiguedades que la accion NO se ejecuto
AVISO_RECHAZO_ESCUCHA = (
    "No logré escuchar su respuesta, señor. Por seguridad NO ejecuto "
    "la acción.")


def _tiene_clave_ia():
    """True si hay una clave de API utilizable para el bucle agente

    Sin clave (ausente, None o vacia) el bucle NO se usa: atender se cae al
    camino de siempre (interpretar por palabras clave). Asi ninguna prueba
    ni ningun uso offline depende de red ni de clave.
    """
    return bool(anthropic_api_key and anthropic_api_key.strip())


def _texto_de_respuesta(respuesta):
    """texto plano de TODOS los bloques text de una respuesta del modelo"""
    return "".join(
        bloque.text for bloque in respuesta.content
        if getattr(bloque, "type", None) == "text")


def _argumentos_de(funcion, entrada):
    """(argumentos, error) para llamar a `funcion` con el input del modelo

    El input del tool_use es un dict con nombres de parametro; el motor solo
    acepta argumentos posicionales (Accion.argumentos), y por eso aqui se
    traduce EN EL ORDEN de la firma. Si falta un parametro obligatorio se
    devuelve un mensaje de error: nunca se lanza una excepcion, porque un
    tool_use malformado tiene que dar un tool_result claro y seguir, no
    tumbar el bucle.
    """
    if not isinstance(entrada, dict):
        return None, ("el input de la herramienta no es un objeto: %r"
                      % (entrada,))
    argumentos = []
    for parametro in inspect.signature(funcion).parameters.values():
        if parametro.kind in (inspect.Parameter.VAR_POSITIONAL,
                              inspect.Parameter.VAR_KEYWORD):
            continue
        if parametro.name in entrada:
            argumentos.append(entrada[parametro.name])
        elif parametro.default is inspect.Parameter.empty:
            return None, ("falta el parametro obligatorio %r"
                          % (parametro.name,))
    return tuple(argumentos), None


def _resultado_de_tool_use(bloque, texto, es_error=False):
    """bloque tool_result, con el id de la peticion que lo origino"""
    return {
        "type": "tool_result",
        "tool_use_id": getattr(bloque, "id", ""),
        "content": texto,
        "is_error": es_error,
    }


def _bloque_de_imagen(ruta):
    """bloque image de la API con el PNG de `ruta`. Devuelve (bloque, error)

    La API de Anthropic NO acepta BMP: solo image/jpeg, image/png, image/gif
    e image/webp. El motor ya entrega un PNG reducido, asi que aqui no se
    convierte nada: se lee el archivo y se codifica en base64, que es el
    formato exacto que espera el bloque. Un archivo que no se puede leer se
    devuelve como error de texto y el bucle SIGUE: no mirar la pantalla nunca
    puede tumbar el turno.
    """
    try:
        with open(ruta, "rb") as archivo:
            datos = archivo.read()
    except OSError as error:
        return None, "no pude leer la captura %s: %s" % (ruta, error)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(datos).decode("ascii"),
        },
    }, None


def _resultado_con_imagen(bloque, ruta, nota=""):
    """tool_result con el TEXTO y la IMAGEN de la captura de pantalla

    PRIVACIDAD: esta funcion es el UNICO sitio por el que una captura de la
    pantalla viaja a la API de Anthropic. Solo se llega aqui cuando el modelo
    pidio la herramienta mirar_pantalla, o sea cuando Edward pregunto algo que
    necesita ver la pantalla. NO es contexto automatico ni se manda en cada
    turno: mandar la pantalla significa que todo su contenido (correos,
    chats, claves escritas) viaja a Anthropic, y por eso es una herramienta
    explicita. Por el mismo motivo el historial guarda el texto pero NO la
    imagen (ver _sin_imagenes): si se guardara, cada turno siguiente la
    reenviaria sin que nadie la pidiera y pagando los tokens otra vez.

    Si el archivo no se puede leer se devuelve el error como texto: el modelo
    se entera y el turno sigue.
    """
    imagen, error = _bloque_de_imagen(ruta)
    if error is not None:
        return _resultado_de_tool_use(bloque, error + nota, es_error=True)
    return {
        "type": "tool_result",
        "tool_use_id": getattr(bloque, "id", ""),
        "content": [
            {"type": "text",
             "text": "Aqui tienes una captura de la pantalla en PNG." + nota},
            imagen,
        ],
        "is_error": False,
    }


def _acepta_confirmacion(texto):
    """True SOLO si Edward acepto con claridad; ante la duda NO se ejecuta

    Se reusan las MISMAS listas de frases del circuito de voz
    (FRASES_ACEPTAR y FRASES_RECHAZAR) y el mismo limpiador de palabras
    (_palabras_de), y al igual que en resolver_confirmacion el RECHAZO gana
    si la frase trae palabras de las dos listas: ante la duda, no se
    destruye nada.
    """
    if texto is None:
        return False
    palabras = set(_palabras_de(str(texto)))
    if FRASES_RECHAZAR & palabras:
        return False
    return bool(FRASES_ACEPTAR & palabras)


def _ejecutar_herramienta(bloque, hablar, escuchar):
    """tool_result de UN tool_use: ejecuta en el motor y devuelve su detalle

    Esta funcion es la UNICA que maneja la confirmacion destructiva del
    bucle (ver el punto 2 del bloque de arriba). El `confirmado` que pudiera
    traer el input del modelo se QUITA y se avisa en el tool_result: esa
    decision no es suya.
    """
    nombre = herramientas.accion_de_herramienta(
        getattr(bloque, "name", None))
    if nombre is None:
        return _resultado_de_tool_use(
            bloque,
            "herramienta desconocida: %r no existe; usa una de las "
            "herramientas disponibles" % (getattr(bloque, "name", None),),
            es_error=True)
    entrada = getattr(bloque, "input", None)
    nota_confirmado = ""
    if isinstance(entrada, dict) and "confirmado" in entrada:
        # LA REGLA DE SEGURIDAD: confirmado NO viene del modelo. Se quita del
        # input y se le dice al modelo que se ignoro; quien confirma es
        # Edward, con su voz, y solo a traves de _acepta_confirmacion.
        entrada = {clave: valor for clave, valor in entrada.items()
                   if clave != "confirmado"}
        nota_confirmado = (" (el parametro 'confirmado' que trajiste se "
                           "ignoro: la confirmacion la da el usuario, no "
                           "el modelo)")
    funcion = motor.REGISTRO[nombre]
    argumentos, error = _argumentos_de(funcion, entrada)
    if error is not None:
        return _resultado_de_tool_use(
            bloque, error + nota_confirmado, es_error=True)
    try:
        resultado = motor.ejecutar(Accion(nombre, tuple(argumentos)),
                                   confirmado=False)
    except Exception as error_ejecucion:
        # un fallo del motor (argumento raro, sistema que responde mal) no
        # puede tumbar el bucle: se devuelve como tool_result con error y
        # se imprime, para que el error no se trague en silencio
        print("aviso: la herramienta %s fallo: %s: %s"
              % (nombre, type(error_ejecucion).__name__, error_ejecucion))
        return _resultado_de_tool_use(
            bloque,
            "fallo al ejecutar %s: %s" % (nombre, error_ejecucion),
            es_error=True)
    if resultado.detalle == motor.REQUIERE_CONFIRMACION:
        # destructiva sin confirmar: el motor NO ejecuto NADA. Ahi el bucle
        # se DETIENE: EL PROGRAMA pregunta en voz, ESCUCHA la respuesta de
        # Edward (funcion escuchar, inyectable en las pruebas) y recien
        # entonces vuelve a llamar al motor. El modelo no opina.
        #
        # REGLA DE SEGURIDAD (FASE G4): si la escucha FALLA (None por
        # silencio o voz ilegible, sin microfono o sin internet), el
        # resultado es RECHAZAR, nunca aceptar: un silencio JAMAS vale
        # como "si". Los reintentos son LIMITADOS
        # (TOPE_REINTENTOS_CONFIRMACION) para no quedar preguntando
        # eternamente, y al agotarse se rechaza y se dice claramente.
        hablar(PREGUNTA_CONFIRMACION % nombre)
        respuesta = None
        for intento in range(TOPE_REINTENTOS_CONFIRMACION + 1):
            respuesta = escuchar()
            if respuesta is not None:
                break
            if intento < TOPE_REINTENTOS_CONFIRMACION:
                hablar(PREGUNTA_REINTENTO)
        if _acepta_confirmacion(respuesta):
            try:
                resultado = motor.ejecutar(Accion(nombre, tuple(argumentos)),
                                           confirmado=True)
            except Exception as error_confirmado:
                print("aviso: la herramienta %s fallo al confirmar: %s: %s"
                      % (nombre, type(error_confirmado).__name__,
                         error_confirmado))
                return _resultado_de_tool_use(
                    bloque,
                    "fallo al ejecutar %s: %s" % (nombre, error_confirmado),
                    es_error=True)
        else:
            if respuesta is None:
                # se agotaron los reintentos sin entender NADA: rechazo
                # EXPLICITO, hablado a Edward y devuelto al modelo
                hablar(AVISO_RECHAZO_ESCUCHA)
                detalle_rechazo = (
                    "no se pudo escuchar la respuesta del usuario tras %d "
                    "intentos; la accion NO se ejecuto. NO la repitas sin "
                    "pedir confirmacion de nuevo."
                    % (TOPE_REINTENTOS_CONFIRMACION + 1))
            else:
                # rechazo (o respuesta confusa, que tambien cuenta como
                # no): el modelo se entera de que Edward dijo que NO, y de
                # que no tiene que repetir la peticion
                detalle_rechazo = (
                    "el usuario rechazo la accion; NO la repitas. "
                    "No se ejecuto nada.")
            return _resultado_de_tool_use(
                bloque, detalle_rechazo + nota_confirmado, es_error=True)
    if nombre == "mirar_pantalla" and resultado.ok:
        # FASE H2: la captura ya viaja como BLOQUE DE IMAGEN, no como texto.
        # Solo por esta via (el modelo tuvo que pedir mirar_pantalla) y con
        # el PNG ya reducido por el motor. Si la captura fallo, no se entra
        # aqui: cae al tool_result de texto con el error y el bucle sigue.
        return _resultado_con_imagen(
            bloque, resultado.detalle, nota_confirmado)
    return _resultado_de_tool_use(
        bloque, resultado.detalle + nota_confirmado,
        es_error=not resultado.ok)


# ---------------------------------------------------------------------------
# FASE H1: MEMORIA DE CONVERSACION ENTRE TURNOS.
#
# Antes cada llamada a bucle_agente empezaba con UN SOLO mensaje ("hola") y
# el modelo no recordaba ni lo que habia dicho dos frases antes. Aqui vive el
# historial de la SESION: una lista de mensajes en el formato EXACTO de la API
# (role + content) que se copia al principio de cada turno y se completa con
# el turno nuevo.
#
# COSTE: cada mensaje guardado se reenvia en CADA llamada a la API (y dentro
# de un turno con herramientas, en cada iteracion), asi que los tokens de
# ENTRADA crecen con la charla: mas caro y mas lento. Por eso hay DOS topes
# fijos y se bota lo MAS VIEJO:
#   - TOPE_MENSAJES_HISTORIAL = 20: un turno normal son 2 mensajes (frase de
#     Edward + respuesta), asi que caben unas 10 intervenciones cortas. Con
#     herramientas un turno ocupa mas, y el tope se queda en menos charla:
#     es el precio de que la llamada no crezca sin fin.
#   - TOPE_CARACTERES_HISTORIAL = 12000: un solo tool_result gordo (listar
#     procesos, revisar disco) puede traer miles de caracteres con muy pocos
#     mensajes; 12.000 caracteres son ~3.000 tokens de entrada como MAXIMO
#     duro, aunque el tope de mensajes aun no se haya alcanzado.
# El turno MAS NUEVO se conserva siempre, aunque el solo pase los topes.
# ---------------------------------------------------------------------------

# historial de la sesion actual, en el formato de la API de Anthropic
HISTORIAL_SESION = []

# topes del historial (ver el bloque de arriba: costo y contexto)
TOPE_MENSAJES_HISTORIAL = 20
TOPE_CARACTERES_HISTORIAL = 12000


def limpiar_historial():
    """vacia la memoria de la conversacion (empieza una SESION nueva)

    Se llama al principio de procesar_orden, que es lo que corre justo
    despues de la palabra de activacion: asi una sesion no hereda la charla
    de la anterior. Se usa clear() sobre la MISMA lista, no una reasignacion,
    para no necesitar `global` y para que nadie se quede con una copia vieja.
    """
    HISTORIAL_SESION.clear()


def _tipo_de_bloque(bloque):
    """tipo de un bloque de contenido, venga como dict o como objeto"""
    if isinstance(bloque, dict):
        return bloque.get("type")
    return getattr(bloque, "type", None)


def _texto_de_bloque(bloque):
    """texto de un bloque de contenido, venga como dict o como objeto"""
    if isinstance(bloque, dict):
        return bloque.get("text")
    return getattr(bloque, "text", None)


def _ids_de_tool_use(bloques):
    """ids de los bloques tool_use de una lista, en orden"""
    return [str(getattr(bloque, "id", "")) for bloque in bloques
            if _tipo_de_bloque(bloque) == "tool_use"]


def _ids_de_tool_result(bloques):
    """ids de los bloques tool_result de una lista, en orden"""
    return [str(bloque.get("tool_use_id", "")) if isinstance(bloque, dict)
            else str(getattr(bloque, "tool_use_id", ""))
            for bloque in bloques
            if _tipo_de_bloque(bloque) == "tool_result"]


def _turno_valido(turno):
    """True si el turno se puede reenviar TAL CUAL a la API

    Anthropic exige que los roles alternen y que cada tool_use vaya seguido,
    en el mensaje SIGUIENTE, de SU tool_result (mismos ids). Un turno a
    medias en el historial hace que la llamada siguiente devuelva 400 y
    Jarvis se quede mudo. Por eso esta comprobacion corre ANTES de guardar:
    si el turno no esta completo, no entra.
    """
    if not turno:
        return False
    if turno[0].get("role") != "user":
        return False
    if not isinstance(turno[0].get("content"), str):
        return False
    if turno[-1].get("role") != "assistant":
        return False
    for posicion, mensaje in enumerate(turno):
        rol_esperado = "user" if posicion % 2 == 0 else "assistant"
        if mensaje.get("role") != rol_esperado:
            return False
        contenido = mensaje.get("content")
        if not contenido:
            return False
        if isinstance(contenido, str):
            # solo el PRIMER mensaje del turno puede ser texto plano: es la
            # frase de Edward. Los tool_result van SIEMPRE en una lista
            if posicion != 0:
                return False
            continue
        if not isinstance(contenido, list):
            return False
        if rol_esperado == "assistant":
            if any(_tipo_de_bloque(bloque) == "tool_result"
                   for bloque in contenido):
                return False
            ids_use = _ids_de_tool_use(contenido)
            if ids_use:
                # su respuesta tiene que ir en el mensaje SIGUIENTE
                if posicion + 1 >= len(turno):
                    return False
                siguiente = turno[posicion + 1].get("content")
                if not isinstance(siguiente, list):
                    return False
                if sorted(ids_use) != sorted(_ids_de_tool_result(siguiente)):
                    return False
        else:
            # un mensaje user con lista es SIEMPRE la respuesta de
            # herramientas del assistant ANTERIOR
            if posicion == 0:
                return False
            if any(_tipo_de_bloque(bloque) != "tool_result"
                   for bloque in contenido):
                return False
            anterior = turno[posicion - 1].get("content")
            if not isinstance(anterior, list):
                return False
            if sorted(_ids_de_tool_result(contenido)) != \
                    sorted(_ids_de_tool_use(anterior)):
                return False
    # la respuesta final tiene que ser TEXTO de verdad (no una lista vacia
    # ni un tool_use sin cerrar)
    cierre = turno[-1].get("content")
    if not isinstance(cierre, list):
        return False
    return any(_tipo_de_bloque(bloque) == "text"
               and bool(_texto_de_bloque(bloque))
               for bloque in cierre)


def _caracteres_historial():
    """estimacion de caracteres del historial (texto, ids y argumentos)"""
    return sum(len(str(mensaje.get("content", "")))
               for mensaje in HISTORIAL_SESION)


def _inicios_de_turno():
    """indices donde EMPIEZA cada turno: un user con texto plano"""
    return [posicion for posicion, mensaje in enumerate(HISTORIAL_SESION)
            if mensaje.get("role") == "user"
            and isinstance(mensaje.get("content"), str)]


def _recortar_historial():
    """bota los turnos MAS VIEJOS hasta respetar los dos topes

    Se corta por TURNOS COMPLETOS y nunca por mensajes sueltos: si se cortara
    a mitad de un turno, el primer mensaje que quedara podria ser un
    tool_result sin su tool_use, la API devolveria 400 y Jarvis se quedaria
    mudo. El turno MAS NUEVO se conserva SIEMPRE, aunque el solo pase el
    tope: perder lo que Edward acaba de decir seria peor que una llamada
    algo mas cara.
    """
    inicios = _inicios_de_turno()
    while len(inicios) > 1 and (
            len(HISTORIAL_SESION) > TOPE_MENSAJES_HISTORIAL
            or _caracteres_historial() > TOPE_CARACTERES_HISTORIAL):
        del HISTORIAL_SESION[:inicios[1]]
        inicios = _inicios_de_turno()


def _sin_imagenes(contenido):
    """contenido de un mensaje SIN bloques de imagen, para el HISTORIAL

    FASE H2: una captura de pantalla pesa cientos de KB y se reenviaria en
    CADA llamada del resto de la sesion. Ademas de costar tokens, eso
    romperia la decision de privacidad: la pantalla sale SOLO cuando el
    modelo pide mirarla, no en cada turno. Por eso al historial se guarda un
    texto que deja constancia de la captura, sin la imagen.

    Solo toca los tool_result cuyo content es una LISTA de bloques (el unico
    sitio donde viaja una imagen). Cualquier otro contenido pasa igual.
    """
    if not isinstance(contenido, list):
        return contenido
    limpio = []
    for bloque in contenido:
        if not isinstance(bloque, dict) \
                or _tipo_de_bloque(bloque) != "tool_result":
            limpio.append(bloque)
            continue
        interior = bloque.get("content")
        if not isinstance(interior, list) or not any(
                _tipo_de_bloque(parte) == "image" for parte in interior):
            limpio.append(bloque)
            continue
        textos = [str(_texto_de_bloque(parte) or "") for parte in interior
                  if _tipo_de_bloque(parte) == "text"]
        copia = dict(bloque)
        copia["content"] = [{
            "type": "text",
            "text": " ".join(texto for texto in textos if texto)
                    + " (la captura ya se mostro: no se reenvia, para no "
                      "mandar la pantalla de nuevo ni gastar tokens)",
        }]
        limpio.append(copia)
    return limpio


def _agregar_turno(turno):
    """guarda un turno COMPLETO (o avisa y no guarda nada) y aplica el tope

    FASE H2: antes de guardar se quitan las imagenes (_sin_imagenes). El
    historial es para el CONTEXTO de los turnos siguientes, y una captura de
    pantalla no se reenvia sola: si el modelo la necesita otra vez, vuelve a
    pedir mirar_pantalla.
    """
    limpio = [dict(mensaje, content=_sin_imagenes(mensaje.get("content")))
              for mensaje in turno]
    if not _turno_valido(limpio):
        print("aviso: no guarde el turno en el historial de la sesion: no "
              "tiene la estructura que exige la API (tool_use sin su "
              "tool_result o roles sin alternar)")
        return
    HISTORIAL_SESION.extend(limpio)
    _recortar_historial()


def bucle_agente(texto_usuario, memoria, hablar=hablar,
                 escuchar=escuchar_confirmacion):
    """el bucle agente (FASE G2): el modelo usa herramientas hasta responder

    Flujo: messages.create CON tools=herramientas.herramientas() ->
    si la respuesta trae tool_use, cada peticion se ejecuta en el MOTOR y su
    tool_result se manda de vuelta en otra llamada. Se repite hasta que el
    modelo devuelva TEXTO (end_turn), que es lo que se le dice a Edward, o
    hasta TOPE_BUCLE_AGENTE iteraciones (ver el por que arriba).

    LA CONFIRMACION destructiva NO se decide aqui ni en el modelo: la maneja
    _ejecutar_herramienta, con voz, directamente con Edward. Y la RESPUESTA
    se escucha por MICROFONO (FASE G4): si la escucha falla se RECHAZA,
    nunca se acepta por silencio, y los reintentos son limitados.

    `hablar` y `escuchar` son inyectables para las pruebas (por defecto, la
    voz de siempre y el MICROFONO de escuchar_confirmacion, NUNCA el
    teclado). `memoria` puede ser None, y se carga sola igual que en
    preguntar_a_ia.

    FASE H1: al principio se copia HISTORIAL_SESION (la memoria de los
    turnos anteriores) y se le agrega la frase de ahora; al final, el turno
    COMPLETO se guarda para el turno siguiente. El historial solo se toca
    aqui, y aqui solo se llega con clave: sin clave no se lee ni se manda, o
    sea que no cuesta ningun token.

    OJO: esta funcion ASUME que hay clave. Quien decide si se usa o se cae
    al camino de siempre (sin clave o con la API caida) es atender().
    """
    if memoria is None:
        memoria = cargar_memoria()
    system = _system_de(memoria)
    definiciones = herramientas.herramientas()
    # el turno arranca con el historial de la sesion y la frase de ahora
    mensajes = list(HISTORIAL_SESION)
    mensajes.append({"role": "user", "content": texto_usuario})
    # `turno` guarda el intercambio COMPLETO de ESTA frase; solo se pasa al
    # historial si llega al texto final (nunca a medias)
    turno = [mensajes[-1]]
    for _ in range(TOPE_BUCLE_AGENTE):
        respuesta = _obtener_cliente_ia().messages.create(
            model=MODELO_IA,
            # 1024 como en preguntar_a_ia: suficiente para una respuesta de
            # voz sin cortar (bug B11)
            max_tokens=1024,
            system=system,
            tools=definiciones,
            messages=mensajes,
        )
        peticiones = [bloque for bloque in respuesta.content
                      if getattr(bloque, "type", None) == "tool_use"]
        if not peticiones:
            # el modelo respondio TEXTO: es la respuesta final para Edward
            texto = _texto_de_respuesta(respuesta)
            if getattr(respuesta, "stop_reason", None) == "max_tokens":
                # igual que en preguntar_a_ia (bug B11): una respuesta
                # cortada no se lee en voz como si estuviera completa
                print("aviso: la respuesta de la IA se corto por el limite "
                      "de max_tokens; puede venir incompleta")
                texto += " Aviso: mi respuesta se cortó por límite de texto."
            # se guarda el turno COMPLETO: frase de Edward + intercambio de
            # herramientas + respuesta final. El cierre se normaliza a UN
            # bloque text con lo que se le dice a Edward, para que el ultimo
            # mensaje del historial siempre sea texto y la estructura siga
            # siendo valida para la API
            if texto:
                cierre = {"role": "assistant",
                          "content": [{"type": "text", "text": texto}]}
                turno.append(cierre)
                _agregar_turno(turno)
            return texto
        # el modelo pidio herramientas: su respuesta viaja COMO ESTA (formato
        # de la API) y los resultados vuelven en un mensaje user siguiente,
        # con un tool_result por cada tool_use
        mensaje_assistant = {"role": "assistant", "content": respuesta.content}
        mensajes.append(mensaje_assistant)
        turno.append(mensaje_assistant)
        mensaje_resultados = {
            "role": "user",
            "content": [_ejecutar_herramienta(bloque, hablar, escuchar)
                        for bloque in peticiones],
        }
        mensajes.append(mensaje_resultados)
        turno.append(mensaje_resultados)
        # FASE H3: si alguna herramienta de este lote CAMBIA la memoria
        # (recordar u olvidar), el system prompt se REHACE antes de la
        # llamada siguiente. Sin esto la nota queda en el archivo pero el
        # modelo sigue con la foto vieja hasta el proximo turno: guardar y
        # no saberlo es no haber aprendido nada. Se relee del archivo porque
        # es lo que quedo de verdad (si recordar se nego por un json roto,
        # el prompt no se inventa una nota).
        if any(herramientas.accion_de_herramienta(
                   getattr(peticion, "name", None)) in motor.CAMBIAN_MEMORIA
               for peticion in peticiones):
            memoria = cargar_memoria()
            system = _system_de(memoria)
    # tope alcanzado sin ninguna respuesta de texto: se dice la VERDAD, no
    # se inventa una respuesta
    texto_corte = ("Lleve %d pasos sin llegar a una respuesta final y corte "
                   "por seguridad. Reformule la pregunta, por favor."
                   % TOPE_BUCLE_AGENTE)
    # el turno queda cerrado con un texto del PROGRAMA (no del modelo), pero
    # es exactamente lo que Edward oyo: asi el historial no se queda con un
    # tool_use sin respuesta final
    turno.append({"role": "assistant",
                  "content": [{"type": "text", "text": texto_corte}]})
    _agregar_turno(turno)
    return texto_corte


def atender(texto_usuario, memoria=None, hablar=hablar,
            escuchar=escuchar_confirmacion):
    """entrada de una frase de Edward -> texto final (o camino de siempre)

    Si HAY CLAVE, usa bucle_agente (el bucle con herramientas). Si NO la
    hay (ausente, None o vacia), o si la API revienta de cualquier forma
    (401, sin red, rate limit), el bucle NO se usa: se cae al camino de HOY,
    interpretar() por palabras clave con las 41 acciones del REGISTRO, sin
    llamar a la IA en ningun punto. Asi el modo offline no depende de red
    ni de clave, que es lo que exige la FASE G2.

    El try/except esta SOLO alrededor de la llamada al bucle, y el error se
    IMPRIME con su tipo y su mensaje: un except que se lo traga en silencio
    esconderia hasta un NameError (fundamento 42), y aqui se quiere caer al
    camino viejo a la vista de todos, no en silencio.

    FASE G4: `escuchar` por defecto es el MICROFONO
    (escuchar_confirmacion), no el teclado. No es un detalle: procesar_orden
    llama a atender SIN escuchar y este le pasa el suyo a bucle_agente, asi
    que si el default fuera input la confirmacion destructiva se teclearia
    aunque bucle_agente tuviera el microfono por defecto.
    """
    if _tiene_clave_ia():
        try:
            return bucle_agente(texto_usuario, memoria,
                                hablar=hablar, escuchar=escuchar)
        except Exception as error:
            print("aviso: el bucle de la IA fallo (%s: %s); sigo con el "
                  "reconocedor de siempre"
                  % (type(error).__name__, error))
    # camino de HOY: interpretar por palabras clave. consultar_ia se salta
    # con aviso: sin clave (o con la API caida) no hay a quien consultar, y
    # el modo offline NO puede depender de la IA
    for accion in interpretar(texto_usuario):
        if accion.nombre == "consultar_ia":
            print("aviso: la IA no esta disponible; diga un comando "
                  "conocido (hora, volumen, abrir, etc.)")
            continue
        ejecutar_accion(accion)
    # quien habla aqui es ejecutar_accion (por el motor o por la cadena
    # if/elif de siempre): no hay texto final que devolver
    return ""


# definimos la funcion procesar_orden que graba el audio del usuario, lo
# convierte a texto y ejecuta acciones según el contenido del texto.
#
# B2: antes esta funcion terminaba LLAMANDOSE A SI MISMA cuando hubo voz en la
# espera. Cada vuelta sumaba un frame a la pila de Python (tope 1000), asi que
# una charla larga acababa en RecursionError, y PEOR: cada frame retenia SU
# audio, de modo que 1200 vueltas eran cientos de MB acumulados en RAM. Ahora
# es un bucle while True con rompe explicito: la profundidad de la pila es
# SIEMPRE 1 y el audio de una vuelta se suelta al empezar la siguiente.
# El comportamiento visible NO cambia: pregunta, espera 6 segundos, si hubo voz
# repite, si no se duerme.
def procesar_orden():
    # FASE H1: cada llamada a procesar_orden es una SESION nueva, porque
    # corre justo despues de la palabra de activacion. Limpiar aqui (y no al
    # dormir) garantiza que al volver a despertar Jarvis no herede la charla
    # anterior; el historial de una sesion no se mezcla con el de otra.
    limpiar_historial()
    while True:
        print("Escuchando...")
        audio = grabar_hasta_silencio(frecuencia)
        # la MISMA variable para escribir y para leer, en el temp del sistema
        # (antes: "grabacion.wav" relativa al directorio de trabajo, siempre)
        ruta_wav = _ruta_wav_unico("grabacion")
        try:
            with wave.open(ruta_wav, "wb") as archivo:
                archivo.setnchannels(1)
                archivo.setsampwidth(2)
                archivo.setframerate(frecuencia)
                archivo.writeframes(audio.tobytes())

            with sr.AudioFile(ruta_wav) as source:
                datos_audio = r.record(source)
                texto = r.recognize_google(datos_audio, language="es-ES")
                print("Texto reconocido: " + texto)
        except sr.RequestError as error:
            # SIN INTERNET: recognize_google (Google) y edge_tts (voz) no
            # pueden funcionar. Antes esto se propagaba y mataba a Jarvis en
            # la primera frase, sin poder ni reconocer ni hablar. Ahora se
            # avisa con HONESTIDAD y se vuelve a ESCUCHAR (sigue el bucle):
            # no hay texto que procesar esta vuelta, y grabar_hasta_silencio
            # bloquea en el microfono, asi que NO hay bucle que se coma la
            # CPU. Un Ctrl+C sigue saliendo (KeyboardInterrupt no es
            # RequestError, asi que no se traga). El `finally` de abajo se
            # ejecuta igual: el wav se borra antes de volver a escuchar.
            print("aviso: sin internet no puedo reconocer ni hablar (%s); "
                  "reintenta" % type(error).__name__)
            continue
        finally:
            # el wav se borra SIEMPRE apenas se lee, tambien si el
            # reconocimiento revienta (sin internet, voz ilegible)
            _borrar_wav(ruta_wav)

        texto = texto.lower()

        # FASE E2a: si hay una accion esperando confirmacion, la frase de
        # AHORA es la respuesta ("si"/"no") y no se procesa como orden
        # normal. Si no hay pendiente, o la frase es una orden normal,
        # resolver_confirmacion devuelve False (y en ese caso ya borro el
        # pendiente) y la frase se manda a atender (FASE G3): con clave usa
        # el bucle agente, sin clave (o con la API caida) cae a interpretar
        # con las 41 acciones de siempre, igual que antes. Una sola frase
        # dispara UNA sola accion (B12).
        if not resolver_confirmacion(texto):
            respuesta = atender(texto)
            if respuesta:
                # con clave, atender devuelve el texto final del modelo y
                # todavia nadie lo dijo: SE LEE EN VOZ a Edward, que es
                # justamente el punto de conectar la voz al bucle. Sin
                # clave atender devuelve "" (quien habla es
                # ejecutar_accion) y aqui no se dice nada de mas, igual
                # que siempre
                hablar(respuesta)

        print("Pensando...")
        hablar("¿Desea que haga algo más por usted?")
        tiempo_inicio_espera = time.time()
        hubo_voz = False
        while time.time() - tiempo_inicio_espera < 6:
            bloque_espera = sd.rec(int(0.5 * frecuencia), samplerate=frecuencia, channels=1, dtype='int16')
            sd.wait()
            volumen_espera = np.abs(bloque_espera).mean()
            if volumen_espera > 1300:  # umbral de voz detectada
                hubo_voz = True
                break

        if hubo_voz:
            # hubo voz: se vuelve a escuchar, SIN apilar frames
            continue
        print("Jarvis se a dormido por favor despiertelo.")
        # una accion pendiente de confirmacion NO sobrevive al sueño: si
        # Edward no respondio a tiempo, es mas seguro olvidarla que
        # preguntarle algo minutos despues al despertar
        olvidar_pendiente()
        break

# definimos la funcion bucle_principal: espera la palabra de activacion y
# luego procesa la orden del usuario. Es el bucle de ARRANQUE, por eso vive en
# una funcion: importar el archivo no tiene que quedar escuchando para siempre.
def bucle_principal():
    print("Esperando la palabra de activación...")
    while True:
        audio_corto = sd.rec(int(3 * frecuencia), samplerate=frecuencia, channels=1, dtype='int16')
        sd.wait()

        # la MISMA variable para escribir y para leer, en el temp del sistema
        # (antes: "escucha_activacion.wav" relativa al directorio de trabajo)
        ruta_wav = _ruta_wav_unico("escucha_activacion")
        try:
            with wave.open(ruta_wav, "wb") as archivo:
                archivo.setnchannels(1)
                archivo.setsampwidth(2)
                archivo.setframerate(frecuencia)
                archivo.writeframes(audio_corto.tobytes())

            try:
                with sr.AudioFile(ruta_wav) as source:
                    datos = r.record(source)
                    texto_corto = r.recognize_google(datos, language="es-ES").lower()
                    print("reconociendo activacion:" + texto_corto)
            finally:
                # el wav se borra SIEMPRE apenas se lee y ANTES de las ramas
                # de activacion: procesar_orden puede correr minutos o no
                # terminar nunca, y no puede esperar basura en el temp
                _borrar_wav(ruta_wav)

            if any(frase in texto_corto for frase in FRASE_ACTIVACION_INICIAL):
                # la hora se calcula AQUI, al decirla, y no como una
                # constante de modulo: si no, mentiria si Jarvis lleva
                # horas corriendo
                ahora = datetime.datetime.now()
                hablar("buenos dias señor. como se encuentra en el dia de hoy? son las " + ahora.strftime("%H:%M") + " de la " + momento_del_dia(ahora))
                hablar( "enque trabajaremos en el dia de hoy señor?")
                procesar_orden()

            elif any(frase in texto_corto for frase in FRASE_ACTIVACION_NORMAL):
                print("reconociendo activacion:" + texto_corto)
                hablar("A la orden, señor")

                procesar_orden()


        # solo los errores de RECONOCIMIENTO: sin internet (RequestError) o
        # sin voz entendible (UnknownValueError). Un Ctrl+C (KeyboardInterrupt)
        # NO entra aqui: tiene que salir afuera y parar el programa, y un
        # fallo de verdad (de la IA, del navegador, del audio) tambien, en vez
        # de quedarse tragado en silencio.
        except (sr.UnknownValueError, sr.RequestError):
            # no reconocio la palabra de activacion: se vuelve a escuchar
            print("No reconoci la activacion, sigo escuchando...")


# CODIGO DE ARRANQUE: solo corre si el archivo se ejecuta como programa
# (python main/main.py). Al importarlo (por ejemplo desde las pruebas) NO se
# carga la memoria, NO se pide el microfono, NO se habla y NO se entra en el
# bucle principal. Las funciones de arriba siguen visibles al importar.
if __name__ == "__main__":
    memoria = cargar_memoria()  # cargamos la memoria del asistente desde el archivo JSON

    if memoria["nombre_usuario"] is None:
        pedir_nombre(memoria)

    bucle_principal()

