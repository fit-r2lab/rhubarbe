import websockets
import json

from r2lab import SidecarAsyncClient

from rhubarbe.logger import monitor_logger as logger

class ReconnectableSidecar:

    def __init__(self, url, category):
        self.url = url
        self.category = category
        #
        self.proto = None
        self.counter = 0

    async def _ensure_connected(self):
        """
        return True if connection is OK and
        False if there's nothing we can do at this point
        """
        if not self.proto:
            # see if we need ssl
            secure, *_ = websockets.uri.parse_uri(self.url)
            kwds = {}
            if secure:
                import ssl
                kwds.update(dict(ssl=ssl.SSLContext()))
            try:
                self.proto = await SidecarAsyncClient(self.url, **kwds)
            except:
                logger.exception(f"Could not connect to {self.url} at this time")
                return False
        # xxx probably need more care
        # also need to check the connection is open
        return True

    async def emit_info(self, info):
        # create a list with that one info
        return await self.emit_infos([info])

    async def emit_infos(self, infos):
        self.counter += 1
        print(f"Sending {infos}")
        clear = await self._ensure_connected()
        if not clear:
            logger.warning(f"dropping message {infos}")
        else:
            # xxx use payload
            payload = dict(category=self.category, action='info', message=infos)
            await self.proto.send(json.dumps(payload))


    def get_counter(self):
        return self.counter
