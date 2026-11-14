"""Provedores HTTP existentes, sem histórico global nem erro tratado como resposta."""

import asyncio
import json
import logging
import queue
import re
import threading
import time
from datetime import datetime

import requests

from config import settings
from providers import get_active_provider
from runtime import SingleWorker
from tools.registry import ArgumentError, ToolCall, decode_json

logger = logging.getLogger(__name__)
_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sábado", "domingo"]
_MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
          "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _resolve_placeholders(template):
    now = datetime.now()
    date = f"{now.day} de {_MESES[now.month]} de {now.year}"
    day, hour = _DIAS[now.weekday()], now.strftime("%H:%M")
    for key, value in {"DATA_HORA": f"{day}, {date}, {hour}", "DATA": date,
                       "HORA": hour, "DIA_SEMANA": day}.items():
        template = template.replace("{{" + key + "}}", value)
    return template


class LLMError(RuntimeError):
    """Código sanitizado; nunca inclui corpo/URL/credencial do provedor."""


class LLMClient:
    def __init__(self, system_prompt=None, prompt_path=None, max_tokens=300, temperature=0.7):
        self.provider = get_active_provider()
        self.worker = SingleWorker()
        self.max_tokens, self.temperature = max_tokens, temperature
        path = prompt_path or settings.prompt_path
        self._prompt_template = (path.read_text(encoding="utf-8-sig").strip()
                                 if path.exists() else system_prompt) or (
            "Você é Juvenal, um agente inteligente e assistente pessoal. "
            "Seja atento, prestativo e bem-humorado, com humor leve quando apropriado. "
            "Responda em português do Brasil, com frases naturais e concisas para voz, "
            "sem Markdown. Use de uma a três frases e no máximo 80 palavras; "
            "se pedirem textos muito longos, informe que suas diretrizes não permitem e ofereça um resumo. "
            "Interprete erros de transcrição pelo contexto quando a intenção for clara; "
            "se houver dúvida, peça um esclarecimento breve sem repetir a frase defeituosa. "
            "Não execute ações de intenção ambígua. Não invente fatos, identidades ou ações realizadas.")
        self._is_webhook = self.provider.model == "webhook"
        self._url = (self.provider.base_url if self._is_webhook else
                     self.provider.base_url.rstrip("/") + "/chat/completions")
        self._headers = {"Content-Type": "application/json"}
        if self._is_webhook:
            self._headers["X-API-Key"] = self.provider.api_key
        else:
            self._headers["Authorization"] = f"Bearer {self.provider.api_key}"
        logger.info("Provedor conversacional configurado; histórico gerido pelo núcleo")

    @property
    def busy(self):
        return self.worker.busy

    def _payload(self, messages, stream, raw=False):
        model = self.provider.model.lower()
        modern = any(model.startswith(p) or f"-{p}" in model
                     for p in ("gpt-4o", "gpt-5", "o1", "o3", "o4"))
        payload = {"model": self.provider.model, "messages": messages, "stream": stream,
                   "temperature": 0.0 if raw else self.temperature}
        payload["max_completion_tokens" if modern else "max_tokens"] = 10 if raw else self.max_tokens
        if model == "gpt-5.6-luna":
            payload["reasoning_effort"] = "none"
        return payload

    async def chat(self, user_message, history=(), timeout=None, *, messages=None,
                   tool_schemas=(), instructions="", reply=None):
        timeout = min(timeout or settings.llm_timeout, settings.llm_timeout)
        deadline = time.monotonic() + timeout
        tokens = queue.Queue(maxsize=128)
        queued_bytes = 0
        guard = threading.Lock()
        start_gate = threading.Event()
        stop = None

        def publish(token):
            nonlocal queued_bytes
            size = len(token.encode("utf-8"))
            with guard:
                if queued_bytes + size > settings.token_queue_bytes:
                    raise LLMError("token_queue_limit")
                queued_bytes += size
            try:
                tokens.put_nowait((token, size))
            except queue.Full:
                raise LLMError("token_queue_limit") from None

        def produce():
            start_gate.wait()
            conversation = [{"role": "system", "content": _resolve_placeholders(self._prompt_template)}]
            if instructions:
                conversation.append({"role": "system", "content": instructions})
            conversation.extend(messages if messages is not None else
                                [*history, {"role": "user", "content": user_message}])
            payload = ({"message": (instructions + "\n\nMensagem do usuário:\n" + user_message
                                    if instructions else user_message)} if self._is_webhook else
                       self._payload(conversation, True))
            if tool_schemas:
                if self._is_webhook:
                    raise LLMError("structured_adapter_unavailable")
                payload.update(tools=list(tool_schemas), tool_choice="auto", parallel_tool_calls=False)
            total = 0
            calls, content = {}, []
            finish_reason = None
            try:
                with requests.post(self._url, headers=self._headers, json=payload,
                                   stream=True, timeout=(min(5, timeout), timeout),
                                   allow_redirects=False) as response:
                    if not 200 <= response.status_code < 300:
                        raise LLMError(f"provider_http_{response.status_code}")
                    response.encoding = "utf-8"
                    if self._is_webhook:
                        body = bytearray()
                        for chunk in response.iter_content(4096):
                            if stop.is_set() or time.monotonic() >= deadline:
                                raise LLMError("provider_timeout")
                            body.extend(chunk)
                            if len(body) > settings.text_bytes * 8:
                                raise LLMError("provider_body_limit")
                        data = json.loads(body)
                        text = data.get("response") if isinstance(data, dict) else None
                        if not isinstance(text, str) or not text.strip():
                            raise LLMError("provider_empty_response")
                        if len(text.encode("utf-8")) > settings.text_bytes:
                            raise LLMError("response_limit")
                        publish(text.strip())
                        if reply is not None:
                            reply.update(content=text.strip(), tool_calls=[])
                        return
                    # Limitar linhas SSE; iter_lines() pode acumular uma linha infinita.
                    pending = bytearray()
                    completed = False
                    for chunk in response.iter_content(1):
                        if stop.is_set() or time.monotonic() >= deadline:
                            raise LLMError("provider_timeout")
                        pending.extend(chunk)
                        if len(pending) > settings.token_queue_bytes:
                            raise LLMError("provider_frame_limit")
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            pending = bytearray(rest)
                            line = line.rstrip(b"\r")
                            if not line.startswith(b"data:"):
                                continue
                            data = line[5:].strip()
                            if data == b"[DONE]":
                                completed = True
                                break
                            event = decode_json(data)
                            if not isinstance(event, dict):
                                raise LLMError("provider_invalid_response")
                            if "error" in event:
                                raise LLMError("provider_stream_error")
                            choices = event.get("choices", [])
                            if not choices:
                                continue
                            choice = choices[0]
                            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                                raise LLMError("provider_invalid_response")
                            reason = choice.get("finish_reason")
                            if reason in {"length", "content_filter", "function_call"}:
                                raise LLMError("provider_incomplete_response")
                            if reason in {"stop", "tool_calls"}:
                                finish_reason = reason
                                completed = True
                            delta = choice.get("delta", {})
                            if not isinstance(delta, dict):
                                raise LLMError("provider_invalid_response")
                            fragments = delta.get("tool_calls") or []
                            if not isinstance(fragments, list) or fragments and not tool_schemas:
                                raise LLMError("provider_unexpected_tools")
                            for fragment in fragments:
                                index = fragment.get("index")
                                if type(index) is not int or not 0 <= index < 4:
                                    raise LLMError("provider_tool_call_limit")
                                call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                if fragment.get("type") not in {None, "function"}:
                                    raise LLMError("provider_invalid_tool_call")
                                identifier = fragment.get("id")
                                if identifier:
                                    if (not isinstance(identifier, str)
                                            or call["id"] and call["id"] != identifier):
                                        raise LLMError("provider_invalid_tool_call")
                                    call["id"] = identifier
                                function = fragment.get("function") or {}
                                for part in ("name", "arguments"):
                                    value = function.get(part) or ""
                                    if not isinstance(value, str):
                                        raise LLMError("provider_invalid_tool_call")
                                    call[part] += value
                                if (len(call["id"]) > 128 or len(call["name"]) > 64
                                        or sum(len(item["arguments"].encode("utf-8"))
                                               for item in calls.values()) > 16384):
                                    raise LLMError("provider_tool_arguments_limit")
                            token = delta.get("content")
                            if token:
                                if not isinstance(token, str):
                                    raise LLMError("provider_invalid_response")
                                total += len(token.encode("utf-8"))
                                if total > settings.text_bytes:
                                    raise LLMError("response_limit")
                                content.append(token)
                                publish(token)
                        if completed:
                            break
                    if not completed:
                        raise LLMError("provider_interrupted_response")
                    parsed_calls = []
                    if calls:
                        if finish_reason != "tool_calls":
                            raise LLMError("provider_incomplete_tool_call")
                        names = {item["function"]["name"] for item in tool_schemas}
                        identifiers = set()
                        for index in sorted(calls):
                            call = calls[index]
                            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"])
                                    or call["id"] in identifiers or call["name"] not in names):
                                raise LLMError("provider_invalid_tool_call")
                            identifiers.add(call["id"])
                            try:
                                arguments = decode_json(call["arguments"])
                            except ArgumentError:
                                raise LLMError("provider_invalid_tool_arguments") from None
                            if not isinstance(arguments, dict):
                                raise LLMError("provider_invalid_tool_arguments")
                            parsed_calls.append(ToolCall(call["id"], call["name"], arguments))
                    elif finish_reason == "tool_calls":
                        raise LLMError("provider_empty_tool_call")
                    if reply is not None:
                        reply.update(content="".join(content), tool_calls=parsed_calls)
            except LLMError:
                raise
            except Exception:
                raise LLMError("provider_request_failed") from None

        future = self.worker.start(produce)
        stop = self.worker.cancel_event
        start_gate.set()
        response_bytes = 0
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise LLMError("provider_timeout")
                if future.done() and future.exception():
                    raise future.exception()
                try:
                    token, size = tokens.get_nowait()
                except queue.Empty:
                    if future.done():
                        future.result()
                        break
                    await asyncio.sleep(0.02)
                    continue
                with guard:
                    queued_bytes -= size
                response_bytes += size
                yield token
            if not response_bytes and not (reply is not None and reply.get("tool_calls")):
                raise LLMError("provider_empty_response")
        finally:
            stop.set()

    async def chat_raw(self, prompt):
        """Classificador isolado; não lê nem grava contexto conversacional."""
        deadline = time.monotonic() + 15
        def request():
            payload = ({"message": prompt} if self._is_webhook else self._payload(
                [{"role": "user", "content": prompt}], False, raw=True))
            try:
                with requests.post(self._url, headers=self._headers, json=payload,
                                   timeout=(5, 15), stream=True, allow_redirects=False) as response:
                    if not 200 <= response.status_code < 300:
                        raise LLMError("classifier_http_error")
                    body = bytearray()
                    for chunk in response.iter_content(1024):
                        if time.monotonic() >= deadline or self.worker.cancel_event.is_set():
                            raise LLMError("classifier_timeout")
                        body.extend(chunk)
                        if len(body) > 8192:
                            raise LLMError("classifier_response_limit")
                    data = json.loads(body)
                    text = (data.get("response") if self._is_webhook else
                            data["choices"][0]["message"]["content"])
                    return text if isinstance(text, str) else ""
            except Exception:
                raise LLMError("classifier_failed") from None
        return await self.worker.run(request, timeout=15)

    def cancel(self):
        self.worker.cancel()
