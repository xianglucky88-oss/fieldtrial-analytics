def calculate(rows, payload):
    groups = {}; missing=[]
    for row in rows:
        if row.get("value") is None: missing.append(row["plot"]); continue
        groups.setdefault(row["treatment"], []).append(float(row["value"]))
    means = {k: round(sum(v)/len(v), 3) for k,v in groups.items()}
    return {"treatment_means": means, "observed_count": sum(map(len, groups.values())), "missing_plots": missing, "blocks": sorted({r["block"] for r in rows})}
