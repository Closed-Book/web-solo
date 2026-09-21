<div align="center">

# web-solo

**给 AI 一双浏览网页的手**

零端口 · 不碰你的浏览器 · 用完即退

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/playwright-auto--installed-2EAD33.svg)](https://playwright.dev/)
[![Platform](https://img.shields.io/badge/macOS-tested-lightgrey.svg)](#限制)
[![Linux](https://img.shields.io/badge/Linux-should%20work-lightgrey.svg)](#限制)

</div>

---

## 它解决什么

### 网页分三类，对症下药省时间

| 这类网站 | 比如 | 怎么办 | 代价 |
|---|---|---|---|
| **静态页、文档站** | Wikipedia · GitHub · 官方 docs | **用 AI 框架默认 skill 抓取**；站点在 [OpenCLI 清单](references/opencli.md)里的更好，直出结构化字段 | 零 |
| **反爬站** | 小红书 · B 站 · 知乎 | web-solo 起一个独立浏览器，headless 直接过 | 一个进程 |
| **要登录的站** | X · 微博 | `login` 登一次，之后一直复用 | 你的一次登录 |

第一类占了日常任务的大半，**能不起浏览器就别起**——快一个量级，也不占内存。web-solo 只负责后两类。

三类的分界写在 [`SKILL.md`](SKILL.md) 里，AI 按它自己决策，不需要你每次指定。

### 可选：装一个 OpenCLI，第一类网页更快

第一类里有一部分站点（npm · arxiv · PyPI · Wikipedia · Stack Overflow · Hacker News 等 **79 个**）有现成的结构化适配器。装上 [OpenCLI](https://www.npmjs.com/package/@jackwener/opencli) 之后，这些站直接吐字段，不必抓回整页再让 AI 从正文里找：

```bash
opencli npm search react -f yaml
```

```yaml
- rank: 1
  name: react
  version: 19.3.0
  weeklyDownloads: 133327282
  license: MIT
```

**不装照常用。** web-solo 的核心是 `scripts/browser.py`，它不依赖这一层。装了只是让第一类网页更快、更省 token，顺带拿得下 `curl` 直接吃 403 的站（npm 就是）。

**它不破坏零端口。** OpenCLI 另有一类要浏览器扩展、会起本地 daemon 的命令——web-solo 只用其中 HTTP 直连的 `[public]` 类。实测 v1.7.22 跑 npm / arxiv / pypi / wikipedia 四条命令，执行前、执行中每 0.4 秒轮询、执行后，19825 端口监听数**全程为 0**，跑完无残留进程。新增开放端口仍然是 0 个。

命中条件、站点清单、装法和安全边界见 [`references/opencli.md`](references/opencli.md)。

### 它跟浏览器怎么说话

驱动浏览器有两种接法：开一个本地调试端口隔着网络指挥，或者用父子进程之间的管道。web-solo 走管道。

|  | 调试端口 | 管道（web-solo） |
|---|---|---|
| 新增开放端口 | 1~2 个 | **0 个**（实测：进程 fd 表里只有 PIPE，无 TCP socket） |
| 用谁的浏览器 | 你正在用的那个 | 自己下的一份 |
| 登录态 | 挂在那个端口后面 | 默认不带，要带用 `login` |
| 跑完之后 | 代理常驻，端口留着 | 进程退出，什么都不留 |

管道没有编号、系统里查不到、父进程一退就断。好处很直接：**装在多少台机器上，要新开的端口都是零，也不用改你现有浏览器的任何设置。**

---

## 快速开始

```bash
git clone https://github.com/Closed-Book/web-solo.git ~/.claude/skills/web-solo
```

装完就能用。依赖在第一次调用时自动装好；想提前确认跑 `bash scripts/setup.sh`，它是幂等的，缺什么装什么，齐了秒退。

---

## 能做什么

```bash
python3 scripts/browser.py fetch  https://example.com                # 取正文
python3 scripts/browser.py shot   https://example.com --out a.png    # 截图
python3 scripts/browser.py eval   https://example.com --js 'document.title'
python3 scripts/browser.py run    steps.json                         # 多步交互
python3 scripts/browser.py tabs                                      # 看配额
python3 scripts/browser.py login  <url> --profile DIR                # 登录一次，存登录态
python3 scripts/browser.py doctor                                    # 环境自检
```

输出是 JSON：

```json
{"url": "https://example.com/", "title": "Example Domain", "text_len": 129, "body": "..."}
```

`run` 接一张步骤表，一次会话里走完点击、输入、滚动、等待、取值，不为每步重开浏览器：

```json
{"steps": [
  {"action": "goto", "url": "https://example.com"},
  {"action": "text", "selector": "body"}
]}
```

完整动作表见 [`SKILL.md`](SKILL.md)。

**不绑定 agent 框架。** `scripts/browser.py` 是个普通 Python CLI，输出 JSON——任何语言、任何框架、你自己在终端里都能直接调。仓库里的 [`AGENTS.md`](AGENTS.md) 是 Linux Foundation 旗下 Agentic AI Foundation 管的跨厂商标准，Codex · Cursor · Copilot Coding Agent · Gemini CLI 等 28+ 工具直接读它。

### 要登录的站：`--profile`

默认每次用一次性临时 profile，不留痕也不带身份。需要登录后的内容，就指定一个持久目录——第一次有头打开、自己登一次，之后 headless 一直复用：

```bash
# 第一次：窗口会打开，你正常登录，登完关掉窗口就行
python3 scripts/browser.py login https://x.com --profile ~/.web-solo-profiles/me

# 之后：登录态自动复用，headless 跑
python3 scripts/browser.py fetch <url> --profile ~/.web-solo-profiles/me
```

`login` 是**唯一一个窗口开在可见区**的命令——因为要你自己操作。其余所有有头场景都在屏幕外。

**登录墙和反爬是两回事。** X、微博这类站返回空白不是被反爬拦住，是站点不给游客看内容——正解是带上自己的登录态，不是去伪装成真人浏览器。

代价要想清楚：profile 目录里是**真 cookie 和会话**，自己保管、别提交进 git（`.gitignore` 挡了常见命名，你自起的名字要自己确认）；带真实身份做自动化访问**有账号风控风险**，高频抓取尤其明显。

### 遇到「正在验证您是否是真人」

按代价从低到高处理，**不要跳步**：

| 顺序 | 做法 | 说明 |
|---|---|---|
| 1 | 默认 headless 直接抓 | 大部分站点到这就过了 |
| 2 | `--headed --unblock 25000` | 切有头等挑战页放行。防护认的是 UA 里的 `HeadlessChrome/` |
| 3 | **交给人** | 前两步都不行，说明这站要真人交互 |

第 3 步不是失败，是**判断题**：这页只是调研里的一个小点 → 丢掉换个源；这页就是你要的那个 → 让使用者自己打开处理，人过一次验证比工具绕十次都快。

同一个站不同页面的防护强度可能不同（知乎首页第 1 档就过，热榜第 2 档还撞验证），所以「某站能不能抓」不是二元答案——按页面记进 [`references/site-patterns/`](references/site-patterns/)。

有头模式的窗口开在屏幕外（`-3000,-3000`），不抢你的焦点；代价是 Dock 会出现图标，headless 不会。

---

## 项目结构

```
web-solo/
├── AGENTS.md                   给任意 coding agent 读的操作手册（跨框架标准）
├── SKILL.md                    Claude Code 的 skill 格式，同一套规范
├── scripts/
│   ├── setup.sh                依赖自检与安装
│   └── browser.py              执行层
└── references/
    ├── opencli.md              可选加速层：OpenCLI [public] 清单与用法
    └── site-patterns/          站点经验：选择器、拦截行为、等待时长
        ├── dpreview.com.md       Cloudflare 站怎么过
        ├── zhihu.com.md          同站不同页防护不同
        ├── xiaohongshu.com.md    headless 直接过
        └── weibo.com.md          登录墙，不是反爬
```

---

## 限制

### 不做的事

⚠️ **web-solo 不做绕过风控的事。** 提供的是「换个 UA 再试一次」这类正当重试，**不提供指纹伪装、验证码破解、代理池**。站点明确不欢迎自动访问时，正确回应是换路或换人。

### 并发与内存

每个标签页约 **100~120 MB** 和一个进程。默认上限 **20 个，跨进程全局共享**——不是每个调用者各 20。多个 AI 子任务并行时，在派发阶段就把配额切开（如 4 个子任务各 5 个）。

关掉标签页内存会真正回收，所以「用完即关」是有效手段，别攒到任务结束。

```bash
export WEB_SOLO_MAX_TABS=12    # 改上限；20 个满载约 2.9 GB
```

### 已知边界

1. **默认不带登录态**，只能拿公开内容。要带就用 `--profile`，代价见上。
2. **不保证过得了所有防护**，防不住就交给人——这是设计，不是缺陷。
3. **平台**：macOS 实测通过；Linux 未实测（容器或 root 下若启动失败，用 `WEB_SOLO_NO_SANDBOX=1`）；**Windows 未适配**，需要改的点在 `browser.py` 里用 `[WINDOWS]` 注释逐个标了，另外 `setup.sh` 要走 WSL 或 Git Bash。欢迎提 issue。

---

## 依赖

| 依赖 | 怎么来 |
|---|---|
| Python 3.9+ | 系统自带，或 `brew install python` |
| Playwright + Chromium | `setup.sh` 自动装（下载约 95 MB，解压后占约 430 MB，只下一次） |

核心依赖就这两项，**不需要浏览器插件、不需要改你现有浏览器的任何设置**。

唯一需要 Node.js 的是可选的 OpenCLI 加速层——不装它 web-solo 功能完整，见 [`references/opencli.md`](references/opencli.md)。

---

## 鸣谢

- [web-access](https://github.com/eze-is/web-access)（一泽 Eze，MIT）
- [Playwright](https://playwright.dev/)（Microsoft，Apache-2.0）
- [AGENTS.md](https://agents.md/)（Agentic AI Foundation）
- [OpenCLI](https://www.npmjs.com/package/@jackwener/opencli)（@jackwener，Apache-2.0，可选加速层）

---

## License

MIT
