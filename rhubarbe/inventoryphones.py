"""
Parse inventory json about phones
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# r0903 too few public methods
# pylint: disable=r0903

import json

from rhubarbe.singleton import Singleton
from rhubarbe.config import Config


class InventoryPhones(metaclass=Singleton):
    """
    A class for loading and storing the phones specifications
    typically from /etc/rhubarbe/inventory-phones.json
    """

    def __init__(self):
        conf = Config()
        try:
            with open(conf.value('testbed', 'inventory_phones_path')) as feed:
                self._phones = json.load(feed)
        except FileNotFoundError:
            self._phones = []

    def all_phones(self):
        """
        For now just return the json contents as is
        """
        return self._phones
