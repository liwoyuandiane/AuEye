import tkinter as tk
from tkinter import ttk, messagebox
import requests
import json
import csv
import ctypes
import random
import os
import time
import threading
import queue
import sys
import smtplib
import logging
from email.mime.text import MIMEText
from collections import deque
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item

# 提示音（Windows 内置模块；非 Windows 或导入失败时静默降级）
try:
    import winsound
except Exception:
    winsound = None

# DPI 适配
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

DEFAULT_CONFIG = {
    "refresh_interval": 2,
    "opacity": 0.55,
    "au_upper_enabled": False,
    "au_upper_value": 0.0,
    "au_lower_enabled": True,
    "au_lower_value": 900.0,
    "extreme_window_min": 4,
    "extreme_threshold": 5.0,
    "extreme_enabled": True,
    "extreme_flash_times": 6,
    "extreme_flash_interval_ms": 150,
    "extreme_cooldown_sec": 60,
    "alert_cooldown_sec": 10,
    # 邮件提醒间隔：价格在阈值上方时，每隔多久发一封邮件（秒）。最低300秒（5分钟）
    "email_interval_sec": 600,
    "sound_enabled": True,
    "log_enabled": False,
    "history_enabled": False,
    "email_enabled": False,
    "email_sender": "",
    "email_password": "",
    "email_receiver": "",
    "smtp_host": "smtp.163.com",
    "smtp_port": 465,
    "smtp_ssl": True,
}


def _config_path():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "config.json")


def _history_path():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "aueye_history.csv")


def _log_file():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "aueye.log")


log = logging.getLogger("gold")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    log.addHandler(_sh)


def _attach_log_file():
    for h in list(log.handlers):
        if isinstance(h, logging.FileHandler):
            return
    try:
        _fh = logging.FileHandler(_log_file(), encoding="utf-8")
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(_fh)
    except Exception:
        pass


def _detach_log_file():
    for h in list(log.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                h.close()
            except Exception:
                pass
            log.removeHandler(h)


def _taskbar_rect():
    """获取任务栏的屏幕矩形 (left, top, right, bottom)。失败返回 None。"""
    try:
        from ctypes import wintypes
        class RECT(ctypes.Structure):
            _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),("right",ctypes.c_long),("bottom",ctypes.c_long)]
        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize",wintypes.DWORD),("hWnd",wintypes.HWND),("uCallbackMessage",wintypes.UINT),
                        ("uEdge",wintypes.UINT),("rc",RECT),("lParam",ctypes.c_long)]
        apt = APPBARDATA(); apt.cbSize = ctypes.sizeof(APPBARDATA)
        if ctypes.windll.shell32.SHAppBarMessage(5, ctypes.byref(apt)):
            r = apt.rc; return (r.left, r.top, r.right, r.bottom)
    except Exception: pass
    return None

def _find_font(size=22, bold=False):
    """寻找可用字体。bold=True 优先 Bold 版，回退 Regular。"""
    names = ("arialbd.ttf","segoeuib.ttf","msyhbd.ttc") if bold else ("arial.ttf","segoeui.ttf","msyh.ttc")
    for name in names:
        for dir_ in [r"C:\Windows\Fonts", os.path.expanduser(r"~\.fonts")]:
            p = os.path.join(dir_, name)
            if os.path.exists(p):
                try: return ImageFont.truetype(p, size)
                except Exception: pass
    # 所有Bold字体都找不到时，回退Regular
    for name in ("arial.ttf","segoeui.ttf","msyh.ttc"):
        for dir_ in [r"C:\Windows\Fonts", os.path.expanduser(r"~\.fonts")]:
            p = os.path.join(dir_, name)
            if os.path.exists(p):
                try: return ImageFont.truetype(p, size)
                except Exception: pass
    try: return ImageFont.truetype("arial.ttf", size)
    except Exception: return ImageFont.load_default()


