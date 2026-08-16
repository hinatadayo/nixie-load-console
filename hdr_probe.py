#!/usr/bin/env python3
"""
hdr_probe.py -- Claude Code のレート制限ヘッダ傍受プローブ

api.anthropic.com への通信を中継し、レスポンスヘッダを覗くだけの
透過プロキシ。ボディには一切手を触れない。

目的:
    Claude Code (Pro/Max) のレスポンスに
    anthropic-ratelimit-unified-5h-* が乗ってくるかを確認する。

使い方:
    ターミナル1:
        python3 hdr_probe.py

    ターミナル2:
        export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
        claude

    Claude Code に何か話しかけると、ターミナル1にヘッダが流れる。

依存:
    標準ライブラリのみ。pip install 不要。

オプション:
    --port N    待ち受けポート (既定 8787)
    --all       anthropic-* 以外のヘッダも全部出す
    --out PATH  最新のレート制限情報を JSON で書き出す (既定 usage.json)
"""

import argparse
import http.client
import json
import re
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "api.anthropic.com"

DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")
PROBE_PREFIX = "/_probe"

# 中継してはいけないヘッダ (RFC 7230 hop-by-hop)
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}

# 注目するヘッダ
WATCH = re.compile(r"^(anthropic-|retry-after|x-ratelimit)", re.I)

ARGS = None
LOCK = threading.Lock()
SEEN = set()
RL_DATA = {}


def log(msg=""):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def dump_headers(method, path, status, headers):
    """レスポンスヘッダを表示し、レート制限情報を JSON に書き出す"""
    stamp = time.strftime("%H:%M:%S")
    picked = []
    for k, v in headers.items():
        if ARGS.all or WATCH.match(k):
            picked.append((k, v))

    with LOCK:
        log()
        log(f"--- {stamp}  {method} {path}  -> {status} ---")
        if not picked:
            log("  (該当ヘッダなし)")
        for k, v in picked:
            mark = " " if k.lower() in SEEN else "*"
            SEEN.add(k.lower())
            log(f" {mark} {k}: {v}")

        rl = {k.lower(): v for k, v in picked
              if k.lower().startswith("anthropic-ratelimit")}
        if rl:
            rl["_captured_at"] = stamp
            RL_DATA.clear()
            RL_DATA.update(rl)
            try:
                with open(ARGS.out, "w", encoding="utf-8") as f:
                    json.dump(rl, f, ensure_ascii=False, indent=2)
            except OSError as e:
                log(f"  [警告] {ARGS.out} に書けませんでした: {e}")


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "hdr_probe"

    def log_message(self, fmt, *a):
        pass  # 標準のアクセスログは黙らせる

    # --- 全メソッドを同じ処理へ ---
    def do_GET(self):
        if self.path == f"{PROBE_PREFIX}/usage.json":
            self.serve_probe_json()
            return
        if self.path in (f"{PROBE_PREFIX}/dashboard", f"{PROBE_PREFIX}", f"{PROBE_PREFIX}/"):
            self.serve_probe_dashboard()
            return
        self.relay()

    def serve_probe_json(self):
        with LOCK:
            body = json.dumps(RL_DATA, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_probe_dashboard(self):
        try:
            body = DASHBOARD_PATH.read_bytes()
        except OSError as e:
            self.send_error(404, f"dashboard.html not found: {e}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.relay()

    def do_PUT(self):
        self.relay()

    def do_PATCH(self):
        self.relay()

    def do_DELETE(self):
        self.relay()

    def do_HEAD(self):
        self.relay()

    def do_OPTIONS(self):
        self.relay()

    def read_request_body(self):
        cl = self.headers.get("Content-Length")
        if cl is not None:
            return self.rfile.read(int(cl))
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            chunks = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        return None

    def relay(self):
        try:
            body = self.read_request_body()
        except Exception as e:
            self.send_error(400, f"request body error: {e}")
            return

        out_headers = {}
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP:
                continue
            out_headers[k] = v
        out_headers["Host"] = UPSTREAM
        # ボディは触らないので、圧縮指定もそのまま通す

        try:
            conn = http.client.HTTPSConnection(UPSTREAM, timeout=900)
            conn.request(self.command, self.path, body=body, headers=out_headers)
            resp = conn.getresponse()
        except Exception as e:
            log(f"[上流エラー] {e}")
            try:
                self.send_error(502, "upstream error")
            except Exception:
                pass
            return

        dump_headers(self.command, self.path, resp.status, resp.headers)

        content_length = resp.getheader("Content-Length")
        try:
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)

            if content_length is not None:
                self.send_header("Content-Length", content_length)
                self.end_headers()
                remaining = int(content_length)
                while remaining > 0:
                    chunk = resp.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            else:
                # ストリーミング (SSE)。チャンク転送で逐次流す
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n" % len(chunk))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global ARGS
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--all", action="store_true",
                   help="anthropic-* 以外のヘッダも表示する")
    p.add_argument("--out", default="usage.json")
    ARGS = p.parse_args()

    try:
        with open(ARGS.out, "r", encoding="utf-8") as f:
            RL_DATA.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass

    log("=" * 62)
    log(" hdr_probe -- レート制限ヘッダ傍受プローブ")
    log("=" * 62)
    log(f"  待ち受け : http://127.0.0.1:{ARGS.port}")
    log(f"  転送先   : https://{UPSTREAM}")
    log(f"  出力     : {ARGS.out}")
    log()
    log("  別のターミナルで以下を実行してください:")
    log()
    log(f"    export ANTHROPIC_BASE_URL=http://127.0.0.1:{ARGS.port}")
    log("    claude")
    log()
    log(f"  ダッシュボード (自動更新) : http://127.0.0.1:{ARGS.port}/_probe/dashboard")
    log()
    log("  * が付いたヘッダは初出です。Ctrl-C で終了。")
    log("=" * 62)

    try:
        Server(("127.0.0.1", ARGS.port), Proxy).serve_forever()
    except KeyboardInterrupt:
        log("\n終了しました。")


if __name__ == "__main__":
    main()