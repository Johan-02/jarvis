# pruebas de los WAVS DE GRABACION al temp del sistema (2026-10-25)
#
# Misma familia que el B14 de hablar(): los tres wavs que Jarvis escribe
# para reconocer voz (nombre_usuario.wav, grabacion.wav y
# escucha_activacion.wav) eran RUTAS RELATIVAS: caian en el directorio de
# trabajo, nunca se borraban y dos instancias se pisaban el mismo archivo.
# Estas pruebas miden el arreglo en cuatro frentes:
#   1. el wav se crea en el temp del sistema y NO en el CWD;
#   2. despues de la llamada NO queda el archivo (se borra);
#   3. dos llamadas seguidas NUNCA usan el mismo nombre;
#   4. si sr.AudioFile revienta, el wav igual se limpia (finally);
#   5. el texto reconocido sigue llegando igual (el flujo no cambia).
#
# REGLAS DE ESTE ARCHIVO: cero microfono, cero red, cero IA. El doble de
# wave.open SÍ crea el archivo real (para poder medir que se borra); todo
# lo demás va con mocks, al estilo de test_fase_b_main.

import os
import sys
import tempfile
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


class _WaveFalsa:
    """doble de wave.open que SÍ crea el archivo real en la ruta dada

    Sin esto el finally no tendria nada que borrar y las pruebas de
    limpieza no medirian nada. Los metodos de formato son no-op: el
    contenido del wav da igual, solo importa que el ARCHIVO exista.
    """

    def __init__(self, ruta, modo):
        self._archivo = open(ruta, modo)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._archivo.close()
        return False

    def setnchannels(self, canales):
        pass

    def setsampwidth(self, ancho):
        pass

    def setframerate(self, frecuencia):
        pass

    def writeframes(self, datos):
        pass


def _doble_wave(rutas):
    """doble de wave.open: registra la ruta ESCRITA y crea el archivo real"""
    def _open(ruta, modo):
        rutas.append(ruta)
        return _WaveFalsa(ruta, modo)
    return _open


class _ContextoFalso:
    """context manager vacio para sr.AudioFile"""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _doble_audiofile(rutas_leidas):
    """doble de sr.AudioFile: registra la ruta LEIDA"""
    def _audiofile(ruta):
        rutas_leidas.append(ruta)
        return _ContextoFalso()
    return _audiofile


def _reconocedor(texto):
    """doble del reconocedor: recognize_google siempre devuelve `texto`"""
    reconocedor = mock.MagicMock()
    reconocedor.recognize_google.return_value = texto
    return reconocedor


def _reloj_de_espera():
    """doble de main.time para que el bucle de espera de voz termine ya

    La primera llamada fija el inicio de la espera y la segunda la saca
    del todo (como en test_fase_b_main). main.time NO se usa para los
    nombres de wav: mkstemp trae su propio generador de unicidad.
    """
    reloj = mock.MagicMock()
    reloj.time.side_effect = [0.0, 100.0]
    return reloj


def _rec_que_corta(cortar_tras):
    """doble de sd.rec que corta el bucle con Ctrl+C tras `cortar_tras` vueltas

    bucle_principal es un while True: sin un corte explicito la prueba se
    colgaria escuchando para siempre. KeyboardInterrupt es lo que hace
    tambien test_fase_b_main para salir: NO lo captura ningun except del
    bucle, asi que sale limpio.
    """
    contador = {"grabaciones": 0}

    def _rec(*argumentos, **opciones):
        contador["grabaciones"] += 1
        if contador["grabaciones"] > cortar_tras:
            raise KeyboardInterrupt("corte de la prueba")
        return np.zeros((10, 1), dtype="int16")

    return _rec


