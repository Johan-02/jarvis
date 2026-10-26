# pruebas del CEREBRO de Groq (main/cerebro.py, 2026-10-26): traducir entre el
# formato de Anthropic (el que habla main.py) y el de OpenAI (el que habla
# Groq) sin red.
#
# FRENTES:
#   1. HERRAMIENTAS: input_schema sale como parameters SIN perder un campo
#      (se comprueba con borrar_archivo, que lleva `ruta`), y el catalogo
#      REAL de main/herramientas.py pasa entero;
#   2. MENSAJES: texto, assistant con tool_use (argumentos STRING JSON que al
#      json.loads devuelven el input original, con float, ruta de Windows y
#      tildes), y CADA tool_result como UN role:"tool";
#   3. LA IMAGEN: sale del role:"tool" (ahi no cabe) y viaja en un role:"user"
#      APARTE con image_url, DESPUES de todos los tool del turno;
#   4. RESPUESTA: texto, tool_calls con input ya dict, "length" -> "max_tokens"
#      y argumentos con JSON roto que NO lanzan;
#   5. CLIENTE: una llamada, a la URL buena, con User-Agent (la trampa del 403
#      de Cloudflare), sin `tools` cuando no hay herramientas, y HTTP 429 ->
#      ErrorCerebro con el codigo y SIN la clave;
#   6. LIMITE DE RITMO: un 429 (o 503) ESPERA lo que diga el error y reintenta
#      (ESPERA_MAXIMA y REINTENTOS de cerebro.py); 401, 403 y 400 salen a la
#      primera porque son errores de verdad. `time.sleep` SIEMPRE mockeado:
#      ninguna prueba de este archivo duerme de verdad.
#   7. EL MODELO: el `model` que se le pasa a create VIAJA en el cuerpo (antes
#      se ignoraba) y main.MODELO_IA, que es lo que se lee en el codigo, y
#      cerebro.MODELO_GROQ, que es lo que de verdad se usa, son EL MISMO
#      nombre: si alguien separa las dos constantes, esta suite falla.
#
# REGLAS DE ESTE ARCHIVO: CERO red (urllib.request.urlopen SIEMPRE mockeado),
# CERO clave real (una inventada), CERO llamadas a Groq y CERO escrituras en
# disco. La clave falsa se escribe una sola vez, en CLAVE_FALSA, y varias
# pruebas comprueban que NO aparece en ningun mensaje de error.

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from main import cerebro

# clave FALSA: no sirve para nada y sirve para todo lo que se prueba aqui
CLAVE_FALSA = "gsk_clave_falsa_de_prueba_0123456789"

# datos de imagen base64 de mentira: solo importa que viajen intactos
DATOS_PNG = "iVBORw0KGgoAAAANSUhEUg=="

# definicion de herramienta como la genera main/herramientas.py (con ruta,
# que es el parametro obligatorio de borrar_archivo)
DEFINICION_BORRAR = {
    "name": "borrar_archivo",
    "description": "borra UN archivo. DESTRUCTIVA: es IRREVERSIBLE.",
    "input_schema": {
        "type": "object",
        "properties": {"ruta": {"type": "string"}},
        "required": ["ruta"],
    },
}


def _respuesta_http(datos, codigo=200):
    """respuesta falsa de urlopen: se puede usar con `with` y tiene read()"""
    crudo = json.dumps(datos).encode("utf-8")
    objeto = mock.MagicMock()
    objeto.status = codigo
    objeto.read.return_value = crudo
    objeto.__enter__ = mock.Mock(return_value=objeto)
    objeto.__exit__ = mock.Mock(return_value=False)
    return objeto


def _error_http(codigo, mensaje, cabeceras=None, caso=None):
    """HTTPError con cuerpo JSON, como el que manda Groq

    OJO: el cuerpo de un HTTPError viaja en un flujo que se CONSUME al leerlo,
    asi que cuando la prueba necesita VARIOS intentos hay que fabricar un
    error NUEVO para cada uno (de ahi que se llame varias veces y se pase una
    LISTA en side_effect). Un `side_effect` que sea una funcion NO lanza: su
    resultado se devuelve como respuesta, y la prueba mediria otra cosa.

    `caso` es el TestCase: si se pasa, el cuerpo se cierra al terminar la
    prueba y el corredor no escupe un ResourceWarning.
    """
    cuerpo = json.dumps({"error": {"message": mensaje}}).encode("utf-8")
    error = urllib.error.HTTPError(cerebro.URL_GROQ, codigo, "error",
                                   cabeceras or {}, io.BytesIO(cuerpo))
    if caso is not None:
        caso.addCleanup(error.close)
    return error


