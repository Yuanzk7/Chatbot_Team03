import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ApiError, createSession, getSession, sendMessage, summarize } from './api'
import SidePanel from './SidePanel.jsx'
import yuni from './assets/yuni.png'
import './App.css'

const STORAGE_KEY = 'career-chat-session-id'

// 첫 화면 예시 문장. 특정 직업·분야를 암시하지 않는 중립적인 표현만 쓴다 (성급한 추천 제한 원칙).
const STARTER_PROMPTS = [
  '무엇을 하고 싶은지 모르겠어요',
  '여러 진로 사이에서 고민하고 있어요',
  '관심 있는 분야를 더 알아보고 싶어요',
]

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  // 현재까지 확인한 기준·제약 (서버가 매 턴 갱신해 돌려준다)
  const [state, setState] = useState(null)
  // 정리 결과(진로맵)와 마지막 메시지 id. 둘을 비교해 '추가 대화 미반영'을 판단한다
  const [careerMap, setCareerMap] = useState(null)
  const [lastMessageId, setLastMessageId] = useState(0)
  const [summarizing, setSummarizing] = useState(false)
  const [summaryError, setSummaryError] = useState(null)
  const [input, setInput] = useState('')
  // 전송 중인 사용자 메시지 { content, requestId }
  const [pending, setPending] = useState(null)
  // 오류 안내 { message, retry: { content, requestId } | null }
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)

  async function startNewSession() {
    const { session_id } = await createSession()
    localStorage.setItem(STORAGE_KEY, session_id)
    setSessionId(session_id)
    setMessages([])
    setState(null)
    setCareerMap(null)
    setLastMessageId(0)
    setSummaryError(null)
    setPending(null)
    setError(null)
    setInput('')
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
          return
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) {
            setError({ message: e.message, retry: null })
            return
          }
          // 서버에 없는 상담 ID면 새로 발급받는다
        }
      }
      try {
        await startNewSession()
      } catch (e) {
        setError({ message: e.message, retry: null })
      }
    }
    restore()
  }, [])

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

  // 전송 또는 정리 요청이 진행 중이면 전송·정리·새 상담을 모두 막는다
  const busy = pending !== null || summarizing

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

  async function handleNewSession() {
    if (busy) return
    if (messages.length > 0 && !window.confirm('새 상담을 시작할까요? 현재 상담은 이 브라우저에서 더 이상 열 수 없습니다.')) return
    try {
      await startNewSession()
    } catch (e) {
      setError({ message: e.message, retry: null })
    }
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <h1>CareerMate</h1>
          <p>나의 진로를 찾아가는 대화</p>
        </div>
        <button type="button" className="secondary" onClick={handleNewSession} disabled={busy}>
          새 상담
        </button>
      </header>

      <div className="layout">
      <main className="chat">
        <div className="messages">
          {messages.length === 0 && !pending && !error && (
            <div className="welcome">
              <div className="mascot" aria-hidden="true">
                <img src={yuni} alt="" />
                <span className="q q1">?</span>
                <span className="q q2">?</span>
                <span className="q q3">?</span>
              </div>
              <div className="welcome-text">
                <h2>진로 고민, 어디서부터 시작할까요?</h2>
                <p>대화를 통해 나의 선택 기준과 다음 탐색 행동을 함께 정리해요.</p>
                <div className="starters">
                  {STARTER_PROMPTS.map((text) => (
                    <button
                      key={text}
                      type="button"
                      className="starter"
                      onClick={() => setInput(text)}
                      disabled={!sessionId}
                    >
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`bubble ${m.role}`}>
              {m.role === 'assistant' ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              ) : (
                m.content
              )}
            </div>
          ))}
          {pending && (
            <>
              <div className="bubble user pending">{pending.content}</div>
              <div className="bubble assistant typing">답변을 작성하고 있어요…</div>
            </>
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
      <SidePanel
        state={state}
        careerMap={careerMap}
        stale={careerMap !== null && lastMessageId > careerMap.based_on_message_id}
        canSummarize={Boolean(sessionId) && messages.length > 0 && !busy}
        summarizing={summarizing}
        summaryError={summaryError}
        onSummarize={handleSummarize}
      />
      </div>
    </div>
  )
}
