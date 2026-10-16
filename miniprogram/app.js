// 轻语 TTS —— 小程序入口
// 全局只维护三类状态：服务地址、合成参数、最近一次生成的音频。

const DEFAULT_SERVER = 'http://127.0.0.1:5000'

App({
  globalData: {
    serverBase: DEFAULT_SERVER,
    voice: 'zh-CN-XiaoxiaoNeural',
    rate: 0,      // -50 ~ +50
    volume: 0,    // -50 ~ +50
    lastAudio: null
  },

  onLaunch() {
    this._restore()
  },

  onHide() {
    this._persist()
  },

  _restore() {
    try {
      const cfg = wx.getStorageSync('qingyu_config')
      if (cfg && typeof cfg === 'object') {
        const g = this.globalData
        if (cfg.serverBase) g.serverBase = cfg.serverBase
        if (cfg.voice) g.voice = cfg.voice
        if (typeof cfg.rate === 'number') g.rate = cfg.rate
        if (typeof cfg.volume === 'number') g.volume = cfg.volume
      }
    } catch (e) {
      console.warn('[轻语] 读取本地配置失败', e)
    }
  },

  _persist() {
    const g = this.globalData
    wx.setStorageSync('qingyu_config', {
      serverBase: g.serverBase,
      voice: g.voice,
      rate: g.rate,
      volume: g.volume
    })
  },

  saveConfig(patch) {
    Object.assign(this.globalData, patch || {})
    this._persist()
  }
})
