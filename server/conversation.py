"""Turno comum, contextos limitados e admissão serializada entre as entradas."""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from uuid import uuid4

from config import settings
from diagnostics import log_text
from dismissal_checker import _is_dismissal_candidate, confirm_dismissal_with_llm
from llm_client import LLMError
from runtime import CapacityBusy

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    history: list = field(default_factory=list)
    updated: float = field(default_factory=time.monotonic)

    def commit(self, user, assistant):
        self.history.extend([{"role": "user", "content": user},
                             {"role": "assistant", "content": assistant}])
        while self.history and (len(self.history) > settings.history_messages or sum(
                len(m["content"].encode("utf-8")) for m in self.history) > settings.history_bytes):
            # Nesta fase o histórico contém somente pares user/assistant completos.
            del self.history[:2]
        self.updated = time.monotonic()


@dataclass
class Interaction:
    origin: str
    context: ConversationContext
    session_id: str = field(default_factory=lambda: uuid4().hex)
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    deadline: float = field(default_factory=lambda: time.monotonic() + settings.interaction_timeout)
    cancelled: bool = False

    @property
    def remaining(self):
        return max(0, self.deadline - time.monotonic())

    def check(self):
        if self.cancelled:
            raise asyncio.CancelledError()
        if self.remaining <= 0:
            raise TimeoutError("interaction_timeout")


@dataclass(frozen=True)
class TurnResult:
    status: str
    text: str = ""
    dismissal: bool = False


class ConversationEngine:
    def __init__(self, llm, output, emit, set_state, transcriber=None):
        self.llm, self.output = llm, output
        self.emit, self.set_state = emit, set_state
        self.transcriber = transcriber
        self.contexts = OrderedDict()
        self.active = None
        self.closing = False

    @property
    def workers_busy(self):
        return self.llm.busy or self.output.busy or bool(self.transcriber and self.transcriber.busy)

    def begin(self, origin, identity):
        # Executado sem await: a reserva é indivisível no event loop.
        if self.closing or self.active is not None or self.workers_busy:
            raise CapacityBusy("busy")
        now = time.monotonic()
        for key in list(self.contexts):
            if now - self.contexts[key].updated >= settings.context_ttl:
                del self.contexts[key]
        if identity not in self.contexts:
            while len(self.contexts) >= settings.max_contexts:
                self.contexts.popitem(last=False)
            self.contexts[identity] = ConversationContext()
        context = self.contexts[identity]
        context.updated = now
        self.contexts.move_to_end(identity)
        self.active = Interaction(origin, context)
        return self.active

    def clear(self, identity):
        if self.active is not None or self.workers_busy:
            raise CapacityBusy("busy")
        self.contexts.pop(identity, None)

    def release(self, session):
        session.cancelled = True
        if self.active is session:
            self.active = None
        self.llm.cancel()
        self.output.cancel()

    async def event(self, session, event, **payload):
        await self.emit({"event": event, "session_id": session.session_id,
                         "turn_id": session.turn_id, "origin": session.origin, **payload})

    async def announce(self, text, session):
        session.check()
        if not isinstance(text, str) or len(text.encode("utf-8")) > settings.text_bytes:
            raise ValueError("announcement_text_limit")
        await self.event(session, "assistant_start")
        await self.event(session, "assistant_token", token=text)
        await self.event(session, "assistant_end", status="complete")
        await self.set_state("speaking")
        result = await self.output.deliver_response(text, session)
        await self.event(session, "delivery_result", status=result.status,
                         mode=result.mode, error=result.error, http_status=result.http_status)
        self._log_delivery(session, result)
        return result

    @staticmethod
    def _log_delivery(session, result, elapsed_ms=None):
        log = logger.info if result.ok else logger.warning
        log("Turno %s: entrega=%s destino=%s erro=%s http=%s duração_ms=%s",
            session.turn_id, result.status, result.mode, result.error or "none",
            result.http_status if result.http_status is not None else "none",
            elapsed_ms if elapsed_ms is not None else "n/a")

    async def process_turn(self, text, session, classify_dismissal=False):
        session.check()
        text = text.strip()
        if not text or len(text.encode("utf-8")) > settings.text_bytes:
            await self.event(session, "error", code="input_text_limit")
            return TurnResult("failed")
        session.turn_id = uuid4().hex
        started = time.monotonic()
        if settings.log_transcripts:
            label = "Entrada textual" if session.origin == "web" else "Transcrição reconhecida"
            logger.info("Turno %s: %s: %s", session.turn_id, label,
                        log_text(text, settings.text_bytes))
        await self.event(session, "user_message", text=text)
        await self.set_state("processing")
        dismissal = False
        if classify_dismissal and _is_dismissal_candidate(text):
            dismissal = await asyncio.wait_for(
                confirm_dismissal_with_llm(text, self.llm), min(15, session.remaining))
            session.check()
        await self.event(session, "assistant_start")
        response = ""
        stream = self.llm.chat(text, list(session.context.history), timeout=session.remaining)
        try:
            async for token in stream:
                session.check()
                response += token
                await self.event(session, "assistant_token", token=token)
            session.check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = str(exc) if isinstance(exc, LLMError) else "conversation_failed"
            logger.warning("Turno %s falhou: %s", session.turn_id, code)
            await self.event(session, "assistant_end", status="failed")
            await self.event(session, "error", code=code)
            # Resposta parcial nunca é sintetizada nem enviada ao endpoint.
            if session.remaining > 0 and not session.cancelled and not self.output.busy:
                await self.announce("Desculpe, não consegui concluir sua resposta. Pode tentar novamente?", session)
            return TurnResult("failed")
        finally:
            await stream.aclose()
        session.context.commit(text, response)
        logger.info("Turno %s: LLM concluído em %d ms", session.turn_id,
                    int((time.monotonic() - started) * 1000))
        await self.event(session, "assistant_end", status="complete")
        await self.set_state("speaking")
        delivery_started = time.monotonic()
        delivery = await self.output.deliver_response(response, session)
        await self.event(session, "delivery_result", status=delivery.status, mode=delivery.mode,
                         error=delivery.error, http_status=delivery.http_status)
        self._log_delivery(session, delivery, int((time.monotonic() - delivery_started) * 1000))
        return TurnResult("complete" if delivery.ok else "delivery_failed", response, dismissal)
