# reconocedor de ordenes del asistente Jarvis (FASE C).
#
# Este modulo traduce TEXTO en ACCIONES. Es una funcion PURA: no habla, no
# graba audio, no abre el navegador y no pregunta a la IA. Solo devuelve lo
# que habria que hacer y main.py se limita a ejecutarlo. Ese corte es lo que
# permite cambiar este parser por un LLM (FASE E) sin tocar el motor.

import dataclasses


# una cosa que el asistente puede hacer, todavia sin ejecutarla. Congelada
# (frozen=True) para que nadie la pueda mutar despues de devolverla.
@dataclasses.dataclass(frozen=True)
class Accion:
    nombre: str
    argumentos: tuple = ()


# "reproduce" es la orden con argumento LIBRE por excelencia: su argumento no
# esta en la tabla sino que es lo que venga DETRAS de la palabra en la frase
# del usuario (estan en ORDENES y en ORDENES_LIBRES de abajo)
ORDEN_REPRODUCIR = "reproduce"

# todas las ordenes que el asistente sabe reconocer sin ayuda de la IA, con
# lo que el usuario dice y la accion que le corresponde. El orden de la tabla
# DESATATA si dos ordenes empiezan en la misma palabra, y hoy eso pasa:
# "cierra chrome a la fuerza" y "cierra chrome" empiezan igual, por eso la
# fuerza va ANTES en la tabla. Si no fuera asi, la frase "cierra chrome a la
# fuerza" se cerraria sin pedir confirmacion nunca.
ORDENES = (
    ("hora", Accion("hora", ())),
    ("youtube", Accion("abrir", ("YouTube", "https://www.youtube.com"))),
    ("google", Accion("abrir", ("Google", "https://www.google.com"))),
    ("gmail", Accion("abrir", ("Gmail", "https://mail.google.com"))),
    ("spotify", Accion("abrir", ("Spotify", "https://www.spotify.com"))),
    ("linkedin", Accion("abrir", ("LinkedIn", "https://www.linkedin.com"))),
    ("tradingview", Accion("abrir", ("TradingView", "https://www.tradingview.com"))),
    # el curso de Dalto es un caso fijo con su video, no un sitio del navegador
    ("curso de python dalto",
     Accion("abrir", ("curso de Python", "https://www.youtube.com/watch?v=nKPbfIU442g"))),

    # --- FASE E2b: abrir programas. El nombre va como lo maneja Windows ---
    ("abre el explorador", Accion("abrir_app", ("explorer.exe",))),
    ("abre chrome", Accion("abrir_app", ("chrome",))),
    ("abre el bloc de notas", Accion("abrir_app", ("notepad",))),
    ("abre la calculadora", Accion("abrir_app", ("calculadora",))),
    ("abre visual studio code", Accion("abrir_app", ("code",))),

    # --- cerrar programas: la fuerza (destructiva) ANTES que la graciosa,
    #     que es la que empieza por las mismas palabras ---
    ("cierra chrome a la fuerza", Accion("cerrar_app_forzado", ("chrome",))),
    ("cierra el navegador a la fuerza",
     Accion("cerrar_app_forzado", ("navegador",))),
    ("cierra chrome", Accion("cerrar_app", ("chrome",))),
    ("cierra el navegador", Accion("cerrar_app", ("navegador",))),
    ("cierra spotify", Accion("cerrar_app", ("spotify",))),
    ("cierra visual studio code", Accion("cerrar_app", ("visual studio code",))),
    ("cierra el bloc de notas", Accion("cerrar_app", ("bloc de notas",))),

    # --- abrir archivos: la raiz va como ALIAS y no como ruta, porque este
    #     modulo es puro y no importa os ---
    ("abre mi escritorio", Accion("abrir_archivo", ("escritorio",))),
    ("abre mis documentos", Accion("abrir_archivo", ("documentos",))),
    ("abre mis descargas", Accion("abrir_archivo", ("descargas",))),

    # --- FASE E2c, GRUPO 2: el equipo. Las tres destructivas (apagar,
    #     reiniciar y suspender) viajan SIN argumentos: el freno de la
    #     confirmacion vive en el MOTOR (DESTRUCTIVAS), no aqui. La ultima
    #     palabra de "apaga la computadora" y de "apaga el pc" es distinta,
    #     por eso se declara cada frase ---
    ("apaga la computadora", Accion("apagar", ())),
    ("apagar la computadora", Accion("apagar", ())),
    ("apaga el pc", Accion("apagar", ())),
    ("reinicia el equipo", Accion("reiniciar", ())),
    ("no te apagues", Accion("cancelar_apagado", ())),
    ("cancela el apagado", Accion("cancelar_apagado", ())),
    ("bloquea la pantalla", Accion("bloquear", ())),
    ("ponse en suspencion", Accion("suspender", ())),
    ("ponse en suspension", Accion("suspender", ())),
    ("sube el volumen", Accion("volumen_subir", ())),
    ("subele", Accion("volumen_subir", ())),
    ("baja el volumen", Accion("volumen_bajar", ())),
    ("bajita", Accion("volumen_bajar", ())),
    ("silencia", Accion("silenciar", ())),
    ("captura la pantalla", Accion("capturar_pantalla", ("escritorio",))),
    ("hazme una captura", Accion("capturar_pantalla", ("escritorio",))),

    # --- FASE E2d, GRUPO 3: escribir y teclar. "escribe <texto>" es de
    #     argumento LIBRE y se declara abajo, en ORDENES_LIBRES. Ojo con el
    #     ORDEN de esta tanda: a igual indice de inicio gana la PRIMERA de
    #     la tabla, y al reves "haz clic derecho" se lanzaria como izquierdo ---
    ("copia", Accion("mandar_teclas", ("ctrl+c",))),
    # "pega" y sus variantes (medido en vivo el 2026-10-23: "pégalo" se iba
    # a consultar_ia porque solo existia la orden "pega"; "pega esto" ya
    # casaba con "pega" y no necesita entrada propia)
    ("pega", Accion("mandar_teclas", ("ctrl+v",))),
    ("pegalo", Accion("mandar_teclas", ("ctrl+v",))),
    ("pégalo", Accion("mandar_teclas", ("ctrl+v",))),
    ("cierra esto", Accion("mandar_teclas", ("ctrl+w",))),
    ("alt tab", Accion("mandar_teclas", ("alt+tab",))),
    ("esc", Accion("mandar_teclas", ("esc",))),
    ("escape", Accion("mandar_teclas", ("esc",))),
    ("enter", Accion("mandar_teclas", ("enter",))),
    ("haz clic derecho", Accion("clic", ("derecho",))),
    ("haz clic", Accion("clic", ("izquierdo",))),
    ("da un clic", Accion("clic", ("izquierdo",))),

    # --- FASE E2d, GRUPO 4: informacion del sistema. Todas SIN argumentos:
    #     la ruta del disco y las raices por defecto las pone el MOTOR, que
    #     es el unico que sabe de carpetas (este modulo sigue siendo puro) ---
    ("cuanto espacio queda en el disco", Accion("espacio_disco", ())),
    ("cuanto espacio hay en el disco", Accion("espacio_disco", ())),
    ("espacio en disco", Accion("espacio_disco", ())),
    ("como esta la memoria", Accion("cpu_y_ram", ())),
    ("como esta la ram", Accion("cpu_y_ram", ())),
    ("cuanta ram", Accion("cpu_y_ram", ())),
    ("cuanta memoria", Accion("cpu_y_ram", ())),
    ("que procesos estan abiertos", Accion("procesos", ())),
    ("que procesos hay", Accion("procesos", ())),
    ("lista de procesos", Accion("procesos", ())),

    # --- FASE E2e, GRUPO 5: red y descargas, mas papelera y borrado. Los
    #     interruptores de wifi y de bluetooth viajan con su INTENCION como
    #     argumento ("apagar"/"encender"): hoy el motor se niega a ejecutarlos
    #     (necesitan permisos de administrador, medido), y ese argumento
    #     queda por si un dia se implementan de verdad. "vacia la papelera"
    #     es destructiva y el freno de la confirmacion vive en el MOTOR ---
    ("como esta el wifi", Accion("wifi_estado", ())),
    ("estado del wifi", Accion("wifi_estado", ())),
    ("apaga el wifi", Accion("wifi", ("apagar",))),
    ("enciende el wifi", Accion("wifi", ("encender",))),
    ("como esta el bluetooth", Accion("bluetooth_estado", ())),
    ("estado del bluetooth", Accion("bluetooth_estado", ())),
    ("apaga el bluetooth", Accion("bluetooth", ("apagar",))),
    ("enciende el bluetooth", Accion("bluetooth", ("encender",))),
    ("lee este json", Accion("leer_json", ("este json",))),
    ("cuantos archivos tiene la papelera", Accion("papelera_ver", ())),
    ("vacia la papelera", Accion("papelera_vaciar", ())),
    ("borra la papelera", Accion("papelera_vaciar", ())),

    # --- ordenes con argumento LIBRE (accion None): lo que venga DETRAS en
    #     la frase. Estan declaradas una a una en ORDENES_LIBRES ---
    ("abre esta pagina", None),
    ("abre la url", None),
    ("abre el enlace", None),
    ("abre el archivo", None),
    # "busca en google" va ANTES que "busca": las DOS empiezan en la palabra
    # "busca" de la frase, y a igual indice de inicio gana la PRIMERA de la
    # tabla. Sin este orden, "busca en google como instalar python" se
    # lanzaria como busqueda de ARCHIVOS con el texto "en google como..."
    ("busca en google", None),
    ("busca", None),
    ("buscar", None),
    ("buscame", None),
    ("búscame", None),
    ("pon el brillo", None),
    # FASE E2d: texto libre que se teclea tal cual, coordenadas del raton y
    # el peso de una carpeta (la raiz, si la frase la trae, es la ULTIMA
    # palabra y tiene que ser una de RAICES_BUSQUEDA)
    ("escribe", None),
    ("mover el ratón a", None),
    ("mover el raton a", None),
    ("cuanto pesa", None),
    # FASE E2e, GRUPO 5: "descarga" aparta una "a <raiz>" FINAL de la frase
    # ("descarga esta imagen a documentos"), y "borra" manda a la DESTRUCTIVA
    # borrar_archivo, que no se ejecuta sin confirmar
    ("descarga", None),
    ("borra", None),
    (ORDEN_REPRODUCIR, None),
)

