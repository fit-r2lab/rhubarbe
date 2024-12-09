"""
the classes that help manage our PDU devices
"""

# pylint: disable=fixme, unspecified-encoding

from dataclasses import dataclass
import asyncio

from pkg_resources import resource_exists, resource_filename
from dataclass_wizard import YAMLWizard
import asyncssh

from .config import Config
from .logger import logger


VERBOSE = False
#VERBOSE = True

# pylint: disable=missing-function-docstring, missing-class-docstring
def verbose(*args, **kwds):
    if not VERBOSE:
        return
    print(*args, **kwds)




@dataclass
class PduHost:

    name: str
    type: str
    IP: str                                     # pylint: disable=invalid-name
    username: str
    password: str
    chain_length: int = 1


    def oneline(self):
        text = f"ssh = {self.username}@{self.IP}"
        if self.is_chained():
            text += f" == daisy chain of {self.chain_length} boxes"
        else:
            text += " == no chaining"
        return text


    def is_chained(self):
        return self.chain_length > 1


    async def run_pdu_shell(self, action, *args, show_stdout=True):
        """
        run the 'pdu' command on the PDU host where this input is attached
        """
        env = dict(PDU_USERNAME=self.username, PDU_PASSWORD=self.password)
        # contains sensitive information
        # verbose(10*'-', "debug: the script configuration:")
        # for key, value in env.items():
        #     verbose(f'export {key}="{value}"')
        # verbose(10*'-')
        script = f"scripts/{self.type}"
        exists = resource_exists('rhubarbe', script)
        if not exists:
            print(f"Could not find script '{script}' - exiting")
            return 255

        command_path = resource_filename('rhubarbe', script)
        command = f"{command_path} {action} {self.IP} {' '.join(str(arg) for arg in args)}"
        verbose(f"PduHost: running command '{command}'")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env)
        stdout, stderr = await proc.communicate()
        verbose(f"{proc.returncode=}")
        if proc.returncode == 255:
            print("FAILURE:", stdout.decode(), end="")
            return 255
        verbose("SUCCESS")
        if stdout and show_stdout:
            print(stdout.decode(), end="")
        if stderr:
            print("STDERR", stderr.decode(), end="")
        return proc.returncode


    async def probe(self):
        return await self.run_pdu_shell("probe")


@dataclass
class PduInput:

    pdu_host_name: str
    outlet: int
    in_chain: int = 0
    # not in the YAML, will be located
    # after loading
    pdu_host: PduHost = None


    def __repr__(self):
        return f"{self.pdu_host_name}:{self.oneline()}"


    def oneline(self):
        if self.in_chain == 0:
            return f"        outlet {self.outlet}"
        return f"chain-{self.in_chain}@outlet-{self.outlet}"

    async def run_pdu_shell(self, action, *args, device_name="", show_stdout=True):
        """
        run the 'pdu' command on the PDU host where this input is attached
        """
        if device_name:
            message = f"PduInput: running action '{action}' on device {device_name}"
        verbose(message)
        return await self.pdu_host.run_pdu_shell(action, *args, show_stdout=show_stdout)




