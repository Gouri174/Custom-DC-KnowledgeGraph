from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

IDC_BASE = "https://indiadatacommons.org/api/node/triples"

def fetch_triples(direction, dcid):
    url = f"{IDC_BASE}/{direction}/{dcid}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()

def build_graph(dcid, depth, visited):
    if depth == 0 or dcid in visited:
        return {}
    visited.add(dcid)
    try:
        out_data = fetch_triples("out", dcid)
    except:
        out_data = {}
    graph = {
        "dcid": dcid,
        "out": out_data,
        "children": []
    }
    for prop, values in out_data.items():
        if prop == "dcid":
            continue
        for v in values[:3]:
            next_dcid = v.get("dcid")
            if next_dcid and next_dcid not in visited:
                child_graph = build_graph(next_dcid, depth - 1, visited)
                if child_graph:
                    graph["children"].append(child_graph)
    return graph

@app.route("/triples/<direction>/<path:dcid>")
def get_triples(direction, dcid):
    if direction not in ("out", "in"):
        return jsonify({"error": "direction must be 'out' or 'in'"}), 400
    url = f"{IDC_BASE}/{direction}/{dcid}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/graph/<path:dcid>")
def get_graph(dcid):
    depth = int(request.args.get("depth", 2))
    visited = set()
    try:
        graph = build_graph(dcid, depth, visited)
        return jsonify(graph)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("India Data Commons proxy running at http://localhost:5050")
    app.run(port=5050, debug=False)