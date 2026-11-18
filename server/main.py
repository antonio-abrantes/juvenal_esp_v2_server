"""Juvenal Server: núcleo comum, saídas e ferramentas com catálogo administrável."""

import asyncio
import base64
import hmac
import ipaddress
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from config import PROJECT_ROOT, settings, esp_settings, hass_settings, events_settings, number, tts_options
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from conversation import ConversationEngine
from automation_catalog import CatalogStore
from catalog_api import create_catalog_router
from legacy_esp import serve_legacy_esp
from llm_client import LLMClient
from response_output import ResponseOutput
from runtime import CapacityBusy
from diagnostics import log_text
from tools.coordinator import ToolsCoordinator
from esp_transport import ESPTransport
from events_api import EventsAPI
from device_control import DeviceControl
from notification_transport import NotificationTransport

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
logger = logging.getLogger("juvenal")
engine = None
transcriber = None
wake_detector = None
connected_clients = set()
device_clients = set()
tasks = set()
agent_state = "idle"
resume_task = None
catalog = CatalogStore(settings.data_dir)
esp_transport = None
events_api = EventsAPI(settings.data_dir, events_settings)
device_control = DeviceControl(events_api, settings.esp_token)


def spawn(coroutine, name):
    task = asyncio.create_task(coroutine, name=name)
    tasks.add(task)
    def finished(item):
        tasks.discard(item)
        if not item.cancelled() and item.exception():
            logger.warning("Tarefa %s terminou com falha controlada", name)
    task.add_done_callback(finished)
    return task


async def broadcast(event):
    async def send(ws):
        try:
            await asyncio.wait_for(ws.send_json(event), 2)
        except Exception:
            connected_clients.discard(ws)
    await asyncio.gather(*(send(ws) for ws in list(connected_clients)))


async def set_state(state):
    global agent_state
    agent_state = state
    await broadcast({"event": "state_change", "state": state})


def panel_authenticated(headers):
    if not settings.panel_username:
        return True
    expected = "Basic " + base64.b64encode(
        f"{settings.panel_username}:{settings.panel_password}".encode()).decode()
    return hmac.compare_digest(headers.get("authorization", "").encode("utf-8"),
                               expected.encode("utf-8"))


def can_operate(ws):
    origin = ws.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != ws.headers.get("host"):
            return False
    if settings.panel_username:
        return panel_authenticated(ws.headers)
    try:
        return ipaddress.ip_address(ws.client.host).is_loopback
    except (ValueError, AttributeError):
        return False


async def admit(origin, identity):
    session = engine.begin(origin, identity)
    try:
        if wake_detector:
            wake_detector.pause()
            await wake_detector.wait_paused()
        return session
    except BaseException:
        engine.release(session)
        await return_to_idle()
        raise


async def return_to_idle():
    global resume_task
    if engine.closing or engine.active is not None:
        return
    if engine.workers_busy:
        await set_state("processing")
        if resume_task is None or resume_task.done():
            async def resume_when_free():
                while engine.workers_busy and not engine.closing:
                    await asyncio.sleep(.1)
                if not engine.closing:
                    await return_to_idle()
            resume_task = spawn(resume_when_free(), "wait-residual-worker")
        return
    await set_state("idle")
    if wake_detector:
        wake_detector.resume()


notifications = NotificationTransport(device_control, admit, return_to_idle)
events_api.delivery_capability = notifications.capability
events_api.validate_delivery = notifications.validate_delivery


async def local_listen(session, initial_prompt):
    if transcriber is None or not settings.local_voice_enabled:
        raise RuntimeError("local_voice_unavailable")
    await set_state("listening")
    texts = []
    def partial(text):
        if not session.cancelled:
            spawn(engine.event(session, "transcript_partial", text=text), "asr-feedback")
    def final(text):
        if not session.cancelled:
            texts.append(text)
            spawn(engine.event(session, "transcript_final", text=text), "asr-final")
    try:
        done = await transcriber.start_session(partial, final, lambda: None,
            max_wait_for_speech=min(settings.conversation_timeout, session.remaining),
            initial_prompt=initial_prompt)
        await asyncio.wait_for(done.wait(), min(95, session.remaining))
        if transcriber.last_error:
            raise RuntimeError(transcriber.last_error)
        session.check()
        return " ".join(texts).strip() or None
    finally:
        await transcriber.stop_session()


