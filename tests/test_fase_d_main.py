# pruebas de la FASE D del proyecto Jarvis (2026-10-20)
#
# Los 5 bugs de robustez que faltaban:
#   B2  procesar_orden se llamaba a si misma -> RecursionError y cada frame
#       retenia su audio (cientos de MB en una charla larga)
#   B5  la memoria del usuario (nombre y notas) NUNCA viajaba al prompt de la
#       IA, aunque el prompt le prometia al modelo que si
#   B7  grabar_hasta_silencio solo cortaba por silencio -> con ruido de fondo
#       Jarvis se quedaba en "Escuchando..." para siempre
#   B11 max_tokens=200 cortaba las respuestas de un modelo de 64K y nadie lo
#       avisaba (se leia una frase a media como si estuviera completa)
#   B14 los mp3 de voz se quedaban en la raiz del proyecto para siempre
#
# REGLA DE ORO de esta fase: cada prueba se verifico ANTES del arreglo
# quitandole a mano el decorador @unittest.expectedFailure en una copia
# temporal (variable de entorno JARVIS_ROOT) y confirmando que caia en la
# ASERCION (no con ModuleNotFoundError ni SyntaxError). Despues se revirtio el
# arreglo en OTRA copia temporal y se confirmo que la prueba volvia a fallar
# (mutacion de control). Las dos mitades, para los 5.
#
# TRAMPA DEL PROYECTO: @unittest.expectedFailure se come CUALQUIER excepcion,
# no solo AssertionError. Por eso cada prueba que puede morir con una
# excepcion de runtime (RecursionError, un bucle infinito frenado por un mock
# que revienta) la convierte a proposito en self.fail(...) = AssertionError.
#
# Ninguna prueba de este archivo toca red, microfono ni el memoria.json real
# de Edward: todo va con dobles.

import inspect
import io
import os
import sys
import tempfile
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


class TestB2SinRecursion(unittest.TestCase):
    """B2: procesar_orden tiene que aguantar una charla larga sin reventar"""

    # 1200 vueltas: mas del doble del tope de 1000 frames de la pila de
    # Python, para que con el defecto el RecursionError sea imposible de
    # esquivar por suerte
    VUELTAS = 1200

    # el decorador se QUITA cuando el arreglo esta hecho: mientras el defecto
    # existe, la prueba tiene que salir como "expected failure" y no romper
    # la suite
    def test_mil_doscientas_vueltas_sin_recursionerror(self):
        """B2: 1200 vueltas de la charla no pueden dar RecursionError

        El defecto era `procesar_orden()` llamandose a si misma al final:
        Python corta en 1000 frames y una charla larga acababa en
        RecursionError (y antes, tragado en silencio por un except vacio).
        Peor aun: CADA frame retenia su audio, asi que 1200 vueltas serian
        cientos de MB acumulados.

        La medida es el DOBLE. Se dobla el tiempo (time.time) con un reloj
        falso que avanza 0.5 s por lectura: asi la espera de 6 segundos se
        cumple en 12 lecturas sin esperar el reloj real (fundamento 28). Se
        dobla el microfono: grabar_hasta_silencio devuelve silencio y sd.rec
        de la espera devuelve VOZ en las primeras 1199 vueltas y DESPUES
        silencio, de modo que la vuelta 1200 es la del "se ha dormido". El
        texto reconocido es "hora", que dispara una accion barata y NUNCA
        toca la IA ni el navegador (ejecutar_accion esta doblado).

        La prueba demuestra las DOS mitades del comportamiento visible:
        (i) la REPETICION ocurre, exactamente 1200 veces, y
        (ii) TERMINA sola, imprimiendo el mensaje de dormir.
        """
        main = _main()
        contador = {"vueltas": 0, "esperas": 0}
        reloj_falso = {"ahora": 0.0}

        def _tick():
            reloj_falso["ahora"] += 0.5
            return reloj_falso["ahora"]

        reloj = mock.MagicMock()
        reloj.time.side_effect = _tick

        def _grabar_falso(*argumentos, **opciones):
            # una vuelta = una llamada a grabar_hasta_silencio
            contador["vueltas"] += 1
            return np.zeros(10, dtype="int16")

        def _rec_de_espera(*argumentos, **opciones):
            # VOZ mientras quede por repetir, silencio en la ultima vuelta
            contador["esperas"] += 1
            if contador["esperas"] < self.VUELTAS:
                return np.full((100, 1), 5000, dtype="int16")
            return np.zeros((100, 1), dtype="int16")

        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = "hora"
        consola = io.StringIO()

        # FASE G3: procesar_orden delega en atender; esta prueba mide la
        # charla larga en el CAMINO SIN CLAVE (el de siempre), que es el
        # que no depende de red ni de IA. Sin forzarlo, con una clave real
        # en el .env estas 1200 vueltas serian 1200 llamadas reales a la
        # API
        with mock.patch.object(main, "time", reloj), \
                mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "grabar_hasta_silencio", _grabar_falso), \
                mock.patch.object(main.sd, "rec",
                                  mock.MagicMock(side_effect=_rec_de_espera)), \
                mock.patch.object(main.sd, "wait", mock.MagicMock()), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main, "ejecutar_accion", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            try:
                main.procesar_orden()
            except RecursionError:
                # se convierte en asercion a proposito: un RecursionError
                # suelto seria un ERROR de la suite y no una FALLA, y con el
                # decorador puesto nadie sabria que murio por recursion
                self.fail(
                    "procesar_orden se cayo con RecursionError tras %d "
                    "vueltas (tope de la pila: %d frames): se llama a si "
                    "misma y CADA frame ademas retiene su audio"
                    % (contador["vueltas"], sys.getrecursionlimit()))

        self.assertEqual(
            contador["vueltas"], self.VUELTAS,
            "procesar_orden hizo %d vueltas y tenia que repetir %d: la "
            "repeticion de la charla no ocurre"
            % (contador["vueltas"], self.VUELTAS))
        self.assertIn(
            "dormido", consola.getvalue(),
            "procesar_orden no imprimio el mensaje de dormir: no llego a "
            "TERMINAR la charla")


