"""
Genera los iconos de estado del monitoreo con la paleta del proyecto.

Se dibujan por codigo en vez de retocar los PNG originales porque asi:
  - el fondo queda TRANSPARENTE de verdad, sin el recuadro blanco/gris que
    traian los originales y que se notaba sobre el tema oscuro
  - los colores salen exactos de la paleta, sin aproximar a ojo
  - se pueden regenerar en cualquier tamano si hace falta

Se dibuja a 4x y se reduce con LANCZOS: es la forma barata de conseguir
bordes suaves sin que el cuadrado redondeado salga dentado.
"""
from PIL import Image, ImageDraw

LADO = 160
SUPER = 4


def icono(color_fondo, color_simbolo, destino,
          hueco=50, largo=0.40, grosor_rel=0.080):
    n = LADO * SUPER
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))     # fondo transparente
    d = ImageDraw.Draw(im)

    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.30),
                        fill=color_fondo)

    margen = int(n * 0.30)
    grosor = int(n * grosor_rel)
    caja = [margen, margen, n - margen, n - margen]

    # Anillo con un hueco arriba. En PIL el angulo 0 son las 3 en punto y
    # crece en sentido horario, asi que las 12 son 270 grados.
    mitad = hueco / 2.0
    d.arc(caja, start=270 + mitad, end=270 - mitad + 360,
          fill=color_simbolo, width=grosor)

    # Barra vertical: baja desde arriba y se queda por encima del centro del
    # anillo, para que no se toquen y el simbolo se lea limpio.
    cx = n // 2
    arriba, abajo = int(n * 0.205), int(n * largo)
    d.line([(cx, arriba), (cx, abajo)], fill=color_simbolo, width=grosor)
    r = grosor // 2
    for y in (arriba, abajo):
        d.ellipse([cx - r, y - r, cx + r, y + r], fill=color_simbolo)

    im.resize((LADO, LADO), Image.LANCZOS).save(destino)


OSCURO = "#17121B"      # paleta
CLARO = "#FCFEFD"       # paleta

# Pareja que usa la aplicacion: los mismos colores que los textos UP y DOWN
icono("#3FD69A", OSCURO, "on.png")
icono("#F9018B", CLARO, "off.png")

# Pareja alternativa, colores 100% de la paleta candy scar
icono("#97C9EC", OSCURO, "alt_on.png")
icono("#F9018B", CLARO, "alt_off.png")
