# pruebas del bug de TILDES del reconocedor de ordenes (2026-10-26).
#
# LO DESTAPO LA VOZ REAL DE EDWARD, no las pruebas: Google transcribe CON
# TILDES, porque asi es como habla un hispanohablante, y el reconocedor solo
# bajaba a minusculas. Medido ANTES del arreglo, con estas frases exactas:
#   interpretar("cuanta ram")            -> cpu_y_ram      BIEN
#   interpretar("Cu\u00e1nta RAM")       -> consultar_ia   MAL
#   interpretar("cu\u00e1nta ram")       -> consultar_ia   MAL
#   interpretar("sube el volumen")       -> volumen_subir  BIEN
#   interpretar("sube el vol\u00famen")  -> consultar_ia   MAL
# Las 291 pruebas del proyecto no lo veian porque escriben el texto sin
# tildes: en la vida real, muchas ordenes se caian a la IA.
#
# LO QUE VIGILA ESTE ARCHIVO:
#   1. las tildes NO cuentan para CASAR una orden;
#   2. el ARGUMENTO que llega al motor conserva las tildes que dijo el
#      usuario ("reproduce canci\u00f3n de Jos\u00e9" tiene que buscar
#      "canci\u00f3n de jos\u00e9", no "cancion de jose");
#   3. ninguna orden de la tabla ORDENES puede quedar inservible por una
#      tilde, ni escribiendola con tildes ni sin ellas.
#
# OJO con el estilo: los acentos de los DATOS van con escapes \uXXXX para no
# depender de la codificacion del archivo (regla del proyecto).

import os
import sys
import unittest
from pathlib import Path

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from main.reconocedor import interpretar, ORDENES, ORDENES_LIBRES
# _indice_de es el ayudante (privado) que hace el CASADO de palabras. La
# prueba de regresion lo llama para poder ver cada entrada de la tabla por
# separado: con interpretar solo, un duplicado sin tilde tapa el fallo.
from main.reconocedor import _indice_de


# tildes y enie, escritas de las dos maneras. El mapa es EXPLICITO y no usa
# unicodedata a proposito: si la normalizacion del modulo se rompe, estas
# pruebas tienen que seguir sabiendo cual es la frase con tilde y cual sin
# ella (con la misma libreria que el codigo, la prueba no probaria nada).
_CON_TILDE = {
    "\u00e1": "a", "\u00e9": "e", "\u00ed": "i", "\u00f3": "o", "\u00fa": "u",
    "\u00fc": "u", "\u00f1": "n",
    "\u00c1": "a", "\u00c9": "e", "\u00cd": "i", "\u00d3": "o", "\u00da": "u",
    "\u00dc": "u", "\u00d1": "n",
}
_SIN_TILDE = {
    "a": "\u00e1", "e": "\u00e9", "i": "\u00ed", "o": "\u00f3", "u": "\u00fa",
}


def _sin_tildes(texto):
    """la misma frase sin tildes ("Cu\u00e1nta RAM" -> "Cuanta RAM")"""
    return "".join(_CON_TILDE.get(letra, letra) for letra in texto)


def _con_tildes(texto):
    """la misma frase con TODAS las vocales acentuadas ("cuanta" -> "c\u00fa\u00e1nt\u00e1")

    Exagerado a proposito: si la comparacion no quitara las tildes, esta
    frase no casaria con nada. Es la version "al reves" de la regla.
    """
    return "".join(_SIN_TILDE.get(letra, letra) for letra in texto)


