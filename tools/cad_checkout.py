#!/usr/bin/env python3
"""CAD Checkout: lock, edit and check in SolidWorks files without the command line.

Wraps the Git LFS locking workflow in a small window:

    Start editing  ->  lock the file(s), then pull the latest version
    Done editing   ->  commit, pull --rebase, push, then unlock
    Discard        ->  throw away your changes and unlock
    Double-click   ->  lock + pull, then open in SolidWorks, ready to edit
    Open           ->  open without locking (read-only, just for looking)

It refuses to check in a file that is still open in SolidWorks (it looks for
SolidWorks' "~$<name>" lock file next to the part).

Standard library only (tkinter). Python 3.8+.
Run with:  python tools/cad_checkout.py         (GUI)
           python tools/cad_checkout.py --check (print status and exit)
"""
from __future__ import annotations

import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashes on Windows
AUTO_REFRESH_MS = 60_000
GIT_DOWNLOAD = "https://git-scm.com/download/win"


# --------------------------------------------------------------------------- git helpers

def find_git():
    """git on PATH, else the copy bundled with GitHub Desktop (it includes Git LFS)."""
    exe = shutil.which("git")
    if exe:
        return exe
    pattern = os.path.join(os.environ.get("LOCALAPPDATA", ""), "GitHubDesktop", "app-*",
                           "resources", "app", "git", "cmd", "git.exe")
    found = sorted(glob.glob(pattern), key=os.path.getmtime)
    return found[-1] if found else None


GIT = find_git()


