"""Composição de capacidades: Home Assistant e consultas independentes."""

from dataclasses import replace

from tools.home_assistant import AutomationTools
from tools.internet import InternetTools
from tools.registry import ToolRegistry


class ToolsCoordinator:
    def __init__(self, catalog, provider):
        self.hass = AutomationTools(catalog, provider)
        self.internet = InternetTools(provider, self.hass.trusted_identity)
        self.registry = ToolRegistry()
        for definition in self.hass.registry.definitions.values():
            self.registry.register(replace(definition, permission=lambda session, original=definition.permission:
                original(session) and not getattr(session, "internet_evidence", False)))
        self.internet.register(self.registry)

    @property
    def busy(self):
        return self.hass.busy or self.internet.busy

    def cancel(self):
        self.hass.cancel()
        self.internet.cancel()

    def confirmation_delivered(self, session):
        self.hass.confirmation_delivered(session)

    async def consume_confirmation(self, text, session):
        return await self.hass.consume_confirmation(text, session)

    async def catalog_capability(self):
        return await self.hass.capability()

    async def capability(self):
        hass = await self.hass.capability()
        internet = self.internet.capabilities()
        return {**hass, "available": hass["available"] or internet["available"],
                "reason": None if hass["available"] or internet["available"] else hass["reason"],
                "home_assistant": hass, "internet": internet}

    async def offer(self, session):
        schemas, instructions, revision = await self.hass.offer(session)
        instructions = instructions.replace("Ferramentas indisponíveis:", "Ferramenta Home Assistant indisponível:")
        names, policy = self.internet.offer(session)
        if getattr(session, "internet_evidence", False):
            schemas = []  # Evidência externa não autoriza uma ação física posterior neste turno.
            policy += " Neste turno já houve consulta externa: responda aos dados, sem executar ações residenciais."
        schemas += [self.registry.definitions[name].schema() for name in names]
        return schemas, instructions + "\n" + policy, revision
