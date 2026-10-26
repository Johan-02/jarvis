# pruebas de la FASE B del proyecto Jarvis (2026-10-19)
#
# A diferencia de test_estatico_main.py, ESTAS pruebas importan main/main.py.
# Solo se pueden correr ya con el guard de __name__ == "__main__" en su sitio;
# sin el guard la importacion entraria en el bucle principal y colgaria la
# suite, por eso el doble de sounddevice que instala _instalar_dobles()
# REVENTA en cuanto alguien llama a grabar.
#
# REGLA DE ORO de esta fase: cada prueba se verifico ANTES del arreglo
# quitandole a mano el decorador @unittest.expectedFailure en una copia
# temporal y confirmando que falla en la ASERCION (no con ModuleNotFoundError
# ni SyntaxError). El decorador se quita recien cuando el arreglo esta hecho.
#
# TRAMPA DEL PROYECTO: @unittest.expectedFailure se come CUALQUIER excepcion,
# no solo AssertionError.

import ast
import datetime
import importlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))

RUTA_MAIN = RAIZ / "main" / "main.py"
RUTA_MEMORIA = RAIZ / "memoria.json"
RUTA_APOYO = RAIZ / "tests" / "apoyo_subproceso_main.py"

# timeout de los subprocesos que importan main.main. Medido en esta maquina:
# la peor marca fue 21.7 s (import con la caché fria; solo `import anthropic`
# cuesta 13.6 s). 45 s es mas del doble de esa peor marca. El corte NO es la
# medida del bug B8: que el arranque este bajo el guard se comprueba por AST
# (instantaneo e inmune a la velocidad de la maquina, fundamento 28); aqui el
# corte solo informa que el modulo no termino, con la salida PARCIAL real.
TIEMPO_SUBPROCESO = 45

# librerias pip que importa main/main.py: si faltan en la maquina NO es el
# defecto que miden estas pruebas, asi que el subproceso las tolera
LIBRERIAS_EXTERNAS = (
    "anthropic", "dotenv", "edge_tts", "numpy", "playsound",
    "pywhatkit", "sounddevice", "speech_recognition",
)


def _doble(nombre, **atributos):
    """crea un modulo falso y lo registra en sys.modules"""
    modulo = types.ModuleType(nombre)
    for clave, valor in atributos.items():
        setattr(modulo, clave, valor)
    sys.modules[nombre] = modulo
    return modulo


def _doble_si_falta(nombre, atributo):
    """instala un doble solo si la libreria real no esta disponible"""
    if nombre in sys.modules:
        if hasattr(sys.modules[nombre], atributo):
            return False
    else:
        try:
            importlib.import_module(nombre)
        except Exception:
            pass  # no esta instalada: se dobla abajo
        else:
            if hasattr(sys.modules[nombre], atributo):
                return False
    return True


def _class_Communicate():
    """doble de edge_tts.Communicate: escribe un mp3 vacio y no dice nada"""
    class _Communicate:
        def __init__(self, *argumentos, **opciones):
            pass

        async def save(self, ruta):
            with open(ruta, "wb") as archivo:
                archivo.write(b"")

    return _Communicate


def _instalar_dobles():
    """doble de las librerias de audio que pueden faltar en la maquina.

    sounddevice SIEMPRE se dobla aunque este instalado: su rec() reventa
    avisando que es un doble. Asi una importacion sin el guard principal
    falla RAPIDO en vez de colgarse en el microfono para siempre.
    """
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))

    if _doble_si_falta("edge_tts", "Communicate"):
        _doble("edge_tts", Communicate=_class_Communicate())

    if _doble_si_falta("playsound", "playsound"):
        _doble("playsound", playsound=lambda ruta: None)

    if _doble_si_falta("pywhatkit", "playonyt"):
        _doble("pywhatkit", playonyt=lambda busqueda: None)

    def _rec(*argumentos, **opciones):
        raise RuntimeError(
            "doble de prueba: main/main.py llamo al microfono al importar")

    def _wait():
        raise RuntimeError(
            "doble de prueba: main/main.py espero al microfono al importar")

    _doble("sounddevice", rec=_rec, wait=_wait)


def _main():
    """importa main.main en este mismo proceso con los dobles instalados"""
    if "main.main" in sys.modules:
        return sys.modules["main.main"]
    _instalar_dobles()
    return importlib.import_module("main.main")


