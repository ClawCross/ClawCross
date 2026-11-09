const assert = require('assert');
const path = require('path');

delete global.ClawcrossPretext;
delete global.ClawcrossTextLayout;

const textLayout = require(path.join(__dirname, '..', 'frontend', 'js', 'pretext_text_core.js'));

const singleLine = textLayout.fitSingleLine(
  '超级超级超级长的团队角色名称 AlphaBetaGammaDelta 🚀',
  96,
  { font: '12px Arial', lineHeight: 14, suffix: '…' }
);

assert.ok(singleLine.text.length > 0, 'fitSingleLine should return display text');
assert.ok(singleLine.truncated, 'fitSingleLine should truncate long text');
assert.ok(singleLine.lineCount <= 1, 'fitSingleLine should keep one line');
assert.ok(singleLine.width <= 96.5, 'fitSingleLine width should respect maxWidth');

const multiLine = textLayout.measureDisplay(
  '多语言文本布局 spring 春天到了 بدأت الرحلة 🚀',
  { font: '700 13px Arial', lineHeight: 16, maxWidth: 120, maxLines: 2 }
);

assert.ok(multiLine.lineCount >= 1, 'measureDisplay should return line count');
assert.ok(multiLine.height >= 16, 'measureDisplay should return height');
assert.ok(Array.isArray(multiLine.lines), 'measureDisplay should return line list');

const gutterWidth = textLayout.measureLabelGutter(
  ['短标签', 'A much longer label for measurement', '系统'],
  { font: '600 10px Arial', lineHeight: 12, minWidth: 110, maxWidth: 180, padding: 26 }
);

assert.ok(gutterWidth >= 110, 'measureLabelGutter should honor minWidth');
assert.ok(gutterWidth <= 180, 'measureLabelGutter should honor maxWidth');

console.log('pretext_text_core ok');
