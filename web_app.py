# -*- coding: utf-8 -*-
"""
WoW 语音包工坊 —— 网页服务（标准库 http.server，零第三方依赖）

启动：
    python web_app.py
然后浏览器打开 http://127.0.0.1:8788
"""

import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402
from core import (APP_NAME, APP_VERSION, CORPUS, CORPUS_META, VOICE_LIST,  # noqa: E402
                  FFmpeg, Job, TTS, build_package, check_lsm_installed,
                  corpus_as_text, detect_ffmpeg, detect_wow_addons,
                  group_oggs_by_voice, load_config, normalize_tag,
                  package_to_zip, parse_lines, qwen_available, run_job,
                  safe_filename, save_config, scan_qwen_voices,
                  scan_voices, voice_label)

PORT = 8788
ROOT = core.app_root()
WEB = os.path.join(core.resource_dir(), "web")
WORK = os.path.join(ROOT, "output")

os.makedirs(WORK, exist_ok=True)

TTS_ENGINE = TTS()
STATE = {"job": None, "ff": None, "history": [], "last_job": None}


def log_push(msg, level="info"):
    STATE["history"].append({"t": time.strftime("%H:%M:%S"), "msg": msg, "level": level})
    if len(STATE["history"]) > 400:
        del STATE["history"][:-400]


# ---------------------------------------------------------------- 业务接口

def api_env(_req):
    cfg = load_config()
    wow = cfg.get("wow_addons_dir") or detect_wow_addons()
    ff_path = cfg.get("ffmpeg") or detect_ffmpeg()
    ff = FFmpeg(ff_path)
    has_edge = TTS_ENGINE._edge_ready()
    has_local = TTS_ENGINE.local_ready()
    local_voices = [v.as_dict() for v in scan_voices(cfg.get("voice_roots", ""))]
    qwen_voices = scan_qwen_voices()
    # output 里已有哪些音色、各多少条（供打包页勾选）
    ogg_groups = group_oggs_by_voice(WORK)
    ogg_voices = sorted([{"key": k, "count": len(v)}
                         for k, v in ogg_groups.items()], key=lambda x: x["key"])
    oggs = [f for f in os.listdir(WORK) if f.lower().endswith(".ogg")] if os.path.isdir(WORK) else []
    total_bytes = 0
    for f in oggs:
        try:
            total_bytes += os.path.getsize(os.path.join(WORK, f))
        except OSError:
            pass
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "root": ROOT,
        "work": WORK,
        "wow_addons": wow,
        "wow_found": bool(wow and os.path.isdir(wow)),
        "ffmpeg": ff_path,
        "ffmpeg_ok": ff.ok,
        "edge_ok": has_edge,
        "local_ok": has_local,
        "qwen_ok": qwen_available(),
        "qwen_voices": qwen_voices,
        "lsm_ok": check_lsm_installed(wow),
        "local_voices": local_voices,
        "config": cfg,
        "ogg_count": len(oggs),
        "ogg_bytes": total_bytes,
        "ogg_voices": ogg_voices,
        "voices": [{"id": i, "name": n, "engine": e} for i, n, e in VOICE_LIST],
        "corpus_meta": CORPUS_META,
        "preview": _preview_list(),
        "log": STATE["history"][-60:],
    }


def _preview_list():
    if not os.path.isdir(WORK):
        return []
    items = []
    for fn in sorted(os.listdir(WORK)):
        if not fn.lower().endswith(".ogg"):
            continue
        p = os.path.join(WORK, fn)
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        items.append({"name": fn, "size": sz})
    return items


def api_corpus(_req):
    """语料转成 '文件名|台词' 每行一条的文本，前端直接塞进文本框。"""
    return {"data": {k: corpus_as_text(v) for k, v in CORPUS.items()}}


def api_parse_preview(req):
    items = parse_lines(req.get("text", ""))
    return {"count": len(items),
            "preview": [{"name": k, "text": v} for k, v in items[:12]]}


def api_save_config(req):
    cfg = load_config()
    for k in ("wow_addons_dir", "ffmpeg", "package", "display_name",
              "author_tag", "interface_version", "voice_roots"):
        if k in req:
            cfg[k] = req[k]
    cfg["author_tag"] = normalize_tag(cfg.get("author_tag", ""))
    save_config(cfg)
    log_push(f"设置已保存（包名 {cfg['package']}）")
    return {"ok": True, "config": cfg}