class TestB5MemoriaALaIA(unittest.TestCase):
    """B5: el system prompt tiene que llevar de verdad la memoria del usuario"""

    MEMORIA_FALSA = {
        "nombre_usuario": "Yohan",
        "notas": ["le gusta el chocolate", "aprende django"],
    }

    def test_la_memoria_viaja_en_el_prompt(self):
        """B5: el payload de la IA contiene el nombre y al menos una nota

        El defecto: preguntar_a_ia() solo enviaba texto_usuario, pero su
        system prompt le prometia al modelo que "tienes acceso a la
        informacion de memoria que se te proporciona arriba (nombre y notas
        del usuario)". O sea: le prometia algo que no recibia. Jarvis le
        decia a Edward que conocia su nombre sin conocerlo, y sus notas eran
        decorativas.

        La memoria se INYECTA con cargar_memoria doblado: esta prueba NUNCA
        lee ni escribe el memoria.json real de Edward. Se comprueba el payload
        COMPLETO que se le manda a la API (model, max_tokens, system,
        messages) y se afirma que el bloque de memoria esta dentro del
        system, que es donde el prompt ya decia que estaba.
        """
        main = _main()
        cliente = mock.MagicMock()

        with mock.patch.object(main, "_obtener_cliente_ia",
                               lambda: cliente), \
                mock.patch.object(main, "cargar_memoria",
                                  lambda: dict(self.MEMORIA_FALSA)):
            main.preguntar_a_ia("cual es mi nombre")

        cliente.messages.create.assert_called_once()
        argumentos = cliente.messages.create.call_args.kwargs
        self.assertIn(
            "system", argumentos,
            "preguntar_a_ia no mando ningun system prompt")
        system = argumentos["system"]

        self.assertIn(
            "Yohan", system,
            "el nombre de la memoria NO viajo al prompt de la IA: el modelo "
            "no sabe como se llama el usuario")
        self.assertIn(
            "le gusta el chocolate", system,
            "las notas de la memoria NO viajaron al prompt de la IA")
        self.assertIn(
            "aprende django", system,
            "solo viajo una parte de las notas: tienen que ir todas")


