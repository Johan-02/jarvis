# pruebas del ARREGLO DE CAIDA SIN INTERNET (2026-10-25)
#
# El defecto medido: hablar() tenia try/finally pero NINGUN except, y
# procesar_orden() tampoco atrapaba sr.RequestError. edge_tts (ConnectionError)
# y recognize_google (sr.RequestError) necesitan RED; con un corte de wifi la
# excepcion se propagaba y Jarvis MORIA en la primera frase. Medicion de campo:
#   procesar_orden  llama a hablar: si   tiene except: NO   <- CRASH
#
# El arreglo (main/main.py):
#   - hablar() absorbe su propio fallo de sintetizacion/reproduccion con
#     `except Exception`, imprimiendo TIPO y MENSAJE (nunca en silencio) y
#     devolviendo normal. OJO: `except Exception`, NO `except:` a secas, para
#     que KeyboardInterrupt (BaseException, Ctrl+C) SIGA SALIENDO (regla B3).
#   - procesar_orden() atrapa sr.RequestError con un aviso HONESTO y CONTINUA
#     el bucle (vuelve a escuchar), sin morir ni quedarse colgado.
#
# REGLAS DE ESTE ARCHIVO: cero red, cero microfono, cero audio real. Todo va
# con dobles (edge_tts, playsound, sd, sr, hablar). Ninguna prueba toca el
# memoria.json real de Edward ni main/main.py.

import io
import os
import sys
import types
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


# tope de reintentos SIN INTERNET en la prueba de procesar_orden: se permiten
# TOPE errores de red (cada uno tiene que ser ATRAPADO y el bucle tiene que
# seguir), y en la llamada siguiente se lanza una excepcion propia para salir
# del bucle y poder medir. Sin este tope la prueba se colgaria para siempre
# (un `while True` que no muere), que es un cuelgue, no una prueba.
TOPE_SIN_INTERNET = 3


class _FinDeLaPrueba(Exception):
    """excepcion propia para salir del bucle de procesar_orden tras medir"""