# las ordenes de accion None de arriba: orden -> (nombre de la accion, tipo
# de argumento). SEIS tipos, medidos contra frases reales:
#   "libre"      -> un solo argumento: todo lo que sigue a la orden;
#   "texto_raiz" -> lo mismo, pero si la frase termina en "en <raiz>" eso
#                   ultimo se aparta como raiz ("busca mi factura en
#                   documentos" -> texto "mi factura", raiz "documentos").
#                   Las raices validas estan en RAICES_BUSQUEDA, que es el
#                   MISMO vocabulario que entiende motor.buscar_archivo;
#   "texto_a_raiz"-> igual, pero apartando un "a <raiz>" o "en <raiz>"
#                   FINAL ("descarga esta imagen a documentos" -> url
#                   "esta imagen", carpeta "documentos"); sin ese final se
#                   devuelve solo el texto y el MOTOR pone su carpeta por
#                   defecto (Descargas);
#   "numero"     -> el primer trozo de la frase que sea SOLO digitos
#                   ("pon el brillo a 50" -> resto "a 50" -> 50). Si la
#                   frase no trae ningun numero la orden NO dispara y la
#                   frase sigue su camino: nunca se inventa un numero;
#   "dos_numeros"-> DOS trozos SOLO digitos, en el orden en que se dijeron
#                   ("mover el ratón a 100 200" -> (100, 200)). Con menos de
#                   dos la orden no dispara: la posicion no se adivina;
#   "raiz_final" -> la ULTIMA palabra de la frase, y solo si es una raiz
#                   conocida ("cuanto pesa la carpeta documentos" ->
#                   ("documentos",)). Si no lo es, la orden no dispara y la
#                   frase se va a la IA: ninguna carpeta se inventa.
ORDENES_LIBRES = {
    ORDEN_REPRODUCIR: ("reproduce", "libre"),
    "abre esta pagina": ("abrir_url", "libre"),
    "abre la url": ("abrir_url", "libre"),
    "abre el enlace": ("abrir_url", "libre"),
    "abre el archivo": ("abrir_archivo", "libre"),
    "busca": ("buscar_archivo", "texto_raiz"),
    "buscar": ("buscar_archivo", "texto_raiz"),
    "buscame": ("buscar_archivo", "texto_raiz"),
    "búscame": ("buscar_archivo", "texto_raiz"),
    "pon el brillo": ("brillo", "numero"),
    # FASE E2d, GRUPOS 3 y 4
    "escribe": ("escribir_texto", "libre"),
    "mover el ratón a": ("mover_raton", "dos_numeros"),
    "mover el raton a": ("mover_raton", "dos_numeros"),
    "cuanto pesa": ("peso_de", "raiz_final"),
    # FASE E2e, GRUPO 5
    "busca en google": ("abrir_web", "libre"),
    "descarga": ("descargar", "texto_a_raiz"),
    "borra": ("borrar_archivo", "libre"),
}

