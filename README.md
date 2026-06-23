# OmniPublisher - Core Backend

Motor centralizado de postagem omnichannel via REST API (FastAPI) que distribui vídeos simultaneamente para YouTube, Instagram e TikTok.

## Requisitos

- Python 3.10+
- Chrome/Chromium instalado na máquina (Necessário para o Selenium no TikTok)

## Configuração do Projeto

1. **Ative o ambiente virtual e instale as dependências:**
   O ambiente virtual `.venv` já está criado e com as bibliotecas instaladas.
   Se precisar reinstalar:
   ```bash
   .\.venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configuração de Variáveis de Ambiente:**
   Copie o arquivo `.env.example` para `.env` e preencha suas credenciais do Instagram:
   ```bash
   cp .env.example .env
   ```

3. **Configuração do YouTube (OAuth2):**
   - Acesse o [Google Cloud Console](https://console.cloud.google.com/).
   - Crie um projeto e ative a **YouTube Data API v3**.
   - Crie credenciais OAuth 2.0 (Tipo: Aplicativo de Computador).
   - Baixe o JSON gerado e salve na raiz do projeto com o nome `client_secret.json`.

4. **Configuração do TikTok (Módulo Local):**
   - O projeto espera o repositório `TiktokAutoUploader` na pasta local `tiktok_uploader/`.
   - Baixe manualmente ou faça clone do repositório:
     ```bash
     git clone https://github.com/makiisthenes/TiktokAutoUploader.git tiktok_uploader
     ```

## Executando o Servidor

Com o ambiente virtual ativado, rode o servidor:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
A documentação Swagger estará disponível em: [http://localhost:8000/docs](http://localhost:8000/docs)

Para rodar no modo sidecar local, usando as variáveis `OMNIPUBLISHER_HOST` e
`OMNIPUBLISHER_PORT`, use:

```bash
python run_omnipublisher.py
```

Por padrão, esse modo escuta em `127.0.0.1:7813`.

Endpoints de runtime:

- `GET /health`: health check para orquestradores como Electron.
- `GET /version`: versão do serviço.
- `GET /runtime`: paths e portas resolvidos no processo atual.

Variáveis úteis para sidecar:

```env
OMNIPUBLISHER_HOST=127.0.0.1
OMNIPUBLISHER_PORT=7813
OMNIPUBLISHER_DATA_DIR=C:/Users/seu_usuario/AppData/Roaming/SeuApp/omnipublisher
OMNIPUBLISHER_DB_PATH=C:/Users/seu_usuario/AppData/Roaming/SeuApp/omnipublisher/omnipublisher.db
OMNIPUBLISHER_SESSIONS_DIR=C:/Users/seu_usuario/AppData/Roaming/SeuApp/omnipublisher/sessions
YOUTUBE_OAUTH_PORT=8080
```

Se `OMNIPUBLISHER_DATA_DIR` não for definido, o comportamento de dev é preservado:
o banco `omnipublisher.db` e a pasta `sessions/` ficam na raiz do projeto.

## Como Consumir a Aplicação (Documentação da API)

### 1. Cadastrar uma Conta (POST `/accounts/`)

Antes de publicar, cadastre a conta da plataforma. O endpoint retorna um `id`;
esse ID é o valor que deve ser usado depois em `accounts` no `/publish/omnichannel`.

**Exemplo TikTok:**
```json
{
  "platform": "tiktok",
  "name": "TikTok Principal",
  "identifier": "@minha_conta",
  "credentials": "COOKIE_SESSIONID_DO_TIKTOK"
}
```

**Exemplo Instagram:**
```json
{
  "platform": "instagram",
  "name": "Instagram Principal",
  "identifier": "meu_usuario",
  "credentials": "minha_senha"
}
```

**Exemplo YouTube:**
```json
{
  "platform": "youtube",
  "name": "Canal Principal",
  "identifier": "email@exemplo.com",
  "credentials": null
}
```

**Exemplo de Resposta:**
```json
{
  "id": "a1f4f6fd-0974-4556-b88d-b2327a478170",
  "platform": "tiktok",
  "name": "TikTok Principal",
  "identifier": "@minha_conta"
}
```

### 2. Iniciar um Upload (POST `/publish/omnichannel`)

Envia a requisição para postar o vídeo. O servidor responde imediatamente com um `task_id`.
Não envie senha, cookie ou session ID neste endpoint. Envie apenas o ID da conta
previamente cadastrada via `POST /accounts/`.

**Exemplo de Requisição (JSON):**
```json
{
  "video_path": "C:/Caminho/Absoluto/Para/Seu/Video.mp4",
  "caption": "Este é um teste incrível do OmniPublisher! #teste #viral",
  "accounts": {
    "youtube": "uuid-da-conta-youtube",
    "instagram": "uuid-da-conta-instagram",
    "tiktok": "a1f4f6fd-0974-4556-b88d-b2327a478170"
  },
  "youtube_title": "Título Incrível",
  "youtube_tags": ["python", "automacao"],
  "youtube_privacy": "public",
  "instagram_format": "reels"
}
```

**Exemplo de Resposta (200 OK):**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "accepted",
  "message": "Upload iniciado. Acompanhe pelo endpoint SSE."
}
```

> **Aviso sobre Sessões:** 
> - **TikTok**: O cookie/session ID é informado no cadastro da conta e fica salvo no banco. No publish, use apenas o `id` dessa conta.
> - **Instagram**: A senha é informada no cadastro da conta. No primeiro uso, o sistema faz login e salva a sessão em `sessions/`.
> - **YouTube**: No primeiro uso, o navegador abrirá na porta `YOUTUBE_OAUTH_PORT` (`8080` por padrão) para autorizar a conta. Depois o token fica salvo em `sessions/`.

### 3. Monitoramento em Tempo Real (GET `/tasks/{task_id}/stream`)

Para não precisar fazer *polling*, o backend usa SSE (Server-Sent Events).

No frontend (ex: Javascript), você pode escutar assim:
```javascript
const evtSource = new EventSource("http://localhost:8000/tasks/550e8400-e29b-41d4-a716-446655440000/stream");

evtSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log("Atualização:", data);
    
    if (data.type === "finished") {
        console.log("Todos os uploads concluídos!");
        evtSource.close();
    }
};
```
O evento trafega progressos e status (`pending`, `uploading`, `success`, `error`) de cada rede de forma assíncrona.
