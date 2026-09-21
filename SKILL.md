---
name: web-solo
description: 零端口网页访问。宿主自带的 WebFetch / WebSearch 拿不到的页面走这里——反爬站、SPA、登录后页面，以及需要点击、填表、上传、滚动加载、截图的交互任务。自起一个独立浏览器实例，不要求使用者开调试端口，不碰使用者正在用的浏览器，用完即退。触发词：抓网页 / 网页截图 / 动态渲染页 / 自动点击填表 / WebFetch 抓不到。
---

# web-solo

## 第一条规则：先跑 setup.sh

**任何浏览器操作之前，先执行一次**：

```bash
bash scripts/setup.sh
```

它是幂等的：依赖齐备时半秒内退出、不做任何事；缺 playwright 包或 chromium 就当场装上。首次安装要下载 chromium 约 95 MB（解压后占约 430 MB），实测空环境全程 44.7 秒，脚本会把进度打在终端上。

这一步不能跳。跳过的表现不是报错，是 `browser.py` 抛一句 `ModuleNotFoundError`，然后你去猜环境。

## 两层路由

**Tier 1 — 宿主自带的 WebFetch / WebSearch。** 静态页、文档站、新闻页、开源项目主页、大部分英文官网先走这层。快、无进程、无依赖。

**Tier 2 — `scripts/browser.py`。** 命中下面任一条才切过来：

- Tier 1 返回空、只有导航栏、或明显短于预期
- 正文由 JS 渲染（SPA、无限滚动列表、点开才展开的内容）
- 页面有反爬防护（Cloudflare 一类）
- 任务本身需要交互：点击、填表、上传文件、滚动加载、截图

不要一上来就开浏览器。一次 Tier 1 的代价是几百毫秒，一次 Tier 2 是几秒加上百 MB 内存。

### Tier 1 的可选加速层：OpenCLI

`command -v opencli` 查不到就跳过本节，按上面两层走——它是可选的，web-solo 不依赖它。

装了的话，目标站在 `references/opencli.md` 的 `[public]` 清单里（79 站，含 npm · pypi · arxiv · wikipedia · hackernews · stackoverflow 等）→ **先试它**：直出结构化字段，比抓网页文本省 token，不起浏览器、不占 tab 配额，而且能拿下直抓返 403 的站（npm 是典型）。

```bash
opencli list | grep -A8 '^  npm$'    # 先确认目标子命令带 [public]
opencli npm search react -f yaml     # 取数；stderr 上的 Node 警告与结果无关
```

三条纪律：

- 🔴 **标签只认 `opencli list`。** 实测 `opencli <site> --help` 把 `[public]` 和 `[cookie]` 一律标成 `[read]`，分辨不出来。误跑 `[cookie]` 命令会去连浏览器扩展和本地 daemon —— **那条路不在 web-solo 范围内**。
- **一次失败就回落**到 Tier 2，不重试、不换着命令试。
- 取回的内容仍要判真假：适配器只吐 schema 字段，注入面比抓网页文本窄，但不等于零。

清单、装法（锁版本）、安全边界见 `references/opencli.md`。

## 启动浏览器前，向使用者展示一次风险须知

一个会话里只说一次，在第一次调用 `browser.py` 之前：

> 即将启动一个独立浏览器实例访问网页。两点需知悉：①部分站点对自动化访问检测严格，如果你选择复制自己的登录态，存在账号风控风险；②默认不带任何登录态，抓取的是公开内容。用完即退，不留后台进程。

说完就继续做，不必等回复——除非使用者要求带登录态（`--profile`），那一步必须先得到明确同意。

## 命令

### 取内容

```bash
python3 scripts/browser.py fetch <url> [--format text|html|title] [--out FILE] [--raw]
                                        [--scroll MAX_MS] [--wait-selector CSS]
                                        [--headed] [--unblock MAX_MS] [--timeout MS]
```

