import os
import pwd
import time
from datetime import datetime
import json
import uuid
import traceback

from .logger import logger
from .config import Config
from .plcapiproxy import PlcApiProxy

debug = False
debug = True

# designed to replace leasesomf ultimately

class Lease:
    """
    a single lease entry
    """

    wire_timeformat = "%Y-%m-%dT%H:%M:%S%Z"

    def __init__(self, plc_lease):
        r = plc_lease
        try:
            self.unique_component_name = Config().value('authorization', 'component_name')
            self.owner = r['name']
            self.lease_id = r['lease_id']
            # this is only for information since there's only one node exposed to SFA
            # so, we take only the first name
            if r['hostname'].split('.')[0] == self.unique_component_name:
                self.subjects = [ self.unique_component_name ]
            else:
                self.subjects = [ r['hostname'] ]
            self.ifrom, self.iuntil = r['t_from'], r['t_until']
            self.broken = False
            # this is only to get __repr__ as short as possible

        except Exception as e:
            self.broken = "lease broken b/c of exception {}".format(e)

        if not self.subjects:
            self.broken = "(no component)"    

    # show a 2-chars prefix
    # one for rights (computed by caller - not shown if not provided)
    # one for situation wrt now: < for past, = for current, > for future
    def __repr__(self, rights_char=None):
        rights_char = 'B' if self.broken \
                      else rights_char if rights_char \
                           else '?'
        now = time.time()
        if self.iuntil < now:
            time_char = '<'
        elif self.ifrom < now:
            time_char = '='
        else:
            time_char = '>'
        time_message = 'from {} until {}'.format(
            self.human(self.ifrom, show_timezone=False),
            self.human(self.iuntil, show_date=False))
        # usual case is that self.subjects == [unique_component_name]
        if len(self.subjects) == 1 and self.subjects[0] == self.unique_component_name:
            scope = ""
        else:
            scope = " -> {}".format(" & ".join(self.subjects))
        return "{}{} {} {} {}".format(rights_char, time_char, time_message, self.owner, scope)

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
        if debug:
            logger.info("currently_valid with lease {}: ".format(self))
        if self.broken:
            if debug:
                logger.info("ignoring broken lease {}".format(self))
            return False
        if not self.owner == login:
            if debug:
                logger.info("login {} is not owner - actual owner is {}".format(login, self.owner))
            return False
        if not self.ifrom <= time.time() <= self.iuntil:
            if debug:
                logger.info("{} : wrong timerange".format(self))
            return False
        if component_name not in self.subjects:
            if debug:
                logger.info("{} not among subjects {}"
                            .format(component_name, self.subjects))
            return False
        # nothing more to check; the subject name cannot be wrong, there's only
        # one node that one can get a lease on
        if debug:
            logger.info("fine")
        return self

