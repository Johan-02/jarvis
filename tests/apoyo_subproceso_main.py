# apoyo de la FASE B: importa main/main.py en un SUBPROCESO.
#
# Uso:
#   python apoyo_subproceso_main.py <raiz_del_proyecto> [sin_clave] [doble_ia]
#
# modos:
#   sin_clave : no se lee el .env real ni la variable ANTHROPIC_API_KEY
#   doble_ia  : el doble de anthropic REVIENTA si alguien construye el
#               cliente de IA, delata un cliente creado al importar
#
# Imprime IMPORT_OK en stdout y sale con codigo 0 solo si importar el modulo
# TERMINA. Si main/main.py ejecuta su codigo de arranque o su bucle principal
# al importar, el subproceso se cuelga y el proceso de pruebas lo detecta con
# el timeout.
#
# Por que hay dobles: en la maquina de pruebas faltan librerias de audio
# (edge_tts, playsound, pywhatkit) y NO se quiere tocar el microfono real.
# Los dobles solo cubren esas librerias; el resto del codigo es el real.
#
# El doble de sounddevice es el truco: rec() devuelve silencio y wait() se
# queda esperando 1 hora, igual que un microfono que nunca termina. Cualquier
# codigo que llame a grabar AL IMPORTAR se cuelga ahi y la prueba lo detecta
# con su timeout holgado (el guard en si se comprueba por AST, en
# test_fase_b_main.TestB8ImportaSinMicrofono.test_el_arranque_esta_debajo_del_guard).

import importlib
import os
import sys
import time
import types


def _doble(nombre, **atributos):
    """crea un modulo falso y lo registra en sys.modules"""
    modulo = types.ModuleType(nombre)
    for clave, valor in atributos.items():
        setattr(modulo, clave, valor)
    sys.modules[nombre] = modulo
    return modulo


def _instalar_dobles(modos):
    """doble de las librerias externas que no son el objeto de la prueba

    modos es el conjunto de palabras que siguen a la raiz en la linea de
    comando: "sin_clave" y "doble_ia".
    """
    # numpy hace falta dentro del doble de sounddevice
    import numpy as np

    # texto a voz de Microsoft
    class _Communicate:
        def __init__(self, *argumentos, **opciones):
            pass

        async def save(self, ruta):
            with open(ruta, "wb") as archivo:
                archivo.write(b"")

    _doble("edge_tts", Communicate=_Communicate)

    # reproduccion de audio
    _doble("playsound", playsound=lambda ruta: None)

    # comando "reproduce ..." en YouTube
    _doble("pywhatkit", playonyt=lambda busqueda: None)

    # microfono: rec() devuelve silencio y wait() se queda esperando para
    # siempre. Un modulo sin el guard principal se cuelga aqui mismo.
    def _rec(*argumentos, **opciones):
        return np.zeros((100, 1), dtype="int16")

    def _wait():
        time.sleep(3600)

    _doble("sounddevice", rec=_rec, wait=_wait)

    if "sin_clave" in modos:
        # modo sin clave: el .env real NO se lee, para no usar la clave de
        # Edward dentro de la prueba, y se borra la variable de entorno
        _doble("dotenv", load_dotenv=lambda *argumentos, **opciones: None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    if "doble_ia" in modos:
        # modo doble_ia: doble de anthropic cuyo constructor REVIENTA. Delata
        # un cliente de IA creado AL IMPORTAR el modulo, que es el defecto
        # medido por test_no_crea_cliente_ia_al_importar.
        class _AnthropicQueRevienta:
            def __init__(self, *argumentos, **opciones):
                raise AssertionError(
                    "main/main.py creo el cliente de IA AL IMPORTAR el modulo")

        _doble("anthropic", Anthropic=_AnthropicQueRevienta)


def main():
    if len(sys.argv) < 2:
        print("uso: apoyo_subproceso_main.py <raiz> [sin_clave] [doble_ia]",
              file=sys.stderr)
        return 2
    raiz = sys.argv[1]
    modos = set(sys.argv[2:])
    sys.path.insert(0, raiz)
    _instalar_dobles(modos)
    # el marcador con flush permite decir la VERDAD si el proceso se corta:
    # IMPORTANDO sin IMPORT_OK significa que main/main.py no termino de
    # importar (y NO que falte el guard, que se comprueba por AST aparte)
    print("IMPORTANDO", flush=True)
    importlib.import_module("main.main")
    print("IMPORT_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
