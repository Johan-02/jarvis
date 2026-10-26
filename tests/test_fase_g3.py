# pruebas de la FASE G3 del proyecto Jarvis (2026-10-23): la VOZ conectada
# al bucle agente.
#
# Tres frentes:
#   1. procesar_orden delega en atender: el texto reconocido pasa a
#      atender (y su respuesta, si la hay, se LEE EN VOZ), NO al
#      `for accion in interpretar(...)` viejo;
#   2. LA CONFIRMACION UNICA: una destructiva ("apaga la computadora")
#      pregunta EXACTAMENTE UNA VEZ con clave (bucle agente) y UNA sola sin
#      clave (circuito E2a), contada con un hablar que SUMA llamadas; si
#      alguna vez salen 2, los dos circuitos estarian vivos para la misma
#      frase y la prueba falla;
#   3. SIN CLAVE sigue igual que hoy: 41 acciones por palabras clave y CERO
#      llamadas a la IA, con el flujo de espera de 6 segundos y dormir
#      (regresion de D y E2a) intacto.
#
# CRITERIO UNICO DE CONFIRMACION (documentado aqui y en main/main.py): los
# DOS circuitos viven en caminos MUTUAMENTE EXCLUSIVOS y nunca los dos para
# la misma frase:
#   - SIN clave (o API caida): atender cae a ejecutar_accion, que guarda el
#     pendiente y pregunta; la frase siguiente la resuelve
#     resolver_confirmacion (E2a), que corre ANTES de atender en
#     procesar_orden. Este circuito es el de siempre, el de E2a.
#   - CON clave: bucle_agente maneja la confirmacion el mismo, con hablar y
#     escuchar dentro de _ejecutar_herramienta (G2), y NUNCA deja
#     pendiente: resolver_confirmacion no tiene nada que resolver ahi.
#
# REGLAS DE ESTE ARCHIVO: la IA va SIEMPRE con cliente falso (cero llamadas
# reales), el motor va doblado y la voz tambien. Ninguna prueba abre el
# microfono, genera voz, toca el reloj del sistema ni apaga nada.

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

# los dobles de respuesta del modelo (texto, tool_use, respuesta y cliente)
# viven en test_fase_g2; se reutilizan para no duplicarlos
try:
    from tests.test_fase_g2 import _texto, _herramienta, _respuesta, _cliente
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_g2 import _texto, _herramienta, _respuesta, _cliente

from main import motor

# memoria falsa: estas pruebas NUNCA leen ni escriben el memoria.json real
MEMORIA_FALSA = {"nombre_usuario": "Yohan", "notas": ["aprende django"]}

# la frase destructiva de todas las pruebas de confirmacion: el reconocedor
# la traduce como Accion("apagar", ()) y "apagar" esta en motor.DESTRUCTIVAS
FRASE_DESTRUCTIVA = "apaga la computadora"

# fragmento IDENTICO en las DOS preguntas de confirmacion del programa (la
# de E2a, en ejecutar_accion, y la de G2, en _ejecutar_herramienta): es lo
# que el contador de hablar cuenta como PREGUNTA. Las respuestas ("apagando
# la computadora en 30 segundos") no lo contienen y no se cuentan
MARCADOR_PREGUNTA = "no se puede deshacer"


def _preguntas_de(voz):
    """cuantas llamadas a hablar fueron la pregunta de confirmacion"""
    return sum(1 for llamada in voz.call_args_list
               if MARCADOR_PREGUNTA in str(llamada.args[0]))


