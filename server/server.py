"""
mây remote SERVER (Windows)  -  v2: video + audio + input + text

Giao thức (TCP): mỗi message = [1 byte type][4 byte length big-endian][payload]
  type 0 = hello (server->client, JSON: w,h,fps,codec,audio:{rate,ch} | null)
  type 1 = video (server->client, 1 access unit H.264 Annex-B)
  type 2 = input (client->server, JSON)
  type 3 = auth  (client->server, JSON: {"token": "..."})
  type 4 = audio (server->client, PCM s16le stereo, ~10ms/chunk)

Input JSON:
  {"t":"mm","x":0..1,"y":0..1}     chuột tuyệt đối (chuẩn hoá)
  {"t":"mr","dx":int,"dy":int}     chuột tương đối (cho game FPS)
  {"t":"mb","b":"l|r|m","d":0|1}   nút chuột
  {"t":"wh","d":120}               cuộn
  {"t":"k","vk":int,"d":0|1}       phím (Windows virtual-key code)
  {"t":"text","s":"xin chào"}      gõ chuỗi Unicode (tiếng Việt OK)
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import hmac
import json
import socket
import struct
import threading
from fractions import Fraction

import av
import dxcam
import numpy as np

MSG_HELLO, MSG_VIDEO, MSG_INPUT, MSG_AUTH, MSG_AUDIO = 0, 1, 2, 3, 4

LOG = print                 # GUI có thể gán lại: server.LOG = queue.put
STOP = threading.Event()    # GUI gọi server.STOP.set() để dừng


def log(msg):
    try:
        LOG(msg)
    except Exception:
        pass


# ---------------------------------------------------------------- network
def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


def recv_msg(sock):
    t, ln = struct.unpack(">BI", recv_exact(sock, 5))
    return t, recv_exact(sock, ln)


class Link:
    """Gửi an toàn từ nhiều thread (video + audio)."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def send(self, t, payload):
        with self.lock:
            self.sock.sendall(struct.pack(">BI", t, len(payload)) + payload)


# ---------------------------------------------------------------- input injection
user32 = ctypes.windll.user32


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


EXTENDED_VK = {0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0xA3, 0xA5, 0x5B, 0x5C}
BTN = {"l": (0x0002, 0x0004), "r": (0x0008, 0x0010), "m": (0x0020, 0x0040)}


class Injector:
    def __init__(self):
        self.keys = set()
        self.buttons = set()

    @staticmethod
    def _send(inp):
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _mouse(self, flags, dx=0, dy=0, data=0):
        i = INPUT(type=0)
        i.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        self._send(i)

    def _key(self, vk, down):
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = 0x0008  # KEYEVENTF_SCANCODE (game đọc scancode)
        if not down:
            flags |= 0x0002
        if vk in EXTENDED_VK:
            flags |= 0x0001
        i = INPUT(type=1)
        i.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        self._send(i)

    def _unicode(self, code, down):
        i = INPUT(type=1)
        i.u.ki = KEYBDINPUT(0, code, 0x0004 | (0 if down else 0x0002), 0, 0)  # KEYEVENTF_UNICODE
        self._send(i)

    def type_text(self, s):
        b = s[:500].encode("utf-16-le")  # surrogate pair cho emoji cũng chạy
        for k in range(0, len(b), 2):
            code = b[k] | (b[k + 1] << 8)
            self._unicode(code, True)
            self._unicode(code, False)

    def handle(self, m):
        t = m.get("t")
        if t == "mm":
            x = min(max(float(m["x"]), 0.0), 1.0)
            y = min(max(float(m["y"]), 0.0), 1.0)
            self._mouse(0x8001, int(x * 65535), int(y * 65535))  # MOVE | ABSOLUTE
        elif t == "mr":
            self._mouse(0x0001, int(m["dx"]), int(m["dy"]))
        elif t == "mb":
            b = m["b"]
            if b in BTN:
                down = bool(m["d"])
                self._mouse(BTN[b][0] if down else BTN[b][1])
                (self.buttons.add if down else self.buttons.discard)(b)
        elif t == "wh":
            self._mouse(0x0800, data=int(m["d"]))
        elif t == "k":
            vk, down = int(m["vk"]), bool(m["d"])
            self._key(vk, down)
            (self.keys.add if down else self.keys.discard)(vk)
        elif t == "text":
            self.type_text(str(m.get("s", "")))

    def release_all(self):
        for vk in list(self.keys):
            self._key(vk, False)
        for b in list(self.buttons):
            self._mouse(BTN[b][1])
        self.keys.clear()
        self.buttons.clear()


