# motor de ejecucion del asistente Jarvis (FASE E1).
#
# Esqueleto y seguridad, NADA mas. El reconocedor traduce TEXTO en ACCIONES
# sin ejecutar nada, y AQUI se decide si una accion se puede ejecutar.
#
# Tres piezas:
#   - Resultado: lo unico que el motor devuelve. NUNCA habla ni imprime (el
#     que habla es main.py), por eso el detalle viaja como texto.
#   - REGISTRO: nombre de la accion -> funcion que la ejecuta.
#   - DESTRUCTIVAS: acciones que destruyen datos o apagan la maquina, y por
#     eso exigen confirmacion.
#
# LA REGLA DE SEGURIDAD: una accion destructiva sin confirmar NO ejecuta NI
# UNA linea, ni un subprocess, ni un os., ni un ctypes: se devuelve
# REQUIERE_CONFIRMACION y ya. La comprobacion va ANTES de buscar la funcion
# en el registro, para que ni siquiera un nombre desconocido pueda colarse.
#
# FASE E1: volumen, hora y papelera. FASE E2b: archivos y programas (abrir
# archivo, abrir app, abrir url, cerrar app y buscar archivo). FASE E2c,
# GRUPO 2: el equipo (volumen subir/bajar/silenciar, brillo, apagar,
# reiniciar, cancelar apagado, bloquear, suspender y captura de pantalla).
# FASE E2d, GRUPOS 3 y 4: escribir y teclar (portapapeles + Ctrl+V,
# SendInput y raton) e informacion del sistema (disco, CPU, RAM, bateria,
# procesos y peso de carpetas).
# FASE E2e, GRUPO 5: red y descargas (wifi, bluetooth, descargar, leer json
# y busqueda web) mas papelera y borrado (papelera_ver y borrar_archivo).
# El motor NO importa audio ni IA: se puede importar en milisegundos, sin
# microfono, sin red y sin clave de API. Lo UNICO que habla con la red es
# urllib de la stdlib, y solo cuando una accion de download lo pide.

import ctypes
import csv
import dataclasses
import datetime
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
import winreg
import zlib

# pywin32: YA instalada en esta PC (medido el 2026-10-21: pywin32==312) y
# declarada en requirements.txt. Es la UNICA dependencia externa del motor y
# NO es de audio, de IA ni de red: el motor sigue importandose sin
# microfono, sin internet y sin clave de API. Su lista blanca explicita esta
# en EXTERNOS_PERMITIDOS de tests/test_motor.py. win32gui cierra ventanas
# (WM_CLOSE) y win32api mueve y pulsa el raton; las DOS son de pywin32.
import win32api
import win32gui

from main.reconocedor import Accion


# detalle FIJO que devuelve una accion destructiva sin confirmar: una
# palabra entera, para que el llamador pueda compararla sin adivinar
REQUIERE_CONFIRMACION = "REQUIERE_CONFIRMACION"

# detalle de una accion que nadie registro en REGISTRO
ACCION_DESCONOCIDA = "ACCION_DESCONOCIDA"


@dataclasses.dataclass(frozen=True)
class Resultado:
    ok: bool
    detalle: str


# ---------------------------------------------------------------------------
# volumen del sistema por COM: ctypes puro, sin librerias instaladas.
# Cadena completa verificada el 2026-10-20 en esta PC:
#   CoCreateInstance(MMDeviceEnumerator) -> GetDefaultAudioEndpoint ->
#   Activate(IAudioEndpointVolume) -> GetMasterVolumeLevelScalar / GetMute.
# ---------------------------------------------------------------------------

# identidades COM fijas de Windows: estan en los headers del sistema
# operativo y son las mismas en cualquier maquina
_CLSID_MMDEVICEENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDEVICEENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAUDIOENDPOINTVOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"
_CLSCTX_ALL = 0x17  # CLSCTX_INPROC_SERVER | INPROC_HANDLER | LOCAL | REMOTE

# CoInitializeEx devuelve esto si COM ya esta inicializada en OTRO modo; en
# ese caso COM igual funciona y NO hay que des-inicializar (no se sumo
# referencia). Medido: sin CoInitializeEx TODO devuelve CO_E_NOTINITIALIZED
# (0x800401F0), aunque la clase este registrada.
_RPC_E_CHANGED_MODE = -2147417846

_HRESULT = ctypes.c_long  # HRESULT = LONG de 32 bits (Windows es LLP64)


class _GUID(ctypes.Structure):
    """estructura GUID de Windows: 16 bytes en little endian"""
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


def _guid(texto):
    """estructura GUID a partir de su texto {12345678-abcd-...}"""
    estructura = _GUID()
    ctypes.memmove(ctypes.byref(estructura), uuid.UUID(texto).bytes_le, 16)
    return estructura


def _metodo(puntero, indice, restype, *argtypes):
    """funcion de la vtable COM en `indice`, con el `this` ya inyectado

    Un puntero COM no es una direccion cualquiera: su PRIMER campo apunta a
    la vtable, un arreglo de punteros a funcion. Las llamadas van en stdcall
    (WINFUNCTYPE) y el PRIMER argumento de todo metodo COM es el propio
    objeto (this); el lambda de abajo lo agrega solo para que en los call
    sites se lea como un metodo normal.
    """
    tabla = ctypes.cast(
        puntero, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    tipo = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    funcion = ctypes.cast(tabla[indice], tipo)
    return lambda *argumentos: funcion(puntero, *argumentos)


def _soltar(puntero):
    """suelta la referencia COM del puntero (vtable[2] = Release)"""
    if puntero is None or not puntero.value:
        return
    _metodo(puntero, 2, ctypes.c_ulong)()


def _fallo(paso, codigo):
    """resultado fallido con el HRESULT en hexadecimal legible"""
    return Resultado(
        ok=False,
        detalle="%s fallo (0x%08X)" % (paso, codigo & 0xFFFFFFFF))


def _interfaz_de_volumen():
    """cadena COM hasta IAudioEndpointVolume, o su fallo. Devuelve
    (fallo, ole32, inicializado, interfaz)

    `fallo` es un Resultado ok=False si algo fallo a medio camino (con todo
    lo ya abierto suelto y COM des-inicializado); es None si todo salio bien,
    y entonces el llamador RECIBE la interfaz y tiene que liberarla EL:
    _soltar(interfaz) y CoUninitialize() SOLO si inicializado=True. El
    enumerador y el dispositivo se sueltan aqui solos, apenas la interfaz
    queda activada: Activate le suma a la interfaz su propia referencia COM,
    independiente de las otras dos.
    """
    ole32 = ctypes.windll.ole32
    # COM exige CoInitialize en el hilo ANTES de cualquier CoCreateInstance.
    # 0 = COINIT_MULTITHREADED. Con 0 (S_OK) o 1 (S_FALSE) si toca
    # des-inicializar al final; con _RPC_E_CHANGED_MODE no, porque no se
    # sumo referencia.
    codigo = ole32.CoInitializeEx(None, 0)
    inicializado = codigo in (0, 1)
    if codigo not in (0, 1, _RPC_E_CHANGED_MODE):
        return (_fallo("CoInitializeEx", codigo), ole32, False, None)

    enumerador = ctypes.c_void_p()
    dispositivo = ctypes.c_void_p()
    interfaz = ctypes.c_void_p()
    fallo = None
    cocrear = ole32.CoCreateInstance
    cocrear.argtypes = (ctypes.POINTER(_GUID), ctypes.c_void_p,
                        ctypes.c_ulong, ctypes.POINTER(_GUID),
                        ctypes.POINTER(ctypes.c_void_p))
    cocrear.restype = _HRESULT
    codigo = cocrear(_guid(_CLSID_MMDEVICEENUMERATOR), None, _CLSCTX_ALL,
                     _guid(_IID_IMMDEVICEENUMERATOR),
                     ctypes.byref(enumerador))
    if codigo != 0:
        fallo = _fallo("CoCreateInstance", codigo)
    else:
        # vtable IMMDeviceEnumerator: 0-2 = IUnknown,
        # 3 = EnumAudioEndpoints, 4 = GetDefaultAudioEndpoint
        # (eFlow=0 = eRender, eRole=0 = eConsole)
        obtener = _metodo(enumerador, 4, _HRESULT, ctypes.c_uint,
                          ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
        codigo = obtener(0, 0, ctypes.byref(dispositivo))
        if codigo != 0:
            fallo = _fallo("GetDefaultAudioEndpoint", codigo)
        else:
            # vtable IMMDevice: 0-2 = IUnknown, 3 = Activate
            activar = _metodo(dispositivo, 3, _HRESULT, ctypes.c_void_p,
                              ctypes.c_ulong, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_void_p))
            codigo = activar(ctypes.byref(_guid(_IID_IAUDIOENDPOINTVOLUME)),
                             _CLSCTX_ALL, None, ctypes.byref(interfaz))
            if codigo != 0:
                fallo = _fallo("Activate", codigo)

    _soltar(dispositivo)
    _soltar(enumerador)
    if fallo is not None:
        if inicializado:
            ole32.CoUninitialize()
        return (fallo, ole32, False, None)
    return (None, ole32, inicializado, interfaz)


def _nivel_maestro(interfaz):
    """nivel maestro de volumen (0.0 a 1.0) de una interfaz ya abierta

    vtable IAudioEndpointVolume: 9 = GetMasterVolumeLevelScalar. El indice
    se verifico el 2026-10-20 contra dos lecturas independientes:
    GetVolumeStepInfo (indice 16) marco 22/51 pasos (coincide con el escalar
    0.43) y GetVolumeRange devolvio [-65.25, 0] dB, que contiene el dB
    leido. El dB NO cumple 20*log10(scalar) porque cada endpoint tiene SU
    curva: por eso la unica comprobacion es de RANGO y no de logaritmo.
    Devuelve (fallo, nivel): `fallo` es un Resultado ok=False si la llamada
    o la SANIDAD fallan (un nivel fuera de [0.0, 1.0] no es un dato
    utilizable, y devolverlo como si lo fuera seria mentirle a Edward con un
    numero inventado); es None si todo salio bien.
    """
    nivel = ctypes.c_float()
    leer_nivel = _metodo(interfaz, 9, _HRESULT, ctypes.POINTER(ctypes.c_float))
    codigo = leer_nivel(ctypes.byref(nivel))
    if codigo != 0:
        return (_fallo("GetMasterVolumeLevelScalar", codigo), None)
    if not 0.0 <= nivel.value <= 1.0:
        return (Resultado(
            ok=False,
            detalle="nivel de volumen fuera de rango: %r"
            % (nivel.value,)), None)
    return (None, nivel.value)


def volumen_leer():
    """nivel maestro de volumen y silencio del sistema. SOLO LEE.

    Inocua por construccion: de toda la cadena COM solo llama metodos de
    LECTURA (GetMasterVolumeLevelScalar y GetMute); no cambia ni escribe
    nada. Devuelve el dato como texto: quien lo dice en voz es main.py.
    """
    fallo, ole32, inicializado, interfaz = _interfaz_de_volumen()
    if fallo is not None:
        return fallo
    try:
        fallo_nivel, nivel = _nivel_maestro(interfaz)
        if fallo_nivel is not None:
            return fallo_nivel

        # vtable IAudioEndpointVolume: 15 = GetMute
        silencio = ctypes.c_int()
        leer_silencio = _metodo(interfaz, 15, _HRESULT, ctypes.POINTER(ctypes.c_int))
        codigo = leer_silencio(ctypes.byref(silencio))
        if codigo != 0:
            return _fallo("GetMute", codigo)
        if silencio.value not in (0, 1):
            return Resultado(
                ok=False,
                detalle="lectura de silencio fuera de rango: %r"
                % (silencio.value,))

        return Resultado(
            ok=True,
            detalle="volumen %d%%, %s" % (
                round(nivel * 100),
                "silenciado" if silencio.value else "sin silenciar"))
    finally:
        # se suelta la referencia de la interfaz y se des-inicializa COM
        # solo si esta cadena fue la que la inicializo
        _soltar(interfaz)
        if inicializado:
            ole32.CoUninitialize()


def _volumen_paso(subir):
    """mueve el volumen maestro UN paso y devuelve su nuevo nivel. Inocua.

    vtable IAudioEndpointVolume: 17 = VolumeStepUp, 18 = VolumeStepDown:
    son los MISMOS pasos del teclado de Windows (GetVolumeStepInfo, indice
    16, los cuenta; el escalar se relee con 9 para anunciar el resultado).
    El nivel no se puede salir de [0.0, 1.0] porque el dispositivo lo
    acota, pero aun asi el resultado se SANITA con _nivel_maestro antes de
    anunciarlo.
    """
    fallo, ole32, inicializado, interfaz = _interfaz_de_volumen()
    if fallo is not None:
        return fallo
    try:
        if subir:
            paso = _metodo(interfaz, 17, _HRESULT)
            nombre, mensaje = "VolumeStepUp", "volumen subido"
        else:
            paso = _metodo(interfaz, 18, _HRESULT)
            nombre, mensaje = "VolumeStepDown", "volumen bajado"
        codigo = paso()
        if codigo != 0:
            return _fallo(nombre, codigo)
        fallo_nivel, nivel = _nivel_maestro(interfaz)
        if fallo_nivel is not None:
            return fallo_nivel
        return Resultado(
            ok=True,
            detalle="%s: volumen %d%%" % (mensaje, round(nivel * 100)))
    finally:
        _soltar(interfaz)
        if inicializado:
            ole32.CoUninitialize()


def volumen_subir():
    """sube el volumen maestro UN paso. Inocua: solo mueve el nivel"""
    return _volumen_paso(True)


def volumen_bajar():
    """baja el volumen maestro UN paso. Inocua: solo mueve el nivel"""
    return _volumen_paso(False)


def silenciar():
    """pone el volumen maestro en silencio. Inocua.

    vtable IAudioEndpointVolume: 14 = SetMute(bMute, pguidEventContext),
    con el silencio puesto en 1. No destruye nada y es reversible.
    """
    fallo, ole32, inicializado, interfaz = _interfaz_de_volumen()
    if fallo is not None:
        return fallo
    try:
        poner_silencio = _metodo(interfaz, 14, _HRESULT, ctypes.c_int,
                                 ctypes.c_void_p)
        codigo = poner_silencio(1, None)
        if codigo != 0:
            return _fallo("SetMute", codigo)
        return Resultado(ok=True, detalle="volumen silenciado")
    finally:
        _soltar(interfaz)
        if inicializado:
            ole32.CoUninitialize()


def hora_actual():
    """hora actual del sistema en formato HH:MM. Inocua: solo lee el reloj"""
    ahora = datetime.datetime.now()
    return Resultado(ok=True, detalle=ahora.strftime("%H:%M"))


def papelera_vaciar():
    """vacia la papelera de reciclaje de Windows. DESTRUCTIVA: borra datos.

    SOLO se llega aqui desde ejecutar() con confirmado=True: el freno esta
    ANTES de esta linea, en ejecutar(). Banderas 0x7 de SHEmptyRecycleBinW
    = SHERB_NOCONFIRMATION (0x1) | SHERB_NOPROGRESSUI (0x2) |
    SHERB_NOSOUND (0x4): la confirmacion la da Edward por voz, no un cuadro
    del sistema.
    """
    funcion = ctypes.windll.shell32.SHEmptyRecycleBinW
    # argtypes para que Windows reciba bien los punteros en 64 bits: sin
    # ellos ctypes asume entero de 32 bits y el puntero se trunca
    funcion.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong)
    funcion.restype = _HRESULT
    codigo = funcion(None, None, 0x7)
    if codigo != 0:
        return _fallo("SHEmptyRecycleBinW", codigo)
    return Resultado(ok=True, detalle="papelera de reciclaje vaciada")