def _texto_de_timeout(capturado):
    """la salida parcial de TimeoutExpired llega en bytes o en str segun version"""
    if capturado is None:
        return ""
    if isinstance(capturado, bytes):
        return capturado.decode("utf-8", errors="replace")
    return capturado


def _importar_en_subproceso(sin_clave=False, tiempo_maximo=TIEMPO_SUBPROCESO,
                            modos=()):
    """importa main.main en un subproceso con cwd temporal.

    modos es una tupla de palabras que se pasan al apoyo tal cual
    ("sin_clave", "doble_ia").

    devuelve (codigo, stdout, stderr); codigo es None si NO TERMINO en
    tiempo_maximo, y entonces stdout/stderr son la salida PARCIAL que el
    proceso alcanzo a imprimir antes del corte, para poder decir donde se
    quedo en vez de adivinarlo.
    """
    comando = [sys.executable, str(RUTA_APOYO), str(RAIZ)]
    if sin_clave:
        comando.append("sin_clave")
    comando.extend(modos)
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory() as tmp:
        # cwd temporal: el modulo escribe wav y mp3, y en una carpeta
        # temporal eso no ensucia el proyecto
        try:
            proceso = subprocess.run(
                comando, cwd=tmp, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=tiempo_maximo, env=entorno)
        except subprocess.TimeoutExpired as exc:
            return None, _texto_de_timeout(getattr(exc, "stdout", None)), \
                _texto_de_timeout(getattr(exc, "stderr", None))
    return proceso.returncode, proceso.stdout, proceso.stderr


def _falta_de_libreria_propia(stderr):
    """true si el subproceso murio porque falta una libreria pip del proyecto"""
    if "ModuleNotFoundError" not in stderr:
        return False
    for libreria in LIBRERIAS_EXTERNAS:
        if ("No module named '%s'" % libreria) in stderr:
            return True
    return False


def _punto_del_corte(salida):
    """frase VERDADERA sobre donde se quedo un subproceso que no termino

    Se apoya en los marcadores que imprime el apoyo con flush: IMPORTANDO
    (antes de importar main.main) e IMPORT_OK (despues). Sin marcadores no
    se puede saber nada, y decir "falta el guard" sin medirlo es mentir.
    """
    if "IMPORT_OK" in salida:
        return ("imprimio IMPORT_OK pero no salio: lo que no termino es "
                "DESPUES del import, no el import")
    if "IMPORTANDO" in salida:
        return ("imprimio IMPORTANDO y no IMPORT_OK: no termino de importar "
                "main.main (sus librerias o su codigo a nivel de modulo); "
                "esto NO prueba que falte el guard")
    if salida:
        return "imprimio %r y se corto antes de llegar a IMPORTANDO" % salida
    return "no llego a imprimir ningun marcador"


def _padres_del_arbol(arbol):
    """mapa nodo -> nodo padre, para saber si un nodo esta dentro de una funcion"""
    padres = {}
    for padre in ast.walk(arbol):
        for hijo in ast.iter_child_nodes(padre):
            padres[hijo] = padre
    return padres


def _esta_en_funcion(nodo, padres):
    actual = padres.get(nodo)
    while actual is not None:
        if isinstance(actual, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        actual = padres.get(actual)
    return False


def _funcion(arbol, nombre):
    """devuelve el FunctionDef con ese nombre, o None"""
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == nombre:
            return nodo
    return None


def _llamadas_de_funcion(funcion):
    """(linea, nombre) de cada llamada con nombre dentro de una funcion"""
    llamadas = []
    for nodo in ast.walk(funcion):
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name):
            llamadas.append((nodo.lineno, nodo.func.id))
    return llamadas


def _es_lectura_de_reloj(nodo):
    """true si el nodo es una llamada que lee el reloj: cualquier algo.now()"""
    return isinstance(nodo, ast.Call) and \
        isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "now"


def _llamadas_de_reloj(funcion):
    """lineas de cada lectura del reloj dentro de una funcion"""
    return sorted(nodo.lineno for nodo in ast.walk(funcion)
                  if _es_lectura_de_reloj(nodo))


