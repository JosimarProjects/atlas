"""O ouvido: captura o microfone e transcreve com faster-whisper, local.

Roda na CPU de proposito. A VRAM dos 8 GB e toda do Qwen; o Whisper small em
int8 usa CPU e nao disputa espaco com o cerebro.
"""
from __future__ import annotations

import queue
import sys
import threading
from typing import Iterator

import numpy as np

from . import config

_modelo = None
_vad = None


def _carregar_stt():
    global _modelo
    if _modelo is None:
        from faster_whisper import WhisperModel
        _modelo = WhisperModel(
            config.STT_MODELO, device="cpu", compute_type=config.STT_COMPUTE
        )
    return _modelo


def _carregar_vad():
    """Silero VAD: distingue fala de ruido sem precisar de GPU."""
    global _vad
    if _vad is None:
        import torch
        modelo, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad",
            force_reload=False, onnx=False, trust_repo=True,
        )
        _vad = modelo
    return _vad


def transcrever(audio: np.ndarray) -> str:
    """float32 mono 16 kHz -> texto."""
    modelo = _carregar_stt()
    segmentos, _info = modelo.transcribe(
        audio, language=config.STT_IDIOMA, vad_filter=True, beam_size=1,
    )
    return " ".join(s.text.strip() for s in segmentos).strip()


def escutar_falas() -> Iterator[np.ndarray]:
    """Gera um array de audio por fala detectada, parando no silencio.

    Segura o microfone aberto para sempre: cada vez que o dono fala e para,
    um bloco sai daqui. E o "ela me ouve" do video.
    """
    import sounddevice as sd
    import torch

    vad = _carregar_vad()
    fila: queue.Queue[np.ndarray] = queue.Queue()

    def _entrada(dados, _frames, _tempo, status):
        if status:
            print(f"[ouvido] {status}", file=sys.stderr)
        fila.put(dados.copy().reshape(-1))

    blocos_silencio_fim = int(
        config.VAD_SILENCIO_S * config.AUDIO_TAXA / config.AUDIO_BLOCO
    )
    minimo_blocos_fala = int(
        config.VAD_FALA_MIN_S * config.AUDIO_TAXA / config.AUDIO_BLOCO
    )

    with sd.InputStream(
        samplerate=config.AUDIO_TAXA, channels=1, dtype="float32",
        blocksize=config.AUDIO_BLOCO, callback=_entrada,
    ):
        acumulado: list[np.ndarray] = []
        silencio = 0
        falando = False

        while True:
            bloco = fila.get()
            prob = float(vad(torch.from_numpy(bloco), config.AUDIO_TAXA).item())

            if prob >= config.VAD_LIMIAR:
                if not falando:
                    falando = True
                    acumulado = []
                acumulado.append(bloco)
                silencio = 0
            elif falando:
                acumulado.append(bloco)   # guarda o rabo da frase
                silencio += 1
                if silencio >= blocos_silencio_fim:
                    falando = False
                    if len(acumulado) >= minimo_blocos_fala:
                        yield np.concatenate(acumulado)
                    acumulado = []
                    silencio = 0


class Microfone:
    """Captura em thread, para o loop principal nao travar enquanto fala."""

    def __init__(self) -> None:
        self.fila: queue.Queue[np.ndarray] = queue.Queue()
        self.mudo = threading.Event()
        self._t: threading.Thread | None = None

    def iniciar(self) -> None:
        def _rodar():
            for audio in escutar_falas():
                # Enquanto a Atlas fala, ignora o microfone -- senao ela se
                # escuta e responde a propria voz.
                if not self.mudo.is_set():
                    self.fila.put(audio)
        self._t = threading.Thread(target=_rodar, daemon=True)
        self._t.start()

    def proxima_fala(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self.fila.get(timeout=timeout)
        except queue.Empty:
            return None

    def silenciar(self) -> None:
        self.mudo.set()

    def religar(self) -> None:
        # Descarta o que entrou enquanto ela falava.
        while not self.fila.empty():
            try:
                self.fila.get_nowait()
            except queue.Empty:
                break
        self.mudo.clear()