class TestTildesNoImportanParaCasar(unittest.TestCase):
    """el bug: una orden conocida se caia a la IA solo por llevar tilde"""

    def test_cuanta_ram_con_tilde_da_lo_mismo_que_sin_tilde(self):
        """el caso EXACTO que destapo la voz de Edward

        Antes del arreglo, "Cu\u00e1nta RAM" devolvia consultar_ia: la orden
        "cuanta ram" existe en ORDENES, pero la frase traia tilde.
        """
        sin_tilde = interpretar("cuanta ram")
        self.assertEqual(
            interpretar("Cu\u00e1nta RAM"), sin_tilde,
            "\"Cu\\u00e1nta RAM\" no da lo mismo que \"cuanta ram\": la tilde "
            "todavia importa para casar la orden")
        self.assertEqual(
            interpretar("cu\u00e1nta ram"), sin_tilde,
            "la tilde en minusculas tambien tiene que dar lo mismo")
        self.assertEqual(
            [accion.nombre for accion in sin_tilde], ["cpu_y_ram"],
            "la frase sin tilde tiene que seguir dando cpu_y_ram: dio %r"
            % (sin_tilde,))

    def test_sube_el_volumen_con_tilde_da_lo_mismo_que_sin_tilde(self):
        """el segundo caso de la voz: "sube el vol\u00famen" se iba a la IA"""
        sin_tilde = interpretar("sube el volumen")
        self.assertEqual(
            interpretar("sube el vol\u00famen"), sin_tilde,
            "\"sube el vol\\u00famen\" no da lo mismo que \"sube el volumen\"")
        self.assertEqual(
            [accion.nombre for accion in sin_tilde], ["volumen_subir"],
            "la frase sin tilde tiene que seguir dando volumen_subir: dio %r"
            % (sin_tilde,))

    def test_que_hora_con_tilde_sigue_dando_hora(self):
        """lo que YA funcionaba no se puede romper: "qu\u00e9 hora es" -> hora

        La orden de la tabla es "hora" (una sola palabra, sin tilde), asi que
        esta frase ya casaba antes del arreglo. Se deja clavada como
        regresion: el arreglo no puede llevarse por delante el caso viejo.
        """
        for texto in ("qu\u00e9 hora es", "que hora es", "dime la hora"):
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones], ["hora"],
                    "%r tiene que seguir dando hora: %r" % (texto, acciones))
                self.assertEqual(acciones[0].argumentos, ())

    def test_orden_de_la_tabla_escrita_con_tilde_funciona(self):
        """las ordenes con tilde EN LA TABLA siguen disparando

        ORDENES trae a proposito 'b\u00fascame', 'p\u00e9galo' y 'mover el
        rat\u00f3n a'. Si la comparacion quitara tildes solo del lado de la
        frase, estas tres dejarian de casar: el lado de la ORDEN tambien se
        normaliza (esta documentado en _indice_de).
        """
        casos = (
            ("b\u00fascame mi factura en documentos", "buscar_archivo",
             ("mi factura", "documentos")),
            ("buscame mi factura en documentos", "buscar_archivo",
             ("mi factura", "documentos")),
            ("p\u00e9galo", "mandar_teclas", ("ctrl+v",)),
            ("pegalo", "mandar_teclas", ("ctrl+v",)),
            ("mover el rat\u00f3n a 100 200", "mover_raton", (100, 200)),
            ("mover el raton a 100 200", "mover_raton", (100, 200)),
        )
        for texto, nombre, argumentos in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones], [nombre],
                    "%r tiene que dar %r: %r" % (texto, nombre, acciones))
                self.assertEqual(
                    acciones[0].argumentos, argumentos,
                    "%r lleva %r y tenia que llevar %r"
                    % (texto, acciones[0].argumentos, argumentos))

    def test_el_argumento_conserva_las_tildes(self):
        """la tilde NO se quita del ARGUMENTO: solo se ignora al comparar

        La orden libre de ORDEN_REPRODUCIR es ("reproduce", "libre"): el
        argumento es todo lo que venga detras. Si al normalizar la frase se
        normalizara tambien lo que se le pasa al motor, se buscaria (o se
        descargaria) otra cosa: "cancion de jose" no es "canci\u00f3n de
        Jos\u00e9".
        """
        acciones = interpretar("reproduce canci\u00f3n de Jos\u00e9")
        self.assertEqual(
            [accion.nombre for accion in acciones], ["reproduce"],
            "\"reproduce canci\\u00f3n de Jos\\u00e9\" no reconocio la orden: %r"
            % (acciones,))
        self.assertEqual(
            acciones[0].argumentos, ("canci\u00f3n de jos\u00e9",),
            "el argumento tiene que conservar las tildes (interpretar baja a "
            "minusculas, pero no quita acentos): llego %r"
            % (acciones[0].argumentos,))

    def test_lo_que_no_es_orden_conocida_sigue_yendo_a_la_ia(self):
        """sin orden conocida, la IA recibe el texto ENTERO y con sus tildes"""
        acciones = interpretar("\u00bfCu\u00e1l es la capital de Francia?")
        self.assertEqual(
            [accion.nombre for accion in acciones], ["consultar_ia"],
            "una frase que no es orden conocida tiene que ir a la IA: %r"
            % (acciones,))
        self.assertEqual(
            acciones[0].argumentos, ("\u00bfcu\u00e1l es la capital de francia?",),
            "la IA tiene que recibir el texto entero y con sus tildes: %r"
            % (acciones[0].argumentos,))


