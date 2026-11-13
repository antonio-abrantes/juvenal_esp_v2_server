"""Trabalho síncrono continua ocupado após cancelar quem aguarda seu resultado."""

import asyncio
import threading
from functools import partial


class CapacityBusy(RuntimeError):
    pass


class SingleWorker:
    def __init__(self):
        self._future = None
        self.cancel_event = threading.Event()

    @property
    def busy(self):
        return self._future is not None and not self._future.done()

    def start(self, function, *args):
        if self.busy:
            raise CapacityBusy("worker_busy")
        self.cancel_event = threading.Event()
        self._future = asyncio.get_running_loop().run_in_executor(
            None, partial(function, *args))
        # Consumir exceção mesmo se o chamador já tiver expirado/cancelado.
        self._future.add_done_callback(
            lambda f: f.exception() if not f.cancelled() else None)
        return self._future

    async def run(self, function, *args, timeout):
        future = self.start(function, *args)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except BaseException:
            self.cancel_event.set()
            raise

    def cancel(self):
        self.cancel_event.set()
