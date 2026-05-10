import { useState, useEffect } from 'react'
import axios from 'axios'

export default function DailyLog({ api }) {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    axios.get(`${api}/daily-logs`).then(r => {
      setLogs(r.data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-gray-500 text-center py-12">טוען לוגים...</div>

  return (
    <div>
      <div className="mb-5">
        <h2 className="text-lg font-bold text-white">לוג יומי</h2>
        <p className="text-xs text-gray-500 mt-1">
          כל בוקר המערכת סורקת את הווטצ'ליסט ומחפשת סיגנלים חדשים.
          כאן מופיע מה קרה בכל יום.
        </p>
      </div>

      {logs.length === 0 ? (
        <div className="text-center py-12 text-gray-600">
          <div className="text-3xl mb-3">📋</div>
          <p>אין לוגים עדיין.</p>
          <p className="text-xs mt-1">הרץ <code className="bg-[#1e1e2e] px-1.5 py-0.5 rounded text-purple-300">python3 daily_alert.py</code> ליצירת הלוג הראשון.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {logs.map((log, i) => {
            const hasSignal = log.signals_count > 0
            const hasChange = log.status_changes > 0

            return (
              <div key={i}>
                <div
                  onClick={() => setExpanded(expanded === i ? null : i)}
                  className={`bg-[#12121a] rounded-xl p-4 border cursor-pointer hover:border-purple-500/30 transition-colors ${
                    hasSignal ? 'border-purple-500/40' : 'border-[#1e1e2e]'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-mono text-gray-300" dir="ltr">{log.date}</span>
                      {hasSignal && (
                        <span className="text-[10px] font-bold text-purple-400 bg-purple-400/10 border border-purple-400/20 px-2 py-0.5 rounded-full">
                          🔥 סיגנל!
                        </span>
                      )}
                      {hasChange && !hasSignal && (
                        <span className="text-[10px] font-bold text-yellow-400 bg-yellow-400/10 border border-yellow-400/20 px-2 py-0.5 rounded-full">
                          {log.status_changes} שינויים
                        </span>
                      )}
                      {!hasSignal && !hasChange && (
                        <span className="text-[10px] text-gray-500">ללא שינוי</span>
                      )}
                    </div>
                    <span className="text-gray-600 text-xs">{expanded === i ? '▲' : '▼'}</span>
                  </div>
                </div>

                {expanded === i && (
                  <div className="bg-[#0c0c14] rounded-b-xl border border-t-0 border-[#1e1e2e] p-4 -mt-2">
                    {/* Watchlist alerts */}
                    {log.watchlist_alerts && log.watchlist_alerts.length > 0 && (
                      <div className="mb-3">
                        <div className="text-xs text-gray-500 mb-2">שינויי ווטצ'ליסט:</div>
                        {log.watchlist_alerts.map((a, j) => (
                          <div key={j} className="flex items-center gap-2 text-xs mb-1">
                            <span className="font-bold text-white" dir="ltr">{a.ticker}</span>
                            {a.changed ? (
                              <span className="text-yellow-400">
                                {a.prev_status} → {a.new_status}
                              </span>
                            ) : (
                              <span className="text-gray-500">{a.new_status}</span>
                            )}
                            <span className="text-gray-600 font-mono" dir="ltr">
                              ${a.current_price} | MA200 {a.dist_ma200_pct > 0 ? '+' : ''}{a.dist_ma200_pct}%
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* New signals */}
                    {log.new_signals && log.new_signals.length > 0 && (
                      <div>
                        <div className="text-xs text-purple-400 font-bold mb-2">סיגנלים חדשים:</div>
                        {log.new_signals.map((s, j) => (
                          <div key={j} className="text-xs text-white bg-purple-500/10 rounded p-2 border border-purple-500/20">
                            {s.symbol} — vol {s.cross_vol_ratio}X, rev +{s.rev_growth_pct}%, short {s.short_pct}%
                          </div>
                        ))}
                      </div>
                    )}

                    {!log.watchlist_alerts?.length && !log.new_signals?.length && (
                      <div className="text-xs text-gray-500">סריקה רוטינית — ללא אירועים מיוחדים.</div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
