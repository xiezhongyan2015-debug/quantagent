import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const SHEET_NAME = "一月分钟原式回测";
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const WORKSPACE_DIR =
  path.basename(SCRIPT_DIR).toLowerCase() === ".codex-artifact"
    ? path.dirname(SCRIPT_DIR)
    : SCRIPT_DIR;

// 16个日历因子的主结果对应关系。偏度主表采用5分钟版；尾盘主表采用卡片单日式。
const MAIN_FACTORS = [
  { calendarCode: "CGO", resultFactor: "CGO" },
  { calendarCode: "UTD", resultFactor: "UTD_20D_NEUTRAL" },
  { calendarCode: "CKDP", resultFactor: "CKDP" },
  { calendarCode: "RVJN", resultFactor: "RVJN_5M" },
  { calendarCode: "ILLIQ_SHORTCUT", resultFactor: "ILLIQ_SHORTCUT_5M_5D" },
  { calendarCode: "WEIGHTED_CLOSE_RATIO", resultFactor: "WEIGHTED_CLOSE_RATIO_5M" },
  { calendarCode: "VOLUME_SHARE_KURT", resultFactor: "VOLUME_SHARE_KURT_10M" },
  { calendarCode: "SCC", resultFactor: "SCC_20D" },
  { calendarCode: "IDEAL_AMPLITUDE", resultFactor: "IDEAL_AMPLITUDE_20D" },
  {
    calendarCode: "REALIZED_SKEW_20D",
    resultFactor: "REALIZED_SKEW_5M_20D",
    extraNote: "主表采用5分钟版；1分钟版在下方版本比较表中同时展示。",
  },
  {
    calendarCode: "LAST_30MIN_RETURN",
    resultFactor: "LAST_30MIN_RETURN",
    extraNote: "主表采用卡片单日式；MA5为原报告主版，在下方版本比较表中补充展示。",
  },
  { calendarCode: "SIMILAR_REVERSE", resultFactor: "SIMILAR_REVERSE" },
  { calendarCode: "PRICE_RESILIENCY", resultFactor: "PRICE_RESILIENCY_1M_TREND" },
  {
    calendarCode: "VOLUME_PEAK_WEIGHTED_PERCENTILE",
    resultFactor: "VOLUME_PEAK_PRICE_POS_20D_NEUTRAL",
  },
  { calendarCode: "TGD_20D", resultFactor: "TGD_20D" },
  {
    calendarCode: "VOLUME_RIDGE_INTERVAL_SKEW",
    resultFactor: "VOLUME_RIDGE_GAP_SKEW_20D",
  },
];

const EXTRA_VERSIONS = [
  {
    calendarCode: "REALIZED_SKEW_20D",
    versionName: "1分钟版",
    resultFactor: "REALIZED_SKEW_1M_20D",
    role: "补充版本",
  },
  {
    calendarCode: "REALIZED_SKEW_20D",
    versionName: "5分钟版",
    resultFactor: "REALIZED_SKEW_5M_20D",
    role: "主表版本",
  },
  {
    calendarCode: "LAST_30MIN_RETURN",
    versionName: "卡片单日式",
    resultFactor: "LAST_30MIN_RETURN",
    role: "主表版本",
  },
  {
    calendarCode: "LAST_30MIN_RETURN",
    versionName: "5日均值（MA5）",
    resultFactor: "LAST_30MIN_RETURN_MA5",
    role: "原报告主版补充",
  },
];

const STATUS_COLUMNS = [
  "factor",
  "name",
  "source_frequency",
  "signal_frequency",
  "expected_direction",
  "strict_status",
  "note",
  "calculated",
];

const IC_COLUMNS = [
  "factor",
  "evaluation_frequency",
  "period",
  "ic_mean",
  "ic_std",
  "icir_raw",
  "icir_annualized",
  "periods_per_year",
  "positive_ratio",
  "newey_west_t",
  "observations",
  "avg_stock_count",
];

const BACKTEST_COLUMNS = [
  "signal_frequency",
  "trade_frequency",
  "annual_return",
  "benchmark_annual_return",
  "annual_excess_return",
  "annual_volatility",
  "sharpe",
  "max_drawdown",
  "excess_ir",
  "win_rate_vs_benchmark",
  "periods",
  "factor",
  "period",
  "direction",
  "in_sample_ic",
  "avg_turnover",
  "avg_group_stocks",
];

const YEARLY_COLUMNS = [
  "factor",
  "signal_frequency",
  "trade_frequency",
  "year",
  "direction",
  "strategy_return",
  "benchmark_return",
  "periods",
  "excess_return",
];

const YEAR_LIST = Array.from({ length: 10 }, (_, index) => 2016 + index);

function usage() {
  return [
    "用法：node update_january_minute_workbook.mjs [选项]",
    "",
    "选项：",
    "  --input <xlsx>          原工作簿",
    "  --results-dir <目录>    一月回测结果所在目录",
    "  --output <xlsx>         临时输出工作簿",
    "  --preview-dir <目录>    所有工作表的预览图片目录",
    "  --inspect-dir <目录>    检查结果目录",
    "  --help                  显示本说明",
  ].join("\n");
}

function resolveFromWorkspace(value) {
  return path.isAbsolute(value) ? path.normalize(value) : path.resolve(WORKSPACE_DIR, value);
}

function parseArgs(argv) {
  const defaults = {
    input: path.join(WORKSPACE_DIR, "因子日历2026因子库.xlsx"),
    resultsDir: path.join(WORKSPACE_DIR, "january_original_minute_results"),
  };
  const args = { ...defaults };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--help") {
      args.help = true;
      continue;
    }
    const keyByToken = {
      "--input": "input",
      "--results-dir": "resultsDir",
      "--output": "output",
      "--preview-dir": "previewDir",
      "--inspect-dir": "inspectDir",
    };
    const key = keyByToken[token];
    if (!key) {
      throw new Error(`不认识的选项：${token}\n\n${usage()}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${token} 后面必须填写路径。`);
    }
    args[key] = resolveFromWorkspace(value);
    index += 1;
  }

  args.input = resolveFromWorkspace(args.input);
  args.resultsDir = resolveFromWorkspace(args.resultsDir);
  const resultRoot = path.join(args.resultsDir, "workbook_update_preview");
  args.output = resolveFromWorkspace(
    args.output ?? path.join(resultRoot, "因子日历2026因子库_一月分钟原式回测_临时.xlsx"),
  );
  args.previewDir = resolveFromWorkspace(args.previewDir ?? path.join(resultRoot, "previews"));
  args.inspectDir = resolveFromWorkspace(args.inspectDir ?? path.join(resultRoot, "inspection"));
  return args;
}

async function requireFile(filePath, label) {
  let stats;
  try {
    stats = await fs.stat(filePath);
  } catch {
    throw new Error(`${label}不存在：${filePath}`);
  }
  if (!stats.isFile() || stats.size === 0) {
    throw new Error(`${label}不是有效文件：${filePath}`);
  }
}

