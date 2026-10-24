from __future__ import annotations

from pathlib import Path


def main() -> None:
    output = Path("fixtures/b9/moodle-final.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 18 Tf 72 720 Td (Synthetic B9 Moodle submission - test data only.) Tj ET\n"
    objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream")
    document = bytearray(b"%PDF-1.4\n% ALIGN B9 synthetic fixture\n")
    offsets = [0]
    for index, content in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("ascii"))
        document.extend(content)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    output.write_bytes(document)
    print(f"Wrote synthetic Moodle PDF fixture to {output} ({len(document)} bytes).")


if __name__ == "__main__":
    main()
