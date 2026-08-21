// 使用 Chrome 内置 PDF 阅读器逐页渲染 Word 导出的验收副本。
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.CODEX_NODE_MODULES, "playwright"));

const pdfRoot = process.argv[2];
const outputRoot = process.argv[3];
const pageCounts = {
  "需求文档.pdf": 5,
  "设计文档.pdf": 6,
  "测试文档.pdf": 12,
  "佐证文档.pdf": 8,
};

fs.mkdirSync(outputRoot, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  args: ["--allow-file-access-from-files"],
});

for (const [pdfName, count] of Object.entries(pageCounts)) {
  const documentOutput = path.join(outputRoot, path.parse(pdfName).name);
  fs.mkdirSync(documentOutput, { recursive: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1100 }, deviceScaleFactor: 1 });
  const pdfUrl = pathToFileURL(path.join(pdfRoot, pdfName)).href;
  for (let pageNumber = 1; pageNumber <= count; pageNumber += 1) {
    await page.goto(`${pdfUrl}#page=${pageNumber}&zoom=page-fit`, { waitUntil: "load" });
    await page.waitForTimeout(1200);
    await page.screenshot({
      path: path.join(documentOutput, `page-${String(pageNumber).padStart(2, "0")}.png`),
    });
  }
  await page.close();
}

await browser.close();
