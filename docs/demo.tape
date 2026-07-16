# vhs tape for the modelferry terminal GIF.
# Render with: vhs docs/demo.tape   (needs vhs installed, network, and modelferry
# on PATH). Output lands at docs/demo.gif. Uses the tiny test repo so the whole
# round trip finishes in a few seconds.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1200
Set Height 760
Set Padding 20
Set TypingSpeed 45ms
Set Theme "Catppuccin Mocha"

Hide
# pack disables hf/xet progress bars itself, so its output stays plain and ends
# on "wrote bundle" with no extra setup here.
Type "rm -rf ./bundles ./model && clear" Enter
Show

Sleep 500ms

Type "# 1. Pack a model into a chunked, hashed bundle." Enter
Type "#    tiny-random-gpt2 ships one model in three formats (safetensors/bin/h5); keep one." Enter
Type "#    200K forces the remaining safetensors file to split into parts." Enter
Sleep 800ms
Type `modelferry pack hf-internal-testing/tiny-random-gpt2 --dest ./bundles --chunk-size 200K --exclude "*.bin" --exclude "*.h5"` Enter
Sleep 6s

Type "B=$(echo ./bundles/tiny-random-gpt2__*)" Enter
Sleep 400ms

Type "# 2. Inspect what a security reviewer would approve" Enter
Sleep 800ms
Type "modelferry inspect $B" Enter
Sleep 3s

Type "# 3. Cross the air gap. Verify with the bundled tool, no modelferry install" Enter
Sleep 800ms
Type "python3 $B/tools/modelferry_offline.py verify $B" Enter
Sleep 3s

Type "# 4. Reassemble the original model tree" Enter
Sleep 800ms
Type "python3 $B/tools/modelferry_offline.py unpack $B ./model" Enter
Sleep 3s

Type "ls ./model" Enter
Sleep 3s
