import CareerMapView from './CareerMapView.jsx'

// 진로맵 전용 패널. 대화 정리 결과와 갱신·닫기 동작을 제공한다.
export default function SidePanel({ careerMap, stale, onClose, ...mapProps }) {
  return (
    <aside id="career-map-panel" className="panel" aria-labelledby="career-map-title">
      <div className="panel-head">
        <h2 id="career-map-title">진로맵</h2>
        <button type="button" className="panel-close" onClick={onClose} aria-label="패널 닫기">
          ×
        </button>
      </div>
      <CareerMapView careerMap={careerMap} stale={stale} {...mapProps} />
    </aside>
  )
}
