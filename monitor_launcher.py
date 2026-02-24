#!/usr/bin/env python3
"""
Launcher for the Options Monitor when run as a packaged app (PyInstaller).
- Loads config from the same folder as the executable.
- Opens the default browser after the server starts.
- Runs the Flask monitor app.
"""

import os
import sys

# PyInstaller: point SSL to bundled certifi CA bundle so HTTPS (Polygon, etc.) works
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", "")
    if _meipass:
        _cacert = os.path.join(_meipass, "certifi", "cacert.pem")
        if os.path.isfile(_cacert):
            os.environ["SSL_CERT_FILE"] = _cacert
            os.environ["REQUESTS_CA_BUNDLE"] = _cacert
import threading
import webbrowser
import time

# When packaged, the exe lives in a folder; config should be next to it.
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
else:
    _exe_dir = os.path.dirname(os.path.abspath(__file__))

# Load .env from exe/app directory so teammates can put OptionsMonitor.env there
_config_names = ["OptionsMonitor.env", ".env"]
for name in _config_names:
    path = os.path.join(_exe_dir, name)
    if os.path.isfile(path):
        from dotenv import load_dotenv
        load_dotenv(path)
        break

# Optional: show a simple error if key is missing (only when frozen, for non-technical users)
if getattr(sys, "frozen", False):
    if not os.getenv("POLYGON_API_KEY", "").strip():
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            tk.messagebox.showerror(
                "Options Monitor",
                "POLYGON_API_KEY is missing.\n\n"
                "Please add a file named OptionsMonitor.env in the same folder as this app,\n"
                "with a line: POLYGON_API_KEY=your_key_here\n\n"
                "You can copy OptionsMonitor.env.example and edit it."
            )
            root.destroy()
        except Exception:
            print("POLYGON_API_KEY is missing. Add OptionsMonitor.env next to this app.")
        sys.exit(1)

# When frozen, run from exe dir so relative paths (e.g. us_ranked_headlines.csv) work
os.chdir(_exe_dir)

def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    """Return True when something is listening on port."""
    import socket
    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (socket.error, OSError):
            time.sleep(0.3)
    return False

if __name__ == "__main__":
    port = 5050

    def run_flask():
        import monitor_app
        # Bind to 127.0.0.1 so only localhost can connect
        import flask
        monitor_app.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

    server_thread = threading.Thread(target=run_flask, daemon=True)
    server_thread.start()

    # Wait until the server is actually listening before opening the browser
    if _wait_for_server(port):
        webbrowser.open(f"http://127.0.0.1:{port}")
    else:
        print("Server did not start in time. Check the console for errors.")

    # Keep the process running; Flask runs in the background thread
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
