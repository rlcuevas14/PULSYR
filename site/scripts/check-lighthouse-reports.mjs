import fs from "node:fs";
import path from "node:path";

const reportDir = path.resolve("artifacts/lighthouse");
const files = fs.existsSync(reportDir)
  ? fs
      .readdirSync(reportDir)
      .filter((name) => name.endsWith(".json") && name !== "summary.json")
  : [];

const thresholds = {
  performance: 0.9,
  accessibility: 0.95,
  "best-practices": 0.95,
  seo: 1,
};
const timingThresholds = {
  "largest-contentful-paint": 2500,
  "cumulative-layout-shift": 0.1,
  "total-blocking-time": 200,
};
const results = [];
const failures = [];

if (files.length !== 3) {
  failures.push(`expected 3 Lighthouse reports, found ${files.length}`);
}

for (const file of files) {
  const report = JSON.parse(fs.readFileSync(path.join(reportDir, file), "utf8"));
  const entry = { file, finalUrl: report.finalUrl, categories: {}, audits: {} };
  for (const [category, minimum] of Object.entries(thresholds)) {
    const score = report.categories?.[category]?.score;
    entry.categories[category] = score;
    if (typeof score !== "number" || score < minimum) {
      failures.push(`${file}: ${category} score ${score} is below ${minimum}`);
    }
  }
  for (const [audit, maximum] of Object.entries(timingThresholds)) {
    const value = report.audits?.[audit]?.numericValue;
    entry.audits[audit] = value;
    if (typeof value !== "number" || value > maximum) {
      failures.push(`${file}: ${audit} ${value} exceeds ${maximum}`);
    }
  }
  results.push(entry);
}

fs.mkdirSync(reportDir, { recursive: true });
fs.writeFileSync(
  path.join(reportDir, "summary.json"),
  `${JSON.stringify({ thresholds, timingThresholds, results, failures }, null, 2)}\n`,
);

if (failures.length) {
  console.error(`Lighthouse gate failed:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log("Lighthouse gate passed for 3 representative public routes.");
