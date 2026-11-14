"""Dois destinos HA, uma tentativa HTTP, sem fallback ou áudio duplicado."""

import asyncio
import json
import logging
import time
from uuid import uuid4

import requests

from automation_catalog import ACTIONS, CatalogError
from config import hass_settings, settings
from diagnostics import log_text
from runtime import SingleWorker
from tools.registry import ArgumentError, ToolResult, decode_json

logger = logging.getLogger(__name__)


COMMAND_PROMPT = """Responda em português do Brasil.

NUNCA questione, explique ou peça confirmação.

SEMPRE execute diretamente o comando solicitado. NUNCA faça perguntas.

Se a entidade já estiver no estado solicitado, informe o estado atual de forma direta.

Caso contrário, informe apenas o que foi feito, em uma frase curta e natural.

Se a execução falhar, informe o erro sem afirmar que executou o comando.

Exemplos:
- Luz da suíte master ligada.
- Luz da cozinha já estava desligada.
- Portão já estava aberto.

Mensagem do usuário:
{command} : EXECUTE"""


def conversation_result(body, request_id, http_status):
    """Fala é autoritativa; HTTP 200 sozinho não significa ação executada."""
    metadata = {"request_id": request_id, "http_status": http_status}
    try:
        document = decode_json(body)
        response = document["response"]
        kind = response["response_type"]
        speech = response["speech"]["plain"]["speech"]
        data = response.get("data", {})
        if (kind not in {"action_done", "query_answer", "error"}
                or not isinstance(speech, str) or not speech.strip()
                or len(speech.encode("utf-8")) > settings.text_bytes
                or any(ord(c) < 32 and c not in "\r\n\t" for c in speech)
                or not isinstance(data, dict)):
            raise ValueError()
        metadata["response_type"] = kind
        if kind == "error":
            return ToolResult("failed", "hass_reported_error", speech, metadata)
        success, failed = data.get("success", []), data.get("failed", [])
        if not isinstance(success, list) or not isinstance(failed, list):
            raise ValueError()
        metadata.update(success_count=len(success), failed_count=len(failed))
        if failed:
            return ToolResult("failed", "hass_partial_failure", speech, metadata)
        if kind == "action_done" and not success:
            return ToolResult("unknown", "hass_action_unconfirmed", speech, metadata)
        return ToolResult("success", "hass_" + kind, speech, metadata)
    except (ArgumentError, KeyError, TypeError, ValueError):
        return ToolResult("unknown", "hass_invalid_response",
            "Não foi possível validar a resposta do Home Assistant. Não vou repetir a ação.",
            metadata)