class TestHablarNoMuere(unittest.TestCase):
    """hablar() absorbe su propio fallo de sintetizacion/reproduccion"""

    def test_edge_tts_roto_no_mata_y_limpia_el_mp3(self):
        """sin internet (edge_tts ConnectionError) hablar NO lanza y limpia

        Se mide con edge_tts doblado: su save() escribe el mp3 de verdad (para
        poder medir que se borra) y DESPUES revienta con ConnectionError, que
        es lo que pasa sin red. Con el defecto (sin except) la excepcion se
        propagaba y la prueba fallaria. Con el arreglo: NO lanza, imprime
        aviso con tipo y mensaje, y el mp3 queda limpio en el temp.
        """
        main = _main()
        rutas = []

        class _CommunicateRoto:
            def __init__(self, *argumentos, **opciones):
                pass

            async def save(self, ruta):
                # escribe el mp3 (para medir el borrado) y luego revienta
                rutas.append(ruta)
                with open(ruta, "wb") as archivo:
                    archivo.write(b"mp3 falso")
                raise ConnectionError(
                    "sin internet: no pude sintetizar la voz")

        dobles = types.SimpleNamespace(Communicate=_CommunicateRoto)
        consola = io.StringIO()
        with mock.patch.object(main, "edge_tts", dobles), \
                mock.patch.object(main, "playsound", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            try:
                main.hablar("hola")
            except Exception as exc:
                self.fail(
                    "hablar lanzo %s: %s con edge_tts roto (sin internet): "
                    "tiene que absorber su propio fallo y seguir"
                    % (type(exc).__name__, exc))

        salida = consola.getvalue()
        self.assertIn(
            "aviso", salida,
            "hablar fallo con edge_tts roto y NO aviso en consola: se trago "
            "el error en silencio")
        self.assertIn(
            "no pude hablar", salida,
            "el aviso de hablar no dice que no pudo hablar: %r" % salida)
        self.assertIn(
            "ConnectionError", salida,
            "el aviso no incluye el TIPO del fallo: %r" % salida)
        self.assertTrue(
            rutas, "hablar nunca llego a crear el mp3: no se puede medir que "
            "se borre")
        for ruta in rutas:
            self.assertFalse(
                os.path.exists(ruta),
                "el mp3 %s quedo huerfano en el temp tras fallar edge_tts: "
                "el finally de limpieza no corrio" % ruta)

    def test_playsound_roto_no_mata(self):
        """un playsound roto tampoco mata a hablar: avisa y sigue

        playsound corre FUERA de asyncio.run; si revienta (archivo que no
        puede abrir, driver de audio caido) la excepcion tenia que quedar
        absorbida igual que la de edge_tts, con aviso impreso.
        """
        main = _main()

        class _CommunicateBueno:
            def __init__(self, *argumentos, **opciones):
                pass

            async def save(self, ruta):
                with open(ruta, "wb") as archivo:
                    archivo.write(b"mp3 falso")

        def _playsound_roto(ruta):
            raise OSError("no hay driver de audio")

        dobles = types.SimpleNamespace(Communicate=_CommunicateBueno)
        consola = io.StringIO()
        with mock.patch.object(main, "edge_tts", dobles), \
                mock.patch.object(main, "playsound", _playsound_roto), \
                mock.patch("sys.stdout", consola):
            try:
                main.hablar("hola")
            except Exception as exc:
                self.fail(
                    "hablar lanzo %s: %s con playsound roto: tiene que "
                    "absorber su propio fallo y seguir"
                    % (type(exc).__name__, exc))

        salida = consola.getvalue()
        self.assertIn(
            "aviso", salida,
            "hablar fallo con playsound roto y NO aviso: se trago en silencio")
        self.assertIn(
            "no pude hablar", salida,
            "el aviso de hablar no dice que no pudo hablar: %r" % salida)
        self.assertIn(
            "OSError", salida,
            "el aviso no incluye el TIPO del fallo: %r" % salida)

    def test_ctrl_c_si_propaga(self):
        """KeyboardInterrupt (Ctrl+C) tiene que seguir SALIENDO de hablar

        PRUEBA EXPLICITA de la regla B3: el arreglo usa `except Exception`,
        que NO captura KeyboardInterrupt (que es BaseException). Si alguien
        lo cambiara a `except:` a secas, el Ctrl+C se tragaria y esta prueba
        FALLARIA (assertRaises esperaba que saliera y no saldria). Un Jarvis
        que no deja salir el Ctrl+C es un Jarvis que no se puede parar.
        """
        main = _main()

        class _CommunicateCtrlC:
            def __init__(self, *argumentos, **opciones):
                pass

            async def save(self, ruta):
                with open(ruta, "wb") as archivo:
                    archivo.write(b"mp3 falso")
                raise KeyboardInterrupt  # el usuario aprieta Ctrl+C

        dobles = types.SimpleNamespace(Communicate=_CommunicateCtrlC)
        with mock.patch.object(main, "edge_tts", dobles), \
                mock.patch.object(main, "playsound", mock.MagicMock()), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                # si el except se comiera el Ctrl+C (except: a secas),
                # hablar devolveria sin lanzar y assertRaises FALLARIA
                main.hablar("hola")


class TestProcesarOrdenNoMuereSinInternet(unittest.TestCase):
    """procesar_orden atrapa sr.RequestError, avisa y sigue escuchando"""

    def setUp(self):
        self.main = _main()
        self.main.olvidar_pendiente()

    def test_sin_internet_avisa_y_el_bucle_sigue(self):
        """sin internet procesar_orden NO revienta: avisa y vuelve a escuchar

        Se dobla el reconocedor para que recognize_google lance
        sr.RequestError (sin red) TOPE veces; cada uno tiene que ser ATRAPADO
        y el bucle tiene que seguir (re-escuchar). En la llamada siguiente se
        lanza _FinDeLaPrueba para salir del while True y poder medir: si el
        bucle no siguiera tras el primer RequestError, nunca llegaria a esa
        llamada y _FinDeLaPrueba no se lanzaria (la prueba fallaria). Si en
        cambio RequestError escapara sin atrapar (mutacion: quitar el
        except), la prueba fallaria con ese error en vez de con
        _FinDeLaPrueba. Se comprueba ademas el aviso HONESTO en consola.
        """
        main = self.main
        RequestError = main.sr.RequestError
        contador = {"sin_internet": 0}

        def _recognize_sin_internet(*argumentos, **opciones):
            contador["sin_internet"] += 1
            if contador["sin_internet"] <= TOPE_SIN_INTERNET:
                raise RequestError("connection error: sin internet")
            # ya se midio lo suficiente: se sale del bucle para medir
            raise _FinDeLaPrueba()

        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.side_effect = _recognize_sin_internet
        consola = io.StringIO()

        # el resto del flujo va doblado: microfono, wav y voz. Nunca hay red
        # ni audio real. La espera de 6 s no se alcanza porque el
        # reconocimiento revienta antes; aun asi se dobla el reloj por si
        with mock.patch.object(main, "anthropic_api_key", None), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main, "atender", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            try:
                main.procesar_orden()
            except RequestError as exc:
                self.fail(
                    "procesar_orden dejo escapar sr.RequestError (%s): sin "
                    "internet tiene que ATRAPARLO, avisar y seguir "
                    "escuchando, no morir" % exc)
            except _FinDeLaPrueba:
                pass  # el bucle siguio hasta el tope: es lo esperado

        self.assertEqual(
            contador["sin_internet"], TOPE_SIN_INTERNET + 1,
            "procesar_orden solo hizo %d intentos de reconocimiento; tenia "
            "que seguir ESCUCHANDO tras cada sr.RequestError y llegar al "
            "%do antes de salir"
            % (contador["sin_internet"], TOPE_SIN_INTERNET + 1))
        salida = consola.getvalue()
        self.assertIn(
            "sin internet", salida,
            "procesar_orden no aviso con honestidad que no hay internet: "
            "salio %r" % salida)
        self.assertIn(
            "reconocer ni hablar", salida,
            "el aviso sin internet no dice que tampoco puede HABLAR (voz): "
            "salio %r" % salida)

    def test_flujo_normal_sigue_intacto(self):
        """regresion: con internet reconocer -> atender -> dormir sin cambios

        El `except sr.RequestError` nuevo NO puede afectar el camino NORMAL:
        cuando recognize_google devuelve texto, procesar_orden lo manda a
        atender y sigue al flujo de espera/dormir de siempre, sin ningun
        aviso de sin internet.
        """
        main = self.main
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = "hora"
        atender = mock.MagicMock(return_value="")
        reloj = mock.MagicMock()
        # 0.0 al fijar el inicio y 100.0 en la condicion: sin voz en la
        # espera Jarvis se duerma YA y la vuelta termina sola (cero espera)
        reloj.time.side_effect = [0.0, 100.0]
        consola = io.StringIO()

        with mock.patch.object(main, "time", reloj), \
                mock.patch.object(main, "anthropic_api_key", None), \
                mock.patch.object(main, "atender", atender), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            main.procesar_orden()

        atender.assert_called_once_with("hora")
        salida = consola.getvalue()
        self.assertIn(
            "dormido", salida,
            "el flujo normal de espera/dormir no ocurrio: la regresion de D "
            "esta rota. Salio: %r" % salida)
        self.assertNotIn(
            "sin internet", salida,
            "con internet funcionando salio un aviso de sin internet: el "
            "except nuevo se esta tragando el camino normal")


if __name__ == "__main__":
    unittest.main()
