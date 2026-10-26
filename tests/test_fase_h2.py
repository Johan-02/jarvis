# pruebas de la FASE H2 del proyecto Jarvis (2026-10-26): QUE EL MODELO
# PUEDA MIRAR LA PANTALLA.
#
# Frentes:
#   1. la CONVERSION BMP -> PNG en Python puro: firma, IHDR con las
#      dimensiones correctas, CRC de cada chunk e IDAT que descomprime al
#      tamano esperado, con BMP de 24 bits (con relleno de fila) y de 32
#      bits (el que produce de verdad el GDI de esta PC: BGRX);
#   2. la REDUCCION: se toma 1 pixel de cada `paso` y el ancho final nunca
#      pasa de 1280, asi que una pantalla de 1920x1080 queda en 960x540;
#   3. la HERRAMIENTA mirar_pantalla: esta en REGISTRO, es INOCUA, aparece
#      SOLA en herramientas() y deja un PNG en el temp;
#   4. el BUCLE: un tool_use de mirar_pantalla devuelve un tool_result con un
#      bloque image/png en base64 que decodifica a la firma PNG;
#   5. la RESILIENCIA: si la captura falla, el tool_result es texto con el
#      error y el bucle sigue hasta la respuesta final;
#   6. la PRIVACIDAD: la imagen NO queda guardada en el historial de la
#      sesion (se reenviaria en cada turno), solo una nota de texto.
#
# REGLAS DE ESTE ARCHIVO: CERO capturas reales (se mockea _bmp_de_pantalla y
# nada toca el GDI), CERO llamadas a la API (cliente falso) y CERO voz. Los
# BMP de prueba se arman EN MEMORIA con struct; el PNG se verifica con zlib,
# sin Pillow (no esta instalada).

import base64
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

# raiz del proyecto; se puede sobre-escribir con JARVIS_ROOT para correr una
# copia de estas pruebas desde una carpeta temporal
RAIZ = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[1]))
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

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

# memoria falsa: estas pruebas NUNCA leen ni escriben el memoria.json real
MEMORIA_FALSA = {"nombre_usuario": "Yohan", "notas": ["aprende django"]}

# firma que todo PNG tiene que traer en sus primeros 8 bytes
FIRMA_PNG = b"\x89PNG\r\n\x1a\n"


def _bmp_desde_pixeles(ancho, alto, pixeles, bits=32):
    """BMP de prueba con las filas dadas de ARRIBA hacia abajo

    `pixeles` es una lista de filas (de arriba a abajo) y cada fila una lista
    de tuplas (r, g, b). El BMP se escribe como lo hace GDI: filas de ABAJO
    hacia ARRIBA, pixeles en BGR (y un byte X en 32 bits) y cada fila
    rellenada a multiplo de 4 bytes. La cabecera es la de un BMP real: 14
    bytes de BITMAPFILEHEADER + 40 de BITMAPINFOHEADER.
    """
    if len(pixeles) != alto or any(len(fila) != ancho for fila in pixeles):
        raise ValueError("los pixeles no coinciden con las dimensiones")
    filas = []
    for fila in reversed(pixeles):
        datos = bytearray()
        for rojo, verde, azul in fila:
            datos.extend((azul, verde, rojo))
            if bits == 32:
                datos.append(0)  # byte X de BGRX, ignorado por el PNG
        datos.extend(b"\x00" * ((4 - len(datos) % 4) % 4))
        filas.append(bytes(datos))
    cuerpo = b"".join(filas)
    info = struct.pack("<IiiHHIIiiII", 40, ancho, alto, 1, bits, 0,
                       len(cuerpo), 0, 0, 0, 0)
    cabecera = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(cuerpo), 0, 0, 54)
    return cabecera + info + cuerpo


def _bmp_solido(ancho, alto, color=(12, 34, 56), bits=32):
    """BMP de prueba de un solo color, sin listas de millones de tuplas"""
    pixel = bytes((color[2], color[1], color[0]))
    if bits == 32:
        pixel += b"\x00"
    fila = pixel * ancho
    fila += b"\x00" * ((4 - len(fila) % 4) % 4)
    cuerpo = fila * alto
    info = struct.pack("<IiiHHIIiiII", 40, ancho, alto, 1, bits, 0,
                       len(cuerpo), 0, 0, 0, 0)
    cabecera = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(cuerpo), 0, 0, 54)
    return cabecera + info + cuerpo


