"""Interfaz visual de voz tipo asistente holográfico para Jarvis.

Ventana flotante, sin bordes, transparente y siempre encima que dibuja un
orbe central que "hincha" (pulso de audio) rodeado de un ecualizador
circular, todo generado en código con tkinter (sin imágenes estáticas).

Diseno de hilos (importante): tkinter NO es seguro entre hilos. Toda llamada
a tkinter (crear ventana, canvas, after, deiconify, withdraw, bind) corre en
el HILO DEL MAINLOOP (un hilo aparte arrancado por iniciar()). El hilo
principal de Jarvis solo escribe dos banderas booleanas (_activa /
_corriendo) y un nivel de audio (_nivel). Asi no hay llamadas cruzadas a
tkinter ni RuntimeError.
"""

import math
import threading
import time

import tkinter as tk
import tkinter.font as tkfont


class InterfazVoz:
    """Ventana con orbe central pulsante y ecualizador circular alrededor."""

    ANCHO = 400
    ALTO = 400
    FONDO = "#000000"              # negro: coincide con el color transparente
    TRANSPARENCIA = "#000000"      # color que la ventana vuelve transparente
    # Colores obligatorios del orbe holografico
    COLOR_NEON = "#0066ff"         # azul neon (anillo exterior / agudos)
    COLOR_ELECTRICO = "#00d4ff"    # azul electrico
    COLOR_CIAN = "#00ffff"         # azul cian (anillo interior / brillo)
    COLOR_CENTRO = "#ffffff"       # punto fijo del centro, blanco puro
    COLOR_TEXTO = "#e0f7ff"        # texto sutil, azul muy claro
    RADIO_MIN = 60                 # radio minimo del orbe (px)
    RADIO_MAX = 120                # radio maximo del orbe (px)
    ANILLOS = 10                   # circulos concéntricos del efecto glow
    BARRAS = 36                    # barras del ecualizador alrededor
    BARRA_MIN_LARGO = 6
    BARRA_MAX_LARGO = 52
    TICK_MS = 40                   # ~25 fps
    FASE_BASE = 0.12               # velocidad del pulso en silencio
    FASE_AUDIO = 0.30              # velocidad extra cuando hay audio

    def __init__(self):
        self.ventana = None
        self.canvas = None
        self._activa = False
        self._corriendo = False
        self._hilo = None
        self._fase = 0.0
        self._nivel = 0.0          # intensidad de audio (0.0 a 1.0)
        self._fuente = None        # se crea en el hilo de tkinter
        # estado del arrastre (solo tocado desde el hilo de tkinter)
        self._arrastrando = False
        self._arrastre_origen_x = 0
        self._arrastre_origen_y = 0
        self._arrastre_ventana_x = 0
        self._arrastre_ventana_y = 0

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
            self._fuente = self._fuente_disponible()
            self.ventana.withdraw()  # inicia oculta; iniciar() la muestra
            self._vincular_arrastre()
            self.ventana.after(self.TICK_MS, self._tick)
            self.ventana.mainloop()
        except tk.TclError:
            # no hay display ni tkinter disponible
            pass
        finally:
            self.ventana = None
            self.canvas = None
            self._fuente = None
            self._activa = False
            self._corriendo = False
            self._arrastrando = False

    def _fuente_disponible(self):
        """Devuelve una fuente sans-serif moderna, con degradacion graceful."""
        candidatos = ["Inter", "Roboto Mono", "Montserrat", "Segoe UI", "Arial"]
        for familia in candidatos:
            prueba = tkfont.Font(family=familia, size=12)
            if prueba.cget("family") == familia:
                return prueba
        return tkfont.Font(family="Segoe UI", size=12)

    def _centrar(self):
        self.ventana.update_idletasks()
        x = (self.ventana.winfo_screenwidth() - self.ANCHO) // 2
        y = (self.ventana.winfo_screenheight() - self.ALTO) // 2
        self.ventana.geometry(
            "%dx%d+%d+%d" % (self.ANCHO, self.ALTO, x, y))

    def _vincular_arrastre(self):
        """Arrastre de la ventana sin bordes desde el orbe azul."""
        canvas = self.canvas
        canvas.bind("<Button-1>", self._iniciar_arrastre)
        canvas.bind("<B1-Motion>", self._mover_arrastre)
        canvas.bind("<ButtonRelease-1>", self._terminar_arrastre)

    def _iniciar_arrastre(self, evento):
        cx = self.ANCHO // 2
        cy = self.ALTO // 2
        dx = evento.x - cx
        dy = evento.y - cy
        # solo se arrastra haciendo clic sobre el orbe (radio max + margen)
        if math.hypot(dx, dy) > self.RADIO_MAX + 24:
            return
        self._arrastrando = True
        self._arrastre_origen_x = evento.x_root
        self._arrastre_origen_y = evento.y_root
        self._arrastre_ventana_x = self.ventana.winfo_x()
        self._arrastre_ventana_y = self.ventana.winfo_y()

    def _mover_arrastre(self, evento):
        if not self._arrastrando:
            return
        nx = self._arrastre_ventana_x + (evento.x_root - self._arrastre_origen_x)
        ny = self._arrastre_ventana_y + (evento.y_root - self._arrastre_origen_y)
        self.ventana.geometry("+%d+%d" % (nx, ny))

    def _terminar_arrastre(self, evento):
        self._arrastrando = False

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
        f = min(max(f, 0.0), 1.0)
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
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

        # --- orbe central: pulso de "hinchazon" ---
        self._fase += self.FASE_BASE + nivel * self.FASE_AUDIO
        pulso = 0.5 + 0.5 * math.sin(self._fase)  # 0.0 .. 1.0
        radio = self.RADIO_MIN + pulso * (self.RADIO_MAX - self.RADIO_MIN)

        # --- orbe central: anillos concéntricos (glow / neon bleed) ---
        # hacia el centro: grosor decreciente y color mas claro
        for k in range(self.ANILLOS):
            fraccion = (self.ANILLOS - k) / self.ANILLOS  # 1 exterior -> 1/N interior
            r = radio * fraccion
            if fraccion > 0.65:
                color = self.COLOR_NEON          # #0066ff exterior
            elif fraccion > 0.30:
                color = self.COLOR_ELECTRICO     # #00d4ff medio
            else:
                color = self.COLOR_CIAN          # #00ffff interior
            grosor = max(1, int(7 * fraccion))
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline=color, width=grosor)

        # punto fijo del centro (pupila del latido), blanco puro
        self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4,
                                fill=self.COLOR_CENTRO, outline="")

        # --- ecualizador circular: barras alrededor del orbe ---
        angulo_paso = 2 * math.pi / self.BARRAS
        for i in range(self.BARRAS):
            angulo = i * angulo_paso
            altura_relativa = 0.5 + 0.5 * math.sin(
                ahora * (0.35 + i * 0.07) + i * 1.3)
            largo = (self.BARRA_MIN_LARGO +
                     altura_relativa * (self.BARRA_MAX_LARGO - self.BARRA_MIN_LARGO) *
                     (0.3 + 0.7 * nivel) * (0.6 + 0.4 * pulso))
            t = i / max(self.BARRAS - 1, 1)
            if t < 0.5:
                color_barra = self._mezclar_color(
                    self.COLOR_ELECTRICO, self.COLOR_CIAN, t * 2)
            else:
                color_barra = self._mezclar_color(
                    self.COLOR_CIAN, self.COLOR_NEON, (t - 0.5) * 2)
            x1 = cx + math.cos(angulo) * radio
            y1 = cy + math.sin(angulo) * radio
            x2 = cx + math.cos(angulo) * (radio + largo)
            y2 = cy + math.sin(angulo) * (radio + largo)
            self.canvas.create_line(x1, y1, x2, y2,
                                    fill=color_barra, width=2, capstyle=tk.ROUND)

        # --- texto sutil en la parte inferior ---
        fuente = self._fuente or tkfont.Font(family="Segoe UI", size=12)
        self.canvas.create_text(
            cx, self.ALTO - 22,
            text="JARVIS",
            fill=self.COLOR_TEXTO,
            font=fuente,
            anchor="s")

    def detener(self):
        """Detiene la animacion y oculta la ventana."""
        self._corriendo = False
        self._activa = False
        self._arrastrando = False
