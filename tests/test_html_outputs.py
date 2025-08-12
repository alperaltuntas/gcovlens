import sys, re, os
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


def href_points_to(doc: str, filename: str) -> bool:
    """Return True if any href attribute points to a path whose basename == filename."""
    for m in re.finditer(r'href=[\'"]([^\'"]+)[\'"]', doc):
        if os.path.basename(m.group(1)) == filename:
            return True
    return False


def get_row_attr(doc: str, line: int, attr: str):
    """Extract attribute value from the <tr id='L{line}'> row (attributes in any order)."""
    import re
    # capture the opening tag for the row with id="L{line}"
    m = re.search(rf'<tr\b[^>]*\bid=[\'"]L{line}[\'"][^>]*>', doc)
    if not m:
        return None
    start_tag = m.group(0)
    m2 = re.search(rf'\b{attr}=[\'"]([^\'"]+)[\'"]', start_tag)
    return m2.group(1) if m2 else None


def test_single_html_highlights_and_minimap(tmp_path: Path):
    a_dir = tmp_path / "runA"
    # line 1: nonexec, line 2: uncovered (0), line 3: covered (3)
    write_gcov(
        a_dir / "file1.gcov",
        "src/file1.c",
        [
            ("-", 1, "// header"),
            ("0", 2, "int x = 0;"),
            ("3", 3, "x++;"),
        ],
    )
    out = tmp_path / "report.html"
    rc = run_cli([str(a_dir), "--format", "html", "-o", str(out)])
    assert rc == 0 and out.exists()

    # summary should default-sort by File and show caret
    summary_html = out.read_text(encoding="utf-8")
    assert "gcovlens Report" in summary_html
    assert 'aria-sort="asc"' in summary_html or "aria-sort='asc'" in summary_html

    # detail page exists (exclude auto-written index.html in details dir)
    details_dir = out.with_name(out.stem + "_files")
    assert details_dir.exists()
    detail_pages = [p for p in details_dir.glob("*.html") if p.name != "index.html"]
    assert len(detail_pages) == 1
    detail_html = detail_pages[0].read_text(encoding="utf-8")

    # minimap present
    assert "id='minimap'" in detail_html or 'id="minimap"' in detail_html

    # breadcrumb links back to actual summary filename (accept relative hrefs)
    assert href_points_to(detail_html, out.name)

    # covered rows get light-green background; uncovered rows get light-red
    assert "#e6ffed" in detail_html  # green background style is present
    assert "#ffebee" in detail_html  # red background style is present

    # minimap uses machine-friendly states for sync
    assert 'data-state="covered"' in detail_html or "data-state='covered'" in detail_html
    assert 'data-state="uncovered"' in detail_html or "data-state='uncovered'" in detail_html


def test_diff_html_detail_changes_and_same_state(tmp_path: Path):
    a_dir = tmp_path / "A"
    b_dir = tmp_path / "B"
    # Line 5: A uncovered -> B covered (change)
    # Line 6: A covered -> B covered (unchanged exec)
    # Line 7: nonexec both
    write_gcov(a_dir / "f.gcov", "src/f.c", [("#####", 5, "f();"), ("1", 6, "g();"), ("-", 7, "// comment")])
    write_gcov(b_dir / "f.gcov", "src/f.c", [("1", 5, "f();"), ("2", 6, "g();"), ("-", 7, "// comment")])

    out = tmp_path / "diff.html"
    rc = run_cli([str(a_dir), str(b_dir), "--format", "html", "-o", str(out)])
    assert rc == 0 and out.exists()

    # locate the detail page for src/f.c
    details_dir = out.with_name(out.stem + "_files")
    assert details_dir.exists()
    fname = gl.sanitize_detail_name("src/f.c")
    detail_html = (details_dir / fname).read_text(encoding="utf-8")

    # minimap present
    assert "id='minimap'" in detail_html or 'id="minimap"' in detail_html

    # became_covered marked and row highlighted green
    assert "became_covered" in detail_html
    assert "#e6ffed" in detail_html  # green exists for changes

    # unchanged executable line (L6) should NOT be a change; accept current or older markup
    state_l6 = get_row_attr(detail_html, 6, "data-state")
    assert state_l6 in ("same", "covered"), f"Unexpected data-state for L6: {state_l6!r}"

    # breadcrumb back to diff summary (accept relative)
    assert href_points_to(detail_html, out.name)
