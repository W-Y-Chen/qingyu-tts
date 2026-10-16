/* 轻语 TTS 网页版
 * 手机与电脑浏览器通用。默认与后端同源部署（由 Flask 提供本页），
 * 也可在页脚修改后端地址用于前后端分离部署。
 */

(function () {
  'use strict';

  var MAX_CHARS = 20000;
  var LS_API = 'qingyu_api';
  var LS_PREFS = 'qingyu_prefs';
  var LS_HISTORY = 'qingyu_history';
  var LS_DOCS = 'qingyu_docs';
  var LS_POS = 'qingyu_pos';
  var LS_THEME = 'qingyu_theme';
  var LS_LOCAL_VOICE = 'qingyu_local_voice';
  var PAUSE_RE = /\[\s*(?:p|停顿)\s*[:：]?\s*(?:\d+(?:\.\d+)?\s*(?:ms|s)?)?\s*\]/gi;

  var ICON_PLAY = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
  var ICON_PAUSE = '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';
  var ICON_SUN = '<svg viewBox="0 0 24 24"><path d="M12 17a5 5 0 100-10 5 5 0 000 10zm0-13.5a1 1 0 011 1V6a1 1 0 01-2 0V4.5a1 1 0 011-1zm0 15a1 1 0 011 1V21a1 1 0 01-2 0v-1.5a1 1 0 011-1zM4.5 12a1 1 0 011-1H7a1 1 0 010 2H5.5a1 1 0 01-1-1zm15 0a1 1 0 011-1H22a1 1 0 010 2h-1.5a1 1 0 01-1-1zM6.99 5.64a1 1 0 011.41 0l1.06 1.06a1 1 0 01-1.41 1.41L6.99 7.05a1 1 0 010-1.41zm10.61 10.61a1 1 0 011.41 0l1.06 1.06a1 1 0 01-1.41 1.41l-1.06-1.06a1 1 0 010-1.41zM17.66 5.64a1 1 0 010 1.41l-1.06 1.06a1 1 0 01-1.41-1.41l1.06-1.06a1 1 0 011.41 0zM7.05 16.25a1 1 0 010 1.41L5.99 18.72a1 1 0 01-1.41-1.41l1.06-1.06a1 1 0 011.41 0z"/></svg>';
  var ICON_MOON = '<svg viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>';

  // ------------------------------------------------------------------ 工具

  function $(id) { return document.getElementById(id); }

  function pad(n) { return String(n).padStart(2, '0'); }

  function fmtTime(sec) {
    var s = Math.max(0, Math.round(Number(sec) || 0));
    return pad(Math.floor(s / 60)) + ':' + pad(s % 60);
  }

  function pct(v) {
    var n = Number(v) || 0;
    return n === 0 ? '正常' : (n > 0 ? '+' + n + '%' : n + '%');
  }

  function fmtDateTime(ts) {
    var d = new Date(ts);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
           ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function notice(msg, kind) {
    var el = $('notice');
    if (!msg) { el.hidden = true; return; }
    el.hidden = false;
    el.className = 'notice' + (kind ? ' ' + kind : ' info');
    el.textContent = msg;
  }

  function setStatus(ok, text) {
    $('statusPill').className = 'status ' + (ok ? 'on' : 'off');
    $('statusText').textContent = text;
  }

  // 构建单文件版时可由 window.QINGYU_DEFAULT_API 注入默认后端地址，
  // 这样把 HTML 传到手机上直接打开就能连到电脑，无需手输地址。
  var DEFAULT_API = (window.QINGYU_DEFAULT_API || '').replace(/\/+$/, '');

  function apiBase() {
    var saved = localStorage.getItem(LS_API);
    if (saved === null) {
      if (DEFAULT_API) return DEFAULT_API;
      // 未手动设置时：网页由服务托管则用同源；
      // 用 file:// 直接打开时，默认连本机后端，避免刚打开就报未连接。
      return location.protocol === 'file:' ? 'http://127.0.0.1:5000' : '';
    }
    return saved.replace(/\/+$/, '');
  }

  function absUrl(relative) {
    if (!relative) return '';
    if (/^https?:\/\//.test(relative)) return relative;
    return apiBase() + relative;
  }

  async function api(path, options) {
    var res = await fetch(apiBase() + path, options || {});
    var ct = res.headers.get('content-type') || '';
    var body = ct.indexOf('application/json') >= 0 ? await res.json() : await res.text();
    if (!res.ok) {
      throw new Error((body && body.error) || ('服务返回状态码 ' + res.status));
    }
    return body;
  }

  function post(path, data) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data || {})
    });
  }

  // ------------------------------------------------------------------ 状态

  var state = {
    voices: [],
    audioUrl: '',
    duration: 0,
    filename: '',
    // 当前正在听的资料名。健身场景下用户是"听资料"而不是"听一个随机文件"，
    // 所以下载文件名要用资料名，否则在手机播放器里认不出来。
    docName: '',
    // 播放位置记忆的键：同一份资料 + 同一音色 + 同一语速视为同一条收听记录
    posKey: '',
    posSaveAt: 0,
    timer: null,
    busy: false,
    busyBtn: null
  };

  var prefs = (function () {
    try { return JSON.parse(localStorage.getItem(LS_PREFS)) || {}; }
    catch (e) { return {}; }
  })();

  function savePrefs() { localStorage.setItem(LS_PREFS, JSON.stringify(prefs)); }

  // ------------------------------------------------------------------ 主题

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    $('btnTheme').innerHTML = theme === 'dark' ? ICON_MOON : ICON_SUN;
  }

  function initTheme() {
    var saved = localStorage.getItem(LS_THEME);
    if (saved !== 'light' && saved !== 'dark') {
      saved = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    applyTheme(saved);
    $('btnTheme').addEventListener('click', function () {
      var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      localStorage.setItem(LS_THEME, next);
      applyTheme(next);
    });
  }

  // ------------------------------------------------------------------ 初始化

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();

    $('rate').value = prefs.rate || 0;
    $('volume').value = prefs.volume || 0;
    $('rateLabel').textContent = pct($('rate').value);
    $('volumeLabel').textContent = pct($('volume').value);

    bindEvents();
    bindPlayer();
    loadHealth();
    loadVoices();
    renderHistory();
    renderDocs();

    loadLocalVoices();
    if (localSupported() && 'onvoiceschanged' in window.speechSynthesis) {
      // 语音列表在很多浏览器里是异步就绪的
      window.speechSynthesis.onvoiceschanged = loadLocalVoices;
    }
    updateLocalButtons();
  });

  function bindEvents() {
    var text = $('text');
    text.addEventListener('input', updateStats);
    // 用户手动改了文本就不再是"那份资料"了，下载文件名要退回默认。
    // 注意：playDoc 里是给 value 赋值，不会触发 input 事件，所以载入资料不受影响。
    text.addEventListener('input', function () { state.docName = ''; });

    $('btnImport').addEventListener('click', function () { $('fileInput').click(); });
    $('fileInput').addEventListener('change', function (e) {
      if (e.target.files && e.target.files[0]) handleFile(e.target.files[0]);
      e.target.value = '';
    });

    var dz = $('dropzone');
    ['dragenter', 'dragover'].forEach(function (t) {
      dz.addEventListener(t, function (e) { e.preventDefault(); dz.classList.add('dragging'); });
    });
    ['dragleave', 'dragend'].forEach(function (t) {
      dz.addEventListener(t, function () { dz.classList.remove('dragging'); });
    });
    dz.addEventListener('drop', function (e) {
      e.preventDefault();
      dz.classList.remove('dragging');
      var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) handleFile(f);
    });

    $('btnClear').addEventListener('click', function () {
      if (!text.value) return;
      if (!confirm('确定要清空当前输入的全部文字吗？')) return;
      text.value = '';
      updateStats();
    });

    Array.prototype.forEach.call(document.querySelectorAll('.tag-btn[data-ms]'), function (btn) {
      btn.addEventListener('click', function () {
        var ms = Number(btn.dataset.ms) || 300;
        insertAtCursor(text, ms >= 1000 ? '[p' + (ms / 1000) + 's]' : '[p' + ms + ']');
        updateStats();
      });
    });

    $('btnPauseHelp').addEventListener('click', function () {
      alert('停顿标记语法：\n\n' +
        '[p] —— 默认停顿 400 毫秒\n' +
        '[p300] 或 [p300ms] —— 停顿 300 毫秒\n' +
        '[p1.5s] —— 停顿 1.5 秒\n' +
        '[停顿] / [停顿800] —— 中文写法\n\n' +
        '段落之间的空行会自动插入 500 毫秒停顿。\n' +
        '网页版会把标记插入光标所在位置。');
    });

    $('voice').addEventListener('change', function () {
      var v = state.voices[$('voice').selectedIndex];
      if (!v) return;
      prefs.voice = v.id;
      savePrefs();
      $('voiceDesc').textContent = v.desc || (v.name + '（' + v.gender + '声，' + (v.style || '通用') + '）');
    });

    $('rate').addEventListener('input', function () {
      $('rateLabel').textContent = pct(this.value);
      prefs.rate = Number(this.value);
      savePrefs();
    });
    $('volume').addEventListener('input', function () {
      $('volumeLabel').textContent = pct(this.value);
      prefs.volume = Number(this.value);
      savePrefs();
    });

    $('btnPreview').addEventListener('click', doPreview);
    $('btnGenerate').addEventListener('click', doGenerate);
    $('btnDownload').addEventListener('click', doDownload);
    $('btnCopyLink').addEventListener('click', doCopyLink);
    $('btnEditServer').addEventListener('click', editServer);
    $('btnLocalSpeak').addEventListener('click', localSpeak);
    $('btnLocalPause').addEventListener('click', localTogglePause);
    // 注意：不能直接把 stopLocal 当回调，事件对象会被当成 silent 参数
    $('btnLocalStop').addEventListener('click', function () { stopLocal(); });
    $('localVoice').addEventListener('change', function () {
      localStorage.setItem(LS_LOCAL_VOICE, this.value);
    });
    $('btnEditServer2').addEventListener('click', editServer);
    $('btnLocalRescan').addEventListener('click', function () {
      loadLocalVoices();
      notice('已重新检测系统语音引擎。');
    });
    $('btnSaveDoc').addEventListener('click', saveCurrentDoc);
  }

  function insertAtCursor(el, text) {
    var start = el.selectionStart == null ? el.value.length : el.selectionStart;
    var end = el.selectionEnd == null ? el.value.length : el.selectionEnd;
    el.value = el.value.slice(0, start) + text + el.value.slice(end);
    el.selectionStart = el.selectionEnd = start + text.length;
    el.focus();
  }

  function updateStats() {
    var t = $('text').value;
    var m = t.match(PAUSE_RE);
    $('charCount').textContent = t.length + ' 字';
    $('pauseCount').textContent = (m ? m.length : 0) + ' 处';
  }

  // ------------------------------------------------------------------ 播放器

  function bindPlayer() {
    var a = $('player');
    a.addEventListener('timeupdate', syncPlayer);
    a.addEventListener('loadedmetadata', function () {
      syncPlayer();
      maybeResume();
    });
    a.addEventListener('play', renderPlayIcon);
    a.addEventListener('pause', function () {
      renderPlayIcon();
      savePos($('player').currentTime || 0);
    });
    a.addEventListener('ended', function () {
      renderPlayIcon();
      savePos($('player').duration || 0);
      $('trackFill').style.width = '0%';
      $('curTime').textContent = '00:00';
    });
    a.addEventListener('error', function () {
      notice('音频加载失败，链接可能已过期（服务端默认保留 2 小时）', 'error');
    });
    $('btnPlay').addEventListener('click', togglePlay);
    $('track').addEventListener('click', seekByClick);
    renderPlayIcon();
  }

  function syncPlayer() {
    var a = $('player');
    var d = isFinite(a.duration) ? a.duration : 0;
    var c = a.currentTime || 0;
    $('curTime').textContent = fmtTime(c);
    $('totTime').textContent = fmtTime(d);
    $('trackFill').style.width = (d > 0 ? (c / d * 100) : 0) + '%';
    if (d > 0) $('durationText').textContent = fmtTime(d);

    // timeupdate 每秒触发好几次，没必要每次都写 localStorage
    var now = Date.now();
    if (now - state.posSaveAt > 5000) {
      state.posSaveAt = now;
      savePos(c);
    }
  }

  function renderPlayIcon() {
    $('btnPlay').innerHTML = $('player').paused ? ICON_PLAY : ICON_PAUSE;
  }

  function togglePlay() {
    var a = $('player');
    if (!a.src) { notice('还没有可播放的音频', 'warn'); return; }
    if (a.paused) tryPlay();
    else a.pause();
  }

  function seekByClick(e) {
    var a = $('player');
    if (!isFinite(a.duration) || a.duration <= 0) return;
    var rect = $('track').getBoundingClientRect();
    var ratio = (e.clientX - rect.left) / rect.width;
    ratio = Math.min(1, Math.max(0, ratio));
    a.currentTime = ratio * a.duration;
  }

  function tryPlay() {
    var p = $('player').play();
    if (p && p.catch) p.catch(function () { /* 自动播放被拦截时静默，用户可手动点播 */ });
  }

  // ------------------------------------------------------------------ 文件导入

  async function handleFile(file) {
    if (file.size > 2 * 1024 * 1024) {
      notice('文件超过 2MB，请拆分后导入', 'warn');
      return;
    }
    notice('正在读取文件…');
    try {
      var buf = await file.arrayBuffer();
      var text = new TextDecoder('utf-8').decode(buf);
      var encoding = 'UTF-8';
      if (text.indexOf('\uFFFD') >= 0) {
        // 出现替换字符说明不是 UTF-8，尝试中文常见编码
        var candidates = ['gb18030', 'gbk', 'big5'];
        for (var i = 0; i < candidates.length; i++) {
          try {
            var alt = new TextDecoder(candidates[i]).decode(buf);
            if (alt.indexOf('\uFFFD') < 0) { text = alt; encoding = candidates[i].toUpperCase(); break; }
          } catch (e) { /* 浏览器不支持该编码则跳过 */ }
        }
      }
      if (text.length > MAX_CHARS) {
        text = text.slice(0, MAX_CHARS);
        notice('文件已截断至 ' + MAX_CHARS + ' 字', 'warn');
      }
      var el = $('text');
      el.value = el.value ? el.value + '\n\n' + text : text;
      updateStats();
      notice('已读取 ' + text.length + ' 字（编码 ' + encoding + '）');
    } catch (e) {
      notice('读取失败：' + e.message, 'error');
    }
  }

  // ------------------------------------------------------------------ 服务自检与音色

  async function loadHealth() {
    try {
      var h = await api('/api/health');
      setStatus(true, '服务已连接');
      $('localTip').innerHTML =
        '后端可用时会优先使用后端合成（可导出 MP3）。此模式随时可用，不需要电脑和网络，' +
        '但<b>无法导出音频文件</b>。';
      $('serverInfo').textContent =
        '停顿标记' + (h.ssml_pause ? '可用' : '不可用') +
        ' · 无损合并' + (h.ffmpeg_merge ? '可用' : '不可用（时长可能有偏差）') +
        ' · 音色 ' + h.voices + ' 个（' + (h.voice_source === 'online' ? '在线' : '内置') + '）' +
        ' · 引擎 ' + (h.engine === 'local' ? '本地' : '云端') + (h.engine_degraded ? '（已降级）' : '');
    } catch (e) {
      setStatus(false, '未连接');
      $('localTip').innerHTML =
        '当前<b>连不上后端</b>，可直接使用下面的离线朗读，不需要电脑和网络。' +
        '注意：<b>这种方式无法导出 MP3 文件</b>。';
      $('serverInfo').textContent = '未连接到服务：' + e.message;
      notice('无法连接后端服务（' + apiBase() + '）。请确认电脑上的服务已启动、手机与电脑在同一 WiFi，或点「修改后端地址」更正。', 'error');
    }
  }

  async function loadVoices() {
    var sel = $('voice');
    try {
      var res = await api('/api/voices');
      state.voices = res.voices || [];
      if (!state.voices.length) throw new Error('音色列表为空');
      sel.innerHTML = '';
      state.voices.forEach(function (v) {
        var opt = document.createElement('option');
        opt.textContent = v.name + ' · ' + v.gender + ' · ' + (v.style || '通用');
        sel.appendChild(opt);
      });
      sel.disabled = false;
      var idx = state.voices.findIndex(function (v) { return v.id === prefs.voice; });
      sel.selectedIndex = idx < 0 ? 0 : idx;
      var cur = state.voices[sel.selectedIndex];
      prefs.voice = cur.id;
      savePrefs();
      $('voiceDesc').textContent = cur.desc || (cur.name + '（' + cur.gender + '声，' + (cur.style || '通用') + '）');
      $('voiceSource').textContent = (res.source === 'online' ? '在线' : '内置') + ' ' + res.count + ' 个';
    } catch (e) {
      sel.innerHTML = '<option>未连接到服务</option>';
      sel.disabled = true;
      $('voiceSource').textContent = '连接失败';
    }
  }

  function currentVoice() {
    var v = state.voices[$('voice').selectedIndex];
    return v ? v.id : 'zh-CN-XiaoxiaoNeural';
  }

  function currentVoiceName() {
    var v = state.voices[$('voice').selectedIndex];
    return v ? v.name : '默认';
  }

  function payload() {
    return {
      text: $('text').value,
      voice: currentVoice(),
      rate: Number($('rate').value),
      volume: Number($('volume').value)
    };
  }

  // ------------------------------------------------------------------ 合成

  function setBusy(busy, btn) {
    state.busy = busy;
    $('btnPreview').disabled = busy;
    $('btnGenerate').disabled = busy;
    if (btn) {
      state.busyBtn = btn;
      btn.innerHTML = '<span class="spinner"></span>' + (btn === $('btnPreview') ? '合成中' : '生成中');
    } else if (state.busyBtn) {
      state.busyBtn.textContent = state.busyBtn === $('btnPreview') ? '试听' : '生成完整音频';
      state.busyBtn = null;
    }
  }

  async function doPreview() {
    if (state.busy) return;
    if (!$('text').value.trim()) { notice('请先输入文本', 'warn'); return; }
    setBusy(true, $('btnPreview'));
    notice('正在合成试听…');
    try {
      var res = await post('/api/preview', payload());
      setAudio(res.audio_url, res.duration, false);
      notice(res.clipped ? '试听仅包含前 200 字，确认音色后点击「生成完整音频」' : '试听已就绪');
      if (res.engine === 'local') {
        notice('已使用本地语音引擎（云端不可达时自动降级），输出为 WAV 文件，可直接下载。');
      }
      tryPlay();
    } catch (e) {
      notice('试听失败：' + e.message, 'error');
    } finally {
      setBusy(false);
    }
  }

  async function doGenerate() {
    if (state.busy) return;
    var t = $('text').value;
    if (!t.trim()) { notice('请先输入文本', 'warn'); return; }
    if (t.length > MAX_CHARS) { notice('文本上限 ' + MAX_CHARS + ' 字', 'warn'); return; }

    setBusy(true, $('btnGenerate'));
    $('progressBox').hidden = false;
    $('barFill').style.width = '0%';
    $('progressText').textContent = '0%';
    $('segmentText').textContent = '已完成 0 / 0 段';
    notice('任务已提交，正在分段合成…');

    try {
      var res = await post('/api/synthesize', payload());
      startPoll(res.task_id);
    } catch (e) {
      $('progressBox').hidden = true;
      setBusy(false);
      notice('提交失败：' + e.message, 'error');
    }
  }

  function startPoll(taskId) {
    stopPoll();
    state.timer = setInterval(function () {
      api('/api/tasks/' + taskId).then(function (t) {
        var p = t.progress || 0;
        $('barFill').style.width = p + '%';
        $('progressText').textContent = p + '%';
        $('segmentText').textContent = '已完成 ' + (t.done_segments || 0) + ' / ' + (t.total_segments || 0) + ' 段';

        if (t.status === 'done') {
          stopPoll();
          $('progressBox').hidden = true;
          setBusy(false);
          setAudio(t.audio_url, t.duration);
          notice('生成完成：' + (t.total_segments || 1) + ' 段 / ' + fmtTime(t.duration) + ' / 耗时 ' + t.elapsed + ' 秒');
          if (t.engine === 'local') {
            notice('已使用本地语音引擎（云端不可达时自动降级），输出为 WAV 文件，可直接下载。');
          }
          tryPlay();
        } else if (t.status === 'error') {
          stopPoll();
          $('progressBox').hidden = true;
          setBusy(false);
          notice('合成失败：' + (t.error || '未知错误'), 'error');
        }
      }).catch(function () {
        /* 单次轮询失败不中断任务，等待下一轮 */
      });
    }, 1000);
  }

  function stopPoll() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  /** 从音频地址推断扩展名：本地引擎输出 wav，云端输出 mp3 */
  function extOf(url) {
    var m = String(url || '').split('?')[0].match(/\.(mp3|wav)$/i);
    return m ? m[1].toLowerCase() : 'mp3';
  }

  /** 文件名里不能出现 \ / : * ? " < > | ，手机/Windows 都会拒绝或截断 */
  function safeFileName(name) {
    var s = String(name || '').replace(/[\\/:*?"<>|\r\n\t]+/g, ' ').trim();
    s = s.replace(/\s+/g, ' ');
    return s.slice(0, 40);
  }

  /** 简单字符串哈希（djb2），用于给"这份文本 + 这个音色"生成稳定的收听进度键 */
  function hash32(str) {
    var h = 5381;
    for (var i = 0; i < str.length; i++) {
      h = ((h << 5) + h + str.charCodeAt(i)) >>> 0;
    }
    return h.toString(36);
  }

  function docKeyOf(text, voice, rate) {
    return hash32(String(text || '') + '|' + String(voice || '') + '|' + String(rate || 0));
  }

  /**
   * @param {boolean} trackPos 是否记忆收听进度。试听只有前 200 字，
   *   进度对整篇没有意义，必须关掉，否则会把"试听到第 8 秒"记成"整篇听到第 8 秒"。
   */
  function setAudio(url, duration, trackPos) {
    state.audioUrl = absUrl(url);
    state.duration = Number(duration) || 0;
    var ext = extOf(url);
    state.filename = (state.docName ? safeFileName(state.docName) : '轻语-' + timestamp()) + '.' + ext;
    if (trackPos === false) {
      state.posKey = '';
    } else {
      // 同一份文本 + 同一音色 + 同一语速视为同一条收听记录。
      // 健身时一份资料往往要分好几次听完，没有这个就只能从头再来。
      state.posKey = docKeyOf($('text').value, currentVoice(), $('rate').value);
    }
    $('playerCard').hidden = false;
    $('player').src = state.audioUrl;
    $('durationText').textContent = fmtTime(state.duration);
    $('totTime').textContent = fmtTime(state.duration);
    $('curTime').textContent = '00:00';
    $('trackFill').style.width = '0%';
    $('player').load();
    pushHistory();
  }

  // ---------------------------- 收听进度（按资料记忆）----------------------------

  function loadPosMap() {
    try { return JSON.parse(localStorage.getItem(LS_POS)) || {}; }
    catch (e) { return {}; }
  }

  /** 纯函数：把进度表裁剪到最近 keep 条。抽出来是为了能被工具脚本直接测试 */
  function prunePosMap(map, keep) {
    var keys = Object.keys(map);
    if (keys.length <= keep) return map;
    var out = {};
    keys.slice(-keep).forEach(function (k) { out[k] = map[k]; });
    return out;
  }

  function savePos(sec) {
    if (!state.posKey) return;
    try {
      var map = loadPosMap();
      // 听完超过 95% 就清除进度，下次从头开始
      if (state.duration && sec > state.duration * 0.95) delete map[state.posKey];
      else map[state.posKey] = Math.floor(sec);
      // 只保留最近 30 条，避免把 localStorage 撑满
      localStorage.setItem(LS_POS, JSON.stringify(prunePosMap(map, 30)));
    } catch (e) { /* 隐私模式下 localStorage 不可写，忽略 */ }
  }

  function savedPos() {
    if (!state.posKey) return 0;
    return Number(loadPosMap()[state.posKey]) || 0;
  }

  /** 元数据就绪后决定是否续播。太靠头/太靠尾的进度不值得跳 */
  function maybeResume() {
    var sec = savedPos();
    if (!sec || sec < 10) return;
    if (state.duration && sec > state.duration - 15) return;
    try {
      $('player').currentTime = sec;
      notice('已跳到上次听到的 ' + fmtTime(sec) + '，从头听请拖回进度条最左端');
    } catch (e) { /* 部分浏览器在无 Range 支持时会抛错，忽略 */ }
  }

  function timestamp() {
    var d = new Date();
    return '' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
           '-' + pad(d.getHours()) + pad(d.getMinutes());
  }

  // ------------------------------------------------------------------ 下载与历史

  async function doDownload() {
    if (!state.audioUrl) return;
    notice('正在下载…');
    try {
      var res = await fetch(state.audioUrl);
      if (!res.ok) throw new Error('下载失败，状态码 ' + res.status);
      var blob = await res.blob();
      var objUrl = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = objUrl;
      a.download = state.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(objUrl); }, 10000);
      notice('已开始下载：' + state.filename);
    } catch (e) {
      notice('下载失败：' + e.message, 'error');
    }
  }

  function doCopyLink() {
    if (!state.audioUrl) return;
    var text = state.audioUrl;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { notice('链接已复制'); },
        function () { notice('复制失败，请手动复制：' + text, 'warn'); }
      );
    } else {
      notice('当前浏览器不支持自动复制，链接为：' + text, 'warn');
    }
  }

  function pushHistory() {
    try {
      var list = JSON.parse(localStorage.getItem(LS_HISTORY)) || [];
      list.unshift({
        url: state.audioUrl,
        name: ($('text').value || '未命名').replace(/\s+/g, ' ').slice(0, 20),
        voice: currentVoiceName(),
        duration: state.duration,
        file: state.filename,
        time: Date.now()
      });
      localStorage.setItem(LS_HISTORY, JSON.stringify(list.slice(0, 20)));
      renderHistory();
    } catch (e) { /* 存储不可用时忽略 */ }
  }

  function renderHistory() {
    var list = [];
    try { list = JSON.parse(localStorage.getItem(LS_HISTORY)) || []; } catch (e) { list = []; }
    var box = $('historyList');
    $('historyCard').hidden = list.length === 0;
    box.innerHTML = '';
    list.forEach(function (item, index) {
      var li = document.createElement('li');

      var main = document.createElement('div');
      main.className = 'hist-main';
      var name = document.createElement('div');
      name.className = 'hist-name';
      name.textContent = item.name || '未命名';
      var meta = document.createElement('div');
      meta.className = 'hist-meta';
      meta.textContent = item.voice + ' · ' + fmtTime(item.duration) + ' · ' + fmtDateTime(item.time);
      main.appendChild(name);
      main.appendChild(meta);

      var play = document.createElement('button');
      play.className = 'hist-btn';
      play.textContent = '播放';
      play.addEventListener('click', function () {
        state.audioUrl = item.url;
        state.filename = item.file || ('轻语-' + timestamp() + '.' + extOf(item.url));
        // 历史记录里没有原文，无法算出 posKey。置空是为了避免把进度写到别的资料名下。
        state.posKey = '';
        state.docName = item.name || '';
        $('playerCard').hidden = false;
        $('player').src = item.url;
        $('durationText').textContent = fmtTime(item.duration);
        $('totTime').textContent = fmtTime(item.duration);
        $('curTime').textContent = '00:00';
        $('trackFill').style.width = '0%';
        $('player').load();
        tryPlay();
      });

      var down = document.createElement('button');
      down.className = 'hist-btn';
      down.textContent = '下载';
      down.addEventListener('click', function () {
        state.audioUrl = item.url;
        state.filename = item.file || ('轻语-' + timestamp() + '.' + extOf(item.url));
        doDownload();
      });

      var del = document.createElement('button');
      del.className = 'hist-btn del';
      del.textContent = '删除';
      del.addEventListener('click', function () {
        list.splice(index, 1);
        localStorage.setItem(LS_HISTORY, JSON.stringify(list));
        renderHistory();
      });

      li.appendChild(main);
      li.appendChild(play);
      li.appendChild(down);
      li.appendChild(del);
      box.appendChild(li);
    });
  }

  // ---------------------------- 离线朗读（手机本地语音引擎）----------------------------
  // 不依赖后端：用浏览器内置的 Web Speech API。缺点是无法导出音频文件，
  // 音色取决于系统是否装有中文语音引擎。

  var local = { chunks: [], idx: 0, running: false, paused: false };

  function localSupported() {
    return 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function';
  }

  function localRate() { return Math.min(2, Math.max(0.5, 1 + Number($('rate').value) / 100)); }
  function localVolume() { return Math.min(1, Math.max(0, (100 + Number($('volume').value)) / 100)); }

  /** 切分朗读块：句末标点、空行、自定义停顿标记都作为切分点 */
  function splitForSpeech(raw) {
    var text = raw.replace(PAUSE_RE, '\u0001');   // 停顿标记转成切分点
    var ends = '。！？!?；;\n';
    var parts = [];
    var buf = '';
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      buf += ch;
      if (ends.indexOf(ch) >= 0 || ch === '\u0001') {
        if (buf.replace(/\u0001/g, '').trim()) parts.push(buf.replace(/\u0001/g, '').trim());
        buf = '';
      }
    }
    if (buf.replace(/\u0001/g, '').trim()) parts.push(buf.replace(/\u0001/g, '').trim());

    // 片段过短会让引擎频繁启停，合并到约 60 字一段
    var merged = [];
    var cur = '';
    for (var j = 0; j < parts.length; j++) {
      cur += parts[j];
      if (cur.length >= 60) { merged.push(cur); cur = ''; }
    }
    if (cur.trim()) merged.push(cur);
    return merged.length ? merged : [raw];
  }

  /** 微信内置浏览器不支持 Web Speech API，且是用户最常见的使用场景，单独识别并给出指引 */
  function inWeChat() {
    return /MicroMessenger/i.test(navigator.userAgent || '');
  }

  function loadLocalVoices() {
    var sel = $('localVoice');
    var tip = $('localVoiceTip');

    if (!localSupported()) {
      sel.innerHTML = '<option>浏览器不支持本地朗读</option>';
      sel.disabled = true;
      tip.innerHTML = inWeChat()
        ? '<b>检测到微信内置浏览器</b>：它不支持网页的语音合成能力。'
          + '请点右上角「···」→「在浏览器打开」，再使用此功能。'
        : '当前浏览器不支持本地朗读（缺少 Web Speech API）。'
          + '请改用 Chrome / Safari / Edge 等浏览器打开，或使用后端合成。';
      return;
    }

    var all = window.speechSynthesis.getVoices() || [];
    if (!all.length) {
      sel.innerHTML = '<option>未检测到系统语音引擎</option>';
      sel.disabled = true;
      tip.innerHTML = inWeChat()
        ? '<b>检测到微信内置浏览器</b>：它读不到系统语音引擎，'
          + '请点右上角「···」→「在浏览器打开」。'
        : '浏览器支持朗读，但没有读到任何系统语音引擎。安卓设备通常需要安装'
          + '「Google 文字转语音」或厂商自带的中文语音包；也可以点下方'
          + '「重新检测语音」再试一次。';
      return;
    }

    var zh = all.filter(function (v) { return /^zh/i.test(v.lang || ''); });
    var list = zh.length ? zh : all;

    sel.innerHTML = '';
    list.forEach(function (v) {
      var opt = document.createElement('option');
      opt.textContent = v.name + '（' + v.lang + '）';
      opt.value = v.voiceURI || v.name;
      sel.appendChild(opt);
    });
    sel.disabled = false;

    var saved = localStorage.getItem(LS_LOCAL_VOICE);
    var idx = -1;
    for (var i = 0; i < list.length; i++) {
      if ((list[i].voiceURI || list[i].name) === saved) { idx = i; break; }
    }
    sel.selectedIndex = idx < 0 ? 0 : idx;

    $('localVoiceTip').textContent = zh.length
      ? '共 ' + zh.length + ' 个中文语音' +
        (zh.length < all.length ? '，另有 ' + (all.length - zh.length) + ' 个非中文语音未列出' : '')
      : '系统未安装中文语音引擎，朗读中文可能发音异常。';
  }

  function currentLocalVoice() {
    var sel = $('localVoice');
    if (!sel || sel.disabled || !sel.value) return null;
    var all = window.speechSynthesis.getVoices() || [];
    for (var i = 0; i < all.length; i++) {
      if ((all[i].voiceURI || all[i].name) === sel.value) return all[i];
    }
    return null;
  }

  function localSpeak() {
    var raw = $('text').value;
    if (!raw.trim()) { notice('请先输入文本', 'warn'); return; }
    if (!localSupported()) { notice('当前浏览器不支持本地朗读', 'warn'); return; }

    stopLocal(true);
    local.chunks = splitForSpeech(raw);
    local.idx = 0;
    local.running = true;
    local.paused = false;
    speakChunk();
    updateLocalButtons();
  }

  function speakChunk() {
    if (!local.running) return;
    if (local.idx >= local.chunks.length) { finishLocal('朗读结束'); return; }

    var u = new window.SpeechSynthesisUtterance(local.chunks[local.idx]);
    var v = currentLocalVoice();
    if (v) { u.voice = v; u.lang = v.lang; } else { u.lang = 'zh-CN'; }
    u.rate = localRate();
    u.volume = localVolume();

    u.onend = function () {
      if (!local.running) return;      // 已被停止，忽略收尾事件
      local.idx++;
      speakChunk();
      if (local.running) updateLocalProgress();
    };
    u.onerror = function (e) {
      if (!local.running) return;   // 由 stop/cancel 触发，属预期行为，不提示

      var code = (e && e.error) || 'unknown';

      // 正常停止：状态栏保持简短，避免长文案把标题挤换行
      if (code === 'interrupted' || code === 'canceled') {
        finishLocal('已停止');
        return;
      }

      var msg;
      if (code === 'synthesis-failed') {
        msg = '系统语音引擎调用失败：设备可能没有可用的中文语音引擎。'
            + (inWeChat() ? '另外微信内置浏览器不支持本地朗读，请点右上角「···」→「在浏览器打开」。' : '');
      } else if (code === 'not-allowed') {
        msg = '浏览器阻止了朗读，请先点击页面任意位置后再试。';
      } else if (code === 'audio-busy' || code === 'audio-hardware') {
        msg = '设备音频被占用，请关闭其它正在播放的声音后重试。';
      } else {
        msg = '朗读中断（' + code + '）。';
      }
      finishLocal('已中断');
      notice(msg, 'warn');
    };

    updateLocalProgress();
    try {
      window.speechSynthesis.speak(u);
    } catch (err) {
      finishLocal('朗读失败：' + err.message);
    }
  }

  function finishLocal(msg) {
    local.running = false;
    local.paused = false;
    if (localSupported()) window.speechSynthesis.cancel();
    setLocalState(msg);
    updateLocalButtons();
  }

  function stopLocal(silent) {
    local.running = false;
    local.paused = false;
    if (localSupported()) window.speechSynthesis.cancel();
    if (!silent) { setLocalState('已停止'); updateLocalButtons(); }
  }

  function localTogglePause() {
    if (!local.running) return;
    if (local.paused) {
      window.speechSynthesis.resume();
      local.paused = false;
      updateLocalProgress();
    } else {
      window.speechSynthesis.pause();
      local.paused = true;
      setLocalState('已暂停 · 第 ' + (local.idx + 1) + ' / ' + local.chunks.length + ' 段');
    }
    updateLocalButtons();
  }

  function updateLocalProgress() {
    var n = Math.min(local.idx + 1, local.chunks.length);
    setLocalState('第 ' + n + ' / ' + local.chunks.length + ' 段');
  }

  function updateLocalButtons() {
    $('btnLocalSpeak').disabled = local.running;
    $('btnLocalPause').disabled = !local.running;
    $('btnLocalStop').disabled = !local.running;
    $('btnLocalPause').textContent = local.paused ? '继续' : '暂停';
  }

  function setLocalState(text) {
    var el = $('localState');
    if (el) el.textContent = text || '';
  }

  // ---------------------------- 我的资料（健身/通勤场景的核心）----------------------------
  // 用户的真实诉求是"健身时听自己的资料"，所以资料必须能存下来反复收听，
  // 而不是每次重新粘贴。资料存在浏览器本地，不上传服务器。

  function loadDocs() {
    try { return JSON.parse(localStorage.getItem(LS_DOCS)) || []; }
    catch (e) { return []; }
  }

  function saveDocs(list) {
    try { localStorage.setItem(LS_DOCS, JSON.stringify((list || []).slice(0, 50))); }
    catch (e) { notice('本地存储空间不足，请先删除一些资料', 'warn'); }
  }

  function renderDocs() {
    var list = loadDocs();
    var box = $('docsList');
    $('docsCount').textContent = list.length + ' 篇';
    $('docsEmpty').hidden = list.length > 0;
    box.innerHTML = '';

    list.forEach(function (d, index) {
      var li = document.createElement('li');

      var main = document.createElement('div');
      main.className = 'doc-main';
      var name = document.createElement('div');
      name.className = 'doc-name';
      name.textContent = d.name || '未命名资料';
      var meta = document.createElement('div');
      meta.className = 'doc-meta';
      meta.textContent = (d.chars || 0) + ' 字 · ' + (d.voiceName || '默认音色') +
                         ' · 语速 ' + pct(d.rate || 0);
      main.appendChild(name);
      main.appendChild(meta);

      var play = document.createElement('button');
      play.className = 'hist-btn';
      play.textContent = '收听';
      play.addEventListener('click', function () { playDoc(index); });

      var del = document.createElement('button');
      del.className = 'hist-btn del';
      del.textContent = '删除';
      del.addEventListener('click', function () {
        var l = loadDocs();
        var target = l[index];
        if (!confirm('删除《' + (target && target.name || '未命名') + '》？该资料的文本也会一并删除。')) return;
        l.splice(index, 1);
        saveDocs(l);
        renderDocs();
      });

      li.appendChild(main);
      li.appendChild(play);
      li.appendChild(del);
      box.appendChild(li);
    });
  }

  /** 载入资料并直接开始合成播放——健身场景下要的就是一步到位 */
  function playDoc(index) {
    var d = loadDocs()[index];
    if (!d) return;

    $('text').value = d.text || '';
    updateStats();

    if (d.voice) {
      for (var i = 0; i < state.voices.length; i++) {
        if (state.voices[i].id === d.voice) { $('voice').selectedIndex = i; break; }
      }
    }
    if (typeof d.rate === 'number') {
      $('rate').value = d.rate;
      $('rateLabel').textContent = pct(d.rate);
    }
    if (typeof d.volume === 'number') {
      $('volume').value = d.volume;
      $('volumeLabel').textContent = pct(d.volume);
    }

    notice('已载入《' + (d.name || '未命名资料') + '》，正在生成音频…');
    // 先置空再由下面 setAudio 用资料名命名下载文件
    state.docName = d.name || '';
    doGenerate();
  }

  function saveCurrentDoc() {
    var t = $('text').value;
    if (!t.trim()) { notice('请先输入或导入文本', 'warn'); return; }

    var suggest = t.trim().replace(/\s+/g, ' ').slice(0, 12);
    var name = prompt('给这份资料起个名字', suggest);
    if (name === null) return;

    var finalName = (name || '').trim() || suggest || '未命名资料';
    var list = loadDocs();
    list.unshift({
      name: finalName,
      text: t,
      chars: t.length,
      voice: currentVoice(),
      voiceName: currentVoiceName(),
      rate: Number($('rate').value),
      volume: Number($('volume').value),
      time: Date.now()
    });
    saveDocs(list);
    renderDocs();
    state.docName = finalName;
    notice('已保存资料《' + finalName + '》，下次点「收听」即可直接播放');
  }

  // ------------------------------------------------------------------ 后端地址

  function editServer() {
    var v = prompt('后端服务地址（留空表示与本页面同源）', apiBase());
    if (v === null) return;
    v = v.trim().replace(/\/+$/, '');
    localStorage.setItem(LS_API, v);
    loadHealth();
    loadVoices();
  }
})();