def _lecturas_de_reloj_a_nivel_de_modulo(arbol):
    """lineas de cada lectura del reloj que NO esta dentro de ninguna funcion

    Se recorre el arbol saltandose funciones, clases y lambdas: lo que quede
    a la vista se evalua UNA sola vez al importar el modulo y despues miente.
    No se mira el nombre de la variable a proposito: renombrar la constante
    no la salva.
    """
    lineas = []
    pendientes = list(ast.iter_child_nodes(arbol))
    while pendientes:
        nodo = pendientes.pop()
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        if _es_lectura_de_reloj(nodo):
            lineas.append(nodo.lineno)
        pendientes.extend(ast.iter_child_nodes(nodo))
    return sorted(lineas)


def _reloj_congelado(instante):
    """doble del modulo datetime cuyo now() devuelve siempre el instante dado"""
    reloj = mock.MagicMock()
    reloj.datetime.now.return_value = instante
    return reloj


class _FinDeLaPrueba(Exception):
    """excepcion propia para salir del bucle de activacion tras medir"""


def _arbol():
    return ast.parse(RUTA_MAIN.read_text(encoding="utf-8"))


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


def _arranque_fuera_del_guard(arbol, nombres):
    """(linea, nombre) de llamadas a esas funciones que correrian al importar

    Recorre el cuerpo del modulo saltandose funciones y clases (ahi no pasa
    nada al importar) y entrando en todos los if, marcando como "dentro del
    guard" todo lo que queda bajo if __name__ == "__main__":. Una llamada a
    esas funciones FUERA del guard se ejecuta al importar el modulo, que es
    el bug B8. Se mide por AST: es instantaneo y NO depende de la velocidad
    de la maquina (fundamento 28).
    """
    fuera = []
    pendientes = [(hijo, False) for hijo in ast.iter_child_nodes(arbol)]
    while pendientes:
        nodo, bajo_guard = pendientes.pop()
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(nodo, ast.If) and _es_guard_main(nodo.test):
            bajo_guard = True
        if isinstance(nodo, ast.Call) and not bajo_guard:
            nombre = None
            if isinstance(nodo.func, ast.Name):
                nombre = nodo.func.id
            elif isinstance(nodo.func, ast.Attribute):
                nombre = nodo.func.attr
            if nombre in nombres:
                fuera.append((nodo.lineno, nombre))
        pendientes.extend((hijo, bajo_guard)
                          for hijo in ast.iter_child_nodes(nodo))
    return sorted(fuera)


# las tres funciones del arranque de main/main.py: si se ejecutan al importar
# (fuera del guard), el modulo entra en la memoria, pide el microfono y se
# mete en el bucle principal justo en medio de quien lo importa
ARRANQUE_MAIN = ("cargar_memoria", "pedir_nombre", "bucle_principal")


