// 이름 화면에서 고르는 말투(페르소나). 서버 llm/personas.py의 키와 같다.
// 카드 그림 안에 이름과 예시 문장이 들어 있어 화면에는 그림만 보여준다
import friend from './assets/personas/friend.webp'
import analyst from './assets/personas/analyst.webp'
import spicy from './assets/personas/spicy.webp'

export const PERSONAS = [
  { value: 'friend', label: '5년지기 친구', image: friend },
  { value: 'counselor', label: '차분한 분석가', image: analyst },
  { value: 'spicy', label: '매운맛 조언가', image: spicy },
]
