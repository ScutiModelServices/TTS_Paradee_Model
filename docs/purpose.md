# Purpose

**Kind:** text → audio

**Group:** Text-to-Speech

**Tags:** `cpu`, `streaming`

| | |
|---|---|
| Input | Plain text (1–5000 chars), one of a few fixed voices, speed (0.5–2). |
| Output | WAV file (16-bit mono), streamable sentence by sentence. |
| Runs on | CPU (onnxruntime). No GPU. |

**Use for:** slightly higher quality speech than Piper, with a few fixed voices.
**Not for:** voice cloning, a large voice choice.
