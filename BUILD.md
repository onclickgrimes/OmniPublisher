# Build do Sidecar

Use sempre o script abaixo para gerar o binario do OmniPublisher:

```powershell
.\scripts\build_sidecar.ps1
```

As opcoes oficiais do Nuitka ficam em `run_omnipublisher.py` via diretivas
`nuitka-project`. Elas existem para impedir regressao de tamanho:

- `googleapiclient.discovery_cache.documents` nao deve ser empacotado. O
  provider do YouTube usa `static_discovery=False`.
- `moviepy`, `imageio` e `imageio_ffmpeg` nao devem ser seguidos pelo Nuitka. O
  Instagram exige `thumb_path` e nao gera thumbnail automaticamente.
- `numpy` nao deve ser seguido pelo Nuitka; ele entra por caminhos opcionais de
  Pillow/instagrapi que nao sao usados no fluxo com `thumb_path`.
- Plugins raros do Pillow ficam fora. O build preserva os formatos comuns usados
  para thumbnail (`JPEG`, `PNG`, `BMP`, `GIF`, `PPM`) e remove binarios opcionais
  de AVIF/WebP/CMS no pos-build.
- `playwright/driver/node.exe` nao deve ser copiado. O TikTok reaproveita Node
  externo por `PLAYWRIGHT_NODEJS_PATH`.
- `tiktok_uploader/config.toml` precisa existir no `.dist`; o script copia esse
  arquivo depois do Nuitka porque o pacote le esse TOML em runtime.

Quando o OmniPublisher roda dentro do Electron do `python-project`, o Electron
ja injeta `PLAYWRIGHT_NODEJS_PATH=process.execPath` e `ELECTRON_RUN_AS_NODE=1`.
Em execucao standalone, defina `OMNIPUBLISHER_PLAYWRIGHT_NODE_PATH` para um
`node.exe`/`electron.exe` externo ou deixe o runtime detectar `node` no PATH.

Nao use `python -m nuitka --project` neste repositorio. No Nuitka 4.1.3 essa
flag e para `pyproject.toml`/`setup.py`; as diretivas `# nuitka-project` sao
lidas quando `run_omnipublisher.py` e passado como entrada posicional.
