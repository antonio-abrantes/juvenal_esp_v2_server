"""Métricas ASR e gravações de comparação opt-in, fora do event loop."""

import hashlib
import json
import logging
import os
import platform
import wave
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from uuid import uuid4

import numpy as np

from config import flag, settings
from diagnostics import log_text

logger = logging.getLogger("asr")


class ASRDiagnostics:
    def __init__(self, backend, model, options):
        self.backend, self.model, self.options = backend, model, options
        self.enabled = flag("ASR_DIAGNOSTICS_ENABLED", False)
        self.save_audio = flag("ASR_SAVE_AUDIO", False)
        if self.save_audio and not self.enabled:
            raise ValueError("ASR_SAVE_AUDIO exige ASR_DIAGNOSTICS_ENABLED=true")
        self.log_transcripts = flag("LOG_TRANSCRIPTS", False)
        self.run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{backend}-{uuid4().hex[:8]}"
        self.directory = settings.data_dir / "asr-comparison" / self.run_id
        self.count = 0
        self.last_record = None

    def loaded(self, elapsed):
        packages = {}
        for name in ("numpy", "faster-whisper", "ctranslate2", "onnx-asr", "onnxruntime"):
            try:
                packages[name] = version(name)
            except PackageNotFoundError:
                pass
        logger.info("ASR pronto: backend=%s modelo=%s carregamento_ms=%d diagnostico=%s grava_audio=%s",
                    self.backend, log_text(self.model), int(elapsed * 1000), self.enabled, self.save_audio)
        self._write({"event": "model_loaded", "load_ms": round(elapsed * 1000, 2),
                     "options": self.options, "packages": packages,
                     "python": platform.python_version(), "platform": platform.system(),
                     "machine": platform.machine(), "logical_cpus": os.cpu_count()})
        if self.enabled:
            logger.info("ASR comparacao: run=%s; arquivos privados em JUVENAL_DATA_DIR/asr-comparison/%s",
                        self.run_id, self.run_id)

    def transcription(self, audio, elapsed, text, status, context=None, *, prompted=False):
        self.count += 1
        pcm = audio.astype("<i2", copy=False).tobytes()
        audio_id = hashlib.sha256(pcm).hexdigest()
        duration = len(audio) / 16000
        record = {"event": "transcription", "sample": self.count, "audio_sha256": audio_id,
                  "audio_seconds": round(duration, 4), "asr_ms": round(elapsed * 1000, 2),
                  "rtf": round(elapsed / duration, 4) if duration else None,
                  "status": status, "first_inference": self.count == 1,
                  "prompt_supplied": prompted, "options": self.options}
        if context:
            # Somente identificadores internos, sem aceitar conteúdo arbitrário do cliente.
            record.update({key: str(context[key])[:128] for key in
                           ("session_id", "turn_id", "source") if key in context})
            if isinstance(context.get("repetition"), int):
                record["repetition"] = context["repetition"]
            if isinstance(context.get("warmup"), bool):
                record["warmup"] = context["warmup"]
        if self.enabled:
            scaled = audio.astype(np.float64)
            record["rms"] = round(float(np.sqrt(np.mean(scaled * scaled))), 2)
            record["peak"] = int(np.max(np.abs(scaled))) if len(audio) else 0
            if self.log_transcripts:
                record["transcript"] = json.loads(log_text(text[:4096]))
                record["transcript_truncated"] = len(text) > 4096
        logger.info("ASR backend=%s amostra=%d audio_id=%s audio_s=%.2f asr_ms=%.0f rtf=%.3f status=%s primeira=%s",
                    self.backend, self.count, audio_id[:12], duration, elapsed * 1000,
                    elapsed / duration if duration else 0, status, self.count == 1)
        self.last_record = record
        if self.enabled and self.save_audio:
            try:
                folder = self.directory / "audio"
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                filename = f"{self.count:03d}-{audio_id[:12]}.wav"
                path = folder / filename
                with path.open("xb") as stream:
                    os.chmod(path, 0o600)
                    with wave.open(stream, "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                        wav.writeframes(pcm)
                record["wav"] = "audio/" + filename
            except OSError as error:
                logger.warning("ASR: gravação WAV falhou (%s); transcrição preservada", type(error).__name__)
        self._write(record)

    def _write(self, record):
        if not self.enabled:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.directory / "results.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                os.chmod(path, 0o600)
                stream.write(json.dumps({"run_id": self.run_id, "backend": self.backend,
                    "model": self.model, "time_utc": datetime.now(timezone.utc).isoformat(),
                    **record}, ensure_ascii=False) + "\n")
        except OSError as error:
            logger.warning("ASR: registro em disco falhou (%s); transcrição preservada", type(error).__name__)