默认输出一个 JSON：`url` / `title` / `text_len` / `body`，必要时带 `hint`。正文大时加 `--out` 写文件，stdout 只留元信息。`--scroll` 在取内容前滚到底，用来触发懒加载。

### 截图

```bash
python3 scripts/browser.py shot <url> --out FILE [--full-page] [--selector CSS]
                                       [--clip x,y,w,h] [--skeleton CSS] [--viewport WxH]
```

### 执行 JS 取数据

```bash
python3 scripts/browser.py eval <url> --js "document.querySelectorAll('article').length"
```

返回值自动序列化成 JSON。取结构化数据优先用它，比把整页 HTML 读回来再解析省得多。

### 多步交互

一次会话里串起一连串动作，写成 JSON 步骤表：

```bash
python3 scripts/browser.py run steps.json [--headed] [--keep-going] [--timeout MS]
```

```json
{
  "headed": false,
  "viewport": [1280, 1600],
  "steps": [
    {"action": "goto", "url": "https://example.com", "wait_until": "domcontentloaded"},
    {"action": "wait_gone", "selector": ".skeleton", "max_ms": 8000},
    {"action": "fill", "selector": "#q", "value": "keyword"},
    {"action": "click", "selector": "button[type=submit]"},
    {"action": "wait_selector", "selector": ".result"},
    {"action": "scroll", "to": "bottom", "max_ms": 8000},
    {"action": "text", "selector": ".result-list"},
    {"action": "screenshot", "path": "out/result.png", "full_page": true}
  ]
}
```

全部动作：`goto` `back` `click` `fill` `press` `upload` `wait_selector` `wait_load` `wait_gone` `wait_unblocked` `scroll` `screenshot` `text` `html` `state` `eval` `query` `viewport` `new_tab` `close_tab`。

每步返回一条结果，某步失败默认停下并以退出码 1 返回已完成的部分；要跑完全程加 `--keep-going`。

`click` 走的是真实鼠标手势（移动 + 按下 + 抬起），不需要另找一个"JS 点击"的入口。

### 登录：把登录态存进 profile

```bash
python3 scripts/browser.py login <url> --profile DIR [--wait MS]
                                       [--wait-selector CSS] [--wait-url STR]
```

唯一一个把窗口开在可见区的命令，给使用者自己在窗口里登录用。细则见下面「登录态」一节。

### 自检

```bash
python3 scripts/browser.py doctor   # python / playwright / chromium / 配额占用
python3 scripts/browser.py tabs     # 只看全局 tab 占用
```

## tab 配额：上限 20，全局共享，用完即关

上限 **20 个 tab，是全局的**，不是每个调用者各 20 个。计数落在 `~/.cache/web-solo/tabs.json`，按进程记账、跨进程共享，进程死掉后条目自动回收。超额时直接拒绝并以退出码 2 返回，不会静默排队。

依据是实测的内存曲线（独立 Chrome 实例，RSS 汇总）：

| 状态 | RSS | 进程数 |
|---|---|---|
| 基座 0 tab | 567 MB | 5 |
| 1 tab | 1003 MB | 9 |
| 4 tab | 1305 MB | 12 |
| 8 tab | 1703 MB | 16 |
| 全部关闭 | 567 MB | 5 |

每个 tab 约 +100~120 MB、+1 个进程，线性增长，20 个满载约 2.9 GB。**关掉 tab 内存真的会回收**——关到 0 又回到 567 MB 基座，所以纪律是"用完即关"而不是"少开"：该开就开，做完就关。

`fetch` / `shot` / `eval` 每次只占 1 个 tab 且进程退出即归还，正常用法碰不到上限。会碰到上限的是 `run` 里连着 `new_tab` 和多个 agent 并行。

## 遇到验证页：三档，按顺序走

