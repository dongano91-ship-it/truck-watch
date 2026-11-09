import ssl, urllib.request, os
ctx = ssl.create_default_context()
print("default context certs:", len(ctx.get_ca_certs()))
unc = ssl._create_unverified_context()
url = "https://curl.se/ca/cacert.pem"
data = urllib.request.urlopen(url, timeout=30, context=unc).read()
open(".cache/cacert.pem","wb").write(data)
print("downloaded cacert.pem bytes:", len(data))
ctx2 = ssl.create_default_context(cafile=".cache/cacert.pem")
print("bundle certs:", len(ctx2.get_ca_certs()))
r = urllib.request.urlopen("https://example.com", timeout=15, context=ctx2)
print("verify with bundle:", r.status)
