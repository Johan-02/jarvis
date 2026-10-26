# pruebas de la FASE E2d del asistente Jarvis (2026-10-21): GRUPO 3
# (ESCRIBIR Y TECLEAR) y GRUPO 4 (INFORMACION DEL SISTEMA). 9 acciones.
#
# REGLA DE ORO: NADA se ejecuta en vivo. ctypes (portapapeles, SendInput,
# GetSystemTimes, GlobalMemoryStatusEx y GetSystemPowerStatus), win32api
# (SetCursorPos y mouse_event), subprocess (tasklist), shutil (disk_usage),
# os (walk) y time (el intervalo de la CPU) van SIEMPRE reemplazados por
# dobles de solo registro: ni una tecla real, ni un clic, ni un movimiento
# de cursor, ni un tasklist, ni un disco tocado de verdad. La UNICA cosa
# "real" de este archivo son los TAMANIOS y OFFSETS de las estructuras de
# Windows, que se leen de la clase sin tocar ningun sistema.
#
# Lo que mide este archivo:
#   1. las 9 acciones nuevas estan en REGISTRO y CLASIFICADAS (la
#      proteccion de tests/test_motor.py sigue viva);
#   2. escribir_texto manda al portapapeles EXACTAMENTE lo que se le dio
#      ("puño año ¿qué?" entero, con acentos y enie) y despues Ctrl+V; un
#      portapapeles ocupado -> ok=False, sin reventar y sin pegar;
#   3. mandar_teclas pulsa y suelta en orden inverso con KEYEVENTF_KEYUP,
#      una tecla desconocida -> ok=False sin mandar NADA, y la estructura
#      INPUT mide lo que Windows exige (40 bytes en x64, wVk en el 8);
#   4. el raton: dentro de pantalla -> SetCursorPos; fuera -> ok=False; un
#      clic es SIEMPRE el par abajo/arriba del mismo boton;
#   5. cpu_y_ram con los ctypes doblados no revienta y da valores en rango;
#      memoria total en 0 o lectura inconsistente -> ok=False, sin inventar;
#   6. procesos con tasklist LOCALIZADO ("1,234,568 KB", con coma de miles)
#      no revienta y devuelve el texto TAL CUAL, nunca un float(); por
#      defecto da un RESUMEN hablable (< 300 caracteres, medido en vivo el
#      2026-10-23) con los que mas memoria usan, y con detalle=True la
#      lista acotada de siempre;
#   7. espacio_disco y peso_de con ruta invalida -> ok=False;
#   8. las 9 ordenes nuevas mapean a su accion correcta, las viejas
#      siguen iguales (ninguna se comio el desempate de otra) y "pega",
#      "pégalo" y variantes mandan Ctrl+V (medido en vivo: "pégalo" se iba
#      a la IA);

import collections
import contextlib
import ctypes
import os
import re
import sys
import unittest
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

# las 9 acciones nuevas de esta fase, TODAS inocuas: o escriben donde esta
# el cursor, o solo LEEN. Ninguna esta ni puede estar en DESTRUCTIVAS
ACCIONES_NUEVAS = (
    # GRUPO 3: escribir y teclar
    "escribir_texto",
    "mandar_teclas",
    "mover_raton",
    "clic",
    # GRUPO 4: informacion del sistema
    "espacio_disco",
    "cpu_y_ram",
    "bateria",
    "procesos",
    "peso_de",
)

_GB = 1024 ** 3

# uso de disco con la MISMA forma que devuelve shutil.disk_usage (namedtuple
# con total, used y free); los numeros salen de medicion real de esta PC
_USO = collections.namedtuple("uso", "total used free")


@contextlib.contextmanager
def _doblado():
    """reemplaza TODO lo que el motor toca del sistema por dobles de registro

    Dentro de esta ventana no puede salir ni un SendInput, ni un portapapeles
    de verdad, ni un SetCursorPos, ni un clic, ni un tasklist, ni un
    disk_usage, ni una llamada ctypes. Los seis nombres se parchan sobre el
    MODULO motor (los mismos que main/motor.py importa a nivel de modulo, por
    eso puede parcharlos). Dos detalles que NO son decoracion:
      - os.path.join une con barras invertidas y expanduser deja su entrada
        tal cual, para que una ruta que pase por ahi se pueda comparar;
      - ctypes.byref devuelve el objeto MISMO: asi el doble de
        GetSystemTimes llena la struct de verdad y _filetime_entero la lee,
        en vez de quedarse con un MagicMock que nadie pudo escribir.
    """
    dobles = {nombre: mock.MagicMock(name=nombre + "_falso")
              for nombre in ("ctypes", "win32api", "subprocess", "shutil",
                             "os", "time")}
    dobles["os"].path.join.side_effect = \
        lambda *partes: "\\".join(str(parte) for parte in partes)
    dobles["os"].path.expanduser.side_effect = lambda texto: texto
    dobles["ctypes"].byref.side_effect = lambda objeto: objeto
    with contextlib.ExitStack() as pila:
        for nombre, doble in dobles.items():
            pila.enter_context(mock.patch.object(motor, nombre, doble))
        yield dobles


def _sucias(dobles):
    """los dobles que quedaron con llamadas registradas (los limpios, no)"""
    return {nombre: list(doble.mock_calls)
            for nombre, doble in dobles.items() if doble.mock_calls}


def _sendinput_acepta_todo(dobles):
    """SendInput devuelve CUANTAS teclas acepto, que es lo que hace Windows

    El doble no lo sabe solo: sin ese entero la comprobacion de
    mandar_teclas marcaria ok=False en TODA prueba de exito. Devolver la
    cantidad pedida es el caso "va todo bien".
    """
    user32 = dobles["ctypes"].windll.user32
    user32.SendInput.side_effect = \
        lambda cantidad, _entradas, _tamano: cantidad
    return user32


def _pantalla_falsa(dobles, ancho=1366, alto=768):
    """win32api.GetSystemMetrics devuelve ese ancho y ese alto

    Sin esto la comparacion 0 <= x < ancho reventaria contra un MagicMock
    (que no soporta orden) en vez de probar el rango de coordenadas.
    """
    dobles["win32api"].GetSystemMetrics.side_effect = \
        lambda indice: ancho if indice == motor._SM_CXSCREEN else alto


