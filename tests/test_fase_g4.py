# pruebas de la FASE G4 del proyecto Jarvis (2026-10-23): la confirmacion
# destructiva se escucha por MICROFONO, no por teclado.
#
# Frentes:
#   1. escuchar_confirmacion(): la funcion NUEVA que graba por microfono
#      (mismo patron de siempre: grabar_hasta_silencio + recognize_google
#      es-ES) y devuelve el texto entendido, o None si no se entiende;
#   2. EL DEFAULT: bucle_agente y atender ya NO tienen input como
#      escuchar por defecto; su default es escuchar_confirmacion. OJO:
#      procesar_orden llama a atender SIN escuchar, por eso el default de
#      atender tambien tiene que ser el microfono (si no, la confirmacion
#      se teclearia aunque bucle_agente tuviera el microfono por defecto);
#   3. LA REGLA DE SEGURIDAD: si la escucha FALLA (None por silencio,
#      UnknownValueError, sin microfono, sin internet RequestError, o se
#      agotan los reintentos), el resultado es RECHAZAR, NUNCA aceptar.
#      Un silencio JAMAS se interpreta como "si". Los reintentos son
#      LIMITADOS (TOPE_REINTENTOS_CONFIRMACION = 2) y al agotarse se
#      rechaza y se dice claramente.
#
# REGLAS DE ESTE ARCHIVO: microfono (grabar_hasta_silencio), reconocedor
# (r y recognize_google), motor, voz e IA van SIEMPRE doblados: NADA de
# audio real, cero llamadas a la API, nada de apagar nada.

import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# el doble de las librerias de audio vive en test_fase_b_main; se reutiliza
# para poder importar main.main sin microfono ni red
try:
    from tests.test_fase_b_main import _main
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_b_main import _main

# los dobles de respuesta del modelo (texto, tool_use, respuesta y cliente)
# viven en test_fase_g2; se reutilizan para no duplicarlos
try:
    from tests.test_fase_g2 import (_texto, _herramienta, _respuesta,
                                    _cliente, _ultimo_tool_result)
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_g2 import (_texto, _herramienta, _respuesta, _cliente,
                              _ultimo_tool_result)

from main import motor

# memoria falsa: estas pruebas NUNCA leen ni escriben el memoria.json real
MEMORIA_FALSA = {"nombre_usuario": "Yohan", "notas": ["aprende django"]}

# la frase destructiva de todas las pruebas de confirmacion
FRASE_DESTRUCTIVA = "apaga la computadora"


def _cliente_destructiva():
    """cliente falso: el modelo pide apagar y luego cierra con texto"""
    return _cliente([
        _respuesta([_herramienta("apagar", {}, "t_apagar")], "tool_use"),
        _respuesta([_texto("apagando en 30 segundos.")], "end_turn"),
    ])


def _ejecutar_falso(accion, confirmado=False):
    """motor falso: FREN sin confirmar y ejecuta con confirmado=True"""
    if not confirmado:
        return motor.Resultado(ok=False, detalle=motor.REQUIERE_CONFIRMACION)
    return motor.Resultado(ok=True,
                           detalle="apagando la computadora en 30 segundos")


def _correr_bucle(main, reconocedor=None, grabar=None, **kwargs):
    """corre atender sobre la frase destructiva, con TODO doblado

    Si NO se pasa `escuchar` en kwargs, atender usa su DEFAULT: el
    microfono de escuchar_confirmacion, alimentado aqui por los mocks
    `grabar` (grabar_hasta_silencio) y `reconocedor` (el objeto r con su
    recognize_google). Asi los tests de voz prueban EL CAMINO REAL, no una
    inyeccion. Devuelve (texto, voz, ejecutar, cliente).
    """
    cliente = _cliente_destructiva()
    voz = mock.MagicMock()
    if reconocedor is None:
        reconocedor = mock.MagicMock()
    if grabar is None:
        grabar = mock.MagicMock()
    with mock.patch.object(main, "clave_ia", "clave-falsa"), \
            mock.patch.object(main, "_obtener_cliente_ia",
                              mock.MagicMock(return_value=cliente)), \
            mock.patch.object(main, "hablar", voz), \
            mock.patch.object(main, "grabar_hasta_silencio", grabar), \
            mock.patch.object(main, "r", reconocedor), \
            mock.patch.object(main.motor, "ejecutar",
                              mock.MagicMock(
                                  side_effect=_ejecutar_falso)) as ejecutar:
        texto = main.atender(FRASE_DESTRUCTIVA, memoria=dict(MEMORIA_FALSA),
                             hablar=voz, **kwargs)
    return texto, voz, ejecutar, cliente


