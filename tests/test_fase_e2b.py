# pruebas de la FASE E2b del asistente Jarvis (2026-10-21): las 5 acciones
# de "archivos y programas" del motor y las ordenes nuevas del reconocedor.
#
# REGLA DE ORO: NADA se ejecuta en vivo. subprocess (taskkill y Popen),
# os.startfile, os.walk, win32gui (WM_CLOSE), winreg (registro) y webbrowser
# van SIEMPRE reemplazados por dobles de solo registro: ni matar procesos, ni
# cerrar ventanas de verdad, ni abrir el navegador, ni recorrer discos.
#
# Lo que mide este archivo:
#   1. las 5 acciones estan en REGISTRO y CLASIFICADAS (la proteccion de
#      tests/test_motor.py sigue viva);
#   2. cada accion le pasa lo CORRECTO a su doble;
#   3. la rama destructiva de cerrar (taskkill /F) sin confirmar NO toca
#      absolutamente nada;
#   4. buscar_archivo con una raiz invalida devuelve ok=False sin reventar;
#   5. las ordenes nuevas del reconocedor mapean a su accion correcta.

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

# la lista blanca INOCUAS vive en test_motor: aqui se reutiliza para comprobar
# que las acciones nuevas estan clasificadas EN ELLA y no solo registradas
try:
    from tests.test_motor import INOCUAS
except ImportError:  # el descubridor pone tests/ en el sys.path
    from test_motor import INOCUAS

from main import motor
from main.reconocedor import Accion, interpretar

# las 5 acciones nuevas de esta fase (cerrar_app_forzado es su variante
# destructiva y se revisa aparte)
ACCIONES_NUEVAS = (
    "abrir_archivo",
    "abrir_app",
    "abrir_url",
    "cerrar_app",
    "buscar_archivo",
)


@contextlib.contextmanager
def _doblado():
    """reemplaza TODO lo que el motor toca del sistema por dobles de registro

    Dentro de esta ventana no puede salir ni un taskkill, ni un WM_CLOSE, ni
    un startfile de verdad: los siete nombres se parchan sobre el MODULO
    motor (los mismos que main/motor.py importa a nivel de modulo, por eso
    puede parcharlos). `os.path.expandvars` se deja identidad y
    `os.path.join` une con barras invertidas, para que una ruta que pase por
    ahi se pueda comparar de verdad en las pruebas.
    """
    dobles = {nombre: mock.MagicMock(name=nombre + "_falso")
              for nombre in ("os", "subprocess", "ctypes", "win32gui",
                             "winreg", "shutil", "webbrowser")}
    dobles["os"].path.expandvars.side_effect = lambda texto: texto
    dobles["os"].path.join.side_effect = \
        lambda *partes: "\\".join(str(parte) for parte in partes)
    with contextlib.ExitStack() as pila:
        for nombre, doble in dobles.items():
            pila.enter_context(mock.patch.object(motor, nombre, doble))
        yield dobles


def _claves_de_ventana(dobles, hwnd=4242, titulo="Google Chrome - Perfil 1",
                       visible=True):
    """hace que EnumWindows "encuentre" UNA ventana falsa con ese titulo

    EnumWindows recorre ventanas llamando al callback que recibe; el doble no
    lo haria nunca solo, y sin esto cerrar_app no encontraria NADA.
    """
    gui = dobles["win32gui"]
    gui.EnumWindows.side_effect = \
        lambda visita, parametro: visita(hwnd, parametro)
    gui.IsWindowVisible.return_value = visible
    gui.GetWindowText.return_value = titulo


