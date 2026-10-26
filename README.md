# 🎤 Jarvis - Asistente de voz en español para Windows

**Qué es:** un asistente de voz en español que corre en Windows, en Python.
Escucha el microfono, traduce lo que se habla en ordenes y las ejecuta en la
propia PC. **No tiene servidor ni pagina web**: todo pasa en el escritorio.
La voz sale con edge-tts (GonzaloNeural) y el reconocimiento de voz usa el
servicio gratis de Google (es-ES), asi que oir y hablar SI necesitan internet.

**Estado:** en desarrollo
**Última actualización:** 25 de octubre de 2026

---

## 📋 Requisitos reales

- **Windows** (el motor usa ctypes, winreg y pywin32: APIs de Windows).
- **Python 3.14.7**, la version con la que esta probado todo el proyecto.
- `pip install -r requirements.txt`

**pywhatkit NO esta en requirements.txt y no se puede instalar en Python
3.14**: su setup.py de 2022 usa distutils, que Python elimino, y el build
revienta con `FileNotFoundError: requirements.txt`. Por eso el comando
"reproduce" abre la busqueda de YouTube con `webbrowser` de la stdlib, sin
instalar nada.

## 🚀 Cómo se ejecuta

```bash
python main/main.py                   # pide el microfono y se queda escuchando
python -m unittest discover -s tests  # corre la suite: hoy 291 pruebas
```

Al arrancar, si `memoria.json` aun no tiene nombre, Jarvis lo pregunta una vez
y lo guarda. Para despertarlo: "jarvis despierta", "hey jarvis", "arriba
jarvis", etc.

## 🔐 Variables de entorno

Jarvis lee **una sola** variable, desde `main/.env`:

```
ANTHROPIC_API_KEY=   # clave de la API de Anthropic (Claude)
```

Copia `.env.example` a `main/.env` y rellena la clave. **Nunca commitees el
`.env` a git.**

## 🧠 El motor: 41 acciones en 5 grupos

GRUPO 1 archivos/programas: abrir_archivo, abrir_app, abrir_url, buscar_archivo,
  cerrar_app, cerrar_app_forzado [DESTRUCTIVA]
GRUPO 2 el equipo: apagar [DESTRUCTIVA], bloquear, brillo, cancelar_apagado,
  capturar_pantalla, mirar_pantalla, reiniciar [DESTRUCTIVA], silenciar,
  suspender [DESTRUCTIVA], volumen_bajar, volumen_leer, volumen_subir
GRUPO 3 escribir/teclear: clic, escribir_texto, mandar_teclas, mover_raton
GRUPO 4 info del sistema: bateria, cpu_y_ram, espacio_disco, peso_de, procesos
GRUPO 5 red/descargas: abrir_web, bluetooth, bluetooth_estado, descargar,
  leer_json, wifi, wifi_estado
MEMORIA (Fase H3): leer_memoria, recordar, olvidar [DESTRUCTIVA]
PAPELERA/BORRADO: borrar_archivo [DESTRUCTIVA], papelera_ver, papelera_vaciar
  [DESTRUCTIVA]
BASE: hora_actual

## 🤖 Claude decide: el bucle agente (Fase G)

**Con clave**, Jarvis no se limita a reconocer palabras clave: **Claude
decide** qué hacer y actúa con las herramientas del motor. El ciclo, en
lenguaje claro (Fases G2/G3):

1. Claude recibe la frase de Edward **junto con las herramientas del motor**.
   Las genera `main/herramientas.py` **a partir de `motor.REGISTRO`**, sin
   ningún catálogo propio: quien agrega una acción al registro obtiene su
   herramienta sola, sin tocar el generador.
2. Si Claude pide una herramienta, **el motor la ejecuta de verdad** y su
   resultado se le devuelve como respuesta; Claude **ve el resultado y
   sigue** (pide otra herramienta o responde con el texto final, que es lo
   que se le dice a Edward en voz).
3. **Tope: 5 iteraciones.** Al llegar, Jarvis avisa honestamente que cortó
   por seguridad, en vez de inventarse una respuesta.

**Memoria de conversación (Fase H1).** El bucle arrastra los últimos turnos
para que Jarvis entienda referencias ("y ahora la luz", "hazlo otra vez").
Tiene **dos topes** (`TOPE_MENSAJES_HISTORIAL = 20` y
`TOPE_CARACTERES_HISTORIAL = 12000`, ~3.000 tokens de entrada por llamada):
sin tope, una charla larga se vuelve cada vez más cara y más lenta. El
historial se **limpia al llegar una palabra de activación nueva**, para que
una sesión no herede la charla de otra. La estructura que viaja a la API se
valida antes de guardarse (`_turno_valido`): cada `tool_use` exige su
`tool_result` en el mensaje siguiente y los roles alternan; un turno
incompleto haría que la API devolviera 400 y Jarvis se rompiera.

**Ver la pantalla (Fase H2).** `mirar_pantalla` captura con GDI y convierte
**BMP → PNG en Python puro** (`zlib` + `struct`), porque la API no acepta BMP.
Reduce a la mitad (1 de cada 2 píxeles, ancho máximo 1280): una pantalla de
1366×768 queda en **683×384 y ~0,10 MB**, validada con un decodificador
independiente (.NET la abre). El `tool_result` lleva un bloque `image` con
`media_type: image/png` en base64. **La imagen SOLO sale cuando el modelo
decide mirar** (Edward pregunta algo que necesita ver la pantalla); no se
manda en cada turno, porque el contenido de la pantalla viaja a Anthropic.

**Aprender (Fase H3).** `recordar` agrega una nota a `memoria.json` y
`olvidar` [DESTRUCTIVA] la quita; `leer_memoria` solo lee. Detalles que
importan:
- El motor **no sabe dónde vive el archivo**: `main.py` le conecta su lector
  estricto y su escritura atómica (Fase B, B9).