def api_preview_tts(req):
    """单条试听：直接返回生成的 ogg。"""
    text = (req.get("text") or "语音试听，注意减伤").strip()
    voice = req.get("voice") or "zh-CN-XiaoxiaoNeural"
    rate = int(req.get("rate", 0) or 0)
    sapi_rate = int(req.get("sapi_rate", 0) or 0)
    ff = _get_ff()
    if not ff.ok:
        return {"ok": False, "err": "找不到 ffmpeg"}
    import tempfile
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    ogg = os.path.join(tempfile.gettempdir(), f"_prev_{int(time.time()*1000)}.ogg")
    try:
        TTS_ENGINE.synth(text, voice, wav, rate_pct=rate, sapi_rate=sapi_rate,
                         temperature=float(req.get("temperature", 0.5) or 0.5))
        ff.to_ogg(wav, ogg)
        return {"ok": True, "file": ogg}
    except Exception as e:
        return {"ok": False, "err": str(e)[:300]}
    finally:
        try:
            if os.path.exists(wav):
                os.remove(wav)
        except OSError:
            pass


def _get_ff():
    cfg = load_config()
    ff = STATE.get("ff")
    path = cfg.get("ffmpeg") or detect_ffmpeg()
    if ff is None or not ff.ok or getattr(ff, "path", "") != path:
        ff = FFmpeg(path)
        STATE["ff"] = ff
    return ff


def api_start(req):
    if STATE["job"] and STATE["job"].running:
        return {"ok": False, "err": "已有任务在跑"}
    text = req.get("text") or ""
    items = parse_lines(text)
    if not items:
        return {"ok": False, "err": "没有可生成的台词（每行 '文件名|台词'）"}
    ff = _get_ff()
    if not ff.ok:
        return {"ok": False, "err": "找不到 ffmpeg，请在「设置」里指定路径"}
    cfg = load_config()
    voice = req.get("voice") or "zh-CN-XiaoxiaoNeural"
    auto_tag = req.get("auto_tag", True)
    vtag = voice_label(voice) if auto_tag else ""
    job = Job(
        items=items,
        voice=voice,
        ff=ff,
        out_dir=WORK,
        rate_pct=int(req.get("rate", 0) or 0),
        sapi_rate=int(req.get("sapi_rate", 0) or 0),
        tag=normalize_tag(req.get("tag") or cfg.get("author_tag") or ""),
        voice_tag=vtag,
        temperature=float(req.get("temperature", 0.5) or 0.5),
    )
    STATE["job"] = job
    STATE["last_job"] = None
    label = vtag or voice
    log_push(f"开始生成 {len(items)} 条（音色：{label}）")

    def worker():
        run_job(job, TTS_ENGINE, lambda s: None)
        if job.cancel:
            log_push("任务已取消", "warn")
        else:
            msg = f"生成结束：成功 {len(job.done)} 条"
            if job.failed:
                msg += f"，失败 {len(job.failed)} 条"
            log_push(msg, "warn" if job.failed else "ok")
        STATE["last_job"] = job.snapshot()

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "total": len(items), "voice_tag": vtag}


def api_status(_req):
    job = STATE["job"]
    base = {"log": STATE["history"][-60:], "preview": _preview_list(),
            "ogg_voices": sorted([{"key": k, "count": len(v)}
                                  for k, v in group_oggs_by_voice(WORK).items()],
                                 key=lambda x: x["key"])}
    if not job:
        # 任务已结束：保留最后一次快照，页面刷新后仍能看到结果
        last = STATE.get("last_job")
        return {"job": last, **base}
    s = job.snapshot()
    s["log"] = STATE["history"][-60:]
    s["preview"] = _preview_list()
    s["ogg_voices"] = base["ogg_voices"]
    return s


def api_cancel(_req):
    if STATE["job"]:
        STATE["job"].cancel = True
        return {"ok": True}
    return {"ok": False}


def api_rename(req):
    """批量重命名：把 ogg 按映射改名（用于修正生成的文件名）。"""
    mapping = req.get("mapping") or {}
    n = 0
    for old, new in mapping.items():
        op = os.path.join(WORK, safe_filename(old))
        np_ = os.path.join(WORK, safe_filename(new))
        if os.path.exists(op) and not os.path.exists(np_):
            os.rename(op, np_)
            n += 1
    log_push(f"重命名 {n} 个文件")
    return {"ok": True, "count": n}


def api_delete(req):
    names = req.get("names") or []
    n = 0
    for fn in names:
        p = os.path.join(WORK, safe_filename(fn))
        try:
            if os.path.exists(p):
                os.remove(p)
                n += 1
        except OSError as e:
            log_push(f"删除失败 {fn}: {e}", "warn")
    log_push(f"删除 {n} 个文件")
    return {"ok": True, "count": n}


def api_clear(_req):
    n = 0
    for fn in os.listdir(WORK):
        if fn.lower().endswith(".ogg"):
            try:
                os.remove(os.path.join(WORK, fn))
                n += 1
            except OSError:
                pass
    log_push(f"清空 {n} 个文件", "warn")
    return {"ok": True, "count": n}


