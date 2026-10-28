<div align="center">

# ⧉ ClipMind

**带大脑的剪贴板。**

本地优先的剪贴板历史管理：语义搜索 + 智能内容提取。
一切数据都留在你的电脑上，永不上传。

[![CI](https://img.shields.io/badge/CI-github--actions-2088ff?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No telemetry](https://img.shields.io/badge/telemetry-none-38d9c8)](#-隐私与安全)

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

---

复制了一个验证码、一个链接、一个色值……十分钟后又要用，但 `Win+V`
只能按字面匹配，找不到"银行短信里那个码"。

ClipMind 解决这个问题：它监听剪贴板、**看懂每条内容是什么**（验证码、
链接、邮箱、电话、JSON、颜色、命令……），然后让你按**含义**搜索，
而不是按字符。

![搜索演示](docs/screenshot-search-otp.png)

> 搜索"验证码"。注意金色的 **776531** 一键复制 chip——只复制码本身，
> 而不是整条短信。此截图在未安装任何模型的情况下实现——意图识别
> 完全离线工作。

## ✨ 亮点

- 🔍 **按含义搜索** —— 搜"那个 github 链接"能找到，即使内容里既没有
  "github"也没有"链接"这几个字。FTS + 向量混合排序，逐级降级。
- 🧠 **知道自己在看什么** —— 开箱识别 14 种内容类型：验证码、链接、
  邮箱、电话、JSON、hex/rgb 颜色、shell 命令、路径、IP、UUID、代码、
  Markdown、表格。
- ⚡ **实体一键复制** —— 只复制验证码数字、只复制链接、只复制电话号
  码，不带周围的一堆废话。
- 🔒 **隐私优先** —— 单个 SQLite 文件，默认零网络。可选的本地 ONNX
  embedding 同样不出网。
- 🛡️ **设计级密钥防护** —— 密码、TOTP 种子、私钥、JWT、API token
  在**入库之前**就会被跳过（形状/熵启发式）。
- 🔴 **实时** —— 新剪贴内容即时出现在面板（WebSocket）。使用次数
  统计会把你最常用的顶上来。
- 🧩 **零重依赖** —— 核心秒装，语义层是可选 extra。
- 🇨🇳 **中文原生搜索** —— trigram 分词，子串检索对中文真正可用。

## 🚀 快速开始

```bash
pip install clipmind
clipmind          # 启动服务并打开面板
```

- **`Ctrl+Shift+V`** —— 任意界面呼出面板
- 直接输入搜索：试试"验证码"、"github"、"颜色"、"链接"，或者描述它
- **Enter** 复制选中项 · **↑↓** 切换 · **Esc** 清空
- 点任何**金色/绿色 chip** 只复制那个实体

> 首次运行剪贴板是空的？点顶栏的 **示例数据** 先看看效果。

### 启用语义搜索（可选，仍然离线）

```bash
pip install 'clipmind[semantic]'    # fastembed + 本地小模型
```

或接入任意 OpenAI 兼容 embedding 接口：

```bash
export CLIPMIND_API_KEY=sk-...
clipmind
```

不装也能用：关键词 + 意图 + 行为信号排序。**UI 永远不会因为模型
没加载而罢工。**

## 🔒 隐私与安全

- **存储**：单个 SQLite 文件（`~/.clipmind/clipmind.db`），删掉即清空。
  无云端、无账号、无遥测。
- **网络**：默认零请求。只有你主动配置 embedding API 才会有外呼，
  且只发送你搜索的那一句，不发送历史。
- **密钥守卫**：入库前检测 TOTP 种子、私钥块、GitHub/Slack/AWS
  token 形状、JWT、高熵密码串 + 用户可配置黑名单。
- **边界**：只监听 `127.0.0.1`，面板不暴露给局域网。

## 🖥️ 命令行

```console
$ clipmind search 验证码
 1. [otp      ]  2.09 您的登录验证码是 776531，请勿泄露给他人
— 1 hits in 1 ms

$ clipmind add "记一下这个"
$ clipmind status
$ clipmind demo          # 注入示例数据
```

## 🗺️ Roadmap

- [ ] 图片剪贴 + OCR
- [ ] Tauri 桌面壳（真正的全局浮层）
- [ ] SQLCipher 静态加密
- [ ] 拼音 & 模糊匹配层
- [ ] 端到端加密的跨设备同步（可选）

## 🤝 参与贡献

```bash
pip install -e '.[dev]'
pytest
```

欢迎 PR —— 尤其是密钥检测启发式，它靠真实世界的模式样本变得更强。

## 许可

[MIT](LICENSE)
