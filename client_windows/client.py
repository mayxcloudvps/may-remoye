"""
mây remote CLIENT (Windows)
- Nhận H.264 + âm thanh từ server, hiển thị bằng pygame, gửi chuột/phím về server
- F8 : bật/tắt "bắt chuột" (chuột tương đối, dùng cho game FPS)
- F11: bật/tắt toàn màn hình
Chạy trực tiếp:  python client.py --host IP --token MATKHAU
Hoặc dùng giao diện: python client_gui.py
"""
import argparse
import ctypes
import json
import queue
import socket
import struct
import threading

import av
import pygame

MSG_HELLO, MSG_VIDEO, MSG_INPUT, MSG_AUTH, MSG_AUDIO = 0, 1, 2, 3, 4


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


# pygame key -> Windows virtual-key
VK = {
    pygame.K_RETURN: 0x0D, pygame.K_ESCAPE: 0x1B, pygame.K_SPACE: 0x20, pygame.K_TAB: 0x09,
    pygame.K_BACKSPACE: 0x08, pygame.K_LSHIFT: 0xA0, pygame.K_RSHIFT: 0xA1,
    pygame.K_LCTRL: 0xA2, pygame.K_RCTRL: 0xA3, pygame.K_LALT: 0xA4, pygame.K_RALT: 0xA5,
    pygame.K_LEFT: 0x25, pygame.K_UP: 0x26, pygame.K_RIGHT: 0x27, pygame.K_DOWN: 0x28,
    pygame.K_DELETE: 0x2E, pygame.K_INSERT: 0x2D, pygame.K_HOME: 0x24, pygame.K_END: 0x23,
    pygame.K_PAGEUP: 0x21, pygame.K_PAGEDOWN: 0x22, pygame.K_CAPSLOCK: 0x14,
    pygame.K_MINUS: 0xBD, pygame.K_EQUALS: 0xBB, pygame.K_COMMA: 0xBC, pygame.K_PERIOD: 0xBE,
    pygame.K_SLASH: 0xBF, pygame.K_SEMICOLON: 0xBA, pygame.K_QUOTE: 0xDE,
    pygame.K_LEFTBRACKET: 0xDB, pygame.K_RIGHTBRACKET: 0xDD, pygame.K_BACKSLASH: 0xDC,
    pygame.K_BACKQUOTE: 0xC0, pygame.K_LSUPER: 0x5B, pygame.K_RSUPER: 0x5C,
}


def to_vk(k):
    if pygame.K_a <= k <= pygame.K_z:
        return 0x41 + k - pygame.K_a
    if pygame.K_0 <= k <= pygame.K_9:
        return 0x30 + k - pygame.K_0
    if pygame.K_F1 <= k <= pygame.K_F12:
        return 0x70 + k - pygame.K_F1
    return VK.get(k)