class TestB8ImportaSinMicrofono(unittest.TestCase):
    """B8: el modulo tiene que importarse sin pedir microfono ni entrar al bucle"""

    def test_el_arranque_esta_debajo_del_guard(self):
        """B8 por AST: memoria, nombre y bucle SOLO corren bajo el guard

        Es la medida DIRECTA del bug: carga de memoria, peticion de nombre y
        bucle_principal tienen que estar dentro de if __name__ == "__main__":.
        Por AST es instantaneo e inmune a la velocidad de la maquina
        (fundamento 28): no importa cuantos segundos tarde el import de
        anthropic, esta prueba no mira el reloj.
        """
        fuera = _arranque_fuera_del_guard(_arbol(), ARRANQUE_MAIN)
        self.assertEqual(
            fuera, [],
            "hay llamadas al arranque FUERA del guard, que correrian al "
            "importar el modulo: lineas %s" % fuera)

    def test_importa_sin_microfono(self):
        """importar main.main termina y no se cuelga (hasta 45 s)

        Esta es la mitad de "NO REVIENTA", no la del guard: el guard se
        comprueba arriba, por AST. Aqui se importa el modulo en un
        subproceso con dobles de audio y se comprueba que TERMINA.

        COMO SE RESUELVE EL ModuleNotFoundError (indicacion de la tarea):
        si al importar faltara alguna libreria pip del proyecto, el proceso
        muriera con ModuleNotFoundError y la prueba NO estaria midiendo el
        bug. Por eso:
          (a) el apoyo instala dobles para las librerias de audio que faltan
              en la maquina (edge_tts, playsound, pywhatkit, sounddevice);
          (b) AUN ASI se tolera el ModuleNotFoundError de esas mismas
              librerias (LIBRERIAS_EXTERNAS): falta de libreria NO es el bug;

        POR QUE 45 s Y NO 10: medido en esta maquina, importar el modulo en
        frio tardo hasta 21.7 s (de los cuales 13.6 s son `import anthropic`
        solo). Con 10 s la prueba fallaba por la VELOCIDAD del disco, no por
        el bug: eso era una bomba de relojeria (fundamento 28). Si aun asi
        no termina en 45 s, el mensaje dice DONDE se quedo (marcador
        IMPORTANDO vs IMPORT_OK) y NO acusa al guard, que se mide aparte.
        """
        codigo, salida, error = _importar_en_subproceso()

        if codigo is None:
            self.fail(
                "el modulo NO TERMINO de importar en %d s (se corto el "
                "subproceso). Lo ultimo que alcanzo a decir: stdout=%r "
                "stderr=%r. %s. Que el arranque este bajo el guard NO se "
                "decide aqui: eso lo mide test_el_arranque_esta_debajo_del_guard"
                % (TIEMPO_SUBPROCESO, salida, error[-400:],
                   _punto_del_corte(salida)))

        if codigo != 0:
            if _falta_de_libreria_propia(error):
                return  # falta de libreria pip: no es el bug medido aqui
            self.fail(
                "importar main.main termino con codigo %d en vez de terminar "
                "limpio:\n%s" % (codigo, error[-800:]))

        self.assertIn(
            "IMPORT_OK", salida,
            "el modulo no termino de importar (no imprimio IMPORT_OK): "
            "stdout=%r stderr=%r" % (salida, error[-400:]))


class TestB3Interrupcion(unittest.TestCase):
    """B3: los except sin tipo se comen Ctrl+C; tienen que dejarlo pasar"""

    def test_interrupcion_no_se_come(self):
        """KeyboardInterrupt tiene que propagarse en el bucle y al pedir el nombre

        Se prueba con mocks: nunca se abre el microfono ni se conecta a
        internet. recognize_google siempre lanza KeyboardInterrupt, que es lo
        que pasa cuando el usuario aprieta Ctrl+C.
        """
        main = _main()

        # --- (i) bucle de activacion ---
        # si el except sin tipo se come el Ctrl+C, el while vuelve a llamar a
        # sd.rec y ahi el doble revienta con un AssertionError que la prueba
        # detecta: el bucle NO puede seguir tras una interrupcion.
        contador = {"grabaciones": 0}

        def _rec_falso(*argumentos, **opciones):
            contador["grabaciones"] += 1
            if contador["grabaciones"] > 1:
                raise AssertionError(
                    "el bucle volvio a grabar tras un KeyboardInterrupt: "
                    "se lo comio el except sin tipo")
            return np.zeros((100, 1), dtype="int16")

        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.side_effect = KeyboardInterrupt

        with mock.patch.object(main.sd, "rec", mock.MagicMock(side_effect=_rec_falso)), \
                mock.patch.object(main.sd, "wait", mock.MagicMock()), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", mock.MagicMock()):
            with self.subTest(bloque="bucle_principal"):
                with self.assertRaises(KeyboardInterrupt):
                    main.bucle_principal()

        # --- (ii) primer arranque: pedir el nombre del usuario ---
        reconocedor_2 = mock.MagicMock()
        reconocedor_2.recognize_google.side_effect = KeyboardInterrupt
        memoria_falsa = {"nombre_usuario": None, "notas": []}

        with mock.patch.object(main, "r", reconocedor_2), \
                mock.patch.object(main, "hablar", mock.MagicMock()), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()):
            with self.subTest(bloque="pedir_nombre"):
                with self.assertRaises(KeyboardInterrupt):
                    main.pedir_nombre(memoria_falsa)


