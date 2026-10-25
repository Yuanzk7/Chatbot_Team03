import { PERSONAS } from '../personas.js'

// 시작 화면: 이름(호칭)과 윤이의 말투를 고른다
export default function NameScreen({ name, onNameChange, tone, onToneChange, error, onStart }) {
  return (
    <div className="app">
      <form className="name-screen" onSubmit={onStart}>
        <h1>CareerMate</h1>
        <p>나의 진로를 찾아가는 대화</p>
        <label htmlFor="name-input">어떻게 불러드리면 좋을까요?</label>
        <input
          id="name-input"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="이름 또는 호칭"
          maxLength={20}
          autoFocus
        />
        <p className="persona-title">어떤 윤이와 이야기할까요?</p>
        <div className="persona-grid" role="radiogroup" aria-label="말투">
          {PERSONAS.map((p) => (
            <button
              key={p.value}
              type="button"
              role="radio"
              aria-checked={tone === p.value}
              aria-label={p.label}
              className={`persona${tone === p.value ? ' active' : ''}`}
              onClick={() => onToneChange(p.value)}
            >
              <img src={p.image} alt={p.label} />
            </button>
          ))}
        </div>
        {error && <p className="name-error">{error}</p>}
        <button type="submit" disabled={!name.trim()}>
          시작하기
        </button>
      </form>
    </div>
  )
}