def _datos_de_texto(texto, finish_reason="stop"):
    """cuerpo de respuesta de Groq cuando el modelo contesta TEXTO"""
    return {
        "choices": [{
            "message": {"role": "assistant", "content": texto},
            "finish_reason": finish_reason,
        }],
    }


def _datos_de_herramientas(llamadas, finish_reason="tool_calls"):
    """cuerpo de respuesta de Groq cuando el modelo pide HERRAMIENTAS"""
    return {
        "choices": [{
            "message": {"role": "assistant", "content": None,
                        "tool_calls": llamadas},
            "finish_reason": finish_reason,
        }],
    }


def _llamada(id_llamada, nombre, argumentos):
    """un tool_call de OpenAI tal como llega: arguments es STRING"""
    return {
        "id": id_llamada,
        "type": "function",
        "function": {"name": nombre, "arguments": argumentos},
    }


def _cabeceras(peticion):
    """cabeceras de un Request con las claves en minusculas"""
    return {clave.lower(): valor for clave, valor in peticion.headers.items()}


class TestHerramientas(unittest.TestCase):
    """_a_openai_herramientas: input_schema -> function.parameters"""

    def test_esquema_completo_sin_perder_campos(self):
        """el esquema entero pasa a parameters, con required y con ruta

        No basta con que exista `parameters`: si el traductor se inventara un
        esquema nuevo, `ruta` desapareceria y el modelo no podria rellenar la
        herramienta.
        """
        traducidas = cerebro._a_openai_herramientas([DEFINICION_BORRAR])
        self.assertEqual(len(traducidas), 1)
        funcion = traducidas[0]["function"]
        self.assertEqual(traducidas[0]["type"], "function")
        self.assertEqual(funcion["name"], "borrar_archivo")
        self.assertEqual(funcion["description"],
                         DEFINICION_BORRAR["description"])
        self.assertEqual(funcion["parameters"],
                         DEFINICION_BORRAR["input_schema"])
        self.assertEqual(funcion["parameters"]["properties"]["ruta"],
                         {"type": "string"})
        self.assertEqual(funcion["parameters"]["required"], ["ruta"])

    def test_catalogo_real_completo(self):
        """las 41 acciones del REGISTRO pasan todas, con parameters"""
        from main import herramientas

        definiciones = herramientas.herramientas()
        traducidas = cerebro._a_openai_herramientas(definiciones)
        self.assertEqual(len(traducidas), len(definiciones))
        for herramienta in traducidas:
            funcion = herramienta["function"]
            self.assertTrue(funcion["name"], "herramienta sin nombre")
            self.assertEqual(funcion["parameters"]["type"], "object",
                             "parameters sin type object en %r"
                             % (funcion["name"],))
        por_nombre = {h["function"]["name"]: h["function"]
                      for h in traducidas}
        self.assertIn("ruta",
                      por_nombre["borrar_archivo"]["parameters"]["properties"])

    def test_sin_esquema_no_revienta(self):
        """una definicion sin input_schema sale con un objeto vacio"""
        traducidas = cerebro._a_openai_herramientas(
            [{"name": "rara", "description": "sin esquema"}])
        self.assertEqual(traducidas[0]["function"]["parameters"],
                         {"type": "object", "properties": {}})


