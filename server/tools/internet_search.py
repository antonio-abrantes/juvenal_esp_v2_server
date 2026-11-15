"""Busca nativa opcional: biblioteca DDGS, sem daemon, container ou API paga."""

import importlib.util
import logging

from tools.internet_http import QueryError


def dependency_available():
    return importlib.util.find_spec("ddgs") is not None


def search(http, args, max_results):
    try:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
    except (ImportError, OSError):
        raise QueryError("search_dependency_missing",
            "A busca precisa da dependência Python de requirements-internet.txt; clima e câmbio continuam disponíveis.") from None

    # O log próprio é opt-in e sanitizado. Bibliotecas não devem registrar a consulta/URL crua.
    logging.getLogger("ddgs").setLevel(logging.CRITICAL)
    logging.getLogger("primp").setLevel(logging.CRITICAL)
    remaining = http.remaining()
    client = DDGS(timeout=max(1, int(min(5, remaining))), verify=True)
    period = args.get("period", "any")
    # News documenta d/w/m. Para ano, usar a busca textual com filtro y,
    # sem fabricar datas de publicação ou afirmar que é um feed de notícias.
    method = client.news if args.get("category") == "news" and period != "year" else client.text
    try:
        rows = method(args["query"], region="br-pt", safesearch="moderate",
            timelimit={"day": "d", "month": "m", "year": "y"}.get(period),
            max_results=max_results, backend="duckduckgo")
    except RatelimitException:
        raise QueryError("search_rate_limited", "O buscador limitou as consultas. Não consegui confirmar a informação agora.") from None
    except TimeoutException:
        raise QueryError("search_timeout", "O buscador excedeu o prazo da consulta.") from None
    except DDGSException:
        raise QueryError("search_unavailable", "Não consegui obter resultados do buscador agora.") from None
    http.remaining()
    return {"results": [{"url": row.get("url") or row.get("href") or "",
        "title": row.get("title", ""), "content": row.get("body", ""),
        "publishedDate": row.get("date")} for row in rows[:max_results] if isinstance(row, dict)]}
