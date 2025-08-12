#!/usr/bin/env python3
"""
gcovlens — coverage and diff reports from directories of GCC .gcov files

Features
- Diff mode (two dirs) and Single-run mode (one dir)
- HTML & Markdown outputs
- Per-file HTML detail pages
- Client-side sorting with visible carets (▲ ▼)
- Default sort: alphabetical by File (caret visible on load)
- Text/code left-aligned; numeric columns right-aligned
- Preserves non-exec ('-') and no-data ('=====') lines for context
- Optional hiding of blank/comment lines in detail pages
- Optional syntax highlighting on detail pages via highlight.js (GitHub themes)
- Adjustable UI font size, code font size, and code line-height
- Detail pages' breadcrumb links back to the actual summary HTML filename
- Detail pages include a right-side clickable, draggable minimap for quick navigation
"""
import argparse
import sys
from pathlib import Path
import os
import re
from typing import Dict, Set, Tuple, Optional, List, Any
import html
import hashlib

LINE_RE = re.compile(r'^\s*(?P<count>-|#{5,}|={5,}|\d+)\s*:\s*(?P<lineno>\d+)\s*:(?P<rest>.*)$')
HEADER_RE = re.compile(r'^\s*(?P<key>Source|Graph|Data|Runs):\s*(?P<val>.*)$')

CHANGE_THRESHOLD = 0.05  # percentage points

class LineInfo:
    __slots__ = ("lineno", "count", "covered", "text", "kind")
    def __init__(self, lineno: int, count: Optional[int], covered: Optional[bool], text: str, kind: str):
        # kind: 'covered', 'uncovered', 'nonexec', 'nodata'
        self.lineno = lineno
        self.count = count
        self.covered = covered
        self.text = text
        self.kind = kind

class CoverageFile:
    def __init__(self, source: str):
        self.source = source
        self.covered: Set[int] = set()
        self.uncovered: Set[int] = set()
        self.lines: Dict[int, LineInfo] = {}

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.uncovered)

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 100.0
        return 100.0 * len(self.covered) / self.total

def parse_gcov_file(path: Path) -> Optional[CoverageFile]:
    source: Optional[str] = None
    covered: Set[int] = set()
    uncovered: Set[int] = set()
    lines: Dict[int, LineInfo] = {}

    try:
        with path.open('r', encoding='utf-8', errors='replace') as f:
            for raw in f:
                m = HEADER_RE.match(raw)
                if m and m.group('key') == 'Source':
                    source = m.group('val').strip()
                    source = str(Path(source))
                    continue

                lm = LINE_RE.match(raw)
                if not lm:
                    continue

                count_tok = lm.group('count')
                lineno = int(lm.group('lineno'))
                text = lm.group('rest').rstrip('\n')

                if count_tok == '-':
                    # Non-executable line, keep for context
                    lines[lineno] = LineInfo(lineno, None, None, text, 'nonexec')
                    continue

                if count_tok.startswith('====='):
                    # No data available (e.g., not compiled)
                    lines[lineno] = LineInfo(lineno, None, None, text, 'nodata')
                    continue

                if count_tok.startswith('#####'):
                    uncovered.add(lineno)
                    lines[lineno] = LineInfo(lineno, 0, False, text, 'uncovered')
                else:
                    try:
                        n = int(count_tok)
                    except ValueError:
                        continue
                    if n > 0:
                        covered.add(lineno)
                        lines[lineno] = LineInfo(lineno, n, True, text, 'covered')
                    else:
                        uncovered.add(lineno)
                        lines[lineno] = LineInfo(lineno, 0, False, text, 'uncovered')
    except Exception as e:
        print(f"WARNING: failed to parse {path}: {e}", file=sys.stderr)
        return None

    if source is None:
        source = path.stem

    cf = CoverageFile(source)
    cf.covered = covered
    cf.uncovered = uncovered
    cf.lines = lines
    return cf

def load_dir(d: Path) -> Dict[str, CoverageFile]:
    mapping: Dict[str, CoverageFile] = {}
    for p in d.rglob('*.gcov'):
        cf = parse_gcov_file(p)
        if cf is None:
            continue
        mapping[cf.source] = cf  # last wins
    # if mapping is empty, see if there is a codecov directory
    if not mapping:
        codecov_dir = d / 'codecov'
        if codecov_dir.exists() and codecov_dir.is_dir():
            return load_dir(codecov_dir)
    return mapping

def format_pct(x: float) -> str:
    return f"{x:.1f}%"

def compute_diff(a: CoverageFile, b: CoverageFile):
    all_exec_lines = a.covered | a.uncovered | b.covered | b.uncovered
    became_covered = (b.covered - a.covered) & all_exec_lines
    became_uncovered = (b.uncovered - a.uncovered) & all_exec_lines
    return became_covered, became_uncovered

def aggregate_totals(files: List[CoverageFile]):
    cov = sum(len(x.covered) for x in files)
    tot = sum(x.total for x in files)
    pct = (100.0 * cov / tot) if tot else 100.0
    return cov, tot, pct

