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
    def manage_accounts(self):                          # pylint: disable=r0914

        # get plcapi specification of what should be
        now = int(time.time())
        active_leases = self.proxy().GetLeases({'alive': now})

        # don't lookup slices yet, we need only one
        persons = self.proxy().GetPersons(
            {}, ['person_id', 'email', 'slice_ids', 'key_ids'])
        keys = self.proxy().GetKeys()

        if active_leases is None or persons is None or keys is None:
            return

        persons_by_id = {p['person_id']: p for p in persons}
        keys_by_id = {k['key_id']: k for k in keys}

        # initialize with the slice names that are in /etc/passwd
        logins = self.slices_from_passwd()
        slice_auth_s = {login: "" for login in logins}

        # at this point active_leases has 0 or 1 item
        for lease in active_leases:
            slicename = lease['name']
            slices = self.proxy().GetSlices(
                {'name': slicename},
                ['slice_id', 'name', 'expires', 'person_ids']
            )
            if len(slices) != 1:
                logger.error("Cannot find slice {}".format(slicename))
                continue
            the_slice = slices[0]
            authorized_keys = self.authorized_key_lines(
                the_slice, persons_by_id, keys_by_id)

            slice_auth_s[slicename] = authorized_keys

            # create account if needed
            try:
                # create account if missing
                if slicename not in logins:
                    self.create_account(slicename)
                    self.create_ssh_config(slicename)
            except Exception as exc:
                logger.exception("could not properly deal "
                                 "with active slice {x} (e={exc})"
                                 .format(x=slicename, exc=exc))

        # apply all authorized_keys
        for slicename, keys in slice_auth_s.items():
            authorized_keys_path = Path("/home") / slicename \
                / ".ssh/authorized_keys"
            replace_file_with_string(authorized_keys_path, keys,
                                     chmod=0o400,
                                     owner="{x}:{x}"
                                     .format(x=slicename),
                                     remove_if_empty=True)

    def run_forever(self, cycle):
        while True:
            beg = time.time()
            logger.info("---------- rhubarbe accounts manager (cycle {}s)"
                        .format(cycle))
            self.manage_accounts()
            now = time.time()
            duration = now - beg
            towait = cycle - duration
            if towait > 0:
                logger.info("---------- rhubarbe accounts manager - "
                            "sleeping for {:.2f}s"
                            .format(towait))
                time.sleep(towait)
            else:
                logger.warning("duration {}s exceeded cycle {}s - "
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
        # trick is
        if cycle != 0:
            self.run_forever(cycle)
        else:
            self.manage_accounts()
