# this class allows to create an authenticated xmlrpc
# connection to 

import getpass

import ssl

#from aioxmlrpc.client import ServerProxy
from xmlrpc.client import ServerProxy

class PlcApiProxy(ServerProxy):

    # use the standard plcapi scheme to run on /PLCAPI/
    # always use password auth in this first rough version
    # could be improved by using session authentication
    def __init__(self, url, email=None, password=None, debug=False):
        self.url = url
        self.email = email
        self.password = password
        self.debug = debug
        ###
        context = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
        context.check_hostname = False
        ServerProxy.__init__(
            self, self.url, allow_none=True, context=context
        )
        
    def __auth__(self, anonymous):
        if anonymous:
            return { 'AuthMethod' : 'anonymous' }
        else:
            if not self.email:
                self.email = input("Enter plcapi email (login) : ")
            if not self.password:
                self.password = getpass.getpass("Enter plcapi password for {} : "
                                                .format(self.email))
            return { 'AuthMethod' : 'password',
                     'Username'   : self.email,
                     'AuthString' : self.password }
        

    # pass the authentication along for all calls
    def __getattr__(self, attr):
        # the default is to use authenticated calls
        # because this is tha majority of the plcapi calls
        def fun(*args, anonymous=False, **kwds):
            if self.debug:
                auth_msg = "[auth]" if not anonymous else "[anon]"
                print("-> Sending {} {} on {} with args={} and kwds={}"
                      .format(auth_msg, attr, self, args, kwds))
            actual_fun = ServerProxy.__getattr__(
                self, attr)
            try:
                retcod = actual_fun(self.__auth__(anonymous), *args, **kwds)
                if self.debug:
                    print("<- Received {}".format(retcod))
                return retcod
            except Exception as e:
                print("ignored exception in {} : {}"
                          .format(attr, e))
        return fun

    def __str__(self):
        return "PLCAPIproxy@{}".format(self.url)