def aggregate_totals_pairs(pairs: List[Tuple[CoverageFile, CoverageFile]]):
    cov_a = sum(len(x.covered) for x, _ in pairs)
    tot_a = sum(x.total for x, _ in pairs)
    cov_b = sum(len(y.covered) for _, y in pairs)
    tot_b = sum(y.total for _, y in pairs)
    pct_a = (100.0 * cov_a / tot_a) if tot_a else 100.0
    pct_b = (100.0 * cov_b / tot_b) if tot_b else 100.0
    delta = pct_b - pct_a
    return cov_a, tot_a, pct_a, cov_b, tot_b, pct_b, delta

def sanitize_detail_name(source: str) -> str:
    """Return a filesystem-safe, stable file name for a detail page."""
    tail = Path(source).name
    h = hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]
    safe_tail = re.sub(r'[^A-Za-z0-9_.-]+', '_', tail)
    return f"{safe_tail}__{h}.html"

def guess_language(source_path: str) -> str:
    """Best-effort mapping from file extension to highlight.js language class."""
    s = source_path.lower()
    mapping = [
        (('f90','f95','f03','f08','f','for','f77'), 'fortran'),
        (('hpp','hh','hxx','cpp','cc','cxx','cuh','cu'), 'cpp'),
        (('h',), 'c'),
        (('c',), 'c'),
        (('py',), 'python'),
        (('sh','bash'), 'bash'),
        (('js',), 'javascript'),
        (('ts',), 'typescript'),
        (('java',), 'java'),
        (('go',), 'go'),
        (('rs',), 'rust'),
    ]
    for exts, lang in mapping:
        for ext in exts:
            if s.endswith('.' + ext):
                return lang
    return ''  # unknown => no explicit language

