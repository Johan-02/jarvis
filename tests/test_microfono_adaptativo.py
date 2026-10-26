# pruebas del UMBRAL ADAPTATIVO y de la AMPLIFICACION del audio (2026-10-26)
#
# EL DEFECTO QUE MIDEN ESTAS PRUEBAS: Jarvis no oia a Edward. Medido con su
# microfono real, 8 segundos de audio (media de np.abs() por bloque de 0.5 s,
# int16):
#     ruido de la habitacion : 667 - 920
#     cuando Edward habla   : 900 - 1294  (maximo de todo el archivo 1294.5)
#     umbral de Jarvis      : 1300 fijo  <-- POR ENCIMA DE SU VOZ
# Como su voz casi nunca pasaba de 1300, grabar_hasta_silencio creia que habia
# silencio desde el primer bloque y cortaba a los 2 segundos, a media frase.
# Google transcribia "Juana" en vez de "cuanta ram" y "es facil disco" en vez
# de "espacio en disco". El microfono SI sirve: el umbral estaba mal calibrado.
#
# REGLAS DE ESTE ARCHIVO: cero microfono real y cero red. sd.rec y sd.wait van
# doblados y devuelven bloques INVENTADOS con los numeros medidos arriba;
# recognize_google va doblado. Ninguna prueba llama a main/main.py.
#
# LOS TRES NUMEROS QUE SE MIDE (con la mutacion correspondiente comprobada):
#   1. ruido 800 + voz 1200: la grabacion NO se corta en el primer bloque de
#      voz y sigue. Con el umbral fijo de 1300 se cortaba a los 2 s.
#   2. _audio_mas_fuerte sube un audio de pico 1000 (sin pasar del tope x8) y
#      devuelve TAL CUAL uno de pico 50 (eso es silencio, no voz).
#   3. escuchar_confirmacion sigue devolviendo None con UnknownValueError: la
#      regla de seguridad de la FASE G4 no se toca.

import io
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


# tope de seguridad de las pruebas: si la grabacion llega a este numero de
# bloques el defecto es un bucle sin corte, y se falla con asercion en vez de
# colgar la suite entera (un cuelgue no es una prueba)
BLOQUES_DE_CORTE = 200


def _bloque(valor, frecuencia):
    """un bloque de 0.5 s con TODAS las muestras al mismo nivel

    La media de np.abs() de este bloque es exactamente `valor`, que es la
    medida que usa el codigo (el ruido medido de Edward fue 800, su voz 1200).
    """
    return np.full((int(0.5 * frecuencia), 1), valor, dtype="int16")


def _serie_de_niveles(niveles, frecuencia, contador=None):
    """doble de sd.rec que devuelve los niveles de `niveles` en orden

    Cuando se acaba la lista repite el ULTIMO nivel (asi una grabacion que se
    alarga no revienta por falta de datos) y a partir de BLOQUES_DE_CORTE
    levanta AssertionError: es la red que convierte un bucle infinito en un
    fallo con mensaje.
    """
    estado = {"i": 0}

    def _rec(*argumentos, **opciones):
        if contador is not None:
            contador["llamadas"] = contador.get("llamadas", 0) + 1
            if contador["llamadas"] > BLOQUES_DE_CORTE:
                raise AssertionError(
                    "grabar_hasta_silencio lleva mas de %d bloques: no corta "
                    "ni por silencio ni por el tope" % BLOQUES_DE_CORTE)
        posicion = estado["i"]
        if posicion < len(niveles):
            estado["i"] = posicion + 1
        nivel = niveles[min(posicion, len(niveles) - 1)]
        return _bloque(nivel, frecuencia)

    return _rec


def _audio_de_pico(pico, muestras=1000):
    """audio int16 que alterna +pico y -pico, con ceros en medio

    Los ceros importan: son los que destapan una ganancia infinita (inf * 0 no
    es 0, es NaN) y los que se comen un recorte mal hecho.
    """
    audio = np.zeros(muestras, dtype="int16")
    audio[::2] = pico
    audio[1::2] = -pico
    return audio


def _pico(audio):
    """pico real del audio, en int de Python (np.abs(-32768) desborda)"""
    return max(abs(int(muestra)) for muestra in audio)


