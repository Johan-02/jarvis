"""Interfaz visual de voz para Jarvis.

Ventana flotante, sin bordes y siempre encima que dibuja ondas circulares
animadas (estilo asistente de voz) mientras Jarvis habla o escucha.

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
    """Ventana con ondas circulares que se expanden desde el centro."""

    ANCHO = 400
    ALTO = 400
    FONDO = "#0a0a0a"  # negro casi total
    # degradado: azul -> purpura -> rosado (colores de Edward)
    COLORES = [
        (0x66, 0x7e, 0xea),  # #667eea azul
        (0x76, 0x4b, 0xa2),  # #764ba2 purpura
        (0xff, 0x69, 0xb4),  # #ff69b4 rosado
    ]
    CANTIDAD_ONDAS = 6
    VELOCIDAD = 1.6  # ciclos de expansion por segundo
    ESPACIADO = 0.15  # desplazamiento de fase entre ondas
    RADIO_MAX = 170
    TICK_MS = 30  # ~33 fps

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

    def _color_onda(self, fase):
        """Interpola el color de una onda segun su fase (0 a 1) en el
        degradado azul -> purpura -> rosado."""
        n = len(self.COLORES) - 1
        i = fase * n
        i0 = min(int(i), n - 1)
        i1 = min(i0 + 1, n)
        f = i - i0
        c0 = self.COLORES[i0]
        c1 = self.COLORES[i1]
        r = int(c0[0] + (c1[0] - c0[0]) * f)
        g = int(c0[1] + (c1[1] - c0[1]) * f)
        b = int(c0[2] + (c1[2] - c0[2]) * f)
        return "#%02x%02x%02x" % (r, g, b)

    def _dibujar_frame(self):
        if self.canvas is None or not self._corriendo:
            return
        try:
            self.canvas.delete("all")
        except tk.TclError:
            self._corriendo = False
            return
        cx = self.ANCHO // 2
        cy = self.ALTO // 2
        ahora = time.time()
        for indice in range(self.CANTIDAD_ONDAS):
            fase = (ahora * self.VELOCIDAD + indice * self.ESPACIADO) % 1.0
            radio = fase * self.RADIO_MAX
            if radio < 6:
                continue
            grosor = max(1, int(5 * (1.0 - fase)))
            color = self._color_onda(fase)
            try:
                self.canvas.create_oval(
                    cx - radio, cy - radio,
                    cx + radio, cy + radio,
                    outline=color, width=grosor)
            except tk.TclError:
                self._corriendo = False
                return

    def detener(self):
        """Detiene la animacion y oculta la ventana."""
        self._corriendo = False
        self._activa = False
