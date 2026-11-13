import os, struct, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ug2map import patch, routes
T = os.environ.get("UG2_TRACKS"); NEED = unittest.skipUnless(T and os.path.isdir(T), "set UG2_TRACKS to an unmodified TRACKS folder")

class DataToolTests(unittest.TestCase):
    @NEED
    def test_patch_with_expectation(self):
        off = 96; old = patch.peek(T, "L4RA.BUN", 0x34250, 0, off, 4)["rows"][0]["hex"][:8]; out = tempfile.mkdtemp()
        patch.apply(T, out, [{"file": "L4RA.BUN", "magic": 0x34250, "index": 0, "offset": off, "old": old, "new": struct.pack("<f", 5.0).hex()}])
        self.assertEqual(patch.peek(out, "L4RA.BUN", 0x34250, 0, off, 4)["rows"][0]["f32"], [5.0])
        with self.assertRaises(AssertionError): patch.apply(T, out, [{"file": "L4RA.BUN", "magic": 0x34250, "index": 0, "offset": off, "old": "deadbeef", "new": "00000000"}], dry=True)

    @NEED
    def test_route_nodes(self):
        files = routes.list_files(T); self.assertTrue(files)
        n = routes.nodes(T, "L4RA", files[0]["file"]); out = tempfile.mkdtemp()
        routes.apply(T, out, "L4RA", [{"file": files[0]["file"], "index": 0, "pos": [1.0, 2.0]}])
        self.assertEqual([round(v) for v in routes.nodes(out, "L4RA", files[0]["file"])[0][1:]], [1, 2]); self.assertEqual(len(routes.nodes(out, "L4RA", files[0]["file"])), len(n))

if __name__ == "__main__": unittest.main()
