"""
build_idc_graph_v6.py
=====================
Production-ready India Data Commons semantic graph builder.

Changes from v5:
  ✅ No hardcoded SEED_DCIDS — pass via --dcids or --dcids-file
  ✅ No SEED_LABELS — names come from API triples (name property)
  ✅ --place is a CLI arg (default: country/IND)
  ✅ --max-obs is a CLI arg (default: 5, 0 = all)
  ✅ Node type inferred from API typeOf property, not string matching

Usage:
  # Start proxy first:
  python app.py

  # Then run with any DCIDs:
  python build_idc_graph_v6.py \\
    --dcids RealValue_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService \\
            Nominal_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService \\
    --place country/IND \\
    --max-obs 5 \\
    --output idc_graph.html

  # Or from a file (one DCID per line):
  python build_idc_graph_v6.py --dcids-file seeds.txt

  # All states:
  python build_idc_graph_v6.py --dcids-file seeds.txt --place geoId/29

  # Fetch all years:
  python build_idc_graph_v6.py --dcids-file seeds.txt --max-obs 0
"""
import argparse
import json
import os
import requests
import sys
import time

PROXY = "http://localhost:5050"

# ── Semantically meaningful outgoing properties (schema-level decision) ────
# This is intentional design, not hardcoding: we only traverse edges that
# carry economic meaning. lat/lon/typeOf/etc. are schema noise.
STATVAR_OUTGOING = {
    "activitySource",
    "economicSector",
    "measurementQualifier",
    "measuredProperty",
    "populationType",
    "statType",
}

# ── Node type classification from API typeOf values ────────────────────────
# Maps Data Commons typeOf values → our semantic categories
# This uses the API's own schema, not string guessing.
TYPEOF_TO_CATEGORY = {
    "StatisticalVariable":   "STAT_VAR",
    "StatVarGroup":          "GROUP",
    "StatVarPeerGroup":      "PEERGROUP",
    "Class":                 "CONCEPT",
    "Property":              "PROPERTY",
    "Enumeration":           "ENUM",
    "Thing":                 "CONCEPT",
}

# Property name → expected category (fallback when typeOf is absent)
PROP_TO_CATEGORY = {
    "activitySource":       "ENUM",
    "economicSector":       "ENUM",
    "measurementQualifier": "ENUM",
    "measuredProperty":     "PROPERTY",
    "populationType":       "CONCEPT",
    "statType":             "STAT_TYPE",
}

COLORS = {
    "STAT_VAR":    "#6c63ff",
    "ENUM":        "#1D9E75",
    "CONCEPT":     "#0F6E56",
    "PROPERTY":    "#5F5E5A",
    "STAT_TYPE":   "#444441",
    "PLACE":       "#BA7517",
    "OBSERVATION": "#00d4aa",
    "TIME":        "#7F77DD",
    "GROUP":       "#854F0B",
    "PEERGROUP":   "#993C1D",
    "UNKNOWN":     "#374151",
}

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
    "GROUP":       "dot",
    "PEERGROUP":   "dot",
    "UNKNOWN":     "dot",
}

# ── API helpers ─────────────────────────────────────────────────────────────

def fetch_out(dcid, proxy=PROXY):
    try:
        r = requests.get(f"{proxy}/triples/out/{dcid}", timeout=20)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        print(f"  ERR fetching {dcid}: {e}", file=sys.stderr)
        return {}

