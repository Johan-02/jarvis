from pydub import AudioSegment
from pydub.effects import low_pass_filter, high_pass_filter
from playsound import playsound

# cargamos el audio que ya generamos antes con edge-tts
audio = AudioSegment.from_mp3("respuesta.mp3")

# aplicamos un filtro que corta los graves y los agudos extremos
# esto simula el sonido de un altavoz pequeño, como de radio
audio_filtrado = low_pass_filter(audio, 3000)  # corta frecuencias muy altas
audio_filtrado = high_pass_filter(audio_filtrado, 300)  # corta frecuencias muy bajas

# guardamos el resultado
audio_filtrado.export("prueba_efecto.mp3", format="mp3")

playsound("prueba_efecto.mp3")

