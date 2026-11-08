# static/images

スポットの「詳しく見る」モーダルに出す写真の置き場所。

- ファイル名は `<spot_id>.jpg`（例 `spot_001.jpg`）。`tools/make_photo.py` で縮めて置く（1枚200KB以下、撮影情報なし）
- 画面に出すには、同期元（スポット担当の `spots/spots.json`）の該当スポットに `"photo": "spot_001.jpg"` を入れて `sync_data.py` を流す
- `photo` が空、またはファイルがないスポットは、モーダルに家紋（巴）の代わり絵が出る
- 使ってよいのは、自分たちで撮った写真か、公式に利用が許された画像だけ
- `.gitignore` と `.gcloudignore` が `*.png` を除外しているので、写真は `.jpg` にする
