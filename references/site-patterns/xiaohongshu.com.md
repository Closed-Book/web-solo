# xiaohongshu.com

实测日期：2026-09-22

- **模式**：headless 即可，第 1 档就过。不需要 `--headed`，也不需要 `--unblock`。
- **参数**：

  ```bash
  python3 scripts/browser.py fetch https://www.xiaohongshu.com/explore
  ```

- **正文选择器**：整页 `body` 的 innerText，explore 页实测 1618 字符（笔记标题 + 作者 + 互动数的信息流）。
- **要等什么**：不用额外等待，`domcontentloaded` 即可拿到上面这个量。要更多信息流条目就加 `--scroll`，它是懒加载的。
- **坑**：
  - 1618 字符是**未登录能看到的公开信息流**。笔记正文、评论这些要登录态，没有 `--profile` 就是拿不到，不要误判成抓取失败去加参数重试。
  - 信息流内容每次刷新都不一样，同一个 URL 两次抓到的字符数会差很多。拿字符数做健康判据时用量级（几百 vs 一千多），不要卡具体数字。
