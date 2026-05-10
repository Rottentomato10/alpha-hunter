import { useState, useEffect } from 'react'
import axios from 'axios'

const FORMULA = [
  { icon: '📈', text: 'חצייה מעל ממוצע 200 עם ווליום כפול', sub: 'מחיר עבר מתחת ל-MA200 למעלה, ביום עם נפח 2X+' },
  { icon: '💰', text: 'צמיחת הכנסות מעל 15%', sub: 'הביזנס גדל — לא רק הספקולציה' },
  { icon: '🩳', text: 'שורט אינטרסט מעל 7%', sub: 'דלק לסקוויז + סימן קונטרריאני' },
  { icon: '🏢', text: 'מידקאפ — $500M עד $15B', sub: 'מספיק גדולה לשרוד, מספיק קטנה לזוז' },
  { icon: '📉', text: 'חברה לא רווחית (עדיין)', sub: 'בשלב צמיחה/מהפך — פוטנציאל מקסימלי' },
  { icon: '📏', text: 'מחיר עד 20% מעל MA200', sub: 'לא מורחקת מדי — עדיין בזול' },
]

const FUNNEL = [
  { label: 'סיג��ל בסיסי (ווליום + מגמה)', total: 7568, prec: '1.1%' },
  { label: '+ תחתית 52 שבועות (25%)', total: 2377, prec: '2.2%' },
  { label: '+ שורט מעל 7%', total: 694, prec: '5.6%' },
  { label: '+ ירידה 15%+ ב-30 יום', total: 388, prec: '8.0%' },
  { label: '+ צמיחת הכנסות 15%+', total: 92, prec: '13.0%' },
  { label: '+ מידקאפ', total: 65, prec: '13.9%' },
  { label: '+ לא רווחית', total: 39, prec: '15.4%' },
]

export default function DNA({ api }) {
  const [control, setControl] = useState(null)

  useEffect(() => {
    axios.get(`${api}/control-group`).then(r => setControl(r.data)).catch(() => {})
  }, [])

  const comparison = control?.comparison || []

  return (
    <div className="space-y-8">
      {/* Section 1 — Winners vs Losers */}
      <div>
        <h2 className="text-lg font-bold text-white mb-1">מה מפריד מנצחים ממפסידים</h2>
        <p className="text-xs text-gray-500 mb-4">67 סיגנלים אמיתיים (רצו 500%+) מול 7,126 סיגנלים שקריים (אותה תבנית, לא רצו)</p>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {comparison.filter(c => c.useful === 'YES').map((c, i) => (
            <div key={i} className="bg-[#12121a] rounded-xl p-4 border border-[#1e1e2e]">
              <div className="text-xs text-gray-500 mb-2">{c.metric}</div>
              <div className="flex items-end justify-between gap-4">
                <div className="text-center">
                  <div className="text-lg font-black text-emerald-400">
                    {typeof c.winners_median === 'number' ? c.winners_median.toFixed(1) : c.winners_median}
                  </div>
                  <div className="text-[10px] text-emerald-400/60">מנצחים</div>
                </div>
                <div className="text-xs text-gray-600 mb-1">vs</div>
                <div className="text-center">
                  <div className="text-lg font-black text-red-400">
                    {typeof c.false_signals_median === 'number' ? c.false_signals_median.toFixed(1) : c.false_signals_median}
                  </div>
                  <div className="text-[10px] text-red-400/60">שקריים</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Section 2 — The Formula */}
      <div>
        <h2 className="text-lg font-bold text-white mb-1">הפורמולה</h2>
        <p className="text-xs text-gray-500 mb-4">6 תנאים שחייבים להתקיים בו-זמנית. כשכולם מתכנסים — זה הסיגנל.</p>

        <div className="space-y-2">
          {FORMULA.map((f, i) => (
            <div key={i} className="bg-[#12121a] rounded-xl p-4 border border-[#1e1e2e] flex items-start gap-4">
              <div className="w-10 h-10 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-lg shrink-0">
                {f.icon}
              </div>
              <div>
                <div className="text-sm font-bold text-white">{f.text}</div>
                <div className="text-xs text-gray-500 mt-0.5">{f.sub}</div>
              </div>
              <div className="mr-auto text-emerald-400 text-lg">✓</div>
            </div>
          ))}
        </div>
      </div>

      {/* Section 3 — Precision Funnel */}
      <div>
        <h2 className="text-lg font-bold text-white mb-1">הדיוק לאורך הפילטרים</h2>
        <p className="text-xs text-gray-500 mb-4">כל פילטר מסנן רעש ומעלה את הדיוק. מ-1.1% ל-15.4%.</p>

        <div className="space-y-1.5">
          {FUNNEL.map((step, i) => {
            const maxWidth = FUNNEL[0].total
            const widthPct = Math.max(8, (step.total / maxWidth) * 100)
            const intensity = i / (FUNNEL.length - 1)
            return (
              <div key={i} className="flex items-center gap-3">
                <div className="w-36 text-xs text-gray-500 text-left shrink-0">{step.label}</div>
                <div className="flex-1 relative">
                  <div
                    className="h-8 rounded-md flex items-center justify-between px-3 transition-all"
                    style={{
                      width: `${widthPct}%`,
                      background: `rgba(124, 58, 237, ${0.1 + intensity * 0.3})`,
                      border: `1px solid rgba(124, 58, 237, ${0.2 + intensity * 0.3})`,
                    }}
                  >
                    <span className="text-xs font-mono text-white">{step.total.toLocaleString()}</span>
                    <span className="text-xs font-bold text-purple-300">{step.prec}</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-4 bg-purple-500/5 border border-purple-500/20 rounded-xl p-4">
          <div className="text-xs text-purple-300">
            <strong>שורה תחתונה:</strong> מתוך 7,568 סיגנלים בסיסיים ב-5 שנים, הפילטרים שלנו מצמצמים ל-39 בלבד.
            מתוכם, 6 היו ריצות אמיתיות של 500%+. דיוק: <strong>15.4%</strong> (1 מתוך 6.5).
          </div>
        </div>
      </div>

      {/* Stats box */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'דיוק', value: '15.4%', color: 'text-purple-400' },
          { label: 'תשואה אם צדקנו', value: '+428%', color: 'text-emerald-400' },
          { label: 'תשואה אם טעינו', value: '+49%', color: 'text-yellow-400' },
          { label: '% חיוביים', value: '97%', color: 'text-emerald-400' },
        ].map((s, i) => (
          <div key={i} className="bg-[#12121a] rounded-xl p-4 border border-[#1e1e2e] text-center">
            <div className={`text-2xl font-black ${s.color}`}>{s.value}</div>
            <div className="text-xs text-gray-500 mt-1">{s.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