async def converse(session, listen):
    turns = 0
    last_text = None
    try:
        await engine.event(session, "wake_word")
        await set_state("wake")
        await engine.event(session, "conversation_start")
        while True:
            session.check()
            await set_state("listening")
            user_text = await listen(session, last_text)
            if not user_text:
                if settings.idle_enabled and settings.idle_message:
                    await engine.announce(settings.idle_message, session)
                break
            turns += 1
            result = await engine.process_turn(user_text, session, classify_dismissal=True)
            if result.status != "complete" or result.dismissal:
                break
            last_text = user_text
            # Sem confirmação de fala do receptor, não abrir escuta automaticamente.
            if engine.output.mode_for(session) == "endpoint" and not settings.legacy_endpoint:
                break
    except asyncio.CancelledError:
        raise
    except Exception:
        await engine.event(session, "error", code="interaction_failed")
        logger.warning("Interação %s interrompida", session.session_id)
    finally:
        engine.release(session)
        await engine.event(session, "conversation_end", turns=turns)
        await return_to_idle()


async def text_turn(session, text):
    try:
        await engine.process_turn(text, session)
    finally:
        engine.release(session)
        await return_to_idle()


async def on_wake_word_detected():
    try:
        session = await admit("local", "local")
    except CapacityBusy:
        return
    await converse(session, local_listen)


@asynccontextmanager
async def lifespan(app):
    global engine, transcriber, wake_detector, esp_transport
    output = ResponseOutput()
    llm = LLMClient()
    if settings.legacy_endpoint:
        logger.warning("Compatibilidade TTS_HA_WEBHOOK_URL ativa: ESP textual, demais entradas locais. "
                       "Migre explicitamente AUDIO_OUTPUT_MODE; nenhum valor secreto será exibido.")
    # HA direto responde no ESP mesmo quando a conversa geral usa saída endpoint.
    ha_playback = (hass_settings.availability()[0] and hass_settings.mode == "api"
                   and bool(settings.esp_token))
    if settings.output_mode in {"local", "esp32"} or ha_playback:
        try:
            from tts import create_tts_engine
            options = tts_options()
            if settings.output_mode == "esp32" or ha_playback:
                if options["backend"] != "piper":
                    raise ValueError("esp32_requires_piper")
                if settings.output_mode != "local":
                    options["output_device"] = None
            output.engine = await asyncio.to_thread(create_tts_engine, **options)
        except Exception:
            logger.warning("Motor de saída indisponível; veja dependências/modelo. Sem fallback de destino.")
    if settings.stt_enabled:
        try:
            from transcriber import create_transcriber
            transcriber = await asyncio.to_thread(create_transcriber)
        except Exception as error:
            logger.warning("STT indisponível (%s): %s; confira ASR_BACKEND, dependências e modelo. "
                           "Sem troca automática de backend; entrada textual continua disponível.",
                           type(error).__name__, log_text(error, limit=1024))
    tools = ToolsCoordinator(catalog, llm.provider)
    engine = ConversationEngine(llm, output, broadcast, set_state, transcriber, tools=tools)
    esp_transport = ESPTransport(engine, transcriber, admit, return_to_idle)
    capability = await tools.capability()
    logger.info("Consultas internet: %s", capability["internet"])
    logger.info("Home Assistant: habilitado=%s modo=%s exige_catalogo=%s log_conteudo=%s",
                hass_settings.enabled, hass_settings.mode, hass_settings.require_catalog,
                hass_settings.log_content)
    logger.info("Ferramentas: disponíveis=%s motivo=%s",
                capability["available"], capability["reason"] or "ready")
    try:
        if settings.local_voice_enabled and transcriber is not None:
            try:
                from wake_word import MicroWakeWordDetector
                wake_detector = MicroWakeWordDetector(
                    str(settings.wake_model), number("WAKE_WORD_THRESHOLD", .97, 1, 0))
                loop = asyncio.get_running_loop()
                def detected():
                    loop.call_soon_threadsafe(
                        lambda: spawn(on_wake_word_detected(), "local-conversation")
                        if not engine.closing else None)
                wake_detector.start_listening(detected)
            except Exception:
                logger.warning("Wake local indisponível; início manual continua disponível.")
        events_api.start()
        notifications.start(engine)
        logger.info("Juvenal pronto: saída=%s entrada_local=%s stt=%s",
                    settings.output_mode, settings.local_voice_enabled, transcriber is not None)
        yield
    finally:
        engine.closing = True
        events_api.closing = True
        if engine.active:
            engine.active.cancelled = True
        llm.cancel()
        output.cancel()
        tools.cancel()
        try:
            await asyncio.wait_for(esp_transport.shutdown(), 2)
        except TimeoutError:
            logger.warning("Prazo de encerramento das conexões ESP atingido")
        try:
            await notifications.close()
        except Exception:
            logger.warning("Falha ao encerrar a entrega de avisos")
        try:
            await device_control.close()
        except Exception:
            logger.warning("Falha ao encerrar o controle de eventos")
        try:
            await events_api.close()
        except Exception:
            logger.warning("Falha ao encerrar eventos; preservando cleanup da conversa")
        if wake_detector:
            await asyncio.to_thread(wake_detector.stop)
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        if transcriber:
            await transcriber.stop_session()
        for ws in list(connected_clients | device_clients):
            try:
                await asyncio.wait_for(ws.close(code=1001), 2)
            except Exception:
                pass
        connected_clients.clear()
        device_clients.clear()
        logger.info("Juvenal encerrando sem repetir ações ou entregas")


