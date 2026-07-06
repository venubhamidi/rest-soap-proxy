"""
Upstream SOAP authentication: build a Zeep client with the right transport and
WS-Security plugin for a service's declared auth mode.

Ported from ``soap_translator``:
- ``TimestampedUsernameToken`` (fresh WSU:Timestamp per request; zeep 4.3 fix).
- ``_build_transport_and_wsse`` (none / wsse_username / basic / client_cert),
  reworked to take the typed config models instead of a loose dict.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from requests import Session
from requests.auth import HTTPBasicAuth
from zeep import Client
from zeep.transports import Transport
from zeep.wsse import utils as wsse_utils
from zeep.wsse.username import UsernameToken

from .config import (
    BasicAuth,
    ClientCertAuth,
    ServiceConfig,
    Settings,
    WsseUsernameAuth,
)

logger = logging.getLogger(__name__)


class TimestampedUsernameToken(UsernameToken):
    """UsernameToken that generates a fresh WSU:Timestamp for each request."""

    def __init__(self, *args, add_timestamp=False, timestamp_ttl=300, **kwargs):
        super().__init__(*args, timestamp_token=None, **kwargs)
        self._add_timestamp = add_timestamp
        self._timestamp_ttl = timestamp_ttl

    def apply(self, envelope, headers):
        if self._add_timestamp:
            now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
            expires = now + datetime.timedelta(seconds=self._timestamp_ttl)
            ts = wsse_utils.WSU.Timestamp()
            ts.append(wsse_utils.WSU.Created(now.isoformat()))
            ts.append(wsse_utils.WSU.Expires(expires.isoformat()))
            self.timestamp_token = ts
        return super().apply(envelope, headers)


def _build_transport_and_wsse(
    svc_cfg: ServiceConfig, settings: Settings
) -> tuple[Transport, Optional[UsernameToken]]:
    """Build the Zeep Transport and optional WSSE plugin for a service."""
    session = Session()
    if svc_cfg.headers:
        session.headers.update(svc_cfg.headers)

    auth = svc_cfg.auth
    wsse_plugin: Optional[UsernameToken] = None

    if isinstance(auth, WsseUsernameAuth):
        wsse_plugin = TimestampedUsernameToken(
            username=auth.username,
            password=auth.password,
            use_digest=auth.use_digest,
            add_timestamp=auth.add_timestamp,
            timestamp_ttl=auth.timestamp_ttl,
        )
        logger.info(
            "Service '%s': WS-Security UsernameToken (digest=%s, timestamp=%s)",
            svc_cfg.name,
            auth.use_digest,
            auth.add_timestamp,
        )
    elif isinstance(auth, BasicAuth):
        session.auth = HTTPBasicAuth(auth.username, auth.password)
        logger.info("Service '%s': HTTP Basic Auth", svc_cfg.name)
    elif isinstance(auth, ClientCertAuth):
        if auth.key_path:
            session.cert = (auth.cert_path, auth.key_path)
        else:
            session.cert = auth.cert_path
        if auth.ca_bundle_path:
            session.verify = auth.ca_bundle_path
        logger.info("Service '%s': client certificate auth", svc_cfg.name)
    else:
        logger.info("Service '%s': no upstream auth", svc_cfg.name)

    transport = Transport(
        session=session,
        timeout=settings.wsdl_request_timeout,
        operation_timeout=settings.operation_timeout,
    )
    return transport, wsse_plugin


def build_client(svc_cfg: ServiceConfig, settings: Settings) -> Client:
    """Load a service's WSDL into a Zeep client with its auth configured."""
    transport, wsse_plugin = _build_transport_and_wsse(svc_cfg, settings)
    return Client(wsdl=svc_cfg.wsdl, transport=transport, wsse=wsse_plugin)
