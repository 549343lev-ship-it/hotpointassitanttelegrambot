"""
storage.py — безкоштовне постійне сховище через гілку GitHub `botdata`.

ЯК ПРАЦЮЄ:
  • При старті: завантажує кеш/клієнтів/правила/логи з гілки botdata → локальні файли
  • Під час роботи: фоновий потік раз на 60с перевіряє чи файли змінились → пушить у гілку
  • Гілка botdata створюється автоматично; main не чіпається → деплої не тригеряться

НАЛАШТУВАННЯ (env на Render):
  GITHUB_TOKEN        — Fine-grained PAT з правом Contents: Read and write на репозиторій
  GITHUB_DATA_REPO    — (опційно) owner/repo; за замовчуванням основний репозиторій бота
  GITHUB_DATA_BRANCH  — (опційно) назва гілки, за замовчуванням botdata

⚠️ Якщо репозиторій ПУБЛІЧНИЙ — дані клієнтів буде видно всім!
   Тоді зроби репо приватним або вкажи окремий приватний у GITHUB_DATA_REPO.
"""

import os, json, base64, hashlib, threading, time
import urllib.request
import urllib.error

GH_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GH_REPO   = os.environ.get("GITHUB_DATA_REPO", "549343lev-ship-it/hotpointassitanttelegrambot")
GH_BRANCH = os.environ.get("GITHUB_DATA_BRANCH", "botdata")
API       = "https://api.github.com"

# Плоскі файли які зберігаємо (catalog.json НЕ треба — він будується з прайсів)
FLAT_FILES = [
    "normalization_cache.json",
    "rules.txt",
    "usage_log.json",
    "not_found_log.json",
    "pending_rules.json",
    "pending_fixes.json",
]
CLIENTS_BUNDLE = "clients_data.json"   # вся папка clients/ упакована в один JSON

_sha_map = {}        # path у гілці → sha останньої відомої версії
_pushed_hash = {}    # path → md5 останнього запушеного вмісту
_lock = threading.Lock()


# ─── HTTP до GitHub API ──────────────────────────────────────────────────────

def _req(method: str, url: str, payload: dict = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {GH_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "hotpointbot-storage")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def _ensure_branch():
    """Створює гілку botdata з main якщо її ще немає."""
    st, _ = _req("GET", f"{API}/repos/{GH_REPO}/git/ref/heads/{GH_BRANCH}")
    if st == 200:
        return True
    # Беремо sha гілки main (або master)
    for base in ("main", "master"):
        st, ref = _req("GET", f"{API}/repos/{GH_REPO}/git/ref/heads/{base}")
        if st == 200:
            sha = ref["object"]["sha"]
            st2, _ = _req("POST", f"{API}/repos/{GH_REPO}/git/refs",
                          {"ref": f"refs/heads/{GH_BRANCH}", "sha": sha})
            return st2 in (200, 201)
    return False


def _get_remote(path: str):
    """Повертає (текст, sha) файла з гілки або (None, None)."""
    st, data = _req("GET",
        f"{API}/repos/{GH_REPO}/contents/{path}?ref={GH_BRANCH}")
    if st == 200 and data.get("content") is not None:
        try:
            text = base64.b64decode(data["content"]).decode("utf-8")
            return text, data.get("sha")
        except Exception:
            return None, data.get("sha")
    return None, None


def _put_remote(path: str, text: str) -> bool:
    """Пушить файл у гілку (create або update)."""
    payload = {
        "message": f"bot autosave: {path}",
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "branch": GH_BRANCH,
    }
    if _sha_map.get(path):
        payload["sha"] = _sha_map[path]
    st, data = _req("PUT", f"{API}/repos/{GH_REPO}/contents/{path}", payload)
    if st in (200, 201):
        _sha_map[path] = data.get("content", {}).get("sha", "")
        return True
    if st in (409, 422):
        # sha застарів — оновлюємо і пробуємо ще раз
        _, sha = _get_remote(path)
        if sha:
            payload["sha"] = sha
            st2, data2 = _req("PUT", f"{API}/repos/{GH_REPO}/contents/{path}", payload)
            if st2 in (200, 201):
                _sha_map[path] = data2.get("content", {}).get("sha", "")
                return True
    print(f"⚠️ storage push {path}: HTTP {st}", flush=True)
    return False


# ─── Пакування папки clients/ в один JSON ────────────────────────────────────

def _pack_clients() -> str:
    data = {}
    if os.path.isdir("clients"):
        for root, _dirs, files in os.walk("clients"):
            for fn in files:
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, "clients").replace(os.sep, "/")
                try:
                    with open(p, encoding="utf-8") as f:
                        data[rel] = f.read()
                except Exception:
                    pass
    return json.dumps(data, ensure_ascii=False)

def _unpack_clients(text: str):
    try:
        data = json.loads(text)
    except Exception:
        return
    for rel, content in data.items():
        p = os.path.join("clients", rel)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass


def _local_text(path: str):
    """Поточний локальний вміст для шляху в гілці (або None)."""
    if path == CLIENTS_BUNDLE:
        return _pack_clients()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None
    return None

def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


# ─── ПУБЛІЧНЕ API ────────────────────────────────────────────────────────────

def restore():
    """Викликати ПЕРЕД import cache/clients: тягне дані з гілки в локальні файли."""
    if not GH_TOKEN:
        print("ℹ️ storage: GITHUB_TOKEN не заданий — постійне сховище вимкнено", flush=True)
        return
    if not _ensure_branch():
        print("⚠️ storage: не вдалося створити/знайти гілку", flush=True)
        return
    restored = 0
    for path in FLAT_FILES:
        text, sha = _get_remote(path)
        if text is not None:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                restored += 1
            except Exception:
                pass
        if sha:
            _sha_map[path] = sha
        _pushed_hash[path] = _md5(text) if text is not None else None
    text, sha = _get_remote(CLIENTS_BUNDLE)
    if text is not None:
        _unpack_clients(text)
        restored += 1
    if sha:
        _sha_map[CLIENTS_BUNDLE] = sha
    _pushed_hash[CLIENTS_BUNDLE] = _md5(text) if text is not None else None
    print(f"💾 storage: відновлено {restored} файл(ів) з гілки {GH_BRANCH}", flush=True)


def save_now():
    """Пушить усі змінені файли зараз."""
    if not GH_TOKEN:
        return
    with _lock:
        for path in FLAT_FILES + [CLIENTS_BUNDLE]:
            text = _local_text(path)
            if text is None:
                continue
            h = _md5(text)
            if _pushed_hash.get(path) == h:
                continue   # не змінилось
            if _put_remote(path, text):
                _pushed_hash[path] = h
                print(f"💾 storage: збережено {path}", flush=True)


def start_autosave(interval: int = 60):
    """Фоновий потік: кожні `interval` секунд пушить зміни."""
    if not GH_TOKEN:
        return
    def loop():
        while True:
            time.sleep(interval)
            try:
                save_now()
            except Exception as e:
                print(f"⚠️ storage autosave: {e}", flush=True)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print(f"💾 storage: автозбереження кожні {interval}с у гілку {GH_BRANCH}", flush=True)