# raices de busqueda que el usuario puede decir en voz, con el MISMO nombre
# que usa el motor (motor._RAICES_BUSQUEDA)
RAICES_BUSQUEDA = ("escritorio", "documentos", "descargas")


# signos de puntuacion que el usuario pega a las palabras al hablar o al
# escribir: "gmail?" tiene que casar con la orden "gmail", y "¿abre" con
# "abre". Se quitan SOLO de los extremos de cada palabra (strip), que es
# donde aparecen; el indice de la palabra NO cambia, asi que la busqueda de
# "reproduce" sigue leyendo la frase original con sus signos.
SIGNOS = ".,;:!?¿¡"


def _indice_de(palabra, orden):
    """indice de la PRIMERA palabra donde aparece la orden, o -1 si no esta

    Se comparan palabras COMPLETAS y no substrings: "ahora" no contiene la
    orden "hora", y "youtube" no casa dentro de "youtubeo". Una orden de
    varias palabras ("curso de python dalto") tiene que salir seguida.
    Antes de comparar se limpian los signos puntuacion de cada palabra
    (SIGNOS): sin eso "abre gmail?" no reconocia nada y acababa en la IA.
    """
    trozos = orden.split()
    ancho = len(trozos)
    limpias = [palabra_i.strip(SIGNOS) for palabra_i in palabra]
    for indice in range(len(limpias) - ancho + 1):
        if limpias[indice:indice + ancho] == trozos:
            return indice
    return -1


