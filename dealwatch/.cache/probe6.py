# -*- coding: utf-8 -*-
import ssl, urllib.request, urllib.error, json, re, http.cookiejar, time
ctx = ssl.create_default_context(cafile="dealwatch/.cache/cacert.pem")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(cj))
H = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
     "Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8"}
def get(u, h=None):
    hd=dict(H); hd.update(h or {})
    try:
        r = op.open(urllib.request.Request(u, headers=hd), timeout=25)
        return r.status, r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), u
    except Exception as e:
        return "ERR", str(e).encode(), u

# 1) Yandex Market: ищем встроенный JSON
s,b,u = get("https://market.yandex.ru/search?text=iphone+15")
print("YM:", s, len(b), "| 'price' hits:", b.count(b'"price"'), "| data-state:", b.count(b"data-state"), "| captcha:", b"captcha" in b.lower())
if s == 200 and b:
    m = re.findall(rb'captcha', b.lower()); print("  title:", re.search(rb'<title>(.{0,80})', b).group(1)[:80])
time.sleep(2)
# 2) Avito повтор с куками
s,b,u = get("https://www.avito.ru/rossiya/telefony?q=iphone+15", {"Referer":"https://www.avito.ru/"})
print("Avito:", s, len(b), "| firewall:", b"firewall" in b.lower(), "| captcha:", b"captcha" in b.lower())
if b: print("  body:", b[:120])
# 3) Megamarket варианты
def post(u, payload, h=None):
    hd=dict(H); hd["Content-Type"]="application/json"; hd.update(h or {})
    try:
        r=op.open(urllib.request.Request(u, data=json.dumps(payload).encode(), headers=hd, method="POST"), timeout=25)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return "ERR", str(e).encode()
base="https://megamarket.ru/api/mobile/v1/catalogService/catalog/search"
for rv in (10, 9, 8):
    s,b=post(base, {"requestVersion":rv,"limit":10,"offset":0,"isMultiCategorySearch":False,"searchByOriginalQuery":False,"selectedSuggestParams":[],"expandedFiltersIds":[],"sorting":0,"ageMore18":None,"showNotAvailable":True,"searchText":"iphone"})
    print(f"MM rv{rv}:", s, b[:90])
