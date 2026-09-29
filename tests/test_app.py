from app import handle


def test_root():
    assert handle("/") == (200, {"service": "ai-factory-sandbox"})


def test_unknown_path():
    assert handle("/nope")[0] == 404