def _argumentos_libres(tipo, resto):
    """argumentos de una orden con argumento libre (`resto` = lo que sigue),
    o None si la frase no trae lo que la orden pide

    "texto_raiz" aparta un "en <raiz>" FINAL de la frase: "busca mi factura
    en documentos" -> texto "mi factura", raiz "documentos". La raiz viaja
    con su ALIAS y no como ruta porque este modulo no importa os. Si no hay
    "en <raiz>" se devuelve solo el texto y el motor usa sus raices por
    defecto.

    "texto_a_raiz" hace lo mismo con un "a <raiz>" o "en <raiz>" FINAL:
    "descarga esta imagen a documentos" -> ("esta imagen", "documentos").
    Sin ese final se devuelve solo el texto, y ahi el MOTOR de descargar
    pone su carpeta por defecto (Descargas): una url sola tambien es una
    descarga pedida.

    "numero" busca el primer trozo que sea SOLO digitos ("pon el brillo a
    50" -> (50,)); sin ningun numero devuelve None, que interpretar
    traduce en "esta orden no dispara": un brillo sin numero no se
    inventa.

    "dos_numeros" pide DOS trozos SOLO digitos en su orden ("mover el
    ratón a 100 200" -> (100, 200)); con uno solo o con ninguno devuelve
    None: la posicion del raton no se adivina.

    "raiz_final" toma la ULTIMA palabra de la frase y solo si es una raiz
    de RAICES_BUSQUEDA ("cuanto pesa la carpeta documentos" ->
    ("documentos",)); si no lo es devuelve None y la frase se va a la IA:
    ninguna carpeta se inventa.
    """
    if tipo == "numero":
        for trozo in resto.split():
            limpio = trozo.strip(SIGNOS)
            if limpio.isdigit():
                return (int(limpio),)
        return None
    if tipo == "dos_numeros":
        limpios = [parte.strip(SIGNOS) for parte in resto.split()]
        digitos = [int(parte) for parte in limpios if parte.isdigit()]
        if len(digitos) < 2:
            return None
        return (digitos[0], digitos[1])
    if tipo == "raiz_final":
        palabras = resto.split()
        if not palabras:
            return None
        ultima = palabras[-1].strip(SIGNOS)
        if ultima not in RAICES_BUSQUEDA:
            return None
        return (ultima,)
    if tipo == "texto_raiz":
        palabras = resto.split()
        if len(palabras) >= 3 and palabras[-2] == "en" \
                and palabras[-1] in RAICES_BUSQUEDA:
            return (" ".join(palabras[:-2]), palabras[-1])
    if tipo == "texto_a_raiz":
        palabras = resto.split()
        if len(palabras) >= 3 and palabras[-2] in ("a", "en") \
                and palabras[-1] in RAICES_BUSQUEDA:
            return (" ".join(palabras[:-2]), palabras[-1])
    return (resto,)


