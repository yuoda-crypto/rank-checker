# 順位チェッカー

Googleの検索順位（1〜9位）を毎日かんたんに記録できるデスクトップアプリ。
キーワードと自分のページURLを登録して「チェック実行」を押すだけ。

![macOS](https://img.shields.io/badge/macOS-対応-blue) ![Windows](https://img.shields.io/badge/Windows%2010%2F11-対応-blue)

## ダウンロード（Windows）

**[Releasesページ](../../releases/latest)** から `rank-checker-win.zip` をダウンロード。

1. ZIPを右クリック →「すべて展開」
2. `セットアップ.bat` をダブルクリック
   - 「WindowsによってPCが保護されました」と出たら「詳細情報」→「実行」
3. デスクトップにできた「順位チェッカー」から起動

詳しくは同梱の「はじめにお読みください.txt」へ。

## 主な機能

- 📈 順位の推移グラフ（TOP3ゾーン表示・前回比バッジ）
- 🔍 キーワード検索（ひらがな・カタカナ・メモの中身でもヒット）
- 🗾 検索地域の指定（47都道府県。エリアページの「現地の順位」が測れる）
- 🥊 競合ウォッチ（最大4サイトの順位を同じグラフに重ねて表示）
- 📄 CSV出力（全キーワードまとめ＝日付×KWの表／キーワード別＝競合順位つき。Excelでそのまま開ける）
- 📝 キーワードごとのメモ・ドラッグで並び替え・絞り込み一括チェック
- 🦀 マスコットの「クロ」が右下で豆知識をしゃべる（100種類）

## 安心ポイント

- データはすべて自分のPC内に保存（外部送信なし）
- Googleへの負荷対策として1回の実行は最大5キーワード・間隔を空けて実行

## 開発者向け

```bash
python -m venv .venv
.venv/bin/pip install pywebview playwright pykakasi
.venv/bin/playwright install chrome
.venv/bin/python app.py
```

Windows配布用ビルドは `v*` タグをpushするとGitHub Actionsが自動でReleasesに添付します。

## クレジット

マスコット「クロ」のGIFは claude-mascot プロジェクト（© 2026 Asiro, MIT License）の素材を使用しています。
