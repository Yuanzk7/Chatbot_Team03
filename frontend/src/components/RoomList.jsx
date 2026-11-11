// 첫 목록 화면: 알아가기와 진로 상담 채팅 중 하나를 골라 들어간다. 진로 상담은 카드를 확인하기 전까지 잠겨 있다.
export default function RoomList({ rooms, onOpen }) {
  return (
    <div className="room-list">
      {rooms.map((r) => (
        <button
          key={r.key}
          type="button"
          className={`room${r.locked ? ' locked' : ''}`}
          onClick={() => onOpen(r.key)}
          disabled={r.locked}
        >
          <span className="room-head">
            <strong className="room-title">{r.title}</strong>
            <span className="room-status">{r.status}</span>
          </span>
          <span className="room-desc">{r.description}</span>
        </button>
      ))}
    </div>
  )
}
