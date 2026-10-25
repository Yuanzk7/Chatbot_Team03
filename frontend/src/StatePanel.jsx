// 기준·제약 탭: 현재까지 확인한 경험·기준·제약. 읽기 전용이며 정정은 대화로만 한다.
const SECTIONS = [
  { key: 'experiences', label: '경험·관심 활동' },
  { key: 'criteria', label: '선택 기준' },
  { key: 'constraints', label: '현실적 제약' },
]

export const STATUS_LABEL = { confirmed: '확인됨', unconfirmed: '미확인', withdrawn: '철회' }

export default function StatePanel({ state }) {
  const hasAnything =
    state && (state.preferred_name || SECTIONS.some(({ key }) => state[key].length > 0))

  if (!hasAnything) {
    return <p className="panel-empty">대화를 나누면 확인된 경험, 선택 기준, 제약이 여기에 정리돼요.</p>
  }

  return (
    <div>
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
