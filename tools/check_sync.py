"""Fails when the admin page or token store drifts from the sibling service.

Every model service keeps a copy of the files in SHARED that differ only in
the lines listed in ALLOWED. Run from either repo: py tools/check_sync.py
"""
import difflib
import re
import sys
from pathlib import Path

SHARED = ("web/index.html", "web/playground.js", "src/tokens.py", "src/settings.py", "src/updates.py", "src/progress.py", ".dockerignore")
OWN_COPY = {"src/updates.py": {"DSC_Laya_Model"}}  # DSC_Laya_Model keeps its own update flow (SERVICE-SPECIFIC.md)
ALLOWED = re.compile(r"^(PREFIX = |const CONFIG = )")  # per-project: token prefix and page config


def lines(path: Path) -> list[str]:
    return [("<per-project line>" if ALLOWED.match(line) else line) for line in path.read_text("utf-8").splitlines()]


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    drift = False
    for sibling in ('DSC_Laya_Model', 'CNV_MiniCPM52B_Model', 'CNV_BitNet2B_Model', 'TTS_Piper_Model', 'TTS_Paradee_Model', 'IMG_TEM_Model', 'CNV_LFM3_Model', 'STC_S1Mini_Model', 'STT_Whisper_Model', 'STT_Moonshine_Model', 'OCR_Tesseract_Model', 'CNV_SparkX26_Model', 'TTS_Audio8_Model', 'TTS_Kitten2_Model', 'OCR_SGLang_Model'):
        other = here.parent / sibling
        if sibling == here.name or not other.is_dir():
            continue
        for name in SHARED:
            if OWN_COPY.get(name, set()) & {here.name, sibling}:
                continue
            diff = list(difflib.unified_diff(lines(here / name), lines(other / name), here.name, other.name, lineterm="", n=0))
            if diff:
                drift = True
                print(f"{name} differs from {sibling}:", *diff[:40], sep="\n")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
