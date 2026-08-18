import json, subprocess
out = subprocess.run(
    ["uv","run","cg","view","D2","fixtures/protel/FX-T268-V6-10米-5065.NET","--hops","1"],
    cwd="E:/__electric/__workspace/stacking/circuit-graph",
    capture_output=True, text=True, encoding="utf-8",
)
d = json.loads(out.stdout)
print("focal:", d["focal"], "hops:", d["hops"])
focal = d["components"].get("D2")
print("\n=== D2 本体 ===")
print(json.dumps(focal, indent=2, ensure_ascii=False))
nets = set()
for c in d["components"].values():
    for p in c.get("pins", []):
        nets.add(p["net"])
print("\n=== D2 周围 1 跳的 nets ===", sorted(nets))
print("\n=== 邻居元件 (1 hop) ===")
for k, v in d["components"].items():
    if k == "D2":
        continue
    pins = [(p["pin"], p["net"]) for p in v.get("pins", [])]
    print(f"  {k}: {v.get('type','?')} value={v.get('value','')} pkg={v.get('package','')} pins={pins}")
