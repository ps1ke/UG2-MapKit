"""Projects: the standard way to package, share and combine map changes.

A project is a folder (or a .ug2proj zip) holding ONLY symbolic changes, never game bytes:

    my-project/
        project.json     manifest: id, name, version, author, description, requires, conflicts, load_after
        edits.json       {"ops": [...], "groups": [...]}  keyed edit operations (see docs/PROJECTS.md)
        assets/          textures (PNG/JPG), imported models and bulk data referenced from edits.json

A workspace holds many projects plus a *stack*: the ordered list of imported projects that are switched on. The build is always
    pristine game files  +  every enabled project in order  +  the project you are editing
so switching a project off is just a rebuild, never an "undo".  The game files are never modified by the projects themselves."""
from __future__ import annotations
import base64, hashlib, io, json, os, re, shutil, time, zipfile

FORMAT = 1
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,47}$")
MAX_ZIP = 512 * 1024 * 1024
BIG_POLYS = 200                      # paste_collision polygon lists above this size go to assets/

def slug(s): return re.sub(r"[^a-z0-9_\-]+", "-", (s or "").lower()).strip("-")[:48] or "project"

def _ver(v):
    out = []
    for p in re.split(r"[.\-+]", str(v)):
        out.append(int(p) if p.isdigit() else 0)
    return tuple(out + [0] * (4 - len(out)))

def version_ok(have, rng):
    """tiny semver range check: '', '1.2', '>=1.0', '>=1.0,<2', '==1.2.3'"""
    for part in [x.strip() for x in str(rng or "").split(",") if x.strip()]:
        m = re.match(r"^(>=|<=|==|>|<|=)?\s*(.+)$", part); op, want = m.group(1) or ">=", _ver(m.group(2)); h = _ver(have)
        if not {">=": h >= want, "<=": h <= want, "==": h == want, "=": h == want, ">": h > want, "<": h < want}[op]: return False
    return True

# ---------- bulk data <-> assets ----------
def _sha(b): return hashlib.sha256(b).hexdigest()[:12]

def externalize(ops, assets_dir):
    """move bulky inline data of ops into files under assets_dir; returns new op list (inputs are not modified)"""
    out = []; os.makedirs(assets_dir, exist_ok=True)
    def put(name, data):
        p = os.path.join(assets_dir, name)
        if not os.path.exists(p): open(p, "wb").write(data)
        return "assets/" + name
    for o in ops:
        o = {k: v for k, v in o.items() if not k.startswith("_")}
        if o.get("op") in ("replace_texture", "add_texture") and isinstance(o.get("image"), str):
            raw = base64.b64decode(o["image"]); ext = "png" if raw[:4] == b"\x89PNG" else "jpg" if raw[:2] == b"\xff\xd8" else "bin"
            o["image_file"] = put(f"tex_{_sha(raw)}.{ext}", raw); del o["image"]
        elif o.get("op") == "add_mesh" and "tris" in o:
            blob = json.dumps({"tris": o["tris"], "uvs": o.get("uvs")}, separators=(",", ":")).encode()
            o["mesh_file"] = put(f"mesh_{_sha(blob)}.json", blob); del o["tris"]; o.pop("uvs", None)
        elif o.get("op") == "paste_collision" and len(o.get("polys", [])) > BIG_POLYS:
            blob = json.dumps(o["polys"], separators=(",", ":")).encode(); o["polys_file"] = put(f"coll_{_sha(blob)}.json", blob); del o["polys"]
        out.append(o)
    return out

def _safe_join(root, rel):
    p = os.path.normpath(os.path.join(root, rel))
    if not (p == root or p.startswith(root + os.sep)): raise ValueError("asset path escapes the project: " + rel)
    return p

def inflate(ops, project_dir):
    """inverse of externalize: read the asset files back into the ops (what the build engine expects)"""
    out = []
    for o in ops:
        o = dict(o)
        if "image_file" in o: o["image"] = base64.b64encode(open(_safe_join(project_dir, o.pop("image_file")), "rb").read()).decode()
        if "mesh_file" in o: d = json.load(open(_safe_join(project_dir, o.pop("mesh_file")))); o["tris"] = d["tris"]; o["uvs"] = d.get("uvs")
        if "polys_file" in o: o["polys"] = json.load(open(_safe_join(project_dir, o.pop("polys_file"))))
        out.append(o)
    return out

# ---------- conflict analysis ----------
def touched(o):
    """keys of the things an op overwrites; two projects writing the same key conflict (additive ops never do)"""
    k = o.get("op"); r = o.get("region", "L4RA")
    if k in ("remove_object", "transform_object", "set_texture"): return [("object", r, str(o.get("id")))]
    if k in ("move_instance", "duplicate_instance"): return [("instance", r, o.get("sec"), o.get("off"))]
    if k == "remove_polys": return [("polygon", r, o.get("sid"), p) for p in o.get("polys", [])]
    if k == "move_vertices": return [("vertices", r, o.get("id"), bool(o.get("proto")))]
    if k == "move_marker": return [("marker", r, o.get("hash"), o.get("index", 0))]
    if k == "replace_texture": return [("texture", str(o.get("key")))]
    if k == "add_texture": return [("new texture", o.get("name"))]
    return []