class TestB7GrabacionConTope(unittest.TestCase):
    """B7: grabar_hasta_silencio necesita un tope duro de duracion"""

    # tope de la prueba: si la grabacion llega aqui, el defecto esta presente
    BLOQUES_DE_PRUEBA = 100

    def test_ruido_continuo_no_deja_grabando_para_siempre(self):
        """B7: con ruido de fondo constante la grabacion se corta sola

        El defecto era un while True que solo rompia por silencio: con ruido
        de fondo por encima del umbral, Jarvis se quedaba en "Escuchando..."
        para siempre y bloques crecia ~317 MB por hora.

        La medida NO depende del reloj (fundamento 28): el doble de sd.rec
        devuelve ruido constante (media 5000, muy por encima del umbral de
        1300) y se CUENTAN bloques. Si se pasan de BLOQUES_DE_PRUEBA sin
        cortar, el doble revienta y la prueba se convierte en una asercion:
        un bucle infinito real colgaria la suite entera, y eso no es una
        prueba, es un cuelgue.
        """
        main = _main()
        contador = {"bloques": 0}

        def _rec_ruidoso(*argumentos, **opciones):
            contador["bloques"] += 1
            if contador["bloques"] > self.BLOQUES_DE_PRUEBA:
                raise AssertionError(
                    "grabar_hasta_silencio lleva %d bloques (%.1f segundos "
                    "de ruido continuo) y sigue grabando: no tiene tope"
                    % (contador["bloques"],
                       contador["bloques"] * 0.5))
            return np.full((int(0.5 * main.frecuencia), 1), 5000,
                           dtype="int16")

        consola = io.StringIO()
        with mock.patch.object(main.sd, "rec",
                               mock.MagicMock(side_effect=_rec_ruidoso)), \
                mock.patch.object(main.sd, "wait", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            try:
                audio = main.grabar_hasta_silencio()
            except AssertionError as exc:
                self.fail(str(exc))

        # el tope declarado tiene que existir y tiene que ser el que dice la
        # firma: no se mide un numero inventado, se mide el que declara el
        # codigo
        parametros = inspect.signature(
            main.grabar_hasta_silencio).parameters
        if "max_segundos" not in parametros:
            self.fail(
                "grabar_hasta_silencio no tiene ningun parametro de tope "
                "(max_segundos): solo puede cortar por silencio")
        maximo = parametros["max_segundos"].default
        bloques_esperados = int(maximo / 0.5)

        self.assertLessEqual(
            contador["bloques"], bloques_esperados,
            "grabar_hasta_silencio hizo %d bloques y con un tope de %.1f "
            "s tenia que cortar en %d"
            % (contador["bloques"], maximo, bloques_esperados))
        self.assertGreater(
            len(audio), 0,
            "el tope devolvio el audio vacio: tiene que devolver LO "
            "GRABADO hasta el corte")
        self.assertIn(
            "aviso", consola.getvalue(),
            "el tope corta la grabacion sin avisarlo: Jarvis se tragaria el "
            "corte y Edward no sabria que la mitad de su frase no se oyo")


class TestB11MaxTokens(unittest.TestCase):
    """B11: max_tokens tiene que ser suficiente y el corte tiene que avisarse"""

    def test_max_tokens_suficiente_y_aviso_si_se_corta(self):
        """B11: max_tokens >= 1024 y si stop_reason es max_tokens se avisa

        El defecto: max_tokens=200 (unas 150 palabras) sobre un modelo de
        64K de contexto, y el stop_reason se ignoraba. La respuesta salia
        cortada y se LEIA EN VOZ como si estuviera completa.

        Se dobla el cliente de la IA y se captura el payload COMPLETO.
        Despues se comprueba el AVISO por los DOS canales: en consola (para
        quien tenga la terminal abierta) y en el TEXTO devuelto (que es lo
        que luego se lee en voz). Y se comprueba tambien lo contrario: una
        respuesta NORMAL no se alarmada, para que el aviso no se convierta
        en un grito permanente.
        """
        main = _main()
        cliente = mock.MagicMock()

        def _respuesta(texto, stop_reason):
            respuesta = mock.MagicMock()
            respuesta.content = [mock.MagicMock(text=texto)]
            respuesta.stop_reason = stop_reason
            return respuesta

        cliente.messages.create.return_value = _respuesta(
            "Mi respuesta completa", "end_turn")
        memoria_falsa = {"nombre_usuario": None, "notas": []}

        with mock.patch.object(main, "_obtener_cliente_ia",
                               lambda: cliente), \
                mock.patch.object(main, "cargar_memoria",
                                  lambda: dict(memoria_falsa)), \
                mock.patch("sys.stdout", io.StringIO()) as consola:
            texto_normal = main.preguntar_a_ia("hola")

        argumentos = cliente.messages.create.call_args.kwargs
        self.assertIn("max_tokens", argumentos,
                      "preguntar_a_ia no paso ningun max_tokens")
        self.assertGreaterEqual(
            argumentos["max_tokens"], 1024,
            "max_tokens=%r: con menos de 1024 las respuestas de un modelo "
            "de 64K salen cortadas y se leen igual en voz"
            % (argumentos["max_tokens"],))
        self.assertEqual(
            texto_normal, "Mi respuesta completa",
            "una respuesta que termino NORMAL se modifico: %r" % texto_normal)
        self.assertNotIn(
            "aviso", consola.getvalue(),
            "se aviso un corte que no ocurrio")

        # --- la segunda mitad: la respuesta que SI viene cortada ---
        cliente.messages.create.return_value = _respuesta(
            "Mi respuesta se corto aqui", "max_tokens")
        with mock.patch.object(main, "_obtener_cliente_ia",
                               lambda: cliente), \
                mock.patch.object(main, "cargar_memoria",
                                  lambda: dict(memoria_falsa)), \
                mock.patch("sys.stdout", io.StringIO()) as consola_cortada:
            texto_cortado = main.preguntar_a_ia("hola")

        self.assertIn(
            "aviso", consola_cortada.getvalue(),
            "stop_reason=max_tokens y no se aviso en consola: nadie se entera "
            "de que la respuesta vino a medias")
        self.assertNotEqual(
            texto_cortado, "Mi respuesta se corto aqui",
            "la respuesta cortada se devuelve IDENTICA: se leeria en voz "
            "como si estuviera completa")


class TestB14Mp3Borrado(unittest.TestCase):
    """B14: el mp3 de voz no puede quedarse ni en el proyecto ni en el temp"""

    def test_el_mp3_no_queda_ni_en_el_proyecto_ni_en_el_temp(self):
        """B14: hablar escribe el mp3 en el temp del sistema y lo borra

        El defecto: hablar() creaba respuesta_<time.time()>.mp3 en el
        DIRECTORIO DE TRABAJO (que al arrancar jarvis es la raiz del
        proyecto) y lo dejaba ahi. 186 mp3 huerfanos, 7 MB; un dia entero de
        uso serian ~1.150 archivos y 43 MB.

        Se mide con edge_tts y playsound doblados: el doble de edge_tts SIEMPRE
        escribe el archivo de verdad en la ruta que recibe (si no, no se podria
        medir que se borre), y el doble de playsound comprueba que el archivo
        EXISTE mientras suena, que es lo que exige playsound 1.2.2.

        CUIDADO: se cambia a un directorio temporal (y se restaura en finally)
        porque con el defecto el archivo se crea en el directorio de trabajo,
        y eso NO puede ensuciar la raiz del proyecto durante una corrida de la
        suite. El directorio de trabajo de un programa arrancado a mano ES la
        raiz del proyecto, asi que medir "no hay nada en el cwd" es medir
        "no hay nada en el proyecto".
        """
        main = _main()
        creados = []
        reproducidos = []

        class _CommunicateFalso:
            def __init__(self, *argumentos, **opciones):
                pass

            async def save(self, ruta):
                creados.append(ruta)
                with open(ruta, "wb") as archivo:
                    archivo.write(b"mp3 falso")

        def _playsound_falso(ruta):
            reproducidos.append(ruta)
            if not os.path.isfile(ruta):
                raise AssertionError(
                    "playsound no encontro el mp3 en %s" % ruta)

        dobles = types.SimpleNamespace(Communicate=_CommunicateFalso)
        with tempfile.TemporaryDirectory() as tmp:
            cwd_antes = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.object(main, "edge_tts", dobles), \
                        mock.patch.object(main, "playsound",
                                          _playsound_falso):
                    main.hablar("hola, probando")
                ensuciados = sorted(os.listdir(tmp))
            finally:
                os.chdir(cwd_antes)

            self.assertTrue(
                creados, "hablar no llego a generar ningun mp3")
            self.assertTrue(
                reproducidos, "hablar no llego a reproducir el mp3")
            self.assertEqual(
                ensuciados, [],
                "hablar dejo %s en el DIRECTORIO DE TRABAJO (que al arrancar "
                "jarvis es la raiz del proyecto): ahi es donde se acumulan "
                "los mp3 huerfanos" % ensuciados)

            for ruta in creados:
                self.assertEqual(
                    os.path.dirname(ruta), tempfile.gettempdir(),
                    "el mp3 %s no se escribio en el temp del sistema sino en "
                    "%r" % (ruta, os.path.dirname(ruta)))
                self.assertFalse(
                    os.path.exists(ruta),
                    "el mp3 %s quedo huerfano en el temp del sistema tras "
                    "reproducirlo" % ruta)


if __name__ == "__main__":
    unittest.main()
