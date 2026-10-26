# pruebas de la FASE H1 del proyecto Jarvis (2026-10-26): MEMORIA DE
# CONVERSACION entre turnos.
#
# El defecto medido: en main/main.py, bucle_agente empezaba SIEMPRE con
#     mensajes = [{"role": "user", "content": texto_usuario}]
# o sea UN solo mensaje por llamada. El modelo no recordaba ni lo que habia
# dicho dos frases antes. Ahora HISTORIAL_SESION vive a nivel de modulo y se
# copia al principio de cada turno.
#
# Frentes:
#   1. dos turnos seguidos: la SEGUNDA llamada recibe el turno 1 completo;
#   2. el TOPE: 100 turnos -> el mas viejo salio y el mas nuevo esta;
#   3. la ESTRUCTURA: se recorre TODO el historial y se exige que cada
#      tool_use vaya seguido de SU tool_result y que los roles alternen. Un
#      turno a medias haria que la API devolviera 400;
#   4. se LIMPIA al empezar una sesion nueva (procesar_orden), no al dormir;
#   5. SIN CLAVE el historial no se usa, no cuesta y interpretar() sigue;
#   6. el flujo de procesar_orden (esperar 6 s -> seguir) no se rompio, y el
#      historial sobrevive entre las dos frases de la MISMA sesion.
#
# REGLAS DE ESTE ARCHIVO: messages.create va SIEMPRE mockeado (cero llamadas
# reales), motor.ejecutar tambien y la voz/microfono tambien. Ninguna prueba
# toca el memoria.json real, ni abre el microfono, ni genera voz.

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


def _tipo_de(bloque):
    """tipo de un bloque de contenido (dict del tool_result u objeto del SDK)"""
    if isinstance(bloque, dict):
        return bloque.get("type")
    return getattr(bloque, "type", None)


def _texto_de(bloque):
    """texto de un bloque de contenido (dict u objeto del SDK)"""
    if isinstance(bloque, dict):
        return bloque.get("text")
    return getattr(bloque, "text", None)


def _cliente_continuo(texto="recibido"):
    """cliente falso que responde TEXTO en cada llamada, sin lista finita

    Para la prueba del tope hacen falta 100 llamadas: con `_cliente` (una
    lista) se acabaria en StopIteration. Aqui cada create devuelve una
    respuesta nueva, todo falso, cero red.
    """
    cliente = mock.MagicMock()
    cliente.messages.create.side_effect = (
        lambda **argumentos: _respuesta([_texto(texto)], "end_turn"))
    return cliente


def _sembrar_historial(main, frase="charla vieja", respuesta="respuesta vieja"):
    """deja un turno valido en el historial, para medir que se limpia"""
    main.HISTORIAL_SESION.extend([
        {"role": "user", "content": frase},
        {"role": "assistant",
         "content": [{"type": "text", "text": respuesta}]},
    ])


class TestMemoriaEntreTurnos(unittest.TestCase):
    """el turno 2 ve el turno 1: eso es lo que faltaba"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def test_el_segundo_turno_recibe_el_primero_en_la_llamada(self):
        """la SEGUNDA llamada a la API lleva la frase y la respuesta del turno 1

        La mutacion que mata: volver a `mensajes = [{"role": "user", ...}]`
        dentro de bucle_agente (sin historial). Ahi la segunda llamada
        llevaria UN solo mensaje y esta prueba revienta.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_texto("Claro, señor.")], "end_turn"),
            _respuesta([_texto("La luz queda apagada.")], "end_turn"),
        ])
        with mock.patch.object(main, "clave_ia", "clave-falsa"), \
                mock.patch.object(main, "_obtener_cliente_ia",
                                  mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main, "hablar", mock.MagicMock()):
            primera = main.atender("enciende la luz",
                                   memoria=dict(MEMORIA_FALSA),
                                   hablar=mock.MagicMock(),
                                   escuchar=mock.MagicMock())
            segunda = main.atender("y ahora apaga la luz",
                                   memoria=dict(MEMORIA_FALSA),
                                   hablar=mock.MagicMock(),
                                   escuchar=mock.MagicMock())

        self.assertEqual(primera, "Claro, señor.")
        self.assertEqual(segunda, "La luz queda apagada.")
        self.assertEqual(
            cliente.messages.create.call_count, 2,
            "atender no hizo las dos llamadas a la IA")

        mensajes = cliente.messages.create.call_args_list[1].kwargs["messages"]
        self.assertEqual(
            len(mensajes), 3,
            "la segunda llamada llevo %d mensajes: la frase de ahora y el "
            "historial del turno 1 son 3" % len(mensajes))
        self.assertEqual(
            mensajes[0], {"role": "user", "content": "enciende la luz"},
            "la segunda llamada NO llevo la frase del turno 1: %r"
            % (mensajes[0],))
        self.assertEqual(
            mensajes[1]["role"], "assistant",
            "el turno 1 no llevo su respuesta del modelo: %r"
            % (mensajes[1],))
        self.assertIn(
            "Claro, señor.",
            [_texto_de(bloque) for bloque in mensajes[1]["content"]],
            "la respuesta del turno 1 no viajo en el historial")
        self.assertEqual(
            mensajes[-1], {"role": "user", "content": "y ahora apaga la luz"},
            "la frase de AHORA no cerro el historial que se mando")

        self.assertEqual(
            [m["role"] for m in main.HISTORIAL_SESION],
            ["user", "assistant", "user", "assistant"],
            "el historial de la sesion no quedo con los dos turnos completos")


