# pylint: disable=logging-fstring-interpolation
# pylint: disable=broad-exception-caught
# pylint: disable=missing-docstring

# pylint: disable=fixme

import json
import asyncio
import ssl

import websockets.uri
import websockets.protocol

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
        self.connection = None
        self.counter = 0
        self.backlog = []
        logger.info(f"reconnectable sidecar to {url} ")


    async def flush_backlog(self):
        for infos in self.backlog[:]:
            if await self.emit_info(infos):
                self.backlog.remove(infos)


    async def emit_info(self, info):
        # create a list with that one info
        return await self.emit_infos([info])


    async def emit_infos(self, infos):
        if not self.connection:
            logger.warning(f"[no conn.] message {infos} goes into backlog"
                           f" (with {len(self.backlog)} others)")
            self.backlog.append(infos)
            return False
        logger.debug(f"Sending {infos}")
        # xxx use Payload
        payload = dict(category=self.category, action='info', message=infos)
        try:
            await self.connection.send(json.dumps(payload))
            self.counter += 1
            return True
        except ConnectionRefusedError:
            logger.warning(f"[conn. refused] message {infos} goes into backlog"
                           f" (with {len(self.backlog)} others)")
            self.backlog.append(infos)
            return False
        except Exception as exc:
            # xxx to review
            logger.exception(f"send failed: {exc}")
            self.connection = None
            return False


    async def keep_connected(self):
        """
        A continuous loop that keeps the connection open
        """
        while True:
            logger.debug(f"in keep_connected, proto={self.connection}")
            if self.connection and self.connection.state == websockets.protocol.State.OPEN:
                logger.debug(f"connection is open")
                if self.backlog:
                    logger.info(f"flushing backlog of {len(self.backlog)} messages")
                    await self.flush_backlog()
                    logger.info(f"after flush, backlog now has {len(self.backlog)} messages")
            else:
                # xxx should we close() our client ?
                self.connection = None
                # see if we need ssl
                secure = websockets.uri.parse_uri(self.url).secure
                kwds = {}
                if secure:
                    kwds.update(dict(ssl=ssl.SSLContext()))
                try:
                    logger.info(f"(re)-connecting to {self.url} ...")
                    self.connection = await SidecarAsyncClient(self.url, **kwds)
                    logger.debug("connected !")
                except ConnectionRefusedError:
                    logger.warning(f"Could not connect to {self.url} at this time")
                except Exception as exc:
                    logger.exception(
                        f"Could not connect to {self.url} at this time - uncaught {exc}")
            await asyncio.sleep(self.keep_period)


    async def watch_back_channel(self, category, callback):
        while True:
            if not self.connection:
                logger.debug(f"backing off for {self.keep_period}s")
                await asyncio.sleep(self.keep_period)
                continue
            try:
                incoming = await self.connection.recv()
                umbrella = json.loads(incoming)
                logger.info(f"tmp - got incoming {umbrella['category']} x {umbrella['action']}")
                if (umbrella['category'] == category and
                        umbrella['action'] == 'request'):
                    callback(umbrella)
            except Exception as exc:
                ### to review
                logger.exception(f"recv failed .. fix me {exc}")
                self.connection = None
