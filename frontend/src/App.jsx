import { useEffect, useState } from 'react'
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
import NameScreen from './screens/NameScreen.jsx'
import ChatScreen from './screens/ChatScreen.jsx'
import './styles/app.css'

const STORAGE_KEY = 'career-chat-session-id'

// 화면 전환(이름 화면 ↔ 상담 화면)과 상담 상태를 모두 여기서 관리하고, 화면 컴포넌트에는 값과 동작만 넘긴다
export default function App() {
  // 'loading': 저장된 상담 복원 중, 'name': 이름 입력 화면, 'chat': 상담 화면
  const [screen, setScreen] = useState('loading')
  const [name, setName] = useState('')
  const [nameError, setNameError] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  // 서버가 갱신하는 상담 상태. 호칭, 대화 단계와 카드 표시 여부를 판단한다.
  const [state, setState] = useState(null)
  // 정리 결과(진로맵)와 마지막 메시지 id. 둘을 비교해 '추가 대화 미반영'을 판단한다
  const [careerMap, setCareerMap] = useState(null)
  const [lastMessageId, setLastMessageId] = useState(0)
  const [summarizing, setSummarizing] = useState(false)
  const [summaryError, setSummaryError] = useState(null)
  // 사용자가 진로맵을 열었는지 관리한다. 내부 상담 기록은 별도 화면으로 표시하지 않는다.
  const [mapOpen, setMapOpen] = useState(false)
  const [input, setInput] = useState('')
  // 전송 중인 사용자 메시지 { content, requestId }
  const [pending, setPending] = useState(null)
  // 오류 안내 { message, retry: { content, requestId } | null }
  const [error, setError] = useState(null)
  // 카드 선택 처리 중 (모델 호출이 있어 전송과 겹치지 않게 막는다)
  const [cardBusy, setCardBusy] = useState(false)

  // 전송·정리·카드 선택 중 하나라도 진행 중이면 전송·정리·새 상담을 모두 막는다
  const busy = pending !== null || summarizing || cardBusy
  const displayName = state?.preferred_name || name
  const stale = careerMap !== null && lastMessageId > careerMap.based_on_message_id
  const hasUserMessages = messages.some((m) => m.role === 'user')
  const pendingCards =
    state?.conversation_stage === 'cards' && state?.rapport?.cards && state.rapport.cards.chosen_id === null && !state.rapport.cards.dismissed
      ? state.rapport.cards
      : null

  async function startNewSession(preferredName) {
    // 서버가 윤이의 인사를 첫 메시지로 넣어 돌려준다
    const { session_id, state: newState, message } = await createSession(preferredName)
    localStorage.setItem(STORAGE_KEY, session_id)
    setSessionId(session_id)
    setMessages([message])
    setState(newState)
    setCareerMap(null)
    setMapOpen(false)
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
      await startNewSession(trimmed)
    } catch (err) {
      setNameError(err.message)
    }
  }

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

  // 다시 뽑기는 카드만 갱신하고, 건너뛰기는 서버가 돌려준 상담 시작 메시지까지 함께 반영한다.
  async function handleCardsAction(action) {
    if (busy || !sessionId) return
    setCardBusy(true)
    setError(null)
    try {
      const { state: newState, messages: added = [] } = await action(sessionId)
      setState(newState)
      if (added.length > 0) {
        setMessages((prev) => [...prev, ...added])
        setLastMessageId(added[added.length - 1].id)
      }
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

  // CareerMate 제목 클릭: 이름 화면을 거치지 않고 같은 이름으로 첫 채팅 화면으로 간다
  async function handleHome() {
    if (busy) return
    setMapOpen(false)
    const currentName = (state?.preferred_name || name).trim()
    if (!currentName || !hasUserMessages) return
    if (!window.confirm('새 대화를 시작할까요? 이름은 그대로 유지되고, 현재 상담은 이 브라우저에서 더 이상 열 수 없습니다.')) return
    try {
      await startNewSession(currentName)
    } catch (e) {
      setError({ message: e.message, retry: null })
    }
  }

  function handleNewSession() {
    if (busy) return
    if (hasUserMessages && !window.confirm('새 상담을 시작할까요? 현재 상담은 이 브라우저에서 더 이상 열 수 없습니다.')) return
    // 이름 화면으로 돌아간다. 이전 이름은 입력창에 남겨 둔다
    localStorage.removeItem(STORAGE_KEY)
    setSessionId(null)
    setMessages([])
    setState(null)
    setCareerMap(null)
    setMapOpen(false)
    setLastMessageId(0)
    setError(null)
    setNameError(null)
    setScreen('name')
  }

  if (screen === 'loading') {
    return <div className="app" />
  }

  if (screen === 'name') {
    return (
      <NameScreen
        name={name}
        onNameChange={setName}
        error={nameError}
        onStart={handleStart}
      />
    )
  }

  return (
    <ChatScreen
      header={{ displayName, busy, onHome: handleHome, onNewSession: handleNewSession }}
      chat={{
        messages,
        pending,
        cardBusy,
        error,
        busy,
        pendingCards,
        conversationStage: state?.conversation_stage,
        onRetry: handleRetry,
        onPickSuggestion: setInput,
        onChooseCard: handleChooseCard,
        onReshuffle: () => handleCardsAction(reshuffleCards),
        onDismiss: () => handleCardsAction(dismissCards),
      }}
      composer={{ value: input, onChange: setInput, onSubmit: handleSubmit, disabled: !sessionId || busy }}
      panel={{
        open: mapOpen,
        onToggle: () => setMapOpen((cur) => !cur),
        onClose: () => setMapOpen(false),
        careerMap,
        stale,
        canSummarize: Boolean(sessionId) && hasUserMessages && !busy,
        summarizing,
        summaryError,
        onSummarize: handleSummarize,
      }}
    />
  )
}