def _cpu_falso(dobles, ocioso=100, total=1000):
    """GetSystemTimes 'mide' DOS veces con deltas controlados

    La struct llega como el objeto MISMO (byref doblado en _doblado), asi
    que el doble la llena de verdad y _filetime_entero la lee. La primera
    lectura queda en cero y la segunda trae los deltas: con ocioso=100 y
    total=1000 el uso sale 90%.
    """
    lecturas = [(0, 0, 0), (ocioso, total, 0)]

    def _llena(ocioso_f, sistema_f, usuario_f):
        v_ocioso, v_sistema, v_usuario = lecturas.pop(0)
        ocioso_f.dwLowDateTime = v_ocioso
        sistema_f.dwLowDateTime = v_sistema
        usuario_f.dwLowDateTime = v_usuario
        return 1

    dobles["ctypes"].windll.kernel32.GetSystemTimes.side_effect = _llena


def _memoria_falsa(dobles, total=16 * _GB, libre=8 * _GB, uso=50,
                   guardadas=None):
    """GlobalMemoryStatusEx llena el struct con esos valores y da exito;

    `guardadas` acumula el struct (si se le pasa una lista) para poder
    inspeccionarlo despues desde la prueba
    """
    def _llena(estado):
        if guardadas is not None:
            guardadas.append(estado)
        estado.dwMemoryLoad = uso
        estado.ullTotalPhys = total
        estado.ullAvailPhys = libre
        return 1

    dobles["ctypes"].windll.kernel32.GlobalMemoryStatusEx.side_effect = _llena


def _bateria_falsa(dobles, ac=1, flag=6, pct=87):
    """GetSystemPowerStatus llena el struct con esos bytes y da exito"""
    def _llena(estado):
        estado.ACLineStatus = ac
        estado.BatteryFlag = flag
        estado.BatteryLifePercent = pct
        return 1

    dobles["ctypes"].windll.kernel32.GetSystemPowerStatus.side_effect = _llena


def _tasklist_falso(dobles, texto):
    """tasklist 'contesta' con esa stdout"""
    respuesta = mock.MagicMock(name="tasklist_falso")
    respuesta.stdout = texto
    respuesta.returncode = 0
    dobles["subprocess"].run.return_value = respuesta


# argumentos de MUESTRA de cada accion: sin ellos, una accion CON argumentos
# daria TypeError en vez de probar su comportamiento. Las muestras son
# cortas a proposito: la prueba que las usa no quiere teclear texto real ni
# mover el raton, solo ver que las 9 pasan por ejecutar() sin confirmacion
_MUESTRAS = {
    "escribir_texto": ("hola",),
    "mandar_teclas": ("ctrl+c",),
    "mover_raton": (10, 10),
    "clic": ("izquierdo",),
}


def _teclas_de(llamada):
    """(wVk, dwFlags) de cada entrada de UNA llamada a SendInput, en orden"""
    _cantidad, entradas, _tamano = llamada.args
    return [(entrada.ki.wVk, entrada.ki.dwFlags)
            for entrada in entradas]


class TestLasNueveClasificadas(unittest.TestCase):
    """las 9 estan en REGISTRO y CLASIFICADAS: la proteccion sigue viva"""

    def test_las_nueve_estan_en_el_registro(self):
        """sin registrar, ejecutar() devolveria ACCION_DESCONOCIDA

        main.py NO se toco en esta fase: por eso los nombres del
        reconocedor tienen que ser CLAVES del REGISTRO tal cual, sin pasar
        por TRADUCCION_MOTOR, o la frase se caeria a la cadena if/elif y
        nunca llegaria al motor.
        """
        faltantes = [nombre for nombre in ACCIONES_NUEVAS
                     if nombre not in motor.REGISTRO]
        self.assertEqual(
            faltantes, [],
            "estas acciones no estan en REGISTRO: %s" % faltantes)

    def test_las_nueve_estan_en_la_lista_blanca(self):
        """cada una esta en INOCUAS y NINGUNA en DESTRUCTIVAS

        Es la misma regla que la parametrizada de tests/test_motor.py,
        repetida aqui para que el fallo diga exactamente cual de las 9 de
        esta fase quedo sin clasificar (o quedo de mas en DESTRUCTIVAS, que
        la pondria a pedir confirmacion para una lectura de disco).
        """
        for nombre in ACCIONES_NUEVAS:
            with self.subTest(nombre=nombre):
                self.assertIn(
                    nombre, INOCUAS,
                    "%r esta en REGISTRO pero NO esta en la lista blanca "
                    "INOCUAS: la parametrizada de test_motor la rechaza"
                    % (nombre,))
                self.assertNotIn(
                    nombre, motor.DESTRUCTIVAS,
                    "%r quedo ademas en DESTRUCTIVAS: pediria confirmacion "
                    "para SOLO LEER o para escribir texto" % (nombre,))

    def test_las_nueve_ejecutan_sin_confirmacion_ni_reventar(self):
        """todas van por ejecutar() con confirmado=False y terminan ok

        Si alguna quedara mal clasificada como destructiva, el detalle
        saldria REQUIERE_CONFIRMACION y el assertNotEqual la delata. Todo
        ocurre con los dobles puestos: ninguna llega al sistema real.
        """
        with _doblado() as dobles:
            # la configuracion que cada accion necesita para llegar a su
            # final feliz: sin ella, el fallo seria del doble y no del
            # codigo que se quiere probar
            _sendinput_acepta_todo(dobles)
            _pantalla_falsa(dobles)
            _cpu_falso(dobles)
            _memoria_falsa(dobles)
            _bateria_falsa(dobles)
            _tasklist_falso(
                dobles, '"x.exe","1","Consola","1",1 KB"\r\n')
            dobles["shutil"].disk_usage.return_value = _USO(
                total=_GB, used=0, free=_GB)
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter([])

            for nombre in ACCIONES_NUEVAS:
                with self.subTest(nombre=nombre):
                    resultado = motor.ejecutar(
                        Accion(nombre, _MUESTRAS.get(nombre, ())),
                        confirmado=False)
                    self.assertNotEqual(
                        resultado.detalle, motor.REQUIERE_CONFIRMACION,
                        "%r pide confirmacion siendo inocua" % (nombre,))
                    self.assertTrue(
                        resultado.ok,
                        "%r no ejecuto: %r" % (nombre, resultado.detalle))

        # todo lo de arriba salio por dobles, nunca por el sistema real:
        # el contexto es el que lo garantiza, aqui solo se mira que los
        # dobles fueron los que recibieron las llamadas
        self.assertTrue(_sucias(dobles),
                        "ninguna de las 9 toco siquiera a los dobles")


