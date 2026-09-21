#!/usr/bin/env python3
"""web-solo 浏览器执行层。

零端口：走 Playwright 的 launch_persistent_context，Chromium 以
remote-debugging-pipe 与本进程通信，不监听任何 TCP 端口。
用完即退：进程退出前关闭 context、删除临时 profile、归还 tab 配额。
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # 类 Unix 才有；缺了就退化成不加锁，配额仍然记账
except ImportError:
    fcntl = None
# [WINDOWS] POSIX 专有：fcntl / flock。Windows 上走 fcntl is None 分支，配额照样记账，
# 但多进程并发读改写 tabs.json 时没有互斥，计数可能偏少。要适配就换 msvcrt.locking
# 或一个跨平台文件锁库。

MAX_TABS = int(os.environ.get("WEB_SOLO_MAX_TABS", "20"))

# 默认开 Chromium 沙箱。抓的是不受信任的外部页面，渲染进程必须被隔离。
# 容器 / root 环境下沙箱要额外的内核权限（SYS_ADMIN capability 或 seccomp 配置），
# 起不来时用 WEB_SOLO_NO_SANDBOX=1 显式关掉——这是使用者的选择，代价是失去进程隔离。
SANDBOX = os.environ.get("WEB_SOLO_NO_SANDBOX", "") != "1"
# [WINDOWS] 路径惯例：~/.cache 是 XDG 约定，Windows 下该落 %LOCALAPPDATA%。
# Path.home() 本身跨平台可用，所以能跑，只是位置不符合平台惯例；用 WEB_SOLO_STATE
# 环境变量可以覆盖。
STATE_DIR = Path(os.environ.get("WEB_SOLO_STATE", str(Path.home() / ".cache" / "web-solo")))
REGISTRY = STATE_DIR / "tabs.json"
LOCKFILE = STATE_DIR / "tabs.lock"

# 有头模式把窗口开在屏幕外，不抢使用者的焦点。
# [WINDOWS] 负坐标未验证：macOS / X11 接受窗口落在可见区域之外，Windows 的窗口管理器
# 可能把它钳回虚拟屏幕范围内——那样窗口会直接出现在使用者屏幕上。适配前先在真机上
# 确认 --window-position 负值的实际落点。
OFFSCREEN = (-3000, -3000)

# 命中这些串基本可以判定是反爬拦截页而不是正文。
BLOCK_MARKERS = (
    "just a moment",
    "checking your browser",
    "verify you are human",
    "attention required",
    "enable javascript and cookies",
    "请稍候",
    "请稍等",
    "安全验证",
)


class TabLimit(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 全局 tab 配额：跨进程共享，registry 记录每个活着的 web-solo 进程占了几个 tab
# --------------------------------------------------------------------------
def _alive(pid: int) -> bool:
    # [WINDOWS] POSIX 专有：os.kill(pid, 0) 在类 Unix 上是「只探活、不发信号」。
    # Windows 的 os.kill 对非 CTRL_*_EVENT 的 sig 一律走 TerminateProcess——传 0 会
    # 真的把那个进程杀掉。适配时必须整段换掉（OpenProcess + GetExitCodeProcess，
    # 或 psutil.pid_exists），不能原样跑。
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    with open(LOCKFILE, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read_registry() -> dict:
    try:
        raw = json.loads(REGISTRY.read_text())
    except Exception:
        return {}
    return {
        k: v
        for k, v in raw.items()
        if k.isdigit() and isinstance(v, int) and v > 0 and _alive(int(k))
    }


def _write_registry(data: dict) -> None:
    REGISTRY.write_text(json.dumps(data))


def registry_snapshot() -> dict:
    with _locked():
        data = _read_registry()
        _write_registry(data)  # 顺手把死进程的条目清掉
    return {"max_tabs": MAX_TABS, "in_use": sum(data.values()), "by_pid": data}


class TabBudget:
    """按进程记账的全局 tab 配额。"""

    def __init__(self) -> None:
        self.pid = str(os.getpid())
        self.count = 0
        atexit.register(self.release_all)

    def reserve(self, n: int = 1) -> None:
        with _locked():
            data = _read_registry()
            others = sum(v for k, v in data.items() if k != self.pid)
            if others + self.count + n > MAX_TABS:
                raise TabLimit(
                    f"tab 配额已满：全局上限 {MAX_TABS}，其它进程占用 {others}，"
                    f"本进程已占用 {self.count}。先关掉不用的 tab 再来。"
                )
            self.count += n
            data[self.pid] = self.count
            _write_registry(data)

    def free(self, n: int = 1) -> None:
        with _locked():
            data = _read_registry()
            self.count = max(0, self.count - n)
            if self.count:
                data[self.pid] = self.count
            else:
                data.pop(self.pid, None)
            _write_registry(data)

    def release_all(self) -> None:
        if self.count <= 0:
            return
        with _locked():
            data = _read_registry()
            data.pop(self.pid, None)
            _write_registry(data)
        self.count = 0


# --------------------------------------------------------------------------
# 浏览器会话
# --------------------------------------------------------------------------
class Session:
    def __init__(
        self,
        headed: bool = False,
        profile: str | None = None,
        viewport: tuple[int, int] = (1280, 1600),
        timeout_ms: int = 30000,
        visible: bool = False,
    ) -> None:
        self.headed = headed
        # 只有 login 命令会把 visible 打开：使用者要在窗口里自己操作。
        # 其余一切有头场景都开在屏幕外，不抢使用者焦点。
        self.visible = visible
        self.viewport = viewport
        self.timeout_ms = timeout_ms
        self.profile = profile
        self._tmp_profile: str | None = None
        self.budget = TabBudget()
        self._pw = None
        self.ctx = None
        self.page = None

    def __enter__(self) -> "Session":
        from playwright.sync_api import sync_playwright

        self.budget.reserve(1)  # persistent context 自带一个 page
        try:
            if self.profile:
                user_data_dir = os.path.expanduser(self.profile)
                os.makedirs(user_data_dir, exist_ok=True)
            else:
                # [WINDOWS] tempfile 本身跨平台；要留意的是 close() 里的 rmtree：
                # Windows 不允许删除仍被打开的文件，Chromium 子进程退出稍慢就会删不掉，
                # 而 ignore_errors=True 会把失败吞掉，临时 profile 静默留在盘上。
                self._tmp_profile = tempfile.mkdtemp(prefix="web-solo-profile-")
                user_data_dir = self._tmp_profile

            w, h = self.viewport
            args = [
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
            ]
            if self.headed:
                args.append(f"--window-size={w},{h}")
                if not self.visible:
                    args.append(f"--window-position={OFFSCREEN[0]},{OFFSCREEN[1]}")

            self._pw = sync_playwright().start()
            self.ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=not self.headed,
                args=args,
                viewport={"width": w, "height": h},
                ignore_default_args=["--enable-automation"],
                # Playwright 的 chromium_sandbox 默认是 False，那会让它自己往命令行里
                # 补一个关闭沙箱的开关。这里必须显式传值：删掉这一行会静默退回那个默认，
                # 而且代码里看不出来——只有 ps 看运行中的进程命令行才发现得了。
                chromium_sandbox=SANDBOX,
            )
            self.ctx.set_default_timeout(self.timeout_ms)
            pages = list(self.ctx.pages)
            self.page = pages[0] if pages else self.ctx.new_page()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self.ctx is not None:
                self.ctx.close()
        except Exception:
            pass
        finally:
            self.ctx = None
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._pw = None
        if self._tmp_profile:
            # [WINDOWS] 见 __enter__ 里的说明：这里的失败是静默的，Windows 上要加重试。
            shutil.rmtree(self._tmp_profile, ignore_errors=True)
            self._tmp_profile = None
        self.budget.release_all()

    # -- tab ---------------------------------------------------------------
    def new_tab(self):
        self.budget.reserve(1)
        try:
            self.page = self.ctx.new_page()
        except BaseException:
            self.budget.free(1)
            raise
        return self.page

    def close_tab(self) -> None:
        if len(self.ctx.pages) <= 1:
            raise RuntimeError("只剩一个 tab，关掉就没有可操作的页面了")
        self.page.close()
        self.budget.free(1)
        self.page = self.ctx.pages[-1]


# --------------------------------------------------------------------------
# 页面动作
# --------------------------------------------------------------------------
def kill_smooth_scroll(page) -> None:
    """页面自带 scroll-behavior: smooth 时，滚动后的立即测量会取到中途位置。"""
    try:
        page.add_style_tag(content="*{scroll-behavior:auto !important}")
    except Exception:
        pass


def block_hint(title: str, text: str) -> str | None:
    probe = (title + " " + text[:2000]).lower()
    if any(m in probe for m in BLOCK_MARKERS):
        return "疑似反爬拦截页：加 --headed 重试"
    if len(text.strip()) < 80:
        return "正文几乎为空：可能被拦截或内容靠 JS 晚出，加 --headed 或 --wait-selector 重试"
    return None


def real_blocks(page, selector: str, min_w: int = 200, min_h: int = 10) -> list[dict]:
    """筛掉隐藏副本（JSON-LD / aria 节点 / 挪到屏幕外的镜像），只留真实渲染块。

    三道筛：不可见的丢掉；尺寸不够的丢掉；整体落在文档左上方之外（left:-9999px
    这类）的丢掉——这种副本尺寸完全合法，只看宽高筛不出来。
    """
    loc = page.locator(selector)
    # bounding_box 给的是视口相对坐标，页面滚过之后正常元素的 y 也会是负的。
    # 加上滚动偏移换算成文档绝对坐标，才能把 left:-9999px 这类副本和它区分开。
    sx, sy = page.evaluate("() => [window.scrollX, window.scrollY]")
    out = []
    for i in range(loc.count()):
        item = loc.nth(i)
        try:
            if not item.is_visible():
                continue
            box = item.bounding_box()
        except Exception:
            continue
        if not box:
            continue
        if box["width"] <= min_w or box["height"] <= min_h:
            continue
        if box["x"] + sx + box["width"] <= 0 or box["y"] + sy + box["height"] <= 0:
            continue
        out.append({"index": i, "box": box})
    return out


def pick_block(page, selector: str, min_w: int = 200, min_h: int = 10):
    hits = real_blocks(page, selector, min_w, min_h)
    if hits:
        return page.locator(selector).nth(hits[0]["index"]), hits[0]["index"]
    return page.locator(selector).first, 0


def wait_gone(page, selector: str, max_ms: int = 10000, interval_ms: int = 250) -> bool:
    """轮询等骨架屏 / loading 占位归零，替代固定 sleep。"""
    deadline = time.time() + max_ms / 1000
    while time.time() < deadline:
        try:
            if page.locator(selector).count() == 0:
                return True
        except Exception:
            return True
        page.wait_for_timeout(interval_ms)
    return False


def wait_unblocked(page, max_ms: int = 25000, interval_ms: int = 500) -> bool:
    """轮询等反爬挑战页自动放行。挑战通过时会发生一次导航，期间取值会抛异常，忽略即可。"""
    deadline = time.time() + max_ms / 1000
    while time.time() < deadline:
        try:
            if not block_hint(page.title(), page.inner_text("body")):
                return True
        except Exception:
            pass
        page.wait_for_timeout(interval_ms)
    return False


def scroll_page(page, to: str = "bottom", max_ms: int = 8000, skeleton: str | None = None) -> dict:
    kill_smooth_scroll(page)
    if to == "top":
        page.evaluate("window.scrollTo(0, 0)")
        return {"scrolled_to": "top", "height": page.evaluate("document.body.scrollHeight")}

    deadline = time.time() + max_ms / 1000
    last_h = -1
    rounds = 0
    at_bottom = False
    probe = ("() => ({h: document.documentElement.scrollHeight, "
             "y: window.scrollY, vh: window.innerHeight})")
    while time.time() < deadline:
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(300)
        rounds += 1
        m = page.evaluate(probe)
        # 停止判据要同时满足「到底了」和「高度不再长」，只看高度会在静态长页上提前停。
        at_bottom = m["y"] + m["vh"] >= m["h"] - 4
        skeleton_left = page.locator(skeleton).count() if skeleton else 0
        if at_bottom and m["h"] == last_h and not skeleton_left:
            break
        last_h = m["h"]
    return {"scrolled_to": to, "rounds": rounds, "height": last_h,
            "at_bottom": at_bottom, "settled": time.time() < deadline}


def page_state(page, fmt: str = "text") -> dict:
    title = page.title()
    text = page.inner_text("body")
    state = {"url": page.url, "title": title, "text_len": len(text)}
    hint = block_hint(title, text)
    if hint:
        state["hint"] = hint
    if fmt == "text":
        state["body"] = text
    elif fmt == "html":
        state["body"] = page.content()
    elif fmt == "title":
        state["body"] = title
    return state


def do_screenshot(page, path: str, full_page: bool = False, selector: str | None = None,
                  clip: str | None = None) -> dict:
    if clip and full_page:
        raise ValueError("--clip 与 --full-page 坐标系不同，不要混用：要么整页，要么给视口内的 clip")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    meta = {"path": os.path.abspath(path)}
    if selector:
        target, idx = pick_block(page, selector)
        target.screenshot(path=path)
        meta.update({"mode": "element", "selector": selector, "matched_index": idx})
    elif clip:
        x, y, w, h = [float(v) for v in clip.split(",")]
        page.screenshot(path=path, clip={"x": x, "y": y, "width": w, "height": h})
        meta.update({"mode": "clip", "clip": [x, y, w, h]})
    else:
        page.screenshot(path=path, full_page=full_page)
        meta.update({"mode": "full_page" if full_page else "viewport"})
    meta["bytes"] = os.path.getsize(path)
    return meta


def run_step(sess: Session, step: dict) -> dict:
    page = sess.page
    action = step.get("action")
    if action == "goto":
        page.goto(step["url"], wait_until=step.get("wait_until", "domcontentloaded"))
        if step.get("wait_selector"):
            page.wait_for_selector(step["wait_selector"])
        return {"action": action, "url": page.url}
    if action == "back":
        page.go_back()
        return {"action": action, "url": page.url}
    if action == "click":
        page.click(step["selector"])  # Playwright 的 click 本身就是真实鼠标手势
        return {"action": action, "selector": step["selector"]}
    if action == "fill":
        page.fill(step["selector"], step["value"])
        return {"action": action, "selector": step["selector"]}
    if action == "press":
        page.press(step.get("selector", "body"), step["key"])
        return {"action": action, "key": step["key"]}
    if action == "upload":
        page.set_input_files(step["selector"], step["files"])
        return {"action": action, "files": step["files"]}
    if action == "wait_selector":
        page.wait_for_selector(step["selector"], state=step.get("state", "visible"))
        return {"action": action, "selector": step["selector"]}
    if action == "wait_load":
        page.wait_for_load_state(step.get("state", "networkidle"))
        return {"action": action, "state": step.get("state", "networkidle")}
    if action == "wait_gone":
        ok = wait_gone(page, step["selector"], step.get("max_ms", 10000))
        return {"action": action, "selector": step["selector"], "gone": ok}
    if action == "wait_unblocked":
        ok = wait_unblocked(page, step.get("max_ms", 25000))
        return {"action": action, "unblocked": ok}
    if action == "scroll":
        return {"action": action, **scroll_page(page, step.get("to", "bottom"),
                                                step.get("max_ms", 8000), step.get("skeleton"))}
    if action == "screenshot":
        return {"action": action, **do_screenshot(page, step["path"], step.get("full_page", False),
                                                  step.get("selector"), step.get("clip"))}
    if action == "text":
        sel = step.get("selector", "body")
        return {"action": action, "selector": sel, "text": page.inner_text(sel)}
    if action == "html":
        return {"action": action, "html": page.content()}
    if action == "state":
        return {"action": action, **page_state(page, step.get("format", "title"))}
    if action == "eval":
        return {"action": action, "value": page.evaluate(step["js"])}
    if action == "query":
        return {"action": action, "selector": step["selector"],
                "blocks": real_blocks(page, step["selector"],
                                      step.get("min_w", 200), step.get("min_h", 10))}
    if action == "viewport":
        page.set_viewport_size({"width": step["width"], "height": step["height"]})
        return {"action": action, "width": step["width"], "height": step["height"]}
    if action == "new_tab":
        sess.new_tab()
        return {"action": action, "tabs": len(sess.ctx.pages)}
    if action == "close_tab":
        sess.close_tab()
        return {"action": action, "tabs": len(sess.ctx.pages)}
    raise ValueError(f"未知动作：{action}")


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def session_from_args(args) -> Session:
    w, h = (int(v) for v in args.viewport.split("x"))
    return Session(headed=args.headed, profile=args.profile, viewport=(w, h),
                   timeout_ms=args.timeout)


def cmd_fetch(args) -> int:
    with session_from_args(args) as sess:
        sess.page.goto(args.url, wait_until=args.wait_until)
        if args.unblock:
            wait_unblocked(sess.page, args.unblock)
        if args.wait_selector:
            sess.page.wait_for_selector(args.wait_selector)
        if args.scroll:
            scroll_page(sess.page, "bottom", args.scroll)
        state = page_state(sess.page, args.format)
        body = state.pop("body", "")
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(body, encoding="utf-8")
            state["out"] = os.path.abspath(args.out)
        elif args.raw:
            sys.stdout.write(body + "\n")
            print(json.dumps(state, ensure_ascii=False), file=sys.stderr)
            return 0
        else:
            state["body"] = body
        return emit(state)


def cmd_shot(args) -> int:
    with session_from_args(args) as sess:
        sess.page.goto(args.url, wait_until=args.wait_until)
        if args.unblock:
            wait_unblocked(sess.page, args.unblock)
        if args.wait_selector:
            sess.page.wait_for_selector(args.wait_selector)
        if args.skeleton:
            wait_gone(sess.page, args.skeleton, args.timeout)
        if args.scroll:
            scroll_page(sess.page, "bottom", args.scroll)
        meta = do_screenshot(sess.page, args.out, args.full_page, args.selector, args.clip)
        meta.update({"url": sess.page.url, "title": sess.page.title()})
        return emit(meta)


def cmd_eval(args) -> int:
    with session_from_args(args) as sess:
        sess.page.goto(args.url, wait_until=args.wait_until)
        if args.unblock:
            wait_unblocked(sess.page, args.unblock)
        if args.wait_selector:
            sess.page.wait_for_selector(args.wait_selector)
        return emit({"url": sess.page.url, "value": sess.page.evaluate(args.js)})


def cmd_run(args) -> int:
    raw = sys.stdin.read() if args.steps == "-" else Path(args.steps).read_text(encoding="utf-8")
    plan = json.loads(raw)
    steps = plan["steps"] if isinstance(plan, dict) else plan
    headed = args.headed or (isinstance(plan, dict) and plan.get("headed", False))
    vp = plan.get("viewport", [1280, 1600]) if isinstance(plan, dict) else [1280, 1600]
    results = []
    with Session(headed=headed, profile=args.profile, viewport=(vp[0], vp[1]),
                 timeout_ms=args.timeout) as sess:
        for i, step in enumerate(steps):
            try:
                results.append({"step": i, "ok": True, **run_step(sess, step)})
            except Exception as exc:
                results.append({"step": i, "ok": False, "action": step.get("action"),
                                "error": f"{type(exc).__name__}: {exc}"})
                if not args.keep_going:
                    return emit({"completed": i, "results": results}, 1)
    return emit({"completed": len(steps), "results": results})


def cmd_login(args) -> int:
    """有头打开一个页面，等使用者自己登录完，把登录态留在 --profile 里。

    这是唯一一个窗口必须可见的命令——使用者要在里面操作。其余有头场景
    一律开在屏幕外（见 Session.visible）。
    """
    w, h = (int(v) for v in args.viewport.split("x"))
    profile_dir = os.path.expanduser(args.profile)
    detected = None
    before = after = 0

    with Session(headed=True, visible=True, profile=args.profile,
                 viewport=(w, h), timeout_ms=args.timeout) as sess:
        sess.page.goto(args.url, wait_until="domcontentloaded")
        before = len(sess.ctx.cookies())
        print(
            f"窗口已打开，请在里面完成登录。最多等 {args.wait // 1000} 秒；"
            f"登录完可以直接关掉窗口。",
            file=sys.stderr,
        )

        deadline = time.time() + args.wait / 1000.0
        while time.time() < deadline:
            try:
                if not sess.ctx.pages:          # 使用者自己关了窗口 = 登录完了
                    detected = "window-closed"
                    break
                if args.wait_selector and sess.page.locator(
                        args.wait_selector).first.is_visible(timeout=500):
                    detected = "wait-selector"
                    break
                if args.wait_url and args.wait_url in sess.page.url:
                    detected = "wait-url"
                    break
            except Exception:
                pass                            # 页面跳转途中取 URL/元素会抛，忽略继续等
            time.sleep(1)

        try:
            after = len(sess.ctx.cookies())
        except Exception:
            after = before

    # Cookies 文件在 context 关闭时才刷盘，所以放到 with 外面查
    store = None
    for cand in ("Default/Cookies", "Cookies"):
        f = os.path.join(profile_dir, cand)
        if os.path.exists(f):
            store = f
            break

    return emit({
        "profile": os.path.abspath(profile_dir),
        "detected_by": detected or "timeout",
        "cookies": {"before": before, "after": after},
        "cookie_store": store,
        "next": f"之后带 --profile {args.profile} 跑 headless 就能复用这份登录态",
    }, 0 if store else 1)


def cmd_tabs(args) -> int:
    return emit(registry_snapshot())


def cmd_doctor(args) -> int:
    info = {"python": sys.executable, "python_version": sys.version.split()[0]}
    try:
        import importlib.metadata as md

        info["playwright"] = md.version("playwright")
    except Exception as exc:
        info["playwright"] = f"缺失（{exc}）"
        return emit(info, 1)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        info["chromium"] = path
        info["chromium_installed"] = os.path.exists(path)
    except Exception as exc:
        info["chromium"] = f"查不到（{exc}）"
        info["chromium_installed"] = False
    info["tabs"] = registry_snapshot()
    return emit(info, 0 if info.get("chromium_installed") else 1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="browser.py", description="web-solo 浏览器执行层（零端口、用完即退）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_url=True):
        if with_url:
            sp.add_argument("url")
        sp.add_argument("--headed", action="store_true", help="有头模式，窗口开在屏幕外；反爬站用这个")
        sp.add_argument("--profile", help="持久化 profile 目录；不给就用一次性临时 profile")
        sp.add_argument("--viewport", default="1280x1600")
        sp.add_argument("--timeout", type=int, default=30000, help="单步超时（毫秒）")
        sp.add_argument("--wait-until", default="domcontentloaded",
                        choices=["commit", "domcontentloaded", "load", "networkidle"])
        sp.add_argument("--wait-selector", help="导航后等这个选择器出现再往下走")
        sp.add_argument("--unblock", type=int, metavar="MAX_MS",
                        help="轮询等反爬挑战页自动放行，配 --headed 用")

    sp = sub.add_parser("fetch", help="打开一个页面取正文 / HTML")
    common(sp)
    sp.add_argument("--format", default="text", choices=["text", "html", "title"])
    sp.add_argument("--out", help="正文写到文件，stdout 只留元信息")
    sp.add_argument("--raw", action="store_true", help="正文直接打到 stdout，元信息走 stderr")
    sp.add_argument("--scroll", type=int, metavar="MAX_MS", help="取内容前滚到底，轮询触发懒加载")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("shot", help="截图")
    common(sp)
    sp.add_argument("--out", required=True)
    sp.add_argument("--full-page", action="store_true")
    sp.add_argument("--selector", help="元素截图；自动跳过隐藏副本")
    sp.add_argument("--clip", help="x,y,w,h（视口坐标系，不能和 --full-page 混用）")
    sp.add_argument("--skeleton", help="骨架屏选择器；轮询等它归零再截")
    sp.add_argument("--scroll", type=int, metavar="MAX_MS")
    sp.set_defaults(func=cmd_shot)

    sp = sub.add_parser("eval", help="在页面里执行 JS 并取回结果")
    common(sp)
    sp.add_argument("--js", required=True)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("run", help="按 JSON 步骤表做一串交互（一次会话内完成）")
    sp.add_argument("steps", help="步骤表文件路径，或 - 从 stdin 读")
    sp.add_argument("--headed", action="store_true")
    sp.add_argument("--profile")
    sp.add_argument("--timeout", type=int, default=30000)
    sp.add_argument("--keep-going", action="store_true", help="某步失败也继续往下跑")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("login", help="有头打开页面，等你自己登录，把登录态存进 --profile")
    sp.add_argument("url")
    sp.add_argument("--profile", required=True, help="登录态保存目录（必填）")
    sp.add_argument("--viewport", default="1280x1600")
    sp.add_argument("--timeout", type=int, default=30000)
    sp.add_argument("--wait", type=int, default=300000,
                    help="最多等多久（毫秒），默认 5 分钟；登录完直接关窗口也会立刻结束")
    sp.add_argument("--wait-selector", help="登录成功的标志元素，出现即提前结束")
    sp.add_argument("--wait-url", help="登录后 URL 里会出现的字符串，匹配即提前结束")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("tabs", help="看全局 tab 配额占用")
    sp.set_defaults(func=cmd_tabs)

    sp = sub.add_parser("doctor", help="自检：python / playwright / chromium / 配额")
    sp.set_defaults(func=cmd_doctor)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except TabLimit as exc:
        return emit({"error": "tab_limit", "message": str(exc)}, 2)
    except Exception as exc:
        return emit({"error": type(exc).__name__, "message": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main())
