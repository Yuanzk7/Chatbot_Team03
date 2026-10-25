import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ApiError,
  chooseCard,
  createSession,
  dismissCards,
  getSession,
  reshuffleCards,
  sendMessage,
  summarize,
} from './api'
import SidePanel from './SidePanel.jsx'
import RapportCards from './RapportCards.jsx'
import yuni from './assets/yuni.png'
import personaFriend from './assets/persona-friend.webp'
import personaAnalyst from './assets/persona-analyst.webp'
import personaSpicy from './assets/persona-spicy.webp'
import './App.css'

const STORAGE_KEY = 'career-chat-session-id'

// 이름 화면에서 고르는 말투(페르소나). 서버 prompts.PERSONAS의 키와 같다.
// 카드 그림 안에 이름과 예시 문장이 들어 있어 화면에는 그림만 보여준다
const PERSONAS = [
  { value: 'friend', label: '5년지기 친구', image: personaFriend },
  { value: 'counselor', label: '차분한 분석가', image: personaAnalyst },
  { value: 'spicy', label: '매운맛 조언가', image: personaSpicy },
]

// 챗봇 말풍선 옆 프로필: 동그라미 안에 윤이 얼굴
function Avatar() {
  return (
    <div className="avatar" aria-hidden="true">
      <img src={yuni} alt="" />
    </div>
  )
}

