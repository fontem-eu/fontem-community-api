"""Tests for ClaudeProxyClient's line-to-block buffering.

httpx.aiter_lines yields one line at a time. The old implementation
re-emitted each line as its own SSE "event" which silently broke the
downstream parser (which requires whole event blocks). These tests
pin the new contract: yield one complete block per iteration.
"""
# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access
from __future__ import annotations

import pytest

from src.assistant.proxy_client import ClaudeProxyClient


class _FakeResponse:
    """Mimics httpx.Response's aiter_lines for tests."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


class _FakeStreamCtx:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return _FakeResponse(self._lines)

    async def __aexit__(self, *a):
        return None


class _FakeAsyncClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def stream(self, *a, **kw):
        return _FakeStreamCtx(self._lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


def _patch_httpx(monkeypatch, lines):
    def fake_async_client(**_kw):
        return _FakeAsyncClient(lines)
    monkeypatch.setattr("src.assistant.proxy_client.httpx.AsyncClient", fake_async_client)


@pytest.mark.asyncio
async def test_yields_one_block_per_event(monkeypatch):
    # Two events, each a 3-line SSE block separated by a blank line
    lines = [
        "event: chunk",
        'data: {"text": "Hello"}',
        "",
        "event: chunk",
        'data: {"text": "world"}',
        "",
    ]
    _patch_httpx(monkeypatch, lines)

    client = ClaudeProxyClient(url="http://fake")
    out = [block async for block in client.stream({"message": "hi", "system": "test"})]

    assert len(out) == 2
    assert out[0] == 'event: chunk\ndata: {"text": "Hello"}\n\n'
    assert out[1] == 'event: chunk\ndata: {"text": "world"}\n\n'


@pytest.mark.asyncio
async def test_ignores_keepalive_comment_lines(monkeypatch):
    lines = [
        ":heartbeat",
        "event: chunk",
        'data: {"text": "x"}',
        "",
    ]
    _patch_httpx(monkeypatch, lines)

    client = ClaudeProxyClient(url="http://fake")
    out = [block async for block in client.stream({"message": "hi", "system": ""})]
    assert len(out) == 1
    assert ":heartbeat" not in out[0]


@pytest.mark.asyncio
async def test_flushes_trailing_block_without_blank_line(monkeypatch):
    # Some proxies don't terminate the last event with a blank line
    lines = [
        "event: chunk",
        'data: {"text": "final"}',
    ]
    _patch_httpx(monkeypatch, lines)

    client = ClaudeProxyClient(url="http://fake")
    out = [block async for block in client.stream({"message": "hi", "system": ""})]
    assert len(out) == 1
    assert '"final"' in out[0]


@pytest.mark.asyncio
async def test_multiple_events_including_status(monkeypatch):
    lines = [
        "event: status",
        'data: {"phase": "connecting"}',
        "",
        "event: status",
        'data: {"phase": "tool_use", "tool": "search_entities"}',
        "",
        "event: chunk",
        'data: {"text": "Here is your answer"}',
        "",
        "event: done",
        'data: {"done": true}',
        "",
    ]
    _patch_httpx(monkeypatch, lines)

    client = ClaudeProxyClient(url="http://fake")
    out = [block async for block in client.stream({"message": "q", "system": "s"})]

    assert len(out) == 4
    assert "connecting" in out[0]
    assert "tool_use" in out[1]
    assert "Here is your answer" in out[2]
    assert '"done": true' in out[3]
