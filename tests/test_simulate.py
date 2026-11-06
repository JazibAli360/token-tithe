"""Phase 3: simulate runs offline, validates fixtures, never writes to GitHub."""

from token_tithe.simulate import simulate


def test_simulate_passes_and_makes_no_writes(tmp_path):
    report = simulate(out_root=tmp_path)
    assert report.ok, report.render()
    names = {s.name: s.status for s in report.stages}
    assert names["world"] == names["baseline"] == names["no GitHub writes"] == "PASS"
    assert names["capacity → execute → review queue"] == "PASS"
    assert all(st != "PENDING" for st in names.values()), "every stage is built"
    assert names["learn → apply → inject"] == "PASS"
    assert (report.run_dir / "report.txt").is_file()
    assert not (report.run_dir / "repos").exists()
