"""
GBFR Skill Status Editor (Endless Ragnarok)
--------------------------------------------
A standalone tool for editing Granblue Fantasy Relink's skill_status.tbl
(Endless Ragnarok / game version 2.0.x).

Requirements:
  - Python 3.9+ with tkinter (bundled with most Windows Python installs)
  - GBFRDataTools (https://github.com/Nenkai/GBFRDataTools/releases)
  - Your own extracted skill_status.tbl (system/table/skill_status.tbl)
  - data/text.json sitting next to this script (bundled) - used to show
    real skill names/descriptions instead of raw internal IDs. Optional:
    if missing, the tool still works, just without readable names.

On first run for a given GBFRDataTools install, this tool will offer to patch
its Headers/skill_status.headers file so it can read Endless Ragnarok's table
format (the ER table has 16 extra bytes per row - 4 new LevelValue columns -
that the community's default headers file doesn't yet know about). It backs
up the original header file before changing it.
"""

import csv
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

if getattr(sys, "frozen", False):
    # Running as a PyInstaller-built .exe: use the exe's own folder, not the temp
    # extraction folder PyInstaller unpacks into (sys._MEIPASS), so data/text.json
    # and the cache still live in a predictable, user-visible location next to the exe.
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEXT_JSON_PATH = os.path.join(SCRIPT_DIR, "data", "text.json")

VALUE_COLUMNS = ["LevelValue1", "LevelValue2", "LevelValue3", "LevelValue4", "LevelValue5", "LevelValue6",
                 "LevelValue7", "LevelValue8", "LevelValue9", "LevelValue10"]
BASE_DISPLAY_COLUMNS = ["Key", "SkillName", "Level"] + VALUE_COLUMNS + ["LevelDescription", "LevelDescriptionText"]
COLUMN_CHAR_WIDTHS = {"Key": 16, "SkillName": 20, "Level": 6, "LevelDescription": 22, "LevelDescriptionText": 30}

MAX_DISPLAYED_ROWS = 300

SELECT_BG = "#4a90d9"
SELECT_FG = "white"
DEFAULT_BG = "white"
DEFAULT_FG = "black"
HEADER_BG = "#dddddd"

HEADER_PATCH_ANCHOR = "add_column|LevelValue6|float\nadd_column|Key|hash_string"
HEADER_PATCH_REPLACEMENT = """add_column|LevelValue6|float

// ER 2.0: +16 bytes vs base game (extends the LevelValue array, level caps raised in ER)
set_min_version|2.0.0
add_column|LevelValue7|float
add_column|LevelValue8|float
add_column|LevelValue9|float
add_column|LevelValue10|float
reset_min_version

add_column|Key|hash_string"""
HEADER_PATCH_MARKER = "LevelValue10"  # presence of this means the patch is already applied

HELPER_COLUMNS = ["SkillName", "LevelDescriptionText"]
HELPER_TABLES = ["text_strings"]

# --- Custom XXHash32 (matches GBFRDataTools.Hashing.XXHash32Custom) ---
MASK32 = 0xFFFFFFFF
PRIME32_1 = 0x9e3779b1
PRIME32_2 = 0x85EBCA77
PRIME32_3 = 0xC2B2AE3D
PRIME32_4 = 0x27D4EB2F
PRIME32_5 = 0x165667B1


def _rotl(x, r):
    x &= MASK32
    return ((x << r) | (x >> (32 - r))) & MASK32


def _round(seed, inp):
    return (_rotl((seed + inp * PRIME32_2) & MASK32, 13) * PRIME32_1) & MASK32


