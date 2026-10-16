# -*- coding: utf-8 -*-
"""
露露语音包 1:1 全量替换生成器（生产版）
==================================================================
读取 SharedMedia_Rurutia 原版全部音频文件名 → 推导 TTS 文本 →
Qwen3-TTS 双音色生成（君仪2 / 未来1）→ 产出两套独立可替换包：
    build/SharedMedia_Rurutia_君仪2/
    build/SharedMedia_Rurutia_未来1/
每个包含与原版一致的三级子目录 + 改写路径的 MyMedia.lua / .toc，
EXBoss / WeakAuras 按显示名索引，配置零改动。
（83 个音效/拟声 SFX 原样拷贝；1528 条中文+数字走 TTS。）

音色规则（已与用户确认）：
- 君仪2：全程 ICL。
- 未来1：数字 + ≤4 字中文走 XV（仅说话人向量，保证说得清）；≥5 字中文走 ICL（保留喜欢的语速）。
特性：断点续跑（已存在的 ogg 跳过）。
"""
import os, re, sys, json, subprocess, tempfile, time, wave, array, shutil

QWEN_ROOT  = r"L:\工作流\Qwen3-TTS-v260128-fix"
QWEN_PY    = os.path.join(QWEN_ROOT, "walkingwithai", "python.exe")
FFMPEG     = r"L:\工作流\小工具\MultiTTS-VoiceMaker\runtime\ffmpeg\bin\ffmpeg.exe"
SRC        = r"D:\暴雪\World of Warcraft\_retail_\Interface\AddOns\SharedMedia_Rurutia"
BUILD      = r"L:\工作流\小工具\WoW语音包工坊\build"
TEMP       = tempfile.gettempdir()
TARGET_MEAN_DB = -16.0
MAX_GAIN_DB = 24.0
PEAK_TARGET = -1.5   # 峰值归一化目标(dBFS)；未来1 XV 语音极弱，按峰值拉齐避免切碎

VOICES = {
    "君仪2": r"L:\工作流\Qwen3-TTS-v260128-fix\saved_voices\君仪2.pt",
    "未来1": r"L:\工作流\Qwen3-TTS-v260128-fix\saved_voices\未来1.pt",
}
SUBDIRS = ["Rurutia", "Rurutia_SA", "Rurutia_Extra"]

NUMERAL = {0:'零',1:'一',2:'二',3:'三',4:'四',5:'五',6:'六',7:'七',8:'八',9:'九'}
DIGIT_MAP = str.maketrans('0123456789', '零一二三四五六七八九')

def cjk_count(s):
    return len(re.findall(r'[\u4e00-\u9fff]', s))

def inner_name(fn):
    base = os.path.splitext(fn)[0]
    m = re.match(r'^【[^】]*】(.*)$', base)
    return m.group(1) if m else base

def tts_text(inner):
    if inner in {'1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七','8':'八','9':'九','10':'十'}:
        return {'1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七','8':'八','9':'九','10':'十'}[inner]
    return inner.translate(DIGIT_MAP)

def parse_countdown(inner):
    if not inner.isdigit() or len(inner) < 3:
        return None
    ds = [int(c) for c in inner]
    for i in range(len(ds) - 1):
        if ds[i] - ds[i+1] != 1:
            return None
    return ds

def classify(fn):
    """返回 ('sfx', None) 或 ('tts', {'kind':...}) """
    base = os.path.splitext(fn)[0]
    if base.startswith('!'):
        return ('sfx', None)
    inner = inner_name(fn)
    if inner.isdigit():
        return ('tts', {'kind': 'numeric'})
    if cjk_count(inner) > 0:
        return ('tts', {'kind': 'cn'})
    return ('sfx', None)   # 英文/拟声 (AOE/Bingo/Wakuwaku/SE01..)

# ---------------- Qwen worker ----------------
WORKER = r'''
# -*- coding: utf-8 -*-
import os, sys, json, wave
ROOT = r"%(root)s"
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
        emit({"ok":False,"err":"%%s: %%s"%%(type(e).__name__,e)})
'''

