import socket
import time
import argparse
import struct
import zlib

# ---------------------------------------------------------------------------
# Packet framing (shared by TCP and UDP)
# ---------------------------------------------------------------------------
# Each message is framed as:
#   [4B length][4B seq][4B CRC32][payload]
# where length = len(payload), CRC32 covers payload only.
#
# --integrity / -I flag enables framing on the sender and verification on the
# receiver.  Without it, raw bytes are sent (legacy / max-throughput mode).
# ---------------------------------------------------------------------------

HEADER_FMT = "!III"          # length, seq, crc32  (all unsigned 32-bit)
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 12 bytes


def encode_message(payload: bytes, seq: int) -> bytes:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = struct.pack(HEADER_FMT, len(payload), seq, crc)
    return header + payload


def decode_header(raw: bytes):
    """Return (length, seq, crc32) from the first HEADER_SIZE bytes."""
    return struct.unpack(HEADER_FMT, raw[:HEADER_SIZE])


def verify_message(payload: bytes, expected_crc: int) -> bool:
    return (zlib.crc32(payload) & 0xFFFFFFFF) == expected_crc


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_bitrate(bits_per_sec: float) -> str:
    """Return a human-readable bitrate string, auto-scaled to bps/Kbps/Mbps/Gbps."""
    if bits_per_sec >= 1e9:
        return f"{bits_per_sec / 1e9:.2f} Gbps"
    if bits_per_sec >= 1e6:
        return f"{bits_per_sec / 1e6:.2f} Mbps"
    if bits_per_sec >= 1e3:
        return f"{bits_per_sec / 1e3:.2f} Kbps"
    return f"{bits_per_sec:.0f} bps"


def fmt_bytes(byte_count: float) -> str:
    """Return a human-readable data volume string, auto-scaled to B/KB/MB/GB."""
    if byte_count >= 1e9:
        return f"{byte_count / 1e9:.2f} GB"
    if byte_count >= 1e6:
        return f"{byte_count / 1e6:.2f} MB"
    if byte_count >= 1e3:
        return f"{byte_count / 1e3:.2f} KB"
    return f"{byte_count:.0f} B"


def bitrate(byte_count: float, elapsed: float) -> float:
    """Return bits per second."""
    return (byte_count * 8) / elapsed if elapsed > 0 else 0.0


def parse_rate(value: str) -> float:
    """Parse a human-friendly bitrate string into bits per second.

    Accepts plain numbers or a number followed by a suffix (case-insensitive):
      k / kbps  → Kbps  (×1 000)
      m / mbps  → Mbps  (×1 000 000)
      g / gbps  → Gbps  (×1 000 000 000)

    Examples: '500', '2k', '100m', '1.5g', '100mbps'
    Raises argparse.ArgumentTypeError on bad input.
    """
    s = value.strip().lower()
    suffixes = {
        "gbps": 1e9, "g": 1e9,
        "mbps": 1e6, "m": 1e6,
        "kbps": 1e3, "k": 1e3,
    }
    for suffix, multiplier in suffixes.items():
        if s.endswith(suffix):
            numeric = s[: -len(suffix)]
            break
    else:
        numeric = s
        multiplier = 1.0

    try:
        return float(numeric) * multiplier
    except ValueError:
        import argparse as _ap
        raise _ap.ArgumentTypeError(
            f"Invalid rate '{value}'. "
            "Use a number with an optional suffix: k/m/g or kbps/mbps/gbps "
            "(e.g. 2k, 100m, 1.5g, 500mbps)"
        )


# ---------------------------------------------------------------------------
# TCP
# ---------------------------------------------------------------------------

