// 轻语 TTS —— 服务端接口封装
// 所有请求统一在此收敛错误，页面层只处理"成功"与"可读的失败原因"。

const app = getApp()

function base() {
  return String((app && app.globalData && app.globalData.serverBase) || '').replace(/\/+$/, '')
}

function request({ method = 'GET', path, data, timeout = 30000 }) {
  return new Promise((resolve, reject) => {
    const url = base() + path
    if (!base()) {
      reject(new Error('尚未配置服务地址'))
      return
    }
    wx.request({
      url,
      method,
      data: data || {},
      header: { 'content-type': 'application/json' },
      timeout,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else {
          const msg = (res.data && res.data.error) || ('服务返回状态码 ' + res.statusCode)
          reject(new Error(msg))
        }
      },
      fail(err) {
        reject(new Error((err && err.errMsg) || '网络请求失败'))
      }
    })
  })
}

module.exports = {
  base,
  /** 把相对音频路径拼成可播放的完整地址 */
  audioUrl(relative) {
    if (!relative) return ''
    if (/^https?:\/\//.test(relative)) return relative
    return base() + relative
  },
  health: () => request({ path: '/api/health' }),
  voices: () => request({ path: '/api/voices' }),
  preview: (payload) => request({ method: 'POST', path: '/api/preview', data: payload, timeout: 90000 }),
  synthesize: (payload) => request({ method: 'POST', path: '/api/synthesize', data: payload }),
  task: (taskId) => request({ path: '/api/tasks/' + taskId, timeout: 15000 })
}
