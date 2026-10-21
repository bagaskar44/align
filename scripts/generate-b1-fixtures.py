"""Generate small synthetic B1 PDF/image fixtures using the locked Pillow/pypdf."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def main() -> None:
    target = Path("tests/fixtures/b1")
    target.mkdir(parents=True, exist_ok=True)
    font_path = next(path for path in (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ) if path.is_file())
    font = ImageFont.truetype(str(font_path), 42)
    image = Image.new("RGB", (1600, 600), "white")
    draw = ImageDraw.Draw(image)
    for y, text in enumerate(("Kode Mata Kuliah: IF101", "Deskripsi Konteks: Dokumen sintetis B1", "Bahasa: id")):
        draw.text((80, 80 + y * 130), text, fill="black", font=font)
    image.save(target / "printed.png")
    image.save(target / "printed.jpg", quality=95)
    image.save(target / "printed.jpeg", quality=95)
    image.save(target / "scanned.pdf", resolution=100)
    rotated = image.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    rotated.save(target / "rotated.jpeg", quality=95, exif=exif)

    writer = PdfWriter()
    for lines in (("Kode Mata Kuliah: IF101", "Deskripsi Konteks: PDF digital sintetis B1"),
                  ("Bahasa: id", "Halaman kedua dengan anchor original.")):
        page = writer.add_blank_page(width=612, height=792)
        font_object = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                        NameObject("/Subtype"): NameObject("/Type1"),
                                        NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font_object)})})
        stream = DecodedStreamObject()
        content = "BT /F1 16 Tf 50 730 Td " + " 0 -35 Td ".join(f"({text}) Tj" for text in lines) + " ET"
        stream.set_data(content.encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(target / "native-two-pages.pdf")
    print(f"Generated synthetic PDF/image fixtures in {target}.")


if __name__ == "__main__":
    main()
