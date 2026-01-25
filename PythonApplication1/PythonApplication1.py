#!/usr/bin/env python3
"""
Single-file Smart Medicine Reminder (Tkinter GUI) with Profile page.

Features:
- Persist medications and user profile to meds.json
- Two scheduling modes: fixed times (HH:MM list) and interval hours
- Background reminder thread with GUI prompt (Taken / Snooze)
- Profile page (name, age, weight, conditions, default snooze, notifications)
- Optional desktop notifications via plyer (if installed)
- Works cross-platform with Python 3.8+
"""

import json
import os
import threading
import time
import queue
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import simpledialog, messagebox, ttk

try:
   # from plyer import notification
    HAS_PLYER = True
except Exception:
    HAS_PLYER = False

DATA_FILE = "meds.json"
CHECK_INTERVAL_SECONDS = 20

# ----------------- scheduling utilities -----------------


def now():
    return datetime.now()


def parse_time(hhmm: str):
    return datetime.strptime(hhmm, "%H:%M").time()


def format_dt(dt: datetime):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"medications": [], "log": [], "profile": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def ensure_profile_defaults(data):
    profile = data.setdefault("profile", {})
    profile.setdefault("name", "")
    profile.setdefault("age", "")
    profile.setdefault("weight", "")
    profile.setdefault("conditions", "")
    profile.setdefault("default_snooze", 10)
    profile.setdefault("notifications", True)


def next_time_for_fixed_times(times_of_day, after_dt=None):
    after_dt = after_dt or now()
    today = after_dt.date()
    for t in sorted(times_of_day):
        candidate = datetime.combine(today, parse_time(t))
        if candidate >= after_dt:
            return candidate
    tomorrow = today + timedelta(days=1)
    return datetime.combine(tomorrow, parse_time(sorted(times_of_day)[0]))


def next_time_for_interval(last_taken_iso, hours_interval, after_dt=None):
    after_dt = after_dt or now()
    if last_taken_iso:
        last_taken = datetime.fromisoformat(last_taken_iso)
        candidate = last_taken + timedelta(hours=hours_interval)
        while candidate < after_dt:
            candidate += timedelta(hours=hours_interval)
        return candidate
    return after_dt


def schedule_next(med):
    if med.get("type") == "fixed":
        med["next_due"] = next_time_for_fixed_times(med["times"], now()).isoformat()
    else:
        med["next_due"] = next_time_for_interval(med.get("last_taken"), med["interval_hours"], now()).isoformat()


# ----------------- background reminder thread -----------------


