# plex_black_suspect_analyzer

録画ファイルを Plex に取り込む際、**録画開始直後にサムネイルが作成されて真っ黒になってしまう問題**を自動的に検出し、再解析を行う Python スクリプトです。

## 機能の概要

1. **時刻差チェック (`check_time_diff`)**
    - `(updatedAt - addedAt)` が指定した閾値（分）より小さい場合、そのアイテムを“録画直後に作られたサムネ疑惑”とみなす。
    - このロジックだけなら負荷は軽く済み、短時間録画ファイルなどを効率的に抽出できる。
2. **黒率チェック (`check_black_image`)**
    - **Pillow** を用いて、Plex が現在保持しているサムネイル画像をダウンロード→ピクセルごとの明度解析。
    - **黒率** (黒ピクセル / 総ピクセル) が指定しきい値以上なら「真っ黒サムネ」と判定。
    - 計算コストが高めなので、デフォルトは時刻差チェックで**怪しい**アイテムだけを検査。
    - `-force-black-check` オプションを付ければ、**すべてのアイテム**で黒率チェックを実行可能。
3. **再解析 or リフレッシュ**
    - **黒サムネ**だった場合 → `PUT /library/metadata/<ratingKey>/analyze` を呼び出し、再度サムネイル生成を試みる
    - **黒くないが、録画完了からの時間差が小さい (＝更新漏れ疑い)** → `PUT /library/metadata/<ratingKey>/refresh` を実行し、**updatedAt** を進める＆メタデータを再スキャンさせる

このフローにより、**録画が完了していない状態の“真っ黒サムネ”を減らす**と同時に、サムネが正常でも updatedAt を適切に更新することで今後の管理をしやすくします。

---

## 動作環境

- Python 3.x
- 主要ライブラリ
    - [requests](https://pypi.org/project/requests/) (Plex API との通信)
    - [Pillow](https://pypi.org/project/Pillow/) (画像解析: ピクセルごとの黒率判定)
- Plex Media Server
    - `/library/metadata/<ratingKey>/analyze` や `/refresh` を `PUT` メソッドで受け付けるバージョンを推奨。
    - **注意:** ライブラリ種別が「none / clip」扱いのアイテムだと `PUT /analyze` が 404 を返す場合があります。

---

## インストール

Debian / Ubuntu 系の例:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-requests python3-pil

```

あるいは `pip3 install requests Pillow` で入れてください。

---

## 使い方

1. **スクリプトを用意**
    - `plex_black_suspect_analyzer.py` の名前で保存し、実行権限を付与:
        
        ```bash
        chmod +x plex_black_suspect_analyzer.py
        
        ```
        
2. **Plex のトークン（`-plex-token`）を取得**
    - *（必ず外部に流出しないように管理してください。GitHub等にコミットしないよう注意）*
3. **実行例**
    
    ```bash
    ./plex_black_suspect_analyzer.py \
      --plex-server=192.168.10.20 \
      --plex-port=32400 \
      --plex-token=<YOUR_PLEX_TOKEN> \
      --library-id=5 \
      --time-diff-minutes=3 \
      --blackness-threshold=0.95 \
      --debug
    
    ```
    
    - `-time-diff-minutes=3`→ `(updatedAt - addedAt) < 3分` なら怪しいと判定
    - `-blackness-threshold=0.95`→ 画像解析で黒率 95% 以上なら真っ黒
    - `-debug`→ コンソールにも DEBUG ログを表示（ファイルログにも保存）
4. **オプションまとめ**
    - `-time-diff-minutes` (float, default=3.0)→ **0** を指定すれば時刻差チェックを事実上無効化できる
    - `-blackness-threshold` (float, default=0.95)→ 0.0～1.0 で指定。値が大きいほど“ほぼ完全な黒”のみ検知
    - `-force-black-check`→ **すべてのアイテム**で黒率チェックを実行（コスト増）
    - `-debug`→ デバッグログをコンソールに出力

---

## 処理の流れ

1. **ライブラリ一覧取得**: `/library/sections/<ID>/all` から `(addedAt, updatedAt, thumb, ...)` を取得
2. **`check_time_diff`**: `(updatedAt - addedAt) < 閾値sec` なら怪しい（`suspicious=True`）
    - ログ上では “SUSPICIOUS” or “OK” が出力
3. **`check_black_image`**:
    - デフォルトでは「`suspicious=True` の場合のみ実行」
    - `-force-black-check` なら全アイテム
    - 黒率 ≥ `blackness_threshold` → “BLACK”、それ以外は “OK”
4. **処理分岐**
    - `suspicious=True & black=True` → `PUT /analyze`
    - `suspicious=True & black=False` → `PUT /refresh`
    - その他 → 何もしない
5. **ログに表示**
    - 何が `PUT /analyze` されたか、`PUT /refresh` されたかを `[INFO]` レベルで確認可能。

---

## 注意・制限

1. **ライブラリ種別 / エージェント**
    - `subtype="clip"` & `noneエージェント` のアイテムでは `PUT /analyze` が 404 を返すことがあります。
    - その場合、スクリプトではリフレッシュ (`PUT /refresh`) を試すか、TV番組や映画のライブラリに移行するなどの対策が必要です。
2. **updatedAt が増えない Plex**
    - 一部環境ではサムネ更新しても `updatedAt` が増えない場合があります。
    - その場合、**時刻差ロジックが無意味**になるかもしれません。
    - `-force-black-check` を常用するなど方針を変える必要があります。
3. **高負荷に注意**
    - `-force-black-check` + 大量のアイテム → サムネイル画像ダウンロード量が膨大になる
    - 適度に cron の実行間隔をあける / 短い録画だけに絞る などで調整してください。
4. **Plex Token の管理**
    - **このトークンを外部に公開しない**でください（README などにも絶対に載せないよう注意）。
    - もし漏れてしまうと権限のある第三者が Plex にアクセスできるリスクがあります。

---

## ライセンス

本スクリプトは MIT License で提供しています。
ソースコードの改変・再配布は自由ですが、使用・運用は 自己責任 でお願いいたします。

---

## 寄稿・不具合報告

- Pull Request, Issue など歓迎いたします。
- 404 やサムネが変わらない等の事例がありましたら、ライブラリ種別やエージェント設定、Plex バージョン等をお知らせください。