# ---------------------------------------------------------------------------
# FASE E2b: archivos y programas. Cinco acciones nuevas, mas una sexta
# (cerrar_app_forzado) que es la MITAD destructiva de cerrar app.
# ---------------------------------------------------------------------------

# WM_CLOSE: mensaje con el que se PIDE a una ventana que se cierre sola. Esta
# en los headers de Windows (winuser.h) y es el mismo en cualquier maquina
_WM_CLOSE = 0x0010

# como dice el usuario la ventana, y que aparece (o no) en su titulo. Medido
# el 2026-10-21: el perfil de esta PC esta en INGLES (Desktop/Documents/
# Downloads, los nombres en espanol NO existen), asi que los titulos de las
# aplicaciones pueden salir en ingles aunque Edward hable espanol: por eso
# cada alias prueba LAS DOS formas.
_ALIAS_VENTANA = {
    "navegador": ("chrome", "firefox", "edge"),
    "bloc de notas": ("bloc de notas", "notepad"),
    "calculadora": ("calculadora", "calculator"),
}

# raices de BUSQUEDA por defecto, medidas en esta PC el 2026-10-21: son los
# nombres FISICOS de las carpetas del perfil. NUNCA se arranca en la raiz del
# disco: C:\ entero tarda ~59 s en recorrerse (y ~2 min si ademas se miden
# tamaños), y por eso la busqueda viene ACOTADA a estas tres carpetas salvo
# que el usuario pida otra raiz a mano.
_RAICES_BUSQUEDA = {
    "escritorio": "Desktop",
    "documentos": "Documents",
    "descargas": "Downloads",
}

# cuantos archivos devuelve como maximo una busqueda: cientos de rutas no son
# algo que se pueda leer ni escuchar
_TOPE_RESULTADOS = 20

# apps de la tienda (UWP), que NO estan en App Paths y se activan con SU URI
# de protocolo. Medido el 2026-10-21 en esta PC: "calculator:" SI se puede
# activar y "calc:" NO. Solo se declara lo medido: no se inventan apps.
_APPS_UWP = {
    "calculadora": "calculator:",
    "calculator": "calculator:",
}

# registro HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths. Medido
# el 2026-10-21: ahi estan chrome.exe y Code.exe (las dos instalaciones de
# USUARIO); notepad.exe, explorer.exe y calc.exe NO estan.
_CLAVE_APP_PATHS = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths"

# taskkill fallo (nombre que no existe, sin permisos, ...). Se guarda la clase
# AQUI y no como subprocess.CalledProcessError: las pruebas reemplazan el
# subprocess del modulo por un doble y una referencia tardia a la clase
# llegaria como un MagicMock, que Python no acepta como destino de un except.
_ERROR_SUBPROCESO = subprocess.CalledProcessError


def _ruta_de_raiz(raiz):
    """ruta absoluta de una raiz de busqueda, o None si no se resuelve

    Acepta el ALIAS de _RAICES_BUSQUEDA ("documentos"), el nombre fisico de
    la carpeta ("Documents") o una ruta entera. El None es lo que el llamador
    necesita para contestar ok=False sin que nadie revente.
    """
    if not isinstance(raiz, str) or not raiz.strip():
        return None
    texto = raiz.strip()
    carpeta = _RAICES_BUSQUEDA.get(texto.lower())
    if carpeta is None:
        return texto
    return os.path.join(os.path.expanduser("~"), carpeta)


def _ignorar_error_de_walk(_error):
    """onerror de os.walk: una carpeta que no se puede abrir se SALTA

    Sin esto, la primera carpeta sin permisos (C:\\System Volume
    Information, por ejemplo) reventaria la busqueda entera con
    PermissionError.
    """
    return None


def abrir_archivo(ruta):
    """abre un archivo o una carpeta con el programa que Windows asocie. Inocua.

    Solo pide al sistema abrir algo que YA existe: no borra, no modifica y no
    instala. Se admite el alias de _RAICES_BUSQUEDA ("escritorio") ademas de
    una ruta entera. Lo que no existe devuelve ok=False en vez de sacar un
    FileNotFoundError al llamador: el motor NUNCA lanza excepciones.
    """
    destino = _ruta_de_raiz(ruta)
    if destino is None:
        return Resultado(ok=False, detalle="falta la ruta del archivo")
    if not os.path.exists(destino):
        return Resultado(ok=False, detalle="no existe: " + destino)
    try:
        os.startfile(destino)
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude abrir %s: %s" % (destino, error))
    return Resultado(ok=True, detalle="abierto: " + destino)


def _ruta_app_paths(imagen):
    """ruta absoluta registrada en HKCU\\App Paths\\`imagen`, o None si no esta"""
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _CLAVE_APP_PATHS + "\\" + imagen) as clave:
            valor, _tipo = winreg.QueryValueEx(clave, "")
    except OSError:
        return None
    if not isinstance(valor, str) or not valor.strip():
        return None
    # la ruta puede venir entre comillas o con variables como %USERPROFILE%
    return os.path.expandvars(valor.strip().strip('"'))


def abrir_app(nombre):
    """abre una aplicacion de Windows por su nombre. Inocua.

    Cadena de busqueda, MEDIDA en esta PC el 2026-10-21:
      1. app de la tienda (UWP) -> su URI de protocolo;
      2. HKCU\\App Paths\\<nombre>.exe -> la ruta absoluta. Ahi estan chrome y
         VS Code, que NO estan en el PATH (medido);
      3. PATH del sistema (shutil.which): ahi SI estan notepad, explorer y
         calc (medido: en App Paths NO estan);
      4. nada -> ok=False y NO se abre absolutamente nada.
    Las .exe van por subprocess.Popen y no por os.startfile porque startfile
    no devuelve el PID y un dia hay que poder cerrar la aplicacion; las UWP
    se activan con su URI, que solo ShellExecute sabe leer.
    """
    if not isinstance(nombre, str) or not nombre.strip():
        return Resultado(ok=False, detalle="falta el nombre de la aplicacion")
    clave = nombre.strip().lower()

    uri = _APPS_UWP.get(clave)
    if uri is not None:
        try:
            os.startfile(uri)
        except OSError as error:
            return Resultado(
                ok=False, detalle="no pude abrir %s: %s" % (clave, error))
        return Resultado(ok=True, detalle="abriendo " + clave)

    imagen = clave if clave.endswith(".exe") else clave + ".exe"
    ruta = _ruta_app_paths(imagen)
    if ruta is None:
        ruta = shutil.which(imagen)
    if ruta is None:
        return Resultado(
            ok=False,
            detalle="no encontre la aplicacion %s: ni en App Paths ni en el PATH"
            % (clave,))
    try:
        # lista y no cadena: con espacios en la ruta ("C:\Program Files\...")
        # la cadena suelta depende de como CreateProcess parte los argumentos
        subprocess.Popen([ruta])
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude abrir %s: %s" % (clave, error))
    return Resultado(ok=True, detalle="abriendo " + clave)


def abrir_url(url):
    """abre una url en el navegador por defecto. Inocua.

    Solo pide al sistema abrir un sitio: no descarga ni instala nada.
    """
    if not isinstance(url, str) or not url.strip():
        return Resultado(ok=False, detalle="falta la url")
    destino = url.strip()
    # "youtube.com" sin esquema NO es una url para webbrowser: lo trataria como
    # un archivo y no abriria nada. Se completa https://
    if "://" not in destino:
        destino = "https://" + destino
    if not webbrowser.open(destino):
        return Resultado(
            ok=False, detalle="no hay navegador para abrir " + destino)
    return Resultado(ok=True, detalle="abriendo " + destino)


def _ventanas_de(nombre):
    """(hwnd, titulo) de las ventanas cuyo titulo contiene `nombre`

    Todo en minusculas, sin distinguir mayusculas. Si `nombre` es un alias de
    _ALIAS_VENTANA se prueban TODAS sus formas. EnumWindows recorre la pila
    de ventanas de MAS ARRIBA hacia abajo, asi que la PRIMERA coincidencia es
    la ventana que el usuario ve delante.
    """
    limpio = nombre.strip().lower()
    patrones = (limpio,) + _ALIAS_VENTANA.get(limpio, ())
    encontradas = []

    def _visita(hwnd, _parametro):
        if not win32gui.IsWindowVisible(hwnd):
            return
        titulo = win32gui.GetWindowText(hwnd)
        if not titulo:
            return
        bajo = titulo.lower()
        if any(patron in bajo for patron in patrones):
            encontradas.append((hwnd, titulo))

    win32gui.EnumWindows(_visita, None)
    return encontradas


def cerrar_app(nombre):
    """cierra la ventana mas arriba cuyo titulo contiene `nombre`. Inocua.

    Solo manda WM_CLOSE, que es un PEDIDO: la aplicacion se cierra sola y
    puede negarse o preguntar antes ("quieres guardar?"), asi que NADA se
    pierde sin que nadie lo acepte. Eso es lo que la hace inocua. Lo que si
    destruye datos es matar el proceso con /F, y eso es OTRA accion,
    cerrar_app_forzado, que esta en DESTRUCTIVAS.
    Sin coincidencia: ok=False y no se manda ningun mensaje.
    """
    if not isinstance(nombre, str) or not nombre.strip():
        return Resultado(ok=False, detalle="falta el nombre de la aplicacion")
    encontradas = _ventanas_de(nombre)
    if not encontradas:
        return Resultado(
            ok=False, detalle="no encontre ninguna ventana con " + nombre)
    hwnd, titulo = encontradas[0]
    win32gui.PostMessage(hwnd, _WM_CLOSE, 0, 0)
    return Resultado(ok=True, detalle="cerrando " + titulo)


