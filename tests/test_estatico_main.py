# pruebas estaticas del proyecto Jarvis (FASE A, 2026-10-19)
#
# Estas pruebas leen el codigo como TEXTO o como AST. NUNCA importan
# main/main.py porque importarlo abre el microfono, pide clave de API y habla.
#
# TRAMPA YA PISADA (fundamento): @unittest.expectedFailure se come
# CUALQUIER excepcion, no solo AssertionError. Por eso cada prueba que hoy da
# fallo conocido se verifico quitandole el decorador a mano en una copia
# temporal y mirando que falle en la ASERCION y no con un ModuleNotFoundError
# ni un SyntaxError.
#
# El decorador se pone solo cuando el defecto todavia existe y NO se puede
# tocar el archivo en esta fase. Cuando el defecto se arregla, se quita.

import ast
import os
import re
import sys
import unittest
from pathlib import Path

# raiz del proyecto; se puede sobre-escribir con la variable de entorno
# JARVIS_ROOT para correr una copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))

RUTA_MAIN = RAIZ / "main" / "main.py"
RUTA_REQUIREMENTS = RAIZ / "requirements.txt"
RUTA_README = RAIZ / "README.md"

# nombre del import -> nombre del paquete en pip, cuando difieren
NOMBRE_EN_PIP = {
    "dotenv": "python-dotenv",
    "edge_tts": "edge-tts",
    "speech_recognition": "speechrecognition",
}


def _arbol_main():
    """parsea main/main.py como AST y devuelve (arbol, texto) sin importarlo"""
    texto = RUTA_MAIN.read_text(encoding="utf-8")
    return ast.parse(texto), texto


def _es_guard_main(prueba):
    """detecta si una condicion es el guard: __name__ == "__main__" """
    if not isinstance(prueba, ast.Compare):
        return False
    if not (isinstance(prueba.left, ast.Name) and prueba.left.id == "__name__"):
        return False
    for comparador in prueba.comparators:
        if isinstance(comparador, ast.Constant) and comparador.value == "__main__":
            return True
    return False


def _lista_asignada(arbol, nombre):
    """devuelve la lista de strings asignada a la variable nombre"""
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name) and destino.id == nombre:
                    if isinstance(nodo.value, ast.List):
                        return [elemento.value for elemento in nodo.value.elts]
    return []


def _nombres_del_requirements():
    """nombres de paquete (en minusculas) declarados en requirements.txt"""
    nombres = []
    for linea in RUTA_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        nombre = re.split(r"[<>=!~\[; \t]", linea)[0]
        nombres.append(nombre.lower())
    return nombres


def _paquetes_de_main():
    """paquetes pip (sin stdlib) que importa main/main.py"""
    arbol, _ = _arbol_main()
    importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                importados.add(alias.name.split(".")[0])
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level == 0 and nodo.module:
                importados.add(nodo.module.split(".")[0])
    externos = importados - set(sys.stdlib_module_names)
    # los modulos LOCALES del proyecto no son paquetes pip: main/reconocedor.py
    # se importa como main.reconocedor y no hay que instalar nada para ello
    locales = {entrada.name for entrada in RAIZ.iterdir()
               if entrada.is_dir() and not entrada.name.startswith((".", "_"))}
    locales |= {entrada.stem for entrada in RAIZ.glob("*.py")}
    externos = externos - locales
    return sorted(NOMBRE_EN_PIP.get(modulo, modulo) for modulo in externos)


class TestMainEstatico(unittest.TestCase):
    """pruebas que leen main/main.py sin importarlo"""

    def test_sin_except_vacio(self):
        """no debe haber except sin tipo (bare except) ni except que solo haga pass"""
        arbol, _ = _arbol_main()
        lineas_sin_tipo = []
        lineas_vacios = []
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.ExceptHandler):
                if nodo.type is None:
                    lineas_sin_tipo.append(nodo.lineno)
                # un handler cuyo cuerpo sea todo pass se considera vacio
                if not nodo.body or all(
                    isinstance(instruccion, ast.Pass) for instruccion in nodo.body
                ):
                    lineas_vacios.append(nodo.lineno)
        self.assertEqual(
            lineas_sin_tipo, [],
            "hay %d except sin tipo (bare except) en las lineas %s"
            % (len(lineas_sin_tipo), sorted(lineas_sin_tipo)))
        self.assertEqual(
            lineas_vacios, [],
            "hay %d except vacios (solo pass) en las lineas %s"
            % (len(lineas_vacios), sorted(lineas_vacios)))

    def test_guard_main(self):
        """main/main.py debe tener el guard if __name__ == "__main__": al final"""
        arbol, _ = _arbol_main()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.If) and _es_guard_main(nodo.test):
                return
        self.fail('no existe el guard if __name__ == "__main__": en main/main.py')

    def test_activacion_sin_duplicados(self):
        """FRASE_ACTIVACION_INICIAL no debe repetir ninguna frase"""
        arbol, _ = _arbol_main()
        frases = _lista_asignada(arbol, "FRASE_ACTIVACION_INICIAL")
        repetidas = sorted(set(frase for frase in frases if frases.count(frase) > 1))
        self.assertEqual(
            repetidas, [],
            "frases repetidas en FRASE_ACTIVACION_INICIAL: %s" % repetidas)

    # NOTA: ninguna de las pruebas de esta clase lleva hoy el decorador
    # @unittest.expectedFailure: marcar una prueba que pasa como fallo
    # esperado daria un "unexpected success" y romperia la suite.
    # test_requirements_real se arreglo en la FASE A (requirements.txt viejo
    # tenia Django==4.2.0 y le faltaban edge-tts, playsound y pywhatkit);
    # test_guard_main y test_sin_except_vacio se arreglaron en la FASE B
    # (guard de __name__ == "__main__" y except con tipos concretos);
    # test_activacion_sin_duplicados se arreglo en la FASE C (habia DOS
    # "arriba jarvis" en FRASE_ACTIVACION_INICIAL); test_readme_honesto se
    # arreglo en la FASE F (el README prometia Django e interfaz web, que no
    # existen, y mentia sobre comandos y limites).
    def test_requirements_real(self):
        """requirements.txt debe listar exactamente lo que el codigo importa"""
        nombres = _nombres_del_requirements()
        # (i) Django sobra: este proyecto no tiene una sola linea que lo use
        self.assertNotIn(
            "django", nombres,
            "requirements.txt aun lista Django, que no importa ningun archivo")
        # (ii) cada paquete pip que importa main/main.py debe estar listado
        faltantes = [paquete for paquete in _paquetes_de_main()
                     if paquete not in nombres]
        self.assertEqual(
            faltantes, [],
            "faltan en requirements.txt: %s" % faltantes)

    def test_readme_honesto(self):
        """el README no debe prometer Django ni interfaz web: no existen"""
        texto = RUTA_README.read_text(encoding="utf-8").lower()
        self.assertNotIn(
            "django", texto,
            "el README todavia menciona Django, que no usa el proyecto")
        self.assertNotIn(
            "interfaz web", texto,
            "el README todavia promete una interfaz web inexistente")


if __name__ == "__main__":
    unittest.main()
