"""
the availability of nodes, as kept in r2labapi: what the commands
`rhubarbe available` and its verbs do
"""

# c0111 no docstrings yet
# pylint: disable=c0111

import os
import sys
from datetime import datetime, timezone

import requests

from .config import Config, ConfigException
from .r2labapiproxy import R2labApiProxy


def iso_to_text(iso):
    "2026-11-10T04:12:00.123456 -> 2026-11-10 04:12 UTC; '' if unknown"
    if not iso:
        return ""
    try:
        date = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except ValueError:
        return iso
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def explain(exc):
    "turn a requests exception into a short message"
    response = getattr(exc, 'response', None)
    if response is not None:
        try:
            detail = response.json().get('detail', '')
        except ValueError:
            detail = ''
        if isinstance(detail, list):
            # validation errors
            detail = "; ".join(str(item.get('msg', item)) for item in detail)
        if response.status_code in (401, 403):
            return f"not allowed ({response.status_code}) {detail}".strip()
        return f"HTTP error {response.status_code} {detail}".strip()
    return f"cannot reach r2labapi: {exc}"


class Availability:
    """
    reading needs no credentials; writing needs an admin of r2labapi:
    when running as root we use the admin_token of the config if there is
    one; other users, or root without a token, give their own email and
    password, with -e and -p, or the environment variables
    R2LABAPI_EMAIL and R2LABAPI_PASSWORD, or else we prompt for them
    """

    def __init__(self, email=None, password=None):
        the_config = Config()
        api_url = the_config.value('r2labapi', 'url')
        token = None
        # the token is for root only; everybody else logs in
        if os.geteuid() == 0:
            try:
                token = the_config.value('r2labapi', 'admin_token')
            except ConfigException:
                pass
        self.regularname = the_config.value('testbed', 'regularname')
        self.proxy = R2labApiProxy(api_url, None if email else token)
        if email and password:
            self.proxy.login(email, password)

    def name(self, node_id):
        return f"{self.regularname}{node_id:02}"

    @staticmethod
    def line(name, record):
        text = f"{name}  {record['status']}"
        if record['status'] == 'ko':
            since = iso_to_text(record.get('since'))
            if since:
                text += f"  since {since}"
            if record.get('reason'):
                text += f"  - {record['reason']}"
        return text

    def show(self, node_ids):
        "the status of these nodes, whatever it is; returns the exit code"
        try:
            known = {record['node_id']: record for record in
                     self.proxy.get_node_availabilities()}
        except requests.exceptions.RequestException as exc:
            print(explain(exc), file=sys.stderr)
            return 1
        for node_id in node_ids:
            print(self.line(self.name(node_id),
                            known.get(node_id, {'status': 'ok'})))
        return 0

    def history(self, node_ids, limit):
        code = 0
        for node_id in node_ids:
            try:
                events = self.proxy.get_node_availability_history(
                    node_id, limit)
            except requests.exceptions.RequestException as exc:
                print(f"{self.name(node_id)}: {explain(exc)}",
                      file=sys.stderr)
                code = 1
                continue
            if not events:
                print(f"{self.name(node_id)}: no change recorded")
            for event in events:
                text = (f"{self.name(node_id)}  "
                        f"{iso_to_text(event['at'])}  {event['status']}")
                if event.get('reason'):
                    text += f"  - {event['reason']}"
                if event.get('set_by'):
                    text += f"  (by {event['set_by']})"
                print(text)
        return code

    def set(self, node_ids, status, reason=None):
        """
        mark the nodes ok or ko; returns the exit code, 1 if
        some node could not be changed
        """
        # authenticate once, and not once per node
        try:
            self.proxy.ensure_authenticated()
        except requests.exceptions.RequestException as exc:
            print(explain(exc), file=sys.stderr)
            return 1
        code = 0
        for node_id in node_ids:
            try:
                record = self.proxy.set_node_availability(
                    node_id, status, reason)
            except requests.exceptions.RequestException as exc:
                print(f"{self.name(node_id)}: {explain(exc)}",
                      file=sys.stderr)
                code = 1
                response = getattr(exc, 'response', None)
                if response is not None and response.status_code in (401, 403):
                    # no point in trying the other ones
                    break
                continue
            print(self.line(self.name(node_id), record))
        return code
