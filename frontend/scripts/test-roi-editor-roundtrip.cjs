const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const workflowDir = path.resolve(__dirname, '../src/pages/workflows');
const editor = ts.createSourceFile(
  'editor.tsx', fs.readFileSync(path.join(workflowDir, 'editor/index.tsx'), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);

function findVariable(source, name) {
  let found;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) {
      assert.equal(found, undefined, `Ambiguous variable ${name}`);
      found = node;
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(found, `Missing variable ${name}`);
  return found;
}

// Execute the editor's actual load/save callbacks, without mounting the page or
// reproducing its serialization logic in the test.
function editorMapper(name) {
  const callback = findVariable(editor, name).initializer.arguments[0];
  assert.ok(ts.isArrowFunction(callback));
  const source = ts.createPrinter().printNode(ts.EmitHint.Expression, callback, editor);
  const compiled = ts.transpileModule(`(${source})`, {
    compilerOptions: { target: ts.ScriptTarget.ES2020 },
  }).outputText;
  return vm.runInNewContext(compiled, {
    data: { is_template: false },
    tr: (value) => value,
    console: { log() {} },
  });
}

const load = editorMapper('convertedNodes');
const save = editorMapper('saveNodes');
const registry = ts.createSourceFile(
  'nodes.tsx', fs.readFileSync(path.join(workflowDir, 'components/nodes/index.tsx'), 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX,
);
const renderTypes = new Set(findVariable(registry, 'nodeTypes').initializer.properties
  .map((property) => property.name.getText(registry)));
const regions = ['post_filter', 'pre_mask', 'crop_infer'].map((mode, index) => ({
  name: `区域 ${index + 1}`, mode, anchor: 'bottom_center',
  polygon: [{ x: 0.1, y: 0.2 }, { x: 0.9, y: 0.2 }, { x: 0.5, y: 0.8 }],
}));
const plain = (value) => JSON.parse(JSON.stringify(value));
let count = 0;

for (const type of ['roi', 'roi_draw']) {
  for (const field of ['roiRegions', 'roi_regions']) {
    for (const reactFlowShape of [false, true]) {
      const stored = {
        id: 'entry-roi', type, name: '入口 ROI', x: 260, y: 160,
        data: { type, [field]: regions },
        ...(reactFlowShape ? { position: { x: 260, y: 160 } } : {}),
      };
      const loaded = load(stored);
      assert.equal(loaded.type, 'roi');
      assert.equal(loaded.data.type, 'roi');
      assert.ok(renderTypes.has(loaded.type), 'Loaded node must have a registered renderer');
      assert.deepEqual(plain(loaded.data.roiRegions), regions);
      const saved = save(loaded);
      assert.equal(saved.type, 'roi');
      assert.equal(saved.id, stored.id);
      assert.deepEqual(plain(saved.data.roiRegions), regions);
      assert.deepEqual(plain(save(load(saved)).data.roiRegions), regions);
      count += 1;
    }
  }
}

// An explicit empty list must remain cleared even if a stale legacy alias exists.
for (const position of [undefined, { x: 260, y: 160 }]) {
  const loaded = load({
    id: 'cleared-roi', type: 'roi_draw', position,
    data: { type: 'roi_draw', roiRegions: [], roi_regions: regions },
  });
  assert.deepEqual(plain(save(loaded).data.roiRegions), []);
  count += 1;
}

console.log(`${count} ROI editor load/save round-trip scenarios passed`);
