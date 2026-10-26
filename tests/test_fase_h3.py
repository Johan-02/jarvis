# pruebas de la FASE H3 del proyecto Jarvis (2026-10-26): QUE JARVIS APRENDA
# Y LO RECUERDE.
#
# Frentes:
#   1. recordar: agrega UNA nota, el archivo sigue siendo json valido, y dos
#      veces la misma nota NO duplica;
#   2. LA PROTECCION DE LAS NOTAS DE EDWARD: si memoria.json esta corrupto,
#      recordar devuelve ok=False y el archivo queda BYTE A BYTE igual. Con
#      el lector normal (el que tapa un json roto con valores por defecto)
#      guardar encima las 17 notas se perderia para siempre;
#   3. LAS 17 NOTAS REALES: se parte de una COPIA temporal de la memoria real,
#      se agrega una nota y se comprueba que las 17 siguen, en su orden, con
#      el nombre intacto;
#   4. olvidar: DESTRUCTIVA (sin confirmar devuelve REQUIERE_CONFIRMACION y
#      CERO escrituras), confirmada borra UNA y solo una, y un indice o texto
#      que no existe devuelve ok=False sin tocar el archivo;
#   5. leer_memoria: dice el nombre y las notas, y NO escribe NADA;
#   6. EL PUNTO FINO: tras un recordar, el system prompt del SIGUIENTE
#      mensaje al modelo ya incluye la nota (y el del turno siguiente
#      tambien). Guardar y seguir con la foto vieja seria no haber aprendido;
#   7. clasificacion: recordar y leer_memoria son INOCUAS, olvidar esta en
#      DESTRUCTIVAS, y las tres salen SOLAS en herramientas().
#
# REGLA ABSOLUTA DE ESTE ARCHIVO: la memoria REAL de Edward
# (jarvis/memoria.json) JAMAS se escribe. Cada prueba apunta
# main.archivo_memoria a un archivo de un directorio TEMPORAL, y
# setUpModule/tearDownModule comprueban que el SHA-256 real no cambio en toda
# la corrida: si cambio, el archivo de pruebas REVIENTA.
#
# CERO llamadas a la API (cliente falso), CERO voz (hablar/escuchar doblados)
# y CERO microfono.

import hashlib
import json
import os
import shutil
import sys
import tempfile
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

try:
    from tests.test_fase_g2 import _texto, _herramienta, _respuesta, _cliente
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_fase_g2 import _texto, _herramienta, _respuesta, _cliente

from main import herramientas
from main import motor
from main.reconocedor import Accion

# memoria real de Edward: SOLO se lee (para copiarla) y se compara su hash
RUTA_MEMORIA_REAL = RAIZ / "memoria.json"

# cuantas notas tiene la memoria real hoy. Si Edward agrega o quita notas a
# mano, actualice este numero y el mensaje: la prueba no mide "17" por
# capricho, mide que un recordar NO toque las que habia
NOTAS_REALES = 17

# memoria chica para las pruebas de comportamiento
MEMORIA_DE_PRUEBA = {"nombre_usuario": "Yohan",
                     "notas": ["aprende django", "le gusta el cafe"]}

# nota con una palabra que NO aparece en el system prompt, para poder afirmar
# que antes del recordar no estaba y despues si
NOTA_NUEVA = "el perro de Edward se llama Firulais"
PALABRA_NUEVA = "Firulais"

_SHA_AL_EMPEZAR = None


def _sha_de_la_memoria_real():
    """SHA-256 del memoria.json REAL, en mayusculas"""
    with open(RUTA_MEMORIA_REAL, "rb") as archivo:
        return hashlib.sha256(archivo.read()).hexdigest().upper()


def setUpModule():
    """guarda el hash real ANTES de la primera prueba del archivo"""
    global _SHA_AL_EMPEZAR
    _SHA_AL_EMPEZAR = _sha_de_la_memoria_real()


def tearDownModule():
    """si el memoria.json real cambio durante estas pruebas, REVIENTA

    Es la red de seguridad de la regla absoluta: ninguna prueba de esta fase
    puede escribir la memoria de Edward. Se compara contra el hash tomado en
    setUpModule (no contra un hash fijo), asi una nota que Edward agregue a
    mano ANTES de correr la suite no se confunde con un bug.
    """
    ahora = _sha_de_la_memoria_real()
    if ahora != _SHA_AL_EMPEZAR:
        raise AssertionError(
            "el memoria.json REAL de Edward cambio durante las pruebas de la "
            "FASE H3: %s -> %s" % (_SHA_AL_EMPEZAR, ahora))