def read_json(proc, logf):
    t0=time.time()
    while time.time()-t0<900:
        ln=proc.stdout.readline()
        if not ln: raise RuntimeError("Qwen 进程退出，见 "+logf)
        s=ln.strip()
        if not s: continue
        try: return json.loads(s)
        except ValueError: continue
    raise RuntimeError("Qwen 响应超时，见 "+logf)

def start_worker():
    sc=os.path.join(TEMP,"_full_worker.py")
    open(sc,"w",encoding="utf-8").write(WORKER % {"root":QWEN_ROOT})
    env=dict(os.environ)
    sox=os.path.join(QWEN_ROOT,"walkingwithai","sox"); ffm=os.path.join(QWEN_ROOT,"walkingwithai","ffmpeg","bin")
    for k,v in [("SOX_PATH",sox),("FFMPEG_PATH",ffm),("HF_HOME",os.path.join(QWEN_ROOT,"models")),
                ("TORCH_HOME",os.path.join(QWEN_ROOT,"models")),("KERAS_BACKEND","torch"),
                ("PYANNOTE_CACHE",os.path.join(QWEN_ROOT,"models","pyannote"))]:
        env[k]=v
    env["PATH"]=sox+os.pathsep+ffm+os.pathsep+env.get("PATH","")
    logf=os.path.join(TEMP,"_full_err.log")
    err=open(logf,"a",encoding="utf-8",buffering=1)
    p=subprocess.Popen([QWEN_PY,"-s","-u",sc],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                       stderr=err,text=True,encoding="utf-8",env=env,cwd=QWEN_ROOT,bufsize=1)
    read_json(p,logf)
    return p, logf

def synth(proc, pt, text, wav, xv):
    req={"cmd":"synth","voice":pt,"text":text,"out":wav,"language":"Chinese","temperature":0.5,"xv":xv}
    proc.stdin.write(json.dumps(req,ensure_ascii=False)+"\n"); proc.stdin.flush()
    m=read_json(proc,None)
    if not m.get("ok"): raise RuntimeError(str(m.get("err"))[:200])
    return m

def volpeak(wav):
    """返回峰值(dBFS)，用于峰值归一化（确定性强、零削波）。"""
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

def assemble_countdown(digit_wavs, out_wav, slot_sec=1.0):
    w0=wave.open(digit_wavs[0],"rb"); sr=w0.getframerate(); w0.close()
    slot=int(slot_sec*sr); total=len(digit_wavs)*slot
    out=array.array("h",[0])*total
    for i,dw in enumerate(digit_wavs):
        w=wave.open(dw,"rb"); n=w.getnframes(); data=w.readframes(n); w.close()
        samples=array.array("h"); samples.frombytes(data)
        m=min(len(samples),slot); out[i*slot:i*slot+m]=samples[:m]
    wo=wave.open(out_wav,"wb"); wo.setnchannels(1); wo.setsampwidth(2); wo.setframerate(sr)
    wo.writeframes(out.tobytes()); wo.close()

def xv_for(vname, kind, inner):
    if vname == "未来1":
        if kind == "numeric":
            return True
        return cjk_count(inner) <= 4   # ≤4 字中文 XV，≥5 字 ICL
    return False