def _tcp_recv_session(conn, bufsize: int, interval: int, integrity: bool):
    """Drain one TCP connection and print a session summary when it closes."""
    total_bytes = 0
    corrupt = 0
    start_time = time.time()
    last_time = start_time
    last_bytes = 0

    with conn:
        if integrity:
            recv_buf = b""
            while True:
                while len(recv_buf) < HEADER_SIZE:
                    chunk = conn.recv(bufsize)
                    if not chunk:
                        break
                    recv_buf += chunk
                if len(recv_buf) < HEADER_SIZE:
                    break

                length, seq, crc = decode_header(recv_buf)

                needed = HEADER_SIZE + length
                while len(recv_buf) < needed:
                    chunk = conn.recv(bufsize)
                    if not chunk:
                        break
                    recv_buf += chunk
                if len(recv_buf) < needed:
                    break

                payload = recv_buf[HEADER_SIZE:needed]
                recv_buf = recv_buf[needed:]

                if not verify_message(payload, crc):
                    corrupt += 1
                    print(f"[TCP] WARNING: corrupt message seq={seq}")

                total_bytes += length

                now = time.time()
                if now - last_time >= interval:
                    interval_bytes = total_bytes - last_bytes
                    elapsed_interval = now - last_time
                    print(f"[TCP][{now - start_time:.1f}s] "
                          f"{fmt_bitrate(bitrate(interval_bytes, elapsed_interval))}")
                    last_time = now
                    last_bytes = total_bytes
        else:
            while True:
                data = conn.recv(bufsize)
                if not data:
                    break
                total_bytes += len(data)

                now = time.time()
                if now - last_time >= interval:
                    interval_bytes = total_bytes - last_bytes
                    elapsed_interval = now - last_time
                    print(f"[TCP][{now - start_time:.1f}s] "
                          f"{fmt_bitrate(bitrate(interval_bytes, elapsed_interval))}")
                    last_time = now
                    last_bytes = total_bytes

    elapsed = time.time() - start_time
    print(f"[TCP] Session: {fmt_bytes(total_bytes)} in {elapsed:.2f}s "
          f"({fmt_bitrate(bitrate(total_bytes, elapsed))})"
          + (f" | corrupt={corrupt}" if integrity else ""))


def tcp_server(host: str, port: int, bufsize: int, interval: int,
               as_client: bool, integrity: bool):
    if as_client:
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.connect((host, port))
        print(f"[TCP] Connected to upstream server at {host}:{port}")
        _tcp_recv_session(conn, bufsize, interval, integrity)
        return

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(1)
    print(f"[TCP] Listening on {host}:{port}  (Ctrl-C to quit)")

    try:
        while True:
            conn, addr = s.accept()
            print(f"[TCP] Connection from {addr}")
            try:
                _tcp_recv_session(conn, bufsize, interval, integrity)
            except Exception as exc:
                print(f"[TCP] Session error: {exc}")
            print(f"[TCP] Waiting for next connection...")
    except KeyboardInterrupt:
        print("\n[TCP] Server stopped.")
    finally:
        s.close()


def tcp_client(host: str, port: int, pktsize: int, bufsize: int,
               duration: int, integrity: bool):
    payload = b"x" * pktsize
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        print(f"[TCP] Connected to {host}:{port}"
              + f" | pktsize={fmt_bytes(pktsize)}"
              + (" (integrity on)" if integrity else ""))
        start_time = time.time()
        sent_bytes = 0
        seq = 0
        while time.time() - start_time < duration:
            if integrity:
                s.sendall(encode_message(payload, seq))
                seq += 1
            else:
                s.sendall(payload)
            sent_bytes += len(payload)

    elapsed = time.time() - start_time
    print(f"[TCP] Sent {fmt_bytes(sent_bytes)} in {elapsed:.2f}s "
          f"({fmt_bitrate(bitrate(sent_bytes, elapsed))})")


# ---------------------------------------------------------------------------
# UDP
# ---------------------------------------------------------------------------

