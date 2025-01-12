#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import requests
import logging
from datetime import datetime
from xml.etree import ElementTree
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def setup_logger(debug_mode=False, log_file_path="/var/log/plex_black_analyzer.log"):
    logger = logging.getLogger("plex_analyze")
    # 全体の最低レベルは一旦DEBUGでもOK。ただし各Handlerが出力レベルを制御
    logger.setLevel(logging.DEBUG)

    # コンソール出力用ハンドラ
    sh = logging.StreamHandler(sys.stdout)
    # --debug オプションがあればDEBUG、なければINFO
    sh.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    sh_formatter = logging.Formatter("[%(levelname)s] %(message)s")
    sh.setFormatter(sh_formatter)
    logger.addHandler(sh)

    # ファイル出力用ハンドラ
    fh = logging.FileHandler(log_file_path, encoding='utf-8')
    # こちらも同様に --debug がなければINFO
    fh.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    fh_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    return logger


def fetch_library_items(logger, plex_server, plex_port, plex_token, library_id):
    url = f"http://{plex_server}:{plex_port}/library/sections/{library_id}/all"
    params = {"X-Plex-Token": plex_token}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"ライブラリ取得失敗: {e}")
        return []

    items = []
    root = ElementTree.fromstring(resp.content)
    for video in root.findall("./Video"):
        rating_key = video.get("ratingKey")
        added_at   = video.get("addedAt", "")
        updated_at = video.get("updatedAt", "")
        thumb      = video.get("thumb", "")
        title      = video.get("title", "Unknown")
        items.append((rating_key, title, added_at, updated_at, thumb))
    return items


def check_time_diff(logger, rating_key, title, added_str, updated_str, threshold_sec):
    """
    (updatedAt - addedAt) が threshold_sec より小さいなら「録画直後サムネ疑い」とみなす
    """
    if not (added_str.isdigit() and updated_str.isdigit()):
        return False

    added_ts   = int(added_str)
    updated_ts = int(updated_str)
    diff_sec   = (datetime.fromtimestamp(updated_ts) - datetime.fromtimestamp(added_ts)).total_seconds()

    if diff_sec < threshold_sec:
        logger.debug(
            f"[{rating_key}, {title}] 時刻差: {diff_sec:.1f} < threshold={threshold_sec:.1f} → SUSPICIOUS"
        )
        return True
    else:
        logger.debug(
            f"[{rating_key}, {title}] 時刻差: {diff_sec:.1f} >= threshold={threshold_sec:.1f} → OK"
        )
        return False


