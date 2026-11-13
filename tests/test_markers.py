import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ug2map import markers
TRACKS = os.environ.get("UG2_TRACKS")

class MarkerTests(unittest.TestCase):
    def test_hash(self): self.assertEqual(markers.H("ForwardStartingPosition"), markers.BY_NAME["ForwardStartingPosition"])

    @unittest.skipUnless(TRACKS and os.path.isdir(TRACKS), "set UG2_TRACKS to an unmodified TRACKS folder")
    def test_move_and_add(self):
        h = markers.BY_NAME["CC_PERF_SHOP"]; out = tempfile.mkdtemp(); n0 = len(markers.list_markers(TRACKS))
        markers.apply(TRACKS, out, "L4RA", [{"op": "move_marker", "hash": h, "index": 0, "pos": [1, 2, 3]}, {"op": "add_marker", "hash": h, "pos": [4, 5, 6]}])
        ms = [m for m in markers.list_markers(out) if m["hash"] == h]
        self.assertEqual(len(markers.list_markers(out)), n0 + 1); self.assertEqual([round(v) for v in ms[0]["pos"]], [1, 2, 3])

if __name__ == "__main__": unittest.main()
