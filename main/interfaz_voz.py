"""Interfaz visual de voz para Jarvis.

Ventana flotante, sin bordes, transparente y siempre encima que dibuja un
visualizador circular AZUL (ondas concéntricas que se expanden desde el
centro como ondas de sonido en el agua) mientras Jarvis habla o escucha.

Diseno de hilos (importante): tkinter NO es seguro entre hilos. Toda llamada
a tkinter (crear ventana, canvas, after, deiconify, withdraw) corre en el
HILO DEL MAINLOOP (un hilo aparte arrancado por iniciar()). El hilo principal
de Jarvis solo escribe dos banderas booleanas (_activa / _corriendo) y arranca
ese hilo. Asi no hay llamadas cruzadas a tkinter ni RuntimeError.
"""

import threading
import time

import tkinter as tk


class InterfazVoz:
    """Ventana con ondas circulares azules que se expanden desde el centro."""

    ANCHO = 400
    ALTO = 400
    FONDO = "#000000"            # negro: coincide con el color transparente
    TRANSPARENCIA = "#000000"    # color que la ventana vuelve transparente
    COLOR_ONDA = "#1e90ff"       # azul predominante (dodgerblue)
    COLOR_ONDA_CLARA = "#6fb4ff" # azul mas claro hacia el borde de la onda
    CANTIDAD_ONDAS = 6           # cuantas ondas concurrentes maximo
    VELOCIDAD = 90.0             # px por segundo de expansion
    TICK_MS = 30                 # ~33 fps
    RADIO_MAX = 170              # radio maximo de una onda (px)
    RADIO_MIN = 14               # radio donde nace una nueva onda (px)
    GROSOR = 3                   # grosor de la linea de cada onda (px)
    NACE_CADA_SEGUNDOS = 0.5     # periodo entre el nacimiento de ondas

    def __init__(self):
        self.ventana = None
        self.canvas = None
        self._activa = False
        self._corriendo = False
        self._hilo = None
        self._ondas = []         # lista de dicts {radio}
        self._ultima_nacimiento = 0.0

    def esta_activa(self):
        """Devuelve True si la ventana esta visible y animando."""
        return self._activa

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
        self._ultima_nacimiento = time.time()
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

    def _nacer_onda(self):
        """Crea una nueva onda en el centro si hay espacio."""
        if len(self._ondas) < self.CANTIDAD_ONDAS:
            self._ondas.append({"radio": float(self.RADIO_MIN)})

    def _color_onda(self, radio):
        """Azul fuerte al nacer que se vuelve mas claro al crecer."""
        proporcion = (radio - self.RADIO_MIN) / (self.RADIO_MAX - self.RADIO_MIN)
        proporcion = min(max(proporcion, 0.0), 1.0)
        return self._mezclar_color(self.COLOR_ONDA, self.COLOR_ONDA_CLARA, proporcion)

    @staticmethod
    def _mezclar_color(c1, c2, f):
        """interpola dos colores hex RRGGBB"""
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
        paso = self.VELOCIDAD * (self.TICK_MS / 1000.0)
        vivas = []
        for onda in self._ondas:
            radio = onda["radio"] + paso
            if radio > self.RADIO_MAX:
                continue  # la onda murio, no se conserva
            try:
                self.canvas.create_oval(
                    cx - radio, cy - radio,
                    cx + radio, cy + radio,
                    outline=self._color_onda(radio),
                    width=self.GROSOR)
            except tk.TclError:
                self._corriendo = False
                return
            vivas.append({"radio": radio})
        self._ondas = vivas
        if ahora - self._ultima_nacimiento >= self.NACE_CADA_SEGUNDOS:
            self._nacer_onda()
            self._ultima_nacimiento = ahora
        if not self._ondas:
            self._nacer_onda()

    def detener(self):
        """Detiene la animacion y oculta la ventana."""
        self._corriendo = False
        self._activa = False