def _chunks(png):
    """[(tipo, datos, crc_leido)] de un PNG, recorriendo sus chunks"""
    if not png.startswith(FIRMA_PNG):
        raise AssertionError("no empieza con la firma PNG")
    trozos = []
    posicion = len(FIRMA_PNG)
    while posicion < len(png):
        largo = struct.unpack(">I", png[posicion:posicion + 4])[0]
        tipo = png[posicion + 4:posicion + 8]
        datos = png[posicion + 8:posicion + 8 + largo]
        crc = struct.unpack(
            ">I", png[posicion + 8 + largo:posicion + 12 + largo])[0]
        trozos.append((tipo, datos, crc))
        posicion += 12 + largo
    return trozos


def _ihdr(png):
    """(ancho, alto) del IHDR de un PNG"""
    for tipo, datos, _crc in _chunks(png):
        if tipo == b"IHDR":
            return struct.unpack(">II", datos[:8])
    raise AssertionError("el PNG no tiene IHDR")


def _crudo(png):
    """bytes que descomprime el IDAT (filas con su byte de filtro delante)"""
    partes = [datos for tipo, datos, _crc in _chunks(png)
              if tipo == b"IDAT"]
    if not partes:
        raise AssertionError("el PNG no tiene IDAT")
    return zlib.decompress(b"".join(partes))


def _hay_imagen(contenido):
    """True si hay un bloque image en cualquier parte de ese contenido"""
    if not isinstance(contenido, list):
        return False
    for bloque in contenido:
        if isinstance(bloque, dict):
            if bloque.get("type") == "image":
                return True
            if _hay_imagen(bloque.get("content")):
                return True
    return False


