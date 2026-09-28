"""mây remote Client - giao diện cài đặt (tkinter). Đóng gói thành .exe bằng PyInstaller."""
import ctypes
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk

import client

APP = "MayRemote"
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CFG_FILE = os.path.join(CFG_DIR, "client.json")


def load_cfg():
    d = {"host": "", "port": 5900, "token": "", "fullscreen": False, "history": []}
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


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_cfg()
        root.title("mây remote Client")
        root.resizable(False, False)

        self.v_host = tk.StringVar(value=self.cfg["host"])
        self.v_port = tk.StringVar(value=str(self.cfg["port"]))
        self.v_token = tk.StringVar(value=self.cfg["token"])
        self.v_full = tk.BooleanVar(value=self.cfg["fullscreen"])

        f = ttk.Frame(root, padding=14)
        f.grid()
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="IP máy chơi game").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Combobox(f, textvariable=self.v_host, values=self.cfg["history"], width=24).grid(
            row=0, column=1, sticky="ew")

        ttk.Label(f, text="Port").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=1024, to=65535, textvariable=self.v_port, width=10).grid(
            row=1, column=1, sticky="w")

        ttk.Label(f, text="Token").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.v_token, show="•", width=27).grid(row=2, column=1, sticky="ew")

        ttk.Checkbutton(f, text="Mở toàn màn hình", variable=self.v_full).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Button(f, text="Kết nối", command=self.connect).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Label(f, text="Trong lúc chơi:  F8 = bắt chuột (FPS)   F11 = toàn màn hình",
                  foreground="#666").grid(row=5, column=0, columnspan=2, sticky="w")

        root.bind("<Return>", lambda e: self.connect())

    def connect(self):
        host = self.v_host.get().strip()
        token = self.v_token.get()
        try:
            port = int(self.v_port.get())
        except ValueError:
            messagebox.showerror("Sai giá trị", "Port phải là số.")
            return
        if not host or not token:
            messagebox.showerror("Thiếu thông tin", "Nhập IP và token của server.")
            return

        hist = [h for h in self.cfg["history"] if h != host]
        self.cfg.update(host=host, port=port, token=token, fullscreen=self.v_full.get(),
                        history=[host] + hist[:7])
        save_cfg(self.cfg)

        self.root.withdraw()
        err = None
        try:
            client.run(host, port, token, self.v_full.get())
        except Exception as e:
            err = str(e)
        self.root.deiconify()
        if err:
            messagebox.showerror("Không kết nối được", err)


def main():
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
  