class TestMensajes(unittest.TestCase):
    """_a_openai_mensajes: mensajes de Anthropic -> mensajes de OpenAI"""

    def test_texto_simple(self):
        salida = cerebro._a_openai_mensajes(
            [{"role": "user", "content": "que hora es"}])
        self.assertEqual(salida, [{"role": "user", "content": "que hora es"}])

    def test_sistema_va_primero(self):
        salida = cerebro._a_openai_mensajes(
            [{"role": "user", "content": "hola"}], sistema="sos Jarvis")
        self.assertEqual(salida[0], {"role": "system", "content": "sos Jarvis"})
        self.assertEqual(salida[1], {"role": "user", "content": "hola"})

    def test_sin_sistema_no_hay_mensaje_system(self):
        salida = cerebro._a_openai_mensajes(
            [{"role": "user", "content": "hola"}], sistema=None)
        self.assertEqual([m["role"] for m in salida], ["user"])

    def test_assistant_con_tool_use_sale_con_arguments_string(self):
        """los argumentos son un STRING JSON que devuelve el input original

        Se usan un float (1.5), una ruta de Windows ("C:\\\\ruta") y una
        tilde para que se vea que json.dumps no los rompe. Si se mandara el
        dict tal cual, la API contestaria 400.
        """
        entrada = {"ruta": "C:\\ruta", "volumen": 1.5, "nota": "canción"}
        salida = cerebro._a_openai_mensajes([{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "voy a borrarlo"},
                {"type": "tool_use", "id": "toolu_1",
                 "name": "borrar_archivo", "input": entrada},
            ],
        }])
        mensaje = salida[0]
        self.assertEqual(mensaje["role"], "assistant")
        self.assertEqual(mensaje["content"], "voy a borrarlo")
        llamadas = mensaje["tool_calls"]
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(llamadas[0]["id"], "toolu_1")
        self.assertEqual(llamadas[0]["type"], "function")
        self.assertEqual(llamadas[0]["function"]["name"], "borrar_archivo")
        argumentos = llamadas[0]["function"]["arguments"]
        self.assertIsInstance(argumentos, str,
                              "arguments tiene que ser STRING JSON, no dict")
        self.assertEqual(json.loads(argumentos), entrada)

    def test_assistant_con_objetos_bloque(self):
        """el content del assistant puede venir como Bloque, no solo dict

        main.py guarda `respuesta.content` tal cual, o sea objetos Bloque de
        ESTE modulo: un traductor que solo lea dicts revienta en el primer
        turno con herramientas.
        """
        bloque = cerebro.Bloque("tool_use", id="toolu_2", name="decir_hora",
                                input={})
        salida = cerebro._a_openai_mensajes(
            [{"role": "assistant", "content": [bloque]}])
        llamada = salida[0]["tool_calls"][0]
        self.assertEqual(llamada["function"]["name"], "decir_hora")
        self.assertEqual(json.loads(llamada["function"]["arguments"]), {})
        self.assertIsNone(salida[0]["content"])

    def test_tool_result_es_un_mensaje_tool(self):
        salida = cerebro._a_openai_mensajes([{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                         "content": "borrado", "is_error": False}],
        }])
        self.assertEqual(salida, [{"role": "tool", "tool_call_id": "toolu_1",
                                   "content": "borrado"}])

    def test_dos_tool_result_dan_dos_mensajes_tool(self):
        """dos resultados del mismo turno -> DOS mensajes role:"tool" seguidos"""
        salida = cerebro._a_openai_mensajes([{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": "primero"},
                {"type": "tool_result", "tool_use_id": "toolu_2",
                 "content": "segundo"},
            ],
        }])
        self.assertEqual(len(salida), 2)
        self.assertEqual([m["role"] for m in salida], ["tool", "tool"])
        self.assertEqual([m["tool_call_id"] for m in salida],
                         ["toolu_1", "toolu_2"])
        self.assertEqual([m["content"] for m in salida],
                         ["primero", "segundo"])

    def test_la_imagen_sale_del_tool_y_viaja_en_user(self):
        """la captura NO va en el role:"tool": va en un user APARTE, despues

        El formato OpenAI no acepta imagen dentro de un mensaje tool (su
        content es texto). Si se colara ahi, Groq contestaria 400 y el modelo
        nunca veria la pantalla.
        """
        salida = cerebro._a_openai_mensajes([{
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_9",
                "content": [
                    {"type": "text", "text": "Aqui tienes una captura."},
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png",
                                "data": DATOS_PNG}},
                ],
                "is_error": False,
            }],
        }])
        self.assertEqual(len(salida), 2, salida)
        primero, segundo = salida
        self.assertEqual(primero["role"], "tool")
        self.assertEqual(primero["tool_call_id"], "toolu_9")
        self.assertEqual(primero["content"], "Aqui tienes una captura.")
        self.assertNotIn("image_url", json.dumps(primero),
                         "la imagen se colo dentro del role tool")
        self.assertNotIn(DATOS_PNG, json.dumps(primero),
                         "los datos de la imagen se colaron en el role tool")
        self.assertEqual(segundo["role"], "user")
        partes = segundo["content"]
        self.assertEqual(partes[0], {"type": "text",
                                     "text": cerebro.TEXTO_DE_LA_CAPTURA})
        self.assertEqual(partes[1]["type"], "image_url")
        self.assertEqual(partes[1]["image_url"]["url"],
                         "data:image/png;base64," + DATOS_PNG)

    def test_la_imagen_va_despues_de_todos_los_tool(self):
        """orden: assistant, tool, tool, y RECIEN ahi la imagen"""
        salida = cerebro._a_openai_mensajes([
            {"role": "user", "content": "mira mi pantalla"},
            {"role": "assistant",
             "content": [{"type": "tool_use", "id": "toolu_1",
                          "name": "mirar_pantalla", "input": {}},
                         {"type": "tool_use", "id": "toolu_2",
                          "name": "decir_hora", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": [
                     {"type": "text", "text": "captura"},
                     {"type": "image",
                      "source": {"type": "base64",
                                 "media_type": "image/png",
                                 "data": DATOS_PNG}}]},
                {"type": "tool_result", "tool_use_id": "toolu_2",
                 "content": "son las 10"},
            ]},
        ])
        self.assertEqual([m["role"] for m in salida],
                         ["user", "assistant", "tool", "tool", "user"])
        self.assertEqual(salida[4]["content"][1]["type"], "image_url")


