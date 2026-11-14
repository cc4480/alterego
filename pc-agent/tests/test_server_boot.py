"""Boot regression test for server.py (added 2026-11-14).

Fix #2 registered the anyio thread-limiter raise through
``app.router.on_startup`` — an attribute Starlette 1.x removed —
and the bridge crash-looped on every boot while the unit suite
stayed green, because no test ever built or served the app.
This test builds the app exactly as main() does and serves it
through ``server._serve`` on a scratch port, then checks
/health. It skips where the MCP SDK is not installed (the VM
suite env); it runs in the PC venv, which is where it matters.
"""
import asyncio
import json
import threading
import urllib.request

import pytest

server = pytest.importorskip(
    "server", reason="MCP SDK not installed in this environment")
pytest.importorskip("mcp")


def _build_app():
    from starlette.routing import Route, WebSocketRoute
    app = server.mcp.streamable_http_app(
        transport_security=server.TransportSecuritySettings(
            enable_dns_rebinding_protection=False))
    app.routes.append(Route("/health", server._health))
    app.routes.append(Route("/chat", server._chat_page))
    app.routes.append(Route("/pair", server._pair, methods=["POST"]))
    app.routes.append(Route("/logout", server._logout, methods=["POST"]))
    app.routes.append(Route("/revoke", server._revoke, methods=["POST"]))
    app.routes.append(WebSocketRoute("/ws", server._ws_relay))
    app.add_middleware(server.BearerAuth)
    return app


def test_app_builds_without_router_on_startup():
    app = _build_app()
    assert app is not None
    # The 2026-11-14 crash: main() may not touch Router.on_startup.
    import inspect
    assert ".on_startup" not in inspect.getsource(server.main)


def test_serve_raises_thread_limiter_and_answers_health():
    server.PORT = 18771
    app = _build_app()
    thread = threading.Thread(
        target=lambda: asyncio.run(server._serve(app)), daemon=True)
    thread.start()
    health = None
    for _ in range(40):
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:18771/health", timeout=2) as resp:
                health = json.loads(resp.read())
            break
        except Exception:
            threading.Event().wait(0.25)
    assert health is not None, "server did not answer /health"
    assert health["ok"] is True
    assert health["thread_tokens"] == server._THREAD_TOKENS_TARGET
    assert health["tools"]["max_inflight"] == 160