class TestUmbralAdaptativo(unittest.TestCase):
    """el umbral se MIDE en la habitacion en vez de venir fijo a 1300"""

    def setUp(self):
        self.main = _main()

    def test_umbral_con_el_ruido_de_edward(self):
        """ruido 800 -> umbral 1080: encima del ruido y DEBAJO de su voz"""
        umbral = self.main.calcular_umbral_silencio(800)

        self.assertAlmostEqual(
            umbral, 1080.0, places=6,
            msg="con ruido 800 el umbral tiene que ser max(250, 800*1.35) = "
                "1080, y salio %r" % (umbral,))
        self.assertGreater(
            umbral, 800,
            "el umbral no quedo por encima del ruido medido (%r): la "
            "grabacion cortaria con el ruido de la habitacion" % (umbral,))
        self.assertLess(
            umbral, 1294.5,
            "el umbral (%r) quedo por encima del maximo medido de la voz de "
            "Edward (1294.5): es exactamente el defecto del 1300 fijo"
            % (umbral,))

    def test_umbral_tiene_suelo_y_techo(self):
        """habitacion muda -> 250 (suelo); habitacion ruidosa -> 1300 (techo)"""
        self.assertEqual(
            self.main.calcular_umbral_silencio(10), 250.0,
            "con ruido casi nulo el umbral tiene que quedarse en el suelo de "
            "250, o el primer roce del microfono corta la grabacion")
        self.assertEqual(
            self.main.calcular_umbral_silencio(5000), 1300.0,
            "en una habitacion muy ruidosa el umbral no puede pasar del techo "
            "de 1300: por encima de ahi Jarvis es sordo de todas formas")

    def test_la_voz_de_edward_no_corta_la_grabacion(self):
        """ruido 800 + voz 1200: la grabacion SIGUE y corta por silencio

        La serie es la de Edward: un bloque de ruido para calibrar, dos de voz
        a 1200 y despues silencio de 800. Con el umbral adaptativo (1080) los
        bloques de voz reinician el contador de silencio y la grabacion dura
        4 s. Con el 1300 fijo se cortaba a los 2 s, a media frase.
        """
        frecuencia = self.main.frecuencia
        contador = {}
        niveles = [800, 800, 1200, 1200, 850, 800, 800, 800]
        consola = io.StringIO()
        with mock.patch.object(
                self.main.sd, "rec",
                mock.MagicMock(side_effect=_serie_de_niveles(
                    niveles, frecuencia, contador))), \
                mock.patch.object(self.main.sd, "wait", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            audio = self.main.grabar_hasta_silencio()

        self.assertEqual(
            contador["llamadas"], len(niveles),
            "la grabacion uso %d bloques y tenia que usar %d (corte a los 2 s "
            "de silencio, DESPUES de las voces)"
            % (contador["llamadas"], len(niveles)))
        self.assertGreater(
            len(audio), int(2 * frecuencia),
            "la grabacion se corto a los 2 segundos (%d muestras = %.1f s): "
            "sigue creyendo que la voz de Edward es silencio"
            % (len(audio), len(audio) / frecuencia))

        # el umbral que se uso tiene que quedar dicho, con su numero: sin esto
        # diagnosticar un corte obliga a adivinar
        self.assertIn(
            "1080", consola.getvalue(),
            "no se imprimio el umbral calculado (1080) con el ruido medido")
        self.assertIn(
            "800", consola.getvalue(),
            "no se imprimio el ruido medido (800) en el aviso del umbral")

    def test_silencio_de_700_corta_a_los_dos_segundos(self):
        """comportamiento de siempre: 4 bloques seguidos de silencio y corta"""
        frecuencia = self.main.frecuencia
        contador = {}
        consola = io.StringIO()
        with mock.patch.object(
                self.main.sd, "rec",
                mock.MagicMock(side_effect=_serie_de_niveles(
                    [700], frecuencia, contador))), \
                mock.patch.object(self.main.sd, "wait", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            audio = self.main.grabar_hasta_silencio()

        self.assertEqual(
            contador["llamadas"], 4,
            "con silencio de 700 (umbral 945) tenia que cortar en 4 bloques "
            "de 0.5 s = 2 s, y uso %d" % contador["llamadas"])
        self.assertEqual(
            len(audio), 4 * int(0.5 * frecuencia),
            "el audio devuelto no son los 2 segundos de silencio")

    def test_umbral_explicito_no_calibra_nada(self):
        """un umbral pasado a mano se usa tal cual y NO gasta captura extra"""
        frecuencia = self.main.frecuencia
        contador = {}
        consola = io.StringIO()
        with mock.patch.object(
                self.main.sd, "rec",
                mock.MagicMock(side_effect=_serie_de_niveles(
                    [5000], frecuencia, contador))), \
                mock.patch.object(self.main.sd, "wait", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            self.main.grabar_hasta_silencio(umbral_silencio=1300)

        # 20 s de tope / 0.5 s por bloque = 40 capturas, las MISMAS de antes
        self.assertEqual(
            contador["llamadas"], 40,
            "con umbral explicito las capturas tienen que ser las de siempre "
            "(40 para el tope de 20 s) y fueron %d" % contador["llamadas"])
        self.assertNotIn(
            "ruido medido", consola.getvalue(),
            "midio el ruido de la habitacion aunque se le paso un umbral "
            "explicito: no debe calibrar nada")
        self.assertIn(
            "aviso", consola.getvalue(),
            "el tope de 20 s corto sin avisar")


class TestAudioMasFuerte(unittest.TestCase):
    """el audio se amplifica antes de transcribir, sin desbordar"""

    def setUp(self):
        self.main = _main()

    def test_audio_flojo_sube_sin_pasar_del_tope(self):
        """pico 1000 sube, pero nunca mas de x8 (el tope documentado)"""
        audio = _audio_de_pico(1000)
        resultado = self.main._audio_mas_fuerte(audio)

        pico = _pico(resultado)
        self.assertGreater(
            pico, 1000,
            "el audio de pico 1000 no subio (pico %d): la amplificacion no se "
            "aplico" % pico)
        self.assertLessEqual(
            pico, 1000 * 8,
            "la ganancia paso del tope de x8 (pico %d): con el ruido de fondo "
            "amplificado el reconocedor entiende menos, no mas" % pico)
        self.assertEqual(
            resultado.dtype, np.dtype("int16"),
            "el audio amplificado tiene que seguir siendo int16: %r"
            % (resultado.dtype,))

    def test_audio_de_amplitud_50_se_devuelve_igual(self):
        """pico 50 es silencio, no voz: se devuelve TAL CUAL"""
        audio = _audio_de_pico(50)
        resultado = self.main._audio_mas_fuerte(audio)

        self.assertTrue(
            np.array_equal(resultado, audio),
            "un audio de pico 50 (silencio) no puede amplificarse: solo mete "
            "ruido en el reconocedor")

    def test_nunca_desborda_el_int16(self):
        """ni un audio medio amplificado ni uno al 91% se salen del int16"""
        # pico 2600 SI se amplifica (2600 * 8 = 20800): es la rama que
        # multiplica y la que podria pasarse de la escala
        medio = _audio_de_pico(2600)
        # pico 30000 ya viene fuerte: se devuelve igual, pero la invariante se
        # comprueba tambien aqui (es el audio mas facil de desbordar)
        fuerte = _audio_de_pico(30000)

        for audio in (medio, fuerte):
            resultado = self.main._audio_mas_fuerte(audio)
            self.assertLessEqual(
                _pico(resultado), 32767,
                "el resultado se paso de 32767 con un audio de pico %d: un "
                "int16 desbordado da la vuelta (positivo->negativo) y el "
                "audio entero queda hecho ruido" % _pico(audio))
            self.assertGreaterEqual(
                min(int(muestra) for muestra in resultado), -32768,
                "el resultado bajo de -32768: el audio se desbordo")
            self.assertEqual(
                resultado.dtype, np.dtype("int16"),
                "el resultado tiene que ser int16 y fue %r: con float u otro "
                "tipo, tobytes() manda basura al reconocedor"
                % (resultado.dtype,))


class TestEscucharConfirmacionSigueSiendoSegura(unittest.TestCase):
    """la regla de la FASE G4 no se toca: None significa RECHAZAR"""

    def setUp(self):
        self.main = _main()

    def _correr(self, audio, reconocedor):
        with mock.patch.object(
                self.main, "grabar_hasta_silencio",
                mock.MagicMock(return_value=audio)), \
                mock.patch.object(self.main, "r", reconocedor):
            return self.main.escuchar_confirmacion()

    def test_voz_ilegible_sigue_devolviendo_none(self):
        """UnknownValueError -> None, y None es RECHAZAR la accion destructiva"""
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.side_effect = (
            self.main.sr.UnknownValueError())

        texto = self._correr(_audio_de_pico(1000), reconocedor)

        self.assertIsNone(
            texto,
            "una voz ilegible tiene que seguir devolviendo None (RECHAZAR): "
            "%r" % (texto,))

    def test_el_audio_que_llega_a_google_va_amplificado(self):
        """el AudioData que recibe recognize_google tiene MAS energia

        Lo que se mide aqui es ENERGIA: la media de np.abs() del audio que sale
        hacia Google contra la del audio grabado. Si la amplificacion se
        quitara, las dos energias serian iguales y la asercion fallaria.
        """
        audio = _audio_de_pico(1000)
        energia_original = np.abs(audio.astype(np.float64)).mean()

        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = "si"
        texto = self._correr(audio, reconocedor)

        self.assertEqual(texto, "si", "no devolvio el texto reconocido")
        datos = reconocedor.recognize_google.call_args.args[0]
        enviado = np.frombuffer(datos.get_raw_data(), dtype=np.int16)
        energia_enviada = np.abs(enviado.astype(np.float64)).mean()

        self.assertGreater(
            energia_enviada, energia_original,
            "el audio que se manda a Google tiene la MISMA energia que la "
            "grabacion (%.1f vs %.1f): no se amplifico nada y el microfono "
            "bajo de Edward sigue sin oirse" % (energia_enviada,
                                                energia_original))


if __name__ == "__main__":
    unittest.main()