class TestRespuesta(unittest.TestCase):
    """_de_openai_respuesta: cuerpo de Groq -> Respuesta"""

    def test_texto_solo(self):
        respuesta = cerebro._de_openai_respuesta(_datos_de_texto("hola, señor"))
        self.assertEqual(len(respuesta.content), 1)
        self.assertEqual(respuesta.content[0].type, "text")
        self.assertEqual(respuesta.content[0].text, "hola, señor")
        self.assertEqual(respuesta.stop_reason, "end_turn")

    def test_tool_calls_con_input_dict(self):
        respuesta = cerebro._de_openai_respuesta(_datos_de_herramientas(
            [_llamada("call_7", "borrar_archivo", '{"ruta": "C:\\\\tmp\\\\a"}')]))
        self.assertEqual(respuesta.stop_reason, "tool_use")
        bloque = respuesta.content[0]
        self.assertEqual(bloque.type, "tool_use")
        self.assertEqual(bloque.id, "call_7")
        self.assertEqual(bloque.name, "borrar_archivo")
        self.assertIsInstance(bloque.input, dict)
        self.assertEqual(bloque.input, {"ruta": "C:\\tmp\\a"})

    def test_finish_reason_length_es_max_tokens(self):
        respuesta = cerebro._de_openai_respuesta(
            _datos_de_texto("se corto aqui", finish_reason="length"))
        self.assertEqual(respuesta.stop_reason, "max_tokens")

    def test_finish_reason_desconocido_es_end_turn(self):
        """un finish_reason que Groq invente NO se propaga raro"""
        respuesta = cerebro._de_openai_respuesta(
            _datos_de_texto("ok", finish_reason="eos"))
        self.assertEqual(respuesta.stop_reason, "end_turn")

    def test_argumentos_con_json_roto_no_lanza(self):
        """un JSON roto devuelve input {} y una nota, nunca una excepcion

        El motor rechaza el input vacio con un tool_result claro ("falta el
        parametro obligatorio") y el modelo se corrige; tumbar el turno por
        una coma de mas seria peor.
        """
        respuesta = cerebro._de_openai_respuesta(_datos_de_herramientas(
            [_llamada("call_8", "borrar_archivo", "{ruta: sin comillas")]))
        bloque = respuesta.content[0]
        self.assertEqual(bloque.input, {})
        self.assertEqual(bloque.name, "borrar_archivo")
        self.assertTrue(bloque.text, "no dejo constancia de los argumentos "
                                    "rotos")

    def test_sin_choices_es_error_del_cerebro(self):
        with self.assertRaises(cerebro.ErrorCerebro):
            cerebro._de_openai_respuesta({"error": {"message": "algo"}})

    def test_respuesta_vacia_no_revienta_content(self):
        """sin texto y sin herramientas queda un text vacio, no una lista nula

        main.preguntar_a_ia lee `respuesta.content[0].text`: con la lista
        vacia, un IndexError se convertiria en "la IA no esta disponible".
        """
        respuesta = cerebro._de_openai_respuesta(
            {"choices": [{"message": {"content": None},
                          "finish_reason": None}]})
        self.assertEqual(respuesta.content[0].type, "text")
        self.assertEqual(respuesta.content[0].text, "")


