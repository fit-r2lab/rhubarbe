import asyncio
import aiohttp
import os
import pwd
import time
import calendar
from datetime import datetime
import json
import uuid
import traceback

from .logger import logger
from .config import Config
from .omfsfaproxy import OmfSfaProxy

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
            self.broken = "(no component)"    

    def __repr__(self):
        now = time.time()
        if self.iuntil < now:
            time_message = 'expired'
#        elif self.ifrom < now:
#            time_message = "from now until {}".format(self.human(self.iuntil, False))
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

    def currently_valid(self, login, component_name):
        """
        tells if the lease is currently applicable
        """
        if debug: logger.info("currently_valid with lease {}: ".format(self))
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
        self.message_bus = message_bus
        # don't use os.getlogin() as this gives root if under su
        self.login = pwd.getpwuid(os.getuid())[0]
        # connection to the omf-sfa server
        omf_sfa_server = Config().value('authorization', 'leases_server')
        omf_sfa_port = Config().value('authorization', 'leases_port')
        self.unique_component_name = Config().value('authorization', 'component_name')
        self.omf_sfa_proxy \
            = OmfSfaProxy(omf_sfa_server, omf_sfa_port,
                          # certificate (when not doing GET)
                          os.path.expanduser("~/.omf/user_cert.pem"),
                          # related keyfile (root has its private key in cert)
                          None if self.login == 'root' else os.path.expanduser("~/.ssh/id_rsa"),
                          # the unique node name 
                          self.unique_component_name)
        ### computed later
        # a list of Lease objects
        self.leases = None
        # output from omf-sfa - essentially less as-is
        self.resources = None

    def __repr__(self):
        if self.leases is None:
            return "<Leases from {} - **(UNFETCHED)**>"\
                .format(self.omf_sfa_proxy)
        else:
#            return "<Leases from {} - fetched at {} - {} lease(s)>"\
#                .format(self.omf_sfa_proxy, self.fetch_time, len(self.leases))
            return "<Leases from {} - {} lease(s)>"\
                .format(self.omf_sfa_proxy, len(self.leases))

    async def feedback(self, field, msg):
        await self.message_bus.put({field: msg})

    def has_special_privileges(self):
        # the condition on login is mostly for tests
        return self.login == 'root' and os.getuid() == 0

    async def currently_valid(self):
        if self.has_special_privileges():
            return True
        try:
            await self.fetch_all()
            return self._currently_valid(self.login)
        except Exception as e:
            await self.feedback('info', "Could not fetch leases : {}".format(e))
            return False

    async def fetch_all(self):
        await asyncio.gather(self.omf_sfa_proxy.fetch_node_uuid(),
                                  self.fetch_leases())

    async def refresh(self):
        self.leases = None
        await self.fetch_all()

    def sort_leases(self):
        self.leases.sort(key=Lease.sort_key)
        
    async def fetch_leases(self):
        if self.leases is not None:
            return self.leases
        await self._fetch_leases()
        return self.leases

    def ssl_context(self, with_cert):
        return self.omf_sfa_proxy.ssl_context(with_cert, self.login == 'root')

    async def _fetch_leases(self):
        self.leases = None
        try:
            logger.info("Leases are being fetched..")

            text = await self.omf_sfa_proxy.REST_as_json('leases', 'GET', None)
            omf_sfa_answer = json.loads(text)
            if debug:
                logger.info("Leases details {}".format(omf_sfa_answer))
            ### when nothing applies we are getting this
            # {'exception': {'code': 404, 'reason': 'No resources matching the request.'}}
            # which omf_sfa chaps apparently think is normal
            if 'exception' in omf_sfa_answer and \
               omf_sfa_answer['exception']['reason'] == 'No resources matching the request.':
                self.leases = []
                self.resources = []
                self.fetch_time = time.strftime("%Y-%m-%d @ %H:%M")
                return
            if 'error' in omf_sfa_answer:
                raise Exception(omf_sfa_answer['error'])
            # resources is in the OMF format as intact as possible
            self.resources = omf_sfa_answer['resource_response']['resources']
            logger.info("{} leases received".format(len(self.resources)))
