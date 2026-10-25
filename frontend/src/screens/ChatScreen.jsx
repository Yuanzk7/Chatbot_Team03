import MessageList from '../components/MessageList.jsx'
import Composer from '../components/Composer.jsx'
import SidePanel from '../components/panel/SidePanel.jsx'

// 상담 화면: 헤더 + 말풍선 목록 + 입력창 + (열려 있으면) 우측 패널
export default function ChatScreen({ header, chat, composer, panel }) {
  return (
    <div className="app">
      <header className="header">
        <button type="button" className="brand brand-link" onClick={header.onHome} title="첫 채팅 화면으로">
          <h1>CareerMate</h1>
          <p>나의 진로를 찾아가는 대화</p>
        </button>
        <div className="header-right">
          <span className="user-name">{header.displayName}님</span>
          <button
            type="button"
            className={`secondary panel-toggle${panel.view === 'state' ? ' open' : ''}`}
            onClick={() => panel.onToggle('state')}
            aria-pressed={panel.view === 'state'}
          >
            기준·제약
          </button>
          <button
            type="button"
            className={`secondary panel-toggle${panel.view === 'map' ? ' open' : ''}`}
            onClick={() => panel.onToggle('map')}
            aria-pressed={panel.view === 'map'}
          >
            진로맵
            {panel.stale && <span className="dot" aria-label="추가 대화 미반영" />}
          </button>
          <button type="button" className="secondary" onClick={header.onNewSession} disabled={header.busy}>
            새 상담
          </button>
        </div>
      </header>

      <div className="layout">
        <main className="chat">
          <MessageList {...chat} />
          <Composer {...composer} />
        </main>
        {panel.view && (
          <SidePanel
            view={panel.view}
            state={panel.state}
            careerMap={panel.careerMap}
            stale={panel.stale}
            canSummarize={panel.canSummarize}
            summarizing={panel.summarizing}
            summaryError={panel.summaryError}
            onSummarize={panel.onSummarize}
            onClose={() => panel.onToggle(null)}
          />
        )}
      </div>
    </div>
  )
}
