# pruebas de la FASE G2 del proyecto Jarvis (2026-10-23): EL BUCLE AGENTICO.
#
# Cinco frentes, uno por clase:
#   1. el bucle: un tool_use del modelo se ejecuta en motor.ejecutar y su
#      detalle vuelve como tool_result en la llamada siguiente;
#   2. LA SEGURIDAD: una destructiva devuelve REQUIERE_CONFIRMACION, EL
#      PROGRAMA pregunta CON VOZ (hablar), ESCUCHA a Edward (escuchar) y
#      solo con aceptacion vuelve a llamar con confirmado=True; con rechazo
#      NO se vuelve a llamar;
#   3. el modelo NUNCA puede poner confirmado: el que lo traiga se ignora y
#      se le dice en el tool_result;
#   4. el bucle termina en texto y respeta TOPE_BUCLE_AGENTE (5): un modelo
#      que siempre pide herramientas no produce bucle infinito;
#   5. SIN CLAVE o con la API revienta (401): el bucle NO se usa y se cae a
#      interpretar() con las 41 acciones del REGISTRO (lo offline sigue
#      funcionando sin red ni clave), y el system prompt ya no pide fingir.
#
# REGLAS DE ESTE ARCHIVO: cliente_ia y messages.create van SIEMPRE
# mockeados, y motor.ejecutar TAMBIEN: aqui no hay CERO llamadas reales a la
# API, ni acciones de sistema reales, ni voz real (hablar siempre doblado).

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

# el doble de las librerias de audio vive en test_fase_b_main; se reutiliza
# para poder importar main.main sin microfono ni red
try:
    from tests.test_fase_b_main import _main
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_b_main import _main

from main import motor
from main.reconocedor import Accion

# memoria falsa: estas pruebas NUNCA leen ni escriben el memoria.json real
MEMORIA_FALSA = {"nombre_usuario": "Yohan", "notas": ["aprende django"]}


def _texto(texto):
    """bloque de texto de la respuesta de un modelo falso"""
    bloque = mock.MagicMock()
    bloque.type = "text"
    bloque.text = texto
    return bloque


def _herramienta(nombre, entrada, identificador="tool_1"):
    """bloque tool_use de la respuesta de un modelo falso"""
    bloque = mock.MagicMock()
    bloque.type = "tool_use"
    bloque.name = nombre
    bloque.input = entrada
    bloque.id = identificador
    return bloque


def _respuesta(bloques, stop_reason):
    """respuesta completa de un modelo falso"""
    respuesta = mock.MagicMock()
    respuesta.content = bloques
    respuesta.stop_reason = stop_reason
    return respuesta


def _cliente(respuestas):
    """cliente falso: messages.create devuelve las respuestas EN ORDEN

    `respuestas` puede ser una lista de respuestas o UNA excepcion (para
    simular un 401 o un corte de red). Nada de esto toca la red.
    """
    cliente = mock.MagicMock()
    if isinstance(respuestas, BaseException):
        cliente.messages.create.side_effect = respuestas
    else:
        cliente.messages.create.side_effect = list(respuestas)
    return cliente


def _ultimo_tool_result(cliente, indice=1):
    """tool_result del ultimo mensaje de la llamada `indice` a create"""
    mensajes = cliente.messages.create.call_args_list[indice].kwargs["messages"]
    contenido = mensajes[-1]["content"]
    return contenido[0]


