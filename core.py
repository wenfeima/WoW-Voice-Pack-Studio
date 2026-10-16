# -*- coding: utf-8 -*-
"""
WoW 语音包工坊 —— 核心库（零第三方依赖，仅标准库 + 可选 edge-tts）

设计要点
--------
1. 自适应目录：所有路径基于本文件位置推导，不写死盘符；WoW 客户端靠注册表/
   常见位置探测，可被用户设置覆盖（存相对/语义标记）。
2. 严格对齐暴雪 SharedMedia 规范：
   - 音频规格 44100Hz / 立体声 / libvorbis -q:a 5 / .ogg
   - MyMedia.lua 用 LSM:Register("sound", 显示名, [[Interface\\Addons\\包名\\目录\\文件]])
   - .toc 需 Interface 版本号 + Dependencies: SharedMedia
3. TTS 多引擎：edge-tts（在线，中文自然）/ 系统 SAPI（离线兜底）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

APP_NAME = "WoW语音包工坊"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------- 路径解析

def app_root():
    """程序根目录：打包后是 exe 所在目录，否则是本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """只读资源目录（web 页面等），打包后在 _MEIPASS。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", app_root())
    return app_root()


CONFIG_PATH = os.path.join(app_root(), "config.json")

DEFAULT_CONFIG = {
    "wow_addons_dir": "",       # WoW 的 Interface\AddOns 目录，留空则自动探测
    "ffmpeg": "",               # ffmpeg 路径，留空则自动探测
    "package": "SharedMedia_WoWVoice",
    "display_name": "我的语音包",
    "author_tag": "【我】",      # 文件名前缀，会被程序规范化
    "interface_version": "120100, 16001, 120105",
    "voice_roots": "",          # 自定义音色搜索根目录，多个用 ; 分隔
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- WoW 探测

_WOW_CANDIDATES = [
    r"D:\暴雪\World of Warcraft",
    r"D:\Blizzard\World of Warcraft",
    r"C:\Program Files (x86)\World of Warcraft",
    r"C:\Program Files\World of Warcraft",
    r"E:\World of Warcraft",
    r"F:\World of Warcraft",
    r"L:\World of Warcraft",
]


def detect_wow_addons():
    """探测 Interface\\AddOns 目录。优先注册表，再常见路径。"""
    # 1) 注册表
    try:
        import winreg
        key = r"SOFTWARE\Wow6432Node\Blizzard\WoW"
        for root, path in ((winreg.HKEY_CURRENT_USER, key),
                           (winreg.HKEY_LOCAL_MACHINE, key)):
            try:
                with winreg.OpenKey(root, path) as k:
                    i = 0
                    while True:
                        try:
                            name, val, _ = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        if isinstance(val, str) and "World of Warcraft" in val:
                            for sub in ("_retail_", "_classic_", "_classic_era_"):
                                p = os.path.join(val, sub, "Interface", "AddOns")
                                if os.path.isdir(p):
                                    return p
                            p = os.path.join(val, "_retail_", "Interface", "AddOns")
                            if os.path.isdir(p):
                                return p
                        i += 1
            except OSError:
                continue
    except Exception:
        pass

    # 2) 常见路径
    for base in _WOW_CANDIDATES:
        p = os.path.join(base, "_retail_", "Interface", "AddOns")
        if os.path.isdir(p):
            return p
    return ""


def detect_ffmpeg():
    """探测 ffmpeg：优先 PATH，其次找同机器上已有的 ffmpeg。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    roots = [
        os.path.join(app_root(), "runtime", "ffmpeg", "bin", "ffmpeg.exe"),
        r"L:\工作流\小工具\MultiTTS-VoiceMaker\runtime\ffmpeg\bin\ffmpeg.exe",
        r"L:\工作流\MultiTTS\runtime\ffmpeg\bin\ffmpeg.exe",
    ]
    for p in roots:
        if os.path.isfile(p):
            return p
    # 兜底：在小工具目录下浅层搜一次
    for base in (r"L:\工作流\小工具", os.path.dirname(app_root())):
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            depth = dirpath[len(base):].count(os.sep)
            if depth > 3:
                dirnames[:] = []
                continue
            if "ffmpeg.exe" in filenames:
                return os.path.join(dirpath, "ffmpeg.exe")
    return ""


# ---------------------------------------------------------------- 名称规范化

_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


def safe_filename(name, fallback="voice"):
    """去掉 Windows 非法字符，保留中文（露露的包就是中文名）。"""
    name = (name or "").strip()
    name = _ILLEGAL.sub("_", name)
    name = name.rstrip(". ")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    # 避免 Windows 保留名
    if name.upper() in ("CON", "PRN", "AUX", "NUL") or re.match(r"^(CON|PRN|AUX|NUL)\.", name.upper()):
        name = "_" + name
    return name[:80]


def normalize_tag(tag):
    """作者前缀规范化：确保以【】包裹，供界面分组用。"""
    tag = (tag or "").strip()
    if not tag:
        return ""
    tag = safe_filename(tag, "我")
    if not (tag.startswith("【") and tag.endswith("】")):
        # 兼容 "露露" -> "【露露】",  "【露露1】" 原样
        tag = "【" + tag.strip("【】") + "】"
    return tag