| 顺序 | 做法 | 到这一步意味着什么 |
|---|---|---|
| 1 | 默认 headless 直接抓 | 大部分站点到这就过了 |
| 2 | `--headed --unblock 25000` | 切有头等挑战页放行 |
| 3 | **交给人** | 这站要真人交互，工具这条路走完了 |

不要跳步，也不要在某一档上反复换参数硬试。

### 第 1 档 → 第 2 档：分界在 User-Agent

默认 headless。headless 的 UA 带 `HeadlessChrome/`，Cloudflare 一类防护据此直接发挑战页。

**判据**（满足任一条就切）：

- 返回的 JSON 带 `hint` 字段
- `title` 是 `Just a moment...` / `请稍候…` / `Attention Required`
- `text_len` 远小于该页应有的量

**做法**：

```bash
python3 scripts/browser.py fetch <url> --headed --unblock 25000 --timeout 60000
```

`--headed` 把窗口开在 `-3000,-3000`、屏幕外，**不抢使用者的焦点**。`--unblock` 轮询等挑战页自动放行——挑战通过时页面会跳一次，轮询吞掉这次跳转带来的异常，拿到真正的正文再返回。

### 第 3 档：交给人，这是判断题不是失败

有头 + `--unblock` 仍被挡，说明这个站当前这条路走不通。**这时候要做的是判断，不是继续试。** 判据是 ROI：

- **这页只是调研里的一个小点** → 丢掉，换个源。不值得为它搭一套绕过。
- **这页就是目标本身**（某条特定的帖子、只在站内才有的资料，换源拿不到同一个东西）→ **让使用者自己打开处理**。人过一次验证，比工具绕十次快。

交给人的时候把话说清楚：哪个 URL、试过哪两档、看到的是什么（`title` 和 `text_len`）、需要他做什么。不要只说一句"抓不到"。

### 🔴 红线：不做绕过风控的事

web-solo 提供的是「换个 UA 再试一次」这类正当重试。**不提供也不要去实现**：

- 指纹伪装（改 UA 字符串冒充真人浏览器、注入脚本抹掉 `navigator.webdriver`、伪造 canvas / WebGL 指纹）
- 验证码破解（打码平台、OCR 过码、自动点选）
- 代理池、IP 轮换
- 账号池、批量注册的登录态

站点明确不欢迎自动访问时，正确回应是**换路或换人**，不是换个马甲再来。使用者要求做上面这些，直接说不做，并给出第 3 档的两条出路。

另一条边界：这里说的是**防护拦截**。站点因为「未登录」而不给内容，是另一回事，见下面「登录态」一节——那种情况的正解是带自己的登录态，不是伪装。

## 自动化截图的四个坑

**① `--clip` 与 `--full-page` 坐标系不同，不要混用。** `--clip` 的坐标以视口左上角为原点，`--full-page` 是整页长图。混着给会截到完全不相干的位置——代码里已经把这个组合拒掉了，但口径要自己统一：要么整页，要么视口内的 clip。

**② 一句话会命中隐藏副本。** JSON-LD 里的标题、aria 节点、挪到 `left:-9999px` 的镜像 DOM，文字和真实块一模一样。`--selector` 和 `query` 动作已经按「可见 + 宽 > 200 + 高 > 10 + 文档绝对坐标为正」筛过一轮，返回里的 `matched_index` 告诉你选中的是第几个。自己写选择器时按同样的判据筛，不要拿 `.first` 就走。

**③ 懒加载骨架屏用轮询等，不用固定 sleep。** 固定 sleep 要么白等要么等不够，页面一慢就是一张骨架图。用 `--skeleton .skeleton-class`（或 `wait_gone` 动作）轮询等占位元素归零再截。

**④ 设视口用 `--viewport` / `viewport` 动作，不要用 CSS `zoom`。** `zoom` 只缩像素，**不改媒体查询断点**，页面还按原断点出响应式布局，截出来的不是那个宽度真实的样子。