class HomeAssistantTransport:
    def __init__(self):
        self.worker = SingleWorker()

    @property
    def busy(self):
        return self.worker.busy

    def cancel(self):
        self.worker.cancel()

    async def execute(self, prepare, session):
        started = time.monotonic()
        timeout = min(hass_settings.timeout, session.remaining)
        deadline = time.monotonic() + timeout
        attempt = {"sent": False}
        request_id = uuid4().hex
        mode = hass_settings.mode

        def result(status, code, message, **metadata):
            return ToolResult(status, code, message, {"request_id": request_id, **metadata})

        def uncertain(code):
            return result("unknown" if attempt["sent"] else "failed", code,
                "Não foi possível confirmar o resultado no Home Assistant. Não vou repetir a ação.")

        def request():
            try:
                available, _ = hass_settings.availability()
                if not available:
                    return result("denied", "hass_unavailable",
                                  "O módulo Home Assistant está desativado ou sem configuração.")
                entry, arguments = prepare()
                session.check()
                command = (arguments["command"] if entry is None else
                           ACTIONS[entry["action"]]["verb"] + " " + arguments["entity_id"])
                if entry is not None and "brightness_pct" in arguments:
                    command += "; brilho em " + str(arguments["brightness_pct"]) + " por cento"
                if entry is not None and "transition" in arguments:
                    command += "; transição em " + str(arguments["transition"]) + " segundos"
                headers = {"Content-Type": "application/json"}
                if mode == "api":
                    url = hass_settings.conversation_url
                    headers["Authorization"] = "Bearer " + hass_settings.api_token
                    payload = {"agent_id": hass_settings.agent_id, "language": "pt-BR",
                               "text": COMMAND_PROMPT.format(command=command)}
                else:
                    url = hass_settings.text_endpoint
                    payload = {hass_settings.text_field: command}
                    if hass_settings.text_key:
                        headers[hass_settings.text_auth_header] = (
                            hass_settings.text_auth_prefix + hass_settings.text_key)
                # Revalidar habilitação, identidade e política opcional no envio.
                prepare()
                session.check()
                if self.worker.cancel_event.is_set() or time.monotonic() >= deadline:
                    return result("denied", "action_cancelled", "A ação foi cancelada antes do envio.")
                # Política local do turno, nunca extraída de conteúdo do LLM/receptor.
                session.hass_delivery = "external" if mode == "text_endpoint" else "api"
                logger.info("Turno %s: HA POST inicio request=%s modo=%s timeout_s=%.1f",
                            session.turn_id, request_id, mode, timeout)
                if hass_settings.log_content:
                    logger.info("Turno %s: HA POST payload request=%s: %s",
                                session.turn_id, request_id,
                                log_text(json.dumps(payload, ensure_ascii=False)))
                attempt["sent"] = True
                with requests.post(url, json=payload, headers=headers, stream=True,
                                   allow_redirects=False,
                                   timeout=(min(5, timeout), timeout)) as response:
                    status = response.status_code
                    session.hass_http_status = status
                    logger.info("Turno %s: HA POST resposta request=%s http=%s duracao_ms=%d",
                                session.turn_id, request_id, status,
                                int((time.monotonic() - started) * 1000))
                    if not 200 <= status < 300:
                        if hass_settings.log_content:
                            preview = bytearray()
                            try:
                                for chunk in response.iter_content(1024):
                                    if self.worker.cancel_event.is_set() or time.monotonic() >= deadline:
                                        break
                                    preview.extend(chunk[:4097 - len(preview)])
                                    if len(preview) > 4096:
                                        break
                                logger.warning("Turno %s: HA POST corpo de erro request=%s: %s",
                                    session.turn_id, request_id,
                                    log_text(bytes(preview).decode("utf-8", errors="replace")))
                            except Exception:
                                logger.warning("Turno %s: HA POST corpo de erro indisponivel request=%s",
                                               session.turn_id, request_id)
                        return result("unknown", "hass_http_error",
                            "O Home Assistant retornou um erro HTTP. A execução não foi confirmada.",
                            http_status=status)
                    if mode == "text_endpoint":
                        # Corpo não é fala do Juvenal. Aceite não comprova execução nem áudio remoto.
                        session.hass_external_accepted = True
                        return result("unknown", "hass_text_accepted",
                            "O endpoint aceitou o comando. Execução e fala ficam a cargo do receptor.",
                            http_status=status)
                    body = bytearray()
                    for chunk in response.iter_content(1024):
                        if self.worker.cancel_event.is_set() or time.monotonic() >= deadline:
                            return uncertain("hass_timeout")
                        body.extend(chunk)
                        if len(body) > 65536:
                            return uncertain("hass_response_limit")
                    if hass_settings.log_content:
                        logger.info("Turno %s: HA POST corpo request=%s: %s",
                                    session.turn_id, request_id,
                                    log_text(bytes(body).decode("utf-8", errors="replace")))
                    return conversation_result(body, request_id, status)
            except (CatalogError, ArgumentError):
                return result("denied", "catalog_changed_or_invalid",
                              "A configuração ou as permissões mudaram; o comando não foi enviado.")
            except asyncio.CancelledError:
                return result("denied", "action_cancelled", "A ação foi cancelada antes do envio.")
            except Exception as error:
                # Não registrar exception/URL/headers/corpo: podem conter credenciais.
                category = ("tls" if isinstance(error, requests.exceptions.SSLError)
                            else "timeout" if isinstance(error, requests.exceptions.Timeout)
                            else "conexao" if isinstance(error, requests.exceptions.ConnectionError)
                            else "transporte")
                logger.warning("Turno %s: HA POST falha request=%s categoria=%s enviado=%s",
                               session.turn_id, request_id, category, attempt["sent"])
                return uncertain("hass_request_failed")

        try:
            outcome = await self.worker.run(request, timeout=timeout)
        except TimeoutError:
            outcome = uncertain("hass_timeout")
        except asyncio.CancelledError:
            logger.warning("Turno %s: HA POST espera cancelada request=%s enviado=%s",
                           session.turn_id, request_id, attempt["sent"])
            raise
        log = logger.info if outcome.status == "success" or outcome.code == "hass_text_accepted" else logger.warning
        log("Turno %s: HA POST resultado request=%s modo=%s enviado=%s estado=%s motivo=%s http=%s duracao_ms=%d",
            session.turn_id, request_id, mode, attempt["sent"], outcome.status, outcome.code,
            session.hass_http_status if session.hass_http_status is not None else "none",
            int((time.monotonic() - started) * 1000))
        return outcome