class _BaseMemoria(unittest.TestCase):
    """base: memoria en un directorio TEMPORAL, nunca la real"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.ruta = os.path.join(self.temporal.name, "memoria.json")
        parche = mock.patch.object(self.main, "archivo_memoria", self.ruta)
        parche.start()
        self.addCleanup(parche.stop)

    def escribir(self, datos):
        """deja `datos` como memoria del directorio temporal"""
        with open(self.ruta, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=4)

    def escribir_texto(self, texto):
        """deja un archivo CRUDO (para simular un json roto)"""
        with open(self.ruta, "w", encoding="utf-8") as archivo:
            archivo.write(texto)

    def leer(self):
        """memoria del directorio temporal"""
        with open(self.ruta, "r", encoding="utf-8") as archivo:
            return json.load(archivo)

    def bytes_del_archivo(self):
        """bytes EXACTOS del archivo, para probar que no se toco"""
        with open(self.ruta, "rb") as archivo:
            return archivo.read()

    def copiar_memoria_real(self):
        """copia la memoria REAL al directorio temporal (solo LEE la real)"""
        shutil.copyfile(RUTA_MEMORIA_REAL, self.ruta)

    def archivos_del_temporal(self):
        """lo que hay en el temporal: si sobra un .tmp, la escritura fallo"""
        return sorted(os.listdir(self.temporal.name))


class TestRecordar(_BaseMemoria):
    """recordar: una nota mas, json valido, sin duplicados"""

    def test_recordar_agrega_una_nota_y_el_archivo_sigue_siendo_json_valido(self):
        """la nota entra al final y el nombre de Edward NO se toca"""
        self.escribir(MEMORIA_DE_PRUEBA)

        resultado = motor.ejecutar(Accion("recordar", (NOTA_NUEVA,)))

        self.assertTrue(resultado.ok,
                        "recordar devolvio ok=False: %r" % (resultado.detalle,))
        self.assertIn(PALABRA_NUEVA, resultado.detalle,
                      "el detalle no dice que se guardo: %r"
                      % (resultado.detalle,))
        datos = self.leer()  # json.load: si el archivo quedo roto, revienta
        self.assertEqual(
            datos["notas"],
            MEMORIA_DE_PRUEBA["notas"] + [NOTA_NUEVA],
            "la nota no quedo al final o se perdio alguna de las que habia: %r"
            % (datos["notas"],))
        self.assertEqual(
            datos["nombre_usuario"], "Yohan",
            "recordar piso el nombre del usuario: %r"
            % (datos["nombre_usuario"],))
        self.assertEqual(
            self.archivos_del_temporal(), ["memoria.json"],
            "quedo un .tmp huerfano: la escritura no fue atomica")

    def test_recordar_dos_veces_la_misma_nota_no_la_duplica(self):
        """el mismo texto (aunque cambien los espacios) entra UNA sola vez"""
        self.escribir(MEMORIA_DE_PRUEBA)

        primera = motor.ejecutar(Accion("recordar", (NOTA_NUEVA,)))
        segunda = motor.ejecutar(
            Accion("recordar", ("  " + NOTA_NUEVA.replace(" ", "   ") + " ",)))

        self.assertTrue(primera.ok)
        self.assertTrue(segunda.ok,
                        "la segunda vez devolvio ok=False: %r"
                        % (segunda.detalle,))
        self.assertIn(
            "ya estaba", segunda.detalle,
            "la segunda vez no aviso que la nota ya estaba: %r"
            % (segunda.detalle,))
        datos = self.leer()
        self.assertEqual(
            datos["notas"].count(NOTA_NUEVA), 1,
            "la nota quedo duplicada: %r" % (datos["notas"],))
        self.assertEqual(
            len(datos["notas"]), len(MEMORIA_DE_PRUEBA["notas"]) + 1,
            "el numero de notas cambio de mas: %r" % (datos["notas"],))

    def test_recordar_con_texto_vacio_no_escribe_nada(self):
        """una nota vacia no se guarda y el archivo queda intacto"""
        self.escribir(MEMORIA_DE_PRUEBA)
        antes = self.bytes_del_archivo()

        for vacio in ("", "   ", None):
            with self.subTest(texto=vacio):
                resultado = motor.ejecutar(Accion("recordar", (vacio,)))
                self.assertFalse(resultado.ok,
                                 "una nota vacia (%r) se guardo" % (vacio,))
                self.assertEqual(
                    self.bytes_del_archivo(), antes,
                    "una nota vacia cambio el archivo")

    def test_sin_conexion_la_memoria_se_niega_y_no_toca_nada(self):
        """sin main.py conectado, recordar no inventa una ruta ni escribe"""
        self.escribir(MEMORIA_DE_PRUEBA)
        antes = self.bytes_del_archivo()

        with mock.patch.object(motor, "_LEER_MEMORIA", None), \
                mock.patch.object(motor, "_GUARDAR_MEMORIA", None):
            resultado = motor.ejecutar(Accion("recordar", (NOTA_NUEVA,)))

        self.assertFalse(resultado.ok)
        self.assertIn("conectada", resultado.detalle,
                      "el motivo no dice que falta la conexion: %r"
                      % (resultado.detalle,))
        self.assertEqual(self.bytes_del_archivo(), antes,
                         "sin conexion, recordar escribio en el archivo")


class TestProteccionDelArchivo(_BaseMemoria):
    """un json roto NO se puede pisar: las notas se perderian para siempre"""

    CASOS_ROTOS = {
        "truncado": '{"nombre_usuario": "Yohan", "notas": ["aprende django", ',
        "no_es_objeto": '[1, 2, 3]',
        "notas_no_es_lista": '{"nombre_usuario": "Yohan", "notas": "una sola"}',
        "notas_con_numero": '{"nombre_usuario": "Yohan", "notas": ["ok", 7]}',
        "vacio": "",
    }

    def test_recordar_con_archivo_corrupto_devuelve_false_y_no_lo_toca(self):
        """ok=False con el motivo, y los BYTES quedan identicos"""
        for nombre, crudo in self.CASOS_ROTOS.items():
            with self.subTest(caso=nombre):
                self.escribir_texto(crudo)
                antes = self.bytes_del_archivo()

                resultado = motor.ejecutar(Accion("recordar", (NOTA_NUEVA,)))

                self.assertFalse(
                    resultado.ok,
                    "con memoria.json roto (%s), recordar dijo ok=True y "
                    "PUDO haber pisado las notas: %r"
                    % (nombre, resultado.detalle))
                self.assertEqual(
                    self.bytes_del_archivo(), antes,
                    "con memoria.json roto (%s), recordar CAMBIO el archivo: "
                    "las notas de Edward se perdieron" % nombre)
                self.assertEqual(
                    self.archivos_del_temporal(), ["memoria.json"],
                    "con memoria.json roto (%s) quedo un .tmp" % nombre)

    def test_olvidar_con_archivo_corrupto_no_borra_ni_pisa_nada(self):
        """el mismo freno para la destructiva confirmada"""
        self.escribir_texto(self.CASOS_ROTOS["truncado"])
        antes = self.bytes_del_archivo()

        resultado = motor.ejecutar(Accion("olvidar", (1,)), confirmado=True)

        self.assertFalse(resultado.ok)
        self.assertEqual(self.bytes_del_archivo(), antes,
                         "olvidar piso un archivo roto")


class TestLasNotasDeEdwardSobreviven(_BaseMemoria):
    """una copia de la memoria REAL: agregar una nota no puede perder ninguna"""

    def test_las_notas_reales_sobreviven_a_un_recordar(self):
        """copia en temp -> 17 notas -> recordar -> 18, con las 17 intactas"""
        self.copiar_memoria_real()
        antes = self.leer()
        self.assertEqual(
            len(antes["notas"]), NOTAS_REALES,
            "la memoria real no tiene %d notas sino %d: si Edward las cambio "
            "a proposito, actualice NOTAS_REALES en esta prueba"
            % (NOTAS_REALES, len(antes["notas"])))

        resultado = motor.ejecutar(Accion("recordar", (NOTA_NUEVA,)))

        self.assertTrue(resultado.ok, resultado.detalle)
        despues = self.leer()
        self.assertEqual(
            len(despues["notas"]), NOTAS_REALES + 1,
            "tras recordar no hay %d notas sino %d"
            % (NOTAS_REALES + 1, len(despues["notas"])))
        self.assertEqual(
            despues["notas"][:NOTAS_REALES], antes["notas"],
            "las %d notas de Edward NO sobrevivieron iguales y en su orden"
            % (NOTAS_REALES,))
        self.assertEqual(
            despues["notas"][-1], NOTA_NUEVA,
            "la nota nueva no quedo al final")
        self.assertEqual(
            despues["nombre_usuario"], antes["nombre_usuario"],
            "el nombre del usuario cambio")


class TestOlvidar(_BaseMemoria):
    """olvidar: DESTRUCTIVA, borra UNA y solo una, y no inventa indices"""

    def test_olvidar_sin_confirmar_pide_confirmacion_y_no_escribe(self):
        """el freno de motor.ejecutar: cero escrituras sin confirmar"""
        self.copiar_memoria_real()
        antes = self.bytes_del_archivo()

        resultado = motor.ejecutar(Accion("olvidar", (1,)), confirmado=False)

        self.assertIn(
            "olvidar", motor.DESTRUCTIVAS,
            "olvidar no esta en DESTRUCTIVAS: se borraria una nota sin "
            "preguntar")
        self.assertEqual(
            resultado.detalle, motor.REQUIERE_CONFIRMACION,
            "olvidar sin confirmar devolvio %r en vez de "
            "REQUIERE_CONFIRMACION" % (resultado.detalle,))
        self.assertFalse(resultado.ok)
        self.assertEqual(
            self.bytes_del_archivo(), antes,
            "olvidar sin confirmar ESCRIBIO en la memoria")

    def test_olvidar_confirmado_borra_una_y_solo_una_por_numero(self):
        """la nota 2 desaparece; la 1 y la 3.. quedan iguales"""
        self.copiar_memoria_real()
        antes = self.leer()

        resultado = motor.ejecutar(Accion("olvidar", (2,)), confirmado=True)

        self.assertTrue(resultado.ok, resultado.detalle)
        despues = self.leer()
        self.assertEqual(
            len(despues["notas"]), len(antes["notas"]) - 1,
            "olvidar no borro exactamente UNA nota: %d -> %d"
            % (len(antes["notas"]), len(despues["notas"])))
        self.assertEqual(
            despues["notas"],
            antes["notas"][:1] + antes["notas"][2:],
            "olvidar no borro la nota 2, o se llevo otra: %r"
            % (despues["notas"],))
        self.assertEqual(despues["nombre_usuario"], antes["nombre_usuario"],
                         "olvidar piso el nombre del usuario")
        self.assertEqual(
            self.archivos_del_temporal(), ["memoria.json"],
            "olvidar dejo un .tmp huerfano")

    def test_olvidar_confirmado_por_texto_exacto_borra_solo_esa(self):
        """el texto se compara ENTERO: no borra por un pedazo"""
        self.copiar_memoria_real()
        antes = self.leer()
        objetivo = antes["notas"][4]

        resultado = motor.ejecutar(Accion("olvidar", (objetivo,)),
                                   confirmado=True)

        self.assertTrue(resultado.ok, resultado.detalle)
        despues = self.leer()
        self.assertEqual(
            despues["notas"], antes["notas"][:4] + antes["notas"][5:],
            "olvidar por texto no borro solo la nota pedida: %r"
            % (despues["notas"],))

    def test_olvidar_un_indice_que_no_existe_no_toca_el_archivo(self):
        """999, 0 y un texto que no esta: ok=False y bytes identicos"""
        self.copiar_memoria_real()
        antes = self.bytes_del_archivo()
        casos = (999, 0, -1, "999", "una nota que no existe")

        for objetivo in casos:
            with self.subTest(objetivo=objetivo):
                resultado = motor.ejecutar(
                    Accion("olvidar", (objetivo,)), confirmado=True)
                self.assertFalse(
                    resultado.ok,
                    "olvidar %r dijo ok=True y la nota no existe" % (objetivo,))
                self.assertEqual(
                    self.bytes_del_archivo(), antes,
                    "olvidar %r que no existe CAMBIO el archivo" % (objetivo,))
        self.assertEqual(
            self.archivos_del_temporal(), ["memoria.json"],
            "un olvidar fallido dejo un .tmp")


class TestLeerMemoria(_BaseMemoria):
    """leer_memoria: dice la verdad y NO escribe"""

    def test_leer_memoria_dice_nombre_cantidad_y_notas_sin_escribir(self):
        """nombre, 17 notas y la lista numerada; bytes intactos"""
        self.copiar_memoria_real()
        antes = self.bytes_del_archivo()
        real = self.leer()

        resultado = motor.ejecutar(Accion("leer_memoria"))

        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn(
            str(real["nombre_usuario"]), resultado.detalle,
            "leer_memoria no dijo el nombre: %r" % (resultado.detalle,))
        self.assertIn(
            "%d notas" % NOTAS_REALES, resultado.detalle,
            "leer_memoria no dijo cuantas notas hay: %r" % (resultado.detalle,))
        self.assertIn(
            "Nota 1: " + real["notas"][0], resultado.detalle,
            "leer_memoria no trae las notas numeradas: %r"
            % (resultado.detalle,))
        self.assertIn(
            "Nota %d: " % NOTAS_REALES + real["notas"][-1],
            resultado.detalle,
            "leer_memoria no llega hasta la ultima nota: %r"
            % (resultado.detalle,))
        self.assertEqual(
            self.bytes_del_archivo(), antes,
            "leer_memoria ESCRIBIO en la memoria: tenia que solo leer")
        self.assertEqual(
            self.archivos_del_temporal(), ["memoria.json"],
            "leer_memoria dejo un .tmp")


class TestElPromptSeEntera(_BaseMemoria):
    """el punto fino: la nota nueva tiene que llegar al MODELO, no solo al disco"""

    def test_tras_recordar_el_siguiente_mensaje_ya_lleva_la_nota(self):
        """la SEGUNDA llamada a create ya trae la nota en el system prompt

        La mutacion que mata: reconstruir el system prompt una sola vez al
        empezar el bucle. En ese caso la nota queda en el archivo pero el
        modelo sigue con la foto vieja y contesta como si no hubiera
        aprendido nada.
        """
        self.escribir(MEMORIA_DE_PRUEBA)
        cliente = _cliente([
            _respuesta([_herramienta(
                "recordar", {"texto": NOTA_NUEVA}, "t_recordar")], "tool_use"),
            _respuesta([_texto("Anotado, senor.")], "end_turn"),
        ])

        with mock.patch.object(self.main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)):
            texto = self.main.bucle_agente(
                "recuerda que " + NOTA_NUEVA, None,
                hablar=mock.MagicMock(), escuchar=mock.MagicMock())

        self.assertEqual(texto, "Anotado, senor.")
        self.assertEqual(
            cliente.messages.create.call_count, 2,
            "el bucle no hizo las dos llamadas esperadas")
        primero = cliente.messages.create.call_args_list[0].kwargs["system"]
        segundo = cliente.messages.create.call_args_list[1].kwargs["system"]
        self.assertNotIn(
            PALABRA_NUEVA, primero,
            "la nota ya estaba en el system prompt ANTES de recordarla: la "
            "prueba no mediria el refresco")
        self.assertIn(
            PALABRA_NUEVA, segundo,
            "tras recordar, el SIGUIENTE mensaje al modelo NO lleva la nota "
            "en el system prompt: Jarvis la guardo y no la sabe")
        self.assertEqual(
            self.leer()["notas"][-1], NOTA_NUEVA,
            "la nota no quedo en el archivo")

    def test_el_turno_siguiente_tambien_la_lleva(self):
        """un bucle_agente NUEVO arma el prompt desde el archivo ya cambiado"""
        self.escribir(MEMORIA_DE_PRUEBA)
        primer_cliente = _cliente([
            _respuesta([_herramienta(
                "recordar", {"texto": NOTA_NUEVA}, "t_recordar")], "tool_use"),
            _respuesta([_texto("Anotado.")], "end_turn"),
        ])
        with mock.patch.object(self.main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=primer_cliente)):
            self.main.bucle_agente("recuerda que " + NOTA_NUEVA, None,
                                   hablar=mock.MagicMock(),
                                   escuchar=mock.MagicMock())
        segundo_cliente = _cliente(
            [_respuesta([_texto("Su perro es Firulais.")], "end_turn")])

        with mock.patch.object(self.main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=segundo_cliente)):
            self.main.bucle_agente("como se llama mi perro", None,
                                   hablar=mock.MagicMock(),
                                   escuchar=mock.MagicMock())

        system = segundo_cliente.messages.create.call_args.kwargs["system"]
        self.assertIn(
            PALABRA_NUEVA, system,
            "el turno siguiente no armo el system prompt con la nota nueva: "
            "Jarvis olvido en el turno lo que aprendio en el anterior")


class TestClasificacionYHerramientas(unittest.TestCase):
    """las tres estan clasificadas y salen SOLAS en herramientas()"""

    def setUp(self):
        self.main = _main()
        self.definiciones = {definicion["name"]: definicion
                             for definicion in herramientas.herramientas()}

    def test_recordar_y_leer_memoria_son_inocuas_y_olvidar_destructiva(self):
        """la clasificacion es la unica puerta de la confirmacion"""
        for nombre in ("recordar", "leer_memoria"):
            with self.subTest(nombre=nombre):
                self.assertIn(nombre, motor.REGISTRO)
                self.assertNotIn(
                    nombre, motor.DESTRUCTIVAS,
                    "%r es inocua y quedo en DESTRUCTIVAS: pediria "
                    "confirmacion sin motivo" % (nombre,))
        self.assertIn("olvidar", motor.REGISTRO)
        self.assertIn(
            "olvidar", motor.DESTRUCTIVAS,
            "olvidar pierde una nota que Edward dijo y NO esta en "
            "DESTRUCTIVAS: se borraria sin confirmar")
        self.assertEqual(
            motor.CAMBIAN_MEMORIA, {"recordar", "olvidar"},
            "las acciones que cambian la memoria son las que obligan a "
            "rehacer el system prompt: %r" % (motor.CAMBIAN_MEMORIA,))

    def test_salen_solas_en_herramientas_con_su_firma(self):
        """el generador las publica del REGISTRO, sin catalogo a mano"""
        for nombre in ("recordar", "leer_memoria", "olvidar"):
            with self.subTest(nombre=nombre):
                self.assertIn(
                    nombre, self.definiciones,
                    "%r no salio en herramientas(): el generador no la ve en "
                    "REGISTRO" % (nombre,))
        recordar = self.definiciones["recordar"]["input_schema"]
        self.assertEqual(recordar["required"], ["texto"])
        self.assertEqual(recordar["properties"]["texto"], {"type": "string"})
        olvidar = self.definiciones["olvidar"]["input_schema"]
        self.assertEqual(olvidar["required"], ["objetivo"])
        self.assertIn("objetivo", olvidar["properties"])
        self.assertEqual(
            self.definiciones["leer_memoria"]["input_schema"],
            {"type": "object", "properties": {}},
            "leer_memoria no lleva argumentos")
        for nombre in ("recordar", "leer_memoria", "olvidar"):
            self.assertNotIn(
                "confirmado", self.definiciones[nombre]["input_schema"]
                .get("properties", {}),
                "%r expone confirmado: el modelo podria olvidar sin "
                "preguntar" % (nombre,))
        self.assertTrue(
            self.definiciones["olvidar"]["description"].startswith(
                herramientas.AVISO_DESTRUCTIVA),
            "olvidar no avisa al modelo que es destructiva: %r"
            % (self.definiciones["olvidar"]["description"],))


class TestLaMemoriaRealSigueIntacta(unittest.TestCase):
    """la red de seguridad, tambien como prueba visible"""

    def test_la_memoria_real_no_cambio_en_esta_corrida(self):
        """el SHA-256 real es el mismo que al empezar el archivo"""
        self.assertEqual(
            _sha_de_la_memoria_real(), _SHA_AL_EMPEZAR,
            "alguna prueba de la FASE H3 escribio el memoria.json REAL")
        self.assertEqual(
            len(json.loads(RUTA_MEMORIA_REAL.read_text(encoding="utf-8"))
                ["notas"]), NOTAS_REALES,
            "la memoria real no tiene las %d notas de Edward" % (NOTAS_REALES,))


if __name__ == "__main__":
    unittest.main()
