import MessageList from '../components/MessageList.jsx'
import Composer from '../components/Composer.jsx'
import SidePanel from '../components/panel/SidePanel.jsx'

// 상담 화면: 헤더 + (진로 상담이 시작되면) 알아가기·진로 상담 탭 + 말풍선 목록 + 입력창 + 사용자가 열었을 때의 진로맵.
export default function ChatScreen({ header, tabs, chat, composer, panel }) {
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
            className={`secondary panel-toggle${panel.open ? ' open' : ''}`}
            onClick={panel.onToggle}
            aria-expanded={panel.open}
            aria-controls="career-map-panel"
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
          {tabs && (
            <div className="stage-tabs" role="tablist">
              {[
                ['rapport', '알아가기'],
                ['career', '진로 상담'],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={tabs.active === key}
                  className={`stage-tab${tabs.active === key ? ' active' : ''}`}
                  onClick={() => tabs.onChange(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <MessageList {...chat} />
          <Composer {...composer} />
        </main>
        {panel.open && (
          <SidePanel
            careerMap={panel.careerMap}
            stale={panel.stale}
            canSummarize={panel.canSummarize}
            summarizing={panel.summarizing}
            summaryError={panel.summaryError}
            onSummarize={panel.onSummarize}
            onClose={panel.onClose}
          />
        )}
      </div>
    </div>
  )
}
