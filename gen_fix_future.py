# -*- coding: utf-8 -*-
"""
定点重做脚本：只把「未来1」版 6 个已反馈问题的文件重合成，
并重写到 build 目录 + 本机 AddOns 目录（文件名不变、仅改 TTS 输入文本/模式）。
修复对照（均走 XV 仅说话人向量，保证短句说得清、不吞字）：
  去消DEBUFF      -> 文本 "去消迪巴夫"（DEBUFF 音译连读，原字母式断读）
  1A减伤          -> 文本 "一A减伤"（字母数字串连读）
  中毒点你        -> 文本 "中毒点你"（保持短语，XV 防断句）
  剩余2次         -> 文本 "剩余两次"（量词前 2->两）
  变！身！        -> 文本 "变身！"（去掉中间标点断句）
  准备转火小怪    -> 文本 "准备转火小怪"（XV 防快语速吞前半）
"""
import os, re, json, subprocess, tempfile, time, wave, array, shutil

QWEN_ROOT = r"L:\工作流\Qwen3-TTS-v260128-fix"
QWEN_PY   = os.path.join(QWEN_ROOT, "walkingwithai", "python.exe")
FFMPEG    = r"L:\工作流\小工具\MultiTTS-VoiceMaker\runtime\ffmpeg\bin\ffmpeg.exe"
SRC       = r"D:\暴雪\World of Warcraft\_retail_\Interface\AddOns\SharedMedia_Rurutia"
BUILD     = r"L:\工作流\小工具\WoW语音包工坊\build"
ADDONS    = r"D:\暴雪\World of Warcraft\_retail_\Interface\AddOns"
TEMP      = tempfile.gettempdir()
PEAK_TARGET = -1.5
VNAME = "未来1"
PT = r"L:\工作流\Qwen3-TTS-v260128-fix\saved_voices\未来1.pt"

# 文件名(含【露露】与扩展名) -> (TTS文本, xv)
OVERRIDES = {
    "【露露】去消DEBUFF.ogg":      ("去消迪巴夫",     True),
    "【露露】1A减伤.ogg":          ("一A减伤",       True),
    "【露露】中毒点你.ogg":        ("中毒点你",       True),
    "【露露】剩余2次.ogg":         ("剩余两次",       True),
    "【露露】变！身！.ogg":        ("变身！",         True),
    "【露露】准备转火小怪.ogg":     ("准备转火小怪",   True),
}

# ---------------- Qwen 常驻工作进程 ----------------
WORKER = r'''
# -*- coding: utf-8 -*-
import os, sys, json, wave
ROOT = r"__ROOT__"
for k,v in [("HF_HOME",os.path.join(ROOT,"models")),("TORCH_HOME",os.path.join(ROOT,"models")),
            ("KERAS_BACKEND","torch"),("PYANNOTE_CACHE",os.path.join(ROOT,"models","pyannote")),
            ("SOX_PATH",os.path.join(ROOT,"walkingwithai","sox")),("FFMPEG_PATH",os.path.join(ROOT,"walkingwithai","ffmpeg","bin"))]:
    os.environ.setdefault(k,v)
_sox=os.environ["SOX_PATH"]; _ffm=os.environ["FFMPEG_PATH"]
if os.path.isdir(_sox) and _sox not in os.environ.get("PATH",""):
    os.environ["PATH"]=_sox+os.pathsep+_ffm+os.pathsep+os.environ.get("PATH","")
sys.path.insert(0, ROOT)
import numpy as np, torch
from qwen_tts import Qwen3TTSModel
from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem as VCPI
torch.serialization.add_safe_globals([VCPI])
MODEL=os.path.join(ROOT,"models","Qwen3-TTS-12Hz-1.7B-Base")
tts=Qwen3TTSModel.from_pretrained(MODEL, device_map="cuda", dtype=torch.bfloat16)
_vc={}
def emit(o):
    sys.stdout.write(json.dumps(o,ensure_ascii=False)+"\n"); sys.stdout.flush()
emit({"ready":True})
def build(vp,xv):
    raw=torch.load(vp,map_location="cpu",weights_only=True)
    if not xv: return raw
    it=raw[0]
    return [VCPI(ref_code=None,ref_spk_embedding=it.ref_spk_embedding,x_vector_only_mode=True,icl_mode=False,ref_text=None)]
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    try:
        req=json.loads(line)
        if req.get("cmd")=="quit": emit({"ok":True}); break
        vp=req["voice"]; xv=bool(req.get("xv",False)); key=(vp,xv)
        if key not in _vc: _vc[key]=build(vp,xv)
        wavs,sr=tts.generate_voice_clone(text=req["text"],language="Chinese",
            voice_clone_prompt=_vc[key],temperature=0.5,top_p=0.9,top_k=50,repetition_penalty=1.1)
        arr=np.clip(np.asarray(wavs[0]),-1,1)
        with wave.open(req["out"],"wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
            f.writeframes((arr*32767).astype(np.int16).tobytes())
        emit({"ok":True,"dur":len(arr)/float(sr)})
    except Exception as e:
        emit({"ok":False,"err":"%s: %s"%(type(e).__name__,e)})
'''

