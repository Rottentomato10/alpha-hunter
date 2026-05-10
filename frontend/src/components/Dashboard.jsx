import { useState, useEffect } from 'react'
import axios from 'axios'

export default function Dashboard({ api }) {
  const [data, setData] = useState(null)
  const [logs, setLogs] = useState([])
  const [aiData, setAiData] = useState({})
  const [sizing, setSizing] = useState(null)
  const [options, setOptions] = useState(null)
  const [expandedAi, setExpandedAi] = useState(null)
  const [mode, setMode] = useState('stocks') // 'stocks' or 'options'

  useEffect(() => {
    axios.get(`${api}/dashboard`).then(r => setData(r.data)).catch(() => {})
    axios.get(`${api}/daily-logs`).then(r => setLogs(r.data.slice(0, 5))).catch(() => {})
    axios.get(`${api}/ai-analysis-all`).then(r => setAiData(r.data)).catch(() => {})
    axios.get(`${api}/position-sizing`).then(r => setSizing(r.data)).catch(() => {})
    axios.get(`${api}/options-analysis`).then(r => { if (!r.data.error) setOptions(r.data) }).catch(() => {})
  }, [])

  if (!data) return <div className="text-gray-500 text-center py-12">טוען...</div>

  const statusColor = (s) => {
    if (s === 'above_ma200') return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20'
    if (s === 'approaching') return 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20'
    return 'text-gray-400 bg-gray-400/10 border-gray-400/20'
  }

  const statusLabel = (item) => {
    const s = item.live_status || item.status
    if (s === 'above_ma200') return 'חצה MA200! בדוק ווליום'
    if (s === 'approaching') return 'מתקרב לחצייה'
    if (s === 'SIGNAL') return '🔥 סיגנל פעיל!'
    return 'ממתין לחציית MA200'
  }

  const progressPct = (item) => {
    const dist = item.dist_ma200_pct || 0
    if (dist >= 0) return 100
    return Math.max(0, Math.min(100, 100 + dist * 5))
  }

  return (
    <div>
      {/* Mode Toggle */}
      <div className="flex items-center gap-2 mb-6">
        <button
          onClick={() => setMode('stocks')}
          className={`px-5 py-2.5 text-sm font-bold rounded-lg transition-all ${
            mode === 'stocks'
              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
              : 'text-gray-500 hover:text-gray-300 border border-[#1e1e2e]'
          }`}
        >
          📈 מניות
        </button>
        <button
          onClick={() => setMode('options')}
          className={`px-5 py-2.5 text-sm font-bold rounded-lg transition-all ${
            mode === 'options'
              ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30'
              : 'text-gray-500 hover:text-gray-300 border border-[#1e1e2e]'
          }`}
        >
          📊 אופציות (LEAPS)
        </button>
      </div>

      {/* ============================================================ */}
      {/* STOCKS MODE */}
      {/* ============================================================ */}
      {mode === 'stocks' && (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Watchlist Cards */}
          <div className="lg:col-span-3 space-y-4">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <span>📡</span> ווטצ'ליסט — גישת מניות
            </h2>

            {data.watchlist.map(item => (
              <div key={item.ticker} className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e] hover:border-emerald-500/30 transition-colors">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <div className="flex items-center gap-3">
                      <span className="text-2xl font-black text-white" dir="ltr">{item.ticker}</span>
                      <span className={`text-[11px] font-bold px-2.5 py-0.5 rounded-full border ${statusColor(item.live_status || item.status)}`}>
                        {statusLabel(item)}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{item.reason}</div>
                  </div>
                  <div className="text-left" dir="ltr">
                    <div className="text-lg font-bold text-white">${item.current_price || '—'}</div>
                    <div className="text-xs text-gray-500">MA200: ${item.ma200 || '—'}</div>
                  </div>
                </div>

                {/* Progress bar */}
                <div className="mb-3">
                  <div className="flex justify-between text-[10px] text-gray-500 mb-1">
                    <span>רחוק מ-MA200</span><span>חצה MA200</span>
                  </div>
                  <div className="w-full h-2 bg-[#1e1e2e] rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${progressPct(item) >= 100 ? 'bg-emerald-400' : progressPct(item) > 70 ? 'bg-yellow-400' : 'bg-gray-600'}`}
                      style={{ width: `${progressPct(item)}%` }} />
                  </div>
                  <div className="text-[10px] text-gray-500 mt-1 text-left" dir="ltr">
                    {item.dist_ma200_pct != null ? `${item.dist_ma200_pct > 0 ? '+' : ''}${item.dist_ma200_pct}% מ-MA200` : ''}
                  </div>
                </div>

                {/* Badges */}
                <div className="flex gap-2 flex-wrap">
                  <span className="text-[11px] bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 px-2 py-0.5 rounded-full">צמיחה +{item.rev_growth}%</span>
                  <span className="text-[11px] bg-red-400/10 text-red-400 border border-red-400/20 px-2 py-0.5 rounded-full">שורט {item.short_interest}%</span>
                  {item.eps_improving != null && (
                    <span className={`text-[11px] px-2 py-0.5 rounded-full border ${item.eps_improving ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20' : 'bg-yellow-400/10 text-yellow-400 border-yellow-400/20'}`}>
                      {item.eps_improving ? 'EPS מאיץ ✅' : 'EPS לא מאיץ ⚠️'}
                    </span>
                  )}
                </div>

                {/* AI */}
                {aiData[item.ticker] && (
                  <div className="mt-3 border-t border-[#1e1e2e] pt-3">
                    <button onClick={() => setExpandedAi(expandedAi === item.ticker ? null : item.ticker)}
                      className="text-[11px] text-purple-400 hover:text-purple-300 flex items-center gap-1">
                      <span>🤖</span> ניתוח AI — {aiData[item.ticker].date}
                      <span className={`mr-2 px-1.5 py-0.5 rounded text-[10px] font-bold ${aiData[item.ticker].sentiment?.includes('חיובי') ? 'bg-emerald-400/10 text-emerald-400' : 'bg-yellow-400/10 text-yellow-400'}`}>
                        {aiData[item.ticker].sentiment}
                      </span>
                      <span className="text-gray-600">{expandedAi === item.ticker ? '▲' : '▼'}</span>
                    </button>
                    {expandedAi === item.ticker && (
                      <div className="mt-2 bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e] text-xs text-gray-300 leading-relaxed whitespace-pre-line">
                        {aiData[item.ticker].analysis}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* Position Sizing */}
            {sizing && sizing.positions && (
              <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e]">
                <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2"><span>💰</span> הקצאת תיק — מניות</h3>
                <div className="space-y-3 mb-4">
                  {sizing.positions.map(p => (
                    <div key={p.ticker} className="flex items-center gap-3">
                      <span className="text-sm font-bold text-white w-12" dir="ltr">{p.ticker}</span>
                      <div className="flex-1"><div className="w-full h-3 bg-[#1e1e2e] rounded-full overflow-hidden"><div className="h-full rounded-full bg-gradient-to-l from-emerald-500 to-emerald-700" style={{ width: `${p.allocation_pct / 25 * 100}%` }} /></div></div>
                      <span className="text-sm font-bold text-emerald-400 w-12 text-left font-mono" dir="ltr">{p.allocation_pct}%</span>
                    </div>
                  ))}
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-bold text-gray-400 w-12">מזומן</span>
                    <div className="flex-1"><div className="w-full h-3 bg-[#1e1e2e] rounded-full overflow-hidden"><div className="h-full rounded-full bg-gray-600" style={{ width: `${sizing.cash_pct}%` }} /></div></div>
                    <span className="text-sm font-bold text-gray-400 w-12 text-left font-mono" dir="ltr">{sizing.cash_pct}%</span>
                  </div>
                </div>
                <div className="border-t border-[#1e1e2e] pt-3 space-y-1 text-xs text-gray-400">
                  <div>🎯 יעד: <strong className="text-emerald-400">+500%</strong> | ⏱️ 8-12 חודשים | ⚠️ stop loss: <strong className="text-red-400">-35%</strong></div>
                </div>
              </div>
            )}
          </div>

          {/* Stats */}
          <div className="lg:col-span-2 space-y-4">
            <h2 className="text-lg font-bold text-white flex items-center gap-2"><span>📊</span> סטטיסטיקות</h2>
            <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e] space-y-4">
              {[
                { label: 'סיגנלים השנה', value: data.total_signals_ytd, color: 'text-purple-400' },
                { label: 'דיוק היסטורי', value: '16%', color: 'text-emerald-400' },
                { label: 'תשואה אם צדקנו', value: '+428%', color: 'text-emerald-400' },
                { label: 'תשואה אם טעינו', value: '+49%', color: 'text-yellow-400' },
                { label: 'ריצות שנותחו', value: data.total_runners, color: 'text-white' },
                { label: 'סיגנל הבא', value: 'כל ~6 שבועות', color: 'text-gray-400' },
              ].map((s, i) => (
                <div key={i} className="flex items-center justify-between">
                  <span className="text-sm text-gray-400">{s.label}</span>
                  <span className={`text-sm font-bold font-mono ${s.color}`}>{s.value}</span>
                </div>
              ))}
            </div>
            <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e]">
              <h3 className="text-sm font-bold text-white mb-3">פעילות אחרונה</h3>
              <div className="space-y-2">
                {logs.map((l, i) => (
                  <div key={i} className="flex items-center justify-between text-xs bg-[#0a0a0f] rounded-lg p-2.5 border border-[#1e1e2e]">
                    <span className="text-gray-400 font-mono" dir="ltr">{l.date}</span>
                    <span className="text-gray-500">{l.signals_count > 0 ? <span className="text-purple-400 font-bold">סיגנל!</span> : l.status_changes > 0 ? `${l.status_changes} שינויים` : 'ללא שינוי'}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* OPTIONS MODE */}
      {/* ============================================================ */}
      {mode === 'options' && (
        <div className="space-y-6">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <span>📊</span> ווטצ'ליסט — גישת אופציות (LEAPS)
          </h2>

          {!options ? (
            <div className="text-gray-500 text-center py-12">אין נתוני אופציות. הרץ <code className="bg-[#1e1e2e] px-1.5 py-0.5 rounded text-purple-300">python3 options_analyzer.py</code></div>
          ) : (
            <>
              {/* Scores Grid */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {(options.scores || []).map(s => (
                  <div key={s.ticker} className={`bg-[#12121a] rounded-xl p-5 border transition-colors ${s.ticker === options.winner ? 'border-purple-500/50 bg-purple-500/[0.03]' : 'border-[#1e1e2e]'}`}>
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <span className="text-xl font-black text-white" dir="ltr">{s.ticker}</span>
                        {s.ticker === options.winner && <span className="text-[10px] bg-purple-400/20 text-purple-400 px-2 py-0.5 rounded-full font-bold">🏆 מומלץ</span>}
                      </div>
                      <div className="text-2xl font-black text-purple-400">{s.total_score}</div>
                    </div>

                    {/* Score bars */}
                    <div className="space-y-2">
                      {[
                        { label: 'IV (זול/יקר)', score: s.iv_score, max: 30, reason: s.iv_reason },
                        { label: 'קטליסט', score: s.catalyst_score, max: 25, reason: s.catalyst_reason },
                        { label: 'תזמון', score: s.timing_score, max: 25, reason: s.timing_reason },
                        { label: 'שורט', score: s.short_score, max: 20, reason: s.short_reason },
                      ].map((dim, i) => (
                        <div key={i}>
                          <div className="flex justify-between text-[10px] mb-0.5">
                            <span className="text-gray-500">{dim.label}</span>
                            <span className="text-gray-400">{dim.score}/{dim.max}</span>
                          </div>
                          <div className="w-full h-1.5 bg-[#1e1e2e] rounded-full overflow-hidden">
                            <div className="h-full rounded-full bg-purple-500" style={{ width: `${dim.score / dim.max * 100}%` }} />
                          </div>
                          <div className="text-[9px] text-gray-600 mt-0.5">{dim.reason}</div>
                        </div>
                      ))}
                    </div>

                    {/* Quick stats */}
                    <div className="mt-3 pt-3 border-t border-[#1e1e2e] grid grid-cols-2 gap-2 text-[10px]">
                      <div><span className="text-gray-500">IV: </span><span className="text-white">{s.current_iv_pct}%</span></div>
                      <div><span className="text-gray-500">שורט: </span><span className="text-white">{s.short_pct}%</span></div>
                      <div><span className="text-gray-500">צמיחה: </span><span className="text-emerald-400">+{s.rev_growth_pct}%</span></div>
                      <div><span className="text-gray-500">MA200: </span><span className="text-white">{s.dist_ma200_pct > 0 ? '+' : ''}{s.dist_ma200_pct}%</span></div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Recommendation */}
              {options.recommendation && !options.recommendation.error && (() => {
                const rec = options.recommendation
                const atm = rec.atm
                const otm = rec.otm
                const scenarios = rec.scenarios || []
                const portfolio = rec.portfolio_options || {}
                return (
                  <div className="space-y-4">
                    {/* Main recommendation */}
                    <div className="bg-purple-500/5 rounded-xl p-5 border border-purple-500/20">
                      <div className="flex items-center gap-3 mb-3">
                        <span className="text-xl">🎯</span>
                        <div>
                          <div className="text-sm font-bold text-purple-400">המלצה: {rec.ticker} LEAPS — תפוגה {rec.expiration} ({rec.days_to_expiry} ימים)</div>
                          <div className="text-xs text-gray-400 mt-1">{rec.why_options}</div>
                        </div>
                      </div>
                      <div className="text-xs text-gray-300 bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e]">{rec.why_this_stock}</div>
                    </div>

                    {/* ATM vs OTM comparison */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="bg-[#12121a] rounded-xl p-5 border border-emerald-500/20">
                        <div className="text-xs text-emerald-400 font-bold mb-3">ATM Call (שמרני יותר)</div>
                        <div className="space-y-2 text-sm">
                          <div className="flex justify-between"><span className="text-gray-500">סטרייק</span><span className="text-white font-mono" dir="ltr">${atm.strike}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">פרמיה/חוזה</span><span className="text-white font-mono" dir="ltr">${atm.cost_per_contract}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">Break-even</span><span className="text-white font-mono" dir="ltr">${atm.breakeven}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">Delta</span><span className="text-white font-mono" dir="ltr">{atm.delta}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">IV</span><span className="text-white font-mono" dir="ltr">{(atm.iv * 100).toFixed(0)}%</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">Open Interest</span><span className="text-white font-mono" dir="ltr">{atm.open_interest}</span></div>
                        </div>
                      </div>
                      <div className="bg-[#12121a] rounded-xl p-5 border border-yellow-500/20">
                        <div className="text-xs text-yellow-400 font-bold mb-3">OTM Call (אגרסיבי יותר)</div>
                        <div className="space-y-2 text-sm">
                          <div className="flex justify-between"><span className="text-gray-500">סטרייק</span><span className="text-white font-mono" dir="ltr">${otm.strike}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">פרמיה/חוזה</span><span className="text-white font-mono" dir="ltr">${otm.cost_per_contract}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">Break-even</span><span className="text-white font-mono" dir="ltr">${otm.breakeven}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">Delta</span><span className="text-white font-mono" dir="ltr">{otm.delta}</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">IV</span><span className="text-white font-mono" dir="ltr">{(otm.iv * 100).toFixed(0)}%</span></div>
                          <div className="flex justify-between"><span className="text-gray-500">Open Interest</span><span className="text-white font-mono" dir="ltr">{otm.open_interest}</span></div>
                        </div>
                      </div>
                    </div>

                    {/* Scenarios */}
                    <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e]">
                      <h3 className="text-sm font-bold text-white mb-3">תרחישים — ATM Call ${atm.strike}</h3>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead className="text-gray-500">
                            <tr>
                              <th className="text-right pb-2">מהלך המניה</th>
                              <th className="text-left pb-2">מחיר עתידי</th>
                              <th className="text-left pb-2">שווי ATM</th>
                              <th className="text-left pb-2">תשואת ATM</th>
                              <th className="text-left pb-2">שווי OTM</th>
                              <th className="text-left pb-2">תשואת OTM</th>
                            </tr>
                          </thead>
                          <tbody>
                            {scenarios.map((s, i) => (
                              <tr key={i} className="border-t border-[#1e1e2e]">
                                <td className={`py-2 font-bold ${s.stock_move_pct > 0 ? 'text-emerald-400' : s.stock_move_pct < 0 ? 'text-red-400' : 'text-gray-400'}`}>
                                  {s.stock_move_pct >= 0 ? '+' : ''}{s.stock_move_pct}%
                                </td>
                                <td className="py-2 text-gray-300 font-mono" dir="ltr">${s.future_price}</td>
                                <td className="py-2 text-gray-300 font-mono" dir="ltr">${s.atm_value}</td>
                                <td className={`py-2 font-bold font-mono ${s.atm_return_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`} dir="ltr">
                                  {s.atm_return_pct >= 0 ? '+' : ''}{s.atm_return_pct}%
                                </td>
                                <td className="py-2 text-gray-300 font-mono" dir="ltr">${s.otm_value}</td>
                                <td className={`py-2 font-bold font-mono ${s.otm_return_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`} dir="ltr">
                                  {s.otm_return_pct >= 0 ? '+' : ''}{s.otm_return_pct}%
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* Portfolio Options */}
                    <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e]">
                      <h3 className="text-sm font-bold text-white mb-4">חלוקת תיק — $10,000</h3>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {['option_a', 'option_b', 'option_c'].map(key => {
                          const opt = portfolio[key]
                          if (!opt) return null
                          const isAggressive = key === 'option_c'
                          const isMid = key === 'option_b'
                          return (
                            <div key={key} className={`rounded-xl p-4 border ${isAggressive ? 'border-red-500/20 bg-red-500/[0.03]' : isMid ? 'border-yellow-500/20 bg-yellow-500/[0.03]' : 'border-emerald-500/20 bg-emerald-500/[0.03]'}`}>
                              <div className={`text-xs font-bold mb-2 ${isAggressive ? 'text-red-400' : isMid ? 'text-yellow-400' : 'text-emerald-400'}`}>
                                {key === 'option_a' ? '🟢 שמרני' : key === 'option_b' ? '🟡 אגרסיבי' : '🔴 LEAPS בלבד'}
                              </div>
                              <div className="text-xs text-gray-400 mb-3">{opt.stock_pct}% מניה + {opt.leaps_pct}% LEAPS</div>
                              <div className="space-y-1.5 text-xs">
                                <div className="flex justify-between">
                                  <span className="text-gray-500">מניה:</span>
                                  <span className="text-white font-mono" dir="ltr">${opt.stock_amount.toLocaleString()}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-gray-500">LEAPS:</span>
                                  <span className="text-white font-mono" dir="ltr">${opt.leaps_amount.toLocaleString()} ({opt.contracts} חוזים)</span>
                                </div>
                                <div className="border-t border-[#1e1e2e] pt-1.5 mt-1.5">
                                  <div className="flex justify-between">
                                    <span className="text-gray-500">הפסד מקס:</span>
                                    <span className="text-red-400 font-bold" dir="ltr">-{opt.max_loss_pct}%</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-gray-500">Best case:</span>
                                    <span className="text-emerald-400 font-bold" dir="ltr">+{opt.best_case_pct?.toLocaleString()}%</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>

                      {/* Exit strategy */}
                      <div className="mt-4 pt-3 border-t border-[#1e1e2e] space-y-1 text-xs text-gray-400">
                        <div>🎯 יעד: <strong className="text-emerald-400">+500%</strong> (המניה) = <strong className="text-purple-400">+1,746%</strong> (ATM LEAPS)</div>
                        <div>⏱️ תפוגה: {rec.days_to_expiry} ימים | ⚠️ אם המניה לא זזה: <strong className="text-red-400">-87% על LEAPS</strong></div>
                        <div>📐 LEAPS = מינוף ללא margin call. הפסד מוגבל לפרמיה ששילמת.</div>
                      </div>
                    </div>
                  </div>
                )
              })()}
            </>
          )}
        </div>
      )}
    </div>
  )
}
