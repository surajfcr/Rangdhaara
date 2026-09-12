"""A picture puzzle on the signup form, so bots can't mass-create accounts.

Signup is the one place a stranger can make the studio's mailbox send email, and a
free Gmail account is cut off for the day after a few hundred messages. Rate limits
alone only slow that down; this stops the automated case outright.

The answer never reaches the browser — only its keyed hash is stored — and each
challenge is single-use, so solving one image once doesn't buy a bot a second signup.
It's drawn as pixels rather than SVG text on purpose: text in the page could simply
be read back out of the markup, which would leave the puzzle there for show only.
"""
import base64
import hmac
import io
import random
import secrets
from datetime import timedelta

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import settings
from .db import iso, one, utcnow

# No O/0, I/1/L or S/5: telling them apart in a wobbly image is a puzzle about eyesight, not humanity.
ALPHABET = "ABCDEFGHJKMNPQRTUVWXYZ23456789"
LENGTH = 5
TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5

WIDTH, HEIGHT = 220, 70
_BACKGROUND = (250, 246, 239)
# All four sit well clear of the background: a puzzle nobody can read is just a locked door.
_INKS = ((31, 36, 33), (140, 60, 38), (62, 70, 65), (99, 72, 20))
# Same idea as the certificate fonts: probe the usual places, and fall back to one Pillow carries.
_FONT_PATHS = (
    "C:/Windows/Fonts/georgiab.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _font(size: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _answer_hash(challenge_id: str, answer: str) -> str:
    return hmac.new(settings.secret_key.encode(), f"captcha|{challenge_id}|{answer}".encode(), "sha256").hexdigest()


def normalize(answer: str | None) -> str:
    return "".join((answer or "").split()).upper()


def _image(text: str, rng: random.Random) -> str:
    """Each glyph drawn, rotated and pasted on its own, then speckled and smudged."""
    canvas = Image.new("RGB", (WIDTH, HEIGHT), _BACKGROUND)
    speckle = ImageDraw.Draw(canvas)
    for _ in range(320):
        x, y = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        speckle.point((x, y), fill=rng.choice(_INKS))

    step = (WIDTH - 40) / len(text)
    for i, char in enumerate(text):
        size = rng.randint(38, 48)
        glyph = Image.new("RGBA", (size + 24, size + 24), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((12, 6), char, font=_font(size), fill=(*rng.choice(_INKS), 255))
        glyph = glyph.rotate(rng.uniform(-30, 30), resample=Image.BICUBIC, expand=False)
        canvas.paste(glyph, (int(20 + i * step + rng.randint(-4, 4)), rng.randint(-6, 8)), glyph)

    # Strokes in the same inks as the letters, so foreground and background can't be split by colour.
    over = ImageDraw.Draw(canvas)
    for _ in range(3):
        points = [(x, rng.randrange(HEIGHT)) for x in range(0, WIDTH + 1, WIDTH // 4)]
        over.line(points, fill=rng.choice(_INKS), width=rng.randint(1, 2), joint="curve")
    for _ in range(8):
        x, y = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        over.arc([x, y, x + rng.randint(20, 60), y + rng.randint(20, 50)], rng.randrange(360), rng.randrange(360),
                 fill=rng.choice(_INKS))

    canvas = canvas.filter(ImageFilter.SMOOTH)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def issue(conn) -> dict:
    """Create a challenge and return the image to show. The answer stays here."""
    challenge_id = secrets.token_urlsafe(18)
    rng = random.SystemRandom()
    answer = "".join(rng.choice(ALPHABET) for _ in range(LENGTH))
    conn.execute(
        "INSERT INTO captcha_challenges (id, answer_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (challenge_id, _answer_hash(challenge_id, answer), iso(utcnow() + TTL), iso()),
    )
    return {"captcha_id": challenge_id, "image": _image(answer, rng), "length": LENGTH}


def verify(conn, challenge_id: str | None, answer: str | None) -> bool:
    """True only once per challenge: a correct answer consumes it."""
    row = one(conn, "SELECT * FROM captcha_challenges WHERE id = ?", (challenge_id or "",))
    if not row or row["consumed_at"] or row["attempts"] >= MAX_ATTEMPTS or row["expires_at"] <= iso():
        return False
    conn.execute("UPDATE captcha_challenges SET attempts = attempts + 1 WHERE id = ?", (challenge_id,))
    if not hmac.compare_digest(row["answer_hash"], _answer_hash(challenge_id, normalize(answer))):
        return False
    conn.execute("UPDATE captcha_challenges SET consumed_at = ? WHERE id = ?", (iso(), challenge_id))
    return True


def purge(conn) -> None:
    conn.execute("DELETE FROM captcha_challenges WHERE expires_at < ?", (iso(utcnow() - timedelta(hours=1)),))