def start_worker():
    sc=os.path.join(TEMP,"_fix_future_worker.py")
    open(sc,"w",encoding="utf-8").write(WORKER.replace("__ROOT__",QWEN_ROOT))
    env=dict(os.environ)
    sox=os.path.join(QWEN_ROOT,"walkingwithai","sox"); ffm=os.path.join(QWEN_ROOT,"walkingwithai","ffmpeg","bin")
    for k,v in [("SOX_PATH",sox),("FFMPEG_PATH",ffm),("HF_HOME",os.path.join(QWEN_ROOT,"models")),
                ("TORCH_HOME",os.path.join(QWEN_ROOT,"models")),("KERAS_BACKEND","torch"),
                ("PYANNOTE_CACHE",os.path.join(QWEN_ROOT,"models","pyannote"))]:
        env[k]=v
    env["PATH"]=sox+os.pathsep+ffm+os.pathsep+env.get("PATH","")
    logf=os.path.join(TEMP,"_fix_future_err.log")
    err=open(logf,"a",encoding="utf-8",buffering=1)
    p=subprocess.Popen([QWEN_PY,"-s","-u",sc],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                       stderr=err,text=True,encoding="utf-8",env=env,cwd=QWEN_ROOT,bufsize=1)
    t0=time.time()
    while time.time()-t0<900:
        ln=p.stdout.readline()
        if not ln: raise RuntimeError("Qwen 进程退出，见 "+logf)
        s=ln.strip()
        if not s: continue
        try:
            if json.loads(s).get("ready"): return p, logf
        except ValueError: continue
    raise RuntimeError("Qwen 响应超时，见 "+logf)

def synth(proc, text, wav, xv):
    req={"cmd":"synth","voice":PT,"text":text,"out":wav,"language":"Chinese","temperature":0.5,"xv":xv}
    proc.stdin.write(json.dumps(req,ensure_ascii=False)+"\n"); proc.stdin.flush()
    t0=time.time()
    while time.time()-t0<900:
        ln=proc.stdout.readline()
        if not ln: raise RuntimeError("Qwen 进程退出")
        s=ln.strip()
        if not s: continue
        try:
            m=json.loads(s)
        except ValueError: continue
        if not m.get("ok"): raise RuntimeError(str(m.get("err"))[:200])
        return m
    raise RuntimeError("响应超时")

def volpeak(wav):
    r=subprocess.run([FFMPEG,"-hide_banner","-i",wav,"-af","volumedetect","-f","null","-"],
                     capture_output=True,text=True,timeout=60)
    for ln in r.stderr.splitlines():
        if "max_volume" in ln:
            try: return float(ln.split(":")[1].split("dB")[0].strip())
            except: pass
    return 0.0

def to_ogg(wav, ogg, gain=0.0):
    filt="aresample=44100"
    if gain: filt+=",volume=%.1fdB"%gain
    subprocess.run([FFMPEG,"-y","-hide_banner","-loglevel","error","-i",wav,"-af",filt,
                    "-ac","2","-c:a","libvorbis","-q:a","5",ogg],check=True,timeout=120)

def find_src(fn):
    for sub in ["Rurutia","Rurutia_SA","Rurutia_Extra"]:
        p=os.path.join(SRC,sub,fn)
        if os.path.isfile(p): return sub,p
    return None,None

def main():
    print("启动 Qwen 工作进程...")
    proc, logf = start_worker()
    print("  ✓ 模型就绪")
    done=0
    for fn,(text,xv) in OVERRIDES.items():
        sub,srcpath = find_src(fn)
        if not sub:
            print("  [跳过] 原版未找到 %s"%fn); continue
        wav=os.path.join(TEMP,"_fixf_%s.wav"%abs(hash(fn)))
        try:
            synth(proc, text, wav, xv)
            peak=volpeak(wav)
            gain=max(0.0, PEAK_TARGET-peak)
            # build 目录
            build_path=os.path.join(BUILD,"SharedMedia_Rurutia_"+VNAME,sub,fn)
            os.makedirs(os.path.dirname(build_path),exist_ok=True)
            to_ogg(wav, build_path, gain)
            # AddOns 目录
            addon_path=os.path.join(ADDONS,"SharedMedia_Rurutia_"+VNAME,sub,fn)
            os.makedirs(os.path.dirname(addon_path),exist_ok=True)
            shutil.copy2(build_path, addon_path)
            try: os.remove(wav)
            except OSError: pass
            done+=1
            print("  ✓ %-22s 文本=%-8s xv=%s 增益=%+.1fdB"%(fn,text,xv,gain))
        except Exception as e:
            print("  [ERR] %s: %s"%(fn,str(e)[:150]))
    try: proc.stdin.write('{"cmd":"quit"}\n'); proc.stdin.flush()
    except: pass
    print("\n完成：重做 %d 个文件（未来1版，已同步 build + 本机 AddOns）"%done)

if __name__=="__main__":
    main()
