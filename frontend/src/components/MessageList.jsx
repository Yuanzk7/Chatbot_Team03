import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import RapportCards from './RapportCards.jsx'
import yuni from '../assets/yuni.png'

// 챗봇 말풍선 옆 프로필: 동그라미 안에 윤이 얼굴
function Avatar() {
  return (
    <div className="avatar" aria-hidden="true">
      <img src={yuni} alt="" />
    </div>
  )
}

// 마지막 답변 아래에 카드 또는 후보를 표시한다. 자유응답 라포·카드 단계에서는 후보 답변을 숨긴다.
export default function MessageList({
  messages,
  pending,
  cardBusy,
  error,
  busy,
  pendingCards,
  conversationStage,
  onRetry,
  onPickSuggestion,
  onChooseCard,
}) {
  const bottomRef = useRef(null)
  const showSuggestions = conversationStage === 'intro' || conversationStage === 'career'

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, pending, error])

  return (
    <div className="messages">
      {messages.map((m, idx) =>
        m.role === 'assistant' ? (
          <div key={m.id} className="row assistant">
            <Avatar />
            <div className="assistant-body">
              <div className="bubble assistant">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              </div>
              {idx === messages.length - 1 && !pending && pendingCards && (
                <RapportCards cards={pendingCards} busy={busy} onStart={onChooseCard} />
              )}
              {/* 마지막 답변에만 예시 답변 칩을 보여준다. 누르면 입력창에 들어가고 고쳐서 보낼 수 있다 */}
              {idx === messages.length - 1 && !pending && !pendingCards && showSuggestions && m.suggestions?.length > 0 && (
                <div className="suggestions">
                  <span className="suggestions-label">예시 답변 · 누르면 입력창에 들어가요</span>
                  {m.suggestions.map((text) => (
                    <button
                      key={text}
                      type="button"
                      className="suggestion"
                      onClick={() => onPickSuggestion(text)}
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
          <div className="bubble assistant typing">진로 상담을 준비하고 있어요…</div>
        </div>
      )}
      {error && (
        <div className="bubble error">
          <p>{error.message}</p>
          {error.retry && (
            <button type="button" onClick={onRetry}>
              다시 시도
            </button>
          )}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
