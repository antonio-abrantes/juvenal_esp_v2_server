"""Executa pedidos explícitos do webhook pelo mesmo adaptador Home Assistant."""

import asyncio
import json
import logging

from events_schema import EventError
from runtime import CapacityBusy

logger = logging.getLogger(__name__)
UNCERTAIN = ("unknown", "event_action_interrupted", "")


class EventActions:
    def __init__(self, events, admit, return_idle):
        self.events = events
        self.store = events.store
        self.admit, self.return_idle = admit, return_idle
        self.engine = self.worker = None

    def capability(self):
        if not self.events.config.actions_enabled:
            return False, "event_actions_disabled"
        if self.worker is None or self.worker.done():
            return False, "event_actions_unavailable"
        available, reason = self.engine.tools.hass.event_command_availability()
        return available, reason or None

    def start(self, engine):
        self.engine = engine
        config = self.events.config
        if config.enabled and config.actions_enabled and not config.error and config.api_key:
            self.worker = asyncio.create_task(self.run(), name="event-actions")

    async def close(self):
        if self.worker is not None:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)

    async def run(self):
        while not self.events.closing:
            if (not self.events.unavailable() and self.engine.active is None
                    and not self.engine.workers_busy):
                try:
                    job = await self.store.next_command()
                    if job is not None:
                        await self.execute(job)
                except (CapacityBusy, EventError):
                    # Corrida pela reserva ou banco temporariamente indisponível.
                    pass
                except Exception as error:
                    logger.warning("Despacho de ação indisponível (%s)", type(error).__name__)
            await asyncio.sleep(1)

    async def execute(self, job):
        job_id = job["job_id"]
        session = await self.admit("event_action", "event:external")
        outcome = None
        try:
            if not await self.store.begin_action(job_id):
                return
            command = json.loads(job["payload_json"])["action"]["command"]
            result = await self.engine.tools.hass.execute_event_command(command, session, job["expires_at"])
            if result.code == "hass_text_accepted":
                outcome = "accepted_external", result.code, ""
            else:
                status = "not_executed" if result.status == "denied" else result.status
                speech = "" if session.hass_delivery == "external" else result.message
                if result.code == "hass_action_unconfirmed":
                    speech = "O Home Assistant não confirmou a execução da ação."
                outcome = status, result.code, speech
        except asyncio.CancelledError:
            outcome = UNCERTAIN
            raise
        except EventError:
            raise
        except Exception as error:
            logger.warning("Evento %s: resultado da ação indisponível (%s)", job_id, type(error).__name__)
            outcome = UNCERTAIN
        finally:
            self.engine.release(session)
            try:
                await self.return_idle()
            finally:
                if outcome is not None:
                    await self.record(job_id, outcome)

    async def record(self, job_id, outcome):
        while True:
            try:
                if await self.store.finish_action(job_id, *outcome):
                    logger.info("Evento %s: ação=%s motivo=%s", job_id, outcome[0], outcome[1])
                return
            except EventError:
                if self.events.closing:
                    logger.warning("Evento %s: resultado será recuperado no próximo início", job_id)
                    return
                await asyncio.sleep(1)
