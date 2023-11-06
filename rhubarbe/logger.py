"""
create/configure logger objects
"""

# c0111 no docstrings yet
# c0103 constants names should be UPPER_CASE
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# r0903 too few public methods
# pylint: disable=c0103, w0703

import sys
from pathlib import Path

import logging
import logging.config
from pathlib import Path

# with systemd it's sooo simpler to log on stdout, that gets managed by journal
# so, we essentially need
# * one all-purpose logger that goes into $HOME/rhubarbe.log
# * one special logger for monitor*s that goes onto stdout -> journal
# * one special logger for accounts - ditto but with a shorter layout

rhubarbe_logging_config = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        'standard': {
            'format': '%(asctime)s %(levelname)s '
                      '%(filename)s:%(lineno)d %(message)s',
            'datefmt': '%m-%d %H:%M:%S'
        },
        'shorter': {
            'format': '%(asctime)s %(levelname)s %(message)s',
            'datefmt': '%d %H:%M:%S'
        },
    },
    'handlers': {
        'rhubarbe': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'formatter': 'standard',
            'filename': f'{str(Path.home())}/rhubarbe.log',
        },
        'monitor': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
            'stream': sys.stdout,
        },
        'accounts': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'shorter',
            'stream': sys.stdout,
        },
    },
    'loggers': {
        'monitor': {
            'handlers': ['monitor'],
            'level': 'INFO',
            'propagate': False,
        },
        'accounts': {
            'handlers': ['accounts'],
            'level': 'INFO',
            'propagate': False,
        },
        'rhubarbe': {
            'handlers': ['rhubarbe'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

logging.config.dictConfig(rhubarbe_logging_config)

# general case:
# from rhubarbe.logger import logger
logger = logging.getLogger('rhubarbe')

# monitor
# from rhubarbe.logger import monitor_logger as logger
monitor_logger = logging.getLogger('monitor')

# accounts
# from rhubarbe.logger import accounts_logger as logger
accounts_logger = logging.getLogger('accounts')

####################
# test
if __name__ == '__main__':
    logger.info("in rhubarbe")
    monitor_logger.info("in monitor")