def html_head(title: str, syntax: str = 'off', theme: str = 'github',
              ui_font_size: Optional[int] = None, code_font_size: float = 12, code_line_height: float = 1.25) -> str:
    ui_font_rule = f"font-size: {ui_font_size}px;" if ui_font_size else ""
    css = """
    <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; {UI_FONT_RULE} }
    h1, h2, h3 { margin: 0.6em 0 0.4em; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    tr:nth-child(even) { background: #f9f9f9; }
    .delta-pos { color: #006400; font-weight: 600; }
    .delta-neg { color: #8B0000; font-weight: 600; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; background: #eee; margin-right: 8px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
    a.filelink { text-decoration: none; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#eee; margin-left:8px; font-weight:600; }
    .covered { color: #0b6; }
    .uncovered { color: #b00; }
    .breadcrumbs a { text-decoration:none; }
    .header { display:flex; justify-content:space-between; align-items:center; margin-bottom: 10px; }
    pre { margin: 0; white-space: pre-wrap; }
    /* numeric cells right-aligned */
    td.num, th.num { text-align: right; }
    /* sortable headers caret */
    th.sortable { cursor: pointer; user-select: none; }
    th.sortable .caret { display:inline-block; margin-left:6px; opacity:0.7; }
    th[aria-sort="asc"] .caret::after { content: "▲"; }
    th[aria-sort="desc"] .caret::after { content: "▼"; }
    th[aria-sort="none"] .caret::after { content: ""; }
    /* subtle styles for non-exec/no-data lines (optional) */
    tr.nonexec td, tr.nodata td { color: #555; }

    /* === Slim code overrides (keep the pre-highlight look) === */
    pre, code, pre code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: {CODE_FONT_SIZE}px;
      line-height: {CODE_LINE_HEIGHT};
    }
    code.hljs { padding: 0; background: transparent; font-weight: 400; }
    .hljs { background: transparent; }
    .hljs * { font-weight: 400; }

    /* === Minimap === */
    .minimap {
      position: fixed;
      right: 10px;
      top: 84px;
      width: 16px;
      height: calc(100vh - 120px);
      border-radius: 6px;
      background: #f3f4f6;
      box-shadow: inset 0 0 0 1px rgba(0,0,0,0.08);
      z-index: 999;
      cursor: grab;
    }
    .minimap.dragging { cursor: grabbing; }
    .minimap .seg {
      position: absolute;
      left: 0; width: 100%;
      opacity: 0.9;
    }
    .minimap .seg.covered { background: #a7f3d0; }         /* light green */
    .minimap .seg.uncovered { background: #fecaca; }       /* light red */
    .minimap .seg.nonexec, .minimap .seg.nodata { background: #e5e7eb; } /* gray */
    .minimap .seg.same { background: #e5e7eb; }            /* neutral gray for unchanged exec lines */
    .minimap .seg.became_covered { background: #34d399; }  /* green */
    .minimap .seg.became_uncovered { background: #f87171; }/* red */
    .minimap .view {
      position: absolute;
      left: 0; width: 100%;
      border: 1px solid rgba(0,0,0,0.35);
      background: rgba(0,0,0,0.08);
      border-radius: 3px;
      pointer-events: none;
    }
    </style>
    """
    css = css.replace("{UI_FONT_RULE}", ui_font_rule)
    css = css.replace("{CODE_FONT_SIZE}", str(code_font_size))
    css = css.replace("{CODE_LINE_HEIGHT}", str(code_line_height))

    js_sorter = """
    <script>
    (function(){
      function getCellValue(td, type){
        let t = td.textContent.trim();
        if(type === 'percent'){ return parseFloat(t.replace('%','')) || 0; }
        if(type === 'num'){ return parseFloat(t) || 0; }
        return t.toLowerCase();
      }
      function compare(a, b, type, dir){
        if(type === 'alpha'){
          if(a < b) return dir==='asc' ? -1 : 1;
          if(a > b) return dir==='asc' ? 1 : -1;
          return 0;
        } else {
          return dir==='asc' ? (a - b) : (b - a);
        }
      }
      function makeSortable(table){
        const thead = table.tHead;
        if(!thead) return;
        const headers = thead.rows[0].cells;
        for(let i=0;i<headers.length;i++){
          const th = headers[i];
          const type = th.getAttribute('data-sort');
          if(!type) continue;
          th.classList.add('sortable');
          if(!th.querySelector('.caret')){
            const caret = document.createElement('span');
            caret.className = 'caret';
            th.appendChild(caret);
          }
          if(!th.hasAttribute('aria-sort')) th.setAttribute('aria-sort','none');
          th.addEventListener('click', function(){
            const tbody = table.tBodies[0];
            const rows = Array.from(tbody.rows);
            const current = th.getAttribute('aria-sort') || 'none';
            for(const h of headers){ if(h!==th) h.setAttribute('aria-sort', 'none'); }
            const dir = current === 'asc' ? 'desc' : 'asc';
            th.setAttribute('aria-sort', dir);
            rows.sort((r1, r2)=>{
              const v1 = getCellValue(r1.cells[i], type);
              const v2 = getCellValue(r2.cells[i], type);
              return compare(v1, v2, type, dir);
            });
            for(const r of rows){ tbody.appendChild(r); }
          });
        }
        // Apply initial sort if any header declares aria-sort asc/desc
        for(let i=0;i<headers.length;i++){
          const th = headers[i];
          const type = th.getAttribute('data-sort');
          const dir = th.getAttribute('aria-sort');
          if(type && dir && dir !== 'none'){
            const tbody = table.tBodies[0];
            const rows = Array.from(tbody.rows);
            rows.sort((r1, r2)=>{
              const v1 = (r1.cells[i] ? r1.cells[i].textContent.trim() : '');
              const v2 = (r2.cells[i] ? r2.cells[i].textContent.trim() : '');
              function parseVal(t){
                if(type === 'percent'){ return parseFloat(t.replace('%','')) || 0; }
                if(type === 'num'){ return parseFloat(t) || 0; }
                return t.toLowerCase();
              }
              const a = parseVal(v1), b = parseVal(v2);
              if(type === 'alpha'){
                if(a < b) return dir==='asc' ? -1 : 1;
                if(a > b) return dir==='asc' ? 1 : -1;
                return 0;
              } else {
                return dir==='asc' ? (a - b) : (b - a);
              }
            });
            for(const r of rows){ tbody.appendChild(r); }
            break;
          }
        }
      }
      document.addEventListener('DOMContentLoaded', function(){
        document.querySelectorAll('table.sortable').forEach(makeSortable);
      });
    })();
    </script>
    """

    js_minimap = """
    <script>
    (function(){
      function clamp(v,a,b){ return Math.max(a,Math.min(b,v)); }

      function initMinimap() {
        const mini = document.getElementById('minimap');
        if (!mini) return;
        const table = document.querySelector('table.sortable');
        if (!table || !table.tBodies[0]) return;
        const rows = Array.from(table.tBodies[0].rows);
        if (!rows.length) return;

        // Build segments
        const total = rows.length;
        const segs = [];
        let curState = null, startIdx = 0;
        function rowState(r){
          const s = r.getAttribute('data-state');
          if (s) return s;
          if (r.classList.contains('nonexec')) return 'nonexec';
          if (r.classList.contains('nodata')) return 'nodata';
          return 'covered';
        }
        rows.forEach((r, i) => {
          const st = rowState(r);
          if (curState === null) { curState = st; startIdx = i; return; }
          if (st !== curState) { segs.push([startIdx, i, curState]); curState = st; startIdx = i; }
        });
        segs.push([startIdx, rows.length, curState]);

        mini.innerHTML = '';
        const frag = document.createDocumentFragment();
        segs.forEach(([s, e, st]) => {
          const seg = document.createElement('div');
          seg.className = 'seg ' + st;
          seg.style.top = (s / total * 100) + '%';
          seg.style.height = (((e - s) / total) * 100) + '%';
          seg.title = st;
          seg.addEventListener('click', () => {
            const target = rows[Math.min(s, rows.length - 1)];
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          });
          frag.appendChild(seg);
        });
        const view = document.createElement('div');
        view.className = 'view';
        frag.appendChild(view);
        mini.appendChild(frag);

        // View math
        let viewFrac = 0;
        function tableAbsTop(el){ const r = el.getBoundingClientRect(); return r.top + window.scrollY; }
        function updateView(){
          const tableTop = tableAbsTop(table);
          const rect = table.getBoundingClientRect();
          const tableHeight = table.scrollHeight || rect.height || 1;
          const scrollTop = window.scrollY;
          const vh = window.innerHeight || document.documentElement.clientHeight || 0;
          const interTop = Math.max(tableTop, Math.min(tableTop + tableHeight, scrollTop));
          const interBottom = Math.max(tableTop, Math.min(tableTop + tableHeight, scrollTop + vh));
          const interHeight = Math.max(0, interBottom - interTop);
          const topFrac = Math.max(0, Math.min(1, (interTop - tableTop) / tableHeight));
          viewFrac = Math.max(0, Math.min(1, interHeight / tableHeight));
          const safeTop = Math.max(0, Math.min(1 - viewFrac, topFrac));
          view.style.top = (safeTop * 100) + '%';
          view.style.height = (viewFrac * 100) + '%';
        }

        // Scroll + drag (separate RAFs)
        let dragging = false;
        let rafScroll = 0;
        let rafDrag = 0;

        function scrollToFraction(frac){
          const rect = table.getBoundingClientRect();
          const tableTop = window.scrollY + rect.top;
          const tableHeight = table.scrollHeight || rect.height || 1;
          const vh = window.innerHeight || document.documentElement.clientHeight || 0;
          const maxScroll = Math.max(0, tableHeight - vh);
          const target = tableTop + Math.max(0, Math.min(1, frac)) * maxScroll;
          window.scrollTo({ top: target, behavior: 'auto' });
          requestAnimationFrame(updateView);
        }

        function onDrag(clientY){
          const mrect = mini.getBoundingClientRect();
          const H = Math.max(1, mrect.height);
          const y = Math.max(0, Math.min(H, clientY - mrect.top));
          const pos = y / H;
          const topFrac = Math.max(0, Math.min(1 - viewFrac, pos - viewFrac/2));
          scrollToFraction(topFrac);
        }

        function onMouseMove(e){
          if (!dragging) return;
          if (rafDrag) cancelAnimationFrame(rafDrag);
          rafDrag = requestAnimationFrame(() => onDrag(e.clientY));
        }

        // Initial + reactive updates
        updateView();
        window.addEventListener('scroll', () => {
          if (rafScroll) return;
          rafScroll = requestAnimationFrame(() => { rafScroll = 0; updateView(); });
        }, { passive: true });
        window.addEventListener('resize', updateView);

        // Mouse
        mini.addEventListener('mousedown', (e) => {
          dragging = true;
          mini.classList.add('dragging');
          onDrag(e.clientY);
          e.preventDefault();
        });
        document.addEventListener('mousemove', onMouseMove, { passive: false });
        document.addEventListener('mouseup', () => {
          dragging = false;
          mini.classList.remove('dragging');
          requestAnimationFrame(updateView);
        });

        // Touch
        mini.addEventListener('touchstart', (e) => {
          if (!e.touches.length) return;
          dragging = true;
          mini.classList.add('dragging');
          onDrag(e.touches[0].clientY);
          e.preventDefault();
        }, { passive: false });
        document.addEventListener('touchmove', (e) => {
          if (!dragging || !e.touches.length) return;
          if (rafDrag) cancelAnimationFrame(rafDrag);
          rafDrag = requestAnimationFrame(() => onDrag(e.touches[0].clientY));
          e.preventDefault();
        }, { passive: false });
        document.addEventListener('touchend', () => {
          dragging = false;
          mini.classList.remove('dragging');
          requestAnimationFrame(updateView);
        });
      }

      document.addEventListener('DOMContentLoaded', initMinimap);
    })();
    </script>
    """

    # Optional syntax highlighting (via CDN)
    syntax_bits = ""
    if syntax == 'hljs':
        theme_map = {
            'github': 'github.min.css',
            'github-dark': 'github-dark.min.css',
        }
        theme_file = theme_map.get(theme, 'github.min.css')
        syntax_bits = (
            f"<link rel='stylesheet' href='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/{theme_file}'>"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js'></script>"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/fortran.min.js'></script>"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/c.min.js'></script>"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/cpp.min.js'></script>"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/python.min.js'></script>"
            "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/bash.min.js'></script>"
            "<script>document.addEventListener('DOMContentLoaded', function(){ if(window.hljs){ hljs.highlightAll(); } });</script>"
        )
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>{css}{js_sorter}{js_minimap}{syntax_bits}</head>"

