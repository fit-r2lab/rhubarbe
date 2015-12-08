import asyncio
import aiohttp
import os
import time, calendar
from datetime import datetime
import json
import uuid

from rhubarbe.logger import logger
from rhubarbe.config import Config

debug = False
debug = True

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
#
# myslice tends to keep leases contiguous, and so does a lot of delete/create
# in this case it seems omf_sfa keeps the lease object but removes the subject from the 'components'
# field. In any case it is quite frequent to see this 'components' being an empty list

class Lease:
    """
    a simple extract from the omf_sfa loghorrea
    """

    wire_timeformat = "%Y-%m-%dT%H:%M:%S%Z"

    def __init__(self, omf_sfa_resource):
        r = omf_sfa_resource
        try:
            self.owner = r['account']['name']
            self.uuid = r['uuid']
            # this is only for information since there's only one node exposed to SFA
            # so, we take only the first name
            self.subjects = [component['name'] for component in r['components']]
            sfrom = r['valid_from']
            suntil = r['valid_until']
            # turns out that datetime.strptime() does not seem to like
            # the terminal 'Z', so let's do this manually
            if sfrom[-1] == 'Z': sfrom = sfrom[:-1] + 'UTC'
            if suntil[-1] == 'Z': suntil = suntil[:-1] + 'UTC'
            self.ifrom = calendar.timegm(time.strptime(sfrom, self.wire_timeformat))
            self.iuntil = calendar.timegm(time.strptime(suntil, self.wire_timeformat))
            self.broken = False
            # this is only to get __repr__ as short as possible
            self.unique_component_name = Config().value('authorization', 'component_name')

        except Exception as e:
            self.broken = "lease broken b/c of exception {}".format(e)

        if not self.subjects:
            self.broken = "lease has no subject component"    

    def __repr__(self):
        now = time.time()
        if self.iuntil < now:
            time_message = 'expired'
        elif self.ifrom < now:
            time_message = "from now until {}".format(self.human(self.iuntil, False))
        else:
            time_message = 'from {} until {}'.format(
                self.human(self.ifrom, show_timezone=False),
                self.human(self.iuntil, show_date=False))
        # usual case is that self.subjects == [unique_component_name]
        if len(self.subjects) == 1 and self.subjects[0] == self.unique_component_name:
            scope = ""
        else:
            scope = " -> {}".format(" & ".join(self.subjects))
        overall = "{}{} - {}".format(self.owner, scope, time_message)
        if self.broken:
            overall = "<BROKEN {}> ".format(self.broken) + overall
        return overall

    def sort_key(self):
        return self.ifrom

    @staticmethod
    def human(epoch, show_date=True, show_timezone=True):
        format = ""
        if show_date:     format += "%m-%d @ "
        format                   += "%H:%M"
        if show_timezone: format += " %Z"
        return time.strftime(format, time.localtime(epoch))

    def is_valid(self, login, component_name):
        if debug: logger.info("is_valid with lease {}: ".format(self))
        if self.broken:
            if debug: logger.info("ignoring broken lease {}".format(self))
            return False
        if not self.owner == login:
            if debug: logger.info("login {} is not owner - actual owner is {}".format(login, self.owner))
            return False
        if not self.ifrom <= time.time() <= self.iuntil:
            if debug: logger.info("{} : wrong timerange".format(self))
            return False
        if component_name not in self.subjects:
            if debug: logger.info("{} not among subjects {}"
                                  .format(component_name, self.subjects))
            return False
        # nothing more to check; the subject name cannot be wrong, there's only
        # one node that one can get a lease on
        if debug: logger.info("fine")
        return self

####################
class Leases:
    # the details of the omf_sfa instance where to look for leases
    def __init__(self, message_bus):
        the_config = Config()
        self.hostname = the_config.value('authorization', 'leases_server')
        self.port = the_config.value('authorization', 'leases_port')
        self.message_bus = message_bus
        self.leases = None
        self.login = os.getlogin()
        self.unique_component_name = Config().value('authorization', 'component_name')

    def __repr__(self):
        if self.leases is None:
            return "<Leases from omf_sfa://{}:{} - **(UNFETCHED)**>"\
                .format(self.hostname, self.port)
        else:
#            return "<Leases from omf_sfa://{}:{} - fetched at {} - {} lease(s)>"\
#                .format(self.hostname, self.port, self.fetch_time, len(self.leases))
            return "<Leases from omf_sfa://{}:{} - {} lease(s)>"\
                .format(self.hostname, self.port, len(self.leases))

    @asyncio.coroutine
    def feedback(self, field, msg):
        yield from self.message_bus.put({field: msg})

    def has_special_privileges(self):
        # the condition on login is mostly for tests
        return self.login == 'root' and os.getuid() == 0

    @asyncio.coroutine
    def is_valid(self):
        if self.has_special_privileges():
            return True
        try:
            yield from self.fetch()
            return self._is_valid(self.login)
        except Exception as e:
            yield from self.feedback('info', "Could not fetch leases : {}".format(e))
            return False

