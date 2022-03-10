# pylint: disable=c0111

import websockets
import json
import asyncio

from r2lab import SidecarAsyncClient

from rhubarbe.logger import monitor_logger as logger

#import logging
#logger.setLevel(logging.DEBUG)

class ReconnectableSidecar:

    def __init__(self, url, category, keep_period=1):
        # keep_period is the frequency where connection is verified for open-ness
        self.url = url
        self.category = category
        self.keep_period = keep_period
        # caller MUST run keep_connected()
        self.proto = None
        self.counter = 0
        logger.info(f"reconnectable sidecar to {url} ")


    async def emit_info(self, info):
        # create a list with that one info
        return await self.emit_infos([info])


    async def emit_infos(self, infos):
        if not self.proto:
            logger.warning(f"dropping message {infos}")
            return False
        logger.debug(f"Sending {infos}")
        # xxx use Payload
        payload = dict(category=self.category, action='info', message=infos)
        # xxx try/except here
        try:
            await self.proto.send(json.dumps(payload))
            self.counter += 1
        except ConnectionRefusedError:
            logger.warning(f"Could not send {self.category} - dropped")
        except Exception as exc:
            # xxx to review
            logger.exception("send failed")
            self.proto = None
            return False
        return True


    async def keep_connected(self):
        """
        A continuous loop that keeps the connection open
        """
        while True:
            logger.debug(f"in keep_connected, proto={self.proto}")
            if self.proto and self.proto.open:
                pass
            else:
                # xxx should we close() our client ?
                self.proto = None
                # see if we need ssl
                secure = websockets.uri.parse_uri(self.url).secure
                kwds = {}
                if secure:
                    import ssl
                    kwds.update(dict(ssl=ssl.SSLContext()))
                try:
                    logger.info(f"(re)-connecting to {self.url} ...")
                    self.proto = await SidecarAsyncClient(self.url, **kwds)
                    logger.debug("connected !")
                except ConnectionRefusedError:
                    logger.warning(f"Could not connect to {self.url} at this time")
                except:
                    logger.exception(f"Could not connect to {self.url} at this time")
            await asyncio.sleep(self.keep_period)


    async def watch_back_channel(self, category, callback):
        while True:
            if not self.proto:
                logger.debug(f"backing off for {self.keep_period}s")
                await asyncio.sleep(self.keep_period)
                continue
            try:
                incoming = await self.proto.recv()
                umbrella = json.loads(incoming)
                logger.info(f"tmp - got incoming {umbrella['category']} x {umbrella['action']}")
                if (umbrella['category'] == category and
                        umbrella['action'] == 'request'):
                    callback(umbrella)
            except Exception as exc:
                ### to review
                logger.exception("recv failed .. fix me")
                self.proto = None
