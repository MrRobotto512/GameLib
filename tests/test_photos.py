"""Console photos: the white studio backdrop becomes transparent and the picture is cropped."""
import io

from PIL import Image

from gameshop.photos import cutout
from gameshop.systems import load_systems


def test_cutout_knocks_out_white_and_crops():
    im = Image.new("RGB", (200, 120), (255, 255, 255))
    for x in range(60, 140):
        for y in range(40, 90):
            im.putpixel((x, y), (40, 40, 48))                 # a dark console in the middle
    for x in range(60, 140):
        im.putpixel((x, 92), (236, 236, 236))                 # a faint shadow line under it
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    out = Image.open(io.BytesIO(cutout(buf.getvalue())))
    assert out.mode == "RGBA" and out.width < 120 and out.height < 90   # cropped to the console
    assert out.getpixel((0, 0))[3] == 0                                   # corner is transparent
    assert out.getpixel((out.width // 2, out.height // 2))[3] == 255      # the console is kept


def test_cutout_keeps_pictures_that_are_already_transparent():
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(30, 70):
        for y in range(30, 70):
            im.putpixel((x, y), (230, 230, 230, 255))           # a white console on nothing
    buf = io.BytesIO()
    im.save(buf, "PNG")
    out = Image.open(io.BytesIO(cutout(buf.getvalue())))
    assert out.getpixel((out.width // 2, out.height // 2)) == (230, 230, 230, 255)
    assert 40 <= out.width <= 48


def test_every_system_names_a_photo():
    systems = load_systems().all()
    assert all(s.photo and s.photo_by for s in systems)
