# pruebas del generador de herramientas de la API de Anthropic (FASE G1,
# 2026-10-23).
#
# El CORAZON de este archivo es TestAutomatismo: registra una accion FALSA
# (reiniciar_router) en REGISTRO dentro de una COPIA TEMPORAL del proyecto,
# con main/herramientas.py IDENTICO al original, y exige que su herramienta
# aparezca SOLA, con su docstring y su esquema. Sin eso, el generador seria
# mentira: habria un catalogo escondido a mano detras de la funcion.
#
# REGLAS DE ESTE ARCHIVO:
#   - TODOS los recorridos van sobre motor.REGISTRO y motor.DESTRUCTIVAS en
#     TIEMPO DE CORRIDA: ninguna lista congelada de nombres. Una accion nueva
#     sin herramienta, o una destructiva sin aviso, ROMPE la suite.
#   - test_ninguna_herramienta_expone_confirmado vale para TODAS las
#     herramientas: si alguna expone `confirmado`, el modelo podria escribir
#     confirmado=True y saltarse la confirmacion de motor.ejecutar (el freno
#     lo tiene el programa, no el modelo).
#   - NADA del motor se EJECUTA aqui: solo se LEEN firmas, docstrings y
#     registros. La accion falsa de la copia temporal tampoco se ejecuta.

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from main import herramientas
from main import motor

# tipos JSON Schema admitidos en una propiedad (los que el generador sabe
# escribir); un tipo fuera de esta lista no lo entenderia la API
TIPOS_JSON = {"string", "integer", "number", "boolean", "array", "object"}

# patron de nombre de herramienta que exige la API de Anthropic: letras,
# digitos, guion y guion bajo (ademas amarra la prueba contra ideogramas,
# fundamento 27). \A y \Z lo anclan ENTERO: sin ellos assertRegex aceptaria
# un nombre con espacios mientras su cola casara
PATRON_NOMBRE = re.compile(r"\A[a-zA-Z0-9_-]{1,64}\Z")

# accion FALSA que se le APPENDA al motor.py de una copia temporal: no vive
# en ningun archivo del proyecto, y por eso nadie pudo agregarla a mano en
# main/herramientas.py
CODIGO_ACCION_FALSA = '''

# --- accion FALSA de prueba: la inyecta tests/test_herramientas.py ---
def reiniciar_router(canal=1):
    """reinicia el router en el canal indicado. DESTRUCTIVA: corta la red."""
    return "accion falsa de prueba: esto NUNCA se ejecuta"


REGISTRO["reiniciar_router"] = reiniciar_router
DESTRUCTIVAS.add("reiniciar_router")
'''

# script que corre DENTRO de la copia temporal y devuelve su registro y sus
# herramientas en JSON, para compararlos aqui arriba
SCRIPT_JSON = (
    "import json\n"
    "from main import motor, herramientas\n"
    "print(json.dumps({'registro': sorted(motor.REGISTRO),\n"
    "                   'herramientas': herramientas.herramientas()},\n"
    "                  ensure_ascii=False))\n"
)


def _por_nombre():
    """herramientas generadas, indexadas por su name"""
    return {h["name"]: h for h in herramientas.herramientas()}


def _sin_aviso(descripcion):
    """description sin el aviso destructivo, para mirar SOLO la frase"""
    if descripcion.startswith(herramientas.AVISO_DESTRUCTIVA):
        return descripcion[len(herramientas.AVISO_DESTRUCTIVA):].lstrip()
    return descripcion


