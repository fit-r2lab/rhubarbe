import asyncio
import aiohttp
import traceback
import ssl
import json

# Use the global rhubarbe logger when that makes sense
try:
    from .logger import logger
except:
    import logging
    logger = logging.getLogger("omfsfaproxy")

debug = False
debug = True

####################
# will move into a separate module
class OmfSfaProxy:
    def __init__(self, hostname, port,
                 user_cert_filename, private_key_filename,
                 unique_component_name,
                 loop = None):
        """
        hostname / port : location of the omf-sfa REST interface
        user_cert_filename : file containing the user certificate 
          typically "~/.omf/user_cert.pem"
        private_key_filename : filename, or None if private key is in user_cert
          typically "~/.ssh/id_rsa" except for root
        unique_component_name : the name of the unique node (e.g. '37nodes')
        """
        self.hostname = hostname
        self.port = port
        self.user_cert_filename = user_cert_filename
        self.private_key_filename = private_key_filename
        self.unique_component_name = unique_component_name
        self.unique_component_uuid = None
        self.loop = loop if loop is not None else asyncio.get_event_loop()

    def __repr__(self):
        return "omf_sfa://{}:{}/".format(self.hostname, self.port)

    #################### talking to the REST API
    def ssl_context(self, anonymous):
        """
        return an SSL context
        anonymous is a boolean, if True no certificate/key
        is used (typically for GET requests)
        """
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        context.verify_mode = ssl.CERT_NONE
        context.check_hostname = False
        if not anonymous:
            cert = self.user_cert_filename
            # passing None to load_cert_chain means the private key should be in certificate
            keyfile = self.private_key_filename if self.private_key_filename else None
            if debug:
                logger.info("Using cert={}, keyfile={}".format(cert, keyfile))
            try:
                context.load_cert_chain(cert, keyfile)
            except FileNotFoundError:
                if keyfile is None:
                    raise FileNotFoundError("{} could not be found".format(cert))
                else:
                    raise FileNotFoundError("One of {} and {} could not be found".format(cert, keyfile))
        #if debug: print('SSL context stats', context.cert_store_stats())
        return context

    def get_cert_connector(self):
        if not hasattr(self, 'cert_connector'):
            context = self.ssl_context(anonymous=False)
            self.cert_connector = aiohttp.TCPConnector(ssl_context = context,
                                                       loop = self.loop)
        return self.cert_connector

    def get_anonymous_connector(self):
        if not hasattr(self, 'anonymous_connector'):
            context = self.ssl_context(anonymous=True)
            self.anonymous_connector = aiohttp.TCPConnector(ssl_context = context,
                                                            loop = self.loop)
        return self.anonymous_connector

    def _url(self, rest_qualifier):
        return "https://{}:{}/resources/{}"\
            .format(self.hostname, self.port, rest_qualifier)
    
    async def REST_as_json(self, rest_qualifier, http_verb, request):
        """
        connects to https://hostname:port/resources/<rest_qualifier> (rest_qualifier typically is 'leases')
        using http_verb (GET/POST/PUT/DELETE)
        and sending 'request' encoded in json (unless it's None, in which case no data is passed)
        """

        headers = {
            'Accept' : 'application/json',
            'Content-Type' : 'application/json'
            }
        try:
            lverb = http_verb.lower()
            coro = getattr(aiohttp, lverb)
            url = self._url(rest_qualifier)
            # setting this to None - for GET essentially
            data = None if not request else json.dumps(request)
    
            # patch : until we reconfigure omf_sfa so that can use the cert and keys
            # so that at least we can issue GET requests
            connector = self.get_anonymous_connector() \
                        if lverb == 'get' else self.get_cert_connector()

            if debug:
                logger.info("Sending verb {} to {}".format(lverb, url))
            response = await coro(url, connector=connector, data=data, headers=headers)
            text = await response.text()
            return text
        except Exception as e:
            logger.exception("***** exception in REST_as_json")
            return json.dumps(str(e))
        
# original recipe was relying on curl
#        curl = [ 'curl', '--silent', '-k' ]
#        curl += [ '--cert', os.path.expanduser("~/.omf/user_cert.pem") ]
#        if self.login != 'root':
#            curl += [ '--key', os.path.expanduser("~/.ssh/id_rsa") ]
#        curl += [ '-H', "Accept: application/json" ]
#        curl += [ '-H', "Content-Type: application/json" ]
#        curl += [ '-X', verb ]
#        curl += [ '-d', json_request ]
#        curl += [ '-i', url ]

    async def _fetch_node_uuid(self):
        self.unique_component_uuid = None
        try:
            logger.info("for global uuid: fetching node {}".format(self.unique_component_name))
            rest_qualifier = "nodes?name={}".format(self.unique_component_name)
            text = await self.REST_as_json(rest_qualifier, 'GET', None)
            omf_sfa_answer = json.loads(text)
            logger.info("Node received")
            r = omf_sfa_answer['resource_response']['resource']
            self.unique_component_uuid = r['uuid']
            logger.info("{} has uuid {}".format(self.unique_component_name,
                                                self.unique_component_uuid))
                
        except Exception as e:
            if debug: print("Nodes.fetch: exception {}".format(e))
            logger.exception("cannot get unique_component_uuid from {}".format(self))
        
    async def fetch_node_uuid(self):
        if self.unique_component_uuid:
            return self.unique_component_uuid
        await self._fetch_node_uuid()
        return self.unique_component_uuid

    @staticmethod
    def is_accepted_lease(omf_lease):
        """
        expects a JSON resources description as produced by omf_sfa

        returns a bool that says whether it should be considered
        this is because when an attempt is made to reserve a lease that is 
        already booked otherwise, a zombie lease (with no component) is 
        created instead
        this lease is also marked as 'pending' but we cannot use that 
        status because it will become 'active' over its timespan
        This just sounds like a design flaw
        In any case, as far as we are concerned, it all boils down to checking 
        that the lease as a non-empty list of components
        """
        return len(omf_lease['components']) > 0


