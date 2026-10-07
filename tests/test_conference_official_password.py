from app.conference.service import _default_official_password


def test_default_official_password_uses_ten_digit_national():
    assert _default_official_password("9744518492") == "9744518492"
    assert _default_official_password("+919744518492") == "9744518492"
    assert _default_official_password(" 9744518492 ") == "9744518492"