def run(host, port, token, fullscreen=False):
    """Kết nối và chạy đến khi đóng cửa sổ. Ném RuntimeError nếu không kết nối được."""
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        raise RuntimeError(f"Không kết nối được tới {host}:{port}\n{e}")
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    send_lock = threading.Lock()

    def send(t, payload):
        with send_lock:
            sock.sendall(struct.pack(">BI", t, len(payload)) + payload)

    def send_input(obj):
        try:
            send(MSG_INPUT, json.dumps(obj, separators=(",", ":")).encode())
        except OSError:
            alive.clear()

    try:
        send(MSG_AUTH, json.dumps({"token": token}).encode())
        t, p = recv_msg(sock)
    except (ConnectionError, OSError):
        sock.close()
        raise RuntimeError("Server từ chối kết nối (sai token?)")
    if t != MSG_HELLO:
        sock.close()
        raise RuntimeError("Server phản hồi không hợp lệ")
    hello = json.loads(p)
    sock.settimeout(None)

    alive = threading.Event()
    alive.set()

    # ---- audio (PCM s16 stereo từ server)
    audio_q = queue.Queue(maxsize=40)
    audio_stream = None
    if hello.get("audio"):
        try:
            import sounddevice as sd
            audio_stream = sd.RawOutputStream(
                samplerate=hello["audio"]["rate"], channels=2, dtype="int16", latency="low")
            audio_stream.start()

            def audio_loop():
                while alive.is_set():
                    try:
                        d = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    try:
                        audio_stream.write(d)
                    except Exception:
                        pass

            threading.Thread(target=audio_loop, daemon=True).start()
        except Exception as e:
            print("Không phát được âm thanh:", e)
            audio_stream = None

    def push_audio(data):
        if audio_stream is None:
            return
        try:
            audio_q.put_nowait(data)
        except queue.Full:  # trễ quá -> bỏ gói cũ nhất
            try:
                audio_q.get_nowait()
                audio_q.put_nowait(data)
            except Exception:
                pass

    # ---- decode thread
    latest = {"frame": None, "id": 0}
    lock = threading.Lock()

    def rx_loop():
        dec = av.CodecContext.create("h264", "r")
        dec.options = {"flags": "low_delay"}
        dec.thread_type = "SLICE"
        try:
            while alive.is_set():
                t, payload = recv_msg(sock)
                if t == MSG_AUDIO:
                    push_audio(payload)
                    continue
                if t != MSG_VIDEO:
                    continue
                try:
                    frames = dec.decode(av.Packet(payload))
                except av.AVError:
                    continue
                for f in frames:
                    img = f.to_ndarray(format="rgb24")
                    with lock:
                        latest["frame"] = (f.width, f.height, img.tobytes())
                        latest["id"] += 1
        except Exception as e:
            print("Mất kết nối:", e)
        finally:
            alive.clear()

    threading.Thread(target=rx_loop, daemon=True).start()

    # ---- UI
    pygame.init()
    pygame.display.set_caption("mây remote  (F8 = bắt chuột, F11 = toàn màn hình)")
    if fullscreen:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    captured = False
    last_id = 0
    BTN = {1: "l", 2: "m", 3: "r"}

    try:
        while alive.is_set():
            sw, sh = screen.get_size()
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    alive.clear()
                elif e.type == pygame.VIDEORESIZE:
                    screen = pygame.display.get_surface()
                elif e.type == pygame.MOUSEMOTION:
                    if captured:
                        if e.rel != (0, 0):
                            send_input({"t": "mr", "dx": e.rel[0], "dy": e.rel[1]})
                    else:
                        send_input({"t": "mm", "x": round(e.pos[0] / sw, 5), "y": round(e.pos[1] / sh, 5)})
                elif e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                    b = BTN.get(e.button)
                    if b:
                        send_input({"t": "mb", "b": b, "d": 1 if e.type == pygame.MOUSEBUTTONDOWN else 0})
                elif e.type == pygame.MOUSEWHEEL:
                    send_input({"t": "wh", "d": int(e.y * 120)})
                elif e.type in (pygame.KEYDOWN, pygame.KEYUP):
                    if e.key == pygame.K_F8:
                        if e.type == pygame.KEYDOWN:
                            captured = not captured
                            pygame.event.set_grab(captured)
                            pygame.mouse.set_visible(not captured)
                            pygame.mouse.get_rel()
                        continue
                    if e.key == pygame.K_F11:
                        if e.type == pygame.KEYDOWN:
                            pygame.display.toggle_fullscreen()
                            screen = pygame.display.get_surface()
                        continue
                    vk = to_vk(e.key)
                    if vk:
                        send_input({"t": "k", "vk": vk, "d": 1 if e.type == pygame.KEYDOWN else 0})

            with lock:
                fr, fid = latest["frame"], latest["id"]
            if fr and fid != last_id:
                last_id = fid
                w, h, data = fr
                surf = pygame.image.frombuffer(data, (w, h), "RGB")
                pygame.transform.scale(surf, (sw, sh), screen)
                pygame.display.flip()
            clock.tick(240)
    finally:
        alive.clear()
        try:
            pygame.event.set_grab(False)
            pygame.mouse.set_visible(True)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
        if audio_stream is not None:
            try:
                audio_stream.stop()
                audio_stream.close()
            except Exception:
                pass
        pygame.quit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=5900)
    ap.add_argument("--token", required=True)
    ap.add_argument("--fullscreen", action="store_true")
    a = ap.parse_args()
    try:
        run(a.host, a.port, a.token, a.fullscreen)
    except RuntimeError as e:
        print(e)


if __name__ == "__main__":
    main()
  
