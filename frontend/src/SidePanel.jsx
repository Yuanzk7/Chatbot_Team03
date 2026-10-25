import StatePanel from './StatePanel.jsx'
import CareerMapView from './CareerMapView.jsx'

// 우측 패널. 헤더 버튼으로 '기준·제약' 또는 '진로맵' 중 하나를 연다
export default function SidePanel({ view, state, careerMap, stale, onClose, ...mapProps }) {
  return (
    <aside className="panel">
      <div className="panel-head">
        <h2>{view === 'state' ? '기준·제약' : '진로맵'}</h2>
        <button type="button" className="panel-close" onClick={onClose} aria-label="패널 닫기">
          ×
        </button>
      </div>
      {view === 'state' ? (
        <StatePanel state={state} />
      ) : (
        <CareerMapView careerMap={careerMap} stale={stale} {...mapProps} />
      )}
    </aside>
  )
}