def interpretar(texto):
    """devuelve la lista de acciones que pide el texto recibido

    NUNCA ejecuta nada: el que ejecuta es main.py. Reglas:
      - una sola frase dispara UNA sola accion; si la frase trae varias
        ordenes, manda la PRIMERA mencionada, que es la que el usuario dijo
        antes (y a igual indice de inicio, la PRIMERA de la tabla ORDENES);
      - el argumento de las ordenes con accion None (ORDENES_LIBRES) es lo
        que viene DETRAS de la orden, sin contarla a ella ni nada anterior;
        si no hay nada detras, la orden no dispara y se devuelve la IA
        (igual si la orden pide un numero y la frase no lo trae: ver
        _argumentos_libres);
      - si no se reconoce ninguna orden, se devuelve una sola accion
        consultar_ia con el texto entero, para que el orquestador se lo pase
        a la IA.
    """
    texto = texto.lower()
    palabra = texto.split()

    # (indice donde empieza la orden en la frase, accion) de la mejor orden
    mejor = None
    for orden, accion in ORDENES:
        indice = _indice_de(palabra, orden)
        if indice < 0:
            continue
        if accion is None:
            libre = ORDENES_LIBRES.get(orden)
            if libre is None:
                continue
            ancho = len(orden.split())
            resto = " ".join(palabra[indice + ancho:])
            if not resto:
                # sin argumento detras no hay orden: no se adivina nada
                continue
            nombre, tipo = libre
            argumentos = _argumentos_libres(tipo, resto)
            if argumentos is None:
                # la frase no trae lo que la orden pide (el numero del
                # brillo): no se inventa nada y la frase sigue su camino
                continue
            accion = Accion(nombre, argumentos)
        if mejor is None or indice < mejor[0]:
            mejor = (indice, accion)

    if mejor is None:
        return [Accion("consultar_ia", (texto,))]

    return [mejor[1]]
