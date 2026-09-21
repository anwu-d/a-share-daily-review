# -*- coding: utf-8 -*-
import html
import re
from pathlib import Path

for name in [
    "图形归类_·_情绪验证看板主页.mhtml.txt",
    "图形归类_·_情绪验证看板结论.mhtml.txt",
]:
    p = Path("E:/Project/stock/data/cache") / name
    t = p.read_text(encoding="utf-8", errors="ignore")
    t = html.unescape(t)
    # strip tags roughly for text dump
    text = re.sub(r"<script[\s\S]*?</script>", " ", t, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out = Path("E:/Project/stock/data/cache") / (name.replace(".txt", "") + ".plain.txt")
    out.write_text(text, encoding="utf-8")
    # print article-like chunks
    print("=" * 60, name)
    # find 基于图形归类 or 群体分析
    for key in ["基于图形归类", "群体分析", "一、", "结论", "去弱留强"]:
        i = text.find(key)
        if i >= 0:
            print(f"--- key {key} @{i} ---")
            print(text[i : i + 2500])
            print()
            break
    else:
        print(text[:2000])
