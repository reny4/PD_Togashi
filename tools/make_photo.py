"""スポットの写真をモーダル用に縮めて static/images/<spot_id>.jpg に保存する（手元で1回だけ使う道具）。

- 長い辺を 1200px までに縮め、JPEG で 200KB 以下になるまで画質を下げる
- 位置情報（GPS）などの撮影情報は消す（スマホで撮った写真の撮影場所を配らないため）
- 自分たちで撮った写真か、公式に利用が許された画像だけを使うこと

    pip install pillow
    python tools/make_photo.py spot_001 撮った写真.jpg

保存したら、同期元（スポット担当の spots/spots.json）の該当スポットに "photo": "spot_001.jpg" を入れて
sync_data.py を流す（data/spots.json だけ直すと次の同期で消える）。
"""
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_SIDE = 1200
MAX_BYTES = 200 * 1024
OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "images"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("使い方: python tools/make_photo.py <spot_id> <元の写真>")
    spot_id, src = sys.argv[1], Path(sys.argv[2])
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")  # 縦横の向きを直してから撮影情報を捨てる
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    for quality in range(85, 30, -5):
        buf = BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)  # exif を渡さない＝撮影情報なし
        if buf.tell() <= MAX_BYTES:
            break
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{spot_id}.jpg"
    out.write_bytes(buf.getvalue())
    print(f"保存しました: {out}（{img.width}x{img.height}、{buf.tell() // 1024}KB、画質{quality}）")
    print(f'同期元の spots/spots.json の {spot_id} に "photo": "{spot_id}.jpg" を入れて sync_data.py を流してください')


if __name__ == "__main__":
    main()
