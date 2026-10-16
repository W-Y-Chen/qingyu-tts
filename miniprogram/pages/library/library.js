// 轻语 TTS —— 音频库：播放 / 删除已保存到本地的音频

const app = getApp()

function fmtTime(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

function fmtDate(ts) {
  const d = new Date(ts)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

Page({
  data: {
    list: [],
    playingPath: '',
    serverBase: ''
  },

  onLoad() {
    this.setData({ serverBase: app.globalData.serverBase })
    this._audio = wx.createInnerAudioContext()
    this._audio.obeyMuteSwitch = false
    this._audio.onEnded(() => this.setData({ playingPath: '' }))
    this._audio.onStop(() => this.setData({ playingPath: '' }))
    this._audio.onError(() => {
      this.setData({ playingPath: '' })
      wx.showToast({ title: '播放失败，文件可能已被清理', icon: 'none' })
    })
  },

  onShow() {
    this.refresh()
  },

  onUnload() {
    if (this._audio) {
      this._audio.destroy()
      this._audio = null
    }
  },

  refresh() {
    const raw = wx.getStorageSync('qingyu_library') || []
    const list = raw.map(item => ({
      ...item,
      durationText: fmtTime(item.duration),
      timeText: fmtDate(item.createdAt)
    }))
    this.setData({ list })
  },

  onToggle(e) {
    const path = e.currentTarget.dataset.path
    if (this.data.playingPath === path) {
      this._audio.stop()
      this.setData({ playingPath: '' })
      return
    }
    this._audio.src = path
    this._audio.play()
    this.setData({ playingPath: path })
  },

  onDelete(e) {
    const self = this
    const path = e.currentTarget.dataset.path
    wx.showModal({
      title: '删除音频',
      content: '删除后本地文件将无法恢复，确定继续吗？',
      success(r) {
        if (!r.confirm) return
        if (self.data.playingPath === path) {
          self._audio.stop()
          self.setData({ playingPath: '' })
        }
        wx.getFileSystemManager().unlink({
          filePath: path,
          complete() {
            const rest = (wx.getStorageSync('qingyu_library') || []).filter(i => i.path !== path)
            wx.setStorageSync('qingyu_library', rest)
            self.refresh()
            wx.showToast({ title: '已删除', icon: 'success' })
          }
        })
      }
    })
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
        }
      }
    })
  }
})