class TestProcesarOrdenDelegaEnAtender(unittest.TestCase):
    """el texto reconocido pasa a atender; el `for` viejo ya no existe"""

    def setUp(self):
        self.main = _main()
        self.main.olvidar_pendiente()

    def _correr_una_vuelta(self, texto, atender_falso, voz):
        """corre procesar_orden con audio doblado y UNA frase reconocida

        El reloj de la espera de 6 segundos se dobla (0.0 al fijar el
        inicio y 100.0 en la condicion) para que sin voz en la espera
        Jarvis se duerma YA y la vuelta termine sola: cero espera real.
        """
        main = self.main
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = texto
        reloj = mock.MagicMock()
        reloj.time.side_effect = [0.0, 100.0]
        consola = io.StringIO()
        with mock.patch.object(main, "time", reloj), \
                mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "atender", atender_falso), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", voz), \
                mock.patch("sys.stdout", consola):
            main.procesar_orden()
        return consola.getvalue()

    def test_el_texto_reconocido_pasa_a_atender(self):
        """atender recibe la frase tal cual, y su respuesta se lee en voz

        La mutacion que esta prueba mata: restaurar el
        `for accion in interpretar(texto): ejecutar_accion(accion)` viejo
        en procesar_orden. Ahi atender nunca se llamaria y la prueba
        revienta. Y si atender devuelve un texto (el del modelo, con
        clave), procesar_orden lo LEE EN VOZ: esa es la mitad de conectar
        la voz al bucle.
        """
        atender = mock.MagicMock(return_value="apagando en 30 segundos")
        voz = mock.MagicMock()

        self._correr_una_vuelta(FRASE_DESTRUCTIVA, atender, voz)

        atender.assert_called_once_with(FRASE_DESTRUCTIVA)
        self.assertIn(
            mock.call("apagando en 30 segundos"), voz.call_args_list,
            "atender devolvio un texto y procesar_orden no lo leyo en voz")

    def test_sin_respuesta_no_se_habla_nada_de_mas(self):
        """sin clave atender devuelve "" y procesar_orden no habla de mas

        El camino offline no puede cambiar: quien habla ahi es
        ejecutar_accion, y un hablar("") de mas seria un mp3 vacio en cada
        frase de Edward.
        """
        atender = mock.MagicMock(return_value="")
        voz = mock.MagicMock()

        self._correr_una_vuelta("sube el volumen", atender, voz)

        atender.assert_called_once_with("sube el volumen")
        self.assertNotIn(
            mock.call(""), voz.call_args_list,
            "procesar_orden hablo un texto vacio: seria un mp3 de mas")


