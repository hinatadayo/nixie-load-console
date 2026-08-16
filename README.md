# nixie-load-console

Claude Code のレート制限ヘッダ (`anthropic-ratelimit-unified-*`) を横取りし、
ニキシー管風の計器パネルでリアルタイム表示するツール。

## 構成

- `hdr_probe.py` — `api.anthropic.com` への通信を中継する透過プロキシ。レスポンスヘッダを見て `usage.json` に書き出す。
- `dashboard.html` — `hdr_probe.py` が配信するライブダッシュボード。4秒おきに自動更新される。
- `usage.json` — 実行時に生成される最新のレート制限情報 (gitignore 対象)。

## 使い方

### 1. プロキシを起動

```
python hdr_probe.py --port 8787
```

### 2. 別のターミナルで Claude Code をこのプロキシ経由にする

```
# PowerShell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8787"
claude
```

```
# bash / zsh
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
claude
```

Claude Code で何かやり取りするたびに、ターミナル1にヘッダが表示され `usage.json` が更新される。

### 3. ダッシュボードを開く

ブラウザで以下を開く:

```
http://127.0.0.1:8787/_probe/dashboard
```

5時間 / 7日ウィンドウの利用率、次回リセット時刻、overage(従量課金)の可否がニキシー管とアナログゲージで表示され、Claude Code とのやり取りに合わせて自動更新される。

## オプション

| フラグ | 既定値 | 説明 |
|---|---|---|
| `--port N` | `8787` | 待ち受けポート |
| `--out PATH` | `usage.json` | レート制限情報の書き出し先 |
| `--all` | off | `anthropic-*` 以外のヘッダも全部表示する |

## 注意

- プロキシを終了/再起動するとダッシュボードは「NO SIGNAL」になる。再起動時は既存の `usage.json` を読み込むので直近の値はすぐ復元される。
- ボディには一切手を触れない透過プロキシなので、Claude Code の動作自体には影響しない。