@dataclass
class PduDevice:
    name: str
    inputs: list[PduInput]
    description: str = ""
    ssh_hostname: str = ""
    ssh_username: str = "root"
    # will be maintained by actions made
    status_cache: bool | None = None
    # if set to True, the device will be turned off when the testbed is idle
    auto_turn_off: bool = False


    async def is_pingable(self, timeout=1) -> bool:
        """
        when there is a ssh_hostname attached, does that answer ping ?
        """
        if self.ssh_hostname is None:
            return False
        command = f"ping -c 1 -w {timeout} {self.ssh_hostname}"
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        # failure occurs through timeout
        returncode = await asyncio.wait_for(proc.wait(), timeout=2)
        return returncode == 0


    async def turn_off_through_ssh_if_pingable(self) -> bool:
        """
        if the device is pingable, turn it off through ssh

        returns True if device is pingable and the ssh command succeeds
        """
        is_pingable = await self.is_pingable()
        if not is_pingable:
            return False
        print(f"Doing a soft TURN OFF on device {self.name}")
        command = "shutdown -h now"
        ssh_options = {
            'known_hosts': None,
            'username': "root",
        }

        async with asyncssh.connect(self.ssh_hostname, **ssh_options) as conn:
            completed = await conn.run(command)
            return completed.returncode == 0


    async def attempt_soft_reset(self):
        soft_turned_off = await self.turn_off_through_ssh_if_pingable()
        # give it some time to complete the soft OFF
        # xxx could be configurable
        if soft_turned_off:
            await asyncio.sleep(15)


    async def run_pdu_shell_on_all_inputs(self, action, *, show_stdout=True):
        retcod = await asyncio.gather(
            *(input.run_pdu_shell(
                action, input.in_chain, input.outlet, device_name = self.name,
                show_stdout=show_stdout)
            for input in self.inputs)
        )
        return retcod


    async def _status_or_on(self, action, *, show_stdout=True):
        """
        the ON and STATUS actions are similar in their logic
        - we are sure that the node is ON if any input is ON
        - we are sure that the node is OFF if all inputs are OFF
        - otherwise, we don't know for sure
        """
        retcods = await self.run_pdu_shell_on_all_inputs(
            action, show_stdout=show_stdout)
        # if all inputs fail, we can't say
        if all(retcod == 255 for retcod in retcods):
            self.status_cache = 255
        # if any input is ON then the device is ON
        elif any(retcod == 0 for retcod in retcods):
            self.status_cache = 0
        # if any input is unknown, then we can't say
        elif any(retcod == 255 for retcod in retcods):
            self.status_cache = 255
        # else we are sure the node is OFF
        else:
            self.status_cache = 1
        verbose(f"{self.name} status_cache is now {self.status_cache}")
        return self.status_cache


    async def on(self):                                 # pylint: disable=invalid-name
        return await self._status_or_on('on')

    async def status(self, show_stdout=True):
        return await self._status_or_on('status', show_stdout=show_stdout)

    async def off(self):
        """
        the OFF action
        start with attempting a soft reset
        after that, the logic is simple because
        * retcod can only be 0 or 255 (not 1)
        * and retcod == 0 means the OFF has succeeded
        """
        await self.attempt_soft_reset()
        retcods = await self.run_pdu_shell_on_all_inputs('off')
        # if all inputs say 0 (they were turned off), node is off
        if all(retcod == 0 for retcod in retcods):
            self.status_cache = 1
        else:
            self.status_cache = 255
        return self.status_cache


    async def reset(self):
        """
        the RESET action
        """
        off = await self.off()
        if off != 1:
            return 255
        # xxx could be configurable
        await asyncio.sleep(15)
        return await self.on()



    async def run_action(self, action):
        """
        returns the main retcod as defined above (0=OK/ON 1=OK/OFF 255=KO)
        also update self.status when relevant

        when a device has several inputs:

        STATUS returns
            0 if at least one input is ON
            1 if all inputs are OFF
            255 otherwise

        ON will try to turn on all inputs
           if AT LEAST ONE works fine, the return code is 0 - 255 otherwise

        OFF will try to turn off all inputs
           if ALL work fine, the return code is 1 - 255 otherwise

        RESET mainly does
            OFF; if fails, reset fails
            sleep for a bit, ON, and returns what ON returns

        OFF and RESET will first try to do a "soft" turn-off using ssh
        (if contigured)
        they go on regardless to do the "hard" turn-off, after some delay
        """

        match action:
            case 'on':
                return await self.on()
            case 'off':
                return await self.off()
            case 'status':
                return await self.status()
            case 'reset':
                return await self.reset()
            case _:
                print(f"OOPS: unknown action '{action}'")
                return 255



