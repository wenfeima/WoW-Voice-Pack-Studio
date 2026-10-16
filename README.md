# WoW 语音包工坊

> 批量生成 WoW 插件语音 → 一键打成标准 SharedMedia 插件包 → 直接装进 AddOns
>
> GitHub：`https://github.com/wenfeima/WoW-Voice-Pack-Studio`
> 本地开发位置：`L:\工作流\小工具\WoW语音包工坊`

**零第三方依赖启动**：网页服务只用到 Python 标准库（`http.server`）。`启动.bat` 会按需自动安装 `sherpa-onnx` / `edge-tts`。程序全程**自适应目录**，不写死盘符，挪到任何位置、WoW 装别的盘都能跑。

---

## 一分钟上手

双击 **`启动.bat`**，浏览器自动打开 `http://127.0.0.1:8788`。

四个页签：

| 页签 | 干什么 |
|---|---|
| ① 批量生成 | 填台词表 → 选音色 → 一键生成 ogg |
| ② 语音清单 | 试听 / 改名 / 删除 / 筛选 |
| ③ 打包安装 | 生成标准插件包到 AddOns，或导出 zip |
| ④ 设置 | WoW 路径、ffmpeg 路径、环境自检 |

台词表每行一条，支持三种写法：

```
文件名|要说的话          ← 推荐
文件名=要说的话
纯台词                  ← 自动命名 v001、v002…
```

---

## 它生成的东西长什么样

```
D:\暴雪\World of Warcraft\_retail_\Interface\AddOns\SharedMedia_WoWVoice\
├── SharedMedia_WoWVoice.toc
├── MyMedia.lua
└── media\
    ├── 【我】1分担.ogg
    ├── 【我】转阶段.ogg
    └── …
```

`MyMedia.lua` 内容：

```lua
local LSM = LibStub("LibSharedMedia-3.0", true)
if not LSM then return end

LSM:Register("sound", "【我】1分担", [[Interface\AddOns\SharedMedia_WoWVoice\media\【我】1分担.ogg]])
```

音频规格**与露露的 SharedMedia_Rurutia 完全一致**：

| 项 | 值 |
|---|---|
| 格式 | Ogg Vorbis |
| 采样率 | 44100 Hz |
| 声道 | 立体声 |
| 质量 | `-q:a 5` |

装完重启游戏，在 **EXBoss / BigWigs / WeakAuras** 的语音下拉里就能选到你的包。

---

## 音色

| 引擎 | 特点 | 说明 |
|---|---|---|
| **Qwen3-TTS 克隆音色**（⭐ 默认） | 用你自己的 `.pt` 音色文件做克隆合成 | 首次生成需加载 4.3G 模型（约 20 秒），之后常驻复用；每条约 7~13 秒 |
| **本地 VITS**（sherpa-onnx） | 离线，快（约 1.3 秒/条） | 扫 `VoicePackMaker\export` 下的 `model.onnx+tokens.txt+lexicon.txt` |
| **edge-tts**（在线） | 16 个中文神经网络音色 | 需要联网 |
| **系统 SAPI** | 纯离线兜底 | 没网、没 GPU 时用 |

**自动识别到的 Qwen3 克隆音色**（来自 `L:\工作流\Qwen3-TTS-v260128-fix\saved_voices\*.pt`）：

| 音色 | 文件 |
|---|---|
| 君仪2 | `saved_voices\君仪2.pt` |
| 未来1 | `saved_voices\未来1.pt` |

新增音色只要把 `.pt` 丢进那个目录，重启工具就能选。

### 关于温度

Qwen3 克隆音色有个「温度」参数（本地 VITS 没有）：

- **采样随机度**，0.3~0.8 可调，默认 **0.50**
- **越低越稳、越可复现** —— 同一句话重新生成两次基本一致
- 调高（0.7+）会有随机变化、更有"表现力"，但也可能**吃字、含糊、音色漂移**

做语音包建议 **0.45~0.55**：几百条短音频要的是统一清楚，不是多样性。

配套参数（写死在 `_QWEN_WORKER` 里）：`top_p=0.9`、`top_k=50`、`repetition_penalty=1.1`。

---

## 区分不同音色

生成的文件名 = `自定义前缀` + `音色标识` + `原名`：

```
【我】君仪2_1分担.ogg
【我】未来1_1分担.ogg
```

在「② 语音清单」页可以按音色标识筛选、试听、改名、删除。
在「① 批量生成」页可以关掉「文件名自动带上音色标识」，全部用同一个前缀。

**打包时按音色拆子目录**（③ 页勾选「按音色拆分目录」）：

```
SharedMedia_WoWVoice/
└── media/
    ├── 君仪2/   ← 42 条
    └── 未来1/   ← 2 条
```

