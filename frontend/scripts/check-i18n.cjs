const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const src = path.resolve(__dirname, '../src');
const readDictionary = (filename) => {
  const content = fs.readFileSync(path.join(src, 'i18n', filename), 'utf8');
  return JSON.parse(content.slice(content.indexOf('= ') + 2).trim().replace(/;$/, ''));
};
const english = readDictionary('ui-en.ts');
const templates = readDictionary('ui-templates-en.ts');
const problems = [];

const localeKeys = (filename) => new Set(
  [...fs.readFileSync(path.join(src, 'locales', filename), 'utf8').matchAll(/^\s*'([^']+)':/gm)]
    .map(([, key]) => key),
);
const chineseKeys = localeKeys('zh-CN.ts');
const englishKeys = localeKeys('en-US.ts');
for (const key of chineseKeys) if (!englishKeys.has(key)) problems.push(`Missing English locale key: ${key}`);
for (const key of englishKeys) if (!chineseKeys.has(key)) problems.push(`Missing Chinese locale key: ${key}`);

for (const [source, translated] of Object.entries({ ...english, ...templates })) {
  if (/[\u3400-\u9fff]/.test(translated)) problems.push(`Untranslated English copy: ${source}`);
}

const walk = (directory, visit) => {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.name.startsWith('.umi') || entry.name === 'i18n') continue;
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(filename, visit);
    else if (/\.tsx?$/.test(entry.name)) visit(filename);
  }
};

walk(src, (filename) => {
  const source = ts.createSourceFile(
    filename,
    fs.readFileSync(filename, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    filename.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const visit = (node) => {
    if (ts.isJsxText(node) && /[\u3400-\u9fff]/.test(node.text)) {
      problems.push(`${filename}: untranslated JSX text ${node.text.trim()}`);
    }
    if (ts.isCallExpression(node) && ['tr', 'trf'].includes(node.expression.getText(source))) {
      const key = node.arguments[0];
      if (key && ts.isStringLiteral(key)) {
        const dictionary = node.expression.getText(source) === 'tr' ? english : templates;
        if (!dictionary[key.text]) problems.push(`${filename}: missing ${key.text}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
});

for (const [source, translated] of Object.entries(templates)) {
  const variables = (text) => [...text.matchAll(/__VAR\d+__/g)].map(([value]) => value).sort();
  if (JSON.stringify(variables(source)) !== JSON.stringify(variables(translated))) {
    problems.push(`Template variables differ: ${source}`);
  }
}

if (problems.length) {
  console.error(problems.join('\n'));
  process.exitCode = 1;
} else {
  console.log(`Checked ${Object.keys(english).length} UI messages and ${Object.keys(templates).length} templates.`);
}
