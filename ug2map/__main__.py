"""UG2 MapKit command line.

  python -m ug2map serve <TRACKS dir> [--workspace DIR] [--install <game TRACKS dir>] [--port 8765]
  python -m ug2map build <TRACKS dir> [--workspace DIR] [--install <game TRACKS dir>]
  python -m ug2map info  <TRACKS dir>

<TRACKS dir> is the TRACKS folder of your (unmodified) NFS Underground 2 install. It is only ever READ."""
import argparse, glob, http.server, json, os, shutil, socketserver, threading, time, webbrowser
from . import relocate, bun, world, server, edit, markers, projects

def regions(tracks): return sorted(os.path.basename(f)[6:-4] for f in glob.glob(os.path.join(tracks, "STREAML4R*.BUN")))

def cmd_info(a):
    for r in regions(a.tracks):
        t = bun.parse(open(os.path.join(a.tracks, f"STREAM{r}.BUN"), "rb").read()); print(r, "objects", len(world.load_objects(t)), "collision sections", len(world.load_collisions(t)))

def build(tracks, out, ops, dry=False, getdata=None):
    by = {}
    for o in ops: by.setdefault(o.get("region", "L4RA"), []).append(o)
    report = []
    if not dry:
        shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)          # the output holds ONLY the files that changed
    MK = ("move_marker", "add_marker")
    for reg, lst in list(by.items()):
        mk = [o for o in lst if o["op"] in MK]
        if mk: report += [f"[{reg}] markers: " + r for r in markers.apply(tracks, out, reg, mk, dry=dry)]
        by[reg] = [o for o in lst if o["op"] not in MK]
    for reg, lst in by.items():
        if not lst: continue
        fn = os.path.join(tracks, f"STREAM{reg}.BUN"); raw = open(fn, "rb").read(); t = bun.parse(raw); report += [f"[{reg}] " + r for r in edit.apply(t, lst, getdata=getdata, topo=edit.Topology(open(os.path.join(tracks, f"{reg}.BUN"), "rb").read()))]
        data, starts = bun.serialize_map(t); tbl = None
        old = bun.parse(raw); tf = os.path.join(tracks, f"{reg}.BUN"); tb0 = open(tf, "rb").read()      # ALWAYS re-address: a chunk that grows inside absorbed padding still changes its block's size
        t2 = relocate.relocate(tb0, old, len(raw), len(data), starts)
        if t2 != tb0:
            tbl = t2; report.append(f"[{reg}] chunk sizes changed (file {len(data) - len(raw):+d} bytes) -> block table in {reg}.BUN re-addressed")
        if not dry:
            if tbl is not None:
                tdst = os.path.join(out, f"{reg}.BUN")
                if os.path.exists(tdst) and os.stat(tdst).st_nlink > 1: os.remove(tdst)
                open(tdst + ".new", "wb").write(tbl); os.replace(tdst + ".new", tdst)
            tmp = os.path.join(out, f"STREAM{reg}.BUN.new"); dst = os.path.join(out, f"STREAM{reg}.BUN")
            if os.path.exists(dst) and os.stat(dst).st_nlink > 1: os.remove(dst)
            open(tmp, "wb").write(data); os.replace(tmp, dst)
    return report


def changed_files(out):
    """every file the build wrote (relative paths)"""
    return [os.path.relpath(os.path.join(r, f), out) for r, _, fs in os.walk(out) for f in fs]

def install(out, game_tracks, backups):
    """copy the built files into the game's TRACKS folder; the first time a file is replaced its original is saved under backups/"""
    n = 0
    for rel in changed_files(out):
        dst = os.path.join(game_tracks, rel); bak = os.path.join(backups, rel)
        if os.path.exists(dst) and not os.path.exists(bak): os.makedirs(os.path.dirname(bak), exist_ok=True); shutil.copy2(dst, bak)
        os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(os.path.join(out, rel), dst + ".new"); os.replace(dst + ".new", dst); n += 1
    return n

def restore(game_tracks, backups):
    n = 0
    for r, _, fs in os.walk(backups):
        for f in fs:
            rel = os.path.relpath(os.path.join(r, f), backups); shutil.copyfile(os.path.join(backups, rel), os.path.join(game_tracks, rel) + ".new"); os.replace(os.path.join(game_tracks, rel) + ".new", os.path.join(game_tracks, rel)); n += 1
    return n

def cmd_build(a):
    ws = projects.Workspace(a.workspace); out = os.path.join(a.workspace, "build", "TRACKS"); cache = {}
    def D(reg):
        if reg not in cache: cache[reg] = server.TrackData(a.tracks, None, track=reg, log=lambda m: print(f"[{reg}] {m}", flush=True))
        return cache[reg]
    ops, rep = ws.effective_ops(ws.load_edits()["ops"]); print("\n".join(rep))
    print("\n".join(build(a.tracks, out, ops, getdata=D))); print("built", len(changed_files(out)), "file(s) into", out)
    if a.install: print("installed", install(out, a.install, os.path.join(a.workspace, "backups")), "file(s) into", a.install)

