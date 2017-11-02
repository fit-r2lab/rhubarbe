#!/usr/bin/env python3
import sys
import random
import asyncio
import asyncssh

debug = False
#debug = True

# this class is specialized (see clientsession_closure below)
# this is how we can access self.node which is a reference to
# the corresponding Node object
class MySSHClientSession(asyncssh.SSHClientSession):
    def __init__(self, *args, **kwds):
        self.data = ""
        super().__init__(*args, **kwds)

    def data_received(self, data, datatype):
        # not adding a \n since it's already in there
        if debug: print('SSS DR: {}:{}-> {} [[of type {}]]'.
                        format(self.node, self.command, data, datatype), end='')
        self.data += data

    def connection_made(self, conn):
        if debug: print('SSS CM: {} {}'.format(self.node, conn))
        pass

    def connection_lost(self, exc):
        if exc:
            if debug: print('SSS CL: {} - exc={}'.format(self.node, exc))
        pass

    def eof_received(self):
        if debug: print('SSS EOF: {}'.format(self.node, self.command))


class MySSHClient(asyncssh.SSHClient):
    def connection_made(self, conn):
        if debug: print('SSC Connection made to %s.' % conn.get_extra_info('peername')[0])

    def auth_completed(self):
        if debug: print('SSC Authentication successful.')

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
        return "SshProxy {}".format(self.node)
    
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
        try:
            # for private keys
            # also pass here client_keys = [some_list]
            # see http://asyncssh.readthedocs.org/en/latest/api.html#specifyingprivatekeys
            self.conn, self.client = await asyncio.wait_for(
                asyncssh.create_connection(
                    MySSHClient, self.hostname, username=self.username, known_hosts=None
                ),
                timeout = timeout)
            return True
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
            #await self.node.feedback('ssh_status', 'connect failed')
            #print('SshProxy.connect failed: {}'.format(e))
            self.conn, self.client = None, None
            return False

    async def run(self, command):
        """
        Run a command
        """
        class clientsession_closure(MySSHClientSession):
            def __init__(ssh_client_session, *args, **kwds):
                ssh_client_session.node = self.node
                ssh_client_session.command = command
                super().__init__(*args, **kwds)

        #print(5*'-', "running on ", self.hostname, ':', command)
        try:
            chan, session = await self.conn.create_session(clientsession_closure, command)
            await chan.wait_closed()
            return session.data
        except:
            return

    # >>> asyncio.iscoroutine(asyncssh.SSHClientConnection.close)
    # False
    async def close(self):
        if self.conn is not None:
            self.conn.close()
            await self.conn.wait_closed()
        self.conn = None

    async def wait_for(self, backoff, timeout=1.):
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
            # random.random() is between 0. and 1. so we need something between 0.5 and 1.5
            random_backoff = (0.5 + random.random()) * backoff
            if self.verbose:
                await self.node.feedback(
                    'ssh_status',
                    "cannot connect, backing off for {:.3}s".format(random_backoff))
            await asyncio.sleep(random_backoff)


# mostly test-oriented
if __name__ == '__main__':        

    from rhubarbe.node import Node

    async def probe(h, message_bus):
        node = Node(h, message_bus)
        proxy = SshProxy(node, verbose=True)
        c = await proxy.connect()
        if not c:
            return False
        out1 = await proxy.run('cat /etc/lsb-release /etc/fedora-release 2> /dev/null')
        print("command1 returned {}".format(out1))
        out2 = await proxy.run('hostname')
        print("command2 returned {}".format(out2))
        await proxy.close()
        return True

    message_bus = asyncio.Queue()

    nodes = sys.argv[1:]
    tasks = [probe(node, message_bus) for node in nodes]

    retcods = asyncio.get_event_loop().run_until_complete(asyncio.gather(*tasks))

    for node, retcod in zip(nodes, retcods):
        print("{}:{}".format(node, retcod))
