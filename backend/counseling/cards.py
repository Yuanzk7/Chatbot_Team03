"""라포 카드: 고정 덱 15장. 라포가 끝나는 턴에 3장을 제시하고 사용자가 한 장을 고르면 확인된 기준이 된다."""
import random

MAX_OFFERED = 3


def _c(i, emoji, name, tagline, meaning, criterion):
    return {"id": i, "emoji": emoji, "name": name, "tagline": tagline, "meaning": meaning, "criterion": criterion}


# tagline: 카드 그림에 들어간 한 줄 (사용자의 말). meaning: 카드 아래에 보여줄 설명. criterion: 상태에 들어갈 기준 문장
DECK = [
    _c(1, "⏳", "시간순삭", "하다 보면 시간이 순삭돼요",
       "하는 과정 자체가 재미있어서 자연스럽게 몰입할 수 있는 일을 찾고 싶어요.", "재미있어서 자연스럽게 몰입하는 일"),
    _c(2, "📈", "레벨업 중", "배울수록 실력이 느는 게 좋아요",
       "새로운 지식과 기술을 배우면서 내 실력이 늘어나는 일이 좋아요.", "계속 배우고 실력이 느는 일"),
    _c(3, "⛏️", "한 우물 장인", "한 분야를 깊이 파고 싶어요",
       "관심 있는 분야를 오래 파고들어 그 분야의 전문가가 되고 싶어요.", "한 분야를 깊이 파고드는 전문성"),
    _c(4, "🔧", "뚝딱 메이커", "직접 만들어 보는 게 좋아요",
       "아이디어를 코드, 제품, 작품처럼 실제 결과물로 만드는 일이 좋아요.", "아이디어를 실제 결과물로 만드는 일"),
    _c(5, "🔍", "물음표 탐정", "원인을 찾아내는 게 즐거워요",
       "문제가 생긴 원인을 분석하고 해결 방법을 찾아낼 때 즐거워요.", "원인을 분석하고 해결 방법을 찾는 일"),
    _c(6, "🧭", "새 길 탐험가", "낯선 길에 뛰어들고 싶어요",
       "낯선 과제나 환경을 경험하며 새로운 일에 도전하고 싶어요.", "새로운 경험과 도전"),
    _c(7, "⛺", "든든한 베이스캠프", "안정적인 환경이 든든해요",
       "앞으로의 일과 생활을 계획할 수 있는 안정적이고 예측 가능한 환경을 원해요.", "안정적이고 예측 가능한 환경"),
    _c(8, "🚗", "내 인생 운전석", "방향은 내가 정하고 싶어요",
       "일하는 방법과 진행 방향을 스스로 정할 수 있는 환경을 원해요.", "방향과 방법을 스스로 정하는 자율성"),
    _c(9, "🤝", "함께라서 가능", "같이 할 때 더 힘이 나요",
       "사람들과 의견을 나누고 서로 도우면서 공동의 목표를 이루는 일이 좋아요.", "함께 협력해 공동의 목표를 이루는 일"),
    _c(10, "🎧", "나만의 몰입존", "혼자 집중할 시간이 필요해요",
       "방해받지 않고 혼자 생각하며 집중할 수 있는 시간이 필요해요.", "혼자 깊이 집중할 수 있는 환경"),
    _c(11, "🌍", "세상에 플러스", "누군가에게 도움이 되고 싶어요",
       "내가 하는 일이 다른 사람의 어려움이나 사회 문제를 해결하는 데 도움이 되면 좋겠어요.", "다른 사람과 사회에 도움이 되는 일"),
    _c(12, "🏆", "해냈다, 이 맛!", "성과가 보일 때 보람을 느껴요",
       "노력한 결과가 구체적인 성과로 드러날 때 보람을 느껴요.", "구체적인 성과에서 느끼는 보람"),
    _c(13, "💰", "통장도 소중해", "충분한 수입도 중요해요",
       "원하는 생활을 꾸릴 수 있을 만큼 충분한 수입을 얻는 것이 중요해요.", "원하는 생활을 받쳐줄 충분한 수입"),
    _c(14, "🌇", "퇴근 후에도 주인공", "일 밖의 시간도 지키고 싶어요",
       "일을 하면서도 휴식, 취미, 소중한 사람들과 보내는 시간을 확보하고 싶어요.", "일 밖의 휴식·취미·관계 시간을 지킬 수 있는 조건"),
    _c(15, "🐢", "마이페이스", "내 속도로 가고 싶어요",
       "진학이나 취업을 내 준비 상황과 속도에 맞춰 결정하고 싶어요.", "내 준비 상황과 속도에 맞춰 결정하기"),
]
DECK_BY_ID = {c["id"]: c for c in DECK}


def deck_text():
    """모델에게 보여줄 카드 목록."""
    return "\n".join(f"- {c['id']}. {c['name']}: {c['meaning']}" for c in DECK)


def _offered(card, evidence=None):
    return {**card, "evidence": evidence}


def random_offer(exclude_ids=(), rng=random):
    pool = [c for c in DECK if c["id"] not in set(exclude_ids)]
    return [_offered(c) for c in rng.sample(pool, MAX_OFFERED)]


def offer_from_picks(picks, rng=random):
    """모델이 고른 (card_id, evidence) 목록을 검증한다. 덱에 없는 id·중복은 버리고, 3장이 안 되면 무작위로 채운다."""
    offered, seen = [], set()
    for p in picks:
        card = DECK_BY_ID.get(p.card_id)
        if card and card["id"] not in seen and len(offered) < MAX_OFFERED:
            seen.add(card["id"])
            offered.append(_offered(card, (p.evidence or "").strip() or None))
    if len(offered) < MAX_OFFERED:
        offered += random_offer(seen, rng)[: MAX_OFFERED - len(offered)]
    return offered


def new_cards(offered, source, turn):
    return {"offered": offered, "source": source, "chosen_id": None, "dismissed": False, "turn": turn}


def pending(cards):
    """아직 고르지도, 건너뛰지도 않은 카드 제시가 있는가."""
    return bool(cards) and cards["chosen_id"] is None and not cards["dismissed"]
