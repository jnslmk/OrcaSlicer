#!/usr/bin/env python3
"""Linux/WebKitGTK camera lifecycle check (PyGObject, pycairo, GTK3, WebKit2GTK 4.1).

Run under the workstation graphics-verification lock and a finite process-group
supervisor. --baseline omits the workaround to check the failing cache isolation.
"""

import argparse
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()

    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, Gtk, WebKit2

    frames = []
    for color in (0xFF0000FF, 0x0000FFFF):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 16, 16)
        pixbuf.fill(color)
        frames.append(pixbuf.save_to_bufferv("jpeg", [], [])[1])
    connections = set()
    connection_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass  # Navigation deliberately disconnects outstanding requests.

        def do_GET(self):
            self.send_response(200)
            self.send_header("Cache-Control", "no-cache")
            if self.path.startswith("/video"):
                self.send_header(
                    "Content-Type", "multipart/x-mixed-replace; boundary=--foo"
                )
                self.end_headers()
                with connection_lock:
                    connections.add(self)
                try:
                    while True:
                        for frame in frames:
                            self.wfile.write(
                                b"--foo\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                + str(len(frame)).encode()
                                + b"\r\n\r\n"
                                + frame
                                + b"\r\n"
                            )
                            self.wfile.flush()
                            time.sleep(0.15)
                finally:
                    with connection_lock:
                        connections.remove(self)
            else:
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""<!doctype html><style>
                    html,body{margin:0;width:100%;height:100%}
                    .video-img,.video-img-mobile{display:block;width:100%;height:100%;object-fit:contain}
                    .video-img-mobile{display:none}
                </style><body><script>
                    for (const name of ['video-img', 'video-img-mobile']) {
                        const img = new Image();
                        img.className = name;
                        img.src = 'http://127.0.0.1:3031/video?camera=1';
                        document.body.appendChild(img);
                    }
                    function cameraImage() {
                        const img = [...document.querySelectorAll('.video-img,.video-img-mobile')]
                            .find(i => i.getClientRects().length);
                        return img?.parentElement.querySelector('iframe')?.contentDocument?.images[0] || img;
                    }
                </script>""")

        def do_POST(self):
            remaining = int(self.headers["Content-Length"])
            received = 0
            while remaining:
                data = self.rfile.read(min(32768, remaining))
                if not data:
                    break
                received += len(data)
                remaining -= len(data)
                time.sleep(0.02)
            reply = str(received).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    stream = ThreadingHTTPServer(("127.0.0.1", 3031), Handler)
    page = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    for server in (stream, page):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    view = WebKit2.WebView()
    if not args.baseline:
        script = (
            pathlib.Path(__file__).resolve().parents[2]
            / "resources/web/elegoolink/camera-cache.js"
        )
        view.get_user_content_manager().add_script(
            WebKit2.UserScript.new(
                script.read_text(),
                WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                WebKit2.UserScriptInjectionTime.START,
                None,
                None,
            )
        )
    window = Gtk.Window()
    window.add(view)
    window.show_all()

    def evaluate(js):
        result = []

        def finished(v, task):
            result.append(v.evaluate_javascript_finish(task).to_json(0))

        view.evaluate_javascript(js, -1, None, None, None, finished)
        deadline = time.monotonic() + 5
        while not result and time.monotonic() < deadline:
            Gtk.main_iteration_do(False)
        assert result, "WebKit did not respond"
        return result[0]

    def wait_for(js, message):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if evaluate(js) == "true":
                return
        raise AssertionError(message)

    def snapshot():
        result = []

        def finished(v, task):
            result.append(bytes(v.get_snapshot_finish(task).get_data()))

        view.get_snapshot(
            WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, finished
        )
        deadline = time.monotonic() + 5
        while not result and time.monotonic() < deadline:
            Gtk.main_iteration_do(False)
        assert result, "WebKit did not capture the camera surface"
        return result[0]

    def check_camera():
        wait_for(
            "typeof cameraImage === 'function' && cameraImage()?.naturalWidth === 16",
            "Camera never decoded a frame",
        )
        wait_for(
            """(() => {
                const frame = document.querySelector('iframe');
                if (!frame) return true;
                const root = frame.contentDocument.documentElement;
                return root.scrollHeight <= root.clientHeight && root.scrollWidth <= root.clientWidth;
            })()""",
            "Camera frame has scrollable overflow",
        )
        first = snapshot()
        deadline = time.monotonic() + 5
        while snapshot() == first:
            assert time.monotonic() < deadline, "Camera shows a frozen frame"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with connection_lock:
                count = len(connections)
            if count == 1:
                return
            Gtk.main_iteration_do(False)
        raise AssertionError(f"Camera retained {count} stream connections")

    url = f"http://127.0.0.1:{page.server_port}/"
    try:
        view.load_uri(url)
        check_camera()
        old_stream = evaluate("cameraImage().src")
        for _ in range(8):
            view.load_uri(url)
            evaluate("document.images.length")
        evaluate("window.oldDocument = true")
        view.load_uri(url)
        wait_for(
            "!window.oldDocument && typeof cameraImage === 'function' && cameraImage()?.naturalWidth === 16",
            "Interrupted navigation did not start the camera",
        )
        assert evaluate("cameraImage().src") != old_stream, (
            "Navigation reused the previous stream's cache key"
        )
        assert (
            evaluate("new URL(cameraImage().src).searchParams.get('camera') === '1'")
            == "true"
        )
        check_camera()
        evaluate("""
            window.upload = 'pending';
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/upload');
            xhr.onload = () => window.upload = xhr.responseText;
            xhr.onabort = () => window.upload = 'aborted';
            xhr.send('x'.repeat(1048576));
            true
        """)
        for _ in range(8):
            old_stream = evaluate("cameraImage().src")
            evaluate("""(() => {
                const old = document.querySelector('.video-img');
                const replacement = new Image();
                replacement.className = 'video-img';
                replacement.src = 'http://127.0.0.1:3031/video?camera=1';
                old.replaceWith(replacement);
                return true;
            })()""")
            check_camera()
            assert evaluate("cameraImage().src") != old_stream, (
                "Expansion reused the previous stream's cache key"
            )
        wait_for(
            "window.upload === '1048576'", "Camera recovery interrupted the file upload"
        )
        evaluate("""
            document.querySelector('.video-img').style.display = 'none';
            document.querySelector('.video-img-mobile').style.display = 'block';
            true
        """)
        check_camera()
        view.load_uri(url + "?history=1")
        wait_for(
            "location.search === '?history=1' && typeof cameraImage === 'function' && cameraImage()?.naturalWidth === 16",
            "Camera did not restart on navigation",
        )
        check_camera()
        view.go_back()
        wait_for(
            "location.search === '' && typeof cameraImage === 'function' && cameraImage()?.naturalWidth === 16",
            "Camera did not restart on back navigation",
        )
        check_camera()
        print(
            "PASS: navigation and repeated expansion keep one moving stream without interrupting uploads"
        )
    finally:
        view.load_uri("about:blank")
        window.destroy()
        for server in (stream, page):
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
