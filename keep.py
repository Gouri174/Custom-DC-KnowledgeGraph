"""
build_idc_graph_v5.py
=====================
Final correct version of India Data Commons MOSPI semantic graph.

Fixes from review:
  ✅ ENUM split into: ENUM / CONCEPT / PROPERTY / STAT_TYPE
  ✅ Observations per year (granular), not bundled
  ✅ Explicit Place node: Observation → FOR_PLACE → India
  ✅ Observation structure: StatVar → Observation → Time + Place → Value

Run:
  Terminal 1: python app.py
  Terminal 2: python build_idc_graph_v5.py
"""
import json, requests, os, time

PROXY = "http://localhost:5050"

SEED_DCIDS = [
    "RealValue_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService",
    "RealValue_Amount_EconomicActivity_GrossValueAdded_PublicAdministrationAndDefenceOrMOSPIOtherService",
    "Nominal_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService",
    "Nominal_Amount_EconomicActivity_GrossValueAdded_PublicAdministrationAndDefenceOrMOSPIOtherService",
]

SEED_LABELS = {
    "RealValue_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService":
        "GVA Other Service\n(Constant prices)",
    "RealValue_Amount_EconomicActivity_GrossValueAdded_PublicAdministrationAndDefenceOrMOSPIOtherService":
        "GVA Public Admin & Defence\n(Constant prices)",
    "Nominal_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService":
        "GVA Other Service\n(Current prices)",
    "Nominal_Amount_EconomicActivity_GrossValueAdded_PublicAdministrationAndDefenceOrMOSPIOtherService":
        "GVA Public Admin & Defence\n(Current prices)",
}

# ── Precise node type for each known DCID ─────────────────────────────────
# ENUM      = constrained dimension value (activitySource, economicSector, qualifier)
# CONCEPT   = broad category/domain (populationType)
# PROPERTY  = what is being measured (measuredProperty)
# STAT_TYPE = how the stat is computed (statType)
NODE_SUBTYPE = {
    "GrossValueAdded":    "ENUM",      # activitySource — what indicator
    "MOSPI_OtherService": "ENUM",      # economicSector — which sector
    "PublicAdministrationAndDefence__MOSPI_OtherService": "ENUM",
    "RealValue":          "ENUM",      # measurementQualifier — price type
    "Nominal":            "ENUM",      # measurementQualifier — price type
    "EconomicActivity":   "CONCEPT",   # populationType — domain concept
    "amount":             "PROPERTY",  # measuredProperty — what is measured
    "measuredValue":      "STAT_TYPE", # statType — how stat computed
}

ENUM_LABELS = {
    "GrossValueAdded":    "Gross Value Added",
    "MOSPI_OtherService": "MOSPI Other Service",
    "PublicAdministrationAndDefence__MOSPI_OtherService": "Public Admin & Defence\n+ MOSPI Other Service",
    "RealValue":          "Real Value\n(constant prices)",
    "Nominal":            "Nominal\n(current prices)",
    "EconomicActivity":   "Economic Activity",
    "amount":             "amount",
    "measuredValue":      "measuredValue",
}

# Only these outgoing properties from StatVar — strict, no extras
STATVAR_OUTGOING = {
    "activitySource":       "ENUM",
    "economicSector":       "ENUM",
    "measurementQualifier": "ENUM",
    "measuredProperty":     "PROPERTY",
    "populationType":       "CONCEPT",
    "statType":             "STAT_TYPE",
}

# Colours per subtype
COLORS = {
    "STAT_VAR":    "#6c63ff",   # purple  — statistical variable
    "ENUM":        "#1D9E75",   # green   — dimension value
    "CONCEPT":     "#0F6E56",   # dark green — domain concept
    "PROPERTY":    "#5F5E5A",   # gray    — measured property
    "STAT_TYPE":   "#444441",   # dark gray — stat computation type
    "PLACE":       "#BA7517",   # amber   — geographic place
    "OBSERVATION": "#00d4aa",   # teal    — one observation
    "TIME":        "#7F77DD",   # lavender — year node
}

