import logging
import logging.config

# we essentially need
# * one all-purpose logger that goes into ./rhubarbe.log
# * one special logger for monitor that goes into /var/log/monitor.log
# * one special logger for accounts that goes into /var/log/accounts.log

#import os
# os.getlogin() is unreliable
# so instead let's see if we can write in /var/log
try:
    monitor_output = '/var/log/monitor.log'
    with open(monitor_output, 'a') as f:
        pass
except:
    monitor_output = 'monitor.log'

try:
    accounts_output = '/var/log/accounts.log'
    with open(accounts_output, 'a') as f:
        pass
except:
    accounts_output = 'accounts.log'

rhubarbe_logging_config = {
    'version' : 1,
    'disable_existing_loggers' : True,
    'formatters': { 
        'standard': { 
            'format': '%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(message)s',
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
            'filename' : 'rhubarbe.log',
        },
        'monitor': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'formatter': 'standard',
            'filename' : monitor_output,
        },
        'accounts': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'formatter': 'shorter',
            'filename' : accounts_output,
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

#################### test
if __name__ == '__main__':
    logger.info("in rhubarbe")
    monitor_logger.info("in monitor")

    
