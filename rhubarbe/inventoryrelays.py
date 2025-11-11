"""
the class that model a relay box
the entry point for acquiring temperatures
"""

# pylint: disable=fixme, unspecified-encoding

from math import nan
from dataclasses import dataclass
from datetime import datetime as DateTime
from pathlib import Path
import asyncio

from importlib import resources
from dataclass_wizard import YAMLWizard
import asyncssh

from .config import Config
from .logger import logger

VERBOSE = False
# VERBOSE = True

# pylint: disable=missing-function-docstring, missing-class-docstring
def verbose(*args, **kwds):
    if not VERBOSE:
        return
    print(*args, **kwds)



@dataclass
class Relay:

    host: str
    # IP: str                                     # pylint: disable=invalid-name

    async def get_temperature(self):
        async with asyncssh.connect(self.host) as conn:
            result = await conn.run('/usr/bin/vcgencmd measure_temp', check=True)
        raw1 = result.stdout
        raw2 = raw1.split('=')[1]
        raw3 = raw2.split("'")[0]
        return float(raw3)

    def store_current_temperature(self, temperature):
        now = DateTime.now().replace(microsecond=0).isoformat()
        # temperature = asyncio.run(self.get_temperature())
        folder = Path(Config().value('testbed', 'relays_database_folder'))
        if not folder.is_dir():
            print(f"Creating folder {folder}")
            folder.mkdir(parents=True, exist_ok=True)
        with (Path(folder) / f"{self.host}.csv").open('a') as writer:
            print(f"{now},{temperature}", file=writer)

@dataclass
class InventoryRelays(YAMLWizard):

    relays: list[Relay]

    @staticmethod
    def load() -> "InventoryRelays":
        the_config = Config()
        yaml_path = the_config.value('testbed', 'inventory_relays_path')
        try:
            with open(yaml_path) as feed:
                return InventoryRelays.from_yaml(feed.read())
        except FileNotFoundError:
            # not all deployments have relays
            logger.warning(f"file not found {yaml_path}")
            return InventoryRelays([])
        except KeyError as exc:
            print(f"something wrong in config file {yaml_path}, {exc}")
            raise


    def get_temperatures(self, *, mode):
        """
        mode is expected to be either 'print' or 'save'
        """
        async def run_all():
            return await asyncio.gather(
                *(relay.get_temperature() for relay in self.relays)
            )

        temperatures = asyncio.run(run_all())
        match mode:
            case 'print':
                for relay, temperature in zip(self.relays, temperatures):
                    print(f"{relay} has temperature {temperature:.2f}C")
            case 'store':
                for relay, temperature in zip(self.relays, temperatures):
                    relay.store_current_temperature(temperature)

    def __iter__(self):
        return iter(self.relays)
    def __len__(self):
        return len(self.relays)
