#!/usr/bin/env python3
"""
A multithreaded TCP SYN scanner module.
This script performs a basic half-open (SYN) scan across a target subnet or host.
"""

import argparse
import sys
import ipaddress
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Union

# Third-party imports
try:
    from scapy.all import IP, TCP, sr1, conf # type: ignore
except ImportError:
    print("[!] Scapy is not installed. Please install it using 'pip install -r requirements.txt'")
    sys.exit(1)

# Configure logging to provide observability as per GEMINI.md
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Disable Scapy's default verbose output to reduce noise
conf.verb = 0

def scan_host(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address], port: int, timeout: float) -> str:
    """
    Scans a single host for a specific port using a TCP SYN packet.

    Args:
        ip (Union[ipaddress.IPv4Address, ipaddress.IPv6Address]): The target IP address.
        port (int): The destination port to scan.
        timeout (float): The maximum time to wait for a response in seconds.

    Returns:
        str: A message indicating the scan result (OPEN, CLOSED, FILTERED, etc.).

    Notes:
        - 0x12 flags indicate a SYN-ACK (port open).
        - 0x14 flags indicate an RST-ACK (port closed).
        - No response (None) indicates the port is likely filtered by a firewall.
    """
    try:
        # Sanitize input: Ensure IP is a string for Scapy
        target_ip: str = str(ip)
        
        # Construct and send the SYN packet
        # Explanation: We use the IP and TCP layers. Flags="S" sets the SYN bit.
        packet = IP(dst=target_ip) / TCP(dport=port, flags="S")
        response = sr1(packet, timeout=timeout)
        
        if response is None:
            logger.debug(f"Target {target_ip}:{port} - No response (FILTERED)")
            return f"[-] {target_ip}:{port} -> FILTERED (No response)"
            
        if response.haslayer(TCP):
            tcp_layer = response.getlayer(TCP)
            flags = tcp_layer.flags
            
            # Check for SYN-ACK (0x12 / 18)
            if flags == 0x12:
                logger.info(f"Target {target_ip}:{port} - Port is OPEN")
                # Send RST to close the half-open connection (good citizenship)
                sr1(IP(dst=target_ip) / TCP(dport=port, flags="R"), timeout=timeout)
                return f"[+] {target_ip}:{port} -> OPEN (Received SYN-ACK)"
            
            # Check for RST-ACK (0x14 / 20)
            elif flags == 0x14:
                logger.debug(f"Target {target_ip}:{port} - Port is CLOSED")
                return f"[-] {target_ip}:{port} -> CLOSED (Received RST)"
        
        return f"[?] {target_ip}:{port} -> UNKNOWN (Unexpected response flags: {flags})"
    except PermissionError:
        logger.error("Insufficient privileges to send raw packets. Try running with sudo.")
        return "[!] Error: Permission Denied"
    except Exception as e:
        logger.exception(f"Unexpected error while scanning {ip}: {e}")
        return f"[!] Error scanning {ip}: {e}"

def validate_port(port_str: str) -> int:
    """
    Validates that the provided port string is a valid integer within the correct range.

    Args:
        port_str (str): The port number as a string.

    Returns:
        int: The validated port number.

    Raises:
        argparse.ArgumentTypeError: If the port is invalid or out of range.
    """
    try:
        port = int(port_str)
        if 1 <= port <= 65535:
            return port
        raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {port_str}. Must be between 1 and 65535.")

def main() -> None:
    """
    Main entry point for the SYN scanner. Handles CLI arguments and orchestrates the scan.
    """
    parser = argparse.ArgumentParser(description="Multithreaded Scapy-based SYN scanner.")
    parser.add_argument("target", help="Target IP or subnet (e.g., 192.168.1.1 or 192.168.1.0/24)")
    parser.add_argument("-p", "--port", type=validate_port, default=80, help="Target port (default: 80)")
    parser.add_argument("-t", "--timeout", type=float, default=2.0, help="Timeout in seconds for each packet (default: 2.0)")
    parser.add_argument("-T", "--threads", type=int, default=10, help="Number of concurrent threads (default: 10)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output (DEBUG level)")
    
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    try:
        # Input Sanitization: Validate and expand target using ipaddress module
        network = ipaddress.ip_network(args.target, strict=False)
        # Use a list of hosts to avoid including network/broadcast addresses in subnets > /32
        hosts: List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]] = []
        if network.prefixlen < 32:
            hosts = list(network.hosts())
        else:
            hosts = [network.network_address]
        
        if not hosts:
            hosts = [network.network_address]

        logger.info(f"Starting SYN scan on {args.target} ({len(hosts)} hosts) for port {args.port}")
        logger.info(f"Configuration: Concurrency={args.threads} threads | Timeout={args.timeout}s")

        # Architectural Choice: Use ThreadPoolExecutor for I/O-bound network tasks.
        # This provides a modular way to handle concurrency while keeping resource usage in check.
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            # map maintains result order, which is helpful for reading output
            results = executor.map(lambda ip: scan_host(ip, args.port, args.timeout), hosts)
            
            for result in results:
                # We only print the results that are successful or errors to keep stdout clean
                if "[+]" in result or "[!]" in result:
                    print(result)
                elif args.verbose:
                    print(result)

        logger.info("Scan operation completed successfully.")

    except ValueError as e:
        logger.error(f"Invalid target format: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("Scan interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Unhandled system error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
