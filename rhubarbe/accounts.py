"""
This implements a daemon that runs on faraday and that
takes care of the defined / authorized logins

think of it as a dedicated nodemanager
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w0703,w1202,w1202

import time
import os
import pwd

from pathlib import Path

from rhubarbe.logger import accounts_logger as logger
from .config import Config
from .plcapiproxy import PlcApiProxy


####################
# adapted from historical planetlab nodemanager

def replace_file_with_string(destination_path, new_contents,
                             owner=None, chmod=None,
                             remove_if_empty=False):
    """
    Replace a file with new contents
    checks for changes
      does not do anything if previous state was already right
    can handle chmod/chown if requested
    can also remove resulting file if contents are void, if requested

    returns True if a change occurred, or the file is deleted
    """
    try:
        with destination_path.open() as previous:
            current = previous.read()
    except IOError:
        current = ""
    if current == new_contents:
        # if turns out to be an empty string, and remove_if_empty is set,
        # then make sure to trash the file if it exists
        if remove_if_empty and not new_contents and destination_path.is_file():
            logger.info(
                "replace_file_with_string: removing file {}"
                .format(destination_path))
            try:
                destination_path.unlink()
            finally:
                return True                             # pylint: disable=w0150
        # we're done and have nothing to do
        return False
    # overwrite file: create a temp in the same directory
    with destination_path.open('w') as new:
        new.write(new_contents)
    if chmod:
        destination_path.chmod(chmod)
    if owner:
        os.system("chown {} {}".format(owner, destination_path))
    return True

####################


class Accounts:

    def __init__(self):
        the_config = Config()
        self.plcapiurl = the_config.value('plcapi', 'url')
        self.email = the_config.value('plcapi', 'admin_email')
        self.password = the_config.value('plcapi', 'admin_password')

        self._proxy = None

    # not reconnectable for now
    def proxy(self):
        if self._proxy is None:
            # also set debug=True if needed
            self._proxy = PlcApiProxy(self.plcapiurl,
                                      email=self.email,
                                      password=self.password)
        return self._proxy

    @staticmethod
    def slices_from_passwd():
        """
        Inspect /etc/passwd and return all logins

        ignore the ones that do not have a '_' in them so that
        we leave alone individual accounts
        """
        return [record.pw_name for record in pwd.getpwall()
                if '_' in record.pw_name]

    @staticmethod
    def slices_with_authorized():
        """
        Iterator:

        Inspect /home/ to find accounts that have authorized_keys.

        Focus on the ones that have a '_' in them, so that we leave
        alone custom accounts like 'guest' or similar.
        """
        homeroot = Path('/home')
        for authorized in homeroot.glob('*/.ssh/authorized_keys'):
            # need to move 2 steps up
            basename = authorized.parts[-3]
            if '_' not in basename:
                continue
            yield basename

    @staticmethod
    def create_account(slicename):
        """
        Does useradd with the right options
        Plus, creates an empty .ssh dir with proper permissions
        NOTE that this addresses ubuntu for now, fedora 'useradd'
        being slightly different as far as I remember
        (at least wrt homedir creation, IIRC again)
        """
        commands = [
            "useradd --create-home --user-group {x} --shell /bin/bash",
            "mkdir /home/{x}/.ssh",
            "chmod 700 /home/{x}/.ssh",
            "chown -R {x}:{x} /home/{x}",
        ]
        for cmd in commands:
            command = cmd.format(x=slicename)
            logger.info("Running {}".format(command))
            retcod = os.system(command)
            if retcod != 0:
                logger.error("{} -> {}".format(command, retcod))

    @staticmethod
    def create_ssh_config(slicename):
        """
        Initialize slice's .ssh/config that keeps ssh from
        being too picky with host keys and similar

        Performed only if not yet existing
        """
        ssh_config_file = Path("/home") / slicename / ".ssh/config"

        # define the magic sequence for both fit* and data*
        config_bases = ['fit', 'data']
        #
        config_pattern = """Host {base}*