class TestCoberturaDelRegistro(unittest.TestCase):
    """toda accion de REGISTRO tiene su herramienta, generada del registro"""

    def test_todo_registro_tiene_su_herramienta_en_orden_estable(self):
        """PARAMETRIZADA sobre REGISTRO: ni falta ninguna ni sobra ninguna

        Se recorren las claves del dict en TIEMPO DE CORRIDA, asi que una
        accion futura queda cubierta sin tocar este archivo. El doble
        llamado ademas comprueba que la salida es determinista (orden por
        nombre), que es lo que hace reproducibles estas pruebas.
        """
        self.assertTrue(
            motor.REGISTRO,
            "REGISTRO vacio: la prueba pasaria sin medir nada de nada")
        generadas = herramientas.herramientas()
        nombres = [definicion["name"] for definicion in generadas]
        self.assertEqual(
            nombres, sorted(motor.REGISTRO),
            "las herramientas no salen ordenadas por nombre ni 1:1 con "
            "REGISTRO: %s" % (nombres,))
        self.assertEqual(
            len(generadas), len(motor.REGISTRO),
            "salieron %d herramientas para %d acciones del registro"
            % (len(generadas), len(motor.REGISTRO)))
        self.assertEqual(
            herramientas.herramientas(), generadas,
            "dos llamadas a herramientas() dieron resultados distintos: "
            "la salida no es determinista")

    def test_es_destructiva_y_accion_de_herramienta_siguen_al_registro(self):
        """el traductor valida contra REGISTRO en vivo, no contra una lista"""
        for nombre in sorted(motor.DESTRUCTIVAS):
            with self.subTest(nombre=nombre):
                self.assertTrue(
                    herramientas.es_destructiva(nombre),
                    "%r esta en DESTRUCTIVAS y el traductor la niega"
                    % (nombre,))
        for nombre in sorted(set(motor.REGISTRO) - motor.DESTRUCTIVAS):
            with self.subTest(nombre=nombre):
                self.assertFalse(
                    herramientas.es_destructiva(nombre),
                    "%r NO esta en DESTRUCTIVAS y el traductor la marca "
                    "destructiva" % (nombre,))
        for nombre in sorted(motor.REGISTRO):
            with self.subTest(nombre=nombre):
                self.assertEqual(
                    herramientas.accion_de_herramienta(nombre), nombre,
                    "el nombre de herramienta %r no se tradujo a si mismo"
                    % (nombre,))
        self.assertIsNone(
            herramientas.accion_de_herramienta("accion_que_no_existe"),
            "una herramienta inexistente tuvo que devolver None")
        self.assertIsNone(
            herramientas.accion_de_herramienta(""),
            "el nombre vacio tuvo que devolver None")


class TestSeguridadConfirmado(unittest.TestCase):
    """LA REGLA: el modelo NUNCA puede pasar `confirmado`"""

    def test_ninguna_herramienta_expone_confirmado(self):
        """recorre TODAS las herramientas, sin lista escrita a mano

        El freno de las destructivas vive en motor.ejecutar y lo controla
        el programa. Si `confirmado` apareciera en un input_schema (en
        required o en properties), el modelo podria escribir
        confirmado=True y apagar la PC sin preguntar; por eso esta prueba
        revienta ante EXPOSICION, venga de donde venga.
        """
        definiciones = herramientas.herramientas()
        self.assertTrue(
            definiciones,
            "no hay ninguna herramienta: la prueba pasaria sin revisar nada")
        for definicion in definiciones:
            nombre = definicion["name"]
            esquema = definicion["input_schema"]
            with self.subTest(nombre=nombre, campo="required"):
                self.assertNotIn(
                    "confirmado", esquema.get("required", []),
                    "la herramienta %r declara `confirmado` OBLIGATORIO: el "
                    "modelo podria confirmar una accion destructiva por su "
                    "cuenta y el freno de motor.ejecutar no serviria"
                    % (nombre,))
            with self.subTest(nombre=nombre, campo="properties"):
                self.assertNotIn(
                    "confirmado", esquema.get("properties", {}),
                    "la herramienta %r EXPONE `confirmado` en su "
                    "input_schema: el modelo podria escribir confirmado=True "
                    "y saltarse la confirmacion de motor.ejecutar (el freno "
                    "lo tiene el programa, no el modelo)" % (nombre,))


