import { useState, useEffect } from 'react'
import axios from 'axios'

export default function Trades({ api }) {
  const [trades, setTrades] = useState([])
  const [summary, setSummary] = useState(null)
  const [tab, setTab] = useState('open')
  const [showForm, setShowForm] = useState(false)
  const [closeModal, setCloseModal] = useState(null)
  const [form, setForm] = useState({ ticker: '', entry_price: '', shares: '', entry_date: '', note: '' })
  const [closeForm, setCloseForm] = useState({ exit_price: '', exit_date: '' })

  const load = () => {
    axios.get(`${api}/trades`).then(r => setTrades(r.data))
    axios.get(`${api}/trades/summary`).then(r => setSummary(r.data))
  }

  useEffect(load, [])

  const openTrades = trades.filter(t => t.status === 'open')
  const closedTrades = trades.filter(t => t.status === 'closed')

  const handleAdd = () => {
    axios.post(`${api}/trades`, {
      ticker: form.ticker,
      entry_price: parseFloat(form.entry_price),
      shares: parseInt(form.shares),
      entry_date: form.entry_date,
      note: form.note,
    }).then(() => {
      setShowForm(false)
      setForm({ ticker: '', entry_price: '', shares: '', entry_date: '', note: '' })
      load()
    })
  }

  const handleClose = (id) => {
    axios.put(`${api}/trades/${id}`, {
      exit_price: parseFloat(closeForm.exit_price),
      exit_date: closeForm.exit_date,
    }).then(() => {
      setCloseModal(null)
      setCloseForm({ exit_price: '', exit_date: '' })
      load()
    })
  }

  const handleDelete = (id) => {
    if (confirm('למחוק עסקה?')) {
      axios.delete(`${api}/trades/${id}`).then(load)
    }
  }

  const pnlColor = (pct) => {
    if (pct >= 500) return 'text-yellow-400'
    if (pct > 0) return 'text-emerald-400'
    if (pct > -35) return 'text-red-400'
    return 'text-red-500 animate-pulse'
  }

  const rowBorder = (t) => {
    if (t.hit_target) return 'border-r-2 border-r-yellow-400'
    if (t.hit_stop) return 'border-r-2 border-r-red-500'
    if ((t.pnl_pct || 0) > 0) return 'border-r-2 border-r-emerald-400/50'
    return 'border-r-2 border-r-red-400/30'
  }

  return (
    <div>
      {/* Summary Bar */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          {[
            { label: 'סה"כ הושקע', value: `$${summary.total_invested.toLocaleString()}`, color: 'text-white' },
            { label: 'שווי נוכחי', value: `$${summary.current_value.toLocaleString()}`, color: 'text-white' },
            { label: 'רווח/הפסד', value: `${summary.total_pnl >= 0 ? '+' : ''}$${summary.total_pnl.toLocaleString()}`, color: summary.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400' },
            { label: '% שינוי', value: `${summary.total_pnl_pct >= 0 ? '+' : ''}${summary.total_pnl_pct}%`, color: summary.total_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400' },
            { label: 'עסקאות פתוחות', value: summary.open_count, color: 'text-purple-400' },
          ].map((s, i) => (
            <div key={i} className="bg-[#12121a] rounded-xl p-3 border border-[#1e1e2e] text-center">
              <div className={`text-lg font-black ${s.color}`}>{s.value}</div>
              <div className="text-[10px] text-gray-500 mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Tabs + Add button */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex gap-1">
          <button onClick={() => setTab('open')} className={`px-4 py-2 text-sm rounded-lg ${tab === 'open' ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30' : 'text-gray-500 hover:text-gray-300'}`}>
            פתוחות ({openTrades.length})
          </button>
          <button onClick={() => setTab('closed')} className={`px-4 py-2 text-sm rounded-lg ${tab === 'closed' ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30' : 'text-gray-500 hover:text-gray-300'}`}>
            סגורות ({closedTrades.length})
          </button>
        </div>
        <button onClick={() => setShowForm(true)} className="bg-purple-500 hover:bg-purple-600 text-white text-sm font-bold px-4 py-2 rounded-lg transition-colors">
          + עסקה חדשה
        </button>
      </div>

      {/* Add Trade Form */}
      {showForm && (
        <div className="bg-[#12121a] rounded-xl p-5 border border-purple-500/30 mb-4">
          <h3 className="text-sm font-bold text-white mb-3">עסקה חדשה</h3>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <input placeholder="טיקר" value={form.ticker} onChange={e => setForm({...form, ticker: e.target.value})}
              className="bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" dir="ltr" />
            <input placeholder="מחיר כניסה $" type="number" step="0.01" value={form.entry_price} onChange={e => setForm({...form, entry_price: e.target.value})}
              className="bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" dir="ltr" />
            <input placeholder="כמות" type="number" value={form.shares} onChange={e => setForm({...form, shares: e.target.value})}
              className="bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" dir="ltr" />
            <input type="date" value={form.entry_date} onChange={e => setForm({...form, entry_date: e.target.value})}
              className="bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" dir="ltr" />
            <input placeholder="הערה (אופציונלי)" value={form.note} onChange={e => setForm({...form, note: e.target.value})}
              className="bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" />
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={handleAdd} className="bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-bold px-4 py-2 rounded-lg">שמור</button>
            <button onClick={() => setShowForm(false)} className="text-gray-500 text-xs px-4 py-2">ביטול</button>
          </div>
        </div>
      )}

      {/* Open Trades Table */}
      {tab === 'open' && (
        <div className="overflow-x-auto rounded-xl border border-[#1e1e2e]">
          {openTrades.length === 0 ? (
            <div className="text-gray-500 text-center py-12">אין עסקאות פתוחות. לחץ "+ עסקה חדשה" להתחלה.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-[#12121a] text-gray-500 text-xs">
                <tr>
                  <th className="px-4 py-3 text-right">טיקר</th>
                  <th className="px-4 py-3 text-left">כניסה</th>
                  <th className="px-4 py-3 text-left">כמות</th>
                  <th className="px-4 py-3 text-left">עלות</th>
                  <th className="px-4 py-3 text-left">שווי עכשיו</th>
                  <th className="px-4 py-3 text-left">רווח/הפסד</th>
                  <th className="px-4 py-3 text-left">%</th>
                  <th className="px-4 py-3 text-left">ימים</th>
                  <th className="px-4 py-3 text-center">סטטוס</th>
                  <th className="px-4 py-3 text-center">פעולות</th>
                </tr>
              </thead>
              <tbody>
                {openTrades.map(t => (
                  <tr key={t.id} className={`border-t border-[#1e1e2e] ${rowBorder(t)}`}>
                    <td className="px-4 py-3 font-bold text-white" dir="ltr">{t.ticker}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-400" dir="ltr">${t.entry_price}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs" dir="ltr">{t.shares}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-400" dir="ltr">${t.total_cost?.toLocaleString()}</td>
                    <td className="px-4 py-3 font-mono text-xs text-white" dir="ltr">${t.current_value?.toLocaleString()}</td>
                    <td className={`px-4 py-3 font-mono text-xs font-bold ${pnlColor(t.pnl_pct || 0)}`} dir="ltr">
                      {t.pnl_dollar >= 0 ? '+' : ''}{t.pnl_dollar?.toLocaleString()}
                    </td>
                    <td className={`px-4 py-3 font-mono text-xs font-bold ${pnlColor(t.pnl_pct || 0)}`} dir="ltr">
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs" dir="ltr">{t.days_held}d</td>
                    <td className="px-4 py-3 text-center text-sm">
                      {t.hit_target ? '🏆' : t.hit_stop ? '⚠️' : '⏳'}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <button onClick={() => setCloseModal(t.id)} className="text-[10px] text-yellow-400 hover:text-yellow-300 ml-2">סגור</button>
                      <button onClick={() => handleDelete(t.id)} className="text-[10px] text-red-400/50 hover:text-red-400 mr-2">מחק</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Closed Trades Table */}
      {tab === 'closed' && (
        <div className="overflow-x-auto rounded-xl border border-[#1e1e2e]">
          {closedTrades.length === 0 ? (
            <div className="text-gray-500 text-center py-12">אין עסקאות סגורות עדיין.</div>
          ) : (
            <>
              <table className="w-full text-sm">
                <thead className="bg-[#12121a] text-gray-500 text-xs">
                  <tr>
                    <th className="px-4 py-3 text-right">טיקר</th>
                    <th className="px-4 py-3 text-left">כניסה</th>
                    <th className="px-4 py-3 text-left">יציאה</th>
                    <th className="px-4 py-3 text-left">רווח/הפסד %</th>
                    <th className="px-4 py-3 text-left">ימים</th>
                    <th className="px-4 py-3 text-left">תאריך</th>
                  </tr>
                </thead>
                <tbody>
                  {closedTrades.map(t => (
                    <tr key={t.id} className="border-t border-[#1e1e2e]">
                      <td className="px-4 py-3 font-bold text-white" dir="ltr">{t.ticker}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-400" dir="ltr">${t.entry_price}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-400" dir="ltr">${t.exit_price}</td>
                      <td className={`px-4 py-3 font-mono text-xs font-bold ${pnlColor(t.pnl_pct || 0)}`} dir="ltr">
                        {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct}%
                      </td>
                      <td className="px-4 py-3 text-gray-500 text-xs" dir="ltr">{t.days_held}d</td>
                      <td className="px-4 py-3 text-gray-500 text-xs font-mono" dir="ltr">{t.entry_date} → {t.exit_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {summary && (
                <div className="p-4 bg-[#12121a] border-t border-[#1e1e2e] flex gap-6 text-xs text-gray-400">
                  <span>אחוז נצחונות: <strong className="text-white">{summary.closed_win_rate}%</strong></span>
                  <span>תשואה ממוצעת: <strong className="text-white">{summary.closed_avg_return}%</strong></span>
                  <span>עסקה הכי טובה: <strong className="text-emerald-400">+{summary.best_trade}%</strong></span>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Close Trade Modal */}
      {closeModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setCloseModal(null)}>
          <div className="bg-[#12121a] rounded-xl p-6 border border-[#1e1e2e] w-80" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-bold text-white mb-4">סגירת עסקה</h3>
            <div className="space-y-3">
              <input placeholder="מחיר יציאה $" type="number" step="0.01" value={closeForm.exit_price}
                onChange={e => setCloseForm({...closeForm, exit_price: e.target.value})}
                className="w-full bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" dir="ltr" />
              <input type="date" value={closeForm.exit_date}
                onChange={e => setCloseForm({...closeForm, exit_date: e.target.value})}
                className="w-full bg-[#0a0a0f] border border-[#1e1e2e] rounded-lg px-3 py-2 text-sm text-white" dir="ltr" />
            </div>
            <div className="flex gap-2 mt-4">
              <button onClick={() => handleClose(closeModal)} className="bg-yellow-500 hover:bg-yellow-600 text-black text-xs font-bold px-4 py-2 rounded-lg">סגור עסקה</button>
              <button onClick={() => setCloseModal(null)} className="text-gray-500 text-xs px-4 py-2">ביטול</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
