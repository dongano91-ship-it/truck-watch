import ssl, urllib.request, socket, json
unc = ssl._create_unverified_context()
for host in ("example.com","search.wb.ru"):
    try:
        ctx = unc
        with socket.create_connection((host,443), timeout=15) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                c = ss.getpeercert()
                print(host, "issuer:", dict(x[0] for x in c["issuer"]).get("organizationName"), "| CN:", dict(x[0] for x in c["subject"]).get("commonName"))
    except Exception as e:
        print(host, "ERR", e)
data = urllib.request.urlopen("https://curl.se/ca/cacert.pem", timeout=30, context=unc).read()
print("cacert bytes:", len(data))
