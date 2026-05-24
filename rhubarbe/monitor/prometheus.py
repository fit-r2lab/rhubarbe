# pylint: disable=missing-function-docstring, logging-fstring-interpolation

from prometheus_client import Gauge, start_http_server

from rhubarbe.singleton import Singleton
from rhubarbe.logger import monitor_logger as logger

_STATUS = {'on': 1.0, 'off': 0.0, 'fail': -1.0, 'unknown': float('nan')}

_NODE_FIELDS = ('cmc_on_off', 'usrp_on_off', 'control_ssh', 'control_ping')


class PrometheusExporter(metaclass=Singleton):

    def __init__(self):
        self._started = False
        self._node_gauges = {
            field: Gauge(
                f'rhubarbe_node_{field}',
                f'node {field} — 1=on 0=off -1=fail',
                ['node'])
            for field in _NODE_FIELDS
        }
        self._pdu_gauge = Gauge(
            'rhubarbe_pdu_on_off',
            'PDU power status — 1=on 0=off',
            ['pdu'])
        self._phone_gauge = Gauge(
            'rhubarbe_phone_airplane_mode',
            'phone airplane mode — 1=on 0=off -1=fail',
            ['phone'])

    def start(self, port):
        if self._started or not port:
            return
        start_http_server(int(port))
        self._started = True
        logger.info(f"Prometheus exporter listening on :{port}")

    def update(self, category, infos):
        if not self._started:
            return
        dispatch = {
            'nodes': self._update_nodes,
            'pdus': self._update_pdus,
            'phones': self._update_phones,
        }
        handler = dispatch.get(category)
        if handler:
            handler(infos)

    def _update_nodes(self, infos):
        for info in infos:
            node = info.get('id', '')
            for field, gauge in self._node_gauges.items():
                if field in info:
                    gauge.labels(node=node).set(_STATUS.get(info[field], float('nan')))

    def _update_pdus(self, infos):
        for info in infos:
            pdu = info.get('id', '')
            if 'on_off' in info:
                self._pdu_gauge.labels(pdu=pdu).set(
                    _STATUS.get(info['on_off'], float('nan')))

    def _update_phones(self, infos):
        for info in infos:
            phone = info.get('id', '')
            if 'airplane_mode' in info:
                self._phone_gauge.labels(phone=phone).set(
                    _STATUS.get(info['airplane_mode'], float('nan')))