class TestCincoAccionesClasificadas(unittest.TestCase):
    """las 5 estan en REGISTRO y CLASIFICADAS: la proteccion sigue viva"""

    def test_las_cinco_estan_en_el_registro(self):
        """sin registrar, ejecutar() devolveria ACCION_DESCONOCIDA"""
        faltantes = [nombre for nombre in ACCIONES_NUEVAS
                     if nombre not in motor.REGISTRO]
        self.assertEqual(
            faltantes, [],
            "estas acciones no estan en REGISTRO: %s" % faltantes)

    def test_las_cinco_estan_clasificadas(self):
        """cada una esta en INOCUAS o en DESTRUCTIVAS: ninguna sin clasificar

        Es la misma regla que la parametrizada de tests/test_motor.py
        (test_toda_accion_del_registro_esta_clasificada), repetida aqui para
        que el fallo diga exactamente cual de las 5 de esta fase se quedo sin
        clasificar.
        """
        for nombre in ACCIONES_NUEVAS + ("cerrar_app_forzado",):
            with self.subTest(nombre=nombre):
                self.assertIn(
                    nombre, motor.REGISTRO,
                    "%r no esta en REGISTRO" % (nombre,))
                self.assertTrue(
                    nombre in INOCUAS or nombre in motor.DESTRUCTIVAS,
                    "%r esta en REGISTRO pero NO esta clasificada: no esta "
                    "en la lista blanca INOCUAS ni en DESTRUCTIVAS"
                    % (nombre,))

    def test_cerrar_app_y_su_version_destructiva_estan_partidas(self):
        """cerrar_app pide WM_CLOSE (inocua); solo la /F pide confirmacion

        Si cerrar_app_forzado quedara en INOCUAS, taskkill /F correria sin
        confirmacion nunca; si cerrar_app quedara en DESTRUCTIVAS, no se
        podria cerrar nada sin preguntar.
        """
        self.assertIn("cerrar_app", INOCUAS)
        self.assertNotIn("cerrar_app", motor.DESTRUCTIVAS)
        self.assertIn("cerrar_app_forzado", motor.DESTRUCTIVAS)
        self.assertNotIn("cerrar_app_forzado", INOCUAS)


