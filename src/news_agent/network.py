from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx


class SourceRateLimited(ValueError):
    def __init__(self, retry_after: str | None):
        try:
            seconds = int(retry_after or "600")
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after or "")
                seconds = int((date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                seconds = 600
        self.seconds = max(60, min(seconds, 7 * 86400))
        super().__init__(f"来源限流，将至少等待 {self.seconds} 秒后重试")


def validate_public_url(url: str, *, resolve_dns: bool = True) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("只允许公开 http(s) 地址")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("不允许访问非标准端口")
    hostname = parsed.hostname.lower().rstrip(".")
    if "." not in hostname or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("不允许访问本机或私有网络域名")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ValueError("不允许访问本机或私有网络地址")
        return
    # With an explicitly configured HTTP proxy, the proxy resolves hostnames, not this machine.
    if not resolve_dns:
        return
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError(
            "域名解析为私有/保留地址；如使用 Fake-IP 代理，请配置 NEWS_BRIEF_HTTP_PROXY 使用代理端 DNS"
        )


def fetch_bytes(client: httpx.Client, url: str, *, max_bytes: int = 3_000_000) -> tuple[bytes, str]:
    for _ in range(6):
        validate_public_url(url, resolve_dns=not getattr(client, "remote_dns", False))
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("重定向缺少目标地址")
                url = str(response.url.join(location))
                continue
            if response.status_code == 429:
                raise SourceRateLimited(response.headers.get("retry-after"))
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ValueError("响应内容超过大小限制")
            return bytes(content), str(response.url)
    raise ValueError("重定向次数过多")


class NewsHTTPClient(httpx.Client):
    remote_dns: bool = False


def http_client(proxy: str | None = None) -> httpx.Client:
    client = NewsHTTPClient(
        proxy=proxy,
        timeout=httpx.Timeout(20, connect=8),
        trust_env=False,
        headers={"User-Agent": "NewsBrief/0.1 (+self-hosted news reader)", "Accept": "*/*"},
    )
    client.remote_dns = bool(proxy)
    return client