@dataclass
class InventoryPdus(YAMLWizard):

    pdu_hosts: list[PduHost]
    devices: list[PduDevice]


    @staticmethod
    def load() -> "InventoryPdus":
        the_config = Config()
        yaml_path = the_config.value('testbed', 'inventory_pdus_path')
        try:
            with open(yaml_path) as feed:
                return InventoryPdus.from_yaml(feed.read()).solve_references()
        except FileNotFoundError:
            # not all deployments have pdus
            logger.warning(f"file not found {yaml_path}")
            return InventoryPdus([], [])
        except KeyError as exc:
            print(f"something wrong in config file {yaml_path}, {exc}")
            raise


    def solve_references(self):
        """
        fill all PduInput instances with their pdu_host attribute
        """
        hosts_by_name = {pdu_host.name: pdu_host for pdu_host in self.pdu_hosts}
        for device in self.devices:
            for input_ in device.inputs:
                input_.pdu_host = hosts_by_name[input_.pdu_host_name]
        return self


    def status(self):
        """
        displays the status for all known devices
        works sequentially on all hosts so that the output is readable
        """
        print(f"we have {len(self.pdu_hosts)} PDUs and {len(self.devices)} devices. ")
        pdu_host_width = max(len(pdu_host.name) for pdu_host in self.pdu_hosts)
        type_width = max(len(pdu_host.type) for pdu_host in self.pdu_hosts)
        sep = 10 * '='

        async def status_all():
            for pdu_host in self.pdu_hosts:
                print(f"{sep} {pdu_host.name:>{pdu_host_width}} ({pdu_host.type:<{type_width}}) {sep}")
                print(f"{pdu_host.oneline()}")

                await pdu_host.probe()
        with asyncio.Runner() as runner:
            runner.run(status_all())


    def list(self, names=None):
        """
        if no name: list all pdu hosts
        otherwise, list all pdu_hosts AND all pdu_devices
        whose name is in the list (case ignored)
        """
        if not names:
            print(f"we have {len(self.pdu_hosts)} PDUs and {len(self.devices)} devices. "
                  f"(*) means auto_turn_off")

        names = [] if names is None else [n.lower() for n in names]
        pdu_host_width = max(len(pdu_host.name) for pdu_host in self.pdu_hosts)
        type_width = max(len(pdu_host.type) for pdu_host in self.pdu_hosts)
        device_width = max(len(device.name) for device in self.devices)
        sep = 10 * '='
        indent_empty = 5 * ' '
        indent_auto = ' (*) '
        for pdu_host in self.pdu_hosts:
            # if no name was passed, list all pdu_hosts
            if names and pdu_host.name.lower() not in names:
                continue
            print(f"{sep} {pdu_host.name:>{pdu_host_width}} ({pdu_host.type:<{type_width}}) {sep}")
            print(f"{pdu_host.oneline()}")
            for device in self.devices:
                for input_ in device.inputs:
                    indent = indent_auto if device.auto_turn_off else indent_empty
                    if input_.pdu_host_name == pdu_host.name:
                        print(f"{indent}{input_.oneline()} "
                              f"→ {device.name:<{device_width}}")

        # if no name was passed, stop here
        if not names:
            return
        for device in self.devices:
            if device.name.lower() not in names:
                continue
            print(f"{sep} device {device.name:^{device_width}} {sep}")
            for input_ in device.inputs:
                indent = indent_auto if device.auto_turn_off else indent_empty
                print(f"{indent}{input_}")

    def _get_object(self, name, attribute, kind):
        l_objs = getattr(self, attribute)
        for obj in l_objs:
            if obj.name == name:
                return obj
        raise ValueError(f"unknown {kind} '{name}'")
    def get_device(self, name) -> PduDevice:
        return self._get_object(name, 'devices', 'device')
    def get_pdu_host(self, name) -> PduHost:
        return self._get_object(name, 'pdu_hosts', 'pdu_host')
