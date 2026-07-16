# Demo GIF

`demo.gif` is rendered from `demo.tape` with [vhs](https://github.com/charmbracelet/vhs).
The official vhs image has no modelferry, so `Dockerfile.vhs` adds it. Regenerate
(needs docker and network) with:

```
docker build -f docs/Dockerfile.vhs -t modelferry-vhs .
docker run --name mfvhs modelferry-vhs docs/demo.tape
docker cp mfvhs:/work/docs/demo.gif docs/demo.gif
docker rm mfvhs
```