def fetch_stat_series(dcid, place):
    try:
        r = requests.get(
            "https://indiadatacommons.org/api/stat/series",
            params={"stat_var": dcid, "place": place},
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return {}

def extract_name(triples_out):
    """Get human-readable name from API triples (no hardcoding)."""
    for v in triples_out.get("name", []):
        if isinstance(v, dict):
            val = v.get("value", "") or v.get("name", "")
            if val: return val
        elif isinstance(v, str) and v:
            return v
    return ""

def infer_category(dcid, types_list, prop_context=None):
    """
    Infer node category from API-returned typeOf values.
    Falls back to prop_context (which prop linked to this node) if typeOf absent.
    """
    for t in (types_list or []):
        cat = TYPEOF_TO_CATEGORY.get(str(t))
        if cat: return cat
    if prop_context:
        return PROP_TO_CATEGORY.get(prop_context, "UNKNOWN")
    return "UNKNOWN"


# ── Graph builder ────────────────────────────────────────────────────────────

def build_graph(seed_dcids, place, max_obs, proxy=PROXY):
    nodes = {}
    edges = []
    seen  = set()

    def add_node(nid, label, category, tooltip=""):
        if nid in nodes: return
        nodes[nid] = {
            "id":       nid,
            "label":    label,
            "type":     category,
            "level":    LEVELS.get(category, 3),
            "tooltip":  tooltip or nid,
        }

    def add_edge(src, tgt, prop):
        # Strict direction: never allow dimension → StatVar, no loops
        if nodes.get(tgt, {}).get("type") == "STAT_VAR": return
        k = f"{src}|{prop}|{tgt}"
        if k in seen: return
        seen.add(k)
        edges.append({"from": src, "to": tgt, "prop": prop})

    # Shared Place node
    place_name = place.split("/")[-1]  # "IND" from "country/IND"
    add_node(place, f"{place_name}\n({place})", "PLACE",
             tooltip=f"DCID: {place}\nType: Place\n\nAll observations fetched for this place.")

    # ── STAT_VAR nodes + dimension edges ────────────────────────────────────
    print(f"\nBuilding graph for {len(seed_dcids)} DCIDs, place={place}\n")

    for dcid in seed_dcids:
        print(f"  Fetching triples: {dcid[:60]}")
        out = fetch_out(dcid)
        time.sleep(0.3)

        # Name from API (no hardcoding)
        name = extract_name(out) or dcid.split("_")[-1][:30]

        # Tooltip from real API properties
        lines = [f"DCID: {dcid}", f"Name: {name}", "Type: STAT_VAR", ""]
        for prop in STATVAR_OUTGOING:
            vals = out.get(prop, [])
            if isinstance(vals, list) and vals:
                v = vals[0]
                if isinstance(v, dict):
                    val = v.get("name", "") or v.get("dcid", "") or v.get("value", "")
                    if val: lines.append(f"{prop}: {val}")
        lines.append("\nSource: indiadatacommons.org")

        add_node(dcid, name, "STAT_VAR", tooltip="\n".join(lines))

        # Dimension edges: StatVar → Enum/Concept/Propertyz
        for prop in STATVAR_OUTGOING:
            vals = out.get(prop, [])
            if not isinstance(vals, list): continue
            for v in vals:
                if not isinstance(v, dict) or not v.get("dcid"): continue
                tdcid  = v["dcid"]
                tname  = v.get("name", "") or v.get("value", "") or tdcid
                ttypes = v.get("types", [])

                # Infer type from API typeOf, fall back to prop context
                category = infer_category(tdcid, ttypes, prop_context=prop)
                label    = tname[:35] if tname else tdcid[:35]
                tooltip  = (f"DCID: {tdcid}\nType: {category}\n"
                            f"Name: {tname}\nLinked via: {prop}\n"
                            f"\nSource: indiadatacommons.org")

                add_node(tdcid, label, category, tooltip=tooltip)
                add_edge(dcid, tdcid, prop)
                print(f"    [{category}] {prop} → {label}")

    # ── Observation layer ────────────────────────────────────────────────────
    print(f"\n  Fetching observations (place={place})...")

    for dcid in seed_dcids:
        print(f"  {dcid[:60]}")
        data = fetch_stat_series(dcid, place)
        time.sleep(0.4)

        series = {}
        if isinstance(data, dict):
            inner = data.get("data", data.get("series", {}))
            if isinstance(inner, dict):
                for pk in [place, place.split("/")[-1], list(inner.keys())[0] if inner else ""]:
                    if pk and pk in inner:
                        series = inner[pk]
                        break
                if not series and inner:
                    first = list(inner.values())[0]
                    if isinstance(first, dict):
                        series = first

        if series:
            all_years = sorted(series.keys(), reverse=True)
            years = all_years if max_obs == 0 else all_years[:max_obs]
            print(f"    {len(series)} total years, using {len(years)}")

            for yr in years:
                val    = series[yr]
                obs_id = f"obs::{dcid}::{yr}"
                add_node(obs_id, f"Obs {yr}", "OBSERVATION",
                         tooltip=(f"Type: OBSERVATION\nStatVar: {dcid}\n"
                                  f"Year: {yr}\nPlace: {place}\n"
                                  f"Value: {val} Crore"))
                add_edge(dcid, obs_id, "hasObservation")

                time_id = f"time::{yr}"
                add_node(time_id, yr, "TIME",
                         tooltip=f"Type: TIME\nYear: {yr}")
                add_edge(obs_id, time_id, "forYear")
                add_edge(obs_id, place, "forPlace")
        else:
            obs_id = f"obs::{dcid}::none"
            add_node(obs_id, "No data\nreturned", "OBSERVATION",
                     tooltip=(f"StatVar: {dcid}\nPlace: {place}\n"
                              f"No observation data returned from API.\n"
                              f"Endpoint: /api/stat/series"))
            add_edge(dcid, obs_id, "hasObservation")
            add_edge(obs_id, place, "forPlace")
            print(f"    No data")

    print(f"\nGraph: {len(nodes)} nodes, {len(edges)} edges")
    return nodes, edges


# ── HTML generator ───────────────────────────────────────────────────────────

def generate_html(nodes, edges, output, place, max_obs):
    vis_nodes = []
    for d in nodes.values():
        c     = COLORS.get(d["type"], "#888780")
        size  = {"STAT_VAR":28,"ENUM":16,"CONCEPT":16,"PROPERTY":12,
                 "STAT_TYPE":10,"PLACE":18,"OBSERVATION":13,"TIME":11,
                 "GROUP":12,"PEERGROUP":12}.get(d["type"], 12)
        shape = SHAPES.get(d["type"], "dot")
        vis_nodes.append({
            "id":d["id"], "label":d["label"], "title":d["tooltip"],
            "level":d["level"],
            "color":{"background":c,"border":c,"highlight":{"background":"#fff"}},
            "size":size, "shape":shape,
            "font":{"color":"#e6edf3","size":11,"multi":True},
        })

    edge_palette = {
        "hasObservation": "#00d4aa",
        "forYear":        "#7F77DD",
        "forPlace":       "#BA7517",
    }
    vis_edges = []
    for i, e in enumerate(edges):
        ec = edge_palette.get(e["prop"], "#4b5563")
        vis_edges.append({
            "id":i, "from":e["from"], "to":e["to"], "label":e["prop"],
            "arrows":"to",
            "color":{"color":ec,"highlight":"#9ca3af"},
            "font":{"color":"#555","size":9,"strokeWidth":0},
            "width":1.5,
        })

    config_note = (f"place={place} · "
                   f"max_obs={'all' if max_obs==0 else max_obs} · "
                   f"{len(set(d['id'] for d in vis_nodes if d['label']))} nodes · "
                   f"{len(vis_edges)} edges")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>India Data Commons — Semantic Graph</title>
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
.legend{{top:10px;left:10px;min-width:260px}}
.leg{{display:flex;align-items:center;gap:8px;margin-bottom:6px;line-height:1.4;font-size:11px}}
.dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.info{{top:10px;right:10px;max-width:360px;display:none;line-height:1.8;white-space:pre-wrap;word-break:break-all;max-height:78vh;overflow-y:auto}}
.info.show{{display:block}}
.info strong{{color:#58a6ff;display:block;margin-bottom:6px}}
.bar{{bottom:10px;left:10px;font-size:11px;color:#8b949e}}
</style>
</head>
<body>
<header>
  <h1>India Data Commons — Semantic Knowledge Graph</h1>
  <span class="badge">Source: indiadatacommons.org</span>
  <span class="badge">{config_note}</span>
</header>
<div class="ctrl">
  <button id="bh" class="on" onclick="setLayout('h')">Hierarchical (LR)</button>
  <button id="bf" onclick="setLayout('f')">Force-directed</button>
  <button onclick="net.fit()">Fit view</button>
  <span style="margin-left:8px;color:#444">ENUM/CONCEPT/PROPERTY → STAT_VAR → OBSERVATION → TIME + PLACE</span>
</div>
<div id="wrap">
  <div id="net"></div>
  <div class="panel legend">
    <div style="font-size:10px;color:#8b949e;margin-bottom:8px;letter-spacing:.5px">NODE TYPES</div>
    <div class="leg"><div class="dot" style="background:#6c63ff"></div>STAT_VAR — statistical variable</div>
    <div class="leg"><div class="dot" style="background:#1D9E75"></div>ENUM — dimension value</div>
    <div class="leg"><div class="dot" style="background:#0F6E56"></div>CONCEPT — domain concept</div>
    <div class="leg"><div class="dot" style="background:#5F5E5A"></div>PROPERTY — measured property</div>
    <div class="leg"><div class="dot" style="background:#444441"></div>STAT_TYPE — stat computation</div>
    <div class="leg"><div class="dot" style="background:#BA7517"></div>PLACE — geographic place</div>
    <div class="leg"><div class="dot" style="background:#00d4aa"></div>OBSERVATION — data point</div>
    <div class="leg"><div class="dot" style="background:#7F77DD"></div>TIME — fiscal year</div>
    <div style="font-size:10px;color:#8b949e;margin-top:10px;line-height:1.7;border-top:1px solid #30363d;padding-top:8px">
      <div>Node types inferred from API typeOf</div>
      <div>Labels from API name property</div>
      <div>No hardcoded labels or node types</div>
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
const hOpts={{layout:{{hierarchical:{{direction:'LR',sortMethod:'directed',levelSeparation:220,nodeSpacing:70,treeSpacing:150}}}},physics:{{enabled:false}},edges:{{smooth:{{type:'cubicBezier',forceDirection:'horizontal',roundness:.4}},arrows:{{to:{{enabled:true,scaleFactor:.6}}}}}},nodes:{{borderWidth:2}},interaction:{{hover:true,tooltipDelay:60,navigationButtons:true,keyboard:true}}}};
const fOpts={{layout:{{hierarchical:false}},physics:{{solver:'barnesHut',barnesHut:{{gravitationalConstant:-14000,centralGravity:.4,springLength:200,springConstant:.04,damping:.1}},maxVelocity:50,minVelocity:.1,stabilization:{{iterations:400}}}},edges:{{smooth:{{type:'dynamic'}},arrows:{{to:{{enabled:true,scaleFactor:.6}}}}}},nodes:{{borderWidth:2}},interaction:{{hover:true,tooltipDelay:60,navigationButtons:true,keyboard:true}}}};
let net=new vis.Network(document.getElementById('net'),{{nodes:ns,edges:es}},hOpts);
function bind(){{
  net.on('click',p=>{{
    const info=document.getElementById('info');
    if(p.nodes.length){{const n=ND.find(x=>x.id===p.nodes[0]);document.getElementById('it').textContent=n.label.replace(/\\n/g,' ');document.getElementById('ib').textContent=n.title;info.classList.add('show');}}
    else info.classList.remove('show');
  }});
  net.on('afterDrawing',()=>{{document.getElementById('bar').textContent=ND.length+' nodes · '+ED.length+' edges · Click any node for details';}});
  net.on('stabilizationIterationsDone',()=>{{net.fit();}});
}}
bind(); setTimeout(()=>net.fit(),400);
function setLayout(m){{document.getElementById('bh').classList.toggle('on',m==='h');document.getElementById('bf').classList.toggle('on',m==='f');net.destroy();net=new vis.Network(document.getElementById('net'),{{nodes:ns,edges:es}},m==='h'?hOpts:fOpts);bind();setTimeout(()=>net.fit(),300);}}
</script></body></html>"""

    with open(output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved: {output} ({os.path.getsize(output)//1024} KB)")


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Build India Data Commons semantic knowledge graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Pass DCIDs directly:
  python build_idc_graph_v6.py \\
    --dcids RealValue_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService \\
            Nominal_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService

  # Pass from file (one DCID per line):
  python build_idc_graph_v6.py --dcids-file seeds.txt

  # Different place (Karnataka state):
  python build_idc_graph_v6.py --dcids-file seeds.txt --place geoId/29

  # Fetch all years (no sampling):
  python build_idc_graph_v6.py --dcids-file seeds.txt --max-obs 0
        """
    )
    p.add_argument("--dcids", nargs="+", metavar="DCID",
                   help="One or more DCIDs to use as graph seeds")
    p.add_argument("--dcids-file", metavar="FILE",
                   help="Text file with one DCID per line (or JSON array)")
    p.add_argument("--place", default="country/IND",
                   help="Place DCID for observations (default: country/IND)")
    p.add_argument("--max-obs", type=int, default=5,
                   help="Max observations per StatVar; 0 = all (default: 5)")
    p.add_argument("--output", default="idc_graph.html",
                   help="Output HTML file (default: idc_graph.html)")
    p.add_argument("--proxy", default="http://localhost:5050",
                   help="Flask proxy URL (default: http://localhost:5050)")
    return p.parse_args()


def load_dcids(args):
    if args.dcids:
        return [d.strip() for d in args.dcids if d.strip()]
    if args.dcids_file:
        with open(args.dcids_file, encoding="utf-8") as f:
            content = f.read().strip()
        if content.startswith("["):
            return json.loads(content)
        return [line.strip() for line in content.splitlines()
                if line.strip() and not line.startswith("#")]
    # Default: the 4 known MOSPI DCIDs (only used if no args provided)
    print("No --dcids or --dcids-file provided. Using default MOSPI DCIDs.")
    return [
        "RealValue_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService",
        "RealValue_Amount_EconomicActivity_GrossValueAdded_PublicAdministrationAndDefenceOrMOSPIOtherService",
        "Nominal_Amount_EconomicActivity_GrossValueAdded_MOSPIOtherService",
        "Nominal_Amount_EconomicActivity_GrossValueAdded_PublicAdministrationAndDefenceOrMOSPIOtherService",
    ]


if __name__ == "__main__":
    args = parse_args()
    proxy = args.proxy

    try:
        requests.get(f"{proxy}/triples/out/test", timeout=5)
    except:
        print(f"ERROR: Proxy not reachable at {proxy}")
        print("Start it with: python app.py")
        sys.exit(1)

    seed_dcids = load_dcids(args)
    print(f"Seeds: {len(seed_dcids)} DCIDs")
    for d in seed_dcids:
        print(f"  {d}")

    graph_nodes, graph_edges = build_graph(seed_dcids, args.place, args.max_obs, proxy)

    json_out = args.output.replace(".html", ".json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({"nodes": list(graph_nodes.values()),
                   "edges": graph_edges,
                   "config": {"place": args.place, "max_obs": args.max_obs,
                               "dcids": seed_dcids}},
                  f, indent=2, ensure_ascii=False)
    print(f"Saved: {json_out}")

    generate_html(graph_nodes, graph_edges, args.output, args.place, args.max_obs)
    print(f"Open: {args.output}")