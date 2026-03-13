"""
REST client for the r2labapi service.
Replaces the XMLRPC-based PlcApiProxy.
"""

# c0111 no docstrings yet
# w1202 logger & format
# pylint: disable=c0111, w1202

import os
import getpass
from datetime import datetime, timezone

import requests

from .logger import logger


def iso_to_epoch(iso_string):
    """Convert ISO 8601 datetime string to Unix epoch."""
    # handle Z suffix for Python 3.10 compat
    if iso_string.endswith('Z'):
        iso_string = iso_string[:-1] + '+00:00'
    return datetime.fromisoformat(iso_string).timestamp()


def epoch_to_iso(epoch):
    """Convert Unix epoch to ISO 8601 UTC datetime string."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class R2labApiProxy:
    """
    A thin REST client for the r2labapi service.

    Two authentication modes:
    - pre-shared admin token: R2labApiProxy(url, admin_token="...")
    - interactive login: R2labApiProxy(url) then write operations
      will prompt for email/password (or read R2LABAPI_EMAIL /
      R2LABAPI_PASSWORD env vars)
    """

    def __init__(self, url, admin_token=None):
        self.url = url.rstrip('/')
        self.session = requests.Session()
        self.session.headers['Accept'] = 'application/json'
        if admin_token:
            self.session.headers['Authorization'] = \
                f'Bearer {admin_token}'

    @property
    def is_authenticated(self):
        return 'Authorization' in self.session.headers

    def login(self, email, password):
        """Authenticate with email/password, store JWT."""
        response = self.session.post(
            f'{self.url}/auth/login',
            json={'email': email, 'password': password})
        response.raise_for_status()
        token = response.json()['access_token']
        self.session.headers['Authorization'] = f'Bearer {token}'

    def ensure_authenticated(self):
        """Prompt for credentials if not already authenticated."""
        if self.is_authenticated:
            return
        email = (os.environ.get("R2LABAPI_EMAIL")
                 or input("Enter r2labapi email (login): "))
        password = (os.environ.get("R2LABAPI_PASSWORD")
                    or getpass.getpass(
                        f"Enter r2labapi password for {email}: "))
        self.login(email, password)

    # ---- low-level HTTP verbs ----

    def _get(self, path, params=None):
        url = f'{self.url}{path}'
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, path, json=None):
        self.ensure_authenticated()
        url = f'{self.url}{path}'
        response = self.session.post(url, json=json)
        response.raise_for_status()
        if response.status_code == 204:
            return None
        return response.json()

    def _patch(self, path, json=None):
        self.ensure_authenticated()
        url = f'{self.url}{path}'
        response = self.session.patch(url, json=json)
        response.raise_for_status()
        return response.json()

    def _delete(self, path):
        self.ensure_authenticated()
        url = f'{self.url}{path}'
        response = self.session.delete(url)
        response.raise_for_status()

    # ---- slices ----

    def get_slices(self):
        """
        GET /slices
        Returns list of SliceRead:
          {id, name, family, member_ids, ...}
        """
        return self._get('/slices')

    def get_slice_keys(self, slicename):
        """
        GET /slices/by-name/{name}/keys
        Returns list of SSHKeyRead:
          {id, key, comment, created_at}
        """
        return self._get(f'/slices/by-name/{slicename}/keys')

    # ---- leases ----

    def get_leases(self, **params):
        """
        GET /leases
        Optional filters: resource_id, slice_id, alive (epoch),
                          after (ISO), before (ISO)
        Returns list of LeaseRead:
          {id, resource_id, slice_id, t_from, t_until, slice_name}
        """
        return self._get('/leases', params=params or None)

    def get_current_leases(self):
        """Convenience: get leases alive right now."""
        import time
        now = int(time.time())
        return self._get('/leases', params={'alive': now})

    def create_lease(self, body):
        """
        POST /leases
        body: {resource_name, slice_name, t_from (ISO), t_until (ISO)}
        Returns LeaseRead
        """
        return self._post('/leases', json=body)

    def update_lease(self, lease_id, body):
        """
        PATCH /leases/{lease_id}
        body: {t_from? (ISO), t_until? (ISO)}
        Returns LeaseRead
        """
        return self._patch(f'/leases/{lease_id}', json=body)

    def delete_lease(self, lease_id):
        """DELETE /leases/{lease_id}"""
        self._delete(f'/leases/{lease_id}')

    # ---- resources ----

    def get_resource_by_name(self, name):
        """
        GET /resources/by-name/{name}
        Returns ResourceRead: {id, name, granularity}
        """
        return self._get(f'/resources/by-name/{name}')

    def __str__(self):
        return f"R2labApiProxy@{self.url}"