def xxhash32_custom(data: bytes) -> int:
    p = data
    h32 = 0x178A54A4
    if len(data) >= 16:
        v1, v2, v3, v4 = 0x2557311B, 0x871FB76A, 0x0133ECF3, 0x62FC7342
        while True:
            v1 = _round(v1, int.from_bytes(p[0:4], "little"))
            v2 = _round(v2, int.from_bytes(p[4:8], "little"))
            v3 = _round(v3, int.from_bytes(p[8:12], "little"))
            v4 = _round(v4, int.from_bytes(p[12:16], "little"))
            p = p[16:]
            if not (len(p) > 16):
                break
        h32 = (_rotl(v1, 1) + _rotl(v2, 7) + _rotl(v3, 12) + _rotl(v4, 18)) & MASK32
    h32 = (h32 + len(data)) & MASK32
    while len(p) >= 4:
        h32 = (_rotl((h32 + (int.from_bytes(p[0:4], "little") * PRIME32_3 & MASK32)) & MASK32, 17) * PRIME32_4) & MASK32
        p = p[4:]
    while len(p) > 0:
        h32 = (_rotl((h32 + (p[0] * PRIME32_5 & MASK32)) & MASK32, 11) * PRIME32_1) & MASK32
        p = p[1:]
    h32 ^= h32 >> 15
    h32 = (h32 * PRIME32_2) & MASK32
    h32 ^= h32 >> 13
    h32 = (h32 * PRIME32_3) & MASK32
    h32 ^= h32 >> 16
    return h32


def hash_hex(s: str) -> str:
    return f"{xxhash32_custom(s.encode('ascii')):08X}"


def ensure_header_patch(gbfr_data_tools_dir):
    """Patch Headers/skill_status.headers to understand ER's extra 16 bytes/row, if not already done."""
    headers_path = os.path.join(gbfr_data_tools_dir, "Headers", "skill_status.headers")
    if not os.path.exists(headers_path):
        raise FileNotFoundError(f"Could not find {headers_path} - is this the right GBFRDataTools folder?")

    with open(headers_path, "r", encoding="utf-8") as f:
        content = f.read()

    if HEADER_PATCH_MARKER in content:
        return "already_patched"

    if HEADER_PATCH_ANCHOR not in content:
        return "anchor_not_found"

    backup_path = headers_path + ".pre_er_backup"
    if not os.path.exists(backup_path):
        shutil.copy2(headers_path, backup_path)

    patched = content.replace(HEADER_PATCH_ANCHOR, HEADER_PATCH_REPLACEMENT)
    with open(headers_path, "w", encoding="utf-8") as f:
        f.write(patched)

    return "patched"


def run_tool(gbfr_data_tools_exe, args):
    result = subprocess.run(
        [gbfr_data_tools_exe] + args,
        cwd=os.path.dirname(gbfr_data_tools_exe),
        capture_output=True, text=True,
    )
    return result