def cerrar_app_forzado(nombre):
    """mata TODOS los procesos con esa imagen con taskkill /F. DESTRUCTIVA.

    /F cierra SIN GUARDAR, por eso esta accion esta en DESTRUCTIVAS y
    ejecutar() no deja llegar a esta linea sin confirmado=True. /T mata
    tambien los subprocesos. Se va por IMAGEN (el .exe) y no por PID: el PID
    exige buscar la ventana previamente, y ademas asi se cierra la app aunque
    no tenga ninguna ventana abierta. El nombre vacio se rechaza ANTES de
    llamar a taskkill, asi que esta accion nunca puede matar "todo" por
    accidente.
    """
    if not isinstance(nombre, str) or not nombre.strip():
        return Resultado(ok=False, detalle="falta el nombre de la aplicacion")
    comando = ["taskkill", "/IM", str(nombre), "/T", "/F"]
    try:
        # check=True: un taskkill distinto de 0 (nombre que no existe, sin
        # permisos) levanta _ERROR_SUBPROCESO y aqui NO se sigue adelante
        subprocess.run(comando, check=True, capture_output=True, text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(
            ok=False, detalle="taskkill rechazo cerrar " + str(nombre))
    except OSError as error:
        return Resultado(ok=False, detalle="no pude correr taskkill: %s"
                         % (error,))
    return Resultado(ok=True, detalle="cerrado a la fuerza: " + str(nombre))


def buscar_archivo(texto, raiz=None):
    """busca archivos cuyo nombre CONTENGA `texto`. SOLO LEE. Inocua.

    os.walk con onerror (una carpeta sin permisos se salta) y con raiz por
    defecto ACOTADA a Escritorio/Documentos/Descargas: recorrer C:\\ entero
    tarda ~59 s (y ~2 min midiendo tamaños), medido el 2026-10-21. NO usa la
    busqueda de Windows (Windows Search), que exige su indice y su servicio.
    `raiz` acepta el alias ("documentos") o una ruta entera; una raiz que no
    es una carpeta devuelve ok=False SIN recorrer nada y sin reventar.
    """
    if not isinstance(texto, str) or not texto.strip():
        return Resultado(ok=False, detalle="falta el texto a buscar")
    buscado = texto.strip().lower()

    if raiz is None:
        raices = [os.path.join(os.path.expanduser("~"), carpeta)
                  for carpeta in _RAICES_BUSQUEDA.values()]
    else:
        ruta = _ruta_de_raiz(raiz)
        if ruta is None or not os.path.isdir(ruta):
            return Resultado(
                ok=False, detalle="raiz no valida: " + repr(raiz))
        raices = [ruta]

    encontrados = []
    for base in raices:
        for directorio, _carpetas, archivos in os.walk(
                base, onerror=_ignorar_error_de_walk):
            for archivo in archivos:
                if buscado in archivo.lower():
                    encontrados.append(os.path.join(directorio, archivo))
            if len(encontrados) >= _TOPE_RESULTADOS:
                break
        if len(encontrados) >= _TOPE_RESULTADOS:
            break

    if not encontrados:
        return Resultado(
            ok=True, detalle="no encontre archivos con " + repr(texto))
    usados = encontrados[:_TOPE_RESULTADOS]
    muestra = "; ".join(usados[:3])
    if len(usados) > 3:
        muestra += "; y %d mas" % (len(usados) - 3)
    return Resultado(
        ok=True, detalle="encontre %d con %r: %s"
        % (len(usados), texto, muestra))


# ---------------------------------------------------------------------------
# FASE E2c, GRUPO 2: el equipo. Volumen por pasos y silencio (sobre la MISMA
# cadena COM de volumen_leer, ya medidas el 2026-10-20), brillo por WMI
# (medido el 2026-10-21), energia (apagar, reiniciar, cancelar apagado y
# suspender), bloqueo de la estacion y captura de pantalla por GDI (medida
# el 2026-10-21, ~21 ms por captura).
# ---------------------------------------------------------------------------

def brillo(nivel):
    """pone el brillo de la pantalla en `nivel` (0 a 100). Inocua.

    powershell con WMI, VERIFICADO en esta PC el 2026-10-21: de 81 a 80 y
    de vuelta a 81 SIN permisos de administrador. La clase
    WmiMonitorBrightnessMethods solo existe en pantallas INTERNAS: en un
    monitor externo/escritorio la coleccion viene VACIA, y eso se devuelve
    como ok=False con su motivo, NUNCA como un exito a ciegas. El nivel se
    rechaza fuera de [0, 100] ANTES de correr powershell.
    """
    if isinstance(nivel, bool) or not isinstance(nivel, (int, float)) \
            or not 0 <= nivel <= 100:
        return Resultado(
            ok=False,
            detalle="brillo fuera de rango: %r (acepto de 0 a 100)"
            % (nivel,))
    # el script PREGUNTA primero si la clase trae algo: si esta VACIA no se
    # llama a WmiSetBrightness y el motor lo entiende por el texto "VACIA".
    # "OK" solo se imprime DESPUES de llamar al metodo, de modo que un exito
    # sin cambio real no puede pasar por bueno
    script = (
        "$m = Get-WmiObject -Namespace root\\wmi -Class "
        "WmiMonitorBrightnessMethods; if ($m) { $null = "
        "$m.WmiSetBrightness(0, %d); 'OK' } else { 'VACIA' }"
        % int(nivel))
    comando = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               script]
    try:
        hecho = subprocess.run(comando, check=True, capture_output=True,
                               text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(
            ok=False, detalle="powershell rechazo cambiar el brillo")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr powershell: %s" % (error,))
    if "VACIA" in (hecho.stdout or ""):
        return Resultado(
            ok=False,
            detalle="este equipo no controla el brillo de esta pantalla; "
                    "pruebe ms-settings:display")
    if "OK" not in (hecho.stdout or ""):
        return Resultado(
            ok=False, detalle="powershell no confirmo el cambio de brillo")
    return Resultado(ok=True, detalle="brillo en %d%%" % int(nivel))


def _validar_espera(segundos):
    """None si `segundos` es un numero mayor que cero; si no, su fallo

    shutdown /t 0 apaga SIN dejar tiempo para cancelar con shutdown /a, y
    por eso 0 (y cualquier cosa que no sea un numero mayor que cero) se
    rechaza ANTES de llamar a shutdown.
    """
    if isinstance(segundos, bool) or not isinstance(segundos, (int, float)):
        return Resultado(ok=False, detalle="espera invalida: %r" % (segundos,))
    if segundos <= 0:
        return Resultado(
            ok=False,
            detalle="la espera tiene que ser de mas de 0 segundos")
    return None


def apagar(segundos=30):
    """apaga la computadora con shutdown /s /t. DESTRUCTIVA: apaga la maquina.

    SOLO se llega aqui desde ejecutar() con confirmado=True: el freno esta
    ANTES de esta linea. La espera por defecto (30 s) deja rato para
    deshacerlo con cancelar_apagado (shutdown /a); una espera de 0 se
    rechaza porque no dejaria ni un instante de cancelacion.
    """
    rechazo = _validar_espera(segundos)
    if rechazo is not None:
        return rechazo
    comando = ["shutdown", "/s", "/t", str(int(segundos))]
    try:
        subprocess.run(comando, check=True, capture_output=True, text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(ok=False, detalle="shutdown rechazo apagar")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr shutdown: %s" % (error,))
    return Resultado(
        ok=True,
        detalle="apagando la computadora en %d segundos; si se arrepiente, "
                "diga cancela el apagado" % int(segundos))


def reiniciar(segundos=30):
    """reinicia la computadora con shutdown /r /t. DESTRUCTIVA: reinicia.

    SOLO se llega aqui desde ejecutar() con confirmado=True: el freno esta
    ANTES de esta linea. Igual que apagar, la espera deja rato para
    deshacerlo con cancelar_apagado y 0 se rechaza.
    """
    rechazo = _validar_espera(segundos)
    if rechazo is not None:
        return rechazo
    comando = ["shutdown", "/r", "/t", str(int(segundos))]
    try:
        subprocess.run(comando, check=True, capture_output=True, text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(ok=False, detalle="shutdown rechazo reiniciar")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr shutdown: %s" % (error,))
    return Resultado(
        ok=True,
        detalle="reiniciando la computadora en %d segundos; si se "
                "arrepiente, diga cancela el apagado" % int(segundos))


def cancelar_apagado():
    """cancela un apagado o reinicio pendiente con shutdown /a. Inocua.

    Solo DESHACE algo que ya estaba mandado: no apaga ni reinicia nada. Sin
    nada que cancelar, shutdown /a sale con error y eso se devuelve como
    ok=False, sin reventar. Por eso esta accion NO esta en DESTRUCTIVAS:
    frenarla dejaria al apagado sin forma de deshacerse.
    """
    try:
        subprocess.run(["shutdown", "/a"], check=True, capture_output=True,
                       text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(
            ok=False, detalle="no hay ningun apagado pendiente que cancelar")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr shutdown /a: %s" % (error,))
    return Resultado(ok=True, detalle="apagado cancelado")


def bloquear():
    """bloquea la estacion de trabajo (pantalla de bloqueo). Inocua.

    rundll32 user32,LockWorkStation solo PONE el candado: no cierra ni mata
    nada y no se pierde trabajo; para seguir trabajando hay que poner el PIN
    o la clave de la cuenta.
    """
    try:
        subprocess.run(["rundll32", "user32,LockWorkStation"], check=True,
                       capture_output=True, text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(
            ok=False, detalle="rundll32 rechazo bloquear la pantalla")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr rundll32: %s" % (error,))
    return Resultado(ok=True, detalle="pantalla bloqueada")


def suspender():
    """suspende el equipo con powrprof.SetSuspendState. DESTRUCTIVA.

    Puede perder lo que este sin GUARDAR (Windows no pregunta antes de
    dormir), y por eso esta en DESTRUCTIVAS y ejecutar() no deja llegar a
    esta linea sin confirmado=True. Los tres argumentos en False =
    SUSPENSION y no hibernacion, sin forzar el apagado y con los eventos de
    despertar activos. SetSuspendState devuelve BOOL: 0 es fallo.
    """
    if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
        return Resultado(
            ok=False, detalle="SetSuspendState no pudo suspender el equipo")
    return Resultado(ok=True, detalle="equipo en suspension")


# constantes de la captura de pantalla. SRCCOPY es el modo de BitBlt que
# copia los pixeles tal cual; las dos Struct son las cabeceras del BMP, y
# su tamanio (14 y 40 bytes) lo fija el formato para siempre
_SRCCOPY = 0x00CC0020
_CABECERA_BMP = struct.Struct("<2sIHHI")    # BITMAPFILEHEADER
_INFO_BMP = struct.Struct("<IiiHHIIiiII")   # BITMAPINFOHEADER
_SM_CXSCREEN = 0                            # GetSystemMetrics: ancho
_SM_CYSCREEN = 1                            # GetSystemMetrics: alto


def _bmp_de_pantalla():
    """pantalla principal en bytes BMP, o el Resultado que explica su fallo

    Devuelve (fallo, bmp): `fallo` es None si salio bien y `bmp` son los
    bytes del archivo ENTERO (las dos cabeceras + los pixeles); si algo
    fallo, `fallo` es un Resultado ok=False y `bmp` es None. Cadena GDI
    ctypes pura, VERIFICADA el 2026-10-21 en esta PC (~21 ms por captura):
    GetDC(0) -> CreateCompatibleDC/CreateCompatibleBitmap -> BitBlt ->
    GetDIBits. Sin Pillow ni pyautogui: no estan instaladas ni hacen falta.
    Captura la pantalla PRINCIPAL (GetDC(0)): en varios monitores el resto
    queda fuera.
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    ancho = user32.GetSystemMetrics(_SM_CXSCREEN)
    alto = user32.GetSystemMetrics(_SM_CYSCREEN)
    if not ancho or not alto:
        return (Resultado(ok=False, detalle="no pude medir la pantalla"),
                None)

    pantalla = user32.GetDC(0)
    if not pantalla:
        return (Resultado(
            ok=False, detalle="GetDC(0) no devolvio ninguna pantalla"), None)
    memoria = gdi32.CreateCompatibleDC(pantalla)
    bitmapa = gdi32.CreateCompatibleBitmap(pantalla, ancho, alto)
    if not memoria or not bitmapa:
        if bitmapa:
            gdi32.DeleteObject(bitmapa)
        if memoria:
            gdi32.DeleteDC(memoria)
        user32.ReleaseDC(0, pantalla)
        return (Resultado(
            ok=False,
            detalle="GDI no pudo preparar la memoria de imagen"), None)

    try:
        gdi32.SelectObject(memoria, bitmapa)
        if not gdi32.BitBlt(memoria, 0, 0, ancho, alto, pantalla, 0, 0,
                            _SRCCOPY):
            return (Resultado(
                ok=False, detalle="BitBlt no pudo copiar la pantalla"), None)
        # BITMAPINFOHEADER con alto POSITIVO: GetDIBits llena el buffer con
        # las filas de ABAJO hacia ARRIBA, que es EXACTAMENTE el orden en
        # que el formato BMP las guarda, asi que salen listas para el
        # archivo sin darles vuelta. Los 4 bytes de mas son la tabla de
        # paleta, ignorada a 32 bits pero exigida como puntero.
        cabeceras_info = _INFO_BMP.pack(
            _INFO_BMP.size, ancho, alto, 1, 32, 0, ancho * alto * 4,
            0, 0, 0, 0)
        info = ctypes.create_string_buffer(_INFO_BMP.size + 4)
        info.raw = cabeceras_info
        buffer_pixeles = ctypes.create_string_buffer(ancho * alto * 4)
        filas = gdi32.GetDIBits(memoria, bitmapa, 0, alto, buffer_pixeles,
                                ctypes.byref(info), 0)
        if not filas:
            return (Resultado(
                ok=False,
                detalle="GetDIBits no devolvio ningun pixel"), None)
        pixeles = buffer_pixeles.raw
        cabecera = _CABECERA_BMP.pack(
            b"BM", _CABECERA_BMP.size + _INFO_BMP.size + len(pixeles),
            0, 0, _CABECERA_BMP.size + _INFO_BMP.size)
        return (None, cabecera + cabeceras_info + pixeles)
    finally:
        gdi32.DeleteObject(bitmapa)
        gdi32.DeleteDC(memoria)
        user32.ReleaseDC(0, pantalla)


def capturar_pantalla(destino="escritorio"):
    """guarda una captura de la pantalla en un BMP dentro de `destino`. Inocua.

    `destino` es SIEMPRE una CARPETA (el alias de _RAICES_BUSQUEDA o una
    ruta entera); el nombre del archivo lo arma el motor con la hora
    ("captura_20261021_153042.bmp"), porque el reconocedor es puro y no
    trae reloj. NO se admite un archivo como destino (decision tomada y
    documentada el 2026-10-23: una sola forma, sin ambiguedad).

    La carpeta destino se CREA (o se valida) ANTES de capturar. MEDIDO en
    vivo el 2026-10-23 en esta PC: sin esto el GDI capturaba la pantalla
    COMPLETA y DESPUES fallaba al escribir en una carpeta que no existia
    ("[Errno 2] No such file or directory"), perdiendo el trabajo y sin
    decirle al usuario que lo unico que faltaba era la carpeta. Ahora una
    ruta imposible (carpeta que Windows no deja crear, o la ruta de un
    archivo EXISTENTE pasado por error) devuelve ok=False SIN gastar la
    captura. Solo ESCRIBE ese archivo nuevo: no borra ni modifica nada mas.
    La escritura va directa (open "wb") y no atomica porque el archivo NO
    existia antes: no hay nada que truncar (fundamento 25).
    """
    carpeta = _ruta_de_raiz(destino)
    if carpeta is None:
        return Resultado(
            ok=False, detalle="falta la carpeta donde guardar la captura")
    try:
        # exist_ok=True perdona la carpeta que YA existe; cualquier otra
        # cosa (un archivo pasado por error, caracteres ilegales, un disco
        # inexistente) levanta OSError ANTES de capturar pantalla
        os.makedirs(carpeta, exist_ok=True)
    except OSError as error:
        return Resultado(
            ok=False,
            detalle="no pude preparar la carpeta %s para la captura: %s"
            % (carpeta, error))
    fallo, bmp = _bmp_de_pantalla()
    if fallo is not None:
        return fallo
    nombre = "captura_%s.bmp" % datetime.datetime.now().strftime(
        "%Y%m%d_%H%M%S")
    ruta = os.path.join(carpeta, nombre)
    try:
        with open(ruta, "wb") as archivo:
            archivo.write(bmp)
    except OSError as error:
        return Resultado(
            ok=False,
            detalle="no pude guardar la captura en %s: %s" % (ruta, error))
    return Resultado(ok=True, detalle="captura guardada en " + ruta)


# ---------------------------------------------------------------------------
# FASE H2: MIRAR LA PANTALLA. El motor captura, REDUCE y deja un PNG en la
# carpeta temporal; NO sabe que existe el proveedor de IA. Quien convierte esa
# ruta en un bloque de imagen y la manda al modelo es main.py.
#
# POR QUE PNG EN PYTHON PURO (zlib + struct) Y NO POWERSHELL + System.Drawing:
#   1. la captura ya esta EN MEMORIA como BMP (_bmp_de_pantalla), asi que
#      convertir aqui no escribe ni lee ningun archivo intermedio ni levanta un
#      proceso de powershell por cada vistazo (~cientos de ms y un proceso
#      mas). La opcion (b) obligaria a guardar el BMP a disco solo para que
#      otro proceso lo abriera;
#   2. zlib y struct son stdlib: cero instalaciones (Pillow NO esta instalada
#      y no se instala) y la conversion queda cubierta por pruebas sin tocar
#      el sistema ni el GDI;
#   3. System.Drawing depende de GDI+ y de la version de PowerShell de la
#      maquina: otra cosa que puede fallar fuera de nuestro codigo.
#
# COSTE: una captura 1920x1080 son ~2.700 tokens y puede pesar varios MB en
# PNG sin reducir. Eso es dinero y latencia en CADA vistazo, asi que se
# reduce ANTES de codificar (ver _ANCHO_MAXIMO_PANTALLA).
# ---------------------------------------------------------------------------

# ancho maximo del PNG que se manda al modelo. POR QUE 1280: a 1920 de ancho
# el texto de una pantalla se lee, pero cuesta ~2.700 tokens de entrada; a
# 1280 (con 1920 queda 960x540, paso 2) se sigue leyendo una terminal o un
# documento y el coste baja a ~1/4. No se reduce mas: por debajo se pierden
# los numeros y las letras pequenas, y una captura ilegible no sirve de nada.
_ANCHO_MAXIMO_PANTALLA = 1280

# tope del proveedor de IA (Groq): 5 MB por imagen. El PNG se comprueba ANTES de
# devolver la ruta; uno mas grande se rechaza aqui con su motivo, en vez de
# mandarlo para que la API lo rechace con un 400 y el turno se pierda.
_TOPE_IMAGEN = 5 * 1024 * 1024

# firma de todo archivo PNG: 8 bytes fijos, iguales en cualquier plataforma
_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"


def _chunk_png(tipo, datos):
    """un chunk PNG: largo, tipo, datos y CRC32 de tipo+datos (big endian)

    El CRC32 del PNG es el de zlib, SIN el complemento a uno que llevan otros
    formatos: por eso se usa zlib.crc32 tal cual. `tipo` son los 4 bytes
    ASCII del chunk (IHDR, IDAT, IEND).
    """
    return (struct.pack(">I", len(datos)) + tipo + datos
            + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))


def _bmp_a_png(bmp, ancho_maximo=_ANCHO_MAXIMO_PANTALLA):
    """convierte un BMP de GDI a un PNG RGB reducido. Devuelve (fallo, png)

    `fallo` es un Resultado ok=False si el BMP no se puede leer; si todo sale
    bien es None y `png` son los bytes del archivo PNG entero. NUNCA lanza:
    una captura corrupta se devuelve como fallo con su motivo.

    Acepta los DOS formatos que puede entregar esta PC:
      - 32 bits (BGRX): es el que produce _bmp_de_pantalla, cuyo
        BITMAPINFOHEADER pide 32 bpp (comprobado leyendo esa funcion), y
      - 24 bits (BGR con filas rellenas a multiplo de 4).
    En los dos, un BMP con altura POSITIVA guarda las filas de ABAJO hacia
    ARRIBA: se leen al REVES para que la imagen no salga invertida. Los
    pixeles del BMP van en BGR y el PNG los pide en RGB.

    La REDUCCION es decimacion por paso entero (se toma 1 pixel de cada
    `paso`, sin interpolar): para 1920 de ancho el paso es 2 y queda 960x540.
    Se eligio asi y no un redimensionado con promedio porque no inventa
    colores, no necesita coma flotante y es barato en Python puro; el texto de
    una pantalla aguanta perfectamente el muestreo.
    """
    if not isinstance(bmp, (bytes, bytearray)) \
            or len(bmp) < _CABECERA_BMP.size + _INFO_BMP.size:
        return (Resultado(
            ok=False, detalle="la captura no alcanza para un BMP"), None)
    firma, _tamano, _r1, _r2, inicio = _CABECERA_BMP.unpack_from(bmp, 0)
    if firma != b"BM":
        return (Resultado(
            ok=False, detalle="la captura no empieza con la firma BM"),
            None)
    (_tamano_info, ancho, alto, planos, bits, compresion, _tamano_imagen,
     _xppm, _yppm, _usados, _importantes) = _INFO_BMP.unpack_from(
        bmp, _CABECERA_BMP.size)
    if planos != 1 or compresion != 0 or bits not in (24, 32):
        return (Resultado(
            ok=False,
            detalle="la captura no es un BMP RGB sin comprimir de 24 o 32 "
                    "bits"), None)
    if ancho <= 0 or alto == 0:
        return (Resultado(
            ok=False, detalle="la captura trae dimensiones invalidas"),
            None)
    if isinstance(ancho_maximo, bool) or not isinstance(ancho_maximo, int) \
            or ancho_maximo < 1:
        return (Resultado(
            ok=False, detalle="ancho maximo invalido: %r" % (ancho_maximo,)),
            None)

    # altura negativa = filas de ARRIBA hacia abajo (BMP top-down); positiva
    # = de abajo hacia arriba, que es lo que hace GDI
    filas = abs(alto)
    abajo_arriba = alto > 0
    bytes_pixel = bits // 8
    # toda fila del BMP se rellena a multiplo de 4 bytes (en 24 bits sobran
    # 1, 2 o 3; en 32 nunca sobra)
    relleno = (4 - (ancho * bytes_pixel) % 4) % 4
    tamano_fila = ancho * bytes_pixel + relleno
    if inicio < 0 or inicio + tamano_fila * filas > len(bmp):
        return (Resultado(
            ok=False, detalle="la captura esta incompleta: faltan pixeles"),
            None)

    # paso entero hacia arriba: 1920/1280 -> 2; 2560/1280 -> 2; 3840 -> 3.
    # El ancho final NUNCA pasa de ancho_maximo y una imagen chica no crece.
    paso = max(1, -(-ancho // ancho_maximo))
    nuevo_ancho = -(-ancho // paso)
    nuevo_alto = -(-filas // paso)

    crudo = bytearray()
    for fila_salida in range(nuevo_alto):
        origen = fila_salida * paso
        if abajo_arriba:
            origen = filas - 1 - origen
        base = inicio + origen * tamano_fila
        # cada fila PNG lleva DELANTE su byte de filtro. 0 = sin filtro: el
        # mas simple y el que menos CPU gasta, que es lo que importa aqui
        crudo.append(0)
        for columna in range(nuevo_ancho):
            pixel = base + columna * paso * bytes_pixel
            # BMP: B, G, R (y opcional X). PNG: R, G, B.
            crudo.append(bmp[pixel + 2])
            crudo.append(bmp[pixel + 1])
            crudo.append(bmp[pixel])

    ihdr = struct.pack(">IIBBBBB", nuevo_ancho, nuevo_alto, 8, 2, 0, 0, 0)
    png = (_FIRMA_PNG + _chunk_png(b"IHDR", ihdr)
           + _chunk_png(b"IDAT", zlib.compress(bytes(crudo)))
           + _chunk_png(b"IEND", b""))
    return (None, png)


def mirar_pantalla():
    """mira la pantalla y deja un PNG reducido en la carpeta temporal. Inocua.

    Solo LEE la pantalla: captura con el GDI de siempre (_bmp_de_pantalla),
    convierte a PNG, reduce a _ANCHO_MAXIMO_PANTALLA y escribe un archivo
    NUEVO en el temp del sistema. No borra, no mueve y no toca nada de lo que
    hay en pantalla. Devuelve la RUTA del PNG en `detalle`; el motor sigue
    devolviendo TEXTO y no sabe que existe el proveedor de IA.

    PRIVACIDAD (decision escrita aqui a proposito): esta accion es la UNICA
    puerta por la que una captura de la pantalla puede viajar a la API. La
    imagen sale SOLO cuando el modelo decide llamar a esta herramienta, o sea
    cuando Edward pregunta algo que necesita ver la pantalla. NO forma parte
    del contexto automatico ni se manda en cada turno: mandar la pantalla
    significa que TODO su contenido (correos, chats, claves escritas) viaja a
    Groq, y por eso es una herramienta explicita y no un anexo fijo.
    """
    fallo, bmp = _bmp_de_pantalla()
    if fallo is not None:
        return fallo
    fallo_png, png = _bmp_a_png(bmp)
    if fallo_png is not None:
        return fallo_png
    if len(png) > _TOPE_IMAGEN:
        return Resultado(
            ok=False,
            detalle="la captura quedo en %d bytes y la API no acepta imagenes "
                    "de mas de 5 MB" % len(png))
    nombre = "jarvis_pantalla_%s_%s.png" % (
        os.getpid(), datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    ruta = os.path.join(tempfile.gettempdir(), nombre)
    try:
        # archivo NUEVO en el temp: no habia nada que truncar (fundamento 25)
        with open(ruta, "wb") as archivo:
            archivo.write(png)
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude guardar la captura en %s: %s"
            % (ruta, error))
    return Resultado(ok=True, detalle=ruta)


# ---------------------------------------------------------------------------
# FASE E2d, GRUPO 3: escribir y teclar.
#
# POR QUE NO pyautogui.write(): medido en esta PC por el Investigador,
# pyautogui.write() y pydirectinput.write() descartan EN SILENCIO los
# caracteres con tilde, la enie y los signos de apertura (el texto de Jarvis
# viene de voz en es-ES, asi que lo mutilarian sin avisar). NO es pereza: es
# que por eso escribir_texto va por el portapapeles (CF_UNICODETEXT, que es
# UTF-16 y guarda cualquier caracter) y un Ctrl+V, dos pasos medibles. Todo
# ctypes puro: ni pyautogui ni pyperclip, que ademas no estarian instaladas.
# ---------------------------------------------------------------------------

# CF_UNICODETEXT es el formato de texto de Windows (UTF-16) y GMEM_MOVEABLE
# es el modo de memoria que SetClipboardData EXIGE para aceptarlo
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

# SendInput: INPUT_KEYBOARD y la bandera que SUELTA la tecla (sin ella la
# tecla quedaria apretada para siempre). De winuser.h, iguales en Windows
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002

# errores de pywin32 (win32api.error es pywintypes.error). Se guarda la
# referencia AQUI y no en cada except: las pruebas reemplazan motor.win32api
# por un doble y una referencia tardia llegaria como un MagicMock, que
# Python no acepta como destino de un except (mismo motivo que
# _ERROR_SUBPROCESO de arriba). Ojo: pywintypes.error hereda de Exception y
# NO de OSError, asi que un except OSError no lo alcanzaria.
_ERROR_WIN32 = win32api.error


# estructuras de INPUT de winuser.h. MOUSEINPUT esta aqui SOLO porque el
# union tiene que medir lo mismo que en C (32 bytes en x64): si INPUT no
# mide exactamente lo que Windows espera, SendInput rechaza todo el paquete
# con ERROR_INVALID_PARAMETER. Medido el 2026-10-21 en esta PC (64 bits):
# INPUT = 40 bytes y el wVk empieza en el byte 8; las dos medidas estan
# tambien en tests/test_fase_e2d.py.
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _DATOS_INPUT(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", ctypes.c_ulong), ("union", _DATOS_INPUT)]


# tamanio de INPUT calculado UNA vez a nivel de modulo: en las pruebas
# ctypes viene doblado y un sizeof en tiempo de ejecucion llegaria como un
# MagicMock dentro de la llamada a SendInput
_TAMANO_INPUT = ctypes.sizeof(_INPUT)

# teclas con nombre propio. Las que no estan aqui (una letra o un digito
# suelo) se convierten a su codigo virtual con ord(); las acentuadas NO
# existen como tecla virtual, y por eso escribir_texto va por portapapeles.
_TECLAS = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "espacio": 0x20,
    "space": 0x20,
    "retroceso": 0x08,
    "backspace": 0x08,
    "suprimir": 0x2E,
    "delete": 0x2E,
    "insertar": 0x2D,
    "inicio": 0x24,
    "home": 0x24,
    "fin": 0x23,
    "end": 0x23,
    "arriba": 0x26,
    "up": 0x26,
    "abajo": 0x28,
    "down": 0x28,
    "izquierda": 0x25,
    "left": 0x25,
    "derecha": 0x27,
    "right": 0x27,
    "re_pag": 0x21,
    "pageup": 0x21,
    "av_pag": 0x22,
    "pagedown": 0x22,
}
# F1 a F12 = 0x70 a 0x7B (se agregan con update y no con 12 lineas sueltas)
_TECLAS.update(("f%d" % numero, 0x70 + numero - 1) for numero in range(1, 13))


def _vk_de(palabra):
    """codigo virtual de UNA palabra de un atajo, o None si no se conoce

    isascii() es obligatorio aqui: "ñ".isalnum() es True y ord("Ñ") = 209,
    que NO es la tecla enie sino VK_NUMLOCK-1 (otra tecla cualquiera). Una
    letra acentuada no se puede mandar como tecla virtual, y esa es justamente
    la razon de existir de escribir_texto.
    """
    if palabra in _TECLAS:
        return _TECLAS[palabra]
    if len(palabra) == 1 and palabra.isascii() and palabra.isalnum():
        return ord(palabra.upper())
    return None


def _portapapeles_poner(texto):
    """pone `texto` en el portapapeles como CF_UNICODETEXT

    Devuelve None si salio bien, o un Resultado ok=False con su motivo si no.
    Cadena ctypes pura, VERIFICADA en esta PC el 2026-10-21 (el Investigador
    confirmo que llegan la enie y las tildes): OpenClipboard -> GlobalAlloc
    (GMEM_MOVEABLE, que es lo que el portapapeles EXIGE) -> GlobalLock ->
    memmove del UTF-16 con su terminador -> GlobalUnlock -> SetClipboardData
    -> CloseClipboard SIEMPRE en el finally.

    Ojo con la memoria: si SetClipboardData la acepta, el DUENO del bloque es
    el portapapeles y GlobalFree lo romperia; por eso GlobalFree solo aparece
    en los DOS caminos de fallo (alloc que no sirvio, y clipboard que
    rechazo). Un portapapeles ocupado se devuelve como ok=False ANTES de
    tocar nada, sin lanzar y sin pegar.
    """
    usuario = ctypes.windll.user32
    nucleo = ctypes.windll.kernel32
    # argtypes/restype: en esta PC Python es de 64 bits y HGLOBAL y LPVOID
    # tambien; sin ellos ctypes devuelve DWORD y TRUNCARIA el puntero a 32
    # bits, y SetClipboardData recibiria un manejo corrupto
    nucleo.GlobalAlloc.argtypes = (ctypes.c_uint, ctypes.c_size_t)
    nucleo.GlobalAlloc.restype = ctypes.c_void_p
    nucleo.GlobalLock.argtypes = (ctypes.c_void_p,)
    nucleo.GlobalLock.restype = ctypes.c_void_p
    usuario.SetClipboardData.argtypes = (ctypes.c_uint, ctypes.c_void_p)
    usuario.SetClipboardData.restype = ctypes.c_void_p

    if not usuario.OpenClipboard(None):
        return Resultado(
            ok=False,
            detalle="el portapapeles esta ocupado; intente de nuevo")
    try:
        datos = (texto + "\0").encode("utf-16-le")
        manejo = nucleo.GlobalAlloc(_GMEM_MOVEABLE, len(datos))
        if not manejo:
            return Resultado(
                ok=False, detalle="no consegui memoria para el texto")
        bloqueado = nucleo.GlobalLock(manejo)
        if not bloqueado:
            nucleo.GlobalFree(manejo)
            return Resultado(
                ok=False, detalle="no pude fijar el texto en el portapapeles")
        try:
            ctypes.memmove(bloqueado, datos, len(datos))
        finally:
            nucleo.GlobalUnlock(manejo)
        if not usuario.SetClipboardData(_CF_UNICODETEXT, manejo):
            nucleo.GlobalFree(manejo)
            return Resultado(
                ok=False, detalle="el portapapeles rechazo el texto")
        return None
    finally:
        usuario.CloseClipboard()


def mandar_teclas(atajo):
    """manda UNA combinacion de teclas a la ventana activa con ctypes
    user32.SendInput. Inocua: no borra ni apaga nada.

    Acepta "ctrl+c", "alt+tab", "enter", "ctrl+shift+escape": la frase se
    parte por "+" y cada trozo se convierte a su codigo virtual. Las teclas
    se PULSAN en el orden dado y se SUELTAN en orden inverso con
    KEYEVENTF_KEYUP, que es como lo hace un teclado de verdad.

    NO VERIFICADO EN VIVO (y aqui esta dicho a proposito): el Investigador NO
    pudo probar esta funcion inyectando teclas reales, porque inyectar teclas
    escribe de verdad en lo que Edward tenga enfrente; NO debe probarse en
    una sesion real sin una ventana de prueba propia. Lo que SI esta medido
    (2026-10-21) y lo comprueban las pruebas es la ESTRUCTURA que sale:
    INPUT de 40 bytes, el wVk en el byte 8, KEYEVENTF_KEYUP en las solturas
    y el conteo que devuelve SendInput. Si una combinacion no se conoce ->
    ok=False sin mandar NADA; si SendInput acepta menos teclas de las que se
    le dieron, tambien ok=False. NUNCA lanza excepciones.
    """
    if not isinstance(atajo, str) or not atajo.strip():
        return Resultado(ok=False, detalle="falta la combinacion de teclas")
    trozos = [trozo.strip().lower() for trozo in atajo.split("+")]
    if any(not trozo for trozo in trozos):
        return Resultado(ok=False, detalle="atajo mal formado: %r" % (atajo,))
    codigos = []
    for trozo in trozos:
        codigo = _vk_de(trozo)
        if codigo is None:
            return Resultado(
                ok=False,
                detalle="no conozco la tecla %r de %r" % (trozo, atajo))
        codigos.append(codigo)

    # una entrada BAJANDO por tecla y otra SUBIENDO por tecla, al reves
    entradas = (_INPUT * (2 * len(codigos)))()
    for posicion, codigo in enumerate(codigos):
        entrada = entradas[posicion]
        entrada.type = _INPUT_KEYBOARD
        entrada.ki.wVk = codigo
        entrada.ki.dwFlags = 0
    for posicion, codigo in enumerate(reversed(codigos)):
        entrada = entradas[len(codigos) + posicion]
        entrada.type = _INPUT_KEYBOARD
        entrada.ki.wVk = codigo
        entrada.ki.dwFlags = _KEYEVENTF_KEYUP

    aceptadas = ctypes.windll.user32.SendInput(
        len(entradas), entradas, _TAMANO_INPUT)
    if aceptadas != len(entradas):
        return Resultado(
            ok=False,
            detalle="SendInput acepto %s de %d teclas" % (aceptadas, len(entradas)))
    return Resultado(ok=True, detalle="teclas enviadas: " + atajo)


def escribir_texto(texto):
    """escribe `texto` en la ventana ACTIVA, con sus tildes y su enie
    enteras. Inocua: solo escribe donde esta el cursor.

    POR QUE NO pyautogui.write(): medido en esta PC por el Investigador,
    pyautogui.write() y pydirectinput.write() descartan EN SILENCIO
    "á é í ó ú ñ ¿ ¡", y el texto de Jarvis viene de voz en es-ES: lo
    mutilarian sin avisar. NO es pereza, es que pyautogui no sirve para
    esto. El camino es portapapeles (CF_UNICODETEXT = UTF-16, guarda
    cualquier caracter) + Ctrl+V, y los DOS pasos se pueden medir: si el
    portapapeles esta ocupado se devuelve ok=False sin reventar y sin
    pegar nada; si el Ctrl+V no sale, el texto al menos queda copiado.
    """
    if not isinstance(texto, str) or not texto.strip():
        return Resultado(
            ok=False, detalle="falta el texto que hay que escribir")
    fallo = _portapapeles_poner(texto)
    if fallo is not None:
        return fallo
    pegado = mandar_teclas("ctrl+v")
    if not pegado.ok:
        return Resultado(
            ok=False,
            detalle="el texto quedo en el portapapeles pero no pude pegarlo: "
                    + pegado.detalle)
    return Resultado(ok=True, detalle="escrito: " + texto)


# banderas de mouse_event (winuser.h): SIEMPRE va el par ABAJO y ARRIBA del
# mismo boton, o el boton quedaria apretado
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040

_BOTONES_RATON = {
    "izquierdo": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
    "derecho": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
    "medio": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
    "central": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
}


def mover_raton(x, y):
    """mueve el cursor a la posicion (x, y) de la pantalla. Inocua: solo
    MUEVE el puntero, no pulsa nada.

    win32api.SetCursorPos (pywin32, ya instalada). Las coordenadas se
    validan contra el tamanio REAL de la pantalla ANTES de mover: fuera de
    ella SetCursorPos lanzaria un pywintypes.error, y eso aqui se traduce
    en ok=False con su motivo, nunca en una excepcion hasta main.py.
    """
    if isinstance(x, bool) or not isinstance(x, int) \
            or isinstance(y, bool) or not isinstance(y, int):
        return Resultado(
            ok=False,
            detalle="coordenadas invalidas: %r, %r (necesito numeros enteros)"
            % (x, y))
    ancho = win32api.GetSystemMetrics(_SM_CXSCREEN)
    alto = win32api.GetSystemMetrics(_SM_CYSCREEN)
    if not isinstance(ancho, int) or not isinstance(alto, int) \
            or not (0 <= x < ancho) or not (0 <= y < alto):
        return Resultado(
            ok=False,
            detalle="(%d, %d) esta fuera de la pantalla" % (x, y))
    try:
        win32api.SetCursorPos((x, y))
    except _ERROR_WIN32 as error:
        return Resultado(
            ok=False, detalle="no pude mover el raton: %s" % (error,))
    return Resultado(ok=True, detalle="raton en %d, %d" % (x, y))


def clic(boton="izquierdo"):
    """pulsa y suelta un boton del raton DONDE ESTE el cursor. Inocua.

    win32api.mouse_event con las banderas de ABAJO y DESPUES de ARRIBA en
    la MISMA posicion (0, 0), que es un clic completo; un solo evento
    dejaria el boton apretado. No se mueve el cursor y no se pulsa nada
    mas. Un boton que no existe -> ok=False SIN llamar a mouse_event.
    """
    if not isinstance(boton, str):
        return Resultado(ok=False, detalle="boton invalido: %r" % (boton,))
    par = _BOTONES_RATON.get(boton.strip().lower())
    if par is None:
        return Resultado(
            ok=False,
            detalle="no conozco el boton %r: uso izquierdo, derecho o medio"
            % (boton,))
    abajo, arriba = par
    try:
        win32api.mouse_event(abajo, 0, 0, 0)
        win32api.mouse_event(arriba, 0, 0, 0)
    except _ERROR_WIN32 as error:
        return Resultado(ok=False, detalle="no pude hacer clic: %s" % (error,))
    return Resultado(ok=True, detalle="clic " + boton.strip().lower())


# ---------------------------------------------------------------------------
# FASE E2d, GRUPO 4: informacion del sistema. Todo stdlib o ctypes de
# Windows (shutil, subprocess, os y las tres APIs de abajo), sin psutil ni
# Pillow ni nada instalado de mas. Las NUEVE acciones de esta fase SOLO
# LEEN o escriben donde esta el cursor: ninguna esta en DESTRUCTIVAS.
# ---------------------------------------------------------------------------

_GB = 1024 ** 3


def _gb(bytes_):
    """bytes a GB con un decimal, ya como texto para decirlo en voz"""
    return "%.1f GB" % (bytes_ / _GB)


def _peso_legible(bytes_):
    """bytes en la unidad que se pueda entender (GB, MB o KB), un decimal"""
    if bytes_ >= _GB:
        return _gb(bytes_)
    if bytes_ >= 1024 ** 2:
        return "%.1f MB" % (bytes_ / (1024 ** 2))
    return "%.1f KB" % (bytes_ / 1024)


def espacio_disco(ruta="C:/"):
    """cuanto cabe, cuanto libre y cuanto usado hay en `ruta`. SOLO LEE.

    shutil.disk_usage: stdlib puro. Una ruta o unidad que no existe levanta
    OSError (medido: FileNotFoundError WinError 161) y eso se traduce en
    ok=False, nunca en una excepcion hasta main.py. Los tres numeros salen
    en GB con un decimal.
    """
    if not isinstance(ruta, str) or not ruta.strip():
        return Resultado(ok=False, detalle="falta la ruta del disco")
    destino = ruta.strip()
    try:
        uso = shutil.disk_usage(destino)
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude leer el disco %s: %s" % (destino, error))
    return Resultado(
        ok=True,
        detalle="disco %s: %s en total, %s libres, %s en uso"
        % (destino, _gb(uso.total), _gb(uso.free), _gb(uso.used)))


# FILETIME: dos DWORD (100 ns desde 1601); MEMORYSTATUSEX: la version de 64
# bits del resumen de memoria (GlobalMemoryStatus vieja trunca los totales
# en una PC con mas de 4 GB)
class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_ulong),
        ("dwHighDateTime", ctypes.c_ulong),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


# dwLength tiene que traer el tamanio de la struct ANTES de la llamada. Se
# calcula UNA vez a nivel de modulo porque en las pruebas ctypes viene
# doblado y un sizeof en tiempo de ejecucion llegaria como un MagicMock
_TAMANO_MEMORYSTATUSEX = ctypes.sizeof(_MEMORYSTATUSEX)

# intervalo entre las DOS lecturas de GetSystemTimes, en segundos. DOS
# lecturas pegadas dan delta total = 0 y un 0% inventado (fundamento 34);
# 0.2 s da deltas comodos. El intervalo va DENTRO de la funcion y termina:
# NUNCA hay un while esperando, que dejaria a main.py colgado
_INTERVALO_CPU = 0.2


def _filetime_entero(estructura):
    """FILETIME (dos DWORD) como un unico entero de 64 bits"""
    return (estructura.dwHighDateTime << 32) | estructura.dwLowDateTime


def _cpu_porcentaje():
    """uso de CPU en [0, 100] medido con DOS lecturas de GetSystemTimes

    Devuelve (fallo, porcentaje): `fallo` es un Resultado ok=False si la API
    fallo o si la lectura salio INCONSISTENTE (delta total <= 0, o mas
    tiempo ocioso que total: eso es un dato corrupto, y publicar un 0%
    inventado seria mentirle a Edward); es None si todo salio bien. No hay
    forma de que esto se quede girando: son DOS llamadas y un sleep.
    """
    nucleo = ctypes.windll.kernel32
    ocioso_1, sistema_1, usuario_1 = _FILETIME(), _FILETIME(), _FILETIME()
    if not nucleo.GetSystemTimes(
            ctypes.byref(ocioso_1), ctypes.byref(sistema_1),
            ctypes.byref(usuario_1)):
        return (Resultado(
            ok=False, detalle="GetSystemTimes no devolvio tiempos"), None)
    time.sleep(_INTERVALO_CPU)
    ocioso_2, sistema_2, usuario_2 = _FILETIME(), _FILETIME(), _FILETIME()
    if not nucleo.GetSystemTimes(
            ctypes.byref(ocioso_2), ctypes.byref(sistema_2),
            ctypes.byref(usuario_2)):
        return (Resultado(
            ok=False, detalle="GetSystemTimes no devolvio tiempos"), None)

    delta_total = (_filetime_entero(sistema_2) - _filetime_entero(sistema_1)
                   + _filetime_entero(usuario_2) - _filetime_entero(usuario_1))
    delta_ocioso = (_filetime_entero(ocioso_2) - _filetime_entero(ocioso_1))
    if delta_total <= 0 or delta_ocioso < 0 or delta_ocioso > delta_total:
        return (Resultado(
            ok=False,
            detalle="la lectura de CPU salio inconsistente (%d/%d ocioso)"
            % (delta_ocioso, delta_total)), None)
    return (None, 100.0 * (delta_total - delta_ocioso) / delta_total)


def cpu_y_ram():
    """uso de CPU y memoria RAM del equipo. SOLO LEE. Inocua.

    CPU: ctypes GetSystemTimes DOS veces con _INTERVALO_CPU de diferencia
    (un SOLO lectura no da porcentaje; dos lecturas son lo minimo honesto,
    y por eso van aca dentro y con un tope, no en un bucle). RAM:
    GlobalMemoryStatusEx, con el porcentaje que calcula Windows (dwMemoryLoad)
    y sus totales en bytes. Un total de RAM en 0 es un dato inutilizable ->
    ok=False: nunca se anuncia un 0% inventado.
    """
    estado = _MEMORYSTATUSEX()
    estado.dwLength = _TAMANO_MEMORYSTATUSEX
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
        return Resultado(
            ok=False,
            detalle="GlobalMemoryStatusEx no devolvio la memoria")
    if not estado.ullTotalPhys:
        return Resultado(
            ok=False, detalle="la memoria total salio en 0: no hay dato")

    fallo_cpu, cpu = _cpu_porcentaje()
    if fallo_cpu is not None:
        return fallo_cpu

    usada = estado.ullTotalPhys - estado.ullAvailPhys
    return Resultado(
        ok=True,
        detalle="CPU %d%%, RAM usada %s de %s (%d%%)"
        % (round(cpu), _gb(usada), _gb(estado.ullTotalPhys),
           estado.dwMemoryLoad))


# SYSTEM_POWER_STATUS de WinBase.h: los 4 BYTE van primero y los dos DWORD
# despues, sin relleno (medido: la struct pesa 12 bytes)
class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def bateria():
    """estado de la bateria y de la corriente. SOLO LEE. Inocua.

    ctypes GetSystemPowerStatus. Windows contesta 255 ("desconocido") en
    BatteryLifePercent cuando no hay bateria, que es lo normal en un
    escritorio (medido: BatteryFlag con el bit 128 = sin bateria). Ahi se
    DICE "desconocida" tal cual y NO se inventa ningun porcentaje
    (fundamento 34: un 0% inventado es indistinguible de un 0% real).
    ok=True porque la consulta salio bien; el dato, cuando no existe, se
    dice honesto en el detalle.
    """
    estado = _SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(estado)):
        return Resultado(
            ok=False, detalle="GetSystemPowerStatus no devolvio la bateria")

    if estado.ACLineStatus == 1:
        corriente = "conectado a la corriente"
    elif estado.ACLineStatus == 0:
        corriente = "funcionando con bateria"
    else:
        corriente = "corriente desconocida"

    if estado.BatteryLifePercent == 255 or (estado.BatteryFlag & 0x80):
        dato = "bateria desconocida o inexistente"
    else:
        dato = "bateria al %d%%" % estado.BatteryLifePercent
    return Resultado(ok=True, detalle=corriente + ", " + dato)


# cuantos procesos salen en la lista COMPLETA (procesos(detalle=True)):
# tasklist de esta PC lista ~300 y eso no se puede ni leer ni escuchar
_TOPE_PROCESOS = 40

# cuantos procesos salen en el RESUMEN HABLABLE por defecto: los que mas
# memoria usan. MEDIDO en vivo el 2026-10-23 en esta PC: la lista de siempre
# salio con 1.402 caracteres, unos 18 segundos hablando sin parar, y Jarvis
# la LEERIA ENTERA; con _TOPE_RESUMEN el resumen queda por debajo de 300
# (~4 segundos), igual que el resto de acciones del motor (cpu_y_ram 40,
# bateria 40, papelera 34, wifi 63). El listado largo NO se pierde: se pide
# con procesos(detalle=True), que ahi si cabe _TOPE_PROCESOS procesos
_TOPE_RESUMEN = 5


def _memoria_en_kb(memoria):
    """los digitos de la columna de memoria de tasklist, SOLO para ordenar

    tasklist localiza la memoria ("1,234,568 KB" con coma de miles, medido
    el 2026-10-21): esos digitos se juntan SOLO para ordenar de mayor a
    menor y quedarse con los que mas memoria usan; el texto que se MUESTRA
    es SIEMPRE el literal original (nunca un float() que asuma ingles). Sin
    ningun digito, la memoria vale 0 para el orden.
    """
    digitos = "".join(caracter for caracter in memoria if caracter.isdigit())
    return int(digitos) if digitos else 0


def procesos(detalle=False):
    """resumen de los procesos que mas memoria usan, o la lista completa.

    tasklist en FORMATO CSV con /nh, y NO se convierte la columna de memoria
    a numero para MOSTRARLA: TRAMPA MEDIDA en esta PC (2026-10-21), tasklist
    /fo csv /nh devuelve la memoria LOCALIZADA con separador de miles -"10,
    868 KB"- en una sola columna entrecomillada. Si una version la dejara sin
    comillas, la columna llegaria partida en trozos y aqui se vuelven a
    juntar con ","; en ningun caso hay un float() que asuma ingles. Ante la
    duda el texto sale TAL CUAL.

    POR QUE UN RESUMEN POR DEFECTO (medido en vivo el 2026-10-23): la lista
    de siempre salio con 1.402 caracteres, unos 18 segundos hablando sin
    parar, y Jarvis la LEERIA ENTERA. Por defecto se dicen SOLO los
    _TOPE_RESUMEN procesos que mas memoria usan (el orden usa los digitos
    de la memoria, lo unico que da tasklist) mas el TOTAL, que no se pierde.
    La lista acotada COMPLETA se pide con procesos(detalle=True) (hasta
    _TOPE_PROCESOS), que ahi si puede pasar los 300 caracteres. NUNCA lanza
    excepciones.
    """
    try:
        salida = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"], check=True,
            capture_output=True, text=True)
    except _ERROR_SUBPROCESO:
        return Resultado(ok=False, detalle="tasklist no quiso correr")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr tasklist: %s" % (error,))

    total = 0
    muestra = []
    for fila in csv.reader((salida.stdout or "").splitlines()):
        if not fila or not fila[0].strip():
            continue
        total += 1
        nombre = fila[0].strip()
        pid = fila[1].strip() if len(fila) > 1 else ""
        item = "%s (pid %s)" % (nombre, pid) if pid else nombre
        memoria = ""
        if len(fila) > 4:
            memoria = ",".join(trozo.strip() for trozo in fila[4:])
            if memoria:
                item += ", " + memoria
        muestra.append((_memoria_en_kb(memoria), item))

    if not muestra:
        return Resultado(
            ok=False, detalle="tasklist no devolvio ningun proceso")

    if not detalle:
        cumbres = sorted(muestra, reverse=True)[:_TOPE_RESUMEN]
        return Resultado(
            ok=True,
            detalle="%d procesos; los %d que mas memoria usan: %s"
            % (total, len(cumbres),
               "; ".join(item for _peso, item in cumbres)))

    usados = muestra[:_TOPE_PROCESOS]
    texto = "%d procesos: %s" % (
        total, "; ".join(item for _peso, item in usados))
    if total > len(usados):
        texto += "; y %d mas" % (total - len(usados))
    return Resultado(ok=True, detalle=texto)


def peso_de(raiz=None):
    """peso en disco de `raiz` sumando sus archivos con os.walk. Inocua.

    Solo SUMA tamanios: no borra ni modifica nada. Igual que buscar_archivo,
    la raiz por defecto viene ACOTADA a Escritorio/Documentos/Descargas:
    recorrer C:\\ entero tarda ~59 s medido el 2026-10-21, y con tamanios
    mas. El onerror salta las carpetas sin permisos (C:\\System Volume
    Information, por ejemplo) y un archivo ilegible se CUENTA aparte en vez
    de romper la suma. Ruta invalida -> ok=False SIN recorrer nada.
    `raiz` acepta el alias ("documentos") o una ruta entera.
    """
    if raiz is None:
        raices = [os.path.join(os.path.expanduser("~"), carpeta)
                  for carpeta in _RAICES_BUSQUEDA.values()]
        origen = "Escritorio, Documentos y Descargas"
    else:
        ruta = _ruta_de_raiz(raiz)
        if ruta is None or not os.path.isdir(ruta):
            return Resultado(
                ok=False, detalle="raiz no valida: " + repr(raiz))
        raices = [ruta]
        origen = str(raiz)

    total = 0
    archivos = 0
    ilegibles = 0
    for base in raices:
        for directorio, _carpetas, nombres in os.walk(
                base, onerror=_ignorar_error_de_walk):
            for nombre in nombres:
                try:
                    total += os.path.getsize(os.path.join(directorio, nombre))
                except OSError:
                    ilegibles += 1
                    continue
                archivos += 1

    if not archivos:
        # sin archivos la suma seria 0.0 KB, que es indistinguible de un
        # cero medido de verdad (fundamento 34): se dice sin archivos
        return Resultado(
            ok=True, detalle="no pude pesar ningun archivo en " + origen)
    detalle = "%s: %s en %d archivos" % (origen, _peso_legible(total), archivos)
    if ilegibles:
        detalle += " (no pude leer %d)" % ilegibles
    return Resultado(ok=True, detalle=detalle)


# ---------------------------------------------------------------------------
# FASE E2e, GRUPO 5: red y descargas, mas papelera y borrado.
#
# RED: medido el 2026-10-21 en esta PC, netsh y powershell contestan en la
# pagina de codigos de la CONSOLA (cp850 = ibm850): con la decodificacion
# por defecto "Descripcion" salia "Descripci¢n" y ninguna clave con tilde
# casaba. Por eso _texto_oem lee esos bytes a mano, con cp850 y con
# errors="replace" para que un byte raro se marque y nunca reviente.
# El ENCENDIDO de wifi y de bluetooth necesita permisos de administrador
# (medido: un proceso NO elevado no puede), y aqui NO se finge que apaga:
# wifi() y bluetooth() se niegan SIEMPRE diciendo el motivo, sin mandar ni
# un comando. Lo que SI se puede sin admin es LEER el estado, y eso va
# encadenado aca abajo.
# DESCARGAS: urllib de la stdlib, con timeout (sin el, un servidor mudo
# dejaria a main.py colgado). descargar SOLO crea un archivo NUEVO (nunca
# sobrescribe) y lo deja completo en su nombre con os.replace.
# PAPELERA Y BORRADO: SHQueryRecycleBinW solo LEE peso y conteo. Lo que
# borra de verdad es borrar_archivo, que es DESTRUCTIVA: os.remove NO pasa
# por la papelera y es IRREVERSIBLE (Send2Trash, que si lo haria reversible,
# NO esta instalada en esta PC y no se instala sola).
# ---------------------------------------------------------------------------

# pagina de codigos de la consola de Windows, MEDIDA el 2026-10-21 en esta
# PC: netsh devolvio bytes cp850 y powershell dijo ibm850 en
# [Console]::OutputEncoding
_CODIGO_CONSOLA = "cp850"

# espera maxima de una llamada de red, en segundos
_ESPERA_RED = 15

# trozo de lectura de una descarga, en bytes: un archivo de 2 GB no cabe en
# memoria, y el ".parte" de abajo va quedando a medias si algo corta
_TROZO_DESCARGA = 1024 * 1024

# prefijo de la busqueda web: el TEXTO se escapa con urllib.parse.quote
_BUSQUEDA_GOOGLE = "https://www.google.com/search?q="

# cuantos dispositivos bluetooth salen en la lista (medido: 10 en esta PC;
# como en procesos, cientos de nombres no se pueden ni leer ni escuchar)
_TOPE_BLUETOOTH = 5

# lo unico que wifi() y bluetooth() pueden contestar hoy: el encendido y el
# apagado de ambas radios exigen permisos de administrador, medido el
# 2026-10-21 en esta PC (un proceso no elevado no puede)
_SIN_PERMISOS = ("requiere permisos de administrador; "
                 "ejecuta Jarvis elevado")


def _texto_oem(salida):
    """salida de subprocess en bytes OEM (consola cp850) como texto

    Medido el 2026-10-21 en esta PC: "netsh wlan show interfaces" devolvio
    b"Descripci\xa2n" (cp850) y powershell dijo ibm850. Con la decodificacion
    por defecto eso salia "Descripci¢n" y la clave "Senal" jamas casaba.
    errors="replace": un byte fuera de tabla se marca en el texto en vez de
    lanzar UnicodeDecodeError hasta main.py. Un stdout None (sin salida) se
    trata como vacio, y un texto ya decodificado pasa tal cual.
    """
    crudo = salida.stdout or b""
    if isinstance(crudo, str):
        return crudo
    return crudo.decode(_CODIGO_CONSOLA, errors="replace")


def wifi_estado():
    """estado de la interfaz wifi con netsh. SOLO LEE. Inocua.

    "netsh wlan show interfaces" solo CONSULTA: no cambia nada del equipo.
    La salida se decodifica con cp850 (_texto_oem) y se la busca por su
    clave en ESPANOL y en INGLES ("estado"/"state", "senal"/"signal"), que
    dependen del idioma de Windows (medidas las dos formas en esta PC).
    Con varias interfaces se queda con la PRIMERA. Una salida truncada
    nunca revienta: lo que haya se dice; si no hay NADA conocido, o si
    netsh sale con error (equipo sin wifi), se devuelve ok=False con su
    motivo. NUNCA lanza excepciones.
    """
    try:
        hecho = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"], check=True,
            capture_output=True)
    except _ERROR_SUBPROCESO:
        return Resultado(
            ok=False,
            detalle="netsh no quiso mostrar el wifi; puede que no haya "
                    "interfaz wifi en este equipo")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr netsh: %s" % (error,))

    campos = {}
    for linea in _texto_oem(hecho).splitlines():
        clave, separador, valor = linea.partition(":")
        if separador:
            campos.setdefault(clave.strip().lower(), valor.strip())

    partes = []
    estado = campos.get("estado") or campos.get("state")
    red = campos.get("ssid")
    senal = campos.get("señal") or campos.get("signal")
    if estado:
        partes.append("estado " + estado)
    if red:
        partes.append("red " + red)
    if senal:
        partes.append("señal " + senal)
    if not partes:
        return Resultado(
            ok=False, detalle="netsh no devolvio ningun dato de wifi")
    return Resultado(ok=True, detalle="wifi: " + ", ".join(partes))


def wifi(nombre_accion=""):
    """NUNCA enciende ni apaga el wifi: se niega y dice por que. Inocua.

    El encendido real es "netsh interface set interface ... admin=enable"
    (o admin=disable) y MEDIDO el 2026-10-21: un proceso NO elevado
    (IsUserAnAdmin = 0) no puede; Jarvis corre sin permisos de
    administrador. Por eso aqui NUNCA se manda el netsh y NUNCA se anuncia
    un cambio que no paso: el resultado es SIEMPRE ok=False con _SIN_PERMISOS.
    El argumento (la intencion pedida: "apagar" o "encender") viaja sin
    usarse por si un dia Jarvis corre elevado y esta funcion se implementa
    de verdad; entonces habra que reclasificarla en DESTRUCTIVAS, porque
    cortar la red tambien es algo que conviene confirmar. El valor por
    defecto es para que un ejecutar() sin argumentos no caiga en un
    TypeError: el motor nunca lanza excepciones.
    """
    return Resultado(ok=False, detalle=_SIN_PERMISOS)


# CSV de powershell (medido el 2026-10-21 en esta PC: 10 dispositivos). La
# cabecera "Status","FriendlyName" la escribe el propio Select-Object con
# esos nombres en CUALQUIER idioma de Windows: no es texto visible del
# sistema operativo y por eso no cambia con el espanol
_GUIA_BLUETOOTH = "Status"


def bluetooth_estado():
    """dispositivos bluetooth con powershell. SOLO LEE. Inocua.

    Get-PnpDevice -Class Bluetooth solo LISTA dispositivos: no los activa
    ni los desactiva (eso seria Disable-PnpDevice / Enable-PnpDevice, que
    SI exige permisos de administrador y esta en bluetooth(), abajo). La
    lista sale en CSV con Select-Object + ConvertTo-Csv, el mismo formato
    que procesos() ya lee, y se decodifica con _texto_oem (la consola es
    cp850 y un nombre de dispositivo puede traer tildes). Equipo sin
    bluetooth -> la lista viene vacia y eso se dice tal cual como ok=True:
    la consulta salio bien, lo que no hay es hardware. powershell que no
    corre es ok=False. La lista viene acotada a _TOPE_BLUETOOTH.
    """
    script = ("Get-PnpDevice -Class Bluetooth | Select-Object Status, "
              "FriendlyName | ConvertTo-Csv -NoTypeInformation")
    comando = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               script]
    try:
        hecho = subprocess.run(comando, check=True, capture_output=True)
    except _ERROR_SUBPROCESO:
        return Resultado(
            ok=False, detalle="powershell no quiso listar el bluetooth")
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude correr powershell: %s" % (error,))

    filas = [fila for fila in csv.reader(_texto_oem(hecho).splitlines())
             if fila and fila[0].strip()]
    if filas and filas[0][0].strip() == _GUIA_BLUETOOTH:
        filas = filas[1:]  # la cabecera de ConvertTo-Csv no es un dispositivo
    nombres = []
    con_problema = 0
    for fila in filas:
        if len(fila) < 2:
            continue
        nombres.append(fila[1].strip())
        if fila[0].strip().lower() != "ok":
            con_problema += 1
    if not nombres:
        return Resultado(ok=True, detalle="no hay dispositivos bluetooth")
    muestra = "; ".join(nombres[:_TOPE_BLUETOOTH])
    if len(nombres) > _TOPE_BLUETOOTH:
        muestra += "; y %d mas" % (len(nombres) - _TOPE_BLUETOOTH)
    detalle = "%d dispositivos bluetooth" % len(nombres)
    if con_problema:
        detalle += " (%d con problema)" % con_problema
    return Resultado(ok=True, detalle=detalle + ": " + muestra)


def bluetooth(nombre_accion=""):
    """NUNCA enciende ni apaga el bluetooth: se niega y dice por que. Inocua.

    Mismo criterio medido que wifi: Enable-PnpDevice y Disable-PnpDevice
    exigen permisos de administrador, y aqui NO se finge el cambio ni se
    toca el dispositivo. El resultado es SIEMPRE ok=False con _SIN_PERMISOS,
    aunque la accion llegue sin argumentos (el motor nunca lanza).
    """
    return Resultado(ok=False, detalle=_SIN_PERMISOS)


def _leer_http(url):
    """(fallo, contenido) de una url, con timeout. Inocua: solo LEE la red.

    Devuelve (fallo, contenido): `fallo` es un Resultado ok=False si la url
    falta, si el servidor contesta distinto de 200 o si la red falla; es
    None si salio bien y entonces `contenido` son los bytes leidos. NUNCA
    lanza: urllib levanta URLError (que ES un OSError) y ValueError para una
    url mal formada, y las dos se traducen aqui.
    """
    if not isinstance(url, str) or not url.strip():
        return (Resultado(ok=False, detalle="falta la url"), None)
    try:
        with urllib.request.urlopen(
                url.strip(), timeout=_ESPERA_RED) as respuesta:
            estado = getattr(respuesta, "status", 200)
            if estado != 200:
                return (Resultado(
                    ok=False,
                    detalle="el servidor contesto %s para %s"
                    % (estado, url.strip())), None)
            return (None, respuesta.read())
    except (OSError, ValueError) as error:
        return (Resultado(
            ok=False, detalle="no pude leer %s: %s" % (url.strip(), error)),
                None)


def _nombre_de_url(url):
    """nombre de archivo que deja la url, o None si la url no trae ninguno

    Solo string: sin os.path, para que una url rara no dependa del sistema
    de archivos de nadie. "https://x.com/a/b.png?x=1" -> "b.png".
    """
    ruta = urllib.parse.urlparse(url.strip()).path.rstrip("/")
    nombre = ruta.rsplit("/", 1)[-1]
    return nombre or None


def descargar(url, destino="descargas"):
    """descarga `url` a un archivo NUEVO dentro de `destino`. Inocua.

    `destino` es una CARPETA (el alias de _RAICES_BUSQUEDA o una ruta
    entera, como abrir_archivo) y el nombre del archivo lo da la propia
    url (_nombre_de_url). SOLO crea un archivo NUEVO: si ya existe uno con
    ese nombre NO se sobrescribe (ok=False con su motivo), porque pisar
    datos de Edward no es "descargar". El contenido baja en trozos de
    _TROZO_DESCARGA a un ".parte" y recien cuando esta COMPLETO pasa al
    nombre definitivo con os.replace (escritura atomica, fundamento 25): una
    descarga cortada no deja un archivo final a medias. Si algo falla a
    medio camino, el ".parte" se limpia (es un archivo que creo ESTA
    llamada, no datos de nadie). Sin red o con error del servidor ->
    ok=False, sin reventar. NUNCA lanza excepciones.
    """
    if not isinstance(url, str) or not url.strip():
        return Resultado(ok=False, detalle="falta la url a descargar")
    nombre = _nombre_de_url(url)
    if nombre is None:
        return Resultado(
            ok=False, detalle="la url no trae nombre de archivo: " + url.strip())
    carpeta = _ruta_de_raiz(destino)
    if carpeta is None:
        return Resultado(
            ok=False, detalle="falta la carpeta donde guardar la descarga")
    ruta = os.path.join(carpeta, nombre)
    if os.path.exists(ruta):
        return Resultado(
            ok=False, detalle="ya existe " + ruta + "; no lo sobrescribo")
    temporal = ruta + ".parte"
    try:
        with urllib.request.urlopen(
                url.strip(), timeout=_ESPERA_RED) as respuesta:
            estado = getattr(respuesta, "status", 200)
            if estado != 200:
                return Resultado(
                    ok=False,
                    detalle="el servidor contesto %s para %s"
                    % (estado, url.strip()))
            with open(temporal, "wb") as archivo:
                while True:
                    trozo = respuesta.read(_TROZO_DESCARGA)
                    if not trozo:
                        break
                    archivo.write(trozo)
        os.replace(temporal, ruta)
    except (OSError, ValueError) as error:
        try:
            os.remove(temporal)
        except OSError:
            pass  # ni siquiera llego a crearse
        return Resultado(
            ok=False,
            detalle="no pude descargar %s: %s" % (url.strip(), error))
    return Resultado(ok=True, detalle="descargado: " + ruta)


def _resumen_de_json(dato):
    """resumen CORTO de un JSON leido, para poder decirlo en voz

    Un JSON de miles de lineas no se puede ni leer ni escuchar entero: se
    dicen sus claves (hasta 8) o su largo, y un valor suelto se corta.
    """
    if isinstance(dato, dict):
        claves = [str(clave) for clave in dato]
        texto = ", ".join(claves[:8])
        if len(claves) > 8:
            texto += ", y %d mas" % (len(claves) - 8)
        return "json con %d claves: %s" % (len(claves), texto)
    if isinstance(dato, list):
        return "json con %d elementos" % len(dato)
    texto = repr(dato)
    if len(texto) > 80:
        texto = texto[:77] + "..."
    return "json: " + texto


def leer_json(url):
    """lee un JSON de internet y devuelve un resumen corto. SOLO LEE. Inocua.

    json.loads sobre los bytes que devuelve la url (acepta bytes, y solo si
    son UTF-8/16/32 validos). Un JSON mal formado se traduce en ok=False con
    su motivo: NUNCA se lanza la excepcion hasta main.py (json levanta
    ValueError, que tambien cubre el UnicodeDecodeError). La url y la red
    van por _leer_http, con timeout y con su propio ok=False.
    """
    fallo, contenido = _leer_http(url)
    if fallo is not None:
        return fallo
    try:
        dato = json.loads(contenido)
    except ValueError:
        return Resultado(
            ok=False, detalle="eso no es un JSON valido: " + url.strip())
    return Resultado(ok=True, detalle=_resumen_de_json(dato))


def abrir_web(texto):
    """busca `texto` en Google y abre los resultados en el navegador. Inocua.

    Solo PIDE abrir una busqueda: no descarga ni instala nada. Es la
    variante de BUSQUEDA de abrir_url (que abre una url tal cual, y por eso
    completa "youtube.com" a https://): NO se funden las dos porque aqui el
    usuario no da ninguna url sino un texto, y fundirlas cambiaria el
    comportamiento de las ordenes viejas de abrir_url. El texto viaja con
    urllib.parse.quote, que escapa espacios y tildes ("%20", "%C3%A9").
    """
    if not isinstance(texto, str) or not texto.strip():
        return Resultado(ok=False, detalle="falta lo que hay que buscar")
    limpio = texto.strip()
    destino = _BUSQUEDA_GOOGLE + urllib.parse.quote(limpio)
    if not webbrowser.open(destino):
        return Resultado(
            ok=False, detalle="no hay navegador para buscar " + limpio)
    return Resultado(ok=True, detalle="buscando en google: " + limpio)


# SHQUERYRBINFO de shlobj_core.h: cbSize primero y los dos ULONGLONG
# alineados a 8 bytes, asi que la struct mide 24 en x64 (medido). Su tamano
# se guarda AQUI abajo por el mismo motivo que _TAMANO_INPUT: en las
# pruebas ctypes viene doblado y un sizeof en tiempo de ejecucion llegaria
# como un MagicMock
class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_uint64),
        ("i64NumItems", ctypes.c_uint64),
    ]


_TAMANO_SHQUERYRBINFO = ctypes.sizeof(_SHQUERYRBINFO)


def papelera_ver():
    """cuantos archivos y cuantos bytes hay en la papelera. SOLO LEE. Inocua.

    SHQueryRecycleBinW con pszRootPath = NULL (todas las unidades): solo
    CONSULTA el conteo y el peso; no borra absolutamente nada (el que borra
    de verdad es SHEmptyRecycleBinW, en papelera_vaciar, que es la
    DESTRUCTIVA). Medido en esta PC el 2026-10-21: 4 archivos, 76674415
    bytes (73.1 MB). Un codigo distinto de 0 se devuelve como su
    hexadecimal, nunca como una excepcion. NUNCA lanza excepciones.
    """
    info = _SHQUERYRBINFO()
    info.cbSize = _TAMANO_SHQUERYRBINFO
    funcion = ctypes.windll.shell32.SHQueryRecycleBinW
    funcion.argtypes = (ctypes.c_void_p, ctypes.POINTER(_SHQUERYRBINFO))
    funcion.restype = _HRESULT
    codigo = funcion(None, ctypes.byref(info))
    if codigo != 0:
        return _fallo("SHQueryRecycleBinW", codigo)
    if not info.i64NumItems:
        # el 0 de aca SI es un conteo medido de verdad (fundamento 34):
        # SHQueryRecycleBinW contesto 0 y la papelera viene vacia
        return Resultado(ok=True, detalle="la papelera de reciclaje esta vacia")
    return Resultado(
        ok=True,
        detalle="%d archivos en la papelera, %s"
        % (info.i64NumItems, _peso_legible(info.i64Size)))


def borrar_archivo(ruta):
    """borra UN archivo con os.remove. DESTRUCTIVA: es IRREVERSIBLE.

    SOLO se llega aqui desde ejecutar() con confirmado=True: el freno esta
    ANTES de esta linea. os.remove NO pasa por la papelera de reciclaje:
    el archivo se pierde para siempre, y por eso el detalle lo DICE en
    claro ("IRREVERSIBLE"). Send2Trash, la libreria que si lo mandaria a
    la papelera, NO esta instalada en esta PC (medido el 2026-10-21) y no
    se instala sola: instalarla es decision de Edward.
    Una ruta vacia o un archivo que no existe no borra NADA. Los
    DIRECTORIOS no se borran (os.remove los rechaza y eso se devuelve como
    ok=False): borrar carpetas enteras seria OTRA accion, todavia no
    existe. NUNCA lanza excepciones.
    """
    if not isinstance(ruta, str) or not ruta.strip():
        return Resultado(ok=False, detalle="falta la ruta del archivo")
    destino = ruta.strip()
    try:
        os.remove(destino)
    except FileNotFoundError:
        return Resultado(ok=False, detalle="no existe ese archivo: " + destino)
    except OSError as error:
        return Resultado(
            ok=False, detalle="no pude borrar %s: %s" % (destino, error))
    return Resultado(
        ok=True,
        detalle="borrado: %s (IRREVERSIBLE: no paso por la papelera)"
        % (destino,))


# ---------------------------------------------------------------------------
# FASE H3: MEMORIA QUE APRENDE. Tres acciones sobre la memoria del usuario:
# recordar (inocua), leer_memoria (inocua) y olvidar (DESTRUCTIVA).
#
# EL MOTOR NO SABE DONDE VIVE memoria.json NI COMO SE ESCRIBE: main.py le
# CONECTA su lector ESTRICTO y su escritor ATOMICO con conectar_memoria(). Es
# la MISMA escritura de la FASE B (bug B9: temporal + os.replace), no una
# segunda copia que serialice el archivo a mano: un bug de memoria no puede
# nacer de dos codigos distintos. Sin conexion las tres acciones se NIEGAN
# con ok=False y no tocan nada.
#
# LAS 17 NOTAS DE EDWARD: antes de escribir, recordar y olvidar leen el
# archivo con el lector ESTRICTO. Si memoria.json no es un json valido, o su
# "notas" no es una lista de textos, NO se escribe NADA y se devuelve ok=False
# con el motivo. Con el lector normal (el que tapa un archivo roto con los
# valores por defecto) guardar encima BORRARIA las notas para siempre.
# ---------------------------------------------------------------------------

# lector estricto y escritor atomico conectados por main.py; None hasta que
# main.py llame a conectar_memoria, asi el motor se sigue importando solo
_LEER_MEMORIA = None
_GUARDAR_MEMORIA = None


def conectar_memoria(leer, guardar):
    """conecta el lector ESTRICTO y el escritor ATOMICO de la memoria

    `leer` devuelve (datos, error): `datos` es el dict de la memoria y `error`
    el motivo por el que el archivo NO se puede usar (json roto, "notas" que
    no es una lista de textos). `guardar` recibe el dict COMPLETO y lo escribe
    con la escritura atomica de la FASE B. main.py los conecta una sola vez,
    al importarse.
    """
    global _LEER_MEMORIA, _GUARDAR_MEMORIA
    _LEER_MEMORIA = leer
    _GUARDAR_MEMORIA = guardar


def _memoria_actual():
    """(datos, error) de la memoria conectada, o error si no hay conexion"""
    if _LEER_MEMORIA is None or _GUARDAR_MEMORIA is None:
        return None, "la memoria no esta conectada (main.py no la conecto)"
    return _LEER_MEMORIA()


def _posicion_de_la_nota(notas, objetivo):
    """(posicion, motivo) de la nota a borrar: indice 0-based o el motivo

    `objetivo` es el NUMERO que se le muestra al modelo (empieza en 1) o el
    TEXTO exacto de la nota. El texto se compara ENTERO: borrar por un pedazo
    de texto podria llevarse una nota que Edward no pidio. Si el numero no
    existe o el texto no esta, no se borra nada y se dice por que.
    """
    if isinstance(objetivo, bool):
        # bool es int para Python: True seria la nota 1, y eso no se pidio
        return None, "no entendi que nota olvidar: %r" % (objetivo,)
    if isinstance(objetivo, int):
        numero = objetivo
    elif isinstance(objetivo, str):
        limpio = " ".join(objetivo.split())
        if not limpio:
            return None, "falta el numero o el texto de la nota a olvidar"
        if limpio.isdigit():
            numero = int(limpio)
        else:
            if limpio not in notas:
                return None, ("no encontre ninguna nota igual a %r: no borre "
                              "nada" % (limpio,))
            return notas.index(limpio), None
    else:
        return None, "no entendi que nota olvidar: %r" % (objetivo,)
    if numero < 1 or numero > len(notas):
        return None, ("no existe la nota %d: la memoria tiene %d notas"
                      % (numero, len(notas)))
    return numero - 1, None


def recordar(texto: str):
    """agrega UNA nota a la memoria del usuario. Inocua.

    Antes de escribir se lee el archivo con el lector ESTRICTO: si
    memoria.json no es un json valido, NO se toca y se devuelve ok=False con
    el motivo. Sin esa comprobacion, guardar encima de un archivo roto lo
    reemplazaria por los valores por defecto y las notas de Edward se
    perderian para siempre. Una nota que ya esta (mismo texto, espacios
    colapsados) NO se duplica: se devuelve ok=True diciendo que ya estaba.
    """
    datos, error = _memoria_actual()
    if error is not None:
        return Resultado(ok=False, detalle="no toque la memoria: " + error)
    if texto is None:
        return Resultado(ok=False, detalle="falta el texto de la nota")
    nota = " ".join(str(texto).split())
    if not nota:
        return Resultado(ok=False, detalle="la nota esta vacia: no guarde nada")
    notas = datos.get("notas") or []
    if nota in notas:
        return Resultado(
            ok=True,
            detalle="esa nota ya estaba en la memoria (%d notas); no la "
                    "duplique" % (len(notas),))
    datos["notas"] = list(notas) + [nota]
    try:
        _GUARDAR_MEMORIA(datos)
    except OSError as error_escritura:
        return Resultado(
            ok=False,
            detalle="no pude guardar la nota: %s" % (error_escritura,))
    return Resultado(
        ok=True,
        detalle="nota guardada: %s (la memoria tiene %d notas)"
                % (nota, len(datos["notas"])))


def olvidar(objetivo):
    """quita UNA nota de la memoria: se pierde para siempre. DESTRUCTIVA.

    `objetivo` es el NUMERO de la nota (el mismo que muestra leer_memoria,
    empezando en 1) o su texto exacto. SOLO se llega aqui desde ejecutar()
    con confirmado=True: el freno esta ANTES de esta linea. Si la nota no
    existe, o el archivo no es valido, ok=False y NO se escribe nada.
    """
    datos, error = _memoria_actual()
    if error is not None:
        return Resultado(ok=False, detalle="no toque la memoria: " + error)
    notas = datos.get("notas") or []
    posicion, motivo = _posicion_de_la_nota(notas, objetivo)
    if motivo is not None:
        return Resultado(ok=False, detalle=motivo)
    borrada = notas[posicion]
    datos["notas"] = [nota for indice, nota in enumerate(notas)
                      if indice != posicion]
    try:
        _GUARDAR_MEMORIA(datos)
    except OSError as error_escritura:
        return Resultado(
            ok=False,
            detalle="no pude olvidar la nota: %s" % (error_escritura,))
    return Resultado(
        ok=True,
        detalle="olvide la nota %d: %s (quedan %d notas)"
                % (posicion + 1, borrada, len(datos["notas"])))


def leer_memoria():
    """nombre del usuario y notas guardadas en la memoria. SOLO LEE. Inocua.

    Devuelve el nombre, cuantas notas hay y las notas NUMERADAS igual que el
    system prompt, para que el modelo pueda pedir olvidar una por su numero.
    Si el archivo no es valido lo dice con ok=False en vez de inventar una
    memoria vacia: un cero de medicion no puede pasar por un cero real
    (fundamento 34).
    """
    datos, error = _memoria_actual()
    if error is not None:
        return Resultado(ok=False, detalle="no pude leer la memoria: " + error)
    nombre = datos.get("nombre_usuario") or "desconocido"
    notas = datos.get("notas") or []
    if not notas:
        return Resultado(
            ok=True,
            detalle="nombre: %s; no hay ninguna nota guardada" % (nombre,))
    return Resultado(
        ok=True,
        detalle="nombre: %s; %d notas: %s" % (
            nombre, len(notas),
            "; ".join("Nota %d: %s" % (indice, nota)
                      for indice, nota in enumerate(notas, start=1))))


# acciones que este motor sabe ejecutar HOY (FASE E1, E2b, E2c, E2d, E2e, H2
# y H3), con su funcion. Quien agregue una accion NUEVA tiene que clasificarla:
# o la mete en DESTRUCTIVAS, o la declara inocua en la lista blanca INOCUAS de
# tests/test_motor.py. La prueba parametrizada sobre REGISTRO se deriva en
# TIEMPO DE CORRIDA y ROMPE si la clasificacion falta.
REGISTRO = {
    "volumen_leer": volumen_leer,
    "hora_actual": hora_actual,
    "papelera_vaciar": papelera_vaciar,
    # FASE E2b: archivos y programas (cinco acciones, mas la variante
    # destructiva de cerrar app)
    "abrir_archivo": abrir_archivo,
    "abrir_app": abrir_app,
    "abrir_url": abrir_url,
    "cerrar_app": cerrar_app,
    "cerrar_app_forzado": cerrar_app_forzado,
    "buscar_archivo": buscar_archivo,
    # FASE E2c, GRUPO 2: el equipo (volumen por pasos, brillo, energia,
    # bloqueo y captura de pantalla)
    "volumen_subir": volumen_subir,
    "volumen_bajar": volumen_bajar,
    "silenciar": silenciar,
    "brillo": brillo,
    "apagar": apagar,
    "reiniciar": reiniciar,
    "cancelar_apagado": cancelar_apagado,
    "bloquear": bloquear,
    "suspender": suspender,
    "capturar_pantalla": capturar_pantalla,
    # FASE H2: mira la pantalla para el modelo. INOCUA: solo lee la pantalla
    # y deja un PNG reducido en el temp; el motor sigue devolviendo TEXTO
    "mirar_pantalla": mirar_pantalla,
    # FASE E2d, GRUPOS 3 y 4: escribir y teclar (portapapeles + Ctrl+V,
    # SendInput y raton) e informacion del sistema (disco, CPU, RAM,
    # bateria, procesos y peso de carpetas). Las NUEVE son inocuas: o
    # escriben donde esta el cursor, o SOLO LEEN
    "escribir_texto": escribir_texto,
    "mandar_teclas": mandar_teclas,
    "mover_raton": mover_raton,
    "clic": clic,
    "espacio_disco": espacio_disco,
    "cpu_y_ram": cpu_y_ram,
    "bateria": bateria,
    "procesos": procesos,
    "peso_de": peso_de,
    # FASE E2e, GRUPO 5: red y descargas, mas papelera y borrado. Las OCHO
    # primeras son inocuas: LEEN el equipo o la red, o se niegan sin tocar
    # nada (wifi y bluetooth: ver sus docstrings). Lo que borra de verdad
    # es borrar_archivo, y esa si va en DESTRUCTIVAS
    "wifi_estado": wifi_estado,
    "wifi": wifi,
    "bluetooth_estado": bluetooth_estado,
    "bluetooth": bluetooth,
    "descargar": descargar,
    "leer_json": leer_json,
    "abrir_web": abrir_web,
    "papelera_ver": papelera_ver,
    "borrar_archivo": borrar_archivo,
    # FASE H3: la memoria que APRENDE. recordar y leer_memoria son INOCUAS
    # (agregar una nota no destruye nada y leer solo lee); olvidar pierde
    # algo que Edward dijo y por eso va en DESTRUCTIVAS
    "recordar": recordar,
    "leer_memoria": leer_memoria,
    "olvidar": olvidar,
}

# acciones destructivas: destruyen datos o apagan la maquina y por eso
# exigen confirmado=True. Sin confirmacion NO se ejecuta absolutamente nada.
# cerrar_app NO esta aqui: manda WM_CLOSE, que deja preguntar antes de cerrar.
# taskkill /F SI pierde lo no guardado, y por eso su nombre es el que esta.
# apagar y reiniciar son shutdown; suspender puede dejar lo no guardado a
# medio camino. cancelar_apagado NO esta: si frenara esa, el apagado ya
# mandado no tendria forma de deshacerse. borrar_archivo SI esta: os.remove
# no pasa por la papelera y el archivo se pierde para siempre.
# olvidar (FASE H3) SI esta: borra una nota que Edward dijo y no hay copia.
DESTRUCTIVAS = {"papelera_vaciar", "cerrar_app_forzado", "apagar", "reiniciar",
                "suspender", "borrar_archivo", "olvidar"}

# acciones que CAMBIAN la memoria del usuario (FASE H3): cuando el bucle
# agente ejecuta una de estas, REHACE el system prompt con la memoria nueva
# antes de la llamada siguiente. Guardar una nota y seguir con la foto vieja
# del prompt seria no haber aprendido nada.
CAMBIAN_MEMORIA = {"recordar", "olvidar"}


def ejecutar(accion: Accion, confirmado: bool = False) -> Resultado:
    """ejecuta UNA accion y devuelve su Resultado. NUNCA habla ni imprime.

    El orden de las lineas de abajo ES la seguridad: primero el freno de las
    destructivas (antes de NINGUNA llamada al sistema), despues la busqueda
    en el registro. Una accion no registrada devuelve ACCION_DESCONOCIDA y
    tampoco se ejecuta nada.
    """
    if accion.nombre in DESTRUCTIVAS and not confirmado:
        return Resultado(ok=False, detalle=REQUIERE_CONFIRMACION)
    funcion = REGISTRO.get(accion.nombre)
    if funcion is None:
        return Resultado(
            ok=False, detalle=ACCION_DESCONOCIDA + ": " + accion.nombre)
    return funcion(*accion.argumentos)
