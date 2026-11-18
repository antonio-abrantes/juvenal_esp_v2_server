"""Presença e reserva de eventos em espera, em WebSocket separado da conversa."""

import asyncio
import hmac
import logging
import time
from uuid import uuid4

from starlette.websockets import WebSocketDisconnect

from events_schema import EXPRESSIONS, EventError
from tools.registry import ArgumentError, decode_json

logger = logging.getLogger(__name__)
HEARTBEAT = 30
OFFER_MS = 5000
DELIVERY_HEADER = "x-juvenal-events-delivery"


class ControlError(ValueError):
    pass


async def receive(ws):
    packet = await ws.receive()
    if packet["type"] == "websocket.disconnect":
        return None, None
    raw = packet.get("text")
    if raw is None or not raw or len(raw.encode("utf-8")) > 2048:
        raise ControlError("control_frame_invalid")
    try:
        frame = decode_json(raw)
    except ArgumentError:
        raise ControlError("control_json_invalid") from None
    if (type(frame) is not dict or set(frame) != {"protocol_version", "event", "payload"}
            or type(frame["protocol_version"]) is not int or frame["protocol_version"] != 1
            or type(frame["event"]) is not str or type(frame["payload"]) is not dict):
        raise ControlError("control_frame_invalid")
    return frame["event"], frame["payload"]


async def send(ws, event, **payload):
    await asyncio.wait_for(ws.send_json({
        "protocol_version": 1, "event": event, "payload": payload,
    }), timeout=2)


