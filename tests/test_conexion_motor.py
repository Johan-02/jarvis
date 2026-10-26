# pruebas de la FASE E2a del proyecto Jarvis (2026-10-21): la CONEXION entre
# main/main.py y el motor de main/motor.py.
#
# Cinco piezas medidas, una por prueba (mas dos AST de guardia):
#   1. una accion del RECONOCEDOR que existe en el REGISTRO llega al motor
#      ya TRADUCIDA ("hora" del reconocedor -> "hora_actual" del REGISTRO);
#   2. una destructiva devuelve REQUIERE_CONFIRMACION, NO se ejecuta nada, y
#      la aceptacion de Edward vuelve a llamar al motor con confirmado=True;
#   3. el rechazo NO vuelve a llamar al motor;
#   4. una frase que no es respuesta no deja el pendiente pegado (zombi);
#   5. una accion fuera del REGISTRO sigue la cadena if/elif de antes sin
#      reventar.
#
# REGLA DE ESTE ARCHIVO: motor.ejecutar, hablar y webbrowser van SIEMPRE
# doblados. Ninguna prueba abre el microfono, genera voz, toca la papelera
# de reciclaje, abre el navegador ni llama a la IA.

import ast
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

RUTA_MAIN = RAIZ / "main" / "main.py"

# el doble de las librerias de audio vive en test_fase_b_main; se reutiliza
# para poder importar main.main sin microfono ni red
try:
    from tests.test_fase_b_main import _main
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_b_main import _main

from main import motor
from main.reconocedor import Accion


def _nombre_de_llamada(llamada):
    """nombre simple de una llamada AST: foo(...) o foo.bar(...)"""
    if isinstance(llamada.func, ast.Name):
        return llamada.func.id
    if isinstance(llamada.func, ast.Attribute):
        return llamada.func.attr
    return None