class TestLaDestructivaPreguntaUnaSolaVez(unittest.TestCase):
    """contado con un hablar que suma: UNA pregunta por frase destructiva"""

    def setUp(self):
        # cada prueba arranca sin pendiente de la anterior: el estado vive
        # en una variable de modulo y sin esto una prueba podria aprovechar
        # el pendiente zombi de la anterior
        self.main = _main()
        self.main.olvidar_pendiente()

    def test_con_clave_pregunta_una_sola_vez(self):
        """con clave el bucle (G2) pregunta y escucha: UNA sola vez

        El modelo pide la herramienta apagar, el motor frena, EL PROGRAMA
        pregunta (hablar), escucha a Edward y con su "si" vuelve a llamar
        con confirmado=True. El texto final lo devuelve atender SIN
        hablarlo: por eso el TOTAL de hablar es exactamente 1, y esa 1 es
        la pregunta.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("apagar", {}, "t_apagar")], "tool_use"),
            _respuesta([_texto("apagando en 30 segundos.")], "end_turn"),
        ])

        def ejecutar_falso(accion, confirmado=False):
            if not confirmado:
                return motor.Resultado(
                    ok=False, detalle=motor.REQUIERE_CONFIRMACION)
            return motor.Resultado(
                ok=True, detalle="apagando la computadora en 30 segundos")

        # el hablar del modulo va doblado con el MISMO contador: si un
        # segundo circuito (por mutation o por bug) preguntara por ahi, el
        # contador lo ve y la prueba falla. Ademas ninguna prueba puede
        # generar voz real
        voz = mock.MagicMock()
        escucha = mock.MagicMock(return_value="si")
        with mock.patch.object(main, "clave_ia", "clave-falsa"), \
                mock.patch.object(main, "_obtener_cliente_ia",
                                  mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main, "hablar", voz), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(
                                      side_effect=ejecutar_falso)) as ejecutar:
            texto = main.atender(FRASE_DESTRUCTIVA,
                                 memoria=dict(MEMORIA_FALSA),
                                 hablar=voz, escuchar=escucha)

        self.assertEqual(
            _preguntas_de(voz), 1,
            "con clave la destructiva pregunto %d veces (contador de "
            "hablar marcado con %r): tiene que ser UNA"
            % (_preguntas_de(voz), MARCADOR_PREGUNTA))
        self.assertEqual(
            voz.call_count, 1,
            "con clave se hablaron %d frases y solo puede hablar la "
            "pregunta: el texto final lo devuelve atender sin hablarlo"
            % voz.call_count)
        escucha.assert_called_once_with()
        self.assertEqual(
            ejecutar.call_count, 2,
            "el motor se llamo %d veces: tenia que ser 2 (freno + "
            "confirmada)" % ejecutar.call_count)
        self.assertFalse(
            ejecutar.call_args_list[0].kwargs.get("confirmado", False),
            "la primera llamada ya vino confirmada: nadie pregunto aun")
        self.assertTrue(
            ejecutar.call_args_list[1].kwargs.get("confirmado"),
            "tras el si de Edward el motor no se llamo con confirmado=True")
        self.assertEqual(
            texto, "apagando en 30 segundos.",
            "atender no devolvio el texto final del modelo: %r" % (texto,))

    def test_sin_clave_pregunta_una_sola_vez(self):
        """sin clave el circuito E2a pregunta: UNA sola vez

        atender cae a ejecutar_accion, que guarda el pendiente y pregunta
        (el hablar del modulo, aqui doblado). No hay escucha en esta
        frase: la respuesta ("si"/"no") la resuelve
        resolver_confirmacion en la SIGUIENTE frase de procesar_orden, que
        es el circuito de E2a de toda la vida.
        """
        main = self.main
        voz = mock.MagicMock()
        cliente = mock.MagicMock()
        freno = motor.Resultado(ok=False, detalle=motor.REQUIERE_CONFIRMACION)
        with mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "_obtener_cliente_ia", cliente), \
                mock.patch.object(main, "hablar", voz), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(return_value=freno)) \
                as ejecutar:
            texto = main.atender(FRASE_DESTRUCTIVA,
                                 hablar=mock.MagicMock(),
                                 escuchar=mock.MagicMock())

        self.assertEqual(
            cliente.call_count, 0,
            "sin clave se llego a construir el cliente de la IA: el "
            "camino offline no puede depender ni de clave ni de red")
        self.assertEqual(
            _preguntas_de(voz), 1,
            "sin clave la destructiva pregunto %d veces (contador de "
            "hablar marcado con %r): tiene que ser UNA"
            % (_preguntas_de(voz), MARCADOR_PREGUNTA))
        self.assertEqual(
            voz.call_count, 1,
            "sin clave se hablaron %d frases y solo puede hablar la "
            "pregunta" % voz.call_count)
        self.assertEqual(
            ejecutar.call_count, 1,
            "sin clave el motor se llamo %d veces: solo puede ser 1, la "
            "del freno; la confirmada es de la frase siguiente"
            % ejecutar.call_count)
        self.assertEqual(
            texto, "",
            "sin clave atender no puede devolver texto: %r" % (texto,))
        self.assertIsNotNone(
            main._accion_pendiente,
            "no quedo pendiente la accion: a la frase siguiente no habria "
            "nada que confirmar")

    def test_a_traves_de_procesar_orden_tambien_pregunta_una_sola_vez(self):
        """el escenario completo: destructiva + "si", con resolver VIVO

        Aqui los DOS circuitos podrian pisarse: resolver_confirmacion (E2a)
        corre ANTES de atender en cada vuelta de procesar_orden. Edward
        contesta "si" DURANTE la espera de 6 segundos (la espera detecta
        voz y se vuelve a escuchar sin dormir, que es el flujo real). Si
        alguien unificara mal y los dos preguntaran para la misma
        destructiva, el contador marca 2 y la prueba falla.
        """
        main = self.main
        voz = mock.MagicMock()
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.side_effect = [
            FRASE_DESTRUCTIVA, "si"]
        reloj = mock.MagicMock()
        # vuelta 1: 0.0 al fijar el inicio y 1.0 en la condicion (1.0 < 6,
        # entra a la espera, que encuentra VOZ y repite); vuelta 2: 0.0 y
        # 7.0 (7.0 >= 6, sin voz, Jarvis se duerme y termina)
        reloj.time.side_effect = [0.0, 1.0, 0.0, 7.0]

        def ejecutar_falso(accion, confirmado=False):
            if not confirmado:
                return motor.Resultado(
                    ok=False, detalle=motor.REQUIERE_CONFIRMACION)
            return motor.Resultado(
                ok=True, detalle="apagando la computadora en 30 segundos")

        consola = io.StringIO()
        with mock.patch.object(main, "time", reloj), \
                mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "hablar", voz), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(
                                      side_effect=ejecutar_falso)) \
                as ejecutar, \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(
                    main.sd, "rec",
                    mock.MagicMock(
                        return_value=np.full((100, 1), 5000, dtype="int16"))), \
                mock.patch.object(main.sd, "wait", mock.MagicMock()), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch("sys.stdout", consola):
            main.procesar_orden()

        self.assertEqual(
            _preguntas_de(voz), 1,
            "a traves de procesar_orden la destructiva pregunto %d veces: "
            "los DOS circuitos de confirmacion estarian vivos para la "
            "misma frase" % _preguntas_de(voz))
        self.assertEqual(
            ejecutar.call_count, 2,
            "el motor se llamo %d veces: tenia que ser 2 (freno en la "
            "frase 1 + confirmada en la frase 2)" % ejecutar.call_count)
        self.assertTrue(
            ejecutar.call_args_list[1].kwargs.get("confirmado"),
            "el si de Edward no llego al motor como confirmado=True")
        self.assertIsNone(
            main._accion_pendiente,
            "tras confirmar quedo un pendiente zombi")
        self.assertIn(
            "dormido", consola.getvalue(),
            "el flujo de dormir no ocurrio: la regresion de D esta rota")


class TestSinClaveSigueIgualQueHoy(unittest.TestCase):
    """sin clave: 41 acciones por palabras clave, cero llamadas a la IA"""

    def setUp(self):
        self.main = _main()
        self.main.olvidar_pendiente()

    def test_sin_clave_llega_a_las_acciones_sin_llamar_a_la_ia(self):
        """el camino offline de atender no cambio con la G3

        Cliente NI construido, frase por interpretar hasta el motor, y las
        41 acciones del REGISTRO intactas (la H2 agrego mirar_pantalla y la
        H3 las tres de memoria). atender devuelve "" porque quien habla es
        ejecutar_accion, igual que siempre.
        """
        main = self.main
        self.assertEqual(
            len(motor.REGISTRO), 41,
            "el REGISTRO de la FASE E2 tenia 37 acciones; la H2 agrego "
            "mirar_pantalla y la H3 recordar, olvidar y leer_memoria")
        cliente = mock.MagicMock()
        with mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "_obtener_cliente_ia", cliente), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(return_value=motor.Resultado(
                                      True, "volumen subido"))) as ejecutar:
            texto = main.atender("sube el volumen",
                                 hablar=mock.MagicMock(),
                                 escuchar=mock.MagicMock())

        cliente.assert_not_called()
        self.assertEqual(
            texto, "",
            "sin clave no hay texto final que devolver: %r" % (texto,))
        self.assertEqual(
            ejecutar.call_args.args[0].nombre, "volumen_subir",
            "sin clave la frase no llego al camino de siempre "
            "interpretar -> motor: %r" % (ejecutar.call_args.args[0],))


if __name__ == "__main__":
    unittest.main()
