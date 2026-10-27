# pruebas de la FASE E2c del asistente Jarvis (2026-10-21): GRUPO 2, EL
# EQUIPO: volumen por pasos y silenciar, brillo, apagar, reiniciar, cancelar
# apagado, bloquear, suspender y captura de pantalla.
#
# REGLA DE ORO: NADA se ejecuta en vivo. subprocess (shutdown, rundll32 y
# powershell), ctypes (COM de audio, powrprof y GDI) y os van SIEMPRE
# reemplazados por dobles de solo registro: ni apagado real, ni reinicio, ni
# suspension, ni powershell de verdad, ni captura contra la pantalla real.
# Las UNICAS escrituras reales de todo el archivo ocurren dentro de una
# carpeta temporal que Python borra sola: un BMP de 70 bytes armado con
# pixeles FALSOS, la carpeta destino que capturar_pantalla ahora CREA antes
# de capturar (arreglo medido en vivo el 2026-10-23) y un .txt de prueba
# para medir que un archivo pasado como destino NO se usa como carpeta.
#
# Lo que mide este archivo:
#   1. las 10 acciones nuevas estan en REGISTRO y CLASIFICADAS (la
#      proteccion de tests/test_motor.py sigue viva);
#   2. las 3 destructivas (apagar, reiniciar, suspender) frenan sin
#      confirmar con CERO llamadas, y confirmadas si mandan SU comando;
#   3. brillo: rango valido -> ok=True con el nivel correcto; la clase WMI
#      VACIA -> ok=False sin reventar; fuera de rango -> cero powershell;
#   4. volumen: subir usa el paso 17, bajar el 18 y silenciar el SetMute
#      14 de la vtable COM, con el nivel saneado a [0, 1];
#   5. apagar(0) o una espera invalida -> ok=False SIN tocar shutdown,
#      aunque venga confirmado=True;
#   6. la captura escribe un BMP completo (GDI doblado) y sin exito de GDI
#      no escribe NADA; la carpeta destino se CREA ANTES de capturar, y una
#      ruta imposible o un archivo pasado como destino fallan SIN capturar;
#   7. las ordenes nuevas del reconocedor mapean a su accion correcta, y
#      "apaga la computadora" (que HOY se iba a la IA) ya se reconoce y pide
#      CONFIRMACION.

import contextlib
import os
import struct
import sys
import tempfile
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

# las 10 acciones nuevas de esta fase (3 destructivas y 7 inocuas)
ACCIONES_NUEVAS = (
    "volumen_subir",
    "volumen_bajar",
    "silenciar",
    "brillo",
    "apagar",
    "reiniciar",
    "cancelar_apagado",
    "bloquear",
    "suspender",
    "capturar_pantalla",
)

DESTRUCTIVAS_NUEVAS = ("apagar", "reiniciar", "suspender")

# pixeles falsos de la captura: pantalla de 2x2 a 32 bits = 16 bytes
PIXELES_FALSOS = b"\x77" * (2 * 2 * 4)


@contextlib.contextmanager
def _doblado():
    """reemplaza TODO lo que el motor toca del sistema por dobles de registro

    Dentro de esta ventana no puede salir ni un shutdown, ni un rundll32, ni
    un powershell, ni una llamada COM o GDI de verdad: los tres nombres se
    parchan sobre el MODULO motor (los mismos que main/motor.py importa a
    nivel de modulo, por eso puede parcharlos). os.path.join une con barras
    invertidas para que una ruta que pase por ahi se pueda comparar de
    verdad en las pruebas.
    """
    dobles = {nombre: mock.MagicMock(name=nombre + "_falso")
              for nombre in ("os", "subprocess", "ctypes")}
    dobles["os"].path.join.side_effect = \
        lambda *partes: "\\".join(str(parte) for parte in partes)
    dobles["os"].path.normpath.side_effect = lambda texto: texto
    dobles["os"].path.expanduser.side_effect = lambda texto: texto
    with contextlib.ExitStack() as pila:
        for nombre, doble in dobles.items():
            pila.enter_context(mock.patch.object(motor, nombre, doble))
        yield dobles