class TestAbrirArchivos(unittest.TestCase):
    """abrir_archivo: os.startfile con la ruta resuelta, o nada"""

    def test_le_pasa_la_ruta_entera_a_startfile(self):
        """la ruta existe -> startfile recibe EXACTAMENTE esa ruta"""
        ruta = r"C:\Users\PRGRAMADOR\Desktop\nota.txt"
        with _doblado() as dobles:
            dobles["os"].path.exists.return_value = True
            resultado = motor.ejecutar(Accion("abrir_archivo", (ruta,)))

        dobles["os"].startfile.assert_called_once_with(ruta)
        dobles["subprocess"].Popen.assert_not_called()
        self.assertTrue(
            resultado.ok, "abrir_archivo no devolvio ok: %r"
            % (resultado.detalle,))

    def test_el_alias_de_la_carpeta_se_resuelve_a_su_ruta(self):
        """"escritorio" (lo que dice el reconocedor) abre el Desktop real"""
        with _doblado() as dobles:
            dobles["os"].path.expanduser.return_value = r"C:\Users\PRGRAMADOR"
            dobles["os"].path.exists.return_value = True
            resultado = motor.ejecutar(Accion("abrir_archivo", ("escritorio",)))

        dobles["os"].startfile.assert_called_once_with(
            r"C:\Users\PRGRAMADOR\Desktop")
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_lo_que_no_existe_no_se_abre_y_no_revienta(self):
        """ruta inexistente -> ok=False y startfile NI se llama

        Sin esta guarda os.startfile lanzaria FileNotFoundError hasta
        main.py, que es el unico que habla.
        """
        with _doblado() as dobles:
            dobles["os"].path.exists.return_value = False
            resultado = motor.ejecutar(
                Accion("abrir_archivo", (r"C:\\no\\existe.txt",)))

        dobles["os"].startfile.assert_not_called()
        self.assertFalse(resultado.ok, "marco ok con una ruta inexistente")
        self.assertIn("no existe", resultado.detalle)

    def test_sin_ruta_no_abre_nada(self):
        """argumento vacio -> ok=False sin tocar el sistema"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("abrir_archivo", ("",)))

        dobles["os"].startfile.assert_not_called()
        self.assertFalse(resultado.ok, resultado.detalle)


class TestAbrirApps(unittest.TestCase):
    """abrir_app: UWP -> URI; registrado -> App Paths; si no -> PATH"""

    def test_la_app_de_tienda_se_abre_por_su_uri(self):
        """calculadora es UWP: URI, no App Paths ni PATH (medido)"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("abrir_app", ("calculadora",)))

        dobles["os"].startfile.assert_called_once_with("calculator:")
        dobles["subprocess"].Popen.assert_not_called()
        dobles["winreg"].OpenKey.assert_not_called()
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_la_app_registrada_va_por_app_paths_y_popen(self):
        """chrome.exe esta en HKCU\\App Paths y NO en el PATH (medido)"""
        ruta = r"C:\Users\PRGRAMADOR\AppData\Local\Google\Chrome\Application\chrome.exe"
        with _doblado() as dobles:
            dobles["winreg"].QueryValueEx.return_value = (ruta, 1)
            resultado = motor.ejecutar(Accion("abrir_app", ("chrome",)))

        # se busco en el registro con la clave exacta y con la extesion .exe
        clave_pedida = dobles["winreg"].OpenKey.call_args.args[1]
        self.assertTrue(
            clave_pedida.endswith("\\chrome.exe"),
            "App Paths se pidio con %r y tenia que terminar en chrome.exe"
            % (clave_pedida,))
        # Popen recibe la ruta absoluta como lista (las rutas con espacios
        # no se pueden dejar sueltas) y NUNCA startfile, que no da el PID
        dobles["subprocess"].Popen.assert_called_once_with([ruta])
        dobles["os"].startfile.assert_not_called()
        dobles["shutil"].which.assert_not_called()
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_si_no_esta_en_app_paths_baja_al_path(self):
        """notepad.exe no esta en App Paths (medido): pasa por el PATH"""
        ruta = r"C:\Windows\System32\notepad.exe"
        with _doblado() as dobles:
            dobles["winreg"].OpenKey.side_effect = OSError("no existe la clave")
            dobles["shutil"].which.return_value = ruta
            resultado = motor.ejecutar(Accion("abrir_app", ("notepad",)))

        dobles["winreg"].OpenKey.assert_called_once()
        dobles["shutil"].which.assert_called_once_with("notepad.exe")
        dobles["subprocess"].Popen.assert_called_once_with([ruta])
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_app_desconocida_no_abre_absolutamente_nada(self):
        """ni registro ni PATH -> ok=False y cero llamadas al sistema"""
        with _doblado() as dobles:
            dobles["winreg"].OpenKey.side_effect = OSError("no existe la clave")
            dobles["shutil"].which.return_value = None
            resultado = motor.ejecutar(
                Accion("abrir_app", ("la que no esta",)))

        dobles["subprocess"].Popen.assert_not_called()
        dobles["os"].startfile.assert_not_called()
        self.assertFalse(
            resultado.ok, "marco ok una aplicacion que no existe")
        self.assertIn("no encontre", resultado.detalle)