def cmd_serve(a):
    cache = {}; lock = threading.Lock(); ws = projects.Workspace(a.workspace); out = os.path.join(a.workspace, "build", "TRACKS"); backups = os.path.join(a.workspace, "backups")
    def D(reg):
        with lock:
            if reg not in cache:
                cache[reg] = server.TrackData(a.tracks, None, track=reg, log=lambda m: print(f"[{reg}] {m}", flush=True))
                cache[reg].overview_json = json.dumps(cache[reg].overview()).encode(); cache[reg].info_json = json.dumps({"bounds": cache[reg].bounds, "sections": cache[reg].sections()}).encode()
            return cache[reg]
    html = lambda: open(os.path.join(os.path.dirname(__file__), "viewer.html"), "rb").read()
    regs = regions(a.tracks); D("L4RA")
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *x): pass
        def _send(self, body, ctype="application/json", code=200, headers=None):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items(): self.send_header(k, v)
            self.end_headers(); self.wfile.write(body)
        def _err(self, e, code=400): self._send(json.dumps({"ok": False, "error": str(e)}).encode(), code=code)
        def do_GET(self):
            path = self.path.split("?")[0]; q = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&")) if "?" in self.path else {}; reg = q.get("region", "L4RA")
            try:
                if path == "/api/regions": self._send(json.dumps({"regions": regs, "traces": [], "install": bool(a.install)}).encode())
                elif path == "/api/info": self._send(D(reg).info_json)
                elif path == "/api/overview": self._send(D(reg).overview_json)
                elif path == "/api/search": self._send(json.dumps(D(reg).search(q.get("q", ""))).encode())
                elif path == "/api/textures": self._send(json.dumps(D(reg).textures()).encode())
                elif path == "/api/tex.png":
                    b = D(reg).thumb(int(q["key"]), int(q.get("s", 64))); self._send(b, "image/png") if b else self.send_error(404)
                elif path == "/api/backdrop": self._send(json.dumps(D(reg).backdrop(q.get("kind", "all"))).encode())
                elif path == "/api/groundz": z = D(reg).ground_z_near(float(q["x"]), float(q["y"])); self._send(json.dumps({"z": z}).encode())
                elif path == "/api/edits": self._send(json.dumps(ws.load_edits()).encode())
                elif path == "/api/projects": self._send(json.dumps(ws.info()).encode())
                elif path == "/api/projects/export":
                    pid = q.get("id") or ws.cfg["active"]; self._send(ws.export_zip(pid), "application/zip", headers={"Content-Disposition": f'attachment; filename="{pid}.ug2proj"'})
                elif path.startswith("/static/"):
                    f = os.path.join(os.path.dirname(__file__), "static", os.path.basename(path))
                    if os.path.isfile(f): self._send(open(f, "rb").read(), "application/javascript")
                    else: self.send_error(404)
                else: self._send(html(), "text/html")
            except Exception as e: self._send(json.dumps({"error": repr(e)}).encode(), code=500)
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n); p = self.path
            if p == "/api/projects/import":
                try: pid = ws.import_zip(raw, replace="replace=1" in (self.headers.get("X-Options") or "")); self._send(json.dumps({"ok": True, "id": pid, **ws.info()}).encode())
                except Exception as e: self._err(e)
                return
            body = json.loads(raw or b"{}")
            try:
                if p == "/api/tile":
                    dd_ = D(body.get("region", "L4RA")); bt_ = bool(body.get("built")) and dd_.ensure_built(out)
                    self._send(json.dumps(dd_.tile(body["region_box"], body.get("have", []), body.get("have_protos", []), body.get("no_tex", False), bt_)).encode())
                elif p == "/api/edits": ws.save_edits(body); self._send(b'{"ok":true}')
                elif p == "/api/projects/new": ws.new(body.get("id") or body.get("name", ""), body.get("name", ""), body.get("author", ""), body.get("description", "")); pid = projects.slug(body.get("id") or body.get("name", "")); ws.open(pid); self._send(json.dumps({"ok": True, **ws.info()}).encode())
                elif p == "/api/projects/open": ws.open(body["id"]); self._send(json.dumps({"ok": True, **ws.info()}).encode())
                elif p == "/api/projects/meta": ws.save_meta(ws.cfg["active"], body); self._send(json.dumps({"ok": True, **ws.info()}).encode())
                elif p == "/api/projects/stack": ws.set_stack(body.get("stack", [])); self._send(json.dumps({"ok": True, **ws.info()}).encode())
                elif p == "/api/projects/remove": ws.remove_from_stack(body["id"], bool(body.get("delete"))); self._send(json.dumps({"ok": True, **ws.info()}).encode())
                elif p == "/api/markers": self._send(json.dumps({"markers": markers.list_markers(a.tracks, body.get("region", "L4RA")), "types": sorted(markers.NAMES.values())}).encode())
                elif p == "/api/clipcoll":
                    td_ = D(body.get("region", "L4RA")); cen_ = body["centroid"]; sz_ = body["size"]; hx_, hy_ = sz_[0] / 2 + 0.5, sz_[1] / 2 + 0.5; out_ = []; seen_ = set()
                    for sid_, c_ in td_.cols.items():
                        if c_.ox > cen_[0] + hx_ or c_.ox + c_.W * 32 < cen_[0] - hx_ or c_.oy > cen_[1] + hy_ or c_.oy + c_.H * 32 < cen_[1] - hy_: continue
                        for i_ in range(c_.npoly):
                            if edit._parked(c_, i_): continue
                            x_, y_, z_ = c_.poly(i_)
                            if x_.min() < -3500 or not (cen_[0] - hx_ <= x_.mean() <= cen_[0] + hx_ and cen_[1] - hy_ <= y_.mean() <= cen_[1] + hy_): continue
                            k_ = c_.kind(i_); key_ = (k_, tuple(x_.round(1)), tuple(y_.round(1)), tuple(z_.round(1)))
                            if key_ in seen_: continue
                            seen_.add(key_); out_.append({"k": k_, "x": [float(v) for v in x_], "y": [float(v) for v in y_], "z": [float(v) for v in z_]})
                            if len(out_) >= 8000: break
                    self._send(json.dumps(out_).encode())
                elif p == "/api/polys":
                    td_ = D(body.get("region", "L4RA")); out_ = []
                    for sid_, pi_ in body.get("polys", []):
                        c_ = td_.cols.get(int(sid_))
                        if c_ is None: continue
                        x_, y_, z_ = c_.poly(int(pi_)); out_.append({"k": c_.kind(int(pi_)), "x": [float(v) for v in x_], "y": [float(v) for v in y_], "z": [float(v) for v in z_]})
                    self._send(json.dumps(out_).encode())
                elif p == "/api/clip": self._send(json.dumps(D(body.get("region", "L4RA")).clip(body.get("items", []))).encode())
                elif p in ("/api/build", "/api/check"):
                    try:
                        ops, prep = ws.effective_ops(body.get("ops", [])); rep = prep + build(a.tracks, out, ops, dry=p == "/api/check", getdata=D); info = {"ok": True, "report": rep, "out": out}
                        if p == "/api/build":
                            files = [{"name": f, "size": os.path.getsize(os.path.join(out, f)), "mtime": time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(os.path.join(out, f))))} for f in changed_files(out)]
                            info.update({"built_at": time.strftime("%H:%M:%S"), "n_ops": len(ops), "files": files})
                        self._send(json.dumps(info).encode())
                    except AssertionError as e: self._send(json.dumps({"ok": False, "error": str(e)}).encode())
                    except Exception as e: self._send(json.dumps({"ok": False, "error": repr(e)}).encode())
                elif p == "/api/install" and a.install: self._send(json.dumps({"ok": True, "installed": install(out, a.install, backups), "to": a.install}).encode())
                elif p == "/api/restore" and a.install: self._send(json.dumps({"ok": True, "restored": restore(a.install, backups)}).encode())
                else: self.send_error(404)
            except Exception as e: self._err(e)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", a.port), H); srv.daemon_threads = True; url = f"http://127.0.0.1:{a.port}/"
    print("UG2 MapKit running at", url, "| regions:", regs, "| workspace:", ws.root, "| build output:", out, flush=True)
    if not a.no_browser: threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

def main():
    p = argparse.ArgumentParser(prog="ug2map", description="Viewer / editor / mod-project builder for NFS Underground 2 maps"); s = p.add_subparsers(dest="cmd", required=True)
    i = s.add_parser("info"); i.add_argument("tracks"); i.set_defaults(f=cmd_info)
    for name, fn in (("serve", cmd_serve), ("build", cmd_build)):
        v = s.add_parser(name); v.add_argument("tracks", help="the TRACKS folder of your unmodified game"); v.add_argument("--workspace", default="ug2-workspace", help="where projects, build output and backups live (default ./ug2-workspace)")
        v.add_argument("--install", help="optional: the TRACKS folder to copy the built files into (originals are backed up first)")
        if name == "serve": v.add_argument("--port", type=int, default=8765); v.add_argument("--no-browser", action="store_true")
        v.set_defaults(f=fn)
    a = p.parse_args(); a.f(a)

if __name__ == "__main__": main()