class TestCliente(unittest.TestCase):
    """ClienteGroq: la llamada HTTP, con urlopen SIEMPRE mockeado"""

    def setUp(self):
        self.cliente = cerebro.ClienteGroq(CLAVE_FALSA)

    def test_no_hace_red_al_construirse(self):
        with mock.patch("main.cerebro.urllib_request.urlopen") as abierto:
            cerebro.ClienteGroq(CLAVE_FALSA)
        self.assertEqual(abierto.call_count, 0)

    def test_create_llama_una_vez_a_la_url_con_user_agent(self):
        """una llamada, a la URL buena, con User-Agent (la trampa del 403)

        Cloudflare responde 403 "error code: 1010" si falta el User-Agent:
        esta prueba es la que impide que alguien lo quite por parecer
        decorativo.
        """
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        return_value=_respuesta_http(
                            _datos_de_texto("son las 10"))) as abierto:
            respuesta = self.cliente.messages.create(
                # el modelo bueno: create RESPETA el `model` que se le pasa
                # (antes mandaba "claude-modelo-viejo" para probar que se
                # ignoraba, y ese contrato ya no existe)
                model=cerebro.MODELO_GROQ,
                max_tokens=1024,
                system="sos Jarvis",
                messages=[{"role": "user", "content": "que hora es"}],
            )
        self.assertEqual(abierto.call_count, 1)
        peticion = abierto.call_args.args[0]
        self.assertEqual(peticion.full_url, cerebro.URL_GROQ)
        self.assertEqual(peticion.get_method(), "POST")
        cabeceras = _cabeceras(peticion)
        self.assertIn("user-agent", cabeceras,
                      "sin User-Agent, Cloudflare contesta 403")
        self.assertEqual(cabeceras["accept"], "application/json")
        self.assertEqual(cabeceras["content-type"], "application/json")
        self.assertEqual(cabeceras["authorization"], "Bearer " + CLAVE_FALSA)
        self.assertEqual(abierto.call_args.kwargs["timeout"],
                         cerebro.TIEMPO_ESPERA)
        cuerpo = json.loads(peticion.data.decode("utf-8"))
        self.assertEqual(cuerpo["model"], "qwen/qwen3.8-27b")
        self.assertEqual(cuerpo["max_tokens"], 1024)
        self.assertEqual(cuerpo["messages"][0]["role"], "system")
        self.assertEqual(respuesta.content[0].text, "son las 10")

    def test_segundo_turno_con_los_bloques_del_primero(self):
        """el content del assistant se vuelve a traducir en el turno siguiente

        Es exactamente lo que hace bucle_agente: guarda `respuesta.content`
        (objetos Bloque de ESTE modulo) y lo vuelve a mandar. Si el traductor
        solo entendiera dicts, el primer turno con herramientas pasaria y el
        SEGUNDO reventaria. Ademas el cuerpo se json.dumps entero: si un
        Bloque se colara sin traducir, salta un TypeError aqui.
        """
        primera = _datos_de_herramientas(
            [_llamada("call_1", "decir_hora", "{}")])
        segunda = _datos_de_texto("son las 10")
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=[_respuesta_http(primera),
                                     _respuesta_http(segunda)]) as abierto:
            mensajes = [{"role": "user", "content": "que hora es"}]
            respuesta = self.cliente.messages.create(
                max_tokens=1024, system="sos Jarvis",
                tools=[DEFINICION_BORRAR], messages=mensajes)
            mensajes.append({"role": "assistant",
                             "content": respuesta.content})
            mensajes.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": "son las 10"}]})
            final = self.cliente.messages.create(
                max_tokens=1024, system="sos Jarvis",
                tools=[DEFINICION_BORRAR], messages=mensajes)
        self.assertEqual(abierto.call_count, 2)
        cuerpo = json.loads(abierto.call_args.args[0].data.decode("utf-8"))
        self.assertEqual([m["role"] for m in cuerpo["messages"]],
                         ["system", "user", "assistant", "tool"])
        self.assertEqual(cuerpo["messages"][2]["tool_calls"][0]["id"],
                         "call_1")
        self.assertEqual(cuerpo["messages"][3]["tool_call_id"], "call_1")
        self.assertEqual(final.content[0].text, "son las 10")

    def test_sin_tools_el_cuerpo_no_lleva_tools(self):
        """main.preguntar_a_ia no manda tools: la clave no puede aparecer"""
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        return_value=_respuesta_http(
                            _datos_de_texto("ok"))) as abierto:
            self.cliente.messages.create(
                max_tokens=256, system="s", messages=[
                    {"role": "user", "content": "hola"}])
        cuerpo = json.loads(abierto.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("tools", cuerpo)

    def test_tools_vacio_tampoco_lleva_tools(self):
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        return_value=_respuesta_http(
                            _datos_de_texto("ok"))) as abierto:
            self.cliente.messages.create(
                tools=[], messages=[{"role": "user", "content": "hola"}])
        cuerpo = json.loads(abierto.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("tools", cuerpo)

    def test_con_tools_van_traducidas(self):
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        return_value=_respuesta_http(_datos_de_herramientas(
                            [_llamada("call_1", "borrar_archivo",
                                      '{"ruta": "x"}')]))) as abierto:
            respuesta = self.cliente.messages.create(
                max_tokens=512, system="s",
                tools=[DEFINICION_BORRAR],
                messages=[{"role": "user", "content": "borra x"}])
        cuerpo = json.loads(abierto.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(len(cuerpo["tools"]), 1)
        self.assertEqual(cuerpo["tools"][0]["function"]["parameters"],
                         DEFINICION_BORRAR["input_schema"])
        self.assertEqual(respuesta.stop_reason, "tool_use")

    def test_http_429_da_error_cerebro_con_codigo_y_sin_clave(self):
        """429 que se AGOTA -> ErrorCerebro legible, con el codigo, SIN la clave

        Con el reintento del limite de ritmo (ver TestReintentoDelLimite) este
        prueba el caso en que los dos reintentos tambien dan 429. `time.sleep`
        va mockeado (no se duerme de verdad en una prueba) y el error se
        fabrica nuevo en cada intento, porque su cuerpo se consume al leerlo.
        """
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=[_error_http(429, "rate limit reached",
                                                 caso=self)
                                     for _ in range(1 + cerebro.REINTENTOS)]
                        ) as abierto, \
                mock.patch("main.cerebro.time.sleep") as dormido:
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self.cliente.messages.create(
                    messages=[{"role": "user", "content": "hola"}])
        texto = str(capturado.exception)
        self.assertIn("429", texto)
        self.assertIn("rate limit", texto)
        self.assertEqual(capturado.exception.codigo, 429)
        self.assertNotIn(CLAVE_FALSA, texto, "la clave se filtro al error")
        self.assertNotIn("Bearer", texto, "la cabecera se filtro al error")
        self.assertEqual(abierto.call_count, 1 + cerebro.REINTENTOS,
                         "no se agotaron los reintentos del 429")
        self.assertEqual(dormido.call_count, cerebro.REINTENTOS,
                         "no se espero entre reintento y reintento")

    def test_error_sin_cuerpo_tampoco_filtra_la_clave(self):
        """un HTTPError con el cuerpo vacio sigue dando un mensaje legible"""
        error = urllib.error.HTTPError(
            cerebro.URL_GROQ, 401, "Unauthorized", {}, io.BytesIO(b""))
        self.addCleanup(error.close)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=error):
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self.cliente.messages.create(
                    messages=[{"role": "user", "content": "hola"}])
        texto = str(capturado.exception)
        self.assertIn("401", texto)
        self.assertTrue(texto.strip())
        self.assertNotIn(CLAVE_FALSA, texto)

    def test_sin_conexion_da_error_cerebro(self):
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=urllib.error.URLError("sin ruta al host")):
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self.cliente.messages.create(
                    messages=[{"role": "user", "content": "hola"}])
        self.assertIn("sin conexion", str(capturado.exception))

    def test_respuesta_que_no_es_json_da_error_cerebro(self):
        crudo = mock.MagicMock()
        crudo.read.return_value = b"<html>error del proxy</html>"
        crudo.__enter__ = mock.Mock(return_value=crudo)
        crudo.__exit__ = mock.Mock(return_value=False)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        return_value=crudo):
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self.cliente.messages.create(
                    messages=[{"role": "user", "content": "hola"}])
        self.assertIn("ilegible", str(capturado.exception))


