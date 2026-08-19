"""A minimal RFB 3.8 server: no auth, one raw-encoded frame of a coloured
screen with a diagonal stripe, and it logs every pointer/key event it gets.
Enough to prove a noVNC client connects, draws, and sends input through the
bridge. Not a VNC server anybody should use for anything else."""
import socket
import struct
import sys
import threading

W, H = 1280, 800
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5999


def frame_pixels():
    # 32bpp little-endian BGRX, depth 24: background violet-ish, stripe white.
    row = bytearray()
    out = bytearray()
    for y in range(H):
        row.clear()
        for x in range(W):
            if abs(x - y * W // H) < 40:
                row += b"\xff\xff\xff\x00"
            else:
                row += b"\xc5\x85\xaa\x00"  # B G R X -> #AA85C5
        out += row
    return bytes(out)


PIXELS = frame_pixels()


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def handle(conn):
    conn.sendall(b"RFB 003.008\n")
    client_version = recv_exact(conn, 12)
    print("client version:", client_version, flush=True)
    conn.sendall(b"\x01\x01")  # one security type: None
    sec = recv_exact(conn, 1)
    print("security chosen:", sec, flush=True)
    conn.sendall(b"\x00\x00\x00\x00")  # SecurityResult OK
    shared = recv_exact(conn, 1)
    print("client init, shared:", shared, flush=True)
    # ServerInit: width, height, pixel format (16 bytes), name
    pixfmt = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    name = b"fake-vnc"
    conn.sendall(struct.pack(">HH", W, H) + pixfmt + struct.pack(">I", len(name)) + name)
    bpp = 4
    while True:
        msg_type = recv_exact(conn, 1)[0]
        if msg_type == 0:  # SetPixelFormat
            recv_exact(conn, 3 + 16)
            print("SetPixelFormat (ignored; we keep 32bpp)", flush=True)
        elif msg_type == 2:  # SetEncodings
            (_, n) = struct.unpack(">BH", recv_exact(conn, 3))
            encs = struct.unpack(f">{n}i", recv_exact(conn, 4 * n))
            print("SetEncodings:", encs[:8], "...", flush=True)
        elif msg_type == 3:  # FramebufferUpdateRequest
            inc, x, y, w, h = struct.unpack(">BHHHH", recv_exact(conn, 9))
            if not inc:
                # Full raw rect.
                conn.sendall(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 0, 0, W, H, 0) + PIXELS)
                print("sent full frame", flush=True)
        elif msg_type == 4:  # KeyEvent
            down, _, key = struct.unpack(">BHI", recv_exact(conn, 7))
            print(f"KeyEvent down={down} keysym=0x{key:x}", flush=True)
        elif msg_type == 5:  # PointerEvent
            mask, x, y = struct.unpack(">BHH", recv_exact(conn, 5))
            print(f"PointerEvent mask={mask} x={x} y={y}", flush=True)
        elif msg_type == 6:  # ClientCutText
            (_, _, n) = struct.unpack(">BHI", recv_exact(conn, 7))
            recv_exact(conn, n)
        else:
            print("unknown message type", msg_type, flush=True)
            return


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT))
srv.listen(2)
print("fake vnc on", PORT, flush=True)
while True:
    conn, _ = srv.accept()

    def run(c=conn):
        try:
            handle(c)
        except ConnectionError:
            print("client went away", flush=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
