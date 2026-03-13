"""
This implements a daemon that runs on faraday and that
takes care of the defined / authorized logins

think of it as a dedicated nodemanager
"""

# current implementation drawbacks:
# when migrating from an old box to a new box, it would make sense
# for this tool to enforce things that are metastable
# that is to say, moving stuff over from one box to the other
# can result in the homedir being present but without the .ssh dir, or
# with wrong ownership..
# This currently is not well taken care of, because all is done once when
# creating the account


# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w0703,w1202,w1202
# pylint: disable=logging-fstring-interpolation

import time
import os
import pwd

from pathlib import Path

from rhubarbe.logger import accounts_logger as logger
from rhubarbe.config import Config
from rhubarbe.r2labapiproxy import R2labApiProxy


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

    returns
      * True if a change occurred, or the file is deleted
      * False if the file has no change
      * None if the file could not be created (e.g. directory missing)
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
                f"replace_file_with_string: removing file {destination_path}")
            try:
                destination_path.unlink()
            except IOError as exc:
                logger.error(f"Could not remove file {destination_path}: {exc}")
            return True
        # we're done and have nothing to do
        return False
    # overwrite file: create a temp in the same directory
    try:
        with destination_path.open('w') as new:
            new.write(new_contents)
        if chmod:
            destination_path.chmod(chmod)
        if owner:
            os.system(f"chown {owner} {destination_path}")
        return True
    except IOError as exc:
        logger.error(f"Cannot create {destination_path}, {exc}")
        return None

####################


class AccountsManager:

    def __init__(self):
        the_config = Config()
        self.api_url = the_config.value('r2labapi', 'url')
        self.admin_token = the_config.value('r2labapi', 'admin_token')

        self._proxy = None

    def proxy(self):
        if self._proxy is None:
            self._proxy = R2labApiProxy(self.api_url, self.admin_token)
        return self._proxy

    @staticmethod
    def slices_from_passwd():
        """
        Inspect /etc/passwd and return all logins

        ignore the ones that do not have a '_' in them so that
        we leave alone individual accounts
        """
        return [record.pw_name for record in pwd.getpwall()
                if '_' in record.pw_name
                and Path(record.pw_dir).exists()]

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
            f"useradd --create-home --user-group {slicename} --shell /bin/bash",
            f"mkdir /home/{slicename}/.ssh",
            f"chmod 700 /home/{slicename}/.ssh",
            f"chown -R {slicename}:{slicename} /home/{slicename}",
            # see issue #14 and #23
            # f"netsop-accessctl add local {slicename}",
        ]
        for command in commands:
            logger.info(f"Running {command}")
            retcod = os.system(command)
            if retcod != 0:
                logger.error(f"{command} -> {retcod}")


    # for #23
    @staticmethod
    def add_in_access_conf(slicename):
        # do the job of netsop-accessctl by hand
        # to get rid of the dependency
        lines = []
        with open("/etc/security/access.conf", encoding="utf-8") as f:
            for line in f:
                # nothing to do
                if line.startswith(f"+:{slicename}:"):
                    return
                # insert before the first "END local"
                if "END local" in line:
                    lines.append(f"+:{slicename}:ALL\n")
                lines.append(line)
        with open("/etc/security/access.conf", "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.system("chmod 444 /etc/security/access.conf")


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
                                 owner=f"{slicename}:{slicename}")

    @staticmethod
    def apply_keys(slicename, keys_string):
        auth_path = Path("/home") / slicename / ".ssh/authorized_keys"
        replace_file_with_string(auth_path,
                                 keys_string,
                                 chmod=0o600,
                                 owner=f"{slicename}:{slicename}",
                                 remove_if_empty=True)

    ##########
    def fetch_authorized_keys(self, slicename):
        """
        Fetch all SSH keys for all members of the given slice,
        using the /slices/by-name/{name}/keys endpoint.
        Returns a canonical authorized_keys string.
        """
        try:
            ssh_keys = self.proxy().get_slice_keys(slicename)
        except Exception as exc:
            logger.error(
                f"Could not fetch keys for slice {slicename}: {exc}")
            return ""
        key_lines = [(k['key'].replace("\n", "") + "\n")
                     for k in ssh_keys]
        key_lines.sort()
        return "".join(key_lines)

    ##########
    def manage_accounts(self, policy):             # pylint: disable=r0914

        try:
            slices = self.proxy().get_slices()
        except Exception as exc:
            logger.info(f"r2labapi unreachable ({exc}) - back to sleep")
            return

        current_slicenames = []
        if policy == 'leased':
            try:
                current_leases = self.proxy().get_current_leases()
                current_slicenames = [
                    lease['slice_name'] for lease in current_leases]
            except Exception as exc:
                logger.info(
                    f"r2labapi: cannot get leases ({exc}) - back to sleep")
                return

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
                    authorized_keys = self.fetch_authorized_keys(slicename)

            # policy == 'open'
            else:
                authorized_keys = self.fetch_authorized_keys(slicename)

            auths_by_login[slicename] = authorized_keys

        # implement it
        for slicename, keys in auths_by_login.items():
            try:
                if slicename not in logins:
                    self.create_account(slicename)
                # do this always, allows to propagate later changes
                self.create_ssh_config(slicename)
                self.apply_keys(slicename, keys)
                # do this always too, allows to repair broken accounts
                self.add_in_access_conf(slicename)
            except Exception:
                logger.exception(f"Could not deal with slice {slicename}")

    def run_forever(self, cycle, policy):
        while True:
            beg = time.time()
            logger.info("accounts manager "
                        f"policy = {policy}, cycle {cycle}s")
            self.manage_accounts(policy)
            now = time.time()
            duration = now - beg
            towait = cycle - duration
            if towait > 0:
                logger.info(f"accounts manager - "
                            f"sleeping for {towait:.2f}s")
                time.sleep(towait)
            else:
                logger.info(f"duration {duration}s exceeded cycle {cycle}s - "
                            f"skipping sleep")

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
            logger.error(f"Unknown policy {policy} - using 'closed'")
            policy = 'closed'
        # trick is
        if cycle != 0:
            self.run_forever(cycle, policy)
        else:
            logger.info(f"accounts manager oneshot "
                        f"policy = {policy}")
            self.manage_accounts(policy)
