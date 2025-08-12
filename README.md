# gcovlens

Generate coverage and diff reports directly from directories of GCC `.gcov` files.

- Diff mode (two dirs) and Single-run mode (one dir)
- HTML & Markdown outputs
- Per-file HTML detail pages
- Sorting with visible carets (▲ ▼) — default sort: filename (A→Z)
- Preserves non-exec (`-`) and no-data (`=====`) lines for context
- Optional hiding of blank/comment lines in detail pages
- Optional syntax highlighting via highlight.js (GitHub themes)
- Adjustable UI font size, code font, and line-height
- A clickable, scrollable minimap for quick navigation

## Install

```bash
git clone https://github.com/alperaltuntas/gcovlens
cd gcovlens
pip install -e .
```

> No Python dependencies required. Detail page syntax highlighting loads **highlight.js** from a CDN when `--syntax=hljs` is used.

## Usage

### Single-run (one directory)
Generate a single coverage report and per-file detail pages:
```bash
gcovlens path/to/runA
```

### Diff mode (two directories)
Compare Run A vs Run B, with a summary and detail pages:
```bash
gcovlens path/to/runA path/to/runB
```

The HTML writer also creates per-file details in a sibling directory:
- `report.html` → `report_files/`
- `diff.html` → `diff_files/`

### Markdown output
```bash
gcovlens path/to/runA path/to/runB --format md -o diff.md
gcovlens path/to/runA --format md -o report.md
```

### Common options
```
--details-dir DIR       # where detail pages go (default: <output>_files)
--display-blank       In detail pages, show whitespace-only lines (non-exec/no-data only).
--strip-comments        # hide comment-only lines (heuristic; same constraint as above)
--syntax {off,hljs}     # syntax highlighting in detail pages (default: hljs)
--syntax-theme {github,github-dark}
--ui-font-size PX       # base UI font size
--code-font-size PX     # code font size (detail pages)
--code-line-height N    # code line-height
```

### Examples
```bash
# Diff report with details and dark theme
gcovlens ./gcov/runA ./gcov/runB -o diff.html --syntax hljs --syntax-theme github-dark

# Single-run HTML report with larger UI font and tighter code lines
gcovlens ./gcov/runA -o report.html --ui-font-size 16 --code-font-size 12 --code-line-height 1.2

# Markdown diff with line lists
gcovlens ./gcov/runA ./gcov/runB --format md --show-lines -o diff.md
```

## Notes

- Input must be directories containing GCC-generated `.gcov` files.
- The parser keeps non-executable (`-`) and no-data (`=====`) lines to preserve code context.
- The summary tables sort **alphabetically by file** on load, and clicking column headers toggles sort with ▲/▼ carets.
- Detail pages use a single `<pre><code>` block per line cell, so text wraps and alignment stays clean.
- When `--syntax=hljs`, the detail pages link to highlight.js over a CDN; if you need fully offline HTML, use `--syntax=off`.

## Uninstall
```bash
pip uninstall gcovlens
```