def api_package(req):
    cfg = load_config()
    for k in ("package", "display_name", "author_tag"):
        if req.get(k):
            cfg[k] = req[k]
    cfg["author_tag"] = normalize_tag(cfg.get("author_tag", ""))
    save_config(cfg)
    try:
        # 语音就生成在 WORK 根目录，所以直接从那里取
        # subdirs 语义：{包内子目录名: 源子目录名(相对 src_dir)}，空串 = 直接读 src_dir
        group = bool(req.get("group_by_voice", True))
        only = req.get("only_voices") or None
        pdir, cnt = build_package(
            cfg, WORK,
            package=cfg["package"],
            display=cfg["display_name"],
            subdirs={"media": ""},
            src_dir=WORK,
            group_by_voice=group,
            only_voices=only,
            log=log_push,
        )
        if group:
            from core import group_oggs_by_voice as _gbv
            gs = _gbv(WORK)
            picked = sorted(only) if only else sorted(gs)
            log_push("本次打包音色：" + "、".join(picked) + f"（共 {cnt} 条）")
        return {"ok": True, "dir": pdir, "count": cnt, "voices": picked}
    except Exception as e:
        return {"ok": False, "err": str(e)[:300]}


def api_zip(_req):
    cfg = load_config()
    try:
        pdir, _ = build_package(cfg, WORK, subdirs={"media": ""},
                                 src_dir=WORK,
                                 group_by_voice=bool(req.get("group_by_voice", True)),
                                 only_voices=req.get("only_voices") or None,
                                 log=log_push)
        zp = os.path.join(ROOT, cfg["package"] + ".zip")
        package_to_zip(pdir, zp)
        log_push(f"已导出 zip：{zp}")
        return {"ok": True, "file": zp}
    except Exception as e:
        return {"ok": False, "err": str(e)[:300]}


def api_scan_wow(_req):
    p = detect_wow_addons()
    return {"ok": bool(p), "dir": p}


def api_open_folder(req):
    """在资源管理器里打开目录。"""
    path = req.get("path") or WORK
    if not os.path.exists(path):
        return {"ok": False, "err": "路径不存在"}
    try:
        if sys.platform == "win32":
            os.startfile(path)          # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "err": str(e)[:200]}


ROUTES = {
    "env": api_env,
    "corpus": api_corpus,
    "parse": api_parse_preview,
    "save_config": api_save_config,
    "preview_tts": api_preview_tts,
    "start": api_start,
    "status": api_status,
    "cancel": api_cancel,
    "rename": api_rename,
    "delete": api_delete,
    "clear": api_clear,
    "package": api_package,
    "zip": api_zip,
    "scan_wow": api_scan_wow,
    "open_folder": api_open_folder,
}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "WoWVoice/" + APP_VERSION

    def log_message(self, fmt, *args):   # 静默访问日志
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, download=False):
        if not os.path.isfile(path):
            self._send_json({"ok": False, "err": "文件不存在"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        if download:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{urllib.parse.quote(os.path.basename(path))}"')
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        one = lambda k, d="": (q.get(k) or [d])[0]

        if u.path == "/":
            return self._send_file(os.path.join(WEB, "index.html"))
        if u.path == "/audio":
            return self._send_file(one("f"))
        if u.path == "/download":
            return self._send_file(one("f"), download=True)
        if u.path == "/api":
            fn = ROUTES.get(one("a"))
            if not fn:
                return self._send_json({"ok": False, "err": "未知接口"}, 404)
            try:
                return self._send_json(fn({"text": one("text"), "voice": one("voice"),
                                           "rate": one("rate"), "sapi_rate": one("sapi_rate"),
                                           "tag": one("tag")}))
            except Exception as e:
                return self._send_json({"ok": False, "err": str(e)[:400]}, 500)
        # 静态
        rel = posixpath.normpath(urllib.parse.unquote(u.path)).lstrip("/")
        target = os.path.normpath(os.path.join(WEB, rel))
        if target.startswith(os.path.normpath(WEB)) and os.path.isfile(target):
            return self._send_file(target)
        self._send_json({"ok": False, "err": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            req = {}
        # 兼容 /api/xxx 与 /xxx 两种写法
        action = u.path.rsplit("/", 1)[-1]
        fn = ROUTES.get(action)
        if not fn:
            return self._send_json({"ok": False, "err": "未知接口"}, 404)
        try:
            return self._send_json(fn(req))
        except Exception as e:
            return self._send_json({"ok": False, "err": str(e)[:400]}, 500)


def main():
    port = PORT
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        print("端口都被占用了，请关掉其他程序再试")
        return
    url = f"http://127.0.0.1:{port}/"
    print("=" * 56)
    print(f"  {APP_NAME} v{APP_VERSION}")
    print(f"  工作目录：{ROOT}")
    print(f"  语音输出：{WORK}")
    print(f"  打开：{url}")
    print("  （关掉这个黑窗口程序就退出了）")
    print("=" * 56)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