class TestClienteIAPerezoso(unittest.TestCase):
    """el cliente de Anthropic no debe crearse al importar el modulo"""

    def test_no_crea_cliente_ia_al_importar(self):
        """importar main.main no puede construir el cliente de la IA

        COMO SE MIDE: el apoyo de subproceso acepta el modo "doble_ia", que
        instala un doble de anthropic cuyo constructor REVIENTA. Si el
        modulo construye el cliente al importar (como hacia la linea 49),
        el subproceso muere y no imprime IMPORT_OK.

        POR QUE NO BASTA "importar con la clave en None" (lo que pedia la
        tarea): en esta maquina anthropic 1.5.0 NO valida la clave cuando
        se construye el cliente, ni siquiera con "banana". `Anthropic
        (api_key=None)` tampoco revienta. Asi que una prueba que solo
        importara sin clave PASARIA TAMBIEN con el defecto presente y no
        delataria nada. El doble que revienta si es una medida del bug.
        """
        codigo, salida, error = _importar_en_subproceso(modos=("doble_ia",))

        if codigo is None:
            self.fail(
                "el modulo NO TERMINO de importar en %d s: stdout=%r "
                "stderr=%r" % (TIEMPO_SUBPROCESO, salida, error[-400:]))

        if codigo != 0:
            if _falta_de_libreria_propia(error):
                return  # falta de libreria pip: no es el bug medido aqui
            self.fail(
                "importar main.main construyo el cliente de la IA (o fallo "
                "por otro motivo) con codigo %d:\n%s" % (codigo, error[-800:]))

        self.assertIn(
            "IMPORT_OK", salida,
            "el modulo no termino de importar: stdout=%r stderr=%r"
            % (salida, error[-400:]))


class TestB4RutaMemoria(unittest.TestCase):
    """B4: la ruta de memoria.json no puede depender del directorio de trabajo"""

    def test_carga_la_memoria_real_aunque_cambie_el_directorio(self):
        """cargar_memoria() lee el memoria.json de la RAIZ del proyecto

        Se comprueba cambiando a un directorio temporal (se restaura en
        finally): con la ruta relativa del defecto, Jarvis arrancaria en una
        carpeta vacia, devolveria la memoria por defecto y CREARIA un
        memoria.json nuevo, perdiendo el nombre de Edward y sus notas.
        La prueba SOLO LEE el memoria.json real; nunca lo escribe.
        """
        main = _main()
        with open(RUTA_MEMORIA, "r", encoding="utf-8") as archivo:
            real = json.load(archivo)

        with tempfile.TemporaryDirectory() as tmp:
            cwd_antes = os.getcwd()
            try:
                os.chdir(tmp)
                datos = main.cargar_memoria()
            finally:
                os.chdir(cwd_antes)

            self.assertEqual(
                datos, real,
                "cargar_memoria() desde otro directorio no devolvio la "
                "memoria real: devolvio %r" % (datos,))
            self.assertFalse(
                os.path.exists(os.path.join(tmp, "memoria.json")),
                "cargar_memoria() creo un memoria.json NUEVO en el "
                "directorio de trabajo, en vez de leer el de la raiz")


