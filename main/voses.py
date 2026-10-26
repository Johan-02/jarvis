import pyttsx3
motor = pyttsx3.init()
voces = motor.getProperty('voices')
for voz in voces:
    print(voz.id, voz.name, voz.languages, voz.gender, voz.age)
    



