import sys, pathlib, re
from pathlib import Path

# Make "src" importable without installing the package
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import gcovlens as gl


def write_gcov(dst: Path, source_path: str, lines):
    """
    lines: list of tuples (count_token, lineno, text)
      count_token in {"-", "#####", "=====", <int as str>}
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        f.write(f"Source: {source_path}\n")
        for tok, ln, txt in lines:
            f.write(f"{tok:>9}:{ln:>5}:{txt}\n")


def test_parse_gcov_file(tmp_path: Path):
    src = tmp_path / "a.gcov"
    write_gcov(
        src,
        "src/foo.f90",
        [
            ("-", 1, "! comment"),
            ("#####", 2, "x = 0"),
            ("0", 3, "y = 0"),
            ("5", 4, "print *, 'hi'"),
            ("=====", 5, "pragma"),
        ],
    )
    cf = gl.parse_gcov_file(src)
    assert cf is not None
    assert cf.source.endswith("src/foo.f90")
    assert cf.covered == {4}
    assert cf.uncovered == {2, 3}
    assert cf.lines[1].kind == "nonexec"
    assert cf.lines[5].kind == "nodata"
    assert cf.lines[4].count == 5 and cf.lines[4].covered is True


def test_compute_diff(tmp_path: Path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_g = a_dir / "foo.gcov"
    b_g = b_dir / "foo.gcov"
    write_gcov(a_g, "src/foo.c", [("#####", 10, "foo();")])
    write_gcov(b_g, "src/foo.c", [("1", 10, "foo();")])

    a_map = gl.load_dir(a_dir)
    b_map = gl.load_dir(b_dir)
    a = a_map["src/foo.c"]
    b = b_map["src/foo.c"]
    became_cov, became_uncov = gl.compute_diff(a, b)
    assert became_cov == {10}
    assert became_uncov == set()


def test_guess_language_and_sanitize():
    assert gl.guess_language("x.F90").lower() == "fortran"
    assert gl.guess_language("x.cpp") == "cpp"
    assert gl.guess_language("x.h") == "c"
    a = gl.sanitize_detail_name("/weird path/with*chars/foo-bar.c")
    b = gl.sanitize_detail_name("/weird path/with*chars/foo-bar.c")
    c = gl.sanitize_detail_name("/different/file.c")
    assert a == b
    assert a != c
