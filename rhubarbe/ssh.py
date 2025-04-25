"""
The SshProxy class is the very early seed of what became SshProxy in apssh

This code still has it, mostly out of laziness
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0703, w1202

import time
import random
import asyncio
import asyncssh

DEBUG = False
# DEBUG = True

from .logger import monitor_logger as logger


class MySSHClientSession(asyncssh.SSHClientSession):
    """
    this class is a specialization of asyncssh's session object,
    - see clientsession_closure below -
    this is how we can access self.node which is a reference to
    the corresponding Node object
    """
    def __init__(self, *args, **kwds):
        self.data = ""
        self.node = None
        self.command = None
        super().__init__(*args, **kwds)

    def data_received(self, data, datatype):
        # not adding a \n since it's already in there
        if DEBUG:
            print('SSS DR: {}:{}-> {} [[of type {}]]'.
                  format(self.node, self.command, data, datatype), end='')
        self.data += data

    def connection_made(self, conn):                    # pylint: disable=w0221
        if DEBUG:
            print(f'SSS CM: {self.node} {conn}')

    def connection_lost(self, exc):
        if exc:
            if DEBUG:
                print(f'SSS CL: {self.node} - exc={exc}')

    def eof_received(self):
        if DEBUG:
            print(f'SSS EOF: {self.node}')


class MySSHClient(asyncssh.SSHClient):
    def connection_made(self, conn):
        if DEBUG:
            print(f"SSC Connection made to "
                  f" {conn.get_extra_info('peername')[0]}.")

    def auth_completed(self):
        if DEBUG:
            print('SSC Authentication successful.')


class SshProxy:
    """
    talk to a Node's control interface using ssh
    """
    def __init__(self, node, username='root', verbose=False):
        self.node = node
        self.username = username
        self.verbose = verbose
        #
        self.hostname = self.node.control_hostname()
        self.status = None
        self.conn, self.client = None, None

    def __repr__(self):
        return f"SshProxy {self.node}"

    # make this an asynchroneous context manager
    # async with SshProxy(...) as ssh:
    #
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        # xxx this might be a little harsh, in the case
        # where an exception did occur
        await self.close()

    async def connect(self, timeout=None):
        begin = time.time()
        # if we don't ctch the exception (typically CancelError)
        # we need this to be set
        retcod = False
        try:
            self.conn, self.client = await asyncio.wait_for(
                asyncssh.create_connection(
                    MySSHClient, self.hostname, username=self.username,
                    known_hosts=None
                ),
                timeout=timeout)
            retcod = True
        # we need to let CancelError propagate here, otherwise there is no way to
        # stop the task from continuing when a timeout has occurred and we want to abort
        # typically within the nightly script, or other waiting tasks when a node is not well
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
        # except (OSError, asyncssh.Error, asyncio.TimeoutError, asyncio.exceptions.CancelledError) as exc:
            logger.debug(f"SSH FAIL on {self.hostname} {type(exc)=} {exc=}")
            self.conn, self.client = None, None
            retcod = False
        except ValueError as exc:
            # seen raised by asyncssh for some reason,
            # anyway bottom line is we can't connect
            #
            logger.debug(f"SSH FAIL on {self.hostname} with ValueError, {exc=}")
            self.conn, self.client = None, None
            retcod = False
        finally:
            end = time.time()
            logger.debug(f"SSH connect {self.hostname} took {end-begin:.3f}s {retcod=}")
            return retcod

    async def run(self, command):
        """
        Run a command
        """
        class ClientsessionClosure(MySSHClientSession):
            def __init__(ssh_client_session,            # pylint: disable=e0213
                         *args, **kwds):
                ssh_client_session.node = self.node
                ssh_client_session.command = command
                super().__init__(*args, **kwds)

        # print(5*'-', "running on ", self.hostname, ':', command)
        begin = time.time()
        try:
            chan, session = await self.conn.create_session(
                ClientsessionClosure, command)
            await chan.wait_closed()
            output = session.data
        except Exception as exc:
            logger.info(f"failed to SSH run {self.hostname} - {type(exc)=} {exc=}")
            output = None
        finally:
            end = time.time()
            logger.info(f"SSH run {self.hostname} took {end-begin:.3f}s")
            return output

    # >>> asyncio.iscoroutine(asyncssh.SSHClientConnection.close)
    # False
    async def close(self):
        if self.conn is not None:
            self.conn.close()
            await self.conn.wait_closed()
        self.conn = None

    async def wait_for(self, backoff, timeout=5.):
        """
        Wait until the ssh service is usable
        """
        self.status = False
        while True:
            if self.verbose:
                await self.node.feedback('ssh_status', "trying to connect")
            self.status = await self.connect(timeout)
            if self.status:
                if self.verbose:
                    await self.node.feedback('ssh_status', "connection OK")
                await self.close()
                return self.status
            # random.random() is between 0. and 1.
            # and so as we need something between 0.5 and 1.5
            random_backoff = (0.5 + random.random()) * backoff
            if self.verbose:
                await self.node.feedback(
                    'ssh_status',
                    f"cannot connect, backing off for {random_backoff:.3}s")
            await asyncio.sleep(random_backoff)


# mostly test-oriented
if __name__ == '__main__':

    def main():
        import sys
        from rhubarbe.node import Node

        async def probe(host, message_bus):
            node = Node(host, message_bus)
            proxy = SshProxy(node, verbose=True)
            conn = await proxy.connect()
            if not conn:
                return False
            out1 = await proxy.run(
                'cat /etc/lsb-release /etc/fedora-release 2> /dev/null')
            print(f"command1 returned {out1}")
            out2 = await proxy.run('hostname')
            print(f"command2 returned {out2}")
            await proxy.close()
            return True

        message_bus = asyncio.Queue()

        nodes = sys.argv[1:]
        tasks = [probe(node, message_bus) for node in nodes]

        retcods = asyncio.new_event_loop()\
            .run_until_complete(asyncio.gather(*tasks))

        for node, retcod in zip(nodes, retcods):
            print(f"{node}:{retcod}")
    main()