def is_blank(text: str) -> bool:
    return text.strip() == ""

def is_comment(text: str) -> bool:
    s = text.lstrip()
    if not s:
        return False
    return s.startswith('!') or s.startswith('//') or s.startswith('#') or s.startswith('/*') or s.startswith('*/')

def write_diff_detail_page(outpath: Path, source: str, a: CoverageFile, b: CoverageFile,
                           display_blank: bool, strip_comments: bool, syntax: str, theme: str,
                           ui_font_size: Optional[int], code_font_size: float, code_line_height: float,
                           breadcrumb_href: str):
    all_lines = sorted(set(a.lines.keys()) | set(b.lines.keys()))
    rows = []
    for ln in all_lines:
        la = a.lines.get(ln)
        lb = b.lines.get(ln)
        a_state = 'missing' if la is None else la.kind
        b_state = 'missing' if lb is None else lb.kind
        a_cnt = '' if la is None or la.count is None else str(la.count)
        b_cnt = '' if lb is None or lb.count is None else str(lb.count)

        # Prefer a non-empty code snippet from B, else A; fall back as needed
        if lb is not None and lb.text and lb.text.strip():
            text = lb.text
        elif la is not None and la.text and la.text.strip():
            text = la.text
        elif lb is not None:
            text = lb.text or ''
        elif la is not None:
            text = la.text or ''
        else:
            text = ''

        # Only hide (never blank cells): for non-exec/no-data/missing on BOTH sides,
        # hide if whitespace-only is to be hidden or comment-only is to be hidden.
        hide_blank = not display_blank
        if ((hide_blank and is_blank(text)) or (strip_comments and is_comment(text))):
            nonexec_like_a = la is None or a_state in ('nonexec', 'nodata', 'missing')
            nonexec_like_b = lb is None or b_state in ('nonexec', 'nodata', 'missing')
            if nonexec_like_a and nonexec_like_b:
                continue

        status = 'same'
        if a_state != b_state:
            if b_state == 'covered' and a_state != 'covered':
                status = 'became_covered'
            elif b_state == 'uncovered' and a_state != 'uncovered':
                status = 'became_uncovered'
            else:
                status = 'changed'
        rows.append((ln, a_cnt, b_cnt, a_state, b_state, text, status))

    parts = [html_head(f"gcovlens Detail — {source}", syntax=syntax, theme=theme,
                       ui_font_size=ui_font_size, code_font_size=code_font_size, code_line_height=code_line_height),
             "<body>"]
    parts.append(f"<h1>gcovlens Detail — {html.escape(source)}</h1>")
    covA = a.percent; covB = b.percent; delta = covB - covA
    link_html = f"<a href='{html.escape(breadcrumb_href)}'>gcovlens Report</a>"
    parts.append(f"""
    <div class="header">
      <div class="breadcrumbs">
        {link_html} / <strong>{html.escape(source)}</strong>
      </div>
      <div>
        <span class="pill">A: {covA:.1f}%</span>
        <span class="pill">B: {covB:.1f}%</span>
        <span class="pill">Δ: {delta:+.1f}%</span>
      </div>
    </div>
    """)
    parts.append("<div id='minimap' class='minimap' title='Click or drag to navigate'></div>")

    lang_cls = ('language-' + guess_language(source)) if guess_language(source) else ''
    parts.append("<table class='sortable'><thead><tr>"
                 "<th class='num' data-sort='num' aria-sort='asc'>Line<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>A count<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>B count<span class='caret'></span></th>"
                 "<th data-sort='alpha' aria-sort='none'>A state<span class='caret'></span></th>"
                 "<th data-sort='alpha' aria-sort='none'>B state<span class='caret'></span></th>"
                 "<th data-sort='alpha' aria-sort='none'>Code<span class='caret'></span></th>"
                 "</tr></thead><tbody>")
    for (ln, a_cnt, b_cnt, a_state, b_state, text, status) in rows:
        classes = []
        if a_state == 'nonexec' or b_state == 'nonexec':
            classes.append('nonexec')
        if a_state == 'nodata' or b_state == 'nodata':
            classes.append('nodata')
        row_style = " style='background:#e6ffed'" if status=='became_covered' else (" style='background:#ffebee'" if status=='became_uncovered' else "")
        # minimap state:
        if status in ('became_covered', 'became_uncovered'):
            state_for_minimap = status
        else:
            if (a_state in ('nonexec', 'nodata', 'missing')) and (b_state in ('nonexec', 'nodata', 'missing')):
                state_for_minimap = 'nonexec' if ('nonexec' in (a_state, b_state)) else 'nodata'
            else:
                state_for_minimap = 'same'
        attrs = f" data-line='{ln}' data-state='{html.escape(str(state_for_minimap))}' id='L{ln}'"
        parts.append(
            f"<tr{attrs} class=\"{' '.join(classes)}\"{row_style}><td class='num'>{ln}</td>"
            f"<td class='num'>{html.escape(str(a_cnt))}</td>"
            f"<td class='num'>{html.escape(str(b_cnt))}</td>"
            f"<td>{html.escape(str(a_state))}</td>"
            f"<td>{html.escape(str(b_state))}</td>"
            f"<td><pre><code class='hljs {lang_cls}'>{html.escape(text)}</code></pre></td></tr>"
        )
    parts.append("</tbody></table></body></html>")
    outpath.write_text("\n".join(parts), encoding='utf-8')

