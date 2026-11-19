from __future__ import annotations

import base64

from bidcraft import offline_cache
from bidcraft.client import BidCraftClient, ClientError


def test_cache_put_get_roundtrip(tmp_path):
    cache = offline_cache.OfflineCache(tmp_path)
    cache.put("https://s.example", "/projects?x=1", b'{"a":1}', "application/json")
    assert cache.get("https://s.example", "/projects?x=1") == (b'{"a":1}', "application/json")
    assert cache.saved_at("https://s.example", "/projects?x=1")
    assert cache.get("https://s.example", "/projects?x=2") is None
    assert cache.get("https://other.example", "/projects?x=1") is None


def test_cacheable_prefixes():
    assert offline_cache.OfflineCache.cacheable("/projects")
    assert offline_cache.OfflineCache.cacheable("/projects/p1/outline")
    assert offline_cache.OfflineCache.cacheable("/my/docs?x=1")
    assert not offline_cache.OfflineCache.cacheable("/admin/users")
    assert not offline_cache.OfflineCache.cacheable("/billing/summary")


def test_bridge_request_serves_cache_when_offline(tmp_path):
    client = BidCraftClient("https://s.example", home=tmp_path)
    cache = offline_cache.OfflineCache(tmp_path / "cache")
    cache.put(client.server, "/projects", b'[{"id":"p1"}]', "application/json")

    def boom(*_args, **_kwargs):
        raise ClientError("无法连接服务器")

    client._request = boom  # type: ignore[method-assign]
    result = client.bridge_request("GET", "/projects")
    assert result["status"] == 200
    assert result["offline"] is True
    assert base64.b64decode(result["body"]) == b'[{"id":"p1"}]'


def test_bridge_request_offline_without_cache(tmp_path):
    client = BidCraftClient("https://s.example", home=tmp_path)

    def boom(*_args, **_kwargs):
        raise ClientError("无法连接服务器")

    client._request = boom  # type: ignore[method-assign]
    result = client.bridge_request("GET", "/projects")
    assert result["status"] == 503
    assert result["offline"] is True
    assert "无法连接服务器" in base64.b64decode(result["body"]).decode()
