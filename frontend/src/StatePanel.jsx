// 기준·제약 탭: 현재까지 확인한 경험·기준·제약. 읽기 전용이며 정정은 대화로만 한다.
const SECTIONS = [
  { key: 'experiences', label: '경험·관심 활동' },
  { key: 'criteria', label: '선택 기준' },
  { key: 'constraints', label: '현실적 제약' },
]

export const STATUS_LABEL = { confirmed: '확인됨', unconfirmed: '미확인', withdrawn: '철회' }

const RAPPORT_LABEL = { pending: '남음', asked: '물어봄', answered: '답함', skipped: '건너뜀' }

// 서로 알아가기(라포 질문) 진행 상황
function RapportSection({ rapport }) {
  if (!rapport || (rapport.questions.length === 0 && !rapport.done)) return null
  const resolved = rapport.questions.filter((q) => q.status === 'answered' || q.status === 'skipped').length
  return (
    <section className="rapport">
      <h3>
        서로 알아가기
        {rapport.questions.length > 0 && (
          <span className="rapport-count">
            {resolved} / {rapport.questions.length}
          </span>
        )}
      </h3>
      {rapport.done && (
        <p className="panel-empty">
          {rapport.end_reason === 'user_requested' ? '고민 이야기로 바로 넘어갔어요.' : '알아가기를 마치고 진로 탐색으로 넘어갔어요.'}
        </p>
      )}
      {rapport.cards?.chosen_id && (
        <p className="chosen-card">
          중심 카드: {rapport.cards.offered.find((c) => c.id === rapport.cards.chosen_id)?.name}
        </p>
      )}
      {rapport.questions.length > 0 && (
        <ul>
          {rapport.questions.map((q) => (
            <li key={q.id} className={q.status === 'pending' ? 'unconfirmed' : ''}>
              <span className={`badge rapport-${q.status}`}>{RAPPORT_LABEL[q.status]}</span>
              <span className="item-text">{q.text}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default function StatePanel({ state }) {
  const hasRapport = state?.rapport && (state.rapport.questions.length > 0 || state.rapport.done)
  const hasAnything =
    state && (hasRapport || SECTIONS.some(({ key }) => state[key].length > 0))

  if (!hasAnything) {
    return <p className="panel-empty">대화를 나누면 확인된 경험, 선택 기준, 제약이 여기에 정리돼요.</p>
  }

  return (
    <div>
      <RapportSection rapport={state.rapport} />
      {state.preferred_name && (
        <section>
          <h3>호칭</h3>
          <p>{state.preferred_name}</p>
        </section>
      )}

      {SECTIONS.map(({ key, label }) => {
        const active = state[key].filter((i) => i.status !== 'withdrawn')
        if (active.length === 0) return null
        return (
          <section key={key}>
            <h3>{label}</h3>
            <ul>
              {active.map((item) => (
                <li key={item.id} className={item.status}>
                  <span className={`badge ${item.status}`}>{STATUS_LABEL[item.status]}</span>
                  <span className="item-text">{item.text}</span>
                  <span className="evidence">“{item.evidence}”</span>
                </li>
              ))}
            </ul>
          </section>
        )
      })}

      {state.corrections.length > 0 && (
        <section>
          <h3>정정 내역</h3>
          <ul className="corrections">
            {state.corrections.map((c, idx) => (
              // 과거 기록은 그 시점의 값(after.text)을 쓴다. 현재 항목 이름을 쓰면 이후 수정이 과거 기록까지 바꾼다
              <li key={idx}>
                <span className="turn">{c.turn}턴</span>
                <span className="item-text">
                  {c.before.text !== c.after.text
                    ? `“${c.before.text}” → “${c.after.text}”`
                    : `“${c.after.text}”`}
                  {c.before.status !== c.after.status &&
                    ` ${STATUS_LABEL[c.before.status]} → ${STATUS_LABEL[c.after.status]}`}
                </span>
                <span className="evidence">“{c.evidence}”</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