class TestAvisoDestructivo(unittest.TestCase):
    """las destructivas avisan ANTES de que el modelo decida llamarlas"""

    def test_el_texto_del_aviso_es_el_acordado(self):
        """un cambio de texto en el aviso rompe el contrato con el modelo"""
        self.assertEqual(
            herramientas.AVISO_DESTRUCTIVA,
            "ACCION DESTRUCTIVA: requiere confirmacion explicita del "
            "usuario antes de ejecutarla.")

    def test_toda_destructiva_lleva_el_aviso_primero(self):
        """PARAMETRIZADA sobre DESTRUCTIVAS, recorrida en tiempo de corrida"""
        self.assertTrue(
            motor.DESTRUCTIVAS,
            "DESTRUCTIVAS vacio: la prueba no mediria ningun aviso")
        generadas = _por_nombre()
        for nombre in sorted(motor.DESTRUCTIVAS):
            with self.subTest(nombre=nombre):
                definicion = generadas.get(nombre)
                self.assertIsNotNone(
                    definicion,
                    "la destructiva %r no tiene ninguna herramienta: no "
                    "hay donde avisarle al modelo" % (nombre,))
                self.assertTrue(
                    definicion["description"].startswith(
                        herramientas.AVISO_DESTRUCTIVA),
                    "la destructiva %r no empieza con el aviso: su "
                    "description es %r"
                    % (nombre, definicion["description"]))


class TestDefinicionesValidas(unittest.TestCase):
    """las definiciones tienen la forma que la API de Anthropic exige"""

    def test_las_definiciones_son_validas_para_la_api(self):
        """recorre TODAS: name y description no vacios, esquema de objeto"""
        definiciones = herramientas.herramientas()
        self.assertTrue(
            definiciones,
            "no hay ninguna herramienta: la prueba pasaria sin revisar nada")
        for definicion in definiciones:
            nombre = definicion.get("name", "")
            with self.subTest(nombre=nombre, campo="name"):
                self.assertIsInstance(nombre, str)
                self.assertTrue(
                    nombre.strip(),
                    "hay una herramienta sin nombre: %r" % (definicion,))
                self.assertRegex(
                    nombre, PATRON_NOMBRE,
                    "el nombre %r no cumple el patron que exige la API "
                    "(letras, digitos, guion y guion bajo, hasta 64)"
                    % (nombre,))
            with self.subTest(nombre=nombre, campo="description"):
                descripcion = definicion.get("description", "")
                self.assertIsInstance(descripcion, str)
                self.assertTrue(
                    descripcion.strip(),
                    "la herramienta %r tiene la description VACIA: toda "
                    "accion necesita su docstring en motor.py" % (nombre,))
            with self.subTest(nombre=nombre, campo="input_schema"):
                esquema = definicion.get("input_schema")
                self.assertIsInstance(esquema, dict)
                self.assertEqual(
                    esquema.get("type"), "object",
                    "input_schema de %r sin type object: %r" % (nombre, esquema))
                propiedades = esquema.get("properties")
                self.assertIsInstance(propiedades, dict)
                obligatorios = esquema.get("required", [])
                self.assertTrue(
                    set(obligatorios) <= set(propiedades),
                    "required de %r nombra parametros que no existen en "
                    "properties: %r" % (nombre, obligatorios))
                if nombre not in motor.REGISTRO:
                    # sin firma que comparar: la prueba de cobertura ya la
                    # acusa (herramienta que no viene del registro)
                    continue
                firmado = inspect.signature(motor.REGISTRO[nombre])
                for parametro in firmado.parameters.values():
                    propiedad = propiedades.get(parametro.name)
                    self.assertIsInstance(
                        propiedad, dict,
                        "%s.%s no tiene ninguna propiedad en el esquema"
                        % (nombre, parametro.name))
                    # solo se exige tipo cuando la FIRMA lo dice (anotacion
                    # o defecto): sin ninguna de las dos la propiedad sale
                    # sin "type" y eso es JSON Schema draft 2020-12 valido
                    firma_dice_tipo = (
                        parametro.annotation is not inspect.Parameter.empty
                        or parametro.default is not inspect.Parameter.empty)
                    if firma_dice_tipo:
                        self.assertIn(
                            propiedad.get("type"), TIPOS_JSON,
                            "%s.%s tiene un tipo que la API no entiende: %r"
                            % (nombre, parametro.name, propiedad.get("type")))