def display_name_for(tag, base):
    """MyMedia.lua 里显示的名字：露露用 【露露1】xxx 分组，我们沿用同风格。"""
    if not tag:
        return base
    inner = tag.strip("【】")
    # 【露露】-> 前缀组 【露露A】；【露露1】-> 原样
    if re.fullmatch(r"[\w\u4e00-\u9fff]+", inner):
        return f"{tag}{base}"
    return f"{tag}{base}"


# ---------------------------------------------------------------- ffmpeg 封装

class FFmpeg:
    def __init__(self, path):
        self.path = path
        self.ok = bool(path) and os.path.isfile(path)

    def to_ogg(self, src, dst, bitrate_q=5, sr=44100, channels=2):
        """转成暴雪兼容的 ogg：44100Hz 立体声 libvorbis。"""
        if not self.ok:
            raise RuntimeError("找不到 ffmpeg，请在设置里指定路径")
        cmd = [
            self.path, "-y", "-hide_banner", "-loglevel", "error",
            "-i", src,
            "-af", f"aresample={sr}",
            "-ac", str(channels),
            "-c:a", "libvorbis", "-q:a", str(bitrate_q),
            dst,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.isfile(dst):
            raise RuntimeError("ffmpeg 转码失败: " + r.stderr.decode("utf-8", "ignore")[-400:])

    def probe_duration(self, path):
        """返回时长（秒），失败返回 None。纯标准库解析 ogg/wav 头。"""
        try:
            if path.lower().endswith(".wav"):
                import wave
                with wave.open(path, "rb") as w:
                    return w.getnframes() / float(w.getframerate())
            # ogg：找最后一页的 granuleposition
            with open(path, "rb") as f:
                data = f.read()
            idx = data.rfind(b"OggS")
            if idx < 0:
                return None
            gran = int.from_bytes(data[idx + 6: idx + 14], "little")
            return gran / 44100.0
        except Exception:
            return None


# ---------------------------------------------------------------- TTS 引擎

def voice_label(voice_id):
    """从音色 id 反推人类可读的短标签，用于自动区分不同音色的产物。"""
    if voice_id.startswith("qwen:"):
        return os.path.splitext(os.path.basename(voice_id[5:]))[0]
    if voice_id.startswith("local:"):
        return os.path.basename(voice_id[6:].rstrip("\\/"))
    if voice_id == "sys":
        return "系统"
    # edge-tts：去掉 zh-CN- 和 Neural 后缀
    v = voice_id.replace("zh-CN-", "").replace("Neural", "")
    return v


VOICE_LIST = [
    # (id, 显示名, 引擎)
    ("zh-CN-XiaoxiaoNeural", "晓晓（女·自然亲切）", "edge"),
    ("zh-CN-XiaoyiNeural", "晓伊（女·活泼）", "edge"),
    ("zh-CN-YunjianNeural", "云健（男·浑厚）", "edge"),
    ("zh-CN-YunxiNeural", "云希（男·年轻）", "edge"),
    ("zh-CN-YunyangNeural", "云扬（男·新闻播报）", "edge"),
    ("zh-CN-XiaochenNeural", "晓辰（女·沉稳）", "edge"),
    ("zh-CN-XiaohanNeural", "晓涵（女·温柔）", "edge"),
    ("zh-CN-XiaomoNeural", "晓墨（女·知性）", "edge"),
    ("zh-CN-XiaoruiNeural", "晓睿（女·少女）", "edge"),
    ("zh-CN-XiaoshuangNeural", "晓双（女·儿童）", "edge"),
    ("zh-CN-XiaoxuanNeural", "晓萱（女·清亮）", "edge"),
    ("zh-CN-XiaoyanNeural", "晓颜（女·标准）", "edge"),
    ("zh-CN-YunfengNeural", "云枫（男·磁性）", "edge"),
    ("zh-CN-YunheNeural", "云和（男·温和）", "edge"),
    ("zh-CN-YunxiaNeural", "云夏（男·青年）", "edge"),
    ("zh-CN-YunzeNeural", "云泽（男·有力）", "edge"),
    ("sys", "系统语音（本机·离线兜底）", "sapi"),
]


class TTS:
    """多引擎 TTS。
    qwen   = Qwen3-TTS 自定义克隆音色（.pt，如君仪2 / 未来1）★最贴合需求
    local  = 本地 VITS 音色目录（sherpa-onnx）
    edge   = edge-tts 在线中文音色
    sapi   = Windows 系统语音（离线兜底）
    """

    def __init__(self):
        self._edge = None
        self._edge_failed = False
        self._local = {}          # path -> OfflineTts

    # -- 本地自定义音色（sherpa-onnx） --
    def local_ready(self):
        try:
            import sherpa_onnx  # noqa
            return True
        except Exception:
            return False

    def _load_local(self, vm):
        """加载（或复用）本地音色推理器。"""
        if vm.path in self._local:
            return self._local[vm.path]
        import sherpa_onnx
        cfg = vm.info()
        tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=vm.model,
                    lexicon=vm.lexicon,
                    tokens=vm.tokens,
                    dict_dir=vm.rules if os.path.isdir(vm.rules) else vm.path,
                    noise_scale=cfg["noise_scale"],
                    noise_scale_w=cfg["noise_scale_w"],
                    length_scale=cfg["length_scale"],
                ),
                provider="cpu", num_threads=4, debug=False,
            ),
            max_num_sentences=1,
        ))
        self._local[vm.path] = tts
        return tts

    def synth_local(self, vm, text, wav_path, speed=1.0):
        tts = self._load_local(vm)
        audio = tts.generate(text, sid=0, speed=speed)
        import struct
        import wave as _w
        sr = audio.sample_rate
        with _w.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            frames = bytearray()
            for s in audio.samples:
                v = int(max(-1.0, min(1.0, s)) * 32767)
                frames += struct.pack("<h", v)
            w.writeframes(bytes(frames))
        return wav_path

    # -- edge-tts --
    def _edge_ready(self):
        if self._edge is not None or self._edge_failed:
            return self._edge is not None
        try:
            import edge_tts  # noqa
            self._edge = edge_tts
            return True
        except Exception:
            self._edge_failed = True
            return False

    def edge_voices(self):
        """联网拉 edge-tts 中文音色列表。"""
        if not self._edge_ready():
            return []
        try:
            import asyncio
            import edge_tts
            vs = asyncio.run(edge_tts.list_voices())
            return [v["ShortName"] for v in vs if v.get("Locale", "").startswith("zh-CN")]
        except Exception:
            return []

    def synth_edge(self, text, voice, out_path, rate="+0%", volume="+0%", pitch="+0Hz"):
        import asyncio
        import edge_tts

        async def run():
            comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
            await comm.save(out_path)

        last = None
        for _ in range(3):
            try:
                asyncio.run(run())
                if os.path.isfile(out_path) and os.path.getsize(out_path) > 512:
                    return out_path
            except Exception as e:      # 网络抖动重试
                last = e
                time.sleep(1.2)
        raise RuntimeError(f"edge-tts 合成失败：{last}")

    # -- 系统 SAPI（离线） --
    def synth_sapi(self, text, out_path, rate=0, voice=None):
        """用 PowerShell 调 System.Speech 合成 wav（离线）。"""
        ps = os.path.join(tempfile.gettempdir(), f"_sapi_{uuid.uuid4().hex}.ps1")
        rate = max(-10, min(10, int(rate)))
        script = f'''
$t = @"
{text}
"@
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioFormatWave], 128, 22, 44100, 1, 32000)
$s.Rate = {rate}
$s.SetOutputToWaveFile("{out_path}", $fmt)
$s.Speak($t)
$s.Dispose()
'''
        with open(ps, "w", encoding="utf-8-sig") as f:
            f.write(script)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", ps],
                capture_output=True, timeout=90,
            )
            if not os.path.isfile(out_path):
                raise RuntimeError("系统语音合成失败：" + r.stderr.decode("utf-8", "ignore")[-300:])
            return out_path
        finally:
            try:
                os.remove(ps)
            except OSError:
                pass

    # -- 统一入口 --
    def synth(self, text, voice, wav_path, rate_pct=0, sapi_rate=0,
              temperature=0.7):
        """rate_pct: 变速百分比。temperature: Qwen 克隆音色多样性（0.1~1.0）。"""
        # ① Qwen3-TTS 自定义克隆音色（.pt）
        if voice.startswith("qwen:"):
            qe = QWEN_ENGINE
            return qe.synth(voice[5:], text, wav_path, temperature=temperature)

        # ② 本地 VITS 音色目录
        if voice.startswith("local:"):
            if not self.local_ready():
                raise RuntimeError("没装 sherpa-onnx，无法使用本地音色。\n"
                                   "安装：pip install sherpa-onnx")
            vm = VoiceModel(voice[6:])
            if not vm.ok:
                raise RuntimeError(f"音色目录不完整（缺 model.onnx / tokens.txt / lexicon.txt）：{vm.path}")
            speed = max(0.5, min(2.0, 1.0 + rate_pct / 100.0))
            return self.synth_local(vm, text, wav_path, speed=speed)

        if voice == "sys":
            return self.synth_sapi(text, wav_path, rate=sapi_rate)
        if not self._edge_ready():
            raise RuntimeError("edge-tts 未安装，无法使用在线音色。\n"
                               "安装：pip install edge-tts\n或改选「系统语音」离线引擎。")
        rate = f"{rate_pct:+d}%"
        return self.synth_edge(text, voice, wav_path, rate=rate)


