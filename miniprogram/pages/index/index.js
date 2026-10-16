// 轻语 TTS —— 主页：文本输入 / 音色与参数 / 试听 / 完整生成 / 保存

const api = require('../../utils/api')
const app = getApp()

// 与服务端 core/text_processor.py 中的正则保持一致
const PAUSE_RE = /\[\s*(?:p|停顿)\s*[:：]?\s*(?:\d+(?:\.\d+)?\s*(?:ms|s)?)?\s*\]/gi
const MAX_TEXT_CHARS = 20000

function fmtTime(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

function pct(v) {
  const n = Number(v) || 0
  return n === 0 ? '正常' : (n > 0 ? `+${n}%` : `${n}%`)
}

Page({
  data: {
    text: '',
    charCount: 0,
    pauseCount: 0,
    canGenerate: false,

    voices: [],
    voiceLabels: [],
    voiceIndex: 0,
    voiceDesc: '',

    rate: 0,
    volume: 0,
    rateLabel: '正常',
    volumeLabel: '正常',

    previewing: false,
    generating: false,
    progress: 0,
    doneSegments: 0,
    totalSegments: 0,

    hasAudio: false,
    audioUrl: '',
    playing: false,
    currentTime: 0,
    currentText: '00:00',
    duration: 0,
    durationText: '00:00',
    seekMax: 1,

    serverBase: ''
  },

  onLoad() {
    const g = app.globalData
    this.setData({
      serverBase: g.serverBase,
      rate: g.rate,
      volume: g.volume,
      rateLabel: pct(g.rate),
      volumeLabel: pct(g.volume)
    })
    this._initAudio()
    this.loadVoices()
  },

  onUnload() {
    this._stopPoll()
    if (this._audio) {
      this._audio.destroy()
      this._audio = null
    }
  },

  // ------------------------------------------------------------ 音频上下文

  _initAudio() {
    const a = wx.createInnerAudioContext()
    a.obeyMuteSwitch = false
    a.onPlay(() => this.setData({ playing: true }))
    a.onPause(() => this.setData({ playing: false }))
    a.onStop(() => this.setData({ playing: false, currentTime: 0, currentText: '00:00' }))
    a.onEnded(() => this.setData({ playing: false, currentTime: 0, currentText: '00:00' }))
    a.onTimeUpdate(() => {
      if (this._seeking) return
      const t = a.currentTime || 0
      this.setData({ currentTime: t, currentText: fmtTime(t) })
    })
    a.onError((e) => {
      this.setData({ playing: false })
      wx.showToast({ title: '播放失败：' + ((e && e.errMsg) || '未知错误'), icon: 'none' })
    })
    this._audio = a
  },

  _play() {
    const url = this.data.audioUrl
    if (!url || !this._audio) return
    if (this._audio.src !== url) this._audio.src = url
    this._audio.play()
  },

  _setAudio(url, duration) {
    const d = Number(duration) || 0
    this.setData({
      audioUrl: url,
      hasAudio: true,
      duration: d,
      durationText: fmtTime(d),
      currentTime: 0,
      currentText: '00:00',
      seekMax: Math.max(1, Math.round(d))
    })
  },

  onTogglePlay() {
    if (!this._audio) return
    if (this.data.playing) this._audio.pause()
    else this._play()
  },

  onStopPlay() {
    if (this._audio) this._audio.stop()
  },

  onSeek(e) {
    if (!this._audio) return
    const v = Number(e.detail.value) || 0
    this._audio.seek(v)
    this.setData({ currentTime: v, currentText: fmtTime(v) })
  },

  // ------------------------------------------------------------ 音色

  async loadVoices() {
    try {
      const res = await api.voices()
      const voices = res.voices || []
      if (!voices.length) throw new Error('音色列表为空')
      const labels = voices.map(v => `${v.name} · ${v.gender} · ${v.style || '通用'}`)
      let idx = voices.findIndex(v => v.id === app.globalData.voice)
      if (idx < 0) idx = 0
      this.setData({
        voices,
        voiceLabels: labels,
        voiceIndex: idx,
        voiceDesc: voices[idx].desc || `${voices[idx].name}（${voices[idx].gender}声，${voices[idx].style || '通用'}）`
      })
      app.saveConfig({ voice: voices[idx].id })
    } catch (e) {
      this.setData({ voices: [], voiceLabels: [], voiceDesc: '' })
      wx.showToast({ title: '未连接到服务，请检查服务地址', icon: 'none' })
    }
  },

  _voice() {
    const v = this.data.voices[this.data.voiceIndex]
    return v ? v.id : 'zh-CN-XiaoxiaoNeural'
  },

  _voiceName() {
    const v = this.data.voices[this.data.voiceIndex]
    return v ? v.name : '默认'
  },

  onVoiceChange(e) {
    const idx = Number(e.detail.value) || 0
    const v = this.data.voices[idx]
    if (!v) return
    this.setData({
      voiceIndex: idx,
      voiceDesc: v.desc || `${v.name}（${v.gender}声，${v.style || '通用'}）`
    })
    app.saveConfig({ voice: v.id })
  },

  onRateChange(e) {
    const v = Number(e.detail.value) || 0
    this.setData({ rate: v, rateLabel: pct(v) })
    app.saveConfig({ rate: v })
  },

  onVolumeChange(e) {
    const v = Number(e.detail.value) || 0
    this.setData({ volume: v, volumeLabel: pct(v) })
    app.saveConfig({ volume: v })
  },

  // ------------------------------------------------------------ 文本

  _applyText(text) {
    const t = String(text || '')
    const pauses = t.match(PAUSE_RE)
    this.setData({
      text: t,
      charCount: t.length,
      pauseCount: pauses ? pauses.length : 0,
      canGenerate: t.trim().length > 0
    })
  },

  onTextInput(e) {
    this._applyText(e.detail.value)
  },

  onClearText() {
    const self = this
    if (!this.data.text) return
    wx.showModal({
      title: '清空文本',
      content: '确定要清空当前输入的全部文字吗？',
      success(r) {
        if (r.confirm) self._applyText('')
      }
    })
  },

  onImportTxt() {
    const self = this
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['txt'],
      success(res) {
        const f = res.tempFiles && res.tempFiles[0]
        if (!f) return
        if (f.size && f.size > 2 * 1024 * 1024) {
          wx.showToast({ title: '文件超过 2MB，请拆分后导入', icon: 'none' })
          return
        }
        wx.showLoading({ title: '读取中' })
        wx.getFileSystemManager().readFile({
          filePath: f.path,
          encoding: 'utf-8',
          success(r) {
            wx.hideLoading()
            let content = String(r.data || '')
            if (content.length > MAX_TEXT_CHARS) {
              content = content.slice(0, MAX_TEXT_CHARS)
              wx.showToast({ title: '已截断至 20000 字', icon: 'none' })
            }
            if (content.indexOf('\uFFFD') >= 0) {
              wx.showModal({
                title: '编码提示',
                content: '检测到非 UTF-8 字符，部分文字可能乱码。建议把 txt 另存为 UTF-8 编码后重新导入。',
                showCancel: false
              })
            }
            const merged = self.data.text ? `${self.data.text}\n\n${content}` : content
            self._applyText(merged)
            wx.showToast({ title: `已读取 ${content.length} 字`, icon: 'none' })
          },
          fail() {
            wx.hideLoading()
            wx.showToast({ title: '读取失败，请确认是 UTF-8 编码的 txt', icon: 'none' })
          }
        })
      }
    })
  },

  onInsertPause(e) {
    const ms = Number(e.currentTarget.dataset.ms) || 300
    const tag = ms >= 1000 ? `[p${ms / 1000}s]` : `[p${ms}]`
    this._applyText(this.data.text + tag)
  },

  onPauseHelp() {
    wx.showModal({
      title: '停顿标记语法',
      content: '在正文中插入方括号标记即可控制停顿：\n\n'
        + '[p] —— 默认停顿 400 毫秒\n'
        + '[p300] 或 [p300ms] —— 停顿 300 毫秒\n'
        + '[p1.5s] —— 停顿 1.5 秒\n'
        + '[停顿] / [停顿800] —— 中文写法\n\n'
        + '段落之间的空行会自动插入 500 毫秒停顿。\n'
        + '受限于输入组件，插入按钮会把标记追加到文末，可手动剪切到目标位置。',
      showCancel: false
    })
  },

  // ------------------------------------------------------------ 合成

  async onPreview() {
    if (this.data.previewing || this.data.generating) return
    if (!this.data.text.trim()) {
      wx.showToast({ title: '请先输入文本', icon: 'none' })
      return
    }
    this.setData({ previewing: true })
    wx.showLoading({ title: '试听合成中' })
    try {
      const res = await api.preview({
        text: this.data.text,
        voice: this._voice(),
        rate: this.data.rate,
        volume: this.data.volume
      })
      this._setAudio(api.audioUrl(res.audio_url), res.duration)
      wx.hideLoading()
      if (res.clipped) wx.showToast({ title: '试听仅含前 200 字', icon: 'none' })
      this._play()
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: (e && e.message) || '试听失败', icon: 'none' })
    } finally {
      this.setData({ previewing: false })
    }
  },

  async onGenerate() {
    if (this.data.previewing || this.data.generating) return
    if (!this.data.text.trim()) {
      wx.showToast({ title: '请先输入文本', icon: 'none' })
      return
    }
    if (this.data.text.length > MAX_TEXT_CHARS) {
      wx.showToast({ title: `文本上限 ${MAX_TEXT_CHARS} 字`, icon: 'none' })
      return
    }
    this.setData({ generating: true, progress: 0, doneSegments: 0, totalSegments: 0 })
    try {
      const res = await api.synthesize({
        text: this.data.text,
        voice: this._voice(),
        rate: this.data.rate,
        volume: this.data.volume
      })
      this._startPoll(res.task_id)
    } catch (e) {
      this.setData({ generating: false })
      wx.showToast({ title: (e && e.message) || '提交失败', icon: 'none' })
    }
  },

  _startPoll(taskId) {
    this._stopPoll()
    this._pollTimer = setInterval(() => {
      api.task(taskId).then(t => {
        this.setData({
          progress: t.progress || 0,
          doneSegments: t.done_segments || 0,
          totalSegments: t.total_segments || 0
        })
        if (t.status === 'done') {
          this._stopPoll()
          this.setData({ generating: false })
          this._setAudio(api.audioUrl(t.audio_url), t.duration)
          wx.showToast({ title: '生成完成', icon: 'success' })
          this._play()
        } else if (t.status === 'error') {
          this._stopPoll()
          this.setData({ generating: false })
          wx.showModal({ title: '合成失败', content: t.error || '未知错误', showCancel: false })
        }
      }).catch(() => { /* 单次轮询失败不中断，等待下一轮 */ })
    }, 1000)
  },

  _stopPoll() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer)
      this._pollTimer = null
    }
  },

  // ------------------------------------------------------------ 保存

  onSave() {
    const self = this
    const url = this.data.audioUrl
    if (!url) return
    wx.showLoading({ title: '保存中' })
    wx.downloadFile({
      url,
      success(res) {
        if (res.statusCode !== 200) {
          wx.hideLoading()
          wx.showToast({ title: '下载失败', icon: 'none' })
          return
        }
        wx.getFileSystemManager().saveFile({
          tempFilePath: res.tempFilePath,
          success(r) {
            const list = wx.getStorageSync('qingyu_library') || []
            list.unshift({
              path: r.savedFilePath,
              name: (self.data.text || '轻语音频').replace(/\s+/g, ' ').slice(0, 20) || '未命名',
              voice: self._voiceName(),
              duration: self.data.duration,
              chars: self.data.charCount,
              createdAt: Date.now()
            })
            wx.setStorageSync('qingyu_library', list.slice(0, 50))
            wx.hideLoading()
            wx.showToast({ title: '已保存到音频库', icon: 'success' })
          },
          fail(e) {
            wx.hideLoading()
            wx.showToast({ title: '保存失败：' + ((e && e.errMsg) || ''), icon: 'none' })
          }
        })
      },
      fail() {
        wx.hideLoading()
        wx.showToast({ title: '下载失败，请检查网络', icon: 'none' })
      }
    })
  },

  onCopyLink() {
    if (!this.data.audioUrl) return
    wx.setClipboardData({
      data: this.data.audioUrl,
      success() {
        wx.showToast({ title: '链接已复制', icon: 'success' })
      }
    })
  },

  // ------------------------------------------------------------ 其它

  gotoLibrary() {
    wx.navigateTo({ url: '/pages/library/library' })
  },

  onEditServer() {
    const self = this
    wx.showModal({
      title: '服务地址',
      editable: true,
      placeholderText: 'http://192.168.1.10:5000',
      content: this.data.serverBase,
      success(r) {
        if (r.confirm && r.content) {
          const v = String(r.content).trim().replace(/\/+$/, '')
          app.saveConfig({ serverBase: v })
          self.setData({ serverBase: v })
          self.loadVoices()
        }
      }
    })
  }
})