def check_black_image(logger, rating_key, title, thumb_url, plex_server, plex_port, plex_token, blackness_threshold):
    """
    サムネイルの黒率を調べる(0.0～1.0)。
    黒率 >= blackness_threshold → True (黒い)
    """
    if not thumb_url or "none" in thumb_url.lower():
        logger.debug(f"[{rating_key}, {title}] サムネURLなし or none → False")
        return False

    if not PIL_AVAILABLE:
        logger.debug(f"[{rating_key}, {title}] PIL未導入で画像判定不可 → False")
        return False

    if thumb_url.startswith("/"):
        thumb_url_full = f"http://{plex_server}:{plex_port}{thumb_url}"
    else:
        thumb_url_full = thumb_url

    try:
        resp = requests.get(thumb_url_full, params={"X-Plex-Token": plex_token}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.debug(f"[{rating_key}, {title}] 画像取得失敗: {e} → “実質サムネ無”と判断して再生成対象にする")
        return True

    try:
        img = Image.open(BytesIO(resp.content)).convert("L")
    except Exception as e:
        logger.debug(f"[{rating_key}, {title}] 画像解析失敗: {e} → False")
        return False

    hist = img.histogram()
    total_pixels = sum(hist)
    if total_pixels == 0:
        logger.debug(f"[{rating_key}, {title}] ピクセル数0 → False")
        return False

    black_pixels = hist[0]
    black_ratio = black_pixels / total_pixels
    if black_ratio >= blackness_threshold:
        logger.debug(
            f"[{rating_key}, {title}] サムネ黒率: {black_ratio:.3f} >= threshold={blackness_threshold} → BLACK"
        )
        return True
    else:
        logger.debug(
            f"[{rating_key}, {title}] サムネ黒率: {black_ratio:.3f} < threshold={blackness_threshold} → OK"
        )
        return False


def put_analyze(logger, plex_server, plex_port, plex_token, rating_key):
    url = f"http://{plex_server}:{plex_port}/library/metadata/{rating_key}/analyze"
    params = {"X-Plex-Token": plex_token}
    try:
        resp = requests.put(url, params=params, data={})
        resp.raise_for_status()
        logger.info(f"ANALYZE [PUT] ratingKey={rating_key} => {resp.status_code}")
    except requests.RequestException as e:
        logger.error(f"ANALYZE失敗 (ratingKey={rating_key}): {e}")


def put_refresh(logger, plex_server, plex_port, plex_token, rating_key):
    url = f"http://{plex_server}:{plex_port}/library/metadata/{rating_key}/refresh"
    params = {"X-Plex-Token": plex_token, "force": "1"}
    try:
        resp = requests.put(url, params=params, data={})
        resp.raise_for_status()
        logger.info(f"REFRESH [PUT] ratingKey={rating_key} => {resp.status_code}")
    except requests.RequestException as e:
        logger.error(f"REFRESH失敗 (ratingKey={rating_key}): {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="デバッグログを詳細出力")
    parser.add_argument("--plex-server", default=os.environ.get("PLEX_SERVER","192.168.10.20"))
    parser.add_argument("--plex-port",   default=os.environ.get("PLEX_PORT","32400"))
    parser.add_argument("--plex-token",  default=os.environ.get("PLEX_TOKEN","YOUR_TOKEN"))
    parser.add_argument("--library-id",  default=os.environ.get("LIBRARY_ID","5"))
    parser.add_argument("--log-file",    default=os.environ.get("LOG_FILE_PATH","/var/log/plex_black_analyzer.log"))

    # Time diff threshold
    parser.add_argument("--time-diff-minutes", type=float, default=3.0,
        help="(updatedAt - addedAt) がこれ未満なら録画直後サムネ疑惑 (分)")
    # Black ratio threshold
    parser.add_argument("--blackness-threshold", type=float, default=0.95,
        help="サムネ黒率(0.0～1.0)がこれ以上なら黒サムネと判定")
    # Force black check
    parser.add_argument("--force-black-check", action="store_true",
        help="time diff がOKでも強制的に check_black_image を実行")

    args = parser.parse_args()

    logger = setup_logger(debug_mode=args.debug, log_file_path=args.log_file)
    logger.info("=== Start Script ===")

    time_diff_sec = args.time_diff_minutes * 60.0

    items = fetch_library_items(
        logger,
        args.plex_server,
        args.plex_port,
        args.plex_token,
        args.library_id
    )
    logger.info(f"取得アイテム数: {len(items)}")

    for (rating_key, title, added_at, updated_at, thumb) in items:
        # 1) check_time_diff
        suspicious = check_time_diff(logger, rating_key, title, added_at, updated_at, time_diff_sec)

        # 2) check_black_image 実行するかどうか
        #    - デフォルトでは suspicious の場合のみ
        #    - --force-black-check があれば常に
        do_black_check = suspicious or args.force_black_check

        black = False
        if do_black_check:
            black = check_black_image(
                logger, rating_key, title, thumb,
                args.plex_server, args.plex_port, args.plex_token,
                args.blackness_threshold
            )

        # 処理分岐
        if suspicious:
            if black:
                # まだ真っ黒 → 再解析
                put_analyze(logger, args.plex_server, args.plex_port, args.plex_token, rating_key)
            else:
                # 黒くないなら updatedAt を進めたいので refresh
                put_refresh(logger, args.plex_server, args.plex_port, args.plex_token, rating_key)
        else:
            # suspicious でない => time diff OK
            # ただし force-black-check で black と判定されたら analyze?
            if args.force_black_check and black:
                put_analyze(logger, args.plex_server, args.plex_port, args.plex_token, rating_key)
            # 何もしない

    logger.info("=== Finished Script ===\n")


if __name__ == "__main__":
    main()