def udp_server(host: str, port: int, bufsize: int, interval: int,
               integrity: bool):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Request a large kernel receive buffer to absorb bursts.
    target_rcvbuf = 64 * 1024 * 1024
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, target_rcvbuf)
    actual_rcvbuf = s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    if actual_rcvbuf < target_rcvbuf:
        print(f"[UDP] WARNING: SO_RCVBUF is {fmt_bytes(actual_rcvbuf)} "
              f"(wanted {fmt_bytes(target_rcvbuf)}). "
              f"Raise net.core.rmem_max to reduce kernel-level drops.")

    s.bind((host, port))
    s.settimeout(5.0)
    print(f"[UDP] Listening on {host}:{port}"
          + (" (integrity on)" if integrity else "")
          + "  (Ctrl-C to quit)")

    def reset_session():
        return dict(total_bytes=0, corrupt=0, expected_seq=0,
                    out_of_order=0, lost=0, pkt_count=0,
                    start_time=None, last_time=None, last_bytes=0)

    def print_summary(ss):
        if ss["start_time"] is None:
            return
        elapsed = time.time() - ss["start_time"]
        print(f"[UDP] Session: {fmt_bytes(ss['total_bytes'])} in {elapsed:.2f}s "
              f"({fmt_bitrate(bitrate(ss['total_bytes'], elapsed))}) "
              f"| pkts={ss['pkt_count']}"
              + (f" lost={ss['lost']} ooo={ss['out_of_order']} corrupt={ss['corrupt']}"
                 if integrity else ""))

    ss = reset_session()

    try:
        while True:
            try:
                data, addr = s.recvfrom(bufsize + HEADER_SIZE + 64)
            except socket.timeout:
                if ss["start_time"] is not None:
                    print_summary(ss)
                    ss = reset_session()
                    print(f"[UDP] Idle — waiting for next session...")
                continue

            now = time.time()

            if ss["start_time"] is None:
                ss["start_time"] = now
                ss["last_time"] = now
                print(f"[UDP] First packet from {addr}")

            ss["pkt_count"] += 1

            if integrity:
                if len(data) < HEADER_SIZE:
                    ss["corrupt"] += 1
                    continue

                length, seq, crc = decode_header(data)
                payload = data[HEADER_SIZE:HEADER_SIZE + length]

                if len(payload) != length:
                    ss["corrupt"] += 1
                    continue

                if not verify_message(payload, crc):
                    ss["corrupt"] += 1
                    print(f"[UDP] WARNING: corrupt packet seq={seq}")
                    continue

                if seq < ss["expected_seq"]:
                    ss["out_of_order"] += 1
                elif seq > ss["expected_seq"]:
                    ss["lost"] += seq - ss["expected_seq"]
                    ss["expected_seq"] = seq + 1
                else:
                    ss["expected_seq"] += 1

                ss["total_bytes"] += length
            else:
                ss["total_bytes"] += len(data)

            if now - ss["last_time"] >= interval:
                interval_bytes = ss["total_bytes"] - ss["last_bytes"]
                elapsed_interval = now - ss["last_time"]
                print(f"[UDP][{now - ss['start_time']:.1f}s] "
                      f"{fmt_bitrate(bitrate(interval_bytes, elapsed_interval))}"
                      + (f" | lost={ss['lost']} ooo={ss['out_of_order']} corrupt={ss['corrupt']}"
                         if integrity else ""))
                ss["last_time"] = now
                ss["last_bytes"] = ss["total_bytes"]

    except KeyboardInterrupt:
        print("\n[UDP] Server stopped.")
        print_summary(ss)
    finally:
        s.close()