# ---------------------------------------------------------------- 语料库

# 分类 -> [(文件名, 台词), ...]  参考露露包的实际命名与用词
CORPUS = {
    "大秘境开场结束": [
        ("1拉", "一号怪拉住了"),
        ("1组准备", "一组准备"),
        ("1分担", "一分摊"),
        ("1打断准备", "打断准备"),
        ("1圈减伤", "一圈，注意减伤"),
        ("2拉", "二号怪拉住了"),
        ("2组准备", "二组准备"),
        ("2分担", "二分摊"),
        ("2打断准备", "打断准备"),
        ("3拉", "三号怪拉住了"),
        ("3组准备", "三组准备"),
        ("3打断准备", "打断准备"),
        ("转阶段", "转阶段了"),
        ("小怪清完", "小怪清完了"),
        ("BOSS击杀", "BOSS 打掉了"),
        ("限时剩5分", "还剩五分钟"),
        ("限时剩3分", "只剩三分钟"),
        ("限时剩1分", "最后一分钟"),
        ("钥匙升级", "钥匙升级了"),
        ("钥匙碎了", "钥匙碎了"),
        ("灭团", "灭团了，重来"),
    ],
    "通用机制提示": [
        ("集合", "集合分散"),
        ("分散", "分散站位"),
        ("停手", "停手，别走火"),
        ("AOE", "小怪聚好，AOE"),
        ("躲技能", "躲开地面技能"),
        ("驱散", "需要驱散"),
        ("打断", "打断好了"),
        ("打断成功", "打断成功"),
        ("减伤", "开减伤"),
        ("爆发", "全力爆发"),
        ("转火", "快转火"),
        ("斩杀", "斩杀"),
        ("免疫", "免疫"),
        ("格挡", "格挡"),
        ("滚开", "快滚开"),
        ("嘲讽", "接怪，嘲讽"),
        ("救人", "救人"),
        ("奶量", "奶量不够"),
        ("战复", "给战复"),
        ("结束", "结束了"),
    ],
    "职业语音": [
        ("dk罩子", "DK 罩子"),
        ("dk setup", "DK 起手"),
        (" interrupts", "打断"),
        ("link", "Link 准备"),
        ("link已插", "Link 已插"),
        ("神器", "神器就绪"),
        ("血脉", "血脉"),
        ("盾", "开盾"),
        ("圣疗", "神圣治疗"),
        ("圣愈", "神圣愈"),
        ("敲钟", "敲钟"),
        ("翅膀", "开翅膀"),
        ("牺牲", "牺牲"),
        ("美德", "美德"),
        ("盘魂", "盘魂者"),
        ("剑", "剑"),
        ("爆发药", "爆发药"),
        ("无敌", "无敌"),
        ("不灭", "不灭"),
        ("压制", "压制"),
        ("保护", "保护"),
        ("圣佑", "圣佑"),
        ("先祖", "先祖"),
        ("升腾", "升腾"),
        ("化身", "化身"),
        ("光焰", "光焰"),
        ("冰箱", "冰箱"),
        ("冰封之韧", "冰封之韧"),
    ],
    "装备饰品提示": [
        ("饰品触发", "饰品触发"),
        ("水壶", "水壶好了"),
        ("无底袋", "无底袋"),
        ("甲壳结界", "甲壳结界"),
        ("束风", "束风"),
        ("海石", "海石"),
        ("仪式容器", "仪式容器"),
        ("枢纽神像", "枢纽神像"),
    ],
}

