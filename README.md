# Multithreaded SYN Scanner with Scapy

This project provides a Python script using the Scapy library to perform a multithreaded SYN scan across a specified subnet or IP address.

## What is a SYN Scan?

A SYN scan, also known as a "half-open" scan, is a technique used in network security to identify open ports on a target host. It involves the following steps:

1. **SYN Packet**: The scanner sends a TCP packet with the SYN (Synchronize) flag set to a target port.
2. **Analysis**:
   - **OPEN**: If the target responds with a **SYN-ACK** (Synchronize-Acknowledge) packet, the port is open. The scanner then sends an **RST** (Reset) packet to close the connection before it's fully established.
   - **CLOSED**: If the target responds with an **RST** packet, the port is closed.
   - **FILTERED**: If there is no response (timeout), the port is likely filtered by a firewall or dropped by the host.

## Multithreading and Performance

The script uses `concurrent.futures.ThreadPoolExecutor` to perform scans concurrently. This significantly increases scanning speed over a large subnet by allowing multiple host checks to happen in parallel.

## Packet Construction with Scapy

The script uses Scapy's domain-specific language for packet construction:

```python
IP(dst=target_ip) / TCP(dport=target_port, flags="S")
```

- **`IP(dst=target_ip)`**: Creates an IP header with the destination set to the target.
- **`TCP(dport=target_port, flags="S")`**: Creates a TCP header targeting a specific port with the **SYN** flag (`"S"`) set.

## Requirements

- Python 3
- Scapy (`pip install -r requirements.txt`)
- Root/Sudo privileges (Required for sending raw packets on Linux/Unix systems)

## Execution Instructions

1. **Install Scapy**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Script**:
   To scan a specific host:
   ```bash
   sudo python3 syn_scan.py 192.168.1.10 -p 80
   ```

   To scan a subnet with 20 threads and a 1-second timeout:
   ```bash
   sudo python3 syn_scan.py 192.168.1.0/24 -p 443 -T 20 -t 1.0
   ```

### Arguments:
- `target`: Target IP or subnet (e.g., `192.168.1.1` or `192.168.1.0/24`).
- `-p`, `--port`: Target port (default: `80`).
- `-t`, `--timeout`: Timeout in seconds for each packet (default: `2.0`).
- `-T`, `--threads`: Number of concurrent threads (default: `10`).

## Legal and Ethical Warning

**Important**: Unauthorized network scanning can be considered a malicious activity. Ensure you have explicit permission from the owner of the network and systems you are scanning. Use this script only for educational purposes or on networks you own and have authorization to test.