app = FastAPI(title="Juvenal Server", lifespan=lifespan)
app.include_router(create_catalog_router(
    catalog, panel_authenticated, lambda: engine.tools if engine else None))
app.include_router(events_api.router())
frontend_dir = PROJECT_ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def serve_index(request: Request):
    if not panel_authenticated(request.headers):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Juvenal"'})
    return FileResponse(frontend_dir / "index.html")


@app.get("/health")
async def health():
    output = engine.output if engine else None
    tool_capability = (await engine.tools.capability() if engine else
                       {"available": False, "reason": "server_starting", "catalog_valid": False})
    return {"status": "ok" if engine and not engine.closing else "starting",
            "state": agent_state, "llm": engine is not None,
            "asr": transcriber is not None, "tts": bool(output and output.engine),
            "wake_word": bool(wake_detector and wake_detector.is_listening()), "clients": len(connected_clients),
            "output_mode": settings.output_mode, "legacy_output": settings.legacy_endpoint,
            "busy": bool(engine and (engine.active or engine.workers_busy)),
            "capabilities": {"text_input": True, "local_voice": settings.local_voice_enabled,
                             "esp_legacy_input": bool(esp_settings.legacy_enabled and settings.esp_token
                                                      and transcriber is not None),
                             "esp_playback": bool(output and output.engine
                                 and hasattr(output.engine, "synthesize_pcm")),
                             "ws_v1": bool(settings.esp_token and transcriber is not None),
                             "tools": tool_capability, "events": {
                                 **events_api.capability(), "control": device_control.capability()}}}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not panel_authenticated(ws.headers) or len(connected_clients) >= 16:
        await ws.close(code=1008)
        return
    await ws.accept()
    connected_clients.add(ws)
    identity = "web:operator" if can_operate(ws) else "web:" + uuid4().hex
    control_context = "local" if settings.local_voice_enabled else "web"
    task = None
    await ws.send_json({"event": "connected", "state": agent_state})
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw.encode("utf-8")) > settings.text_bytes * 2:
                await ws.close(code=1009)
                break
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError()
            except ValueError:
                await ws.send_json({"event": "error", "code": "invalid_control"})
                continue
            if not can_operate(ws):
                await ws.send_json({"event": "error", "code": "panel_control_requires_auth"})
                continue
            action = message.get("action")
            try:
                if action == "clear_history":
                    target = message.get("context", control_context)
                    if target not in {"local", "web"}:
                        raise ValueError("invalid_context")
                    engine.clear("local" if target == "local" else identity)
                    await ws.send_json({"event": "history_cleared"})
                elif action == "send_text":
                    text = message.get("text", "")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    if len(text.encode("utf-8")) > settings.text_bytes:
                        raise ValueError("input_text_limit")
                    session = await admit("web", identity)
                    control_context = "web"
                    task = spawn(text_turn(session, text), "web-text-turn")
                elif action == "wake":
                    if not settings.local_voice_enabled or transcriber is None:
                        raise ValueError("local_voice_unavailable")
                    session = await admit("local", "local")
                    control_context = "local"
                    task = spawn(converse(session, local_listen), "web-local-conversation")
            except CapacityBusy:
                await ws.send_json({"event": "busy"})
            except ValueError:
                await ws.send_json({"event": "error", "code": "invalid_or_unavailable_action"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("Conexão do painel encerrada")
    finally:
        connected_clients.discard(ws)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@app.websocket("/ws/esp/control")
async def websocket_esp_control(ws: WebSocket):
    await device_control.serve(ws)


@app.websocket("/ws/esp/notify")
async def websocket_esp_notification(ws: WebSocket):
    await notifications.serve(ws)


@app.websocket("/ws/esp")
async def websocket_esp(ws: WebSocket):
    if esp_transport is None:
        await ws.close(code=1013)
        return
    await esp_transport.serve(ws)


@app.websocket("/ws/esp/legacy")
async def websocket_esp_legacy(ws: WebSocket):
    if not esp_settings.legacy_enabled or not settings.esp_token:
        await ws.close(code=1008)
        return
    if len(device_clients) >= 8:
        await ws.close(code=1008)
        return
    device_clients.add(ws)
    try:
        await serve_legacy_esp(ws, transcriber, admit, converse, broadcast, spawn)
    finally:
        device_clients.discard(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False,
                ws_max_size=max(8192, settings.text_bytes * 2), ws_max_queue=4, log_level="info")
