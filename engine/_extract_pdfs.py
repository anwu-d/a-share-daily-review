# -*- coding: utf-8 -*-
from pathlib import Path

from pypdf import PdfReader

folder = Path("E:/Project/stock/每日结论及其他")
out_dir = Path("E:/Project/stock/data/cache/pdftext")
out_dir.mkdir(parents=True, exist_ok=True)

for pdf in sorted(folder.glob("*.pdf")):
    r = PdfReader(str(pdf))
    parts = []
    for i, page in enumerate(r.pages):
        t = page.extract_text() or ""
        parts.append(f"\n----- page {i+1} -----\n{t}")
    text = "".join(parts)
    out = out_dir / (pdf.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    print(f"{pdf.name}: pages={len(r.pages)} chars={len(text)}")