# ---------------------------------------------------------------------------
class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("设置")
        self.geometry("380x520")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(padx=12, pady=12, expand=True, fill="both")

        self.tab_general = ttk.Frame(self.notebook)
        self.tab_alerts  = ttk.Frame(self.notebook)
        self.tab_extreme = ttk.Frame(self.notebook)
        self.tab_email   = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_general, text="通用")
        self.notebook.add(self.tab_alerts,  text="提醒")
        self.notebook.add(self.tab_extreme, text="异动")
        self.notebook.add(self.tab_email,   text="邮件")

        self._build_general()
        self._build_alerts()
        self._build_extreme()
        self._build_email()

        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(footer, text="保存并应用", command=self._save).pack(side="right")
        ttk.Button(footer, text="取消", command=self._on_close).pack(side="right", padx=(0, 8))

        self.after(50, self.focus_force)

    def _build_general(self):
        frame = ttk.Frame(self.tab_general)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(frame, text="刷新间隔（秒）").grid(row=0, column=0, sticky="w")
        self.var_interval = tk.StringVar(value=str(self.app.interval))
        ttk.Entry(frame, textvariable=self.var_interval, width=12).grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(frame, text="透明度（0.2~1.0）").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.var_opacity = tk.DoubleVar(value=float(self.app.root.attributes("-alpha")))
        ttk.Scale(frame, variable=self.var_opacity, from_=0.2, to=1.0, orient="horizontal",
                  command=self._on_opacity_change).grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(12, 0))
        self.opacity_value_label = ttk.Label(frame, text=f"{self.var_opacity.get():.2f}")
        self.opacity_value_label.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(12, 0))

        self.var_sound_enabled = tk.BooleanVar(value=self.app.sound_enabled)
        ttk.Checkbutton(frame, text="声音提醒（阈值/异动时提示音）",
                        variable=self.var_sound_enabled).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))

        self.var_log_enabled = tk.BooleanVar(value=self.app.log_enabled)
        ttk.Checkbutton(frame, text="记录日志文件 aueye.log",
                        variable=self.var_log_enabled).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.var_history_enabled = tk.BooleanVar(value=self.app.history_enabled)
        ttk.Checkbutton(frame, text="记录历史价格 aueye_history.csv",
                        variable=self.var_history_enabled).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        frame.columnconfigure(1, weight=1)

    def _build_alerts(self):
        frame = ttk.Frame(self.tab_alerts)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.var_au_upper_enabled = tk.BooleanVar(value=self.app.au_upper_target is not None)
        self.var_au_upper_value   = tk.StringVar(value=str(self.app.au_upper_target or 0.0))
        self.var_au_lower_enabled = tk.BooleanVar(value=self.app.au_lower_target is not None)
        self.var_au_lower_value   = tk.StringVar(value=str(self.app.au_lower_target or 0.0))
        ttk.Label(frame, text="浙商金 上破提醒").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(frame, variable=self.var_au_upper_enabled).grid(row=0, column=1, sticky="w")
        ttk.Entry(frame, textvariable=self.var_au_upper_value, width=12).grid(row=0, column=2, sticky="e")
        ttk.Label(frame, text="浙商金 下破提醒").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(frame, variable=self.var_au_lower_enabled).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.var_au_lower_value, width=12).grid(row=1, column=2, sticky="e", pady=(10, 0))
        frame.columnconfigure(1, weight=1)

    def _build_extreme(self):
        frame = ttk.Frame(self.tab_extreme)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.var_extreme_enabled = tk.BooleanVar(value=self.app.extreme_enabled)
        ttk.Checkbutton(frame, text="启用异动提醒", variable=self.var_extreme_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="统计窗口（分钟）").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.var_window_min = tk.StringVar(value=str(int(self.app.extreme_window_sec / 60)))
        ttk.Entry(frame, textvariable=self.var_window_min, width=12).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Label(frame, text="异动阈值").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.var_extreme_threshold = tk.StringVar(value=str(self.app.extreme_threshold))
        ttk.Entry(frame, textvariable=self.var_extreme_threshold, width=12).grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Separator(frame).grid(row=3, column=0, columnspan=2, sticky="we", pady=16)
        ttk.Label(frame, text="闪烁次数").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.var_flash_times = tk.StringVar(value=str(self.app.extreme_flash_times))
        ttk.Entry(frame, textvariable=self.var_flash_times, width=12).grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Label(frame, text="闪烁间隔（毫秒）").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.var_flash_interval = tk.StringVar(value=str(self.app.extreme_flash_interval_ms))
        ttk.Entry(frame, textvariable=self.var_flash_interval, width=12).grid(row=5, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Label(frame, text="冷却时间（秒）").grid(row=6, column=0, sticky="w", pady=(10, 0))
        self.var_cooldown = tk.StringVar(value=str(self.app.extreme_cooldown_sec))
        ttk.Entry(frame, textvariable=self.var_cooldown, width=12).grid(row=6, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        frame.columnconfigure(1, weight=1)

    def _build_email(self):
        frame = ttk.Frame(self.tab_email)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.var_email_enabled = tk.BooleanVar(value=self.app.email_enabled)
        ttk.Checkbutton(frame, text="启用邮件提醒", variable=self.var_email_enabled).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="发件人地址").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.var_email_sender = tk.StringVar(value=self.app.email_sender)
        ttk.Entry(frame, textvariable=self.var_email_sender, width=36).grid(row=1, column=1, columnspan=3, sticky="we", padx=(10, 0), pady=(12, 0))
        ttk.Label(frame, text="授权码").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.var_email_password = tk.StringVar(value=self.app.email_password)
        ttk.Entry(frame, textvariable=self.var_email_password, width=36, show="*").grid(row=2, column=1, columnspan=3, sticky="we", padx=(10, 0), pady=(10, 0))
        ttk.Label(frame, text="收件人地址").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.var_email_receiver = tk.StringVar(value=self.app.email_receiver)
        ttk.Entry(frame, textvariable=self.var_email_receiver, width=36).grid(row=3, column=1, columnspan=3, sticky="we", padx=(10, 0), pady=(10, 0))
        ttk.Separator(frame).grid(row=4, column=0, columnspan=3, sticky="we", pady=14)
        ttk.Label(frame, text="SMTP 服务器").grid(row=5, column=0, sticky="w")
        self.var_smtp_host = tk.StringVar(value=self.app.smtp_host)
        ttk.Entry(frame, textvariable=self.var_smtp_host).grid(row=5, column=1, columnspan=2, sticky="we", padx=(10, 0), pady=(2, 2))

        ttk.Label(frame, text="端口").grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.var_smtp_port = tk.StringVar(value=str(self.app.smtp_port))
        ttk.Entry(frame, textvariable=self.var_smtp_port, width=10).grid(row=6, column=1, sticky="w", padx=(10, 0), pady=(6, 0))

        self.var_smtp_ssl = tk.BooleanVar(value=self.app.smtp_ssl)
        ttk.Checkbutton(frame, text="SSL 加密", variable=self.var_smtp_ssl).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Separator(frame).grid(row=7, column=0, columnspan=3, sticky="we", pady=10)
        ttk.Label(frame, text="邮件提醒间隔（分钟）").grid(row=8, column=0, sticky="w", pady=(4, 0))
        self.var_email_interval = tk.StringVar(value=str(max(5, int(self.app.email_interval_sec // 60))))
        ttk.Entry(frame, textvariable=self.var_email_interval, width=8).grid(row=8, column=1, sticky="w", padx=(10, 0), pady=(4, 0))
        ttk.Label(frame, text="≥5（价格停留阈值时按此间隔重复发邮件）", foreground="#9AA0A6").grid(row=9, column=0, columnspan=4, sticky="w")

        ttk.Button(frame, text="发送测试邮件", command=self._test_email).grid(row=10, column=0, columnspan=4, sticky="w", pady=(14, 0))
        frame.columnconfigure(1, weight=1)

    def _test_email(self):
        sender   = self.var_email_sender.get().strip()
        password = self.var_email_password.get().strip()
        receiver = self.var_email_receiver.get().strip()
        if not all([sender, password, receiver]):
            messagebox.showerror("错误", "请先填写发件人、授权码和收件人", parent=self)
            return
        def do_send():
            ok, err = self.app.send_email(sender, password, receiver,
                subject="浙商金监控 — 测试邮件",
                body="邮件提醒功能配置成功，此为测试邮件。")
            if ok:
                self.after(0, lambda: messagebox.showinfo("成功", "测试邮件发送成功", parent=self))
            else:
                self.after(0, lambda: messagebox.showerror("发送失败", f"错误信息：{err}", parent=self))
        threading.Thread(target=do_send, daemon=True).start()

    def _on_opacity_change(self, value):
        try:
            self.opacity_value_label.config(text=f"{float(value):.2f}")
        except Exception:
            return

    def _save(self):
        try:
            interval = float(self.var_interval.get().strip())
            if interval <= 0: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "刷新间隔必须是有效的正数", parent=self); return
        opacity = float(self.var_opacity.get())
        if not (0.2 <= opacity <= 1.0):
            messagebox.showerror("错误", "透明度必须在 0.2 到 1.0 之间", parent=self); return
        def pv(ev, vv):
            if not ev.get(): return None
            try: return float(vv.get().strip())
            except Exception: return None
        au_u = pv(self.var_au_upper_enabled, self.var_au_upper_value)
        au_l = pv(self.var_au_lower_enabled, self.var_au_lower_value)
        try:
            wm = int(float(self.var_window_min.get().strip()))
            if wm <= 0: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "统计窗口必须是有效的正整数（分钟）", parent=self); return
        try:
            et = float(self.var_extreme_threshold.get().strip())
            if et <= 0: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "异动阈值必须是有效的正数", parent=self); return
        try:
            ft = int(float(self.var_flash_times.get().strip()))
            if ft < 1 or ft > 50: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "闪烁次数必须是 1~50 的整数", parent=self); return
        try:
            fi = int(float(self.var_flash_interval.get().strip()))
            if fi < 50: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "闪烁间隔必须是不小于 50 的整数（毫秒）", parent=self); return
        try:
            cd = int(float(self.var_cooldown.get().strip()))
            if cd < 1: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "冷却时间必须是有效的正整数（秒）", parent=self); return
        try:
            sp = int(self.var_smtp_port.get().strip())
            if sp < 1 or sp > 65535: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "SMTP 端口必须是 1~65535 的整数", parent=self); return

        try:
            email_interval_min = int(float(self.var_email_interval.get().strip()))
            if email_interval_min < 5: raise ValueError()
        except Exception:
            messagebox.showerror("错误", "邮件提醒间隔必须 ≥ 5 分钟", parent=self); return

        new_cfg = {
            "refresh_interval": interval,
            "opacity": opacity,
            "au_upper_enabled": au_u is not None,
            "au_upper_value": float(au_u or 0.0),
            "au_lower_enabled": au_l is not None,
            "au_lower_value": float(au_l or 0.0),
            "extreme_enabled":           self.var_extreme_enabled.get(),
            "extreme_window_min":        wm,
            "extreme_threshold":         et,
            "extreme_flash_times":       ft,
            "extreme_flash_interval_ms": fi,
            "extreme_cooldown_sec":      cd,
            "email_enabled":  self.var_email_enabled.get(),
            "email_sender":   self.var_email_sender.get().strip(),
            "email_password": self.var_email_password.get().strip(),
            "email_receiver": self.var_email_receiver.get().strip(),
            "smtp_host": self.var_smtp_host.get().strip(),
            "smtp_port": sp,
            "smtp_ssl":  self.var_smtp_ssl.get(),
            "email_interval_sec": max(300, int(float(self.var_email_interval.get().strip()) * 60)),
            "sound_enabled":  self.var_sound_enabled.get(),
            "log_enabled":     self.var_log_enabled.get(),
            "history_enabled": self.var_history_enabled.get(),
        }
        self.app.apply_config(new_cfg)
        self.app.save_config()
        messagebox.showinfo("成功", "设置已保存并应用", parent=self)

    def _on_close(self):
        self.app.settings_window = None
        self.destroy()


