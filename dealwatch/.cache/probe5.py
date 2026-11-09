import ssl, urllib.request, urllib.error, json
ctx = ssl.create_default_context(cafile="dealwatch/.cache/cacert.pem")
H = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Accept-Language":"ru-RU,ru;q=0.9"}
def get(u, h=None):
    hd = dict(H); hd.update(h or {})
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=25, context=ctx)
        return r.status, r.geturl(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, u, e.read()
    except Exception as e:
        return "ERR", u, str(e).encode()

s,u,b = get("https://api.ipify.org?format=json"); print("IP:", s, b[:100])
s,u,b = get("http://ip-api.com/json/?fields=country,regionName,isp,proxy,hosting"); print("GEO:", s, b[:200])
s,u,b = get("https://www.wildberries.ru/"); print("WB site:", s, len(b))
s,u,b = get("https://www.ozon.ru/search/?text=iphone"); print("Ozon site:", s, len(b), u[:80])
s,u,b = get("https://market.yandex.ru/search?text=iphone"); 
if bus := b: print("YM len:", len(b), "price-ish hits:", b.count(b'"price"'), b.count(b'"value"'))
