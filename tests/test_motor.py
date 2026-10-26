# pruebas del motor del asistente Jarvis (FASE E1, 2026-10-20)
#
# El CORAZON de este archivo es test_toda_accion_del_registro_esta_clasificada:
# PARAMETRIZADA sobre REGISTRO, recorre sus claves en TIEMPO DE CORRIDA (no
# una lista de nombres congelada) y exige que CADA accion este CLASIFICADA:
#   - o esta en la lista blanca INOCUAS declarada AQUI abajo, o
#   - esta en DESTRUCTIVAS y ejecutar(a, confirmado=False) devuelve
#     REQUIERE_CONFIRMACION sin ejecutar absolutamente nada.
# Quien agregue una accion nueva y se olvide de clasificarla ROMPE la
# prueba. Ese es el valor: no hay forma de agregar poder al motor en
# silencio.
#
# REGLAS DE ESTE ARCHIVO:
#   - NUNCA se ejecuta nada destructivo de verdad: las acciones de DESTRUCTIVAS
#     se llaman con ctypes/subprocess/os del motor REEMPLAZADOS por dobles
#     que SOLO registran llamadas. Ni apagados, ni reinicios, ni papelera real.
#   - Cero audio, cero IA: main/motor.py se comprueba por AST, sin importarlo
#     contra el sistema.
#   - hora_actual depende del reloj, pero la prueba NUNCA afirma la hora
#     exacta (fundamento 28): solo el FORMATO.
#
# LAS DOS MITADES de esta suite (2026-10-20): con el codigo del proyecto,
# pasa; en copia temporal con una accion destructiva FALSA (apagar_pc)
# agregada a REGISTRO sin clasificar, la parametrizada SE ROMPE con
# nombre='apagar_pc'. La salida de la mitad mala quedo pegada en el reporte
# de la FASE E1.

import ast
import contextlib
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

from main import motor
from main.reconocedor import Accion

RUTA_MOTOR = RAIZ / "main" / "motor.py"

# LISTA BLANCA EXPLICITA de acciones inocuas: las UNICAS que ejecutar() puede
# correr sin confirmacion. No es una lista congelada contra el registro: la
# prueba recorre REGISTRO y exige que cada clave este aqui o en DESTRUCTIVAS.
# Inocua = no borra, no apaga, no mata procesos y no destruye nada: lee
# (hora, volumen, disco, CPU, RAM, bateria, procesos y peso de carpetas),
# mueve un nivel (pasos de volumen y brillo), silencia,
# bloquea la estacion (no pierde trabajo), deshace un apagado YA mandado,
# ESCRIBE donde esta el cursor sin borrar nada (portapapeles + Ctrl+V,
# combinaciones de teclas, movimiento y clic del raton) o
# PIDE al sistema abrir/cerrar algo que puede negarse (archivo, app, url,
# ventana por WM_CLOSE, busqueda, captura). FASE E2e agrega: LEER la red y
# la papelera (wifi_estado, bluetooth_estado, descargar, leer_json,
# abrir_web y papelera_ver) y los dos interruptores wifi/bluetooth, que se
# niegan sin tocar nada (siempre ok=False, cero llamadas al sistema).
# Lo que fuerza el cierre con
# taskkill /F es cerrar_app_forzado, y lo que apaga, reinicia o suspende la
# maquina (apagar, reiniciar, suspender) o borra archivos de verdad
# (borrar_archivo, que usa os.remove y NO pasa por la papelera) va en
# DESTRUCTIVAS.
INOCUAS = (
    "volumen_leer",
    "hora_actual",
    "abrir_archivo",
    "abrir_app",
    "abrir_url",
    "cerrar_app",
    "buscar_archivo",
    # FASE E2c, GRUPO 2: el equipo
    "volumen_subir",
    "volumen_bajar",
    "silenciar",
    "brillo",
    "cancelar_apagado",
    "bloquear",
    "capturar_pantalla",
    # FASE H2: solo LEE la pantalla y deja un PNG reducido en el temp
    "mirar_pantalla",
    # FASE E2d, GRUPOS 3 y 4: escribir y teclar, raton e informacion
    "escribir_texto",
    "mandar_teclas",
    "mover_raton",
    "clic",
    "espacio_disco",
    "cpu_y_ram",
    "bateria",
    "procesos",
    "peso_de",
    # FASE E2e, GRUPO 5: red y descargas, mas papelera SOLO LEE. wifi y
    # bluetooth (los interruptores) estan aqui porque se NIEGAN: nunca
    # encienden ni apagan nada y siempre devuelven ok=False
    "wifi_estado",
    "wifi",
    "bluetooth_estado",
    "bluetooth",
    "descargar",
    "leer_json",
    "abrir_web",
    "papelera_ver",
    # FASE H3: memoria que aprende. recordar AGREGA una nota (no destruye
    # nada de lo que habia) y leer_memoria SOLO LEE; olvidar SI destruye (una
    # nota que Edward dijo) y por eso esta en DESTRUCTIVAS, no aqui
    "recordar",
    "leer_memoria",
)

