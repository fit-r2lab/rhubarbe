"""
The PlcApiProxy class allows to create an authenticated xmlrpc
connection to a myplc server; typically r2labapi.inria.fr
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0703, w1202

import getpass

import ssl

# from aioxmlrpc.client import ServerProxy
from xmlrpc.client import ServerProxy


class PlcApiProxy(ServerProxy):                         # pylint: disable=r0903

    # use the standard plcapi scheme to run on /PLCAPI/
    # always use password auth in this first rough version
    # could be improved by using session authentication
    def __init__(self, url, email=None, password=None, debug=False):
        self.url = url
        self.email = email
        self.password = password
        self.debug = debug
        ###
        context = ssl.SSLContext()
        context.check_hostname = False
        ServerProxy.__init__(
            self, self.url, allow_none=True, context=context
        )

    def __auth__(self, anonymous):
        if anonymous:
            return {'AuthMethod': 'anonymous'}
        else:
            if not self.email:
                self.email = input("Enter plcapi email (login) : ")
            if not self.password:
                self.password = getpass.getpass(
                    f"Enter plcapi password for {self.email} : ")
            return {'AuthMethod': 'password',
                    'Username': self.email,
                    'AuthString': self.password}

    def __getattr__(self, attr):
        """
        pass the authentication along for all calls
        """
        # the default is to use authenticated calls
        # because this is the majority of the plcapi calls
        def fun(*args, anonymous=False, **kwds):
            if self.debug:
                auth_msg = "[auth]" if not anonymous else "[anon]"
                print(f"-> Sending {auth_msg} {attr} on {self} "
                      f"with args={args} and kwds={kwds}")
            actual_fun = ServerProxy.__getattr__(
                self, attr)
            try:
                retcod = actual_fun(self.__auth__(anonymous), *args, **kwds)
                if self.debug:
                    print(f"<- Received {retcod}")
                return retcod
            except Exception as exc:
                print(f"ignored exception in {attr} : {exc}")
        return fun

    def __str__(self):
        return f"PLCAPIproxy@{self.url}"