class TestReintentoDelLimiteDeRitmo(unittest.TestCase):
    """el 429 (y el 503) se ESPERAN; los errores de verdad salen a la primera

    Medido: la capa gratis de Groq da 7.000 tokens de entrada por minuto y una
    frase gasta ~2.700, asi que a la segunda frase seguida llega el 429. Antes
    de esto el turno se perdia y main.atender caia a palabras clave.
    """

    def setUp(self):
        self.cliente = cerebro.ClienteGroq(CLAVE_FALSA)

    def _pedir(self):
        return self.cliente.messages.create(
            messages=[{"role": "user", "content": "que hora es"}])

    def test_429_con_retry_after_espera_y_devuelve_la_respuesta(self):
        """Retry-After: 3 -> duerme 3 y el SEGUNDO intento contesta

        Es el caso util: el turno no se pierde, Edward recibe su respuesta.
        """
        error = _error_http(429, "rate limit reached", {"Retry-After": "3"},
                            caso=self)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=[error, _respuesta_http(
                            _datos_de_texto("son las 10"))]) as abierto, \
                mock.patch("main.cerebro.time.sleep") as dormido:
            respuesta = self._pedir()
        self.assertEqual(abierto.call_count, 2, "no se reintento el 429")
        dormido.assert_called_once_with(3.0)
        self.assertEqual(respuesta.content[0].text, "son las 10")
        self.assertEqual(respuesta.stop_reason, "end_turn")

    def test_429_sin_cabecera_saca_los_segundos_del_cuerpo_y_los_topa(self):
        """el cuerpo dice 21.68 s y se duerme 20 (ESPERA_MAXIMA), no 21.68

        Un turno de voz no puede quedarse colgado 21 s si el tope son 20. Se
        comprueba el NUMERO de la espera, no solo que se haya llamado.
        """
        error = _error_http(
            429, "Rate limit reached ... Please try again in 21.685714285s.",
            caso=self)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=[error, _respuesta_http(
                            _datos_de_texto("son las 10"))]), \
                mock.patch("main.cerebro.time.sleep") as dormido:
            respuesta = self._pedir()
        dormido.assert_called_once_with(20.0)
        self.assertEqual(dormido.call_args.args[0], cerebro.ESPERA_MAXIMA)
        self.assertEqual(respuesta.content[0].text, "son las 10")

    def test_429_repetido_agota_los_reintentos_y_lanza_con_el_codigo(self):
        """429 una y otra vez -> ErrorCerebro, con 429 en el mensaje

        Se fabrica un error NUEVO para cada intento (1 + REINTENTOS): el
        cuerpo de un HTTPError se consume al leerlo, y ademas es lo que pasa de
        verdad (cada peticion trae su propia respuesta).
        """
        errores = [_error_http(429, "rate limit reached", caso=self)
                   for _ in range(1 + cerebro.REINTENTOS)]
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=errores) as abierto, \
                mock.patch("main.cerebro.time.sleep") as dormido:
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self._pedir()
        self.assertEqual(abierto.call_count, 1 + cerebro.REINTENTOS,
                         "no se hicieron 1 + REINTENTOS intentos")
        self.assertEqual(dormido.call_count, cerebro.REINTENTOS)
        texto = str(capturado.exception)
        self.assertIn("429", texto)
        self.assertEqual(capturado.exception.codigo, 429)
        self.assertNotIn(CLAVE_FALSA, texto, "la clave se filtro al error")

    def test_401_no_se_reintenta_y_no_duerme_ni_una_vez(self):
        """401 (clave mala) es un error de VERDAD: esperar no lo arregla"""
        error = _error_http(401, "Invalid API Key", caso=self)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=error) as abierto, \
                mock.patch("main.cerebro.time.sleep") as dormido:
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self._pedir()
        self.assertEqual(abierto.call_count, 1, "se reintento un 401")
        self.assertEqual(dormido.call_count, 0,
                         "se durmio por un 401: solo se espera por ritmo")
        self.assertIn("401", str(capturado.exception))

    def test_400_no_se_reintenta_y_no_duerme_ni_una_vez(self):
        error = _error_http(400, "invalid request: messages is required",
                            caso=self)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=error) as abierto, \
                mock.patch("main.cerebro.time.sleep") as dormido:
            with self.assertRaises(cerebro.ErrorCerebro) as capturado:
                self._pedir()
        self.assertEqual(abierto.call_count, 1, "se reintento un 400")
        self.assertEqual(dormido.call_count, 0)
        self.assertIn("400", str(capturado.exception))

    def test_503_tambien_se_espera_y_reintenta(self):
        """503 es "vuelve en un momento": entra en los codigos de ritmo"""
        error = _error_http(503, "service unavailable", caso=self)
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        side_effect=[error, _respuesta_http(
                            _datos_de_texto("son las 10"))]) as abierto, \
                mock.patch("main.cerebro.time.sleep") as dormido:
            respuesta = self._pedir()
        self.assertEqual(abierto.call_count, 2)
        dormido.assert_called_once_with(cerebro.ESPERA_POR_DEFECTO)
        self.assertEqual(respuesta.content[0].text, "son las 10")