class TestLimpiezaDeDocstrings(unittest.TestCase):
    """la description es la PRIMERA frase, no el docstring entero"""

    def test_las_descriptions_se_limpian_para_todas(self):
        """recorre TODAS: sin saltos de linea, sin el cuerpo, sin quedar vacias"""
        self.assertTrue(
            motor.REGISTRO,
            "REGISTRO vacio: la prueba pasaria sin medir nada")
        generadas = _por_nombre()
        for nombre in sorted(motor.REGISTRO):
            docstring = motor.REGISTRO[nombre].__doc__
            completo_descripcion = generadas[nombre]["description"]
            frase = _sin_aviso(completo_descripcion)
            with self.subTest(nombre=nombre):
                self.assertTrue(
                    frase,
                    "la accion %r no tiene docstring en motor.py: su "
                    "description quedaria vacia" % (nombre,))
                self.assertNotIn(
                    "\n", completo_descripcion,
                    "la description de %r trae saltos de linea" % (nombre,))
                if not docstring or "\n" not in docstring:
                    continue  # una docstring de UNA linea no puede colarse entera
                completo = " ".join(docstring.split())
                self.assertNotEqual(
                    frase, completo,
                    "la description de %r es el docstring ENTERO, no su "
                    "primera frase" % (nombre,))
                self.assertLess(
                    len(frase), len(completo),
                    "la description de %r pesa lo mismo que el docstring "
                    "completo: se colo entero" % (nombre,))

    def test_la_docstring_larga_de_apagar_no_se_cuela(self):
        """caso concreto: cuatro lineas de docstring y solo la primera sale"""
        texto = _por_nombre()["apagar"]["description"]
        self.assertEqual(
            texto,
            herramientas.AVISO_DESTRUCTIVA
            + " apaga la computadora con shutdown /s /t.",
            "apagar salio con la frase equivocada: %r" % (texto,))
        self.assertNotIn(
            "SOLO se llega aqui", texto,
            "se colo una frase del CUERPO del docstring de apagar")
        self.assertNotIn(
            "cancelar_apagado", texto,
            "se colo una linea posterior del docstring de apagar")


class TestFirmaDeAcciones(unittest.TestCase):
    """required y tipos salen de inspect.signature, recalculados AQUI"""

    def test_required_sale_de_la_firma_sin_defecto(self):
        """parametro sin defecto -> obligatorio; con defecto -> opcional

        La expectativa se recalcula con inspect.signature EN LA PRUEBA
        (no se le pregunta al generador), asi que un required escrito a
        mano en main/herramientas.py romperia aqui.
        """
        for nombre in sorted(motor.REGISTRO):
            firmado = inspect.signature(motor.REGISTRO[nombre])
            esquema = _por_nombre()[nombre]["input_schema"]
            propiedades = esquema.get("properties", {})
            obligatorios = esquema.get("required", [])
            with self.subTest(nombre=nombre):
                self.assertEqual(
                    sorted(propiedades), sorted(firmado.parameters),
                    "los properties de %r no son los parametros de la firma"
                    % (nombre,))
                for parametro in firmado.parameters.values():
                    if parametro.default is inspect.Parameter.empty:
                        self.assertIn(
                            parametro.name, obligatorios,
                            "%s.%s no tiene defecto y no salio en required"
                            % (nombre, parametro.name))
                    else:
                        self.assertNotIn(
                            parametro.name, obligatorios,
                            "%s.%s TIENE defecto y salio en required"
                            % (nombre, parametro.name))

    def test_un_parametro_none_no_es_obligatorio_y_se_tipa_string(self):
        """raiz=None (buscar_archivo, peso_de): opcional, tipo string"""
        ninguno_obligatorio = []
        for nombre in sorted(motor.REGISTRO):
            firmado = inspect.signature(motor.REGISTRO[nombre])
            esquema = _por_nombre()[nombre]["input_schema"]
            for parametro in firmado.parameters.values():
                if parametro.default is None:
                    self.assertNotIn(
                        parametro.name, esquema.get("required", []),
                        "%s.%s tiene defecto None y salio en required"
                        % (nombre, parametro.name))
                    self.assertEqual(
                        esquema["properties"][parametro.name],
                        {"type": "string"},
                        "%s.%s tiene defecto None y no se tipo string"
                        % (nombre, parametro.name))
                    ninguno_obligatorio.append("%s.%s" % (nombre, parametro.name))
        self.assertTrue(
            ninguno_obligatorio,
            "ninguna accion trae un parametro con defecto None: la prueba "
            "pasaria sin cubrir el caso de raiz=None")
        esquema = _por_nombre()["buscar_archivo"]["input_schema"]
        self.assertIn("raiz", esquema["properties"])
        self.assertNotIn("raiz", esquema.get("required", []))
        self.assertIn("texto", esquema.get("required", []))


