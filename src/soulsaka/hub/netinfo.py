"""Find the addresses other devices can reach this hub on."""

from __future__ import annotations

import socket


def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packets are sent for UDP connect
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def hub_urls(port: int) -> list[str]:
    urls = [f"http://127.0.0.1:{port}"]
    urls += [f"http://{ip}:{port}" for ip in lan_ips()]
    host = socket.gethostname()
    if host and "." not in host:
        urls.append(f"http://{host}.local:{port}")
    return urls