class TestRegresionTodasLasOrdenes(unittest.TestCase):
    """la que impide que el bug vuelva: TODA la tabla, con y sin tildes

    Se recorre ORDENES entera y cada frase se prueba en sus TRES formas: tal
    cual esta escrita, sin ninguna tilde, y con todas las vocales
    acentuadas. Las tres TIENEN que dar LA MISMA accion (nombre Y
    argumentos).

    A las ordenes LIBRES se les pega detras un argumento fijo y SIN tildes
    (ARGUMENTO_POR_TIPO): sin el no disparan (una orden libre sin argumento
    se va a la IA a proposito) y la prueba estaria mirando un consultar_ia
    que no mide nada. Como el argumento no lleva tildes, la unica diferencia
    entre las tres frases esta en la ORDEN: si el resultado cambia, es por
    la tilde.

    Se compara la accion COMPLETA y no solo el nombre: con el argumento fijo
    sin tildes, hasta los argumentos tienen que salir identicos.
    """

    # argumento ASCII (sin tildes) que hace disparar cada tipo de orden libre
    ARGUMENTO_POR_TIPO = {
        "libre": "texto de prueba",
        "texto_raiz": "mi factura en documentos",
        "texto_a_raiz": "esta imagen a documentos",
        "numero": "a 50",
        "dos_numeros": "100 200",
        "raiz_final": "la carpeta documentos",
    }

    def test_cada_frase_de_la_tabla_da_la_misma_accion_con_y_sin_tildes(self):
        revisadas = 0
        disparan = 0
        for frase, accion_tabla in ORDENES:
            if accion_tabla is None:
                nombre, tipo = ORDENES_LIBRES[frase]
                sufijo = " " + self.ARGUMENTO_POR_TIPO[tipo]
            else:
                nombre, sufijo = accion_tabla.nombre, ""
            variantes = tuple(variante + sufijo for variante in
                              (frase, _sin_tildes(frase), _con_tildes(frase)))
            resultados = [interpretar(texto) for texto in variantes]
            with self.subTest(frase=frase):
                self.assertEqual(
                    resultados, [resultados[0]] * len(resultados),
                    "la orden %r cambia de accion segun las tildes: %r"
                    % (frase, list(zip(variantes, resultados))))
                self.assertEqual(
                    [accion.nombre for accion in resultados[0]], [nombre],
                    "la orden %r de la tabla da %r y tenia que dar %r"
                    % (frase, resultados[0][0].nombre, nombre))
                revisadas += 1
                if resultados[0][0].nombre != "consultar_ia":
                    disparan += 1
        # la tabla crece con el proyecto: 90 entradas hoy (2026-10-26). Si
        # alguien anade o quita ordenes, el numero se ajusta; lo que NO puede
        # pasar es que la prueba se quede mirando una tabla vacia.
        self.assertGreaterEqual(
            revisadas, 80,
            "solo se revisaron %d ordenes: la tabla ORDENES no es la que se "
            "esperaba" % (revisadas,))
        # con el argumento pegado, las 90 disparan (medido el 2026-10-26).
        # Sin este suelo la prueba podria pasar sin haber medido nada.
        self.assertGreaterEqual(
            disparan, 85,
            "solo %d ordenes disparan en sus tres formas: la prueba no esta "
            "midiendo lo que cree" % (disparan,))

    def test_ninguna_entrada_de_la_tabla_queda_muda_por_una_tilde(self):
        """CADA entrada de ORDENES casa con su PROPIA frase, con tilde o sin ella

        Esta prueba entra un escalon mas abajo que interpretar y por eso ve
        lo que la de arriba NO puede ver: la tabla tiene duplicados a
        proposito ('buscame' y 'b\u00fascame', 'pegalo' y 'p\u00e9galo',
        'mover el raton a' y 'mover el rat\u00f3n a'), asi que si el lado de
        la ORDEN dejara de normalizarse, el duplicado sin tilde taparia el
        fallo y la accion saldria igual. Aqui se comprueba lo que de verdad
        importa: que la entrada TAL CUAL esta escrita case con la frase que
        dice, en sus tres formas de acentuacion (indice 0).

        Se llama a _indice_de, el ayudante que hace el casado, con la frase
        YA normalizada igual que se la pasa interpretar (minusculas y sin
        tildes, con el mapa de esta prueba: no con el del modulo, para que
        la prueba no se valide a si misma).
        """
        mudas = []
        for frase, _accion in ORDENES:
            for variante in (frase, _sin_tildes(frase), _con_tildes(frase)):
                palabras = [_sin_tildes(trozo) for trozo in
                            variante.lower().split()]
                if _indice_de(palabras, frase) != 0:
                    mudas.append((frase, variante))
        self.assertEqual(
            mudas, [],
            "estas entradas de ORDENES no casan con su propia frase: %r"
            % (mudas,))

    def test_cada_orden_libre_es_clave_de_ordenes_libres(self):
        """una entrada libre sin su clave en ORDENES_LIBRES no dispara NUNCA

        El tipo de argumento se busca con ORDENES_LIBRES.get(orden) y la clave
        es el texto EXACTO de la tabla. Si alguien anade 'b\u00fascame' a
        ORDENES y la clave de ORDENES_LIBRES la escribe sin tilde, esa orden
        se queda muda y las tildes no tendrian nada que ver.
        """
        faltan = [frase for frase, accion in ORDENES
                  if accion is None and frase not in ORDENES_LIBRES]
        self.assertEqual(
            faltan, [],
            "estas ordenes libres no tienen su clave en ORDENES_LIBRES y "
            "nunca van a disparar: %r" % (faltan,))


if __name__ == "__main__":
    unittest.main()
