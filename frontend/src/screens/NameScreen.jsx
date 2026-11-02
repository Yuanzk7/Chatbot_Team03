// 시작 화면: 이름(호칭)을 입력한다
export default function NameScreen({ name, onNameChange, error, onStart }) {
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
        {error && <p className="name-error">{error}</p>}
        <button type="submit" disabled={!name.trim()}>
          시작하기
        </button>
      </form>
    </div>
  )
}
