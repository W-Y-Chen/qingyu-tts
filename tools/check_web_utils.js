/**
 * 校验「我的资料 / 下载文件名 / 收听进度」这几个前端逻辑。
 *
 * 同样遵守一条规矩：直接从 web/app.js 抽出真实源码运行，不另写副本，
 * 否则会出现"测试通过但页面行为不同"的假验证。
 *
 * 运行：node tools/check_web_utils.js
 */

const fs = require('fs');
const path = require('path');

const appPath = path.join(__dirname, '..', 'web', 'app.js');
const src = fs.readFileSync(appPath, 'utf8');

/** 从源文件中按大括号配平抽出某个具名函数的完整源码 */
function extract(name) {
  const marker = 'function ' + name + '(';
  const start = src.indexOf(marker);
  if (start < 0) throw new Error('未找到 ' + name);

  let depth = 0, end = -1, seenOpen = false;
  for (let i = start; i < src.length; i++) {
    if (src[i] === '{') { depth++; seenOpen = true; }
    else if (src[i] === '}') {
      depth--;
      if (seenOpen && depth === 0) { end = i + 1; break; }
    }
  }
  if (end < 0) throw new Error(name + ' 括号未闭合');
  return src.slice(start, end);
}

function loadAll(names) {
  const body = names.map(extract).join('\n');
  return eval(`(function(){ ${body}\n return { ${names.join(', ')} }; })()`);
}

const u = loadAll(['safeFileName', 'hash32', 'docKeyOf', 'prunePosMap']);

// ---- 断言框架 ----
let passed = 0;
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

console.log('前端资料/下载/进度逻辑校验（源码：web/app.js）\n');

// ---------------- 下载文件名 ----------------

check('Windows 非法字符全部被替换', () => {
  const bad = '第3章:绪论/肌肉?生长*原理<上>|"下"\\补充';
  const out = u.safeFileName(bad);
  assert(!/[\\/:*?"<>|]/.test(out), '仍含非法字符：' + out);
});

check('资料名能保留中文并用作下载文件名', () => {
  const out = u.safeFileName('运动生理学 第3章');
  assert(out === '运动生理学 第3章', '结果不符：' + out);
});

check('连续空白被折叠成一个空格', () => {
  const out = u.safeFileName('A   　 \t B');
  assert(out.indexOf('  ') < 0, '存在连续空格：' + JSON.stringify(out));
});

check('超长资料名被截断到 40 字（避免文件系统拒绝）', () => {
  const out = u.safeFileName('资'.repeat(200));
  assert(out.length === 40, '长度 ' + out.length + '，期望 40');
});

check('空资料名返回空串而不是 undefined', () => {
  assert(u.safeFileName('') === '', '应为空串');
  assert(u.safeFileName(null) === '', 'null 应为空串');
  assert(u.safeFileName(undefined) === '', 'undefined 应为空串');
});

// ---------------- 收听进度键 ----------------

check('同样的文本+音色+语速得到同一个键', () => {
  const a = u.docKeyOf('同一份资料', 'zh-CN-XiaoxiaoNeural', 0);
  const b = u.docKeyOf('同一份资料', 'zh-CN-XiaoxiaoNeural', 0);
  assert(a === b, '两次结果不同：' + a + ' vs ' + b);
});

check('文本变化后键必须变（否则会串到别的资料）', () => {
  const a = u.docKeyOf('资料甲', 'v', 0);
  const b = u.docKeyOf('资料乙', 'v', 0);
  assert(a !== b, '不同文本得到相同键，会导致进度串台');
});

check('音色或语速变化后键也要变（换音色重听应从头开始）', () => {
  const base = u.docKeyOf('长资料', 'v1', 0);
  assert(u.docKeyOf('长资料', 'v2', 0) !== base, '换音色后键未变');
  assert(u.docKeyOf('长资料', 'v1', 20) !== base, '换语速后键未变');
});

check('20000 字长文本也能算出稳定且非空的键', () => {
  const t = '肌肉收缩的基本原理是'.repeat(1000);
  const k = u.docKeyOf(t, 'v', 0);
  assert(typeof k === 'string' && k.length > 0, '键为空');
  assert(u.docKeyOf(t, 'v', 0) === k, '长文本哈希不稳定');
});

check('空文本不抛异常', () => {
  const k = u.docKeyOf('', '', 0);
  assert(typeof k === 'string', '应返回字符串');
});

// ---------------- 进度表裁剪 ----------------

check('条目数不超过上限时原样返回', () => {
  const m = { a: 1, b: 2 };
  const out = u.prunePosMap(m, 30);
  assert(out.a === 1 && out.b === 2, '内容被误删');
});

check('超出上限时只保留最近 30 条', () => {
  const m = {};
  for (let i = 0; i < 50; i++) m['k' + i] = i;
  const out = u.prunePosMap(m, 30);
  const keys = Object.keys(out);
  assert(keys.length === 30, '实际保留 ' + keys.length + ' 条');
  assert(out.k49 === 49, '最新的记录被删掉了');
  assert(out.k0 === undefined, '最旧的记录没有删掉');
});

check('裁剪不会修改传入的原对象', () => {
  const m = { a: 1, b: 2 };
  u.prunePosMap(m, 1);
  assert(Object.keys(m).length === 2, '原对象被就地修改');
});

console.log(`\n通过 ${passed} 项${process.exitCode ? '，存在失败项' : '，全部通过'}`);
