"""Interfaz visual de voz para Jarvis.

Ventana flotante, sin bordes y siempre encima que dibuja un ecualizador de
barras verticales (estilo visualizador de musica) mientras Jarvis habla o
escucha. Las barras suben y bajan de forma organica, como si "vivieran" con
el audio, en el degradado azul -> purpura -> rosado de Edward.

Diseno de hilos (importante): tkinter NO es seguro entre hilos. Toda llamada
a tkinter (crear ventana, canvas, after, deiconify, withdraw) corre en el
HILO DEL MAINLOOP (un hilo aparte arrancado por iniciar()). El hilo principal
de Jarvis solo escribe dos banderas booleanas (_activa / _corriendo) y arranca
ese hilo. Asi no hay llamadas cruzadas a tkinter ni RuntimeError.
"""

import threading
import time
import math

import tkinter as tk


class InterfazVoz:
    """Ventana con barras verticales de ecualizador que suben y bajan."""

    ANCHO = 400
    ALTO = 400
    FONDO = "#000000"  # negro total: solo se ven las barras animadas
    # degradado: azul -> purpura -> rosado (colores de Edward)
    COLORES = [
        "#667eea",  # azul
        "#764ba2",  # purpura
        "#ff69b4",  # rosado
    ]
    CANTIDAD_BARRAS = 24
    ANCHO_BARRA = 6
    ESPACIO_BARRA = 9
    VELOCIDAD = 3.0  # ciclos de subida/bajada por segundo
    TICK_MS = 30  # ~33 fps
    ALTURA_MIN = 12  # altura minima de cada barra (siempre visible)
    ALTURA_MAX = 170  # altura maxima de una barra

    def __init__(self):
        self.ventana = None
        self.canvas = None
        self._activa = False
        self._corriendo = False
        self._hilo = None

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
        if self._hilo is None or not self._hilo.is_alive():
            self._hilo = threading.Thread(target=self._ejecutar, daemon=True)
            self._hilo.start()

    def _ejecutar(self):
        """Corre en el hilo aparte: es el dueño de tkinter y del mainloop."""
        try:
            self.ventana = tk.Tk()
            self.ventana.overrideredirect(True)  # sin bordes ni barra de titulos
            self.ventana.attributes("-topmost", True)  # siempre encima
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

    def _color_barra(self, indice):
        """Color de una barra segun su indice en el degradado azul->purpura->rosado."""
        if self.CANTIDAD_BARRAS > 1:
            i = indice * (len(self.COLORES) - 1) / (self.CANTIDAD_BARRAS - 1)
        else:
            i = 0.0
        n = len(self.COLORES) - 1
        i0 = min(int(i), n - 1) if n > 0 else 0
        i1 = min(i0 + 1, n)
        f = i - i0
        c0 = self._hex_a_rgb(self.COLORES[i0])
        c1 = self._hex_a_rgb(self.COLORES[i1])
        r = int(c0[0] + (c1[0] - c0[0]) * f)
        g = int(c0[1] + (c1[1] - c0[1]) * f)
        b = int(c0[2] + (c1[2] - c0[2]) * f)
        return "#%02x%02x%02x" % (r, g, b)

    @staticmethod
    def _hex_a_rgb(hex_color):
        """convierte "#RRGGBB" en (R, G, B)"""
        return (int(hex_color[1:3], 16),
                int(hex_color[3:5], 16),
                int(hex_color[5:7], 16))

    def _dibujar_frame(self):
        if self.canvas is None or not self._corriendo:
            return
        try:
            self.canvas.delete("all")
        except tk.TclError:
            self._corriendo = False
            return
        ahora = time.time()
        # posicion inicial centrada: las barras ocupan el centro de la ventana
        total_ancho = (self.CANTIDAD_BARRAS * self.ANCHO_BARRA +
                       (self.CANTIDAD_BARRAS - 1) * self.ESPACIO_BARRA)
        x_inicio = (self.ANCHO - total_ancho) // 2
        base_y = self.ALTO - 40  # las barras crecen hacia arriba desde aqui
        for indice in range(self.CANTIDAD_BARRAS):
            # cada barra tiene su propia fase para que el ecualizador parezca
            # organico, no un bloque que sube y baja todo junto
            fase = (ahora * self.VELOCIDAD + indice * 0.75) % (2 * math.pi)
            # altura oscilante entre ALTURA_MIN y ALTURA_MAX
            altura = self.ALTURA_MIN + int(
                (self.ALTURA_MAX - self.ALTURA_MIN) *
                (0.5 + 0.5 * math.sin(fase)))
            x = x_inicio + indice * (self.ANCHO_BARRA + self.ESPACIO_BARRA)
            color = self._color_barra(indice)
            try:
                self.canvas.create_rectangle(
                    x, base_y - altura,
                    x + self.ANCHO_BARRA, base_y,
                    fill=color, outline="")
            except tk.TclError:
                self._corriendo = False
                return

    def detener(self):
        """Detiene la animacion y oculta la ventana."""
        self._corriendo = False
        self._activa = False
