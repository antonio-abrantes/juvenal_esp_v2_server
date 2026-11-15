"""ASR PCM selecionável; aquisição local e lifecycle compartilhados."""

import asyncio
import logging
import os
import time
from collections import deque

import numpy as np

from config import flag, integer, number, local_device, whisper_options
from asr_diagnostics import ASRDiagnostics
from runtime import SingleWorker

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000
BLOCKSIZE = 1600
MAX_SESSION_DURATION = 30.0
ENERGY_THRESHOLD = 300
SILENCE_AFTER_SPEECH = 2.0


class PCMTranscriber:
    def __init__(self):
        self.worker = SingleWorker()
        self._audio_device = local_device("AUDIO_INPUT_DEVICE")
        self._session_active = False
        self._stream = None
        self._monitor_task = None
        self.last_error = None

    @property
    def busy(self):
        return self._session_active or self.worker.busy or (
            self._monitor_task is not None and not self._monitor_task.done())

    def is_active(self):
        return self._session_active

    async def transcribe_pcm(self, pcm: bytes, initial_prompt=None, timeout=60, *, context=None):
        if not pcm or len(pcm) % 2 or len(pcm) > 960000:
            raise ValueError("invalid_pcm")
        audio = np.frombuffer(pcm, dtype="<i2").copy()
        return await self.worker.run(self._transcribe, [audio], initial_prompt, context, timeout=timeout)

    def _transcribe(self, audio_chunks, initial_prompt=None, context=None):
        audio = np.concatenate(audio_chunks)
        if len(audio) > int(MAX_SESSION_DURATION * SAMPLE_RATE):
            raise ValueError("capture_limit")
        started = time.perf_counter()
        result, status = "", "error"
        try:
            result = self._recognize(audio, initial_prompt)
            if self.worker.cancel_event.is_set():
                status = "cancelled"
                return ""
            status = "ok" if result else "empty"
            return result
        finally:
            # Inclui pré-processamento/decodificação do ASR, nunca disco, LLM ou TTS.
            elapsed = time.perf_counter() - started
            self.diagnostics.transcription(audio, elapsed, result, status, context,
                                           prompted=bool(initial_prompt))

    async def start_session(self, on_partial, on_final, on_session_end,
                            max_wait_for_speech=None, initial_prompt=None):
        if self.busy:
            raise RuntimeError("capture_busy")
        import sounddevice as sd  # Nunca importado para áudio recebido pela rede.

        self._session_active = True
        self.last_error = None
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        chunks = []
        pre = deque(maxlen=5)
        speech = False
        overflow = False
        samples = 0
        started = last_voice = time.monotonic()
        wait_limit = max_wait_for_speech or 8

        def audio_callback(indata, frames, timing, status):
            nonlocal speech, last_voice, overflow, samples
            if not self._session_active:
                return
            chunk = indata[:, 0].copy()
            energy = np.abs(chunk.astype(np.int32)).mean()
            if energy > ENERGY_THRESHOLD:
                if not speech:
                    speech = True
                    chunks.extend(pre)
                    samples += sum(len(p) for p in pre)
                    pre.clear()
                    loop.call_soon_threadsafe(on_partial, "Ouvindo...")
                last_voice = time.monotonic()
            if speech:
                if samples + len(chunk) > int(MAX_SESSION_DURATION * SAMPLE_RATE):
                    overflow = True
                    return
                chunks.append(chunk)
                samples += len(chunk)
            else:
                pre.append(chunk)

        try:
            candidates = list(dict.fromkeys([
                (self._audio_device, 1), (self._audio_device, 2), (None, 1), (None, 2)]))
            for device, channels in candidates:
                try:
                    self._stream = sd.InputStream(
                        samplerate=SAMPLE_RATE, channels=channels, dtype="int16",
                        blocksize=BLOCKSIZE, device=device, callback=audio_callback)
                    self._stream.start()
                    break
                except Exception:
                    self._close_stream()
            else:
                raise RuntimeError("microphone_unavailable")
        except BaseException:
            self._session_active = False
            self._close_stream()
            done.set()
            raise

        async def monitor():
            try:
                while self._session_active:
                    now = time.monotonic()
                    if overflow or now - started >= MAX_SESSION_DURATION:
                        if speech:
                            raise RuntimeError("capture_limit")
                        break
                    if (speech and now - last_voice >= SILENCE_AFTER_SPEECH
                            or not speech and now - started >= wait_limit):
                        break
                    await asyncio.sleep(.1)
                self._session_active = False
                self._close_stream()
                if speech and chunks:
                    on_partial("Transcrevendo...")
                    text = await self.worker.run(self._transcribe, chunks, initial_prompt, timeout=60)
                    if text:
                        on_final(text)
            except asyncio.CancelledError:
                self.worker.cancel()
                raise
            except Exception:
                self.last_error = "capture_or_transcription_failed"
                logger.warning("Falha de captura/transcrição; recursos liberados")
            finally:
                self._session_active = False
                self._close_stream()
                try:
                    on_session_end()
                finally:
                    done.set()

        self._monitor_task = asyncio.create_task(monitor(), name="local-capture")
        return done

    def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    async def stop_session(self):
        self._session_active = False
        self.worker.cancel()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        self._close_stream()

    def shutdown(self):
        self._session_active = False
        self.worker.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()
        self._close_stream()


