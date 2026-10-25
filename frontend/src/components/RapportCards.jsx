// 라포 카드 고르기: 라포가 끝나는 턴에 윤이 말풍선 아래에 3장이 뜬다. 한 장만 고른다.
// 그림 파일(assets/cards/card-<id>.webp)이 있으면 그림을, 없으면 이모지 카드를 보여준다
const images = import.meta.glob('../assets/cards/card-*.webp', { eager: true, import: 'default' })

function cardImage(id) {
  return images[`../assets/cards/card-${id}.webp`] ?? null
}

export default function RapportCards({ cards, busy, onChoose, onReshuffle, onDismiss }) {
  return (
    <div className="rapport-cards">
      <p className="cards-label">지금 가장 나다운 카드 하나를 골라 주세요</p>
      <div className="cards-grid">
        {cards.offered.map((c) => {
          const src = cardImage(c.id)
          return (
            <button key={c.id} type="button" className="rapport-card" onClick={() => onChoose(c.id)} disabled={busy}>
              {src ? (
                <img className="card-image" src={src} alt={`${c.name}: ${c.tagline}`} />
              ) : (
                <span className="card-fallback">
                  <span className="card-emoji" aria-hidden="true">
                    {c.emoji}
                  </span>
                  <strong>{c.name}</strong>
                  <span className="card-tagline">{c.tagline}</span>
                </span>
              )}
              <span className="card-meaning">{c.meaning}</span>
              {c.evidence && <span className="card-evidence">“{c.evidence}”</span>}
            </button>
          )
        })}
      </div>
      <div className="cards-actions">
        <button type="button" className="secondary" onClick={onReshuffle} disabled={busy}>
          {cards.source === 'random' ? '다른 카드 보기' : '셋 다 아닌 것 같아요'}
        </button>
        <button type="button" className="link" onClick={onDismiss} disabled={busy}>
          건너뛸게요
        </button>
      </div>
    </div>
  )
}
