import socket
import time
import argparse

def run_server(host: str, port: int, bufsize: int, interval: int):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen(1)
        print(f"Server listening on {host}:{port}")

        conn, addr = s.accept()
        print(f"Connection from {addr}")

        total_bytes = 0
        start_time = time.time()
        last_time = start_time
        last_bytes = 0

        with conn:
            while True:
                data = conn.recv(bufsize)
                if not data:
                    break
                total_bytes += len(data)

                # Check if interval has passed
                now = time.time()
                if now - last_time >= interval:
                    interval_bytes = total_bytes - last_bytes
                    mbps = (interval_bytes * 8) / ((now - last_time) * 1e6)
                    print(f"[{now - start_time:.1f}s] {mbps:.2f} Mbps")
                    last_time = now
                    last_bytes = total_bytes

        elapsed = time.time() - start_time
        mbps = (total_bytes * 8) / (elapsed * 1e6)
        print(f"Total: Received {total_bytes/1e6:.2f} MB in {elapsed:.2f} sec ({mbps:.2f} Mbps)")


def run_client(server: str, port: int, bufsize: int, duration: int):
    data = b"x" * bufsize
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((server, port))
        print(f"Connected to {server}:{port}")

        start_time = time.time()
        sent_bytes = 0

        while time.time() - start_time < duration:
            s.sendall(data)
            sent_bytes += len(data)

        elapsed = time.time() - start_time
        mbps = (sent_bytes * 8) / (elapsed * 1e6)
        print(f"Sent {sent_bytes/1e6:.2f} MB in {elapsed:.2f} sec ({mbps:.2f} Mbps)")


def main():
    parser = argparse.ArgumentParser(description="Mini iperf-like TCP test tool")
    parser.add_argument("-s", "--server", action="store_true", help="Run in server mode")
    parser.add_argument("-c", "--client", metavar="HOST", help="Run in client mode, connect to HOST")
    parser.add_argument("-p", "--port", type=int, default=5001, help="Port number (default: 5001)")
    parser.add_argument("-t", "--time", type=int, default=10, help="Duration of test in seconds (client only)")
    parser.add_argument("-b", "--bufsize", type=int, default=64*1024, help="Buffer size in bytes (default: 65536)")
    parser.add_argument("-i", "--interval", type=int, default=1, help="Interval in seconds for server updates (default: 1)")

    args = parser.parse_args()

    if args.server:
        run_server("0.0.0.0", args.port, args.bufsize, args.interval)
    elif args.client:
        run_client(args.client, args.port, args.bufsize, args.time)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
