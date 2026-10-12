// Screenshots of the dashboard with headless Chrome (puppeteer-core).
//
//   PUPPETEER_FROM=<dir with node_modules/puppeteer-core> JOB_ID=job_x HEAL_JOB_ID=job_y BOOKS_JOB_ID=job_z \
//     node docs/screenshots/capture.mjs http://localhost:3000 [shots...]
//
// Shots: hero (job builder: request, schema, preview with a provenance popover), progress (a job's overview),
// results (records with filters), healing (layout-change timeline), evaluation, toscrape (records of the
// books.toscrape.com run), audit. Every page shows data from the running API; see README > Screenshots.
import { createRequire } from "module";

const require = createRequire(process.env.PUPPETEER_FROM || import.meta.url);
const puppeteer = require("puppeteer-core");
const [base, ...shots] = process.argv.slice(2);
const out = process.env.OUT_DIR || new URL(".", import.meta.url).pathname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME || "/usr/bin/google-chrome",
  headless: "new",
  args: ["--no-sandbox"],
  protocolTimeout: 900000,
});

async function open(url, { width = 1500, height = 950 } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 1.5 });
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log("page error:", msg.text());
  });
  await page.goto(url, { waitUntil: "networkidle0", timeout: 120000 });
  await sleep(800);
  return page;
}

async function full(page, name, { maxHeight = 2400 } = {}) {
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewport({ width: page.viewport().width, height: Math.min(height, maxHeight), deviceScaleFactor: 1.5 });
  await sleep(500);
  await page.screenshot({ path: `${out}${name}.png` });
  console.log(`${name}.png`);
}

async function clickButton(page, text) {
  await page.evaluate((label) => {
    const button = [...document.querySelectorAll("button")].find((b) => b.innerText.trim().startsWith(label));
    button?.click();
  }, text);
}

async function waitForText(page, text, timeout = 600000) {
  await page.waitForFunction((t) => document.body.innerText.toLowerCase().includes(t.toLowerCase()), { timeout, polling: 1000 }, text);
}

for (const shot of shots) {
  if (shot === "hero") {
    const page = await open(`${base}/?preset=${process.env.PRESET || "shop-04"}`, { height: 1100 });
    await page.waitForFunction(() => [...document.querySelectorAll("textarea")].every((t) => t.value.length > 0), { timeout: 30000 });
    await clickButton(page, "Propose schema");
    await waitForText(page, "2 · Schema");
    await clickButton(page, "Preview first page");
    await waitForText(page, "3 · Preview with provenance");
    await sleep(1500);
    await full(page, "hero", { maxHeight: 1250 });
  } else if (shot === "progress") {
    const page = await open(`${base}/jobs/${process.env.JOB_ID}`, { height: 1000 });
    await full(page, "progress", { maxHeight: 1250 });
  } else if (shot === "results") {
    const page = await open(`${base}/jobs/${process.env.JOB_ID}?tab=records`, { height: 1000 });
    await page.evaluate(() => {
      const cells = document.querySelectorAll("tbody tr:nth-child(3) td.cell-prov");
      cells[1]?.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    await page.hover("tbody tr:nth-child(3) td.cell-prov:nth-of-type(3)");
    await sleep(600);
    await page.screenshot({ path: `${out}results.png` });
    console.log("results.png");
  } else if (shot === "healing") {
    const page = await open(`${base}/healing`, { height: 1100 });
    await full(page, "healing", { maxHeight: 2300 });
  } else if (shot === "evaluation") {
    const page = await open(`${base}/evaluation`);
    await full(page, "evaluation", { maxHeight: 2600 });
  } else if (shot === "toscrape") {
    const page = await open(`${base}/jobs/${process.env.BOOKS_JOB_ID}?tab=records`, { height: 1000 });
    await page.hover("tbody tr:nth-child(2) td.cell-prov:nth-of-type(7)");
    await sleep(600);
    await page.screenshot({ path: `${out}toscrape.png` });
    console.log("toscrape.png");
  } else if (shot === "audit") {
    const page = await open(`${base}/audit`, { height: 1000 });
    await page.screenshot({ path: `${out}audit-log.png` });
    console.log("audit-log.png");
  } else if (shot === "extractors") {
    const page = await open(`${base}/jobs/${process.env.JOB_ID}?tab=extractors`, { height: 900 });
    await full(page, "extractors", { maxHeight: 1400 });
  }
}
await browser.close();
