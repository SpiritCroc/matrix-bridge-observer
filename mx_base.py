import asyncio
import logging
from nio import AsyncClient
from threading import Lock

class BaseMatrixUser:
    def __init__(self, logname, config):
        self.log = logging.getLogger(logname)
        self.mx_id = config["mx_id"]
        self.homeserver = config["homeserver"]
        self.token = config["token"]
        self.loop = asyncio.new_event_loop()
        self.client_lock = Lock()

    async def async_with_client(self, func):
        client = None
        try:
            client = AsyncClient(self.homeserver, self.mx_id)
            client.access_token = self.token
            client.user_id = self.mx_id
            return await func(client)
        except:
            self.log.exception("Failed to execute with matrix client")
        finally:
            if client != None:
                try:
                    await client.close()
                except:
                    pass

    def with_client(self, func):
        with self.client_lock:
            return self.loop.run_until_complete(self.async_with_client(func))
