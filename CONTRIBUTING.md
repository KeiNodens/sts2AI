# 開発と公開対象

Pythonの変更は `python -B -m unittest discover -s tests -v` で確認します。ゲームが不要なテストと、ゲームを操作する実機確認を分けてください。

生成物、解析済みソース、ゲームと第三者Modのバイナリ、セーブ、ログ、鍵、個人設定をコミットしないでください。公開ファイルを変更した場合は、差分を確認して `python -m tools.check_release --refresh-manifest` で目録を更新します。通常の検査は `python -m tools.check_release` です。

第三者由来のコードを追加する場合は出典とライセンスを記録し、既存の著作権表示を保持してください。