function requireColumns(table, requiredColumns) {
  const missing = requiredColumns.filter((name) => !table.headers.includes(name));
  if (missing.length > 0) {
    throw new Error(`${path.basename(table.filePath)} 缺少列：${missing.join("、")}`);
  }
}

function isBlank(value) {
  return value === null || value === undefined || String(value).trim() === "";
}

function cleanHeader(value) {
  return String(value ?? "")
    .replace(/^\uFEFF/, "")
    .trim();
}

// CSV由artifact-tool读取，避免自行处理引号、逗号和换行。
async function readCsvTable(filePath) {
  const text = (await fs.readFile(filePath, "utf8")).replace(/^\uFEFF/, "");
  if (!text.trim()) {
    throw new Error(`${path.basename(filePath)} 是空文件。`);
  }
  const csvWorkbook = await Workbook.fromCSV(text, { sheetName: "CSV" });
  const csvSheet = csvWorkbook.worksheets.getItem("CSV");
  const usedRange = csvSheet.getUsedRange(true);
  if (!usedRange) {
    throw new Error(`${path.basename(filePath)} 没有可读数据。`);
  }
  const matrix = usedRange.values;
  if (!Array.isArray(matrix) || matrix.length < 2) {
    throw new Error(`${path.basename(filePath)} 至少需要一行列名和一行数据。`);
  }
  const headers = matrix[0].map(cleanHeader);
  if (headers.some((header) => !header)) {
    throw new Error(`${path.basename(filePath)} 存在空列名。`);
  }
  const duplicates = headers.filter((header, index) => headers.indexOf(header) !== index);
  if (duplicates.length > 0) {
    throw new Error(`${path.basename(filePath)} 存在重复列名：${[...new Set(duplicates)].join("、")}`);
  }
  const rows = matrix
    .slice(1)
    .filter((row) => row.some((value) => !isBlank(value)))
    .map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? null])));
  if (rows.length === 0) {
    throw new Error(`${path.basename(filePath)} 没有数据行。`);
  }
  return { filePath, headers, rows };
}

function makeIndex(table, keyFields) {
  const index = new Map();
  for (const row of table.rows) {
    const parts = keyFields.map((field) => {
      if (isBlank(row[field])) {
        throw new Error(`${path.basename(table.filePath)} 的 ${field} 存在空值。`);
      }
      return String(row[field]).trim();
    });
    const key = parts.join("\u0001");
    if (index.has(key)) {
      throw new Error(
        `${path.basename(table.filePath)} 存在重复记录：${keyFields
          .map((field, position) => `${field}=${parts[position]}`)
          .join("，")}`,
      );
    }
    index.set(key, row);
  }
  return index;
}

function requireIndexed(index, keyParts, fileLabel) {
  const key = keyParts.map((value) => String(value)).join("\u0001");
  const row = index.get(key);
  if (!row) {
    throw new Error(`${fileLabel} 缺少记录：${keyParts.join(" / ")}`);
  }
  return row;
}

function asNumber(value, field, sourceLabel) {
  if (isBlank(value)) {
    throw new Error(`${sourceLabel} 的 ${field} 为空。`);
  }
  const number = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isFinite(number)) {
    throw new Error(`${sourceLabel} 的 ${field} 不是有效数字：${String(value)}`);
  }
  return number;
}

function asBoolean(value, field, sourceLabel) {
  if (typeof value === "boolean") return value;
  const normalized = String(value ?? "").trim().toLowerCase();
  if (["true", "1", "yes"].includes(normalized)) return true;
  if (["false", "0", "no"].includes(normalized)) return false;
  throw new Error(`${sourceLabel} 的 ${field} 不是有效布尔值：${String(value)}`);
}

function asDirection(value, sourceLabel) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "high") return "高因子组";
  if (normalized === "low") return "低因子组";
  throw new Error(`${sourceLabel} 的 direction 只能是 high 或 low，实际为：${String(value)}`);
}

function classification(strictStatus) {
  if (strictStatus === "approximation_only") return "1分钟近似";
  if (["missing_data_definition", "missing_parameters"].includes(strictStatus)) {
    return "原式未测";
  }
  if (
    ["runnable", "runnable_example_frequency", "formula_typo_corrected", "text_interpreted"].includes(
      strictStatus,
    )
  ) {
    return "可严格/按卡片参数测";
  }
  throw new Error(`factor_status.csv 出现未处理的 strict_status：${strictStatus}`);
}

function availabilityText(strictStatus) {
  const textByStatus = {
    runnable: "可严格回测",
    runnable_example_frequency: "可按卡片示例参数回测",
    formula_typo_corrected: "可按卡片正文修正回测",
    text_interpreted: "可按卡片正文解释回测",
    approximation_only: "仅可做1分钟近似",
    missing_data_definition: "原式不可严格回测；旧近似值保留",
    missing_parameters: "原式不可严格回测；旧近似值保留",
  };
  return textByStatus[strictStatus];
}

function backtestStatusText(strictStatus) {
  const textByStatus = {
    runnable: "已回测（分钟原式）",
    runnable_example_frequency: "已回测（卡片示例参数）",
    formula_typo_corrected: "已回测（按卡片正文修正）",
    text_interpreted: "已回测（按卡片正文解释）",
    approximation_only: "已回测（1分钟近似）",
    missing_data_definition: "已回测（日线近似；分钟原式未测）",
    missing_parameters: "待补参数（分钟原式未测）",
  };
  return textByStatus[strictStatus];
}

function dataStatusText(statusRow) {
  const strictStatus = String(statusRow.strict_status).trim();
  const note = String(statusRow.note ?? "").trim();
  if (strictStatus === "approximation_only") {
    return `已完成1分钟近似；${note}`;
  }
  if (strictStatus === "missing_data_definition") {
    return `原式所需数据或定义不完整；${note}`;
  }
  if (strictStatus === "missing_parameters") {
    return `原式缺少必要参数；${note}`;
  }
  return `回测所需数据已齐，主版本已计算；${note}`;
}

function signalFrequencyText(statusRow) {
  const factor = String(statusRow.factor).trim();
  const frequency = String(statusRow.signal_frequency).trim();
  if (frequency === "monthly") return "月末";
  if (frequency === "weekly_tuesday") return "每周二（日值先取5日均值）";
  if (frequency === "every_5_days") return "每5个交易日";
  if (frequency === "daily") return "每日";
  if (frequency === "unavailable" && factor === "CKDP") return "原式待定义后按日";
  if (frequency === "unavailable" && factor === "SIMILAR_REVERSE") {
    return "补齐持有期、半衰期和基准后按日";
  }
  throw new Error(`未处理的 signal_frequency：${factor} / ${frequency}`);
}

