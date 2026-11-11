import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import cards as C  # noqa: E402


def test_deck_has_15_unique_cards():
    assert [c["id"] for c in C.DECK] == list(range(1, 16))
    assert len({c["name"] for c in C.DECK}) == 15


# 챗봇이 정하는 카드는 한 장이다. 무작위 보충도 한 장만 만들며 제외 목록을 지킨다.
def test_random_offer_excludes_current_and_gives_one_card():
    first = C.random_offer(rng=random.Random(1))
    ids = [c["id"] for c in first]
    assert len(ids) == 1
    again = C.random_offer(ids, rng=random.Random(2))
    assert not set(c["id"] for c in again) & set(ids)
    assert all(c["evidence"] is None for c in again)


def test_offer_from_picks_validates_and_fills():
    picks = [SimpleNamespace(card_id=1, evidence="시간 가는 줄 몰랐어요"), SimpleNamespace(card_id=1, evidence="중복"),
             SimpleNamespace(card_id=99, evidence="없는 카드")]
    offered = C.offer_from_picks(picks, rng=random.Random(0))
    assert len(offered) == 1
    assert offered[0]["id"] == 1 and offered[0]["evidence"] == "시간 가는 줄 몰랐어요"
    fallback = C.offer_from_picks([SimpleNamespace(card_id=99, evidence="없는 카드")], rng=random.Random(0))
    assert len(fallback) == 1 and fallback[0]["evidence"] is None


def test_pending_flags():
    cards = C.new_cards(C.random_offer(rng=random.Random(0)), "random", 3)
    assert C.pending(cards)
    cards["chosen_id"] = cards["offered"][0]["id"]
    assert not C.pending(cards)
    cards = C.new_cards(C.random_offer(rng=random.Random(0)), "random", 3)
    cards["dismissed"] = True
    assert not C.pending(cards)
    assert not C.pending(None)
