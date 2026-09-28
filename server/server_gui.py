"""mây remote Server - giao diện cài đặt (tkinter). Đóng gói thành .exe bằng PyInstaller."""
import argparse
import ctypes
import json
import os
import queue
import secrets
import socket
import string
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import server

APP = "MayRemote"
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CFG_FILE = os.path.join(CFG_DIR, "server.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
HEIGHTS = ["Gốc", "1440", "1080", "900", "720", "480"]
ENCODERS = ["auto", "h264_nvenc", "h264_amf", "h264_qsv", "libx264"]


def new_token():
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def load_cfg():
    d = {"port": 5900, "token": new_token(), "fps": 60, "bitrate": 20, "height": "Gốc",
         "encoder": "auto", "monitor": 0, "audio": True, "autostart": False}
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            d.update(json.load(f))
    except Exception:
        pass
    return d


def save_cfg(d):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def local_ips():
    ips = []
    try:  # IP đang dùng để ra mạng (không gửi gói nào cả)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips or ["(không xác định)"]


def set_autostart(on):
    import winreg
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}" --autostart'
    else:
        cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --autostart'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP + "Server", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, APP + "Server")
            except FileNotFoundError:
                pass


class App:
    def __init__(self, root, autostart_flag=False):
        self.root = root
        self.q = queue.Queue()
        self.thread = None
        cfg = load_cfg()

        root.title("mây remote Server")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.v = {
            "port": tk.StringVar(value=str(cfg["port"])),
            "token": tk.StringVar(value=cfg["token"]),
            "fps": tk.StringVar(value=str(cfg["fps"])),
            "bitrate": tk.StringVar(value=str(cfg["bitrate"])),
            "height": tk.StringVar(value=str(cfg["height"])),
            "encoder": tk.StringVar(value=cfg["encoder"]),
            "monitor": tk.StringVar(value=str(cfg["monitor"])),
            "audio": tk.BooleanVar(value=cfg["audio"]),
            "autostart": tk.BooleanVar(value=cfg["autostart"]),
        }

        f = ttk.Frame(root, padding=12)
        f.grid(sticky="nsew")
        f.columnconfigure(1, weight=1)
        r = 0

        def row(label, widget):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=r, column=1, sticky="ew", pady=3)
            r += 1

        row("Port", ttk.Spinbox(f, from_=1024, to=65535, textvariable=self.v["port"], width=12))

        tok = ttk.Frame(f)
        ttk.Entry(tok, textvariable=self.v["token"]).pack(side="left", fill="x", expand=True)
        ttk.Button(tok, text="Tạo mới", width=8,
                   command=lambda: self.v["token"].set(new_token())).pack(side="left", padx=(6, 0))
        row("Token (mật khẩu)", tok)

        row("FPS", ttk.Combobox(f, textvariable=self.v["fps"], values=["30", "60", "90", "120"], width=12))
        row("Bitrate (Mbps)", ttk.Spinbox(f, from_=2, to=200, textvariable=self.v["bitrate"], width=12))
        row("Độ phân giải (chiều cao)", ttk.Combobox(f, textvariable=self.v["height"], values=HEIGHTS,
                                                      state="readonly", width=12))
        row("Encoder", ttk.Combobox(f, textvariable=self.v["encoder"], values=ENCODERS,
                                    state="readonly", width=12))
        row("Màn hình số", ttk.Spinbox(f, from_=0, to=7, textvariable=self.v["monitor"], width=12))

        ttk.Checkbutton(f, text="Truyền âm thanh", variable=self.v["audio"]).grid(
            row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Checkbutton(f, text="Tự chạy server cùng Windows", variable=self.v["autostart"]).grid(
            row=r, column=0, columnspan=2, sticky="w"); r += 1

        self.info = ttk.Label(f, foreground="#0a6b3d", justify="left")
        self.info.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 4)); r += 1
        self.refresh_info()

        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=2, sticky="ew", pady=4); r += 1
        self.btn = ttk.Button(btns, text="Bắt đầu server", command=self.toggle)
        self.btn.pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="Mở firewall", command=self.open_firewall).pack(side="left", padx=(6, 0))

        self.status = ttk.Label(f, text="Đang tắt")
        self.status.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        self.txt = tk.Text(f, height=9, width=58, state="disabled", wrap="word")
        self.txt.grid(row=r, column=0, columnspan=2, sticky="nsew", pady=(4, 0))

        self.poll()
        if autostart_flag:
            root.iconify()
            root.after(800, self.start)

    # ---------------------------------------------------------------- helpers
    def refresh_info(self):
        self.info.config(text="IP máy này: " + ", ".join(local_ips()) +
                         f"\nNhập IP + Port {self.v['port'].get()} + Token vào app điện thoại / client.")

    def log(self, msg):
        self.txt.config(state="normal")
        self.txt.insert("end", msg + "\n")
        if int(self.txt.index("end-1c").split(".")[0]) > 500:
            self.txt.delete("1.0", "100.0")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def collect(self):
        try:
            port = int(self.v["port"].get())
            fps = int(self.v["fps"].get())
            bitrate = float(self.v["bitrate"].get())
            monitor = int(self.v["monitor"].get())
        except ValueError:
            messagebox.showerror("Sai giá trị", "Port / FPS / Bitrate / Màn hình phải là số.")
            return None
        token = self.v["token"].get().strip()
        if not token:
            messagebox.showerror("Thiếu token", "Nhập token hoặc bấm 'Tạo mới'.")
            return None
        h = self.v["height"].get()
        return argparse.Namespace(
            host="0.0.0.0", port=port, token=token, fps=fps, bitrate=bitrate,
            height=0 if h == "Gốc" else int(h), monitor=monitor,
            encoder=self.v["encoder"].get(), no_audio=not self.v["audio"].get())

    def save(self):
        cfg = {k: v.get() for k, v in self.v.items()}
        for k in ("port", "fps", "monitor"):
            try:
                cfg[k] = int(cfg[k])
            except ValueError:
                pass
        try:
            cfg["bitrate"] = float(cfg["bitrate"])
        except ValueError:
            pass
        save_cfg(cfg)
        try:
            set_autostart(bool(cfg["autostart"]))
        except Exception as e:
            self.log(f"[gui] không đặt được autostart: {e}")

    # ---------------------------------------------------------------- actions
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def toggle(self):
        if self.running():
            server.STOP.set()
        else:
            self.start()

    def start(self):
        if self.running():
            return
        args = self.collect()
        if not args:
            return
        self.save()
        self.refresh_info()
        server.LOG = self.q.put
        server.STOP.clear()
        self.thread = threading.Thread(target=self._run, args=(args,), daemon=True)
        self.thread.start()

    def _run(self, args):
        try:
            server.run_server(args)
        except Exception as e:
            self.q.put(f"[lỗi] {e}")

    def open_firewall(self):
        try:
            port = int(self.v["port"].get())
        except ValueError:
            messagebox.showerror("Sai giá trị", "Port phải là số.")
            return
        params = (f'advfirewall firewall add rule name="mayremote {port}" '
                  f'dir=in action=allow protocol=TCP localport={port}')
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", params, None, 0)
        messagebox.showinfo("Firewall", "Đã gửi lệnh mở port (cần bấm Yes ở hộp thoại quyền Admin).")

    def poll(self):
        try:
            while True:
                self.log(self.q.get_nowait())
        except queue.Empty:
            pass
        running = self.running()
        stopping = running and server.STOP.is_set()
        if stopping:
            self.btn.config(text="Đang dừng...", state="disabled")
            self.status.config(text="Đang dừng...")
        elif running:
            self.btn.config(text="Dừng server", state="normal")
            self.status.config(text="● Đang chạy")
        else:
            self.btn.config(text="Bắt đầu server", state="normal")
            self.status.config(text="Đang tắt")
        self.root.after(200, self.poll)

    def on_close(self):
        server.STOP.set()
        self.save()
        self.root.destroy()


def main():
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    root = tk.Tk()
    App(root, autostart_flag="--autostart" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
      