# a lot of confusion on the status we find attached to leases
# not sure how this happens exactly
# but I do see leases with no component actually in the way
# in that they prevent other leases to be created at the same time
# so for now I turn off the code that filters out such broken leases
# because they are sometimes relevant (and sometimes not, go figure)
## we keep only the non-broken ones; might not be the best idea ever
## esp. for admin, but well, this currently only is a backup/playground tool 
#            self.resources = [ resource for resource in self.resources if OmfSfaProxy.is_accepted_lease(resource)]
#            logger.info("{} non-pending leases kept".format(len(self.resources)))
            # decoded as a list of Lease objects
            self.leases = [ Lease(resource) for resource in self.resources ]
            self.sort_leases()
            self.fetch_time = time.strftime("%Y-%m-%d @ %H:%M")
                
        except Exception as e:
            if debug: print("Leases.fetch: exception {}".format(e))
            traceback.print_exc()
            await self.feedback('leases_error', 'cannot get leases from {} - exception {}'
                                     .format(self, e))
        
    def _currently_valid(self, login):
        # must have run fetch_all() before calling this
        return any([lease.currently_valid(login, self.unique_component_name)
                    for lease in self.leases])

    # this can be used with a fake message queue, it's synchroneous
    def print(self):
        print(5*'-', self,
              "with special privileges" if self.has_special_privileges() else "")
        if self.leases is not None:
            def two_chars(lease):
                if self.has_special_privileges():
                    return '**'
                if lease.broken:
                    return 'BB'
                if lease.currently_valid(self.login, self.unique_component_name):
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
            return time.strftime(Lease.wire_timeformat, time.localtime(input))
        patterns = [
            # fill in year
            "%Y-{}:00%Z",           #"%Y-{}:00",        
            "%Y-%m-{}:00%Z",        #"%Y-%m-{}:00",     
            "%Y-%m-%dT{}:00%Z",     #"%Y-%m-%dT{}:00",  
            "%Y-%m-%dT{}:00:00%Z",  #"%Y-%m-%dT{}:%M:00",  
        ]

        for pattern in patterns:
            fill = time.strftime(pattern).format(input)
            try:
                n = time.strptime(fill, Lease.wire_timeformat)
                return time.strftime(Lease.wire_timeformat, n)
            except:
                pass    

    # xxx need to check from/until for non-overlap first

    def locate_check_user(self, user):
        prefixes_string = Config().value('authorization', 'user_auto_prefixes')
        candidates = [ user ]
        candidates += [ "{}.{}".format(prefix, user) for prefix in prefixes_string.strip().split() ]
        for candidate in candidates:
            if os.path.exists("/home/{}".format(candidate)):
                return candidate
        return None
        
    async def _add_lease(self, owner, input_from, input_until):
        if owner != 'root':
            owner = self.locate_check_user(owner)
            if not owner:
                print("user {} not found under /home - giving up".format(owner))
                logger.error("Unknown user {}".format(owner))
                return
        t_from = Leases.to_wireformat(input_from)
        if not t_from:
            print("invalid time from: {}".format(input_from))
            return
        t_until = Leases.to_wireformat(input_until)
        if not t_until:
            print("invalid time until: {}".format(input_until))
            return
        # just making sure
        node_uuid = await self.omf_sfa_proxy.fetch_node_uuid()
        lease_request = {
            'name' : str(uuid.uuid1()),
            'valid_from' : t_from,
            'valid_until' : t_until,
            'account_attributes' : { 'name' : owner },
            'components' : [ {'uuid' : node_uuid } ],
            }
        text = await self.omf_sfa_proxy.REST_as_json('leases', 'POST', lease_request)
        # it is easy to update self.leases, let's do it instead of refetching
        try:
            js = json.loads(text)
            if 'exception' in js:
                print("Could not add lease: ", js['exception']['reason'])
            else:
                resource = js['resource_response']['resource']
                if not OmfSfaProxy.is_accepted_lease(resource):
                    print("Could not add lease: conflicting timeslot")
                else:
                    self.leases.append(Lease(resource))
                    self.sort_leases()
                    print("OK")
            return text
        except:
            print('Error', "Cannot add lease - js={}".format(js))
            traceback.print_exc()
            pass

    def get_lease_by_rank(self, lease_rank):
        try:
            irank = int(lease_rank)
            return self.leases[irank-1]
        except:
            pass
        
    async def _update_lease(self, lease_rank, input_from=None, input_until=None):
        if input_from is None and input_until is None:
            logger.info("update_lease : nothing to do")
            return
        if input_from is not None:
            t_from = Leases.to_wireformat(input_from)
            if not t_from:
                print("invalid time from: {}".format(input_from))
                return
        if input_until is not None:
            t_until = Leases.to_wireformat(input_until)
            if not t_until:
                print("invalid time until: {}".format(input_until))
                return
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if not the_lease:
            print("Cannot find lease with rank {}".format(lease_rank))
            return
        lease_uuid = the_lease.uuid
        request = {'uuid' : lease_uuid}
        if input_from is not None:
            request['valid_from'] = t_from
        if input_until is not None:
            request['valid_until'] = t_until
        text = await self.omf_sfa_proxy.REST_as_json('leases', 'PUT', request)
        # xxx we could use this result to update self.leases instead of fetching it again
        try:
            js = json.loads(text)
            js['resource_response']['resource']
            self.leases = None
            print("OK")
            return text
        except:
            traceback.print_exc()
            pass

    async def _delete_lease(self, lease_rank):
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if not the_lease:
            print("Cannot find lease with rank {}".format(lease_rank))
            return
        lease_uuid = the_lease.uuid
        request = {'uuid' : lease_uuid}
        text = await self.omf_sfa_proxy.REST_as_json('leases', 'DELETE', request)
        # xxx we could use this result to update self.leases instead of fetching it again
        try:
            js = json.loads(text)
            if js['resource_response']['response'] == 'OK':
                self.leases = None
                print("OK")
            return text
        except:
            traceback.print_exc()
            pass

    async def main(self, interactive):
        await self.fetch_all()
        self.print()
        if not interactive:
            return 0
        try:
            result = await self.interactive()
            return result
        except (KeyboardInterrupt, EOFError) as e:
            print("Bye")
            return 1

    async def interactive(self):
        help_message = """
Enter one of the letters inside [], and answer the questions

A lease index is a number as shown on the left in the leases list

Times can be entered simply as 
* just 14, or 14:00, for today at 2p.m.
* 14:30 for today at 2:30 p.m., or 
* 27T10:30 for the 27th this month at 10:30 a.m., or
* 12-10T01:00 for the 12th december this year at 1 a.m., or
* 2016-01-02T08:00 for January 1st, 2016, at 8:00

In all the above cases, times will be understood as local time (French Riviera). 

Leaving a time empty means either 'now', or 'do not change', depending on the context
"""
        ### interactive mode
        if not self.has_special_privileges():
            # xxx need to reconfigure omf_sfa
            print("Lease management available to root only for now")
            return
        while True:
            current_time = time.strftime("%H:%M")
            answer = input("{} - Enter command ([l]ist, [a]dd, [u]pdate, [d]elete, [r]efresh, [h]elp, [q]uit : "
                           .format(current_time))
            char = answer[0].lower() if answer else 'l'
            if char == 'l':
                self.print()
            elif char == 'a':
                if self.has_special_privileges():
                    owner = input("For slice name : ")
                else:
                    owner = self.login
                time_from = input("From : ")
                time_until = input("Until : ")
                result = await self._add_lease(owner, time_from, time_until)
                await self.fetch_all()
            elif char == 'u':
                rank = input("Enter lease index : ")
                time_from = input("From : ")
                time_until = input("Until : ")
                result = await self._update_lease(rank, time_from, time_until)
                await self.fetch_all()
            elif char == 'd':
                rank = input("Enter lease index : ")
                result = await self._delete_lease(rank)
                await self.fetch_all()
            elif char == 'r':
                await self.refresh()
                self.print()
            elif char == 'h':
                print(help_message)
            elif char == 'q':
                print('bye')
                break
            else:
                print("Command not understood {}".format(answer))
        return 0
