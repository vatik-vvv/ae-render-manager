import json
import logging
import os
import sys
import tempfile

import requests

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

from app_paths import config_path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_MAX_SIDE = 2000
PREVIEW_MIN_SIDE = 64
PREVIEW_MAX_SIDE_LIMIT = 8000

BOT_TOKEN = ""
CHAT_ID = ""
PREVIEW_MAX_SIDE = DEFAULT_PREVIEW_MAX_SIDE

SUPPORTED_PREVIEW_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp", ".webp",
}


def _load_config():
    global BOT_TOKEN, CHAT_ID, PREVIEW_MAX_SIDE
    cfg_file = config_path()
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, encoding="utf-8") as f:
                cfg = json.load(f)
            tg = cfg.get("telegram", {})
            BOT_TOKEN = tg.get("bot_token", "")
            CHAT_ID = str(tg.get("chat_id", ""))
            raw_max = tg.get("preview_max_side", DEFAULT_PREVIEW_MAX_SIDE)
            try:
                PREVIEW_MAX_SIDE = int(raw_max)
            except (TypeError, ValueError):
                PREVIEW_MAX_SIDE = DEFAULT_PREVIEW_MAX_SIDE
            PREVIEW_MAX_SIDE = max(PREVIEW_MIN_SIDE, min(PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE_LIMIT))
        except Exception as e:
            logger.warning("Error loading Telegram config: %s", e)
    else:
        logger.warning("config.json not found, notifications disabled.")


_load_config()


def reload_config():
    _load_config()


def check_preview_dependencies():
    missing = []
    if Image is None:
        missing.append("Pillow")
    if np is None:
        missing.append("numpy")
    if not missing:
        return True, "", ""
    names = ", ".join(missing)
    if getattr(sys, "frozen", False):
        hint = "Rebuild exe: pip install -r requirements.txt && pyinstaller main.spec"
        return False, f"Missing in build: {names}", hint
    py = sys.executable
    hint = f'"{py}" -m pip install -r requirements.txt'
    return False, f"Not installed: {names}", hint


def _resolve_max_side(max_side):
    if max_side is None:
        reload_config()
        return PREVIEW_MAX_SIDE
    try:
        value = int(max_side)
    except (TypeError, ValueError):
        value = DEFAULT_PREVIEW_MAX_SIDE
    return max(PREVIEW_MIN_SIDE, min(value, PREVIEW_MAX_SIDE_LIMIT))


def send_message(text, timeout=5):
    reload_config()
    if not BOT_TOKEN or not CHAT_ID:
        return False, "Telegram bot_token or chat_id not set in config.json"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code == 200:
            return True, ""
        return False, f"Telegram API {response.status_code}: {response.text[:200]}"
    except requests.Timeout:
        return False, f"Telegram timeout (>{timeout}s)"
    except Exception as e:
        return False, str(e)


def _tone_map_to_uint8(arr):
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    arr = np.maximum(arr, 0)
    if arr.size == 0:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    if np.issubdtype(arr.dtype, np.floating):
        peak = float(np.percentile(arr, 99.5))
        peak = max(peak, 1e-4)
        arr = np.clip(arr / peak, 0.0, 1.0)
        arr = (arr * 255.0).astype(np.uint8)
    elif arr.dtype == np.uint16:
        arr = (arr / 256).astype(np.uint8)
    elif arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    return arr


def _numpy_to_pil_rgb(arr):
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.ndim == 3:
        channels = arr.shape[2]
        if channels == 4:
            arr = arr[:, :, :3]
        elif channels == 1:
            arr = np.repeat(arr, 3, axis=2)
        elif channels >= 3:
            arr = arr[:, :, :3]
    arr = _tone_map_to_uint8(
        arr.astype(np.float64) if np.issubdtype(arr.dtype, np.floating) else arr
    )
    return Image.fromarray(arr, mode="RGB")


def _load_with_opencv(image_path):
    if cv2 is None or np is None:
        return None, "Install opencv-python-headless numpy for EXR"
    data = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if data is None:
        return None, f"OpenCV could not read: {os.path.basename(image_path)}"
    if data.ndim == 2:
        rgb = data
    else:
        rgb = cv2.cvtColor(data[:, :, :3], cv2.COLOR_BGR2RGB) if data.shape[2] >= 3 else data[:, :, 0]
    return _numpy_to_pil_rgb(rgb), ""


def _load_with_pillow(image_path):
    if Image is None:
        return None, "Pillow not installed"
    try:
        with Image.open(image_path) as img:
            img.load()
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (0, 0, 0))
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
            return img.copy(), ""
    except Exception as e:
        return None, str(e)


def _load_preview_image(image_path):
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in SUPPORTED_PREVIEW_EXTENSIONS:
        return None, f"Unsupported preview format: {ext}"
    if ext == ".exr":
        return _load_with_opencv(image_path)
    img, err = _load_with_pillow(image_path)
    if img is not None:
        return img, ""
    if ext in (".tif", ".tiff", ".exr"):
        return _load_with_opencv(image_path)
    return None, err or "Could not open image"


def _prepare_jpeg(image_path, max_side=DEFAULT_PREVIEW_MAX_SIDE):
    if Image is None:
        return None, "Pillow not installed"
    max_side = _resolve_max_side(max_side)
    temp_path = None
    try:
        img, load_err = _load_preview_image(image_path)
        if img is None:
            return None, load_err or "Could not load image"
        width, height = img.size
        scale = min(1.0, max_side / float(max(width, height))) if max(width, height) > 0 else 1.0
        if scale < 1.0:
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.LANCZOS)
        fd, temp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        img.save(temp_path, format="JPEG", quality=85, optimize=True)
        return temp_path, ""
    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return None, str(e)


def _send_photo_file(file_path, caption=None, mime="image/jpeg", timeout=30):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(file_path, "rb") as photo_file:
        files = {"photo": (os.path.basename(file_path), photo_file, mime)}
        data = {"chat_id": CHAT_ID}
        if caption:
            data["caption"] = caption[:1024]
        response = requests.post(url, data=data, files=files, timeout=timeout)
    if response.status_code == 200:
        return True, ""
    return False, f"Telegram API {response.status_code}: {response.text[:300]}"


def send_image(image_path, caption=None, max_side=None, timeout=30):
    reload_config()
    if not BOT_TOKEN or not CHAT_ID:
        return False, "Telegram bot_token or chat_id not set in config.json"
    if not image_path or not os.path.isfile(image_path):
        return False, f"File not found: {image_path}"
    max_side = _resolve_max_side(max_side)
    temp_path = None
    try:
        temp_path, prep_err = _prepare_jpeg(image_path, max_side)
        if not temp_path:
            return False, prep_err or "Could not prepare JPEG"
        size_mb = os.path.getsize(temp_path) / (1024 * 1024)
        if size_mb > 9.5:
            return False, f"JPEG {size_mb:.1f} MB exceeds Telegram limit (~10 MB)"
        return _send_photo_file(temp_path, caption=caption, mime="image/jpeg", timeout=timeout)
    except requests.Timeout:
        return False, f"Telegram timeout (>{timeout}s)"
    except Exception as e:
        return False, str(e)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