CORPUS_META = {
    "大秘境开场结束": "M+ 路线节奏、计时、钥匙状态",
    "通用机制提示": "躲避、驱散、打断、转火、爆发的通用喊话",
    "职业语音": "关键天赋/技能就绪播报（对应 Rurutia_SA）",
    "装备饰品提示": "饰品触发播报（对应 RurutiaSA_Trinkets）",
}


def corpus_as_text(cat):
    """把分类转成 '文件名|台词' 每行一条的文本。cat 传分类名或 [(名,词)] 列表。"""
    items = cat if isinstance(cat, (list, tuple)) else CORPUS.get(cat, [])
    return "\n".join(f"{k}|{v}" for k, v in items)


# ---------------------------------------------------------------- 批量生成

class Job:
    def __init__(self, items, voice, ff, out_dir, rate_pct=0, sapi_rate=0, tag="",
                 voice_tag="", temperature=0.7):
        self.items = items           # [(name, text), ...]
        self.voice = voice
        self.ff = ff
        self.out_dir = out_dir
        self.rate_pct = rate_pct
        self.sapi_rate = sapi_rate
        self.tag = tag              # 用户自定义前缀
        self.voice_tag = voice_tag  # 自动加的音色标识，如「君仪2」
        self.temperature = temperature
        self.lock = threading.Lock()
        self.done = []                # 已完成 name
        self.failed = []              # [(name, err)]
        # 先置 True 再返回，避免调用方在子线程启动前读到 False 而误判"已结束"
        self.running = True
        self.started = False
        self.cancel = False
        self.current = ""
        self.t0 = time.time()

    def snapshot(self):
        with self.lock:
            done_n = len(self.done)
            fail_n = len(self.failed)
            total = len(self.items)
            eta = None
            # 按已耗时估算剩余秒数（至少跑完 1 条才有意义）
            if self.started and (done_n + fail_n) > 0 and (done_n + fail_n) < total:
                elapsed = time.time() - self.t0
                per = elapsed / (done_n + fail_n)
                eta = int(per * (total - done_n - fail_n))
            return {
                "total": total,
                "done": list(self.done),
                "failed": [{"name": n, "err": e} for n, e in self.failed],
                "running": self.running,
                "started": self.started,
                "cancel": self.cancel,
                "current": self.current,
                "pending": total - done_n - fail_n,
                "eta_sec": eta,
                "elapsed": int(time.time() - self.t0),
            }


