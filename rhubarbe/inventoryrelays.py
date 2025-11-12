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

import pandas as pd

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
        with (folder / "temperatures.csv").open('a') as writer:
            print(f"{now},{self.host},{temperature}", file=writer)


    def load_past_data(self, *, duration=None, resample_period=None):
        folder = Path(Config().value('testbed', 'relays_database_folder'))
        try:
            df = pd.read_csv(
                Path(folder) / f"{self.host}.csv",
                names=['timestamp', self.host],
                parse_dates=['timestamp'],
                index_col='timestamp'
            )
            if duration is not None:
                time_threshold = DateTime.now() - duration
                df = df[df.index >= time_threshold]
            if resample_period is not None:
                df = df.resample(resample_period).mean()
            return df
        except FileNotFoundError:
            logger.warning(f"no past data for relay {self.host}")
            return pd.DataFrame()

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

    def load_past_data(self, *, duration=None, resample_period=None):
        pieces = []
        for relay in self.relays:
            piece = relay.load_past_data(
                duration=duration,
                resample_period=resample_period
            )
            piece['relay'] = relay.host
            pieces.append(piece)
        return pd.concat(pieces, axis=0)
