# pylint: disable=logging-fstring-interpolation
# pylint: disable=broad-exception-caught
# pylint: disable=missing-docstring

# pylint: disable=fixme

import json
import time
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
        # when the last send succeeded; starts at creation time as a grace period
        self.last_send_ok = time.time()
        # what remains to be sent: id -> info, merged; see emit_infos()
        self.backlog = {}
        # True while flush_backlog() is sending
        self._flushing = False
        logger.info(f"reconnectable sidecar to {url} ")

    def __repr__(self):
        size = f"no backlog" if not self.backlog else f"backlog={len(self.backlog)} ids"
        conn = f"no connection" if not self.connection else f"connection={self.connection.state}"
        age = f"last send ok {time.time() - self.last_send_ok:.0f}s ago"
        return f"ReconnectableSidecar({self.url}) - {size} - {conn} - {age}"


    def _buffer(self, infos):
        """
        Merge infos into the backlog; the backlog only keeps one
        (merged) info per id, so its size is bounded by the number of ids,
        however long we stay disconnected. Later values win, which matches
        how the sidecar itself overwrites (id x key) values
        """
        for info in infos:
            if 'id' not in info:
                logger.error(f"dropping info without an id: {info}")
                continue
            self.backlog.setdefault(info['id'], {}).update(info)

    def _requeue(self, infos):
        """
        Same as _buffer, for infos that we failed to send; the backlog
        may already contain news about the same ids, that need to win
        """
        for info in infos:
            older = dict(info)
            older.update(self.backlog.get(info['id'], {}))
            self.backlog[info['id']] = older


    async def flush_backlog(self):
        """
        Send the whole backlog as one message
        """
        if self._flushing or not self.backlog:
            return
        self._flushing = True
        try:
            pending = list(self.backlog.values())
            # emits that come in while we send go into a fresh backlog
            self.backlog = {}
            if not await self._send(pending):
                self._requeue(pending)
        finally:
            self._flushing = False


    # info singular: create a list with that one info
    async def emit_info(self, info):
        return await self.emit_infos([info])


    async def emit_infos(self, infos):
        """
        Send infos to the sidecar, or keep them for later

        As long as the backlog is not empty, or is being flushed,
        everything goes through the backlog and is sent by keep_connected();
        this is how we make sure that older data never gets sent
        after newer data, e.g. right after a reconnection.

        Returns True if infos were sent right away
        """
        # logger.debug(f"{self}: emit_infos is sending {infos}")
        from rhubarbe.monitor.prometheus import PrometheusExporter  # avoid circular import
        PrometheusExporter().update(self.category, infos)
        if self.connection and not self.backlog and not self._flushing:
            if await self._send(infos):
                return True
            self._requeue(infos)
        else:
            if not self.backlog:
                logger.warning(f"[no conn.] buffering until the sidecar is back")
            self._buffer(infos)
        return False


    async def _send(self, infos):
        """
        The actual sending; returns True if successful
        it is up to the caller to take care of infos if not
        """
        connection = self.connection
        if connection is None:
            return False
        payload = json.dumps(
            dict(category=self.category, action='info', message=infos))
        try:
            await connection.send(payload)
        except ConnectionRefusedError:
            logger.warning(f"[conn. refused] {len(infos)} infos not sent")
            return False
        except websockets.exceptions.ConnectionClosed as exc:
            # this includes both ConnectionClosedError and ConnectionClosedOK
            logger.warning(f"[conn. closed] {len(infos)} infos not sent - {exc}")
            self.connection = None
            return False
        except Exception as exc:
            logger.exception(f"connection.send failed: {type(exc)}: {exc}")
            self.connection = None
            return False
        self.counter += 1
        self.last_send_ok = time.time()
        return True


    async def keep_connected(self):
        """
        A continuous loop that keeps the connection open
        """
        while True:
            if self.connection and self.connection.state == websockets.protocol.State.OPEN:
                logger.debug(f"in keep_connected, connection is open")
                if self.backlog:
                    logger.info(f"flushing backlog with {len(self.backlog)} ids")
                    await self.flush_backlog()
                    logger.info(f"after flush, backlog now has {len(self.backlog)} ids")
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
                # TimeoutError is a subclass of OSError, so it must come first
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout while connecting to {self.url}")
                # a refused connection may come as a ConnectionRefusedError
                # or as an OSError('Multiple exceptions: ...'), when
                # the hostname resolves to several addresses
                except OSError as exc:
                    logger.warning(
                        f"Could not connect to {self.url} at this time: {exc}")
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
