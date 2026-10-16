# -*- coding: utf-8 -*-
"""
露露语音包 1:1 替换 · 验证生成器（音量归一化 + 倒计时定时 + 未来1改用仅说话人向量模式）
--------------------------------------------------------------------------------
读取 SharedMedia_Rurutia 的原始文件名 → 推导 TTS 文本 → Qwen3-TTS 克隆音色生成，
输出文件名与露露原版完全一致。

修正点：
- 未来1 语速过快（克隆时把参考 UP 主的快语速也学来了，短句/数字被压缩成"说不完"）：
  改用 x_vector_only_mode=True，只克隆音色、语速交给模型自身，短句/数字恢复正常。
- 音量归一化（只提不压）：低于 TARGET_MEAN_DB 才补增益。
- 倒计时定时：文件名是递减数字序列（321 / 54321）时，逐位合成，N 位 = N 秒。
"""
import os, re, sys, json, subprocess, tempfile, time, wave, array, urllib.parse

# ---------------- 路径 ----------------
QWEN_ROOT  = r"L:\工作流\Qwen3-TTS-v260128-fix"
QWEN_PY    = os.path.join(QWEN_ROOT, "walkingwithai", "python.exe")
QWEN_MODEL = os.path.join(QWEN_ROOT, "models", "Qwen3-TTS-12Hz-1.7B-Base")
FFMPEG     = r"L:\工作流\小工具\MultiTTS-VoiceMaker\runtime\ffmpeg\bin\ffmpeg.exe"
SRC        = r"D:\暴雪\World of Warcraft\_retail_\Interface\AddOns\SharedMedia_Rurutia"
# 每个音色可单独指定是否用仅说话人向量模式（xv）
# 默认走 ICL（保留参考音色韵律）；仅对"未来1 的数字"切到 XV（只克隆音色、语速归模型，
# 避免单字/倒计时被压成说不完）。中文全部保持 ICL，保留用户喜欢的未来1 语速。
VOICES = {
    "君仪2": {"pt": r"L:\工作流\Qwen3-TTS-v260128-fix\saved_voices\君仪2.pt", "xv": False},
    "未来1": {"pt": r"L:\工作流\Qwen3-TTS-v260128-fix\saved_voices\未来1.pt", "xv": False},
}
OUT = r"L:\工作流\小工具\WoW语音包工坊\review_validate"
TEMP = tempfile.gettempdir()

TARGET_MEAN_DB = -16.0
MAX_GAIN_DB = 24.0
PEAK_TARGET = -1.5   # 峰值归一化目标(dBFS)；未来1 XV 语音极弱，按峰值拉齐避免切碎

# ---------------- 文件名 → TTS 文本 ----------------
NUMBER_MAP = {'1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七',
              '8':'八','9':'九','10':'十','321':'三二一'}
DIGIT_MAP = str.maketrans('0123456789', '零一二三四五六七八九')
NUMERAL = {0:'零',1:'一',2:'二',3:'三',4:'四',5:'五',6:'六',7:'七',8:'八',9:'九'}

def tts_text(base):
    m = re.match(r'^【[^】]*】(.*)$', base)
    inner = m.group(1) if m else base
    if inner in NUMBER_MAP:
        return NUMBER_MAP[inner]
    return inner.translate(DIGIT_MAP)

def parse_countdown(base):
    m = re.match(r'^【[^】]*】(.*)$', base)
    inner = m.group(1) if m else base
    if not inner.isdigit() or len(inner) < 3:
        return None
    ds = [int(c) for c in inner]
    for i in range(len(ds) - 1):
        if ds[i] - ds[i+1] != 1:
            return None
    return ds

def cjk_count(s):
    return len(re.findall(r'[\u4e00-\u9fff]', s))

