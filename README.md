# holo_goods 個人用メモ

このリポジトリは個人運用前提です。
外部向け説明ではなく、自分用の操作手順だけを残します。

## 前提
- 作業場所: `E:\my_dev\holo_goods`
- 定期実行の本体: `holo_monitor`
- GASコード: `holo_goods_gas`

## 通常実行（全サイト巡回）
```powershell
cd E:\my_dev\holo_goods
python -m holo_monitor.runner
```

## 単一URLだけ手動実行（シートへ1件投入）
`runner.py` は URL 引数で手動1件モードに入ります。

```powershell
cd E:\my_dev\holo_goods
python -m holo_monitor.runner --url "https://example.com/item"
```

短縮形:
```powershell
python -m holo_monitor.runner -u "https://example.com/item"
```

先頭引数でも可:
```powershell
python -m holo_monitor.runner "https://example.com/item"
```

補足:
- 互換として `MANUAL_ITEM_URL` 環境変数も使えます。
- 単一URL実行時はサイト全巡回は行いません。

## 状態ファイル（state）
- 各サイトの既知IDは `state/*.json` で管理されます。
- 差分取得の基準に使うため、手動で消すと再取得量が増えます。

## サイト設定
- 監視対象は `holo_monitor/sites.yaml`。
- 現在は `gamers` を含めて有効化済み。

## GAS（clasp）運用メモ
- `clasp push` は Git の push と挙動が違います。
- ローカル削除が自動で反映されないケースがあるため、必要なら Apps Script 側で手動削除します。
- 不要な上書きを避けるため、意図しない `clasp pull` は実行しません。

## 自分用チェックコマンド
```powershell
# 監視設定の構文確認
python - <<'PY'
import yaml
with open(r'E:\my_dev\holo_goods\holo_monitor\sites.yaml', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
print([s.get('id') for s in cfg.get('sites', [])])
PY
```