class TestConexionMotor(unittest.TestCase):
    """main.py manda al motor lo que el motor sabe hacer; el resto no cambia"""

    def setUp(self):
        # cada prueba arranca sin pendiente de la anterior: el estado vive en
        # una variable de modulo y sin esta limpieza una prueba podria
        # aprovecharse de la zombi de la anterior
        self.main = _main()
        self.main.olvidar_pendiente()

    def test_accion_en_registro_llega_traducida(self):
        """el "hora" del reconocedor llega al motor como "hora_actual"

        Sin el traductor TRADUCCION_MOTOR, "hora" no es una clave del
        REGISTRO y la accion caeria a la cadena if/elif de antes: el motor
        nunca se enteraria. El nombre tiene que ir TRADUCIDO en la llamada y
        los argumentos tienen que viajar intactos.
        """
        main = self.main
        resultado = motor.Resultado(ok=True, detalle="14:30")
        with mock.patch.object(main.motor, "ejecutar",
                               mock.MagicMock(return_value=resultado)) \
                as motor_falso, \
                mock.patch.object(main, "hablar",
                                  mock.MagicMock()) as voz:
            main.ejecutar_accion(Accion("hora", ()))

        self.assertEqual(motor_falso.call_count, 1,
                         "el motor no fue llamado: la accion no salio del "
                         "REGISTRO traducida")
        llamada = motor_falso.call_args
        accion_motor = llamada.args[0]
        self.assertEqual(
            accion_motor.nombre, "hora_actual",
            "el motor recibio el nombre %r y tenia que recibir la traduccion "
            "a hora_actual" % (accion_motor.nombre,))
        self.assertEqual(
            accion_motor.argumentos, (),
            "los argumentos no viajaron intactos: %r"
            % (accion_motor.argumentos,))
        self.assertFalse(
            llamada.kwargs.get("confirmado", False),
            "una accion inocua no puede llegar confirmada al motor")
        # lo que devuelva el motor lo dice Jarvis (el RESULTADO del motor, y
        # no la frase vieja de la rama hora: hablar se llamo UNA sola vez)
        voz.assert_called_once_with("14:30")

    def test_destructiva_pide_confirmacion_y_las_aceptacion_confirma(self):
        """destructiva -> REQUIERE_CONFIRMACION sin ejecutar; "si" confirma

        La primera llamada al motor devuelve el freno del motor; en ese
        punto Jarvis pregunta y NO se ejecuta nada (el contador sigue en 1).
        La aceptacion de Edward vuelve a llamar al motor con confirmado=True
        y la MISMA accion guardada.
        """
        main = self.main
        freno = motor.Resultado(ok=False, detalle=motor.REQUIERE_CONFIRMACION)
        hecho = motor.Resultado(ok=True,
                                detalle="papelera de reciclaje vaciada")
        with mock.patch.object(main.motor, "ejecutar",
                               mock.MagicMock(
                                   side_effect=[freno, hecho])) \
                as motor_falso, \
                mock.patch.object(main, "hablar",
                                  mock.MagicMock()) as voz:
            main.ejecutar_accion(Accion("papelera_vaciar", ()))

            self.assertEqual(
                motor_falso.call_count, 1,
                "el freno del motor no freno nada: el motor fue llamado "
                "mas de una vez sin confirmacion")
            self.assertFalse(
                motor_falso.call_args.kwargs.get("confirmado", False),
                "la destructiva llego confirmada sin que nadie confirmara")
            self.assertIsNotNone(
                main._accion_pendiente,
                "no se guardo la accion pendiente: Jarvis pregunto y la "
                "aceptacion no tendria que ejecutar")
            voz.assert_called_once()
            self.assertIn(
                "no se puede deshacer", voz.call_args.args[0],
                "Jarvis no pregunto por la confirmacion: dijo %r"
                % (voz.call_args.args[0],))

            respuesta = main.resolver_confirmacion("s\u00ed, hazlo")

        self.assertTrue(
            respuesta,
            "la aceptacion no fue reconocida como respuesta")
        self.assertEqual(
            motor_falso.call_count, 2,
            "la aceptacion no volvio a llamar al motor")
        segunda = motor_falso.call_args
        self.assertTrue(
            segunda.kwargs.get("confirmado"),
            "la aceptacion llamo al motor sin confirmado=True")
        self.assertEqual(
            segunda.args[0].nombre, "papelera_vaciar",
            "la aceptacion no ejecuto la MISMA accion pendiente")
        self.assertIsNone(
            main._accion_pendiente,
            "tras confirmar, el pendiente quedo pegado (zombi)")
        # el resultado de la ejecucion confirmada lo dijo Jarvis
        voz.assert_called_with("papelera de reciclaje vaciada")

    def test_rechazo_no_vuelve_a_llamar_al_motor(self):
        """el rechazo limpia el pendiente y NO ejecuta NADA

        Unica llamada permitida: la del freno inicial. Si el rechazo
        volviera a llamar al motor (aunque sea sin confirmar) seria un
        riesgo inutil; y si dejara el pendiente vivo, la frase siguiente se
        tragaria como respuesta vieja.
        """
        main = self.main
        freno = motor.Resultado(ok=False, detalle=motor.REQUIERE_CONFIRMACION)
        with mock.patch.object(main.motor, "ejecutar",
                               mock.MagicMock(return_value=freno)) \
                as motor_falso, \
                mock.patch.object(main, "hablar",
                                  mock.MagicMock()):
            main.ejecutar_accion(Accion("papelera_vaciar", ()))
            respuesta = main.resolver_confirmacion("no, cancela")

        self.assertTrue(respuesta, "el rechazo no fue reconocido como "
                                   "respuesta")
        self.assertEqual(
            motor_falso.call_count, 1,
            "el rechazo volvio a llamar al motor (%d llamadas)"
            % motor_falso.call_count)
        self.assertIsNone(
            main._accion_pendiente,
            "tras el rechazo, el pendiente quedo pegado (zombi)")

    def test_frase_que_no_es_respuesta_no_deja_estado_pegado(self):
        """una orden normal tras el pendiente se procesa como orden normal

        resolver_confirmacion tiene que devolver False (para que
        procesar_orden la procese igual que siempre) Y haber borrado el
        pendiente en el camino: si lo dejara vivo, la proxima frase del
        usuario se tragaria como respuesta a una pregunta vieja.
        """
        main = self.main
        freno = motor.Resultado(ok=False, detalle=motor.REQUIERE_CONFIRMACION)
        with mock.patch.object(main.motor, "ejecutar",
                               mock.MagicMock(return_value=freno)) \
                as motor_falso, \
                mock.patch.object(main, "hablar",
                                  mock.MagicMock()):
            main.ejecutar_accion(Accion("papelera_vaciar", ()))
            respuesta = main.resolver_confirmacion("que hora es")

        self.assertFalse(
            respuesta,
            "una orden normal se comio la frase en vez de devolverla para "
            "que se procese")
        self.assertEqual(
            motor_falso.call_count, 1,
            "la frase normal volvio a llamar al motor por su cuenta")
        self.assertIsNone(
            main._accion_pendiente,
            "la frase normal dejo el pendiente pegado (zombi)")

    def test_accion_fuera_de_registro_sigue_como_antes(self):
        """"abrir" no esta en el REGISTRO: entra al navegador, no al motor

        Y una accion que nadie conoce sigue imprimiendo el MISMO aviso de
        siempre en consola, sin reventar. Es la cadena if/elif intacta.
        """
        main = self.main
        with mock.patch.object(main.motor, "ejecutar",
                               mock.MagicMock()) as motor_falso, \
                mock.patch.object(main, "webbrowser") as navegador, \
                mock.patch.object(main, "hablar", mock.MagicMock()):
            main.ejecutar_accion(
                Accion("abrir", ("Gmail", "https://mail.google.com")))

            motor_falso.assert_not_called()
            navegador.open.assert_called_once_with("https://mail.google.com")
            self.assertIsNone(
                main._accion_pendiente,
                "una accion fuera del REGISTRO dejo pendiente algo")

            consola = io.StringIO()
            with contextlib.redirect_stdout(consola):
                main.ejecutar_accion(Accion("inventada", ()))

        self.assertIn(
            "aviso: no se ejecuto la accion inventada", consola.getvalue(),
            "una accion desconocida ya no avisa en consola")


