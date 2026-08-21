// 使用真实 Chrome 访问运行中的监视端并生成可复验的界面佐证。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.CODEX_NODE_MODULES, "playwright"));

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(sourceRoot, "..");
const output = path.join(projectRoot, "佐证材料");
const frames = path.join(output, "gif-frames");
fs.mkdirSync(frames, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
await page.goto("http://127.0.0.1:9010", { waitUntil: "networkidle" });
await page.waitForSelector("#pointRows tr[data-id='YC01']", { timeout: 15000 });
await page.waitForFunction(() => document.querySelector("#connectionPill")?.classList.contains("connected"));

// 主界面同时展示完整字段、连接状态、自动刷新和六页分页。
await page.screenshot({ path: path.join(output, "01-运行主界面.png"), fullPage: true });

// 连续帧来自真实1秒自动刷新，稍后由 Pillow 合成为动态 GIF。
for (let index = 0; index < 7; index += 1) {
  await page.screenshot({ path: path.join(frames, `frame-${String(index).padStart(2, "0")}.png`) });
  await page.waitForTimeout(1050);
}

// 第二页证明超过20条自动分页，页脚应显示“第2/6页”。
await page.getByRole("button", { name: "2", exact: true }).click();
await page.waitForTimeout(500);
await page.setViewportSize({ width: 1440, height: 1500 });
await page.evaluate(() => window.scrollTo(0, 0));
await page.screenshot({ path: path.join(output, "02-自动分页.png") });

// 回到遥测第一页，打开 YC01 的真实历史曲线。
await page.setViewportSize({ width: 1440, height: 1000 });
await page.getByRole("button", { name: "1", exact: true }).click();
await page.waitForSelector("#pointRows tr[data-id='YC01']");
await page.locator("#pointRows tr[data-id='YC01']").click();
await page.waitForTimeout(800);
await page.screenshot({ path: path.join(output, "03-历史曲线.png") });

// 完成人工置数，截图应显示0x20、人工替代和置数标签。
await page.locator("#openManual").click();
await page.locator("#manualValue").fill("225.55");
await page.locator("#submitManual").click();
await page.waitForFunction(() => document.querySelector("#drawerQuality")?.textContent?.includes("0x20"));
await page.waitForTimeout(400);
await page.screenshot({ path: path.join(output, "04-人工置数.png") });

// 演示结束后恢复自动模拟，不把人工状态留给后续测试。
await page.locator("#clearManual").click();
await page.waitForFunction(() => document.querySelector("#drawerQuality")?.textContent?.includes("0x00"));
await browser.close();
