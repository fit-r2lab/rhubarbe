"""
Parse inventory yaml about phones
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# r0903 too few public methods
# pylint: disable=r0903

import yaml

from rhubarbe.singleton import Singleton
from rhubarbe.config import Config


class InventoryPhones(metaclass=Singleton):
    """
    A class for loading and storing the phones specifications
    typically from /etc/rhubarbe/inventory-phones.yaml
    """

    def __init__(self):
        conf = Config()
        try:
            with open(conf.value('testbed', 'inventory_phones_path')) as feed:
                self._phones = yaml.safe_load(feed)['phones']
        except FileNotFoundError:
            self._phones = []

    def all_phones(self):
        """
        For now just return the yaml contents as is - a list of dicts
        """
        return self._phones
