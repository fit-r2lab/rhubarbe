import asyncio
import aiohttp
import os
import time, calendar
from datetime import datetime
import json

from logger import logger
from config import the_config
from inventory import the_inventory

# Nov 2015
# what we get from omf_sfa is essentially something like this
#root@faraday /tmp/asyncio # curl -k https://localhost:12346/resources/leases
# {
#  "resource_response": {
#    "resources": [
#      {
#        "urn": "urn:publicid:IDN+omf:r2lab+lease+ee3614fb-74d2-4097-99c5-fe0b988f2f2d",
#        "uuid": "ee3614fb-74d2-4097-99c5-fe0b988f2f2d",
#        "resource_type": "lease",
#        "valid_from": "2015-11-26T10:30:00Z",
#        "valid_until": "2015-11-26T11:30:00Z",
#        "status": "accepted",
#        "client_id": "b089e80a-3b0a-4580-86ba-aacff6e4043e",
#        "components": [
#          {
#            "name": "r2lab",
#            "urn": "urn:publicid:IDN+omf:r2lab+node+r2lab",
#            "uuid": "11fbdd9e-067f-4ee9-bd98-3b12d63fe189",
#            "resource_type": "node",
#            "domain": "omf:r2lab",
#            "available": true,
#            "status": "unknown",
#            "exclusive": true
#          }
#        ],
#        "account": {
#          "name": "onelab.inria.foo1",
#          "urn": "urn:publicid:IDN+onelab:inria+slice+foo1",
#          "uuid": "6a49945c-1a17-407e-b334-dca4b9b40373",
#          "resource_type": "account",
#          "created_at": "2015-11-26T10:22:26Z",
#          "valid_until": "2015-12-08T15:13:52Z"
#        }
#      }
#    ],
#    "about": "/resources/leases"
#  }

class MyLease:

    wire_timeformat = "%Y-%m-%dT%H:%M:%S%Z"
    human_timeformat = "%m-%d @ %H:%M %Z"

    """
    a simple extract from the omf_sfa loghorrea
    """
    def __init__(self, omf_sfa_resource):
        r = omf_sfa_resource
        try:
            self.owner = r['account']['name']
            # this is only for information since there's only one node exposed to SFA
            self.subject = r['components'][0]['name']
            sfrom = r['valid_from']
            suntil = r['valid_until']
            # turns out that datetime.strptime() does not seem to like
            # the terminal 'Z', so let's do this manually
            if sfrom[-1] == 'Z': sfrom = sfrom[:-1] + 'UTC'
            if suntil[-1] == 'Z': suntil = suntil[:-1] + 'UTC'
            self.ifrom = calendar.timegm(time.strptime(sfrom, self.wire_timeformat))
            self.iuntil = calendar.timegm(time.strptime(suntil, self.wire_timeformat))
            self.broken = False
        except Exception as e:
            if 'components' not in r:
                self.broken = 'lease with no component : ignored'
            else:
                self.broken = "lease broken b/c of exception {}".format(e)
            yield from self.feedback('lease', "ERROR: {}".format(self.broken))

    def __repr__(self):
        if self.broken:
            return "<BROKEN LEASE {}>".format(self.broken)
        now = time.time()
        if self.iuntil < now:
            time_message = 'expired'
        elif self.ifrom < now:
            time_message = "from now until {}".format(self.human(self.iuntil))
        else:
            time_message = 'from {} until {}'.format(
                self.human(self.ifrom),
                self.human(self.iuntil))
        return "<Lease for {} on {} - {}>"\
            .format(self.owner, self.subject, time_message)

    @staticmethod
    def human(epoch):
        return time.strftime(MyLease.human_timeformat, time.localtime(epoch))

    def is_valid(self, owner):
        if self.broken:
            return False
        if not self.owner == owner:
            logger.info("{} : wrong owner - wants {}".format(self, owner))
            return False
        if not self.ifrom <= time.time() <= self.iuntil:
            logger.info("{} : wrong timerange".format(self))
            return False
        # nothing more to check; the subject name cannot be wrong, there's only
        # one node that one can get a lease on
        return self

####################
class Leases:
    # the details of the omf_sfa instance where to look for leases
    def __init__(self, message_bus, login=None):
        self.hostname = the_config.value('permissions', 'leases_server')
        self.port = the_config.value('permissions', 'leases_port')
        self.message_bus = message_bus
        self.login = login if login is not None else os.getlogin()
        self.myleases = None

    def __repr__(self):
        return "<Leases for login {} - omf_sfa@{}:{}>"\
            .format(self.login, self.hostname, self.port)

    def has_special_privileges(self):
        # the condition on login is mostly for tests
        return self.login == 'root' and os.getuid() == 0

    @asyncio.coroutine
    def feedback(self, field, msg):
        yield from self.message_bus.put({field: msg})

    @asyncio.coroutine
    def is_valid(self):
        if self.has_special_privileges():
            return True
        try:
            yield from self.fetch()
            return self._is_valid()
        except Exception as e:
            yield from self.feedback('info', "Could not fetch leases : {}".format(e))
            return False

# TCPConnector with verify_ssl = False
# or ProxyConnector (that inherits TCPConnector) ?
    @asyncio.coroutine
    def fetch(self):
        if self.myleases is not None:
            return
        self.myleases = []
        try:
            connector = aiohttp.TCPConnector(verify_ssl=False)
            url = "https://{}:{}/resources/leases".format(self.hostname, self.port)
            response = yield from aiohttp.get(url, connector=connector)
            text = yield from response.text()
            omf_sfa_answer = json.loads(text)
            resources = omf_sfa_answer['resource_response']['resources']
            self.myleases = [ MyLease(resource) for resource in resources ]
        except Exception as e:
            yield from self.feedback('leases_error', 'cannot get leases from {}'.format(self))
        
    def _is_valid(self):
        valid_leases = [ lease.is_valid(self.login) for lease in self.myleases]
        return valid_leases and valid_leases[0]

    def display(self):
        if self.has_special_privileges():
            yield from self.feedback('lease', "{}: privileged account".format(self))
        else:
            yield from self.feedback('lease', str(self))
            for i, mylease in enumerate(self.myleases):
                yield from self.feedback('lease', "{}: {}".format(i, mylease))

# micro test
if __name__ == '__main__':
    import sys
    @asyncio.coroutine
    def foo(login):
        leases = Leases(asyncio.Queue(), login=login)
        print("leases {}".format(leases))
        valid = yield from leases.is_valid()
        print("valid = {}".format(valid))
        leases.display()
    def test_one_login(login):
        print("Testing for login={}".format(login))
        asyncio.get_event_loop().run_until_complete(foo(login))

    builtin_logins = ['root', 'someoneelse', 'onelab.inria.foo1']
    arg_logins = sys.argv[1:]
    for login in arg_logins + builtin_logins:
        test_one_login(login)
    
