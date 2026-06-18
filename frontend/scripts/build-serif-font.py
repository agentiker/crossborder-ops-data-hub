#!/usr/bin/env python3
"""生成自托管中文衬线 display 子集字体（Noto Serif SC, 600）。

为什么：UI 的 display 文字（wordmark / 首页问候 / 页面标题 / 卡标题，均带
`.font-display` 类）要对齐 StoreClaw 的衬线气质。中文系统宋体（Songti SC）只在
Mac 上好看，其它机器/服务器渲染不一致，故自托管。完整 Noto Serif SC（CJK）有数 MB，
这里用 Google Fonts 的 `text=` 只切出本应用实际用到的汉字 + 基础 ASCII，约 90KB woff2。

何时重跑：当 display 文案新增了当前子集没有的汉字（页面标题出现新字、问候语改写等），
否则缺字会优雅回退到系统宋体（同一字内可能字体不一致）。重跑后提交更新的 woff2。

用法：  uv run python frontend/scripts/build-serif-font.py
依赖：  requests、fonttools 已在项目环境（pyproject）中。
"""
import glob
import os
import re
import urllib.parse

import requests

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
OUT_DIR = os.path.join(SRC, "assets", "fonts")
OUT_FILE = "noto-serif-sc-600.woff2"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# 基础 ASCII + 常见标点：保证 display 里偶发的拉丁/数字也走衬线
ASCII = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    " .,:：，。、！？!?（）()「」%—-+/＋"
)


def collect_cjk() -> set[str]:
    chars: set[str] = set()
    for pat in ("**/*.tsx", "**/*.ts"):
        for path in glob.glob(os.path.join(SRC, pat), recursive=True):
            with open(path, encoding="utf-8") as f:
                for ch in f.read():
                    if "一" <= ch <= "鿿":  # CJK 统一表意
                        chars.add(ch)
    return chars


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    text = "".join(sorted(collect_cjk())) + ASCII
    print(f"subset glyphs = {len(set(text))}")

    url = (
        "https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@600"
        "&text=" + urllib.parse.quote(text) + "&display=swap"
    )
    css = requests.get(url, headers={"User-Agent": UA}, timeout=30).text
    m = re.search(r"src:\s*url\((https://[^)]+)\)\s*format\('woff2'\)", css)
    if not m:
        raise SystemExit("未从 Google Fonts CSS 解析到 woff2 URL：\n" + css[:500])

    data = requests.get(m.group(1), headers={"User-Agent": UA}, timeout=30).content
    out = os.path.join(OUT_DIR, OUT_FILE)
    with open(out, "wb") as f:
        f.write(data)
    print(f"wrote {out}  ({len(data)} bytes)")


if __name__ == "__main__":
    main()