class TestConversionBmpAPng(unittest.TestCase):
    """BMP conocido -> PNG con firma, dimensiones, CRC y pixeles correctos"""

    def test_bmp_de_32_bits_el_que_produce_gdi_se_convierte_bien(self):
        """BGRX de GDI (32 bits) -> PNG RGB, con las filas dadas vuelta

        Los pixeles se eligen DISTINTOS por posicion: si el conversor no
        invierte las filas o no cambia BGR a RGB, el IDAT descomprimido no
        coincide y la prueba revienta.
        """
        pixeles = [
            [(255, 0, 0), (0, 255, 0)],      # fila de ARRIBA en el BMP
            [(0, 0, 255), (255, 255, 255)],  # fila de ABAJO
        ]
        fallo, png = motor._bmp_a_png(_bmp_desde_pixeles(2, 2, pixeles))
        self.assertIsNone(fallo, "la conversion fallo: %r" % (fallo,))
        self.assertTrue(png.startswith(FIRMA_PNG),
                        "el PNG no empieza con su firma")

        trozos = _chunks(png)
        self.assertEqual([tipo for tipo, _d, _c in trozos],
                         [b"IHDR", b"IDAT", b"IEND"],
                         "los chunks del PNG no son IHDR, IDAT e IEND")
        for tipo, datos, crc in trozos:
            self.assertEqual(
                crc, zlib.crc32(tipo + datos) & 0xFFFFFFFF,
                "el CRC del chunk %r esta mal" % (tipo,))

        self.assertEqual(_ihdr(png), (2, 2),
                         "el IHDR no trae las dimensiones de la captura")

        esperado = (b"\x00" + b"\xff\x00\x00" + b"\x00\xff\x00"
                    + b"\x00" + b"\x00\x00\xff" + b"\xff\xff\xff")
        self.assertEqual(
            _crudo(png), esperado,
            "el IDAT no descomprime a los pixeles esperados (filas "
            "invertidas o BGR sin cambiar a RGB): %r" % (_crudo(png),))
        self.assertEqual(
            len(_crudo(png)), 2 * (1 + 2 * 3),
            "el IDAT no mide lo esperado para 2x2 RGB")

    def test_bmp_de_24_bits_con_relleno_de_fila_se_convierte_bien(self):
        """3x2 de 24 bits: cada fila lleva 3 bytes de relleno que se saltan"""
        pixeles = [
            [(10, 20, 30), (40, 50, 60), (70, 80, 90)],
            [(1, 2, 3), (4, 5, 6), (7, 8, 9)],
        ]
        fallo, png = motor._bmp_a_png(_bmp_desde_pixeles(3, 2, pixeles, bits=24))
        self.assertIsNone(fallo, "la conversion de 24 bits fallo: %r" % (fallo,))
        self.assertEqual(_ihdr(png), (3, 2))
        esperado = (b"\x00" + bytes((10, 20, 30, 40, 50, 60, 70, 80, 90))
                    + b"\x00" + bytes((1, 2, 3, 4, 5, 6, 7, 8, 9)))
        self.assertEqual(
            _crudo(png), esperado,
            "el relleno de fila de 24 bits se colo en los pixeles")
        self.assertEqual(len(_crudo(png)), 2 * (1 + 3 * 3))

    def test_un_bmp_que_no_es_bmp_no_revienta_y_devuelve_fallo(self):
        """firma mala, bits no soportados y captura cortada: ok=False"""
        casos = {
            "basura": b"no soy un bmp",
            "firma": b"XX" + _bmp_desde_pixeles(1, 1, [[(1, 2, 3)]])[2:],
            "ocho_bits": _bmp_desde_pixeles(2, 2, [[(1, 2, 3)] * 2] * 2)[:28]
                          + struct.pack("<H", 8)
                          + _bmp_desde_pixeles(2, 2, [[(1, 2, 3)] * 2] * 2)[30:],
            "cortado": _bmp_desde_pixeles(4, 4, [[(1, 2, 3)] * 4] * 4)[:60],
        }
        for nombre, bmp in casos.items():
            with self.subTest(caso=nombre):
                fallo, png = motor._bmp_a_png(bmp)
                self.assertIsNotNone(
                    fallo, "un BMP invalido (%s) devolvio un PNG" % nombre)
                self.assertFalse(fallo.ok)
                self.assertIsNone(png)


class TestReduccion(unittest.TestCase):
    """se reduce ANTES de codificar: menos tokens, menos dinero, menos latencia"""

    def test_toma_uno_de_cada_dos_pixeles_y_deja_la_mitad(self):
        """4x4 con maximo 2 -> 2x2, con los pixeles de las posiciones pares"""
        pixeles = [[(0, 0, 0)] * 4 for _ in range(4)]
        pixeles[0][0] = (1, 2, 3)
        pixeles[0][2] = (7, 8, 9)
        pixeles[2][0] = (11, 12, 13)
        pixeles[2][2] = (17, 18, 19)
        fallo, png = motor._bmp_a_png(
            _bmp_desde_pixeles(4, 4, pixeles), ancho_maximo=2)
        self.assertIsNone(fallo, "la reduccion fallo: %r" % (fallo,))
        self.assertEqual(
            _ihdr(png), (2, 2),
            "el PNG no quedo a la mitad de ancho y alto")
        esperado = (b"\x00" + bytes((1, 2, 3)) + bytes((7, 8, 9))
                    + b"\x00" + bytes((11, 12, 13)) + bytes((17, 18, 19)))
        self.assertEqual(
            _crudo(png), esperado,
            "el muestreo no tomo los pixeles de las posiciones pares")

    def test_una_pantalla_1920x1080_queda_en_960x540_y_bajo_5_mb(self):
        """el caso real: 1920 de ancho -> paso 2 -> 960x540, PNG chico"""
        bmp = _bmp_solido(1920, 1080)
        self.assertEqual(len(bmp), 14 + 40 + 1920 * 1080 * 4,
                         "el BMP de prueba no mide lo que un GDI real")
        fallo, png = motor._bmp_a_png(bmp)
        self.assertIsNone(fallo, "la conversion de la pantalla fallo: %r"
                          % (fallo,))
        self.assertEqual(
            _ihdr(png), (960, 540),
            "1920x1080 no quedo en 960x540: %r" % (_ihdr(png),))
        self.assertLess(
            len(png), motor._TOPE_IMAGEN,
            "el PNG pasa del tope de 5 MB de la API: %d bytes" % len(png))
        self.assertLess(
            len(png), 2 * 1024 * 1024,
            "el PNG no queda HOLGADAMENTE por debajo de 5 MB: %d bytes"
            % len(png))
        self.assertEqual(
            len(_crudo(png)), 540 * (1 + 960 * 3),
            "el IDAT no descomprime al tamano esperado de 960x540 RGB")

    def test_una_imagen_chica_no_se_agranda(self):
        """2x2 con el maximo normal: se queda en 2x2 (paso 1)"""
        fallo, png = motor._bmp_a_png(
            _bmp_desde_pixeles(2, 2, [[(1, 2, 3), (4, 5, 6)],
                                      [(7, 8, 9), (10, 11, 12)]]))
        self.assertIsNone(fallo)
        self.assertEqual(_ihdr(png), (2, 2),
                         "una imagen menor al maximo no se puede agrandar")