class TestAutomatismo(unittest.TestCase):
    """el generador NO tiene catalogo: una accion falsa aparece SOLA"""

    def test_accion_falsa_registrada_genera_su_herramienta_sola(self):
        """copia temporal + accion falsa + herramienta que NADIE escribio a mano

        Se copia main/ a un directorio temporal, se le APPENDA al motor.py
        de LA COPIA la accion falsa reiniciar_router (registrada en
        REGISTRO y en DESTRUCTIVAS) y se corre un subprocesso contra esa
        copia. main/herramientas.py de la copia tiene que quedar BYTE A
        BYTE igual al original, y aun asi la herramienta falsa aparece,
        con su docstring como description (mas el aviso destructivo) y su
        esquema generado de la firma. Si alguien agregara las herramientas
        a mano, o quitara una accion del recorrido, esta prueba REVIENTA.
        """
        with tempfile.TemporaryDirectory() as temporal:
            copia = Path(temporal) / "jarvis"
            shutil.copytree(
                RAIZ / "main", copia / "main",
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.wav", ".env", "prueba_*.py"))

            # se parcha SOLO el motor.py de la copia; el generador queda
            # intacto y eso lo comprueba la linea siguiente
            ruta_motor_copia = copia / "main" / "motor.py"
            with open(ruta_motor_copia, "a", encoding="utf-8") as archivo:
                archivo.write(CODIGO_ACCION_FALSA)
            self.assertEqual(
                (RAIZ / "main" / "herramientas.py").read_bytes(),
                (copia / "main" / "herramientas.py").read_bytes(),
                "la copia NO conserva el generador IDENTICO: esta prueba no "
                "probaria que la herramienta salio sola")

            # el subprocesso corre CONTRA LA COPIA (cwd) y sin JARVIS_ROOT,
            # para que ni el path ni el entorno apunten al proyecto real
            entorno = {clave: valor for clave, valor in os.environ.items()
                       if clave != "JARVIS_ROOT"}
            hecho = subprocess.run(
                [sys.executable, "-c", SCRIPT_JSON], cwd=str(copia),
                capture_output=True, text=True, timeout=120, env=entorno)
            self.assertEqual(
                hecho.returncode, 0,
                "el subprocesso de la copia revienta:\nSTDOUT: %s\nSTDERR: %s"
                % (hecho.stdout, hecho.stderr))
            try:
                datos = json.loads(hecho.stdout)
            except ValueError:
                self.fail(
                    "la salida del subprocesso no es JSON: %r (stderr: %r)"
                    % (hecho.stdout, hecho.stderr))

            registro = datos["registro"]
            self.assertIn(
                "reiniciar_router", registro,
                "la accion falsa no quedo registrada en la copia")
            nombres = [definicion["name"]
                       for definicion in datos["herramientas"]]
            self.assertEqual(
                nombres, registro,
                "las herramientas de la copia no son 1:1 con SU registro "
                "(sobra o falta una, y nadie la agrego a mano): herramientas"
                "=%s registro=%s" % (nombres, registro))

            falsas = [definicion for definicion in datos["herramientas"]
                      if definicion["name"] == "reiniciar_router"]
            self.assertEqual(
                len(falsas), 1,
                "la accion falsa salio %d veces: tenia que salir UNA, sola"
                % (len(falsas)))
            self.assertEqual(
                falsas[0]["description"],
                herramientas.AVISO_DESTRUCTIVA
                + " reinicia el router en el canal indicado.",
                "la accion falsa no trajo SU docstring como description: %r"
                % (falsas[0]["description"],))
            self.assertEqual(
                falsas[0]["input_schema"],
                {"type": "object", "properties": {"canal": {"type": "integer"}}},
                "el esquema de la accion falsa no salio de SU firma: %r"
                % (falsas[0]["input_schema"],))


if __name__ == "__main__":
    unittest.main()