- **Antes de escribir se valida el JSON.** Si `memoria.json` está roto,
  `recordar` devuelve `ok=False` y **no toca el archivo**: sin esa comprobación
  se reemplazaría por una memoria vacía y las notas de Edward se perderían
  para siempre. Probado con un archivo roto de verdad (queda bit a bit igual).
- Evita duplicados exactos (espacios colapsados).
- Tras un `recordar`/`olvidar`, el bucle **relee la memoria y rehace el system
  prompt** antes de la llamada siguiente (`motor.CAMBIAN_MEMORIA`): si no,
  Jarvis guardaría la nota y seguiría sin saberla en ese mismo turno.

**Modelo actual: `claude-haiku-5-5`** (constante `MODELO_IA` en `main.py`).
El haiku-4.5 tenía su retiro garantizado el 15/10/2026 y esa fecha ya pasó;
el 5.5 es el actual y el más barato. Cambiar de modelo es UNA línea.

**Sin `ANTHROPIC_API_KEY` (o con la API caída), Jarvis sigue funcionando**:
cae a **las 41 acciones por palabras clave** de siempre, **sin llamar a la
IA en ningún punto**. Eso está probado (Fase G3) y hace que el modo offline
no dependa de red ni de clave.

## 🛡️ Seguridad: el freno esta DENTRO del motor

- **7 acciones destructivas**: `apagar`, `reiniciar`, `suspender`,
  `papelera_vaciar`, `borrar_archivo`, `cerrar_app_forzado` y `olvidar` (una
  nota de la memoria). Sin decir "si",
  `ejecutar()` devuelve `REQUIERE_CONFIRMACION` y **NO ejecuta NADA**: ni un
  subprocess, ni un os., ni un ctypes. El freno va ANTES de buscar la funcion
  en el registro.
- **`borrar_archivo` es IRREVERSIBLE**: usa `os.remove`, que NO pasa por la
  papelera de reciclaje; el resultado lo dice explicitamente
  ("IRREVERSIBLE: no paso por la papelera").
- **La Fase G respeta ese freno (seguridad G2/G3):**
  - **`confirmado` NO aparece en NINGÚN `input_schema`**: el modelo no puede
    saltarse la confirmación. Lo garantiza `tests/test_herramientas.py`,
    que recorre TODAS las herramientas sin lista escrita a mano.
  - **El que pregunta por VOZ es el programa, NO el modelo**: cuando el motor
    frena una destructiva, es Jarvis quien le pregunta a Edward con su voz y
    escucha la respuesta por micrófono (nunca el teclado).
  - **Si Jarvis no entiende la respuesta (silencio, error, sin internet),
    RECHAZA**: un silencio JAMÁS vale como "sí". **Reintentos: 2** (3
    intentos en total); al agotarlos no ejecuta nada y lo dice claro.

## ⚠️ Límites honestos (medidos, no inventados)

- **Encender o apagar WiFi y Bluetooth no se puede**: exige permisos de
  administrador (medido con `IsUserAnAdmin = 0`). Las acciones `wifi` y
  `bluetooth` existen pero SIEMPRE devuelven `ok=False` diciendolo; nunca
  fingen que lo apagaron. Lo que SI se puede es LEER el estado
  (`wifi_estado`, `bluetooth_estado`).
- **`brillo` solo funciona en pantallas que lo soportan**: en un monitor
  externo la clase WMI (`WmiMonitorBrightnessMethods`) viene vacia y la accion
  devuelve `ok=False` con su motivo.
- **`escribir_texto` usa portapapeles + Ctrl+V a proposito**:
  `pyautogui.write` descarta en silencio `á é í ó ú ñ ¿` y mutilaria el texto
  de voz.
- **`mandar_teclas` NO se probo inyectando teclas reales**: inyectar escribiria
  en lo que el usuario tenga enfrente. Lo comprobado es la estructura del
  paquete que sale (medida con ctypes), no el teclado fisico.
- **`buscar_archivo` y `peso_de` estan acotados** a Escritorio/Documentos/
  Descargas por defecto: recorrer `C:\` entero tardo ~59 s medido.
- **`procesos` limita la salida** a 40 lineas.
- **`papelera_vaciar` y `borrar_archivo` nunca se probaron en vivo.**
- **Sin `ANTHROPIC_API_KEY` valida, Jarvis sigue siendo útil**: cae a las
  **41 acciones por palabras clave** de siempre, **sin llamar a la IA en
  ningun punto**; lo que no sea una orden conocida va a `consultar_ia`, y
  ahi SI necesita la clave. El motor de acciones NO la necesita: es
  stdlib/ctypes (mas pywin32).

## 🧪 Cómo se prueba

```bash
python -m unittest discover -s tests
```

Hoy son **291 pruebas**. No abren microfono ni hablan: leen el codigo como
texto o como AST, y las del motor usan dobles en vez de tocar el equipo real.

## 📁 Estructura

```
jarvis/
├── main/             ← main.py (bucle de voz), motor.py (41 acciones),
│                       herramientas.py (genera las herramientas de la API
│                       de Claude A PARTIR de REGISTRO, sin duplicar
│                       catalogos), reconocedor.py (texto -> acciones) y
│                       main/.env
├── tests/            ← las 291 pruebas (unittest)
├── requirements.txt  ← dependencias reales del proyecto
├── .env.example      ← plantilla de la unica variable de entorno
├── memoria.json      ← memoria del asistente (nombre y notas)
└── *.mp3 / *.wav     ← audios VIEJOS; hoy la voz y los wavs de grabacion
                        van al temp del sistema y se borran solos
```
