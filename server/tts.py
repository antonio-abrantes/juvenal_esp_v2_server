"""Síntese Piper independente da reprodução e de qualquer endpoint HTTP."""

import io
import logging
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config import PROJECT_ROOT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PCMChunk:
    pcm: bytes
    sample_rate: int
    channels: int = 1
    sample_format: str = "s16le"


class PiperTTSEngine:
    def __init__(self, model_name="pt_PT-tugao-medium", models_dir=None,
                 use_cuda=False, length_scale=1.0, noise_scale=0.667,
                 noise_w_scale=0.8, volume=1.0, speaker_id=None, output_device=None):
        from piper import PiperVoice, SynthesisConfig
        self.syn_config_cls = SynthesisConfig
        self.length_scale, self.noise_scale = length_scale, noise_scale
        self.noise_w_scale, self.volume = noise_w_scale, volume
        self.speaker_id, self.output_device = speaker_id, output_device
        self.voice = PiperVoice.load(str(self._resolve_model(model_name, models_dir)), use_cuda=use_cuda)

    def _resolve_model(self, name, models_dir):
        from piper.download_voices import download_voice
        import platformdirs
        cache = Path(platformdirs.user_cache_dir("piper")) / "voices"
        path = Path(name)
        explicit_path = path if path.is_absolute() else PROJECT_ROOT / path
        if name.endswith(".onnx") and explicit_path.is_file():
            return explicit_path
        candidates = ([Path(models_dir)] if models_dir else []) + [PROJECT_ROOT / "models", cache]
        filename = name if name.endswith(".onnx") else name + ".onnx"
        for folder in candidates:
            candidate = folder / filename
            if candidate.is_file():
                return candidate
        cache.mkdir(parents=True, exist_ok=True)
        # Só alcançado se o modo realmente usar TTS; endpoint não importa este módulo.
        download_voice(name, download_dir=cache)
        candidate = cache / filename
        if not candidate.is_file():
            raise FileNotFoundError("piper_model_missing")
        return candidate

    def _make_syn_config(self):
        return self.syn_config_cls(length_scale=self.length_scale, noise_scale=self.noise_scale,
                                   noise_w_scale=self.noise_w_scale, volume=self.volume,
                                   speaker_id=self.speaker_id)

    def synthesize_stream(self, text):
        yield from self.voice.synthesize(text, syn_config=self._make_syn_config())

    def synthesize_pcm(self, text):
        """Chunks nativos com metadados; conversão para WS v1 pertence à fase 03."""
        for chunk in self.synthesize_stream(text):
            samples = np.clip(chunk.audio_float_array, -1.0, 1.0)
            pcm = np.round(samples * 32767).astype("<i2").tobytes()
            yield PCMChunk(pcm, chunk.sample_rate)

    def synthesize_to_bytes(self, text):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            self.voice.synthesize_wav(text, wav, syn_config=self._make_syn_config())
        return buf.getvalue()

    def speak_sync(self, text, cancel: threading.Event):
        import sounddevice as sd
        def play(samples, rate):
            if cancel.is_set():
                return
            try:
                sd.play(samples, samplerate=rate, device=self.output_device, blocking=False)
            except Exception:
                # Compatibilidade do player local: fallback somente de dispositivo local.
                logger.warning("Dispositivo local indisponível; tentando o padrão local")
                sd.play(samples, samplerate=rate, device=None, blocking=False)
            while sd.get_stream().active:
                if cancel.wait(0.02):
                    sd.stop()
                    return

        try:
            for index, chunk in enumerate(self.synthesize_stream(text)):
                if cancel.is_set():
                    return
                if index:
                    play(np.zeros(int(chunk.sample_rate * 0.08), dtype=np.float32), chunk.sample_rate)
                play(chunk.audio_float_array, chunk.sample_rate)
        finally:
            if cancel.is_set():
                sd.stop()


class Pyttsx3TTSEngine:
    """Backend local opcional e explícito; nunca fallback do endpoint."""
    def __init__(self, output_device=None, **kwargs):
        if output_device is not None:
            raise ValueError("pyttsx3 não seleciona AUDIO_OUTPUT_DEVICE; use Piper")
        import pyttsx3
        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", 175)

    def speak_sync(self, text, cancel):
        if cancel.is_set():
            return
        self.engine.say(text)
        self.engine.runAndWait()


def create_tts_engine(backend="piper", **kwargs):
    if backend == "piper":
        return PiperTTSEngine(**kwargs)
    if backend == "pyttsx3":
        return Pyttsx3TTSEngine(**kwargs)
    raise ValueError("TTS_BACKEND inválido")
