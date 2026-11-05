#!/usr/bin/env python3
"""CAD Checkout: lock, edit and check in SolidWorks files without the command line.

Wraps the Git LFS locking workflow in a small window:

    Start editing  ->  lock the file(s), then pull the latest version
    Done editing   ->  commit, pull --rebase, push, then unlock
    Discard        ->  throw away your changes and unlock
    Open           ->  open in SolidWorks (or double-click a row)

It refuses to check in a file that is still open in SolidWorks (it looks for
SolidWorks' "~$<name>" lock file next to the part).

Standard library only (tkinter). Python 3.8+.
Run with:  python tools/cad_checkout.py         (GUI)
           python tools/cad_checkout.py --check (print status and exit)
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashes on Windows
AUTO_REFRESH_MS = 60_000


# --------------------------------------------------------------------------- git helpers

def git(repo, *args, input_text=None):
    """Run git and return (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(
            ["git", *args], cwd=repo, input=input_text, capture_output=True,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
    except FileNotFoundError:
        return 127, "", "git is not installed or not on PATH"
    return p.returncode, p.stdout, p.stderr


def find_repo():
    here = os.path.dirname(os.path.abspath(__file__))
    code, out, err = git(here, "rev-parse", "--show-toplevel")
    if code:
        raise SystemExit("cad_checkout must live inside the git repo:\n" + err)
    return os.path.normpath(out.strip())


def sw_lock_file(repo, path):
    """SolidWorks writes ~$<name> next to a file while it is open."""
    d, name = os.path.split(os.path.join(repo, path))
    return os.path.join(d, "~$" + name)


def load_state(repo):
    """Collect everything the table needs. Slow-ish (hits GitHub for locks)."""
    state = {"rows": [], "lock_error": None, "branch": "?", "upstream": None}

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

    # Locks on the server
    locks = {}
    code, out, err = git(repo, "lfs", "locks", "--verify", "--json")
    if code == 0:
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


# --------------------------------------------------------------------------- GUI

class App:
    def __init__(self, root, repo):
        self.root, self.repo = root, repo
        self.state = {"rows": [], "upstream": None}
        self.rows_by_path = {}
        self.busy = False
        self.events = queue.Queue()

        root.title("CAD Checkout - " + os.path.basename(repo))
        root.geometry("900x560")
        root.minsize(640, 400)

        top = ttk.Frame(root, padding=(10, 8))
        top.pack(fill="x")
        self.info = ttk.Label(top, text="Loading...")
        self.info.pack(side="left")
        self.banner = ttk.Label(top, text="", foreground="#b00020")
        self.banner.pack(side="right")

        cols = ("lock", "change", "sw")
        self.tree = ttk.Treeview(root, columns=cols, selectmode="extended")
        self.tree.heading("#0", text="File")
        self.tree.heading("lock", text="Lock")
        self.tree.heading("change", text="Your changes")
        self.tree.heading("sw", text="SolidWorks")
        self.tree.column("#0", width=430)
        self.tree.column("lock", width=180)
        self.tree.column("change", width=110, anchor="center")
        self.tree.column("sw", width=100, anchor="center")
        self.tree.tag_configure("you", foreground="#1b7f3b")
        self.tree.tag_configure("them", foreground="#b00020")
        self.tree.pack(fill="both", expand=True, padx=10)
        self.tree.bind("<Double-1>", lambda e: self.open_in_sw())

        bar = ttk.Frame(root, padding=(10, 8))
        bar.pack(fill="x")
        self.buttons = [
            ttk.Button(bar, text="Start editing", command=self.start_editing),
            ttk.Button(bar, text="Done editing...", command=self.done_editing),
            ttk.Button(bar, text="Discard my changes", command=self.discard),
            ttk.Button(bar, text="Open in SolidWorks", command=self.open_in_sw),
            ttk.Button(bar, text="Refresh", command=self.refresh),
        ]
        for b in self.buttons:
            b.pack(side="left", padx=(0, 6))

        self.logbox = tk.Text(root, height=9, wrap="word", state="disabled",
                              font=("Consolas", 9), background="#f6f6f6")
        self.logbox.pack(fill="x", padx=10, pady=(0, 10))

        root.after(100, self.poll_events)
        root.after(AUTO_REFRESH_MS, self.auto_refresh)
        self.refresh()

    # ----- plumbing
    def log(self, msg):  # thread-safe
        self.events.put(("log", msg))

    def _append_log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
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

    def auto_refresh(self):
        if not self.busy:
            self.refresh()
        self.root.after(AUTO_REFRESH_MS, self.auto_refresh)

    def show_state(self, state):
        self.state = state
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.rows_by_path = {}
        for r in state["rows"]:
            self.rows_by_path[r["path"]] = r
            self.tree.insert("", "end", iid=r["path"], text=r["path"], tags=(r["lock"],),
                             values=(lock_label(r), r["change"], "Open" if r["open"] else ""))
        keep = [p for p in selected if p in self.rows_by_path]
        if keep:
            self.tree.selection_set(keep)
        up = state["upstream"] or "not connected to GitHub"
        self.info.configure(text="Branch: %s  ->  %s    |    %d SolidWorks files"
                            % (state["branch"], up, len(state["rows"])))
        err = state.get("lock_error")
        self.banner.configure(text=("Lock status unavailable: " + err) if err else "")

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
    def start_editing(self):
        rows = self.selected()
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
            msg = "You can now edit:\n\n" + "\n".join(r["path"] for r in rows)
            if was_open:
                msg += ("\n\nThese were already open read-only. Close and reopen them in "
                        "SolidWorks:\n" + "\n".join(was_open))
            return msg

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
            message = simpledialog.askstring(
                "Commit message",
                "Briefly, what did you change?\n(e.g. 'Thicken blade toe to 9 mm')\n\n"
                + "\n".join(changed), parent=self.root)
            if not message or not message.strip():
                return
            message = message.strip()

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
        rows = self.selected()
        for r in rows:
            full = os.path.join(self.repo, r["path"])
            if os.path.exists(full):
                if r["lock"] != "you" and r["change"] != "New":
                    self.log("Note: %s is not locked by you; it will open read-only." % r["path"])
                os.startfile(full)  # Windows: opens with SolidWorks


def main():
    repo = find_repo()
    if "--check" in sys.argv:
        st = load_state(repo)
        print("branch:", st["branch"], "| upstream:", st["upstream"])
        if st["lock_error"]:
            print("lock status unavailable:", st["lock_error"])
        for r in st["rows"]:
            print("%-60s %-22s %-9s %s" % (r["path"], lock_label(r), r["change"],
                                           "OPEN" if r["open"] else ""))
        return
    root = tk.Tk()
    App(root, repo)
    root.mainloop()


if __name__ == "__main__":
    main()
