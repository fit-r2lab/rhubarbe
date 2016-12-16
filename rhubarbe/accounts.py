"""
This implements a daemon that runs on faraday and that

* cyclically fetches something like GetSlivers() and adjusts local accounts accordingly
* it is also planned that it will listen on a port so as to implement 
  a short-circuit

think of it as a dedicated nodemanager
"""

import sys
import os.path
import tempfile
import pwd
import glob

from rhubarbe.logger import accounts_logger as logger
from .config import Config
from .plcapiproxy import PlcApiProxy

####################
# stolen from historical planetlab nodemanager
def replace_file_with_string(filename, new_contents,
                             chmod=None, remove_if_empty=False):
    """
    Replace a file with new contents
    checks for changes: does not do anything if previous state was already right
    can handle chmod if requested
    can also remove resulting file if contents are void, if requested
    performs atomically:
    writes in a tmp file, which is then renamed

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
            logger.info("replace_file_with_string: removing file {}".format(filename))
            try:
                os.unlink(filename)
            finally:
                return True
        # we're done and have nothing to do
        return False
    # overwrite filename file: create a temp in the same directory
    path = os.path.dirname(filename) or '.'
    fd, name = tempfile.mkstemp('', 'repl', path)
    os.write(fd, new_contents)
    os.close(fd)
    if os.path.exists(filename):
        os.unlink(filename)
    shutil.move(name, filename)
    if chmod:
        os.chmod(filename, chmod)
    return True

####################
class Accounts:
    def __init__(self):
        the_config = Config()
        self.plcapiurl = the_config.value('plcapi', 'url')
        self.email = the_config.value('plcapi', 'admin_email')
        self.password = the_config.value('plcapi', 'admin_password')

        self._proxy = None
        self._running = False

    # not reconnectable for now
    def proxy(self):
        if self._proxy is None:
            self._proxy = PlcApiProxy(self.plcapiurl,
                                      email=self.email, password=self.password,
                                      #debug = True
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
            if not '_' in basename:
                continue
            if not os.path.exists(os.path.join(homedir, ".ssh/authorized_keys")):
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
            if not os.path.exists(os.path.join(homedir, ".ssh/authorized_keys")):
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
            "useradd --create-home --user-group {x}",
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

    ssh_config = """Host fit*
    LogLevel=error
    StrictHostKeyChecking no
    UserKnownHostsFile=/dev/null
    CheckHostIP=no
"""

    def cycle(self):
        self._running = True
        logger.info("Accounts.cycle ----------")
        # get context
        passwd_entries = self.all_passwd_entries()
        home_basenames = self.authorized_home_basenames()
        legacy_basenames = self.authorized_legacy_basenames()
        
        # get plcapi specification of what should be 
        slices = self.proxy().GetSlices({},['slice_id', 'name', 'expires', 'person_ids'])
        persons = self.proxy().GetPersons({},['person_id', 'email', 'slice_ids', 'key_ids'])
        keys = self.proxy().GetKeys()

        persons_by_id = { p['person_id'] : p for p in persons }
        keys_by_id = { k['key_id'] : k for k in keys }
        
        active_slices = []
        for slice in slices:
            slicename = slice['name']
            persons = [ persons_by_id[id] for id in slice['person_ids']]
            key_ids = sum( (p['key_ids'] for p in persons), [])
            keys = [ keys_by_id[id] for id in key_ids ]
            authorized = [ k['key'] for k in keys]
            authorized.sort()
            authorized_string = ""
            for k in authorized:
                authorized_string += (k + "\n")

            # don't bother to create an account if the slice has no key
            if authorized:
                try:
                    active_slices.append(slicename)
                    # create account if missing
                    if slicename not in passwd_entries:
                        self.create_account(slicename)
                    # dictate authorized_keys contents
                    ssh_auth_keys = "/home/{x}/.ssh/authorized_keys".format(x=slicename)
                    replace_file_with_string(ssh_auth_keys, authorized_string,
                                             chmod=0o400, remove_if_empty=True)
                    # suggest ssh config - only if not existing
                    ssh_config_file = "/home/{x}/.ssh/config_file".format(x=slicename)
                    if not os.path.exists(ssh_config_file):
                        replace_file_with_string(ssh_config_file,
                                                 self.ssh_config,
                                                 chmod = 0o600)
                    
                except Exception as e:
                    logger.exception("could not properly deal with active slice {x}"
                                     .format(x=slicename))

        # find out about slices that currently have suthorized keys but should not
        for slicename in home_basenames:
            if slicename not in active_slices:
                try:
                    logger.info("Removing authorized_keys for {x}".format(x=slicename))
                    ssh_auth_keys = "/home/{x}/.ssh/authorized_keys".format(x=slicename)
                    os.unlink(ssh_auth_keys)
                except Exception as e:
                    logger.exception("could not properly deal with inactive slice {x}"
                                     .format(x=slicename))
        
        # a one-shot piece of code : turn off legacy slices
        for slicename in legacy_basenames:
            try:
                logger.info("legacy slicename {x} needs to be shutdown".format(x=slicename))
                ssh_auth_keys = "/home/{x}/.ssh/authorized_keys".format(x=slicename)
                # xxx enable the following line to tear down legacy slices
                # os.unlink(ssh_auth_keys)
            except Exception as e:
                logger.exception("could not properly deal with legacy slice {x}"
                                 .format(x=slicename))

        self._running = False

    def main(self):
        return self.cycle()