class Workspace:
    def __init__(self, root):
        self.root = os.path.abspath(root); self.pdir = os.path.join(self.root, "projects"); os.makedirs(self.pdir, exist_ok=True)
        self.cfg_path = os.path.join(self.root, "workspace.json"); self.cfg = {"active": None, "stack": []}
        if os.path.exists(self.cfg_path):
            try: self.cfg.update(json.load(open(self.cfg_path)))
            except Exception: pass
        if not self.cfg.get("active") or not os.path.isdir(self.dir(self.cfg["active"])):
            ids = self.list_ids(); self.cfg["active"] = ids[0] if ids else self.new("my-project", "My project")["id"]
        self._save()

    def _save(self): json.dump(self.cfg, open(self.cfg_path + ".tmp", "w"), indent=1); os.replace(self.cfg_path + ".tmp", self.cfg_path)
    def dir(self, pid): return os.path.join(self.pdir, pid)
    def list_ids(self): return sorted(d for d in os.listdir(self.pdir) if os.path.isfile(os.path.join(self.pdir, d, "project.json")))
    def meta(self, pid): return json.load(open(os.path.join(self.dir(pid), "project.json")))

    def new(self, pid, name="", author="", description=""):
        pid = slug(pid or name)
        if not ID_RE.match(pid): raise ValueError("project id must be lowercase letters, digits, - or _")
        d = self.dir(pid)
        if os.path.exists(d): raise ValueError(f"project '{pid}' already exists")
        os.makedirs(os.path.join(d, "assets"))
        m = {"format": FORMAT, "id": pid, "name": name or pid, "version": "0.1.0", "author": author, "description": description, "target": "nfsu2-map",
             "requires": [], "conflicts": [], "load_after": [], "created": time.strftime("%Y-%m-%d")}
        json.dump(m, open(os.path.join(d, "project.json"), "w"), indent=1); json.dump({"ops": [], "groups": []}, open(os.path.join(d, "edits.json"), "w"))
        return m

    def save_meta(self, pid, upd):
        m = self.meta(pid)
        for k in ("name", "version", "author", "description"):
            if k in upd: m[k] = str(upd[k])[:2000]
        for k in ("requires", "conflicts", "load_after"):
            if k in upd and isinstance(upd[k], list): m[k] = upd[k]
        json.dump(m, open(os.path.join(self.dir(pid), "project.json"), "w"), indent=1); return m

    def open(self, pid):
        if pid not in self.list_ids(): raise ValueError("no such project")
        self.cfg["active"] = pid; self.cfg["stack"] = [s for s in self.cfg["stack"] if s["id"] != pid]; self._save()

    def load_edits(self, pid=None):
        pid = pid or self.cfg["active"]; p = os.path.join(self.dir(pid), "edits.json")
        d = json.load(open(p)) if os.path.exists(p) else {"ops": [], "groups": []}
        d["ops"] = inflate(d.get("ops", []), self.dir(pid)); return d

    def save_edits(self, body, pid=None):
        pid = pid or self.cfg["active"]; d = self.dir(pid)
        out = {"ops": externalize(body.get("ops", []), os.path.join(d, "assets")), "groups": body.get("groups", [])}
        tmp = os.path.join(d, "edits.json.tmp"); json.dump(out, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, os.path.join(d, "edits.json"))
        self._gc(pid, out)

    def _gc(self, pid, edits):
        """delete asset files no op refers to any more"""
        used = {v for o in edits["ops"] for k, v in o.items() if k.endswith("_file") and isinstance(v, str)}; ad = os.path.join(self.dir(pid), "assets")
        for f in os.listdir(ad) if os.path.isdir(ad) else []:
            if "assets/" + f not in used and f.startswith(("tex_", "mesh_", "coll_")): os.remove(os.path.join(ad, f))

    # ---------- stack ----------
    def stack(self):
        """imported projects in load order with dependency diagnostics"""
        st = []; ids = [s["id"] for s in self.cfg["stack"] if s["id"] in self.list_ids()]; enabled = {s["id"]: s.get("enabled", True) for s in self.cfg["stack"]}
        for i, pid in enumerate(ids):
            m = self.meta(pid); probs = []
            for rq in m.get("requires", []):
                rid = rq["id"] if isinstance(rq, dict) else str(rq); rng = rq.get("version", "") if isinstance(rq, dict) else ""
                if rid == self.cfg["active"]: probs.append(f"requires '{rid}' which is the project you are editing (import it instead)"); continue
                if rid not in ids: probs.append(f"requires '{rid}' (not imported)")
                elif not enabled.get(rid, True) and enabled.get(pid, True): probs.append(f"requires '{rid}' which is switched off")
                elif not version_ok(self.meta(rid).get("version", "0"), rng): probs.append(f"requires '{rid}' {rng} but {self.meta(rid).get('version')} is imported")
                elif ids.index(rid) > i: probs.append(f"must load after '{rid}' (move it down)")
            for cf in m.get("conflicts", []):
                cid = cf["id"] if isinstance(cf, dict) else str(cf)
                if cid in ids and enabled.get(cid, True) and enabled.get(pid, True): probs.append(f"conflicts with '{cid}'")
            for la in m.get("load_after", []):
                if la in ids and ids.index(la) > i: probs.append(f"asks to load after '{la}'")
            st.append({"id": pid, "enabled": enabled.get(pid, True), "meta": m, "problems": probs})
        return st

    def set_stack(self, items):
        have = set(self.list_ids()); self.cfg["stack"] = [{"id": s["id"], "enabled": bool(s.get("enabled", True))} for s in items if s["id"] in have and s["id"] != self.cfg["active"]]; self._save()

    def remove_from_stack(self, pid, delete=False):
        self.cfg["stack"] = [s for s in self.cfg["stack"] if s["id"] != pid]; self._save()
        if delete and pid != self.cfg["active"] and os.path.isdir(self.dir(pid)): shutil.rmtree(self.dir(pid))

    def effective_ops(self, active_ops):
        """ops of every enabled imported project (in order, tagged with their project) followed by the active project's ops; plus a report"""
        ops = []; owners = {}; report = []
        for s in self.stack():
            if not s["enabled"]: continue
            for p in s["problems"]: report.append(f"[{s['id']}] WARNING: {p}")
            for o in self.load_edits(s["id"])["ops"]:
                o["_project"] = s["id"]; ops.append(o)
                for k in touched(o): owners.setdefault(k, []).append(s["id"])
        for o in active_ops:
            o = dict(o); o["_project"] = self.cfg["active"]; ops.append(o)
            for k in touched(o): owners.setdefault(k, []).append(self.cfg["active"])
        seen = set()
        for k, who in owners.items():
            if len(set(who)) > 1 and (k, tuple(who)) not in seen:
                seen.add((k, tuple(who))); report.append(f"CONFLICT {k[0]} {', '.join(str(x) for x in k[1:])}: changed by {', '.join(dict.fromkeys(who))}: the later one wins ({who[-1]})")
        return ops, report

    # ---------- share ----------
    def export_zip(self, pid):
        d = self.dir(pid); buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(".tmp"): continue
                    p = os.path.join(root, f); z.write(p, os.path.relpath(p, d))
        return buf.getvalue()

    def import_zip(self, data, replace=False):
        if len(data) > MAX_ZIP: raise ValueError("project too large")
        z = zipfile.ZipFile(io.BytesIO(data))
        if sum(i.file_size for i in z.infolist()) > MAX_ZIP * 2: raise ValueError("project too large when unpacked")
        names = z.namelist()
        for n in names:
            if n.startswith("/") or ".." in n.split("/") or "\\" in n: raise ValueError("unsafe path in project: " + n)
            if not (n in ("project.json", "edits.json", "README.md", "LICENSE") or n.startswith("assets/")): raise ValueError("unexpected file in project: " + n)
        if "project.json" not in names or "edits.json" not in names: raise ValueError("not a UG2 MapKit project (project.json / edits.json missing)")
        m = json.loads(z.read("project.json")); pid = m.get("id", "")
        if m.get("format", 1) > FORMAT: raise ValueError("this project needs a newer UG2 MapKit")
        if not ID_RE.match(pid): raise ValueError("invalid project id in the file")
        dst = self.dir(pid)
        if os.path.exists(dst):
            if not replace: raise ValueError(f"project '{pid}' already exists (version {self.meta(pid).get('version')}); import with replace to update it")
            if pid == self.cfg["active"]: raise ValueError("cannot replace the project you are editing")
            shutil.rmtree(dst)
        os.makedirs(dst)
        for n in names:
            if n.endswith("/"): continue
            p = _safe_join(dst, n); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(z.read(n))
        os.makedirs(os.path.join(dst, "assets"), exist_ok=True)
        if not any(s["id"] == pid for s in self.cfg["stack"]): self.cfg["stack"].append({"id": pid, "enabled": True})
        self._save(); return pid

    def info(self):
        a = self.cfg["active"]; return {"active": {"id": a, "meta": self.meta(a)}, "stack": self.stack(), "available": [{"id": i, "name": self.meta(i).get("name", i), "version": self.meta(i).get("version", "")} for i in self.list_ids()]}
