# pruebas del FILTRO DE HERRAMIENTAS (main/main.py::_herramientas_para,
# 2026-10-26).
#
# POR QUE EXISTE: MEDIDO con la clave real, el catalogo de las 41 herramientas
# cuesta 2.635 tokens de entrada y una frase completa 2.684. La capa GRATIS de
# Groq da 7.000 tokens por minuto, asi que con las 41 solo entran 1,3 frases
# por minuto y despues llega el HTTP 429. Con UNA sola herramienta el mismo
# qwen mide 546 tokens (5 veces menos).
#
# LA REGLA: si la frase pide UNA sola orden conocida (y no trae varias
# encadenadas) se manda SOLO esa herramienta; en cualquier otro caso se mandan
# las 41. Y NUNCA se deja al modelo sin herramientas: si el filtro dejara la
# lista vacia, se cae a las 41.
#
# REGLAS DE ESTE ARCHIVO: CERO red, CERO clave, CERO llamadas a Groq y CERO
# escrituras en disco. Solo se mira QUE LISTA devuelve la funcion. La consola se
# captura siempre, para no ensuciar la salida de la suite con el aviso.
#
# OJO CON LOS NUMEROS: hoy el catalogo tiene 41 herramientas, pero las pruebas
# comparan contra `herramientas.herramientas()` y no contra el 41 escrito a
# mano: el dia que Edward agregue una accion al REGISTRO, esto sigue valiendo.

import contextlib
import io
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

try:
    from tests.test_fase_b_main import _main
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_b_main import _main

from main import herramientas
from main.reconocedor import Accion


class TestHerramientasPara(unittest.TestCase):
    """_herramientas_para: que catalogo se le manda al modelo en cada frase"""

    def setUp(self):
        self.main = _main()
        self.todas = [definicion["name"]
                      for definicion in herramientas.herramientas()]

    def _llamar(self, texto):
        """llama al filtro con la consola capturada -> (nombres, consola)

        Se captura SIEMPRE: el filtro imprime un aviso cuando recorta y esas
        lineas no tienen que aparecer mezcladas con el resumen de la suite.
        """
        consola = io.StringIO()
        with contextlib.redirect_stdout(consola):
            definiciones = self.main._herramientas_para(texto)
        return [definicion["name"] for definicion in definiciones], \
            consola.getvalue()

    def test_que_hora_es_manda_solo_hora_actual(self):
        """la orden "hora" del reconocedor se traduce a "hora_actual"

        Se comprueba el NOMBRE exacto y que sea UNA sola: sin la traduccion de
        TRADUCCION_MOTOR el filtro no encontraria nada en el catalogo y
        mandaria las 41 sin que nadie se diera cuenta.
        """
        nombres, consola = self._llamar("que hora es")
        self.assertEqual(nombres, ["hora_actual"],
                         "no se filtro a la herramienta de la hora: %r"
                         % (nombres,))
        self.assertIn("1 herramienta en vez de %d" % len(self.todas), consola,
                      "no quedo constancia en el log del ahorro: %r"
                      % (consola,))

    def test_sube_el_volumen_manda_solo_volumen_subir(self):
        nombres, _ = self._llamar("sube el volumen")
        self.assertEqual(nombres, ["volumen_subir"],
                         "no se filtro a volumen_subir: %r" % (nombres,))

    def test_varias_ordenes_manda_las_41(self):
        """"baja el volumen y abre chrome" necesita DOS: no se recorta

        Si se mandara solo una, Jarvis perderia una capacidad y Edward se
        quedaria con media orden hecha.
        """
        nombres, consola = self._llamar("baja el volumen y abre chrome")
        self.assertEqual(nombres, self.todas,
                         "se filtro una frase con VARIAS ordenes: %r"
                         % (nombres,))
        self.assertIn("volumen_bajar", nombres)
        self.assertIn("abrir_app", nombres)
        self.assertNotIn("aviso: mando", consola,
                         "aviso de filtrado en una frase con varias ordenes")

    def test_frase_que_no_casa_con_nada_manda_las_41(self):
        """sin orden reconocida el reconocedor devuelve consultar_ia -> 41"""
        nombres, _ = self._llamar("cuentame un chiste")
        self.assertEqual(nombres, self.todas,
                         "se filtro una frase de consultar_ia: %r"
                         % (nombres,))

    def test_si_el_filtro_deja_la_lista_vacia_manda_las_41(self):
        """NUNCA se deja al modelo sin herramientas

        Se falsea interpretar con una accion que NO esta en el catalogo (hoy
        no hay ninguna frase real que lo consiga): el filtro se quedaria con
        cero definiciones y el modelo no podria hacer nada. Tiene que caer a
        las 41.
        """
        with mock.patch.object(self.main, "interpretar",
                               return_value=[Accion("no_esta_en_el_catalogo",
                                                    ())]):
            nombres, _ = self._llamar("una frase cualquiera")
        self.assertEqual(nombres, self.todas,
                         "el modelo se quedo sin herramientas: %r"
                         % (nombres,))

    def test_una_accion_real_del_catalogo_se_filtra(self):
        """control positivo del caso anterior: abrir_app SI existe y se filtra"""
        nombres, _ = self._llamar("abre chrome")
        self.assertEqual(nombres, ["abrir_app"],
                         "abrir_app no se filtro: %r" % (nombres,))


class TestTraeVariasOrdenes(unittest.TestCase):
    """el helper que decide si la frase pide MAS DE UNA orden

    Se compara sobre el texto YA normalizado con _sin_acentos, asi que las
    tildes no cuentan: "también" llega como "tambien".
    """

    def setUp(self):
        self.main = _main()

    def test_frases_con_varias_ordenes(self):
        casos = (
            "baja el volumen y abre chrome",        # " y "
            "abre chrome, y baja el volumen",       # ","
            "abre chrome luego baja el volumen",    # " luego "
            "abre chrome después baja el volumen",  # " despues " (con tilde)
            "abre chrome también baja el volumen",   # " tambien " (con tilde)
            "abre chrome además baja el volumen",   # " ademas " (con tilde)
        )
        for frase in casos:
            self.assertTrue(
                self.main._trae_varias_ordenes(frase),
                "no se vio que pide varias ordenes: %r" % (frase,))

    def test_frase_de_una_sola_orden(self):
        self.assertFalse(self.main._trae_varias_ordenes("que hora es"),
                         "una frase de UNA orden se tomo por varias")
        self.assertFalse(self.main._trae_varias_ordenes("sube el volumen"),
                         "una frase de UNA orden se tomo por varias")

    def test_la_y_sin_espacios_no_cuenta(self):
        """el " y " va con espacios: la letra suelta no parte la frase"""
        self.assertFalse(self.main._trae_varias_ordenes("abre youtube"),
                         "la letra y de una palabra se tomo por una orden mas")


if __name__ == "__main__":
    unittest.main()