class TestEscribirTexto(unittest.TestCase):
    """portapapeles (UTF-16) + Ctrl+V: los acentos llegan ENTEROS"""

    def test_el_portapapeles_recibe_los_acentos_y_la_enie_enteros(self):
        """LA PRUEBA CENTRAL: "puño año ¿qué?" sale byte a byte intacta

        pyautogui.write() perderia estos mismos caracteres en silencio (esa
        es la razon de existir de esta accion), asi que el dato que hay que
        proteger es que lo que ENTRO a memmove es exactamente la cadena que
        llego, ya en UTF-16, y que despues se dispara Ctrl+V.
        """
        texto = "puño año ¿qué?"
        with _doblado() as dobles:
            user32 = _sendinput_acepta_todo(dobles)
            resultado = motor.ejecutar(Accion("escribir_texto", (texto,)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(resultado.detalle, "escrito: " + texto)

        # el portapapeles recibe CF_UNICODETEXT (13) y la cadena ENTERA
        escritura = dobles["ctypes"].memmove.call_args
        self.assertIsNotNone(escritura, "no se copio nada al portapapeles")
        datos = escritura.args[1]
        self.assertEqual(
            datos.decode("utf-16-le"), texto + "\0",
            "el portapapeles recibo %r y tenia que recibir %r"
            % (datos.decode("utf-16-le"), texto + "\0"))
        self.assertIn("ñ", texto, "la prueba no esta midiendo la enie")
        self.assertEqual(
            dobles["ctypes"].windll.user32.SetClipboardData.call_args.args[0],
            motor._CF_UNICODETEXT,
            "no se uso CF_UNICODETEXT (13)")

        # y despues: Ctrl+V con sus 4 entradas (ctrl abajo, v abajo, v
        # arriba, ctrl arriba) y KEYEVENTF_KEYUP en las dos solturas
        user32.SendInput.assert_called_once()
        llamada = user32.SendInput.call_args
        self.assertEqual(llamada.args[0], 4, "Ctrl+V son 4 entradas")
        self.assertEqual(
            _teclas_de(llamada),
            [(0x11, 0), (ord("V"), 0), (ord("V"), motor._KEYEVENTF_KEYUP),
             (0x11, motor._KEYEVENTF_KEYUP)],
            "Ctrl+V salio mal formado")

    def test_un_portapapeles_ocupado_no_lanza_y_no_pega(self):
        """OpenClipboard devuelve 0 -> ok=False, cero teclas y sin cerrar

        Cerrar lo que no se abrio tambien seria un error: CloseClipboard
        solo se llama si OpenClipboard tuvo exito, y por eso el finally
        esta DESPUES de esa primera comprobacion.
        """
        with _doblado() as dobles:
            user32 = dobles["ctypes"].windll.user32
            user32.OpenClipboard.return_value = 0
            resultado = motor.ejecutar(
                Accion("escribir_texto", ("hola",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("ocupado", resultado.detalle)
        dobles["ctypes"].windll.kernel32.GlobalAlloc.assert_not_called()
        user32.SetClipboardData.assert_not_called()
        user32.SendInput.assert_not_called()
        user32.CloseClipboard.assert_not_called()

    def test_un_alloc_que_falla_cierra_el_portapapeles_y_no_pega(self):
        """GlobalAlloc devuelve 0 -> ok=False y CloseClipboard SI se llama

        Es el finally del portapapeles: sin el, la proxima llamada se
        quedaria con el portapapeles trabado para todo el equipo.
        """
        with _doblado() as dobles:
            user32 = _sendinput_acepta_todo(dobles)
            dobles["ctypes"].windll.kernel32.GlobalAlloc.return_value = 0
            resultado = motor.ejecutar(Accion("escribir_texto", ("hola",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("memoria", resultado.detalle)
        user32.SetClipboardData.assert_not_called()
        user32.CloseClipboard.assert_called_once_with()
        user32.SendInput.assert_not_called()

    def test_un_clipboard_que_rechaza_el_texto_libera_la_memoria(self):
        """SetClipboardData sin exito -> ok=False, GlobalFree y sin pegar

        Ahi el bloque es DUENO del llamador, y no liberarlo dejaria memoria
        colgada en cada intento fallido.
        """
        with _doblado() as dobles:
            user32 = _sendinput_acepta_todo(dobles)
            user32.SetClipboardData.return_value = None
            resultado = motor.ejecutar(Accion("escribir_texto", ("hola",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("rechazo", resultado.detalle)
        dobles["ctypes"].windll.kernel32.GlobalFree.assert_called_once()
        user32.CloseClipboard.assert_called_once_with()
        user32.SendInput.assert_not_called()

    def test_un_texto_vacio_no_abre_el_portapapeles(self):
        """sin texto no se toca NADA: ni clipboard, ni teclas"""
        for muestra in ("", "   ", None):
            with _doblado() as dobles, self.subTest(texto=muestra):
                resultado = motor.ejecutar(
                    Accion("escribir_texto", (muestra,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["ctypes"].windll.user32.OpenClipboard \
                    .assert_not_called()
                dobles["ctypes"].windll.user32.SendInput.assert_not_called()


class TestMandarTeclas(unittest.TestCase):
    """SendInput: pultan en orden y SUELTAN al reves; lo demas no se toca"""

    def test_ctrl_c_pulsa_ctrl_y_c_y_los_suelta_al_reves(self):
        with _doblado() as dobles:
            user32 = _sendinput_acepta_todo(dobles)
            resultado = motor.ejecutar(
                Accion("mandar_teclas", ("ctrl+c",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            dobles["ctypes"].windll.user32.SendInput.call_count, 1)
        llamada = user32.SendInput.call_args
        self.assertEqual(llamada.args[0], 4, "ctrl y c: 4 entradas")
        self.assertEqual(
            llamada.args[2], motor._TAMANO_INPUT,
            "SendInput exige sizeof(INPUT) como tercer argumento")
        self.assertEqual(
            _teclas_de(llamada),
            [(0x11, 0), (ord("C"), 0), (ord("C"), motor._KEYEVENTF_KEYUP),
             (0x11, motor._KEYEVENTF_KEYUP)],
            "salio mal: las teclas no se sueltan en orden inverso")

    def test_un_atajo_largo_suelta_todo_en_orden_inverso(self):
        """ctrl+shift+escape -> 6 entradas: 3 pulsadas, 3 soltadas al reves"""
        with _doblado() as dobles:
            user32 = _sendinput_acepta_todo(dobles)
            resultado = motor.ejecutar(
                Accion("mandar_teclas", ("ctrl+shift+escape",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            _teclas_de(user32.SendInput.call_args),
            [(0x11, 0), (0x10, 0), (0x1B, 0),
             (0x1B, motor._KEYEVENTF_KEYUP),
             (0x10, motor._KEYEVENTF_KEYUP),
             (0x11, motor._KEYEVENTF_KEYUP)])

    def test_una_tecla_desconocida_no_manda_nada(self):
        """ok=False y CERO SendInput: una letra con tilde NO es tecla
        virtual (por eso escribir_texto va por portapapeles)"""
        for atajo in ("ctrl+noexiste", "ñ", "á", "ctrl+", "+ctrl", "z+"):
            with _doblado() as dobles, self.subTest(atajo=atajo):
                resultado = motor.ejecutar(
                    Accion("mandar_teclas", (atajo,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["ctypes"].windll.user32.SendInput.assert_not_called()

    def test_una_entrada_que_no_es_texto_no_lanza(self):
        for muestra in (None, "", 42, ("ctrl", "c")):
            with _doblado() as dobles, self.subTest(atajo=muestra):
                resultado = motor.ejecutar(
                    Accion("mandar_teclas", (muestra,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["ctypes"].windll.user32.SendInput.assert_not_called()

    def test_si_sendinput_acepta_menos_teclas_no_es_exito(self):
        """Windows contesto 1 de 4 -> ok=False: a medias tambien es fallo"""
        with _doblado() as dobles:
            user32 = dobles["ctypes"].windll.user32
            user32.SendInput.side_effect = \
                lambda cantidad, _entradas, _tamano: cantidad - 1
            resultado = motor.ejecutar(
                Accion("mandar_teclas", ("ctrl+c",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("SendInput", resultado.detalle)

    def test_la_estructura_input_mide_lo_que_windows_exige(self):
        """MEDIDO el 2026-10-21 en esta PC: INPUT = 40 bytes (x64) o 28
        (x86), el DWORD type en el byte 0 y el wVk justo despues del union

        SendInput rechaza todo el paquete si cbSize no es sizeof(INPUT), y
        un wVk corrido teclearia otra cosa. Esto NO toca el sistema: solo
        se escribe en un struct propio y se leen sus bytes.
        """
        self.assertIn(
            motor._TAMANO_INPUT, (28, 40),
            "INPUT no mide lo que Windows exige: %d bytes"
            % motor._TAMANO_INPUT)
        self.assertEqual(motor._TAMANO_INPUT, ctypes.sizeof(motor._INPUT))

        entrada = motor._INPUT()
        entrada.type = motor._INPUT_KEYBOARD
        entrada.ki.wVk = ord("A")
        crudo = ctypes.string_at(ctypes.byref(entrada), motor._TAMANO_INPUT)
        self.assertEqual(
            crudo[:4], b"\x01\x00\x00\x00",
            "el DWORD type no esta en el byte 0 o no vale INPUT_KEYBOARD")
        desplazamiento = motor._TAMANO_INPUT - ctypes.sizeof(motor._DATOS_INPUT)
        self.assertEqual(
            crudo[desplazamiento:desplazamiento + 2], b"A\x00",
            "el wVk no quedo en el byte %d" % desplazamiento)


class TestRaton(unittest.TestCase):
    """SetCursorPos y mouse_event: dentro del rango, par abajo/arriba"""

    def test_mover_dentro_de_pantalla_llama_setcursorpos(self):
        with _doblado() as dobles:
            _pantalla_falsa(dobles, 1366, 768)
            resultado = motor.ejecutar(Accion("mover_raton", (100, 200)))

        self.assertTrue(resultado.ok, resultado.detalle)
        dobles["win32api"].SetCursorPos.assert_called_once_with((100, 200))

    def test_mover_fuera_de_pantalla_no_mueve_nada(self):
        """fuera del rango -> ok=False y CERO SetCursorPos, que en la PC
        real habria lanzado un pywintypes.error"""
        casos = ((5000, 200), (100, 5000), (-1, 10), (10, -1))
        for x, y in casos:
            with _doblado() as dobles, self.subTest(x=x, y=y):
                _pantalla_falsa(dobles, 1366, 768)
                resultado = motor.ejecutar(Accion("mover_raton", (x, y)))
                self.assertFalse(resultado.ok, resultado.detalle)
                self.assertIn("fuera", resultado.detalle)
                dobles["win32api"].SetCursorPos.assert_not_called()

    def test_una_coordenada_que_no_es_entero_no_mueve_nada(self):
        """los booleanos cuentan: True no es la coordenada 1"""
        for muestra in (("cien", 200), (100, "doscientos"), (True, 10),
                        (None, 10), (10.5, 10)):
            with _doblado() as dobles, self.subTest(x=muestra):
                _pantalla_falsa(dobles, 1366, 768)
                resultado = motor.ejecutar(
                    Accion("mover_raton", (muestra, 10)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["win32api"].SetCursorPos.assert_not_called()

    def test_un_error_de_win32_no_lanza(self):
        """SetCursorPos que lanza -> ok=False, la excepcion no se escapa"""
        with _doblado() as dobles:
            _pantalla_falsa(dobles, 1366, 768)
            dobles["win32api"].SetCursorPos.side_effect = \
                motor._ERROR_WIN32(5, "acceso denegado")
            resultado = motor.ejecutar(Accion("mover_raton", (10, 20)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no pude mover", resultado.detalle)

    def test_el_clic_es_el_par_abajo_y_arriba_del_mismo_boton(self):
        """SIEMPRE los dos eventos, en ese orden: uno solo dejaria el
        boton apretado para siempre"""
        casos = (
            ("izquierdo", motor._MOUSEEVENTF_LEFTDOWN, motor._MOUSEEVENTF_LEFTUP),
            ("derecho", motor._MOUSEEVENTF_RIGHTDOWN, motor._MOUSEEVENTF_RIGHTUP),
            ("medio", motor._MOUSEEVENTF_MIDDLEDOWN, motor._MOUSEEVENTF_MIDDLEUP),
        )
        for boton, abajo, arriba in casos:
            with _doblado() as dobles, self.subTest(boton=boton):
                resultado = motor.ejecutar(Accion("clic", (boton,)))
                eventos = dobles["win32api"].mouse_event.call_args_list
                self.assertTrue(resultado.ok, resultado.detalle)
                self.assertEqual(
                    [list(llamada.args) for llamada in eventos],
                    [[abajo, 0, 0, 0], [arriba, 0, 0, 0]],
                    "el clic %r no salio como par abajo/arriba" % (boton,))

    def test_un_boton_desconocido_no_pulsa_nada(self):
        for boton in ("cola", "", None, 7):
            with _doblado() as dobles, self.subTest(boton=boton):
                resultado = motor.ejecutar(Accion("clic", (boton,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["win32api"].mouse_event.assert_not_called()

    def test_clic_por_defecto_es_el_izquierdo(self):
        """la accion sin argumentos tiene que ser un clic normal"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("clic"))

        self.assertTrue(resultado.ok, resultado.detalle)
        eventos = dobles["win32api"].mouse_event.call_args_list
        self.assertEqual(
            [list(llamada.args) for llamada in eventos],
            [[motor._MOUSEEVENTF_LEFTDOWN, 0, 0, 0],
             [motor._MOUSEEVENTF_LEFTUP, 0, 0, 0]])


class TestEspacioDisco(unittest.TestCase):
    """shutil.disk_usage: GB con un decimal; ruta mala -> ok=False"""

    def test_los_tres_gb_salen_con_un_decimal(self):
        with _doblado() as dobles:
            dobles["shutil"].disk_usage.return_value = _USO(
                total=476 * _GB + _GB // 2,      # 476.5 GB
                used=76 * _GB,                   # 76.0 GB
                free=400 * _GB + _GB // 2)       # 400.5 GB
            resultado = motor.ejecutar(Accion("espacio_disco"))

        self.assertTrue(resultado.ok, resultado.detalle)
        dobles["shutil"].disk_usage.assert_called_once_with("C:/")
        self.assertEqual(
            resultado.detalle,
            "disco C:/: 476.5 GB en total, 400.5 GB libres, 76.0 GB en uso")
        self.assertRegex(resultado.detalle, r"\d+\.\d GB")

    def test_una_ruta_invalida_es_ok_false_sin_reventar(self):
        """medido en esta PC: disk_usage de una unidad inexistente tira
        FileNotFoundError, que es un OSError"""
        with _doblado() as dobles:
            dobles["shutil"].disk_usage.side_effect = FileNotFoundError(
                "WinError 161")
            resultado = motor.ejecutar(
                Accion("espacio_disco", ("Z:/no_existe",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no pude leer", resultado.detalle)

    def test_sin_ruta_no_se_llama_al_disco(self):
        for muestra in ("", "   ", None):
            with _doblado() as dobles, self.subTest(ruta=muestra):
                resultado = motor.ejecutar(
                    Accion("espacio_disco", (muestra,)))
                self.assertFalse(resultado.ok, resultado.detalle)
                dobles["shutil"].disk_usage.assert_not_called()


class TestCpuYRam(unittest.TestCase):
    """GetSystemTimes DOS veces + GlobalMemoryStatusEx: sin bucles ni cifras
    inventadas"""

    def test_no_revienta_y_da_valores_en_rango(self):
        """CPU 90%, RAM 8.0 de 16.0 GB, DOS lecturas y UN solo sleep"""
        with _doblado() as dobles:
            guardadas = []
            _cpu_falso(dobles, ocioso=100, total=1000)
            _memoria_falsa(dobles, guardadas=guardadas)
            resultado = motor.ejecutar(Accion("cpu_y_ram", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        cpu = re.search(r"CPU (\d+)%", resultado.detalle)
        self.assertIsNotNone(
            cpu, "no salio el porcentaje de CPU en %r" % (resultado.detalle,))
        self.assertEqual(int(cpu.group(1)), 90)
        self.assertTrue(0 <= int(cpu.group(1)) <= 100,
                        "CPU fuera de rango: %s" % cpu.group(1))
        ram = re.search(r"RAM usada [\d.]+ GB de [\d.]+ GB \((\d+)%\)",
                        resultado.detalle)
        self.assertIsNotNone(
            ram, "no salio la RAM en %r" % (resultado.detalle,))
        self.assertTrue(0 <= int(ram.group(1)) <= 100,
                        "RAM fuera de rango: %s" % ram.group(1))
        self.assertIn("8.0 GB", resultado.detalle)
        self.assertIn("16.0 GB", resultado.detalle)

        # DOS lecturas y UNA dormida de _INTERVALO_CPU: ni un solo lectura
        # (daria 0% inventado) ni un bucle que dejaria a main.py colgado
        self.assertEqual(
            dobles["ctypes"].windll.kernel32.GetSystemTimes.call_count, 2,
            "medir CPU exige exactamente DOS lecturas")
        dobles["time"].sleep.assert_called_once_with(motor._INTERVALO_CPU)

        # el struct llego con dwLength puesto (sin eso Windows lo rechaza)
        self.assertEqual(guardadas[0].dwLength, motor._TAMANO_MEMORYSTATUSEX)

    def test_una_memoria_total_en_cero_no_se_anuncia(self):
        """0 GB de 0 GB seria un cero indistinguible de un cero medido"""
        with _doblado() as dobles:
            _cpu_falso(dobles)
            _memoria_falsa(dobles, total=0, libre=0)
            resultado = motor.ejecutar(Accion("cpu_y_ram", ()))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("total salio en 0", resultado.detalle)
        dobles["time"].sleep.assert_not_called()

    def test_una_lectura_inconsistente_no_se_anuncia(self):
        """mas tiempo OCIOSO que total: dato corrupto, no un 0%"""
        with _doblado() as dobles:
            _cpu_falso(dobles, ocioso=2000, total=1000)
            _memoria_falsa(dobles)
            resultado = motor.ejecutar(Accion("cpu_y_ram", ()))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("inconsistente", resultado.detalle)

    def test_apis_que_fallan_devuelven_ok_false(self):
        """ninguna de las DOS lecturas ni la memoria dan exito inventado"""
        with _doblado() as dobles:
            _memoria_falsa(dobles)
            dobles["ctypes"].windll.kernel32.GetSystemTimes.side_effect = \
                lambda *_argumentos: 0
            resultado = motor.ejecutar(Accion("cpu_y_ram", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("GetSystemTimes", resultado.detalle)

        # la SEGUNDA lectura puede caer a mitad de camino, tras el sleep
        with _doblado() as dobles:
            _memoria_falsa(dobles)
            dobles["ctypes"].windll.kernel32.GetSystemTimes.side_effect = \
                [1, 0]
            resultado = motor.ejecutar(Accion("cpu_y_ram", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("GetSystemTimes", resultado.detalle)

        with _doblado() as dobles:
            dobles["ctypes"].windll.kernel32.GlobalMemoryStatusEx \
                .return_value = 0
            resultado = motor.ejecutar(Accion("cpu_y_ram", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("GlobalMemoryStatusEx", resultado.detalle)


class TestBateria(unittest.TestCase):
    """GetSystemPowerStatus: si el sistema no sabe, se dice tal cual"""

    def test_una_bateria_real_se_dice_con_su_porcentaje(self):
        with _doblado() as dobles:
            _bateria_falsa(dobles, ac=1, flag=6, pct=87)
            resultado = motor.ejecutar(Accion("bateria", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            resultado.detalle, "conectado a la corriente, bateria al 87%")

    def test_un_escritorio_no_inventa_porcentaje(self):
        """255 = desconocido y bit 128 = sin bateria: se DICE, no se adivina"""
        with _doblado() as dobles:
            _bateria_falsa(dobles, ac=255, flag=128, pct=255)
            resultado = motor.ejecutar(Accion("bateria", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("desconocid", resultado.detalle)
        self.assertNotIn(
            "%", resultado.detalle,
            "invento un porcentaje: %r" % (resultado.detalle,))
        self.assertNotIn("255", resultado.detalle,
                         "suelta el codigo 255 como si fuera un dato")

    def test_una_falla_de_la_api_no_es_exito(self):
        with _doblado() as dobles:
            dobles["ctypes"].windll.kernel32.GetSystemPowerStatus \
                .return_value = 0
            resultado = motor.ejecutar(Accion("bateria", ()))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("GetSystemPowerStatus", resultado.detalle)


class TestProcesos(unittest.TestCase):
    """tasklist en csv: la memoria LOCALIZADA se devuelve tal cual; por
    defecto sale un RESUMEN hablable (los que mas memoria usan), medido el
    2026-10-23 en vivo: la lista entera dio 1.402 caracteres (~18 s)"""

    def _filas(self, cantidad, salto=10):
        """`cantidad` filas de tasklist con memoria creciente de `salto` KB"""
        return "".join(
            '"proc%d.exe","%d","Consola","1",%d KB"\r\n'
            % (n, n, n * salto)
            for n in range(cantidad))

    def test_el_resumen_por_defecto_ordena_por_memoria_y_cabe_en_300(self):
        """MEDIDO en vivo el 2026-10-23: 1.402 caracteres (~18 segundos
        hablando) eran INACEPTABLES para la voz. Por defecto ahora salen
        SOLO los _TOPE_RESUMEN de mas memoria, en orden descendente, y el
        total NUNCA se pierde"""
        # las filas medidas de verdad en la PC (chrome el mas pesado) mas
        # relleno hasta 185 procesos, como la medicion real
        filas = (
            '"System Idle Process","0","Services","0","8 KB"\r\n'
            '"System","4","Services","0","10,972 KB"\r\n'
            '"Registry","108","Services","0","64,300 KB"\r\n'
            '"chrome.exe","4128","Consola","1","1,234,568 KB"\r\n'
            + self._filas(181))
        with _doblado() as dobles:
            _tasklist_falso(dobles, filas)
            resultado = motor.ejecutar(Accion("procesos", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertLess(
            len(resultado.detalle), 300,
            "el resumen tiene %d caracteres: la voz tardaria %d segundos en "
            "decirlo entero: %r"
            % (len(resultado.detalle), len(resultado.detalle) // 78,
               resultado.detalle))
        self.assertIn("185 procesos", resultado.detalle,
                      "se perdio el TOTAL de procesos")
        self.assertEqual(
            resultado.detalle.count("(pid "), motor._TOPE_RESUMEN,
            "no salio exactamente el tope del resumen")
        # orden descendente de memoria: chrome (1.234.568) primero, luego
        # Registry, System y los dos rellenos mas grandes
        posiciones = [resultado.detalle.index(nombre) for nombre in
                      ("chrome.exe", "Registry (pid 108)", "System (pid 4)",
                       "proc180.exe", "proc179.exe")]
        self.assertEqual(
            posiciones, sorted(posiciones),
            "el resumen NO salio ordenado de mas a menos memoria: %r"
            % (resultado.detalle,))

    def test_la_memoria_localizada_sale_tal_cual_tambien_en_el_resumen(self):
        """TRAMPA MEDIDA en esta PC: tasklist /fo csv /nh devuelve
        "1,234,568 KB" con coma de miles; aqui no hay ni un float()"""
        salida = (
            '"System Idle Process","0","Services","0","8 KB"\r\n'
            '"chrome.exe","1234","Consola","1","1,234,568 KB"\r\n'
            '"notepad.exe","5678","Consola","1","12.345 KB"\r\n')
        with _doblado() as dobles:
            _tasklist_falso(dobles, salida)
            resultado = motor.ejecutar(Accion("procesos", ()))

        self.assertTrue(resultado.ok, resultado.detalle)
        dobles["subprocess"].run.assert_called_once_with(
            ["tasklist", "/fo", "csv", "/nh"], check=True,
            capture_output=True, text=True)
        self.assertIn("3 procesos", resultado.detalle)
        self.assertIn("chrome.exe (pid 1234)", resultado.detalle)
        self.assertIn(
            "1,234,568 KB", resultado.detalle,
            "la coma de miles se pierde o se convirtio en numero: %r"
            % (resultado.detalle,))
        self.assertIn("12.345 KB", resultado.detalle)

    def test_con_detalle_true_sale_la_lista_acotada_a_40_mas_el_total(self):
        """~300 procesos no se pueden ni leer ni escuchar: con detalle=True
        la lista se corta en _TOPE_PROCESOS pero el TOTAL es el real"""
        filas = self._filas(45)
        with _doblado() as dobles:
            _tasklist_falso(dobles, filas)
            resultado = motor.ejecutar(Accion("procesos", (True,)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("45 procesos:", resultado.detalle)
        self.assertEqual(
            resultado.detalle.count("(pid "), motor._TOPE_PROCESOS,
            "no salio exactamente el tope de procesos")
        self.assertIn("; y 5 mas", resultado.detalle)

    def test_una_memoria_partida_se_vuelve_a_juntar(self):
        """si el sistema local la dejara SIN comillas, la columna llega en
        trozos y aqui se rehace con ',' (nunca se asume punto flotante)"""
        salida = '"raro.exe","99","Consola","1",123,456 KB\r\n'
        with _doblado() as dobles:
            _tasklist_falso(dobles, salida)
            resultado = motor.ejecutar(Accion("procesos", (True,)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("raro.exe (pid 99), 123,456 KB", resultado.detalle)

    def test_tasklist_que_revienta_o_no_dice_nada_es_ok_false(self):
        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = \
                motor._ERROR_SUBPROCESO(1, ["tasklist"])
            resultado = motor.ejecutar(Accion("procesos", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("tasklist", resultado.detalle)

        with _doblado() as dobles:
            _tasklist_falso(dobles, "")
            resultado = motor.ejecutar(Accion("procesos", ()))
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("ningun proceso", resultado.detalle)

        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = OSError("no existe")
            resultado = motor.ejecutar(Accion("procesos", ()))
        self.assertFalse(resultado.ok, resultado.detalle)


class TestPesoDe(unittest.TestCase):
    """os.walk con raices acotadas: suma tamanios y nunca reventa"""

    def test_una_ruta_invalida_no_recorre_nada(self):
        with _doblado() as dobles:
            dobles["os"].path.isdir.return_value = False
            resultado = motor.ejecutar(Accion("peso_de", ("no_existe",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("raiz no valida", resultado.detalle)
        dobles["os"].walk.assert_not_called()

    def test_el_alias_se_resuelve_a_su_carpeta_real(self):
        """'documentos' -> ~\\Documents, el MISMO vocabulario que el motor"""
        with _doblado() as dobles:
            dobles["os"].path.expanduser.side_effect = \
                lambda texto: r"C:\Users\Edward"
            dobles["os"].path.isdir.return_value = True
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter([])
            resultado = motor.ejecutar(Accion("peso_de", ("documentos",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            dobles["os"].walk.call_args.args[0],
            r"C:\Users\Edward\Documents",
            "el alias documentos no se resolvio a su carpeta real")

    def test_sin_raiz_recorre_las_tres_carpetas_acotadas(self):
        """NUNCA C:\\ entero: son ~59 s medidos; las tres raices por
        defecto son Escritorio, Documentos y Descargas"""
        with _doblado() as dobles:
            dobles["os"].path.expanduser.side_effect = \
                lambda texto: r"C:\Users\Edward"
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter([])
            motor.ejecutar(Accion("peso_de"))

        recorridas = [llamada.args[0]
                      for llamada in dobles["os"].walk.call_args_list]
        self.assertEqual(
            recorridas,
            [r"C:\Users\Edward\Desktop", r"C:\Users\Edward\Documents",
             r"C:\Users\Edward\Downloads"],
            "las raices por defecto cambiaron: %r" % (recorridas,))

    def test_suma_los_tamanios_y_los_entrega_en_gb(self):
        with _doblado() as dobles:
            dobles["os"].path.isdir.return_value = True
            dobles["os"].walk.side_effect = lambda raiz, onerror=None: iter([
                (r"C:\docs", ["sub"], ["a.txt", "b.txt"]),
                (r"C:\docs\sub", [], ["c.txt"]),
            ])
            dobles["os"].path.getsize.side_effect = lambda ruta: {
                r"C:\docs\a.txt": _GB + 512 * 1024 ** 2,  # 1.5 GB
                r"C:\docs\b.txt": 10 * 1024 ** 2,          # 10 MB
                r"C:\docs\sub\c.txt": 2048,                # 2 KB
            }[ruta]
            resultado = motor.ejecutar(Accion("peso_de", ("documentos",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("1.5 GB", resultado.detalle)
        self.assertIn("3 archivos", resultado.detalle)

    def test_un_archivo_ilegible_se_cuenta_y_no_rompe_la_suma(self):
        """PermissionError en getsize -> se aparta y se AVISA, no se traga"""
        with _doblado() as dobles:
            dobles["os"].path.isdir.return_value = True
            dobles["os"].walk.side_effect = lambda raiz, onerror=None: iter([
                (r"C:\docs", [], ["a.txt", "b.txt"]),
            ])
            dobles["os"].path.getsize.side_effect = [1024, OSError("denegado")]
            resultado = motor.ejecutar(Accion("peso_de", ("documentos",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("1 archivos", resultado.detalle)
        self.assertIn("no pude leer 1", resultado.detalle)

    def test_sin_archivos_no_anuncia_un_peso_en_cero(self):
        """0.0 KB seria indistinguible de una carpeta que PESA cero"""
        with _doblado() as dobles:
            dobles["os"].path.isdir.return_value = True
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter([])
            resultado = motor.ejecutar(Accion("peso_de", ("documentos",)))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("no pude pesar ningun archivo", resultado.detalle)
        self.assertNotIn("0.0", resultado.detalle)


class TestOrdenesE2d(unittest.TestCase):
    """cada frase nueva mapea a su accion correcta, sin ejecutar nada"""

    def test_las_ordenes_nuevas_mapean_a_su_accion(self):
        casos = (
            # --- GRUPO 3: escribir y teclar ---
            ("escribe hola mundo", "escribir_texto", ("hola mundo",)),
            ("escribe mi correo", "escribir_texto", ("mi correo",)),
            ("copia", "mandar_teclas", ("ctrl+c",)),
            ("pega", "mandar_teclas", ("ctrl+v",)),
            ("pega esto", "mandar_teclas", ("ctrl+v",)),
            ("pegalo", "mandar_teclas", ("ctrl+v",)),
            ("pégalo", "mandar_teclas", ("ctrl+v",)),
            ("cierra esto", "mandar_teclas", ("ctrl+w",)),
            ("alt tab", "mandar_teclas", ("alt+tab",)),
            ("esc", "mandar_teclas", ("esc",)),
            ("enter", "mandar_teclas", ("enter",)),
            # el derecho ANTES que el izquierdo en la tabla, o el desempate
            # lo lanzaria como clic izquierdo
            ("haz clic derecho", "clic", ("derecho",)),
            ("haz clic", "clic", ("izquierdo",)),
            ("da un clic", "clic", ("izquierdo",)),
            ("mover el ratón a 100 200", "mover_raton", (100, 200)),
            ("mover el raton a 50 60", "mover_raton", (50, 60)),
            # --- GRUPO 4: informacion del sistema ---
            ("cuanto espacio queda en el disco",
             "espacio_disco", ()),
            ("cuanto espacio hay en el disco", "espacio_disco", ()),
            ("como esta la memoria", "cpu_y_ram", ()),
            ("cuanta ram", "cpu_y_ram", ()),
            ("que procesos estan abiertos", "procesos", ()),
            ("lista de procesos", "procesos", ()),
            ("cuanto pesa la carpeta documentos",
             "peso_de", ("documentos",)),
            ("cuanto pesa mis descargas", "peso_de", ("descargas",)),
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

    def test_pegalo_y_variantes_de_pega_mandan_ctrl_v(self):
        """MEDIDO en vivo el 2026-10-23: "pégalo" se iba a consultar_ia
        porque la orden "pega" no traia sus variantes. Ahora las DOS
        mitades: pasa tras agregar pegalo/pégalo en ORDENES, y FALLA si se
        quitan (verificado con mutacion en copia temporal). "pega esto" y
        la frase que TERMINA en la orden ya casaban con "pega"; el signo
        pegado lo limpia _indice_de. NINGUNA puede acabar en la IA."""
        casos = (
            "pega",
            "pega esto",
            "pegalo",
            "pégalo",
            "¡pégalo!",
            "¿pega esto?",
            "esto pega",          # la orden como ULTIMA palabra
            "esto ya pégalo",     # acentuada, tambien al final
        )
        for texto in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones],
                    ["mandar_teclas"],
                    "%r no reconocio pega: se fue a %r en vez de mandar "
                    "ctrl+v" % (texto, [a.nombre for a in acciones]))
                self.assertEqual(
                    acciones[0].argumentos, ("ctrl+v",),
                    "%r mando %r y tenia que mandar ctrl+v"
                    % (texto, acciones[0].argumentos))

    def test_sin_lo_que_la_orden_pide_se_va_a_la_ia(self):
        """nada se inventa: ni texto, ni la segunda coordenada, ni la raiz"""
        casos = (
            "escribe",                      # sin texto detras
            "mover el ratón a 100",         # UNA sola coordenada
            "mover el ratón a cien",        # sin numeros
            "cuanto pesa",                  # sin carpeta
            "cuanto pesa la carpeta de fotos",  # la ultima palabra no es raiz
        )
        for texto in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones],
                    ["consultar_ia"],
                    "%r disparo una accion sin lo que pide: %r"
                    % (texto, acciones))

    def test_las_ordenes_viejas_siguen_iguales(self):
        """las nuevas no se comieron el desempate ni el argumento de nadie"""
        casos = (
            ("abre gmail", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("que hora es", "hora", ()),
            ("reproduce metallica", "reproduce", ("metallica",)),
            ("cierra chrome a la fuerza",
             "cerrar_app_forzado", ("chrome",)),
            ("cierra chrome", "cerrar_app", ("chrome",)),
            ("busca mi factura en documentos",
             "buscar_archivo", ("mi factura", "documentos")),
            ("abre mi escritorio", "abrir_archivo", ("escritorio",)),
            ("pon el brillo a 50", "brillo", (50,)),
            ("captura la pantalla", "capturar_pantalla", ("escritorio",)),
            ("apaga la computadora", "apagar", ()),
            ("sube el volumen", "volumen_subir", ()),
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

    def test_la_escritura_con_tildes_se_reconoce_como_texto_libre(self):
        """la frase del usuario llega al motor con SU texto, no recortado a
        la primera palabra (el reconocedor baja a minusculas, que es su
        comportamiento de siempre: lo demas del texto viaja entero)"""
        acciones = interpretar("escribe la dirección de mi señoría")
        self.assertEqual(acciones[0].nombre, "escribir_texto")
        self.assertEqual(acciones[0].argumentos,
                         ("la dirección de mi señoría",))


if __name__ == "__main__":
    unittest.main()
