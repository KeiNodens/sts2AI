# sts2AI

[![Python checks](https://github.com/KeiNodens/sts2AI/actions/workflows/ci.yml/badge.svg)](https://github.com/KeiNodens/sts2AI/actions)

Slay the Spire 2を、人間とAIで協力プレイするためのローカルツールです。既存の[STS2MCP](https://github.com/Gennadiyev/STS2MCP)を拡張し、カード効果の整理、行動評価、実行時のトレースと観測を追加しました。

自分のWindows環境で開発した実験的なプロジェクトです。mod/は既存Modの改変版、advisor/は追加したPythonの判断・観測層です。ゲームと第三者Modのバイナリ、セーブ、生成済みの大規模KBは含みません。

## 追加したもの

- ポートを変えて同一PCの二つのゲームを接続する構成。
- カードやパワーのカタログ取得と、実行された処理を記録するトレース。
- カードの効果・出所・信頼度を整理し、順序と組み合わせを評価するPython advisor。
- カード使用後に手札選択が残る場合の操作と、協力プレイの投票待ちへの対応。

固定の定型処理で進む自動ドライバもあります。その結果はLLMのプレイ能力の測定ではありません。勝率改善や全ランの完走は保証していません。

## 前提とPythonの導入

Windows、Python 3.11以降、購入済みのSlay the Spire 2が必要です。改変Modのビルドには.NET 9をターゲットにできるSDKと参照パックが必要です。利用するMod同士の対応版は自分の環境で揃えてください。

このリポジトリだけを取得し、ルートで実行します。

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -c requirements-lock.txt .
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

## 改変Modのビルドと配置

```powershell
./mod/build.ps1 -GameDir '<your-game-directory>'
```

mod/out/STS2_MCP/STS2_MCP.dllをゲームのmodsフォルダーへコピーし、mod/mod_manifest.jsonをSTS2_MCP.jsonという名前で同じ場所へコピーします。元のSTS2MCPとMod IDを共有するので、同時に二つの版を入れず、置き換える前に既存のファイルをバックアップしてください。ゲームの設定でModを有効にします。

ビルドはゲームに同梱されたsts2.dll、GodotSharp、Harmonyを参照しますが、これらを出力へコピーしません。改変版であり、STS2MCPやゲーム開発元との提携はありません。

## 人間側とAI側を起動

```powershell
./scripts/launch.ps1 -GameDir '<game>' -Role host
./scripts/launch.ps1 -GameDir '<game>' -Role join
```

--fastmp host/joinで同一PC内の接続を試みます。人間側APIは15526、AI側は15527です。ロビーの準備状態を確認して開始してください。協力プレイには相手の投票待ちがあるため、待機を操作失敗と誤認して連打しないでください。

## MCPクライアントへの登録

設定例の`<repo>`を絶対パスに置き換えます。

```json
{
  "mcpServers": {
    "sts2": {
      "command": "<repo>/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_bridge.server", "--port", "15527", "--no-trust-env"],
      "env": {"STS2_KB_PATH": "<repo>/card_kb.json"}
    }
  }
}
```

主な追加ツールはget_decision_packとflush_observationsです。既存のget_game_stateなども利用できます。カード使用、ターン終了、報酬・イベント選択はゲームを進めます。元のツールにはプロフィールを変更・削除する操作もあるので、クライアントで許可する範囲を選んでください。

## 自動進行とカードKB

```powershell
.venv/Scripts/python.exe -m advisor.auto2 --port 15527 --steps 300
```

自動ドライバは実際のランを進行させ、トレースと観測をローカルKBへ保存します。重要なセーブを退避してテスト用のランで試してください。保存先はSTS2_KB_PATH、既定は実行ディレクトリのcard_kb.jsonです。KBがなくても計算値から評価しますが、未知のMod機構を正確に扱えるとは限りません。

ゲームが起動し、改変ModのAPIが応答している状態でカタログを取得できます。

```powershell
.venv/Scripts/python.exe -m tools.gen_card_kb --port 15527 --out card_kb.json --mods-dir '<game>/mods'
.venv/Scripts/python.exe -m tools.drain_trace --port 15527 --kb card_kb.json
```

--mods-dirは更新検出用の任意指定です。指定しなければローカルModファイルの更新は追跡しません。生成したKBやゲーム内の情報を公開リポジトリへ追加しないでください。

## 構成と検証

mod/に改変したC# Mod、mcp_bridge/にMCP、advisor/に評価・観測・ドライバ、tools/にKB生成、tests/にゲーム不要の合成データによるテストがあります。別のチェックアウトや本番KBを必要としません。

公開用の確認結果と実機検証の区別は[VALIDATION.md](VALIDATION.md)、データと操作の注意点は[SECURITY.md](SECURITY.md)、元プロジェクトと許諾表示は[NOTICE.md](NOTICE.md)を参照してください。自作部分は[MIT License](LICENSE)、元のSTS2MCPのMIT許諾文は[licenses/STS2MCP-MIT.txt](licenses/STS2MCP-MIT.txt)です。

各プロジェクトは別々の仮想環境へ導入してください。検証済みの依存バージョンはrequirements-lock.txtに固定しています。