class WhisperTranscriber(PCMTranscriber):
    def __init__(self, model_size="base", language="pt", device="cpu", compute_type="int8"):
        super().__init__()
        self._beam_size = integer("WHISPER_BEAM_SIZE", 1, 20)
        self._temperature = number("WHISPER_TEMPERATURE", 0, 2, 0)
        self._no_speech_threshold = number("WHISPER_NO_SPEECH_THRESHOLD", .7, 1, 0)
        self._condition_on_previous_text = flag("WHISPER_CONDITION_ON_PREVIOUS_TEXT", True)
        self._language = language
        self.diagnostics = ASRDiagnostics("whisper", model_size, {
            "language": language, "device": device, "precision": compute_type,
            "beam_size": self._beam_size, "temperature": self._temperature,
            "no_speech_threshold": self._no_speech_threshold,
            "condition_on_previous_text": self._condition_on_previous_text,
            "vad_filter": True, "cpu_threads": "runtime_default"})
        started = time.perf_counter()
        from faster_whisper import WhisperModel
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.diagnostics.loaded(time.perf_counter() - started)

    def _recognize(self, audio, initial_prompt=None):
        # Parâmetros da baseline preservados, inclusive o VAD interno do Whisper.
        segments, _ = self._model.transcribe(
            audio.astype(np.float32) / 32768.0, language=self._language,
            beam_size=self._beam_size, temperature=self._temperature,
            no_speech_threshold=self._no_speech_threshold,
            condition_on_previous_text=self._condition_on_previous_text,
            initial_prompt=initial_prompt, word_timestamps=False, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200})
        texts = []
        for segment in segments:
            if self.worker.cancel_event.is_set():
                return ""
            if segment.text.strip():
                texts.append(segment.text.strip())
        result = " ".join(texts)
        words = result.lower().split()
        if len(words) > 6 and len(set(words)) / len(words) < .4:
            logger.warning("Transcrição repetitiva descartada")
            return ""
        return result


class ParakeetTranscriber(PCMTranscriber):
    def __init__(self):
        super().__init__()
        model = os.getenv("PARAKEET_MODEL", "alefiury/parakeet-tdt-v3-ptBR-30s-onnx").strip()
        if not model:
            raise ValueError("PARAKEET_MODEL vazio")
        threads = integer("PARAKEET_CPU_THREADS", 0, 64, 0)
        self.diagnostics = ASRDiagnostics("parakeet", model, {
            "language": "pt-BR", "device": "CPUExecutionProvider", "precision": "int8",
            "cpu_threads": threads or "runtime_default", "vad_filter": False,
            "initial_prompt_supported": False})
        started = time.perf_counter()
        import onnx_asr
        import onnxruntime as ort
        options = ort.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
        self._model = onnx_asr.load_model(
            model, quantization="int8", sess_options=options,
            providers=["CPUExecutionProvider"])
        self.diagnostics.loaded(time.perf_counter() - started)

    def _recognize(self, audio, initial_prompt=None):
        # Este checkpoint já é PT-BR; não recebe prompt do LLM nem altera o áudio capturado.
        result = self._model.recognize(audio.astype(np.float32) / 32768.0,
                                       sample_rate=SAMPLE_RATE)
        if not isinstance(result, str):
            raise RuntimeError("parakeet_invalid_result")
        return result.strip()


def create_transcriber():
    backend = os.getenv("ASR_BACKEND", "whisper").strip().lower()
    if backend == "whisper":
        return WhisperTranscriber(**whisper_options())
    if backend == "parakeet":
        return ParakeetTranscriber()
    raise ValueError("ASR_BACKEND: use whisper ou parakeet")