class TestElModelo(unittest.TestCase):
    """el modelo que DE VERDAD viaja: se respeta y es el que se lee"""

    def setUp(self):
        self.cliente = cerebro.ClienteGroq(CLAVE_FALSA)

    def _cuerpo(self, **opciones):
        """cuerpo que se mando a Groq en una llamada (con urlopen mockeado)"""
        with mock.patch("main.cerebro.urllib_request.urlopen",
                        return_value=_respuesta_http(
                            _datos_de_texto("ok"))) as abierto:
            self.cliente.messages.create(
                messages=[{"role": "user", "content": "hola"}], **opciones)
        peticion = abierto.call_args.args[0]
        return json.loads(peticion.data.decode("utf-8"))

    def test_create_respeta_el_modelo_que_se_le_pasa(self):
        """un `model` con valor viaja TAL CUAL, sin que cerebro lo cambie

        Antes create se lo comia y mandaba siempre MODELO_GROQ: si alguien
        cambiaba MODELO_IA en main.py, el modelo real no cambiaba y nadie se
        enteraba.
        """
        cuerpo = self._cuerpo(model="otro/modelo", max_tokens=256,
                              system="s")
        self.assertEqual(cuerpo["model"], "otro/modelo",
                         "create no respeto el `model` que se le paso: mando "
                         "%r" % (cuerpo["model"],))

    def test_sin_modelo_se_usa_el_de_por_defecto(self):
        """create() sin `model` (None o vacio) manda MODELO_GROQ"""
        for vacio in (None, ""):
            cuerpo = self._cuerpo(model=vacio, max_tokens=256)
            self.assertEqual(
                cuerpo["model"], cerebro.MODELO_GROQ,
                "con model=%r se mando %r en vez del de por defecto (%r)"
                % (vacio, cuerpo["model"], cerebro.MODELO_GROQ))

    def test_el_modelo_de_main_y_el_de_cerebro_son_el_mismo(self):
        """main.MODELO_IA y cerebro.MODELO_GROQ no pueden separarse

        main.py LEE MODELO_IA y lo pasa a create, y cerebro usa MODELO_GROQ
        cuando no le llega ninguno. Si los dos nombres se separan, el modelo
        que se lee en el codigo deja de ser el que de verdad se usa y el
        cambio se pierde en silencio. Se importa main.main con el ayudante de
        test_fase_b_main porque importarlo a pelo exige las librerias de audio
        (pywhatkit no esta en esta maquina).
        """
        try:
            from tests.test_fase_b_main import _main
        except ImportError:  # el descubridor pone tests/ en el sys.path
            from test_fase_b_main import _main
        M = _main()
        self.assertEqual(
            M.MODELO_IA, cerebro.MODELO_GROQ,
            "MODELO_IA (%r, en main/main.py) y MODELO_GROQ (%r, en "
            "main/cerebro.py) se separaron: el modelo que se lee en el codigo "
            "ya no es el que se usa, y nadie se entera. Cambialas juntas."
            % (M.MODELO_IA, cerebro.MODELO_GROQ))


class TestSinIdeogramas(unittest.TestCase):
    """el editor mete ideogramas en archivos largos (fundamento 27)"""

    def test_solo_caracteres_latinos(self):
        ruta = Path(cerebro.__file__)
        texto = ruta.read_text(encoding="utf-8")
        raros = sorted({caracter for caracter in texto
                        if ord(caracter) > 0x2FFF})
        self.assertEqual(raros, [], "caracteres raros en %s: %r"
                         % (ruta.name, raros))


if __name__ == "__main__":
    unittest.main()