class TestWavsEnTempDelSistema(unittest.TestCase):
    """los tres wavs de grabacion van al temp del sistema y se borran siempre"""

    def _procesar_orden(self, main, rutas, leidas, texto="hora"):
        """corre procesar_orden una vez con todos los dobles puestos

        atender va doblado (registra el texto que llega y devuelve "":
        procesar_orden no dice nada de mas, igual que sin clave). Devuelve
        la lista de textos que llegaron a atender.
        """
        recibidos = []
        with mock.patch.object(
                main, "grabar_hasta_silencio",
                mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", _doble_wave(rutas)), \
                mock.patch.object(main.sr, "AudioFile",
                                  _doble_audiofile(leidas)), \
                mock.patch.object(main, "r", _reconocedor(texto)), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main, "time", _reloj_de_espera()), \
                mock.patch.object(
                    main, "atender",
                    lambda t: recibidos.append(t) or ""):
            main.procesar_orden()
        return recibidos

    def test_procesar_orden_escribe_en_temp_y_borra(self):
        """el wav va al temp del sistema y NO queda tras la llamada"""
        main = _main()
        rutas, leidas = [], []
        self._procesar_orden(main, rutas, leidas)

        self.assertTrue(rutas, "procesar_orden no llego a escribir ningun wav")
        for ruta in rutas:
            self.assertEqual(
                os.path.dirname(ruta), tempfile.gettempdir(),
                "el wav %s no se escribio en el temp del sistema sino en %r"
                % (ruta, os.path.dirname(ruta)))
            self.assertFalse(
                os.path.exists(ruta),
                "el wav %s quedo huerfano tras leerlo" % ruta)

    def test_escritura_y_lectura_usan_el_mismo_archivo(self):
        """wave.open y sr.AudioFile tienen que apuntar a la MISMA variable"""
        main = _main()
        rutas, leidas = [], []
        self._procesar_orden(main, rutas, leidas)

        self.assertTrue(rutas, "no hubo escritura de wav")
        self.assertEqual(
            rutas, leidas,
            "la ruta leida por sr.AudioFile no es la que escribio "
            "wave.open: las dos referencias dejaron de compartir variable")

    def test_nombre_unico_dos_llamadas(self):
        """dos llamadas seguidas NUNCA usan el mismo nombre de wav"""
        main = _main()
        rutas, leidas = [], []
        self._procesar_orden(main, rutas, leidas)
        self._procesar_orden(main, rutas, leidas)

        self.assertEqual(len(rutas), 2, "faltaron llamadas: %r" % (rutas,))
        self.assertEqual(
            len(set(rutas)), 2,
            "dos llamadas seguidas usaron el MISMO nombre de wav: %r"
            % (rutas,))

    def test_no_ensucia_el_directorio_de_trabajo(self):
        """el wav NO se crea en el CWD (que al arrancar jarvis es la raiz)"""
        main = _main()
        rutas, leidas = [], []
        with tempfile.TemporaryDirectory() as tmp:
            cwd_antes = os.getcwd()
            try:
                os.chdir(tmp)
                self._procesar_orden(main, rutas, leidas)
                ensuciados = sorted(os.listdir(tmp))
            finally:
                os.chdir(cwd_antes)

            self.assertTrue(rutas, "no hubo escritura de wav")
            self.assertEqual(
                ensuciados, [],
                "procesar_orden dejo %s en el DIRECTORIO DE TRABAJO: ahi es "
                "donde se acumulaban los wav huerfanos" % (ensuciados,))
            for ruta in rutas:
                self.assertEqual(
                    os.path.dirname(ruta), tempfile.gettempdir(),
                    "el wav %r no cayo en el temp del sistema" % (ruta,))

    def test_si_audiofile_revienta_el_wav_se_borra(self):
        """un fallo inesperado de lectura no puede dejar basura (finally)"""
        main = _main()
        rutas = []
        with mock.patch.object(
                main, "grabar_hasta_silencio",
                mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", _doble_wave(rutas)), \
                mock.patch.object(
                    main.sr, "AudioFile",
                    mock.MagicMock(side_effect=RuntimeError("falso"))):
            with self.assertRaises(RuntimeError):
                main.procesar_orden()

        self.assertTrue(rutas, "no hubo escritura de wav")
        for ruta in rutas:
            self.assertFalse(
                os.path.exists(ruta),
                "el wav %s quedo en el temp aunque sr.AudioFile revienta: "
                "el finally no corrio" % ruta)

    def test_pedir_nombre_usa_temp_borra_y_el_nombre_llega(self):
        """pedir_nombre: temp + borrado + el texto reconocido llega igual"""
        main = _main()
        rutas, leidas = [], []
        memoria = {"nombre_usuario": None, "notas": []}
        with mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main, "guardar_memoria",
                                  mock.MagicMock()), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", _doble_wave(rutas)), \
                mock.patch.object(main.sr, "AudioFile",
                                  _doble_audiofile(leidas)), \
                mock.patch.object(main, "r", _reconocedor("Edward")):
            main.pedir_nombre(memoria)

        self.assertEqual(
            memoria["nombre_usuario"], "Edward",
            "el nombre reconocido no llego a la memoria: se rompio el flujo")
        self.assertTrue(rutas, "pedir_nombre no llego a escribir ningun wav")
        self.assertEqual(rutas, leidas,
                         "escribir y leer no usaron la MISMA variable")
        for ruta in rutas:
            self.assertEqual(os.path.dirname(ruta), tempfile.gettempdir(),
                             "el wav %r no cayo en el temp" % (ruta,))
            self.assertFalse(os.path.exists(ruta),
                             "el wav %s quedo huerfano" % ruta)

    def test_pedir_nombre_si_no_se_reconoce_igual_se_borra(self):
        """si el reconocimiento falla (sin internet) el wav igual se limpia"""
        main = _main()
        rutas = []
        memoria = {"nombre_usuario": None, "notas": []}
        hablados = []
        with mock.patch.object(main, "hablar", hablados.append), \
                mock.patch.object(main, "guardar_memoria",
                                  mock.MagicMock()), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", _doble_wave(rutas)), \
                mock.patch.object(
                    main.sr, "AudioFile",
                    mock.MagicMock(
                        side_effect=main.sr.RequestError("sin internet"))):
            main.pedir_nombre(memoria)  # el except lo captura: no revienta

        self.assertEqual(
            memoria["nombre_usuario"], None,
            "con la escucha fallada no puede registrarse nombre")
        self.assertTrue(
            any("No logré" in texto for texto in hablados),
            "no se aviso del fallo de reconocimiento: %r" % (hablados,))
        self.assertTrue(rutas, "no hubo escritura de wav")
        for ruta in rutas:
            self.assertFalse(
                os.path.exists(ruta),
                "el wav %s quedo en el temp tras un reconocimiento fallido"
                % ruta)

    def test_bucle_principal_dos_vueltas_usan_nombres_distintos(self):
        """dos vueltas del bucle de activacion usan nombres DISTINTOS"""
        main = _main()
        rutas, leidas = [], []
        # "no activa nada" no casa con FRASE_ACTIVACION_INICIAL ni NORMAL;
        # el corte en la 3ra rec deja que las 2 primeras vueltas escriban
        # y lean SU wav antes de salir con Ctrl+C
        with mock.patch.object(main.sd, "rec", _rec_que_corta(cortar_tras=2)), \
                mock.patch.object(main.sd, "wait", mock.MagicMock()), \
                mock.patch.object(main.wave, "open", _doble_wave(rutas)), \
                mock.patch.object(main.sr, "AudioFile",
                                  _doble_audiofile(leidas)), \
                mock.patch.object(main, "r", _reconocedor("no activa nada")), \
                mock.patch.object(main, "hablar", mock.MagicMock()):
            with self.assertRaises(KeyboardInterrupt):
                main.bucle_principal()

        self.assertEqual(
            len(rutas), 2,
            "el bucle no dio las 2 vueltas esperadas: %r" % (rutas,))
        self.assertEqual(
            len(set(rutas)), 2,
            "dos vueltas del bucle usaron el MISMO nombre de wav: %r"
            % (rutas,))
        for ruta in rutas:
            self.assertEqual(os.path.dirname(ruta), tempfile.gettempdir(),
                             "el wav %r no cayo en el temp" % (ruta,))
            self.assertFalse(os.path.exists(ruta),
                             "el wav %s quedo huerfano" % ruta)


if __name__ == "__main__":
    unittest.main()