def _reconocedor(resultado=None, error=None):
    """reconocedor falso: devuelve `resultado` o lanza `error`"""
    reconocedor = mock.MagicMock()
    if error is not None:
        reconocedor.recognize_google.side_effect = error
    else:
        reconocedor.recognize_google.return_value = resultado
    return reconocedor


def _grabacion():
    """grabacion falsa: un audio corto de ceros, como si hubiera silencio"""
    return mock.MagicMock(return_value=np.zeros(10, dtype="int16"))


def _frases_habladas(voz, fragmento):
    """cuantas llamadas a hablar trajeron `fragmento`"""
    return sum(1 for llamada in voz.call_args_list
               if fragmento in str(llamada.args[0]))


class TestEscucharConfirmacion(unittest.TestCase):
    """la funcion NUEVA: microfono y reconocedor doblados, cero audio real"""

    def setUp(self):
        self.main = _main()

    def _correr(self, grabar, reconocedor):
        main = self.main
        with mock.patch.object(main, "grabar_hasta_silencio", grabar), \
                mock.patch.object(main, "r", reconocedor):
            return main.escuchar_confirmacion()

    def test_devuelve_el_texto_entendido(self):
        """grabar + recognize_google es-ES -> el texto tal cual, sin wav"""
        texto = self._correr(_grabacion(), _reconocedor(resultado="si"))

        self.assertEqual(
            texto, "si",
            "escuchar_confirmacion no devolvio el texto reconocido: %r"
            % (texto,))

    def test_usa_la_frecuencia_del_modulo_y_es_es(self):
        """usa la frecuencia del modulo y pide es-ES al reconocedor"""
        main = self.main
        grabar = _grabacion()
        reconocedor = _reconocedor(resultado="no")
        self._correr(grabar, reconocedor)

        self.assertEqual(grabar.call_count, 1,
                         "no grabo UNA sola vez")
        self.assertEqual(grabar.call_args.args, (main.frecuencia,),
                         "no grabo con la frecuencia del modulo: %r"
                         % (grabar.call_args.args,))
        idioma = reconocedor.recognize_google.call_args.kwargs.get("language")
        self.assertEqual(
            idioma, "es-ES",
            "no pidio el reconocimiento en es-ES: %r" % (idioma,))

    def test_voz_ilegible_devuelve_none(self):
        """UnknownValueError (silencio, ruido) -> None, nunca texto"""
        texto = self._correr(
            _grabacion(),
            _reconocedor(error=self.main.sr.UnknownValueError()))

        self.assertIsNone(
            texto,
            "una voz ilegible tiene que devolver None: %r" % (texto,))

    def test_sin_internet_devuelve_none(self):
        """RequestError (sin internet) -> None, sin reventar el programa"""
        texto = self._correr(
            _grabacion(),
            _reconocedor(error=self.main.sr.RequestError("peticion", "r")))

        self.assertIsNone(
            texto,
            "sin internet la confirmacion tiene que ser None: %r" % (texto,))

    def test_sin_microfono_devuelve_none(self):
        """grabar_hasta_silencio revientando (no hay microfono) -> None"""
        grabar = mock.MagicMock(side_effect=RuntimeError("no hay microfono"))
        texto = self._correr(grabar, _reconocedor())

        self.assertIsNone(
            texto,
            "sin microfono la confirmacion tiene que ser None: %r" % (texto,))


class TestElDefaultEsElMicrofono(unittest.TestCase):
    """ni bucle_agente ni atender pueden tener input como escuchar default"""

    def setUp(self):
        self.main = _main()

    def test_el_escuchar_por_defecto_es_escuchar_confirmacion(self):
        """el default de las DOS funciones es la de MICROFONO, no input

        atender entra en la lista adrede: procesar_orden la llama SIN
        escuchar y ella le pasa el suyo a bucle_agente, asi que si su
        default fuera input la confirmacion se teclearia en el uso real
        aunque bucle_agente estuviera bien.
        """
        main = self.main
        for funcion in (main.bucle_agente, main.atender):
            with self.subTest(funcion=funcion.__name__):
                default = inspect.signature(
                    funcion).parameters["escuchar"].default
                self.assertIs(
                    default, main.escuchar_confirmacion,
                    "el escuchar por defecto de %s no es el microfono: %r"
                    % (funcion.__name__, default))
                self.assertIsNot(
                    default, input,
                    "el escuchar por defecto de %s es input: la "
                    "confirmacion se teclearia por accidente"
                    % funcion.__name__)