# TCPConnector with verify_ssl = False
# or ProxyConnector (that inherits TCPConnector) ?
    @asyncio.coroutine
    def fetch(self):
        if self.leases is not None and self.unique_component_uuid is not None:
            return
        yield from asyncio.gather(
            self._fetch_leases(),
            self._fetch_node_uuid(),
        )
        return

    @asyncio.coroutine
    def _fetch_leases(self):
        self.leases = []
        try:
            logger.info("Leases are being fetched..")
            connector = aiohttp.TCPConnector(verify_ssl=False)
            url = "https://{}:{}/resources/leases".format(self.hostname, self.port)
            response = yield from aiohttp.get(url, connector=connector)
            text = yield from response.text()
            omf_sfa_answer = json.loads(text)
            if debug: logger.info("Leases details {}".format(omf_sfa_answer))
            ### when nothing applies we are getting this
            # {'exception': {'code': 404, 'reason': 'No resources matching the request.'}}
            # which omf_sfa chaps apparently think is normal
            if 'exception' in omf_sfa_answer and \
               omf_sfa_answer['exception']['reason'] == 'No resources matching the request.':
                self.leases = []
                self.fetch_time = time.strftime("%Y-%m-%d @ %H:%M")
                return
            resources = omf_sfa_answer['resource_response']['resources']
            logger.info("{} leases received".format(len(resources)))
            # we should keep only the non-broken ones but until we are confident
            # that broken leases truly have no other impact, let's be cautious
            self.leases = [ Lease(resource) for resource in resources ]
            self.leases.sort(key=Lease.sort_key)
            self.fetch_time = time.strftime("%Y-%m-%d @ %H:%M")
                
        except Exception as e:
            if debug: print("Leases.fetch: exception {}".format(e))
            import traceback
            traceback.print_exc()            
            yield from self.feedback('leases_error', 'cannot get leases from {} - exception {}'
                                     .format(self, e))
        
    @asyncio.coroutine
    def _fetch_node_uuid(self):
        try:
            logger.info("fetching node{}".format(self.unique_component_name))
            connector = aiohttp.TCPConnector(verify_ssl=False)
            url = "https://{}:{}/resources/nodes?name={}"\
                .format(self.hostname, self.port, self.unique_component_name)
            response = yield from aiohttp.get(url, connector=connector)
            text = yield from response.text()
            omf_sfa_answer = json.loads(text)
            logger.info("Node received")
            r = omf_sfa_answer['resource_response']['resource']
            self.unique_component_uuid = r['uuid']
            logger.info("{} has uuid {}".format(self.unique_component_name,
                                                self.unique_component_uuid))
                
        except Exception as e:
            if debug: print("Nodes.fetch: exception {}".format(e))
            yield from self.feedback('nodes',
                                     'cannot get unique_component_uuid from {} - exception {}'
                                     .format(self, e))
        
    def _is_valid(self, login):
        # must have run fetch() before calling this
        return any([lease.is_valid(login, self.unique_component_name)
                    for lease in self.leases])

    # this can be used with a fake message queue, it's synchroneous
    def print(self):
        print(5*'-', self,
              "with special privileges" if self.has_special_privileges() else "")
        if self.leases is not None:
            def two_chars(lease):
                if lease.broken:
                    return 'BB'
                if lease.is_valid(self.login, self.unique_component_name):
                    return '^^'
                return '..'
            for i, lease in enumerate(self.leases):
                print("{:2d} {}: {}".format(i+1, two_chars(lease), lease))

    ########## material to create and modify leases
    @staticmethod
    def to_wireformat(input):
        if not input:
            return time.strftime(Lease.wire_timeformat, time.localtime())
        if isinstance(input, (int, float)):
            return time.strftime(Lease.wire_timeformat, input)
        patterns = [
            # fill in year
            "%Y-{}:00",        "%Y-{}:00%Z",
            "%Y-%m-{}:00",     "%Y-%m-{}:00%Z",
            "%Y-%m-%dT{}:00",  "%Y-%m-%dT{}:00%Z",
        ]

        for pattern in patterns:
            fill = time.strftime(pattern).format(input)
            try:
                #print("fill={}".format(fill))
                n = time.strptime(fill, Lease.wire_timeformat)
                return time.strftime(Lease.wire_timeformat, n)
            except:
