import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "D:/大学/金融投资与量化/因子日历测试/因子日历2026因子库.xlsx";
const input = await FileBlob.load(path);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheets = await workbook.inspect({
  kind: "workbook,sheet,table",
  include: "id,name,range,values,formulas",
  maxChars: 16000,
  tableMaxRows: 8,
  tableMaxCols: 30,
  tableMaxCellChars: 120,
});
process.stdout.write(sheets.ndjson);
const factorSheet = workbook.worksheets.getItem("因子总库");
process.stdout.write("\nFACTOR_ROWS\n");
process.stdout.write(JSON.stringify(factorSheet.getRange("A4:AL20").values));