def load_text_entries():
    """Returns list of (id_hash, text) from the bundled text.json, or [] if it's missing."""
    if not os.path.exists(TEXT_JSON_PATH):
        return []
    with open(TEXT_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data["rows_"] if isinstance(data, dict) and "rows_" in data else data
    entries = []
    for item in rows:
        col = item.get("column_", item)
        id_hash = col.get("id_hash_")
        text = col.get("text_")
        if id_hash:
            entries.append((id_hash, text))
    return entries


def apply_name_resolution(con, entries):
    """Adds SkillName/LevelDescriptionText columns and fills in as much as possible,
    including resolving raw-hex Key/LevelDescription hashes by brute-force matching
    against every candidate skill ID string found in the bundled text data."""
    cur = con.cursor()

    cur.execute("DROP TABLE IF EXISTS text_strings")
    cur.execute("CREATE TABLE text_strings (id_hash TEXT PRIMARY KEY, text TEXT)")
    cur.executemany("INSERT OR REPLACE INTO text_strings (id_hash, text) VALUES (?, ?)", entries)

    cur.execute("PRAGMA table_info(skill_status)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "SkillName" not in existing_cols:
        cur.execute("ALTER TABLE skill_status ADD COLUMN SkillName TEXT")
    if "LevelDescriptionText" not in existing_cols:
        cur.execute("ALTER TABLE skill_status ADD COLUMN LevelDescriptionText TEXT")

    id_hashes = [e[0] for e in entries]

    # Resolve raw-hex Key values by hashing every "SKILL_xxx_yy" candidate name string
    key_candidates = {}
    skill_name_re = re.compile(r"^TXT_(SKILL_\d+_\d+)$")
    for id_hash in id_hashes:
        m = skill_name_re.match(id_hash)
        if m:
            candidate = m.group(1)
            key_candidates[hash_hex(candidate)] = candidate

    # Resolve raw-hex LevelDescription values the same way, using full EXPLAIN strings
    desc_candidates = {}
    desc_re = re.compile(r"^TXT_SKILL_EXPLAIN_\d+_\d+$")
    for id_hash in id_hashes:
        if desc_re.match(id_hash):
            desc_candidates[hash_hex(id_hash)] = id_hash

    cur.execute("SELECT DISTINCT Key FROM skill_status WHERE Key NOT LIKE 'SKILL\\_%' ESCAPE '\\'")
    for (raw,) in cur.fetchall():
        if raw in key_candidates:
            cur.execute("UPDATE skill_status SET Key = ? WHERE Key = ?", (key_candidates[raw], raw))

    cur.execute("SELECT DISTINCT LevelDescription FROM skill_status WHERE LevelDescription NOT LIKE 'TXT\\_%' ESCAPE '\\'")
    for (raw,) in cur.fetchall():
        if raw in desc_candidates:
            cur.execute("UPDATE skill_status SET LevelDescription = ? WHERE LevelDescription = ?", (desc_candidates[raw], raw))

    cur.execute("""
        UPDATE skill_status
        SET SkillName = (SELECT text FROM text_strings WHERE text_strings.id_hash = 'TXT_' || skill_status.Key)
        WHERE SkillName IS NULL
    """)
    cur.execute("""
        UPDATE skill_status
        SET LevelDescriptionText = (SELECT text FROM text_strings WHERE text_strings.id_hash = skill_status.LevelDescription)
        WHERE LevelDescriptionText IS NULL
    """)
    con.commit()


class SetupDialog(tk.Toplevel):
    """Asks the user for the paths/settings needed, then hands off to the main editor."""

    def __init__(self, master):
        super().__init__(master)
        self.title("GBFR Skill Status Editor - Setup")
        self.geometry("680x260")
        self.minsize(680, 260)
        self.result = None

        pad = {"padx": 10, "pady": 6}

        ttk.Label(self, text="GBFRDataTools.exe:").grid(row=0, column=0, sticky="w", **pad)
        self.tools_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.tools_var, width=55).grid(row=0, column=1, **pad)
        ttk.Button(self, text="Browse", command=self.pick_tools_exe).grid(row=0, column=2, **pad)

        ttk.Label(self, text="skill_status.tbl:").grid(row=1, column=0, sticky="w", **pad)
        self.tbl_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.tbl_var, width=55).grid(row=1, column=1, **pad)
        ttk.Button(self, text="Browse", command=self.pick_tbl).grid(row=1, column=2, **pad)

        ttk.Label(self, text="Game version (for -v flag):").grid(row=2, column=0, sticky="w", **pad)
        self.version_var = tk.StringVar(value="2.0.2")
        ttk.Entry(self, textvariable=self.version_var, width=20).grid(row=2, column=1, sticky="w", **pad)

        text_status = "found" if os.path.exists(TEXT_JSON_PATH) else "NOT FOUND (names will show as raw IDs)"
        ttk.Label(
            self,
            text=f"Bundled name data (data/text.json): {text_status}\n\n"
                 "This will patch GBFRDataTools' skill_status.headers to understand\n"
                 "Endless Ragnarok's table format, if it hasn't been already.\n"
                 "The original header file is backed up first.",
            foreground="#555",
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", **pad)

        ttk.Button(self, text="Continue", command=self.on_continue).grid(row=4, column=0, columnspan=3, pady=16)

        # Make this window modal so the browse dialogs can't leave a stale, still-clickable
        # setup window sitting behind them. (Deliberately NOT calling self.transient(master) -
        # master/root is kept withdrawn until conversion finishes, and many window managers
        # hide a transient window whenever its owner isn't shown, which hid this whole dialog.)
        self.update_idletasks()  # make sure the window is actually mapped before grabbing it
        self.grab_set()
        self.focus_set()

    def pick_tools_exe(self):
        path = filedialog.askopenfilename(
            parent=self, title="Select GBFRDataTools.exe", filetypes=[("Executable", "*.exe")]
        )
        if path:
            self.tools_var.set(path)

    def pick_tbl(self):
        path = filedialog.askopenfilename(
            parent=self, title="Select skill_status.tbl", filetypes=[("Table file", "*.tbl")]
        )
        if path:
            self.tbl_var.set(path)

    def on_continue(self):
        tools_exe = self.tools_var.get().strip()
        tbl_path = self.tbl_var.get().strip()
        version = self.version_var.get().strip()

        if not tools_exe or not os.path.exists(tools_exe):
            messagebox.showerror("Missing file", "Please select a valid GBFRDataTools.exe")
            return
        if not tbl_path or not os.path.exists(tbl_path):
            messagebox.showerror("Missing file", "Please select a valid skill_status.tbl")
            return
        if not version:
            messagebox.showerror("Missing value", "Please enter a game version.")
            return

        gbfr_dir = os.path.dirname(tools_exe)
        try:
            patch_status = ensure_header_patch(gbfr_dir)
        except FileNotFoundError as e:
            messagebox.showerror("Error", str(e))
            return

        if patch_status == "anchor_not_found":
            proceed = messagebox.askyesno(
                "Header patch not applied",
                "Could not find the expected pattern in skill_status.headers to patch it "
                "for Endless Ragnarok automatically (the file may have changed).\n\n"
                "Continuing without the patch will likely fail to convert an ER-version "
                "skill_status.tbl. Continue anyway?",
            )
            if not proceed:
                return
        elif patch_status == "patched":
            messagebox.showinfo(
                "Header patched",
                "skill_status.headers was updated to support Endless Ragnarok's table format.\n"
                "The original file was backed up as skill_status.headers.pre_er_backup.",
            )

        self.result = (tools_exe, tbl_path, version)
        self.destroy()


def convert_tbl_to_sqlite(tools_exe, tbl_path, version, force=False, report=None):
    """report, if given, is called with a short status string at each stage
    (must be thread-safe to queue - do not touch Tk widgets directly from here)."""
    if report is None:
        report = lambda msg: None

    report("Checking for a cached conversion...")
    work_dir = os.path.join(SCRIPT_DIR, "data", "cache")
    os.makedirs(work_dir, exist_ok=True)
    working_tbl = os.path.join(work_dir, "skill_status.tbl")
    sqlite_path = os.path.join(work_dir, "skill_status_working.sqlite")

    # Skip reconversion if we already have a cached database newer than the source .tbl
    # (i.e. the source hasn't changed since we last converted it).
    if not force and os.path.exists(sqlite_path) and os.path.exists(working_tbl):
        cached_matches_source = (
            os.path.getmtime(sqlite_path) >= os.path.getmtime(tbl_path)
            and os.path.getsize(working_tbl) == os.path.getsize(tbl_path)
        )
        if cached_matches_source:
            report("Loaded from cache.")
            return sqlite_path, work_dir, True  # cache hit

    report("Copying table file...")
    shutil.copy2(tbl_path, working_tbl)

    if os.path.exists(sqlite_path):
        os.remove(sqlite_path)

    report("Converting table to a database (this can take a few seconds)...")
    result = run_tool(tools_exe, ["tbl-to-sqlite", "-i", work_dir, "-o", sqlite_path, "-v", version])
    if result.returncode != 0 or "Unhandled exception" in result.stdout:
        raise RuntimeError(f"tbl-to-sqlite failed:\n{result.stdout}\n{result.stderr}")

    report("Resolving skill names from bundled text data...")
    con = sqlite3.connect(sqlite_path)
    entries = load_text_entries()
    if entries:
        apply_name_resolution(con, entries)
    con.close()

    report("Done.")
    return sqlite_path, work_dir, False  # freshly converted


class SkillStatusEditor:
    def __init__(self, root, tools_exe, source_tbl_path, sqlite_path, work_dir, version, on_clear_cache=None):
        self.root = root
        self.tools_exe = tools_exe
        self.source_tbl_path = source_tbl_path
        self.sqlite_path = sqlite_path
        self.work_dir = work_dir
        self.version = version
        self.on_clear_cache = on_clear_cache
        self.changelog_path = os.path.join(work_dir, "skill_status_changelog.csv")

        root.title(f"GBFR Skill Status Editor - {os.path.basename(source_tbl_path)}")
        root.geometry("1250x650")

        self.con = sqlite3.connect(sqlite_path)

        cur = self.con.execute("PRAGMA table_info(skill_status)")
        existing_cols = {row[1] for row in cur.fetchall()}
        self.display_columns = [c for c in BASE_DISPLAY_COLUMNS if c in existing_cols or c in VALUE_COLUMNS or c == "Key" or c == "Level" or c == "LevelDescription"]

        self.row_data = []
        self.cell_labels = {}
        self.selected = set()
        self.anchor_row = None
        self.anchor_col = None

        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text="Key or Skill Name contains:").pack(side="left")
        self.search_var = tk.StringVar(value="")
        entry = ttk.Entry(top, textvariable=self.search_var, width=30)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.run_search())

        ttk.Button(top, text="Search", command=self.run_search).pack(side="left", padx=4)
        ttk.Button(top, text="Export to .tbl...", command=self.export_to_tbl).pack(side="right", padx=4)
        ttk.Button(top, text="Clear Cache...", command=self.clear_cache).pack(side="right", padx=4)

        self.status_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.status_var, foreground="#555").pack(side="left", padx=12)

        hint = ttk.Frame(root)
        hint.pack(fill="x", padx=8)
        ttk.Label(
            hint, text="click=select  shift+click=range (same column)  Enter=edit selected",
            foreground="#777",
        ).pack(side="left")

        container = ttk.Frame(root)
        container.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0, bg="white")
        vsb = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(container, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.grid_frame = tk.Frame(self.canvas, bg="white")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

        root.bind("<Return>", self.on_enter_key)

        # Don't auto-load the whole table on startup (thousands of rows would render very
        # slowly as individual widgets) - wait for the user to search for something first.
        self.status_var.set("Type a search term above and hit Search/Enter to load rows.")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 40), "units")

    def log_change(self, key, level, col_name, old_value, new_value):
        is_new_file = not os.path.exists(self.changelog_path)
        with open(self.changelog_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new_file:
                writer.writerow(["timestamp", "Key", "Level", "Column", "OldValue", "NewValue"])
            writer.writerow([datetime.now().isoformat(timespec="seconds"), key, level, col_name, old_value, new_value])

    def run_search(self):
        raw_term = self.search_var.get().strip()
        if not raw_term:
            self.status_var.set("Type a search term above and hit Search/Enter to load rows.")
            self.render_rows([])
            return

        term = f"%{raw_term}%"
        has_name = "SkillName" in self.display_columns
        where_clause = "Key LIKE ? OR SkillName LIKE ?" if has_name else "Key LIKE ?"
        params = (term, term) if has_name else (term,)
        query = f"""
            SELECT {', '.join(self.display_columns)}
            FROM skill_status
            WHERE {where_clause}
            ORDER BY Key, Level
            LIMIT ?
        """
        cur = self.con.execute(query, params + (MAX_DISPLAYED_ROWS + 1,))
        rows = cur.fetchall()

        truncated = len(rows) > MAX_DISPLAYED_ROWS
        if truncated:
            rows = rows[:MAX_DISPLAYED_ROWS]
            self.status_var.set(f"Showing first {MAX_DISPLAYED_ROWS} results - refine your search to see more.")
        else:
            self.status_var.set(f"{len(rows)} row(s)")

        self.render_rows(rows)

    def render_rows(self, rows):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.cell_labels.clear()
        self.selected.clear()
        self.anchor_row = None
        self.anchor_col = None
        self.row_data = [tuple(row) for row in rows]

        for c, col_name in enumerate(self.display_columns):
            width = COLUMN_CHAR_WIDTHS.get(col_name, 10)
            lbl = tk.Label(self.grid_frame, text=col_name, width=width, bg=HEADER_BG, relief="ridge", anchor="center")
            lbl.grid(row=0, column=c, sticky="nsew")

        for r, row in enumerate(self.row_data):
            for c, col_name in enumerate(self.display_columns):
                width = COLUMN_CHAR_WIDTHS.get(col_name, 10)
                value = row[c]
                lbl = tk.Label(
                    self.grid_frame, text=str(value), width=width, bg=DEFAULT_BG, fg=DEFAULT_FG,
                    relief="flat", anchor="center", padx=2, pady=1,
                )
                lbl.grid(row=r + 1, column=c, sticky="nsew")

                if col_name in VALUE_COLUMNS:
                    self.cell_labels[(r, c)] = lbl
                    lbl.bind("<Button-1>", lambda e, r=r, c=c: self.on_cell_click(r, c))
                    lbl.bind("<Shift-Button-1>", lambda e, r=r, c=c: self.on_cell_shift_click(r, c))

    def clear_selection(self):
        for (r, c) in self.selected:
            self.cell_labels[(r, c)].configure(bg=DEFAULT_BG, fg=DEFAULT_FG)
        self.selected.clear()

    def select_cell(self, r, c):
        self.selected.add((r, c))
        self.cell_labels[(r, c)].configure(bg=SELECT_BG, fg=SELECT_FG)

    def on_cell_click(self, r, c):
        self.root.focus_set()
        self.clear_selection()
        self.anchor_row = r
        self.anchor_col = c
        self.select_cell(r, c)

    def on_cell_shift_click(self, r, c):
        self.root.focus_set()
        if self.anchor_col is None:
            return self.on_cell_click(r, c)
        if c != self.anchor_col:
            return
        lo, hi = sorted((self.anchor_row, r))
        self.clear_selection()
        self.anchor_col = c
        for rr in range(lo, hi + 1):
            self.select_cell(rr, c)

    def on_enter_key(self, event):
        if not self.selected:
            return
        self.open_bulk_edit_popup()

    def open_bulk_edit_popup(self):
        cols = {c for (_, c) in self.selected}
        if len(cols) != 1:
            return
        col_index = next(iter(cols))
        col_name = self.display_columns[col_index]
        key_index = self.display_columns.index("Key")
        level_index = self.display_columns.index("Level")
        count = len(self.selected)

        popup = tk.Toplevel(self.root)
        popup.title(f"Edit {col_name} ({count} cell{'s' if count != 1 else ''})")
        popup.geometry("320x120")

        ttk.Label(popup, text=f"Editing {count} cell(s) in {col_name}").pack(pady=(10, 6))
        var = tk.StringVar()
        entry = ttk.Entry(popup, textvariable=var)
        entry.pack(pady=4)
        entry.focus_set()

        def apply_value():
            new_value = var.get()
            try:
                float(new_value)
            except ValueError:
                messagebox.showerror("Invalid value", "Please enter a number.")
                return
            for (r, c) in self.selected:
                key = self.row_data[r][key_index]
                level = self.row_data[r][level_index]
                old_value = self.row_data[r][col_index]
                self.con.execute(
                    f"UPDATE skill_status SET {col_name} = ? WHERE Key = ? AND Level = ?",
                    (new_value, key, level),
                )
                self.log_change(key, level, col_name, old_value, new_value)
                self.row_data[r] = tuple(
                    new_value if i == col_index else v for i, v in enumerate(self.row_data[r])
                )
                self.cell_labels[(r, c)].configure(text=new_value)
            self.con.commit()
            self.clear_selection()
            popup.destroy()

        entry.bind("<Return>", lambda e: apply_value())
        ttk.Button(popup, text="Apply to all selected", command=apply_value).pack(pady=4)

    def export_to_tbl(self):
        dest_dir = filedialog.askdirectory(parent=self.root, title="Choose folder to export skill_status.tbl into")
        if not dest_dir:
            return

        clean_path = os.path.join(self.work_dir, "skill_status_export.sqlite")
        if os.path.exists(clean_path):
            os.remove(clean_path)
        shutil.copy2(self.sqlite_path, clean_path)

        con = sqlite3.connect(clean_path)
        cur = con.cursor()
        for table in HELPER_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        for col in HELPER_COLUMNS:
            try:
                cur.execute(f"ALTER TABLE skill_status DROP COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        con.commit()
        con.close()

        result = run_tool(self.tools_exe, ["sqlite-to-tbl", "-i", clean_path, "-o", dest_dir, "-v", self.version])
        if result.returncode != 0 or "Unhandled exception" in result.stdout:
            messagebox.showerror("Export failed", f"{result.stdout}\n{result.stderr}")
            return

        messagebox.showinfo("Export complete", f"skill_status.tbl written to:\n{dest_dir}")

    def clear_cache(self):
        proceed = messagebox.askyesno(
            "Clear cache",
            "This deletes the cached working database for this table, including any edits "
            "not yet exported to a .tbl file, and takes you back to pick a skill_status.tbl "
            "again. Continue?",
        )
        if not proceed:
            return

        self.con.close()
        if self.on_clear_cache:
            self.on_clear_cache()


class LoadingDialog(tk.Toplevel):
    """Shown while the .tbl -> SQLite conversion runs in a background thread."""

    def __init__(self, master, initial_text):
        super().__init__(master)
        self.title("Please wait")
        self.geometry("440x130")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # don't let the user close it mid-conversion

        self.status_var = tk.StringVar(value=initial_text)
        ttk.Label(self, textvariable=self.status_var, wraplength=400, justify="left").pack(
            pady=(20, 10), padx=15, fill="x"
        )
        self.progress = ttk.Progressbar(self, mode="indeterminate", length=400)
        self.progress.pack(pady=10, padx=15)
        self.progress.start(12)

    def set_status(self, text):
        self.status_var.set(text)


def reset_root(root):
    """Tear down whatever's currently built on root so it can be reused for a fresh run."""
    for child in root.winfo_children():
        child.destroy()
    root.unbind("<Return>")
    root.withdraw()


def launch(root):
    setup = SetupDialog(root)
    root.wait_window(setup)  # blocks until the setup Toplevel is closed

    if setup.result is None:
        root.destroy()
        return  # user closed the setup dialog without continuing

    tools_exe, tbl_path, version = setup.result

    status_queue = queue.Queue()
    result_box = {}
    loading = LoadingDialog(root, "Starting...")

    def worker():
        try:
            sqlite_path, work_dir, cache_hit = convert_tbl_to_sqlite(
                tools_exe, tbl_path, version, report=lambda msg: status_queue.put(("status", msg))
            )
            result_box["ok"] = (sqlite_path, work_dir, cache_hit)
        except Exception as e:
            result_box["error"] = str(e)
        status_queue.put(("done", None))

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                kind, payload = status_queue.get_nowait()
                if kind == "status":
                    loading.set_status(payload)
                elif kind == "done":
                    loading.destroy()
                    if "error" in result_box:
                        messagebox.showerror("Conversion failed", result_box["error"])
                        root.destroy()
                        sys.exit(1)
                    sqlite_path, work_dir, cache_hit = result_box["ok"]
                    root.deiconify()

                    def on_clear_cache(work_dir=work_dir):
                        shutil.rmtree(work_dir, ignore_errors=True)
                        reset_root(root)
                        launch(root)  # back to the setup dialog

                    editor = SkillStatusEditor(
                        root, tools_exe, tbl_path, sqlite_path, work_dir, version, on_clear_cache=on_clear_cache
                    )
                    if cache_hit:
                        editor.root.title(editor.root.title() + " (loaded from cache)")
                    return  # stop polling, hand off to the normal Tk event loop
        except queue.Empty:
            pass
        root.after(100, poll)

    root.after(100, poll)


def main():
    root = tk.Tk()
    root.withdraw()  # hide the main window until setup is done and we're ready to show the editor
    launch(root)
    root.mainloop()


if __name__ == "__main__":
    main()
