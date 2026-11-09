"""gen_android_webui.py -- copy the desktop WEB UI page (webui_page.PAGE, fonts inlined) into the Android app's
assets so the phone serves the very same page. Run after editing webui_page.py / webui_fonts.py:

    python tools/gen_android_webui.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
OUT = REPO / "android" / "app" / "src" / "main" / "assets" / "webui.html"


def render() -> str:
    from gt7telem.webui_page import PAGE
    return PAGE


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