def run_job(job, tts, progress_cb):
    """同步执行批量生成（由后台线程调用）。"""
    tmpdir = tempfile.mkdtemp(prefix="wowvoice_")
    job.started = True
    try:
        for idx, (name, text) in enumerate(job.items, 1):
            if job.cancel:
                break
            # 文件名 = 自定义前缀 + 音色标识 + _ + 原名
            # ★ 必须有下划线分隔，否则「君仪2」+「1分担」会粘成「君仪21分担」，
            #   前端/打包都无法再拆出音色分组
            parts = [p for p in (job.tag, job.voice_tag) if p]
            fname = safe_filename("_".join(parts) + ("_" if parts else "") + name,
                                  f"v{idx:03d}")
            with job.lock:
                job.current = f"[{idx}/{len(job.items)}] {fname}"
            progress_cb(job.snapshot())
            wav = os.path.join(tmpdir, fname + ".wav")
            ogg = os.path.join(job.out_dir, fname + ".ogg")
            try:
                if os.path.exists(ogg) and os.path.getsize(ogg) > 512:
                    with job.lock:
                        job.done.append(fname + ".ogg")
                    continue
                tts.synth(text, job.voice, wav,
                          rate_pct=job.rate_pct, sapi_rate=job.sapi_rate,
                          temperature=job.temperature)
                job.ff.to_ogg(wav, ogg)
                with job.lock:
                    job.done.append(fname + ".ogg")
            except Exception as e:                        # 单条失败不中断
                with job.lock:
                    job.failed.append((name, str(e)[:200]))
            finally:
                for p in (wav,):
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
            progress_cb(job.snapshot())
    finally:
        job.running = False
        job.current = ""
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
    progress_cb(job.snapshot())


def parse_lines(text):
    """
    解析 '文件名|台词' 文本。兼容三种写法：
      文件名|要说的话
      文件名=要说的话
      纯台词              （自动命名 v001、v002…）

    容错：整段只有逗号/换行但没有分隔符时，按逗号拆成多条，
    避免把整个分类当成一条念完。
    """
    text = text or ""
    out = []

    # 情况 A：正常多行
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not l.startswith("#")]
    has_sep = any(("|" in l or "=" in l) for l in lines)

    if not lines:
        return []

    if has_sep:
        for i, line in enumerate(lines, 1):
            if "|" in line:
                k, v = line.split("|", 1)
            elif "=" in line:
                k, v = line.split("=", 1)
            else:
                k, v = f"v{i:03d}", line
            k, v = k.strip(), v.strip()
            if v:
                out.append((safe_filename(k, f"v{i:03d}"), v))
        return out

    # 情况 B：单行/整段，无分隔符 —— 按逗号拆（兼容旧版逗号语料）
    blob = "，".join(lines) if len(lines) > 1 else lines[0]
    parts = [p.strip() for p in blob.replace("，", ",").split(",")]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        # 偶数个片段才按「名,词」成对；否则整体当纯台词
        if len(parts) % 2 == 0:
            for i in range(0, len(parts), 2):
                out.append((safe_filename(parts[i], f"v{i//2+1:03d}"), parts[i + 1]))
        else:
            for i, p in enumerate(parts, 1):
                out.append((safe_filename(p, f"v{i:03d}"), p))
        return out

    for i, line in enumerate(lines, 1):
        out.append((safe_filename(f"v{i:03d}"), line))
    return out


# ---------------------------------------------------------------- 自定义音色

# 自动扫描的音色根目录（相对/绝对皆可；不存在则跳过）
_VOICE_ROOTS = [
    r"L:\工作流\VoicePackMaker\export",
    r"L:\工作流\VoicePackMaker\vits\VITS-fast-fine-tuning-main\EXPORT_MODEL_Q2",
    r"L:\工作流\vits_train\export",
]


class VoiceModel:
    """一个本地自定义音色（sherpa-onnx VITS 目录）。"""

    def __init__(self, path, name=None):
        self.path = path
        self.name = name or os.path.basename(path.rstrip("\\/"))
        self.model = os.path.join(path, "model.onnx")
        # 兼容 model.onnx / junyi-vits.onnx 等各种命名
        if not os.path.isfile(self.model):
            for fn in os.listdir(path) if os.path.isdir(path) else []:
                if fn.lower().endswith(".onnx") and "model" not in fn.lower():
                    self.model = os.path.join(path, fn)
                    break
        self.tokens = os.path.join(path, "tokens.txt")
        self.lexicon = os.path.join(path, "lexicon.txt")
        self.rules = os.path.join(path, "rules")
        self.config = os.path.join(path, "config.yaml")

    @property
    def ok(self):
        return os.path.isfile(self.model) and os.path.isfile(self.tokens) \
            and os.path.isfile(self.lexicon)

    def info(self):
        """读 config.yaml 拿推理参数；没有就用 sherpa 默认值。"""
        # 音色调高/调低（仅本地 VITS 有效）：noise_scale_w
        d = {"noise_scale": 0.667, "noise_scale_w": 0.8,
             "length_scale": 1.0, "sample_rate": None, "speed": 1.0}
        try:
            import re
            txt = open(self.config, "r", encoding="utf-8").read()
            m = re.search(r'"modelDir".*?\}', txt, re.S)
            for k in list(d):
                mm = re.search(rf'"{k}"\s*:\s*([0-9.]+)', txt)
                if mm:
                    d[k] = float(mm.group(1))
            mm = re.search(r"sampleRate:\s*(\d+)", txt)
            if mm:
                d["sample_rate"] = int(mm.group(1))
        except Exception:
            pass
        return d

    def as_dict(self):
        return {
            "id": "local:" + self.path,
            "name": self.name + "（本地音色）",
            "engine": "local",
            "path": self.path,
            "ok": self.ok,
            "info": self.info(),
        }