def write_single_detail_page(outpath: Path, source: str, cf: CoverageFile,
                             display_blank: bool, strip_comments: bool, syntax: str, theme: str,
                             ui_font_size: Optional[int], code_font_size: float, code_line_height: float,
                             breadcrumb_href: str):
    lines = []
    for ln in sorted(cf.lines.keys()):
        li = cf.lines[ln]
        # filter non-exec/no-data if requested (hide-only; never blank cells)
        if li.kind in ('nonexec','nodata'):
            txt = li.text
            hide_blank = not display_blank
            if (hide_blank and is_blank(txt)) or (strip_comments and is_comment(txt)):
                continue
        # human-readable state
        if li.kind == 'nonexec':
            state = 'non-exec'
        elif li.kind == 'nodata':
            state = 'no-data'
        elif li.kind == 'covered':
            state = 'covered'
        elif li.kind == 'uncovered':
            state = 'uncovered'
        else:
            state = li.kind or ''
        lines.append((ln, li.count if li.count is not None else '', state, li.text, li.kind))

    parts = [html_head(f"gcovlens Detail — {source}", syntax=syntax, theme=theme,
                       ui_font_size=ui_font_size, code_font_size=code_font_size, code_line_height=code_line_height),
             "<body>"]
    parts.append(f"<h1>gcovlens Detail — {html.escape(source)}</h1>")
    link_html = f"<a href='{html.escape(breadcrumb_href)}'>gcovlens Report</a>"
    parts.append(f"""
    <div class="header">
      <div class="breadcrumbs">
        {link_html} / <strong>{html.escape(source)}</strong>
      </div>
      <div>
        <span class="pill">Coverage: {cf.percent:.1f}%</span>
        <span class="pill">Covered: {len(cf.covered)}</span>
        <span class="pill">Total: {cf.total}</span>
      </div>
    </div>
    """)
    parts.append("<div id='minimap' class='minimap' title='Click or drag to navigate'></div>")

    lang_cls = ('language-' + guess_language(source)) if guess_language(source) else ''
    parts.append("<table class='sortable'><thead><tr>"
                 "<th class='num' data-sort='num' aria-sort='asc'>Line<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>Count<span class='caret'></span></th>"
                 "<th data-sort='alpha' aria-sort='none'>State<span class='caret'></span></th>"
                 "<th data-sort='alpha' aria-sort='none'>Code<span class='caret'></span></th>"
                 "</tr></thead><tbody>")

    for ln, cnt, state, text, kind in lines:
        classes = []
        if kind == 'nonexec':
            classes.append('nonexec')
        if kind == 'nodata':
            classes.append('nodata')

        # Highlight covered/uncovered
        if kind == 'covered':
            row_style = " style='background:#e6ffed'"
        elif kind == 'uncovered':
            row_style = " style='background:#ffebee'"
        else:
            row_style = ""

        data_state = kind if kind in ('covered','uncovered','nonexec','nodata') else 'covered'

        attrs = f" data-line='{ln}' data-state='{html.escape(data_state)}' id='L{ln}'"
        parts.append(
            f"<tr{attrs} class=\"{' '.join(classes)}\"{row_style}><td class='num'>{ln}</td>"
            f"<td class='num'>{html.escape(str(cnt))}</td>"
            f"<td>{html.escape(state)}</td>"
            f"<td><pre><code class='hljs {lang_cls}'>{html.escape(text)}</code></pre></td></tr>"
        )
    parts.append("</tbody></table></body></html>")
    outpath.write_text("\n".join(parts), encoding='utf-8')