class TestAbrirUrls(unittest.TestCase):
    """abrir_url: webbrowser.open con la url ya completa"""

    def test_le_completa_el_esquema_a_la_url(self):
        """"youtube.com" sin https:// se trata como archivo y no abre nada"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("abrir_url", ("youtube.com",)))

        dobles["webbrowser"].open.assert_called_once_with(
            "https://youtube.com")
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_no_duplica_el_esquema_si_ya_lo_trae(self):
        """una url completa viaja intacta, sin https://https://"""
        url = "https://mail.google.com/mail/u/0"
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("abrir_url", (url,)))

        dobles["webbrowser"].open.assert_called_once_with(url)
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_url_vacia_no_abre_nada(self):
        """sin url no se llama al navegador (webbrowser.open("") no sirve)"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(Accion("abrir_url", ("  ",)))

        dobles["webbrowser"].open.assert_not_called()
        self.assertFalse(resultado.ok, resultado.detalle)


class TestCerrarApp(unittest.TestCase):
    """cerrar_app manda WM_CLOSE (inocua); la /F es OTRA accion"""

    def test_manda_wm_close_a_la_ventana_de_la_app(self):
        """WM_CLOSE = 0x0010, al hwnd que encontro EnumWindows"""
        with _doblado() as dobles:
            _claves_de_ventana(dobles, hwnd=4242,
                               titulo="Google Chrome - Perfil 1")
            resultado = motor.ejecutar(Accion("cerrar_app", ("chrome",)))

        dobles["win32gui"].PostMessage.assert_called_once_with(4242, 0x0010, 0, 0)
        dobles["subprocess"].run.assert_not_called()
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_el_alias_navegador_tambien_cierra(self):
        """"el navegador" no esta en el titulo: el alias prueba chrome,
        firefox y edge (medido: los titulos pueden salir en ingles)"""
        with _doblado() as dobles:
            _claves_de_ventana(dobles, titulo="Microsoft Edge - juntada")
            resultado = motor.ejecutar(
                Accion("cerrar_app", ("navegador",)))

        dobles["win32gui"].PostMessage.assert_called_once_with(
            4242, 0x0010, 0, 0)
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_sin_coincidencia_no_manda_nada(self):
        """ninguna ventana con ese nombre -> ok=False y cero mensajes"""
        with _doblado() as dobles:
            _claves_de_ventana(dobles, titulo="Bloc de notas")
            resultado = motor.ejecutar(Accion("cerrar_app", ("chrome",)))

        dobles["win32gui"].PostMessage.assert_not_called()
        dobles["subprocess"].run.assert_not_called()
        self.assertFalse(resultado.ok, resultado.detalle)
        self.assertIn("no encontre", resultado.detalle)

    def test_la_fuerza_sin_confirmar_no_mata_nada(self):
        """LA REGLA: confirmado=False -> REQUIERE_CONFIRMACION y CERO llamadas

        taskkill /F pierde lo no guardado, asi que sin confirmacion no puede
        llegar ni al subprocess: ni taskkill, ni win32gui, ni os.
        """
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("cerrar_app_forzado", ("chrome",)),
                confirmado=False)

        self.assertEqual(
            resultado.detalle, motor.REQUIERE_CONFIRMACION,
            "la destructiva sin confirmar devolvio %r" % (resultado.detalle,))
        self.assertFalse(resultado.ok)
        dobles["subprocess"].run.assert_not_called()
        dobles["win32gui"].PostMessage.assert_not_called()
        dobles["os"].startfile.assert_not_called()

    def test_la_fuerza_confirmada_manda_taskkill_con_t_y_f(self):
        """confirmada -> taskkill /IM <app> /T /F, con check=True"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("cerrar_app_forzado", ("chrome",)),
                confirmado=True)

        dobles["subprocess"].run.assert_called_once()
        llamada = dobles["subprocess"].run.call_args
        self.assertEqual(
            llamada.args[0], ["taskkill", "/IM", "chrome", "/T", "/F"],
            "taskkill salio con %r" % (llamada.args[0],))
        self.assertTrue(
            llamada.kwargs.get("check"),
            "sin check=True un taskkill rechazado seguiria como exito")
        dobles["win32gui"].PostMessage.assert_not_called()
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_sin_nombre_la_fuerza_no_corre_taskkill(self):
        """nombre vacio -> ok=False ANTES de llamar a taskkill"""
        with _doblado() as dobles:
            resultado = motor.ejecutar(
                Accion("cerrar_app_forzado", ("",)), confirmado=True)

        dobles["subprocess"].run.assert_not_called()
        self.assertFalse(resultado.ok, resultado.detalle)