class TestBucleConHerramientas(unittest.TestCase):
    """el modelo pide una herramienta, el motor la ejecuta, el detalle vuelve"""

    def setUp(self):
        self.main = _main()

    def test_hora_actual_se_ejecuta_en_el_motor_y_el_detalle_vuelve(self):
        """tool_use de hora_actual -> motor.ejecutar y su detalle al modelo

        El bucle hace DOS llamadas a create: la primera CON tools, la
        segunda con el tool_result del motor en los mensajes. El texto final
        ("Son las 14:30.") es lo que recibe Edward.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("hora_actual", {}, "t_hora")], "tool_use"),
            _respuesta([_texto("Son las 14:30.")], "end_turn"),
        ])
        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(return_value=motor.Resultado(
                                      True, "14:30 de la tarde"))) as ejecutar:
            texto = main.bucle_agente("que hora es", dict(MEMORIA_FALSA),
                                      hablar=mock.MagicMock(),
                                      escuchar=mock.MagicMock())

        self.assertEqual(
            texto, "Son las 14:30.",
            "el bucle no devolvio el texto final del modelo: %r" % (texto,))
        self.assertEqual(
            ejecutar.call_count, 1,
            "motor.ejecutar no se llamo UNA vez con hora_actual")
        self.assertEqual(
            ejecutar.call_args.args[0], Accion("hora_actual", ()),
            "el motor no recbio la accion hora_actual sin argumentos: %r"
            % (ejecutar.call_args.args[0],))
        self.assertFalse(
            ejecutar.call_args.kwargs.get("confirmado", False),
            "una accion inocua llego confirmada al motor")

        primer_llamado = cliente.messages.create.call_args_list[0].kwargs
        nombres = [h["name"] for h in primer_llamado["tools"]]
        self.assertEqual(
            nombres, sorted(motor.REGISTRO),
            "la primera llamada no llevo TODAS las herramientas del "
            "REGISTRO: %s" % (nombres,))

        tool_result = _ultimo_tool_result(cliente)
        self.assertEqual(
            tool_result["type"], "tool_result",
            "lo que volvio al modelo no es un tool_result: %r" % (tool_result,))
        self.assertEqual(
            tool_result["tool_use_id"], "t_hora",
            "el tool_result no trae el id de su tool_use")
        self.assertIn(
            "14:30 de la tarde", tool_result["content"],
            "el detalle del motor NO volvio al modelo: %r"
            % (tool_result["content"],))
        self.assertFalse(
            tool_result["is_error"],
            "una accion exitosa volvio marcada como error")

    def test_destructiva_pregunta_en_voz_y_confirma_solo_si_acepta(self):
        """REQUIERE_CONFIRMACION -> hablar, escuchar, y recien confirmar

        El bucle se DETIENE en el freno: EL PROGRAMA habla la pregunta,
        escucha a Edward y con aceptacion llama a motor.ejecutar con
        confirmado=True. El resultado de esa negociacion es lo que recibe
        el modelo (tool_result con "papelera de reciclaje vaciada").
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("papelera_vaciar", {}, "t_pap")],
                       "tool_use"),
            _respuesta([_texto("papelera vaciada.")], "end_turn"),
        ])

        def ejecutar_falso(accion, confirmado=False):
            if not confirmado:
                return motor.Resultado(ok=False,
                                       detalle=motor.REQUIERE_CONFIRMACION)
            return motor.Resultado(ok=True,
                                   detalle="papelera de reciclaje vaciada")

        voz = mock.MagicMock()
        escucha = mock.MagicMock(return_value="si, hazlo")

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(
                                      side_effect=ejecutar_falso)) as ejecutar:
            texto = main.bucle_agente("vacia la papelera",
                                      dict(MEMORIA_FALSA),
                                      hablar=voz, escuchar=escucha)

        self.assertEqual(
            ejecutar.call_count, 2,
            "el motor se llamo %d veces: tenia que ser 2 (freno + "
            "confirmada)" % ejecutar.call_count)
        self.assertFalse(
            ejecutar.call_args_list[0].kwargs.get("confirmado", False),
            "la primera llamada ya vino confirmada: nadie pregunto aun")
        self.assertTrue(
            ejecutar.call_args_list[1].kwargs.get("confirmado"),
            "tras la aceptacion de Edward el motor NO se llamo con "
            "confirmado=True")
        self.assertEqual(
            ejecutar.call_args_list[1].args[0],
            Accion("papelera_vaciar", ()),
            "la confirmacion no ejecuto la MISMA accion")

        self.assertTrue(
            voz.called,
            "no se hablo la pregunta de confirmacion: el que pregunta es "
            "EL PROGRAMA, con voz, no el modelo")
        self.assertIn(
            "no se puede deshacer", voz.call_args.args[0],
            "la pregunta no avisa de que la accion no se puede deshacer: %r"
            % (voz.call_args.args[0],))
        escucha.assert_called_once_with()

        tool_result = _ultimo_tool_result(cliente)
        self.assertIn(
            "papelera de reciclaje vaciada", tool_result["content"],
            "el resultado de la negociacion NO llego al modelo: %r"
            % (tool_result["content"],))
        self.assertEqual(
            texto, "papelera vaciada.",
            "el texto final del modelo no llego a Edward: %r" % (texto,))

    def test_rechazo_de_edward_no_ejecuta_nada_y_se_lo_dice_al_modelo(self):
        """con rechazo el motor se llamo SOLO la vez del freno

        Y el tool_result le dice al modelo que el usuario rechazo la accion
        y que NO la repita: nunca un "hecho" falso.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("papelera_vaciar", {}, "t_pap")],
                       "tool_use"),
            _respuesta([_texto("entendido, no la toco.")], "end_turn"),
        ])
        voz = mock.MagicMock()
        escucha = mock.MagicMock(return_value="no, cancela")

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(
                                      return_value=motor.Resultado(
                                          ok=False,
                                          detalle=motor
                                          .REQUIERE_CONFIRMACION))) as ejecutar:
            texto = main.bucle_agente("vacia la papelera",
                                      dict(MEMORIA_FALSA),
                                      hablar=voz, escuchar=escucha)

        self.assertEqual(
            ejecutar.call_count, 1,
            "el rechazo volvio a llamar al motor (%d llamadas): NO se "
            "ejecuto nada pero se pregunto igual" % ejecutar.call_count)
        self.assertFalse(
            ejecutar.call_args.kwargs.get("confirmado", False),
            "el motor fue llamado con confirmado=True sin aceptacion")
        voz.assert_called_once()
        escucha.assert_called_once_with()

        tool_result = _ultimo_tool_result(cliente)
        self.assertIn(
            "rechazo", tool_result["content"],
            "el modelo no se entero del rechazo: %r"
            % (tool_result["content"],))
        self.assertIn(
            "NO la repitas", tool_result["content"],
            "al modelo no se le prohibio repetir la accion rechazada: %r"
            % (tool_result["content"],))
        self.assertTrue(
            tool_result["is_error"],
            "el rechazo volvio como exito: seria decirle al modelo que la "
            "accion se hizo")
        self.assertEqual(
            texto, "entendido, no la toco.",
            "el bucle no continuo hasta el texto del modelo")

    def test_confirmado_que_trae_el_modelo_se_ignora_y_se_avisa(self):
        """cualquier tool_use que traiga `confirmado` se quita del input

        El parametro no esta en ningun input_schema (lo garantiza G1); si
        el modelo lo inventara, aqui se ignora, NO se pasa al motor, y se
        le dice en el tool_result. Sin aceptacion de Edward no hay
        negociacion que negociar: ni hablar ni escuchar se usan.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("papelera_vaciar",
                                     {"confirmado": True}, "t_conf")],
                       "tool_use"),
            _respuesta([_texto("listo.")], "end_turn"),
        ])
        voz = mock.MagicMock()
        escucha = mock.MagicMock()

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(
                                      return_value=motor.Resultado(
                                          True,
                                          "papelera vaciada"))) as ejecutar:
            main.bucle_agente("vacia la papelera", dict(MEMORIA_FALSA),
                              hablar=voz, escuchar=escucha)

        self.assertEqual(
            ejecutar.call_count, 1,
            "el input con confirmado genero mas de una llamada al motor")
        self.assertFalse(
            ejecutar.call_args.kwargs.get("confirmado", False),
            "confirmado del MODELO llego al motor: el modelo decidio la "
            "confirmacion por su cuenta")
        tool_result = _ultimo_tool_result(cliente)
        self.assertIn(
            "confirmado", tool_result["content"],
            "no se le dijo al modelo que su confirmado se ignoro: %r"
            % (tool_result["content"],))
        self.assertIn(
            "ignoro", tool_result["content"],
            "el aviso sobre el confirmado ignorado no esta claro: %r"
            % (tool_result["content"],))
        voz.assert_not_called()
        escucha.assert_not_called()


