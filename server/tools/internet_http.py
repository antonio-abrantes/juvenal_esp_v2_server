"""Leitura HTTP limitada, DNS fixado por conexão e TLS verificado; sem credenciais/proxy."""

import http.client
import ipaddress
import json
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit


class QueryError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


def public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if address.version == 6:
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if any(address in ipaddress.ip_network(net) for net in ("64:ff9b::/96", "64:ff9b:1::/48")):
            return False
    return True


def checked_url(url):
    try:
        p = urlsplit(url)
        if (len(url) > 2048 or p.scheme not in {"http", "https"} or not p.hostname
                or p.username or p.password or p.fragment or "\\" in url
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
            raise ValueError()
        p.port
        return p
    except (ValueError, TypeError):
        raise QueryError("invalid_source_url", "O endereço da fonte não é válido.") from None


class _HTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _HTTPS(_HTTP):
    def connect(self):
        super().connect()
        try:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        except BaseException:
            self.sock.close()
            raise


class QueryHTTP:
    def __init__(self, deadline, cancelled):
        self.deadline, self.cancelled = deadline, cancelled

    def remaining(self):
        if self.cancelled.is_set() or time.monotonic() >= self.deadline:
            raise TimeoutError()
        return max(.01, self.deadline - time.monotonic())

    def get(self, url, *, params=None, limit=262144, html=False):
        if params:
            p = checked_url(url)
            url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(params), ""))
        for redirect in range(3):
            p = checked_url(url)
            port = p.port or (443 if p.scheme == "https" else 80)
            if port not in {80, 443}:
                raise QueryError("source_port_rejected", "A porta dessa fonte não é permitida.")
            self.remaining()
            addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(
                p.hostname, port, type=socket.SOCK_STREAM)))
            self.remaining()
            if not addresses or any(not public_address(address) for address in addresses):
                raise QueryError("source_address_rejected", "A fonte não é um endereço público permitido.")
            # Conectar ao IP já verificado evita nova resolução DNS/rebinding na conexão.
            conn = (_HTTPS if p.scheme == "https" else _HTTP)(
                p.hostname, port, addresses[0], min(5, self.remaining()))
            try:
                target = p.path or "/"
                if p.query:
                    target += "?" + p.query
                conn.request("GET", target, headers={"User-Agent": "Juvenal/1.0 (read-only)",
                    "Accept": "text/html,text/plain" if html else "application/json",
                    "Accept-Encoding": "identity", "Connection": "close"})
                if conn.sock:
                    conn.sock.settimeout(min(5, self.remaining()))
                response = conn.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    if redirect == 2 or not response.getheader("Location"):
                        raise QueryError("source_redirect_rejected", "A fonte redirecionou a consulta além do permitido.")
                    url = urljoin(url, response.getheader("Location"))
                    continue
                if response.status != 200:
                    raise QueryError("source_http_error", f"A fonte respondeu HTTP {response.status}; não obtive os dados.")
                kind = response.getheader("Content-Type", "").split(";")[0].strip().lower()
                allowed = {"text/html", "application/xhtml+xml", "text/plain"} if html else {
                    "application/json", "application/json;odata.metadata=minimal"}
                if kind not in allowed and not (not html and kind.endswith("+json")):
                    raise QueryError("source_content_rejected", "O formato devolvido pela fonte não é suportado.")
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise QueryError("source_encoding_rejected", "A fonte devolveu conteúdo comprimido não solicitado.")
                body = bytearray()
                while True:
                    if conn.sock:
                        conn.sock.settimeout(min(5, self.remaining()))
                    else:
                        self.remaining()
                    chunk = response.read1(min(8192, limit + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > limit:
                        raise QueryError("source_size_limit", "A resposta da fonte excedeu o limite de leitura.")
                self.remaining()
                return bytes(body), url
            finally:
                conn.close()
        raise QueryError("source_redirect_rejected", "Não consegui abrir a fonte.")

    def json(self, url, **kwargs):
        data, final_url = self.get(url, **kwargs)
        try:
            return json.loads(data), final_url
        except (ValueError, UnicodeError):
            raise QueryError("source_json_invalid", "A fonte devolveu dados inválidos.") from None


class PageText(HTMLParser):
    """Texto visível limitado; não interpreta scripts, formulários ou instruções."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.parts, self.size = 0, [], 0
        self.published = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}:
            self.skip += 1
        if tag == "meta":
            attrs = dict(attrs)
            if attrs.get("property", attrs.get("name")) in {"article:published_time", "datePublished"}:
                self.published = (attrs.get("content") or "")[:64] or None

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and self.size < 12000:
            text = " ".join(data.split())[:12000 - self.size]
            if text:
                self.parts.append(text)
                self.size += len(text)
