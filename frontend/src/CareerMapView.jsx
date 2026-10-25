import { STATUS_LABEL } from './StatePanel.jsx'

// 진로맵 탭: '현재까지 정리하기' 버튼과 결과(선택 기준·후보 비교·다음 탐색 행동)
export default function CareerMapView({
  careerMap,
  stale,
  canSummarize,
  summarizing,
  summaryError,
  onSummarize,
}) {
  const selected = careerMap?.next_actions.filter((a) => a.selected) ?? []
  const proposed = careerMap?.next_actions.filter((a) => !a.selected) ?? []

  return (
    <div>
      <button
        type="button"
        className={`summarize${stale ? ' stale' : ''}`}
        onClick={onSummarize}
        disabled={!canSummarize}
      >
        {summarizing ? '정리하는 중…' : careerMap ? '다시 정리하기' : '현재까지 정리하기'}
      </button>
      {stale && <p className="notice">추가 대화가 아직 반영되지 않았어요. 다시 정리하면 갱신돼요.</p>}
      {summaryError && <p className="panel-error">{summaryError}</p>}

      {!careerMap && !summaryError && (
        <p className="panel-empty">
          대화를 나눈 뒤 버튼을 누르면 지금까지의 선택 기준, 후보 비교, 다음 탐색 행동을 정리해요.
        </p>
      )}

      {careerMap && (
        <>
          <section>
            <h3>선택 기준</h3>
            {careerMap.criteria.length === 0 ? (
              <p className="panel-empty">아직 확인된 기준이 없어요.</p>
            ) : (
              <ul>
                {careerMap.criteria.map((c) => (
                  <li key={c.id} className={c.status}>
                    <span className={`badge ${c.status}`}>{STATUS_LABEL[c.status]}</span>
                    <span className="item-text">{c.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3>후보 비교</h3>
            {careerMap.candidates.length === 0 ? (
              <p className="panel-empty">아직 대화에 등장한 후보가 없어요.</p>
            ) : (
              careerMap.candidates.map((cand) => (
                <div className="candidate" key={cand.name}>
                  <div className="candidate-head">
                    <strong>{cand.name}</strong>
                    <span className={`badge source ${cand.source}`}>
                      {cand.source === 'user' ? '내가 언급' : '챗봇 소개'}
                    </span>
                  </div>
                  {careerMap.criteria.length > 0 && (
                    <ul>
                      {careerMap.criteria.map((c) => {
                        // 모델이 근거 없이 설명을 지어내지 않도록, 빠진 기준은 빈칸 대신 '추가 확인 필요'로 보여준다
                        const found = cand.by_criteria.find((n) => n.criterion_id === c.id)
                        return (
                          <li key={c.id}>
                            <span className="crit">{c.text}</span>
                            {found ? (
                              <span className="item-text">{found.note}</span>
                            ) : (
                              <span className="item-text needs-check">추가 확인 필요</span>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>
              ))
            )}
          </section>

          <section>
            <h3>내가 선택한 행동</h3>
            {selected.length === 0 ? (
              <p className="panel-empty">아직 직접 고른 행동이 없어요. 대화에서 해보고 싶은 행동을 정해 보세요.</p>
            ) : (
              <ul className="actions">
                {selected.map((a) => (
                  <li key={a.text}>{a.text}</li>
                ))}
              </ul>
            )}
          </section>

          {proposed.length > 0 && (
            <section>
              <h3>제안된 행동</h3>
              <ul className="actions proposed">
                {proposed.map((a) => (
                  <li key={a.text}>{a.text}</li>
                ))}
              </ul>
            </section>
          )}

          <p className="updated">마지막 갱신: {new Date(careerMap.updated_at).toLocaleString('ko-KR')}</p>
        </>
      )}
    </div>
  )
}