class TestB9MemoriaSegura(unittest.TestCase):
    """B9: la memoria tiene que sobrevivir a un corte y a un json corrupto"""

    def test_escritura_atomica_no_trunca_ni_deja_temporal(self):
        """guardar_memoria() escribe a un temporal y luego usa os.replace

        Se simula un corte de corriente parchando json.dump para que escriba
        media linea y luego revienta. Con la escritura directa del defecto,
        open(archivo, "w") TRUNCA el archivo ANTES de escribir, asi que el
        memoria.json quedaria ilegible y se perderian el nombre y las notas
        de Edward. Ademas no puede quedar ningun temporal huerfano.
        SIEMPRE se escribe en un directorio temporal: el memoria.json real
        de Edward no se toca.
        """
        main = _main()
        buenos = {"nombre_usuario": "Prueba", "notas": ["nota buena"]}

        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "memoria.json")
            with mock.patch.object(main, "archivo_memoria", ruta):
                main.guardar_memoria(buenos)
                self.assertEqual(
                    sorted(os.listdir(tmp)), ["memoria.json"],
                    "tras un guardado normal quedaron archivos huerfanos: %s"
                    % sorted(os.listdir(tmp)))

                def _dump_que_se_corta(datos, archivo, **opciones):
                    archivo.write('{"cortado": ')
                    raise OSError("corte de corriente simulado")

                with mock.patch.object(main.json, "dump", _dump_que_se_corta):
                    with self.assertRaises(OSError):
                        main.guardar_memoria(
                            {"nombre_usuario": "Nuevo", "notas": []})

                self.assertEqual(
                    sorted(os.listdir(tmp)), ["memoria.json"],
                    "tras el corte quedo un temporal huerfano: %s"
                    % sorted(os.listdir(tmp)))
                try:
                    with open(ruta, "r", encoding="utf-8") as archivo:
                        leido = json.load(archivo)
                except Exception as exc:
                    self.fail(
                        "el memoria.json quedo ILEGIBLE tras el corte: %s"
                        % exc)
                self.assertEqual(
                    leido, buenos,
                    "el memoria.json perdio los datos buenos tras el corte: %r"
                    % (leido,))

    def test_json_corrupto_da_los_valores_por_defecto(self):
        """cargar_memoria() no puede matar el arranque con un json corrupto

        Un memoria.json a medias (por un corte, un disco lleno o una mano
        torpe) tiene que devolver los valores por defecto, no lanzar.
        """
        main = _main()
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "memoria.json")
            with open(ruta, "w", encoding="utf-8") as archivo:
                archivo.write('{"nombre_usuario": "Yohan", "notas": [trunca')
            with mock.patch.object(main, "archivo_memoria", ruta):
                try:
                    datos = main.cargar_memoria()
                except Exception as exc:
                    self.fail(
                        "cargar_memoria() lanzo %s con un memoria.json "
                        "corrupto: %s" % (type(exc).__name__, exc))

        self.assertEqual(
            datos, {"nombre_usuario": None, "notas": []},
            "con un memoria.json corrupto no devolvio los valores por "
            "defecto: devolvio %r" % (datos,))