def scan_voices(extra_roots=""):
    """扫描本机自定义音色。返回 VoiceModel 列表。"""
    roots = list(_VOICE_ROOTS)
    if extra_roots:
        roots = [r.strip() for r in extra_roots.replace("|", ";").split(";") if r.strip()] + roots
    found, seen = [], set()
    for root in roots:
        root = os.path.expandvars(root)
        if not os.path.isdir(root):
            continue
        # root 本身可能是音色目录
        cand = [root]
        try:
            for n in sorted(os.listdir(root)):
                p = os.path.join(root, n)
                if os.path.isdir(p):
                    cand.append(p)
        except OSError:
            continue
        for p in cand:
            rp = os.path.abspath(p)
            if rp in seen:
                continue
            vm = VoiceModel(p)
            if vm.ok:
                seen.add(rp)
                found.append(vm)
    return found


# ---------------------------------------------------------------- Qwen3-TTS 音色克隆

# Qwen3-TTS 工程根目录与模型
_QWEN_ROOT = r"L:\工作流\Qwen3-TTS-v260128-fix"
_QWEN_PY = os.path.join(_QWEN_ROOT, "walkingwithai", "python.exe")
_QWEN_MODEL = os.path.join(_QWEN_ROOT, "models", "Qwen3-TTS-12Hz-1.7B-Base")
_QWEN_VOICES = os.path.join(_QWEN_ROOT, "saved_voices")


def qwen_available():
    return os.path.isfile(_QWEN_PY) and os.path.isdir(_QWEN_MODEL)


def scan_qwen_voices():
    """扫描 Qwen3-TTS 的 .pt 音色。返回 [{id,name,path}]。"""
    out = []
    if not os.path.isdir(_QWEN_VOICES):
        return out
    try:
        for fn in sorted(os.listdir(_QWEN_VOICES)):
            if fn.lower().endswith(".pt"):
                base = os.path.splitext(fn)[0]
                out.append({
                    "id": "qwen:" + os.path.join(_QWEN_VOICES, fn),
                    "name": base + "（Qwen3 克隆音色）",
                    "engine": "qwen",
                    "path": os.path.join(_QWEN_VOICES, fn),
                    "ok": True,
                })
    except OSError:
        pass
    return out


# 子进程脚本：模型加载慢（4.3G 权重），必须常驻复用，不能每条重载
_QWEN_WORKER = r'''
# -*- coding: utf-8 -*-
"""Qwen3-TTS 常驻工作进程：stdin 收 JSON 行，stdout 回 JSON 行。"""
import os, sys, json, wave
ROOT = r"%(root)s"
os.environ.setdefault("HF_HOME", os.path.join(ROOT, "models"))
os.environ.setdefault("TORCH_HOME", os.path.join(ROOT, "models"))
os.environ.setdefault("KERAS_BACKEND", "torch")
os.environ.setdefault("PYANNOTE_CACHE", os.path.join(ROOT, "models", "pyannote"))
os.environ.setdefault("SOX_PATH", os.path.join(ROOT, "walkingwithai", "sox"))
os.environ.setdefault("FFMPEG_PATH", os.path.join(ROOT, "walkingwithai", "ffmpeg", "bin"))
_sox = os.environ["SOX_PATH"]
_ffm = os.environ["FFMPEG_PATH"]
if os.path.isdir(_sox) and _sox not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _sox + os.pathsep + _ffm + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, ROOT)

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel
from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem as VCPI
torch.serialization.add_safe_globals([VCPI])

MODEL = os.path.join(ROOT, "models", "Qwen3-TTS-12Hz-1.7B-Base")
tts = Qwen3TTSModel.from_pretrained(MODEL, device_map="cuda", dtype=torch.bfloat16)
_voice_cache = {}


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


emit({"ready": True, "cuda": True})

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            emit({"ok": True})
            break
        vp = req["voice"]
        if vp not in _voice_cache:
            _voice_cache[vp] = torch.load(vp, map_location="cpu", weights_only=True)
        items = _voice_cache[vp]
        wavs, sr = tts.generate_voice_clone(
            text=req["text"], language=req.get("language", "Chinese"),
            voice_clone_prompt=items,
            temperature=float(req.get("temperature", 0.7)),
            top_p=float(req.get("top_p", 0.9)),
            top_k=int(req.get("top_k", 50)),
            repetition_penalty=float(req.get("repetition_penalty", 1.1)),
        )
        arr = np.clip(np.asarray(wavs[0]), -1, 1)
        out = req["out"]
        with wave.open(out, "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
            f.writeframes((arr * 32767).astype(np.int16).tobytes())
        emit({"ok": True, "sr": sr, "dur": len(arr) / float(sr)})
    except Exception as e:
        emit({"ok": False, "err": "%%s: %%s" %% (type(e).__name__, e)})
'''


