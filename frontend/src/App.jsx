import { useEffect, useState } from 'react'
import {
  ApiError,
  chooseCard,
  createSession,
  getSession,
  restartRapport,
  sendMessage,
  summarize,
} from './api'
import NameScreen from './screens/NameScreen.jsx'
import ChatScreen from './screens/ChatScreen.jsx'
import './styles/app.css'

const STORAGE_KEY = 'career-chat-session-id'

// 화면 전환(이름 화면 ↔ 상담 화면)과 상담 상태를 모두 여기서 관리하고, 화면 컴포넌트에는 값과 동작만 넘긴다
export default function App() {
  // 'loading': 저장된 상담 복원 중, 'name': 이름 입력 화면, 'chat': 상담 화면(첫 목록 화면 포함)
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
  // 카드 확인·알아가기 재시작 처리 중이면 그 안내 문구 (모델 호출이 있어 전송과 겹치지 않게 막는다)
  const [cardBusy, setCardBusy] = useState(null)
  // 상담 화면 안에서 'home'(알아가기·진로 상담 목록) 또는 'chat'(고른 채팅)을 보여준다.
  const [view, setView] = useState('chat')
  // 지금 들어가 있는 채팅. 진로 상담이 시작된 뒤의 알아가기 채팅은 읽기 전용이다.
  const [room, setRoom] = useState('rapport')

  // 전송·정리·카드 선택 중 하나라도 진행 중이면 전송·정리·새 상담을 모두 막는다
  const busy = pending !== null || summarizing || Boolean(cardBusy)
  const displayName = state?.preferred_name || name
  const stale = careerMap !== null && lastMessageId > careerMap.based_on_message_id
  const hasUserMessages = messages.some((m) => m.role === 'user')
  const pendingCards =
    state?.conversation_stage === 'cards' && state?.rapport?.cards && state.rapport.cards.chosen_id === null && !state.rapport.cards.dismissed
      ? state.rapport.cards
      : null
  // 채팅 전환 기록으로 알아가기·진로 상담을 나눈다. 시작 턴 기록이 없는 이전 버전의 진로 상담은 목록 없이 한 채팅으로 보여준다.
  const careerStarted = state?.career_started_turn != null
  const inCareer = state?.conversation_stage === 'career'
  const switches =
    state?.room_switches ?? (careerStarted ? [{ room: 'career', turn: state.career_started_turn }] : [])
  const roomOf = (turn) => switches.reduce((cur, s) => (turn >= s.turn ? s.room : cur), 'rapport')
  const hasRooms = careerStarted || !inCareer
  // 진로 상담 중에는 알아가기 채팅을 비워 두고, 다시 하는 알아가기는 마지막 전환부터 보여준다.
  const rapportStart = !inCareer && switches.length > 0 ? switches[switches.length - 1].turn : 0
  // 알아가기를 다시 하는 동안 진로 상담 채팅은 읽기만 할 수 있다.
  const readOnly = room === 'career' && !inCareer
  const visibleMessages = !hasRooms
    ? messages
    : room === 'career'
      ? messages.filter((m) => roomOf(m.turn) === 'career')
      : inCareer
        ? []
        : messages.filter((m) => m.turn >= rapportStart)
  const rooms = [
    {
      key: 'rapport',
      title: '알아가기',
      description: inCareer ? '다시 하면 라포 카드가 새로 만들어져요' : '윤이와 서로 알아가는 대화',
      status: inCareer ? '완료' : '진행 중',
      locked: false,
    },
    {
      key: 'career',
      title: '진로 상담',
      description: !careerStarted
        ? '알아가기를 마치면 열려요'
        : inCareer
          ? '진로 고민을 함께 살펴보는 대화'
          : '새 카드를 확인하면 이어서 상담해요',
      status: !careerStarted ? '잠김' : inCareer ? '진행 중' : '읽기만 가능',
      locked: !careerStarted,
    },
  ]
  // 화면 위 이름 옆에 보여줄 카드. 알아가기를 다시 하는 동안에는 이전에 확인한 카드를 보여준다.
  const cards = state?.rapport?.cards
  const headerCard =
    cards?.offered?.find((c) => c.id === cards.chosen_id) ?? state?.rapport?.previous_card ?? null

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
    // 새 상담은 목록을 거치지 않고 바로 알아가기 채팅으로 들어간다.
    setRoom('rapport')
    setView('chat')
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
          // 다시 열면 알아가기·진로 상담 목록부터 보여준다. 나눌 수 없는 이전 버전 진로 상담은 바로 채팅을 연다.
          const legacyCareer = data.state.career_started_turn == null && data.state.conversation_stage === 'career'
          setView(legacyCareer ? 'chat' : 'home')
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
      setMessages((prev) => [...prev, { id: `user-${requestId}`, role: 'user', content, turn: message.turn }, message])
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
    setCardBusy('진로 상담을 준비하고 있어요…')
    setError(null)
    try {
      const { messages: added, state: newState } = await chooseCard(sessionId, cardId)
      setMessages((prev) => [...prev, ...added])
      setState(newState)
      // 다시 한 알아가기의 카드를 확인하면 새 메시지 없이 이전 진로 상담으로 돌아간다.
      if (added.length > 0) setLastMessageId(added[added.length - 1].id)
      // 카드를 확인하면 진로 상담 채팅으로 바로 들어간다.
      setRoom('career')
    } catch (e) {
      setError({ message: e.message, retry: null })
    } finally {
      setCardBusy(null)
    }
  }

  // 목록에서 채팅을 연다. 진로 상담 중에 알아가기를 고르면 새 카드가 만들어진다고 알린 뒤 알아가기를 다시 시작한다.
  async function openRoom(key) {
    if (key !== 'rapport' || !inCareer) {
      setRoom(key)
      setView('chat')
      return
    }
    if (busy || !sessionId) return
    if (!window.confirm('알아가기를 다시 하면 라포 카드가 새로 만들어져요. 다시 시작할까요?')) return
    setRoom('rapport')
    setView('chat')
    setCardBusy('알아가기를 다시 준비하고 있어요…')
    setError(null)
    try {
      const { messages: added, state: newState } = await restartRapport(sessionId)
      setMessages((prev) => [...prev, ...added])
      setState(newState)
      setLastMessageId(added[added.length - 1].id)
    } catch (e) {
      setError({ message: e.message, retry: null })
    } finally {
      setCardBusy(null)
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
      header={{ displayName, card: headerCard, busy, onHome: handleHome, onNewSession: handleNewSession }}
      nav={
        hasRooms
          ? {
              view,
              rooms,
              current: rooms.find((r) => r.key === room),
              onOpen: openRoom,
              onBack: () => setView('home'),
            }
          : null
      }
      chat={{
        messages: visibleMessages,
        pending: readOnly ? null : pending,
        cardBusy: readOnly ? null : cardBusy,
        error: readOnly ? null : error,
        busy,
        pendingCards,
        conversationStage: state?.conversation_stage,
        onRetry: handleRetry,
        onPickSuggestion: setInput,
        onChooseCard: handleChooseCard,
        cardActionLabel: careerStarted ? '진로 상담으로 돌아가기' : '진로 상담 시작하기',
        boldQuestions: hasRooms && room === 'rapport',
      }}
      composer={{
        value: input,
        onChange: setInput,
        onSubmit: handleSubmit,
        disabled: !sessionId || busy || Boolean(pendingCards) || readOnly || (room === 'rapport' && inCareer),
        placeholder: pendingCards
          ? '카드를 확인하고 진로 상담을 시작해 주세요'
          : readOnly
            ? '새 카드를 확인하면 이어서 상담할 수 있어요'
            : undefined,
      }}
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
