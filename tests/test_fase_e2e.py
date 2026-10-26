# pruebas de la FASE E2e del asistente Jarvis (2026-10-21): GRUPO 5 (RED Y
# DESCARGAS) y PAPELERA Y BORRADO. NUEVE acciones clasificadas: las OCHO del
# bloque (wifi_estado, wifi, bluetooth_estado, descargar, leer_json,
# abrir_web, papelera_ver y borrar_archivo) mas bluetooth(), el interruptor
# que comparte el criterio MEDIDO con wifi().
#
# REGLA DE ORO: NADA se ejecuta en vivo. subprocess (netsh y powershell),
# ctypes (SHQueryRecycleBinW), os (remove, replace y join), webbrowser y el
# resto de sistemas van SIEMPRE reemplazados por dobles de solo registro, y
# TODO lo que habla con la red pasa por urllib.request.urlopen DOBLADO: ni
# una consulta real de wifi, ni un powershell, ni una descarga, ni un json,
# ni una busqueda de google, ni la papelera tocada de verdad, ni UN archivo
# borrado. Lo unico "real" de este archivo son los bytes FIXTURE de las
# salidas (cp850, medidos en esta PC el 2026-10-21) y un archivo temporal
# descartable que Python borra solo.
#
# Lo que mide este archivo:
#   1. las 9 acciones estan en REGISTRO y CLASIFICADAS (la proteccion de
#      tests/test_motor.py sigue viva);
#   2. wifi_estado lee netsh en cp850: un SSID acentuado ("Café Internet")
#      sale ENTERO, una salida truncada no revienta y sin wifi netsh cae
#      como ok=False;
#   3. wifi() y bluetooth() SIEMPRE se niegan: cero subprocess, cero os,
#      cero ctypes; nunca se anuncia un apagado que no paso;
#   4. bluetooth_estado cuenta y lista los dispositivos del CSV de
#      powershell, y sin hardware lo dice;
#   5. descargar (urlopen doblado): 200 crea el archivo NUEVO con su
#      contenido y su nombre de la url; 404, status 200!= y descarga
#      cortada -> ok=False sin dejar basura; un archivo existente NO se
#      sobrescribe;
#   6. leer_json: json valido -> resumen corto; json malo o url muerta ->
#      ok=False sin lanzar;
#   7. abrir_web arma la url de Google con quote (espacios y tildes);
#   8. papelera_ver suma el struct medido (4 archivos, 76674415 bytes =
#      73.1 MB) SOLO con SHQueryRecycleBinW, nunca con SHEmptyRecycleBinW;
#   9. borrar_archivo sin confirmar -> REQUIERE_CONFIRMACION y CERO
#      llamadas; confirmada SI borra y el detalle DICE que es irreversible;
#   10. las ordenes nuevas mapean a su accion, "vacia la papelera" pide
#       confirmacion y las viejas siguen iguales.

import contextlib
import ctypes
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# la lista blanca INOCUAS vive en test_motor: aqui se reutiliza para comprobar
# que las acciones nuevas estan clasificadas EN ELLA y no solo registradas
try:
    from tests.test_motor import INOCUAS
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_motor import INOCUAS

from main import motor
from main.reconocedor import Accion, interpretar

# las 9 acciones de esta fase, TODAS clasificadas: o SOLO LEEN (el equipo, la
# red o la papelera), o se niegan sin tocar nada, o borran con confirmacion
ACCIONES_NUEVAS = (
    "wifi_estado",
    "wifi",
    "bluetooth_estado",
    "bluetooth",
    "descargar",
    "leer_json",
    "abrir_web",
    "papelera_ver",
    "borrar_archivo",
)

# de ellas, estas DOS se niegan SIEMPRE: nunca encienden ni apagan nada
INTERRUPTORES = ("wifi", "bluetooth")

# lo unico que los interruptores pueden contestar (motor._SIN_PERMISOS,
# escrito a mano aqui para que un cambio de redaccion lo delate)
_SIN_PERMISOS = "requiere permisos de administrador; ejecuta Jarvis elevado"

# urls de las pruebas de red: la misma tabla se alimenta los dobles y las
# muestras, para que ninguna prueba se quede sin respuesta
URL_DESCARGA = "https://ejemplo.com/archivos/foto.png"
URL_JSON = "https://ejemplo.com/datos.json"

# salida REAL de "netsh wlan show interfaces" en esta PC el 2026-10-21, en
# bytes cp850 tal cual los devuelve la consola. El SSID acentuado es la
# trampa de la decodificacion: con el default saldria "Caf‚ Internet", y la
# clave "Señal" con tilde jamas casaria
_SALIDA_NETSH = (
    "\r\nHay 1 interfaz en el sistema: \r\n\r\n"
    "    Nombre                 : Wi-Fi\r\n"
    "    Descripción            : Realtek 8822CE Wireless LAN\r\n"
    "    Estado                 : conectado\r\n"
    "    SSID                   : Café Internet\r\n"
    "    BSSID                  : dc:71:37:27:d1:cb\r\n"
    "    Estado de la red hospedada: No disponible\r\n"
    "    Señal                           : 100% \r\n"
).encode("cp850")

# la MISMA lectura con Windows en ingles: las claves son otras y aqui se
# tienen que casar igual
_SALIDA_NETSH_EN = (
    "Interface name     : Wi-Fi\r\n"
    "State              : connected\r\n"
    "SSID               : MiWifi\r\n"
    "Signal             : 86%\r\n"
).encode("cp850")

