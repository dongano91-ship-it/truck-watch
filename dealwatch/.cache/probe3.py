import ssl, urllib.request, os
os.makedirs("dealwatch/.cache", exist_ok=True)
unc = ssl._create_unverified_context()
p = "dealwatch/.cache/cacert.pem"
if not os.path.exists(p):
    open(p,"wb").write(urllib.request.urlopen("https://curl.se/ca/cacert.pem", timeout=30, context=unc).read())
ctx = ssl.create_default_context(cafile=p)
print("bundle certs:", len(ctx.get_ca_certs()))
for u in ("https://example.com", "https://search.wb.ru/exactmatch/ru/common/v9/search?appType=1&curr=rub&dest=-1257786&query=iphone&resultset=catalog&spp=30"):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0"}), timeout=25, context=ctx)
        print("OK", r.status, len(r.read()), u[:60])
    except Exception as e:
        print("FAIL", type(e).__name__, str(e)[:120], u[:60])
