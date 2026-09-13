"""Web dashboard server launcher for RecoverIT."""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser

import uvicorn


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def open_browser_delayed(url: str, delay: float = 1.0) -> None:
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def start_server(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    # Find free port if 8000 is occupied
    actual_port = port
    while is_port_in_use(actual_port, host) and actual_port < port + 20:
        actual_port += 1

    url = f"http://{host}:{actual_port}"
    print("\n" + "=" * 62)
    print("  RecoverIT — Web Incident Investigation Dashboard")
    print(f"  Server URL:  {url}")
    print("  Press Ctrl+C to stop the server.")
    print("=" * 62 + "\n")

    if open_browser:
        threading.Thread(target=open_browser_delayed, args=(url, 1.2), daemon=True).start()

    uvicorn.run("recoverit.web.app:app", host=host, port=actual_port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch RecoverIT Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")

    args = parser.parse_args()
    start_server(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
