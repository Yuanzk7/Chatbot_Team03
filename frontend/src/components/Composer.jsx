// 입력창. Enter 전송, Shift+Enter 줄바꿈, 한글 조합 중 Enter는 무시
export default function Composer({ value, onChange, onSubmit, disabled }) {
  return (
    <form className="composer" onSubmit={onSubmit}>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            onSubmit(e)
          }
        }}
        placeholder="메시지를 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)"
        rows={2}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        전송
      </button>
    </form>
  )
}
