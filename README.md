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

AI 要读网页，绕不开一个问题：**怎么驱动浏览器**。常见做法是让你打开浏览器的调试端口，然后隔着这个端口指挥它。

那个端口是没有锁的。任何能连上它的程序——包括你随手打开的一个网页——都能读走你所有的 cookie、开任意页面、截你的屏。

web-solo 换了一条路：**用管道，不用端口。**

|  | 调试端口方案 | web-solo |
|---|---|---|
| 怎么连浏览器 | 开一个本地端口，隔着网络指挥 | 父子进程之间的管道，**不走网络** |
| 新增开放端口 | 1~2 个，无鉴权 | **0 个** |
| 用谁的浏览器 | 你正在用的那个 | 自己下的一份，**不碰你的** |
| 你的登录态 | 全部暴露给这个端口 | **默认不带** |
| 跑完之后 | 代理常驻，端口一直开着 | 进程退出，什么都不留 |

管道没有编号、系统里查不到、父进程一退就断。装在多少台机器上，新增的开放端口都是零。

---

## 快速开始

```bash
git clone https://github.com/Closed-Book/web-solo.git ~/.claude/skills/web-solo
```

装完就能用，依赖会在第一次调用时自动装好（Playwright + Chromium，下载约 95 MB，只下一次）。

想先确认环境：

```bash
bash ~/.claude/skills/web-solo/scripts/setup.sh
```

它是幂等的——缺什么装什么，齐了就秒退。

---

## 能做什么

```bash
python3 scripts/browser.py fetch  https://example.com          # 取正文
python3 scripts/browser.py shot   https://example.com --out a.png   # 截图
python3 scripts/browser.py eval   https://example.com --js 'document.title'
python3 scripts/browser.py run    steps.json                   # 多步交互（步骤表见下）
python3 scripts/browser.py tabs                                # 看配额占用
python3 scripts/browser.py doctor                              # 环境自检
```

`fetch` 的输出：

```json
{
  "url": "https://example.com/",
  "title": "Example Domain",
  "text_len": 129,
  "body": "Example Domain\n\nThis domain is for use in documentation examples..."
}
```

`run` 接一张 JSON 步骤表，在一次会话里走完点击、输入、滚动、等待、取值，不用为每步重开浏览器。`steps.json` 由你自己写，最小形态：

```json
{
  "steps": [
    {"action": "goto", "url": "https://example.com"},
    {"action": "text", "selector": "body"}
  ]
}
```

完整动作表（`goto` / `click` / `fill` / `scroll` / `wait_selector` / `wait_gone` / `text` / `screenshot` / `eval`）见 [`SKILL.md`](SKILL.md) 的「多步交互」节。

**不绑定任何 agent 框架。** `scripts/browser.py` 就是一个普通的 Python CLI，输出 JSON，任何框架、任何语言的脚本、甚至你自己在终端里都能直接调。`SKILL.md` 是 Claude Code 的技能格式——别的框架忽略它，直接用 CLI 即可。

---

## 遇到「正在验证您是否是真人」怎么办

有些站点用 Cloudflare 一类的防护挡自动访问。按代价从低到高处理，**不要跳步**：

| 顺序 | 做法 | 说明 |
|---|---|---|
| 1 | 默认 headless 直接抓 | 大部分站点到这一步就过了 |
| 2 | `--headed --unblock 25000` | 切有头等挑战页放行。防护认的是 UA 里的 `HeadlessChrome/`，换掉就过 |
| 3 | **交给人** | 前两步都不行，说明这站要真人交互 |

第 3 步不是失败，是**判断题**：

- 这页只是调研里的一个小点 → **丢掉，换个源**，不值得为它搭一套绕过
- 这页就是你要的那个（比如某条特定的帖子、某个只在站内才有的资料）→ **让使用者自己打开处理**，人过一次验证，比工具绕十次都快

⚠️ **web-solo 不做绕过风控的事。** 它提供的是「换个 UA 再试一次」这种正当重试，不提供指纹伪装、验证码破解、代理池。站点明确不欢迎自动访问时，正确的回应是换路或换人，不是换个马甲再来。

有头模式的窗口开在屏幕外（`-3000,-3000`），不会抢你的焦点。代价是 Dock 会出现一个图标——headless 不会。

---

## 实测记录

2026-09-22 实测，macOS 26.3 / Python 3.12。下面都是 `browser.py` 的原样输出，没有为了好看换参数重试。

### 基础能力

| 项 | 结果 |
|---|---|
| `fetch example.com` | 129 字符 |
| `shot wikipedia.org` | 192873 字节 |
| 运行中 chromium 的 TCP 监听端口 | **0 个**（命令行是 `--remote-debugging-pipe`，进程 fd 表里没有任何 TCP socket） |
| 进程退出后 | chromium 进程数归零，临时 profile 目录自动删除 |
| `setup.sh` 依赖已齐 | 0.2–0.6 秒秒退 |
| `setup.sh` 空环境首装 | 44.7 秒（装 Playwright + 下载 Chromium 94.3 MiB） |

### 中文站抓取