#                import traceback
#                traceback.print_exc()
                pass    

    
    #################### managing leases in the REST API
    @asyncio.coroutine
    def _send_json(self, request, verb):
        # xxx could do better with aiohttp but for now this is good enough
        if debug:
            logger.info("Sending request {}".format(request))
        json_request = json.dumps(request)
        curl = [ 'curl', '-k', '-q' ]
        curl += [ '--cert', os.path.expanduser("~/.omf/user_cert.pem") ]
        curl += [ '-H', "Accept: application/json" ]
        curl += [ '-H', "Content-Type:application/json" ]
        curl += [ '-X', verb ]
        curl += [ '-d', json_request ]
        curl += [ '-i', 'https://{}:{}/resources/leases'.format(self.hostname, self.port) ]

        # xxx retrive result so we know what's going wrong
        with open("/tmp/request.json", "w") as output:
            output.write(json.dumps(json_request) + "\n")

        subprocess = yield from asyncio.create_subprocess_exec(
            *curl,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.STDOUT)
        try:
            out, err = yield from subprocess.communicate()
            if debug: logger.info("curl returned {}".format(out))
            return 0
        except Exception as e:
            import traceback
            traceback.print_exc()
            return 1

    # xxx need to check from/until for non-overlap first
    # xxx would make sense to check the owner is known as well
    # at least in /home/
    @asyncio.coroutine
    def _create_lease(self, owner, input_from, input_until):
        if not self.has_special_privileges():
            print("must be root for now to create leases")
            return
        if not os.path.exists("/home/{}".format(owner)):
            print("Unknown user {}".format(owner))
            logger.error("Unknown user {}".format(owner))
            return
        t_from = Leases.to_wireformat(input_from)
        if not t_from:
            print("invalid time {}".format(input_from))
            return
        t_until = Leases.to_wireformat(input_until)
        if not t_until:
            print("invalid time {}".format(input_until))
            return
        lease_request = {
            'name' : str(uuid.uuid1()),
            'valid_from' : t_from,
            'valid_until' : t_until,
            'account_attributes' : { 'name' : owner },
            'components' : [ {'uuid' : self.unique_component_uuid} ],
            }
        retcod = yield from self._send_json(lease_request, 'POST')
        return retcod

    def get_lease_by_rank(self, lease_rank):
        try:
            irank = int(lease_rank)
            return self.leases[irank-1]
        except:
            pass
        
    @asyncio.coroutine
    def _update_lease(self, lease_rank, input_from=None, input_until=None):
        if input_from is None and input_until is None:
            logger.info("update_lease : nothing to do")
            return
        if input_from is not None:
            t_from = Leases.to_wireformat(input_from)
            if not t_from:
                print("invalid time {}".format(input_from))
                return
        if input_until is not None:
            t_until = Leases.to_wireformat(input_until)
            if not t_until:
                print("invalid time {}".format(input_until))
                return
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if the_lease is None:
            print("Cannot find lease with rank {}"
                  .format(lease_rank))
            return
        lease_uuid = the_lease.uuid
        request = {'uuid' : lease_uuid}
        if input_from is not None:
            request['valid_from'] = t_from
        if input_until is not None:
            request['valid_until'] = t_until
        retcod = yield from self._send_json(request, 'PUT')
        return retcod

    @asyncio.coroutine
    def _delete_lease(self, lease_rank):
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if the_lease is None:
            print("Cannot find lease with rank {}"
                  .format(lease_rank))
            return
        lease_uuid = the_lease.uuid
        request = {'uuid' : lease_uuid}
        retcod = yield from self._send_json(request, 'DELETE')
        return retcod

    @asyncio.coroutine
    def main(self, interactive):
        yield from self.fetch()
        self.print()
        if not interactive:
            return 0
        try:
            result = yield from self.interactive()
            return result
        except KeyboardInterrupt as e:
            print("Bye")
            return 1

    @asyncio.coroutine
    def interactive(self):
        help_message = """
Enter one of the letters inside [], and answer the questions

A lease index is a number as shown on the left in the leases list

Times can be entered simply as 
* 14:00 for today at 2p.m., or 
* 27T10:30 for the 27th this month at 10:30 a.m., or
* 12-10T01:00 for the 12th december this year at 1 a.m., or
* 2016-01-02T08:00 for January 1st, 2016, at 8:00

In all the above cases, times will be understood as local time (French Riviera). 

Leaving a time empty means either 'now', or 'do not change', depending on the context
"""
        ### interactive mode
        while True:
            current_time = time.strftime("%H:%M")
            answer = input("{} - Enter command ([l]ist, [a]dd, [u]pdate, [d]elete, [r]efresh, [h]elp, [q]uit : "
                           .format(current_time))
            char = answer[0].lower() if answer else 'l'
            if char == 'l':
                self.print()
            elif char == 'a':
                owner = input("For slice name : ")
                time_from = input("From : ")
                time_until = input("Until : ")
                result = yield from self._create_lease(owner, time_from, time_until)
                if result == 0:
                    print("OK")
                    self.leases = None
                    yield from self.fetch()
            elif char == 'u':
                rank = input("Enter lease index : ")
                time_from = input("From : ")
                time_until = input("Until : ")
                result = yield from self._update_lease(rank, time_from, time_until)
                if result == 0:
                    print("OK")
                    self.leases = None
                    yield from self.fetch()
            elif char == 'd':
                rank = input("Enter lease index : ")
                result = yield from self._delete_lease(rank)
                if result == 0:
                    print("OK")
                    self.leases = None
                    yield from self.fetch()
            elif char == 'r':
                self.leases = None
                yield from self.fetch()
                self.print()
            elif char == 'h':
                print(help_message)
            elif char == 'q':
                print('bye')
                break
            else:
                print("Command not understood {}".format(answer))
        return 0
