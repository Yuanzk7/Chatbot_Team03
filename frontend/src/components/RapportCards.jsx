// 라포 카드: 알아가기가 끝나면 윤이가 대화를 보고 고른 카드 한 장을 보여주고, 진로 상담을 시작하는 버튼을 둔다.
// 그림 파일(assets/cards/card-<id>.webp)이 있으면 그림을, 없으면 이모지 카드를 보여준다
const images = import.meta.glob('../assets/cards/card-*.webp', { eager: true, import: 'default' })

function cardImage(id) {
  return images[`../assets/cards/card-${id}.webp`] ?? null
}

export default function RapportCards({ cards, busy, onStart }) {
  const card = cards.offered[0]
  const src = cardImage(card.id)
  return (
    <div className="rapport-cards">
      <p className="cards-label">윤이가 대화를 보고 고른 카드예요</p>
      <div className="rapport-card">
        {src ? (
          <img className="card-image" src={src} alt={`${card.name}: ${card.tagline}`} />
        ) : (
          <span className="card-fallback">
            <span className="card-emoji" aria-hidden="true">
              {card.emoji}
            </span>
            <strong>{card.name}</strong>
            <span className="card-tagline">{card.tagline}</span>
          </span>
        )}
        <span className="card-meaning">{card.meaning}</span>
        {card.evidence && <span className="card-evidence">“{card.evidence}”</span>}
      </div>
      <div className="cards-actions">
        <button type="button" onClick={() => onStart(card.id)} disabled={busy}>
          진로 상담 시작하기
        </button>
      </div>
    </div>
  )
}