def udp_client(host: str, port: int, pktsize: int, bufsize: int,
               duration: int, integrity: bool, rate_bps: float = 0,
               bind_port: int = 0):
    """Send UDP datagrams.

    rate_bps: target send rate in bits per second (0 = unlimited). Pacing is
    done by tracking when the next datagram is due; no sleep if already behind.
    bind_port: source port to bind to (0 = let the OS pick).
    """
    payload = b"x" * pktsize

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", bind_port))
        actual_src_port = s.getsockname()[1]
        print(f"[UDP] Sending to {host}:{port}"
              + f" (src port {actual_src_port})"
              + f" | pktsize={fmt_bytes(pktsize)}"
              + (" (integrity on)" if integrity else "")
              + (f" @ {fmt_bitrate(rate_bps)}" if rate_bps > 0 else " (unlimited)"))
        start_time = time.time()
        sent_bytes = 0
        seq = 0
        next_send_time = start_time

        while time.time() - start_time < duration:
            if rate_bps > 0:
                now = time.time()
                if now < next_send_time:
                    time.sleep(next_send_time - now)
                dgram_size = pktsize + (HEADER_SIZE if integrity else 0)
                next_send_time += dgram_size / (rate_bps / 8)

            if integrity:
                dgram = encode_message(payload, seq)
                s.sendto(dgram, (host, port))
            else:
                s.sendto(payload, (host, port))
            seq += 1
            sent_bytes += pktsize

    elapsed = time.time() - start_time
    print(f"[UDP] Sent {fmt_bytes(sent_bytes)} in {elapsed:.2f}s "
          f"({fmt_bitrate(bitrate(sent_bytes, elapsed))}) | pkts={seq}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_common_server_args(p):
    p.add_argument("--host", default="0.0.0.0",
                   help="Bind address (default: 0.0.0.0)")
    p.add_argument("-p", "--port", type=int, default=5001,
                   help="Port (default: 5001)")
    p.add_argument("-b", "--bufsize", type=int, default=256 * 1024,
                   help="Socket read buffer size in bytes (default: 262144)")
    p.add_argument("-i", "--interval", type=int, default=1,
                   help="Reporting interval in seconds (default: 1)")
    p.add_argument("-I", "--integrity", action="store_true",
                   help="Enable framing: length prefix + CRC32 per message")


def add_common_client_args(p):
    p.add_argument("host", help="Server hostname or IP")
    p.add_argument("-p", "--port", type=int, default=5001,
                   help="Port (default: 5001)")
    p.add_argument("-s", "--pktsize", type=int, default=1400,
                   help="Payload size per packet/send in bytes (default: 1400)")
    p.add_argument("-b", "--bufsize", type=int, default=256 * 1024,
                   help="Socket send buffer size in bytes (default: 262144)")
    p.add_argument("-t", "--time", type=int, default=10,
                   help="Test duration in seconds (default: 10)")
    p.add_argument("-I", "--integrity", action="store_true",
                   help="Enable framing: length prefix + CRC32 per message")


def main():
    parser = argparse.ArgumentParser(
        description="Mini iperf-like tool — TCP & UDP throughput tester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # TCP server
  %(prog)s tcp server

  # TCP client, 64 KB chunks, 10s
  %(prog)s tcp client 192.168.1.5 -s 65536

  # UDP server with integrity checking
  %(prog)s udp server -I

  # UDP client, 1400-byte datagrams, rate-limited to 100 Mbps
  %(prog)s udp client 192.168.1.5 -I -s 1400 -r 100m
""")

    sub = parser.add_subparsers(dest="proto", required=True,
                                metavar="PROTOCOL")

    # ---- TCP ----
    tcp_p = sub.add_parser("tcp", help="TCP mode")
    tcp_sub = tcp_p.add_subparsers(dest="role", required=True,
                                   metavar="ROLE")

    tcp_srv = tcp_sub.add_parser("server", help="TCP server (receiver)")
    add_common_server_args(tcp_srv)
    tcp_srv.add_argument("-a", "--as-client", action="store_true",
                         help="Connect outward to a remote TCP server "
                              "instead of listening")

    tcp_cli = tcp_sub.add_parser("client", help="TCP client (sender)")
    add_common_client_args(tcp_cli)

    # ---- UDP ----
    udp_p = sub.add_parser("udp", help="UDP mode")
    udp_sub = udp_p.add_subparsers(dest="role", required=True,
                                   metavar="ROLE")

    udp_srv = udp_sub.add_parser("server", help="UDP server (receiver)")
    add_common_server_args(udp_srv)

    udp_cli = udp_sub.add_parser("client", help="UDP client (sender)")
    add_common_client_args(udp_cli)
    udp_cli.add_argument("-r", "--rate", type=parse_rate, default=0,
                         metavar="RATE",
                         help="Target send rate (default: unlimited). "
                              "Accepts a number with an optional suffix: "
                              "k/kbps, m/mbps, g/gbps — e.g. 2k, 100m, 1.5g. "
                              "Use this to avoid flooding the receiver's "
                              "kernel buffer and causing false packet loss.")
    udp_cli.add_argument("-B", "--bind-port", type=int, default=0,
                         metavar="PORT",
                         help="Source port to bind before sending "
                              "(default: 0, OS picks an ephemeral port)")

    args = parser.parse_args()

    if args.proto == "tcp":
        if args.role == "server":
            tcp_server(args.host, args.port, args.bufsize, args.interval,
                       args.as_client, args.integrity)
        else:
            tcp_client(args.host, args.port, args.pktsize, args.bufsize,
                       args.time, args.integrity)
    else:  # udp
        if args.role == "server":
            udp_server(args.host, args.port, args.bufsize, args.interval,
                       args.integrity)
        else:
            udp_client(args.host, args.port, args.pktsize, args.bufsize,
                       args.time, args.integrity, args.rate, args.bind_port)


if __name__ == "__main__":
    main()