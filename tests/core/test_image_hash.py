import io

from PIL import Image, ImageDraw

from news_aggregator.core.image_hash import dhash, hamming_distance


def _screenshot(text: str, size: tuple[int, int] = (725, 430)) -> Image.Image:
    """Похоже на скриншот поста: светлый пузырь с тёмными строками на фоне."""
    image = Image.new("RGB", size, (150, 190, 140))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 20, size[0] - 30, size[1] - 20), radius=20, fill=(255, 255, 255))
    for line, word in enumerate(text.split()):
        y = 60 + line * 45
        width = 80 + (sum(map(ord, word)) % 400)
        draw.rectangle((70, y, 70 + width, y + 22), fill=(20, 20, 20))
    return image


def _jpeg(image: Image.Image, quality: int = 87) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def test_same_file_gives_same_hash() -> None:
    data = _jpeg(_screenshot("Несправедливо что директорам не присылают шпионок"))

    assert dhash(data) == dhash(data)
    assert dhash(data).bit_length() <= 256


def test_reupload_of_same_picture_stays_close() -> None:
    original = _screenshot("Несправедливо что директорам не присылают шпионок")
    reuploaded = original.resize((640, 380))

    distance = hamming_distance(dhash(_jpeg(original)), dhash(_jpeg(reuploaded, quality=70)))

    assert distance <= 20


def test_different_pictures_are_far_apart() -> None:
    first = dhash(_jpeg(_screenshot("Несправедливо что директорам не присылают шпионок")))
    second = dhash(_jpeg(_screenshot("Вышла новая версия ядра Linux с поддержкой Rust")))

    assert hamming_distance(first, second) > 20


def test_hamming_distance_counts_differing_bits() -> None:
    assert hamming_distance(0b1011, 0b0001) == 2
    assert hamming_distance(5, 5) == 0
