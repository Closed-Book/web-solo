# web-solo · Agent 操作手册

零端口的网页访问工具。核心是一个 Python CLI，**不绑定任何 agent 框架**——本文件是给任意 coding agent 读的操作说明。

> Claude Code 用户：本仓库同时提供 `SKILL.md`（skill 格式，含路由决策规则）与 `.claude-plugin/`，装进 `~/.claude/skills/` 即可。
> 其他框架：忽略那两个，按本文件调 CLI。

---

## 开工第一步

```bash
bash scripts/setup.sh
```

幂等：检测 Python / Playwright / Chromium，缺什么装什么，齐了秒退（0.2–0.6 秒）。空环境首装约 45 秒（下载 Chromium 约 95 MB）。**任何浏览器操作前先跑它**，不要假设环境已就绪。

---

## 什么时候该用这个工具

先判断网页属于哪一类，**不要一上来就起浏览器**：

| 网页类型 | 例子 | 处理 |
|---|---|---|
| 静态页、文档站 | Wikipedia · GitHub · 官方 docs | **用你宿主自带的网页抓取**，快一个量级，不占内存 |
| 反爬站 | 小红书 · B 站 · 知乎 | `browser.py`，headless 直接过 |
| 需要登录 | X · 微博 | `browser.py` + `--profile`（见下） |
| 需要交互 | 点击、填表、滚动加载、上传 | `browser.py run` |

第一类占日常任务的大半。**能不起浏览器就别起。**

---

## 命令

全部输出 JSON，失败时输出 `{"error": "...", ...}` 并以非零码退出。

```bash
python3 scripts/browser.py fetch  <url>                    # 取正文/HTML
python3 scripts/browser.py shot   <url> --out FILE         # 截图
python3 scripts/browser.py eval   <url> --js 'EXPR'        # 执行 JS 取值
python3 scripts/browser.py run    steps.json               # 多步交互
python3 scripts/browser.py tabs                            # 看配额占用
python3 scripts/browser.py doctor                          # 环境自检
```

通用参数：`--headed`（有头，窗口开在屏幕外）· `--profile DIR`（持久化登录态）· `--timeout MS` · `--unblock MS`（等挑战页放行）。

### 多步交互

一次会话里串起一连串动作，不为每步重开浏览器：

```json
{
  "steps": [
    {"action": "goto", "url": "https://example.com"},
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

---

## 三条必须遵守的纪律

### 1. tab 配额：上限 20，跨进程全局共享

**不是每个调用者各 20。** 派并行子任务时，在派发阶段就把配额切开写进各自的指令（如 4 个子任务各 5 个），不能让每个都按 20 算。

每个标签页约 100~120 MB 和一个进程，20 个满载约 2.9 GB。关闭会真正回收内存，所以**拿到数据当场关，不要攒到任务结束**。`tabs` 命令看当前占用。

### 2. 遇到验证页，按代价从低到高，不要跳步

| 顺序 | 做法 |
|---|---|
| 1 | 默认 headless 直接抓 —— 大部分站到这就过了 |
| 2 | `--headed --unblock 25000` —— 防护认的是 UA 里的 `HeadlessChrome/` |
| 3 | **交给人** —— 前两步都不行，说明这站要真人交互 |

第 3 步不是失败，是判断题：这页只是调研里的一个小点 → 丢掉换个源；这页就是目标本身 → **告诉使用者，让他自己打开处理**，人过一次验证比工具绕十次都快。

🔴 **不要尝试绕过风控。** 不做指纹伪装、验证码破解、代理池、账号池。站点明确不欢迎自动访问时，正确回应是换路或换人。

### 3. 登录墙和反爬是两回事

X、微博这类站返回空白**不是被反爬拦住**，是站点不给游客看内容。别在这种站上反复切有头、加等待——那解决不了问题。

正解是带登录态：第一次有头打开让使用者登一次，之后复用。

```bash
# 第一次：使用者自己在窗口里登录
python3 scripts/browser.py run login.json --headed --profile ~/.web-solo-profiles/me --timeout 300000
# 之后：headless 复用
python3 scripts/browser.py fetch <url> --profile ~/.web-solo-profiles/me
```

profile 目录里是真 cookie，**不要提交进 git**，也不要在未经使用者同意时创建。

---

## 截图的四个坑（都不报错，只给错图）

1. **坐标系**：`clip` 与 `full_page` 的坐标基准不同，混用会截到错误区域。统一口径，不要混。
2. **隐藏副本**：JSON-LD、aria 节点会让同一句文本命中多次。按可见性 + 尺寸 + 文档绝对坐标三道筛，只按尺寸筛会选中 `left:-9999px` 的幽灵块。
3. **懒加载骨架屏**：固定 `sleep` 不可靠。轮询骨架元素归零再截。
4. **视口断点**：设视口要用真正的 viewport 参数，CSS `zoom` **不改媒体查询断点**，响应式布局会一直停在窄屏形态。

另：页面的 `scroll-behavior: smooth` 会让滚动后的立即测量拿到中途位置，注入 `*{scroll-behavior:auto!important}` 再滚。

---

## 站点经验

`references/site-patterns/` 下按站记录实测过的行为：哪档能过、拿到多少内容、失效信号是什么。**抓完一个新站，把经验写回去**——尤其是「同一个站不同页面防护强度不同」这类，光记站名没用。

---

## 环境变量

| 变量 | 作用 |
|---|---|
| `WEB_SOLO_MAX_TABS` | 改 tab 上限（默认 20），改之前先算内存 |
| `WEB_SOLO_STATE` | 改配额状态文件位置（默认 `~/.cache/web-solo`） |
| `WEB_SOLO_NO_SANDBOX=1` | 关闭 chromium sandbox。**仅在容器或 root 下启动失败时用**，代价是失去进程隔离 |

---

## 平台

macOS 实测通过。Linux 未实测（容器或 root 下若启动失败，用 `WEB_SOLO_NO_SANDBOX=1`）。**Windows 未适配** —— 需要改的点在 `scripts/browser.py` 里用 `[WINDOWS]` 注释标了出来，`setup.sh` 需走 WSL 或 Git Bash。