# modulos externos (NO stdlib, NO del proyecto) que el motor SI puede
# importar. pywin32 esta instalada en esta PC (medido el 2026-10-21:
# pywin32==312): win32gui cierra ventanas con WM_CLOSE y win32api mueve y
# pulsa el raton. No son de audio, de IA ni de red: el motor sigue
# importandose sin microfono, sin internet y sin clave de API, que es lo que
# test_no_importa_audio_ni_ia protege.
EXTERNOS_PERMITIDOS = {"win32api", "win32gui"}

# librerias pip de audio e IA que main/motor.py NO puede importar (el motor
# tiene que importarse sin microfono, sin red y sin clave de API)
LIBRERIAS_PROHIBIDAS = {
    "anthropic", "speech_recognition", "sounddevice", "edge_tts",
    "playsound", "pywhatkit",
}

# ademas de las librerias, estos NOMBRES son de ejecucion/habla: el motor
# devuelve texto, no habla ni imprime
NOMBRES_PROHIBIDOS = LIBRERIAS_PROHIBIDAS | {"hablar", "preguntar_a_ia", "print"}


@contextlib.contextmanager
def _solo_registra():
    """reemplaza lo que toca el sistema del motor por dobles SOLO de registro

    Dentro de esta ventana NADA puede llegar al sistema real: si el freno de
    una accion destructiva fallara, la llamada cae en el doble y la prueba
    lo ve en vez de apagar, reiniciar, vaciar la papelera, matar procesos o
    mandar WM_CLOSE a una ventana de VERDAD. Los nombres de abajo se parchan
    sobre el MODULO motor: por eso mismo main/motor.py tiene prohibido
    importar dentro de funciones (test_los_imports_estan_a_nivel_de_modulo) y
    toda referencia a ctypes/subprocess/os/win32api/win32gui pasa por un
    global del modulo, que es el que aqui se reemplaza.
    """
    dobles = {
        "ctypes": mock.MagicMock(name="ctypes_falso"),
        "subprocess": mock.MagicMock(name="subprocess_falso"),
        "os": mock.MagicMock(name="os_falso"),
        "win32api": mock.MagicMock(name="win32api_falso"),
        "win32gui": mock.MagicMock(name="win32gui_falso"),
        "winreg": mock.MagicMock(name="winreg_falso"),
        "shutil": mock.MagicMock(name="shutil_falso"),
        "webbrowser": mock.MagicMock(name="webbrowser_falso"),
    }
    with contextlib.ExitStack() as pila:
        for nombre, doble in dobles.items():
            pila.enter_context(
                mock.patch.object(motor, nombre, doble, create=True))
        yield dobles


def _llamadas_por_sistema(dobles):
    """conteo de llamadas registradas por cada doble, para mensajes claros"""
    return {nombre: list(doble.mock_calls) for nombre, doble in dobles.items()}


