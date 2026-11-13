"""
Wake Word detection using microWakeWord .tflite quantized model.

Uses pymicro_features.MicroFrontend for audio preprocessing (same as official
microWakeWord) and ai-edge-litert for TFLite inference on Windows.

Audio pipeline:
  Microphone (16kHz int16) -> MicroFrontend (40 features per 10ms stride)
  -> quantize to int8 -> TFLite model (2 features per invoke, stateful)
  -> dequantize uint8 output -> probability [0, 1]
"""

import logging
import os
import threading
import time
from typing import Callable, Optional

import numpy as np
from config import local_device

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRONTEND_CHUNK_SAMPLES = 160
FRONTEND_CHUNK_BYTES = FRONTEND_CHUNK_SAMPLES * 2
FRAMES_PER_INVOKE = 2
MIC_CHUNK_SAMPLES = 160
WARMUP_INVOCATIONS = 40
CONSECUTIVE_DETECTIONS = 3  # require N consecutive frames above threshold


class MicroWakeWordDetector:
    """Wake word detector using microWakeWord tflite + pymicro_features."""

    def __init__(self, model_path: str, probability_cutoff: float = 0.97):
        self.model_path = model_path
        self.probability_cutoff = probability_cutoff
        self._listening = False
        self._paused = False
        self._reset_requested = False  # signal loop to re-warmup
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable] = None
        self._mic_released = threading.Event()
        self._mic_released.set()

        self._audio_device = local_device("AUDIO_INPUT_DEVICE")

        # Load TFLite interpreter
        try:
            from ai_edge_litert.interpreter import Interpreter
            logger.info("Usando ai-edge-litert (Windows)")
        except ImportError:
            try:
                import tflite_runtime.interpreter as tflite
                Interpreter = tflite.Interpreter
                logger.info("Usando tflite-runtime")
            except ImportError:
                try:
                    import tensorflow as tf
                    Interpreter = tf.lite.Interpreter
                    logger.info("Usando tensorflow.lite")
                except ImportError:
                    raise ImportError("Nenhum runtime TFLite encontrado!")

        self.interpreter = Interpreter(model_path=model_path)
        self.interpreter.allocate_tensors()

        self.input_details = self.interpreter.get_input_details()[0]
        self.output_details = self.interpreter.get_output_details()[0]

        inp_shape = self.input_details["shape"]
        inp_dtype = self.input_details["dtype"]
        out_dtype = self.output_details["dtype"]

        # Input quantization params (int8)
        inp_quant = self.input_details.get("quantization_parameters", {})
        self.input_scale = float(inp_quant.get("scales", [1.0])[0])
        self.input_zero_point = int(inp_quant.get("zero_points", [0])[0])

        # Output quantization params (uint8)
        out_quant = self.output_details.get("quantization_parameters", {})
        self.output_zero_point = int(out_quant.get("zero_points", [0])[0])

        logger.info(
            f"microWakeWord tflite carregado: {model_path}\n"
            f"  Input:  shape={inp_shape} dtype={inp_dtype}\n"
            f"          quant: scale={self.input_scale}, zp={self.input_zero_point}\n"
            f"  Output: dtype={out_dtype}, zp={self.output_zero_point}\n"
            f"  Threshold: {probability_cutoff}"
        )

        # Verify pymicro_features
        from pymicro_features import MicroFrontend
        self._MicroFrontend = MicroFrontend
        logger.info("pymicro_features.MicroFrontend disponivel")

    def _quantize_input(self, features):
        """Quantize float32 features to int8."""
        quantized = np.round(features / self.input_scale) + self.input_zero_point
        return np.clip(quantized, -128, 127).astype(np.int8)

    def _dequantize_output(self, raw_output):
        """Dequantize uint8 output to probability [0, 1]. Official formula."""
        return (1.0 / 255.0) * (float(raw_output) - self.output_zero_point)

    def _invoke_model(self, quantized_frames):
        """Invoke model with 2 quantized feature slices. Returns probability."""
        input_data = quantized_frames.reshape(self.input_details["shape"])
        self.interpreter.set_tensor(self.input_details["index"], input_data)
        self.interpreter.invoke()
        raw_out = self.interpreter.get_tensor(self.output_details["index"])
        raw_value = int(raw_out.flatten()[0])
        return self._dequantize_output(raw_value)

    def start_listening(self, callback):
        """Start wake word listening in a daemon thread."""
        if self._listening:
            logger.warning("Wake word ja esta a escutar")
            return

        self._callback = callback
        self._listening = True
        self._paused = False
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Wake word listener iniciado. Aguardando wake word...")

    def _listen_loop(self):
        """Main loop: mic -> MicroFrontend -> model -> detection.
        
        IMPORTANT: The mic stream is opened/closed around pause/resume
        so that local Whisper capture can use the mic without conflict.
        """
        import sounddevice as sd

        try:
            micro_frontend = self._MicroFrontend()
            pending_features = []
            invocation_count = 0
            cooldown_until = 0.0
            consecutive_above = 0  # consecutive invocations above threshold
            stream = None
            stream_channels = 1

            def _open_stream():
                nonlocal stream, stream_channels
                if stream is not None:
                    return

                candidates = [
                    (self._audio_device, 1),
                    (self._audio_device, 2),
                    (None, 1),
                    (None, 2),
                ]
                seen = set()
                unique_candidates = []
                for dev, ch in candidates:
                    key = (dev, ch)
                    if key not in seen:
                        seen.add(key)
                        unique_candidates.append(key)

                last_err = None
                for dev, ch in unique_candidates:
                    try:
                        stream = sd.InputStream(
                            samplerate=SAMPLE_RATE,
                            channels=ch,
                            dtype="int16",
                            blocksize=MIC_CHUNK_SAMPLES,
                            device=dev,
                        )
                        stream.start()
                        self._mic_released.clear()
                        stream_channels = ch
                        logger.info(
                            f"Microfone wake word aberto: {SAMPLE_RATE}Hz, canais={ch}, int16, "
                            f"{MIC_CHUNK_SAMPLES} amostras/chunk (dispositivo: {dev})"
                        )
                        return
                    except Exception as e:
                        if stream is not None:
                            try:
                                stream.close()
                            except Exception:
                                pass
                            stream = None
                        last_err = e
                        logger.debug(f"Tentativa de abrir mic (dev={dev}, canais={ch}) falhou: {e}")

                if last_err:
                    raise last_err

            def _close_stream():
                nonlocal stream
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    stream = None
                    logger.debug("Microfone wake word fechado (liberado para ASR)")
                self._mic_released.set()

            _open_stream()

            while self._listening:
                if self._paused:
                    # Liberar microfone para a captura local do Whisper.
                    _close_stream()
                    pending_features.clear()
                    time.sleep(0.05)
                    continue

                # Check if a reset was requested (after resume)
                if self._reset_requested:
                    self._reset_requested = False
                    self.interpreter.allocate_tensors()
                    pending_features.clear()
                    micro_frontend = self._MicroFrontend()
                    invocation_count = 0
                    consecutive_above = 0
                    cooldown_until = time.time() + 1.0
                    logger.info("Wake word: estado resetado, re-warmup iniciado")

                # Ensure mic is open
                if stream is None:
                    _open_stream()

                audio, overflowed = stream.read(MIC_CHUNK_SAMPLES)
                if overflowed:
                    logger.debug("Audio overflow (ignorado)")

                if stream_channels > 1:
                    audio_mono = audio[:, 0]
                else:
                    audio_mono = audio.flatten()

                audio_bytes = audio_mono.tobytes()

                result = micro_frontend.process_samples(audio_bytes)
                if result.features:
                    feat = np.array(result.features, dtype=np.float32)
                    pending_features.append(feat)

                while len(pending_features) >= FRAMES_PER_INVOKE:
                    pair = pending_features[:FRAMES_PER_INVOKE]
                    pending_features = pending_features[FRAMES_PER_INVOKE:]

                    features = np.stack(pair, axis=0)
                    quantized = self._quantize_input(features)

                    probability = self._invoke_model(quantized)
                    invocation_count += 1

                    if invocation_count <= WARMUP_INVOCATIONS:
                        if invocation_count == WARMUP_INVOCATIONS:
                            logger.info(
                                f"Warmup completo ({WARMUP_INVOCATIONS} "
                                f"invocacoes). Detecao ativa!"
                            )
                        continue

                    # Log probability periodically for debugging
                    if invocation_count % 10 == 0 or probability > 0.5:
                        logger.debug(
                            f"[ww] inv={invocation_count} "
                            f"prob={probability:.4f} "
                            f"consec={consecutive_above}"
                        )

                    if probability >= self.probability_cutoff:
                        consecutive_above += 1
                        now = time.time()
                        if (consecutive_above >= CONSECUTIVE_DETECTIONS
                                and now >= cooldown_until):
                            logger.info(
                                f"Wake word detetada! "
                                f"Prob: {probability:.4f} "
                                f"({consecutive_above} frames consecutivos, "
                                f"threshold: {self.probability_cutoff})"
                            )
                            cooldown_until = now + 3.0
                            consecutive_above = 0
                            self._paused = True
                            # Close mic immediately so ASR starts clean
                            _close_stream()
                            if self._callback:
                                self._callback()
                    else:
                        consecutive_above = 0

            _close_stream()

        except Exception as e:
            logger.error("Falha no listener da wake word")
            self._listening = False
        finally:
            if 'stream' in locals() and stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            self._mic_released.set()

    def pause(self):
        """Pause listening."""
        if self._listening:
            self._mic_released.clear()
        self._paused = True
        logger.debug("Wake word pausado")

    def is_listening(self):
        return self._listening

    def resume(self):
        """Resume listening. Resets model state and forces re-warmup."""
        # Re-allocate tensors to clear internal stateful accumulators
        # Signal the listen loop to reset its counters and MicroFrontend
        self._reset_requested = True
        self._paused = False
        logger.info("Wake word retomado - reset + re-warmup em curso...")

    async def wait_paused(self):
        import asyncio
        deadline = time.monotonic() + 2
        while not self._mic_released.is_set():
            if time.monotonic() >= deadline:
                raise RuntimeError("wake_microphone_busy")
            await asyncio.sleep(.02)

    def stop(self):
        """Stop listening completely."""
        self._listening = False
        self._paused = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        logger.info("Wake word listener parado")
