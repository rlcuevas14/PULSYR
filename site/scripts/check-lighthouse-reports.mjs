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
const routeNames = ["home", "product", "docs"];

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

if (files.length !== 9) {
  failures.push(`expected 9 Lighthouse reports (3 samples × 3 routes), found ${files.length}`);
}

for (const routeName of routeNames) {
  const routeFiles = files.filter((file) => file.startsWith(`${routeName}-`));
  const reports = routeFiles.map((file) => ({
    file,
    report: JSON.parse(fs.readFileSync(path.join(reportDir, file), "utf8")),
  }));
  const entry = {
    route: routeName,
    files: routeFiles,
    finalUrl: reports[0]?.report.finalUrl,
    categories: {},
    audits: {},
  };
  if (reports.length !== 3) {
    failures.push(`${routeName}: expected 3 samples, found ${reports.length}`);
    results.push(entry);
    continue;
  }
  for (const [category, minimum] of Object.entries(thresholds)) {
    const samples = reports.map(({ report }) => report.categories?.[category]?.score);
    const score = samples.every((value) => typeof value === "number") ? median(samples) : undefined;
    entry.categories[category] = { median: score, samples };
    if (typeof score !== "number" || score < minimum) {
      failures.push(`${routeName}: median ${category} score ${score} is below ${minimum}`);
    }
  }
  for (const [audit, maximum] of Object.entries(timingThresholds)) {
    const samples = reports.map(({ report }) => report.audits?.[audit]?.numericValue);
    const value = samples.every((sample) => typeof sample === "number") ? median(samples) : undefined;
    entry.audits[audit] = { median: value, samples };
    if (typeof value !== "number" || value > maximum) {
      failures.push(`${routeName}: median ${audit} ${value} exceeds ${maximum}`);
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
console.log("Lighthouse gate passed using 3-sample medians for 3 representative public routes.");
