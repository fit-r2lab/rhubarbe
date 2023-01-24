"""
the classes that help manage our PDU devices
"""

from dataclasses import dataclass
import asyncio

from pkg_resources import resource_exists, resource_filename

from dataclass_wizard import YAMLWizard
#from yaml import load, CLoader as Loader

from .config import Config


VERBOSE = False
VERBOSE = True

# pylint: disable=missing-function-docstring, missing-class-docstring
def verbose(*args, **kwds):
    if not verbose:
        return
    print(*args, **kwds)




@dataclass
class PduHost:

    name: str
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


    async def run_pdu_shell(self, action, *args):
        """
        run the 'pdu' command on the PDU box where this input is attached
        """
        env = dict(PDU_IP=self.IP, PDU_USERNAME=self.username, PDU_PASSWORD=self.password)
        if VERBOSE:
            print(10*'-', "debug: the script configuration:")
            for key, value in env.items():
                print(f'export {key}="{value}"')
            print(10*'-')
        script = "scripts/pdu"
        exists = resource_exists('rhubarbe', script)
        if not exists:
            print(f"Could not find script '{script}' - exiting")
            return 255

        command_path = resource_filename('rhubarbe', script)
        command = f"{command_path} {action} {' '.join(str(arg) for arg in args)}"
        verbose(f"running {command}")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env)
        stdout, stderr = await proc.communicate()
        verbose(f"{proc.returncode=}")
        if proc.returncode == 255:
            print("FAILURE:", stdout, end="")
            return 255
        verbose("SUCCESS")
        stdout and print(stdout, end="")            # pylint: disable=expression-not-assigned
        stderr and print("STDERR", stderr, end="")  # pylint: disable=expression-not-assigned
        return proc.returncode




@dataclass
class PduInput:

    pdu_host_name: str
    outlet: int
    in_chain: int = 0
    # not in the YAML, will be located
    # after loading
    pdu_host: PduHost = None


    def oneline(self, chained):
        text = f"outlet#{self.outlet}"
        if not chained:
            text += "@box-0"
        else:
            text += f"@box-{self.in_chain}"
        return text


    async def run_pdu_shell(self, action, *args, device_name=""):
        """
        run the 'pdu' command on the PDU box where this input is attached
        """
        if device_name:
            message = f"running action {action} on device {device_name}"
        verbose(message)
        return await self.pdu_host.run_pdu_shell(action, *args)




@dataclass
class PduDevice:
    name: str
    inputs: list[PduInput]
    description: str = ""
    ssh_hostname: str = ""
    ssh_username: str = "root"
    # will be maintained by actions made
    status: bool | None = None


    async def is_pingable(self, timeout=1) -> bool:
        """
        when there is a ssh_hostname attached, does that answer ping ?
        """
        if self.ssh_hostname is None:
            return False
        command = ["ping", "-c", "1", "-w", timeout, self.ssh_hostname]
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        # failure occurs through timeout
        returncode = await asyncio.wait_for(proc.wait())
        return returncode == 0


    async def turn_off_through_ssh_if_pingable(self) -> bool:
        """
        if the device is pingable, turn it off through ssh

        returns True if device is pingable and the ssh command succeeds
        """
        is_pingable = await self.is_pingable()
        if not is_pingable:
            return False
        print("Doing a soft TURN OFF on device {self.name}")
        command = "shutdown -h now"
        async with asyncssh.connect(self.ssh_hostname) as conn:
            completed = conn.run(command)
            return completed.returncode == 0


    async def run_pdu_shell_on_all_inputs(self, action):
        retcod = await asyncio.gather(
            *(input.run_pdu_shell(action, input.outlet, input.in_chain, device_name = self.name)
            for input in self.inputs)
        )
        return retcod


    async def run_action(self, action):
        """
        returns the main retcod as defined above (0=OK/ON 1=OK/OFF 255=KO)
        also update self.status

        when a device has several inputs:

        STATUS returns
            0 if at least one input is ON
            1 if all inputs are OFF
            255 otherwise

        ON will try to turn on all inputs
           if AT LEAST ONE works fine, the return code is 0 - 255 otherwise

        OFF will try to turn off all inputs
           if ALL work fine, the return code is 0 - 255 otherwise

        RESET mainly does
            OFF; if fails, reset fails
            sleep for a bit, ON, and returns what ON returns

        OFF and RESET will first try to do a "soft" turn-off using ssh
        (if contigured)
        they go on regardless to do the "hard" turn-off, after some delay
        """

        match action:
            # do a soft turn off if feasible
            case 'off' | 'reset':
                soft_turned_off = await self.turn_off_through_ssh_if_pingable()
                # give it some time to complete the soft OFF
                # xxx could be configurable
                if soft_turned_off:
                    await asyncio.sleep(15)

        # a reset is primarily a OFF and then a ON
        if action == 'reset':
            print("doing a HARD OFF on {self.name}")
            retcod = await self.run_pdu_shell_on_all_inputs("off")
            print('reset (1)', retcod)
            await asyncio.sleep(5)
            action = 'on'

        match action:
            case 'on' | 'off' | 'status':
                retcod = await self.run_pdu_shell_on_all_inputs(action)
                print(retcod)
                # xxx retcod is now a composite thing....
                if retcod == 255:
                    self.status = None
                    return 255
                verbose("SUCCESS")
                match action:
                    case 'status':
                        if retcod == 0:
                            self.status = True
                            return 0
                        elif retcod == 1:
                            self.status = False
                            return 1
                    case 'on':
                        self.status = True
                        return 0
                    case 'off':
                        self.status = False
                        return 1
            case _:
                print(f"OOPS: unknown action '{action}'")
                self.status = None
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
        except IOError:
            print(f"file not found {yaml_path}")
            raise
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


    def list_all(self):
        print(f"we have {len(self.pdu_hosts)} PDUs and {len(self.devices)} devices")
        pdu_host_width = max(len(pdu_host.name) for pdu_host in self.pdu_hosts)
        device_width = max(len(device.name) for device in self.devices)
        sep1 = 10 * '='
        indent = 4 * ' '
        for pdu_host in self.pdu_hosts:
            print(f"{sep1} {pdu_host.name:^{pdu_host_width}} {sep1}")
            print(f"{pdu_host.oneline()}")
            for device in self.devices:
                for input_ in device.inputs:
                    if input_.pdu_host_name == pdu_host.name:
                        print(f"{indent} {device.name:>{device_width}}"
                              f" ← {input_.oneline(pdu_host.is_chained())}")


    def get_device(self, name) -> PduDevice:
        def spot_name_in_list(name, l_objs, kind):
            for obj in l_objs:
                if obj.name == name:
                    return obj
            raise ValueError(f"unknown {kind} '{name}'")
        return spot_name_in_list(name, self.devices, "device")