class TestBuscarArchivo(unittest.TestCase):
    """buscar_archivo: solo LEE, acotado por defecto y sin reventar"""

    # lo que mide esta clase: las 3 carpetas por defecto, que es el tope que
    # evita recorrer C:\ (medido: ~59 s, y ~2 min midiendo tamaños)
    RAICES_POR_DEFECTO = [
        r"C:\Users\PRGRAMADOR\Desktop",
        r"C:\Users\PRGRAMADOR\Documents",
        r"C:\Users\PRGRAMADOR\Downloads",
    ]

    def test_por_defecto_solo_recorre_las_tres_carpetas(self):
        """raiz=None -> Escritorio, Documentos y Descargas: NUNCA la raiz del
        disco (C:), que tarda ~59 s en recorrerse (medido)"""
        with _doblado() as dobles:
            dobles["os"].path.expanduser.return_value = r"C:\Users\PRGRAMADOR"
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter(
                    [(raiz, [], ["factura_2026.pdf", "otro.txt"])])
            resultado = motor.ejecutar(
                Accion("buscar_archivo", ("factura",)))

        raizes = [llamada.args[0]
                  for llamada in dobles["os"].walk.call_args_list]
        self.assertEqual(
            raizes, self.RAICES_POR_DEFECTO,
            "la busqueda por defecto salio de las carpetas permitidas: %s"
            % (raizes,))
        # onerror siempre puesto: una carpeta sin permisos se SALTA
        for llamada in dobles["os"].walk.call_args_list:
            self.assertIn("onerror", llamada.kwargs,
                          "os.walk sin onerror reventaria con PermissionError")
        self.assertTrue(resultado.ok, resultado.detalle)
        self.assertIn("factura_2026.pdf", resultado.detalle)

    def test_raiz_invalida_devuelve_ok_false_sin_recorrer_nada(self):
        """raiz que no es carpeta -> ok=False, sin os.walk y sin excepcion"""
        for raiz in ("C:\\esta_carpeta_no_existe", "", "archivo.pdf"):
            with self.subTest(raiz=raiz):
                with _doblado() as dobles:
                    dobles["os"].path.isdir.return_value = False
                    resultado = motor.ejecutar(
                        Accion("buscar_archivo", ("factura", raiz)))

                self.assertFalse(
                    resultado.ok,
                    "raiz %r invalida y devolvio ok=True" % (raiz,))
                self.assertIn("raiz no valida", resultado.detalle)
                # la cadena vacia se descarta ANTES de isdir (no hay nada que
                # preguntar); en los otros casos la prueba tiene que ver que
                # SI se pregunto, o pasaria aunque el codigo entrara por otra
                if raiz:
                    self.assertTrue(
                        dobles["os"].path.isdir.called,
                        "no se llego a isdir(): la rama probada no es la "
                        "rama de raiz invalida")
                dobles["os"].walk.assert_not_called()

    def test_raiz_valida_se_resuelve_y_se_usa(self):
        """el alias "documentos" (lo que dice el reconocedor) se usa tal cual"""
        with _doblado() as dobles:
            dobles["os"].path.expanduser.return_value = r"C:\Users\PRGRAMADOR"
            dobles["os"].path.isdir.return_value = True
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter([(raiz, [], ["factura.pdf"])])
            resultado = motor.ejecutar(
                Accion("buscar_archivo", ("factura", "documentos")))

        dobles["os"].walk.assert_called_once()
        self.assertEqual(
            dobles["os"].walk.call_args.args[0],
            r"C:\Users\PRGRAMADOR\Documents",
            "el alias documentos no se resolvio a su carpeta real")
        self.assertTrue(resultado.ok, resultado.detalle)

    def test_sin_resultados_avisa_sin_mentir(self):
        """no encontrar nada NO es un error: la busqueda corrio bien"""
        with _doblado() as dobles:
            dobles["os"].path.expanduser.return_value = r"C:\Users\PRGRAMADOR"
            dobles["os"].walk.side_effect = \
                lambda raiz, onerror=None: iter([(raiz, [], ["otro.txt"])])
            resultado = motor.ejecutar(
                Accion("buscar_archivo", ("factura",)))

        self.assertTrue(
            resultado.ok,
            "no encontrar nada marco ok=False: main.py lo esconderia en "
            "consola en vez de contestarle a Edward")
        self.assertIn("no encontre", resultado.detalle)

    def test_el_onerror_de_walk_no_revienta(self):
        """una carpeta sin permisos se salta: el handler no lanza nada"""
        self.assertIsNone(
            motor._ignorar_error_de_walk(PermissionError("denegado")),
            "onerror devolvio algo y, peor, pudo lanzar la excepcion")


