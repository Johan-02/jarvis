# pruebas de la FASE C del proyecto Jarvis (2026-10-19).
#
# Dos bloques:
#   1. el BLOQUEO: main/main.py importaba pywhatkit, que NO se puede instalar
#      en Python 3.14 (su setup.py de 2022 usa distutils, eliminado del
#      lenguaje), asi que el asistente no podia ni arrancar. El comando
#      "reproduce" ahora abre la busqueda de YouTube con webbrowser.
#   2. el RECONOCEDOR: main/reconocedor.py traduce texto en acciones SIN
#      ejecutar nada. Aqui se miden sus defectos B10, B12 y B6 con frases
#      reales, sin audio y sin red.
#
# REGLA DE ORO de esta fase: cada prueba se verifico ANTES del arreglo
# quitandole a mano el decorador @unittest.expectedFailure en una copia
# temporal y confirmando que fallaba en la ASERCION (no con ModuleNotFoundError
# ni SyntaxError). El decorador se quita recien cuando el arreglo esta hecho.
#
# TRAMPA DEL PROYECTO: @unittest.expectedFailure se come CUALQUIER excepcion,
# no solo AssertionError.

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

RUTA_MAIN = RAIZ / "main" / "main.py"
RUTA_RECONOCEDOR = RAIZ / "main" / "reconocedor.py"

# el doble de las librerias de audio vive en test_fase_b_main; se reutiliza
# para poder importar main.main sin microfono ni red
try:
    from tests.test_fase_b_main import _main
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_b_main import _main

from main.reconocedor import interpretar


class TestBloqueoPywhatkit(unittest.TestCase):
    """el bloqueo: Jarvis no podia arrancar por un paquete imposible de instalar"""

    def test_reproduce_no_instala_pywhatkit(self):
        """main/main.py no contiene pywhatkit y reproduce con webbrowser

        Se lee el archivo como TEXTO, sin importarlo. pywhatkit==5.4.1 usa
        distutils en su setup.py y en Python 3.14 el build revienta con
        FileNotFoundError: 'requirements.txt'. No es transitorio: es
        imposible, y por esa linea el asistente no arrancaba.
        """
        texto = RUTA_MAIN.read_text(encoding="utf-8")
        self.assertNotIn(
            "pywhatkit", texto.lower(),
            "main/main.py sigue mencionando pywhatkit, que no se puede "
            "instalar en Python 3.14 y deja al asistente sin arrancar")
        self.assertIn(
            "youtube.com/results?search_query=", texto,
            "el comando reproduce ya no abre la busqueda de YouTube en "
            "main/main.py")
        self.assertIn(
            "urllib.parse.quote", texto,
            "la busqueda se tiene que codificar con urllib.parse.quote: los "
            "acentos y la ENIE rompen la url si se concatenan crudas")

    def test_reproduce_abre_la_url_de_busqueda_de_youtube(self):
        """el comando reproduce abre la busqueda de YouTube, con la url codificada

        Se ejecuta la accion con webbrowser y hablar doblados: no se abre
        ningun navegador y no se genera voz. La busqueda lleva acento y
        ENIE a proposito: con concatenacion cruda la url quedaria rota.
        """
        main = _main()
        acciones = interpretar("reproduce canci\u00f3n de la \u00f1o\u00f1a")

        with mock.patch.object(main, "webbrowser") as navegador, \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(
                    main, "preguntar_a_ia",
                    mock.MagicMock(return_value="respuesta falsa")):
            for accion in acciones:
                main.ejecutar_accion(accion)

        navegador.open.assert_called_once_with(
            "https://www.youtube.com/results?search_query="
            "canci%C3%B3n%20de%20la%20%C3%B1o%C3%B1a")
        self.assertEqual(
            [accion.nombre for accion in acciones], ["reproduce"],
            "con una sola orden el reconocedor no puede devolver mas de una "
            "accion: devolvio %r" % (acciones,))


