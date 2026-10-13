"""Double-click desktop UI for a one-shot ChatGPT continue timer."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from automation import Target, worker
from timer import Timer, countdown, duration_seconds


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.timer = Timer()
        self.targets: list[Target] = []
        self.target: Target | None = None
        self.process = None
        self.queue = None
        self.operation = ""
        self.worker_started = 0.0
        root.title("Continue Timer")
        root.geometry("560x560")
        root.minsize(560, 560)
        root.configure(background="#f5f7fa")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 11))
        style.configure("TFrame", background="#f5f7fa")
        style.configure("TLabel", background="#f5f7fa", foreground="#182638")
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Clock.TLabel", font=("Segoe UI", 32), foreground="#125d52")
        style.configure("TButton", padding=(12, 8))
        style.configure("Primary.TButton", background="#125d52", foreground="white")
        style.map("Primary.TButton", background=[("active", "#0c493f"), ("disabled", "#dce2e8")],
                  foreground=[("disabled", "#536171")])
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Continue, later.", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text='Send "continue" once to your open ChatGPT or Codex chat.').pack(anchor="w", pady=(6, 20))
        ttk.Label(frame, text="ChatGPT / Codex window").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 16))
        self.windows = ttk.Combobox(row, state="readonly")
        self.windows.bind("<<ComboboxSelected>>", self.selection_changed)
        self.windows.pack(side="left", fill="x", expand=True)
        self.refresh = ttk.Button(row, text="Find window", command=self.find)
        self.refresh.pack(side="right", padx=(10, 0))
        ttk.Label(frame, text="Send after").pack(anchor="w")
        fields = ttk.Frame(frame)
        fields.pack(anchor="w", pady=(6, 0))
        self.values = [tk.StringVar(value=value) for value in ("3", "0", "0")]
        self.inputs = []
        for index, (label, value) in enumerate(zip(("Hours", "Minutes", "Seconds"), self.values)):
            group = ttk.Frame(fields)
            group.grid(row=0, column=index, padx=(0, 16))
            entry = ttk.Spinbox(group, from_=0, to=168 if index == 0 else 59, width=6, textvariable=value)
            entry.pack()
            self.inputs.append(entry)
            ttk.Label(group, text=label).pack(anchor="w", pady=(4, 0))
        self.clock = tk.StringVar(value="03:00:00")
        ttk.Label(frame, textvariable=self.clock, style="Clock.TLabel").pack(anchor="w", pady=(16, 4))
        self.status = tk.StringVar(value="Open your conversation, then click Find window.")
        ttk.Label(frame, textvariable=self.status, wraplength=485).pack(anchor="w", fill="x")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(16, 12))
        self.start = ttk.Button(buttons, text="Start timer", style="Primary.TButton", command=self.arm, state="disabled")
        self.start.pack(side="left")
        self.cancel = ttk.Button(buttons, text="Cancel timer", command=self.cancel_timer, state="disabled")
        self.cancel.pack(side="left", padx=(10, 0))
        ttk.Label(frame, text="Keep this app open and your PC awake and unlocked.\n"
                  "Existing drafts are never overwritten.", wraplength=485).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.tick)

    def busy_controls(self, busy: bool):
        self.refresh.configure(state="disabled" if busy else "normal")
        self.windows.configure(state="disabled" if busy else "readonly")
        for entry in self.inputs:
            entry.configure(state="disabled" if busy else "normal")
        self.start.configure(state="disabled" if busy or not self.targets else "normal")

    def launch_worker(self, operation: str):
        self.operation = operation
        self.queue = mp.get_context("spawn").Queue()
        self.process = mp.get_context("spawn").Process(
            target=worker, args=(operation, self.target, self.queue), daemon=True,
        )
        self.process.start()
        self.worker_started = time.monotonic()
        self.busy_controls(True)

    def find(self):
        self.status.set("Looking for accessible ChatGPT / Codex conversations...")
        self.launch_worker("discover")

    def selection_changed(self, event=None):
        index = self.windows.current()
        if index < 0:
            return
        reason = self.targets[index].unavailable_reason
        self.status.set(f"Window found. {reason} You can schedule a later check."
                        if reason else "Choose the delay, then start the timer.")

    def arm(self):
        try:
            seconds = duration_seconds(*(value.get() for value in self.values))
            index = self.windows.current()
            if index < 0:
                raise ValueError("Select a ChatGPT or Codex window first.")
            self.target = self.targets[index]
            self.timer.start(seconds)
        except ValueError as error:
            messagebox.showerror("Cannot start timer", str(error), parent=self.root)
            return
        self.busy_controls(True)
        self.cancel.configure(state="normal")
        due = datetime.fromtimestamp(self.timer.deadline).strftime("%I:%M:%S %p")
        self.status.set(f'Scheduled for {due}. Will send "continue" once.')

    def cancel_timer(self):
        self.timer.cancel()
        self.cancel.configure(state="disabled")
        self.busy_controls(False)
        self.status.set("Timer cancelled. Nothing was sent.")

    def finish_worker(self, kind: str, result):
        operation = self.operation
        self.cleanup_worker()
        if kind == "error":
            self.status.set(result)
        elif operation == "discover":
            self.targets = result
            self.windows.configure(values=[
                f"{target.app_name}: {target.title} (window {target.handle})" for target in result
            ])
            if result:
                self.windows.current(0)
                self.selection_changed()
            else:
                self.windows.set("")
                self.status.set("No accessible ChatGPT / Codex conversation found. Make its tab or "
                                "desktop window visible, then try again.")
        else:
            self.status.set(result.message)
        self.busy_controls(False)

    def cleanup_worker(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=2)
            self.process.close()
            self.process = None
        if self.queue is not None:
            self.queue.close()
            self.queue = None

    def tick(self):
        if self.timer.deadline is not None:
            self.clock.set(countdown(self.timer.remaining()))
        if self.timer.take_due():
            self.clock.set("00:00:00")
            self.cancel.configure(state="disabled")
            self.status.set("Checking the conversation and submitting...")
            self.launch_worker("send")
        if self.process is not None:
            try:
                kind, result = self.queue.get_nowait()
            except queue.Empty:
                if time.monotonic() - self.worker_started > 30:
                    self.finish_worker("error", "Accessibility operation timed out. Check ChatGPT "
                                       "before trying again; submission will not be retried.")
                elif not self.process.is_alive():
                    # Queue's feeder can finish just after the child exits.
                    try:
                        kind, result = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        self.finish_worker("error", "Accessibility worker exited unexpectedly. "
                                           "Check ChatGPT before trying again.")
                    else:
                        self.finish_worker(kind, result)
            else:
                self.finish_worker(kind, result)
        self.root.after(100, self.tick)

    def close(self):
        if self.timer.deadline is not None or self.process is not None:
            if not messagebox.askyesno("Close Continue Timer?",
                                      "Closing stops the timer and any pending operation. Close?",
                                      parent=self.root):
                return
        self.timer.cancel()
        self.cleanup_worker()
        self.root.destroy()


def main() -> int:
    mp.freeze_support()
    root = tk.Tk()
    if os.name != "nt":
        root.withdraw()
        messagebox.showerror("Windows required", "This app uses Windows accessibility. "
                             "macOS and Linux are not supported.")
        root.destroy()
        return 1
    app = App(root)
    if "--smoke-test" in sys.argv:
        root.update()
        assert app.clock.get() == "03:00:00"
        assert str(app.start["state"]) == "disabled"
        app.launch_worker("discover")
        try:
            kind, result = app.queue.get(timeout=35)
            if kind != "ok":
                raise RuntimeError(result)
        finally:
            app.cleanup_worker()
            root.destroy()
        return 0
    root.after(0, app.find)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