class TestOrdenesNuevasDelReconocedor(unittest.TestCase):
    """cada frase nueva mapea a la accion correcta, sin ejecutar nada"""

    def test_las_ordenes_nuevas_mapean_a_su_accion(self):
        casos = (
            # abrir app: el argumento va como lo entiende Windows
            ("abre el explorador", "abrir_app", ("explorer.exe",)),
            ("abre chrome", "abrir_app", ("chrome",)),
            ("abre el bloc de notas", "abrir_app", ("notepad",)),
            ("abre la calculadora", "abrir_app", ("calculadora",)),
            ("abre visual studio code", "abrir_app", ("code",)),
            # abrir archivo: la raiz viaja como alias, el modulo es puro
            ("abre mi escritorio", "abrir_archivo", ("escritorio",)),
            ("abre mis documentos", "abrir_archivo", ("documentos",)),
            ("abre mis descargas", "abrir_archivo", ("descargas",)),
            # abrir url: lo que sigue a la orden
            ("abre esta pagina youtube.com", "abrir_url", ("youtube.com",)),
            ("abre la url ejemplo.com", "abrir_url", ("ejemplo.com",)),
            ("abre el enlace youtube.com", "abrir_url", ("youtube.com",)),
            # buscar: texto, y la raiz final si la frase la trae
            ("busca mi factura en documentos",
             "buscar_archivo", ("mi factura", "documentos")),
            ("busca el recibo", "buscar_archivo", ("el recibo",)),
            ("buscame el informe en descargas",
             "buscar_archivo", ("el informe", "descargas")),
            ("búscame la boleta", "buscar_archivo", ("la boleta",)),
            # cerrar: el nombre de la ventana o su alias
            ("cierra chrome", "cerrar_app", ("chrome",)),
            ("cierra el navegador", "cerrar_app", ("navegador",)),
            ("cierra spotify", "cerrar_app", ("spotify",)),
            ("cierra visual studio code",
             "cerrar_app", ("visual studio code",)),
            # la fuerza: DEBE ganar el desempate contra la version graciosa,
            # o "cierra chrome a la fuerza" se cerraria sin confirmacion nunca
            ("cierra chrome a la fuerza",
             "cerrar_app_forzado", ("chrome",)),
            ("cierra el navegador a la fuerza",
             "cerrar_app_forzado", ("navegador",)),
        )
        for texto, nombre, argumentos in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    len(acciones), 1,
                    "%r devolvio %d acciones y tiene que devolver 1: %r"
                    % (texto, len(acciones), acciones))
                self.assertEqual(
                    acciones[0].nombre, nombre,
                    "%r debio ser %r y fue %r"
                    % (texto, nombre, acciones[0].nombre))
                self.assertEqual(
                    acciones[0].argumentos, argumentos,
                    "%r debio llevar %r y llevo %r"
                    % (texto, argumentos, acciones[0].argumentos))

    def test_las_ordenes_viejas_siguen_iguales(self):
        """las nuevas no se comieron el desempate de las de antes"""
        casos = (
            ("abre google", "abrir",
             ("Google", "https://www.google.com")),
            ("abre youtube", "abrir",
             ("YouTube", "https://www.youtube.com")),
            ("que hora es", "hora", ()),
            ("reproduce metallica", "reproduce", ("metallica",)),
        )
        for texto, nombre, argumentos in casos:
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(acciones[0].nombre, nombre,
                                 "%r cambio de accion: %r"
                                 % (texto, acciones[0].nombre))
                self.assertEqual(acciones[0].argumentos, argumentos,
                                 "%r cambio de argumentos: %r"
                                 % (texto, acciones[0].argumentos))

    def test_sin_url_detras_no_se_inventa_nada(self):
        """"abre esta pagina" sola va a la IA, igual que "reproduce" sola"""
        for texto in ("abre esta pagina", "abre la url", "abre el archivo"):
            with self.subTest(texto=texto):
                acciones = interpretar(texto)
                self.assertEqual(
                    [accion.nombre for accion in acciones],
                    ["consultar_ia"],
                    "%r disparo una accion sin argumento: %r"
                    % (texto, acciones))


if __name__ == "__main__":
    unittest.main()
