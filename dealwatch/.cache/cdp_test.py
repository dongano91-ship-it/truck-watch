# -*- coding: utf-8 -*-
"""Минимальный CDP-клиент: проверка, пробивает ли реальный Chrome защиту площадок."""
import base64, json, os, socket, struct, subprocess, time, urllib.request, urllib.error, random

PORT = 9333
PROFILE = os.path.abspath("dealwatch/.cache/chrome-profile")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def http_get(path, method="GET"):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), method=method)
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())


class WS:
    def __init__(self, url):
        _, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
        req = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("handshake failed: %r" % head[:200])
        self.buf = rest

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj):
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
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        """Одно сообщение целиком (с учётом фрагментации и control-фреймов)."""
        while True:
            b1, b2 = self._recv_exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(ln)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping
                self.sock.sendall(b"\x8a\x80" + bytes(4))
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                return payload
            if opcode == 0x8:
                raise ConnectionError("ws closed")


class Page:
    def __init__(self, ws_url):
        self.ws = WS(ws_url)
        self.msg_id = 0

    def call(self, method, params=None, timeout=60):
        self.msg_id += 1
        mid = self.msg_id
        self.ws.send({"id": mid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.sock.settimeout(max(1, deadline - time.time()))
            msg = json.loads(self.ws.recv().decode())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})
        raise TimeoutError(method)

    def evaluate(self, expr, await_promise=False):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True,
            "awaitPromise": await_promise, "userGesture": True,
        })
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text"))
        return r["result"].get("value")

    def goto(self, url, wait=6):
        self.call("Page.enable")
        self.call("Page.navigate", {"url": url})
        time.sleep(wait)


def main():
    os.makedirs(PROFILE, exist_ok=True)
    proc = subprocess.Popen([
        CHROME, "--remote-debugging-port=%d" % PORT,
        "--user-data-dir=%s" % PROFILE, "--no-first-run",
        "--no-default-browser-check", "--headless=new",
        "--disable-blink-features=AutomationControlled",
        "--disable-crash-reporter", "--disable-gpu",
        "--window-size=1400,900", "--lang=ru-RU",
        "--user-agent=" + UA, "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                http_get("/json/version")
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("Chrome не поднялся")
            return
        print("Chrome OK:", http_get("/json/version")["Browser"])

        target = http_get("/json/new?about:blank", method="PUT")
        page = Page(target["webSocketDebuggerUrl"])

        probes = [
            ("WB каталог", "https://www.wildberries.ru/", "document.title + ' | ' + document.body.innerText.length"),
            ("WB поиск", "https://www.wildberries.ru/catalog/0/search.aspx?search=iphone%2015",
             "document.body.innerText.slice(0,200)"),
            ("Avito", "https://www.avito.ru/rossiya/telefony?q=iphone+15",
             "document.title + ' | items=' + document.querySelectorAll('[data-marker=\"item\"]').length"),
            ("Ozon", "https://www.ozon.ru/search/?text=iphone+15",
             "document.title + ' | body=' + document.body.innerText.length"),
            ("YM", "https://market.yandex.ru/search?text=iphone+15",
             "document.title + ' | body=' + document.body.innerText.length"),
            ("MM", "https://megamarket.ru/catalog/?q=iphone%2015",
             "document.title + ' | body=' + document.body.innerText.length"),
        ]
        for name, url, expr in probes:
            try:
                page.goto(url, wait=7)
                print("%-12s -> %s" % (name, page.evaluate(expr)))
            except Exception as e:
                print("%-12s -> ERR %s" % (name, str(e)[:120]))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
