# netperf.py

A lightweight, dependency-free network throughput tester in the spirit of `iperf`, supporting both TCP and UDP with optional message integrity verification (length prefix + CRC32).

## Requirements

Python 3.7+. No third-party packages needed.

## Usage

```
python netperf.py <tcp|udp> <server|client> [options]
```

Both servers run indefinitely until stopped with Ctrl-C. The TCP server accepts one connection at a time and loops back to waiting when it closes. The UDP server prints a session report after 5 seconds of silence and then waits for the next session.

---

## TCP

### Server

```
python netperf.py tcp server [--host HOST] [-p PORT] [-b BUFSIZE] [-i INTERVAL] [-I] [-a]
```

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `-p`, `--port` | `5001` | Port to listen on |
| `-b`, `--bufsize` | `262144` | Socket read buffer size in bytes |
| `-i`, `--interval` | `1` | Throughput reporting interval in seconds |
| `-I`, `--integrity` | off | Enable length prefix + CRC32 framing |
| `-a`, `--as-client` | off | Connect outward to a remote TCP server instead of listening |

### Client

```
python netperf.py tcp client <HOST> [-p PORT] [-s PKTSIZE] [-b BUFSIZE] [-t TIME] [-I]
```

| Flag | Default | Description |
|---|---|---|
| `HOST` | *(required)* | Server hostname or IP |
| `-p`, `--port` | `5001` | Port to connect to |
| `-s`, `--pktsize` | `1400` | Payload size per `send` call in bytes |
| `-b`, `--bufsize` | `262144` | Socket send buffer size in bytes |
| `-t`, `--time` | `10` | Test duration in seconds |
| `-I`, `--integrity` | off | Enable length prefix + CRC32 framing |

### TCP examples

```bash
# Basic throughput test
python netperf.py tcp server
python netperf.py tcp client 192.168.1.5

# 64 KB chunks, integrity checking, 30-second run, report every 2s
python netperf.py tcp server -I -i 2
python netperf.py tcp client 192.168.1.5 -I -s 65536 -t 30

# Server connects outward (useful when the sender is behind NAT)
python netperf.py tcp client 192.168.1.5            # sender side — connects normally
python netperf.py tcp server --host 192.168.1.5 -a  # receiver dials out instead
```

---

## UDP

### Server

```
python netperf.py udp server [--host HOST] [-p PORT] [-b BUFSIZE] [-i INTERVAL] [-I]
```

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `-p`, `--port` | `5001` | Port to listen on |
| `-b`, `--bufsize` | `262144` | Socket read buffer size in bytes |
| `-i`, `--interval` | `1` | Throughput reporting interval in seconds |
| `-I`, `--integrity` | off | Enable length prefix + CRC32 + sequence tracking |

The server requests a 64 MB socket receive buffer (`SO_RCVBUF`) on startup. If the OS caps it below that, a warning is printed with the actual value and the sysctl needed to raise it.

### Client

```
python netperf.py udp client <HOST> [-p PORT] [-s PKTSIZE] [-b BUFSIZE] [-t TIME] [-I] [-r RATE] [-B PORT]
```

| Flag | Default | Description |
|---|---|---|
| `HOST` | *(required)* | Server hostname or IP |
| `-p`, `--port` | `5001` | Port to send to |
| `-s`, `--pktsize` | `1400` | Datagram payload size in bytes |
| `-b`, `--bufsize` | `262144` | Socket send buffer size in bytes |
| `-t`, `--time` | `10` | Test duration in seconds |
| `-I`, `--integrity` | off | Enable length prefix + CRC32 framing |
| `-r`, `--rate` | unlimited | Target send rate — see rate syntax below |
| `-B`, `--bind-port` | `0` (OS picks) | Source port to bind before sending |

### Rate syntax (`-r`)

The rate argument accepts a plain number (bits per second) or a number with a suffix:

| Suffix | Meaning | Example |
|---|---|---|
| `k` or `kbps` | Kilobits per second | `500k`, `500kbps` |
| `m` or `mbps` | Megabits per second | `100m`, `100mbps` |
| `g` or `gbps` | Gigabits per second | `1g`, `1.5gbps` |

Suffixes are case-insensitive. If no suffix is given, the value is treated as raw bps.

### UDP examples

```bash
# Basic UDP throughput test
python netperf.py udp server
python netperf.py udp client 192.168.1.5

# With integrity checking and 1400-byte datagrams (close to Ethernet MTU)
python netperf.py udp server -I
python netperf.py udp client 192.168.1.5 -I -s 1400

# Rate-limited to 100 Mbps
python netperf.py udp client 192.168.1.5 -I -s 1400 -r 100m

# Rate-limited to 500 Mbps
python netperf.py udp client 192.168.1.5 -I -s 1400 -r 500m

# Rate-limited to 1 Gbps
python netperf.py udp client 192.168.1.5 -I -s 1400 -r 1g

# Send from a fixed source port (useful for firewall rules or NAT pinholing)
python netperf.py udp client 192.168.1.5 -I -s 1400 -B 6000
```

---

## Integrity mode (`-I`)

When enabled on both sender and receiver, every message is wrapped in a 12-byte header:

```
[ 4B length ][ 4B sequence number ][ 4B CRC32 ] [ payload ]
```

- **Length** — payload byte count, used to re-assemble framed TCP messages and validate UDP datagram completeness.
- **Sequence number** — monotonically increasing counter per session. The UDP server uses this to detect lost and out-of-order packets.
- **CRC32** — computed over the payload only. A mismatch is reported as a corrupt packet and the message is discarded.

> **Both sides must use `-I` together.** A framed sender talking to an unframed receiver (or vice versa) will produce garbage results.

---

## Understanding UDP packet loss

On loopback or fast networks, the sender can push data faster than the OS can deliver it to the application. When the kernel socket receive buffer fills up, datagrams are silently dropped *before your code sees them*, but the sender's sequence numbers keep incrementing — so the server counts them as lost.

Two mitigations are built in:

**1. Larger receive buffer** — the server requests 64 MB via `SO_RCVBUF`. On Linux the OS caps this at `net.core.rmem_max`. To raise it:

```bash
sudo sysctl -w net.core.rmem_max=67108864
```

**2. Send-rate limiting (`-r`)** — cap the client to a rate the receiver can drain. Start conservative and increase until you see loss appear:

```bash
# Start at 100 Mbps
python netperf.py udp client 192.168.1.5 -I -r 100m

# Step up to 500 Mbps
python netperf.py udp client 192.168.1.5 -I -r 500m
```

If you see `lost > 0` at a given rate, that's your ceiling for that path.

---

## Output reference

Bitrates are auto-scaled to the most readable unit (bps / Kbps / Mbps / Gbps). Data volumes are auto-scaled to B / KB / MB / GB.

### Interval line (both protocols)
```
[UDP][2.0s] 487.23 Mbps | lost=0 ooo=0 corrupt=0
[TCP][2.0s] 9.31 Gbps
```
- `[2.0s]` — seconds since session start
- bitrate — throughput over the last interval (payload bytes only, excluding framing overhead)
- `lost` — datagrams with a sequence gap (UDP + `-I` only)
- `ooo` — datagrams that arrived with a sequence number lower than expected
- `corrupt` — datagrams that failed the CRC32 check or had a malformed header

### Session summary line
```
[UDP] Session: 1.10 GB in 10.00s (879.84 Mbps) | pkts=785909 lost=0 ooo=0 corrupt=0
[TCP] Session: 842.91 MB in 10.01s (673.54 Mbps) | corrupt=0
```
Printed when a TCP connection closes or after 5 seconds of UDP silence.
