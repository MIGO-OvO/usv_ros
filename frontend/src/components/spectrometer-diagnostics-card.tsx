import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppStore } from '@/store'

const STATES: Record<string, string> = {
  disconnected: '已断开', unconfigured: '未配置', settling: '硬件稳定等待',
  mapping: '正在配置 I2CMAP', mapped: 'I2CMAP 已验证', configuring: '正在配置 ADSCFG',
  ready: '配置已验证', failed: '配置失败', idle: '未采集', starting: '等待首个有效帧',
  ads_configured: 'ADS 已配置 / I2CMAP 未验证',
  acquiring: '正在采集', stopped: '已停止', stale: '数据过期', error: '采集错误',
  i2c_error: 'I2C 错误', not_configured: '设备未配置', saturated: '信号饱和',
  watchdog_tripped: '看门狗已锁存', disabled: '已禁用',
}
const PHASES: Record<string, string> = {
  idle: '空闲', settling: '硬件稳定等待', serial_failed: '串口失败',
  i2c_map: 'I2CMAP', i2c_mapped: 'I2CMAP 已验证', i2c_map_failed: 'I2CMAP 失败',
  ads_config: 'ADSCFG', configured: 'ADSCFG 已验证', ads_config_failed: 'ADSCFG 失败',
  start: 'ADSSTART', start_failed: 'ADSSTART 失败', wait_frame: '等待首个有效帧',
  frame_timeout: '首帧超时', no_valid_frame: '首帧失败', retry_sync: '重试前同步', running: '启动成功',
}

export function SpectrometerDiagnosticsCard() {
  const {
    connected, statusSnapshotReceived, serialConnected, spectrometerConfigState, automationSpectroState,
    spectrometerTxnPhase, spectrometerLastTxnError, spectrometerTxnAttempt,
    spectrometerRetryErrors, automationSpectroAgeS, automationOwnerAgeS, automationControllerFault,
  } = useAppStore()
  const diagnosticsLive = connected && statusSnapshotReceived
  const unknownState = connected ? '未知（等待状态更新）' : '未知（Web 离线）'
  const stateText = (state: string | null) => diagnosticsLive ? (STATES[state || ''] || state || '未知') : unknownState
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle>分光与串口状态</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
          <div><dt className="text-xs text-muted-foreground">串口连接</dt><dd>{!diagnosticsLive || serialConnected === null ? '未知' : serialConnected ? '已连接' : '已断开'}</dd></div>
          <div><dt className="text-xs text-muted-foreground">I2C / ADS 配置</dt><dd>{stateText(spectrometerConfigState)}</dd></div>
          <div><dt className="text-xs text-muted-foreground">分光采集</dt><dd>{stateText(automationSpectroState)}</dd></div>
          <div><dt className="text-xs text-muted-foreground">最近事务阶段</dt><dd>{diagnosticsLive ? (PHASES[spectrometerTxnPhase || ''] || spectrometerTxnPhase || '未知') : unknownState}{diagnosticsLive && spectrometerTxnAttempt > 0 && ` · 尝试 ${spectrometerTxnAttempt}/3`}</dd></div>
          <div><dt className="text-xs text-muted-foreground">最后有效分光数据</dt><dd className="tabular-nums">{diagnosticsLive && typeof automationSpectroAgeS === 'number' ? `${automationSpectroAgeS.toFixed(1)} 秒前` : '未知'}</dd></div>
          <div><dt className="text-xs text-muted-foreground">Owner 心跳</dt><dd className="tabular-nums">{diagnosticsLive && typeof automationOwnerAgeS === 'number' ? `${automationOwnerAgeS.toFixed(1)} 秒前` : '未知'}</dd></div>
        </dl>
        <p className="break-all text-xs" role={diagnosticsLive && spectrometerLastTxnError ? 'alert' : undefined}>{diagnosticsLive ? '最近事务错误' : '最后已知事务错误'}：{spectrometerLastTxnError || (diagnosticsLive ? '无' : '未知')}</p>
        {automationControllerFault && <p className="break-all text-xs text-amber-600 dark:text-amber-400">{diagnosticsLive ? '控制器故障' : '最后已知控制器故障'}：{automationControllerFault}</p>}
        {spectrometerRetryErrors.length > 0 && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">{diagnosticsLive ? '本次事务重试记录' : '最后已知事务重试记录'}（{spectrometerRetryErrors.length}）</summary>
            <ul className="mt-2 space-y-1">{spectrometerRetryErrors.map((retry) => <li className="break-all" key={retry.attempt}>尝试 {retry.attempt} · {PHASES[retry.phase] || retry.phase}：{retry.error}</li>)}</ul>
          </details>
        )}
      </CardContent>
    </Card>
  )
}