class DeviceControl:
    def __init__(self, events_api, token):
        self.events = events_api
        self.token = token
        self.peer = None
        self.online = False
        self.state = None
        self.last_result = None
        self.closing = False
        self.pending_media = None
        self.expressions = ()
        self.delivery_peer = False
        self.speaker_available = False
        self.playback_available = lambda: False
        self.can_offer = lambda: True

    def capability(self):
        return {"online": self.online, "state": self.state, "last_result": self.last_result,
                "delivery": self.delivery_peer, "expressions": self.expressions}

    def authorized(self, ws):
        headers = ws.headers.getlist("authorization")
        return (not self.closing and not self.events.unavailable() and self.token and len(headers) == 1
                and hmac.compare_digest(
                    headers[0].encode("utf-8"), ("Bearer " + self.token).encode("utf-8")))

    def claim_media(self, payload):
        pending = self.pending_media
        if pending is None:
            return None
        ids, job, deadline = pending
        if time.monotonic() >= deadline:
            self.pending_media = None
            return None
        if any(payload[key] != value for key, value in ids.items()):
            return None
        self.pending_media = None
        return job

    async def serve(self, ws):
        if not self.authorized(ws) or self.peer is not None:
            await ws.close(code=1008)
            return
        self.peer = asyncio.current_task()
        reader = None
        close_code = 1000
        reaction = None
        try:
            await ws.accept()
            event, hello = await asyncio.wait_for(receive(ws), timeout=5)
            if event is None:
                return
            if (event != "device.hello" or set(hello) != {
                    "device_id", "notifications", "playback", "playback_buffer_bytes",
                    "expressions", "default_expression"}
                    or hello["device_id"] != self.events.config.device_id
                    or hello["notifications"] is not True or type(hello["playback"]) is not bool
                    or type(hello["playback_buffer_bytes"]) is not int
                    or hello["playback_buffer_bytes"] != 16384
                    or hello["default_expression"] != "neutral"):
                raise ControlError("control_hello_invalid")
            expressions = hello["expressions"]
            if (type(expressions) is not list or len(expressions) > len(EXPRESSIONS)
                    or any(type(value) is not str or value not in EXPRESSIONS for value in expressions)
                    or len(set(expressions)) != len(expressions) or "neutral" not in expressions):
                raise ControlError("control_expressions_invalid")

            await send(ws, "control.ready", connection_id=uuid4().hex,
                       heartbeat_seconds=HEARTBEAT, events_enabled=True)
            self.online = True
            self.expressions = tuple(expressions)
            self.delivery_peer = ws.headers.getlist(DELIVERY_HEADER) == ["1"]
            self.speaker_available = hello["playback"]
            logger.info("Controle de eventos conectado")
            reader = asyncio.create_task(receive(ws))
            offer = None
            offer_deadline = 0.0
            handoff_deadline = None
            reaction_deadline = 0.0
            sequence = 0
            pending_ping = None
            clock = asyncio.get_running_loop().time
            last_pong = clock()
            next_ping = last_pong + HEARTBEAT
            next_offer = last_pong
            while not self.closing:
                done, _ = await asyncio.wait({reader}, timeout=1)
                now = clock()
                if handoff_deadline is not None:
                    if time.monotonic() >= handoff_deadline:
                        return
                    if reader in done:
                        event, _ = reader.result()
                        if event is None:
                            return
                        reader = asyncio.create_task(receive(ws))
                    continue
                if now - last_pong >= 3 * HEARTBEAT:
                    raise ControlError("control_heartbeat_timeout")
                if reader in done:
                    event, payload = reader.result()
                    if event is None:
                        return
                    reader = asyncio.create_task(receive(ws))
                    if event == "control.pong":
                        if (set(payload) != {"sequence"} or type(payload["sequence"]) is not int
                                or payload["sequence"] != pending_ping):
                            raise ControlError("control_pong_invalid")
                        last_pong = now
                        pending_ping = None
                    elif event == "device.status":
                        state = payload.get("state")
                        if (set(payload) != {"state", "speaker_available"}
                                or type(state) is not str or state not in {
                                    "idle", "voice", "manual", "settings", "notification"}
                                or type(payload["speaker_available"]) is not bool):
                            raise ControlError("control_state_invalid")
                        self.state = state
                        self.speaker_available = payload["speaker_available"]
                    elif event == "notification.finished":
                        if (reaction is None or set(payload) != {"job_id", "offer_id", "result"}
                                or any(payload[key] != value for key, value in reaction.items())
                                or payload["result"] not in ("completed", "failed")):
                            raise ControlError("control_reaction_invalid")
                        result = payload["result"]
                        await self.events.store.finish_delivery(reaction["job_id"], result,
                                                                "" if result == "completed" else "reaction_failed")
                        self.last_result = result
                        reaction = None
                    elif event in {"notification.accepted", "notification.deferred"}:
                        keys = {"job_id", "offer_id"}
                        if event == "notification.deferred":
                            keys.add("reason")
                        if (offer is None or now >= offer_deadline or set(payload) != keys
                                or any(payload[key] != offer[key] for key in ("job_id", "offer_id"))):
                            raise ControlError("control_offer_stale")
                        if event == "notification.accepted":
                            remaining = candidate["expires_at"] - time.time()
                            if self.delivery_peer and remaining > 0 and self.can_offer():
                                if candidate["kind"] == "notification":
                                    handoff_deadline = time.monotonic() + min(5, remaining)
                                    self.pending_media = (offer, candidate, handoff_deadline)
                                    await send(ws, "notification.proceed", **offer)
                                    continue
                                reaction = offer
                                if await self.events.store.begin_effect(offer["job_id"]):
                                    await send(ws, "notification.proceed", **offer)
                                    offer = None
                                    reaction_deadline = clock() + 7
                                    continue
                                reaction = None
                            confirmed = (not self.delivery_peer and
                                         await self.events.store.confirm_control_offer(offer["job_id"]))
                            result = "control_confirmed" if confirmed else "expired" if remaining <= 0 else "deferred"
                            if not self.delivery_peer and not confirmed:
                                result = "expired"
                            await send(ws, "notification.release", **offer, result=result)
                        else:
                            result = payload["reason"]
                            if type(result) is not str or result not in {"busy", "not_ready", "unsupported"}:
                                raise ControlError("control_defer_invalid")
                        self.last_result = result
                        logger.info("Oferta de controle %s: %s", offer["job_id"], result)
                        offer = None
                    else:
                        raise ControlError("control_event_invalid")
                if offer is not None and now >= offer_deadline:
                    raise ControlError("control_offer_timeout")
                if reaction is not None and now >= reaction_deadline:
                    raise ControlError("control_reaction_timeout")
                if now >= next_ping and pending_ping is None:
                    sequence += 1
                    await send(ws, "control.ping", sequence=sequence)
                    pending_ping = sequence
                    next_ping = now + HEARTBEAT
                media_pending = self.pending_media is not None and time.monotonic() < self.pending_media[2]
                if (offer is None and reaction is None and not media_pending and self.state == "idle"
                        and now >= next_offer and not self.events.unavailable()
                        and (not self.delivery_peer or self.can_offer())):
                    next_offer = now + 1
                    try:
                        candidate = await self.events.store.take_control_offer(
                            hello["device_id"], expressions,
                            self.speaker_available and (not self.delivery_peer or self.playback_available()),
                            self.delivery_peer)
                    except EventError:
                        continue
                    if candidate is None:
                        continue
                    remaining_ms = min(86400000, int((candidate["expires_at"] - time.time()) * 1000))
                    if remaining_ms <= 0:
                        continue
                    offer = {"job_id": candidate["job_id"], "offer_id": uuid4().hex}
                    offer_deadline = clock() + min(OFFER_MS, remaining_ms) / 1000
                    await send(ws, "notification.offer", **offer, kind=candidate["kind"],
                               expression=candidate["expression"], remaining_ms=remaining_ms,
                               offer_timeout_ms=OFFER_MS)
        except (ControlError, EventError, TimeoutError) as error:
            close_code = 1008
            self.last_result = str(error) if isinstance(error, (ControlError, EventError)) else "control_timeout"
            logger.info("Controle de eventos encerrado: %s", self.last_result)
        except (RuntimeError, OSError, WebSocketDisconnect):
            self.last_result = "control_disconnected"
        finally:
            self.online = False
            self.state = None
            self.delivery_peer = False
            self.expressions = ()
            self.speaker_available = False
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            try:
                await asyncio.wait_for(ws.close(code=1001 if self.closing else close_code), timeout=2)
            except (RuntimeError, OSError, TimeoutError, WebSocketDisconnect):
                pass
            finally:
                self.peer = None
                if reaction is not None:
                    try:
                        await self.events.store.finish_delivery(reaction["job_id"], "unknown", "reaction_ack_missing")
                    except EventError:
                        logger.warning("Estado da reação será recuperado no próximo início")

    async def close(self):
        self.closing = True
        if self.peer is not None:
            self.peer.cancel()
            await asyncio.gather(self.peer, return_exceptions=True)
