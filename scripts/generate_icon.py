"""Gera assets/app-icon.png (RGBA) e assets/app-icon.ico a partir do desenho original.

Ferramenta de desenvolvimento: usa o Pillow que ja vem na .venv junto com o
PyInstaller. O sistema em si continua usando apenas a biblioteca padrao.

    .\\.venv\\Scripts\\python.exe scripts\\generate_icon.py

- Recorta o fundo azul arredondado com transparencia (o PNG original e RGB,
  com cantos brancos que apareciam na barra de tarefas escura).
- 32 px ou mais: desenho completo (pastas, lupa, ZIP e visto).
- 16 e 24 px: versao simplificada (pasta + lupa), legivel em tamanho pequeno.
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PNG_PATH = ROOT / "assets" / "app-icon.png"
ICO_PATH = ROOT / "assets" / "app-icon.ico"

# Geometria do fundo arredondado no PNG de 1254 px (medida no desenho original).
# A mascara fica 4 px para dentro para remover a franja clara da borda.
SHAPE_BOX = (45, 27, 1203, 1202)
SHAPE_RADIUS = 272
SUPERSAMPLE = 4

FULL_SIZES = (32, 48, 64, 128, 256)
SMALL_SIZES = (16, 24)


def rounded_mask(size, box, radius):
    """Mascara anti-serrilhada (desenhada maior e reduzida)."""
    big = Image.new("L", (size[0] * SUPERSAMPLE, size[1] * SUPERSAMPLE), 0)
    scaled_box = tuple(value * SUPERSAMPLE for value in box)
    ImageDraw.Draw(big).rounded_rectangle(scaled_box, radius * SUPERSAMPLE, fill=255)
    return big.resize(size, Image.LANCZOS)


def transparent_full_icon(source):
    """Desenho completo com cantos transparentes, recortado e centralizado."""
    image = source.convert("RGBA")
    image.putalpha(rounded_mask(image.size, SHAPE_BOX, SHAPE_RADIUS))
    left, top, right, bottom = SHAPE_BOX
    cropped = image.crop((left, top, right + 1, bottom + 1))
    side = max(cropped.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return square


def simplified_icon(size):
    """Pasta amarela + lupa sobre o mesmo fundo azul, para 16/24 px."""
    canvas = 256
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Fundo azul em degrade vertical, nas cores do icone completo.
    gradient = Image.new("RGBA", (canvas, canvas))
    top_color, bottom_color = (18, 88, 205), (5, 46, 132)
    for y in range(canvas):
        t = y / (canvas - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top_color, bottom_color))
        ImageDraw.Draw(gradient).line([(0, y), (canvas, y)], fill=color + (255,))
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, canvas - 1, canvas - 1), 58, fill=255)
    image.paste(gradient, (0, 0), mask)

    # Pasta: aba traseira mais escura e corpo amarelo.
    draw.rounded_rectangle((34, 58, 124, 112), 14, fill=(222, 160, 18, 255))
    draw.rounded_rectangle((28, 84, 214, 206), 18, fill=(250, 199, 52, 255))

    # Lupa: lente azul clara, aro claro e cabo escuro.
    center, radius, ring = (150, 132), 54, 20
    draw.line([(188, 172), (226, 212)], fill=(30, 41, 59, 255), width=30)
    draw.ellipse(
        (center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius),
        fill=(232, 238, 247, 255),
    )
    inner = radius - ring
    draw.ellipse(
        (center[0] - inner, center[1] - inner, center[0] + inner, center[1] + inner),
        fill=(110, 182, 240, 255),
    )
    return image.resize((size, size), Image.LANCZOS)


def main(output_dir=None):
    """Gera os arquivos em assets/ ou, se informado, em outra pasta (previa)."""
    png_path = Path(output_dir) / PNG_PATH.name if output_dir else PNG_PATH
    ico_path = Path(output_dir) / ICO_PATH.name if output_dir else ICO_PATH

    full = transparent_full_icon(Image.open(PNG_PATH))
    full.save(png_path)

    images = [simplified_icon(size) for size in SMALL_SIZES]
    images += [full.resize((size, size), Image.LANCZOS) for size in FULL_SIZES]
    largest = images[-1]
    # O Pillow escolhe, para cada tamanho, a imagem com exatamente esse tamanho.
    largest.save(
        ico_path,
        sizes=[(image.width, image.height) for image in images],
        append_images=images[:-1],
    )
    print(f"Gerado: {png_path} ({full.width}px) e {ico_path} {SMALL_SIZES + FULL_SIZES}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else None)
