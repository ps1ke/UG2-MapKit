import base64, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ug2map import projects as P

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 40).decode()

class ProjectTests(unittest.TestCase):
    def setUp(self): self.ws = P.Workspace(tempfile.mkdtemp())

    def test_assets_roundtrip(self):
        ops = [{"op": "replace_texture", "key": "1", "image": PNG}, {"op": "add_mesh", "tris": [[0] * 9], "uvs": [[0] * 6], "key": 1}, {"op": "remove_object", "id": 3}]
        self.ws.save_edits({"ops": ops})
        raw = open(os.path.join(self.ws.dir("my-project"), "edits.json")).read()
        self.assertNotIn(PNG, raw)                       # bulky data lives in assets/
        self.assertEqual(self.ws.load_edits()["ops"][0]["image"], PNG)
        self.assertEqual(self.ws.load_edits()["ops"][1]["tris"], [[0] * 9])

    def test_export_import_and_conflicts(self):
        self.ws.new("pack-a", "Pack A"); self.ws.save_edits({"ops": [{"op": "remove_object", "region": "L4RA", "id": 5}]}, "pack-a")
        z = self.ws.export_zip("pack-a"); other = P.Workspace(tempfile.mkdtemp())
        self.assertEqual(other.import_zip(z), "pack-a")
        with self.assertRaises(ValueError): other.import_zip(z)          # same id again
        other.save_edits({"ops": [{"op": "remove_object", "region": "L4RA", "id": 5}]})
        ops, rep = other.effective_ops(other.load_edits()["ops"])
        self.assertEqual(len(ops), 2); self.assertTrue(any("CONFLICT" in r for r in rep))
        other.set_stack([{"id": "pack-a", "enabled": False}]); ops, rep = other.effective_ops(other.load_edits()["ops"])
        self.assertEqual(len(ops), 1); self.assertEqual(rep, [])

    def test_dependencies(self):
        self.ws.new("lib", "Lib"); self.ws.new("addon", "Addon"); self.ws.save_meta("addon", {"requires": [{"id": "lib", "version": ">=2"}]})
        self.ws.set_stack([{"id": "addon", "enabled": True}, {"id": "lib", "enabled": True}])
        probs = [p for s in self.ws.stack() for p in s["problems"]]
        self.assertTrue(any("lib" in p for p in probs))

    def test_rejects_unsafe_zip(self):
        import io, zipfile
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z: z.writestr("project.json", '{"id":"evil"}'); z.writestr("edits.json", "{}"); z.writestr("../x.txt", "x")
        with self.assertRaises(ValueError): self.ws.import_zip(b.getvalue())

    def test_versions(self):
        self.assertTrue(P.version_ok("1.2.0", ">=1.0,<2")); self.assertFalse(P.version_ok("2.0", ">=1.0,<2"))

if __name__ == "__main__": unittest.main()

class UpdateTests(unittest.TestCase):
    def test_check(self):
        from ug2map import updates, __version__
        d = tempfile.mkdtemp(); cf = os.path.join(d, "u.json")
        r = updates.check(cf, fetch=lambda: "99.0.0"); self.assertTrue(r["newer"])
        r = updates.check(cf, fetch=lambda: (_ for _ in ()).throw(AssertionError("must use the cache"))); self.assertEqual(r["latest"], "99.0.0")
        r = updates.check(None, fetch=lambda: __version__); self.assertFalse(r["newer"])
        r = updates.check(None, fetch=lambda: 1 / 0); self.assertFalse(r["newer"])      # offline: silent
