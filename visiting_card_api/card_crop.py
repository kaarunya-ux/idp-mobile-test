"""Find a visiting card lying on a wooden desk and crop the photo to it (Pillow only).

Desk pixels are orange (HSV hue 8-35 of 255) with saturation >= 50; measured on the test set,
wood is hue 16-20 with saturation 118-184, while cards are low-saturation (white, grey, olive,
charcoal: 13-73) or a different hue (blue ~147). Everything that is not desk is a candidate.
Desk glare is low-saturation too, but it runs off the edge of the photo and the card does not.
When glare touches a card corner the two merge into one region; eroding the mask harder cuts
that bridge, and the card's box is grown back by the erosion radius.
The card is the largest candidate that is a filled, card-shaped region away from the photo edge.
When nothing qualifies the caller sends the whole photo, so a failed crop costs speed, never fields.
On the 16 test photos: 15 cropped correctly, 1 (a creased card merged with glare) not cropped.
"""

from collections import deque

from PIL import Image, ImageFilter

THUMB = 240                    # detection runs on a thumbnail this wide; ~10-20 ms in pure Python
EROSIONS = (1, 5, 9, 13, 17, 21)
PAD = 0.05                     # margin added around the card, as a fraction of the card's size


def _regions(mask: Image.Image):
    """Yield (area, x0, y0, x1, y1) for each 4-connected white region of an L-mode mask."""
    tw, th = mask.size
    px = mask.load()
    seen = bytearray(tw * th)
    for y in range(th):
        for x in range(tw):
            if not px[x, y] or seen[y * tw + x]:
                continue
            seen[y * tw + x] = 1
            queue = deque([(x, y)])
            area, x0, y0, x1, y1 = 0, x, y, x, y
            while queue:
                cx, cy = queue.popleft()
                area += 1
                x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < tw and 0 <= ny < th and px[nx, ny] and not seen[ny * tw + nx]:
                        seen[ny * tw + nx] = 1
                        queue.append((nx, ny))
            yield area, x0, y0, x1, y1


def find_card_box(img: Image.Image) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the card in img's pixels, or None if not confidently found."""
    thumb = img.convert("RGB")
    thumb.thumbnail((THUMB, THUMB))
    tw, th = thumb.size
    h, s, _ = thumb.convert("HSV").split()
    mask = Image.new("L", thumb.size)
    mask.putdata([0 if 8 <= a <= 35 and b >= 50 else 255 for a, b in zip(h.getdata(), s.getdata())])
    # Opening drops specks of dark wood grain; closing fills coloured print inside a card.
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))

    for k in EROSIONS:
        eroded = mask.filter(ImageFilter.MinFilter(k)) if k > 1 else mask
        r = k // 2
        best = None
        for area, x0, y0, x1, y1 in _regions(eroded):
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            if x0 == 0 or y0 == 0 or x1 == tw - 1 or y1 == th - 1:
                continue                                  # runs off the photo: glare, not a card
            if area < 0.6 * bw * bh:
                continue                                  # not a filled rectangle
            full_w, full_h = bw + 2 * r, bh + 2 * r       # size before erosion
            if full_w * full_h < 0.04 * tw * th:
                continue                                  # too small to be the card
            if not 1.2 <= max(full_w, full_h) / min(full_w, full_h) <= 2.2:
                continue                                  # not card-shaped
            if best is None or area > best[0]:
                best = (area, x0 - r, y0 - r, x1 + r + 1, y1 + r + 1)
        if best:
            _, x0, y0, x1, y1 = best
            sx, sy = img.width / tw, img.height / th
            pw, ph = (x1 - x0) * PAD, (y1 - y0) * PAD
            return (max(0, round((x0 - pw) * sx)), max(0, round((y0 - ph) * sy)),
                    min(img.width, round((x1 + pw) * sx)), min(img.height, round((y1 + ph) * sy)))
    return None


def crop_to_card(img: Image.Image) -> tuple[Image.Image, bool]:
    box = find_card_box(img)
    return (img.crop(box), True) if box else (img, False)
