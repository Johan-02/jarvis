# paquete main del asistente Jarvis.
#
# Este archivo hace que main/ sea un paquete REGULAR y no solo un paquete
# "namespace" (sin __init__.py). La diferencia se ve al arrancar el
# asistente como script (python main/main.py): ahí sys.path[0] es la carpeta
# main/, y sin __init__.py el import `from main.reconocedor import ...`
# encontraba PRIMERO el archivo main.py como modulo suelto y revienta con
# "'main' is not a package". Con __init__.py el paquete main/ se encuentra
# como paquete antes de llegar al archivo.
#
# Tiene que quedar VACIO (solo comentarios): importar el paquete no puede
# cargar las librerias de audio ni nada del asistente.