class TestTopeDelHistorial(unittest.TestCase):
    """sin tope, cada charla se vuelve mas cara y mas lenta"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def test_cien_turnos_botan_el_mas_viejo_y_conservan_el_mas_nuevo(self):
        """100 turnos -> el historial queda dentro del tope, sin el primero

        La mutacion que mata: quitar _recortar_historial() de _agregar_turno
        (o el while del tope). Ahi el historial guardaria 200 mensajes y
        "turno 1" seguiria dentro: las dos aserciones revientan.
        """
        main = self.main
        cliente = _cliente_continuo()
        with mock.patch.object(main, "clave_ia", "clave-falsa"), \
                mock.patch.object(main, "_obtener_cliente_ia",
                                  mock.MagicMock(return_value=cliente)):
            for numero in range(1, 101):
                main.atender("turno %d" % numero,
                             memoria=dict(MEMORIA_FALSA),
                             hablar=mock.MagicMock(),
                             escuchar=mock.MagicMock())

        self.assertEqual(
            cliente.messages.create.call_count, 100,
            "no se hicieron los 100 turnos que pide la prueba")
        self.assertEqual(
            len(main.HISTORIAL_SESION), main.TOPE_MENSAJES_HISTORIAL,
            "el historial tiene %d mensajes y el tope es %d: el tope no "
            "esta actuando" % (len(main.HISTORIAL_SESION),
                               main.TOPE_MENSAJES_HISTORIAL))
        frases = [m["content"] for m in main.HISTORIAL_SESION
                  if m["role"] == "user" and isinstance(m["content"], str)]
        self.assertNotIn(
            "turno 1", frases,
            "el turno MAS VIEJO sigue en el historial: no se bota nada")
        self.assertIn(
            "turno 100", frases,
            "el turno MAS NUEVO se perdio al recortar: %r" % (frases[-3:],))


class TestEstructuraValidaParaLaApi(unittest.TestCase):
    """tool_use seguido de SU tool_result y roles alternando, TODO el historial"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def test_todo_el_historial_se_puede_reenviar_tal_cual(self):
        """recorre el historial completo y verifica la estructura de la API

        Dos turnos CON herramienta: user -> assistant(tool_use) ->
        user(tool_result) -> assistant(texto), dos veces. Se revisan los 8
        mensajes uno por uno. La mutacion que mata: no guardar el mensaje
        user con los tool_result (o guardar un turno a medias). Ahi un
        tool_use queda sin su respuesta, los roles dejan de alternar y la
        prueba revienta.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("hora_actual", {}, "t_hora_1")],
                       "tool_use"),
            _respuesta([_texto("Son las 14:30.")], "end_turn"),
            _respuesta([_herramienta("hora_actual", {}, "t_hora_2")],
                       "tool_use"),
            _respuesta([_texto("Siguen siendo las 14:30.")], "end_turn"),
        ])
        with mock.patch.object(main, "clave_ia", "clave-falsa"), \
                mock.patch.object(main, "_obtener_cliente_ia",
                                  mock.MagicMock(return_value=cliente)), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(return_value=motor.Resultado(
                                      True, "14:30"))):
            main.atender("que hora es", memoria=dict(MEMORIA_FALSA),
                         hablar=mock.MagicMock(), escuchar=mock.MagicMock())
            main.atender("y ahora", memoria=dict(MEMORIA_FALSA),
                         hablar=mock.MagicMock(), escuchar=mock.MagicMock())

        historial = list(main.HISTORIAL_SESION)
        self.assertEqual(
            len(historial), 8,
            "el historial no tiene los dos turnos completos (8 mensajes): "
            "%d" % len(historial))
        self.assertEqual(
            historial[0]["role"], "user",
            "el historial no abre con la frase de Edward")
        self.assertEqual(
            historial[-1]["role"], "assistant",
            "el historial no cierra con la respuesta del modelo")

        tool_use_vistos = 0
        tool_result_vistos = 0
        for posicion, mensaje in enumerate(historial):
            rol_esperado = "user" if posicion % 2 == 0 else "assistant"
            self.assertEqual(
                mensaje["role"], rol_esperado,
                "en la posicion %d el rol es %r y tenia que ser %r: los "
                "roles no alternan" % (posicion, mensaje["role"],
                                       rol_esperado))
            contenido = mensaje["content"]
            if isinstance(contenido, str):
                # un texto plano es SIEMPRE la frase de Edward que ABRE un
                # turno: los tool_result van en lista
                self.assertEqual(
                    mensaje["role"], "user",
                    "un texto plano viajo en un mensaje assistant")
                self.assertTrue(
                    contenido,
                    "un turno abre con una frase VACIA de Edward")
                continue
            self.assertIsInstance(
                contenido, list,
                "el mensaje %d no es una lista de bloques: %r"
                % (posicion, contenido))
            tipos = [_tipo_de(bloque) for bloque in contenido]
            if mensaje["role"] == "assistant":
                self.assertNotIn(
                    "tool_result", tipos,
                    "un tool_result viajo en un mensaje assistant")
                ids_use = [getattr(bloque, "id", None)
                           for bloque in contenido
                           if _tipo_de(bloque) == "tool_use"]
                tool_use_vistos += len(ids_use)
                if ids_use:
                    self.assertLess(
                        posicion + 1, len(historial),
                        "el tool_use %r quedo SIN su tool_result: es el "
                        "ultimo mensaje del historial" % (ids_use,))
                    siguiente = historial[posicion + 1]
                    self.assertEqual(
                        siguiente["role"], "user",
                        "despues de un tool_use no viene un mensaje user "
                        "con su tool_result")
                    ids_result = [bloque["tool_use_id"]
                                  for bloque in siguiente["content"]
                                  if _tipo_de(bloque) == "tool_result"]
                    self.assertEqual(
                        sorted(ids_use), sorted(ids_result),
                        "el tool_use %r no tiene SU tool_result: llegaron "
                        "%r" % (sorted(ids_use), sorted(ids_result)))
            else:
                self.assertTrue(
                    tipos and all(tipo == "tool_result" for tipo in tipos),
                    "un mensaje user con lista solo puede traer "
                    "tool_result: %r" % (tipos,))
                tool_result_vistos += len(tipos)
                anterior = historial[posicion - 1]["content"]
                ids_use = [getattr(bloque, "id", None)
                           for bloque in anterior
                           if _tipo_de(bloque) == "tool_use"]
                ids_result = [bloque["tool_use_id"]
                              for bloque in contenido
                              if _tipo_de(bloque) == "tool_result"]
                self.assertEqual(
                    sorted(ids_use), sorted(ids_result),
                    "un tool_result sin su tool_use del mensaje anterior: "
                    "%r contra %r" % (sorted(ids_result), sorted(ids_use)))

        self.assertEqual(
            tool_use_vistos, 2,
            "el historial no guardo los 2 tool_use de los dos turnos")
        self.assertEqual(
            tool_result_vistos, 2,
            "el historial no guardo los 2 tool_result de los dos turnos")


class TestSeLimpiaAlEmpezarSesion(unittest.TestCase):
    """una sesion no hereda la charla de la anterior"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def test_procesar_orden_limpia_el_historial_al_empezar(self):
        """un historial viejo NO sobrevive a la palabra de activacion

        procesar_orden corre justo despues de la activacion: es el punto
        elegido para limpiar. Se siembra un turno, se corre UNA vuelta real
        de procesar_orden (con todo doblado) y el historial tiene que quedar
        vacio. La mutacion que mata: quitar limpiar_historial() de
        procesar_orden; entonces el turno sembrado seguiria ahi.
        """
        main = self.main
        _sembrar_historial(main)
        self.assertEqual(
            len(main.HISTORIAL_SESION), 2,
            "la prueba no sembro el historial viejo")

        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = "hola"
        reloj = mock.MagicMock()
        # 0.0 al fijar el inicio y 100.0 en la condicion: sin voz en la
        # espera Jarvis se duerme YA y la vuelta termina sola
        reloj.time.side_effect = [0.0, 100.0]
        with mock.patch.object(main, "time", reloj), \
                mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "atender",
                                  mock.MagicMock(return_value="")), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch("sys.stdout", io.StringIO()):
            main.procesar_orden()

        self.assertEqual(
            main.HISTORIAL_SESION, [],
            "el historial viejo sobrevivio al empezar la sesion: %r"
            % (main.HISTORIAL_SESION,))


