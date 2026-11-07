"""Outbound model policy for locally imported private messages."""
import ipaddress
import os
from urllib.parse import urlsplit

from runtime_config import ini_get_bool


def cloud_allowed(platform):
    if str(platform).strip().lower() != "wechat":
        return True
    value = os.environ.get("MOM_INDEX_WECHAT_ALLOW_CLOUD")
    if value is not None:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return ini_get_bool("wechat", "allow_cloud_analysis", False)


class PrivateEndpointError(ValueError):
    pass


def request_options(platform, base_url):
    if cloud_allowed(platform):
        return {}
    endpoint = urlsplit(base_url)
    host = endpoint.hostname or ""
    if host != "localhost":
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
    else:
        local = True
    if not local or endpoint.scheme not in {"http", "https"}:
        raise PrivateEndpointError("微信正文仅允许发送到本机模型；未外发")
    return {"allow_redirects": False, "proxies": {"http": "", "https": "", "all": ""}}