class ReminderThread(threading.Thread):
    def __init__(self, data, event_queue: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.data = data
        self.q = event_queue
        self.stop_event = stop_event

    def run(self):
        while not self.stop_event.is_set():
            current = now()
            for idx, med in enumerate(self.data.get("medications", [])):
                next_due_iso = med.get("next_due")
                if not next_due_iso:
                    schedule_next(med)
                    save_data(self.data)
                    continue
                try:
                    next_due = datetime.fromisoformat(next_due_iso)
                except Exception:
                    schedule_next(med)
                    save_data(self.data)
                    continue
                # if due (or past due), enqueue event
                if current >= next_due:
                    # send an event; UI will handle retries / snooze / taken
                    self.q.put({"type": "due", "index": idx, "due": med["next_due"]})
            time.sleep(CHECK_INTERVAL_SECONDS)


# ----------------- GUI dialogs -----------------


class AddEditDialog(simpledialog.Dialog):
    def __init__(self, parent, title, med=None):
        self.med = med
        super().__init__(parent, title)

    def body(self, master):
        tk.Label(master, text="Name:").grid(row=0, column=0, sticky="e")
        self.e_name = tk.Entry(master, width=30)
        self.e_name.grid(row=0, column=1, padx=4, pady=2)

        tk.Label(master, text="Dose:").grid(row=1, column=0, sticky="e")
        self.e_dose = tk.Entry(master, width=30)
        self.e_dose.grid(row=1, column=1, padx=4, pady=2)

        tk.Label(master, text="Mode:").grid(row=2, column=0, sticky="e")
        self.mode = tk.StringVar(value="fixed")
        ttk.Radiobutton(master, text="Fixed times", variable=self.mode, value="fixed", command=self._toggle).grid(row=2, column=1, sticky="w")
        ttk.Radiobutton(master, text="Interval (hours)", variable=self.mode, value="interval", command=self._toggle).grid(row=2, column=1, sticky="e")

        tk.Label(master, text="Times (HH:MM, comma):").grid(row=3, column=0, sticky="e")
        self.e_times = tk.Entry(master, width=30)
        self.e_times.grid(row=3, column=1, padx=4, pady=2)

        tk.Label(master, text="Interval hours:").grid(row=4, column=0, sticky="e")
        self.e_interval = tk.Entry(master, width=10)
        self.e_interval.grid(row=4, column=1, sticky="w", padx=4, pady=2)

        if self.med:
            self.e_name.insert(0, self.med.get("name", ""))
            self.e_dose.insert(0, self.med.get("dose", ""))
            if self.med.get("type") == "fixed":
                self.mode.set("fixed")
                self.e_times.insert(0, ", ".join(self.med.get("times", [])))
            else:
                self.mode.set("interval")
                self.e_interval.insert(0, str(self.med.get("interval_hours", "")))
        self._toggle()
        return self.e_name

    def _toggle(self):
        mode = self.mode.get()
        if mode == "fixed":
            self.e_times.config(state="normal")
            self.e_interval.config(state="disabled")
        else:
            self.e_times.config(state="disabled")
            self.e_interval.config(state="normal")

    def apply(self):
        name = self.e_name.get().strip()
        dose = self.e_dose.get().strip()
        mode = self.mode.get()
        med = {"name": name, "dose": dose, "created": now().isoformat(), "last_taken": None}
        if mode == "fixed":
            times = [t.strip() for t in self.e_times.get().split(",") if t.strip()]
            med.update({"type": "fixed", "times": times})
        else:
            hours = float(self.e_interval.get().strip())
            med.update({"type": "interval", "interval_hours": hours})
        self.result = med


class ProfileDialog(simpledialog.Dialog):
    """Dialog to view/edit user profile (medical topic)."""
    def __init__(self, parent, title, profile):
        self.profile = profile
        super().__init__(parent, title)

    def body(self, master):
        tk.Label(master, text="Name:").grid(row=0, column=0, sticky="e")
        self.e_name = tk.Entry(master, width=30)
        self.e_name.grid(row=0, column=1, padx=4, pady=2)

        tk.Label(master, text="Age:").grid(row=1, column=0, sticky="e")
        self.e_age = tk.Entry(master, width=10)
        self.e_age.grid(row=1, column=1, sticky="w", padx=4, pady=2)

        tk.Label(master, text="Weight (kg):").grid(row=2, column=0, sticky="e")
        self.e_weight = tk.Entry(master, width=10)
        self.e_weight.grid(row=2, column=1, sticky="w", padx=4, pady=2)

        tk.Label(master, text="Conditions (comma-separated):").grid(row=3, column=0, sticky="ne")
        self.e_conditions = tk.Text(master, width=30, height=4)
        self.e_conditions.grid(row=3, column=1, padx=4, pady=2)

        tk.Label(master, text="Default snooze (minutes):").grid(row=4, column=0, sticky="e")
        self.e_snooze = tk.Entry(master, width=10)
        self.e_snooze.grid(row=4, column=1, sticky="w", padx=4, pady=2)

        self.notifications_var = tk.BooleanVar(value=True)
        self.chk_notify = ttk.Checkbutton(master, text="Enable desktop notifications", variable=self.notifications_var)
        self.chk_notify.grid(row=5, column=1, sticky="w", padx=4, pady=2)

        # populate
        self.e_name.insert(0, self.profile.get("name", ""))
        self.e_age.insert(0, str(self.profile.get("age", "")))
        self.e_weight.insert(0, str(self.profile.get("weight", "")))
        self.e_conditions.insert("1.0", self.profile.get("conditions", ""))
        self.e_snooze.insert(0, str(self.profile.get("default_snooze", 10)))
        self.notifications_var.set(bool(self.profile.get("notifications", True)))
        return self.e_name

    def apply(self):
        self.result = {
            "name": self.e_name.get().strip(),
            "age": self.e_age.get().strip(),
            "weight": self.e_weight.get().strip(),
            "conditions": self.e_conditions.get("1.0", "end").strip(),
            "default_snooze": int(self.e_snooze.get().strip() or 10),
            "notifications": bool(self.notifications_var.get())
        }


# ----------------- main application -----------------


class ReminderApp:
    def __init__(self, root):
        self.root = root
        root.title("Smart Medicine Reminder")
        self.data = load_data()
        ensure_profile_defaults(self.data)

        for med in self.data.get("medications", []):
            if not med.get("next_due"):
                schedule_next(med)
        save_data(self.data)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None

        # UI components
        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)

        self.lb = tk.Listbox(frame, height=12, width=72)
        self.lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.lb.yview)
        sb.pack(side="left", fill="y")
        self.lb.config(yscrollcommand=sb.set)

        btn_frame = ttk.Frame(root, padding=8)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Add", command=self.add_med).pack(side="left")
        ttk.Button(btn_frame, text="Edit", command=self.edit_med).pack(side="left")
        ttk.Button(btn_frame, text="Remove", command=self.remove_med).pack(side="left")
        self.start_btn = ttk.Button(btn_frame, text="Start Reminders", command=self.toggle_thread)
        self.start_btn.pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Take Now", command=self.take_now).pack(side="left")
        ttk.Button(btn_frame, text="Snooze", command=self.snooze_now).pack(side="left")
        ttk.Button(btn_frame, text="View Log", command=self.view_log).pack(side="left")
        ttk.Button(btn_frame, text="Profile", command=self.edit_profile).pack(side="right")

        self.status = ttk.Label(root, text="")
        self.status.pack(fill="x", padx=8, pady=4)

        self.refresh_list()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_queue()

    def refresh_list(self):
        self.lb.delete(0, tk.END)
        for i, m in enumerate(self.data.get("medications", [])):
            nd = m.get("next_due") or "not scheduled"
            if isinstance(nd, str):
                try:
                    # try to shorten isoformat
                    nd = datetime.fromisoformat(nd).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            line = f"[{i}] {m['name']} - {m['dose']}  next: {nd}"
            self.lb.insert(tk.END, line)
        prof = self.data.get("profile", {})
        name = prof.get("name") or "(no profile)"
        self.status.config(text=f"User: {name}   Med count: {len(self.data.get('medications', []))}")

    def add_med(self):
        dlg = AddEditDialog(self.root, "Add medication")
        if dlg.result:
            med = dlg.result
            schedule_next(med)
            self.data.setdefault("medications", []).append(med)
            save_data(self.data)
            self.refresh_list()

    def edit_med(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showinfo("Edit", "Select a medication to edit.")
            return
        idx = sel[0]
        med = self.data["medications"][idx]
        dlg = AddEditDialog(self.root, "Edit medication", med=med)
        if dlg.result:
            new_med = dlg.result
            # preserve last_taken if present
            new_med["last_taken"] = med.get("last_taken")
            schedule_next(new_med)
            self.data["medications"][idx] = new_med
            save_data(self.data)
            self.refresh_list()

    def remove_med(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showinfo("Remove", "Select a medication to remove.")
            return
        idx = sel[0]
        med = self.data["medications"].pop(idx)
        save_data(self.data)
        self.refresh_list()
        messagebox.showinfo("Removed", f"Removed {med['name']}")

    def toggle_thread(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=2)
            self.thread = None
            self.stop_event.clear()
            self.start_btn.config(text="Start Reminders")
        else:
            self.thread = ReminderThread(self.data, self.q, self.stop_event)
            self.thread.start()
            self.start_btn.config(text="Stop Reminders")

    def take_now(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showinfo("Take", "Select a medication to mark as taken.")
            return
        idx = sel[0]
        self._acknowledge_taken(idx)

    def snooze_now(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showinfo("Snooze", "Select a medication to snooze.")
            return
        idx = sel[0]
        # use profile default snooze as initial value
        default = int(self.data.get("profile", {}).get("default_snooze", 10))
        minutes = simpledialog.askinteger("Snooze", "Snooze minutes:", initialvalue=default, minvalue=1, maxvalue=1440)
        if minutes:
            self._snooze(idx, minutes)

    def view_log(self):
        logs = self.data.get("log", [])
        if not logs:
            messagebox.showinfo("Log", "No log entries.")
            return
        text = "\n".join(f"{l['taken_at']} - {l['med']} - {l['dose']}" for l in logs[-200:])
        top = tk.Toplevel(self.root)
        top.title("Taken Log")
        txt = tk.Text(top, wrap="none", width=80, height=20)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.config(state="disabled")

    def edit_profile(self):
        prof = self.data.get("profile", {})
        dlg = ProfileDialog(self.root, "User Profile (medical)", prof)
        if dlg.result:
            self.data["profile"].update(dlg.result)
            save_data(self.data)
            self.refresh_list()
            messagebox.showinfo("Profile", "Profile saved.")

    # internal actions
    def _acknowledge_taken(self, idx):
        med = self.data["medications"][idx]
        med["last_taken"] = now().isoformat()
        if med.get("type") == "fixed":
            med["next_due"] = next_time_for_fixed_times(med["times"], now()).isoformat()
        else:
            med["next_due"] = next_time_for_interval(med["last_taken"], med["interval_hours"]).isoformat()
        self.data.setdefault("log", []).append({
            "med": med["name"],
            "dose": med["dose"],
            "taken_at": med["last_taken"]
        })
        save_data(self.data)
        self.refresh_list()
        message = f"{med['name']} taken at {format_dt(datetime.fromisoformat(med['last_taken']))}"
        self._notify("Logged", message)

    def _snooze(self, idx, minutes=10):
        med = self.data["medications"][idx]
        new_due = now() + timedelta(minutes=minutes)
        med["next_due"] = new_due.isoformat()
        save_data(self.data)
        self.refresh_list()
        self._notify("Snoozed", f"{med['name']} snoozed until {format_dt(new_due)}")

    def _notify(self, title, message):
        # print to console and use desktop notify if available and enabled via profile
        print(f"[{format_dt(now())}] {title} - {message}")
        prof = self.data.get("profile", {})
        if prof.get("notifications", True) and HAS_PLYER:
            try:
                notification.notify(title=title, message=message, timeout=5)
            except Exception:
                pass

    # queue processing for due reminders
    def poll_queue(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev.get("type") == "due":
                    idx = ev["index"]
                    # Protect against deleted meds
                    if idx < 0 or idx >= len(self.data.get("medications", [])):
                        continue
                    med = self.data["medications"][idx]
                    # show prompt window
                    self.show_due_dialog(idx, med)
        except queue.Empty:
            pass
        self.root.after(1000, self.poll_queue)

    def show_due_dialog(self, idx, med):
        # bring a small dialog with Taken / Snooze options
        def on_taken():
            top.destroy()
            self._acknowledge_taken(idx)

        def on_snooze():
            default = int(self.data.get("profile", {}).get("default_snooze", 10))
            minutes = simpledialog.askinteger("Snooze", "Snooze minutes:", initialvalue=default, minvalue=1, maxvalue=1440, parent=top)
            top.destroy()
            if minutes:
                self._snooze(idx, minutes)

        # include profile summary in the notification message
        prof = self.data.get("profile", {})
        profile_text = f"User: {prof.get('name','(no profile)')}"
        self._notify("Medication due", f"{med['name']} - {med['dose']}   ({profile_text})")
        top = tk.Toplevel(self.root)
        top.title("Medication due")
        top.transient(self.root)
        top.grab_set()
        ttk.Label(top, text=f"{med['name']} - {med['dose']}").pack(padx=12, pady=8)
        ttk.Button(top, text="Taken", command=on_taken).pack(side="left", padx=8, pady=8)
        ttk.Button(top, text="Snooze", command=on_snooze).pack(side="left", padx=8, pady=8)

    def on_close(self):
        if self.thread and self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=2)
        save_data(self.data)
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ReminderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()