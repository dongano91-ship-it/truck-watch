# -*- coding: utf-8 -*-
"""
browser.py — управление реальным Google Chrome через DevTools Protocol.
Без зависимостей: только стандартная библиотека.

Зачем браузер, а не requests:
  Wildberries, Ozon, Авито и Мегамаркет отдают 403/429/капчу любым HTTP-клиентам,
  потому что проверяют TLS-отпечаток и выполняют JS-челлендж. Обычный Chrome
  проходит эти проверки, поэтому мы просто управляем им снаружи.
"""
from __future__ import annotations

import base64
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class BrowserError(RuntimeError):
    pass


def find_chrome() -> str | None:
    env = os.environ.get("DEALWATCH_CHROME")
    if env and os.path.exists(env):
        return env
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _get_json(port: int, path: str, method: str = "GET", timeout: float = 5.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8", "replace")) if raw else {}


class _WS:
    """Минимальный websocket-клиент: нужен только для CDP."""

    def __init__(self, url: str, timeout: float = 30.0):
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(8192)
            if not chunk:
                raise BrowserError("Chrome закрыл соединение при подключении к CDP")
            buf += chunk
        head, _, self._buf = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise BrowserError(f"CDP handshake отклонён: {head[:120]!r}")

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise BrowserError("CDP-соединение закрыто")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = bytes(random.getrandbits(8) for _ in range(4))
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> bytes:
        while True:
            b1, b2 = self._read_exact(2)
            opcode, masked, n = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping
                self.sock.sendall(b"\x8a\x80" + bytes(4))
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise BrowserError("CDP-соединение закрыто")
            if opcode in (0x1, 0x2):
                return payload

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Page:
    """Одна вкладка Chrome."""

    def __init__(self, ws_url: str):
        self.ws = _WS(ws_url)
        self._id = 0
        self.call("Page.enable")
        self.call("Runtime.enable")

    def call(self, method: str, params: dict | None = None, timeout: float = 90.0) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send({"id": mid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.sock.settimeout(max(0.5, deadline - time.time()))
            msg = json.loads(self.ws.recv().decode("utf-8", "replace"))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise BrowserError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise BrowserError(f"таймаут CDP-вызова {method}")

    def evaluate(self, expr: str, await_promise: bool = False):
        res = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        }, timeout=60.0)
        if res.get("exceptionDetails"):
            raise BrowserError(str(res["exceptionDetails"].get("text"))[:200])
        return res.get("result", {}).get("value")

    def goto(self, url: str, settle: float = 3.0, timeout: float = 60.0) -> None:
        self.call("Page.navigate", {"url": url}, timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self.evaluate("document.readyState")
            except BrowserError:
                state = None
            if state in ("complete", "interactive"):
                break
            time.sleep(0.4)
        time.sleep(settle)

    def scroll(self, steps: int = 4, pause: float = 0.6) -> None:
        for _ in range(steps):
            try:
                self.evaluate("window.scrollBy(0, Math.min(900, document.body.scrollHeight)); 1")
            except BrowserError:
                pass
            time.sleep(pause)

    def close(self) -> None:
        try:
            self.call("Page.close", timeout=5)
        except Exception:
            pass
        self.ws.close()


class Chrome:
    """Запуск/подключение к Chrome и выдача вкладок."""

    def __init__(self, port: int = 9222, profile_dir: str = ".chrome-profile",
                 headless: bool = False, ua: str | None = None, verbose: bool = True):
        self.port = port
        self.profile_dir = os.path.abspath(profile_dir)
        self.headless = headless
        self.ua = ua or DEFAULT_UA
        self.verbose = verbose
        self.proc: subprocess.Popen | None = None
        self._pages: list[Page] = []
        self._own = False

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [chrome] {msg}")

    def start(self) -> None:
        # 1. Уже запущен нашим профилем — просто подключаемся.
        try:
            ver = _get_json(self.port, "/json/version")
            self._log(f"подключаюсь к запущенному Chrome: {ver.get('Browser')}")
            return
        except Exception:
            pass

        chrome = find_chrome()
        if not chrome:
            raise BrowserError("Не найден Chrome/Edge. Установите Chrome или задайте DEALWATCH_CHROME")

        os.makedirs(self.profile_dir, exist_ok=True)
        args = [
            chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-crash-reporter",
            "--disable-features=Translate,MediaRouter",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--window-size=1440,900",
            "--lang=ru-RU",
            f"--user-agent={self.ua}",
            "about:blank",
        ]
        if self.headless:
            args.insert(1, "--headless=new")

        self._log("запускаю Chrome...")
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if self.headless else 0,
        )
        self._own = True

        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                _get_json(self.port, "/json/version", timeout=2)
                return
            except Exception:
                if self.proc.poll() is not None:
                    raise BrowserError(
                        "Chrome сразу завершился. Если вы в песочнице DSH — запускайте "
                        "инструмент обычным двойным кликом по .bat, вне песочницы."
                    )
                time.sleep(0.5)
        raise BrowserError("Chrome не открыл порт отладки за 45 секунд")

    def new_page(self, url: str = "about:blank") -> Page:
        try:
            target = _get_json(self.port, f"/json/new?{urllib.parse.quote(url, safe=':/?&=%')}",
                               method="PUT", timeout=10)
        except urllib.error.HTTPError:
            target = _get_json(self.port, "/json/new", method="PUT", timeout=10)
        page = Page(target["webSocketDebuggerUrl"])
        self._pages.append(page)
        return page

    def stop(self) -> None:
        for p in self._pages:
            p.close()
        self._pages.clear()
        if not (self._own and self.proc):
            return
        try:
            if self.proc.poll() is None:
                # Аккуратно закрываем браузер, чтобы не повредить профиль с логинами.
                try:
                    ver = _get_json(self.port, "/json/version", timeout=3)
                    ws = _WS(ver["webSocketDebuggerUrl"], timeout=5)
                    ws.send({"id": 1, "method": "Browser.close"})
                    ws.close()
                except Exception:
                    pass
                time.sleep(1.5)
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