def _sucias(dobles):
    """los dobles que quedaron con llamadas registradas (los limpios, no)"""
    return {nombre: list(doble.mock_calls)
            for nombre, doble in dobles.items() if doble.mock_calls}


@contextlib.contextmanager
def _solo_gdi_y_subprocess():
    """como _doblado, pero con el os REAL: para probar que la carpeta
    destino de la captura se CREA (o se RECHAZA) de verdad ANTES de
    capturar (arreglo medido en vivo el 2026-10-23)

    ctypes y subprocess si van doblados: ni una llamada GDI real, ni un
    powershell de verdad. os queda entero (sin parchar) para que
    os.makedirs corra contra el sistema de archivos de verdad, igual que
    despues el open() real donde se escribe el BMP.
    """
    dobles = {nombre: mock.MagicMock(name=nombre + "_falso")
              for nombre in ("ctypes", "subprocess")}
    with contextlib.ExitStack() as pila:
        for nombre, doble in dobles.items():
            pila.enter_context(mock.patch.object(motor, nombre, doble))
        yield dobles


def _audio_falso(dobles, nivel=0.5, silencio=0):
    """la cadena COM de ctypes 'abre' el volumen y devuelve EXITO

    ctypes.cast se usa para dos cosas distintas (leer la vtable y armar la
    funcion) y el doble resuelve las DOS: su .contents es la vtable
    COMPARTIDA, que recuerda todos los indices pedidos, y llamarlo devuelve
    0 (S_OK). `nivel` es el que GetMasterVolumeLevelScalar "lee" y `silencio`
    el que GetMute "lee". Devuelve la vtable, para mirar sus indices.
    """
    ct = dobles["ctypes"]
    ct.windll.ole32.CoInitializeEx.return_value = 0
    ct.windll.ole32.CoCreateInstance.return_value = 0
    vtable = mock.MagicMock(name="vtable")

    def _cast_falso(_puntero, _tipo):
        funcion = mock.MagicMock(name="cast_falso")
        funcion.contents = vtable
        funcion.return_value = 0
        return funcion

    ct.cast.side_effect = _cast_falso
    ct.c_float.return_value.value = nivel
    ct.c_int.return_value.value = silencio
    return vtable


def _indices_de(vtable):
    """indices pedidos a la vtable, en orden, a traves de todos los _metodo"""
    return [llamada.args[0]
            for llamada in vtable.__getitem__.call_args_list]


def _powershell_falso(dobles, salida="", codigo=0):
    """hace que powershell 'conteste' con esa stdout y ese codigo de salida"""
    respuesta = mock.MagicMock(name="salida_powershell")
    respuesta.stdout = salida
    respuesta.returncode = codigo
    dobles["subprocess"].run.return_value = respuesta
    return respuesta


def _gdi_falso(dobles, filas_devueltas=2):
    """la cadena GDI 'captura' una pantalla de 2x2 con pixeles falsos

    GetSystemMetrics devuelve 2 y 2, BitBlt y GetDIBits exito, y los dos
    create_string_buffer devuelven (en ese orden) la cabecera de info y el
    buffer de pixeles con PIXELES_FALSOS de adentro. NADA toca la pantalla
    real.
    """
    ct = dobles["ctypes"]
    ct.windll.user32.GetSystemMetrics.side_effect = lambda codigo: 2
    ct.windll.gdi32.BitBlt.return_value = 1
    ct.windll.gdi32.GetDIBits.return_value = filas_devueltas
    info = mock.MagicMock(name="info_bmp")
    buffer_pixeles = mock.MagicMock(name="buffer_pixeles")
    buffer_pixeles.raw = PIXELES_FALSOS
    ct.create_string_buffer.side_effect = [info, buffer_pixeles]
    return info