# ---------------------------------------------------------------- audio (WASAPI loopback)
class AudioCapture:
    """Bắt âm thanh hệ thống (thứ đang phát ra loa) -> PCM s16 stereo."""

    def __init__(self, link):
        import pyaudiowpatch as pa
        self.pa_mod = pa
        self.link = link
        self.pa = pa.PyAudio()
        wasapi = self.pa.get_host_api_info_by_type(pa.paWASAPI)
        dev = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if not dev.get("isLoopbackDevice"):
            for lb in self.pa.get_loopback_device_info_generator():
                if dev["name"] in lb["name"]:
                    dev = lb
                    break
        self.dev = dev
        self.rate = int(dev["defaultSampleRate"])
        self.ch = int(dev["maxInputChannels"])
        self.stream = None

    def info(self):
        return {"rate": self.rate, "ch": 2}

    def start(self):
        pa = self.pa_mod

        def cb(in_data, frame_count, time_info, status):
            try:
                a = np.frombuffer(in_data, dtype=np.int16).reshape(-1, self.ch)
                if self.ch == 1:
                    a = np.repeat(a, 2, axis=1)
                elif self.ch > 2:
                    a = a[:, :2]
                self.link.send(MSG_AUDIO, np.ascontiguousarray(a).tobytes())
            except Exception:
                pass
            return (None, pa.paContinue)

        self.stream = self.pa.open(
            format=pa.paInt16, channels=self.ch, rate=self.rate,
            frames_per_buffer=self.rate // 100, input=True,
            input_device_index=self.dev["index"], stream_callback=cb)
        log(f"[server] audio: {self.dev['name']} {self.rate}Hz {self.ch}ch")

    def stop(self):
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            self.pa.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------- encoder
def encoder_options(name, fps):
    if name == "h264_nvenc":
        return {"preset": "p1", "tune": "ll", "zerolatency": "1", "rc": "cbr"}
    if name == "h264_amf":
        return {"usage": "ultralowlatency", "quality": "speed", "rc": "cbr"}
    if name == "h264_qsv":
        return {"preset": "veryfast"}
    return {"preset": "ultrafast", "tune": "zerolatency",
            "x264-params": f"repeat-headers=1:keyint={fps}:scenecut=0"}


def make_encoder(w, h, fps, bitrate, prefer):
    order = ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"] if prefer == "auto" else [prefer]
    for name in order:
        try:
            c = av.CodecContext.create(name, "w")
            c.width, c.height = w, h
            c.pix_fmt = "yuv420p"
            c.time_base = Fraction(1, fps)
            c.framerate = Fraction(fps, 1)
            c.bit_rate = bitrate
            c.gop_size = fps          # keyframe mỗi 1 giây -> client vào giữa chừng vẫn giải mã được
            c.max_b_frames = 0
            c.options = encoder_options(name, fps)
            c.open()
            log(f"[server] encoder: {name}")
            return name, c
        except Exception as e:  # noqa
            log(f"[server] {name} không dùng được: {e}")
    raise RuntimeError("Không có encoder H.264 nào chạy được")


# ---------------------------------------------------------------- session
def reader(conn, inj, stop):
    try:
        while not stop.is_set():
            t, p = recv_msg(conn)
            if t == MSG_INPUT:
                inj.handle(json.loads(p))
    except Exception:
        pass
    finally:
        stop.set()


def stream(conn, args):
    link = Link(conn)
    cam = dxcam.create(output_idx=args.monitor, output_color="BGRA")
    src_w, src_h = cam.width, cam.height
    if args.height and args.height < src_h:
        out_h = args.height
        out_w = round(src_w * out_h / src_h)
    else:
        out_w, out_h = src_w, src_h
    out_w -= out_w % 2
    out_h -= out_h % 2

    name, enc = make_encoder(out_w, out_h, args.fps, int(args.bitrate * 1_000_000), args.encoder)

    audio = None
    if not args.no_audio:
        try:
            audio = AudioCapture(link)
        except Exception as e:
            log(f"[server] tắt audio (lỗi: {e})")

    link.send(MSG_HELLO, json.dumps({
        "w": out_w, "h": out_h, "fps": args.fps, "codec": name,
        "audio": audio.info() if audio else None}).encode())

    inj = Injector()
    stop = threading.Event()
    threading.Thread(target=reader, args=(conn, inj, stop), daemon=True).start()

    if audio:
        audio.start()
    cam.start(target_fps=args.fps, video_mode=True)
    n = 0
    try:
        while not stop.is_set() and not STOP.is_set():
            frame = cam.get_latest_frame()
            vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="bgra")
            vf = vf.reformat(width=out_w, height=out_h, format="yuv420p")
            vf.pts = n
            n += 1
            for pkt in enc.encode(vf):
                link.send(MSG_VIDEO, bytes(pkt))
    finally:
        stop.set()
        if audio:
            audio.stop()
        inj.release_all()
        try:
            cam.stop()
            cam.release()
        except Exception:
            pass


def handle_client(conn, addr, args):
    log(f"[server] kết nối từ {addr}")
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(10)
        t, p = recv_msg(conn)
        tok = str(json.loads(p).get("token", "")).encode()
        if t != MSG_AUTH or not hmac.compare_digest(tok, args.token.encode()):
            log("[server] sai token, đóng kết nối")
            return
        conn.settimeout(None)
        stream(conn, args)
    except Exception as e:
        log(f"[server] phiên kết thúc: {e}")
    finally:
        conn.close()
        log("[server] đã ngắt")


def run_server(args):
    """Chạy vòng lặp server (blocking). Gọi STOP.set() để dừng."""
    STOP.clear()
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(1)
    srv.settimeout(0.5)
    log(f"[server] lắng nghe {args.host}:{args.port}")
    try:
        while not STOP.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            handle_client(conn, addr, args)  # 1 client mỗi lần
    finally:
        srv.close()
        log("[server] đã dừng")


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5900)
    ap.add_argument("--token", required=True, help="mật khẩu chung với client")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=float, default=20, help="Mbps")
    ap.add_argument("--height", type=int, default=0, help="scale xuống chiều cao này (vd 720, 1080); 0 = giữ nguyên")
    ap.add_argument("--monitor", type=int, default=0)
    ap.add_argument("--encoder", default="auto",
                    choices=["auto", "h264_nvenc", "h264_amf", "h264_qsv", "libx264"])
    ap.add_argument("--no-audio", action="store_true", help="tắt âm thanh")
    return ap


def main():
    run_server(build_parser().parse_args())


if __name__ == "__main__":
    main()
