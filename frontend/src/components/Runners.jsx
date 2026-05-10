import { useState, useEffect } from 'react'
import axios from 'axios'

export default function Runners({ api }) {
  const [runners, setRunners] = useState([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState('peak_return_pct')
  const [sortDir, setSortDir] = useState(-1)
  const [filterSector, setFilterSector] = useState('')
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    axios.get(`${api}/runners/full`).then(r => {
      setRunners(r.data)
      setLoading(false)
    })
  }, [])

  const sectors = [...new Set(runners.map(r => r.sector).filter(Boolean))]

  const filtered = runners
    .filter(r => !filterSector || r.sector === filterSector)
    .sort((a, b) => ((a[sortKey] || 0) - (b[sortKey] || 0)) * sortDir)

  const handleSort = (key) => {
    if (sortKey === key) setSortDir(d => d * -1)
    else { setSortKey(key); setSortDir(-1) }
  }

  const cols = [
    { key: 'symbol', label: 'טיקר', w: 'w-16' },
    { key: 'sector', label: 'סקטור', w: 'w-32' },
    { key: 'peak_return_pct', label: 'תשואה שיא', w: 'w-24' },
    { key: 'days_to_peak', label: 'ימים לשיא', w: 'w-20' },
    { key: 'ideal_entry_return_pct', label: 'תשואה מכניסה', w: 'w-24' },
    { key: 'entry_window_days', label: 'חלון כניסה', w: 'w-20' },
    { key: 'run_start_date', label: 'תאריך', w: 'w-24' },
  ]

  if (loading) return <div className="text-gray-500 text-center py-12">טוען 159 ריצות...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-bold text-white">ריצות היסטוריות — {filtered.length} ריצות</h2>
        <select
          value={filterSector}
          onChange={e => setFilterSector(e.target.value)}
          className="bg-[#12121a] border border-[#1e1e2e] text-gray-300 text-xs rounded-lg px-3 py-2"
        >
          <option value="">כל הסקטורים</option>
          {sectors.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="overflow-x-auto rounded-xl border border-[#1e1e2e]">
        <table className="w-full text-sm">
          <thead className="bg-[#12121a] text-gray-500 text-xs">
            <tr>
              {cols.map(c => (
                <th
                  key={c.key}
                  onClick={() => handleSort(c.key)}
                  className={`px-4 py-3 cursor-pointer hover:text-gray-300 transition-colors text-right ${
                    sortKey === c.key ? 'text-purple-400' : ''
                  }`}
                >
                  {c.label} {sortKey === c.key ? (sortDir > 0 ? '↑' : '↓') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <>
                <tr
                  key={`${r.symbol}-${r.run_start_date}`}
                  onClick={() => setExpanded(expanded === i ? null : i)}
                  className={`border-t border-[#1e1e2e] cursor-pointer hover:bg-purple-500/5 transition-colors ${
                    i % 2 === 0 ? 'bg-[#0c0c14]' : 'bg-[#0a0a0f]'
                  }`}
                >
                  <td className="px-4 py-2.5 font-bold text-white" dir="ltr">{r.symbol}</td>
                  <td className="px-4 py-2.5 text-xs text-gray-500">{r.sector || '—'}</td>
                  <td className="px-4 py-2.5 font-mono font-bold text-emerald-400" dir="ltr">+{r.peak_return_pct?.toLocaleString()}%</td>
                  <td className="px-4 py-2.5 text-gray-400 font-mono text-xs" dir="ltr">{r.days_to_peak || '—'}d</td>
                  <td className="px-4 py-2.5 font-mono text-emerald-300 text-xs" dir="ltr">
                    {r.ideal_entry_return_pct ? `+${r.ideal_entry_return_pct.toLocaleString()}%` : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-gray-400 font-mono text-xs" dir="ltr">{r.entry_window_days || '—'}d</td>
                  <td className="px-4 py-2.5 text-gray-500 font-mono text-xs" dir="ltr">{r.run_start_date}</td>
                </tr>
                {expanded === i && (
                  <tr key={`exp-${i}`} className="bg-[#0c0c14]">
                    <td colSpan={7} className="px-6 py-4 border-t border-purple-500/20">
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                        <div className="bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e]">
                          <div className="text-gray-500 mb-1">ימי ספייק לפני</div>
                          <div className="text-white font-bold">{r.spike_count_90d || '?'} ימים</div>
                        </div>
                        <div className="bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e]">
                          <div className="text-gray-500 mb-1">סוג פריצה</div>
                          <div className="text-white font-bold">{r.launch_type || '?'}</div>
                        </div>
                        <div className="bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e]">
                          <div className="text-gray-500 mb-1">ירידה מקסי��לית בדרך</div>
                          <div className="text-red-400 font-bold font-mono" dir="ltr">{r.max_drawdown_during_run_pct?.toFixed(0) || '?'}%</div>
                        </div>
                        <div className="bg-[#0a0a0f] rounded-lg p-3 border border-[#1e1e2e]">
                          <div className="text-gray-500 mb-1">מגמה לפני</div>
                          <div className="text-white font-bold">{r.accum_price_trend || '?'}</div>
                        </div>
                      </div>
                      <div className="mt-3 text-xs text-gray-400">
                        ספייק ראשון {r.days_first_spike_to_run || '?'} ימים לפני הריצה •
                        ווליום ביום הפריצה: {r.launch_vol_ratio || '?'}X •
                        סגנון: {r.run_style || '?'}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