export default function App() {
  // 'loading': 저장된 상담 복원 중, 'name': 이름 입력 화면, 'chat': 상담 화면
  const [screen, setScreen] = useState('loading')
  const [name, setName] = useState('')
  const [tone, setTone] = useState('counselor')
  const [nameError, setNameError] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  // 현재까지 확인한 기준·제약 (서버가 매 턴 갱신해 돌려준다)
  const [state, setState] = useState(null)
  // 정리 결과(진로맵)와 마지막 메시지 id. 둘을 비교해 '추가 대화 미반영'을 판단한다
  const [careerMap, setCareerMap] = useState(null)
  const [lastMessageId, setLastMessageId] = useState(0)
  const [summarizing, setSummarizing] = useState(false)
  const [summaryError, setSummaryError] = useState(null)
  // 우측 패널에 무엇을 보여줄지: null(닫힘) | 'state'(기준·제약) | 'map'(진로맵)
  const [panelView, setPanelView] = useState(null)
  const [input, setInput] = useState('')
  // 전송 중인 사용자 메시지 { content, requestId }
  const [pending, setPending] = useState(null)
  // 오류 안내 { message, retry: { content, requestId } | null }
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)

  async function startNewSession(preferredName, selectedTone) {
    // 서버가 윤이의 인사를 첫 메시지로 넣어 돌려준다
    const { session_id, state: newState, message } = await createSession(preferredName, selectedTone)
    localStorage.setItem(STORAGE_KEY, session_id)
    setSessionId(session_id)
    setMessages([message])
    setState(newState)
    setCareerMap(null)
    setLastMessageId(message.id)
    setSummaryError(null)
    setPending(null)
    setError(null)
    setInput('')
    setScreen('chat')
  }

  useEffect(() => {
    async function restore() {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        try {
          const data = await getSession(saved)
          setSessionId(saved)
          setMessages(data.messages)
          setState(data.state)
          setCareerMap(data.career_map)
          setLastMessageId(data.last_message_id)
          setName(data.state.preferred_name ?? '')
          setTone(data.state.tone ?? 'counselor')
          setScreen('chat')
          return
        } catch (e) {
          // 서버에 없는 상담 ID면 이름 화면부터 다시. 그 외 오류는 이름 화면에 표시
          if (!(e instanceof ApiError && e.status === 404)) setNameError(e.message)
        }
      }
      setScreen('name')
    }
    restore()
  }, [])

  async function handleStart(e) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setNameError(null)
    try {
      await startNewSession(trimmed, tone)
    } catch (err) {
      setNameError(err.message)
    }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, pending, error])

  async function send(content, requestId) {
    setError(null)
    setPending({ content, requestId })
    try {
      const { message, state: newState } = await sendMessage(sessionId, content, requestId)
      setMessages((prev) => [...prev, { id: `user-${requestId}`, role: 'user', content }, message])
      setState(newState)
      setLastMessageId(message.id)
      setPending(null)
    } catch (e) {
      setPending(null)
      setInput(content)
      setError({ message: e.message, retry: { content, requestId } })
    }
  }

  // 카드 선택 처리 중 (모델 호출이 있어 전송과 겹치지 않게 막는다)
  const [cardBusy, setCardBusy] = useState(false)
  // 전송·정리·카드 선택 중 하나라도 진행 중이면 전송·정리·새 상담을 모두 막는다
  const busy = pending !== null || summarizing || cardBusy

  const pendingCards =
    state?.rapport?.cards && state.rapport.cards.chosen_id === null && !state.rapport.cards.dismissed
      ? state.rapport.cards
      : null

  async function handleChooseCard(cardId) {
    if (busy || !sessionId) return
    setCardBusy(true)
    setError(null)
    try {
      const { messages: added, state: newState } = await chooseCard(sessionId, cardId)
      setMessages((prev) => [...prev, ...added])
      setState(newState)
      setLastMessageId(added[added.length - 1].id)
    } catch (e) {
      setError({ message: e.message, retry: null })
    } finally {
      setCardBusy(false)
    }
  }

  async function handleCardsAction(action) {
    if (busy || !sessionId) return
    setCardBusy(true)
    try {
      const { state: newState } = await action(sessionId)
      setState(newState)
    } catch (e) {
      setError({ message: e.message, retry: null })
    } finally {
      setCardBusy(false)
    }
  }

  async function handleSummarize() {
    if (busy || !sessionId) return
    setSummarizing(true)
    setSummaryError(null)
    try {
      const { career_map } = await summarize(sessionId)
      setCareerMap(career_map)
    } catch (e) {
      setSummaryError(e.message)
    } finally {
      setSummarizing(false)
    }
  }

  function handleSubmit(e) {
    e.preventDefault()
    const content = input.trim()
    if (!content || busy || !sessionId) return
    setInput('')
    // 실패한 메시지를 그대로 다시 보내면 같은 request_id를 재사용해 중복 저장을 막는다
    const requestId =
      error?.retry && error.retry.content === content ? error.retry.requestId : crypto.randomUUID()
    send(content, requestId)
  }

  function handleRetry() {
    if (!error?.retry || busy) return
    setInput('')
    send(error.retry.content, error.retry.requestId)
  }

  // CareerMate 제목 클릭: 이름 화면을 거치지 않고 같은 이름으로 첫 채팅 화면으로 간다
  async function handleHome() {
    if (busy) return
    setPanelView(null)
    const currentName = (state?.preferred_name || name).trim()
    const hasUserMessages = messages.some((m) => m.role === 'user')
    if (!currentName || !hasUserMessages) return
    if (!window.confirm('새 대화를 시작할까요? 이름과 말투는 그대로 유지되고, 현재 상담은 이 브라우저에서 더 이상 열 수 없습니다.')) return
    try {
      await startNewSession(currentName, state?.tone ?? tone)
    } catch (e) {
      setError({ message: e.message, retry: null })
    }
  }

  function handleNewSession() {
    if (busy) return
    if (messages.some((m) => m.role === 'user') && !window.confirm('새 상담을 시작할까요? 현재 상담은 이 브라우저에서 더 이상 열 수 없습니다.')) return
    // 이름 화면으로 돌아간다. 이전 이름은 입력창에 남겨 둔다
    localStorage.removeItem(STORAGE_KEY)
    setSessionId(null)
    setMessages([])
    setState(null)
    setCareerMap(null)
    setLastMessageId(0)
    setError(null)
    setNameError(null)
    setScreen('name')
  }

  const displayName = state?.preferred_name || name
  const stale = careerMap !== null && lastMessageId > careerMap.based_on_message_id

  if (screen === 'loading') {
    return <div className="app" />
  }

  if (screen === 'name') {
    return (
      <div className="app">
        <form className="name-screen" onSubmit={handleStart}>
          <h1>CareerMate</h1>
          <p>나의 진로를 찾아가는 대화</p>
          <label htmlFor="name-input">어떻게 불러드리면 좋을까요?</label>
          <input
            id="name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="이름 또는 호칭"
            maxLength={20}
            autoFocus
          />
          <p className="persona-title">어떤 윤이와 이야기할까요?</p>
          <div className="persona-grid" role="radiogroup" aria-label="말투">
            {PERSONAS.map((p) => (
              <button
                key={p.value}
                type="button"
                role="radio"
                aria-checked={tone === p.value}
                aria-label={p.label}
                className={`persona${tone === p.value ? ' active' : ''}`}
                onClick={() => setTone(p.value)}
              >
                <img src={p.image} alt={p.label} />
              </button>
            ))}
          </div>
          {nameError && <p className="name-error">{nameError}</p>}
          <button type="submit" disabled={!name.trim()}>
            시작하기
          </button>
        </form>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="header">
        <button type="button" className="brand brand-link" onClick={handleHome} title="첫 채팅 화면으로">
          <h1>CareerMate</h1>
          <p>나의 진로를 찾아가는 대화</p>
        </button>
        <div className="header-right">
          <span className="user-name">{displayName}님</span>
          <button
            type="button"
            className={`secondary panel-toggle${panelView === 'state' ? ' open' : ''}`}
            onClick={() => setPanelView((v) => (v === 'state' ? null : 'state'))}
            aria-pressed={panelView === 'state'}
          >
            기준·제약
          </button>
          <button
            type="button"
            className={`secondary panel-toggle${panelView === 'map' ? ' open' : ''}`}
            onClick={() => setPanelView((v) => (v === 'map' ? null : 'map'))}
            aria-pressed={panelView === 'map'}
          >
            진로맵
            {stale && <span className="dot" aria-label="추가 대화 미반영" />}
          </button>
          <button type="button" className="secondary" onClick={handleNewSession} disabled={busy}>
            새 상담
          </button>
        </div>
      </header>

      <div className="layout">
      <main className="chat">
        <div className="messages">
          {messages.map((m, idx) =>
            m.role === 'assistant' ? (
              <div key={m.id} className="row assistant">
                <Avatar />
                <div className="assistant-body">
                  <div className="bubble assistant">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  </div>
                  {/* 마지막 답변에만 예시 답변 칩을 보여준다. 누르면 입력창에 들어가고 고쳐서 보낼 수 있다 */}
                  {idx === messages.length - 1 && !pending && pendingCards && (
                    <RapportCards
                      cards={pendingCards}
                      busy={busy}
                      onChoose={handleChooseCard}
                      onReshuffle={() => handleCardsAction(reshuffleCards)}
                      onDismiss={() => handleCardsAction(dismissCards)}
                    />
                  )}
                  {idx === messages.length - 1 && !pending && !pendingCards && m.suggestions?.length > 0 && (
                    <div className="suggestions">
                      <span className="suggestions-label">예시 답변 · 누르면 입력창에 들어가요</span>
                      {m.suggestions.map((text) => (
                        <button
                          key={text}
                          type="button"
                          className="suggestion"
                          onClick={() => setInput(text)}
                          disabled={busy}
                        >
                          {text}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div key={m.id} className="bubble user">
                {m.content}
              </div>
            ),
          )}
          {pending && (
            <>
              <div className="bubble user pending">{pending.content}</div>
              <div className="row assistant">
                <Avatar />
                <div className="bubble assistant typing">답변을 작성하고 있어요…</div>
              </div>
            </>
          )}
          {cardBusy && (
            <div className="row assistant">
              <Avatar />
              <div className="bubble assistant typing">카드를 보고 있어요…</div>
            </div>
          )}
          {error && (
            <div className="bubble error">
              <p>{error.message}</p>
              {error.retry && (
                <button type="button" onClick={handleRetry}>
                  다시 시도
                </button>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                handleSubmit(e)
              }
            }}
            placeholder="메시지를 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)"
            rows={2}
            disabled={!sessionId || busy}
          />
          <button type="submit" disabled={!sessionId || busy || !input.trim()}>
            전송
          </button>
        </form>
      </main>
      {panelView && (
        <SidePanel
          view={panelView}
          state={state}
          careerMap={careerMap}
          stale={stale}
          canSummarize={Boolean(sessionId) && messages.some((m) => m.role === 'user') && !busy}
          summarizing={summarizing}
          summaryError={summaryError}
          onSummarize={handleSummarize}
          onClose={() => setPanelView(null)}
        />
      )}
      </div>
    </div>
  )
}