class TestSinClaveNoUsaNiCuesta(unittest.TestCase):
    """sin GROQ_API_KEY nada cambia: interpretar() y cero tokens"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def test_sin_clave_el_historial_no_se_manda_ni_se_toca(self):
        """clave None -> cliente NI construido, historial intacto, motor si

        El historial solo se lee dentro de bucle_agente, y a bucle_agente
        solo se llega con clave. Sin clave: cero llamadas a la IA (cero
        costo), el historial guardado no se toca y la frase sigue por
        interpretar() hasta el motor.
        """
        main = self.main
        _sembrar_historial(main)
        antes = list(main.HISTORIAL_SESION)
        cliente = mock.MagicMock()
        with mock.patch.object(main, "clave_ia", None), \
                mock.patch.object(main, "_obtener_cliente_ia", cliente), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(main.motor, "ejecutar",
                                  mock.MagicMock(return_value=motor.Resultado(
                                      True, "volumen subido"))) as ejecutar:
            texto = main.atender("sube el volumen",
                                 memoria=dict(MEMORIA_FALSA),
                                 hablar=mock.MagicMock(),
                                 escuchar=mock.MagicMock())

        self.assertEqual(
            texto, "",
            "sin clave no hay texto final que devolver: %r" % (texto,))
        cliente.assert_not_called()
        self.assertEqual(
            main.HISTORIAL_SESION, antes,
            "sin clave el historial cambio: se uso o se limpio sin motivo")
        self.assertEqual(
            ejecutar.call_args.args[0].nombre, "volumen_subir",
            "sin clave la frase no llego a interpretar -> motor: %r"
            % (ejecutar.call_args.args[0],))


class TestFlujoDeProcesarOrden(unittest.TestCase):
    """esperar 6 s -> si hay voz, seguir en la MISMA sesion"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def test_hay_voz_tras_la_espera_y_la_sesion_sigue(self):
        """dos frases en la misma sesion: el flujo de 6 s sigue vivo

        Vuelta 1: la espera detecta voz y se vuelve a escuchar (NO duerme).
        Vuelta 2: la espera se agota y duerme. El historial tiene que tener
        los DOS turnos de la misma sesion (4 mensajes): la limpieza del
        principio no puede borrar la charla a mitad de sesion. Cero llamadas
        reales: cliente falso y microfono doblado.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_texto("Son las 14:30.")], "end_turn"),
            _respuesta([_texto("Volumen subido.")], "end_turn"),
        ])
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.side_effect = ["hora", "volumen"]
        reloj = mock.MagicMock()
        # vuelta 1: 0.0 de inicio y 1.0 en la condicion (1.0 < 6, entra a la
        # espera, que encuentra VOZ y repite); vuelta 2: 0.0 y 7.0 (7.0 >= 6,
        # sin voz, duerme)
        reloj.time.side_effect = [0.0, 1.0, 0.0, 7.0]
        consola = io.StringIO()
        with mock.patch.object(main, "time", reloj), \
                mock.patch.object(main, "clave_ia", "clave-falsa"), \
                mock.patch.object(main, "_obtener_cliente_ia",
                                  mock.MagicMock(return_value=cliente)), \
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
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch("sys.stdout", consola):
            main.procesar_orden()

        self.assertEqual(
            cliente.messages.create.call_count, 2,
            "la sesion no proceso las dos frases: %d llamadas a la IA"
            % cliente.messages.create.call_count)
        self.assertEqual(
            [m["role"] for m in main.HISTORIAL_SESION],
            ["user", "assistant", "user", "assistant"],
            "tras seguir por voz, el historial no tiene las DOS frases de "
            "la misma sesion: %r" % (main.HISTORIAL_SESION,))
        self.assertIn(
            "dormido", consola.getvalue(),
            "el flujo de dormir no ocurrio: la espera de 6 s esta rota")


if __name__ == "__main__":
    unittest.main()