class TestHerramientaMirarPantalla(unittest.TestCase):
    """la accion del motor: en REGISTRO, inocua y con PNG en el temp"""

    def test_esta_en_el_registro_y_es_inocua(self):
        """REGISTRO la trae y DESTRUCTIVAS no: solo lee la pantalla"""
        self.assertIn("mirar_pantalla", motor.REGISTRO)
        self.assertNotIn(
            "mirar_pantalla", motor.DESTRUCTIVAS,
            "mirar_pantalla se clasifico como destructiva: solo LEE la "
            "pantalla y no toca nada")

    def test_aparece_sola_en_las_herramientas_de_la_api(self):
        """el generador de REGISTRO la publica sin tocarla a mano"""
        definiciones = {h["name"]: h for h in herramientas.herramientas()}
        self.assertIn(
            "mirar_pantalla", definiciones,
            "mirar_pantalla no salio sola en herramientas(): el generador "
            "no la esta viendo en REGISTRO")
        definicion = definiciones["mirar_pantalla"]
        self.assertEqual(
            definicion["input_schema"],
            {"type": "object", "properties": {}},
            "mirar_pantalla no lleva argumentos y su esquema tiene que "
            "estar vacio: %r" % (definicion["input_schema"],))
        self.assertIn("pantalla", definicion["description"].lower())

    def test_deja_un_png_valido_en_el_temp_sin_capturar_de_verdad(self):
        """_bmp_de_pantalla mockeado: el PNG existe, mide 4x2 y es valido"""
        bmp = _bmp_desde_pixeles(4, 2, [
            [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)],
            [(13, 14, 15), (16, 17, 18), (19, 20, 21), (22, 23, 24)],
        ])
        with tempfile.TemporaryDirectory() as temporal, \
                mock.patch.object(motor, "_bmp_de_pantalla",
                                  mock.MagicMock(return_value=(None, bmp))), \
                mock.patch.object(motor.tempfile, "gettempdir",
                                  return_value=temporal):
            resultado = motor.ejecutar(Accion("mirar_pantalla"))
            self.assertTrue(
                resultado.ok,
                "mirar_pantalla fallo con la captura mockeada: %r"
                % (resultado.detalle,))
            ruta = resultado.detalle
            self.assertTrue(ruta.endswith(".png"),
                            "el detalle no es la ruta de un PNG: %r" % (ruta,))
            self.assertEqual(
                os.path.dirname(ruta), temporal,
                "el PNG no quedo en la carpeta temporal")
            self.assertTrue(os.path.exists(ruta),
                            "el PNG no existe en %s" % ruta)
            with open(ruta, "rb") as archivo:
                png = archivo.read()
            self.assertTrue(png.startswith(FIRMA_PNG),
                            "el archivo no tiene la firma PNG")
            self.assertEqual(_ihdr(png), (4, 2),
                             "el PNG no trae las dimensiones de la captura")
            self.assertLess(
                os.path.getsize(ruta), motor._TOPE_IMAGEN,
                "el PNG mockeado no queda por debajo de 5 MB")
            # el detalle que devuelve al bucle es TEXTO (la ruta), nunca bytes
            self.assertIsInstance(resultado.detalle, str)

    def test_si_la_captura_falla_devuelve_el_error_sin_escribir_nada(self):
        """GDI roto -> ok=False con su motivo y ningun archivo en el temp"""
        with tempfile.TemporaryDirectory() as temporal, \
                mock.patch.object(
                    motor, "_bmp_de_pantalla",
                    mock.MagicMock(return_value=(motor.Resultado(
                        ok=False,
                        detalle="GetDC(0) no devolvio ninguna pantalla"),
                        None))), \
                mock.patch.object(motor.tempfile, "gettempdir",
                                  return_value=temporal):
            resultado = motor.ejecutar(Accion("mirar_pantalla"))
            quedaron = os.listdir(temporal)

        self.assertFalse(resultado.ok)
        self.assertIn("pantalla", resultado.detalle)
        self.assertEqual(quedaron, [],
                         "una captura fallida dejo un archivo en el temp")


