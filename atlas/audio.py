"""Audio para a interface web: transcrever o que chega, sintetizar o que sai.

O navegador grava em WebM/Opus, que o Whisper nao le. O ffmpeg converte para
PCM 16 kHz mono -- o formato que o faster-whisper espera -- e o resultado
volta como texto. No sentido inverso, o Piper gera um WAV que o navegador
toca direto.

Tudo local: a voz nao sai da maquina, nem na entrada nem na saida.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import config

_FFMPEG: str | None = None


def _ffmpeg() -> str:
    """Acha o ffmpeg: no PATH, ou no build estatico baixado em engine/."""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    import shutil
    achado = shutil.which("ffmpeg")
    if not achado:
        for c in (config.RAIZ / "engine").rglob("ffmpeg.exe"):
            achado = str(c)
            break
    _FFMPEG = achado or "ffmpeg"
    return _FFMPEG


def transcrever_bytes(dados: bytes) -> str:
    """Audio de qualquer formato -> texto, pelo faster-whisper local."""
    if len(dados) < 1200:
        return ""        # clique sem fala: nao vale acordar o Whisper

    with tempfile.TemporaryDirectory() as tmp:
        entrada = Path(tmp) / "entrada"
        entrada.write_bytes(dados)
        saida = Path(tmp) / "audio.wav"
        r = subprocess.run(
            [_ffmpeg(), "-v", "error", "-y", "-i", str(entrada),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(saida)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode or not saida.exists():
            raise RuntimeError(
                f"ffmpeg nao converteu o audio: {r.stderr.decode('utf-8', 'replace')[:200]}")

        import wave
        with wave.open(str(saida)) as w:
            quadros = w.readframes(w.getnframes())
        amostras = np.frombuffer(quadros, dtype=np.int16).astype(np.float32) / 32768.0

    if amostras.size < 16000 * 0.3:    # menos de 0,3 s nao e fala
        return ""

    from .ouvido import transcrever
    return transcrever(amostras).strip()


def sintetizar_wav(texto: str) -> bytes:
    """Texto -> WAV pronto para o navegador tocar."""
    import io
    import wave

    from . import voz

    audio, taxa = voz.sintetizar(texto)
    if audio.size == 0:
        return b""
    inteiro = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(inteiro.tobytes())
    return buf.getvalue()


def pronto() -> dict[str, bool]:
    from . import voz
    import shutil
    return {
        "ouvir": bool(shutil.which("ffmpeg")
                      or list((config.RAIZ / "engine").rglob("ffmpeg.exe"))),
        "falar": voz.disponivel(),
    }
