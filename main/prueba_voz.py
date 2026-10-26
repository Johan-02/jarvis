import edge_tts
import asyncio
from playsound import playsound

async def generar_voz(texto, voz, archivo_salida):
    comunicador = edge_tts.Communicate(texto, voz)
    await comunicador.save(archivo_salida)

texto_prueba = "Buenas. Todo en orden por aquí. Estoy listo para lo que necesites."
#voz_elegida = "es-ES-AlvaroNeural"
#voz_elegida = "es-PR-VictorNeural"
#voz_elegida = "es-MX-JorgeNeural"
#voz_elegida = "es-VE-SebastianNeural"
voz_elegida = "es-CO-GonzaloNeural"

asyncio.run(generar_voz(texto_prueba, voz_elegida, "prueba.mp3"))
playsound("prueba.mp3")