StrictHostKeyChecking no
UserKnownHostsFile=/dev/null
CheckHostIP=no
"""

        ssh_config = "\n".join(
            [config_pattern.format(base=base) for base in config_bases])
        replace_file_with_string(ssh_config_file,
                                 ssh_config,
                                 chmod=0o600,
                                 owner="{x}:{x}".format(x=slicename))

    @staticmethod
    def apply_keys(slicename, keys_string):
        auth_path = Path("/home") / slicename / ".ssh/authorized_keys"
        replace_file_with_string(auth_path,
                                 keys_string,
                                 chmod=0o600,
                                 owner="{x}:{x}".format(x=slicename),
                                 remove_if_empty=True)

    ##########
    @staticmethod
    def authorized_key_lines(plc_slice, plc_persons_by_id, plc_keys_by_id):
        """
        returns the expected contents of that slice's authorized_keys file
        """
        persons = [plc_persons_by_id[id] for id in plc_slice['person_ids']]
        key_ids = set(sum((p['key_ids'] for p in persons), []))
        # somtimes the key comes with a final "\n"
        keys = [plc_keys_by_id[id] for id in key_ids]
        key_lines = [(k['key'].replace("\n", "") + "\n") for k in keys]
        # this is so we get something canonical that does not
        # change everytime
        key_lines.sort()
        return "".join(key_lines)

    ##########
    def manage_accounts(self, policy):             # pylint: disable=r0914

        # get plcapi specification of what should be
        slices = self.proxy().GetSlices(
            {}, ['slice_id', 'name', 'expires', 'person_ids'])
        persons = self.proxy().GetPersons(
            {}, ['person_id', 'email', 'slice_ids', 'key_ids'])
        keys = self.proxy().GetKeys()

        current_leases = []
        if policy == 'leased':
            now = int(time.time())
            current_leases = self.proxy().GetLeases(
                {'alive': now}, ['name'])

        if (current_leases is None or slices is None
                or persons is None or keys is None):
            logger.info("PLCAPI unreachable - back to sleep")
            return

        # prepare data
        persons_by_id = {p['person_id']: p for p in persons}
        keys_by_id = {k['key_id']: k for k in keys}
        # current_slicenames will contain 0 or 1 item
        current_slicenames = [lease['name'] for lease in current_leases]

        # initialize with the slice names that are in /etc/passwd
        logins = self.slices_from_passwd()

        # initialize map login_name -> authorized_keys contents
        # this is where we handle the fact that obsolete slices
        # will effectively have their authorized_keys voided
        auths_by_login = {login: "" for login in logins}

        for sliceobj in slices:
            slicename = sliceobj['name']
            # policy-dependant
            if policy == 'closed':
                authorized_keys = ""

            elif policy == 'leased':
                authorized_keys = ""
                if slicename in current_slicenames:
                    authorized_keys = self.authorized_key_lines(
                        sliceobj, persons_by_id, keys_by_id)

            # policy == 'open'
            else:
                authorized_keys = self.authorized_key_lines(
                    sliceobj, persons_by_id, keys_by_id)

            auths_by_login[slicename] = authorized_keys

        # implement it
        for slicename, keys in auths_by_login.items():
            try:
                if slicename not in logins:
                    self.create_account(slicename)
                # do this always, allows to propagate later changes
                self.create_ssh_config(slicename)
                self.apply_keys(slicename, keys)
            except Exception:
                logger.exception("Could not deal with slice {}"
                                 .format(slicename))

    def run_forever(self, cycle, policy):
        while True:
            beg = time.time()
            logger.info("---------- rhubarbe accounts manager "
                        "policy = {}, cycle {}s"
                        .format(policy, cycle))
            self.manage_accounts(policy)
            now = time.time()
            duration = now - beg
            towait = cycle - duration
            if towait > 0:
                logger.info("---------- rhubarbe accounts manager - "
                            "sleeping for {:.2f}s"
                            .format(towait))
                time.sleep(towait)
            else:
                logger.info("duration {}s exceeded cycle {}s - "
                            "skipping sleep"
                            .format(duration, cycle))

    def main(self, cycle):
        """
        cycle is the duration in seconds of one cycle

        Corner cases:
        * cycle = None : fetch value from config_bases
        * cycle = 0 : run just once (for debug mostly)

        """
        if cycle is None:
            cycle = Config().value('accounts', 'cycle')
        cycle = int(cycle)
        policy = Config().value('accounts', 'access_policy')
        if policy not in ('open', 'leased', 'closed'):
            logger.error("Unknown policy {} - using 'closed'"
                         .format(policy))
            policy = 'closed'
        # trick is
        if cycle != 0:
            self.run_forever(cycle, policy)
        else:
            logger.info("---------- rhubarbe accounts manager oneshot "
                        "policy = {}"
                        .format(policy))
            self.manage_accounts(policy)
