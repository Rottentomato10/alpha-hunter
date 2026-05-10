import { useState, useEffect } from 'react'
import axios from 'axios'

export default function Dashboard({ api }) {
  const [data, setData] = useState(null)
  const [logs, setLogs] = useState([])
  const [aiData, setAiData] = useState({})
  const [sizing, setSizing] = useState(null)
  const [options, setOptions] = useState(null)
  const [expandedAi, setExpandedAi] = useState(null)

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
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
      {/* Watchlist — right 60% */}
      <div className="lg:col-span-3 space-y-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <span>📡</span> ווטצ'ליסט פעיל
        </h2>

        {data.watchlist.map(item => (
          <div key={item.ticker} className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e] hover:border-purple-500/30 transition-colors">
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
                <span>רחוק מ-MA200</span>
                <span>חצה MA200</span>
              </div>
              <div className="w-full h-2 bg-[#1e1e2e] rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    progressPct(item) >= 100 ? 'bg-emerald-400' : progressPct(item) > 70 ? 'bg-yellow-400' : 'bg-gray-600'
                  }`}
                  style={{ width: `${progressPct(item)}%` }}
                />
              </div>
              <div className="text-[10px] text-gray-500 mt-1 text-left" dir="ltr">
                {item.dist_ma200_pct != null ? `${item.dist_ma200_pct > 0 ? '+' : ''}${item.dist_ma200_pct}% מ-MA200` : ''}
              </div>
            </div>

            {/* Badges */}
            <div className="flex gap-2 flex-wrap">
              <span className="text-[11px] bg-emerald-400/10 text-emerald-400 border border-emerald-400/20 px-2 py-0.5 rounded-full">
                צמיחה +{item.rev_growth}%
              </span>
              <span className="text-[11px] bg-red-400/10 text-red-400 border border-red-400/20 px-2 py-0.5 rounded-full">
                שורט {item.short_interest}%
              </span>
              {item.eps_improving != null && (
                <span className={`text-[11px] px-2 py-0.5 rounded-full border ${
                  item.eps_improving
                    ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20'
                    : 'bg-yellow-400/10 text-yellow-400 border-yellow-400/20'
                }`}>
                  {item.eps_improving ? 'EPS מאיץ ✅' : 'EPS לא מאיץ ⚠️'}
                </span>
              )}
              <span className="text-[11px] bg-gray-400/10 text-gray-400 border border-gray-400/20 px-2 py-0.5 rounded-full">
                נוסף: {item.added_date}
              </span>
            </div>

            {/* AI Analysis */}
            {aiData[item.ticker] && (
              <div className="mt-3 border-t border-[#1e1e2e] pt-3">
                <button
                  onClick={() => setExpandedAi(expandedAi === item.ticker ? null : item.ticker)}
                  className="text-[11px] text-purple-400 hover:text-purple-300 flex items-center gap-1"
                >
                  <span>🤖</span>
                  ניתוח AI — {aiData[item.ticker].date}
                  <span className={`mr-2 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                    aiData[item.ticker].sentiment === 'חיובי מאוד' || aiData[item.ticker].sentiment === 'חיובי'
                      ? 'bg-emerald-400/10 text-emerald-400'
                      : aiData[item.ticker].sentiment === 'שלילי'
                        ? 'bg-red-400/10 text-red-400'
                        : 'bg-yellow-400/10 text-yellow-400'
                  }`}>
                    {aiData[item.ticker].sentiment}
                  </span>
                  <span className="text-gray-600">{expandedAi === item.ticker ? '▲' : '▼'}</span>
                </button>
                {expandedAi === item.ticker && (
                  <div className="mt-2 bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e] text-xs text-gray-300 leading-relaxed whitespace-pre-line">
                    {aiData[item.ticker].analysis}
                    {aiData[item.ticker].headlines?.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-[#1e1e2e]">
                        <div className="text-gray-500 mb-1">כותרות אחרונות:</div>
                        {aiData[item.ticker].headlines.map((h, j) => (
                          <div key={j} className="text-gray-400 text-[10px]">• {h}</div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {data.watchlist.length === 0 && (
          <div className="text-gray-500 text-center py-8">אין מניות בווטצ'ליסט כרגע</div>
        )}

        {/* Position Sizing */}
        {sizing && sizing.positions && (
          <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e] mt-4">
            <h3 className="text-sm font-bold text-white mb-3 flex items-center gap-2">
              <span>💰</span> חלוקת תיק מוצעת
            </h3>

            <div className="space-y-3 mb-4">
              {sizing.positions.map(p => (
                <div key={p.ticker} className="flex items-center gap-3">
                  <span className="text-sm font-bold text-white w-12" dir="ltr">{p.ticker}</span>
                  <div className="flex-1">
                    <div className="w-full h-3 bg-[#1e1e2e] rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-gradient-to-l from-purple-500 to-indigo-500"
                        style={{ width: `${p.allocation_pct / 25 * 100}%` }}
                      />
                    </div>
                  </div>
                  <span className="text-sm font-bold text-purple-400 w-12 text-left font-mono" dir="ltr">
                    {p.allocation_pct}%
                  </span>
                  <span className="text-[10px] text-gray-500 w-14 text-left" dir="ltr">
                    ({p.total_score}/100)
                  </span>
                </div>
              ))}

              {/* Cash */}
              <div className="flex items-center gap-3">
                <span className="text-sm font-bold text-gray-400 w-12">מזומן</span>
                <div className="flex-1">
                  <div className="w-full h-3 bg-[#1e1e2e] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gray-600"
                      style={{ width: `${sizing.cash_pct / 100 * 100}%` }}
                    />
                  </div>
                </div>
                <span className="text-sm font-bold text-gray-400 w-12 text-left font-mono" dir="ltr">
                  {sizing.cash_pct}%
                </span>
                <span className="text-[10px] text-gray-600 w-14"></span>
              </div>
            </div>

            {/* Exit strategy */}
            <div className="border-t border-[#1e1e2e] pt-3 space-y-1.5">
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <span>🎯</span>
                <span>יעד: <strong className="text-emerald-400">+500%</strong></span>
                <span className="text-gray-600">|</span>
                <span>⏱️ זמן צפוי: <strong className="text-white">8-12 חודשים</strong></span>
              </div>
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <span>⚠️</span>
                <span>Stop loss מוצע: <strong className="text-red-400">-35%</strong></span>
                <span className="text-gray-600">(נורמלי לאסטרטגיה זו)</span>
              </div>
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <span>📐</span>
                <span>מקסימום 25% בפוזיציה בודדת (Quarter Kelly)</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Stats — left 40% */}
      <div className="lg:col-span-2 space-y-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <span>📊</span> סטטיסטיקות מערכת
        </h2>

        <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e] space-y-4">
          {[
            { label: 'סיגנלים השנה', value: data.total_signals_ytd, color: 'text-purple-400' },
            { label: 'דיוק היסטורי', value: '15%', color: 'text-emerald-400' },
            { label: 'תשואה חציונית (צדקנו)', value: '+428%', color: 'text-emerald-400' },
            { label: 'תשואה חציונית (טעינו)', value: '+49%', color: 'text-yellow-400' },
            { label: 'ריצות היסטוריות שנותחו', value: data.total_runners, color: 'text-white' },
            { label: 'סיגנל הבא צפוי', value: 'כל ~6 שבועות', color: 'text-gray-400' },
          ].map((s, i) => (
            <div key={i} className="flex items-center justify-between">
              <span className="text-sm text-gray-400">{s.label}</span>
              <span className={`text-sm font-bold font-mono ${s.color}`}>{s.value}</span>
            </div>
          ))}
        </div>

        {/* Recent logs */}
        <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e]">
          <h3 className="text-sm font-bold text-white mb-3">פעילות אחרונה</h3>
          {logs.length > 0 ? (
            <div className="space-y-2">
              {logs.map((l, i) => (
                <div key={i} className="flex items-center justify-between text-xs bg-[#0a0a0f] rounded-lg p-2.5 border border-[#1e1e2e]">
                  <span className="text-gray-400 font-mono" dir="ltr">{l.date}</span>
                  <span className="text-gray-500">
                    {l.signals_count > 0
                      ? <span className="text-purple-400 font-bold">סיגנל!</span>
                      : l.status_changes > 0
                        ? `${l.status_changes} שינויים`
                        : 'ללא שינוי'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-gray-500">אין לוגים עדיין. הפעל daily_alert.py</div>
          )}
        </div>

        {/* Signal reminder */}
        <div className="bg-purple-500/5 border border-purple-500/20 rounded-xl p-4">
          <div className="text-xs text-purple-300 font-medium mb-1">תזכורת</div>
          <div className="text-xs text-gray-400 leading-relaxed">
            כשמניה מהווטצ'ליסט חוצה MA200 עם ווליום כפול — זה הסיגנל.
            הדיוק: 1 מתוך 7 הופכת לריצה של 500%+.
            גם ה-6 "שגויות" מחזירות +49% חציוני.
          </div>
        </div>
      </div>

      {/* Options Analysis — Full Width Below */}
      {options && options.recommendation && !options.recommendation.error && (
        <div className="lg:col-span-5 mt-2">
          <div className="bg-[#12121a] rounded-xl p-5 border border-[#1e1e2e]">
            <h3 className="text-sm font-bold text-white mb-4 flex items-center gap-2">
              <span>📊</span> ניתוח אופציות — LEAPS
            </h3>

            {/* Scores comparison */}
            <div className="grid grid-cols-3 gap-3 mb-4">
              {(options.scores || []).map(s => (
                <div key={s.ticker} className={`rounded-lg p-3 border ${s.ticker === options.winner ? 'border-purple-500/50 bg-purple-500/5' : 'border-[#1e1e2e] bg-[#0a0a0f]'}`}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-bold text-white text-sm" dir="ltr">{s.ticker}</span>
                    {s.ticker === options.winner && <span className="text-[10px] bg-purple-400/20 text-purple-400 px-1.5 py-0.5 rounded">🏆 מומלץ</span>}
                  </div>
                  <div className="text-xl font-black text-purple-400 mb-2">{s.total_score}<span className="text-xs text-gray-500">/100</span></div>
                  <div className="space-y-1 text-[10px]">
                    <div className="flex justify-between"><span className="text-gray-500">IV</span><span className="text-gray-300">{s.iv_score}/30</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">קטליסט</span><span className="text-gray-300">{s.catalyst_score}/25</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">תזמון</span><span className="text-gray-300">{s.timing_score}/25</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">שורט</span><span className="text-gray-300">{s.short_score}/20</span></div>
                  </div>
                </div>
              ))}
            </div>

            {/* Winner recommendation */}
            {(() => {
              const rec = options.recommendation
              const atm = rec.atm
              const scenarios = rec.scenarios || []
              const portfolio = rec.portfolio_options || {}
              return (
                <div className="space-y-4">
                  <div className="bg-[#0a0a0f] rounded-lg p-4 border border-[#1e1e2e]">
                    <div className="text-xs text-purple-400 font-bold mb-1">המלצה: {rec.ticker} LEAPS — {rec.expiration}</div>
                    <div className="text-xs text-gray-400 mb-2">{rec.why_this_stock}</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
                      <div className="bg-[#12121a] rounded p-2">
                        <div className="text-gray-500">סטרייק ATM</div>
                        <div className="text-white font-bold" dir="ltr">${atm.strike}</div>
                      </div>
                      <div className="bg-[#12121a] rounded p-2">
                        <div className="text-gray-500">פרמיה/חוזה</div>
                        <div className="text-white font-bold" dir="ltr">${atm.cost_per_contract}</div>
                      </div>
                      <div className="bg-[#12121a] rounded p-2">
                        <div className="text-gray-500">Break-even</div>
                        <div className="text-white font-bold" dir="ltr">${atm.breakeven}</div>
                      </div>
                      <div className="bg-[#12121a] rounded p-2">
                        <div className="text-gray-500">Delta</div>
                        <div className="text-white font-bold" dir="ltr">{atm.delta}</div>
                      </div>
                    </div>
                  </div>

                  {/* Scenarios */}
                  <div className="bg-[#0a0a0f] rounded-lg p-4 border border-[#1e1e2e]">
                    <div className="text-xs text-gray-500 mb-2">תרחישים (ATM Call):</div>
                    <div className="grid grid-cols-5 gap-1 text-[10px]">
                      {scenarios.map((s, i) => (
                        <div key={i} className={`rounded p-2 text-center ${s.atm_return_pct > 0 ? 'bg-emerald-400/5' : 'bg-red-400/5'}`}>
                          <div className="text-gray-500">מניה {s.stock_move_pct >= 0 ? '+' : ''}{s.stock_move_pct}%</div>
                          <div className={`font-bold ${s.atm_return_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {s.atm_return_pct >= 0 ? '+' : ''}{s.atm_return_pct}%
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Portfolio options */}
                  <div className="bg-[#0a0a0f] rounded-lg p-4 border border-[#1e1e2e]">
                    <div className="text-xs text-gray-500 mb-2">חלוקת תיק ($10,000):</div>
                    <div className="grid grid-cols-3 gap-2 text-[10px]">
                      {['option_a', 'option_b', 'option_c'].map(key => {
                        const opt = portfolio[key]
                        if (!opt) return null
                        return (
                          <div key={key} className="bg-[#12121a] rounded p-2.5 border border-[#1e1e2e]">
                            <div className="text-gray-300 font-bold mb-1">{opt.name?.split('(')[0]}</div>
                            <div className="text-gray-500 mb-1">{opt.stock_pct}% מניה + {opt.leaps_pct}% LEAPS</div>
                            <div className="text-red-400">הפסד מקס: -{opt.max_loss_pct}%</div>
                            <div className="text-emerald-400 font-bold">Best: +{opt.best_case_pct?.toLocaleString()}%</div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </div>
              )
            })()}
          </div>
        </div>
      )}
    </div>
  )
}
