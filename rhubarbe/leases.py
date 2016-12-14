from .config import Config

use_plc = Config().value('authorization', 'leases_api') == 'plcapi'

if use_plc:
    from .leasesplc import Leases, Lease
else:
    from .leasesomf import Leases, Lease
