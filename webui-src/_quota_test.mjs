import puppeteer from "puppeteer-core";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const MOCK = {
  "quota/users": {
    global: { enabled: true, max_total: 10, max_hour: 3, max_day: 5, admin_exempt: true },
    users: [
      { user_id: "1001", user_name: "alice", total_used: 2, hour_used: 1, day_used: 1, max_total: null, max_hour: null, max_day: null },
      { user_id: "1002", user_name: "bob", total_used: 0, hour_used: 0, day_used: 0, max_total: 5, max_hour: 2, max_day: null },
    ],
  },
};
const POSTS = {};

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

  // 模拟 AstrBot 内嵌桥接：apiGet/apiPost 直接返回 json_response 的值（与 astrbot 行为一致）
  await page.evaluateOnNewDocument((mock) => {
    const M = mock;
    window.AstrBotPluginPage = {
      apiGet(endpoint, params) {
        return new Promise((resolve) => {
          setTimeout(() => resolve(M[endpoint] !== undefined ? M[endpoint] : {}), 50);
        });
      },
      apiPost(endpoint, body) {
        return new Promise((resolve) => {
          setTimeout(() => resolve({ ok: true }), 50);
        });
      },
    };
  }, MOCK);

  await page.goto("http://127.0.0.1:8899/", { waitUntil: "networkidle0" });
  await page.evaluate(() => { location.hash = "#/quota"; });
  await new Promise((r) => setTimeout(r, 1500));

  const info = await page.evaluate(() => {
    const root = document.querySelector(".quota-view");
    const rows = document.querySelectorAll(".n-data-table .n-data-table-tr");
    const cells = Array.from(rows).map((tr) =>
      Array.from(tr.querySelectorAll("td")).map((td) => (td.innerText || "").trim())
    );
    return { hasRoot: !!root, tableRows: rows.length, cells, bodyText: (document.body.innerText || "").slice(0, 400) };
  });

  console.log("=== QUOTA (astrbot mock) INFO ===");
  console.log(JSON.stringify(info, null, 2));
  console.log("=== CONSOLE LOGS ===");
  console.log(logs.join("\n"));

  await browser.close();
})().catch((e) => { console.error("TEST ERROR:", e); process.exit(1); });