class TestClasificacionDeAcciones(unittest.TestCase):
    """toda accion de REGISTRO tiene que estar clasificada: inocua o destructiva"""

    def test_el_registro_y_las_destructivas_no_estan_vacios(self):
        """una suite sobre un registro vacio PASARIA sin probar nada de nada"""
        self.assertTrue(
            motor.REGISTRO,
            "REGISTRO esta vacio: la prueba parametrizada no recorreria "
            "ninguna accion y pasaria sin medir nada")
        self.assertTrue(
            motor.DESTRUCTIVAS,
            "DESTRUCTIVAS esta vacio: el motor no tendria ninguna accion "
            "frenada y la prueba no podria medir la seguridad")

    def test_ni_inocua_ni_destructiva_a_la_vez(self):
        """contradiccion peligrosa: una accion en las DOS listas se ejecuta
        como si fuera inocua, porque la lista blanca se mira primero"""
        repetidas = sorted(set(INOCUAS) & motor.DESTRUCTIVAS)
        self.assertEqual(
            repetidas, [],
            "estas acciones estan en INOCUAS y en DESTRUCTIVAS a la vez "
            "(se ejecutarian sin confirmacion): %s" % repetidas)

    def test_toda_accion_del_registro_esta_clasificada(self):
        """PARAMETRIZADA sobre REGISTRO: sin clasificar, la prueba ROMPE

        Se recorren las claves del registro en TIEMPO DE CORRIDA, asi que
        esta prueba protege tambien contra las acciones de la FASE siguiente:
        una accion nueva sin clasificar no encuentra su nombre en INOCUAS, no
        esta en DESTRUCTIVAS y cae en el fail de abajo.

        La mitad destructiva corre SIEMPRE dentro de _solo_registra(): aunque
        alguien hubiera roto el freno de ejecutar(), la llamada caeria en el
        doble y aqui se veria, sin tocar el sistema real.
        """
        self.assertTrue(
            motor.REGISTRO,
            "REGISTRO vacio: la parametrizada no probaria nada")
        for nombre in sorted(motor.REGISTRO):
            with self.subTest(nombre=nombre):
                if nombre in INOCUAS:
                    # declarada inocua en la lista blanca explicita de arriba
                    continue
                if nombre not in motor.DESTRUCTIVAS:
                    self.fail(
                        "la accion %r esta en REGISTRO pero NO esta "
                        "clasificada: no aparece en la lista blanca INOCUAS "
                        "ni en DESTRUCTIVAS. Sin clasificar no se puede saber "
                        "si ejecutarla sin confirmacion es seguro"
                        % (nombre,))

                # es destructiva: tiene que FRENAR sin confirmar, y sin
                # ejecutar absolutamente nada (cero llamadas a cualquier
                # sistema, aunque el freno estuviera roto)
                with _solo_registra() as dobles:
                    resultado = motor.ejecutar(
                        Accion(nombre), confirmado=False)
                self.assertEqual(
                    resultado.detalle, "REQUIERE_CONFIRMACION",
                    "la accion destructiva %r sin confirmar devolvio %r y "
                    "tenia que devolver REQUIERE_CONFIRMACION"
                    % (nombre, resultado.detalle))
                self.assertFalse(
                    resultado.ok,
                    "la accion destructiva %r sin confirmar marco ok=True"
                    % (nombre,))
                sucias = {sistema: llamadas
                          for sistema, llamadas
                          in _llamadas_por_sistema(dobles).items()
                          if llamadas}
                self.assertEqual(
                    sucias, {},
                    "sin confirmar, la accion %r toco el sistema real por "
                    "estos caminos: %s" % (nombre, sucias))


# argumentos de MUESTRA de cada destructiva para la rama CONFIRMADA: las que
# se pueden llamar sin argumentos (papelera_vaciar, apagar, reiniciar y
# suspender usan su valor por defecto) no llevan muestra y se llaman sin
# argumentos. Sin muestra, una destructiva CON argumentos daria TypeError en
# vez de probar su comportamiento.
MUESTRAS_DESTRUCTIVAS = {"cerrar_app_forzado": ("chrome.exe",),
                         "borrar_archivo": ("archivo/que/no/existe.txt",)}

# FASE H3: destructivas cuyo efecto NO pasa por los modulos del sistema que
# _solo_registra() reemplaza. olvidar escribe memoria.json por el escritor
# atomico que main.py le conecta al motor; aqui, sin main.py importado, el
# motor ni sabe donde esta el archivo (y no debe saberlo: esta prueba JAMAS
# puede tocar el memoria.json de Edward). Su mitad CONFIRMADA se mide en
# tests/test_fase_h3.py contra una COPIA temporal: borra UNA nota y solo una.
# La mitad SIN confirmar SI se comprueba aqui, como la de todas.
DESTRUCTIVAS_SIN_SISTEMA = {"olvidar"}