class TestProcesarOrdenUsaElPendiente(unittest.TestCase):
    """guardas de integracion: procesar_orden tiene que pasar por el circuito

    Las 5 pruebas de arriba son de UNIDAD: llaman a ejecutar_accion y a
    resolver_confirmacion directamente. Si procesar_orden dejara de llamar a
    resolver_confirmacion, esas 5 seguirian pasando y el pendiente quedaria
    pegado para siempre en el programa real. Estas dos pruebas leen el
    codigo por AST para cerrar ese hueco.
    """

    def _procesar_orden(self):
        arbol = ast.parse(RUTA_MAIN.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and \
                    nodo.name == "procesar_orden":
                return nodo
        self.fail("no se encontro la funcion procesar_orden en main/main.py")

    def _llamadas(self, nombre):
        nodo = self._procesar_orden()
        return sorted(
            llamada.lineno for llamada in ast.walk(nodo)
            if isinstance(llamada, ast.Call)
            and _nombre_de_llamada(llamada) == nombre)

    def test_resuelve_la_confirmacion_antes_de_atender(self):
        """procesar_orden pasa la frase por resolver_confirmacion primero

        El ORDEN importa: si atender corriera antes, la frase "si" ya se
        habria mandado al bucle de la IA (o a interpretar) antes de que el
        pendiente se enterara de que era la respuesta. FASE G3:
        procesar_orden ya NO llama a interpretar directo; la frase va a
        atender, que es la que decide el camino (con clave el bucle, sin
        clave interpretar). Lo que no se puede perder es esto: el
        resolver_confirmacion ANTES de procesar la frase como orden.
        """
        resolver = self._llamadas("resolver_confirmacion")
        atender = self._llamadas("atender")
        self.assertTrue(
            resolver,
            "procesar_orden no llama a resolver_confirmacion: el pendiente "
            "nunca se resuelve y queda pegado para siempre")
        self.assertTrue(
            atender,
            "procesar_orden dejo de llamar a atender: la voz no llega ni "
            "al bucle agente ni al camino de siempre")
        self.assertLess(
            resolver[0], atender[0],
            "resolver_confirmacion se llama DESPUES de atender: la "
            "frase respuesta ya se proceso como orden normal")

    def test_olvida_el_pendiente_al_dormirse(self):
        """el pendiente NO sobrevive al sueño de Jarvis

        Si Edward no responde y Jarvis se duerme, la pregunta queda vieja:
        preguntarle algo minutos despues al despertar seria un estado zombi.
        """
        self.assertTrue(
            self._llamadas("olvidar_pendiente"),
            "procesar_orden no olvida el pendiente al dormirse")


if __name__ == "__main__":
    unittest.main()
