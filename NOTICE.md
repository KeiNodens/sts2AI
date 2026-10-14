# 出典とライセンス

自作のPython、Java、文書、変更部分には[MIT License](LICENSE)を適用します。

mod/ のC#コードと mcp_bridge/server.py は [Gennadiyev/STS2MCP](https://github.com/Gennadiyev/STS2MCP)由来のコードまたは構成が含まれます。元作者はYikun Ji (Kunologist)、元のライセンスはMITです。[元の許諾文](licenses/STS2MCP-MIT.txt)を保持しています。

取り込み元はローカル記録のSTS2MCP v0.4.0です。追加したcatalog、trace、ポート指定とPython advisorを同梱し、公式版との非提携を明示します。

ゲーム本体、第三者Mod、ゲームの画像・説明文・解析済みソース、学習済みの大規模カードKBは同梱していません。利用者が別途取得するものには各提供元の条件が適用されます。Slay the Spireの開発元および依存Modの公式プロジェクトとは提携していません。

PythonのMCP SDKとHTTPXはpipで取得する依存です。ライブラリのコードはこのリポジトリに複製していません。StS1のJavaビルドではゲームとModTheSpire、BaseMod、StSLib、Together in Spireを参照します。StS2のビルドではゲームに同梱されるGodotSharp、Harmony、sts2.dllを参照し、これらを成果物へコピーしません。

任意のローカル解析に使うCFRは別途取得してください。CFRのJARや第三者の解析結果をこのリポジトリへ追加しないでください。
