"""会話エンジン・史実・スポット担当のファイルを app/data/ にコピーする。

担当が engine/persona.md、engine/sources/、spots/spots.json を直したら、
デプロイの前にこれを1回実行する（Cloud Run には app/ フォルダだけが上がるため）。

    python sync_data.py
"""
import shutil
from pathlib import Path

APP = Path(__file__).parent
ROOT = APP.parent
DATA = APP / "data"

(DATA / "sources").mkdir(parents=True, exist_ok=True)
for old in (DATA / "sources").glob("*"):
    old.unlink()
shutil.copy(ROOT / "engine" / "persona.md", DATA / "persona.md")
shutil.copy(ROOT / "engine" / "topic.py", APP / "topic.py")
shutil.copy(ROOT / "spots" / "spots.json", DATA / "spots.json")
for p in (ROOT / "engine" / "sources").glob("*"):
    if p.suffix in (".md", ".txt") and p.name != "README.md":
        shutil.copy(p, DATA / "sources" / p.name)
print("コピーしました:", sorted(str(p.relative_to(APP)) for p in DATA.rglob("*") if p.is_file()))