| 站点 | 第 1 档 headless | 第 2 档 有头 + `--unblock 20000` |
|---|---|---|
| 小红书 explore | ✅ 1618 字符 | — |
| B 站首页 | ✅ 563 字符 | — |
| 知乎首页 | ✅ 459 字符 | — |
| 知乎热榜 | — | ❌ 「安全验证 - 知乎」，29 字符 |
| 微博 | ❌ 0 字符，`Sina Visitor System` | ⚠️ 272 字符、title 为空，实质没过 |
| x.com | ❌ 0 字符 | — |

三条结论：

1. **反爬检测层基本能过。** 小红书、B 站、知乎首页都在第 1 档直接拿到了正文——其中知乎对普通 HTTP 抓取是直接 403。
2. **登录墙过不了，这是设计不是缺陷。** 默认不带任何登录态，站点不给游客看的内容就是拿不到。x.com 和微博返回 0 属于这一类，不是被反爬拦住了。
3. **同一个站，不同页面的防护强度不一样。** 知乎首页第 1 档就过，热榜第 2 档仍然撞安全验证。所以"某站能不能抓"不是一个二元答案——按页面记，写进 `references/site-patterns/`。

### 要带登录态：`--profile`

上面第 2 条那类站（x.com、微博），正解是带自己的登录态，不是去伪装成真人浏览器。

第一次，有头打开，**自己在窗口里登录一次**：

```bash
python3 scripts/browser.py run login.json --headed --profile ~/.web-solo-profiles/myaccount --timeout 300000
```

（`login.json` 里放一个 `goto` 加一个等登录后元素的 `wait_selector`，把窗口撑到你登完；写法见 `SKILL.md`。）

之后所有调用带同一个 `--profile` 跑 headless，登录态自动复用：

```bash
python3 scripts/browser.py fetch <url> --profile ~/.web-solo-profiles/myaccount
```

实测（`localStorage` 跨进程存取）：带同一 `--profile` 的新进程能读回上一次写的值；不带 `--profile` 的进程读到 `null`——一次性临时 profile 确实是隔离的。

代价要先想清楚：

- 带登录态 = **把自己的凭据交给这个工具**。
- profile 目录里是真 cookie 和会话，**自己保管**，别放进会同步到别处的目录。
- **别提交进 git**（仓库的 `.gitignore` 已经挡掉了常见的 profile 目录名，但你自己起的名字要自己确认）。
- 自动化访问带着真实登录态，**存在账号风控风险**，高频抓取尤其明显。

---

## 项目结构

```
web-solo/
├── SKILL.md                      给 AI 读的执行规范：两层路由 + 纪律
├── README.md                     本文件
├── scripts/
│   ├── setup.sh                  依赖自检与安装（幂等，齐了秒退）
│   └── browser.py                执行层：fetch / shot / eval / run / tabs / doctor
├── references/
│   └── site-patterns/            站点经验：每站的选择器、拦截行为、等待时长
│       ├── dpreview.com.md       Cloudflare 站怎么过
│       ├── zhihu.com.md          同一个站不同页面防护强度不同
│       ├── xiaohongshu.com.md    headless 直接过
│       └── weibo.com.md          登录墙，不是反爬
├── .claude-plugin/plugin.json    插件元信息
└── LICENSE                       MIT
```

**两层路由**（写在 `SKILL.md` 里，AI 按它决策）：

| 层 | 用什么 | 什么时候 |
|---|---|---|
| 1 · 轻量 | 宿主自带的网页抓取 | 静态页、文档站、大部分英文官网 |
| 2 · 浏览器 | `browser.py` | 反爬站、SPA、需要点击/滚动/登录态的页面 |

能用第 1 层就别起浏览器——它快一个量级，也不占内存。

---

## 并发与内存

每个标签页约占 **100~120 MB**、一个进程。默认上限 **20 个标签页，跨进程全局共享**——不是每个调用者各 20。

多个 AI 子任务并行时，在派发阶段就把配额切开（比如 4 个子任务各 5 个），别让每个都按 20 算。

关掉标签页内存会真正回收，所以「用完即关」是有效手段：拿到数据当场关，不要攒到任务结束。

改上限：

```bash
export WEB_SOLO_MAX_TABS=12
```

调之前先算一遍内存——20 个满载约 2.9 GB。

---

## 限制

诚实说三条：

1. **默认不带登录态。** 只能拿公开内容，登录后才能看的东西拿不到。要带登录态，得自己指定一个持久化 profile 目录，并明白那意味着把凭据交给了这个工具。
2. **不保证过得了所有防护。** 见上面那节——防不住就交给人，这是设计，不是缺陷。
3. **平台支持**：macOS 实测通过，Linux 同理（同一套 POSIX 调用），**Windows 未验证**。已知需要适配的点在 `scripts/browser.py` 里逐个用 `[WINDOWS]` 注释标了出来（进程探活、文件锁、临时目录清理、屏幕外窗口坐标）；另外 `setup.sh` 是 bash 脚本，Windows 上要走 WSL 或 Git Bash。欢迎提 issue。

---

## 依赖

| 依赖 | 版本 | 怎么来 |
|---|---|---|
| Python | 3.9+ | 系统自带，或 `brew install python` |
| Playwright | 任意近版 | `setup.sh` 自动装 |
| Chromium | 跟随 Playwright | `setup.sh` 自动下载（下载约 95 MB，解压后占约 430 MB，只下一次） |

没有别的了。不需要 Node.js，不需要浏览器插件，不需要改你现有浏览器的任何设置。

---

## License

MIT
