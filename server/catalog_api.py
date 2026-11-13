"""Administração autenticada do catálogo; não expõe executor de comandos."""

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from automation_catalog import ACTIONS, CatalogError
from config import settings, tool_settings
from tools.registry import ArgumentError, decode_json


def create_catalog_router(catalog, authenticated, get_tools):
    router = APIRouter()
    capacity = asyncio.Semaphore(4)

    def authorize(request, write=False):
        if not settings.panel_username:
            return JSONResponse({"error": "catalog_admin_credentials_required"}, status_code=503)
        if not authenticated(request.headers):
            return JSONResponse({"error": "catalog_unauthorized"}, status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Juvenal"'})
        if write:
            try:
                origin = urlsplit(request.headers.get("origin", ""))
                same_origin = (origin.scheme == request.url.scheme and
                               origin.netloc == request.headers.get("host") and
                               not origin.query and not origin.fragment and origin.path in {"", "/"})
            except ValueError:
                same_origin = False
            if (not same_origin or request.headers.get("x-juvenal-catalog") != "1"
                    or request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}):
                return JSONResponse({"error": "catalog_origin_rejected"}, status_code=403)
        return None

    async def payload(snapshot=None, error=None):
        tools = get_tools()
        capability = (await tools.capability() if tools else
                      {"available": False, "reason": "server_starting", "catalog_valid": False})
        return {"schema_version": 1, "revision": snapshot.revision if snapshot else error.revision,
                "automations": snapshot.automations if snapshot else [],
                "valid": error is None, "error": error.code if error else None,
                "actions": ACTIONS, "capability": capability,
                "limits": {"entries": tool_settings.catalog_entries,
                           "bytes": tool_settings.catalog_bytes}}

    @router.get("/api/automations")
    async def read_catalog(request: Request):
        denial = authorize(request)
        if denial is not None:
            return denial
        async with capacity:
            try:
                snapshot = await asyncio.to_thread(catalog.snapshot)
                result = await payload(snapshot=snapshot)
            except CatalogError as error:
                result = await payload(error=error)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.put("/api/automations")
    async def replace_catalog(request: Request):
        denial = authorize(request, write=True)
        if denial is not None:
            return denial
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse({"error": "catalog_json_required"}, status_code=415)
        async with capacity:
            body = bytearray()
            try:
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > tool_settings.catalog_bytes * 2:
                        return JSONResponse({"error": "catalog_request_limit"}, status_code=413)
                document = decode_json(body)
                if (not isinstance(document, dict) or set(document) != {"revision", "automations"}
                        or not isinstance(document["revision"], str)
                        or len(document["revision"]) != 64):
                    raise ArgumentError("catalog_request_invalid")
                snapshot = await asyncio.to_thread(
                    catalog.save, document["revision"], document["automations"])
                return JSONResponse(await payload(snapshot=snapshot),
                                    headers={"Cache-Control": "no-store"})
            except CatalogError as error:
                status = 409 if error.code == "catalog_revision_conflict" else 422
                return JSONResponse({"error": error.code}, status_code=status)
            except ArgumentError:
                return JSONResponse({"error": "catalog_request_invalid"}, status_code=422)
            except Exception:
                return JSONResponse({"error": "catalog_operation_failed"}, status_code=500)

    return router
