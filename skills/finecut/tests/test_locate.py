from skills.finecut.locate import locate_phrase

WORDS = [
    {"word": "美", "start": 54.6, "end": 54.8},
    {"word": "国", "start": 54.8, "end": 55.0},
    {"word": "从", "start": 55.0, "end": 55.2},
    {"word": "61", "start": 56.7, "end": 57.0},
    {"word": "万", "start": 57.0, "end": 57.2},
    {"word": "美元", "start": 57.2, "end": 57.6},
]

def test_locates_phrase_span():
    start, end = locate_phrase(WORDS, "61万美元")
    assert abs(start - 56.7) < 1e-6
    assert abs(end - 57.6) < 1e-6

def test_missing_phrase_returns_none():
    assert locate_phrase(WORDS, "不存在的话") is None
