"""Um único destino por resposta; endpoint textual não carrega motores de voz."""

import asyncio
import logging
import time
from dataclasses import dataclass

import requests

from config import settings
from diagnostics import log_text
from runtime import SingleWorker

logger = logging.getLogger(__name__)


def _endpoint_error_code(error):
    """Classificar sem registrar str(error), que pode conter URL ou credenciais."""
    categories = (
        (requests.exceptions.SSLError, "endpoint_tls_failed_unknown"),
        (requests.exceptions.ProxyError, "endpoint_proxy_failed_unknown"),
        (requests.exceptions.ConnectTimeout, "endpoint_connect_timeout_unknown"),
        (requests.exceptions.ReadTimeout, "endpoint_read_timeout_unknown"),
        (requests.exceptions.ConnectionError, "endpoint_connection_failed_unknown"),
        (requests.exceptions.Timeout, "endpoint_timeout_unknown"),
    )
    for exception_type, code in categories:
        if isinstance(error, exception_type):
            return code
    return "endpoint_transport_unknown"


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    mode: str
    error: str = ""
    http_status: int | None = None

    @property
    def ok(self):
        return self.status in {"delivered", "accepted"}


class ResponseOutput:
    def __init__(self):
        self.worker = SingleWorker()
        self.engine = None
        self.local_error = "local_output_unavailable"

    @property
    def busy(self):
        return self.worker.busy

    def mode_for(self, session):
        if settings.legacy_endpoint and session.origin == "esp32":
            return "endpoint"
        return settings.output_mode

    def _log_endpoint_response(self, response, attempt):
        if not settings.endpoint_log_response:
            return
        body = bytearray()
        limit = 4096
        try:
            for chunk in response.iter_content(chunk_size=512):
                if time.monotonic() >= attempt["deadline"]:
                    raise TimeoutError()
                body.extend(chunk[:limit + 1 - len(body)])
                if len(body) > limit:
                    break
            truncated = len(body) > limit
            # O retorno não entra no histórico, agente, tools ou TTS.
            preview = bytes(body[:limit]).decode("utf-8", errors="replace")
            logger.info("Turno %s: retorno HTTP do endpoint (http=%s%s): %s",
                        attempt["turn_id"], response.status_code,
                        ", limitado a 4096 bytes" if truncated else "",
                        log_text(preview))
        except Exception:
            # Falha ao registrar o corpo não transforma um aceite HTTP em falha de entrega.
            logger.warning("Turno %s: corpo do retorno HTTP indisponível/incompleto (http=%s)",
                           attempt["turn_id"], response.status_code)

    def _endpoint(self, text, attempt):
        headers = {}
        if settings.endpoint_key:
            headers[settings.endpoint_auth_header] = settings.endpoint_auth_prefix + settings.endpoint_key
        payload = {"json": {"texto": text}} if settings.endpoint_format == "json" else {
            "data": text.encode("utf-8")}
        if settings.endpoint_format == "text":
            headers["Content-Type"] = "text/plain; charset=utf-8"
        try:
            # Sem redirects, retentativas ou fallback. Corpo opcional só para diagnóstico.
            with requests.post(settings.endpoint_url, headers=headers, **payload,
                               stream=True, allow_redirects=False,
                               timeout=(min(5, settings.endpoint_timeout), settings.endpoint_timeout)) as response:
                if response.status_code not in settings.endpoint_statuses:
                    result = DeliveryResult("failed", "endpoint", "endpoint_http_status", response.status_code)
                else:
                    result = DeliveryResult("accepted", "endpoint", http_status=response.status_code)
                attempt["result"] = result
                self._log_endpoint_response(response, attempt)
                return result
        except requests.RequestException as error:
            return DeliveryResult("failed", "endpoint", _endpoint_error_code(error))

    async def deliver_response(self, text, session):
        mode = self.mode_for(session)
        session.check()
        if session.hass_delivery == "external":
            # O POST já ocorreu na ferramenta. Nunca enviar de novo nem produzir
            # áudio local/ESP, inclusive se o resultado do receptor for incerto.
            return DeliveryResult(
                "accepted" if session.hass_external_accepted else "failed",
                "hass_text_endpoint",
                "" if session.hass_external_accepted else "hass_external_unconfirmed",
                session.hass_http_status)
        if session.hass_delivery == "api" and session.esp_link is not None:
            mode = "esp32"
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > settings.text_bytes:
            return DeliveryResult("failed", mode, "invalid_response_text")
        attempt = {}
        try:
            if mode == "esp32":
                if session.esp_link is None or self.engine is None:
                    return DeliveryResult("failed", mode, "esp32_playback_unavailable")
                await session.esp_link.play_text(text, self.engine, self.worker)
                result = DeliveryResult("delivered", mode)
            elif mode == "endpoint":
                timeout = min(settings.endpoint_timeout, session.remaining)
                attempt = {"turn_id": session.turn_id, "deadline": time.monotonic() + timeout}
                result = await self.worker.run(self._endpoint, text, attempt, timeout=timeout)
            else:
                if self.engine is None:
                    return DeliveryResult("failed", mode, self.local_error)
                # Evento criado antes do worker; cancel() também sinaliza esse evento.
                import threading
                stop = threading.Event()
                self._playback_stop = stop
                try:
                    await self.worker.run(self.engine.speak_sync, text, stop,
                                          timeout=min(settings.delivery_timeout, session.remaining))
                finally:
                    stop.set()
                result = DeliveryResult("delivered", mode)
            session.check()
            return result
        except asyncio.TimeoutError:
            if mode == "endpoint" and attempt.get("result") is not None:
                logger.warning("Turno %s: prazo do diagnóstico HTTP atingido; status já recebido preservado",
                               session.turn_id)
                return attempt["result"]
            return DeliveryResult("failed", mode, "delivery_timeout_unknown")
        except asyncio.CancelledError:
            self.cancel()
            raise
        except Exception:
            return DeliveryResult("failed", mode, "delivery_failed")

    def cancel(self):
        self.worker.cancel()
        stop = getattr(self, "_playback_stop", None)
        if stop:
            stop.set()