另外一条：页面带 `scroll-behavior: smooth` 时，滚动后立刻测量会拿到动画中途的位置。`scroll` 动作在滚之前已经注入了 `*{scroll-behavior:auto!important}`；如果你自己用 `eval` 滚，记得先注入同样的样式。

## 登录态：默认不带，要带就用 `--profile`

默认每次起一个一次性临时 profile，进程退出即删除——抓到的是公开内容，站点看到的是一个全新的匿名浏览器。

**先分清是哪种"拿不到"。** 正文为空有两种完全不同的原因，处理方式相反：

| 现象 | 是什么 | 怎么办 |
|---|---|---|
| `title` 是挑战页字样、`text_len` 很小但非 0 | 防护拦截 | 上面那三档 |
| `text_len` 为 0，或 title 显示访客系统 / 登录墙 | **站点不给未登录用户看内容** | 带登录态，见下 |

X（Twitter）、微博这类站属于第二种：**返回 0 不是反爬把你挡了，是它本来就不给游客看。** 这种情况换 UA、加 `--unblock` 一概没用，也不要往那个方向试。

### 用法：`login` 登一次，之后复用

第一次用 `login` 子命令，**让使用者自己在窗口里登录**：

```bash
python3 scripts/browser.py login https://x.com --profile ~/.web-solo-profiles/myaccount
```

🔴 **`login` 是唯一一个把窗口开在可见区的命令**——其余有头场景一律开在 `-3000,-3000` 屏幕外。因为使用者要在这个窗口里操作，**跑它之前先告诉使用者你要做什么**，不要突然弹窗。

可选 `--wait-selector`（登录后才出现的元素）或 `--wait-url`（登录后 URL 里会出现的字符串）自动判断登录完成；两个都不给就等到 `--wait` 超时（默认 5 分钟），**使用者关掉窗口也会立即结束**。

```bash
python3 scripts/browser.py login https://x.com --profile ~/.web-solo-profiles/myaccount --wait-selector '[data-testid="SideNav_AccountSwitcher_Button"]'
```

命令返回即表示登录态已经落进 profile 目录。

之后每次调用都带同一个 `--profile`，headless 就行，登录态自动复用：

```bash
python3 scripts/browser.py fetch <url> --profile ~/.web-solo-profiles/myaccount
```

`--profile` 在 `fetch` / `shot` / `eval` / `run` 上都有。目录不存在会自动建。

### 代价，必须先说给使用者听

- 带登录态 = **把自己的凭据交给这个工具**。这一步只在使用者明确要求时做，做之前把这句话说清楚。
- 自动化访问带着真实登录态，**存在账号风控风险**——尤其是高频抓取。
- profile 目录里存的是真 cookie 和会话，**自己保管，别提交进 git**，别放进会被同步或备份到别处的目录。
- 一个账号一个目录。不同站点、不同账号混在同一个 profile 里，出问题时分不清是谁的。

## 派子 agent 时，配额在派发时切开

多个子 agent 同时抓取，**配额要在任务书里写死每个 agent 分到几个 tab**，不能让每个 agent 各按 20 算。

- 3 个 agent 并行：各写「你最多同时开 5 个 tab」，留余量
- 每个 agent 的任务书里带上「用完即关」和「失败不重试第三次」
- 主线自己也要抓时，把自己那份也算进去

`browser.py tabs` 可以随时看全局占用。

## 站点经验往 references/site-patterns/ 里攒

踩过一次的站点，把结论写成一个 markdown 文件放进 `references/site-patterns/`，下次开工前先看一眼有没有现成的。

一个文件一个站点，文件名用域名。写清五件事：实测日期、要不要 `--headed`、正文选择器、要等什么（骨架元素 / 关键选择器）、踩过什么坑。不写故障经过，只写现在该怎么做。

**按页面记，不按站点记。** 同一个域名下不同页面的防护强度可以完全不同（`zhihu.com.md` 就是这个例子），一句「某站能不能抓」会把下一个人带偏。