# ---------------------------------------------------------------------------
class GoldTaskbarDoubleLine:
    """浙商金价监控。

    启动后默认最小化到系统托盘，托盘图标本身直接显示实时金价数字（Pillow 渲染）。
    右键托盘图标可切换显示完整悬浮卡片、打开设置、退出。
    """

    TRAY_ICON_SIZE = 256  # 超高分辨率源图，缩放后更清晰

    def __init__(self):
        self.config = self.load_config()
        self.root   = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(self.config.get("opacity", 0.55)))

        self.bg_color              = "#000000"
        self.card_color            = "#101218"
        self.card_border           = "#2A2D36"
        self.card_highlight_color  = "#1B1F2A"
        self.card_border_highlight = "#3A4150"
        self.text_color            = "#F5F5F7"
        self.muted_color           = "#9AA0A6"
        self.up_color              = "#FF3B30"
        self.down_color            = "#00C853"
        self.font_family           = "Segoe UI"
        self.text_font_size        = 10
        self.flash_text_font_size  = 12
        self.arrow_font_size       = 15
        self.card_width            = 200
        self.card_height           = 60
        self.corner_radius         = 15

        self.root.configure(bg=self.bg_color)
        self.root.attributes("-transparentcolor", self.bg_color)

        user32  = ctypes.windll.user32
        self.sw = user32.GetSystemMetrics(0)
        self.sh = user32.GetSystemMetrics(1)

        position = self._load_position()
        if position:
            x, y = position
        else:
            x = self.sw - self.card_width  - 20
            y = self.sh - self.card_height - 80
        self.root.geometry(f"{self.card_width}x{self.card_height}+{x}+{y}")

        # ===== 悬浮卡片画布 =====
        self.canvas = tk.Canvas(
            self.root, width=self.card_width, height=self.card_height,
            bg=self.bg_color, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.card_fill_items   = []
        self.card_border_items = []
        self._draw_card()

        # 卡片：浙商 + 金价 + 涨跌箭头（单行居中布局）
        self.au_icon = self.canvas.create_text(
            8, 28, text="浙商", fill="#4F8EF7",
            font=(self.font_family, 12, "bold"), anchor="w")
        self.root.update_idletasks()
        au_label_right = self.canvas.bbox(self.au_icon)[2] + 8
        self.au_value_text = self.canvas.create_text(
            au_label_right, 28, text="--", fill=self.text_color,
            font=(self.font_family, 14, "bold"), anchor="w")
        self.au_arrow_text = self.canvas.create_text(
            self.card_width - 8, 28, text="•", fill=self.muted_color,
            font=(self.font_family, 15, "bold"), anchor="e")

        self._bind_drag()

        # ===== HTTP Session =====
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Linux; arm_64; Android 10) AppleWebKit/537.36"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        })

        # ===== 价格监控目标 =====
        self.au_upper_target = None
        self.au_lower_target = 900.00
        self.au_upper_triggered = False
        self.au_lower_triggered = False
        self.au_upper_last_ts   = 0.0
        self.au_lower_last_ts   = 0.0
        self.alert_cooldown_sec = 10

        # 邮件独立冷却时间戳（价格在阈值上方时按间隔重复发邮件）
        self.email_au_upper_last_ts = 0.0
        self.email_au_lower_last_ts = 0.0
        self.email_interval_sec    = 600  # 默认10分钟，最低5分钟

        # 邮件 / SMTP
        self.email_enabled  = False
        self.email_sender   = ""
        self.email_password = ""
        self.email_receiver = ""
        self.smtp_host = "smtp.163.com"
        self.smtp_port = 465
        self.smtp_ssl  = True

        # 声音 / 日志 / 历史
        self.sound_enabled  = True
        self.log_enabled     = False
        self.history_enabled = False

        # ===== 线程共享价格 =====
        self.au      = None
        self.prev_au = None

        # ===== 异动检测 =====
        self.interval                = 2
        self.extreme_window_sec      = 300
        self.extreme_threshold       = 5.0
        self.extreme_enabled         = True
        self.extreme_flash_times     = 6
        self.extreme_flash_interval_ms = 150
        self.extreme_cooldown_sec    = 60
        self.au_history              = deque()
        self.extreme_last_ts         = 0.0
        self.flash_active            = False
        self._flash_after_id         = None

        # 线程同步
        self._price_lock      = threading.Lock()
        self._action_queue    = queue.Queue()
        self._fetch_fail_count = 0

        self.apply_config(self.config)

        self.settings_window = None
        self.tray   = None
        self.hidden = True          # 启动默认隐藏主卡片，只显示托盘
        self.root.withdraw()
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.data_thread = threading.Thread(target=self._data_fetch_loop, daemon=True)
        self.data_thread.start()
        self._update_ui_cycle()

    # ================================================================ config
    def load_config(self):
        try:
            path = _config_path()
            if not os.path.exists(path):
                return DEFAULT_CONFIG.copy()
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            return {**DEFAULT_CONFIG, **saved}
        except Exception:
            return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            with open(_config_path(), "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            return

    def apply_config(self, cfg):
        self.config   = {**self.config, **cfg}
        self.interval = float(self.config.get("refresh_interval", self.interval))
        self.root.attributes("-alpha", float(self.config.get("opacity", self.root.attributes("-alpha"))))

        def target(key, vk):
            if not self.config.get(key, False): return None
            try: return float(self.config.get(vk, 0.0))
            except Exception: return None
        self.au_upper_target = target("au_upper_enabled", "au_upper_value")
        self.au_lower_target = target("au_lower_enabled", "au_lower_value")

        self.extreme_window_sec      = int(self.config.get("extreme_window_min", int(self.extreme_window_sec/60))) * 60
        self.extreme_threshold       = float(self.config.get("extreme_threshold",  self.extreme_threshold))
        self.extreme_enabled         = bool(self.config.get("extreme_enabled",     self.extreme_enabled))
        self.extreme_flash_times     = int(self.config.get("extreme_flash_times",  self.extreme_flash_times))
        self.extreme_flash_interval_ms = int(self.config.get("extreme_flash_interval_ms", self.extreme_flash_interval_ms))
        self.extreme_cooldown_sec    = int(self.config.get("extreme_cooldown_sec", self.extreme_cooldown_sec))
        self.alert_cooldown_sec = int(self.config.get("alert_cooldown_sec", self.alert_cooldown_sec))

        self.email_interval_sec = max(300, int(self.config.get("email_interval_sec", 600)))  # 最低5分钟

        self.email_enabled  = bool(self.config.get("email_enabled",  False))
        self.email_sender   = str(self.config.get("email_sender",  ""))
        self.email_password = str(self.config.get("email_password", ""))
        self.email_receiver = str(self.config.get("email_receiver", ""))
        self.smtp_host = str(self.config.get("smtp_host", "smtp.163.com")).strip() or "smtp.163.com"
        self.smtp_port = int(self.config.get("smtp_port", 465))
        self.smtp_ssl  = bool(self.config.get("smtp_ssl",  True))

        self.sound_enabled     = bool(self.config.get("sound_enabled",     True))
        self.log_enabled       = bool(self.config.get("log_enabled",       False))
        self.history_enabled   = bool(self.config.get("history_enabled",   False))

        if self.log_enabled: _attach_log_file()
        else: _detach_log_file()
        self.au_history.clear()

    # ================================================================ 动态托盘图标
    def _make_tray_icon(self, price=None, prev_price=None):
        """生成圆形托盘图标：超大两行金价数字，字号填满圆内空间。"""
        sz = self.TRAY_ICON_SIZE

        # 涨跌配色
        if price is not None and prev_price is not None and price > prev_price:
            bg, fg = (200,35,35),  (255,255,255)    # 红底白字
        elif price is not None and prev_price is not None and price < prev_price:
            bg, fg = (25,140,60),  (255,255,255)    # 绿底白字
        else:
            bg, fg = (50,50,65),   (255,255,255)    # 深灰底白字

        # 透明背景 + 圆形裁剪
        img = Image.new("RGBA", (sz, sz), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([(0,0),(sz-1,sz-1)], fill=bg+(255,))

        # 两行数字
        if price is not None:
            s = f"{int(round(price))}".zfill(4)
        else:
            s = "--"
        line1, line2 = s[:2], s[2:]

        # ★ 字号拉满：每行字号 = 圆直径的 42%（两行叠起超圆高 → 溢出裁掉也不管）
        font_size = int(sz * 0.42)
        font = _find_font(size=font_size, bold=True)

        def draw_line(text, y_center):
            try:
                bbox = draw.textbbox((0,0), text, font=font)
                tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
            except Exception:
                tw, th = len(text)*font_size//2, font_size
            x = (sz - tw) / 2
            y = y_center - th/2
            draw.text((x, y), text, fill=fg+(255,), font=font)

        draw_line(line1, sz * 0.34)
        draw_line(line2, sz * 0.72)
        return img

    def _setup_tray(self):
        """创建系统托盘图标（动态显示金价数字，随价格刷新变化）。"""
        menu = (
            item("显示/隐藏金价卡片", self._tray_toggle),
            item("设置",             self._tray_settings),
            item("退出",             self._tray_quit),
        )
        init_icon = self._make_tray_icon(price=None)
        self.tray = pystray.Icon("AuEye", init_icon, "AuEye", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_toggle(self,   icon=None, menu_item=None): self.root.after(0, self.toggle_visible)
    def _tray_settings(self, icon=None, menu_item=None): self.root.after(0, self.open_settings)
    def _tray_quit(self,     icon=None, menu_item=None): self.root.after(0, self.quit_app)

    def toggle_visible(self):
        if self.hidden:
            self.root.deiconify(); self.hidden = False
        else:
            self.root.withdraw();  self.hidden = True

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify(); self.settings_window.lift(); self.settings_window.focus_force(); return
        self.settings_window = SettingsWindow(self.root, self)

    def quit_app(self):
        try:
            if self.tray is not None: self.tray.stop()
        except Exception: pass
        try: self.root.quit(); self.root.destroy()
        except Exception: pass

    # ================================================================ 卡片绘制
    def _draw_card(self):
        for it in self.card_fill_items:   self.canvas.delete(it)
        for it in self.card_border_items: self.canvas.delete(it)
        self.card_fill_items   = []
        self.card_border_items = []
        r, (x1, y1, x2, y2) = self.corner_radius, (0, 0, self.card_width, self.card_height)
        cc, cb = self.card_color, self.card_border
        self.card_fill_items += [
            self.canvas.create_rectangle(x1+r, y1, x2-r, y2, fill=cc, outline=cc),
            self.canvas.create_rectangle(x1, y1+r, x2, y2-r, fill=cc, outline=cc),
            self.canvas.create_oval(x1, y1, x1+2*r, y1+2*r, fill=cc, outline=cc),
            self.canvas.create_oval(x2-2*r, y1, x2, y1+2*r, fill=cc, outline=cc),
            self.canvas.create_oval(x1, y2-2*r, x1+2*r, y2, fill=cc, outline=cc),
            self.canvas.create_oval(x2-2*r, y2-2*r, x2, y2, fill=cc, outline=cc),
        ]
        self.card_border_items += [
            self.canvas.create_rectangle(x1+r, y1+1, x2-r, y1+2, fill=cb, outline=cb),
            self.canvas.create_rectangle(x1+r, y2-2, x2-r, y2-1, fill=cb, outline=cb),
            self.canvas.create_rectangle(x1+1, y1+r, x1+2, y2-r, fill=cb, outline=cb),
            self.canvas.create_rectangle(x2-2, y1+r, x2-1, y2-r, fill=cb, outline=cb),
        ]

    def _apply_card_style(self, fill_color, border_color):
        for it in self.card_fill_items:   self.canvas.itemconfig(it, fill=fill_color,   outline=fill_color)
        for it in self.card_border_items: self.canvas.itemconfig(it, fill=border_color, outline=border_color)

    def _reset_flash_style(self):
        self.canvas.itemconfig(self.au_value_text, fill=self.text_color,
                               font=(self.font_family, 14, "bold"))
        self.canvas.itemconfig(self.au_icon, fill="#4F8EF7",
                               font=(self.font_family, 12, "bold"))
        self._apply_card_style(self.card_color, self.card_border)
        self.flash_active = False

    # ================================================================ 拖拽
    def _bind_drag(self):
        for w in (self.canvas, self.root):
            w.bind("<ButtonPress-1>",  self._start_drag)
            w.bind("<B1-Motion>",      self._on_drag)
            w.bind("<ButtonRelease-1>",self._end_drag)

    def _start_drag(self, event):
        self.drag_offset_x = event.x_root - self.root.winfo_x()
        self.drag_offset_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event):
        self.root.geometry(f"{self.card_width}x{self.card_height}"
                           f"+{event.x_root - self.drag_offset_x}"
                           f"+{event.y_root - self.drag_offset_y}")

    def _end_drag(self, event):
        if hasattr(self, '_save_timer') and self._save_timer:
            self.root.after_cancel(self._save_timer)
        self._save_timer = self.root.after(500, self._save_position)

    def _save_position(self):
        try:
            self.config["window_x"] = self.root.winfo_x()
            self.config["window_y"] = self.root.winfo_y()
            self.save_config()
        except Exception: pass

    def _load_position(self):
        try:
            x, y = self.config.get("window_x"), self.config.get("window_y")
            if x is None or y is None: return None
            return int(x), int(y)
        except Exception: return None

    # ================================================================ 数据抓取
    def _fetch_au(self):
        """浙商银行财富金价格（京东金融，productSku=1961543816，元/克）。"""
        try:
            res = self.session.post(
                "https://api.jdjygold.com/gw2/generic/hj/h5/m/cfGetLatestPriceInfo",
                json={"reqData": {"productSku": "1961543816"}},
                headers={"Content-Type": "application/json", "Origin": "https://www.jd.com", "Referer": "https://www.jd.com/"},
                timeout=2)
            res.raise_for_status()
            d = res.json()
            if d.get("success") and d["resultData"].get("code") == "00000000":
                return float(d["resultData"]["data"]["price"])
        except Exception: pass
        return None

    # ================================================================ 通知
    def _notify(self, msg):
        try:
            if self.tray is not None and self.tray.visible:
                self.tray.notify(msg, "浙商金提醒"); return
        except Exception: pass
        log.info(f"通知: {msg}")

    def _play_sound(self):
        if not getattr(self, "sound_enabled", True) or winsound is None: return
        try:
            threading.Thread(target=winsound.Beep, args=(880, 180), daemon=True).start()
        except Exception: pass

    def send_email(self, sender, password, receiver, subject, body):
        host, port, ussl = getattr(self,"smtp_host","smtp.163.com"), getattr(self,"smtp_port",465), getattr(self,"smtp_ssl",True)
        try:
            msg = MIMEText(body, "plain", "utf-8"); msg["Subject"]=subject; msg["From"]=sender; msg["To"]=receiver
            smtp = smtplib.SMTP_SSL(host, port, timeout=10) if ussl else smtplib.SMTP(host, port, timeout=10)
            if not ussl: smtp.starttls()
            with smtp: smtp.login(sender, password); smtp.sendmail(sender, [receiver], msg.as_string())
            return True, ""
        except Exception as e: return False, str(e)

    def _send_alert_email(self, subject, body):
        if not self.email_enabled or not all([self.email_sender, self.email_password, self.email_receiver]): return
        threading.Thread(target=self.send_email, args=(self.email_sender, self.email_password, self.email_receiver, subject, body), daemon=True).start()

    # ================================================================ 提醒逻辑
    def _check_alert(self, price, symbol="AU"):
        """价格阈值穿越检测（仅浙商）。

        触发锁：首次穿越只触发一次（tray通知+声音+邮件），价格回落后重置。
        邮件重复：价格停留在阈值区间时，按 email_interval_sec（默认10分钟）
        间隔重复发送邮件提醒，直到价格回落。
        """
        if price is None: return
        now = time.time()
        entries = {"AU":[("au_upper", self.au_upper_target, "上破", "浙商金提醒 — 上破"),
                         ("au_lower", self.au_lower_target, "下破", "浙商金提醒 — 下破")]}
        for key, target, direction, subject in entries.get(symbol, []):
            if target is None: continue
            is_up  = "upper" in key
            trig   = getattr(self, f"{key}_triggered")
            cross  = (is_up and price >= target) or (not is_up and price <= target)
            reset  = (is_up and price < target)  or (not is_up and price > target)
            if cross and not trig:
                # 首次穿越：tray通知 + 声音 + 邮件
                if now - getattr(self, f"{key}_last_ts") >= self.alert_cooldown_sec:
                    msg = f"浙商金 {direction} {target}，当前价格：{price:.2f}"
                    self._notify(msg); self._play_sound(); self._send_alert_email(subject, msg)
                    setattr(self, f"{key}_last_ts", now)
                    setattr(self, f"email_{key}_last_ts", now)  # 同步邮件时间戳
                setattr(self, f"{key}_triggered", True)
            elif cross and trig:
                # 已触发且价格仍停留 → 按邮件间隔重复发邮件
                email_last = getattr(self, f"email_{key}_last_ts")
                if now - email_last >= self.email_interval_sec:
                    msg = f"浙商金 持续{direction} {target}，当前价格：{price:.2f}"
                    self._send_alert_email(subject, msg)
                    setattr(self, f"email_{key}_last_ts", now)
            elif reset:
                setattr(self, f"{key}_triggered", False)

    # ================================================================ 异动
    def _track_au_extreme(self, price):
        if not self.extreme_enabled: return
        now = time.time()
        self.au_history.append((now, price))
        cutoff = now - self.extreme_window_sec
        while self.au_history and self.au_history[0][0] < cutoff: self.au_history.popleft()
        if len(self.au_history) < 2: return
        delta = price - self.au_history[0][1]
        if abs(delta) < self.extreme_threshold: return
        if now - self.extreme_last_ts < self.extreme_cooldown_sec: return
        self.extreme_last_ts = now
        self._play_sound()
        self._action_queue.put(("flash", self.up_color if delta > 0 else self.down_color, self.extreme_flash_times))

    # ================================================================ 闪烁
    def _flash_text(self, color, times):
        if times <= 0: return
        if self._flash_after_id is not None:
            self.root.after_cancel(self._flash_after_id); self._flash_after_id = None; self._reset_flash_style()
        self.flash_active = True; total = times * 2
        def toggle(rem):
            if rem <= 0: self._reset_flash_style(); self._flash_after_id = None; return
            use = rem % 2 == 0
            self.canvas.itemconfig(self.au_value_text, fill=color if use else self.text_color,
                                   font=(self.font_family, self.flash_text_font_size if use else 14, "bold"))
            if use: self._apply_card_style(self.card_highlight_color, self.card_border_highlight)
            else:   self._apply_card_style(self.card_color, self.card_border)
            self._flash_after_id = self.root.after(self.extreme_flash_interval_ms, lambda: toggle(rem-1))
        toggle(total)

    # ================================================================ UI 循环
    def _update_ui_cycle(self):
        """主线程：消费 action 队列 + 刷新卡片 + 更新托盘图标（每 200ms）。"""
        try:
            while True:
                a = self._action_queue.get_nowait()
                if a[0] == "flash": self._flash_text(a[1], a[2])
        except queue.Empty: pass

        with self._price_lock: au, prev = self.au, self.prev_au

        # 卡片
        if au is not None:
            self.canvas.itemconfig(self.au_value_text, text=f"{au:.2f}")
            if prev is not None:
                arrow = "↑" if au > prev else ("↓" if au < prev else "•")
                clr   = self.up_color if au > prev else (self.down_color if au < prev else self.muted_color)
                self.canvas.itemconfig(self.au_arrow_text, text=arrow, fill=clr)

        # ★ 动态托盘图标：每 200ms 检查，金价变化时生成新图标替换
        if self.tray is not None and self.tray.visible:
            try:
                new_img = self._make_tray_icon(price=au, prev_price=prev)
                self.tray.icon = new_img
                self.tray.title = f"浙商金 {au:.2f}" if au is not None else "浙商金"
            except Exception:
                pass

        self.root.after(200, self._update_ui_cycle)

    # ================================================================ 历史记录
    def _write_history(self, au):
        if not getattr(self, "history_enabled", False): return
        try:
            new_file = not os.path.exists(_history_path())
            with open(_history_path(), "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file: w.writerow(["time","zs"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), "" if au is None else f"{au:.2f}"])
        except Exception: pass

    # ================================================================ 抓取循环
    def _data_fetch_loop(self):
        while True:
            try:
                new_au = self._fetch_au()
                if new_au is not None:
                    self._fetch_fail_count = 0
                    with self._price_lock: self.prev_au = self.au; self.au = new_au
                    self._check_alert(new_au, "AU")
                    self._track_au_extreme(new_au)
                else:
                    self._fetch_fail_count += 1
                self._write_history(new_au)
            except KeyboardInterrupt: raise
            except Exception as e:
                log.error(f"Data fetch loop error: {e}")
                self._fetch_fail_count += 1
            backoff = min(self.interval * (2 ** self._fetch_fail_count), 60) if self._fetch_fail_count > 0 else self.interval
            time.sleep(backoff + random.uniform(0.08, 0.7))

    def run(self):
        try: self.root.mainloop()
        except KeyboardInterrupt: pass
        finally:
            try: self.root.destroy()
            except Exception: pass


if __name__ == "__main__":
    try:
        app = GoldTaskbarDoubleLine()
        app.run()
    except Exception:
        import traceback
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception: pass
        raise