function returnHorizonText(statusRow) {
  const factor = String(statusRow.factor).trim();
  const frequency = String(statusRow.signal_frequency).trim();
  if (frequency === "monthly") {
    return "次月首个交易日开盘至次月最后交易日收盘";
  }
  if (frequency === "weekly_tuesday") {
    return "周二收盘后出信号，次日开盘至第6个交易日开盘（持有5个交易日）";
  }
  if (["daily", "every_5_days"].includes(frequency)) {
    return "IC按原信号日评价；交易每5个交易日选一次，次日开盘至第6个交易日开盘（原文未给持有期）";
  }
  if (frequency === "unavailable" && factor === "CKDP") {
    return "分钟原式未测；旧日线近似仍为5个交易日";
  }
  if (frequency === "unavailable" && factor === "SIMILAR_REVERSE") {
    return "未测；原文未给持有期";
  }
  throw new Error(`未处理的未来收益期：${factor} / ${frequency}`);
}

function windowText(factor) {
  const textByFactor = {
    CGO: "100日",
    UTD_20D_NEUTRAL: "日内240分钟；过去20日",
    CKDP: "原式未限定；分钟原式未测",
    RVJN_5M: "5分钟×48段；日值取5日均值",
    ILLIQ_SHORTCUT_5M_5D: "5分钟×48段；t-1至t-5",
    WEIGHTED_CLOSE_RATIO_5M: "5分钟×48段；单日",
    VOLUME_SHARE_KURT_10M: "10分钟×24段；单日",
    SCC_20D: "20日",
    IDEAL_AMPLITUDE_20D: "20日；高低收盘价各25%",
    REALIZED_SKEW_5M_20D: "5分钟×48段；过去20日均值",
    LAST_30MIN_RETURN: "14:30—15:00；单日",
    SIMILAR_REVERSE: "历史120日；相似序列6日；阈值0.4；其余参数缺失",
    PRICE_RESILIENCY_1M_TREND: "1分钟近似；EMA20、SMA120、STD120",
    VOLUME_PEAK_PRICE_POS_20D_NEUTRAL: "同分钟前20日阈值；日值20日均值；20日反转控制",
    TGD_20D: "1分钟×240段；日残差取20日均值",
    VOLUME_RIDGE_GAP_SKEW_20D: "同分钟前20日阈值；汇总过去20日间隔",
  };
  const text = textByFactor[factor];
  if (!text) throw new Error(`未设置观察窗口：${factor}`);
  return text;
}

function limitationText(statusRow) {
  const status = String(statusRow.strict_status).trim();
  const textByStatus = {
    runnable: "按原文参数计算；成交额与价格明显不一致的日期不进入相关窗口。",
    runnable_example_frequency: "采用卡片示例的分钟频率，未做分钟频率敏感性比较。",
    formula_typo_corrected: "按正文含义修正卡片公式中的明显笔误，并在新增结果页注明。",
    text_interpreted: "卡片没有完整公式，按正文文字逐步实现；具体解释见新增结果页。",
    approximation_only: "原文要求tick数据；本次只用1分钟K线近似，不能视为严格复现。",
    missing_data_definition: "缺少逐价位筹码分布生成方法；分钟原式未测，旧日线近似仅作参考。",
    missing_parameters: "缺持有期、半衰期和超额收益基准；原式未测。",
  };
  const text = textByStatus[status];
  if (!text) throw new Error(`未设置限制说明：${status}`);
  return text;
}

