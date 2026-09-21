# dpreview.com

- **模式**：必须 `--headed`。headless 稳定拿到 Cloudflare 挑战页，`title` 为 `Just a moment...`，正文 0–264 字符。
- **参数**：

  ```bash
  python3 scripts/browser.py fetch https://www.dpreview.com/news \
      --headed --unblock 25000 --timeout 60000 --out news.txt
  ```

- **正文选择器**：整页 `body` 的 innerText 即可，新闻列表页约 9400 字符。
- **要等什么**：`--unblock 25000`。挑战页会在十几秒内自动放行，放行时发生一次导航，url 从带 `__cf_chl_rt_tk=` 的参数跳回干净地址。
- **坑**：只加 `--headed` 不加 `--unblock`，会在挑战页还没走完的时候就把 `请稍候…` 当正文取走（118 字符）。判断依据看 `text_len` 和返回里的 `hint`，不要看 HTTP 状态码——挑战页也是 200。