class TestDestructivasSoloConConfirmacion(unittest.TestCase):
    """las destructivas frenan sin confirmar y SI se ejecutan confirmadas"""

    def test_sin_confirmar_cero_llamadas_y_confirmada_si_llama(self):
        """para CADA destructiva, las DOS mitades del freno, con dobles

        (i)  confirmado=False -> REQUIERE_CONFIRMACION y CERO llamadas, ni
             un subprocess, ni un os., ni un ctypes, ni un win32gui;
        (ii) confirmado=True  -> SI hubo llamada (y el doble devuelve exito,
             para ver que el camino confirmado termina bien).

        Excepcion declarada: DESTRUCTIVAS_SIN_SISTEMA (hoy solo olvidar) no
        pasa por esos modulos, y su mitad confirmada se mide en
        tests/test_fase_h3.py contra una copia temporal. Sin confirmar SI se
        mide aqui, igual que las demas.

        NUNCA se ejecuta la accion de verdad: todo ocurre con
        ctypes/subprocess/os/win32gui reemplazados por dobles de solo
        registro.
        """
        destructivas = sorted(motor.DESTRUCTIVAS)
        self.assertTrue(
            destructivas,
            "DESTRUCTIVAS vacio: la prueba no mediria ninguna destructiva")

        for nombre in destructivas:
            with self.subTest(nombre=nombre, caso="sin_confirmar"):
                with _solo_registra() as dobles:
                    resultado = motor.ejecutar(
                        Accion(nombre), confirmado=False)
                self.assertEqual(
                    resultado.detalle, "REQUIERE_CONFIRMACION",
                    "%r sin confirmar devolvio %r en vez de "
                    "REQUIERE_CONFIRMACION" % (nombre, resultado.detalle))
                sucias = {sistema: llamadas
                          for sistema, llamadas
                          in _llamadas_por_sistema(dobles).items()
                          if llamadas}
                self.assertEqual(
                    sucias, {},
                    "confirmado=False y %r aun asi toco estos sistemas: %s"
                    % (nombre, sucias))

            if nombre in DESTRUCTIVAS_SIN_SISTEMA:
                # su camino confirmado no usa estos modulos: se mide en
                # test_fase_h3.py con un archivo temporal de verdad
                continue

            with self.subTest(nombre=nombre, caso="confirmada"):
                with _solo_registra() as dobles:
                    # la ruta de la papelera necesita que el doble de ctypes
                    # devuelva 0 (exito); una destructiva con otro camino la
                    # recibe igual aqui, con SU muestra de argumentos
                    dobles["ctypes"].windll.shell32 \
                        .SHEmptyRecycleBinW.return_value = 0
                    muestra = MUESTRAS_DESTRUCTIVAS.get(nombre, ())
                    resultado = motor.ejecutar(
                        Accion(nombre, muestra), confirmado=True)
                total = sum(len(llamadas)
                            for llamadas in _llamadas_por_sistema(dobles).values())
                self.assertGreater(
                    total, 0,
                    "confirmada, %r no toco NADA del sistema: o no esta en "
                    "REGISTRO (devolvio ACCION_DESCONOCIDA) o su funcion no "
                    "llamo a ningun sistema" % (nombre,))
                self.assertTrue(
                    resultado.ok,
                    "confirmada, %r termino mal con el doble en exito: %r"
                    % (nombre, resultado.detalle))


