# -*- coding: utf-8 -*-
import ssl, urllib.request, urllib.error, json
ctx = ssl.create_default_context(cafile="dealwatch/.cache/cacert.pem")
H = {
 "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
 "Accept":"*/*","Accept-Language":"ru-RU,ru;q=0.9,en;q=0.8",
}
def get(u, h=None, data=None, m="GET"):
    hd = dict(H); hd.update(h or {})
    req = urllib.request.Request(u, headers=hd, data=data, method=m)
    try:
        r = urllib.request.urlopen(req, timeout=25, context=ctx)
        b = r.read()
        return r.status, len(b), b[:200]
    except urllib.error.HTTPError as e:
        return e.code, 0, e.read()[:200]
    except Exception as e:
        return "ERR", 0, str(e)[:150].encode()

tests = [
 ("WB v9 plain",  "https://search.wb.ru/exactmatch/ru/common/v9/search?appType=1&curr=rub&dest=-1257786&query=iphone&resultset=catalog&spp=30&lang=ru&locale=ru"),
 ("WB v9 origin", "https://search.wb.ru/exactmatch/ru/common/v9/search?appType=1&curr=rub&dest=-1257786&query=iphone&resultset=catalog&spp=30&lang=ru&locale=ru", {"Origin":"https://www.wildberries.ru","Referer":"https://www.wildberries.ru/"}),
 ("WB v13",       "https://search.wb.ru/exactmatch/ru/common/v13/search?appType=1&curr=rub&dest=-1257786&query=iphone&resultset=catalog&spp=30&lang=ru&locale=ru"),
 ("WB card",      "https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm=146961398"),
 ("Ozon composer",  "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/search/?text=iphone"),
 ("Avito html",     "https://www.avito.ru/rossiya/telefony?q=iphone"),
 ("YaMarket html",  "https://market.yandex.ru/search?text=iphone"),
]
for t in tests:
    h = t[2] if len(t) > 2 else None
    s, n, snip = get(t[1], h)
    print(f"{t[0]:16} -> {s} len={n} :: {snip[:150]!r}")

s,n,snip = get("https://megamarket.ru/api/mobile/v1/catalogService/catalog/search",
   {"Content-Type":"application/json","Origin":"https://megamarket.ru","Referer":"https://megamarket.ru/"},
   json.dumps({"requestVersion":10,"limit":10,"offset":0,"isMultiCategorySearch":False,"searchByOriginalQuery":False,"selectedSuggestParams":[],"expandedFiltersIds":[],"sorting":0,"ageMore18":None,"showNotAvailable":True,"searchText":"iphone"}).encode(), "POST")
print(f"{'Megamarket':16} -> {s} len={n} :: {snip[:150]!r}")