`MyMedia.lua` 里的显示名会变成 `君仪2/1分担`、`未来1/1分担`，
**游戏内 EXBoss / BigWigs / WA 的语音下拉就会按人物分组**，一眼分开。

---

## 内置语料

四类，可一键载入或追加到自己的台词表：

| 分类 | 内容 |
|---|---|
| 大秘境开场结束 | 拉怪、计时、钥匙、灭团 |
| 通用机制提示 | 集合分散、停手、AOE、驱散、打断、转火、斩杀 |
| 职业语音 | 关键天赋/技能就绪播报（对应露露的 Rurutia_SA） |
| 装备饰品提示 | 饰品触发（对应 RurutiaSA_Trinkets） |

---

## 设计上的几个关键点

**自适应目录** —— 不写死任何盘符。程序位置变了、WoW 装在别的盘，都能跑。

**WoW 路径自动探测** —— 先读注册表 `Wow6432Node\Blizzard\WoW`，再扫常见安装位置。
本机探测结果：`D:\暴雪\World of Warcraft\_retail_\Interface\AddOns`。

**不硬依赖 SharedMedia** —— 很多用户装的是独立的 `LibSharedMedia-3.0` 插件，
写死 `Dependencies: SharedMedia` 会导致加载失败。所以用
`LibStub("LibSharedMedia-3.0", true)` 保护，没加载就静默跳过，不刷红字。
打包时若检测不到 LibSharedMedia 会明确警告。

**单条失败不中断** —— 批量生成时某条报错只记进失败列表，其余继续跑。

**断点续跑** —— 已存在的 ogg 会跳过，中断后重跑不重复生成。

---

## 已知限制

- edge-tts 需要联网；离线请切「系统语音」
- 本地 VITS 模型（`L:\工作流\vits_train\export\junyi-vits.onnx`）是繁体 Coqui 导出、
  缺 `lexicon.txt`，暂时没接进来。要用需补 lexicon 或重新导出 sherpa-onnx 版
- `Interface:` 版本号默认 `120100, 16001, 120105`，客户端更新后需在④页改

---

---

## 从 GitHub 获取

```bash
git clone https://github.com/wenfeima/WoW-Voice-Pack-Studio.git
cd WoW-Voice-Pack-Studio
双击 启动.bat        # Windows：自动找 Python、装轻量依赖、开浏览器
# 或手动：
python web_app.py    # 然后打开 http://127.0.0.1:8788
```

首次运行会在本目录生成 `config.json`（已写入 `.gitignore`，不会上传你的本机路径）。

---

## 环境准备（Qwen3-TTS 克隆音色引擎 ⭐ 默认）

网页里默认的「自定义克隆音色」依赖本地 Qwen3-TTS 环境，**这一块无法塞进仓库**（模型约 4.3G + 你的私有 `.pt` 音色），需自行准备：

1. 准备一个装好 `qwen_tts` + `torch`（CUDA）的 Python 环境，并下载 `Qwen3-TTS-12Hz-1.7B-Base` 模型。
2. 把克隆好的音色文件（`.pt`）放到某个目录，例如 `Qwen3-TTS-xxx/saved_voices/`：
   - `君仪2.pt`、`未来1.pt` …… 丢进去重启工具即可在音色下拉里选到。
3. 工具在「④ 设置」里点「扫描 Qwen 音色」会自动列出该目录下的 `.pt`。

> 只要不用 Qwen3 克隆音色，纯离线也能跑：选「系统语音（SAPI）」或联网用「edge-tts」，不需要 GPU、不需要模型。

`ffmpeg`：① 页/④ 页可手动指定；`启动.bat` 也会在 `MultiTTS-VoiceMaker\runtime` 等常见位置自动找。

---

## 附：露露语音包 1:1 替换脚本

根目录下的 `gen_full.py` / `gen_replace.py` / `gen_fix_future.py` 是给 **SharedMedia_Rurutia（露露）** 做「文件名 1:1 替换」的批处理脚本（把原版 1693 个文件用自定义音色重生成，EXBoss 按名索引起作用）。

- 它们是**参考/进阶脚本**，不是网页工具的一部分，需手动改脚本顶部的 `QWEN_ROOT` / `SRC` / `BUILD` 等绝对路径再跑（目前写的是作者本机路径）。
- `gen_full.py`：全量替换（双音色：君仪2 / 未来1），带断点续跑。
- `gen_replace.py`：20 条抽样验证生成器（验证 ICL/XV 模式、音量、倒计时）。
- `gen_fix_future.py`：定点重做未来1版若干发音有问题的文件（DEBUFF 连读、XX点你断句、数字 2→两 等）。

---

## 相关

对比参考：`L:\工作流\小工具\WoW语音包生成器`（tkinter 版，本机无 tkinter 跑不起来）
本工具是重写版，Web GUI，双击即用。