class TestConfirmacionPorVozEnElBucle(unittest.TestCase):
    """el flujo completo por el CAMINO REAL (default = microfono)"""

    def setUp(self):
        self.main = _main()

    def test_si_se_ejecuta_con_confirmado_true(self):
        """"si" reconocido -> el motor se llama 2 veces y la 2da confirma"""
        main = self.main
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, reconocedor=_reconocedor(resultado="si"),
            grabar=_grabacion())

        self.assertEqual(
            ejecutar.call_count, 2,
            "el motor se llamo %d veces: tenia que ser 2 (freno + "
            "confirmada)" % ejecutar.call_count)
        self.assertFalse(
            ejecutar.call_args_list[0].kwargs.get("confirmado", False),
            "la primera llamada ya vino confirmada: nadie pregunto aun")
        self.assertTrue(
            ejecutar.call_args_list[1].kwargs.get("confirmado"),
            "el si de Edward no llego al motor como confirmado=True")
        self.assertIn(
            "apagando la computadora en 30 segundos",
            _ultimo_tool_result(cliente)["content"],
            "la ejecucion confirmada no volvio al modelo")
        self.assertEqual(texto, "apagando en 30 segundos.")

    def test_no_no_se_ejecuta(self):
        """"no" reconocido -> el motor se llamo SOLO la vez del freno"""
        main = self.main
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, reconocedor=_reconocedor(resultado="no"),
            grabar=_grabacion())

        self.assertEqual(
            ejecutar.call_count, 1,
            "el motor se llamo %d veces con un no: solo puede ser 1, la "
            "del freno" % ejecutar.call_count)
        self.assertFalse(
            ejecutar.call_args.kwargs.get("confirmado", False),
            "el motor fue llamado con confirmado=True tras un no")
        self.assertIn("rechazo", _ultimo_tool_result(cliente)["content"])
        self.assertEqual(texto, "apagando en 30 segundos.")

    def test_silencio_none_se_rechaza(self):
        """REGLA DE SEGURIDAD: silencio que da None -> NUNCA se acepta

        La mutacion que mata: hacer que _acepta_confirmacion(None) sea
        True (o que el bucle acepte None). Ahi un silencio ejecutaria la
        accion destructiva y esta prueba revienta: el motor se llamaria 2
        veces y la 2da con confirmado=True.
        """
        main = self.main
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, reconocedor=_reconocedor(error=main.sr.UnknownValueError()),
            grabar=_grabacion())

        self.assertEqual(
            ejecutar.call_count, 1,
            "el silencio ACEPTO: el motor se llamo %d veces (tenia que "
            "ser 1, solo el freno)" % ejecutar.call_count)
        self.assertTrue(
            _ultimo_tool_result(cliente)["is_error"],
            "el rechazo por silencio no volvio marcado como error")
        self.assertIn(
            "NO se ejecuto", _ultimo_tool_result(cliente)["content"],
            "al modelo no se le dijo claramente que NO se ejecuto nada")
        self.assertEqual(texto, "apagando en 30 segundos.")

    def test_voz_ilegible_se_rechaza(self):
        """UnknownValueError -> se RECHAZA, sin ejecutar nada"""
        main = self.main
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, reconocedor=_reconocedor(error=main.sr.UnknownValueError()),
            grabar=_grabacion())

        self.assertEqual(
            ejecutar.call_count, 1,
            "una voz ilegible ejecuto la accion: el motor se llamo "
            "%d veces" % ejecutar.call_count)
        self.assertIn(
            "NO se ejecuto", _ultimo_tool_result(cliente)["content"])

    def test_sin_internet_se_rechaza(self):
        """RequestError (sin internet) -> se RECHAZA, sin ejecutar nada"""
        main = self.main
        texto, voz, ejecutar, cliente = _correr_bucle(
            main,
            reconocedor=_reconocedor(
                error=main.sr.RequestError("peticion", "r")),
            grabar=_grabacion())

        self.assertEqual(
            ejecutar.call_count, 1,
            "sin internet se ejecuto la accion: el motor se llamo %d "
            "veces" % ejecutar.call_count)
        self.assertIn(
            "NO se ejecuto", _ultimo_tool_result(cliente)["content"])

    def test_sin_microfono_se_rechaza(self):
        """grabar revientando (no hay microfono) -> se RECHAZA"""
        main = self.main
        grabar = mock.MagicMock(side_effect=RuntimeError("no hay microfono"))
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, reconocedor=_reconocedor(), grabar=grabar)

        self.assertEqual(
            ejecutar.call_count, 1,
            "sin microfono se ejecuto la accion: el motor se llamo %d "
            "veces" % ejecutar.call_count)
        self.assertIn(
            "NO se ejecuto", _ultimo_tool_result(cliente)["content"])

    def test_reintentos_agotados_se_rechaza_y_se_avisa(self):
        """el tope de reintentos existe: se RECHAZA y se dice claramente

        La mutacion que mata: quitar el tope (range(10) o un while True).
        Ahi escuchar se llamaria mas de 1+TOPE_REINTENTOS_CONFIRMACION
        veces y la cuenta de abajo revienta. Y el bucle TERMINA (el texto
        final llega): sin tope esto seria un bucle eterno.
        """
        main = self.main
        escucha = mock.MagicMock(return_value=None)
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, escuchar=escucha)

        self.assertEqual(
            escucha.call_count, main.TOPE_REINTENTOS_CONFIRMACION + 1,
            "los reintentos no estan limitados a %d: escuchar se llamo "
            "%d veces" % (main.TOPE_REINTENTOS_CONFIRMACION,
                          escucha.call_count))
        self.assertEqual(
            ejecutar.call_count, 1,
            "tras agotar reintentos el motor se llamo %d veces: tenia "
            "que ser 1, solo el freno" % ejecutar.call_count)
        self.assertEqual(
            _frases_habladas(voz, main.PREGUNTA_REINTENTO),
            main.TOPE_REINTENTOS_CONFIRMACION,
            "no se repitio la pregunta en cada reintento")
        self.assertEqual(
            _frases_habladas(voz, main.AVISO_RECHAZO_ESCUCHA), 1,
            "no se aviso VOZ de que la accion quedo sin ejecutar")
        self.assertIn(
            "NO se ejecuto", _ultimo_tool_result(cliente)["content"],
            "al modelo no se le dijo que la accion NO se ejecuto")
        self.assertEqual(
            texto, "apagando en 30 segundos.",
            "tras el rechazo el bucle no siguio hasta el texto final")

    def test_reintento_exitoso_se_ejecuta(self):
        """None la 1ra vez, "si" en el reintento -> se ejecuta igual"""
        main = self.main
        escucha = mock.MagicMock(side_effect=[None, "si"])
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, escuchar=escucha)

        self.assertEqual(
            escucha.call_count, 2,
            "no se reintento la escucha: %d llamadas" % escucha.call_count)
        self.assertEqual(
            ejecutar.call_count, 2,
            "tras un si en el reintento el motor no se confirmo")
        self.assertTrue(
            ejecutar.call_args_list[1].kwargs.get("confirmado"),
            "el si del reintento no llego como confirmado=True")
        self.assertEqual(
            _frases_habladas(voz, main.PREGUNTA_REINTENTO), 1,
            "no se repitio la pregunta antes del reintento")
        self.assertEqual(texto, "apagando en 30 segundos.")

    def test_sigue_siendo_inyectable_con_doble_de_texto(self):
        """con escuchar= un doble de texto puro sigue funcionando

        Es la inyeccion de las pruebas de G2/G3: escuchar devolviendo
        "hazlo" (una palabra de FRASES_ACEPTAR) tiene que confirmar.
        """
        main = self.main
        escucha = mock.MagicMock(return_value="hazlo")
        texto, voz, ejecutar, cliente = _correr_bucle(
            main, escuchar=escucha)

        escucha.assert_called_once_with()
        self.assertEqual(
            ejecutar.call_count, 2,
            "el doble de texto no llego a confirmar: motor %d llamadas"
            % ejecutar.call_count)
        self.assertTrue(
            ejecutar.call_args_list[1].kwargs.get("confirmado"),
            "la aceptacion del doble de texto no confirmo la accion")
        self.assertEqual(texto, "apagando en 30 segundos.")


if __name__ == "__main__":
    unittest.main()