class TestLasDiezClasificadas(unittest.TestCase):
    """las 10 estan en REGISTRO y CLASIFICADAS: la proteccion sigue viva"""

    def test_las_diez_estan_en_el_registro(self):
        """sin registrar, ejecutar() devolveria ACCION_DESCONOCIDA"""
        faltantes = [nombre for nombre in ACCIONES_NUEVAS
                     if nombre not in motor.REGISTRO]
        self.assertEqual(
            faltantes, [],
            "estas acciones no estan en REGISTRO: %s" % faltantes)

    def test_las_diez_estan_clasificadas(self):
        """cada una esta en INOCUAS o en DESTRUCTIVAS: ninguna sin clasificar

        Es la misma regla que la parametrizada de tests/test_motor.py,
        repetida aqui para que el fallo diga exactamente cual de las 10 de
        esta fase se quedo sin clasificar.
        """
        for nombre in ACCIONES_NUEVAS:
            with self.subTest(nombre=nombre):
                self.assertTrue(
                    nombre in INOCUAS or nombre in motor.DESTRUCTIVAS,
                    "%r esta en REGISTRO pero NO esta clasificada: no esta "
                    "en la lista blanca INOCUAS ni en DESTRUCTIVAS"
                    % (nombre,))

    def test_las_tres_destructivas_estan_partidas(self):
        """apagar/reiniciar/suspender piden confirmacion; lo que las DESHACE
        y lo que no pierde trabajo, NO (si cancelar_apagado fuera
        destructiva, el apagado ya mandado no tendria vuelta atras)"""
        for nombre in DESTRUCTIVAS_NUEVAS:
            with self.subTest(nombre=nombre):
                self.assertIn(nombre, motor.DESTRUCTIVAS,
                              "%r no esta en DESTRUCTIVAS" % (nombre,))
                self.assertNotIn(nombre, INOCUAS,
                                 "%r quedo ademas en INOCUAS: se ejecutaria "
                                 "sin confirmacion" % (nombre,))
        for nombre in ("cancelar_apagado", "bloquear"):
            with self.subTest(nombre=nombre):
                self.assertIn(nombre, INOCUAS)
                self.assertNotIn(nombre, motor.DESTRUCTIVAS)