# ---------------- Qwen3-TTS 常驻工作进程 ----------------
WORKER = r'''
# -*- coding: utf-8 -*-
import os, sys, json, wave
ROOT = r"%(root)s"
os.environ.setdefault("HF_HOME", os.path.join(ROOT, "models"))
os.environ.setdefault("TORCH_HOME", os.path.join(ROOT, "models"))
os.environ.setdefault("KERAS_BACKEND", "torch")
os.environ.setdefault("PYANNOTE_CACHE", os.path.join(ROOT, "models", "pyannote"))
os.environ.setdefault("SOX_PATH", os.path.join(ROOT, "walkingwithai", "sox"))
os.environ.setdefault("FFMPEG_PATH", os.path.join(ROOT, "walkingwithai", "ffmpeg", "bin"))
_sox = os.environ["SOX_PATH"]; _ffm = os.environ["FFMPEG_PATH"]
if os.path.isdir(_sox) and _sox not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _sox + os.pathsep + _ffm + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, ROOT)
import numpy as np, torch
from qwen_tts import Qwen3TTSModel
from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem as VCPI
torch.serialization.add_safe_globals([VCPI])
MODEL = os.path.join(ROOT, "models", "Qwen3-TTS-12Hz-1.7B-Base")
tts = Qwen3TTSModel.from_pretrained(MODEL, device_map="cuda", dtype=torch.bfloat16)
_voice_cache = {}
def emit(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n"); sys.stdout.flush()
emit({"ready": True, "cuda": True})
def build_prompt(vp, xv):
    raw = torch.load(vp, map_location="cpu", weights_only=True)
    if not xv:
        return raw
    it = raw[0]
    return [VCPI(ref_code=None, ref_spk_embedding=it.ref_spk_embedding,
                 x_vector_only_mode=True, icl_mode=False, ref_text=None)]
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            emit({"ok": True}); break
        vp = req["voice"]; xv = bool(req.get("xv", False))
        key = (vp, xv)
        if key not in _voice_cache:
            _voice_cache[key] = build_prompt(vp, xv)
        items = _voice_cache[key]
        wavs, sr = tts.generate_voice_clone(
            text=req["text"], language=req.get("language", "Chinese"),
            voice_clone_prompt=items,
            temperature=float(req.get("temperature", 0.5)),
            top_p=float(req.get("top_p", 0.9)),
            top_k=int(req.get("top_k", 50)),
            repetition_penalty=float(req.get("repetition_penalty", 1.1)),
        )
        arr = np.clip(np.asarray(wavs[0]), -1, 1)
        with wave.open(req["out"], "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
            f.writeframes((arr * 32767).astype(np.int16).tobytes())
        emit({"ok": True, "sr": sr, "dur": len(arr) / float(sr)})
    except Exception as e:
        emit({"ok": False, "err": "%%s: %%s" %% (type(e).__name__, e)})
'''

def read_json_line(proc, logf):
    t0 = time.time()
    while time.time() - t0 < 600:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("Qwen 工作进程提前退出，见 " + logf)
        s = line.strip()
        if not s:
            continue
        try:
            return json.loads(s)
        except ValueError:
            continue
    raise RuntimeError("Qwen 工作进程响应超时，见 " + logf)

