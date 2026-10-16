const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element(extra = {}) {
  const listeners = {};
  return {
    value: '', checked: true, dataset: {}, textContent: '',
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    fire(name) { for (const fn of listeners[name] || []) fn({ target: this }); },
    ...extra,
  };
}

test('per-block choice previews before save and global choice returns to global preview', () => {
  const frame = element({ dataset: { base: '/blog/strip-preview/' }, src: '' });
  const status = element();
  const form = element();
  const global = element({ value: 'list' });
  const marker = element({ name: 'strip_layout_home_0', value: '' });
  const byId = {
    'strip-preview-frame': frame, 'strip-preview-status': status,
    'blog-settings-form': form, 'bs-strip-style': global,
    'bs-heading': element({ value: 'From the blog' }),
    'bs-eyebrow': element(), 'bs-subtitle': element(),
    'bs-count': element({ value: '1' }), 'bs-enabled': element(),
  };
  const document = {
    getElementById: (id) => byId[id] || null,
    querySelectorAll: (selector) => selector === 'select[name^="strip_layout_"]' ? [marker] : [],
  };
  const script = fs.readFileSync(path.join(__dirname, '../../static/js/blog_strip_preview.js'), 'utf8');
  vm.runInNewContext(script, { document, window: {}, setTimeout: (fn) => { fn(); return 1; }, clearTimeout() {} });

  assert.match(frame.src, /marker=home_0/);
  marker.value = 'front_line';
  marker.fire('change');
  assert.match(frame.src, /strip_layout_home_0=front_line/);
  global.value = 'cards';
  global.fire('change');
  assert.match(frame.src, /strip_style=cards/);
  assert.doesNotMatch(frame.src, /marker=home_0/);
  marker.value = '';
  global.value = 'list';
  form.fire('reset');
  assert.match(frame.src, /strip_style=list/);
});
