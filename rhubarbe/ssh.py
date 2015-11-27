#!/usr/bin/env python3
import sys
import random
import asyncio
import asyncssh

from node import Node

debug = False

# this class is specialized (see clientsession_closure below)
# this is how we can access self.node which is a reference to
# the corresponding Node object
class MySSHClientSession(asyncssh.SSHClientSession):
    def data_received(self, data, datatype):
        # not adding a \n since it's already in there
        if debug: print('SSS: {}:{}-> {}'.format(self.node, self.command, data), end='')

    def connection_made(self, conn):
        if debug: print('SSS: connection made {}'.format(conn))
        pass

    def connection_lost(self, exc):
        if exc:
            if debug: print('SSS connection lost: ' + str(exc), file=sys.stderr)
        pass

    def eof_received(self):
        if debug: print('SSS {}:self.command got EOF'.format(self.node, self.command))


class MySSHClient(asyncssh.SSHClient):
    def connection_made(self, conn):
        if debug: print('SSC Connection made to %s.' % conn.get_extra_info('peername')[0])

    def auth_completed(self):
        if debug: print('SSC Authentication successful.')

class SshProxy:
    def __init__(self, node, verbose=False):
        self.node = node
        self.verbose = verbose
        #
        self.hostname = self.node.control_hostname()
        self.status = None

    def __repr__(self):
        return "SshProxy {}".format(self.node.hostname)
    
    @asyncio.coroutine
    def connect(self):
        try:
            # for private keys
            # also pass here client_keys = [some_list]
            # see http://asyncssh.readthedocs.org/en/latest/api.html#specifyingprivatekeys
            self.conn, self.client = yield from asyncssh.create_connection(
                MySSHClient, self.hostname, username='root', known_hosts=None
                )
            return True
        except (OSError, asyncssh.Error) as e:
            #yield from self.node.feedback('ssh_status', 'connect failed')
            # print('MYssh failed: {}'.format(e))
            return False

    @asyncio.coroutine
    def run(self, command):
        class clientsession_closure(MySSHClientSession):
            def __init__(ssh_client_session, *args, **kwds):
                ssh_client_session.node = self.node
                ssh_client_session.command = command
                super().__init__(*args, **kwds)

        print(5*'-', "running on ", self.hostname, ':', command)
        chan, session = yield from self.conn.create_session(clientsession_closure, command)
        yield from chan.wait_closed()

    @asyncio.coroutine
    def close(self):
        self.conn.close()

    @asyncio.coroutine
    def wait_for(self, backoff):
        self.status = False
        while True:
            self.status = yield from self.connect()
            if self.status:
                if self.verbose:
                    yield from self.node.feedback('ssh_status', "connection OK")
                yield from self.close()
                return self.status
            random_backoff = (0.5+random.random())*backoff
            if self.verbose:
                yield from self.node.feedback(
                    'ssh_status',
                    "cannot connect, backing off for {:.3}s".format(random_backoff))
            yield from asyncio.sleep(random_backoff)

@asyncio.coroutine
def probe(h, message_bus):
    node = Node(h, message_bus)
    proxy = SshProxy(node)
    c = yield from proxy.connect()
    if not c:
        return False
    yield from proxy.run('cat /etc/lsb-release /etc/fedora-release 2> /dev/null')
    yield from proxy.run('hostname')
    yield from proxy.close()
    return True

if __name__ == '__main__':        

    message_bus = asyncio.Queue()

    nodes = sys.argv[1:]
    tasks = [ probe(node, message_bus) for node in nodes]

    retcods = asyncio.get_event_loop().run_until_complete(asyncio.gather(*tasks))

    for node, retcod in zip(nodes, retcods):
        print("{}:{}".format(node, retcod))
                
