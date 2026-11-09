import os, glob
os.makedirs("dealwatch/certs", exist_ok=True)
parts = []
base = "dealwatch/.cache/cacert.pem"
if os.path.exists(base):
    parts.append(open(base,"rb").read())
for p in sorted(glob.glob("certs/*.crt")):
    parts.append(open(p,"rb").read())
out = b"\n".join(p if p.endswith(b"\n") else p + b"\n" for p in parts)
open("dealwatch/certs/ca-bundle.pem","wb").write(out)
print("bundle bytes:", len(out), "files:", 1+len(glob.glob("certs/*.crt")))
