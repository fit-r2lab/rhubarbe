"""
This implements a daemon that runs on faraday and that

* cyclically fetches something like GetSlivers()
  and adjusts local accounts accordingly
* it is also planned that it will listen on some port
  so that one can get it to fuse its cycle an redo the
  core of its job right away

think of it as a dedicated nodemanager
"""

import time
import os.path
import pwd
import glob

from rhubarbe.logger import accounts_logger as logger
from .config import Config
from .plcapiproxy import PlcApiProxy

####################
# stolen from historical planetlab nodemanager


def replace_file_with_string(filename, new_contents,
                             owner=None, chmod=None, remove_if_empty=False):
    """
    Replace a file with new contents
    checks for changes
      does not do anything if previous state was already right
    can handle chmod/chown if requested
    can also remove resulting file if contents are void, if requested

    returns True if a change occurred, or the file is deleted
    """
    try:
        with open(filename) as f:
            current = f.read()
    except:
        current = ""
    if current == new_contents:
        # if turns out to be an empty string, and remove_if_empty is set,
        # then make sure to trash the file if it exists
        if remove_if_empty and not new_contents and os.path.isfile(filename):
            logger.info(
                "replace_file_with_string: removing file {}".format(filename))
            try:
                os.unlink(filename)
            finally:
                return True
        # we're done and have nothing to do
        return False
    # overwrite filename file: create a temp in the same directory
    path = os.path.dirname(filename) or '.'
    with open(filename, 'w') as f:
        f.write(new_contents)
    if chmod:
        os.chmod(filename, chmod)
    if owner:
        retcod = os.system("chown {} {}".format(owner, filename))
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
            self._proxy = PlcApiProxy(self.plcapiurl,
                                      email=self.email, password=self.password,
                                      # debug = True
                                      )
        return self._proxy

    def authorized_home_basenames(self):
        """
        Inspect /home/ to find accounts that have authorized_keys

        temporarily leave alone the ones that start with 'onelab.'

        also focus on the ones that have a '_' in them so that we leave
        artificial accounts like 'michelle' or 'mario' that can maybe
        be helpful some day

        """
        basenames = []
        for homedir in glob.glob("/home/*"):
            basename = os.path.basename(homedir)
            if basename.startswith("onelab."):
                continue
            if '_' not in basename:
                continue
            if not os.path.exists(
                    os.path.join(homedir, ".ssh/authorized_keys")):
                continue
            basenames.append(basename)
        return basenames

    def authorized_legacy_basenames(self):
        """
        Inspect /home/ to find accounts that have authorized_keys
        focus on the ones that start with 'onelab.'

        """
        basenames = []
        for homedir in glob.glob("/home/onelab.*"):
            basename = os.path.basename(homedir)
            if not os.path.exists(
                    os.path.join(homedir, ".ssh/authorized_keys")):
                continue
            basenames.append(basename)
        return basenames

    def all_passwd_entries(self):
        """
        Returns all entries in /etc/passwd
        this is just to figure if an account needs to be created
        """
        return [pw.pw_name for pw in pwd.getpwall()]

    def create_account(self, slicename):
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

    def create_ssh_config(self, slicename):
        """
        Initialize slice's .ssh/config that keeps ssh from
        being too picky with host keys and similar

        Performed only if not yet existing
        """
        ssh_config_file = "/home/{x}/.ssh/config".format(x=slicename)

        if not os.path.exists(ssh_config_file):

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
    def authorized_key_lines(self, plc_slice,
                             plc_persons_by_id, plc_keys_by_id):
        """
        returns the expected contents of that slice's authorized_keys file
        """
        slicename = plc_slice['name']
        persons = [plc_persons_by_id[id] for id in plc_slice['person_ids']]
        key_ids = set(sum((p['key_ids'] for p in persons), []))
        keys = [plc_keys_by_id[id] for id in key_ids]
        key_lines = [(k['key'] + "\n") for k in keys]
        # this is so we get something canonical that does not
        # change everytime
        key_lines.sort()
        return "".join(key_lines)

    ##########
    def manage_accounts(self):
        # get context
        passwd_entries = self.all_passwd_entries()
        home_basenames = self.authorized_home_basenames()
        legacy_basenames = self.authorized_legacy_basenames()

        # get plcapi specification of what should be
        slices = self.proxy().GetSlices(
            {}, ['slice_id', 'name', 'expires', 'person_ids'])
        persons = self.proxy().GetPersons(
            {}, ['person_id', 'email', 'slice_ids', 'key_ids'])
        keys = self.proxy().GetKeys()

        if slices is None or persons is None or keys is None:
            logger.error("Cannot reach PLCAPI endpoint at this time - back to sleep")
            return

        persons_by_id = {p['person_id']: p for p in persons}
        keys_by_id = {k['key_id']: k for k in keys}

        active_slices = []

        for slice in slices:
            slicename = slice['name']
            authorized_keys = self.authorized_key_lines(
                slice, persons_by_id, keys_by_id)

            # don't bother to create an account if the slice has no key
            if authorized_keys:
                try:
                    active_slices.append(slicename)
                    # create account if missing
                    if slicename not in passwd_entries:
                        self.create_account(slicename)
                    # dictate authorized_keys contents
                    ssh_auth_keys = "/home/{x}/.ssh/authorized_keys".format(
                        x=slicename)
                    replace_file_with_string(ssh_auth_keys, authorized_keys,
                                             chmod=0o400,
                                             owner="{x}:{x}".format(
                                                 x=slicename),
                                             remove_if_empty=True)
                    self.create_ssh_config(slicename)

                except Exception as e:
                    logger.exception("could not properly deal "
                                     "with active slice {x} (e={e})"
                                     .format(x=slicename, e=e))

        # find out about slices that currently have suthorized keys but should
        # not
        for slicename in home_basenames:
            if slicename not in active_slices:
                try:
                    logger.info(
                        "Removing authorized_keys for {x}".format(x=slicename))
                    ssh_auth_keys = "/home/{x}/.ssh/authorized_keys".format(
                        x=slicename)
                    os.unlink(ssh_auth_keys)
                except Exception as e:
                    logger.exception("could not properly deal "
                                     "with inactive slice {x} (e={e})"
                                     .format(x=slicename, e=e))

        # a one-shot piece of code : turn off legacy slices
        for slicename in legacy_basenames:
            try:
                logger.info(
                    "legacy slicename {x} "
                    "needs to be shutdown".format(x=slicename))
                ssh_auth_keys = "/home/{x}/.ssh/authorized_keys".format(
                    x=slicename)
                # xxx enable the following line to tear down legacy slices
                # os.unlink(ssh_auth_keys)
            except Exception as e:
                logger.exception("could not properly deal "
                                 "with inactive slice {x} (e={e})"
                                 .format(x=slicename, e=e))


    def run_forever(self, period):
        while True:
            beg = time.time()
            logger.info("---------- rhubarbe accounts manager (period {})"
                        .format(period))
            self.manage_accounts()
            now = time.time()
            duration = now - beg
            towait = period - duration
            logger.info("---------- rhubarbe accounts manager - sleeping for {}"
                        .format(towait))
            if towait <= 0:
                logger.warning("duration {} exceeded period {} - skipping sleep"
                               .format(duration, period))
            else:
                time.sleep(period - duration)

    def main(self, cycle):
        if cycle is None:
            cycle = Config().value('accounts', 'cycle')
        cycle = int(cycle)
        if cycle != 0:
            self.run_forever(cycle)
        else:
            self.manage_accounts()
              
