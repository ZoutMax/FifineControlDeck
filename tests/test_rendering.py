"""Key rendering: colour parsing, image size, press flash, device JPEG encoding."""
from PIL import Image, ImageStat

from fifine_deck import rendering


def _mean_luma(img):
    return ImageStat.Stat(img.convert("L")).mean[0]


def test_hex_parsing():
    assert rendering._hex("#ff0000") == (255, 0, 0)
    assert rendering._hex("#f00") == (255, 0, 0)        # 3-digit expands
    assert rendering._hex("not-a-color") == (16, 16, 32)  # fallback


def test_render_key_dimensions():
    img = rendering.render_key(100, label="Test", bg_color="#202040")
    assert img.size == (100, 100)
    assert img.mode == "RGB"


def test_render_key_pressed_is_brighter():
    normal = rendering.render_key(80, bg_color="#404040")
    pressed = rendering.render_key(80, bg_color="#404040", pressed=True)
    assert _mean_luma(pressed) > _mean_luma(normal)


def test_to_device_jpeg_returns_jpeg():
    img = Image.new("RGB", (100, 100), (10, 20, 30))
    data = rendering.to_device_jpeg(img, rotation=180, flip=(True, False))
    assert isinstance(data, bytes)
    assert data[:2] == b"\xff\xd8"   # JPEG start-of-image marker