class TestDestructivasDelEquipo(unittest.TestCase):
    """las 3 frenan sin confirmar (cero llamadas) y confirmadas SI mandan"""

    def test_sin_confirmar_cero_llamadas_a_cualquier_sistema(self):
        """LA REGLA: confirmado=False -> REQUIERE_CONFIRMACION y nada mas

        Ni shutdown, ni rundll32, ni powrprof, ni ctypes, ni subprocess:
        aunque el freno de ejecutar() estuviera roto, la llamada caeria en
        el doble y esta prueba lo veria.
        """
        for nombre in DESTRUCTIVAS_NUEVAS:
            with self.subTest(nombre=nombre):
                with _doblado() as dobles:
                    resultado = motor.ejecutar(
                        Accion(nombre), confirmado=False)
                self.assertEqual(
                    resultado.detalle, motor.REQUIERE_CONFIRMACION,
                    "%r sin confirmar devolvio %r"
                    % (nombre, resultado.detalle))
                self.assertFalse(resultado.ok)
                self.assertEqual(
                    _sucias(dobles), {},
                    "sin confirmar, %r toco el sistema por: %s"
                    % (nombre, _sucias(dobles)))

    def test_apagar_confirmado_manda_shutdown_s_con_su_espera(self):
        """confirmado=True -> shutdown /s /t 30, con check=True"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("apagar"), confirmado=True)

        dobles["subprocess"].run.assert_called_once_with(
            ["shutdown", "/s", "/t", "30"], check=True,
            capture_output=True, text=True)
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_reiniciar_confirmado_manda_shutdown_r(self):
        """confirmado=True -> shutdown /r /t 30 (la /r es lo que lo separa
        de apagar)"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("reiniciar"), confirmado=True)

        dobles["subprocess"].run.assert_called_once_with(
            ["shutdown", "/r", "/t", "30"], check=True,
            capture_output=True, text=True)
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_suspender_confirmado_llama_powrprof_y_no_shutdown(self):
        """confirmado=True -> SetSuspendState(False, False, False): la
        SUSPENSION y no la hibernacion, sin forzar y con despertar activo"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("suspender"), confirmado=True)

        dobles["ctypes"].windll.powrprof.SetSuspendState \
            .assert_called_once_with(False, False, False)
        dobles["subprocess"].run.assert_not_called()
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_espera_invalida_no_toca_shutdown_ni_confirmada(self):
        """apagar(0) -> ok=False y CERO shutdown, aunque venga confirmada

        /t 0 no dejaria NI UN instante para cancelar con shutdown /a, y una
        espera que no es numero ni siquiera llega al comando.
        """
        casos = (
            Accion("apagar", (0,)),
            Accion("apagar", (-5,)),
            Accion("apagar", ("treinta",)),
            Accion("reiniciar", (0,)),
        )
        with _doblado() as dobles:
            for accion in casos:
                with self.subTest(accion=accion):
                    resultado = motor.ejecutar(accion, confirmado=True)
                    self.assertFalse(
                        resultado.ok,
                        "%r con espera invalida devolvio ok=True"
                        % (accion,))
                    self.assertIn("espera", resultado.detalle)
                    dobles["subprocess"].run.assert_not_called()

    def test_suspender_fallido_no_dice_ok(self):
        """SetSuspendState devuelve 0 -> ok=False, nunca un exito inventado"""
        with _doblado() as dobles:
            dobles["ctypes"].windll.powrprof.SetSuspendState.return_value = 0
            resultado = motor.ejecutar(Accion("suspender"), confirmado=True)

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("SetSuspendState", resultado.detalle)


class TestVolumenPorPasos(unittest.TestCase):
    """subir/bajar mueven SU paso de la vtable; silenciar SU SetMute"""

    def test_subir_usa_volume_step_up_y_relee_el_nivel(self):
        """indice 17 = VolumeStepUp; despues se relee el escalar (9) para
        anunciar un nivel SANITADO, y COM se des-inicializa"""
        with _doblado() as dobles:
            vtable = _audio_falso(dobles, nivel=0.55)
            resultado = motor.ejecutar(Accion("volumen_subir"))

        indices = _indices_de(vtable)
        self.assertIn(17, indices,
                      "no se pidio VolumeStepUp (17): %r" % (indices,))
        self.assertNotIn(18, indices,
                         "pidio ademas VolumeStepDown (18): %r" % (indices,))
        self.assertIn(9, indices,
                      "no se releyo el nivel tras el paso: %r" % (indices,))
        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("55%", resultado.detalle)
        dobles["ctypes"].windll.ole32.CoUninitialize \
            .assert_called_once_with()

    def test_bajar_usa_volume_step_down(self):
        """indice 18 = VolumeStepDown, el paso contrario al de arriba"""
        with _doblado() as dobles:
            vtable = _audio_falso(dobles, nivel=0.44)
            resultado = motor.ejecutar(Accion("volumen_bajar"))

        indices = _indices_de(vtable)
        self.assertIn(18, indices,
                      "no se pidio VolumeStepDown (18): %r" % (indices,))
        self.assertNotIn(17, indices,
                         "pidio ademas VolumeStepUp (17): %r" % (indices,))
        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("44%", resultado.detalle)

    def test_silenciar_usa_set_mute(self):
        """indice 14 = SetMute: silenciar es inocua y reversible"""
        with _doblado() as dobles:
            vtable = _audio_falso(dobles)
            resultado = motor.ejecutar(Accion("silenciar"))

        self.assertIn(14, _indices_de(vtable),
                      "no se pidio SetMute (14): %r" % (_indices_de(vtable),))
        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("silenciado", resultado.detalle)

    def test_un_fallo_de_com_no_revienta_y_no_se_sigue(self):
        """CoInitializeEx con un codigo desconocido -> ok=False con su
        HRESULT legible, y NUNCA se llega a CoCreateInstance"""
        with _doblado() as dobles:
            dobles["ctypes"].windll.ole32.CoInitializeEx.return_value = \
                0x80004005
            resultado = motor.ejecutar(Accion("volumen_subir"))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("CoInitializeEx fallo", resultado.detalle)
        self.assertIn("0x80004005", resultado.detalle)
        dobles["ctypes"].windll.ole32.CoCreateInstance \
            .assert_not_called()

    def test_un_nivel_fuera_de_rango_no_se_anuncia_ok(self):
        """SANIDAD: el escalar tiene que quedar en [0, 1]; un 1.7 es un dato
        corrupto y decir "volumen 170%" seria mentir"""
        with _doblado() as dobles:
            _audio_falso(dobles, nivel=1.7)
            resultado = motor.ejecutar(Accion("volumen_subir"))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("fuera de rango", resultado.detalle)

    def test_volumen_leer_sigue_leyendo_tras_el_reordenamiento(self):
        """la accion de la FASE E1 rehizo su cadena sobre el helper nuevo y
        tiene que seguir devolviendo lo mismo: nivel y silencio leidos"""
        with _doblado() as dobles:
            _audio_falso(dobles, nivel=0.5, silencio=1)
            resultado = motor.ejecutar(Accion("volumen_leer"))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(
            resultado.detalle, "volumen 50%, silenciado",
            "volumen_leer cambio de texto: %r" % (resultado.detalle,))


class TestBrillo(unittest.TestCase):
    """powershell/WMI: valido si; clase VACIA no revienta; nada a ciegas"""

    def test_rango_valido_cambia_el_brillo_y_lo_dice(self):
        """el script echo 'OK' DESPUES de llamar WmiSetBrightness(0, 50)"""
        with _doblado() as dobles:
            _powershell_falso(dobles, salida="OK\r\n")
            resultado = motor.ejecutar(Accion("brillo", (50,)))

        comando = dobles["subprocess"].run.call_args.args[0]
        self.assertEqual(
            comando[0], "powershell",
            "brillo no corrio powershell sino %r" % (comando,))
        self.assertIn(
            "WmiSetBrightness(0, 50)", comando[-1],
            "powershell no trajo el nivel 50: %r" % (comando[-1],))
        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("50%", resultado.detalle)

    def test_clase_wmi_vacia_devuelve_ok_false_con_el_error_real(self):
        """ya no hay caso especial "VACIA": si powershell no dice OK, se
        devuelve su salida real como error. No se inventa un mensaje."""
        with _doblado() as dobles:
            _powershell_falso(dobles, salida="VACIA\r\n")
            resultado = motor.ejecutar(Accion("brillo", (80,)))

        self.assertFalse(
            resultado.ok,
            "con salida no-OK marco ok=True")
        self.assertIn("no confirmo el brillo: VACIA", resultado.detalle)

    def test_fuera_de_rango_no_corre_powershell(self):
        """0 a 100, numeros de verdad: todo lo demas se rechaza ANTES de
        llamar a powershell, con cero subprocess"""
        with _doblado() as dobles:
            for nivel in (150, -1, "cincuenta", None, True):
                with self.subTest(nivel=nivel):
                    resultado = motor.ejecutar(Accion("brillo", (nivel,)))
                    self.assertFalse(
                        resultado.ok,
                        "brillo %r devolvio ok=True" % (nivel,))
                    self.assertIn("rango", resultado.detalle)
                    dobles["subprocess"].run.assert_not_called()

    def test_powershell_que_revienta_no_es_exito(self):
        """exit != 0 levanta CalledProcessError y eso se traduce en
        ok=False, nunca en una excepcion hasta main.py"""
        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = \
                motor._ERROR_SUBPROCESO(1, ["powershell"])
            resultado = motor.ejecutar(Accion("brillo", (50,)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("rechazo", resultado.detalle)

    def test_sin_ok_explisito_en_la_salida_no_dice_exito(self):
        """powershell salio con 0 pero NO dijo 'OK': no se confia"""
        with _doblado() as dobles:
            _powershell_falso(dobles, salida="")
            resultado = motor.ejecutar(Accion("brillo", (50,)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no confirmo", resultado.detalle)


class TestCancelaYBloquea(unittest.TestCase):
    """cancelar_apagado y bloquear: inocuas, sin pedir confirmacion"""

    def test_cancelar_apagado_manda_shutdown_a(self):
        """shutdown /a solo DESHACE: por eso esta accion es inocua"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("cancelar_apagado"))

        dobles["subprocess"].run.assert_called_once_with(
            ["shutdown", "/a"], check=True, capture_output=True, text=True)
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_cancelar_sin_pendiente_no_revienta(self):
        """shutdown /a sin nada que cancelar sale con error: ok=False con
        su motivo, y ninguna excepcion se escapa"""
        with _doblado() as dobles:
            dobles["subprocess"].run.side_effect = \
                motor._ERROR_SUBPROCESO(1, ["shutdown", "/a"])
            resultado = motor.ejecutar(Accion("cancelar_apagado"))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no hay ningun apagado pendiente", resultado.detalle)

    def test_bloquear_manda_rundll32_con_lock_work_station(self):
        """rundll32 user32,LockWorkStation: candado SIN perder trabajo"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("bloquear"))

        dobles["subprocess"].run.assert_called_once_with(
            ["rundll32", "user32,LockWorkStation"], check=True,
            capture_output=True, text=True)
        dobles["ctypes"].windll.powrprof.SetSuspendState \
            .assert_not_called()
        self.assertTrue(resultado.ok, resultado.detalle)


class TestCapturaDePantalla(unittest.TestCase):
    """capturar_pantalla: GDI doblado, y el BMP que sale es de verdad"""

    def test_sin_carpeta_no_enciende_gdi_ni_subprocess(self):
        """destino vacio -> ok=False y ni GetDC, ni BitBlt, ni powershell"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("capturar_pantalla", ("",)))

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("carpeta", resultado.detalle)
        dobles["ctypes"].windll.user32.GetDC.assert_not_called()
        dobles["ctypes"].windll.gdi32.BitBlt.assert_not_called()
        dobles["subprocess"].run.assert_not_called()

    def test_escribe_un_bmp_completo_en_la_carpeta(self):
        """el unico archivo real de la suite: 70 bytes (14 + 40 + 16) con
        sus DOS cabeceras y los pixeles falsos en el orden del BMP"""
        with tempfile.TemporaryDirectory() as carpeta:
            with _doblado() as dobles:
                _gdi_falso(dobles)
                resultado = motor.ejecutar(
                    Accion("capturar_pantalla", (carpeta,)))
            # aqui el os REAL ya volvio: se mira la carpeta de verdad, y
            # DENTRO del with, porque al salir TemporaryDirectory la borra
            nombres = os.listdir(carpeta)
            self.assertTrue(resultado.ok, resultado.detalle)
            self.assertEqual(len(nombres), 1,
                             "archivos escritos: %r" % (nombres,))
            nombre = nombres[0]
            self.assertTrue(
                nombre.startswith("captura_") and nombre.endswith(".bmp"),
                "el archivo se llamo %r" % (nombre,))
            contenido = Path(carpeta, nombre).read_bytes()

        self.assertEqual(
            len(contenido), 14 + 40 + 16,
            "el BMP tiene %d bytes y tenia que tener 70" % len(contenido))
        self.assertEqual(contenido[:2], b"BM", "firma del BMP")
        self.assertEqual(
            contenido[2:6], struct.pack("<I", 70), "bfSize del BMP")
        self.assertEqual(
            contenido[10:14], struct.pack("<I", 54),
            "bfOffBits: los pixeles empiezan despues de las dos cabeceras")
        self.assertEqual(
            contenido[14:18], struct.pack("<I", 40), "biSize de la info")
        self.assertEqual(
            contenido[54:], PIXELES_FALSOS,
            "los pixeles no llegaron enteros ni en su orden")

    def test_sin_exitos_de_gdi_no_escribe_nada(self):
        """GetDIBits devuelve 0 filas -> ok=False y la carpeta queda VACIA:
        un BMP a medias peor que ninguno"""
        with tempfile.TemporaryDirectory() as carpeta:
            with _doblado() as dobles:
                _gdi_falso(dobles, filas_devueltas=0)
                resultado = motor.ejecutar(
                    Accion("capturar_pantalla", (carpeta,)))
            nombres = os.listdir(carpeta)

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("GetDIBits", resultado.detalle)
        self.assertEqual(nombres, [], "escribio algo sin pixels: %r"
                         % (nombres,))

    def test_getdc_vacio_no_revienta(self):
        """sin pantalla no hay captura: ok=False sin excepcion"""
        with tempfile.TemporaryDirectory() as carpeta:
            with _doblado() as dobles:
                _gdi_falso(dobles)
                dobles["ctypes"].windll.user32.GetDC.return_value = 0
                resultado = motor.ejecutar(
                    Accion("capturar_pantalla", (carpeta,)))
            nombres = os.listdir(carpeta)

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("GetDC", resultado.detalle)
        self.assertEqual(nombres, [])

    def test_crea_la_carpeta_faltante_y_luego_guarda_la_captura(self):
        """MEDIDO en vivo el 2026-10-23: carpeta inexistente -> [Errno 2]
        DESPUES de capturar la pantalla, perdiendo el trabajo. Ahora la
        carpeta destino se CREA ANTES (aqui con el os REAL: solo ctypes y
        subprocess doblados)"""
        with tempfile.TemporaryDirectory() as base:
            carpeta = os.path.join(base, "capturas_jarvis")  # NO existe
            with _solo_gdi_y_subprocess() as dobles:
                _gdi_falso(dobles)
                resultado = motor.ejecutar(
                    Accion("capturar_pantalla", (carpeta,)))
            self.assertTrue(
                os.path.isdir(carpeta),
                "la carpeta destino no se creo: %r" % (carpeta,))
            nombres = os.listdir(carpeta)

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertEqual(len(nombres), 1,
                         "archivos escritos: %r" % (nombres,))
        self.assertTrue(
            nombres[0].startswith("captura_") and nombres[0].endswith(".bmp"),
            "el archivo se llamo %r" % (nombres[0],))

    def test_una_ruta_imposible_no_captura_ni_escribe(self):
        """MEDIDO en vivo el 2026-10-23: una carpeta que no se puede crear
        hacia que el GDI capturara la pantalla COMPLETA y DESPUES fallara
        al escribir. Ahora ok=False ANTES de capturar: cero GetDC, cero
        BitBlt y cero escritura (os REAL para medir el rechazo de Windows;
        caracteres ilegales siempre los rechaza, sin depender de unidades
        que puedan existir o no)"""
        with tempfile.TemporaryDirectory() as base:
            imposible = os.path.join(base, "carpeta|ilegal")
            with _solo_gdi_y_subprocess() as dobles:
                _gdi_falso(dobles)
                resultado = motor.ejecutar(
                    Accion("capturar_pantalla", (imposible,)))
            quedo = os.path.exists(imposible)

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("carpeta", resultado.detalle)
        dobles["ctypes"].windll.user32.GetDC.assert_not_called()
        dobles["ctypes"].windll.gdi32.BitBlt.assert_not_called()
        self.assertFalse(quedo, "se creo la carpeta ilegal")

    def test_un_archivo_como_destino_no_se_usa_como_carpeta(self):
        """pasar un ARCHIVO por error (medido en vivo el 2026-10-23: lo
        trataba como carpeta y fallaba igual DESPUES de capturar) ->
        ok=False ANTES de capturar: destino es SIEMPRE carpeta, sin
        ambiguedad, y el archivo de Edward queda intacto"""
        with tempfile.TemporaryDirectory() as base:
            archivo = os.path.join(base, "no_soy_carpeta.txt")
            with open(archivo, "w", encoding="utf-8") as handle:
                handle.write("intacto")
            with _solo_gdi_y_subprocess() as dobles:
                _gdi_falso(dobles)
                resultado = motor.ejecutar(
                    Accion("capturar_pantalla", (archivo,)))
            contenido = Path(archivo).read_text(encoding="utf-8")

        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("carpeta", resultado.detalle)
        dobles["ctypes"].windll.user32.GetDC.assert_not_called()
        self.assertEqual(contenido, "intacto",
                         "el archivo destino fue tocado")