def to_markdown_diff(rows, totals, show_lines):
    cov_a, tot_a, pct_a, cov_b, tot_b, pct_b, delta = totals
    md = []
    md.append("# gcovlens Diff Report\n")
    md.append(f"**Run A:** {format_pct(pct_a)} ({cov_a}/{tot_a})  \n**Run B:** {format_pct(pct_b)} ({cov_b}/{tot_b})  \n**Delta:** {delta:+.1f}%\n")
    md.append("\n## File Summary (changed only)\n")
    md.append("| File | A % | B % | Δ % | +covered | +uncovered |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        md.append(f"| `{r['file']}` | {format_pct(r['a_pct'])} | {format_pct(r['b_pct'])} | {r['delta']:+.1f}% | {len(r['became_covered'])} | {len(r['became_uncovered'])} |")
    if show_lines and rows:
        md.append("\n## Line-level Changes\n")
        for r in rows:
            if not r['became_covered'] and not r['became_uncovered']:
                continue
            md.append(f"\n### `{r['file']}`\n")
            if r['became_covered']:
                md.append("**Became covered:** " + ", ".join(map(str, sorted(r['became_covered']))))
            if r['became_uncovered']:
                md.append("\n**Became uncovered:** " + ", ".join(map(str, sorted(r['became_uncovered']))))
            md.append("")
    return "\n".join(md)

def to_markdown_single(files: List[CoverageFile]):
    cov, tot, pct = aggregate_totals(files)
    md = []
    md.append("# gcovlens Report\n")
    md.append(f"**Coverage:** {format_pct(pct)} ({cov}/{tot})\n")
    md.append("\n## File Summary\n")
    md.append("| File | % Covered | Covered | Total | Uncovered |")
    md.append("|---|---:|---:|---:|---:|")
    for cf in sorted(files, key=lambda x: x.percent):
        md.append(f"| `{cf.source}` | {format_pct(cf.percent)} | {len(cf.covered)} | {cf.total} | {len(cf.uncovered)} |")
    return "\n".join(md)

def to_html_diff(rows, totals, detail_links: Dict[str, str],
                 ui_font_size: Optional[int], code_font_size: float, code_line_height: float):
    cov_a, tot_a, pct_a, cov_b, tot_b, pct_b, delta = totals
    parts = [html_head("gcovlens Diff", ui_font_size=ui_font_size, code_font_size=code_font_size, code_line_height=code_line_height),
             "<body>", "<h1>gcovlens Diff Report</h1>"]
    parts.append(f"""
    <div class="grid">
      <div class="badge">Run A: {pct_a:.1f}% ({cov_a}/{tot_a})</div>
      <div class="badge">Run B: {pct_b:.1f}% ({cov_b}/{tot_b})</div>
      <div class="badge">Δ: <span class="{ 'delta-pos' if (pct_b-pct_a)>=0 else 'delta-neg' }">{(pct_b-pct_a):+.1f}%</span></div>
    </div>
    """)
    parts.append("<h2>File Summary (changed only)</h2>")
    parts.append("<table class='sortable'><thead><tr>"
                 "<th data-sort='alpha' aria-sort='asc'>File<span class='caret'></span></th>"
                 "<th class='num' data-sort='percent' aria-sort='none'>A %<span class='caret'></span></th>"
                 "<th class='num' data-sort='percent' aria-sort='none'>B %<span class='caret'></span></th>"
                 "<th class='num' data-sort='percent' aria-sort='none'>Δ %<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>+covered<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>+uncovered<span class='caret'></span></th>"
                 "</tr></thead><tbody>")
    if rows:
        for r in sorted(rows, key=lambda R: R['file'].lower()):
            cls = 'delta-pos' if r['delta'] >= 0 else 'delta-neg'
            label = html.escape(r['file'])
            link = detail_links.get(r['file'])
            if link:
                label = f"<a class='filelink' href='{html.escape(link)}'><code>{label}</code></a>"
            else:
                label = f"<code>{label}</code>"
            parts.append(
                f"<tr><td style='text-align:left'>{label}</td>"
                f"<td class='num'>{r['a_pct']:.1f}%</td>"
                f"<td class='num'>{r['b_pct']:.1f}%</td>"
                f"<td class='num {cls}'>{r['delta']:+.1f}%</td>"
                f"<td class='num'>{len(r['became_covered'])}</td>"
                f"<td class='num'>{len(r['became_uncovered'])}</td></tr>"
            )
    else:
        parts.append("<tr><td colspan='6' style='text-align:center'>No coverage changes detected</td></tr>")
    parts.append("</tbody></table></body></html>")
    return "\n".join(parts)

def to_html_single(files: List[CoverageFile], detail_links: Dict[str,str],
                   ui_font_size: Optional[int], code_font_size: float, code_line_height: float):
    cov, tot, pct = aggregate_totals(files)
    parts = [html_head("gcovlens Report", ui_font_size=ui_font_size, code_font_size=code_font_size, code_line_height=code_line_height),
             "<body>", "<h1>gcovlens Report</h1>"]
    parts.append(f"""
    <div class="grid">
      <div class="badge">Coverage: {pct:.1f}% ({cov}/{tot})</div>
      <div class="badge">Files: {len(files)}</div>
    </div>
    """)
    parts.append("<h2>File Summary</h2>")
    parts.append("<table class='sortable'><thead><tr>"
                 "<th data-sort='alpha' aria-sort='asc'>File<span class='caret'></span></th>"
                 "<th class='num' data-sort='percent' aria-sort='none'>% Covered<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>Covered<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>Total<span class='caret'></span></th>"
                 "<th class='num' data-sort='num' aria-sort='none'>Uncovered<span class='caret'></span></th>"
                 "</tr></thead><tbody>")
    if files:
        for cf in sorted(files, key=lambda x: x.source.lower()):
            label = html.escape(cf.source)
            link = detail_links.get(cf.source)
            if link:
                label = f"<a class='filelink' href='{html.escape(link)}'><code>{label}</code></a>"
            else:
                label = f"<code>{label}</code>"
            parts.append(
                f"<tr><td style='text-align:left'>{label}</td>"
                f"<td class='num'>{cf.percent:.1f}%</td>"
                f"<td class='num'>{len(cf.covered)}</td>"
                f"<td class='num'>{cf.total}</td>"
                f"<td class='num'>{len(cf.uncovered)}</td></tr>"
            )
    else:
        parts.append("<tr><td colspan='5' style='text-align:center'>No executable lines found</td></tr>")
    parts.append("</tbody></table></body></html>")
    return "\n".join(parts)

def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate coverage or diff reports from GCC .gcov files.")
    parser.add_argument("run_a", type=Path, help="Directory for Run A (.gcov files)")
    parser.add_argument("run_b", nargs="?", type=Path, help="Directory for Run B (.gcov files). If omitted, single-run mode.")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Write report to this file (HTML or Markdown based on --format)")
    parser.add_argument("--format", "-f", choices=["html", "md"], default="html", help="Output format")
    parser.add_argument("--show-lines", action="store_true", help="(MD only) Include per-file line numbers that changed coverage state in diff mode.")
    parser.add_argument("--details-dir", type=Path, default=None, help="(HTML) Directory to write per-file HTML detail pages. Default: <output>_files")
    parser.add_argument("--display-blank", action="store_true", help="In detail pages, show whitespace-only lines (non-exec/no-data only).")
    parser.add_argument("--strip-comments", action="store_true", help="In detail pages, hide comment-only lines (non-exec/no-data only; heuristic).")
    parser.add_argument("--syntax", choices=["off","hljs"], default="hljs", help="Syntax highlighting engine for detail pages.")
    parser.add_argument("--syntax-theme", choices=["github","github-dark"], default="github", help="Syntax theme when --syntax=hljs.")
    parser.add_argument("--ui-font-size", type=int, default=12, help="Base UI font size in px (summary & detail pages).")
    parser.add_argument("--code-font-size", type=float, default=13, help="Code font size in px (detail pages).")
    parser.add_argument("--code-line-height", type=float, default=0.25, help="Code line-height (detail pages).")
    args = parser.parse_args(argv)

    # Expand '~' and normalize key paths early
    args.run_a = args.run_a.expanduser()
    if args.run_b is not None:
        args.run_b = args.run_b.expanduser()
    if args.output is not None:
        args.output = args.output.expanduser()
    if args.details_dir is not None:
        args.details_dir = args.details_dir.expanduser()

    if not args.run_a.exists() or not args.run_a.is_dir():
        print(f"ERROR: {args.run_a} is not a directory", file=sys.stderr)
        return 2
    if args.run_b is not None and (not args.run_b.exists() or not args.run_b.is_dir()):
        print(f"ERROR: {args.run_b} is not a directory", file=sys.stderr)
        return 2

    # Decide mode
    is_diff = args.run_b is not None

    # Always provide an output filename if none was given
    if args.output is None:
        run_a_base = args.run_a.resolve().name
        base = f"coverage_{run_a_base}"
        if is_diff:
            run_b_base = args.run_b.resolve().name
            base = f"coverage_diff_{run_a_base}_V_{run_b_base}"
        ext = ".html" if args.format == "html" else ".md"
        args.output = Path(base + ext)

    # Prepare details directory (HTML only)
    details_dir: Optional[Path] = None
    if args.format == "html":
        details_dir = args.details_dir or args.output.with_name(args.output.stem + "_files")
        try:
            details_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"ERROR: could not create details directory {details_dir}: {e}", file=sys.stderr)
            return 2

    if is_diff:
        a_map = load_dir(args.run_a)
        b_map = load_dir(args.run_b)
        common = sorted(set(a_map.keys()) | set(b_map.keys()))
        rows: List[Dict[str, Any]] = []
        pairs: List[Tuple[CoverageFile, CoverageFile]] = []
        detail_links: Dict[str, str] = {}

        for key in common:
            a = a_map.get(key, CoverageFile(key))
            b = b_map.get(key, CoverageFile(key))
            became_covered, became_uncovered = compute_diff(a, b)
            delta = b.percent - a.percent
            include = abs(delta) >= CHANGE_THRESHOLD or became_covered or became_uncovered
            if include:
                rows.append({
                    "file": key,
                    "a_pct": a.percent,
                    "b_pct": b.percent,
                    "delta": delta,
                    "became_covered": became_covered,
                    "became_uncovered": became_uncovered,
                    "a": a, "b": b,
                })
            pairs.append((a, b))

        totals = aggregate_totals_pairs(pairs)

        if args.format == "md":
            out = to_markdown_diff(rows, totals, args.show_lines)
        else:
            if details_dir is not None:
                for r in rows:
                    fname = sanitize_detail_name(r['file'])
                    detail_path = details_dir / fname
                    breadcrumb_href = os.path.relpath(args.output, start=detail_path.parent).replace('\\','/')
                    write_diff_detail_page(detail_path, r['file'], r['a'], r['b'],
                                           args.display_blank, args.strip_comments,
                                           args.syntax, args.syntax_theme,
                                           args.ui_font_size, args.code_font_size, args.code_line_height,
                                           breadcrumb_href=breadcrumb_href)
                    detail_links[r['file']] = f"{details_dir.name}/{fname}"
            out = to_html_diff(rows, totals, detail_links, args.ui_font_size, args.code_font_size, args.code_line_height)

    else:
        file_map = load_dir(args.run_a)
        files = list(file_map.values())
        detail_links: Dict[str, str] = {}

        if args.format == "md":
            out = to_markdown_single(files)
        else:
            if details_dir is not None:
                for cf in files:
                    fname = sanitize_detail_name(cf.source)
                    detail_path = details_dir / fname
                    breadcrumb_href = os.path.relpath(args.output, start=detail_path.parent).replace('\\','/')
                    write_single_detail_page(detail_path, cf.source, cf,
                                             args.display_blank, args.strip_comments,
                                             args.syntax, args.syntax_theme,
                                             args.ui_font_size, args.code_font_size, args.code_line_height,
                                             breadcrumb_href=breadcrumb_href)
                    detail_links[cf.source] = f"{details_dir.name}/{fname}"
            out = to_html_single(files, detail_links, args.ui_font_size, args.code_font_size, args.code_line_height)

    # Write the summary (and index.html inside details dir for convenience)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"ERROR: could not create parent directory for output {args.output}: {e}", file=sys.stderr)
        return 2
    try:
        if args.format == "html" and details_dir is not None:
            (details_dir / "index.html").write_text(out, encoding='utf-8')
        args.output.write_text(out, encoding='utf-8')
    except FileNotFoundError as e:
        print(f"ERROR: could not write output file {args.output}: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: writing output failed: {e}", file=sys.stderr)
        return 2

    print("")
    print(f"Wrote {args.format.upper()} report to: {args.output}")
    if args.format == "html" and details_dir is not None:
        print(f"Wrote per-file details to: {details_dir}/")


if __name__ == "__main__":
    sys.exit(main())
