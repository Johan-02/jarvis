"""generador de las herramientas de la API del modelo (FASE G1).

TODA la informacion sale de motor.REGISTRO y de la firma de sus
funciones: este modulo NO tiene ningun catalogo propio. Quien agregue una
accion nueva a REGISTRO obtiene su herramienta SOLA, sin tocar este
archivo. Lo comprueba la prueba de automatismo de
tests/test_herramientas.py, que registra una accion FALSA en una copia
temporal del proyecto y exige ver su herramienta ahi, con
main/herramientas.py IDENTICO al original.

LA REGLA DE SEGURIDAD: el modelo NUNCA puede pasar `confirmado`. Ese
parametro NO aparece en NINGUN input_schema, porque el freno de las
destructivas vive en motor.ejecutar y lo controla EL PROGRAMA, no el
modelo: si el modelo pudiera escribir confirmado=True se saltaria la
confirmacion y apagaria la PC sin preguntar. Si alguna accion de REGISTRO
llegara a declarar ese parametro, su esquema lo expondria y la prueba
tests/test_herramientas.py (que recorre TODAS las herramientas, sin
lista escrita a mano) REVIENTA antes de que eso llegue a produccion. La
garantia es ESA PRUEBA, no un filtro silencioso aqui dentro: un filtro
ocultaria el problema en vez de denunciarlo.
"""

import inspect

from main import motor

# aviso que se ANTEPONE a la description de toda accion destructiva, para
# que el modelo lea el peligro ANTES de decidir llamarla
AVISO_DESTRUCTIVA = ("ACCION DESTRUCTIVA: requiere confirmacion explicita "
                     "del usuario antes de ejecutarla.")

# tipos JSON Schema que salen de las ANOTACIONES de la firma (si no hay
# anotacion, el tipo viene del valor por defecto; ver _tipo_de)
_ANOTACIONES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    tuple: "array",
    dict: "object",
}

# los mismos tipos cuando la anotacion llega como TEXTO (PEP 563,
# `from __future__ import annotations`): sin esto la anotacion "int" no
# casaria con la clave int y el parametro saldria sin tipo
_ANOTACIONES_TEXTO = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "tuple": "array",
    "dict": "object",
}


def _primera_frase(docstring):
    """primera frase completa del docstring, colapsada a UNA sola linea

    El corte va en el primer punto (o ! o ?) seguido de espacio o del fin
    del texto: un punto DENTRO de un token no corta ("user32.SendInput",
    "0.5", "shutdown /s /t."). Un docstring sin ninguna frase cerrada
    sale entero. Los saltos de linea se colapsan a espacios, asi que la
    description nunca trae \\n.
    """
    texto = " ".join((docstring or "").split())
    for posicion, caracter in enumerate(texto):
        if caracter in ".!?":
            if posicion + 1 == len(texto) or texto[posicion + 1].isspace():
                return texto[:posicion + 1]
    return texto


def _descripcion(nombre, funcion):
    """description de la herramienta: la PRIMERA frase del docstring

    Si la accion es destructiva se ANTEPONE AVISO_DESTRUCTIVA: el modelo
    tiene que saberlo antes de decidir si la llama.
    """
    frase = _primera_frase(funcion.__doc__)
    if nombre in motor.DESTRUCTIVAS:
        return AVISO_DESTRUCTIVA + " " + frase
    return frase


def _tipo_de(parametro):
    """tipo JSON Schema de un parametro, o None si la firma no lo dice

    Orden: anotacion -> valor por defecto -> nada. Un None como defecto se
    tipa "string" (raiz=None es texto opcional y NO va en required).
    """
    anotacion = parametro.annotation
    if anotacion is not inspect.Parameter.empty:
        if isinstance(anotacion, str):
            return _ANOTACIONES_TEXTO.get(anotacion)
        return _ANOTACIONES.get(anotacion)
    if parametro.default is inspect.Parameter.empty:
        # sin anotacion ni defecto la firma NO dice el tipo, y AQUI NO SE
        # ADIVINA: inventar "string" mentiria para brillo(nivel) ni para
        # mover_raton(x, y), que son numeros. La propiedad sale SIN
        # "type" (JSON Schema lo admite) y el tipo lo resuelve el modelo
        # con la description de la propia accion, que es quien dice
        # "(0 a 100)" o "(x, y) de la pantalla"
        return None
    defecto = parametro.default
    if defecto is None:
        return "string"
    # bool ANTES que int: isinstance(True, int) es True y True no es "integer"
    if isinstance(defecto, bool):
        return "boolean"
    if isinstance(defecto, int):
        return "integer"
    if isinstance(defecto, float):
        return "number"
    if isinstance(defecto, str):
        return "string"
    if isinstance(defecto, (list, tuple)):
        return "array"
    if isinstance(defecto, dict):
        return "object"
    return None


def _esquema_de(funcion):
    """input_schema JSON Schema de UNA accion, generado SOLO de su firma

    Los parametros SIN defecto van en required; los opcionales NO. Un
    *args/**kwargs no se declara: JSON Schema no lo admite (hoy ninguna
    accion de REGISTRO lo trae).
    """
    propiedades = {}
    obligatorios = []
    for nombre, parametro in inspect.signature(funcion).parameters.items():
        if parametro.kind in (inspect.Parameter.VAR_POSITIONAL,
                              inspect.Parameter.VAR_KEYWORD):
            continue
        propiedad = {}
        tipo = _tipo_de(parametro)
        if tipo is not None:
            propiedad["type"] = tipo
        propiedades[nombre] = propiedad
        if parametro.default is inspect.Parameter.empty:
            obligatorios.append(nombre)
    esquema = {"type": "object", "properties": propiedades}
    if obligatorios:
        esquema["required"] = obligatorios
    return esquema


def herramientas():
    """definiciones de TODAS las herramientas, ordenadas por nombre

    Se recorre motor.REGISTRO EN LA LLAMADA (no una copia congelada): una
    accion agregada al registro aparece aqui sola, y el orden por nombre
    hace que la salida sea determinista para las pruebas.
    """
    definiciones = []
    for nombre in sorted(motor.REGISTRO):
        funcion = motor.REGISTRO[nombre]
        definiciones.append({
            "name": nombre,
            "description": _descripcion(nombre, funcion),
            "input_schema": _esquema_de(funcion),
        })
    return definiciones


def es_destructiva(nombre):
    """True si esa accion exige confirmacion (sigue a motor.DESTRUCTIVAS)"""
    return nombre in motor.DESTRUCTIVAS


def accion_de_herramienta(nombre):
    """nombre del REGISTRO que ejecuta esa herramienta, o None si no existe

    Hoy el nombre de la herramienta ES el nombre de la accion, y este
    traductor es el UNICO punto por el que pasa la traduccion: si un dia
    los nombres se separan (un prefijo, por ejemplo), solo hay que
    cambiarlo AQUI y en ninguna otra parte.
    """
    if nombre in motor.REGISTRO:
        return nombre
    return None