class Leases:
    # the details of the omf_sfa instance where to look for leases
    def __init__(self, message_bus):
        self.message_bus = message_bus
        # don't use os.getlogin() as this gives root if under su
        self.login = pwd.getpwuid(os.getuid())[0]
        # connection to the omf-sfa server
        plcapi_server = Config().value('authorization', 'leases_server')
        plcapi_port = Config().value('authorization', 'leases_port')
        self.unique_component_name = Config().value('authorization', 'component_name')
        plcapi_url = "https://{}:{}/PLCAPI/".format(plcapi_server, plcapi_port)
        self.plcapi_proxy = PlcApiProxy(plcapi_url)
        ### computed later
        # a list of Lease objects
        self.leases = None
        # output from omf-sfa - essentially less as-is
        self.plc_leases = None

    def __repr__(self):
        if self.leases is None:
            return "<Leases from {} - **(UNFETCHED)**>"\
                .format(self.plcapi_proxy)
        else:
            return "<Leases from {} - {} lease(s)>"\
                .format(self.plcapi_proxy, len(self.leases))

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
        await self.fetch_leases()

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

    async def _fetch_leases(self):
        self.leases = None
        try:
            logger.info("Leases are being fetched..")
            self.plc_leases = self.plcapi_proxy.GetLeases({'day': 0}, anonymous=True)
            logger.info("{} leases received".format(len(self.plc_leases)))
            # decoded as a list of Lease objects
            self.leases = [ Lease(resource) for resource in self.plc_leases ]
            self.sort_leases()
            self.fetch_time = time.strftime("%Y-%m-%d @ %H:%M")
                
        except Exception as e:
            if debug: print("Leases.fetch: exception {}".format(e))
            traceback.print_exc()
            await self.feedback('leases_error',
                                'cannot get leases from {} - exception {}'
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
            def rights_char(lease):
                # B like broken
                if lease.broken:
                    return 'B'
                # S like super user
                if self.has_special_privileges():
                    return 'S'
                # * for yes you can use it
                if lease.currently_valid(self.login, self.unique_component_name):
                    return '*'
                return ' '
            for i, lease in enumerate(self.leases):
                print("{:3d} {}".format(i+1, lease.__repr__(rights_char=rights_char(lease))))

    ########## material to create and modify leases
    @staticmethod
    def to_epoch(input):
        if not input:
            return time.time()
        if isinstance(input, (int, float)):
            return input
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
                struct_time = time.strptime(fill, Lease.wire_timeformat)
                return int(time.mktime(struct_time))
            except:
                pass    

    def locate_check_user(self, user):
        prefixes_string = Config().value('authorization', 'user_auto_prefixes')
        candidates = [ user ]
        candidates += [ "{}.{}".format(prefix, user)
                        for prefix in prefixes_string.strip().split() ]
        for candidate in candidates:
            if os.path.exists("/home/{}".format(candidate)):
                return candidate
        ### TMP XXX
        return user
        return None
        
    async def _add_lease(self, owner, input_from, input_until):
        if owner != 'root':
            owner = self.locate_check_user(owner)
            if not owner:
                print("user {} not found under /home - giving up".format(owner))
                logger.error("Unknown user {}".format(owner))
                return
        t_from = Leases.to_epoch(input_from)
        if not t_from:
            print("invalid time from: {}".format(input_from))
            return
        t_until = Leases.to_epoch(input_until)
        if not t_until:
            print("invalid time until: {}".format(input_until))
            return
        # just making sure
        try:
            hostname = self.plcapi_proxy.GetNodes()[0]['hostname']
            retcod = self.plcapi_proxy.AddLeases([ hostname], owner, t_from, t_until)
            if 'new_ids' in retcod:
                # do we want to automatically
                # recompute the index ?
                print("OK")
                # force next reload
                self.leases = None
            elif 'errors' in retcod and retcod['errors']:
                for error in retcod['errors']:
                    print("error: {}".format(error))
        except Exception as e:
            print('Error', "Cannot add lease - e={}".format(e))
            traceback.print_exc()
            pass

    def get_lease_by_rank(self, lease_rank):
        try:
            irank = int(lease_rank)
            return self.leases[irank-1]
        except:
            pass
        
    async def _update_lease(self, lease_rank, input_from, input_until):
        update_fields = {}
        if not input_from and not input_until:
            logger.info("update_lease : nothing to do")
            return
        if input_from:
            t_from = Leases.to_epoch(input_from)
            if not t_from:
                print("invalid time from: {}".format(input_from))
                return
            update_fields['t_from'] = t_from
        if input_until:
            t_until = Leases.to_epoch(input_until)
            if not t_until:
                print("invalid time until: {}".format(input_until))
                return
            update_fields['t_until'] = t_until
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if not the_lease:
            print("Cannot find lease with rank {}".format(lease_rank))
            return
        lease_ids = [ the_lease.lease_id ]
        retcod = self.plcapi_proxy.UpdateLeases( lease_ids, update_fields)
        if 'errors' in retcod and retcod['errors']:
            for error in retcod['errors']:
                print("error: {}".format(error))
        else:
            print("OK")
            # force next reload
            self.leases = None

    async def _delete_lease(self, lease_rank):
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if not the_lease:
            print("Cannot find lease with rank {}".format(lease_rank))
            return
        lease_ids = [the_lease.lease_id]
        retcod = self.plcapi_proxy.DeleteLeases(lease_ids)
        if retcod == 1:
            print("OK")
            # force next reload
            self.leases = None
        else:
            print("not deleted")

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
#        if not self.has_special_privileges():
#            # xxx need to reconfigure omf_sfa
#            print("Lease management available to root only for now")
#            return
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