class TestReconocedor(unittest.TestCase):
    """B10, B12 y B6 medidos sobre main/reconocedor.py, con frases reales"""

    def test_interpretar_quita_solo_la_palabra_de_orden(self):
        """B10: la busqueda de "reproduce" no se come la frase entera

        El defecto hacia " ".join(palabra[1:]), que quita SIEMPRE la primera
        palabra sin comprobar que fuera "reproduce": con "por favor
        reproduce metallica" buscaba "favor reproduce metallica".
        """
        casos = (
            ("reproduce metallica", "metallica"),
            ("por favor reproduce metallica", "metallica"),
            ("reproduce el tema enter sandman", "el tema enter sandman"),
            ("jarvis reproduce despues no", "despues no"),
        )
        for texto, busqueda_esperada in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r devolvio %d acciones y tiene que devolver 1: %r"
                    % (texto, len(acciones), acciones))
                self.assertEqual(
                    acciones[0].nombre, "reproduce",
                    "%r no reconocio la orden reproduce: %r" % (texto, acciones))
                self.assertEqual(
                    acciones[0].argumentos, (busqueda_esperada,),
                    "%r extrajo %r y tenia que extraer %r"
                    % (texto, acciones[0].argumentos, busqueda_esperada))

    def test_reproduce_sin_busqueda_no_dispara_la_orden(self):
        """si "reproduce" viene sola, sin nada que reproducir, no hay accion"""
        for texto in ("reproduce", "por favor reproduce"):
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(len(acciones), 1, "%r devolvio %r"
                                 % (texto, acciones))
                self.assertEqual(
                    acciones[0].nombre, "consultar_ia",
                    "%r devolvio %r con una busqueda vacia" % (texto, acciones))

    def test_una_frase_dispara_una_sola_accion(self):
        """B12: una sola frase dispara UNA accion, la que menciona primero

        El defecto eran 10 "if" encadenados sin "elif": "abre gmail y
        youtube" abria LOS DOS. La regla de desempate es la PRIMERA orden
        mencionada en la frase, que es la que el usuario dijo primero.
        """
        casos = (
            ("abre gmail y youtube", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("pon youtube y spotify a la vez", "abrir",
             ("YouTube", "https://www.youtube.com")),
            ("que hora es y abre google", "hora", ()),
        )
        for texto, nombre_esperado, argumentos_esperados in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r disparo %d acciones y tiene que disparar UNA: %r"
                    % (texto, len(acciones), acciones))
                self.assertEqual(
                    acciones[0].nombre, nombre_esperado,
                    "%r debio reconocer %r y reconocio %r"
                    % (texto, nombre_esperado, acciones[0].nombre))
                self.assertEqual(
                    acciones[0].argumentos, argumentos_esperados,
                    "%r reconocio %r y tenia que reconocer %r"
                    % (texto, acciones[0].argumentos, argumentos_esperados))

    def test_orden_dicha_como_ultima_palabra(self):
        """la orden que TERMINA la frase tambien vale (cubre la mutacion M11)

        _indice_de tiene que recorrer hasta la ultima posicion posible:
        range(len(palabra) - ancho + 1). La mutacion M11 le quitaba ese +1,
        y entonces una orden dicha como ULTIMA palabra de la frase se
        ignoraba y acababa en consultar_ia: "abre gmail" se iba a la IA en
        vez de abrir Gmail. Todas estas frases acaban en la palabra clave a
        proposito (o la llevan al final de la orden multi-palabra): con M11,
        ninguna de ellas se reconoce.
        """
        casos = (
            ("abre gmail", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("por favor abre gmail", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("abre google", "abrir",
             ("Google", "https://www.google.com")),
            ("abre youtube", "abrir",
             ("YouTube", "https://www.youtube.com")),
            ("pon spotify", "abrir",
             ("Spotify", "https://www.spotify.com")),
            ("abre linkedin", "abrir",
             ("LinkedIn", "https://www.linkedin.com")),
            ("abre tradingview", "abrir",
             ("TradingView", "https://www.tradingview.com")),
            ("dime que hora", "hora", ()),
            ("hora", "hora", ()),
            ("curso de python dalto", "abrir",
             ("curso de Python", "https://www.youtube.com/watch?v=nKPbfIU442g")),
            ("por favor abre el curso de python dalto", "abrir",
             ("curso de Python", "https://www.youtube.com/watch?v=nKPbfIU442g")),
        )
        for texto, nombre_esperado, argumentos_esperados in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r devolvio %d acciones y tiene que devolver 1: %r"
                    % (texto, len(acciones), acciones))
                self.assertEqual(
                    acciones[0].nombre, nombre_esperado,
                    "%r lleva la orden al FINAL de la frase y no se "
                    "reconocio: debio dar %r y dio %r"
                    % (texto, nombre_esperado, acciones[0].nombre))
                self.assertEqual(
                    acciones[0].argumentos, argumentos_esperados,
                    "%r reconocio %r y tenia que reconocer %r"
                    % (texto, acciones[0].argumentos, argumentos_esperados))

    def test_orden_con_signo_pegado(self):
        """la orden pegada a un signo de puntuacion tambien se reconoce

        _indice_de comparaba palabras COMPLETAS contra la frase tal cual, y
        "gmail?" no es "gmail": "abre gmail?", "¿abre gmail?" y "abre gmail."
        acababan en consultar_ia. Lo mismo con un signo en la PRIMERA palabra
        ("¿abre") y con la orden multi-palabra ("curso de python dalto.").
        El arreglo limpia los signos ".,;:!?¿¡" de cada palabra ANTES de
        comparar, sin mover los indices, de modo que la busqueda de
        "reproduce" sigue leyendo la frase original.

        Cada frase lleva el signo PEGADO A LA PALABRA DE LA ORDEN a proposito:
        sin el arreglo, NINGUNA de ellas se reconoce (se verifico en copia
        temporal quitando la limpieza: falla en la asercion, no por excepcion).
        """
        casos = (
            ("abre gmail?", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("¿abre gmail?", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("abre gmail.", "abrir",
             ("Gmail", "https://mail.google.com")),
            ("¡pon spotify!", "abrir",
             ("Spotify", "https://www.spotify.com")),
            ("abre tradingview!", "abrir",
             ("TradingView", "https://www.tradingview.com")),
            ("¿abre youtube,", "abrir",
             ("YouTube", "https://www.youtube.com")),
            ("curso de python dalto:", "abrir",
             ("curso de Python",
              "https://www.youtube.com/watch?v=nKPbfIU442g")),
            ("hora?", "hora", ()),
            ("dime la hora;", "hora", ()),
        )
        for texto, nombre_esperado, argumentos_esperados in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r devolvio %d acciones y tiene que devolver 1: %r"
                    % (texto, len(acciones), acciones))
                self.assertEqual(
                    acciones[0].nombre, nombre_esperado,
                    "%r lleva el signo pegado a la palabra de la orden y no "
                    "se reconocio: debio dar %r y dio %r"
                    % (texto, nombre_esperado, acciones[0].nombre))
                self.assertEqual(
                    acciones[0].argumentos, argumentos_esperados,
                    "%r reconocio %r y tenia que reconocer %r"
                    % (texto, acciones[0].argumentos, argumentos_esperados))

    def test_comando_conocido_no_tambien_va_a_la_ia(self):
        """B6: si ya se reconocio un comando, NO se manda a la IA

        El defecto era `if not any(...) or len(palabra) > 4`: con mas de 4
        palabras mandaba el texto a la IA AUNQUE fuera un comando conocido,
        y como los "if" de arriba ya habian corrido, acababa haciendo dos
        cosas a la vez.
        """
        casos = (
            "por favor abre youtube ahora",
            "jarvis que hora es ahora por favor",
            "reproduce metallica otra vez por favor",
        )
        for texto in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r disparo %d acciones y tiene que disparar UNA: %r"
                    % (texto, len(acciones), acciones))
                self.assertNotEqual(
                    acciones[0].nombre, "consultar_ia",
                    "%r es un comando conocido pero tambien se lo paso a "
                    "la IA: %r" % (texto, acciones))

    def test_texto_sin_ordenes_conocidas_va_a_la_ia(self):
        """lo que no es ninguna orden conocida se pasa a la IA, entero"""
        casos = ("cual es el sentido de la vida", "hola")
        for texto in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1, "%r devolvio %r" % (texto, acciones))
                self.assertEqual(
                    acciones[0].nombre, "consultar_ia",
                    "%r no es ninguna orden conocida y devolvio %r"
                    % (texto, acciones))
                self.assertEqual(
                    acciones[0].argumentos, (texto,),
                    "la IA tiene que recibir el texto entero del usuario")

    def test_reconocedor_no_toca_audio_red_ni_ia(self):
        """interpretar es pura: cero audio, cero red, cero IA

        Se mide por AST (imports, nombres de variables y atributos), no como
        texto: un COMENTARIO que mencione "hablar" no ejecuta nada y no tiene
        que delatar al modulo. Solo puede importar stdlib: dataclasses y
        unicodedata (esta ultima para quitar tildes al COMPARAR, que la usa
        _sin_acentos desde el 2026-10-26).
        """
        texto = RUTA_RECONOCEDOR.read_text(encoding="utf-8")
        arbol = ast.parse(texto)
        importados = set()
        nombres_usados = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                importados.update(
                    alias.name.split(".")[0] for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level == 0 and nodo.module:
                    importados.add(nodo.module.split(".")[0])
            elif isinstance(nodo, ast.Name):
                nombres_usados.add(nodo.id)
            elif isinstance(nodo, ast.Attribute):
                nombres_usados.add(nodo.attr)
        self.assertEqual(
            sorted(importados - {"dataclasses", "unicodedata"}), [],
            "main/reconocedor.py importa librerias que no son de stdlib")
        prohibidos = {
            "hablar", "preguntar_a_ia", "webbrowser", "anthropic",
            "sounddevice", "edge_tts", "playsound", "pywhatkit",
            "speech_recognition", "asyncio", "requests",
        }
        encontrados = sorted(nombres_usados & prohibidos)
        self.assertEqual(
            encontrados, [],
            "main/reconocedor.py usa nombres de ejecucion: %s" % encontrados)


if __name__ == "__main__":
    unittest.main()
