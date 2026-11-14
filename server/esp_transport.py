"""WS v1 por interação, PCM limitado, VAD e playback confirmado por turno."""

import asyncio
import hmac
import json
import logging
import math
import queue
import sys
import threading
import time
from array import array
from uuid import uuid4

from config import settings, esp_settings
from runtime import CapacityBusy
from tools.registry import ArgumentError, decode_json

logger = logging.getLogger(__name__)
FORMAT = {"sample_rate": 16000, "channels": 1, "sample_format": "s16le"}
PREROLL_BYTES = 16000  # 500 ms; silencio de espera nunca acumula uma fala inteira.


class ESPProtocolError(RuntimeError):
    pass


def read_control(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
        raise ESPProtocolError("control_limit")
    try:
        value = decode_json(raw)
    except ArgumentError:
        raise ESPProtocolError("invalid_control") from None
    if (not isinstance(value, dict) or set(value) - {
            "protocol_version", "event", "session_id", "turn_id", "payload"}
            or type(value.get("protocol_version")) is not int or value["protocol_version"] != 1
            or not isinstance(value.get("event"), str)
            or not isinstance(value.get("payload"), dict)):
        raise ESPProtocolError("invalid_envelope")
    return value


def preview(text):
    raw = "".join(char if ord(char) >= 32 else " " for char in text).encode("utf-8")
    return {"text": raw[:1024].decode("utf-8", errors="ignore"), "truncated": len(raw) > 1024}


class ESPConnection:
    def __init__(self, manager, ws, session, playback, window):
        self.manager, self.ws, self.session = manager, ws, session
        self.playback, self.window = playback, window
        self.send_lock = asyncio.Lock()
        self.incoming = asyncio.Queue(maxsize=64)
        self.queued_bytes = self.input_bytes = 0
        self.waiting_input = self.receiving = False
        self.listen_ready = self.speech_started = self.speech_reported = False
        self.input_stopping = False
        self.idle_deadline = self.capture_deadline = self.speech_start_bytes = None
        self.output_active = self.output_end_sent = False
        self.sent_bytes = self.consumed_bytes = 0
        self.credit = asyncio.Event()
        self.playback_done = asyncio.Event()
        self.reader = self.runner = None
        self.stopped = False
        self.response = ""
        self.turns = 0

    async def send(self, event, payload=None, terminal=False):
        if not terminal:
            self.session.check()
        message = {"protocol_version": 1, "event": event,
                   "session_id": self.session.session_id, "turn_id": self.session.turn_id,
                   "payload": payload or {}}
        async with self.send_lock:
            await asyncio.wait_for(self.ws.send_json(message), 2)
            if event == "output.end":
                self.output_end_sent = True

    async def observe(self, event, payload):
        if event == "user_message":
            await self.send("transcript", preview(payload["text"]))
        elif event == "assistant_start":
            self.response = ""
            await self.send("assistant.state", {"state": "processing"})
        elif event == "assistant_token":
            self.response += payload["token"]
        elif event == "assistant_end" and payload.get("status") == "complete":
            await self.send("assistant.response", {**preview(self.response), "display_only": True})

    def enqueue(self, kind, payload):
        size = len(payload) if kind == "pcm" else 0
        if self.queued_bytes + size > 65536:
            raise ESPProtocolError("input_queue_limit")
        try:
            self.incoming.put_nowait((kind, payload))
        except asyncio.QueueFull:
            raise ESPProtocolError("input_queue_limit") from None
        self.queued_bytes += size

    async def receive(self):
        while True:
            packet = await self.ws.receive()
            if packet["type"] == "websocket.disconnect":
                raise ESPProtocolError("disconnected")
            pcm = packet.get("bytes")
            if pcm is not None:
                if (not self.receiving or not self.listen_ready or not pcm or len(pcm) % 2 or len(pcm) > 4096):
                    raise ESPProtocolError("invalid_input_pcm")
                self.input_bytes += len(pcm)
                if (self.speech_start_bytes is not None and not self.input_stopping
                        and self.input_bytes - self.speech_start_bytes > int(32000 * esp_settings.max_input_seconds)):
                    raise ESPProtocolError("input_duration_limit")
                self.enqueue("pcm", pcm)
                continue
            message = read_control(packet.get("text"))
            if (message.get("session_id") != self.session.session_id
                    or message.get("turn_id") != self.session.turn_id):
                raise ESPProtocolError("session_or_turn_mismatch")
            event, payload = message["event"], message["payload"]
            if event == "session.cancel":
                self.session.cancelled = True
                raise ESPProtocolError("cancelled")
            if event == "input.start":
                if (not self.waiting_input or self.receiving or payload != FORMAT
                        or any(type(payload.get(key)) is not type(value) for key, value in FORMAT.items())):
                    raise ESPProtocolError("invalid_input_start")
                self.waiting_input, self.receiving = False, True
                self.input_bytes = 0
                self.enqueue("start", payload)
            elif event == "input.ready":
                if not self.receiving or self.listen_ready or payload:
                    raise ESPProtocolError("invalid_input_ready")
                self.listen_ready = True
                self.idle_deadline = time.monotonic() + settings.conversation_idle_timeout
                self.enqueue("ready", payload)
                logger.info("Sessao %s turno %s: escuta pronta; inatividade=%.2fs",
                            self.session.session_id, self.session.turn_id, settings.conversation_idle_timeout)
            elif event == "input.speech_started":
                if not self.receiving or not self.listen_ready or self.speech_reported or payload:
                    raise ESPProtocolError("invalid_speech_started")
                self.speech_reported = True
                self.start_speech("esp")
                self.enqueue("speech", payload)
            elif event == "input.end":
                if not self.receiving or set(payload) != {"reason", "total_samples"}:
                    raise ESPProtocolError("invalid_input_end")
                if type(payload["total_samples"]) is not int or payload["total_samples"] * 2 != self.input_bytes:
                    raise ESPProtocolError("input_totals_mismatch")
                self.receiving = False
                self.enqueue("end", payload)
            elif event == "playback.credit":
                consumed = payload.get("consumed_bytes")
                if (not self.output_active or set(payload) != {"consumed_bytes"}
                        or type(consumed) is not int or consumed % 2
                        or not self.consumed_bytes <= consumed <= self.sent_bytes):
                    raise ESPProtocolError("invalid_playback_credit")
                self.consumed_bytes = consumed
                self.credit.set()
            elif event == "playback.finished":
                async with self.send_lock:
                    if (not self.output_active or not self.output_end_sent or self.playback_done.is_set()
                            or payload != {"total_bytes": self.sent_bytes,
                                          "total_samples": self.sent_bytes // 2}
                            or any(type(value) is not int for value in payload.values())):
                        raise ESPProtocolError("invalid_playback_ack")
                    self.playback_done.set()
            else:
                raise ESPProtocolError("unexpected_event")

    async def next_input(self, deadline):
        timeout = min(deadline, self.session.deadline) - time.monotonic()
        if timeout <= 0:
            raise ESPProtocolError("input_timeout")
        try:
            kind, payload = await asyncio.wait_for(self.incoming.get(), timeout)
        except TimeoutError:
            raise ESPProtocolError("input_timeout") from None
        if kind == "pcm":
            self.queued_bytes -= len(payload)
        return kind, payload

    def start_speech(self, source):
        # Executado no mesmo event loop que o leitor/VAD, sem await: cancelar e
        # iniciar a protecao da etapa e uma transicao indivisivel.
        if self.speech_started or self.input_stopping:
            return
        now = time.monotonic()
        self.speech_started = True
        self.idle_deadline = None
        self.capture_deadline = now + esp_settings.max_input_seconds
        self.speech_start_bytes = self.input_bytes
        self.session.deadline = now + settings.interaction_timeout
        logger.info("Sessao %s turno %s: inicio de fala (%s); inatividade cancelada",
                    self.session.session_id, self.session.turn_id, source)

    async def capture(self):
        header_deadline = time.monotonic() + esp_settings.handshake_seconds
        kind, _ = await self.next_input(header_deadline)
        if kind != "start":
            raise ESPProtocolError("input_start_required")
        kind, _ = await self.next_input(header_deadline)
        if kind != "ready":
            raise ESPProtocolError("input_ready_required")
        audio, pending, preroll = bytearray(), bytearray(), bytearray()
        voice_frames = silence = count = stop_count = 0
        speech = stopping = idle_expired = False
        stop_deadline = None
        next_log = time.monotonic() + 2
        while True:
            deadline = (stop_deadline if stopping else self.capture_deadline
                        if self.speech_started else self.idle_deadline)
            if deadline is None:
                raise ESPProtocolError("input_not_ready")
            try:
                kind, payload = await self.next_input(deadline)
            except ESPProtocolError as error:
                if str(error) != "input_timeout" or stopping:
                    raise
                if self.session.remaining <= 0:
                    raise ESPProtocolError("interaction_timeout") from None
                # Um aviso de fala pode ter cancelado o deadline enquanto
                # next_input aguardava com o prazo antigo.
                if self.speech_started and time.monotonic() < self.capture_deadline:
                    continue
                if not self.speech_started and self.idle_deadline is not None and time.monotonic() < self.idle_deadline:
                    continue  # wait_for pode acordar antes pela resolucao do relogio.
                if speech:
                    raise ESPProtocolError("input_duration_limit") from None
                idle_expired = not self.speech_started
                stopping = self.input_stopping = True
                self.idle_deadline = None
                stop_count = count
                stop_deadline = time.monotonic() + 2
                reason = "conversation_idle_timeout" if idle_expired else "no_speech"
                logger.info("Sessao %s turno %s: parada de entrada; motivo=%s",
                            self.session.session_id, self.session.turn_id, reason)
                await self.send("input.stop", {"reason": reason})
                continue
            if kind == "speech":
                continue
            if kind == "end":
                if payload["reason"] not in {"complete", "client_vad", "server_vad", "no_speech",
                                            "conversation_idle_timeout"}:
                    raise ESPProtocolError("capture_limit_or_error")
                if payload["total_samples"] * 2 != count:
                    raise ESPProtocolError("input_totals_mismatch")
                return None if idle_expired else bytes(audio) if speech else b""
            if kind != "pcm":
                raise ESPProtocolError("unexpected_input_event")
            count += len(payload)
            if stopping:
                if count - stop_count > 65536:
                    raise ESPProtocolError("input_stop_not_observed")
                continue
            pending.extend(payload)
            while len(pending) >= 640:
                raw = bytes(pending[:640])
                del pending[:640]
                block = array("h")
                block.frombytes(raw)
                if sys.byteorder != "little":
                    block.byteswap()
                rms = math.sqrt(sum(sample * sample for sample in block) / 320)
                if not speech:
                    preroll.extend(raw)
                    if len(preroll) > PREROLL_BYTES:
                        del preroll[:-PREROLL_BYTES]
                    voice_frames = voice_frames + 1 if rms >= esp_settings.vad_start_rms else 0
                    speech = voice_frames >= 3
                    if speech:
                        audio.extend(preroll)
                        preroll.clear()
                        # Confirmacao por PCM tambem protege a transicao do timer.
                        self.start_speech("server_vad")
                        logger.info("Sessao %s: VAD inicio de fala; RMS=%d limiar=%d",
                                    self.session.session_id, int(rms), esp_settings.vad_start_rms)
                else:
                    audio.extend(raw)
                    silence = silence + 320 if rms < esp_settings.vad_end_rms else 0
                    if silence >= int(16000 * esp_settings.silence_seconds):
                        stopping = self.input_stopping = True
                        stop_count = count
                        stop_deadline = min(self.session.deadline, time.monotonic() + 2)
                        logger.info("Sessao %s: VAD fim de fala; silencio=%.2fs audio=%.2fs",
                                    self.session.session_id, silence / 16000, len(audio) / 32000)
                        await self.send("input.stop", {"reason": "vad_silence"})
                        break
                if len(audio) > int(32000 * esp_settings.max_input_seconds) + PREROLL_BYTES:
                    raise ESPProtocolError("input_duration_limit")
                if time.monotonic() >= next_log:
                    logger.info("Sessao %s: PCM recebido=%.2fs RMS=%d fala=%s silencio=%.2fs",
                                self.session.session_id, count / 32000, int(rms), speech, silence / 16000)
                    next_log = time.monotonic() + 2

    async def run(self):
        turns = 0
        await self.manager.engine.event(self.session, "wake_word")
        await self.manager.engine.event(self.session, "conversation_start")
        while True:
            self.session.check()
            self.waiting_input = True
            self.listen_ready = self.speech_started = self.speech_reported = False
            self.input_stopping = False
            self.idle_deadline = self.capture_deadline = self.speech_start_bytes = None
            # O prazo de protecao pertence ao turno ativo, nao a idade da conexao.
            # So o input.ready do ESP arma o timeout de inatividade.
            self.session.deadline = float("inf")
            self.response = ""
            await self.manager.engine.set_state("listening")
            await self.send("session.ready", {**FORMAT,
                "output_mode": self.manager.engine.output.mode_for(self.session),
                "vad_start_rms": esp_settings.vad_start_rms,
                "conversation_events": True, "continuation": turns > 0})
            audio = await self.capture()
            if audio is None:
                return "inactivity"
            if not audio:
                self.session.turn_id = uuid4().hex
                continue
            await self.send("assistant.state", {"state": "processing"})
            await self.manager.engine.set_state("processing")
            started = time.monotonic()
            text = await self.manager.transcriber.transcribe_pcm(
                audio, timeout=min(60, self.session.remaining))
            self.session.check()
            logger.info("Sessao %s turno %s: STT %d ms, audio %d bytes",
                        self.session.session_id, self.session.turn_id,
                        int((time.monotonic() - started) * 1000), len(audio))
            if not text:
                logger.info("Sessao %s turno %s: transcricao vazia; retomando escuta",
                            self.session.session_id, self.session.turn_id)
                self.session.turn_id = uuid4().hex
                continue
            result = await self.manager.engine.process_turn(text, self.session, classify_dismissal=True)
            if result.status != "complete":
                raise ESPProtocolError("turn_or_delivery_failed")
            turns += 1
            self.turns = turns
            if result.dismissal:
                return "completed"
            self.session.turn_id = uuid4().hex

    async def play_text(self, text, engine, worker):
        if not self.playback or engine is None or not hasattr(engine, "synthesize_pcm"):
            raise ESPProtocolError("esp32_playback_unavailable")
        from pcm_stream import pcm16_blocks
        blocks = queue.Queue(maxsize=24)
        stop = threading.Event()
        deadline = min(self.session.deadline, time.monotonic() + esp_settings.playback_seconds + 5)
        self.output_active = True
        self.output_end_sent = False
        self.sent_bytes = self.consumed_bytes = 0
        self.credit.clear()
        self.playback_done.clear()

        def produce():
            for block in pcm16_blocks(engine.synthesize_pcm(text), stop, esp_settings.playback_seconds):
                while not stop.is_set() and not worker.cancel_event.is_set():
                    if time.monotonic() >= deadline:
                        raise ESPProtocolError("synthesis_timeout")
                    try:
                        blocks.put(block, timeout=.05)
                        break
                    except queue.Full:
                        continue
                if stop.is_set() or worker.cancel_event.is_set():
                    return

        try:
            await self.send("output.start", {**FORMAT, "block_samples": 320, "block_bytes": 640})
            future = worker.start(produce)
            while True:
                self.session.check()
                if time.monotonic() >= deadline:
                    raise ESPProtocolError("playback_timeout")
                if future.done() and future.exception():
                    raise future.exception()
                try:
                    block = blocks.get_nowait()
                except queue.Empty:
                    if future.done():
                        future.result()
                        break
                    await asyncio.sleep(.01)
                    continue
                while self.sent_bytes + len(block) - self.consumed_bytes > self.window:
                    self.credit.clear()
                    if self.sent_bytes + len(block) - self.consumed_bytes <= self.window:
                        break
                    await asyncio.wait_for(self.credit.wait(), min(5, max(.001, deadline - time.monotonic())))
                    self.session.check()
                self.sent_bytes += len(block)
                async with self.send_lock:
                    await asyncio.wait_for(self.ws.send_bytes(block), 2)
            if not self.sent_bytes:
                raise ESPProtocolError("empty_synthesis")
            await self.send("output.end", {"total_bytes": self.sent_bytes,
                                          "total_samples": self.sent_bytes // 2})
            await asyncio.wait_for(self.playback_done.wait(), max(.001, deadline - time.monotonic()))
            self.session.check()
            logger.info("Sessão %s turno %s: playback confirmado, %d bytes",
                        self.session.session_id, self.session.turn_id, self.sent_bytes)
        finally:
            stop.set()
            worker.cancel()
            self.output_active = False

    async def serve(self):
        self.session.esp_link = self
        started = time.monotonic()
        result, code = "error", "session_failed"
        self.reader = asyncio.create_task(self.receive(), name="esp-v1-receive")
        self.runner = asyncio.create_task(self.run(), name="esp-v1-interaction")
        try:
            done, _ = await asyncio.wait((self.reader, self.runner),
                return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise ESPProtocolError("interaction_timeout")
            if self.reader in done:
                self.reader.result()
                raise ESPProtocolError("disconnected")
            result, code = self.runner.result(), ""
        except asyncio.CancelledError:
            result, code = "cancelled", "server_shutdown"
            raise
        except Exception as error:
            code = str(error) if isinstance(error, ESPProtocolError) else "session_failed"
            result = "cancelled" if code == "cancelled" else "error"
            logger.warning("Sessão %s encerrada: %s", self.session.session_id, code)
        finally:
            self.session.cancelled = True
            for task in (self.reader, self.runner):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(self.reader, self.runner, return_exceptions=True)
            try:
                if code:
                    await self.manager.engine.event(self.session, "error", code=code)
                await self.manager.engine.event(self.session, "conversation_end", turns=self.turns)
            except Exception:
                pass
            try:
                if code:
                    await self.send("session.error", {"code": code}, terminal=True)
                await self.send("session.end", {"result": result, "code": code}, terminal=True)
            except Exception:
                pass
            self.manager.engine.release(self.session)
            self.session.esp_link = None
            logger.info("Sessao %s: resultado=%s turnos=%d duracao_ms=%d",
                        self.session.session_id, result, self.turns,
                        int((time.monotonic() - started) * 1000))
            while not self.incoming.empty():
                self.incoming.get_nowait()
            try:
                await asyncio.wait_for(self.ws.close(code=1000 if not code else 1008), 2)
            except Exception:
                pass
            await self.manager.return_idle()


class ESPTransport:
    def __init__(self, engine, transcriber, admit, return_idle):
        self.engine, self.transcriber = engine, transcriber
        self.admit, self.return_idle = admit, return_idle
        self.connections = set()
        self.sockets = set()
        self.closing = False

    async def serve(self, ws):
        expected = "Bearer " + settings.esp_token
        if (self.closing or not settings.esp_token
                or not hmac.compare_digest(ws.headers.get("authorization", "").encode(),
                                           expected.encode()) or len(self.sockets) >= 4):
            await ws.close(code=1008)
            return
        self.sockets.add(ws)
        connection = session = None
        try:
            await ws.accept()
            hello = read_control(await asyncio.wait_for(ws.receive_text(), esp_settings.handshake_seconds))
            payload = hello["payload"]
            if (hello["event"] != "session.start" or hello.get("session_id") or hello.get("turn_id")
                    or set(payload) != {"device_id", "playback", "playback_buffer_bytes", "conversation_events"}
                    or payload.get("conversation_events") is not True
                    or payload["device_id"] != settings.esp_device_id
                    or type(payload["playback"]) is not bool
                    or type(payload["playback_buffer_bytes"]) is not int
                    or not (4096 if payload["playback"] else 640) <= payload["playback_buffer_bytes"] <= 16384):
                raise ESPProtocolError("invalid_session_start")
            if self.transcriber is None:
                raise ESPProtocolError("stt_unavailable")
            if settings.output_mode == "esp32" and (
                    not payload["playback"] or self.engine.output.engine is None):
                raise ESPProtocolError("esp32_playback_unavailable")
            session = await self.admit("esp32", "esp:" + settings.esp_device_id)
            logger.info("Sessao %s: WS v1 admitido; saida=%s playback=%s",
                        session.session_id, self.engine.output.mode_for(session), payload["playback"])
            connection = ESPConnection(self, ws, session, payload["playback"],
                                       payload["playback_buffer_bytes"])
            self.connections.add(connection)
            await connection.serve()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = ("busy" if isinstance(error, CapacityBusy) else
                    str(error) if isinstance(error, ESPProtocolError) else "handshake_failed")
            try:
                await asyncio.wait_for(ws.send_json({"protocol_version": 1, "event": "session.error",
                    "payload": {"code": code}}), 2)
                await asyncio.wait_for(ws.close(code=1008), 2)
            except Exception:
                pass
        finally:
            self.sockets.discard(ws)
            self.connections.discard(connection)
            if session is not None and self.engine.active is session:
                self.engine.release(session)
                await self.return_idle()

    async def shutdown(self):
        self.closing = True
        for connection in list(self.connections):
            connection.session.cancelled = True
            if connection.runner:
                connection.runner.cancel()
            if connection.reader:
                connection.reader.cancel()
        await asyncio.gather(*(ws.close(code=1001) for ws in list(self.sockets)), return_exceptions=True)
