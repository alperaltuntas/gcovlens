import sys
from pathlib import Path

# Make "src" importable without installing the package (works for src/gcovlens.py layout)
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

import gcovlens as gl  # type: ignore


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


def run_cli(args):
    # Normalize None (success) to 0 so assertions are stable
    rc = gl.main(args)
    return 0 if rc is None else rc


def test_single_markdown_basic(tmp_path: Path):
    """Single-run MD contains header, summary, table and correct percentages."""
    a_dir = tmp_path / "runA"
    # 1 nonexec, 1 uncovered (0), 1 covered (3) -> 1/2 = 50.0%
    write_gcov(
        a_dir / "file1.gcov",
        "src/file1.c",
        [
            ("-", 1, "// header"),
            ("0", 2, "int x = 0;"),
            ("3", 3, "x++;"),
        ],
    )
    out = tmp_path / "report.md"
    rc = run_cli([str(a_dir), "--format", "md", "-o", str(out)])
    assert rc == 0 and out.exists()

    md = out.read_text(encoding="utf-8")
    # Title and summary
    assert "# gcovlens Report" in md
    assert "**Coverage:** 50.0% (1/2)" in md
    # Table header and row for the file
    assert "| File | % Covered | Covered | Total | Uncovered |" in md
    assert "`src/file1.c`" in md
    # The row should include the expected numbers
    assert "50.0%" in md and "| 1 | 2 | 1 |" in md.replace("`src/file1.c` | ", "")


def test_diff_markdown_changed_only(tmp_path: Path):
    """Diff MD includes only changed files and shows correct A/B/Δ and line-change counts."""
    a_dir = tmp_path / "A"
    b_dir = tmp_path / "B"
    # Line 5: A uncovered -> B covered (change)
    # Line 6: A covered -> B covered (unchanged exec)
    # Line 7: nonexec both
    write_gcov(a_dir / "f.gcov", "src/f.c", [("#####", 5, "f();"), ("1", 6, "g();"), ("-", 7, "// comment")])
    write_gcov(b_dir / "f.gcov", "src/f.c", [("1", 5, "f();"), ("2", 6, "g();"), ("-", 7, "// comment")])

    out = tmp_path / "diff.md"
    rc = run_cli([str(a_dir), str(b_dir), "--format", "md", "-o", str(out)])
    assert rc == 0 and out.exists()

    md = out.read_text(encoding="utf-8")
    # Title & overall summary
    assert "# gcovlens Diff Report" in md
    assert "**Run A:** 50.0% (1/2)" in md
    assert "**Run B:** 100.0% (2/2)" in md
    assert "**Delta:** +50.0%" in md

    # File summary section and row for src/f.c
    assert "## File Summary (changed only)" in md
    assert "| File | A % | B % | Δ % | +covered | +uncovered |" in md
    # The row should reflect A=50.0, B=100.0, Δ=+50.0, +covered=1, +uncovered=0
    # Example row: | `src/f.c` | 50.0% | 100.0% | +50.0% | 1 | 0 |
    assert "`src/f.c`" in md
    assert "50.0% | 100.0% | +50.0% | 1 | 0" in md


def test_diff_markdown_show_lines(tmp_path: Path):
    """With --show-lines, the diff MD includes the 'Line-level Changes' section listing changed line numbers."""
    a_dir = tmp_path / "A2"
    b_dir = tmp_path / "B2"
    # Changes: 10 (uncovered->covered), 12 (covered->uncovered)
    write_gcov(a_dir / "g.gcov", "src/g.c", [("#####", 10, "a();"), ("1", 12, "b();")])
    write_gcov(b_dir / "g.gcov", "src/g.c", [("1", 10, "a();"), ("0", 12, "b();")])

    out = tmp_path / "diff_lines.md"
    rc = run_cli([str(a_dir), str(b_dir), "--format", "md", "--show-lines", "-o", str(out)])
    assert rc == 0 and out.exists()

    md = out.read_text(encoding="utf-8")
    assert "# gcovlens Diff Report" in md
    assert "## Line-level Changes" in md
    # Should list the file and both categories with the line numbers
    assert "### `src/g.c`" in md
    assert "Became covered:" in md and "10" in md
    assert "Became uncovered:" in md and "12" in md