class TestTopeDeIteraciones(unittest.TestCase):
    """un modelo que siempre pide herramientas NO puede hacer bucle infinito"""

    def setUp(self):
        self.main = _main()

    def test_un_modelo_que_siempre_pide_herramientas_corta_en_el_tope(self):
        """TOPE_BUCLE_AGENTE=5: cinco llamadas y devuelve un texto honesto

        El cliente falso solo tiene CINCO respuestas: si el bucle pidiera
        una mas, create revienta con StopIteration y la prueba falla. No
        puede colgarse: el tope esta en el codigo y aqui se mide.
        """
        main = self.main
        self.assertEqual(
            main.TOPE_BUCLE_AGENTE, 5,
            "el tope acordado para la FASE G2 es 5 iteraciones")
        respuestas = [
            _respuesta([_herramienta("hora_actual", {}, "t%d" % indice)],
                       "tool_use")
            for indice in range(main.TOPE_BUCLE_AGENTE)
        ]
        cliente = _cliente(respuestas)

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(
                                      return_value=motor.Resultado(
                                          True, "14:30"))) as ejecutar:
            texto = main.bucle_agente("dime la hora", dict(MEMORIA_FALSA),
                                      hablar=mock.MagicMock(),
                                      escuchar=mock.MagicMock())

        self.assertEqual(
            cliente.messages.create.call_count, main.TOPE_BUCLE_AGENTE,
            "el bucle hizo %d llamadas y el tope es %d"
            % (cliente.messages.create.call_count,
               main.TOPE_BUCLE_AGENTE))
        self.assertEqual(
            ejecutar.call_count, main.TOPE_BUCLE_AGENTE,
            "no se ejecutaron las %d herramientas pedidas"
            % main.TOPE_BUCLE_AGENTE)
        self.assertIsInstance(texto, str)
        self.assertTrue(texto, "al cortar por tope se devolvio texto vacio")
        self.assertIn(
            "corte por seguridad", texto,
            "el corte por tope no se aviso con honestidad: %r" % (texto,))