def start_worker():
    script = os.path.join(TEMP, "_qwen_replace_worker.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(WORKER % {"root": QWEN_ROOT})
    env = dict(os.environ)
    sox = os.path.join(QWEN_ROOT, "walkingwithai", "sox")
    ffmpeg = os.path.join(QWEN_ROOT, "walkingwithai", "ffmpeg", "bin")
    for k, v in [("SOX_PATH", sox), ("FFMPEG_PATH", ffmpeg),
                 ("HF_HOME", os.path.join(QWEN_ROOT, "models")),
                 ("TORCH_HOME", os.path.join(QWEN_ROOT, "models")),
                 ("KERAS_BACKEND", "torch"),
                 ("PYANNOTE_CACHE", os.path.join(QWEN_ROOT, "models", "pyannote"))]:
        env[k] = v
    env["PATH"] = sox + os.pathsep + ffmpeg + os.pathsep + env.get("PATH", "")
    logf = os.path.join(TEMP, "_qwen_replace_err.log")
    err = open(logf, "a", encoding="utf-8", buffering=1)
    p = subprocess.Popen([QWEN_PY, "-s", "-u", script],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=err, text=True, encoding="utf-8", env=env,
                         cwd=QWEN_ROOT, bufsize=1)
    msg = read_json_line(p, logf)
    if not msg.get("ready"):
        raise RuntimeError("Qwen 工作进程未返回 ready，见 " + logf)
    return p, logf

def synth(proc, vcfg, text, wav, logf, xv=None):
    if xv is None:
        xv = vcfg["xv"]
    req = {"cmd": "synth", "voice": vcfg["pt"], "text": text, "out": wav,
           "language": "Chinese", "temperature": 0.5, "xv": bool(xv)}
    proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n"); proc.stdin.flush()
    m = read_json_line(proc, logf)
    if not m.get("ok"):
        raise RuntimeError("合成失败：" + str(m.get("err"))[:300])
    return m

def volumedetect_peak(wav):
    """返回峰值(dBFS)。用于峰值归一化（确定性强、零削波）。"""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", wav, "-af", "volumedetect",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=60)
    for ln in r.stderr.splitlines():
        if "max_volume" in ln:
            try:
                return float(ln.split(":")[1].split("dB")[0].strip())
            except Exception:
                pass
    return 0.0

def to_ogg(wav, ogg, gain_db=0.0):
    filt = "aresample=44100"
    if gain_db:
        filt += ",volume=%.1fdB" % gain_db
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", wav,
           "-af", filt, "-ac", "2", "-c:a", "libvorbis", "-q:a", "5", ogg]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0 or not os.path.isfile(ogg):
        raise RuntimeError("ffmpeg 转码失败: " + r.stderr.decode("utf-8", "ignore")[-300:])

def assemble_countdown(digit_wavs, out_wav, slot_sec=1.0):
    w0 = wave.open(digit_wavs[0], "rb")
    sr = w0.getframerate(); w0.close()
    slot = int(slot_sec * sr)
    total = len(digit_wavs) * slot
    out = array.array("h", [0]) * total
    for i, dw in enumerate(digit_wavs):
        w = wave.open(dw, "rb")
        n = w.getnframes(); data = w.readframes(n); w.close()
        samples = array.array("h"); samples.frombytes(data)
        m = min(len(samples), slot)
        out[i * slot: i * slot + m] = samples[:m]
    wo = wave.open(out_wav, "wb")
    wo.setnchannels(1); wo.setsampwidth(2); wo.setframerate(sr)
    wo.writeframes(out.tobytes()); wo.close()

# ---------------- 验证样本 ----------------
NUMBER_BASES = ['1','2','3','4','5','6','7','8','9','10','321','54321']
CN_BASES = ['打断奥术','别出圈','易伤阶段','雷光茶','传给远程',
            '冰墓点你','群体流血','准备吹风','拉线']
PROMPTS = [(b, '数字') for b in NUMBER_BASES] + [(b, '中文') for b in CN_BASES]

