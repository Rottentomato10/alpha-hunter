import { useState } from 'react'
import Dashboard from './components/Dashboard'
import Runners from './components/Runners'
import DNA from './components/DNA'
import Radar from './components/Radar'
import DailyLog from './components/DailyLog'
import Trades from './components/Trades'

const tabs = [
  { id: 'dashboard', label: 'דשבורד', icon: '📡' },
  { id: 'trades', label: 'עסקאות', icon: '💼' },
  { id: 'runners', label: 'ריצות היסטוריות', icon: '🏆' },
  { id: 'dna', label: 'ה-DNA', icon: '🧬' },
  { id: 'radar', label: 'רדאר', icon: '📡' },
  { id: 'log', label: 'לוג יומי', icon: '📋' },
]

const API = '/api'

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')

  return (
    <div className="min-h-screen bg-[#0a0a0f]" dir="rtl">
      <header className="border-b border-[#1e1e2e] bg-[#0a0a0f]/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-indigo-600 flex items-center justify-center text-xs font-black text-white">A</div>
            <span className="text-lg font-extrabold text-white tracking-tight">ALPHA HUNTER</span>
            <span className="text-[10px] text-purple-400 bg-purple-400/10 border border-purple-400/20 px-2 py-0.5 rounded-full font-medium">500%+ סורק</span>
          </div>
        </div>
        <div className="max-w-7xl mx-auto px-6 pb-0">
          <nav className="flex gap-0.5 -mb-px">
            {tabs.map(t => (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`px-4 py-2.5 text-sm font-medium rounded-t-lg transition-all flex items-center gap-1.5 border-b-2 ${
                  activeTab === t.id
                    ? 'text-purple-400 border-purple-500 bg-[#12121a]'
                    : 'text-gray-500 border-transparent hover:text-gray-300 hover:bg-[#12121a]/50'
                }`}
              >
                <span className="text-xs">{t.icon}</span>
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === 'dashboard' && <Dashboard api={API} />}
        {activeTab === 'trades' && <Trades api={API} />}
        {activeTab === 'runners' && <Runners api={API} />}
        {activeTab === 'dna' && <DNA api={API} />}
        {activeTab === 'radar' && <Radar api={API} />}
        {activeTab === 'log' && <DailyLog api={API} />}
      </main>
    </div>
  )
}