# salida REAL de Get-PnpDevice -Class Bluetooth en esta PC (10 dispositivos;
# aqui van 3, con un aparato en Error y un nombre acentuado que solo se lee
# bien con cp850)
_SALIDA_BLUETOOTH = (
    '"Status","FriendlyName"\r\n'
    '"OK","Microsoft Bluetooth Enumerator"\r\n'
    '"OK","Teclado inalámbrico de Edward"\r\n'
    '"Error","Dispositivo roto"\r\n'
).encode("cp850")


@contextlib.contextmanager
def _doblado():
    """reemplaza TODO lo que el motor toca del sistema por dobles de registro

    Dentro de esta ventana no puede salir ni un netsh, ni un powershell, ni
    un SHQueryRecycleBinW, ni un os.remove, ni un navegador. Los ocho
    nombres se parchan sobre el MODULO motor (los mismos que main/motor.py
    importa a nivel de modulo). Dos detalles que NO son decoracion:
    ctypes.byref devuelve el objeto MISMO, para que el doble de
    SHQueryRecycleBinW llene el struct de verdad; y os.path.join une con
    barras invertidas, para que una ruta que pase por ahi se pueda leer.
    """
    dobles = {nombre: mock.MagicMock(name=nombre + "_falso")
              for nombre in ("ctypes", "subprocess", "os", "webbrowser",
                             "winreg", "win32api", "win32gui", "shutil")}
    dobles["ctypes"].byref.side_effect = lambda objeto: objeto
    dobles["os"].path.join.side_effect = \
        lambda *partes: "\\".join(str(parte) for parte in partes)
    with contextlib.ExitStack() as pila:
        for nombre, doble in dobles.items():
            pila.enter_context(mock.patch.object(motor, nombre, doble))
        yield dobles


def _sucias(dobles):
    """los dobles que quedaron con llamadas registradas (los limpios, no)"""
    return {nombre: list(doble.mock_calls)
            for nombre, doble in dobles.items() if doble.mock_calls}


def _subprocess_falso(dobles, salidas):
    """subprocess.run contesta con la salida que corresponda a cada comando

    `salidas` es nombre del comando ("netsh", "powershell") -> bytes tal
    cual los devolveria la consola (cp850). Un comando sin salida responde
    vacio; el returncode 0 es el exito.
    """
    def _corre(comando, **_argumentos):
        respuesta = mock.MagicMock(name="salida_falsa")
        respuesta.returncode = 0
        respuesta.stdout = salidas.get(comando[0], b"")
        return respuesta
    dobles["subprocess"].run.side_effect = _corre


def _papelera_llena(dobles, items=4, peso=76674415, codigo=0,
                    guardadas=None):
    """SHQueryRecycleBinW llena el struct con la medicion y devuelve codigo

    Los numeros por defecto son los MEDIDOS en esta PC el 2026-10-21:
    4 archivos y 76674415 bytes (73.1 MB). `guardadas` acumula el struct
    (si se le pasa una lista) para poder mirar su cbSize despues.
    """
    def _llena(_raiz, info):
        if guardadas is not None:
            guardadas.append(info)
        info.i64NumItems = items
        info.i64Size = peso
        return codigo
    dobles["ctypes"].windll.shell32.SHQueryRecycleBinW.side_effect = _llena


def _respuesta(conteudo, estado=200):
    """respuesta http falsa: su `status` fijo y un read() por cada trozo

    `conteudo` es una LISTA de trozos: read() los suelta en orden y despues
    b"" para cerrar la lectura. El __enter__ se apunta a la MISMA
    respuesta: con un MagicMock a secas, "with urlopen(...) as r" daria un
    hijo distinto y los trozos se perderian.
    """
    respuesta = mock.MagicMock(name="respuesta_http")
    respuesta.status = estado
    respuesta.read.side_effect = list(conteudo) + [b""]
    respuesta.__enter__.return_value = respuesta
    return respuesta


@contextlib.contextmanager
def _urlopen_con(respuestas):
    """parcha urllib.request.urlopen para que responda segun la url

    `respuestas` es url -> lista de trozos. SIN RED en toda la suite: cada
    prueba que toca descargar o leer_json pasa por aqui.
    """
    def _abre(url, timeout=None):
        return _respuesta(respuestas[url.strip()])
    with mock.patch.object(motor.urllib.request, "urlopen",
                           side_effect=_abre) as doble:
        yield doble


@contextlib.contextmanager
def _urlopen_devuelve(respuesta):
    """parcha urllib.request.urlopen para que devuelva SIEMPRE esa respuesta"""
    with mock.patch.object(motor.urllib.request, "urlopen",
                           return_value=respuesta) as doble:
        yield doble


@contextlib.contextmanager
def _urlopen_que_falla(excepcion):
    """parcha urllib.request.urlopen para que SIEMPRE lance esa excepcion"""
    with mock.patch.object(motor.urllib.request, "urlopen",
                           side_effect=excepcion) as doble:
        yield doble


