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
SCHEDULER_INTERVAL_SECONDS=30
RUNNING_JOB_STALE_MINUTES=30
ACCOUNT_STATUS_CACHE_TTL_SECONDS=600
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

Também é possível gerenciar contas com:

- `GET /accounts/`: lista contas cadastradas.
- `GET /accounts/?platform=tiktok`: lista contas de uma plataforma.
- `GET /accounts/{account_id}`: consulta uma conta.
- `PATCH /accounts/{account_id}`: atualiza nome, identificador ou credenciais.
- `DELETE /accounts/{account_id}`: remove a conta e tenta limpar o arquivo de sessão associado.

As respostas de contas nunca retornam `credentials`.

### 2. Workspaces e Contas

Workspaces agrupam contas por projeto/contexto. As contas continuam globais e podem
estar em vários workspaces ou em nenhum.
Se o banco não possuir nenhum workspace no boot, o backend cria automaticamente
um workspace `Default` e vincula contas globais já existentes a ele.

Endpoints:

- `GET /workspaces`: lista workspaces.
- `POST /workspaces`: cria um workspace.
- `GET /workspaces/{workspace_id}`: consulta um workspace.
- `GET /workspaces/{workspace_id}/overview`: retorna workspace, contas, status cacheado e contadores de publicações.
- `PATCH /workspaces/{workspace_id}`: atualiza nome, slug ou descrição.
- `DELETE /workspaces/{workspace_id}`: remove o workspace sem apagar contas.
- `GET /workspaces/{workspace_id}/accounts`: lista contas vinculadas.
- `POST /workspaces/{workspace_id}/accounts`: vincula uma conta global ao workspace.
- `DELETE /workspaces/{workspace_id}/accounts/{account_id}`: remove o vínculo.
- `GET /accounts?workspace_id=...`: lista contas filtradas por workspace.

Exemplo para criar workspace:

```json
{
  "name": "Histórias da Bíblia",
  "slug": "historias-da-biblia",
  "description": "Workspace de conteúdo bíblico."
}
```

Exemplo para vincular conta:

```json
{
  "account_id": "a1f4f6fd-0974-4556-b88d-b2327a478170",
  "label": "TikTok Principal",
  "is_default": true
}
```

Status de autenticação das contas:

- `GET /accounts/{account_id}/status`: status individual.
- `GET /accounts/{account_id}/status?refresh=true`: força nova verificação.
- `POST /accounts/{account_id}/status/refresh`: marca como `checking` e verifica em background.
- `GET /workspaces/{workspace_id}/accounts/status`: status das contas do workspace.
- `GET /workspaces/{workspace_id}/accounts/status?refresh=true`: força nova verificação.
- `POST /workspaces/{workspace_id}/accounts/status/refresh`: marca as contas como `checking` e verifica em background.

O status usa cache em SQLite por `ACCOUNT_STATUS_CACHE_TTL_SECONDS`. Para TikTok,
a checagem é leve: valida se existe `session_id` cadastrado e se a lib está disponível;
a validação web completa continua acontecendo no publish.
O endpoint de overview não força validação; ele retorna apenas cache fresco ou `unknown`.

### 3. Iniciar ou Agendar um Upload (POST `/publish/omnichannel`)

Envia a requisição para postar o vídeo. O servidor responde imediatamente com um `task_id`.
Não envie senha, cookie ou session ID neste endpoint. Envie apenas o ID da conta
previamente cadastrada via `POST /accounts/`.
O servidor valida antes de criar a task se o arquivo existe, se a conta existe e se
a conta pertence à plataforma informada.

**Exemplo de publicação imediata:**
```json
{
  "workspace_id": "uuid-do-workspace",
  "mode": "immediate",
  "video_path": "C:/Caminho/Absoluto/Para/Seu/Video.mp4",
  "thumb_path": "C:/Caminho/Absoluto/Para/Sua/Thumb.jpg",
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

**Exemplo de publicação agendada:**
```json
{
  "workspace_id": "uuid-do-workspace",
  "mode": "scheduled",
  "scheduled_at": "2026-06-24T14:00:00-03:00",
  "video_path": "C:/Caminho/Absoluto/Para/Seu/Video.mp4",
  "thumb_path": "C:/Caminho/Absoluto/Para/Sua/Thumb.jpg",
  "caption": "Post agendado pelo OmniPublisher! #teste",
  "accounts": {
    "tiktok": "a1f4f6fd-0974-4556-b88d-b2327a478170"
  }
}
```

Quando `mode` é `scheduled`, o job fica persistido no SQLite com status `queued`.
O worker interno varre o banco a cada `SCHEDULER_INTERVAL_SECONDS` e dispara jobs
com `scheduled_at <= now`.
Quando `workspace_id` é enviado, todas as contas em `accounts` precisam estar
vinculadas ao workspace informado.

`thumb_path` é opcional. Quando informado, o arquivo precisa existir localmente.
O backend envia essa imagem como capa/thumbnail nas plataformas suportadas pela
integração atual: YouTube e Instagram. No TikTok, `thumb_path` é ignorado.
Se o vídeo for publicado mas a plataforma recusar a capa/thumbnail, o job continua
como `success` e o detalhe fica registrado como evento `platform_warning`.

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

### 4. Consultar e Monitorar Publicações

As publicações ficam persistidas no SQLite.

- `GET /tasks`: lista publicações persistidas.
- `GET /tasks?status=queued`: lista publicações por status.
- `GET /tasks?workspace_id=...`: lista publicações de um workspace.
- `GET /tasks/{task_id}`: retorna detalhes e status por plataforma.
- `POST /tasks/{task_id}/cancel`: cancela uma publicação `queued` ou `running`.
- `GET /tasks/{task_id}/stream`: acompanha atualizações via SSE.

Status principais:

- `queued`: aguardando execução ou horário agendado.
- `running`: publicação em andamento.
- `success`: todas as plataformas concluíram com sucesso.
- `error`: pelo menos uma plataforma falhou.
- `canceled`: publicação cancelada antes de concluir.

O scheduler também marca como erro jobs `running` que excedem `TIMEOUT_SECONDS`.
No startup, jobs `running` antigos são recuperados como erro após `RUNNING_JOB_STALE_MINUTES`,
pois nenhuma execução em memória sobrevive a reinício do processo.

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
O evento trafega progressos, avisos e status (`pending`, `uploading`, `success`, `error`, `canceled`) de cada rede de forma assíncrona.
