import { useState } from 'react'
import StatePanel from './StatePanel.jsx'
import CareerMapView from './CareerMapView.jsx'

export default function SidePanel({ state, careerMap, stale, ...mapProps }) {
  const [tab, setTab] = useState('state')

  return (
    <aside className="panel">
      <div className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'state'}
          className={`tab${tab === 'state' ? ' active' : ''}`}
          onClick={() => setTab('state')}
        >
          기준·제약
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'map'}
          className={`tab${tab === 'map' ? ' active' : ''}`}
          onClick={() => setTab('map')}
        >
          진로맵
          {stale && <span className="dot" aria-label="추가 대화 미반영" />}
        </button>
      </div>
      {tab === 'state' ? (
        <StatePanel state={state} />
      ) : (
        <CareerMapView careerMap={careerMap} stale={stale} {...mapProps} />
      )}
    </aside>
  )
}
