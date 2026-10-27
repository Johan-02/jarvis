"""Interfaz visual de voz para Jarvis.

Ventana flotante, sin bordes, transparente y siempre encima que dibuja un
circulo central que "hincha" (se expande y contrae como un latido) y una
serie de barras alrededor (ecualizador circular) mientras Jarvis habla o
escucha. El pulso representa la intensidad del audio.

Diseno de hilos (importante): tkinter NO es seguro entre hilos. Toda llamada
a tkinter (crear ventana, canvas, after, deiconify, withdraw) corre en el
HILO DEL MAINLOOP (un hilo aparte arrancado por iniciar()). El hilo principal
de Jarvis solo escribe dos banderas booleanas (_activa / _corriendo) y un
nivel de audio (_nivel). Asi no hay llamadas cruzadas a tkinter ni
RuntimeError.
"""

import math
import threading
import time

import tkinter as tk


class InterfazVoz:
    """Ventana con circulo central que pulsa y ecualizador circular alrededor."""

    ANCHO = 400
    ALTO = 400
    FONDO = "#000000"              # negro: coincide con el color transparente
    TRANSPARENCIA = "#000000"      # color que la ventana vuelve transparente
    COLOR_CENTRO = "#1e90ff"       # azul del circulo central (dodgerblue)
    COLOR_CENTRO_SUAVE = "#4da6ff" # azul mas claro para el degradado
    COLOR_BARRA_BAJA = "#1e90ff"   # azul para los graves
    COLOR_BARRA_ALTA = "#8a2be2"   # azul-purpura para los agudos
    RADIO_MIN = 60                 # radio minimo del circulo central (px)
    RADIO_MAX = 100                # radio maximo del circulo central (px)
    BARRAS = 36                    # barras del ecualizador alrededor del circulo
    BARRA_MIN_LARGO = 6            # longitud minima de cada barra (px)
    BARRA_MAX_LARGO = 52           # longitud maxima de cada barra (px)
    TICK_MS = 40                   # ~25 fps
    FASE_BASE = 0.12               # velocidad del pulso en silencio
    FASE_AUDIO = 0.30              # velocidad extra cuando hay audio

    def __init__(self):
        self.ventana = None
        self.canvas = None
        self._activa = False
        self._corriendo = False
        self._hilo = None
        self._fase = 0.0           # fase del pulso del circulo central
        self._nivel = 0.0          # intensidad de audio simulada (0.0 a 1.0)

    def esta_activa(self):
        """Devuelve True si la ventana esta visible y animando."""
        return self._activa

    def establecer_nivel(self, nivel):
        """Fija la intensidad de audio simulada (0.0 silencioso, 1.0 fuerte).

        Se llama desde el hilo principal de Jarvis; solo guarda el valor,
        nunca toca tkinter directamente.
        """
        try:
            self._nivel = min(max(float(nivel), 0.0), 1.0)
        except (TypeError, ValueError):
            self._nivel = 0.0

    def iniciar(self):
        """Muestra la ventana y arranca la animacion en un hilo aparte.

        El hilo creado aqui es el unico que toca tkinter. Si no hay display
        disponible, tk.Tk() lanza TclError, se captura y Jarvis sigue sin la
        ventana visual (no se rompe nada).
        """
        if self._activa:
            return
        self._activa = True
        self._corriendo = True
        if self._hilo is None or not self._hilo.is_alive():
            self._hilo = threading.Thread(target=self._ejecutar, daemon=True)
            self._hilo.start()

    def _ejecutar(self):
        """Corre en el hilo aparte: es el dueño de tkinter y del mainloop."""
        try:
            self.ventana = tk.Tk()
            self.ventana.overrideredirect(True)  # sin bordes ni barra de titulos
            self.ventana.attributes("-topmost", True)  # siempre encima
            self.ventana.attributes("-transparentcolor", self.TRANSPARENCIA)
            self.ventana.configure(bg=self.FONDO)
            # NO focus_force ni grab_set: la ventana no roba el foco del
            # teclado, asi Jarvis sigue escuchando por el microfono.
            try:
                self.ventana.attributes("-toolwindow", True)  # Windows: sin icono en la barra
            except tk.TclError:
                pass
            self.canvas = tk.Canvas(
                self.ventana,
                width=self.ANCHO,
                height=self.ALTO,
                bg=self.FONDO,
                highlightthickness=0)
            self.canvas.pack()
            self._centrar()
            self.ventana.withdraw()  # inicia oculta; iniciar() la muestra
            self.ventana.after(self.TICK_MS, self._tick)
            self.ventana.mainloop()
        except tk.TclError:
            # no hay display ni tkinter disponible
            pass
        finally:
            self.ventana = None
            self.canvas = None
            self._activa = False
            self._corriendo = False

    def _centrar(self):
        self.ventana.update_idletasks()
        x = (self.ventana.winfo_screenwidth() - self.ANCHO) // 2
        y = (self.ventana.winfo_screenheight() - self.ALTO) // 2
        self.ventana.geometry(
            "%dx%d+%d+%d" % (self.ANCHO, self.ALTO, x, y))

    def _tick(self):
        """Bucle de animacion: corre en el hilo de mainloop via after()."""
        try:
            if self.ventana is None:
                return
            if self._activa:
                self.ventana.deiconify()
                if self._corriendo:
                    self._dibujar_frame()
                else:
                    self.canvas.delete("all")
            else:
                self.ventana.withdraw()
            self.ventana.after(self.TICK_MS, self._tick)
        except tk.TclError:
            pass

    @staticmethod
    def _mezclar_color(c1, c2, f):
        """Interpola dos colores hex RRGGBB. f en 0.0 (c1) a 1.0 (c2)."""
        r1, g1, b1 = (int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16))
        r2, g2, b2 = (int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16))
        r = int(r1 + (r2 - r1) * f)
        g = int(g1 + (g2 - g1) * f)
        b = int(b1 + (b2 - b1) * f)
        return "#%02x%02x%02x" % (r, g, b)

    def _dibujar_frame(self):
        if self.canvas is None or not self._corriendo:
            return
        try:
            self.canvas.delete("all")
        except tk.TclError:
            self._corriendo = False
            return

        ahora = time.time()
        cx = self.ANCHO // 2
        cy = self.ALTO // 2
        nivel = self._nivel  # 0.0 .. 1.0, intensidad de audio simulada

        # --- circulo central: pulso de "hinchazon" ---
        self._fase += self.FASE_BASE + nivel * self.FASE_AUDIO
        pulso = 0.5 + 0.5 * math.sin(self._fase)  # 0.0 .. 1.0
        radio = self.RADIO_MIN + pulso * (self.RADIO_MAX - self.RADIO_MIN)

        # degradado azul: varios ovales concéntricos, mas fuerte en el centro
        pasos = 5
        for k in range(pasos, 0, -1):
            fraccion = k / pasos
            r = radio * fraccion
            color = self._mezclar_color(
                self.COLOR_CENTRO, self.COLOR_CENTRO_SUAVE, 1.0 - fraccion)
            grosor = max(1, int(4 * fraccion))
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline=color, width=grosor)

        # punto fijo en el centro (pupila del latido)
        self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                                fill=self.COLOR_CENTRO, outline="")

        # --- ecualizador circular: barras alrededor del circulo ---
        angulo_paso = 2 * math.pi / self.BARRAS
        for i in range(self.BARRAS):
            angulo = i * angulo_paso
            # cada barata oscila a su propia frecuencia (graves/medios/agudos)
            altura_relativa = 0.5 + 0.5 * math.sin(
                ahora * (0.4 + i * 0.09) + i * 1.7)
            # se hincha con el pulso central y con el nivel de audio
            largo = (self.BARRA_MIN_LARGO +
                     altura_relativa * (self.BARRA_MAX_LARGO - self.BARRA_MIN_LARGO) *
                     (0.35 + 0.65 * nivel) * (0.7 + 0.3 * pulso))
            # color segun la posicion: azul (graves) -> azul-purpura (agudos)
            color_barra = self._mezclar_color(
                self.COLOR_BARRA_BAJA, self.COLOR_BARRA_ALTA,
                i / max(self.BARRAS - 1, 1))
            # inicio en el borde del circulo central, fin hacia afuera
            x1 = cx + math.cos(angulo) * radio
            y1 = cy + math.sin(angulo) * radio
            x2 = cx + math.cos(angulo) * (radio + largo)
            y2 = cy + math.sin(angulo) * (radio + largo)
            self.canvas.create_line(x1, y1, x2, y2,
                                    fill=color_barra, width=2, capstyle=tk.ROUND)

    def detener(self):
        """Detiene la animacion y oculta la ventana."""
        self._corriendo = False
        self._activa = False
