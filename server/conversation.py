"""Turno comum, contextos limitados e admissão serializada entre as entradas."""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from uuid import uuid4

from config import settings, tool_settings
from diagnostics import log_text
from dismissal_checker import _is_dismissal_candidate, confirm_dismissal_with_llm
from llm_client import LLMError
from runtime import CapacityBusy
from tools.registry import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    history: list = field(default_factory=list)
    updated: float = field(default_factory=time.monotonic)
    pending_action: object = None

    def commit(self, user, assistant):
        self.commit_messages([{"role": "user", "content": user},
                              {"role": "assistant", "content": assistant}])

    def commit_messages(self, messages):
        self.history.extend(messages)
        while self.history and (len(self.history) > settings.history_messages or
                len(json.dumps(self.history, ensure_ascii=False).encode("utf-8")) > settings.history_bytes):
            # Remover o turno inteiro preserva cada par tool_call/resultado.
            next_user = next((index for index, message in enumerate(self.history[1:], 1)
                              if message["role"] == "user"), len(self.history))
            del self.history[:next_user]
        self.updated = time.monotonic()


@dataclass
class Interaction:
    origin: str
    context: ConversationContext
    identity: str = ""
    session_id: str = field(default_factory=lambda: uuid4().hex)
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    deadline: float = field(default_factory=lambda: time.monotonic() + settings.interaction_timeout)
    cancelled: bool = False
    tool_results: dict = field(default_factory=dict)
    offered_automations: set = field(default_factory=set)
    catalog_revision: str = ""
    esp_link: object = None
    hass_delivery: str = ""
    hass_external_accepted: bool = False
    hass_http_status: int | None = None
    internet_evidence: bool = False

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
    def __init__(self, llm, output, emit, set_state, transcriber=None, tools=None):
        self.llm, self.output = llm, output
        self.emit, self.set_state = emit, set_state
        self.transcriber = transcriber
        self.tools = tools
        self.contexts = OrderedDict()
        self.active = None
        self.closing = False

    @property
    def workers_busy(self):
        return (self.llm.busy or self.output.busy or bool(self.tools and self.tools.busy)
                or bool(self.transcriber and self.transcriber.busy))

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
        self.active = Interaction(origin, context, identity=identity)
        return self.active

    def clear(self, identity):
        if self.active is not None or self.workers_busy:
            raise CapacityBusy("busy")
        self.contexts.pop(identity, None)

    def release(self, session):
        session.cancelled = True
        pending = session.context.pending_action
        if pending is not None and not pending.delivered:
            session.context.pending_action = None
        if self.active is session:
            self.active = None
        self.llm.cancel()
        self.output.cancel()
        if self.tools:
            self.tools.cancel()

    async def event(self, session, event, **payload):
        if session.esp_link is not None:
            await session.esp_link.observe(event, payload)
        await self.emit({"event": event, "session_id": session.session_id,
                         "turn_id": session.turn_id, "origin": session.origin, **payload})

    async def announce(self, text, session):
        session.check()
        if not isinstance(text, str) or len(text.encode("utf-8")) > settings.text_bytes:
            raise ValueError("announcement_text_limit")
        await self.event(session, "assistant_start")
        await self.event(session, "assistant_token", token=text)
        await self.event(session, "assistant_end", status="complete")
        if session.hass_delivery != "external":
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
        if session.esp_link is None:
            session.turn_id = uuid4().hex
        session.tool_results.clear()
        session.hass_delivery = ""
        session.hass_external_accepted = False
        session.hass_http_status = None
        started = time.monotonic()
        if settings.log_transcripts:
            label = "Entrada textual" if session.origin == "web" else "Transcrição reconhecida"
            logger.info("Turno %s: %s: %s", session.turn_id, label,
                        log_text(text, settings.text_bytes))
        await self.event(session, "user_message", text=text)
        await self.set_state("processing")
        dismissal = False
        if classify_dismissal and session.context.pending_action is None and _is_dismissal_candidate(text):
            dismissal = await asyncio.wait_for(
                confirm_dismissal_with_llm(text, self.llm), min(15, session.remaining))
            session.check()
        await self.event(session, "assistant_start")
        response = ""
        trace = [{"role": "user", "content": text}]
        try:
            response = await self._generate_response(text, session, trace)
            if not response.strip() or len(response.encode("utf-8")) > settings.text_bytes:
                raise LLMError("response_limit_or_empty")
            session.check()
        except asyncio.CancelledError:
            session.context.pending_action = None
            self._preserve_interrupted_trace(session, trace)
            raise
        except Exception as exc:
            session.context.pending_action = None
            had_tools = self._preserve_interrupted_trace(session, trace)
            code = str(exc) if isinstance(exc, LLMError) else "conversation_failed"
            logger.warning("Turno %s falhou: %s", session.turn_id, code)
            await self.event(session, "assistant_end", status="failed")
            await self.event(session, "error", code=code)
            # Resposta parcial nunca é sintetizada nem enviada ao endpoint.
            if session.remaining > 0 and not session.cancelled and not self.output.busy:
                message = ("A conversa foi interrompida após uma etapa de automação. "
                           "Não vou repetir ações; o resultado disponível ficou registrado."
                           if had_tools else
                           "Desculpe, não consegui concluir sua resposta. Pode tentar novamente?")
                await self.announce(message, session)
            return TurnResult("failed")
        trace.append({"role": "assistant", "content": response})
        session.context.commit_messages(trace)
        logger.info("Turno %s: resposta concluída em %d ms", session.turn_id,
                    int((time.monotonic() - started) * 1000))
        await self.event(session, "assistant_end", status="complete")
        if session.hass_delivery != "external":
            await self.set_state("speaking")
        delivery_started = time.monotonic()
        try:
            delivery = await self.output.deliver_response(response, session)
        except BaseException:
            session.context.pending_action = None
            raise
        if not delivery.ok:
            session.context.pending_action = None
        elif self.tools:
            self.tools.confirmation_delivered(session)
        await self.event(session, "delivery_result", status=delivery.status, mode=delivery.mode,
                         error=delivery.error, http_status=delivery.http_status)
        self._log_delivery(session, delivery, int((time.monotonic() - delivery_started) * 1000))
        return TurnResult("complete" if delivery.ok else "delivery_failed", response, dismissal)

    @staticmethod
    def _preserve_interrupted_trace(session, trace):
        if not any(message["role"] == "tool" for message in trace):
            return False
        session.context.commit_messages([*trace, {"role": "assistant", "content":
            "Conversa interrompida. Preserve os resultados das ferramentas; não repita ações."}])
        return True

    async def _batch(self, calls, session, trace):
        trace.append({"role": "assistant", "content": None,
                      "tool_calls": [call.message() for call in calls]})
        results = []
        try:
            for call in calls:
                session.check()
                definition = self.tools.registry.definitions.get(call.name)
                if results and results[-1].status != "success" and not (definition and definition.read_only):
                    result = ToolResult("denied", "batch_stopped",
                                        "Outra ação desta rodada precisa de atenção; nada foi enviado.")
                else:
                    result = await self.tools.registry.execute(call, session)
                results.append(result)
                trace.append({"role": "tool", "tool_call_id": call.call_id,
                              "content": result.content()})
                await self.event(session, "tool_result", name=call.name,
                                 status=result.status, code=result.code)
                if definition and definition.read_only and result.status == "success":
                    await self.event(session, "information_sources", name=call.name, data=result.data)
        finally:
            # Cancelamento não pode deixar chamadas sem resultado no histórico.
            for call in calls[len(results):]:
                trace.append({"role": "tool", "tool_call_id": call.call_id,
                    "content": ToolResult("unknown", "tool_interrupted",
                        "Espera interrompida; não repetir a ação.").content()})
        return results

    async def _generate_response(self, text, session, trace):
        session.internet_evidence = False
        if self.tools and session.context.pending_action is not None:
            pending = session.context.pending_action
            call = ToolCall("confirm_" + uuid4().hex, "home_assistant", pending.arguments)
            trace.append({"role": "assistant", "content": None, "tool_calls": [call.message()]})
            try:
                result = await self.tools.consume_confirmation(text, session)
            except BaseException:
                trace.append({"role": "tool", "tool_call_id": call.call_id, "content":
                    ToolResult("unknown", "confirmation_interrupted",
                               "Confirmação interrompida; não repetir.").content()})
                raise
            trace.append({"role": "tool", "tool_call_id": call.call_id, "content": result.content()})
            await self.event(session, "tool_result", name="home_assistant",
                             status=result.status, code=result.code)
            await self.event(session, "assistant_token", token=result.message)
            return result.message
        call_count = 0
        call_ids = set()
        confirmed = []
        for round_index in range(tool_settings.max_rounds + 1):
            session.check()
            schemas, instructions, revision = (await self.tools.offer(session) if self.tools
                                               else ([], "", ""))
            if round_index >= tool_settings.max_rounds or call_count >= tool_settings.max_calls:
                schemas = []
                instructions += "\nLimite de ferramentas atingido. Termine sem pedir novas ações."
            session.catalog_revision = revision
            reply, response = {}, ""
            stream = self.llm.chat(text, timeout=session.remaining,
                messages=[*session.context.history, *trace], tool_schemas=schemas,
                instructions=instructions, reply=reply)
            try:
                async for token in stream:
                    session.check()
                    response += token
                    # Com tools, aguardar conclusão para não anunciar uma ação ainda não executada.
                    if not schemas:
                        await self.event(session, "assistant_token", token=token)
            finally:
                await stream.aclose()
            calls = reply.get("tool_calls", [])
            if not calls:
                if schemas:
                    await self.event(session, "assistant_token", token=response)
                return response
            if (not self.tools or not schemas or len(calls) + call_count > tool_settings.max_calls
                    or any(call.call_id in call_ids for call in calls)):
                raise LLMError("tool_call_limit_or_duplicate")
            call_count += len(calls)
            call_ids.update(call.call_id for call in calls)
            results = await self._batch(calls, session, trace)
            if session.hass_delivery:
                # HA já produziu a fala, ou assumiu a fala remota. Não chamar
                # o LLM novamente para reescrever/confirmar/reexecutar a ação.
                response = "\n".join(item.message for item in results)
                await self.event(session, "assistant_token", token=response)
                return response
            for call, result in zip(calls, results):
                if result.status == "success" and not self.tools.registry.is_read_only(call.name):
                    label = call.arguments.get("automation_id", call.name)
                    if not isinstance(label, str) or len(label) > 64:
                        label = call.name
                    if label not in confirmed:
                        confirmed.append(label)
            interrupted = next((item for call, item in zip(calls, results)
                if item.status != "success" and not self.tools.registry.is_read_only(call.name)), None)
            if interrupted:
                summary = "".join("Execução confirmada para " + label + ". " for label in confirmed)
                response = summary + interrupted.message
                await self.event(session, "assistant_token", token=response)
                return response
        raise LLMError("tool_round_limit")