# ---------------- 主流程 ----------------
def main():
    os.makedirs(OUT, exist_ok=True)
    for v in VOICES:
        os.makedirs(os.path.join(OUT, v), exist_ok=True)

    print("[1/3] 启动 Qwen3-TTS 工作进程（首次加载约 20~40s）...")
    proc, logf = start_worker()
    print("       ✓ 模型就绪；君仪2=ICL，未来1=仅说话人向量")

    total = len(PROMPTS) * len(VOICES)
    done = 0
    t0 = time.time()
    for base, kind in PROMPTS:
        digits = parse_countdown(base)
        ogg_name = "【露露】%s.ogg" % base
        for vname, vcfg in VOICES.items():
            # 未来1：数字（含单字/倒计时）一律 XV，避免说不完；
            # 中文 ≤4 字也走 XV（短词在 ICL 下易被快语速吞字，如 冰墓点你）；
            # 中文 ≥5 字走 ICL，保留用户喜欢的利落语速与韵律。
            if vname == "未来1":
                if kind == "数字":
                    xv = True
                else:
                    xv = cjk_count(tts_text(base)) <= 4
            else:
                xv = False
            wav = os.path.join(TEMP, "_qg_%s.wav" % abs(hash(base + vname)))
            if digits:
                dwavs = []
                for d in digits:
                    dw = os.path.join(TEMP, "_qg_d%dc%d.wav" % (d, abs(hash(base + vname))))
                    synth(proc, vcfg, NUMERAL[d], dw, logf, xv)
                    dwavs.append(dw)
                assemble_countdown(dwavs, wav, slot_sec=1.0)
                for dw in dwavs:
                    try: os.remove(dw)
                    except OSError: pass
                label = "倒计时%d秒" % len(digits)
            else:
                text = tts_text(base)
                synth(proc, vcfg, text, wav, logf, xv)
                label = text
            # 峰值归一化到 PEAK_TARGET（确定性强、零削波；不裁静音以免切碎弱语音）
            peak = volumedetect_peak(wav)
            gain = max(0.0, PEAK_TARGET - peak)
            ogg = os.path.join(OUT, vname, ogg_name)
            to_ogg(wav, ogg, gain_db=gain)
            try: os.remove(wav)
            except OSError: pass
            done += 1
            print("  [%d/%d] %-12s %-6s 文本=%-8s 原音量=%+.1fdB 增益=%+.1fdB"
                  % (done, total, ogg_name, vname, label, mean, gain))
    try:
        proc.stdin.write('{"cmd":"quit"}\n'); proc.stdin.flush()
    except Exception:
        pass

    rows = []
    for base, kind in PROMPTS:
        digits = parse_countdown(base)
        note = ("倒计时%d秒" % len(digits)) if digits else tts_text(base)
        ogg_name = "【露露】%s.ogg" % base
        a = urllib.parse.quote("君仪2/" + ogg_name)
        b = urllib.parse.quote("未来1/" + ogg_name)
        rows.append((base, note, kind, a, b))
    html = '<!DOCTYPE html><html lang=zh><head><meta charset=utf-8>'
    html += '<title>露露替换 · 双音色 A/B 验证（未来1已修正语速）</title>'
    html += '<style>body{font-family:system-ui,Arial,sans-serif;background:#1e1e2e;color:#eee;margin:0;padding:24px}'
    html += 'h1{font-size:20px}.row{display:flex;align-items:center;gap:14px;padding:9px 0;border-bottom:1px solid #333}'
    html += '.nm{width:130px;font-family:monospace}.tx{width:90px;color:#ffd479}.k{width:48px;color:#888;font-size:12px}'
    html += 'audio{height:30px}</style></head><body>'
    html += '<h1>露露语音包替换 · 双音色 A/B 验证（最终规则版）</h1>'
    html += '<p style="color:#888;font-size:13px">左=君仪2（全程 ICL）。右=未来1（数字 + ≤4字中文走 XV 仅说话人向量，保证说得清；≥5字中文走 ICL 保留你喜欢的利落语速）。文件名与露露原版一致。'
    html += '重点听：未来1 的短词（冰墓点你等）是否完整清楚；长句是否仍是你喜欢的语感。</p>'
    for base, note, kind, a, b in rows:
        html += '<div class=row><div class=nm>%s</div><div class=k>%s</div><div class=tx>%s</div>' % (base, kind, note)
        html += '<audio controls src="%s"></audio><audio controls src="%s"></audio></div>' % (a, b)
    html += '</body></html>'
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print("\n[3/3] 完成！验证包：%s （共 %d 条，A/B 页 index.html）" % (OUT, total))

if __name__ == "__main__":
    main()