class TestB1Hora(unittest.TestCase):
    """B1: la hora y el momento del dia se calculan al USARLAS, no al importar"""

    # dos instantes distintos, fijados por la prueba: nada de lo que sigue
    # depende de la hora real del sistema (fundamento 28)
    INSTANTE_MANANA = datetime.datetime(2026, 1, 1, 9, 30)
    INSTANTE_NOCHE = datetime.datetime(2026, 1, 1, 21, 45)

    def test_momento_del_dia_es_funcion_pura(self):
        """momento_del_dia(ahora) devuelve el momento del dia de esa hora

        El reloj se INYECTA: la funcion recibe el instante como argumento y
        nunca llama a datetime.now() ella sola, asi que la prueba no depende
        de la hora real del sistema (fundamento 28). Ademas no basta con que
        la funcion exista y sea pura: tienen que USARLA los dos sitios que
        dicen la hora.
        """
        main = _main()
        if not hasattr(main, "momento_del_dia"):
            self.fail(
                "no existe la funcion momento_del_dia(ahora): la hora se "
                "calcula una sola vez al importar y queda congelada")

        casos = (
            (datetime.datetime(2026, 1, 1, 7, 0), "mañana"),
            (datetime.datetime(2026, 1, 1, 13, 0), "tarde"),
            (datetime.datetime(2026, 1, 1, 20, 0), "noche"),
        )
        for instante, esperado in casos:
            with self.subTest(instante="%02d:00" % instante.hour):
                self.assertEqual(
                    main.momento_del_dia(instante), esperado,
                    "momento_del_dia(%02d:00) devolvio %r y no %r"
                    % (instante.hour, main.momento_del_dia(instante), esperado))

        # y los dos consumidores tienen que llamarla: si no, la funcion seria
        # un adorno puro y la hora diria "de la mañana" a medianoche. Los
        # consumidores son bucle_principal (el saludo de buenos dias) y
        # ejecutar_accion (la accion "hora" que dispara procesar_orden).
        arbol = _arbol()
        for consumidor in ("ejecutar_accion", "bucle_principal"):
            funcion = _funcion(arbol, consumidor)
            self.assertIsNotNone(
                funcion, "no existe la funcion %s" % consumidor)
            llamadas = [nombre for _, nombre in _llamadas_de_funcion(funcion)]
            self.assertIn(
                "momento_del_dia", llamadas,
                "%s nunca llama a momento_del_dia: diria la hora sin decir "
                "en que parte del dia cae" % consumidor)

    def test_sin_hora_congelada_a_nivel_de_modulo(self):
        """ninguna hora puede quedar calculada al importar el modulo

        Se mide por AST y SIN nombrar variables: se busca cualquier lectura
        del reloj (.now()) fuera de toda funcion, con el nombre que tenga la
        variable. Renombrar la constante (por ejemplo _HORA_ARRANQUE) ya no
        la salva. Ademas los dos consumidores tienen que leer el reloj DENTRO
        de la propia funcion, no de una referencia de modulo.
        """
        arbol = _arbol()

        congeladas = ["linea %d" % linea
                      for linea in _lecturas_de_reloj_a_nivel_de_modulo(arbol)]
        self.assertEqual(
            congeladas, [],
            "hay lecturas de reloj fuera de toda funcion, que se evaluan una "
            "sola vez al importar y mentiran despues: lineas %s" % congeladas)

        for consumidor in ("ejecutar_accion", "bucle_principal"):
            funcion = _funcion(arbol, consumidor)
            self.assertIsNotNone(
                funcion, "no existe la funcion %s" % consumidor)
            frescas = _llamadas_de_reloj(funcion)
            self.assertTrue(
                frescas,
                "%s no lee el reloj dentro de la propia funcion: estara "
                "usando una constante de modulo congelada al importar"
                % consumidor)

    def test_la_hora_hablada_sigue_al_reloj_inyectado(self):
        """la hora que dice Jarvis es la del instante INYECTADO, en los cuatro sitios

        COMO SE MIDE: se congelan los DOS relojes por donde hoy pasa la
        hora — el de main.main (main.datetime) y el del motor
        (main.motor.datetime), al que manda la orden TRADUCCION_MOTOR— con
        dos instantes DISTINTOS y se comprueba que cada consumidor dice la
        hora de su instante. Si la hora estuviera guardada en una constante
        de modulo, las frases saldrian con la hora REAL de la maquina y no
        con la inyectada. Los dos instantes se exigen juntos a proposito:
        no pueden coincidir a la vez con la hora real, asi que no hay hora
        del sistema que apruebe la prueba por suerte.

        Cuatro consumidores: bucle_principal (el saludo, que aun calcula la
        hora el mismo) y los TRES que hoy pasan por el MOTOR
        (procesar_orden y ejecutar_accion via TRADUCCION_MOTOR, y
        motor.hora_actual llamado directo). El motor solo dice la HORA
        ("09:30"); el momento del dia lo dice unicamente el saludo, y por
        eso las dos frases del saludo se exigen ademas con "mañana" y
        "noche".
        """
        main = _main()
        consumidores = (
            # nombre, metodo que lo mide, y si ademas dice el momento del dia
            ("bucle_principal", self._saludo_de_bucle_principal, True),
            ("procesar_orden", self._hora_que_dice_procesar_orden, False),
            ("ejecutar_accion", self._hora_que_dice_ejecutar_accion, False),
            ("motor.hora_actual", self._hora_que_dice_directo_al_motor, False),
        )
        for nombre, metodo, dice_momento in consumidores:
            with self.subTest(consumidor=nombre):
                texto_manana = metodo(main, self.INSTANTE_MANANA)
                texto_noche = metodo(main, self.INSTANTE_NOCHE)
                self.assertIn(
                    "09:30", texto_manana,
                    "%s dijo %r en vez de la hora inyectada 09:30"
                    % (nombre, texto_manana))
                self.assertIn(
                    "21:45", texto_noche,
                    "%s dijo %r en vez de la hora inyectada 21:45"
                    % (nombre, texto_noche))
                if dice_momento:
                    self.assertIn(
                        "mañana", texto_manana,
                        "%s dijo %r en vez de 'de la mañana' con el reloj en "
                        "09:30" % (nombre, texto_manana))
                    self.assertIn(
                        "noche", texto_noche,
                        "%s dijo %r en vez de 'de la noche' con el reloj en "
                        "21:45" % (nombre, texto_noche))
                self.assertNotEqual(
                    texto_manana, texto_noche,
                    "%s dijo exactamente lo mismo con dos relojes distintos: "
                    "la hora esta congelada" % nombre)

    def _saludo_de_bucle_principal(self, main, instante):
        """arranca bucle_principal con el reloj congelado en instante y
        devuelve el saludo de buenos dias que dijo Jarvis"""
        reloj = _reloj_congelado(instante)
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = "jarvis despierta"
        hablados = []
        contador = {"grabaciones": 0}

        def _rec_que_se_cansa(*argumentos, **opciones):
            contador["grabaciones"] += 1
            if contador["grabaciones"] > 1:
                # segunda vuelta del bucle: la hora ya se midio, se sale
                raise _FinDeLaPrueba()
            return np.zeros((100, 1), dtype="int16")

        with mock.patch.object(main, "datetime", reloj), \
                mock.patch.object(main.motor, "datetime", reloj), \
                mock.patch.object(main.sd, "rec",
                                  mock.MagicMock(side_effect=_rec_que_se_cansa)), \
                mock.patch.object(main.sd, "wait", mock.MagicMock()), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", hablados.append), \
                mock.patch.object(main, "procesar_orden", mock.MagicMock()):
            with self.assertRaises(_FinDeLaPrueba):
                main.bucle_principal()

        self.assertTrue(
            hablados, "bucle_principal no llego a decir el saludo de buenos dias")
        return hablados[0]

    def _hora_que_dice_procesar_orden(self, main, instante):
        """corre procesar_orden pidiendo la hora con el reloj congelado en
        instante y devuelve la frase con la hora que dijo Jarvis

        El reloj se congela en los DOS sitios donde se calcula: main.main
        (main.datetime) y el motor (main.motor.datetime), porque la orden
        "hora" pasa por TRADUCCION_MOTOR y el motor calcula la hora con SU
        propio datetime.
        """
        reloj = _reloj_congelado(instante)
        reconocedor = mock.MagicMock()
        reconocedor.recognize_google.return_value = "hora"
        hablados = []

        # time.time() se dobla para que el bucle de espera de voz termine ya:
        # la primera llamada fija el inicio y la segunda lo saca del todo
        reloj_de_espera = mock.MagicMock()
        reloj_de_espera.time.side_effect = [0.0, 100.0]

        # FASE G3: procesar_orden delega en atender; se fuerza el CAMINO
        # SIN CLAVE (el de siempre: interpretar -> motor) y asi esta
        # prueba jamas puede hacer una llamada real a la API tenga o no
        # tenga clave el .env de la maquina
        with mock.patch.object(main, "datetime", reloj), \
                mock.patch.object(main.motor, "datetime", reloj), \
                mock.patch.object(main, "time", reloj_de_espera), \
                mock.patch.object(main, "anthropic_api_key", None), \
                mock.patch.object(
                    main, "grabar_hasta_silencio",
                    mock.MagicMock(return_value=np.zeros(10, dtype="int16"))), \
                mock.patch.object(main.wave, "open", mock.MagicMock()), \
                mock.patch.object(main.sr, "AudioFile", mock.MagicMock()), \
                mock.patch.object(main, "r", reconocedor), \
                mock.patch.object(main, "hablar", hablados.append):
            main.procesar_orden()

        self.assertTrue(
            hablados, "procesar_orden no llego a decir la hora")
        return hablados[0]

    def _hora_que_dice_ejecutar_accion(self, main, instante):
        """llama ejecutar_accion con la orden "hora" (que el traductor
        TRADUCCION_MOTOR manda al motor) con el reloj congelado en instante
        y devuelve la frase con la hora que dijo Jarvis"""
        reloj = _reloj_congelado(instante)
        hablados = []
        with mock.patch.object(main, "datetime", reloj), \
                mock.patch.object(main.motor, "datetime", reloj), \
                mock.patch.object(main, "hablar", hablados.append):
            main.ejecutar_accion(main.Accion("hora", ()))
        self.assertTrue(
            hablados, "ejecutar_accion no llego a decir la hora")
        return hablados[0]

    def _hora_que_dice_directo_al_motor(self, main, instante):
        """llama motor.hora_actual con el reloj del motor congelado en
        instante y devuelve el detalle del Resultado, que es la hora que
        dijo el motor (el motor nunca habla: solo devuelve el texto)"""
        reloj = _reloj_congelado(instante)
        with mock.patch.object(main.motor, "datetime", reloj):
            resultado = main.motor.hora_actual()
        self.assertTrue(
            resultado.ok, "motor.hora_actual fallo: " + resultado.detalle)
        return resultado.detalle


if __name__ == "__main__":
    unittest.main()