class TestBucleConImagen(unittest.TestCase):
    """en el bucle, mirar_pantalla viaja como BLOQUE DE IMAGEN"""

    def setUp(self):
        self.main = _main()
        self.main.limpiar_historial()
        self.main.olvidar_pendiente()

    def _png_de_prueba(self):
        """(ruta, bytes) de un PNG chico valido, hecho con el motor"""
        fallo, png = motor._bmp_a_png(
            _bmp_desde_pixeles(2, 2, [[(255, 0, 0), (0, 255, 0)],
                                      [(0, 0, 255), (255, 255, 255)]]))
        self.assertIsNone(fallo)
        descriptor, ruta = tempfile.mkstemp(suffix=".png")
        os.close(descriptor)
        with open(ruta, "wb") as archivo:
            archivo.write(png)
        self.addCleanup(os.remove, ruta)
        return ruta, png

    def test_el_tool_result_lleva_un_bloque_image_png_en_base64(self):
        """tool_use de mirar_pantalla -> texto + image con el PNG en base64"""
        main = self.main
        ruta, png = self._png_de_prueba()
        cliente = _cliente([
            _respuesta([_herramienta("mirar_pantalla", {}, "t_ver")],
                       "tool_use"),
            _respuesta([_texto("Veo una ventana con texto.")], "end_turn"),
        ])

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(
                    main.motor, "ejecutar",
                    mock.MagicMock(return_value=motor.Resultado(
                        True, ruta))) as ejecutar:
            texto = main.bucle_agente(
                "que hay en mi pantalla", dict(MEMORIA_FALSA),
                hablar=mock.MagicMock(), escuchar=mock.MagicMock())

        self.assertEqual(texto, "Veo una ventana con texto.")
        self.assertEqual(ejecutar.call_count, 1)
        self.assertEqual(ejecutar.call_args.args[0], Accion("mirar_pantalla", ()))

        mensajes = cliente.messages.create.call_args_list[1].kwargs["messages"]
        tool_result = mensajes[-1]["content"][0]
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_result["tool_use_id"], "t_ver")
        self.assertFalse(tool_result["is_error"],
                         "una captura exitosa volvio marcada como error")
        bloques = tool_result["content"]
        self.assertIsInstance(
            bloques, list,
            "el tool_result de mirar_pantalla NO lleva una lista de bloques: "
            "%r" % (bloques,))
        imagenes = [bloque for bloque in bloques
                    if bloque.get("type") == "image"]
        self.assertEqual(len(imagenes), 1,
                         "el tool_result no lleva UN bloque image")
        fuente = imagenes[0]["source"]
        self.assertEqual(
            fuente["type"], "base64",
            "la imagen no viaja como base64: %r" % (fuente,))
        self.assertEqual(
            fuente["media_type"], "image/png",
            "la API NO acepta BMP: el media_type tiene que ser image/png, "
            "no %r" % (fuente["media_type"],))
        self.assertEqual(
            base64.b64decode(fuente["data"]), png,
            "el base64 no decodifica EXACTAMENTE al PNG del motor")
        self.assertTrue(
            base64.b64decode(fuente["data"]).startswith(FIRMA_PNG),
            "el base64 no decodifica a la firma PNG")
        self.assertTrue(any(bloque.get("type") == "text"
                            and "captura" in bloque.get("text", "").lower()
                            for bloque in bloques),
                        "el tool_result no dice que es una captura: %r"
                        % (bloques,))

    def test_si_la_captura_falla_el_bucle_sigue_con_el_error_en_texto(self):
        """captura rota -> tool_result de texto con el error y respuesta final

        La mutacion que mata: dejar que el fallo de mirar_pantalla lance una
        excepcion (o cortar el bucle) cuando la captura no sale. Aqui el
        modelo tiene que recibir el error y el turno terminar igual.
        """
        main = self.main
        cliente = _cliente([
            _respuesta([_herramienta("mirar_pantalla", {}, "t_fallo")],
                       "tool_use"),
            _respuesta([_texto("No pude verla, señor.")], "end_turn"),
        ])
        error = "GetDC(0) no devolvio ninguna pantalla"

        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(
                    main.motor, "ejecutar",
                    mock.MagicMock(return_value=motor.Resultado(
                        False, error))):
            texto = main.bucle_agente(
                "mira mi pantalla", dict(MEMORIA_FALSA),
                hablar=mock.MagicMock(), escuchar=mock.MagicMock())

        self.assertEqual(
            cliente.messages.create.call_count, 2,
            "el bucle no siguio tras el fallo de la captura")
        tool_result = cliente.messages.create.call_args_list[1] \
            .kwargs["messages"][-1]["content"][0]
        self.assertIsInstance(
            tool_result["content"], str,
            "un fallo de captura mando una imagen igual: %r"
            % (tool_result["content"],))
        self.assertIn(error, tool_result["content"],
                      "el error de la captura no llego al modelo")
        self.assertTrue(tool_result["is_error"],
                        "el fallo de captura no volvio como error")
        self.assertEqual(texto, "No pude verla, señor.")

    def test_la_imagen_no_se_guarda_en_el_historial_de_la_sesion(self):
        """privacidad y costo: el historial guarda el texto, NO la imagen

        Si la imagen quedara en HISTORIAL_SESION se reenviaria en CADA
        llamada del resto de la sesion: la pantalla saldria sin que el modelo
        la pidiera y se pagarian los tokens otra vez. La mutacion que mata:
        quitar _sin_imagenes de _agregar_turno.
        """
        main = self.main
        ruta, _png = self._png_de_prueba()
        cliente = _cliente([
            _respuesta([_herramienta("mirar_pantalla", {}, "t_priv")],
                       "tool_use"),
            _respuesta([_texto("Ya la vi.")], "end_turn"),
        ])
        with mock.patch.object(main, "_obtener_cliente_ia",
                               mock.MagicMock(return_value=cliente)), \
                mock.patch.object(
                    main.motor, "ejecutar",
                    mock.MagicMock(return_value=motor.Resultado(
                        True, ruta))):
            main.bucle_agente("mira la pantalla", dict(MEMORIA_FALSA),
                              hablar=mock.MagicMock(),
                              escuchar=mock.MagicMock())

        self.assertTrue(main.HISTORIAL_SESION,
                        "el turno no quedo en el historial")
        for mensaje in main.HISTORIAL_SESION:
            self.assertFalse(
                _hay_imagen(mensaje["content"]),
                "una captura de pantalla quedo guardada en el historial: se "
                "reenviaria en cada turno sin que nadie la pida")
        guardado = [bloque for mensaje in main.HISTORIAL_SESION
                    if isinstance(mensaje["content"], list)
                    for bloque in mensaje["content"]
                    if isinstance(bloque, dict)
                    and bloque.get("type") == "tool_result"]
        self.assertTrue(guardado, "el tool_result no quedo en el historial")
        texto_guardado = str(guardado[0]["content"])
        self.assertIn(
            "no se reenvia", texto_guardado,
            "el historial no deja constancia de que la captura no se "
            "reenvia: %r" % (texto_guardado,))


if __name__ == "__main__":
    unittest.main()