def main():
    os.makedirs(BUILD, exist_ok=True)
    # 收集任务
    tasks=[]   # (sub, fn, kind_or_None)
    for sub in SUBDIRS:
        d=os.path.join(SRC,sub)
        if not os.path.isdir(d): continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(('.ogg','.mp3')): continue
            cls, info = classify(fn)
            tasks.append((sub, fn, cls, info))
    print("扫描到 %d 个音频：TTS=%d，原样拷贝(SFX)=%d"
          % (len(tasks), sum(1 for t in tasks if t[2]=='tts'), sum(1 for t in tasks if t[2]=='sfx')))

    # 建立两个输出包骨架 + 拷贝 .toc
    pkgs={}
    for vname in VOICES:
        pkg=os.path.join(BUILD,"SharedMedia_Rurutia_"+vname)
        for sub in SUBDIRS:
            os.makedirs(os.path.join(pkg,sub), exist_ok=True)
        pkgs[vname]=pkg
        # .toc：复制原版并改标题（不影响索引）
        toc_src=os.path.join(SRC,"SharedMedia_Rurutia.toc")
        if os.path.isfile(toc_src):
            shutil.copy2(toc_src, os.path.join(pkg,"SharedMedia_Rurutia_"+vname+".toc"))

    print("启动 Qwen 工作进程...")
    proc, logf = start_worker()

    stats={"tts":0,"sfx":0,"skip":0,"err":0}
    t0=time.time()
    for sub, fn, cls, info in tasks:
        inner=inner_name(fn)
        for vname, pt in VOICES.items():
            pkg=pkgs[vname]
            out_rel=os.path.join(sub, fn)
            out_path=os.path.join(pkg, out_rel)
            if os.path.isfile(out_path) and os.path.getsize(out_path)>2000:
                stats["skip"]+=1; continue
            if cls == "sfx":
                shutil.copy2(os.path.join(SRC,sub,fn), out_path)
                stats["sfx"]+=1
                continue
            # TTS
            try:
                xv=xv_for(vname, info["kind"], inner)
                wav=os.path.join(TEMP,"_full_%s.wav"%abs(hash(vname+fn)))
                if info["kind"]=="numeric":
                    cd=parse_countdown(inner)
                    if cd:
                        dw=[]
                        for d in cd:
                            dwp=os.path.join(TEMP,"_fd%d.wav"%d)
                            synth(proc, pt, NUMERAL[d], dwp, xv); dw.append(dwp)
                        assemble_countdown(dw, wav, slot_sec=1.0)
                        for x in dw:
                            try: os.remove(x)
                            except OSError: pass
                    else:
                        synth(proc, pt, tts_text(inner), wav, xv)
                else:
                    synth(proc, pt, tts_text(inner), wav, xv)
                peak=volpeak(wav)
                gain=max(0.0, PEAK_TARGET-peak)
                to_ogg(wav, out_path, gain)
                try: os.remove(wav)
                except OSError: pass
                stats["tts"]+=1
            except Exception as e:
                stats["err"]+=1
                print("  [ERR] %s/%s %s: %s"%(vname, sub, fn, str(e)[:120]))
                try: os.remove(out_path)
                except OSError: pass
        # 进度
        done=stats["tts"]+stats["sfx"]+stats["skip"]+stats["err"]
        if done % 50 == 0:
            print("  进度 %d/%d  已用时 %.1f 分"%(done, len(tasks)*len(VOICES), (time.time()-t0)/60))

    try: proc.stdin.write('{"cmd":"quit"}\n'); proc.stdin.flush()
    except: pass

    # 生成 MyMedia.lua（复制原版并改写 addon 路径，显示名/顺序不变）
    src_lua=os.path.join(SRC,"MyMedia.lua")
    if os.path.isfile(src_lua):
        for vname in VOICES:
            pkg=os.path.join(BUILD,"SharedMedia_Rurutia_"+vname)
            dst=os.path.join(pkg,"MyMedia.lua")
            txt=open(src_lua,encoding="utf-8",errors="ignore").read()
            txt=txt.replace("SharedMedia_Rurutia","SharedMedia_Rurutia_"+vname)
            open(dst,"w",encoding="utf-8").write(txt)
            print("  已生成 %s/MyMedia.lua"%os.path.basename(pkg))
    else:
        print("  [警告] 未找到原版 MyMedia.lua，需手动注册媒体")

    print("\n完成！统计：TTS=%d，SFX拷贝=%d，跳过(已存在)=%d，错误=%d"
          %(stats["tts"],stats["sfx"],stats["skip"],stats["err"]))
    print("产物目录：%s"%BUILD)

if __name__=="__main__":
    main()
