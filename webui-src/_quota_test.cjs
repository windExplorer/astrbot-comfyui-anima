const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const BASE = path.resolve(__dirname, "..", "pages", "anima-console-vue");

const MOCK = {
  "quota/users": {
    global: { enabled: true, max_total: 10, max_hour: 3, max_day: 5, admin_exempt: true },
    users: [
      { user_id: "1001", user_name: "alice", total_used: 2, hour_used: 1, day_used: 1, max_total: -1, max_hour: -1, max_day: -1 },
      { user_id: "1002", user_name: "bob", total_used: 0, hour_used: 0, day_used: 0, max_total: 5, max_hour: 2, max_day: -1 },
    ],
  },
};

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--no-proxy-server", "--proxy-bypass-list=*"],
  });
  const page = await browser.newPage();
  const logs = [];
  page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
  page.on("pageerror", (e) => logs.push(`[PAGEERROR] ${e.message}`));
  page.on("requestfailed", (r) => logs.push(`[REQFAIL] ${r.url()} ${r.failure() && r.failure().errorText}`));

  // mock fetch for /api/*
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const u = req.url();
    const m = u.match(/\/api\/(.+?)(\?.*)?$/);
    if (m) {
      const ep = m[1];
      const body = MOCK[ep];
      if (body !== undefined) {
        return req.respond({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
      }
      return req.respond({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    }
    req.continue();
  });

  const idx = path.join(BASE, "index.html");
  await page.goto("file://" + idx, { waitUntil: "networkidle0" });
  // 注入 standalone 标记（file:// 下无脚本注入），并触发 quota 路由
  await page.evaluate(() => { window.__ANIMA_STANDALONE__ = true; });
  // 跳到 #/quota
  await page.evaluate(() => { location.hash = "#/quota"; });
  await new Promise((r) => setTimeout(r, 1500));

  const info = await page.evaluate(() => {
    const root = document.querySelector(".quota-view");
    const errBanner = document.querySelector(".n-result, .n-message, [class*=error]");
    const rows = document.querySelectorAll(".n-data-table .n-data-table-tr");
    const headings = Array.from(document.querySelectorAll("h2,h3")).map((x) => x.textContent);
    return {
      hasRoot: !!root,
      tableRows: rows.length,
      headings,
      bodyText: (document.body.innerText || "").slice(0, 600),
    };
  });

  console.log("=== QUOTA PAGE INFO ===");
  console.log(JSON.stringify(info, null, 2));
  console.log("=== CONSOLE LOGS ===");
  console.log(logs.join("\n"));

  await browser.close();
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(1); });
