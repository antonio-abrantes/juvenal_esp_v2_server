"""Adaptador explícito do WS antigo; não implementa o contrato v1 da fase 03."""

import asyncio
import hmac
import json
from uuid import uuid4

from fastapi import WebSocketDisconnect

from config import settings
from runtime import CapacityBusy


async def serve_legacy_esp(ws, transcriber, admit, converse, emit, spawn):
    authenticated = bool(settings.esp_token)
    if authenticated and not hmac.compare_digest(
            ws.headers.get("authorization", ""), "Bearer " + settings.esp_token):
        await ws.close(code=1008)
        return
    await ws.accept()
    # Sem credencial, não aceitar identidade fornecida pelo cliente para retomar contexto.
    identity = "esp:" + (settings.esp_device_id if authenticated else uuid4().hex)
    queue = asyncio.Queue(maxsize=16)  # Cada frame <= 4096 bytes: teto 64 KiB.
    send_lock = asyncio.Lock()
    task = None
    receiving_audio = False

    async def send(event):
        async with send_lock:
            await asyncio.wait_for(ws.send_json(event), 2)

    async def listen(session, initial_prompt):
        nonlocal receiving_audio
        if transcriber is None:
            raise RuntimeError("asr_unavailable")
        while not queue.empty():
            queue.get_nowait()
        receiving_audio = True
        audio = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(30, session.remaining)
        await send({"event": "start_audio"})
        try:
            while loop.time() < deadline:
                wait = settings.esp_silence_timeout if audio else settings.conversation_timeout
                try:
                    chunk = await asyncio.wait_for(queue.get(), min(wait, deadline - loop.time()))
                except asyncio.TimeoutError:
                    if audio and loop.time() >= deadline:
                        raise RuntimeError("capture_limit") from None
                    break
                if chunk is None:
                    break
                audio.extend(chunk)
                if len(audio) > 960000:
                    raise RuntimeError("capture_limit")
            else:
                if audio:
                    raise RuntimeError("capture_limit")
        finally:
            receiving_audio = False
            await send({"event": "stop_audio"})
        session.check()
        if not audio:
            return None
        # O legado usa pausa de pacotes. VAD de voz e WS por sessão são da fase 03.
        return await transcriber.transcribe_pcm(bytes(audio), initial_prompt, min(60, session.remaining))

    await emit({"event": "esp_connected"})
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            frame = message.get("bytes")
            if frame:
                if len(frame) > 4096 or len(frame) % 2:
                    await ws.close(code=1009)
                    break
                if receiving_audio:
                    try:
                        queue.put_nowait(frame)
                    except asyncio.QueueFull:
                        await send({"event": "error", "code": "audio_queue_limit"})
                        await ws.close(code=1009)
                        break
                continue
            text = message.get("text", "")
            if len(text.encode("utf-8")) > 8192:
                await ws.close(code=1009)
                break
            try:
                control = json.loads(text)
                if not isinstance(control, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                await send({"event": "error", "code": "invalid_control"})
                continue
            if "protocol_version" in control:
                await send({"event": "error", "code": "protocol_v1_not_available"})
                continue
            action = control.get("action")
            if action == "ping":
                await send({"event": "pong"})
            elif action == "wake":
                if transcriber is None:
                    await send({"event": "error", "code": "asr_unavailable"})
                    continue
                try:
                    session = await admit("esp32", identity)
                except CapacityBusy:
                    await send({"event": "busy"})
                    continue
                task = spawn(converse(session, listen), "legacy-esp-conversation")
            elif action == "stop_audio" and receiving_audio:
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    await ws.close(code=1009)
                    break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        while not queue.empty():
            queue.get_nowait()
        await emit({"event": "esp_disconnected"})