class TestLasNueveClasificadas(unittest.TestCase):
    """las 9 estan en REGISTRO y CLASIFICADAS: la proteccion sigue viva"""

    def test_las_nueve_estan_en_el_registro(self):
        """sin registrar, ejecutar() devolveria ACCION_DESCONOCIDA

        main.py NO se toco en esta fase: por eso los nombres del
        reconocedor tienen que ser CLAVES del REGISTRO tal cual.
        """
        faltantes = [nombre for nombre in ACCIONES_NUEVAS
                     if nombre not in motor.REGISTRO]
        self.assertEqual(
            faltantes, [],
            "estas acciones no estan en REGISTRO: %s" % faltantes)

    def test_las_nueve_estan_clasificadas(self):
        """cada una esta en INOCUAS o en DESTRUCTIVAS, y NUNCA en las dos

        Es la misma regla que la parametrizada de tests/test_motor.py,
        repetida aqui para que el fallo diga exactamente cual de las 9 de
        esta fase quedo sin clasificar.
        """
        for nombre in ACCIONES_NUEVAS:
            with self.subTest(nombre=nombre):
                if nombre in motor.DESTRUCTIVAS:
                    self.assertNotIn(
                        nombre, INOCUAS,
                        "%r esta en DESTRUCTIVAS y ademas en INOCUAS: se "
                        "ejecutaria sin confirmacion" % (nombre,))
                else:
                    self.assertIn(
                        nombre, INOCUAS,
                        "%r esta en REGISTRO pero NO esta en la lista blanca "
                        "INOCUAS: la parametrizada de test_motor la rechaza"
                        % (nombre,))
        self.assertIn(
            "borrar_archivo", motor.DESTRUCTIVAS,
            "borrar_archivo usa os.remove, que NO pasa por la papelera: "
            "sin estar en DESTRUCTIVAS se ejecutaria sin confirmacion")
        for nombre in ("wifi_estado", "bluetooth_estado", "descargar",
                       "leer_json", "abrir_web", "papelera_ver"):
            self.assertNotIn(
                nombre, motor.DESTRUCTIVAS,
                "%r solo LEE y quedo en DESTRUCTIVAS: pediria confirmacion "
                "para una lectura" % (nombre,))

    def test_las_inocuas_ejecutan_sin_confirmacion_ni_reventar(self):
        """las 6 con final feliz van por ejecutar() con confirmado=False

        Los interruptores (wifi, bluetooth) quedan FUERA: su final es
        siempre ok=False y eso se prueba aparte, en TestInterruptores.
        borrar_archivo tampoco entra: es DESTRUCTIVA y su freno se prueba
        en TestBorrarArchivo. Todo ocurre con los dobles puestos.
        """
        con_final_feliz = [nombre for nombre in ACCIONES_NUEVAS
                           if nombre not in INTERRUPTORES
                           and nombre != "borrar_archivo"]
        self.assertEqual(
            len(con_final_feliz), 6,
            "las acciones con final feliz cambiaron: %r" % (con_final_feliz,))
        with tempfile.TemporaryDirectory() as tempdir:
            muestras = dict(_MUESTRAS, descargar=(URL_DESCARGA, tempdir))
            with _urlopen_con({URL_DESCARGA: [b"contenido de prueba"],
                               URL_JSON: [b'{"a": 1}']}) as urlopen, \
                    _doblado() as dobles:
                _subprocess_falso(
                    dobles, {"netsh": _SALIDA_NETSH,
                             "powershell": _SALIDA_BLUETOOTH})
                _papelera_llena(dobles)
                dobles["webbrowser"].open.return_value = True
                dobles["os"].path.exists.return_value = False

                for nombre in con_final_feliz:
                    with self.subTest(nombre=nombre):
                        resultado = motor.ejecutar(
                            Accion(nombre, muestras.get(nombre, ())),
                            confirmado=False)
                        self.assertNotEqual(
                            resultado.detalle, motor.REQUIERE_CONFIRMACION,
                            "%r pide confirmacion siendo inocua" % (nombre,))
                        self.assertTrue(
                            resultado.ok,
                            "%r no ejecuto: %r" % (nombre, resultado.detalle))

            urlopen.assert_called()
        self.assertTrue(_sucias(dobles),
                        "ninguna de las 6 toco siquiera a los dobles")


# argumentos de MUESTRA de cada accion: sin ellos, una accion CON argumentos
# daria TypeError en vez de probar su comportamiento. descargar lleva SU
# carpeta de la prueba (tempdir), que se agrega dentro del test
_MUESTRAS = {
    "wifi": ("apagar",),
    "bluetooth": ("encender",),
    "leer_json": (URL_JSON,),
    "abrir_web": ("como instalar python",),
}


