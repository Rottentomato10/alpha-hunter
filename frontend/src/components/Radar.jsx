import { useState, useEffect } from 'react'
import axios from 'axios'

export default function Radar({ api }) {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    axios.get(`${api}/radar`).then(r => {
      setData(r.data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-gray-500 text-center py-12">טוען רדאר...</div>

  const getRowColor = (r) => {
    const dist = r.dist_to_ma200_pct || r.price_30d_ret || 0
    if (dist >= 0) return 'border-r-2 border-r-emerald-400 bg-emerald-400/[0.03]'
    if (dist > -5) return 'border-r-2 border-r-yellow-400 bg-yellow-400/[0.02]'
    if (dist > -15) return 'border-r-2 border-r-yellow-600/50'
    return 'border-r-2 border-r-red-400/30'
  }

  const getStatusBadge = (r) => {
    const dist = r.dist_to_ma200_pct || 0
    if (dist >= 0) return { text: 'מעל MA200', color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20' }
    if (dist > -5) return { text: 'קרוב מאוד', color: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20' }
    if (dist > -15) return { text: 'מתקרב', color: 'text-yellow-600 bg-yellow-600/10 border-yellow-600/20' }
    return { text: 'רחוק', color: 'text-red-400 bg-red-400/10 border-red-400/20' }
  }

  const formatMcap = (v) => {
    if (!v) return '—'
    if (v >= 1e9) return `$${(v/1e9).toFixed(1)}B`
    if (v >= 1e6) return `$${(v/1e6).toFixed(0)}M`
    return `$${v}`
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-lg font-bold text-white">רדאר — ווטצ'ליסט מורחב</h2>
          <p className="text-xs text-gray-500 mt-1">מניות שעוברות פילטר פונדמנטלי ומתקרבות לחצייה. צבע = מרחק מ-MA200.</p>
        </div>
        <div className="flex gap-2 text-[10px]">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-400"></span>מעל</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-400"></span>קרוב</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-400"></span>רחוק</span>
        </div>
      </div>

      {data.length === 0 ? (
        <div className="text-gray-500 text-center py-12">אין נתונים. הרץ scanner ותרענן cache.</div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[#1e1e2e]">
          <table className="w-full text-sm">
            <thead className="bg-[#12121a] text-gray-500 text-xs">
              <tr>
                <th className="px-4 py-3 text-right">טיקר</th>
                <th className="px-4 py-3 text-right">ציון</th>
                <th className="px-4 py-3 text-right">סטטוס</th>
                <th className="px-4 py-3 text-left">מחיר</th>
                <th className="px-4 py-3 text-left">צמיחה</th>
                <th className="px-4 py-3 text-left">שורט</th>
                <th className="px-4 py-3 text-left">שווי שוק</th>
                <th className="px-4 py-3 text-right">סקטור</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r, i) => {
                const badge = getStatusBadge(r)
                return (
                  <tr key={i} className={`border-t border-[#1e1e2e] ${getRowColor(r)}`}>
                    <td className="px-4 py-2.5 font-bold text-white" dir="ltr">{r.symbol}</td>
                    <td className="px-4 py-2.5">
                      <span className="text-xs font-bold font-mono text-purple-400 bg-purple-400/10 px-2 py-0.5 rounded">
                        {r.score}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${badge.color}`}>
                        {badge.text}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-gray-300" dir="ltr">${r.current_price}</td>
                    <td className="px-4 py-2.5 text-xs" dir="ltr">
                      {r.rev_growth_pct != null ? (
                        <span className="text-emerald-400 font-bold">+{r.rev_growth_pct}%</span>
                      ) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs" dir="ltr">
                      {r.short_pct != null ? (
                        <span className="text-red-400">{r.short_pct}%</span>
                      ) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-500" dir="ltr">{formatMcap(r.market_cap)}</td>
                    <td className="px-4 py-2.5 text-xs text-gray-500">{r.sector || '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
