"""EV-01: HTTP autenticado, fila/status e manutenção; ainda sem dispatcher de áudio."""

import asyncio
import hmac
import logging
import math
import re
import time
from contextlib import contextmanager

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from diagnostics import log_text
from events_schema import BODY_LIMIT, EventError, compact, decode_event, parse_event, request_fingerprint
from events_store import EventsStore

logger = logging.getLogger(__name__)


def response(value, status=200, retry_after=None):
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(value, status_code=status, headers=headers)


class EventsAPI:
    def __init__(self, data_dir, config):
        self.config = config
        self.store = EventsStore(data_dir, config)
        self.closing = False
        self._worker = None
        self._failure = ""
        self._active_requests = 0
        self._tokens = 5.0
        self._rate_at = time.monotonic()
        self.delivery_capability = lambda: (False, "notification_unavailable")
        self.validate_delivery = lambda document: None

    def unavailable(self):
        if self.config.error:
            return self.config.error
        if not self.config.enabled:
            return "events_disabled"
        if not self.config.api_key:
            return "events_api_key_missing"
        if self.closing:
            return "events_stopping"
        if self._failure:
            return self._failure
        if self.store.error:
            return self.store.error
        if not self.store.ready:
            return "events_starting"
        return ""

    def capability(self):
        reason = self.unavailable()
        delivery_available, delivery_reason = self.delivery_capability()
        return {"enabled": self.config.enabled, "available": not bool(reason),
                "reason": reason or None, "stage": "notifications",
                "pending": self.store.pending_jobs,
                "delivery_available": not reason and delivery_available,
                "delivery_reason": reason or delivery_reason,
                "actions_available": False}

    def start(self):
        if self.config.enabled and not self.config.error and self.config.api_key:
            self._worker = asyncio.create_task(self._run(), name="events-worker")
        else:
            self._log_status()

    def _log_status(self):
        reason = self.unavailable()
        logger.info("Eventos: habilitado=%s recepcao=%s motivo=%s etapa=notifications",
                    self.config.enabled, not bool(reason), reason or "ready")

    async def _run(self):
        try:
            await self.store.start()
            self._log_status()
            while self.store.ready and not self.closing:
                await asyncio.sleep(1)
                try:
                    await self.store.maintain()
                except EventError:
                    # Erro sanitizado fica no health; próxima rodada pode recuperar a capacidade.
                    pass
        except Exception as error:
            self._failure = "events_worker_unavailable"
            logger.warning("Worker de eventos indisponível (%s)", type(error).__name__)

    async def close(self):
        self.closing = True
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        await self.store.close()

    def authorize(self, request):
        # A chave é própria do webhook. Não exigir Basic, Bearer ou cadastro de emissor.
        if self.config.error or not self.config.enabled or not self.config.api_key:
            raise EventError(self.unavailable(), 503)
        keys = request.headers.getlist("apikey")
        if (len(keys) != 1 or len(keys[0]) > 1024
                or not hmac.compare_digest(keys[0].encode("utf-8"), self.config.api_key.encode("utf-8"))):
            raise EventError("event_unauthorized", 401)
        reason = self.unavailable()
        if reason:
            raise EventError(reason, 503, 5)

    @contextmanager
    def request_slot(self):
        # Uma chave compartilhada = um bucket, sem confiar em IP/proxy/producer.
        now = time.monotonic()
        self._tokens = min(5.0, self._tokens + (now - self._rate_at) * .5)
        self._rate_at = now
        if self._tokens < 1:
            raise EventError("events_rate_limited", 429, max(1, math.ceil((1 - self._tokens) / .5)))
        self._tokens -= 1
        if self._active_requests >= 4:
            raise EventError("events_busy", 503, 2)
        self._active_requests += 1
        try:
            yield
        finally:
            self._active_requests -= 1

    async def body(self, request):
        parts = [part.strip().lower() for part in request.headers.get("content-type", "").split(";")]
        if parts[0] != "application/json":
            raise EventError("event_json_required", 415)
        for parameter in parts[1:]:
            if parameter.startswith("charset=") and parameter[8:].strip().strip('"') not in {"utf-8", "utf8"}:
                raise EventError("event_utf8_required", 415)
        lengths = request.headers.getlist("content-length")
        if lengths:
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
                raise EventError("event_content_length_invalid", 400)
            if int(lengths[0]) > BODY_LIMIT:
                raise EventError("event_body_limit", 413)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > BODY_LIMIT:
                        raise EventError("event_body_limit", 413)
                    body.extend(chunk)
        except TimeoutError:
            raise EventError("event_body_timeout", 408) from None
        except ClientDisconnect:
            raise EventError("event_client_disconnected", 400) from None
        return bytes(body)

    def router(self):
        router = APIRouter()

        @router.post("/api/events")
        async def submit_event(request: Request):
            started = time.monotonic()
            try:
                self.authorize(request)
                with self.request_slot():
                    document = decode_event(await self.body(request))
                    if document["kind"] == "command":
                        # EV-04 ainda não existe. Não aceitar um comando que jamais será executado nesta versão.
                        if not self.config.actions_enabled:
                            raise EventError("event_actions_disabled", 403)
                        raise EventError("event_actions_not_implemented", 503)
                    # Retry de um pedido já validado conserva os defaults originais,
                    # mesmo que o .env tenha mudado desde a primeira admissão.
                    result = await self.store.duplicate(document["event_id"], request_fingerprint(document))
                    if result is None:
                        submission = parse_event(document, self.config)
                        self.validate_delivery(submission.document)
                        result = await self.store.submit(submission)
                    logger.info("Evento %s: status=%s duplicado=%s duracao_ms=%d; sem reproducao",
                                result["job_id"], result["status"], result["duplicate"],
                                int((time.monotonic() - started) * 1000))
                    if self.config.log_content:
                        logger.info("Evento %s: conteudo recebido: %s", result["job_id"],
                                    log_text(compact(document), 4096))
                    return response(result, 200 if result["duplicate"] else 202)
            except EventError as error:
                logger.info("POST de evento recusado: %s; duracao_ms=%d", error.code,
                            int((time.monotonic() - started) * 1000))
                return response({"error": error.code}, error.status, error.retry_after)

        @router.get("/api/events/{job_id}")
        async def get_event(job_id: str, request: Request):
            try:
                self.authorize(request)
                with self.request_slot():
                    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
                        raise EventError("event_not_found", 404)
                    result = await self.store.get(job_id)
                    capability = self.capability()
                    result.update(delivery_available=capability["delivery_available"],
                                  delivery_reason=capability["delivery_reason"])
                    return response(result)
            except EventError as error:
                return response({"error": error.code}, error.status, error.retry_after)

        return router