class TestWifiEstado(unittest.TestCase):
    """netsh wlan show interfaces: cp850, claves en espanol o en ingles"""

    def test_decodifica_cp850_y_saca_estado_red_y_senal(self):
        """LA PRUEBA CENTRAL: "Café Internet" sale ENTERO, byte a byte

        Con la decodificacion por defecto saldria "Caf‚ Internet" (cp850
        0x82 leido como cp1252) y el acento estaria mordido.
        """
        with _doblado() as dobles:
            _subprocess_falso(dobles, {"netsh": _SALIDA_NETSH})
            resultado = motor.ejecutar(Accion("wifi_estado", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            resultado.detalle,
            "wifi: estado conectado, red Café Internet, señal 100%",
            "salio mal decodificado o mal parseado: %r" % (resultado.detalle,))
        dobles["subprocess"].run.assert_called_once_with(
            ["netsh", "wlan", "show", "interfaces"], check=True,
            capture_output=True)

    def test_las_claves_en_ingles_tambien_casan(self):
        """Windows en ingles: state/signal en vez de estado/senal. Los
        nombres de los campos salen SIEMPRE en espanol, que es lo que
        habla Jarvis; lo que cambia con el idioma es la CLAVE de netsh"""
        with _doblado() as dobles:
            _subprocess_falso(dobles, {"netsh": _SALIDA_NETSH_EN})
            resultado = motor.ejecutar(Accion("wifi_estado", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            resultado.detalle,
            "wifi: estado connected, red MiWifi, señal 86%")

    def test_una_salida_truncada_no_revienta_y_devuelve_lo_que_hay(self):
        """corte a mitad de linea y sin ssid: se dice lo que haya, sin crash"""
        with _doblado() as dobles:
            _subprocess_falso(
                dobles, {"netsh": b"Nombre : Wi-Fi\r\n    Estado : conec"})
            resultado = motor.ejecutar(Accion("wifi_estado", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(resultado.detalle, "wifi: estado conec")

    def test_sin_wifi_o_sin_campos_es_ok_false_nunca_una_excepcion(self):
        """netsh sin interfaz wireless sale con error, y una salida sin
        ningun campo conocido tampoco es un exito inventado"""
        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = \
                motor._ERROR_SUBPROCESO(1, ["netsh"])
            resultado = motor.ejecutar(Accion("wifi_estado", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("netsh", resultado.detalle)

        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = OSError("no existe")
            resultado = motor.ejecutar(Accion("wifi_estado", ()))
        self.assertFalse(resultado.ok, resultado.detalle)

        for salida in (b"", b"Hay 0 interfaces en el sistema.\r\n"):
            with _doblado() as dobles, self.subTest(salida=salida):
                _subprocess_falso(dobles, {"netsh": salida})
                resultado = motor.ejecutar(Accion("wifi_estado", ()))
                self.assertFalse(resultado.ok, resultado.detalle)
                self.assertIn("ningun dato", resultado.detalle)

    def test_una_clave_que_empieza_igual_no_roba_el_estado(self):
        """"Estado de la red hospedada" NO es "Estado": comparar con
        startswith devolveria 'No disponible' como si fuera el wifi"""
        with _doblado() as dobles:
            _subprocess_falso(
                dobles,
                {"netsh": b"Estado de la red hospedada: No disponible\r\n"})
            resultado = motor.ejecutar(Accion("wifi_estado", ()))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertNotIn("hospedada", resultado.detalle)


class TestInterruptores(unittest.TestCase):
    """wifi() y bluetooth() SIEMPRE se niegan: nunca se anuncia un cambio"""

    def test_se_niegan_sin_tocar_absolutamente_nada(self):
        """ni netsh, ni powershell, ni os, ni ctypes, ni navegador: cero

        Es la segunda mitad de la trampa del apagado de wifi: no basta con
        devolver ok=False, hay que probar que NINGUN sistema fue llamado.
        """
        for nombre in INTERRUPTORES:
            for intencion in ("apagar", "encender"):
                with _doblado() as dobles, \
                        self.subTest(nombre=nombre, intencion=intencion):
                    resultado = motor.ejecutar(
                        Accion(nombre, (intencion,)))
                    self.assertFalse(
                        resultado.ok,
                        "%r dijo ok=True siendo incapaz de tocar la radio"
                        % (nombre,))
                    self.assertEqual(
                        resultado.detalle, _SIN_PERMISOS,
                        "%r cambio el mensaje de los permisos: %r"
                        % (nombre, resultado.detalle))
                    self.assertEqual(
                        _sucias(dobles), {},
                        "%r (%s) toco estos sistemas sin permisos: %r"
                        % (nombre, intencion, _sucias(dobles)))

    def test_sin_argumentos_tambien_se_niegan(self):
        """ejecutar() puede llegar sin argumentos: nunca TypeError"""
        for nombre in INTERRUPTORES:
            with _doblado(), self.subTest(nombre=nombre):
                resultado = motor.ejecutar(Accion(nombre))
                self.assertFalse(resultado.ok, resultado.detalle)
                self.assertEqual(resultado.detalle, _SIN_PERMISOS)


class TestBluetoothEstado(unittest.TestCase):
    """powershell Get-PnpDevice: lista, cuenta y dice el Error"""

    def test_lista_los_dispositivos_y_cuenta_los_que_fallan(self):
        with _doblado() as dobles:
            _subprocess_falso(dobles, {"powershell": _SALIDA_BLUETOOTH})
            resultado = motor.ejecutar(Accion("bluetooth_estado", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("3 dispositivos bluetooth (1 con problema)",
                      resultado.detalle)
        # el nombre acentuado solo se lee bien decodificando la consola en
        # cp850: con el default saldria mordido
        self.assertIn("Teclado inalámbrico de Edward", resultado.detalle)
        self.assertIn("Dispositivo roto", resultado.detalle)
        comando = dobles["subprocess"].run.call_args.args[0]
        self.assertIn("Get-PnpDevice -Class Bluetooth", comando[4],
                      "el comando no es Get-PnpDevice: %r" % (comando,))

    def test_sin_dispositivos_lo_dice_como_lectura_y_no_como_fallo(self):
        """equipo sin bluetooth: la consulta salio bien, lo que no hay es
        hardware, y eso NO es un error (fundamento 34: sin datos se DICE)"""
        with _doblado() as dobles:
            _subprocess_falso(
                dobles, {"powershell": b'"Status","FriendlyName"\r\n'})
            resultado = motor.ejecutar(Accion("bluetooth_estado", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(resultado.detalle, "no hay dispositivos bluetooth")

    def test_la_lista_viene_acotada(self):
        filas = "".join('"OK","dispositivo %d"\r\n' % numero
                        for numero in range(7))
        with _doblado() as dobles:
            _subprocess_falso(
                dobles, {"powershell": ('"Status","FriendlyName"\r\n'
                                        + filas).encode("cp850")})
            resultado = motor.ejecutar(Accion("bluetooth_estado", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("7 dispositivos bluetooth", resultado.detalle)
        self.assertEqual(
            resultado.detalle.count("dispositivo "), motor._TOPE_BLUETOOTH,
            "no salio exactamente el tope de la lista")
        self.assertIn("; y 2 mas", resultado.detalle)

    def test_powershell_que_no_corre_es_ok_false(self):
        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = \
                motor._ERROR_SUBPROCESO(1, ["powershell"])
            resultado = motor.ejecutar(Accion("bluetooth_estado", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("powershell", resultado.detalle)

        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = OSError("no existe")
            resultado = motor.ejecutar(Accion("bluetooth_estado", ()))
        self.assertFalse(resultado.ok, resultado.detalle)


class TestDescargar(unittest.TestCase):
    """urlopen doblado, con el archivo SÍ real dentro de un tempdir"""

    def test_una_descarga_ok_crea_el_archivo_nuevo_con_su_contenido(self):
        """el nombre sale de la url y el ".parte" NO queda a medias"""
        with tempfile.TemporaryDirectory() as tempdir:
            with _urlopen_con({URL_DESCARGA: [b"hola ", b"mundo"]}) as doble:
                resultado = motor.ejecutar(
                    Accion("descargar", (URL_DESCARGA, tempdir)))

            self.assertTrue(resultado.ok, resultado.detalle)
            ruta = os.path.join(tempdir, "foto.png")
            self.assertTrue(os.path.exists(ruta),
                            "no quedo el archivo final: %r"
                            % (resultado.detalle,))
            with open(ruta, "rb") as archivo:
                self.assertEqual(archivo.read(), b"hola mundo")
            self.assertEqual(
                sorted(os.listdir(tempdir)), ["foto.png"],
                "quedo basura (un .parte) junto al archivo")
            # el timeout viaja SIEMPRE: un servidor mudo no puede colgar a
            # main.py
            self.assertEqual(
                doble.call_args.kwargs, {"timeout": motor._ESPERA_RED})
            self.assertEqual(
                resultado.detalle, "descargado: " + ruta)

    def test_un_404_es_ok_false_sin_crear_nada(self):
        """urlopen levanta HTTPError 404 (que ES un OSError) y aqui no hay
        ni un .parte, ni un archivo final, ni una excepcion hasta main.py"""
        error = urllib.error.HTTPError(
            URL_DESCARGA, 404, "Not Found", {}, None)
        with tempfile.TemporaryDirectory() as tempdir:
            with _urlopen_que_falla(error) as doble:
                resultado = motor.ejecutar(
                    Accion("descargar", (URL_DESCARGA, tempdir)))

            self.assertFalse(resultado.ok, resultado.detalle)
            self.assertIn("404", resultado.detalle)
            self.assertEqual(os.listdir(tempdir), [],
                             "dejo archivos tras el 404")
            doble.assert_called_once()

    def test_una_respuesta_que_no_es_200_no_descarga(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with _urlopen_devuelve(_respuesta([b"nada"], estado=503)):
                resultado = motor.ejecutar(
                    Accion("descargar", (URL_DESCARGA, tempdir)))

            self.assertFalse(resultado.ok, resultado.detalle)
            self.assertIn("503", resultado.detalle)
            self.assertEqual(os.listdir(tempdir), [])

    def test_no_sobrescribe_un_archivo_existente(self):
        """pisar un archivo de Edward no es "descargar": ni un byte se
        toca y ni siquiera se abre la url"""
        with tempfile.TemporaryDirectory() as tempdir:
            ruta = os.path.join(tempdir, "foto.png")
            with open(ruta, "wb") as archivo:
                archivo.write(b"ORIGINAL")
            with _urlopen_devuelve(_respuesta([b"nuevo"])) as doble:
                resultado = motor.ejecutar(
                    Accion("descargar", (URL_DESCARGA, tempdir)))

            self.assertFalse(resultado.ok, resultado.detalle)
            self.assertIn("ya existe", resultado.detalle)
            with open(ruta, "rb") as archivo:
                self.assertEqual(archivo.read(), b"ORIGINAL",
                                 "sobrescribio el archivo existente")
            doble.assert_not_called()

    def test_una_url_sin_nombre_de_archivo_no_descarga(self):
        with tempfile.TemporaryDirectory() as tempdir:
            with _urlopen_devuelve(_respuesta([b"x"])) as doble:
                resultado = motor.ejecutar(
                    Accion("descargar", ("https://ejemplo.com/", tempdir)))

            self.assertFalse(resultado.ok, resultado.detalle)
            self.assertIn("nombre de archivo", resultado.detalle)
            doble.assert_not_called()

    def test_una_descarga_cortada_limpia_el_parte(self):
        """read() que levanta a mitad de transferencia: ok=False y ni el
        .parte ni el archivo final quedan en la carpeta"""
        with tempfile.TemporaryDirectory() as tempdir:
            with _urlopen_devuelve(
                    _respuesta([b"mitad", OSError("corto")])):
                resultado = motor.ejecutar(
                    Accion("descargar", (URL_DESCARGA, tempdir)))

            self.assertFalse(resultado.ok, resultado.detalle)
            self.assertIn("no pude descargar", resultado.detalle)
            self.assertEqual(os.listdir(tempdir), [],
                             "dejo un .parte o un archivo a medias")

    def test_sin_url_no_llama_a_nada(self):
        for muestra in ("", "   ", None):
            with _doblado() as dobles, self.subTest(url=muestra):
                resultado = motor.ejecutar(
                    Accion("descargar", (muestra,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["subprocess"].run.assert_not_called()

    def test_por_defecto_se_va_a_descargas(self):
        """sin carpeta en la frase, el MOTOR pone su alias de siempre"""
        with tempfile.TemporaryDirectory() as tempdir:
            # la carpeta Descargas TIENE que existir: el archivo se abre de
            # verdad (solo el os del motor viene doblado)
            os.makedirs(os.path.join(tempdir, "Downloads"))
            with _doblado() as dobles:
                dobles["os"].path.expanduser.side_effect = \
                    lambda texto: tempdir
                dobles["os"].path.exists.return_value = False
                with _urlopen_con({URL_DESCARGA: [b"x"]}):
                    resultado = motor.ejecutar(
                        Accion("descargar", (URL_DESCARGA,)))

            self.assertTrue(resultado.ok, resultado.detalle)
            self.assertEqual(
                dobles["os"].path.join.call_args.args,
                (os.path.join(tempdir, "Downloads"), "foto.png"),
                "por defecto no se fue a la carpeta Descargas")


class TestLeerJson(unittest.TestCase):
    """urlopen doblado: json bueno -> resumen; malo o muerto -> ok=False"""

    def test_un_json_ok_da_un_resumen_corto(self):
        with _urlopen_con({URL_JSON: [b'{"nombre": "Jarvis", "pi": 3.14}']}):
            resultado = motor.ejecutar(Accion("leer_json", (URL_JSON,)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("json con 2 claves", resultado.detalle)
        self.assertIn("nombre", resultado.detalle)
        self.assertLess(len(resultado.detalle), 100,
                        "el resumen no es corto: %r" % (resultado.detalle,))

    def test_una_lista_dice_su_largo(self):
        with _urlopen_con({URL_JSON: [b"[1, 2, 3]"]}):
            resultado = motor.ejecutar(Accion("leer_json", (URL_JSON,)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(resultado.detalle, "json con 3 elementos")

    def test_un_json_malo_es_ok_false_sin_lanzar(self):
        with _urlopen_con({URL_JSON: [b"no es json {"]}):
            resultado = motor.ejecutar(Accion("leer_json", (URL_JSON,)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("JSON valido", resultado.detalle)

    def test_una_url_muerta_es_ok_false(self):
        with _urlopen_que_falla(urllib.error.URLError("sin red")):
            resultado = motor.ejecutar(Accion("leer_json", (URL_JSON,)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no pude leer", resultado.detalle)

    def test_un_404_es_ok_false(self):
        error = urllib.error.HTTPError(URL_JSON, 404, "Not Found", {}, None)
        with _urlopen_que_falla(error):
            resultado = motor.ejecutar(Accion("leer_json", (URL_JSON,)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("404", resultado.detalle)

    def test_sin_url_no_llama_a_nada(self):
        for muestra in ("", "   ", None):
            with _urlopen_devuelve(_respuesta([b"{}"])) as doble, \
                    self.subTest(url=muestra):
                resultado = motor.ejecutar(Accion("leer_json", (muestra,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                self.assertIn("falta la url", resultado.detalle)
                doble.assert_not_called()


class TestAbrirWeb(unittest.TestCase):
    """webbrowser con la url de Google armada con quote"""

    def test_arma_la_url_de_busqueda_con_los_espacios_escapados(self):
        with _doblado() as dobles:
            dobles["webbrowser"].open.return_value = True
            resultado = motor.ejecutar(
                Accion("abrir_web", ("como instalar python",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        dobles["webbrowser"].open.assert_called_once_with(
            "https://www.google.com/search?q=como%20instalar%20python")

    def test_las_tildes_se_escapan_con_quote(self):
        """quote usa UTF-8: "café" -> "caf%C3%A9", que Google entiende"""
        with _doblado() as dobles:
            dobles["webbrowser"].open.return_value = True
            motor.ejecutar(Accion("abrir_web", ("buscar café",)))

        destinos = [llamada.args[0]
                    for llamada in dobles["webbrowser"].open.call_args_list]
        self.assertEqual(
            destinos,
            ["https://www.google.com/search?q=buscar%20caf%C3%A9"])

    def test_sin_navegador_no_es_exito(self):
        """webbrowser.open devuelve False -> ok=False, sin anunciar nada"""
        with _doblado() as dobles:
            dobles["webbrowser"].open.return_value = False
            resultado = motor.ejecutar(Accion("abrir_web", ("algo",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("navegador", resultado.detalle)

    def test_sin_texto_no_abre_nada(self):
        for muestra in ("", "   ", None):
            with _doblado() as dobles, self.subTest(texto=muestra):
                resultado = motor.ejecutar(Accion("abrir_web", (muestra,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["webbrowser"].open.assert_not_called()


class TestPapeleraVer(unittest.TestCase):
    """SHQueryRecycleBinW: solo LEE el conteo y el peso"""

    def test_suma_lo_medido_y_nunca_llama_a_vaciar(self):
        """4 archivos y 76674415 bytes = los MEDIDOS en esta PC"""
        with _doblado() as dobles:
            guardadas = []
            _papelera_llena(dobles, guardadas=guardadas)
            resultado = motor.ejecutar(Accion("papelera_ver", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            resultado.detalle, "4 archivos en la papelera, 73.1 MB")
        # el struct viene con su cbSize puesto (sin eso Windows lo rechaza)
        self.assertEqual(
            guardadas[0].cbSize, motor._TAMANO_SHQUERYRBINFO,
            "no se puso el cbSize del struct")
        # SOLO consulta: SHEmptyRecycleBinW (la que borra) ni se acerco
        dobles["ctypes"].windll.shell32.SHEmptyRecycleBinW \
            .assert_not_called()

    def test_la_struct_mide_lo_que_windows_exige(self):
        """x64: DWORD + relleno + dos ULONGLONG = 24 bytes (medido). Un
        struct corrido daria un conteo y un peso inventados"""
        self.assertEqual(
            ctypes.sizeof(motor._SHQUERYRBINFO), 24,
            "SHQUERYRBINFO no mide lo que Windows exige en x64")

    def test_una_papelera_vacia_se_dice_tal_cual(self):
        """el 0 de a SI es un conteo medido, no un dato que falto"""
        with _doblado() as dobles:
            _papelera_llena(dobles, items=0, peso=0)
            resultado = motor.ejecutar(Accion("papelera_ver", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(resultado.detalle, "la papelera de reciclaje esta vacia")
        self.assertNotIn("0.0", resultado.detalle,
                         "invento un peso de 0.0 KB")

    def test_un_fallo_de_la_api_es_ok_false_con_su_hexadecimal(self):
        with _doblado() as dobles:
            _papelera_llena(dobles, codigo=5)
            resultado = motor.ejecutar(Accion("papelera_ver", ()))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertEqual(
            resultado.detalle, "SHQueryRecycleBinW fallo (0x00000005)")


class TestBorrarArchivo(unittest.TestCase):
    """os.remove: destructiva, con su freno y con su aviso de irreversible"""

    def test_sin_confirmar_cero_llamadas(self):
        """el freno de ejecutar() va ANTES de os.remove: ni una llamada"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("borrar_archivo", ("C:/algo.txt",)), confirmado=False)

        self.assertEqual(resultado.detalle, motor.REQUIERE_CONFIRMACION)
        self.assertFalse(resultado.ok)
        self.assertEqual(
            _sucias(dobles), {},
            "sin confirmar, borrar_archivo toco estos sistemas: %r"
            % (_sucias(dobles),))

    def test_confirmada_si_borra_y_lo_dice_claro(self):
        """confirmada SI llama a os.remove, y el detalle DICE que es
        irreversible: la papelera no se toco"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("borrar_archivo", ("C:/algo.txt",)), confirmado=True)

        self.assertTrue(resultado.ok, resultado.detalle)
        dobles["os"].remove.assert_called_once_with("C:/algo.txt")
        self.assertIn("IRREVERSIBLE", resultado.detalle)
        self.assertIn("papelera", resultado.detalle)
        self.assertIn("C:/algo.txt", resultado.detalle)

    def test_un_archivo_que_no_existe_es_ok_false(self):
        """FileNotFoundError es un OSError: se traduce, no se lanza"""
        with _doblado() as dobles:
            dobles["os"].remove.side_effect = FileNotFoundError("no hay")
            resultado = motor.ejecutar(
                Accion("borrar_archivo", ("fantasma.txt",)), confirmado=True)

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no existe", resultado.detalle)

    def test_un_error_cualquiera_es_ok_false(self):
        with _doblado() as dobles:
            dobles["os"].remove.side_effect = PermissionError("denegado")
            resultado = motor.ejecutar(
                Accion("borrar_archivo", ("blindo.txt",)), confirmado=True)

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no pude borrar", resultado.detalle)

    def test_sin_ruta_no_borra_nada(self):
        for muestra in ("", "   ", None):
            with _doblado() as dobles, self.subTest(ruta=muestra):
                resultado = motor.ejecutar(
                    Accion("borrar_archivo", (muestra,)), confirmado=True)
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["os"].remove.assert_not_called()

    def test_la_prueba_real_de_descartables_no_toca_nada_de_edward(self):
        """descarga de seguridad del comportamiento REAL de os.remove, con
        un archivo temporal descartable CREADO por esta prueba: nadie mas
        se borra. (os NO viene doblado aqui, a proposito.)"""
        with tempfile.NamedTemporaryFile(
                suffix=".txt", delete=False) as temporal:
            temporal.write(b"descartable")
            ruta = temporal.name
        try:
            resultado = motor.ejecutar(
                Accion("borrar_archivo", (ruta,)), confirmado=True)
            self.assertTrue(resultado.ok, resultado.detalle)
            self.assertFalse(os.path.exists(ruta),
                             "el archivo temporal sigue ahi")
        except Exception:
            # por si algo fallara antes, el descartable se limpia igual
            if os.path.exists(ruta):
                os.remove(ruta)
            raise


class TestOrdenesE2e(unittest.TestCase):
    """cada frase nueva mapea a su accion correcta, sin ejecutar nada"""

    def test_las_ordenes_nuevas_mapean_a_su_accion(self):
        casos = (
            ("¿como esta el wifi?", "wifi_estado", ()),
            ("como esta el wifi", "wifi_estado", ()),
            ("estado del wifi", "wifi_estado", ()),
            ("apaga el wifi", "wifi", ("apagar",)),
            ("enciende el wifi", "wifi", ("encender",)),
            ("¿como esta el bluetooth?", "bluetooth_estado", ()),
            ("estado del bluetooth", "bluetooth_estado", ()),
            ("apaga el bluetooth", "bluetooth", ("apagar",)),
            ("enciende el bluetooth", "bluetooth", ("encender",)),
            ("descarga esta imagen a documentos",
             "descargar", ("esta imagen", "documentos")),
            ("descarga el informe en documentos",
             "descargar", ("el informe", "documentos")),
            ("descarga https://ejemplo.com/foto.png",
             "descargar", ("https://ejemplo.com/foto.png",)),
            ("lee este json", "leer_json", ("este json",)),
            ("busca en google como instalar python",
             "abrir_web", ("como instalar python",)),
            ("busca en google recetas de pollo",
             "abrir_web", ("recetas de pollo",)),
            ("¿cuantos archivos tiene la papelera?", "papelera_ver", ()),
            ("vacia la papelera", "papelera_vaciar", ()),
            ("borra la papelera", "papelera_vaciar", ()),
            ("borra este archivo", "borrar_archivo", ("este archivo",)),
            ("borra el archivo informe.txt",
             "borrar_archivo", ("el archivo informe.txt",)),
        )
        for texto, nombre, argumentos in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r devolvio %d acciones y tiene que devolver 1: %r"
                    % (texto, len(acciones), acciones))
                self.assertEqual(
                    acciones[0].nombre, nombre,
                    "%r debio ser %r y fue %r"
                    % (texto, nombre, acciones[0].nombre))
                self.assertEqual(
                    acciones[0].argumentos, argumentos,
                    "%r debio llevar %r y llevo %r"
                    % (texto, argumentos, acciones[0].argumentos))
                # main.py no se toco: sin clave del REGISTRO la frase se
                # caeria a la cadena if/elif y nunca llegaria al motor
                self.assertIn(
                    acciones[0].nombre, motor.REGISTRO,
                    "%r no es una accion que el motor sepa ejecutar"
                    % (acciones[0].nombre,))

    def test_borra_la_papelera_gana_sobre_borra(self):
        """"borra la papelera" empieza como la orden libre "borra" y como
        la fija "borra la papelera": a igual indice gana la PRIMERA de la
        tabla, y ahi esta la fija. Sin ese orden, la papelera SE VACIARIA
        como si el archivo a borrar fuera literalmente "la papelera" """
        acciones = interpretar("borra la papelera")
        self.assertEqual(acciones[0].nombre, "papelera_vaciar")

    def test_vacia_la_papelera_es_destructiva_y_pide_confirmacion(self):
        """es destructiva en el MOTOR: sin confirmado=True no se vacia nada
        (main.py es el que pregunta en voz cuando ve este detalle)"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("papelera_vaciar", ()))

        self.assertEqual(resultado.detalle, motor.REQUIERE_CONFIRMACION)
        self.assertEqual(
            _sucias(dobles), {},
            "papelera_vaciar sin confirmar toco estos sistemas: %r"
            % (_sucias(dobles),))

    def test_sin_argumento_detras_se_va_a_la_ia(self):
        """una orden libre sin nada detras no dispara: no se adivina nada"""
        for texto in ("descarga", "borra"):
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones],
                    ["consultar_ia"],
                    "%r disparo una accion sin argumento: %r"
                    % (texto, acciones))

    def test_las_ordenes_viejas_siguen_iguales(self):
        """las nuevas no se comieron el desempate ni el argumento de nadie"""
        casos = (
            ("busca mi factura en documentos",
             "buscar_archivo", ("mi factura", "documentos")),
            ("abre gmail", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("como esta la memoria", "cpu_y_ram", ()),
            ("cuanto pesa la carpeta documentos",
             "peso_de", ("documentos",)),
            ("haz clic derecho", "clic", ("derecho",)),
            ("escribe hola", "escribir_texto", ("hola",)),
            ("apaga la computadora", "apagar", ()),
            ("sube el volumen", "volumen_subir", ()),
            ("que procesos hay", "procesos", ()),
            ("reproduce metallica", "reproduce", ("metallica",)),
        )
        for texto, nombre, argumentos in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    acciones[0].nombre, nombre,
                    "%r cambio de accion: %r" % (texto, acciones[0].nombre))
                self.assertEqual(
                    acciones[0].argumentos, argumentos,
                    "%r cambio de argumentos: %r"
                    % (texto, acciones[0].argumentos))


if __name__ == "__main__":
    unittest.main()