class TestSinClaveSigueFuncionando(unittest.TestCase):
    """sin clave (o con la API caida) el bucle NO se usa: interpretar manda"""

    def setUp(self):
        self.main = _main()

    def test_sin_clave_se_usa_interpretar_y_las_acciones_siguen(self):
        """clave ausente o vacia -> atender cae al camino de HOY

        Se comprueba con las DOS formas de no tener clave (None y ""): el
        cliente ni se construye, la frase se pasa por interpretar() y la
        accion llega al motor. Y el REGISTRO sigue teniendo sus acciones
        (41 desde la FASE H3): nada de lo offline se rompio.
        """
        main = self.main
        self.assertEqual(
            len(motor.REGISTRO), 41,
            "el REGISTRO de la FASE E2 tenia 37 acciones; la H2 agrego "
            "mirar_pantalla y la H3 las tres de memoria (recordar, olvidar "
            "y leer_memoria); si cambio el numero, actualice esta prueba "
            "con cuidado")
        for clave in (None, ""):
            with self.subTest(clave=clave):
                cliente = mock.MagicMock()
                with mock.patch.object(main, "anthropic_api_key", clave), \
                        mock.patch.object(main, "_obtener_cliente_ia",
                                          cliente), \
                        mock.patch.object(main, "hablar",
                                          mock.MagicMock()), \
                        mock.patch.object(
                            main.motor, "ejecutar",
                            mock.MagicMock(return_value=motor.Resultado(
                                True, "volumen subido"))) as ejecutar:
                    texto = main.atender(
                        "sube el volumen",
                        hablar=mock.MagicMock(),
                        escuchar=mock.MagicMock())

                cliente.assert_not_called()
                self.assertEqual(
                    texto, "",
                    "sin clave no hay texto final que devolver: %r" % (texto,))
                self.assertEqual(
                    ejecutar.call_args.args[0].nombre, "volumen_subir",
                    "sin clave la frase no llego al camino de siempre "
                    "interpretar -> motor: %r"
                    % (ejecutar.call_args.args[0],))

    def test_si_la_api_revierte_401_se_cae_al_camino_de_hoy(self):
        """create lanzando 401 -> se imprime el aviso y sigue interpretar

        La clave existia (por eso el bucle se intento) pero la API
        revienta: el bucle se descarta, el error SE IMPRIME (no se traga
        en silencio) y la frase sigue el camino de siempre hasta el motor.
        """
        main = self.main
        cliente = _cliente(RuntimeError("401 Unauthorized"))
        consola = io.StringIO()

        with mock.patch.object(main, "anthropic_api_key", "clave-falsa"), \
                mock.patch.object(main, "_obtener_cliente_ia",
                                  mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(
                    main.motor, "ejecutar",
                    mock.MagicMock(return_value=motor.Resultado(
                        True, "ram: 40 por ciento"))) as ejecutar, \
                mock.patch("sys.stdout", consola):
            texto = main.atender("cuanta ram",
                                 hablar=mock.MagicMock(),
                                 escuchar=mock.MagicMock())

        cliente.messages.create.assert_called_once()
        self.assertEqual(
            texto, "",
            "con la API caida no hay texto final que devolver: %r" % (texto,))
        self.assertIn(
            "aviso", consola.getvalue(),
            "el fallo de la API se trago en silencio: nadie se entera de "
            "por que se uso el camino viejo")
        self.assertEqual(
            ejecutar.call_args.args[0].nombre, "cpu_y_ram",
            "con la API caida la frase no llego al camino de siempre: %r"
            % (ejecutar.call_args.args[0],))
        self.assertEqual(len(motor.REGISTRO), 41,
                         "las 41 acciones siguen disponibles")

    def test_sin_clave_una_frase_libre_no_llama_a_la_ia_ni_revienta(self):
        """offline: una frase que no es orden no depende de red ni de clave

        interpretar la devuelve como consultar_ia, y sin clave esa accion
        se salta con aviso en vez de llamar a la API: el uso offline no
        depende de la IA en NINGUN punto.
        """
        main = self.main
        cliente = mock.MagicMock()
        consola = io.StringIO()

        with mock.patch.object(main, "anthropic_api_key", None), \
                mock.patch.object(main, "_obtener_cliente_ia", cliente), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            texto = main.atender("hola, que tal",
                                 hablar=mock.MagicMock(),
                                 escuchar=mock.MagicMock())

        cliente.assert_not_called()
        self.assertEqual(
            texto, "",
            "offline una frase libre devolvio texto: %r" % (texto,))
        self.assertIn(
            "aviso", consola.getvalue(),
            "sin clave no se aviso que la IA no esta disponible")


class TestPromptVerdadero(unittest.TestCase):
    """el system prompt ya no le pide al modelo que finja haber actuado"""

    def setUp(self):
        self.main = _main()

    def test_el_system_prompt_no_pide_fingir(self):
        """fuera las frases viejas; dentro la verdad de las herramientas

        Antes el prompt pedia textualmente confirmar como si ya hubiera
        hecho el trabajo aunque no lo hubiera hecho, cuando el motor todavia
        no existia. Hoy el motor SI existe (41 acciones) y el prompt tiene
        que decir la verdad: usar las herramientas, ser honesto en lo que
        esta fuera de su alcance y nunca afirmar algo que no ejecuto.
        """
        main = self.main
        cliente = _cliente([_respuesta([_texto("hola")], "end_turn")])

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main, "cargar_memoria",
                                  lambda: dict(MEMORIA_FALSA)):
            main.preguntar_a_ia("hola")

        system = cliente.messages.create.call_args.kwargs["system"]
        bajo = system.lower()
        for frase in ("confirma con naturalidad",
                      "no digas que no puedes",
                      "listo, ahí lo tienes"):
            self.assertNotIn(
                frase, bajo,
                "el system prompt todavia pide fingir: contiene %r" % frase)

        self.assertIn(
            "herramientas", bajo,
            "el prompt no le dice al modelo que TIENE herramientas reales")
        self.assertIn(
            "honestidad", bajo,
            "el prompt no le pide honestidad fuera de su alcance")
        self.assertIn(
            "ejecutaste", bajo,
            "el prompt no prohíbe afirmar cosas que no ejecuto")
        # la memoria del usuario sigue viajando (bug B5): no se perdio al
        # reescribir el prompt
        self.assertIn(
            "Yohan", system,
            "al reescribir el prompt se dejo fuera la memoria del usuario")
        self.assertIn(
            "aprende django", system,
            "al reescribir el prompt se dejo fuera una nota de la memoria")

    def test_el_modelo_es_el_actual(self):
        """MODELO_IA es claude-haiku-5-5 y preguntar_a_ia lo usa

        claude-haiku-4-5 tenia retiro garantizado el 15/10/2026 y esa fecha
        ya paso: queda la constante nombrada con el actual y mas barato.
        """
        main = self.main
        self.assertEqual(
            main.MODELO_IA, "claude-haiku-5-5",
            "MODELO_IA cambio: %r" % (main.MODELO_IA,))
        cliente = _cliente([_respuesta([_texto("ok")], "end_turn")])

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)):
            main.preguntar_a_ia("hola")

        modelo_llamado = cliente.messages.create.call_args.kwargs["model"]
        self.assertEqual(
            modelo_llamado, "claude-haiku-5-5",
            "preguntar_a_ia sigue usando el modelo viejo: %r"
            % (modelo_llamado,))


if __name__ == "__main__":
    unittest.main()