function parseNdjson(ndjson) {
  return String(ndjson ?? "")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function safeFileName(value) {
  return String(value).replace(/[<>:"/\\|?*]/g, "_");
}

function isoDateText(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function styleNewSheet(sheet, mainStartRow, mainEndRow, extraStartRow, extraEndRow, yearlyStartRow, yearlyEndRow) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(mainStartRow);
  sheet.freezePanes.freezeColumns(3);

  sheet.getRange("A1:Y1").format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF", size: 20, name: "Microsoft YaHei" },
    verticalAlignment: "center",
  };
  sheet.getRange("A2:Y2").format = {
    fill: "#D9EAF7",
    font: { color: "#1F4E78", size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
  };
  sheet.getRange("A3:Y3").format = {
    fill: "#FFF2CC",
    font: { color: "#7F6000", italic: true, size: 10, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#F4B183" },
  };

  for (const range of ["A5:D5", "E5:H5", "I5:L5"]) {
    sheet.getRange(range).format = {
      fill: range.startsWith("A") ? "#D9EAD3" : range.startsWith("E") ? "#FFF2CC" : "#F4CCCC",
      font: { bold: true, color: "#1F4E78", size: 10, name: "Microsoft YaHei" },
      horizontalAlignment: "center",
      verticalAlignment: "center",
      borders: { preset: "outside", style: "medium", color: "#1F4E78" },
    };
  }
  for (const range of ["A6:D6", "E6:H6", "I6:L6"]) {
    sheet.getRange(range).format = {
      fill: range.startsWith("A") ? "#E2F0D9" : range.startsWith("E") ? "#FFF2CC" : "#F4CCCC",
      font: { bold: true, color: "#17365D", size: 18, name: "Carlito" },
      horizontalAlignment: "center",
      verticalAlignment: "center",
      borders: { preset: "outside", style: "medium", color: "#1F4E78" },
    };
  }
  sheet.getRange("M5:Y6").format = {
    fill: "#D9EAF7",
    font: { color: "#1F4E78", size: 10, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "medium", color: "#1F4E78" },
  };

  sheet.getRange("A8:Y11").format = {
    fill: "#F2F6FA",
    font: { color: "#1F1F1F", size: 9, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
    borders: {
      insideHorizontal: { style: "thin", color: "#D9E2F3" },
      bottom: { style: "thin", color: "#D9E2F3" },
    },
  };

  for (const row of [mainStartRow - 1, extraStartRow - 1, yearlyStartRow - 2]) {
    sheet.getRange(`A${row}:Y${row}`).format = {
      fill: "#0F6B78",
      font: { bold: true, color: "#FFFFFF", size: 11, name: "Microsoft YaHei" },
      verticalAlignment: "center",
    };
  }

  sheet.getRange(`A${mainStartRow}:Y${mainStartRow}`).format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF", size: 9, name: "Microsoft YaHei" },
    wrapText: true,
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#B4C6E7" },
  };
  for (let row = mainStartRow + 1; row <= mainEndRow; row += 1) {
    sheet.getRange(`A${row}:Y${row}`).format = {
      fill: (row - mainStartRow) % 2 === 1 ? "#D9EAF7" : "#FFFFFF",
      font: { color: "#1F1F1F", size: 9, name: "Microsoft YaHei" },
      wrapText: true,
      verticalAlignment: "center",
      borders: { preset: "all", style: "thin", color: "#D9E2F3" },
    };
  }

  sheet.getRange(`A${extraStartRow}:J${extraStartRow}`).format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF", size: 9, name: "Microsoft YaHei" },
    wrapText: true,
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#B4C6E7" },
  };
  for (let row = extraStartRow + 1; row <= extraEndRow; row += 1) {
    sheet.getRange(`A${row}:J${row}`).format = {
      fill: (row - extraStartRow) % 2 === 1 ? "#D9EAF7" : "#FFFFFF",
      font: { color: "#1F1F1F", size: 9, name: "Microsoft YaHei" },
      wrapText: true,
      verticalAlignment: "center",
      borders: { preset: "all", style: "thin", color: "#D9E2F3" },
    };
  }

  sheet.getRange(`A${yearlyStartRow - 1}:M${yearlyStartRow - 1}`).format = {
    fill: "#FFF2CC",
    font: { italic: true, color: "#7F6000", size: 9, name: "Microsoft YaHei" },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange(`A${yearlyStartRow}:M${yearlyStartRow}`).format = {
    fill: "#1F4E78",
    font: { bold: true, color: "#FFFFFF", size: 9, name: "Microsoft YaHei" },
    wrapText: true,
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#B4C6E7" },
  };
  for (let row = yearlyStartRow + 1; row <= yearlyEndRow; row += 1) {
    sheet.getRange(`A${row}:M${row}`).format = {
      fill: (row - yearlyStartRow) % 2 === 1 ? "#D9EAF7" : "#FFFFFF",
      font: { color: "#1F1F1F", size: 9, name: "Microsoft YaHei" },
      verticalAlignment: "center",
      borders: { preset: "all", style: "thin", color: "#D9E2F3" },
    };
  }

  sheet.getRange(`H${mainStartRow + 1}:J${mainEndRow}`).format.numberFormat = "0.0000";
  sheet.getRange(`K${mainStartRow + 1}:K${mainEndRow}`).format.numberFormat = "0.0%";
  sheet.getRange(`L${mainStartRow + 1}:M${mainEndRow}`).format.numberFormat = "0.0000";
  sheet.getRange(`O${mainStartRow + 1}:R${mainEndRow}`).format.numberFormat = "0.00%";
  sheet.getRange(`S${mainStartRow + 1}:S${mainEndRow}`).format.numberFormat = "0.000";
  sheet.getRange(`T${mainStartRow + 1}:W${mainEndRow}`).format.numberFormat = "0.00%";
  sheet.getRange(`X${mainStartRow + 1}:X${mainEndRow}`).format.numberFormat = "0.000";
  sheet.getRange(`E${extraStartRow + 1}:G${extraEndRow}`).format.numberFormat = "0.0000";
  sheet.getRange(`H${extraStartRow + 1}:I${extraEndRow}`).format.numberFormat = "0.00%";
  sheet.getRange(`D${yearlyStartRow + 1}:M${yearlyEndRow}`).format.numberFormat = "0.00%";

  sheet.getRange("A:A").format.columnWidth = 24;
  sheet.getRange("B:B").format.columnWidth = 21;
  sheet.getRange("C:C").format.columnWidth = 38;
  sheet.getRange("D:D").format.columnWidth = 22;
  sheet.getRange("E:E").format.columnWidth = 15;
  sheet.getRange("F:F").format.columnWidth = 20;
  sheet.getRange("G:G").format.columnWidth = 58;
  sheet.getRange("H:M").format.columnWidth = 14;
  sheet.getRange("N:N").format.columnWidth = 14;
  sheet.getRange("O:R").format.columnWidth = 14;
  sheet.getRange("S:S").format.columnWidth = 11;
  sheet.getRange("T:W").format.columnWidth = 14;
  sheet.getRange("X:X").format.columnWidth = 11;
  sheet.getRange("Y:Y").format.columnWidth = 42;

  sheet.getRange("1:1").format.rowHeight = 32;
  sheet.getRange("2:2").format.rowHeight = 22;
  sheet.getRange("3:3").format.rowHeight = 38;
  sheet.getRange("5:6").format.rowHeight = 26;
  sheet.getRange("8:11").format.rowHeight = 24;
  sheet.getRange(`${mainStartRow}:${mainStartRow}`).format.rowHeight = 42;
  sheet.getRange(`${mainStartRow + 1}:${mainEndRow}`).format.rowHeight = 52;
  sheet.getRange(`${extraStartRow}:${extraStartRow}`).format.rowHeight = 34;
  sheet.getRange(`${extraStartRow + 1}:${extraEndRow}`).format.rowHeight = 34;
  sheet.getRange(`${yearlyStartRow}:${yearlyStartRow}`).format.rowHeight = 30;
  sheet.getRange(`${yearlyStartRow + 1}:${yearlyEndRow}`).format.rowHeight = 26;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }

  if (path.resolve(args.input).toLowerCase() === path.resolve(args.output).toLowerCase()) {
    throw new Error("临时输出不能与原工作簿同名同路径，脚本不会覆盖原文件。");
  }

  const sourceFiles = {
    status: path.join(args.resultsDir, "factor_status.csv"),
    ic: path.join(args.resultsDir, "original_frequency_ic_summary.csv"),
    backtest: path.join(args.resultsDir, "original_frequency_backtest_summary.csv"),
    yearly: path.join(args.resultsDir, "original_frequency_yearly_backtest.csv"),
  };

  // 先检查全部输入；任何一份结果缺失时，不创建工作簿和预览文件。
  await requireFile(args.input, "原工作簿");
  await requireFile(sourceFiles.status, "factor_status.csv");
  await requireFile(sourceFiles.ic, "original_frequency_ic_summary.csv");
  await requireFile(sourceFiles.backtest, "original_frequency_backtest_summary.csv");
  await requireFile(sourceFiles.yearly, "original_frequency_yearly_backtest.csv");

  const [statusTable, icTable, backtestTable, yearlyTable] = await Promise.all([
    readCsvTable(sourceFiles.status),
    readCsvTable(sourceFiles.ic),
    readCsvTable(sourceFiles.backtest),
    readCsvTable(sourceFiles.yearly),
  ]);
  requireColumns(statusTable, STATUS_COLUMNS);
  requireColumns(icTable, IC_COLUMNS);
  requireColumns(backtestTable, BACKTEST_COLUMNS);
  requireColumns(yearlyTable, YEARLY_COLUMNS);

  const statusIndex = makeIndex(statusTable, ["factor"]);
  const icIndex = makeIndex(icTable, ["factor", "period"]);
  const backtestIndex = makeIndex(backtestTable, ["factor", "period"]);
  const yearlyIndex = makeIndex(yearlyTable, ["factor", "year"]);

  const allResultFactors = new Set([
    ...MAIN_FACTORS.map((item) => item.resultFactor),
    ...EXTRA_VERSIONS.map((item) => item.resultFactor),
  ]);
  for (const factor of allResultFactors) {
    requireIndexed(statusIndex, [factor], "factor_status.csv");
  }

  const resultByCalendar = new Map();
  for (const item of MAIN_FACTORS) {
    const statusRow = requireIndexed(statusIndex, [item.resultFactor], "factor_status.csv");
    const strictStatus = String(statusRow.strict_status).trim();
    const kind = classification(strictStatus);
    const calculated = asBoolean(
      statusRow.calculated,
      "calculated",
      `factor_status.csv / ${item.resultFactor}`,
    );
    if (kind !== "原式未测" && !calculated) {
      throw new Error(`factor_status.csv 显示 ${item.resultFactor} 尚未计算，不能写入工作簿。`);
    }

    const base = {
      ...item,
      statusRow,
      strictStatus,
      kind,
      calculated,
      icFull: null,
      ic2016: null,
      ic2021: null,
      backtestFull: null,
      backtest2021: null,
    };

    if (kind !== "原式未测") {
      base.icFull = requireIndexed(icIndex, [item.resultFactor, "full"], "original_frequency_ic_summary.csv");
      base.ic2016 = requireIndexed(
        icIndex,
        [item.resultFactor, "2016_2020"],
        "original_frequency_ic_summary.csv",
      );
      base.ic2021 = requireIndexed(
        icIndex,
        [item.resultFactor, "2021_2025"],
        "original_frequency_ic_summary.csv",
      );
      base.backtestFull = requireIndexed(
        backtestIndex,
        [item.resultFactor, "full"],
        "original_frequency_backtest_summary.csv",
      );
      base.backtest2021 = requireIndexed(
        backtestIndex,
        [item.resultFactor, "2021_2025"],
        "original_frequency_backtest_summary.csv",
      );
      if (String(base.icFull.evaluation_frequency).trim() !== String(statusRow.signal_frequency).trim()) {
        throw new Error(
          `${item.resultFactor} 的 evaluation_frequency 与 factor_status.csv.signal_frequency 不一致。`,
        );
      }
      for (const row of [base.backtestFull, base.backtest2021]) {
        if (String(row.signal_frequency).trim() !== String(statusRow.signal_frequency).trim()) {
          throw new Error(`${item.resultFactor} 的交易频率与 factor_status.csv 不一致。`);
        }
        const expectedTrade = String(statusRow.signal_frequency).trim() === "daily"
          ? "every_5_days"
          : String(statusRow.signal_frequency).trim();
        if (String(row.trade_frequency).trim() !== expectedTrade) {
          throw new Error(`${item.resultFactor} 的实际换仓频率不符合设定。`);
        }
      }
      if (
        String(base.backtestFull.direction).trim().toLowerCase() !==
        String(base.backtest2021.direction).trim().toLowerCase()
      ) {
        throw new Error(`${item.resultFactor} 的全期与2021—2025选股方向不一致。`);
      }
      for (const year of YEAR_LIST) {
        const row = requireIndexed(
          yearlyIndex,
          [item.resultFactor, year],
          "original_frequency_yearly_backtest.csv",
        );
        if (String(row.signal_frequency).trim() !== String(statusRow.signal_frequency).trim()) {
          throw new Error(`${item.resultFactor} 的${year}年结果频率与 factor_status.csv 不一致。`);
        }
        const expectedTrade = String(statusRow.signal_frequency).trim() === "daily"
          ? "every_5_days"
          : String(statusRow.signal_frequency).trim();
        if (String(row.trade_frequency).trim() !== expectedTrade) {
          throw new Error(`${item.resultFactor} 的${year}年实际换仓频率不符合设定。`);
        }
      }
    }
    resultByCalendar.set(item.calendarCode, base);
  }

  const countByKind = [...resultByCalendar.values()].reduce((counts, item) => {
    counts[item.kind] = (counts[item.kind] ?? 0) + 1;
    return counts;
  }, {});
  if (
    countByKind["可严格/按卡片参数测"] !== 13 ||
    countByKind["1分钟近似"] !== 1 ||
    countByKind["原式未测"] !== 2
  ) {
    throw new Error(
      `因子分类数量不符合要求：${JSON.stringify(countByKind)}；应为13项可严格/按卡片参数测、1项1分钟近似、2项原式未测。`,
    );
  }

  for (const item of EXTRA_VERSIONS) {
    const statusRow = requireIndexed(statusIndex, [item.resultFactor], "factor_status.csv");
    if (!asBoolean(statusRow.calculated, "calculated", `factor_status.csv / ${item.resultFactor}`)) {
      throw new Error(`补充版本 ${item.resultFactor} 尚未计算。`);
    }
    requireIndexed(icIndex, [item.resultFactor, "full"], "original_frequency_ic_summary.csv");
    for (const period of ["full", "2021_2025"]) {
      const row = requireIndexed(
        backtestIndex,
        [item.resultFactor, period],
        "original_frequency_backtest_summary.csv",
      );
      if (String(row.signal_frequency).trim() !== String(statusRow.signal_frequency).trim()) {
        throw new Error(`${item.resultFactor} 的交易频率与 factor_status.csv 不一致。`);
      }
      const expectedTrade = String(statusRow.signal_frequency).trim() === "daily"
        ? "every_5_days"
        : String(statusRow.signal_frequency).trim();
      if (String(row.trade_frequency).trim() !== expectedTrade) {
        throw new Error(`${item.resultFactor} 的实际换仓频率不符合设定。`);
      }
    }
  }

  const inputBlob = await FileBlob.load(args.input);
  const workbook = await SpreadsheetFile.importXlsx(inputBlob);
  const originalSheetInspection = await workbook.inspect({
    kind: "sheet",
    include: "id,name",
    maxChars: 8000,
  });
  const originalSheetNames = parseNdjson(originalSheetInspection.ndjson)
    .filter((record) => record.kind === "sheet")
    .map((record) => record.name);
  if (originalSheetNames.includes(SHEET_NAME)) {
    throw new Error(`原工作簿已经有“${SHEET_NAME}”工作表。请使用未更新的原工作簿。`);
  }

  const factorSheet = workbook.worksheets.getItem("因子总库");
  const factorRows = factorSheet.getRange("A5:AL35").values;
  const januaryRows = factorRows.slice(0, 16);
  const januaryCodes = januaryRows.map((row) => String(row[2] ?? "").trim());
  const expectedCodes = new Set(MAIN_FACTORS.map((item) => item.calendarCode));
  const missingCalendarRows = [...expectedCodes].filter((code) => !januaryCodes.includes(code));
  const unexpectedRows = januaryCodes.filter((code) => !expectedCodes.has(code));
  if (missingCalendarRows.length > 0 || unexpectedRows.length > 0) {
    throw new Error(
      `因子总库前16行与一月因子名单不一致。缺少：${missingCalendarRows.join("、") || "无"}；多出：${
        unexpectedRows.join("、") || "无"
      }。`,
    );
  }

  const calendarName = new Map(januaryRows.map((row) => [String(row[2]).trim(), String(row[1]).trim()]));
  const orderedResults = januaryCodes.map((code) => resultByCalendar.get(code));
  const runDate = new Date();
  const runDateText = isoDateText(runDate);

  const mainSectionRow = 13;
  const mainHeaderRow = 14;
  const mainFirstDataRow = 15;
  const mainLastDataRow = mainFirstDataRow + orderedResults.length - 1;
  const extraSectionRow = 32;
  const extraHeaderRow = 33;
  const extraFirstDataRow = 34;
  const extraLastDataRow = extraFirstDataRow + EXTRA_VERSIONS.length - 1;
  const yearlySectionRow = 39;
  const yearlyNoteRow = 40;
  const yearlyHeaderRow = 41;
  const yearlyFirstDataRow = 42;
  const yearlyLastDataRow = yearlyFirstDataRow + orderedResults.length - 1;

  const sheet = workbook.worksheets.add(SHEET_NAME);
  sheet.mergeCells("A1:Y1");
  sheet.mergeCells("A2:Y2");
  sheet.mergeCells("A3:Y3");
  for (const range of ["A5:D5", "A6:D6", "E5:H5", "E6:H6", "I5:L5", "I6:L6", "M5:Y6"]) {
    sheet.mergeCells(range);
  }
  for (const row of [8, 9, 10, 11, mainSectionRow, extraSectionRow, yearlySectionRow]) {
    sheet.mergeCells(`A${row}:Y${row}`);
  }
  sheet.mergeCells(`A${yearlyNoteRow}:M${yearlyNoteRow}`);

  sheet.getRange("A1").values = [["一月分钟原式与卡片参数回测"]];
  sheet.getRange("A2").values = [[
    `16个一月日历因子｜IC与交易均按原始信号频率｜生成日期：${runDateText}`,
  ]];
  sheet.getRange("A3").values = [[
    "13项可严格或按卡片参数测试；价格弹性仅用1分钟K线近似；CKDP缺逐价位筹码分布定义，相似反转缺持有期、半衰期和超额收益基准，二者分钟原式均未测。",
  ]];
  sheet.getRange("A5").values = [["可严格/按卡片参数测"]];
  sheet.getRange("A6").values = [[13]];
  sheet.getRange("E5").values = [["价格弹性1分钟近似"]];
  sheet.getRange("E6").values = [[1]];
  sheet.getRange("I5").values = [["CKDP与相似反转原式未测"]];
  sheet.getRange("I6").values = [[2]];
  sheet.getRange("M5").values = [[
    "主表只把实际完成的分钟原式、卡片参数版或明确标注的近似版写入指标列。CKDP和相似反转在本页不引用旧近似指标；旧值仅保留在“因子总库”原有单元格中。",
  ]];
  sheet.getRange("A8").values = [[
    "IC口径：来自 original_frequency_ic_summary.csv；主结果取 period=full。日频信号对应重叠5日收益，年化ICIR按实际有效信号数/年再除以5；主表同时保留未年化ICIR。负向因子的IC>0占比应反向理解。",
  ]];
  sheet.getRange("A9").values = [[
    "交易口径：月频因子按月末信号、次月首日开盘买入并在月末收盘退出，每期完整计买卖费用；周频按周二信号持有5日；日频卡因未给持有期而每5日选一次。退出缺价按此前最近收盘价估值，不代表一定能成交。",
  ]];
  sheet.getRange("A10").values = [[
    "已实现偏度：卡片允许1分钟或5分钟，两版都已计算；主表采用 REALIZED_SKEW_5M_20D，版本比较表同时列出1分钟版。",
  ]];
  sheet.getRange("A11").values = [[
    "尾盘半小时收益：主表采用卡片单日式 LAST_30MIN_RETURN；LAST_30MIN_RETURN_MA5 作为原报告主版补充展示，二者仍对应同一个日历因子。",
  ]];
  sheet.getRange(`A${mainSectionRow}`).values = [["16个一月日历因子主结果"]];

  const mainHeaders = [
    "日历因子代码",
    "因子名称",
    "主结果代码",
    "测试性质",
    "卡片频率",
    "实际评价频率",
    "实现说明",
    "全期IC（原频率）",
    "全期ICIR（未年化，icir_raw）",
    "全期ICIR（年化，icir_annualized）",
    "IC>0占比（负向因子反看）",
    "2016—2020 IC",
    "2021—2025 IC",
    "选股方向",
    "全期扣费年化",
    "全期基准年化",
    "全期年化相差",
    "全期调仓期末最大回撤",
    "全期夏普",
    "2021—2025扣费年化",
    "2021—2025基准年化",
    "2021—2025年化相差",
    "2021—2025调仓期末最大回撤",
    "2021—2025夏普",
    "备注",
  ];
  sheet.getRange(`A${mainHeaderRow}:Y${mainHeaderRow}`).values = [mainHeaders];

  const mainValues = orderedResults.map((item) => {
    const sourceLabel = item.resultFactor;
    const note = [String(item.statusRow.note ?? "").trim(), item.extraNote ?? ""]
      .filter(Boolean)
      .join("；");
    if (item.kind === "原式未测") {
      return [
        item.calendarCode,
        calendarName.get(item.calendarCode),
        item.resultFactor,
        item.kind,
        String(item.statusRow.source_frequency),
        signalFrequencyText(item.statusRow),
        note,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        null,
        "分钟原式未测；因子总库原有近似数值不改。",
      ];
    }
    return [
      item.calendarCode,
      calendarName.get(item.calendarCode),
      item.resultFactor,
      item.kind,
      String(item.statusRow.source_frequency),
      signalFrequencyText(item.statusRow),
      note,
      asNumber(item.icFull.ic_mean, "ic_mean", `${sourceLabel} / IC full`),
      asNumber(item.icFull.icir_raw, "icir_raw", `${sourceLabel} / IC full`),
      asNumber(item.icFull.icir_annualized, "icir_annualized", `${sourceLabel} / IC full`),
      asNumber(item.icFull.positive_ratio, "positive_ratio", `${sourceLabel} / IC full`),
      asNumber(item.ic2016.ic_mean, "ic_mean", `${sourceLabel} / IC 2016_2020`),
      asNumber(item.ic2021.ic_mean, "ic_mean", `${sourceLabel} / IC 2021_2025`),
      asDirection(item.backtestFull.direction, `${sourceLabel} / backtest full`),
      asNumber(item.backtestFull.annual_return, "annual_return", `${sourceLabel} / backtest full`),
      asNumber(
        item.backtestFull.benchmark_annual_return,
        "benchmark_annual_return",
        `${sourceLabel} / backtest full`,
      ),
      asNumber(
        item.backtestFull.annual_excess_return,
        "annual_excess_return",
        `${sourceLabel} / backtest full`,
      ),
      asNumber(item.backtestFull.max_drawdown, "max_drawdown", `${sourceLabel} / backtest full`),
      asNumber(item.backtestFull.sharpe, "sharpe", `${sourceLabel} / backtest full`),
      asNumber(item.backtest2021.annual_return, "annual_return", `${sourceLabel} / backtest 2021_2025`),
      asNumber(
        item.backtest2021.benchmark_annual_return,
        "benchmark_annual_return",
        `${sourceLabel} / backtest 2021_2025`,
      ),
      asNumber(
        item.backtest2021.annual_excess_return,
        "annual_excess_return",
        `${sourceLabel} / backtest 2021_2025`,
      ),
      asNumber(
        item.backtest2021.max_drawdown,
        "max_drawdown",
        `${sourceLabel} / backtest 2021_2025`,
      ),
      asNumber(item.backtest2021.sharpe, "sharpe", `${sourceLabel} / backtest 2021_2025`),
      item.kind === "1分钟近似" ? "卡片要求tick数据，本结果仅为1分钟近似。" : "",
    ];
  });
  sheet.getRange(`A${mainFirstDataRow}:Y${mainLastDataRow}`).values = mainValues;

  for (let index = 0; index < orderedResults.length; index += 1) {
    const row = mainFirstDataRow + index;
    const kind = orderedResults[index].kind;
    const fill =
      kind === "可严格/按卡片参数测" ? "#E2F0D9" : kind === "1分钟近似" ? "#FFF2CC" : "#F4CCCC";
    sheet.getRange(`D${row}`).format.fill = fill;
  }

  sheet.getRange(`A${extraSectionRow}`).values = [["偏度与尾盘因子的版本比较"]];
  sheet.getRange(`A${extraHeaderRow}:J${extraHeaderRow}`).values = [[
    "日历因子代码",
    "版本",
    "结果代码",
    "作用",
    "全期IC（原频率）",
    "ICIR（未年化）",
    "ICIR（年化）",
    "全期扣费年化（原信号频率）",
    "2021—2025扣费年化（原信号频率）",
    "实际评价频率",
  ]];
  const extraValues = EXTRA_VERSIONS.map((item) => {
    const statusRow = requireIndexed(statusIndex, [item.resultFactor], "factor_status.csv");
    const icFull = requireIndexed(icIndex, [item.resultFactor, "full"], "original_frequency_ic_summary.csv");
    const backFull = requireIndexed(backtestIndex, [item.resultFactor, "full"], "original_frequency_backtest_summary.csv");
    const back2021 = requireIndexed(
      backtestIndex,
      [item.resultFactor, "2021_2025"],
      "original_frequency_backtest_summary.csv",
    );
    return [
      item.calendarCode,
      item.versionName,
      item.resultFactor,
      item.role,
      asNumber(icFull.ic_mean, "ic_mean", `${item.resultFactor} / IC full`),
      asNumber(icFull.icir_raw, "icir_raw", `${item.resultFactor} / IC full`),
      asNumber(icFull.icir_annualized, "icir_annualized", `${item.resultFactor} / IC full`),
      asNumber(backFull.annual_return, "annual_return", `${item.resultFactor} / backtest full`),
      asNumber(back2021.annual_return, "annual_return", `${item.resultFactor} / backtest 2021_2025`),
      signalFrequencyText(statusRow),
    ];
  });
  sheet.getRange(`A${extraFirstDataRow}:J${extraLastDataRow}`).values = extraValues;

  sheet.getRange(`A${yearlySectionRow}`).values = [["逐年交易收益（原信号频率）"]];
  sheet.getRange(`A${yearlyNoteRow}`).values = [[
    "来自 original_frequency_yearly_backtest.csv 的 strategy_return；CKDP与相似反转分钟原式未测，因此本表留空。",
  ]];
  sheet.getRange(`A${yearlyHeaderRow}:M${yearlyHeaderRow}`).values = [[
    "日历因子代码",
    "因子名称",
    "主结果代码",
    ...YEAR_LIST.map(String),
  ]];
  const yearlyValues = orderedResults.map((item) => {
    const values = item.kind === "原式未测"
      ? YEAR_LIST.map(() => null)
      : YEAR_LIST.map((year) => {
          const row = requireIndexed(yearlyIndex, [item.resultFactor, year], "original_frequency_yearly_backtest.csv");
          return asNumber(row.strategy_return, "strategy_return", `${item.resultFactor} / ${year}`);
        });
    return [item.calendarCode, calendarName.get(item.calendarCode), item.resultFactor, ...values];
  });
  sheet.getRange(`A${yearlyFirstDataRow}:M${yearlyLastDataRow}`).values = yearlyValues;

  styleNewSheet(
    sheet,
    mainHeaderRow,
    mainLastDataRow,
    extraHeaderRow,
    extraLastDataRow,
    yearlyHeaderRow,
    yearlyLastDataRow,
  );

  // 更新因子总库的说明和状态；数值列从新增工作表引用，便于查看来源。
  factorSheet.getRange("A2").values = [[
    `共31个量价因子；一月16个、三月新增15个。最后更新：${runDateText}`,
  ]];
  factorSheet.getRange("A3").values = [[
    "一月IC按原信号频率计算；交易按月末、周二或注明的每5日安排。训练区间只使用2020年底前已结束的未来收益；全期ICIR写未年化值。",
  ]];
  factorSheet.getRange("G4").values = [["建议/本次信号频度"]];
  factorSheet.getRange("S4").values = [["IC>0占比（负向反看）"]];
  factorSheet.getRange("Z4").values = [["全期调仓期末最大回撤"]];
  factorSheet.getRange("AE4").values = [["2021—2025调仓期末最大回撤"]];

  const mainSheetRowByCalendar = new Map(
    orderedResults.map((item, index) => [item.calendarCode, mainFirstDataRow + index]),
  );
  for (let index = 0; index < januaryCodes.length; index += 1) {
    const workbookRow = 5 + index;
    const item = resultByCalendar.get(januaryCodes[index]);
    const implementationNote = [String(item.statusRow.note ?? "").trim(), item.extraNote ?? ""]
      .filter(Boolean)
      .join("；");
    factorSheet.getRange(`F${workbookRow}:H${workbookRow}`).values = [[
      String(item.statusRow.source_frequency),
      signalFrequencyText(item.statusRow),
      windowText(item.resultFactor),
    ]];
    factorSheet.getRange(`M${workbookRow}:P${workbookRow}`).values = [[
      dataStatusText(item.statusRow),
      availabilityText(item.strictStatus),
      backtestStatusText(item.strictStatus),
      implementationNote,
    ]];
    factorSheet.getRange(`AH${workbookRow}`).values = [[returnHorizonText(item.statusRow)]];
    factorSheet.getRange(`AG${workbookRow}`).values = [["2016-01-04至2025-12-31"]];
    factorSheet.getRange(`AK${workbookRow}`).values = [[limitationText(item.statusRow)]];
    factorSheet.getRange(`AL${workbookRow}`).values = [[runDate]];

    if (item.kind !== "原式未测") {
      const sourceRow = mainSheetRowByCalendar.get(item.calendarCode);
      const sourceColumns = ["H", "I", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X"];
      factorSheet.getRange(`Q${workbookRow}:AF${workbookRow}`).formulas = [[
        ...sourceColumns.map((column) => `='${SHEET_NAME}'!${column}${sourceRow}`),
      ]];
    }

    const availabilityCell = factorSheet.getRange(`N${workbookRow}`);
    if (item.kind === "可严格/按卡片参数测") {
      availabilityCell.format = { fill: "#E2F0D9", font: { color: "#006100", bold: true } };
    } else if (item.kind === "1分钟近似") {
      availabilityCell.format = { fill: "#FFF2CC", font: { color: "#7F6000", bold: true } };
    } else {
      availabilityCell.format = { fill: "#F4CCCC", font: { color: "#9C0006", bold: true } };
    }
  }

  // “总览”的计数改为按状态开头统计，能识别新加入的分钟回测说明。
  const overviewSheet = workbook.worksheets.getItem("总览");
  overviewSheet.getRange("A2").values = [[
    `一月16个｜三月新增15个｜已补一月分钟原式与卡片参数回测｜更新日期：${runDateText}`,
  ]];
  overviewSheet.getRange("A3").values = [[
    "下方原有结果区为历史日线改版（统一5日）；本次一月分钟原式及原频率结果见“一月分钟原式回测”。",
  ]];
  overviewSheet.getRange("A8").values = [["历史日线因子版本"]];
  overviewSheet.getRange("A23").values = [["历史日线改版重点结果（统一5日）"]];
  overviewSheet.getRange("C5").formulas = [["=COUNTIF('因子总库'!$O$5:$O$35,\"已回测*\")"]];
  overviewSheet.getRange("E5").values = [[15]];
  overviewSheet.getRange("E5").format.numberFormat = "0";
  overviewSheet.getRange("G5").formulas = [["=COUNTIF('因子总库'!$O$5:$O$35,\"待回测*\")"]];
  overviewSheet.getRange("I5").formulas = [["=COUNTIF('因子总库'!$O$5:$O$35,\"待补参数*\")"]];
  overviewSheet.getRange("K5").formulas = [["=COUNTIF('因子总库'!$O$5:$O$35,\"待补数据*\")"]];

  const oldSheetNotes = [
    ["回测结果", "历史日线改版回测结果（统一5日）", "旧版统一5日结果；本次分钟原式及原频率结果见“一月分钟原式回测”。"],
    ["分年表现", "历史日线改版分年表现（统一5日）", "旧版统一5日结果；本次分钟原式分年结果见“一月分钟原式回测”。"],
    ["回测总结", "历史日线改版回测总结（统一5日）", "旧版总结；本次分钟原式结论以“一月分钟原式回测”和最新报告为准。"],
    ["日线改版总结", "历史日线改版总结（统一5日）", "历史版本，只用于和分钟原式结果比较。"],
  ];
  for (const [sheetName, title, note] of oldSheetNotes) {
    const oldSheet = workbook.worksheets.getItem(sheetName);
    oldSheet.getRange("A1").values = [[title]];
    oldSheet.getRange("A2").values = [[note]];
  }

  const sheetInspection = await workbook.inspect({
    kind: "sheet",
    include: "id,name",
    maxChars: 10000,
  });
  const mainInspection = await workbook.inspect({
    kind: "region",
    sheetId: SHEET_NAME,
    range: `A1:Y${yearlyLastDataRow}`,
    maxChars: 24000,
  });
  const factorInspection = await workbook.inspect({
    kind: "region",
    sheetId: "因子总库",
    range: "M4:AF20",
    maxChars: 18000,
  });
  const factorFormulaInspection = await workbook.inspect({
    kind: "formula",
    sheetId: "因子总库",
    range: "Q5:AF20",
    maxChars: 16000,
    options: { maxResults: 300 },
  });
  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300, summary: "最终公式错误检查" },
    maxChars: 12000,
  });
  const errorMatches = parseNdjson(formulaErrors.ndjson).filter((record) => record.kind === "match");

  await fs.mkdir(args.previewDir, { recursive: true });
  await fs.mkdir(args.inspectDir, { recursive: true });
  await Promise.all([
    fs.writeFile(path.join(args.inspectDir, "工作表清单.inspect.ndjson"), sheetInspection.ndjson, "utf8"),
    fs.writeFile(path.join(args.inspectDir, "一月分钟原式回测.inspect.ndjson"), mainInspection.ndjson, "utf8"),
    fs.writeFile(path.join(args.inspectDir, "因子总库一月行.inspect.ndjson"), factorInspection.ndjson, "utf8"),
    fs.writeFile(path.join(args.inspectDir, "因子总库一月公式.inspect.ndjson"), factorFormulaInspection.ndjson, "utf8"),
    fs.writeFile(path.join(args.inspectDir, "公式错误检查.inspect.ndjson"), formulaErrors.ndjson, "utf8"),
  ]);
  if (errorMatches.length > 0) {
    throw new Error(`发现${errorMatches.length}处公式错误，已把检查结果写入：${args.inspectDir}`);
  }

  const finalSheetNames = [...originalSheetNames, SHEET_NAME];
  const previewFiles = [];
  for (const sheetName of finalSheetNames) {
    const preview = await workbook.render({
      sheetName,
      autoCrop: "all",
      scale: 1,
      format: "png",
    });
    const previewPath = path.join(args.previewDir, `${safeFileName(sheetName)}.png`);
    await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
    previewFiles.push(previewPath);
  }

  await fs.mkdir(path.dirname(args.output), { recursive: true });
  const outputBlob = await SpreadsheetFile.exportXlsx(workbook);
  await outputBlob.save(args.output);

  const manifest = {
    input: args.input,
    output: args.output,
    sources: sourceFiles,
    classificationCounts: countByKind,
    sheetCountBefore: originalSheetNames.length,
    sheetCountAfter: finalSheetNames.length,
    previews: previewFiles,
    formulaErrorCount: 0,
    generatedAt: new Date().toISOString(),
  };
  await fs.writeFile(
    path.join(args.inspectDir, "运行摘要.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );

  process.stdout.write(
    [
      "临时工作簿已生成。",
      `输出：${args.output}`,
      `工作表预览：${args.previewDir}`,
      `检查结果：${args.inspectDir}`,
      `工作表数量：${originalSheetNames.length} -> ${finalSheetNames.length}`,
      "公式错误：0",
    ].join("\n") + "\n",
  );
}

main().catch((error) => {
  process.stderr.write(`[错误] ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