class QwenEngine:
    """Qwen3-TTS 常驻工作进程管理器（跨请求复用模型，避免重复加载）。"""

    def __init__(self):
        self.proc = None
        self.tmp_script = None

    def _ensure_script(self):
        if self.tmp_script and os.path.isfile(self.tmp_script):
            return self.tmp_script
        p = os.path.join(tempfile.gettempdir(), "_qwen_tts_worker.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_QWEN_WORKER % {"root": _QWEN_ROOT})
        self.tmp_script = p
        return p
    def start(self, timeout=300):
        """拉起工作进程，等 ready。"""
        if self.proc and self.proc.poll() is None:
            return True
        if not qwen_available():
            raise RuntimeError(f"找不到 Qwen3-TTS 工程：{_QWEN_ROOT}")
        script = self._ensure_script()
        env = dict(os.environ)
        # ★ 必须把 SoX / ffmpeg 加进 PATH：Qwen3-TTS 的音频处理依赖它们
        env["HF_HOME"] = os.path.join(_QWEN_ROOT, "models")
        env["TORCH_HOME"] = os.path.join(_QWEN_ROOT, "models")
        env["KERAS_BACKEND"] = "torch"
        env["PYANNOTE_CACHE"] = os.path.join(_QWEN_ROOT, "models", "pyannote")
        sox = os.path.join(_QWEN_ROOT, "walkingwithai", "sox")
        ffmpeg = os.path.join(_QWEN_ROOT, "walkingwithai", "ffmpeg", "bin")
        env["SOX_PATH"] = sox
        env["FFMPEG_PATH"] = ffmpeg
        env["PATH"] = sox + os.pathsep + ffmpeg + os.pathsep + env.get("PATH", "")
        # stderr 落盘，便于诊断工作进程崩溃原因
        logf = os.path.join(tempfile.gettempdir(), "_qwen_worker_err.log")
        err = open(logf, "a", encoding="utf-8", buffering=1)
        self.proc = subprocess.Popen(
            [_QWEN_PY, "-s", "-u", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=err, text=True, encoding="utf-8",
            env=env, cwd=_QWEN_ROOT, bufsize=1,
        )
        self._errlog = logf
        t0 = time.time()
        while time.time() - t0 < timeout:
            line = self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("ready"):
                return True
        raise RuntimeError("Qwen3-TTS 启动超时（模型加载可能超过 5 分钟），"
                           f"详见 {logf}")

    def synth(self, voice_path, text, wav_path, temperature=0.5):
        # 首次调用会在工作线程里阻塞等待模型加载（约 20~40 秒）
        if not self.start():
            raise RuntimeError("Qwen3-TTS 未就绪")
        if self.proc.poll() is not None:
            raise RuntimeError("Qwen3-TTS 进程已退出，请看 "
                               + getattr(self, "_errlog", "日志"))
        req = {
            "cmd": "synth", "voice": voice_path, "text": text,
            "out": wav_path, "language": "Chinese",
            "temperature": temperature,
        }
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("Qwen3-TTS 进程已退出，请看 "
                               + getattr(self, "_errlog", "日志"))
        msg = json.loads(line)
        if not msg.get("ok"):
            raise RuntimeError("Qwen3-TTS 合成失败：" + str(msg.get("err"))[:300])
        return wav_path

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write('{"cmd":"quit"}\n')
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None


# 全局单例：模型常驻，避免每条语音重载 4.3G 权重
QWEN_ENGINE = QwenEngine()


# ---------------------------------------------------------------- 打包插件

def check_lsm_installed(addons_dir):
    """检查 AddOns 里是否已有 LibSharedMedia-3.0（直接或被整合包内置）。"""
    if not addons_dir or not os.path.isdir(addons_dir):
        return False
    try:
        for n in os.listdir(addons_dir):
            nl = n.lower()
            if "libsharedmedia" in nl:
                return True
        # 也可能塞在某个整合包内部
        for n in os.listdir(addons_dir):
            sub = os.path.join(addons_dir, n, "LibSharedMedia-3.0")
            if os.path.isdir(sub):
                return True
    except OSError:
        pass
    return False


def voice_key_of(filename):
    """从 ogg 文件名反推音色标识（与 group_oggs_by_voice 同一套规则）。"""
    base = os.path.splitext(os.path.basename(filename))[0]
    core = base
    m = re.match(r"^【[^】]*】(.*)$", base)
    if m:
        core = m.group(1)
    m2 = re.match(r"^(.+?)[_.](.+)$", core)
    return m2.group(1) if m2 else ""


def group_oggs_by_voice(src_dir):
    """
    扫描 src_dir 下的 ogg，按「音色标识」分组。

    文件名形如 【我】君仪2_1分担.ogg
        → 组 key = "君仪2"，条目名 = "1分担"
    没有标识的（如 【我】1分担.ogg）归到 "" 组。

    返回 {组名: [(条目名, 文件名), ...]}，按组名字典序。
    """
    groups = {}
    if not os.path.isdir(src_dir):
        return groups
    for fn in sorted(os.listdir(src_dir)):
        if not fn.lower().endswith(".ogg"):
            continue
        base = os.path.splitext(fn)[0]
        # 剥掉自定义前缀 【xxx】
        core = base
        m = re.match(r"^【[^】]*】(.*)$", base)
        if m:
            core = m.group(1)
        # 再剥音色标识（到第一个 _ 或 . 为止）
        gkey, item = "", core
        m2 = re.match(r"^(.+?)[_.](.+)$", core)
        if m2:
            gkey, item = m2.group(1), m2.group(2)
        groups.setdefault(gkey, []).append((item, fn))
    return groups


def build_package(cfg, out_dir, package=None, display=None, subdirs=None,
                  src_dir=None, log=None, group_by_voice=False,
                  only_voices=None):
    """
    把生成的 ogg 打成标准 SharedMedia 插件包。

    out_dir  : 语音产出根目录（即 output/）
    src_dir  : 实际读取 ogg 的目录，默认 == out_dir
    subdirs  : {包内子目录名: 源子目录名(相对 src_dir)}，默认 {"media": ""}
    group_by_voice : True 时按音色标识拆子目录 + 显示名加「音色/」前缀
    only_voices : 只打包这些音色（None/空 = 全部）。值为音色标识列表，
                  如 ["君仪2"] 或 ["君仪2", "未来1"]；[""] 表示无标识那批
    返回 (包目录, 条数)。
    """
    package = package or cfg.get("package") or "SharedMedia_WoWVoice"
    display = display or cfg.get("display_name") or "我的语音包"
    package = safe_filename(package, "SharedMedia_WoWVoice")
    log = log or (lambda m: None)
    src_dir = src_dir or out_dir
    subdirs = subdirs or {"media": ""}

    addons = cfg.get("wow_addons_dir") or detect_wow_addons()
    if not addons or not os.path.isdir(addons):
        raise RuntimeError("找不到 WoW 的 Interface\\AddOns 目录，请在设置里手动指定")
    pdir = os.path.join(addons, package)
    if os.path.isdir(pdir):
        log(f"目标已存在，将覆盖：{pdir}")
        # 注意：不做整目录删除 —— 批量删文件会被安全策略拦截，直接把服务进程杀掉。
        # MyMedia.lua 每次都从本次 entries 重新生成，所以上一版遗留的 ogg
        # 不会出现在注册表里（只是白占磁盘，用户想清就手动删或换个包名）。
    else:
        os.makedirs(pdir, exist_ok=True)

    entries = []           # (显示名, 包内子目录, 文件名)

    # only_voices: None/空 = 全部；否则只打包指定音色
    only = None
    if only_voices:
        only = set(only_voices)
        if "" in only and len(only) > 1:
            only.discard("")   # 空串单独传才表示"只要无标识那批"

    if group_by_voice:
        # 按音色拆：media/君仪2/xxx.ogg + 显示名 "君仪2/xxx"
        for gkey, items in sorted(group_oggs_by_voice(src_dir).items()):
            if only is not None and gkey not in only:
                continue
            dest_sub = "media/" + safe_filename(gkey, "默认") if gkey else "media"
            mdir = os.path.join(pdir, dest_sub)
            os.makedirs(mdir, exist_ok=True)
            for item, fn in items:
                shutil.copy2(os.path.join(src_dir, fn), os.path.join(mdir, fn))
                disp = f"{gkey}/{item}" if gkey else item
                entries.append((disp, dest_sub, fn))
    else:
        # 平铺模式：也要按音色过滤
        for dest_sub, src_sub in subdirs.items():
            sdir = os.path.join(src_dir, src_sub) if src_sub else src_dir
            if not os.path.isdir(sdir):
                continue
            mdir = os.path.join(pdir, dest_sub)
            os.makedirs(mdir, exist_ok=True)
            for fn in sorted(os.listdir(sdir)):
                if not fn.lower().endswith(".ogg"):
                    continue
                if only is not None and voice_key_of(fn) not in only:
                    continue
                shutil.copy2(os.path.join(sdir, fn), os.path.join(mdir, fn))
                base = os.path.splitext(fn)[0]
                entries.append((base, dest_sub, fn))

    if not entries:
        if only is not None:
            raise RuntimeError("勾选的音色下没有 .ogg 文件，换个音色或先取消筛选")
        raise RuntimeError(f"{src_dir} 里没有 .ogg 文件，先生成语音")

    # MyMedia.lua
    # 用 LibStub(..., true) 做保护：LibSharedMedia-3.0 未加载时静默跳过，
    # 避免插件列表里刷红字。Dependencies 只做「可选加载」声明，不写死
    # SharedMedia（很多用户装的是独立的 LibSharedMedia-3.0 插件）。
    lines = [
        'local LSM = LibStub("LibSharedMedia-3.0", true)',
        'if not LSM then return end',
        "",
        "-- Generated by WoW语音包工坊",
        "",
        "-- SOUND",
    ]
    for disp, sub, fn in entries:
        disp = disp.replace('"', "'")
        # 统一用反斜杠：WoW 的路径习惯，混用正斜杠部分客户端会找不到文件
        path = f"Interface\\AddOns\\{package}\\{sub.replace('/', chr(92))}\\{fn}"
        lines.append(f'LSM:Register("sound", "{disp}", [[{path}]])')
    with open(os.path.join(pdir, "MyMedia.lua"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # toc
    iface = cfg.get("interface_version") or DEFAULT_CONFIG["interface_version"]
    toc = f"""## Interface: {iface}
## Title: |cff55ff55{display}|r
## Title-zhCN: |cff55ff55[{display}]|r 语音包
## DefaultState: enabled
## Author: {cfg.get('author_tag', '我').strip('【】') or '我'}
## Version: {APP_VERSION}
## Notes: 由 WoW语音包工坊生成|n共 {len(entries)} 条语音。|n需要 LibSharedMedia-3.0（多数整合包已内置）。
## LoadOnDemand: 1
## X-CurseForge-Project-ID: 0
MyMedia.lua
"""
    with open(os.path.join(pdir, package + ".toc"), "w", encoding="utf-8") as f:
        f.write(toc)

    log(f"打包完成：{pdir}（{len(entries)} 条）")
    if not check_lsm_installed(addons):
        log("⚠ AddOns 里没找到 LibSharedMedia-3.0，语音不会出现在插件设置里。"
            "请装 LibSharedMedia-3.0（或用整合包内置的版本）")
    return pdir, len(entries)


def package_to_zip(pdir, zip_path):
    import zipfile
    base = os.path.basename(pdir)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, files in os.walk(pdir):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, os.path.dirname(pdir))
                z.write(full, rel)
    return zip_path
