"""A voz: Piper TTS, local e rapido.

O Piper gera fala em CPU bem mais rapido que tempo real, entao a resposta sai
quase junto com o fim da geracao do texto -- e nao disputa VRAM com o Qwen.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

from . import config

_voz = None


def _caminho_voz() -> tuple[Path, Path]:
    onnx = config.VOZES / f"{config.VOZ_MODELO}.onnx"
    jsn = config.VOZES / f"{config.VOZ_MODELO}.onnx.json"
    return onnx, jsn


def disponivel() -> bool:
    onnx, jsn = _caminho_voz()
    return onnx.exists() and jsn.exists()


def _carregar():
    global _voz
    if _voz is None:
        from piper import PiperVoice
        onnx, _ = _caminho_voz()
        if not onnx.exists():
            raise FileNotFoundError(
                f"Voz nao encontrada: {onnx}. Rode scripts/baixa-voz.sh."
            )
        _voz = PiperVoice.load(str(onnx))
    return _voz


def sintetizar(texto: str) -> tuple[np.ndarray, int]:
    """Texto -> (audio float32 mono, taxa de amostragem)."""
    voz = _carregar()
    pedacos: list[np.ndarray] = []
    taxa = 22050

    for chunk in voz.synthesize(texto):
        # A API do Piper ja mudou de forma entre versoes; aceita as duas.
        bruto = getattr(chunk, "audio_int16_bytes", None)
        if bruto is None:
            bruto = getattr(chunk, "audio_int16_array", None)
            if bruto is not None:
                pedacos.append(np.asarray(bruto, dtype=np.int16))
                taxa = getattr(chunk, "sample_rate", taxa)
                continue
            bruto = bytes(chunk)
        pedacos.append(np.frombuffer(bruto, dtype=np.int16))
        taxa = getattr(chunk, "sample_rate", taxa)

    if not pedacos:
        return np.zeros(0, dtype=np.float32), taxa
    inteiro = np.concatenate(pedacos)
    return inteiro.astype(np.float32) / 32768.0, taxa


def falar(texto: str) -> None:
    """Sintetiza e toca, bloqueando ate terminar."""
    texto = texto.strip()
    if not texto:
        return
    import sounddevice as sd

    audio, taxa = sintetizar(texto)
    if audio.size == 0:
        return
    sd.play(audio, taxa)
    sd.wait()


def salvar_wav(texto: str, destino: Path) -> Path:
    audio, taxa = sintetizar(texto)
    inteiro = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(inteiro.tobytes())
    return destino


if __name__ == "__main__":
    frase = " ".join(sys.argv[1:]) or "Oi, eu sou a Atlas. Estou rodando no seu computador."
    falar(frase)