# LR hierarchy levels
LEVELS = {
    "CONCEPT":     0,
    "ENUM":        1,
    "PROPERTY":    1,
    "STAT_TYPE":   1,
    "STAT_VAR":    3,
    "PLACE":       5,
    "OBSERVATION": 5,
    "TIME":        6,
}

SHAPES = {
    "STAT_VAR":    "diamond",
    "ENUM":        "dot",
    "CONCEPT":     "ellipse",
    "PROPERTY":    "box",
    "STAT_TYPE":   "box",
    "PLACE":       "star",
    "OBSERVATION": "square",
    "TIME":        "triangleDown",
}

def fetch_out(dcid):
    try:
        r = requests.get(f"{PROXY}/triples/out/{dcid}", timeout=20)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        print(f"  ERR: {e}")
        return {}

def fetch_stat_series(dcid, place="country/IND"):
    try:
        r = requests.get(
            "https://indiadatacommons.org/api/stat/series",
            params={"stat_var": dcid, "place": place},
            timeout=20
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return {}


def build_graph():
    nodes = {}
    edges = []
    seen  = set()

    def add_node(nid, label, ntype, tooltip=""):
        if nid in nodes: return
        lvl = LEVELS.get(ntype, 3)
        nodes[nid] = {
            "id": nid, "label": label, "type": ntype,
            "level": lvl, "tooltip": tooltip or nid,
        }

    def add_edge(src, tgt, prop, color=None):
        # Enforce direction — never allow dimension → StatVar
        if nodes.get(tgt,{}).get("type") == "STAT_VAR": return
        k = f"{src}|{prop}|{tgt}"
        if k in seen: return
        seen.add(k)
        edges.append({"from":src,"to":tgt,"prop":prop,"color":color})

    # ── Shared Place node (India) ──────────────────────────────────────────
    add_node("country/IND", "India\n(country/IND)", "PLACE",
             tooltip="DCID: country/IND\nType: Place\nName: India\n\nAll MOSPI observations are for this place.")

    # ── StatVar nodes + their dimension edges ──────────────────────────────
    print("\nStep 1: StatVar nodes and dimension edges...")
    for dcid in SEED_DCIDS:
        print(f"  {dcid[:55]}")
        out = fetch_out(dcid)
        time.sleep(0.3)

        # Build tooltip
        lines = [f"DCID: {dcid}", "Type: STAT_VAR", ""]
        for v in out.get("name",[]):
            nm = v.get("value","") if isinstance(v,dict) else str(v)
            if nm: lines.append(f"Name: {nm}"); break
        for prop in STATVAR_OUTGOING:
            vals = out.get(prop,[])
            if isinstance(vals,list) and vals:
                v = vals[0]
                if isinstance(v,dict):
                    val = v.get("name","") or v.get("dcid","") or v.get("value","")
                    if val: lines.append(f"{prop}: {val}")
        lines.append("\nSource: indiadatacommons.org")

        add_node(dcid, SEED_LABELS[dcid], "STAT_VAR", tooltip="\n".join(lines))

        # Add dimension edges
        for prop, expected_type in STATVAR_OUTGOING.items():
            vals = out.get(prop,[])
            if not isinstance(vals,list): continue
            for v in vals:
                if not isinstance(v,dict): continue
                tdcid = v.get("dcid","")
                tname = v.get("name","") or v.get("value","") or tdcid
                if not tdcid: continue

                # Use precise subtype
                actual_type = NODE_SUBTYPE.get(tdcid, expected_type)
                label = ENUM_LABELS.get(tdcid, tname[:30])
                tooltip = (f"DCID: {tdcid}\nType: {actual_type}\n"
                           f"Name: {tname}\n"
                           f"Constrains StatVar via: {prop}\n"
                           f"\nSource: indiadatacommons.org")
                add_node(tdcid, label, actual_type, tooltip=tooltip)
                add_edge(dcid, tdcid, prop)
                print(f"    [{actual_type}] {prop} → {label.replace(chr(10),' ')}")

    # ── Observation layer: one node per year ──────────────────────────────
    print("\nStep 2: Fetching observations per year...")
    MAX_OBS = 5  # show 5 most recent years per StatVar

    for dcid in SEED_DCIDS:
        print(f"  {dcid[:55]}")
        data = fetch_stat_series(dcid)
        time.sleep(0.4)

        series = {}
        if isinstance(data, dict):
            inner = data.get("data", data.get("series", {}))
            if isinstance(inner, dict):
                for pk in ["country/IND","IND","India"]:
                    if pk in inner:
                        series = inner[pk]; break
                if not series and inner:
                    first = list(inner.values())[0]
                    if isinstance(first,dict): series = first

        if series:
            sorted_years = sorted(series.keys(), reverse=True)[:MAX_OBS]
            print(f"    {len(series)} total years, showing {len(sorted_years)} most recent")

            for yr in sorted_years:
                val = series[yr]
                # One Observation node per year
                obs_id = f"obs::{dcid}::{yr}"
                obs_label = f"Observation\n{yr}"
                obs_tooltip = (
                    f"Type: OBSERVATION\n"
                    f"StatVar: {dcid}\n"
                    f"Year: {yr}\n"
                    f"Place: India (country/IND)\n"
                    f"Value: {val}\n"
                    f"\nThis is a single annual data point."
                )
                add_node(obs_id, obs_label, "OBSERVATION", tooltip=obs_tooltip)
                add_edge(dcid, obs_id, "hasObservation")

                # Time node
                time_id = f"time::{yr}"
                add_node(time_id, yr, "TIME",
                         tooltip=f"Type: TIME\nYear: {yr}\nFiscal year in Indian NAS")
                add_edge(obs_id, time_id, "forYear")

                # Place edge (explicit)
                add_edge(obs_id, "country/IND", "forPlace")

                print(f"    {yr} → {val}")
        else:
            # No live data — show placeholder observation
            obs_id = f"obs::{dcid}::placeholder"
            add_node(obs_id, "Observations\n(India, annual)", "OBSERVATION",
                     tooltip=(f"StatVar: {dcid}\nPlace: India\n"
                              f"Fetch: /api/stat/series?stat_var={dcid}&place=country/IND\n"
                              f"\nNo live data returned — may need auth or different endpoint."))
            add_edge(dcid, obs_id, "hasObservation")
            add_edge(obs_id, "country/IND", "forPlace")
            print(f"    No data returned")

    print(f"\nGraph: {len(nodes)} nodes, {len(edges)} edges")
    return nodes, edges


def generate_html(nodes, edges):
    vis_nodes = []
    for d in nodes.values():
        c     = COLORS.get(d["type"], "#888780")
        size  = {"STAT_VAR":28,"ENUM":16,"CONCEPT":16,"PROPERTY":12,
                 "STAT_TYPE":10,"PLACE":18,"OBSERVATION":14,"TIME":12}.get(d["type"],12)
        shape = SHAPES.get(d["type"],"dot")
        vis_nodes.append({
            "id":d["id"],"label":d["label"],"title":d["tooltip"],
            "level":d["level"],
            "color":{"background":c,"border":c,"highlight":{"background":"#fff"}},
            "size":size,"shape":shape,
            "font":{"color":"#e6edf3","size":11,"multi":True},
        })

    edge_colors = {
        "hasObservation": "#00d4aa",
        "forYear":        "#7F77DD",
        "forPlace":       "#BA7517",
    }
    vis_edges = []
    for i,e in enumerate(edges):
        ec = edge_colors.get(e["prop"], "#4b5563")
        vis_edges.append({
            "id":i,"from":e["from"],"to":e["to"],"label":e["prop"],
            "arrows":"to",
            "color":{"color":ec,"highlight":"#9ca3af"},
            "font":{"color":"#555","size":9,"strokeWidth":0},
            "width":1.5,
        })

    legend = [
        ("diamond","#6c63ff","STAT_VAR — statistical variable (DCID)"),
        ("dot",    "#1D9E75","ENUM — dimension value\n(activitySource, economicSector, qualifier)"),
        ("ellipse","#0F6E56","CONCEPT — domain concept (populationType)"),
        ("box",    "#5F5E5A","PROPERTY — measuredProperty"),
        ("box",    "#444441","STAT_TYPE — statType (how stat computed)"),
        ("star",   "#BA7517","PLACE — geographic location (India)"),
        ("square", "#00d4aa","OBSERVATION — one data point (per year)"),
        ("tri",    "#7F77DD","TIME — fiscal year"),
    ]
    leg_html = ""
    for sh,c,l in legend:
        if sh == "diamond":
            svg = f"<polygon points='7,0 14,7 7,14 0,7' fill='{c}'/>"
        elif sh == "ellipse":
            svg = f"<ellipse cx='7' cy='7' rx='7' ry='5' fill='{c}'/>"
        elif sh == "box":
            svg = f"<rect width='14' height='10' y='2' rx='2' fill='{c}'/>"
        elif sh == "star":
            svg = f"<polygon points='7,0 9,5 14,5 10,8 12,14 7,10 2,14 4,8 0,5 5,5' fill='{c}'/>"
        elif sh == "square":
            svg = f"<rect width='12' height='12' x='1' y='1' fill='{c}'/>"
        elif sh == "tri":
            svg = f"<polygon points='7,14 0,0 14,0' fill='{c}'/>"
        else:
            svg = f"<circle cx='7' cy='7' r='6' fill='{c}'/>"
        leg_html += f'<div class="leg"><svg width="14" height="14" style="flex-shrink:0">{svg}</svg><span>{l}</span></div>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>India Data Commons MOSPI — Final Semantic Graph</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/dist/vis-network.min.js"></script>
<link href="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/dist/dist/vis-network.min.css" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
header{{padding:10px 20px;background:#161b22;border-bottom:1px solid #21262d;display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap}}
h1{{font-size:14px;font-weight:600}}
.badge{{font-size:11px;color:#8b949e;background:#21262d;border:1px solid #30363d;padding:2px 9px;border-radius:12px}}
.ctrl{{padding:6px 20px;background:#161b22;border-bottom:1px solid #21262d;display:flex;gap:8px;flex-shrink:0;align-items:center;font-size:12px;color:#8b949e}}
button{{background:#21262d;border:1px solid #30363d;color:#e6edf3;padding:4px 12px;border-radius:5px;font-size:12px;cursor:pointer}}
button:hover{{background:#30363d}}
button.on{{background:#6c63ff;border-color:#6c63ff;color:#fff}}
#wrap{{flex:1;position:relative;overflow:hidden}}
#net{{width:100%;height:100%}}
.panel{{position:absolute;background:rgba(22,27,34,.95);border:1px solid #30363d;border-radius:8px;padding:10px 14px;font-size:12px}}
.legend{{top:10px;left:10px;min-width:280px}}
.leg{{display:flex;align-items:center;gap:8px;margin-bottom:6px;line-height:1.4;font-size:11px}}
.info{{top:10px;right:10px;max-width:360px;display:none;line-height:1.8;white-space:pre-wrap;word-break:break-all;max-height:78vh;overflow-y:auto}}
.info.show{{display:block}}
.info strong{{color:#58a6ff;display:block;margin-bottom:6px}}
.bar{{bottom:10px;left:10px;font-size:11px;color:#8b949e}}
</style>
</head>
<body>
<header>
  <h1>India Data Commons — MOSPI Final Semantic Graph</h1>
  <span class="badge">Source: indiadatacommons.org</span>
  <span class="badge">{len(vis_nodes)} nodes</span>
  <span class="badge">{len(vis_edges)} edges</span>
  <span class="badge">4 StatVars · per-year observations · Place modelled</span>
</header>
<div class="ctrl">
  Layout:
  <button id="bh" class="on" onclick="setLayout('h')">Hierarchical (LR)</button>
  <button id="bf" onclick="setLayout('f')">Force-directed</button>
  <button onclick="net.fit()">Fit view</button>
  <span style="margin-left:8px;color:#444">
    ENUM/CONCEPT/PROPERTY → STAT_VAR → OBSERVATION → TIME + PLACE
  </span>
</div>
<div id="wrap">
  <div id="net"></div>
  <div class="panel legend">
    <div style="font-size:10px;color:#8b949e;margin-bottom:8px;letter-spacing:.5px">NODE TYPES</div>
    {leg_html}
    <div style="font-size:10px;color:#8b949e;margin-top:10px;line-height:1.7;border-top:1px solid #30363d;padding-top:8px">
      <div style="text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px">Reading left → right</div>
      <div>ENUM constrains → STAT_VAR</div>
      <div>STAT_VAR hasObservation → OBSERVATION</div>
      <div>OBSERVATION forYear → TIME</div>
      <div>OBSERVATION forPlace → PLACE (India)</div>
    </div>
  </div>
  <div class="panel info" id="info">
    <strong id="it"></strong><span id="ib"></span>
  </div>
  <div class="panel bar" id="bar">Loading...</div>
</div>
<script>
const ND={json.dumps(vis_nodes,ensure_ascii=False)};
const ED={json.dumps(vis_edges,ensure_ascii=False)};
const ns=new vis.DataSet(ND), es=new vis.DataSet(ED);
const hOpts={{
  layout:{{hierarchical:{{direction:'LR',sortMethod:'directed',
    levelSeparation:220,nodeSpacing:70,treeSpacing:150}}}},
  physics:{{enabled:false}},
  edges:{{smooth:{{type:'cubicBezier',forceDirection:'horizontal',roundness:.4}},
    arrows:{{to:{{enabled:true,scaleFactor:.6}}}}}},
  nodes:{{borderWidth:2}},
  interaction:{{hover:true,tooltipDelay:60,navigationButtons:true,keyboard:true}}
}};
const fOpts={{
  layout:{{hierarchical:false}},
  physics:{{solver:'barnesHut',
    barnesHut:{{gravitationalConstant:-14000,centralGravity:.4,
      springLength:200,springConstant:.04,damping:.1}},
    maxVelocity:50,minVelocity:.1,stabilization:{{iterations:400}}}},
  edges:{{smooth:{{type:'dynamic'}},arrows:{{to:{{enabled:true,scaleFactor:.6}}}}}},
  nodes:{{borderWidth:2}},
  interaction:{{hover:true,tooltipDelay:60,navigationButtons:true,keyboard:true}}
}};
let net=new vis.Network(document.getElementById('net'),{{nodes:ns,edges:es}},hOpts);
function bind(){{
  net.on('click',p=>{{
    const info=document.getElementById('info');
    if(p.nodes.length){{
      const n=ND.find(x=>x.id===p.nodes[0]);
      document.getElementById('it').textContent=n.label.replace(/\\n/g,' ');
      document.getElementById('ib').textContent=n.title;
      info.classList.add('show');
    }}else info.classList.remove('show');
  }});
  net.on('afterDrawing',()=>{{
    document.getElementById('bar').textContent=
      ND.length+' nodes · '+ED.length+' edges · Click any node for details';
  }});
  net.on('stabilizationIterationsDone',()=>{{net.fit();}});
}}
bind();
setTimeout(()=>net.fit(),400);
function setLayout(m){{
  document.getElementById('bh').classList.toggle('on',m==='h');
  document.getElementById('bf').classList.toggle('on',m==='f');
  net.destroy();
  net=new vis.Network(document.getElementById('net'),{{nodes:ns,edges:es}},
    m==='h'?hOpts:fOpts);
  bind();
  setTimeout(()=>net.fit(),300);
}}
</script>
</body>
</html>"""

    with open("idc_graph.html","w",encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: idc_graph.html ({os.path.getsize('idc_graph.html')//1024} KB)")


if __name__ == "__main__":
    try:
        requests.get(f"{PROXY}/triples/out/test", timeout=5)
    except:
        print("ERROR: Start proxy first: python app.py")
        exit()

    graph_nodes, graph_edges = build_graph()

    with open("idc_graph_v5.json","w",encoding="utf-8") as f:
        json.dump({"nodes":list(graph_nodes.values()),
                   "edges":graph_edges}, f, indent=2, ensure_ascii=False)
    print("Saved: idc_graph_v5.json")
    generate_html(graph_nodes, graph_edges)
    print("Open: idc_graph.html")