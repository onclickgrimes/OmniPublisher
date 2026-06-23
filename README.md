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

### 1. Iniciar um Upload (POST `/publish/omnichannel`)

Envia a requisição para postar o vídeo. O servidor responde imediatamente com um `task_id`.

**Exemplo de Requisição (JSON):**
```json
{
  "video_path": "C:/Caminho/Absoluto/Para/Seu/Video.mp4",
  "caption": "Este é um teste incrível do OmniPublisher! #teste #viral",
  "platforms": ["youtube", "instagram", "tiktok"],
  "tiktok_session_id": "SEU_COOKIE_SESSIONID_DO_TIKTOK_AQUI",
  "youtube_title": "Título Incrível",
  "youtube_tags": ["python", "automacao"],
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
> - **Instagram**: No primeiro uso, o sistema vai demorar um pouco para fazer o login. Depois ele gera o arquivo `sessions/instagram_settings.json` e as próximas postagens não exigirão re-autenticação.
> - **YouTube**: No primeiro uso, o console abrirá uma aba no navegador na porta `8080` pedindo que você autorize com sua conta do Google. Depois o token é salvo.

### 2. Monitoramento em Tempo Real (GET `/tasks/{task_id}/stream`)

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