def git(repo, *args, input_text=None):
    """Run git and return (returncode, stdout, stderr)."""
    if not GIT:
        return 127, "", "git is not installed"
    try:
        p = subprocess.run(
            [GIT, *args], cwd=repo, input=input_text, capture_output=True,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
    except OSError as e:
        return 127, "", "could not run git: %s" % e
    return p.returncode, p.stdout, p.stderr


def find_repo():
    """Return the repo root, or None if this script isn't inside a git repo."""
    here = os.path.dirname(os.path.abspath(__file__))
    code, out, _ = git(here, "rev-parse", "--show-toplevel")
    return os.path.normpath(out.strip()) if code == 0 else None


# --------------------------------------------------------------------------- setup checks

def lfs_installed(repo):
    return git(repo, "lfs", "version")[0] == 0


def lfs_configured(repo):
    """LFS filters are registered with git, and this repo has the LFS hooks."""
    _, out, _ = git(repo, "config", "--get", "filter.lfs.process")
    if not out.strip():
        return False
    _, hooks, _ = git(repo, "rev-parse", "--git-path", "hooks")
    pre_push = os.path.join(repo, hooks.strip(), "pre-push")
    try:
        with open(pre_push, encoding="utf-8", errors="replace") as f:
            return "lfs" in f.read()
    except OSError:
        return False


def missing_lfs_files(repo):
    """LFS files that are still pointer stubs on disk (marked '-' by git lfs ls-files)."""
    _, out, _ = git(repo, "lfs", "ls-files")
    return [line.split(" - ", 1)[1] for line in out.splitlines() if " - " in line]


def preflight(repo, ask, fix=True):
    """Check git/LFS setup. `ask(title, msg, kind)` shows a dialog ('error' or 'yesno').

    Returns True when the tool is safe to use.
    """
    if not GIT:
        ask("Git not found", "Git isn't installed (or isn't on PATH).\n\nInstall Git for "
            "Windows, which includes Git LFS:\n" + GIT_DOWNLOAD, "error")
        return False
    if not repo:
        ask("Not in the repo", "cad_checkout.py must be inside the project's git folder.",
            "error")
        return False
    if not lfs_installed(repo):
        ask("Git LFS not installed",
            "This repo stores CAD files with Git LFS, which isn't installed for:\n" + GIT +
            "\n\nInstall Git for Windows (it includes LFS):\n" + GIT_DOWNLOAD +
            "\nor Git LFS on its own: https://git-lfs.com\n\nThen reopen CAD Checkout.",
            "error")
        return False
    if not lfs_configured(repo):
        why = ("Git LFS is installed but not set up on this computer/repo.\n\nWithout it, "
               "CAD files get committed outside LFS and lock checks are skipped on push.")
        if not fix:
            ask("Git LFS not set up", why + "\nFix: run 'git lfs install' in the repo.", "error")
            return False
        if not ask("Set up Git LFS?", why + "\n\nRun 'git lfs install' now? (one-time setup)",
                   "yesno"):
            return False
        code, out, err = git(repo, "lfs", "install")
        if code or not lfs_configured(repo):
            ask("Setup failed", (out + err).strip() or "git lfs install failed", "error")
            return False
    stubs = missing_lfs_files(repo)
    if stubs and fix and ask(
            "Download CAD files?",
            "%d file(s) are placeholders, not real files (the repo was cloned before LFS was "
            "set up). SolidWorks will say they're corrupt.\n\nDownload them now? This may "
            "take a minute.\n\n%s" % (len(stubs), "\n".join(stubs[:10]) +
                                      ("\n..." if len(stubs) > 10 else "")), "yesno"):
        code, out, err = git(repo, "lfs", "pull")
        if code:
            ask("Download failed", (out + err).strip(), "error")
    return True


def sw_lock_file(repo, path):
    """SolidWorks writes ~$<name> next to a file while it is open."""
    d, name = os.path.split(os.path.join(repo, path))
    return os.path.join(d, "~$" + name)


def load_state(repo, cached=None):
    """Collect everything the table needs.

    Normally hits GitHub for lock status (slow-ish). Pass a previous state as
    `cached` for a fast, local-only refresh that reuses its lock list.
    """
    state = {"rows": [], "lock_error": None, "branch": "?", "upstream": None, "locks": {}}

    _, out, _ = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    state["branch"] = out.strip() or "?"
    code, out, _ = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    state["upstream"] = out.strip() if code == 0 else None

    # Every tracked or new (not ignored) file that .gitattributes marks lockable
    _, out, _ = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = [p for p in out.split("\0") if p]
    lockable = []
    if paths:
        _, out, _ = git(repo, "check-attr", "-z", "--stdin", "lockable",
                        input_text="\0".join(paths) + "\0")
        f = out.split("\0")
        lockable = [f[i] for i in range(0, len(f) - 2, 3) if f[i + 2] == "set"]

    # Local changes
    changes = {}
    _, out, _ = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries, i = out.split("\0"), 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path = e[:2], e[3:]
        if xy[0] in "RC":  # renames/copies carry the old path as an extra entry
            i += 1
        changes[path] = "New" if xy == "??" else ("Deleted" if "D" in xy else "Modified")

    # Locks on the server (or reused from the last full refresh)
    locks = {}
    if cached is not None:
        locks = dict(cached.get("locks") or {})
        state["lock_error"] = cached.get("lock_error")
        code = None
    else:
        code, out, err = git(repo, "lfs", "locks", "--verify", "--json")
    if code is None:
        pass
    elif code == 0:
        try:
            data = json.loads(out or "{}")
        except ValueError:
            data = {}
        for who in ("ours", "theirs"):
            for lk in data.get(who) or []:
                owner = (lk.get("owner") or {}).get("name", "?")
                locks[lk["path"]] = ("you" if who == "ours" else "them", owner)
    else:
        msg = (err or out).strip().splitlines()
        state["lock_error"] = msg[-1] if msg else "could not reach the lock server"
    state["locks"] = locks

    for path in sorted(set(lockable) | set(locks), key=str.lower):
        lock, owner = locks.get(path, ("free", ""))
        state["rows"].append({
            "path": path,
            "lock": lock,
            "owner": owner,
            "change": changes.get(path, ""),
            "open": os.path.exists(sw_lock_file(repo, path)),
        })
    return state


def lock_label(row):
    if row["lock"] == "you":
        return "Locked by you"
    if row["lock"] == "them":
        return "Locked by " + row["owner"]
    return "Free"


class StepFailed(Exception):
    pass


# --------------------------------------------------------------------------- theme

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Brand colours (sampled from the logo) + a dark UI built around them
ORANGE, ORANGE_HOVER, ORANGE_DOWN = "#ff761e", "#ff8f45", "#e0600c"
GREY, GREY_HOVER = "#585a5c", "#6a6c6e"
BLACK = "#000000"
BG = "#121314"          # window
SURFACE = "#1c1d1f"     # table, inputs
RAISED = "#26282a"      # secondary buttons
BORDER = "#303235"
TEXT = "#f2f2f2"
MUTED = "#9a9da1"
RED = "#ff5c5c"
FONT = "Segoe UI"
MONO = "Consolas"


def setup_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")  # the only built-in theme that honours custom colours
    root.configure(background=BG)
    style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10),
                    bordercolor=BORDER, lightcolor=BG, darkcolor=BG, focuscolor=ORANGE,
                    troughcolor=SURFACE, selectbackground=GREY, selectforeground=TEXT)

    style.configure("TFrame", background=BG)
    style.configure("Header.TFrame", background=BLACK)
    style.configure("Title.TLabel", background=BLACK, foreground=TEXT, font=(FONT, 17, "bold"))
    style.configure("Sub.TLabel", background=BLACK, foreground=MUTED, font=(FONT, 9))
    style.configure("Busy.TLabel", background=BLACK, foreground=ORANGE, font=(FONT, 9, "bold"))
    style.configure("Stat.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
    style.configure("StatNum.TLabel", background=BG, foreground=TEXT, font=(FONT, 9, "bold"))
    style.configure("Warn.TLabel", background=BG, foreground=RED, font=(FONT, 9, "bold"))
    style.configure("Section.TLabel", background=BG, foreground=MUTED, font=(FONT, 8, "bold"))
    style.configure("Empty.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 11),
                    justify="center")
    style.configure("DlgTitle.TLabel", background=BG, foreground=TEXT, font=(FONT, 13, "bold"))
    style.configure("Mono.TLabel", background=BG, foreground=MUTED, font=(MONO, 9))

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    rowheight=32, borderwidth=0, font=(FONT, 10))
    style.map("Treeview", background=[("selected", GREY)], foreground=[("selected", TEXT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no border
    style.configure("Treeview.Heading", background=BLACK, foreground=MUTED,
                    font=(FONT, 9, "bold"), relief="flat", borderwidth=0, padding=(10, 7),
                    lightcolor=BLACK, darkcolor=BLACK, bordercolor=BLACK)
    style.map("Treeview.Heading", background=[("active", "#161616")])

    style.configure("Vertical.TScrollbar", background=GREY, troughcolor=SURFACE,
                    bordercolor=SURFACE, lightcolor=GREY, darkcolor=GREY, arrowcolor=TEXT,
                    gripcount=0, arrowsize=12)
    style.map("Vertical.TScrollbar", background=[("active", GREY_HOVER)])

    def button(name, bg, fg, hover, down, bold=False):
        style.configure(name, background=bg, foreground=fg, borderwidth=0, relief="flat",
                        padding=(16, 8), font=(FONT, 10, "bold" if bold else "normal"),
                        lightcolor=bg, darkcolor=bg, bordercolor=bg, focuscolor=bg)
        style.map(name,
                  background=[("disabled", SURFACE), ("pressed", down), ("active", hover)],
                  foreground=[("disabled", "#55585c")],
                  lightcolor=[("pressed", down), ("active", hover)],
                  darkcolor=[("pressed", down), ("active", hover)])

    button("Primary.TButton", ORANGE, BLACK, ORANGE_HOVER, ORANGE_DOWN, bold=True)
    button("TButton", RAISED, TEXT, "#303235", "#1f2022")
    button("Danger.TButton", RAISED, RED, "#3a2526", "#2a1c1d")
    return style


def style_window(win):
    """Dark Windows title bar (Win10 20H1+), black caption on Win11. No-op elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        for attr, value in ((20, 1), (35, 0x00000000)):  # DARK_MODE, CAPTION_COLOR (0xBBGGRR)
            v = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v),
                                                       ctypes.sizeof(v))
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


def load_image(name):
    try:
        return tk.PhotoImage(file=os.path.join(ASSETS, name))
    except tk.TclError:
        return None


# --------------------------------------------------------------------------- GUI

class App:
    def __init__(self, root, repo):
        self.root, self.repo = root, repo
        self.state = {"rows": [], "upstream": None}
        self.rows_by_path = {}
        self.busy = False
        self.fast_pending = False
        self.window_active = True
        self.events = queue.Queue()
        self.repo_name = os.path.basename(repo)

        root.title("CAD Checkout")
        root.geometry("980x640")
        root.minsize(720, 460)
        setup_style(root)
        style_window(root)

        # ----- header: logo, title, branch, busy indicator
        header = ttk.Frame(root, style="Header.TFrame", padding=(18, 12))
        header.pack(fill="x")
        self.logo = load_image("logo-48.png")
        if self.logo:
            ttk.Label(header, image=self.logo, background=BLACK).pack(side="left", padx=(0, 12))
        titles = ttk.Frame(header, style="Header.TFrame")
        titles.pack(side="left")
        ttk.Label(titles, text="CAD Checkout", style="Title.TLabel").pack(anchor="w")
        self.sub = ttk.Label(titles, text=self.repo_name, style="Sub.TLabel")
        self.sub.pack(anchor="w")
        self.busy_label = ttk.Label(header, text="", style="Busy.TLabel")
        self.busy_label.pack(side="right")

        # Thin orange strip under the header; a segment slides along it while busy
        self.strip = tk.Canvas(root, height=3, background=BLACK, highlightthickness=0,
                               borderwidth=0)
        self.strip.pack(fill="x")
        self.strip_pos = None  # None = idle (solid strip)
        self.strip.bind("<Configure>", lambda e: self._draw_strip())

        # ----- stats row
        stats = ttk.Frame(root, padding=(18, 12, 18, 8))
        stats.pack(fill="x")
        self.stats = ttk.Label(stats, text="Loading...", style="Stat.TLabel")
        self.stats.pack(side="left")
        self.banner = ttk.Label(stats, text="", style="Warn.TLabel")
        self.banner.pack(side="right")

        # ----- file table
        card = tk.Frame(root, background=BORDER, padx=1, pady=1)  # 1px border
        inner = tk.Frame(card, background=SURFACE)
        inner.pack(fill="both", expand=True)
        cols = ("lock", "change", "sw")
        self.tree = ttk.Treeview(inner, columns=cols, selectmode="extended")
        self.tree.heading("#0", text="  FILE", anchor="w")
        self.tree.heading("lock", text="LOCK", anchor="w")
        self.tree.heading("change", text="YOUR CHANGES", anchor="w")
        self.tree.heading("sw", text="SOLIDWORKS", anchor="w")
        self.tree.column("#0", width=440, minwidth=200)
        self.tree.column("lock", width=200, minwidth=120)
        self.tree.column("change", width=130, minwidth=90)
        self.tree.column("sw", width=110, minwidth=80)
        self.tree.tag_configure("you", foreground=ORANGE)
        self.tree.tag_configure("them", foreground=MUTED)  # someone else's: greyed out
        self.tree.tag_configure("free", foreground=TEXT)
        scroll = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.empty = ttk.Label(inner, style="Empty.TLabel",
                               text="No SolidWorks files yet\n\nSave parts into cad/parts/ "
                                    "and they'll show up here")

        # ----- bottom section. Packed bottom-up *before* the table so that when the
        # window is small the table shrinks instead of pushing these off-screen.
        self.logbox = tk.Text(root, height=7, wrap="word", state="disabled", font=(MONO, 9),
                              background="#0b0b0c", foreground=MUTED, relief="flat",
                              borderwidth=0, padx=10, pady=8, highlightthickness=1,
                              highlightbackground=BORDER, highlightcolor=BORDER,
                              selectbackground=GREY, insertbackground=ORANGE)
        self.logbox.tag_configure("cmd", foreground="#6f7276")
        self.logbox.tag_configure("ok", foreground=ORANGE)
        self.logbox.tag_configure("fail", foreground=RED)
        self.logbox.pack(side="bottom", fill="x", padx=18, pady=(0, 18))
        ttk.Label(root, text="ACTIVITY", style="Section.TLabel",
                  padding=(18, 14, 18, 4)).pack(side="bottom", anchor="w")
        ttk.Label(root, text="Double-click a file to lock it and open it in SolidWorks",
                  style="Stat.TLabel", padding=(18, 0)).pack(side="bottom", anchor="w")

        bar = ttk.Frame(root, padding=(18, 12))
        bar.pack(side="bottom", fill="x")
        start = ttk.Button(bar, text="Start editing", style="Primary.TButton",
                           command=self.start_editing)
        done = ttk.Button(bar, text="Done editing", style="Primary.TButton",
                          command=self.done_editing)
        open_ = ttk.Button(bar, text="Open read-only", command=self.open_in_sw)
        discard = ttk.Button(bar, text="Discard changes", style="Danger.TButton",
                             command=self.discard)
        refresh = ttk.Button(bar, text="↻  Refresh", command=self.refresh)
        start.pack(side="left", padx=(0, 8))
        done.pack(side="left", padx=(0, 20))
        open_.pack(side="left", padx=(0, 8))
        discard.pack(side="left")
        refresh.pack(side="right")
        self.buttons = [start, done, open_, discard, refresh]

        card.pack(fill="both", expand=True, padx=18)  # last: gets whatever space is left

        # Fast refresh whenever the window comes back into focus from another app.
        # FocusIn/Out also fire when focus moves between our own widgets, so only
        # count it as "left" if nothing in this app has focus a moment later.
        root.bind("<FocusOut>", lambda e: root.after(50, self._check_left_app))
        root.bind("<FocusIn>", self._on_focus_in)

        root.after(100, self.poll_events)
        root.after(AUTO_REFRESH_MS, self.auto_refresh)
        self.refresh()

    # ----- plumbing
    def log(self, msg):  # thread-safe
        self.events.put(("log", msg))

    def _append_log(self, msg):
        tag = ("cmd" if msg.startswith("$ ") else "ok" if msg.startswith("OK: ")
               else "fail" if msg.startswith("FAILED: ") else ())
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n", tag)
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def step(self, *args):
        """Run one git command from a worker thread; raise StepFailed on error."""
        self.log("$ git " + " ".join(args))
        code, out, err = git(self.repo, *args)
        text = (out + err).strip()
        if text:
            self.log(text)
        if code:
            raise StepFailed(text.splitlines()[-1] if text else "git %s failed" % args[0])
        return out

    def set_busy(self, busy):
        self.busy = busy
        for b in self.buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        self.root.configure(cursor="watch" if busy else "")
        self.busy_label.configure(text="WORKING…" if busy else "")
        if busy and self.strip_pos is None:
            self.strip_pos = 0.0
            self._animate_strip()

    def _draw_strip(self):
        c = self.strip
        c.delete("all")
        w = c.winfo_width()
        if self.strip_pos is None:
            c.create_rectangle(0, 0, w, 3, fill=ORANGE, width=0)
        else:
            seg = max(w // 4, 60)
            x = int(self.strip_pos * (w + seg)) - seg
            c.create_rectangle(x, 0, x + seg, 3, fill=ORANGE, width=0)

    def _animate_strip(self):
        if not self.busy:
            self.strip_pos = None  # back to a solid strip
        else:
            self.strip_pos = (self.strip_pos + 0.015) % 1.0
            self.root.after(16, self._animate_strip)
        self._draw_strip()

    def run_async(self, title, work):
        """Run work() in a thread; it returns a success message or raises."""
        self.set_busy(True)

        def target():
            try:
                msg = work()
                self.events.put(("done", (True, title, msg)))
            except StepFailed as e:
                self.events.put(("done", (False, title, str(e))))
            except Exception as e:  # noqa: BLE001 - surface anything to the user
                self.events.put(("done", (False, title, repr(e))))
            self.events.put(("state", load_state(self.repo)))

        threading.Thread(target=target, daemon=True).start()

    def poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "state":
                    self.show_state(payload)
                    self.set_busy(False)
                elif kind == "fast_state":
                    self.fast_pending = False
                    if not self.busy:  # a full refresh/action in flight wins
                        self.show_state(payload)
                elif kind == "done":
                    ok, title, msg = payload
                    self._append_log(("OK: " if ok else "FAILED: ") + (msg or title))
                    if ok:
                        if msg:
                            messagebox.showinfo(title, msg, parent=self.root)
                    else:
                        messagebox.showerror(title, msg, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def refresh(self):
        if self.busy:
            return
        self.set_busy(True)

        def target():
            self.events.put(("state", load_state(self.repo)))

        threading.Thread(target=target, daemon=True).start()

    def fast_refresh(self):
        """Local-only refresh (changes, SolidWorks open/closed); keeps known locks."""
        if self.busy or self.fast_pending:
            return
        self.fast_pending = True
        cached = self.state

        def target():
            self.events.put(("fast_state", load_state(self.repo, cached=cached)))

        threading.Thread(target=target, daemon=True).start()

    def _check_left_app(self):
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):  # focus is in a native dialog / popdown
            focused = None
        if focused is None:
            self.window_active = False

    def _on_focus_in(self, _event):
        if not self.window_active:
            self.window_active = True
            self.fast_refresh()

    def auto_refresh(self):
        if not self.busy:
            self.refresh()
        self.root.after(AUTO_REFRESH_MS, self.auto_refresh)

    def show_state(self, state):
        self.state = state
        selected = set(self.tree.selection())
        scroll = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        self.rows_by_path = {}
        for r in state["rows"]:
            self.rows_by_path[r["path"]] = r
            dot = "●" if r["lock"] != "free" else "○"
            self.tree.insert("", "end", iid=r["path"], text="  " + r["path"],
                             tags=(r["lock"],),
                             values=("%s  %s" % (dot, lock_label(r)), r["change"],
                                     "●  Open" if r["open"] else ""))
        keep = [p for p in selected if p in self.rows_by_path]
        if keep:
            self.tree.selection_set(keep)
        self.tree.yview_moveto(scroll)  # don't jump back to the top on refresh

        if state["rows"]:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

        up = state["upstream"] or "not connected to GitHub"
        self.sub.configure(text="%s   ·   %s → %s"
                           % (self.repo_name, state["branch"], up))
        rows = state["rows"]
        counts = [
            (len(rows), "files"),
            (sum(r["lock"] == "you" for r in rows), "locked by you"),
            (sum(r["lock"] == "them" for r in rows), "locked by others"),
            (sum(bool(r["change"]) for r in rows), "changed"),
        ]
        self.stats.configure(text="     ·     ".join("%d %s" % c for c in counts))
        err = state.get("lock_error")
        self.banner.configure(text=("⚠  Lock status unavailable: " + err) if err else "")

    def selected(self):
        rows = [self.rows_by_path[p] for p in self.tree.selection() if p in self.rows_by_path]
        if not rows:
            messagebox.showinfo("Nothing selected", "Select one or more files first "
                                "(Ctrl+click to select several).", parent=self.root)
        return rows

    def need_upstream(self):
        if not self.state.get("upstream"):
            messagebox.showerror("Not connected", "This repo isn't connected to GitHub yet "
                                 "(no upstream branch), so locking and pushing can't work.",
                                 parent=self.root)
            return False
        return True

    # ----- actions
    def on_double_click(self, event):
        """Double-click a row: lock it (if needed), pull, then open in SolidWorks."""
        path = self.tree.identify_row(event.y)
        row = self.rows_by_path.get(path)
        if not row or self.busy:
            return
        if row["lock"] == "you" or row["change"] == "New":
            self.open_paths([path])  # already ours / not in the repo yet: nothing to lock
            return
        if row["lock"] == "them":
            if messagebox.askyesno("Locked by " + row["owner"],
                                   "%s is locked by %s.\n\nOpen it read-only to look at it?"
                                   % (path, row["owner"]), parent=self.root):
                self.open_paths([path])
            return
        if not self.state.get("upstream"):
            self.log("Not connected to GitHub; opening %s read-only." % path)
            self.open_paths([path])
            return
        self.start_editing([row], open_after=True)

    def start_editing(self, rows=None, open_after=False):
        rows = rows or self.selected()
        if not rows or not self.need_upstream():
            return
        theirs = [r for r in rows if r["lock"] == "them"]
        if theirs:
            messagebox.showwarning("Locked by someone else", "Ask them to finish first:\n\n" +
                                   "\n".join("%s  (%s)" % (r["path"], r["owner"]) for r in theirs),
                                   parent=self.root)
            return
        to_lock = [r["path"] for r in rows if r["lock"] == "free" and r["change"] != "New"]
        was_open = [r["path"] for r in rows if r["open"] and r["path"] in to_lock]

        def work():
            for p in to_lock:  # lock first, so nobody can push between our pull and lock
                self.step("lfs", "lock", p)
            self.step("pull", "--ff-only")
            if was_open:
                return ("Locked. These were already open read-only, so close and reopen "
                        "them in SolidWorks:\n\n" + "\n".join(was_open))
            if open_after:
                self.open_paths([r["path"] for r in rows])
                return ""  # no popup, SolidWorks opening is the confirmation
            return "You can now edit:\n\n" + "\n".join(r["path"] for r in rows)

        self.run_async("Start editing", work)

    def done_editing(self):
        rows = self.selected()
        if not rows:
            return
        theirs = [r for r in rows if r["lock"] == "them"]
        if theirs:
            messagebox.showerror("Locked by someone else", "\n".join(
                "%s  (%s)" % (r["path"], r["owner"]) for r in theirs), parent=self.root)
            return
        still_open = [r["path"] for r in rows if r["open"]]
        if still_open:
            messagebox.showerror("Close in SolidWorks first",
                                 "Save and close these in SolidWorks before checking in:\n\n"
                                 + "\n".join(still_open), parent=self.root)
            return
        changed = [r["path"] for r in rows if r["change"]]
        lock_first = [r["path"] for r in rows
                      if r["lock"] == "free" and r["change"] in ("Modified", "Deleted")]
        to_unlock = [r["path"] for r in rows if r["lock"] == "you"] + lock_first
        if not changed and not to_unlock:
            messagebox.showinfo("Nothing to do", "No changes and nothing locked by you.",
                                parent=self.root)
            return
        if not self.need_upstream():
            return
        message = None
        if changed:
            message = self.ask_commit_message(changed)
            if not message:
                return

        def work():
            for p in lock_first:  # edited without locking: grab the lock before pushing
                self.step("lfs", "lock", p)
            if changed:
                self.step("add", "--all", "--", *changed)
                self.step("commit", "-m", message, "--", *changed)
                try:
                    self.step("pull", "--rebase", "--autostash")
                except StepFailed:
                    git(self.repo, "rebase", "--abort")
                    raise StepFailed("Couldn't merge with GitHub's latest version. Your commit "
                                     "is saved locally; ask for help before retrying.")
                self.step("push")
            for p in to_unlock:
                self.step("lfs", "unlock", p)
            return ("Pushed and unlocked." if changed else "Unlocked (no changes).") + \
                "\n\n" + "\n".join(r["path"] for r in rows)

        self.run_async("Done editing", work)

    def ask_commit_message(self, files):
        """Themed replacement for simpledialog.askstring. Returns the message or None."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Done editing")
        dlg.configure(background=BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        body = ttk.Frame(dlg, padding=(22, 18, 22, 18))
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="What did you change?", style="DlgTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="One line, e.g. “Thicken blade toe to 9 mm”",
                  style="Stat.TLabel").pack(anchor="w", pady=(2, 10))
        entry = tk.Entry(body, width=58, font=(FONT, 11), background=SURFACE, foreground=TEXT,
                         insertbackground=ORANGE, relief="flat", highlightthickness=2,
                         highlightbackground=BORDER, highlightcolor=ORANGE,
                         selectbackground=GREY, selectforeground=TEXT)
        entry.pack(fill="x", ipady=7)

        ttk.Label(body, text="CHECKING IN", style="Section.TLabel").pack(anchor="w",
                                                                       pady=(16, 4))
        shown = files[:8] + (["… and %d more" % (len(files) - 8)] if len(files) > 8 else [])
        ttk.Label(body, text="\n".join(shown), style="Mono.TLabel").pack(anchor="w")

        result = {"msg": None}

        def ok(_event=None):
            msg = entry.get().strip()
            if not msg:
                entry.configure(highlightbackground=RED, highlightcolor=RED)
                return
            result["msg"] = msg
            dlg.destroy()

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(20, 0))
        ttk.Button(btns, text="Commit & push", style="Primary.TButton",
                   command=ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

        dlg.update_idletasks()  # centre over the main window
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 3
        dlg.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        style_window(dlg)
        dlg.grab_set()
        entry.focus_set()
        self.root.wait_window(dlg)
        return result["msg"]

    def discard(self):
        rows = self.selected()
        if not rows:
            return
        if any(r["open"] for r in rows):
            messagebox.showerror("Close in SolidWorks first",
                                 "Close the files in SolidWorks (without saving) first.",
                                 parent=self.root)
            return
        revert = [r["path"] for r in rows if r["change"] in ("Modified", "Deleted")]
        to_unlock = [r["path"] for r in rows if r["lock"] == "you"]
        if not revert and not to_unlock:
            messagebox.showinfo("Nothing to discard", "No changes or locks on these files.",
                                parent=self.root)
            return
        if revert and not messagebox.askyesno(
                "Discard changes?", "This permanently throws away your changes to:\n\n"
                + "\n".join(revert) + "\n\nContinue?", icon="warning", parent=self.root):
            return

        def work():
            for p in revert:
                self.step("restore", "--staged", "--worktree", "--", p)
            for p in to_unlock:
                self.step("lfs", "unlock", p)
            return "Discarded changes and released locks."

        self.run_async("Discard", work)

    def open_in_sw(self):
        """Button: open without locking (read-only unless already yours)."""
        rows = self.selected()
        for r in rows:
            if r["lock"] != "you" and r["change"] != "New":
                self.log("Note: %s is not locked by you; it will open read-only. "
                         "Double-click it instead to lock and open." % r["path"])
        self.open_paths([r["path"] for r in rows])

    def open_paths(self, paths):
        """Open files with their default app (SolidWorks). Safe from worker threads."""
        for p in paths:
            full = os.path.join(self.repo, p)
            if not os.path.exists(full):
                self.log("Can't open %s: file not found." % p)
                continue
            self.log("Opening " + p)
            try:
                os.startfile(full)  # Windows only
            except OSError as e:
                self.log("Can't open %s: %s" % (p, e))


def main():
    repo = find_repo()
    if "--check" in sys.argv:
        def ask(title, msg, kind):
            print("[%s] %s" % (title, msg))
            return False
        print("git:", GIT)
        if not preflight(repo, ask, fix=False):
            return
        stubs = missing_lfs_files(repo)
        if stubs:
            print("not downloaded (pointer files):", ", ".join(stubs))
        st = load_state(repo)
        print("branch:", st["branch"], "| upstream:", st["upstream"])
        if st["lock_error"]:
            print("lock status unavailable:", st["lock_error"])
        for r in st["rows"]:
            print("%-60s %-22s %-9s %s" % (r["path"], lock_label(r), r["change"],
                                           "OPEN" if r["open"] else ""))
        return
    if os.name == "nt":
        try:  # own taskbar entry + icon instead of grouping under python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MAE156.CADCheckout")
        except Exception:  # noqa: BLE001 - cosmetic
            pass
    root = tk.Tk()
    root.withdraw()  # hidden until setup checks pass, so dialogs still show under pythonw
    try:  # window, taskbar and dialog icon
        root.iconbitmap(default=os.path.join(ASSETS, "logo.ico"))
    except tk.TclError:
        pass

    def ask(title, msg, kind):
        if kind == "yesno":
            return messagebox.askyesno(title, msg, parent=root)
        messagebox.showerror(title, msg, parent=root)
        return False

    if not preflight(repo, ask):
        root.destroy()
        return
    root.deiconify()
    App(root, repo)
    root.mainloop()


if __name__ == "__main__":
    main()
