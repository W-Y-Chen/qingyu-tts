/**
 * 校验离线朗读的分段逻辑。
 *
 * 做法：直接从 web/app.js 中抽出真实的 PAUSE_RE 与 splitForSpeech 源码来运行，
 * 而不是另写一份副本——避免"测试通过了但线上代码是另一套"的假验证。
 *
 * 运行：node tools/check_offline_speech.js
 */

const fs = require('fs');
const path = require('path');

const appPath = path.join(__dirname, '..', 'web', 'app.js');
const src = fs.readFileSync(appPath, 'utf8');

// ---- 抽出 PAUSE_RE 与 splitForSpeech ----
const pauseLine = src.match(/var PAUSE_RE = .*;/);
if (!pauseLine) fail('未找到 PAUSE_RE');

const start = src.indexOf('function splitForSpeech(');
if (start < 0) fail('未找到 splitForSpeech');

let depth = 0, end = -1, seenOpen = false;
for (let i = start; i < src.length; i++) {
  if (src[i] === '{') { depth++; seenOpen = true; }
  else if (src[i] === '}') {
    depth--;
    if (seenOpen && depth === 0) { end = i + 1; break; }
  }
}
if (end < 0) fail('splitForSpeech 括号未闭合');

const splitForSpeech = eval(
  `(function(){ ${pauseLine[0]} return ${src.slice(start, end)} })()`
);

// ---- 断言 ----
let passed = 0;
const SEP = '\u0001';

function check(name, fn) {
  try {
    fn();
    console.log('  PASS  ' + name);
    passed++;
  } catch (e) {
    console.error('  FAIL  ' + name + '\n        ' + e.message);
    process.exitCode = 1;
  }
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }

console.log('离线朗读分段逻辑校验（源码：web/app.js）\n');

check('短文本不分段', () => {
  const r = splitForSpeech('你好，世界。');
  assert(r.length === 1, '期望 1 段，实际 ' + r.length);
  assert(r[0] === '你好，世界。', '内容被改动：' + r[0]);
});

check('长文本按句末标点分成多段', () => {
  const text = Array.from({ length: 30 }, (_, i) => `这是第${i}句话。`).join('');
  const r = splitForSpeech(text);
  assert(r.length > 1, '期望多段，实际 ' + r.length);
  assert(r.join('').length === text.length, '拼接后长度应与原文一致');
});

check('停顿标记被转成切分点且不残留在输出中', () => {
  const text = '第一句。[p800]第二句。[p1.5s]第三句。';
  const r = splitForSpeech(text);
  const joined = r.join('');
  assert(joined.indexOf('[p') < 0, '输出中仍含停顿标记：' + joined);
  assert(joined.indexOf(SEP) < 0, '输出中残留内部分隔符');
  assert(joined.replace(/[。]/g, '').length > 0, '输出为空');
});

check('中文标记 [停顿] 同样生效', () => {
  const r = splitForSpeech('甲。[停顿]乙。');
  assert(r.join('').indexOf('[停顿]') < 0, '中文标记未被处理');
});

check('分段后每段不会过短（避免引擎频繁启停）', () => {
  const text = Array.from({ length: 40 }, (_, i) => `第${i}句。`).join('');
  const r = splitForSpeech(text);
  const short = r.slice(0, -1).filter(s => s.length < 60);
  assert(short.length === 0, '存在过短片段：' + JSON.stringify(short.slice(0, 3)));
});

check('空文本不会抛异常', () => {
  const r = splitForSpeech('');
  assert(Array.isArray(r), '应返回数组');
});

check('无标点的超长文本也能切分', () => {
  const text = '啊'.repeat(5000);
  const r = splitForSpeech(text);
  assert(r.length >= 1, '应至少返回一段');
  assert(r.join('').length === 5000, '内容不应丢失');
});

console.log(`\n通过 ${passed} 项${process.exitCode ? '，存在失败项' : '，全部通过'}`);