class TestOrdenesDelEquipo(unittest.TestCase):
    """cada frase nueva mapea a su accion correcta, sin ejecutar nada"""

    def test_las_ordenes_mapean_a_su_accion(self):
        casos = (
            # energia destructiva: sin argumentos (el freno es del motor)
            ("apaga la computadora", "apagar", ()),
            ("apagar la computadora", "apagar", ()),
            ("apaga el pc", "apagar", ()),
            ("reinicia el equipo", "reiniciar", ()),
            ("no te apagues", "cancelar_apagado", ()),
            ("cancela el apagado", "cancelar_apagado", ()),
            ("bloquea la pantalla", "bloquear", ()),
            ("ponse en suspencion", "suspender", ()),
            ("ponse en suspension", "suspender", ()),
            # volumen: subir, bajar y silenciar, con sus variantes dichas
            ("sube el volumen", "volumen_subir", ()),
            ("subele", "volumen_subir", ()),
            ("baja el volumen", "volumen_bajar", ()),
            ("bajita", "volumen_bajar", ()),
            ("silencia", "silenciar", ()),
            # brillo: el numero de la frase viaja como argumento
            ("pon el brillo a 50", "brillo", (50,)),
            ("pon el brillo 75", "brillo", (75,)),
            # captura: la raiz viaja como alias, el modulo es puro
            ("captura la pantalla", "capturar_pantalla", ("escritorio",)),
            ("hazme una captura", "capturar_pantalla", ("escritorio",)),
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

    def test_las_destructivas_reconocidas_piden_confirmacion(self):
        """EL CASO NUEVO: "apaga la computadora" HOY se iba a la IA; ahora
        se reconoce Y el motor la frena (REQUIERE_CONFIRMACION) con CERO
        llamadas al sistema, que es lo que main.py traduce en preguntar"""
        frases = ("apaga la computadora", "apagar la computadora",
                  "apaga el pc", "reinicia el equipo", "ponse en suspencion")
        with _doblado() as dobles:
            for frase in frases:
                with self.subTest(frase=frase):
                    acciones = interpretar(frase)
                    self.assertIn(
                        acciones[0].nombre, DESTRUCTIVAS_NUEVAS,
                        "%r no reconocio una destructiva: %r"
                        % (frase, acciones))
                    resultado = motor.ejecutar(acciones[0])
                    self.assertEqual(
                        resultado.detalle, motor.REQUIERE_CONFIRMACION,
                        "%r no pido confirmacion: %r"
                        % (frase, resultado.detalle))
                    self.assertFalse(resultado.ok)
            self.assertEqual(
                _sucias(dobles), {},
                "una destructiva nueva toco el sistema sin confirmar: %s"
                % _sucias(dobles))

    def test_pon_el_brillo_sin_numero_va_a_la_ia(self):
        """sin numero no se inventa NINGUNO: la frase sigue su camino"""
        for texto in ("pon el brillo", "pon el brillo a cincuenta"):
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones],
                    ["consultar_ia"],
                    "%r disparo una accion sin numero: %r"
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
            ("pon spotify", "abrir",
             ("Spotify", "https://www.spotify.com")),
            ("busca mi factura en documentos",
             "buscar_archivo", ("mi factura", "documentos")),
            ("abre mi escritorio", "abrir_archivo", ("escritorio",)),
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
