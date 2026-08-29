import json, sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
print("top keys:", list(d.keys()))
u2 = d.get("components", {}).get("U2")
print(json.dumps(u2, indent=2, ensure_ascii=False))
for k in ("matches", "primitives", "recognition", "blocks", "roles"):
    if k in d:
        print(k, "->", json.dumps(d[k], indent=2, ensure_ascii=False))
