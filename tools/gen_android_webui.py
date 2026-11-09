"""gen_android_webui.py -- copy the desktop WEB UI pages (webui_page.PAGE and the passcode page
webui_login.LOGIN_PAGE, fonts inlined) into the Android app's assets so the phone serves the very same pages, and
write the QR golden matrices the Android QrCode unit test checks against. Run after editing webui_page.py,
webui_login.py, webui_fonts.py or qr.py:

    python tools/gen_android_webui.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
ASSETS = REPO / "android" / "app" / "src" / "main" / "assets"
OUT = ASSETS / "webui.html"
OUT_LOGIN = ASSETS / "webui_login.html"
OUT_QR = REPO / "android" / "telemetry" / "src" / "test" / "resources" / "qr_golden.json"

# what the Dashboard really encodes, plus edge sizes for each QR version boundary
QR_SAMPLES = [
    "http://192.168.1.20:8080/?t=Qm9vbS1yZWFkeS1zZXQtZ28tMTIzNDU2Nzg5MGFiY2RlZmdo",
    "http://10.0.0.5:8080/?t=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "TRACE",
    "x" * 14, "y" * 26, "z" * 42, "w" * 62, "v" * 84, "u" * 106, "t" * 122, "s" * 152, "r" * 180, "q" * 213,
]


def render() -> str:
    from gt7telem.webui_page import PAGE
    return PAGE


def render_login() -> str:
    from gt7telem.webui_login import LOGIN_PAGE
    return LOGIN_PAGE


def render_qr_golden() -> str:
    from gt7telem import qr
    rows = []
    for text in QR_SAMPLES:
        m = qr.encode(text)
        rows.append({"text": text, "rows": ["".join("1" if v else "0" for v in row) for row in m]})
    return json.dumps({"samples": rows}, indent=1) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for path, text in ((OUT, render()), (OUT_LOGIN, render_login()), (OUT_QR, render_qr_golden())):
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path} ({path.stat().st_size} bytes)")
