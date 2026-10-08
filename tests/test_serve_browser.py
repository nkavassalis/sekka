"""The served page in a real browser, reached through a *different* port.

Regression cover for the bug where the page rendered nothing but Textual's logo
and the app name: textual-serve baked the websocket URL from the address the
server bound, so any player who did not type exactly that host and port (LAN
address, SSH tunnel, port-forward, reverse proxy) opened a socket to the wrong
place and the intro overlay never went away.

Connecting through a local port-forwarder is the cheapest honest stand-in for
"the URL in the page is not the URL the player used". Skipped when the serve
extra, Playwright or Chromium is missing.
"""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")
pytest.importorskip("textual_serve", reason="sekka[serve] extra not installed")

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"server never listened on {port}")


class Forwarder:
    """TCP forward src_port -> dst_port, so the page can be opened on another port."""

    def __init__(self, src: int, dst: int) -> None:
        self.src, self.dst = src, dst
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", src))
        self.sock.listen(8)
        self.alive = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _pipe(self, a: socket.socket, b: socket.socket) -> None:
        try:
            while self.alive:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (a, b):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def _run(self) -> None:
        while self.alive:
            try:
                client, _ = self.sock.accept()
                upstream = socket.create_connection(("127.0.0.1", self.dst))
            except OSError:
                return
            threading.Thread(target=self._pipe, args=(client, upstream), daemon=True).start()
            threading.Thread(target=self._pipe, args=(upstream, client), daemon=True).start()

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """A real `sekka serve`, plus a forwarder on a second port."""
    port, forwarded = free_port(), free_port()
    workdir = tmp_path_factory.mktemp("served")
    (workdir / ".sekka").mkdir()
    (workdir / ".sekka" / "config.json").write_text(json.dumps({
        "endpoint": "http://127.0.0.1:1/v1",     # nothing lives there; boot screen is enough
        "model": "test-model",
        "autosave": False,
    }))
    server = subprocess.Popen(
        [sys.executable, "-m", "sekka", "serve", "--serve-port", str(port)],
        cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    fwd = None
    try:
        wait_for_port(port)
        fwd = Forwarder(forwarded, port)
        yield f"http://127.0.0.1:{forwarded}"
    finally:
        if fwd:
            fwd.close()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chrome = p.chromium.launch()
        except Exception as exc:                      # no browser binary on this box
            pytest.skip(f"chromium not available: {exc}")
        try:
            yield chrome
        finally:
            chrome.close()


def page_state(page) -> dict:
    """Screen state, tolerant of a document that has not parsed yet (the inline
    styles are render-blocking, so `.textual-terminal` can be missing for a while)."""
    return page.evaluate("""() => {
      const term = document.querySelector('.textual-terminal');
      if (!term) return {ready: false, classes: '', wsUrl: null, introVisible: true, litPixels: 0,
                         inkFraction: 0, meanInkLuma: 0, roboto: false};
      const canvas = term.querySelector('canvas');
      let ink = 0, lum = 0, total = 0;
      if (canvas) {
        const d = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 0; i < d.length; i += 4) {
          const l = 0.2126*d[i] + 0.7152*d[i+1] + 0.0722*d[i+2];
          total++;
          if (l > 40) { ink++; lum += l; }
        }
      }
      return {
        ready: true,
        classes: document.body.className,
        wsUrl: term.dataset.sessionWebsocketUrl,
        introVisible: document.querySelector('.intro-dialog').getClientRects().length > 0,
        litPixels: ink,
        inkFraction: total ? ink / total : 0,
        meanInkLuma: ink ? Math.round(lum / ink) : 0,
        roboto: [...document.fonts].some(f => /Roboto Mono/i.test(f.family) && f.status === 'loaded'),
      };
    }""")


def first_page(page, url: str) -> dict:
    """Open the page and wait for the first terminal bytes to reach the screen."""
    page.goto(url, wait_until="commit")
    deadline = time.time() + 30
    while time.time() < deadline:
        state = page_state(page)
        if state["ready"] and "-first-byte" in state["classes"]:
            return state
        time.sleep(0.25)
    raise AssertionError(f"terminal never woke up: {page_state(page)}")


def test_page_assets_are_served_by_sekka(served):
    """The relative asset paths have to resolve through the forwarder too."""
    conn = http.client.HTTPConnection("127.0.0.1", int(served.rsplit(":", 1)[1]), timeout=10)
    conn.request("GET", "/")
    body = conn.getresponse().read().decode()
    assert 'src="static/js/textual.js"' in body          # relative, not a bind-address URL
    assert 'href="http' not in body and 'src="http' not in body   # nothing third-party loads
    conn.request("GET", "/static/js/textual.js")
    assert conn.getresponse().status == 200


def test_page_on_an_unexpected_port_still_connects_and_renders(served, browser):
    """The whole point: open on a port the server never bound and play anyway."""
    page = browser.new_page(viewport={"width": 1100, "height": 650})
    try:
        state = first_page(page, served)
        assert state["wsUrl"] == served.replace("http", "ws") + "/ws"
        assert not state["introVisible"]
        page.wait_for_timeout(2500)                    # let the boot screen paint
        assert page_state(page)["litPixels"] > 200
    finally:
        page.close()


def test_typing_through_the_forwarded_page_reaches_the_app(served, browser):
    """/play is answered by the app itself, so it proves the socket is live both ways."""
    page = browser.new_page(viewport={"width": 1100, "height": 650})
    try:
        first_page(page, served)
        page.keyboard.type("/play")
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
        state = page_state(page)
        assert state["ready"] and state["litPixels"] > 200
        assert "-closed" not in state["classes"]
    finally:
        page.close()


def test_served_screen_uses_the_intended_font_and_a_bright_palette(served, browser):
    """Guards the 'the browser looks faint next to a terminal' complaint.

    textual.js asks xterm for `'Roboto Mono', Monaco, ...`. textual-serve ships the
    TTF but upstream only ever referenced it from a CDN stylesheet, which sekka does
    not load, so the @font-face rules in the template are what keep the terminal on
    that face instead of a thinner fallback. The luma floor is the other half: the
    pre-2026-10 default theme measured ~102 on this same screen.
    """
    page = browser.new_page(viewport={"width": 1100, "height": 650})
    try:
        first_page(page, served)
        page.wait_for_timeout(2500)
        state = page_state(page)
        assert state["roboto"], "terminal fell back to a substitute monospace face"
        assert state["meanInkLuma"] > 130, f"rendering is faint: {state}"
        assert state["inkFraction"] > 0.003, f"almost nothing drawn: {state}"
    finally:
        page.close()
