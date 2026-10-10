# -*- coding: utf-8 -*-
# Redis 缓存记录可视化面板
# 用法：在项目根目录运行  ./.venv/Scripts/python.exe scripts/redis_visualizer.py
# 然后浏览器打开 http://localhost:8008 即可看到缓存记录（支持自动刷新）
# 说明：只读取 Redis，不修改任何数据，不影响业务代码
# 位置说明：本脚本属于"运维/调试工具"，和 ingest_news.py 一起放 scripts/；
#   它不 import 项目任何模块（只用标准库 + redis），所以放在子目录里照样能独立运行。
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import redis

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# 连接 Redis（protocol=2 是 redis-py 8.x 连 Redis 5.x 必须的，否则报 HELLO 错误）
r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
    protocol=2
)


def get_keys_info():
    """扫描所有 key，返回 名称/类型/TTL/大小/值"""
    keys = []
    for key in r.scan_iter():
        kind = r.type(key)          # 类型：string/list/hash/set/zset
        ttl = r.ttl(key)            # 剩余存活秒数（-1=永不过期）
        info = {"name": key, "type": kind, "ttl": ttl, "value": None, "size": 0}
        try:
            if kind == "string":
                val = r.get(key)
                info["size"] = len(val) if val else 0
                info["value"] = val
            elif kind == "list":
                items = r.lrange(key, 0, -1)
                info["size"] = len(items)
                info["value"] = items
            elif kind == "hash":
                h = r.hgetall(key)
                info["size"] = len(h)
                info["value"] = h
            elif kind == "set":
                s = r.smembers(key)
                info["size"] = len(s)
                info["value"] = list(s)
            elif kind == "zset":
                z = r.zrange(key, 0, -1, withscores=True)
                info["size"] = len(z)
                info["value"] = z
        except Exception as e:
            info["value"] = "<读取失败: %s>" % e
        keys.append(info)
    keys.sort(key=lambda k: k["name"])
    return keys


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 不打印访问日志，保持终端干净

    # 统一响应 JSON
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # 统一响应 HTML
    def send_html(self, html, code=200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/keys":
            try:
                data = get_keys_info()
                self.send_json({"ok": True, "total": len(data), "keys": data})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_html(PAGE)


# ---------- 前端页面：深色主题缓存面板 ----------
PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Redis 缓存记录可视化</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:"Microsoft YaHei",system-ui,sans-serif; background:#1e1e2e; color:#cdd6f4; padding:24px; }
  .header { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; margin-bottom:20px; }
  h1 { font-size:20px; color:#89b4fa; }
  .sub { color:#a6adc8; font-size:13px; margin-top:4px; }
  .actions { display:flex; gap:10px; align-items:center; }
  button { background:#313244; color:#cdd6f4; border:1px solid #45475a; border-radius:8px; padding:8px 16px; cursor:pointer; font-size:13px; }
  button:hover { background:#45475a; }
  .stats { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
  .stat { background:#313244; border:1px solid #45475a; border-radius:10px; padding:12px 20px; min-width:130px; }
  .stat .num { font-size:24px; font-weight:bold; color:#a6e3a1; }
  .stat .label { font-size:12px; color:#a6adc8; margin-top:4px; }
  table { width:100%; border-collapse:collapse; background:#313244; border-radius:10px; overflow:hidden; }
  th,td { text-align:left; padding:10px 14px; border-bottom:1px solid #45475a; font-size:13px; }
  th { background:#181825; color:#89b4fa; font-weight:600; }
  tr:hover td { background:#38384d; cursor:pointer; }
  .badge { display:inline-block; padding:2px 10px; border-radius:20px; font-size:12px; color:#11111b; font-weight:bold; }
  .t-string { background:#a6e3a1; } .t-list { background:#89b4fa; } .t-hash { background:#fab387; }
  .t-set { background:#cba6f7; } .t-zset { background:#f38ba8; }
  .ttl-no { color:#a6e3a1; } .ttl-ok { color:#f9e2af; } .ttl-dead { color:#f38ba8; }
  .detail { display:none; background:#181825; padding:14px; border-radius:8px; margin:6px 0; }
  .detail.open { display:block; }
  pre { white-space:pre-wrap; word-break:break-all; color:#a6e3a1; font-size:12px; font-family:Consolas,monospace; }
  .empty { text-align:center; color:#a6adc8; padding:40px; }
  #status { font-size:12px; color:#a6adc8; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🐍 Redis 缓存记录可视化</h1>
    <div class="sub">localhost:6379 / db 0 &nbsp;·&nbsp; 每 5 秒自动刷新（点刷新按钮立即刷新）</div>
  </div>
  <div class="actions">
    <span id="status"></span>
    <button onclick="loadKeys()">🔄 立即刷新</button>
  </div>
</div>

<div class="stats">
  <div class="stat"><div class="num" id="sTotal">-</div><div class="label">缓存记录总数</div></div>
  <div class="stat"><div class="num" id="sExpiring">-</div><div class="label">带过期时间</div></div>
  <div class="stat"><div class="num" id="sForever">-</div><div class="label">永不过期</div></div>
</div>

<div id="tableWrap"></div>

<script>
var TYPE_LABEL = { "string":"string", "list":"list", "hash":"hash", "set":"set", "zset":"zset" };

function badge(kind) {
  return '<span class="badge t-' + kind + '">' + (TYPE_LABEL[kind] || kind) + '</span>';
}

function ttlText(ttl) {
  if (ttl === -1) return '<span class="ttl-no">永不过期</span>';
  if (ttl <= 0)   return '<span class="ttl-dead">已过期</span>';
  var m = Math.floor(ttl / 60), s = ttl % 60;
  return '<span class="ttl-ok">' + m + '分' + s + '秒</span>';
}

function prettyValue(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === "string") {
    try { return JSON.stringify(JSON.parse(v), null, 2); } catch(e) { return v; }
  }
  return JSON.stringify(v, null, 2);
}

function render(keys) {
  var sTotal = document.getElementById("sTotal");
  var sExpiring = document.getElementById("sExpiring");
  var sForever = document.getElementById("sForever");
  sTotal.textContent = keys.length;
  var expiring = keys.filter(function(k){ return k.ttl > 0; }).length;
  var forever = keys.filter(function(k){ return k.ttl === -1; }).length;
  sExpiring.textContent = expiring;
  sForever.textContent = forever;

  if (keys.length === 0) {
    document.getElementById("tableWrap").innerHTML =
      '<div class="empty">🍃 暂无缓存记录<br><br>先运行 tests/smoke_cache.py 或调用业务接口写入缓存</div>';
    return;
  }
  var rows = keys.map(function(k, i) {
    var val = prettyValue(k.value);
    return '<tr onclick="toggleDetail(' + i + ')">' +
      '<td><b>' + k.name + '</b></td>' +
      '<td>' + badge(k.type) + '</td>' +
      '<td>' + k.size + '</td>' +
      '<td>' + ttlText(k.ttl) + '</td>' +
      '</tr>' +
      '<tr id="row' + i + '" class="detail"><td colspan="4"><pre>' +
      escHtml(val) + '</pre></td></tr>';
  }).join("");
  document.getElementById("tableWrap").innerHTML =
    '<table><tr><th>Key（缓存记录名）</th><th>类型</th><th>大小</th><th>剩余时间</th></tr>' + rows + '</table>';
}

function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function toggleDetail(i) {
  var row = document.getElementById("row" + i);
  row.classList.toggle("open");
}

function loadKeys() {
  fetch("/api/keys").then(function(res){ return res.json(); }).then(function(data) {
    document.getElementById("status").textContent = "⏱ " + new Date().toLocaleTimeString();
    if (data.ok) { render(data.keys); }
    else { document.getElementById("tableWrap").innerHTML = '<div class="empty">❌ ' + escHtml(data.error) + '</div>'; }
  }).catch(function(e) {
    document.getElementById("tableWrap").innerHTML = '<div class="empty">❌ 连接失败：' + escHtml(e) + '</div>';
  });
}

loadKeys();
setInterval(loadKeys, 5000); // 每 5 秒自动刷新
</script>
</body>
</html>
"""


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8008), Handler)
    print("可视化面板已启动 → http://localhost:8008")
    print("按 Ctrl+C 停止服务")
    server.serve_forever()
