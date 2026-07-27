on run
	set projectDir to "/Users/e0201/claude-projects/free-dev/rank-checker"
	tell application "Terminal"
		activate
		do script "cd '" & projectDir & "' && ./run.sh --visible; echo ''; echo '=== 完了！このウィンドウは閉じてOKだよ ==='"
	end tell
end run
