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

class ReconnectableSidecar:

    def __init__(self, url, category, keep_period=1):
        # keep_period is the period at which connection is verified for open-ness
        self.url = url
        self.category = category
        self.keep_period = keep_period
        # caller MUST run keep_connected()
        self.connection = None
        self.counter = 0
        self.backlog = []
        logger.info(f"reconnectable sidecar to {url} ")

    def __repr__(self):
        size = f"no backlog" if not self.backlog else f"backlog={len(self.backlog)}"
        conn = f"no connection" if not self.connection else f"connection={self.connection.state}"
        return f"ReconnectableSidecar({self.url}) - {size} - {conn}"


    async def flush_backlog(self):
        # do not mess with the iteration subject
        for infos in self.backlog[:]:
            if await self.emit_infos(infos):
                self.backlog.remove(infos)


    # info singular: create a list with that one info
    async def emit_info(self, info):
        return await self.emit_infos([info])


    async def emit_infos(self, infos):
        # logger.debug(f"{self}: emit_infos is sending {infos}")
        if not self.connection:
            logger.warning(f"[no conn.] backlog ->  {infos}"
                           f" (with {len(self.backlog)} others)")
            self.backlog.append(infos)
            return False

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
        except websockets.exceptions.ConnectionClosedError as exc:
            logger.warning(f"[conn. closed] message {infos} goes into backlog"
                           f" (with {len(self.backlog)} others)")
            self.backlog.append(infos)
            self.connection = None
            return False
        except Exception as exc:
            # xxx to review
            logger.exception(f"connection.send failed: {type(exc)}: {exc}")
            self.connection = None
            return False
        finally:
            pass
            # logger.debug(f"<<< emit_infos is leaving connection.send() with {payload}")


    async def keep_connected(self):
        """
        A continuous loop that keeps the connection open
        """
        while True:
            if self.connection and self.connection.state == websockets.protocol.State.OPEN:
                logger.debug(f"in keep_connected, connection is open")
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