class TestMotorEstatico(unittest.TestCase):
    """main/motor.py leido como AST: sin importarlo contra el sistema"""

    @staticmethod
    def _arbol():
        return ast.parse(RUTA_MOTOR.read_text(encoding="utf-8"))

    @staticmethod
    def _imports_y_nombres(arbol):
        importados = set()
        nombres = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                importados.update(
                    alias.name.split(".")[0] for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom):
                if nodo.level == 0 and nodo.module:
                    importados.add(nodo.module.split(".")[0])
            elif isinstance(nodo, ast.Name):
                nombres.add(nodo.id)
            elif isinstance(nodo, ast.Attribute):
                nombres.add(nodo.attr)
        return importados, nombres

    def test_no_importa_audio_ni_ia(self):
        """el motor no importa audio ni IA: se importa en milisegundos

        Se mide por AST (imports y nombres usados), no como texto: un
        COMENTARIO que mencione "anthropic" no ejecuta nada. Ademas solo se
        admiten stdlib, los modulos locales del proyecto (main.*) y los de la
        lista blanca EXPLICITA EXTERNOS_PERMITIDOS (hoy: win32gui, de
        pywin32, que cierra ventanas y no pide ni microfono, ni red, ni clave
        de API): ni una libreria pip mas. Y cero prints: el motor devuelve
        texto, no habla.
        """
        arbol = self._arbol()
        importados, nombres = self._imports_y_nombres(arbol)

        externos = (importados - set(sys.stdlib_module_names)
                    - {"main"} - EXTERNOS_PERMITIDOS)
        self.assertEqual(
            sorted(externos), [],
            "main/motor.py importa librerias que no son stdlib, del "
            "proyecto ni de la lista blanca EXTERNOS_PERMITIDOS: %s"
            % sorted(externos))

        prohibidas = sorted(importados & LIBRERIAS_PROHIBIDAS)
        self.assertEqual(
            prohibidas, [],
            "main/motor.py importa audio o IA: %s" % prohibidas)

        usados = sorted(nombres & NOMBRES_PROHIBIDOS)
        self.assertEqual(
            usados, [],
            "main/motor.py usa nombres de audio, IA o habla: %s" % usados)

    def test_los_imports_estan_a_nivel_de_modulo(self):
        """ningun import puede vivir DENTRO de una funcion del motor

        Motivo de seguridad: los dobles de _solo_registra() reemplazan los
        GLOBALES del modulo (motor.ctypes, motor.subprocess, motor.os). Un
        `import ctypes` hecho dentro de una funcion crearia una referencia
        LOCAL que el doble NO alcanzaria, y una destructiva sin freno tocaria
        el sistema real. Con todos los imports arriba del todo, toda
        referencia a esos modulos pasa por el global parchable.
        """
        arbol = self._arbol()
        padres = {}
        for padre in ast.walk(arbol):
            for hijo in ast.iter_child_nodes(padre):
                padres[hijo] = padre

        dentro = []
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.Import, ast.ImportFrom)):
                continue
            actual = padres.get(nodo)
            while actual is not None:
                if isinstance(actual, (ast.FunctionDef,
                                       ast.AsyncFunctionDef, ast.Lambda)):
                    if isinstance(nodo, ast.Import):
                        que = [alias.name for alias in nodo.names]
                    else:
                        que = [nodo.module]
                    dentro.append((nodo.lineno, que))
                    break
                actual = padres.get(actual)
        self.assertEqual(
            sorted(dentro), [],
            "hay imports DENTRO de funciones en main/motor.py (linea, "
            "modulo): escaparian a los dobles de las pruebas: %s"
            % sorted(dentro))


class TestAccionInocuaEjecutada(unittest.TestCase):
    """las inocuas de la lista blanca ejecutan de verdad y devuelven Resultado"""

    def test_hora_actual_se_ejecuta_y_respeta_el_formato(self):
        """hora_actual via REGISTRO y ejecutar() devuelve HH:MM

        Es la unica inocua que se ejecuta en la suite a proposito:
        volumen_leer toca el dispositivo de audio REAL de la PC (es inocua,
        solo lee, pero depende de que exista un endpoint) y aqui no se quiere
        una prueba que dependa de la maquina. NUNCA se afirma la hora
        EXACTA (fundamento 28): solo el formato, que es estable.
        """
        resultado = motor.ejecutar(Accion("hora_actual"))
        self.assertTrue(
            resultado.ok,
            "hora_actual no devolvio ok: %r" % (resultado.detalle,))
        self.assertRegex(
            resultado.detalle, r"^\d{2}:\d{2}$",
            "hora_actual devolvio %r y tenia que devolver HH:MM"
            % (resultado.detalle,))


if __name__ == "__main__":
    unittest.main()
